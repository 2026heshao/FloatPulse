# -*- coding: utf-8 -*-
"""小卡片（CardWindow）自动收回 + 拖动锁自愈验证。

覆盖两个真实缺陷：
  ★2026-09-30「卡片偶发永久滞留 = 变成常驻」
    CardWindow._dragging 只在 mouseReleaseEvent 清零；release 一旦丢失
    （拖动中途切窗 / 弹模态 / 抬起点落到别处），标志位永久 True，
    is_locked() 恒真 → 宿主 _check_hover_state 与 _card_watchdog_tick
    双双早退 → 卡片永不收回（看门狗也救不回来）。
    修法：is_locked() 用物理按键状态交叉校验 + mouseMoveEvent 补一道。

A 关闭态：鼠标离开 → 卡片自动收回
B 常驻态（card_always_show=True）：鼠标离开 → 卡片保持
C 运行中把开关由 ON 改 OFF → 卡片随即被收回
D 拖动锁自愈：_dragging=True 但左键未按 → is_locked() 必须 False 且补发信号
E 基线：_dragging=False → is_locked() 恒 False
F 自愈后卡片能被正常收回（锁不再阻断两条收回路径）

跑法：python tools/run_gui_check.py tools/verify_card_close.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_app = QApplication.instance() or QApplication(sys.argv)

import knowledge_ball as kb  # noqa: E402
from src.config import ConfigManager  # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    print(f"{'[OK]  ' if cond else '[FAIL]'} {name}"
          f"{('  -> ' + detail) if (detail and not cond) else ''}", flush=True)


def pump(ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        _app.processEvents()
        time.sleep(0.01)


def make_ball(always_show):
    tmp = tempfile.mkdtemp(prefix="fp_cardclose_")
    cfg = ConfigManager(os.path.join(tmp, "config.json"))
    cfg.set("card_always_show", always_show)
    cfg.save()
    return kb.FloatingBall([], config_manager=cfg), cfg


def isolate_pointer(ball):
    """把「鼠标既不在球上也不在卡片上」钉死，隔离真实光标位置的影响。

    停掉定时器避免后台 tick 抢跑，只留显式调用 _check_hover_state。
    """
    ball._hover_check_timer.stop()
    ball._card_watchdog.stop()
    ball._ball_hit = lambda p: False
    ball.card_window.geometry = lambda: QRect(0, 0, 0, 0)


def force_leave(ball):
    for _ in range(ball.HOVER_LEAVE_COUNT + 1):
        ball._check_hover_state()


# ================= A. 关闭态自动收回 =================
ball, cfg = make_ball(False)
ball.show_card_mode("fragment")
pump(400)
check("A1 卡片已弹出", ball.card_window.isVisible())
isolate_pointer(ball)
force_leave(ball)
pump(600)
check("A2 关闭态：离开后自动收回", not ball.card_window.isVisible(),
      f"isVisible={ball.card_window.isVisible()}")

# ================= B. 常驻态保持 =================
ball2, cfg2 = make_ball(True)
ball2.show_card_mode("fragment")
pump(400)
isolate_pointer(ball2)
force_leave(ball2)
pump(600)
check("B1 常驻态：离开后保持", ball2.card_window.isVisible(),
      f"isVisible={ball2.card_window.isVisible()}")

# ================= C. 运行中改开关 → 当即恢复收回 =================
cfg2.set("card_always_show", False)
cfg2.save()
force_leave(ball2)
pump(600)
check("C1 运行中关掉开关 → 卡片被收回", not ball2.card_window.isVisible(),
      f"isVisible={ball2.card_window.isVisible()}")

# ================= D. 拖动锁自愈（本次修复的核心） =================
ball3, _ = make_ball(False)
ball3.show_card_mode("fragment")
pump(400)
isolate_pointer(ball3)
w3 = ball3.card_window

fired = []
w3.card_drag_finished.connect(lambda: fired.append(True))

w3._dragging = True                      # 模拟「丢失 release」留下的僵尸锁
# 离屏无真实按键 → QApplication.mouseButtons() 为 NoButton，
# 正是「拖拽早已结束」的等价状态
check("D1 僵尸拖动锁可被 is_locked() 自愈",
      w3.is_locked() is False, f"is_locked={w3.is_locked()}")
check("D2 自愈后 _dragging 标志已复位", w3._dragging is False)
check("D3 自愈补发 card_drag_finished（宿主据此收尾）", fired == [True],
      f"fired={fired}")

# 再看一次：锁已清，收回路径恢复通畅
force_leave(ball3)
pump(600)
check("F1 自愈后卡片能被正常收回", not w3.isVisible(),
      f"isVisible={w3.isVisible()}")

# ================= E. 基线语义 =================
ball4, _ = make_ball(False)
check("E1 未拖动时 is_locked() 恒 False", ball4.card_window.is_locked() is False)

n_ok = sum(1 for _, ok in _results if ok)
print(f"\n==== {n_ok}/{len(_results)} 通过 ====")
for name, ok in _results:
    if not ok:
        print(f"  FAIL: {name}")
sys.stdout.flush()
os._exit(0 if n_ok == len(_results) else 1)
