# -*- coding: utf-8 -*-
"""打包产物启动冒烟 —— 发出去之前，exe 至少要真的跑起来过一次。

用法：
    python tools/smoke_packaged.py [exe路径] [--timeout 秒] [--windowed]

    exe 缺省 = dist2/FloatPulse/FloatPulse.exe（本地 PyInstaller 惯例产物）；
    CI 里传 dist/FloatPulse/FloatPulse.exe（release.yml 用 --distpath dist）。

判定（与 knowledge_ball 的启动打点对齐）：
    成功   = 进程存活，且 exe 同目录 float_data/app.log 新增内容里出现
             「[启动] 全部完成」（启动序列 9 阶段全部走完）
    失败   = 进程提前退出 / 超时未见完成打点

两种已知误判源，都会给出专属诊断而不是笼统报错：
  1. 单实例锁（Global\\..._pkg 互斥量）：本机已有**打包版**在运行时，
     新进程静默 sys.exit(0)（开发版用 _dev 后缀互不影响）——表现为
     「秒退且退出码 0」，提示关掉正在运行的打包版再冒烟；
  2. 冒烟默认 QT_QPA_PLATFORM=offscreen（CI / 无人值守环境不能弹真窗），
     若构建剔掉了 offscreen 平台插件会启动即退——可用 --windowed 在
     本机带桌面会话时复核。

冒烟会在 exe 同目录的 float_data/ 里留下运行痕迹（app.log 等）——
build_release.py 白名单收集发布包（只认 exe 与 _internal/），这些
痕迹不会被打进 zip；本地 dist2/ 本就是可弃构建产物。

退出码：0 通过，1 失败（CI 里作为发布闸门）。
"""

import argparse
import os
import subprocess
import sys
import time

# 启动完成打点（knowledge_ball.py 启动序列末尾写进 app.log）
BOOT_DONE_MARK = "[启动] 全部完成"
# app.log 相对 exe 的位置（打包运行时数据目录 = exe 同目录 float_data/）
LOG_RELPATH = os.path.join("float_data", "app.log")
DEFAULT_TIMEOUT_S = 60        # CI 冷启动 + 依赖解压，给足余量
POLL_INTERVAL_S = 1.0


def _tail(text, lines=25):
    return "\n".join(text.splitlines()[-lines:])


def smoke(exe_path, timeout_s, windowed):
    if not os.path.isfile(exe_path):
        print(f"FAIL: 找不到 exe：{exe_path}")
        return 1
    log_path = os.path.join(os.path.dirname(os.path.abspath(exe_path)),
                            LOG_RELPATH)
    # 只读「本次运行新增」的日志：记录旧文件长度（不删旧文件——
    # dist2 里可能有上一次构建/运行的痕迹，删了反而掩盖问题）
    log_start = os.path.getsize(log_path) if os.path.isfile(log_path) else 0

    env = dict(os.environ)
    if not windowed:
        env["QT_QPA_PLATFORM"] = "offscreen"

    print(f"[冒烟] 启动 {exe_path}（平台={'windowed' if windowed else 'offscreen'}，"
          f"超时 {timeout_s}s）")
    proc = subprocess.Popen([exe_path], env=env, cwd=os.path.dirname(exe_path))
    t0 = time.monotonic()

    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                # 提前退出：区分单实例静默退（码 0 且几乎没活）与崩溃（非 0）
                alive = time.monotonic() - t0
                print(f"FAIL: 进程 {alive:.1f}s 后退出，退出码 {rc}")
                if rc == 0 and alive < 5:
                    print("诊断：秒退且退出码 0 —— 疑似单实例锁"
                          "（本机已有打包版在运行，Global 互斥量静默让位）。\n"
                          "      关掉正在运行的打包版 FloatPulse 后重试。")
                elif not windowed:
                    print("诊断：offscreen 下启动即退 —— 疑似构建剔掉了 "
                          "offscreen 平台插件，可加 --windowed 在本机复核。")
                _print_log_tail(log_path, log_start)
                return 1
            if _log_has(log_path, log_start, BOOT_DONE_MARK):
                took = time.monotonic() - t0
                print(f"PASS: 启动完成打点已出现（{took:.1f}s）")
                return 0
            if time.monotonic() - t0 > timeout_s:
                print(f"FAIL: {timeout_s}s 内未见「{BOOT_DONE_MARK}」打点"
                      "（进程仍在运行，疑似启动序列卡死）")
                _print_log_tail(log_path, log_start)
                return 1
            time.sleep(POLL_INTERVAL_S)
    finally:
        _terminate(proc)


def _log_has(log_path, start_offset, mark):
    if not os.path.isfile(log_path):
        return False
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start_offset)
            return mark in f.read()
    except OSError:
        return False


def _print_log_tail(log_path, start_offset):
    if not os.path.isfile(log_path):
        print("---- app.log 不存在（启动连日志系统都没走到）----")
        return
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start_offset)
            new_text = f.read()
        print("---- app.log 本次新增尾部 ----")
        print(_tail(new_text) if new_text.strip()
              else "（无新增内容）")
    except OSError as exc:
        print(f"---- app.log 读取失败：{exc!r} ----")


def _terminate(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    print("[冒烟] 已结束被测进程")


def main():
    ap = argparse.ArgumentParser(description="打包产物启动冒烟")
    ap.add_argument("exe", nargs="?", default=os.path.join(
        "dist2", "FloatPulse", "FloatPulse.exe"),
        help="被测 exe（默认 dist2/FloatPulse/FloatPulse.exe）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help=f"等待启动完成的秒数（默认 {DEFAULT_TIMEOUT_S}）")
    ap.add_argument("--windowed", action="store_true",
                    help="不用 offscreen，真窗口运行（需桌面会话，本机复核用）")
    args = ap.parse_args()
    return smoke(args.exe, args.timeout, args.windowed)


if __name__ == "__main__":
    sys.exit(main())
