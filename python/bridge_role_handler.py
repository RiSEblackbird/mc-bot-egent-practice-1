# -*- coding: utf-8 -*-
"""Bridge listenerとエージェント役割管理のヘルパー。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from bridge_client import BridgeClient
from runtime.bridge_events import BridgeEventHooks, BridgeEventListener
from utils import log_structured_event

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from agent import AgentOrchestrator


class BridgeRoleHandler:
    """Bridge イベント購読と役割ステートの一括管理を担う。"""

    def __init__(self, agent: "AgentOrchestrator") -> None:
        self._agent = agent
        self._logger = agent.logger
        self._shared_agents: Dict[str, Dict[str, Any]] = {}
        self._current_role_id: str = "generalist"
        self._pending_role: Optional[Tuple[str, Optional[str]]] = None
        self._bridge_client = BridgeClient()
        hooks = BridgeEventHooks(
            set_memory=agent.memory.set,
            request_role_switch=self.request_role_switch,
            format_position=agent._format_position_payload,
            ingest_perception=agent.status_service.ingest_perception_snapshot,
            apply_primary_role=self._apply_primary_role_update,
        )
        self._listener = BridgeEventListener(
            bridge_client=self._bridge_client,
            hooks=hooks,
            shared_agents=self._shared_agents,
            logger=self._logger,
        )

    @property
    def current_role(self) -> str:
        return self._current_role_id

    @property
    def bridge_client(self) -> BridgeClient:
        return self._bridge_client

    def request_role_switch(self, role_id: str, *, reason: Optional[str] = None) -> None:
        sanitized = (role_id or "").strip() or "generalist"
        if sanitized == self._current_role_id:
            return
        self._pending_role = (sanitized, reason)
        self._logger.info(
            "pending role switch registered role=%s reason=%s",
            sanitized,
            reason,
        )

    def consume_pending_role_switch(self) -> Optional[Tuple[str, Optional[str]]]:
        pending = self._pending_role
        self._pending_role = None
        return pending

    async def apply_role_switch(self, role_id: str, reason: Optional[str]) -> bool:
        if role_id == self._current_role_id:
            return False

        resp = await self._agent.actions.set_role(role_id, reason=reason)
        if not resp.get("ok"):
            self._logger.warning("role switch command failed role=%s resp=%s", role_id, resp)
            return False

        label = None
        data = resp.get("data")
        if isinstance(data, dict):
            label_raw = data.get("label")
            if isinstance(label_raw, str):
                label = label_raw

        role_info = {
            "id": role_id,
            "label": label or role_id,
            "reason": reason,
        }
        self._current_role_id = role_id
        primary_state = self._shared_agents.setdefault("primary", {})
        primary_state["role"] = role_info
        self._shared_agents["primary"] = primary_state
        self._agent.memory.set("agent_active_role", role_info)
        self._agent.memory.set("multi_agent", self._shared_agents)
        self._logger.info("role switch applied role=%s label=%s", role_id, role_info["label"])
        return True

    async def start_listener(self) -> None:
        await self._listener.start()

    async def stop_listener(self) -> None:
        await self._listener.stop()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        await self._listener.handle_agent_event(args)

    async def handle_bridge_event(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        event_level = str(payload.get("event_level") or "info")
        message = str(payload.get("message") or payload.get("type") or "event")
        region = str(payload.get("region") or "").strip()
        coords_text = self._format_block_pos(payload.get("block_pos"))
        attributes = payload.get("attributes")

        summary_parts = [f"[{event_level}] {message}"]
        if region:
            summary_parts.append(f"region={region}")
        if coords_text:
            summary_parts.append(f"pos={coords_text}")
        if isinstance(attributes, dict) and attributes:
            preview = ", ".join(
                f"{key}={attributes[key]}" for key in list(attributes)[:3]
            )
            if preview:
                summary_parts.append(f"attrs={preview}")
        summary = " / ".join(summary_parts)

        report: Dict[str, Any] = {
            "summary": summary,
            "category": str(payload.get("type") or "bridge_event"),
            "event_level": event_level,
        }
        if region:
            report["region"] = region
        if isinstance(payload.get("block_pos"), dict):
            report["block_pos"] = payload["block_pos"]
        if isinstance(attributes, dict) and attributes:
            report["attributes"] = attributes

        history = self._agent.memory.get("bridge_event_reports", [])
        if not isinstance(history, list):
            history = []
        history.append(report)
        self._agent.memory.set("bridge_event_reports", history[-10:])

        log_structured_event(
            self._logger,
            "bridge event received",
            level=logging.INFO,
            event_level=event_level,
            langgraph_node_id="agent.bridge_events",
            context={
                "region": region or "unknown",
                "summary": summary,
            },
        )

    def augment_failure_reason_with_events(
        self,
        failure_reason: str,
        reports: Sequence[Dict[str, Any]],
    ) -> str:
        if not reports:
            return failure_reason

        latest = reports[-1]
        region = str(latest.get("region") or "").strip()
        coords = self._format_block_pos(latest.get("block_pos"))
        segments: List[str] = []
        if region:
            segments.append(f"保護領域: {region}")
        if coords:
            segments.append(f"座標: {coords}")
        if not segments:
            return failure_reason

        return f"{failure_reason} (最近の検知: {' / '.join(segments)})"

    def _apply_primary_role_update(self, role_info: Dict[str, Any]) -> None:
        role_id = str(role_info.get("id", "generalist") or "generalist")
        self._current_role_id = role_id
        primary_state = self._shared_agents.setdefault("primary", {})
        primary_state["role"] = role_info
        self._shared_agents["primary"] = primary_state
        self._agent.memory.set("agent_active_role", role_info)
        self._agent.memory.set("multi_agent", self._shared_agents)

    def _format_block_pos(self, block_pos: Any) -> str:
        if isinstance(block_pos, dict):
            try:
                x = int(block_pos.get("x"))
                y = int(block_pos.get("y"))
                z = int(block_pos.get("z"))
                return f"X={x} Y={y} Z={z}"
            except Exception:
                return ""
        return ""


__all__ = ["BridgeRoleHandler"]
