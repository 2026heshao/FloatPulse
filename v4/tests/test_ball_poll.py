# -*- coding: utf-8 -*-
"""悬浮球悬停轮询空闲降频状态机（HoverPollPolicy）单元测试。

覆盖（成熟化 3.6 / 优化调研 6.1）：
  1. 真值表：静止 <1s → 50；静止 ≥1s → 200；任何活动 → 50
  2. 有状态决策：note_activity 清零静止计时并恢复全速；on_tick 降频/保持
  3. 接线约定：策略常量与 FloatingBall.HOVER_CHECK_INTERVAL 同源（50ms）

纯逻辑测试，不创建 QApplication。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge_ball import FloatingBall, HoverPollPolicy  # noqa: E402


# ====================================================================
# 1. 纯函数真值表 next_interval(idle_ms, active)
# ====================================================================
class TestNextIntervalTruthTable:
    def test_active_always_50(self):
        """活动（无论静止多久）→ 立即恢复 50ms"""
        assert HoverPollPolicy.next_interval(0, True) == 50
        assert HoverPollPolicy.next_interval(500, True) == 50
        assert HoverPollPolicy.next_interval(999, True) == 50
        assert HoverPollPolicy.next_interval(1000, True) == 50
        assert HoverPollPolicy.next_interval(60000, True) == 50

    def test_idle_below_threshold_50(self):
        """静止 <1s → 保持 50ms"""
        assert HoverPollPolicy.next_interval(0, False) == 50
        assert HoverPollPolicy.next_interval(999, False) == 50

    def test_idle_at_or_above_threshold_200(self):
        """静止 ≥1s → 降到 200ms（含边界值 1000）"""
        assert HoverPollPolicy.next_interval(1000, False) == 200
        assert HoverPollPolicy.next_interval(1500, False) == 200
        assert HoverPollPolicy.next_interval(10**6, False) == 200

    def test_interval_constants(self):
        """降频档位与门槛：50 / 200 / 1s（上限即最坏感知延迟）"""
        assert HoverPollPolicy.ACTIVE_INTERVAL_MS == 50
        assert HoverPollPolicy.IDLE_INTERVAL_MS == 200
        assert HoverPollPolicy.IDLE_AFTER_MS == 1000


# ====================================================================
# 2. 有状态决策：note_activity / on_tick
# ====================================================================
class TestPolicyStateful:
    def test_initial_state_active(self):
        """初始态：全速，未降频"""
        p = HoverPollPolicy()
        assert p.slowed is False

    def test_tick_uses_injected_now(self):
        """on_tick 接受注入的 monotonic 秒值（测试不真等 1 秒）"""
        p = HoverPollPolicy()
        p.note_activity()
        base = p.last_activity_ms
        assert p.on_tick(base + 0.049) == 50    # 静止 49ms
        assert p.on_tick(base + 1.0) == 200     # 静止整 1s（边界含）
        assert p.slowed is True

    def test_activity_restores_full_speed(self):
        """降频后来一次活动 → 返回 50、状态复位、静止计时清零"""
        p = HoverPollPolicy()
        base = 100.0
        p.note_activity()
        p.last_activity_ms = base - 2.0         # 伪造已静止 2 秒
        assert p.on_tick(base) == 200
        assert p.slowed is True
        # 活动 → 恢复 50
        assert p.note_activity() == 50
        assert p.slowed is False
        # 静止计时已清零：紧接着的 tick 仍是 50
        assert p.on_tick(p.last_activity_ms + 0.2) == 50

    def test_activity_when_not_slowed_returns_none(self):
        """未降频时活动打点返回 None（调用方无需触碰定时器）"""
        p = HoverPollPolicy()
        assert p.note_activity() is None

    def test_repeated_ticks_stay_slow(self):
        """持续静止：每次 tick 都返回 200（不会自行回弹）"""
        p = HoverPollPolicy()
        p.note_activity()
        base = p.last_activity_ms
        for extra in (1.0, 1.2, 5.0, 60.0):
            assert p.on_tick(base + extra) == 200
        assert p.slowed is True

    def test_slow_then_active_then_idle_again(self):
        """完整循环：降频 → 活动恢复 → 再次静止再降频"""
        p = HoverPollPolicy()
        p.note_activity()
        base = p.last_activity_ms
        assert p.on_tick(base + 1.5) == 200             # 降频
        assert p.note_activity() == 50                  # 活动恢复
        base2 = p.last_activity_ms
        assert p.on_tick(base2 + 0.5) == 50             # 仍在 1s 内
        assert p.on_tick(base2 + 1.0) == 200            # 再次降频


# ====================================================================
# 3. 接线约定：与 FloatingBall 的常量同源
# ====================================================================
def test_active_interval_matches_ball_constant():
    """策略活动档必须等于悬浮球悬停检测定时器的既有间隔（50ms），
    否则活动恢复 / 各显式 start(HOVER_CHECK_INTERVAL) 路径会互相打架。"""
    assert HoverPollPolicy.ACTIVE_INTERVAL_MS == FloatingBall.HOVER_CHECK_INTERVAL


if __name__ == "__main__":
    if pytest:
        pytest.main([os.path.abspath(__file__), "-q"])
