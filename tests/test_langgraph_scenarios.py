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
    ActionDirective,
    PlanOut,
    ReActStep,
    get_plan_priority,
    plan,
    reset_plan_priority,
)
from agent_orchestrator import UnifiedAgentGraph  # type: ignore  # noqa: E402
from langgraph_state import UnifiedPlanState  # type: ignore  # noqa: E402


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


def test_action_directive_overrides_category(
    monkeypatch: pytest.MonkeyPatch, orchestrator_noop: AgentOrchestrator
) -> None:
    recorded: Dict[str, Any] = {}

    async def fake_handle(
        self,
        category: str,
        step: str,
        *,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        explicit_coords: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        recorded["category"] = category
        recorded["explicit_coords"] = explicit_coords
        return True, explicit_coords, None

    monkeypatch.setattr(AgentOrchestrator, "_handle_action_task", fake_handle)

    directive = ActionDirective(
        directive_id="step-1",
        step="カスタム採掘",
        category="mine",
        args={"coordinates": {"x": 5, "y": 60, "z": -3}},
    )
    plan_out = PlanOut(
        plan=["カスタム採掘"],
        resp="",
        directives=[directive],
    )

    asyncio.run(orchestrator_noop._execute_plan(plan_out))

    assert recorded["category"] == "mine"
    assert recorded["explicit_coords"] == (5, 60, -3)


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


def test_low_confidence_triggers_pre_action_review(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.output_text = text
            self.output: List[Any] = []

    class QueueAsyncOpenAI:
        def __init__(self, queue: List[DummyResponse]) -> None:
            self._queue = queue
            self.responses = self

        async def create(self, **_: Any) -> DummyResponse:
            if not self._queue:
                raise RuntimeError("no more responses queued")
            return self._queue.pop(0)

    plan_payload = json.dumps(
        {
            "plan": ["安全に周囲を確認する"],
            "resp": "了解しました。",
            "intent": "explore",
            "arguments": {
                "coordinates": None,
                "quantity": None,
                "target": None,
                "notes": {},
                "confidence": 0.2,
                "clarification_needed": "none",
                "detected_modalities": [],
            },
            "blocking": False,
            "react_trace": [],
            "confidence": 0.2,
            "clarification_needed": "none",
            "detected_modalities": [],
            "backlog": [],
            "next_action": "execute",
            "goal_profile": {
                "summary": "",
                "category": "",
                "priority": "medium",
                "success_criteria": [],
                "blockers": [],
            },
            "constraints": [],
            "execution_hints": [],
            "directives": [],
            "recovery_hints": [],
        }
    )
    follow_up = "作業開始前に、現在位置や危険物の有無をもう一度教えてください。"
    queue: List[DummyResponse] = [DummyResponse(plan_payload), DummyResponse(follow_up)]

    monkeypatch.setattr(
        sys.modules["planner"].openai,  # type: ignore[index]
        "AsyncOpenAI",
        lambda: QueueAsyncOpenAI(queue),
    )

    asyncio.run(reset_plan_priority())
    result = asyncio.run(plan("危険がないか確認して", {}))

    assert result.next_action == "chat"
    assert "危険物" in result.resp
    assert result.backlog
    assert result.backlog[-1]["label"] == "自動確認"


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


def test_unified_graph_success(monkeypatch: pytest.MonkeyPatch, orchestrator_noop: AgentOrchestrator) -> None:
    async def stub_plan(_: str, __: Dict[str, Any]) -> PlanOut:
        return PlanOut(plan=["南へ移動"], resp="了解しました。", intent="move")

    monkeypatch.setattr(sys.modules["planner"], "plan", stub_plan)
    monkeypatch.setattr(sys.modules["agent_orchestrator"], "plan", stub_plan)

    graph = UnifiedAgentGraph(orchestrator_noop)

    async def runner() -> UnifiedPlanState:
        return await graph.run("南へ進んで", {})

    result = asyncio.run(runner())
    assert result.get("handled") is True
    events = result.get("structured_events") or []
    labels = {event.get("step_label") for event in events}
    assert {"analyze_intent", "generate_plan", "dispatch_action", "mineflayer_node"}.issubset(labels)


def test_unified_graph_plan_failure(monkeypatch: pytest.MonkeyPatch, orchestrator_noop: AgentOrchestrator) -> None:
    async def failing_plan(_: str, __: Dict[str, Any]) -> PlanOut:
        raise ValueError("bad json")

    monkeypatch.setattr(sys.modules["planner"], "plan", failing_plan)
    monkeypatch.setattr(sys.modules["agent_orchestrator"], "plan", failing_plan)

    graph = UnifiedAgentGraph(orchestrator_noop)

    async def runner() -> UnifiedPlanState:
        return await graph.run("うまくいかない指示", {})

    result = asyncio.run(runner())
    assert isinstance(result.get("plan_out"), PlanOut)
    assert result.get("plan_out").plan == []
    events = result.get("structured_events") or []
    failure_events = [event for event in events if event.get("step_label") == "generate_plan"]
    assert failure_events and failure_events[0].get("error")
