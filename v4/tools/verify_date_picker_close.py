# -*- coding: utf-8 -*-
"""离屏验证：日期选择器「点击外部关闭」（2026-10-02）。

覆盖：
  A 真实外点路径（QTest，窗口系统层参与，与实机同源）
    A1 打开弹层可见
    A2 点旁边按钮 → 关闭（Qt.Popup 窗口系统级外点关闭）
    A3 ★ 点输入框本体 → 关闭且不被重放顶开（用户原始 bug 的回归钉子）
    A4 点 8px 透明阴影边距 → 关闭（用户视角的「弹层旁边」）
    A5 点面板内空隙 → 不误关
    A6 一次性开关被用掉后重开 → WA_NoMouseReplay 重新装填为 True
  B 应用级兜底（sendEvent 直达，绕过窗口系统层）
    B1 无关控件按下 → 关闭（过滤器兜底）
    B2 弹层已关 → 过滤器已卸载，不再干扰
    B3 点锚点 → 豁免放行，由 DateField toggle 关闭
    B4 弹层内点日期 → picked 恰一次（过滤器不劫持自家事件）
  C 契约钉子
    C1 set_anchor 接线（popup._anchor is field）
    C2 Esc 关闭回归
  M 护栏自检（前提失效必须先红：探针点几何 + 源码钉子）
"""
import os
import sys

V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V4)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (  # noqa: E402
    QEvent, QPointF, QPoint, QRectF, Qt,
)
from PyQt6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QPushButton, QVBoxLayout, QWidget,
)

from src.date_picker import (  # noqa: E402
    POPUP_H, POPUP_W, SHADOW, CalendarPopup, DateField,
)

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, n=8):
    for _ in range(n):
        app.processEvents()


def send_press(target, pos=None):
    point = QPointF(pos) if pos is not None else QPointF(5, 5)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, point,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, ev)


def click(app, widget, pos):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=pos)
    pump(app)


PANEL = QRectF(SHADOW, SHADOW, POPUP_W, POPUP_H)
MARGIN_POS = QPoint(2, 2)
GAP_POS = QPoint(150, 45)

app = QApplication.instance() or QApplication(sys.argv)

# ---- 布景：宿主窗口 + DateField + 旁边按钮（模拟任务页布局） ----
host = QWidget()
host.resize(400, 300)
lay = QVBoxLayout(host)
field = DateField(host, theme="light")
btn = QPushButton("旁边按钮")
lay.addWidget(field)
lay.addWidget(btn)
host.show()
pump(app)


def popup():
    return field.popup


def open_popup():
    field.open_popup()
    pump(app)


def close_popup():
    if popup() is not None and popup().isVisible():
        popup().close()
    pump(app)


print("== M. 护栏自检（前提失效必须先红）==")
check("M1 margin 点在面板外、gap 点在面板内",
      not PANEL.contains(QPointF(MARGIN_POS))
      and PANEL.contains(QPointF(GAP_POS)))
src = open(os.path.join(V4, "src", "date_picker.py"), encoding="utf-8").read()
show_ev = src.split("def showEvent", 1)[1].split("def hideEvent", 1)[0]
check("M2 showEvent 里重新装填 WA_NoMouseReplay（一次性开关，缺它第二次打开关不掉）",
      "WA_NoMouseReplay" in show_ev)
check("M3 showEvent 里安装应用级过滤器（兜底层）",
      "installEventFilter(self)" in show_ev)
init_seg = src.split("def __init__(self, parent=None, theme=DEFAULT_THEME)",
                     1)[1].split("def _sync_today", 1)[0]
check("M4 __init__ 与 showEvent 双重装填（构造即带上）",
      "WA_NoMouseReplay" in init_seg)

print("== A. 真实外点路径（QTest）==")
open_popup()
check("A1 打开弹层可见", popup() is not None and popup().isVisible())

click(app, btn, QPoint(5, 5))
check("A2 点旁边按钮 → 关闭", not popup().isVisible())

open_popup()
click(app, field, QPoint(10, 10))
check("A3 ★ 点输入框本体 → 关闭且不被重放顶开（原始 bug 回归）",
      not popup().isVisible())

open_popup()
click(app, popup(), MARGIN_POS)
check("A4 点 8px 透明阴影边距 → 关闭", not popup().isVisible())

open_popup()
check("A5 前提：gap 点不命中任何控件", popup().hit_test(GAP_POS) == ("none",))
click(app, popup(), GAP_POS)
check("A5 点面板内空隙 → 不误关", popup().isVisible())

close_popup()
open_popup()
click(app, popup(), MARGIN_POS)          # 用一次点击把一次性开关用掉
check("A6a 关闭点击后开关被用掉（Qt 一次性语义）",
      not popup().testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay))
open_popup()
check("A6b 重开后 WA_NoMouseReplay 重新装填为 True",
      popup().testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay))
click(app, field, QPoint(10, 10))
check("A6c 重开后点输入框依旧关得掉", not popup().isVisible())

print("== B. 应用级兜底（sendEvent 直达）==")
standalone = CalendarPopup(None, "light")
standalone.configure(selected=None, year=2026, month=10)
standalone.show()
pump(app)
dummy = QPushButton("dummy")
dummy.show()
pump(app)
send_press(dummy)
pump(app)
check("B1 无关控件按下 → 弹层关闭（过滤器兜底）", not standalone.isVisible())

send_press(dummy)
check("B2 弹层已关 → 过滤器已卸载，不误伤后续点击", not standalone.isVisible())
standalone.close()
dummy.deleteLater()

open_popup()
send_press(field)
pump(app)
check("B3 点锚点 → 豁免放行，由 DateField toggle 关闭", not popup().isVisible())

open_popup()
got = []
popup().picked.connect(got.append)
cx, cy = SHADOW + 8 + 4 * 40 + 20, SHADOW + 34 + 24 + 15
send_press(popup(), QPointF(cx, cy))
pump(app)
check("B4 弹层内点日期 → picked 恰一次（不劫持自家事件）",
      len(got) == 1 and not popup().isVisible())

print("== C. 契约钉子 ==")
open_popup()
check("C1 set_anchor 接线（锚点豁免依赖它）", popup()._anchor is field)
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
               Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(popup(), ev)
pump(app)
check("C2 Esc 关闭回归", not popup().isVisible())

close_popup()
host.hide()
print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
