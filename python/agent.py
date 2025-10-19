# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from websockets import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from websockets.server import serve

from config import AgentConfig, load_agent_config
from actions import Actions
from bridge_ws import BotBridge
from memory import Memory
from planner import PlanOut, compose_barrier_notification, plan
from utils import setup_logger

logger = setup_logger("agent")

load_dotenv()

# --- 設定の読み込み --------------------------------------------------------

_CONFIG_RESULT = load_agent_config()
AGENT_CONFIG: AgentConfig = _CONFIG_RESULT.config
WS_URL = AGENT_CONFIG.ws_url
AGENT_WS_HOST = AGENT_CONFIG.agent_host
AGENT_WS_PORT = AGENT_CONFIG.agent_port
DEFAULT_MOVE_TARGET_RAW = AGENT_CONFIG.default_move_target_raw
DEFAULT_MOVE_TARGET = AGENT_CONFIG.default_move_target

logger.info(
    "Agent configuration loaded (ws_url=%s, bind=%s:%s, default_target=%s)",
    WS_URL,
    AGENT_WS_HOST,
    AGENT_WS_PORT,
    DEFAULT_MOVE_TARGET,
)


@dataclass
class ChatTask:
    """Node 側から渡されるチャット指示をキュー化する際のデータ構造。"""

    username: str
    message: str


@dataclass(frozen=True)
class ActionTaskRule:
    """行動系タスクをカテゴリ別に整理するためのルール定義。"""

    keywords: Tuple[str, ...]
    hints: Tuple[str, ...] = ()
    label: str = ""
    implemented: bool = False


