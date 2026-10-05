# -*- coding: utf-8 -*-
"""
碎片面板置顶（原 reuse 卡保留部分）集成单测（offscreen 真面板）。

护栏点：
  · 面板无裸 QPushButton（统一走 SmoothButton/IconButton）
  · 置顶条目在列表中排到最前
  · 置顶行有 ★ 标记（图标/字符承载按实现）

历史注：本文件原名 ``test_reuse_panel.py``，还覆盖「一键粘回」面板链路
（底部按钮 / 右键菜单 / 焦点还原 / 发键 / 配置开关退化）。粘回功能已于
2026-10-05 经用户拍板整体删除（真机不可靠 + 无区分反馈），相关用例与
按键替身随底层执行器模块一起移除；残留由闸门测试钉死。
"""

import os
import sys

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


    @property
    def config(self):
        return self._config

    @property
    def fragment_manager(self):
        return self._fragment_manager

    @property
    def note_manager(self):
        return self._note_manager

    @property
    def task_manager(self):
        return self._task_manager

    @property
    def temp_asset_manager(self):
        return self._temp_asset_manager

    def refresh_page(self, _name):
        pass

    def show_toast(self, msg):
        self.toasts.append(msg)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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
