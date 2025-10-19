"""AgentOrchestrator の採掘分岐に関するテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402


class DummyActions:
    """採掘系テストで Mineflayer とのやり取りを記録するスタブ。"""

    def __init__(self) -> None:
        self.mine_calls: List[Dict[str, Any]] = []
        self.equip_calls: List[Dict[str, Optional[str]]] = []

    async def say(self, text: str) -> Dict[str, bool]:
        return {"ok": True}

    async def mine_ores(
        self,
        targets: List[str],
        *,
        scan_radius: int,
        max_targets: int,
    ) -> Dict[str, Any]:
        self.mine_calls.append(
            {
                "targets": list(targets),
                "scan_radius": scan_radius,
                "max_targets": max_targets,
            }
        )
        return {"ok": True, "data": {"minedBlocks": []}}

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        self.equip_calls.append(
            {
                "tool_type": tool_type,
                "item_name": item_name,
                "destination": destination,
            }
        )
        return {"ok": True}


@pytest.fixture
def orchestrator_fixture() -> Tuple[AgentOrchestrator, DummyActions, Memory]:
    """アクションとメモリを差し替えた Orchestrator のテスト用インスタンス。"""

    actions = DummyActions()
    memory = Memory()
    orchestrator = AgentOrchestrator(actions, memory)
    return orchestrator, actions, memory


def test_mine_skips_when_suitable_pickaxe_exists(
    orchestrator_fixture: Tuple[AgentOrchestrator, DummyActions, Memory]
) -> None:
    """十分なツルハシがある場合は採掘を発行せず装備切り替えで終える。"""

    orchestrator, actions, memory = orchestrator_fixture
    memory.set(
        "inventory_detail",
        {
            "pickaxes": [
                {
                    "name": "diamond_pickaxe",
                    "displayName": "ダイヤのツルハシ",
                    "durability": 23,
                }
            ]
        },
    )

    backlog: List[Dict[str, str]] = []

    async def runner() -> None:
        handled, _ = await orchestrator._handle_action_task(
            "mine",
            "近くのダイヤモンド鉱石を採掘して",
            last_target_coords=None,
            backlog=backlog,
        )
        assert handled is True

    asyncio.run(runner())

    assert backlog == []
    assert actions.mine_calls == []
    assert actions.equip_calls == [
        {"tool_type": "pickaxe", "item_name": None, "destination": "hand"}
    ]


def test_mine_continues_when_pickaxe_missing(
    orchestrator_fixture: Tuple[AgentOrchestrator, DummyActions, Memory]
) -> None:
    """要求ランクを満たすツルハシがない場合は採掘コマンドを実行する。"""

    orchestrator, actions, memory = orchestrator_fixture
    memory.set(
        "inventory_detail",
        {
            "pickaxes": [
                {
                    "name": "stone_pickaxe",
                    "displayName": "石のツルハシ",
                    "durability": 10,
                }
            ]
        },
    )

    backlog: List[Dict[str, str]] = []

    async def runner() -> None:
        handled, _ = await orchestrator._handle_action_task(
            "mine",
            "レッドストーン鉱石を掘って",
            last_target_coords=None,
            backlog=backlog,
        )
        assert handled is True

    asyncio.run(runner())

    assert backlog == []
    assert len(actions.mine_calls) == 1
    assert actions.mine_calls[0]["targets"] == [
        "redstone_ore",
        "deepslate_redstone_ore",
    ]
    assert actions.equip_calls == []
