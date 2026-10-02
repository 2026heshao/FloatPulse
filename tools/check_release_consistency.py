# -*- coding: utf-8 -*-
"""发布三源一致性自检：app_version.py ↔ CHANGELOG.md ↔ Release tag（纯标准库）。

为什么要有这个脚本
====================================================================
版本号散在三处，发版靠人肉同步迟早翻车：

  1. ``v4/src/app_version.py`` 的 ``APP_VERSION``（程序「关于」页、
     「检查更新」都用它，是唯一真相源）
  2. ``CHANGELOG.md`` 的 ``## [v<版本>] - 日期`` 版本标题（用户看的变更记录）
  3. Release tag（``v<版本>``，触发 ``.github/workflows/release.yml``）

任何一处漏改，发出去的就是「安装包写着 4.7.1、程序自报 4.7.0、更新检查
还提示升级到自己」这类错版事故，且 Release 一经发布就难撤回。所以在 CI
打包**之前**先跑本脚本对齐，对不上就终止发布（release.yml 第一步）。

用法
====================================================================
    python tools/check_release_consistency.py --expect 4.7.0

CI 上由 release.yml 取 tag 名去掉 ``v`` 前缀传入；``--expect`` 带 ``v``
前缀也能跑（内部剥掉），两种写法都收。

校验项
====================================================================
  ① ``app_version.py`` 的 ``APP_VERSION`` == ``--expect``
  ② ``CHANGELOG.md`` 存在 ``## [<expect>]``（或 ``[v<expect>]``）版本标题，
     按现有标题格式解析，容忍行尾日期等后缀（现有格式形如
     ``## [v4.7.0] - 2026-09-29``）

退出码：0 = 三源对齐 / 1 = 任一不符（打印明确差异）/ 2 = 用法错误。
对 ``app_version.py`` **只读**，绝不改运行时代码。
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ★ 本脚本全程打印中文，而 Windows 上 stdout 的默认编码未必能表示中文：
# GitHub 的 windows runner 是 cp1252，会在第一句 print 就抛
# UnicodeEncodeError: 'charmap' codec can't encode characters —— 表现为
# 「tag 推上去、Release 卡在第 1 步秒红，实际什么都没构建」（2026-10-02 实测）。
# 工作流侧已统一设 PYTHONUTF8=1，这里再兜一层：脚本被任何环境单独调用都不炸。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                     # 老版本解释器 / 被重定向成非文本流
        pass

VERSION_FILE = os.path.join(ROOT, "v4", "src", "app_version.py")
CHANGELOG_FILE = os.path.join(ROOT, "CHANGELOG.md")


def normalize_version(text: str) -> str:
    """版本号归一：剥掉一个可选的 v/V 前缀与首尾空白（4.7.0 ↔ v4.7.0）"""
    text = (text or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    return text


def read_app_version(version_file: str) -> str:
    """从 app_version.py 里取 APP_VERSION（正则读，不 import，只读不写）"""
    with open(version_file, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise SystemExit(f"[X] 读不到 APP_VERSION 常量：{version_file}")
    return m.group(1)


def changelog_find_heading(changelog_file: str, expect: str) -> str:
    """在 CHANGELOG.md 里找 ``## [<expect>]`` 版本标题，找到返回该行，没有返回空串。

    按现有标题格式解析：``## [v4.7.0] - 2026-09-29`` —— 方括号里是版本
    （容忍 v 前缀），行尾的日期等后缀不影响匹配；``## [Unreleased]``
    这类非版本标题自然对不上号，不会误伤。
    """
    want = normalize_version(expect)
    if not want:
        return ""
    with open(changelog_file, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^##\s*\[([^\]]+)\]", line)
            if not m:
                continue
            if normalize_version(m.group(1)) == want:
                return line.rstrip()
    return ""


def check(expect: str, version_file: str, changelog_file: str) -> list:
    """跑全部校验项，返回问题列表（空 = 对齐）。输出与退出码由 main 决定"""
    problems = []
    actual = read_app_version(version_file)
    if normalize_version(actual) != normalize_version(expect):
        problems.append(
            f"APP_VERSION 不一致：app_version.py = {actual}，--expect = {expect}")
    heading = changelog_find_heading(changelog_file, expect)
    if not heading:
        problems.append(
            f"CHANGELOG.md 缺版本标题：找不到「## [{expect}]」"
            f"（现有格式形如「## [v{expect}] - 日期」，标题行必须含方括号版本号）")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布三源一致性自检（app_version ↔ CHANGELOG ↔ tag）")
    ap.add_argument("--expect", required=True,
                    help="期望版本号（CI 传 tag 名去掉 v 前缀；带 v 也能跑）")
    ap.add_argument("--version-file", default=VERSION_FILE,
                    help="app_version.py 路径（默认仓库内 v4/src/app_version.py，测试用）")
    ap.add_argument("--changelog", default=CHANGELOG_FILE,
                    help="CHANGELOG.md 路径（默认仓库根，测试用）")
    args = ap.parse_args(argv)

    print("=" * 68)
    print(f"发布三源一致性自检   --expect {args.expect}")
    print("-" * 68)
    problems = check(args.expect, args.version_file, args.changelog)
    if problems:
        for p in problems:
            print(f"[X] {p}")
        print("[X] 三源未对齐，终止发布（先改 app_version.py / CHANGELOG.md / tag 使三者一致）")
        return 1

    actual = read_app_version(args.version_file)
    heading = changelog_find_heading(args.changelog, args.expect)
    print(f"[OK] app_version.py 的 APP_VERSION = {actual}")
    print(f"[OK] CHANGELOG.md 版本标题：{heading}")
    print("[OK] 三源对齐，可以发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
