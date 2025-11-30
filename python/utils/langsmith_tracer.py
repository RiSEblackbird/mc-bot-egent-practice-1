# -*- coding: utf-8 -*-
"""LangSmith SDK を用いた Thought/Action/Observation トレース送信ラッパー。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from langsmith import Client

from utils import setup_logger

if TYPE_CHECKING:
    from planner import ReActStep
else:  # pragma: no cover - テスト時の循環参照回避用
    ReActStep = Any  # type: ignore


class ThoughtActionObservationTracer:
    """自己対話ステップを LangSmith へ安全に転送するための薄いラッパー。"""

    def __init__(
        self,
        *,
        api_url: Optional[str],
        api_key: Optional[str],
        project: Optional[str],
        default_tags: Sequence[str] = (),
        enabled: bool = True,
        client: Optional[Client] = None,
    ) -> None:
        # LangSmith の接続設定を保持し、無効化フラグや API キー欠如時は完全に no-op とする。
        self._api_url = api_url
        self._api_key = api_key
        self._project = project
        self._default_tags = tuple(default_tags)
        self._explicit_enabled = enabled
        self._client = client
        self._logger = setup_logger("utils.langsmith")

    @property
    def enabled(self) -> bool:
        """トレース送信が有効かどうかを返す。"""

        return bool(self._explicit_enabled and (self._client or self._api_key))

    def _ensure_client(self) -> Optional[Client]:
        """クライアントを遅延初期化し、利用可能な場合に返す。"""

        if not self.enabled:
            return None
        if self._client:
            return self._client
        try:
            self._client = Client(
                api_key=self._api_key,
                api_url=self._api_url,
                default_project=self._project,
            )
            return self._client
        except Exception as exc:  # pragma: no cover - SDK 初期化失敗はログのみ
            self._logger.warning("LangSmith client initialization failed: %s", exc)
            return None

    def start_run(self, name: str, *, metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """自己対話ループ全体の親 Run を生成する。"""

        client = self._ensure_client()
        if client is None:
            return None

        run_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "id": run_id,
            "name": name,
            "run_type": "chain",
            "inputs": metadata or {},
            "tags": list(self._default_tags),
            "start_time": datetime.now(timezone.utc),
        }
        try:
            client.create_run(**payload)
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("LangSmith run creation skipped: %s", exc)
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
        """ReAct ステップ単位で LangSmith へ子 Run を登録する。"""

        client = self._ensure_client()
        if client is None or not run_id:
            return

        payload: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "name": f"self-dialogue-step-{step_index}",
            "run_type": "llm",
            "inputs": {
                "thought": step.thought,
                "action": step.action,
                "observation": step.observation,
            },
            "tags": list(self._default_tags),
            "parent_run_id": run_id,
            "extra": metadata or {},
            "start_time": datetime.now(timezone.utc),
        }
        try:
            client.create_run(**payload)
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("LangSmith step creation skipped: %s", exc)

    def complete_run(
        self,
        run_id: Optional[str],
        *,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """親 Run の完了を LangSmith へ通知する。"""

        client = self._ensure_client()
        if client is None or not run_id:
            return

        payload: Dict[str, Any] = {
            "end_time": datetime.now(timezone.utc),
            "outputs": outputs or {},
            "error": error,
        }
        try:
            client.update_run(run_id, **payload)
        except Exception as exc:  # pragma: no cover - 通信例外は通知のみ
            self._logger.warning("LangSmith run completion skipped: %s", exc)


__all__ = ["ThoughtActionObservationTracer"]
