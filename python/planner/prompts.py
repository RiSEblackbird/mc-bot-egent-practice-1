"""LLM へ渡すプロンプト生成と Responses API の入出力整形。"""
from __future__ import annotations

from typing import Any, Dict, List

from openai.types.responses import EasyInputMessageParam, Response

from .models import PlanOut

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


def build_responses_input(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
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


def extract_output_text(response: Response) -> str:
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


__all__ = [
    "BARRIER_SYSTEM",
    "SOCRATIC_REVIEW_SYSTEM",
    "SYSTEM",
    "build_barrier_prompt",
    "build_pre_action_review_prompt",
    "build_responses_input",
    "build_user_prompt",
    "extract_output_text",
]
