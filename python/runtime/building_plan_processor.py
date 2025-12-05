# -*- coding: utf-8 -*-
"""建築計画ノードの責務をまとめた専用コンポーネント。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from services.building_service import (
    BuildingPhase,
    advance_building_state,
    checkpoint_to_dict,
    restore_checkpoint,
)
from utils import log_structured_event

if TYPE_CHECKING:
    from agent import AgentOrchestrator


class BuildingPlanProcessor:
    """チェックポイント復元・計画算出・ログ出力を担うコンポーネント。"""

    def __init__(self, orchestrator: "AgentOrchestrator") -> None:
        self._orchestrator = orchestrator

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """建築タスクに関するチェックポイント更新と計画作成を実行する。"""

        orchestrator = self._orchestrator
        backlog = state["backlog"]
        label = state.get("rule_label") or "建築作業"

        checkpoint_raw = orchestrator.memory.get("building_checkpoint")  # type: ignore[attr-defined]
        requirements = orchestrator.memory.get("building_material_requirements", {})  # type: ignore[attr-defined]
        layout = orchestrator.memory.get("building_layout", [])  # type: ignore[attr-defined]
        inventory_snapshot = orchestrator.memory.get("inventory_summary", {})  # type: ignore[attr-defined]

        checkpoint = restore_checkpoint(checkpoint_raw)
        resumed = (
            checkpoint.phase != BuildingPhase.SURVEY or checkpoint.placed_blocks > 0
        )
        checkpoint_base_id = orchestrator.memory.get("building_checkpoint_base_id")  # type: ignore[attr-defined]
        if not isinstance(checkpoint_base_id, str) or not checkpoint_base_id:
            checkpoint_base_id = f"building:{state.get('step', 'unknown')}"
            orchestrator.memory.set(  # type: ignore[attr-defined]
                "building_checkpoint_base_id",
                checkpoint_base_id,
            )
        updated_checkpoint, procurement_plan, placement_plan = advance_building_state(
            checkpoint=checkpoint,
            requirements=requirements if isinstance(requirements, dict) else {},
            inventory=inventory_snapshot if isinstance(inventory_snapshot, dict) else {},
            layout=layout if isinstance(layout, list) else [],
        )

        orchestrator.memory.set(  # type: ignore[attr-defined]
            "building_checkpoint",
            checkpoint_to_dict(updated_checkpoint),
        )

        checkpoint_identifier = (
            f"{checkpoint_base_id}:{updated_checkpoint.phase.value}:{updated_checkpoint.placed_blocks}"
        )
        placement_snapshot: List[Dict[str, Any]] = [
            {
                "block": task.block,
                "coords": {
                    "x": task.coords[0],
                    "y": task.coords[1],
                    "z": task.coords[2],
                },
            }
            for task in placement_plan
        ]
        event_level = "recovery" if resumed else "progress"
        log_structured_event(
            orchestrator.logger,  # type: ignore[attr-defined]
            "building checkpoint advanced",
            langgraph_node_id="action.handle_building",
            checkpoint_id=checkpoint_identifier,
            event_level=event_level,
            context={
                "phase": updated_checkpoint.phase.value,
                "procurement_plan": procurement_plan,
                "placement_batch": placement_snapshot,
                "reserved_materials": dict(updated_checkpoint.reserved_materials),
                "role": state.get("active_role", ""),
                "resumed": resumed,
            },
        )

        procurement_label = (
            ", ".join(f"{name}:{amount}" for name, amount in procurement_plan.items())
            if procurement_plan
            else "なし"
        )
        placement_label = (
            ", ".join(
                f"{task.block}@{task.coords[0]},{task.coords[1]},{task.coords[2]}"
                for task in placement_plan
            )
            if placement_plan
            else "なし"
        )

        backlog.append(
            {
                "category": "build",
                "step": state["step"],
                "label": label,
                "module": "building",
                "phase": updated_checkpoint.phase.value,
                "procurement": procurement_label,
                "placement": placement_label,
                "role": state.get("active_role", ""),
            }
        )

        return {
            "handled": True,
            "updated_target": state.get("last_target_coords"),
            "failure_detail": None,
        }


__all__ = ["BuildingPlanProcessor"]
