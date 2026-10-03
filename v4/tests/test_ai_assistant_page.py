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


# ====================================================================
# 贴底跟随（2026-10-03 用户要求「自动定位到最新消息 / AI 即将输出的位置」）
# ====================================================================
# 背景（实测数据）：改之前 `_scroll_to_bottom` 只有一句
# ``QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))`` —— widgetResizable
# 的 QScrollArea 要等一次布局传递才算出新的 maximum，singleShot 当下读到的是
# **上一轮**的旧值，于是每次都停在「离底一张气泡」的位置（实测差 44~46px，
# 连加 12 条后累计差 168px），最新一条总有一截在视口外。
#
# 修法两条腿：① `rangeChanged` 补钉（布局算完那一刻才拿得到真 maximum）；
# ② 贴底粘性 —— 用户主动往上翻历史时不抢滚动条，发送 / 切会话则无条件回最新。
_LONG = "这是一条用于把消息流撑过一屏的较长测试文本。" * 6


@pytest.fixture()
def chat(plug):
    """每用例一张**已 show 过**的新页面。

    两个必须点：① 滚动状态是实例状态，用例间必须隔离；② 不 show 的话
    视口高为 0、maximum 恒 0，"差=0" 的断言会退化成恒真（假护栏）——
    下面每个用例都先断言真的溢出了，再断言落点。
    """
    from PyQt6.QtWidgets import QApplication
    widget = plug.AiAssistantPlugin().create_page(_make_ctx())
    widget.resize(900, 700)
    widget.show()
    for _ in range(12):
        QApplication.processEvents()
    yield widget
    widget.hide()
    widget.deleteLater()


def _pump(ms=80):
    import time
    from PyQt6.QtWidgets import QApplication
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _fill_bubbles(widget):
    """灌 5 组长气泡把消息流撑过一屏（浮按钮测试的前提是真溢出）"""
    for i in range(5):
        widget.add_bubble("你", "提问 %d" % i)
        widget.add_bubble("AI", "回复 %d %s" % (i, _LONG))
    _pump(120)


def _overflowed(widget):
    """前置条件：消息流确实高出视口（否则下面的落点断言毫无意义）"""
    bar = widget._scroll.verticalScrollBar()
    return bar.maximum() > 0 and widget._scroll.viewport().height() > 0


class TestStickToBottom:
    def test_new_messages_land_exactly_at_bottom(self, chat):
        """每加一条都要精确落到底（旧实现差一张气泡 ≈44px）"""
        for i in range(8):
            chat.add_bubble("你", "第 %d 条提问" % i)
            chat.add_bubble("AI", "第 %d 条回复 %s" % (i, _LONG))
            _pump(40)
        _pump(120)
        assert _overflowed(chat), "消息流没溢出视口，断言会退化成恒真"
        bar = chat._scroll.verticalScrollBar()
        assert bar.value() == bar.maximum(), (
            "最新消息没落到底：差 %d px" % (bar.maximum() - bar.value()))

    def test_thinking_bubble_and_reply_land_at_bottom(self, chat):
        """「AI 即将输出的位置」：思考气泡与最终回复都要贴底可见"""
        for i in range(5):
            chat.add_bubble("你", "提问 %d" % i)
            chat.add_bubble("AI", "回复 %d %s" % (i, _LONG))
        _pump(120)
        chat._show_thinking()
        _pump(120)
        bar = chat._scroll.verticalScrollBar()
        assert bar.value() == bar.maximum(), "思考气泡没落到底"
        chat._hide_thinking()
        chat.add_bubble("AI", "AI 的完整回复\n" + _LONG)
        _pump(120)
        assert bar.value() == bar.maximum(), "AI 回复没落到底"

    def test_scrolling_up_stops_the_auto_follow(self, chat):
        """用户往上翻历史时，新消息不许把视角拽走"""
        for i in range(5):
            chat.add_bubble("你", "提问 %d" % i)
            chat.add_bubble("AI", "回复 %d %s" % (i, _LONG))
        _pump(120)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(0)
        _pump(60)
        assert chat._stick_bottom is False, "滚到顶部后应解除贴底"
        chat.add_bubble("AI", "用户在看历史时又来了一条")
        _pump(120)
        assert bar.value() == 0, "用户在翻历史，视角被强行拽到了 %d" % bar.value()

    def test_scrolling_back_to_bottom_resumes_follow(self, chat):
        """用户自己滚回底部 → 恢复跟随"""
        for i in range(5):
            chat.add_bubble("你", "提问 %d" % i)
            chat.add_bubble("AI", "回复 %d %s" % (i, _LONG))
        _pump(120)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(0)
        _pump(60)
        bar.setValue(bar.maximum())
        _pump(60)
        assert chat._stick_bottom is True, "滚回底部应恢复贴底"
        chat.add_bubble("AI", "恢复跟随后的一条")
        _pump(120)
        assert bar.value() == bar.maximum(), "恢复跟随后没落到底"

    def test_dispatch_forces_view_to_latest(self, chat):
        """手动发送 = 明确要看这一轮结果，哪怕此前在翻历史也要回最新"""
        for i in range(5):
            chat.add_bubble("你", "提问 %d" % i)
            chat.add_bubble("AI", "回复 %d %s" % (i, _LONG))
        _pump(120)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(0)
        _pump(60)
        chat._input.setPlainText("测试一下发送定位")
        chat._on_send_clicked()
        _pump(120)
        assert bar.value() == bar.maximum(), (
            "发送后没回到最新：差 %d px" % (bar.maximum() - bar.value()))

    def test_rebuild_pins_to_bottom(self, chat):
        """切会话 / 新建 / 清空后无条件落到最新"""
        for i in range(5):
            chat.add_bubble("你", "提问 %d" % i)
            chat.add_bubble("AI", "回复 %d %s" % (i, _LONG))
        _pump(120)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(0)
        _pump(60)
        chat._rebuild_stream_from_session()
        _pump(120)
        assert bar.value() == bar.maximum(), "重建会话后没落到底"


