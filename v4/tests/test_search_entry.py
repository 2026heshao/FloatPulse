# -*- coding: utf-8 -*-
"""Ctrl+K 站内搜索入口（并入 kb-search 插件）的回归。

背景（2026-09-29）
--------------------------------
原宿主内置的 ``GlobalSearchDialog``（Ctrl+K）整体并入 ``kb-search`` 插件：
对话框文件删除，能力（素材覆盖 + 结果跳转）迁进插件，Ctrl+K 改为打开
插件页。这是**跨宿主 / 插件的接口改造**，坏起来有两个特点：

  1. **静默**——热键连到一个已经被删掉的方法上，只在用户按 Ctrl+K 时才炸；
     插件侧调一个宿主不存在的方法，被 ``getattr`` 兜底成「提示一句」，
     功能没了但测试全绿。
  2. **难在 pytest 里跑**——MainWindow 要 QApplication，本文件走静态 AST
     分析 + 纯函数测试，把「接线对不对」钉死，行为验证交给离屏脚本
     ``tools/verify_kb_search.py``。

覆盖：
  A 旧对话框确实删干净了（文件不在、全仓无引用）
  B Ctrl+K 接到新入口，且旧入口方法已不存在
  C ``show_search_result`` 的 kind→页索引映射与宿主 NAV_PAGE_INDEX 一致
  D 插件侧跳转的两个纯函数（锚点解析 / 关键词选择）
  E 宿主数据源契约：PluginData 必须暴露 assets
"""

import ast
import importlib.util
import io
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

MAIN_WINDOW = os.path.join(BASE, "src", "main_window.py")
OLD_DIALOG = os.path.join(BASE, "src", "global_search_dialog.py")
PLUGIN_PATH = os.path.join(ROOT, "plugins", "kb-search", "plugin.py")
PLUGIN_ID = "kb-search"
PAGE_KEY = f"plugin:{PLUGIN_ID}"


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _tree(path):
    return ast.parse(_read(path))


def _main_window_class():
    for node in ast.walk(_tree(MAIN_WINDOW)):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow 类没找到")


def _method_node(name):
    for node in _main_window_class().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


@pytest.fixture(scope="module")
def plug():
    """直载插件模块（纯函数部分不构造控件，不需要 QApplication）"""
    spec = importlib.util.spec_from_file_location(
        "fp_test_search_entry_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ====================================================================
# A 旧对话框删干净
# ====================================================================
class TestLegacyRemoved:
    def test_dialog_file_deleted(self):
        assert not os.path.exists(OLD_DIALOG), \
            "旧的全库搜索对话框应已并入插件，文件不该还在"

    def test_no_reference_left_anywhere(self):
        """全仓不能有任何残留引用——留着的话 import 时才炸，属静默缺陷

        ⚠ 排除项：``.workbuddy/backups``（本项目的改动前备份，天然含旧代码）
        和本测试文件自身（要断言那两个名字，必然命中自己）。
        """
        self_path = os.path.normcase(os.path.abspath(__file__))
        hits = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".git", ".pytest_cache",
                                    "build", "dist", "dist2", ".workbuddy",
                                    "node_modules")]
            for f in files:
                if not f.endswith((".py", ".json")):
                    continue
                p = os.path.join(root, f)
                if os.path.normcase(os.path.abspath(p)) == self_path:
                    continue
                try:
                    src = _read(p)
                except Exception:                 # noqa: BLE001
                    continue
                if "global_search_dialog" in src or "GlobalSearchDialog" in src:
                    hits.append(os.path.relpath(p, ROOT))
        assert hits == [], f"仍有残留引用：{hits}"

    def test_no_search_dialog_attribute_left(self):
        """``_search_dialog`` 相关的属性/清理逻辑必须一起删掉，
        否则 _apply_theme / _hide_aux_windows 会引用一个永不存在的对象"""
        src = _read(MAIN_WINDOW)
        assert "_search_dialog" not in src
        assert "_on_search_jump" not in src
        assert "_hide_aux_windows" not in src


