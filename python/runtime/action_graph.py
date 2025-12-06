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

from langgraph.graph.state import CompiledStateGraph

from services.minedojo_client import MineDojoDemoMetadata

from runtime.action_graph_builder import ActionGraphBuilder
from runtime.unified_agent_graph import UnifiedAgentGraph
from utils import span_context
from langgraph_state import UnifiedPlanState

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
        self._graph: CompiledStateGraph = ActionGraphBuilder(orchestrator).build()

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


__all__ = [
    "ActionGraph",
    "ActionTaskRule",
    "ChatTask",
    "UnifiedAgentGraph",
]
