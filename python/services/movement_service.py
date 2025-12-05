# -*- coding: utf-8 -*-
"""移動系アクションと障壁報告を司るサービスモジュール。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple, TYPE_CHECKING

from utils import log_structured_event

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用の依存
    from actions import Actions
    from memory import Memory
    from perception_service import PerceptionCoordinator


@dataclass(frozen=True)
class MovementResult:
    """移動結果を統一フォーマットで保持するデータコンテナ。"""

    ok: bool
    destination: Tuple[int, int, int]
    error_detail: Optional[str]
    raw_response: Any


class MovementService:
    """Mineflayer 移動指示と障壁共有を集約するサービスクラス。"""

    def __init__(
        self,
        *,
        actions: "Actions",
        memory: "Memory",
        perception: "PerceptionCoordinator",
        logger: logging.Logger,
    ) -> None:
        # Actions への依存を明示し、副作用の入口を一本化する。
        self._actions = actions
        self._memory = memory
        self._perception = perception
        self._logger = logger

    async def move_to_coordinates(self, coords: Iterable[int]) -> MovementResult:
        """指定座標への移動を発行し、統一フォーマットで結果を返す。"""

        x, y, z = coords
        destination = {"x": int(x), "y": int(y), "z": int(z)}
        log_structured_event(
            self._logger,
            "moveTo command dispatched",
            event_level="trace",
            context={"event": "movement.request", "destination": destination},
        )
        resp = await self._actions.move_to(destination["x"], destination["y"], destination["z"])
        ok = bool(resp.get("ok"))
        error_detail = None if ok else resp.get("error") or "Mineflayer 側の理由不明な拒否"
        log_structured_event(
            self._logger,
            "moveTo completed",
            level=logging.INFO if ok else logging.WARNING,
            event_level="progress" if ok else "warning",
            context={
                "event": "movement.response",
                "destination": destination,
                "ok": ok,
                "error": error_detail,
            },
        )
        if ok:
            self._memory.set("last_destination", destination)
        return MovementResult(
            ok=ok,
            destination=(destination["x"], destination["y"], destination["z"]),
            error_detail=error_detail,
            raw_response=resp,
        )

    async def report_execution_barrier(self, step: str, reason: str) -> None:
        """処理継続を妨げる障壁を構造化ログとチャットで共有する。"""

        context = {"event": "movement.execution_barrier", "step": step, "reason": reason}
        log_structured_event(
            self._logger,
            "execution barrier detected",
            level=logging.WARNING,
            event_level="warn",
            context=context,
        )
        await self._perception.report_execution_barrier(step, reason)


__all__ = ["MovementService", "MovementResult"]
