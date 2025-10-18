# -*- coding: utf-8 -*-
# gpt-5-mini を用いたプランニング：自然文→PLAN/RESP の二分出力
import os
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from utils import setup_logger
from dotenv import load_dotenv
import openai

logger = setup_logger("planner")
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

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

# gpt-5-mini をはじめとした一部のモデルは温度固定で API が受け付けないため、
# 送信時には temperature フィールドを省略する必要がある。
TEMPERATURE_LOCKED_MODELS = {"gpt-5-mini"}


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
class PlanOut(BaseModel):
    plan: List[str] = Field(default_factory=list)  # 実行ステップ（高レベル）
    resp: str = ""  # プレイヤー向け日本語応答


class BarrierNotification(BaseModel):
    """障壁通知用のメッセージをパースするためのスキーマ。"""

    message: str = ""

# OpenAI Chat Completions API は response_format=json_object を利用する際、
# "json" という語がプロンプト内に含まれている必要がある。
# システムメッセージに明示することで、推論モデルに json 形式での応答を
# 強制しつつ API 要件を満たす。
SYSTEM = """あなたはMinecraftの自律ボットです。日本語の自然文指示を、
現在の状況を考慮して実行可能な高レベルのステップ列に分解し、同時に
プレイヤーへ返す丁寧な日本語メッセージを用意してください。行動開始
前に許可を求める質問は挟まず、指示された作業に着手する前提で端的に
了承してください。プレイヤーが座標や数量などの具体情報を伝えた場合
は、同じ内容を繰り返し尋ねないでください。

出力は必ず json 形式のオブジェクトで、キーは "plan": string[], "resp": string とする。
推論の思考過程は出力に含めないこと。
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
{{"plan": ["畑へ移動", "小麦を収穫", "パンを作る"], "resp": "了解しました。小麦を収穫してパンを作りますね。"}}
"""

async def plan(user_msg: str, context: Dict[str, Any]) -> PlanOut:
    from openai import AsyncOpenAI
    client = AsyncOpenAI()
    prompt = build_user_prompt(user_msg, context)
    logger.info(f"LLM prompt: {prompt}")

    temperature = resolve_request_temperature(MODEL)
    request_payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        request_payload["temperature"] = temperature

    resp = await client.chat.completions.create(**request_payload)
    content = resp.choices[0].message.content
    logger.info(f"LLM raw: {content}")
    try:
        data = PlanOut.model_validate_json(content)
    except Exception:
        # 最低限のフォールバック
        data = PlanOut(plan=[], resp="了解しました。")
    return data


async def compose_barrier_notification(
    step: str, reason: str, context: Dict[str, Any]
) -> str:
    """障壁発生時にプレイヤーへ送る確認メッセージを LLM によって生成する。"""

    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    prompt = build_barrier_prompt(step, reason, context)
    logger.info(f"Barrier prompt: {prompt}")

    temperature = resolve_request_temperature(MODEL)
    request_payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": BARRIER_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        request_payload["temperature"] = temperature

    resp = await client.chat.completions.create(**request_payload)
    content = resp.choices[0].message.content
    logger.info(f"Barrier raw: {content}")

    try:
        parsed = BarrierNotification.model_validate_json(content)
        if parsed.message.strip():
            return parsed.message.strip()
    except Exception:
        logger.exception("failed to parse barrier notification JSON")

    # LLM 応答がパースできない場合は従来の短縮メッセージを返す。
    return "問題を確認しました。状況を共有いただけますか？"
