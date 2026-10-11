# -*- coding: utf-8 -*-
"""插件 stdlib 依赖 ⊆ (spec hiddenimports ∪ 主程序可达集) 护栏。

背景（2026-10-08 zip 版实机踩坑）：插件在冻结（PyInstaller）环境里随宿主
解释器运行，包里只有主程序静态分析可达的模块。ai-assistant 顶层
`import uuid`，而主程序自身从不 import uuid → 打包产物里没有 →
插件中心「加载失败: 导入失败 (ModuleNotFoundError)」。

本护栏在**不打包**的前提下比对这个缺口：
  1. 扫 plugins/*/（含子目录）全部顶层 import / from-import；
  2. 取 Python 标准库子集（sys.stdlib_module_names，剔除本地包名）；
  3. 断言每一项要么被主程序代码（knowledge_ball.py + v4/src）import，
     要么显式列在 FloatPulse.spec 的 hiddenimports 里。
另钉死本次修复的三个模块必须在 hiddenimports 里（防手滑删掉）。

注意定位口径：本测试对主程序可达集是**近似**（文件级扫描，非 PyInstaller
模块图）；它必然红灯于「插件用了两边都没有的库」，不会误报主程序私有
细节。新增插件用了新标准库 → 按红灯提示去 spec 里补 hiddenimports。
"""

import os
import re
import sys

import pytest

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BASE)          # 项目根（FloatPulse.spec 所在）
_SRC = os.path.join(_BASE, "src")

if _BASE not in sys.path:
    sys.path.insert(0, _BASE)


# ---------------- 收集工具 ----------------

def _iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "build", "dist")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _top_imports(path):
    """单个文件里 import 的顶层包名集合（ast 解析，level>0 相对导入忽略）。"""
    import ast
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return set()
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def _stdlib_only(tops, extra_local):
    """留下标准库顶层名；extra_local 里的名字视为本地包，剔除。"""
    local = set(extra_local)
    return {t for t in tops
            if t in sys.stdlib_module_names and t not in local}


def _spec_hiddenimports():
    with open(os.path.join(_ROOT, "FloatPulse.spec"),
              "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"hiddenimports\s*=\s*\[([^\]]*)\]", text, re.S)
    assert m, "FloatPulse.spec 里找不到 hiddenimports 列表"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


# ---------------- 测试 ----------------

class TestPluginStdlibBundled:
    def test_plugin_stdlib_covered(self):
        # 插件 stdlib 依赖
        plugin_files = list(_iter_py(os.path.join(_ROOT, "plugins")))
        assert plugin_files, "plugins/ 目录为空，扫描路径是否漂移？"
        local_names = set()
        for f in plugin_files:
            local_names.add(os.path.splitext(os.path.basename(f))[0])
        plugin_tops = set()
        for f in plugin_files:
            plugin_tops |= _top_imports(f)
        plugin_std = _stdlib_only(plugin_tops, local_names)
        # PyQt6 不在 stdlib_module_names，天然过滤；防未来口径漂移
        plugin_std.discard("PyQt6")

        # 主程序可达集（近似：入口 + src 全部）
        main_files = [os.path.join(_BASE, "knowledge_ball.py")]
        main_files += list(_iter_py(_SRC))
        main_files = [f for f in main_files if os.path.exists(f)]
        assert os.path.exists(main_files[0]), "knowledge_ball.py 不在 v4/？"
        main_tops = set()
        for f in main_files:
            main_tops |= _top_imports(f)
        main_std = _stdlib_only(main_tops, set())

        hidden = {h.split(".")[0] for h in _spec_hiddenimports()}
        missing = plugin_std - main_std - hidden
        assert not missing, (
            "插件用到的标准库不在打包 hiddenimports 也不被主程序引用，"
            f"冻结包里会 ModuleNotFoundError：{sorted(missing)}"
            " → 请加入 FloatPulse.spec 的 hiddenimports")

    def test_regression_modules_pinned(self):
        """本次修复钉死：uuid（3 个插件）/ base64、hmac（vault）
        必须显式在 hiddenimports 里，主程序可达集不作为兜底借口。"""
        hidden = _spec_hiddenimports()
        for name in ("uuid", "base64", "hmac", "encodings.idna"):
            assert name in hidden, f"FloatPulse.spec hiddenimports 缺 {name!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
