# -*- coding: utf-8 -*-
"""チャット関連のアクションモジュール。"""

from typing import Any, Dict

from .base import ActionModule
from .validators import _require_non_empty_text


class ChatActions(ActionModule):
    """チャット通知のみを担当するアクション集合。"""

    async def say(self, text: str) -> Dict[str, Any]:
        """チャット送信コマンドを Mineflayer へ中継する。"""

        payload = {"type": "chat", "args": {"text": _require_non_empty_text(text, field="text")}}
        return await self._dispatch("chat", payload)


__all__ = ["ChatActions"]
