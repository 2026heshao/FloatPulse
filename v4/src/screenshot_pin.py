# -*- coding: utf-8 -*-
"""
====================================================================
截图钉屏模块  -  ScreenshotPinController / SnipOverlay / PinWindow
====================================================================
流程：全局热键（Ctrl+Alt+S）或悬浮球右键菜单
     → SnipOverlay（主屏快照 + 暗遮罩 + 十字光标拖选）
     → 裁剪选区 → PinWindow（无边框置顶参考浮窗）

设计要点：
  1. 主屏截图：QScreen.grabWindow(0) 返回的 QPixmap 自带 devicePixelRatio，
     全程使用**逻辑坐标**运算，落 pixmap 源矩形时乘 dpr（本机 125% DPI 实测）。
     MVP 仅支持主屏。
  2. ESC 拦截：主窗口注册了 ApplicationShortcut 级别的全局 Esc（退出程序）。
     覆盖层 / 钉图窗口必须拦截 QEvent.ShortcutOverride 并 accept，
     否则按 Esc 取消截图会误触发「退出整个程序」。
  3. 拖选小于 12×12 逻辑像素视为误触，直接取消不钉。
  4. 钉图窗口 Tool 层级（不占任务栏）、WA_ShowWithoutActivating 不抢焦点。
     视口模型：滚轮=内容缩放（光标锚点，窗框不动）；
     右下角抓手拖拽=窗框等比例缩放（内容铺满）；
     批注（画笔/箭头/马赛克，Ctrl+Z 撤销）钉在 base 坐标不受视口影响；
     左键拖动 / 双击关闭 / 右键菜单（复制/保存含批注合成）。
  5. 控制器负责去重（截图进行中忽略重复触发）与钉图生命周期管理。
====================================================================
"""

from PyQt6.QtWidgets import QApplication, QWidget, QMenu, QFileDialog
from PyQt6.QtCore import (
    Qt, QEvent, QRect, QRectF, QPoint, QObject, QPointF,
    pyqtSignal, QPropertyAnimation, QAbstractAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QPen, QFont, QAction, QGuiApplication,
    QImage, QIcon, QBrush,
)
import math

from src.logger import get_logger
from src.controls import ScreenToast
from src.theme import FALLBACK_ACCENT, get_colors, get_menu_qss

# 选区最小边长（逻辑像素），小于该值视为误触
_MIN_SELECTION = 12


