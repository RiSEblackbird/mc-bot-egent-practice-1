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
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class MineDojoConfig:
    """MineDojo 連携に必要な接続情報とキャッシュ設定。"""

    api_base_url: str
    api_key: str | None
    dataset_dir: str | None
    cache_dir: str
    request_timeout: float


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
    llm_timeout_seconds: float  # Responses API 呼び出しを強制終了するまでの猶予秒数


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
    minedojo_timeout, timeout_warnings = _parse_positive_float(
        source.get("MINEDOJO_REQUEST_TIMEOUT"), _DEFAULT_MINEDOJO_REQUEST_TIMEOUT
    )
    llm_timeout_seconds, llm_timeout_warnings = _parse_positive_float(
        source.get("LLM_TIMEOUT_SECONDS"), _DEFAULT_LLM_TIMEOUT_SECONDS
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
        ),
        llm_timeout_seconds=llm_timeout_seconds,
    )

    for warning in warnings:
        logger.warning(warning)

    return ConfigLoadResult(config=config, warnings=warnings)