# ====================================================================
# B Ctrl+K 接线
# ====================================================================
class TestShortcutWiring:
    def test_ctrl_k_opens_new_entry(self):
        src = _read(MAIN_WINDOW)
        tree = _tree(MAIN_WINDOW)
        # 找到 QShortcut(QKeySequence("Ctrl+K"), ...) 那一段
        found_key, found_target = False, set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "userData":
                    pass
            # QShortcut(QKeySequence("Ctrl+K"), self)
            if (isinstance(node.func, ast.Name) and node.func.id == "QShortcut"
                    and node.args and isinstance(node.args[0], ast.Call)
                    and getattr(node.args[0].func, "id", "") == "QKeySequence"
                    and node.args[0].args
                    and isinstance(node.args[0].args[0], ast.Constant)
                    and node.args[0].args[0].value == "Ctrl+K"):
                found_key = True
        assert found_key, "main_window 里找不到 Ctrl+K 的 QShortcut 注册"
        # 同一段里必须 connect 到新入口
        assert "search_shortcut.activated.connect(self._open_search_entry)" in src
        del found_target

    def test_old_entry_method_gone(self):
        assert _method_node("_open_global_search") is None

    def test_new_entry_and_jump_exist(self):
        assert _method_node("_open_search_entry") is not None
        assert _method_node("show_search_result") is not None

    def test_entry_uses_plugin_page_and_degrades(self):
        """入口必须先试插件页；失败只提示、不抛、不新开对话框"""
        node = _method_node("_open_search_entry")
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        names = {getattr(c.func, "attr", "") for c in calls}
        assert "show_plugin_page" in names
        assert "show_toast" in names
        # 不许再出现任何对话框构造（QDialog / GlobalSearchDialog）
        assert "GlobalSearchDialog" not in ast.dump(node)

    def test_entry_page_key_matches_plugin_id(self):
        src = _read(MAIN_WINDOW)
        assert 'SEARCH_PLUGIN_ID = "kb-search"' in src
        assert 'plugin:{self.SEARCH_PLUGIN_ID}' in src \
            or 'f"plugin:{self.SEARCH_PLUGIN_ID}"' in src
        assert PAGE_KEY == "plugin:kb-search"


# ====================================================================
# C show_search_result：kind → 页索引映射
# ====================================================================
def _jump_mapping():
    """从 AST 里把 ``show_search_result`` 内的 kind → 页索引字面量映射表抠出来。

    跟着实现走、不另抄一份：抄一份的话两边一起改错就永远测不出来。
    """
    node = _method_node("show_search_result")
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Dict) and len(sub.keys) >= 5
                and all(isinstance(k, ast.Constant) for k in sub.keys)
                and all(isinstance(v, ast.Constant) for v in sub.values)):
            return {k.value: v.value for k, v in zip(sub.keys, sub.values)}
    raise AssertionError("show_search_result 里没找到 kind 映射表")


class TestJumpMapping:
    def test_covers_plugin_kinds(self, plug):
        """插件会产生的每一种 kind 都要有落点，漏一个就变成「点了没反应」"""
        mapping = _jump_mapping()
        assert set(plug.KIND_LABEL) <= set(mapping), \
            f"插件有 {sorted(set(plug.KIND_LABEL))}，宿主只认 {sorted(mapping)}"

    def test_indices_match_nav_page_index(self):
        """映射值必须等于宿主 NAV_PAGE_INDEX 的真实物理索引——
        写错一位就是把用户送到别的页面（且完全不报错）"""
        from src.main_window import NAV_PAGE_INDEX
        expect = {
            "fragment": NAV_PAGE_INDEX["fragments"],
            "task": NAV_PAGE_INDEX["tasks"],
            "note": NAV_PAGE_INDEX["notes"],
            "knowledge": NAV_PAGE_INDEX["knowledge"],
            "asset": NAV_PAGE_INDEX["assets"],
        }
        assert _jump_mapping() == expect, f"{_jump_mapping()} != {expect}"

    def test_unknown_kind_not_mapped(self):
        """未知 kind 不能被静默当成 0 号页（那会跳到碎片页，比不跳更迷惑）"""
        src = _read(MAIN_WINDOW)
        assert '.get(str(kind or ""))' in src
        assert "if idx is None:" in src


