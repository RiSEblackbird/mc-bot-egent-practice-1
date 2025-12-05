"""TaskRouter の協調ロジックを検証するユニットテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
import logging

import pytest

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from orchestrator.action_analyzer import ActionAnalyzer  # type: ignore  # noqa: E402
from orchestrator.task_router import TaskRouter  # type: ignore  # noqa: E402


@dataclass
class StubChatPipeline:
    """TaskRouter からの委譲呼び出しを検証するためのスタブ。"""

    backlog_calls: List[Tuple[Iterable[Dict[str, str]], bool]] = None
    detection_calls: List[Tuple[Iterable[Dict[str, Any]], bool]] = None
    action_task_response: Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]] = (
        True,
        None,
        None,
    )
    select_pickaxe_response: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.backlog_calls = [] if self.backlog_calls is None else self.backlog_calls
        self.detection_calls = (
            [] if self.detection_calls is None else self.detection_calls
        )

    async def handle_action_task(
        self,
        category: str,
        step: str,
        *,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        explicit_coords: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        return self.action_task_response

    def select_pickaxe_for_targets(
        self, ore_names: Iterable[str]
    ) -> Optional[Dict[str, Any]]:
        return self.select_pickaxe_response

    async def handle_action_backlog(
        self, backlog: Iterable[Dict[str, str]], *, already_responded: bool
    ) -> None:
        self.backlog_calls.append((list(backlog), already_responded))

    async def handle_detection_reports(
        self, reports: Iterable[Dict[str, Any]], *, already_responded: bool
    ) -> None:
        self.detection_calls.append((list(reports), already_responded))


@dataclass
class StubSkillDetection:
    """SkillDetectionCoordinator を置き換える簡易スタブ。"""

    perform_result: Optional[Dict[str, Any]] = None
    perform_error: Optional[str] = None
    last_category: Optional[str] = None
    skill_match: Any = None
    execute_response: Tuple[bool, Optional[str]] = (True, None)
    exploration_response: Tuple[bool, Optional[str]] = (False, "fallback")

    async def perform_detection_task(
        self, category: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        self.last_category = category
        return self.perform_result, self.perform_error

    def summarize_position_status(self, data: Dict[str, Any]) -> str:
        return f"position:{data!r}"

    def summarize_general_status(self, data: Dict[str, Any]) -> str:
        return f"general:{data!r}"

    async def find_skill_for_step(self, handler: Any, category: str, step: str):
        return self.skill_match

    async def execute_skill_match(
        self, match: Any, step: str
    ) -> Tuple[bool, Optional[str]]:
        return self.execute_response

    async def begin_skill_exploration(
        self, match: Any, step: str
    ) -> Tuple[bool, Optional[str]]:
        return self.exploration_response


@pytest.fixture()
def task_router() -> TaskRouter:
    """ActionAnalyzer とスタブ依存から TaskRouter を構築する。"""

    chat_pipeline = StubChatPipeline()
    skill_detection = StubSkillDetection()
    barrier_calls: List[Tuple[str, str]] = []

    async def report_barrier(label: str, reason: str) -> None:
        barrier_calls.append((label, reason))

    router = TaskRouter(
        action_analyzer=ActionAnalyzer(),
        chat_pipeline=chat_pipeline,
        skill_detection=skill_detection,
        minedojo_handler=object(),
        report_execution_barrier=report_barrier,
        logger=logging.getLogger("test.task_router"),
    )
    router._barrier_calls = barrier_calls  # type: ignore[attr-defined]
    router._chat_pipeline = chat_pipeline  # type: ignore[attr-defined]
    router._skill_detection = skill_detection  # type: ignore[attr-defined]
    return router


def test_classify_detection_task_uses_keyword(task_router: TaskRouter) -> None:
    """キーワードに基づいて検出タスクが適切に分類されることを確認する。"""

    assert task_router.classify_detection_task("現在位置を教えて") == "player_position"


@pytest.mark.anyio
async def test_perform_detection_task_reports_barrier(task_router: TaskRouter) -> None:
    """検出実行が失敗した際に障壁報告が行われることを検証する。"""

    task_router._skill_detection.perform_error = "timeout"  # type: ignore[attr-defined]
    result = await task_router.perform_detection_task("player_position")
    assert result is None
    assert task_router._skill_detection.last_category == "player_position"  # type: ignore[attr-defined]
    assert task_router._barrier_calls == [  # type: ignore[attr-defined]
        ("現在位置の確認", "ステータス取得に失敗しました（timeout）。")
    ]


@pytest.mark.anyio
async def test_handle_backlog_and_reports_delegate(task_router: TaskRouter) -> None:
    """バックログ整理と検出報告が ChatPipeline へ委譲されることを確認する。"""

    backlog = [{"category": "build"}]
    reports = [{"summary": "ok"}]

    await task_router.handle_action_backlog(backlog, already_responded=False)
    await task_router.handle_detection_reports(reports, already_responded=True)

    assert task_router._chat_pipeline.backlog_calls == [  # type: ignore[attr-defined]
        (backlog, False)
    ]
    assert task_router._chat_pipeline.detection_calls == [  # type: ignore[attr-defined]
        (reports, True)
    ]


@pytest.mark.anyio
async def test_skill_delegation_and_pickaxe_selection(task_router: TaskRouter) -> None:
    """スキル探索/再生とツルハシ選択の委譲を一括で検証する。"""

    skill_match = object()
    task_router._skill_detection.skill_match = skill_match  # type: ignore[attr-defined]
    task_router._skill_detection.execute_response = (False, "not registered")  # type: ignore[attr-defined]
    task_router._skill_detection.exploration_response = (True, None)  # type: ignore[attr-defined]
    task_router._chat_pipeline.select_pickaxe_response = {"name": "diamond_pickaxe"}  # type: ignore[attr-defined]

    assert await task_router.find_skill_for_step("mine", "step") is skill_match
    handled, detail = await task_router.execute_skill_match(skill_match, "step")
    assert handled is False
    assert detail == "not registered"

    explored, error = await task_router.begin_skill_exploration(skill_match, "step")
    assert explored is True
    assert error is None

    pickaxe = task_router.select_pickaxe_for_targets(["diamond_ore"])
    assert pickaxe == {"name": "diamond_pickaxe"}
