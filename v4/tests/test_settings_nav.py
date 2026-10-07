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

import gc
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

    def test_ten_categories(self):
        # 9 个行为配置分类 + 关于（2026-10-02 番茄钟从全局工具独立成分类）
        assert len(SETTINGS_CATEGORIES) == 10


# ====================================================================
# SettingsPanel：导航构建与切换（离屏 QApplication）
# ====================================================================
# 页面存活桩（2026-10-07）：面板 C++ 树**不能**让 GC 在会话中段析构
# （同 test_ai_assistant_export 实锤：运行中动画被 GC 兜底析构会打乱
# QUnifiedTimer 簿记 → 同会话后续所有 QAbstractAnimation 冻结）。
# 不整页同步 delete（复杂子树上 fail-fast），持引用到进程退出。
_KEEP_ALIVE = []


def _quiesce(widget, app):
    """面板「受控墓地」拆除（2026-10-07 顺序冻结修复）。

    settings_nav → interaction_visual_v 顺序曾必现 6 项动画冻结：
    面板生命周期里自产的一次性 overlay（Stepper 滑层 / fade_in_once
    控制器，`_done` 里 deleteLater 自尽）留下成批「Python 包装器还在、
    C++ 已删」的动画垃圾；这些垃圾若拖到下一个测试文件的分配触发 GC
    时才被兜底析构，会打乱 QUnifiedTimer 簿记。修法 = 拆除时在**本
    文件的受控点**把事情做绝：
      1. stop 面板下全部动画/定时器（防运行中析构）；
      2. 反复冲刷 DeferredDelete（自尽 overlay 的删除当场落地，不悬
         到会话末）；
      3. gc.collect() 把动画包装器的引用环一并掐死（不留到下一个
         文件被随机时点的 GC 收割）。
    """
    from PyQt6.QtCore import QAbstractAnimation, QEvent, QTimer
    from PyQt6.QtWidgets import QApplication
    for anim in widget.findChildren(QAbstractAnimation):
        anim.stop()
    for t in widget.findChildren(QTimer):
        t.stop()
    widget.close()
    for _ in range(3):
        QApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
        app.processEvents()
    gc.collect()


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
    host = types.SimpleNamespace(config=_FakeConfig(), current_theme="dark")
    w = SettingsPanel(host)
    yield w
    _quiesce(w, qapp)
    _KEEP_ALIVE.append(w)


class TestSettingsNavPanel:
    def test_stack_pages_match_categories(self, panel):
        assert panel._cat_stack.count() == len(SETTINGS_CATEGORIES)

    def test_nav_buttons_match_categories(self, panel):
        assert len(panel._cat_btns) == len(SETTINGS_CATEGORIES)
        for key, (_k, icon, label) in zip(
                panel._cat_btns, SETTINGS_CATEGORIES):
            # UI 重构 04：第二项由 emoji 改为 icons.py 自绘图标名，图标不再
            # 出现在按钮文字里，改断言按钮承载的 icon_name（QIcon 位图无文字）。
            assert panel._cat_btns[key].icon_name == icon
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
# V5 指示滑块·懒加载主题同步回归（2026-10-07 用户报障）
# ====================================================================
class TestNavIndicatorThemeOnBuild:
    """懒加载构建后指示滑块必须立即跟随当前主题。

    主窗 ``_apply_theme`` 只刷已构建页（懒加载跳过未构建页）；
    ``SettingsPanel.__init__`` 末尾补的 ``apply_theme()`` 缺失时，
    ``_SettingsNavIndicator`` 停在构造默认 ``get_colors("dark")`` 上——
    自定义强调色下选中底=暗红块（用户报障截图），默认强调色下=暗绿块，
    直到用户手动切一次主题才被纠正。
    """

    def test_indicator_follows_theme_at_build(self, qapp):
        from src.theme import get_colors
        host = types.SimpleNamespace(config=_FakeConfig(),
                                     current_theme="light")
        p = SettingsPanel(host)
        try:
            assert p._nav_indicator._colors == get_colors("light"), (
                "构建后指示滑块配色未跟随当前主题（懒加载漏主题同步，"
                "选中底会停在 dark 主题端点色上）")
        finally:
            # 同 panel fixture：受控墓地拆除，防 GC 中段析构带动画的面板
            _quiesce(p, qapp)
            _KEEP_ALIVE.append(p)


# ====================================================================
# 搬运完整性：分组拆页后关键控件一个都不能少
# ====================================================================
class TestRowsSurviveSplit:
    @pytest.mark.parametrize("attr", [
        # 外观
        "_set_theme_light", "_set_theme_dark", "_set_anim_speed",
        "_set_card_size", "_set_mini_icon_size",
        # 悬浮球
        "_set_ball_visible", "_set_ball_size", "_set_auto_hide_enabled",
        "_set_auto_hide", "_set_hide_fullscreen", "_set_card_always_show",
        "_set_plugins",
        # 剪贴板 / 素材
        "_set_clipboard_max", "_set_clipboard_filter", "_set_clipboard_images",
        "_set_temp_asset_max_count", "_set_temp_asset_max_file",
        "_set_temp_asset_max_days", "_set_asset_thumb",
        # 全局工具
        "_set_screenshot", "_set_screenshot_hotkey",
        # 番茄钟（2026-10-02 独立分类）
        "_set_pomodoro", "_set_pomodoro_focus",
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
