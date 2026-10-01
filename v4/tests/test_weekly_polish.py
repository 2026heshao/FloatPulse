# -*- coding: utf-8 -*-
"""周报插件 v1.1.0「AI 润色」（P1）回归：纯函数 + 对话框离屏行为。

钉住：
1. build_polish_request 参数契约（与文本工坊同构）与各类拒绝路径；
2. 润色结果回填预览区、基线更新、按钮复位；
3. 覆盖保护：手动编辑过的草稿默认拦截（确认选「否」零请求）；
4. 未接入 AI / 本地未就绪 / 空草稿只引导不发请求。
"""

import importlib.util
import json
import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "weekly-report", "plugin.py")
_MOD_NAME = "fp_test_weekly_polish_plugin"


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


# ---------------- 纯函数层 ----------------

class TestBuildPolishRequest:
    def test_cloud_request_shape(self, plug):
        url, headers, body, err = plug.build_polish_request(
            {"mode": "cloud", "base_url": "https://api.x.com/v1/",
             "api_key": "sk-1", "model": "m"}, "# 周报\n- 写了 A")
        assert err == ""
        assert url == "https://api.x.com/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-1"
        assert body["model"] == "m" and body["stream"] is False
        assert body["messages"][0]["role"] == "system"
        assert "周报草稿" in body["messages"][1]["content"]

    def test_local_mode(self, plug):
        url, headers, body, err = plug.build_polish_request(
            {"mode": "local", "local_port": 8095}, "草稿")
        assert err == "" and url == "http://127.0.0.1:8095/v1/chat/completions"
        assert "Authorization" not in headers and body["model"] == "local"

    def test_rejections(self, plug):
        bad = plug.build_polish_request({"mode": "cloud", "base_url": "",
                                         "model": ""}, "草稿")
        assert bad[0] is None and "地址" in bad[3]
        bad2 = plug.build_polish_request(
            {"mode": "cloud", "base_url": "ftp://x", "model": "m"}, "草稿")
        assert bad2[0] is None
        bad3 = plug.build_polish_request({"mode": "cloud",
                                          "base_url": "http://x/v1",
                                          "model": "m"}, "   ")
        assert bad3[0] is None and "草稿为空" in bad3[3]

    def test_long_draft_capped(self, plug):
        _, _, body, err = plug.build_polish_request(
            {"mode": "cloud", "base_url": "http://x/v1", "model": "m"},
            "长" * (plug.MAX_DRAFT_CHARS + 500))
        assert err == ""
        sent = body["messages"][1]["content"]
        assert f"原始 {plug.MAX_DRAFT_CHARS + 500} 字符" in sent


class TestParseAiReply:
    def test_ok(self, plug):
        text, err = plug.parse_ai_reply(
            {"ok": True, "status": 200,
             "body": json.dumps({"choices": [{"message": {"content": "润色稿"}}]})})
        assert text == "润色稿" and err is None

    def test_errors(self, plug):
        _, e401 = plug.parse_ai_reply({"ok": False, "status": 401,
                                       "body": "", "error": "HTTP 401"})
        assert "AI 总配置" in e401
        _, ebad = plug.parse_ai_reply({"ok": True, "status": 200,
                                       "body": "not-json"})
        assert "协议" in ebad


# ---------------- 对话框行为 ----------------

class _FakeAI:
    def __init__(self, attached=True, ready=True):
        self._attached = attached
        self._ready = ready

    def is_attached(self, plugin_id=None):
        return self._attached

    def params(self, plugin_id=None):
        return {"mode": "local", "local_port": 8095,
                "local_ready": self._ready}


