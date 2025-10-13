# -*- coding: utf-8 -*-
# gpt-5-mini を用いたプランニング：自然文→PLAN/RESP の二分出力
import os
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from utils import setup_logger
from dotenv import load_dotenv
import openai

logger = setup_logger("planner")
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL") or None
if base_url:
    openai.base_url = base_url

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# 期待する出力スキーマ（簡易）
class PlanOut(BaseModel):
    plan: List[str] = Field(default_factory=list)  # 実行ステップ（高レベル）
    resp: str = ""                                 # プレイヤー向け日本語応答

SYSTEM = """あなたはMinecraftの自律ボットです。日本語の自然文指示を、
現在の状況を考慮して実行可能な高レベルのステップ列に分解し、同時に
プレイヤーへ返す丁寧な日本語メッセージを用意してください。

出力は必ず JSON で、キーは "plan": string[], "resp": string とする。
推論の思考過程は出力に含めないこと。
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
JSON のみ。例：
{{"plan": ["畑へ移動", "小麦を収穫", "パンを作る"], "resp": "了解しました。小麦を収穫してパンを作りますね。"}}
"""

async def plan(user_msg: str, context: Dict[str, Any]) -> PlanOut:
    from openai import AsyncOpenAI
    client = AsyncOpenAI()
    prompt = build_user_prompt(user_msg, context)
    logger.info(f"LLM prompt: {prompt}")

    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    logger.info(f"LLM raw: {content}")
    try:
        data = PlanOut.model_validate_json(content)
    except Exception:
        # 最低限のフォールバック
        data = PlanOut(plan=[], resp="了解しました。")
    return data
