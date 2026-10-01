# -*- coding: utf-8 -*-
"""
verify_card_click_feedback.py — B9 点击球无反馈缺陷验证

缺陷（docs/视觉交互升级与缺陷审计-2026-09-22.md B9）：
  卡片窗已弹出但停在非卡片页（任务/碎片）时，点击悬浮球只调
  card_window.next_card()，界面仍停在原页 → 用户看不到任何反应。

期望（修复后）：
  点击球 = 「回到卡片页 + 换一张」，与滚轮路径语义一致。
  卡片窗未弹出时行为不变（弹出并随机展示）；锁定态不响应。

验证手法：注入替身卡窗 + 调真实 mouseReleaseEvent 驱动，
不直接调内部方法（避免假通过）。
"""
import os
import sys

# v4/ 根目录按本文件位置推导（勿写死绝对路径：换机器/改目录名即失效）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from knowledge_ball import FloatingBall  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeCardWindow:
    """记录调用序列的卡窗替身（鸭子类型，只看被调了什么）。"""

    def __init__(self, visible=True, locked=False, last_mode="card"):
        self._visible = visible
        self._locked_flag = locked
        self._last_mode = last_mode
        self.calls = []

    def isVisible(self):
        return self._visible

    @property
    def current_mode(self):
        """D3 穿透清零（2026-09-30）：宿主改读公开特性 current_mode，
        替身须同步补上（原直接读 _last_mode）。只读，语义等价。"""
        return self._last_mode

    def is_locked(self):
        return self._locked_flag

    def pos(self):
        return QPoint(100, 100)

    def hide(self):
        self._visible = False
        self.calls.append("hide")

    def next_card(self):
        self.calls.append("next_card")

    def show_next_random(self):
        self.calls.append("show_next_random")
        self._visible = True

    def popup_near(self, rect):
        self.calls.append("popup_near")

    def _switch_mode(self, mode):
        self.calls.append(f"switch_mode:{mode}")
        self._last_mode = mode

    def switch_mode(self, mode):
        """公开入口（D3 穿透清零 2026-09-30）：宿主改调 switch_mode，
        替身同步补上——真身 CardWindow.switch_mode 即转调 _switch_mode，
        故这里保持同一调用记录，断言口径不变。"""
        self._switch_mode(mode)


def click_ball(ball):
    """构造一次真实的「按下 → 释放（未移动）」左键事件序列。"""
    rect = ball._ball_visual_rect()
    center = rect.center()
    gp = QPointF(center)
    lp = QPointF(ball.mapFromGlobal(center))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, lp, gp,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    ball.mousePressEvent(press)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, lp, gp,
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    ball.mouseReleaseEvent(release)


def make_ball(card_win):
    ball = FloatingBall(cards=[])
    ball._card_window = card_win
    return ball


# ---------- A. 核心缺陷场景：可见 + 停在任务页 → 必须先切回卡片页 ----------
cw = FakeCardWindow(visible=True, last_mode="task")
ball = make_ball(cw)
click_ball(ball)
ok = "switch_mode:card" in cw.calls and "next_card" in cw.calls
seq_ok = (cw.calls.index("switch_mode:card") < cw.calls.index("next_card"))
check("A. 卡片窗停在任务页时点击球 → 先切回卡片模式再换卡",
      ok and seq_ok, f"calls={cw.calls}")

# ---------- B. 可见 + 停在碎片页 → 同样要切回 ----------
cw = FakeCardWindow(visible=True, last_mode="fragment")
ball = make_ball(cw)
click_ball(ball)
check("B. 卡片窗停在碎片页时点击球 → 同样切回卡片模式",
      "switch_mode:card" in cw.calls and "next_card" in cw.calls,
      f"calls={cw.calls}")

# ---------- C. 可见 + 已在卡片页 → 不该有多余 switch_mode（保持原行为） ----------
cw = FakeCardWindow(visible=True, last_mode="card")
ball = make_ball(cw)
click_ball(ball)
no_extra = not any(c.startswith("switch_mode") for c in cw.calls)
check("C. 已在卡片页时点击球 → 只换卡、不重复切换模式",
      no_extra and cw.calls == ["next_card"], f"calls={cw.calls}")

# ---------- D. 卡片窗不可见 → 弹出并随机展示（原行为不变） ----------
cw = FakeCardWindow(visible=False, last_mode="card")
ball = make_ball(cw)
click_ball(ball)
check("D. 卡片窗未弹出时点击球 → 弹出 + 随机展示（原行为不变）",
      cw.calls == ["show_next_random", "popup_near"], f"calls={cw.calls}")

# ---------- E. 锁定态 → 完全不响应 ----------
cw = FakeCardWindow(visible=True, locked=True, last_mode="task")
ball = make_ball(cw)
click_ball(ball)
check("E. 卡片窗锁定时点击球 → 不换卡、不切模式",
      cw.calls == [], f"calls={cw.calls}")

# ---------- F. 拖拽后释放 → 走吸附分支，不触发换卡 ----------
cw = FakeCardWindow(visible=True, last_mode="task")
ball = make_ball(cw)
ball._snap_to_edge = lambda *a, **k: None          # 屏蔽真实吸附动画
ball._schedule_position_save = lambda *a, **k: None
rect = ball._ball_visual_rect()
center = rect.center()
gp = QPointF(center)
press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(ball.mapFromGlobal(center)),
                    gp, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
ball.mousePressEvent(press)
ball._moved = True                                  # 模拟已判定为拖拽
ball._pressed = True
far = QPointF(center.x() + 120, center.y())
ball.mouseReleaseEvent(QMouseEvent(
    QEvent.Type.MouseButtonRelease, QPointF(ball.mapFromGlobal(center)),
    far, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
    Qt.KeyboardModifier.NoModifier))
check("F. 拖拽后释放 → 不触发换卡（吸附分支独立）",
      cw.calls == [], f"calls={cw.calls}")

print()
failed = [r for r in results if not r[1]]
print(f"[DONE] {len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    sys.exit(1)
sys.exit(0)
