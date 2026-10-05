# -*- coding: utf-8 -*-
"""大列表窗口化渲染（list_windowing）单元测试 + 两面板离屏冒烟。

覆盖（成熟化 3.6 / 优化调研 1.3）：
  1. 纯逻辑决策面：首屏块大小（≤阈值全量 / 超阈值首块）、追加触发与封顶、
     重置语义（换条件即 reset）
  2. FragmentsPanel / TasksPanel offscreen 冒烟：500 条下 refresh 不炸、
     首屏行数上限生效、滚动追加生效、小数据量全量直建（行为与旧实现一致）
  3. 语义保持：碎片面板 preserve_view 下被选中的深部条目 refresh 后仍选中
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal  # noqa: E402

from src.list_windowing import (  # noqa: E402
    FIRST_CHUNK, FULL_THRESHOLD, ListWindowing,
    first_build_count, next_build_count,
)


# ====================================================================
# 1. 纯逻辑决策面
# ====================================================================
class TestFirstBuildCount:
    def test_zero_total_builds_nothing(self):
        assert first_build_count(0) == 0

    def test_small_total_builds_all(self):
        """≤ 阈值：全量直建（与旧实现行为一致）"""
        assert first_build_count(1) == 1
        assert first_build_count(50) == 50
        assert first_build_count(FULL_THRESHOLD) == FULL_THRESHOLD

    def test_large_total_builds_first_chunk(self):
        """> 阈值：只建首块"""
        assert first_build_count(FULL_THRESHOLD + 1) == FIRST_CHUNK
        assert first_build_count(5000) == FIRST_CHUNK


class TestNextBuildCount:
    def test_nothing_to_build(self):
        assert next_build_count(0, 0) == 0
        assert next_build_count(500, 500) == 0
        assert next_build_count(600, 500) == 0    # 越界防错

    def test_full_chunk_and_tail_cap(self):
        assert next_build_count(0, 500) == FIRST_CHUNK
        assert next_build_count(120, 500) == FIRST_CHUNK
        assert next_build_count(480, 500) == 20   # 尾块封顶到剩余量


class TestListWindowing:
    def test_reset_small_not_windowed(self):
        w = ListWindowing()
        assert w.reset(80) == 80
        assert (w.total, w.built) == (80, 80)
        assert w.windowed is False
        assert w.extend() == 0

    def test_reset_large_first_chunk(self):
        w = ListWindowing()
        assert w.reset(500) == FIRST_CHUNK
        assert (w.total, w.built) == (500, FIRST_CHUNK)
        assert w.windowed is True

    def test_extend_until_exhausted(self):
        """反复追加到建满：累计建入精确等于 total，之后追加返回 0"""
        w = ListWindowing()
        built = w.reset(500)
        while True:
            n = w.extend()
            if n == 0:
                break
            built += n
        assert built == 500
        assert w.built == 500

    def test_reset_resets_built(self):
        """换筛选/搜索即 reset：已建行数清零重算（换条件语义保持）"""
        w = ListWindowing()
        w.reset(500)
        w.extend()
        assert w.built == 2 * FIRST_CHUNK
        assert w.reset(30) == 30
        assert (w.total, w.built, w.windowed) == (30, 30, False)

    def test_custom_threshold_and_chunk(self):
        w = ListWindowing(threshold=10, chunk=5)
        assert w.reset(8) == 8            # ≤ 阈值全量
        w2 = ListWindowing(threshold=10, chunk=5)
        assert w2.reset(26) == 5          # > 阈值按自定义块
        assert w2.extend() == 5
        assert w2.extend() == 5
        assert w2.extend() == 5
        assert w2.extend() == 5
        assert w2.extend() == 1           # 尾块
        assert w2.extend() == 0


# ====================================================================
# 2. 面板离屏冒烟（500 条 + 小数据量）
# ====================================================================
class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    """面板 refresh 路径所需的最小宿主替身（鸭子类型）"""

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


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_fragment_manager(tmp_path, n):
    from src.fragment_manager import FragmentManager, TYPE_CLIPBOARD_TEXT
    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    for i in range(n):
        mgr.add_fragment(TYPE_CLIPBOARD_TEXT, f"碎片内容 {i} 正文正文",
                         source=f"来源{i}")
    return mgr


def _fragment_ids_in_list(panel):
    """按列表顺序取已建行的碎片 id（跳过组头 None）"""
    return [panel._frag_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(panel._frag_list.count())]


def test_fragments_panel_windowed_smoke(qapp, tmp_path):
    """500 条碎片：refresh 不炸、首屏行数 = 首块上限、追加与 reset 生效"""
    from src.fragments_panel import FragmentsPanel

    host = _FakeHost()
    host._fragment_manager = _make_fragment_manager(tmp_path, 500)
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    total_rows = len(panel._rows)
    assert total_rows > FULL_THRESHOLD               # 500 条必然窗口化
    assert panel._frag_list.count() == FIRST_CHUNK   # 首屏行数上限生效
    # 计数标签口径不变（数据 500 条全显示，只是渲染分块）
    assert panel._frag_count_label.text() == "显示 500 条 / 共 500 条"
    # 行序正确：已建行的碎片 id 与描述符表前缀一致（组头行 id 为 None）
    spec_ids = [row[1].fragment_id for row in panel._rows if row[0] == "frag"]
    built_ids = [v for v in _fragment_ids_in_list(panel) if v is not None]
    assert built_ids == spec_ids[:len(built_ids)]

    # 追加一块（滚动加载回调通道）
    panel._extend_list_rows()
    assert panel._frag_list.count() == 2 * FIRST_CHUNK

    # 换筛选即 reset：搜索后回到首屏（refresh 语义保持）
    panel.apply_external_keyword("碎片内容")
    assert len(panel._rows) > FULL_THRESHOLD
    assert panel._frag_list.count() == FIRST_CHUNK


def test_fragments_panel_small_list_full_build(qapp, tmp_path):
    """30 条（≤阈值）：全量直建，行数 = 描述符总数，与旧实现一致"""
    from src.fragments_panel import FragmentsPanel

    host = _FakeHost()
    host._fragment_manager = _make_fragment_manager(tmp_path, 30)
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    total_rows = len(panel._rows)
    assert panel._frag_list.count() == total_rows    # 全量直建
    assert panel._windowing.windowed is False
    assert panel._frag_count_label.text() == "显示 30 条 / 共 30 条"


def test_fragments_panel_preserve_selection_deep(qapp, tmp_path):
    """preserve_view 语义保持：选中第 500 条后刷新，选中项不丢。

    被选条目在首块之外 → refresh 续建到包含它为止（选中项不因
    窗口化而缩水，「删除/合并后接着操作下一条」语义不变）。
    """
    from src.fragments_panel import FragmentsPanel
    from src.theme import get_colors

    host = _FakeHost()
    host._fragment_manager = _make_fragment_manager(tmp_path, 500)
    panel = FragmentsPanel(host)
    colors = get_colors("dark")
    panel.refresh(preserve_view=False)
    # 走与滚动追加相同的分块通道建满
    while True:
        n = panel._windowing.extend()
        if n == 0:
            break
        panel._build_list_rows(n, colors)
    assert panel._frag_list.count() == len(panel._rows)

    # 选中最后一个碎片行
    for i in range(panel._frag_list.count() - 1, -1, -1):
        if panel._frag_list.item(i).data(Qt.ItemDataRole.UserRole) is not None:
            panel._frag_list.item(i).setSelected(True)
            break

    panel.refresh(preserve_view=True)
    assert len(panel._get_selected_ids()) == 1
    assert panel._frag_list.count() == len(panel._rows)   # 续建到包含选中项


def test_tasks_panel_windowed_smoke(qapp, tmp_path):
    """500 条任务：refresh 不炸、首屏行数上限生效、组头与任务行齐全"""
    from src.task_delegate import KIND_ROLE
    from src.task_manager import KIND_HEADER, KIND_ROW, TaskManager
    from src.tasks_panel import TasksPanel

    tm = TaskManager(str(tmp_path / "tasks.json"))
    deadlines = ["2026-01-10", "2026-01-20", "2030-05-01", ""]
    for i in range(500):
        tm.add_task(f"任务 {i}", "", deadlines[i % len(deadlines)])
    host = _FakeHost()
    host._task_manager = tm
    panel = TasksPanel(host)
    panel.refresh()

    total_rows = len(panel._rows)
    assert total_rows > FULL_THRESHOLD               # 500 行 + 若干组头
    assert panel._task_list.count() == FIRST_CHUNK   # 首屏行数上限生效
    # 组头行在首屏内；任务行携带 int 主键（右键/勾选依赖）
    headers = [i for i in range(panel._task_list.count())
               if panel._task_list.item(i).data(KIND_ROLE) == KIND_HEADER]
    assert len(headers) >= 1
    task_rows = [panel._task_list.item(i)
                 for i in range(panel._task_list.count())
                 if panel._task_list.item(i).data(KIND_ROLE) == KIND_ROW]
    assert task_rows and all(
        isinstance(w.data(Qt.ItemDataRole.UserRole), int) for w in task_rows)

    # 追加一块（滚动加载回调通道）
    panel._extend_list_rows()
    assert panel._task_list.count() > FIRST_CHUNK


def test_tasks_panel_small_list_full_build(qapp, tmp_path):
    """10 条任务（≤阈值）：全量直建（组头 + 任务行），与旧实现一致"""
    from src.task_manager import TaskManager
    from src.tasks_panel import TasksPanel

    tm = TaskManager(str(tmp_path / "tasks_small.json"))
    for i in range(10):
        tm.add_task(f"小任务 {i}", "", "")
    host = _FakeHost()
    host._task_manager = tm
    panel = TasksPanel(host)
    panel.refresh()

    total_rows = len(panel._rows)
    assert total_rows == 11                          # 10 行 + 1 组头
    assert panel._task_list.count() == total_rows
    assert panel._windowing.windowed is False


def test_scroll_loader_triggers_on_scroll(qapp):
    """attach_scroll_loader：滚动条到底 → 回调被触发，顶部不触发"""
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem

    from src.list_windowing import attach_scroll_loader

    calls = []
    lst = QListWidget()
    lst.resize(300, 200)
    lst.show()
    for i in range(300):
        QListWidgetItem(f"行 {i}", lst)
    qapp.processEvents()                             # offscreen 下先落一次布局
    attach_scroll_loader(lst, lambda: calls.append(1))
    bar = lst.verticalScrollBar()
    bar.setValue(0)
    assert calls == []                               # 顶部不触发
    bar.setValue(bar.maximum())                      # 滚到底
    assert len(calls) >= 1


if __name__ == "__main__":
    pytest.main([os.path.abspath(__file__), "-q"])
