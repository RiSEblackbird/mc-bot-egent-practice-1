# -*- coding: utf-8 -*-
"""ハイブリッド指示（VPT + フォールバック）の解析と実行を担当するモジュール。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from actions import ActionValidationError
from planner import ActionDirective, ReActStep
from utils import setup_logger


@dataclass
class HybridDirectivePayload:
    """LangGraph から渡される hybrid 指示の解析結果を保持する構造体。"""

    vpt_actions: List[Dict[str, Any]]
    fallback_command: Optional[Dict[str, Any]]
    metadata: Dict[str, Any]


class HybridDirectiveHandler:
    """hybrid executor のパースと実行を分離し、AgentOrchestrator を薄くするヘルパー。"""

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        self._logger = setup_logger("agent.hybrid_directive")

    def parse_arguments(self, directive: ActionDirective) -> HybridDirectivePayload:
        args = directive.args if isinstance(directive.args, dict) else {}
        raw_vpt_actions: Any = None
        if "vpt_actions" in args:
            raw_vpt_actions = args.get("vpt_actions")
        elif "vptActions" in args:
            raw_vpt_actions = args.get("vptActions")
        vpt_actions: List[Dict[str, Any]] = []
        if raw_vpt_actions is not None:
            if not isinstance(raw_vpt_actions, list):
                raise ValueError("vpt_actions は配列で指定してください。")
            for index, item in enumerate(raw_vpt_actions):
                if not isinstance(item, dict):
                    raise ValueError(f"vpt_actions[{index}] はオブジェクトで指定してください。")
                vpt_actions.append(item)

        fallback_command = args.get("fallback_command")
        if fallback_command is None and "fallbackCommand" in args:
            fallback_command = args.get("fallbackCommand")
        if fallback_command is not None and not isinstance(fallback_command, dict):
            raise ValueError("fallback_command はオブジェクトで指定してください。")

        metadata = args.get("metadata")
        if metadata is None and "vpt_metadata" in args:
            metadata = args.get("vpt_metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata はオブジェクトで指定してください。")
        metadata = dict(metadata or {})

        if not vpt_actions and fallback_command is None:
            raise ValueError("hybrid 指示には vpt_actions もしくは fallback_command のいずれかが必要です。")

        return HybridDirectivePayload(
            vpt_actions=vpt_actions,
            fallback_command=fallback_command,
            metadata=metadata,
        )

    async def execute(
        self,
        directive: ActionDirective,
        payload: HybridDirectivePayload,
        *,
        directive_meta: Optional[Dict[str, Any]],
        react_entry: Optional[ReActStep],
        thought_text: str,
        index: int,
        total_steps: int,
    ) -> bool:
        orchestrator = self._orchestrator
        try:
            with orchestrator._directive_scope(directive_meta):
                result = await orchestrator.actions.execute_hybrid_action(
                    vpt_actions=payload.vpt_actions,
                    fallback_command=payload.fallback_command,
                    metadata=payload.metadata or None,
                )
        except ActionValidationError as exc:
            self._logger.warning("hybrid directive validation failed: %s", exc)
            await orchestrator.movement_service.report_execution_barrier(
                directive.label or directive.step or "hybrid",
                f"ハイブリッド指示の検証に失敗しました: {exc}",
            )
            return False
        except Exception:
            self._logger.exception("hybrid directive execution failed")
            await orchestrator.movement_service.report_execution_barrier(
                directive.label or directive.step or "hybrid",
                "hybrid 指示の実行中に例外が発生しました。",
            )
            return False

        event_context = {
            "step_index": index + 1,
            "total_steps": total_steps,
            "thought": thought_text,
            "action": react_entry.action if react_entry else directive.step,
            "fallback": bool(payload.fallback_command),
            "vpt_actions": len(payload.vpt_actions),
        }
        if isinstance(result, dict):
            event_context.update({"response": result.get("ok"), "error": result.get("error")})

        orchestrator._log_structured_react_step(
            "hybrid directive executed",
            event_level="info",
            context=event_context,
        )
        return True


__all__ = ["HybridDirectivePayload", "HybridDirectiveHandler"]
