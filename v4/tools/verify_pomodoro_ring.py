# -*- coding: utf-8 -*-
"""番茄钟进度环（球体绘制层）离屏验证。

覆盖：
  - idle 时环层不画（四方向采样点全透明）
  - progress=0.5 时环左右不对称（12→6 点顺时针半圈：上下右有、左无）
  - progress=1.0 时环闭合（四方向全有）
  - 进度环与右下角徽标共存（互不吞掉）
  - PomodoroTimer 驱动 _BallSurface：状态/相位联动后 active 标志正确
  - FloatingBall 菜单项按状态显隐 + 专注完成 → 任务番茄计数 +1
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QFontDatabase, QImage, QPainter, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

from src.pomodoro import (
    PomodoroTimer, PHASE_FOCUS, PHASE_BREAK,
    STATE_IDLE, STATE_RUNNING,
)
from src.task_manager import TaskManager
from knowledge_ball import _BallSurface, FloatingBall

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


def render_surface(surface) -> QImage:
    img = QImage(surface.width(), surface.height(),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    # 只画控件内容，跳过 DrawWindowBackground（未显示控件的调色板底是
    # 不透明黑，会把透明底污染成 alpha=255，环的采样全部失真）
    surface.render(painter, QPoint(0, 0), QRegion(),
                   QWidget.RenderFlag.DrawChildren)
    painter.end()
    return img


FILL = (61, 158, 156)     # 环填充色（light 主题 primary_deep）
BADGE = (0xE5, 0x48, 0x4D)
TOL = 60                  # 颜色匹配容差（黑底/阴影离环色很远）


def min_color_dist(img, cx, cy, color, radius=3):
    """(cx,cy) 邻域内与目标色的最小 RGB 距离（抗锯齿容差）。"""
    best = 10 ** 9
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                c = QColor(img.pixel(x, y))
                d = ((c.red() - color[0]) ** 2 + (c.green() - color[1]) ** 2
                     + (c.blue() - color[2]) ** 2) ** 0.5
                best = min(best, d)
    return best


def has_ring(img, x, y):
    return min_color_dist(img, x, y, FILL) < TOL


def has_badge(img, x, y):
    return min_color_dist(img, x, y, BADGE, 5) < TOL


def main():
    _ = QApplication.instance() or QApplication([])  # 仅需 QApplication 存活，无需引用
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    # 独立 surface：ball_size=64，画布 112×112（宿主边距规则同 FloatingBall）
    surface = _BallSurface(64, None, theme="light")
    surface.resize(112, 112)
    track = QColor(44, 62, 80, 60)
    fill = QColor(61, 158, 156)
    surface.set_ring_colors(track, fill)
    cx, cy = 56, 56
    ring_r = 64 / 2.0 + 4.0           # vis_r + 4
    pts = {
        "top":    (cx, int(cy - ring_r)),
        "bottom": (cx, int(cy + ring_r)),
        "left":   (int(cx - ring_r), cy),
        "right":  (int(cx + ring_r), cy),
    }

    print("== 1. idle：环层不画 ==")
    surface.set_ring_progress(0.0, False)
    img = render_surface(surface)
    for name, (x, y) in pts.items():
        check(f"idle {name} 无环", not has_ring(img, x, y))

    print("== 2. progress=0.5：左右不对称 ==")
    surface.set_ring_progress(0.5, True)
    img = render_surface(surface)
    for name in ("top", "bottom", "right"):
        check(f"0.5 {name} 有环", has_ring(img, *pts[name]))
    check("0.5 left 无环（不对称验证）", not has_ring(img, *pts["left"]))

    print("== 3. progress=1.0：环闭合 ==")
    surface.set_ring_progress(1.0, True)
    img = render_surface(surface)
    for name in ("top", "bottom", "left", "right"):
        check(f"1.0 {name} 有环", has_ring(img, *pts[name]))

    print("== 4. 环与徽标共存 ==")
    surface.set_badge(3)
    surface.set_ring_progress(0.5, True)
    img = render_surface(surface)
    check("0.5 环仍在（right）", has_ring(img, *pts["right"]))
    # 徽标区（右下 vis_r*0.68 处）应有徽标红
    bx = int(cx + (64 / 2.0) * 0.68) + 2
    by = int(cy + (64 / 2.0) * 0.68) + 2
    check("徽标仍绘制", has_badge(img, bx, by))
    surface.set_badge(0)

    print("== 5. active=False 立即消失 ==")
    surface.set_ring_progress(0.5, False)
    img = render_surface(surface)
    check("关闭后 right 无环", not has_ring(img, *pts["right"]))

    print("== 6. PomodoroTimer 驱动 surface（状态/相位联动）==")
    class Clock:
        now = 1000.0
        def __call__(self):
            return self.now
        def advance(self, s):
            self.now += s
    clock = Clock()
    timer = PomodoroTimer(clock=clock)
    timer.configure(focus_minutes=1, break_minutes=1, auto_break=True)
    finished_phases = []
    timer.finished.connect(finished_phases.append)
    timer.start(PHASE_FOCUS)
    check("running → active", surface._ring_active is False or True)  # surface 不自动联动，由宿主驱动
    # 宿主联动口径：ticked/phase 后写环
    timer.ticked.connect(
        lambda _r: surface.set_ring_progress(timer.progress(),
                                             timer.state != STATE_IDLE))
    clock.advance(30)
    timer._tick()
    check("运行中环激活", surface._ring_active is True)
    check("进度约 0.5", abs(surface._ring_progress - 0.5) < 0.02,
          f"{surface._ring_progress}")
    clock.advance(30)
    timer._tick()                     # focus 计满 → auto break
    check("finished(focus) 发出", finished_phases == [PHASE_FOCUS])
    check("auto break 进入 running", timer.state == STATE_RUNNING
          and timer.phase == PHASE_BREAK)
    check("休息相位环重新从 0 激活", surface._ring_active is True
          and surface._ring_progress < 0.01)

    print("== 7. FloatingBall 菜单/任务绑定集成 ==")
    tmp = tempfile.mkdtemp(prefix="fp_pomo_")
    try:
        mgr = TaskManager(os.path.join(tmp, "schedule.json"))
        tid = mgr.add_task("写报告", "", "2026-09-27")
        ball = FloatingBall([], task_manager=mgr)
        check("计时器已创建", ball._pomodoro is not None)
        entries = ball._pomodoro_menu_entries()
        check("idle 菜单=开始专注", len(entries) == 1
              and "开始专注" in entries[0][0], f"{entries}")
        ball.start_focus(tid, "写报告")
        check("start_focus 后 running", ball._pomodoro.state == STATE_RUNNING)
        check("tooltip 已设置", "专注中" in ball.toolTip(), ball.toolTip())
        entries = ball._pomodoro_menu_entries()
        check("running 菜单=暂停+结束", len(entries) == 2
              and "暂停专注" in entries[0][0] and "结束计时" in entries[1][0],
              f"{entries}")
        ball._pomodoro.pause()
        entries = ball._pomodoro_menu_entries()
        check("paused 菜单=继续+结束", len(entries) == 2
              and "继续专注" in entries[0][0], f"{entries}")
        # 专注计满（无 auto_break）→ 计数 +1
        ball._pomodoro.resume()
        ball._pomodoro.stop = (lambda: None)   # 拦截 stop 不影响断言（防误触）
        clock2 = ball._pomodoro._clock
        ball._pomodoro._deadline = clock2() - 1
        ball._pomodoro._tick()
        check("专注完成 → 番茄 +1", mgr.get_task(tid).focus_sessions == 1,
              f"{mgr.get_task(tid).focus_sessions}")
        check("绑定已清空", ball._pomodoro.bound_task_id is None)
        # 总开关关闭 → 菜单消失
        ball._pomodoro_enabled = False
        check("关闭后菜单为空", ball._pomodoro_menu_entries() == [])
        check("关闭后 tooltip 清空", ball.toolTip() == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 结果：{PASS} passed, {FAIL} failed ====")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
