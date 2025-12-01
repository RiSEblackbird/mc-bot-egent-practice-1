# -*- coding: utf-8 -*-
"""AgentBridge のイベントストリーム購読と正規化を担うインフラ層モジュール。

SSE の受信からイベントキュー管理までをこのモジュールに閉じ込め、
AgentOrchestrator 側ではメモリ更新や役割切替といったハンドラのみを
依存注入する設計としている。イベントループやキューを差し替え可能にし、
非同期処理の単体テストを容易にする。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from bridge_client import BRIDGE_EVENT_STREAM_ENABLED, BridgeClient, BridgeError
from utils import log_structured_event, setup_logger


@dataclass
class BridgeEventHooks:
    """Bridge イベント処理で利用する副作用ハンドラの集合。

    メモリ更新や役割切替といった状態変更を外部へ委譲するため、
    Orchestrator から必要なコールバックを注入する。
    """

    set_memory: Callable[[str, Any], None]
    request_role_switch: Callable[[str, Optional[str]], None]
    format_position: Callable[[Dict[str, Any]], Optional[str]]
    ingest_perception: Callable[[Dict[str, Any], str], None]
    apply_primary_role: Callable[[Dict[str, Any]], None]

    def __post_init__(self) -> None:  # pragma: no cover - 型安全性の確保が主目的
        # いずれかのハンドラが欠落しているとイベント処理が進まないため、
        # 初期化段階で明示的に検知する。
        for name, handler in [
            ("set_memory", self.set_memory),
            ("request_role_switch", self.request_role_switch),
            ("format_position", self.format_position),
            ("ingest_perception", self.ingest_perception),
            ("apply_primary_role", self.apply_primary_role),
        ]:
            if not callable(handler):
                raise ValueError(f"BridgeEventHooks.{name} must be callable")


class BridgeEventListener:
    """BridgeClient からの SSE を購読し、エージェントイベントへ正規化する責務を持つ。"""

    def __init__(
        self,
        *,
        bridge_client: BridgeClient | None = None,
        hooks: BridgeEventHooks,
        shared_agents: Optional[Dict[str, Dict[str, Any]]] = None,
        logger: Optional[logging.Logger] = None,
        queue: Optional[asyncio.Queue[Dict[str, Any]]] = None,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
        stop_event: Optional[asyncio.Event] = None,
        thread_stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._bridge_client = bridge_client or BridgeClient()
        self._hooks = hooks
        self._shared_agents = shared_agents if shared_agents is not None else {}
        self._logger = logger or setup_logger("agent.bridge_events")
        self._queue = queue
        self._event_loop = event_loop
        self._stop_event = stop_event
        self._thread_stop_event = thread_stop_event
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        """イベント購読のポンプとコンシューマを起動する。"""

        if not BRIDGE_EVENT_STREAM_ENABLED:
            self._logger.info("bridge event stream disabled via env; skip listener setup")
            return
        if self._tasks:
            return

        self._stop_event = self._stop_event or asyncio.Event()
        self._thread_stop_event = self._thread_stop_event or threading.Event()
        self._queue = self._queue or asyncio.Queue()
        self._event_loop = self._event_loop or asyncio.get_running_loop()

        pump = asyncio.create_task(self._bridge_event_pump(), name="bridge-event-pump")
        consumer = asyncio.create_task(
            self._bridge_event_consumer(), name="bridge-event-consumer"
        )
        self._tasks.extend([pump, consumer])

    async def stop(self) -> None:
        """起動済みのイベント購読タスクを安全に停止する。"""

        if not self._tasks:
            return

        if self._stop_event:
            self._stop_event.set()
        if self._thread_stop_event:
            self._thread_stop_event.set()

        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._stop_event = None
        self._thread_stop_event = None

    async def _bridge_event_pump(self) -> None:
        """SSE ストリームからのイベントをキューへ積むバックグラウンドタスク。"""

        if self._stop_event is None or self._thread_stop_event is None:
            return
        if self._queue is None or self._event_loop is None:
            return

        loop = self._event_loop

        def _enqueue(event: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._queue.put_nowait, event)

        while not self._stop_event.is_set():
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._bridge_client.consume_event_stream(
                        _enqueue, self._thread_stop_event
                    ),
                )
            except BridgeError as exc:
                log_structured_event(
                    self._logger,
                    "bridge event stream encountered recoverable error",
                    level=logging.WARNING,
                    event_level="warning",
                    langgraph_node_id="agent.bridge_events",
                    context={"error": str(exc)},
                )
            except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
                log_structured_event(
                    self._logger,
                    "bridge event stream failed unexpectedly",
                    level=logging.ERROR,
                    event_level="fault",
                    langgraph_node_id="agent.bridge_events",
                    context={"error": str(exc)},
                    exc_info=True,
                )

            if not self._stop_event.is_set():
                await asyncio.sleep(1.0)

    async def _bridge_event_consumer(self) -> None:
        """Bridge イベントキューを消費し、正規化されたイベントへ連携する。"""

        if self._stop_event is None or self._queue is None:
            return

        while not self._stop_event.is_set():
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self.handle_agent_event(payload)
            finally:
                self._queue.task_done()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        """Node 側から届いたマルチエージェントイベントを解析して記憶する。"""

        events: list[Dict[str, Any]] = []
        raw_events = args.get("events")
        if isinstance(raw_events, list):
            events.extend([item for item in raw_events if isinstance(item, dict)])

        single_event = args.get("event")
        if isinstance(single_event, dict):
            events.append(single_event)

        if not events:
            self._logger.error("agent event payload missing event=%s", args)
            return

        for event in events:
            channel = str(event.get("channel", ""))
            if channel != "multi-agent":
                self._logger.warning("unsupported event channel=%s", channel)
                continue

            agent_id = str(event.get("agentId", "primary") or "primary")
            agent_state = dict(self._shared_agents.get(agent_id, {}))
            agent_state["timestamp"] = event.get("timestamp")

            kind = str(event.get("event", ""))
            payload = event.get("payload")
            if isinstance(payload, dict):
                agent_state.setdefault("events", []).append({"kind": kind, "payload": payload})

            if kind == "position" and isinstance(payload, dict):
                agent_state["position"] = payload
                formatted = self._hooks.format_position(payload)
                if formatted:
                    self._hooks.set_memory("player_pos", formatted)
            elif kind == "status" and isinstance(payload, dict):
                agent_state["status"] = payload
                threat = str(payload.get("threatLevel", "")).lower()
                if threat in {"high", "critical"}:
                    self._hooks.request_role_switch("defender", reason="threat-alert")
                supply = str(payload.get("supplyDemand", "")).lower()
                if supply == "shortage":
                    self._hooks.request_role_switch("supplier", reason="supply-shortage")
            elif kind == "roleUpdate" and isinstance(payload, dict):
                role_id = str(payload.get("roleId", "generalist") or "generalist")
                role_label = str(payload.get("label", role_id))
                role_info = {
                    "id": role_id,
                    "label": role_label,
                    "reason": payload.get("reason"),
                    "responsibilities": payload.get("responsibilities"),
                }
                agent_state["role"] = role_info
                if agent_id == "primary":
                    self._hooks.apply_primary_role(role_info)
            elif kind == "perception" and isinstance(payload, dict):
                self._hooks.ingest_perception(payload, source="agent-event")

            self._shared_agents[agent_id] = agent_state

        self._hooks.set_memory("multi_agent", self._shared_agents)
