# -*- coding: utf-8 -*-
"""LangGraph ベースのプラン生成エントリポイント。

planner.graph へ分離したステート・ノード定義をここから呼び出し、
テスト時は依存注入でクライアントやペイロードを差し替えやすくする。
"""
from __future__ import annotations

import asyncio
import openai
from typing import Any, Callable, Dict, Optional

from langgraph.graph.state import CompiledStateGraph

from llm.client import (
    AsyncOpenAI,
    create_async_openai_client,
    resolve_gpt5_reasoning_effort,
    resolve_gpt5_verbosity,
    resolve_request_temperature,
)
from .graph import (
    ActionDirective,
    BARRIER_SYSTEM,
    BarrierNotification,
    BarrierNotificationError,
    BarrierNotificationTimeout,
    PlanArguments,
    PlanOut,
    PlanPriorityManager,
    UnifiedPlanState,
    build_barrier_prompt,
    build_plan_graph,
    ReActStep,
    record_recovery_hints,
    record_structured_step,
    _build_responses_input,
    _extract_output_text,
)
from planner_config import PlannerConfig, load_planner_config
from utils import setup_logger

logger = setup_logger("planner")

_PLANNER_CONFIG = load_planner_config()
_PRIORITY_MANAGER = PlanPriorityManager(_PLANNER_CONFIG)


def _default_async_client_factory() -> AsyncOpenAI:
    """AsyncOpenAI の生成を共通化し、テスト時はモックへ差し替えやすくする。"""

    try:
        if _PLANNER_CONFIG.api_key or _PLANNER_CONFIG.base_url:
            return openai.AsyncOpenAI(api_key=_PLANNER_CONFIG.api_key, base_url=_PLANNER_CONFIG.base_url)
        return openai.AsyncOpenAI()
    except TypeError:
        # pytest のモックで引数を受け付けない場合にも備える。
        return openai.AsyncOpenAI()


_ASYNC_CLIENT_FACTORY = _default_async_client_factory
_PLAN_GRAPH: Optional[CompiledStateGraph] = None


def _build_responses_payload(system_prompt: str, user_prompt: str, config: PlannerConfig) -> Dict[str, Any]:
    """Responses API 呼び出しに共通するペイロードを一元生成する。"""

    payload: Dict[str, Any] = {
        "model": config.model,
        "input": _build_responses_input(system_prompt, user_prompt),
        "text": {"format": {"type": "json_object"}},
    }

    temperature = resolve_request_temperature(config)
    if temperature is not None:
        payload["temperature"] = temperature

    verbosity = resolve_gpt5_verbosity(config)
    if verbosity:
        payload.setdefault("text", {})["verbosity"] = verbosity

    reasoning_effort = resolve_gpt5_reasoning_effort(config)
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

    return payload


def _get_plan_graph() -> CompiledStateGraph:
    global _PLAN_GRAPH
    if _PLAN_GRAPH is None:
        _PLAN_GRAPH = build_plan_graph(
            _PLANNER_CONFIG,
            priority_manager=_PRIORITY_MANAGER,
            async_client_factory=_ASYNC_CLIENT_FACTORY,
            payload_builder=lambda system, user: _build_responses_payload(system, user, _PLANNER_CONFIG),
        )
    return _PLAN_GRAPH


async def plan(user_msg: str, context: Dict[str, Any]) -> PlanOut:
    """ユーザーの日本語チャットを Responses API へ投げ、実行プランを復元する。"""

    graph = _get_plan_graph()
    safe_user_msg = str(user_msg or "")
    safe_context = dict(context or {})
    initial_state: UnifiedPlanState = {
        "user_msg": safe_user_msg,
        "context": safe_context,
        "structured_events": [],
    }
    result = await graph.ainvoke(initial_state)
    plan_out = result.get("plan_out")

    def _attach_plan_metadata(plan: PlanOut) -> PlanOut:
        """LangGraph から戻る補助情報を PlanOut へ再適用する。"""

        backlog = result.get("backlog")
        if isinstance(backlog, list):
            plan.backlog = list(backlog)
        next_action = result.get("next_action")
        if isinstance(next_action, str) and next_action:
            plan.next_action = next_action
        return plan

    if isinstance(plan_out, PlanOut):
        return _attach_plan_metadata(plan_out)

    if isinstance(plan_out, dict):
        try:
            return _attach_plan_metadata(PlanOut.model_validate(plan_out))
        except Exception:
            logger.warning("plan graph returned non PlanOut dict; fallback engaged")

    logger.warning("plan graph returned unexpected payload; using default fallback")
    return PlanOut(plan=[], resp="了解しました。")


async def get_plan_priority() -> str:
    """現在のプラン優先度を LangGraph の状態から取得する。"""

    return await _PRIORITY_MANAGER.snapshot()


async def reset_plan_priority() -> None:
    """テストやリカバリーでプラン優先度を初期状態へ戻す。"""

    await _PRIORITY_MANAGER.mark_success()


async def compose_barrier_notification(
    step: str, reason: str, context: Dict[str, Any], *,
    client_factory: Optional[Callable[[], AsyncOpenAI]] = None,
) -> str:
    """作業障壁を Responses API へ説明し、プレイヤー向け確認メッセージを得る。"""

    factory = client_factory or _ASYNC_CLIENT_FACTORY
    client = factory()
    prompt = build_barrier_prompt(step, reason, context)
    logger.info(f"Barrier prompt: {prompt}")

    request_payload = _build_responses_payload(BARRIER_SYSTEM, prompt, _PLANNER_CONFIG)

    try:
        resp = await asyncio.wait_for(
            client.responses.create(**request_payload),
            timeout=_PLANNER_CONFIG.llm_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        message = f"barrier notification timed out after {_PLANNER_CONFIG.llm_timeout_seconds:.1f} seconds"
        logger.warning(
            "barrier notification request timed out (step=%s): %s",
            step,
            message,
        )
        raise BarrierNotificationTimeout(message) from exc
    except Exception as exc:
        logger.warning(
            "barrier notification request failed (step=%s): %s",
            step,
            exc,
        )
        raise BarrierNotificationError(str(exc)) from exc

    content = _extract_output_text(resp)
    logger.info(f"Barrier raw: {content}")

    try:
        parsed = BarrierNotification.model_validate_json(content)
        if parsed.message.strip():
            return parsed.message.strip()
    except Exception:
        logger.exception("failed to parse barrier notification JSON")

    # LLM 応答がパースできない場合は従来の短縮メッセージを返す。
    return "問題を確認しました。状況を共有いただけますか？"


__all__ = [
    "ActionDirective",
    "plan",
    "openai",
    "PlanArguments",
    "PlanOut",
    "ReActStep",
    "get_plan_priority",
    "reset_plan_priority",
    "compose_barrier_notification",
    "record_structured_step",
    "record_recovery_hints",
]
