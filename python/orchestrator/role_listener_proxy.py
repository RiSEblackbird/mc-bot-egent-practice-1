# -*- coding: utf-8 -*-
"""RolePerceptionAdapter のイベント購読を肩代わりする薄いプロキシ。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from orchestrator.role_perception_adapter import RolePerceptionAdapter


@dataclass
class RolePerceptionListenerProxy:
    """橋渡し用の監視メソッドを明示するための小さな委譲クラス。

    AgentOrchestrator 本体の責務を絞り込み、イベント購読/停止/処理に
    関するメソッドをまとめて転送する。薄いラッパーでもコメントを付与する
    ことで、新規メンバーがエントリポイントを追いやすくする。
    """

    role_perception: RolePerceptionAdapter

    async def start_bridge_event_listener(self) -> None:
        """AgentBridge のイベント受信ループを開始する。"""

        await self.role_perception.start_bridge_listener()

    async def stop_bridge_event_listener(self) -> None:
        """イベント受信ループを停止し、クリーンアップを委譲する。"""

        await self.role_perception.stop_bridge_listener()

    async def handle_agent_event(self, args: Dict[str, Any]) -> None:
        """Node 側から到着するマルチエージェントイベントを取り込む。"""

        await self.role_perception.handle_agent_event(args)


__all__ = ["RolePerceptionListenerProxy"]
