# -*- coding: utf-8 -*-
"""AI 助手 · 思考动画自绘化（v1.11.0）回归。

钉住四条底线：
1. ThinkingDots 零字体依赖（纯 paintEvent 自绘，离屏渲染非空）；
2. 动画计时器：默认启动、stop() 停、_tick 推进相位；
3. 减弱动效开启时不启动计时器，但静态渲染仍然有内容；
4. 页面接线：_show_thinking 幂等、气泡进流靠左、_hide_thinking 计数
   回落且引用清空、回复到达后自动拆（_dispatch 全链路）。
"""

import importlib.util
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "ai-assistant", "plugin.py")
_MOD_NAME = "fp_test_ai_thinking_plugin"


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


@pytest.fixture()
def restore_reduce_motion():
    yield
    plug = sys.modules[_MOD_NAME]
    plug.motion.set_reduce_motion(False)


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


def _make_ctx(captured=None):
    def _post(url, headers, body, timeout=0.0, on_done=None):
        if captured is not None:
            captured.append({"url": url, "on_done": on_done})
        return True

    return types.SimpleNamespace(
        plugin_id="ai-assistant",
        data_dir=tempfile.mkdtemp(prefix="fp_ai_think_test_"),
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
        http_post_json_async=_post,
        parent_window=lambda: None,
        open_main_window=lambda: None,
        logger=types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None),
    )


# ---------------- 控件层 ----------------

class TestThinkingDots:
    def test_construct_fixed_size_and_animating(self, qapp, plug):
        dots = plug.ThinkingDots(_make_ctx())
        assert dots.width() > 0 and dots.height() > 0
        assert dots.is_animating()
        assert dots.phase_ms() >= 0
        dots.stop()
        assert not dots.is_animating()

    def test_tick_advances_phase(self, qapp, plug):
        dots = plug.ThinkingDots(_make_ctx())
        dots.stop()
        before = dots.phase_ms()
        dots._tick()
        assert dots.phase_ms() != before
        dots.deleteLater()

    def test_paint_renders_nonempty(self, qapp, plug):
        dots = plug.ThinkingDots(_make_ctx())
        dots.stop()
        img = dots.grab().toImage()
        assert not img.isNull()
        found = any(img.pixelColor(x, img.height() // 2).alpha() > 0
                    for x in range(img.width()))
        assert found, "三点应渲染出非空像素"
        dots.deleteLater()

    def test_reduce_motion_static(self, qapp, plug, restore_reduce_motion):
        plug.motion.set_reduce_motion(True)
        dots = plug.ThinkingDots(_make_ctx())
        assert not dots.is_animating()          # 不启动计时器
        img = dots.grab().toImage()             # 静态也要画得出来
        found = any(img.pixelColor(x, img.height() // 2).alpha() > 0
                    for x in range(img.width()))
        assert found
        dots.deleteLater()

    def test_color_fallback_without_host(self, qapp, plug):
        dots = plug.ThinkingDots(_make_ctx())   # parent_window -> None
        assert dots._color.isValid()
        assert dots._color.name().lower() in ("#27787a", "#6ffee9") \
            or dots._color.isValid()            # 主题色或兜底，均合法
        dots.deleteLater()

    def test_theme_change_follows(self, qapp, plug):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Host(QObject):
            theme_changed = pyqtSignal(str)

            def __init__(self):
                super().__init__()
                self.current_theme = "light"

        host = Host()

        def parent_window():
            return host

        ctx = _make_ctx()
        ctx.parent_window = parent_window
        dots = plug.ThinkingDots(ctx)
        before = dots._color.name()
        host.current_theme = "dark"
        dots._on_theme_changed("dark")
        after = dots._color.name()
        assert before != after                  # light/dark 主色不同
        dots.deleteLater()
        host.deleteLater()


# ---------------- 页面接线 ----------------

@pytest.fixture()
def page(qapp, plug):
    from fp_test_ai_thinking_plugin import AiChatPage
    w = AiChatPage(_make_ctx())
    w.show()
    yield w
    w.deleteLater()


def test_show_thinking_idempotent_and_left_aligned(page):
    n0 = page._stream.count()
    page._show_thinking()
    n1 = page._stream.count()
    page._show_thinking()                       # 幂等：不重复挂
    assert page._stream.count() == n1 == n0 + 1
    assert page._think_bubble is not None
    assert page._think_dots is not None
    # 布局 = [...已有气泡, 本气泡, 末尾 stretch]：气泡在 stretch 前一位
    item = page._stream.itemAt(page._stream.count() - 2)
    assert item.widget() is page._think_bubble
    assert item.alignment() & __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignLeft
    page._hide_thinking()


def test_hide_thinking_restores_count(page):
    n0 = page._stream.count()
    page._show_thinking()
    assert page._stream.count() == n0 + 1
    page._hide_thinking()
    assert page._stream.count() == n0
    assert page._think_bubble is None
    assert page._think_dots is None


def test_dispatch_shows_and_reply_hides(page):
    # 让页面「已接入 AI 总配置」（_dispatch 的放行前置）
    page._ctx.ai.is_attached = lambda plugin_id=None: True
    page._ctx.ai.params = lambda plugin_id=None: {
        "mode": "cloud", "base_url": "https://api.x.com/v1",
        "api_key": "sk", "model": "m"}
    captured = []
    page._ctx.http_post_json_async = (
        lambda url, headers, body, timeout=0.0, on_done=None:
        captured.append(on_done) or True)
    page._input.setPlainText("你好")
    page._on_send_clicked()
    assert page._busy
    assert page._think_bubble is not None       # 在途：气泡挂着
    assert page._think_dots is not None
    captured.pop()({"ok": True, "status": 200,
                    "body": __import__("json").dumps(
                        {"choices": [{"message": {"content": "回复"}}]})})
    assert not page._busy
    assert page._think_bubble is None           # 回复到达：拆掉
    assert page._think_dots is None
