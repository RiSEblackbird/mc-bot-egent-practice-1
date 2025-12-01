# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent_bootstrap import build_agent_dependencies
from chat_pipeline import ChatPipeline
from bridge_role_handler import BridgeRoleHandler
from perception_service import PerceptionCoordinator
from agent_settings import (
    AgentRuntimeSettings,
    DEFAULT_AGENT_RUNTIME_SETTINGS,
)
from config import AgentConfig
from services.minedojo_client import MineDojoClient
from services.skill_repository import SkillRepository
from actions import Actions
from memory import Memory
from planner import (
    ActionDirective,
    PlanArguments,
    PlanOut,
    ReActStep,
    plan,
)
from skills import SkillMatch, SkillNode
from utils import log_structured_event, setup_logger
from runtime.action_graph import ActionTaskRule, ChatTask
from runtime.inventory_sync import InventorySynchronizer
from runtime.reflection_prompt import build_reflection_prompt
from runtime.hybrid_directive import HybridDirectivePayload
from runtime.rules import (
    ACTION_TASK_RULES,
    COORD_PATTERNS,
    DETECTION_TASK_KEYWORDS,
    EQUIP_KEYWORD_RULES,
)

logger = setup_logger("agent")

# --- 設定の読み込み --------------------------------------------------------

RUNTIME_SETTINGS = DEFAULT_AGENT_RUNTIME_SETTINGS
AGENT_CONFIG: AgentConfig = RUNTIME_SETTINGS.config


