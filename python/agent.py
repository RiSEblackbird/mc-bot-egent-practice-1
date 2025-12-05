# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent_bootstrap import build_agent_dependencies
from chat_pipeline import ChatPipeline
from bridge_role_handler import BridgeRoleHandler
from perception_service import PerceptionCoordinator
from orchestrator import OrchestratorDependencies, PlanRuntimeContext
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
from orchestrator.plan_executor import PlanExecutor
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
from runtime.action_graph import ChatTask
from runtime.inventory_sync import InventorySynchronizer
from runtime.reflection_prompt import build_reflection_prompt
from runtime.hybrid_directive import HybridDirectivePayload
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
        self._plan_runtime = PlanRuntimeContext(
            default_move_target=self.default_move_target,
            low_food_threshold=self.low_food_threshold,
            structured_event_history_limit=self.structured_event_history_limit,
            perception_history_limit=self.perception_history_limit,
        )
        self._dependencies = OrchestratorDependencies(
            actions=self.actions,
            memory=self.memory,
            chat_pipeline=self._chat_pipeline,
            bridge_roles=self._bridge_roles,
            perception=self._perception,
            status_service=self.status_service,
            inventory_sync=self.inventory_sync,
            hybrid_handler=self._hybrid_handler,
            minedojo_handler=self.minedojo_handler,
            tracer=self._tracer,
            runtime_settings=self.settings,
            skill_repository=self.skill_repository,
        )
        self._plan_executor = PlanExecutor(
            agent=self,
            dependencies=self._dependencies,
            runtime=self._plan_runtime,
        )
        self._action_analyzer = ActionAnalyzer()
        self._skill_detection = SkillDetectionCoordinator(
            actions=self.actions,
            memory=self.memory,
            status_service=self.status_service,
            inventory_sync=self.inventory_sync,
            skill_repository=self.skill_repository,
        )

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

    def _classify_detection_task(self, text: str) -> Optional[str]:
        return self._action_analyzer.classify_detection_task(text)

    def _infer_equip_arguments(self, text: str) -> Optional[Dict[str, str]]:
        return self._action_analyzer.infer_equip_arguments(text)

    def _infer_mining_request(self, text: str) -> Dict[str, Any]:
        return self._action_analyzer.infer_mining_request(text)


    async def _perform_detection_task(self, category: str) -> Optional[Dict[str, Any]]:
        """Mineflayer 側のステータス取得コマンドを実行し、メモリと報告用要約を更新する。"""

        result, error_detail = await self._skill_detection.perform_detection_task(category)
        if result:
            return result

        if error_detail:
            label_map = {
                "player_position": "現在位置の確認",
                "inventory_status": "所持品の確認",
                "general_status": "状態の共有",
            }
            await self._report_execution_barrier(
                label_map.get(category, "ステータス確認"),
                f"ステータス取得に失敗しました（{error_detail}）。",
            )
        else:
            self.logger.warning("unknown detection category encountered category=%s", category)
        return None

    def _summarize_position_status(self, data: Dict[str, Any]) -> str:
        return self._skill_detection.summarize_position_status(data)

    def _summarize_general_status(self, data: Dict[str, Any]) -> str:
        return self._skill_detection.summarize_general_status(data)

    def _classify_action_task(self, text: str) -> Optional[str]:
        return self._action_analyzer.classify_action_task(text)

    async def _find_skill_for_step(
        self,
        category: str,
        step: str,
    ) -> Optional[SkillMatch]:
        """MineDojo 文脈を含めたスキル探索をハンドラーへ委譲する。"""

        return await self._skill_detection.find_skill_for_step(
            self.minedojo_handler, category, step
        )

    async def _execute_skill_match(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        """既存スキルを Mineflayer 側へ再生指示し、結果を戻す。"""

        return await self._skill_detection.execute_skill_match(match, step)

    async def _begin_skill_exploration(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        """未習得スキルのため探索モードへ切り替える。"""

        return await self._skill_detection.begin_skill_exploration(match, step)

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
        return self._action_analyzer.extract_coordinates(text)

    def _extract_argument_coordinates(
        self, arguments: PlanArguments | Dict[str, Any] | None
    ) -> Optional[Tuple[int, int, int]]:
        return self._action_analyzer.extract_argument_coordinates(arguments)

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


