# -*- coding: utf-8 -*-
"""站内搜索 v1.1.0 增强（K1 范围过滤 + K2 跳转自愈）回归。

K1：core.search 接受数据源集合——分数在全库算好后按范围排除，
多选的排序与不过滤时逐条一致；单个 kind 字符串旧口径不变。
K2：碎片/笔记跳转前模拟目标页子串过滤（与宿主面板同口径），
必空则改跳空关键词（只切页显示全部）。
"""

import importlib.util
import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_PATH = os.path.join(ROOT, "plugins", "kb-search", "plugin.py")
CORE_PATH = os.path.join(ROOT, "plugins", "kb-search", "kb_search_core.py")
_MOD = "fp_test_kb_search_kinds"
_CORE_MOD = "fp_test_kb_search_kinds_core"


@pytest.fixture(scope="module")
def plug():
    spec = importlib.util.spec_from_file_location(_MOD, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def kb():
    spec = importlib.util.spec_from_file_location(_CORE_MOD, CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_CORE_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


def _index_with(kb):
    idx = kb.SearchIndex()
    idx.add("note:1", "月度归档会议记录", kind="note", title="笔记 · 归档")
    idx.add("fragment:1", "周报归档到 Obsidian", kind="fragment",
            title="碎片 · 手记")
    idx.add("task:1", "整理月报归档", kind="task", title="任务 · 归档")
    idx.add("knowledge:1", "归档流程说明", kind="knowledge",
            title="知识库 · 第 1 段")
    idx.finalize()
    return idx


class TestCoreKindSet:
    def test_single_kind_string_unchanged(self, kb):
        idx = _index_with(kb)
        hits = idx.search("归档", kind="note")
        assert [h.kind for h in hits] == ["note"]

    def test_kind_set_filters(self, kb):
        idx = _index_with(kb)
        hits = idx.search("归档", kind={"note", "task"})
        assert {h.kind for h in hits} <= {"note", "task"}
        assert hits, "范围集合非空时应有命中"

    def test_kind_set_order_matches_unfiltered(self, kb):
        """多选排序 = 不过滤排序按范围剔除（分数在全库算好后才排除）"""
        idx = _index_with(kb)
        all_hits = idx.search("归档", top_n=50)
        subset = idx.search("归档", top_n=50, kind={"note", "task"})
        expect = [h.uid for h in all_hits if h.kind in ("note", "task")]
        assert [h.uid for h in subset] == expect

    def test_kind_set_empty_result(self, kb):
        idx = _index_with(kb)
        assert idx.search("归档", kind={"asset"}) == []

    def test_other_kinds_still_indexed(self, kb):
        """范围过滤只影响 search，索引始终覆盖全部（重建口径不变）"""
        idx = _index_with(kb)
        uids = {h.uid for h in idx.search("归档", top_n=50)}
        assert uids == {"note:1", "fragment:1", "task:1", "knowledge:1"}


class TestTargetFilterMatches:
    def test_empty_keyword_means_no_filter(self, plug):
        data = types.SimpleNamespace(fragments=lambda: [], notes=lambda: [])
        assert plug.target_filter_matches("fragment", "  ", data) is True

    def test_fragment_mirrors_host_filter(self, plug):
        data = types.SimpleNamespace(
            fragments=lambda: [
                {"fragment_id": 1, "content": "周报已归档", "source": "手记"},
                {"fragment_id": 2, "content": "别的", "source": ""}],
            notes=lambda: [])
        # 与宿主同口径：content 或 source 含关键词（不分大小写）
        assert plug.target_filter_matches("fragment", "归档", data) is True
        assert plug.target_filter_matches("fragment", "周报已", data) is True
        assert plug.target_filter_matches("fragment", "手记", data) is True
        assert plug.target_filter_matches("fragment", "不存在的词", data) is False
        assert plug.target_filter_matches("fragment", "别的", data) is True

    def test_note_mirrors_host_filter(self, plug):
        data = types.SimpleNamespace(
            fragments=lambda: [],
            notes=lambda: [
                {"note_id": 1, "title": "会议纪要", "content": "讨论了预算"}])
        assert plug.target_filter_matches("note", "纪要", data) is True
        assert plug.target_filter_matches("note", "预算", data) is True
        assert plug.target_filter_matches("note", "预算表", data) is False

    def test_other_kinds_not_applicable(self, plug):
        data = types.SimpleNamespace(fragments=lambda: [], notes=lambda: [])
        for kind in ("knowledge", "task", "asset"):
            assert plug.target_filter_matches(kind, "归档", data) is None

    def test_data_error_returns_none(self, plug):
        def boom():
            raise RuntimeError("x")
        data = types.SimpleNamespace(fragments=boom, notes=boom)
        assert plug.target_filter_matches("fragment", "归档", data) is None
        assert plug.target_filter_matches("note", "归档", data) is None


class TestSearchPageUI:
    @pytest.fixture(scope="class")
    def page(self, plug):
        from PyQt6.QtWidgets import QApplication
        # ★ 必须留存引用：裸调用被 GC → C++ 实例销毁 → QPixmap qFatal（RC=127）
        _APP = QApplication.instance() or QApplication([])
        data = types.SimpleNamespace(
            tasks=lambda: [], fragments=lambda: [], notes=lambda: [],
            knowledge=lambda: [], assets=lambda: [])
        ctx = types.SimpleNamespace(
            plugin_id="kb-search", data=data,
            parent_window=lambda: None, show_toast=lambda *a, **k: None,
            logger=types.SimpleNamespace(
                warning=lambda *a, **k: None, info=lambda *a, **k: None))
        w = plug.SearchPage(ctx)
        w.show()
        yield w
        w.deleteLater()

    def test_filter_row_default_all_on(self, page, plug):
        assert len(page._kind_checks) == 5
        assert all(c.isChecked() for c in page._kind_checks.values())
        assert page._selected_kinds() == list(plug.KIND_LABEL.keys())  # 全开

    def test_uncheck_all_blocks_search(self, page):
        page._input.setText("归档")
        for chk in page._kind_checks.values():
            chk.setChecked(False)
        page._run_search()
        assert page._hits == []
        assert "至少勾选一个" in page._stat.text()

    def test_uncheck_one_filters_results(self, page):
        page.rebuild_index()                          # 空数据也能重建
        for chk in page._kind_checks.values():
            chk.setChecked(True)
        page._input.setText("归档")
        page._run_search()
        base = len(page._hits)
        page._kind_checks["note"].setChecked(False)
        page._kind_checks["fragment"].setChecked(False)
        page._kind_checks["task"].setChecked(False)
        page._kind_checks["asset"].setChecked(False)
        page._run_search()
        # 只剩知识库范围：索引本页无知识库数据 → 空结果 + 范围提示
        assert page._hits == []
        assert "范围" in page._stat.text()
        assert base == 0                              # 空数据基准

    def test_jump_self_heal_uses_empty_keyword(self, page):
        """模拟过滤必空 → jump 收到空关键词 + toast（只切页）"""
        jumps = []
        host = types.SimpleNamespace(
            show_search_result=lambda kind, kw, num=None:
                jumps.append((kind, kw, num)) or True)
        page._ctx = types.SimpleNamespace(
            plugin_id="kb-search",
            data=types.SimpleNamespace(
                fragments=lambda: [{"fragment_id": 1, "content": "完全无关",
                                    "source": ""}],
                notes=lambda: [], tasks=lambda: [],
                knowledge=lambda: [], assets=lambda: []),
            parent_window=lambda: host,
            show_toast=lambda msg, ms=0: setattr(page, "_last_toast", msg),
            logger=types.SimpleNamespace(
                warning=lambda *a, **k: None, info=lambda *a, **k: None))

        class _Hit:
            uid = "fragment:1"
            kind = "fragment"
            matched = ("zzz",)          # 检索项在目标数据里过滤必空
        page._jump_to(_Hit())
        assert jumps == [("fragment", "", None)]
        assert "显示全部" in getattr(page, "_last_toast", "")

    def test_jump_passes_keyword_when_filter_matches(self, page):
        jumps = []
        host = types.SimpleNamespace(
            show_search_result=lambda kind, kw, num=None:
                jumps.append((kind, kw, num)) or True)
        page._ctx = types.SimpleNamespace(
            plugin_id="kb-search",
            data=types.SimpleNamespace(
                fragments=lambda: [{"fragment_id": 1, "content": "周报归档",
                                    "source": ""}],
                notes=lambda: [], tasks=lambda: [],
                knowledge=lambda: [], assets=lambda: []),
            parent_window=lambda: host,
            show_toast=lambda msg, ms=0: None,
            logger=types.SimpleNamespace(
                warning=lambda *a, **k: None, info=lambda *a, **k: None))

        class _Hit:
            uid = "fragment:1"
            kind = "fragment"
            matched = ("归档",)
        page._jump_to(_Hit())
        assert jumps == [("fragment", "归档", None)]
