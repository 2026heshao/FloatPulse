# -*- coding: utf-8 -*-
"""
====================================================================
图标渲染（Qt 层）  -  icon_render
====================================================================
把 :mod:`src.icons` 里的路径数据画成 QPainterPath / QPixmap / QIcon。

为什么与 :mod:`src.icons` 分成两个文件
=======================================
``icons.py`` 是**纯数据 + 纯解析**，零 PyQt6 依赖 —— 这样 pytest 能在
没有 QApplication 的环境里直接断言全部图标数据（路径可解析、名称唯一、
覆盖清单完整），毫秒级、不依赖离屏平台。渲染才是真正需要 Qt 的部分，
单独放这里（与 ``nav_layout`` 纯逻辑 / ``main_window`` 负责渲染是同款切法）。

QPainterPath 不解析 path d 字符串
=================================
这是本方案唯一的技术坑：``QPainterPath`` 没有 ``fromString`` 这类 API
（解析 SVG 路径本来就得靠 QtSvg，而打包产物里没有 QtSvg.dll）。
所以 ``icons.parse_path`` 先把 d 字符串拆成 ``[(命令, 参数)]``，
这里再逐条执行成 QPainterPath —— 解析与执行分开，解析那一半因此
可以被纯 pytest 覆盖。

缓存
====
两组缓存，都是模块级 dict：
  · ``_PATH_CACHE``   名称 → QPainterPath（路径与主题无关，永不失效）
  · ``_PIXMAP_CACHE`` (名称, 尺寸, 颜色, dpr) → QPixmap

QPixmap 缓存键带颜色与 dpr，是因为**同一个图标在不同主题/不同选中态下
颜色不同**，它们必须各自成像；不带颜色缓存的话切主题后图标会停留在
旧配色上（这正是本项目在碎片列表上栽过的坑）。
====================================================================
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)

from src.icons import ICON_PATHS, ICON_SIZE, ICON_FILLED, parse_path


# 默认笔宽（**目标像素**，不是 24 视框里的值）
DEFAULT_STROKE = 1.7

_PATH_CACHE = {}
_PIXMAP_CACHE = {}


# ====================================================================
# 路径构建
# ====================================================================
def _build_path(cmds) -> QPainterPath:
    """``[(命令, 参数)]`` → QPainterPath（支持 M/L/H/V/C/Q/Z，大小写皆可）。"""
    path = QPainterPath()
    cx = cy = 0.0          # 当前点
    sx = sy = 0.0          # 当前子路径起点（Z 回这里）
    for cmd, args in cmds:
        upper = cmd.upper()
        rel = cmd.islower()
        if upper == "M":
            x = (cx + args[0]) if rel else args[0]
            y = (cy + args[1]) if rel else args[1]
            path.moveTo(x, y)
            cx, cy = x, y
            sx, sy = x, y
        elif upper == "L":
            x = (cx + args[0]) if rel else args[0]
            y = (cy + args[1]) if rel else args[1]
            path.lineTo(x, y)
            cx, cy = x, y
        elif upper == "H":
            x = (cx + args[0]) if rel else args[0]
            path.lineTo(x, cy)
            cx = x
        elif upper == "V":
            y = (cy + args[0]) if rel else args[0]
            path.lineTo(cx, y)
            cy = y
        elif upper == "C":
            if rel:
                pts = [(cx + args[0], cy + args[1]),
                       (cx + args[2], cy + args[3]),
                       (cx + args[4], cy + args[5])]
            else:
                pts = [(args[0], args[1]), (args[2], args[3]), (args[4], args[5])]
            path.cubicTo(pts[0][0], pts[0][1], pts[1][0], pts[1][1],
                         pts[2][0], pts[2][1])
            cx, cy = pts[2]
        elif upper == "Q":
            if rel:
                pts = [(cx + args[0], cy + args[1]), (cx + args[2], cy + args[3])]
            else:
                pts = [(args[0], args[1]), (args[2], args[3])]
            path.quadTo(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
            cx, cy = pts[1]
        elif upper == "Z":
            path.closeSubpath()
            cx, cy = sx, sy
    return path


def icon_path(name: str) -> QPainterPath:
    """取图标的 QPainterPath（按名称缓存；未登记的名称抛 KeyError）。"""
    cached = _PATH_CACHE.get(name)
    if cached is not None:
        return cached
    d = ICON_PATHS.get(name)
    if d is None:
        raise KeyError("未登记的图标：%r（可用：%s）"
                       % (name, ", ".join(ICON_PATHS)))
    path = _build_path(parse_path(d))
    _PATH_CACHE[name] = path
    return path


# ====================================================================
# 绘制
# ====================================================================
def paint_icon(painter, name: str, rect, color, width: float = DEFAULT_STROKE):
    """把图标画进 ``painter`` 的 ``rect``（取内接正方形并居中）。

    ``width`` 是**目标像素**下的笔宽 —— 内部按缩放比换算到 24 视框，
    因此同一图标在 16px 与 48px 下的视觉粗细一致（若不换算，48px 下
    描边会细得几乎看不见）。

    填充型图标（``icons.ICON_FILLED``，如 ⋯ 三点）用画刷填充而非描边：
    零长度/极小路径在描边管线里要么整个跳过要么成环孔，只有实心成立。
    """
    path = icon_path(name)
    r = QRectF(rect)
    side = min(r.width(), r.height())
    if side <= 0:
        return
    scale = side / float(ICON_SIZE)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(r.x() + (r.width() - side) / 2.0,
                      r.y() + (r.height() - side) / 2.0)
    painter.scale(scale, scale)
    if name in ICON_FILLED:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
    else:
        pen = QPen(QColor(color))
        pen.setWidthF(max(0.01, float(width)) / scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def current_dpr() -> float:
    """当前屏幕的设备像素比（无 QGuiApplication / 无屏幕时安全返回 1.0）。

    用 ``QGuiApplication`` 而不是 ``QApplication``：设备像素比只跟屏幕有关，
    取它不必把 QtWidgets 拖进本模块的依赖里（QtGui 就够了）。
    """
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    try:
        return float(screen.devicePixelRatio()) or 1.0
    except Exception:
        return 1.0


def _color_key(color) -> str:
    """归一化颜色缓存键（同一颜色的 QColor / "#RRGGBB" / "rgba(...)" 写法统一）。"""
    return QColor(color).name(QColor.NameFormat.HexArgb)


def icon_pixmap(name: str, size: int, color, dpr=None, width: float = DEFAULT_STROKE) -> QPixmap:
    """渲染成 QPixmap（按 名称+尺寸+颜色+dpr 缓存）。

    ``size`` 是**逻辑像素**；实际位图按 ``size × dpr`` 开，再
    ``setDevicePixelRatio``，所以在 125%/150% 缩放下图标是清晰的，
    而不是被 Qt 放大糊掉。
    """
    if dpr is None:
        dpr = current_dpr()
    try:
        dpr = float(dpr) or 1.0
    except (TypeError, ValueError):
        dpr = 1.0
    size = max(1, int(size))
    key = (name, size, _color_key(color), round(dpr, 3), round(float(width), 3))
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    paint_icon(painter, name, QRectF(0.0, 0.0, float(px), float(px)),
               color, width * dpr)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    _PIXMAP_CACHE[key] = pm
    return pm


def icon(name: str, size: int, color, on_color=None,
         disabled_color=None, dpr=None, width: float = DEFAULT_STROKE) -> QIcon:
    """构造 QIcon：Off 态用 ``color``，On 态用 ``on_color``，Disabled 态用
    ``disabled_color``。

    ★ On 态是给 ``setCheckable(True)`` 的按钮准备的：Qt 在按钮选中时取
    ``QIcon.State.On`` 的 pixmap 绘制。导航按钮"选中变主色"因此**不需要
    额外接 toggled 信号重设图标** —— setIcon 一次就够，切页高亮与图标
    变色天然同步（少一类"信号漏接导致图标不变色"的静默缺陷）。

    Disabled 态（P1）：Qt 对缺 Disabled 位图的图标会自动生成"褪色"版本，
    但生成算法不受主题控制；显式提供（``text_disabled``）让 Stepper 这类
    到边界置灰的按钮与 QSS 的 ``:disabled`` 文字色保持同一灰度。
    """
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, size, color, dpr, width),
                 QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(icon_pixmap(name, size, on_color or color, dpr, width),
                 QIcon.Mode.Normal, QIcon.State.On)
    if disabled_color is not None:
        ic.addPixmap(icon_pixmap(name, size, disabled_color, dpr, width),
                     QIcon.Mode.Disabled)
    return ic


def clear_cache():
    """清空缓存（测试用；正常运行时不需要调）。"""
    _PATH_CACHE.clear()
    _PIXMAP_CACHE.clear()
