# -*- coding: utf-8 -*-
# gpt-5-mini を用いたプランニング：自然文→PLAN/RESP の二分出力
import asyncio
import os
from typing import Any, Dict, List, Optional, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from opentelemetry.trace import Status, StatusCode

from utils import setup_logger, span_context
from langgraph_state import UnifiedPlanState, record_recovery_hints, record_structured_step
from dotenv import load_dotenv
import openai
from openai.types.responses import EasyInputMessageParam, Response
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from config import load_agent_config

# pytest でのモンキーパッチ互換性を維持するため、従来のエイリアスを公開しておく。
AsyncOpenAI = openai.AsyncOpenAI

logger = setup_logger("planner")
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# AgentConfig は一度だけ読み込み、Responses API へのタイムアウト秒数を
# LangGraph 内のノードから参照できるようモジュールレベルで公開する。
_PLANNER_CONFIG_RESULT = load_agent_config()
LLM_TIMEOUT_SECONDS = _PLANNER_CONFIG_RESULT.config.llm_timeout_seconds

# OPENAI_BASE_URL を安全に正規化する。
#   - スキームが欠けていれば http:// を補完して警告を表示
#   - 期待される形式: https://api.openai.com/v1 のような完全な URL
raw_base_url = os.getenv("OPENAI_BASE_URL")
if raw_base_url:
    normalized_base_url = raw_base_url.strip()
    if normalized_base_url:
        parsed_url = urlparse(normalized_base_url)
        if not parsed_url.scheme:
            auto_prefixed_url = f"http://{normalized_base_url}"
            parsed_auto_prefixed = urlparse(auto_prefixed_url)
            if not parsed_auto_prefixed.scheme:
                raise ValueError(
                    "OPENAI_BASE_URL にはスキームを含めた完全な URL を指定してください (例: https://api.openai.com/v1)"
                )
            logger.warning(
                "OPENAI_BASE_URL にスキームが指定されていなかったため http:// を補完しました。"
                " 期待される形式の例: https://api.openai.com/v1"
            )
            normalized_base_url = auto_prefixed_url
        openai.base_url = normalized_base_url

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_TEMPERATURE = 0.3

GPT5_MODEL_PREFIX = "gpt-5"
ALLOWED_VERBOSITY_LEVELS = {"low", "medium", "high"}
ALLOWED_REASONING_EFFORT = {"low", "medium", "high"}

# gpt-5-mini をはじめとした一部のモデルは温度固定で API が受け付けないため、
# 送信時には temperature フィールドを省略する必要がある。
TEMPERATURE_LOCKED_MODELS = {"gpt-5-mini"}


