# -*- coding: utf-8 -*-
"""建築フェーズ管理の純粋関数群。

建築系の LangGraph ノードは、副作用を持つ Mineflayer 呼び出しを行う前に、
このモジュールで提供する純粋関数を利用して進捗判断・資材調達計画・
配置バッチの選定を行う。ジョブが中断されてもチェックポイント情報だけ
を元に安全に再開・ロールバックできるよう、状態遷移ロジックを集約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


class BuildingPhase(str, Enum):
    """建築ワークフローのフェーズ。"""

    SURVEY = "survey"
    PROCUREMENT = "procurement"
    PLACEMENT = "placement"
    INSPECTION = "inspection"


@dataclass(frozen=True)
class PlacementTask:
    """ブロック名と座標をまとめた配置バッチ要素。"""

    block: str
    coords: Tuple[int, int, int]


@dataclass(frozen=True)
class BuildingCheckpoint:
    """建築再開時に必要なミニマルなチェックポイント情報。"""

    phase: BuildingPhase = BuildingPhase.SURVEY
    reserved_materials: Mapping[str, int] = field(default_factory=dict)
    placed_blocks: int = 0

    def to_dict(self) -> Dict[str, Union[str, int, Dict[str, int]]]:
        """シリアライズ用の辞書を生成する。"""

        return {
            "phase": self.phase.value,
            "reserved_materials": {k: int(v) for k, v in self.reserved_materials.items()},
            "placed_blocks": int(self.placed_blocks),
        }


_PHASE_SEQUENCE: Tuple[BuildingPhase, ...] = (
    BuildingPhase.SURVEY,
    BuildingPhase.PROCUREMENT,
    BuildingPhase.PLACEMENT,
    BuildingPhase.INSPECTION,
)


def restore_checkpoint(raw: Optional[Mapping[str, object]]) -> BuildingCheckpoint:
    """チェックポイントの復元を行い、欠損値は安全な既定値で補う。"""

    if not isinstance(raw, Mapping):
        return BuildingCheckpoint()

    phase_value = raw.get("phase")
    try:
        phase = BuildingPhase(str(phase_value)) if phase_value is not None else BuildingPhase.SURVEY
    except ValueError:
        # 不明なフェーズは安全側の SURVEY へ戻す。
        phase = BuildingPhase.SURVEY

    reserved_raw = raw.get("reserved_materials")
    reserved: Dict[str, int] = {}
    if isinstance(reserved_raw, Mapping):
        for key, value in reserved_raw.items():
            try:
                quantity = int(value)
            except (TypeError, ValueError):
                continue
            if quantity > 0:
                reserved[str(key)] = quantity

    placed_value = raw.get("placed_blocks")
    try:
        placed_blocks = max(int(placed_value), 0)
    except (TypeError, ValueError):
        placed_blocks = 0

    # 総配置数よりも大きい placed_blocks が渡されても、後続処理で自然に丸め込まれる。
    return BuildingCheckpoint(phase=phase, reserved_materials=reserved, placed_blocks=placed_blocks)


def checkpoint_to_dict(checkpoint: BuildingCheckpoint) -> Dict[str, Union[str, int, Dict[str, int]]]:
    """チェックポイントをシリアライズし、Memory へ格納しやすい形へ整える。"""

    return checkpoint.to_dict()


def plan_material_procurement(
    requirements: Mapping[str, int],
    inventory: Mapping[str, int],
    reserved: Optional[Mapping[str, int]] = None,
) -> Dict[str, int]:
    """不足資材を算出し、調達が必要なブロック数を返す。"""

    plan: Dict[str, int] = {}
    reserved = reserved or {}
    for name, required in requirements.items():
        try:
            required_int = max(int(required), 0)
        except (TypeError, ValueError):
            continue
        available = max(int(inventory.get(name, 0)), 0)
        already_reserved = max(int(reserved.get(name, 0)), 0)
        missing = required_int - min(required_int, available + already_reserved)
        if missing > 0:
            plan[name] = missing
    return plan


def plan_block_placement(
    layout: Sequence[Union[PlacementTask, Mapping[str, object], Tuple[str, Iterable[int]]]],
    placed_blocks: int,
    *,
    batch_size: int = 5,
) -> Tuple[List[PlacementTask], int]:
    """配置すべきブロックの次バッチを抽出し、完了カウントを計算する。"""

    normalized_layout = _normalize_layout(layout)
    total_blocks = len(normalized_layout)
    start_index = min(max(placed_blocks, 0), total_blocks)
    end_index = min(start_index + max(batch_size, 1), total_blocks)
    return normalized_layout[start_index:end_index], end_index


def advance_building_state(
    checkpoint: BuildingCheckpoint,
    requirements: Mapping[str, int],
    inventory: Mapping[str, int],
    layout: Sequence[Union[PlacementTask, Mapping[str, object], Tuple[str, Iterable[int]]]],
    *,
    batch_size: int = 5,
) -> Tuple[BuildingCheckpoint, Dict[str, int], List[PlacementTask]]:
    """チェックポイントを元に、次に取るべき調達・配置バッチとフェーズを決定する。"""

    normalized_layout = _normalize_layout(layout)
    procurement_plan = plan_material_procurement(requirements, inventory, checkpoint.reserved_materials)
    placement_plan, updated_count = plan_block_placement(normalized_layout, checkpoint.placed_blocks, batch_size=batch_size)
    next_phase = transition_phase(
        current_phase=checkpoint.phase,
        procurement_plan=procurement_plan,
        placement_plan=placement_plan,
        total_blocks=len(normalized_layout),
        placed_blocks=updated_count,
    )

    synchronized_reserved = _synchronize_reserved_materials(
        requirements=requirements,
        inventory=inventory,
        checkpoint_reserved=checkpoint.reserved_materials,
    )

    placed_blocks = checkpoint.placed_blocks
    if placement_plan:
        placed_blocks = updated_count
    elif next_phase == BuildingPhase.INSPECTION:
        placed_blocks = len(normalized_layout)

    updated_checkpoint = BuildingCheckpoint(
        phase=next_phase,
        reserved_materials=synchronized_reserved,
        placed_blocks=placed_blocks,
    )
    return updated_checkpoint, procurement_plan, placement_plan


def transition_phase(
    *,
    current_phase: BuildingPhase,
    procurement_plan: Mapping[str, int],
    placement_plan: Sequence[PlacementTask],
    total_blocks: int,
    placed_blocks: int,
) -> BuildingPhase:
    """建築フェーズの遷移判定。"""

    if procurement_plan:
        # 資材調達の不足がある間は PROCUREMENT を維持する。
        return BuildingPhase.PROCUREMENT

    if placement_plan:
        # 調達が完了している場合のみ配置へ進む。
        return BuildingPhase.PLACEMENT

    if total_blocks > 0 and placed_blocks >= total_blocks:
        # 全ブロックを配置済みなら検査フェーズへ。
        return BuildingPhase.INSPECTION

    if current_phase == BuildingPhase.INSPECTION:
        # 検査中は後戻りしない（ロールバックは rollback_building_state を利用）。
        return BuildingPhase.INSPECTION

    # 初期状態（SURVEY）か、配置が存在しない小規模案件。
    return BuildingPhase.SURVEY


def rollback_building_state(
    checkpoint: BuildingCheckpoint,
    *,
    failed_phase: Optional[BuildingPhase] = None,
    placements_attempted: Optional[Iterable[PlacementTask]] = None,
) -> BuildingCheckpoint:
    """失敗時に安全なフェーズまで巻き戻す。"""

    target_phase = failed_phase or checkpoint.phase
    try:
        current_index = _PHASE_SEQUENCE.index(target_phase)
    except ValueError:
        current_index = 0
    rollback_index = max(0, current_index - 1)
    new_phase = _PHASE_SEQUENCE[rollback_index]

    placements_reverted = checkpoint.placed_blocks
    if placements_attempted:
        attempted_count = len(list(placements_attempted))
        placements_reverted = max(0, checkpoint.placed_blocks - attempted_count)

    if new_phase == BuildingPhase.SURVEY:
        # SURVEY へ戻る際は再調達と再配置が必要になるため、カウンタと予約を初期化する。
        return BuildingCheckpoint(phase=new_phase)

    reserved = {
        key: value
        for key, value in checkpoint.reserved_materials.items()
        if value > 0
    }
    return BuildingCheckpoint(
        phase=new_phase,
        reserved_materials=reserved,
        placed_blocks=placements_reverted,
    )


def _normalize_layout(
    layout: Sequence[Union[PlacementTask, Mapping[str, object], Tuple[str, Iterable[int]]]],
) -> List[PlacementTask]:
    """多様なフォーマットで渡されるレイアウトを PlacementTask のリストへ揃える。"""

    normalized: List[PlacementTask] = []
    for entry in layout:
        if isinstance(entry, PlacementTask):
            normalized.append(entry)
            continue
        if isinstance(entry, Mapping):
            block = entry.get("block")
            coords = entry.get("coords")
        else:
            try:
                block, coords = entry  # type: ignore[misc]
            except (TypeError, ValueError):
                continue
        if not isinstance(block, str):
            continue
        coords_tuple = _as_coords_tuple(coords)
        if coords_tuple is None:
            continue
        normalized.append(PlacementTask(block=block, coords=coords_tuple))
    return normalized


def _as_coords_tuple(coords: object) -> Optional[Tuple[int, int, int]]:
    """座標表現を (x, y, z) のタプルへ変換する。"""

    if isinstance(coords, PlacementTask):
        return coords.coords

    if isinstance(coords, (list, tuple)):
        if len(coords) != 3:
            return None
        try:
            return int(coords[0]), int(coords[1]), int(coords[2])
        except (TypeError, ValueError):
            return None

    if isinstance(coords, Mapping):
        try:
            return int(coords.get("x")), int(coords.get("y")), int(coords.get("z"))
        except (TypeError, ValueError):
            return None

    return None


def _synchronize_reserved_materials(
    *,
    requirements: Mapping[str, int],
    inventory: Mapping[str, int],
    checkpoint_reserved: Mapping[str, int],
) -> Dict[str, int]:
    """要求量と手持ち・予約情報から次回再開時の予約状況を安定させる。"""

    synchronized: Dict[str, int] = {}
    for name, required in requirements.items():
        try:
            required_int = max(int(required), 0)
        except (TypeError, ValueError):
            continue
        existing = max(int(checkpoint_reserved.get(name, 0)), 0)
        available = max(int(inventory.get(name, 0)), 0)
        synchronized[name] = min(required_int, existing + available)
    return synchronized


__all__ = [
    "BuildingCheckpoint",
    "BuildingPhase",
    "PlacementTask",
    "advance_building_state",
    "checkpoint_to_dict",
    "plan_block_placement",
    "plan_material_procurement",
    "restore_checkpoint",
    "rollback_building_state",
    "transition_phase",
]
