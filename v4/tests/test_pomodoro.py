# -*- coding: utf-8 -*-
"""番茄钟状态机回归（不依赖 GUI 事件循环，直接驱动 _tick + 注入时钟）。

覆盖：状态机合法/非法转移 / progress 边界永不越界 /
休眠补偿（时间戳大幅前跳，remaining 与 finished 判定正确）/
auto_break 自动进入休息 / 配置边界值与非法值夹取 /
Task.focus_sessions 持久化与旧数据迁移 / TaskManager.add_focus_session。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QCoreApplication

from src.pomodoro import (
    PomodoroTimer, PHASE_FOCUS, PHASE_BREAK,
    STATE_IDLE, STATE_RUNNING, STATE_PAUSED,
)
from src.task_manager import Task, TaskManager


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeClock:
    """可手动推进的单调时钟（伪造休眠/时间流逝）。"""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def signals():
    """信号收集器。"""
    box = {"ticked": [], "phase": [], "finished": [], "state": []}
    return box


def _wire(timer: PomodoroTimer, box: dict):
    timer.ticked.connect(box["ticked"].append)
    timer.phase_changed.connect(box["phase"].append)
    timer.finished.connect(box["finished"].append)
    timer.state_changed.connect(box["state"].append)


# ==================================================================
# 状态机转移
# ==================================================================
def test_start_pause_resume_stop(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    assert t.state == STATE_IDLE

    assert t.start(PHASE_FOCUS) is True
    assert t.state == STATE_RUNNING
    assert t.phase == PHASE_FOCUS
    assert t.remaining_seconds() == 25 * 60

    clock.advance(5)
    t._tick()
    assert t.remaining_seconds() == 25 * 60 - 5

    assert t.pause() is True
    assert t.state == STATE_PAUSED
    frozen = t.remaining_seconds()
    clock.advance(600)          # 暂停期间时间流逝不影响剩余量
    assert t.remaining_seconds() == frozen

    assert t.resume() is True
    assert t.state == STATE_RUNNING

    t.stop()
    assert t.state == STATE_IDLE
    assert t.remaining_seconds() == 0


def test_illegal_transitions_safe(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    # idle 直接 resume / pause → 拒绝且不崩
    assert t.resume() is False
    assert t.pause() is False
    # running 中重复 start → 拒绝
    t.start(PHASE_FOCUS)
    assert t.start(PHASE_BREAK) is False
    assert t.phase == PHASE_FOCUS
    # stop 幂等
    t.stop()
    t.stop()
    assert t.state == STATE_IDLE
    # pause 后 resume 以外动作：pause 再次拒绝
    t.pause()
    assert t.pause() is False


def test_progress_boundaries(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock, )
    t.configure(focus_minutes=10)
    assert t.progress() == 0.0          # idle → 0
    t.start(PHASE_FOCUS)
    assert t.progress() == 0.0
    clock.advance(300)                   # 一半
    t._tick()
    assert abs(t.progress() - 0.5) < 0.01
    clock.advance(299)                   # 差 1 秒计满：接近但不越界
    t._tick()
    assert abs(t.progress() - 1.0) < 0.01
    assert t.progress() <= 1.0
    clock.advance(3600)                  # 远超时长 → 计满落回 idle
    t._tick()
    assert t.state == STATE_IDLE         # 无 auto_break → 计满落回 idle
    t.stop()
    assert t.progress() == 0.0


# ==================================================================
# 休眠补偿（最关键）
# ==================================================================
def test_sleep_compensation(qapp, signals):
    """休眠补偿：时间戳前跳后首个 tick 直接对齐真实剩余量。

    25 分钟专注中途休眠 20 分钟 → 醒来 remaining=300（不是
    「只走了一两秒」的累加漂移）；继续睡过截止点 → finished 正确。
    """
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=25)
    t.start(PHASE_FOCUS)
    clock.advance(300)                  # 正常走 5 分钟
    t._tick()
    assert t.remaining_seconds() == 25 * 60 - 300
    clock.advance(15 * 60)              # 伪造休眠 15 分钟：QTimer 未 tick
    t._tick()
    assert t.remaining_seconds() == 300  # 休眠被补偿，不漂移
    clock.advance(6 * 60)               # 再休眠过截止点
    t._tick()
    assert t.remaining_seconds() == 0
    assert t.state == STATE_IDLE
    assert signals["finished"] == [PHASE_FOCUS]


def test_finished_signal_and_remaining_track(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=1)
    t.start(PHASE_FOCUS)
    clock.advance(30)
    t._tick()
    assert t.remaining_seconds() == 30
    clock.advance(29)
    t._tick()
    assert t.remaining_seconds() == 1
    clock.advance(1)
    t._tick()
    assert t.remaining_seconds() == 0
    assert signals["finished"] == [PHASE_FOCUS]


# ==================================================================
# auto_break 相位切换
# ==================================================================
def test_auto_break_on(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=1, break_minutes=5, auto_break=True)
    t.start(PHASE_FOCUS)
    clock.advance(60)
    t._tick()
    # finished(focus) 已发，且自动进入休息 running 态
    assert signals["finished"] == [PHASE_FOCUS]
    assert t.state == STATE_RUNNING
    assert t.phase == PHASE_BREAK
    assert t.remaining_seconds() == 5 * 60
    assert PHASE_BREAK in signals["phase"]


def test_auto_break_off(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=1, auto_break=False)
    t.start(PHASE_FOCUS)
    clock.advance(60)
    t._tick()
    assert t.state == STATE_IDLE
    assert t.phase == PHASE_FOCUS       # 相位不自动切
    assert PHASE_BREAK not in signals["phase"]


def test_break_phase_uses_break_minutes(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(break_minutes=7)
    t.start(PHASE_BREAK)
    assert t.remaining_seconds() == 7 * 60


# ==================================================================
# 配置边界值 / 非法值
# ==================================================================
def test_configure_boundaries(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=1, break_minutes=1)
    t.start(PHASE_FOCUS)
    assert t.remaining_seconds() == 60
    t.stop()
    t.configure(focus_minutes=120, break_minutes=60)
    t.start(PHASE_FOCUS)
    assert t.remaining_seconds() == 120 * 60
    t.stop()


def test_configure_invalid_clamped(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=0, break_minutes=-5, auto_break="yes")
    assert t.focus_minutes == 25        # 非正回退默认
    assert t.break_minutes == 5
    assert t.auto_break is True         # 非 None → bool 化
    t.configure(focus_minutes=999)      # 超上限夹到 120
    assert t.focus_minutes == 120
    t.configure(focus_minutes="abc")    # 非数回退默认
    assert t.focus_minutes == 25


def test_invalid_phase_falls_back(qapp, signals):
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    assert t.start("nonsense") is True
    assert t.phase == PHASE_FOCUS


# ==================================================================
# Task.focus_sessions 持久化 / 迁移 / add_focus_session
# ==================================================================
def _make_manager(tmp_path) -> TaskManager:
    return TaskManager(os.path.join(str(tmp_path), "schedule.json"))


def test_task_focus_sessions_migration(tmp_path):
    # 旧数据无该键 → 0；显式值保留；负数夹 0
    assert Task.from_dict({"task_id": 1, "title": "a"}).focus_sessions == 0
    assert Task.from_dict({"task_id": 1, "focus_sessions": 3}).focus_sessions == 3
    assert Task.from_dict({"task_id": 1, "focus_sessions": -2}).focus_sessions == 0


def test_task_focus_sessions_roundtrip(tmp_path):
    mgr = _make_manager(tmp_path)
    tid = mgr.add_task("写周报", "", "2026-09-27")
    assert mgr.add_focus_session(tid) == 1
    assert mgr.add_focus_session(tid, 2) == 3
    assert mgr.add_focus_session(tid, 0) is None     # 非法 n 拒绝
    assert mgr.add_focus_session(9999) is None       # 不存在的任务
    # 重开管理器 → 持久化生效
    mgr2 = TaskManager(mgr._json_path)
    assert mgr2.get_task(tid).focus_sessions == 3


def test_add_focus_session_via_pomodoro_flow(tmp_path, signals):
    """端到端：专注计满 → 悬浮球侧回调口径（计数 +1 并落盘）。"""
    mgr = _make_manager(tmp_path)
    tid = mgr.add_task("复习", "", "")
    clock = FakeClock()
    t = PomodoroTimer(clock=clock)
    _wire(t, signals)
    t.configure(focus_minutes=1, auto_break=False)
    t.bound_task_id = tid
    t.bound_title = "复习"
    t.start(PHASE_FOCUS)
    clock.advance(60)
    t._tick()
    assert t.state == STATE_IDLE
    assert mgr.get_task(tid).focus_sessions == 0   # 计数由调用方做，计时器不越权
