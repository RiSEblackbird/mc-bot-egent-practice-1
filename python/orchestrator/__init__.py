# -*- coding: utf-8 -*-
"""AgentOrchestrator を構成するモジュール群。

単一ファイルへ集約されていた計画実行・タスク分類・検出/スキル補助を
専門モジュールへ切り出すことで、1 ファイルあたりのコンテクストサイズを
縮小しつつ、依存関係を明示する。実装クラスは `context.py` で定義された
依存セットを参照する。
"""

from .context import OrchestratorDependencies, PlanRuntimeContext

__all__ = [
    "OrchestratorDependencies",
    "PlanRuntimeContext",
]
