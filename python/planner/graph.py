"""プランナーの LangGraph 構築と関連ステート管理を担当するモジュール。"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from opentelemetry.trace import Status, StatusCode
from openai.types.responses import EasyInputMessageParam, Response
from pydantic import BaseModel, Field

from llm.client import AsyncOpenAI
from planner_config import PlannerConfig
from utils import log_structured_event, setup_logger, span_context

logger = setup_logger("planner.graph")


class UnifiedPlanState(TypedDict, total=False):
    """プランニング系とアクション系で共有するステート表現。"""

    # プラン生成フェーズで利用
    user_msg: str
    context: Dict[str, Any]
    prompt: str
    payload: Dict[str, Any]
    response: Any
    content: str
    plan_out: Any
    parse_error: str
    llm_error: str
    priority: str
    fallback_plan_out: Any

    # アクションディスパッチで利用
    category: str
    step: str
    last_target_coords: Optional[Tuple[int, int, int]]
    explicit_coords: Optional[Tuple[int, int, int]]
    backlog: List[Dict[str, str]]
    next_action: str
    confirmation_required: bool
    follow_up_message: str
    rule_label: str
    rule_implemented: bool
    handled: bool
    updated_target: Optional[Tuple[int, int, int]]
    failure_detail: Optional[str]
    module: str
    active_role: str
    role_transitioned: bool
    role_transition_reason: Optional[str]
    skill_candidate: Any
    skill_status: str

    # 観測メタデータ
    step_label: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    error: Optional[str]
    structured_events: List[Dict[str, Any]]
    structured_event_history: List[Dict[str, Any]]
    perception_history: List[Dict[str, Any]]
    perception_summary: str
    perception_profile: Dict[str, Any]
    perception_confidence: Optional[float]
    recovery_hints: List[str]


def _serialize_for_log(data: Mapping[str, Any]) -> Dict[str, Any]:
    """ログ出力用にシンプルな辞書へ正規化するヘルパー。"""

    safe: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = list(value)
        elif isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items()}
        else:
            safe[key] = repr(value)
    return safe


def record_structured_step(
    state: UnifiedPlanState,
    *,
    step_label: str,
    inputs: Optional[Mapping[str, Any]] = None,
    outputs: Optional[Mapping[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """ノードの入出力とエラーを統一形式で記録し、ログにも残す。"""

    events = list(state.get("structured_events") or [])
    entry: Dict[str, Any] = {
        "step_label": step_label,
        "inputs": _serialize_for_log(dict(inputs or {})),
        "outputs": _serialize_for_log(dict(outputs or {})),
        "error": error,
    }
    events.append(entry)
    log_structured_event(
        logger,
        "langgraph_step",
        context=entry,
        langgraph_node_id=step_label,
    )
    return {
        "structured_events": events,
        "step_label": step_label,
        "inputs": entry["inputs"],
        "outputs": entry["outputs"],
        "error": error,
    }


def record_recovery_hints(state: UnifiedPlanState, hints: Sequence[str]) -> Dict[str, Any]:
    """再計画用ヒントをステートへ保存し、ログにも残す。"""

    recovered: List[str] = []
    for hint in hints:
        text = str(hint or "").strip()
        if text:
            recovered.append(text)
    if not recovered:
        return {}

    log_structured_event(
        logger,
        "langgraph_recovery_hints",
        context={"count": len(recovered), "preview": recovered[:3]},
        langgraph_node_id="recovery_hints",
    )
    state["recovery_hints"] = recovered
    return {"recovery_hints": recovered}


class PlanPriorityManager:
    """LLM 連携の成功/失敗に応じて優先度を調整するシンプルな状態管理。"""

    def __init__(self, config: PlannerConfig) -> None:
        self._lock = asyncio.Lock()
        self._priority = "normal"
        self._review_threshold = config.plan_confidence_review_threshold
        self._critical_threshold = config.plan_confidence_critical_threshold

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

    def evaluate_confidence_gate(self, plan_out: "PlanOut") -> Dict[str, Any]:
        """Determine whether a pre-action review is required for the given plan."""

        reason = ""
        if getattr(plan_out, "blocking", False):
            return {"needs_review": False, "reason": reason}
        if getattr(plan_out, "clarification_needed", "none") != "none":
            return {"needs_review": False, "reason": reason}

        confidence = float(getattr(plan_out, "confidence", 0.0) or 0.0)
        if confidence <= self._critical_threshold:
            reason = f"confidence={confidence:.2f}"
        elif confidence <= self._review_threshold:
            reason = f"confidence={confidence:.2f}"

        return {"needs_review": bool(reason), "reason": reason}


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
    plan: List[str] = Field(default_factory=list)
    resp: str = ""
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


SYSTEM = """あなたはMinecraftの自律ボットです。日本語の自然文指示を、
現在の状況を考慮して実行可能な高レベルのステップ列に分解し、同時に
プレイヤーへ伝える短い応答（日本語）を生成します。返却する JSON には
実行計画だけでなく、実行時に必要なメタデータも含めてください。"""

BARRIER_SYSTEM = """あなたはMinecraftのサポートボットです。停滞している作業の概要を理解し、
プレイヤーに丁寧で簡潔な日本語メッセージを作成してください。状況説明と、
必要な確認事項や追加指示の依頼を 2 文程度で伝えてください。出力は必ず
json オブジェクトで、キーは "message": string のみを含めてください。"""

SOCRATIC_REVIEW_SYSTEM = """あなたは計画の安全性を見直すレビューアです。実行計画の要約と
推定確信度が提供されるので、プレイヤーに 1～2 文の丁寧な日本語で確認質問を行ってください。
作業に不安がある理由や追加で必要な情報を簡潔に伝え、過度に謝らず落ち着いた口調で書きます。"""


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


def build_pre_action_review_prompt(plan_out: PlanOut, reason: str) -> str:
    """Confidence gate 用のフォローアップ質問プロンプトを生成する。"""

    steps_text = "\n".join(f"- {step}" for step in plan_out.plan) or "- (手順なし)"
    goal_summary = plan_out.goal_profile.summary if plan_out.goal_profile else ""
    intent = plan_out.intent or "unknown"
    return f"""# 計画概要
