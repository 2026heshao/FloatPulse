# -*- coding: utf-8 -*-
"""插件**宿主模块**（src.*）依赖 ⊆ (主程序可达集 ∪ spec hiddenimports) 护栏。

背景（2026-10-11 zip 版实机踩坑）：
  插件能 import 宿主模块（``from src.plugin_ui import ...``）是插件契约的
  一部分，但冻结（PyInstaller）包里只有**主程序静态分析可达**的模块。
  ``src/plugin_ui.py`` 只被插件 import、主程序自身从不引用 → 打包产物里
  没有它 → 插件装好后加载失败：
  ``ModuleNotFoundError: No module named 'src.plugin_ui'``。

  这是 ``test_plugin_bundle_stdlib.py`` 的**同构缺口**：那份只盖标准库，
  宿主模块（src.*）此前完全没有护栏，所以同一类事故换了张皮又发生一次。

本护栏在**不打包**的前提下比对：
  1. 扫 plugins/*/（含子目录）全部 ``from src.X import`` / ``import src.X``；
  2. 取主程序可达的 src 模块集（knowledge_ball.py + v4/src 全量，用
     ``src.X`` / ``from src import X`` 两种写法都识别）；
  3. 断言每个插件依赖的 src 模块要么被主程序 import，要么显式列在
     FloatPulse.spec 的 hiddenimports 里（``src.X`` 形式）。
另钉死本次修复模块 ``src.plugin_ui`` 必须在 hiddenimports 里（防手滑删）。

注意定位口径：主程序可达集是**文件级扫描近似**（非 PyInstaller 模块图），
必然保守 —— 它不会漏报「插件用了两边都没有的宿主模块」。
"""

import ast
import os
import re

import pytest

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BASE)          # 项目根（FloatPulse.spec 所在）
_V4 = _BASE
_SRC = os.path.join(_BASE, "src")

if _BASE not in os.sys.path:
    os.sys.path.insert(0, _BASE)


def _iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "build", "dist")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _src_module_imports(path) -> set:
    """单个文件里依赖的 ``src.<模块>`` 名字集合（含 ``from src import X``）。

    只认顶层绝对导入（level == 0）：相对导入属于文件内部组织，与冻结包
    可达性无关。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "src":
                # from src import plugin_ui, controls
                for alias in node.names:
                    out.add(alias.name)
            elif node.module.startswith("src."):
                # from src.plugin_ui import X
                out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    out.add(alias.name.split(".")[1])
    return out


def _spec_hiddenimports() -> set:
    with open(os.path.join(_ROOT, "FloatPulse.spec"),
              "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"hiddenimports\s*=\s*\[([^\]]*)\]", text, re.S)
    assert m, "FloatPulse.spec 里找不到 hiddenimports 列表"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


class TestPluginHostModulesBundled:
    def test_plugin_src_modules_covered(self):
        plugin_files = list(_iter_py(os.path.join(_ROOT, "plugins")))
        assert plugin_files, "plugins/ 目录为空，扫描路径是否漂移？"
        plugin_mods = set()
        for f in plugin_files:
            plugin_mods |= _src_module_imports(f)
        assert plugin_mods, (
            "没扫到任何 `from src.X import` —— 插件契约是否变了？"
            "（本护栏若失效等于没有）")

        # 主程序可达集（近似：入口 + v4/src 全量）
        main_files = [os.path.join(_V4, "knowledge_ball.py")]
        main_files += list(_iter_py(_SRC))
        main_files = [f for f in main_files if os.path.exists(f)]
        assert os.path.exists(main_files[0]), "knowledge_ball.py 不在 v4/？"
        main_mods = set()
        for f in main_files:
            main_mods |= _src_module_imports(f)

        # hiddenimports 里可能写成 src.X（宿主模块）或裸名（标准库）；
        # 只取 src.* 那部分与插件依赖面比对
        hidden = _spec_hiddenimports()
        hidden_src = {h.split(".")[1] for h in hidden
                      if h.startswith("src.")}

        missing = sorted(m for m in plugin_mods
                         if m not in main_mods and m not in hidden_src)
        assert not missing, (
            "插件依赖的宿主模块不在打包 hiddenimports 也不被主程序引用，"
            f"冻结包里会 ModuleNotFoundError：{[f'src.{m}' for m in missing]}"
            " → 请加入 FloatPulse.spec 的 hiddenimports")

    def test_plugin_ui_pinned(self):
        """本次修复钉死：src.plugin_ui 必须在 hiddenimports 里。

        主程序可达集不作为兜底借口——plugin_ui 是「只给插件用」的宿主
        模块，一旦有人把它从 hiddenimports 摘掉，冻结包立刻复发。
        """
        hidden = _spec_hiddenimports()
        assert "src.plugin_ui" in hidden, (
            "FloatPulse.spec hiddenimports 缺 'src.plugin_ui'"
            "（插件会 ModuleNotFoundError）")

    def test_probe_self_check(self):
        """护栏自检：扫描器必须真的能识别两种 import 写法。

        若哪天 AST 口径漂移（比如只认一种写法），本护栏会静默失效——
        这里用内联源码直接验证，不依赖仓库文件布局。
        """
        import tempfile
        samples = {
            "from src.plugin_ui import PluginDialog": {"plugin_ui"},
            "from src import plugin_ui": {"plugin_ui"},
            "import src.controls": {"controls"},
            "from src.a.b import c": {"a"},
            "from . import relative": set(),          # 相对导入不算
            "import os": set(),
        }
        for code, expect in samples.items():
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".py", delete=False,
                    encoding="utf-8") as fh:
                fh.write(code + "\n")
                path = fh.name
            try:
                assert _src_module_imports(path) == expect, (
                    f"扫描口径漂移：{code!r} 得到 "
                    f"{_src_module_imports(path)}，期望 {expect}")
            finally:
                os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
