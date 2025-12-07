# -*- coding: utf-8 -*-
"""Dependency assembly helpers for AgentOrchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from actions import Actions
from agent_settings import AgentRuntimeSettings, DEFAULT_AGENT_RUNTIME_SETTINGS
from config import AgentConfig
from memory import Memory
from chat_pipeline import ChatPipeline
from orchestrator.action_analyzer import ActionAnalyzer
from orchestrator.context import OrchestratorDependencies, PlanRuntimeContext
from orchestrator.plan_executor import PlanExecutor
from orchestrator.role_perception_adapter import RolePerceptionAdapter
from orchestrator.skill_detection import SkillDetectionCoordinator
from orchestrator.task_router import TaskRouter
from services.movement_service import MovementService
from runtime.action_graph import ActionGraph
from runtime.chat_queue import ChatQueue
from runtime.hybrid_directive import HybridDirectiveHandler
from runtime.inventory_sync import InventorySynchronizer, summarize_inventory_status
from runtime.status_service import StatusService
from runtime.minedojo_handler import MineDojoHandler
from services.minedojo_client import MineDojoClient
from services.skill_repository import SkillRepository
from utils import ThoughtActionObservationTracer, setup_logger


@dataclass(frozen=True)
class AgentDependencies:
    """Container for fully-wired AgentOrchestrator collaborators."""

    skill_repository: SkillRepository
    tracer: ThoughtActionObservationTracer
    inventory_sync: InventorySynchronizer
    status_service: StatusService
    action_graph: ActionGraph
    chat_queue: ChatQueue
    minedojo_client: MineDojoClient
    minedojo_handler: MineDojoHandler
    hybrid_handler: HybridDirectiveHandler


@dataclass(frozen=True)
class AgentInitialization:
    """Aggregate object returned by initialize_agent_runtime().

    AgentOrchestrator.__init__ から副作用を切り離し、注入する設定値と
    下位依存を 1 箇所で確認できるようにまとめる。
    """

    settings: AgentRuntimeSettings
    config: AgentConfig
    logger: logging.Logger
    default_move_target: Optional[Tuple[int, int, int]]
    dependencies: AgentDependencies
    movement_service: MovementService
    chat_pipeline: ChatPipeline
    role_perception: RolePerceptionAdapter
    plan_runtime: PlanRuntimeContext
    orchestrator_dependencies: OrchestratorDependencies
    action_analyzer: ActionAnalyzer
    skill_detection: SkillDetectionCoordinator
    task_router: TaskRouter
    plan_executor: PlanExecutor


def build_agent_dependencies(
    *,
    owner: "AgentOrchestrator",
    actions: Actions,
    memory: Memory,
    config: AgentConfig,
    settings: AgentRuntimeSettings,
    logger: logging.Logger,
    skill_repository: Optional[SkillRepository] = None,
    inventory_sync: Optional[InventorySynchronizer] = None,
    minedojo_client: Optional[MineDojoClient] = None,
) -> AgentDependencies:
    """Instantiate complex Agent collaborators from a single location."""

    repo = skill_repository
    if repo is None:
        seed_path = Path(__file__).resolve().parent / "skills" / "seed_library.json"
        repo = SkillRepository(
            settings.skill_library_path,
            seed_path=str(seed_path),
        )

    langsmith_cfg = config.langsmith
    tracer = ThoughtActionObservationTracer(
        api_url=langsmith_cfg.api_url,
        api_key=langsmith_cfg.api_key,
        project=langsmith_cfg.project,
        default_tags=langsmith_cfg.tags,
        enabled=langsmith_cfg.enabled,
    )

    inv_sync = inventory_sync or InventorySynchronizer(
        summarizer=summarize_inventory_status
    )
    status_service = StatusService(
        actions=actions,
        memory=memory,
        inventory_sync=inv_sync,
        logger=logger,
        status_timeout_seconds=settings.status_refresh_timeout_seconds,
        status_retry=settings.status_refresh_retry,
        status_backoff_seconds=settings.status_refresh_backoff_seconds,
        structured_event_history_limit=settings.structured_event_history_limit,
        perception_history_limit=settings.perception_history_limit,
    )

    chat_queue = ChatQueue(
        process_task=owner._process_chat,
        say=owner._safe_say,
        queue_max_size=config.queue_max_size,
        task_timeout_seconds=config.worker_task_timeout_seconds,
        timeout_retry_limit=owner._MAX_TASK_TIMEOUT_RETRY,
        logger=logger,
    )

    action_graph = ActionGraph(owner)
    minedojo_client_obj = minedojo_client or MineDojoClient(config.minedojo)
    minedojo_handler = MineDojoHandler(
        actions=actions,
        memory=memory,
        skill_repository=repo,
        minedojo_client=minedojo_client_obj,
        tracer=tracer,
        config=config,
        logger=logger,
    )

    hybrid_handler = HybridDirectiveHandler(owner)

    return AgentDependencies(
        skill_repository=repo,
        tracer=tracer,
        inventory_sync=inv_sync,
        status_service=status_service,
        action_graph=action_graph,
        chat_queue=chat_queue,
        minedojo_client=minedojo_client_obj,
        minedojo_handler=minedojo_handler,
        hybrid_handler=hybrid_handler,
    )


def initialize_agent_runtime(
    *,
    owner: "AgentOrchestrator",
    actions: Actions,
    memory: Memory,
    skill_repository: Optional[SkillRepository] = None,
    config: Optional[AgentConfig] = None,
    runtime_settings: Optional[AgentRuntimeSettings] = None,
    minedojo_client: Optional[MineDojoClient] = None,
    inventory_sync: Optional[InventorySynchronizer] = None,
    logger: Optional[logging.Logger] = None,
) -> AgentInitialization:
    """Assemble orchestrator dependencies and runtime context in one place."""

    settings = runtime_settings or DEFAULT_AGENT_RUNTIME_SETTINGS
    resolved_config = config or settings.config
    orchestrator_logger = logger or setup_logger("agent.orchestrator")

    # Agent 属性に対する副作用をここへ閉じ込め、初期化順序を一本化する。
    owner.logger = orchestrator_logger

    dependencies = build_agent_dependencies(
        owner=owner,
        actions=actions,
        memory=memory,
        config=resolved_config,
        settings=settings,
        logger=orchestrator_logger,
        skill_repository=skill_repository,
        inventory_sync=inventory_sync,
        minedojo_client=minedojo_client,
    )

    # BridgeRoleHandler などから参照されるため、アダプタ組み立て前にエージェントへ公開する。
    owner.status_service = dependencies.status_service

    role_perception = RolePerceptionAdapter(owner)
    # PlanExecutor などが __init__ 前にフォールバックアクセスするため、最低限の属性を先に付与しておく。
    owner._role_perception = role_perception  # noqa: SLF001
    chat_pipeline = ChatPipeline(owner)
    # ChatQueue のコールバックが __init__ 後の遅延評価でも必ずパイプラインへアクセスできるよう、
    # 生成直後にエージェントへ束縛しておく。
    owner._chat_pipeline = chat_pipeline  # noqa: SLF001
    movement_service = MovementService(
        actions=actions,
        memory=memory,
        perception=role_perception.perception,
        logger=orchestrator_logger,
    )
    plan_runtime = PlanRuntimeContext(
        default_move_target=resolved_config.default_move_target,
        low_food_threshold=settings.low_food_threshold,
        structured_event_history_limit=settings.structured_event_history_limit,
        perception_history_limit=settings.perception_history_limit,
    )
    action_analyzer = ActionAnalyzer()
    # ChatQueue 側のコールバックが初期化順序に依存しないよう、解析系の依存も先行して束縛する。
    owner._action_analyzer = action_analyzer  # noqa: SLF001
    skill_detection = SkillDetectionCoordinator(
        actions=actions,
        memory=memory,
        status_service=dependencies.status_service,
        inventory_sync=dependencies.inventory_sync,
        skill_repository=dependencies.skill_repository,
    )
    task_router = TaskRouter(
        action_analyzer=action_analyzer,
        chat_pipeline=chat_pipeline,
        skill_detection=skill_detection,
        minedojo_handler=dependencies.minedojo_handler,
        report_execution_barrier=movement_service.report_execution_barrier,
        logger=orchestrator_logger,
    )

    orchestrator_dependencies = OrchestratorDependencies(
        actions=actions,
        memory=memory,
        chat_pipeline=chat_pipeline,
        role_perception=role_perception,
        bridge_roles=role_perception.bridge_roles,
        perception=role_perception.perception,
        status_service=dependencies.status_service,
        inventory_sync=dependencies.inventory_sync,
        hybrid_handler=dependencies.hybrid_handler,
        minedojo_handler=dependencies.minedojo_handler,
        tracer=dependencies.tracer,
        runtime_settings=settings,
        movement_service=movement_service,
        skill_repository=dependencies.skill_repository,
        task_router=task_router,
    )
    plan_executor = PlanExecutor(
        agent=owner, dependencies=orchestrator_dependencies, runtime=plan_runtime
    )

    return AgentInitialization(
        settings=settings,
        config=resolved_config,
        logger=orchestrator_logger,
        default_move_target=resolved_config.default_move_target,
        dependencies=dependencies,
        movement_service=movement_service,
        chat_pipeline=chat_pipeline,
        role_perception=role_perception,
        plan_runtime=plan_runtime,
        orchestrator_dependencies=orchestrator_dependencies,
        action_analyzer=action_analyzer,
        skill_detection=skill_detection,
        task_router=task_router,
        plan_executor=plan_executor,
    )


__all__ = [
    "AgentDependencies",
    "AgentInitialization",
    "build_agent_dependencies",
    "initialize_agent_runtime",
]
