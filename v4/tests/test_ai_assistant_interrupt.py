# -*- coding: utf-8 -*-
"""AI 助手 · 中断在途回答（2026-10-06）回归。

钉住五条底线：
1. 忙时发送键变「停止」且保持可点，回复到达后复原为「发送」；
2. 中断立刻复位：思考气泡拆掉、_busy 回落、状态行 + 提示气泡就位；
3. 中断后迟到的回复被代数令牌静默丢弃：不渲染、不进历史、不落存档；
4. 忙时回车/点击（框里已输入下一问）= 中断 + 追问一次完成；
5. 清空会话同样作废在途请求（迟到回复不再渲染进已清空的消息流）。
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
_MOD_NAME = "fp_test_ai_interrupt_plugin"


@pytest.fixture(scope="module")
def qapp():
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    yield _APP


_APP = None

# 页面存活桩（2026-10-07，与 test_ai_assistant_export.py 同款）：teardown
# 已停净动画/定时器，但不能让 GC 在会话中段析构页面 C++ 树（实测单跑
# 第 2 项起 fail-fast 0xC0000409；整页同步 delete 同样不可）——持引用到
# 进程退出，随 QApplication 一并回收。
_KEEP_ALIVE = []


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


def _make_ctx():
    return types.SimpleNamespace(
        plugin_id="ai-assistant",
        data_dir=tempfile.mkdtemp(prefix="fp_ai_interrupt_test_"),
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


def _attach(page, captured):
    """让页面「已接入 AI 总配置」并把在途回调捕获进 captured"""
    page._ctx.ai.is_attached = lambda plugin_id=None: True
    page._ctx.ai.params = lambda plugin_id=None: {
        "mode": "cloud", "base_url": "https://api.x.com/v1",
        "api_key": "sk", "model": "m"}
    page._ctx.http_post_json_async = (
        lambda url, headers, body, timeout=0.0, on_done=None:
        captured.append(on_done) or True)


def _reply(text="回复"):
    return {"ok": True, "status": 200,
            "body": json.dumps({"choices": [{"message": {"content": text}}]})}


@pytest.fixture()
def page(qapp, plug):
    from fp_test_ai_interrupt_plugin import AiChatPage
    w = AiChatPage(_make_ctx())
    w.show()
    yield w
    # 拆除三件套（2026-10-07，与 test_ai_assistant_export.py 同款）：
    # ① 先 stop 全部动画/计时器——带运行中动画的窗口被析构会打乱
    #    QUnifiedTimer 簿记（冻结/崩源）；
    # ② 定向派发本页 DeferredDelete——全局 None 派发会把全会话 pending
    #    删除一锅端（已被否的写法）；
    # ③ _KEEP_ALIVE 持引用防 GC 会话中段析构。
    from PyQt6.QtCore import QAbstractAnimation, QEvent, QTimer
    from PyQt6.QtWidgets import QApplication
    for anim in w.findChildren(QAbstractAnimation):
        anim.stop()
    for t in w.findChildren(QTimer):
        t.stop()
    w.close()
    # 页面本体不删（整页同步强制删在复杂子树上 fail-fast，实测单跑即崩）；
    # 这里只冲本页 rebuild 等遗留的 pending deleteLater。泄漏面=测试内存。
    QApplication.sendPostedEvents(w, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    _KEEP_ALIVE.append(w)


# ---------------- 发送⇄停止一体键 ----------------

def test_send_button_morphs_to_stop_while_busy(page):
    captured = []
    _attach(page, captured)
    page._input.setPlainText("你好")
    page._on_send_clicked()
    assert page._busy
    assert page._send_btn.text() == "停止"
    assert page._send_btn.isEnabled()          # 忙时也可点 = 中断入口
    captured.pop()(_reply())
    assert not page._busy
    assert page._send_btn.text() == "发送"      # 回复到达：复原


def test_stop_click_resets_ui_and_adds_hint(page):
    captured = []
    _attach(page, captured)
    page._input.setPlainText("你好")
    n0 = page._stream.count()
    page._on_send_clicked()
    assert page._stream.count() == n0 + 2      # 你气泡 + 思考气泡
    page._send_btn.click()                     # 忙时点发送键 = 中断
    assert not page._busy
    assert page._think_bubble is None
    assert page._think_dots is None
    assert page._send_btn.text() == "发送"
    assert page._status.text() == "已中断本次回答"
    # 思考气泡拆掉、提示气泡顶上 → 恢复到 n0 + 你气泡 + 提示气泡
    assert page._stream.count() == n0 + 2
    assert page._input.toPlainText() == ""     # 空框中断：不误发新消息


# ---------------- 代际令牌：迟到回复静默丢弃 ----------------

def test_late_reply_after_interrupt_is_discarded(page):
    captured = []
    _attach(page, captured)
    page._input.setPlainText("你好")
    page._on_send_clicked()
    on_done = captured.pop()
    n = page._stream.count()
    page._interrupt()
    on_done(_reply("迟到答"))                   # 真实请求后台自然结束
    assert page._stream.count() == n           # 不渲染
    assert page._history == []                 # 不进历史
    assert page._session["messages"] == []     # 不落会话存档
    assert not page._busy
    assert page._send_btn.text() == "发送"


def test_stale_reply_does_not_clash_with_new_turn(page):
    captured = []
    _attach(page, captured)
    page._input.setPlainText("第一问")
    page._on_send_clicked()
    stale = captured.pop()
    page._input.setPlainText("第二问")
    page._on_send_clicked()                    # 忙时回车 = 中断 + 追问
    stale(_reply("迟到答"))                     # 旧回复晚到：不渲染
    assert page._history == []
    assert page._busy                          # 新一轮仍在途
    assert page._input.toPlainText() == ""     # 第二问已被发出（框已清空）
    captured.pop()(_reply("第二答"))
    assert [m["content"] for m in page._history] == ["第二问", "第二答"]
    assert page._session["messages"][-1]["content"] == "第二答"


# ---------------- 清空会话连带作废在途 ----------------

def test_clear_chat_cancels_inflight(page):
    captured = []
    _attach(page, captured)
    page._input.setPlainText("你好")
    page._on_send_clicked()
    on_done = captured.pop()
    page._clear_chat()
    assert not page._busy
    assert page._send_btn.text() == "发送"
    n = page._stream.count()
    on_done(_reply())                          # 迟到回复：不得进已清空的流
    assert page._stream.count() == n
    assert page._history == []
