# -*- coding: utf-8 -*-
"""截图钉屏（screenshot_pin）逻辑层回归。

不碰真实屏幕：不调用 start_capture()（那会真截屏），所有 PinWindow /
SnipOverlay 均由代码注入的 QPixmap 离屏构造；滚轮缩放用合成的
QWheelEvent 驱动真实 wheelEvent 路径。

覆盖（成熟化 4.4 盲区补齐）：
  1. 框选矩形归一化（SnipOverlay._selection：起点终点任意方向）与 Esc 取消
  2. 钉图坐标数学：_img_rect（dpr 换算）/ _to_base / _clamp_pan 边界
  3. 滚轮缩放：光标锚定（指哪放哪）、ZOOM_MIN 边界不抖动、reset 复位
  4. 抓手等比改窗框：铺满公式、最小窗框钳制、无起点安全 no-op
  5. 批注撤销栈：push 上限、空栈撤销安全、画笔/清除可撤销（像素级断言）、
     箭头同点释放不落墨
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (  # noqa: E402
    QEvent, QPoint, QPointF, QRect, QRectF, Qt,
)
from PyQt6.QtGui import (  # noqa: E402
    QColor, QKeyEvent, QPixmap, QWheelEvent,
)
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.screenshot_pin import PinWindow, SnipOverlay  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_pixmap(w=200, h=150, dpr=1.0, color=Qt.GlobalColor.darkCyan):
    """底图：纯色填充（马赛克/画笔的像素断言需要非空内容）"""
    pix = QPixmap(w, h)
    pix.fill(color)
    if dpr != 1.0:
        pix.setDevicePixelRatio(dpr)
    return pix


def _make_pin(qapp, w=200, h=150, dpr=1.0):
    return PinWindow(_make_pixmap(w, h, dpr), QPoint(30, 40), "dark")


def _make_overlay(qapp):
    return SnipOverlay(_make_pixmap(100, 80), QRect(0, 0, 1920, 1080), "dark")


def _wheel(pin, x, y, steps):
    """合成滚轮事件驱动真实 wheelEvent（steps 正=放大）"""
    delta = int(120 * steps)
    ev = QWheelEvent(
        QPointF(x, y), QPointF(x, y),
        QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    pin.wheelEvent(ev)


def _key_escape():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                     Qt.KeyboardModifier.NoModifier)


# ====================================================================
# 1. 框选归一化与 Esc 取消
# ====================================================================
class TestSnipSelection:
    def test_selection_normalized_any_direction(self, qapp):
        ov = _make_overlay(qapp)
        ov._origin = QPoint(200, 150)
        ov._pos = QPoint(80, 60)          # 终点在起点左上 → 需归一化
        assert ov._selection() == QRect(80, 60, 120, 90)

    def test_selection_empty_before_press(self, qapp):
        ov = _make_overlay(qapp)
        assert ov._selection() == QRect()  # 未按下时无选区

    def test_esc_closes_overlay(self, qapp, monkeypatch):
        """Esc = 取消截图（关闭框选层），绝不退出程序——1.3 行为契约"""
        ov = _make_overlay(qapp)
        closed = []
        monkeypatch.setattr(ov, "close", lambda: closed.append(1))
        ov.keyPressEvent(_key_escape())
        assert closed == [1]


# ====================================================================
# 2. 钉图坐标数学
# ====================================================================
class TestPinGeometry:
    def test_initial_img_rect_and_window_size(self, qapp):
        pin = _make_pin(qapp, 200, 150)
        assert pin._img_rect() == QRectF(0, 0, 200, 150)
        assert (pin.width(), pin.height()) == (200, 150)

    def test_dpr_divides_display_size(self, qapp):
        """QPixmap.size() 是设备像素：dpr=2 时显示尺寸减半"""
        pin = _make_pin(qapp, 200, 150, dpr=2.0)
        assert (pin._base_w, pin._base_h) == (100, 75)
        assert pin._img_rect() == QRectF(0, 0, 100, 75)

    def test_to_base_roundtrip_at_zoom_one(self, qapp):
        pin = _make_pin(qapp)
        assert pin._to_base(QPoint(50, 30)) == QPoint(50, 30)
        pin._pan = QPointF(10.0, 20.0)     # 平移后窗口坐标 = base + pan
        assert pin._to_base(QPoint(60, 50)) == QPoint(50, 30)

    def test_clamp_pan_keeps_minimum_overlap(self, qapp):
        pin = _make_pin(qapp)
        pin._zoom = 2.0                    # 图像 400x300 > 窗框 200x150
        img_w, img_h = 400.0, 300.0
        m = pin._PAN_MARGIN
        lo = pin._clamp_pan(QPointF(-9999, -9999))
        assert lo.x() == pytest.approx(m - img_w)
        assert lo.y() == pytest.approx(m - img_h)
        hi = pin._clamp_pan(QPointF(9999, 9999))
        assert hi.x() == pytest.approx(pin.width() - m)
        assert hi.y() == pytest.approx(pin.height() - m)


# ====================================================================
# 3. 滚轮缩放：光标锚定 / 边界 / 复位
# ====================================================================
class TestPinZoom:
    def test_wheel_zoom_in_anchors_cursor(self, qapp):
        """光标下的 base 点缩放前后保持在同一窗口位置（指哪放哪）"""
        pin = _make_pin(qapp)
        cx, cy = 100, 80                   # 中心点，缩放后不触发钳制
        _wheel(pin, cx, cy, +1)
        step = pin._ZOOM_STEP
        assert pin._zoom == pytest.approx(step)
        # 锚定不变量：(cursor - pan) / zoom == base 点
        assert (cx - pin._pan.x()) / pin._zoom == pytest.approx(cx)
        assert (cy - pin._pan.y()) / pin._zoom == pytest.approx(cy)

    def test_wheel_zoom_out_hits_min_boundary(self, qapp):
        pin = _make_pin(qapp)
        for _ in range(30):
            _wheel(pin, 10, 10, -1)
        assert pin._zoom == pytest.approx(pin._ZOOM_MIN)

    def test_zoom_boundary_no_jitter(self, qapp):
        """已到边界再滚不动：zoom 与 pan 均不再变化"""
        pin = _make_pin(qapp)
        for _ in range(30):
            _wheel(pin, 10, 10, -1)
        zoom, pan = pin._zoom, pin._pan
        _wheel(pin, 10, 10, -1)
        assert pin._zoom == pytest.approx(zoom)
        assert pin._pan == pan

    def test_reset_zoom_restores_base(self, qapp):
        pin = _make_pin(qapp)
        _wheel(pin, 100, 80, +3)
        pin.reset_zoom()
        assert pin._zoom == pytest.approx(1.0)
        assert pin._pan == QPointF(0.0, 0.0)
        assert (pin.width(), pin.height()) == (pin._base_w, pin._base_h)


# ====================================================================
# 4. 抓手等比改窗框
# ====================================================================
class TestPinResize:
    def test_apply_resize_scales_and_fills(self, qapp):
        pin = _make_pin(qapp, 200, 150)
        pin._resize_start = (QPoint(1000, 1000), 200, 150)
        pin._apply_resize(QPoint(1300, 1000))      # dx=300, dy=0
        assert (pin.width(), pin.height()) == (500, 375)
        assert pin._zoom == pytest.approx(500 / 200)
        assert pin._pan == QPointF(0.0, 0.0)       # 内容铺满：平移归零

    def test_apply_resize_min_clamp(self, qapp):
        pin = _make_pin(qapp, 200, 150)
        pin._resize_start = (QPoint(1000, 1000), 200, 150)
        pin._apply_resize(QPoint(-9999, -9999))    # 无限缩小 → 窗框下限
        lo = max(pin._VIEW_MIN, round(pin._base_w * pin._ZOOM_MIN))
        assert pin.width() == lo
        assert pin.height() == max(1, round(lo * 150 / 200))

    def test_apply_resize_without_start_is_noop(self, qapp):
        pin = _make_pin(qapp)
        size = (pin.width(), pin.height())
        pin._apply_resize(QPoint(5000, 5000))      # 未进入抓手拖拽 → 安全
        assert (pin.width(), pin.height()) == size


# ====================================================================
# 5. 批注与撤销栈
# ====================================================================
def _alpha(img, x, y):
    return QColor.fromRgba(img.pixel(x, y)).alpha()


class TestPinAnnotUndo:
    def test_pen_paint_is_undoable_pixel_level(self, qapp):
        pin = _make_pin(qapp)
        pin.set_tool("pen")
        pin.mousePressEvent(_press(pin, 50, 50))
        assert _alpha(pin._annot, 50, 50) > 0      # 落墨
        pin.undo_annot()
        assert _alpha(pin._annot, 50, 50) == 0     # 撤销回到透明

    def test_undo_on_empty_stack_is_safe(self, qapp):
        pin = _make_pin(qapp)
        pin.undo_annot()                            # 不抛异常
        assert pin._undo_stack == []

    def test_undo_stack_capped(self, qapp):
        pin = _make_pin(qapp)
        for _ in range(pin._UNDO_MAX + 5):
            pin._push_undo()
        assert len(pin._undo_stack) == pin._UNDO_MAX

    def test_clear_annot_is_undoable(self, qapp):
        pin = _make_pin(qapp)
        pin.set_tool("pen")
        pin.mousePressEvent(_press(pin, 50, 50))
        pin.clear_annot()
        assert _alpha(pin._annot, 50, 50) == 0     # 已清空
        pin.undo_annot()
        assert _alpha(pin._annot, 50, 50) > 0      # 清除也可撤销

    def test_arrow_release_at_same_point_paints_nothing(self, qapp):
        """箭头拖拽距离为 0：不落墨（预览未确认）"""
        pin = _make_pin(qapp)
        pin.set_tool("arrow")
        pin.mousePressEvent(_press(pin, 60, 60))
        pin.mouseReleaseEvent(_release(pin, 60, 60))
        assert _alpha(pin._annot, 60, 60) == 0

    def test_mosaic_paint_changes_pixels(self, qapp):
        pin = _make_pin(qapp)
        pin.set_tool("mosaic")
        pin.mousePressEvent(_press(pin, 80, 80))
        assert _alpha(pin._annot, 80, 80) > 0      # 马赛克块落入批注层

    def test_unknown_tool_ignored(self, qapp):
        pin = _make_pin(qapp)
        pin.set_tool("eraser")                      # 非法工具名 → 忽略
        assert pin._tool is None


# ====================================================================
# 事件合成辅助（放在用例后便于阅读）
# ====================================================================
def _press(pin, x, y):
    from PyQt6.QtGui import QMouseEvent
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _release(pin, x, y):
    from PyQt6.QtGui import QMouseEvent
    return QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, y),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)
