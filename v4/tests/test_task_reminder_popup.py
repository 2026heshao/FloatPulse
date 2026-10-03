# -*- coding: utf-8 -*-
"""
任务提醒自绘弹窗回归  -  test_task_reminder_popup
====================================================================
背景（2026-10-02 用户点名重设计）：任务提醒（启动 +4s 与每日 9:00，
三桶口径）此前走原生托盘气泡（``QSystemTrayIcon.showMessage`` → 系统
toast），样式不可控、观感差。现换自绘顶层卡片 ``task_reminder_popup``。

本文件钉四层契约：
  A. 纯逻辑：build_rows 分桶展示行（空桶跳过 / 每桶封顶 + 「另有 N 项」）
     与 elide_title 省略；
  B. 内容：configure 后计数 / 徽标 / 溢出提示如实渲染，语气徽标随
     「是否有逾期」在 danger / primary 之间切换；
  C. 行为：「打开任务页」与点卡片空白处发 open_requested 并收起；
     「知道了」/ 关闭钮 / 倒计时到点收起发 dismissed；悬停暂停倒计时
     （记剩余量）；单例复用不堆叠；右下角贴边定位；
  D. 回退钉子：knowledge_ball._check_task_reminders 不得再调原生气泡
     （AST 扫 show_message），且必须走 task_reminder_popup.show_reminder；
     主窗 QSS 必须带 #taskReminderCard 骨架规则。
====================================================================
"""

import ast
import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (
    QAbstractAnimation, QEvent, QPointF, Qt,
)
from PyQt6.QtGui import QEnterEvent, QFontMetrics
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from src import motion
from src.task_reminder_popup import (
    AUTO_DISMISS_MS, CARD_W, PER_BUCKET, SHADOW, TaskReminderPopup,
    build_rows, elide_title, show_reminder,
)
from src.theme import get_main_window_qss

_APP = None


