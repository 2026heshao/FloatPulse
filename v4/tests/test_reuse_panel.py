# -*- coding: utf-8 -*-
"""
一键粘回面板集成单测（reuse 卡，offscreen 真面板）。

护栏点：
  · 底部「粘回选中」按钮存在，且面板无裸 QPushButton（沿用 IconButton）
  · 无选中时「粘回」只提示、不炸
  · 有选中时：内容进剪贴板 + 计数埋点 + 焦点还原 + 发键（替身后端）
  · 粘贴失败 → 降级「已复制到剪贴板」提示，不抛异常
  · 配置关闭 fragment_paste_enabled → 退化为仅复制（不触发发键）
  · 置顶条目在列表中排到最前
"""

import os
import sys

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.paste_helper import (  # noqa: E402
    REASON_RESTORE_FAILED, PasteHelper,
)


class _FakeConfig:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value
        return True

    def save(self):
        pass


class _FakeClipboard:
    def __init__(self):
        self.text = None

    def put_text(self, text):
        self.text = text


class _FakeHost(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig()
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = _FakeClipboard()
        self._task_manager = None
        self._temp_asset_manager = None
        self.current_theme = "dark"
        self.anim_speed = 1.0
        self.toasts = []

    def refresh_page(self, _name):
        pass

    def show_toast(self, msg):
        self.toasts.append(msg)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _PanelApis:
    """面板用替身后端（可切成功 / 失败）。"""

    def __init__(self, ok=True, reason=None):
        self.ok = ok
        self.reason = reason
        self.sent = 0

    def get_foreground_window(self):
        return 4321

    def is_window(self, hwnd):
        return True

    def set_foreground_window(self, hwnd):
        return self.ok and self.reason != REASON_RESTORE_FAILED

    def send_ctrl_v(self):
        self.sent += 1
        return self.ok


def _make_panel(tmp_path, contents):
    from src.fragment_manager import FragmentManager
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    for c in contents:
        mgr.add_clipboard_text(c)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)
    return panel, mgr, host


def _inject_backend(panel, apis):
    panel._paste_helper = PasteHelper(apis=apis, sleep=lambda _s: None)


def _select_first_fragment(panel):
    lst = panel._frag_list
    for i in range(lst.count()):
        item = lst.item(i)
        if item.data(0x0100) is not None:  # UserRole == 256
            item.setSelected(True)
            return item.data(0x0100)
    return None


def test_no_bare_qpushbutton_in_panel(qapp, tmp_path):
    """护栏：面板禁止裸 QPushButton（统一走 SmoothButton/IconButton）。"""
    panel, _m, _h = _make_panel(tmp_path, ["a"])
    assert not panel.findChildren(QPushButton, None) or all(
        type(w).__module__.startswith("src.") or
        type(w).__name__ != "QPushButton"
        for w in panel.findChildren(QPushButton))


def test_paste_with_no_selection_shows_toast(qapp, tmp_path):
    panel, _m, host = _make_panel(tmp_path, ["a"])
    _inject_backend(panel, _PanelApis())
    panel._on_paste()
    assert any("选中" in t for t in host.toasts)


def test_paste_success_copies_and_sends(qapp, tmp_path):
    panel, _m, host = _make_panel(tmp_path, ["要粘回的内容"])
    apis = _PanelApis(ok=True)
    _inject_backend(panel, apis)
    panel._last_foreign_hwnd = 4321
    fid = _select_first_fragment(panel)
    assert fid is not None
    panel._on_paste()
    assert host._clipboard_monitor.text == "要粘回的内容"
    assert apis.sent == 1
    assert panel._reuse_counter.count_of("要粘回的内容") == 1


def test_paste_records_duplicate_count(qapp, tmp_path):
    panel, _m, _h = _make_panel(tmp_path, ["重复内容"])
    _inject_backend(panel, _PanelApis(ok=True))
    panel._last_foreign_hwnd = 4321
    _select_first_fragment(panel)
    panel._on_paste()
    panel._on_paste()
    assert panel._reuse_counter.count_of("重复内容") == 2
    assert panel._reuse_counter.duplicate_total() == 1


def test_paste_failure_degrades_to_copy(qapp, tmp_path):
    """还原焦点失败 → 内容仍进剪贴板 + 提示手动粘贴，不抛异常。"""
    panel, _m, host = _make_panel(tmp_path, ["降级内容"])
    apis = _PanelApis(ok=True, reason=REASON_RESTORE_FAILED)
    _inject_backend(panel, apis)
    panel._last_foreign_hwnd = 4321
    _select_first_fragment(panel)
    panel._on_paste()          # 不得抛
    assert host._clipboard_monitor.text == "降级内容"
    assert any("手动" in t or "剪贴板" in t for t in host.toasts)
    assert apis.sent == 0


def test_paste_disabled_by_config_only_copies(qapp, tmp_path):
    panel, _m, host = _make_panel(tmp_path, ["配置内容"])
    apis = _PanelApis(ok=True)
    _inject_backend(panel, apis)
    panel._last_foreign_hwnd = 4321
    panel._host._config.set("fragment_paste_enabled", False)
    _select_first_fragment(panel)
    panel._on_paste()
    assert host._clipboard_monitor.text == "配置内容"
    assert apis.sent == 0
    assert any("已复制" in t for t in host.toasts)


def test_paste_context_menu_entry_works(qapp, tmp_path):
    panel, mgr, host = _make_panel(tmp_path, ["右键粘回内容"])
    apis = _PanelApis(ok=True)
    _inject_backend(panel, apis)
    panel._last_foreign_hwnd = 4321
    fid = mgr.get_all_fragments()[0].fragment_id
    panel._paste_fragment(fid)
    assert host._clipboard_monitor.text == "右键粘回内容"
    assert apis.sent == 1


def test_pinned_fragment_rendered_first(qapp, tmp_path):
    from src.fragment_manager import FragmentManager
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    old = mgr.add_clipboard_text("老内容置顶")
    mgr.set_pinned(old, True)
    mgr.add_clipboard_text("新内容1")
    mgr.add_clipboard_text("新内容2")
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)
    # 第一条真实碎片行（跳过日期组头）应是置顶那条
    first_fid = None
    for i in range(panel._frag_list.count()):
        fid = panel._frag_list.item(i).data(0x0100)
        if fid is not None:
            first_fid = fid
            break
    assert first_fid == old


def test_pinned_row_has_star_marker(qapp, tmp_path):
    from src.fragment_manager import FragmentManager
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    fid = mgr.add_clipboard_text("带标记")
    mgr.set_pinned(fid, True)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)
    texts = [panel._frag_list.item(i).text()
             for i in range(panel._frag_list.count())]
    assert any("★" in t for t in texts)
