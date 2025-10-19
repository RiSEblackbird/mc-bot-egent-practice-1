# -*- coding: utf-8 -*-
"""LangGraph を用いたタスクキュー処理のモジュール化補助。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TypedDict, TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

if TYPE_CHECKING:
    from agent import AgentOrchestrator


@dataclass
class ChatTask:
    """Node 側から渡されるチャット指示をキュー化する際のデータ構造。"""

    username: str
    message: str


@dataclass(frozen=True)
class ActionTaskRule:
    """行動系タスクをカテゴリ別に整理するためのルール定義。"""

    keywords: Tuple[str, ...]
    hints: Tuple[str, ...] = ()
    label: str = ""
    implemented: bool = False
    priority: int = 0


class _ActionState(TypedDict, total=False):
    """LangGraph のステート: 行動カテゴリの処理に必要な情報を集約する。"""

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
        }
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
            return {
                "handled": False,
                "updated_target": state.get("last_target_coords"),
                "failure_detail": None,
                "module": "generic",
            }

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
            return {"module": module}

        async def handle_move(state: _ActionState) -> Dict[str, Any]:
            step = state["step"]
            explicit_coords = state.get("explicit_coords")
            last_target = state.get("last_target_coords")
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

            error_detail = resp.get("error") or "Mineflayer 側の理由不明な拒否"
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
            # 建築ノードはまだ Mineflayer 側の実装が乏しいため、backlog に整理して
            # プレイヤーへ進捗報告できるようにする。Graph 内でモジュール名を保持する
            # ことで、未実装カテゴリの可視化を容易にする。
            backlog = state["backlog"]
            label = state.get("rule_label") or "建築作業"
            backlog.append(
                {
                    "category": "build",
                    "step": state["step"],
                    "label": label,
                    "module": "building",
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

        graph.add_node("initialize", initialize)
        graph.add_node("route_module", route_module)
        graph.add_node("handle_move", handle_move)
        graph.add_node("handle_equip", handle_equip)
        graph.add_node("handle_mining", handle_mining)
        graph.add_node("handle_building", handle_building)
        graph.add_node("handle_defense", handle_defense)
        graph.add_node("handle_generic", handle_generic)
        graph.add_node("finalize", finalize)

        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "route_module")
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
        graph.add_edge("finalize", END)

        return graph.compile()


__all__ = ["ActionGraph", "ActionTaskRule", "ChatTask"]
