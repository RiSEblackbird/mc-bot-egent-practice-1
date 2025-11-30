# -*- coding: utf-8 -*-
"""継続採掘モードの制御ロジック。"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from actions import Actions
from bridge_client import BridgeClient, BridgeError
from heuristics.artificial_filters import build_mining_mask
from .tunnel_geometry import generate_window, right_vector

logger = logging.getLogger(__name__)

TUNNEL_TORCH_INTERVAL = int(os.getenv("TUNNEL_TORCH_INTERVAL", "8"))
TUNNEL_FUNCTIONAL_NEAR_RADIUS = int(os.getenv("TUNNEL_FUNCTIONAL_NEAR_RADIUS", "4"))
TUNNEL_LIQUIDS_STOP = os.getenv("TUNNEL_LIQUIDS_STOP", "true").lower() == "true"
TUNNEL_WINDOW_LENGTH = int(os.getenv("TUNNEL_WINDOW_LENGTH", "8"))


@dataclass
class TunnelSection:
    """断面の寸法をまとめたデータクラス。"""

    width: int
    height: int


class TunnelMode:
    """継続採掘モードのメインループ。

    Anchor 座標と方向ベクトルを入力として、Paper 側の AgentBridge と連携しながら
    採掘・フロンティア前進・たいまつ設置を行う。Mineflayer へのコマンド実行は
    Actions クラスへ委譲し、HTTP 経由の環境情報取得は BridgeClient が担当する。"""

    def __init__(self, bridge: BridgeClient, actions: Actions) -> None:
        self._bridge = bridge
        self._actions = actions

    async def run(
        self,
        world: str,
        anchor: Dict[str, int],
        direction: Sequence[int],
        section: TunnelSection,
        length: int,
        owner: str,
    ) -> None:
        logger.info(
            "tunnel.start world=%s anchor=%s direction=%s section=%s length=%d owner=%s",
            world,
            anchor,
            direction,
            section,
            length,
            owner,
        )
        try:
            job = await self._call_bridge(
                self._bridge.start_mine,
                world,
                anchor,
                direction,
                {"w": section.width, "h": section.height},
                length,
                owner,
            )
        except BridgeError as exc:
            logger.error("tunnel.start_failed error=%s", exc)
            raise
        job_id = job["job_id"]
        logger.info("tunnel.job_started job_id=%s", job_id)
        step = 0
        torch_counter = 0
        try:
            while step < length:
                window_positions = list(
                    generate_window(
                        anchor,
                        direction,
                        section,
                        step,
                        min(TUNNEL_WINDOW_LENGTH, length - step),
                    )
                )
                try:
                    evaluations = await self._call_bridge(
                        self._bridge.bulk_eval, world, window_positions, job_id
                    )
                except BridgeError as exc:
                    if self._is_liquid_stop(exc):
                        logger.warning(
                            "tunnel.stop_condition job_id=%s reason=liquid_detected step=%d", job_id, step
                        )
                        break
                    raise
                cp_results = await self._call_bridge(
                    self._bridge.is_player_placed_bulk, world, window_positions
                )
                mask = build_mining_mask(evaluations, cp_results)
                stop_reason = self._detect_hard_stop(evaluations, cp_results)
                if stop_reason:
                    logger.warning(
                        "tunnel.stop_condition job_id=%s reason=%s step=%d", job_id, stop_reason, step
                    )
                    break
                to_mine = [pos for pos, allowed, info in zip(window_positions, mask, evaluations) if allowed and not info.get("is_air", False)]
                if to_mine:
                    await self._actions.mine_blocks(to_mine)
                torch_counter += 1
                if TUNNEL_TORCH_INTERVAL > 0 and torch_counter >= TUNNEL_TORCH_INTERVAL:
                    torch_pos = self._torch_position(anchor, direction, step)
                    await self._actions.place_torch(torch_pos)
                    torch_counter = 0
                step += 1
                try:
                    advance = await self._call_bridge(self._bridge.advance, job_id, 1)
                except BridgeError as exc:
                    if self._is_liquid_stop(exc):
                        logger.warning(
                            "tunnel.stop_condition job_id=%s reason=liquid_detected step=%d", job_id, step
                        )
                        break
                    raise
                if advance.get("finished"):
                    logger.info("tunnel.job_finished job_id=%s", job_id)
                    break
        finally:
            await self._call_bridge(self._bridge.stop, job_id)
            logger.info("tunnel.job_stopped job_id=%s", job_id)

    def _is_liquid_stop(self, exc: BridgeError) -> bool:
        """BridgeError が液体検知による 409 かどうかを簡潔に判定する。"""

        payload = getattr(exc, "payload", None)
        if exc.status_code == 409 and isinstance(payload, dict):
            if payload.get("error") == "liquid_detected" or payload.get("stop"):
                return True
        return False

    async def _call_bridge(self, func, *args, **kwargs):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _torch_position(
        self,
        anchor: Dict[str, int],
        direction: Sequence[int],
        step: int,
    ) -> Dict[str, int]:
        dx, _, dz = direction
        ax, ay, az = anchor["x"], anchor["y"], anchor["z"]
        right = right_vector(direction)
        base_x = ax + dx * step
        base_z = az + dz * step
        return {
            "x": base_x + right[0],
            "y": ay + 1,
            "z": base_z + right[2],
        }

    def _detect_hard_stop(
        self,
        evaluations: Sequence[Dict[str, object]],
        cp_results: Sequence[Dict[str, object]],
    ) -> str | None:
        for evaluation, cp in zip(evaluations, cp_results):
            if bool(evaluation.get("near_functional")):
                return "functional_block"
            if TUNNEL_LIQUIDS_STOP and bool(evaluation.get("is_liquid")):
                return "liquid_detected"
            if bool(cp.get("is_player_placed")):
                return "player_placed"
        return None


__all__ = ["TunnelMode", "TunnelSection"]
