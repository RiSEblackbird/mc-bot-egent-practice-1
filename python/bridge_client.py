# -*- coding: utf-8 -*-
"""Paper 側の AgentBridge プラグインと HTTP 連携するクライアントモジュール。"""

from __future__ import annotations

import json as jsonlib
import logging
import os
import time
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

import httpx
from opentelemetry.trace import Status, StatusCode

from utils import log_structured_event, setup_logger, span_context

logger = setup_logger("bridge.http")

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:19071")
BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "CHANGE_ME")
BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_HTTP_TIMEOUT", "3.0"))
BRIDGE_RETRY = int(os.getenv("BRIDGE_HTTP_RETRY", "3"))
BRIDGE_EVENT_STREAM_PATH = os.getenv("BRIDGE_EVENT_STREAM_PATH", "/v1/events/stream")
BRIDGE_EVENT_STREAM_ENABLED = os.getenv("BRIDGE_EVENT_STREAM_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BRIDGE_EVENT_STREAM_RECONNECT_DELAY = float(os.getenv("BRIDGE_EVENT_STREAM_RECONNECT_DELAY", "3.0"))


def _default_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if BRIDGE_API_KEY:
        headers["X-API-Key"] = BRIDGE_API_KEY
    return headers


class BridgeError(RuntimeError):
    """ブリッジ API との通信に失敗した際に送出されるアプリケーション例外。"""

    def __init__(self, message: str, status_code: int | None = None, payload: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class Frontier:
    """HTTP レスポンスで利用するフロンティア情報の表現。"""

    start: Dict[str, int]
    end: Dict[str, int]


class BridgeClient:
    """AgentBridge プラグインの HTTP API を呼び出す高水準クライアント。

    低レベルの再試行や JSON パースエラー処理を吸収し、呼び出し元が業務ロジックに
    集中できるようにする。"""

    def __init__(self, base_url: str = BRIDGE_URL, timeout: float = BRIDGE_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(base_url=self._base_url, headers=_default_headers(), timeout=self._timeout)

    def close(self) -> None:
        """HTTP コネクションを明示的にクローズする。"""

        self._client.close()

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/health")

    def start_mine(
        self,
        world: str,
        anchor: Dict[str, int],
        direction: Iterable[int],
        section: Dict[str, int],
        length: int,
        owner: str,
    ) -> Dict[str, Any]:
        payload = {
            "world": world,
            "anchor": anchor,
            "dir": list(direction),
            "section": section,
            "length": length,
            "owner": owner,
        }
        return self._request("POST", "/v1/jobs/start_mine", json=payload)

    def advance(self, job_id: str, steps: int = 1) -> Dict[str, Any]:
        payload = {"job_id": job_id, "steps": steps}
        return self._request("POST", "/v1/jobs/advance", json=payload)

    def stop(self, job_id: str) -> Dict[str, Any]:
        return self._request("POST", "/v1/jobs/stop", json={"job_id": job_id})

    def bulk_eval(
        self,
        world: str,
        positions: Iterable[Dict[str, int]],
        job_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"world": world, "positions": list(positions)}
        if job_id:
            payload["job_id"] = job_id
        return self._request("POST", "/v1/blocks/bulk_eval", json=payload)

    def is_player_placed_bulk(
        self,
        world: str,
        positions: Iterable[Dict[str, int]],
        lookup_seconds: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"world": world, "positions": list(positions)}
        if lookup_seconds is not None:
            payload["lookup_seconds"] = lookup_seconds
        return self._request("POST", "/v1/coreprotect/is_player_placed_bulk", json=payload)

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self._base_url}{path}"
        attempt = 0
        backoff = 0.2
        with span_context(
            "bridge.http.request",
            langgraph_node_id="bridge.http_request",
            event_level="info",
            attributes={"http.method": method, "http.url": url},
        ) as span:
            while True:
                try:
                    response = self._client.request(method, url, json=json)
                    span.set_attribute("http.status_code", response.status_code)
                    if response.status_code >= 400:
                        payload: Any | None = None
                        detail = response.text
                        try:
                            payload = response.json()
                            if isinstance(payload, dict) and payload.get("error"):
                                detail = str(payload.get("error"))
                        except jsonlib.JSONDecodeError:
                            payload = None
                        span.set_status(
                            Status(StatusCode.ERROR, f"AgentBridge error {response.status_code}")
                        )
                        span.record_exception(
                            BridgeError(
                                f"AgentBridge error {response.status_code}: {detail}",
                                status_code=response.status_code,
                                payload=payload,
                            )
                        )
                        raise BridgeError(
                            f"AgentBridge error {response.status_code}: {detail}",
                            status_code=response.status_code,
                            payload=payload,
                        )
                    if not response.content:
                        return None
                    return response.json()
                except (httpx.HTTPError, jsonlib.JSONDecodeError) as exc:  # type: ignore[arg-type]
                    attempt += 1
                    context = {
                        "method": method,
                        "path": path,
                        "attempt": attempt,
                        "max_attempts": BRIDGE_RETRY,
                        "error": str(exc),
                    }
                    span.set_attribute("bridge.retry_attempt", attempt)
                    if attempt > BRIDGE_RETRY:
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        log_structured_event(
                            logger,
                            "bridge http request failed permanently",
                            level=logging.ERROR,
                            event_level="fault",
                            langgraph_node_id="bridge.http_request",
                            context=context,
                        )
                        raise BridgeError(f"HTTP request to AgentBridge failed: {exc}") from exc
                    log_structured_event(
                        logger,
                        "bridge http request failed, scheduling retry",
                        level=logging.WARNING,
                        event_level="retry",
                        langgraph_node_id="bridge.http_request",
                        context=context,
                    )
                    time.sleep(backoff)
                    backoff *= 2

    def consume_event_stream(
        self,
        on_event: Callable[[Dict[str, Any]], None],
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """SSE イベントストリームを購読し、受信したデータをコールバックへ渡す。

        ブロッキングで動作するため、asyncio 側からは run_in_executor などを介して
        呼び出すことを想定している。stop_event がセットされた場合は安全に終了する。
        """

        if not BRIDGE_EVENT_STREAM_ENABLED:
            logger.info("bridge event stream disabled; skip subscription")
            return

        url = f"{self._base_url}{BRIDGE_EVENT_STREAM_PATH}"
        while stop_event is None or not stop_event.is_set():
            try:
                with self._client.stream("GET", url, timeout=None) as response:
                    if response.status_code >= 400:
                        raise BridgeError(
                            f"event stream error {response.status_code}",
                            status_code=response.status_code,
                        )
                    buffer: List[str] = []
                    for line in response.iter_lines():
                        if stop_event is not None and stop_event.is_set():
                            return
                        if line is None:
                            continue
                        stripped = line.strip()
                        if not stripped:
                            self._emit_buffered_event(buffer, on_event)
                            buffer = []
                            continue
                        if stripped.startswith("event:"):
                            # keepalive や event 名はログのみで利用し、データ抽出は data 行へ限定する。
                            logger.debug("sse event field=%s", stripped)
                            continue
                        if stripped.startswith("data:"):
                            buffer.append(stripped.replace("data:", "", 1).strip())
                # ストリーム終了時の明示的な遅延を挟み、再接続時のスパムを防ぐ。
                time.sleep(BRIDGE_EVENT_STREAM_RECONNECT_DELAY)
            except BridgeError as exc:
                log_structured_event(
                    logger,
                    "bridge event stream failed", 
                    level=logging.WARNING,
                    event_level="warning",
                    context={"error": str(exc)},
                )
                time.sleep(BRIDGE_EVENT_STREAM_RECONNECT_DELAY)
            except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
                log_structured_event(
                    logger,
                    "bridge event stream unexpected error", 
                    level=logging.ERROR,
                    event_level="fault",
                    context={"error": str(exc)},
                    exc_info=True,
                )
                time.sleep(BRIDGE_EVENT_STREAM_RECONNECT_DELAY)

    def _emit_buffered_event(
        self, lines: List[str], on_event: Callable[[Dict[str, Any]], None]
    ) -> None:
        if not lines:
            return
        raw = "\n".join(lines)
        try:
            event = jsonlib.loads(raw)
            if isinstance(event, dict):
                on_event(event)
        except jsonlib.JSONDecodeError:
            log_structured_event(
                logger,
                "bridge event stream payload decode failed",
                level=logging.WARNING,
                event_level="warning",
                context={"payload": raw},
            )


__all__ = [
    "BridgeClient",
    "BridgeError",
    "Frontier",
    "BRIDGE_EVENT_STREAM_ENABLED",
    "BRIDGE_EVENT_STREAM_PATH",
]
