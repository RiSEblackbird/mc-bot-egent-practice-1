# -*- coding: utf-8 -*-
"""Directive 系ステップの解釈と実行を一手に引き受けるモジュール。

PlanExecutor 側から directive_scope の開始・終了などの副作用を隠蔽し、
単純な戻り値（handled/observation/status 等）で分岐できるようにする。
新人メンバーが流れを追いやすいよう、各ハンドリング分岐には「なぜ」
そうするのかを明示的にコメントする。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from orchestrator.directive_utils import (
    directive_scope,
    execute_hybrid_directive,
    parse_hybrid_directive_args,
)
from orchestrator.action_step_executor import ActionStepExecutor
from planner import ActionDirective, PlanOut, ReActStep
from runtime.rules import DETECTION_TASK_KEYWORDS

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用の依存
    from orchestrator.plan_executor import PlanExecutor


@dataclass
class DirectiveResult:
    """DirectiveExecutor が返す単純な結果コンテナ。

    PlanExecutor.run 側ではこの構造体だけを見れば挙動を分岐できるように
    しておき、フローの理解コストを下げる。
    """

    handled: bool
    observation: str = ""
    status: str = "skipped"
    event_level: str = "trace"
    log_level: int = logging.INFO
    detection_report: Optional[Dict[str, Any]] = None
    last_target_coords: Optional[Tuple[int, int, int]] = None
    failure_reason: Optional[str] = None
    should_halt: bool = False
    emit_log: bool = True


class DirectiveExecutor:
    """Directive の種類ごとに処理を切り替える協調クラス。"""

    def __init__(self, plan_executor: "PlanExecutor") -> None:
        # PlanExecutor への参照を保持し、既存のユーティリティや __getattr__
        # フォールバック（AgentOrchestrator のメソッド）を再利用する。
        self._plan = plan_executor
        self._logger = plan_executor.logger
        self._actions = plan_executor.actions
        self._movement_service = plan_executor.movement_service
        self._task_router = plan_executor.task_router
        self._hybrid_handler = plan_executor._hybrid_handler
        self._default_move_target = plan_executor.default_move_target
        # 行動ステップの実行を専用クラスへ委譲し、PlanExecutor.run 側の分岐を浅くする。
        self._action_step_executor = ActionStepExecutor(plan_executor)

    async def handle_step(
        self,
        *,
        directive: Optional[ActionDirective],
        directive_meta: Optional[Dict[str, Any]],
        directive_coords: Optional[Tuple[int, int, int]],
        argument_coords: Optional[Tuple[int, int, int]],
        normalized: str,
        plan_out: PlanOut,
        index: int,
        total_steps: int,
        react_entry: Optional[ReActStep],
        thought_text: str,
        last_target_coords: Optional[Tuple[int, int, int]],
        action_backlog: List[Dict[str, str]],
    ) -> DirectiveResult:
        """単一ステップを解釈し、実行結果を返すメインハンドラー。"""

        if not normalized:
            return DirectiveResult(
                handled=True,
                observation="ステップ文字列が空だったためスキップしました。",
                status="skipped",
            )

        minedojo_result = await self._handle_minedojo_directive(
            directive, plan_out, index, react_entry
        )
        if minedojo_result:
            return minedojo_result

        chat_result = await self._handle_chat_directive(
            directive, normalized, directive_meta, react_entry
        )
        if chat_result:
            return chat_result

        hybrid_result = await self._handle_hybrid_directive(
            directive,
            directive_meta,
            plan_out,
            index=index,
            total_steps=total_steps,
            react_entry=react_entry,
            thought_text=thought_text,
        )
        if hybrid_result:
            return hybrid_result

        detection_result = await self._handle_detection_task(
            directive,
            directive_meta,
            normalized,
            plan_out,
            react_entry,
            index,
        )
        if detection_result:
            return detection_result

        coords_result = await self._handle_coordinates_task(
            directive,
            directive_meta,
            normalized,
            directive_coords,
            argument_coords,
            react_entry,
            action_backlog,
        )
        if coords_result:
            return coords_result

        status_check_result = await self._handle_status_check(
            normalized, react_entry
        )
        if status_check_result:
            return status_check_result

        proactive_move_result = await self._handle_proactive_move(
            normalized, last_target_coords, react_entry
        )
        if proactive_move_result:
            return proactive_move_result

        action_step_result = await self._action_step_executor.handle_step(
            normalized,
            directive_meta,
            directive_coords,
            last_target_coords,
            action_backlog,
            directive_category=directive.category
            if isinstance(directive, ActionDirective)
            else None,
        )
        if action_step_result:
            if react_entry and action_step_result.observation:
                react_entry.observation = action_step_result.observation
            return DirectiveResult(
                handled=action_step_result.handled,
                observation=action_step_result.observation,
                status=action_step_result.status,
                event_level=action_step_result.event_level,
                log_level=action_step_result.log_level,
                last_target_coords=action_step_result.last_target_coords,
                failure_reason=action_step_result.failure_reason,
                should_halt=action_step_result.should_halt,
                emit_log=action_step_result.emit_log,
            )

        return await self._handle_fallback(normalized, react_entry)

    async def _handle_minedojo_directive(
        self,
        directive: Optional[ActionDirective],
        plan_out: PlanOut,
        index: int,
        react_entry: Optional[ReActStep],
    ) -> Optional[DirectiveResult]:
        """MineDojo executor 向けの directive を処理する。"""

        if not directive or directive.executor != "minedojo":
            return None
        handled = await self._plan.minedojo_handler.handle_directive(
            directive, plan_out, index
        )
        if not handled:
            return None

        observation = "MineDojo の自己対話タスクを実行しました。"
        if react_entry:
            react_entry.observation = observation
        return DirectiveResult(
            handled=True,
            observation=observation,
            status="completed",
            event_level="progress",
        )

    async def _handle_chat_directive(
        self,
        directive: Optional[ActionDirective],
        normalized: str,
        directive_meta: Optional[Dict[str, Any]],
        react_entry: Optional[ReActStep],
    ) -> Optional[DirectiveResult]:
        """チャット送信系 directive を実行する。"""

        if not directive or directive.executor != "chat":
            return None
        chat_message = str(directive.args.get("message") if isinstance(directive.args, dict) else "") or directive.label or normalized
        if not chat_message:
            return None

        with directive_scope(self._actions, directive_meta):
            await self._actions.say(chat_message)
        observation_text = f"チャット通知を送信: {chat_message}"
        if react_entry:
            react_entry.observation = observation_text

        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="completed",
            event_level="progress",
        )

    async def _handle_hybrid_directive(
        self,
        directive: Optional[ActionDirective],
        directive_meta: Optional[Dict[str, Any]],
        plan_out: PlanOut,
        *,
        index: int,
        total_steps: int,
        react_entry: Optional[ReActStep],
        thought_text: str,
    ) -> Optional[DirectiveResult]:
        """ハイブリッド directive を解析・実行する。"""

        if not directive or directive.executor != "hybrid":
            return None
        try:
            hybrid_payload = parse_hybrid_directive_args(self._hybrid_handler, directive)
        except ValueError as exc:
            await self._movement_service.report_execution_barrier(
                directive.label or directive.step or "hybrid",
                f"ハイブリッド指示の解析に失敗しました: {exc}",
            )
            return DirectiveResult(
                handled=True,
                observation=str(exc),
                status="failed",
                event_level="fault",
                log_level=logging.WARNING,
            )

        handled = await execute_hybrid_directive(
            self._hybrid_handler,
            directive,
            hybrid_payload,
            directive_meta=directive_meta,
            react_entry=react_entry,
            thought_text=thought_text,
            index=index,
            total_steps=total_steps,
        )
        if not handled:
            return None

        # execute_hybrid_directive 内で react_entry 等を更新するため、ここでは
        # 追加ログを発行せず成功を伝えるだけにとどめる。
        observation_text = react_entry.observation if react_entry else "ハイブリッド指示を完了しました。"
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="completed",
            event_level="progress",
            emit_log=False,
        )

    async def _handle_detection_task(
        self,
        directive: Optional[ActionDirective],
        directive_meta: Optional[Dict[str, Any]],
        normalized: str,
        plan_out: PlanOut,
        react_entry: Optional[ReActStep],
        index: Optional[int] = None,
    ) -> Optional[DirectiveResult]:
        """検出系タスクを実行して報告する。"""

        detection_category = None
        if directive and directive.category in DETECTION_TASK_KEYWORDS:
            detection_category = directive.category
        if not detection_category:
            detection_category = self._task_router.classify_detection_task(normalized)
        if not detection_category and plan_out.intent.strip().lower().startswith("report"):
            detection_category = "general_status"
        if not detection_category:
            return None

        if index is not None:
            self._logger.info(
                "plan_step index=%d classified as detection_report category=%s",
                index,
                detection_category,
            )
        with directive_scope(self._actions, directive_meta):
            detection_result = await self._task_router.perform_detection_task(
                detection_category
            )
        if detection_result:
            observation_text = str(
                detection_result.get("summary")
                or "ステータスを報告しました。"
            )
            data = detection_result.get("data")
            if isinstance(data, dict):
                coords = (data.get("x"), data.get("y"), data.get("z"))
                if all(isinstance(coord, (int, float)) for coord in coords):
                    observation_text = (
                        f"位置報告: X={int(coords[0])} / Y={int(coords[1])} / Z={int(coords[2])}"
                    )
            if react_entry:
                react_entry.observation = observation_text
            return DirectiveResult(
                handled=True,
                observation=observation_text,
                status="completed",
                event_level="progress",
                detection_report=detection_result,
            )

        observation_text = "ステータス取得に失敗し障壁を報告しました。"
        if react_entry:
            react_entry.observation = observation_text
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="failed",
            event_level="fault",
            log_level=logging.WARNING,
        )

    async def _handle_coordinates_task(
        self,
        directive: Optional[ActionDirective],
        directive_meta: Optional[Dict[str, Any]],
        normalized: str,
        directive_coords: Optional[Tuple[int, int, int]],
        argument_coords: Optional[Tuple[int, int, int]],
        react_entry: Optional[ReActStep],
        action_backlog: List[Dict[str, str]],
    ) -> Optional[DirectiveResult]:
        """座標を伴う移動/行動タスクを処理する。"""

        coords = directive_coords or argument_coords or self._plan._extract_coordinates(normalized)
        if not coords:
            return None

        action_category = (directive.category if isinstance(directive, ActionDirective) and directive.category else "move")
        self._logger.info(
            "plan_step classified as %s coords=%s",
            action_category,
            coords,
        )
        with directive_scope(self._actions, directive_meta):
            handled, last_target_coords, failure_detail = await self._plan._handle_action_task(
                action_category,
                normalized,
                last_target_coords=coords,
                backlog=action_backlog,
                explicit_coords=coords,
            )
        if not handled:
            observation_text = failure_detail or "座標移動の処理に失敗しました。"
            if react_entry:
                react_entry.observation = observation_text
            return DirectiveResult(
                handled=True,
                observation=observation_text,
                status="failed",
                event_level="fault",
                log_level=logging.WARNING,
                last_target_coords=last_target_coords,
                failure_reason=failure_detail
                or "座標移動の処理に失敗しました。Mineflayer の応答を確認してください。",
                should_halt=True,
            )

        target_coords = last_target_coords or coords
        observation_text = (
            f"移動成功: X={target_coords[0]} / Y={target_coords[1]} / Z={target_coords[2]}"
            if target_coords
            else "移動に成功しました。"
        )
        if react_entry:
            react_entry.observation = observation_text
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="completed",
            event_level="progress",
            last_target_coords=last_target_coords or coords,
        )

    async def _handle_status_check(
        self, normalized: str, react_entry: Optional[ReActStep]
    ) -> Optional[DirectiveResult]:
        """状況確認系ステップを無害にスキップする。"""

        if not self._plan._is_status_check_step(normalized):
            return None
        observation_text = "ステータス確認ステップのため実行要と判断しました。"
        if react_entry:
            react_entry.observation = observation_text
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="skipped",
        )

    async def _handle_proactive_move(
        self,
        normalized: str,
        last_target_coords: Optional[Tuple[int, int, int]],
        react_entry: Optional[ReActStep],
    ) -> Optional[DirectiveResult]:
        """移動継続で吸収できるステップを処理する。"""

        if not await self._plan._attempt_proactive_progress(normalized, last_target_coords):
            return None
        observation_text = "前回の目的地へ継続移動しました。"
        if react_entry:
            react_entry.observation = observation_text
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="completed",
            event_level="progress",
            last_target_coords=last_target_coords,
        )

    async def _handle_fallback(
        self, normalized: str, react_entry: Optional[ReActStep]
    ) -> DirectiveResult:
        """いずれのハンドラーにも該当しない場合の最終処理。"""

        observation_text = "対応可能なアクションが見つからず障壁を通知しました。"
        if react_entry:
            react_entry.observation = observation_text
        await self._movement_service.report_execution_barrier(
            normalized,
            "対応可能なアクションが見つからず停滞しています。計画ステップの表現を見直してください。",
        )
        return DirectiveResult(
            handled=True,
            observation=observation_text,
            status="failed",
            event_level="fault",
            log_level=logging.WARNING,
        )

