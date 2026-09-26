# -*- coding: utf-8 -*-
"""
====================================================================
玻璃壳组件  -  glass
====================================================================
把「高级透明玻璃」拆成可复用的 6 层叠加，供大窗口与小卡片共用：

  1. 窗口外圈柔和阴影（多层同心圆角矩形，越外越淡）
  2. 容器填充（半透明，露出桌面）
  3. 顶部高光带（上 32% 高，白色线性渐变到透明 —— 玻璃的"反光"）
  4. 1px 内描边（上亮下暗，模拟玻璃厚度）
  5. 内嵌面板由 QSS 负责（见 theme.py 的 panel 相关规则）
  6. 噪点覆盖（3~5% 灰度噪声，消除"塑料感"，是质感的关键一层）

设计要点：
  - 全部在 paintEvent 里用 QPainter 手绘，不使用 QGraphicsDropShadowEffect
    （后者在半透明窗口上会走离屏渲染，拖动时明显掉帧）
  - 噪点纹理只生成一次并缓存，避免每帧重新构造像素
  - 主题切换只需调用 apply_theme(colors)，不做重建
====================================================================
"""

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation,
)
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import QWidget

from src.constants import DEFAULT_THEME


# ====================================================================
# 噪点纹理：只生成一次，全局缓存
# ====================================================================
_NOISE_CACHE = {}


def get_noise_pixmap(size: int = 128, alpha: int = 10) -> QPixmap:
    """生成（或取出缓存的）灰度噪点纹理。

    alpha 为噪点最大不透明度（0-255）。调用方用 QPainter.setOpacity
    再叠一层系数即可控制浓淡，无需为每个透明度单独生成。
    """
    key = (size, alpha)
    cached = _NOISE_CACHE.get(key)
    if cached is not None:
        return cached

    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    # 用确定性的伪随机序列生成，保证每次运行纹理一致（便于视觉回归对比）
    seed = 0x9E3779B9
    painter = QPainter(pm)
    for y in range(size):
        for x in range(size):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            v = (seed >> 16) & 0xFF
            if v % 3:                      # 只画约 1/3 的点，噪点更细碎
                continue
            shade = 255 if (v & 8) else 0
            painter.setPen(QColor(shade, shade, shade, alpha))
            painter.drawPoint(x, y)
    painter.end()

    _NOISE_CACHE[key] = pm
    return pm


def draw_soft_shadow(painter: QPainter, rect: QRectF, radius: float,
                     layers: int = 6, max_alpha: int = 48, offset_y: float = 4.0):
    """在 rect 周围画多层柔和阴影（替代 QGraphicsDropShadowEffect）。

    层数越少越省，越外圈越淡；offset_y 制造"光源在上"的自然投影。
    """
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(layers, 0, -1):
        t = i / layers                      # t→1 为最外圈
        expand = 1.0 + (radius * 0.8) * t
        alpha = int(max_alpha * (1.0 - t) ** 1.4)
        if alpha <= 0:
            continue
        r = rect.adjusted(-expand, -expand + offset_y, expand, expand + offset_y)
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawRoundedRect(r, radius + expand * 0.35, radius + expand * 0.35)
    painter.restore()


class GlassPanel(QWidget):
    """透明玻璃壳容器：负责填充 / 顶部高光 / 双色描边 / 噪点。

    用法：
        panel = GlassPanel(radius=14)
        panel.apply_theme(colors)      # colors 来自 theme.get_colors()
    子控件照常 addWidget，QSS 只负责子控件的样式，不要给本控件设
    background-color，否则会盖住这里画的高光与噪点。
    """

    # 顶部高光带占整体高度的比例（与效果图一致）
    HIGHLIGHT_RATIO = 0.28
    # 高光最强处的不透明度（0-1）
    HIGHLIGHT_ALPHA = 0.50
    # 噪点整体浓淡系数（0-1），对应效果图的 3~5%（过重会显脏，宁轻勿重）
    NOISE_OPACITY = 0.22

    def __init__(self, parent=None, radius: float = 14.0, noise: bool = True):
        super().__init__(parent)
        self._radius = float(radius)
        self._noise_enabled = noise
        self._fill = QColor(255, 255, 255, 148)
        self._edge_top = QColor(255, 255, 255, 224)
        self._edge_bottom = QColor(16, 32, 48, 20)
        self._noise = get_noise_pixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    # ---------------- 主题 ----------------
    def apply_theme(self, colors: dict):
        """按主题色字典刷新填充/描边颜色"""
        self._fill = _to_color(colors.get("glass_fill", "rgba(255,255,255,148)"))
        self._edge_top = _to_color(colors.get("glass_edge", "rgba(255,255,255,224)"))
        self._edge_bottom = _to_color(
            colors.get("glass_edge_bottom", "rgba(16,32,48,20)"))
        self.update()

    def set_radius(self, radius: float):
        self._radius = float(radius)
        self.update()

    def radius(self) -> float:
        return self._radius

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_glass(painter, self.width(), self.height())
        painter.end()

    def _paint_glass(self, painter: QPainter, w: int, h: int):
        """真正的绘制实现（坐标系为本控件的 0,0 起）"""
        if w <= 0 or h <= 0:
            return
        r = self._radius
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)

        # --- 2 层：容器填充 ---
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._fill))
        painter.drawPath(path)

        # 后续高光/噪点都裁剪在圆角内
        painter.save()
        painter.setClipPath(path)

        # --- 3 层：顶部高光带 ---
        band_h = h * self.HIGHLIGHT_RATIO
        grad = QLinearGradient(QPointF(0, 0), QPointF(0, band_h))
        top = QColor(255, 255, 255, int(255 * self.HIGHLIGHT_ALPHA))
        end = QColor(255, 255, 255, 0)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, end)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(0, 0, w, band_h))

        # --- 6 层：噪点 ---
        if self._noise_enabled and not self._noise.isNull():
            painter.setOpacity(self.NOISE_OPACITY)
            painter.drawTiledPixmap(0, 0, w, h, self._noise)
            painter.setOpacity(1.0)

        painter.restore()

        # --- 4 层：1px 内描边（上亮下暗）---
        edge_grad = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        edge_grad.setColorAt(0.0, self._edge_top)
        edge_grad.setColorAt(1.0, self._edge_bottom)
        pen = painter.pen()
        pen.setBrush(QBrush(edge_grad))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, r, r)


