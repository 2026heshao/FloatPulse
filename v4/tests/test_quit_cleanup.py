# -*- coding: utf-8 -*-
"""退出显式收尾回归（1.4）—— knowledge_ball._shutdown_once。

钉死的行为：
  A 幂等：连调多次只有第一次实际执行（_safe_quit 与 aboutToQuit 都会
    触发收尾，不能重复注销/重复 stop）
  B 按真实接线跑一遍：三个热键管理器 unregister_all + AI_SERVER.stop +
    剪贴板监听 stop，各组件只被调一次
  C 单步失败（抛异常）只跳过该步，后续步骤照常执行
  D 晚绑定安全：组件尚未创建（NameError，启动早期退出）不算致命
  E 未收尾过的空状态 / 已收尾标记互不串扰（新 state 重新生效）
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import knowledge_ball                      # noqa: E402


# ---------------- 测试替身 ----------------
class _FakeHotkeyMgr:
    """GlobalHotkeyManager 替身：记录 unregister_all 调用次数"""

    def __init__(self, calls, key):
        self._calls = calls
        self._key = key

    def unregister_all(self):
        self._calls[self._key] = self._calls.get(self._key, 0) + 1


class _FakeAiServer:
    def __init__(self, calls):
        self._calls = calls

    def stop(self):
        self._calls["ai_stop"] = self._calls.get("ai_stop", 0) + 1


class _FakeClipboardMonitor:
    def __init__(self, calls):
        self._calls = calls

    def stop(self):
        self._calls["clip_stop"] = self._calls.get("clip_stop", 0) + 1


def _build_wiring(calls, state):
    """复刻 main() 里 _shutdown_resources 的真实接线（步骤与顺序一致）"""
    hotkey_mgr = _FakeHotkeyMgr(calls, "hotkey")
    shot_hotkey_mgr = _FakeHotkeyMgr(calls, "shot")
    plugin_hotkey_mgr = _FakeHotkeyMgr(calls, "plugin")
    ai_server = _FakeAiServer(calls)
    clipboard_monitor = _FakeClipboardMonitor(calls)

    def _shutdown_resources():
        knowledge_ball._shutdown_once(state, [
            ("快捕条热键注销", lambda: hotkey_mgr.unregister_all()),
            ("截图热键注销", lambda: shot_hotkey_mgr.unregister_all()),
            ("插件热键注销", lambda: plugin_hotkey_mgr.unregister_all()),
            ("AI 本地服务停止", lambda: ai_server.stop()),
            ("剪贴板监听停止", lambda: clipboard_monitor.stop()),
        ])

    return _shutdown_resources


def test_shutdown_once_idempotent():
    """连调两次只生效一次（mock 计数口径）"""
    calls = {}
    state = {"done": False}
    quit_resources = _build_wiring(calls, state)
    assert quit_resources() is None          # 收尾函数无返回值（副作用式）
    assert knowledge_ball._shutdown_once(state, []) is False  # 第二次幂等跳过
    assert calls == {"hotkey": 1, "shot": 1, "plugin": 1,
                     "ai_stop": 1, "clip_stop": 1}


def test_shutdown_once_real_wiring_twice():
    """_safe_quit 先收尾 → aboutToQuit 再收尾：各组件仍只生效一次"""
    calls = {}
    state = {"done": False}
    quit_resources = _build_wiring(calls, state)
    quit_resources()      # 模拟托盘「退出程序」→ _safe_quit 内的收尾
    quit_resources()      # 模拟 aboutToQuit 内的重复收尾
    quit_resources()      # 再来一次也不该有任何副作用
    assert calls == {"hotkey": 1, "shot": 1, "plugin": 1,
                     "ai_stop": 1, "clip_stop": 1}


def test_shutdown_once_swallows_step_errors():
    """单步抛异常只告警，不阻断后续步骤（退出链路绝不卡死）"""
    calls = []
    state = {"done": False}
    steps = [
        ("会炸的一步", lambda: 1 / 0),
        ("正常步骤", lambda: calls.append("ok")),
    ]
    assert knowledge_ball._shutdown_once(state, steps) is True
    assert calls == ["ok"]


def test_shutdown_once_tolerates_unbound_components():
    """晚绑定安全：组件未创建时步骤抛 NameError，其余步骤照常执行"""
    calls = []
    state = {"done": False}
    # 故意引用未定义的 hotkey_mgr，模拟启动早期异常退出时的收尾
    steps = [
        ("未创建的组件", lambda: hotkey_mgr.unregister_all()),  # noqa: F821
        ("已就绪的组件", lambda: calls.append("ok")),
    ]
    assert knowledge_ball._shutdown_once(state, steps) is True
    assert calls == ["ok"]


def test_shutdown_once_new_state_rearms():
    """换新的 state 标记重新生效（各次运行/各处调用互不串扰）"""
    calls = []
    first_state = {"done": False}
    assert knowledge_ball._shutdown_once(
        first_state, [("A", lambda: calls.append("A"))]) is True
    assert knowledge_ball._shutdown_once(
        first_state, [("A", lambda: calls.append("A"))]) is False
    second_state = {"done": False}
    assert knowledge_ball._shutdown_once(
        second_state, [("A", lambda: calls.append("A"))]) is True
    assert calls == ["A", "A"]
