# -*- coding: utf-8 -*-
"""日期弹层「点击外部关闭」回归  -  test_date_picker_outside_close
====================================================================
背景（2026-10-02）：自绘日历弹层上线后，用户实测「点弹层旁边关不掉」。
根因有三层，本文件逐层钉死：

  1. **一次性开关被用掉**：``WA_NoMouseReplay`` 若只在 ``__init__`` 设
     一次，Qt 在一次「关闭弹层的点击」后会把它复位 —— 第二次打开后，
     外点关闭 + 点击重放会把弹层重新打开（点输入框 = 永远关不掉）。
     修复是每次 ``showEvent`` 重新装填；
  2. **8px 透明阴影边距**：窗口系统层把它算作「点在弹层内」，点击被
     ``hit_test=("none")`` 无声吞掉；修复是落在面板矩形外即关闭；
  3. **平台兜底**：``Qt.Popup`` 的窗口系统级外点关闭若因透明分层窗口
     等平台差异失效，弹层就漏关；修复是弹层可见期间挂应用级事件过滤
     器（锚点豁免，toggle 归 ``DateField`` 自己）。

测试口径（两组缺一不可）
========================
``QApplication.sendEvent`` 直达路径绕过窗口系统层，专门钉**应用级过
滤器**的契约；``QTest.mouseClick`` 走窗口系统接口，钉**真实外点路径**
（与实机行为同源）。只测前者的话，``Qt.Popup`` 那层坏了测试照样全绿
—— 那正是这次用户实测才暴露的盲区。
====================================================================
"""

import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (                                            # noqa: E402
    QEvent, QPointF, QPoint, QRectF, Qt,
)
from PyQt6.QtGui import QMouseEvent                                   # noqa: E402
from PyQt6.QtTest import QTest                                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from src.date_picker import (                                         # noqa: E402
    POPUP_H, POPUP_W, SHADOW, CalendarPopup, DateField,
)

_APP = None


