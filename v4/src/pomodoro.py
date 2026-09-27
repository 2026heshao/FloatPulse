# -*- coding: utf-8 -*-
"""
====================================================================
番茄钟计时器  -  PomodoroTimer
====================================================================
纯 QtCore 逻辑模块（QObject / QTimer），不依赖 Qt Widgets，
便于 pytest 直接驱动状态机。

设计要点：
  1. 计时基准用「截止时间戳 + 注入时钟」而非 QTimer 次数累加：
     Windows 休眠/锁屏期间 QTimer 不触发，累加法会严重漂移；
     每次 tick 用 now 与 deadline 比对算 remaining，
     休眠醒来自动补偿到正确状态。
  2. 时钟可注入（clock 参数）：测试里伪造时间前进即可覆盖
     「休眠补偿」路径，无需真实等待。
  3. 状态机：idle / running / paused；相位 focus / break 独立区分，
     同一状态机跑两套时长。finished(phase) 在相位计满时发出。
  4. auto_break=True 时专注计满自动进入休息相位（继续 running）；
     False 则回到 idle，由用户手动开始。
  5. idle 状态 QTimer 处于 stop，不空转。
  6. 绑定任务（bound_task_id / bound_title）由调用方（悬浮球）读写，
     本模块只保存不解释 —— 计时逻辑与任务数据解耦。

模块导出：
  - PomodoroTimer
  - STATE_IDLE / STATE_RUNNING / STATE_PAUSED
  - PHASE_FOCUS / PHASE_BREAK
====================================================================
"""

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# ---- 状态 ----
STATE_IDLE = "idle"        # 空闲（未计时 / 已结束）
STATE_RUNNING = "running"  # 计时中
STATE_PAUSED = "paused"    # 暂停（保留剩余时长）

# ---- 相位 ----
PHASE_FOCUS = "focus"      # 专注相位（默认时长 pomodoro_focus_minutes）
PHASE_BREAK = "break"      # 休息相位（默认时长 pomodoro_break_minutes）

_VALID_PHASES = (PHASE_FOCUS, PHASE_BREAK)


