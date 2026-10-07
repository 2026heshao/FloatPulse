# -*- coding: utf-8 -*-
"""动效 token 与时长口径（motion）单元测试 —— 纯逻辑，无需 Qt / offscreen。

覆盖（UI 强化方案 A1）：
  1. token 键完整性与取值约束（MOTION / EASE）
  2. 缩放口径唯一性：``duration(ms, speed) == max(0, int(ms / speed))``，
     并与既有 verify_nav_drag / qa_verify_nav_drag 的硬断言（110 / 220 / 440）对齐
  3. clamp：非法 / 非正 / NaN / inf 档位回退中性档 1.0；非正时长归零
  4. 缓动名合法性（必须是 Qt ``QEasingCurve.Type`` 的真实成员名）
  5. 结构约束：模块不得引入 PyQt6（与 nav_layout / md_export 同款铁律）
  6. 接线回归：三处时长缩放必须已改走 motion.duration（不再是散落字面量）
"""

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import motion  # noqa: E402

V4_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Qt 6 QEasingCurve.Type 的成员名白名单（只列本项目可能用到的子集，够断言即可）
QT_EASING_NAMES = {
    "Linear", "InQuad", "OutQuad", "InOutQuad",
    "InCubic", "OutCubic", "InOutCubic",
    "InQuart", "OutQuart", "InOutQuart",
    "InQuint", "OutQuint", "InOutQuint",
    "InSine", "OutSine", "InOutSine",
    "InExpo", "OutExpo", "InOutExpo",
    "InBack", "OutBack", "InOutBack",
}

# 三处时长缩放接线点（A1 收敛目标）
# ★ 2026-10-05（T03 D2 拆分随迁）：card_window 的页面家族（任务勾选动画等
#   motion.duration 调用方）整体迁入 src/card_window_pages.py，钉子跟随
#   代码迁址；main_window 的 _nav_anim_ms 唯一入口保持原位钉死。
WIRED_SITES = ("src/card_window_pages.py", "src/tasks_panel.py",
               "src/main_window.py")


# ====================================================================
# 1. token 键完整性与取值约束
# ====================================================================
class TestTokens:
    def test_motion_keys_complete(self):
        """约定键一个都不能少（新增动效按语义取档，不允许各自造数）"""
        assert set(motion.MOTION) == {
            "instant", "fast", "base", "slow", "stagger",
            # 启动链路（高仿真设计稿 2026-10-03 方案 D）
            "spark_fly", "progress_tween", "wake_pop", "wake_halo",
            "splash_out", "splash_hold", "enter_fade", "enter_rise",
            "ball_delay", "ball_pop",
            # 轻提示气泡（2026-10-06 重设计，src/toast.py；toast_move =
            # 存活气泡平移补间，R9 与堆叠补位共用）
            "toast_in", "toast_out", "toast_stagger", "toast_move",
            # 按钮交互反馈系统化升级（2026-10-07 规格文档 §6，controls.py）
            "press_out", "focus_ring",
        }

    def test_button_feedback_token_values(self):
        """按钮交互反馈批新增 token 定稿值（规格文档 §6 表）：改值 =
        动效设计变更，必须过规格评审。"""
        assert motion.MOTION["press_out"] == 150, "松手回弹应比 fast(120) 略长"
        assert motion.MOTION["focus_ring"] == 100
        assert motion.MOTION["press_out"] > motion.MOTION["fast"]

    def test_motion_values_are_non_negative_ints(self):
        for key, value in motion.MOTION.items():
            assert isinstance(value, int), f"{key} 应为 int，实为 {type(value)}"
            assert value >= 0, f"{key} 不应为负：{value}"

    def test_instant_is_zero(self):
        """instant 必须是 0：调用侧据此跳过动画注册"""
        assert motion.MOTION["instant"] == 0

    def test_motion_durations_ordered(self):
        """语义档位应递增：fast < base < slow（避免有人把档位写反）"""
        assert motion.MOTION["fast"] < motion.MOTION["base"] < motion.MOTION["slow"]

    def test_ease_keys_complete(self):
        assert set(motion.EASE) == {"out", "out_quint", "in_out",
                                    "in", "out_back"}

    def test_ease_values_are_valid_qt_names(self):
        """缓动必须是 Qt QEasingCurve.Type 的真实成员名（拼错会 getattr 崩）"""
        for key, name in motion.EASE.items():
            assert isinstance(name, str) and name, f"{key} 缓动名应为非空字符串"
            assert name in QT_EASING_NAMES, f"{key} → {name} 不是合法 Qt 缓动名"

    def test_speed_range_matches_config_range(self):
        """档位区间需与 config._CONFIG_RANGES['anim_speed'] 对齐"""
        config_src = (V4_ROOT / "src" / "config.py").read_text(encoding="utf-8")
        ast.parse(config_src)  # 语法自检：确认读到的是有效源码
        assert "anim_speed" in config_src
        assert (motion.SPEED_MIN, motion.SPEED_MAX) == (0.5, 2.0)


