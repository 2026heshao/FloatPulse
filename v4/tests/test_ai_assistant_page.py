# -*- coding: utf-8 -*-
"""AI 助手页面（AiChatPage）构建回归测试。

背景（2026-09-29）：AI 总配置迁移删除插件私有设置卡时，`add_bubble`
被连带误删——12 处调用悬空，`AiChatPage.__init__` 首条欢迎气泡即抛
AttributeError，宿主 `_register_plugin_pages` 捕获后「页面注入失败，
跳过」，**侧栏 AI 助手入口静默消失**（只有日志一行，无 UI 提示）。

本文件钉住「页面能离屏构建」这条底线：create_page 成功返回 QWidget、
欢迎气泡在消息流里、快捷指令按钮与规则库行都在。任何再来的缺方法 /
缺控件回归都会在这里以同等方式爆炸，而不是在用户侧丢入口。
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
_MOD_NAME = "fp_test_ai_assistant_page_plugin"


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def plug():
    """按插件加载器的命名方式加载真实 plugin.py（同 test_ai_assistant_actions）"""
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeAI:
    """ctx.ai 门面替身：覆盖 AiChatPage 构建期触到的面"""

    def __init__(self):
        self._listeners = []

    def add_listener(self, cb):
        self._listeners.append(cb)          # 不回放：构建期免状态机依赖

    def remove_listener(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def is_attached(self, plugin_id=None):
        return False

    def params(self, plugin_id=None):
        return {"mode": "cloud", "base_url": "", "model": ""}

    def stop_local(self):
        pass


def _make_ctx():
    return types.SimpleNamespace(
        plugin_id="ai-assistant",
        data_dir=tempfile.mkdtemp(prefix="fp_ai_page_test_"),
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


@pytest.fixture(scope="module")
def page(qapp, plug):
    widget = plug.AiAssistantPlugin().create_page(_make_ctx())
    yield widget
    widget.deleteLater()


class TestPageBuilds:
    def test_create_page_returns_widget(self, page):
        from PyQt6.QtWidgets import QWidget
        assert isinstance(page, QWidget)

    def test_add_bubble_exists(self, page):
        """缺 add_bubble = 页面注入失败 = 侧栏入口消失（本文件的存在理由）"""
        assert callable(getattr(page, "add_bubble", None))

    def test_welcome_bubble_in_stream(self, page):
        """欢迎气泡已进消息流（__init__ 首个 add_bubble 调用存活）"""
        from PyQt6.QtWidgets import QLabel
        texts = [w.text() for w in page.findChildren(QLabel)]
        assert any("快捷指令" in t for t in texts), texts[:5]

    def test_quick_command_buttons_built(self, page, plug):
        """每个快捷指令都有对应按钮（构建期局部 quick_row 的产物）"""
        from PyQt6.QtWidgets import QPushButton
        btn_texts = {b.text() for b in page.findChildren(QPushButton)}
        for text, _kind, _prompt in plug.QUICK_COMMANDS:
            assert text in btn_texts, f"缺快捷指令按钮：{text}"

    def test_rules_container_alive(self, page):
        """规则库行容器可用（ctx.data_dir 读写路径走通）"""
        assert isinstance(page._rules_rows, list)
        assert page._rules_form is not None
