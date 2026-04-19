# -*- coding: utf-8 -*-
"""Agent WebSocket サーバーをカプセル化するモジュール。"""

from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import ValidationError

from runtime.transport_envelope import CURRENT_TRANSPORT_VERSION, make_transport_envelope, validate_transport_envelope

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

        envelope = self._parse_envelope(payload)
        if envelope is None:
            return {"ok": False, "error": "invalid envelope"}

        body = envelope.body
        if envelope.kind == "command" and envelope.name == "chat":
            args = body.get("args") or {}
            username = str(args.get("username", "")).strip() or "Player"
            message = str(args.get("message", "")).strip()

            if not message:
                self.logger.warning("empty chat message received username=%s", username)
                return {"ok": False, "error": "empty message"}

            await self.orchestrator.enqueue_chat(username, message)
            return self._ok_response(envelope)

        if envelope.kind == "event" and envelope.name == "agentEvent":
            args = body.get("args") or {}
            await self.orchestrator.handle_agent_event(args)
            return self._ok_response(envelope)

        self.logger.error("unsupported envelope kind=%s name=%s", envelope.kind, envelope.name)
        return {"ok": False, "error": "unsupported type"}

    def _parse_envelope(self, payload: Dict[str, Any]):
        try:
            envelope = validate_transport_envelope(payload)
            if envelope.version != CURRENT_TRANSPORT_VERSION:
                self.logger.error("unsupported envelope version=%s", envelope.version)
                return None
            return envelope
        except ValidationError:
            legacy_type = payload.get("type")
            if isinstance(legacy_type, str) and isinstance(payload.get("args"), dict):
                self.logger.warning("legacy payload detected type=%s; wrap into envelope", legacy_type)
                legacy_kind = "event" if legacy_type == "agentEvent" else "command"
                wrapped = make_transport_envelope(
                    source="legacy-node-bot",
                    kind=legacy_kind,
                    name=legacy_type,
                    body={"type": legacy_type, "args": payload.get("args")},
                )
                return validate_transport_envelope(wrapped)
            self.logger.exception("invalid transport envelope payload=%s", payload)
            return None

    def _ok_response(self, envelope) -> Dict[str, Any]:
        return {
            "ok": True,
            "trace_id": envelope.trace_id,
            "run_id": envelope.run_id,
            "message_id": envelope.message_id,
        }
