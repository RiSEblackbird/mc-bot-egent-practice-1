from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import bridge_ws  # noqa: E402  # isort:skip
from bridge_ws import BotBridge  # type: ignore  # noqa: E402  # isort:skip


class _RefusingConnector:
    """接続拒否を模倣し、呼び出し回数を数えるテスト専用コネクタ。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __aenter__(self) -> "_RefusingConnector":
        self.calls += 1
        raise ConnectionRefusedError("refused")

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


class _HangingWebSocket:
    """受信を意図的にタイムアウトさせるための擬似 WebSocket。"""

    def __init__(self) -> None:
        self.sent_messages: List[str] = []

    async def __aenter__(self) -> "_HangingWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)

    async def recv(self) -> str:
        await asyncio.sleep(0.05)
        return "{}"


@pytest.mark.anyio
async def test_connect_retry_and_logging(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    connector = _RefusingConnector()

    def fake_connect(*args: Any, **kwargs: Any) -> _RefusingConnector:
        return connector

    monkeypatch.setattr(bridge_ws.websockets, "connect", fake_connect)

    notifications: List[Tuple[str, int, str]] = []

    async def on_retry(attempt: int, error_type: str) -> None:
        notifications.append(("retry", attempt, error_type))

    async def on_give_up(retries: int, error_type: str) -> None:
        notifications.append(("give_up", retries, error_type))

    bridge = BotBridge(
        ws_url="ws://example",
        max_retries=3,
        backoff_base=0.01,
        connect_timeout=0.01,
    )

    with caplog.at_level(logging.WARNING, logger="bridge"):
        result = await bridge.send({"type": "ping"}, on_retry=on_retry, on_give_up=on_give_up)

    assert connector.calls == 3
    assert result["ok"] is False
    assert result["error"] == "connect_refused"
    assert result["retries"] == 2

    retry_events = [record for record in caplog.records if getattr(record, "event_level", "") == "retry"]
    fault_events = [record for record in caplog.records if getattr(record, "event_level", "") == "fault"]
    assert retry_events, "リトライイベントが記録されていません"
    assert fault_events, "最終失敗のイベントが記録されていません"

    assert notifications.count(("give_up", 2, "connect_refused")) == 1
    recorded_retries = [item for item in notifications if item[0] == "retry"]
    assert {item[1] for item in recorded_retries} == {1, 2}


@pytest.mark.anyio
async def test_receive_timeout_reports_fault(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    socket = _HangingWebSocket()

    def fake_connect(*args: Any, **kwargs: Any) -> _HangingWebSocket:
        return socket

    monkeypatch.setattr(bridge_ws.websockets, "connect", fake_connect)

    bridge = BotBridge(
        ws_url="ws://example",
        recv_timeout=0.01,
        send_timeout=0.01,
        connect_timeout=0.01,
        max_retries=2,
    )

    with caplog.at_level(logging.ERROR, logger="bridge"):
        result = await bridge.send({"type": "status"})

    assert result["ok"] is False
    assert result["error"] == "recv_timeout"
    fault_events = [record for record in caplog.records if getattr(record, "event_level", "") == "fault"]
    assert fault_events, "受信タイムアウトが fault として記録されていません"
    assert socket.sent_messages, "送信が実行されていません"
