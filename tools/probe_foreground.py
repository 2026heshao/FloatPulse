# -*- coding: utf-8 -*-
"""前台强夺验证探针（2026-10-10，问题3 修复验收）。

流程：找已在运行的主窗 → 记录当前前台 → 启动第二实例触发应用自带的
「唤醒信号 → main_window.show()」链路 → 轮询前台窗口归属，判断主窗
是否真的拿到了前台（修复前：show 后前台仍是别的进程）。

用法：python tools/probe_foreground.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _enum(pid):
    out = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            out.append((hwnd, rect))
        return True

    user32.EnumWindows(CB(cb), 0)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    args = ap.parse_args()
    pid = args.pid

    fg0 = user32.GetForegroundWindow()
    o0 = wintypes.DWORD()
    user32.GetWindowThreadProcessId(fg0, ctypes.byref(o0))
    print(f"唤醒前前台：hwnd={fg0} pid={o0.value}"
          f"{'（就是本应用）' if o0.value == pid else '（别的应用）'}")

    wins = [w for w in _enum(pid)
            if (w[1].right - w[1].left) >= 900 and (w[1].bottom - w[1].top) >= 600]
    state = "已可见" if wins else "关闭态（将靠唤醒信号唤起）"
    print(f"主窗当前：{state}")

    # 触发唤醒：第二实例 → 命名事件 → 本实例 show 主窗
    python = (r"C:\Users\LENOVO\AppData\Local\Programs\Python"
              r"\Python312\python.exe")
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen([python, r"v4\knowledge_ball.py"], cwd=cwd,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    hit_at, main_hwnd, seen = None, None, []
    while time.time() - t0 < 15:
        fg = user32.GetForegroundWindow()
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(owner))
        seen.append(owner.value)
        if main_hwnd is None:
            found = [w for w in _enum(pid)
                     if (w[1].right - w[1].left) >= 900
                     and (w[1].bottom - w[1].top) >= 600]
            if found:
                main_hwnd = found[0][0]
                print(f"主窗出现：hwnd={main_hwnd} "
                      f"（唤醒后 {time.time()-t0:.1f}s）")
        if main_hwnd is not None and fg == main_hwnd:
            hit_at = time.time() - t0
            break
        time.sleep(0.1)
    if hit_at is not None:
        print(f"★ 主窗成为前台窗口：唤醒后 {hit_at:.1f}s（前台强夺生效）")
        return 0
    others = sorted({p for p in seen if p and p != pid})
    print(f"✗ 15s 内主窗未取得前台"
          f"（主窗 hwnd={main_hwnd}）；期间前台属 pid={others or ['(无)']}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
