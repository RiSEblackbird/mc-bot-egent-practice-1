# -*- coding: utf-8 -*-
"""Paper 側の AgentBridge プラグインと HTTP 連携するクライアントモジュール。"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:19071")
BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "CHANGE_ME")
BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_HTTP_TIMEOUT", "3.0"))
BRIDGE_RETRY = int(os.getenv("BRIDGE_HTTP_RETRY", "3"))


def _default_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if BRIDGE_API_KEY:
        headers["X-API-Key"] = BRIDGE_API_KEY
    return headers


class BridgeError(RuntimeError):
    """ブリッジ API との通信に失敗した際に送出されるアプリケーション例外。"""


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
        while True:
            try:
                response = self._client.request(method, url, json=json)
                if response.status_code >= 400:
                    raise BridgeError(f"AgentBridge error {response.status_code}: {response.text}")
                if not response.content:
                    return None
                return response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:  # type: ignore[arg-type]
                attempt += 1
                if attempt > BRIDGE_RETRY:
                    raise BridgeError(f"HTTP request to AgentBridge failed: {exc}") from exc
                logger.warning("Bridge request failed (%s), retrying...", exc)
                time.sleep(backoff)
                backoff *= 2


__all__ = ["BridgeClient", "BridgeError", "Frontier"]
