# -*- coding: utf-8 -*-
"""設置/クラフト系アクションをまとめたモジュール。"""

from typing import Any, Dict, Optional

from .base import ActionModule
from .errors import ActionValidationError
from .validators import _require_non_empty_text, _require_position


class BuildingActions(ActionModule):
    """ブロック設置やクラフト操作を担当するアクション群。"""

    async def place_torch(self, position: Dict[str, int]) -> Dict[str, Any]:
        """たいまつを指定位置に設置するコマンドを送信する。"""

        payload = {"type": "placeTorch", "args": _require_position(position)}
        return await self._dispatch("placeTorch", payload)

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        """指定した種類のアイテムを手に持ち替える。"""

        args: Dict[str, Any] = {"destination": destination}
        if tool_type:
            args["toolType"] = tool_type
        if item_name:
            args["itemName"] = item_name

        payload = {"type": "equipItem", "args": args}
        return await self._dispatch("equipItem", payload)

    async def place_block(
        self,
        block: str,
        position: Dict[str, int],
        *,
        face: Optional[str] = None,
        sneak: bool = False,
    ) -> Dict[str, Any]:
        """任意のブロックを指定位置へ設置するコマンドを送信する。"""

        args: Dict[str, Any] = {
            "block": _require_non_empty_text(block, field="block"),
            "position": _require_position(position),
            "sneak": bool(sneak),
        }
        if face:
            args["face"] = face

        payload = {"type": "placeBlock", "args": args}
        return await self._dispatch("placeBlock", payload)

    async def craft_item(
        self,
        item_name: str,
        *,
        amount: int = 1,
        use_crafting_table: bool = True,
    ) -> Dict[str, Any]:
        """クラフトレシピを指定して作業台/インベントリで作成する。"""

        if amount <= 0:
            raise ActionValidationError("amount は 1 以上の整数で指定してください")

        payload = {
            "type": "craftItem",
            "args": {
                "item": _require_non_empty_text(item_name, field="item"),
                "amount": int(amount),
                "useCraftingTable": bool(use_crafting_table),
            },
        }
        return await self._dispatch("craftItem", payload)


__all__ = ["BuildingActions"]
