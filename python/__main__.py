# -*- coding: utf-8 -*-
"""Python エージェント実行用のエントリポイント。"""

from __future__ import annotations

import asyncio
import os
import sys

from runtime.bootstrap import main as runtime_main
from utils import setup_logger

logger = setup_logger("agent.entrypoint")


def main() -> None:
    """Runtime bootstrap を起動する公開エントリポイント。"""
    logger.info(
        "starting python agent entrypoint",
        extra={
            "structured_context": {
                "cwd": os.getcwd(),
                "sys_path": sys.path,
                "pythonpath": os.getenv("PYTHONPATH"),
                "agent_ws_host": os.getenv("AGENT_WS_HOST"),
                "agent_ws_port": os.getenv("AGENT_WS_PORT"),
                "agent_ws_url": os.getenv("AGENT_WS_URL"),
                "ws_url": os.getenv("WS_URL"),
            }
        },
    )
    asyncio.run(runtime_main())


if __name__ == "__main__":
    main()
