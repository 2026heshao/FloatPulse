# -*- coding: utf-8 -*-
"""窗口透明度设置（window_opacity，50-100%）单元测试。

钉死的行为：
  1. config 三件套：默认 100 / int / (50, 100)；越界与错型 set 拒写；
     config.json 垃圾值加载回退默认
  2. MainWindow._target_window_opacity：百分比 → 0.5~1.0 收敛，
     垃圾配置回 1.0（借未绑定方法在轻量替身上测，不构造主窗）
  3. show → 入场淡入终点 = 配置目标透明度（预设 70% → pump 后 ≈0.7）
  4. 设置页「窗口透明度」Stepper：范围 50-100 每档 5%，改动落盘 + 直调
     主窗 _apply_window_opacity；host 缺该方法时不崩（callable 守卫）
  5. 真窗：config 改值 → _apply_window_opacity 即时生效
  6. 恢复默认：透明度回 100% 且真窗即时回到不透明、Stepper 同步刷新
"""

import json
import os
import sys
import tempfile
import time
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                    # noqa: E402

from src.clipboard_monitor import ClipboardMonitor        # noqa: E402
from src.config import ConfigManager, DEFAULT_CONFIG      # noqa: E402
from src.docx_manager import DocxManager                  # noqa: E402
from src.fragment_manager import FragmentManager          # noqa: E402
from src.main_window import MainWindow                    # noqa: E402
from src.note_manager import NoteManager                  # noqa: E402
from src.settings_panel import SettingsPanel              # noqa: E402
from src.task_manager import TaskManager                  # noqa: E402
from src.temp_asset_manager import TempAssetManager       # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeConfig:
    """dict 兜底的配置替身：面板构建只需要 get / set / save"""

    def __init__(self, d=None):
        self._d = dict(d or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        pass


def _tmp_path(name: str) -> str:
    d = tempfile.mkdtemp(prefix="fp_win_opacity_")
    return os.path.join(d, name)


def _pump(app, ms: int):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ====================================================================
# 1. config 三件套
# ====================================================================
class TestConfigOpacityKeys:
    def test_defaults(self):
        assert DEFAULT_CONFIG["window_opacity"] == 100

    def test_set_rejects_out_of_range_and_wrong_type(self):
        cm = ConfigManager(_tmp_path("guard.json"))
        assert cm.set("window_opacity", "半透明") is False   # 类型错误
        assert cm.set("window_opacity", 49) is False         # 低于下限
        assert cm.set("window_opacity", 101) is False        # 高于上限
        assert cm.set("window_opacity", 70) is True          # 合法值放行
        assert cm.get("window_opacity") == 70

    def test_load_falls_back_on_illegal_values(self):
        path = _tmp_path("bad.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"window_opacity": 300}, f)
        cm = ConfigManager(path)
        assert cm.get("window_opacity") == 100

    def test_load_accepts_legal_values(self):
        path = _tmp_path("good.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"window_opacity": 55}, f)
        cm = ConfigManager(path)
        assert cm.get("window_opacity") == 55


# ====================================================================
# 2. _target_window_opacity 纯逻辑（借未绑定方法，不构造主窗）
# ====================================================================
class _OpacityHost:
    """只带 _config 的最小宿主，借 MainWindow 的未绑定方法"""

    _target_window_opacity = MainWindow._target_window_opacity

    def __init__(self, config):
        self._config = config


class TestTargetOpacity:
    @pytest.mark.parametrize("pct,expected", [
        (100, 1.0), (70, 0.7), (50, 0.5),
    ])
    def test_pct_to_fraction(self, pct, expected):
        host = _OpacityHost(_FakeConfig({"window_opacity": pct}))
        assert host._target_window_opacity() == pytest.approx(expected)

    def test_missing_key_defaults_opaque(self):
        host = _OpacityHost(_FakeConfig())
        assert host._target_window_opacity() == 1.0

    def test_garbage_config_falls_back_opaque(self):
        host = _OpacityHost(_FakeConfig({"window_opacity": "垃圾"}))
        assert host._target_window_opacity() == 1.0

    def test_out_of_range_clamped(self):
        """config 层已拦越界，这里钉住函数自身的二次收敛（防御纵深）"""
        host = _OpacityHost(_FakeConfig({"window_opacity": 10}))
        assert host._target_window_opacity() == pytest.approx(0.5)
        host._config.set("window_opacity", 999)
        assert host._target_window_opacity() == pytest.approx(1.0)


# ====================================================================
# 3. 真窗：入场淡入终点 = 配置目标（预设 70%）
# ====================================================================
def _build_window(config: ConfigManager) -> MainWindow:
    """与 test_side_split.py 同款的真实施工配方（临时目录数据）"""
    tmp = tempfile.mkdtemp(prefix="fp_opacity_test_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)
    return MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                      clip, temp_mgr)


class TestMainWindowOpacity:
    def test_show_fades_to_configured_target(self, qapp):
        config = ConfigManager(_tmp_path("w70.json"))
        config.set("window_opacity", 70)
        w = _build_window(config)
        try:
            assert w._target_window_opacity() == pytest.approx(0.7)
            w.show()
            _pump(qapp, 700)   # 入场 fade 200ms + slide 320ms 必然播完
            assert w.windowOpacity() == pytest.approx(0.7, abs=0.01)
            # 改配置 → 即时应用回不透明
            config.set("window_opacity", 100)
            w._apply_window_opacity()
            assert w.windowOpacity() == pytest.approx(1.0, abs=0.01)
        finally:
            w.close()
            w.deleteLater()

    def test_default_config_ends_opaque(self, qapp):
        config = ConfigManager(_tmp_path("w100.json"))
        w = _build_window(config)
        try:
            w.show()
            _pump(qapp, 700)
            assert w.windowOpacity() == pytest.approx(1.0, abs=0.01)
        finally:
            w.close()
            w.deleteLater()


# ====================================================================
# 4. 设置页「窗口透明度」行
# ====================================================================
@pytest.fixture(scope="module")
def panel(qapp):
    calls = {"applied": []}
    host = types.SimpleNamespace(
        _config=_FakeConfig({"window_opacity": 70}),
        current_theme="dark",
        _theme="dark",
        _apply_window_opacity=lambda: calls["applied"].append(True),
    )
    return SettingsPanel(host), calls


class TestSettingsOpacityRow:
    def test_stepper_range_and_initial_value(self, panel):
        p, _ = panel
        st = p._set_window_opacity
        st.setValue(30)                     # 低于下限 → 夹回 50
        assert st.value() == 50
        st.setValue(999)                    # 高于上限 → 夹回 100
        assert st.value() == 100
        st.setValue(70)
        assert st.value() == 70             # 构造初值来自配置（70）

    def test_change_persists_and_calls_host(self, panel):
        p, calls = panel
        n = len(calls["applied"])
        p._set_window_opacity.setValue(85)
        assert p._config.get("window_opacity") == 85
        assert len(calls["applied"]) == n + 1

    def test_same_value_no_rewrite(self, panel):
        """值未变化时不重复写配置（与其它步进器一致的省写口径）"""
        p, calls = panel
        p._config.set("window_opacity", 60)
        p._set_window_opacity.blockSignals(True)
        p._set_window_opacity.setValue(60)
        p._set_window_opacity.blockSignals(False)
        n = len(calls["applied"])
        p._set_window_opacity.setValue(60)
        assert p._config.get("window_opacity") == 60
        assert len(calls["applied"]) == n   # 同值不触发额外应用

    def test_host_without_method_does_not_crash(self, qapp):
        """host 缺 _apply_window_opacity 时静默跳过（防御测试替身/旧宿主）"""
        host = types.SimpleNamespace(
            _config=_FakeConfig({"window_opacity": 70}),
            current_theme="dark")
        p = SettingsPanel(host)
        p._set_window_opacity.setValue(90)
        assert p._config.get("window_opacity") == 90


# ====================================================================
# 5. 恢复默认：透明度回 100% + 真窗即时回到不透明
# ====================================================================
class TestResetRestoresOpacity:
    def test_reset_restores_opacity(self, qapp, monkeypatch):
        import src.settings_panel as sp_mod

        class _FakeMsgBox:
            """恢复默认确认框替身（GlassMessageBox.question 返回 bool）"""

            @staticmethod
            def question(*_a, **_k):
                return True

        config = ConfigManager(_tmp_path("reset.json"))
        config.set("window_opacity", 70)
        w = _build_window(config)
        try:
            w.show()
            _pump(qapp, 700)
            assert w.windowOpacity() == pytest.approx(0.7, abs=0.01)

            sp = w._page_settings
            monkeypatch.setattr(sp_mod, "GlassMessageBox", _FakeMsgBox)
            sp._on_reset_settings()
            _pump(qapp, 100)

            assert config.get("window_opacity") == 100
            assert sp._set_window_opacity.value() == 100   # Stepper 同步刷新
            assert w.windowOpacity() == pytest.approx(1.0, abs=0.01)
        finally:
            w.close()
            w.deleteLater()
