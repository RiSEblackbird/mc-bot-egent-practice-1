# -*- coding: utf-8 -*-
"""アクション実行の WebSocket コマンドをラップするモジュール。"""

import itertools
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from bridge_ws import BotBridge
from utils import log_structured_event, setup_logger


class ActionValidationError(ValueError):
    """アクション呼び出し時の入力不備を明示する例外。"""


def _require_position(position: Dict[str, Any], *, label: str = "position") -> Dict[str, int]:
    """座標辞書に x/y/z の整数が含まれることを検証する補助関数。"""

    missing_keys = {axis for axis in ("x", "y", "z") if axis not in position}
    if missing_keys:
        raise ActionValidationError(f"{label} は x, y, z を含む必要があります: missing={sorted(missing_keys)}")

    validated: Dict[str, int] = {}
    for axis in ("x", "y", "z"):
        value = position[axis]
        if not isinstance(value, int):
            raise ActionValidationError(f"{label}.{axis} は int で指定してください: actual={type(value).__name__}")
        validated[axis] = value
    return validated


def _require_positions(positions: Sequence[Dict[str, Any]]) -> List[Dict[str, int]]:
    """座標配列が空でなく、各要素が座標辞書であることを検証する。"""

    if not positions:
        raise ActionValidationError("positions は 1 件以上の座標を含めてください")
    return [_require_position(pos, label="positions[]") for pos in positions]


def _require_non_empty_text(value: Optional[str], *, field: str) -> str:
    """文字列フィールドが空でないことを検証する。"""

    if value is None or not isinstance(value, str) or not value.strip():
        raise ActionValidationError(f"{field} は 1 文字以上の文字列で指定してください")
    return value.strip()