def _to_color(value) -> QColor:
    """把 QSS 风格的颜色字符串转成 QColor（兼容 #RRGGBB 与 rgba(...)）。"""
    if isinstance(value, QColor):
        return QColor(value)
    text = str(value).strip()
    if text.startswith("rgba") or text.startswith("rgb"):
        try:
            inner = text[text.index("(") + 1: text.rindex(")")]
            parts = [p.strip() for p in inner.replace("%", "").split(",")]
            r, g, b = (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
            if len(parts) > 3:
                a = parts[3]
                a = float(a)
                if a <= 1.0:
                    a = a * 255.0
                alpha = int(a)
            else:
                alpha = 255
            return QColor(r, g, b, alpha)
        except (ValueError, IndexError):
            return QColor(255, 255, 255, 148)
    c = QColor(text)
    return c if c.isValid() else QColor(255, 255, 255, 148)


class GlassWindowRoot(QWidget):
    """带外圈柔和阴影的窗口根容器。

    窗口本身设 WA_TranslucentBackground，这里负责在容器外围画阴影；
    阴影参数随主题调整（深色主题下阴影更重）。
    """

    def __init__(self, parent=None, margin: int = 18, radius: float = 14.0):
        super().__init__(parent)
        self._margin = int(margin)
        self._radius = float(radius)
        self._shadow_alpha = 48
        self._shadow_offset = 4.0

    def apply_theme(self, colors: dict):
        theme_name = colors.get("_name", DEFAULT_THEME)
        if theme_name == "dark":
            self._shadow_alpha = 96
            self._shadow_offset = 6.0
        else:
            self._shadow_alpha = 48
            self._shadow_offset = 4.0
        self.update()

    def set_radius(self, radius: float):
        self._radius = float(radius)
        self.update()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        m = self._margin
        inner = QRectF(m, m, w - 2 * m, h - 2 * m)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_soft_shadow(painter, inner, self._radius,
                         layers=6, max_alpha=self._shadow_alpha,
                         offset_y=self._shadow_offset)
        painter.end()


# ====================================================================
# 选中项指示条（导航 / Tab 通用）
# ====================================================================
class NavIndicator(QWidget):
    """3px 圆角竖条：跟随选中项平滑滑动。

    - move_to_y(y)  带动画滑动（260ms OutQuint，接近 iOS 的手感）
    - snap_to_y(y)  直接落位（窗口首次显示时用，避免从 0 滑下来的怪动画）
    """

    DEFAULT_W = 3
    DEFAULT_H = 22

    def __init__(self, parent=None, width: int = 3, height: int = 22):
        super().__init__(parent)
        self._color = QColor(91, 192, 190)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._ready = False

    def _on_anim_value(self, value):
        self.move(self.x(), int(round(float(value))))

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def move_to_y(self, y: float):
        """平滑滑动到目标 Y（相对父控件）"""
        if not self._ready:
            self.snap_to_y(y)
            return
        self._anim.stop()
        self._anim.setStartValue(float(self.y()))
        self._anim.setEndValue(float(y))
        self._anim.start()
        self.update()

    def snap_to_y(self, y: float):
        """无动画直接落位"""
        self._anim.stop()
        self.move(self.x(), int(round(float(y))))
        self._ready = True
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawPath(path)
        painter.end()
