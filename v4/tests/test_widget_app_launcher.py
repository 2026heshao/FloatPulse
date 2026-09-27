# -*- coding: utf-8 -*-
"""软件导航（widget_app_launcher）右键菜单单元测试。

覆盖：
  1. AppCardWidget.contextMenuEvent：弹菜单 + 事件就地消费（不冒泡主窗口）
  2. AppLauncherPage._on_card_action：launch / edit / locate / copypath /
     remove 五个动作分发
  3. 页面空白区右键：新增 / 管理
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.widget_app_launcher import (          # noqa: E402
    APP_ITEM_TEMPLATE, AppCardWidget, AppLauncherPage,
)


# ====================================================================
# 替身与夹具
# ====================================================================
class _FakeConfig:
    """ConfigManager 替身：内存 dict，绝不读写用户 config.json"""

    def __init__(self, data=None):
        self._data = {"app_card_size": 96, "app_auto_back_home": False,
                      "apps": []}
        if data:
            self._data.update(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_app(name="演示软件", exe=None):
    return {**APP_ITEM_TEMPLATE, "name": name,
            "exe_path": exe or "C:/nonexistent_demo.exe"}


def _make_page(apps=None, auto_back=False):
    cfg = _FakeConfig({"apps": apps or [],
                       "app_auto_back_home": auto_back})
    return AppLauncherPage(cfg)


# ====================================================================
# 卡片右键菜单：弹菜单 + 事件就地消费
# ====================================================================
class TestCardContextMenu:
    def test_context_menu_pops_and_consumes_event(
            self, qapp, monkeypatch):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QContextMenuEvent
        import src.widget_app_launcher as wal

        exec_called = []
        monkeypatch.setattr(
            wal.QMenu, "exec",
            lambda self, *a, **k: exec_called.append(True))

        card = AppCardWidget(_make_app(), 0, 96)
        ev = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(100, 100))
        card.contextMenuEvent(ev)
        assert exec_called == [True]
        assert ev.isAccepted()          # 就地消费 → 不冒泡到主窗口兜底

    def test_action_requested_signal_payload(self, qapp):
        """菜单动作经 actionRequested 信号携带 (index, action_id)"""
        got = []
        card = AppCardWidget(_make_app(), 3, 96)
        card.actionRequested.connect(lambda i, a: got.append((i, a)))
        card.actionRequested.emit(3, "copypath")
        assert got == [(3, "copypath")]


# ====================================================================
# 页面动作分发：launch / copypath / locate / edit / remove
# ====================================================================
class TestCardActionDispatch:
    def test_launch(self, qapp, monkeypatch):
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app()])
        seen = []

        def _fake_launch(app, parent=None):
            seen.append(app)
            return True

        monkeypatch.setattr(wal, "launch_app", _fake_launch)
        page._on_card_action(0, "launch")
        assert seen and seen[0]["name"] == "演示软件"

    def test_launch_auto_back_home(self, qapp, monkeypatch):
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app()], auto_back=True)
        monkeypatch.setattr(wal, "launch_app", lambda app, parent=None: True)
        got = []
        page.request_switch_to_home.connect(lambda: got.append(True))
        page._on_card_action(0, "launch")
        assert got == [True]

    def test_copypath_to_clipboard(self, qapp):
        from PyQt6.QtWidgets import QApplication
        page = _make_page(apps=[_make_app(exe="C:/some/dir/tool.exe")])
        page._on_card_action(0, "copypath")
        assert QApplication.clipboard().text() == "C:/some/dir/tool.exe"
        QApplication.clipboard().clear()   # 释放 OLE 剪贴板引用，防离屏 teardown 崩溃

    def test_locate_valid_path(self, qapp, tmp_path, monkeypatch):
        import subprocess
        import src.widget_app_launcher as wal
        exe = tmp_path / "real.exe"
        exe.write_bytes(b"MZ")
        page = _make_page(apps=[_make_app(exe=str(exe))])
        calls = []
        monkeypatch.setattr(wal.subprocess, "Popen",
                            lambda *a, **k: calls.append(a))
        page._on_card_action(0, "locate")
        assert calls and calls[0][0][0] == "explorer"
        assert str(exe) in calls[0][0]

    def test_locate_missing_path_warns(self, qapp, monkeypatch):
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app(exe="C:/no/such/file.exe")])
        warned = []
        monkeypatch.setattr(wal.QMessageBox, "warning",
                            lambda *a, **k: warned.append(a))
        page._on_card_action(0, "locate")
        assert len(warned) == 1 and "不存在" in warned[0][2]

    def test_edit_updates_entry_and_config(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QDialog
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app()])
        opened = []

        class _FakeEditDlg:
            def __init__(self, app_data, parent=None, theme=None):
                opened.append(app_data)
                self.result_app = {**app_data, "name": "改名的软件"}

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(wal, "AppEditDialog", _FakeEditDlg)
        page._on_card_action(0, "edit")
        assert opened and opened[0]["name"] == "演示软件"
        assert page.app_list[0]["name"] == "改名的软件"
        assert page._config_manager.get("apps")[0]["name"] == "改名的软件"

    def test_edit_cancel_keeps_entry(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QDialog
        import src.widget_app_launcher as wal

        class _FakeEditDlg:
            def __init__(self, app_data, parent=None, theme=None):
                self.result_app = {**app_data, "name": "不该生效"}

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(wal, "AppEditDialog", _FakeEditDlg)
        page = _make_page(apps=[_make_app()])
        page._on_card_action(0, "edit")
        assert page.app_list[0]["name"] == "演示软件"

    def test_remove_confirmed(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app("甲"), _make_app("乙")])
        monkeypatch.setattr(
            wal.QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._on_card_action(0, "remove")
        assert [a["name"] for a in page.app_list] == ["乙"]
        assert page._config_manager.get("apps") == page.app_list

    def test_remove_declined_keeps_entry(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app()])
        monkeypatch.setattr(
            wal.QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.No)
        page._on_card_action(0, "remove")
        assert len(page.app_list) == 1

    def test_index_out_of_range_noop(self, qapp, monkeypatch):
        import src.widget_app_launcher as wal
        page = _make_page(apps=[_make_app()])
        monkeypatch.setattr(wal, "launch_app",
                            lambda app, parent=None: True)
        for action in ("launch", "edit", "locate", "copypath", "remove"):
            page._on_card_action(99, action)     # 不抛异常即通过
        assert len(page.app_list) == 1


# ====================================================================
# 页面空白区右键：新增 / 管理
# ====================================================================
class TestPageContextMenu:
    def test_blank_menu_pops_and_consumes_event(self, qapp, monkeypatch):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QContextMenuEvent
        import src.widget_app_launcher as wal

        exec_called = []
        monkeypatch.setattr(
            wal.QMenu, "exec",
            lambda self, *a, **k: exec_called.append(True))

        page = _make_page()
        ev = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(100, 100))
        page.contextMenuEvent(ev)
        assert exec_called == [True]
        assert ev.isAccepted()

    def test_add_quick_appends_and_saves(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QDialog
        import src.widget_app_launcher as wal

        new_app = _make_app("新软件")

        class _FakeEditDlg:
            def __init__(self, app_data, parent=None, theme=None):
                self.result_app = new_app

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(wal, "AppEditDialog", _FakeEditDlg)
        page = _make_page()
        page._on_add_quick()
        assert [a["name"] for a in page.app_list] == ["新软件"]
        assert page._config_manager.get("apps") == page.app_list

    def test_cards_dispatch_via_page(self, qapp):
        """卡片 actionRequested 已连到页面分发（用业务效应验证，非自连自触发）"""
        from PyQt6.QtWidgets import QFrame, QApplication
        page = _make_page(apps=[
            _make_app("甲", exe="C:/x/a.exe"),
            _make_app("乙", exe="C:/x/b.exe"),
        ])
        cards = [c for c in page.findChildren(QFrame)
                 if c.objectName() == "appCard"]
        assert len(cards) == 2
        # 卡片发信号 → 页面 _on_card_action → 剪贴板出现对应路径
        cards[1].actionRequested.emit(1, "copypath")
        assert QApplication.clipboard().text() == "C:/x/b.exe"
        QApplication.clipboard().clear()   # 释放 OLE 剪贴板引用，防离屏 teardown 崩溃
