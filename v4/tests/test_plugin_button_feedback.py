# -*- coding: utf-8 -*-
"""插件页面按钮 hover 反馈回归  -  test_plugin_button_feedback
====================================================================
背景（2026-10-03）：五个插件页面（ai-assistant / ai-text-workshop /
kb-search / recurring-tasks / vault）里的按钮是**裸 QPushButton**——
基态命中 theme.py 全局 ``QPushButton`` 规则（未命名 = $primary 实底、
命名 = secondaryBtn ghost），而 QSS 的 ``:hover`` 只保留文字色变化
（与基态同色 = 零变化），背景过渡**有意**全权交给 SmoothButton 的
overlay 体系——裸按钮没有这套机制 → **鼠标放置零反馈**。

修法：30 处构造全部换成签名兼容的 ``SmoothButton``（QSS 命中与
外观不变，只补 paint 层平滑 hover/press），不碰 theme.py / controls.py。

本文件钉两层契约：
  A. 静态：插件源码不允许再出现裸 ``QPushButton(`` 构造（AST 扫描）；
  B. 行为：未命名按钮 = 兜底 overlay（primary_hover 实底）、
     secondaryBtn = primary 18% 淡染——插件按钮的两种实际反馈语系。
====================================================================
"""

import ast
import io
import os

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
REPO = os.path.dirname(BASE)
PLUGINS = os.path.join(REPO, "plugins")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from src.controls import _SMOOTH_OVERLAYS, SmoothButton         # noqa: E402

_APP = None


@pytest.fixture(scope="module")
def qapp():
    """★ 模块级全局持有 QApplication（局部版被 GC 后 QPixmap qFatal）。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


def _bare_qpushbutton_lines(src: str):
    """源码里所有裸 ``QPushButton(`` 构造调用的行号（AST，非正则）。"""
    tree = ast.parse(src)
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "QPushButton"]


def _smoothbutton_count(src: str):
    tree = ast.parse(src)
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "SmoothButton")


# 五个自带页面的插件（weekly-report 页面无按钮，不在契约内）
_PLUGINS_WITH_BUTTONS = (
    "ai-assistant", "ai-text-workshop", "kb-search", "recurring-tasks", "vault",
)


class TestStaticContract:
    """插件源码静态契约：按钮构造必须走 SmoothButton。"""

    @pytest.mark.parametrize("name", _PLUGINS_WITH_BUTTONS)
    def test_no_bare_qpushbutton_construction(self, name):
        """裸 QPushButton 构造 = hover 零反馈（本次用户报告的根因）。"""
        src = io.open(os.path.join(PLUGINS, name, "plugin.py"),
                      encoding="utf-8").read()
        bare = _bare_qpushbutton_lines(src)
        assert bare == [], (
            "%s 仍有裸 QPushButton 构造（行 %s）——裸按钮没有 overlay "
            "机制，鼠标放置零反馈；请改用 SmoothButton（签名兼容）"
            % (name, bare))

    @pytest.mark.parametrize("name", _PLUGINS_WITH_BUTTONS)
    def test_uses_smoothbutton(self, name):
        """ SmoothButton 真的在用（防 import 加了但构造没换的假修）。"""
        src = io.open(os.path.join(PLUGINS, name, "plugin.py"),
                      encoding="utf-8").read()
        assert _smoothbutton_count(src) > 0, (
            "%s 没有任何 SmoothButton 构造" % name)

    def test_scanner_is_not_a_no_op(self):
        """反向验证：扫描器必须能报出裸 QPushButton（护栏能红灯）。"""
        sample = ("from PyQt6.QtWidgets import QPushButton\n"
                  "b = QPushButton('x')\n")
        assert _bare_qpushbutton_lines(sample) == [2]
        clean = ("from src.controls import SmoothButton\n"
                 "b = SmoothButton('x')\n")
        assert _bare_qpushbutton_lines(clean) == []


class TestOverlayContract:
    """插件按钮换 SmoothButton 后的实际反馈语系。"""

    def test_unnamed_button_uses_fallback_overlay(self, qapp):
        """recurring-tasks / ai-assistant 主功能按钮：未命名 → 兜底实底组
        （基态 $primary 实底，hover 平滑过渡到 $primary_hover）。"""
        b = SmoothButton("发送")
        assert b._overlay_specs() == _SMOOTH_OVERLAYS[None]
        assert b._overlay_specs()[0] == ("primary_hover", 255)
        b.deleteLater()

    def test_named_secondary_button_gets_tint_overlay(self, qapp):
        """vault / ai-text-workshop 列表按钮：secondaryBtn → primary 18%
        淡染（ghost 语义的既定 hover 端点），press 只下沉不换底。"""
        b = SmoothButton("复制结果")
        b.setObjectName("secondaryBtn")
        assert b._overlay_specs() == (("primary", 18), None)
        b.deleteLater()

    def test_smoothbutton_is_still_a_qpushbutton(self, qapp):
        """外观契约：SmoothButton 仍是 QPushButton —— QSS 命中的选择器
        不变，插件按钮基态外观与改造前逐像素同源。"""
        b = SmoothButton("发送")
        assert isinstance(b, QPushButton)
        b.deleteLater()

    def test_construction_signature_compatible(self, qapp):
        """签名兼容冒烟：原 QPushButton(text, parent) 用法原样可用
        （30 处替换是纯类名替换，构造行零改动）。"""
        host = QWidget()
        b = SmoothButton("文本", host)
        assert b.parent() is host
        assert b.text() == "文本"
        b.deleteLater()
        host.deleteLater()