class PlanPriorityManager:
    """LLM 連携の成功/失敗に応じて優先度を調整するシンプルな状態管理。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._priority = "normal"

    async def mark_success(self) -> str:
        async with self._lock:
            self._priority = "normal"
            return self._priority

    async def mark_failure(self) -> str:
        async with self._lock:
            self._priority = "high"
            return self._priority

    async def snapshot(self) -> str:
        async with self._lock:
            return self._priority


_PRIORITY_MANAGER = PlanPriorityManager()
_PLAN_GRAPH: Optional[CompiledStateGraph] = None
_PlanState = UnifiedPlanState


def is_gpt5_family(model: str) -> bool:
    """モデル名が gpt-5 系統かどうかを判定する。"""

    return model.startswith(GPT5_MODEL_PREFIX)


def resolve_gpt5_verbosity(model: str) -> Optional[str]:
    """gpt-5 系モデル向けの verbosity パラメータを環境変数から決定する。"""

    if not is_gpt5_family(model):
        return None

    raw = os.getenv("OPENAI_VERBOSITY")
    if not raw:
        return None

    value = raw.strip().lower()
    if value not in ALLOWED_VERBOSITY_LEVELS:
        logger.warning(
            "OPENAI_VERBOSITY=%s はサポート対象 (low/medium/high) 外のため送信しません。", raw
        )
        return None

    return value


def resolve_gpt5_reasoning_effort(model: str) -> Optional[str]:
    """gpt-5 系モデル向けの reasoning.effort を環境変数から決定する。"""

    if not is_gpt5_family(model):
        return None

    raw = os.getenv("OPENAI_REASONING_EFFORT")
    if not raw:
        return None

    value = raw.strip().lower()
    if value not in ALLOWED_REASONING_EFFORT:
        logger.warning(
            "OPENAI_REASONING_EFFORT=%s はサポート対象 (low/medium/high) 外のため送信しません。",
            raw,
        )
        return None

    return value


def resolve_request_temperature(model: str) -> Optional[float]:
    """LLM へ渡す温度パラメータをモデル仕様に合わせて決定する。

    * gpt-5-mini など温度固定モデルの場合は `None` を返し、API 呼び出し時に
      temperature フィールドを送信しないようにする。
    * `OPENAI_TEMPERATURE` が設定された場合は 0.0～2.0 の範囲に正規化し、
      無効値は既定値へフォールバックする。

    Args:
        model: 利用する OpenAI モデル名。

    Returns:
        API へ渡す温度 (float) または送信不要な場合は None。
    """

    raw_temperature = os.getenv("OPENAI_TEMPERATURE")

    if model in TEMPERATURE_LOCKED_MODELS:
        if raw_temperature:
            logger.warning(
                "OPENAI_TEMPERATURE=%s が設定されていますが、%s は温度固定モデルのため無視します。",
                raw_temperature,
                model,
            )
        return None

    if not raw_temperature:
        return DEFAULT_TEMPERATURE

    try:
        requested = float(raw_temperature)
    except ValueError:
        logger.warning(
            "OPENAI_TEMPERATURE=%s は数値として解釈できません。既定値 %.2f にフォールバックします。",
            raw_temperature,
            DEFAULT_TEMPERATURE,
        )
        return DEFAULT_TEMPERATURE

    if not 0.0 <= requested <= 2.0:
        logger.warning(
            "OPENAI_TEMPERATURE=%.3f はサポート範囲 (0.0～2.0) 外のため、既定値 %.2f にフォールバックします。",
            requested,
            DEFAULT_TEMPERATURE,
        )
        return DEFAULT_TEMPERATURE

    return requested

# 期待する出力スキーマ（簡易）
class ReActStep(BaseModel):
    """ReAct 形式で LangGraph へ流す 1 ステップ分の思考と行動。"""

    thought: str = ""
    action: str = ""
    observation: str = ""


class PlanArguments(BaseModel):
    """LLM が推定した実行パラメータを型安全に保持するためのスキーマ。"""

    coordinates: Optional[Dict[str, int]] = Field(
        default=None,
        description="移動や採掘の起点となる座標 (X/Y/Z)。",
    )
    quantity: Optional[int] = Field(
        default=None,
        ge=0,
        description="要求された数量（負数は不正値として拒否する）。",
    )
    target: Optional[str] = Field(
        default=None,
        description="対象ブロックやアイテムの名称。",
    )
    notes: Dict[str, Any] = Field(
        default_factory=dict,
        description="補足情報（自由形式）。",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="引数推定の確信度 (0.0～1.0)。",
    )
    clarification_needed: Literal["none", "confirmation", "data_gap"] = Field(
        default="none",
        description="追加確認の種類 (none/confirmation/data_gap)。",
    )
    detected_modalities: List[str] = Field(
        default_factory=list,
        description="入力に含まれるモダリティ（例: text, image）。",
    )


class ConstraintSpec(BaseModel):
    """LLM が検出した制約条件を表す。"""

    label: str = ""
    rationale: str = ""
    severity: Literal["soft", "hard"] = "soft"


class GoalProfile(BaseModel):
    """タスクのゴール要約と優先度を構造化して保持する。"""

    summary: str = ""
    category: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    success_criteria: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


class ExecutionHint(BaseModel):
    """Mineflayer/MineDojo 実行前に共有したいヒントの集合。"""

    key: str = ""
    value: str = ""
    source: str = ""


class ActionDirective(BaseModel):
    """plan[].step と 1:1 で対応する構造化指示。"""

    directive_id: str = ""
    step: str = ""
    label: str = ""
    category: str = ""
    executor: Literal["mineflayer", "minedojo", "chat", "hybrid"] = "mineflayer"
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "executor 固有の追加パラメータ。"
            "hybrid 指示では `vpt_actions` (List[Dict]) と `fallback_command` "
            "(例: {'type': 'moveTo', 'args': {...}}) を期待する。"
        ),
    )
    safety_checks: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    fallback: str = ""


class PlanOut(BaseModel):
    plan: List[str] = Field(default_factory=list)  # 実行ステップ（高レベル）
    resp: str = ""  # プレイヤー向け日本語応答
    intent: str = Field(
        default="",
        description="LLM が推定したメイン意図（例: move/build/gather など）。",
    )
    arguments: PlanArguments = Field(
        default_factory=PlanArguments,
        description="座標や数量などの構造化パラメータ群。",
    )
    blocking: bool = Field(
        default=False,
        description="ユーザー確認が必要な場合に true。false なら即時実行してよい。",
    )
    react_trace: List[ReActStep] = Field(
        default_factory=list,
        description="Responses API から得た ReAct ループの素案。Observation は Mineflayer 実行結果で更新する。",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="プラン全体の確信度 (0.0～1.0)。",
    )
    clarification_needed: Literal["none", "confirmation", "data_gap"] = Field(
        default="none",
        description="追加確認が必要かどうか (none/confirmation/data_gap)。",
    )
    detected_modalities: List[str] = Field(
        default_factory=list,
        description="入力内で認識したモダリティ（text/image など）。",
    )
    backlog: List[Dict[str, str]] = Field(
        default_factory=list,
        description="ActionGraph へ差し戻すためのバックログ候補。",
    )
    next_action: str = Field(
        default="execute",
        description="graph からの推奨遷移 (execute/chat など)。",
    )
    goal_profile: GoalProfile = Field(
        default_factory=GoalProfile,
        description="ゴール要約と優先度。",
    )
    constraints: List[ConstraintSpec] = Field(
        default_factory=list,
        description="実行上の制約条件一覧。",
    )
    execution_hints: List[ExecutionHint] = Field(
        default_factory=list,
        description="Mineflayer/MineDojo への補助ヒント。",
    )
    directives: List[ActionDirective] = Field(
        default_factory=list,
        description="各ステップに対応する構造化指示列。",
    )
    recovery_hints: List[str] = Field(
        default_factory=list,
        description="前回障壁から引き継いだ再計画ヒント。",
    )


class BarrierNotificationError(RuntimeError):
    """障壁通知生成で通信系エラーが発生したことを示す基底例外。"""


class BarrierNotificationTimeout(BarrierNotificationError):
    """Responses API 呼び出しが所定時間内に完了しなかったことを示す例外。"""


class BarrierNotification(BaseModel):
    """障壁通知用のメッセージをパースするためのスキーマ。"""

    message: str = ""


def _build_plan_graph() -> CompiledStateGraph:
    manager = _PRIORITY_MANAGER
    graph: StateGraph = StateGraph(_PlanState)

    async def prepare_payload(state: _PlanState) -> Dict[str, Any]:
        recovery_hints = _extract_recovery_hints_from_context(state)
        if recovery_hints:
            record_recovery_hints(state, recovery_hints)
        prompt = build_user_prompt(state["user_msg"], state["context"])
        logger.info("LLM prompt: %s", prompt)
        payload = _build_responses_payload(SYSTEM, prompt)
        metadata = record_structured_step(
            state,
            step_label="prepare_payload",
            inputs={"user_msg": state.get("user_msg", ""), "context_keys": list(state.get("context", {}).keys())},
            outputs={"prompt_preview": prompt[:120]},
        )
        result: Dict[str, Any] = {"prompt": prompt, "payload": payload}
        result.update(metadata)
        return result

    async def call_llm(state: _PlanState) -> Dict[str, Any]:
        """Responses API を呼び出し、タイムアウト時は安全なフォールバックを返す。"""

        with span_context(
            "llm.responses.create",
            langgraph_node_id="plan.call_llm",
            event_level="info",
            attributes={"llm.model": MODEL},
        ) as span:

            async def _build_failure_payload(reason: str, *, log_as_warning: bool) -> Dict[str, Any]:
                """例外発生時に優先度降格とフォールバックプランを組み立てる。"""

                priority = await manager.mark_failure()
                fallback = PlanOut(plan=[], resp="了解しました。")
                if log_as_warning:
                    logger.warning("plan graph detected LLM timeout: %s", reason)
                else:
                    logger.exception("plan graph failed to call Responses API: %s", reason)
                if span.is_recording():
                    span.set_status(Status(StatusCode.ERROR, reason))
                payload = {
                    "llm_error": reason,
                    "content": "",
                    "priority": priority,
                    "fallback_plan_out": fallback,
                }
                payload.update(
                    record_structured_step(
                        state,
                        step_label="call_llm",
                        inputs={"model": MODEL},
                        outputs={"priority": priority, "fallback": True},
                        error=reason,
                    )
                )
                return payload

            try:
                client = openai.AsyncOpenAI()
                resp = await asyncio.wait_for(
                    client.responses.create(**state["payload"]),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                timeout_reason = f"timeout after {LLM_TIMEOUT_SECONDS:.1f} seconds"
                if span.is_recording():
                    span.set_attribute("llm.timeout_seconds", LLM_TIMEOUT_SECONDS)
                return await _build_failure_payload(timeout_reason, log_as_warning=True)
            except Exception as exc:
                if span.is_recording():
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                return await _build_failure_payload(str(exc), log_as_warning=False)

            content = _extract_output_text(resp)
            logger.info("LLM raw: %s", content)
            payload = {"response": resp, "content": content}
            payload.update(
                record_structured_step(
                    state,
                    step_label="call_llm",
                    inputs={"model": MODEL},
                    outputs={"content_length": len(content)},
                )
            )
            if span.is_recording():
                span.set_attribute("llm.content_length", len(content))
            return payload

    async def parse_plan(state: _PlanState) -> Dict[str, Any]:
        if state.get("llm_error"):
            priority = state.get("priority") or await manager.mark_failure()
            result: Dict[str, Any] = {"parse_error": state["llm_error"], "priority": priority}
            fallback_plan = state.get("fallback_plan_out")
            if fallback_plan is not None:
                result["fallback_plan_out"] = fallback_plan
            result.update(
                record_structured_step(
                    state,
                    step_label="parse_plan",
                    inputs={"has_llm_error": True},
                    outputs={"priority": priority},
                    error=state.get("llm_error", ""),
                )
            )
            return result

        try:
            plan_data = PlanOut.model_validate_json(state.get("content") or "")
        except Exception as exc:
            logger.exception("plan graph failed to parse JSON plan")
            priority = await manager.mark_failure()
            result = {"parse_error": str(exc), "priority": priority}
            result.update(
                record_structured_step(
                    state,
                    step_label="parse_plan",
                    inputs={"content_preview": (state.get("content") or "")[:120]},
                    outputs={"priority": priority},
                    error=str(exc),
                )
            )
            return result

        priority = await manager.mark_success()
        recovery_hints = _extract_recovery_hints_from_context(state)
        if recovery_hints:
            plan_data.recovery_hints = recovery_hints
        result = {"plan_out": plan_data, "priority": priority}
        result.update(
            record_structured_step(
                state,
                step_label="parse_plan",
                inputs={"content_preview": (state.get("content") or "")[:120]},
                outputs={"priority": priority, "intent": plan_data.intent},
            )
        )
        return result

    async def normalize_react_trace(state: _PlanState) -> Dict[str, Any]:
        plan_out = state.get("plan_out")
        if not isinstance(plan_out, PlanOut):
            return {}

        _normalize_directives(plan_out)
        normalized_trace: List[ReActStep] = []
        for entry in plan_out.react_trace:
            # LLM 側が空文字で埋めた場合でも、Action テキストが存在するものだけを残す。
            if not isinstance(entry, ReActStep):
                continue
            thought = entry.thought.strip()
            action = entry.action.strip()
            observation = entry.observation.strip()
            if not action:
                continue
            normalized_trace.append(
                ReActStep(
                    thought=thought,
                    action=action,
                    observation=observation,
                )
            )

        plan_out.react_trace = normalized_trace
        result = {"plan_out": plan_out}
        result.update(
            record_structured_step(
                state,
                step_label="normalize_react_trace",
                inputs={"trace_count": len(plan_out.react_trace)},
                outputs={"normalized_count": len(normalized_trace)},
            )
        )
        return result


def _normalize_directives(plan_out: PlanOut) -> None:
    """plan ステップ数と directive リストを整合させ、欠損フィールドを補完する。"""

    if not isinstance(plan_out.plan, list) or not plan_out.plan:
        plan_out.directives = []
        return

    normalized: List[ActionDirective] = []
    for index, step in enumerate(plan_out.plan):
        directive: ActionDirective
        if index < len(plan_out.directives):
            candidate = plan_out.directives[index]
            directive = candidate if isinstance(candidate, ActionDirective) else ActionDirective()
        else:
            directive = ActionDirective()

        if not directive.step:
            directive.step = step
        if not directive.label:
            directive.label = directive.step
        if not directive.directive_id:
            directive.directive_id = f"step-{index + 1}"

        normalized.append(directive)

    plan_out.directives = normalized


def _extract_recovery_hints_from_context(state: _PlanState) -> List[str]:
    """context や state に含まれる再計画ヒントを安全に取り出す。"""

    hints: List[str] = []
    sources: List[Any] = []
    context = state.get("context")
    if isinstance(context, dict):
        sources.append(context.get("recovery_hints"))
    raw_state = state.get("recovery_hints")
    if raw_state is not None:
        sources.append(raw_state)

    for source in sources:
        if isinstance(source, (list, tuple)):
            for entry in source:
                text = str(entry or "").strip()
                if text:
                    hints.append(text)
        elif isinstance(source, str):
            text = source.strip()
            if text:
                hints.append(text)
    if not hints:
        return []
    # 重複を除去して順序を維持する
    unique: List[str] = []
    seen = set()
    for hint in hints:
        if hint not in seen:
            unique.append(hint)
            seen.add(hint)
    return unique

    async def intent_negotiation(state: _PlanState) -> Dict[str, Any]:
        """曖昧さが残る場合にフォローアップ質問を準備し、バックログへ差し戻す。"""

        plan_out = state.get("plan_out")
        backlog: List[Dict[str, str]] = list(state.get("backlog") or [])
        confirmation_required = False
        follow_up_message = ""

        if isinstance(plan_out, PlanOut):
            follow_up_message = plan_out.resp.strip()
            confirmation_required = bool(
                plan_out.blocking or plan_out.clarification_needed != "none"
            )
            if confirmation_required and follow_up_message:
                backlog.append(
                    {
                        "category": "chat",
                        "label": "ユーザー確認",
                        "message": follow_up_message,
                        "reason": plan_out.clarification_needed
                        or ("blocking" if plan_out.blocking else "none"),
                    }
                )
                plan_out.next_action = "chat"
            else:
                plan_out.next_action = "execute"
            plan_out.backlog = backlog

        result = {
            "plan_out": plan_out,
            "backlog": backlog,
            "confirmation_required": confirmation_required,
            "follow_up_message": follow_up_message,
        }
        result.update(
            record_structured_step(
                state,
                step_label="intent_negotiation",
                inputs={
                    "blocking": getattr(plan_out, "blocking", False),
                    "clarification_needed": getattr(plan_out, "clarification_needed", "none"),
                },
                outputs={
                    "backlog_count": len(backlog),
                    "next_action": getattr(plan_out, "next_action", "execute"),
                    "confirmation_required": confirmation_required,
                },
            )
        )
        return result

    async def route_to_chat(state: _PlanState) -> Dict[str, Any]:
        """確認フローへ進む場合に next_action を chat へ固定する。"""

        plan_out = state.get("plan_out")
        backlog: List[Dict[str, str]] = list(state.get("backlog") or [])
        if isinstance(plan_out, PlanOut):
            plan_out.backlog = backlog
            plan_out.next_action = "chat"

        result = {"plan_out": plan_out, "backlog": backlog, "next_action": "chat"}
        result.update(
            record_structured_step(
                state,
                step_label="route_to_chat",
                inputs={"backlog_count": len(backlog)},
                outputs={"next_action": "chat"},
            )
        )
        return result

    async def fallback_plan(state: _PlanState) -> Dict[str, Any]:
        logger.warning(
            "plan fallback triggered parse_error=%s llm_error=%s",
            state.get("parse_error"),
            state.get("llm_error"),
        )
        fallback = state.get("fallback_plan_out")
        if not isinstance(fallback, PlanOut):
            fallback = PlanOut(plan=[], resp="了解しました。")
        result = {"plan_out": fallback}
        result.update(
            record_structured_step(
                state,
                step_label="fallback_plan",
                inputs={"parse_error": state.get("parse_error"), "llm_error": state.get("llm_error")},
                outputs={"plan_steps": len(fallback.plan)},
            )
        )
        return result

    async def finalize(state: _PlanState) -> Dict[str, Any]:
        priority = state.get("priority")
        if priority:
            logger.info("plan priority resolved=%s", priority)
        if priority is not None:
            return {"priority": priority}
        return {}

    graph.add_node("prepare_payload", prepare_payload)
    graph.add_node("call_llm", call_llm)
    graph.add_node("parse_plan", parse_plan)
    graph.add_node("normalize_react_trace", normalize_react_trace)
    graph.add_node("intent_negotiation", intent_negotiation)
    graph.add_node("route_to_chat", route_to_chat)
    graph.add_node("fallback_plan", fallback_plan)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare_payload")
    graph.add_edge("prepare_payload", "call_llm")
    graph.add_edge("call_llm", "parse_plan")
    graph.add_conditional_edges(
        "parse_plan",
        lambda state: "success" if "plan_out" in state else "failure",
        {"success": "normalize_react_trace", "failure": "fallback_plan"},
    )
    graph.add_conditional_edges(
        "normalize_react_trace",
        lambda state: "needs_chat" if state.get("confirmation_required") else "ready",
        {"ready": "intent_negotiation", "needs_chat": "intent_negotiation"},
    )
    graph.add_conditional_edges(
        "intent_negotiation",
        lambda state: "chat" if state.get("confirmation_required") else "execute",
        {"execute": "finalize", "chat": "route_to_chat"},
    )
    graph.add_edge("route_to_chat", "finalize")
    graph.add_edge("fallback_plan", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def _get_plan_graph() -> CompiledStateGraph:
    global _PLAN_GRAPH
    if _PLAN_GRAPH is None:
        _PLAN_GRAPH = _build_plan_graph()
    return _PLAN_GRAPH


# OpenAI Responses API で response_format=json_object を指定する場合も、
# プロンプト内に "json" という語を含めておくと安定して構造化応答が得られる。
# システムメッセージで明示しておくことで、推論モデルへ JSON 出力を強制する。
SYSTEM = """あなたはMinecraftの自律ボットです。日本語の自然文指示を、
現在の状況を考慮して実行可能な高レベルのステップ列に分解し、同時に
プレイヤーへ返す丁寧な日本語メッセージを用意してください。行動開始
前に許可を求める質問は挟まず、指示された作業に着手する前提で端的に
了承してください。プレイヤーが座標や数量などの具体情報を伝えた場合
は、同じ内容を繰り返し尋ねないでください。曖昧さを定量化するため、
確信度と追加確認の要否を必ず判定し、必要に応じて丁寧なフォローアップ
質問を用意してください。

