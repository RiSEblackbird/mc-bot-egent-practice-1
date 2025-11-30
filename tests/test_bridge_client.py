# -*- coding: utf-8 -*-
"""BridgeClient のエラーハンドリングを検証するユニットテスト。"""

from __future__ import annotations

import pathlib
import sys
import unittest

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "python"))

from bridge_client import BridgeClient, BridgeError  # noqa: E402


class BridgeClientTest(unittest.TestCase):
    def test_bridge_error_contains_payload_and_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "liquid_detected", "stop": True})

        transport = httpx.MockTransport(handler)
        client = BridgeClient(base_url="http://example.com")
        client._client = httpx.Client(transport=transport, base_url=client._base_url, headers={}, timeout=client._timeout)

        with self.assertRaises(BridgeError) as ctx:
            client.bulk_eval("world", [{"x": 0, "y": 64, "z": 0}], job_id="stub")

        err = ctx.exception
        self.assertEqual(err.status_code, 409)
        self.assertIsInstance(err.payload, dict)
        self.assertTrue(err.payload.get("stop"))

        client.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
