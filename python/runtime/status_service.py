from __future__ import annotations

"""Mineflayer の状態取得とサマリー生成を担うサービスモジュール。

AgentOrchestrator の計画実行ロジックから状態取得の詳細を切り離し、
メモリ更新やイベント履歴の蓄積を一元的に扱う。Actions や Memory など
副作用のある依存はコンストラクタ経由で注入する。
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from actions import Actions
from memory import Memory
from runtime.inventory_sync import InventorySynchronizer


class StatusService:
    """Mineflayer との状態同期とコンテキスト構築を担当する専用クラス。"""

    def __init__(
        self,
        *,
        actions: Actions,
        memory: Memory,
        inventory_sync: InventorySynchronizer,
        logger: logging.Logger,
        status_timeout_seconds: float,
        status_retry: int,
        status_backoff_seconds: float,
        structured_event_history_limit: int,
        perception_history_limit: int,
        default_role_label: str = "汎用サポーター",
    ) -> None:
        self.actions = actions
        self.memory = memory
        self.inventory_sync = inventory_sync
        self.logger = logger
        self.status_timeout_seconds = status_timeout_seconds
        self.status_retry = status_retry
        self.status_backoff_seconds = status_backoff_seconds
        self.structured_event_history_limit = structured_event_history_limit
        self.perception_history_limit = perception_history_limit
        self.default_role_label = default_role_label

    async def prime_status_for_planning(self) -> List[str]:
        """LLM へ渡す前に Mineflayer 状況を収集し、欠損項目を補完する。"""

        requested = ["general"]
        if not self.memory.get("player_pos_detail"):
            requested.append("position")
        if not self.memory.get("inventory_detail"):
            requested.append("inventory")

        failures: List[str] = []
        for kind in requested:
            ok = await self._request_status_with_backoff(kind)
            if not ok:
                failures.append(kind)

        return failures

    def build_context_snapshot(self, *, current_role_id: str) -> Dict[str, Any]:
        """LLM へ渡す簡易コンテキストを生成する。"""

        snapshot = {
            "player_pos": self.memory.get("player_pos", "不明"),
            "inventory_summary": self.memory.get("inventory", "不明"),
            "general_status": self.memory.get("general_status", "未記録"),
            "dig_permission": self.memory.get("dig_permission", "未評価"),
            "last_chat": self.memory.get("last_chat", "未記録"),
            "last_destination": self.memory.get("last_destination", "未記録"),
            "active_role": self.memory.get(
                "agent_active_role",
                {"id": current_role_id, "label": self.default_role_label},
            ),
        }
        minedojo_context = self.memory.get("minedojo_context")
        if minedojo_context:
            snapshot["minedojo_support"] = minedojo_context
        block_eval = self.memory.get("block_evaluation")
        if block_eval:
            snapshot["block_evaluation"] = block_eval
        structured_history = self.memory.get("structured_event_history")
        if isinstance(structured_history, list) and structured_history:
            snapshot["structured_event_history"] = structured_history[-3:]
        perception_history = self.memory.get("perception_snapshots")
        if isinstance(perception_history, list) and perception_history:
            snapshot["perception_history"] = perception_history[-3:]
        perception_summary = self.memory.get("perception_summary")
        if isinstance(perception_summary, str) and perception_summary.strip():
            snapshot["perception_summary"] = perception_summary.strip()
        last_plan_summary = self.memory.get("last_plan_summary")
        if isinstance(last_plan_summary, dict) and last_plan_summary:
            snapshot["last_plan_summary"] = last_plan_summary
        reflection_context = self.memory.build_reflection_context()
        if reflection_context:
            snapshot["recent_reflections"] = reflection_context
        active_reflection_prompt = self.memory.get_active_reflection_prompt()
        if active_reflection_prompt:
            snapshot["active_reflection_prompt"] = active_reflection_prompt
        recovery_hints = self.memory.get("recovery_hints")
        if isinstance(recovery_hints, list) and recovery_hints:
            snapshot["recovery_hints"] = recovery_hints
        self.logger.info("context snapshot built=%s", snapshot)
        return snapshot

    def collect_recent_mineflayer_context(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Mineflayer 由来の履歴をまとめ、LangGraph 連携用に返す。"""

        structured_event_history = self._load_history("structured_event_history")
        perception_history = self._load_history("perception_snapshots")

        snapshot = self.build_perception_snapshot()
        if snapshot:
            perception_history.append(snapshot)
        event_limit = self.structured_event_history_limit
        perception_limit = self.perception_history_limit
        structured_event_history = structured_event_history[-event_limit:]
        perception_history = perception_history[-perception_limit:]

        if snapshot:
            self.memory.set("perception_snapshots", perception_history)
        if structured_event_history:
            self.memory.set("structured_event_history", structured_event_history)

        return structured_event_history, perception_history

    def summarize_position_status(self, data: Dict[str, Any]) -> str:
        """Node 側から受け取った位置情報をプレイヤー向けの要約文へ整形する。"""

        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            position = data.get("position")
            if isinstance(position, dict):
                x = position.get("x")
                y = position.get("y")
                z = position.get("z")
                dimension = data.get("dimension") or "unknown"
                if all(isinstance(value, int) for value in (x, y, z)):
                    return (
                        "現在位置は "
                        f"X={x} / Y={y} / Z={z}（ディメンション: {dimension}）です。"
                    )

        return "現在位置の最新情報を取得しました。"

    def summarize_general_status(self, data: Dict[str, Any]) -> str:
        """体力・満腹度・掘削許可のステータスを読みやすい文章にまとめる。"""

        if isinstance(data, dict):
            formatted = str(data.get("formatted") or "").strip()
            if formatted:
                return formatted

            health = data.get("health")
            max_health = data.get("maxHealth")
            food = data.get("food")
            saturation = data.get("saturation")
            dig_permission = data.get("digPermission")
            if all(
                isinstance(value, (int, float))
                for value in (health, max_health, food, saturation)
            ) and isinstance(dig_permission, dict):
                allowed = dig_permission.get("allowed")
                reason = dig_permission.get("reason")
                permission_text = "あり" if allowed else f"なし（{reason}）"
                return (
                    "体力や満腹度は正常に取得できました。"
                    f"体力: {health}/{max_health}、"
                    f"満腹度: {food}/{saturation}、"
                    f"掘削許可: {permission_text}"
                )

        return "プレイヤーの状態を取得しました。"

    def build_perception_snapshot(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """位置・空腹度・天候をまとめた perception スナップショットを生成する。"""

        pos_detail = self.memory.get("player_pos_detail") or {}
        general_detail = self.memory.get("general_status_detail") or {}
        if not isinstance(pos_detail, dict):
            pos_detail = {}
        if not isinstance(general_detail, dict):
            general_detail = {}
        base = extra if isinstance(extra, dict) else {}

        position = None
        if all(axis in pos_detail for axis in ("x", "y", "z")):
            position = {
                "x": pos_detail.get("x"),
                "y": pos_detail.get("y"),
                "z": pos_detail.get("z"),
                "dimension": pos_detail.get("dimension") or pos_detail.get("world"),
            }

        hunger = base.get("food") or base.get("foodLevel") or base.get("hunger")
        if hunger is None:
            hunger = (
                general_detail.get("food")
                or general_detail.get("foodLevel")
                or general_detail.get("hunger")
            )

        weather = base.get("weather") or general_detail.get("weather")

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position": position,
            "food_level": hunger,
            "health": base.get("health") or general_detail.get("health"),
            "weather": weather,
            "is_raining": base.get("isRaining") or general_detail.get("isRaining"),
        }

        if isinstance(base, dict):
            for source_key, target_key in (
                ("weather", "weather"),
                ("time", "time"),
                ("lighting", "lighting"),
                ("hazards", "hazards"),
                ("nearby_entities", "nearby_entities"),
                ("nearbyEntities", "nearby_entities"),
                ("warnings", "warnings"),
                ("summary", "summary"),
            ):
                value = base.get(source_key)
                if value is not None:
                    snapshot[target_key] = value

        if not any(value is not None for value in snapshot.values()):
            return None

        return snapshot

    def ingest_perception_snapshot(
        self, snapshot: Dict[str, Any], *, source: str
    ) -> None:
        """perception スナップショットを履歴へ追加し、要約を更新する。"""

        history = self._append_perception_snapshot(snapshot)
        self.memory.set("perception_snapshots", history)
        summary = self._summarize_perception_snapshot(snapshot, source=source)
        if summary:
            self.memory.set("perception_summary", summary)

    async def _request_status_with_backoff(self, kind: str) -> bool:
        """タイムアウトと指数バックオフ付きで gather_status を呼び出す。"""

        backoff = self.status_backoff_seconds
        for attempt in range(1, self.status_retry + 2):
            try:
                resp = await asyncio.wait_for(
                    self.actions.gather_status(kind),
                    timeout=self.status_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "gather_status timed out kind=%s attempt=%d", kind, attempt
                )
                resp = None
            except Exception as exc:  # pragma: no cover - 例外経路はログ検証を優先
                self.logger.exception(
                    "gather_status raised unexpected error kind=%s attempt=%d", kind, attempt
                )
                resp = {"ok": False, "error": str(exc)}

            if isinstance(resp, dict) and resp.get("ok"):
                self._cache_status(kind, resp.get("data") or {})
                return True

            if attempt <= self.status_retry:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            error_detail = "Mineflayer から応答がありません。"
            if isinstance(resp, dict) and resp.get("error"):
                error_detail = str(resp.get("error"))
            self.logger.warning(
                "gather_status failed permanently kind=%s error=%s", kind, error_detail
            )
        return False

    def _cache_status(self, kind: str, data: Dict[str, Any]) -> None:
        """gather_status の結果を要約し、再利用しやすい形で保存する。"""

        if kind == "position":
            summary = self.summarize_position_status(data)
            self.memory.set("player_pos", summary)
            self.memory.set("player_pos_detail", data)
            return

        if kind == "inventory":
            summary = self.inventory_sync.summarize(data)
            self.memory.set("inventory", summary)
            self.memory.set("inventory_detail", data)
            return

        if kind == "general":
            summary = self.summarize_general_status(data)
            self.memory.set("general_status", summary)
            self.memory.set("general_status_detail", data)
            if isinstance(data, dict) and "digPermission" in data:
                self.memory.set("dig_permission", data.get("digPermission"))
            self._record_structured_event_history(data)
            self._store_perception_from_status(data)
            return

        self.logger.info("cache_status skipped unknown kind=%s", kind)

    def _record_structured_event_history(self, payload: Dict[str, Any]) -> None:
        """Mineflayer 側の構造化イベント配列を履歴に蓄積する。"""

        history = self._load_history("structured_event_history")
        limit = self.structured_event_history_limit
        for key in ("structuredEvents", "events", "eventHistory"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                new_events = [item for item in candidate if isinstance(item, dict)]
                if new_events:
                    history.extend(new_events)
                break

        trimmed = history[-limit:]
        self.memory.set("structured_event_history", trimmed)

    def _store_perception_from_status(self, status: Dict[str, Any]) -> None:
        """general ステータスに含まれる perception 情報を履歴へ追加する。"""

        perception_payload = None
        for key in ("perception", "perceptionSnapshot", "perception_snapshot"):
            candidate = status.get(key)
            if isinstance(candidate, dict):
                perception_payload = candidate
                break

        snapshot = self.build_perception_snapshot(perception_payload)
        if snapshot is None:
            return

        self.ingest_perception_snapshot(snapshot, source="gather_status")

    def _append_perception_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """perception スナップショットを履歴へ追加し、上限件数で丸める。"""

        history = self._load_history("perception_snapshots")
        history.append(snapshot)
        return history[-self.perception_history_limit :]

    def _summarize_perception_snapshot(
        self, snapshot: Dict[str, Any], *, source: str = "unknown"
    ) -> Optional[str]:
        """Node 側から届いた perception スナップショットを短い文章へ要約する。"""

        parts: List[str] = []
        hazards = snapshot.get("hazards")
        if isinstance(hazards, dict):
            liquid_count = hazards.get("liquids")
            voids = hazards.get("voids")
            if isinstance(liquid_count, (int, float)) and liquid_count > 0:
                parts.append(f"液体検知: {int(liquid_count)} 箇所")
            if isinstance(voids, (int, float)) and voids > 0:
                parts.append(f"落下リスク: {int(voids)} 箇所")

        entities = snapshot.get("nearby_entities") or snapshot.get("nearbyEntities")
        if isinstance(entities, dict):
            hostile_count = entities.get("hostiles")
            if isinstance(hostile_count, (int, float)) and hostile_count > 0:
                details = entities.get("details") or []
                formatted = []
                if isinstance(details, list):
                    for entry in details[:3]:
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("kind") not in {"hostile", "Hostile"}:
                            continue
                        name = entry.get("name") or "敵対モブ"
                        distance = entry.get("distance")
                        bearing = entry.get("bearing") or ""
                        if isinstance(distance, (int, float)):
                            formatted.append(f"{name}({distance:.1f}m{bearing})")
                        else:
                            formatted.append(str(name))
                parts.append(
                    f"敵対モブ {int(hostile_count)} 体: {', '.join(formatted) if formatted else '詳細不明'}"
                )

        lighting = snapshot.get("lighting")
        if isinstance(lighting, dict):
            block_light = lighting.get("block")
            if isinstance(block_light, (int, float)):
                parts.append(f"明るさ: {block_light}")

        weather = snapshot.get("weather")
        if isinstance(weather, dict):
            label = weather.get("label")
            if isinstance(label, str) and label:
                parts.append(f"天候: {label}")

        summary = " / ".join(part for part in parts if part)
        return summary or None

    def _load_history(self, key: str) -> List[Dict[str, Any]]:
        """メモリに格納された履歴リストを辞書のみ抽出して返す。"""

        raw = self.memory.get(key, [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]


__all__ = ["StatusService"]
