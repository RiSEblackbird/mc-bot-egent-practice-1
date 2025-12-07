# -*- coding: utf-8 -*-
"""スキル登録・呼び出しに関するアクションモジュール。"""

from typing import Any, Dict, List, Optional

from .base import ActionModule


class SkillActions(ActionModule):
    """スキル登録や探索を担当するアクション群。"""

    async def register_skill(
        self,
        *,
        skill_id: str,
        title: str,
        description: str,
        steps: List[str],
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """スキル定義を Mineflayer 側へ登録する。"""

        payload: Dict[str, Any] = {"type": "registerSkill", "args": {
            "skillId": skill_id,
            "title": title,
            "description": description,
            "steps": steps,
        }}
        if tags:
            payload["args"]["tags"] = tags
        return await self._dispatch("registerSkill", payload)

    async def invoke_skill(
        self,
        skill_id: str,
        *,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """登録済みスキルの再生を要求する。"""

        args: Dict[str, Any] = {"skillId": skill_id}
        if context:
            args["context"] = context
        payload = {"type": "invokeSkill", "args": args}
        return await self._dispatch("invokeSkill", payload)

    async def begin_skill_exploration(
        self,
        *,
        skill_id: str,
        description: str,
        step_context: str,
    ) -> Dict[str, Any]:
        """未習得スキルの探索モードを Mineflayer へ通知する。"""

        payload = {"type": "skillExplore", "args": {
            "skillId": skill_id,
            "description": description,
            "context": step_context,
        }}
        return await self._dispatch("skillExplore", payload)


__all__ = ["SkillActions"]
