# -*- coding: utf-8 -*-
"""Direction inference utilities for tunnel mode."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from bridge_client import BridgeClient

from .tunnel import TunnelSection
from .tunnel_geometry import generate_window

logger = logging.getLogger(__name__)

PREVIEW_STEPS = int(os.getenv("TUNNEL_DIR_PREVIEW_STEPS", "4"))
LIQUID_PENALTY = float(os.getenv("TUNNEL_DIR_LIQUID_PENALTY", "8"))
FUNCTIONAL_PENALTY = float(os.getenv("TUNNEL_DIR_FUNCTIONAL_PENALTY", "5"))
PLAYER_PLACED_PENALTY = float(os.getenv("TUNNEL_DIR_PLAYER_PENALTY", "3"))
AIR_REWARD = float(os.getenv("TUNNEL_DIR_AIR_REWARD", "0.4"))
SOLID_REWARD = float(os.getenv("TUNNEL_DIR_SOLID_REWARD", "1.0"))

CANDIDATE_DIRECTIONS: Tuple[Tuple[int, int, int], ...] = (
    (1, 0, 0),   # East
    (-1, 0, 0),  # West
    (0, 0, 1),   # South
    (0, 0, -1),  # North
)


@dataclass(frozen=True)
class DirectionInferenceResult:
    """Inference output for tunnel direction detection."""

    direction: Tuple[int, int, int]
    score: float
    safe_blocks: int
    hazards: List[str]


def format_direction(direction: Sequence[int]) -> str:
    """Convert a direction vector into a human-readable string."""

    mapping = {
        (1, 0, 0): "east (+X)",
        (-1, 0, 0): "west (-X)",
        (0, 0, 1): "south (+Z)",
        (0, 0, -1): "north (-Z)",
    }
    return mapping.get(tuple(direction), f"{tuple(direction)}")


def infer_tunnel_direction(
    bridge: BridgeClient,
    world: str,
    anchor: Dict[str, int],
    section: TunnelSection,
    *,
    preview_steps: int | None = None,
) -> DirectionInferenceResult:
    """Infer the safest tunnel direction by sampling candidate windows."""

    samples = preview_steps or PREVIEW_STEPS
    evaluated: List[DirectionInferenceResult] = []

    for direction in CANDIDATE_DIRECTIONS:
        try:
            window_positions = list(generate_window(anchor, direction, section, 0, samples))
        except ValueError:
            continue
        if not window_positions:
            continue

        evaluations = bridge.bulk_eval(world, window_positions)
        cp_results = bridge.is_player_placed_bulk(world, window_positions)
        score, safe_blocks, hazards = _score_direction(evaluations, cp_results)
        evaluated.append(DirectionInferenceResult(direction=direction, score=score, safe_blocks=safe_blocks, hazards=hazards))

    if not evaluated:
        raise ValueError("自動推定に利用できる方向が見つかりませんでした。")

    best = max(evaluated, key=lambda result: result.score)
    if best.score <= 0:
        hazard_preview = ", ".join(best.hazards[:3]) if best.hazards else "unknown"
        raise ValueError(f"すべての方向で危険が検知されました（例: {hazard_preview}）。")

    logger.info(
        "tunnel.direction_inferred direction=%s score=%.2f safe_blocks=%d hazards=%s",
        format_direction(best.direction),
        best.score,
        best.safe_blocks,
        best.hazards,
    )
    return best


def _score_direction(
    evaluations: Sequence[Dict[str, object]],
    cp_results: Sequence[Dict[str, object]],
) -> Tuple[float, int, List[str]]:
    """Assign a heuristic score based on hazards and available space."""

    score = 0.0
    safe_blocks = 0
    hazards: List[str] = []

    for evaluation, cp in zip(evaluations, cp_results):
        if bool(evaluation.get("is_liquid")):
            score -= LIQUID_PENALTY
            hazards.append("liquid")
            continue
        if bool(evaluation.get("near_functional")):
            score -= FUNCTIONAL_PENALTY
            hazards.append("functional_block")
            continue
        if bool(cp.get("is_player_placed")):
            score -= PLAYER_PLACED_PENALTY
            hazards.append("player_placed")
            continue

        if bool(evaluation.get("is_air")):
            score += AIR_REWARD
        else:
            score += SOLID_REWARD
            safe_blocks += 1

    return score, safe_blocks, hazards


__all__ = ["DirectionInferenceResult", "infer_tunnel_direction", "format_direction"]
