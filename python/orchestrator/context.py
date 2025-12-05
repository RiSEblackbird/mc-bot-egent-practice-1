# -*- coding: utf-8 -*-
"""AgentOrchestrator モジュール間で共有する依存セット。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from actions import Actions
    from agent_settings import AgentRuntimeSettings
    from bridge_role_handler import BridgeRoleHandler
    from chat_pipeline import ChatPipeline
    from perception_service import PerceptionCoordinator
    from orchestrator.role_perception_adapter import RolePerceptionAdapter
    from runtime.hybrid_directive import HybridDirectiveHandler
    from runtime.inventory_sync import InventorySynchronizer
    from runtime.minedojo_handler import MineDojoHandler
    from runtime.status_service import StatusService
    from services.skill_repository import SkillRepository
    from utils import ThoughtActionObservationTracer
    from memory import Memory


@dataclass(frozen=True)
class PlanRuntimeContext:
    """PlanExecutor へ渡す閾値・設定値。"""

    default_move_target: Optional[Tuple[int, int, int]]
    low_food_threshold: int
    structured_event_history_limit: int
    perception_history_limit: int


@dataclass(frozen=True)
class OrchestratorDependencies:
    """AgentOrchestrator が下位モジュールへ提供する共有依存。"""

    actions: "Actions"
    memory: "Memory"
    chat_pipeline: "ChatPipeline"
    role_perception: "RolePerceptionAdapter"
    bridge_roles: "BridgeRoleHandler"
    perception: "PerceptionCoordinator"
    status_service: "StatusService"
    inventory_sync: "InventorySynchronizer"
    hybrid_handler: "HybridDirectiveHandler"
    minedojo_handler: "MineDojoHandler"
    tracer: "ThoughtActionObservationTracer"
    runtime_settings: "AgentRuntimeSettings"
    skill_repository: Optional["SkillRepository"] = None

