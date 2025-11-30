from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_minedojo_demo_registration_and_reuse(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")

    from agent import AgentOrchestrator  # type: ignore  # noqa: E402
    from config import AgentConfig, LangSmithConfig, MineDojoConfig  # type: ignore  # noqa: E402
    from memory import Memory  # type: ignore  # noqa: E402
    from services.minedojo_client import (  # type: ignore  # noqa: E402
        MineDojoDemonstration,
        MineDojoMission,
    )
    from services.skill_repository import SkillRepository  # type: ignore  # noqa: E402

    class StubActions:
        """MineDojo デモ由来のスキル登録・再生を観測するテスト用スタブ。"""

        def __init__(self) -> None:
            self.played: List[Dict[str, Any]] = []
            self.registered: List[Dict[str, Any]] = []
            self.invoked: List[Dict[str, Any]] = []

        async def play_vpt_actions(
            self, actions: List[Dict[str, Any]], *, metadata: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            record = {"actions": actions, "metadata": metadata or {}}
            self.played.append(record)
            return {"ok": True, "data": record}

        async def register_skill(
            self,
            *,
            skill_id: str,
            title: str,
            description: str,
            steps: List[str],
            tags: Optional[List[str]] = None,
        ) -> Dict[str, Any]:
            self.registered.append(
                {
                    "skill_id": skill_id,
                    "title": title,
                    "description": description,
                    "steps": steps,
                    "tags": tags or [],
                }
            )
            return {"ok": True}

        async def invoke_skill(
            self, skill_id: str, *, context: Optional[str] = None
        ) -> Dict[str, Any]:
            self.invoked.append({"skill_id": skill_id, "context": context})
            return {"ok": True}

    class StubMineDojoClient:
        """固定のミッション/デモを返すスタブクライアント。"""

        async def fetch_mission(self, mission_id: str) -> MineDojoMission:
            return MineDojoMission(
                mission_id=mission_id,
                title="ダイヤモンド採掘トレーニング",
                objective="安全にダイヤモンドを掘り当てる",
                tags=("mining", "safety"),
                source="test",
                raw={"difficulty": "medium"},
            )

        async def fetch_demonstrations(
            self, mission_id: str, *, limit: int = 1
        ) -> List[MineDojoDemonstration]:
            return [
                MineDojoDemonstration(
                    mission_id=mission_id,
                    demo_id="demo-1",
                    summary="地下渓谷での採掘手順",
                    actions=[{"type": "moveTo", "args": {"x": 1, "y": 64, "z": -3}}],
                    tags=("mining",),
                    source="test",
                    raw={"duration": 12.3, "success": True},
                )
            ]

    repo_path = tmp_path / "skills.json"
    cache_dir = tmp_path / "cache"
    config = AgentConfig(
        ws_url="ws://127.0.0.1:8765",
        agent_host="0.0.0.0",
        agent_port=9000,
        default_move_target=(0, 64, 0),
        default_move_target_raw="0,64,0",
        skill_library_path=str(repo_path),
        minedojo=MineDojoConfig(
            api_base_url="https://example.org",
            api_key=None,
            dataset_dir=None,
            cache_dir=str(cache_dir),
            request_timeout=5.0,
            sim_env="",
            sim_seed=0,
            sim_max_steps=0,
        ),
        langsmith=LangSmithConfig(
            api_url="",
            api_key=None,
            project=None,
            enabled=False,
            tags=(),
        ),
        llm_timeout_seconds=30.0,
        queue_max_size=10,
        worker_task_timeout_seconds=120.0,
    )

    actions = StubActions()
    memory = Memory()
    skill_repository = SkillRepository(str(repo_path))
    orchestrator = AgentOrchestrator(
        actions,
        memory,
        skill_repository=skill_repository,
        config=config,
        minedojo_client=StubMineDojoClient(),
    )
    orchestrator._action_graph.run = AsyncMock(return_value=(True, None, None))  # type: ignore[assignment]

    handled, _, failure = await orchestrator._handle_action_task(
        "mine",
        "ダイヤのミッションを開始",
        last_target_coords=None,
        backlog=[],
    )

    assert handled is True
    assert failure is None
    assert actions.registered, "MineDojo デモが Actions.registerSkill へ伝搬されていません"

    tree = await skill_repository.get_tree()
    assert tree.nodes, "SkillRepository に MineDojo デモが登録されていません"
    registered_skill_id = actions.registered[0]["skill_id"]
    assert registered_skill_id in tree.nodes

    # MineDojo 文脈タグを使って曖昧なリクエストでもスキルを再利用できることを確認する。
    match = await orchestrator._find_skill_for_step("mine", "ミッションをもう一度再生して")
    assert match is not None
    await orchestrator._execute_skill_match(match, "ミッション再利用")

    assert actions.invoked and actions.invoked[0]["skill_id"] == registered_skill_id
