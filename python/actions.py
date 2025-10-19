# -*- coding: utf-8 -*-
"""アクション実行の WebSocket コマンドをラップするモジュール。"""

import itertools
import time
from typing import Any, Dict, List, Optional

from bridge_ws import BotBridge
from utils import setup_logger


class Actions:
    """LLM が選択した高レベルアクションを Mineflayer コマンドへ変換するユーティリティ。"""

    def __init__(self, bridge: BotBridge) -> None:
        self.bridge = bridge
        # アクション実行のトレースを残して、Mineflayer 側での挙動と突き合わせできるようにする。
        self.logger = setup_logger("actions")
        # command_id を付番して、Node 側のログと相互参照しやすくする。
        self._command_seq = itertools.count(1)

    async def say(self, text: str) -> Dict[str, Any]:
        """チャット送信コマンドを Mineflayer へ中継する。"""

        payload = {"type": "chat", "args": {"text": text}}
        return await self._dispatch("chat", payload)

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """指定座標への移動を要求するコマンドを送信する。"""

        payload = {"type": "moveTo", "args": {"x": x, "y": y, "z": z}}
        return await self._dispatch("moveTo", payload)

    async def mine_blocks(self, positions: List[Dict[str, int]]) -> Dict[str, Any]:
        """断面で破壊すべき座標群を Mineflayer へ渡す。

        Node 側では positions 配列を順次破壊する実装を想定し、ここでは
        Mineflayer 向けのシンプルなメッセージを送るだけに留める。"""

        payload = {"type": "mineBlocks", "args": {"positions": positions}}
        return await self._dispatch("mineBlocks", payload)

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int = 12,
        max_targets: int = 3,
    ) -> Dict[str, Any]:
        """周囲の鉱石を探索・採掘するコマンドを送信する。"""

        # Mineflayer 側での探索範囲や対象鉱石の種類を完全に指定し、
        # 再現性の高い採掘手順をリモート操作で実現する。

        payload = {
            "type": "mineOre",
            "args": {
                "ores": ore_names,
                "scanRadius": scan_radius,
                "maxTargets": max_targets,
            },
        }
        return await self._dispatch("mineOre", payload)

    async def place_torch(self, position: Dict[str, int]) -> Dict[str, Any]:
        """たいまつを指定位置に設置するコマンドを送信する。"""

        payload = {"type": "placeTorch", "args": position}
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

    async def gather_status(self, kind: str) -> Dict[str, Any]:
        """Mineflayer 側から位置・所持品などのステータス情報を取得する。"""

        payload = {"type": "gatherStatus", "args": {"kind": kind}}
        return await self._dispatch("gatherStatus", payload)

    async def _dispatch(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """共通の送信処理: 付番、送信時間、レスポンスを詳細に記録する。"""

        command_id = next(self._command_seq)
        started_at = time.perf_counter()
        self.logger.info(
            "command[%03d] dispatch=%s payload=%s", command_id, command, payload
        )
        resp = await self.bridge.send(payload)
        elapsed = time.perf_counter() - started_at
        if resp.get("ok"):
            self.logger.info(
                "command[%03d] %s succeeded duration=%.3fs resp=%s",
                command_id,
                command,
                elapsed,
                resp,
            )
        else:
            self.logger.error(
                "command[%03d] %s failed duration=%.3fs resp=%s",
                command_id,
                command,
                elapsed,
                resp,
            )
        return resp

    # TODO: 採掘・設置・追従・戦闘・クラフト等を順次追加
