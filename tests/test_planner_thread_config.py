from __future__ import annotations

from typing import Any, Dict

import pytest

import planner
from planner.models import PlanOut


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    async def ainvoke(self, state: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self.calls.append({"state": state, "config": config})
        return {"plan_out": PlanOut(plan=["移動"], resp="了解しました。")}


@pytest.mark.anyio
async def test_plan_uses_context_thread_id_for_langgraph_config(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = _FakeGraph()
    monkeypatch.setattr(planner, "_PLAN_GRAPH", fake_graph)

    plan_out = await planner.plan("拠点に戻って", {"thread_id": "thread-abc", "run_id": "run-1"})

    assert plan_out.plan == ["移動"]
    assert fake_graph.calls[0]["config"] == {"configurable": {"thread_id": "thread-abc"}}


@pytest.mark.anyio
async def test_plan_generates_thread_id_when_context_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_graph = _FakeGraph()
    monkeypatch.setattr(planner, "_PLAN_GRAPH", fake_graph)

    await planner.plan("丸石を掘る", {})

    config = fake_graph.calls[0]["config"]
    assert isinstance(config, dict)
    thread_id = config["configurable"]["thread_id"]
    assert isinstance(thread_id, str)
    assert len(thread_id) == 32
