# -*- coding: utf-8 -*-
"""碎片工作台预览就地编辑（2026-10-03 行为变更护栏）

原「只读预览 + 编辑按钮 → 玻璃弹窗」链路改为预览区直接编辑：
  - _content 解除只读；编辑按钮（_edit_btn / _on_edit）整体拆除；
    右键菜单「编辑内容」与详情弹窗「编辑内容」入口保留
  - 停止输入 800ms 自动写回（FragmentManager.update_fragment：拒收空
    内容、内容变化时类别自动重算、无变化不落盘不刷列表）
  - 切换选中前 flush 未保存修改；空内容拒收且不回滚用户输入
  - 自动保存回环（commit -> refresh -> _sync_preview -> show_fragment
    同文本）不重写编辑框——重设 setPlainText 会把用户光标打回开头

钉法：offscreen 真面板（_FakeHost 模式沿用 test_list_windowing）+
源码拆除回归闸 + 帮助页文案同步断言。
"""
import os

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

# 路径自举：tests/ 的上一级是 v4/
_V4_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PANEL_PATH = os.path.join(_V4_DIR, "src", "fragments_panel.py")
_HELP_PATH = os.path.join(_V4_DIR, "src", "main_window.py")

with open(_PANEL_PATH, encoding="utf-8") as _f:
    _PANEL_SRC = _f.read()


# ====================================================================
# 宿主替身（与 test_list_windowing 同款最小契约）
# ====================================================================
class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig()
        self._task_manager = None
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = None
        self.current_theme = "dark"
        self.anim_speed = 1.0


def _make_panel(tmp_path, contents):
    """造碎片管理器（每条内容唯一，避开 12 条去重窗口）+ 挂真面板"""
    from src.fragment_manager import FragmentManager, TYPE_CLIPBOARD_TEXT
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    ids = []
    for text in contents:
        ids.append(mgr.add_fragment(TYPE_CLIPBOARD_TEXT, text, source="测试"))
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    return panel, mgr, ids


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ====================================================================
# 1. 只读解除 + 编辑按钮拆除
# ====================================================================
def test_preview_content_is_editable(qapp, tmp_path):
    """预览区 QTextEdit 解除只读；复制按钮保留"""
    from src.fragments_panel import _PreviewPane

    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    assert isinstance(pane, _PreviewPane)
    assert pane._content.isReadOnly() is False
    assert hasattr(pane, "_copy_btn")          # 「复制」动作保留


def test_edit_button_removed(qapp, tmp_path):
    """编辑按钮及相关逻辑整体拆除：实例无 _edit_btn/_on_edit，源码零残留"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    assert not hasattr(pane, "_edit_btn")
    assert not hasattr(pane, "_on_edit")
    # 源码拆除回归闸：标识符任何形式出现（含连线/方法/注释）都算复辟
    assert "_edit_btn" not in _PANEL_SRC
    assert "_on_edit" not in _PANEL_SRC


# ====================================================================
# 2. 装填防回声 + 去抖启动
# ====================================================================
def test_show_fragment_no_echo(qapp, tmp_path):
    """show_fragment 装填文本走 blockSignals，不得触发自动保存链"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    echoes = []
    pane._content.textChanged.connect(lambda: echoes.append(1))
    pane.show_fragment(mgr.get_fragment(ids[0]))
    assert echoes == []
    assert pane._content.toPlainText() == "随手记的普通文本内容"
    assert not pane._save_timer.isActive()


def test_typing_starts_debounce(qapp, tmp_path):
    """输入即刷新字数统计并启动 800ms 去抖"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))
    assert not pane._save_timer.isActive()

    new_text = "改了一半的内容"
    pane._content.setPlainText(new_text)
    assert pane._save_timer.isActive()
    assert pane._stat.text() == f"{len(new_text)} 字"


# ====================================================================
# 3. 自动保存落盘（真 timeout 链）+ 类别重算
# ====================================================================
def test_debounce_autosave_writes_back(qapp, tmp_path, monkeypatch):
    """停止输入后 timeout 触发真保存：数据层落盘 + 列表保位刷新"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))

    calls = []
    monkeypatch.setattr(
        panel, "refresh",
        lambda preserve_view=False: calls.append(preserve_view))

    pane._content.setPlainText("自动保存后的新内容")
    # 去抖间隔压到 0：走真信号链 textChanged -> start -> timeout -> commit
    pane._save_timer.setInterval(0)
    pane._save_timer.start()
    QTest.qWait(20)

    assert mgr.get_fragment(ids[0]).content == "自动保存后的新内容"
    assert calls == [True]              # 有变化才刷列表，且保浏览位置
    assert not pane._save_timer.isActive()


