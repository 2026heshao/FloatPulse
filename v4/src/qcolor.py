# -*- coding: utf-8 -*-
"""主题色值 → QColor 的安全转换（全站共享，2026-10-07 V 批抽出）。

背景：主题 token 里两种形态并存 —— ``#RRGGBB`` 实色与 ``rgba(r, g, b, a)``
半透明（``panel_fill`` / ``panel_edge`` / ``primary_a30`` 等）。PyQt6 的
``QColor(str)`` **不认**这种 rgba 串（直接解析失败返回无效黑），自绘控件
取主题色必须走本转换器；glass.py 有同语义的 ``_to_color``（历史实现，
因 controls↔glass 导入环禁令不能反向引用，故独立成最小模块）。

alpha 双约定兼容（与 glass._to_color 同口径）：
  · ``rgba(r, g, b, 0.30)`` 浮点分数（0~1）
  · ``rgba(r, g, b, 44)``   0-255 整数（Qt QSS 惯例，>1 即按此读）
====================================================================
"""

import re

from PyQt6.QtGui import QColor

_RGBA_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)


def qcolor(value, fallback: str = "#000000") -> QColor:
    """把主题色值（QColor / #RRGGBB / rgba(...) / 命名色）安全转 QColor。"""
    if isinstance(value, QColor):
        return QColor(value)
    text = str(value).strip()
    direct = QColor(text)
    if direct.isValid():
        return direct
    m = _RGBA_RE.match(text)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            r = int(float(parts[0]))
            g = int(float(parts[1]))
            b = int(float(parts[2]))
            a = float(parts[3]) if len(parts) > 3 else 1.0
            if a <= 1.0:
                a = a * 255.0
            return QColor(r, g, b, int(round(a)))
        except (ValueError, IndexError):
            pass
    fb = QColor(fallback)
    return fb if fb.isValid() else QColor("#000000")
