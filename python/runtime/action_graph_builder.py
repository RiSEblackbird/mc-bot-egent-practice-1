from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from skills import SkillMatch

from runtime.action_graph_utils import with_metadata, wrap_for_logging
from runtime.building_plan_processor import BuildingPlanProcessor
from runtime.move_handler import handle_move
from langgraph_state import UnifiedPlanState

_ActionState = UnifiedPlanState


class ActionGraphBuilder:
    """ActionGraph のノード群を組み立てるヘルパー。

    大きくなりがちなノード実装を分離し、LangGraph の構造を見通しやすくする。
    """

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        self._building_processor = BuildingPlanProcessor(orchestrator)

    def build(self) -> CompiledStateGraph:
        orchestrator = self._orchestrator
        graph: StateGraph = StateGraph(_ActionState)

        async def initialize(state: _ActionState) -> Dict[str, Any]:
            base = {
                "handled": False,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": None,
                "module": "generic",
                "active_role": state.get("active_role", orchestrator.current_role),
                "role_transitioned": False,
                "role_transition_reason": None,
                "skill_status": "none",
            }
            return with_metadata(
                state,
                step_label="initialize_action",
                base=base,
                inputs={"category": state.get("category"), "step": state.get("step")},
                outputs={
                    "active_role": base["active_role"],
                    "perception_samples": len(state.get("perception_history") or []),
                    "perception_summary": state.get("perception_summary"),
                    "event_samples": len(state.get("structured_event_history") or []),
                },
            )

        async def seek_skill(state: _ActionState) -> Dict[str, Any]:
            category = state.get("category", "")
            step = state["step"]
            if not category:
                return with_metadata(
                    state,
                    step_label="seek_skill",
                    base={"skill_status": "none"},
                    inputs={"step": step},
                    outputs={"skill_status": "none"},
                )
            match = await orchestrator.task_router.find_skill_for_step(category, step)  # type: ignore[attr-defined]
            if match is None:
                return with_metadata(
                    state,
                    step_label="seek_skill",
                    base={"skill_status": "none"},
                    inputs={"category": category, "step": step},
                    outputs={"skill_status": "none"},
                )
            if match.unlocked:
                if not hasattr(orchestrator.actions, "invoke_skill"):
                    orchestrator.logger.info(  # type: ignore[attr-defined]
                        "skill invocation skipped because Actions.invoke_skill is unavailable",
                    )
                    return with_metadata(
                        state,
                        step_label="seek_skill",
                        base={"skill_status": "none"},
                        inputs={"category": category, "step": step},
                        outputs={"skill_status": "none"},
                    )
                handled, failure_detail = await orchestrator.task_router.execute_skill_match(match, step)  # type: ignore[attr-defined]
                status = "handled" if handled else "failed"
                if not handled and failure_detail is None:
                    status = "none"
                base = {
                    "handled": handled,
                    "failure_detail": failure_detail,
                    "updated_target": state.get("last_target_coords"),
                    "skill_candidate": match,
                    "skill_status": status,
                }
                return with_metadata(
                    state,
                    step_label="seek_skill",
                    base=base,
                    inputs={"category": category, "step": step},
                    outputs={"skill_status": status},
                    error=failure_detail,
                )
            if not hasattr(orchestrator.actions, "begin_skill_exploration"):
                orchestrator.logger.info(  # type: ignore[attr-defined]
                    "skill exploration skipped because Actions.begin_skill_exploration is unavailable",
                )
                return with_metadata(
                    state,
                    step_label="seek_skill",
                    base={"skill_status": "none"},
                    inputs={"category": category, "step": step},
                    outputs={"skill_status": "none"},
                )
            return with_metadata(
                state,
                step_label="seek_skill",
                base={
                    "skill_candidate": match,
                    "skill_status": "locked",
                    "updated_target": state.get("last_target_coords"),
                },
                inputs={"category": category, "step": step},
                outputs={"skill_status": "locked"},
            )

        async def apply_role_policy(state: _ActionState) -> Dict[str, Any]:
            active_role = state.get("active_role", orchestrator.current_role)
            transitioned = False
            reason: Optional[str] = None
            pending = orchestrator._consume_pending_role_switch()  # type: ignore[attr-defined]
            if pending:
                desired_role, pending_reason = pending
                reason = pending_reason
                if desired_role and desired_role != active_role:
                    transitioned = await orchestrator._apply_role_switch(desired_role, pending_reason)  # type: ignore[attr-defined]
                    if transitioned:
                        active_role = orchestrator.current_role  # type: ignore[attr-defined]
            base = {
                "active_role": active_role,
                "role_transitioned": transitioned,
                "role_transition_reason": reason,
            }
            return with_metadata(
                state,
                step_label="apply_role_policy",
                base=base,
                inputs={"pending": bool(pending)},
                outputs={"active_role": active_role, "role_transitioned": transitioned},
            )

        def route_module(state: _ActionState) -> Dict[str, Any]:
            category = state.get("category", "")
            module = "generic"
            if category == "mine":
                module = "mining"
            elif category == "build":
                module = "building"
            elif category == "fight":
                module = "defense"
            elif category in ("move", "move_to_player"):
                module = "move"
            elif category == "equip":
                module = "equip"
            return with_metadata(
                state,
                step_label="route_module",
                base={"module": module},
                inputs={"category": category},
                outputs={"module": module},
            )

        async def trigger_exploration(state: _ActionState) -> Dict[str, Any]:
            match = state.get("skill_candidate")
            if not isinstance(match, SkillMatch):
                return with_metadata(
                    state,
                    step_label="trigger_exploration",
                    base={
                        "handled": False,
                        "failure_detail": "探索対象のスキル候補が見つかりませんでした。",
                        "updated_target": state.get("last_target_coords"),
                        "skill_status": "failed",
                    },
                    inputs={"step": state.get("step")},
                    outputs={"skill_status": "failed"},
                    error="missing_skill_candidate",
                )
            handled, failure_detail = await orchestrator.task_router.begin_skill_exploration(match, state["step"])  # type: ignore[attr-defined]
            status = "exploration" if handled else "failed"
            base = {
                "handled": handled,
                "failure_detail": failure_detail,
                "updated_target": state.get("last_target_coords"),
                "skill_status": status,
            }
            return with_metadata(
                state,
                step_label="trigger_exploration",
                base=base,
                inputs={"step": state.get("step")},
                outputs={"skill_status": status},
                error=failure_detail,
            )

        async def handle_move_node(state: _ActionState) -> Dict[str, Any]:
            return await handle_move(state, orchestrator)

        async def handle_equip(state: _ActionState) -> Dict[str, Any]:
            step = state["step"]
            equip_args = orchestrator.task_router.infer_equip_arguments(step)  # type: ignore[attr-defined]
            if not equip_args:
                await orchestrator.movement_service.report_execution_barrier(  # type: ignore[attr-defined]
                    step,
                    "装備するアイテムを推測できませんでした。ツール名や用途をもう少し具体的に指示してください。",
                )
                return {
                    "handled": True,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": None,
                }

            refresh_ok, inventory_detail, refresh_error = await orchestrator.inventory_sync.refresh(  # type: ignore[attr-defined]
                orchestrator
            )
            if not refresh_ok:
                reason = (
                    f"装備前に所持品を確認できず装備手順を中断しました（{refresh_error}）。"
                )
                await orchestrator.movement_service.report_execution_barrier(step, reason)  # type: ignore[attr-defined]
                return {
                    "handled": False,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": reason,
                }

            tool_type = equip_args.get("tool_type")
            item_name = equip_args.get("item_name")

            items = []
            raw_items = inventory_detail.get("items") if isinstance(inventory_detail, dict) else []
            if isinstance(raw_items, list):
                items = [item for item in raw_items if isinstance(item, dict)]

            item_found = False
            if item_name:
                target = item_name.lower()
                for item in items:
                    name = str(item.get("name") or "").lower()
                    display = str(item.get("displayName") or "").lower()
                    if target == name or target == display:
                        item_found = True
                        break

            if not item_found and tool_type:
                normalized_tool = tool_type.lower()
                if normalized_tool == "pickaxe":
                    pickaxes = inventory_detail.get("pickaxes")
                    if isinstance(pickaxes, list) and any(isinstance(p, dict) for p in pickaxes):
                        item_found = True
                if not item_found:
                    for item in items:
                        name = str(item.get("name") or "").lower()
                        display = str(item.get("displayName") or "").lower()
                        if normalized_tool in name or normalized_tool in display:
                            item_found = True
                            break

            if not item_found:
                label = item_name or tool_type or "指定装備"
                reason = f"インベントリに {label} が見つからず装備できませんでした。"
                await orchestrator.movement_service.report_execution_barrier(  # type: ignore[attr-defined]
                    step, reason
                )
                return {
                    "handled": False,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": reason,
                }

            resp = await orchestrator.actions.equip_item(  # type: ignore[attr-defined]
                tool_type=equip_args.get("tool_type"),
                item_name=equip_args.get("item_name"),
                destination=equip_args.get("destination", "hand"),
            )
            if resp.get("ok"):
                return {
                    "handled": True,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": None,
                }

            error_detail_raw = resp.get("error") or "Mineflayer 側の理由不明な拒否"
            error_detail = str(error_detail_raw)
            normalized_error = error_detail.lower()
            if "requested item is not available in inventory" in normalized_error:
                retry_refresh_ok, retry_inventory, retry_error = await orchestrator.inventory_sync.refresh(  # type: ignore[attr-defined]
                    orchestrator
                )
                if not retry_refresh_ok:
                    orchestrator.memory.set("inventory", "所持品の再取得に失敗しました。")  # type: ignore[attr-defined]
                    orchestrator.memory.set("inventory_detail", {})  # type: ignore[attr-defined]

                label = item_name or tool_type or "指定装備"
                if retry_refresh_ok:
                    pickaxe_hint = ""
                    if isinstance(retry_inventory, dict):
                        pickaxes = retry_inventory.get("pickaxes")
                        if isinstance(pickaxes, list) and not pickaxes:
                            pickaxe_hint = "（ツルハシが不足しています）"
                    reason = (
                        f"装備対象『{label}』がインベントリから見つからず、計画を続行できません。"
                        f"補充してから再度指示してください。{pickaxe_hint}"
                    )
                else:
                    detail = retry_error or "所持品の状態を確認できませんでした"
                    reason = (
                        f"装備対象『{label}』がインベントリで欠品しており、所持品の再取得にも"
                        f"失敗しました（{detail}）。"
                    )

                await orchestrator.movement_service.report_execution_barrier(  # type: ignore[attr-defined]
                    step,
                    reason,
                )
                return {
                    "handled": False,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": reason,
                }

            return {
                "handled": False,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": f"装備コマンドが失敗しました: {error_detail}",
            }

        async def handle_mining(state: _ActionState) -> Dict[str, Any]:
            step = state["step"]
            mining_request = orchestrator.task_router.infer_mining_request(step)  # type: ignore[attr-defined]
            candidate_pickaxe = orchestrator.task_router.select_pickaxe_for_targets(  # type: ignore[attr-defined]
                mining_request["targets"]
            )
            if candidate_pickaxe:
                display_name = str(
                    candidate_pickaxe.get("displayName")
                    or candidate_pickaxe.get("name")
                    or "ツルハシ"
                )
                equip_step = f"所持ツルハシ（{display_name}）を装備する"
                equip_handled, _, equip_failure = await orchestrator._handle_action_task(  # type: ignore[attr-defined]
                    "equip",
                    equip_step,
                    last_target_coords=state.get("last_target_coords"),
                    backlog=state["backlog"],
                )
                if not equip_handled:
                    return {
                        "handled": False,
                        "updated_target": state.get("last_target_coords"),
                        "failure_detail": equip_failure,
                    }
                return {
                    "handled": True,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": None,
                }

            resp = await orchestrator.actions.mine_ores(  # type: ignore[attr-defined]
                mining_request["targets"],
                scan_radius=mining_request["scan_radius"],
                max_targets=mining_request["max_targets"],
            )
            if resp.get("ok"):
                return {
                    "handled": True,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": None,
                }

            error_detail = resp.get("error") or "鉱石採掘コマンドが拒否されました"
            return {
                "handled": False,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": f"採掘コマンドが失敗しました: {error_detail}",
            }

        async def handle_building(state: _ActionState) -> Dict[str, Any]:
            return self._building_processor.process(state)

        async def handle_defense(state: _ActionState) -> Dict[str, Any]:
            backlog = state["backlog"]
            label = state.get("rule_label") or "戦闘行動"
            backlog.append(
                {
                    "category": "fight",
                    "step": state["step"],
                    "label": label,
                    "module": "defense",
                    "role": state.get("active_role", ""),
                }
            )
            return {
                "handled": True,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": None,
            }

        async def handle_generic(state: _ActionState) -> Dict[str, Any]:
            if state.get("rule_implemented"):
                orchestrator.logger.info(  # type: ignore[attr-defined]
                    "action category=%s has implemented flag but no handler step='%s'",
                    state["category"],
                    state["step"],
                )
                return {
                    "handled": False,
                    "updated_target": state.get("last_target_coords"),
                    "failure_detail": None,
                }

            backlog = state["backlog"]
            backlog.append(
                {
                    "category": state["category"],
                    "step": state["step"],
                    "label": state.get("rule_label") or state["category"],
                    "role": state.get("active_role", ""),
                }
            )
            orchestrator.logger.info(  # type: ignore[attr-defined]
                "action category=%s queued to backlog (unimplemented) step='%s'",
                state["category"],
                state["step"],
            )
            return {
                "handled": True,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": None,
            }

        def finalize(state: _ActionState) -> Dict[str, Any]:
            if state.get("updated_target") is None:
                return {"updated_target": state.get("last_target_coords")}
            return {}

        graph.add_node("initialize", wrap_for_logging("initialize_action", initialize))
        graph.add_node("seek_skill", wrap_for_logging("seek_skill", seek_skill))
        graph.add_node(
            "apply_role_policy",
            wrap_for_logging("apply_role_policy", apply_role_policy),
        )
        graph.add_node("route_module", wrap_for_logging("route_module", route_module))
        graph.add_node(
            "trigger_exploration",
            wrap_for_logging("trigger_exploration", trigger_exploration),
        )
        graph.add_node("handle_move", wrap_for_logging("handle_move", handle_move_node))
        graph.add_node("handle_equip", wrap_for_logging("handle_equip", handle_equip))
        graph.add_node("handle_mining", wrap_for_logging("handle_mining", handle_mining))
        graph.add_node(
            "handle_building",
            wrap_for_logging("handle_building", handle_building),
        )
        graph.add_node(
            "handle_defense",
            wrap_for_logging("handle_defense", handle_defense),
        )
        graph.add_node(
            "handle_generic",
            wrap_for_logging("handle_generic", handle_generic),
        )
        graph.add_node("finalize", wrap_for_logging("finalize_action", finalize))

        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "seek_skill")
        graph.add_edge("apply_role_policy", "route_module")
        graph.add_conditional_edges(
            "seek_skill",
            lambda state: state.get("skill_status", "none"),
            {
                "handled": "finalize",
                "failed": "finalize",
                "locked": "trigger_exploration",
                "exploration": "finalize",
                "none": "apply_role_policy",
            },
        )
        graph.add_conditional_edges(
            "route_module",
            lambda state: state.get("module", "generic"),
            {
                "move": "handle_move",
                "equip": "handle_equip",
                "mining": "handle_mining",
                "building": "handle_building",
                "defense": "handle_defense",
                "generic": "handle_generic",
            },
        )
        graph.add_edge("handle_move", "finalize")
        graph.add_edge("handle_equip", "finalize")
        graph.add_edge("handle_mining", "finalize")
        graph.add_edge("handle_building", "finalize")
        graph.add_edge("handle_defense", "finalize")
        graph.add_edge("handle_generic", "finalize")
        graph.add_edge("trigger_exploration", "finalize")
        graph.add_edge("finalize", END)

        return graph.compile()


__all__ = ["ActionGraphBuilder", "_ActionState"]
