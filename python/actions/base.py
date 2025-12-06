# -*- coding: utf-8 -*-
"""Actions ファサードで共有するディスパッチ基底クラス。"""

from __future__ import annotations

import itertools
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from bridge_ws import BotBridge
from utils import log_structured_event, setup_logger

from .errors import ActionValidationError


class ActionDispatcher:
    """BotBridge との送受信を一元管理する基底クラス。

    各アクションモジュールはこのクラスのインスタンスを共有し、
    コマンド番号の採番や directive メタデータの付与などの横断的処理を
    `_dispatch` を経由して実行する。
    """

    def __init__(
        self,
        bridge: BotBridge,
        *,
        on_bridge_retry: Optional[Callable[[int, str], Awaitable[None]]] = None,
        on_bridge_give_up: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        # Bridge インスタンスを保持し、全アクションで共有する。
        self.bridge = bridge
        self.logger = setup_logger("actions")
        self._command_seq = itertools.count(1)
        self._on_bridge_retry = on_bridge_retry
        self._on_bridge_give_up = on_bridge_give_up
        self._current_directive_meta: Optional[Dict[str, Any]] = None

    def begin_directive_scope(self, meta: Dict[str, Any]) -> None:
        """直後のコマンドへ directive メタデータを付与する。"""

        self._current_directive_meta = dict(meta)

    def end_directive_scope(self) -> None:
        """directive メタデータのスコープを終了する。"""

        self._current_directive_meta = None

    async def _dispatch(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """共通の送信処理: 付番、送信時間、レスポンスを詳細に記録する。"""

        command_id = next(self._command_seq)
        started_at = time.perf_counter()
        wire_payload = dict(payload)
        if self._current_directive_meta:
            wire_payload["meta"] = dict(self._current_directive_meta)
        log_structured_event(
            self.logger,
            "dispatch prepared",
            event_level="progress",
            context={"command": command, "command_id": command_id, "payload": wire_payload},
        )
        try:
            resp = await self.bridge.send(
                wire_payload,
                on_retry=self._on_bridge_retry,
                on_give_up=self._on_bridge_give_up,
            )
        except Exception as error:  # noqa: BLE001 - 送信失敗はそのまま上位へ伝搬させる
            log_structured_event(
                self.logger,
                "dispatch failed",
                level=logging.ERROR,
                event_level="fault",
                context={"command": command, "command_id": command_id, "payload": wire_payload},
                exc_info=error,
            )
            raise

        elapsed = time.perf_counter() - started_at
        event_level = "success" if resp.get("ok") else "fault"
        log_structured_event(
            self.logger,
            "dispatch completed",
            level=logging.INFO if resp.get("ok") else logging.ERROR,
            event_level=event_level,
            context={
                "command": command,
                "command_id": command_id,
                "payload": wire_payload,
                "response": resp,
                "duration_sec": round(elapsed, 3),
            },
        )
        return resp

    def _normalize_command_payload(self, payload: Dict[str, Any], *, label: str) -> Dict[str, Any]:
        """汎用コマンドペイロードの妥当性検証を行うヘルパー。"""

        if not isinstance(payload, dict):
            raise ActionValidationError(f"{label} はオブジェクトで指定してください")
        command_type = payload.get("type")
        if not isinstance(command_type, str) or not command_type.strip():
            raise ActionValidationError(f"{label}.type は 1 文字以上の文字列で指定してください")
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            raise ActionValidationError(f"{label}.args はオブジェクトで指定してください")
        normalized: Dict[str, Any] = {
            "type": command_type.strip(),
            "args": dict(args),
        }
        return normalized

    def _normalize_vpt_actions(
        self,
        actions: Optional[list[Dict[str, Any]]],
    ) -> list[Dict[str, Any]]:
        """VPT 指示のリスト形式を安全に正規化する。"""

        if actions is None:
            return []
        if not isinstance(actions, list):
            raise ActionValidationError("vpt_actions は配列で指定してください")
        normalized: list[Dict[str, Any]] = []
        for index, item in enumerate(actions):
            if not isinstance(item, dict):
                raise ActionValidationError(f"vpt_actions[{index}] はオブジェクトで指定してください")
            normalized.append(item)
        return normalized


class ActionModule:
    """各種アクションカテゴリが継承する共通モジュール基底クラス。"""

    def __init__(self, dispatcher: ActionDispatcher) -> None:
        # 送信ロジックを一本化するため、ActionDispatcher インスタンスを保持する。
        self._dispatcher = dispatcher

    @property
    def logger(self) -> logging.Logger:
        """共通ロガーへのアクセスを提供する。"""

        return self._dispatcher.logger

    async def _dispatch(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """ActionDispatcher 経由でコマンドを送信するヘルパー。"""

        return await self._dispatcher._dispatch(command, payload)

    def _normalize_command_payload(self, payload: Dict[str, Any], *, label: str) -> Dict[str, Any]:
        """ディスパッチャの正規化処理を委譲する。"""

        return self._dispatcher._normalize_command_payload(payload, label=label)

    def _normalize_vpt_actions(
        self,
        actions: Optional[list[Dict[str, Any]]],
    ) -> list[Dict[str, Any]]:
        """VPT 指示の正規化をディスパッチャへ委譲する。"""

        return self._dispatcher._normalize_vpt_actions(actions)


__all__ = ["ActionDispatcher", "ActionModule"]
