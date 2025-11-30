# -*- coding: utf-8 -*-
"""LangGraph ノード間で共有する共通ステートと観測用ヘルパー群。

新規参加メンバーが LangGraph のデータフローを追いやすいよう、
プラン生成とアクションディスパッチで使うフィールドを TypedDict で
集約する。ノードごとに入力・出力・エラーを記録するヘルパーも提供し、
構造化ログへ同じフォーマットで出力できるようにしている。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple, TypedDict

from utils import log_structured_event, setup_logger

logger = setup_logger("langgraph.state")


class UnifiedPlanState(TypedDict, total=False):
    """プランニング系とアクション系で共有するステート表現。"""

    # プラン生成フェーズで利用
    user_msg: str
    context: Dict[str, Any]
    prompt: str
    payload: Dict[str, Any]
    response: Any
    content: str
    plan_out: Any
    parse_error: str
    llm_error: str
    priority: str
    fallback_plan_out: Any

    # アクションディスパッチで利用
    category: str
    step: str
    last_target_coords: Optional[Tuple[int, int, int]]
    explicit_coords: Optional[Tuple[int, int, int]]
    backlog: List[Dict[str, str]]
    rule_label: str
    rule_implemented: bool
    handled: bool
    updated_target: Optional[Tuple[int, int, int]]
    failure_detail: Optional[str]
    module: str
    active_role: str
    role_transitioned: bool
    role_transition_reason: Optional[str]
    skill_candidate: Any
    skill_status: str

    # 観測メタデータ
    step_label: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    error: Optional[str]
    structured_events: List[Dict[str, Any]]
    structured_event_history: List[Dict[str, Any]]
    perception_history: List[Dict[str, Any]]


def _serialize_for_log(data: Mapping[str, Any]) -> Dict[str, Any]:
    """ログ出力用にシンプルな辞書へ正規化するヘルパー。"""

    safe: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = list(value)
        elif isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items()}
        else:
            safe[key] = repr(value)
    return safe


def record_structured_step(
    state: UnifiedPlanState,
    *,
    step_label: str,
    inputs: Optional[Mapping[str, Any]] = None,
    outputs: Optional[Mapping[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """ノードの入出力とエラーを統一形式で記録し、ログにも残す。"""

    events = list(state.get("structured_events") or [])
    entry: Dict[str, Any] = {
        "step_label": step_label,
        "inputs": _serialize_for_log(dict(inputs or {})),
        "outputs": _serialize_for_log(dict(outputs or {})),
        "error": error,
    }
    events.append(entry)
    log_structured_event(
        logger,
        "langgraph_step",
        context=entry,
        langgraph_node_id=step_label,
    )
    return {"structured_events": events, "step_label": step_label, "inputs": entry["inputs"], "outputs": entry["outputs"], "error": error}


__all__ = ["UnifiedPlanState", "record_structured_step"]
