# -*- coding: utf-8 -*-
"""VPT 実行とフォールバック制御を扱うハイブリッドアクションモジュール。"""

from typing import Dict, List, Optional
import logging

from utils import log_structured_event

from .base import ActionModule
from .errors import ActionValidationError


class HybridActions(ActionModule):
    """VPT 指示と通常コマンドの切り替えを安全に提供するアクション群。"""

    async def play_vpt_actions(
        self,
        actions: List[Dict[str, Any]],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """VPT で生成した低レベル操作列を Mineflayer へ転送する。"""

        payload: Dict[str, Any] = {"type": "playVptActions", "args": {"actions": actions}}
        if metadata:
            payload["args"]["metadata"] = metadata
        return await self._dispatch("playVptActions", payload)

    async def execute_hybrid_action(
        self,
        *,
        vpt_actions: Optional[List[Dict[str, Any]]],
        fallback_command: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """VPT 再生と通常コマンドを安全に切り替えるハイブリッド実行を提供する。"""

        normalized_vpt = self._normalize_vpt_actions(vpt_actions)
        normalized_fallback = (
            self._normalize_command_payload(fallback_command, label="fallback_command")
            if fallback_command is not None
            else None
        )

        if not normalized_vpt and normalized_fallback is None:
            raise ActionValidationError(
                "hybrid 指示には vpt_actions もしくは fallback_command のいずれかが必要です。"
            )

        last_error: Optional[str] = None
        if normalized_vpt:
            try:
                response = await self.play_vpt_actions(normalized_vpt, metadata=metadata)
                if response.get("ok"):
                    log_structured_event(
                        self.logger,
                        "hybrid action executed via vpt",
                        event_level="progress",
                        context={
                            "executor": "vpt",
                            "fallback_defined": normalized_fallback is not None,
                            "response": response,
                        },
                    )
                    return {"ok": True, "executor": "vpt", "response": response}
                last_error = str(response.get("error") or "Mineflayer reported ok=false")
            except Exception as exc:  # noqa: BLE001 - 呼び出し側で扱う
                last_error = str(exc)
                log_structured_event(
                    self.logger,
                    "hybrid action vpt path failed",
                    level=logging.WARNING,
                    event_level="warning",
                    context={"error": last_error},
                    exc_info=exc,
                )

        if normalized_fallback is None:
            raise ActionValidationError(
                "VPT 指示が失敗しましたが fallback_command が指定されていません。"
                f" reason={last_error or 'unknown'}"
            )

        fallback_response = await self._dispatch(
            normalized_fallback["type"],
            normalized_fallback,
        )
        log_structured_event(
            self.logger,
            "hybrid action executed via fallback command",
            event_level="progress" if fallback_response.get("ok") else "fault",
            level=logging.INFO if fallback_response.get("ok") else logging.WARNING,
            context={
                "executor": "command",
                "fallback_reason": last_error,
                "response": fallback_response,
            },
        )
        return {
            "ok": fallback_response.get("ok", False),
            "executor": "command",
            "response": fallback_response,
            "fallback_reason": last_error,
        }


__all__ = ["HybridActions"]
