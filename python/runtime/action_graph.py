# -*- coding: utf-8 -*-
"""LangGraph を用いたタスクキュー処理のモジュール化補助。

AgentOrchestrator からノード定義とステート初期化を切り出し、
LangGraph の振る舞いを単体で検証しやすい構成にしている。
Mineflayer 連携やスキル探索は依存注入された orchestrator 経由で
行い、runtime 層は共通ユーティリティへの一方向依存にとどめる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from skills import SkillMatch
from services.minedojo_client import MineDojoDemoMetadata

from runtime.action_graph_utils import with_metadata, wrap_for_logging
from runtime.building_plan_processor import BuildingPlanProcessor
from runtime.move_handler import handle_move
from utils import span_context
from langgraph_state import UnifiedPlanState, record_structured_step
from planner import PlanOut, plan

if TYPE_CHECKING:
    from agent import AgentOrchestrator


@dataclass
class ChatTask:
    """Node 側から渡されるチャット指示をキュー化する際のデータ構造。"""

    username: str
    message: str
    # worker() のタイムアウト再試行回数を記録し、無限リトライを防止する。
    retry_count: int = 0


@dataclass(frozen=True)
class ActionTaskRule:
    """行動系タスクをカテゴリ別に整理するためのルール定義。"""

    keywords: Tuple[str, ...]
    hints: Tuple[str, ...] = ()
    label: str = ""
    implemented: bool = False
    priority: int = 0


_ActionState = UnifiedPlanState


class ActionGraph:
    """AgentOrchestrator 内のアクションタスク処理を LangGraph へ委譲する補助クラス。"""

    def __init__(self, orchestrator: "AgentOrchestrator") -> None:
        self._orchestrator = orchestrator
        self._building_processor = BuildingPlanProcessor(orchestrator)
        self._graph: CompiledStateGraph = self._build_graph()

    async def run(
        self,
        *,
        category: str,
        step: str,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        rule: ActionTaskRule,
        explicit_coords: Optional[Tuple[int, int, int]] = None,
        structured_event_history: Optional[List[Dict[str, Any]]] = None,
        perception_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        """LangGraph を実行し、処理結果を元のインターフェースへ変換する。"""

        state: _ActionState = {
            "category": category,
            "step": step,
            "last_target_coords": last_target_coords,
            "explicit_coords": explicit_coords,
            "backlog": backlog,
            "rule_label": rule.label or category,
            "rule_implemented": rule.implemented,
            "active_role": self._orchestrator.current_role,
            "role_transitioned": False,
            "structured_events": [],
            "structured_event_history": list(structured_event_history or []),
            "perception_history": list(perception_history or []),
            "perception_summary": self._orchestrator.memory.get("perception_summary"),
            "minedojo_demo_metadata": None,
        }
        metadata = getattr(self._orchestrator, "_active_minedojo_demo_metadata", None)
        if isinstance(metadata, MineDojoDemoMetadata):
            state["minedojo_demo_metadata"] = metadata.to_dict()
        with span_context(
            "langgraph.action_graph.run",
            langgraph_node_id="action_graph",
            event_level="info",
            attributes={
                "action.category": category,
                "action.step": step,
                "action.role": self._orchestrator.current_role,
            },
        ):
            result = await self._graph.ainvoke(state)

        handled = bool(result.get("handled"))
        updated_target = result.get("updated_target", last_target_coords)
        failure_detail = result.get("failure_detail")
        if updated_target is None and last_target_coords is not None:
            updated_target = last_target_coords
        return handled, updated_target, failure_detail

    def _build_graph(self) -> CompiledStateGraph:
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
                    # Mineflayer 側で未登録スキルだった場合は skill_status を none に戻し、
                    # LangGraph が通常の装備・採掘ヒューリスティックへ遷移できるようにする。
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
            elif category == "move":
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
                await orchestrator._report_execution_barrier(  # type: ignore[attr-defined]
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
                await orchestrator._report_execution_barrier(step, reason)  # type: ignore[attr-defined]
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
                await orchestrator._report_execution_barrier(step, reason)  # type: ignore[attr-defined]
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
                # Mineflayer 側で装備対象が欠品している場合は、即座に最新の所持品を
                # 取得し直してメモリへ反映する。これにより障壁通知や再計画時の
                # コンテキストへ「ツルハシを持っていない」という事実を正確に渡せる。
                retry_refresh_ok, retry_inventory, retry_error = await orchestrator.inventory_sync.refresh(  # type: ignore[attr-defined]
                    orchestrator
                )
                if not retry_refresh_ok:
                    # 所持品の再取得にも失敗した場合は、以降の判断材料として誤情報が
                    # 残らないよう関連メモリをリセットしておく。
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

                await orchestrator._report_execution_barrier(  # type: ignore[attr-defined]
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
            # 採掘ノードでは、所持ツルハシの再利用と Mineflayer への mineOre 発行を
            # グラフ内で完結させる。既存のヒューリスティックを呼び出して装備推論と
            # リトライ分岐を確保するため、必要に応じて equip ノードへ再帰委譲する。
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
            # 防衛系の指示も backlog として扱い、今後 Mineflayer 側に戦闘コマンドが
            # 追加された際に LangGraph のノード差し替えだけで拡張できるようにしている。
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


class UnifiedAgentGraph:
    """意図解析から Mineflayer API までを LangGraph で直列に実行する統合グラフ。"""

    def __init__(self, orchestrator: "AgentOrchestrator") -> None:
        self._orchestrator = orchestrator
        self._graph: CompiledStateGraph = self._build_graph()

    async def run(self, user_msg: str, context: Dict[str, Any]) -> UnifiedPlanState:
        """ユーザー発話を起点に統合 LangGraph を実行する。"""

        structured_events, perception_history = self._orchestrator._collect_recent_mineflayer_context()  # type: ignore[attr-defined]
        initial_state: UnifiedPlanState = {
            "user_msg": user_msg,
            "context": context,
            "structured_events": [],
            "structured_event_history": structured_events,
            "perception_history": perception_history,
        }
        return await self._graph.ainvoke(initial_state)

    def render_mermaid(self) -> str:
        """Mermaid 文字列を生成して可視化するためのヘルパー。"""

        return self._graph.get_graph().draw_mermaid()

    def _detect_intent(self, message: str) -> str:
        """簡易なキーワードマッチで意図カテゴリを推定する。"""

        lowered = message.lower()
        for category, rule in self._orchestrator._ACTION_TASK_RULES.items():  # type: ignore[attr-defined]
            for keyword in rule.keywords:
                if keyword and keyword.lower() in lowered:
                    return category
        return "generic"

    def _build_graph(self) -> CompiledStateGraph:
        orchestrator = self._orchestrator
        graph: StateGraph = StateGraph(UnifiedPlanState)

        async def analyze_intent(state: UnifiedPlanState) -> Dict[str, Any]:
            intent = self._detect_intent(state.get("user_msg", ""))
            outputs = {"intent": intent}
            result: Dict[str, Any] = {"category": intent, "step": state.get("user_msg", "")}
            result.update(
                record_structured_step(
                    state,
                    step_label="analyze_intent",
                    inputs={"user_msg": state.get("user_msg")},
                    outputs=outputs,
                )
            )
            return result

        async def generate_plan(state: UnifiedPlanState) -> Dict[str, Any]:
            try:
                plan_out = await plan(state.get("user_msg", ""), state.get("context", {}))
                category = plan_out.intent or state.get("category") or "generic"
                step_text = plan_out.plan[0] if plan_out.plan else state.get("step", "")
                result: Dict[str, Any] = {
                    "plan_out": plan_out,
                    "category": category,
                    "step": step_text,
                }
                result.update(
                    record_structured_step(
                        state,
                        step_label="generate_plan",
                        inputs={"user_msg": state.get("user_msg"), "context_keys": list(state.get("context", {}).keys())},
                        outputs={"intent": category, "plan_steps": len(plan_out.plan)},
                    )
                )
                return result
            except Exception as exc:  # pragma: no cover - 例外時は次ノードで回復
                fallback = PlanOut(plan=[], resp="了解しました。")
                result = {
                    "plan_out": fallback,
                    "category": state.get("category", "generic"),
                    "step": state.get("step", ""),
                    "parse_error": str(exc),
                }
                result.update(
                    record_structured_step(
                        state,
                        step_label="generate_plan",
                        inputs={"user_msg": state.get("user_msg")},
                        outputs={"intent": result["category"], "plan_steps": len(fallback.plan)},
                        error=str(exc),
                    )
                )
                return result

        async def dispatch_action(state: UnifiedPlanState) -> Dict[str, Any]:
            category = state.get("category") or "generic"
            plan_out = state.get("plan_out")
            step_text = state.get("step") or state.get("user_msg", "")
            if isinstance(plan_out, PlanOut) and plan_out.plan:
                step_text = plan_out.plan[0]
            backlog = state.get("backlog") or []
            handled, updated_target, failure_detail = await orchestrator._handle_action_task(  # type: ignore[attr-defined]
                category,
                step_text or "",
                last_target_coords=state.get("last_target_coords"),
                backlog=backlog,
                explicit_coords=state.get("explicit_coords"),
            )
            result: Dict[str, Any] = {
                "handled": handled,
                "updated_target": updated_target,
                "failure_detail": failure_detail,
                "backlog": backlog,
            }
            result.update(
                record_structured_step(
                    state,
                    step_label="dispatch_action",
                    inputs={"category": category, "step": step_text},
                    outputs={"handled": handled, "failure_detail": failure_detail},
                    error=failure_detail,
                )
            )
            return result

        async def mineflayer_node(state: UnifiedPlanState) -> Dict[str, Any]:
            plan_out = state.get("plan_out")
            response_text = ""
            error: Optional[str] = None
            say_result: Optional[Any] = None
            if isinstance(plan_out, PlanOut):
                response_text = plan_out.resp
            if hasattr(orchestrator.actions, "say") and response_text:
                try:
                    say_result = await orchestrator.actions.say(response_text)  # type: ignore[attr-defined]
                except Exception as exc:  # pragma: no cover - Mineflayer 例外は上位で観測
                    error = str(exc)
            result: Dict[str, Any] = {
                "final_response": response_text,
                "say_result": say_result,
            }
            result.update(
                record_structured_step(
                    state,
                    step_label="mineflayer_node",
                    inputs={"response_text": response_text[:80]},
                    outputs={"say_result": bool(say_result)},
                    error=error,
                )
            )
            return result

        graph.add_node("analyze_intent", analyze_intent)
        graph.add_node("generate_plan", generate_plan)
        graph.add_node("dispatch_action", dispatch_action)
        graph.add_node("mineflayer_node", mineflayer_node)

        graph.add_edge(START, "analyze_intent")
        graph.add_edge("analyze_intent", "generate_plan")
        graph.add_edge("generate_plan", "dispatch_action")
        graph.add_edge("dispatch_action", "mineflayer_node")
        graph.add_edge("mineflayer_node", END)

        return graph.compile()


__all__ = [
    "ActionGraph",
    "ActionTaskRule",
    "ChatTask",
    "UnifiedAgentGraph",
]