出力は必ず json 形式のオブジェクトで、キーは "plan": string[], "resp": string,
"intent": string, "arguments": object, "blocking": boolean,
"react_trace": {"thought": string, "action": string, "observation": string}[],
"confidence": number (0.0-1.0), "clarification_needed": "none" | "confirmation" | "data_gap",
"detected_modalities": string[], "backlog": object[], "next_action": string,
"goal_profile": object, "constraints": object[], "execution_hints": object[],
"directives": object[], "recovery_hints": string[] とする。react_trace の observation
は環境からの観測値で後から上書きされるため、空文字列のまま残してください。
thought にはステップを採択した理由を日本語で 1 文以内に要約し、action には
実行する具体的な操作を記述します。"intent" には move/build/gather など主な
行動タイプを、"arguments" には座標や数量、対象名を含む構造化パラメータを
含め、"blocking" は実行前にプレイヤー確認が必要なら true を返してください。

goal_profile には { "summary": "...", "category": "...", "priority": "low|medium|high",
"success_criteria": [], "blockers": [] } を含めてください。constraints は
{ "label": "...", "rationale": "...", "severity": "soft|hard" } の配列とします。
execution_hints には { "key": "...", "value": "...", "source": "memory|perception|user" }
の形式で Mineflayer/MineDojo 実行時に参照したいヒントを列挙してください。

