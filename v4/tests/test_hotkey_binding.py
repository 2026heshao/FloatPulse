# -*- coding: utf-8 -*-
"""热键绑定助手回归（成熟化 4.3 D4）—— src/hotkey_binding.py。

钉住的行为（与 knowledge_ball 原手写样板逐字节等价的契约）：
  1. ConfigHotkeyBinding.reapply：先 unregister_all → pre_hooks → 开关判定
     → register；开关关闭保持注销态；注册失败只告警不抛异常
  2. 失败告警文案模板 {hotkey} 占位（截图文案逐字一致）
  3. reapply_hotkey_bindings：绑定表批量注册，单条失败不中断后续，
     返回成功条数
纯逻辑测试：GlobalHotkeyManager 以记录调用的假实现替身，不依赖 Win32。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.hotkey_binding import (ConfigHotkeyBinding,      # noqa: E402
                                reapply_hotkey_bindings)


class _FakeManager:
    """GlobalHotkeyManager 替身：记录调用序列，register 可编程成败"""

    def __init__(self, register_results=None):
        self.calls = []                # [("unregister_all",) / ("register", text, cb)]
        self._register_results = list(register_results or [])

    def unregister_all(self):
        self.calls.append(("unregister_all",))

    def register(self, hotkey_text, callback):
        self.calls.append(("register", hotkey_text, callback))
        if self._register_results:
            return self._register_results.pop(0)
        return True


class _FakeConfig:
    """ConfigManager 替身：dict 兜底的 get"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture()
def log_capture(monkeypatch):
    """把 hotkey_binding 的 get_logger 换成记录器，断言告警文案"""
    records = []

    class _Cap:
        def warning(self, msg, *args, **kwargs):
            records.append(("warning", msg))

        def info(self, msg, *args, **kwargs):
            records.append(("info", msg))

    monkeypatch.setattr("src.hotkey_binding.get_logger", lambda: _Cap())
    return records


def test_reapply_registers_enabled_hotkey(log_capture):
    """开关开 → 注销后按配置热键串注册回调，返回 True"""
    mgr = _FakeManager()
    cfg = _FakeConfig({"screenshot_enabled": True,
                       "screenshot_hotkey": "Ctrl+Alt+S"})
    binding = ConfigHotkeyBinding(
        mgr, cfg,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=int,
        fail_log="全局热键注册失败（可能被占用）：{hotkey}")

    assert binding.reapply() is True
    assert ("register", "Ctrl+Alt+S", int) in mgr.calls
    assert mgr.calls[0] == ("unregister_all",)


def test_reapply_disabled_keeps_unregistered(log_capture):
    """开关关 → unregister_all 后不注册任何键（与原样板 return 一致）"""
    mgr = _FakeManager()
    cfg = _FakeConfig({"screenshot_enabled": False})
    binding = ConfigHotkeyBinding(
        mgr, cfg,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=int,
        fail_log="截图热键注册失败（可能被占用）：{hotkey}")

    assert binding.reapply() is False
    assert mgr.calls == [("unregister_all",)]
    assert not any(c[0] == "register" for c in mgr.calls)


def test_pre_hooks_run_between_unregister_and_register(log_capture):
    """hide() 钩子的时序：unregister_all → hide → 开关判定 → register"""
    mgr = _FakeManager()
    cfg = _FakeConfig({"screenshot_enabled": True,
                       "screenshot_hotkey": "Ctrl+Alt+S"})
    order = []
    mgr.unregister_all = lambda: (order.append("unregister_all"),) and None

    def _hook():
        order.append("hook")

    real_register = mgr.register

    def _register(text, cb):
        order.append("register")
        return real_register(text, cb)

    mgr.register = _register
    binding = ConfigHotkeyBinding(
        mgr, cfg,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=int,
        fail_log="x{hotkey}", pre_hooks=(_hook,))

    assert binding.reapply() is True
    assert order == ["unregister_all", "hook", "register"]


def test_register_failure_warns_and_returns_false(log_capture):
    """注册失败（键位被占用）→ 只告警不抛异常，返回 False"""
    mgr = _FakeManager(register_results=[False])
    cfg = _FakeConfig({"screenshot_enabled": True,
                       "screenshot_hotkey": "Ctrl+Alt+F12"})
    binding = ConfigHotkeyBinding(
        mgr, cfg,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=int,
        fail_log="全局热键注册失败（可能被占用）：{hotkey}")

    assert binding.reapply() is False
    assert ("warning", "全局热键注册失败（可能被占用）：Ctrl+Alt+F12") \
        in log_capture


def test_reapply_reregisters_after_config_change(log_capture):
    """重注册时机：再次 reapply 必先踢掉旧注册再注册新串"""
    mgr = _FakeManager()
    cfg = _FakeConfig({"screenshot_enabled": True,
                       "screenshot_hotkey": "Ctrl+Alt+S"})
    binding = ConfigHotkeyBinding(
        mgr, cfg,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=int,
        fail_log="x{hotkey}")
    binding.reapply()
    cfg.values["screenshot_hotkey"] = "Ctrl+Alt+J"
    assert binding.reapply() is True
    texts = [c[1] for c in mgr.calls if c[0] == "register"]
    assert texts == ["Ctrl+Alt+S", "Ctrl+Alt+J"]
    assert mgr.calls[0] == ("unregister_all",)
    assert mgr.calls[2] == ("unregister_all",)   # 第二轮先注销再注册新串


def test_binding_manager_property(log_capture):
    """manager 特性暴露所属管理器（退出收尾 unregister_all 用）"""
    mgr = _FakeManager()
    binding = ConfigHotkeyBinding(
        mgr, _FakeConfig(), enabled_key="e", hotkey_key="h",
        default_hotkey="Ctrl+Alt+S", callback=int, fail_log="x")
    assert binding.manager is mgr


def test_reapply_hotkey_bindings_batch(log_capture):
    """绑定表批量：逐条注册、失败只告警不中断、返回成功条数。

    fail_log 为调用方拼好的最终文案（插件表传入含 act.hotkey/act.id 的
    f-string，与原实现一致）；ConfigHotkeyBinding 的 {hotkey} 模板替换
    只发生在单键绑定侧。
    """
    mgr = _FakeManager(register_results=[True, False, True])
    bindings = [
        ("Ctrl+Alt+1", int, "失败一：Ctrl+Alt+1"),
        ("Ctrl+Alt+2", int, "失败二：Ctrl+Alt+2"),
        ("Ctrl+Alt+3", int, "失败三：Ctrl+Alt+3"),
    ]
    ok = reapply_hotkey_bindings(mgr, bindings)
    assert ok == 2
    assert ("warning", "失败二：Ctrl+Alt+2") in log_capture
    # 注销在最前，三条注册按绑定表顺序
    assert mgr.calls[0] == ("unregister_all",)
    texts = [c[1] for c in mgr.calls if c[0] == "register"]
    assert texts == ["Ctrl+Alt+1", "Ctrl+Alt+2", "Ctrl+Alt+3"]


def test_reapply_hotkey_bindings_empty_table(log_capture):
    """空绑定表 → 只做注销（等价原 unregister_all + 循环零次）"""
    mgr = _FakeManager()
    assert reapply_hotkey_bindings(mgr, []) == 0
    assert mgr.calls == [("unregister_all",)]
