# -*- coding: utf-8 -*-
"""継続採掘モードの制御ロジック。"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from actions import Actions
from bridge_client import BridgeClient, BridgeError
from heuristics.artificial_filters import build_mining_mask

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
                    self._generate_window(anchor, direction, section, step, min(TUNNEL_WINDOW_LENGTH, length - step))
                )
                evaluations = await self._call_bridge(
                    self._bridge.bulk_eval, world, window_positions, job_id
                )
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
                advance = await self._call_bridge(self._bridge.advance, job_id, 1)
                if advance.get("finished"):
                    logger.info("tunnel.job_finished job_id=%s", job_id)
                    break
        finally:
            await self._call_bridge(self._bridge.stop, job_id)
            logger.info("tunnel.job_stopped job_id=%s", job_id)

    async def _call_bridge(self, func, *args, **kwargs):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _generate_window(
        self,
        anchor: Dict[str, int],
        direction: Sequence[int],
        section: TunnelSection,
        step: int,
        window: int,
    ) -> Iterable[Dict[str, int]]:
        dx, dy, dz = direction
        if dy != 0:
            raise ValueError("TunnelMode は水平のみ対応しています")
        lateral = self._lateral_vector(direction)
        ax, ay, az = anchor["x"], anchor["y"], anchor["z"]
        for offset in range(window):
            base_x = ax + dx * (step + offset)
            base_y = ay
            base_z = az + dz * (step + offset)
            for w in range(section.width):
                for h in range(section.height):
                    yield {
                        "x": base_x + lateral[0] * w,
                        "y": base_y + h + lateral[1] * w,
                        "z": base_z + lateral[2] * w,
                    }

    def _lateral_vector(self, direction: Sequence[int]) -> Tuple[int, int, int]:
        dx, _, dz = direction
        if dx == 1 and dz == 0:
            return (0, 0, 1)
        if dx == -1 and dz == 0:
            return (0, 0, 1)
        if dz == 1 and dx == 0:
            return (1, 0, 0)
        if dz == -1 and dx == 0:
            return (1, 0, 0)
        raise ValueError("方向ベクトルが不正です")

    def _torch_position(
        self,
        anchor: Dict[str, int],
        direction: Sequence[int],
        step: int,
    ) -> Dict[str, int]:
        dx, _, dz = direction
        ax, ay, az = anchor["x"], anchor["y"], anchor["z"]
        right = self._right_vector(direction)
        base_x = ax + dx * step
        base_z = az + dz * step
        return {
            "x": base_x + right[0],
            "y": ay + 1,
            "z": base_z + right[2],
        }

    def _right_vector(self, direction: Sequence[int]) -> Tuple[int, int, int]:
        dx, _, dz = direction
        if dx == 1 and dz == 0:
            return (0, 0, -1)
        if dx == -1 and dz == 0:
            return (0, 0, 1)
        if dz == 1 and dx == 0:
            return (-1, 0, 0)
        if dz == -1 and dx == 0:
            return (1, 0, 0)
        raise ValueError("方向ベクトルが不正です")

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
