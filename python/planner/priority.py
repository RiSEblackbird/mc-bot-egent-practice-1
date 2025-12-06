"""Plan の優先度を簡易に管理するヘルパー。"""
from __future__ import annotations

import asyncio

from planner_config import PlannerConfig


class PlanPriorityManager:
    """LLM 連携の成功/失敗に応じて優先度を調整するシンプルな状態管理。"""

    def __init__(self, config: PlannerConfig) -> None:
        self._lock = asyncio.Lock()
        self._priority = "normal"
        self._review_threshold = config.plan_confidence_review_threshold
        self._critical_threshold = config.plan_confidence_critical_threshold

    async def mark_success(self) -> str:
        async with self._lock:
            self._priority = "normal"
            return self._priority

    async def mark_failure(self) -> str:
        async with self._lock:
            self._priority = "high"
            return self._priority

    async def snapshot(self) -> str:
        async with self._lock:
            return self._priority

    def evaluate_confidence_gate(self, plan_out: "PlanOut") -> dict:
        """Determine whether a pre-action review is required for the given plan."""

        reason = ""
        if getattr(plan_out, "blocking", False):
            return {"needs_review": False, "reason": reason}
        if getattr(plan_out, "clarification_needed", "none") != "none":
            return {"needs_review": False, "reason": reason}

        confidence = float(getattr(plan_out, "confidence", 0.0) or 0.0)
        if confidence <= self._critical_threshold:
            reason = f"confidence={confidence:.2f}"
        elif confidence <= self._review_threshold:
            reason = f"confidence={confidence:.2f}"

        return {"needs_review": bool(reason), "reason": reason}


__all__ = ["PlanPriorityManager"]