# ====================================================================
# 1.5 启动链路 token 钉死（高仿真设计稿 2026-10-03 方案 D 定稿值）
# ====================================================================
class TestStartupTokens:
    """方案 D 定稿值：数值改动 = 动效设计变更，必须过设计稿评审"""

    def test_startup_token_values(self):
        expected = {
            "spark_fly": 180,       # 光点环端→球心（InCubic 吸入）
            "progress_tween": 200,  # 光点到达后球/环插值
            "wake_pop": 220,        # 满格弹跳
            "wake_halo": 320,       # 满格光晕
            "splash_out": 240,      # 闪屏淡出
            "splash_hold": 120,     # 主窗 show → 闪屏淡出交接延迟
            "enter_fade": 200,      # 主窗入场淡入
            "enter_rise": 320,      # 主窗入场 16px 上浮
            "ball_delay": 160,      # 主窗 show → 悬浮球浮现延迟
            "ball_pop": 260,        # 悬浮球 OutBack 弹性浮现
        }
        for key, value in expected.items():
            assert motion.MOTION[key] == value, \
                f"{key} 应为 {value}ms，实为 {motion.MOTION[key]}ms"

    def test_startup_ease_values(self):
        assert motion.EASE["in"] == "InCubic"          # 光点吸入
        assert motion.EASE["out_back"] == "OutBack"    # 球浮现过冲

    def test_relay_ordering_holds(self):
        """接力节奏约束：交接延迟 < 球延迟，淡出短于入场上升——交叠不抢戏"""
        assert motion.MOTION["splash_hold"] < motion.MOTION["ball_delay"]
        assert motion.MOTION["splash_out"] < motion.MOTION["enter_rise"]


class TestStartupChainWiring:
    """启动链路动效接线：token 消费点必须走 motion（禁止字面量回流）"""

    def test_splash_uses_motion_tokens(self):
        src = (V4_ROOT / "src" / "splash.py").read_text(encoding="utf-8")
        for token in ("base", "fast", "spark_fly", "progress_tween",
                      "wake_pop", "wake_halo", "splash_out"):
            assert f'"{token}"' in src, f"splash.py 未消费 token {token}"

    def test_main_window_entrance_uses_enter_tokens(self):
        src = (V4_ROOT / "src" / "main_window.py").read_text(encoding="utf-8")
        assert 'motion.MOTION["enter_fade"]' in src
        assert 'motion.MOTION["enter_rise"]' in src

    def test_relay_wired_in_entrypoint(self):
        src = (V4_ROOT / "knowledge_ball.py").read_text(encoding="utf-8")
        assert "after_wake" in src                 # 苏醒 → show 主窗
        assert 'motion.MOTION["splash_hold"]' in src
        assert 'motion.MOTION["ball_delay"]' in src
        assert "play_startup_pop" in src           # 悬浮球压轴浮现


# ====================================================================
# 2. 缩放口径（唯一实现，跨模块对齐）
# ====================================================================
class TestDurationScaling:
    def test_neutral_speed_is_identity(self):
        for ms in (0, 1, 120, 150, 180, 220, 280, 1000):
            assert motion.duration(ms, 1.0) == ms

    def test_default_speed_is_neutral(self):
        """speed 缺省即中性档，便于新代码 `motion.duration(180)` 直用"""
        assert motion.duration(180) == 180

    def test_faster_speed_shortens(self):
        assert motion.duration(220, 2.0) == 110
        assert motion.duration(150, 2.0) == 75

    def test_slower_speed_lengthens(self):
        assert motion.duration(220, 0.5) == 440
        assert motion.duration(150, 0.5) == 300

    def test_parity_with_nav_drag_assertions(self):
        """与 tools/verify_nav_drag.py、qa_verify_nav_drag.py 的硬断言严格一致"""
        assert motion.duration(220, 1.0) == 220
        assert motion.duration(220, 2.0) == 110
        assert motion.duration(220, 0.5) == 440

    def test_truncates_not_rounds(self):
        """口径是 int() 截断（不是 round）：1.3x 下 220ms → 169ms 而非 169/170 抖"""
        assert motion.duration(220, 1.3) == 169
        assert motion.duration(150, 1.3) == 115

    def test_floats_and_strings_accepted(self):
        assert motion.duration("220", "2.0") == 110
        assert motion.duration(220.0, 2) == 110

    def test_result_is_int(self):
        assert isinstance(motion.duration(220, 1.3), int)


