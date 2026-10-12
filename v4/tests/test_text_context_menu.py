# -*- coding: utf-8 -*-
"""文本控件中文右键菜单回归（2026-10-12 用户报障）。

背景
----
用户在多个窗口右键都弹出 Qt 自带的英文标准菜单（``Undo / Redo / Cut /
Copy / Paste / Delete / Select All``），实测它 ``styleSheet()`` 长度为 0
（应用主题覆盖不到），且空控件下几乎全灰 —— 即用户所说的"没有实质作用"。
本文件钉住接手后的行为契约，防止回退成系统菜单。

钉法（三层）
------------
1. **菜单内容契约**：中文标签、项序、分隔符、快捷键后缀；
2. **能力差异化**：只读控件只留复制/全选；**密码回显控件不得出现剪切/
   复制**（安全闸门：Qt 标准菜单在密码态同样禁用二者，本模块必须保持
   同等约束，否则改造会引入泄密通道）；不适用项**保留但置灰**；
3. **真实驱动**：经应用级事件过滤器真投递 ContextMenu 事件，断言事件被
   消费（Qt 不再弹英文菜单）且动作真改数据。

为什么坚持真投递而不是只看 ``build_text_menu`` 的返回
------------------------------------------------------
本模块的价值一半在"接得住事件"：过滤器的解析、放行分支（CustomContextMenu
/ NoContextMenu / 非文本控件）与主题父链解析，都只在真事件路径上才走到。
只断言菜单对象会漏掉整条接线（正是本次报障的形态：菜单"有"，但没用）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from src import text_context_menu as tcm  # noqa: E402

_APP = None


def _app():
    """取（必要时创建）QApplication，引用留模块级防 GC（见 test_icons 教训）。"""
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _labels(menu):
    out = []
    for a in menu.actions():
        if a.isSeparator():
            out.append("<sep>")
            continue
        out.append(a.text().replace("&", "").split("\t")[0])
    return out


def _find(menu, label):
    for a in menu.actions():
        if a.isSeparator():
            continue
        if a.text().replace("&", "").split("\t")[0] == label:
            return a
    return None


def _trigger(menu, label) -> bool:
    act = _find(menu, label)
    if act is None:
        return False
    act.trigger()
    return True


FULL_MENU = ["撤销", "重做", "<sep>", "剪切", "复制", "粘贴", "删除",
             "<sep>", "全选"]


# ====================================================================
# 1. 菜单内容契约
# ====================================================================
def test_editable_line_edit_has_chinese_full_menu_and_key_hints():
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abc")
    menu = tcm.build_text_menu(le)
    assert _labels(menu) == FULL_MENU
    # 快捷键按 Qt 口径以 \t 后缀右对齐展示
    assert _find(menu, "撤销").text().endswith("\tCtrl+Z")
    assert _find(menu, "全选").text().endswith("\tCtrl+A")


def test_menu_has_no_english_leftovers():
    """回退成 Qt 标准菜单时，这批英文标签会立刻出现。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abc")
    labels = set(_labels(tcm.build_text_menu(le)))
    for english in ("Undo", "Redo", "Cut", "Copy", "Paste", "Delete",
                    "Select All"):
        assert english not in labels


def test_menu_carries_app_theme_qss_not_system_default():
    """主题 QSS 必须落到菜单上（用户报障的"白底英文"根因之一）。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abc")
    assert len(tcm.build_text_menu(le).styleSheet()) > 100


@pytest.mark.parametrize("text,expect_selection", [
    ("abc", False), ("", False), ("abc", True),
])
def test_select_all_enabled_only_with_content(text, expect_selection):
    """「全选」只在有内容时可用（空控件下 Qt 原本也是置灰的）。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText(text)
    if expect_selection:
        le.selectAll()
    act = _find(tcm.build_text_menu(le), "全选")
    assert act.isEnabled() is bool(text)


# ====================================================================
# 2. 能力差异化（安全闸门 + 保留但置灰）
# ====================================================================
def test_read_only_text_edit_only_offers_copy_and_select_all():
    """只读控件（揭示原文 / 使用说明 / 知识库结果）不出现改写类动作。"""
    from PyQt6.QtWidgets import QTextEdit
    _app()
    ro = QTextEdit()
    ro.setReadOnly(True)
    ro.setPlainText("只读正文")
    ro.selectAll()
    menu = tcm.build_text_menu(ro)
    assert _labels(menu) == ["复制", "<sep>", "全选"]
    assert _find(menu, "复制").isEnabled() is True


