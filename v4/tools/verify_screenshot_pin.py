# -*- coding: utf-8 -*-
"""截图钉屏：拖选裁剪 / ESC 拦截 / 钉图尺寸与生命周期 验证。

A 覆盖层构造：全屏几何 + 十字光标
B ESC 拦截：ShortcutOverride 事件被 accept 并吃掉（不落到全局退出快捷键）
C 拖选裁剪：伪造鼠标事件走真实 mouse 事件链 → 逻辑坐标选区 × dpr 裁剪
D 选区过小：误触取消，不发出 region_selected
E 钉图：显示尺寸 = pixmap/dpr（逻辑坐标）、closed 信号 → 控制器计数回落
F 控制器 close_all：钉图与覆盖层全关
G 主题切换：边框色字典取值不抛异常

跑法：python tools/run_gui_check.py tools/verify_screenshot_pin.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QRect, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QPixmap, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.screenshot_pin import (
    SnipOverlay, PinWindow, ScreenshotPinController, _MIN_SELECTION,
)

_app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


DPR = 2.0  # 用放大的 dpr 验证逻辑/设备坐标换算
SCREEN_W, SCREEN_H = 800, 600

shot = QPixmap(round(SCREEN_W * DPR), round(SCREEN_H * DPR))
shot.setDevicePixelRatio(DPR)
shot.fill(QColor("#223344"))
geo_out = []

# ================= A. 覆盖层构造 =================
overlay = SnipOverlay(shot, _app.primaryScreen().geometry(), "dark")
check("A1 覆盖层无边框置顶", overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
      and overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
overlay.show()
check("A2 显示成功", overlay.isVisible())
geo_out = overlay.geometry()
exp_geo = _app.primaryScreen().geometry()
check("A3 几何=屏幕", geo_out == exp_geo, f"geo={geo_out} exp={exp_geo}")

# ================= B. ESC 拦截（全局退出快捷键冲突防护） =================
ev = QEvent(QEvent.Type.ShortcutOverride)
# QTest.keyEvent 无法直接构造 ShortcutOverride，手动造 QKeyEvent
from PyQt6.QtGui import QKeyEvent
ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape,
               Qt.KeyboardModifier.NoModifier)
accepted = ev.isAccepted()
handled = overlay.event(ev)
check("B1 ShortcutOverride 被处理", handled is True)
check("B2 事件已 accept", ev.isAccepted() and not accepted)
overlay._origin = None
QTest.keyClick(overlay, Qt.Key.Key_Escape)
check("B3 Esc 关闭覆盖层", not overlay.isVisible())
overlay2 = SnipOverlay(shot, _app.primaryScreen().geometry(), "dark")
overlay2.show()

# ================= C. 拖选裁剪（逻辑坐标 × dpr） =================
picked = []


def mouse_ev(ev_type, local, gp, button, buttons):
    return QMouseEvent(ev_type, QPointF(local), QPointF(gp),
                       button, buttons, Qt.KeyboardModifier.NoModifier)


overlay2.region_selected.connect(lambda pix, gp: picked.append((pix, gp)))

# 选区 (100,80)-(300,220) 逻辑坐标 → 期望裁剪 200x140 逻辑尺寸
lp1 = QPointF(100, 80)
lp2 = QPointF(300, 220)
gp1 = overlay2.mapToGlobal(lp1.toPoint())
gp2 = overlay2.mapToGlobal(lp2.toPoint())
overlay2.mousePressEvent(mouse_ev(QEvent.Type.MouseButtonPress, lp1, gp1,
                                  Qt.MouseButton.LeftButton,
                                  Qt.MouseButton.LeftButton))
overlay2.mouseMoveEvent(mouse_ev(QEvent.Type.MouseMove, lp2, gp2,
                                 Qt.MouseButton.NoButton,
                                 Qt.MouseButton.LeftButton))
overlay2.mouseReleaseEvent(mouse_ev(QEvent.Type.MouseButtonRelease, lp2, gp2,
                                    Qt.MouseButton.LeftButton,
                                    Qt.MouseButton.LeftButton))
check("C1 region_selected 已发射", len(picked) == 1)
if picked:
    pix, gp = picked[0]
    # QRect(p1,p2) 宽高含端点：300-100+1=201
    exp_w, exp_h = 201, 141
    check("C2 裁剪尺寸=逻辑选区x2(dpr)", pix.width() == exp_w * 2 and pix.height() == exp_h * 2,
          f"{pix.width()}x{pix.height()}")
    check("C2b 逻辑尺寸=选区", round(pix.width() / DPR) == exp_w and round(pix.height() / DPR) == exp_h,
          f"logical={pix.width() / DPR}x{pix.height() / DPR}")
    check("C3 裁剪图保留 dpr", abs(pix.devicePixelRatio() - DPR) < 1e-6,
          f"dpr={pix.devicePixelRatio()}")
    check("C4 全局坐标正确", gp == gp1, f"gp={gp} expect={gp1}")
check("C5 释放后覆盖层关闭", not overlay2.isVisible())

# ================= D. 选区过小取消 =================
picked2 = []
overlay3 = SnipOverlay(shot, _app.primaryScreen().geometry(), "dark")
overlay3.region_selected.connect(lambda pix, gp: picked2.append((pix, gp)))
overlay3.show()
tiny = QPointF(10, 10)
tiny2 = QPointF(10 + _MIN_SELECTION - 1, 10)
overlay3.mousePressEvent(mouse_ev(QEvent.Type.MouseButtonPress, tiny,
                                  overlay3.mapToGlobal(tiny.toPoint()),
                                  Qt.MouseButton.LeftButton,
                                  Qt.MouseButton.LeftButton))
overlay3.mouseReleaseEvent(mouse_ev(QEvent.Type.MouseButtonRelease, tiny2,
                                    overlay3.mapToGlobal(tiny2.toPoint()),
                                    Qt.MouseButton.LeftButton,
                                    Qt.MouseButton.LeftButton))
check("D1 过小选区不发射信号", len(picked2) == 0)
check("D2 过小选区直接取消", not overlay3.isVisible())

# ================= E. 钉图尺寸 / 生命周期 =================
ctrl = ScreenshotPinController(theme="dark")
if picked:
    pix, gp = picked[0]
    closed_log = []
    pin = PinWindow(pix, gp, "dark")
    pin.closed.connect(lambda p: closed_log.append(p))
    ctrl._pins.append(pin)
    check("E1 钉图显示尺寸=逻辑坐标", pin.width() == 201 and pin.height() == 141,
          f"{pin.width()}x{pin.height()}")
    check("E2 Tool 层级不占任务栏", bool(pin.windowFlags() & Qt.WindowType.Tool))
    check("E3 不抢焦点", bool(pin.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)))
    # 拖动
    p0 = pin.pos()
    pin._drag_offset = QPoint(5, 5)
    pin.mouseMoveEvent(mouse_ev(QEvent.Type.MouseMove, QPointF(10, 10),
                                gp + QPoint(60, 40), Qt.MouseButton.NoButton,
                                Qt.MouseButton.LeftButton))
    check("E4 拖动移动窗口", pin.pos() == p0 + QPoint(55, 35),
          f"pos={pin.pos()}")
    pin.close()
    check("E5 closed 信号发射", len(closed_log) == 1)
else:
    check("E0 前置裁剪失败", False)

# ================= F. 控制器 close_all =================
overlay4 = SnipOverlay(shot, _app.primaryScreen().geometry(), "dark")
overlay4.show()
ctrl._overlay = overlay4
pin2 = PinWindow(shot, QPoint(0, 0), "dark")
ctrl._pins.append(pin2)
ctrl.close_all()
check("F1 close_all 清空钉图", len(ctrl._pins) == 0)
check("F2 close_all 关闭覆盖层", ctrl._overlay is None and not overlay4.isVisible())

# ================= G. 主题切换 =================
try:
    ctrl.apply_theme("light")
    ctrl.apply_theme("dark")
    ctrl.apply_theme("bogus")   # 非法名应被忽略不崩
    ok = True
except Exception as exc:
    ok = False
    print("G exception:", exc)
check("G1 主题切换不抛异常", ok)

# ================= H. 滚轮缩放（内容缩放，窗框尺寸不动） =================
from PyQt6.QtGui import QWheelEvent
from src.screenshot_pin import PinWindow as _PW


def wheel(pin, up, local=None):
    d = QPoint(0, 120) if up else QPoint(0, -120)
    c = local if local is not None else QPointF(pin.width() / 2, pin.height() / 2)
    ev = QWheelEvent(
        c, QPointF(0, 0),
        QPoint(0, 0), d, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    pin.wheelEvent(ev)


if picked:
    pix, gp = picked[0]
    pin3 = _PW(pix, QPoint(200, 200), "dark")
    base_w, base_h = pin3.width(), pin3.height()          # 201x141
    check("H1 滚轮不改窗框", True)                         # 占位对齐编号，见 H2
    wheel(pin3, True)
    check("H2 放大 ×1.25 且窗框不动", abs(pin3._zoom - 1.25) < 1e-6
          and pin3.width() == base_w and pin3.height() == base_h,
          f"zoom={pin3._zoom} size={pin3.width()}x{pin3.height()}")
    # 光标锚定（中心）：光标下的 base 点缩放前后不变
    c_center = QPointF(base_w / 2, base_h / 2)
    b0 = pin3._to_base(QPoint(round(c_center.x()), round(c_center.y())))
    wheel(pin3, True, c_center)
    b1 = pin3._to_base(QPoint(round(c_center.x()), round(c_center.y())))
    check("H3 光标锚定（中心）", b0 == b1, f"{b0} -> {b1}")
    wheel(pin3, False)                                     # 1.5625 → 1.25
    wheel(pin3, False)                                     # 1.25 → 1.0
    check("H4 缩小回 1.0/pan≈0", abs(pin3._zoom - 1.0) < 1e-6
          and abs(pin3._pan.x()) <= 1 and abs(pin3._pan.y()) <= 1,
          f"zoom={pin3._zoom} pan={pin3._pan}")
    # 非中心锚点：右下角放大，base 点保持
    cx, cy = base_w - 10, base_h - 10
    b_before = pin3._to_base(QPoint(cx, cy))
    wheel(pin3, True, QPointF(cx, cy))
    b_after = pin3._to_base(QPoint(cx, cy))
    check("H5 光标锚定（角点）", b_before == b_after, f"{b_before} -> {b_after}")
    for _ in range(20):                                    # 连续缩小 → 触底
        wheel(pin3, False)
    check("H6 缩放下限夹紧", abs(pin3._zoom - _PW._ZOOM_MIN) < 1e-6,
          f"zoom={pin3._zoom}")
    for _ in range(25):                                    # 连续放大 → 触顶
        wheel(pin3, True)
    check("H7 放大上限夹紧", abs(pin3._zoom - _PW._ZOOM_MAX) < 1e-6,
          f"zoom={pin3._zoom}")
    m = _PW._PAN_MARGIN                                    # 平移夹紧：至少 24px 交集
    ok_pan = (pin3._pan.x() <= pin3.width() - m
              and pin3._pan.x() >= m - pin3._base_w * pin3._zoom
              and pin3._pan.y() <= pin3.height() - m
              and pin3._pan.y() >= m - pin3._base_h * pin3._zoom)
    check("H8 pan 夹紧在交集内", ok_pan, f"pan={pin3._pan}")
    pin3.reset_zoom()
    check("H9 重置：尺寸/zoom/pan 全复位", pin3.width() == base_w
          and pin3.height() == base_h and abs(pin3._zoom - 1.0) < 1e-6
          and pin3._pan == QPointF(0, 0))
else:
    check("H0 前置裁剪失败", False)

# ================= I. 钉图批注（画笔/箭头/马赛克/撤销） =================
def annot_has_ink(pin):
    """批注层是否存在非透明像素（稀疏采样，device px 坐标）"""
    img = pin._annot
    sx = max(1, img.width() // 80)
    sy = max(1, img.height() // 80)
    for y in range(0, img.height(), sy):
        for x in range(0, img.width(), sx):
            if img.pixelColor(x, y).alpha() > 0:
                return True
    return False


pin_a = PinWindow(shot.copy(QRect(0, 0, round(200 * DPR), round(140 * DPR))),
                  QPoint(30, 30), "dark")
pin_a.show()
check("I1 批注层尺寸/dpr", pin_a._annot.width() == round(200 * DPR)
      and abs(pin_a._annot.devicePixelRatio() - DPR) < 1e-6,
      f"{pin_a._annot.width()}x{pin_a._annot.height()} dpr={pin_a._annot.devicePixelRatio()}")
check("I2 初始批注层透明", not annot_has_ink(pin_a))

pin_a.set_tool("pen")
check("I3 画笔模式+十字光标", pin_a._tool == "pen"
      and pin_a.cursor().shape() == Qt.CursorShape.CrossCursor)
QTest.mousePress(pin_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(10, 10))
QTest.mouseMove(pin_a, QPoint(60, 10))
QTest.mouseRelease(pin_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                   QPoint(60, 10))
check("I4 画笔落墨", annot_has_ink(pin_a))
check("I5 画笔颜色=默认红",
      pin_a._annot.pixelColor(round(35 * DPR), round(10 * DPR)).alpha() > 0)
check("I6 撤销栈入栈", len(pin_a._undo_stack) >= 1)

pin_a.undo_annot()
check("I7 撤销后批注层回透明", not annot_has_ink(pin_a))

pin_a._paint_arrow(QPoint(20, 20), QPoint(80, 60))
check("I8 箭头落墨", annot_has_ink(pin_a))
tip_px = pin_a._annot.pixelColor(round(78 * DPR), round(59 * DPR))
check("I9 箭头笔色", tip_px.alpha() > 0 and tip_px.red() > 150)  # 默认红 #FF5252

pin_a._push_undo()
pin_a._annot.fill(Qt.GlobalColor.transparent)
pin_a._paint_mosaic_line(QPoint(30, 30), QPoint(70, 30))
check("I10 马赛克落墨", annot_has_ink(pin_a))

comp = pin_a._composited()
check("I11 合成图含批注", comp.size() == pin_a._pix.size()
      and abs(comp.devicePixelRatio() - DPR) < 1e-6)

pin_a.clear_annot()
check("I12 清除批注", not annot_has_ink(pin_a))
pin_a.undo_annot()
check("I13 清除可撤销（马赛克回显）", annot_has_ink(pin_a))

# 缩放+平移下的坐标映射：(pt - pan) / zoom
pin_a.reset_zoom()
pin_a._zoom = 2.0
pin_a._pan = QPointF(10.0, 5.0)
mapped = pin_a._to_base(QPoint(50, 25))
check("I14 缩放/平移坐标映射", mapped == QPoint(20, 10), f"got {mapped}")
pin_a._zoom = 1.0
pin_a._pan = QPointF(0.0, 0.0)

pin_a.set_tool(None)
check("I15 结束批注恢复箭头光标", pin_a._tool is None
      and pin_a.cursor().shape() == Qt.CursorShape.ArrowCursor)

# ================= J. 右下角抓手：窗框等比例缩放，内容铺满 =================
from PyQt6.QtGui import QMouseEvent

pin_b = PinWindow(shot.copy(QRect(0, 0, round(200 * DPR), round(140 * DPR))),
                  QPoint(50, 50), "dark")
pin_b.show()
w0, h0 = pin_b.width(), pin_b.height()                     # 200x140
aspect = w0 / h0
QTest.mousePress(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(w0 - 2, h0 - 2))
check("J1 抓手按下进入缩放", pin_b._resizing and pin_b._resize_start is not None)

g_target = pin_b.mapToGlobal(QPoint(w0 + 60, h0 + 40))
ev_move = QMouseEvent(
    QEvent.Type.MouseMove, QPointF(w0 + 60, h0 + 40), QPointF(g_target),
    Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
    Qt.KeyboardModifier.NoModifier,
)
_app.sendEvent(pin_b, ev_move)
# 按下点在抓手内 (198,138)：全局位移 = (62, 42)，横向占优
dx_real, dy_real = 62, 42
exp_w = round(w0 + max(dx_real, dy_real * aspect))          # 262
exp_h = round(exp_w / aspect)
check("J2 等比例缩放窗框", pin_b.width() == exp_w and pin_b.height() == exp_h,
      f"{pin_b.width()}x{pin_b.height()} exp={exp_w}x{exp_h}")
check("J3 内容铺满窗框", abs(pin_b._zoom - exp_w / w0) < 1e-6
      and pin_b._pan == QPointF(0, 0)
      and abs(pin_b._img_rect().right() - exp_w) < 1.5
      and abs(pin_b._img_rect().bottom() - exp_h) < 1.5)

QTest.mouseRelease(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                   QPoint(w0 + 60, h0 + 40))
check("J4 释放退出缩放态", not pin_b._resizing and pin_b._resize_start is None)

# 超限拖拽 → 上限夹紧（base 200 × 5.0 = 1000）
QTest.mousePress(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(pin_b.width() - 2, pin_b.height() - 2))
g_far = pin_b.mapToGlobal(QPoint(pin_b.width() + 5000, pin_b.height() + 5000))
ev_far = QMouseEvent(
    QEvent.Type.MouseMove, QPointF(pin_b.width() + 5000, pin_b.height() + 5000),
    QPointF(g_far), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
    Qt.KeyboardModifier.NoModifier,
)
_app.sendEvent(pin_b, ev_far)
check("J5 窗框上限夹紧", pin_b.width() == round(200 * _PW._ZOOM_MAX),
      f"w={pin_b.width()}")
QTest.mouseRelease(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                   QPoint(pin_b.width(), pin_b.height()))

pin_b.reset_zoom()
check("J6 抓手缩放后可重置", pin_b.width() == w0 and pin_b.height() == h0
      and abs(pin_b._zoom - 1.0) < 1e-6 and pin_b._pan == QPointF(0, 0))

# 批注模式下抓手不响应（画笔优先）
pin_b.set_tool("pen")
QTest.mousePress(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(w0 - 2, h0 - 2))
check("J7 批注模式抓手不抢事件", not pin_b._resizing and pin_b._drawing)
QTest.mouseRelease(pin_b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                   QPoint(w0 - 2, h0 - 2))
pin_b.set_tool(None)

# ================= 汇总 =================
failed = [n for n, ok in _results if not ok]
print("=" * 40)
print(f"共 {len(_results)} 项，失败 {len(failed)} 项")
if failed:
    print("失败项：", failed)
sys.exit(1 if failed else 0)
