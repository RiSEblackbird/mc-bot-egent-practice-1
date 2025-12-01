from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from runtime.minedojo import MineDojoSelfDialogueExecutor  # type: ignore  # noqa: E402
from planner import ReActStep  # type: ignore  # noqa: E402
from services.minedojo_client import (  # type: ignore  # noqa: E402
    MineDojoClient,
    MineDojoDemonstration,
    MineDojoMission,
)
from services.skill_repository import SkillRepository  # type: ignore  # noqa: E402
from utils.langsmith_tracer import ThoughtActionObservationTracer  # type: ignore  # noqa: E402


class StubLangSmithClient:
    """LangSmith SDK 呼び出しを記録するシンプルなテストダブル。"""

    def __init__(self) -> None:
        self.created_runs = []
        self.updated_runs = []

    def create_run(self, **kwargs):  # type: ignore[override]
        self.created_runs.append(kwargs)
        return {"id": kwargs.get("id")}

    def update_run(self, run_id, **kwargs):  # type: ignore[override]
        self.updated_runs.append((run_id, kwargs))
        return {"id": run_id}


class StubActions:
    """Mineflayer 連携の代わりに呼び出し内容を記録するダミー実装。"""

    def __init__(self) -> None:
        self.registered = []
        self.invoked = []

    async def register_skill(self, **kwargs):  # type: ignore[override]
        self.registered.append(kwargs)
        return {"ok": True, "args": kwargs}

    async def invoke_skill(self, skill_id: str, *, context: str | None = None):  # type: ignore[override]
        self.invoked.append({"skill_id": skill_id, "context": context})
        return {"ok": True, "skill_id": skill_id, "context": context}


class StubMineDojoClient(MineDojoClient):
    """fetch 系のみスタブ化した MineDojo クライアント。"""

    def __init__(self) -> None:
        # Base クラスの初期化を避けるためダミー引数を与えない
        pass

    async def fetch_mission(self, mission_id: str) -> MineDojoMission | None:  # type: ignore[override]
        return MineDojoMission(
            mission_id=mission_id,
            title=f"Mission {mission_id}",
            objective="Collect wood and craft tools",
            tags=("gather", "craft"),
            source="stub",
        )

        async def fetch_demonstrations(
            self, mission_id: str, *, limit: int = 1
        ) -> list[MineDojoDemonstration]:  # type: ignore[override]
            return [
                MineDojoDemonstration(
                    mission_id=mission_id,
                    demo_id=f"demo-{mission_id}",
                    summary="Break tree blocks and craft planks",
                    actions=(),
                    tags=("gather",),
                    source="stub",
                )
                for _ in range(limit)
            ]


def test_thought_action_observation_tracer_records_runs() -> None:
    client = StubLangSmithClient()
    tracer = ThoughtActionObservationTracer(
        api_url="http://localhost",
        api_key="dummy",
        project="test-project",
        default_tags=("self-dialogue",),
        enabled=True,
        client=client,
    )

    run_id = tracer.start_run("demo", metadata={"mission": "abc"})
    tracer.record_step(run_id, step=ReActStep(thought="考える", action="move", observation="ok"), step_index=0)
    tracer.complete_run(run_id, outputs={"result": "ok"})

    assert client.created_runs, "LangSmith クライアントへ create_run が送信されていません"
    assert client.created_runs[0]["name"] == "demo"
    assert client.updated_runs, "LangSmith クライアントへ update_run が送信されていません"
    assert client.updated_runs[0][0] == run_id


@pytest.mark.anyio
async def test_self_dialogue_executor_updates_skill_and_invokes(tmp_path: Path) -> None:
    repo_path = tmp_path / "skills.json"
    repository = SkillRepository(str(repo_path))
    tracer = ThoughtActionObservationTracer(
        api_url=None,
        api_key=None,
        project=None,
        enabled=False,
    )
    actions = StubActions()
    client = StubMineDojoClient()
    executor = MineDojoSelfDialogueExecutor(
        actions=actions,
        client=client,
        skill_repository=repository,
        tracer=tracer,
        env_params={"sim_env": "creative", "sim_seed": 99, "sim_max_steps": 10},
    )

    react_trace = [ReActStep(thought="木を探す", action="move to tree", observation="見つかった")]
    await executor.run_self_dialogue(
        "mission-wood",
        react_trace,
        skill_id="skill-wood-path",
        title="木材採集ルート",
        success=True,
    )

    tree = await repository.get_tree()
    node = tree.nodes.get("skill-wood-path")
    assert node is not None
    assert node.success_count == 1
    assert actions.registered, "registerSkill が送信されていません"
    assert actions.invoked, "invokeSkill が送信されていません"
