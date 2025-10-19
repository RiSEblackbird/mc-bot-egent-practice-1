from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

STUB_DIR = PROJECT_ROOT / "tests" / "stubs"
if str(STUB_DIR) not in sys.path:
    sys.path.insert(0, str(STUB_DIR))


from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402


ROLE_LABELS = {
    "generalist": "汎用サポーター",
    "defender": "防衛支援",
    "supplier": "補給調整",
    "scout": "先行偵察",
}


class RecordingActions:
    """テスト用にアクション呼び出しを記録する簡易スタブ。"""

    def __init__(self) -> None:
        self.role_calls: List[Tuple[str, Optional[str]]] = []
        self.move_calls: List[Tuple[int, int, int]] = []

    async def say(self, text: str) -> Dict[str, Any]:
        return {"ok": True, "echo": text}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        self.move_calls.append((x, y, z))
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

    async def set_role(self, role_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        self.role_calls.append((role_id, reason))
        label = ROLE_LABELS.get(role_id, role_id)
        return {"ok": True, "data": {"roleId": role_id, "label": label}}


def test_defender_role_selected_when_threat_detected() -> None:
    async def runner() -> None:
        actions = RecordingActions()
        memory = Memory()
        orchestrator = AgentOrchestrator(actions, memory)

        await orchestrator.handle_agent_event(
            {
                "event": {
                    "channel": "multi-agent",
                    "event": "status",
                    "agentId": "primary",
                    "timestamp": 1000,
                    "payload": {"threatLevel": "critical"},
                }
            }
        )

        backlog: List[Dict[str, str]] = []
        handled, updated, failure = await orchestrator._handle_action_task(  # type: ignore[attr-defined]
            "fight",
            "敵を迎撃して拠点を守って",
            last_target_coords=None,
            backlog=backlog,
        )

        assert handled is True
        assert failure is None
        assert actions.role_calls == [("defender", "threat-alert")]
        assert any(entry.get("module") == "defense" and entry.get("role") == "defender" for entry in backlog)
        active_role = memory.get("agent_active_role")
        assert isinstance(active_role, dict)
        assert active_role.get("id") == "defender"

    asyncio.run(runner())


def test_supplier_role_coordinates_replenishment_meetup() -> None:
    async def runner() -> None:
        actions = RecordingActions()
        memory = Memory()
        orchestrator = AgentOrchestrator(actions, memory)

        await orchestrator.handle_agent_event(
            {
                "event": {
                    "channel": "multi-agent",
                    "event": "status",
                    "agentId": "primary",
                    "timestamp": 2000,
                    "payload": {"supplyDemand": "shortage"},
                }
            }
        )

        backlog: List[Dict[str, str]] = []
        handled, updated, failure = await orchestrator._handle_action_task(  # type: ignore[attr-defined]
            "move",
            "資材補給のため集合地点へ向かう",
            last_target_coords=None,
            backlog=backlog,
            explicit_coords=(8, 64, -4),
        )

        assert handled is True
        assert failure is None
        assert updated == (8, 64, -4)
        assert actions.role_calls == [("supplier", "supply-shortage")]
        assert any(entry.get("category") == "role" and entry.get("role") == "supplier" for entry in backlog)
        active_role = memory.get("agent_active_role")
        assert isinstance(active_role, dict)
        assert active_role.get("id") == "supplier"

    asyncio.run(runner())
