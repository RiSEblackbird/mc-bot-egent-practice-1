"""プランナーの LangGraph 構築と関連ステート管理を担当するモジュール。"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from opentelemetry.trace import Status, StatusCode

from llm.client import AsyncOpenAI
from planner_config import PlannerConfig
from utils import span_context

from .models import (
    ActionDirective,
    BarrierNotification,
    BarrierNotificationError,
    BarrierNotificationTimeout,
    ConstraintSpec,
    ExecutionHint,
    GoalProfile,
    PlanArguments,
    PlanOut,
    ReActStep,
    normalize_directives,
)
from .priority import PlanPriorityManager
from .prompts import (
    BARRIER_SYSTEM,
    SOCRATIC_REVIEW_SYSTEM,
    SYSTEM,
    build_barrier_prompt,
    build_pre_action_review_prompt,
    build_responses_input,
    build_user_prompt,
    extract_output_text,
)
from .state import UnifiedPlanState, record_recovery_hints, record_structured_step, logger

# 以前の公開 API を維持するためのエイリアス
_build_responses_input = build_responses_input
_extract_output_text = extract_output_text


def _extract_recovery_hints_from_context(state: UnifiedPlanState) -> List[str]:
    hints: List[str] = []
    context = state.get("context") or {}
    raw_hints = context.get("recovery_hints")
    if isinstance(raw_hints, (list, tuple)):
        for hint in raw_hints:
            text = str(hint or "").strip()
            if text:
                hints.append(text)
    return hints


async def _compose_pre_action_follow_up(
    plan_out: PlanOut,
    reason: str,
    *,
    client_factory: Callable[[], AsyncOpenAI],
    payload_builder: Callable[[str, str], Dict[str, Any]],
    timeout_seconds: float,
) -> str:
    """Responses API を利用してソクラテス式のフォローアップ文を生成する。"""

    client = client_factory()
    prompt = build_pre_action_review_prompt(plan_out, reason)
    payload = payload_builder(SOCRATIC_REVIEW_SYSTEM, prompt)
    try:
        resp = await asyncio.wait_for(
            client.responses.create(**payload),
            timeout=timeout_seconds,
        )
        text = extract_output_text(resp).strip()
        if text:
            return text
    except Exception as exc:  # pragma: no cover - LLM 障害はログのみに留める
        logger.warning("pre_action_review compose failed: %s", exc)
    return "作業内容に不確実な点があるため、追加の指示をいただけますか？"


def build_plan_graph(
    config: PlannerConfig,
    *,
    priority_manager: PlanPriorityManager,
    async_client_factory: Callable[[], AsyncOpenAI],
    payload_builder: Callable[[str, str], Dict[str, Any]],
) -> CompiledStateGraph:
    """Plan 用 LangGraph を構築してコンパイルする。"""

    manager = priority_manager
    graph: StateGraph = StateGraph(UnifiedPlanState)

    async def prepare_payload(state: UnifiedPlanState) -> Dict[str, Any]:
        recovery_hints = _extract_recovery_hints_from_context(state)
        if recovery_hints:
            record_recovery_hints(state, recovery_hints)
        prompt = build_user_prompt(state.get("user_msg", ""), state.get("context", {}))
        logger.info("LLM prompt: %s", prompt)
        payload = payload_builder(SYSTEM, prompt)
        metadata = record_structured_step(
            state,
            step_label="prepare_payload",
            inputs={"user_msg": state.get("user_msg", ""), "context_keys": list(state.get("context", {}).keys())},
            outputs={"prompt_preview": prompt[:120]},
        )
        result: Dict[str, Any] = {"prompt": prompt, "payload": payload}
        result.update(metadata)
        return result

    async def call_llm(state: UnifiedPlanState) -> Dict[str, Any]:
        """Responses API を呼び出し、タイムアウト時は安全なフォールバックを返す。"""

        with span_context(
            "llm.responses.create",
            langgraph_node_id="plan.call_llm",
            event_level="info",
            attributes={"llm.model": config.model},
        ) as span:

            async def _build_failure_payload(reason: str, *, log_as_warning: bool) -> Dict[str, Any]:
                """例外発生時に優先度降格とフォールバックプランを組み立てる。"""

                priority = await manager.mark_failure()
                fallback = PlanOut(plan=[], resp="了解しました。")
                if log_as_warning:
                    logger.warning("plan graph detected LLM timeout: %s", reason)
                else:
                    logger.exception("plan graph failed to call Responses API: %s", reason)
                if span.is_recording():
                    span.set_status(Status(StatusCode.ERROR, reason))
                payload = {
                    "llm_error": reason,
                    "content": "",
                    "priority": priority,
                    "fallback_plan_out": fallback,
                }
                payload.update(
                    record_structured_step(
                        state,
                        step_label="call_llm",
                        inputs={"model": config.model},
                        outputs={"priority": priority, "fallback": True},
                        error=reason,
                    )
                )
                return payload

            try:
                client = async_client_factory()
                resp = await asyncio.wait_for(
                    client.responses.create(**state["payload"]),
                    timeout=config.llm_timeout_seconds,
                )
            except asyncio.TimeoutError:
                timeout_reason = f"timeout after {config.llm_timeout_seconds:.1f} seconds"
                if span.is_recording():
                    span.set_attribute("llm.timeout_seconds", config.llm_timeout_seconds)
                return await _build_failure_payload(timeout_reason, log_as_warning=True)
            except Exception as exc:
                if span.is_recording():
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                return await _build_failure_payload(str(exc), log_as_warning=False)

            content = extract_output_text(resp)
            logger.info("LLM raw: %s", content)
            payload = {"response": resp, "content": content}
            payload.update(
                record_structured_step(
                    state,
                    step_label="call_llm",
                    inputs={"model": config.model},
                    outputs={"content_length": len(content)},
                )
            )
            if span.is_recording():
                span.set_attribute("llm.content_length", len(content))
            return payload

    async def parse_plan(state: UnifiedPlanState) -> Dict[str, Any]:
        if state.get("llm_error"):
            priority = state.get("priority") or await manager.mark_failure()
            result: Dict[str, Any] = {"parse_error": state["llm_error"], "priority": priority}
            fallback_plan = state.get("fallback_plan_out")
            if fallback_plan is not None:
                result["fallback_plan_out"] = fallback_plan
            result.update(
                record_structured_step(
                    state,
                    step_label="parse_plan",
                    inputs={"has_llm_error": True},
                    outputs={"priority": priority},
                    error=state.get("llm_error", ""),
                )
            )
            return result

        try:
            plan_data = PlanOut.model_validate_json(state.get("content") or "")
        except Exception as exc:
            logger.exception("plan graph failed to parse JSON plan")
            priority = await manager.mark_failure()
            result = {"parse_error": str(exc), "priority": priority}
            result.update(
                record_structured_step(
                    state,
                    step_label="parse_plan",
                    inputs={"content_preview": (state.get("content") or "")[:120]},
                    outputs={"priority": priority},
                    error=str(exc),
                )
            )
            return result

        priority = await manager.mark_success()
        recovery_hints = _extract_recovery_hints_from_context(state)
        if recovery_hints:
            plan_data.recovery_hints = recovery_hints
        result = {"plan_out": plan_data, "priority": priority}
        result.update(
            record_structured_step(
                state,
                step_label="parse_plan",
                inputs={"content_preview": (state.get("content") or "")[:120]},
                outputs={"priority": priority, "intent": plan_data.intent},
            )
        )
        return result

    async def normalize_react_trace(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out = state.get("plan_out")
        if not isinstance(plan_out, PlanOut):
            logger.warning("normalize_react_trace received non PlanOut")
            return {}

        trace: List[ReActStep] = []
        for entry in plan_out.react_trace:
            trace.append(
                ReActStep(
                    thought=entry.thought,
                    action=entry.action,
                    observation=getattr(entry, "observation", ""),
                )
            )
        plan_out.react_trace = trace
        normalize_directives(plan_out)

        return record_structured_step(
            state,
            step_label="normalize_react_trace",
            inputs={"react_trace_count": len(trace)},
            outputs={"directive_count": len(plan_out.directives)},
        )

    async def pre_action_review(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out: PlanOut = state.get("plan_out")
        if not isinstance(plan_out, PlanOut):
            logger.warning("pre_action_review received non PlanOut")
            return {}

        evaluation = manager.evaluate_confidence_gate(plan_out)
        if not evaluation["needs_review"]:
            return record_structured_step(
                state,
                step_label="pre_action_review",
                inputs={"confidence": plan_out.confidence},
                outputs={"needs_review": False},
            )

        follow_up_message = await _compose_pre_action_follow_up(
            plan_out,
            evaluation.get("reason", ""),
            client_factory=async_client_factory,
            payload_builder=payload_builder,
            timeout_seconds=config.llm_timeout_seconds,
        )
        plan_out.next_action = "chat"
        plan_out.resp = follow_up_message or plan_out.resp
        plan_out.backlog = plan_out.backlog or []
        plan_out.backlog.append(
            {"type": "review", "reason": evaluation.get("reason", ""), "label": "自動確認"}
        )

        result = {
            "plan_out": plan_out,
            "follow_up_message": follow_up_message,
            "confirmation_required": True,
        }
        result.update(
            record_structured_step(
                state,
                step_label="pre_action_review",
                inputs={"confidence": plan_out.confidence},
                outputs={"needs_review": True, "reason": evaluation.get("reason", "")},
            )
        )
        return result

    async def intent_negotiation(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out = state.get("plan_out")
        content = state.get("content") or ""
        confirmation_required = bool(state.get("confirmation_required"))
        backlog: List[Dict[str, str]] = []
        follow_up_message = state.get("follow_up_message", "")

        if content:
            backlog.append({"type": "plan", "summary": content[:120], "label": "プラン概要"})

        if isinstance(plan_out, PlanOut):
            blocking = getattr(plan_out, "blocking", False)
            confirmation_required = confirmation_required or bool(
                getattr(plan_out, "clarification_needed", "none") != "none"
            )
            if plan_out.backlog:
                backlog.extend(plan_out.backlog)
            if blocking or confirmation_required:
                confirmation_required = True
                plan_out.next_action = "chat"
            else:
                plan_out.next_action = "execute"
            plan_out.backlog = backlog
            if confirmation_required and follow_up_message:
                plan_out.resp = follow_up_message

        result = {
            "plan_out": plan_out,
            "backlog": backlog,
            "confirmation_required": confirmation_required,
            "follow_up_message": follow_up_message,
            "next_action": getattr(plan_out, "next_action", state.get("next_action")),
        }
        result.update(
            record_structured_step(
                state,
                step_label="intent_negotiation",
                inputs={
                    "blocking": getattr(plan_out, "blocking", False),
                    "clarification_needed": getattr(plan_out, "clarification_needed", "none"),
                },
                outputs={
                    "backlog_count": len(backlog),
                    "next_action": getattr(plan_out, "next_action", "execute"),
                    "confirmation_required": confirmation_required,
                },
            )
        )
        return result

    async def route_to_chat(state: UnifiedPlanState) -> Dict[str, Any]:
        """確認フローへ進む場合に next_action を chat へ固定する。"""

        plan_out = state.get("plan_out")
        backlog: List[Dict[str, str]] = list(state.get("backlog") or [])
        if isinstance(plan_out, PlanOut):
            plan_out.backlog = backlog
            plan_out.next_action = "chat"

        result = {"plan_out": plan_out, "backlog": backlog, "next_action": "chat"}
        result.update(
            record_structured_step(
                state,
                step_label="route_to_chat",
                inputs={"backlog_count": len(backlog)},
                outputs={"next_action": "chat"},
            )
        )
        return result

    async def fallback_plan(state: UnifiedPlanState) -> Dict[str, Any]:
        logger.warning(
            "plan fallback triggered parse_error=%s llm_error=%s",
            state.get("parse_error"),
            state.get("llm_error"),
        )
        fallback = state.get("fallback_plan_out")
        if not isinstance(fallback, PlanOut):
            fallback = PlanOut(plan=[], resp="了解しました。")
        result = {"plan_out": fallback}
        result.update(
            record_structured_step(
                state,
                step_label="fallback_plan",
                inputs={"parse_error": state.get("parse_error"), "llm_error": state.get("llm_error")},
                outputs={"plan_steps": len(fallback.plan)},
            )
        )
        return result

    async def finalize(state: UnifiedPlanState) -> Dict[str, Any]:
        priority = state.get("priority")
        if priority:
            logger.info("plan priority resolved=%s", priority)
        if priority is not None:
            return {"priority": priority}
        return {}

    graph.add_node("prepare_payload", prepare_payload)
    graph.add_node("call_llm", call_llm)
    graph.add_node("parse_plan", parse_plan)
    graph.add_node("normalize_react_trace", normalize_react_trace)
    graph.add_node("pre_action_review", pre_action_review)
    graph.add_node("intent_negotiation", intent_negotiation)
    graph.add_node("route_to_chat", route_to_chat)
    graph.add_node("fallback_plan", fallback_plan)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare_payload")
    graph.add_edge("prepare_payload", "call_llm")
    graph.add_edge("call_llm", "parse_plan")
    graph.add_conditional_edges(
        "parse_plan",
        lambda state: "success" if "plan_out" in state else "failure",
        {"success": "normalize_react_trace", "failure": "fallback_plan"},
    )
    graph.add_edge("normalize_react_trace", "pre_action_review")
    graph.add_edge("pre_action_review", "intent_negotiation")
    graph.add_conditional_edges(
        "intent_negotiation",
        lambda state: "chat" if state.get("confirmation_required") else "execute",
        {"execute": "finalize", "chat": "route_to_chat"},
    )
    graph.add_edge("route_to_chat", "finalize")
    graph.add_edge("fallback_plan", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


__all__ = [
    "ActionDirective",
    "BarrierNotification",
    "BarrierNotificationError",
    "BarrierNotificationTimeout",
    "ConstraintSpec",
    "ExecutionHint",
    "GoalProfile",
    "PlanArguments",
    "PlanOut",
    "PlanPriorityManager",
    "ReActStep",
    "UnifiedPlanState",
    "build_barrier_prompt",
    "build_plan_graph",
    "build_pre_action_review_prompt",
    "build_user_prompt",
    "record_recovery_hints",
    "record_structured_step",
    "SYSTEM",
    "BARRIER_SYSTEM",
    "SOCRATIC_REVIEW_SYSTEM",
    "_build_responses_input",
    "_extract_output_text",
    "build_responses_input",
    "extract_output_text",
]
