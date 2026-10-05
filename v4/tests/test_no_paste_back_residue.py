# -*- coding: utf-8 -*-
"""「一键粘回」整体删除残留闸门（2026-10-05）。

背景：碎片「粘回」（记最近外部前台窗口 → 还原焦点 → 发 Ctrl+V）真机
不可靠——本程序多顶层窗口（悬浮球/小卡片/便签/对话框）会被前台轮询
记成「刚才用的窗口」，粘回落回自家、且成功路径零反馈，用户实测
「和单纯复制一样」。经用户拍板**整体删除**（底部按钮 / 右键菜单 /
前台轮询 / 执行器模块 / fragment_paste_enabled 配置键），保留置顶与
普通复制。

本文件把「残留扫描」固化成断言：
  1. `v4/**/*.py` 不得再出现粘回痕迹（模块名 / 配置键 / 类名 / 功能名）；
  2. 仓库根部 .md（对外门面）同样归零。

刻意的 token 级放行（非整文件跳过）：两个由 ``test_reuse_*.py`` 改名
而来的置顶测试，docstring 里的「历史注」要如实写明删的是什么功能——
与快捕条闸门放行 test_onboarding 负向钉子同体例。

反向验证（改坏必红）：
  · 在 `v4/src/` 任意文件塞一行 `from src.paste_helper import PasteHelper`
    → 扫描用例必红。
"""
import os

# ---- 目录自举：禁写死绝对路径 ----
_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
REPO = os.path.dirname(V4)

TOKENS = ("paste_helper", "fragment_paste_enabled", "PasteHelper",
          "ReuseCounter", "reuse_counter", "粘回")

# 历史注 token 级放行：改名后的置顶测试 docstring 如实记录删了什么。
ALLOWED = {
    "tests/test_fragment_pinned.py": {"粘回"},
    "tests/test_fragment_pinned_panel.py": {"粘回"},
}

# CHANGELOG 的 `### Removed` 本就要写明删了什么（题面许可）；本文件
# 自身必须写出待查 token，属自指，非残留。
SKIP_REL = {"tests/test_no_paste_back_residue.py"}
SKIP_FILES = {"CHANGELOG.md"}
SKIP_DIRS = {"__pycache__", ".ruff_cache", ".pytest_cache"}


def _iter_v4_py():
    for dirpath, dirnames, filenames in os.walk(V4):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _scan(path, rel):
    """返回该文件的残留命中列表 [(lineno, token, line)]。"""
    allowed = ALLOWED.get(rel, set())
    hits = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            for tok in TOKENS:
                if tok in line and tok not in allowed:
                    hits.append((lineno, tok, line.strip()[:90]))
    return hits


def test_no_paste_back_residue_in_v4():
    offenders = []
    for path in _iter_v4_py():
        rel = os.path.relpath(path, V4).replace(os.sep, "/")
        if rel in SKIP_REL:
            continue
        for lineno, tok, line in _scan(path, rel):
            offenders.append("v4/%s:%d [%s] %s" % (rel, lineno, tok, line))
    assert not offenders, (
        "「一键粘回」残留（2026-10-05 已整体删除，置顶/复制保留）：\n"
        + "\n".join(offenders))


def test_no_paste_back_residue_in_root_docs():
    """仓库根部 .md（对外门面）必须归零（CHANGELOG 的 Removed 除外）。"""
    offenders = []
    for name in sorted(os.listdir(REPO)):
        if not name.endswith(".md") or name in SKIP_FILES:
            continue
        path = os.path.join(REPO, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                for tok in TOKENS:
                    if tok in line:
                        offenders.append("%s:%d [%s] %s"
                                         % (name, lineno, tok, line.strip()[:90]))
    assert not offenders, ("仓库根部 .md 仍有粘回痕迹：\n"
                           + "\n".join(offenders))


def test_ruff_config_has_no_dangling_paste_ignore():
    """verify_reuse_paste.py 已删 → ruff.toml 里不许留悬空 per-file 条目。"""
    path = os.path.join(REPO, "ruff.toml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "verify_reuse_paste" not in text, (
        "ruff.toml 仍留着已删脚本的 per-file ignore（悬空条目）")
    # 同批验证：置顶测试与闸门自身确实存在（防「整段被误删」反向错误）
    for rel in ("tests/test_fragment_pinned.py",
                "tests/test_fragment_pinned_panel.py"):
        assert os.path.exists(os.path.join(V4, rel)), "护栏自身路径自检失败"
