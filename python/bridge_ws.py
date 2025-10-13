# -*- coding: utf-8 -*-
import asyncio
import json
import os
import websockets
from typing import Any, Dict
from utils import setup_logger

logger = setup_logger("bridge")

class BotBridge:
    """Python→Node WebSocket ブリッジ（単純な送信ユーティリティ）"""
    def __init__(self, ws_url: str | None = None) -> None:
        self.ws_url = ws_url or os.getenv("WS_URL", "ws://127.0.0.1:8765")

    async def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"WS send: {payload}")
        async with websockets.connect(self.ws_url) as ws:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            resp = await ws.recv()
            logger.debug(f"WS recv: {resp}")
            return json.loads(resp)
