from pathlib import Path

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from config import load_agent_config  # type: ignore  # noqa: E402


def test_load_agent_config_returns_defaults() -> None:
    result = load_agent_config({})
    config = result.config

    assert config.ws_url == "ws://127.0.0.1:8765"
    assert config.agent_host == "0.0.0.0"
    assert config.agent_port == 9000
    assert config.default_move_target == (0, 64, 0)
    assert config.minedojo.api_base_url == "https://api.minedojo.org/v1"
    assert config.minedojo.cache_dir == "var/cache/minedojo"
    assert config.minedojo.api_key is None
    assert config.llm_timeout_seconds == 30.0
    assert config.queue_max_size == 20
    assert config.worker_task_timeout_seconds == 300.0


def test_load_agent_config_emits_warning_on_invalid_port() -> None:
    result = load_agent_config({"AGENT_WS_PORT": "invalid"})

    assert result.config.agent_port == 9000
    assert any("ポート値" in warning for warning in result.warnings)


def test_load_agent_config_handles_invalid_move_target() -> None:
    result = load_agent_config({"DEFAULT_MOVE_TARGET": "bad"})

    assert result.config.default_move_target == (0, 64, 0)
    assert any("DEFAULT_MOVE_TARGET" in warning for warning in result.warnings)


def test_load_agent_config_handles_invalid_llm_timeout() -> None:
    result = load_agent_config({"LLM_TIMEOUT_SECONDS": "-10"})

    assert result.config.llm_timeout_seconds == 30.0
    assert any("タイムアウト値" in warning for warning in result.warnings)


def test_load_agent_config_handles_invalid_queue_max_size() -> None:
    result = load_agent_config({"AGENT_QUEUE_MAX_SIZE": "-1"})

    assert result.config.queue_max_size == 20
    assert any("キュー上限" in warning for warning in result.warnings)


def test_load_agent_config_reads_worker_timeout() -> None:
    result = load_agent_config({"WORKER_TASK_TIMEOUT_SECONDS": "45"})

    assert result.config.worker_task_timeout_seconds == 45.0
