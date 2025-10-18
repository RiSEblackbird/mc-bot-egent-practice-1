# -*- coding: utf-8 -*-
"""テスト用の AgentBridge スタブ。"""

from __future__ import annotations

from typing import Dict, Iterable, List


class BridgeStub:
    """TunnelMode の統合テストで利用する単純なスタブ実装。"""

    def __init__(self, length: int = 4) -> None:
        self.length = length
        self.started = False
        self.stopped = False
        self.advanced_steps = 0

    def start_mine(self, world: str, anchor: Dict[str, int], direction, section, length: int, owner: str) -> Dict[str, object]:
        self.started = True
        self.length = length
        return {
            "job_id": "stub-job",
            "frontier": {"from": anchor, "to": anchor},
        }

    def bulk_eval(self, world: str, positions: List[Dict[str, int]], job_id: str | None = None):
        result = []
        for pos in positions:
            result.append(
                {
                    "pos": pos,
                    "block_id": "minecraft:stone",
                    "is_air": False,
                    "is_liquid": False,
                    "near_functional": False,
                    "in_job_region": True,
                }
            )
        return result

    def is_player_placed_bulk(self, world: str, positions: List[Dict[str, int]], lookup_seconds: int | None = None):
        return [
            {
                "pos": pos,
                "is_player_placed": False,
                "who": None,
            }
            for pos in positions
        ]

    def advance(self, job_id: str, steps: int = 1):
        self.advanced_steps += steps
        finished = self.advanced_steps >= self.length
        return {"ok": True, "finished": finished}

    def stop(self, job_id: str):
        self.stopped = True
        return {"ok": True}


__all__ = ["BridgeStub"]
