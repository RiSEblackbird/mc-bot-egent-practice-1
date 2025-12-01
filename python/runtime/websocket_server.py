# -*- coding: utf-8 -*-
"""Agent WebSocket サーバーをカプセル化するモジュール。"""

from __future__ import annotations

import json
from typing import Any, Dict

from websockets import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from utils import setup_logger


class AgentWebSocketServer:
    """Node -> Python のチャット転送を受け付ける WebSocket サーバー。"""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.logger = setup_logger("agent.ws")

    async def handler(self, websocket: WebSocketServerProtocol) -> None:
        """各接続ごとに JSON コマンドを受信・処理する。"""

        peer = f"{websocket.remote_address}" if websocket.remote_address else "unknown"
        self.logger.info("connection opened from %s", peer)
        try:
            async for raw in websocket:
                response = await self._handle_message(raw)
                await websocket.send(json.dumps(response, ensure_ascii=False))
        except (ConnectionClosedOK, ConnectionClosedError):
            self.logger.info("connection closed from %s", peer)
        except Exception:
            self.logger.exception("unexpected error while handling connection from %s", peer)

    async def _handle_message(self, raw: str) -> Dict[str, Any]:
        """受信文字列を解析し、サポートするコマンドへ振り分ける。"""

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.error("invalid JSON payload=%s", raw)
            return {"ok": False, "error": "invalid json"}

        payload_type = payload.get("type")
        if payload_type == "chat":
            args = payload.get("args") or {}
            username = str(args.get("username", "")).strip() or "Player"
            message = str(args.get("message", "")).strip()

            if not message:
                self.logger.warning("empty chat message received username=%s", username)
                return {"ok": False, "error": "empty message"}

            await self.orchestrator.enqueue_chat(username, message)
            return {"ok": True}

        if payload_type == "agentEvent":
            args = payload.get("args") or {}
            await self.orchestrator.handle_agent_event(args)
            return {"ok": True}

        self.logger.error("unsupported payload type=%s", payload_type)
        return {"ok": False, "error": "unsupported type"}