directives は plan の各ステップと 1:1 で並ぶ配列です。要素は
{ "directive_id": "step-1", "step": "プレーンテキストの手順",
  "label": "人間に伝わる短い説明", "category": "move/build/...",
  "executor": "mineflayer|minedojo|chat|hybrid",
  "args": { "coordinates": {"x": -10, "y": 64, "z": 20}, ... },
  "safety_checks": [], "success_criteria": [], "fallback": "失敗時メッセージ" }
のように記述してください。MineDojo シミュレーションに委譲したい場合は executor="minedojo"
を指定し、args.mission_id に候補ミッション ID を含めます。

曖昧さが残る場合は clarification_needed を confirmation（全体方針の確認）
または data_gap（追加データの不足）で指定し、resp に日本語の確認質問を含め、
backlog に chat 用のタスク概要を入れて next_action="chat" としてください。
確認不要なら next_action="execute" とし、confidence は計画の確からしさを
0.0～1.0 で数値化してください。recovery_hints には直近障壁から引き継いだ
教訓を 1 行ずつ列挙し、次回の再計画でも同じ問題を避けられるようにしてください。
"""
BARRIER_SYSTEM = """あなたはMinecraftのサポートボットです。停滞している作業の概要を理解し、
プレイヤーに丁寧で簡潔な日本語メッセージを作成してください。状況説明と、
必要な確認事項や追加指示の依頼を 2 文程度で伝えてください。出力は必ず
json オブジェクトで、キーは "message": string のみを含めてください。"""


def build_barrier_prompt(step: str, reason: str, context: Dict[str, Any]) -> str:
    """障壁情報と補助コンテキストを LLM へ渡すためのプロンプトを生成する。"""

    ctx_lines = [f"- {key}: {value}" for key, value in context.items()]
    ctx_block = "\n".join(ctx_lines)
    return f"""# 現在発生している問題
