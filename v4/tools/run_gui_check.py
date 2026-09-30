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
单脚本档（原有用法，行为不变）：
    python tools/run_gui_check.py tools/verify_nav_live_letway.py [--shots]
    python tools/run_gui_check.py test_init.py

批量档（成熟化 4.2 前置：GUI 夜检 workflow 用，按名单跑精选子集）：
    python tools/run_gui_check.py --only verify_splash,verify_nav_groups
    python tools/run_gui_check.py --only verify_splash,verify_nav_groups --shots

  - ``--only`` 接逗号分隔名单：短名自动补 ``tools/`` 前缀与 ``.py`` 后缀，
    也可直接写相对路径；名单之外无法识别的参数（如 ``--shots``）原样
    透传给每个脚本（``--timeout`` 是运行器自己的，不透传）；
  - 每个脚本独立子进程 + 独立超时（``--timeout`` 可调，默认 240s），
    一个崩了不影响后面的继续跑，最后汇总退出码（全绿 0 / 任一失败 1），
    CI 夜检据此红绿；
  - 单脚本档行为与从前一致：打印 RC 与完整日志。

自动注入 QT_QPA_PLATFORM=offscreen 与 PYTHONIOENCODING=utf-8。
通过与否看脚本自身的功能步骤输出，不要只看退出码。
"""
import argparse
import os
import subprocess
import sys
import tempfile

PY = sys.executable
DEFAULT_TARGET = "tools/verify_nav_live_letway.py"
TIMEOUT_S = 240


def run_one(target: str, extra: list, timeout_s: int = TIMEOUT_S):
    """跑单个脚本，返回 (rc, 完整日志, 是否超时)。

    子进程写临时文件 → 父进程读文件，**不走 PIPE**（理由见模块头）。
    """
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"

    log_fd, log_path = tempfile.mkstemp(prefix="gui_check_", suffix=".log")
    os.close(log_fd)
    timed_out = False
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as out:
            p = subprocess.run([PY, "-u", target] + extra, stdout=out,
                               stderr=subprocess.STDOUT, env=env,
                               timeout=timeout_s, stdin=subprocess.DEVNULL)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc, timed_out = -1, True
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        log = f.read()
    os.unlink(log_path)
    return rc, log, timed_out


def resolve_target(name: str) -> str:
    """名单短名 → tools/ 下脚本路径（自动补 .py 后缀）；已带路径的原样放行"""
    if os.path.isfile(name):
        return name
    if not name.endswith(".py"):
        name += ".py"
    return os.path.join("tools", name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="离屏跑 GUI 验证脚本（单脚本档 / --only 批量档）")
    ap.add_argument("target", nargs="?", default=DEFAULT_TARGET,
                    help="单脚本档：要跑的脚本路径")
    ap.add_argument("--only", default="",
                    help="批量档：逗号分隔脚本名单（如 verify_splash,verify_nav_groups），"
                         "短名自动解析到 tools/ 下；给了 --only 就忽略 target")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S,
                    help=f"单个脚本超时秒数（默认 {TIMEOUT_S}s）")
    args, extra = ap.parse_known_args(argv)

    if not args.only:
        # ---- 单脚本档：与原实现行为一致（打印 RC 与完整日志，不设退出码）----
        rc, log, timed_out = run_one(args.target, extra, args.timeout)
        if timed_out:
            print(f"TIMEOUT (>{args.timeout}s)")
        else:
            print("RC=", rc)
        print(log)
        return 0

    # ---- 批量档：逐个跑、逐个超时，最后汇总（CI 夜检的退出码来源）----
    names = [n.strip() for n in args.only.split(",") if n.strip()]
    if not names:
        print("[X] --only 名单为空")
        return 2
    failed = []
    for i, name in enumerate(names, 1):
        target = resolve_target(name)
        print("=" * 68)
        print(f"[GUI-CHECK {i}/{len(names)}] {target}")
        rc, log, timed_out = run_one(target, extra, args.timeout)
        if timed_out:
            print(f"TIMEOUT (>{args.timeout}s)")
            failed.append(f"{target}   超时(>{args.timeout}s)")
        else:
            print("RC=", rc)
            if rc != 0:
                failed.append(f"{target}   rc={rc}")
        print(log)
    print("=" * 68)
    if failed:
        print(f"[X] {len(failed)}/{len(names)} 个脚本未通过：")
        for item in failed:
            print(f"    - {item}")
        return 1
    print(f"[OK] 全部 {len(names)} 个脚本通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