intent: {intent}
goal: {goal_summary}
steps:
{steps_text}

# 確信度
confidence: {plan_out.confidence:.2f}
reason: {reason or 'none'}

# 期待する出力
プレイヤーに対して丁寧に確認する 1～2 文の日本語だけを返してください。
危険要素や不足情報について簡潔に触れ、追加で欲しい情報を質問してください。
"""


def build_user_prompt(user_msg: str, context: Dict[str, Any]) -> str:
    """ユーザー発話と周辺状況を LangGraph へ渡すためのプロンプトに整形する。"""

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
  "recovery_hints": [],
  "react_trace": [
    {{"thought": "農作業を開始する準備が必要", "action": "畑へ移動", "observation": ""}},
    {{"thought": "材料を確保する", "action": "小麦を収穫", "observation": ""}},
    {{"thought": "食料を用意する", "action": "パンを作る", "observation": ""}}
  ]
}}
"""


def _build_responses_input(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
    """Responses API へ渡す message 配列を生成する補助関数。"""

    messages = [
        EasyInputMessageParam(role="system", content=system_prompt),
        EasyInputMessageParam(role="user", content=user_prompt),
    ]

    serialized: List[Dict[str, Any]] = []
    for msg in messages:
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


def _normalize_directives(plan_out: PlanOut) -> None:
    directives: List[ActionDirective] = []
    for idx, step in enumerate(plan_out.plan):
        directive = plan_out.directives[idx] if idx < len(plan_out.directives) else ActionDirective()
        directive.directive_id = directive.directive_id or f"step-{idx + 1}"
        directive.step = directive.step or step
        if not directive.label:
            directive.label = directive.step[:24]
        if not directive.category:
            directive.category = plan_out.intent or ""
        directives.append(directive)

    plan_out.directives = directives


def _extract_recovery_hints_from_context(state: UnifiedPlanState) -> List[str]:
    hints: List[str] = []
    context = state.get("context") or {}
    raw_hints = context.get("recovery_hints")
    if isinstance(raw_hints, (list, tuple)):
        for hint in raw_hints:
            text = str(hint or "").strip()
            if text:
                hints.append(text)
    return hints


def _extract_output_text(response: Response) -> str:
    """Responses API の出力から JSON 本文を安全に取り出す。"""

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


async def _compose_pre_action_follow_up(
    plan_out: PlanOut,
    reason: str,
    *,
    client_factory: Callable[[], AsyncOpenAI],
    payload_builder: Callable[[str, str], Dict[str, Any]],
    timeout_seconds: float,
) -> str:
    """Responses API を利用してソクラテス式のフォローアップ文を生成する。"""

    client = client_factory()
    prompt = build_pre_action_review_prompt(plan_out, reason)
    payload = payload_builder(SOCRATIC_REVIEW_SYSTEM, prompt)
    try:
        resp = await asyncio.wait_for(
            client.responses.create(**payload),
            timeout=timeout_seconds,
        )
        text = _extract_output_text(resp).strip()
        if text:
            return text
    except Exception as exc:  # pragma: no cover - LLM 障害はログのみに留める
        logger.warning("pre_action_review compose failed: %s", exc)
    return "作業内容に不確実な点があるため、追加の指示をいただけますか？"


def build_plan_graph(
    config: PlannerConfig,
    *,
    priority_manager: PlanPriorityManager,
    async_client_factory: Callable[[], AsyncOpenAI],
    payload_builder: Callable[[str, str], Dict[str, Any]],
) -> CompiledStateGraph:
    """Plan 用 LangGraph を構築してコンパイルする。"""

    manager = priority_manager
    graph: StateGraph = StateGraph(UnifiedPlanState)

    async def prepare_payload(state: UnifiedPlanState) -> Dict[str, Any]:
        recovery_hints = _extract_recovery_hints_from_context(state)
        if recovery_hints:
            record_recovery_hints(state, recovery_hints)
        prompt = build_user_prompt(state.get("user_msg", ""), state.get("context", {}))
        logger.info("LLM prompt: %s", prompt)
        payload = payload_builder(SYSTEM, prompt)
        metadata = record_structured_step(
            state,
            step_label="prepare_payload",
            inputs={"user_msg": state.get("user_msg", ""), "context_keys": list(state.get("context", {}).keys())},
            outputs={"prompt_preview": prompt[:120]},
        )
        result: Dict[str, Any] = {"prompt": prompt, "payload": payload}
        result.update(metadata)
        return result

    async def call_llm(state: UnifiedPlanState) -> Dict[str, Any]:
        """Responses API を呼び出し、タイムアウト時は安全なフォールバックを返す。"""

        with span_context(
            "llm.responses.create",
            langgraph_node_id="plan.call_llm",
            event_level="info",
            attributes={"llm.model": config.model},
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
                        inputs={"model": config.model},
                        outputs={"priority": priority, "fallback": True},
                        error=reason,
                    )
                )
                return payload

            try:
                client = async_client_factory()
                resp = await asyncio.wait_for(
                    client.responses.create(**state["payload"]),
                    timeout=config.llm_timeout_seconds,
                )
            except asyncio.TimeoutError:
                timeout_reason = f"timeout after {config.llm_timeout_seconds:.1f} seconds"
                if span.is_recording():
                    span.set_attribute("llm.timeout_seconds", config.llm_timeout_seconds)
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
                    inputs={"model": config.model},
                    outputs={"content_length": len(content)},
                )
            )
            if span.is_recording():
                span.set_attribute("llm.content_length", len(content))
            return payload

    async def parse_plan(state: UnifiedPlanState) -> Dict[str, Any]:
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

    async def normalize_react_trace(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out = state.get("plan_out")
        if not isinstance(plan_out, PlanOut):
            logger.warning("normalize_react_trace received non PlanOut")
            return {}

        trace: List[ReActStep] = []
        for entry in plan_out.react_trace:
            trace.append(
                ReActStep(
                    thought=entry.thought,
                    action=entry.action,
                    observation=getattr(entry, "observation", ""),
                )
            )
        plan_out.react_trace = trace
        _normalize_directives(plan_out)

        return record_structured_step(
            state,
            step_label="normalize_react_trace",
            inputs={"react_trace_count": len(trace)},
            outputs={"directive_count": len(plan_out.directives)},
        )

    async def pre_action_review(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out: PlanOut = state.get("plan_out")
        if not isinstance(plan_out, PlanOut):
            logger.warning("pre_action_review received non PlanOut")
            return {}

        evaluation = manager.evaluate_confidence_gate(plan_out)
        if not evaluation["needs_review"]:
            return record_structured_step(
                state,
                step_label="pre_action_review",
                inputs={"confidence": plan_out.confidence},
                outputs={"needs_review": False},
            )

        follow_up_message = await _compose_pre_action_follow_up(
            plan_out,
            evaluation.get("reason", ""),
            client_factory=async_client_factory,
            payload_builder=payload_builder,
            timeout_seconds=config.llm_timeout_seconds,
        )
        plan_out.next_action = "chat"
        # フォローアップ文を PlanOut にも保持し、Mineflayer 側でそのまま案内できるようにする。
        plan_out.resp = follow_up_message or plan_out.resp
        plan_out.backlog = plan_out.backlog or []
        plan_out.backlog.append(
            {"type": "review", "reason": evaluation.get("reason", ""), "label": "自動確認"}
        )

        result = {
            "plan_out": plan_out,
            "follow_up_message": follow_up_message,
            "confirmation_required": True,
        }
        result.update(
            record_structured_step(
                state,
                step_label="pre_action_review",
                inputs={"confidence": plan_out.confidence},
                outputs={"needs_review": True, "reason": evaluation.get("reason", "")},
            )
        )
        return result

    async def intent_negotiation(state: UnifiedPlanState) -> Dict[str, Any]:
        plan_out = state.get("plan_out")
        content = state.get("content") or ""
        confirmation_required = bool(state.get("confirmation_required"))
        backlog: List[Dict[str, str]] = []
        follow_up_message = state.get("follow_up_message", "")

        if content:
            backlog.append({"type": "plan", "summary": content[:120], "label": "プラン概要"})

        if isinstance(plan_out, PlanOut):
            blocking = getattr(plan_out, "blocking", False)
            confirmation_required = confirmation_required or bool(
                getattr(plan_out, "clarification_needed", "none") != "none"
            )
            if plan_out.backlog:
                backlog.extend(plan_out.backlog)
            if blocking or confirmation_required:
                confirmation_required = True
                plan_out.next_action = "chat"
            else:
                plan_out.next_action = "execute"
            plan_out.backlog = backlog
            if confirmation_required and follow_up_message:
                plan_out.resp = follow_up_message

        result = {
            "plan_out": plan_out,
            "backlog": backlog,
            "confirmation_required": confirmation_required,
            "follow_up_message": follow_up_message,
            "next_action": getattr(plan_out, "next_action", state.get("next_action")),
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

    async def route_to_chat(state: UnifiedPlanState) -> Dict[str, Any]:
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

    async def fallback_plan(state: UnifiedPlanState) -> Dict[str, Any]:
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

    async def finalize(state: UnifiedPlanState) -> Dict[str, Any]:
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
    graph.add_node("pre_action_review", pre_action_review)
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
    graph.add_edge("normalize_react_trace", "pre_action_review")
    graph.add_edge("pre_action_review", "intent_negotiation")
    graph.add_conditional_edges(
        "intent_negotiation",
        lambda state: "chat" if state.get("confirmation_required") else "execute",
        {"execute": "finalize", "chat": "route_to_chat"},
    )
    graph.add_edge("route_to_chat", "finalize")
    graph.add_edge("fallback_plan", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


__all__ = [
    "ActionDirective",
    "BarrierNotification",
    "BarrierNotificationError",
    "BarrierNotificationTimeout",
    "ConstraintSpec",
    "ExecutionHint",
    "GoalProfile",
    "PlanArguments",
    "PlanOut",
    "PlanPriorityManager",
    "ReActStep",
    "UnifiedPlanState",
    "build_barrier_prompt",
    "build_plan_graph",
    "build_pre_action_review_prompt",
    "build_user_prompt",
    "record_recovery_hints",
    "record_structured_step",
    "SYSTEM",
    "BARRIER_SYSTEM",
    "SOCRATIC_REVIEW_SYSTEM",
    "_build_responses_input",
    "_extract_output_text",
]
