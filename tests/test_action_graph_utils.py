# -*- coding: utf-8 -*-
"""ActionGraph ユーティリティの単体テスト。"""
import asyncio
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from runtime.action_graph_utils import with_metadata, wrap_for_logging


def test_with_metadata_merges_base_and_records_event():
    state = {"structured_events": []}

    result = with_metadata(
        state,
        step_label="test_step",
        base={"handled": True},
        inputs={"foo": "bar"},
        outputs={"handled": True},
    )

    assert result["handled"] is True
    assert result["structured_events"][0]["step_label"] == "test_step"
    assert result["inputs"] == {"foo": "bar"}
    assert result["outputs"]["handled"] is True


def test_wrap_for_logging_appends_structured_event():
    async def sample_node(state):
        return {"handled": True, "module": "move"}

    state = {"category": "move", "step": "移動する", "structured_events": []}
    wrapped = wrap_for_logging("sample", sample_node)

    result = asyncio.run(wrapped(state))

    assert result["handled"] is True
    assert any(event.get("step_label") == "sample" for event in result["structured_events"])


def test_wrap_for_logging_skips_duplicate_entries():
    async def sample_node(state):
        return {"handled": False, "failure_detail": "skip"}

    state = {
        "category": "move",
        "step": "移動する",
        "structured_events": [{"step_label": "sample"}],
    }
    wrapped = wrap_for_logging("sample", sample_node)

    result = asyncio.run(wrapped(state))

    assert result["handled"] is False
    # 既存イベントを尊重し、新たな structured_events を付与しないこと。
    assert state["structured_events"] == [{"step_label": "sample"}]
    assert "structured_events" not in result
