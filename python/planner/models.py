"""プランナー周辺の Pydantic モデルと補助変換ロジック。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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


def normalize_directives(plan_out: PlanOut) -> None:
    """PlanOut 内の directives を手順と同期させる。"""

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
    "ReActStep",
    "normalize_directives",
]
