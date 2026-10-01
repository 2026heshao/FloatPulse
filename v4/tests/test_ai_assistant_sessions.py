# -*- coding: utf-8 -*-
"""AI 助手会话持久化 + 多会话（A1，v1.9.0）回归测试。

钉住三条底线：
1. 存档读写 roundtrip 与消毒（脏数据宽容降级、上限裁剪、current_id 回落）；
2. 成功轮次落盘（_persist_turn）→ 重建页面后问答气泡与模型上下文恢复；
3. 多会话 UI 行为（新开/切换/删除/清空）与「至少一个会话」不变式。
"""

import importlib.util
import json
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "ai-assistant", "plugin.py")
_MOD_NAME = "fp_test_ai_assistant_sessions_plugin"


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def plug():
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeAI:
    def add_listener(self, cb):
        pass

    def remove_listener(self, cb):
        pass

    def is_attached(self, plugin_id=None):
        return False

    def params(self, plugin_id=None):
        return {"mode": "cloud", "base_url": "", "model": ""}

    def stop_local(self):
        pass


def _make_ctx(data_dir=None):
    return types.SimpleNamespace(
        plugin_id="ai-assistant",
        data_dir=data_dir or tempfile.mkdtemp(prefix="fp_ai_sess_test_"),
        has_capability=lambda cap: True,
        ai=_FakeAI(),
        data=types.SimpleNamespace(
            tasks=lambda: [], fragments=lambda: [],
            notes=lambda: [], knowledge=lambda: [],
        ),
        write=types.SimpleNamespace(add_note=lambda **kw: None),
        manage=types.SimpleNamespace(
            can_manage=lambda: False, undo_delete=lambda token: None),
        show_toast=lambda msg, **kw: None,
        http_post_json_async=lambda *a, **k: False,
        parent_window=lambda: None,
        open_main_window=lambda: None,
        logger=types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None),
    )


# ---------------- 纯逻辑：消毒 / 上限 / roundtrip ----------------

class TestSanitize:
    def test_non_dict_returns_empty_store(self, plug):
        for bad in (None, [], "x", 42):
            st = plug.sanitize_sessions(bad)
            assert st == {"version": 1, "current_id": "", "sessions": []}

    def test_valid_roundtrip_keeps_fields(self, plug):
        raw = {"version": 1, "current_id": "a",
               "sessions": [
                   {"id": "a", "title": "周会", "created_at": "t0",
                    "updated_at": "t1",
                    "messages": [
                        {"role": "user", "content": "q", "display": "Q"},
                        {"role": "assistant", "content": "A", "display": "A"}]}]}
        st = plug.sanitize_sessions(raw)
        assert st["current_id"] == "a"
        assert st["sessions"][0]["title"] == "周会"
        assert st["sessions"][0]["messages"][0]["display"] == "Q"

    def test_dirty_entries_dropped(self, plug):
        raw = {"sessions": [
            "junk",                                   # 非 dict 会话
            {"id": "", "messages": [{"role": "user", "content": "x"}]},  # 空 id
            {"id": "b", "messages": [
                {"role": "system", "content": "no"},  # 非法角色
                {"role": "user", "content": "   "},   # 空内容
                {"role": "user", "content": "keep", "display": "K"}]},
        ]}
        st = plug.sanitize_sessions(raw)
        assert [s["id"] for s in st["sessions"]] == ["b"]
        assert st["sessions"][0]["messages"] == [
            {"role": "user", "content": "keep", "display": "K"}]

    def test_message_char_cap(self, plug):
        raw = {"sessions": [{"id": "a", "messages": [
            {"role": "user", "content": "x" * 99999}]}]}
        st = plug.sanitize_sessions(raw)
        assert len(st["sessions"][0]["messages"][0]["content"]) \
            == plug.SESSION_MSG_CHARS

    def test_session_and_msg_limits(self, plug):
        raw = {"sessions": [
            {"id": f"s{i}", "updated_at": f"2026-01-{i:02d}T00:00:00",
             "messages": [{"role": "user", "content": "m"}]}
            for i in range(plug.SESSION_LIMIT + 5)]}
        st = plug.sanitize_sessions(raw)
        assert len(st["sessions"]) == plug.SESSION_LIMIT
        # 最近活跃的幸存（updated_at 降序裁剪）
        assert st["sessions"][0]["id"] == f"s{plug.SESSION_LIMIT + 4}"

        msgs = [{"role": "user" if j % 2 == 0 else "assistant",
                 "content": str(j)}
                for j in range(plug.SESSION_MSG_LIMIT + 9)]
        st2 = plug.sanitize_sessions(
            {"sessions": [{"id": "a", "messages": msgs}]})
        kept = st2["sessions"][0]["messages"]
        assert len(kept) == plug.SESSION_MSG_LIMIT
        # 裁剪后保持成对
        assert len(kept) % 2 == 0

    def test_current_id_falls_back(self, plug):
        raw = {"current_id": "ghost",
               "sessions": [{"id": "real",
                             "messages": [{"role": "user", "content": "x"}]}]}
        st = plug.sanitize_sessions(raw)
        assert st["current_id"] == "real"


