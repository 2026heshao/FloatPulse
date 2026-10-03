# -*- coding: utf-8 -*-
"""启动闪屏 · 离屏端到端验证（run_gui_check.py 包装，offscreen 平台）。

2026-10-03 方案 D 重写后的覆盖面：
  A show 后可见、位置在屏幕内（居中偏上）、固定 320×184
  B 动画在转：帧驱动计数随时间增长（QTimer 33ms 生效）
  C set_stage(text, i, total)：文案 / 计数 / 总数三同步；空文案不覆盖
  D 像素比色：卡片底色真实绘制、圆角外透明（grab() 真彩，无黑底污染）
  E 充能链（因果动画）：光点到达后 frac 收敛到 index/total，计数同步
  F 快机结算 + 满格苏醒：未完链跳格结算；满格只苏醒一次，
    after_wake（三段接力焦点交接点）触发，苏醒后补注册也立即放行
  G finish()：淡出后窗口关闭（幂等：重复调用不炸）
  H reduce_motion：跳格充能零动画、交接立即放行、finish 直接 close

跑法：python tools/run_gui_check.py tools/verify_splash.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src import motion
from src.splash import LaunchSplash

app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


screen = app.primaryScreen().availableGeometry()
print(f"== 屏幕 {screen.width()}x{screen.height()} ==", flush=True)

CHAIN_MS = motion.MOTION["spark_fly"] + motion.MOTION["progress_tween"]

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
check("A3 尺寸固定 320x184", (g.width(), g.height()) == (splash.W, splash.H)
      == (320, 184))

# ================== B：动画帧驱动 ==================
print("== B 动画帧驱动 ==", flush=True)
f0 = splash._frames
QTest.qWait(150)
f1 = splash._frames
check("B1 帧计数随时间增长（QTimer 33ms 生效）", f1 > f0, f"{f0} -> {f1}")

# ================== C：阶段文案 / 计数 ==================
print("== C 阶段文案与计数 ==", flush=True)
check("C1 初始文案", splash._stage_text == "正在启动…",
      f"text={splash._stage_text!r}")
splash.set_stage("构建主窗口界面…", 6, 11)
check("C2 set_stage 更新文案", splash._stage_text == "构建主窗口界面…")
check("C3 计数与总数同步", (splash._counter, splash._total) == (6, 11),
      f"{splash._counter} / {splash._total}")
splash.set_stage("", 7, 11)               # 空文案被忽略，档位仍推进
check("C4 空文案不覆盖但档位推进",
      splash._stage_text == "构建主窗口界面…" and splash._counter == 7)

# ================== D：像素比色 ==================
print("== D 像素比色 ==", flush=True)
img = splash.grab().toImage()
# y=55：标题区之下、进度环顶点（y≈74）之上的空白玻璃区
center = img.pixelColor(img.width() // 2, 55)
corner = img.pixelColor(0, 0)                      # 圆角外
bg = splash._c_bg                                  # glass_fill 解析色
diff = (abs(center.red() - bg.red())
        + abs(center.green() - bg.green())
        + abs(center.blue() - bg.blue()))
check("D1 玻璃底色 = 主题 glass_fill", center.alpha() >= 190 and diff <= 60,
      f"pixel={center.name()} bg={bg.name()} diff={diff}")
check("D2 圆角外透明", corner.alpha() == 0, f"alpha={corner.alpha()}")

# ================== E：充能链（因果动画） ==================
print("== E 充能链 ==", flush=True)
splash.set_stage("最后准备…", 10, 11)
check("E1 光点在途（链已开）", splash._spark is not None)
QTest.qWait(int(CHAIN_MS * 1.6) + 100)
check("E2 光点到达后充能收敛到 10/11",
      abs(splash._frac - 10 / 11) < 1e-6, f"frac={splash._frac:.4f}")
check("E3 计数与环同步", splash._counter == 10)

# ================== F：快机结算 + 满格苏醒 ==================
print("== F 快机结算与满格苏醒 ==", flush=True)
splash.finish()                                    # E 用的实例收尾，不再参与
splash2 = LaunchSplash(theme="light")
splash2.start()
splash2.set_stage("初始化悬浮球…", 5, 11)
QTest.qWait(60)                                    # 光点在途（未充能）
splash2.set_stage("显示主窗口…", 11, 11)           # 未完链立即结算再开新链
check("F1 未完链已结算到旧目标（跳格不丢档）",
      abs(splash2._frac - 5 / 11) < 1e-9, f"frac={splash2._frac:.4f}")
fired = []
splash2.after_wake(lambda: fired.append(1))
QTest.qWait(CHAIN_MS + motion.MOTION["wake_halo"] * 2 + 200)
check("F2 满格充能收敛到 1.0", abs(splash2._frac - 1.0) < 1e-6,
      f"frac={splash2._frac:.4f}")
check("F3 满格苏醒已播（只此一次）", splash2._woke and splash2._wake_done)
check("F4 after_wake 焦点交接触发", fired == [1])
splash2.after_wake(lambda: fired.append(2))        # 苏醒后补注册
QTest.qWait(60)
check("F5 苏醒后注册的回调立即放行", fired == [1, 2])

# ================== G：finish 淡出关闭 ==================
print("== G finish 收尾 ==", flush=True)
from PyQt6.sip import isdeleted
splash2.finish()
# 轮询等待淡出完成（offscreen 调度有抖动，固定等待会偶发超时）
left, closed = 3000, False
while left > 0 and not closed:
    closed = isdeleted(splash2) or not splash2.isVisible()
    if not closed:
        QTest.qWait(50)
        left -= 50
check("G1 淡出后窗口已关闭", closed)
if not isdeleted(splash2):
    splash2.finish()                # 幂等
app.processEvents()
check("G2 重复 finish 不炸", True)

# ================== H：reduce_motion 零动画口径 ==================
print("== H reduce_motion ==", flush=True)
motion.set_reduce_motion(True)
try:
    rs = LaunchSplash(theme="light")
    rs.start()
    rs.set_stage("检查数据完整性…", 2, 11)
    check("H1 跳格充能（零动画）", abs(rs._frac - 2 / 11) < 1e-9,
          f"frac={rs._frac:.4f}")
    rs.set_stage("显示主窗口…", 11, 11)
    check("H2 满格苏醒零动画直接完成", rs._wake_done)
    rfired = []
    rs.after_wake(lambda: rfired.append(1))
    app.processEvents()
    check("H3 焦点交接立即放行", rfired == [1])
    rs.finish()
    QTest.qWait(80)
    check("H4 finish 直接 close", isdeleted(rs) or not rs.isVisible())
finally:
    motion.set_reduce_motion(False)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