手順: {step}
原因: {reason}

# 参考情報
{ctx_block}

# 出力要件
状況を説明し、プレイヤーに確認したい事項を丁寧に尋ねてください。
応答は {{"message": "..."}} 形式の json オブジェクトで出力してください。
"""

def build_user_prompt(user_msg: str, context: Dict[str, Any]) -> str:
    # 必要最小限の状態を与える（今後拡張）
    ctx_lines = [f"- {k}: {v}" for k, v in context.items()]
    ctx = "\n".join(ctx_lines)
    return f"""# ユーザーの発話
{user_msg}

# 直近の状況（要約）
{ctx}

# 出力フォーマット
json のみ。例：
{{
  "plan": ["畑へ移動", "小麦を収穫", "パンを作る"],
  "resp": "了解しました。小麦を収穫してパンを作りますね。",
  "intent": "farm",
  "arguments": {{
    "coordinates": {{"x": -10, "y": 64, "z": 20}},
    "quantity": 12,
    "target": "wheat",
    "notes": {{"needs_tools": true}},
    "confidence": 0.82,
    "clarification_needed": "none",
    "detected_modalities": ["text"]
  }},
  "blocking": false,
  "confidence": 0.82,
  "clarification_needed": "none",
  "detected_modalities": ["text"],
  "backlog": [],
  "next_action": "execute",
  "goal_profile": {{
    "summary": "食料不足を解消するための農作業",
    "category": "farm",
    "priority": "medium",
    "success_criteria": ["パンを 6 個以上確保"],
    "blockers": []
  }},
  "constraints": [
    {{"label": "夜間の敵対モブ", "rationale": "畑周辺が暗い", "severity": "soft"}}
  ],
  "execution_hints": [
    {{"key": "inventory.wheat", "value": "0", "source": "memory"}}
  ],
  "directives": [
    {{
      "directive_id": "step-1",
      "step": "畑へ移動",
      "label": "畑まで移動",
      "category": "move",
      "executor": "mineflayer",
      "args": {{"coordinates": {{"x": -10, "y": 64, "z": 20}}}},
      "safety_checks": [],
      "success_criteria": ["畑の入口に到達"]
    }},
    {{
      "directive_id": "step-2",
      "step": "小麦を収穫",
      "label": "成熟小麦の収穫",
      "category": "farm",
      "executor": "mineflayer",
      "args": {{"target": "wheat"}}
    }},
    {{
      "directive_id": "step-3",
      "step": "パンを作る",
      "label": "パンをクラフト",
      "category": "craft",
      "executor": "mineflayer",
      "args": {{"item": "bread", "amount": 6}}
    }}
  ],
  "recovery_hints": [],
  "react_trace": [
    {{"thought": "農作業を開始する準備が必要", "action": "畑へ移動", "observation": ""}},
    {{"thought": "材料を確保する", "action": "小麦を収穫", "observation": ""}},
    {{"thought": "食料を用意する", "action": "パンを作る", "observation": ""}}
  ]
}}
"""

def _build_responses_input(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
    """Responses API へ渡す message 配列を生成する補助関数。

    EasyInputMessageParam を経由して型安全に構築し、辞書へ変換することで
    API 仕様変更が起きてもメッセージ構造の妥当性を確保する。"""

    messages = [
        EasyInputMessageParam(role="system", content=system_prompt),
        EasyInputMessageParam(role="user", content=user_prompt),
    ]

    serialized: List[Dict[str, Any]] = []
    for msg in messages:
        # OpenAI SDK で EasyInputMessageParam の実装が変化した場合でも、
        # Responses API へ渡す辞書構造を破綻させないための安全策。
        if hasattr(msg, "model_dump"):
            serialized.append(msg.model_dump(mode="json", exclude_none=True))
        elif isinstance(msg, dict):
            serialized.append({k: v for k, v in msg.items() if v is not None})
        else:
            serialized.append({
                "role": getattr(msg, "role", ""),
                "content": getattr(msg, "content", ""),
            })

    return serialized


def _build_responses_payload(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Responses API 呼び出しに共通するペイロードを一元生成する。

    * text.format へ json_object を指定し、Responses API 側で JSON 出力を強制
    * gpt-5 系パラメータ（temperature / verbosity / reasoning.effort）の
      解決ロジックを集中させ、plan() / compose_barrier_notification() の
      重複を無くす
    """

    payload: Dict[str, Any] = {
        "model": MODEL,
        "input": _build_responses_input(system_prompt, user_prompt),
        "text": {"format": {"type": "json_object"}},
    }

    temperature = resolve_request_temperature(MODEL)
    if temperature is not None:
        payload["temperature"] = temperature

    verbosity = resolve_gpt5_verbosity(MODEL)
    if verbosity:
        # Responses API では text.verbosity を使って詳細度を制御する。
        payload["text"]["verbosity"] = verbosity

    reasoning_effort = resolve_gpt5_reasoning_effort(MODEL)
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

    return payload


