# -*- coding: utf-8 -*-
"""
====================================================================
全局系统热键模块  -  GlobalHotkeyManager
====================================================================
基于 Win32 RegisterHotKey/UnregisterHotKey 的全局热键管理，
配合 Qt 原生事件过滤器接收 WM_HOTKEY 消息，无需额外线程。

设计要点：
  1. RegisterHotKey(hwnd=None) → WM_HOTKEY 投递到当前线程消息队列，
     由 Qt 主事件循环泵出，QAbstractNativeEventFilter 拦截分发
  2. 热键字符串格式："Ctrl+Alt+K"、"Alt+Q"、"Ctrl+Shift+F5"，
     支持 Ctrl/Alt/Shift/Win 修饰键 + 字母/数字/F1~F12
  3. MOD_NOREPEAT：按住不重复触发
  4. 同一热键组合被其他程序占用时注册失败（返回 False，不崩溃）
  5. 程序退出前务必 unregister_all()，否则组合键残留占用到进程结束

仅 Windows 可用（ctypes.windll）；其他平台所有方法安全降级为空操作。
====================================================================
"""

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter

try:
    _user32 = ctypes.windll.user32
    _IS_WINDOWS = True
except AttributeError:
    _user32 = None
    _IS_WINDOWS = False

WM_HOTKEY = 0x0312

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000

_MOD_MAP = {
    "ctrl":   _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "alt":    _MOD_ALT,
    "shift":  _MOD_SHIFT,
    "win":    _MOD_WIN,
    "meta":   _MOD_WIN,
}


def parse_hotkey(text: str):
    """
    解析热键字符串为 (modifiers, virtual_key)。

    - 格式："Ctrl+Alt+K"（不区分大小写，顺序任意，最后一个为按键）
    - 支持：Ctrl/Control/Alt/Shift/Win/Meta + 字母/数字/F1~F12
    - 解析失败返回 None
    """
    if not _IS_WINDOWS:
        return None
    if not text or not text.strip():
        return None

    mods = 0
    key_vk = 0
    tokens = [t.strip().lower() for t in text.split("+") if t.strip()]
    if not tokens:
        return None

    for i, token in enumerate(tokens):
        is_last = (i == len(tokens) - 1)
        if token in _MOD_MAP:
            if is_last and len(tokens) > 1:
                return None  # 修饰键不能作为最终按键
            mods |= _MOD_MAP[token]
            continue
        if not is_last:
            return None  # 修饰键必须在最后一段按键之前

        # F1~F24
        if len(token) >= 2 and token[0] == "f" and token[1:].isdigit():
            n = int(token[1:])
            if 1 <= n <= 24:
                key_vk = 0x70 + n - 1
                continue
            return None
        # 单个字母 / 数字
        if len(token) == 1 and (token.isalpha() or token.isdigit()):
            key_vk = ord(token.upper())
            continue
        # 其他字符：用 VkKeyScanW 映射（仅接受无 Shift 组合的简单映射）
        if len(token) == 1:
            res = _user32.VkKeyScanW(wintypes.WCHAR(token))
            if res != -1 and (res >> 8) & 0xFF == 0:
                key_vk = res & 0xFF
                continue
        return None

    if key_vk == 0 or mods == 0:
        return None  # 必须至少带一个修饰键，避免抢占普通按键
    return (mods, key_vk)


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """
    全局热键管理器（QAbstractNativeEventFilter）。

    用法：
        mgr = GlobalHotkeyManager()
        app.eventDispatcher().installNativeEventFilter(mgr)
        mgr.register("Ctrl+Alt+K", callback)
        ... 退出前：
        mgr.unregister_all()
    """

    def __init__(self):
        super().__init__()
        self._next_id = 1
        # hotkey_id -> (callback, hotkey_text)
        self._hotkeys = {}

    # ---------------- 注册 / 注销 ----------------
    def register(self, hotkey_text: str, callback) -> bool:
        """注册全局热键，成功返回 True；组合非法或被占用返回 False"""
        parsed = parse_hotkey(hotkey_text)
        if parsed is None:
            return False
        mods, vk = parsed
        new_id = self._next_id
        self._next_id += 1
        if not _user32.RegisterHotKey(None, new_id, mods | _MOD_NOREPEAT, vk):
            return False
        self._hotkeys[new_id] = (callback, hotkey_text)
        return True

    def unregister_text(self, hotkey_text: str):
        """注销指定热键字符串的所有注册"""
        dead_ids = [hid for hid, (_, text) in self._hotkeys.items()
                    if text == hotkey_text]
        for hid in dead_ids:
            _user32.UnregisterHotKey(None, hid)
            self._hotkeys.pop(hid, None)

    def unregister_all(self):
        """注销全部热键（程序退出前调用）"""
        for hid in list(self._hotkeys.keys()):
            _user32.UnregisterHotKey(None, hid)
        self._hotkeys.clear()

    def is_registered(self, hotkey_text: str) -> bool:
        return any(text == hotkey_text for _, text in self._hotkeys.values())

    # ---------------- 原生事件过滤 ----------------
    def nativeEventFilter(self, eventType, message):
        """拦截 WM_HOTKEY：wParam 即注册时返回的 id"""
        if _IS_WINDOWS and eventType == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    entry = self._hotkeys.get(int(msg.wParam))
                    if entry is not None:
                        entry[0]()
            except Exception:
                pass  # 消息解析失败不阻断事件流
        return False, 0
