# -*- coding: utf-8 -*-
"""Agent runtime settings aggregation and helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Tuple

from config import AgentConfig, load_agent_config
from utils import setup_logger

logger = setup_logger("agent.settings")


def _parse_float(source: Mapping[str, str], key: str, default: float) -> float:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "環境変数 %s='%s' を float へ変換できないため %s 秒を使用します。",
            key,
            raw,
            default,
        )
        return default


def _parse_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "環境変数 %s='%s' を int へ変換できないため %s を使用します。",
            key,
            raw,
            default,
        )
        return default


@dataclass(frozen=True)
class AgentRuntimeSettings:
    """Dynamic runtime knobs layered on top of AgentConfig."""

    config: AgentConfig
    status_refresh_timeout_seconds: float
    status_refresh_retry: int
    status_refresh_backoff_seconds: float
    block_eval_radius: int
    block_eval_timeout_seconds: float
    block_eval_height_delta: int
    structured_event_history_limit: int
    perception_history_limit: int
    low_food_threshold: int

    @property
    def ws_url(self) -> str:
        return self.config.ws_url

    @property
    def agent_ws_host(self) -> str:
        return self.config.agent_host

    @property
    def agent_ws_port(self) -> int:
        return self.config.agent_port

    @property
    def default_move_target(self) -> Tuple[int, int, int]:
        return self.config.default_move_target

    @property
    def default_move_target_raw(self) -> str:
        return self.config.default_move_target_raw

    @property
    def skill_library_path(self) -> str:
        return self.config.skill_library_path


def load_agent_runtime_settings(
    env: Mapping[str, str] | None = None,
) -> AgentRuntimeSettings:
    """Load AgentConfig and derived runtime settings from environment."""

    source: Mapping[str, str] = env or os.environ
    config_result = load_agent_config(source)
    config = config_result.config

    settings = AgentRuntimeSettings(
        config=config,
        status_refresh_timeout_seconds=_parse_float(
            source, "STATUS_REFRESH_TIMEOUT_SECONDS", 3.0
        ),
        status_refresh_retry=_parse_int(source, "STATUS_REFRESH_RETRY", 2),
        status_refresh_backoff_seconds=_parse_float(
            source, "STATUS_REFRESH_BACKOFF_SECONDS", 0.5
        ),
        block_eval_radius=_parse_int(source, "BLOCK_EVAL_RADIUS", 3),
        block_eval_timeout_seconds=_parse_float(
            source, "BLOCK_EVAL_TIMEOUT_SECONDS", 3.0
        ),
        block_eval_height_delta=_parse_int(source, "BLOCK_EVAL_HEIGHT_DELTA", 1),
        structured_event_history_limit=_parse_int(
            source, "STRUCTURED_EVENT_HISTORY_LIMIT", 10
        ),
        perception_history_limit=_parse_int(source, "PERCEPTION_HISTORY_LIMIT", 5),
        low_food_threshold=_parse_int(source, "LOW_FOOD_THRESHOLD", 6),
    )

    logger.info(
        "Agent configuration loaded (ws_url=%s, bind=%s:%s, default_target=%s)",
        settings.ws_url,
        settings.agent_ws_host,
        settings.agent_ws_port,
        settings.default_move_target,
    )
    return settings


DEFAULT_AGENT_RUNTIME_SETTINGS = load_agent_runtime_settings()


__all__ = [
    "AgentRuntimeSettings",
    "DEFAULT_AGENT_RUNTIME_SETTINGS",
    "load_agent_runtime_settings",
]
