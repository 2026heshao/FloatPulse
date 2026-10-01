# -*- coding: utf-8 -*-
"""启动闪屏 · 离屏端到端验证（run_gui_check.py 包装，offscreen 平台）。

覆盖：
  A show 后可见、位置在屏幕内（居中偏上）
  B 动画在转：等待 ~120ms 后旋转弧角度已变化（QTimer 帧驱动生效）
  C set_stage 更新阶段文案
  D 像素比色：卡片底色真实绘制、圆角外透明（grab() 真彩，无黑底污染）
  E finish()：淡出后窗口关闭（幂等：重复调用不炸）

跑法：python tools/run_gui_check.py tools/verify_splash.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.splash import LaunchSplash

app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


screen = app.primaryScreen().availableGeometry()
print(f"== 屏幕 {screen.width()}x{screen.height()} ==", flush=True)

# ================== A：显示与位置 ==================
print("== A 显示与位置 ==", flush=True)
splash = LaunchSplash(theme="light")
splash.start()
app.processEvents()
check("A1 窗口可见", splash.isVisible())
g = splash.geometry()
check("A2 位置在屏幕内", screen.contains(g.topLeft())
      and screen.contains(g.bottomRight()),
      f"({g.x()},{g.y()} {g.width()}x{g.height()})")
check("A3 尺寸固定 300x170", (g.width(), g.height()) == (splash.W, splash.H))

# ================== B：动画在转 ==================
print("== B 动画帧驱动 ==", flush=True)
a0 = splash._angle
QTest.qWait(150)
a1 = splash._angle
check("B1 旋转弧角度随时间变化（QTimer 生效）", a0 != a1,
      f"{a0} -> {a1}")

# ================== C：阶段文案 ==================
print("== C 阶段文案 ==", flush=True)
check("C1 初始文案", splash._stage_text == "正在启动…",
      f"text={splash._stage_text!r}")
splash.set_stage("构建主窗口界面…")
check("C2 set_stage 更新文案", splash._stage_text == "构建主窗口界面…")
splash.set_stage("")                       # 空串被忽略
check("C3 空文案不覆盖", splash._stage_text == "构建主窗口界面…")

# ================== D：像素比色 ==================
print("== D 像素比色 ==", flush=True)
img = splash.grab().toImage()
# y=55：标题区之下、悬浮球轨道（顶点 y≈65）之上的空白玻璃区
center = img.pixelColor(img.width() // 2, 55)
corner = img.pixelColor(0, 0)                      # 圆角外
bg = splash._c_bg                                  # glass_fill 解析色
diff = (abs(center.red() - bg.red())
        + abs(center.green() - bg.green())
        + abs(center.blue() - bg.blue()))
check("D1 玻璃底色 = 主题 glass_fill", center.alpha() >= 190 and diff <= 60,
      f"pixel={center.name()} bg={bg.name()} diff={diff}")
check("D2 圆角外透明", corner.alpha() == 0, f"alpha={corner.alpha()}")

# ================== E：finish 淡出关闭 ==================
print("== E finish 收尾 ==", flush=True)
from PyQt6.sip import isdeleted
splash.finish()
QTest.qWait(500)                    # 淡出 300ms + 余量
# 淡出完成后对象可能已被 deleteLater 销毁——「已销毁」视为「已关闭」
check("E1 淡出后窗口已关闭",
      isdeleted(splash) or not splash.isVisible())
if not isdeleted(splash):
    splash.finish()                 # 幂等
app.processEvents()
check("E2 重复 finish 不炸", True)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
