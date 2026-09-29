# -*- coding: utf-8 -*-
"""设置页内部分类导航（SettingsPanel）单元测试。

背景（2026-09-29）：设置页从「单页长滚动」重构为「左侧分类导航 +
右侧分类内容」的切换式布局。分组卡片只是换了挂载容器（原共享滚动列
→ 各分类独立滚动页），所有设置项的持久化 / 信号广播行为应零变化。

覆盖：
  1. SETTINGS_CATEGORIES 常量完整性（key 唯一 / 非空 / 含 ai 与 about）
  2. 面板构建：stack 页数、导航按钮数与分类一致，互斥选中
  3. show_category：正常切换 / 未知 key 容错
  4. 搬运完整性：各分类关键控件属性仍在（防分组拆页漏项）
  5. 「恢复默认设置」在页底常驻栏（不在分类 stack 内）
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.settings_panel import SETTINGS_CATEGORIES, SettingsPanel  # noqa: E402


# ====================================================================
# SETTINGS_CATEGORIES 常量
# ====================================================================
class TestSettingsCategories:
    def test_keys_unique_nonempty(self):
        keys = [c[0] for c in SETTINGS_CATEGORIES]
        assert len(keys) == len(set(keys))
        assert all(keys)

    def test_entries_are_key_icon_label(self):
        for key, icon, label in SETTINGS_CATEGORIES:
            assert isinstance(key, str) and key
            assert isinstance(icon, str) and icon
            assert isinstance(label, str) and label

    def test_has_ai_and_about(self):
        keys = [c[0] for c in SETTINGS_CATEGORIES]
        # AI 总配置（插件后端单一真相源）与关于必须各占一个分类
        assert "ai" in keys and "about" in keys

    def test_nine_categories(self):
        # 8 个行为配置分类 + 关于
        assert len(SETTINGS_CATEGORIES) == 9


# ====================================================================
# SettingsPanel：导航构建与切换（离屏 QApplication）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeConfig:
    """dict 兜底的配置替身：面板构建只需要 get / set / save"""

    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        pass


@pytest.fixture(scope="module")
def panel(qapp):
    host = types.SimpleNamespace(_config=_FakeConfig(), current_theme="dark")
    return SettingsPanel(host)


class TestSettingsNavPanel:
    def test_stack_pages_match_categories(self, panel):
        assert panel._cat_stack.count() == len(SETTINGS_CATEGORIES)

    def test_nav_buttons_match_categories(self, panel):
        assert len(panel._cat_btns) == len(SETTINGS_CATEGORIES)
        for key, (_k, icon, label) in zip(
                panel._cat_btns, SETTINGS_CATEGORIES):
            assert icon in panel._cat_btns[key].text()
            assert label in panel._cat_btns[key].text()
            assert panel._cat_btns[key].objectName() == "settingsNavBtn"

    def test_initial_category_is_first(self, panel):
        assert panel._cat_stack.currentIndex() == 0
        first_key = SETTINGS_CATEGORIES[0][0]
        assert panel._cat_btns[first_key].isChecked()

    def test_show_category_switches(self, panel):
        panel.show_category("ai")
        assert panel._cat_stack.currentIndex() == panel._cat_index["ai"]
        assert panel._cat_btns["ai"].isChecked()
        assert not panel._cat_btns[SETTINGS_CATEGORIES[0][0]].isChecked()

    def test_show_category_unknown_key_noop(self, panel):
        cur = panel._cat_stack.currentIndex()
        panel.show_category("no-such-key")
        assert panel._cat_stack.currentIndex() == cur

    def test_buttons_mutually_exclusive(self, panel):
        panel.show_category("about")
        checked = [k for k, b in panel._cat_btns.items() if b.isChecked()]
        assert checked == ["about"]


# ====================================================================
# 搬运完整性：分组拆页后关键控件一个都不能少
# ====================================================================
class TestRowsSurviveSplit:
    @pytest.mark.parametrize("attr", [
        # 外观
        "_set_theme_light", "_set_theme_dark", "_set_anim_speed",
        "_set_card_size",
        # 悬浮球
        "_set_ball_visible", "_set_ball_size", "_set_auto_hide_enabled",
        "_set_auto_hide", "_set_hide_fullscreen", "_set_card_always_show",
        "_set_plugins",
        # 剪贴板 / 素材
        "_set_clipboard_max", "_set_clipboard_filter", "_set_clipboard_images",
        "_set_temp_asset_max_count", "_set_temp_asset_max_file",
        "_set_temp_asset_max_days", "_set_asset_thumb",
        # 全局工具
        "_set_quick_capture", "_set_capture_hotkey", "_set_screenshot",
        "_set_screenshot_hotkey", "_set_pomodoro", "_set_pomodoro_focus",
        "_set_pomodoro_break", "_set_pomodoro_auto",
        # 启动与系统
        "_set_autostart", "_set_restore_last_page", "_set_close_to_tray",
        "_set_task_reminder",
        # 导出
        "_set_vault_path", "_set_export_btn",
        # AI 总配置
        "_ai_mode_cloud", "_ai_mode_local", "_ai_url", "_ai_key",
        "_ai_model", "_ai_exe", "_ai_gguf", "_ai_port", "_ai_local_btn",
        "_ai_save_btn", "_ai_plugins_combo",
    ])
    def test_widget_attr_exists(self, panel, attr):
        assert hasattr(panel, attr), f"拆页后丢失控件：{attr}"

    def test_ai_mode_rows_hide_with_separators(self, panel):
        """模式切换隐藏整行时，行下分隔线必须跟着藏（否则卡片留空档）"""
        panel.show_category("ai")
        # 默认 cloud：本地行 + 其分隔线都藏；云端行 + 分隔线都在
        assert panel._ai_row_url.isVisibleTo(panel)
        assert panel._row_sep[panel._ai_row_url].isVisibleTo(panel)
        assert not panel._ai_row_exe.isVisibleTo(panel)
        assert not panel._row_sep[panel._ai_row_exe].isVisibleTo(panel)
        # 切 local：两侧对调
        panel._config.set("ai_backend_mode", "local")
        panel._ai_apply_mode_ui()
        assert panel._ai_row_exe.isVisibleTo(panel)
        assert panel._row_sep[panel._ai_row_exe].isVisibleTo(panel)
        assert not panel._ai_row_url.isVisibleTo(panel)
        assert not panel._row_sep[panel._ai_row_url].isVisibleTo(panel)
        panel._config.set("ai_backend_mode", "cloud")
        panel._ai_apply_mode_ui()

    def test_reset_button_in_footer_not_in_stack(self, panel):
        """恢复默认按钮挂在 outer 常驻栏，任何分类下都可用"""
        chain, w = set(), panel._reset_btn.parentWidget()
        while w is not None:
            chain.add(id(w))
            w = w.parentWidget()
        assert id(panel._cat_stack) not in chain
        assert "恢复默认设置" in panel._reset_btn.text()
