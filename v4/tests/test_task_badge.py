"""悬浮球徽标口径护栏：refresh_badge = 全部未完成任务数（2026-10-07 改口径）。

旧口径「今日到期 + 已逾期」会把无截止日的习惯类任务永远漏掉
（用户报「5 条未完成只显示 1」根因即此）；本文件钉死新口径防回退。
refresh_badge 只依赖 _task_manager / _surface 两个属性 → 用
object.__new__ 跳过 QWidget.__init__，零 GUI 依赖可单跑。
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_ball():
    from knowledge_ball import FloatingBall

    ball = FloatingBall.__new__(FloatingBall)  # PyQt6 禁 object.__new__
    return ball


def test_badge_counts_all_unfinished():
    """无截止日 / 逾期 / 未来日期的未完成任务全计入，已完成不计。"""
    ball = _make_ball()
    tasks = [
        types.SimpleNamespace(done=False, deadline=""),            # 无截止日（旧口径漏掉的正是它）
        types.SimpleNamespace(done=False, deadline="2020-01-01"),  # 已逾期
        types.SimpleNamespace(done=False, deadline="2999-01-01"),  # 未来日期
        types.SimpleNamespace(done=False, deadline="bad-date"),    # 脏日期（旧口径也不计）
        types.SimpleNamespace(done=True, deadline=""),             # 已完成 → 不计
    ]
    ball._task_manager = types.SimpleNamespace(get_all_tasks=lambda: tasks)
    seen = []
    ball._surface = types.SimpleNamespace(set_badge=seen.append)
    ball.refresh_badge()
    assert seen == [4]


def test_badge_zero_with_empty_tasks():
    ball = _make_ball()
    ball._task_manager = types.SimpleNamespace(get_all_tasks=lambda: [])
    seen = []
    ball._surface = types.SimpleNamespace(set_badge=seen.append)
    ball.refresh_badge()
    assert seen == [0]


def test_badge_exception_falls_to_zero():
    ball = _make_ball()

    def boom():
        raise RuntimeError("x")

    ball._task_manager = types.SimpleNamespace(get_all_tasks=boom)
    seen = []
    ball._surface = types.SimpleNamespace(set_badge=seen.append)
    ball.refresh_badge()
    assert seen == [0]
