"""LangGraph 向け構造化ロギングの回帰テスト群。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import pytest

from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from bridge_client import BRIDGE_RETRY, BridgeClient, BridgeError  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from utils import setup_logger  # type: ignore  # noqa: E402
from utils.logging import StructuredLogFormatter  # type: ignore  # noqa: E402


class PassiveActions:
    """Mineflayer 呼び出しをスタブ化した受動的アクション群。"""

    async def say(self, text: str) -> Dict[str, Any]:
        return {"ok": True, "echo": text}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        return {"ok": True, "pos": (x, y, z)}

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        return {"ok": True, "tool_type": tool_type, "item_name": item_name, "destination": destination}

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int,
        max_targets: int,
    ) -> Dict[str, Any]:
        return {"ok": True, "ores": list(ore_names)}


def _run_build_node(
    orchestrator: AgentOrchestrator,
    backlog: List[Dict[str, str]],
    *,
    step: str = "ここに小屋を建てて",
) -> Tuple[bool, Any, Any]:
    async def runner() -> Tuple[bool, Any, Any]:
        return await orchestrator._handle_action_task(  # type: ignore[attr-defined]
            "build",
            step,
            last_target_coords=None,
            backlog=backlog,
        )

    return asyncio.run(runner())


def test_structured_log_outputs_json() -> None:
    logger = setup_logger("test.struct")
    formatter = StructuredLogFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        0,
        "node processed",
        args=(),
        exc_info=None,
        extra={
            "langgraph_node_id": "node:test",
            "checkpoint_id": "cp-123",
            "event_level": "progress",
            "structured_context": {"foo": "bar", "count": 2},
        },
    )
    payload = json.loads(formatter.format(record))
    assert payload["langgraph_node_id"] == "node:test"
    assert payload["checkpoint_id"] == "cp-123"
    assert payload["event_level"] == "progress"
    assert payload["context"]["foo"] == "bar"
    assert payload["context"]["count"] == 2


def test_building_recovery_logs_recovery_event(caplog: pytest.LogCaptureFixture) -> None:
    actions = PassiveActions()
    memory = Memory()
    orchestrator = AgentOrchestrator(actions, memory)
    orchestrator.memory.set("building_material_requirements", {"oak_planks": 2})  # type: ignore[attr-defined]
    orchestrator.memory.set(
        "building_layout",
        [
            {"block": "oak_planks", "coords": (0, 64, 0)},
            {"block": "oak_planks", "coords": (1, 64, 0)},
        ],
    )  # type: ignore[attr-defined]
    orchestrator.memory.set("inventory_summary", {"oak_planks": 2})  # type: ignore[attr-defined]
    orchestrator.memory.set(
        "building_checkpoint",
        {"phase": "placement", "reserved_materials": {"oak_planks": 2}, "placed_blocks": 1},
    )  # type: ignore[attr-defined]
    backlog: List[Dict[str, str]] = []

    with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
        handled, _, failure = _run_build_node(orchestrator, backlog)

    assert handled is True
    assert failure is None
    matching = [record for record in caplog.records if record.getMessage() == "building checkpoint advanced"]
    assert matching, "構造化ログが出力されていません"
    record = matching[-1]
    assert getattr(record, "event_level", "") == "recovery"
    assert getattr(record, "langgraph_node_id", "") == "action.handle_building"
    assert isinstance(getattr(record, "checkpoint_id", None), str)
    context = getattr(record, "structured_context", {})
    assert context.get("resumed") is True
    assert context.get("phase") in {"placement", "inspection"}


def test_bridge_client_logs_fault_on_disconnect(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx  # noqa: WPS433 (テスト用に局所 import)
    import time

    def fake_request(self, method, url, json=None):  # type: ignore[override]
        raise httpx.TransportError("connection lost")

    monkeypatch.setattr(httpx.Client, "request", fake_request, raising=False)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    client = BridgeClient(base_url="http://127.0.0.1:19071")
    with caplog.at_level(logging.WARNING, logger="bridge.http"):
        with pytest.raises(BridgeError):
            client.health()
    client.close()

    permanent = [
        record
        for record in caplog.records
        if record.getMessage() == "bridge http request failed permanently"
    ]
    assert permanent, "通信断を示すログが出力されていません"
    final_record = permanent[-1]
    assert getattr(final_record, "event_level", "") == "fault"
    context = getattr(final_record, "structured_context", {})
    assert context.get("attempt") == BRIDGE_RETRY + 1
