# -*- coding: utf-8 -*-
"""AI 文本工坊 · 结果历史 + 指令收藏 UI（W1/W2，v1.5.0）离屏回归。

钉住：
1. 真实请求路径（假网络桥）成功后自动落历史 + 文件落盘；
2. 历史卡展开/回填/删除/清空，转任务按钮按回填内容重新判定；
3. 收藏指令：保存/去重/填入/删除，空指令被拦。
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
                           "ai-text-workshop", "plugin.py")
_MOD_NAME = "fp_test_workshop_history_plugin"


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
    def is_attached(self, plugin_id=None):
        return True

    def params(self, plugin_id=None):
        return {"mode": "cloud", "base_url": "https://api.x.com/v1",
                "api_key": "sk", "model": "m"}


def _make_ctx(captured=None):
    def _post(url, headers, body, timeout=0.0, on_done=None):
        if captured is not None:
            captured.append({"url": url, "body": body, "on_done": on_done})
        return True

    return types.SimpleNamespace(
        plugin_id="ai-text-workshop",
        data_dir=tempfile.mkdtemp(prefix="fp_workshop_hist_"),
        has_capability=lambda cap: cap in ("network", "write", "ai"),
        ai=_FakeAI(),
        write=types.SimpleNamespace(
            add_fragment=lambda text: 1,
            add_note=lambda title, content: 1,
            add_task=lambda title, note="", deadline="": 1),
        show_toast=lambda msg, **kw: None,
        http_post_json_async=_post,
        parent_window=lambda: None,
        open_main_window=lambda: None,
        logger=types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None))


@pytest.fixture()
def page_factory(qapp, plug):
    made = []

    def make(captured=None):
        w = plug.AiWorkshopPage(_make_ctx(captured))
        w.show()                      # isVisible 断言需要祖先链可见
        made.append(w)
        return w

    yield make
    for w in made:
        w.deleteLater()


def _ok_body(text):
    return {"ok": True, "status": 200,
            "body": json.dumps({"choices": [
                {"message": {"content": text}}]})}


class TestHistoryUI:
    def test_success_reply_records_history(self, page_factory, plug):
        captured = []
        page = page_factory(captured)
        page._src_edit.setPlainText("原始素材文本")
        page._run_action("summarize", "总结它")
        assert captured and callable(captured[0]["on_done"])
        captured[0]["on_done"](_ok_body("- 要点一\n- 要点二"))
        # 历史落盘 + 内存最前
        path = os.path.join(page._ctx.data_dir, plug.HISTORY_FILE)
        assert os.path.isfile(path)
        assert page._store["history"][0]["label"] == "📌 总结要点"
        assert "要点一" in page._store["history"][0]["result"]
        assert page._store["history"][0]["source"] == "原始素材文本"
        assert page._pending_meta is None

    def test_failed_reply_records_nothing(self, page_factory):
        captured = []
        page = page_factory(captured)
        page._src_edit.setPlainText("素材")
        page._run_action("summarize", "总结它")
        captured[0]["on_done"]({"ok": False, "status": 401, "body": "",
                                "error": "HTTP 401"})
        assert page._store["history"] == []
        assert page._pending_meta is None

    def test_history_card_toggle_and_refill(self, page_factory, plug):
        page = page_factory()
        assert not page._history_card.isVisible()
        page._record_history("summarize", "📌 总结要点", "",
                             "源文摘要", "- 甲\n- 乙")
        page._toggle_history()
        assert page._history_card.isVisible()
        assert page._hist_empty.isHidden()
        assert len(page._hist_rows) == 1
        # 点击条目 → 结果区回填 + 复制可用
        page._hist_rows[0].click()
        assert "甲" in page._result_edit.toPlainText()
        assert page._copy_btn.isEnabled()
        # 非待办类历史回填 → 转任务禁用（write 已授权时）
        assert not page._tasks_btn.isEnabled()

    def test_task_history_refill_enables_tasks_btn(self, page_factory):
        page = page_factory()
        page._record_history("extract_tasks", "✅ 提取待办", "", "s",
                             "- [ ] 给客户回电话\n- [ ] 交报表")
        page._toggle_history()
        page._hist_rows[0].click()
        assert page._tasks_btn.isEnabled()

    def test_delete_and_clear_history(self, page_factory):
        page = page_factory()
        page._record_history("summarize", "L1", "", "", "结果一")
        page._record_history("polish_email", "L2", "", "", "结果二")
        page._toggle_history()
        page._delete_history(0)
        assert len(page._store["history"]) == 1
        assert page._store["history"][0]["result"] == "结果一"
        page._clear_history()
        assert page._store["history"] == []
        assert page._hist_empty.isVisible()

    def test_history_limit_prune(self, page_factory, plug):
        page = page_factory()
        for i in range(plug.HISTORY_LIMIT + 5):
            page._record_history("summarize", "L", "", "", f"结果{i}")
        assert len(page._store["history"]) == plug.HISTORY_LIMIT
        assert page._store["history"][0]["result"] == \
            f"结果{plug.HISTORY_LIMIT + 4}"


class TestPresets:
    def test_save_dedupe_and_empty_rejected(self, page_factory):
        page = page_factory()
        assert page._preset_widget.isHidden()
        page._custom_edit.setText("改成朋友圈文案")
        page._save_preset()
        assert page._preset_widget.isVisible()
        assert len(page._store["presets"]) == 1
        page._custom_edit.setText("改成朋友圈文案")
        page._save_preset()
        assert len(page._store["presets"]) == 1      # 去重
        page._custom_edit.setText("   ")
        page._save_preset()
        assert len(page._store["presets"]) == 1      # 空指令被拦

    def test_use_and_delete_preset(self, page_factory):
        page = page_factory()
        page._custom_edit.setText("把这段话翻成英文并押头韵")
        page._save_preset()
        page._custom_edit.setText("")
        pid = page._store["presets"][0]["id"]
        btn = page._preset_lay.itemAt(0).widget()
        btn.click()
        assert page._custom_edit.text() == "把这段话翻成英文并押头韵"
        page._delete_preset(pid)
        assert page._store["presets"] == []
        assert page._preset_widget.isHidden()

    def test_preset_limit_prune(self, page_factory, plug):
        page = page_factory()
        for i in range(plug.PRESET_LIMIT + 3):
            page._custom_edit.setText(f"指令{i}")
            page._save_preset()
        assert len(page._store["presets"]) == plug.PRESET_LIMIT
        assert page._store["presets"][0]["text"] == \
            f"指令{plug.PRESET_LIMIT + 2}"

    def test_restart_restores_presets_and_history(self, page_factory, plug):
        captured = []
        page = page_factory(captured)
        ctx_dir = page._ctx.data_dir
        page._src_edit.setPlainText("源文")
        page._run_action("summarize", "总结")
        captured[0]["on_done"](_ok_body("- 要点"))
        page._custom_edit.setText("常用指令")
        page._save_preset()
        page.deleteLater()
        # 同 data_dir 重建页面（等价重启）
        ctx = _make_ctx()
        ctx.data_dir = ctx_dir
        w2 = plug.AiWorkshopPage(ctx)
        try:
            assert len(w2._store["presets"]) == 1
            assert w2._store["history"][0]["result"] == "- 要点"
            w2._toggle_history()
            assert len(w2._hist_rows) == 1
        finally:
            w2.deleteLater()


# ---------------- W3：源文超长截断在处理中状态行明示（v1.5.1） ----------------

def test_run_overflow_notice_shown(page_factory):
    captured = []
    page = page_factory(captured)
    try:
        page._src_edit.setPlainText("字" * 9001)
        page._run_action("summarize", "总结")
        assert "截断" in page._status.text()
        assert "8000" in page._status.text()
        assert page._busy
    finally:
        page._set_busy(False)


def test_run_normal_no_overflow_notice(page_factory):
    captured = []
    page = page_factory(captured)
    try:
        page._src_edit.setPlainText("短文")
        page._run_action("summarize", "总结")
        assert "截断" not in page._status.text()
        assert "处理中" in page._status.text()
    finally:
        page._set_busy(False)


def test_run_overflow_request_body_still_truncated(page_factory):
    captured = []
    page = page_factory(captured)
    try:
        page._src_edit.setPlainText("字" * 9001)
        page._run_action("summarize", "总结")
        body = captured[0]["body"]
        user_msg = body["messages"][-1]["content"]
        assert "超长已截断" in user_msg
    finally:
        page._set_busy(False)
