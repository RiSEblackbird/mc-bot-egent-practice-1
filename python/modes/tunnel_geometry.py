# -*- coding: utf-8 -*-
"""Utility helpers shared across tunnel mode components."""

from __future__ import annotations

from typing import Dict, Iterator, Sequence, Tuple, Protocol


class TunnelSectionProtocol(Protocol):
    """Protocol that mirrors the TunnelSection dataclass contract."""

    width: int
    height: int


def generate_window(
    anchor: Dict[str, int],
    direction: Sequence[int],
    section: TunnelSectionProtocol,
    step: int,
    window: int,
) -> Iterator[Dict[str, int]]:
    """Yield block coordinates for the specified tunnel window.

    Args:
        anchor: Starting XYZ position for the tunnel.
        direction: Cardinal direction vector (dx, dy, dz).
        section: Section dimensions (width x height).
        step: Current frontier step offset.
        window: Forward window length to inspect.
    """

    dx, dy, dz = direction
    if dy != 0:
        raise ValueError("TunnelMode supports only horizontal directions")

    lateral = lateral_vector(direction)
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


def lateral_vector(direction: Sequence[int]) -> Tuple[int, int, int]:
    """Return the vector that spans the tunnel width."""

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


def right_vector(direction: Sequence[int]) -> Tuple[int, int, int]:
    """Return the vector that points to the right-hand wall (torch placement)."""

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


__all__ = ["generate_window", "lateral_vector", "right_vector", "TunnelSectionProtocol"]
