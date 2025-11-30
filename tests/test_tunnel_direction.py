# -*- coding: utf-8 -*-
"""Tests for tunnel direction inference."""

from __future__ import annotations

import pytest

from python.modes.tunnel import TunnelSection
from python.modes.tunnel_direction import infer_tunnel_direction


class DirectionBridgeStub:
    """Bridge stub that flags hazards based on axis direction."""

    def __init__(self, anchor: dict[str, int], hazard_map: dict[str, str]) -> None:
        self.anchor = anchor
        self.hazard_map = hazard_map

    def bulk_eval(self, world: str, positions: list[dict[str, int]], job_id=None):
        del world, job_id
        return [self._build_eval_entry(pos) for pos in positions]

    def is_player_placed_bulk(self, world: str, positions: list[dict[str, int]], lookup_seconds=None):
        del world, lookup_seconds
        results = []
        for pos in positions:
            axis = self._axis_from_position(pos)
            results.append(
                {
                    "pos": pos,
                    "is_player_placed": self.hazard_map.get(axis) == "player",
                }
            )
        return results

    def _build_eval_entry(self, pos: dict[str, int]) -> dict[str, object]:
        axis = self._axis_from_position(pos)
        hazard = self.hazard_map.get(axis)
        entry = {
            "pos": pos,
            "is_air": False,
            "is_liquid": hazard == "liquid",
            "near_functional": hazard == "functional",
        }
        return entry

    def _axis_from_position(self, pos: dict[str, int]) -> str:
        if pos["x"] > self.anchor["x"]:
            return "east"
        if pos["x"] < self.anchor["x"]:
            return "west"
        if pos["z"] > self.anchor["z"]:
            return "south"
        if pos["z"] < self.anchor["z"]:
            return "north"
        return "origin"


def test_infer_direction_prefers_safe_axis():
    anchor = {"x": 0, "y": 64, "z": 0}
    # East contains liquid, North includes player-placed blocks. South/West are safe.
    hazard_map = {"east": "liquid", "north": "player"}
    bridge = DirectionBridgeStub(anchor, hazard_map)
    section = TunnelSection(width=2, height=2)

    result = infer_tunnel_direction(bridge, "world", anchor, section, preview_steps=2)

    assert result.direction in {(0, 0, 1), (-1, 0, 0)}
    assert result.score > 0
    assert "liquid" not in result.hazards


def test_infer_direction_raises_when_all_hazardous():
    anchor = {"x": 0, "y": 64, "z": 0}
    hazard_map = {
        "east": "liquid",
        "west": "functional",
        "south": "player",
        "north": "liquid",
    }
    bridge = DirectionBridgeStub(anchor, hazard_map)
    section = TunnelSection(width=2, height=2)

    with pytest.raises(ValueError):
        infer_tunnel_direction(bridge, "world", anchor, section, preview_steps=2)
