# -*- coding: utf-8 -*-
"""命令面板「新建任务」落点回归（收尾任务 C2）—— 调用链钉子。

钉死的调用链（防连错槽）：
  命令面板 execute_entry(action.new_task)
    → MainWindow.focus_new_task()        （公开委托：切页 + 面板聚焦）
      → TasksPanel.focus_new_task()      （聚焦新建输入框，不新造交互）

覆盖：
  A TasksPanel.focus_new_task() 真实面板（替身宿主）执行后，Qt 焦点
    落在 _task_title_input 上；
  B main_window.focus_new_task 委托体源码钉死：必须先 show_page 到
    tasks 物理页、再调 _page_tasks.focus_new_task（AST 抽方法体断言，
    不实例化 MainWindow —— 主窗构造依赖完整环境）；
  C 面板方法只聚焦、不改文本、不触发 _on_add（行为零新增）。
"""

import ast
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

MAIN_WINDOW_PATH = os.path.join(BASE, "src", "main_window.py")


# ====================================================================
# 替身宿主（体例照 tests/test_operation_feedback.py 的最小面板宿主）
# ====================================================================
class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    """TasksPanel 所需最小宿主替身"""

    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig()
        self._task_manager = None
        self.current_theme = "dark"
        self.anim_speed = 1.0
        self.toasts = []

    @property
    def config(self):
        return self._config

    @property
    def task_manager(self):
        return self._task_manager

    def show_toast(self, text, ms=2800, **kwargs):
        self.toasts.append(text)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_panel(qapp, tmp_path):
    from src.task_manager import TaskManager
    from src.tasks_panel import TasksPanel

    host = _FakeHost()
    host._task_manager = TaskManager(str(tmp_path / "tasks.json"))
    return TasksPanel(host)


# ====================================================================
# A. 面板级：聚焦新建输入框
# ====================================================================
def test_focus_new_task_focuses_title_input(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    panel.show()                      # offscreen 下也要可见才有焦点链路
    panel.focus_new_task()
    QApplication.processEvents()
    # 焦点断言用「该窗口内的焦点控件」：offscreen 无活动窗口，
    # QApplication.focusWidget() 恒为 None，不是被测行为的一部分
    assert panel.focusWidget() is panel._task_title_input


def test_focus_new_task_does_not_add_or_clear(qapp, tmp_path):
    """只聚焦：不触发添加、不清空已输入文本（行为零新增）"""
    panel = _make_panel(qapp, tmp_path)
    panel._task_title_input.setText("预填内容")
    n_before = len(panel._task_manager.get_all_tasks())
    panel.focus_new_task()
    QApplication.processEvents()
    assert panel._task_title_input.text() == "预填内容"
    assert len(panel._task_manager.get_all_tasks()) == n_before


# ====================================================================
# B. 主窗委托体源码钉子（AST 抽方法体，防连错槽）
# ====================================================================
def _main_window_method_source(name: str) -> str:
    """从 main_window.py 抽 MainWindow 类内指定方法的源码段"""
    with open(MAIN_WINDOW_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and item.name == name:
                    return ast.get_source_segment(
                        open(MAIN_WINDOW_PATH, encoding="utf-8").read(), item)
    raise AssertionError(f"MainWindow.{name} 不存在")


def test_main_window_focus_new_task_delegate_pins():
    src = _main_window_method_source("focus_new_task")
    # 先切页（tasks 物理页 = NAV_PAGE_INDEX["tasks"]）再聚焦面板输入框
    assert 'NAV_PAGE_INDEX["tasks"]' in src
    assert "self.show_page(" in src
    assert "self._page_tasks.focus_new_task()" in src


# ====================================================================
# C. 公开面落点存在性（main_window 必须有公开 focus_new_task；
#    命令面板 _run_action 分派已由 test_command_palette.py 钉死）
# ====================================================================
def test_delegate_is_public_not_property():
    src = _main_window_method_source("focus_new_task")
    assert src.startswith("def focus_new_task")
