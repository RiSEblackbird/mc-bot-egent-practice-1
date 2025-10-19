"""LangGraph 統合に関するシナリオテスト。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import sys

pytest.importorskip("langgraph")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from planner import (  # type: ignore  # noqa: E402
    PlanOut,
    ReActStep,
    get_plan_priority,
    plan,
    reset_plan_priority,
)


class MoveFailureActions:
    """移動アクションを常に失敗させるテスト用スタブ。"""

    async def say(self, text: str) -> Dict[str, Any]:
        return {"ok": True, "echo": text}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        return {"ok": False, "error": "path blocked"}


class NoOpActions:
    """行動系コマンドをすべて成功扱いで無視するスタブ。"""

    async def say(self, text: str) -> Dict[str, Any]:
        return {"ok": True, "echo": text}

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        return {"ok": True, "pos": (x, y, z)}

    async def gather_status(self, category: str) -> Dict[str, Any]:
        if category == "position":
            return {
                "ok": True,
                "data": {"x": 0, "y": 64, "z": 0, "dimension": "overworld"},
            }
        return {"ok": True, "data": {}}

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


@pytest.fixture
def orchestrator_with_failure() -> AgentOrchestrator:
    actions = MoveFailureActions()
    memory = Memory()
    return AgentOrchestrator(actions, memory)


@pytest.fixture
def orchestrator_noop() -> AgentOrchestrator:
    actions = NoOpActions()
    memory = Memory()
    return AgentOrchestrator(actions, memory)


def test_action_graph_reports_move_failure(orchestrator_with_failure: AgentOrchestrator) -> None:
    backlog: List[Dict[str, str]] = []

    async def runner() -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        return await orchestrator_with_failure._handle_action_task(
            "move",
            "南へ 10 ブロック移動",
            last_target_coords=None,
            backlog=backlog,
        )

    handled, updated, failure = asyncio.run(runner())

    assert handled is False
    assert updated is None
    assert failure is not None and "blocked" in failure
    assert backlog == []


def test_action_graph_parallel_modules_do_not_share_backlog(orchestrator_noop: AgentOrchestrator) -> None:
    backlog: List[Dict[str, str]] = []

    async def runner() -> List[Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]]:
        return await asyncio.gather(
            orchestrator_noop._handle_action_task(
                "build",
                "ここに小屋を建てて",
                last_target_coords=None,
                backlog=backlog,
            ),
            orchestrator_noop._handle_action_task(
                "fight",
                "近くのモンスターを倒して",
                last_target_coords=None,
                backlog=backlog,
            ),
        )

    results = asyncio.run(runner())
    assert all(result[0] for result in results)
    modules = {entry.get("module") for entry in backlog}
    assert modules == {"building", "defense"}
    assert len(backlog) == 2


def test_plan_graph_priority_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.output_text = text
            self.output: List[Any] = []

    class SequencedAsyncOpenAI:
        def __init__(self, queue: List[Any]) -> None:
            self._queue = queue
            self.responses = self

        async def create(self, **_: Any) -> Any:
            if not self._queue:
                raise RuntimeError("no more responses queued")
            result = self._queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    queue: List[Any] = [
        DummyResponse("not json"),
        DummyResponse(
            '{"plan": ["move"], "resp": "了解しました。", "react_trace": ['
            '{"thought": "移動して対応", "action": "move", "observation": ""}]}'
        ),
    ]

    monkeypatch.setattr(
        sys.modules["planner"].openai,  # type: ignore[index]
        "AsyncOpenAI",
        lambda: SequencedAsyncOpenAI(queue),
    )

    asyncio.run(reset_plan_priority())

    first_plan = asyncio.run(plan("失敗する応答", {}))
    assert first_plan.plan == []
    assert asyncio.run(get_plan_priority()) == "high"

    second_plan = asyncio.run(plan("成功する応答", {}))
    assert second_plan.plan == ["move"]
    assert asyncio.run(get_plan_priority()) == "normal"

    asyncio.run(reset_plan_priority())


def test_react_loop_logs_observations(
    caplog: pytest.LogCaptureFixture, orchestrator_noop: AgentOrchestrator
) -> None:
    plan_out = PlanOut(
        plan=["南へ 10 ブロック移動", "現在位置を確認"],
        resp="",
        react_trace=[
            ReActStep(thought="安全な位置へ移動する", action="南へ 10 ブロック移動"),
            ReActStep(thought="現在位置を共有する", action="現在位置を確認"),
        ],
    )

    caplog.set_level(logging.INFO, logger="agent")

    async def runner() -> None:
        await orchestrator_noop._execute_plan(plan_out, initial_target=(0, 64, 0))

    asyncio.run(runner())

    react_logs: List[Dict[str, Any]] = []
    for record in caplog.records:
        try:
            payload = json.loads(record.message)
        except json.JSONDecodeError:
            continue
        if payload.get("message") == "react_step":
            context = payload.get("context") or {}
            react_logs.append(context)

    assert len(react_logs) >= 2
    assert react_logs[0]["thought"] == "安全な位置へ移動する"
    assert "移動成功" in react_logs[0]["observation"]
    assert "X=" in react_logs[1]["observation"] or "報告" in react_logs[1]["observation"]
    assert plan_out.react_trace[0].observation.startswith("移動成功")
