# -*- coding: utf-8 -*-
"""アクションモジュール共通の例外定義。"""


class ActionValidationError(ValueError):
    """アクション呼び出し時の入力不備を明示する例外。"""


__all__ = ["ActionValidationError"]
