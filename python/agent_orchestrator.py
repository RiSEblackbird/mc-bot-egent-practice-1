# -*- coding: utf-8 -*-
"""LangGraph 連携モジュールの後方互換ラッパー。

主要ロジックは `python/runtime` 配下へ移設したため、このモジュールは
既存の import を壊さないようにエイリアスだけを提供する。新規開発では
`runtime.action_graph` や `runtime.reflection_prompt` を直接参照すること。
"""

from __future__ import annotations

from runtime.action_graph import ActionGraph, ActionTaskRule, ChatTask, UnifiedAgentGraph
from runtime.reflection_prompt import build_reflection_prompt
from runtime.minedojo import MineDojoSelfDialogueExecutor

__all__ = [
    "ActionGraph",
    "ActionTaskRule",
    "ChatTask",
    "UnifiedAgentGraph",
    "build_reflection_prompt",
    "MineDojoSelfDialogueExecutor",
]
