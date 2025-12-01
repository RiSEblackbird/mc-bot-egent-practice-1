# -*- coding: utf-8 -*-
"""Dependency assembly helpers for AgentOrchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from actions import Actions
from agent_settings import AgentRuntimeSettings
from config import AgentConfig
from memory import Memory
from runtime.action_graph import ActionGraph
from runtime.chat_queue import ChatQueue
from runtime.hybrid_directive import HybridDirectiveHandler
from runtime.inventory_sync import InventorySynchronizer, summarize_inventory_status
from runtime.status_service import StatusService
from runtime.minedojo_handler import MineDojoHandler
from services.minedojo_client import MineDojoClient
from services.skill_repository import SkillRepository
from utils import ThoughtActionObservationTracer


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


__all__ = ["AgentDependencies", "build_agent_dependencies"]
