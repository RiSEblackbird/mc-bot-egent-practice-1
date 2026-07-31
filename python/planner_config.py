"""Planner モジュール向けの設定読み込みと正規化ロジックを集約する。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

from config import load_agent_config
from utils import setup_logger

logger = setup_logger("planner.config")

OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_REASONING_EFFORT = "high"
OPENAI_VERBOSITY = "low"

_REMOVED_MODEL_ENV_VARS = (
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_VERBOSITY",
    "OPENAI_TEMPERATURE",
)


@dataclass(frozen=True)
class PlannerConfig:
    """プランナーで利用する OpenAI 関連設定と閾値を保持する。"""

    plan_confidence_review_threshold: float = 0.55
    plan_confidence_critical_threshold: float = 0.35
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    llm_timeout_seconds: float = 30.0
    model: str = field(default=OPENAI_MODEL, init=False)
    reasoning_effort: str = field(default=OPENAI_REASONING_EFFORT, init=False)
    verbosity: str = field(default=OPENAI_VERBOSITY, init=False)


def _normalize_base_url(raw_base_url: Optional[str]) -> Optional[str]:
    """OPENAI_BASE_URL を安全に正規化する。"""

    if not raw_base_url:
        return None

    normalized_base_url = raw_base_url.strip()
    if not normalized_base_url:
        return None

    parsed_url = urlparse(normalized_base_url)
    if parsed_url.scheme:
        return normalized_base_url

    auto_prefixed_url = f"http://{normalized_base_url}"
    parsed_auto_prefixed = urlparse(auto_prefixed_url)
    if not parsed_auto_prefixed.scheme:
        raise ValueError(
            "OPENAI_BASE_URL にはスキームを含めた完全な URL を指定してください (例: https://api.openai.com/v1)"
        )

    logger.warning(
        "OPENAI_BASE_URL にスキームが指定されていなかったため http:// を補完しました。 期待される形式の例: https://api.openai.com/v1"
    )
    return auto_prefixed_url


def _parse_threshold(raw_value: Optional[str], default: float, *, env_key: str) -> float:
    """環境変数からしきい値を安全に解析し、無効値は既定へフォールバックする。"""

    if raw_value is None:
        return default

    try:
        parsed = float(raw_value)
        return parsed
    except ValueError:
        logger.warning(
            "%s=%s は数値として解釈できません。既定値 %.2f を採用します。",
            env_key,
            raw_value,
            default,
        )
        return default


def load_planner_config(env: Mapping[str, str] | None = None) -> PlannerConfig:
    """環境変数と config.load_agent_config の結果を統合した PlannerConfig を生成する。"""

    if env is None:
        load_dotenv()
        source = os.environ
    else:
        source = env

    removed_keys = [key for key in _REMOVED_MODEL_ENV_VARS if key in source]
    if removed_keys:
        joined_keys = ", ".join(removed_keys)
        raise ValueError(
            f"廃止されたモデル設定環境変数を削除してください: {joined_keys}. "
            f"Responses API は {OPENAI_MODEL} / reasoning={OPENAI_REASONING_EFFORT} / "
            f"verbosity={OPENAI_VERBOSITY} に固定されています。"
        )

    agent_config_result = load_agent_config(source)
    for warning in agent_config_result.warnings:
        logger.warning("agent config warning: %s", warning)

    base_url = _normalize_base_url(source.get("OPENAI_BASE_URL"))
    api_key = source.get("OPENAI_API_KEY")

    review_threshold = _parse_threshold(
        source.get("PLAN_CONFIDENCE_REVIEW_THRESHOLD"),
        0.55,
        env_key="PLAN_CONFIDENCE_REVIEW_THRESHOLD",
    )
    critical_threshold = _parse_threshold(
        source.get("PLAN_CONFIDENCE_CRITICAL_THRESHOLD"),
        0.35,
        env_key="PLAN_CONFIDENCE_CRITICAL_THRESHOLD",
    )

    return PlannerConfig(
        plan_confidence_review_threshold=review_threshold,
        plan_confidence_critical_threshold=critical_threshold,
        base_url=base_url,
        api_key=api_key,
        llm_timeout_seconds=agent_config_result.config.llm_timeout_seconds,
    )


__all__ = [
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_VERBOSITY",
    "PlannerConfig",
    "load_planner_config",
]