@pytest.fixture(scope="module")
def qapp():
    """★ 模块级全局持有 QApplication（局部变量版被 GC 后 QPixmap qFatal，
    表现为 pytest 静默硬崩 —— 本仓库已栽过一次，见 MEMORY）。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


@pytest.fixture
def popup(qapp):
    """无锚点的独立弹层（standalone：外点一律关闭）。"""
    p = CalendarPopup(None, "light")
    p.configure(selected=(2026, 10, 2), year=2026, month=10)
    p.show()
    qapp.processEvents()
    yield p
    p.close()
    p.deleteLater()
    qapp.processEvents()


@pytest.fixture
def field_rig(qapp):
    """宿主窗口 + DateField + 旁边一个普通按钮（模拟任务页布局）。"""
    host = QWidget()
    host.resize(400, 300)
    lay = QVBoxLayout(host)
    field = DateField(host, theme="light")
    btn = QPushButton("旁边按钮")
    lay.addWidget(field)
    lay.addWidget(btn)
    host.show()
    qapp.processEvents()
    yield host, field, btn
    if field.popup is not None:
        field.popup.close()
    host.hide()
    host.deleteLater()
    qapp.processEvents()


def _pump(qapp):
    for _ in range(6):
        qapp.processEvents()


def _send_press(target, pos=None):
    """向控件直达投递一次左键按下（绕过窗口系统层）。"""
    point = QPointF(pos) if pos is not None else QPointF(5, 5)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, point,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, ev)


def _click(qapp, widget, pos):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=pos)
    _pump(qapp)


# 面板矩形（窗口坐标）与两个探针点：margin 在面板外、gap 在面板内空隙
_PANEL = QRectF(SHADOW, SHADOW, POPUP_W, POPUP_H)
_MARGIN_POS = QPoint(2, 2)
_GAP_POS = QPoint(150, 45)          # 星期表头行的空白处


class TestMarginAndGap:
    """8px 透明阴影边距：用户视角的「弹层旁边」，必须能关闭。"""

    def test_margin_point_is_outside_panel(self):
        """前提自检：探针点真在面板外（布局常量改动时这里先红）。"""
        assert not _PANEL.contains(QPointF(_MARGIN_POS))

    def test_gap_point_is_inside_panel_but_hits_nothing(self, popup):
        """前提自检：gap 点在面板内且不命中任何控件（不是误点的按钮）。"""
        assert _PANEL.contains(QPointF(_GAP_POS))
        assert popup.hit_test(_GAP_POS) == ("none",)

    def test_click_margin_closes(self, qapp, popup):
        _click(qapp, popup, _MARGIN_POS)
        assert not popup.isVisible()

    def test_click_gap_inside_panel_keeps_open(self, qapp, popup):
        _click(qapp, popup, _GAP_POS)
        assert popup.isVisible()

    def test_no_mousereplay_pinned_at_construction(self, popup):
        """防重放是外点关闭语义的一半，构造时就必须带上。"""
        assert popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)


class TestAppFilterFallback:
    """应用级兜底过滤器（sendEvent 直达，绕过窗口系统层）。"""

    def test_press_on_unrelated_widget_closes(self, qapp, popup):
        dummy = QPushButton("dummy")
        dummy.show()
        _pump(qapp)
        _send_press(dummy)
        _pump(qapp)
        assert not popup.isVisible()
        dummy.deleteLater()

    def test_filter_inactive_after_hidden(self, qapp, popup):
        popup.close()
        _pump(qapp)
        dummy = QPushButton("dummy")
        _send_press(dummy)          # 弹层已关，过滤器必须已卸载：不关任何东西
        assert not popup.isVisible()
        dummy.deleteLater()

    def test_press_inside_popup_not_hijacked(self, qapp, popup):
        """弹层内部点击必须照常工作（过滤器不能劫持自家事件）。"""
        got = []
        popup.picked.connect(got.append)
        # 第 4 格中心（窗口坐标 = 布局坐标 + SHADOW 偏移），选非今天防假护栏
        cx = SHADOW + 8 + 4 * 40 + 20
        cy = SHADOW + 34 + 24 + 15
        _send_press(popup, QPointF(cx, cy))
        _pump(qapp)
        assert len(got) == 1, "弹层内点日期应正常发出 picked"
        assert not popup.isVisible()

    def test_anchor_press_toggles_close(self, qapp, field_rig):
        """点锚点（输入框）= 关闭，由 DateField 自己的 toggle 逻辑完成。"""
        _, field, _btn = field_rig
        field.open_popup()
        _pump(qapp)
        assert field.popup.isVisible()
        _send_press(field)
        _pump(qapp)
        assert not field.popup.isVisible()

    def test_rearm_after_closing_click(self, qapp, popup):
        """★ 一次性开关被用掉后，下次打开必须重新装填。

        一次「关闭弹层的点击」会把 WA_NoMouseReplay 复位（Qt 一次性
        语义，实测钉死）：不重装填的话，第二次打开后点输入框就是
        「关闭→重放→重开」——用户报告的原始 bug。
        """
        _click(qapp, popup, _MARGIN_POS)
        assert not popup.isVisible()
        assert not popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay), (
            "前提失效：这次关闭点击应当把一次性开关用掉，"
            "若不复位说明 Qt 行为变了，本测试与 showEvent 重装填都要重审")
        popup.show()
        _pump(qapp)
        assert popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay), (
            "showEvent 没有重新装填 WA_NoMouseReplay")


class TestDateFieldIntegration:
    """QTest 真实路径（窗口系统层参与，与实机同源）。"""

    def test_click_field_while_open_closes_and_stays_closed(self, qapp, field_rig):
        """★ 用户原始 bug 的回归钉子：点输入框必须关掉且不被重放顶开。"""
        _host, field, _btn = field_rig
        field.open_popup()
        _pump(qapp)
        assert field.popup.isVisible()
        _click(qapp, field, QPoint(10, 10))
        assert not field.popup.isVisible()

    def test_sibling_button_click_closes(self, qapp, field_rig):
        _host, field, btn = field_rig
        field.open_popup()
        _pump(qapp)
        _click(qapp, btn, QPoint(5, 5))
        assert not field.popup.isVisible()

    def test_roundtrip_open_close_open(self, qapp, field_rig):
        """关过几轮之后依旧能正常打开（状态机没有越关越坏）。"""
        _host, field, btn = field_rig
        for _ in range(3):
            field.open_popup()
            _pump(qapp)
            assert field.popup.isVisible()
            _click(qapp, btn, QPoint(5, 5))
            assert not field.popup.isVisible()
        field.open_popup()
        _pump(qapp)
        assert field.popup.isVisible()
        assert field.popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)

    def test_anchor_is_wired(self, qapp, field_rig):
        """锚点豁免依赖 set_anchor 接线，漏接则点输入框会被过滤器先关。"""
        _host, field, _btn = field_rig
        field.open_popup()
        _pump(qapp)
        assert field.popup._anchor is field
