# -*- coding: utf-8 -*-
"""Python エージェント実行用のエントリポイント。"""

from __future__ import annotations

import asyncio

import os
import sys
from pathlib import Path

from runtime.bootstrap import main
from utils import setup_logger

logger = setup_logger("agent.entrypoint")

if __name__ == "__main__":
    # runtime パッケージを確実に解決できるよう、python ディレクトリを先頭に追加する。
    project_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(project_dir))
    logger.info(
        "starting python agent entrypoint",
        extra={
            "structured_context": {
                "cwd": os.getcwd(),
                "sys_path": sys.path,
                "pythonpath": os.getenv("PYTHONPATH"),
                "sys_path_added": str(project_dir),
                "agent_ws_host": os.getenv("AGENT_WS_HOST"),
                "agent_ws_port": os.getenv("AGENT_WS_PORT"),
                "agent_ws_url": os.getenv("AGENT_WS_URL"),
                "ws_url": os.getenv("WS_URL"),
            }
        },
    )
    asyncio.run(main())
