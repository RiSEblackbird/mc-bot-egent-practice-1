# -*- coding: utf-8 -*-
"""Installable Python agent entrypoint."""

from __future__ import annotations

import asyncio
import os

from runtime.bootstrap import main
from utils import setup_logger

logger = setup_logger("agent.entrypoint")


def run() -> None:
    logger.info(
        "starting python agent entrypoint",
        extra={
            "structured_context": {
                "cwd": os.getcwd(),
                "agent_ws_host": os.getenv("AGENT_WS_HOST"),
                "agent_ws_port": os.getenv("AGENT_WS_PORT"),
                "agent_ws_url": os.getenv("AGENT_WS_URL"),
                "ws_url": os.getenv("WS_URL"),
            }
        },
    )
    asyncio.run(main())


if __name__ == "__main__":
    run()
