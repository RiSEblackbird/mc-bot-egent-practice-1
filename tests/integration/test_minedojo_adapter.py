from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_minedojo_context_is_injected_into_actions_and_memory() -> None:
    pytest.importorskip("langgraph")

    from agent import AgentOrchestrator  # type: ignore  # noqa: E402
    from config import AgentConfig, MineDojoConfig  # type: ignore  # noqa: E402
    from memory import Memory  # type: ignore  # noqa: E402
    from services.minedojo_client import (  # type: ignore  # noqa: E402
        MineDojoDemonstration,
        MineDojoMission,
    )

    class StubActions:
        """MineDojo デモの送信結果を記録する最小限のスタブ。"""

        def __init__(self) -> None:
            self.played: List[Dict[str, Any]] = []

        async def play_vpt_actions(
            self, actions: List[Dict[str, Any]], *, metadata: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            record = {"actions": actions, "metadata": metadata or {}}
            self.played.append(record)
            return {"ok": True, "data": record}

        async def say(self, text: str) -> Dict[str, Any]:  # pragma: no cover - helper for safety
            return {"ok": True, "data": {"text": text}}

    class StubMineDojoClient:
        """固定レスポンスでミッションとデモを返すテスト用クライアント。"""

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
                    demo_id="demo-1",
                    summary="地下渓谷での採掘手順",
                    actions=[{"type": "moveTo", "args": {"x": 1, "y": 64, "z": -3}}],
                    source="test",
                    raw={"duration": 12.3, "success": True},
                )
            ]

    class StubSkillRepository:
        """スキル検索を無効化する簡易スタブ。"""

        async def match_skill(self, text: str, *, category: Optional[str] = None) -> None:
            return None

        async def record_usage(self, skill_id: str, *, success: bool) -> None:  # pragma: no cover - not used
            return None

    config = AgentConfig(
        ws_url="ws://127.0.0.1:8765",
        agent_host="0.0.0.0",
        agent_port=9000,
        default_move_target=(0, 64, 0),
        default_move_target_raw="0,64,0",
        skill_library_path="/tmp/skills.json",
        minedojo=MineDojoConfig(
            api_base_url="https://example.org",
            api_key=None,
            dataset_dir=None,
            cache_dir="/tmp/minedojo-cache",
            request_timeout=5.0,
        ),
        llm_timeout_seconds=30.0,
        queue_max_size=10,
        worker_task_timeout_seconds=120.0,
    )
    actions = StubActions()
    memory = Memory()
    orchestrator = AgentOrchestrator(
        actions,
        memory,
        skill_repository=StubSkillRepository(),
        config=config,
        minedojo_client=StubMineDojoClient(),
    )
    orchestrator._action_graph.run = AsyncMock(return_value=(True, None, None))  # type: ignore[assignment]

    handled, _, failure = await orchestrator._handle_action_task(
        "mine",
        "ダイヤモンドを採掘する",
        last_target_coords=None,
        backlog=[],
    )

    assert handled is True
    assert failure is None

    assert orchestrator.memory.get("minedojo_context") == {
        "mission": {
            "mission_id": "obtain_diamond",
            "title": "ダイヤモンド採掘トレーニング",
            "objective": "安全にダイヤモンドを掘り当てる",
            "tags": ["mining", "safety"],
            "source": "test",
        },
        "demonstrations": [
            {
                "demo_id": "demo-1",
                "summary": "地下渓谷での採掘手順",
                "action_types": ["moveTo"],
                "action_count": 1,
            }
        ],
    }

    assert len(actions.played) == 1
    assert actions.played[0]["metadata"]["mission_id"] == "obtain_diamond"
    assert actions.played[0]["metadata"]["demo_id"] == "demo-1"
    assert actions.played[0]["actions"] == [{"type": "moveTo", "args": {"x": 1, "y": 64, "z": -3}}]
