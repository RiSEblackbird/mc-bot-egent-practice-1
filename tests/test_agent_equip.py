"""AgentOrchestrator の装備推論ロジックに関するテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402


class DummyActions:
    """AgentOrchestrator から呼び出されるアクションを記録するスタブ。"""

    def __init__(self) -> None:
        self.equip_calls: List[Dict[str, Optional[str]]] = []

    async def say(self, text: str) -> Dict[str, bool]:
        return {"ok": True}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, bool]:
        return {"ok": True}

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, bool]:
        self.equip_calls.append(
            {
                "tool_type": tool_type,
                "item_name": item_name,
                "destination": destination,
            }
        )
        return {"ok": True}


@pytest.fixture
def orchestrator() -> AgentOrchestrator:
    actions = DummyActions()
    memory = Memory()
    return AgentOrchestrator(actions, memory)


def test_infer_equip_arguments_recognizes_pickaxe(orchestrator: AgentOrchestrator) -> None:
    """ツルハシ装備指示から pickaxe を推測できることを確認する。"""

    result = orchestrator._infer_equip_arguments("渡されたツルハシを装備する")
    assert result == {"tool_type": "pickaxe", "destination": "hand"}


def test_infer_equip_arguments_detects_off_hand(orchestrator: AgentOrchestrator) -> None:
    """左手と盾の指示で off-hand 装備が選択されることを検証する。"""

    result = orchestrator._infer_equip_arguments("左手に盾を構えておいて")
    assert result == {"tool_type": "shield", "destination": "off-hand"}


def test_handle_action_task_dispatches_equip(orchestrator: AgentOrchestrator) -> None:
    """equip カテゴリのステップが equipItem コマンドを発行することをテストする。"""

    backlog: List[Dict[str, str]] = []

    async def runner() -> None:
        handled, _ = await orchestrator._handle_action_task(
            "equip",
            "渡されたツルハシを装備する",
            last_target_coords=None,
            backlog=backlog,
        )

        assert handled is True

    asyncio.run(runner())

    assert backlog == []
    dummy_actions = orchestrator.actions  # type: ignore[assignment]
    assert isinstance(dummy_actions, DummyActions)
    assert dummy_actions.equip_calls == [
        {"tool_type": "pickaxe", "item_name": None, "destination": "hand"}
    ]
