from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from langgraph_state import UnifiedPlanState, record_structured_step
from planner import PlanOut, plan


class UnifiedAgentGraph:
    """意図解析から Mineflayer API までを LangGraph で直列に実行する統合グラフ。"""

    def __init__(self, orchestrator: Any) -> None:
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
            error: Optional[str] = None  # type: ignore[assignment]
            say_result: Optional[Any] = None  # type: ignore[assignment]
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


__all__ = ["UnifiedAgentGraph"]
