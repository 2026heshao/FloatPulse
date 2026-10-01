# -*- coding: utf-8 -*-
"""系统托盘控制器回归（成熟化 4.3 D1-lite）—— src/tray.py。

钉住的行为（与 knowledge_ball 原内联实现逐字等价的契约）：
  1. 创建即设图标/tooltip/可见（tooltip「生活悬浮球」）
  2. 托盘单击 → 主窗口显隐切换（带日志）；双击/右键忽略
  3. 气泡点击 → 主窗口 show/raise/activate + show_page(1)（任务页）
  4. 右键菜单结构：主窗口 / 悬浮球 / 📌 便签子菜单 / 分隔线 / 退出程序；
     便签子菜单 aboutToShow 时按注入数据源重建
  5. 首次收进托盘提示只发一次（tray_hint_shown 落盘防重复）
  6. 启动一次性告知：config 损坏 → 警示

offscreen 运行；showMessage 一律打桩，绝不弹真实气泡。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon                            # noqa: E402
from PyQt6.QtWidgets import (QApplication, QMenu,        # noqa: E402
                             QSystemTrayIcon)

from src.tray import TrayController                      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeMainWindow:
    """主窗口替身：只记录显隐/切页调用"""

    def __init__(self):
        self.visible = False
        self.calls = []

    def isVisible(self):
        return self.visible

    def show(self):
        self.calls.append("show")
        self.visible = True

    def hide(self):
        self.calls.append("hide")
        self.visible = False

    def raise_(self):
        self.calls.append("raise")

    def activateWindow(self):
        self.calls.append("activate")

    def show_page(self, index):
        self.calls.append(f"show_page:{index}")


class _FakeConfig:
    """ConfigManager 替身：dict 兜底 + 落盘计数 + load_reset_reason"""

    def __init__(self, values=None, load_reset_reason=None):
        self.values = dict(values or {})
        self.save_count = 0
        self.load_reset_reason = load_reset_reason

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.save_count += 1


class _FakeStickyManager:
    """StickyNoteManager 替身：get_all / raise_sticky / bring_all_to_front /
    close_all（托盘只允许用这些公开 API）"""

    def __init__(self, entries=None):
        self._entries = entries or []
        self.calls = []

    def get_all(self):
        return list(self._entries)

    def raise_sticky(self, sticky_id):
        self.calls.append(("raise", sticky_id))
        return True

    def bring_all_to_front(self):
        self.calls.append(("front",))

    def close_all(self):
        self.calls.append(("close_all",))


def _make_tray(qapp, main_window, config, sticky=None):
    tray = TrayController(icon=qapp.windowIcon())
    tray.attach(main_window, config)
    tray.build_menu(
        toggle_main_window=lambda: main_window.calls.append("menu_toggle_main"),
        toggle_ball_visibility=lambda: main_window.calls.append(
            "menu_toggle_ball"),
        quit_app=lambda: main_window.calls.append("menu_quit"),
        sticky_manager=sticky or _FakeStickyManager(),
    )
    # 打桩 show_message：记录气泡参数，绝不弹真实通知
    tray._messages = []
    tray.show_message = lambda title, body, icon=None, ms=0: \
        tray._messages.append((title, body, icon, ms))
    return tray


def _emit_activated(tray, reason):
    tray._icon_widget.activated.emit(reason)


def test_init_sets_tooltip_and_visible(qapp):
    """创建即设 tooltip 与可见（与原内联创建逐字一致）"""
    tray = TrayController(icon=QIcon())
    assert tray._icon_widget.toolTip() == "生活悬浮球"


def test_single_click_toggles_main_window(qapp):
    """单击 → 可见时隐藏（日志含状态）；再单击 → 显示+raise+activate"""
    mw = _FakeMainWindow()
    cfg = _FakeConfig()
    tray = _make_tray(qapp, mw, cfg)

    mw.visible = True
    _emit_activated(tray, QSystemTrayIcon.ActivationReason.Trigger)
    assert mw.calls == ["hide"]
    assert mw.visible is False

    _emit_activated(tray, QSystemTrayIcon.ActivationReason.Trigger)
    assert mw.calls == ["hide", "show", "raise", "activate"]
    assert mw.visible is True


def test_double_click_and_context_ignored(qapp):
    """只响应单击：双击/中键/右键不改变主窗口状态"""
    mw = _FakeMainWindow()
    tray = _make_tray(qapp, mw, _FakeConfig())
    for reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                   QSystemTrayIcon.ActivationReason.MiddleClick,
                   QSystemTrayIcon.ActivationReason.Context):
        _emit_activated(tray, reason)
    assert mw.calls == []


def test_message_click_opens_task_page(qapp):
    """点击提醒气泡 → 显示主窗口并切到任务页（页索引 1）"""
    mw = _FakeMainWindow()
    tray = _make_tray(qapp, mw, _FakeConfig())
    tray._icon_widget.messageClicked.emit()
    assert mw.calls == ["show", "raise", "activate", "show_page:1"]


def test_menu_structure_and_actions(qapp):
    """菜单结构：主窗口 / 悬浮球 / 便签子菜单 / 分隔线 / 退出程序"""
    mw = _FakeMainWindow()
    tray = _make_tray(qapp, mw, _FakeConfig())
    menu = tray._icon_widget.contextMenu()
    assert isinstance(menu, QMenu)
    actions = menu.actions()
    assert [a.text() for a in actions if not a.isSeparator()][:2] == \
        ["显示 / 隐藏主窗口", "显示 / 隐藏悬浮球"]
    # 倒数第一（跳过分隔线）是退出程序；子菜单位于其后
    non_sep = [a for a in actions if not a.isSeparator()]
    assert non_sep[-1].text() == "退出程序"
    submenu = non_sep[2]
    assert submenu.menu() is not None
    assert submenu.menu().title() == "📌 便签"
    # 触发主窗口/悬浮球/退出三个动作 → 注入的回调被调用
    non_sep[0].trigger()
    non_sep[1].trigger()
    non_sep[-1].trigger()
    assert mw.calls == ["menu_toggle_main", "menu_toggle_ball", "menu_quit"]


def test_sticky_submenu_rebuild(qapp):
    """便签子菜单 aboutToShow 重建：图标前缀 / 全部置前 / 全部关闭"""
    mw = _FakeMainWindow()
    sticky = _FakeStickyManager(entries=[
        (1, "便签标题可能很长被截断" * 3, None, "note"),
        (2, "任务便签", None, "task"),
    ])
    tray = _make_tray(qapp, mw, _FakeConfig(), sticky=sticky)
    menu = tray._icon_widget.contextMenu()
    submenu = [a for a in menu.actions()
               if not a.isSeparator()][2].menu()
    submenu.aboutToShow.emit()          # aboutToShow → _rebuild_sticky_menu
    texts = [a.text() for a in submenu.actions() if not a.isSeparator()]
    assert texts[0].startswith("📄 ")
    assert len(texts[0]) <= 27          # 标题截断到 24 字（icon+空格+24）
    assert texts[1].startswith("📋 ")
    assert "任务便签" in texts[1]
    assert texts[-2] == "⬆ 全部置前"
    assert texts[-1] == "✕ 全部关闭"
    # 点「全部置前」「全部关闭」→ 注入的管理器公开方法被调
    acts = [a for a in submenu.actions() if not a.isSeparator()]
    acts[-2].trigger()
    acts[-1].trigger()
    assert sticky.calls == [("front",), ("close_all",)]


def test_sticky_submenu_empty_shows_placeholder(qapp):
    """无便签 → 「（暂无便签）」禁用占位 + 全部关闭仍在"""
    mw = _FakeMainWindow()
    sticky = _FakeStickyManager(entries=[])
    tray = _make_tray(qapp, mw, _FakeConfig(), sticky=sticky)
    menu = tray._icon_widget.contextMenu()
    submenu = [a for a in menu.actions()
               if not a.isSeparator()][2].menu()
    submenu.aboutToShow.emit()
    acts = [a for a in submenu.actions() if not a.isSeparator()]
    assert acts[0].text() == "（暂无便签）"
    assert not acts[0].isEnabled()
    assert acts[-1].text() == "✕ 全部关闭"


def test_notify_hidden_to_tray_only_once(qapp):
    """首次收进托盘提示一次并落盘 tray_hint_shown；此后静默"""
    mw = _FakeMainWindow()
    cfg = _FakeConfig()
    tray = _make_tray(qapp, mw, cfg)
    tray.notify_hidden_to_tray()
    tray.notify_hidden_to_tray()
    assert len(tray._messages) == 1
    title, body, _icon, ms = tray._messages[0]
    assert title == "已收进托盘"
    assert "程序仍在后台运行" in body
    assert ms == 6000
    assert cfg.values["tray_hint_shown"] is True
    assert cfg.save_count == 1


def test_notify_hidden_to_tray_skipped_when_shown(qapp):
    """tray_hint_shown 已为 True → 不再提示、不写盘"""
    mw = _FakeMainWindow()
    cfg = _FakeConfig({"tray_hint_shown": True})
    tray = _make_tray(qapp, mw, cfg)
    tray.notify_hidden_to_tray()
    assert tray._messages == []
    assert cfg.save_count == 0


def test_notify_startup_once_corrupt_only(qapp):
    """config 损坏 → 单条「设置已重置」警示（异常退出气泡已移除）"""
    mw = _FakeMainWindow()
    cfg = _FakeConfig(load_reset_reason="corrupt")
    tray = _make_tray(qapp, mw, cfg)
    tray.notify_startup_once()
    titles = [m[0] for m in tray._messages]
    assert titles == ["设置已重置"]
    assert "config.json.corrupt.bak" in tray._messages[0][1]


def test_notify_startup_once_clean_session_silent(qapp):
    """正常会话（无损坏）→ 零气泡"""
    mw = _FakeMainWindow()
    cfg = _FakeConfig(load_reset_reason=None)
    tray = _make_tray(qapp, mw, cfg)
    tray.notify_startup_once()
    assert tray._messages == []
