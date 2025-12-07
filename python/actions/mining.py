# -*- coding: utf-8 -*-
"""採掘系アクションをまとめたモジュール。"""

from typing import Any, Dict, List

from .base import ActionModule
from .errors import ActionValidationError
from .validators import _require_positions


class MiningActions(ActionModule):
    """ブロック破壊や鉱石探索など、採掘に関連するアクション群。"""

    async def mine_blocks(self, positions: List[Dict[str, int]]) -> Dict[str, Any]:
        """断面で破壊すべき座標を Mineflayer へ渡す。"""

        payload = {"type": "mineBlocks", "args": {"positions": _require_positions(positions)}}
        return await self._dispatch("mineBlocks", payload)

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int = 12,
        max_targets: int = 3,
    ) -> Dict[str, Any]:
        """周囲の鉱石を探索・採掘するコマンドを送信する。"""

        if not ore_names:
            raise ActionValidationError("ore_names は 1 件以上指定してください")

        payload = {
            "type": "mineOre",
            "args": {
                "ores": ore_names,
                "scanRadius": int(scan_radius),
                "maxTargets": int(max_targets),
            },
        }
        return await self._dispatch("mineOre", payload)


__all__ = ["MiningActions"]