# ====================================================================
# 3. clamp / 边界
# ====================================================================
class TestClampAndBounds:
    @pytest.mark.parametrize("bad", [0, -1, -0.5, None, "abc", "", [], {}])
    def test_bad_speed_falls_back_to_neutral(self, bad):
        """非法或非正档位回退 1.0 —— 绝不允许「档位 0 → 时长爆炸」"""
        assert motion.sanitize_speed(bad) == 1.0
        assert motion.duration(150, bad) == 150

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_speed_falls_back(self, bad):
        assert motion.sanitize_speed(bad) == 1.0

    def test_tiny_positive_speed_is_floored(self):
        """极小正档位用 MIN_SPEED 兜底，防止除零溢出"""
        assert motion.sanitize_speed(1e-12) == motion.MIN_SPEED
        assert motion.duration(100, 1e-12) == 10000

    def test_speed_out_of_config_range_passes_through(self):
        """区间夹取属配置层职责，动效层不再夹一次（避免掩盖配置错误）"""
        assert motion.sanitize_speed(3.0) == 3.0
        assert motion.sanitize_speed(0.1) == 0.1

    def test_zero_and_negative_ms_clamp_to_zero(self):
        assert motion.duration(0) == 0
        assert motion.duration(-5) == 0

    def test_bad_ms_returns_zero(self):
        """时长入参也不是可信类型（配置/计算可能给 None）→ 归零而非崩"""
        assert motion.duration(None) == 0
        assert motion.duration("abc") == 0

    def test_result_never_negative(self):
        for speed in (0.5, 1.0, 2.0, 0, -3, None):
            assert motion.duration(150, speed) >= 0


# ====================================================================
# 4. eased_ms
# ====================================================================
class TestEasedMs:
    def test_lookup_and_scale(self):
        assert motion.eased_ms("base", 1.0) == motion.MOTION["base"]
        assert motion.eased_ms("base", 2.0) == motion.MOTION["base"] // 2
        assert motion.eased_ms("fast", 1.0) == 120

    def test_instant_token_is_zero(self):
        assert motion.eased_ms("instant", 0.5) == 0

    def test_unknown_token_raises(self):
        """拼错的 token 必须立刻暴露，不得静默回退默认时长"""
        with pytest.raises(KeyError):
            motion.eased_ms("basic")

    def test_speed_scaling_applies_to_all_tokens(self):
        for key in motion.MOTION:
            assert motion.eased_ms(key, 2.0) == motion.duration(
                motion.MOTION[key], 2.0)


