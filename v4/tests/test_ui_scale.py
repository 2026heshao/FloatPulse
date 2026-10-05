# -*- coding: utf-8 -*-
"""界面缩放 / 字号（成熟化 3.5）单元测试。

钉住的行为：
  1. 缩放纯函数 scaled_font_pt：各合法档位的换算正确
  2. config 新键：ui_scale（int，85-130；上限 2026-10-05 由 150 收窄，
     150% 档设置页步进器溢出）与 first_run_done（bool）的
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
        """各合法档位的换算（基准 10pt；round half-to-even：85→8、115→12）"""
        assert scaled_font_pt(100) == BASE_FONT_PT == 10
        # 具体值钉死（函数内是 BASE_FONT_PT * scale / 100，不重组浮点表达式）
        # 150 已从合法档位移除（2026-10-05 收窄），但纯函数对它的换算仍钉住
        assert [scaled_font_pt(s) for s in UI_SCALE_VALUES] == [8, 10, 12, 13]
        assert scaled_font_pt(150) == 15

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
        assert cm.set("ui_scale", 150) is False        # ★ 高于收窄后的上限
        assert cm.set("ui_scale", 151) is False        # 高于旧上限（历史用例）
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

    def test_load_falls_back_for_stored_150_after_range_narrowing(self):
        """★ 2026-10-05 收窄护栏：已存 150 的老配置加载 → 回落默认 100。

        只收窄 RANGE 不动默认值（不走版本化迁移）——老用户不静默钳到
        130，而是按既有「越界回落默认」策略回 100，需重新选择档位。
        """
        path = _tmp_path("old150.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ui_scale": 150}, f)
        cm = ConfigManager(path)
        assert cm.get("ui_scale") == 100

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
        config=_FakeConfig(),
        current_theme="dark",
        _theme="dark",
        reapply_theme=lambda: calls.__setitem__("theme", calls["theme"] + 1),
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


# ====================================================================
# P1-3（2026-10-04）：QSS 写死 px 字号随档位缩放
# ====================================================================
from src.theme import (current_ui_scale, get_card_window_qss,   # noqa: E402
                       get_main_window_qss, get_menu_qss, scale_px_fonts,
                       set_ui_scale)


class TestScalePxFonts:
    def test_scale_100_is_byte_identical(self):
        qss = "QLabel { font-size: 13px; padding: 0 11px; }"
        assert scale_px_fonts(qss, 100) == qss
        assert scale_px_fonts(qss) == qss            # 缺省 = 当前档位（100）

    def test_all_font_sizes_scale_layout_px_untouched(self):
        qss = ("QLabel#a { font-size: 13px; }\n"
               "QLabel#b { font-size: 11px; padding: 0 13px; }\n"
               "QFrame#c { border: 1px solid x; border-radius: 3px;\n"
               "           font-size: 15px; margin: 10px 2px; }")
        out = scale_px_fonts(qss, 85)
        assert "font-size: 11px" in out              # 13*0.85=11.05→11
        assert "font-size: 9px" in out               # 11*0.85=9.35→9
        assert "font-size: 13px" in out              # 15*0.85=12.75→13
        assert "padding: 0 13px" in out              # 布局 px 不动
        assert "border-radius: 3px" in out
        assert "margin: 10px 2px" in out

    def test_scale_150_values(self):
        """150% 取值钉死（round half-to-even：19.5→20、22.5→22、16.5→16）"""
        assert scale_px_fonts("font-size: 13px", 150) == "font-size: 20px"
        assert scale_px_fonts("font-size: 15px", 150) == "font-size: 22px"
        assert scale_px_fonts("font-size: 11px", 150) == "font-size: 16px"

    def test_floor_at_1px_and_garbage_scale(self):
        assert "font-size: 1px" in scale_px_fonts("font-size: 1px", 85)
        assert scale_px_fonts("font-size: 13px", None) == \
            scale_px_fonts("font-size: 13px")        # None → 当前档位
        assert scale_px_fonts("font-size: 13px", "大") == "font-size: 13px"
        assert scale_px_fonts("", 150) == ""

    def test_multi_occurrence_all_replaced(self):
        qss = "A{font-size:10px}B{font-size:10px}C{font-size: 10px}"
        out = scale_px_fonts(qss, 130)
        assert out.count("13px") == 3                # 10*1.3=13
        assert "10px" not in out


class TestQssGettersScale:
    def test_100_percent_byte_identical_to_substitute(self, qapp):
        """★ 硬护栏：档位 100 时三个 getter 与裸 substitute 逐字节相等"""
        for getter, tpl in ((get_main_window_qss, theme_mod._QSS_MAIN_WINDOW),
                            (get_card_window_qss, theme_mod._QSS_CARD_WINDOW),
                            (get_menu_qss, theme_mod._QSS_MENU)):
            try:
                set_ui_scale(100)
                assert getter("dark") == tpl.substitute(
                    theme_mod.get_colors("dark"))
            finally:
                set_ui_scale(100)

    def test_scaled_qss_differs_and_restores(self, qapp):
        try:
            set_ui_scale(100)
            baseline = get_main_window_qss("dark")
            set_ui_scale(150)
            scaled = get_main_window_qss("dark")
            assert scaled != baseline
            assert "font-size: 20px" in scaled       # 13px→20
            assert "font-size: 10px" not in scaled   # 全部被换算过
            set_ui_scale(100)
            assert get_main_window_qss("dark") == baseline
        finally:
            set_ui_scale(100)

    def test_apply_app_font_registers_scale(self, qapp):
        try:
            apply_app_font(130)
            assert current_ui_scale() == 130
            qss = get_main_window_qss("dark")
            assert "font-size: 17px" in qss          # 13px@130% = 16.9→17
        finally:
            apply_app_font(100)
        assert current_ui_scale() == 100

    def test_menu_and_card_qss_scale_too(self, qapp):
        """菜单模板只有 13px 一档（85%→11px）；卡片模板 10–17px 全换算"""
        try:
            set_ui_scale(85)
            assert "font-size: 11px" in get_menu_qss("dark")     # 13px→11
            assert "font-size: 11px" in get_card_window_qss("dark")  # 13→11
            assert "font-size: 8px" in get_card_window_qss("dark")   # 10→8.5→8
        finally:
            set_ui_scale(100)


class TestBallBadgeFont:
    def test_badge_px_follows_scale(self):
        """球体徽标字号随档位（round half-to-even：16.5→16、14.3→14）"""
        import knowledge_ball
        assert knowledge_ball.badge_font_px(100) == 11       # 基准不变
        assert knowledge_ball.badge_font_px(85) == 9         # 9.35→9
        assert knowledge_ball.badge_font_px(130) == 14       # 14.3→14
        assert knowledge_ball.badge_font_px(150) == 16       # 16.5→16
        assert knowledge_ball.badge_font_px("大") == 11      # 脏值回基准
        assert knowledge_ball.badge_font_px() == 11          # 缺省读当前档位

    def test_badge_floor(self):
        import knowledge_ball
        assert knowledge_ball.badge_font_px(1) == 9          # 下限 9px


# ====================================================================
# B2（2026-10-05）：scale_px 唯一换算点 + 未换算字号护栏
# ====================================================================
import re                                                        # noqa: E402

from src.theme import scale_px                                   # noqa: E402

# 与 theme._FONT_SIZE_PX_RE 同源的字段抓取（护栏自扫描用，故意独立一份
# ——护栏要抓的是「最终产物里的字号」，不依赖实现内部那条正则的存在）
_FINAL_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(\d+)px")

# 全部成品 QSS 出口 × 模板（B2 护栏的扫描面：新增 QSS 出口必须登记进来）
_QSS_OUTLETS = (
    ("main_window", get_main_window_qss, "get_main_window_qss",
     lambda: theme_mod._QSS_MAIN_WINDOW),
    ("card_window", get_card_window_qss, "get_card_window_qss",
     lambda: theme_mod._QSS_CARD_WINDOW),
    ("menu", get_menu_qss, "get_menu_qss",
     lambda: theme_mod._QSS_MENU),
)


class TestScalePx:
    """唯一换算点：QSS 模板与自绘字号都必须走它（不许各写各的 round）。"""

    def test_official_steps(self):
        """五个合法档位的关键换算值钉死（half-to-even 基线，P1-3 同源）"""
        assert scale_px(13, 100) == 13
        assert scale_px(13, 85) == 11          # 11.05→11
        assert scale_px(13, 130) == 17         # 16.9→17
        assert scale_px(13, 150) == 20         # 19.5→20（half-to-even）
        assert scale_px(11, 150) == 16         # 16.5→16
        assert scale_px(15, 150) == 22         # 22.5→22

    def test_monotonic_and_floor(self):
        assert scale_px(10, 85) == 8           # 8.5→8（half-to-even）
        assert scale_px(1, 1) == 1             # 下限 1px（QSS 不收 0）
        assert scale_px(1, 150) == 2
        vals = [scale_px(13, s) for s in range(85, 151)]
        assert vals == sorted(vals), "档位越大字号不许回缩"

    def test_scale_px_fonts_routes_through_scale_px(self):
        """scale_px_fonts 的输出 = 逐处 scale_px（同源换算的回归钉）"""
        qss = "A{font-size:13px}B{font-size: 10px}C{font-size:17px}"
        for s in (85, 115, 130, 150):
            out = scale_px_fonts(qss, s)
            for src_v, out_v in zip(_FINAL_FONT_SIZE_RE.findall(qss),
                                    _FINAL_FONT_SIZE_RE.findall(out)):
                assert int(out_v) == scale_px(src_v, s)


class TestNoUnscaledFontSizes:
    """★ 护栏：成品 QSS 输出里不得再出现未换算的写死字号。

    做法：对每个 QSS 出口，先把模板 substitute 出「未缩放基准」，逐处
    记下写死字号；再按档位生成成品，逐处断言 = scale_px(原值, 档位)。
    任何一处漏换算（值没变）都会让序列错位当场变红——比"值集合相等"
    强得多：10px→15px 这类「缩放值撞上别的原值」也逃不掉。
    反向验证：把 scale_px_fonts 改成原样返回（不缩放）→ 本类全红。
    """

    @pytest.mark.parametrize("scale", [s for s in UI_SCALE_VALUES if s != 100])
    def test_every_font_size_is_scaled(self, qapp, scale):
        for _, getter, getter_name, tpl_of in _QSS_OUTLETS:
            template_qss = tpl_of().substitute(
                theme_mod.get_colors("dark"))
            src_values = _FINAL_FONT_SIZE_RE.findall(template_qss)
            assert src_values, \
                "%s 模板抓不到写死字号，护栏自检失败" % getter_name
            try:
                set_ui_scale(scale)
                out = getter("dark")
            finally:
                set_ui_scale(100)
            out_values = _FINAL_FONT_SIZE_RE.findall(out)
            assert len(out_values) == len(src_values), \
                "%s 输出字号处数变了" % getter_name
            expected = [scale_px(v, scale) for v in src_values]
            assert [int(v) for v in out_values] == expected, \
                "%s 在 %d%% 档存在未换算的写死字号" % (getter_name, scale)

    def test_all_scales_differ_from_baseline(self, qapp):
        """非 100 档的成品必须与基准不同（整条 QSS 级别的粗护栏）"""
        for _, getter, getter_name, _tpl in _QSS_OUTLETS:
            set_ui_scale(100)
            baseline = getter("dark")
            for scale in (85, 115, 130, 150):
                try:
                    set_ui_scale(scale)
                    assert getter("dark") != baseline, \
                        "%s 在 %d%% 档与基准逐字节相同=没缩放" % (
                            getter_name, scale)
                finally:
                    set_ui_scale(100)
