# -*- coding: utf-8 -*-
"""
====================================================================
一键粘回（还原焦点 + 发 Ctrl+V）  -  paste_helper
====================================================================
碎片面板「粘回」动作的底层实现：把碎片内容写进剪贴板后，**记住当前
前台窗口 → 还原焦点 → 发一次 Ctrl+V**，把内容直接落到用户原本在用的
输入框里，省掉「复制 → 切窗口 → 粘贴」三步。

**纯 stdlib（ctypes），零 PyQt6 依赖** —— 这样才能被独立单测直接 import
并注入假的窗口句柄 / 假的按键发射器，不需要离屏 Qt、不需要真实按键。

设计要点（任务卡 reuse 护栏 §3/§4）
==================================
  1. 焦点抢夺是本动作**唯一的真风险**：Qt 面板本身可能抢焦点，若粘错
     窗口比不粘还糟。因此顺序严格为
        ``记录 GetForegroundWindow()`` → ``SetForegroundWindow`` 还原
        → 短暂等待 → ``SendInput`` 发 Ctrl+V。
  2. 任一环节不可用（非 Windows / API 缺失 / 句柄为 0 / 还原失败）
     一律**降级**为「只复制到剪贴板」：返回 ``(False, 原因)``，由 UI
     侧提示用户手动 Ctrl+V。本模块**永不抛异常到调用方**。
  3. 所有外部依赖（取前台窗口 / 设前台窗口 / 发按键 / 睡眠）都可在
     构造时注入替身，便于单测走完全链路与降级路径。

真实平台为 Windows 时用 ``user32``；非 Windows 时全部 API 显式置 None，
``available`` 为 False，调用方直接走降级。
"""

import sys
import time

IS_WINDOWS = sys.platform == "win32"

# 按键虚拟码：Ctrl / V（与 Win32 VK 定义一致）
VK_CONTROL = 0x11
VK_V = 0x56

# 还原焦点后、发键前的等待（秒）：SetForegroundWindow 生效不是即时的，
# 抢在窗口真正激活前发键会落到错误目标。实测 60~120ms 足够稳。
FOCUS_SETTLE_SECONDS = 0.08

# 降级 / 失败原因码（UI 侧据此给不同提示）
REASON_NOT_WINDOWS = "not_windows"
REASON_NO_FOREGROUND = "no_foreground"
REASON_RESTORE_FAILED = "restore_failed"
REASON_SEND_FAILED = "send_failed"


# ====================================================================
# 真实平台后端（惰性探测，失败即不可用，不抛）
# ====================================================================
def _load_win32_apis():
    """绑定 user32 的真实现（成功返回 _Win32Apis，失败返回 None）。"""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        # 显式声明 restype/argtypes：64 位下句柄是 8 字节，
        # 不声明会被 ctypes 默认按 int（4 字节）截断，句柄高位丢失。
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        return _RealApis(user32)
    except Exception:
        return None


class _RealApis:
    """真实 Windows API 封装（与替身同接口）。"""

    def __init__(self, user32):
        self._user32 = user32

    def get_foreground_window(self):
        return self._user32.GetForegroundWindow()

    def is_window(self, hwnd) -> bool:
        if not hwnd:
            return False
        return bool(self._user32.IsWindow(hwnd))

    def set_foreground_window(self, hwnd) -> bool:
        return bool(self._user32.SetForegroundWindow(hwnd))

    def send_ctrl_v(self) -> bool:
        """经 SendInput 发一次 Ctrl+V（keydown/up 成对，中间夹 V）。"""
        import ctypes
        from ctypes import wintypes

        try:
            user32 = self._user32
            # INPUT 结构（KEYBDINPUT）—— Win32 定义，大小随位数变化
            ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(
                ctypes.c_void_p) == 8 else ctypes.c_ulong

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD),
                            ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ulong_ptr)]

            class _INPUTunion(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD),
                            ("u", _INPUTunion)]

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002

            def _key(vk, up=False):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.u.ki.wVk = vk
                inp.u.ki.wScan = 0
                inp.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
                inp.u.ki.time = 0
                inp.u.ki.dwExtraInfo = 0
                return inp

            seq = (_key(VK_CONTROL), _key(VK_V),
                   _key(VK_V, True), _key(VK_CONTROL, True))
            arr = (INPUT * len(seq))(*seq)
            sent = user32.SendInput(len(seq), ctypes.byref(arr),
                                    ctypes.sizeof(INPUT))
            return sent == len(seq)
        except Exception:
            return False


_apis = None
_probed = False


