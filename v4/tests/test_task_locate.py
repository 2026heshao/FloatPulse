# -*- coding: utf-8 -*-
"""任务提醒跳转定位回归（2026-10-08 稳定性轮 P1）。

钉死的调用链：
  任务提醒弹窗 open_requested
    → knowledge_ball._open_task_page_from_reminder
      → main_window.show_page(1) + _page_tasks.focus_task_by_title(首条逾期)

本文件只测面板级 focus_task_by_title 行为：
  - 命中：按 ROLE_TITLE 精确匹配第一条 → 选中 + scrollToItem，返回 True
  - 未命中（任务不存在 / 标题为空）→ False，选中态不被破坏

宿主替身体例照 tests/test_panel_focus_new_task.py（offscreen 真实面板）。
"""

import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


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


def _seed_overdue_and_future(panel):
    """一逾期 + 一未来（逾期组按分组排序排最前）"""
    tm = panel._task_manager
    tm.add_task("逾期任务甲", "", "2020-01-01")
    tm.add_task("未来任务乙", "", "2099-01-01")
    panel.refresh()


def test_focus_task_by_title_selects_row(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    _seed_overdue_and_future(panel)
    assert panel.focus_task_by_title("逾期任务甲") is True
    item = panel._task_list.currentItem()
    assert item is not None
    from src.task_delegate import ROLE_TITLE
    assert item.data(ROLE_TITLE) == "逾期任务甲"


def test_focus_task_by_title_miss_returns_false(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    _seed_overdue_and_future(panel)
    before = panel._task_list.currentItem()
    assert panel.focus_task_by_title("不存在的任务") is False
    assert panel._task_list.currentItem() is before   # 选中态不被破坏


def test_focus_task_by_title_empty_string(qapp, tmp_path):
    panel = _make_panel(qapp, tmp_path)
    _seed_overdue_and_future(panel)
    assert panel.focus_task_by_title("") is False


def test_focus_task_by_title_after_rebuild(qapp, tmp_path):
    """提醒闭环全链路：完成→重建→改期→再定位仍命中"""
    panel = _make_panel(qapp, tmp_path)
    _seed_overdue_and_future(panel)
    tm = panel._task_manager
    t = next(t for t in tm.get_all_tasks() if t.title == "逾期任务甲")
    tm.update_task(t.task_id, t.title, "", "2098-06-01")
    panel.refresh()
    assert panel.focus_task_by_title("逾期任务甲") is True
