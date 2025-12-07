# -*- coding: utf-8 -*-
"""AgentOrchestrator の生成と配線を担当するファクトリ群。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from actions import Actions
from agent_bootstrap import AgentDependencies, AgentInitialization, initialize_agent_runtime
from agent_settings import AgentRuntimeSettings
from config import AgentConfig
from memory import Memory
from orchestrator.action_analyzer import ActionAnalyzer
from orchestrator.context import OrchestratorDependencies, PlanRuntimeContext
from orchestrator.plan_executor import PlanExecutor
from orchestrator.role_listener_proxy import RolePerceptionListenerProxy
from orchestrator.role_perception_adapter import RolePerceptionAdapter
from orchestrator.skill_detection import SkillDetectionCoordinator
from orchestrator.task_router import TaskRouter
from services.minedojo_client import MineDojoClient
from services.movement_service import MovementService
from services.skill_repository import SkillRepository
from chat_pipeline import ChatPipeline
from runtime.inventory_sync import InventorySynchronizer
from agent import AgentOrchestrator


@dataclass(frozen=True)
class AgentOrchestratorWiring:
    """AgentOrchestrator へ注入する依存セットのスナップショット。"""

    actions: Actions
    memory: Memory
    settings: AgentRuntimeSettings
    config: AgentConfig
    logger: logging.Logger
    default_move_target: Optional[Tuple[int, int, int]]
    dependencies: AgentDependencies
    movement_service: MovementService
    chat_pipeline: ChatPipeline
    role_perception: RolePerceptionAdapter
    role_listener: RolePerceptionListenerProxy
    plan_runtime: PlanRuntimeContext
    orchestrator_dependencies: OrchestratorDependencies
    action_analyzer: ActionAnalyzer
    skill_detection: SkillDetectionCoordinator
    task_router: TaskRouter
    plan_executor: PlanExecutor


def _build_initialization(
    agent: AgentOrchestrator,
    *,
    actions: Actions,
    memory: Memory,
    skill_repository: Optional[SkillRepository],
    config: Optional[AgentConfig],
    runtime_settings: Optional[AgentRuntimeSettings],
    minedojo_client: Optional[MineDojoClient],
    inventory_sync: Optional[InventorySynchronizer],
    logger: Optional[logging.Logger],
) -> AgentInitialization:
    """AgentOrchestrator の初期化手順を 1 箇所にまとめるヘルパー。"""

    # __init__ では副作用を避け、ここで設定や依存の解決を完結させる。
    return initialize_agent_runtime(
        owner=agent,
        actions=actions,
        memory=memory,
        skill_repository=skill_repository,
        config=config,
        runtime_settings=runtime_settings,
        minedojo_client=minedojo_client,
        inventory_sync=inventory_sync,
        logger=logger,
    )


def assemble_agent_wiring(
    actions: Actions,
    memory: Memory,
    *,
    skill_repository: Optional[SkillRepository] = None,
    config: Optional[AgentConfig] = None,
    runtime_settings: Optional[AgentRuntimeSettings] = None,
    minedojo_client: Optional[MineDojoClient] = None,
    inventory_sync: Optional[InventorySynchronizer] = None,
    logger: Optional[logging.Logger] = None,
    agent: Optional[AgentOrchestrator] = None,
) -> AgentOrchestratorWiring:
    """AgentOrchestrator.__init__ に渡す依存セットを生成するファクトリ。

    1インスタンスのみを終始利用することで、初期化前コールバックが
    別インスタンスを参照する不具合を防ぐ。
    """

    orchestrator = agent or AgentOrchestrator.__new__(AgentOrchestrator)
    # エージェントのメソッドが依存を参照できるよう、最低限の属性を先行で付与する。
    orchestrator.actions = actions
    orchestrator.memory = memory

    bootstrap = _build_initialization(
        orchestrator,
        actions=actions,
        memory=memory,
        skill_repository=skill_repository,
        config=config,
        runtime_settings=runtime_settings,
        minedojo_client=minedojo_client,
        inventory_sync=inventory_sync,
        logger=logger,
    )
    role_listener = RolePerceptionListenerProxy(bootstrap.role_perception)

    return AgentOrchestratorWiring(
        actions=actions,
        memory=memory,
        settings=bootstrap.settings,
        config=bootstrap.config,
        logger=bootstrap.logger,
        default_move_target=bootstrap.default_move_target,
        dependencies=bootstrap.dependencies,
        movement_service=bootstrap.movement_service,
        chat_pipeline=bootstrap.chat_pipeline,
        role_perception=bootstrap.role_perception,
        role_listener=role_listener,
        plan_runtime=bootstrap.plan_runtime,
        orchestrator_dependencies=bootstrap.orchestrator_dependencies,
        action_analyzer=bootstrap.action_analyzer,
        skill_detection=bootstrap.skill_detection,
        task_router=bootstrap.task_router,
        plan_executor=bootstrap.plan_executor,
    )


def create_agent_orchestrator(
    actions: Actions,
    memory: Memory,
    *,
    skill_repository: Optional[SkillRepository] = None,
    config: Optional[AgentConfig] = None,
    runtime_settings: Optional[AgentRuntimeSettings] = None,
    minedojo_client: Optional[MineDojoClient] = None,
    inventory_sync: Optional[InventorySynchronizer] = None,
    logger: Optional[logging.Logger] = None,
) -> AgentOrchestrator:
    """完成済みの配線情報を持つ AgentOrchestrator を返すファクトリ。"""

    agent = AgentOrchestrator.__new__(AgentOrchestrator)
    wiring = assemble_agent_wiring(
        actions,
        memory,
        skill_repository=skill_repository,
        config=config,
        runtime_settings=runtime_settings,
        minedojo_client=minedojo_client,
        inventory_sync=inventory_sync,
        logger=logger,
        agent=agent,
    )
    AgentOrchestrator.__init__(agent, wiring)
    return agent


__all__ = [
    "AgentOrchestratorWiring",
    "assemble_agent_wiring",
    "create_agent_orchestrator",
]