class Actions:
    """LLM が選択した高レベルアクションを Mineflayer コマンドへ変換するユーティリティ。"""

    def __init__(
        self,
        bridge: BotBridge,
        *,
        on_bridge_retry: Optional[Callable[[int, str], Awaitable[None]]] = None,
        on_bridge_give_up: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        self.bridge = bridge
        # アクション実行のトレースを残して、Mineflayer 側での挙動と突き合わせできるようにする。
        self.logger = setup_logger("actions")
        # command_id を付番して、Node 側のログと相互参照しやすくする。
        self._command_seq = itertools.count(1)
        # Bridge レベルのリトライをチャット通知などに転用するためのフック。
        self._on_bridge_retry = on_bridge_retry
        self._on_bridge_give_up = on_bridge_give_up
        self._current_directive_meta: Optional[Dict[str, Any]] = None

    def begin_directive_scope(self, meta: Dict[str, Any]) -> None:
        """直後のコマンドへ directive メタデータを付与する。"""

        self._current_directive_meta = dict(meta)

    def end_directive_scope(self) -> None:
        """directive メタデータのスコープを終了する。"""

        self._current_directive_meta = None

    async def say(self, text: str) -> Dict[str, Any]:
        """チャット送信コマンドを Mineflayer へ中継する。"""

        payload = {"type": "chat", "args": {"text": _require_non_empty_text(text, field="text")}}
        return await self._dispatch("chat", payload)

    async def move_to(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """指定座標への移動を要求するコマンドを送信する。"""

        payload = {"type": "moveTo", "args": _require_position({"x": x, "y": y, "z": z})}
        return await self._dispatch("moveTo", payload)

    async def mine_blocks(self, positions: List[Dict[str, int]]) -> Dict[str, Any]:
        """断面で破壊すべき座標群を Mineflayer へ渡す。

        Node 側では positions 配列を順次破壊する実装を想定し、ここでは
        Mineflayer 向けのシンプルなメッセージを送るだけに留める。"""

        payload = {"type": "mineBlocks", "args": {"positions": _require_positions(positions)}}
        return await self._dispatch("mineBlocks", payload)

    async def mine_ores(
        self,
        ore_names: List[str],
        *,
        scan_radius: int = 12,
        max_targets: int = 3,
    ) -> Dict[str, Any]:
        """周囲の鉱石を探索・採掘するコマンドを送信する。"""

        # Mineflayer 側での探索範囲や対象鉱石の種類を完全に指定し、
        # 再現性の高い採掘手順をリモート操作で実現する。

        if not ore_names:
            raise ActionValidationError("ore_names は 1 件以上指定してください")

        payload = {
            "type": "mineOre",
            "args": {
                "ores": ore_names,
                "scanRadius": int(scan_radius),
                "maxTargets": int(max_targets),
            },
        }
        return await self._dispatch("mineOre", payload)

    async def place_torch(self, position: Dict[str, int]) -> Dict[str, Any]:
        """たいまつを指定位置に設置するコマンドを送信する。"""

        payload = {"type": "placeTorch", "args": _require_position(position)}
        return await self._dispatch("placeTorch", payload)

    async def equip_item(
        self,
        *,
        tool_type: Optional[str] = None,
        item_name: Optional[str] = None,
        destination: str = "hand",
    ) -> Dict[str, Any]:
        """指定した種類のアイテムを手に持ち替える。"""

        args: Dict[str, Any] = {"destination": destination}
        if tool_type:
            args["toolType"] = tool_type
        if item_name:
            args["itemName"] = item_name

        payload = {"type": "equipItem", "args": args}
        return await self._dispatch("equipItem", payload)

    async def place_block(
        self,
        block: str,
        position: Dict[str, int],
        *,
        face: Optional[str] = None,
        sneak: bool = False,
    ) -> Dict[str, Any]:
        """任意のブロックを指定位置へ設置するコマンドを送信する。"""

        args: Dict[str, Any] = {
            "block": _require_non_empty_text(block, field="block"),
            "position": _require_position(position),
            "sneak": bool(sneak),
        }
        if face:
            args["face"] = face

        payload = {"type": "placeBlock", "args": args}
        return await self._dispatch("placeBlock", payload)

    async def set_role(self, role_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        """LangGraph からの役割切替を Node 側へ送信する。"""

        args: Dict[str, Any] = {"roleId": role_id}
        if reason:
            args["reason"] = reason

        payload = {"type": "setAgentRole", "args": args}
        return await self._dispatch("setAgentRole", payload)

    async def gather_status(self, kind: str) -> Dict[str, Any]:
        """Mineflayer 側から位置・所持品などのステータス情報を取得する。"""

        payload = {"type": "gatherStatus", "args": {"kind": kind}}
        return await self._dispatch("gatherStatus", payload)

    async def register_skill(
        self,
        *,
        skill_id: str,
        title: str,
        description: str,
        steps: List[str],
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """スキル定義を Mineflayer 側へ登録する。"""

        payload = {"type": "registerSkill", "args": {
            "skillId": skill_id,
            "title": title,
            "description": description,
            "steps": steps,
        }}
        if tags:
            payload["args"]["tags"] = tags
        return await self._dispatch("registerSkill", payload)

    async def invoke_skill(
        self,
        skill_id: str,
        *,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """登録済みスキルの再生を要求する。"""

        args: Dict[str, Any] = {"skillId": skill_id}
        if context:
            args["context"] = context
        payload = {"type": "invokeSkill", "args": args}
        return await self._dispatch("invokeSkill", payload)

    async def follow_player(
        self,
        target_name: str,
        *,
        stop_distance: int = 2,
        maintain_line_of_sight: bool = True,
    ) -> Dict[str, Any]:
        """指定プレイヤーを追従するコマンドを送信する。"""

        payload = {
            "type": "followPlayer",
            "args": {
                "target": _require_non_empty_text(target_name, field="target"),
                "stopDistance": int(stop_distance),
                "maintainLineOfSight": bool(maintain_line_of_sight),
            },
        }
        return await self._dispatch("followPlayer", payload)

    async def attack_entity(
        self,
        entity_name: str,
        *,
        mode: str = "melee",
        chase_distance: int = 6,
    ) -> Dict[str, Any]:
        """対象エンティティへの戦闘コマンドを送信する。"""

        normalized_mode = mode.lower()
        if normalized_mode not in {"melee", "ranged"}:
            raise ActionValidationError("mode は 'melee' もしくは 'ranged' を指定してください")

        payload = {
            "type": "attackEntity",
            "args": {
                "target": _require_non_empty_text(entity_name, field="target"),
                "mode": normalized_mode,
                "chaseDistance": int(chase_distance),
            },
        }
        return await self._dispatch("attackEntity", payload)

    async def craft_item(
        self,
        item_name: str,
        *,
        amount: int = 1,
        use_crafting_table: bool = True,
    ) -> Dict[str, Any]:
        """クラフトレシピを指定して作業台/インベントリで作成する。"""

        if amount <= 0:
            raise ActionValidationError("amount は 1 以上の整数で指定してください")

        payload = {
            "type": "craftItem",
            "args": {
                "item": _require_non_empty_text(item_name, field="item"),
                "amount": int(amount),
                "useCraftingTable": bool(use_crafting_table),
            },
        }
        return await self._dispatch("craftItem", payload)

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

    async def begin_skill_exploration(
        self,
        *,
        skill_id: str,
        description: str,
        step_context: str,
    ) -> Dict[str, Any]:
        """未習得スキルの探索モードを Mineflayer へ通知する。"""

        payload = {"type": "skillExplore", "args": {
            "skillId": skill_id,
            "description": description,
            "context": step_context,
        }}
        return await self._dispatch("skillExplore", payload)

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
        actions: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if actions is None:
            return []
        if not isinstance(actions, list):
            raise ActionValidationError("vpt_actions は配列で指定してください")
        normalized: List[Dict[str, Any]] = []
        for index, item in enumerate(actions):
            if not isinstance(item, dict):
                raise ActionValidationError(f"vpt_actions[{index}] はオブジェクトで指定してください")
            normalized.append(item)
        return normalized