def _get_apis():
    """惰性探测真实后端（只探测一次）。"""
    global _apis, _probed
    if not _probed:
        _apis = _load_win32_apis()
        _probed = True
    return _apis


# ====================================================================
# 对外主入口
# ====================================================================
class PasteHelper:
    """一键粘回执行器。

    所有外部依赖可注入（``apis`` / ``sleep``），默认真实后端 + time.sleep；
    单测传替身即可覆盖「成功 / 还原失败 / 无前台窗口 / 发键失败 / 非
    Windows」全部路径。

    典型用法::

        helper = PasteHelper()
        hwnd = helper.capture_foreground()      # 弹窗前记录前台窗口
        ... 复制内容到剪贴板 ...
        ok, reason = helper.paste_to(hwnd)      # 还原焦点 + Ctrl+V
        if not ok:
            show_toast("已复制到剪贴板，请手动粘贴")
    """

    def __init__(self, apis=None, sleep=None, settle=FOCUS_SETTLE_SECONDS):
        self._apis = apis if apis is not None else _get_apis()
        self._sleep = sleep or time.sleep
        self._settle = settle

    # ---- 能力探测 ----
    @property
    def available(self) -> bool:
        """底层 API 是否可用（False → 调用方应直接走「只复制」降级）。"""
        return self._apis is not None

    # ---- 记录前台窗口 ----
    def capture_foreground(self):
        """记录当前前台窗口句柄；不可用 / 无窗口时返回 0。

        必须在弹出任何 Qt 窗口**之前**调用，否则记录到的可能是自己。
        """
        if self._apis is None:
            return 0
        try:
            hwnd = self._apis.get_foreground_window()
            return hwnd or 0
        except Exception:
            return 0

    # ---- 还原焦点 + 发键 ----
    def paste_to(self, hwnd) -> tuple:
        """把焦点还原到 ``hwnd`` 并发一次 Ctrl+V。

        返回 ``(成功?, 原因码或 None)``：
          · 成功 → ``(True, None)``
          · 失败 → ``(False, REASON_*)``，调用方据此降级为「只复制」。
        **不抛异常**。
        """
        if self._apis is None:
            return False, REASON_NOT_WINDOWS
        if not hwnd or not self._apis.is_window(hwnd):
            return False, REASON_NO_FOREGROUND
        try:
            if not self._apis.set_foreground_window(hwnd):
                return False, REASON_RESTORE_FAILED
            # 等窗口真正激活再发键，避免落到旧目标
            self._sleep(self._settle)
            if not self._apis.send_ctrl_v():
                return False, REASON_SEND_FAILED
            return True, None
        except Exception:
            return False, REASON_SEND_FAILED


# ====================================================================
# 重复复制计数埋点（产品指标：同一内容的重复复制次数）
# ====================================================================
class ReuseCounter:
    """同一内容被重复复制的计数（纯内存，进程生命周期内有效）。

    指标口径：``record(content)`` 返回该内容**本次是第几次**被复用
    （首次 = 1）。同一内容第 2 次起即为「重复复制」，累计
    ``duplicate_total()`` 给出「重复劳动总次数」，供后续接 UI / 日志。
    归一化：按去空白后的原文计数（同内容不同尾随空白视为同一条）。
    """

    def __init__(self):
        self._counts = {}
        self._duplicates = 0

    def record(self, content) -> int:
        """记一次复制，返回该内容累计被复制的次数（首次返回 1）。"""
        text = "" if content is None else str(content)
        n = self._counts.get(text, 0) + 1
        self._counts[text] = n
        if n > 1:
            self._duplicates += 1
        return n

    def count_of(self, content) -> int:
        """该内容累计被复制的次数（未复制过返回 0）。"""
        text = "" if content is None else str(content)
        return self._counts.get(text, 0)

    def duplicate_total(self) -> int:
        """全部「重复复制」（第 2 次及以后）的累计次数 —— 产品指标。"""
        return self._duplicates

    def unique_contents(self) -> int:
        """被复制过的不同内容条数。"""
        return len(self._counts)

    def repeated_contents(self) -> int:
        """被重复复制（≥2 次）的不同内容条数。"""
        return sum(1 for n in self._counts.values() if n > 1)

    def reset(self):
        """清空全部计数（测试 / 手动重置用）。"""
        self._counts.clear()
        self._duplicates = 0


# 进程级默认计数器（面板直接复用，无需自建）
_default_counter = ReuseCounter()


def default_counter() -> ReuseCounter:
    """返回进程级共享的重复复制计数器。"""
    return _default_counter