class AgentOrchestrator:
    """受信チャットを順次処理し、LLM プラン→Mineflayer 操作を遂行する中核クラス。"""

    # Mineflayer へ渡す座標はプレイヤーの指示の表記揺れが多いため、複数の正規表現
    # を用意して柔軟に抽出する。スラッシュ区切り（-36 / 73 / -66）や全角スラッシュ、
    # カンマ区切り、XYZ: -36 / 73 / -66 などを一括で処理できるようにしている。
    # プレイヤーは座標を多彩な表記で共有するため、ここでは代表的な揺れを広くカバー
    # する正規表現を複数用意する。スラッシュ／カンマ区切りに加え、XYZ ラベル付き
    # 表記や波括弧を伴う書き方も解析できるようにし、座標の再確認を最小化する。
    _COORD_PATTERNS = (
        re.compile(r"(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)"),
        re.compile(
            r"XYZ[:：]?\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)"
        ),
        re.compile(
            r"X\s*[:＝=]?\s*(-?\d+)[^\d-]+Y\s*[:＝=]?\s*(-?\d+)[^\d-]+Z\s*[:＝=]?\s*(-?\d+)",
            re.IGNORECASE,
        ),
    )
    # 行動系タスクをカテゴリごとに整理し、未実装アクションでも丁寧に扱えるようにする。
    # keywords は分類、hints は移動継続など暗黙の補助に利用する。
    _ACTION_TASK_RULES: Dict[str, ActionTaskRule] = {
        "move": ActionTaskRule(
            keywords=(
                "移動",
                "向かう",
                "歩く",
                "進む",
                "到達",
                "到着",
                "目指す",
            ),
            hints=(
                "段差",
                "足場",
                "はしご",
                "登",
                "降",
                "経路",
                "通路",
                "迂回",
                "高さ",
            ),
            label="指定地点への移動",
            implemented=True,
        ),
        "mine": ActionTaskRule(
            keywords=(
                "採掘",
                "採鉱",
                "鉱石",
                "掘る",
                "ブランチ",
            ),
            label="採掘作業",
        ),
        "farm": ActionTaskRule(
            keywords=(
                "収穫",
                "畑",
                "農",
                "植え",
                "耕す",
            ),
            label="農作業",
        ),
        "craft": ActionTaskRule(
            keywords=(
                "クラフト",
                "作成",
                "作る",
                "製作",
            ),
            label="クラフト処理",
        ),
        "follow": ActionTaskRule(
            keywords=(
                "ついて",
                "追尾",
                "同行",
                "付いて",
            ),
            label="追従行動",
        ),
        "build": ActionTaskRule(
            keywords=(
                "建て",
                "建築",
                "建造",
                "組み立て",
            ),
            label="建築作業",
        ),
        "fight": ActionTaskRule(
            keywords=(
                "戦う",
                "迎撃",
                "戦闘",
                "倒す",
                "守る",
            ),
            label="戦闘行動",
        ),
        "equip": ActionTaskRule(
            keywords=(
                "装備",
                "持ち替え",
                "手に持つ",
                "構える",
            ),
            label="装備持ち替え",
            implemented=True,
        ),
        "deliver": ActionTaskRule(
            keywords=(
                "渡す",
                "届ける",
                "受け渡し",
                "納品",
            ),
            label="アイテム受け渡し",
        ),
        "storage": ActionTaskRule(
            keywords=(
                "チェスト",
                "収納",
                "保管",
                "しまう",
            ),
            label="保管操作",
        ),
        "gather": ActionTaskRule(
            keywords=(
                "集め",
                "確保",
                "調達",
                "集める",
            ),
            label="素材収集",
        ),
    }
    # 現在位置や所持品などを確認してチャットへ報告するだけのステップは、
    # 移動や採取といったアクションとは別系統の「検出報告タスク」として扱い、
    # 進捗報告のテンプレートに流れ込まないように専用の分類を用意する。
    _DETECTION_TASK_KEYWORDS = {
        "player_position": (
            "現在位置",
            "現在地",
            "座標",
            "座標を報告",
            "XYZ",
        ),
        "inventory_status": (
            "所持品",
            "インベントリ",
            "持ち物",
            "手持ち",
            "アイテム一覧",
        ),
        "general_status": (
            "状態を報告",
            "状況を報告",
            "体力の状況",
            "満腹度",
        ),
    }
    _DETECTION_LABELS = {
        "player_position": "現在位置の報告",
        "inventory_status": "所持品の確認",
        "general_status": "状態の共有",
    }
    # 装備ステップ用のキーワード→装備対象の推測マップ。右手・左手のヒントも同時に解析する。
    _EQUIP_KEYWORD_RULES = (
        (("ツルハシ", "ピッケル", "pickaxe"), {"tool_type": "pickaxe"}),
        (("剣", "ソード", "sword"), {"tool_type": "sword"}),
        (("斧", "おの", "axe"), {"tool_type": "axe"}),
        (("シャベル", "スコップ", "shovel", "spade"), {"tool_type": "shovel"}),
        (("クワ", "鍬", "hoe"), {"tool_type": "hoe"}),
        (("盾", "シールド", "shield"), {"tool_type": "shield"}),
        (("松明", "たいまつ", "torch"), {"item_name": "torch"}),
    )

    def __init__(
        self,
        actions: Actions,
        memory: Memory,
        *,
        config: AgentConfig | None = None,
    ) -> None:
        self.actions = actions
        self.memory = memory
        self.queue: asyncio.Queue[ChatTask] = asyncio.Queue()
        self.config = config or AGENT_CONFIG
        # 設定値をローカル変数へコピーしておくことで、テスト時に差し込まれた構成も尊重する。
        self.default_move_target = self.config.default_move_target
        self.logger = setup_logger("agent.orchestrator")

    async def enqueue_chat(self, username: str, message: str) -> None:
        """WebSocket から受け取ったチャットをワーカーに積む。"""

        task = ChatTask(username=username, message=message)
        await self.queue.put(task)
        self.logger.info(
            "chat task enqueued username=%s message=%s queue_size=%d",
            username,
            message,
            self.queue.qsize(),
        )

    async def worker(self) -> None:
        """チャットキューを逐次処理するバックグラウンドタスク。"""

        while True:
            queue_before = self.queue.qsize()
            self.logger.info(
                "worker awaiting task queue_size_before_get=%d", queue_before
            )
            task = await self.queue.get()
            try:
                started_at = time.perf_counter()
                await self._process_chat(task)
                elapsed = time.perf_counter() - started_at
                self.logger.info(
                    "worker processed username=%s duration=%.3fs remaining_queue=%d",
                    task.username,
                    elapsed,
                    self.queue.qsize(),
                )
            except Exception:
                self.logger.exception("failed to process chat task username=%s", task.username)
            finally:
                self.queue.task_done()

    async def _process_chat(self, task: ChatTask) -> None:
        """単一のチャット指示に対して LLM 計画とアクション実行を行う。"""

        context = self._build_context_snapshot()
        self.logger.info(
            "creating plan for username=%s message='%s' context=%s",
            task.username,
            task.message,
            context,
        )

        # 元チャットに含まれる座標を先に解析し、LLM の計画が座標を省略しても
        # 直ちに移動へ移れるようヒントとして保持する。
        user_hint_coords = self._extract_coordinates(task.message)
        if user_hint_coords:
            self.logger.info(
                "user message provided coordinates=%s", user_hint_coords
            )

        plan_out = await plan(task.message, context)
        self.logger.info(
            "plan generated steps=%d plan=%s resp=%s",
            len(plan_out.plan),
            plan_out.plan,
            plan_out.resp,
        )

        # LLM の丁寧な応答をそのままプレイヤーへ relay する。
        if plan_out.resp:
            self.logger.info(
                "relaying llm response to player username=%s resp='%s'",
                task.username,
                plan_out.resp,
            )
            await self.actions.say(plan_out.resp)

        await self._execute_plan(plan_out, initial_target=user_hint_coords)
        self.memory.set("last_chat", {"username": task.username, "message": task.message})

    def _build_context_snapshot(self) -> Dict[str, Any]:
        """LLM へ渡す簡易コンテキストを生成する。"""

        snapshot = {
            "player_pos": self.memory.get("player_pos", "不明"),
            "inventory_summary": self.memory.get("inventory", "不明"),
            "last_chat": self.memory.get("last_chat", "未記録"),
            "last_destination": self.memory.get("last_destination", "未記録"),
        }
        self.logger.info("context snapshot built=%s", snapshot)
        return snapshot

    async def _execute_plan(
        self, plan_out: PlanOut, *, initial_target: Optional[Tuple[int, int, int]] = None
    ) -> None:
        """LLM が出力した高レベルステップを簡易ヒューリスティックで実行する。

        Args:
            plan_out: LLM から取得した行動計画と応答文。
            initial_target: プレイヤーが元のチャットで直接指定した座標。LLM の
                ステップに座標が含まれなくても直ちに移動へ移れるよう、初期値
                として利用する。
        """

        total_steps = len(plan_out.plan)
        # プラン生成が空配列で戻るケースでは行動開始前から停滞するため、
        # 直ちに障壁として報告してプレイヤーへ状況を伝える。
        if total_steps == 0:
            await self._report_execution_barrier(
                "LLM が生成した計画",
                "手順が 1 件も返されず、行動に移れません。プロンプトや状況を確認してください。",
            )
            return

        # 直前に検出した移動座標を記録し、以降の「移動」ステップで座標が省略
        # された場合でも同じ目的地へ移動し続けられるようにする。
        last_target_coords: Optional[Tuple[int, int, int]] = initial_target
        detection_reports: List[Dict[str, str]] = []
        action_backlog: List[Dict[str, str]] = []
        for index, step in enumerate(plan_out.plan, start=1):
            normalized = step.strip()
            self.logger.info(
                "plan_step index=%d/%d raw='%s' normalized='%s'",
                index,
                total_steps,
                step,
                normalized,
            )
            if not normalized:
                continue

            detection_category = self._classify_detection_task(normalized)
            if detection_category:
                self.logger.info(
                    "plan_step index=%d classified as detection_report category=%s",
                    index,
                    detection_category,
                )
                detection_reports.append({
                    "category": detection_category,
                    "step": normalized,
                })
                continue

            coords = self._extract_coordinates(normalized)
            if coords:
                self.logger.info(
                    "plan_step index=%d classified as coordinate_move coords=%s",
                    index,
                    coords,
                )
                handled, last_target_coords = await self._handle_action_task(
                    "move",
                    normalized,
                    last_target_coords=coords,
                    backlog=action_backlog,
                    explicit_coords=coords,
                )
                if not handled:
                    await self._report_execution_barrier(
                        normalized,
                        "座標移動の処理に失敗しました。ログを確認してください。",
                    )
                continue

            if self._is_status_check_step(normalized):
                # 状況確認系のメタ指示は Mineflayer の直接操作に該当しないため、
                # 障壁扱いにせず静かに無視して実行フローを前に進める。
                self.logger.info(
                    "plan_step index=%d ignored introspection step='%s'",
                    index,
                    normalized,
                )
                continue

            if await self._attempt_proactive_progress(normalized, last_target_coords):
                continue

            action_category = self._classify_action_task(normalized)
            if action_category:
                self.logger.info(
                    "plan_step index=%d classified as action_task category=%s",
                    index,
                    action_category,
                )
                handled, last_target_coords = await self._handle_action_task(
                    action_category,
                    normalized,
                    last_target_coords=last_target_coords,
                    backlog=action_backlog,
                )
                if handled:
                    continue

            if "報告" in normalized or "伝える" in normalized:
                self.logger.info(
                    "plan_step index=%d issuing status_report",
                    index,
                )
                await self.actions.say("進捗を確認しています。続報をお待ちください。")
                continue

            self.logger.info(
                "plan_step index=%d no_direct_mapping step='%s'",
                index,
                normalized,
            )
            await self._report_execution_barrier(
                normalized,
                "対応可能なアクションが見つからず停滞しています。計画ステップの表現を見直してください。",
            )

        if detection_reports:
            await self._handle_detection_reports(
                detection_reports,
                already_responded=bool(plan_out.resp.strip()),
            )

        if action_backlog:
            await self._handle_action_backlog(
                action_backlog,
                already_responded=bool(plan_out.resp.strip()),
            )

    def _is_status_check_step(self, text: str) -> bool:
        """位置・所持品確認など実際の操作が不要なステップかを判定する。"""

        status_keywords = (
            "現在位置",
            "座標表示",
            "位置を確認",
            "所持品を確認",
            "状況を確認",
        )
        return any(keyword in text for keyword in status_keywords)

    def _is_move_step(self, text: str) -> bool:
        """ステップが明示的に移動を要求しているかを判定する。"""

        rule = self._ACTION_TASK_RULES.get("move")
        return bool(rule and self._match_keywords(text, rule.keywords))

    def _should_continue_move(self, text: str) -> bool:
        """段差調整など移動継続で吸収できるステップかどうかを推測する。"""

        rule = self._ACTION_TASK_RULES.get("move")
        return bool(rule and self._match_keywords(text, rule.hints))

    def _classify_detection_task(self, text: str) -> Optional[str]:
        """検出報告タスク（位置・所持品などの確認系ステップ）を分類する。"""

        normalized = text.replace(" ", "").replace("　", "")
        for category, keywords in self._DETECTION_TASK_KEYWORDS.items():
            for keyword in keywords:
                if keyword in normalized:
                    return category
        return None

    def _infer_equip_arguments(self, text: str) -> Optional[Dict[str, str]]:
        """装備ステップから Mineflayer へ渡す装備パラメータを推測する。"""

        normalized = text.lower()
        destination = "hand"
        if "左手" in text or "オフハンド" in normalized or "off-hand" in normalized:
            destination = "off-hand"
        elif "右手" in text:
            destination = "hand"

        for keywords, mapping in self._EQUIP_KEYWORD_RULES:
            if any(keyword and keyword in text for keyword in keywords):
                result: Dict[str, str] = {"destination": destination}
                result.update(mapping)
                return result
            if any(keyword and keyword.lower() in normalized for keyword in keywords):
                result = {"destination": destination, **mapping}
                return result

        return None

    async def _attempt_proactive_progress(
        self, step: str, last_target_coords: Optional[Tuple[int, int, int]]
    ) -> bool:
        """未対応ステップでも移動継続で処理できる場合は実行し True を返す。"""

        if not last_target_coords:
            return False

        if self._should_continue_move(step):
            self.logger.info(
                "interpreting step='%s' as continue_move coords=%s",
                step,
                last_target_coords,
            )
            await self._move_to_coordinates(last_target_coords)
            return True

        return False

    def _classify_action_task(self, text: str) -> Optional[str]:
        """行動系タスクのカテゴリを判定し、保留リスト整理に利用する。"""

        normalized = text.replace(" ", "").replace("　", "")
        for category, rule in self._ACTION_TASK_RULES.items():
            if self._match_keywords(normalized, rule.keywords):
                return category
        return None

    def _match_keywords(self, text: str, keywords: Tuple[str, ...]) -> bool:
        """任意のキーワードが文中に含まれるかを評価するヘルパー。"""

        return any(keyword and keyword in text for keyword in keywords)

    async def _handle_action_task(
        self,
        category: str,
        step: str,
        *,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        explicit_coords: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]]]:
        """行動タスクを処理し、必要に応じて保留一覧を更新する。"""

        rule = self._ACTION_TASK_RULES.get(category)
        if not rule:
            backlog.append({"category": category, "step": step, "label": category})
            self.logger.warning(
                "action category=%s missing rule so queued to backlog step='%s'",
                category,
                step,
            )
            return False, last_target_coords

        if category == "move":
            target = explicit_coords or self._extract_coordinates(step)
            updated_target = target or last_target_coords
            used_default_target = False
            if updated_target is None:
                updated_target = self.default_move_target
                used_default_target = True

            self.logger.info(
                "handling move step='%s' target=%s used_default=%s",
                step,
                updated_target,
                used_default_target,
            )
            move_ok = await self._move_to_coordinates(updated_target)
            if used_default_target:
                await self._report_execution_barrier(
                    step,
                    "指示文から移動先の座標を特定できず、既定座標へ退避しました。文章に XYZ 形式の座標を含めてください。",
                )
            if not move_ok:
                await self._report_execution_barrier(
                    step,
                    "フォールバック移動が Mineflayer に拒否されました。ログの moveTo 応答内容を確認してください。",
                )
            return True, updated_target

        if category == "equip":
            equip_args = self._infer_equip_arguments(step)
            if not equip_args:
                self.logger.info("equip step inference failed step='%s'", step)
                await self._report_execution_barrier(
                    step,
                    "装備するアイテムを推測できませんでした。ツール名や用途をもう少し具体的に指示してください。",
                )
                return True, last_target_coords

            self.logger.info(
                "handling equip step='%s' args=%s",
                step,
                equip_args,
            )
            resp = await self.actions.equip_item(
                tool_type=equip_args.get("tool_type"),
                item_name=equip_args.get("item_name"),
                destination=equip_args.get("destination", "hand"),
            )
            if resp.get("ok"):
                return True, last_target_coords

            error_detail = resp.get("error") or "Mineflayer 側の理由不明な拒否"
            await self._report_execution_barrier(
                step,
                f"装備コマンドが失敗しました: {error_detail}",
            )
            return True, last_target_coords

        if rule.implemented:
            self.logger.info(
                "action category=%s has implemented flag but no handler step='%s'",
                category,
                step,
            )
            return False, last_target_coords

        backlog.append({
            "category": category,
            "step": step,
            "label": rule.label or category,
        })
        self.logger.info(
            "action category=%s queued to backlog (unimplemented) step='%s'",
            category,
            step,
        )
        return True, last_target_coords

    async def _handle_action_backlog(
        self,
        backlog: Iterable[Dict[str, str]],
        *,
        already_responded: bool,
    ) -> None:
        """未実装アクションの backlog をメモリとチャットへ整理する。"""

        backlog_list = list(backlog)
        if not backlog_list:
            return

        self.memory.set("last_pending_actions", backlog_list)

        unique_labels: List[str] = []
        for item in backlog_list:
            label = item.get("label") or item.get("category") or "未分類の行動"
            if label not in unique_labels:
                unique_labels.append(label)

        if already_responded:
            self.logger.info(
                "skip action backlog follow-up because initial response already sent backlog=%s",
                backlog_list,
            )
            return

        summary = "、".join(unique_labels)
        await self.actions.say(
            (
                f"{summary}の行動リクエストを検知しましたが、Mineflayer 側の下位アクションが未実装のため待機中です。"
                "追加の指示や優先順位があればお知らせください。"
            )
        )

    async def _handle_detection_reports(
        self,
        reports: Iterable[Dict[str, str]],
        *,
        already_responded: bool,
    ) -> None:
        """検出報告タスクをメモリへ整理し、必要に応じて丁寧な補足メッセージを送る。"""

        report_list = list(reports)
        if not report_list:
            return

        self.memory.set("last_detection_reports", report_list)
        if already_responded:
            # LLM からプレイヤー向け応答が既に提示されている場合は追加送信を控え、
            # ログとメモリへの整理だけでフローを終える。重複応答による冗長さを防ぐため。
            self.logger.info(
                "skip detection follow-up because initial response already sent reports=%s",
                report_list,
            )
            return

        # 未返信の場合は検出タスクを丁寧に説明する補足メッセージを構築する。
        labels = []
        for item in report_list:
            category = item.get("category", "")
            label = self._DETECTION_LABELS.get(category)
            if label and label not in labels:
                labels.append(label)

        if not labels:
            labels.append("状況確認")

        summary = "、".join(labels)
        await self.actions.say(
            f"{summary}の確認依頼を検出報告タスクとして整理しました。追加で知りたい情報があれば教えてください。"
        )

    def _extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        """ステップ文字列から XYZ 座標らしき数値を抽出する。"""

        for pattern in self._COORD_PATTERNS:
            match = pattern.search(text)
            if match:
                x, y, z = (int(match.group(i)) for i in range(1, 4))
                return x, y, z
        return None

    async def _move_to_coordinates(self, coords: Iterable[int]) -> bool:
        """Mineflayer の移動アクションを発行し、結果をログへ残すユーティリティ。"""

        x, y, z = coords
        self.logger.info("requesting moveTo to (%d, %d, %d)", x, y, z)
        resp = await self.actions.move_to(x, y, z)
        self.logger.info("moveTo response=%s", resp)
        if resp.get("ok"):
            self.memory.set("last_destination", {"x": x, "y": y, "z": z})
            return True

        # ここまで来た場合は Mineflayer からエラー応答が返却されたことを意味する。
        # ゲーム内チャットとログへ障壁を即時通報し、プレイヤーと開発者が原因を
        # 追跡しやすいようにする。
        else:
            self.logger.error("moveTo command rejected resp=%s", resp)
            error_detail = resp.get("error") or "Mineflayer 側の理由不明な拒否"
            await self._report_execution_barrier(
                f"座標 ({x}, {y}, {z}) への移動",
                f"Mineflayer からエラー応答を受け取りました（{error_detail}）。",
            )
            return False

    async def _report_execution_barrier(self, step: str, reason: str) -> None:
        """処理を継続できない障壁を検知した際にチャットとログで即時共有する。"""

        self.logger.warning(
            "execution barrier detected step='%s' reason='%s'",
            step,
            reason,
        )
        message = await self._compose_barrier_message(step, reason)
        await self.actions.say(message)

    async def _compose_barrier_message(self, step: str, reason: str) -> str:
        """障壁内容を LLM に渡して、プレイヤー向けの確認メッセージを生成する。"""

        try:
            context = self._build_context_snapshot()
            context.update({"queue_backlog": self.queue.qsize()})
            llm_message = await compose_barrier_notification(step, reason, context)
            if llm_message:
                self.logger.info(
                    "barrier message composed via LLM step='%s' message='%s'",
                    step,
                    llm_message,
                )
                return llm_message
        except Exception:
            self.logger.exception("failed to compose barrier message via LLM")

        # LLM 連携に失敗した場合は従来通り短縮メッセージを返す。
        short_step = self._shorten_text(step, limit=40)
        short_reason = self._shorten_text(reason, limit=60)
        return f"手順「{short_step}」で問題が発生しました: {short_reason}"

    @staticmethod
    def _shorten_text(text: str, *, limit: int) -> str:
        """チャット送信用にテキストを安全な長さへ丸めるユーティリティ。"""

        text = text.strip()
        return text if len(text) <= limit else f"{text[:limit]}…"