# ====================================================================
# 5. 结构约束：模块保持零 Qt 依赖
# ====================================================================
class TestNoQtDependency:
    def test_module_does_not_import_pyqt6(self):
        """纯逻辑模块铁律：pytest 可直跑、reduce_motion 只需改这一处"""
        tree = ast.parse((V4_ROOT / "src" / "motion.py").read_text("utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "PyQt6" not in imported, f"motion.py 不应依赖 Qt，实导入：{imported}"

    def test_only_stdlib_imports(self):
        tree = ast.parse((V4_ROOT / "src" / "motion.py").read_text("utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert mods <= {"math"}, f"仅允许标准库，实导入：{mods}"


# ====================================================================
# 6. 接线回归：散落口径已收敛到 motion.duration
# ====================================================================
class TestWiringRegression:
    @pytest.mark.parametrize("rel", WIRED_SITES)
    def test_site_routes_through_motion(self, rel):
        src = (V4_ROOT / rel).read_text(encoding="utf-8")
        assert "motion" in src, f"{rel} 未接入 motion"
        assert "motion.duration(" in src, f"{rel} 未调用 motion.duration()"

    def test_old_scattered_formula_removed(self):
        """A1 的核心收益：散落的 ``/ max(0.01, ...)`` 缩放式必须清干净"""
        for rel in WIRED_SITES:
            src = (V4_ROOT / rel).read_text(encoding="utf-8")
            assert "/ max(0.01," not in src, f"{rel} 仍有散落缩放式"
            assert "CHECK_ANIM_MS / " not in src, f"{rel} 仍有散落缩放式"

    def test_main_window_keeps_nav_anim_ms_entry(self):
        """_nav_anim_ms 保留为唯一入口（多处调用 + verify 脚本依赖）"""
        src = (V4_ROOT / "src" / "main_window.py").read_text(encoding="utf-8")
        assert "def _nav_anim_ms(" in src


# ====================================================================
# 减弱动效总闸（#14，2026-10-01）
# ====================================================================
def test_reduce_motion_defaults_off_and_duration_normal():
    """默认关闭：duration/eased_ms 走正常缩放口径（既有断言的前提）。"""
    import importlib
    importlib.reload(motion)
    try:
        assert motion.reduce_motion() is False
        assert motion.duration(180, 1.0) == 180
        key = sorted(motion.MOTION)[0]
        assert motion.eased_ms(key, 2.0) == motion.duration(
            motion.MOTION[key], 2.0)
    finally:
        motion.set_reduce_motion(False)


def test_reduce_motion_on_collapses_all_durations_to_zero():
    """开启后一律 0（含脏输入、任意档位/倍速）——瞬显而非按比例缩短。"""
    motion.set_reduce_motion(True)
    try:
        for ms in (0, 18, 120, 180, 280, 440, "180", None, -5):
            assert motion.duration(ms, 1.0) == 0, ms
            assert motion.duration(ms, 0.5) == 0, ms
            assert motion.duration(ms, 2.0) == 0, ms
        for kind in motion.MOTION:
            assert motion.eased_ms(kind, 1.0) == 0, kind
    finally:
        motion.set_reduce_motion(False)


def test_reduce_motion_off_restores_scaling():
    """关闭后恢复正常缩放（开关可逆，设置页往返切换不残留）。"""
    motion.set_reduce_motion(True)
    motion.set_reduce_motion(False)
    assert motion.duration(180, 1.0) == 180
    assert motion.duration(280, 2.0) == 140


def test_reduce_motion_config_key_defaults_false_and_roundtrips(tmp_path):
    """config 三件套：默认 False、bool 归一、落盘往返。"""
    from src.config import ConfigManager
    cfg = ConfigManager(str(tmp_path / "config.json"))
    assert cfg.get("reduce_motion", False) is False
    cfg.set("reduce_motion", True)
    cfg.save()
    cfg2 = ConfigManager(str(tmp_path / "config.json"))
    assert cfg2.get("reduce_motion", False) is True
    # 脏值归一为 bool（TYPES 表的 bool 通道）
    cfg2.set("reduce_motion", "yes")
    assert isinstance(cfg2.get("reduce_motion"), bool)


def test_reduce_motion_not_in_ranges_table():
    """bool 不进 _CONFIG_RANGES（区间夹取只属数值档位，这是既定口径）。"""
    from src.config import _CONFIG_RANGES
    assert "reduce_motion" not in _CONFIG_RANGES


def test_settings_row_writes_config_and_flips_gate():
    """设置页行端到端：ToggleSwitch 翻转 → 配置落盘 + motion 总闸同步。"""
    from types import SimpleNamespace

    from src.settings_panel import SettingsPanel
    _app = None
    try:
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
    except RuntimeError:                                   # pragma: no cover
        pytest.skip("QApplication 不可用")
    _ = _app  # 引用留在调用方
    from src import motion as _motion

    emitted = []
    host = SimpleNamespace(
        theme_changed=SimpleNamespace(emit=lambda *a: None),
        anim_speed_changed=SimpleNamespace(emit=lambda v: emitted.append(v)),
        ball_size_changed=SimpleNamespace(emit=lambda v: None),
        current_theme="light", _theme="light",
        ui_scale_changed=SimpleNamespace(emit=lambda v: None),
        config=None,
    )
    # 复用 test_ui_scale 的替身思路：SettingsPanel 只依赖 config 的 get/set/save
    import tempfile
    from src.config import ConfigManager
    cfg = ConfigManager(str(tempfile.mkdtemp(prefix="fp_rm_") + "/c.json"))
    host.config = cfg

    class _CfgNS(SimpleNamespace):
        """host.config 需要的是对象属性形态（面板内多处 self._config.get）"""
    panel = SettingsPanel.__new__(SettingsPanel)   # 不走完整构造，只测处理器
    panel._config = cfg
    panel._host = host
    from src.controls import ToggleSwitch
    t = ToggleSwitch(checked=False)
    t.toggled.connect(lambda checked: panel._on_reduce_motion_changed(checked))
    t.setChecked(True)
    try:
        assert cfg.get("reduce_motion") is True
        assert _motion.reduce_motion() is True
    finally:
        _motion.set_reduce_motion(False)
