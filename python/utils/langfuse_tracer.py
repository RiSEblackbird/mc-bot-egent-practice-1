# -*- coding: utf-8 -*-
"""Langfuse SDK を用いた Thought/Action/Observation トレース送信ラッパー。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from langfuse import Langfuse

from utils import setup_logger

if TYPE_CHECKING:
    from planner import ReActStep
else:  # pragma: no cover - テスト時の循環参照回避用
    ReActStep = Any  # type: ignore


class ThoughtActionObservationTracer:
    """自己対話ステップを Langfuse へ安全に転送するための薄いラッパー。"""

    def __init__(
        self,
        *,
        host: Optional[str],
        public_key: Optional[str],
        secret_key: Optional[str],
        default_tags: Sequence[str] = (),
        enabled: bool = True,
        client: Optional[Langfuse] = None,
    ) -> None:
        # Langfuse の接続設定を保持し、無効化時やキー不足時は no-op として動作させる。
        self._host = host
        self._public_key = public_key
        self._secret_key = secret_key
        self._default_tags = tuple(default_tags)
        self._explicit_enabled = enabled
        self._client = client
        # 親トレースと子スパンの関連を確実に追跡するため、run_id と observation を対応付ける。
        self._active_observations: Dict[str, Any] = {}
        self._logger = setup_logger("utils.langfuse")

    @property
    def enabled(self) -> bool:
        """トレース送信が有効かどうかを返す。"""

        has_credentials = bool(self._client or (self._public_key and self._secret_key))
        return bool(self._explicit_enabled and has_credentials)

    def _ensure_client(self) -> Optional[Langfuse]:
        """クライアントを遅延初期化し、利用可能な場合に返す。"""

        if not self.enabled:
            return None
        if self._client:
            return self._client
        try:
            self._client = Langfuse(
                host=self._host,
                public_key=self._public_key,
                secret_key=self._secret_key,
                tracing_enabled=True,
            )
            return self._client
        except Exception as exc:  # pragma: no cover - SDK 初期化失敗はログのみ
            self._logger.warning("Langfuse client initialization failed: %s", exc)
            return None

    def start_run(self, name: str, *, metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """自己対話ループ全体の親 Observation を生成する。"""

        client = self._ensure_client()
        if client is None:
            return None

        run_id = uuid.uuid4().hex
        payload: Dict[str, Any] = {
            "trace_context": {"trace_id": run_id},
            "name": name,
            "as_type": "chain",
            "input": metadata or {},
            "metadata": {"tags": list(self._default_tags)},
        }
        try:
            observation = client.start_observation(**payload)
            self._active_observations[run_id] = observation
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("Langfuse run creation skipped: %s", exc)
            return None
        return run_id

    def record_step(
        self,
        run_id: Optional[str],
        *,
        step: ReActStep,
        step_index: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """ReAct ステップ単位で Langfuse へ子 Observation を登録する。"""

        client = self._ensure_client()
        if client is None or not run_id:
            return

        payload: Dict[str, Any] = {
            "trace_context": {"trace_id": run_id},
            "name": f"self-dialogue-step-{step_index}",
            "as_type": "span",
            "input": {
                "thought": step.thought,
                "action": step.action,
                "observation": step.observation,
            },
            "metadata": {
                "tags": list(self._default_tags),
                **(metadata or {}),
            },
        }
        try:
            span = client.start_observation(**payload)
            span.end(end_time=int(datetime.now(timezone.utc).timestamp() * 1000))
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("Langfuse step creation skipped: %s", exc)

    def complete_run(
        self,
        run_id: Optional[str],
        *,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """親 Observation の完了を Langfuse へ通知する。"""

        client = self._ensure_client()
        if client is None or not run_id:
            return

        observation = self._active_observations.pop(run_id, None)
        if observation is None:
            return

        payload: Dict[str, Any] = {
            "output": outputs or {},
            "metadata": {"error": error} if error else None,
        }
        try:
            observation.update(**payload)
            observation.end(end_time=int(datetime.now(timezone.utc).timestamp() * 1000))
            client.flush()
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("Langfuse run completion skipped: %s", exc)

    def end_run(
        self,
        run_id: Optional[str],
        *,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """既存呼び出し互換のため complete_run へ委譲する。"""

        self.complete_run(run_id, outputs=metadata, error=error)


__all__ = ["ThoughtActionObservationTracer"]
