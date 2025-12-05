# -*- coding: utf-8 -*-
"""Bridge/Perception 系処理を束ねるアダプタ。

AgentOrchestrator が担っていた役割切替や perception 周りのラッパーを
1 箇所へまとめ、構造化ログと例外ハンドリングを統一する。テストからも
直接呼び出しやすい薄い API とし、副作用を明確に管理できるようにする。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from bridge_role_handler import BridgeRoleHandler
from perception_service import PerceptionCoordinator
from utils import log_structured_event

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from agent import AgentOrchestrator


class RolePerceptionAdapter:
    """BridgeRoleHandler と PerceptionCoordinator の統合アダプタ。"""

    def __init__(self, agent: "AgentOrchestrator") -> None:
        # AgentOrchestrator へ副作用を隠蔽しつつ、必要な依存だけを保持する。
        self._agent = agent
        self.logger = agent.logger
        self.bridge_roles = BridgeRoleHandler(agent)
        self.perception = PerceptionCoordinator(agent, bridge_roles=self.bridge_roles)

    @property
    def current_role(self) -> str:
        return self.bridge_roles.current_role

    def request_role_switch(self, role_id: str, *, reason: Optional[str] = None) -> None:
        self.bridge_roles.request_role_switch(role_id, reason=reason)

    def consume_pending_role_switch(self) -> Optional[Tuple[str, Optional[str]]]:
        return self.bridge_roles.consume_pending_role_switch()

    async def apply_role_switch(self, role_id: str, reason: Optional[str]) -> bool:
        try:
            applied = await self.bridge_roles.apply_role_switch(role_id, reason)
        except Exception as exc:  # pragma: no cover - 例外経路はログを優先
            self._log_adapter_error(
                "role_switch_failed",
                exc,
                {"role_id": role_id, "reason": reason or ""},
            )
            raise

        event_level = "info" if applied else "debug"
        log_structured_event(
            self.logger,
            "role_switch_result",
            level=logging.INFO,
            event_level=event_level,
            langgraph_node_id="agent.role_adapter",
            context={"role_id": role_id, "applied": applied, "reason": reason},
        )
        return applied

    def collect_recent_mineflayer_context(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        try:
            return self.perception.collect_recent_mineflayer_context()
        except Exception as exc:
            self._log_adapter_error(
                "collect_recent_context_failed",
                exc,
                {"current_role": self.current_role},
            )
            raise

    def build_perception_snapshot(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            return self.perception.build_perception_snapshot(extra)
        except Exception as exc:
            self._log_adapter_error(
                "build_perception_snapshot_failed",
                exc,
                {"has_extra": bool(extra)},
            )
            raise

    def ingest_perception_snapshot(
        self, snapshot: Dict[str, Any], *, source: str
    ) -> None:
        try:
            self.perception.ingest_perception_snapshot(snapshot, source=source)
        except Exception as exc:
            self._log_adapter_error(
                "ingest_perception_snapshot_failed",
                exc,
                {"source": source, "keys": list(snapshot.keys())},
            )
            raise

    async def collect_block_evaluations(self) -> None:
        try:
            await self.perception.collect_block_evaluations()
        except Exception as exc:
            self._log_adapter_error(
                "collect_block_evaluations_failed",
                exc,
                {"current_role": self.current_role},
            )
            raise

    async def start_bridge_listener(self) -> None:
        await self.bridge_roles.start_listener()

    async def stop_bridge_listener(self) -> None:
        await self.bridge_roles.stop_listener()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        await self.bridge_roles.handle_agent_event(args)

    def augment_failure_reason_with_events(
        self, failure_reason: str, reports: List[Dict[str, Any]]
    ) -> str:
        return self.bridge_roles.augment_failure_reason_with_events(
            failure_reason, reports
        )

    def _log_adapter_error(self, event: str, exc: Exception, context: Dict[str, Any]) -> None:
        log_structured_event(
            self.logger,
            event,
            level=logging.ERROR,
            event_level="error",
            langgraph_node_id="agent.role_adapter",
            context={**context, "error": str(exc)},
        )


__all__ = ["RolePerceptionAdapter"]
