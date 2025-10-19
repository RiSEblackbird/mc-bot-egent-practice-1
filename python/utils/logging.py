# -*- coding: utf-8 -*-
"""LangGraph 連携に特化した構造化ロギングユーティリティ。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Mapping, Optional

@dataclass(frozen=True)
class StructuredLogContext:
    """ログ出力時に付与する LangGraph 関連のメタデータ。"""

    langgraph_node_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    event_level: Optional[str] = None

    def merge(
        self,
        *,
        langgraph_node_id: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        event_level: Optional[str] = None,
    ) -> "StructuredLogContext":
        """既存の文脈に新しい値をマージしたコンテキストを返す。"""

        return StructuredLogContext(
            langgraph_node_id=langgraph_node_id or self.langgraph_node_id,
            checkpoint_id=checkpoint_id or self.checkpoint_id,
            event_level=event_level or self.event_level,
        )


def _initial_context() -> StructuredLogContext:
    """ContextVar の既定値を明示的に生成する補助関数。"""

    return StructuredLogContext()


# LangGraph ノード実行時のメタデータをスレッドローカルに保持し、
# 非同期処理でも漏れなくログへ付加できるようにする。
_LOG_CONTEXT: ContextVar[StructuredLogContext] = ContextVar(
    "langgraph_log_context", default=_initial_context()
)


class StructuredLogFormatter(logging.Formatter):
    """LangGraph のメタデータを含んだ JSON ログを整形するフォーマッタ。"""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        context = _LOG_CONTEXT.get()
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event_level": getattr(record, "event_level", None) or context.event_level,
            "langgraph_node_id": getattr(record, "langgraph_node_id", None)
            or context.langgraph_node_id,
            "checkpoint_id": getattr(record, "checkpoint_id", None) or context.checkpoint_id,
        }

        structured_context = getattr(record, "structured_context", None)
        if structured_context:
            payload["context"] = _serialize_context(structured_context)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # None の値は JSON に出力せず、ログの可読性とサイズを抑える。
        compact = {key: value for key, value in payload.items() if value is not None}
        return json.dumps(compact, ensure_ascii=False, sort_keys=False)


def _serialize_context(value: Any) -> Any:
    """ログ用にコンテキスト値を再帰的にシリアライズする。"""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _serialize_context(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_context(v) for v in value]
    if isinstance(value, StructuredLogContext):
        return {
            "langgraph_node_id": value.langgraph_node_id,
            "checkpoint_id": value.checkpoint_id,
            "event_level": value.event_level,
        }
    return repr(value)


def setup_logger(name: str = "agent", level: int = logging.INFO) -> logging.Logger:
    """LangGraph メタデータを付与する JSON ロガーを構築する。"""

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(isinstance(handler.formatter, StructuredLogFormatter) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(StructuredLogFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
    return logger


def get_current_log_context() -> StructuredLogContext:
    """現在の LangGraph ログ文脈を取得する。"""

    return _LOG_CONTEXT.get()


def clear_langgraph_context() -> None:
    """ログ文脈を初期状態へ戻す。"""

    _LOG_CONTEXT.set(_initial_context())


@contextmanager
def langgraph_log_context(
    *,
    langgraph_node_id: Optional[str] = None,
    checkpoint_id: Optional[str] = None,
    event_level: Optional[str] = None,
) -> Iterator[StructuredLogContext]:
    """LangGraph ノード単位のログ文脈をスコープ限定で適用する。"""

    base = _LOG_CONTEXT.get()
    merged = base.merge(
        langgraph_node_id=langgraph_node_id,
        checkpoint_id=checkpoint_id,
        event_level=event_level,
    )
    token = _LOG_CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _LOG_CONTEXT.reset(token)


def log_structured_event(
    logger: logging.Logger,
    message: str,
    *,
    level: int = logging.INFO,
    langgraph_node_id: Optional[str] = None,
    checkpoint_id: Optional[str] = None,
    event_level: Optional[str] = None,
    context: Optional[Mapping[str, Any]] = None,
    exc_info: Any = None,
) -> None:
    """LangGraph 文脈付きで構造化ログを出力する高水準ヘルパー。"""

    extra: Dict[str, Any] = {}
    if context:
        extra["structured_context"] = context
    if langgraph_node_id:
        extra["langgraph_node_id"] = langgraph_node_id
    if checkpoint_id:
        extra["checkpoint_id"] = checkpoint_id
    if event_level:
        extra["event_level"] = event_level

    with langgraph_log_context(
        langgraph_node_id=langgraph_node_id,
        checkpoint_id=checkpoint_id,
        event_level=event_level,
    ):
        logger.log(level, message, extra=extra, exc_info=exc_info)


__all__ = [
    "StructuredLogContext",
    "clear_langgraph_context",
    "get_current_log_context",
    "langgraph_log_context",
    "log_structured_event",
    "setup_logger",
]
