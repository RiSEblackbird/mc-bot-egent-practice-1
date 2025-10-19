# -*- coding: utf-8 -*-
"""MineDojo API やローカルデータセットからミッション情報を取得するクライアント。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

from config import MineDojoConfig
from utils import setup_logger


@dataclass
class MineDojoMission:
    """LLM プロンプトへ差し込むためのミッション情報。"""

    mission_id: str
    title: str
    objective: str
    tags: Sequence[str] = field(default_factory=tuple)
    source: str = "api"
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_prompt_payload(self) -> Dict[str, Any]:
        """プロンプトへ渡しやすい要約ペイロードを生成する。"""

        return {
            "mission_id": self.mission_id,
            "title": self.title,
            "objective": self.objective,
            "tags": list(self.tags),
            "source": self.source,
        }


@dataclass
class MineDojoDemonstration:
    """MineDojo のデモを Actions.play_vpt_actions と共有するための構造体。"""

    demo_id: str
    summary: str
    actions: Sequence[Dict[str, Any]] = field(default_factory=tuple)
    source: str = "api"
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> Dict[str, Any]:
        """Actions 側へ転送する際のメタデータを生成する。"""

        payload = {
            "demo_id": self.demo_id,
            "summary": self.summary,
            "source": self.source,
        }
        if "duration" in self.raw:
            payload["duration"] = self.raw["duration"]
        if "success" in self.raw:
            payload["success"] = self.raw["success"]
        return payload


class MineDojoClient:
    """MineDojo API とローカルデータセットを透過的に扱う薄いクライアント。"""

    def __init__(self, config: MineDojoConfig, *, http_client: Optional[httpx.AsyncClient] = None) -> None:
        # MineDojo 連携で参照するパラメータ群を保持し、メソッド毎に再利用する。
        self._config = config
        self._logger = setup_logger("services.minedojo")
        self._http_client = http_client
        self._mission_cache: Dict[str, MineDojoMission] = {}
        self._demo_cache: Dict[str, List[MineDojoDemonstration]] = {}
        self._cache_dir = Path(config.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_lock = asyncio.Lock()

    async def aclose(self) -> None:
        """生成した HTTP クライアントを明示的に破棄する。"""

        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def fetch_mission(self, mission_id: str) -> Optional[MineDojoMission]:
        """ミッション ID に対応するメタ情報を取得する。"""

        mission_key = mission_id.strip()
        if not mission_key:
            return None

        cached = self._mission_cache.get(mission_key)
        if cached:
            return cached

        payload = await self._read_cached_json(f"mission_{mission_key}.json")
        source = "cache"
        if payload is None:
            payload = await self._load_local_dataset("missions", mission_key)
            source = "dataset" if payload is not None else source
        if payload is None:
            payload = await self._request_json(f"/missions/{mission_key}")
            source = "api" if payload is not None else source
            if payload is not None:
                await self._write_cache(f"mission_{mission_key}.json", payload)

        if not isinstance(payload, dict):
            return None

        mission = self._parse_mission_payload(mission_key, payload, source)
        self._mission_cache[mission_key] = mission
        return mission

    async def fetch_demonstrations(
        self, mission_id: str, *, limit: int = 1
    ) -> List[MineDojoDemonstration]:
        """ミッションに紐づくデモを取得する。"""

        mission_key = mission_id.strip()
        if not mission_key:
            return []

        cached = self._demo_cache.get(mission_key)
        if cached:
            return cached[:limit]

        payload = await self._read_cached_json(f"demo_{mission_key}.json")
        source = "cache"
        if payload is None:
            payload = await self._load_local_dataset("demos", mission_key)
            source = "dataset" if payload is not None else source
        if payload is None:
            endpoint = f"/missions/{mission_key}/demonstrations"
            payload = await self._request_json(endpoint, params={"limit": limit})
            source = "api" if payload is not None else source
            if payload is not None:
                await self._write_cache(f"demo_{mission_key}.json", payload)

        demos = self._parse_demonstrations_payload(mission_key, payload, source)
        self._demo_cache[mission_key] = demos
        return demos[:limit]

    async def _request_json(
        self, path: str, *, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        """MineDojo API から JSON を取得する汎用メソッド。"""

        if not self._config.api_key:
            self._logger.info("MineDojo API key is not configured; skipping remote request path=%s", path)
            return None

        client = await self._ensure_http_client()
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        try:
            response = await client.get(path, headers=headers, params=params)
        except httpx.HTTPError:
            self._logger.exception("MineDojo API request failed path=%s", path)
            return None

        if response.status_code >= 400:
            self._logger.warning(
                "MineDojo API returned error status=%s path=%s body=%s",
                response.status_code,
                path,
                response.text,
            )
            return None

        try:
            return response.json()
        except ValueError:
            self._logger.error("MineDojo API response JSON decode failed path=%s", path)
            return None

    async def _ensure_http_client(self) -> httpx.AsyncClient:
        """httpx.AsyncClient を遅延生成し、再利用する。"""

        if self._http_client is not None:
            return self._http_client

        timeout = httpx.Timeout(self._config.request_timeout)
        self._http_client = httpx.AsyncClient(
            base_url=self._config.api_base_url,
            timeout=timeout,
        )
        return self._http_client

    async def _read_cached_json(self, filename: str) -> Optional[Any]:
        """キャッシュディレクトリから JSON を読み込む。"""

        cache_path = self._cache_dir / filename
        if not cache_path.exists():
            return None

        async with self._cache_lock:
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._logger.exception("failed to read MineDojo cache file path=%s", cache_path)
                return None

    async def _write_cache(self, filename: str, payload: Any) -> None:
        """取得した JSON をキャッシュへ書き出す。"""

        cache_path = self._cache_dir / filename
        async with self._cache_lock:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                self._logger.exception("failed to write MineDojo cache file path=%s", cache_path)

    async def _load_local_dataset(self, subdir: str, mission_id: str) -> Optional[Any]:
        """ローカルデータセットを参照できる場合はファイルから読み込む。"""

        if not self._config.dataset_dir:
            return None

        dataset_path = Path(self._config.dataset_dir) / subdir / f"{mission_id}.json"
        if not dataset_path.exists():
            return None

        try:
            return json.loads(dataset_path.read_text(encoding="utf-8"))
        except Exception:
            self._logger.exception("failed to load MineDojo dataset path=%s", dataset_path)
            return None

    def _parse_mission_payload(
        self, mission_id: str, payload: Dict[str, Any], source: str
    ) -> MineDojoMission:
        """ミッション JSON から必要項目だけ抽出する。"""

        title = str(payload.get("title") or payload.get("name") or mission_id)
        objective = str(payload.get("objective") or payload.get("description") or "")
        tags_field = payload.get("tags")
        if isinstance(tags_field, (list, tuple)):
            tags = tuple(str(tag) for tag in tags_field if str(tag).strip())
        else:
            tags = ()
        return MineDojoMission(
            mission_id=mission_id,
            title=title,
            objective=objective,
            tags=tags,
            source=source,
            raw=payload,
        )

    def _parse_demonstrations_payload(
        self, mission_id: str, payload: Any, source: str
    ) -> List[MineDojoDemonstration]:
        """デモ JSON から実行に必要なアクション列を抽出する。"""

        if isinstance(payload, dict) and "demos" in payload:
            entries = payload.get("demos")
        else:
            entries = payload

        if not isinstance(entries, list):
            return []

        demos: List[MineDojoDemonstration] = []
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            demo_id = str(item.get("id") or item.get("demo_id") or f"{mission_id}-{index}")
            summary = str(item.get("summary") or item.get("description") or "")
            actions_field = item.get("actions") or item.get("trajectory")
            actions: Sequence[Dict[str, Any]] = []
            if isinstance(actions_field, list):
                normalized_actions: List[Dict[str, Any]] = []
                for action in actions_field:
                    if isinstance(action, dict):
                        normalized_actions.append(action)
                actions = normalized_actions
            demos.append(
                MineDojoDemonstration(
                    demo_id=demo_id,
                    summary=summary,
                    actions=actions,
                    source=source,
                    raw=item,
                )
            )
        return demos


__all__ = [
    "MineDojoClient",
    "MineDojoMission",
    "MineDojoDemonstration",
]
