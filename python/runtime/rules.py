# -*- coding: utf-8 -*-
"""エージェントの共通ルール定数を集約するモジュール。

座標抽出パターンや行動カテゴリのルールはエージェントの振る舞いに直結する。
単一箇所へまとめることで、Mineflayer 側の挙動調整やキーワード追加を安全に
共有でき、AgentOrchestrator からも明示的に参照できるようにする。
"""

from __future__ import annotations

import re
from re import Pattern
from typing import Dict, Tuple

from runtime.action_graph import ActionTaskRule

# プレイヤーが送りがちな座標表記の揺れを吸収するための正規表現パターン群。
COORD_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)"),
    re.compile(
        r"XYZ[:：]?\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)",
    ),
    re.compile(
        r"X\s*[:＝=]?\s*(-?\d+)[^\d-]+Y\s*[:＝=]?\s*(-?\d+)[^\d-]+Z\s*[:＝=]?\s*(-?\d+)",
        re.IGNORECASE,
    ),
)

# 行動系タスクをカテゴリごとに整理するための分類ルール。
ACTION_TASK_RULES: Dict[str, ActionTaskRule] = {
    "move": ActionTaskRule(
        keywords=(
            "移動",
            "向かう",
            "歩く",
            "進む",
            "到達",
            "到着",
            "目指す",
        ),
        hints=(
            "段差",
            "足場",
            "はしご",
            "登",
            "降",
            "経路",
            "通路",
            "迂回",
            "高さ",
        ),
        label="指定地点への移動",
        implemented=True,
        priority=15,
    ),
    "move_to_player": ActionTaskRule(
        keywords=(
            "移動",
            "向かう",
            "歩く",
            "到達",
            "合流",
            "向かいます",
        ),
        hints=(
            "段差",
            "足場",
            "明るさ",
            "湧き",
            "経路",
            "迂回",
        ),
        label="プレイヤー座標への移動",
        implemented=True,
        priority=16,
    ),
    "mine": ActionTaskRule(
        keywords=(
            "採掘",
            "採鉱",
            "鉱石",
            "掘る",
            "ブランチ",
        ),
        label="採掘作業",
        priority=10,
    ),
    "farm": ActionTaskRule(
        keywords=(
            "収穫",
            "畑",
            "農",
            "植え",
            "耕す",
        ),
        label="農作業",
    ),
    "craft": ActionTaskRule(
        keywords=(
            "クラフト",
            "作成",
            "作る",
            "製作",
        ),
        label="クラフト処理",
    ),
    "follow": ActionTaskRule(
        keywords=(
            "ついて",
            "追尾",
            "同行",
            "付いて",
        ),
        label="追従行動",
    ),
    "build": ActionTaskRule(
        keywords=(
            "建て",
            "建築",
            "建造",
            "組み立て",
        ),
        label="建築作業",
    ),
    "fight": ActionTaskRule(
        keywords=(
            "戦う",
            "迎撃",
            "戦闘",
            "倒す",
            "守る",
        ),
        label="戦闘行動",
    ),
    "equip": ActionTaskRule(
        keywords=(
            "装備",
            "持ち替え",
            "手に持つ",
            "構える",
        ),
        label="装備持ち替え",
        implemented=True,
        priority=20,
    ),
    "deliver": ActionTaskRule(
        keywords=(
            "渡す",
            "届ける",
            "受け渡し",
            "納品",
        ),
        label="アイテム受け渡し",
    ),
    "storage": ActionTaskRule(
        keywords=(
            "チェスト",
            "収納",
            "保管",
            "しまう",
        ),
        label="保管操作",
    ),
    "gather": ActionTaskRule(
        keywords=(
            "集め",
            "確保",
            "調達",
            "集める",
        ),
        label="素材収集",
    ),
}

# ステータス報告系タスクを表すキーワードの分類表。
DETECTION_TASK_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "player_position": (
        "現在位置",
        "現在地",
        "座標",
        "座標を報告",
        "XYZ",
    ),
    "inventory_status": (
        "所持品",
        "インベントリ",
        "持ち物",
        "手持ち",
        "アイテム一覧",
    ),
    "general_status": (
        "状態を報告",
        "状況を報告",
        "体力の状況",
        "満腹度",
    ),
}

# 装備切り替えの推測に使うキーワード辞書。tool_type / item_name を手掛かりにする。
EQUIP_KEYWORD_RULES: Tuple[Tuple[Tuple[str, ...], Dict[str, str]], ...] = (
    (("ツルハシ", "ピッケル", "pickaxe"), {"tool_type": "pickaxe"}),
    (("剣", "ソード", "sword"), {"tool_type": "sword"}),
    (("斧", "おの", "axe"), {"tool_type": "axe"}),
    (("シャベル", "スコップ", "shovel", "spade"), {"tool_type": "shovel"}),
    (("クワ", "鍬", "hoe"), {"tool_type": "hoe"}),
    (("盾", "シールド", "shield"), {"tool_type": "shield"}),
    (("松明", "たいまつ", "torch"), {"item_name": "torch"}),
)

# ツルハシごとのランク序列。採掘可否判定で使用する。
PICKAXE_TIER_BY_NAME: Dict[str, int] = {
    "wooden_pickaxe": 1,
    "golden_pickaxe": 1,
    "stone_pickaxe": 2,
    "iron_pickaxe": 3,
    "diamond_pickaxe": 4,
    "netherite_pickaxe": 5,
}

# 各鉱石がドロップするために必要な最小ツルハシランク。
ORE_PICKAXE_REQUIREMENTS: Dict[str, int] = {
    "diamond_ore": 3,
    "deepslate_diamond_ore": 3,
    "redstone_ore": 3,
    "deepslate_redstone_ore": 3,
    "gold_ore": 3,
    "deepslate_gold_ore": 3,
    "lapis_ore": 2,
    "deepslate_lapis_ore": 2,
    "iron_ore": 2,
    "deepslate_iron_ore": 2,
    "coal_ore": 1,
    "deepslate_coal_ore": 1,
}

__all__ = [
    "COORD_PATTERNS",
    "ACTION_TASK_RULES",
    "DETECTION_TASK_KEYWORDS",
    "EQUIP_KEYWORD_RULES",
    "PICKAXE_TIER_BY_NAME",
    "ORE_PICKAXE_REQUIREMENTS",
]
