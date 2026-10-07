# -*- coding: utf-8 -*-
"""AI 助手会话导出（A2）+ 自定义快捷指令（A3，v1.10.0）回归测试。

钉住四条底线：
1. session_to_markdown：display 优先（用户侧界面文案）、空消息跳过、
   末尾单换行，纯函数不碰 UI；
2. safe_export_name：Windows 非法字符替换、空标题回退、截断；
3. sanitize_custom_quick：脏数据宽容降级、截断、去重、上限 6；
4. UI 行为：导出按钮存在、空会话拦截、选路径后真实落盘、
   自定义指令添加/直发/删除与「＋」按钮常驻。
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
_MOD_NAME = "fp_test_ai_assistant_export_plugin"

# 页面存活桩（2026-10-07）：teardown 已停净动画/定时器，但**不能**让 GC 在
# 会话中段析构页面 C++ 树（实测两次全量同点位原生崩溃；整页同步 delete
# 也 fail-fast）—— 持引用到进程退出，随 QApplication 一并回收。
_KEEP_ALIVE = []


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
        data_dir=data_dir or tempfile.mkdtemp(prefix="fp_ai_export_test_"),
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


@pytest.fixture()
def page(qapp, plug):
    from fp_test_ai_assistant_export_plugin import AiChatPage
    w = AiChatPage(_make_ctx())
    w.show()
    yield w
    # 确定性清场（2026-10-07 全量二分定位）：页面此前靠 GC 兜底回收，其上
    # 运行中的按压 QVariantAnimation（按钮反馈 U 批起按钮自带）在 C++ 析构
    # 时绕过 QUnifiedTimer 正常注销 → 动画定时器簿记失配（认为在跑、实际
    # 已停）→ 同会话后续所有 QAbstractAnimation 冻结（currentTime 恒 0；
    # asset_thumbs 淡入 / help_nav / v5 滑块 / v9 cp / v2 滚动条 / v7 日历 /
    # v10 Stepper / splash / opacity 十余例全量确定性失败的共同根因）。
    # 拆除时显式 stop 全部动画即可保住簿记一致（整页同步 delete 实测会在
    # 复杂子树上 fail-fast，故不强制删对象，泄漏面仅为测试内存）。
    from PyQt6.QtCore import QAbstractAnimation, QEvent, QTimer
    from PyQt6.QtWidgets import QApplication
    for anim in w.findChildren(QAbstractAnimation):
        anim.stop()
    for dots in w.findChildren(QTimer):
        dots.stop()
    w.close()
    # rebuild 等 deleteLater 的旧子控件也清干净（pending 删除悬到会话末
    # 会在 GC 兜底析构时打乱 QUnifiedTimer 簿记 —— 实测冻结动画驱动）
    QApplication.sendPostedEvents(w, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    _KEEP_ALIVE.append(w)


# ---------------- A2：session_to_markdown / safe_export_name ----------------

class TestMarkdown:
    def test_empty_session(self, plug):
        md = plug.session_to_markdown({"title": "测试", "messages": []})
        assert md.startswith("# AI 助手会话：测试")
        assert "消息 0 条" in md
        assert md.endswith("\n") and not md.endswith("\n\n")

    def test_user_prefers_display(self, plug):
        sess = {"title": "T", "messages": [
            {"role": "user", "content": "q\n\nDATA", "display": "总结任务"}]}
        md = plug.session_to_markdown(sess)
        assert "**你：**" in md
        assert "总结任务" in md
        assert "DATA" not in md          # 数据块不进导出

    def test_ai_uses_content(self, plug):
        sess = {"title": "T", "messages": [
            {"role": "assistant", "content": "你好", "display": "你好"}]}
        md = plug.session_to_markdown(sess)
        assert "**AI：**" in md and "你好" in md

    def test_empty_messages_skipped(self, plug):
        sess = {"title": "T", "messages": [
            {"role": "user", "content": "a", "display": "a"},
            {"role": "assistant", "content": "   "},
            "junk"]}
        md = plug.session_to_markdown(sess)
        assert "消息 1 条" in md

    def test_none_and_missing(self, plug):
        md = plug.session_to_markdown(None)
        assert "# AI 助手会话：会话" in md


class TestSafeName:
    def test_bad_chars_replaced(self, plug):
        assert plug.safe_export_name('a/b\\c:d*e?f"g<h>i|j') == \
            "a_b_c_d_e_f_g_h_i_j"

    def test_empty_fallback(self, plug):
        assert plug.safe_export_name("") == "会话"
        assert plug.safe_export_name("///") == "会话"

    def test_truncate_and_strip_dots(self, plug):
        assert plug.safe_export_name("a" * 50) == "a" * 40
        assert plug.safe_export_name("..标题..") == "标题"


# ---------------- A3：sanitize_custom_quick ----------------

class TestSanitizeQuick:
    def test_non_list(self, plug):
        assert plug.sanitize_custom_quick(None) == []
        assert plug.sanitize_custom_quick("x") == []
        assert plug.sanitize_custom_quick(42) == []

    def test_strip_truncate_dedupe(self, plug):
        out = plug.sanitize_custom_quick(
            ["  a  ", "a", "", None, "b" * 300, "c"])
        assert out == ["a", "b" * 200, "c"]

    def test_cap_six(self, plug):
        out = plug.sanitize_custom_quick([str(i) for i in range(10)])
        assert len(out) == 6 and out == [str(i) for i in range(6)]


# ---------------- UI：导出按钮 / 落盘 / 自定义指令 ----------------

def test_export_button_exists(page):
    assert page._session_export_btn is not None
    assert page._session_export_btn.parent() is page


def test_export_empty_session_blocked(page):
    page._session["messages"] = []
    page._export_session()
    assert "还没有可导出" in page._status.text()


def test_export_writes_markdown(page, plug, monkeypatch, tmp_path):
    page._session["messages"] = [
        {"role": "user", "content": "q", "display": "问"},
        {"role": "assistant", "content": "答", "display": "答"}]
    page._session["title"] = "周会"
    out = tmp_path / "out.md"
    monkeypatch.setattr(plug.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    page._export_session()
    text = out.read_text(encoding="utf-8")
    assert "# AI 助手会话：周会" in text
    assert "**你：**" in text and "**AI：**" in text
    assert "已导出" in page._status.text()


def test_export_cancel_is_silent(page, plug, monkeypatch):
    page._session["messages"] = [
        {"role": "user", "content": "q", "display": "q"}]
    monkeypatch.setattr(plug.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    page._export_session()
    assert "已导出" not in page._status.text()


def test_custom_quick_initial_plus_only(page):
    btns = page._custom_quick_host.layout().count()
    assert btns == 1                    # 只有「＋」


def test_custom_quick_send(page, monkeypatch):
    seen = {}

    def fake_dispatch(display, data_block, prompt):
        seen.update(display=display, data_block=data_block, prompt=prompt)

    monkeypatch.setattr(page, "_dispatch", fake_dispatch)
    page._cfg["custom_quick"] = ["改写这段话"]
    page._rebuild_custom_quick()
    btn = page._custom_quick_host.layout().itemAt(0).widget()
    btn.click()
    assert seen == {"display": "改写这段话", "data_block": None,
                    "prompt": "改写这段话"}


def test_custom_quick_send_busy_blocked(page, monkeypatch):
    called = []
    monkeypatch.setattr(page, "_dispatch",
                        lambda *a, **k: called.append(a))
    page._cfg["custom_quick"] = ["x"]
    page._rebuild_custom_quick()
    page._busy = True
    page._send_custom_quick("x")
    assert called == []
    assert "正在处理" in page._status.text()


def test_custom_quick_remove(page, plug):
    page._cfg["custom_quick"] = ["甲", "乙"]
    page._rebuild_custom_quick()
    page._remove_custom_quick("甲")
    assert page._cfg["custom_quick"] == ["乙"]
    # 落盘校验
    cfg_path = os.path.join(page._ctx.data_dir, plug.CONFIG_FILE)
    assert json.load(open(cfg_path, encoding="utf-8"))["custom_quick"] == ["乙"]
    # 按钮重建：1 条自定义 + 1 个「＋」
    assert page._custom_quick_host.layout().count() == 2


def test_add_custom_quick_via_dialog(page, plug, monkeypatch):
    dlg_calls = []

    class FakeDlg:
        def __init__(self, ctx, parent=None):
            dlg_calls.append(ctx)

        @staticmethod
        def exec():
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def text(self):
            return "  帮我润色  "

    monkeypatch.setattr(plug, "QuickAddDialog", FakeDlg)
    page._add_custom_quick()
    assert page._cfg["custom_quick"] == ["帮我润色"]
    assert len(dlg_calls) == 1
    assert page._custom_quick_host.layout().count() == 2


def test_add_custom_quick_cap(page, monkeypatch):
    page._cfg["custom_quick"] = [f"p{i}" for i in range(6)]
    page._rebuild_custom_quick()
    page._add_custom_quick()            # 不弹对话框即拦截
    assert len(page._cfg["custom_quick"]) == 6
    assert "上限" in page._status.text()


def test_quick_add_dialog(qapp, plug):
    dlg = plug.QuickAddDialog(None)
    dlg.edit.setText("  测试指令  ")
    assert dlg.text() == "测试指令"
    dlg.edit.setText("   ")
    assert dlg.text() == ""
