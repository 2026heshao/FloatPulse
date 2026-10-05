# -*- coding: utf-8 -*-
"""插件 UI 基类回归 —— plugin_ui / PluginDialog。

与 test_plugin_api.py 分开的原因：后者刻意不进口 PyQt6（保证无 GUI 环境
可跑），而本文件必须构造 QWidget。本文件同样用 offscreen，不弹真窗口。

钉死的行为：
  A PluginDialog 是 GlassDialog 的子类（拿到整套玻璃视觉）
  B 构造不崩：ctx=None / ctx 无 parent_window / parent_window 抛异常
  C 取值优先级：显式 parent > ctx.parent_window()
  D 视觉铁律：无边框 + 半透明底 + GlassPanel 容器（objectName=mainWindow）
  E QSS 真的下发到容器（primaryBtn / secondaryBtn 规则在）
  F add_footer 已接好 clicked —— 再连一次会双触发（回归护栏）
  G 主题：apply_theme 能从 host 的 current_theme 取到配色
  H 官方再导出：flash_button / make_separator / 两个 label 工厂可用
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtWidgets import (                                  # noqa: E402
    QApplication, QLabel, QPushButton, QWidget,
)

from src.glass import GlassPanel                                # noqa: E402
from src.glass_dialog import GlassDialog                        # noqa: E402
from src.plugin_ui import (                                     # noqa: E402
    PluginDialog, flash_button, make_hint_label, make_separator,
    make_section_label,
)
from src.theme import get_main_window_qss                       # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeHost(QWidget):
    """最小宿主替身：只需 current_theme + _container（GlassDialog 的取法）"""

    def __init__(self, theme="light"):
        super().__init__()
        self.current_theme = theme
        self._container = GlassPanel(self, radius=14)
        self._container.setObjectName("mainWindow")
        self._container.setStyleSheet(get_main_window_qss(theme))


    @property
    def container(self):
        return self._container


# ---------------- A / D / E ----------------
def test_is_glass_dialog_subclass(qapp):
    dlg = PluginDialog(None, title="t")
    assert isinstance(dlg, GlassDialog)


def test_visual_contract(qapp):
    """无边框 + 半透明 + GlassPanel 容器（objectName 与主窗口一致）"""
    dlg = PluginDialog(None, title="t")
    assert dlg.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert isinstance(dlg._container, GlassPanel)
    assert dlg._container.objectName() == "mainWindow"


def test_qss_applied_to_container(qapp):
    """主窗口 QSS 必须真的下发，否则按钮/输入框样式全失效"""
    host = _FakeHost()
    dlg = PluginDialog(host, title="t")
    qss = dlg._container.styleSheet()
    assert qss
    assert "primaryBtn" in qss and "secondaryBtn" in qss


def test_title_bar_present(qapp):
    dlg = PluginDialog(None, title="我的面板", subtitle="副标题")
    bar = dlg._container.findChild(QWidget, "titleBar")
    assert bar is not None
    texts = [c.text() for c in bar.findChildren(QLabel)]
    assert "我的面板" in texts and "副标题" in texts


# ---------------- B / C ----------------
@pytest.mark.parametrize("ctx", [None, object()])
def test_construct_without_usable_ctx(qapp, ctx):
    """ctx 缺失/不可用都要能弹窗（无主窗口场景）"""
    dlg = PluginDialog(ctx, title="t")
    assert dlg.width() > 0 and dlg.height() > 0


def test_construct_when_parent_window_raises(qapp):
    class _Ctx:
        def parent_window(self):
            raise RuntimeError("boom")

    dlg = PluginDialog(_Ctx(), title="t")       # 不得抛
    assert dlg._container is not None


def test_construct_with_direct_host(qapp):
    """写法 3：直接传宿主窗口（无 parent_window 方法）也要取到主题。

    这条是回归护栏：早期实现只认 ctx.parent_window()，直接传宿主时会静默
    退化成默认主题（dark 配色），文档里写的"也接受直接传宿主窗口"就成了空话。
    """
    host = _FakeHost(theme="light")
    dlg = PluginDialog(host, title="t")
    assert dlg._host is host
    # light 的 glass_fill 是白色系，dark 是深色系 —— 用亮度区分
    fill = dlg._container._fill
    assert fill.red() > 128 and fill.green() > 128, \
        f"取到 dark 配色了：{fill.getRgb()}"


def test_explicit_parent_wins(qapp):
    """显式 parent 优先于 ctx.parent_window()（取主题要用 parent 的）"""
    host = _FakeHost(theme="light")

    other = _FakeHost(theme="dark")
    class _Ctx:
        def parent_window(self):
            return other

    dlg = PluginDialog(_Ctx(), title="t", parent=host)
    # 主题应来自显式 parent（light）
    assert dlg._host is host


# ---------------- F ----------------
def test_footer_clicked_wired_once(qapp):
    """add_footer 内部已 connect；外部再连一次会导致每次点击触发两次。

    这条是回归护栏：周报窗口曾因重复接线导致「另存为」弹两回文件对话框。
    """
    dlg = PluginDialog(None, title="t")
    calls = []
    btns = dlg.add_footer([("点我", "primaryBtn", lambda: calls.append(1))])
    btns[0].click()
    assert calls == [1], f"按钮被触发 {len(calls)} 次（应为 1）"


def test_footer_returns_buttons_in_order(qapp):
    dlg = PluginDialog(None, title="t")
    btns = dlg.add_footer([
        ("A", "secondaryBtn", None),
        ("B", "primaryBtn", None),
    ])
    assert [b.text() for b in btns] == ["A", "B"]
    assert [b.objectName() for b in btns] == ["secondaryBtn", "primaryBtn"]


# ---------------- G ----------------
def test_theme_follows_host(qapp):
    """host.current_theme 决定取哪套配色。

    注意：不要断言两份 QSS 字符串不同 —— 设计上 apply_theme() **优先复用
    宿主容器的 QSS**（"与主窗口完全一致"正是目的），所以宿主有 QSS 时
    两者会相同。真正反映主题的是 GlassPanel 的配色表。
    """
    light = _FakeHost(theme="light")
    dark = _FakeHost(theme="dark")
    d1 = PluginDialog(light, title="t")
    d2 = PluginDialog(dark, title="t")

    # 配色必须真的落到 GlassPanel 上（_fill/_edge_top 是解析后的 QColor）
    assert d1._container._fill != d2._container._fill
    assert d1._container._edge_top != d2._container._edge_top


def test_theme_qss_reused_from_host(qapp):
    """宿主已有 QSS 时必须直接复用（保证与主窗口像素级一致）"""
    host = _FakeHost(theme="light")
    sentinel = "/* HOST_QSS_SENTINEL */\n" + get_main_window_qss("light")
    host._container.setStyleSheet(sentinel)
    dlg = PluginDialog(host, title="t")
    assert "HOST_QSS_SENTINEL" in dlg._container.styleSheet()

# ---------------- H ----------------
def test_reexports_available(qapp):
    """官方再导出的组件都可以直接用"""
    _ = PluginDialog(None, title="t")  # 构造即校验再导出可用（保留引用防提前回收）
    assert isinstance(make_separator(), QWidget)
    assert make_section_label("标题").objectName() == "sectionLabel"
    assert make_hint_label("提示").objectName() == "hintLabel"

    btn = QPushButton("测试")
    flash_button(btn, "✅ 已复制", ms=10)
    assert btn.text() == "✅ 已复制"
    assert not btn.isEnabled()          # flash 期间禁用，防连点


def test_content_goes_into_body(qapp):
    """内容加到 body_layout 才能正确落在标题栏下方"""
    dlg = PluginDialog(None, title="t")
    label = QLabel("内容")
    dlg.body_layout.addWidget(label)
    assert label in set(dlg.body.findChildren(object))
