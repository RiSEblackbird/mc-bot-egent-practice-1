"""OpenAI クライアントと gpt-5 系パラメータ解決処理を集約するユーティリティ。"""
from __future__ import annotations

import os
from typing import Mapping, Optional

import openai

from planner_config import PlannerConfig
from utils import setup_logger

logger = setup_logger("llm.client")

# pytest でのモック差し替え互換を維持するため、旧インポートと同名のエイリアスを提供する。
AsyncOpenAI = openai.AsyncOpenAI
OpenAI = openai.OpenAI


def create_openai_client(config: PlannerConfig) -> OpenAI:
    """同期 OpenAI クライアントを設定付きで初期化する。"""

    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def create_async_openai_client(config: PlannerConfig) -> AsyncOpenAI:
    """非同期 OpenAI クライアントを設定付きで初期化する。"""

    return AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)


def is_gpt5_family(model: str) -> bool:
    """モデル名が gpt-5 系統かどうかを判定する。"""

    return model.startswith("gpt-5")


def resolve_gpt5_verbosity(config: PlannerConfig, env: Mapping[str, str] | None = None) -> Optional[str]:
    """gpt-5 系モデル向けの verbosity パラメータを環境変数から決定する。"""

    if not is_gpt5_family(config.model):
        return None

    source = env or os.environ
    raw = source.get("OPENAI_VERBOSITY")
    if not raw:
        return None

    value = raw.strip().lower()
    if value not in config.allowed_verbosity_levels:
        logger.warning(
            "OPENAI_VERBOSITY=%s はサポート対象 (low/medium/high) 外のため送信しません。",
            raw,
        )
        return None

    return value


def resolve_gpt5_reasoning_effort(config: PlannerConfig, env: Mapping[str, str] | None = None) -> Optional[str]:
    """gpt-5 系モデル向けの reasoning.effort を環境変数から決定する。"""

    if not is_gpt5_family(config.model):
        return None

    source = env or os.environ
    raw = source.get("OPENAI_REASONING_EFFORT")
    if not raw:
        return None

    value = raw.strip().lower()
    if value not in config.allowed_reasoning_effort:
        logger.warning(
            "OPENAI_REASONING_EFFORT=%s はサポート対象 (low/medium/high) 外のため送信しません。",
            raw,
        )
        return None

    return value


def resolve_request_temperature(config: PlannerConfig, env: Mapping[str, str] | None = None) -> Optional[float]:
    """LLM へ渡す温度パラメータをモデル仕様に合わせて決定する。"""

    source = env or os.environ
    raw_temperature = source.get("OPENAI_TEMPERATURE")

    if config.model in config.temperature_locked_models:
        if raw_temperature:
            logger.warning(
                "OPENAI_TEMPERATURE=%s が設定されていますが、%s は温度固定モデルのため無視します。",
                raw_temperature,
                config.model,
            )
        return None

    if not raw_temperature:
        return config.default_temperature

    try:
        requested = float(raw_temperature)
    except ValueError:
        logger.warning(
            "OPENAI_TEMPERATURE=%s は数値として解釈できません。既定値 %.2f にフォールバックします。",
            raw_temperature,
            config.default_temperature,
        )
        return config.default_temperature

    if not 0.0 <= requested <= 2.0:
        logger.warning(
            "OPENAI_TEMPERATURE=%.3f はサポート範囲 (0.0～2.0) 外のため、既定値 %.2f にフォールバックします。",
            requested,
            config.default_temperature,
        )
        return config.default_temperature

    return requested


__all__ = [
    "AsyncOpenAI",
    "OpenAI",
    "create_async_openai_client",
    "create_openai_client",
    "is_gpt5_family",
    "resolve_gpt5_reasoning_effort",
    "resolve_gpt5_verbosity",
    "resolve_request_temperature",
]
