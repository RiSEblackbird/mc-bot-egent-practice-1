# -*- coding: utf-8 -*-
"""アクション実行の WebSocket コマンドをラップするモジュール。"""

import itertools
import time
from typing import Any, Dict, List

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

    async def place_torch(self, position: Dict[str, int]) -> Dict[str, Any]:
        """たいまつを指定位置に設置するコマンドを送信する。"""

        payload = {"type": "placeTorch", "args": position}
        return await self._dispatch("placeTorch", payload)

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
