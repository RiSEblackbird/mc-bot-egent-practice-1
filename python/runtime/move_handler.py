# -*- coding: utf-8 -*-
"""移動系 LangGraph ノードの責務を集約した専用モジュール。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from agent import AgentOrchestrator


async def handle_move(
    state: Dict[str, Any],
    orchestrator: "AgentOrchestrator",
) -> Dict[str, Any]:
    """座標推定・既定座標フォールバック・空腹時バックログ追記をまとめて処理する。"""

    step = state["step"]
    explicit_coords: Optional[Tuple[int, int, int]] = state.get("explicit_coords")
    last_target = state.get("last_target_coords")
    perception_history = state.get("perception_history") or []
    recent_perception = perception_history[-1] if perception_history else {}
    hunger_level = recent_perception.get("food_level")
    weather = recent_perception.get("weather")
    target = explicit_coords or orchestrator._extract_coordinates(step)  # type: ignore[attr-defined]
    if target is None:
        target = last_target
    used_default = False
    if target is None:
        target = orchestrator.default_move_target  # type: ignore[attr-defined]
        used_default = True
    if target is None:
        await orchestrator.movement_service.report_execution_barrier(  # type: ignore[attr-defined]
            step,
            "指示文から移動先の座標を特定できず、実行を継続できませんでした。文章に XYZ 形式の座標を含めてください。",
        )
        return {
            "handled": False,
            "updated_target": last_target,
            "failure_detail": "移動先の座標が不明です。",
        }

    move_result = await orchestrator.movement_service.move_to_coordinates(target)  # type: ignore[attr-defined]
    if used_default:
        await orchestrator.movement_service.report_execution_barrier(  # type: ignore[attr-defined]
            step,
            "指示文から移動先の座標を特定できず、既定座標へ退避しました。文章に XYZ 形式の座標を含めてください。",
        )
    if not move_result.ok:
        error_detail = move_result.error_detail or "Mineflayer 側で移動が拒否されました"
        return {
            "handled": False,
            "updated_target": last_target,
            "failure_detail": error_detail,
        }

    if isinstance(hunger_level, (int, float)) and hunger_level <= orchestrator.low_food_threshold:  # type: ignore[attr-defined]
        # LangGraph の後続ノードへ空腹度情報を渡し、食料補給のフォローアップを促す。
        state["backlog"].append(
            {
                "category": "status",
                "step": step,
                "label": "空腹度が低いため、食料補給を検討してください",
                "weather": weather,
                "food_level": hunger_level,
            }
        )

    if state.get("role_transitioned"):
        active_role = state.get("active_role", "")
        reason = state.get("role_transition_reason") or ""
        state["backlog"].append(
            {
                "category": "role",
                "step": step,
                "label": f"役割切替: {active_role or '不明'}",
                "module": "role",
                "role": active_role,
                "reason": reason,
            }
        )

    return {
        "handled": True,
        "updated_target": target,
        "failure_detail": None,
    }


__all__ = ["handle_move"]
