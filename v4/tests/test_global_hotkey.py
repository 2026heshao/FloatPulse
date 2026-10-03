# -*- coding: utf-8 -*-
"""全局热键管理器回归：多实例 id 不冲突（截图钉屏热键无效的根因）。

根因：RegisterHotKey(hwnd=NULL) 的 id 空间是线程级的；实例级计数器会让
第二个 GlobalHotkeyManager 也从 id=1 开始注册，实测同 id 重复注册返回 True
（顶替旧注册），WM_HOTKEY 按 id 路由时两个实例互相串台。

用真实 Win32 注册（Windows 环境），键位选冷门 F7/F8 降低环境占用干扰。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.global_hotkey import GlobalHotkeyManager, parse_hotkey, _IS_WINDOWS

pytestmark = pytest.mark.skipif(not _IS_WINDOWS, reason="仅 Windows")


def test_parse_hotkey_basic():
    assert parse_hotkey("Ctrl+Alt+S") == (0x2 | 0x1, ord("S"))
    assert parse_hotkey("Alt+Q") == (0x1, ord("Q"))
    assert parse_hotkey("") is None
    assert parse_hotkey("Ctrl") is None          # 无最终按键
    assert parse_hotkey("Ctrl+Alt+Shift+F12")[0] & 0x4


def test_two_managers_ids_never_collide():
    """两个实例各注册一个热键 → 内部 id 必须不同（串台回归）"""
    mgr_a = GlobalHotkeyManager()
    mgr_b = GlobalHotkeyManager()
    try:
        ok_a = mgr_a.register("Ctrl+Alt+F7", lambda: None)
        ok_b = mgr_b.register("Ctrl+Alt+F8", lambda: None)
        # 键位可能被环境占用（返回 False），但只要两边都注册成功，id 必不重叠
        if ok_a and ok_b:
            ids_a = set(mgr_a._hotkeys.keys())
            ids_b = set(mgr_b._hotkeys.keys())
            assert ids_a.isdisjoint(ids_b), f"id 冲突：{ids_a} ∩ {ids_b}"
    finally:
        mgr_a.unregister_all()
        mgr_b.unregister_all()


def test_register_unregister_cycle():
    mgr = GlobalHotkeyManager()
    try:
        ok = mgr.register("Ctrl+Alt+F9", lambda: None)
        if ok:  # 键位未被占用时才可断言
            assert mgr.is_registered("Ctrl+Alt+F9")
            mgr.unregister_text("Ctrl+Alt+F9")
            assert not mgr.is_registered("Ctrl+Alt+F9")
    finally:
        mgr.unregister_all()


def test_unregister_all_idempotent():
    """unregister_all 连调多次必须安全（1.4 退出收尾可能重复触发）"""
    mgr = GlobalHotkeyManager()
    try:
        ok = mgr.register("Ctrl+Alt+F11", lambda: None)
        if ok:  # 键位未被占用时才可断言注册态
            assert mgr.is_registered("Ctrl+Alt+F11")
        mgr.unregister_all()
        assert not mgr._hotkeys
        mgr.unregister_all()      # 第二次：无注册可注销，不得抛异常
        assert not mgr._hotkeys
    finally:
        mgr.unregister_all()      # 兜底再清一次（同样幂等）


def test_unregister_all_on_fresh_manager():
    """从未注册过的管理器直接 unregister_all 也安全（启动早期退出场景）"""
    mgr = GlobalHotkeyManager()
    mgr.unregister_all()
    assert not mgr._hotkeys
