"""AgentOrchestrator のタスク分類ロジックの差分を検証するテスト群。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from runtime.rules import ACTION_TASK_RULES  # type: ignore  # noqa: E402


@dataclass
class NullActions:
    """AgentOrchestrator 初期化に必要なダミーのアクションハンドラ。"""

    def __getattr__(self, name: str):  # type: ignore[override]
        """テストではアクション呼び出しが行われないため、空のコルーチンを返す。"""

        async def _noop(*args, **kwargs):
            return {"ok": True}

        return _noop


@pytest.fixture
def orchestrator() -> AgentOrchestrator:
    """キーワード分類メソッドを検証するためのオーケストレータインスタンス。"""

    return AgentOrchestrator(NullActions(), Memory())


def legacy_classify(orchestrator: AgentOrchestrator, text: str) -> Optional[str]:
    """旧ロジック（先着順の単純一致）を再現し、新旧差分を比較する。"""

    normalized = text.replace(" ", "").replace("　", "")
    for category, rule in ACTION_TASK_RULES.items():
        if any(keyword and keyword in normalized for keyword in rule.keywords):
            return category
    return None


@pytest.mark.parametrize(
    "text",
    (
        "採掘現場まで移動する",
        "採掘現場で鉱石を掘る",
        "畑を収穫しておいて",
        "敵を迎撃して",
    ),
)
def test_classification_matches_legacy_for_single_category(
    orchestrator: AgentOrchestrator, text: str
) -> None:
    """単一カテゴリだけを含む指示では新旧ロジックが同じ結果になる。"""

    assert orchestrator.task_router.classify_action_task(text) == legacy_classify(
        orchestrator, text
    )


def test_classification_prioritizes_equip_over_mine(
    orchestrator: AgentOrchestrator,
) -> None:
    """装備と採掘が混在する指示では装備が優先される。"""

    text = "採掘に行く前にツルハシを装備して採掘を開始"
    assert legacy_classify(orchestrator, text) == "mine"
    assert orchestrator.task_router.classify_action_task(text) == "equip"


def test_classification_handles_punctuated_text(
    orchestrator: AgentOrchestrator,
) -> None:
    """句読点を含む文でも旧ロジックと同じカテゴリを返す。"""

    text = "目的地へ移動して、鉱石を掘る"
    assert legacy_classify(orchestrator, text) == "move"
    assert orchestrator.task_router.classify_action_task(text) == "move"
