# -*- coding: utf-8 -*-
"""LangGraph を用いたタスクキュー処理のモジュール化補助。"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from skills import SkillMatch, SkillNode
from services.building_service import (
    BuildingPhase,
    advance_building_state,
    checkpoint_to_dict,
    restore_checkpoint,
)
from services.minedojo_client import (
    MineDojoClient,
    MineDojoDemoMetadata,
    MineDojoDemonstration,
    MineDojoMission,
)
from services.skill_repository import SkillRepository

from utils import (
    ThoughtActionObservationTracer,
    log_structured_event,
    setup_logger,
    span_context,
)
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


def build_reflection_prompt(
    failed_step: str,
    failure_reason: str,
    *,
    detection_reports: Sequence[Dict[str, Any]] = (),
    action_backlog: Sequence[Dict[str, Any]] = (),
    previous_reflections: Sequence[Dict[str, Any]] = (),
) -> str:
    """再計画時に渡す Reflexion プロンプトを生成する補助関数。"""

    lines: List[str] = [
        "以下の障壁を踏まえた再計画を提案してください。",
        f"失敗したステップ: {failed_step}",
        f"失敗理由: {failure_reason}",
    ]

    if detection_reports:
        lines.append("関連ステータス報告:")
        for report in detection_reports:
            summary = str(report.get("summary") or report.get("category") or "").strip()
            if summary:
                lines.append(f"- {summary}")

    if action_backlog:
        lines.append("未消化のアクション候補:")
        for item in action_backlog:
            label = str(
                item.get("label")
                or item.get("step")
                or item.get("category")
                or "未分類のアクション"
            ).strip()
            if label:
                lines.append(f"- {label}")

    if previous_reflections:
        lines.append("過去の反省ログ:")
        for entry in previous_reflections:
            improvement = str(entry.get("improvement") or "改善案未記録").strip()
            retry_result = str(entry.get("retry_result") or "結果未記録").strip()
            lines.append(f"- {improvement} / 再試行結果: {retry_result}")

    lines.append(
        "同じ失敗を繰り返さないよう、具体的な改善ポイントを含む計画ステップを提示してください。"
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class ActionTaskRule:
    """行動系タスクをカテゴリ別に整理するためのルール定義。"""

    keywords: Tuple[str, ...]
    hints: Tuple[str, ...] = ()
    label: str = ""
    implemented: bool = False
    priority: int = 0


_ActionState = UnifiedPlanState


async def _refresh_inventory_snapshot(
    orchestrator: "AgentOrchestrator",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """最新の所持品情報を Mineflayer から取得し、メモリへ反映する。"""

    # gather_status("inventory") の存在を確認して、Mineflayer 連携が無効な環境で
    # 余計なエラーを発生させないようにする。新規参画者が初期設定を失念しても
    # 障害内容を明示できるように分岐を設けている。テスト環境では事前に
    # Memory へ差し込んだスナップショットを尊重し、完全オフラインでも
    # 装備推論を継続できるようにする。
    if not hasattr(orchestrator.actions, "gather_status"):
        cached_inventory = orchestrator.memory.get("inventory_detail")  # type: ignore[attr-defined]
        if isinstance(cached_inventory, dict):
            summary = orchestrator._summarize_inventory_status(cached_inventory)  # type: ignore[attr-defined]
            orchestrator.memory.set("inventory", summary)  # type: ignore[attr-defined]
            orchestrator.memory.set("inventory_detail", cached_inventory)  # type: ignore[attr-defined]
            return True, cached_inventory, None

        return False, {}, "Mineflayer 側で所持品取得 API が有効化されていません。"

    try:
        resp = await orchestrator.actions.gather_status("inventory")  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
        orchestrator.logger.exception(
            "inventory refresh failed via gather_status",
            exc_info=exc,
        )
        return False, {}, "所持品の再取得中に予期しない例外が発生しました。"

    if not isinstance(resp, dict) or not resp.get("ok"):
        error_detail = "Mineflayer が所持品を返しませんでした。"
        if isinstance(resp, dict):
            error_detail = str(resp.get("error") or error_detail)
        return False, {}, error_detail

    data = resp.get("data")
    if not isinstance(data, dict):
        data = {}

    summary = orchestrator._summarize_inventory_status(data)  # type: ignore[attr-defined]
    orchestrator.memory.set("inventory", summary)  # type: ignore[attr-defined]
    orchestrator.memory.set("inventory_detail", data)  # type: ignore[attr-defined]

    return True, data, None


class ActionGraph:
    """AgentOrchestrator 内のアクションタスク処理を LangGraph へ委譲する補助クラス。"""

    def __init__(self, orchestrator: "AgentOrchestrator") -> None:
        self._orchestrator = orchestrator
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
            "perception_summary": orchestrator.memory.get("perception_summary"),
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

        def _with_metadata(
            state: _ActionState,
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

        def _wrap_for_logging(label: str, func):
            async def _runner(state: _ActionState):
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
                    outputs = {k: result.get(k) for k in ("handled", "module", "skill_status", "failure_detail") if k in result}
                    result.update(
                        record_structured_step(
                            state,
                            step_label=label,
                            inputs={"category": state.get("category"), "step": state.get("step")},
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
            return _with_metadata(
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
                return _with_metadata(
                    state,
                    step_label="seek_skill",
                    base={"skill_status": "none"},
                    inputs={"step": step},
                    outputs={"skill_status": "none"},
                )
            match = await orchestrator._find_skill_for_step(category, step)  # type: ignore[attr-defined]
            if match is None:
                return _with_metadata(
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
                    return _with_metadata(
                        state,
                        step_label="seek_skill",
                        base={"skill_status": "none"},
                        inputs={"category": category, "step": step},
                        outputs={"skill_status": "none"},
                    )
                handled, failure_detail = await orchestrator._execute_skill_match(match, step)  # type: ignore[attr-defined]
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
                return _with_metadata(
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
                return _with_metadata(
                    state,
                    step_label="seek_skill",
                    base={"skill_status": "none"},
                    inputs={"category": category, "step": step},
                    outputs={"skill_status": "none"},
                )
            return _with_metadata(
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
            return _with_metadata(
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
            return _with_metadata(
                state,
                step_label="route_module",
                base={"module": module},
                inputs={"category": category},
                outputs={"module": module},
            )

        async def trigger_exploration(state: _ActionState) -> Dict[str, Any]:
            match = state.get("skill_candidate")
            if not isinstance(match, SkillMatch):
                return _with_metadata(
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
            handled, failure_detail = await orchestrator._begin_skill_exploration(match, state["step"])  # type: ignore[attr-defined]
            status = "exploration" if handled else "failed"
            base = {
                "handled": handled,
                "failure_detail": failure_detail,
                "updated_target": state.get("last_target_coords"),
                "skill_status": status,
            }
            return _with_metadata(
                state,
                step_label="trigger_exploration",
                base=base,
                inputs={"step": state.get("step")},
                outputs={"skill_status": status},
                error=failure_detail,
            )

        async def handle_move(state: _ActionState) -> Dict[str, Any]:
            step = state["step"]
            explicit_coords = state.get("explicit_coords")
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
                await orchestrator._report_execution_barrier(  # type: ignore[attr-defined]
                    step,
                    "指示文から移動先の座標を特定できず、実行を継続できませんでした。文章に XYZ 形式の座標を含めてください。",
                )
                return {
                    "handled": False,
                    "updated_target": last_target,
                    "failure_detail": "移動先の座標が不明です。",
                }

            move_ok, move_error = await orchestrator._move_to_coordinates(target)  # type: ignore[attr-defined]
            if used_default:
                await orchestrator._report_execution_barrier(  # type: ignore[attr-defined]
                    step,
                    "指示文から移動先の座標を特定できず、既定座標へ退避しました。文章に XYZ 形式の座標を含めてください。",
                )
            if not move_ok:
                error_detail = move_error or "Mineflayer 側で移動が拒否されました"
                return {
                    "handled": False,
                    "updated_target": last_target,
                    "failure_detail": error_detail,
                }
            if isinstance(hunger_level, (int, float)) and hunger_level <= orchestrator.low_food_threshold:  # type: ignore[attr-defined]
                # コンテキストを LangGraph の後続ノードへ共有し、空腹時のフォローアップを促す。
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

        async def handle_equip(state: _ActionState) -> Dict[str, Any]:
            step = state["step"]
            equip_args = orchestrator._infer_equip_arguments(step)  # type: ignore[attr-defined]
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

            refresh_ok, inventory_detail, refresh_error = await _refresh_inventory_snapshot(
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
                retry_refresh_ok, retry_inventory, retry_error = await _refresh_inventory_snapshot(
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
            mining_request = orchestrator._infer_mining_request(step)  # type: ignore[attr-defined]
            candidate_pickaxe = orchestrator._select_pickaxe_for_targets(  # type: ignore[attr-defined]
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
            # Mineflayer 側の建築アクションは段階的に拡張する予定のため、LangGraph 側では
            # 純粋関数ベースで資材調達と配置計画を立て、ジョブの中断・再開が安全に行える
            # ようにする。副作用を伴う操作は最終的に別ノードへ移譲し、ここでは計画のみ
            # を算出する方針。
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
            placement_snapshot = [
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
            # LangGraph のビルド系ノードは障害復旧時の分析が重要なため、
            # イベントレベルや配置バッチを構造化ログで追跡する。
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

        graph.add_node("initialize", _wrap_for_logging("initialize_action", initialize))
        graph.add_node("seek_skill", _wrap_for_logging("seek_skill", seek_skill))
        graph.add_node(
            "apply_role_policy",
            _wrap_for_logging("apply_role_policy", apply_role_policy),
        )
        graph.add_node("route_module", _wrap_for_logging("route_module", route_module))
        graph.add_node(
            "trigger_exploration",
            _wrap_for_logging("trigger_exploration", trigger_exploration),
        )
        graph.add_node("handle_move", _wrap_for_logging("handle_move", handle_move))
        graph.add_node("handle_equip", _wrap_for_logging("handle_equip", handle_equip))
        graph.add_node("handle_mining", _wrap_for_logging("handle_mining", handle_mining))
        graph.add_node(
            "handle_building",
            _wrap_for_logging("handle_building", handle_building),
        )
        graph.add_node(
            "handle_defense",
            _wrap_for_logging("handle_defense", handle_defense),
        )
        graph.add_node(
            "handle_generic",
            _wrap_for_logging("handle_generic", handle_generic),
        )
        graph.add_node("finalize", _wrap_for_logging("finalize_action", finalize))

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
