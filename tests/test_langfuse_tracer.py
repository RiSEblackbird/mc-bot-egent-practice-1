from pathlib import Path

import pytest

from runtime.minedojo import MineDojoSelfDialogueExecutor  # type: ignore  # noqa: E402
from planner import ReActStep  # type: ignore  # noqa: E402
from services.minedojo_client import (  # type: ignore  # noqa: E402
    MineDojoClient,
    MineDojoDemonstration,
    MineDojoMission,
)
from utils.langfuse_tracer import ThoughtActionObservationTracer  # type: ignore  # noqa: E402

class StubObservation:
    """Langfuse Observation の update/end を記録するテストダブル。"""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.updates = []
        self.ended = False

    def update(self, **kwargs):  # type: ignore[override]
        self.updates.append(kwargs)
        return self

    def end(self, **kwargs):  # type: ignore[override]
        self.ended = True
        return self

class StubLangfuseClient:
    """Langfuse SDK 呼び出しを記録するシンプルなテストダブル。"""

    def __init__(self) -> None:
        self.created_observations = []
        self.flushed = False

    def start_observation(self, **kwargs):  # type: ignore[override]
        observation = StubObservation(kwargs)
        self.created_observations.append(observation)
        return observation

    def flush(self) -> None:
        self.flushed = True

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

    async def record_mission_outcome(self, mission_id: str, *, outcome):  # type: ignore[override]
        return {"mission_id": mission_id, "outcome": outcome}

def test_thought_action_observation_tracer_records_runs() -> None:
    client = StubLangfuseClient()
    tracer = ThoughtActionObservationTracer(
        host="http://localhost",
        public_key="pk_test_dummy",
        secret_key="sk_test_dummy",
        default_tags=("self-dialogue",),
        enabled=True,
        client=client,
    )

    run_id = tracer.start_run("demo", metadata={"mission": "abc"})
    tracer.record_step(run_id, step=ReActStep(thought="考える", action="move", observation="ok"), step_index=0)
    tracer.complete_run(run_id, outputs={"result": "ok"})

    assert client.created_observations, "Langfuse クライアントへ observation が送信されていません"
    assert client.created_observations[0].payload["name"] == "demo"
    assert client.created_observations[0].updates, "Langfuse クライアントへ update が送信されていません"
    assert client.flushed is True

@pytest.mark.anyio
async def test_self_dialogue_executor_updates_skill_and_invokes(tmp_path: Path) -> None:
    class StubRepository:
        def __init__(self) -> None:
            self.nodes = {}

        async def register_skill(self, node):
            self.nodes[node.id] = node

        async def get_tree(self):
            return type("Tree", (), {"nodes": self.nodes})()

    repository = StubRepository()
    tracer = ThoughtActionObservationTracer(
        host=None,
        public_key=None,
        secret_key=None,
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
    # SkillRepository.Node 依存を切り離し、self dialogue フローの副作用に集中して検証する。
    executor._build_skill_node = lambda *args, **kwargs: type("Node", (), {"id": "skill-wood-path"})()  # type: ignore[assignment]

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