# ====================================================================
# 「↓ 回到最新」浮按钮（2026-10-03 P2）
# ====================================================================
# 契约：可见性必须**由 _stick_bottom 推导**（贴底隐藏、离底浮现）；
# 不进 root layout（占高度会挤压消息流）；点击 = _scroll_to_bottom(force=True)。
class TestBackToLatestButton:
    def test_hidden_when_stuck_to_bottom(self, chat):
        _fill_bubbles(chat)
        assert _overflowed(chat)               # 前置：真的溢出了
        assert chat._stick_bottom is True
        assert not chat._back_btn.isVisible()

    def test_visible_after_scrolling_up(self, chat):
        _fill_bubbles(chat)
        assert _overflowed(chat)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() - 120))
        _pump()
        assert chat._stick_bottom is False
        assert chat._back_btn.isVisible()
        # 定位在滚动区右下角、16px 边距内，没被裁掉
        btn = chat._back_btn
        sc = chat._scroll
        assert btn.parent() is sc
        assert sc.width() - 60 <= btn.x() + btn.width() <= sc.width()
        assert sc.height() - 60 <= btn.y() + btn.height() <= sc.height()

    def test_click_returns_to_bottom_and_hides(self, chat):
        _fill_bubbles(chat)
        assert _overflowed(chat)
        bar = chat._scroll.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() - 120))
        _pump()
        assert chat._back_btn.isVisible()
        chat._back_btn.click()
        _pump()
        assert bar.value() == bar.maximum()
        assert chat._stick_bottom is True
        assert not chat._back_btn.isVisible()

    def test_button_not_in_root_layout(self, chat):
        _fill_bubbles(chat)
        """不进 root layout：占高度的布局成员会挤压消息流（任务书 §3.2）"""
        lay = chat.layout()
        assert chat._back_btn.parent() is chat._scroll
        in_layout = any(lay.itemAt(i).widget() is chat._back_btn
                        for i in range(lay.count()))
        assert not in_layout

    def test_visibility_derives_from_stick_state(self, chat):
        """反向验证：把贴底状态机冻结在「恒贴底」（_self_scrolling 是页面
        既有的「跳过本次滚动」机制）后滚走，按钮必须保持隐藏——实现若把
        显隐直接挂到滚动事件而不是贴底状态上，本用例变红。"""
        _fill_bubbles(chat)
        assert _overflowed(chat)
        bar = chat._scroll.verticalScrollBar()
        chat._self_scrolling = True          # 冻结状态机（模拟恒贴底）
        chat._stick_bottom = True
        try:
            bar.setValue(max(0, bar.maximum() - 120))
            _pump()
            assert chat._stick_bottom is True
            assert not chat._back_btn.isVisible()
        finally:
            chat._self_scrolling = False     # 还原，teardown 不带病退出
