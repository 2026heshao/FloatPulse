# -*- coding: utf-8 -*-
"""笔记管理页优化批次（N1/N2/N3/N4/N5）—— 面板级回归 + 调用链钉子。

覆盖：
  N1 focus_new_note 调用链（体例照 tests/test_panel_focus_new_task.py）：
    命令面板 execute_entry(action.new_note)
      → MainWindow.focus_new_note()     （公开委托：切页 + 面板新建聚焦）
        → NotesPanel.focus_new_note()   （复用 _on_new，零新交互模式）
  N3 Ctrl+F 页级快捷键：存在性（findChildren(QShortcut)）+ 作用域 +
    _focus_search 聚焦并全选；
  N4 「● 未保存」态用主题 warning 语义色着色，正常态清回无内联样式；
  N5 状态栏字数：「编辑中：… · N 字」/「● 未保存 · N 字」，空内容省略；
  N2 导出文案：三面板右键菜单 + 命令面板 title + 设置页按钮统一为
    「导出全部到 Obsidian」（源码钉子，消除"只导当前"的语义歧义）。

单跑：
    cd v4 && python -m pytest tests/test_panel_focus_new_note.py -q
"""

import ast
import io
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal          # noqa: E402
from PyQt6.QtGui import QShortcut                     # noqa: E402
from PyQt6.QtWidgets import QApplication              # noqa: E402

from src.theme import get_colors                      # noqa: E402

MAIN_WINDOW_PATH = os.path.join(BASE, "src", "main_window.py")

# N2 文案钉子的对象文件（源 → 期望出现的精确串）
_EXPORT_PINS = {
    "src/notes_panel.py": 'menu.addAction("导出全部到 Obsidian")',
    "src/fragments_panel.py": 'menu.addAction("导出全部到 Obsidian")',
    "src/tasks_panel.py": 'menu.addAction("导出全部到 Obsidian")',
    "src/command_palette.py": 'title="导出全部到 Obsidian"',
    "src/settings_panel.py": 'SmoothButton("导出全部到 Obsidian")',
}


def _read(rel):
    with io.open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()


# ====================================================================
# 替身宿主（体例照 tests/test_panel_focus_new_task.py 的最小面板宿主）
# ====================================================================
class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    """NotesPanel 所需最小宿主替身"""

    data_changed = pyqtSignal(str)

    def __init__(self, theme="dark"):
        super().__init__()
        self._config = _FakeConfig()
        self._note_manager = None
        self.current_theme = theme
        self.toasts = []

    @property
    def config(self):
        return self._config

    def show_toast(self, text, ms=2800, **kwargs):
        self.toasts.append(text)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_panel(qapp, tmp_path, theme="dark"):
    from src.note_manager import NoteManager
    from src.notes_panel import NotesPanel

    host = _FakeHost(theme)
    host._note_manager = NoteManager(str(tmp_path / "notes.json"))
    return NotesPanel(host)


