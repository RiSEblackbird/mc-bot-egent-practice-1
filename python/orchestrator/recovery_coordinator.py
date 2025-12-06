# -*- coding: utf-8 -*-
"""計画実行失敗時のリカバリフローを一括管理するモジュール。

PlanExecutor 側で例外系の分岐が増殖すると可読性が下がるため、反省プロンプト生成・
メモリ更新・再計画依頼といった副作用を 1 つのクラスへまとめる。新人メンバーでも
追いやすいよう、各処理の目的を docstring で明確にしている。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from planner import PlanOut, plan
from runtime.reflection_prompt import build_reflection_prompt

if TYPE_CHECKING:  # pragma: no cover
    from actions import Actions
    from memory import Memory
    from orchestrator.role_perception_adapter import RolePerceptionAdapter
    from orchestrator.task_router import TaskRouter
    from runtime.status_service import StatusService
    from services.movement_service import MovementService


PlanRunner = Callable[[PlanOut, Optional[Tuple[int, int, int]], int], Awaitable[None]]


class RecoveryCoordinator:
    """計画実行の失敗時に発生する副作用を集約する調停役。"""

    def __init__(
        self,
        *,
        actions: "Actions",
        memory: "Memory",
        movement_service: "MovementService",
        role_perception: "RolePerceptionAdapter",
        status_service: "StatusService",
        task_router: "TaskRouter",
        logger: logging.Logger,
        plan_runner: PlanRunner,
        max_replan_depth: int,
    ) -> None:
        self.actions = actions
        self.memory = memory
        self.movement_service = movement_service
        self.role_perception = role_perception
        self.status_service = status_service
        self.task_router = task_router
        self.logger = logger
        # PlanExecutor.run へ依存を明示するためのコールバック。
        self._plan_runner = plan_runner
        self._max_replan_depth = max_replan_depth

    async def handle_failure(
        self,
        *,
        failed_step: str,
        failure_reason: str,
        detection_reports: List[Dict[str, Any]],
        action_backlog: List[Dict[str, str]],
        remaining_steps: List[str],
        replan_depth: int,
    ) -> None:
        """実行障壁発生時の記録・共有・再計画要求をまとめて処理する。"""

        await self.movement_service.report_execution_barrier(
            failed_step, failure_reason
        )

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
            failure_reason = self.role_perception.augment_failure_reason_with_events(
                failure_reason, bridge_reports
            )

        task_signature = self.memory.derive_task_signature(failed_step)
        previous_reflections = self.memory.export_reflections_for_prompt(
            task_signature=task_signature,
            limit=3,
        )
        reflection_prompt = build_reflection_prompt(
            failed_step,
            failure_reason,
            detection_reports=merged_detection_reports,
            action_backlog=action_backlog,
            previous_reflections=previous_reflections,
        )
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
            await self.task_router.handle_detection_reports(
                merged_detection_reports,
                already_responded=True,
            )

        if action_backlog:
            await self.task_router.handle_action_backlog(
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
        """Reflexion プロンプトを含めた再計画リクエストを LLM に送る。"""

        if replan_depth >= self._max_replan_depth:
            self.logger.warning(
                "skip replan because max depth reached step='%s' reason='%s'",
                failed_step,
                failure_reason,
            )
            return

        context = self.status_service.build_context_snapshot(
            current_role_id=self.role_perception.current_role
        )
        inventory_detail = self.memory.get("inventory_detail")
        if inventory_detail is not None:
            context["inventory_detail"] = inventory_detail
        remaining_text = "、".join(remaining_steps) if remaining_steps else ""
        replan_instruction = (
            f"手順「{failed_step}」の実行に失敗しました（{failure_reason}）。"
            "現在の状況を踏まえて作業を継続するための別案を提示してください。"
        )
        if remaining_text:
            replan_instruction += f" 未完了ステップ候補: {remaining_text}"

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

        await self._plan_runner(
            new_plan,
            initial_target=None,
            replan_depth=replan_depth + 1,
        )


__all__ = ["RecoveryCoordinator"]
