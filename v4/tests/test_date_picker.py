# -*- coding: utf-8 -*-
"""
自绘日历弹层 / 日期框回归  -  test_date_picker
====================================================================
背景（2026-10-02）：日程任务页的截止日期控件，从 Qt 原生 ``QDateEdit``
日历弹层换成**自绘弹层 + 自绘日期框**（外观对齐 Chromium 原生
date picker，见 src/date_picker.py）。原生弹层本来由 Qt 保证「画在哪 =
点在哪」，换成自绘之后这条不变量必须自己钉住。

本文件钉死四层契约：

  A. 几何：42 个日期格 + 表头两个箭头 + 页脚两块的**命中区**与绘制区
     同源；月视图与日视图尺寸完全一致（切换不跳）；标题区不与箭头重叠；
  B. 行为：点日期格 / 清除 / 今天分别发对应信号；翻月翻年、标题切月视图、
     键盘 Esc 关闭与方向键移动都落到实处；
  C. DateField：只读 + 关掉原生微调钮与原生弹层；空值哨兵语义
     （``dateOrNone`` / ``setDateOrNone`` / ``isEmpty``）；点框开弹层；
     选日期只发**一次** ``dateChanged``（防重复刷新的静默缺陷）；
  D. 主题：日历色板在两个主题都齐、蓝色强调色按用户拍板**钉死字面量**
     （防后人"顺手改成主色绿"）、选中块对比度达标、QSS 仍给右侧日历图标
     留出 30px 且不再残留原生 ``::drop-down`` 规则（假控件）。
====================================================================
"""

import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (                                            # noqa: E402
    QDate, QEvent, QPoint, QPointF, QSize, Qt,
)
from PyQt6.QtGui import QKeyEvent, QMouseEvent                        # noqa: E402
from PyQt6.QtTest import QTest                                        # noqa: E402
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication            # noqa: E402

from src import date_picker                                           # noqa: E402
from src.date_picker import (                                         # noqa: E402
    POPUP_H, POPUP_W, SHADOW, CalendarPopup, DateField, SENTINEL_DATE,
)
from src.theme import THEMES, get_card_window_qss, get_main_window_qss  # noqa: E402

_APP = None

CAL_TOKENS = (
    "cal_popup_bg", "cal_popup_edge", "cal_title", "cal_text", "cal_muted",
    "cal_weekday", "cal_nav_icon", "cal_hover_bg", "cal_divider",
    "cal_accent", "cal_on_accent",
)


@pytest.fixture(scope="module")
def qapp():
    """★ 必须用模块级全局持有 QApplication。

    局部变量版本的 ``QApplication([])`` 会被 GC 回收，之后任何
    ``QPixmap`` 操作直接 qFatal abort —— 表现为「pytest 静默硬崩、
    无 traceback」，本仓库已经栽过一次（见 MEMORY 记录）。
    """
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


@pytest.fixture
def popup(qapp):
    # 必须 show()：QTest.mouseClick 走的是窗口级事件投递，未显示的控件
    # 拿不到点击（会静默什么都不发生，测试变成永远通过的假护栏）。
    p = CalendarPopup(None, "light")
    p.configure(selected=(2026, 10, 2), year=2026, month=10)
    p.show()
    qapp.processEvents()
    yield p
    p.close()
    _hard_delete(qapp, p)


def _hard_delete(qapp, widget):
    """确定性拆除：deleteLater + **强制派发 DeferredDelete**。

    processEvents 不派发 DeferredDelete（MEMORY 已档），叠加 V7 起
    CalendarPopup 挂 hover 动画子对象后，「大量弹层 + DateField」组合
    在会话收尾段触发 0xC0000409 fail-fast（A/B 实测 HEAD 版 0 / V7 版
    127，两半分组各自绿、全量才崩的累积型）。这里显式清场，把 C++
    对象的销毁从「解释器收尾段的不确定时序」提前到 fixture 拆除点。
    """
    widget.deleteLater()
    from PyQt6.QtCore import QEvent
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def field(qapp):
    f = DateField()
    f.resize(150, 30)
    f.show()
    qapp.processEvents()
    yield f
    if f.popup is not None:
        f.popup.close()
    f.close()
    _hard_delete(qapp, f)


def _click(widget, pos):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=pos)


def _center(rect):
    return rect.center()


