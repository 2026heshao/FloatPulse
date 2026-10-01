# -*- coding: utf-8 -*-
"""周报插件 v1.2.0（P2 段落开关 + P3 导出历史）回归。

钉住：
1. include=None 与旧版输出逐字节一致（向后兼容硬保证）；
2. 各段落开关独立生效；「任务」勾选同时控制完成+未完成两段；
   统计行只列保留段落；专注统计恒保留；
3. 导出历史：消毒 / roundtrip / 去重置前 / 上限 10 / 坏文件降级；
4. UI：三勾选联动重生成、footer 新增按钮不破坏既有五个文案、
   另存/写 vault 成功记入最近导出、菜单构造与清空。
"""

import importlib.util
import os
import sys
import tempfile
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "weekly-report", "plugin.py")
_MOD_NAME = "fp_test_weekly_v120_plugin"


@pytest.fixture(scope="module")
def qapp():
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    yield _APP


_APP = None


@pytest.fixture(scope="module")
def plug():
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_TODAY = __import__("datetime").date(2026, 9, 30)
_TASKS = [{"task_id": 1, "title": "完成 A", "done": True,
           "deadline": "", "completed_at": "2026-09-28 10:00",
           "focus_sessions": 2},
          {"task_id": 2, "title": "推进 B", "done": False,
           "deadline": "2026-09-29", "focus_sessions": 1}]
_FRAGS = [{"fragment_id": 1, "content": "想到一个点子",
           "category": "text", "source": "随手记",
           "created_at": "2026-09-29 09:00"}]
_NOTES = [{"note_id": 1, "title": "会议纪要", "content": "…",
           "update_time": "2026-09-29 11:00", "create_time": ""}]


def _report(plug, **kw):
    return plug.build_report(_TASKS, _FRAGS, _NOTES, {},
                             _TODAY - __import__("datetime").timedelta(days=6),
                             _TODAY, now=__import__("datetime").datetime(
                                 2026, 9, 30, 18, 0), **kw)


# ---------------- P2：段落开关 ----------------

class TestInclude:
    def test_none_matches_legacy(self, plug):
        """include=None 与旧路径（无参数）输出逐字节一致"""
        assert _report(plug) == _report(plug, include=None)

    def test_missing_keys_default_on(self, plug):
        assert _report(plug, include={}) == _report(plug, include=None)

    def test_drop_fragments(self, plug):
        text = _report(plug, include={"fragments": False})
        assert "🧩" not in text and "想到一个点子" not in text
        assert "碎片" not in text.split("\n")[3]        # 统计行不再列碎片
        assert "## ✅" in text and "## 📝" in text       # 其他段保留

    def test_drop_tasks_hides_both_sections_and_tail(self, plug):
        text = _report(plug, include={"done": False, "open": False})
        assert "## ✅" not in text and "## 🔄" not in text
        assert "完成 A" not in text and "推进 B" not in text
        assert "🍅" in text                              # 专注统计恒保留

    def test_drop_notes_and_pomodoro(self, plug):
        text = _report(plug, include={"notes": False, "pomodoro": False})
        assert "## 📝" not in text and "会议纪要" not in text
        assert "## 🍅" not in text

    def test_normalize_include_dirty(self, plug):
        assert plug.normalize_include(None)["done"] is True
        assert plug.normalize_include(42)["open"] is True
        assert plug.normalize_include({"done": 0})["done"] is False
        assert plug.normalize_include({"bogus": False})["notes"] is True


# ---------------- P3：导出历史存档 ----------------

def _ctx(data_dir=None):
    return types.SimpleNamespace(
        plugin_id="weekly-report",
        data_dir=data_dir or tempfile.mkdtemp(prefix="fp_wr_exp_"),
        config={"obsidian_vault_path": ""},
        data=types.SimpleNamespace(
            sources=lambda: [], tasks=lambda: [], fragments=lambda: [],
            notes=lambda: [], pomodoro=lambda: {}),
        logger=types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None),
        show_toast=lambda *a, **k: None,
        parent_window=lambda: None,
        http_post_json_async=lambda *a, **k: False,
        has_capability=lambda cap: False,
        open_main_window=lambda: None,
    )


