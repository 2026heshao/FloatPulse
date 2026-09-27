#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FloatPulse 仓库自动巡检脚本（git_autopilot）

判定链：
    有改动 → Git 中间态检查 → 文件是否静默 → py_compile → pytest
    → 按功能模块分组 → 分组提交 or 人工介入报告

安全闸门（任一命中即退出）：
    1. merge / rebase / cherry-pick 中间态或 index.lock → 退出
    2. 有文件在静默窗口内被改动（会话还在动手）→ 等下一轮
    3. py_compile 失败 → 不提交
    4. pytest 未全绿 → 不提交
    5. 改动文件数异常（疑似 .gitignore 失效）→ 不提交
    6. 绝不自动 push

用法：
    python tools/git_autopilot.py                 # 巡检并提交
    python tools/git_autopilot.py --dry-run       # 只检测不提交
    python tools/git_autopilot.py --quiet-min 10  # 静默阈值 10 分钟
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_PATH = REPO / ".workbuddy" / "memory" / "git-autopilot.log"

# pytest 依赖 PyQt6，本机装在系统 Python；可用环境变量覆盖
PYTHON = r"C:/Users/LENOVO/AppData/Local/Programs/Python/Python312/python.exe"

QUIET_MIN_DEFAULT = 5
MAX_FILES_ABNORMAL = 100
PYTEST_TIMEOUT = 300

# 功能模块分组：(组名, 路径前缀元组, commit 前缀)
# 顺序敏感：专属模块在前，共享热点文件（core）兜底在后；
#           verify_*.py 类脚本要排 tools 之前才能归到对应模块。
# 新增业务模块时，仿照 pomodoro 加一行即可。
GROUPS = [
    ("plugins", ("v4/src/plugin_", "v4/plugins/", "v4/tests/test_plugin_",
                 "v4/tools/verify_plugin_"), "feat(plugins)"),
    ("fragments", ("v4/src/fragment_", "v4/tests/test_fragment_",
                   "v4/tools/verify_fragment_"), "feat(fragments)"),
    ("tasks", ("v4/src/task", "v4/tests/test_task",
               "v4/tools/verify_task"), "feat(tasks)"),
    ("pomodoro", ("v4/src/pomodoro", "v4/tests/test_pomodoro",
                  "v4/tools/verify_pomodoro"), "feat(pomodoro)"),
    ("notes", ("v4/src/note", "v4/tests/test_note"), "feat(notes)"),
    ("screenshot", ("v4/src/screenshot", "v4/tools/verify_screenshot"),
     "feat(screenshot)"),
    ("nav", ("v4/src/nav_", "v4/tools/verify_card_nav"), "feat(nav)"),
    ("assets", ("v4/src/asset_", "v4/src/temp_asset_"), "feat(assets)"),
    ("docs", ("docs/", "CHANGELOG.md", "README.md", "CONTRIBUTING.md"), "docs"),
    ("tools", ("v4/tools/", "tools/"), "chore(tools)"),
    ("core", ("v4/knowledge_ball.py", "v4/src/main_window.py",
              "v4/src/config.py", "v4/src/theme.py",
              "v4/src/settings_panel.py", "v4/src/card_window.py",
              "v4/src/json_store.py", "v4/src/app_paths.py"), "chore(core)"),
]

DEFAULT_GROUP = ("misc", "chore")


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("utf-8", "replace").decode("ascii", "replace"), flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd, cwd=None, timeout=60, stdin_text=None):
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd or REPO), timeout=timeout, input=stdin_text,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout after %ss" % timeout
    except Exception as e:
        return -2, "", "%s: %s" % (type(e).__name__, e)


def git(args, timeout=60, stdin_text=None):
    return run(["git", "-c", "core.quotepath=false"] + args,
               timeout=timeout, stdin_text=stdin_text)


def check_repo_state():
    """返回 None 表示正常，否则返回阻断原因"""
    gitdir = REPO / ".git"
    if not gitdir.exists():
        return "不是 git 仓库（缺少 .git）"
    for marker, desc in (
        (gitdir / "MERGE_HEAD", "存在 MERGE_HEAD：合并未结束"),
        (gitdir / "index.lock", "存在 index.lock：别的 git 进程在跑"),
        (gitdir / "rebase-merge", "rebase 进行中"),
        (gitdir / "rebase-apply", "rebase 进行中"),
        (gitdir / "CHERRY_PICK_HEAD", "cherry-pick 进行中"),
    ):
        if marker.exists():
            return desc
    code, out, _ = git(["branch", "--show-current"])
    if code != 0:
        return "无法获取当前分支"
    if not out.strip():
        return "处于 detached HEAD 状态"
    return None


def get_changes():
    """返回 [(状态码, 路径)]，-z 避免中文路径被转义，-uall 展开未跟踪目录。

    不加 -uall 时 git 会把「整个目录都未被跟踪」折叠成一条 `?? tools/`，
    导致后续 mtime 检测和 git add 都落在目录而非具体文件上。
    """
    code, out, err = git(["status", "--porcelain", "-z", "-uall"])
    if code != 0:
        log("!! git status 失败：%s" % err.strip())
        return []
    items = []
    for raw in out.split("\0"):
        if len(raw) >= 4 and raw.strip():
            items.append((raw[:2].strip(), raw[3:]))
    return items


