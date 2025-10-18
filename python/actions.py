# -*- coding: utf-8 -*-
# 高レベルアクション名を Node 側の JSON コマンドに変換
from typing import Dict, Any
from bridge_ws import BotBridge
from utils import setup_logger

class Actions:
    def __init__(self, bridge: BotBridge) -> None:
        self.bridge = bridge
        # アクション実行のトレースを残して、Mineflayer 側での挙動と突き合わせできるようにする。
        self.logger = setup_logger("actions")

    async def say(self, text: str) -> Dict[str, Any]:
        self.logger.info(f"queue chat -> '{text}'")
        resp = await self.bridge.send({"type": "chat", "args": {"text": text}})
        if resp.get("ok"):
            self.logger.info("chat command succeeded")
        else:
            self.logger.error(f"chat command failed: {resp}")
        return resp

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        self.logger.info(f"queue moveTo -> ({x}, {y}, {z})")
        resp = await self.bridge.send({"type": "moveTo", "args": {"x": x, "y": y, "z": z}})
        if resp.get("ok"):
            self.logger.info("moveTo command succeeded")
        else:
            self.logger.error(f"moveTo command failed: {resp}")
        return resp

    # TODO: 採掘・設置・追従・戦闘・クラフト等を順次追加
