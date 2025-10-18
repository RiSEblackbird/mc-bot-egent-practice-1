# -*- coding: utf-8 -*-
"""LLM 連携で利用する簡易オンメモリ辞書。"""

from typing import Any, Dict

from utils import setup_logger


class Memory:
    """辞書ベースの簡易記憶装置（将来的に永続化へ差し替えやすい構造）。"""

    def __init__(self) -> None:
        self.kv: Dict[str, Any] = {}
        self.logger = setup_logger("memory")

    def get(self, key: str, default=None):
        value = self.kv.get(key, default)
        self.logger.debug("memory get key=%s value=%s", key, value)
        return value

    def set(self, key: str, value):
        self.logger.info("memory set key=%s value=%s", key, value)
        self.kv[key] = value
