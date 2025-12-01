# -*- coding: utf-8 -*-
"""テキストベースのタスク分類と座標抽出を担当するモジュール。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple, Union

from planner import PlanArguments
from runtime.rules import (
    ACTION_TASK_RULES,
    COORD_PATTERNS,
    DETECTION_TASK_KEYWORDS,
    EQUIP_KEYWORD_RULES,
)


ArgumentsType = Union[PlanArguments, Dict[str, object], None]


@dataclass
class ActionAnalyzer:
    """LLM の自然文指示から構造化パラメータを抽出するユーティリティ。"""

    def classify_action_task(self, text: str) -> Optional[str]:
        segments = self._split_action_segments(text)
        best_category: Optional[str] = None
        best_score: Optional[Tuple[int, int, int, int]] = None

        for order_index, (category, rule) in enumerate(ACTION_TASK_RULES.items()):
            matched_keywords = set()
            longest_keyword = 0

            for segment in segments:
                matches = self._collect_keyword_matches(segment, rule.keywords)
                if not matches:
                    continue

                matched_keywords.update(matches)
                segment_longest = max(len(keyword) for keyword in matches)
                longest_keyword = max(longest_keyword, segment_longest)

            if not matched_keywords:
                continue

            score = (
                rule.priority,
                len(matched_keywords),
                longest_keyword,
                -order_index,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_category = category

        return best_category

    def classify_detection_task(self, text: str) -> Optional[str]:
        normalized = text.replace(" ", "").replace("　", "")
        for category, keywords in DETECTION_TASK_KEYWORDS.items():
            for keyword in keywords:
                if keyword in normalized:
                    return category
        return None

    def extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        for pattern in COORD_PATTERNS:
            match = pattern.search(text)
            if match:
                x, y, z = (int(match.group(i)) for i in range(1, 4))
                return x, y, z
        return None

    def extract_argument_coordinates(
        self, arguments: ArgumentsType
    ) -> Optional[Tuple[int, int, int]]:
        raw = None
        if isinstance(arguments, PlanArguments):
            raw = arguments.coordinates
        elif isinstance(arguments, dict):
            raw = arguments.get("coordinates")

        if isinstance(raw, dict):
            try:
                return (int(raw.get("x")), int(raw.get("y")), int(raw.get("z")))
            except Exception:
                return None
        return None

    def infer_equip_arguments(self, text: str) -> Optional[Dict[str, str]]:
        normalized = text.lower()
        destination = "hand"
        if "左手" in text or "オフハンド" in normalized or "off-hand" in normalized:
            destination = "off-hand"
        elif "右手" in text:
            destination = "hand"

        for keywords, mapping in EQUIP_KEYWORD_RULES:
            if any(keyword and keyword in text for keyword in keywords):
                result: Dict[str, str] = {"destination": destination}
                result.update(mapping)
                return result
            if any(keyword and keyword.lower() in normalized for keyword in keywords):
                result = {"destination": destination, **mapping}
                return result

        return None

    def infer_mining_request(self, text: str) -> Dict[str, int]:
        normalized = text.lower()
        targets: List[str] = []
        keyword_map = (
            (
                ("レッドストーン", "redstone"),
                ["redstone_ore", "deepslate_redstone_ore"],
            ),
            (("ダイヤ", "ダイア", "diamond"), ["diamond_ore", "deepslate_diamond_ore"]),
            (("ラピス", "lapis"), ["lapis_ore", "deepslate_lapis_ore"]),
            (("鉄", "iron"), ["iron_ore", "deepslate_iron_ore"]),
            (("金", "gold"), ["gold_ore", "deepslate_gold_ore"]),
            (("石炭", "coal"), ["coal_ore", "deepslate_coal_ore"]),
        )

        for keywords, ores in keyword_map:
            if any(keyword in text for keyword in keywords) or any(
                keyword in normalized for keyword in keywords
            ):
                for ore in ores:
                    if ore not in targets:
                        targets.append(ore)

        if not targets:
            targets = ["redstone_ore", "deepslate_redstone_ore"]

        scan_radius = 12
        if "広範囲" in text or "探し回" in text:
            scan_radius = 18
        elif "近く" in text or "付近" in text:
            scan_radius = 8

        max_targets = 3
        if "大量" in text or "たくさん" in text or "複数" in text:
            max_targets = 5
        elif "一つ" in text or "ひとつ" in text:
            max_targets = 1

        return {
            "targets": targets,
            "scan_radius": scan_radius,
            "max_targets": max_targets,
        }

    def _split_action_segments(self, text: str) -> Tuple[str, ...]:
        separators = r"[、。,，,\n]+"
        parts = [segment.strip() for segment in re.split(separators, text) if segment.strip()]
        if not parts:
            return (text,)
        return tuple(parts)

    def _collect_keyword_matches(
        self, text: str, keywords: Tuple[str, ...]
    ) -> List[str]:
        compact = text.replace(" ", "").replace("　", "")
        compact_lower = compact.lower()
        matches: List[str] = []
        for keyword in keywords:
            normalized_keyword = keyword.replace(" ", "").replace("　", "")
            if not normalized_keyword:
                continue
            if normalized_keyword in compact or normalized_keyword.lower() in compact_lower:
                matches.append(keyword)
        return matches


__all__ = ["ActionAnalyzer"]
