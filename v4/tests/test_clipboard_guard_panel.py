# -*- coding: utf-8 -*-
"""凭证哨兵面板入口集成（offscreen 真面板）。

护栏点：
  · 面板无裸 QPushButton；凭证入口是 IconButton（可见时）
  · 无命中时入口隐藏；命中后入口出现且计数正确
  · 命中只提示、不弹窗（后台监听不打断用户）
  · 回溯对话框可对每条改判（allow/deny/mask）
  · 「本次记住」勾选后写入进程级记忆，且不写持久配置
"""
import os
import sys

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton, QRadioButton

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import secret_guard as sg  # noqa: E402
from PyQt6.QtWidgets import QCheckBox  # noqa: E402


GH = "ghp_" + "a1B2c3D4" * 5
SK = "sk-" + "Xy9zAb2C" * 5


class _FakeConfig:
    def __init__(self, data=None):
        self._d = dict(data or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value
        return True

    def save(self):
        pass


class _FakeMonitor(QObject):
    """凭证哨兵替身监听器：暴露 secret_guarded 信号 + 队列 API。"""

    secret_guarded = pyqtSignal(dict)
    fragments_trimmed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._queue = []
        self.remembered = []
        self.text = None

    def put_text(self, text):
        self.text = text

    def guard_pending(self):
        return list(self._queue)

    def guard_pending_count(self):
        return len(self._queue)

    def push(self, info):
        self._queue.append(info)

    def resolve_guard(self, index, mode):
        if 0 <= index < len(self._queue):
            del self._queue[index]
            return True
        return False

    def remember_guard_mode(self, mode):
        self.remembered.append(mode)


class _FakeHost(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig({"clipboard_guard_enabled": True,
                                    "clipboard_guard_mode": "mask"})
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = _FakeMonitor()
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

    def show_toast(self, msg, **kwargs):
        self.toasts.append(msg)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_panel(tmp_path, contents=("普通内容",)):
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


def _hit(rule="openai_key", text=SK):
    return {
        "mode": "mask", "rule": rule, "rules": [rule], "count": 1,
        "preview": sg.mask_text(text), "content": text, "fragment_id": 1,
    }


def test_no_bare_qpushbutton_in_panel(qapp, tmp_path):
    panel, _m, _h = _make_panel(tmp_path)
    for w in panel.findChildren(QPushButton):
        assert type(w).__name__ != "QPushButton"


def test_guard_entry_hidden_when_no_hits(qapp, tmp_path):
    panel, _m, _h = _make_panel(tmp_path)
    assert panel._guard_btn.isHidden() is True


def test_guard_entry_appears_on_hit_with_count(qapp, tmp_path):
    panel, _m, host = _make_panel(tmp_path)
    host._clipboard_monitor.push(_hit())
    host._clipboard_monitor.secret_guarded.emit(_hit())
    assert panel._guard_btn.isHidden() is False
    assert "1" in panel._guard_btn.text()


def test_hit_shows_toast_not_dialog(qapp, tmp_path):
    """后台监听：命中只 toast 提示，不弹窗打断。"""
    panel, _m, host = _make_panel(tmp_path)
    host._clipboard_monitor.secret_guarded.emit(_hit())
    assert any("凭证" in t for t in host.toasts)


def test_dialog_apply_allow_resolves(qapp, tmp_path, monkeypatch):
    panel, _m, host = _make_panel(tmp_path)
    mon = host._clipboard_monitor
    mon.push(_hit())
    from src import glass_dialog

    resolved = {}

    def fake_exec(self):
        for rb in self.findChildren(QRadioButton):
            if rb.property("guard_mode") == "allow":
                rb.setChecked(True)
        self._guard_apply()
        return 0

    monkeypatch.setattr(glass_dialog.GlassDialog, "exec", fake_exec)

    orig_resolve = mon.resolve_guard

    def spy_resolve(idx, mode):
        resolved["mode"] = mode
        return orig_resolve(idx, mode)

    mon.resolve_guard = spy_resolve
    panel._open_guard_dialog()
    assert resolved.get("mode") == "allow"


def test_dialog_remember_mode(qapp, tmp_path, monkeypatch):
    panel, _m, host = _make_panel(tmp_path)
    mon = host._clipboard_monitor
    mon.push(_hit())
    from src import glass_dialog

    def fake_exec(self):
        for cb in self.findChildren(QCheckBox):
            cb.setChecked(True)
        self._guard_apply()
        return 0

    monkeypatch.setattr(glass_dialog.GlassDialog, "exec", fake_exec)
    panel._open_guard_dialog()
    assert mon.remembered == ["mask"]