class AgentOrchestrator:
    """受信チャットを順次処理し、LLM プラン→Mineflayer 操作を遂行する中核クラス。"""

    # 再計画の連鎖が無限に進むとチャット出力が雪崩のように発生するため、
    # 自動リトライは上限回数で打ち切り、最悪時でも運用者が介入しやすくする。
    _MAX_REPLAN_DEPTH = 2
    # チャット処理がタイムアウトした際の再試行上限。無制限リトライでキューを
    # 埋め続けると新規指示を受け付けられなくなるため、再計画と同じ深さで早期開放する。
    _MAX_TASK_TIMEOUT_RETRY = _MAX_REPLAN_DEPTH

    # 座標抽出パターンは runtime.rules.COORD_PATTERNS で一元管理する。
    # 行動カテゴリのルールセットは runtime.rules.ACTION_TASK_RULES として共有する。
    # 検出系タスクのキーワード分類は runtime.rules.DETECTION_TASK_KEYWORDS を参照する。
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
    # 装備ステップのキーワード解析ルールは runtime.rules.EQUIP_KEYWORD_RULES を利用する。
    # 採掘に必要なツルハシランクの対応表は runtime.rules へ切り出して共有する。

    def __init__(
        self,
        actions: Actions,
        memory: Memory,
        *,
        skill_repository: SkillRepository | None = None,
        config: AgentConfig | None = None,
        runtime_settings: AgentRuntimeSettings | None = None,
        minedojo_client: MineDojoClient | None = None,
        inventory_sync: InventorySynchronizer | None = None,
    ) -> None:
        self.actions = actions
        self.memory = memory
        self.settings = runtime_settings or RUNTIME_SETTINGS
        self.config = config or self.settings.config
        # 設定値をローカル変数へコピーしておくことで、テスト時に差し込まれた構成も尊重する。
        self.default_move_target = self.config.default_move_target
        self.logger = setup_logger("agent.orchestrator")
        # LangGraph 側での意思決定に活用する閾値や履歴上限をまとめて保持する。
        self.low_food_threshold = self.settings.low_food_threshold
        self.structured_event_history_limit = self.settings.structured_event_history_limit
        self.perception_history_limit = self.settings.perception_history_limit
        dependencies = build_agent_dependencies(
            owner=self,
            actions=self.actions,
            memory=self.memory,
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            skill_repository=skill_repository,
            inventory_sync=inventory_sync,
            minedojo_client=minedojo_client,
        )
        self.skill_repository = dependencies.skill_repository
        self._tracer = dependencies.tracer
        self.inventory_sync = dependencies.inventory_sync
        self.status_service = dependencies.status_service
        self._action_graph = dependencies.action_graph
        self.chat_queue = dependencies.chat_queue
        self.minedojo_client = dependencies.minedojo_client
        self.minedojo_handler = dependencies.minedojo_handler
        self._hybrid_handler = dependencies.hybrid_handler
        self._chat_pipeline = ChatPipeline(self)
        self._bridge_roles = BridgeRoleHandler(self)
        self._perception = PerceptionCoordinator(self)

    async def enqueue_chat(self, username: str, message: str) -> None:
        """WebSocket から受け取ったチャットをワーカーに積むラッパー。"""

        await self.chat_queue.enqueue_chat(username, message)

    async def _safe_say(self, message: str) -> None:
        """Actions.say が未提供でも安全に呼び出せるようラップする。"""

        if hasattr(self.actions, "say"):
            await self.actions.say(message)
            return

        # スタブ環境でも計画生成を継続できるよう、ログだけ残して処理を進める。
        self.logger.info(
            "skip chat dispatch because Actions.say is unavailable message=%s", message
        )

    async def worker(self) -> None:
        """チャットキューを逐次処理するバックグラウンドタスクの委譲。"""

        await self.chat_queue.worker()

    async def start_bridge_event_listener(self) -> None:
        """AgentBridge のイベントストリーム購読タスクを起動する。"""

        await self._bridge_roles.start_listener()

    async def stop_bridge_event_listener(self) -> None:
        """イベント購読タスクを停止し、スレッドセーフに後始末する。"""

        await self._bridge_roles.stop_listener()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        """Node 側から届いたマルチエージェントイベントを解析して記憶する。"""

        await self._bridge_roles.handle_agent_event(args)

    def request_role_switch(self, role_id: str, *, reason: Optional[str] = None) -> None:
        """LangGraph ノードからの役割切替要求をキューへ記録する。"""

        self._bridge_roles.request_role_switch(role_id, reason=reason)

    def _consume_pending_role_switch(self) -> Optional[Tuple[str, Optional[str]]]:
        return self._bridge_roles.consume_pending_role_switch()

    @property
    def current_role(self) -> str:
        return self._bridge_roles.current_role

    async def _apply_role_switch(self, role_id: str, reason: Optional[str]) -> bool:
        """実際に Node 側へ役割変更コマンドを送信し、成功時は記憶を更新する。"""

        return await self._bridge_roles.apply_role_switch(role_id, reason)

    def _collect_recent_mineflayer_context(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Mineflayer 由来の履歴を LangGraph へ渡すためにまとめて取得する。"""

        return self._perception.collect_recent_mineflayer_context()

    def _build_perception_snapshot(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """ステータスサービス経由で perception スナップショットを生成する互換ラッパー。"""

        return self._perception.build_perception_snapshot(extra)

    def _ingest_perception_snapshot(self, snapshot: Dict[str, Any], *, source: str) -> None:
        """従来のエントリポイントを保ちながら perception ingestion をサービスへ委譲する。"""

        self._perception.ingest_perception_snapshot(snapshot, source=source)

    async def _collect_block_evaluations(self) -> None:
        """Bridge から近傍ブロックの情報を収集し、危険度の概略をメモリへ保持する。"""

        await self._perception.collect_block_evaluations()

    async def _process_chat(self, task: ChatTask) -> None:
        """単一のチャット指示に対して LLM 計画とアクション実行を行う。"""

        await self._chat_pipeline.run_chat_task(task)

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

    def _parse_hybrid_directive_args(
        self,
        directive: ActionDirective,
    ) -> HybridDirectivePayload:
        return self._hybrid_handler.parse_arguments(directive)

    async def _execute_hybrid_directive(
        self,
        directive: ActionDirective,
        payload: HybridDirectivePayload,
        *,
        directive_meta: Optional[Dict[str, Any]],
        react_entry: Optional[ReActStep],
        thought_text: str,
        index: int,
        total_steps: int,
    ) -> bool:
        return await self._hybrid_handler.execute(
            directive,
            payload,
            directive_meta=directive_meta,
            react_entry=react_entry,
            thought_text=thought_text,
            index=index,
            total_steps=total_steps,
        )

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
            target_category = directive.category if isinstance(directive, ActionDirective) else ""

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
                handled = await self.minedojo_handler.handle_directive(
                    directive, plan_out, index
                )
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

            if directive and directive_executor == "hybrid":
                try:
                    hybrid_payload = self._parse_hybrid_directive_args(directive)
                except ValueError as exc:
                    await self._report_execution_barrier(
                        directive.label or directive.step or "hybrid",
                        f"ハイブリッド指示の解析に失敗しました: {exc}",
                    )
                    continue
                handled = await self._execute_hybrid_directive(
                    directive,
                    hybrid_payload,
                    directive_meta=directive_meta,
                    react_entry=react_entry,
                    thought_text=thought_text,
                    index=index,
                    total_steps=total_steps,
                )
                if handled:
                    continue

            detection_category = None
            if directive and directive.category in DETECTION_TASK_KEYWORDS:
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
                action_category = target_category or "move"
                self.logger.info(
                    "plan_step index=%d classified as %s coords=%s",
                    index,
                    action_category,
                    coords,
                )
                with self._directive_scope(directive_meta):
                    handled, last_target_coords, failure_detail = await self._handle_action_task(
                        action_category,
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
            if directive and directive.category in ACTION_TASK_RULES:
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
            failure_reason = self._bridge_roles.augment_failure_reason_with_events(
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

        context = self.status_service.build_context_snapshot(
            current_role_id=self._bridge_roles.current_role
        )
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

        rule = ACTION_TASK_RULES.get("move")
        return bool(rule and self._match_keywords(text, rule.keywords))

    def _should_continue_move(self, text: str) -> bool:
        """段差調整など移動継続で吸収できるステップかどうかを推測する。"""

        rule = ACTION_TASK_RULES.get("move")
        return bool(rule and self._match_keywords(text, rule.hints))

    def _classify_detection_task(self, text: str) -> Optional[str]:
        """検出報告タスク（位置・所持品などの確認系ステップ）を分類する。"""

        normalized = text.replace(" ", "").replace("　", "")
        for category, keywords in DETECTION_TASK_KEYWORDS.items():
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

        for keywords, mapping in EQUIP_KEYWORD_RULES:
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
            summary = self.status_service.summarize_position_status(data)
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
            summary = self.inventory_sync.summarize(data)
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
            summary = self.status_service.summarize_general_status(data)
            self.memory.set("general_status", summary)
            self.memory.set("general_status_detail", data)
            if isinstance(data, dict) and "digPermission" in data:
                self.memory.set("dig_permission", data.get("digPermission"))
            return {"category": category, "summary": summary, "data": data}

        self.logger.warning("unknown detection category encountered category=%s", category)
        return None

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

        for order_index, (category, rule) in enumerate(ACTION_TASK_RULES.items()):
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
        """MineDojo 文脈を含めたスキル探索をハンドラーへ委譲する。"""

        return await self.minedojo_handler.find_skill_for_step(category, step)

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

        return await self._chat_pipeline.handle_action_task(
            category,
            step,
            last_target_coords=last_target_coords,
            backlog=backlog,
            explicit_coords=explicit_coords,
        )

    def _select_pickaxe_for_targets(
        self, ore_names: Iterable[str]
    ) -> Optional[Dict[str, Any]]:
        """要求鉱石に適したツルハシが記憶済みインベントリにあるかを調べる。"""

        return self._chat_pipeline.select_pickaxe_for_targets(ore_names)

    async def _handle_action_backlog(
        self,
        backlog: Iterable[Dict[str, str]],
        *,
        already_responded: bool,
    ) -> None:
        """未実装アクションの backlog をメモリとチャットへ整理する。"""

        await self._chat_pipeline.handle_action_backlog(
            backlog,
            already_responded=already_responded,
        )

    async def _handle_detection_reports(
        self,
        reports: Iterable[Dict[str, Any]],
        *,
        already_responded: bool,
    ) -> None:
        """検出報告タスクをメモリへ整理し、必要に応じて丁寧な補足メッセージを送る。"""

        await self._chat_pipeline.handle_detection_reports(
            reports,
            already_responded=already_responded,
        )

    def _extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        """ステップ文字列から XYZ 座標らしき数値を抽出する。"""

        for pattern in COORD_PATTERNS:
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

        await self._perception.report_execution_barrier(step, reason)


