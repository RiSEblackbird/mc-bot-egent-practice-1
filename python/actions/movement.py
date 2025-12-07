# -*- coding: utf-8 -*-
"""移動・追従・戦闘に関するアクションモジュール。"""

from typing import Any, Dict

from .base import ActionModule
from .errors import ActionValidationError
from .validators import _require_non_empty_text, _require_position


class MovementActions(ActionModule):
    """経路移動や追尾など、Bot のポジション操作を扱う。"""

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """指定座標への移動を要求するコマンドを送信する。"""

        payload = {"type": "moveTo", "args": _require_position({"x": x, "y": y, "z": z})}
        return await self._dispatch("moveTo", payload)

    async def follow_player(
        self,
        target_name: str,
        *,
        stop_distance: int = 2,
        maintain_line_of_sight: bool = True,
    ) -> Dict[str, Any]:
        """指定プレイヤーを追従するコマンドを送信する。"""

        payload = {
            "type": "followPlayer",
            "args": {
                "target": _require_non_empty_text(target_name, field="target"),
                "stopDistance": int(stop_distance),
                "maintainLineOfSight": bool(maintain_line_of_sight),
            },
        }
        return await self._dispatch("followPlayer", payload)

    async def attack_entity(
        self,
        entity_name: str,
        *,
        mode: str = "melee",
        chase_distance: int = 6,
    ) -> Dict[str, Any]:
        """対象エンティティへの戦闘コマンドを送信する。"""

        normalized_mode = mode.lower()
        if normalized_mode not in {"melee", "ranged"}:
            raise ActionValidationError("mode は 'melee' もしくは 'ranged' を指定してください")

        payload = {
            "type": "attackEntity",
            "args": {
                "target": _require_non_empty_text(entity_name, field="target"),
                "mode": normalized_mode,
                "chaseDistance": int(chase_distance),
            },
        }
        return await self._dispatch("attackEntity", payload)


__all__ = ["MovementActions"]