class TestStoreIO:
    def test_save_load_roundtrip(self, plug):
        ctx = _make_ctx()
        store = plug.sanitize_sessions(None)
        s = plug._fresh_session()
        s["title"] = "测试会话"
        s["messages"] = [{"role": "user", "content": "q", "display": "Q"}]
        store["sessions"] = [s]
        store["current_id"] = s["id"]
        assert plug.save_sessions(ctx, store) is True
        loaded = plug.load_sessions(ctx)
        assert loaded["current_id"] == s["id"]
        assert loaded["sessions"][0]["title"] == "测试会话"

    def test_corrupt_file_degrades_and_kept(self, plug):
        ctx = _make_ctx()
        path = os.path.join(ctx.data_dir, plug.SESSIONS_FILE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        st = plug.load_sessions(ctx)
        assert st["sessions"] == []
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "{not json"     # 原文件保留供抢救

    def test_title_from_first_line(self, plug):
        assert plug.session_title_from("第一行\n第二行") == "第一行"
        assert plug.session_title_from("  \n  尾行") == "尾行"
        assert plug.session_title_from("") == "新会话"
        assert len(plug.session_title_from("长" * 40)) == 24


# ---------------- 页面级：落盘 / 恢复 / 多会话 UI ----------------

@pytest.fixture()
def page_factory(qapp, plug):
    made = []

    def make(data_dir=None):
        ctx = _make_ctx(data_dir)
        w = plug.AiAssistantPlugin().create_page(ctx)
        made.append(w)
        return w

    yield make
    for w in made:
        w.deleteLater()


class TestPageSessions:
    def test_fresh_page_has_one_session_and_welcome(self, page_factory):
        page = page_factory()
        assert page._session_combo.count() == 1
        assert page._session_combo.currentText() == "新会话"
        # 欢迎气泡在消息流里（流里只有 1 行气泡 + stretch）
        assert page._stream.count() == 2

    def test_persist_turn_writes_and_titles(self, page_factory, plug):
        page = page_factory()
        page._persist_turn("完整内容（含数据块）", "总结任务", "好的，摘要如下")
        path = os.path.join(page._ctx.data_dir, plug.SESSIONS_FILE)
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        msgs = data["sessions"][0]["messages"]
        assert len(msgs) == 2
        assert msgs[0]["display"] == "总结任务"
        # 标题从首条用户显示文案取
        assert data["sessions"][0]["title"] == "总结任务"
        # 下拉框同步刷新
        assert page._session_combo.currentText() == "总结任务"

    def test_restart_restores_bubbles_and_history(self, page_factory, plug):
        ctx = _make_ctx()
        p1 = page_factory(ctx.data_dir)
        p1._persist_turn("q1-content", "问一", "答一")
        p1._persist_turn("q2-content", "问二", "答二")
        p1.deleteLater()
        p2 = page_factory(ctx.data_dir)
        # 气泡恢复：2 轮 = 4 张气泡 + 欢迎（有消息不再出现）→ 流 4 行 + stretch
        assert p2._stream.count() == 5
        assert p2._history[-1] == {"role": "assistant", "content": "答二"}
        assert p2._session_combo.currentText() == "问一"  # 首条用户文案作标题
        # 模型上下文窗口封顶 MAX_HISTORY
        assert len(p2._history) <= plug.MAX_HISTORY

    def test_new_and_switch_session(self, page_factory):
        page = page_factory()
        page._persist_turn("q", "第一条", "a1")
        page._new_session()
        assert page._session_combo.count() == 2
        assert page._session["title"] == "新会话"
        assert page._stream.count() == 2          # 空会话 → 欢迎气泡
        # 切回第一个会话（按 data 找索引）
        idx = page._session_combo.findData(page._store["sessions"][1]["id"])
        page._session_combo.setCurrentIndex(idx)
        assert page._session["title"] == "第一条"
        assert page._stream.count() == 3          # 1 轮 = 2 气泡 + stretch
        assert page._history[-1]["content"] == "a1"

    def test_switch_blocked_while_busy(self, page_factory):
        page = page_factory()
        page._persist_turn("q", "有会话", "a")
        page._new_session()
        page._busy = True
        idx = page._session_combo.findData(page._store["sessions"][1]["id"])
        page._session_combo.setCurrentIndex(idx)
        # 在途请求期间：指针不切、状态行提示
        assert page._session["title"] == "新会话"

    def test_delete_session(self, page_factory):
        page = page_factory()
        page._persist_turn("q", "甲", "a")
        page._new_session()
        page._persist_turn("q2", "乙", "b")
        page._delete_session()
        assert page._session_combo.count() == 1
        assert page._session["title"] == "甲"      # 自动切到幸存会话
        # 删到只剩一条：清空内容而非删除
        page._delete_session()
        assert page._session_combo.count() == 1
        assert page._session["messages"] == []
        assert page._stream.count() == 2           # 欢迎气泡回来了

    def test_clear_chat_keeps_session(self, page_factory, plug):
        page = page_factory()
        page._persist_turn("q", "会话甲", "a")
        page._clear_chat()
        assert page._session["messages"] == []
        assert page._session_combo.count() == 1    # 会话本体还在
        with open(os.path.join(page._ctx.data_dir, plug.SESSIONS_FILE),
                  encoding="utf-8") as f:
            assert json.load(f)["sessions"][0]["messages"] == []