def test_read_only_copy_disabled_without_selection():
    from PyQt6.QtWidgets import QTextEdit
    _app()
    ro = QTextEdit()
    ro.setReadOnly(True)
    ro.setPlainText("只读正文")
    ro.textCursor().clearSelection()
    assert _find(tcm.build_text_menu(ro), "复制").isEnabled() is False


@pytest.mark.parametrize("mode_name", ["Password", "NoEcho"])
def test_secret_field_never_offers_cut_or_copy(mode_name):
    """★ 安全闸门：密码 / 无回显控件不得出现「剪切」「复制」。

    Qt 标准菜单在密码态同样禁用二者；本模块若因为"统一中文菜单"而放开，
    就把回显掩码下的明文暴露成菜单可达能力。此项为硬约束。
    """
    from PyQt6.QtWidgets import QLineEdit
    _app()
    pw = QLineEdit()
    pw.setEchoMode(getattr(QLineEdit.EchoMode, mode_name))
    pw.setText("TOPSECRET")
    pw.selectAll()
    labels = _labels(tcm.build_text_menu(pw))
    assert "剪切" not in labels
    assert "复制" not in labels
    # 其余能力照常保留
    assert labels == ["撤销", "重做", "<sep>", "粘贴", "删除",
                      "<sep>", "全选"]


def test_normal_field_does_offer_cut_and_copy():
    """反例对照：普通回显控件必须保留剪切 / 复制（防"一刀切禁掉"）。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abc")
    le.selectAll()
    labels = _labels(tcm.build_text_menu(le))
    assert "剪切" in labels and "复制" in labels


def test_unavailable_actions_stay_visible_but_disabled():
    """用户口径：不适用项保留但置灰（不隐藏），菜单结构稳定。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abc")
    le.deselect()
    menu = tcm.build_text_menu(le)
    assert _labels(menu) == FULL_MENU
    assert _find(menu, "复制").isEnabled() is False
    assert _find(menu, "剪切").isEnabled() is False


def test_readonly_secret_field_has_no_leading_separator():
    """★ 只读 + 密码回显：上方两组**都为空**，菜单不得以悬空分隔线开头。

    当前仓内无此类控件，但「只读」与「密码」两判据组合可达 —— 无条件
    ``addSeparator()`` 会渲染出一条开头分隔线，故分隔线改为按组内容条件化。
    """
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setReadOnly(True)
    le.setEchoMode(QLineEdit.EchoMode.Password)
    le.setText("TOPSECRET")
    labels = _labels(tcm.build_text_menu(le))
    assert labels == ["全选"], f"只读+密码态菜单异常：{labels}"
    assert labels[0] != "<sep>"


def test_every_menu_starts_and_ends_with_a_real_action():
    """通杀断言：任何能力组合下菜单都不得以分隔线开头/结尾。"""
    from PyQt6.QtWidgets import QLineEdit, QTextEdit
    _app()
    samples = []
    for ro in (False, True):
        for mode in ("Normal", "Password"):
            le = QLineEdit()
            le.setReadOnly(ro)
            le.setEchoMode(getattr(QLineEdit.EchoMode, mode))
            le.setText("content")
            samples.append(le)
    for ro in (False, True):
        te = QTextEdit()
        te.setReadOnly(ro)
        te.setPlainText("content")
        samples.append(te)
    for w in samples:
        acts = tcm.build_text_menu(w).actions()
        assert acts, f"{type(w).__name__} 菜单为空"
        assert not acts[0].isSeparator(), "菜单以分隔线开头"
        assert not acts[-1].isSeparator(), "菜单以分隔线结尾"
        # 连续两条分隔线同样是渲染缺陷
        for a, b in zip(acts, acts[1:]):
            assert not (a.isSeparator() and b.isSeparator()), "出现连续分隔线"


# ====================================================================
# 3. 动作分发真改数据
# ====================================================================
def test_copy_action_writes_selection_to_clipboard():
    from PyQt6.QtWidgets import QApplication, QTextEdit
    _app()
    te = QTextEdit()
    te.setPlainText("复制这段文本")
    te.selectAll()
    QApplication.clipboard().clear()
    _trigger(tcm.build_text_menu(te), "复制")
    assert QApplication.clipboard().text() == "复制这段文本"
    QApplication.clipboard().clear()      # 释放 OLE 引用防 teardown 崩溃


