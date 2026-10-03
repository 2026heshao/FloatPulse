# -*- coding: utf-8 -*-
"""src/time_format.py 统一相对时间 —— 七段口径逐档钉死。

为什么钉这么细：两处 UI（notes_panel 列表 / kb-search 结果 meta 行）
共用这份实现，口径漂移 = 两处显示悄悄不一致；而每档边界（60s /
3600s / 86400s / 7 天）都是手工拍的，回归时最容易悄悄挪动。所有用例
用 ``now`` 注入固定时刻，不依赖真实时钟。
"""

import os
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.time_format import TS_FORMAT, format_relative_time  # noqa: E402

NOW = datetime(2026, 10, 2, 12, 0)


def _ts(**delta):
    """NOW 往前推 delta 的 "YYYY-MM-DD HH:MM" 时间戳"""
    return (NOW - timedelta(**delta)).strftime(TS_FORMAT)


class TestSevenSegments:
    def test_just_now(self):
        # 时间戳是分钟精度（无秒）：与 now 同分钟才落「刚刚」档
        assert format_relative_time("2026-10-02 12:00", now=NOW) == "刚刚"

    def test_minutes(self):
        assert format_relative_time(_ts(minutes=5), now=NOW) == "5 分钟前"

    def test_hours(self):
        assert format_relative_time(_ts(hours=3), now=NOW) == "3 小时前"

    def test_yesterday(self):
        assert format_relative_time(_ts(hours=26), now=NOW) == "昨天 10:00"

    def test_days(self):
        assert format_relative_time(_ts(days=3), now=NOW) == "3 天前"

    def test_same_year_compact(self):
        """≥7 天且同年 → MM-DD HH:MM（新档，替代原样 16 字符全格式）"""
        assert format_relative_time(_ts(days=20), now=NOW) == "09-12 12:00"

    def test_cross_year_raw(self):
        """跨年 → 原样全格式"""
        ts = "2025-12-01 08:30"
        assert format_relative_time(ts, now=NOW) == ts

    def test_boundary_seven_days(self):
        """7 天是分界：差 1 分钟落「6 天前」，整 7 天起走同年紧凑档
        （时间戳分钟精度，构造只能到分钟粒度）"""
        assert format_relative_time(
            _ts(days=6, hours=23, minutes=59), now=NOW) == "6 天前"
        assert format_relative_time(_ts(days=7), now=NOW) == "09-25 12:00"


class TestDirtyInput:
    def test_bad_format_returns_raw(self):
        assert format_relative_time("2026/10/01") == "2026/10/01"
        assert format_relative_time("昨天下午") == "昨天下午"

    def test_empty_and_none(self):
        assert format_relative_time("") == "未知时间"
        assert format_relative_time(None) == "未知时间"

    def test_future_returns_raw(self):
        """时间在当前之后（系统时钟被改）→ 原样显示，不抛"""
        ts = (NOW + timedelta(hours=2)).strftime(TS_FORMAT)
        assert format_relative_time(ts, now=NOW) == ts

    def test_no_now_uses_real_clock(self):
        """不传 now 走真实时钟（生产路径）——只验证不抛且返回非空字符串"""
        out = format_relative_time(datetime.now().strftime(TS_FORMAT))
        assert isinstance(out, str) and out
