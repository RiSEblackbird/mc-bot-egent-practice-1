from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from planner import (  # type: ignore  # noqa: E402
    PlanOut,
    get_plan_priority,
    plan,
    reset_plan_priority,
)


class ReplanActions:
    """Mineflayer とのやり取りを模したスタブで再計画フローを観察する。"""

    def __init__(self) -> None:
        self.mine_calls: List[Dict[str, Any]] = []
        self.equip_calls: List[Dict[str, Optional[str]]] = []
        self.say_messages: List[str] = []

    async def say(self, text: str) -> Dict[str, bool]:
        self.say_messages.append(text)
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
        return {"ok": False, "error": "pickaxe not equipped"}

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

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, bool]:
        return {"ok": True}


def test_mining_failure_triggers_replan(monkeypatch: pytest.MonkeyPatch) -> None:
    """採掘失敗時に障壁通知を挟んで再計画が走ることを統合テストする。"""

    actions = ReplanActions()
    memory = Memory()
    orchestrator = AgentOrchestrator(actions, memory)

    async def fake_barrier(step: str, reason: str, context: Dict[str, Any]) -> str:
        return f"障壁: {step} / {reason}"

    replan_prompts: List[str] = []

    async def fake_plan(message: str, context: Dict[str, Any]) -> PlanOut:
        replan_prompts.append(message)
        return PlanOut(
            plan=["渡されたツルハシを装備する"],
            resp="代替プランで進めます。",
        )

    monkeypatch.setattr("agent.compose_barrier_notification", fake_barrier)
    monkeypatch.setattr("agent.plan", fake_plan)

    plan_out = PlanOut(
        plan=["近くのダイヤモンド鉱石を採掘する", "チェストに鉱石を収納する"],
        resp="",
    )

    async def runner() -> None:
        await orchestrator._execute_plan(plan_out)

    asyncio.run(runner())

    assert len(actions.mine_calls) == 1
    assert actions.mine_calls[0]["targets"]
    assert len(actions.say_messages) == 2
    assert actions.say_messages[0].startswith("障壁")
    assert actions.say_messages[1] == "代替プランで進めます。"
    assert actions.equip_calls == [
        {"tool_type": "pickaxe", "item_name": None, "destination": "hand"}
    ]
    assert replan_prompts and "失敗" in replan_prompts[0]


def test_plan_timeout_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM タイムアウト時にフォールバックプランと優先度昇格が行われる。"""

    class TimeoutAsyncOpenAI:
        """Responses.create が TimeoutError を送出するスタブクライアント。"""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.responses = self

        async def create(self, *args: Any, **kwargs: Any) -> Any:
            raise asyncio.TimeoutError("simulated timeout")

    monkeypatch.setattr("planner.AsyncOpenAI", TimeoutAsyncOpenAI)

    async def runner() -> tuple[PlanOut, str]:
        await reset_plan_priority()
        plan_out_inner = await plan("状況どう？", {})
        priority_inner = await get_plan_priority()
        await reset_plan_priority()
        return plan_out_inner, priority_inner

    plan_out, priority = asyncio.run(runner())

    assert plan_out.plan == []
    assert plan_out.resp == "了解しました。"
    assert priority == "high"


def test_barrier_timeout_uses_short_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """障壁通知生成がタイムアウトしても短縮メッセージがチャットへ送信される。"""

    class TimeoutAsyncOpenAI:
        """障壁通知用の Responses.create がタイムアウトするスタブクライアント。"""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.responses = self

        async def create(self, *args: Any, **kwargs: Any) -> Any:
            raise asyncio.TimeoutError("simulated barrier timeout")

    monkeypatch.setattr("planner.AsyncOpenAI", TimeoutAsyncOpenAI)

    actions = ReplanActions()
    memory = Memory()
    orchestrator = AgentOrchestrator(actions, memory)

    step = (
        "地下採掘拠点へ向かう途中でモンスターと遭遇しつつ複数の資材を携行"
        "した状態で緊急退避を試みる"
    )
    reason = (
        "敵対モブの連続攻撃と落下ダメージで残り体力が危険水準まで低下し、装"
        "備の耐久値も限界に近づいたため安全確保を優先する必要がある"
    )

    async def runner() -> None:
        await orchestrator._report_execution_barrier(step, reason)

    asyncio.run(runner())

    assert actions.say_messages, "障壁メッセージが送信されていません"

    expected_step = step.strip()
    if len(expected_step) > 40:
        expected_step = f"{expected_step[:40]}…"
    expected_reason = reason.strip()
    if len(expected_reason) > 60:
        expected_reason = f"{expected_reason[:60]}…"
    expected_message = (
        f"手順「{expected_step}」で問題が発生しました: {expected_reason}"
    )

    assert actions.say_messages[0] == expected_message