@pytest.fixture()
def page_factory(qapp, plug):
    made = []

    def make(captured=None, ai=None):
        data = types.SimpleNamespace(
            sources=lambda: ["tasks", "fragments", "notes"],
            tasks=lambda: [{"task_id": 1, "title": "写周报", "done": True,
                            "deadline": "", "completed_at":
                                f"{__import__('datetime').date.today().isoformat()} 10:00",
                            "focus_sessions": 2}],
            fragments=lambda: [], notes=lambda: [],
            pomodoro=lambda: {})
        ctx = types.SimpleNamespace(
            plugin_id="weekly-report",
            config={"obsidian_vault_path": ""},
            data=data,
            has_capability=lambda cap: cap in ("network", "ai"),
            ai=ai or _FakeAI(),
            logger=types.SimpleNamespace(
                warning=lambda *a, **k: None, info=lambda *a, **k: None),
            show_toast=lambda *a, **k: None,
            open_main_window=lambda: None,
            parent_window=lambda: None,
            http_post_json_async=lambda url, headers, body, timeout=0.0,
            on_done=None: _capture(captured, url, headers, body, on_done),
        )
        dlg = plug.ReportDialog(ctx)
        dlg.show()
        made.append(dlg)
        return dlg

    def _capture(captured, url, headers, body, on_done):
        if captured is not None:
            captured.append({"url": url, "body": body, "on_done": on_done})
        return True

    yield make
    for d in made:
        d.deleteLater()


def _ok_body(text):
    return {"ok": True, "status": 200,
            "body": json.dumps({"choices": [{"message": {"content": text}}]})}


class TestDialogPolish:
    def test_success_replaces_preview(self, page_factory):
        captured = []
        dlg = page_factory(captured)
        before = dlg._text.toPlainText()
        assert before.strip()
        dlg._do_polish()
        assert captured and callable(captured[0]["on_done"])
        captured[0]["on_done"](_ok_body("# 正式周报\n- 整理好了"))
        assert dlg._text.toPlainText() == "# 正式周报\n- 整理好了"
        # 基线更新 + 按钮复位
        assert dlg._last_generated == "# 正式周报\n- 整理好了"
        assert dlg._polish_btn.isEnabled()
        assert not dlg._polish_busy
        assert "润色完成" in dlg._status.text()

    def test_not_attached_guides_only(self, page_factory):
        captured = []
        dlg = page_factory(captured, ai=_FakeAI(attached=False))
        dlg._do_polish()
        assert captured == []
        assert "AI 总配置" in dlg._status.text()

    def test_local_not_ready_guides_only(self, page_factory):
        captured = []
        dlg = page_factory(captured, ai=_FakeAI(ready=False))
        dlg._do_polish()
        assert captured == []
        assert "未就绪" in dlg._status.text()

    def test_failed_reply_restores_button(self, page_factory):
        captured = []
        dlg = page_factory(captured)
        dlg._do_polish()
        captured[0]["on_done"]({"ok": False, "status": 401, "body": "",
                                "error": "HTTP 401"})
        assert dlg._polish_btn.isEnabled() and not dlg._polish_busy
        assert dlg._text.toPlainText() != ""     # 预览不被破坏

    def test_edited_draft_requires_confirm(self, page_factory, plug, monkeypatch):
        captured = []
        dlg = page_factory(captured)
        # 手动编辑 → 与基线不一致
        dlg._text.setPlainText(dlg._last_generated + "\n手改的一行")
        asks = []
        from PyQt6.QtWidgets import QMessageBox as _QB

        class _FakeBox:
            StandardButton = _QB.StandardButton
            answer = None

            @classmethod
            def question(cls, *a, **k):
                asks.append(1)
                return cls.answer

        _FakeBox.answer = _QB.StandardButton.No
        monkeypatch.setattr(plug, "QMessageBox", _FakeBox)
        dlg._do_polish()
        assert asks and captured == []           # 拦截：零请求

        _FakeBox.answer = _QB.StandardButton.Yes
        dlg._do_polish()
        assert len(captured) == 1                # 确认后放行

    def test_untouched_draft_sends_directly(self, page_factory):
        captured = []
        dlg = page_factory(captured)
        dlg._do_polish()                         # 与基线一致 → 不弹确认
        assert len(captured) == 1
