# -*- coding: utf-8 -*-
"""离屏 GUI 脚本运行器：用 subprocess + timeout 包一层跑验证脚本。

背景（本机实测）
----------------
沙箱里用 bash 直接跑 PyQt6 GUI 脚本会被 SIGTERM 打断，且"进程退出"环节
概率性不返回（同一脚本 4 次里 3 次挂）。用 python 的 subprocess 起子进程、
子进程加 ``-u``（无缓冲）、外层加 timeout，才能稳定拿到真实输出。

用法
----
    python tools/run_gui_check.py tools/verify_nav_live_letway.py [--shots]
    python tools/run_gui_check.py test_init.py

自动注入 QT_QPA_PLATFORM=offscreen 与 PYTHONIOENCODING=utf-8。
通过与否看脚本自身的功能步骤输出，不要只看退出码。
"""
import os
import subprocess
import sys

PY = sys.executable
TARGET = sys.argv[1] if len(sys.argv) > 1 else "tools/verify_nav_live_letway.py"
EXTRA = sys.argv[2:]

env = dict(os.environ)
env["QT_QPA_PLATFORM"] = "offscreen"
env["PYTHONIOENCODING"] = "utf-8"

try:
    p = subprocess.run(
        [PY, "-u", TARGET] + EXTRA,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=240,
    )
    print("RC=", p.returncode)
    print("--- STDOUT ---")
    print(p.stdout)
    print("--- STDERR (tail) ---")
    print(p.stderr[-4000:])
except subprocess.TimeoutExpired as e:
    print("TIMEOUT")
    out = e.stdout
    print(out[-4000:] if isinstance(out, str) else out)
