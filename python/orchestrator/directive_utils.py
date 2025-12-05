# -*- coding: utf-8 -*-
"""Directive 処理の共通ユーティリティ群。

AgentOrchestrator と PlanExecutor 間で重複していた directive 関連の
ヘルパーを集約し、責務を明示するためのモジュール。実装を 1 か所に
寄せることで、ハンドラー差し替え時の影響範囲を限定し、トレーサビリ
ティを高める。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planner import ActionDirective, PlanOut, ReActStep
from runtime.hybrid_directive import HybridDirectiveHandler, HybridDirectivePayload


def resolve_directive_for_step(
    directives: Sequence[Any],
    index: int,
    fallback_step: str,
    *,
    logger: Optional[logging.Logger] = None,
) -> Optional[ActionDirective]:
    """Plan ステップに対応する ActionDirective を安全に取り出す。

    Parameters
    ----------
    directives: Sequence[Any]
        LangGraph から返却された directive 配列。
    index: int
        現在処理中の 1 始まりインデックス。
    fallback_step: str
        directive.step が未設定だった場合の補完用ステップ文字列。
    logger: Optional[logging.Logger]
        バリデーション失敗時に警告を残す先。指定が無い場合は静かに None を返す。
    """

    if not directives or index - 1 >= len(directives):
        return None
    candidate = directives[index - 1]
    if isinstance(candidate, ActionDirective):
        return candidate
    if isinstance(candidate, dict):
        try:
            directive = ActionDirective.model_validate(candidate)
        except Exception:
            if logger:
                logger.warning(
                    "directive validation failed index=%d payload=%s", index, candidate
                )
            return None
        if not directive.step:
            directive.step = fallback_step
        return directive
    return None


def build_directive_meta(
    directive: Optional[ActionDirective],
    plan_out: PlanOut,
    index: int,
    total_steps: int,
) -> Optional[Dict[str, Any]]:
    """LangGraph や telemetry に引き継ぐ directive メタ情報を構築する。"""

    if not isinstance(directive, ActionDirective):
        return None
    directive_id = directive.directive_id or f"step-{index}"
    goal_summary = ""
    if getattr(plan_out, "goal_profile", None):
        goal_summary = plan_out.goal_profile.summary or ""
    return {
        "directiveId": directive_id,
        "directiveLabel": directive.label or directive.step or "",
        "directiveCategory": directive.category or plan_out.intent,
        "directiveExecutor": directive.executor or "mineflayer",
        "planIntent": plan_out.intent,
        "goalSummary": goal_summary,
        "stepIndex": index,
        "totalSteps": total_steps,
    }


def coerce_coordinate_tuple(payload: Any) -> Optional[Tuple[int, int, int]]:
    """辞書形式の座標を整数タプルへ変換する安全なヘルパー。"""

    if not isinstance(payload, dict):
        return None
    try:
        x = int(payload.get("x"))
        y = int(payload.get("y"))
        z = int(payload.get("z"))
    except Exception:
        return None
    return (x, y, z)


def extract_directive_coordinates(
    directive: Optional[ActionDirective],
) -> Optional[Tuple[int, int, int]]:
    """ActionDirective から座標候補を抽出し、最初に解釈できたものを返す。"""

    if not isinstance(directive, ActionDirective):
        return None
    args = directive.args if isinstance(directive.args, dict) else {}
    candidates: List[Any] = []
    for key in ("coordinates", "position"):
        if key in args:
            candidates.append(args[key])
    path = args.get("path")
    if isinstance(path, list) and path:
        candidates.append(path[0])

    for candidate in candidates:
        coords = coerce_coordinate_tuple(candidate)
        if coords:
            return coords
    return None


def parse_hybrid_directive_args(
    hybrid_handler: HybridDirectiveHandler, directive: ActionDirective
) -> HybridDirectivePayload:
    """Hybrid 指示の引数をハンドラー経由で解析する。"""

    return hybrid_handler.parse_arguments(directive)


async def execute_hybrid_directive(
    hybrid_handler: HybridDirectiveHandler,
    directive: ActionDirective,
    payload: HybridDirectivePayload,
    *,
    directive_meta: Optional[Dict[str, Any]],
    react_entry: Optional[ReActStep],
    thought_text: str,
    index: int,
    total_steps: int,
) -> bool:
    """Hybrid executor の実行を一元化し、監査用メタを保持する。"""

    return await hybrid_handler.execute(
        directive,
        payload,
        directive_meta=directive_meta,
        react_entry=react_entry,
        thought_text=thought_text,
        index=index,
        total_steps=total_steps,
    )


@contextlib.contextmanager
def directive_scope(actions: Any, meta: Optional[Dict[str, Any]]):
    """Actions.begin/end_directive_scope を安全に包むコンテキスト。"""

    has_interface = hasattr(actions, "begin_directive_scope") and hasattr(
        actions, "end_directive_scope"
    )
    if meta and has_interface:
        actions.begin_directive_scope(meta)  # type: ignore[attr-defined]
    try:
        yield
    finally:
        if meta and has_interface:
            actions.end_directive_scope()  # type: ignore[attr-defined]


__all__ = [
    "build_directive_meta",
    "coerce_coordinate_tuple",
    "directive_scope",
    "execute_hybrid_directive",
    "extract_directive_coordinates",
    "parse_hybrid_directive_args",
    "resolve_directive_for_step",
]
