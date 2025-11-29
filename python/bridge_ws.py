# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

from utils import log_structured_event, setup_logger

logger = setup_logger("bridge")

class BotBridge:
    """Python→Node WebSocket ブリッジ（単純な送信ユーティリティ）"""

    def __init__(
        self,
        ws_url: str | None = None,
        *,
        connect_timeout: float = 5.0,
        send_timeout: float = 3.0,
        recv_timeout: float = 5.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
    ) -> None:
        # Docker Compose 実行時はサービス名でルーティングできるよう、node-bot ホストを既定とする。
        self.ws_url = ws_url or os.getenv("WS_URL", "ws://node-bot:8765")
        # タイムアウトとリトライ設定を明示して、デッドロックや無限待機を避ける。
        self.connect_timeout = connect_timeout
        self.send_timeout = send_timeout
        self.recv_timeout = recv_timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base

    async def send(
        self,
        payload: Dict[str, Any],
        *,
        on_retry: Optional[Callable[[int, str], Awaitable[None]]] = None,
        on_give_up: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """WebSocket 送信を行い、接続/送信/受信ごとにタイムアウトと例外を区別する。

        on_retry/on_give_up を通じて呼び出し元（Actions など）が Mineflayer や
        ユーザーへ再試行/断念の通知を転送できるフックを提供する。
        """

        logger.info(f"WS send: {payload}")
        for attempt in range(1, self.max_retries + 1):
            stage = "connect"
            try:
                async with websockets.connect(
                    self.ws_url, open_timeout=self.connect_timeout
                ) as ws:
                    stage = "send"
                    await asyncio.wait_for(
                        ws.send(json.dumps(payload, ensure_ascii=False)),
                        timeout=self.send_timeout,
                    )
                    stage = "recv"
                    resp = await asyncio.wait_for(ws.recv(), timeout=self.recv_timeout)
                    logger.info(f"WS recv: {resp}")
                    return json.loads(resp)
            except Exception as error:  # noqa: BLE001 - 失敗種別ごとに判定するため広く捕捉
                error_type = self._classify_error(stage, error)
                is_connect_failure = stage == "connect"
                should_retry = is_connect_failure and attempt < self.max_retries
                event_level = "retry" if should_retry else "fault"
                log_structured_event(
                    logger,
                    "WS communication failed",
                    level=logging.WARNING if should_retry else logging.ERROR,
                    event_level=event_level,
                    context={
                        "stage": stage,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "payload": payload,
                        "error_type": error_type,
                    },
                    exc_info=error,
                )
                if should_retry:
                    if on_retry:
                        await on_retry(attempt, error_type)
                    await asyncio.sleep(self._compute_backoff(attempt))
                    continue

                if on_give_up:
                    await on_give_up(attempt - 1, error_type)
                return {
                    "ok": False,
                    "error": error_type,
                    "retries": attempt - 1,
                    "message": str(error),
                }

    def _classify_error(self, stage: str, error: Exception) -> str:
        """例外内容から段階別のエラー種別をテキストで返す。"""

        if isinstance(error, asyncio.TimeoutError):
            return f"{stage}_timeout"
        if isinstance(error, ConnectionRefusedError):
            return "connect_refused"
        if isinstance(error, OSError):
            return f"{stage}_os_error"
        return f"{stage}_error"

    def _compute_backoff(self, attempt: int) -> float:
        """指数バックオフの遅延を計算する。"""

        return min(self.backoff_base * (2 ** (attempt - 1)), 8.0)
