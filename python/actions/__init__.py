# -*- coding: utf-8 -*-
"""LLM からの高レベル指示をカテゴリ別に委譲する Actions ファサード。"""

from typing import Any, Awaitable, Callable, Dict, List, Optional

from bridge_ws import BotBridge

from .base import ActionDispatcher
from .building import BuildingActions
from .chat import ChatActions
from .errors import ActionValidationError
from .hybrid import HybridActions
from .management import ManagementActions
from .mining import MiningActions
from .movement import MovementActions
from .skills import SkillActions


class Actions:
    """分割した各アクションモジュールへの委譲を担うファサードクラス。"""

    def __init__(
        self,
        bridge: BotBridge,
        *,
        on_bridge_retry: Optional[Callable[[int, str], Awaitable[None]]] = None,
        on_bridge_give_up: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        # 共通ディスパッチャを用意し、モジュール間で状態とロギングを共有する。
        self._dispatcher = ActionDispatcher(
            bridge,
            on_bridge_retry=on_bridge_retry,
            on_bridge_give_up=on_bridge_give_up,
        )
        # カテゴリ別の実装に委譲し、責務を明確化する。
        self.chat = ChatActions(self._dispatcher)
        self.movement = MovementActions(self._dispatcher)
        self.mining = MiningActions(self._dispatcher)
        self.building = BuildingActions(self._dispatcher)
        self.skills = SkillActions(self._dispatcher)
        self.management = ManagementActions(self._dispatcher)
        self.hybrid = HybridActions(self._dispatcher)

    # directive スコープの操作はディスパッチャが直接管理する。
    def begin_directive_scope(self, meta: Dict[str, Any]) -> None:
        """直後のコマンドへ directive メタデータを付与する。"""

        self._dispatcher.begin_directive_scope(meta)

    def end_directive_scope(self) -> None:
        """directive メタデータのスコープを終了する。"""

        self._dispatcher.end_directive_scope()

    # --- Chat ---
    async def say(self, text: str) -> Dict[str, Any]:
        """チャット送信コマンドを Mineflayer へ中継する。"""

        return await self.chat.say(text)

    # --- Movement & Combat ---
    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """指定座標への移動を要求するコマンドを送信する。"""

        return await self.movement.move_to(x, y, z)

    async def follow_player(
        self,
        target_name: str,
        *,
        stop_distance: int = 2,
        maintain_line_of_sight: bool = True,
    ) -> Dict[str, Any]:
        """指定プレイヤーを追従するコマンドを送信する。"""

        return await self.movement.follow_player(
            target_name,
            stop_distance=stop_distance,
            maintain_line_of_sight=maintain_line_of_sight,
        )

    async def attack_entity(
        self,
        entity_name: str,
        *,
        mode: str = "melee",
        chase_distance: int = 6,
    ) -> Dict[str, Any]:
        """対象エンティティへの戦闘コマンドを送信する。"""

        return await self.movement.attack_entity(
            entity_name,
            mode=mode,
            chase_distance=chase_distance,
        )

    # --- Mining ---
    async def mine_blocks(self, positions: List[Dict[str, int]]) -> Dict[str, Any]:
        """断面で破壊すべき座標を Mineflayer へ渡す。"""

        return await self.mining.mine_blocks(positions)

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int = 12,
        max_targets: int = 3,
    ) -> Dict[str, Any]:
        """周囲の鉱石を探索・採掘するコマンドを送信する。"""

        return await self.mining.mine_ores(
            ore_names,
            scan_radius=scan_radius,
            max_targets=max_targets,
        )

    # --- Building / Crafting ---
    async def place_torch(self, position: Dict[str, int]) -> Dict[str, Any]:
        """たいまつを指定位置に設置するコマンドを送信する。"""

        return await self.building.place_torch(position)

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        """指定した種類のアイテムを手に持ち替える。"""

        return await self.building.equip_item(
            tool_type=tool_type,
            item_name=item_name,
            destination=destination,
        )

    async def place_block(
        self,
        block: str,
        position: Dict[str, int],
        *,
        face: Optional[str] = None,
        sneak: bool = False,
    ) -> Dict[str, Any]:
        """任意のブロックを指定位置へ設置するコマンドを送信する。"""

        return await self.building.place_block(
            block,
            position,
            face=face,
            sneak=sneak,
        )

    async def craft_item(
        self,
        item_name: str,
        *,
        amount: int = 1,
        use_crafting_table: bool = True,
    ) -> Dict[str, Any]:
        """クラフトレシピを指定して作業台/インベントリで作成する。"""

        return await self.building.craft_item(
            item_name,
            amount=amount,
            use_crafting_table=use_crafting_table,
        )

    # --- Management ---
    async def set_role(self, role_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        """LangGraph からの役割切替を Node 側へ送信する。"""

        return await self.management.set_role(role_id, reason=reason)

    async def gather_status(self, kind: str) -> Dict[str, Any]:
        """Mineflayer 側から位置・所持品などのステータス情報を取得する。"""

        return await self.management.gather_status(kind)

    # --- Skill operations ---
    async def register_skill(
        self,
        *,
        skill_id: str,
        title: str,
        description: str,
        steps: List[str],
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """スキル定義を Mineflayer 側へ登録する。"""

        return await self.skills.register_skill(
            skill_id=skill_id,
            title=title,
            description=description,
            steps=steps,
            tags=tags,
        )

    async def invoke_skill(
        self,
        skill_id: str,
        *,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """登録済みスキルの再生を要求する。"""

        return await self.skills.invoke_skill(skill_id, context=context)

    async def begin_skill_exploration(
        self,
        *,
        skill_id: str,
        description: str,
        step_context: str,
    ) -> Dict[str, Any]:
        """未習得スキルの探索モードを Mineflayer へ通知する。"""

        return await self.skills.begin_skill_exploration(
            skill_id=skill_id,
            description=description,
            step_context=step_context,
        )

    # --- Hybrid ---
    async def play_vpt_actions(
        self,
        actions: List[Dict[str, Any]],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """VPT で生成した低レベル操作列を Mineflayer へ転送する。"""

        return await self.hybrid.play_vpt_actions(actions, metadata=metadata)

    async def execute_hybrid_action(
        self,
        *,
        vpt_actions: Optional[List[Dict[str, Any]]],
        fallback_command: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """VPT 再生と通常コマンドを安全に切り替えるハイブリッド実行を提供する。"""

        return await self.hybrid.execute_hybrid_action(
            vpt_actions=vpt_actions,
            fallback_command=fallback_command,
            metadata=metadata,
        )


__all__ = ["Actions", "ActionValidationError"]