# ====================================================================
# D 插件侧跳转的纯函数
# ====================================================================
class TestPluginJumpHelpers:
    def test_parse_anchor_roundtrip(self, plug):
        for i in (0, 1, 17, 59):
            assert plug.parse_result_anchor(f"{plug.RESULT_SCHEME}:{i}") == i

    def test_parse_anchor_rejects_foreign(self, plug):
        assert plug.parse_result_anchor("http://example.com") is None
        assert plug.parse_result_anchor("") is None
        assert plug.parse_result_anchor(None) is None
        assert plug.parse_result_anchor("fp-result:") is None
        assert plug.parse_result_anchor("fp-result:abc") is None
        assert plug.parse_result_anchor("fp-result:-1") is None
        # 相邻 scheme 前缀不能被误吞
        assert plug.parse_result_anchor("fp-result-x:3") is None

    def test_jump_keyword_prefers_longest_match(self, plug):
        """整条查询在目标面板是「原样子串过滤」，必须换成字面命中的单项"""
        assert plug.pick_jump_keyword(["报", "月报", "报表归档"], "月报 归档") \
            == "报表归档"
        # 命中项长度相同时取先出现的那个（make_snippet 按出现位置排序，
        # 所以这里等价于「取最靠前的最长项」）——顺序稳定，不随哈希抖动
        assert plug.pick_jump_keyword(["月报", "归档"], "月报 归档") == "月报"
        assert plug.pick_jump_keyword(["归档", "月报"], "月报 归档") == "归档"

    def test_jump_keyword_falls_back_to_query(self, plug):
        assert plug.pick_jump_keyword([], "月报 归档") == "月报 归档"
        assert plug.pick_jump_keyword(None, " 空格 ") == "空格"
        assert plug.pick_jump_keyword(["", None], "兜底") == "兜底"

    def test_jump_to_calls_host_entry(self, plug):
        """宿主入口名必须与 main_window 里的一致（改一边不改另一边会静默失效）"""
        src = _read(PLUGIN_PATH)
        assert 'getattr(host, "show_search_result", None)' in src
        assert _method_node("show_search_result") is not None

    def test_knowledge_num_comes_from_uid(self, plug):
        """知识库是位置型数据源：段号必须从 uid 取，才能定位到那一段"""
        src = _read(PLUGIN_PATH)
        assert 'str(hit.uid).split(":", 1)[1]' in src

    def test_page_binds_anchor_click(self, plug):
        """锚点分发绑定契约。v1.5.0 起结果区是卡片列：卡内 QLabel 链接
        （标题/正文/尾行）统一经 _on_card_link 转 QUrl 走 _on_anchor 单点
        分发；setOpenExternalLinks(False) 防止自定义 scheme 被系统浏览器
        抢走的红线保留在每张卡的链接标签上"""
        src = _read(PLUGIN_PATH)
        # 标题的绑定在 _ElidedTitle 内部（self.linkActivated），
        # 正文/尾行在 ResultCard 上（self._body / self._foot）
        assert "self.linkActivated.connect(page._on_card_link)" in src
        assert "self._body.linkActivated.connect(page._on_card_link)" in src
        assert "self._foot.linkActivated.connect(page._on_card_link)" in src
        assert "def _on_card_link(self, href):" in src
        assert "self._on_anchor(QUrl(href))" in src
        assert src.count("setOpenExternalLinks(False)") >= 3

    def test_hits_cache_is_reset_on_every_path(self, plug):
        """渲染用的 hits 与点击时反查的 _hits 必须是同一个列表；
        空查询 / 检索异常两条提前返回路径也要清空，否则会点到上一轮的结果

        ⚠ ast.dump 里赋值是 ``Attribute(attr='_hits')``，不能搜源码字符串
        ``self._hits = ``（dump 不含源码文本）。
        """
        node = None
        for n in ast.walk(_tree(PLUGIN_PATH)):
            if isinstance(n, ast.FunctionDef) and n.name == "_run_search":
                node = n
        assert node is not None
        assigns = [n for n in ast.walk(node) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Attribute) and t.attr == "_hits"
                           for t in n.targets)]
        assert len(assigns) >= 3, \
            f"_run_search 的三条路径都要写 self._hits，实际 {len(assigns)} 处"


# ====================================================================
# E 宿主数据源契约
# ====================================================================
class TestHostDataContract:
    def test_plugin_data_exposes_assets(self):
        from src.plugin_api import PluginData
        assert PluginData.SOURCE_ASSETS == "assets"
        assert hasattr(PluginData, "assets")

    def test_assets_accessor_returns_list_on_missing_source(self):
        """数据源缺失要降级成空列表，不能让插件侧拿到 None 炸掉"""
        from src.plugin_api import PluginData
        assert PluginData().assets() == []

    def test_host_injects_assets_provider(self):
        """knowledge_ball 装配时必须注入 assets provider——
        漏了的话插件静默少一个数据源（搜不到素材，且不报错）"""
        src = _read(os.path.join(BASE, "knowledge_ball.py"))
        assert '"assets": lambda:' in src
        assert "get_all_assets()" in src
        # 素材只给元数据：不给文件路径（依赖白名单没有文件系统）
        assert 'a.original_name' in src
        assert 'a.stored_path' not in src

    def test_knowledge_panel_can_locate(self):
        """知识库定位入口必须存在，且清搜索框时挡掉去抖信号"""
        from src.knowledge_panel import KnowledgePanel
        assert hasattr(KnowledgePanel, "locate_paragraph")
        src = _read(os.path.join(BASE, "src", "knowledge_panel.py"))
        assert "self._kb_search.blockSignals(True)" in src
        assert "self._search_timer.stop()" in src
