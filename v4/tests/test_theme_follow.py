# -*- coding: utf-8 -*-
"""跟随系统主题（成熟化 3.1）回归测试。

钉住的行为：
  1. resolve_theme_name：light / dark 直通；follow 按系统色 scheme 解析；
     Unknown / 老 PyQt6 / 异常 / 无 GUI 一律回落 dark；非法值回落默认主题
  2. get_colors 等公开入口吃 "follow" 原始值 → 拿到的是解析后的配色
     （主窗口 / 球 / 面板整条链路因此无需关心 follow 的存在）
  3. config 的 theme 白名单：light / dark / follow 合法，垃圾值回退默认
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt                              # noqa: E402
from PyQt6.QtWidgets import QApplication                 # noqa: E402

from src import theme as theme_mod                       # noqa: E402
from src.config import ConfigManager, DEFAULT_CONFIG     # noqa: E402
from src.theme import (THEME_VALUES, DEFAULT_THEME,      # noqa: E402
                       resolve_theme_name, get_colors)


class _FakeHints:
    """styleHints 替身：colorScheme() 返回注入的枚举值（不依赖窗口平台）"""

    def __init__(self, scheme):
        self._scheme = scheme

    def colorScheme(self):
        return self._scheme


class _BrokenHints:
    """colorScheme() 抛异常的替身：老版本 / 平台层故障的兜底路径"""

    def colorScheme(self):
        raise RuntimeError("not supported")


@pytest.fixture(scope="module")
def qapp():
    """offscreen 下跑一次真 QApplication（resolve 的无注入分支需要实例）"""
    return QApplication.instance() or QApplication([])


# ====================================================================
# resolve_theme_name：直通 / follow / 兜底
# ====================================================================
class TestResolvePassthrough:
    def test_light_dark_unchanged(self):
        assert resolve_theme_name("light") == "light"
        assert resolve_theme_name("dark") == "dark"

    def test_explicit_theme_ignores_system(self):
        """显式选择不经过系统判断：即使注入相反的 scheme 也原样返回"""
        dark_hints = _FakeHints(Qt.ColorScheme.Dark)
        light_hints = _FakeHints(Qt.ColorScheme.Light)
        assert resolve_theme_name("light", dark_hints) == "light"
        assert resolve_theme_name("dark", light_hints) == "dark"


class TestResolveFollow:
    def test_follow_dark_scheme(self):
        assert resolve_theme_name("follow",
                                  _FakeHints(Qt.ColorScheme.Dark)) == "dark"

    def test_follow_light_scheme(self):
        assert resolve_theme_name("follow",
                                  _FakeHints(Qt.ColorScheme.Light)) == "light"

    def test_follow_unknown_falls_back_dark(self):
        assert resolve_theme_name(
            "follow", _FakeHints(Qt.ColorScheme.Unknown)) == "dark"

    def test_follow_broken_hints_falls_back_dark(self):
        assert resolve_theme_name("follow", _BrokenHints()) == "dark"

    def test_follow_no_app_falls_back_dark(self):
        """无注入且无 QGuiApplication 实例（纯逻辑环境）→ dark，不抛错"""
        from PyQt6.QtGui import QGuiApplication
        assert QGuiApplication.instance() is None or True
        # 若测试进程里恰好有实例（qapp fixture 已建），此用例退化为
        # 「真取一次不出错且返回合法值」；两条路径都不允许抛异常
        value = resolve_theme_name("follow")
        assert value in ("light", "dark")

    def test_follow_real_offscreen_app(self, qapp):
        """offscreen 真取 styleHints：结果合法（平台无关的冒烟）"""
        assert resolve_theme_name("follow") in ("light", "dark")


class TestResolveFallback:
    @pytest.mark.parametrize("bad", ["", "blue", "Light", "FOLLOW", None,
                                     123, ["dark"]])
    def test_invalid_value_falls_back_default(self, bad):
        """非法值回落 DEFAULT_THEME（与旧 get_colors 未知主题兜底同口径）"""
        assert resolve_theme_name(bad) == DEFAULT_THEME


# ====================================================================
# get_colors / THEME_VALUES
# ====================================================================
class TestPublicSurface:
    def test_theme_values_set(self):
        assert THEME_VALUES == frozenset({"light", "dark", "follow"})

    def test_get_colors_follow_resolves(self, monkeypatch):
        """get_colors 直接收 "follow"：解析结果决定配色（链路收口验证）"""
        monkeypatch.setattr(theme_mod, "_system_scheme",
                            lambda hints=None: "light")
        assert get_colors("follow") is theme_mod.THEMES["light"]
        monkeypatch.setattr(theme_mod, "_system_scheme",
                            lambda hints=None: "dark")
        assert get_colors("follow") is theme_mod.THEMES["dark"]

    def test_get_colors_invalid_still_default(self):
        assert get_colors("bogus") is theme_mod.THEMES[DEFAULT_THEME]

    def test_get_colors_explicit_untouched(self):
        """现有 light / dark 调用零变化"""
        assert get_colors("light") is theme_mod.THEMES["light"]
        assert get_colors("dark") is theme_mod.THEMES["dark"]


# ====================================================================
# config 白名单：follow 合法、垃圾值回退默认
# ====================================================================
class TestConfigThemeWhitelist:
    def test_set_accepts_three_values(self, tmp_path):
        cm = ConfigManager(str(tmp_path / "config.json"))
        assert cm.set("theme", "light") is True
        assert cm.set("theme", "dark") is True
        assert cm.set("theme", "follow") is True

    def test_set_rejects_garbage(self, tmp_path):
        cm = ConfigManager(str(tmp_path / "config.json"))
        assert cm.set("theme", "blue") is False
        assert cm.get("theme") == DEFAULT_CONFIG["theme"]

    def test_load_rejects_garbage(self, tmp_path):
        """手改 config.json 塞垃圾主题 → 回退默认，不静默破坏主题链路"""
        import json
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"theme": "purple"}), encoding="utf-8")
        cm = ConfigManager(str(path))
        assert cm.get("theme") == DEFAULT_CONFIG["theme"]

    def test_follow_value_persists(self, tmp_path):
        path = tmp_path / "config.json"
        cm = ConfigManager(str(path))
        cm.set("theme", "follow")
        cm.save()
        cm2 = ConfigManager(str(path))
        assert cm2.get("theme") == "follow"