def _clamp_minutes(value, default):
    """分钟数防御性夹取：非数 / 非正回退默认值，并限制在 1~120。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if v <= 0:
        return default
    return max(1, min(120, v))


class PomodoroTimer(QObject):
    """番茄钟状态机（单实例语义：同一时刻只有一个相位在跑）。

    信号：
      - ticked(int)          每秒剩余秒数（running 态）
      - phase_changed(str)   相位切换（含自动进入休息）
      - finished(str)        相位计满（参数=刚完成的相位）
      - state_changed(str)   状态切换（idle/running/paused）
    """

    ticked = pyqtSignal(int)
    phase_changed = pyqtSignal(str)
    finished = pyqtSignal(str)
    state_changed = pyqtSignal(str)

    def __init__(self, parent=None, clock=None):
        super().__init__(parent)
        # 时钟注入点：缺省 time.monotonic（不受系统改时间影响）
        self._clock = clock if clock is not None else time.monotonic
        self._state = STATE_IDLE
        self._phase = PHASE_FOCUS
        # 时长配置（分钟），configure() 可随时更新
        self._focus_minutes = 25
        self._break_minutes = 5
        self._auto_break = False
        # 运行时内部量
        self._duration_seconds = 0      # 当前相位总时长（秒）
        self._deadline = 0.0            # 截止时间戳（clock 口径）
        self._remaining_on_pause = 0    # 暂停时冻结的剩余秒数
        # 任务绑定（调用方读写，本模块不解释）
        self.bound_task_id = None
        self.bound_title = ""

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    # ---------------- 配置 ----------------
    def configure(self, focus_minutes=None, break_minutes=None,
                  auto_break=None):
        """更新时长配置；运行中修改在下一个相位生效（当前相位不打断）。"""
        if focus_minutes is not None:
            self._focus_minutes = _clamp_minutes(focus_minutes, 25)
        if break_minutes is not None:
            self._break_minutes = _clamp_minutes(break_minutes, 5)
        if auto_break is not None:
            self._auto_break = bool(auto_break)

    @property
    def focus_minutes(self) -> int:
        return self._focus_minutes

    @property
    def break_minutes(self) -> int:
        return self._break_minutes

    @property
    def auto_break(self) -> bool:
        return self._auto_break

    # ---------------- 只读状态 ----------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def phase(self) -> str:
        return self._phase

    def remaining_seconds(self) -> int:
        """当前剩余秒数：running 实时算 / paused 冻结值 / idle 0。"""
        if self._state == STATE_RUNNING:
            return max(0, int(round(self._deadline - self._clock())))
        if self._state == STATE_PAUSED:
            return max(0, int(self._remaining_on_pause))
        return 0

    def progress(self) -> float:
        """已流逝比例 0.0~1.0（永不越界）；idle 返回 0.0。"""
        if self._state == STATE_IDLE or self._duration_seconds <= 0:
            return 0.0
        elapsed = self._duration_seconds - self.remaining_seconds()
        return max(0.0, min(1.0, elapsed / self._duration_seconds))

    # ---------------- 状态机动作 ----------------
    def start(self, phase: str = PHASE_FOCUS) -> bool:
        """开始一个新相位（满时长起跑）。

        - running 中调用返回 False（不允许叠加计时）
        - paused / idle 中调用均从头开始该相位
        """
        if self._state == STATE_RUNNING:
            return False
        self._phase = phase if phase in _VALID_PHASES else PHASE_FOCUS
        minutes = (self._focus_minutes if self._phase == PHASE_FOCUS
                   else self._break_minutes)
        self._duration_seconds = minutes * 60
        self._deadline = self._clock() + self._duration_seconds
        self._remaining_on_pause = 0
        self._set_state(STATE_RUNNING)
        self._timer.start()
        self.phase_changed.emit(self._phase)
        self.ticked.emit(self._duration_seconds)   # 立即给 UI 一次满刻度
        return True

    def pause(self) -> bool:
        """暂停（仅 running 态有效），冻结剩余时长。"""
        if self._state != STATE_RUNNING:
            return False
        self._remaining_on_pause = self.remaining_seconds()
        self._timer.stop()
        self._set_state(STATE_PAUSED)
        return True

    def resume(self) -> bool:
        """继续（仅 paused 态有效），从冻结剩余量重新推截止时间戳。"""
        if self._state != STATE_PAUSED:
            return False
        self._deadline = self._clock() + max(1, int(self._remaining_on_pause))
        self._set_state(STATE_RUNNING)
        self._timer.start()
        return True

    def stop(self) -> None:
        """结束计时回到 idle（任何状态均可安全调用，幂等）。"""
        self._timer.stop()
        self._remaining_on_pause = 0
        self._duration_seconds = 0
        self._set_state(STATE_IDLE)

    def toggle(self) -> bool:
        """开始专注 / 暂停 / 继续的三态切换（悬浮球菜单便捷入口）。"""
        if self._state == STATE_RUNNING:
            return self.pause()
        if self._state == STATE_PAUSED:
            return self.resume()
        return self.start(PHASE_FOCUS)

    # ---------------- 内部 ----------------
    def _set_state(self, new_state: str):
        if new_state != self._state:
            self._state = new_state
            self.state_changed.emit(new_state)

    def _tick(self):
        """每秒tick：按截止时间戳算剩余（休眠醒来自动补偿到正确状态）。"""
        if self._state != STATE_RUNNING:
            return
        remaining = max(0, int(round(self._deadline - self._clock())))
        self.ticked.emit(remaining)
        if remaining > 0:
            return
        # 相位计满
        done_phase = self._phase
        self._timer.stop()
        self.finished.emit(done_phase)
        # 先落回 idle 再自动续相位：start() 拒绝从 running 态起跑
        self._duration_seconds = 0
        self._set_state(STATE_IDLE)
        if self._auto_break and done_phase == PHASE_FOCUS:
            self.start(PHASE_BREAK)   # 自动进入休息相位（继续 running）
