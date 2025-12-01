# -*- coding: utf-8 -*-
"""MineDojo 連携や自己対話処理のブートストラップモジュール。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from config import AgentConfig, load_agent_config
from services.minedojo_client import (
    MineDojoClient,
    MineDojoDemonstration,
    MineDojoMission,
)
from services.skill_repository import SkillRepository
from actions import Actions
from bridge_ws import BotBridge
from utils import ThoughtActionObservationTracer, setup_logger
from planner import ReActStep


class MineDojoSelfDialogueExecutor:
    """MineDojo 環境での自己対話ループをまとめる軽量エグゼキューター。"""

    def __init__(
        self,
        *,
        actions: Any,
        client: MineDojoClient,
        skill_repository: SkillRepository,
        tracer: ThoughtActionObservationTracer,
        env_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        # MineDojo 連携とスキル永続化を 1 箇所で制御し、自己対話の成果を Mineflayer へ共有する。
        self._actions = actions
        self._client = client
        self._skill_repository = skill_repository
        self._tracer = tracer
        self._env_params = env_params or {}
        self._logger = setup_logger("agent.self_dialogue")

    async def run_self_dialogue(
        self,
        mission_id: str,
        react_trace: Sequence[ReActStep],
        *,
        skill_id: str,
        title: str,
        success: bool,
    ) -> None:
        """ReAct ステップを LangSmith へ送信しつつスキル登録と使用実績を更新する。"""

        mission = await self._client.fetch_mission(mission_id)
        demonstrations = await self._client.fetch_demonstrations(mission_id, limit=1)
        run_id = self._tracer.start_run(
            "minedojo-self-dialogue",
            metadata={
                "mission_id": mission_id,
                "sim_env": self._env_params.get("sim_env"),
                "sim_seed": self._env_params.get("sim_seed"),
                "sim_max_steps": self._env_params.get("sim_max_steps"),
            },
        )

        for index, step in enumerate(react_trace):
            self._tracer.record_step(
                run_id,
                step=step,
                step_index=index,
                metadata={"mission": mission_id},
            )

        node = self._build_skill_node(
            mission_id,
            react_trace,
            skill_id=skill_id,
            title=title,
            mission=mission,
            demonstrations=demonstrations,
        )
        await self._skill_repository.register_skill(node)
        await self._client.record_mission_outcome(
            mission_id,
            outcome={
                "mission_id": mission_id,
                "title": title,
                "success": success,
                "skill_id": skill_id,
            },
        )
        self._tracer.end_run(run_id, metadata={"skill_id": skill_id})
        self._logger.info(
            "registered MineDojo skill_id=%s mission_id=%s", skill_id, mission_id
        )

    def _build_skill_node(
        self,
        mission_id: str,
        react_trace: Sequence[ReActStep],
        *,
        skill_id: str,
        title: str,
        mission: Optional[MineDojoMission],
        demonstrations: Sequence[MineDojoDemonstration],
    ) -> SkillRepository.Node:
        """ReAct ステップとミッション情報から Skill ノードを組み立てる。"""

        tags = [f"mission:{mission_id}", "minedojo"]
        if mission and mission.tags:
            tags.extend(mission.tags)
        steps = [
            {
                "thought": step.thought,
                "action": step.action,
                "observation": step.observation,
            }
            for step in react_trace
        ]
        return SkillRepository.Node(
            id=skill_id,
            label=title,
            description=mission.summary if mission else "",
            tags=tags,
            script=react_trace,
            steps=steps,
            metadata={
                "mission": mission.to_dict() if mission else None,
                "demonstrations": [demo.to_dict() for demo in demonstrations],
            },
        )


async def run_minedojo_self_dialogue(
    mission_id: str,
    react_trace: Sequence[ReActStep],
    *,
    skill_id: str,
    title: str,
    success: bool = True,
    config: AgentConfig | None = None,
) -> None:
    """MineDojo 環境向け自己対話を単体実行する簡易エントリポイント。"""

    cfg = config or load_agent_config().config
    bridge = BotBridge(cfg.ws_url)
    actions = Actions(bridge)
    seed_path = Path(__file__).resolve().parent.parent / "skills" / "seed_library.json"
    skill_repo = SkillRepository(
        cfg.skill_library_path,
        seed_path=str(seed_path),
    )
    minedojo_client = MineDojoClient(cfg.minedojo)
    tracer = ThoughtActionObservationTracer(
        api_url=cfg.langsmith.api_url,
        api_key=cfg.langsmith.api_key,
        project=cfg.langsmith.project,
        default_tags=cfg.langsmith.tags,
        enabled=cfg.langsmith.enabled,
    )
    executor = MineDojoSelfDialogueExecutor(
        actions=actions,
        client=minedojo_client,
        skill_repository=skill_repo,
        tracer=tracer,
        env_params={
            "sim_env": cfg.minedojo.sim_env,
            "sim_seed": cfg.minedojo.sim_seed,
            "sim_max_steps": cfg.minedojo.sim_max_steps,
        },
    )
    await executor.run_self_dialogue(
        mission_id,
        react_trace,
        skill_id=skill_id,
        title=title,
        success=success,
    )
    await minedojo_client.aclose()
