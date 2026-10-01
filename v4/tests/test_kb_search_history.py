# -*- coding: utf-8 -*-
"""站内搜索 · 搜索历史（K3，v1.2.0）回归。

钉住四条底线：
1. sanitize_history：脏数据宽容降级、截断、去重、上限 10；
2. 存档 roundtrip + 坏文件降级为空（原文件保留）；
3. 有效检索记入历史、点击历史词回填重搜、删除单条、清空；
4. 「最近」行只在输入框为空且有历史时显示。
"""

import importlib.util
import json
import os
import sys
import tempfile
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_PATH = os.path.join(ROOT, "plugins", "kb-search", "plugin.py")
_MOD = "fp_test_kb_search_history"


_APP = None      # QApplication 必须留存引用：裸调用被 GC → C++ 实例销毁 →
                 # 后续 QPixmap 构造 qFatal（RC=127 静默硬崩，见项目铁律）


@pytest.fixture(scope="module")
def plug():
    spec = importlib.util.spec_from_file_location(_MOD, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- 纯函数：消毒 / 存档 ----------------

class TestSanitize:
    def test_non_list(self, plug):
        assert plug.sanitize_history(None) == []
        assert plug.sanitize_history("x") == []
        assert plug.sanitize_history(42) == []

    def test_strip_truncate_dedupe(self, plug):
        out = plug.sanitize_history(["  归档  ", "归档", "", None,
                                     "周" * 80])
        assert out == ["归档", "周" * 60]

    def test_cap_ten(self, plug):
        out = plug.sanitize_history([f"w{i}" for i in range(15)])
        assert out == [f"w{i}" for i in range(10)]


def _ctx(data_dir=None):
    return types.SimpleNamespace(
        plugin_id="kb-search",
        data_dir=data_dir,
        data=types.SimpleNamespace(
            tasks=lambda: [], fragments=lambda: [], notes=lambda: [],
            knowledge=lambda: [], assets=lambda: []),
        parent_window=lambda: None, show_toast=lambda *a, **k: None,
        logger=types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None))


class TestStore:
    def test_roundtrip(self, plug, tmp_path):
        ctx = _ctx(str(tmp_path))
        assert plug.save_history(ctx, ["a", "b"]) is True
        assert plug.load_history(ctx) == ["a", "b"]

    def test_bad_file_degrades_empty_and_kept(self, plug, tmp_path):
        ctx = _ctx(str(tmp_path))
        path = os.path.join(str(tmp_path), plug.HISTORY_FILE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        assert plug.load_history(ctx) == []
        assert os.path.isfile(path)          # 原文件保留

    def test_missing_dir_returns_false(self, plug):
        ctx = _ctx(None)                     # 无 data_dir（旧宿主/兜底）
        assert plug.save_history(ctx, ["a"]) is False
        assert plug.load_history(ctx) == []


# ---------------- UI：最近行 ----------------

@pytest.fixture(scope="module")
def qapp():
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    yield _APP


@pytest.fixture()
def page(qapp, plug):
    ctx = _ctx(tempfile.mkdtemp(prefix="fp_kb_hist_"))
    w = plug.SearchPage(ctx)
    w.show()
    yield w
    w.deleteLater()


def test_initial_empty_history_hidden(page):
    assert page._history == []
    assert not page._recent_row.isVisible()


def test_run_search_records_history(page, plug):
    page._input.setText("归档")
    page._run_search()
    assert page._history == ["归档"]
    path = os.path.join(page._ctx.data_dir, plug.HISTORY_FILE)
    assert json.load(open(path, encoding="utf-8")) == ["归档"]


def test_remember_dedup_moves_front(page):
    page._remember_history("甲")
    page._remember_history("乙")
    page._remember_history("甲")
    assert page._history == ["甲", "乙"]


def test_recent_row_visibility(page):
    page._remember_history("甲")
    page._sync_recent_visible()
    assert page._recent_row.isVisible()      # 输入空 + 有历史
    page._input.setText("甲")                # 填入 → 隐藏
    assert not page._recent_row.isVisible()
    page._input.clear()
    assert page._recent_row.isVisible()


def test_recent_row_widgets(page):
    page._remember_history("甲")
    page._remember_history("乙")
    page._sync_recent_visible()
    lay = page._recent_row.layout()
    texts = [lay.itemAt(i).widget().text()
             for i in range(lay.count())
             if lay.itemAt(i).widget() is not None]
    assert texts == ["最近", "乙", "甲", "清空"]


def test_click_history_word_fills_input(page):
    page._remember_history("甲")
    page._sync_recent_visible()
    lay = page._recent_row.layout()
    btn = next(lay.itemAt(i).widget() for i in range(lay.count())
               if lay.itemAt(i).widget() is not None
               and lay.itemAt(i).widget().text() == "甲")
    btn.click()
    assert page._input.text() == "甲"
    assert page._history == ["甲"]           # 顶到最前（本来就在）


def test_remove_history(page, plug):
    page._remember_history("甲")
    page._remember_history("乙")
    page._remove_history("甲")
    assert page._history == ["乙"]
    path = os.path.join(page._ctx.data_dir, plug.HISTORY_FILE)
    assert json.load(open(path, encoding="utf-8")) == ["乙"]
    page._remove_history("乙")
    assert not page._recent_row.isVisible()  # 清空后整行隐藏


def test_clear_history(page):
    page._remember_history("甲")
    page._remember_history("乙")
    page._clear_history()
    assert page._history == []
    assert not page._recent_row.isVisible()


def test_empty_query_not_recorded(page):
    page._input.setText("   ")
    page._run_search()
    assert page._history == []
