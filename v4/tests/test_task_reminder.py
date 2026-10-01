# -*- coding: utf-8 -*-
"""托盘任务提醒分桶：bucket_unfinished 三桶口径。

回归的是 2026-09-30 的真实缺陷——此前提醒只覆盖「今日到期 / 已逾期」，
**无截止日的未完成任务永远不会被提醒**（用户实测 5 条未完成任务
deadline 全为空串，静默不提醒）。
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.task_manager import (  # noqa: E402
    bucket_unfinished, STATE_NONE, STATE_TODAY, STATE_OVERDUE, STATE_FUTURE,
)


class FakeTask:
    def __init__(self, title, deadline="", done=False):
        self.title = title
        self.deadline = deadline
        self.done = done


TODAY = "2026-09-30"


class TestBucketUnfinished(unittest.TestCase):

    def test_undated_unfinished_is_collected(self):
        """★核心回归：无截止日的未完成任务必须进「未安排日期」桶。"""
        tasks = [FakeTask("无日期任务", deadline="")]
        overdue, today_due, undated = bucket_unfinished(tasks, TODAY)
        self.assertEqual([t.title for t in undated], ["无日期任务"])
        self.assertEqual(overdue, [])
        self.assertEqual(today_due, [])

    def test_blank_and_dirty_deadline_both_undated(self):
        """空串 / 空白 / 脏格式统一视为无日期（走 task_state 同一口径）。"""
        for raw in ("", "   ", None, "2026/09/30", "2026-13-40", "今天"):
            with self.subTest(deadline=raw):
                _, _, undated = bucket_unfinished(
                    [FakeTask(f"t-{raw}", deadline=raw)], TODAY)
                self.assertEqual(len(undated), 1)

    def test_three_buckets_are_exclusive_and_ordered_by_input(self):
        tasks = [
            FakeTask("逾期甲", "2026-09-20"),
            FakeTask("今日乙", "2026-09-30"),
            FakeTask("无日期丙", ""),
            FakeTask("未来丁", "2026-10-05"),
        ]
        overdue, today_due, undated = bucket_unfinished(tasks, TODAY)
        self.assertEqual([t.title for t in overdue], ["逾期甲"])
        self.assertEqual([t.title for t in today_due], ["今日乙"])
        self.assertEqual([t.title for t in undated], ["无日期丙"])
        # 未来任务不进任何提醒桶
        self.assertNotIn("未来丁",
                         [t.title for t in overdue + today_due + undated])

    def test_done_tasks_never_collected(self):
        tasks = [
            FakeTask("已完成无日期", "", done=True),
            FakeTask("已完成逾期", "2026-01-01", done=True),
            FakeTask("已完成今日", TODAY, done=True),
        ]
        self.assertEqual(bucket_unfinished(tasks, TODAY), ([], [], []))

    def test_empty_input(self):
        self.assertEqual(bucket_unfinished([], TODAY), ([], [], []))

    def test_all_undated_counts_are_what_reminder_shows(self):
        """用户实测场景复刻：5 条未完成、deadline 全空 → 提醒总数应为 5。"""
        tasks = [FakeTask(f"任务{i}", "") for i in range(5)]
        overdue, today_due, undated = bucket_unfinished(tasks, TODAY)
        self.assertEqual(len(overdue) + len(today_due) + len(undated), 5)
        self.assertEqual(len(undated), 5)

    def test_state_none_constant_wired(self):
        """常量语义自检：bucket 用到的 STATE_NONE 就是 task_state 的返回值。"""
        self.assertEqual(STATE_NONE, "none")
        self.assertEqual(STATE_TODAY, "today")
        self.assertEqual(STATE_OVERDUE, "overdue")
        self.assertEqual(STATE_FUTURE, "future")


if __name__ == "__main__":
    unittest.main(verbosity=2)
