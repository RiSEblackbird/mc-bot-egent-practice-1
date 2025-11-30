# -*- coding: utf-8 -*-
"""Python エージェントの設定読み込み処理。

環境変数に散らばっていた値の正規化を 1 箇所へ集約し、
テスト容易性と可観測性を高める。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List, Mapping, MutableSequence, Tuple

from utils import setup_logger

logger = setup_logger("agent.config")

_DEFAULT_WS_URL = "ws://127.0.0.1:8765"
_DEFAULT_AGENT_HOST = "0.0.0.0"
_DEFAULT_AGENT_PORT = 9000
_DEFAULT_MOVE_TARGET_RAW = "0,64,0"
_DEFAULT_MOVE_TARGET = (0, 64, 0)
_DEFAULT_SKILL_LIBRARY_PATH = "var/skills/library.json"
_DEFAULT_MINEDOJO_API_BASE_URL = "https://api.minedojo.org/v1"
_DEFAULT_MINEDOJO_CACHE_DIR = "var/cache/minedojo"
_DEFAULT_MINEDOJO_REQUEST_TIMEOUT = 10.0
_DEFAULT_MINEDOJO_SIM_ENV = "creative"
_DEFAULT_MINEDOJO_SIM_SEED = 42
_DEFAULT_MINEDOJO_SIM_MAX_STEPS = 120
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
_DEFAULT_AGENT_QUEUE_MAX_SIZE = 20
_DEFAULT_WORKER_TASK_TIMEOUT_SECONDS = 300.0
_DEFAULT_LANGSMITH_API_URL = "https://api.smith.langchain.com"
_DEFAULT_LANGSMITH_PROJECT = "mc-bot"


@dataclass(frozen=True)
class MineDojoConfig:
    """MineDojo 連携に必要な接続情報とキャッシュ設定。"""

    api_base_url: str
    api_key: str | None
    dataset_dir: str | None
    cache_dir: str
    request_timeout: float
    sim_env: str
    sim_seed: int
    sim_max_steps: int


@dataclass(frozen=True)
class LangSmithConfig:
    """LangSmith 連携で利用する接続設定とタグの集合。"""

    api_url: str
    api_key: str | None
    project: str | None
    enabled: bool
    tags: Tuple[str, ...]


@dataclass(frozen=True)
class AgentConfig:
    """エージェント本体が参照する設定値の集合。"""

    ws_url: str
    agent_host: str
    agent_port: int
    default_move_target: Tuple[int, int, int]
    default_move_target_raw: str
    skill_library_path: str
    minedojo: MineDojoConfig
    langsmith: LangSmithConfig
    llm_timeout_seconds: float  # Responses API 呼び出しを強制終了するまでの猶予秒数
    queue_max_size: int  # チャットキューの上限。0 なら無制限
    worker_task_timeout_seconds: float  # 単一チャット処理のタイムアウト猶予


@dataclass(frozen=True)
class ConfigLoadResult:
    """設定読み込み結果と警告一覧のペア。"""

    config: AgentConfig
    warnings: List[str]


def _collect_warnings(container: MutableSequence[str], items: Iterable[str]) -> None:
    """警告メッセージを順序を保ったまま蓄積するユーティリティ。"""

    for message in items:
        container.append(message)


def _parse_port(raw: str | None, default: int) -> Tuple[int, List[str]]:
    """環境変数からポート番号を安全に読み取る。"""

    warnings: List[str] = []

    if raw is None or raw.strip() == "":
        return default, warnings

    try:
        value = int(raw)
        if value <= 0 or value > 65535:
            raise ValueError
        return value, warnings
    except ValueError:
        warnings.append(f"環境変数のポート値 '{raw}' が不正なため {default} を使用します。")
        return default, warnings


def _parse_default_move_target(raw: str) -> Tuple[Tuple[int, int, int], List[str]]:
    """座標文字列を (x, y, z) タプルに変換する。"""

    warnings: List[str] = []

    try:
        parts = [int(part.strip()) for part in raw.split(",")]
        if len(parts) != 3:
            raise ValueError
        return (parts[0], parts[1], parts[2]), warnings
    except Exception:
        warnings.append(
            f"DEFAULT_MOVE_TARGET='{raw}' の解析に失敗したため {_DEFAULT_MOVE_TARGET} を採用します。"
        )
        return _DEFAULT_MOVE_TARGET, warnings


def _parse_positive_float(raw: str | None, default: float) -> Tuple[float, List[str]]:
    """正の浮動小数点数を安全に解析する。"""

    warnings: List[str] = []

    if raw is None or raw.strip() == "":
        return default, warnings

    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
        return value, warnings
    except ValueError:
        warnings.append(
            f"環境変数のタイムアウト値 '{raw}' が不正なため {default} 秒を使用します。"
        )
        return default, warnings


def _parse_positive_int(raw: str | None, default: int) -> Tuple[int, List[str]]:
    """正の整数を安全に解析する。"""

    warnings: List[str] = []

    if raw is None or raw.strip() == "":
        return default, warnings

    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value, warnings
    except ValueError:
        warnings.append(
            f"環境変数のキュー上限 '{raw}' が不正なため {default} を使用します。"
        )
        return default, warnings


def _parse_bool(raw: str | None, default: bool) -> bool:
    """真偽値の文字列表現を解釈する。"""

    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def load_agent_config(env: Mapping[str, str] | None = None) -> ConfigLoadResult:
    """プロセス環境から Python エージェントの設定を読み取る。"""

    source = env or os.environ
    warnings: List[str] = []

    ws_url = source.get("WS_URL", _DEFAULT_WS_URL)
    agent_host_raw = source.get("AGENT_WS_HOST", _DEFAULT_AGENT_HOST)
    agent_port, port_warnings = _parse_port(source.get("AGENT_WS_PORT"), _DEFAULT_AGENT_PORT)
    move_target_raw = source.get("DEFAULT_MOVE_TARGET", _DEFAULT_MOVE_TARGET_RAW)
    move_target, move_warnings = _parse_default_move_target(move_target_raw)
    skill_library_path = source.get("SKILL_LIBRARY_PATH", _DEFAULT_SKILL_LIBRARY_PATH)
    minedojo_api_base = source.get(
        "MINEDOJO_API_BASE_URL", _DEFAULT_MINEDOJO_API_BASE_URL
    )
    minedojo_api_key = source.get("MINEDOJO_API_KEY")
    minedojo_dataset_dir = source.get("MINEDOJO_DATASET_DIR")
    minedojo_cache_dir_raw = source.get("MINEDOJO_CACHE_DIR", _DEFAULT_MINEDOJO_CACHE_DIR)
    minedojo_sim_env = source.get("MINEDOJO_SIM_ENV", _DEFAULT_MINEDOJO_SIM_ENV)
    minedojo_sim_seed, sim_seed_warnings = _parse_positive_int(
        source.get("MINEDOJO_SIM_SEED"), _DEFAULT_MINEDOJO_SIM_SEED
    )
    minedojo_sim_max_steps, sim_step_warnings = _parse_positive_int(
        source.get("MINEDOJO_SIM_MAX_STEPS"), _DEFAULT_MINEDOJO_SIM_MAX_STEPS
    )
    minedojo_timeout, timeout_warnings = _parse_positive_float(
        source.get("MINEDOJO_REQUEST_TIMEOUT"), _DEFAULT_MINEDOJO_REQUEST_TIMEOUT
    )
    llm_timeout_seconds, llm_timeout_warnings = _parse_positive_float(
        source.get("LLM_TIMEOUT_SECONDS"), _DEFAULT_LLM_TIMEOUT_SECONDS
    )
    queue_max_size, queue_warnings = _parse_positive_int(
        source.get("AGENT_QUEUE_MAX_SIZE"), _DEFAULT_AGENT_QUEUE_MAX_SIZE
    )
    worker_task_timeout_seconds, worker_timeout_warnings = _parse_positive_float(
        source.get("WORKER_TASK_TIMEOUT_SECONDS"), _DEFAULT_WORKER_TASK_TIMEOUT_SECONDS
    )
    langsmith_api_url = source.get("LANGSMITH_API_URL", _DEFAULT_LANGSMITH_API_URL)
    langsmith_api_key = source.get("LANGSMITH_API_KEY")
    langsmith_project = source.get("LANGSMITH_PROJECT", _DEFAULT_LANGSMITH_PROJECT)
    langsmith_enabled = _parse_bool(source.get("LANGSMITH_ENABLED"), False)
    langsmith_tags_raw = source.get("LANGSMITH_TAGS", "")
    langsmith_tags: Tuple[str, ...] = tuple(
        token.strip()
        for token in langsmith_tags_raw.split(",")
        if token.strip()
    )

    minedojo_cache_dir = minedojo_cache_dir_raw.strip() or _DEFAULT_MINEDOJO_CACHE_DIR
    minedojo_dataset = (
        minedojo_dataset_dir.strip() if minedojo_dataset_dir and minedojo_dataset_dir.strip() else None
    )
    minedojo_api_base = (
        minedojo_api_base.strip() or _DEFAULT_MINEDOJO_API_BASE_URL
    )

    _collect_warnings(warnings, port_warnings)
    _collect_warnings(warnings, move_warnings)
    _collect_warnings(warnings, timeout_warnings)
    _collect_warnings(warnings, llm_timeout_warnings)
    _collect_warnings(warnings, queue_warnings)
    _collect_warnings(warnings, worker_timeout_warnings)
    _collect_warnings(warnings, sim_seed_warnings)
    _collect_warnings(warnings, sim_step_warnings)

    config = AgentConfig(
        ws_url=ws_url,
        agent_host=agent_host_raw if agent_host_raw.strip() else _DEFAULT_AGENT_HOST,
        agent_port=agent_port,
        default_move_target=move_target,
        default_move_target_raw=move_target_raw,
        skill_library_path=skill_library_path.strip() or _DEFAULT_SKILL_LIBRARY_PATH,
        minedojo=MineDojoConfig(
            api_base_url=minedojo_api_base,
            api_key=(minedojo_api_key.strip() if minedojo_api_key and minedojo_api_key.strip() else None),
            dataset_dir=minedojo_dataset,
            cache_dir=minedojo_cache_dir,
            request_timeout=minedojo_timeout,
            sim_env=minedojo_sim_env.strip() or _DEFAULT_MINEDOJO_SIM_ENV,
            sim_seed=minedojo_sim_seed,
            sim_max_steps=minedojo_sim_max_steps,
        ),
        langsmith=LangSmithConfig(
            api_url=langsmith_api_url.strip() or _DEFAULT_LANGSMITH_API_URL,
            api_key=(
                langsmith_api_key.strip() if langsmith_api_key and langsmith_api_key.strip() else None
            ),
            project=langsmith_project.strip() or _DEFAULT_LANGSMITH_PROJECT,
            enabled=langsmith_enabled,
            tags=langsmith_tags,
        ),
        llm_timeout_seconds=llm_timeout_seconds,
        queue_max_size=queue_max_size,
        worker_task_timeout_seconds=worker_task_timeout_seconds,
    )

    for warning in warnings:
        logger.warning(warning)

    return ConfigLoadResult(config=config, warnings=warnings)
