# -*- coding: utf-8 -*-
"""
====================================================================
全屏应用检测  -  FullscreenWatcher
====================================================================
悬浮球 / 小卡片都是常置顶（WindowStaysOnTopHint + Tool）的工具窗口，
全屏应用（视频、游戏、PPT 演示）在前台时会一直被压在画面上。成熟桌面
工具（Snipaste / Quicker 等）的做法是：检测到前台全屏就自动让位。

设计要点：
  1. 纯 ctypes 调 user32，不引第三方依赖（与 clipboard_monitor 同思路）
  2. 判据：前台**顶层**窗口的 GetWindowRect 覆盖它所在显示器的 rcMonitor
     （容忍 2px 误差）。最大化窗口只覆盖工作区 rcWork（比 rcMonitor 少了
     任务栏高度），因此不会被误判为全屏
  3. 排除：本程序自己的窗口、被最小化的窗口、系统外壳窗口（桌面/任务栏）
  4. 轮询间隔 1.2s —— 全屏切换是低频事件，无需更高精度，空闲开销可忽略
  5. 检测异常一律当作"非全屏"，绝不因检测本身让悬浮球消失或崩溃
====================================================================
"""

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# ---------------- Win32 绑定 ----------------
_user32 = ctypes.windll.user32

_MONITOR_DEFAULTTONEAREST = 2
_TOLERANCE = 2  # 允许的尺寸误差（像素）

# 系统外壳窗口类名：桌面 / 任务栏 / 副屏任务栏
_SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")

if _user32 is not None:
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = [wintypes.HWND,
                                      ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if _user32 is not None:
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR,
                                        ctypes.POINTER(_MONITORINFO)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL


class FullscreenWatcher(QObject):
    """周期性检测"前台窗口是否全屏"，状态变化时发 fullscreen_changed(bool)"""

    fullscreen_changed = pyqtSignal(bool)

    INTERVAL_MS = 1200

    def __init__(self, exclude_hwnds=None, parent=None):
        """
        exclude_hwnds: 可调用对象，返回本程序自己的窗口句柄集合（int），
                       这些窗口即使全屏也不判定为"外部全屏应用"
        """
        super().__init__(parent)
        self._exclude_hwnds = exclude_hwnds if exclude_hwnds is not None else (lambda: ())
        self._is_fullscreen = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ---------------- 生命周期 ----------------
    def start(self):
        """开始轮询（立即先测一次，避免启动瞬间漏判）"""
        self._poll()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._is_fullscreen = False

    def is_running(self) -> bool:
        return self._timer.isActive()

    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    # ---------------- 轮询 ----------------
    def _poll(self):
        current = self.detect()
        if current != self._is_fullscreen:
            self._is_fullscreen = current
            self.fullscreen_changed.emit(current)

    # ---------------- 检测 ----------------
    def detect(self) -> bool:
        """前台顶层窗口是否占满其所在显示器（异常一律返回 False）"""
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return False
            # 本程序自己的窗口 → 不算外部全屏
            try:
                if int(hwnd) in {int(h) for h in (self._exclude_hwnds() or ())}:
                    return False
            except Exception:
                pass
            # 最小化窗口 / 系统外壳窗口
            if _user32.IsIconic(hwnd):
                return False
            buf = ctypes.create_unicode_buffer(256)
            if _user32.GetClassNameW(hwnd, buf, 256):
                if buf.value in _SHELL_CLASSES:
                    return False
            # 窗口矩形 vs 所在显示器矩形
            rect = wintypes.RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
            if not monitor:
                return False
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return False
            mon = info.rcMonitor
            mon_w = mon.right - mon.left
            mon_h = mon.bottom - mon.top
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top
            return (win_w >= mon_w - _TOLERANCE
                    and win_h >= mon_h - _TOLERANCE)
        except Exception:
            return False