def _extract_output_text(response: Response) -> str:
    """Responses API の出力から JSON 本文を安全に取り出す。

    output_text プロパティが利用可能な場合はそれを優先し、存在しないケース
    ではメッセージ配列を走査して最初の text チャンクを返す。"""

    text = getattr(response, "output_text", "") or ""
    if text:
        return text

    for item in response.output or []:
        if getattr(item, "type", None) == "message":
            for content in getattr(item, "content", []):
                content_type = getattr(content, "type", None)
                if content_type in {"output_text", "text"}:
                    candidate = getattr(content, "text", "") or ""
                    if candidate:
                        return candidate

    return ""


async def plan(user_msg: str, context: Dict[str, Any]) -> PlanOut:
    """ユーザーの日本語チャットを Responses API へ投げ、実行プランを復元する。"""

    graph = _get_plan_graph()
    safe_user_msg = str(user_msg or "")
    safe_context = dict(context or {})
    initial_state: _PlanState = {
        "user_msg": safe_user_msg,
        "context": safe_context,
        "structured_events": [],
    }
    result = await graph.ainvoke(initial_state)
    plan_out = result.get("plan_out")

    def _attach_plan_metadata(plan: PlanOut) -> PlanOut:
        """LangGraph から戻る補助情報を PlanOut へ再適用する。"""

        backlog = result.get("backlog")
        if isinstance(backlog, list):
            plan.backlog = list(backlog)
        next_action = result.get("next_action")
        if isinstance(next_action, str) and next_action:
            plan.next_action = next_action
        return plan

    if isinstance(plan_out, PlanOut):
        return _attach_plan_metadata(plan_out)

    if isinstance(plan_out, dict):
        try:
            return _attach_plan_metadata(PlanOut.model_validate(plan_out))
        except Exception:
            logger.warning("plan graph returned non PlanOut dict; fallback engaged")

    logger.warning("plan graph returned unexpected payload; using default fallback")
    return PlanOut(plan=[], resp="了解しました。")


