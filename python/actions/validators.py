# -*- coding: utf-8 -*-
"""アクション実行時の入力値を検証するユーティリティ群。"""

from typing import Any, Dict, List, Optional, Sequence

from .errors import ActionValidationError


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


__all__ = ["_require_position", "_require_positions", "_require_non_empty_text"]
