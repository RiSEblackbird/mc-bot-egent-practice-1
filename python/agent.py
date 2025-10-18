# -*- coding: utf-8 -*-
# 起動エントリ：WS接続→チャット入力（暫定: 標準入力）→ LLM 計画 → 行動
import asyncio
import os
from dotenv import load_dotenv
from utils import setup_logger
from bridge_ws import BotBridge
from actions import Actions
from memory import Memory
from planner import plan

logger = setup_logger("agent")

load_dotenv()
WS_URL = os.getenv("WS_URL", "ws://127.0.0.1:8765")

async def main():
    bridge = BotBridge(WS_URL)
    actions = Actions(bridge)
    mem = Memory()

    logger.info("Python agent started. Type a Japanese instruction (simulating in-game chat).")
    logger.info("例: パンが無い / 鉄が足りない / ついてきて")

    # ここではまず標準入力で疑似チャット。後で Paper 側→Node→Python の実受信に差し替え可。
    while True:
        user_msg = input("> ").strip()
        logger.info(f"received pseudo-chat input: '{user_msg}'")

        if not user_msg:
            logger.info("input was empty after stripping; waiting for next message")
            continue

        # （暫定）プレイヤー位置などの状況は mem から渡す（実装中は空）
        context = {
            "player_pos": mem.get("player_pos", "不明"),
            "inventory_summary": mem.get("inventory", "不明"),
        }

        logger.info(f"building execution plan for message='{user_msg}' with context={context}")
        plan_out = await plan(user_msg, context)
        # プレイヤーへ応答
        logger.info(f"LLM responded with plan={plan_out.plan} resp='{plan_out.resp}'")
        await actions.say(plan_out.resp)

        # ごく簡単な PLAN 実行デモ： "移動" っぽいテキストがあれば固定座標に移動
        for step in plan_out.plan:
            if "移動" in step:
                logger.info(f"auto-move triggered by plan step='{step}' -> destination=(0, 64, 0)")
                # デモ用：スポーン近くへ移動（座標は適宜変更）
                await actions.move_to(0, 64, 0)

        # TODO: step の語彙に応じて dig / craft 等の実アクションにマッピングしていく
        # 例: if "小麦" in step and "収穫" in step: -> dig wheat; etc.

if __name__ == "__main__":
    asyncio.run(main())