async def get_plan_priority() -> str:
    """現在のプラン優先度を LangGraph の状態から取得する。"""

    return await _PRIORITY_MANAGER.snapshot()


async def reset_plan_priority() -> None:
    """テストやリカバリーでプラン優先度を初期状態へ戻す。"""

    await _PRIORITY_MANAGER.mark_success()


async def compose_barrier_notification(
    step: str, reason: str, context: Dict[str, Any]
) -> str:
    """作業障壁を Responses API へ説明し、プレイヤー向け確認メッセージを得る。"""

    client = openai.AsyncOpenAI()
    prompt = build_barrier_prompt(step, reason, context)
    logger.info(f"Barrier prompt: {prompt}")

    request_payload = _build_responses_payload(BARRIER_SYSTEM, prompt)

    try:
        resp = await asyncio.wait_for(
            client.responses.create(**request_payload),
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        message = f"barrier notification timed out after {LLM_TIMEOUT_SECONDS:.1f} seconds"
        logger.warning(
            "barrier notification request timed out (step=%s): %s",
            step,
            message,
        )
        raise BarrierNotificationTimeout(message) from exc
    except Exception as exc:
        logger.warning(
            "barrier notification request failed (step=%s): %s",
            step,
            exc,
        )
        raise BarrierNotificationError(str(exc)) from exc

    content = _extract_output_text(resp)
    logger.info(f"Barrier raw: {content}")

    try:
        parsed = BarrierNotification.model_validate_json(content)
        if parsed.message.strip():
            return parsed.message.strip()
    except Exception:
        logger.exception("failed to parse barrier notification JSON")

    # LLM 応答がパースできない場合は従来の短縮メッセージを返す。
    return "問題を確認しました。状況を共有いただけますか？"
