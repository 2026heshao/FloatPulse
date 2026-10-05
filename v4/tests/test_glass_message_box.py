# -*- coding: utf-8 -*-
"""GlassMessageBox 单测（2026-10 遗留旧 UI 统一专项 · T01 基建护栏）。

覆盖口径：
  · 四静态工厂存在性 + 签名可用（exec 替身后不真弹模态）；
  · question 返回 bool（确认 True / 取消·ESC False，不再是 Yes/No 枚举）；
  · danger=True 时确认按钮 objectName="dangerBtn"，否则 primaryBtn，
    取消恒为 secondaryBtn（铁律：footer 一律 SmoothButton）；
  · question 默认焦点在「取消」（防回车误触危险操作，已拍板）；
  · 双主题构建（light/dark 都要真用上，铁律）；
  · ★ 主题解析回归钉：**浅色宿主 + 无 Qt parent 的 GlassDialog 当 parent**
    必须解析出 light —— 用户实测报障（清仓建议弹窗里嵌套确认框被渲染成
    深色）的根因即此链路漏查 ``parent._host.current_theme``；本断言在
    修复前红灯、修复后转绿（护栏必须能红灯）。

反向验证：把 glass_message_box.py 的 ``_resolve_theme`` 里 ``_host`` 一查
删掉 → test_resolve_theme_glassdialog_parent_is_light 必红。
"""
import os
import sys

import pytest

from PyQt6.QtWidgets import QApplication

_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
sys.path.insert(0, V4)

from src.glass_dialog import GlassDialog  # noqa: E402
from src.glass_message_box import (  # noqa: E402
    _MAX_H, GlassMessageBox, _resolve_theme,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------- 主题解析 ----------------
class _LightHost:
    current_theme = "light"


class _DarkHost:
    current_theme = "dark"


class _PanelLike:
    """面板形态：主题挂在 _host.current_theme（MainWindow/面板 host）"""

    def __init__(self):
        self._host = _LightHost()

    def parent(self):
        return None


class _WidgetLike:
    """桌面小部件形态：无 _host，只有 self._theme（widget_app_launcher）"""

    _theme = "dark"

    def parent(self):
        return None


def test_resolve_theme_direct_current_theme():
    assert _resolve_theme(_DarkHost()) == "dark"


def test_resolve_theme_via_host_attribute():
    assert _resolve_theme(_PanelLike()) == "light"


def test_resolve_theme_via_private_theme():
    assert _resolve_theme(_WidgetLike()) == "dark"


def test_resolve_theme_glassdialog_parent_is_light(qapp):
    """★ 回归钉：GlassDialog 当 parent（其 _host 是浅色宿主）→ light。

    清仓建议弹窗（GlassDialog）里嵌套 GlassMessageBox 时，弹窗自身无
    current_theme/_theme，Qt parent 为 None —— 只有查 ``_host`` 才能命中。
    """
    parent_dlg = GlassDialog(_LightHost(), title="清仓建议")
    try:
        assert _resolve_theme(parent_dlg) == "light"
    finally:
        parent_dlg.deleteLater()


def test_resolve_theme_nested_messagebox_via_host(qapp):
    """消息框嵌套：外层 GlassMessageBox 的宿主是 _ThemeHost 壳（存 _host），
    内层以外层为 parent 时同样要解析正确（默认主题 dark）。"""
    outer = GlassMessageBox(None, "外层", "x", icon_kind="warning")
    try:
        assert outer._theme_name == "dark"
        assert _resolve_theme(outer) == "dark"
    finally:
        outer.deleteLater()


def test_resolve_theme_falls_back_to_default():
    assert _resolve_theme(None) == "dark"          # DEFAULT_THEME = dark
    assert _resolve_theme(object()) == "dark"      # 链上无主题属性


# ---------------- 四工厂 ----------------
def test_four_factories_exist():
    for name in ("question", "information", "warning", "critical"):
        method = getattr(GlassMessageBox, name, None)
        assert callable(method), "缺少静态工厂 %s" % name
        assert isinstance(getattr(GlassMessageBox, name),
                          staticmethod) or callable(method)


def test_question_returns_bool(qapp, monkeypatch):
    """question 返回 bool：确认 True / 取消 False（不再有 Yes/No 枚举）。"""
    monkeypatch.setattr(GlassMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(GlassMessageBox, "result", lambda self: 1)
    assert GlassMessageBox.question(None, "t", "x") is True
    monkeypatch.setattr(GlassMessageBox, "result", lambda self: 0)
    assert GlassMessageBox.question(None, "t", "x") is False
    monkeypatch.setattr(GlassMessageBox, "result",
                        lambda self: 4)          # QDialog.Rejected
    assert GlassMessageBox.question(None, "t", "x") is False


def test_notice_factories_return_none(qapp, monkeypatch):
    monkeypatch.setattr(GlassMessageBox, "exec", lambda self: None)
    assert GlassMessageBox.information(None, "t", "x") is None
    assert GlassMessageBox.warning(None, "t", "x") is None
    assert GlassMessageBox.critical(None, "t", "x") is None


def test_danger_button_object_name(qapp, monkeypatch):
    """danger=True → 确认钮 dangerBtn；否则 primaryBtn；取消恒 secondaryBtn。"""
    captured = {}
    original = GlassMessageBox.add_footer

    def _spy(self, buttons):
        captured["names"] = [e[1] for e in buttons]
        return original(self, buttons)

    monkeypatch.setattr(GlassMessageBox, "add_footer", _spy)
    monkeypatch.setattr(GlassMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(GlassMessageBox, "result", lambda self: 0)

    GlassMessageBox.question(None, "删除", "x", danger=True)
    assert captured["names"] == ["dangerBtn", "secondaryBtn"]

    GlassMessageBox.question(None, "加入", "x")
    assert captured["names"] == ["primaryBtn", "secondaryBtn"]


def test_question_default_focus_on_cancel(qapp, monkeypatch):
    """默认焦点在「取消」侧（防回车误触危险操作）。"""
    monkeypatch.setattr(GlassMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(GlassMessageBox, "result", lambda self: 0)
    box = None
    original = GlassMessageBox.add_footer

    def _spy(self, buttons):
        nonlocal box
        box = self
        return original(self, buttons)

    monkeypatch.setattr(GlassMessageBox, "add_footer", _spy)
    GlassMessageBox.question(None, "确认删除", "x", danger=True)
    assert box is not None
    focused = box.focusWidget()
    assert focused is not None
    assert focused.objectName() == "secondaryBtn", (
        "question 弹窗默认焦点必须落在取消钮（回车=取消，防误触）")


def test_dual_theme_construction(qapp):
    """light/dark 双主题都要真用上（铁律）：两主题各构建一次并取色成功。"""
    for theme in ("light", "dark"):
        for kind in ("question", "information", "warning", "critical"):
            box = GlassMessageBox(None, "标题", "正文", icon_kind=kind,
                                  theme=theme)
            try:
                assert box._theme_name == theme
                assert box._icon_label.pixmap() is not None
                assert not box._icon_label.pixmap().isNull()
            finally:
                box.deleteLater()


def test_narrow_width_and_adaptive_height(qapp):
    """窄体：宽固定 440；高随文本行数自适应（长文封顶滚动）。"""
    short = GlassMessageBox(None, "t", "一行", icon_kind="info")
    try:
        assert short.width() == 440
    finally:
        short.deleteLater()
    long = GlassMessageBox(None, "t", "很长的正文" * 200, icon_kind="info")
    try:
        assert long.height() <= _MAX_H + 1
    finally:
        long.deleteLater()
    assert long.height() >= short.height()
