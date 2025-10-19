# -*- coding: utf-8 -*-
"""VPT 推論パイプラインを管理するサービスレイヤー。

Mineflayer から収集した観測を VPT (Video PreTraining) 互換の
特徴量へ変換し、取得済みモデルで操作シーケンスを生成する。
実運用では Hugging Face から提供されている OpenAI VPT モデルの
チェックポイントを利用することを想定しているが、テスト環境や
モデル未導入の環境では安全なヒューリスティック動作へフォール
バックする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from utils import setup_logger

try:  # optional dependency: PyTorch
    import torch
except ImportError:  # pragma: no cover - PyTorch が存在しない環境でのフォールバック
    torch = None  # type: ignore[assignment]

try:  # optional dependency: huggingface_hub
    from huggingface_hub import hf_hub_download, model_info
except ImportError:  # pragma: no cover - huggingface_hub が無い場合は後段で警告
    hf_hub_download = None  # type: ignore[assignment]
    model_info = None  # type: ignore[assignment]

if torch is not None:  # pragma: no cover - 型チェック用のエイリアス
    TorchModule = torch.jit.ScriptModule
else:  # pragma: no cover
    TorchModule = Any

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 循環依存を避けるため型チェック時のみインポート
    from bridge_ws import BotBridge


@dataclass(frozen=True)
class VPTModelSpec:
    """Hugging Face 上にホストされた VPT モデルの識別子情報。"""

    repo_id: str
    filename: str
    revision: str = "main"
    expected_license: Optional[str] = "mit"


def _wrap_degrees(value: float) -> float:
    """-180～180 度の範囲へ折り返して扱いやすくする。"""

    normalized = (value + 180.0) % 360.0 - 180.0
    return normalized


class _TorchJitPolicyAdapter:
    """TorchScript 化された VPT ポリシーを Python サイドで扱う薄いラッパー。"""

    def __init__(self, module: TorchModule, *, device: str, dtype: str = "float32") -> None:
        self.module = module
        self.device = device
        self.dtype = dtype

        # VPT ポリシーは推論専用のため常時 eval モードを維持する。
        if torch is not None:
            self.module.eval()

    def __call__(self, features: Sequence[float], *, max_steps: int, temperature: float) -> List[Dict[str, Any]]:
        if torch is None:
            raise RuntimeError("PyTorch is not available. Cannot execute VPT policy.")

        feature_tensor = torch.tensor(features, dtype=getattr(torch, self.dtype), device=self.device).unsqueeze(0)

        with torch.no_grad():
            # TorchScript 側で `infer_actions` 互換 API が公開されている想定。
            if hasattr(self.module, "infer_actions"):
                raw_actions = self.module.infer_actions(feature_tensor, max_steps=max_steps, temperature=temperature)
            else:
                raw_actions = self.module(feature_tensor, max_steps=max_steps, temperature=temperature)

        return _normalize_policy_output(raw_actions, max_steps=max_steps)


def _normalize_policy_output(raw_actions: Any, *, max_steps: int) -> List[Dict[str, Any]]:
    """TorchScript から返される様々な形式の出力を統一的な辞書配列へ整形する。"""

    actions: List[Dict[str, Any]] = []

    if raw_actions is None:
        return actions

    if isinstance(raw_actions, (list, tuple)):
        for item in raw_actions[:max_steps]:
            actions.append(_ensure_action_dict(item))
        return actions

    if torch is not None and isinstance(raw_actions, torch.Tensor):
        tensor = raw_actions.detach().cpu().tolist()
        for row in tensor[:max_steps]:
            actions.append(_ensure_action_dict(row))
        return actions

    # 辞書単体が返るケースでは単一アクションとして扱う。
    if isinstance(raw_actions, Mapping):
        actions.append(_ensure_action_dict(raw_actions))
        return actions

    raise TypeError(f"Unsupported VPT policy output type: {type(raw_actions)!r}")


def _ensure_action_dict(raw: Any) -> Dict[str, Any]:
    """モデル出力を Node.js 側が解釈できる辞書形式へ変換する。"""

    if isinstance(raw, Mapping):
        return dict(raw)

    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        kind = str(raw[0])
        duration_ticks = int(raw[2]) if isinstance(raw[2], (int, float)) else 0
        payload: Dict[str, Any] = {"kind": kind, "durationTicks": duration_ticks}
        if kind == "control":
            payload["control"] = str(raw[1])
        elif kind == "look":
            payload.update({"yaw": float(raw[1]), "pitch": 0.0, "relative": True})
        return payload

    if isinstance(raw, str):
        return {"kind": raw, "durationTicks": 0}

    raise TypeError(f"Unsupported VPT action element: {raw!r}")


class VPTController:
    """VPT モデルのロード・推論・観測整形を一元管理するファサード。"""

    def __init__(
        self,
        *,
        model_spec: Optional[VPTModelSpec] = None,
        cache_dir: str | Path = "var/vpt",
        device: Optional[str] = None,
        tick_interval_ms: float = 50.0,
        policy: Optional[Callable[[Sequence[float], Dict[str, Any]], List[Dict[str, Any]]]] = None,
    ) -> None:
        self.model_spec = model_spec
        self.cache_dir = Path(cache_dir)
        self.device = device or ("cuda" if torch is not None and torch.cuda.is_available() else "cpu")
        self.tick_interval_ms = max(1.0, float(tick_interval_ms))
        self.logger = setup_logger("vpt")
        self._policy: Optional[Callable[[Sequence[float], Dict[str, Any]], List[Dict[str, Any]]]] = policy
        self._loaded_model_path: Optional[Path] = None

    async def gather_observation(self, bridge: "BotBridge") -> Mapping[str, Any]:
        """Mineflayer から VPT 用観測値を取得する。"""

        response = await bridge.send({"type": "gatherVptObservation", "args": {}})
        if not response.get("ok"):
            raise RuntimeError(f"gatherVptObservation failed: {response}")
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise TypeError("gatherVptObservation returned invalid payload")
        return data

    def verify_model_license(self) -> Optional[str]:
        """モデルカードに記載されたライセンスを検証し、期待値と一致するか確認する。"""

        if not self.model_spec or model_info is None:
            return None

        info = model_info(self.model_spec.repo_id, revision=self.model_spec.revision)
        license_value = (info.cardData or {}).get("license") or info.license

        if self.model_spec.expected_license and license_value:
            normalized_actual = license_value.strip().lower()
            normalized_expected = self.model_spec.expected_license.strip().lower()
            if normalized_actual != normalized_expected:
                raise ValueError(
                    f"Model license mismatch: expected {normalized_expected}, got {normalized_actual}"
                )

        return license_value

    def ensure_model(self) -> Path:
        """モデルチェックポイントをローカルへ取得し、ファイルパスを返す。"""

        if not self.model_spec:
            raise RuntimeError("model_spec is not configured")

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if self._loaded_model_path and self._loaded_model_path.exists():
            return self._loaded_model_path

        if hf_hub_download is None:
            raise RuntimeError("huggingface_hub is not installed. Install it to download VPT weights.")

        file_path = hf_hub_download(
            repo_id=self.model_spec.repo_id,
            filename=self.model_spec.filename,
            revision=self.model_spec.revision,
            local_dir=str(self.cache_dir),
        )

        resolved = Path(file_path)
        self._loaded_model_path = resolved
        self.logger.info("VPT model downloaded", extra={"path": str(resolved)})
        return resolved

    def load_pretrained(self) -> None:
        """TorchScript 形式の VPT モデルを読み込み、推論アダプターを初期化する。"""

        if torch is None:
            raise RuntimeError("PyTorch is not installed. Install torch before loading VPT models.")

        checkpoint_path = self.ensure_model()
        module = torch.jit.load(str(checkpoint_path), map_location=self.device)
        adapter = _TorchJitPolicyAdapter(module, device=self.device)

        def _policy(features: Sequence[float], meta: Dict[str, Any]) -> List[Dict[str, Any]]:
            return adapter(
                features,
                max_steps=int(meta.get("max_steps", 120)),
                temperature=float(meta.get("temperature", 0.0)),
            )

        self._policy = _policy

    def generate_action_sequence(
        self,
        observation: Mapping[str, Any],
        *,
        max_actions: int = 120,
        temperature: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """観測値から VPT アクション列を生成する。"""

        max_actions = max(1, int(max_actions))
        feature_vector = self._encode_features(observation)

        if self._policy:
            try:
                actions = self._policy(
                    feature_vector,
                    {
                        "max_steps": max_actions,
                        "temperature": float(temperature),
                        "tick_interval_ms": self.tick_interval_ms,
                    },
                )
                if actions:
                    return actions[:max_actions]
            except Exception as error:  # pragma: no cover - 例外発生時はヒューリスティックへ切替
                self.logger.error("VPT policy execution failed", exc_info=error)

        return self._heuristic_policy(observation, max_actions=max_actions)

    def _encode_features(self, observation: Mapping[str, Any]) -> List[float]:
        """VPT モデルへ入力するための特徴量ベクトルを構築する。"""

        position = observation.get("position", {})
        velocity = observation.get("velocity", {})
        orientation = observation.get("orientation", {})
        navigation_hint = observation.get("navigationHint", {})
        status = observation.get("status", {})

        def _get(mapping: Mapping[str, Any], key: str, default: float = 0.0) -> float:
            try:
                return float(mapping.get(key, default))
            except (TypeError, ValueError):
                return default

        feature_vector: List[float] = [
            _get(position, "x") / 100.0,
            _get(position, "y") / 100.0,
            _get(position, "z") / 100.0,
            _get(velocity, "x"),
            _get(velocity, "y"),
            _get(velocity, "z"),
            math.radians(_get(orientation, "yawDegrees")),
            math.radians(_get(orientation, "pitchDegrees")),
            _get(navigation_hint, "horizontalDistance"),
            _get(navigation_hint, "verticalOffset"),
            _get(status, "health"),
            _get(status, "food"),
            1.0 if bool(observation.get("onGround", True)) else 0.0,
        ]

        hotbar = observation.get("hotbar", [])
        if isinstance(hotbar, Iterable):
            for slot in hotbar:
                if isinstance(slot, Mapping):
                    feature_vector.append(_get(slot, "count"))
                else:
                    feature_vector.append(0.0)

        return feature_vector

    def _heuristic_policy(self, observation: Mapping[str, Any], *, max_actions: int) -> List[Dict[str, Any]]:
        """モデルが利用できない場合の安全なヒューリスティックポリシー。"""

        actions: List[Dict[str, Any]] = []
        orientation = observation.get("orientation", {})
        navigation_hint = observation.get("navigationHint", {})
        on_ground = bool(observation.get("onGround", True))
        velocity = observation.get("velocity", {})

        current_yaw = float(orientation.get("yawDegrees", 0.0))
        target_yaw = float(navigation_hint.get("targetYawDegrees", current_yaw))
        yaw_delta = _wrap_degrees(target_yaw - current_yaw)

        if abs(yaw_delta) > 5.0:
            actions.append(
                {
                    "kind": "look",
                    "yaw": yaw_delta,
                    "pitch": 0.0,
                    "relative": True,
                    "durationTicks": self._ticks_for_duration(0.2),
                }
            )

        horizontal_distance = float(navigation_hint.get("horizontalDistance", 0.0))
        vertical_offset = float(navigation_hint.get("verticalOffset", 0.0))
        upward_velocity = float(velocity.get("y", 0.0))

        if not on_ground and upward_velocity < 0:
            actions.append({"kind": "wait", "durationTicks": self._ticks_for_duration(0.4)})

        if on_ground and vertical_offset > 0.75:
            actions.append(
                {
                    "kind": "control",
                    "control": "jump",
                    "state": True,
                    "durationTicks": self._ticks_for_duration(0.1),
                }
            )
            actions.append(
                {
                    "kind": "control",
                    "control": "jump",
                    "state": False,
                    "durationTicks": 0,
                }
            )

        if horizontal_distance > 0.5:
            travel_ticks = self._ticks_for_duration(min(horizontal_distance / 3.0, 2.0))
            actions.append(
                {
                    "kind": "control",
                    "control": "forward",
                    "state": True,
                    "durationTicks": travel_ticks,
                }
            )
            actions.append(
                {
                    "kind": "control",
                    "control": "forward",
                    "state": False,
                    "durationTicks": 0,
                }
            )

        return actions[:max_actions]

    def _ticks_for_duration(self, seconds: float) -> int:
        """秒数を Minecraft Tick 数へ換算する。"""

        seconds = max(0.0, float(seconds))
        tick_length = self.tick_interval_ms / 1000.0
        ticks = int(round(seconds / tick_length))
        return max(1, ticks)

