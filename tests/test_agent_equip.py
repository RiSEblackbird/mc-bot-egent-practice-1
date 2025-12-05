"""AgentOrchestrator の装備推論ロジックに関するテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import copy

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

    def __init__(self, *, inventory_data: Optional[Dict[str, Any]] = None) -> None:
        self.equip_calls: List[Dict[str, Optional[str]]] = []
        self.say_messages: List[str] = []
        # gather_status("inventory") の戻り値を差し替えられるようにすることで、
        # 装備テストがさまざまな在庫状況を簡単に再現できる。
        default_inventory = {
            "formatted": "所持品は 1 種類（ツルハシ 1 本）を確認しました。",
            "items": [
                {
                    "slot": 0,
                    "name": "iron_pickaxe",
                    "displayName": "Iron Pickaxe",
                    "count": 1,
                    "enchantments": [],
                    "maxDurability": 250,
                    "durabilityUsed": 0,
                    "durability": 250,
                }
            ],
            "pickaxes": [
                {
                    "slot": 0,
                    "name": "iron_pickaxe",
                    "displayName": "Iron Pickaxe",
                    "count": 1,
                    "enchantments": [],
                    "maxDurability": 250,
                    "durabilityUsed": 0,
                    "durability": 250,
                }
            ],
        }
        self._inventory_snapshot: Dict[str, Any] = inventory_data or default_inventory

    async def say(self, text: str) -> Dict[str, bool]:
        self.say_messages.append(text)
        return {"ok": True}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, bool]:
        return {"ok": True}

    async def gather_status(self, kind: str) -> Dict[str, Any]:
        if kind != "inventory":
            return {"ok": False, "error": f"unsupported status kind: {kind}"}
        return {"ok": True, "data": copy.deepcopy(self._inventory_snapshot)}

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
        handled, _, failure = await orchestrator._handle_action_task(
            "equip",
            "渡されたツルハシを装備する",
            last_target_coords=None,
            backlog=backlog,
        )

        assert handled is True
        assert failure is None

    asyncio.run(runner())

    assert backlog == []
    dummy_actions = orchestrator.actions  # type: ignore[assignment]
    assert isinstance(dummy_actions, DummyActions)
    assert dummy_actions.equip_calls == [
        {"tool_type": "pickaxe", "item_name": None, "destination": "hand"}
    ]


def test_handle_equip_reports_barrier_when_pickaxe_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在庫にピッケルがない場合は装備コマンドを送信せず障壁通知する。"""

    inventory_data = {
        "formatted": "所持品は空です。",
        "items": [],
        "pickaxes": [],
    }
    actions = DummyActions(inventory_data=inventory_data)
    memory = Memory()
    orchestrator = AgentOrchestrator(actions, memory)

    async def fake_barrier(step: str, reason: str, context: Dict[str, Any]) -> str:
        return f"障壁: {step} / {reason}"

    monkeypatch.setattr("perception_service.compose_barrier_notification", fake_barrier)

    backlog: List[Dict[str, str]] = []

    async def runner() -> None:
        handled, _, failure = await orchestrator._handle_action_task(
            "equip",
            "採掘用のツルハシを装備して",
            last_target_coords=None,
            backlog=backlog,
        )

        assert handled is False
        assert failure is not None
        assert "装備" in failure

    asyncio.run(runner())

    assert backlog == []
    assert actions.equip_calls == []
    assert actions.say_messages, "障壁メッセージが送信されていません"
    assert "ツルハシ" in actions.say_messages[0]
