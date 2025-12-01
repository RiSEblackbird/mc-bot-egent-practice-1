# -*- coding: utf-8 -*-
"""チャットキュー処理を集約する専用クラス。

AgentOrchestrator からキュー運用と再試行ポリシーを切り出し、
依存注入されたコールバックで計画実行とチャット送信を実施する。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from runtime.action_graph import ChatTask
from utils import log_structured_event, setup_logger


class ChatQueue:
    """チャットタスクの受付と実行を一元管理する軽量ヘルパー。"""

    def __init__(
        self,
        *,
        process_task: Callable[[ChatTask], Awaitable[None]],
        say: Callable[[str], Awaitable[None]],
        queue_max_size: int,
        task_timeout_seconds: float,
        timeout_retry_limit: int,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        # LLM 計画や Mineflayer 実行などの本処理を外部から注入し、単体テストで差し替えやすくする。
        self._process_task = process_task
        # ユーザーへの通知方法を注入して、超過時やドロップ時のメッセージ方針を分離する。
        self._say = say
        self._task_timeout_seconds = task_timeout_seconds
        self._timeout_retry_limit = timeout_retry_limit
        # 混雑時の背圧を明示的に制御するため、設定値に応じてキュー上限を固定する。
        self.queue: asyncio.Queue[ChatTask] = asyncio.Queue(maxsize=queue_max_size)
        self.logger = logger or setup_logger("agent.chat_queue")

    @property
    def backlog_size(self) -> int:
        """現在のキュー長を公開 API として提供する。"""

        return self.queue.qsize()

    async def enqueue_chat(self, username: str, message: str) -> None:
        """外部から受け取ったチャットをワーカーに積む。"""

        task = ChatTask(username=username, message=message)
        # 直近の指示を優先するため、キュー満杯時は最古のタスクを破棄して新規指示の受付を確保する。
        if self.queue.maxsize > 0 and self.queue.qsize() >= self.queue.maxsize:
            await self._handle_queue_overflow(task)
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
                await asyncio.wait_for(
                    self._process_task(task),
                    timeout=self._task_timeout_seconds,
                )
                elapsed = time.perf_counter() - started_at
                self.logger.info(
                    "worker processed username=%s duration=%.3fs remaining_queue=%d",
                    task.username,
                    elapsed,
                    self.queue.qsize(),
                )
            except asyncio.TimeoutError:
                elapsed = time.perf_counter() - started_at
                log_structured_event(
                    self.logger,
                    "chat task timed out; re-queuing or dropping per retry limit",
                    level=logging.WARNING,
                    event_level="warning",
                    context={
                        "username": task.username,
                        "duration_sec": round(elapsed, 3),
                        "timeout_limit_sec": self._task_timeout_seconds,
                        "retry_count": task.retry_count,
                        "retry_limit": self._timeout_retry_limit,
                    },
                    exc_info=True,
                )
                if task.retry_count < self._timeout_retry_limit:
                    task.retry_count += 1
                    if self.queue.maxsize > 0 and self.queue.qsize() >= self.queue.maxsize:
                        await self._handle_queue_overflow(task)
                    await self.queue.put(task)
                    self.logger.warning(
                        "chat task timeout requeued username=%s retry=%d",
                        task.username,
                        task.retry_count,
                    )
                else:
                    await self._say(
                        "処理が長時間停止したため、この指示をスキップしました。最新の指示を優先します。"
                    )
                    self.logger.error(
                        "chat task timeout dropped username=%s retry_limit=%d",
                        task.username,
                        self._timeout_retry_limit,
                    )
            except Exception:
                self.logger.exception("failed to process chat task username=%s", task.username)
            finally:
                self.queue.task_done()

    async def _handle_queue_overflow(self, incoming: ChatTask) -> None:
        """混雑時に最古のタスクを破棄し、最新チャットの受け付けを保証する。"""

        dropped: Optional[ChatTask] = None
        try:
            dropped = self.queue.get_nowait()
            # get() で取り出した分を完了扱いにして、未完了カウンタの不整合を防ぐ。
            self.queue.task_done()
        except asyncio.QueueEmpty:
            dropped = None

        log_structured_event(
            self.logger,
            "chat queue overflow detected; dropping oldest task to prioritize latest instruction",
            level=logging.WARNING,
            event_level="warning",
            context={
                "policy": "drop_oldest",
                "queue_size": self.queue.qsize(),
                "queue_max_size": self.queue.maxsize,
                "incoming_username": incoming.username,
                "dropped_username": getattr(dropped, "username", None),
            },
        )
        await self._say(
            "処理が混雑しているため、古い指示をスキップし最新の指示を優先します。"
        )


__all__ = ["ChatQueue"]
