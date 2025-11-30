# -*- coding: utf-8 -*-
"""TunnelMode のユニットテストと簡易統合テスト。"""

from __future__ import annotations

import asyncio
import unittest

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "python"))

from bridge_client import BridgeError
from heuristics.artificial_filters import build_mining_mask
from modes import tunnel
from modes.tunnel import TunnelMode, TunnelSection
from tests.stubs.bridge_stub import BridgeStub


class FakeActions:
    """Actions の非同期メソッドを記録するテストダブル。"""

    def __init__(self) -> None:
        self.mined: list[list[dict[str, int]]] = []
        self.torches: list[dict[str, int]] = []

    async def mine_blocks(self, positions):  # type: ignore[no-untyped-def]
        self.mined.append(list(positions))
        return {"ok": True}

    async def place_torch(self, position):  # type: ignore[no-untyped-def]
        self.torches.append(dict(position))
        return {"ok": True}


class MiningMaskTest(unittest.TestCase):
    def test_build_mining_mask_filters_conditions(self) -> None:
        evaluations = [
            {
                "pos": {"x": 0, "y": 0, "z": 0},
                "block_id": "minecraft:stone",
                "is_liquid": False,
                "near_functional": False,
                "in_job_region": True,
            },
            {
                "pos": {"x": 1, "y": 0, "z": 0},
                "block_id": "minecraft:stone",
                "is_liquid": True,
                "near_functional": False,
                "in_job_region": True,
            },
            {
                "pos": {"x": 2, "y": 0, "z": 0},
                "block_id": "minecraft:oak_planks",
                "is_liquid": False,
                "near_functional": False,
                "in_job_region": True,
            },
        ]
        cp_results = [
            {"pos": {"x": 0, "y": 0, "z": 0}, "is_player_placed": False},
            {"pos": {"x": 1, "y": 0, "z": 0}, "is_player_placed": False},
            {"pos": {"x": 2, "y": 0, "z": 0}, "is_player_placed": True},
        ]
        mask = build_mining_mask(evaluations, cp_results)
        self.assertEqual(mask, [True, False, False])


class TunnelModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_tunnel_progress_and_torch(self) -> None:
        bridge = BridgeStub(length=3)
        actions = FakeActions()
        tunnel.TUNNEL_TORCH_INTERVAL = 2
        mode = TunnelMode(bridge, actions)
        await mode.run(
            world="world",
            anchor={"x": 0, "y": 64, "z": 0},
            direction=(1, 0, 0),
            section=TunnelSection(width=2, height=2),
            length=3,
            owner="Tester",
        )
        self.assertTrue(bridge.started)
        self.assertTrue(bridge.stopped)
        self.assertGreaterEqual(len(actions.mined), 1)
        self.assertGreaterEqual(len(actions.torches), 1)

    async def test_tunnel_stops_on_liquid_conflict(self) -> None:
        class LiquidBridgeStub(BridgeStub):
            def __init__(self) -> None:
                super().__init__(length=2)
                self.liquid_reported = False

            def bulk_eval(self, world, positions, job_id=None):  # type: ignore[override]
                if not self.liquid_reported:
                    self.liquid_reported = True
                    raise BridgeError(
                        "liquid detected",
                        status_code=409,
                        payload={
                            "error": "liquid_detected",
                            "stop": True,
                            "stop_pos": {"x": 0, "y": 64, "z": 0},
                        },
                    )
                return super().bulk_eval(world, positions, job_id)

        bridge = LiquidBridgeStub()
        actions = FakeActions()
        mode = TunnelMode(bridge, actions)
        await mode.run(
            world="world",
            anchor={"x": 0, "y": 64, "z": 0},
            direction=(1, 0, 0),
            section=TunnelSection(width=1, height=2),
            length=2,
            owner="Tester",
        )
        self.assertTrue(bridge.started)
        self.assertTrue(bridge.stopped)
        self.assertLessEqual(bridge.advanced_steps, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