def test_paste_action_inserts_clipboard_text():
    from PyQt6.QtWidgets import QApplication, QLineEdit
    _app()
    QApplication.clipboard().setText("粘贴内容")
    le = QLineEdit()
    le.selectAll()
    # 菜单是构建时快照：剪贴板就绪后再建，才反映「粘贴可用」
    _trigger(tcm.build_text_menu(le), "粘贴")
    assert le.text() == "粘贴内容"
    QApplication.clipboard().clear()


def test_delete_action_removes_selection():
    from PyQt6.QtWidgets import QTextEdit
    _app()
    te = QTextEdit()
    te.setPlainText("abcdef")
    te.selectAll()
    _trigger(tcm.build_text_menu(te), "删除")
    assert te.toPlainText() == ""


def test_delete_action_removes_one_char_without_selection():
    """无选区时删除光标处一个字符（QTextEdit 家族无 del_，走 QTextCursor）。"""
    from PyQt6.QtWidgets import QTextEdit
    _app()
    te = QTextEdit()
    te.setPlainText("abcdef")
    cur = te.textCursor()
    cur.setPosition(1)
    te.setTextCursor(cur)
    _trigger(tcm.build_text_menu(te), "删除")
    assert te.toPlainText() == "acdef"


def test_line_edit_delete_uses_del_underscore():
    """QLineEdit 有 del_()（实测），走它而不是 QTextCursor 分支。"""
    from PyQt6.QtWidgets import QLineEdit
    _app()
    le = QLineEdit()
    le.setText("abcdef")
    le.setCursorPosition(1)
    _trigger(tcm.build_text_menu(le), "删除")
    assert le.text() == "acdef"


def test_select_all_action_selects_everything():
    from PyQt6.QtWidgets import QTextEdit
    _app()
    te = QTextEdit()
    te.setPlainText("abcdef")
    te.textCursor().clearSelection()
    _trigger(tcm.build_text_menu(te), "全选")
    assert te.textCursor().hasSelection()


# ====================================================================
# 4. 事件接管与放行（真投递）
# ====================================================================
def _ctx_event():
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QContextMenuEvent
    return QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                             QPoint(5, 5), QPoint(100, 100))


class _ExecSpy:
    """把 QMenu.exec 打桩成记录而不真弹（离屏真 exec 会挂）。"""

    def __init__(self):
        self.shown = []

    def __enter__(self):
        from PyQt6.QtWidgets import QMenu
        self._orig = QMenu.exec
        QMenu.exec = lambda menu, *a, **k: self.shown.append(menu)
        return self

    def __exit__(self, *exc):
        from PyQt6.QtWidgets import QMenu
        QMenu.exec = self._orig
        return False


class _HandlerSpy:
    """子类化真控件，记录**控件自身 contextMenuEvent** 是否被调用。

    ★ 这是"事件被消费"的有效判据：``QContextMenuEvent`` 新建时
    ``isAccepted()`` 即为 True，只看 ``ev.isAccepted()`` 恒真（假绿）。
    真正决定 Qt 是否还会弹自带英文菜单的是过滤器返回值 —— 返回 False 时
    控件自身的 handler 会被调用（实测）。故此处直接盯 handler。
    """

    @staticmethod
    def make():
        from PyQt6.QtWidgets import QLineEdit

        class _Probe(QLineEdit):
            def __init__(self):
                super().__init__()
                self.handler_calls = 0

            def contextMenuEvent(self, event):      # noqa: N802 (Qt 命名)
                self.handler_calls += 1
                # 刻意不调 super()：离屏下真弹菜单会挂
                event.accept()

        return _Probe()


def test_filter_consumes_event_so_qt_never_shows_its_own_menu():
    """接得住事件 + **真消费**（否则 Qt 会再弹一次英文标准菜单）。

    以「控件自身 contextMenuEvent 未被调用」为判据，而不是恒真的
    ``ev.isAccepted()``（该事件默认即 accepted）。
    """
    _app()
    filt = tcm.install_text_context_menu()
    probe = _HandlerSpy.make()
    probe.setText("abc")
    with _ExecSpy() as spy:
        _app().sendEvent(probe, _ctx_event())
        assert len(spy.shown) == 1
        assert _labels(spy.shown[0]) == FULL_MENU
    assert probe.handler_calls == 0, \
        "控件自身 handler 被调用 = Qt 还会弹自带的英文标准菜单"
    filt.uninstall()


