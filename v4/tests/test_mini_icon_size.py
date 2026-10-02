# -*- coding: utf-8 -*-
"""小卡片（悬浮球旁）软件导航页图标尺寸可调 —— 护栏测试。

背景（2026-10-02）：设置页的「软件卡片尺寸」只作用于**主窗口**软件导航页
（AppCardWidget 的 icon = card_size - 20，本来就跟随）；小卡片
（CardWindow 第 6 页）的 icon_px=36 / btn_size=76 是两处独立硬编码，
用户改设置项时它纹丝不动 —— 表现为「图标大小调不动」。

本文件钉死修复后的契约：
  1. 常量与换算：范围/默认值/按钮边长公式（src.constants）
  2. CardWindow.mini_icon_size：懒读配置 + 越界钳制 + 脏值容错
  3. CardWindow.apply_icon_size：公开 API（宿主门面，不许穿透私有成员）
  4. _refresh_app_page：真正按 mini_icon_size 渲染（禁回退硬编码）
  5. 配置三件套 + 设置页步进器落盘/广播/钳制
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import (  # noqa: E402
    MINI_ICON_MIN, MINI_ICON_MAX, MINI_ICON_DEFAULT, MINI_BTN_PAD,
    mini_btn_size,
)
from src.config import (  # noqa: E402
    DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES,
)


# ====================================================================
# 1. 常量与换算
# ====================================================================
class TestMiniIconConstants:
    def test_range_sane(self):
        assert MINI_ICON_MIN == 24
        assert MINI_ICON_MAX == 56
        assert MINI_ICON_DEFAULT == 36
        assert MINI_ICON_MIN < MINI_ICON_DEFAULT < MINI_ICON_MAX

    def test_default_matches_legacy_hardcode(self):
        """默认值必须等于历史硬编码的36 —— 老用户视觉零变化。"""
        assert MINI_ICON_DEFAULT == 36
        assert mini_btn_size(MINI_ICON_DEFAULT) == 76

    def test_btn_size_is_monotonic(self):
        sizes = [mini_btn_size(v) for v in range(MINI_ICON_MIN - 10,
                                                 MINI_ICON_MAX + 10)]
        assert sizes == sorted(sizes)

    def test_btn_size_clamps_out_of_range(self):
        """越界输入按端点算，不外扩（否则能把 440px 窗口撑破）。"""
        assert mini_btn_size(0) == mini_btn_size(MINI_ICON_MIN)
        assert mini_btn_size(9999) == mini_btn_size(MINI_ICON_MAX)

    def test_pad_is_positive(self):
        assert MINI_BTN_PAD > 0


# ====================================================================
# 2. 配置三件套
# ====================================================================
class TestMiniIconConfig:
    def test_in_three_sets(self):
        assert "app_mini_icon_size" in DEFAULT_CONFIG
        assert _CONFIG_TYPES["app_mini_icon_size"] is int
        assert _CONFIG_RANGES["app_mini_icon_size"] == (MINI_ICON_MIN,
                                                         MINI_ICON_MAX)

    def test_default_matches_constant(self):
        assert DEFAULT_CONFIG["app_mini_icon_size"] == MINI_ICON_DEFAULT

    def test_not_bare_int_range(self):
        """bool 不该混进int 键的范围表（本项目铁律）。"""
        assert isinstance(_CONFIG_RANGES["app_mini_icon_size"], tuple)


# ====================================================================
# 3~4. CardWindow 侧（需要 Qt）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeConfig:
    """ConfigManager 替身：内存 dict，绝不读写用户 config.json"""

    def __init__(self, data=None):
        self._data = {"app_mini_icon_size": MINI_ICON_DEFAULT, "apps": []}
        if data:
            self._data.update(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        pass


def _make_card(config=None):
    from src.card_window import CardWindow
    cw = CardWindow()
    cw.set_config_manager(config or _FakeConfig())
    return cw


def _app_buttons(cw):
    """网格里的 appLaunchBtn 按钮（按 objectName 过滤，空态占位不算）。

    从 ``_app_content`` 找而不是 ``_app_grid``——后者是 QLayout，
    按钮是挂在它宿主 widget 上的子控件，对 layout  findChildren 恒为空。
    """
    from PyQt6.QtWidgets import QWidget
    return [w for w in cw._app_content.findChildren(QWidget)
            if w.objectName() == "appLaunchBtn"]


class TestMiniIconProperty:
    def test_reads_config(self, qapp):
        cw = _make_card(_FakeConfig({"app_mini_icon_size": 52}))
        assert cw.mini_icon_size == 52

    def test_default_without_config_manager(self, qapp):
        """未注入 ConfigManager 时回退默认，不得抛异常。"""
        from src.card_window import CardWindow
        cw = CardWindow()
        assert cw._config_manager is None
        assert cw.mini_icon_size == MINI_ICON_DEFAULT

    def test_clamps_out_of_range(self, qapp):
        for raw, want in ((0, MINI_ICON_MIN), (9999, MINI_ICON_MAX),
                          (-5, MINI_ICON_MIN)):
            cw = _make_card(_FakeConfig({"app_mini_icon_size": raw}))
            assert cw.mini_icon_size == want

    def test_dirty_config_value_no_crash(self, qapp):
        """脏配置（字符串/None）不得抛——property 侧静默回退默认。"""
        for raw in ("abc", None, [1]):
            cw = _make_card(_FakeConfig({"app_mini_icon_size": raw}))
            assert cw.mini_icon_size == MINI_ICON_DEFAULT

    def test_set_config_manager_resets_cache(self, qapp):
        """换配置源必须让缓存作废，否则会读到上一个源的值。"""
        cw = _make_card(_FakeConfig({"app_mini_icon_size": 30}))
        assert cw.mini_icon_size == 30
        cw.set_config_manager(_FakeConfig({"app_mini_icon_size": 50}))
        assert cw.mini_icon_size == 50


class TestApplyIconSize:
    def test_is_public_method(self):
        """宿主门面：apply_icon_size 必须是公开方法，不能靠穿透私有成员。"""
        from src.card_window import CardWindow
        assert callable(getattr(CardWindow, "apply_icon_size", None))

    def test_updates_property(self, qapp):
        cw = _make_card()
        cw.apply_icon_size(48)
        assert cw.mini_icon_size == 48

    def test_clamps(self, qapp):
        cw = _make_card()
        cw.apply_icon_size(9999)
        assert cw.mini_icon_size == MINI_ICON_MAX
        cw.apply_icon_size(1)
        assert cw.mini_icon_size == MINI_ICON_MIN

    def test_dirty_input_ignored(self, qapp):
        """非数值输入必须被忽略（保持上一个有效值），而不是打成默认值。

        打成默认值会让用户长按 ± 时手一抖界面就跳回36，比忽略更糟。
        """
        cw = _make_card()
        cw.apply_icon_size(48)
        for raw in ("nope", None, [1]):
            cw.apply_icon_size(raw)
            assert cw.mini_icon_size == 48, \
                "脏输入 %r 不该改变已生效的值" % (raw,)

    def test_idempotent_no_rebuild(self, qapp, monkeypatch):
        """同值重复调用不应重建网格（长按 ± 时会连发很多次）。"""
        cw = _make_card(_FakeConfig({"apps": [
            {"name": "A", "exe_path": "", "icon_path": ""}]}))
        cw.switch_mode("app")
        cw.show()
        qapp.processEvents()
        calls = []
        monkeypatch.setattr(cw, "_refresh_app_page",
                            lambda: calls.append(1))
        cw.apply_icon_size(44)
        cw.apply_icon_size(44)
        assert len(calls) == 1
        cw.hide()

    def test_hidden_card_defers_rebuild(self, qapp, monkeypatch):
        """小卡片不可见时不重建（切到app 页自然会读到新值）。"""
        cw = _make_card(_FakeConfig({"apps": [
            {"name": "A", "exe_path": "", "icon_path": ""}]}))
        cw.switch_mode("app")
        calls = []
        monkeypatch.setattr(cw, "_refresh_app_page",
                            lambda: calls.append(1))
        cw.apply_icon_size(44)
        assert calls == []
        assert cw.mini_icon_size == 44, "值本身必须已更新"


class TestRenderUsesConfig:
    def _render(self, qapp, icon_size):
        cw = _make_card(_FakeConfig({
            "app_mini_icon_size": icon_size,
            "apps": [{"name": "演示", "exe_path": "", "icon_path": ""}]}))
        cw._refresh_app_page()
        return cw

    def test_button_size_follows_icon_size(self, qapp):
        for icon in (MINI_ICON_MIN, MINI_ICON_DEFAULT, MINI_ICON_MAX):
            cw = self._render(qapp, icon)
            btns = _app_buttons(cw)
            assert btns, "软件网格没有渲染出按钮"
            for b in btns:
                assert b.iconSize().width() == icon
                assert b.width() == mini_btn_size(icon)
            cw.deleteLater()

    def test_no_hardcoded_36(self):
        """源码级钉子：36/76 不得再作为字面量出现在渲染路径里。

        只查 _refresh_app_page 函数体 —— 别处的 36 可能是别的语义。
        """
        import inspect
        from src.card_window import CardWindow
        src = inspect.getsource(CardWindow._refresh_app_page)
        assert "icon_px = 36" not in src, "图标尺寸又退回硬编码 36 了"
        assert "btn_size = 76" not in src, "按钮尺寸又退回硬编码 76 了"

    def test_uses_mini_icon_size_property(self, qapp):
        cw = self._render(qapp, 50)
        assert cw.mini_icon_size == 50
        for b in _app_buttons(cw):
            assert b.iconSize().width() == 50
        cw.deleteLater()


# ====================================================================
# 5. 设置页
# ====================================================================
class TestSettingsRow:
    def test_row_exists(self, qapp):
        import src.settings_panel as sp_mod
        # 只验证槽位方法存在（真正构建需完整 host，控件属性存在性由
        # test_settings_nav.TestRowsSurviveSplit 覆盖）
        assert hasattr(sp_mod.SettingsPanel, "_on_mini_icon_size_changed")

    def test_slot_persists_and_broadcasts(self, qapp):
        import src.settings_panel as sp_mod

        seen = []
        cfg = _FakeConfig({"app_mini_icon_size": MINI_ICON_DEFAULT})

        class _Host:
            _config = cfg
            mini_icon_size_changed = None  # 占位，下面换成真信号载体

        class _Sig:
            def emit(self, v):
                seen.append(v)

        panel = sp_mod.SettingsPanel.__new__(sp_mod.SettingsPanel)
        panel._config = cfg
        panel._host = _Host()
        panel._host.mini_icon_size_changed = _Sig()

        panel._on_mini_icon_size_changed(50)
        assert cfg.get("app_mini_icon_size") == 50
        assert seen == [50]

        # 越界必须先钳制再落盘（否则写出非法值，被 RANGES 静默丢弃）
        panel._on_mini_icon_size_changed(9999)
        assert cfg.get("app_mini_icon_size") == MINI_ICON_MAX
        assert seen[-1] == MINI_ICON_MAX

    def test_signal_declared_on_main_window(self):
        """MainWindow 必须声明该信号，否则接线处 AttributeError。"""
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "src", "main_window.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
                for sub in node.body:
                    tgt = None
                    if isinstance(sub, ast.AnnAssign):
                        tgt = sub.target
                    elif isinstance(sub, ast.Assign):
                        tgt = sub.targets[0]
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
        assert "mini_icon_size_changed" in names, \
            "MainWindow 未声明 mini_icon_size_changed 信号"