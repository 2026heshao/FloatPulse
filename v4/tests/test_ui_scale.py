# -*- coding: utf-8 -*-
"""界面缩放 / 字号（成熟化 3.5）单元测试。

钉住的行为：
  1. 缩放纯函数 scaled_font_pt：五个合法档位的换算正确
  2. config 新键：ui_scale（int，85-150）与 first_run_done（bool）的
     类型 / 范围校验、非法值加载回退默认、set 拒写
  3. apply_app_font：offscreen 下真改 QApplication 字号（pointSize 对得上）
  4. 设置页「界面缩放」下拉改动 → 落盘 + 全局字号变化 + 主题刷新链
     （_apply_theme / theme_changed 广播）被触发
"""

import json
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                  # noqa: E402

from src import theme as theme_mod                        # noqa: E402
from src.config import ConfigManager, DEFAULT_CONFIG      # noqa: E402
from src.settings_panel import SettingsPanel              # noqa: E402
from src.theme import (BASE_FONT_PT, UI_SCALE_VALUES,     # noqa: E402
                       apply_app_font, scaled_font_pt)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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


# ====================================================================
# 缩放纯函数
# ====================================================================
class TestScaledFontPt:
    def test_all_official_steps(self):
        """五个合法档位的换算（基准 10pt；round half-to-even：85→8、115→12）"""
        assert scaled_font_pt(100) == BASE_FONT_PT == 10
        # 具体值钉死（函数内是 BASE_FONT_PT * scale / 100，不重组浮点表达式）
        assert [scaled_font_pt(s) for s in UI_SCALE_VALUES] == [8, 10, 12, 13, 15]

    def test_monotonic_and_positive(self):
        pts = [scaled_font_pt(s) for s in range(85, 151)]
        assert all(p > 0 for p in pts)
        assert pts == sorted(pts)               # 缩放越大字号不回缩


def _tmp_path(name: str) -> str:
    """轻量临时文件路径（mkdtemp 建目录，失败由系统临时目录兜底）"""
    d = tempfile.mkdtemp(prefix="fp_ui_scale_")
    return os.path.join(d, name)


# ====================================================================
# config 新键：类型 / 范围 / 非法值回退
# ====================================================================
class TestConfigUiScaleKeys:
    def test_defaults(self):
        assert DEFAULT_CONFIG["ui_scale"] == 100
        assert DEFAULT_CONFIG["first_run_done"] is False

    def test_set_rejects_out_of_range_and_wrong_type(self):
        cm = ConfigManager(_tmp_path("guard.json"))
        assert cm.set("ui_scale", "大") is False       # 类型错误
        assert cm.set("ui_scale", 84) is False         # 低于下限
        assert cm.set("ui_scale", 151) is False        # 高于上限
        assert cm.set("ui_scale", 130) is True         # 合法档位放行
        assert cm.get("ui_scale") == 130

    def test_load_falls_back_on_illegal_values(self):
        """config.json 里的垃圾值（越界 / 类型错）→ 该键回退默认"""
        path = _tmp_path("bad.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ui_scale": 300, "first_run_done": "yes"}, f)
        cm = ConfigManager(path)
        assert cm.get("ui_scale") == 100
        assert cm.get("first_run_done") is False

    def test_load_accepts_legal_values(self):
        path = _tmp_path("good.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ui_scale": 115, "first_run_done": True}, f)
        cm = ConfigManager(path)
        assert cm.get("ui_scale") == 115
        assert cm.get("first_run_done") is True


# ====================================================================
# apply_app_font：offscreen 真改 QApplication 字号
# ====================================================================
class TestApplyAppFont:
    def test_font_point_size_follows_scale(self, qapp):
        try:
            pt = apply_app_font(130)
            assert pt == theme_mod.scaled_font_pt(130)
            assert QApplication.instance().font().pointSize() == pt
        finally:
            apply_app_font(100)             # 还原，不污染同进程其它用例

    def test_scale_150_grows_font(self, qapp):
        try:
            apply_app_font(100)
            base = QApplication.instance().font().pointSize()
            apply_app_font(150)
            assert QApplication.instance().font().pointSize() > base
        finally:
            apply_app_font(100)


# ====================================================================
# 设置页「界面缩放」行：改动 → 落盘 + 字号 + 主题刷新链
# ====================================================================
@pytest.fixture(scope="module")
def panel(qapp):
    calls = {"theme": 0, "emitted": []}
    host = types.SimpleNamespace(
        _config=_FakeConfig(),
        current_theme="dark",
        _theme="dark",
        _apply_theme=lambda: calls.__setitem__("theme", calls["theme"] + 1),
        theme_changed=types.SimpleNamespace(
            emit=lambda name: calls["emitted"].append(name)),
    )
    return SettingsPanel(host), calls


class TestSettingsUiScaleRow:
    def test_combo_has_official_steps(self, panel):
        p, _ = panel
        combo = p._set_ui_scale
        labels = [combo.itemText(i) for i in range(combo.count())]
        assert labels == [f"{s}%" for s in UI_SCALE_VALUES]
        assert [combo.itemData(i) for i in range(combo.count())] \
            == list(UI_SCALE_VALUES)

    def test_change_applies_font_and_theme_chain(self, panel, qapp):
        p, calls = panel
        try:
            idx_130 = p._set_ui_scale.findData(130)
            assert idx_130 >= 0
            p._set_ui_scale.setCurrentIndex(idx_130)   # 触发 currentIndexChanged
            assert p._config.get("ui_scale") == 130
            assert QApplication.instance().font().pointSize() \
                == scaled_font_pt(130)
            assert calls["theme"] >= 1                 # _apply_theme 被走
            assert calls["emitted"][-1] == "dark"      # 广播解析后的主题名
        finally:
            apply_app_font(100)

    def test_replay_button_exists(self, panel):
        p, _ = panel
        assert hasattr(p, "_onboard_btn")
        assert "重看引导" in p._onboard_btn.text()
