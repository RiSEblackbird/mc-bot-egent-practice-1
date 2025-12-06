from __future__ import annotations

"""エージェント内部状態を簡易に可視化する HTTP ダッシュボードサーバー。"""

import asyncio
import contextlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from utils import setup_logger


class DashboardServer:
    """ブラウザからボットの内部状況を確認するための軽量 HTTP サーバー。"""

    def __init__(
        self,
        orchestrator: Any,
        *,
        host: str,
        port: int,
        access_token: Optional[str] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.access_token = access_token
        self.logger = setup_logger("agent.dashboard")
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        """HTTP サーバーを起動する。start_server は即時返却する。"""

        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        socknames = ", ".join(
            f"{sock.getsockname()[0]}:{sock.getsockname()[1]}"
            for sock in (self._server.sockets or [])
        )
        self.logger.info("dashboard listening on http://%s", socknames or "unknown")

    async def stop(self) -> None:
        """サーバーを停止し、ソケットを解放する。"""

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self.logger.info("dashboard stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """GET リクエストのみを受け付ける極小 HTTP ハンドラ。"""

        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            await self._write_response(
                writer, 413, "Payload Too Large", b"request too large"
            )
            return
        except Exception:
            await self._write_response(writer, 400, "Bad Request", b"bad request")
            return

        try:
            request_line, *header_lines = raw.decode("latin-1").split("\r\n")
            method, target, _ = request_line.split(" ")
        except Exception:
            await self._write_response(writer, 400, "Bad Request", b"malformed request")
            return

        headers: Dict[str, str] = {}
        for line in header_lines:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        parsed = urlparse(target)
        if self.access_token and self._extract_token(headers, parsed) != self.access_token:
            await self._write_response(
                writer,
                401,
                "Unauthorized",
                b'{"error":"unauthorized"}',
                content_type="application/json",
            )
            return

        if method != "GET":
            await self._write_response(writer, 405, "Method Not Allowed", b"method not allowed")
            return

        path = parsed.path or "/"
        if path == "/static/app.js":
            await self._serve_static(writer, "app.js", content_type="application/javascript; charset=utf-8")
            return

        if path == "/api/state":
            payload = self._build_state_payload()
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            await self._write_response(
                writer, 200, "OK", body, content_type="application/json"
            )
            return

        if path == "/api/health":
            body = json.dumps({"ok": True}).encode("utf-8")
            await self._write_response(
                writer, 200, "OK", body, content_type="application/json"
            )
            return

        html = self._render_index()
        await self._write_response(
            writer, 200, "OK", html.encode("utf-8"), content_type="text/html; charset=utf-8"
        )

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
        body: bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        """HTTP レスポンスを書き出し、接続を閉じる。"""

        headers = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("latin-1") + body)
        try:
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve_static(
        self, writer: asyncio.StreamWriter, filename: str, *, content_type: str
    ) -> None:
        """ダッシュボード用の静的アセットを返却する。"""

        static_dir = Path(__file__).resolve().parent / "static"
        target = static_dir / filename
        if not target.exists():
            await self._write_response(writer, 404, "Not Found", b"not found")
            return

        try:
            body = target.read_bytes()
        except Exception:
            await self._write_response(writer, 500, "Internal Server Error", b"failed to read asset")
            return

        await self._write_response(writer, 200, "OK", body, content_type=content_type)

    def _extract_token(self, headers: Dict[str, str], parsed) -> Optional[str]:
        """Authorization ヘッダーまたは token クエリからトークンを取得する。"""

        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        query = parse_qs(parsed.query or "")
        tokens = query.get("token") or []
        return tokens[0].strip() if tokens else None

    def _build_state_payload(self) -> Dict[str, Any]:
        """ダッシュボードへ返却する JSON フレンドリーなサマリーを生成する。"""

        memory = getattr(self.orchestrator, "memory", None)
        queue = getattr(self.orchestrator, "chat_queue", None)
        status_service = getattr(self.orchestrator, "status_service", None)

        status_snapshot: Dict[str, Any] = {}
        if status_service:
            try:
                status_snapshot = status_service.build_context_snapshot(
                    current_role_id=getattr(self.orchestrator, "current_role", "unknown")
                )
            except Exception as exc:
                self.logger.warning(
                    "failed to build context snapshot for dashboard: %s", exc
                )

        last_plan = memory.get("last_plan_summary") if memory else None
        plan_summary = last_plan if isinstance(last_plan, dict) else {}

        structured_events = []
        if memory:
            events = memory.get("structured_event_history") or []
            if isinstance(events, list):
                structured_events = [item for item in events if isinstance(item, dict)][-10:]

        perception_history = []
        perception_snapshots = memory.get("perception_snapshots") if memory else None
        if isinstance(perception_snapshots, list):
            perception_history = [
                item for item in perception_snapshots if isinstance(item, dict)
            ][-5:]

        reflections = []
        if memory and hasattr(memory, "list_reflections"):
            try:
                reflections = [
                    entry.to_dict() for entry in memory.list_reflections(limit=5)
                ]
            except Exception as exc:
                self.logger.warning("failed to read reflections for dashboard: %s", exc)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "role": {
                "current": getattr(self.orchestrator, "current_role", "unknown"),
            },
            "queue": {
                "backlog": getattr(queue, "backlog_size", 0),
                "max_size": (getattr(getattr(queue, "queue", None), "maxsize", 0) or None),
            },
            "last_chat": memory.get("last_chat") if memory else None,
            "plan_summary": plan_summary,
            "status": status_snapshot,
            "perception": {
                "summary": memory.get("perception_summary") if memory else None,
                "history": perception_history,
            },
            "events": structured_events,
            "recent_reflections": reflections,
        }

    def _render_index(self) -> str:
        """ダッシュボードのシンプルな HTML を返す。"""

        return """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>MC Bot Dashboard</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 20px; background: #0b1021; color: #e7edf3; }
    h1 { margin-top: 0; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .card { padding: 12px; border-radius: 8px; background: #131a33; box-shadow: 0 2px 8px rgba(0,0,0,0.35); }
    .label { color: #8ca0c8; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
    pre { background: #0f1428; padding: 12px; border-radius: 8px; overflow: auto; color: #cdd7e5; }
    .error { color: #ffb3b3; }
    code { color: #a6e22e; }
    a { color: #7dd3fc; }
    button { background: #1e2847; color: #e7edf3; border: 1px solid #3a4a78; padding: 6px 10px; border-radius: 6px; cursor: pointer; }
    button:hover { background: #253257; }
  </style>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script defer src="/static/app.js"></script>
</head>
<body>
  <h1>Minecraft Agent Dashboard</h1>
  <div id="root">loading...</div>
</body>
</html>
"""

