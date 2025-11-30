# -*- coding: utf-8 -*-
"""Voyager 互換のスキルトリーを表現するデータモデル群。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


@dataclass
class SkillNode:
    """単一スキルを表現するデータクラス。"""

    identifier: str
    title: str
    description: str
    categories: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    keywords: Tuple[str, ...] = field(default_factory=tuple)
    examples: Tuple[str, ...] = field(default_factory=tuple)
    prerequisites: Tuple[str, ...] = field(default_factory=tuple)
    follow_ups: Tuple[str, ...] = field(default_factory=tuple)
    unlocked: bool = True
    usage_count: int = 0
    success_count: int = 0
    last_used_at: Optional[str] = None

    def score_for_text(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        context_tags: Tuple[str, ...] = (),
        mission_id: Optional[str] = None,
    ) -> float:
        """与えられたテキストやタグ情報との類似度を総合スコアとして算出する。"""

        normalized = text.lower()
        score = 0.0

        lowered_tags = {tag.lower().strip() for tag in self.tags if str(tag).strip()}

        if mission_id:
            lowered_mission = mission_id.lower()
            if lowered_mission == self.identifier.lower():
                score += 6.0
            elif lowered_mission in lowered_tags:
                score += 4.0

        if context_tags:
            tag_matches = sum(1 for tag in context_tags if tag.lower().strip() in lowered_tags)
            if tag_matches:
                score += min(tag_matches, 3) * 1.5

        if category:
            lowered_category = category.lower()
            if lowered_category in (value.lower() for value in self.categories):
                score += 3.0
            elif lowered_category in (value.lower() for value in self.tags):
                score += 1.5
            else:
                score -= 1.5

        for keyword in set(self.keywords):
            lowered = keyword.lower().strip()
            if lowered and lowered in normalized:
                score += 2.5

        for tag in lowered_tags:
            lowered = tag.strip()
            if lowered and lowered in normalized:
                score += 1.0

        title_lower = self.title.lower()
        if title_lower and title_lower in normalized:
            score += 1.5

        description_lower = self.description.lower()
        if description_lower and description_lower in normalized:
            score += 0.5

        for example in set(self.examples):
            lowered_example = example.lower().strip()
            if not lowered_example:
                continue
            match_count = sum(1 for token in lowered_example.split() if token in normalized)
            score += 0.2 * match_count

        return max(score, 0.0)

    def register_usage(self, *, success: bool) -> None:
        """利用実績を更新し、最終使用時刻を記録する。"""

        self.usage_count += 1
        if success:
            self.success_count += 1
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.last_used_at = timestamp

    def to_dict(self) -> Dict[str, object]:
        """JSON 永続化向けに辞書へ変換する。"""

        return {
            "id": self.identifier,
            "title": self.title,
            "description": self.description,
            "categories": list(self.categories),
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "examples": list(self.examples),
            "prerequisites": list(self.prerequisites),
            "followUps": list(self.follow_ups),
            "unlocked": self.unlocked,
            "usageCount": self.usage_count,
            "successCount": self.success_count,
            "lastUsedAt": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SkillNode":
        """辞書からスキル情報を復元するファクトリ。"""

        return cls(
            identifier=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            categories=tuple(data.get("categories", []) or []),
            tags=tuple(data.get("tags", []) or []),
            keywords=tuple(data.get("keywords", []) or []),
            examples=tuple(data.get("examples", []) or []),
            prerequisites=tuple(data.get("prerequisites", []) or []),
            follow_ups=tuple(data.get("followUps", []) or []),
            unlocked=bool(data.get("unlocked", True)),
            usage_count=int(data.get("usageCount", 0)),
            success_count=int(data.get("successCount", 0)),
            last_used_at=data.get("lastUsedAt") if data.get("lastUsedAt") else None,
        )


@dataclass(frozen=True)
class SkillMatch:
    """テキスト検索でヒットしたスキルと一致度スコアのペア。"""

    skill: SkillNode
    score: float

    @property
    def unlocked(self) -> bool:
        """対象スキルが解放済みかどうかを返す。"""

        return self.skill.unlocked


@dataclass
class SkillTree:
    """スキルノードの集合を保持するシンプルなツリーモデル。"""

    nodes: Dict[str, SkillNode] = field(default_factory=dict)
    roots: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        """JSON 出力向けの辞書を生成する。"""

        return {
            "roots": list(self.roots),
            "skills": [node.to_dict() for node in self.nodes.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SkillTree":
        """辞書構造から SkillTree を再構築する。"""

        raw_skills = data.get("skills", []) or []
        nodes: Dict[str, SkillNode] = {}
        for raw in raw_skills:
            if isinstance(raw, dict):
                node = SkillNode.from_dict(raw)
                if node.identifier:
                    nodes[node.identifier] = node
        roots_raw = data.get("roots", []) or []
        roots: List[str] = []
        for item in roots_raw:
            value = str(item).strip()
            if value:
                roots.append(value)
        return cls(nodes=nodes, roots=tuple(roots))

    def find_best_match(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        include_locked: bool = True,
        tags: Tuple[str, ...] = (),
        mission_id: Optional[str] = None,
    ) -> Optional[SkillMatch]:
        """指定テキストに最も一致するスキルを返す。"""

        best_match: Optional[SkillMatch] = None
        for node in self.nodes.values():
            if not include_locked and not node.unlocked:
                continue
            score = node.score_for_text(
                text,
                category=category,
                context_tags=tags,
                mission_id=mission_id,
            )
            if score <= 0:
                continue
            if best_match is None or score > best_match.score:
                best_match = SkillMatch(skill=node, score=score)
                continue
            if (
                best_match is not None
                and abs(score - best_match.score) < 1e-6
                and node.unlocked
                and not best_match.skill.unlocked
            ):
                best_match = SkillMatch(skill=node, score=score)
        return best_match

    def ensure_node(self, node: SkillNode) -> None:
        """与えられたスキルノードを追加または更新する。"""

        self.nodes[node.identifier] = node
        if node.identifier not in self.roots and not node.prerequisites:
            self.roots = tuple({*self.roots, node.identifier})

    def mark_unlocked(self, skill_id: str) -> None:
        """指定スキルを解放済みとして扱う。"""

        node = self.nodes.get(skill_id)
        if not node:
            return
        node.unlocked = True


__all__ = ["SkillMatch", "SkillNode", "SkillTree"]
