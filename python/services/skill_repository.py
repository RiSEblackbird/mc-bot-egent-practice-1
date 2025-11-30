# -*- coding: utf-8 -*-
"""スキルトリーの永続化と検索を担うリポジトリ層。"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional

from skills import SkillMatch, SkillNode, SkillTree
from utils import setup_logger


class SkillRepository:
    """JSON ファイルをバックエンドにしたスキル永続化クラス。"""

    def __init__(self, storage_path: str, *, seed_path: Optional[str] = None) -> None:
        self._storage_path = Path(storage_path)
        self._seed_path = Path(seed_path) if seed_path else None
        self._logger = setup_logger("skills.repository")
        self._lock = asyncio.Lock()
        self._tree: Optional[SkillTree] = None

    async def get_tree(self) -> SkillTree:
        """スキルトリー全体を読み込む。"""

        async with self._lock:
            return await self._ensure_tree()

    async def match_skill(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        tags: tuple[str, ...] = (),
        mission_id: Optional[str] = None,
    ) -> Optional[SkillMatch]:
        """計画ステップのテキストとタグ文脈から最適なスキル候補を検索する。"""

        async with self._lock:
            tree = await self._ensure_tree()
            match = tree.find_best_match(
                text,
                category=category,
                include_locked=True,
                tags=tags,
                mission_id=mission_id,
            )
            if match:
                self._logger.info(
                    "skill match text='%s' category=%s mission=%s tags=%s skill=%s score=%.2f unlocked=%s",
                    text,
                    category,
                    mission_id,
                    tags,
                    match.skill.identifier,
                    match.score,
                    match.unlocked,
                )
            else:
                self._logger.info(
                    "skill match text='%s' category=%s mission=%s tags=%s result=none",
                    text,
                    category,
                    mission_id,
                    tags,
                )
            return match

    async def record_usage(self, skill_id: str, *, success: bool) -> None:
        """スキル使用実績を更新し、永続化する。"""

        async with self._lock:
            tree = await self._ensure_tree()
            node = tree.nodes.get(skill_id)
            if not node:
                self._logger.warning("skill usage recording skipped because id=%s not found", skill_id)
                return
            node.register_usage(success=success)
            await self._persist(tree)

    async def register_skill(self, node: SkillNode) -> None:
        """新しいスキルをツリーへ登録する。"""

        async with self._lock:
            tree = await self._ensure_tree()
            tree.ensure_node(node)
            await self._persist(tree)

    async def mark_unlocked(self, skill_id: str) -> None:
        """スキルを解放済みとして扱う。"""

        async with self._lock:
            tree = await self._ensure_tree()
            if skill_id in tree.nodes:
                tree.mark_unlocked(skill_id)
                await self._persist(tree)

    async def _ensure_tree(self) -> SkillTree:
        if self._tree is None:
            self._tree = await asyncio.to_thread(self._load_tree)
        return self._tree

    def _load_tree(self) -> SkillTree:
        if not self._storage_path.exists():
            self._prepare_storage()
        try:
            with self._storage_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError:
            self._logger.error("skill storage json decode failed path=%s", self._storage_path)
            payload = {"roots": [], "skills": []}
        return SkillTree.from_dict(payload)

    def _prepare_storage(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        if self._seed_path and self._seed_path.exists():
            shutil.copy(self._seed_path, self._storage_path)
            return
        default_payload = {"roots": [], "skills": []}
        with self._storage_path.open("w", encoding="utf-8") as handle:
            json.dump(default_payload, handle, ensure_ascii=False, indent=2)

    async def _persist(self, tree: SkillTree) -> None:
        data = tree.to_dict()
        await asyncio.to_thread(self._write_tree, data)

    def _write_tree(self, data: dict) -> None:
        with self._storage_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)


__all__ = ["SkillRepository"]