def _app():
    """取（必要时创建）QApplication，引用留模块级（test_icons 同款），
    防止 PyQt6 包装对象被回收后 QPixmap/qFatal 静默崩进程。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def popup():
    """单例弹窗 + reduce_motion（测试要确定性终态，动画另行专项测）。"""
    _app()
    motion.set_reduce_motion(True)
    inst = TaskReminderPopup.popup_instance()
    yield inst
    inst.hide()
    motion.set_reduce_motion(False)


def _configure(inst, overdue=("交房租", "回复王老师邮件"),
               due=("50 俯卧撑",), undated=("整理桌面文件", "预约体检", "买墨盒")):
    return inst.configure(list(overdue), list(due), list(undated),
                          theme="light")


def _labels_by_name(inst, name):
    return [w for w in inst.findChildren(QLabel) if w.objectName() == name]


def _enter_event():
    return QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5))


# ====================================================================
# A. 纯逻辑
# ====================================================================
def test_build_rows_capping_and_overflow():
    rows = build_rows(["a", "b", "c", "d", "e"], [], [],
                      per_bucket=PER_BUCKET)
    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "overdue" and row["tone"] == "danger"
    assert row["count"] == 5 and row["extra"] == 2
    assert row["shown"] == ["a", "b", "c"]


def test_build_rows_skips_empty_buckets_keeps_order():
    rows = build_rows([], ["今日"], [])
    assert [r["key"] for r in rows] == ["due"]
    assert rows[0]["tone"] == "primary" and rows[0]["extra"] == 0
    rows = build_rows([], [], ["x", "y"])
    assert [r["key"] for r in rows] == ["undated"]
    assert rows[0]["tone"] == "muted"


def test_build_rows_all_empty():
    assert build_rows([], [], []) == []


def test_elide_title_short_circumvents_and_long_elides():
    fm = QFontMetrics(_app().font())
    assert elide_title("短标题", fm, 100) == "短标题"
    long_text = "超长任务标题" * 40
    elided = elide_title(long_text, fm, 100)
    assert elided != long_text and elided.endswith("…")


# ====================================================================
# B. 内容渲染
# ====================================================================
def test_configure_renders_counts_chip_and_order(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    counts = [w.text() for w in _labels_by_name(popup, "taskRemindCount")]
    assert counts == ["2", "1", "3"]
    heads = popup._head.text() == "任务提醒"
    assert heads
    assert popup._chip.text() == "6 项未完成"
    titles = [w.text() for w in _labels_by_name(popup, "taskRemindTitle")]
    assert titles[0] == "交房租" and "50 俯卧撑" in titles


def test_chip_tone_danger_only_with_overdue(popup):
    # 语气底色是 rgba 令牌在 $surface 上的合成值（_alpha_over 手拆算术）：
    #   danger_alpha rgba(163,45,45,.10) + #FFFFFF → #F6EAEA
    #   primary_a12 rgba(15,110,86,.12) + #FFFFFF → #E2EEEB
    # 钉字面量：语气/令牌值漂移会在此红灯，改值须过肉眼校验。
    _configure(popup)                       # 有逾期 → danger 色系
    assert "#F6EAEA" in popup._chip.styleSheet()
    _configure(popup, overdue=())           # 无逾期 → primary 色系
    assert "#E2EEEB" in popup._chip.styleSheet()
    assert "#F6EAEA" not in popup._chip.styleSheet()


def test_overflow_hint_rendered(popup):
    _configure(popup, overdue=("a", "b", "c", "d"))
    popup.show()
    QApplication.processEvents()
    overs = [w.text() for w in _labels_by_name(popup, "taskRemindOverflow")]
    assert overs == ["…另有 1 项"]


def test_long_title_elided_with_tooltip(popup):
    long_title = "很长的任务标题" * 30
    _configure(popup, due=(long_title,))
    popup.show()
    QApplication.processEvents()
    title_labels = [w for w in _labels_by_name(popup, "taskRemindTitle")
                    if w.toolTip() == long_title]
    assert len(title_labels) == 1
    assert title_labels[0].text().endswith("…")
    assert title_labels[0].text() != long_title


# ====================================================================
# C. 行为
# ====================================================================
def test_open_button_emits_open_requested_and_closes(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    got = []
    popup.open_requested.connect(lambda: got.append(1))
    QTest.mouseClick(popup._open_btn, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert got == [1]
    assert not popup.isVisible(), "打开任务页后弹窗应收起"


def test_later_button_dismisses_with_signal(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    got = []
    popup.dismissed.connect(lambda: got.append(1))
    QTest.mouseClick(popup._later_btn, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert got == [1]
    assert not popup.isVisible()


def test_card_body_click_opens(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    got = []
    popup.open_requested.connect(lambda: got.append(1))
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton,
                     pos=popup.rect().center())
    QApplication.processEvents()
    assert got == [1]


def test_auto_dismiss_timer_fires(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    got = []
    popup.dismissed.connect(lambda: got.append(1))
    popup._timer.start(50)                  # 缩短倒计时模拟到点
    QTest.qWait(400)
    assert got == [1] and not popup.isVisible()


def test_hover_pause_stops_and_resume_rearms(popup):
    _configure(popup)
    popup._arm(5000)
    assert popup._timer.isActive()
    popup.enterEvent(_enter_event())
    assert not popup._timer.isActive(), "悬停应暂停倒计时"
    assert 0 < popup._remaining <= 5000, "暂停时应记录剩余量（≤武装值）"
    popup.leaveEvent(QEvent(QEvent.Type.Leave))
    assert popup._timer.isActive(), "移开应从剩余量续时"
    assert popup._timer.remainingTime() <= popup._remaining


def test_auto_dismiss_constant_is_user_approved_value():
    """12 秒自动收起 + 悬停暂停是用户拍板口径，防止被顺手改掉。"""
    assert AUTO_DISMISS_MS == 12_000


def test_singleton_reuse_no_stack(popup):
    inst = show_reminder(["a"], ["b"], ["c"], theme="light")
    assert inst is TaskReminderPopup.popup_instance()
    again = show_reminder(["d"], [], ["e"], theme="light")
    assert again is inst
    assert inst.isVisible(), "重复弹应换内容复用同一实例"


def test_position_bottom_right_inside_screen(popup):
    _configure(popup)
    popup.show()
    QApplication.processEvents()
    from PyQt6.QtGui import QGuiApplication
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert popup.width() == CARD_W + 2 * SHADOW
    assert popup.geometry().right() <= avail.right()
    assert popup.geometry().bottom() <= avail.bottom()
    assert popup.x() > avail.center().x(), "应贴屏幕右半区"


def test_animated_show_runs_fade_and_pos():
    _app()
    motion.set_reduce_motion(False)
    try:
        inst = show_reminder(["a"], ["b"], ["c"], theme="light")
        assert inst._fade_anim.state() == QAbstractAnimation.State.Running
        assert inst._pos_anim.state() == QAbstractAnimation.State.Running
        assert inst.isVisible()
        inst.dismiss()
        inst._fade_anim.setCurrentTime(inst._fade_anim.duration())
        inst._pos_anim.setCurrentTime(inst._pos_anim.duration())
        QApplication.processEvents()
        assert not inst.isVisible()
    finally:
        motion.set_reduce_motion(False)


def test_theme_dark_reapplies_palette(popup):
    popup.configure(["a"], ["b"], ["c"], theme="dark")
    assert popup._theme == "dark"
    assert popup.styleSheet() == get_main_window_qss("dark")


# ====================================================================
# D. 回退钉子（QSS 契约 + AST 源码钉）
# ====================================================================
RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def _rules(qss):
    for m in RULE_RE.finditer(qss):
        # 选择器也要剥注释：裸放在规则前的注释会被并进"选择器"
        sel = COMMENT_RE.sub("", m.group(1))
        yield " ".join(sel.split()), COMMENT_RE.sub("", m.group(2))


def test_main_qss_has_reminder_card_skeleton():
    bodies = dict(_rules(get_main_window_qss("light")))
    for sel in ("QFrame#taskReminderCard", "QLabel#taskRemindHead",
                "QLabel#taskRemindTitle", "QLabel#taskRemindCount",
                "QLabel#taskRemindOverflow", "QFrame#taskRemindFoot"):
        assert sel in bodies, "任务提醒弹窗骨架规则 %s 缺失" % sel
    assert "background-color" in bodies["QFrame#taskReminderCard"]
    assert "border-top" in bodies["QFrame#taskRemindFoot"]


def test_knowledge_ball_no_longer_uses_native_balloon():
    """回退钉子：任务提醒不得再走原生托盘气泡（AST 扫描，防倒退）。"""
    src_path = os.path.join(BASE, "knowledge_ball.py")
    with open(src_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == "_check_task_reminders":
            fn = node
            break
    assert fn is not None, "_check_task_reminders 函数不见了？"
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "show_message" not in attrs, (
        "任务提醒不得再调 tray.show_message（原生气泡已退役）")
    assert "show_reminder" in attrs, "应调用 task_reminder_popup.show_reminder"
    assert "bucket_unfinished" in {n.id for n in ast.walk(fn)
                                   if isinstance(n, ast.Name)}, \
        "三桶口径必须保留"
