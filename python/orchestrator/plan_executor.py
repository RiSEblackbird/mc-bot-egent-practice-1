# -*- coding: utf-8 -*-
"""AgentOrchestrator の計画実行ロジックを担当するモジュール。"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TYPE_CHECKING

from orchestrator.context import OrchestratorDependencies, PlanRuntimeContext
from planner import ActionDirective, PlanArguments, PlanOut, ReActStep, plan
from runtime.rules import ACTION_TASK_RULES, DETECTION_TASK_KEYWORDS
from runtime.reflection_prompt import build_reflection_prompt
from utils import log_structured_event

if TYPE_CHECKING:  # pragma: no cover
    from agent import AgentOrchestrator


class PlanExecutor:
    """AgentOrchestrator から計画実行と再計画処理を切り出した協調クラス。"""

    def __init__(
        self,
        *,
        agent: "AgentOrchestrator",
        dependencies: OrchestratorDependencies,
        runtime: PlanRuntimeContext,
    ) -> None:
        self._agent = agent
        self._dependencies = dependencies
        self._runtime = runtime

    def __getattr__(self, item: str) -> Any:
        """AgentOrchestrator へ属性アクセスをフォールバックする。"""

        return getattr(self._agent, item)

    async def run(
        self,
        plan_out: PlanOut,
        *,
        initial_target: Optional[Tuple[int, int, int]] = None,
        replan_depth: int = 0,
    ) -> None:
        """LLM が出力した高レベルステップを簡易ヒューリスティックで実行する。"""

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

        await self.run(
            new_plan,
            initial_target=None,
            replan_depth=replan_depth + 1,
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

    def _match_keywords(self, text: str, keywords: Tuple[str, ...]) -> bool:
        return any(keyword and keyword in text for keyword in keywords)

    @contextlib.contextmanager
    def directive_scope(self, meta: Optional[Dict[str, Any]]):
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


__all__ = ["PlanExecutor"]
