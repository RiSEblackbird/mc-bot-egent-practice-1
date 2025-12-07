# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from chat_pipeline import ChatPipeline
from orchestrator.action_analyzer import ActionAnalyzer
from orchestrator.directive_utils import (
    build_directive_meta,
    coerce_coordinate_tuple,
    directive_scope,
    execute_hybrid_directive,
    extract_directive_coordinates,
    parse_hybrid_directive_args,
    resolve_directive_for_step,
)
from orchestrator.skill_detection import SkillDetectionCoordinator
from orchestrator.task_router import TaskRouter
from orchestrator.plan_executor import PlanExecutor
from orchestrator.role_perception_adapter import RolePerceptionAdapter
from actions import Actions
from memory import Memory
from planner import (
    ActionDirective,
    PlanArguments,
    PlanOut,
    ReActStep,
    plan,
)
from utils import log_structured_event, setup_logger
from runtime.action_graph import ChatTask
from runtime.reflection_prompt import build_reflection_prompt
from runtime.hybrid_directive import HybridDirectivePayload
from services.movement_service import MovementService
from runtime.inventory_sync import InventorySynchronizer

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from agent_lifecycle import AgentOrchestratorWiring
logger = setup_logger("agent")


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

    def __init__(self, wiring: "AgentOrchestratorWiring") -> None:
        """受け取った依存を保持するだけのシンプルなコンストラクタ。"""

        self.actions = wiring.actions
        self.memory = wiring.memory
        self.settings = wiring.settings
        self.config = wiring.config
        # 設定値をローカル変数へコピーしておくことで、テスト時に差し込まれた構成も尊重する。
        self.default_move_target = wiring.default_move_target
        self.logger = wiring.logger
        # LangGraph 側での意思決定に活用する閾値や履歴上限をまとめて保持する。
        self.low_food_threshold = self.settings.low_food_threshold
        self.structured_event_history_limit = self.settings.structured_event_history_limit
        self.perception_history_limit = self.settings.perception_history_limit
        dependencies = wiring.dependencies
        self.skill_repository = dependencies.skill_repository
        self._tracer = dependencies.tracer
        self.inventory_sync = dependencies.inventory_sync
        self.status_service = dependencies.status_service
        self._action_graph = dependencies.action_graph
        self.chat_queue = dependencies.chat_queue
        self.minedojo_client = dependencies.minedojo_client
        self.minedojo_handler = dependencies.minedojo_handler
        self._hybrid_handler = dependencies.hybrid_handler
        self._chat_pipeline = wiring.chat_pipeline
        self._role_perception = wiring.role_perception
        # 役割イベント系の副作用はプロキシにまとめ、キュー処理ロジックと明確に分離する。
        self._role_listener = wiring.role_listener
        self._bridge_roles = self._role_perception.bridge_roles
        self._perception = self._role_perception.perception
        self.movement_service = wiring.movement_service
        self._plan_runtime = wiring.plan_runtime
        self._action_analyzer = wiring.action_analyzer
        self._skill_detection = wiring.skill_detection
        self.task_router = wiring.task_router
        self._dependencies = wiring.orchestrator_dependencies
        self._plan_executor = wiring.plan_executor
        self._validate_dependencies()

    def _validate_dependencies(self) -> None:
        """必須依存が欠落していないかを初期化時に検証する。"""

        missing = []
        if getattr(self, "_chat_pipeline", None) is None:
            missing.append("chat_pipeline")
        if getattr(self, "_action_analyzer", None) is None:
            missing.append("action_analyzer")
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"AgentOrchestrator is missing required dependencies: {joined}"
            )

    def _get_chat_pipeline(self) -> ChatPipeline:
        """チャット処理パイプラインを安全に取得する。"""

        pipeline = getattr(self, "_chat_pipeline", None)
        if pipeline is None:
            raise RuntimeError(
                "chat pipeline is not initialized; check AgentOrchestrator wiring"
            )
        return pipeline

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

        await self._role_listener.start_bridge_event_listener()

    async def stop_bridge_event_listener(self) -> None:
        """イベント購読タスクを停止し、スレッドセーフに後始末する。"""

        await self._role_listener.stop_bridge_event_listener()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        """Node 側から届いたマルチエージェントイベントを解析して記憶する。"""

        await self._role_listener.handle_agent_event(args)

    def request_role_switch(self, role_id: str, *, reason: Optional[str] = None) -> None:
        """LangGraph ノードからの役割切替要求をキューへ記録する。"""

        self._role_perception.request_role_switch(role_id, reason=reason)

    def _consume_pending_role_switch(self) -> Optional[Tuple[str, Optional[str]]]:
        return self._role_perception.consume_pending_role_switch()

    @property
    def current_role(self) -> str:
        return self._role_perception.current_role

    @property
    def role_perception(self) -> RolePerceptionAdapter:
        """役割・perception 系の副作用を一括管理するアダプタへの参照。"""

        return self._role_perception

    async def _apply_role_switch(self, role_id: str, reason: Optional[str]) -> bool:
        """実際に Node 側へ役割変更コマンドを送信し、成功時は記憶を更新する。"""

        return await self._role_perception.apply_role_switch(role_id, reason)

    def _collect_recent_mineflayer_context(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Mineflayer 由来の履歴を LangGraph へ渡すためにまとめて取得する。"""

        return self._role_perception.collect_recent_mineflayer_context()

    def _build_perception_snapshot(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """ステータスサービス経由で perception スナップショットを生成する互換ラッパー。"""

        return self._role_perception.build_perception_snapshot(extra)

    def _ingest_perception_snapshot(self, snapshot: Dict[str, Any], *, source: str) -> None:
        """従来のエントリポイントを保ちながら perception ingestion をサービスへ委譲する。"""

        self._role_perception.ingest_perception_snapshot(snapshot, source=source)

    async def _collect_block_evaluations(self) -> None:
        """Bridge から近傍ブロックの情報を収集し、危険度の概略をメモリへ保持する。"""

        await self._role_perception.collect_block_evaluations()

    async def _process_chat(self, task: ChatTask) -> None:
        """単一のチャット指示に対して LLM 計画とアクション実行を行う。"""

        pipeline = self._get_chat_pipeline()
        await pipeline.run_chat_task(task)

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
        """directive_utils へ委譲し、PlanExecutor と実装を共有する。"""

        return resolve_directive_for_step(
            directives,
            index,
            fallback_step,
            logger=self.logger,
        )

    def _build_directive_meta(
        self,
        directive: Optional[ActionDirective],
        plan_out: PlanOut,
        index: int,
        total_steps: int,
    ) -> Optional[Dict[str, Any]]:
        """directive メタ構築をユーティリティ経由で統一する。"""

        return build_directive_meta(directive, plan_out, index, total_steps)

    def _extract_directive_coordinates(
        self,
        directive: Optional[ActionDirective],
    ) -> Optional[Tuple[int, int, int]]:
        """指示の座標抽出ロジックを共通ユーティリティに委譲する。"""

        return extract_directive_coordinates(directive)

    def _parse_hybrid_directive_args(
        self,
        directive: ActionDirective,
    ) -> HybridDirectivePayload:
        """Hybrid 指示引数のパースをハンドラーへ明示的に委譲する。"""

        return parse_hybrid_directive_args(self._hybrid_handler, directive)

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
        """Hybrid 指示の実行をユーティリティ経由で一元化する。"""

        return await execute_hybrid_directive(
            self._hybrid_handler,
            directive,
            payload,
            directive_meta=directive_meta,
            react_entry=react_entry,
            thought_text=thought_text,
            index=index,
            total_steps=total_steps,
        )

    def _coerce_coordinate_tuple(self, payload: Any) -> Optional[Tuple[int, int, int]]:
        """座標辞書の安全な整数変換を共通ロジックへ委譲する。"""

        return coerce_coordinate_tuple(payload)

    def _directive_scope(self, meta: Optional[Dict[str, Any]]):
        """directive_utils のスコープコンテキストを経由して安全に委譲する。"""

        return directive_scope(self.actions, meta)

    async def _execute_plan(
        self,
        plan_out: PlanOut,
        *,
        initial_target: Optional[Tuple[int, int, int]] = None,
        replan_depth: int = 0,
    ) -> None:
        """LLM が出力した高レベルステップを PlanExecutor へ委譲する。"""

        await self._plan_executor.run(
            plan_out,
            initial_target=initial_target,
            replan_depth=replan_depth,
        )

    def _extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        return self._action_analyzer.extract_coordinates(text)

    def _extract_argument_coordinates(
        self, arguments: PlanArguments | Dict[str, Any] | None
    ) -> Optional[Tuple[int, int, int]]:
        return self._action_analyzer.extract_argument_coordinates(arguments)