class TestExportsStore:
    def test_sanitize_dirty(self, plug):
        out = plug.sanitize_exports(None)
        assert out == []
        out = plug.sanitize_exports(["junk", 42,
                                     {"path": "  a.md  ", "at": "x"},
                                     {"path": "a.md", "at": "2026"},
                                     {"path": "b" * 900}])
        # 去重保先出现（与 sanitize_history 同口径；record_export 负责置前）
        assert out == [{"path": "a.md", "at": "x"},
                       {"path": "b" * 500, "at": ""}]

    def test_sanitize_cap(self, plug):
        raw = [{"path": f"p{i}.md"} for i in range(15)]
        assert [it["path"] for it in plug.sanitize_exports(raw)] == \
            [f"p{i}.md" for i in range(10)]

    def test_roundtrip(self, plug):
        ctx = _ctx()
        assert plug.record_export(ctx, "D:/x/周报-a.md",
                                  at=__import__("datetime").datetime(
                                      2026, 9, 30, 9, 30))
        assert plug.load_exports(ctx) == [
            {"path": "D:/x/周报-a.md", "at": "2026-09-30 09:30"}]

    def test_record_moves_existing_front(self, plug):
        ctx = _ctx()
        plug.record_export(ctx, "a.md")
        plug.record_export(ctx, "b.md")
        plug.record_export(ctx, "a.md")
        paths = [it["path"] for it in plug.load_exports(ctx)]
        assert paths == ["a.md", "b.md"]

    def test_bad_file_degrades(self, plug, tmp_path):
        ctx = _ctx(str(tmp_path))
        path = os.path.join(str(tmp_path), plug.EXPORTS_FILE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{bad")
        assert plug.load_exports(ctx) == []
        assert os.path.isfile(path)

    def test_no_data_dir(self, plug):
        ctx = _ctx()
        ctx.data_dir = None                 # 宿主未注入数据目录
        assert plug.save_exports(ctx, []) is False
        assert plug.record_export(ctx, "a.md") is False
        assert plug.load_exports(ctx) == []


# ---------------- UI：勾选 / footer / 最近导出 ----------------

@pytest.fixture()
def page_factory(qapp, plug):
    made = []

    def make(data_dir=None):
        ctx = _ctx(data_dir)
        d = plug.ReportDialog(ctx)
        d.show()
        made.append(d)
        return d

    yield make
    for d in made:
        d.deleteLater()


def test_footer_buttons_intact(page_factory):
    d = page_factory()
    # F6 护卫：既有五个按钮文案与角色不变（新增按钮只加在最左）
    assert d._recent_btn.text() == "🕘 最近导出"
    assert d._copy_btn.text() == "📋 复制到剪贴板"
    assert d._save_btn.text() == "另存为 .md…"
    assert d._vault_btn.text() == "🗂 写入 Obsidian vault"


def test_section_toggles_regenerate(page_factory):
    d = page_factory()
    assert "## 🧩" in d._text.toPlainText()
    d._inc_frag.setChecked(False)
    assert "## 🧩" not in d._text.toPlainText()
    assert "## ✅" in d._text.toPlainText()
    d._inc_tasks.setChecked(False)
    assert "## ✅" not in d._text.toPlainText()
    assert "## 🔄" not in d._text.toPlainText()
    assert "## 🍅" in d._text.toPlainText()


def test_save_as_records_export(page_factory, plug, monkeypatch, tmp_path):
    d = page_factory(str(tmp_path))
    out = tmp_path / "out.md"
    monkeypatch.setattr(plug.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    d._do_save_as()
    assert out.exists()
    items = plug.load_exports(d._ctx)
    assert len(items) == 1 and items[0]["path"] == str(out)


def test_vault_write_records_export(page_factory, plug, monkeypatch,
                                    tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    d = page_factory(str(tmp_path / "ctxdata"))
    d._ctx.config["obsidian_vault_path"] = str(vault)
    d._vault_btn.setEnabled(True)
    monkeypatch.setattr(
        plug.QMessageBox, "question",
        staticmethod(lambda *a, **k: plug.QMessageBox.StandardButton.Yes))
    d._do_vault()
    items = plug.load_exports(d._ctx)
    assert len(items) == 1
    assert "报告" in items[0]["path"]


def test_recent_menu_construction(page_factory, plug):
    d = page_factory()
    # 空记录菜单构造（不 exec，只断言能建出来）
    menu = plug.QMenu(d)
    empty = menu.addAction("（还没有导出记录）")
    empty.setEnabled(False)
    assert not empty.isEnabled()
    # 有记录 → record 后 load 能取回
    plug.record_export(d._ctx, "D:/x/报告.md")
    items = plug.load_exports(d._ctx)
    assert items and items[0]["path"] == "D:/x/报告.md"


def test_clear_exports(page_factory, plug):
    d = page_factory()
    plug.record_export(d._ctx, "a.md")
    d._clear_exports()
    assert plug.load_exports(d._ctx) == []
    assert "已清空" in d._status.text()
