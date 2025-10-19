# -*- coding: utf-8 -*-
"""Reflexion ログをファイルへ安全に読み書きする永続化サービス。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from utils import setup_logger


class ReflectionStore:
    """反省ログを JSON ファイルに保存し、再起動後も参照できるようにする。"""

    def __init__(self, path: str | Path = "var/memory/reflections.json") -> None:
        self._path = Path(path)
        self._logger = setup_logger("memory.reflection_store")
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """保存先ディレクトリを作成し、意図しない PermissionError を防ぐ。"""

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._logger.exception("failed to prepare reflection store directory path=%s", self._path)
            raise

    def load_entries(self) -> List[Dict[str, Any]]:
        """保存済みの反省ログを読み出して返す。"""

        if not self._path.exists():
            return []

        try:
            with self._path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except json.JSONDecodeError:
            self._logger.exception("reflection store JSON is invalid path=%s", self._path)
            return []
        except FileNotFoundError:
            return []
        except Exception:
            self._logger.exception("failed to read reflection store path=%s", self._path)
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            entries = payload.get("entries", [])
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        self._logger.warning(
            "reflection store payload had unexpected structure path=%s payload_type=%s",
            self._path,
            type(payload),
        )
        return []

    def save_entries(self, entries: Iterable[Dict[str, Any]]) -> None:
        """渡された反省ログを JSON 形式で安全に書き出す。"""

        serializable: List[Dict[str, Any]] = [dict(item) for item in entries]
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fp:
                json.dump({"entries": serializable}, fp, ensure_ascii=False, indent=2)
            tmp_path.replace(self._path)
        except Exception:
            self._logger.exception("failed to persist reflection store path=%s", self._path)
            if tmp_path.exists():
                with contextlib.suppress(Exception):
                    tmp_path.unlink()
            raise
