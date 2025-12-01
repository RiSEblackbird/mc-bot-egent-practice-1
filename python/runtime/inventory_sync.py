# -*- coding: utf-8 -*-
"""所持品スナップショットの取得と要約ロジックを集約するモジュール。

LangGraph ノードやオーケストレータが Mineflayer のインベントリ状態を
再利用できるようにしつつ、依存方向を utils のみに抑えて循環参照を
防ぐ。呼び出し元の実装差し替えを容易にするため、プロトコルで必要な
インターフェースを明示している。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Tuple, Callable

from utils import setup_logger


class InventorySyncSubject(Protocol):
    """インベントリ同期に必要な最小限のインターフェースを表現するプロトコル。"""

    actions: Any
    memory: Any
    logger: Any


class InventorySynchronizer:
    """Mineflayer からインベントリを取得し、要約をメモリへ保存するヘルパー。"""

    def __init__(self, *, summarizer: Callable[[Dict[str, Any]], str] | None = None) -> None:
        self._logger = setup_logger("runtime.inventory_sync")
        self._summarize = summarizer or summarize_inventory_status

    @property
    def summarize(self) -> Callable[[Dict[str, Any]], str]:
        """要約関数を公開し、依存注入先でも共通ロジックを再利用できるようにする。"""

        return self._summarize

    async def refresh(
        self, orchestrator: InventorySyncSubject
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """最新の所持品情報を取得し、メモリへ反映する。

        gather_status("inventory") が未定義の場合はキャッシュ済みデータで
        代替し、完全オフライン環境でも装備推論が続行できるようにしている。
        例外時は詳細な理由を返し、呼び出し元が障壁通知を組み立てやすい
        ようにする。
        """

        if not hasattr(orchestrator.actions, "gather_status"):
            self._logger.info(
                "inventory_sync: gather_status unavailable; falling back to cached snapshot"
            )
            cached_inventory = orchestrator.memory.get("inventory_detail")
            if isinstance(cached_inventory, dict):
                summary = self._summarize(cached_inventory)
                orchestrator.memory.set("inventory", summary)
                orchestrator.memory.set("inventory_detail", cached_inventory)
                return True, cached_inventory, None

            return False, {}, "Mineflayer 側で所持品取得 API が有効化されていません。"

        try:
            resp = await orchestrator.actions.gather_status("inventory")
        except Exception as exc:  # pragma: no cover - Mineflayer 側の例外はログ検証を優先
            orchestrator.logger.exception(
                "inventory refresh failed via gather_status",
                exc_info=exc,
            )
            return False, {}, "所持品の再取得中に予期しない例外が発生しました。"

        if not isinstance(resp, dict) or not resp.get("ok"):
            error_detail = "Mineflayer が所持品を返しませんでした。"
            if isinstance(resp, dict):
                error_detail = str(resp.get("error") or error_detail)
            return False, {}, error_detail

        data = resp.get("data")
        if not isinstance(data, dict):
            data = {}

        summary = self._summarize(data)
        orchestrator.memory.set("inventory", summary)
        orchestrator.memory.set("inventory_detail", data)

        return True, data, None


def summarize_inventory_status(data: Dict[str, Any]) -> str:
    """インベントリ情報を主要要約へ変換する共通ロジック。"""

    if isinstance(data, dict):
        formatted = str(data.get("formatted") or "").strip()
        if formatted:
            return formatted

        items = data.get("items")
        if isinstance(items, list):
            item_count = len(items)
            pickaxes = data.get("pickaxes")
            pickaxe_count = len(pickaxes) if isinstance(pickaxes, list) else 0
            return f"所持品は {item_count} 種類を確認しました（ツルハシ {pickaxe_count} 本）。"

    return "所持品一覧を取得しました。"


__all__ = ["InventorySynchronizer", "InventorySyncSubject", "summarize_inventory_status"]
