# -*- coding: utf-8 -*-
"""検出系タスクとスキル補助ロジックを担当するモジュール。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from actions import Actions
from memory import Memory
from runtime.inventory_sync import InventorySynchronizer
from runtime.status_service import StatusService
from services.skill_repository import SkillRepository
from skills import SkillMatch


@dataclass
class SkillDetectionCoordinator:
    """AgentOrchestrator から副作用を切り離した仲介クラス。"""

    actions: Actions
    memory: Memory
    status_service: StatusService
    inventory_sync: InventorySynchronizer
    skill_repository: SkillRepository

    async def perform_detection_task(
        self, category: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if category == "player_position":
            resp = await self.actions.gather_status("position")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が現在位置を返しませんでした。"
                return None, error_detail
            data = resp.get("data") or {}
            summary = self.summarize_position_status(data)
            self.memory.set("player_pos", summary)
            self.memory.set("player_pos_detail", data)
            return {"category": category, "summary": summary, "data": data}, None

        if category == "inventory_status":
            resp = await self.actions.gather_status("inventory")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が所持品を返しませんでした。"
                return None, error_detail
            data = resp.get("data") or {}
            summary = self.inventory_sync.summarize(data)
            self.memory.set("inventory", summary)
            self.memory.set("inventory_detail", data)
            return {"category": category, "summary": summary, "data": data}, None

        if category == "general_status":
            resp = await self.actions.gather_status("general")
            if not resp.get("ok"):
                error_detail = resp.get("error") or "Mineflayer が状態値を返しませんでした。"
                return None, error_detail
            data = resp.get("data") or {}
            summary = self.summarize_general_status(data)
            self.memory.set("general_status", summary)
            self.memory.set("general_status_detail", data)
            if isinstance(data, dict) and "digPermission" in data:
                self.memory.set("dig_permission", data.get("digPermission"))
            return {"category": category, "summary": summary, "data": data}, None

        return None, None

    def summarize_position_status(self, data: Dict[str, Any]) -> str:
        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            position = data.get("position")
            if isinstance(position, dict):
                x = position.get("x")
                y = position.get("y")
                z = position.get("z")
                dimension = data.get("dimension") or "unknown"
                if all(isinstance(value, int) for value in (x, y, z)):
                    return f"現在位置は X={x} / Y={y} / Z={z}（ディメンション: {dimension}）です。"

        return "現在位置の最新情報を取得しました。"

    def summarize_general_status(self, data: Dict[str, Any]) -> str:
        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            health = data.get("health")
            max_health = data.get("maxHealth")
            food = data.get("food")
            saturation = data.get("saturation")
            dig_permission = data.get("digPermission")
            if all(
                isinstance(value, (int, float))
                for value in (health, max_health, food, saturation)
            ) and isinstance(dig_permission, dict):
                allowed = dig_permission.get("allowed")
                reason = dig_permission.get("reason")
                permission_text = "あり" if allowed else f"なし（{reason}）"
                return (
                    f"体力: {int(health)}/{int(max_health)}、満腹度: {int(food)}/20、飽和度: {float(saturation):.1f}、"
                    f"採掘許可: {permission_text}。"
                )

        return "体力や採掘許可の現在値を確認しました。"

    async def find_skill_for_step(
        self, minedojo_handler: Any, category: str, step: str
    ) -> Optional[SkillMatch]:
        return await minedojo_handler.find_skill_for_step(category, step)

    async def execute_skill_match(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        if not hasattr(self.actions, "invoke_skill"):
            return False, None

        resp = await self.actions.invoke_skill(match.skill.identifier, context=step)
        if resp.get("ok"):
            await self.skill_repository.record_usage(match.skill.identifier, success=True)
            self.memory.set(
                "last_skill_usage",
                {"skill_id": match.skill.identifier, "title": match.skill.title, "step": step},
            )
            return True, None

        await self.skill_repository.record_usage(match.skill.identifier, success=False)
        error_detail = resp.get("error")
        if isinstance(error_detail, str) and "is not registered" in error_detail:
            return False, None

        error_detail = error_detail or "Mineflayer 側でスキル再生が拒否されました"
        return False, f"スキル『{match.skill.title}』の再生に失敗しました: {error_detail}"

    async def begin_skill_exploration(
        self,
        match: SkillMatch,
        step: str,
    ) -> Tuple[bool, Optional[str]]:
        if not hasattr(self.actions, "begin_skill_exploration"):
            return False, None

        resp = await self.actions.begin_skill_exploration(
            skill_id=match.skill.identifier,
            description=match.skill.description,
            step_context=step,
        )
        if resp.get("ok"):
            self.memory.set(
                "last_skill_exploration",
                {"skill_id": match.skill.identifier, "title": match.skill.title, "step": step},
            )
            return True, None

        error_detail = resp.get("error") or "探索モードへの切り替えが Mineflayer 側で拒否されました"
        return False, error_detail


__all__ = ["SkillDetectionCoordinator"]