# ====================================================================
# N1-A. 面板级：focus_new_note = 清空 + 聚焦编辑区（复用 _on_new）
# ====================================================================
def test_focus_new_note_focuses_editor(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    panel.show()
    panel.focus_new_note()
    QApplication.processEvents()
    # 焦点断言用「该窗口内的焦点控件」：offscreen 无活动窗口，
    # QApplication.focusWidget() 恒为 None，不是被测行为的一部分
    assert panel.focusWidget() is panel._note_edit


def test_focus_new_note_does_not_create_or_keep_content(qapp, tmp_path):
    """零新交互：编辑区已空时不建笔记、状态为新建引导（复用 _on_new）"""
    panel = _make_panel(qapp, tmp_path)
    n_before = len(panel._note_manager.get_all_notes())
    panel.focus_new_note()
    QApplication.processEvents()
    assert panel._note_edit.toPlainText() == ""
    assert len(panel._note_manager.get_all_notes()) == n_before
    assert panel._note_status_label.text() == "新建笔记，输入内容自动保存"


# ====================================================================
# N1-B. 主窗委托体源码钉子（AST 抽方法体，防连错槽）
# ====================================================================
def _main_window_method_source(name: str) -> str:
    """从 main_window.py 抽 MainWindow 类内指定方法的源码段"""
    with open(MAIN_WINDOW_PATH, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and item.name == name:
                    return ast.get_source_segment(src, item)
    raise AssertionError(f"MainWindow.{name} 不存在")


def test_main_window_focus_new_note_delegate_pins():
    src = _main_window_method_source("focus_new_note")
    # 先切页（notes 物理页 = NAV_PAGE_INDEX["notes"]）再走面板公开方法
    assert 'NAV_PAGE_INDEX["notes"]' in src
    assert "self.show_page(" in src
    assert "self._page_notes.focus_new_note()" in src


def test_delegate_is_public_not_property():
    src = _main_window_method_source("focus_new_note")
    assert src.startswith("def focus_new_note")


# ====================================================================
# N3. Ctrl+F 页级快捷键
# ====================================================================
def test_ctrl_f_shortcut_registered_page_scoped(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    shortcuts = {sc.key().toString(): sc for sc in panel.findChildren(QShortcut)}
    assert "Ctrl+F" in shortcuts
    # 页级作用域：焦点在笔记页内才触发，不占全局命名空间
    assert shortcuts["Ctrl+F"].context() == \
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ShortcutContext.WidgetWithChildrenShortcut


def test_focus_search_focuses_and_selects(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    panel.show()
    panel._note_search.setText("关键词")
    panel._focus_search()
    QApplication.processEvents()
    assert panel.focusWidget() is panel._note_search
    assert panel._note_search.selectedText() == "关键词"


# ====================================================================
# N4 / N5. 状态栏：未保存着色 + 字数
# ====================================================================
def test_unsaved_status_colored_with_theme_warning(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    panel._loading_note = False
    panel._current_note_id = 1
    panel._note_edit.setPlainText("12345")          # 触发 _on_text_changed
    expected = get_colors("dark")["warning"]
    assert panel._note_status_label.text() == "● 未保存 · 5 字"
    assert f"color: {expected}" in panel._note_status_label.styleSheet()


def test_light_theme_uses_light_warning(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path, theme="light")
    panel._loading_note = False
    panel._current_note_id = 1
    panel._note_edit.setPlainText("abc")
    expected = get_colors("light")["warning"]
    assert f"color: {expected}" in panel._note_status_label.styleSheet()


def test_normal_status_clears_inline_color(qapp, tmp_path):
    """保存后回到正常态：样式清空（不残留 warning 色）"""
    panel = _make_panel(qapp, tmp_path)
    note_id = panel._note_manager.add_note("旧内容")
    panel._loading_note = False
    panel._current_note_id = note_id
    panel._note_edit.setPlainText("12345")
    assert "color:" in panel._note_status_label.styleSheet()
    panel._on_save()
    assert panel._note_status_label.text() == "已保存"
    assert panel._note_status_label.styleSheet() == ""


def test_editing_status_carries_word_count(qapp, tmp_path):
    """选中既有笔记 → 「编辑中：… · N 字」（N=内容字符数）"""
    panel = _make_panel(qapp, tmp_path)
    panel._note_manager.add_note("hello 世界")
    panel.refresh()
    item = panel._note_list.item(0)
    assert item is not None
    panel._on_selected(item, None)
    text = panel._note_status_label.text()
    assert text.startswith("编辑中：")
    assert text.endswith("· 8 字")


def test_empty_content_omits_word_count(qapp, tmp_path):
    """空内容省略字数（不显示"0 字"，口径与批次设计一致）"""
    panel = _make_panel(qapp, tmp_path)
    panel._loading_note = False
    panel._current_note_id = None
    panel._note_save_timer.stop()
    panel._on_text_changed()          # 空编辑区 + 无当前笔记 → 早退不改状态
    panel._note_status_label.setText("新建笔记，输入内容自动保存")
    panel._note_edit.setPlainText("")  # 仍为空
    assert "字" not in panel._note_status_label.text()


# ====================================================================
# N2. 导出文案统一（源码钉子：五处入口全为「导出全部到 Obsidian」）
# ====================================================================
def test_export_wording_unified():
    for rel, needle in _EXPORT_PINS.items():
        src = _read(rel)
        assert needle in src, f"{rel} 缺少统一文案：{needle}"
    # 旧文案不许残留在这些入口的同一写法里
    for rel in ("src/notes_panel.py", "src/fragments_panel.py",
                "src/tasks_panel.py"):
        assert 'menu.addAction("导出到 Obsidian")' not in _read(rel), rel
