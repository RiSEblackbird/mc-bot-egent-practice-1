# -*- coding: utf-8 -*-
"""Python エージェントのエントリポイント。

プレイヤーのチャットを Node.js 側から WebSocket で受信し、LLM による計画生成と
Mineflayer へのアクション実行を統合する。従来の標準入力デモから脱却し、
実運用に耐える自律フローへ移行するための実装。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from dotenv import load_dotenv
from websockets import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from websockets.server import serve

from actions import Actions
from bridge_ws import BotBridge
from memory import Memory
from planner import PlanOut, plan
from utils import setup_logger

logger = setup_logger("agent")

load_dotenv()

# --- 環境変数の読み込み ----------------------------------------------------

WS_URL = os.getenv("WS_URL", "ws://127.0.0.1:8765")
AGENT_WS_HOST = os.getenv("AGENT_WS_HOST", "0.0.0.0")
DEFAULT_MOVE_TARGET_RAW = os.getenv("DEFAULT_MOVE_TARGET", "0,64,0")


def _parse_port(raw: Optional[str], default: int) -> int:
    """環境変数からポート番号を安全に読み取る。"""

    if raw is None:
        return default
    try:
        value = int(raw)
        if value <= 0 or value > 65535:
            raise ValueError
        return value
    except ValueError:
        logger.warning("環境変数のポート値 '%s' が不正なため %d を使用します。", raw, default)
        return default


AGENT_WS_PORT = _parse_port(os.getenv("AGENT_WS_PORT"), 9000)


def _parse_default_move_target(raw: str) -> Tuple[int, int, int]:
    """環境変数から読み込んだ座標文字列を整数タプルへ変換する。"""

    try:
        parts = [int(part.strip()) for part in raw.split(",")]
        if len(parts) != 3:
            raise ValueError
        return parts[0], parts[1], parts[2]
    except Exception:
        logger.warning(
            "DEFAULT_MOVE_TARGET='%s' の解析に失敗したため (0, 64, 0) を採用します。",
            raw,
        )
        return (0, 64, 0)


DEFAULT_MOVE_TARGET = _parse_default_move_target(DEFAULT_MOVE_TARGET_RAW)


@dataclass
class ChatTask:
    """Node 側から渡されるチャット指示をキュー化する際のデータ構造。"""

    username: str
    message: str


class AgentOrchestrator:
    """受信チャットを順次処理し、LLM プラン→Mineflayer 操作を遂行する中核クラス。"""

    # Mineflayer へ渡す座標はプレイヤーの指示の表記揺れが多いため、複数の正規表現
    # を用意して柔軟に抽出する。スラッシュ区切り（-36 / 73 / -66）や全角スラッシュ、
    # カンマ区切り、XYZ: -36 / 73 / -66 などを一括で処理できるようにしている。
    _COORD_PATTERNS = (
        re.compile(r"(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)"),
        re.compile(
            r"XYZ[:：]?\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)\s*(?:[,/]|／)\s*(-?\d+)"
        ),
    )

    def __init__(self, actions: Actions, memory: Memory) -> None:
        self.actions = actions
        self.memory = memory
        self.queue: asyncio.Queue[ChatTask] = asyncio.Queue()
        self.default_move_target = DEFAULT_MOVE_TARGET
        self.logger = setup_logger("agent.orchestrator")

    async def enqueue_chat(self, username: str, message: str) -> None:
        """WebSocket から受け取ったチャットをワーカーに積む。"""

        task = ChatTask(username=username, message=message)
        await self.queue.put(task)
        self.logger.info(
            "chat task enqueued username=%s message=%s queue_size=%d",
            username,
            message,
            self.queue.qsize(),
        )

    async def worker(self) -> None:
        """チャットキューを逐次処理するバックグラウンドタスク。"""

        while True:
            queue_before = self.queue.qsize()
            self.logger.info(
                "worker awaiting task queue_size_before_get=%d", queue_before
            )
            task = await self.queue.get()
            try:
                started_at = time.perf_counter()
                await self._process_chat(task)
                elapsed = time.perf_counter() - started_at
                self.logger.info(
                    "worker processed username=%s duration=%.3fs remaining_queue=%d",
                    task.username,
                    elapsed,
                    self.queue.qsize(),
                )
            except Exception:
                self.logger.exception("failed to process chat task username=%s", task.username)
            finally:
                self.queue.task_done()

    async def _process_chat(self, task: ChatTask) -> None:
        """単一のチャット指示に対して LLM 計画とアクション実行を行う。"""

        context = self._build_context_snapshot()
        self.logger.info(
            "creating plan for username=%s message='%s' context=%s",
            task.username,
            task.message,
            context,
        )

        plan_out = await plan(task.message, context)
        self.logger.info(
            "plan generated steps=%d plan=%s resp=%s",
            len(plan_out.plan),
            plan_out.plan,
            plan_out.resp,
        )

        # LLM の丁寧な応答をそのままプレイヤーへ relay する。
        if plan_out.resp:
            self.logger.info(
                "relaying llm response to player username=%s resp='%s'",
                task.username,
                plan_out.resp,
            )
            await self.actions.say(plan_out.resp)

        await self._execute_plan(plan_out)
        self.memory.set("last_chat", {"username": task.username, "message": task.message})

    def _build_context_snapshot(self) -> Dict[str, Any]:
        """LLM へ渡す簡易コンテキストを生成する。"""

        snapshot = {
            "player_pos": self.memory.get("player_pos", "不明"),
            "inventory_summary": self.memory.get("inventory", "不明"),
            "last_chat": self.memory.get("last_chat", "未記録"),
        }
        self.logger.info("context snapshot built=%s", snapshot)
        return snapshot

    async def _execute_plan(self, plan_out: PlanOut) -> None:
        """LLM が出力した高レベルステップを簡易ヒューリスティックで実行する。"""

        total_steps = len(plan_out.plan)
        # 直前に検出した移動座標を記録し、以降の「移動」ステップで座標が省略
        # された場合でも同じ目的地へ移動し続けられるようにする。
        last_target_coords: Optional[Tuple[int, int, int]] = None
        # 同一ステップが複数回検出された際に同じ警告を連投しないための記録領域。
        reported_blockers: set[str] = set()
        for index, step in enumerate(plan_out.plan, start=1):
            normalized = step.strip()
            self.logger.info(
                "plan_step index=%d/%d raw='%s' normalized='%s'",
                index,
                total_steps,
                step,
                normalized,
            )
            if not normalized:
                continue

            coords = self._extract_coordinates(normalized)
            if coords:
                self.logger.info(
                    "plan_step index=%d classified as coordinate_move coords=%s",
                    index,
                    coords,
                )
                last_target_coords = coords
                await self._move_to_coordinates(coords)
                continue

            if any(keyword in normalized for keyword in ("移動", "向かう", "歩く")):
                target_coords = last_target_coords or self.default_move_target
                if last_target_coords:
                    self.logger.info(
                        "plan_step index=%d fallback_move reuse_last_target=%s",
                        index,
                        target_coords,
                    )
                else:
                    self.logger.info(
                        "plan_step index=%d fallback_move keywords_detected default_target=%s",
                        index,
                        self.default_move_target,
                    )
                await self._move_to_coordinates(target_coords)
                continue

            if "報告" in normalized or "伝える" in normalized:
                self.logger.info(
                    "plan_step index=%d issuing status_report",
                    index,
                )
                await self.actions.say("進捗を確認しています。続報をお待ちください。")
                continue

            self.logger.info(
                "plan_step index=%d no_direct_mapping step='%s'",
                index,
                normalized,
            )
            if normalized not in reported_blockers:
                reported_blockers.add(normalized)
                await self._report_execution_barrier(
                    normalized,
                    "対応可能なアクションが見つからず停滞しています。",
                )

    def _extract_coordinates(self, text: str) -> Optional[Tuple[int, int, int]]:
        """ステップ文字列から XYZ 座標らしき数値を抽出する。"""

        for pattern in self._COORD_PATTERNS:
            match = pattern.search(text)
            if match:
                x, y, z = (int(match.group(i)) for i in range(1, 4))
                return x, y, z
        return None

    async def _move_to_coordinates(self, coords: Iterable[int]) -> None:
        """Mineflayer の移動アクションを発行し、結果をログに残す。"""

        x, y, z = coords
        self.logger.info("requesting moveTo to (%d, %d, %d)", x, y, z)
        resp = await self.actions.move_to(x, y, z)
        self.logger.info("moveTo response=%s", resp)
        if resp.get("ok"):
            self.memory.set("last_destination", {"x": x, "y": y, "z": z})
        else:
            self.logger.error("moveTo command rejected resp=%s", resp)
            error_detail = resp.get("error") or "Mineflayer 側の理由不明な拒否"
            await self._report_execution_barrier(
                f"座標 ({x}, {y}, {z}) への移動",
                f"Mineflayer からエラー応答を受け取りました（{error_detail}）。",
            )

    async def _report_execution_barrier(self, step: str, reason: str) -> None:
        """処理を継続できない障壁を検知した際にチャットとログで即時共有する。"""

        short_step = self._shorten_text(step, limit=40)
        short_reason = self._shorten_text(reason, limit=60)
        self.logger.warning(
            "execution barrier detected step='%s' reason='%s'",
            step,
            reason,
        )
        await self.actions.say(
            f"手順「{short_step}」で問題が発生しました: {short_reason}"
        )

    @staticmethod
    def _shorten_text(text: str, *, limit: int) -> str:
        """チャット送信用にテキストを安全な長さへ丸めるユーティリティ。"""

        text = text.strip()
        return text if len(text) <= limit else f"{text[:limit]}…"


class AgentWebSocketServer:
    """Node -> Python のチャット転送を受け付ける WebSocket サーバー。"""

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.logger = setup_logger("agent.ws")

    async def handler(self, websocket: WebSocketServerProtocol) -> None:
        """各接続ごとに JSON コマンドを受信・処理する。"""

        peer = f"{websocket.remote_address}" if websocket.remote_address else "unknown"
        self.logger.info("connection opened from %s", peer)
        try:
            async for raw in websocket:
                response = await self._handle_message(raw)
                await websocket.send(json.dumps(response, ensure_ascii=False))
        except (ConnectionClosedOK, ConnectionClosedError):
            self.logger.info("connection closed from %s", peer)
        except Exception:
            self.logger.exception("unexpected error while handling connection from %s", peer)

    async def _handle_message(self, raw: str) -> Dict[str, Any]:
        """受信文字列を解析し、サポートするコマンドへ振り分ける。"""

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.error("invalid JSON payload=%s", raw)
            return {"ok": False, "error": "invalid json"}

        if payload.get("type") != "chat":
            self.logger.error("unsupported payload type=%s", payload.get("type"))
            return {"ok": False, "error": "unsupported type"}

        args = payload.get("args") or {}
        username = str(args.get("username", "")).strip() or "Player"
        message = str(args.get("message", "")).strip()

        if not message:
            self.logger.warning("empty chat message received username=%s", username)
            return {"ok": False, "error": "empty message"}

        await self.orchestrator.enqueue_chat(username, message)
        return {"ok": True}


async def main() -> None:
    """エージェントを起動し、WebSocket サーバーとワーカーを開始する。"""

    bridge = BotBridge(WS_URL)
    actions = Actions(bridge)
    mem = Memory()
    orchestrator = AgentOrchestrator(actions, mem)
    ws_server = AgentWebSocketServer(orchestrator)

    worker_task = asyncio.create_task(orchestrator.worker(), name="agent-worker")

    async with serve(ws_server.handler, AGENT_WS_HOST, AGENT_WS_PORT):
        logger.info("Python agent is listening on ws://%s:%s", AGENT_WS_HOST, AGENT_WS_PORT)
        try:
            await asyncio.Future()  # 実行を継続
        except asyncio.CancelledError:
            logger.info("main loop cancelled")
        finally:
            worker_task.cancel()
            with contextlib.suppress(Exception):
                await worker_task


if __name__ == "__main__":
    asyncio.run(main())
