# -*- coding: utf-8 -*-
"""离屏 GUI 脚本运行器：用 subprocess + timeout 包一层跑验证脚本。

背景（本机实测）
----------------
沙箱里用 bash 直接跑 PyQt6 GUI 脚本会被 SIGTERM 打断，且"进程退出"环节
概率性不返回（同一脚本 4 次里 3 次挂）。用 python 的 subprocess 起子进程、
子进程加 ``-u``（无缓冲）、外层加 timeout，才能稳定拿到真实输出。

★ 2026-09-29 关键修正：**不要用 capture_output / PIPE**
--------------------------------------------------------
被判为"挂死"的脚本，用文件重定向重跑往往 3 秒就正常退出
（``test_init.py`` 实测：PIPE 下 240s 超时且看不到 ``[DONE]``；
 ``> out.txt`` 下 RC=0、elapsed=3s、``[DONE]`` 正常打印）。

原因是 ``subprocess.run(capture_output=True)`` 走 ``communicate()``：
它要等到 stdout/stderr 两个管子**读到 EOF** 才返回，而 EOF 取决于
"所有持有写端的进程都退出" —— GUI 进程会拉起子进程（QProcess 等），
只要有一个把写端继承下去，父进程即使已经退出，读取仍然阻塞到超时。
文件重定向不存在这个问题：父进程退出即写出完毕。

所以这里改成"子进程写临时文件 → 父进程读文件"，超时判定也更可信。

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
import tempfile

PY = sys.executable
TARGET = sys.argv[1] if len(sys.argv) > 1 else "tools/verify_nav_live_letway.py"
EXTRA = sys.argv[2:]
TIMEOUT_S = 240

env = dict(os.environ)
env["QT_QPA_PLATFORM"] = "offscreen"
env["PYTHONIOENCODING"] = "utf-8"

log_fd, log_path = tempfile.mkstemp(prefix="gui_check_", suffix=".log")
os.close(log_fd)

try:
    with open(log_path, "w", encoding="utf-8", errors="replace") as out:
        p = subprocess.run([PY, "-u", TARGET] + EXTRA, stdout=out,
                           stderr=subprocess.STDOUT, env=env,
                           timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    print("RC=", p.returncode)
except subprocess.TimeoutExpired:
    print(f"TIMEOUT (>{TIMEOUT_S}s)")

with open(log_path, "r", encoding="utf-8", errors="replace") as f:
    print(f.read())
os.unlink(log_path)
