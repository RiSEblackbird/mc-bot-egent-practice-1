"""initialize_agent_runtime の注入ロジックに関するテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent import AgentOrchestrator  # type: ignore  # noqa: E402
from agent_settings import AgentRuntimeSettings  # type: ignore  # noqa: E402
from config import AgentConfig, LangSmithConfig, MineDojoConfig  # type: ignore  # noqa: E402
from memory import Memory  # type: ignore  # noqa: E402
from runtime.inventory_sync import InventorySynchronizer  # type: ignore  # noqa: E402


@dataclass
class StubActions:
    """AgentOrchestrator 初期化で必要となる最小限のアクションスタブ。"""

    say_calls: list[str]

    def __init__(self) -> None:
        self.say_calls = []

    async def say(self, message: str) -> None:
        self.say_calls.append(message)

    async def set_role(self, role_id: str, reason: str | None = None) -> dict:
        return {"ok": True, "data": {"label": role_id, "reason": reason}}

    async def gather_status(self, kind: str) -> dict:
        # status_service 側の呼び出しをモックしやすくするため、固定レスポンスを返す。
        return {"ok": True, "data": {"kind": kind}}


class SkillRepositoryStub:
    """永続化を伴わないテスト用のスキルリポジトリスタブ。"""

    def __init__(self) -> None:
        self.recorded: list[str] = []

    async def record_usage(self, skill_id: str, *, success: bool) -> None:  # pragma: no cover - 呼び出しはテスト外
        self.recorded.append(skill_id)


class MineDojoClientStub:
    """MineDojoClient の差し替え用にフィールドのみを持つ簡易スタブ。"""

    def __init__(self) -> None:
        self.used = False


@pytest.fixture
def base_config(tmp_path: Path) -> AgentConfig:
    """テスト専用の AgentConfig を生成するヘルパー。"""

    langsmith = LangSmithConfig(
        api_url="https://example.invalid/api",
        api_key="dummy",
        project="test-project",
        enabled=False,
        tags=("test",),
    )
    minedojo = MineDojoConfig(
        api_base_url="https://example.invalid/minedojo",
        api_key="dummy",
        dataset_dir=str(tmp_path / "dataset"),
        cache_dir=str(tmp_path / "cache"),
        request_timeout=1.0,
        sim_env="creative",
        sim_seed=99,
        sim_max_steps=12,
    )
    return AgentConfig(
        ws_url="ws://localhost:9999",
        agent_host="0.0.0.0",
        agent_port=12345,
        default_move_target=(1, 2, 3),
        default_move_target_raw="1,2,3",
        skill_library_path=str(tmp_path / "skills.json"),
        minedojo=minedojo,
        langsmith=langsmith,
        llm_timeout_seconds=5.0,
        queue_max_size=5,
        worker_task_timeout_seconds=60.0,
    )


def test_initialize_agent_runtime_applies_custom_settings(base_config: AgentConfig) -> None:
    """カスタム設定が PlanRuntimeContext と依存セットに伝搬することを確認する。"""

    actions = StubActions()
    memory = Memory()
    runtime_settings = AgentRuntimeSettings(
        config=base_config,
        status_refresh_timeout_seconds=9.5,
        status_refresh_retry=5,
        status_refresh_backoff_seconds=1.5,
        block_eval_radius=2,
        block_eval_timeout_seconds=4.5,
        block_eval_height_delta=1,
        structured_event_history_limit=42,
        perception_history_limit=24,
        low_food_threshold=3,
    )

    orchestrator = AgentOrchestrator(
        actions,
        memory,
        config=base_config,
        runtime_settings=runtime_settings,
    )

    assert orchestrator.config is base_config
    assert orchestrator.settings is runtime_settings
    assert orchestrator.default_move_target == (1, 2, 3)
    assert orchestrator._plan_runtime.low_food_threshold == 3
    assert orchestrator._plan_runtime.structured_event_history_limit == 42
    assert orchestrator._plan_runtime.perception_history_limit == 24
    assert orchestrator._dependencies.runtime_settings is runtime_settings


def test_initialize_agent_runtime_respects_dependency_overrides(
    base_config: AgentConfig,
) -> None:
    """差し替えた依存がファクトリ内部で再生成されないことを確認する。"""

    actions = StubActions()
    memory = Memory()
    skill_repo = SkillRepositoryStub()
    inv_sync = InventorySynchronizer(summarizer=lambda _: "custom")
    custom_client = MineDojoClientStub()

    orchestrator = AgentOrchestrator(
        actions,
        memory,
        config=base_config,
        skill_repository=skill_repo,  # type: ignore[arg-type]
        inventory_sync=inv_sync,
        minedojo_client=custom_client,  # type: ignore[arg-type]
    )

    assert orchestrator.skill_repository is skill_repo
    assert orchestrator.inventory_sync is inv_sync
    assert orchestrator.minedojo_client is custom_client
    assert orchestrator._dependencies.skill_repository is skill_repo
    assert orchestrator._dependencies.inventory_sync is inv_sync