def newest_change_age(changes, now):
    """(最年轻改动距今分钟数, 文件名)"""
    youngest, who = float("inf"), ""
    for _, path in changes:
        fp = REPO / path
        if not fp.exists():
            continue
        try:
            age = (now - fp.stat().st_mtime) / 60.0
        except OSError:
            continue
        if age < youngest:
            youngest, who = age, path
    return youngest, who


def classify(path):
    norm = path.replace("\\", "/")
    for name, prefixes, prefix in GROUPS:
        for p in prefixes:
            if norm.startswith(p) or norm == p:
                return name, prefix
    return DEFAULT_GROUP


def group_changes(changes):
    buckets = {}
    for status, path in changes:
        g, _ = classify(path)
        buckets.setdefault(g, []).append((status, path))
    return buckets


def py_compile_ok(paths):
    targets = [p for p in paths if p.endswith(".py") and (REPO / p).exists()]
    if not targets:
        return True, "无 .py 文件，跳过"
    code, out, err = run([PYTHON, "-m", "py_compile"] + targets, timeout=120)
    if code == 0:
        return True, "py_compile 通过（%d 个文件）" % len(targets)
    return False, (err or out).strip()[-500:]


def pytest_ok():
    code, out, err = run([PYTHON, "-m", "pytest", "-q"],
                         cwd=REPO / "v4", timeout=PYTEST_TIMEOUT)
    lines = (out or err).strip().splitlines()
    summary = lines[-1] if lines else "无输出"
    ok = code == 0 and ("passed" in summary or "skipped" in summary
                        or "no tests ran" in summary)
    return ok, summary


def commit_group(group, prefix, items, evidence, dry_run):
    files = [p for _, p in items]
    subject = "%s: 自动检出 %s 模块改动（%d 文件）" % (prefix, group, len(files))
    body = [subject, "", "由 tools/git_autopilot.py 自动提交。",
            "质量校验：%s" % evidence, "", "变更文件："]
    for status, path in items:
        body.append("  %s  %s" % (status or "M", path))
    message = "\n".join(body)

    if dry_run:
        return True, "[dry-run] 将提交 %s：%s" % (group, ", ".join(files))

    code, _, err = git(["add", "--"] + files)
    if code != 0:
        return False, "git add 失败：%s" % err.strip()[-300:]

    code, _, err = git(["commit", "-F", "-"], timeout=60, stdin_text=message)
    if code != 0:
        git(["reset"])  # 还原暂存区，别把现场搞乱
        return False, "git commit 失败：%s" % err.strip()[-300:]
    return True, "已提交 %s：%s" % (group, subject)


def main():
    ap = argparse.ArgumentParser(description="FloatPulse 仓库自动巡检")
    ap.add_argument("--dry-run", action="store_true", help="只检测不提交")
    ap.add_argument("--quiet-min", type=int, default=QUIET_MIN_DEFAULT)
    args = ap.parse_args()

    log("=" * 60)
    log("巡检开始 quiet=%dmin dry_run=%s" % (args.quiet_min, args.dry_run))

    blocked = check_repo_state()
    if blocked:
        log("跳过 —— %s" % blocked)
        return 1

    changes = get_changes()
    if not changes:
        log("无未提交改动，本轮结束")
        return 0
    log("检出 %d 项改动" % len(changes))

    if len(changes) > MAX_FILES_ABNORMAL:
        log("跳过 —— 改动 %d 个文件超过阈值 %d，疑似 .gitignore 失效，请人工确认"
            % (len(changes), MAX_FILES_ABNORMAL))
        return 1

    youngest, who = newest_change_age(changes, time.time())
    if youngest < args.quiet_min:
        log("跳过 —— 仍有文件在改动中：%s（%.1f 分钟前），等待下一轮"
            % (who, youngest))
        return 0
    log("所有改动已静默（最年轻 %s @ %.1f 分钟前）" % (who, youngest))

    ok, detail = py_compile_ok([p for _, p in changes])
    if not ok:
        log("跳过 —— 语法检查未通过：%s" % detail)
        return 1
    log("语法检查：%s" % detail)

    ok, summary = pytest_ok()
    if not ok:
        log("跳过 —— pytest 未通过：%s（修好后下一轮自动提交）" % summary)
        return 1
    log("pytest：%s" % summary)
    evidence = "py_compile OK / pytest %s" % summary

    buckets = group_changes(changes)
    log("划分为 %d 个功能组：%s" % (len(buckets), ", ".join(sorted(buckets))))

    prefix_map = dict((n, p) for n, _, p in GROUPS)
    prefix_map[DEFAULT_GROUP[0]] = DEFAULT_GROUP[1]

    failed = 0
    for group in sorted(buckets):
        ok, msg = commit_group(group, prefix_map.get(group, "chore"),
                               buckets[group], evidence, args.dry_run)
        log(("OK  " if ok else "FAIL ") + msg)
        if not ok:
            failed += 1

    _, remain, _ = git(["status", "--porcelain"])
    left = len([l for l in remain.splitlines() if l.strip()])
    log("巡检结束，剩余未提交 %d 项，失败 %d 组" % (left, failed))

    if not args.dry_run and left == 0 and failed == 0:
        ahead = git(["rev-list", "--count", "origin/main..main"])[1].strip()
        log("本地已领先远程 %s 条（未自动推送，Push 请人工确认）" % (ahead or "?"))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("!! 脚本异常：%s: %s" % (type(e).__name__, e))
        sys.exit(1)
