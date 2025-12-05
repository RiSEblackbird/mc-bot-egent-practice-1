# -*- coding: utf-8 -*-
"""ActionGraph の共通ユーティリティ集。

ActionGraph._build_graph 内でしか使われていなかった補助関数を
モジュールとして切り出すことで、LangGraph 構築ロジックの
ネストを抑え、単体テストでの再利用性も高める。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Mapping, Optional

from langgraph_state import record_structured_step


def with_metadata(
    state: Mapping[str, Any],
    *,
    step_label: str,
    base: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """record_structured_step を統一的に適用する薄いラッパー。"""

    merged = dict(base or {})
    merged.update(
        record_structured_step(
            state,
            step_label=step_label,
            inputs=inputs,
            outputs=outputs,
            error=error,
        )
    )
    return merged


def wrap_for_logging(label: str, func: Callable[[Any], Any]):
    """LangGraph ノードの出力へ構造化ログを付与するデコレータ。"""

    async def _runner(state):
        result_or_coroutine = func(state)
        result = (
            await result_or_coroutine
            if inspect.isawaitable(result_or_coroutine)
            else result_or_coroutine
        )
        events = state.get("structured_events") or []
        if any(event.get("step_label") == label for event in events):
            return result

        outputs: Dict[str, Any] = {}
        if isinstance(result, dict):
            outputs = {
                k: result.get(k)
                for k in ("handled", "module", "skill_status", "failure_detail")
                if k in result
            }
            result.update(
                record_structured_step(
                    state,
                    step_label=label,
                    inputs={
                        "category": state.get("category"),
                        "step": state.get("step"),
                    },
                    outputs=outputs,
                )
            )
        else:
            record_structured_step(
                state,
                step_label=label,
                inputs={"category": state.get("category"), "step": state.get("step")},
                outputs=outputs,
            )
        return result

    return _runner


__all__ = ["with_metadata", "wrap_for_logging"]
