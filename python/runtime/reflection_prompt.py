# -*- coding: utf-8 -*-
"""再計画時に使用する Reflexion プロンプトの生成ユーティリティ。

このモジュールは LangGraph ノードやオーケストレータから参照される
汎用的なプロンプト構築ヘルパーを集約する。循環参照を避けるため、
依存先は共通ユーティリティと標準ライブラリのみに限定している。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def build_reflection_prompt(
    failed_step: str,
    failure_reason: str,
    *,
    detection_reports: Sequence[Dict[str, Any]] = (),
    action_backlog: Sequence[Dict[str, Any]] = (),
    previous_reflections: Sequence[Dict[str, Any]] = (),
) -> str:
    """再計画時に渡す Reflexion プロンプトを生成する補助関数。

    LangGraph の再計画ノードだけでなく、Mineflayer からの障害検知や
    既存の backlog もまとめて提示し、失敗理由を踏まえた改善提案を
    引き出すためのテンプレートを返す。
    """

    lines: List[str] = [
        "以下の障壁を踏まえた再計画を提案してください。",
        f"失敗したステップ: {failed_step}",
        f"失敗理由: {failure_reason}",
    ]

    if detection_reports:
        lines.append("関連ステータス報告:")
        for report in detection_reports:
            summary = str(report.get("summary") or report.get("category") or "").strip()
            if summary:
                lines.append(f"- {summary}")

    if action_backlog:
        lines.append("未消化のアクション候補:")
        for item in action_backlog:
            label = str(
                item.get("label")
                or item.get("step")
                or item.get("category")
                or item.get("summary")
                or "未分類のアクション"
            )
            if label:
                lines.append(f"- {label}")

    if previous_reflections:
        lines.append("過去の反省点・改善案:")
        for entry in previous_reflections:
            improvement = str(entry.get("improvement") or "改善案なし").strip()
            retry_result = str(entry.get("retry_result") or "結果未記録").strip()
            lines.append(f"- {improvement} / 再試行結果: {retry_result}")

    lines.append(
        "同じ失敗を繰り返さないよう、具体的な改善ポイントを含む計画ステップを提示してください。"
    )
    return "\n".join(lines)


__all__ = ["build_reflection_prompt"]