def test_category_recomputed_on_save(qapp, tmp_path):
    """编辑成 URL：类别随内容自动重算（text -> link），面板持有对象换新"""
    from src.fragment_classifier import CAT_LINK, CAT_TEXT

    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    frag = mgr.get_fragment(ids[0])
    assert frag.category == CAT_TEXT

    # 真实路径：预览有内容 = 必有选中项。先建列表并选中条目，
    # 否则 commit 后的 refresh(preserve_view) -> _sync_preview 会因
    # 列表无选中项把预览清回占位页（测试自造的空列表假象）
    panel.refresh(preserve_view=False)
    for i in range(panel._frag_list.count()):
        item = panel._frag_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == ids[0]:
            item.setSelected(True)
            break
    panel._sync_preview()
    assert pane._frag is not None and pane._frag.fragment_id == ids[0]

    pane._content.setPlainText("https://example.com/page")
    pane._commit_edit()

    fresh = mgr.get_fragment(ids[0])
    assert fresh.category == CAT_LINK
    assert pane._frag.category == CAT_LINK      # _commit_edit 已换新引用


# ====================================================================
# 4. 切换 flush / 无变化 / 空内容 / 保光标
# ====================================================================
def test_switch_selection_flushes_pending_edit(qapp, tmp_path):
    """去抖中切走：未落盘的修改立即 flush，不丢字"""
    panel, mgr, ids = _make_panel(
        tmp_path, ["随手记的普通文本内容", "第二条随手记的内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))
    pane._content.setPlainText("切换前改掉的内容")
    assert pane._save_timer.isActive()          # 还没到 800ms

    pane.show_fragment(mgr.get_fragment(ids[1]))        # 切换 -> flush
    assert mgr.get_fragment(ids[0]).content == "切换前改掉的内容"
    assert pane._content.toPlainText() == "第二条随手记的内容"
    assert not pane._save_timer.isActive()


def test_commit_no_change_skips_refresh(qapp, tmp_path, monkeypatch):
    """文本无变化：不落盘、不刷列表"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))

    calls = []
    monkeypatch.setattr(
        panel, "refresh",
        lambda preserve_view=False: calls.append(preserve_view))

    pane._commit_edit()
    assert calls == []
    assert mgr.get_fragment(ids[0]).content == "随手记的普通文本内容"


def test_empty_text_rejected_without_rollback(qapp, tmp_path):
    """全空白输入：数据层拒收保原文，编辑框不回滚用户输入"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))

    pane._content.setPlainText("   ")
    pane._commit_edit()

    assert mgr.get_fragment(ids[0]).content == "随手记的普通文本内容"
    assert pane._content.toPlainText() == "   "


def test_same_text_reload_keeps_cursor(qapp, tmp_path):
    """自动保存回环（show_fragment 同文本）不重写编辑框：光标保留"""
    panel, mgr, ids = _make_panel(tmp_path, ["随手记的普通文本内容"])
    pane = panel._preview
    pane.show_fragment(mgr.get_fragment(ids[0]))

    cursor = pane._content.textCursor()
    cursor.setPosition(3)
    pane._content.setTextCursor(cursor)

    # 模拟 commit -> refresh -> _sync_preview -> show_fragment(同文本)
    pane.show_fragment(mgr.get_fragment(ids[0]))
    assert pane._content.toPlainText() == "随手记的普通文本内容"
    assert pane._content.textCursor().position() == 3


# ====================================================================
# 5. 帮助页文案同步（用户可见变更口径）
# ====================================================================
def test_help_text_synced():
    """帮助页碎片章预览文案与就地编辑行为一致"""
    with open(_HELP_PATH, encoding="utf-8") as f:
        help_src = f.read()
    assert "可直接修改（停止输入 800ms 自动保存）" in help_src
    # 旧「编辑按钮」文案不得残留（右键/详情弹窗的「编辑内容」入口不在此列）
    assert "可「复制」「编辑」" not in help_src
