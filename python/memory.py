# -*- coding: utf-8 -*-
"""LLM 連携で利用する簡易オンメモリ辞書と Reflexion 記録。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional

from utils import setup_logger
from services.reflection_store import ReflectionStore


def _now_iso() -> str:
    """UTC タイムスタンプを ISO8601 形式で生成するヘルパー。"""

    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReflectionLogEntry:
    """失敗ステップと再試行結果を整理するためのログレコード。"""

    id: str
    task_signature: str
    failed_step: str
    failure_reason: str
    improvement: str
    retry_result: str
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON へ書き出す際に安全な辞書へ変換する。"""

        return {
            "id": self.id,
            "task_signature": self.task_signature,
            "failed_step": self.failed_step,
            "failure_reason": self.failure_reason,
            "improvement": self.improvement,
            "retry_result": self.retry_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ReflectionLogEntry":
        """永続化済みの辞書からログレコードを復元する。"""

        metadata = payload.get("metadata")
        meta_dict = metadata if isinstance(metadata, dict) else {}
        return cls(
            id=str(payload.get("id") or uuid.uuid4()),
            task_signature=str(payload.get("task_signature") or ""),
            failed_step=str(payload.get("failed_step") or ""),
            failure_reason=str(payload.get("failure_reason") or ""),
            improvement=str(payload.get("improvement") or ""),
            retry_result=str(payload.get("retry_result") or "pending"),
            created_at=str(payload.get("created_at") or _now_iso()),
            updated_at=str(payload.get("updated_at") or _now_iso()),
            metadata=meta_dict,
        )


class Memory:
    """辞書ベースの記憶装置と Reflexion ログを統合した管理コンポーネント。"""

    def __init__(self, reflection_store: Optional[ReflectionStore] = None) -> None:
        self.kv: Dict[str, Any] = {}
        self.logger = setup_logger("memory")
        self._reflection_store = reflection_store or ReflectionStore()
        self._reflection_logs: Dict[str, ReflectionLogEntry] = {}
        self._active_reflection_id: Optional[str] = None
        self._load_reflections()

    def get(self, key: str, default=None):
        value = self.kv.get(key, default)
        self.logger.debug("memory get key=%s value=%s", key, value)
        return value

    def set(self, key: str, value):
        self.logger.info("memory set key=%s value=%s", key, value)
        self.kv[key] = value

    # ------------------------------------------------------------------
    # Reflexion ログ関連の操作
    # ------------------------------------------------------------------

    def _load_reflections(self) -> None:
        """永続化された反省ログを読み込みメモリ上へ復元する。"""

        try:
            entries = self._reflection_store.load_entries()
        except Exception:
            self.logger.exception("failed to load reflections from store")
            return

        for item in entries:
            try:
                entry = ReflectionLogEntry.from_dict(item)
            except Exception:
                self.logger.exception("skip invalid reflection entry payload=%s", item)
                continue
            self._reflection_logs[entry.id] = entry

    def _persist_reflections(self) -> None:
        """現在の反省ログ一覧をストレージへ保存する。"""

        try:
            self._reflection_store.save_entries(
                entry.to_dict() for entry in self._reflection_logs.values()
            )
        except Exception:
            self.logger.exception("failed to persist reflections")

    def derive_task_signature(self, step: str) -> str:
        """ステップ文を正規化し、反省ログのカテゴリキーとして利用する。"""

        normalized = " ".join(str(step or "").split())
        return normalized or "unknown"

    def begin_reflection(
        self,
        *,
        task_signature: str,
        failed_step: str,
        failure_reason: str,
        improvement: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReflectionLogEntry:
        """新しい反省ログを生成し、再試行フェーズの開始を記録する。"""

        entry_id = str(uuid.uuid4())
        timestamp = _now_iso()
        entry = ReflectionLogEntry(
            id=entry_id,
            task_signature=task_signature,
            failed_step=failed_step,
            failure_reason=failure_reason,
            improvement=improvement,
            retry_result="pending",
            created_at=timestamp,
            updated_at=timestamp,
            metadata=dict(metadata or {}),
        )
        self._reflection_logs[entry_id] = entry
        self._active_reflection_id = entry_id
        self._persist_reflections()
        self.logger.info(
            "reflection session opened id=%s task_signature=%s",
            entry_id,
            task_signature,
        )
        return entry

    def finalize_pending_reflection(
        self,
        *,
        outcome: str,
        detail: Optional[str] = None,
    ) -> Optional[ReflectionLogEntry]:
        """進行中の反省ログへ結果を記録し、必要に応じて完了扱いへする。"""

        if not self._active_reflection_id:
            return None

        entry = self._reflection_logs.get(self._active_reflection_id)
        if not entry:
            self._active_reflection_id = None
            return None

        label = outcome.strip() if outcome else "unknown"
        if detail:
            label = f"{label}: {detail}"

        entry.retry_result = label
        entry.updated_at = _now_iso()
        if label != "pending":
            self._active_reflection_id = None
        self._persist_reflections()
        self.logger.info(
            "reflection session finalized id=%s result=%s",
            entry.id,
            entry.retry_result,
        )
        return entry

    def list_reflections(
        self,
        *,
        limit: Optional[int] = None,
        task_signature: Optional[str] = None,
    ) -> List[ReflectionLogEntry]:
        """保存済み反省ログを新しい順に取得するユーティリティ。"""

        entries = sorted(
            self._reflection_logs.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        if task_signature:
            entries = [entry for entry in entries if entry.task_signature == task_signature]
        if limit is not None:
            entries = entries[:limit]
        return entries

    def build_reflection_context(self, *, limit: int = 3) -> List[Dict[str, Any]]:
        """plan() へ渡すための反省ログ要約を生成する。"""

        summary: List[Dict[str, Any]] = []
        for entry in self.list_reflections(limit=limit):
            summary.append(
                {
                    "failed_step": entry.failed_step,
                    "failure_reason": entry.failure_reason,
                    "improvement": entry.improvement,
                    "retry_result": entry.retry_result,
                    "updated_at": entry.updated_at,
                }
            )
        return summary

    def export_reflections_for_prompt(
        self,
        *,
        task_signature: str,
        limit: int = 3,
    ) -> List[Dict[str, str]]:
        """Reflexion プロンプト生成用に最低限のフィールドへ整形する。"""

        logs = []
        for entry in self.list_reflections(limit=limit, task_signature=task_signature):
            logs.append(
                {
                    "failed_step": entry.failed_step,
                    "improvement": entry.improvement,
                    "retry_result": entry.retry_result,
                }
            )
        return logs

    def get_active_reflection_prompt(self) -> Optional[str]:
        """現在進行中の再試行で利用すべき改善提案プロンプトを返す。"""

        if self._active_reflection_id:
            entry = self._reflection_logs.get(self._active_reflection_id)
            if entry:
                return entry.improvement
        recent = self.list_reflections(limit=1)
        if recent:
            return recent[0].improvement
        return None