def test_filter_handles_scrollarea_viewport_target():
    """QTextEdit 的 ContextMenu 先到 viewport（裸 QWidget），须回升到控件。"""
    from PyQt6.QtWidgets import QTextEdit
    _app()
    filt = tcm.install_text_context_menu()
    te = QTextEdit()
    te.setPlainText("abc")
    with _ExecSpy() as spy:
        _app().sendEvent(te.viewport(), _ctx_event())
        assert len(spy.shown) == 1
    filt.uninstall()


def test_resolve_text_widget_returns_none_for_non_text():
    from PyQt6.QtWidgets import QWidget
    _app()
    assert tcm.resolve_text_widget(QWidget()) is None


def test_filter_skips_custom_context_menu_policy():
    """控件自带右键菜单（列表类）时不许抢。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLineEdit
    _app()
    filt = tcm.install_text_context_menu()
    le = QLineEdit()
    le.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    with _ExecSpy() as spy:
        _app().sendEvent(le, _ctx_event())
        assert spy.shown == []
    filt.uninstall()


def test_filter_skips_no_context_menu_policy():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLineEdit
    _app()
    filt = tcm.install_text_context_menu()
    le = QLineEdit()
    le.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    with _ExecSpy() as spy:
        _app().sendEvent(le, _ctx_event())
        assert spy.shown == []
    filt.uninstall()


def test_filter_skips_disabled_widget():
    from PyQt6.QtWidgets import QLineEdit
    _app()
    filt = tcm.install_text_context_menu()
    le = QLineEdit()
    le.setEnabled(False)
    with _ExecSpy() as spy:
        _app().sendEvent(le, _ctx_event())
        assert spy.shown == []
    filt.uninstall()


def test_install_is_idempotent_and_uninstall_detaches():
    _app()
    a = tcm.install_text_context_menu()
    b = tcm.install_text_context_menu()
    assert a is b and a.installed
    tcm.uninstall_text_context_menu()
    assert not a.installed


# ====================================================================
# 5. 主题解析
# ====================================================================
def test_resolve_theme_reads_light_and_dark_from_parent_chain():
    from PyQt6.QtWidgets import QLineEdit, QWidget
    _app()
    host = QWidget()
    host._theme = "dark"
    le = QLineEdit(host)
    assert tcm.resolve_theme(le) == "dark"
    host._theme = "light"
    assert tcm.resolve_theme(le) == "light"


def test_resolve_theme_ignores_illegal_value_and_keeps_climbing():
    """链上出现同名但取值非法的属性不许误判（沿用 IconButton._detect_theme 口径）。"""
    from PyQt6.QtWidgets import QLineEdit, QWidget
    _app()
    outer = QWidget()
    outer._theme = "dark"
    inner = QWidget(outer)
    inner._theme = "blue-ish"
    le = QLineEdit(inner)
    assert tcm.resolve_theme(le) == "dark"


def test_resolve_theme_falls_back_to_default_without_host():
    from PyQt6.QtWidgets import QLineEdit
    from src.constants import DEFAULT_THEME
    _app()
    assert tcm.resolve_theme(QLineEdit()) == DEFAULT_THEME


def test_theme_qss_differs_between_light_and_dark():
    from PyQt6.QtWidgets import QLineEdit, QWidget
    _app()
    host = QWidget()
    host._theme = "light"
    le = QLineEdit(host)
    light = tcm.build_text_menu(le).styleSheet()
    host._theme = "dark"
    dark = tcm.build_text_menu(le).styleSheet()
    assert light != dark and len(dark) > 100


# ====================================================================
# 6. 装配接线（防"写了模块没接上"）
# ====================================================================
def test_wiring_exposes_installer():
    from src import wiring
    assert callable(wiring.wire_text_context_menu)


def test_main_assembly_calls_wire_text_context_menu():
    """main() 必须真的调用装配（AST：不 import GUI 即可断言接线存在）。"""
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "knowledge_ball.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    assert "wire_text_context_menu" in called, \
        "main() 未调用 wire_text_context_menu —— 菜单不会装配"
