"""Perception snapshot ingestion and summary tests for AgentOrchestrator."""

from __future__ import annotations

from pathlib import Path

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402


class PassiveActions:
    async def say(self, text: str):  # pragma: no cover - simple stub
        return {"ok": True, "echo": text}


def test_perception_snapshot_summary_updates_memory() -> None:
    orchestrator = AgentOrchestrator(PassiveActions(), Memory())
    snapshot = {
        "hazards": {"liquids": 2, "voids": 1},
        "nearby_entities": {
            "hostiles": 1,
            "details": [
                {
                    "kind": "hostile",
                    "name": "Skeleton",
                    "distance": 4.5,
                    "bearing": "北",
                }
            ],
        },
        "lighting": {"block": 5},
        "weather": {"label": "rain"},
    }

    orchestrator._ingest_perception_snapshot(snapshot, source="test")

    history = orchestrator.memory.get("perception_snapshots")  # type: ignore[attr-defined]
    assert isinstance(history, list) and history, "perception history should store snapshots"
    summary = orchestrator.memory.get("perception_summary")  # type: ignore[attr-defined]
    assert isinstance(summary, str)
    assert "液体検知" in summary
    assert "敵対モブ" in summary
    assert "天候" in summary
