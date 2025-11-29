from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


from actions import Actions  # type: ignore  # noqa: E402
from bridge_ws import BotBridge  # type: ignore  # noqa: E402
from services import VPTController  # type: ignore  # noqa: E402


class DummyBridge(BotBridge):
    """テスト用に WebSocket を実際には開かずに応答を差し込むスタブ。"""

    def __init__(self, response: Dict[str, Any]) -> None:
        super().__init__(ws_url="ws://dummy")
        self.response = response
        self.requests: List[Dict[str, Any]] = []

    async def send(self, payload: Dict[str, Any], **_: Any) -> Dict[str, Any]:  # type: ignore[override]
        self.requests.append(payload)
        return self.response


class RecordingBridge(BotBridge):
    """送信内容だけを記録する簡易スタブ。"""

    def __init__(self) -> None:
        super().__init__(ws_url="ws://dummy")
        self.sent_payloads: List[Dict[str, Any]] = []

    async def send(self, payload: Dict[str, Any], **_: Any) -> Dict[str, Any]:  # type: ignore[override]
        self.sent_payloads.append(payload)
        return {"ok": True, "echo": payload}


def build_sample_observation() -> Mapping[str, Any]:
    return {
        "position": {"x": 12.0, "y": 64.0, "z": -5.0},
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "orientation": {"yawDegrees": 90.0, "pitchDegrees": 5.0},
        "navigationHint": {"targetYawDegrees": 0.0, "horizontalDistance": 4.5, "verticalOffset": 0.0},
        "status": {"health": 18, "food": 16},
        "onGround": True,
        "hotbar": [{"count": 4}, {"count": 1}],
    }


def test_vpt_controller_generates_alignment_actions() -> None:
    controller = VPTController(tick_interval_ms=50.0)
    observation = build_sample_observation()
    actions = controller.generate_action_sequence(observation, max_actions=6)

    assert len(actions) >= 2
    assert actions[0]["kind"] == "look"
    assert actions[0]["relative"] is True
    assert actions[1]["kind"] == "control"
    assert actions[1]["control"] == "forward"


def test_gather_observation_via_bridge() -> None:
    async def runner() -> None:
        observation = build_sample_observation()
        bridge = DummyBridge({"ok": True, "data": observation})
        controller = VPTController()

        fetched = await controller.gather_observation(bridge)

        assert fetched == observation
        assert bridge.requests[0]["type"] == "gatherVptObservation"

    asyncio.run(runner())


def test_actions_dispatches_vpt_sequence() -> None:
    async def runner() -> None:
        bridge = RecordingBridge()
        actions_helper = Actions(bridge)
        sequence = [
            {"kind": "look", "yaw": -45.0, "pitch": 0.0, "relative": True, "durationTicks": 4},
            {"kind": "control", "control": "forward", "state": True, "durationTicks": 10},
            {"kind": "control", "control": "forward", "state": False, "durationTicks": 0},
        ]

        await actions_helper.play_vpt_actions(sequence, metadata={"policy": "heuristic"})

        assert bridge.sent_payloads
        payload = bridge.sent_payloads[0]
        assert payload["type"] == "playVptActions"
        assert payload["args"]["actions"] == sequence
        assert payload["args"]["metadata"] == {"policy": "heuristic"}

    asyncio.run(runner())

