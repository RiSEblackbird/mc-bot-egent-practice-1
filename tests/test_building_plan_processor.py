# -*- coding: utf-8 -*-
"""building_plan_processor モジュールの単体テスト。"""
import logging
from pathlib import Path
import sys
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from runtime.building_plan_processor import BuildingPlanProcessor


class DummyMemory:
    """BuildingPlanProcessor が利用する永続メモリを簡易的に模倣する。"""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:  # pragma: no cover - 単純アクセサ
        return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value


class DummyOrchestrator:
    """建築計画用の orchestrator 依存を最小化したスタブ。"""

    def __init__(self):
        self.memory = DummyMemory()
        self.logger = logging.getLogger("building-plan-test")


def _create_state(step: str) -> Dict[str, Any]:
    return {
        "step": step,
        "backlog": [],
        "rule_label": "建築テスト",
        "last_target_coords": (0, 0, 0),
        "active_role": "builder",
    }


def test_process_creates_checkpoint_and_backlog_entries():
    orchestrator = DummyOrchestrator()
    orchestrator.memory.set(
        "building_material_requirements",
        {"oak_planks": 2},
    )
    orchestrator.memory.set(
        "building_layout",
        [("oak_planks", (0, 0, 0)), ("oak_planks", (1, 0, 0))],
    )
    orchestrator.memory.set("inventory_summary", {"oak_planks": 1})
    processor = BuildingPlanProcessor(orchestrator)
    state = _create_state("二段の足場を作る")

    result = processor.process(state)

    assert result == {"handled": True, "updated_target": (0, 0, 0), "failure_detail": None}
    checkpoint = orchestrator.memory.get("building_checkpoint")
    assert checkpoint["phase"] in {"procurement", "placement"}
    assert state["backlog"][0]["module"] == "building"
    assert "procurement" in state["backlog"][0]
    assert orchestrator.memory.get("building_checkpoint_base_id").startswith("building:")


def test_process_marks_resumed_and_updates_placement_snapshot():
    orchestrator = DummyOrchestrator()
    orchestrator.memory.set("building_checkpoint", {"phase": "placement", "placed_blocks": 1})
    orchestrator.memory.set("building_material_requirements", {"stone": 1})
    orchestrator.memory.set("building_layout", [("stone", (0, 1, 0)), ("stone", (0, 2, 0))])
    orchestrator.memory.set("inventory_summary", {"stone": 2})
    processor = BuildingPlanProcessor(orchestrator)
    state = _create_state("塔を拡張する")

    result = processor.process(state)

    assert result["handled"] is True
    backlog_entry = state["backlog"][0]
    assert backlog_entry["phase"] in {"placement", "inspection"}
    assert "placement" in backlog_entry
