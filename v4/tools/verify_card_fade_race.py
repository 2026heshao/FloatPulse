# -*- coding: utf-8 -*-
"""小卡片「连续弹出两次」竞态修复验证（2026-10-05）。

用户实测：鼠标放上悬浮球，偶发小卡片连续弹出两次。根因是收回链路的
140ms 淡出窗口与弹出路径竞态：

  _check_hover_state 判定离开 → _hide_card_faded 起 140ms 淡出 →
  淡出期间鼠标回到球上（手抖擦边 / 快速去而复返）→ isVisible() 仍为
  True，弹出分支误判「卡片还在」跳过 → finished 回调照旧 hide() →
  卡片在球上凭空消失 → 用户再移入 = 第二次弹出。

修复（knowledge_ball.py）：
  ① _hide_card_faded 打代号（gen），_on_card_fade_done 按代号对账，
    过期回调直接作废；
  ② _show_card_on_hover 先 _cancel_card_fade() 接管在途淡出；
  ③ finished 兜底：淡出结束瞬间鼠标已回球/卡片 → 留守不 hide。

覆盖：
  G1 基线回归：鼠标离开 → 卡片照常自动收回
  G2 淡出期间鼠标回球 → 卡片留守、透明度复位（旧代码此处红灯）
  G3 弹出路径取消在途淡出 → 透明度立即回 1.0、卡片保持
  G4 代号对账：过期回调 no-op、最新回调仍可收起
  G5 正常收回不受兜底影响（鼠标确实不在 → 淡出结束照常 hide）

跑法：python tools/run_gui_check.py tools/verify_card_fade_race.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, QRect
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


def make_ball():
    tmp = tempfile.mkdtemp(prefix="fp_faderace_")
    cfg = ConfigManager(os.path.join(tmp, "config.json"))
    cfg.set("card_always_show", False)
    cfg.save()
    return kb.FloatingBall([], config_manager=cfg)


def isolate_pointer(ball, on_ball):
    """钉死「鼠标是否在球上」，隔离真实光标位置对断言的干扰。

    on_ball=False 时连卡片矩形也钉成 0 尺寸（_check_hover_state 的
    in_card 判定恒 False）；停掉定时器只留显式调用，避免后台 tick 抢跑。
    """
    ball._hover_check_timer.stop()
    ball._card_watchdog.stop()
    ball._ball_hit = (lambda p: True) if on_ball else (lambda p: False)
    if not on_ball:
        ball.card_window.geometry = lambda: QRect(0, 0, 0, 0)


def force_leave(ball):
    """模拟鼠标离开：连续 tick 让 out_count 达到收回阈值"""
    for _ in range(ball.HOVER_LEAVE_COUNT + 1):
        ball._check_hover_state()


def fade_running(ball):
    anim = ball._card_fade_anim
    return (anim is not None
            and anim.state() == QAbstractAnimation.State.Running)


# ================= G1. 基线回归：离开照常收回 =================
ball = make_ball()
ball.show_card_mode("fragment")
pump(400)
check("G1.1 卡片已弹出", ball.card_window.isVisible())
isolate_pointer(ball, on_ball=False)
force_leave(ball)
check("G1.2 达到离开阈值后收回淡出已启动", fade_running(ball))
pump(400)
check("G1.3 淡出结束卡片已收回", not ball.card_window.isVisible(),
      f"isVisible={ball.card_window.isVisible()}")

# ================= G2. 淡出期间鼠标回球 → 留守（核心红灯场景） =================
ball2 = make_ball()
ball2.show_card_mode("fragment")
pump(400)
isolate_pointer(ball2, on_ball=False)
force_leave(ball2)
check("G2.1 收回淡出已启动", fade_running(ball2))
# 淡出进行到一半，鼠标回到球上（旧代码：finished 照旧 hide → 凭空消失）
isolate_pointer(ball2, on_ball=True)
pump(400)
check("G2.2 淡出结束卡片留守不消失", ball2.card_window.isVisible(),
      f"isVisible={ball2.card_window.isVisible()}")
check("G2.3 透明度已复位（下次弹出正常）",
      ball2.card_window.windowOpacity() == 1.0,
      f"opacity={ball2.card_window.windowOpacity()}")
check("G2.4 收回检测已重启（不会就此滞留成常驻）",
      ball2._hover_check_timer.isActive())
# 随后鼠标真的离开 → 仍要能正常收回
isolate_pointer(ball2, on_ball=False)
force_leave(ball2)
pump(400)
check("G2.5 留守后再次离开照常收回", not ball2.card_window.isVisible())

# ================= G3. 弹出路径取消在途淡出 =================
ball3 = make_ball()
ball3.show_card_mode("fragment")
pump(400)
isolate_pointer(ball3, on_ball=False)
force_leave(ball3)
check("G3.1 收回淡出已启动", fade_running(ball3))
# 悬停路径接管：_show_card_on_hover 应先作废淡出并复位透明度
isolate_pointer(ball3, on_ball=True)
ball3._show_card_on_hover()
check("G3.2 弹出路径已取消在途淡出", not fade_running(ball3))
check("G3.3 透明度立即回 1.0（不再半透明闪一下）",
      ball3.card_window.windowOpacity() == 1.0,
      f"opacity={ball3.card_window.windowOpacity()}")
pump(300)
check("G3.4 卡片保持显示", ball3.card_window.isVisible())

# ================= G4. 代号对账：过期回调作废 =================
ball4 = make_ball()
ball4.show_card_mode("fragment")
pump(400)
isolate_pointer(ball4, on_ball=True)
stale_gen = ball4._card_fade_gen          # 过期代号（从未有过的旧回调）
ball4._on_card_fade_done(stale_gen)
check("G4.1 过期代号回调不执行 hide", ball4.card_window.isVisible())
isolate_pointer(ball4, on_ball=False)
force_leave(ball4)                        # 正常启动一轮收回（gen 已递增）
current_gen = ball4._card_fade_gen
check("G4.2 新一轮收回代号已递增", current_gen > stale_gen)
ball4._on_card_fade_done(current_gen)     # 鼠标确实不在 → 照常收起
check("G4.3 最新代号回调正常收起", not ball4.card_window.isVisible())

# ================= G5. 鼠标确实不在时兜底不劫持正常收回 =================
ball5 = make_ball()
ball5.show_card_mode("fragment")
pump(400)
isolate_pointer(ball5, on_ball=False)
force_leave(ball5)
pump(400)
check("G5.1 正常收回路径完好", not ball5.card_window.isVisible())
check("G5.2 透明度复位保证下次弹出正常",
      ball5.card_window.windowOpacity() == 1.0)

n_ok = sum(1 for _, ok in _results if ok)
print(f"\n==== {n_ok}/{len(_results)} 通过 ====")
for name, ok in _results:
    if not ok:
        print(f"  FAIL: {name}")
sys.stdout.flush()
os._exit(0 if n_ok == len(_results) else 1)
