# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from websockets import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from websockets.server import serve

from config import AgentConfig, load_agent_config
from services.minedojo_client import (
    MineDojoClient,
    MineDojoDemoMetadata,
    MineDojoDemonstration,
    MineDojoMission,
)
from services.skill_repository import SkillRepository
from actions import Actions
from bridge_client import (
    BRIDGE_EVENT_STREAM_ENABLED,
    BridgeClient,
    BridgeError,
)
from bridge_ws import BotBridge
from memory import Memory
from planner import (
    ActionDirective,
    PlanArguments,
    PlanOut,
    ReActStep,
    BarrierNotificationError,
    BarrierNotificationTimeout,
    compose_barrier_notification,
    plan,
)
from skills import SkillMatch, SkillNode
from utils import ThoughtActionObservationTracer, log_structured_event, setup_logger
from agent_orchestrator import (
    ActionGraph,
    ActionTaskRule,
    ChatTask,
    MineDojoSelfDialogueExecutor,
    build_reflection_prompt,
)

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
SKILL_LIBRARY_PATH = AGENT_CONFIG.skill_library_path
STATUS_REFRESH_TIMEOUT_SECONDS = float(os.getenv("STATUS_REFRESH_TIMEOUT_SECONDS", "3.0"))
STATUS_REFRESH_RETRY = int(os.getenv("STATUS_REFRESH_RETRY", "2"))
STATUS_REFRESH_BACKOFF_SECONDS = float(os.getenv("STATUS_REFRESH_BACKOFF_SECONDS", "0.5"))
BLOCK_EVAL_RADIUS = int(os.getenv("BLOCK_EVAL_RADIUS", "3"))
BLOCK_EVAL_TIMEOUT_SECONDS = float(os.getenv("BLOCK_EVAL_TIMEOUT_SECONDS", "3.0"))
BLOCK_EVAL_HEIGHT_DELTA = int(os.getenv("BLOCK_EVAL_HEIGHT_DELTA", "1"))
STRUCTURED_EVENT_HISTORY_LIMIT = int(os.getenv("STRUCTURED_EVENT_HISTORY_LIMIT", "10"))
PERCEPTION_HISTORY_LIMIT = int(os.getenv("PERCEPTION_HISTORY_LIMIT", "5"))
LOW_FOOD_THRESHOLD = int(os.getenv("LOW_FOOD_THRESHOLD", "6"))

logger.info(
    "Agent configuration loaded (ws_url=%s, bind=%s:%s, default_target=%s)",
    WS_URL,
    AGENT_WS_HOST,
    AGENT_WS_PORT,
    DEFAULT_MOVE_TARGET,
)


