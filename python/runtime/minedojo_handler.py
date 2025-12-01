# -*- coding: utf-8 -*-
"""MineDojo 連携の実装を集約したハンドラー。

AgentOrchestrator からはインジェクションされた依存だけで利用できるようにし、
ミッション解決・デモ取得・スキル登録といった MineDojo 固有の責務を一箇所へ
閉じ込める。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from actions import Actions
from config import AgentConfig
from memory import Memory
from planner import ActionDirective, PlanOut, ReActStep
from runtime.minedojo import MineDojoSelfDialogueExecutor
from services.minedojo_client import (
    MineDojoClient,
    MineDojoDemoMetadata,
    MineDojoDemonstration,
    MineDojoMission,
)
from services.skill_repository import SkillRepository
from skills import SkillMatch, SkillNode
from utils import ThoughtActionObservationTracer, log_structured_event


class MineDojoHandler:
    """MineDojo まわりの副作用をまとめる仲介クラス。"""

    _MISSION_BINDINGS: Dict[str, str] = {
        "mine": "obtain_diamond",
        "farm": "harvest_wheat",
        "build": "build_simple_house",
    }

    def __init__(
        self,
        *,
        actions: Actions,
        memory: Memory,
        skill_repository: SkillRepository,
        minedojo_client: MineDojoClient,
        tracer: ThoughtActionObservationTracer,
        config: AgentConfig,
        logger: logging.Logger,
    ) -> None:
        # 振る舞いを外部から注入して、テスト時はスタブへ差し替えられるようにする。
        self.actions = actions
        self.memory = memory
        self.skill_repository = skill_repository
        self.minedojo_client = minedojo_client
        self.logger = logger

        self._self_dialogue_executor = MineDojoSelfDialogueExecutor(
            actions=self.actions,
            client=self.minedojo_client,
            skill_repository=self.skill_repository,
            tracer=tracer,
            env_params={
                "sim_env": config.minedojo.sim_env,
                "sim_seed": config.minedojo.sim_seed,
                "sim_max_steps": config.minedojo.sim_max_steps,
            },
        )
        # 直近のミッションとデモを保持し、スキル登録やタグ生成に再利用する。
        self._active_mission: Optional[MineDojoMission] = None
        self._active_demos: List[MineDojoDemonstration] = []
        self._active_mission_id: Optional[str] = None
        self._active_demo_metadata: Optional[MineDojoDemoMetadata] = None

    async def handle_directive(
        self, directive: ActionDirective, plan_out: PlanOut, step_index: int
    ) -> bool:
        """計画内の MineDojo 指示を自己対話実行へ委譲する。"""

        mission_id = self._resolve_mission_id_from_directive(directive, plan_out)
        if not mission_id:
            return False

        skill_id = self._resolve_skill_id(directive, mission_id)
        title = directive.label or directive.step or f"MineDojo {mission_id}"
        success_flag = directive.args.get("simulate_success") if isinstance(directive.args, dict) else None
        success = bool(success_flag) if isinstance(success_flag, bool) else True

        try:
            await self._self_dialogue_executor.run_self_dialogue(
                mission_id,
                plan_out.react_trace or [],
                skill_id=skill_id,
                title=title,
                success=success,
            )
        except Exception:
            self.logger.exception(
                "MineDojo directive failed mission=%s step_index=%d", mission_id, step_index
            )
            return False

        self.logger.info(
            "MineDojo directive executed mission=%s skill_id=%s step_index=%d",
            mission_id,
            skill_id,
            step_index,
        )
        return True

    async def maybe_trigger_autorecovery(self, plan_out: PlanOut) -> bool:
        """手順欠損時に MineDojo 自己対話で補完する。"""

        intent = (plan_out.intent or "").strip()
        react_trace: List[ReActStep] = list(getattr(plan_out, "react_trace", []) or [])
        has_steps = bool(plan_out.plan)
        trigger_for_empty_plan = not has_steps
        trigger_for_minedojo_intent = bool(
            intent and intent in self._MISSION_BINDINGS and react_trace
        )
        if not (trigger_for_empty_plan or trigger_for_minedojo_intent):
            return False

        mission_id = self._resolve_mission_id_from_plan(plan_out)
        if not mission_id:
            return False

        if not react_trace:
            react_trace = [
                ReActStep(
                    thought="LLM が手順を返さなかったため、自己対話ログで補完する",
                    action="self_dialogue",
                    observation="",
                )
            ]

        skill_id = f"autorecover::{mission_id}::{int(time.time())}"
        try:
            await self._self_dialogue_executor.run_self_dialogue(
                mission_id,
                react_trace,
                skill_id=skill_id,
                title=f"Auto recovery for {mission_id}",
                success=False,
            )
        except Exception:
            self.logger.exception("MineDojo autorecovery failed mission=%s", mission_id)
            return False

        await self.actions.say(
            "十分な手順を生成できなかったため、記録済みの自己対話ログを参照して計画を立て直します。"
        )
        log_structured_event(
            self.logger,
            "minedojo_autorecovery_triggered",
            event_level="recovery",
            context={
                "mission_id": mission_id,
                "intent": intent or "(unknown)",
                "reason": "empty_plan" if trigger_for_empty_plan else "intent_directive",
            },
        )
        return True

    async def attach_context(self, category: str, step: str) -> None:
        """アクション実行前に MineDojo のミッション・デモ情報を取得してメモリへ共有する。"""

        mission_id = self._MISSION_BINDINGS.get(category)
        if not mission_id:
            return
        if not self.minedojo_client:
            return

        if mission_id == self._active_mission_id and self._active_demos:
            return

        mission: Optional[MineDojoMission] = None
        demos: List[MineDojoDemonstration] = []
        metadata_list: List[MineDojoDemoMetadata] = []

        mission = await self.minedojo_client.fetch_mission(mission_id)
        demos = await self.minedojo_client.fetch_demonstrations(mission_id, limit=1)
        if hasattr(self.minedojo_client, "fetch_demo_metadata"):
            metadata_list = await self.minedojo_client.fetch_demo_metadata(mission_id, limit=1)  # type: ignore[attr-defined]
        if not metadata_list and demos:
            mission_tags = mission.tags if mission else ()
            metadata_list = [
                demo.to_metadata(mission_tags=mission_tags)
                for demo in demos
                if hasattr(demo, "to_metadata")
            ]

        self._active_mission = mission
        self._active_demos = demos
        self._active_mission_id = mission_id if (mission or demos) else None
        if metadata_list:
            self._active_demo_metadata = metadata_list[0]
        else:
            self._active_demo_metadata = None

        context_payload = self._build_context_payload(mission, demos, metadata_list)
        if context_payload:
            self.memory.set("minedojo_context", context_payload)

        if demos and self._active_demo_metadata:
            await self._prime_actions_with_demo(demos[0], self._active_demo_metadata)
            await self._register_minedojo_demo_skill(
                mission,
                self._active_demo_metadata,
                demos[0],
            )

    async def find_skill_for_step(self, category: str, step: str) -> Optional[SkillMatch]:
        """MineDojo 状態に応じたタグ付きでスキル検索する。"""

        try:
            mission_id = self._active_mission_id
            context_tags: List[str] = []
            if self._active_demo_metadata:
                context_tags.extend(list(self._active_demo_metadata.tags))
                context_tags.append("minedojo")
                context_tags.append(self._active_demo_metadata.mission_id)
            if self._active_mission:
                context_tags.extend(list(self._active_mission.tags))
            normalized_tags = tuple(
                dict.fromkeys(tag for tag in context_tags if str(tag).strip())
            )

            return await self.skill_repository.match_skill(
                step,
                category=category,
                tags=normalized_tags,
                mission_id=mission_id,
            )
        except Exception:
            self.logger.exception("skill matching failed category=%s step='%s'", category, step)
            return None

    def _resolve_mission_id_from_directive(
        self, directive: ActionDirective, plan_out: PlanOut
    ) -> Optional[str]:
        args = directive.args if isinstance(directive.args, dict) else {}
        mission_candidate = args.get("mission_id")
        if isinstance(mission_candidate, str) and mission_candidate.strip():
            return mission_candidate.strip()
        return self._resolve_mission_id_from_plan(plan_out)

    def _resolve_mission_id_from_plan(self, plan_out: PlanOut) -> Optional[str]:
        intent = (plan_out.intent or "").strip()
        if intent and intent in self._MISSION_BINDINGS:
            return self._MISSION_BINDINGS[intent]
        if plan_out.goal_profile and plan_out.goal_profile.category:
            category = plan_out.goal_profile.category
            if category in self._MISSION_BINDINGS:
                return self._MISSION_BINDINGS[category]
        return None

    def _resolve_skill_id(self, directive: ActionDirective, mission_id: str) -> str:
        args = directive.args if isinstance(directive.args, dict) else {}
        skill_id = args.get("skill_id")
        if isinstance(skill_id, str) and skill_id.strip():
            return skill_id.strip()
        return f"minedojo::{mission_id}::{int(time.time())}"

    def _build_context_payload(
        self,
        mission: Optional[MineDojoMission],
        demos: List[MineDojoDemonstration],
        metadata_list: List[MineDojoDemoMetadata],
    ) -> Optional[Dict[str, Any]]:
        if not mission and not demos:
            return None

        payload: Dict[str, Any] = {}
        if mission:
            payload["mission"] = mission.to_prompt_payload()
        if demos:
            payload["demonstrations"] = [
                self._format_demo_for_context(demo, metadata_list[index])
                for index, demo in enumerate(demos)
                if demo and index < len(metadata_list)
            ]
        return payload

    def _format_demo_for_context(
        self, demo: MineDojoDemonstration, metadata: MineDojoDemoMetadata
    ) -> Dict[str, Any]:
        action_types: List[str] = []
        for action in list(demo.actions)[:3]:
            if isinstance(action, dict):
                label = str(action.get("type") or action.get("name") or "unknown")
                action_types.append(label)
        return {
            "demo_id": demo.demo_id,
            "summary": demo.summary,
            "mission_id": metadata.mission_id,
            "tags": list(metadata.tags),
            "action_types": action_types,
            "action_count": len(demo.actions),
        }

    async def _prime_actions_with_demo(
        self, demo: MineDojoDemonstration, metadata: MineDojoDemoMetadata
    ) -> None:
        if not hasattr(self.actions, "play_vpt_actions"):
            return
        if not demo.actions:
            return

        actions_payload = [dict(item) for item in demo.actions if isinstance(item, dict)]
        if not actions_payload:
            return

        metadata_dict = metadata.to_dict()
        try:
            resp = await self.actions.play_vpt_actions(actions_payload, metadata=metadata_dict)
        except Exception:
            self.logger.exception(
                "MineDojo demo preload failed mission=%s demo=%s",
                metadata.mission_id,
                demo.demo_id,
            )
            return
        if resp.get("ok"):
            self.memory.set(
                "minedojo_last_demo_metadata",
                {"mission_id": metadata.mission_id, "demo_id": demo.demo_id, "metadata": metadata_dict},
            )
        else:
            self.logger.warning(
                "MineDojo demo preload command failed mission=%s demo=%s resp=%s",
                metadata.mission_id,
                demo.demo_id,
                resp,
            )

    async def _register_minedojo_demo_skill(
        self,
        mission: Optional[MineDojoMission],
        metadata: MineDojoDemoMetadata,
        demo: MineDojoDemonstration,
    ) -> None:
        # ミッション単位でスキル ID を固定し、NDJSON ログと照合しやすいタグを束ねる。
        skill_id = f"minedojo::{metadata.mission_id}::{metadata.demo_id}"
        tree = await self.skill_repository.get_tree()
        already_exists = skill_id in tree.nodes

        tags: List[str] = [
            "minedojo",
            metadata.mission_id,
            f"mission:{metadata.mission_id}",
            *list(metadata.tags),
        ]
        if mission:
            tags.extend(list(mission.tags))
        normalized_tags = tuple(dict.fromkeys(tag for tag in tags if str(tag).strip()))

        description_parts: List[str] = []
        if mission:
            description_parts.append(mission.objective)
        description_parts.append(f"demo={metadata.summary}")

        keywords: List[str] = []
        if mission:
            keywords.extend([mission.title, mission.objective])
        keywords.append(metadata.summary)

        node = SkillNode(
            identifier=skill_id,
            title=mission.title if mission else f"MineDojo {metadata.mission_id}",
            description=" / ".join(part for part in description_parts if part) or metadata.summary,
            categories=tuple(mission.tags) if mission else (),
            tags=normalized_tags,
            keywords=tuple(keyword for keyword in keywords if keyword),
            examples=(metadata.summary,),
        )
        await self.skill_repository.register_skill(node)

        if not hasattr(self.actions, "register_skill"):
            return
        if already_exists:
            # Mineflayer 側に重複登録してもログが汚れるだけなので回避する。
            return

        try:
            await self.actions.register_skill(  # type: ignore[attr-defined]
                skill_id=skill_id,
                title=node.title,
                description=node.description,
                steps=[demo.summary or metadata.summary],
                tags=list(normalized_tags),
            )
        except Exception:
            self.logger.warning("register_skill dispatch failed for %s", skill_id)
