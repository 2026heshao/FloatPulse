# -*- coding: utf-8 -*-
"""主窗打开卡顿诊断探针（2026-10-10，问题3 定位用）。

只读探测：启动应用后，按窗口尺寸锁定「主窗」（1280x740 量级），
持续测量它的 WM_NULL 响应时延与中心点的顶层窗口归属，
回答「是事件循环被占满，还是有别的窗口盖在上面吃点击」。

用法：python tools/probe_mainwin_open.py
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def windows_of(pid):
    """该进程的全部可见顶层窗口 -> [(hwnd, rect, class)]"""
    out = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, 128)
        out.append((hwnd, rect, buf.value))
        return True

    user32.EnumWindows(CB(cb), 0)
    return out


def main():
    python = (r"C:\Users\LENOVO\AppData\Local\Programs\Python"
              r"\Python312\python.exe")
    cwd = r"D:\桌面\AI Port\FloatPulse"
    proc = subprocess.Popen([python, r"v4\knowledge_ball.py"], cwd=cwd)
    print(f"started pid={proc.pid} at {time.strftime('%H:%M:%S')}")
    main_hwnd, t0 = None, time.time()
    worst, worst_at = 0, ""
    while time.time() - t0 < 18:
        wins = windows_of(proc.pid)
        if main_hwnd is None:
            for hwnd, rect, cls in wins:
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w >= 900 and h >= 600:          # 主窗量级
                    main_hwnd = hwnd
                    print(f"[{time.time()-t0:5.1f}s] 主窗出现 "
                          f"hwnd={hwnd} {w}x{h} class={cls}")
                    break
        if main_hwnd is not None:
            st = time.perf_counter()
            res = ctypes.c_size_t()
            ok = user32.SendMessageTimeoutW(
                main_hwnd, 0, 0, 0, 2, 6000, ctypes.byref(res))
            ms = (time.perf_counter() - st) * 1000
            if ms > worst:
                worst, worst_at = ms, f"{time.time()-t0:.1f}s"
            if ms > 120 or not ok:
                print(f"[{time.time()-t0:5.1f}s] 响应迟滞 {ms:.0f}ms ok={ok}")
        time.sleep(0.05)
    print(f"探测结束：最差响应 {worst:.0f}ms（{worst_at}）")
    if main_hwnd:
        rect = wintypes.RECT()
        user32.GetWindowRect(main_hwnd, ctypes.byref(rect))
        pt = wintypes.POINT((rect.left + rect.right) // 2,
                            (rect.top + rect.bottom) // 2)
        top = user32.WindowFromPoint(pt)
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(top, ctypes.byref(owner))
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(top, buf, 128)
        tag = "本进程" if owner.value == proc.pid else f"外部 pid={owner.value}"
        print(f"主窗中心点顶层窗口：hwnd={top} class={buf.value} → {tag}")
    proc.terminate()
    print("probe process terminated")


if __name__ == "__main__":
    sys.exit(main())
