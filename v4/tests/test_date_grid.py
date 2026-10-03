# -*- coding: utf-8 -*-
"""日期选择器纯逻辑 —— 护栏测试（src/date_grid.py）。

背景（2026-10-02）：日程任务页的截止日期弹层从 Qt 原生 QCalendarWidget
换成自绘（外观对齐 Chromium 原生 date picker，见 src/date_picker.py）。
自绘之后，「日历整体错位一格」和「弹层跑到屏幕外」这两类缺陷不会再被
Qt 挡住，所以把它们背后的纯函数单独钉在这里。

本文件钉死三层契约：
  A. 月矩阵：周一起始、固定 6×7=42 格、含上下月补位；并以**用户给的
     截图那一个月（2026-10）**逐格对拍 —— 28/29/30 是上月补位、1 号
     落在「四」列、末行是下月 2~8 号；
  B. 月份算术：跨年进位/退位、负数 delta、非法月份的报错口径；
  C. 弹层定位：右缘对齐、横向夹边界、下方放不下翻到上方、上下都放不下
     时贴边（而不是把弹层推出可视区）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.date_grid import (  # noqa: E402
    COLS, DAYS_IN_GRID, MONTH_LABELS, ROWS, WEEKDAY_LABELS, WEEK_START,
    add_months, days_in_month, is_same_day, month_cells, month_title,
    place_popup,
)


# ====================================================================
# A. 月矩阵
# ====================================================================
class TestMonthCells:
    def test_grid_is_fixed_6x7(self):
        assert (COLS, ROWS) == (7, 6)
        assert DAYS_IN_GRID == 42
        for year, month in ((2026, 10), (2024, 2), (2026, 2), (2027, 1)):
            assert len(month_cells(year, month)) == 42

    def test_week_starts_monday(self):
        assert WEEK_START == 0
        assert WEEKDAY_LABELS == ("一", "二", "三", "四", "五", "六", "日")

    def test_first_cell_is_monday_on_or_before_the_first(self):
        cells = month_cells(2026, 10)
        first_cell = cells[0]
        # 2026-10-01 是周四 → 补位到同一周的周一（9/28）
        assert first_cell == (2026, 9, 28, False)
        assert len(cells) == 42

    def test_october_2026_matches_the_screenshot(self):
        """逐格对拍用户提供的截图（Chromium 原生 date picker）。

        截图内容：
            28 29 30  1  2  3  4
             5  6  7  8  9 10 11
            12 13 14 15 16 17 18
            19 20 21 22 23 24 25
            26 27 28 29 30 31  1
             2  3  4  5  6  7  8
        这是本次改造的**唯一验收基准**：列错一格，整张日历就错了。
        """
        expect_days = (
            28, 29, 30, 1, 2, 3, 4,
            5, 6, 7, 8, 9, 10, 11,
            12, 13, 14, 15, 16, 17, 18,
            19, 20, 21, 22, 23, 24, 25,
            26, 27, 28, 29, 30, 31, 1,
            2, 3, 4, 5, 6, 7, 8,
        )
        expect_in_month = (
            False, False, False, True, True, True, True,
            True, True, True, True, True, True, True,
            True, True, True, True, True, True, True,
            True, True, True, True, True, True, True,
            True, True, True, True, True, True, False,
            False, False, False, False, False, False, False,
        )
        cells = month_cells(2026, 10)
        assert tuple(c[2] for c in cells) == expect_days
        assert tuple(c[3] for c in cells) == expect_in_month

    def test_selected_cell_index_of_the_screenshot(self):
        """截图里选中的 2 号 = 第 5 格（下标 4），点它必须命中 10-02。"""
        cells = month_cells(2026, 10)
        assert cells[4] == (2026, 10, 2, True)

    def test_in_month_count_equals_days_in_month(self):
        for year, month in ((2026, 10), (2026, 2), (2024, 2), (2026, 12)):
            cells = month_cells(year, month)
            marked = [c for c in cells if c[3]]
            assert len(marked) == days_in_month(year, month)
            # 补位日必须紧贴首尾，不能夹在中间
            idx = [i for i, c in enumerate(cells) if c[3]]
            assert idx == list(range(idx[0], idx[0] + len(idx)))

    def test_cells_are_consecutive_dates(self):
        from datetime import date, timedelta
        cells = month_cells(2026, 3)
        days = [date(c[0], c[1], c[2]) for c in cells]
        for prev, cur in zip(days, days[1:]):
            assert cur - prev == timedelta(days=1)

    def test_invalid_month_raises(self):
        for bad in (0, 13, -1, "x", None):
            with pytest.raises(ValueError):
                month_cells(2026, bad)


# ====================================================================
# B. 月份算术
# ====================================================================
class TestMonthArithmetic:
    def test_add_months_crosses_year_boundary(self):
        assert add_months(2026, 1, -1) == (2025, 12)
        assert add_months(2026, 12, 1) == (2027, 1)
        assert add_months(2026, 10, 3) == (2027, 1)

    def test_add_months_handles_large_negative_delta(self):
        assert add_months(2026, 1, -13) == (2024, 12)
        assert add_months(2026, 3, -12) == (2025, 3)

    def test_add_months_zero_is_identity(self):
        assert add_months(2026, 10, 0) == (2026, 10)

    def test_month_label_only_within_1_12(self):
        for start in range(1, 13):
            year, month = add_months(2020, start, 0)
            assert 1 <= month <= 12
            assert year == 2020

    def test_days_in_month_leap_year(self):
        assert days_in_month(2024, 2) == 29
        assert days_in_month(2026, 2) == 28
        assert days_in_month(2026, 10) == 31

    def test_month_title_matches_screenshot(self):
        assert month_title(2026, 10) == "2026年10月"
        assert month_title(2026, 1) == "2026年1月"     # 不补零，与截图一致

    def test_month_labels_are_twelve(self):
        assert len(MONTH_LABELS) == 12
        assert MONTH_LABELS[0] == "1月"
        assert MONTH_LABELS[9] == "10月"


class TestIsSameDay:
    def test_equal_and_not_equal(self):
        assert is_same_day((2026, 10, 2), (2026, 10, 2))
        assert not is_same_day((2026, 10, 2), (2026, 10, 3))
        assert not is_same_day((2026, 10, 2), (2025, 10, 2))

    def test_none_never_matches(self):
        assert not is_same_day(None, (2026, 10, 2))
        assert not is_same_day((2026, 10, 2), None)
        assert not is_same_day(None, None)

    def test_ignores_extra_components(self):
        """月矩阵的格子是 4 元组（含 in_month），比对只看前三项。"""
        assert is_same_day((2026, 10, 2, True), (2026, 10, 2))


# ====================================================================
# C. 弹层定位
# ====================================================================
SCREEN = (0, 0, 1920, 1080)


class TestPlacePopup:
    def test_right_edges_are_aligned(self):
        x, _ = place_popup((900, 300, 150, 30), (296, 278), SCREEN)
        assert x + 296 == 900 + 150

    def test_flip_above_when_no_room_below(self):
        """截图里弹层压在输入框上方，就是因为下方放不下。"""
        _, y = place_popup((900, 1000, 150, 30), (296, 278), SCREEN)
        assert y + 278 <= 1000 - 6

    def test_below_when_room_is_enough(self):
        _, y = place_popup((900, 300, 150, 30), (296, 278), SCREEN)
        assert y == 300 + 30 + 6

    def test_clamped_at_left_edge(self):
        x, _ = place_popup((10, 300, 150, 30), (296, 278), SCREEN)
        assert x == 8

    def test_clamped_at_right_edge(self):
        # 锚点贴屏幕最右侧：弹层不许越出右边缘
        x, _ = place_popup((1900, 300, 150, 30), (296, 278), SCREEN)
        assert x + 296 <= SCREEN[2] - 8

    def test_never_above_screen_top(self):
        _, y = place_popup((900, 10, 150, 30), (296, 278), SCREEN)
        assert y >= 8

    def test_offset_screen_origin_is_respected(self):
        """副屏（原点非 0）同样成立。"""
        screen = (1920, 0, 1920, 1080)
        x, _ = place_popup((1900, 300, 150, 30), (296, 278), screen)
        assert x >= 1920 + 8
        assert x + 296 <= 1920 + 1920 - 8

    def test_result_is_integers(self):
        x, y = place_popup((900, 300, 150.4, 30.6), (296.7, 278.2), SCREEN)
        assert isinstance(x, int) and isinstance(y, int)

    def test_popup_wider_than_screen_falls_back_to_margin(self):
        x, _ = place_popup((100, 300, 150, 30), (5000, 278), (0, 0, 800, 600))
        assert x == 8
