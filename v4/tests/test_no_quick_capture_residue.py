# -*- coding: utf-8 -*-
"""卡 1「删除快捕条」残留闸门（2026-10-03）。

背景：快捕条 43 天只贡献 3/232 = 1.3% 的碎片，且 `Ctrl+Alt+K` 这条入口
路径基本没走过 → 已拍板整体删除，并**释放 `Ctrl+Alt+K`**（插件从此可注册
它）。释放要真的生效，光删 UI 不够：`reserved_hotkeys` / `core_norm` /
`reserve_hotkeys` 三处「核心保留热键」现场必须一并收口，否则验证工具说
「被占用」、实际插件能注册 —— 这种「假绿」比崩还危险。

本文件把「残留扫描」固化成断言，并额外钉住三件事：
  1. `v4/**` 与仓库根部的 .md 里不得再出现快捕条痕迹（少数刻意保留除外）；
  2. `reserved_hotkeys` 仍非空且仍是**核心键**（防日后被清空 → 保护线悄悄失效）；
  3. 那个唯一的刻意保留（test_onboarding 的负向钉子）确实存在 ——
     否则一旦有人删掉它，本闸门会「因为没人提」而静默放宽。

反向验证（改坏必红）：
  · 在 `v4/src/` 任意文件塞一行 `from src.quick_capture import QuickCaptureWindow`
    → `test_no_quick_capture_residue_in_v4` 必红；
  · 把 `knowledge_ball.py` 的 `reserved_hotkeys` 改成空元组
    → `test_core_reserved_hotkeys_not_empty` 必红。
"""
import ast
import os

# ---- 目录自举：禁写死绝对路径 ----
_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
REPO = os.path.dirname(V4)

TOKENS = ("quick_capture", "QuickCapture", "快速捕捉", "Ctrl+Alt+K")

# 刻意保留：test_onboarding 的负向钉子要求 Ctrl+Alt+K **不**出现，
# 它自身必然含该字面量；只放行这一个 token，不放行整个文件。
# test_no_native_messagebox：docstring 里引用了本文件名作「体例出处」，
# 同为护栏自指（token 级放行，非整文件跳过）。
ALLOWED = {
    "tests/test_onboarding.py": {"Ctrl+Alt+K"},
    "tests/test_no_native_messagebox.py": {"quick_capture"},
}

# 不扫 CHANGELOG.md：`### Removed` 那条记录本就要写明删了什么（题面许可）。
# 不扫**本文件自己**：闸门必须写出待查 token 与刻意保留项，属自指，非残留。
SKIP_REL = {"tests/test_no_quick_capture_residue.py"}
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


def test_no_quick_capture_residue_in_v4():
    offenders = []
    for path in _iter_v4_py():
        rel = os.path.relpath(path, V4).replace(os.sep, "/")
        if rel in SKIP_REL:
            continue
        for lineno, tok, line in _scan(path, rel):
            offenders.append("v4/%s:%d [%s] %s" % (rel, lineno, tok, line))
    assert not offenders, (
        "快捕条残留（2026-10-03 卡 1 已整体删除，含 Ctrl+Alt+K 的释放）：\n"
        + "\n".join(offenders))


def test_no_quick_capture_residue_in_root_docs():
    """仓库根部的 .md 是对外门面，必须彻底归零（不留任何解释性残留）。

    扫根目录**全部** .md（README / CONTRIBUTING …），只跳过 `CHANGELOG.md` ——
    它的 `### Removed` 那条本就要写明删了什么（题面许可），且历史条目属
    既成事实记录，改写会与「Removed」自相矛盾。
    """
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
    assert not offenders, ("仓库根部 .md 仍有快捕条痕迹：\n"
                           + "\n".join(offenders))


def test_ruff_config_has_no_dangling_quick_capture_ignore():
    """verify_quick_capture.py 已删 → ruff.toml 里不许留悬空 per-file 条目。"""
    path = os.path.join(REPO, "ruff.toml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "verify_quick_capture" not in text, (
        "ruff.toml 仍留着已删脚本的 per-file ignore（悬空条目）")
    # 同批验证：确实存在且被引用（防「整段被误删」这种反向错误）
    assert os.path.exists(os.path.join(
        V4, "tools", "verify_clipboard_guard.py")), "护栏自身路径自检失败"


# ====================================================================
# 释放专项：核心保留热键必须仍非空，且就是 screenshot_hotkey
# ====================================================================
def _reserved_hotkeys_call():
    """AST 取出 knowledge_ball.py 里 ActionRegistry(reserved_hotkeys=...) 的节点。"""
    path = os.path.join(V4, "knowledge_ball.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords or ():
                if kw.arg == "reserved_hotkeys":
                    found.append(kw.value)
    return found


def test_core_reserved_hotkeys_not_empty():
    """`reserved_hotkeys` 必须仍是非空元组。

    快捕条删除后 `Ctrl+Alt+S`（截图钉屏）成了**唯一**核心保留热键 ——
    若日后截图也被重构掉，这里会红，提醒回来补兜底断言，而不是让
    保护线悄悄变空（空 = 任何插件都能抢任意键）。
    """
    nodes = _reserved_hotkeys_call()
    assert nodes, "knowledge_ball.py 里找不到 reserved_hotkeys= 关键字实参"
    for value in nodes:
        assert isinstance(value, ast.Tuple) and value.elts, (
            "reserved_hotkeys 被清空了 —— 核心热键保护线失效")
        assert len(value.elts) == 1, (
            "快捕条已删，核心保留热键应恰好 1 个（screenshot_hotkey）")


def test_core_reserved_hotkeys_is_screenshot():
    """唯一核心键必须取自 screenshot_hotkey（而不是写死的字面量）。"""
    nodes = _reserved_hotkeys_call()
    assert nodes, "knowledge_ball.py 里找不到 reserved_hotkeys= 关键字实参"
    for value in nodes:
        src = ast.unparse(value.elts[0])
        assert "screenshot_hotkey" in src, (
            "核心保留热键应取自 screenshot_hotkey，实际为：%s" % src)


def test_onboarding_negative_guard_still_present():
    """刻意保留项必须真的存在 —— 否则本闸门会因为「没人提」而静默放宽。"""
    path = os.path.join(V4, "tests", "test_onboarding.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert '"Ctrl+Alt+K" not in keys' in text, (
        "test_onboarding 的负向钉子丢了：Ctrl+Alt+K 重新出现将无人拦截")
    assert "tests/test_onboarding.py" in ALLOWED
