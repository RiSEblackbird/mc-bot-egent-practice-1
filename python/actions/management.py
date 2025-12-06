# -*- coding: utf-8 -*-
"""ステータス取得やロール切替などの管理系アクションモジュール。"""

from typing import Dict, Optional

from .base import ActionModule


class ManagementActions(ActionModule):
    """Bot の状態管理に関するアクション群。"""

    async def set_role(self, role_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        """LangGraph からの役割切替を Node 側へ送信する。"""

        args: Dict[str, Any] = {"roleId": role_id}
        if reason:
            args["reason"] = reason

        payload = {"type": "setAgentRole", "args": args}
        return await self._dispatch("setAgentRole", payload)

    async def gather_status(self, kind: str) -> Dict[str, Any]:
        """Mineflayer 側から位置・所持品などのステータス情報を取得する。"""

        payload = {"type": "gatherStatus", "args": {"kind": kind}}
        return await self._dispatch("gatherStatus", payload)


__all__ = ["ManagementActions"]
