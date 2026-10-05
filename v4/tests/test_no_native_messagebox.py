# -*- coding: utf-8 -*-
"""原生 QMessageBox 禁用闸门（2026-10 遗留旧 UI 统一专项 · T05 护栏）。

背景：GlassMessageBox（src/glass_message_box.py）已接管 v4/src 全部
消息框语义（information / question / warning / critical），成功回执类
降级 ScreenToast。原生 QMessageBox 全仓只允许**一个**幸存点：
``src/logger.py`` 的崩溃兜底（sys.excepthook 里必须用最笨组件 ——
GlassDialog 依赖 glass/controls/theme/icon_render 全链路，若异常恰好
来自这些模块，玻璃兜底弹窗本身再炸，用户连日志路径都看不到）。

实现口径：AST 扫描（体例照 tests/test_no_quick_capture_residue.py）——
认 import（Import / ImportFrom）与属性访问链（QMessageBox.xxx），
字符串/注释提及不算命中，所以 glass_message_box.py / glass_dialog.py
的 docstring 与注释天然放行。

防假护栏双钉（「假绿」比崩还危险）：
  1. 白名单里的 logger.py 必须**确实仍含** QMessageBox（刻意保留项必须
     存在 —— 否则一旦有人删掉它，本闸门会「因为没人提」而静默放宽，
     下一次想加 QMessageBox 就无人拦截）；
  2. src/glass_message_box.py 必须存在且四工厂（question / information /
     warning / critical）齐全（防止把闸门放行建立在空实现上）。

反向验证（改坏必红）：
  · 在 v4/src/ 任意文件加 ``from PyQt6.QtWidgets import QMessageBox``
    或 ``QMessageBox.question(...)`` → test_no_native_messagebox_in_src
    必红；
  · 删掉 logger.py 的 QMessageBox 用法 → test_whitelist_logger_still_has_
    messagebox 必红（白名单失效须显式收窄）；
  · 删掉 glass_message_box.py 任一工厂 → test_glass_message_box_has_all_
    four_factories 必红。
"""
import ast
import os

# ---- 目录自举：禁写死绝对路径 ----
_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
SRC = os.path.join(V4, "src")

# 唯一白名单：崩溃兜底保留原生（docs/system_design.md §3 专项评估结论 A）
ALLOWED = {
    "src/logger.py",
}

SKIP_DIRS = {"__pycache__", ".ruff_cache", ".pytest_cache"}

_FACTORIES = ("question", "information", "warning", "critical")


def _iter_src_py():
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _rel(path):
    return os.path.relpath(path, V4).replace(os.sep, "/")


def _qm_hits(tree):
    """AST 找 QMessageBox 的 import 与属性访问链，返回行号列表。"""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "QMessageBox":
                    hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "QMessageBox" or any(
                    alias.name == "QMessageBox" for alias in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id == "QMessageBox":
                hits.append(node.lineno)
    return hits


def test_no_native_messagebox_in_src():
    """v4/src 全量 AST 扫描：QMessageBox 只许在白名单（logger.py）出现。"""
    offenders = []
    for path in _iter_src_py():
        rel = _rel(path)
        if rel in ALLOWED:
            continue
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for lineno in _qm_hits(tree):
            offenders.append("%s:%d" % (rel, lineno))
    assert not offenders, (
        "v4/src 出现原生 QMessageBox（一律改走 src.glass_message_box."
        "GlassMessageBox 四工厂；成功回执类降级 ScreenToast.show_msg）。"
        "命中：\n" + "\n".join(offenders))


def test_whitelist_logger_still_has_messagebox():
    """防假护栏钉 1：logger.py 必须仍含 QMessageBox 用法。

    崩溃兜底是全仓唯一刻意保留项 —— 它若被删，白名单即告失效，必须
    显式从 ALLOWED 收窄，而不是让闸门静默放空。
    """
    assert ALLOWED == {"src/logger.py"}, (
        "白名单口径被改动：唯一放行点只能是 src/logger.py（崩溃兜底）")
    path = os.path.join(SRC, "logger.py")
    assert os.path.isfile(path), "src/logger.py 不存在？"
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    assert _qm_hits(tree), (
        "logger.py 已不含 QMessageBox —— 崩溃兜底被改写或删除：白名单失效，"
        "请把 ALLOWED 收窄为空并确认新兜底方案")


def test_glass_message_box_has_all_four_factories():
    """防假护栏钉 2：glass_message_box.py 存在且四工厂齐全。"""
    path = os.path.join(SRC, "glass_message_box.py")
    assert os.path.isfile(path), "src/glass_message_box.py 不存在（闸门失去放行依据）"
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "GlassMessageBox":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
    missing = [n for n in _FACTORIES if n not in methods]
    assert not missing, (
        "GlassMessageBox 缺少静态工厂：%s（闸门放行不得建立在空实现上）"
        % missing)
