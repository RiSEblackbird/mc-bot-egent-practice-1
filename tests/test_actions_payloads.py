from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from actions import ActionValidationError, Actions  # type: ignore  # noqa: E402


class RecordingBridge:
    """テスト用に送信内容を記録する簡易 WebSocket ブリッジ。"""

    def __init__(self, response: Dict[str, Any] | None = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.response = response or {"ok": True, "marker": "test"}

    async def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:  # noqa: D401 - テスト用スタブ
        self.sent.append(payload)
        return self.response | {"echo": payload}


@pytest.mark.anyio
async def test_mine_blocks_payload() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    result = await actions.mine_blocks([{"x": 1, "y": 64, "z": -3}])

    assert bridge.sent[-1] == {
        "type": "mineBlocks",
        "args": {"positions": [{"x": 1, "y": 64, "z": -3}]},
    }
    assert result["ok"] is True


@pytest.mark.anyio
async def test_place_block_payload_with_face() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    await actions.place_block("oak_planks", {"x": 2, "y": 65, "z": 5}, face="north", sneak=True)

    assert bridge.sent[-1] == {
        "type": "placeBlock",
        "args": {
            "block": "oak_planks",
            "position": {"x": 2, "y": 65, "z": 5},
            "sneak": True,
            "face": "north",
        },
    }


@pytest.mark.anyio
async def test_follow_player_payload() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    await actions.follow_player("Taishi", stop_distance=4, maintain_line_of_sight=False)

    assert bridge.sent[-1] == {
        "type": "followPlayer",
        "args": {"target": "Taishi", "stopDistance": 4, "maintainLineOfSight": False},
    }


@pytest.mark.anyio
async def test_attack_entity_mode_validation() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    with pytest.raises(ActionValidationError):
        await actions.attack_entity("zombie", mode="invalid")


@pytest.mark.anyio
async def test_craft_item_payload() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    await actions.craft_item("oak_planks", amount=3, use_crafting_table=False)

    assert bridge.sent[-1] == {
        "type": "craftItem",
        "args": {"item": "oak_planks", "amount": 3, "useCraftingTable": False},
    }


@pytest.mark.anyio
async def test_dispatch_outputs_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    with caplog.at_level(logging.INFO, logger="actions"):
        await actions.say("進捗ログ")

    # 進捗と完了の両方が出力されることを期待する。
    progress = [record for record in caplog.records if getattr(record, "event_level", "") == "progress"]
    completed = [record for record in caplog.records if getattr(record, "event_level", "") == "success"]
    assert progress, "dispatch prepared ログが出力されていません"
    assert completed, "dispatch completed ログが出力されていません"


@pytest.mark.anyio
async def test_validate_positions_rejects_empty() -> None:
    bridge = RecordingBridge()
    actions = Actions(bridge)

    with pytest.raises(ActionValidationError):
        await actions.mine_blocks([])