def _key(widget, key):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# ====================================================================
# A. 几何：命中区 == 绘制区
# ====================================================================
class TestHitRegions:
    def test_every_day_cell_is_clickable(self, popup):
        """42 格逐格验证：格子中心必须命中自己那一格，且下标一致。"""
        for i in range(42):
            hit = popup.hit_test(_center(popup._cell_rect(i)))
            assert hit == ("day", i), "第 %d 格命中成 %r" % (i, hit)

    def test_cell_centers_map_to_expected_dates(self, popup):
        """第 5 格 = 截图里被选中的 10-02。"""
        hit = popup.hit_test(_center(popup._cell_rect(4)))
        assert hit == ("day", 4)
        cell = popup.cells[hit[1]]
        assert cell[:3] == (2026, 10, 2)

    def test_nav_buttons_are_clickable(self, popup):
        assert popup.hit_test(_center(popup._nav_rect(0))) == ("prev_month",)
        assert popup.hit_test(_center(popup._nav_rect(1))) == ("next_month",)

    def test_footer_buttons_are_clickable(self, popup):
        assert popup.hit_test(_center(popup._clear_rect())) == ("clear",)
        assert popup.hit_test(_center(popup._today_rect())) == ("today",)

    def test_title_opens_month_view_region(self, popup):
        assert popup.hit_test(_center(popup._title_rect())) == ("title",)

    def test_title_and_nav_do_not_overlap(self, popup):
        """标题热区若压到箭头，点"翻月"会变成"打开月视图"——必须互斥。"""
        title = popup._title_rect()
        for i in (0, 1):
            nav = popup._nav_rect(i)
            assert not title.intersects(nav), (title, nav)

    def test_month_view_regions(self, popup):
        popup._activate(("title",))
        assert popup.mode == "month"
        for i in range(12):
            assert popup.hit_test(_center(popup._month_rect(i))) == ("month", i + 1)
        assert popup.hit_test(_center(popup._nav_rect(0))) == ("year_prev",)
        assert popup.hit_test(_center(popup._nav_rect(1))) == ("year_next",)

    def test_size_is_identical_in_both_views(self, popup):
        """两个视图同尺寸 —— 否则点 ▼ 时弹层会跳一下。"""
        assert popup.sizeHint() == QSize(POPUP_W + 2 * SHADOW,
                                         POPUP_H + 2 * SHADOW)
        before = popup.sizeHint()
        popup._activate(("title",))
        assert popup.sizeHint() == before

    def test_hit_test_accepts_point_and_tuple(self, popup):
        rect = popup._cell_rect(4)
        assert popup.hit_test(rect.center()) == popup.hit_test(
            (rect.center().x(), rect.center().y()))

    def test_outside_area_hits_nothing(self, popup):
        assert popup.hit_test((POPUP_W + 2 * SHADOW + 20, 10)) == ("none",)


