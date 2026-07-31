"""OpenAI クライアント生成と Responses API 呼び出しの観測を集約する。"""
from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Dict, Literal, Tuple

import openai

from planner_config import PlannerConfig
from utils import log_structured_event, setup_logger

logger = setup_logger("llm.client")

# pytest でのモック差し替え互換を維持するため、旧インポートと同名のエイリアスを提供する。
AsyncOpenAI = openai.AsyncOpenAI
OpenAI = openai.OpenAI

ResponseCallPurpose = Literal[
    "plan",
    "pre_action_review",
    "barrier_notification",
    "replan",
]
ResponseCallOutcome = Literal[
    "success",
    "timeout",
    "refusal",
    "schema_validation_failure",
    "request_failure",
]


def create_openai_client(config: PlannerConfig) -> OpenAI:
    """同期 OpenAI クライアントを設定付きで初期化する。"""

    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def create_async_openai_client(config: PlannerConfig) -> AsyncOpenAI:
    """非同期 OpenAI クライアントを設定付きで初期化する。"""

    return AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _build_observation(
    config: PlannerConfig,
    *,
    purpose: ResponseCallPurpose,
    replan_depth: int,
    latency_ms: float,
    response: Any = None,
) -> Dict[str, Any]:
    usage = _get_value(response, "usage")
    input_details = _get_value(usage, "input_tokens_details")
    output_details = _get_value(usage, "output_tokens_details")
    return {
        "call_purpose": purpose,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "verbosity": config.verbosity,
        "input_tokens": _optional_int(_get_value(usage, "input_tokens")),
        "cached_input_tokens": _optional_int(_get_value(input_details, "cached_tokens")),
        "output_tokens": _optional_int(_get_value(usage, "output_tokens")),
        "reasoning_tokens": _optional_int(_get_value(output_details, "reasoning_tokens")),
        "latency_ms": round(latency_ms, 3),
        "replan_depth": max(0, int(replan_depth)),
    }


def log_response_outcome(
    observation: Dict[str, Any],
    outcome: ResponseCallOutcome,
    *,
    error: str | None = None,
) -> None:
    """Responses API 呼び出しの最終結果を秘密情報なしで構造化ログへ残す。"""

    context = dict(observation)
    context["outcome"] = outcome
    if error:
        context["error_type"] = error
    log_structured_event(
        logger,
        "openai_response_call",
        event_level="info" if outcome == "success" else "warning",
        langgraph_node_id=f"llm.{context['call_purpose']}",
        context=context,
    )


async def call_responses_api(
    client: AsyncOpenAI,
    payload: Dict[str, Any],
    *,
    config: PlannerConfig,
    purpose: ResponseCallPurpose,
    replan_depth: int = 0,
) -> Tuple[Any, Dict[str, Any]]:
    """Responses API を timeout 付きで呼び出し、usage と latency を返す。"""

    started_at = monotonic()
    try:
        response = await asyncio.wait_for(
            client.responses.create(**payload),
            timeout=config.llm_timeout_seconds,
        )
    except asyncio.TimeoutError:
        observation = _build_observation(
            config,
            purpose=purpose,
            replan_depth=replan_depth,
            latency_ms=(monotonic() - started_at) * 1000,
        )
        log_response_outcome(observation, "timeout", error="TimeoutError")
        raise
    except Exception as exc:
        observation = _build_observation(
            config,
            purpose=purpose,
            replan_depth=replan_depth,
            latency_ms=(monotonic() - started_at) * 1000,
        )
        log_response_outcome(observation, "request_failure", error=exc.__class__.__name__)
        raise

    observation = _build_observation(
        config,
        purpose=purpose,
        replan_depth=replan_depth,
        latency_ms=(monotonic() - started_at) * 1000,
        response=response,
    )
    return response, observation


__all__ = [
    "AsyncOpenAI",
    "OpenAI",
    "ResponseCallOutcome",
    "ResponseCallPurpose",
    "call_responses_api",
    "create_async_openai_client",
    "create_openai_client",
    "log_response_outcome",
]