class AgentWebSocketServer:
    """Node -> Python のチャット転送を受け付ける WebSocket サーバー。"""

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.logger = setup_logger("agent.ws")

    async def handler(self, websocket: WebSocketServerProtocol) -> None:
        """各接続ごとに JSON コマンドを受信・処理する。"""

        peer = f"{websocket.remote_address}" if websocket.remote_address else "unknown"
        self.logger.info("connection opened from %s", peer)
        try:
            async for raw in websocket:
                response = await self._handle_message(raw)
                await websocket.send(json.dumps(response, ensure_ascii=False))
        except (ConnectionClosedOK, ConnectionClosedError):
            self.logger.info("connection closed from %s", peer)
        except Exception:
            self.logger.exception("unexpected error while handling connection from %s", peer)

    async def _handle_message(self, raw: str) -> Dict[str, Any]:
        """受信文字列を解析し、サポートするコマンドへ振り分ける。"""

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.error("invalid JSON payload=%s", raw)
            return {"ok": False, "error": "invalid json"}

        if payload.get("type") != "chat":
            self.logger.error("unsupported payload type=%s", payload.get("type"))
            return {"ok": False, "error": "unsupported type"}

        args = payload.get("args") or {}
        username = str(args.get("username", "")).strip() or "Player"
        message = str(args.get("message", "")).strip()

        if not message:
            self.logger.warning("empty chat message received username=%s", username)
            return {"ok": False, "error": "empty message"}

        await self.orchestrator.enqueue_chat(username, message)
        return {"ok": True}


async def main() -> None:
    """エージェントを起動し、WebSocket サーバーとワーカーを開始する。"""

    bridge = BotBridge(WS_URL)
    actions = Actions(bridge)
    mem = Memory()
    orchestrator = AgentOrchestrator(actions, mem)
    ws_server = AgentWebSocketServer(orchestrator)

    worker_task = asyncio.create_task(orchestrator.worker(), name="agent-worker")

    async with serve(ws_server.handler, AGENT_WS_HOST, AGENT_WS_PORT):
        logger.info("Python agent is listening on ws://%s:%s", AGENT_WS_HOST, AGENT_WS_PORT)
        try:
            await asyncio.Future()  # 実行を継続
        except asyncio.CancelledError:
            logger.info("main loop cancelled")
        finally:
            worker_task.cancel()
            with contextlib.suppress(Exception):
                await worker_task


if __name__ == "__main__":
    asyncio.run(main())