# ====================================================================
# B. 行为
# ====================================================================
class TestPopupBehaviour:
    def test_clicking_a_day_emits_picked(self, popup):
        got = []
        popup.picked.connect(got.append)
        _click(popup, _center(popup._cell_rect(4)))
        assert [d.toString("yyyy-MM-dd") for d in got] == ["2026-10-02"]

    def test_clear_emits_cleared(self, popup):
        got = []
        popup.cleared.connect(lambda: got.append(True))
        _click(popup, _center(popup._clear_rect()))
        assert got == [True]

    def test_today_emits_current_date(self, popup):
        got = []
        popup.picked.connect(got.append)
        _click(popup, _center(popup._today_rect()))
        assert len(got) == 1
        assert got[0] == QDate.currentDate()

    def test_next_month_advances_and_refreshes_cells(self, popup):
        popup._activate(("next_month",))
        assert popup.shown_month == (2026, 11)
        assert popup.cells[0][:3] == (2026, 10, 26)      # 11 月的首个补位日

    def test_prev_month_goes_back_across_year(self):
        p = CalendarPopup(None, "light")
        p.configure(selected=None, year=2026, month=1)
        p._activate(("prev_month",))
        assert p.shown_month == (2025, 12)
        p.close()

    def test_title_toggles_between_views(self, popup):
        popup._activate(("title",))
        assert popup.mode == "month"
        popup._activate(("title",))
        assert popup.mode == "day"

    def test_picking_a_month_returns_to_day_view(self, popup):
        popup._activate(("title",))
        popup._activate(("month", 3))
        assert popup.mode == "day"
        assert popup.shown_month == (2026, 3)

    def test_year_arrows_in_month_view(self, popup):
        popup._activate(("title",))
        popup._activate(("year_next",))
        assert popup.shown_month[0] == 2027
        popup._activate(("year_prev",))
        assert popup.shown_month[0] == 2026

    def test_escape_closes(self, qapp, popup):
        popup.show()
        qapp.processEvents()
        assert popup.isVisible()
        _key(popup, Qt.Key.Key_Escape)
        assert not popup.isVisible()

    def test_page_down_changes_month(self, popup):
        _key(popup, Qt.Key.Key_PageDown)
        assert popup.shown_month == (2026, 11)

    def test_arrow_keys_move_selection(self, popup):
        popup._selected = (2026, 10, 2)
        _key(popup, Qt.Key.Key_Right)
        assert popup._selected == (2026, 10, 3)
        _key(popup, Qt.Key.Key_Down)
        assert popup._selected == (2026, 10, 10)

    def test_arrow_key_across_month_edge_moves_view(self, popup):
        popup._selected = (2026, 10, 31)
        _key(popup, Qt.Key.Key_Right)          # 11-01 是补位日
        assert popup.shown_month == (2026, 11)

    def test_mouse_move_updates_hover(self, popup):
        pos = _center(popup._cell_rect(9))
        posf = QPointF(pos.x(), pos.y())
        ev = QMouseEvent(QEvent.Type.MouseMove, posf,
                         Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        popup.mouseMoveEvent(ev)
        assert popup._hover == ("day", 9)
        popup.leaveEvent(QEvent(QEvent.Type.Leave))
        assert popup._hover == ("none",)

    def test_configure_accepts_none_selection(self, popup):
        popup.configure(selected=None, year=2026, month=10)
        assert popup._selected is None
        assert popup.shown_month == (2026, 10)

    def test_paint_renders_something(self, qapp, popup):
        popup.show()
        qapp.processEvents()
        pm = popup.grab()
        assert not pm.isNull()
        assert pm.width() == POPUP_W + 2 * SHADOW


# ====================================================================
# C. DateField（日期框）
# ====================================================================
class TestDateField:
    def test_default_value_is_today(self, field):
        assert not field.isEmpty()
        assert field.date() == QDate.currentDate()
        assert field.dateOrNone() == QDate.currentDate()

    def test_native_spin_buttons_and_popup_are_off(self, field):
        """原生微调钮与原生弹层都必须关掉 —— 否则两套弹层会叠出来。"""
        assert field.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
        assert field.calendarPopup() is False

    def test_display_format_matches_screenshot(self, field):
        assert field.displayFormat() == "yyyy/MM/dd"
        assert field.lineEdit().text() == QDate.currentDate().toString("yyyy/MM/dd")

    def test_line_edit_is_read_only(self, field):
        assert field.lineEdit().isReadOnly() is True

    def test_clear_makes_it_empty(self, field):
        field._on_cleared()
        assert field.isEmpty()
        assert field.dateOrNone() is None
        assert field.date() == SENTINEL_DATE

    def test_empty_shows_placeholder(self, field):
        field._on_cleared()
        assert field.lineEdit().text() == "选择日期"

    def test_set_date_or_none_roundtrip(self, field):
        field.setDateOrNone(QDate(2026, 10, 2))
        assert field.dateOrNone() == QDate(2026, 10, 2)
        field.setDateOrNone(None)
        assert field.dateOrNone() is None
        assert field.isEmpty()

    def test_click_opens_custom_popup(self, qapp, field):
        assert field.popup is None
        _click(field, QPoint(10, 10))
        assert field.popup is not None
        assert isinstance(field.popup, CalendarPopup)
        assert field.popup.isVisible()

    def test_popup_opens_on_selected_month(self, qapp, field):
        field.setDateOrNone(QDate(2026, 10, 2))
        _click(field, QPoint(10, 10))
        assert field.popup.shown_month == (2026, 10)

    def test_picking_a_date_updates_value_once(self, qapp, field):
        """★ 选一个**不等于今天**的格子。

        默认值是今天，若固定挑「本月 2 号」，跑到 2 号那天这条断言会变成
        「设成同一个值」→ Qt 不发信号 → 测试静默失效（假护栏）。
        """
        seen = []
        field.dateChanged.connect(lambda d: seen.append(d))
        _click(field, QPoint(10, 10))
        today = QDate.currentDate()
        today_key = (today.year(), today.month(), today.day())
        idx, cell = next(
            (i, c) for i, c in enumerate(field.popup.cells)
            if c[3] and c[:3] != today_key)
        field.popup._activate(("day", idx))
        expect = QDate(cell[0], cell[1], cell[2])
        assert field.dateOrNone() == expect
        assert len(seen) == 1, "dateChanged 应只发一次，实际 %d 次" % len(seen)

    def test_clearing_from_popup_updates_value_once(self, qapp, field):
        seen = []
        field.dateChanged.connect(lambda d: seen.append(d))
        _click(field, QPoint(10, 10))
        field.popup._activate(("clear",))
        assert field.dateOrNone() is None
        assert len(seen) == 1

    def test_clearing_an_already_empty_field_emits_nothing(self, qapp, field):
        field._on_cleared()
        seen = []
        field.dateChanged.connect(lambda d: seen.append(d))
        field._on_cleared()
        assert seen == []

    def test_keyboard_opens_popup(self, qapp, field):
        _key(field, Qt.Key.Key_Space)
        assert field.popup is not None and field.popup.isVisible()

    def test_hiding_field_closes_popup(self, qapp, field):
        field.show()
        _click(field, QPoint(10, 10))
        assert field.popup.isVisible()
        field.hide()
        assert not field.popup.isVisible()

    def test_theme_change_reaches_icon_and_popup(self, qapp, field):
        _click(field, QPoint(10, 10))
        # 先显式落到 light —— 无宿主时默认主题是 DEFAULT_THEME（当前为
        # dark），直接切 dark 会因为"主题没变"而看起来"图标不变色"
        field.apply_theme("light")
        light_pm = field._icon_btn.icon().pixmap(15, 15).toImage()
        field.apply_theme("dark")
        assert field._theme == "dark"
        assert field.popup._colors["cal_accent"] == THEMES["dark"]["cal_accent"]
        dark_pm = field._icon_btn.icon().pixmap(15, 15).toImage()
        assert light_pm != dark_pm, "换主题后日历图标没变色"
        field.apply_theme("light")
        assert field._icon_btn.icon().pixmap(15, 15).toImage() == light_pm

    def test_apply_theme_without_argument_reads_host(self, qapp):
        class Host:
            current_theme = "dark"

        f = DateField(Host())
        assert f._theme == "dark"
        Host.current_theme = "light"
        f.apply_theme()
        assert f._theme == "light"
        f.deleteLater()

    def test_explicit_theme_argument_wins(self, qapp):
        """小卡片没有 current_theme 可读，必须靠实参把主题带进来。"""
        f = DateField(theme="dark")
        assert f._theme == "dark"
        f.deleteLater()


# ====================================================================
# D. 主题令牌与 QSS
# ====================================================================
def _contrast(fg: str, bg: str) -> float:
    def lum(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        chans = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        lin = [(c / 12.92) if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in chans]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


class TestCalendarThemeTokens:
    def test_all_calendar_tokens_exist_in_both_themes(self):
        for theme in ("light", "dark"):
            missing = [t for t in CAL_TOKENS if not THEMES[theme].get(t)]
            assert not missing, "%s 主题缺少日历令牌：%s" % (theme, missing)

    def test_token_sets_are_symmetric(self):
        light = {t for t in THEMES["light"] if t.startswith("cal_")}
        dark = {t for t in THEMES["dark"] if t.startswith("cal_")}
        assert light == dark == set(CAL_TOKENS)

    def test_accent_literals_are_pinned(self):
        """★ 蓝色强调色是**用户拍板的决定**，不是随手取的值。

        防的是"顺手把它改成产品主色绿"或改成别的蓝：这个断言必须比较
        **写死的字面量**，不能写成「theme 里的值 == date_picker 读到的值」
        —— 后者两侧同源，永远为真（本仓库栽过这种假护栏）。
        """
        assert THEMES["light"]["cal_accent"] == "#1A73E8"
        assert THEMES["light"]["cal_on_accent"] == "#FFFFFF"
        assert THEMES["dark"]["cal_accent"] == "#8AB4F8"
        assert THEMES["dark"]["cal_on_accent"] == "#202124"

    def test_accent_contrast_meets_wcag(self):
        for theme in ("light", "dark"):
            c = THEMES[theme]
            ratio = _contrast(c["cal_on_accent"], c["cal_accent"])
            assert ratio >= 4.5, ("%s 主题选中日文字对比度仅 %.2f:1"
                                  % (theme, ratio))

    def test_readable_text_meets_wcag(self):
        """要"读清"的三种文字（当月日期 / 月标题 / 星期表头）都过 4.5:1。"""
        for theme in ("light", "dark"):
            c = THEMES[theme]
            for key in ("cal_text", "cal_title", "cal_weekday"):
                ratio = _contrast(c[key], c["cal_popup_bg"])
                assert ratio >= 4.5, ("%s 主题 %s 对比度仅 %.2f:1"
                                      % (theme, key, ratio))

    def test_out_of_month_days_are_deliberately_dimmer(self):
        """跨月补位日必须**比当月日、也比星期表头更浅**。

        这条比较的是两条对比度的大小关系而不是绝对阈值 —— 钉的是"层级
        关系"这个设计意图（Chromium 就是这个层级）。若有人为了凑 WCAG
        把 cal_muted 调成正文灰，这条会红，界面也就丢了"哪个月是本月"
        的视觉提示。
        """
        for theme in ("light", "dark"):
            c = THEMES[theme]
            bg = c["cal_popup_bg"]
            muted = _contrast(c["cal_muted"], bg)
            assert muted >= 2.5, "%s 补位日淡到看不见：%.2f:1" % (theme, muted)
            assert muted < _contrast(c["cal_text"], bg), theme
            assert muted < _contrast(c["cal_weekday"], bg), theme


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


class TestCalendarQss:
    def _bodies(self, qss, selector):
        """指定选择器的声明体（**先剥注释** —— 否则注释里写的 "30px"
        会让这条断言恒真，退化成假护栏）。"""
        out = []
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", qss):
            if selector in m.group(1):
                out.append(_COMMENT_RE.sub("", m.group(2)))
        return out

    @pytest.mark.parametrize("getter", [get_main_window_qss, get_card_window_qss])
    def test_date_field_reserves_room_for_the_icon(self, getter):
        """右侧 30px 内边距是日历图标钮的位置来源，缺了就会被文字压住。"""
        for theme in ("light", "dark"):
            bodies = self._bodies(getter(theme), "QDateEdit#taskDate")
            assert bodies, "QSS 缺少 QDateEdit#taskDate 规则"
            joined = " ".join(" ".join(b.split()) for b in bodies)
            assert re.search(r"padding\s*:\s*5px\s+30px\s+5px\s+10px", joined), (
                "%s/%s 的 QDateEdit#taskDate 未给日历图标留出右侧 30px：%r"
                % (theme, getter.__name__, joined))

    def test_native_dropdown_rule_is_gone(self):
        """原生下拉箭头/微调钮的 QSS 规则必须删干净 —— 留着就是假控件。

        （真正关掉它们的是 DateField 里的 setButtonSymbols(NoButtons) 与
        calendarPopup(False)；这条断言防的是"顺手把 QSS 规则加回来"。）
        """
        for theme in ("light", "dark"):
            joined = get_card_window_qss(theme) + get_main_window_qss(theme)
            assert "QDateEdit#taskDate::drop-down" not in joined
            assert "QDateEdit#taskDate::up-button" not in joined

    def test_popup_widget_is_not_styled_by_qss(self):
        """弹层是自绘的：它绝不能出现在 QSS 里（两套机制会打架）。

        断言前先剥注释 —— 注释里提到 "calendarPopup" 不算数，否则这条
        护栏会因为一行说明文字而恒假（真加规则时反而抓不住）。
        """
        for theme in ("light", "dark"):
            joined = _COMMENT_RE.sub(
                "", get_card_window_qss(theme) + get_main_window_qss(theme))
            assert "calendarPopup" not in joined

    def test_native_dropdown_removal_check_is_not_a_no_op(self):
        """反向验证：本类用的剥注释手法真的能识别出规则。

        取一条**确实存在**的规则名做正例，避免上一条断言因为"解析全错 ->
        永远为空"而恒真。
        """
        light = get_card_window_qss("light")
        bodies = self._bodies(light, "QDateEdit#taskDate")
        assert bodies, "解析器没抓到任何 QDateEdit#taskDate 规则"
        assert "QDateEdit#taskDate:focus" in light


class TestPublicSurface:
    def test_module_constants_are_sane(self):
        assert POPUP_W == 2 * 8 + 7 * date_picker.CELL_W
        assert POPUP_H == (date_picker.HEADER_H + date_picker.WEEKDAY_H
                           + 6 * date_picker.CELL_H + date_picker.FOOTER_H)
        assert SHADOW > 0

    def test_popup_is_owned_by_the_field(self, qapp, field):
        _click(field, QPoint(10, 10))
        assert field.popup.parent() is not None
        assert field.popup.objectName() == "calendarPopup"
