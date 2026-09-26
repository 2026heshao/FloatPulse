# -*- coding: utf-8 -*-
"""快速捕捉条：Esc 关闭 + 手柄拖动 + 位置记忆 验证。

A Esc：QLineEdit 吃 Esc 导致窗口关不掉 → eventFilter 拦截后真实按键可关
B 无配置：show_centered = 鼠标所在屏幕居中
C 拖动手柄：伪造 QMouseEvent 走真实 eventFilter → 窗口移动 + 落盘
D 位置恢复：重新 show_centered 回到上次拖动位置
E 屏外位置回退：配置点不在任何屏 → 回退居中

跑法：python tools/run_gui_check.py tools/verify_quick_capture.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QGuiApplication, QCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.quick_capture import QuickCaptureWindow
from src.config import ConfigManager

_app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


tmp = tempfile.mkdtemp(prefix="fp_qc_")
cfg = ConfigManager(os.path.join(tmp, "config.json"))


class StubFM:
    def add_clipboard_text(self, text, source=""):
        return True


qc = QuickCaptureWindow(StubFM(), theme="dark", config_manager=cfg)

# ================= A. Esc 关闭 =================
hidden_fired = []
qc.capture_hidden.connect(lambda: hidden_fired.append(1))
qc.show_centered()
check("A0 显示成功", qc.isVisible())
QTest.keyClick(qc._input, Qt.Key.Key_Escape)
check("A1 Esc 关闭输入条", not qc.isVisible())
check("A2 capture_hidden 已发射", len(hidden_fired) == 1)
check("A3 输入框已清空", qc._input.text() == "")

# ================= B. 无配置 → 所在屏幕居中 =================
qc.show_centered()
screen = (QGuiApplication.screenAt(QCursor.pos())
          or QGuiApplication.primaryScreen())
geo = screen.availableGeometry()
expect = QPoint(geo.x() + (geo.width() - qc.WIDTH) // 2,
                geo.y() + (geo.height() - qc.HEIGHT) // 3)
check("B1 无配置时鼠标所在屏幕居中", qc.pos() == expect,
      f"pos={qc.pos()} expect={expect}")

# ================= C. 手柄拖动（伪造鼠标事件走窗口级 grabMouse 拖动） =================
start = qc.pos()
app = QApplication.instance()


def mouse_ev(ev_type, local, gp, button, buttons):
    return QMouseEvent(ev_type, QPointF(local), QPointF(gp),
                       button, buttons, Qt.KeyboardModifier.NoModifier)


# 手柄矩形内的本地点（handle 在窗口内 x≈2..28）
local0 = QPoint(13, 28)
g0 = qc.mapToGlobal(local0)                          # 按下点（全局逻辑坐标）
app.sendEvent(qc, mouse_ev(QEvent.Type.MouseButtonPress,
                           local0, QPointF(g0),
                           Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton))
check("C0 按下手柄进入拖动态", qc._drag_active)

g1 = g0 + QPoint(80, 40)                             # 拖到 +80,+40
app.sendEvent(qc, mouse_ev(QEvent.Type.MouseMove,
                           local0, QPointF(g1),
                           Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton))
app.sendEvent(qc, mouse_ev(QEvent.Type.MouseButtonRelease,
                           local0, QPointF(g1),
                           Qt.MouseButton.LeftButton,
                           Qt.MouseButton.NoButton))

moved = qc.pos() - start
check("C1 拖动移动窗口 +80,+40", moved == QPoint(80, 40),
      f"moved={moved}")
check("C2 位置落盘 quick_capture_pos",
      cfg.get("quick_capture_pos") == [qc.x(), qc.y()],
      repr(cfg.get("quick_capture_pos")))
check("C3 拖动态已复位", not qc._drag_active)

# ================= D. 位置恢复 =================
qc.hide()
qc.show_centered()
check("D1 再次呼出恢复上次拖动位置",
      qc.pos() == start + QPoint(80, 40), f"pos={qc.pos()}")

# ================= E. 屏外位置回退 =================
cfg.set("quick_capture_pos", [50000, 50000])
qc.hide()
qc.show_centered()
check("E1 配置点不在任何屏 → 回退屏幕居中", qc.pos() == expect,
      f"pos={qc.pos()}")

# ================= F. Esc 三重通道 =================
from PyQt6.QtGui import QShortcut, QKeySequence  # noqa: E402

shortcuts = [c for c in qc.findChildren(QShortcut)
             if c.key() == QKeySequence(Qt.Key.Key_Escape)]
check("F1 Esc QShortcut 已注册", len(shortcuts) == 1)

qc.show_centered()
hidden_fired.clear()
qc._close_via_esc()          # QShortcut activated 的实际处理函数
check("F2 _close_via_esc 隐藏窗口", not qc.isVisible())
check("F3 capture_hidden 已发射", len(hidden_fired) == 1)

# 输入框有内容时 Esc 应先清空再关闭
qc.show_centered()
qc._input.setText("待清空内容")
qc._close_via_esc()
check("F4 Esc 清空输入", qc._input.text() == "")

# ---- 汇总 ----
failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
