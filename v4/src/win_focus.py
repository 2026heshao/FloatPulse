# -*- coding: utf-8 -*-
"""
====================================================================
前台焦点强夺  -  win_focus
====================================================================
Windows 前台锁（Foreground Lock）下，**非前台进程**调用
``SetForegroundWindow`` / Qt 的 ``activateWindow()`` 会被系统静默拒绝
—— 窗口画出来了，却始终不是前台窗口：用户看得见、点不动（键盘输入
也进不去），直到手动点一次标题栏才恢复。

2026-10-10 实机取证（tools/probe_mainwin_open.py 三次交叉验证）：
主窗 show 后自身响应始终 ≤73ms（事件循环毫不忙），但主窗中心点的顶层
窗口一直是**别的进程**（资源管理器 / 浏览器）——「打开主窗卡几秒」
的真身就是前台没抢到。

本模块只做一件事：把某个窗口**真的**推到前台。三步递进，全部失败
才算失败（不做静默假装成功）：
  1. ``SetForegroundWindow`` 直接试（进程本身有前台权限时一步到位）；
  2. 失败 → ``AttachThreadInput`` 把本线程输入队列附到当前前台窗口的
     线程上再试（绕过前台锁的经典手法），无论成败都**必须分离**；
  3. 仍失败 → ALT 键轻点一次解除前台锁再试（模拟用户按键，系统视为
     "用户输入"从而放行前台切换）。

只在前两步都失败时才走到第 3 步 —— 模拟按键会短暂扰动键盘状态，
非必要不用。

纯 ctypes、零 PyQt（可被任意模块与单测直接引用）；非 Windows 平台
一律安全降级返回 False，绝不抛异常。
====================================================================
"""

import ctypes

try:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _IS_WINDOWS = True
except AttributeError:                      # 非 Windows：全部安全降级
    _user32 = None
    _kernel32 = None
    _IS_WINDOWS = False

SW_SHOW = 5
VK_MENU = 0x12                              # ALT
_KEYEVENTF_KEYUP = 0x0002


if _IS_WINDOWS:
    # 显式声明签名：默认 c_int 在 64 位下会把 HWND 截断成 32 位出乱子
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.SetForegroundWindow.restype = ctypes.c_bool
    _user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    _user32.BringWindowToTop.restype = ctypes.c_bool
    _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.IsWindow.argtypes = [ctypes.c_void_p]
    _user32.IsWindow.restype = ctypes.c_bool
    _user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    _user32.AttachThreadInput.argtypes = [
        ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
    _user32.AttachThreadInput.restype = ctypes.c_bool


def force_foreground(hwnd) -> bool:
    """把 ``hwnd`` 推到前台；成功返回 True。

    ``hwnd`` 接受 int 或任何可 ``int()`` 的对象（PyQt 的 ``winId()``
    返回值直接可用）。非 Windows / 句柄为 0 / 句柄非法 → False。
    """
    if not _IS_WINDOWS:
        return False
    try:
        h = int(hwnd)
    except (TypeError, ValueError):
        return False
    if not h:
        return False
    try:
        if not _user32.IsWindow(ctypes.c_void_p(h)):
            return False
    except Exception:                       # noqa: BLE001 - 探测失败不抛
        return False

    try:
        _user32.ShowWindow(ctypes.c_void_p(h), SW_SHOW)
    except Exception:                       # noqa: BLE001
        pass

    # ---- 1. 直接试 ----
    try:
        if _user32.SetForegroundWindow(ctypes.c_void_p(h)):
            return True
    except Exception:                       # noqa: BLE001
        pass

    # ---- 2. 附加到当前前台线程再试（前台锁绕行）----
    try:
        fg = _user32.GetForegroundWindow()
        if fg and int(fg) != h:
            fg_pid = ctypes.c_ulong()
            fg_thread = _user32.GetWindowThreadProcessId(
                ctypes.c_void_p(int(fg)), ctypes.byref(fg_pid))
            cur_thread = _kernel32.GetCurrentThreadId()
            if fg_thread and fg_thread != cur_thread:
                attached = _user32.AttachThreadInput(
                    ctypes.c_ulong(cur_thread), ctypes.c_ulong(fg_thread),
                    True)
                try:
                    _user32.BringWindowToTop(ctypes.c_void_p(h))
                    if _user32.SetForegroundWindow(ctypes.c_void_p(h)):
                        return True
                finally:
                    if attached:            # 附加成功就必须分离，防输入队列
                        _user32.AttachThreadInput(  # 长期串接导致键盘异常
                            ctypes.c_ulong(cur_thread),
                            ctypes.c_ulong(fg_thread), False)
    except Exception:                       # noqa: BLE001
        pass

    # ---- 3. ALT 轻点解除前台锁后最后一试 ----
    try:
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _user32.keybd_event(VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
        return bool(_user32.SetForegroundWindow(ctypes.c_void_p(h)))
    except Exception:                       # noqa: BLE001
        return False
