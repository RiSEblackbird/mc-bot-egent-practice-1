# -*- coding: utf-8 -*-
"""エージェント起動のブートストラップ処理をまとめるモジュール。"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv
from websockets.server import serve

from config import AgentConfig, load_agent_config
from actions import Actions
from bridge_ws import BotBridge
from memory import Memory
from services.skill_repository import SkillRepository
from utils import setup_logger
from runtime.websocket_server import AgentWebSocketServer
from runtime.minedojo import run_minedojo_self_dialogue
from agent import AgentOrchestrator
from agent_lifecycle import create_agent_orchestrator
from dashboard import DashboardServer

logger = setup_logger("agent.bootstrap")


def load_runtime_config() -> AgentConfig:
    """環境変数を読み込み、AgentConfig を返却するヘルパー。"""

    load_dotenv()
    return load_agent_config().config


def build_dependencies(config: AgentConfig) -> Tuple[BotBridge, Actions, Memory, SkillRepository]:
    """エージェントが利用する依存オブジェクトを生成する。"""

    bridge = BotBridge(config.ws_url)
    actions = Actions(bridge)
    memory = Memory()
    seed_path = Path(__file__).resolve().parent.parent / "skills" / "seed_library.json"
    skill_repo = SkillRepository(config.skill_library_path, seed_path=str(seed_path))
    return bridge, actions, memory, skill_repo


async def main() -> None:
    """エージェントを起動し、WebSocket サーバーとワーカーを開始する。"""

    config = load_runtime_config()
    logger.info(
        "bootstrapping agent ws_url=%s agent_host=%s agent_port=%s dashboard_enabled=%s",
        config.ws_url,
        config.agent_host,
        config.agent_port,
        config.dashboard.enabled,
    )
    _, actions, memory, skill_repo = build_dependencies(config)
    orchestrator = create_agent_orchestrator(
        actions,
        memory,
        skill_repository=skill_repo,
        config=config,
    )
    ws_server = AgentWebSocketServer(orchestrator)
    dashboard_server: DashboardServer | None = None
    if config.dashboard.enabled:
        try:
            dashboard_server = DashboardServer(
                orchestrator,
                host=config.dashboard.host,
                port=config.dashboard.port,
                access_token=config.dashboard.access_token,
            )
            await dashboard_server.start()
        except Exception:
            logger.exception(
                "failed to start dashboard server host=%s port=%s",
                config.dashboard.host,
                config.dashboard.port,
            )
    await orchestrator.start_bridge_event_listener()

    worker_task = asyncio.create_task(orchestrator.worker(), name="agent-worker")

    try:
        async with serve(ws_server.handler, config.agent_host, config.agent_port):
            logger.info(
                "Python agent is listening on ws://%s:%s (ws_url=%s)",
                config.agent_host,
                config.agent_port,
                config.ws_url,
            )
            try:
                await asyncio.Future()  # 実行を継続
            except asyncio.CancelledError:
                logger.info("main loop cancelled")
            finally:
                worker_task.cancel()
                with contextlib.suppress(Exception):
                    await worker_task
                await orchestrator.stop_bridge_event_listener()
                if dashboard_server:
                    await dashboard_server.stop()
    except Exception:
        logger.exception(
            "agent bootstrap failed host=%s port=%s ws_url=%s",
            config.agent_host,
            config.agent_port,
            config.ws_url,
        )
        raise


__all__ = [
    "AgentOrchestrator",
    "create_agent_orchestrator",
    "main",
    "load_runtime_config",
    "run_minedojo_self_dialogue",
]
