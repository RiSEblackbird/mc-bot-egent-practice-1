# -*- coding: utf-8 -*-
"""採掘時に人工物を守るための判定ロジック。"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

# --- 自然ブロックと人工ブロックのホワイトリスト／ブラックリスト ---------------------

# MineCraft で自然に生成されるブロックを最小限列挙。人工物と誤検知したくないため、
# 実運用で掘削対象とする石・土・砂系を中心にまとめている。
NATURAL_BLOCKS: Sequence[str] = (
    "minecraft:stone",
    "minecraft:diorite",
    "minecraft:andesite",
    "minecraft:granite",
    "minecraft:tuff",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:gravel",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:sandstone",
    "minecraft:red_sandstone",
    "minecraft:clay",
    "minecraft:coal_ore",
    "minecraft:iron_ore",
    "minecraft:copper_ore",
    "minecraft:lapis_ore",
    "minecraft:gold_ore",
    "minecraft:redstone_ore",
    "minecraft:diamond_ore",
    "minecraft:emerald_ore",
    "minecraft:nether_quartz_ore",
    "minecraft:nether_gold_ore",
    "minecraft:ancient_debris",
    "minecraft:oak_log",
    "minecraft:birch_log",
    "minecraft:spruce_log",
    "minecraft:dark_oak_log",
    "minecraft:jungle_log",
    "minecraft:acacia_log",
    "minecraft:oak_leaves",
    "minecraft:birch_leaves",
    "minecraft:spruce_leaves",
    "minecraft:jungle_leaves",
    "minecraft:acacia_leaves",
    "minecraft:dark_oak_leaves",
)

# 人工物は多岐に渡るため、自然ブロックに該当しないものはすべて人工扱いとし、
# NATURAL_BLOCKS を拡張していく方針とする。
NATURAL_SET = frozenset(NATURAL_BLOCKS)


def is_natural(block_id: str) -> bool:
    """自然生成ブロックであれば True を返す。"""

    return block_id in NATURAL_SET


def build_mining_mask(
    evaluations: Sequence[Dict[str, object]],
    cp_results: Sequence[Dict[str, object]],
) -> List[bool]:
    """バルク評価の結果から「安全に破壊できるブロック」のマスクを構築する。"""

    cp_map = {
        (int(result["pos"]["x"]), int(result["pos"]["y"]), int(result["pos"]["z"])): bool(
            result.get("is_player_placed", True)
        )
        for result in cp_results
    }
    mask: List[bool] = []
    for evaluation in evaluations:
        pos = evaluation["pos"]
        key = (int(pos["x"]), int(pos["y"]), int(pos["z"]))
        block_id = str(evaluation.get("block_id"))
        safe = (
            bool(evaluation.get("in_job_region", False))
            and not bool(evaluation.get("is_liquid", False))
            and not bool(evaluation.get("near_functional", False))
            and not bool(cp_map.get(key, True))
            and is_natural(block_id)
        )
        mask.append(safe)
    return mask


__all__ = ["NATURAL_BLOCKS", "is_natural", "build_mining_mask"]
