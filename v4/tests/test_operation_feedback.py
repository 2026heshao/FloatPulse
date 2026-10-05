# -*- coding: utf-8 -*-
"""常用操作轻提示反馈（toast）回归 —— 2026-10-05。

背景：应用已有 ScreenToast 体系，但部分常用操作静默。本次补齐：
  1. 任务页手动添加任务 → 「已添加任务：标题」（长标题截断 16 字）
  2. 碎片右键「归类为」纠正 → 「已归类为「标签」」
  3. 软件导航右键「复制路径」→ 「已复制路径」（此前刻意无声，改为反馈）
  4. 截图钉屏右键「复制图片」→ 「钉图已复制」（此前仅写日志）

钉死的行为：
  A 反馈走既有 ScreenToast.show_msg / host.show_toast，不造新轮子
  B 操作本体行为不变（写入 / 剪贴板 / 归类落库）
  C 失败路径不弹成功提示（碎片不存在 → 不提示）
knowledge_ball 插件写入 provider 的 toast（走 main_window.show_toast）
依赖完整主窗环境，不在本文件覆盖，由离屏冒烟兜底。
"""

import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.fragment_classifier import CATEGORY_LABELS  # noqa: E402


# ====================================================================
# 公共替身
# ====================================================================
class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    """面板所需最小宿主替身 + show_toast 录制"""

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
        self.toasts = []


    @property
    def config(self):
        return self._config

    @property
    def task_manager(self):
        return self._task_manager

    @property
    def fragment_manager(self):
        return self._fragment_manager

    @property
    def note_manager(self):
        return self._note_manager

    def show_toast(self, text, ms=2800):
        self.toasts.append(text)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def toast_recorder(monkeypatch):
    """录制 ScreenToast.show_msg 类级调用（顶层窗口路径用）"""
    from src.controls import ScreenToast

    calls = []

    def _fake(text, theme="", ms=2600):
        calls.append((text, theme))

    monkeypatch.setattr(ScreenToast, "show_msg", _fake)
    return calls


# ====================================================================
# 1. 任务页手动添加 → toast
# ====================================================================
def test_tasks_add_shows_toast(qapp, tmp_path):
    from src.task_manager import TaskManager
    from src.tasks_panel import TasksPanel

    tm = TaskManager(str(tmp_path / "tasks.json"))
    host = _FakeHost()
    host._task_manager = tm
    panel = TasksPanel(host)

    panel._task_title_input.setText("周三交周报")
    panel._on_add()
    assert host.toasts == ["已添加任务：周三交周报"]
    assert tm.get_task(1) is not None          # 操作本体不变


def test_tasks_add_long_title_truncated(qapp, tmp_path):
    from src.task_manager import TaskManager
    from src.tasks_panel import TasksPanel

    tm = TaskManager(str(tmp_path / "tasks.json"))
    host = _FakeHost()
    host._task_manager = tm
    panel = TasksPanel(host)

    long_title = "超" * 30
    panel._task_title_input.setText(long_title)
    panel._on_add()
    assert host.toasts == [f"已添加任务：{'超' * 15}…"]


def test_tasks_add_empty_no_toast(qapp, tmp_path):
    """空标题直接 return：不加任务也不弹提示"""
    from src.task_manager import TaskManager
    from src.tasks_panel import TasksPanel

    tm = TaskManager(str(tmp_path / "tasks.json"))
    host = _FakeHost()
    host._task_manager = tm
    panel = TasksPanel(host)

    panel._task_title_input.setText("   ")
    panel._on_add()
    assert host.toasts == []
    assert len(tm.get_all_tasks()) == 0


# ====================================================================
# 2. 碎片归类纠正 → toast
# ====================================================================
def _make_fragments_panel(qapp, tmp_path, n=1):
    from src.fragment_classifier import CAT_LINK
    from src.fragment_manager import FragmentManager, TYPE_CLIPBOARD_TEXT
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    ids = []
    for i in range(n):
        ids.append(mgr.add_fragment(TYPE_CLIPBOARD_TEXT, f"内容 {i}",
                                    source="测试"))
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)
    return panel, mgr, ids, CAT_LINK


def test_apply_category_shows_toast(qapp, tmp_path):
    panel, mgr, ids, CAT_LINK = _make_fragments_panel(qapp, tmp_path)
    label = CATEGORY_LABELS[CAT_LINK]
    assert panel._apply_category(ids[0], CAT_LINK) is True
    assert panel._host.toasts == [f"已归类为「{label}」"]
    assert mgr.get_fragment(ids[0]).category == CAT_LINK   # 落库不变


def test_apply_category_missing_fragment_no_toast(qapp, tmp_path):
    """碎片已不存在：归类失败不弹成功提示"""
    panel, mgr, ids, CAT_LINK = _make_fragments_panel(qapp, tmp_path)
    assert panel._apply_category(99999, CAT_LINK) is False
    assert panel._host.toasts == []


# ====================================================================
# 3. 软件导航复制路径 → toast（此前刻意无声，2026-10-05 改为反馈）
# ====================================================================
def test_copy_app_path_shows_toast(qapp, toast_recorder):
    from src.widget_app_launcher import AppLauncherPage

    page = AppLauncherPage(_FakeConfig())
    page._copy_app_path({"exe_path": "C:/tools/demo.exe"})
    assert QApplication.clipboard().text() == "C:/tools/demo.exe"  # 本体不变
    assert toast_recorder == [("已复制路径", page._theme)]


def test_copy_app_path_empty_no_toast(qapp, toast_recorder):
    """exe_path 为空：不写剪贴板也不弹提示"""
    from src.widget_app_launcher import AppLauncherPage

    page = AppLauncherPage(_FakeConfig())
    page._copy_app_path({"exe_path": "  "})
    assert toast_recorder == []


# ====================================================================
# 4. 截图钉屏复制图片 → toast（此前仅写日志）
# ====================================================================
def test_pin_copy_shows_toast(qapp, toast_recorder):
    from src.screenshot_pin import PinWindow

    pix = QPixmap(60, 40)
    pix.fill(Qt.GlobalColor.darkCyan)
    pin = PinWindow(pix, QPoint(30, 40), "dark")
    pin._copy_to_clipboard()
    assert not QApplication.clipboard().pixmap().isNull()      # 本体不变
    assert toast_recorder == [("钉图已复制", "dark")]
