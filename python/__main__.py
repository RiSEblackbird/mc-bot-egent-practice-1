# -*- coding: utf-8 -*-
"""Python エージェント実行用のエントリポイント。"""

from __future__ import annotations

import asyncio

from runtime.bootstrap import main

if __name__ == "__main__":
    asyncio.run(main())
