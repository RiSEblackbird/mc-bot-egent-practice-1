# -*- coding: utf-8 -*-
"""チャット/スキル系のルーティング責務をまとめたファサード。"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from orchestrator.action_analyzer import ActionAnalyzer
from orchestrator.skill_detection import SkillDetectionCoordinator
from chat_pipeline import ChatPipeline
from skills import SkillMatch


class TaskRouter:
    """チャット分類やスキル探索を集約し、依存の結合度を下げる調停クラス。

    ChatPipeline と SkillDetectionCoordinator を横断的に呼び出す機能を 1 か所に
    まとめ、AgentOrchestrator からは単一のファサード経由で分類・探索・バック
    ログ整理を依頼できるようにする。新人メンバーがトレースしやすいよう、
    メソッドごとの責務をドキュメント化したうえで、呼び出し元は TaskRouter
    以外の内部構造を意識せずに済む形を維持する。
    """

    _DETECTION_LABELS = {
        "player_position": "現在位置の確認",
        "inventory_status": "所持品の確認",
        "general_status": "状態の共有",
    }

    def __init__(
        self,
        *,
        action_analyzer: ActionAnalyzer,
        chat_pipeline: ChatPipeline,
        skill_detection: SkillDetectionCoordinator,
        minedojo_handler: Any,
        report_execution_barrier: Callable[[str, str], Awaitable[None]],
        logger: logging.Logger,
    ) -> None:
        self._action_analyzer = action_analyzer
        self._chat_pipeline = chat_pipeline
        self._skill_detection = skill_detection
        self._minedojo_handler = minedojo_handler
        self._report_execution_barrier = report_execution_barrier
        self.logger = logger

    # --- 分類/推論ロジック -------------------------------------------------
    def classify_detection_task(self, text: str) -> Optional[str]:
        return self._action_analyzer.classify_detection_task(text)

    def classify_action_task(self, text: str) -> Optional[str]:
        return self._action_analyzer.classify_action_task(text)

    def infer_equip_arguments(self, text: str) -> Optional[Dict[str, str]]:
        return self._action_analyzer.infer_equip_arguments(text)

    def infer_mining_request(self, text: str) -> Dict[str, Any]:
        return self._action_analyzer.infer_mining_request(text)

    # --- 検出タスク ---------------------------------------------------------
    async def perform_detection_task(self, category: str) -> Optional[Dict[str, Any]]:
        """ステータス検出タスクを委譲し、失敗時は障壁として即時共有する。"""

        result, error_detail = await self._skill_detection.perform_detection_task(
            category
        )
        if result:
            return result

        if error_detail:
            label = self._DETECTION_LABELS.get(category, "ステータス確認")
            await self._report_execution_barrier(
                label, f"ステータス取得に失敗しました（{error_detail}）。"
            )
        else:
            self.logger.warning(
                "unknown detection category encountered category=%s", category
            )
        return None

    def summarize_position_status(self, data: Dict[str, Any]) -> str:
        return self._skill_detection.summarize_position_status(data)

    def summarize_general_status(self, data: Dict[str, Any]) -> str:
        return self._skill_detection.summarize_general_status(data)

    # --- MineDojo スキル探索 ------------------------------------------------
    async def find_skill_for_step(
        self, category: str, step: str
    ) -> Optional[SkillMatch]:
        return await self._skill_detection.find_skill_for_step(
            self._minedojo_handler, category, step
        )

    async def execute_skill_match(
        self, match: SkillMatch, step: str
    ) -> Tuple[bool, Optional[str]]:
        return await self._skill_detection.execute_skill_match(match, step)

    async def begin_skill_exploration(
        self, match: SkillMatch, step: str
    ) -> Tuple[bool, Optional[str]]:
        return await self._skill_detection.begin_skill_exploration(match, step)

    # --- 行動タスク/バックログ処理 -----------------------------------------
    async def handle_action_task(
        self,
        category: str,
        step: str,
        *,
        last_target_coords: Optional[Tuple[int, int, int]],
        backlog: List[Dict[str, str]],
        explicit_coords: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int, int]], Optional[str]]:
        return await self._chat_pipeline.handle_action_task(
            category,
            step,
            last_target_coords=last_target_coords,
            backlog=backlog,
            explicit_coords=explicit_coords,
        )

    def select_pickaxe_for_targets(
        self, ore_names: Iterable[str]
    ) -> Optional[Dict[str, Any]]:
        return self._chat_pipeline.select_pickaxe_for_targets(ore_names)

    async def handle_action_backlog(
        self, backlog: Iterable[Dict[str, str]], *, already_responded: bool
    ) -> None:
        await self._chat_pipeline.handle_action_backlog(
            backlog, already_responded=already_responded
        )

    async def handle_detection_reports(
        self, reports: Iterable[Dict[str, Any]], *, already_responded: bool
    ) -> None:
        await self._chat_pipeline.handle_detection_reports(
            reports, already_responded=already_responded
        )


__all__ = ["TaskRouter"]
