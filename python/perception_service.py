# -*- coding: utf-8 -*-
"""Perception とステータス関連のユーティリティ群。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from bridge_client import BridgeError
from planner import (
    BarrierNotificationError,
    BarrierNotificationTimeout,
    compose_barrier_notification,
)

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from agent import AgentOrchestrator
    from bridge_role_handler import BridgeRoleHandler


class PerceptionCoordinator:
    """AgentOrchestrator から抽出した認識系の補助ロジック。"""

    def __init__(
        self,
        agent: "AgentOrchestrator",
        *,
        bridge_roles: "BridgeRoleHandler | None" = None,
    ) -> None:
        # AgentOrchestrator からの副作用を明示し、テスト注入も容易にする。
        self._agent = agent
        self._logger = agent.logger
        self._bridge_roles = bridge_roles or getattr(agent, "_bridge_roles", None)

    def collect_recent_mineflayer_context(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self._agent.status_service.collect_recent_mineflayer_context()

    def build_perception_snapshot(self, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        return self._agent.status_service.build_perception_snapshot(extra)

    def ingest_perception_snapshot(self, snapshot: Dict[str, Any], *, source: str) -> None:
        self._agent.status_service.ingest_perception_snapshot(snapshot, source=source)

    async def collect_block_evaluations(self) -> None:
        agent = self._agent
        detail = agent.memory.get("player_pos_detail") or {}
        try:
            x = int(detail.get("x"))
            y = int(detail.get("y"))
            z = int(detail.get("z"))
        except Exception:
            agent.logger.info(
                "skip block evaluation because player position detail is unavailable"
            )
            return

        world = str(detail.get("dimension") or detail.get("world") or "world")
        positions: List[Dict[str, int]] = []
        radius = agent.settings.block_eval_radius
        height_delta = agent.settings.block_eval_height_delta
        for dx in range(-radius, radius + 1):
            for dy in range(-height_delta, height_delta + 1):
                for dz in range(-radius, radius + 1):
                    positions.append({"x": x + dx, "y": y + dy, "z": z + dz})

        if not self._bridge_roles:
            agent.logger.warning(
                "skip block evaluation because bridge role handler is unavailable"
            )
            return

        loop = asyncio.get_running_loop()
        try:
            evaluations = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._bridge_roles.bridge_client.bulk_eval(world, positions)
                    ),
                timeout=agent.settings.block_eval_timeout_seconds,
            )
        except (asyncio.TimeoutError, BridgeError) as exc:
            agent.logger.warning(
                "block evaluation failed world=%s error=%s", world, exc
            )
            return
        except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
            agent.logger.exception("unexpected error during block evaluation", exc_info=exc)
            return

        summary = self._summarize_block_evaluations(evaluations)
        agent.memory.set("block_evaluation", summary)

    async def report_execution_barrier(self, step: str, reason: str) -> None:
        agent = self._agent
        agent.logger.warning(
            "execution barrier detected step='%s' reason='%s'",
            step,
            reason,
        )
        message = await self._compose_barrier_message(step, reason)
        await agent.actions.say(message)

    def _summarize_block_evaluations(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """BridgeClient.bulk_eval の結果を安全に集計する。"""

        hazard_counts: Dict[str, int] = {}
        safe_blocks = 0
        lava_depths: List[int] = []
        for block in evaluations:
            block_type = str(block.get("type") or "")
            if not block_type:
                continue
            hazard = str(block.get("hazard") or "none")
            if hazard != "none":
                hazard_counts[hazard] = hazard_counts.get(hazard, 0) + 1
                if hazard == "lava":
                    try:
                        lava_depths.append(int(block.get("depth", 0)))
                    except Exception:
                        continue
            else:
                safe_blocks += 1

        summary: Dict[str, Any] = {
            "hazards": hazard_counts,
            "safe_blocks": safe_blocks,
        }
        if lava_depths:
            summary["max_lava_depth"] = max(lava_depths)
        return summary

    async def _compose_barrier_message(self, step: str, reason: str) -> str:
        agent = self._agent
        try:
            current_role = self._bridge_roles.current_role if self._bridge_roles else "generalist"
            context = agent.status_service.build_context_snapshot(
                current_role_id=current_role
            )
            backlog_size = 0
            chat_queue = getattr(agent, "chat_queue", None)
            if chat_queue is not None:
                backlog_size = getattr(chat_queue, "backlog_size", 0) or 0
            else:
                agent.logger.warning(
                    "chat_queue is unavailable while composing barrier message; using backlog_size=0"
                )
            context.update({"queue_backlog": backlog_size})
            llm_message = await compose_barrier_notification(step, reason, context)
            if llm_message:
                agent.logger.info(
                    "barrier message composed via LLM step='%s' message='%s'",
                    step,
                    llm_message,
                )
                return llm_message
        except BarrierNotificationTimeout as exc:
            agent.logger.warning(
                "barrier message generation timed out step='%s': %s",
                step,
                exc,
            )
        except BarrierNotificationError as exc:
            agent.logger.warning(
                "barrier message generation failed step='%s': %s",
                step,
                exc,
            )
        except Exception as exc:
            agent.logger.warning(
                "barrier message generation failed step='%s': %s",
                step,
                exc,
            )

        short_step = self._shorten_text(step, limit=40)
        short_reason = self._shorten_text(reason, limit=60)
        return f"手順「{short_step}」で問題が発生しました: {short_reason}"

    @staticmethod
    def _shorten_text(text: str, *, limit: int) -> str:
        text = text.strip()
        return text if len(text) <= limit else f"{text[:limit]}…"


__all__ = ["PerceptionCoordinator"]
