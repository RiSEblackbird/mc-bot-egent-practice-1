# -*- coding: utf-8 -*-
# 簡易メモリ：今は最小限。必要に応じてDB/ベクトル検索等に拡張。
from typing import Dict, Any

class Memory:
    def __init__(self) -> None:
        self.kv: Dict[str, Any] = {}
    def get(self, key: str, default=None):
        return self.kv.get(key, default)
    def set(self, key: str, value):
        self.kv[key] = value
