# -*- coding: utf-8 -*-
"""托盘气泡「点击直达」动作注入口回归（2026-10-10 更新气泡直达 Release 页）。

钉住的行为：
  1. 默认行为零变化：未注册动作时点击气泡 → 主窗 show/raise/activate
     + show_page(1)（任务页，任务提醒气泡的既有落点）
  2. 注册动作后点击 → 只执行动作（不跳任务页）且**一次性**：再点回落默认
  3. 动作抛异常：留痕不炸托盘，点击语义回落默认
  4. TTL 到点动作自动失效（点击走默认路径，防陈旧路由）
  5. show_message(action=...) 一站式：发气泡即注册
  6. 新气泡覆盖旧点击语义：show_message 未带 action → 清除已注册动作
     （messageClicked 是托盘图标级全局信号，防止旧动作串台到后续气泡）
  7. 源码钉子：wiring 侧动作经 QDesktopServices.openUrl 打开
     RELEASES_PAGE_URL，气泡文案自说明可点击

offscreen 运行；showMessage 一律打桩，绝不弹真实气泡。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest                                  # noqa: E402
from PyQt6.QtWidgets import (QApplication,                      # noqa: E402
                             QSystemTrayIcon)

from src.tray import TrayController                             # noqa: E402


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
    """ConfigManager 替身：dict 兜底 + 落盘计数"""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.save_count = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.save_count += 1


def _stub_show_message(tray):
    """打桩 showMessage：记录气泡参数，绝不弹真实通知"""
    tray._messages = []

    def _fake_show(title, body, icon=None, ms=0):
        tray._messages.append((title, body, ms))

    tray._icon_widget.showMessage = _fake_show


def _make_tray(qapp, config=None):
    mw = _FakeMainWindow()
    tray = TrayController(icon=qapp.windowIcon())
    tray.attach(mw, config or _FakeConfig())
    _stub_show_message(tray)
    return tray, mw


def _click_bubble(tray):
    """模拟点击气泡（messageClicked 托盘图标级全局信号）"""
    tray._icon_widget.messageClicked.emit()


def test_default_click_opens_task_page(qapp):
    """默认行为零变化：未注册动作 → 主窗三连 + 任务页"""
    tray, mw = _make_tray(qapp)
    _click_bubble(tray)
    assert mw.calls == ["show", "raise", "activate", "show_page:1"]


def test_registered_action_consumed_on_click(qapp):
    """注册动作后点击只执行动作；一次性消费，再点回落默认路径"""
    tray, mw = _make_tray(qapp)
    tray.set_message_action(lambda: mw.calls.append("open_release"))
    _click_bubble(tray)
    assert mw.calls == ["open_release"]
    _click_bubble(tray)
    assert mw.calls == ["open_release", "show", "raise", "activate",
                        "show_page:1"]


def test_action_exception_does_not_break_tray(qapp):
    """动作抛异常：留痕不炸托盘；动作已消费，下次点击回落默认"""
    tray, mw = _make_tray(qapp)

    def _boom():
        mw.calls.append("boom")
        raise RuntimeError("模拟动作失败")

    tray.set_message_action(_boom)
    _click_bubble(tray)                     # 异常不得外抛
    assert mw.calls == ["boom"]
    _click_bubble(tray)
    assert mw.calls[-1] == "show_page:1"


def test_action_ttl_expiry(qapp):
    """TTL 到点动作自动失效：点击走默认任务页路径"""
    tray, mw = _make_tray(qapp)
    tray.set_message_action(lambda: mw.calls.append("open_release"),
                            ttl_ms=50)
    QTest.qWait(150)
    _click_bubble(tray)
    assert mw.calls == ["show", "raise", "activate", "show_page:1"]


def test_show_message_one_stop_registration(qapp):
    """show_message(action=...) 一站式：发气泡即注册点击动作"""
    tray, mw = _make_tray(qapp)
    tray.show_message(
        "发现新版本 v9.9.9", "点击直达下载页",
        QSystemTrayIcon.MessageIcon.Information, 12000,
        action=lambda: mw.calls.append("open_release"))
    assert tray._messages == [("发现新版本 v9.9.9", "点击直达下载页", 12000)]
    _click_bubble(tray)
    assert mw.calls == ["open_release"]


def test_show_message_without_action_clears_pending(qapp):
    """新气泡覆盖旧点击语义：未带 action 的气泡清除已注册动作（防串台）"""
    tray, mw = _make_tray(qapp)
    tray.set_message_action(lambda: mw.calls.append("stale_release"))
    tray.show_message("番茄钟", "休息一下")   # 未带 action
    _click_bubble(tray)
    assert mw.calls == ["show", "raise", "activate", "show_page:1"]


def test_new_action_overrides_old(qapp):
    """重复注册：新动作覆盖旧动作（latest wins）"""
    tray, mw = _make_tray(qapp)
    tray.set_message_action(lambda: mw.calls.append("first"))
    tray.set_message_action(lambda: mw.calls.append("second"))
    _click_bubble(tray)
    assert mw.calls == ["second"]


def test_set_message_action_none_clears(qapp):
    """显式清除：set_message_action(None) 后点击回落默认路径"""
    tray, mw = _make_tray(qapp)
    tray.set_message_action(lambda: mw.calls.append("x"))
    tray.set_message_action(None)
    _click_bubble(tray)
    assert mw.calls == ["show", "raise", "activate", "show_page:1"]


def test_wiring_sources_open_release_page():
    """源码钉子：wiring 侧动作走 QDesktopServices.openUrl + RELEASES_PAGE_URL"""
    wiring_path = os.path.join(BASE, "src", "wiring.py")
    with open(wiring_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "QDesktopServices.openUrl" in src
    assert "RELEASES_PAGE_URL" in src
    assert "点击直达下载页" in src