# ====================================================================
# 截图覆盖层：全屏快照 + 遮罩 + 拖选
# ====================================================================
class SnipOverlay(QWidget):
    """全屏截图覆盖层。拖选完成发出 region_selected(裁剪pixmap, 全局坐标)"""

    region_selected = pyqtSignal(QPixmap, QPoint)

    def __init__(self, shot: QPixmap, screen_geo: QRect, theme: str):
        super().__init__(None)
        self._shot = shot
        self._theme = theme
        self._origin = None       # 拖选起点（本地逻辑坐标）
        self._pos = None          # 拖选当前点
        self._dpr = shot.devicePixelRatio() or 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen_geo)
        self._accent = QColor(get_colors(theme).get("primary", FALLBACK_ACCENT))

    # ---------------- 事件 ----------------
    def event(self, e):
        # 拦截全局 Esc 快捷键（ApplicationShortcut）：取消截图而非退出程序
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            get_logger().info("[截图] Esc 取消截图")
            self.close()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position().toPoint()
            self._pos = self._origin
            self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            get_logger().info("[截图] 右键取消截图")
            self.close()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._pos = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        sel = QRect(self._origin, e.position().toPoint()).normalized()
        if sel.width() < _MIN_SELECTION or sel.height() < _MIN_SELECTION:
            get_logger().info("[截图] 选区过小，视为误触取消")
            self.close()
            return
        # 逻辑坐标 → 设备像素源矩形
        dpr = self._dpr
        src = QRect(
            round(sel.x() * dpr), round(sel.y() * dpr),
            round(sel.width() * dpr), round(sel.height() * dpr),
        )
        pix = self._shot.copy(src)
        pix.setDevicePixelRatio(dpr)
        global_pos = self.mapToGlobal(sel.topLeft())
        get_logger().info(
            f"[截图] 选区 {sel.width()}x{sel.height()} @ {global_pos.x()},{global_pos.y()}"
        )
        self.region_selected.emit(pix, global_pos)
        self.close()

    # ---------------- 绘制 ----------------
    def _selection(self) -> QRect:
        if self._origin is None or self._pos is None:
            return QRect()
        # 与拖拽方向无关：取两角包围盒（QRect(p1,p2) 的右下角是含端点语义，
        # 直接 normalized() 会让「反向拖」比「正向拖」差 1px 的裁剪范围）
        return QRect(QPoint(min(self._origin.x(), self._pos.x()),
                            min(self._origin.y(), self._pos.y())),
                     QPoint(max(self._origin.x(), self._pos.x()),
                            max(self._origin.y(), self._pos.y()))).normalized()

    def paintEvent(self, _event):
        p = QPainter(self)
        # 1. 屏幕快照（QPixmap 自带 dpr，drawPixmap 按逻辑尺寸铺开）
        p.drawPixmap(0, 0, self._shot)
        # 2. 全屏暗遮罩
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        sel = self._selection()
        if not sel.isNull() and sel.width() >= 1 and sel.height() >= 1:
            # 3. 选区恢复原色（源矩形乘 dpr）
            dpr = self._dpr
            src = QRectF(
                sel.x() * dpr, sel.y() * dpr,
                sel.width() * dpr, sel.height() * dpr,
            )
            p.drawPixmap(QRectF(sel), self._shot, src)
            # 4. 选区边框
            p.setPen(QPen(self._accent, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sel)
            # 5. 尺寸提示（选区内右下角）
            label = f"{sel.width()} x {sel.height()}"
            font = QFont("Microsoft YaHei", 10)
            font.setBold(False)
            p.setFont(font)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label) + 16
            th = fm.height() + 8
            tip = QRect(sel.right() - tw - 6, sel.bottom() - th - 6, tw, th)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(tip, 4, 4)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(tip, Qt.AlignmentFlag.AlignCenter, label)
        else:
            # 6. 未拖选时顶部操作提示条
            text = "拖选要钉住的范围，松开即钉屏 · Esc 或右键取消"
            font = QFont("Microsoft YaHei", 10)
            p.setFont(font)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(text) + 36
            h = fm.height() + 16
            bar = QRect((self.width() - w) // 2, 28, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(bar, 8, 8)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(bar, Qt.AlignmentFlag.AlignCenter, text)


# ====================================================================
# 钉图浮窗：置顶参考窗（视口窗框 + 内容缩放 + 批注）
# ====================================================================
class PinWindow(QWidget):
    """钉在屏幕上的截图浮窗。两个独立维度：

    - 视口（窗框）：右下角抓手等比例拖动改变窗框大小，内容自动铺满
    - 内容（图像）：滚轮缩放（0.15×–5×），窗框尺寸不动，以**鼠标位置为锚点**，
      放大即"指哪看哪"（自带平移）；超出窗框部分裁剪
    - 批注：画笔 / 箭头 / 马赛克画刷，批注层在 base 逻辑坐标系，
      缩放/拖动/改窗框不影响批注锚点；复制/保存时合成批注。Ctrl+Z 撤销
    - 左键拖动=移动窗口 / 双击关闭 / 右键菜单
    关闭时发出 closed(self)。
    """

    closed = pyqtSignal(object)

    # 内容缩放参数（相对 base 逻辑尺寸）
    _ZOOM_STEP = 1.25      # 每格滚轮缩放系数
    _ZOOM_MIN = 0.15
    _ZOOM_MAX = 5.0
    _PAN_MARGIN = 24       # 平移夹紧：窗框内至少保留 24px 图像交集

    # 视口抓手（右下角等比例拖拽改窗框）
    _GRIP = 18             # 抓手热区边长（逻辑 px）
    _VIEW_MIN = 60         # 窗框最小边长

    # 批注参数（base 逻辑坐标系，与缩放无关）
    _PEN_WIDTH = 3
    _MOSAIC_RADIUS = 11    # 马赛克画刷半径（逻辑 px）
    _MOSAIC_CELL = 10      # 马赛克格子尺寸（设备 px）
    _UNDO_MAX = 20
    _ANNOT_COLORS = (("红", "#FF5252"), ("黄", "#FFD740"),
                     ("蓝", "#40C4FF"), ("白", "#FFFFFF"))

    def __init__(self, pixmap: QPixmap, global_pos: QPoint, theme: str):
        super().__init__(None)
        self._pix = pixmap
        self._theme = theme
        self._drag_offset = None
        self._accent = QColor(get_colors(theme).get("primary", FALLBACK_ACCENT))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(False)

        dpr = pixmap.devicePixelRatio() or 1.0
        # QPixmap.size() 是设备像素，显示尺寸 = /dpr（逻辑坐标）
        disp_w = max(1, round(pixmap.width() / dpr))
        disp_h = max(1, round(pixmap.height() / dpr))
        self._base_w = disp_w          # 图像基准逻辑尺寸（zoom=1.0）
        self._base_h = disp_h
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)  # 图像左上角在窗口内的位置（视口平移）
        self._resizing = False         # 抓手拖拽改窗框进行中
        self._resize_start = None      # (全局起点, start_w, start_h)

        # 批注层：与底图同尺寸的透明 QImage（device px + dpr），绘制用 base 逻辑坐标
        self._annot = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self._annot.setDevicePixelRatio(dpr)
        self._annot.fill(Qt.GlobalColor.transparent)
        self._undo_stack = []          # QImage 快照栈（撤销）
        self._tool = None              # None / "pen" / "arrow" / "mosaic"
        self._annot_color = QColor(self._ANNOT_COLORS[0][1])
        self._drawing = False
        self._stroke_last = None       # 画笔上一落点
        self._mosaic_last = None       # 马赛克上一落点
        self._pending = None           # 箭头预览 (start, end)（base 逻辑坐标）

        self.setFixedSize(disp_w, disp_h)
        self.move(global_pos)

        # 渐入动画（130ms，结束自动销毁）
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(130)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    # ---------------- 主题 ----------------
    def apply_theme(self, theme: str):
        self._theme = theme
        self._accent = QColor(get_colors(theme).get("primary", FALLBACK_ACCENT))
        self.update()

    # ---------------- 事件 ----------------
    def event(self, e):
        # 拦截全局 Esc 快捷键：关闭钉图而非退出程序
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if (e.modifiers() & Qt.KeyboardModifier.ControlModifier
                and e.key() == Qt.Key.Key_Z):
            self.undo_annot()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        # 右下角抓手：等比例拖拽改窗框（仅移动模式，批注工具激活时不响应）
        if (e.button() == Qt.MouseButton.LeftButton
                and self._tool is None
                and self._grip_rect().contains(e.position().toPoint())):
            self._resizing = True
            self._resize_start = (
                e.globalPosition().toPoint(), self.width(), self.height()
            )
            return
        if e.button() == Qt.MouseButton.LeftButton and self._tool is not None:
            pos = self._to_base(e.position().toPoint())
            self._push_undo()
            if self._tool == "pen":
                self._drawing = True
                self._stroke_last = pos
                self._paint_pen_segment(pos, pos)
            elif self._tool == "arrow":
                self._pending = (pos, pos)
            elif self._tool == "mosaic":
                self._drawing = True
                self._mosaic_last = pos
                self._paint_mosaic_line(pos, pos)
            self.update()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, e):
        if self._resizing:
            self._apply_resize(e.globalPosition().toPoint())
            return
        # 悬停抓手热区时切换光标（移动模式）
        if (self._tool is None and self._drag_offset is None
                and not (e.buttons() & Qt.MouseButton.LeftButton)):
            on_grip = self._grip_rect().contains(e.position().toPoint())
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if on_grip
                           else Qt.CursorShape.ArrowCursor)
        if self._drawing:
            pos = self._to_base(e.position().toPoint())
            if self._tool == "pen":
                self._paint_pen_segment(self._stroke_last, pos)
                self._stroke_last = pos
            elif self._tool == "mosaic":
                self._paint_mosaic_line(self._mosaic_last, pos)
                self._mosaic_last = pos
            self.update()
            return
        if self._pending is not None and (
            e.buttons() & Qt.MouseButton.LeftButton
        ):
            self._pending = (self._pending[0],
                             self._to_base(e.position().toPoint()))
            self.update()
            return
        if self._drag_offset is not None and (
            e.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        if self._resizing:
            self._resizing = False
            self._resize_start = None
            get_logger().info(
                f"[截图] 窗框调整为 {self.width()}x{self.height()}（zoom={self._zoom:.2f}）"
            )
            return
        if self._drawing:
            self._drawing = False
            self._stroke_last = None
            self._mosaic_last = None
            self.update()
            return
        if self._pending is not None:
            start, end = self._pending
            self._pending = None
            if start != end:
                self._paint_arrow(start, end)
            self.update()
            return
        self._drag_offset = None

    # ---------------- 视口与坐标 ----------------
    def _img_rect(self) -> QRectF:
        """图像在窗口内的显示矩形（base 逻辑尺寸 × zoom + 平移）"""
        return QRectF(self._pan.x(), self._pan.y(),
                      self._base_w * self._zoom, self._base_h * self._zoom)

    def _to_base(self, widget_pt: QPoint) -> QPoint:
        """窗口（widget）坐标 → base 逻辑坐标：(pt - pan) / zoom"""
        z = self._zoom if self._zoom > 0 else 1.0
        return QPoint(
            round((widget_pt.x() - self._pan.x()) / z),
            round((widget_pt.y() - self._pan.y()) / z),
        )

    def _clamp_pan(self, pan: QPointF) -> QPointF:
        """平移夹紧：图像与窗框至少保留 _PAN_MARGIN 交集，不至拖丢"""
        img_w = self._base_w * self._zoom
        img_h = self._base_h * self._zoom
        m = self._PAN_MARGIN
        x = min(max(pan.x(), m - img_w), self.width() - m)
        y = min(max(pan.y(), m - img_h), self.height() - m)
        return QPointF(x, y)

    def _grip_rect(self) -> QRect:
        """右下角抓手热区（仅移动模式可拖）"""
        return QRect(self.width() - self._GRIP, self.height() - self._GRIP,
                     self._GRIP, self._GRIP)

    def _apply_resize(self, global_pos: QPoint):
        """抓手拖拽：窗框等比例缩放，内容自动铺满（左上角固定不动）"""
        if self._resize_start is None:
            return
        g0, w0, h0 = self._resize_start
        dx = global_pos.x() - g0.x()
        dy = global_pos.y() - g0.y()
        aspect = w0 / max(1, h0)
        # 取横向/纵向位移（纵向按宽高比折算）的较大者，保证斜拖手感自然
        delta = max(dx, dy * aspect)
        lo = max(self._VIEW_MIN, round(self._base_w * self._ZOOM_MIN))
        hi = round(self._base_w * self._ZOOM_MAX)
        new_w = min(max(round(w0 + delta), lo), hi)
        new_h = max(1, round(new_w / aspect))
        self.setFixedSize(new_w, new_h)
        # 内容铺满窗框：zoom = 窗框宽 / 基准宽，平移归零
        self._zoom = new_w / self._base_w
        self._pan = QPointF(0.0, 0.0)
        self.update()

    # ---------------- 批注 ----------------
    def set_tool(self, tool):
        """切换批注工具：None=移动模式（恢复拖动），pen/arrow/mosaic"""
        if tool not in (None, "pen", "arrow", "mosaic"):
            return
        self._tool = tool
        self._pending = None
        self._drawing = False
        self.setCursor(
            Qt.CursorShape.CrossCursor if tool else Qt.CursorShape.ArrowCursor
        )
        get_logger().info(f"[截图] 批注工具：{tool or '移动'}")
        self.update()

    def set_annot_color(self, hex_color: str):
        self._annot_color = QColor(hex_color)

    def _push_undo(self):
        self._undo_stack.append(self._annot.copy())
        if len(self._undo_stack) > self._UNDO_MAX:
            self._undo_stack.pop(0)

    def undo_annot(self):
        """撤销上一步批注（Ctrl+Z / 右键菜单）"""
        if not self._undo_stack:
            return
        self._annot = self._undo_stack.pop()
        self.update()

    def clear_annot(self):
        """清除全部批注（可撤销）"""
        self._push_undo()
        self._annot.fill(Qt.GlobalColor.transparent)
        get_logger().info("[截图] 批注已清除")
        self.update()

    def _annot_painter(self) -> QPainter:
        p = QPainter(self._annot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        return p

    def _paint_pen_segment(self, a: QPoint, b: QPoint):
        p = self._annot_painter()
        p.setPen(QPen(self._annot_color, self._PEN_WIDTH,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        if a == b:
            p.drawPoint(a)
        else:
            p.drawLine(a, b)
        p.end()

    def _paint_arrow(self, start: QPoint, end: QPoint):
        p = self._annot_painter()
        p.setPen(QPen(self._annot_color, self._PEN_WIDTH,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(QBrush(self._annot_color))
        p.drawLine(start, end)
        # 箭头头部（实心三角）
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        head = self._PEN_WIDTH * 5
        tip = QPointF(end)
        base = tip - QPointF(head * math.cos(angle), head * math.sin(angle))
        spread = 0.45
        wing1 = base + QPointF(head * 0.6 * math.cos(angle - spread + math.pi / 2),
                               head * 0.6 * math.sin(angle - spread + math.pi / 2))
        wing2 = base + QPointF(head * 0.6 * math.cos(angle + spread - math.pi / 2),
                               head * 0.6 * math.sin(angle + spread - math.pi / 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tip, wing1, wing2)
        p.end()

    def _mosaic_region(self, rect: QRect):
        """把 base 上 rect 区域打成马赛克，盖到批注层"""
        dpr = self._pix.devicePixelRatio() or 1.0
        dev = QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                    round(rect.width() * dpr), round(rect.height() * dpr)
                    ).intersected(self._pix.rect())
        if dev.isEmpty():
            return
        region = self._pix.copy(dev)
        cell = self._MOSAIC_CELL
        small = region.scaled(
            max(1, region.width() // cell), max(1, region.height() // cell),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        chunky = small.scaled(
            region.width(), region.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        p = self._annot_painter()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(QRectF(rect), chunky, QRectF(chunky.rect()))
        p.end()

    def _paint_mosaic_line(self, a: QPoint, b: QPoint):
        """马赛克画刷：两点间按半径步进打点（防止快速拖动断档）"""
        r = self._MOSAIC_RADIUS
        bounds = QRect(0, 0, self._base_w, self._base_h)
        step = max(1, r // 2)
        dist = max(abs(b.x() - a.x()), abs(b.y() - a.y()))
        n = max(1, dist // step)
        for i in range(n + 1):
            t = i / n
            cx = round(a.x() + (b.x() - a.x()) * t)
            cy = round(a.y() + (b.y() - a.y()) * t)
            rect = QRect(cx - r, cy - r, 2 * r, 2 * r).intersected(bounds)
            if not rect.isEmpty():
                self._mosaic_region(rect)

    def _composited(self) -> QPixmap:
        """底图 + 批注层合成（复制/保存用），保留 dpr"""
        dpr = self._pix.devicePixelRatio() or 1.0
        out = QPixmap(self._pix.size())
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.drawPixmap(0, 0, self._pix)
        p.drawImage(QRectF(0, 0, self._base_w, self._base_h),
                    self._annot, QRectF(self._annot.rect()))
        p.end()
        return out

    # ---------------- 滚轮缩放（内容缩放，窗框不动） ----------------
    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if delta == 0:
            return
        factor = self._ZOOM_STEP if delta > 0 else 1.0 / self._ZOOM_STEP
        new_zoom = max(self._ZOOM_MIN, min(self._ZOOM_MAX, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return  # 已到边界，不抖动
        # 以光标为锚：光标下的 base 点缩放前后保持在同一窗口位置（指哪放哪）
        cursor = e.position()
        z_old = self._zoom
        base_x = (cursor.x() - self._pan.x()) / z_old
        base_y = (cursor.y() - self._pan.y()) / z_old
        self._zoom = new_zoom
        self._pan = self._clamp_pan(QPointF(
            cursor.x() - base_x * new_zoom,
            cursor.y() - base_y * new_zoom,
        ))
        self.update()

    def reset_zoom(self):
        """复位：窗框回基准尺寸、内容 1.0×、平移归零"""
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.setFixedSize(self._base_w, self._base_h)
        self.update()

    def mouseDoubleClickEvent(self, _e):
        self.close()

    def enterEvent(self, _e):
        self.update()

    def leaveEvent(self, _e):
        self.update()

    def closeEvent(self, _e):
        self.closed.emit(self)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(get_menu_qss(self._theme))

        copy_action = QAction("复制到剪贴板", menu)
        copy_action.triggered.connect(self._copy_to_clipboard)
        menu.addAction(copy_action)

        save_action = QAction("保存为 PNG", menu)
        save_action.triggered.connect(self._save_as_png)
        menu.addAction(save_action)

        # 批注子菜单：工具 / 颜色 / 撤销 / 清除
        annot_menu = menu.addMenu("批注")
        for label, tool in (("画笔", "pen"), ("箭头", "arrow"),
                            ("马赛克", "mosaic")):
            act = QAction(label, annot_menu)
            act.setCheckable(True)
            act.setChecked(self._tool == tool)
            act.triggered.connect(lambda _c, t=tool: self.set_tool(t))
            annot_menu.addAction(act)
        color_menu = annot_menu.addMenu("颜色")
        for label, hex_color in self._ANNOT_COLORS:
            act = QAction(label, color_menu)
            act.setIcon(QIcon(self._color_swatch(hex_color)))
            act.triggered.connect(lambda _c, h=hex_color: self.set_annot_color(h))
            color_menu.addAction(act)
        annot_menu.addSeparator()
        undo_action = QAction("撤销批注 (Ctrl+Z)", annot_menu)
        undo_action.triggered.connect(self.undo_annot)
        annot_menu.addAction(undo_action)
        clear_action = QAction("清除批注", annot_menu)
        clear_action.triggered.connect(self.clear_annot)
        annot_menu.addAction(clear_action)
        move_action = QAction("结束批注（恢复拖动）", annot_menu)
        move_action.triggered.connect(lambda: self.set_tool(None))
        annot_menu.addAction(move_action)

        reset_action = QAction("重置大小", menu)
        reset_action.triggered.connect(self.reset_zoom)
        menu.addAction(reset_action)

        menu.addSeparator()

        close_action = QAction("关闭", menu)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)

        menu.exec(event.globalPos())

    @staticmethod
    def _color_swatch(hex_color: str) -> QPixmap:
        pm = QPixmap(12, 12)
        pm.fill(QColor(hex_color))
        return pm

    # ---------------- 动作 ----------------
    def _copy_to_clipboard(self):
        QGuiApplication.clipboard().setPixmap(self._composited())
        get_logger().info("[截图] 钉图已复制到剪贴板")
        # 轻提示反馈（2026-10-05）：此前仅写日志，用户无感知
        ScreenToast.show_msg("钉图已复制", self._theme)

    def _save_as_png(self):
        from datetime import datetime
        default_name = f"FloatPulse_钉图_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        pictures = QStandardPaths_writable("Pictures")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存钉图", f"{pictures}/{default_name}",
            "PNG 图片 (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self._composited().save(path, "PNG"):
            get_logger().info(f"[截图] 钉图已保存：{path}")
        else:
            get_logger().warning(f"[截图] 钉图保存失败：{path}")

    # ---------------- 绘制 ----------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = self._img_rect()
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        # 批注层（同源同尺寸，随内容一起缩放/平移）
        p.drawImage(target, self._annot, QRectF(self._annot.rect()))
        # 箭头拖动预览（base 坐标 → 窗口：translate(pan) + scale(zoom)）
        if self._pending is not None:
            p.save()
            p.translate(self._pan)
            p.scale(self._zoom, self._zoom)
            pen = QPen(self._annot_color, self._PEN_WIDTH / max(1.0, self._zoom),
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(QBrush(self._annot_color))
            s, e_pt = self._pending
            if s != e_pt:
                angle = math.atan2(e_pt.y() - s.y(), e_pt.x() - s.x())
                head = self._PEN_WIDTH * 5
                tip = QPointF(e_pt)
                base_pt = tip - QPointF(head * math.cos(angle),
                                        head * math.sin(angle))
                spread = 0.45
                wing1 = base_pt + QPointF(
                    head * 0.6 * math.cos(angle - spread + math.pi / 2),
                    head * 0.6 * math.sin(angle - spread + math.pi / 2))
                wing2 = base_pt + QPointF(
                    head * 0.6 * math.cos(angle + spread - math.pi / 2),
                    head * 0.6 * math.sin(angle + spread - math.pi / 2))
                p.drawLine(s, e_pt)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(tip, wing1, wing2)
            p.restore()
        # 右下角抓手指示纹（移动模式 + 悬停时显示，三条斜线）
        if self.underMouse() and self._tool is None:
            p.setPen(QPen(self._accent, 2))
            w, h = self.width(), self.height()
            for i in (1, 2, 3):
                p.drawLine(w - i * 5, h - 2, w - 2, h - i * 5)
        border_w = 2 if self.underMouse() else 1
        p.setPen(QPen(self._accent, border_w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


def QStandardPaths_writable(category: str) -> str:
    """取标准目录（图片/文档），避免在模块头引入 QStandardPaths 污染命名空间"""
    from PyQt6.QtCore import QStandardPaths
    enum_map = {
        "Pictures": QStandardPaths.StandardLocation.PicturesLocation,
        "Documents": QStandardPaths.StandardLocation.DocumentsLocation,
        "Home": QStandardPaths.StandardLocation.HomeLocation,
    }
    loc = enum_map.get(category, QStandardPaths.StandardLocation.HomeLocation)
    path = QStandardPaths.writableLocation(loc)
    return path if path else ""


# ====================================================================
# 控制器：入口去重 + 钉图生命周期 + 主题
# ====================================================================
class ScreenshotPinController(QObject):
    """截图钉屏控制器：热键 / 菜单调用 start_capture()"""

    def __init__(self, theme: str = "dark"):
        super().__init__()
        self._theme = theme
        self._overlay = None
        self._pins = []

    # ---------------- 对外 ----------------
    @property
    def pin_count(self) -> int:
        return len(self._pins)

    def start_capture(self):
        """开始截图（热键 / 悬浮球菜单入口）。已在截图中则忽略。"""
        if self._overlay is not None:
            get_logger().info("[截图] 已在截图中，忽略重复触发")
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            get_logger().warning("[截图] 无可用屏幕，取消")
            return
        shot = screen.grabWindow(0)
        if shot.isNull():
            get_logger().warning("[截图] 屏幕快照失败")
            return
        overlay = SnipOverlay(shot, screen.geometry(), self._theme)
        overlay.region_selected.connect(self._pin_region)
        overlay.destroyed.connect(self._on_overlay_destroyed)
        self._overlay = overlay
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        get_logger().info("[截图] 开始截图（主屏）")

    def close_all(self):
        """关闭全部钉图与覆盖层（程序退出前调用）"""
        if self._overlay is not None:
            try:
                self._overlay.close()
            except RuntimeError:
                pass
            self._overlay = None
        for pin in list(self._pins):
            try:
                pin.close()
            except RuntimeError:
                pass
        self._pins.clear()

    def apply_theme(self, theme: str):
        """主题切换时同步覆盖层（若有）与已钉图的边框色"""
        if theme not in ("light", "dark"):
            return
        self._theme = theme
        if self._overlay is not None:
            try:
                self._overlay._theme = theme
                self._overlay._accent = QColor(
                    get_colors(theme).get("primary", FALLBACK_ACCENT)
                )
                self._overlay.update()
            except RuntimeError:
                pass
        for pin in list(self._pins):
            try:
                pin.apply_theme(theme)
            except RuntimeError:
                pass

    # ---------------- 内部 ----------------
    def _pin_region(self, pixmap: QPixmap, global_pos: QPoint):
        pin = PinWindow(pixmap, global_pos, self._theme)
        pin.closed.connect(self._on_pin_closed)
        self._pins.append(pin)
        pin.show()
        pin.raise_()
        get_logger().info(f"[截图] 已钉图（当前 {len(self._pins)} 张）")

    def _on_overlay_destroyed(self, _obj=None):
        self._overlay = None

    def _on_pin_closed(self, pin):
        try:
            self._pins.remove(pin)
        except ValueError:
            pass