class AgentOrchestrator:
    """受信チャットを順次処理し、LLM プラン→Mineflayer 操作を遂行する中核クラス。"""

    # 再計画の連鎖が無限に進むとチャット出力が雪崩のように発生するため、
    # 自動リトライは上限回数で打ち切り、最悪時でも運用者が介入しやすくする。
    _MAX_REPLAN_DEPTH = 2
    # チャット処理がタイムアウトした際の再試行上限。無制限リトライでキューを
    # 埋め続けると新規指示を受け付けられなくなるため、再計画と同じ深さで早期開放する。
    _MAX_TASK_TIMEOUT_RETRY = _MAX_REPLAN_DEPTH

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
            priority=15,
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
            priority=10,
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
            priority=20,
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
    # MineDojo のミッション ID へ分類カテゴリをマッピングする。カテゴリ追加時に
    # 参照することで、デモ取得の影響範囲を明示できるようにしている。
    _MINEDOJO_MISSION_BINDINGS: Dict[str, str] = {
        "mine": "obtain_diamond",
        "farm": "harvest_wheat",
        "build": "build_simple_house",
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
    _HAZARD_BLOCK_KEYWORDS = (
        "lava",
        "magma",
        "fire",
        "cactus",
        "powder_snow",
        "campfire",
    )
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
    # 採掘に必要なツルハシのランクと、対応するアイテム名の評価指標。
    # Mineflayer が返すアイテム名（name）は vanilla の ID に準拠するため、
    # それぞれに序列を割り当てて比較する。木≒金 < 石 < 鉄 < ダイヤ < ネザライト。
    _PICKAXE_TIER_BY_NAME = {
        "wooden_pickaxe": 1,
        "golden_pickaxe": 1,
        "stone_pickaxe": 2,
        "iron_pickaxe": 3,
        "diamond_pickaxe": 4,
        "netherite_pickaxe": 5,
    }
    # 各鉱石がドロップするために必要な最小ツルハシランクを定義する。
    _ORE_PICKAXE_REQUIREMENTS = {
        "diamond_ore": 3,
        "deepslate_diamond_ore": 3,
        "redstone_ore": 3,
        "deepslate_redstone_ore": 3,
        "gold_ore": 3,
        "deepslate_gold_ore": 3,
        "lapis_ore": 2,
        "deepslate_lapis_ore": 2,
        "iron_ore": 2,
        "deepslate_iron_ore": 2,
        "coal_ore": 1,
        "deepslate_coal_ore": 1,
    }

    def __init__(
        self,
        actions: Actions,
        memory: Memory,
        *,
        skill_repository: SkillRepository | None = None,
        config: AgentConfig | None = None,
        minedojo_client: MineDojoClient | None = None,
    ) -> None:
        self.actions = actions
        self.memory = memory
        repo = skill_repository
        if repo is None:
            seed_path = Path(__file__).resolve().parent / "skills" / "seed_library.json"
            repo = SkillRepository(
                SKILL_LIBRARY_PATH,
                seed_path=str(seed_path),
            )
        # Voyager 互換のスキルライブラリを共有し、タスク実行前に再利用候補を即座に取得する。
        self.skill_repository = repo
        self.config = config or AGENT_CONFIG
        langsmith_cfg = self.config.langsmith
        self._tracer = ThoughtActionObservationTracer(
            api_url=langsmith_cfg.api_url,
            api_key=langsmith_cfg.api_key,
            project=langsmith_cfg.project,
            default_tags=langsmith_cfg.tags,
            enabled=langsmith_cfg.enabled,
        )
        # 混雑時の背圧を明示的に制御するため、設定値に応じてキュー上限を固定する。
        self.queue: asyncio.Queue[ChatTask] = asyncio.Queue(
            maxsize=self.config.queue_max_size
        )
        # 設定値をローカル変数へコピーしておくことで、テスト時に差し込まれた構成も尊重する。
        self.default_move_target = self.config.default_move_target
        self.logger = setup_logger("agent.orchestrator")
        # LangGraph ベースのタスクハンドラを初期化して、カテゴリ別モジュールを明確化する。
        self._action_graph = ActionGraph(self)
        self._current_role_id: str = "generalist"
        self._pending_role: Optional[Tuple[str, Optional[str]]] = None
        self._shared_agents: Dict[str, Dict[str, Any]] = {}
        # LangGraph 側での意思決定に活用する閾値や履歴上限をまとめて保持する。
        self.low_food_threshold = LOW_FOOD_THRESHOLD
        self.structured_event_history_limit = STRUCTURED_EVENT_HISTORY_LIMIT
        self.perception_history_limit = PERCEPTION_HISTORY_LIMIT
        # MineDojo クライアントを初期化し、テスト時にはスタブを差し込めるようにする。
        self.minedojo_client = minedojo_client or MineDojoClient(self.config.minedojo)
        self._active_minedojo_mission: Optional[MineDojoMission] = None
        self._active_minedojo_demos: List[MineDojoDemonstration] = []
        self._active_minedojo_mission_id: Optional[str] = None
        self._active_minedojo_demo_metadata: Optional[MineDojoDemoMetadata] = None
        # 周辺環境の安全性を評価するため、Bridge HTTP クライアントを初期化しておく。
        self._bridge_client = BridgeClient()
        self._bridge_event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._bridge_event_stop: Optional[asyncio.Event] = None
        self._bridge_event_thread_stop: Optional[threading.Event] = None
        self._bridge_event_tasks: List[asyncio.Task[Any]] = []
        self._self_dialogue_executor = MineDojoSelfDialogueExecutor(
            actions=self.actions,
            client=self.minedojo_client,
            skill_repository=self.skill_repository,
            tracer=self._tracer,
            env_params={
                "sim_env": self.config.minedojo.sim_env,
                "sim_seed": self.config.minedojo.sim_seed,
                "sim_max_steps": self.config.minedojo.sim_max_steps,
            },
        )

    async def enqueue_chat(self, username: str, message: str) -> None:
        """WebSocket から受け取ったチャットをワーカーに積む。"""

        task = ChatTask(username=username, message=message)
        # 直近の指示を優先するため、キュー満杯時は最古のタスクを破棄して新規指示の受付を確保する。
        if self.queue.maxsize > 0 and self.queue.qsize() >= self.queue.maxsize:
            await self._handle_queue_overflow(task)
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
                await asyncio.wait_for(
                    self._process_chat(task),
                    timeout=self.config.worker_task_timeout_seconds,
                )
                elapsed = time.perf_counter() - started_at
                self.logger.info(
                    "worker processed username=%s duration=%.3fs remaining_queue=%d",
                    task.username,
                    elapsed,
                    self.queue.qsize(),
                )
            except asyncio.TimeoutError:
                elapsed = time.perf_counter() - started_at
                log_structured_event(
                    self.logger,
                    "chat task timed out; re-queuing or dropping per retry limit",
                    level=logging.WARNING,
                    event_level="warning",
                    context={
                        "username": task.username,
                        "duration_sec": round(elapsed, 3),
                        "timeout_limit_sec": self.config.worker_task_timeout_seconds,
                        "retry_count": task.retry_count,
                        "retry_limit": self._MAX_TASK_TIMEOUT_RETRY,
                    },
                    exc_info=True,
                )
                if task.retry_count < self._MAX_TASK_TIMEOUT_RETRY:
                    task.retry_count += 1
                    if self.queue.maxsize > 0 and self.queue.qsize() >= self.queue.maxsize:
                        await self._handle_queue_overflow(task)
                    await self.queue.put(task)
                    self.logger.warning(
                        "chat task timeout requeued username=%s retry=%d",
                        task.username,
                        task.retry_count,
                    )
                else:
                    await self.actions.say(
                        "処理が長時間停止したため、この指示をスキップしました。最新の指示を優先します。"
                    )
                    self.logger.error(
                        "chat task timeout dropped username=%s retry_limit=%d",
                        task.username,
                        self._MAX_TASK_TIMEOUT_RETRY,
                    )
            except Exception:
                self.logger.exception("failed to process chat task username=%s", task.username)
            finally:
                self.queue.task_done()

    async def start_bridge_event_listener(self) -> None:
        """AgentBridge のイベントストリーム購読タスクを起動する。"""

        if not BRIDGE_EVENT_STREAM_ENABLED:
            self.logger.info("bridge event stream disabled via env; skip listener setup")
            return
        if self._bridge_event_tasks:
            return

        self._bridge_event_stop = asyncio.Event()
        self._bridge_event_thread_stop = threading.Event()
        pump = asyncio.create_task(self._bridge_event_pump(), name="bridge-event-pump")
        consumer = asyncio.create_task(
            self._bridge_event_consumer(), name="bridge-event-consumer"
        )
        self._bridge_event_tasks.extend([pump, consumer])

    async def stop_bridge_event_listener(self) -> None:
        """イベント購読タスクを停止し、スレッドセーフに後始末する。"""

        if not self._bridge_event_tasks:
            return

        if self._bridge_event_stop:
            self._bridge_event_stop.set()
        if self._bridge_event_thread_stop:
            self._bridge_event_thread_stop.set()

        for task in list(self._bridge_event_tasks):
            task.cancel()
            with contextlib.suppress(Exception):
                await task
        self._bridge_event_tasks.clear()

    async def _handle_queue_overflow(self, incoming: ChatTask) -> None:
        """混雑時に最古のタスクを破棄し、最新チャットの受け付けを保証する。"""

        dropped: Optional[ChatTask] = None
        try:
            dropped = self.queue.get_nowait()
            # get() で取り出した分を完了扱いにして、未完了カウンタの不整合を防ぐ。
            self.queue.task_done()
        except asyncio.QueueEmpty:
            dropped = None

        log_structured_event(
            self.logger,
            "chat queue overflow detected; dropping oldest task to prioritize latest instruction",
            level=logging.WARNING,
            event_level="warning",
            context={
                "policy": "drop_oldest",
                "queue_size": self.queue.qsize(),
                "queue_max_size": self.queue.maxsize,
                "incoming_username": incoming.username,
                "dropped_username": getattr(dropped, "username", None),
            },
        )
        await self.actions.say(
            "処理が混雑しているため、古い指示をスキップし最新の指示を優先します。"
        )

    async def _bridge_event_pump(self) -> None:
        """SSE ストリームからのイベントをキューへ積むバックグラウンドタスク。"""

        if self._bridge_event_stop is None or self._bridge_event_thread_stop is None:
            return

        loop = asyncio.get_running_loop()

        def _enqueue(event: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._bridge_event_queue.put_nowait, event)

        while not self._bridge_event_stop.is_set():
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._bridge_client.consume_event_stream(
                        _enqueue, self._bridge_event_thread_stop
                    ),
                )
            except BridgeError as exc:
                log_structured_event(
                    self.logger,
                    "bridge event stream encountered recoverable error",
                    level=logging.WARNING,
                    event_level="warning",
                    langgraph_node_id="agent.bridge_events",
                    context={"error": str(exc)},
                )
            except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
                log_structured_event(
                    self.logger,
                    "bridge event stream failed unexpectedly",
                    level=logging.ERROR,
                    event_level="fault",
                    langgraph_node_id="agent.bridge_events",
                    context={"error": str(exc)},
                    exc_info=True,
                )

            if not self._bridge_event_stop.is_set():
                await asyncio.sleep(1.0)

    async def _bridge_event_consumer(self) -> None:
        """Bridge イベントキューを消費し、検出レポートへ整理する。"""

        if self._bridge_event_stop is None:
            return

        while not self._bridge_event_stop.is_set():
            try:
                payload = await asyncio.wait_for(
                    self._bridge_event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._handle_bridge_event(payload)
            finally:
                self._bridge_event_queue.task_done()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        """Node 側から届いたマルチエージェントイベントを解析して記憶する。"""

        events: List[Dict[str, Any]] = []
        raw_events = args.get("events")
        if isinstance(raw_events, list):
            events.extend([item for item in raw_events if isinstance(item, dict)])

        single_event = args.get("event")
        if isinstance(single_event, dict):
            events.append(single_event)

        if not events:
            self.logger.error("agent event payload missing event=%s", args)
            return

        for event in events:
            channel = str(event.get("channel", ""))
            if channel != "multi-agent":
                self.logger.warning("unsupported event channel=%s", channel)
                continue

            agent_id = str(event.get("agentId", "primary") or "primary")
            agent_state = dict(self._shared_agents.get(agent_id, {}))
            agent_state["timestamp"] = event.get("timestamp")

            kind = str(event.get("event", ""))
            payload = event.get("payload")
            if isinstance(payload, dict):
                agent_state.setdefault("events", []).append({"kind": kind, "payload": payload})

            if kind == "position" and isinstance(payload, dict):
                agent_state["position"] = payload
                formatted = self._format_position_payload(payload)
                if formatted:
                    self.memory.set("player_pos", formatted)
            elif kind == "status" and isinstance(payload, dict):
                agent_state["status"] = payload
                threat = str(payload.get("threatLevel", "")).lower()
                if threat in {"high", "critical"}:
                    self.request_role_switch("defender", reason="threat-alert")
                supply = str(payload.get("supplyDemand", "")).lower()
                if supply == "shortage":
                    self.request_role_switch("supplier", reason="supply-shortage")
            elif kind == "roleUpdate" and isinstance(payload, dict):
                role_id = str(payload.get("roleId", "generalist") or "generalist")
                role_label = str(payload.get("label", role_id))
                role_info = {
                    "id": role_id,
                    "label": role_label,
                    "reason": payload.get("reason"),
                    "responsibilities": payload.get("responsibilities"),
                }
                agent_state["role"] = role_info
                if agent_id == "primary":
                    self._current_role_id = role_id
                    self.memory.set("agent_active_role", role_info)

            self._shared_agents[agent_id] = agent_state

        self.memory.set("multi_agent", self._shared_agents)

    def request_role_switch(self, role_id: str, *, reason: Optional[str] = None) -> None:
        """LangGraph ノードからの役割切替要求をキューへ記録する。"""

        sanitized = (role_id or "").strip() or "generalist"
        if sanitized == self._current_role_id:
            return
        self._pending_role = (sanitized, reason)
        self.logger.info(
            "pending role switch registered role=%s reason=%s",
            sanitized,
            reason,
        )

    def _consume_pending_role_switch(self) -> Optional[Tuple[str, Optional[str]]]:
        pending = self._pending_role
        self._pending_role = None
        return pending

    @property
    def current_role(self) -> str:
        return self._current_role_id

    async def _apply_role_switch(self, role_id: str, reason: Optional[str]) -> bool:
        """実際に Node 側へ役割変更コマンドを送信し、成功時は記憶を更新する。"""

        if role_id == self._current_role_id:
            return False

        resp = await self.actions.set_role(role_id, reason=reason)
        if not resp.get("ok"):
            self.logger.warning("role switch command failed role=%s resp=%s", role_id, resp)
            return False

        label = None
        data = resp.get("data")
        if isinstance(data, dict):
            label_raw = data.get("label")
            if isinstance(label_raw, str):
                label = label_raw

        role_info = {
            "id": role_id,
            "label": label or role_id,
            "reason": reason,
        }
        self._current_role_id = role_id
        primary_state = self._shared_agents.setdefault("primary", {})
        primary_state["role"] = role_info
        self._shared_agents["primary"] = primary_state
        self.memory.set("agent_active_role", role_info)
        self.memory.set("multi_agent", self._shared_agents)
        self.logger.info("role switch applied role=%s label=%s", role_id, role_info["label"])
        return True

    async def _prime_status_for_planning(self) -> None:
        """LLM へ渡す前に Mineflayer 状況を収集し、メモリへ反映する。"""

        requested = ["general"]
        if not self.memory.get("player_pos_detail"):
            requested.append("position")
        if not self.memory.get("inventory_detail"):
            requested.append("inventory")

        failures: List[str] = []
        for kind in requested:
            ok = await self._request_status_with_backoff(kind)
            if not ok:
                failures.append(kind)

        if failures:
            await self._report_execution_barrier(
                "状態取得",
                f"{', '.join(failures)} の取得に失敗しました。Mineflayer への接続状況を確認してください。",
            )

    async def _request_status_with_backoff(self, kind: str) -> bool:
        """タイムアウトと指数バックオフ付きで gather_status を呼び出す。"""

        backoff = STATUS_REFRESH_BACKOFF_SECONDS
        for attempt in range(1, STATUS_REFRESH_RETRY + 2):
            try:
                resp = await asyncio.wait_for(
                    self.actions.gather_status(kind),
                    timeout=STATUS_REFRESH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "gather_status timed out kind=%s attempt=%d", kind, attempt
                )
                resp = None
            except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
                self.logger.exception(
                    "gather_status raised unexpected error kind=%s attempt=%d", kind, attempt
                )
                resp = {"ok": False, "error": str(exc)}

            if isinstance(resp, dict) and resp.get("ok"):
                self._cache_status(kind, resp.get("data") or {})
                return True

            if attempt <= STATUS_REFRESH_RETRY:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            error_detail = "Mineflayer から応答がありません。"
            if isinstance(resp, dict) and resp.get("error"):
                error_detail = str(resp.get("error"))
            self.logger.warning(
                "gather_status failed permanently kind=%s error=%s", kind, error_detail
            )
        return False

    def _cache_status(self, kind: str, data: Dict[str, Any]) -> None:
        """gather_status の結果を要約し、再利用しやすい形で保存する。"""

        if kind == "position":
            summary = self._summarize_position_status(data)
            self.memory.set("player_pos", summary)
            self.memory.set("player_pos_detail", data)
            return

        if kind == "inventory":
            summary = self._summarize_inventory_status(data)
            self.memory.set("inventory", summary)
            self.memory.set("inventory_detail", data)
            return

        if kind == "general":
            summary = self._summarize_general_status(data)
            self.memory.set("general_status", summary)
            self.memory.set("general_status_detail", data)
            if isinstance(data, dict) and "digPermission" in data:
                self.memory.set("dig_permission", data.get("digPermission"))
            self._record_structured_event_history(data)
            self._store_perception_from_status(data)
            return

        self.logger.info("cache_status skipped unknown kind=%s", kind)

    def _record_structured_event_history(self, payload: Dict[str, Any]) -> None:
        """Mineflayer 側の構造化イベント配列を履歴に蓄積する。"""

        history = self._load_history("structured_event_history")
        limit = getattr(self, "structured_event_history_limit", STRUCTURED_EVENT_HISTORY_LIMIT)
        for key in ("structuredEvents", "events", "eventHistory"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                new_events = [item for item in candidate if isinstance(item, dict)]
                if new_events:
                    history.extend(new_events)
                break

        trimmed = history[-limit:]
        self.memory.set("structured_event_history", trimmed)

    def _store_perception_from_status(self, status: Dict[str, Any]) -> None:
        """general ステータスに含まれる perception 情報を履歴へ追加する。"""

        perception_payload = None
        for key in ("perception", "perceptionSnapshot", "perception_snapshot"):
            candidate = status.get(key)
            if isinstance(candidate, dict):
                perception_payload = candidate
                break

        snapshot = self._build_perception_snapshot(perception_payload)
        if snapshot is None:
            return

        history = self._append_perception_snapshot(snapshot)
        self.memory.set("perception_snapshots", history)

    def _build_perception_snapshot(self, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """位置・空腹度・天候をまとめた perception スナップショットを生成する。"""

        pos_detail = self.memory.get("player_pos_detail") or {}
        general_detail = self.memory.get("general_status_detail") or {}
        if not isinstance(pos_detail, dict):
            pos_detail = {}
        if not isinstance(general_detail, dict):
            general_detail = {}
        base = extra if isinstance(extra, dict) else {}

        position = None
        if all(axis in pos_detail for axis in ("x", "y", "z")):
            position = {
                "x": pos_detail.get("x"),
                "y": pos_detail.get("y"),
                "z": pos_detail.get("z"),
                "dimension": pos_detail.get("dimension") or pos_detail.get("world"),
            }

        hunger = base.get("food") or base.get("foodLevel") or base.get("hunger")
        if hunger is None:
            hunger = (
                general_detail.get("food")
                or general_detail.get("foodLevel")
                or general_detail.get("hunger")
            )

        weather = base.get("weather") or general_detail.get("weather")

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position": position,
            "food_level": hunger,
            "health": base.get("health") or general_detail.get("health"),
            "weather": weather,
            "is_raining": base.get("isRaining") or general_detail.get("isRaining"),
        }

        if not any(value is not None for value in snapshot.values()):
            return None

        return snapshot

    def _append_perception_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """perception スナップショットを履歴へ追加し、上限件数で丸める。"""

        history = self._load_history("perception_snapshots")
        limit = getattr(self, "perception_history_limit", PERCEPTION_HISTORY_LIMIT)
        history.append(snapshot)
        return history[-limit:]

    def _load_history(self, key: str) -> List[Dict[str, Any]]:
        """メモリに格納された履歴リストを辞書のみ抽出して返す。"""

        raw = self.memory.get(key, [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _collect_recent_mineflayer_context(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Mineflayer 由来の履歴を LangGraph へ渡すためにまとめて取得する。"""

        structured_event_history = self._load_history("structured_event_history")
        perception_history = self._load_history("perception_snapshots")

        # 直近のメモリ内容から最新スナップショットを生成し、欠損時にも状態復元できるようにする。
        snapshot = self._build_perception_snapshot()
        if snapshot:
            perception_history.append(snapshot)
        event_limit = getattr(self, "structured_event_history_limit", STRUCTURED_EVENT_HISTORY_LIMIT)
        perception_limit = getattr(self, "perception_history_limit", PERCEPTION_HISTORY_LIMIT)
        structured_event_history = structured_event_history[-event_limit:]
        perception_history = perception_history[-perception_limit:]

        if snapshot:
            self.memory.set("perception_snapshots", perception_history)
        if structured_event_history:
            self.memory.set("structured_event_history", structured_event_history)

        return structured_event_history, perception_history

    async def _collect_block_evaluations(self) -> None:
        """Bridge から近傍ブロックの情報を収集し、危険度の概略をメモリへ保持する。"""

        detail = self.memory.get("player_pos_detail") or {}
        try:
            x = int(detail.get("x"))
            y = int(detail.get("y"))
            z = int(detail.get("z"))
        except Exception:
            self.logger.info(
                "skip block evaluation because player position detail is unavailable"
            )
            return

        world = str(detail.get("dimension") or detail.get("world") or "world")
        positions: List[Dict[str, int]] = []
        for dx in range(-BLOCK_EVAL_RADIUS, BLOCK_EVAL_RADIUS + 1):
            for dy in range(-BLOCK_EVAL_HEIGHT_DELTA, BLOCK_EVAL_HEIGHT_DELTA + 1):
                for dz in range(-BLOCK_EVAL_RADIUS, BLOCK_EVAL_RADIUS + 1):
                    positions.append({"x": x + dx, "y": y + dy, "z": z + dz})

        loop = asyncio.get_running_loop()
        try:
            evaluations = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: self._bridge_client.bulk_eval(world, positions)
                ),
                timeout=BLOCK_EVAL_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, BridgeError) as exc:
            self.logger.warning(
                "block evaluation failed world=%s error=%s", world, exc
            )
            return
        except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
            self.logger.exception("unexpected error during block evaluation", exc_info=exc)
            return

        summary = self._summarize_block_evaluations(evaluations)
        self.memory.set("block_evaluation", summary)

    async def _process_chat(self, task: ChatTask) -> None:
        """単一のチャット指示に対して LLM 計画とアクション実行を行う。"""

        await self._prime_status_for_planning()
        await self._collect_block_evaluations()
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
        self._record_plan_summary(plan_out)

        structured_coords = self._extract_argument_coordinates(plan_out.arguments)
        if structured_coords:
            self.logger.info(
                "plan arguments provided coordinates=%s", structured_coords
            )
        initial_target = structured_coords or user_hint_coords

        # LLM の丁寧な応答をそのままプレイヤーへ relay する。
        if plan_out.resp:
            self.logger.info(
                "relaying llm response to player username=%s resp='%s'",
                task.username,
                plan_out.resp,
            )
            await self.actions.say(plan_out.resp)

        await self._execute_plan(plan_out, initial_target=initial_target)
        self.memory.set("last_chat", {"username": task.username, "message": task.message})

    def _format_position_payload(self, payload: Dict[str, Any]) -> Optional[str]:
        """位置イベントからコンテキスト表示用の文字列を生成する。"""

        x = payload.get("x")
        y = payload.get("y")
        z = payload.get("z")
        if not all(isinstance(value, (int, float)) for value in (x, y, z)):
            return None
        dimension = payload.get("dimension")
        dimension_label = dimension if isinstance(dimension, str) and dimension else "unknown"
        return f"X={int(x)} / Y={int(y)} / Z={int(z)}（ディメンション: {dimension_label}）"

    def _build_context_snapshot(self) -> Dict[str, Any]:
        """LLM へ渡す簡易コンテキストを生成する。"""

        snapshot = {
            "player_pos": self.memory.get("player_pos", "不明"),
            "inventory_summary": self.memory.get("inventory", "不明"),
            "general_status": self.memory.get("general_status", "未記録"),
            "dig_permission": self.memory.get("dig_permission", "未評価"),
            "last_chat": self.memory.get("last_chat", "未記録"),
            "last_destination": self.memory.get("last_destination", "未記録"),
            "active_role": self.memory.get(
                "agent_active_role",
                {"id": self._current_role_id, "label": "汎用サポーター"},
            ),
        }
        minedojo_context = self.memory.get("minedojo_context")
        if minedojo_context:
            snapshot["minedojo_support"] = minedojo_context
        block_eval = self.memory.get("block_evaluation")
        if block_eval:
            snapshot["block_evaluation"] = block_eval
        structured_history = self.memory.get("structured_event_history")
        if isinstance(structured_history, list) and structured_history:
            snapshot["structured_event_history"] = structured_history[-3:]
        perception_history = self.memory.get("perception_snapshots")
        if isinstance(perception_history, list) and perception_history:
            snapshot["perception_history"] = perception_history[-3:]
        last_plan_summary = self.memory.get("last_plan_summary")
        if isinstance(last_plan_summary, dict) and last_plan_summary:
            snapshot["last_plan_summary"] = last_plan_summary
        reflection_context = self.memory.build_reflection_context()
        if reflection_context:
            snapshot["recent_reflections"] = reflection_context
        active_reflection_prompt = self.memory.get_active_reflection_prompt()
        if active_reflection_prompt:
            snapshot["active_reflection_prompt"] = active_reflection_prompt
        recovery_hints = self.memory.get("recovery_hints")
        if isinstance(recovery_hints, list) and recovery_hints:
            snapshot["recovery_hints"] = recovery_hints
        self.logger.info("context snapshot built=%s", snapshot)
        return snapshot

    def _record_plan_summary(self, plan_out: PlanOut) -> None:
        """ゴール・制約・directive を Memory と構造化ログへ残す。"""

        goal_summary = ""
        priority = ""
        goal_category = ""
        if getattr(plan_out, "goal_profile", None):
            goal_summary = plan_out.goal_profile.summary or ""
            priority = plan_out.goal_profile.priority or ""
            goal_category = plan_out.goal_profile.category or ""
        constraints = [constraint.label for constraint in getattr(plan_out, "constraints", []) if constraint.label]
        directive_count = len(getattr(plan_out, "directives", []) or [])
        payload = {
            "goal": goal_summary,
            "goal_category": goal_category,
            "goal_priority": priority,
            "constraint_count": len(constraints),
            "intent": plan_out.intent,
            "directive_count": directive_count,
        }
        if constraints:
            payload["constraints"] = constraints[:3]
        self.memory.set("last_plan_summary", payload)
        if getattr(plan_out, "recovery_hints", None):
            self.memory.set("recovery_hints", list(plan_out.recovery_hints))
        log_structured_event(
            self.logger,
            "plan_summary",
            event_level="progress",
            langgraph_node_id="planner.plan_summary",
            context=payload,
        )

    async def _handle_minedojo_directive(self, directive: ActionDirective, plan_out: PlanOut, step_index: int) -> bool:
        executor = getattr(self, "_self_dialogue_executor", None)
        if executor is None:
            return False

        args = directive.args if isinstance(directive.args, dict) else {}
        mission_id = ""
        mission_candidate = args.get("mission_id")
        if isinstance(mission_candidate, str) and mission_candidate.strip():
            mission_id = mission_candidate.strip()
        if not mission_id:
            mission_id = self._MINEDOJO_MISSION_BINDINGS.get(directive.category, "")
        if not mission_id:
            return False

        skill_id = args.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            skill_id = f"minedojo::{mission_id}::{int(time.time())}"
        title = directive.label or directive.step or f"MineDojo {mission_id}"
        success_flag = args.get("simulate_success")
        success = bool(success_flag) if isinstance(success_flag, bool) else True

        try:
            await executor.run_self_dialogue(
                mission_id,
                plan_out.react_trace or [],
                skill_id=skill_id,
                title=title,
                success=success,
            )
        except Exception:
            self.logger.exception(
                "MineDojo directive failed mission=%s step_index=%d", mission_id, step_index
            )
            return False

        self.logger.info(
            "MineDojo directive executed mission=%s skill_id=%s step_index=%d",
            mission_id,
            skill_id,
            step_index,
        )
        return True

    def _resolve_directive_for_step(
        self,
        directives: Sequence[Any],
        index: int,
        fallback_step: str,
    ) -> Optional[ActionDirective]:
        if not directives or index - 1 >= len(directives):
            return None
        candidate = directives[index - 1]
        if isinstance(candidate, ActionDirective):
            return candidate
        if isinstance(candidate, dict):
            try:
                directive = ActionDirective.model_validate(candidate)
            except Exception:
                self.logger.warning("directive validation failed index=%d payload=%s", index, candidate)
                return None
            if not directive.step:
                directive.step = fallback_step
            return directive
        return None

    def _build_directive_meta(
        self,
        directive: Optional[ActionDirective],
        plan_out: PlanOut,
        index: int,
        total_steps: int,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(directive, ActionDirective):
            return None
        directive_id = directive.directive_id or f"step-{index}"
        goal_summary = ""
        if getattr(plan_out, "goal_profile", None):
            goal_summary = plan_out.goal_profile.summary or ""
        return {
            "directiveId": directive_id,
            "directiveLabel": directive.label or directive.step or "",
            "directiveCategory": directive.category or plan_out.intent,
            "directiveExecutor": directive.executor or "mineflayer",
            "planIntent": plan_out.intent,
            "goalSummary": goal_summary,
            "stepIndex": index,
            "totalSteps": total_steps,
        }

    def _extract_directive_coordinates(
        self,
        directive: Optional[ActionDirective],
    ) -> Optional[Tuple[int, int, int]]:
        if not isinstance(directive, ActionDirective):
            return None
        args = directive.args if isinstance(directive.args, dict) else {}
        candidates: List[Any] = []
        for key in ("coordinates", "position"):
            if key in args:
                candidates.append(args[key])
        path = args.get("path")
        if isinstance(path, list) and path:
            candidates.append(path[0])

        for candidate in candidates:
            coords = self._coerce_coordinate_tuple(candidate)
            if coords:
                return coords
        return None

    def _coerce_coordinate_tuple(self, payload: Any) -> Optional[Tuple[int, int, int]]:
        if not isinstance(payload, dict):
            return None
        try:
            x = int(payload.get("x"))
            y = int(payload.get("y"))
            z = int(payload.get("z"))
        except Exception:
            return None
        return (x, y, z)

    @contextlib.contextmanager
    def _directive_scope(self, meta: Optional[Dict[str, Any]]):
        has_interface = hasattr(self.actions, "begin_directive_scope") and hasattr(
            self.actions, "end_directive_scope"
        )
        if meta and has_interface:
            self.actions.begin_directive_scope(meta)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if meta and has_interface:
                self.actions.end_directive_scope()  # type: ignore[attr-defined]

    async def _execute_plan(
        self,
        plan_out: PlanOut,
        *,
        initial_target: Optional[Tuple[int, int, int]] = None,
        replan_depth: int = 0,
    ) -> None:
        """LLM が出力した高レベルステップを簡易ヒューリスティックで実行する。

        Args:
            plan_out: LLM から取得した行動計画と応答文。
            initial_target: プレイヤーが元のチャットで直接指定した座標。LLM の
                ステップに座標が含まれなくても直ちに移動へ移れるよう、初期値
                として利用する。
        """

        action_backlog: List[Dict[str, str]] = list(getattr(plan_out, "backlog", []) or [])
        if plan_out.blocking or plan_out.clarification_needed != "none" or plan_out.next_action == "chat":
            follow_up_message = plan_out.resp.strip() or "作業内容を確認させてください。"
            self.logger.info(
                "plan execution paused for confirmation: blocking=%s clarification=%s confidence=%.2f backlog=%s",
                plan_out.blocking,
                plan_out.clarification_needed,
                plan_out.confidence,
                action_backlog,
            )
            if follow_up_message:
                await self.actions.say(follow_up_message)
            # チャット送信後も再試行可能な形で ActionGraph のバックログへ戻す。
            action_backlog.append(
                {
                    "category": "chat",
                    "label": "フォローアップ質問",
                    "message": follow_up_message,
                    "reason": plan_out.clarification_needed or ("blocking" if plan_out.blocking else "none"),
                }
            )
            await self._handle_action_backlog(action_backlog, already_responded=True)
            return

        total_steps = len(plan_out.plan)
        argument_coords = self._extract_argument_coordinates(plan_out.arguments)
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
        detection_reports: List[Dict[str, Any]] = []
        react_trace: List[ReActStep] = list(plan_out.react_trace)
        directives: List[Any] = list(getattr(plan_out, "directives", []) or [])
        for index, step in enumerate(plan_out.plan, start=1):
            normalized = step.strip()
            self.logger.info(
                "plan_step index=%d/%d raw='%s' normalized='%s'",
                index,
                total_steps,
                step,
                normalized,
            )
            react_entry: Optional[ReActStep] = None
            if 0 <= index - 1 < len(react_trace):
                candidate = react_trace[index - 1]
                if isinstance(candidate, ReActStep):
                    react_entry = candidate

            thought_text = react_entry.thought.strip() if react_entry else ""
            observation_text = ""
            status = "skipped"
            event_level = "trace"
            log_level = logging.INFO
            directive = self._resolve_directive_for_step(directives, index, normalized)
            directive_meta = self._build_directive_meta(directive, plan_out, index, total_steps)
            directive_executor = directive.executor if isinstance(directive, ActionDirective) else ""
            directive_coords = self._extract_directive_coordinates(directive) if directive else None

            if not normalized:
                observation_text = "ステップ文字列が空だったためスキップしました。"
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action="",
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                continue

            if directive and directive_executor == "minedojo":
                handled = await self._handle_minedojo_directive(directive, plan_out, index)
                if handled:
                    observation_text = "MineDojo の自己対話タスクを実行しました。"
                    status = "completed"
                    event_level = "progress"
                    if react_entry:
                        react_entry.observation = observation_text
                    self._emit_react_log(
                        index=index,
                        total_steps=total_steps,
                        thought=thought_text,
                        action=normalized,
                        observation=observation_text,
                        status=status,
                        event_level=event_level,
                        log_level=log_level,
                    )
                    continue

            if directive and directive_executor == "chat":
                chat_message = str(directive.args.get("message") if isinstance(directive.args, dict) else "") or directive.label or normalized
                if chat_message:
                    with self._directive_scope(directive_meta):
                        await self.actions.say(chat_message)
                    observation_text = f"チャット通知を送信: {chat_message}"
                    status = "completed"
                    event_level = "progress"
                    if react_entry:
                        react_entry.observation = observation_text
                    self._emit_react_log(
                        index=index,
                        total_steps=total_steps,
                        thought=thought_text,
                        action=normalized,
                        observation=observation_text,
                        status=status,
                        event_level=event_level,
                        log_level=log_level,
                    )
                    continue

            detection_category = None
            if directive and directive.category in self._DETECTION_TASK_KEYWORDS:
                detection_category = directive.category
            if not detection_category:
                detection_category = self._classify_detection_task(normalized)
            if not detection_category and plan_out.intent.strip().lower().startswith("report"):
                detection_category = "general_status"
            if detection_category:
                self.logger.info(
                    "plan_step index=%d classified as detection_report category=%s",
                    index,
                    detection_category,
                )
                with self._directive_scope(directive_meta):
                    detection_result = await self._perform_detection_task(
                        detection_category
                    )
                if detection_result:
                    detection_reports.append(detection_result)
                    observation_text = str(
                        detection_result.get("summary")
                        or "ステータスを報告しました。"
                    )
                    data = detection_result.get("data")
                    if isinstance(data, dict):
                        coords = (data.get("x"), data.get("y"), data.get("z"))
                        if all(isinstance(coord, (int, float)) for coord in coords):
                            # caplog で位置報告を明示的に追跡できるよう、座標を含む
                            # 文字列へ置き換える。メンバーが動作確認しやすいよう、
                            # 人間可読なフォーマットを採用する。
                            observation_text = (
                                f"位置報告: X={int(coords[0])} / Y={int(coords[1])} / Z={int(coords[2])}"
                            )
                    status = "completed"
                    event_level = "progress"
                else:
                    observation_text = "ステータス取得に失敗し障壁を報告しました。"
                    status = "failed"
                    event_level = "fault"
                    log_level = logging.WARNING
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                continue

            coords = directive_coords or argument_coords or self._extract_coordinates(normalized)
            if coords:
                self.logger.info(
                    "plan_step index=%d classified as coordinate_move coords=%s",
                    index,
                    coords,
                )
                with self._directive_scope(directive_meta):
                    handled, last_target_coords, failure_detail = await self._handle_action_task(
                        "move",
                        normalized,
                        last_target_coords=coords,
                        backlog=action_backlog,
                        explicit_coords=coords,
                    )
                if not handled:
                    observation_text = failure_detail or "座標移動の処理に失敗しました。"
                    status = "failed"
                    event_level = "fault"
                    log_level = logging.WARNING
                    if react_entry:
                        react_entry.observation = observation_text
                    self._emit_react_log(
                        index=index,
                        total_steps=total_steps,
                        thought=thought_text,
                        action=normalized,
                        observation=observation_text,
                        status=status,
                        event_level=event_level,
                        log_level=log_level,
                    )
                    await self._handle_plan_failure(
                        failed_step=normalized,
                        failure_reason=
                            failure_detail
                            or "座標移動の処理に失敗しました。Mineflayer の応答を確認してください。",
                        detection_reports=detection_reports,
                        action_backlog=action_backlog,
                        remaining_steps=plan_out.plan[index:],
                        replan_depth=replan_depth,
                    )
                    return

                target_coords = last_target_coords or coords
                if target_coords:
                    observation_text = (
                        f"移動成功: X={target_coords[0]} / Y={target_coords[1]} / Z={target_coords[2]}"
                    )
                else:
                    observation_text = "移動に成功しました。"
                status = "completed"
                event_level = "progress"
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
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
                observation_text = "ステータス確認ステップのため実行不要と判断しました。"
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                continue

            if await self._attempt_proactive_progress(normalized, last_target_coords):
                observation_text = "前回の目的地へ継続移動しました。"
                status = "completed"
                event_level = "progress"
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                continue

            action_category = None
            if directive and directive.category in self._ACTION_TASK_RULES:
                action_category = directive.category
            if not action_category:
                action_category = self._classify_action_task(normalized)
            if action_category:
                self.logger.info(
                    "plan_step index=%d classified as action_task category=%s",
                    index,
                    action_category,
                )
                with self._directive_scope(directive_meta):
                    handled, last_target_coords, failure_detail = await self._handle_action_task(
                        action_category,
                        normalized,
                        last_target_coords=last_target_coords,
                        backlog=action_backlog,
                        explicit_coords=directive_coords if action_category == "move" else None,
                    )
                if handled:
                    if action_category == "move":
                        destination = last_target_coords or self.default_move_target
                        if destination:
                            observation_text = (
                                f"移動成功: X={destination[0]} / Y={destination[1]} / Z={destination[2]}"
                            )
                        else:
                            observation_text = "移動に成功しました。"
                    else:
                        observation_text = f"{action_category} タスクを完了しました。"
                    status = "completed"
                    event_level = "progress"
                    if react_entry:
                        react_entry.observation = observation_text
                    self._emit_react_log(
                        index=index,
                        total_steps=total_steps,
                        thought=thought_text,
                        action=normalized,
                        observation=observation_text,
                        status=status,
                        event_level=event_level,
                        log_level=log_level,
                    )
                    continue

                observation_text = (
                    failure_detail
                    or "Mineflayer からアクションが拒否され、残りの計画を進められませんでした。"
                )
                status = "failed"
                event_level = "fault"
                log_level = logging.WARNING
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                await self._handle_plan_failure(
                    failed_step=normalized,
                    failure_reason=
                        failure_detail
                        or "Mineflayer からアクションが拒否され、残りの計画を進められませんでした。",
                    detection_reports=detection_reports,
                    action_backlog=action_backlog,
                    remaining_steps=plan_out.plan[index:],
                    replan_depth=replan_depth,
                )
                return

            if "報告" in normalized or "伝える" in normalized:
                self.logger.info(
                    "plan_step index=%d issuing status_report",
                    index,
                )
                with self._directive_scope(directive_meta):
                    await self.actions.say("進捗を確認しています。続報をお待ちください。")
                observation_text = "進捗報告メッセージを送信しました。"
                status = "completed"
                event_level = "progress"
                if react_entry:
                    react_entry.observation = observation_text
                self._emit_react_log(
                    index=index,
                    total_steps=total_steps,
                    thought=thought_text,
                    action=normalized,
                    observation=observation_text,
                    status=status,
                    event_level=event_level,
                    log_level=log_level,
                )
                continue

            self.logger.info(
                "plan_step index=%d no_direct_mapping step='%s'",
                index,
                normalized,
            )
            observation_text = "対応可能なアクションが見つからず障壁を通知しました。"
            status = "failed"
            event_level = "fault"
            log_level = logging.WARNING
            if react_entry:
                react_entry.observation = observation_text
            self._emit_react_log(
                index=index,
                total_steps=total_steps,
                thought=thought_text,
                action=normalized,
                observation=observation_text,
                status=status,
                event_level=event_level,
                log_level=log_level,
            )
            await self._report_execution_barrier(
                normalized,
                "対応可能なアクションが見つからず停滞しています。計画ステップの表現を見直してください。",
            )
            continue

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

        # 計画が最後まで完了した場合は pending 状態の反省ログへ成功結果を書き戻す。
        completed_reflection = self.memory.finalize_pending_reflection(
            outcome="success",
            detail="計画ステップを完了",
        )
        if completed_reflection:
            self.logger.info(
                "reflection session marked as success id=%s", completed_reflection.id
            )

    def _emit_react_log(
        self,
        *,
        index: int,
        total_steps: int,
        thought: str,
        action: str,
        observation: str,
        status: str,
        event_level: str,
        log_level: int,
    ) -> None:
        """ReAct ループの Thought/Action/Observation を構造化ログへ出力する。"""

        context = {
            "step_index": index,
            "total_steps": total_steps,
            "thought": thought,
            "action": action,
            "observation": observation,
            "status": status,
        }
        # caplog 経由で ReAct ログを確実に解析できるよう、構造化ログとは別に
        # JSON 文字列を明示的に出力する。新人メンバーが pytest 上で挙動を
        # 追いやすいよう、必要最低限のメタデータを含めたメッセージを残す。
        raw_payload = {
            "message": "react_step",
            "event_level": event_level,
            "langgraph_node_id": "agent.react_loop",
            "context": context,
        }
        self.logger.log(log_level, json.dumps(raw_payload, ensure_ascii=False))
        log_structured_event(
            self.logger,
            "react_step",
            level=log_level,
            event_level=event_level,
            langgraph_node_id="agent.react_loop",
            context=context,
        )

    async def _handle_plan_failure(
        self,
        *,
        failed_step: str,
        failure_reason: str,
        detection_reports: List[Dict[str, Any]],
        action_backlog: List[Dict[str, str]],
        remaining_steps: List[str],
        replan_depth: int,
    ) -> None:
        """Mineflayer 側の失敗で計画を続行できない場合の回復処理をまとめる。"""

        await self._report_execution_barrier(failed_step, failure_reason)

        # 直前の再試行が完了していない状態で失敗が再発した場合は、結果を明示的に記録する。
        previous_pending = self.memory.finalize_pending_reflection(
            outcome="failed",
            detail=f"step='{failed_step}' reason='{failure_reason}'",
        )
        if previous_pending:
            self.logger.info(
                "previous reflection marked as failed id=%s", previous_pending.id
            )

        merged_detection_reports: List[Dict[str, Any]] = list(detection_reports)
        bridge_reports = self.memory.get("bridge_event_reports", [])
        if isinstance(bridge_reports, list) and bridge_reports:
            merged_detection_reports.extend(bridge_reports[-5:])
            failure_reason = self._augment_failure_reason_with_events(
                failure_reason, bridge_reports
            )

        task_signature = self.memory.derive_task_signature(failed_step)
        previous_reflections = self.memory.export_reflections_for_prompt(
            task_signature=task_signature,
            limit=3,
        )
        # 失敗状況と過去の学習履歴をまとめ、次回 plan() へ渡す Reflexion プロンプトを生成する。
        reflection_prompt = build_reflection_prompt(
            failed_step,
            failure_reason,
            detection_reports=merged_detection_reports,
            action_backlog=action_backlog,
            previous_reflections=previous_reflections,
        )
        # 永続化ログへ改善案を追加し、plan() の文脈へ差し込めるように保持する。
        self.memory.begin_reflection(
            task_signature=task_signature,
            failed_step=failed_step,
            failure_reason=failure_reason,
            improvement=reflection_prompt,
            metadata={
                "detection_reports": list(merged_detection_reports),
                "action_backlog": list(action_backlog),
                "remaining_steps": list(remaining_steps),
            },
        )
        self.memory.set("last_reflection_prompt", reflection_prompt)
        self.memory.set(
            "recovery_hints",
            [
                f"step:{failed_step}",
                failure_reason,
            ],
        )

        if merged_detection_reports:
            await self._handle_detection_reports(
                merged_detection_reports,
                already_responded=True,
            )

        if action_backlog:
            await self._handle_action_backlog(
                action_backlog,
                already_responded=True,
            )

        await self._request_replan(
            failed_step=failed_step,
            failure_reason=failure_reason,
            remaining_steps=remaining_steps,
            replan_depth=replan_depth,
        )

    async def _request_replan(
        self,
        *,
        failed_step: str,
        failure_reason: str,
        remaining_steps: List[str],
        replan_depth: int,
    ) -> None:
        """障壁内容を踏まえて LLM へ再計画を依頼し、後続ステップを自動調整する。"""

        if replan_depth >= self._MAX_REPLAN_DEPTH:
            self.logger.warning(
                "skip replan because max depth reached step='%s' reason='%s'",
                failed_step,
                failure_reason,
            )
            return

        context = self._build_context_snapshot()
        inventory_detail = self.memory.get("inventory_detail")
        # 所持品詳細を replan コンテキストへ含めることで、直前の装備失敗で
        # ツルハシが不足しているなどの状況を LLM へ明確に伝えられる。
        if inventory_detail is not None:
            context["inventory_detail"] = inventory_detail
        remaining_text = "、".join(remaining_steps) if remaining_steps else ""
        replan_instruction = (
            f"手順「{failed_step}」の実行に失敗しました（{failure_reason}）。"
            "現在の状況を踏まえて作業を継続するための別案を提示してください。"
        )
        if remaining_text:
            replan_instruction += f" 未完了ステップ候補: {remaining_text}"

        # Reflexion プロンプトを再計画メッセージの冒頭へ付与し、LLM へ明示的な振り返りを促す。
        reflection_prompt = self.memory.get_active_reflection_prompt()
        if reflection_prompt:
            replan_instruction = f"{reflection_prompt}\n\n{replan_instruction}"

        self.logger.info(
            "requesting replan depth=%d instruction='%s' context=%s",
            replan_depth + 1,
            replan_instruction,
            context,
        )

        new_plan = await plan(replan_instruction, context)

        if new_plan.resp.strip():
            await self.actions.say(new_plan.resp)

        await self._execute_plan(
            new_plan,
            initial_target=None,
            replan_depth=replan_depth + 1,
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

    def _infer_mining_request(self, text: str) -> Dict[str, Any]:
        """採掘ステップから鉱石種類と探索パラメータを推測する。"""

        normalized = text.lower()
        targets: List[str] = []
        keyword_map = (
            (
                ("レッドストーン", "redstone"),
                ["redstone_ore", "deepslate_redstone_ore"],
            ),
            (("ダイヤ", "ダイア", "diamond"), ["diamond_ore", "deepslate_diamond_ore"]),
            (("ラピス", "lapis"), ["lapis_ore", "deepslate_lapis_ore"]),
            (("鉄", "iron"), ["iron_ore", "deepslate_iron_ore"]),
            (("金", "gold"), ["gold_ore", "deepslate_gold_ore"]),
            (("石炭", "coal"), ["coal_ore", "deepslate_coal_ore"]),
        )

        for keywords, ores in keyword_map:
            if any(keyword in text for keyword in keywords) or any(
                keyword in normalized for keyword in keywords
            ):
                for ore in ores:
                    if ore not in targets:
                        targets.append(ore)

        if not targets:
            # 指定がない場合はレッドストーン採掘を想定した既定値に倒す。
            # プレイヤーが抽象的に「鉱石を掘って」と述べたケースでも、
            # もっとも要望が多いレッドストーン収集に先回りで対応する。
            targets = ["redstone_ore", "deepslate_redstone_ore"]

        scan_radius = 12
        if "広範囲" in text or "探し回" in text:
            scan_radius = 18
        elif "近く" in text or "付近" in text:
            scan_radius = 8

        max_targets = 3
        if "大量" in text or "たくさん" in text or "複数" in text:
            max_targets = 5
        elif "一つ" in text or "ひとつ" in text:
            max_targets = 1

        request = {
            "targets": targets,
            "scan_radius": scan_radius,
            "max_targets": max_targets,
        }
        return request

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
            move_ok, move_error = await self._move_to_coordinates(last_target_coords)
            if not move_ok and move_error:
                await self._report_execution_barrier(
                    step,
                    f"継続移動に失敗しました（{move_error}）。",
                )
            return move_ok

        return False

    async def _perform_detection_task(self, category: str) -> Optional[Dict[str, Any]]:
        """Mineflayer 側のステータス取得コマンドを実行し、メモリと報告用要約を更新する。"""

        if category == "player_position":
            resp = await self.actions.gather_status("position")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が現在位置を返しませんでした。"
                await self._report_execution_barrier(
                    "現在位置の確認",
                    f"ステータス取得に失敗しました（{error_detail}）。",
                )
                return None

            data = resp.get("data") or {}
            summary = self._summarize_position_status(data)
            self.memory.set("player_pos", summary)
            self.memory.set("player_pos_detail", data)
            return {"category": category, "summary": summary, "data": data}

        if category == "inventory_status":
            resp = await self.actions.gather_status("inventory")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が所持品を返しませんでした。"
                await self._report_execution_barrier(
                    "所持品の確認",
                    f"ステータス取得に失敗しました（{error_detail}）。",
                )
                return None

            data = resp.get("data") or {}
            summary = self._summarize_inventory_status(data)
            self.memory.set("inventory", summary)
            self.memory.set("inventory_detail", data)
            return {"category": category, "summary": summary, "data": data}

        if category == "general_status":
            resp = await self.actions.gather_status("general")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が状態値を返しませんでした。"
                await self._report_execution_barrier(
                    "状態の共有",
                    f"ステータス取得に失敗しました（{error_detail}）。",
                )
                return None

            data = resp.get("data") or {}
            summary = self._summarize_general_status(data)
            self.memory.set("general_status", summary)
            self.memory.set("general_status_detail", data)
            if isinstance(data, dict) and "digPermission" in data:
                self.memory.set("dig_permission", data.get("digPermission"))
            return {"category": category, "summary": summary, "data": data}

        self.logger.warning("unknown detection category encountered category=%s", category)
        return None

    def _summarize_block_evaluations(
        self, evaluations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Bridge の評価結果を LLM へ渡しやすいフラグに変換する。"""

        hazards: List[Dict[str, Any]] = []
        liquids: List[Dict[str, Any]] = []
        functional: List[Dict[str, Any]] = []
        for entry in evaluations or []:
            block_id = str(entry.get("block_id") or "").lower()
            pos = entry.get("pos") or entry.get("position") or {}
            marker = {"block": block_id or "unknown", "pos": pos}
            if entry.get("is_liquid"):
                liquids.append(marker)
            if entry.get("near_functional"):
                functional.append(marker)
            if any(keyword in block_id for keyword in self._HAZARD_BLOCK_KEYWORDS):
                hazards.append(marker)

        return {
            "has_liquid_nearby": bool(liquids),
            "has_functional_nearby": bool(functional),
            "has_hazard_nearby": bool(hazards),
            "hazard_samples": hazards[:5],
            "liquid_samples": liquids[:5],
            "functional_samples": functional[:5],
        }

    def _summarize_position_status(self, data: Dict[str, Any]) -> str:
        """Node 側から受け取った位置情報をプレイヤー向けの要約文へ整形する。"""

        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            position = data.get("position")
            if isinstance(position, dict):
                x = position.get("x")
                y = position.get("y")
                z = position.get("z")
                dimension = data.get("dimension") or "unknown"
                if all(isinstance(value, int) for value in (x, y, z)):
                    return f"現在位置は X={x} / Y={y} / Z={z}（ディメンション: {dimension}）です。"

        return "現在位置の最新情報を取得しました。"

    def _summarize_inventory_status(self, data: Dict[str, Any]) -> str:
        """インベントリ情報を主要要約へ変換する。"""

        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            items = data.get("items")
            if isinstance(items, list):
                item_count = len(items)
                pickaxes = data.get("pickaxes")
                pickaxe_count = len(pickaxes) if isinstance(pickaxes, list) else 0
                return f"所持品は {item_count} 種類を確認しました（ツルハシ {pickaxe_count} 本）。"

        return "所持品一覧を取得しました。"

    def _summarize_general_status(self, data: Dict[str, Any]) -> str:
        """体力・満腹度・掘削許可のステータスを読みやすい文章にまとめる。"""

        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            health = data.get("health")
            max_health = data.get("maxHealth")
            food = data.get("food")
            saturation = data.get("saturation")
            dig_permission = data.get("digPermission")
            if all(
                isinstance(value, (int, float))
                for value in (health, max_health, food, saturation)
            ) and isinstance(dig_permission, dict):
                allowed = dig_permission.get("allowed")
                reason = dig_permission.get("reason")
                permission_text = "あり" if allowed else f"なし（{reason}）"
                return (
                    f"体力: {int(health)}/{int(max_health)}、満腹度: {int(food)}/20、飽和度: {float(saturation):.1f}、"
                    f"採掘許可: {permission_text}。"
                )

        return "体力や採掘許可の現在値を確認しました。"

    def _classify_action_task(self, text: str) -> Optional[str]:
        """行動系タスクのカテゴリを判定し、保留リスト整理に利用する。"""

        segments = self._split_action_segments(text)
        best_category: Optional[str] = None
        best_score: Optional[Tuple[int, int, int, int]] = None

        for order_index, (category, rule) in enumerate(self._ACTION_TASK_RULES.items()):
            # 各カテゴリ候補を優先度→一致キーワード数→キーワード長→定義順で採点する。
            matched_keywords = set()
            longest_keyword = 0

            for segment in segments:
                matches = self._collect_keyword_matches(segment, rule.keywords)
                if not matches:
                    continue

                matched_keywords.update(matches)
                segment_longest = max(len(keyword) for keyword in matches)
                longest_keyword = max(longest_keyword, segment_longest)

            if not matched_keywords:
                continue

            score = (
                rule.priority,
                len(matched_keywords),
                longest_keyword,
                -order_index,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_category = category

        return best_category

    def _match_keywords(self, text: str, keywords: Tuple[str, ...]) -> bool:
        """任意のキーワードが文中に含まれるかを評価するヘルパー。"""

        return any(keyword and keyword in text for keyword in keywords)

    def _split_action_segments(self, text: str) -> Tuple[str, ...]:
        """句読点や改行を基準にアクション指示を分割し、個別判定に役立てる。"""

        separators = r"[、。,，,\n]+"
        parts = [segment.strip() for segment in re.split(separators, text) if segment.strip()]
        if not parts:
            return (text,)
        return tuple(parts)

    def _collect_keyword_matches(
        self, text: str, keywords: Tuple[str, ...]
    ) -> List[str]:
        """指定した文とキーワード群の一致候補を列挙し、重み付けに利用する。"""

        compact = text.replace(" ", "").replace("　", "")
        compact_lower = compact.lower()
        matches: List[str] = []
        for keyword in keywords:
            normalized_keyword = keyword.replace(" ", "").replace("　", "")
            if not normalized_keyword:
                continue
            if normalized_keyword in compact or normalized_keyword.lower() in compact_lower:
                matches.append(keyword)
        return matches

    async def _find_skill_for_step(
        self,
        category: str,
        step: str,
    ) -> Optional[SkillMatch]:
        """ステップ文から再利用可能なスキルを検索する。"""

        try:
            mission_id = self._active_minedojo_mission_id
            context_tags: List[str] = []
            if self._active_minedojo_demo_metadata:
                context_tags.extend(list(self._active_minedojo_demo_metadata.tags))
                context_tags.append("minedojo")
                context_tags.append(self._active_minedojo_demo_metadata.mission_id)
            if self._active_minedojo_mission:
                context_tags.extend(list(self._active_minedojo_mission.tags))
            normalized_tags = tuple(dict.fromkeys(tag for tag in context_tags if str(tag).strip()))

            return await self.skill_repository.match_skill(
                step,
                category=category,
                tags=normalized_tags,
                mission_id=mission_id,
            )
        except Exception:
            self.logger.exception("skill matching failed category=%s step='%s'", category, step)
            return None

    async def _execute_skill_match(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        """既存スキルを Mineflayer 側へ再生指示し、結果を戻す。"""

        if not hasattr(self.actions, "invoke_skill"):
            self.logger.info("Actions.invoke_skill is unavailable; falling back to legacy execution flow")
            return False, None

        resp = await self.actions.invoke_skill(match.skill.identifier, context=step)
        if resp.get("ok"):
            await self.skill_repository.record_usage(match.skill.identifier, success=True)
            self.memory.set(
                "last_skill_usage",
                {"skill_id": match.skill.identifier, "title": match.skill.title, "step": step},
            )
            return True, None

        await self.skill_repository.record_usage(match.skill.identifier, success=False)
        error_detail = resp.get("error")
        if isinstance(error_detail, str) and "is not registered" in error_detail:
            # Mineflayer 側でスキルが未登録の場合はヒューリスティックへ委譲する。
            # INFO ログのみ残して失敗理由を握りつぶすことで LangGraph が通常経路へ進み、
            # 既存の装備・採掘処理へスムーズにフォールバックできるようにする。
            self.logger.info(
                "skill %s missing on Mineflayer; defer to heuristics error=%s",
                match.skill.identifier,
                error_detail,
            )
            return False, None

        error_detail = error_detail or "Mineflayer 側でスキル再生が拒否されました"
        return False, f"スキル『{match.skill.title}』の再生に失敗しました: {error_detail}"

    async def _begin_skill_exploration(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        """未習得スキルのため探索モードへ切り替える。"""

        if not hasattr(self.actions, "begin_skill_exploration"):
            self.logger.info("Actions.begin_skill_exploration is unavailable; skipping exploration mode")
            return False, None

        resp = await self.actions.begin_skill_exploration(
            skill_id=match.skill.identifier,
            description=match.skill.description,
            step_context=step,
        )
        if resp.get("ok"):
            self.memory.set(
                "last_skill_exploration",
                {"skill_id": match.skill.identifier, "title": match.skill.title, "step": step},
            )
            return True, None

        error_detail = resp.get("error") or "探索モードへの切り替えが Mineflayer 側で拒否されました"
        return False, error_detail

    async def _attach_minedojo_context(self, category: str, step: str) -> None:
        """分類カテゴリに応じて MineDojo のミッション/デモを準備する。"""

        mission_id = self._MINEDOJO_MISSION_BINDINGS.get(category)
        if not mission_id:
            return

        if not self.minedojo_client:
            self.logger.info(
                "MineDojo client is unavailable; skip context binding category=%s step='%s'",
                category,
                step,
            )
            return

        if mission_id == self._active_minedojo_mission_id and self._active_minedojo_demos:
            return

        try:
            mission = await self.minedojo_client.fetch_mission(mission_id)
            demos = await self.minedojo_client.fetch_demonstrations(mission_id, limit=1)
        except Exception:
            self.logger.exception("failed to fetch MineDojo resources mission=%s", mission_id)
            return

        self._active_minedojo_mission = mission
        self._active_minedojo_demos = demos
        self._active_minedojo_mission_id = mission_id if (mission or demos) else None
        metadata_list: List[MineDojoDemoMetadata] = []
        if demos:
            mission_tags = mission.tags if mission else ()
            metadata_list = [demo.to_metadata(mission_tags=mission_tags) for demo in demos]
            self._active_minedojo_demo_metadata = metadata_list[0]
        else:
            self._active_minedojo_demo_metadata = None

        context_payload = self._build_minedojo_context_payload(mission, demos, metadata_list)
        if context_payload:
            self.memory.set("minedojo_context", context_payload)

        if demos and self._active_minedojo_demo_metadata:
            await self._prime_actions_with_demo(demos[0], self._active_minedojo_demo_metadata)
            await self._register_minedojo_demo_skill(
                mission,
                self._active_minedojo_demo_metadata,
                demos[0],
            )

    def _build_minedojo_context_payload(
        self,
        mission: Optional[MineDojoMission],
        demos: List[MineDojoDemonstration],
        metadata_list: List[MineDojoDemoMetadata],
    ) -> Optional[Dict[str, Any]]:
        """LLM プロンプトへ差し込む MineDojo 情報を整形する。"""

        if not mission and not demos:
            return None

        payload: Dict[str, Any] = {}
        if mission:
            payload["mission"] = mission.to_prompt_payload()
        if demos:
            payload["demonstrations"] = [
                self._format_minedojo_demo_for_context(demo, metadata_list[index])
                for index, demo in enumerate(demos)
                if demo and index < len(metadata_list)
            ]
        return payload

    def _format_minedojo_demo_for_context(
        self, demo: MineDojoDemonstration, metadata: MineDojoDemoMetadata
    ) -> Dict[str, Any]:
        """デモを LLM 用に要約し、過剰なデータ転送を避ける。"""

        action_types: List[str] = []
        for action in list(demo.actions)[:3]:
            if isinstance(action, dict):
                label = str(action.get("type") or action.get("name") or "unknown")
                action_types.append(label)
        return {
            "demo_id": demo.demo_id,
            "summary": demo.summary,
            "mission_id": metadata.mission_id,
            "tags": list(metadata.tags),
            "action_types": action_types,
            "action_count": len(demo.actions),
        }

    async def _prime_actions_with_demo(
        self, demo: MineDojoDemonstration, metadata: MineDojoDemoMetadata
    ) -> None:
        """取得したデモを Actions へ送信し、Mineflayer 側で事前ロードする。"""

        if not hasattr(self.actions, "play_vpt_actions"):
            return

        if not demo.actions:
            return

        actions_payload = [dict(item) for item in demo.actions if isinstance(item, dict)]
        if not actions_payload:
            return

        metadata_dict = metadata.to_dict()
        try:
            resp = await self.actions.play_vpt_actions(actions_payload, metadata=metadata_dict)
        except Exception:
            self.logger.exception(
                "MineDojo demo preload failed mission=%s demo=%s",
                metadata.mission_id,
                demo.demo_id,
            )
            return

        if resp.get("ok"):
            self.memory.set(
                "minedojo_last_demo_metadata",
                {"mission_id": metadata.mission_id, "demo_id": demo.demo_id, "metadata": metadata_dict},
            )
        else:
            self.logger.warning(
                "MineDojo demo preload command failed mission=%s demo=%s resp=%s",
                metadata.mission_id,
                demo.demo_id,
                resp,
            )

    async def _register_minedojo_demo_skill(
        self,
        mission: Optional[MineDojoMission],
        metadata: MineDojoDemoMetadata,
        demo: MineDojoDemonstration,
    ) -> None:
        """MineDojo デモをスキルライブラリへ登録し、Mineflayer 側にも伝搬する。"""

        # ミッション単位でスキル ID を固定し、NDJSON ログと照合しやすいタグを束ねる。
        skill_id = f"minedojo::{metadata.mission_id}::{metadata.demo_id}"
        tree = await self.skill_repository.get_tree()
        already_exists = skill_id in tree.nodes

        tags: List[str] = [
            "minedojo",
            metadata.mission_id,
            f"mission:{metadata.mission_id}",
            *list(metadata.tags),
        ]
        if mission:
            tags.extend(list(mission.tags))
        normalized_tags = tuple(dict.fromkeys(tag for tag in tags if str(tag).strip()))

        description_parts: List[str] = []
        if mission:
            description_parts.append(mission.objective)
        description_parts.append(f"demo={metadata.summary}")

        keywords: List[str] = []
        if mission:
            keywords.extend([mission.title, mission.objective])
        keywords.append(metadata.summary)

        node = SkillNode(
            identifier=skill_id,
            title=mission.title if mission else f"MineDojo {metadata.mission_id}",
            description=" / ".join(part for part in description_parts if part) or metadata.summary,
            categories=tuple(mission.tags) if mission else (),
            tags=normalized_tags,
            keywords=tuple(keyword for keyword in keywords if keyword),
            examples=(metadata.summary,),
        )
        await self.skill_repository.register_skill(node)

        if not hasattr(self.actions, "register_skill"):
            return
        if already_exists:
            # Mineflayer 側に重複登録してもログが汚れるだけなので回避する。
            return

        try:
            await self.actions.register_skill(  # type: ignore[attr-defined]
                skill_id=skill_id,
                title=node.title,
                description=node.description,
                steps=[demo.summary or metadata.summary],
                tags=list(normalized_tags),
            )
        except Exception:
            self.logger.warning("register_skill dispatch failed for %s", skill_id)

    async def _handle_action_task(
        self,
        category: str,
        step: str,
        *,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        explicit_coords: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        """行動タスクを処理し、失敗時は理由を添えて返す。"""

        rule = self._ACTION_TASK_RULES.get(category)
        if not rule:
            backlog.append({"category": category, "step": step, "label": category})
            self.logger.warning(
                "action category=%s missing rule so queued to backlog step='%s'",
                category,
                step,
            )
            return False, last_target_coords, None

        structured_event_history, perception_history = self._collect_recent_mineflayer_context()
        self.logger.info(
            "delegating action category=%s step='%s' to langgraph module=%s",
            category,
            step,
            rule.label or category,
        )
        await self._attach_minedojo_context(category, step)
        handled, updated_target, failure_detail = await self._action_graph.run(
            category=category,
            step=step,
            last_target_coords=last_target_coords,
            backlog=backlog,
            rule=rule,
            explicit_coords=explicit_coords,
            structured_event_history=structured_event_history,
            perception_history=perception_history,
        )
        return handled, updated_target, failure_detail

    def _select_pickaxe_for_targets(
        self, ore_names: Iterable[str]
    ) -> Optional[Dict[str, Any]]:
        """要求鉱石に適したツルハシが記憶済みインベントリにあるかを調べる。"""

        inventory_detail = self.memory.get("inventory_detail")
        if not isinstance(inventory_detail, dict):
            return None

        pickaxes = inventory_detail.get("pickaxes")
        if not isinstance(pickaxes, list):
            return None

        # 対象鉱石の中でもっとも高い要求ランクを算出する。
        required_tier = 1
        for ore in ore_names:
            tier = self._ORE_PICKAXE_REQUIREMENTS.get(ore, 1)
            required_tier = max(required_tier, tier)

        best_candidate: Optional[Dict[str, Any]] = None
        best_tier = 0
        for item in pickaxes:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            if not isinstance(name, str):
                continue

            tier = self._PICKAXE_TIER_BY_NAME.get(name)
            if tier is None or tier < required_tier:
                continue

            if not self._has_sufficient_pickaxe_durability(item):
                continue

            if tier > best_tier:
                best_candidate = item
                best_tier = tier

        return best_candidate

    def _has_sufficient_pickaxe_durability(self, item: Dict[str, Any]) -> bool:
        """ツルハシの耐久値が残っているかを柔軟に判断する。"""

        remaining = self._extract_pickaxe_remaining_durability(item)
        if remaining is None:
            # Mineflayer から耐久値が渡されないケースでは残量不明だが、
            # 所持している限り利用可能と判断する。
            return True

        return remaining > 0

    def _extract_pickaxe_remaining_durability(
        self, item: Dict[str, Any]
    ) -> Optional[float]:
        """所持ツルハシ情報から残耐久を推定して数値として返す。"""

        # Node 側で直接算出された耐久値があれば最優先で利用し、
        # 欠損時のみ古いキーへフォールバックする。
        direct_value = item.get("durability")
        if isinstance(direct_value, (int, float)):
            return float(direct_value)

        max_durability = item.get("maxDurability")
        durability_used = item.get("durabilityUsed")
        if isinstance(max_durability, (int, float)) and isinstance(
            durability_used, (int, float)
        ):
            return float(max_durability) - float(durability_used)

        for key in ("durabilityRemaining", "remainingDurability"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)

        for key in ("durabilityRatio", "durability_ratio"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)

        for key in ("durabilityPercent", "durability_percent"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value) / 100.0

        return None

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
        reports: Iterable[Dict[str, Any]],
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

        # 未返信の場合は取得した内容そのものを共有し、プレイヤーが追加指示を出しやすくする。
        segments: List[str] = []
        for item in report_list:
            summary_text = str(item.get("summary") or "").strip()
            if summary_text:
                segments.append(summary_text.rstrip("。"))

        if not segments:
            labels = []
            for item in report_list:
                category = item.get("category", "")
                label = self._DETECTION_LABELS.get(category)
                if label and label not in labels:
                    labels.append(label)
            if not labels:
                labels.append("状況確認")
            message = f"{'、'.join(labels)}の確認結果を取得しました。"
        else:
            message = "。".join(segments) + "。"

        await self.actions.say(message)

    async def _handle_bridge_event(self, payload: Dict[str, Any]) -> None:
        """Bridge 側の SSE イベントを検出レポートとして整形する。"""

        if not isinstance(payload, dict):
            return

        event_level = str(payload.get("event_level") or "info")
        message = str(payload.get("message") or payload.get("type") or "event")
        region = str(payload.get("region") or "").strip()
        coords_text = self._format_block_pos(payload.get("block_pos"))

        summary_parts = [f"[{event_level}] {message}"]
        if region:
            summary_parts.append(f"region={region}")
        if coords_text:
            summary_parts.append(f"pos={coords_text}")
        summary = " / ".join(summary_parts)

        report: Dict[str, Any] = {
            "summary": summary,
            "category": str(payload.get("type") or "bridge_event"),
            "event_level": event_level,
        }
        if region:
            report["region"] = region
        if isinstance(payload.get("block_pos"), dict):
            report["block_pos"] = payload["block_pos"]

        history = self.memory.get("bridge_event_reports", [])
        if not isinstance(history, list):
            history = []
        history.append(report)
        self.memory.set("bridge_event_reports", history[-10:])

        log_structured_event(
            self.logger,
            "bridge event received",
            level=logging.INFO,
            event_level=event_level,
            langgraph_node_id="agent.bridge_events",
            context={
                "region": region or "unknown",
                "summary": summary,
            },
        )

    def _format_block_pos(self, block_pos: Any) -> str:
        """Block 座標辞書を人間可読な文字列に整形する。"""

        if isinstance(block_pos, dict):
            try:
                x = int(block_pos.get("x"))
                y = int(block_pos.get("y"))
                z = int(block_pos.get("z"))
                return f"X={x} Y={y} Z={z}"
            except Exception:
                return ""
        return ""

    def _augment_failure_reason_with_events(
        self, failure_reason: str, reports: Sequence[Dict[str, Any]]
    ) -> str:
        """最新の保護領域イベント情報を失敗理由へ添える。"""

        if not reports:
            return failure_reason

        latest = reports[-1]
        region = str(latest.get("region") or "").strip()
        coords = self._format_block_pos(latest.get("block_pos"))
        segments: List[str] = []
        if region:
            segments.append(f"保護領域: {region}")
        if coords:
            segments.append(f"座標: {coords}")
        if not segments:
            return failure_reason

        return f"{failure_reason} (最近の検知: {' / '.join(segments)})"

    def _extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        """ステップ文字列から XYZ 座標らしき数値を抽出する。"""

        for pattern in self._COORD_PATTERNS:
            match = pattern.search(text)
            if match:
                x, y, z = (int(match.group(i)) for i in range(1, 4))
                return x, y, z
        return None

    def _extract_argument_coordinates(
        self, arguments: PlanArguments | Dict[str, Any] | None
    ) -> Optional[Tuple[int, int, int]]:
        """PlanOut.arguments から座標だけを安全に取り出す。"""

        raw = None
        if isinstance(arguments, PlanArguments):
            raw = arguments.coordinates
        elif isinstance(arguments, dict):
            raw = arguments.get("coordinates")

        if isinstance(raw, dict):
            try:
                return (int(raw.get("x")), int(raw.get("y")), int(raw.get("z")))
            except Exception:
                return None
        return None

    async def _move_to_coordinates(
        self, coords: Iterable[int]
    ) -> Tuple[bool, Optional[str]]:
        """Mineflayer の移動アクションを発行し、結果をログへ残すユーティリティ。"""

        x, y, z = coords
        self.logger.info("requesting moveTo to (%d, %d, %d)", x, y, z)
        resp = await self.actions.move_to(x, y, z)
        self.logger.info("moveTo response=%s", resp)
        if resp.get("ok"):
            self.memory.set("last_destination", {"x": x, "y": y, "z": z})
            return True, None

        # ここまで来た場合は Mineflayer からエラー応答が返却されたことを意味する。
        self.logger.error("moveTo command rejected resp=%s", resp)
        error_detail = resp.get("error") or "Mineflayer 側の理由不明な拒否"
        return False, error_detail

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
        except BarrierNotificationTimeout as exc:
            self.logger.warning(
                "barrier message generation timed out step='%s': %s",
                step,
                exc,
            )
        except BarrierNotificationError as exc:
            self.logger.warning(
                "barrier message generation failed step='%s': %s",
                step,
                exc,
            )
        except Exception:
            self.logger.exception("failed to compose barrier message via LLM")

        # LLM 連携が利用できない場合は、プレイヤーが状況を素早く把握できるよう
        # 既存の短縮メッセージロジックで即時応答を組み立てる。
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

        payload_type = payload.get("type")
        if payload_type == "chat":
            args = payload.get("args") or {}
            username = str(args.get("username", "")).strip() or "Player"
            message = str(args.get("message", "")).strip()

            if not message:
                self.logger.warning("empty chat message received username=%s", username)
                return {"ok": False, "error": "empty message"}

            await self.orchestrator.enqueue_chat(username, message)
            return {"ok": True}

        if payload_type == "agentEvent":
            args = payload.get("args") or {}
            await self.orchestrator.handle_agent_event(args)
            return {"ok": True}

        self.logger.error("unsupported payload type=%s", payload_type)
        return {"ok": False, "error": "unsupported type"}


async def run_minedojo_self_dialogue(
    mission_id: str,
    react_trace: Sequence[ReActStep],
    *,
    skill_id: str,
    title: str,
    success: bool = True,
) -> None:
    """MineDojo 環境向け自己対話を単体実行する簡易エントリポイント。"""

    bridge = BotBridge(WS_URL)
    actions = Actions(bridge)
    seed_path = Path(__file__).resolve().parent / "skills" / "seed_library.json"
    skill_repo = SkillRepository(
        SKILL_LIBRARY_PATH,
        seed_path=str(seed_path),
    )
    minedojo_client = MineDojoClient(AGENT_CONFIG.minedojo)
    tracer = ThoughtActionObservationTracer(
        api_url=AGENT_CONFIG.langsmith.api_url,
        api_key=AGENT_CONFIG.langsmith.api_key,
        project=AGENT_CONFIG.langsmith.project,
        default_tags=AGENT_CONFIG.langsmith.tags,
        enabled=AGENT_CONFIG.langsmith.enabled,
    )
    executor = MineDojoSelfDialogueExecutor(
        actions=actions,
        client=minedojo_client,
        skill_repository=skill_repo,
        tracer=tracer,
        env_params={
            "sim_env": AGENT_CONFIG.minedojo.sim_env,
            "sim_seed": AGENT_CONFIG.minedojo.sim_seed,
            "sim_max_steps": AGENT_CONFIG.minedojo.sim_max_steps,
        },
    )
    await executor.run_self_dialogue(
        mission_id,
        react_trace,
        skill_id=skill_id,
        title=title,
        success=success,
    )
    await minedojo_client.aclose()


async def main() -> None:
    """エージェントを起動し、WebSocket サーバーとワーカーを開始する。"""

    bridge = BotBridge(WS_URL)
    actions = Actions(bridge)
    mem = Memory()
    # 既定のスキル定義を JSON から読み込み、学習済みスキルとの差分を蓄積できるようにする。
    seed_path = Path(__file__).resolve().parent / "skills" / "seed_library.json"
    skill_repo = SkillRepository(
        SKILL_LIBRARY_PATH,
        seed_path=str(seed_path),
    )
    orchestrator = AgentOrchestrator(actions, mem, skill_repository=skill_repo)
    ws_server = AgentWebSocketServer(orchestrator)
    await orchestrator.start_bridge_event_listener()

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
            await orchestrator.stop_bridge_event_listener()


if __name__ == "__main__":
    asyncio.run(main())
