# -*- coding: utf-8 -*-
# 高レベルアクション名を Node 側の JSON コマンドに変換
from typing import Dict, Any
from bridge_ws import BotBridge

class Actions:
    def __init__(self, bridge: BotBridge) -> None:
        self.bridge = bridge

    async def say(self, text: str) -> Dict[str, Any]:
        return await self.bridge.send({"type": "chat", "args": {"text": text}})

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        return await self.bridge.send({"type": "moveTo", "args": {"x": x, "y": y, "z": z}})

    # TODO: 採掘・設置・追従・戦闘・クラフト等を順次追加
