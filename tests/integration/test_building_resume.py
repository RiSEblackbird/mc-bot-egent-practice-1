"""建築フェーズのチェックポイント統合テスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from services import (  # type: ignore  # noqa: E402
    BuildingPhase,
    PlacementTask,
    checkpoint_to_dict,
    restore_checkpoint,
    rollback_building_state,
)


class PassiveActions:
    """Mineflayer 呼び出しをスタブ化したアクション群。"""

    async def say(self, text: str) -> Dict[str, Any]:
        return {"ok": True, "echo": text}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        return {"ok": True, "pos": (x, y, z)}

    async def equip_item(
        self,
        *,
        tool_type: str | None = None,
        item_name: str | None = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "tool_type": tool_type,
            "item_name": item_name,
            "destination": destination,
        }

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int,
        max_targets: int,
    ) -> Dict[str, Any]:
        return {"ok": True, "ores": list(ore_names)}


@pytest.fixture
def building_orchestrator() -> AgentOrchestrator:
    actions = PassiveActions()
    memory = Memory()
    return AgentOrchestrator(actions, memory)


def _run_build_node(
    orchestrator: AgentOrchestrator,
    backlog: List[Dict[str, str]],
) -> tuple[bool, Any, Any]:
    async def runner() -> tuple[bool, Any, Any]:
        return await orchestrator._handle_action_task(  # type: ignore[attr-defined]
            "build",
            "ここに小屋を建てて",
            last_target_coords=None,
            backlog=backlog,
        )

    return asyncio.run(runner())


def test_checkpoint_restoration_and_procurement_plan(building_orchestrator: AgentOrchestrator) -> None:
    orchestrator = building_orchestrator
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "building_material_requirements",
        {"oak_planks": 12, "glass": 4},
    )
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "building_layout",
        [
            {"block": "oak_planks", "coords": (0, 64, 0)},
            {"block": "oak_planks", "coords": (1, 64, 0)},
            {"block": "glass", "coords": (1, 65, 0)},
        ],
    )
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "inventory_summary",
        {"oak_planks": 8},
    )
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "building_checkpoint",
        {"phase": "survey", "reserved_materials": {}, "placed_blocks": 0},
    )

    backlog: List[Dict[str, str]] = []
    handled, updated, failure = _run_build_node(orchestrator, backlog)

    assert handled is True
    assert failure is None
    assert updated is None

    checkpoint_after = orchestrator.memory.get("building_checkpoint")  # type: ignore[attr-defined]
    restored = restore_checkpoint(checkpoint_after)
    assert restored.phase == BuildingPhase.PROCUREMENT
    assert "procurement" in backlog[0]
    assert "placement" in backlog[0]
    assert "glass:4" in backlog[0]["procurement"]


def test_phase_transition_to_placement_and_inspection(building_orchestrator: AgentOrchestrator) -> None:
    orchestrator = building_orchestrator
    requirements = {"oak_planks": 3}
    layout = [
        ("oak_planks", (0, 64, 0)),
        ("oak_planks", (1, 64, 0)),
        ("oak_planks", (2, 64, 0)),
    ]
    orchestrator.memory.set("building_material_requirements", requirements)  # type: ignore[attr-defined]
    orchestrator.memory.set("building_layout", layout)  # type: ignore[attr-defined]
    orchestrator.memory.set("inventory_summary", {"oak_planks": 3})  # type: ignore[attr-defined]
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "building_checkpoint",
        {"phase": "procurement", "reserved_materials": {"oak_planks": 3}, "placed_blocks": 0},
    )

    backlog: List[Dict[str, str]] = []
    handled, _, failure = _run_build_node(orchestrator, backlog)
    assert handled is True
    assert failure is None
    assert backlog[0]["phase"] == BuildingPhase.PLACEMENT.value
    assert backlog[0]["placement"].startswith("oak_planks@")

    # 2 回目の呼び出しで配置完了状態のチェックを行い、inspection へ進む。
    backlog_second: List[Dict[str, str]] = []
    handled_second, _, failure_second = _run_build_node(orchestrator, backlog_second)
    assert handled_second is True
    assert failure_second is None
    assert backlog_second[0]["phase"] == BuildingPhase.INSPECTION.value
    assert backlog_second[0]["placement"] == "なし"


def test_rollback_restores_previous_phase(building_orchestrator: AgentOrchestrator) -> None:
    orchestrator = building_orchestrator
    layout = [
        PlacementTask("oak_planks", (0, 64, 0)),
        PlacementTask("oak_planks", (1, 64, 0)),
        PlacementTask("oak_planks", (2, 64, 0)),
    ]
    orchestrator.memory.set("building_material_requirements", {"oak_planks": 3})  # type: ignore[attr-defined]
    orchestrator.memory.set("building_layout", layout)  # type: ignore[attr-defined]
    orchestrator.memory.set("inventory_summary", {"oak_planks": 3})  # type: ignore[attr-defined]
    orchestrator.memory.set(  # type: ignore[attr-defined]
        "building_checkpoint",
        checkpoint_to_dict(
            rollback_building_state(
                restore_checkpoint(
                    {"phase": "placement", "reserved_materials": {"oak_planks": 3}, "placed_blocks": 3}
                ),
                placements_attempted=layout,
            )
        ),
    )

    backlog: List[Dict[str, str]] = []
    handled, _, failure = _run_build_node(orchestrator, backlog)
    assert handled is True
    assert failure is None
    assert backlog[0]["phase"] == BuildingPhase.PLACEMENT.value
    assert backlog[0]["placement"].count("oak_planks@") == 3

    checkpoint_after = orchestrator.memory.get("building_checkpoint")  # type: ignore[attr-defined]
    restored = restore_checkpoint(checkpoint_after)
    assert restored.phase == BuildingPhase.PLACEMENT
    assert restored.placed_blocks == 3
