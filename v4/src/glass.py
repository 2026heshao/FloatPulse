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
    QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation,
)
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import QPushButton, QWidget

from src.constants import DEFAULT_THEME
from src import wallpaper as _wallpaper


# ====================================================================
# 壁纸图层（主窗玻璃壳与小卡片共用）
# ====================================================================
class SurfaceBackground:
    """自定义背景图图层：一张 QPixmap + 一组参数，负责把图按规矩画出来。

    为什么单独成一个小类而不是塞进调用方：主窗（GlassPanel）与小卡片
    （CardWindow）是两条互不相干的绘制管线，但「cover 怎么裁、tile 怎么铺、
    遮罩怎么叠」必须**只有一份实现**，否则两处必然慢慢长出不一致。

    调用顺序契约：本类**不自己裁剪** —— 宿主应先把 painter 裁到想要的
    形状（圆角矩形），再调 :meth:`paint`；这样难看的直角边缘不会出现。

    性能：缩放结果按 (尺寸, 模式, 模糊) 缓存，窗口 resize 之外不重算；
    模糊用「降采样再放大」的近似（真高斯核要走 QGraphicsBlurEffect +
    离屏渲染，静态背景图不值这个开销）。
    """

    def __init__(self):
        self._path = ""
        self._mode = _wallpaper.DEFAULT_MODE
        self._opacity = _wallpaper.DEFAULT_OPACITY
        self._blur = _wallpaper.DEFAULT_BLUR
        self._veil = _wallpaper.DEFAULT_VEIL
        self._veil_color = QColor("#FAFAF8")
        self._source = QPixmap()
        self._cache_key = None
        self._cache_pix = QPixmap()

    # ---------------- 配置 ----------------
    @property
    def enabled(self) -> bool:
        """是否真的有东西可画（没配图或图加载失败都算关）"""
        return not self._source.isNull()

    @property
    def veil(self) -> int:
        """当前遮罩百分比（宿主据此换算自己还要保留多少实底）"""
        return self._veil

    def configure(self, path: str = "", mode: str = "cover",
                  opacity: int = 100, blur: int = 0, veil: int = 82,
                  veil_color: str = "#FAFAF8") -> bool:
        """更新参数；返回「是否有实质变化」（宿主据此决定要不要 update()）。

        参数已由 `wallpaper.sanitize_spec` 收敛过；这里再兜一层是为防止
        UI 层绕过校验直接调。
        """
        spec = _wallpaper.sanitize_spec(
            name=path, mode=mode, opacity=opacity, blur=blur, veil=veil)

        new_color = QColor(veil_color)
        if not new_color.isValid():
            new_color = QColor("#FAFAF8")

        # 只在换图时才读盘解码：壁纸动辄数 MB，原实现每次 configure 都
        # QPixmap(path) 重解码一遍 —— 遮罩/模糊/不透明度的每个 ± 都会
        # 触发一次 configure，大图下步进器连点整段卡顿（2026-10-05）。
        # 路径未变时保留已解码位图与缩放缓存；解码失败时 _path 收敛成
        # ""，与请求路径必然不等，下次调用仍会照常重试。
        source_changed = False
        if path != self._path:
            try:
                src_pm = QPixmap(path) if (path and spec["name"]) else QPixmap()
            except Exception:          # 损坏的图片不该拖垮界面
                src_pm = QPixmap()
            new_src = path if not src_pm.isNull() else ""
            if new_src != self._path:
                source_changed = True
                self._path = new_src
                self._source = src_pm
                self._cache_key = None
                self._cache_pix = QPixmap()

        changed = (source_changed
                   or spec["mode"] != self._mode
                   or spec["opacity"] != self._opacity
                   or spec["blur"] != self._blur
                   or spec["veil"] != self._veil
                   or new_color.rgb() != self._veil_color.rgb())

        self._mode = spec["mode"]
        self._opacity = spec["opacity"]
        self._blur = spec["blur"]
        self._veil = spec["veil"]
        self._veil_color = new_color
        return bool(changed)

    # ---------------- 绘制 ----------------
    def paint(self, painter: QPainter, w: int, h: int) -> None:
        """把背景画到 ``(0,0,w,h)`` 区域（假定裁剪已由宿主设置好）"""
        if not self.enabled or w <= 0 or h <= 0:
            return
        pm = self._prepared(w, h)
        if pm.isNull():
            return

        painter.save()
        if self._opacity < 100:
            painter.setOpacity(painter.opacity() * self._opacity / 100.0)

        if self._mode == "tile":
            painter.drawTiledPixmap(0, 0, w, h, pm)
        elif self._mode == "center":
            painter.drawPixmap((w - pm.width()) // 2,
                               (h - pm.height()) // 2, pm)
        else:
            painter.drawPixmap(0, 0, w, h, pm)
        painter.restore()

        # 主题色遮罩：叠在图片之上、UI 文字之下，是「图片好看」与
        # 「文字看得清」之间的唯一调节点。
        if self._veil > 0:
            veil = QColor(self._veil_color)
            veil.setAlphaF(min(1.0, self._veil / 100.0))
            painter.fillRect(QRectF(0, 0, w, h), QBrush(veil))

    # ---------------- 内部 ----------------
    def _prepared(self, w: int, h: int) -> QPixmap:
        """按目标尺寸取（或现算）一张可直接 drawPixmap(0,0,w,h) 的图"""
        key = (w, h, self._mode, self._blur)
        if key == self._cache_key and not self._cache_pix.isNull():
            return self._cache_pix

        src = self._source
        pm = QPixmap()
        if not src.isNull() and w > 0 and h > 0:
            if self._mode in ("tile", "center"):
                pm = src
            else:
                if self._mode == "stretch":
                    ratio = Qt.AspectRatioMode.IgnoreAspectRatio
                elif self._mode == "contain":
                    ratio = Qt.AspectRatioMode.KeepAspectRatio
                else:  # cover
                    ratio = Qt.AspectRatioMode.KeepAspectRatioByExpanding
                pm = src.scaled(QSize(w, h), ratio,
                                Qt.TransformationMode.SmoothTransformation)
                pm = self._apply_blur(pm)
                if self._mode == "cover" and (pm.width() > w
                                              or pm.height() > h):
                    pm = pm.copy(max(0, (pm.width() - w) // 2),
                                 max(0, (pm.height() - h) // 2), w, h)

        self._cache_key = key
        self._cache_pix = pm
        return pm

    def _apply_blur(self, pm: QPixmap) -> QPixmap:
        """降采样→放大的近似模糊（走两趟，比单趟更接近真高斯）。

        factor 随模糊强度线性放大；尺寸的地板是 1px，防止超大模糊值把
        图缩成 0 宽导致 QPixmap 变 null（那会让背景直接消失）。
        """
        if self._blur <= 0 or pm.isNull():
            return pm
        target = pm.size()
        factor = max(2, self._blur * 2)
        for divisor in (factor, max(2, factor // 2)):
            dw = max(1, target.width() // divisor)
            dh = max(1, target.height() // divisor)
            if dw >= target.width() or dh >= target.height():
                continue
            pm = (pm.scaled(QSize(dw, dh),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
                    .scaled(target,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))
        return pm


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
    # 高光最强处的不透明度（0-1）。
    # UI 重构 01（2026-10-02）：玻璃拟态数值退役 —— 归零后高光带/噪点不再
    # 可见，视觉等同实底。主题里的 glass_highlight / noise_alpha 是无消费
    # 者的死键，真正的开关是这两个类常量（theme.py 同款注释）；等 03/05
    # 包清完调用点再决定是否连绘制代码一起移除。
    HIGHLIGHT_ALPHA = 0.0
    # 噪点整体浓淡系数（0-1）：同上退役归零
    NOISE_OPACITY = 0.0

    def __init__(self, parent=None, radius: float = 14.0, noise: bool = True):
        super().__init__(parent)
        self._radius = float(radius)
        self._noise_enabled = noise
        self._fill = QColor(255, 255, 255, 148)
        self._edge_top = QColor(255, 255, 255, 224)
        self._edge_bottom = QColor(16, 32, 48, 20)
        self._noise = get_noise_pixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # 自定义背景图图层（默认空，未配置时 paint 开销 = 一次布尔判断）
        self._background = SurfaceBackground()

    # ---------------- 主题 ----------------
    def apply_theme(self, colors: dict):
        """按主题色字典刷新填充/描边颜色"""
        self._fill = _to_color(colors.get("glass_fill", "rgba(255,255,255,148)"))
        self._edge_top = _to_color(colors.get("glass_edge", "rgba(255,255,255,224)"))
        self._edge_bottom = _to_color(
            colors.get("glass_edge_bottom", "rgba(16,32,48,20)"))
        self.update()

    def set_background(self, path: str = "", mode: str = "cover",
                       opacity: int = 100, blur: int = 0, veil: int = 82,
                       veil_color: str = "#FAFAF8") -> bool:
        """设置/关闭自定义背景图；返回是否发生实质变化（变了才 update()）。

        ``path`` 传空串或加载失败的文件 = 关闭壁纸，其它参数此时无意义。
        """
        changed = self._background.configure(
            path, mode, opacity, blur, veil, veil_color)
        if changed:
            self.update()
        return changed

    def background_enabled(self) -> bool:
        """当前是否真的挂着一张壁纸（宿主据此决定是否把自己变成半透明）"""
        return self._background.enabled

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

        # --- 2.5 层：自定义背景图（图片 + 主题色遮罩）---
        # 排在实底填充**之后**、高光之前：实底永远是不透明兜底（图片丢了
        # 或半透明也不至于露出桌面），遮罩则在图片之上压出可读性。
        self._background.paint(painter, w, h)

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


class NavArrow(QWidget):
    """侧栏组标题左侧的折叠箭头：自绘三角 + **旋转动画**（0° 指向右 → 90° 指向下）。

    为什么自绘而不放字符（2026-09-29 实测）：
      · 几何符号 ▼/▶ 在中文字体（msyh）里**部分缺字形** —— 实测
        QRawFont.supportsCharacter('▶') 为 False，只能靠系统字体回退，
        回退不到就是长期挂在侧栏上的"豆腐块"方框；
      · 字符宽度随字体/字号变化，折叠与展开之间切换会让组标题行的
        sizeHint 抖动；自绘固定 8×8，完全不参与字体度量；
      · 真正想要的是"箭头转下去"这个动作 —— 字符做不到。
    """

    SIZE = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0.0
        self._color = QColor(140, 148, 166)
        self._color_normal = QColor(140, 148, 166)
        self._color_hover = QColor(140, 148, 166)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, value):
        self._angle = float(value)
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    @property
    def angle(self) -> float:
        return self._angle

    def set_angle(self, angle: float, animate: bool = True, duration: int = 240):
        """转到指定角度（度）。animate=False 或时长 ≤0 时直接落位。"""
        self._anim.stop()
        if not animate or duration <= 0:
            self._angle = float(angle)
            self.update()
            return
        self._anim.setDuration(int(duration))
        self._anim.setStartValue(float(self._angle))
        self._anim.setEndValue(float(angle))
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.rotate(self._angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        # 指向右的三角，绕中心旋转
        painter.drawPolygon(QPolygonF([
            QPointF(-2.2, -3.4), QPointF(3.2, 0.0), QPointF(-2.2, 3.4),
        ]))
        painter.end()


class NavGroupHeader(QPushButton):
    """侧栏分组标题按钮：左侧自绘箭头 + 标题文案（点击切换该组展开/折叠）。

    箭头是**子控件**而非文本里的字符，所以标题文案里不再带 ▼/▶；
    文本左侧的留白由 QSS 的 ``padding-left`` 提供（见 theme.py）。
    """

    ARROW_X = 10          # 箭头左边缘（与 QSS padding-left 对齐）

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.arrow = NavArrow(self)
        self._expanded = False
        self._expanded_angle = 90.0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # QSS 的上下 margin 对称（4px），所以几何中心即视觉中心
        self.arrow.move(self.ARROW_X,
                        (self.height() - self.arrow.height()) // 2)

    def set_arrow_color(self, color, hover_color=None):
        """箭头配色（主题切换时同步）；hover_color 为空则鼠标悬停不换色"""
        self.arrow.set_color(color)
        self.arrow._color_normal = QColor(color)
        self.arrow._color_hover = (QColor(hover_color)
                                   if hover_color is not None else QColor(color))

    def enterEvent(self, event):
        self.arrow.set_color(self.arrow._color_hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.arrow.set_color(self.arrow._color_normal)
        super().leaveEvent(event)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True,
                     duration: int = 240):
        self._expanded = bool(expanded)
        self.arrow.set_angle(self._expanded_angle if self._expanded else 0.0,
                             animate=animate, duration=duration)
