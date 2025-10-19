# -*- coding: utf-8 -*-
"""Reflexion メモリの永続化とサマリ生成ロジックのユニットテスト。"""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from memory import Memory  # type: ignore  # noqa: E402
from services.reflection_store import ReflectionStore  # type: ignore  # noqa: E402


def test_reflection_memory_persistence(tmp_path: Path) -> None:
    """反省ログの保存と再読み込みが行えることを確認する。"""

    store_path = tmp_path / "reflections.json"
    memory = Memory(reflection_store=ReflectionStore(path=store_path))

    signature = memory.derive_task_signature("石炭を掘る 作業手順")
    prompt = "失敗理由を踏まえ、事前にツール耐久を確認してから採掘を再開する"
    entry = memory.begin_reflection(
        task_signature=signature,
        failed_step="石炭を掘る",
        failure_reason="ツルハシの耐久不足で Mineflayer が拒否",
        improvement=prompt,
        metadata={"remaining_steps": ["代替のツールを確保する"]},
    )

    assert store_path.exists(), "反省ログの保存先ファイルが生成される"
    assert entry.retry_result == "pending"
    assert memory.get_active_reflection_prompt() == prompt

    memory.finalize_pending_reflection(outcome="success", detail="再計画で復旧")
    context = memory.build_reflection_context(limit=1)
    assert context, "反省ログのサマリが 1 件以上返る"
    assert context[0]["retry_result"].startswith("success"), "再試行結果が成功で記録される"

    reloaded = Memory(reflection_store=ReflectionStore(path=store_path))
    summary = reloaded.build_reflection_context(limit=1)
    assert summary[0]["failed_step"] == "石炭を掘る"
    assert summary[0]["retry_result"] == context[0]["retry_result"]

    prompt_logs = reloaded.export_reflections_for_prompt(task_signature=signature, limit=1)
    assert prompt_logs[0]["improvement"] == prompt


def test_reflection_signature_normalization(tmp_path: Path) -> None:
    """ステップ文字列の空白揺れが署名生成で吸収されることを確認する。"""

    store_path = tmp_path / "sig.json"
    memory = Memory(reflection_store=ReflectionStore(path=store_path))

    signature_a = memory.derive_task_signature("  採掘   を 行う  ")
    signature_b = memory.derive_task_signature("採掘 を 行う")

    assert signature_a == signature_b == "採掘 を 行う"
