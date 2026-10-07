# -*- coding: utf-8 -*-
"""轻提示停留时长设置项测试（2026-10-06 重设计版）。

前身钉 B4 的 toast_duration_ms 单键；2026-10-06「轻提示」卡以
toast_duration 三档基准 + 语义倍率取代单键，本文件改钉三件事：

  1. 主窗转发：main_window.show_toast 全参数透传 ToastCenter.push——
     ``ms`` 显式传值不被覆盖；ms=0 哨兵交给 ToastCenter 按基准档位 +
     语义倍率定驻留（src/toast._resolve_lifetime，钉在 test_toast.py G 段）；
  2. 退役键兼容：config v2→v3 迁移**保留注册**（版本史不可改写），但
     toast_duration_ms 已从 DEFAULT_CONFIG 退役——迁移产物被 _load 忽略、
     下次 save() 自然脱落，存量文件零报错；
  3. 设置页端到端：「全局工具」页「轻提示」卡——底缘 Stepper（24-120
     每档 8）落盘、位置/时长分段点击落盘、refresh() 同步控件不误触发。
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

from PyQt6.QtWidgets import QApplication          # noqa: E402

from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.config import ConfigManager, DEFAULT_CONFIG  # noqa: E402
from src.docx_manager import DocxManager          # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.json_store import (MIGRATIONS, STORE_VERSIONS,  # noqa: E402
                            _CONFIG_V2_DEFAULT_TOAST_MS,
                            _migrate_config_2_to_3)
from src.main_window import MainWindow            # noqa: E402
from src.note_manager import NoteManager          # noqa: E402
from src.settings_panel import SettingsPanel      # noqa: E402
from src.task_manager import TaskManager          # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.toast import ToastCenter                 # noqa: E402


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
    d = tempfile.mkdtemp(prefix="fp_toast_dur_")
    return os.path.join(d, name)


def _pump(app, ms: int):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ====================================================================
# 1. 主窗转发：show_toast 全参数透传 ToastCenter.push（唯一入口）
# ====================================================================
class _ToastHost:
    """只带 config 的最小宿主，借 MainWindow.show_toast 未绑定方法"""

    show_toast = MainWindow.show_toast

    def __init__(self, config=None, theme="dark"):
        self._config = config
        self.current_theme = theme

    @property
    def config(self):
        return self._config


@pytest.fixture
def push_calls(monkeypatch):
    """把 ToastCenter.push 换成录制器（show_toast 的唯一出气口）"""
    calls = []

    def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(ToastCenter, "push", staticmethod(_record))
    return calls


class TestShowToastForwardsToPush:
    def test_plain_text_defaults(self, push_calls):
        _ToastHost(None).show_toast("你好")
        call = push_calls[0]
        assert call["title"] == "你好"
        assert call["kind"] == "neutral"
        assert call["ms"] == 0            # 哨兵交给 ToastCenter 按档位定驻留

    def test_explicit_ms_wins(self, push_calls):
        """调用方显式传 ms 的长文案（导出 4200/3200）原样透传"""
        host = _ToastHost(None)
        host.show_toast("导出完成", 4200)
        host.show_toast("已导出", 3200)
        assert [c["ms"] for c in push_calls] == [4200, 3200]

    def test_semantic_params_passthrough(self, push_calls):
        undo = lambda: None  # noqa: E731
        _ToastHost(None).show_toast("已删除", kind="accent", msg="「草稿」",
                                    action=("撤销", undo))
        call = push_calls[0]
        assert call["kind"] == "accent"
        assert call["msg"] == "「草稿」"
        assert call["action"] == ("撤销", undo)


# ====================================================================
# 2. 退役键兼容：config v2 → v3 迁移保留注册，键本身退役
# ====================================================================
class TestRetiredKeyCompat:
    def test_migration_still_registered(self):
        """版本史不可改写：v2→v3 迁移照常注册，历史默认值不被改动"""
        assert MIGRATIONS["config"][2] is _migrate_config_2_to_3
        assert _CONFIG_V2_DEFAULT_TOAST_MS == 2800   # 历史值（键退役后无人读）
        assert DEFAULT_CONFIG["schema_version"] == STORE_VERSIONS["config"]

    def test_key_retired_from_default_config(self):
        """2026-10-06 起键退役：DEFAULT_CONFIG / 新配置文件不再有它"""
        assert "toast_duration_ms" not in DEFAULT_CONFIG
        cm = ConfigManager(_tmp_path("fresh.json"))
        assert cm.get("toast_duration_ms") is None

    def test_migrate_function_pure_setdefault(self):
        """迁移函数本身不变：缺键补历史默认、已有值不覆写（幂等）"""
        assert _migrate_config_2_to_3({}) == \
            {"toast_duration_ms": _CONFIG_V2_DEFAULT_TOAST_MS}
        assert _migrate_config_2_to_3({"toast_duration_ms": 5000}) == \
            {"toast_duration_ms": 5000}

    def test_v2_file_loads_clean_and_key_drops_on_save(self):
        """端到端：v2 存量文件零报错加载；迁移产物被 _load 忽略，
        下次 save() 自然脱落（用户值不受影响）"""
        p = _tmp_path("v2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 2,
                       "window_opacity": 85,
                       "asset_group_gap_seconds": 300}, f)
        cm = ConfigManager(p)
        assert cm.load_reset_reason is None
        assert cm.get("toast_duration_ms") is None      # 退役键不再读取
        assert cm.get("window_opacity") == 85           # 用户值保留
        assert cm.get("asset_group_gap_seconds") == 300
        cm.save()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "toast_duration_ms" not in data          # 落盘时脱落
        assert data["window_opacity"] == 85

    def test_legacy_file_without_version_still_loads(self):
        """无版本号存量文件（视为当前版本，零迁移）照常加载"""
        p = _tmp_path("legacy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"theme": "light"}, f)
        cm = ConfigManager(p)
        assert cm.get("theme") == "light"
        assert cm.get("toast_duration_ms") is None

    def test_new_settings_keys_present_after_load(self):
        """替代键就位：7 键「轻提示」卡配置随默认值兜底（R12）"""
        cm = ConfigManager(_tmp_path("newkeys.json"))
        assert cm.get("toast_enabled") is True
        assert cm.get("toast_duration") == "standard"


# ====================================================================
# 3. 设置页端到端：「轻提示」卡（全局工具页）行控件 → config 落盘
# ====================================================================
def _build_window(config: ConfigManager) -> MainWindow:
    """与 test_window_opacity.py 同款的真实施工配方（临时目录数据）"""
    tmp = tempfile.mkdtemp(prefix="fp_toast_e2e_")
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


class TestSettingsToastCard:
    """面板级：底缘 Stepper 行为与 refresh 同步（轻量替身宿主）"""

    @pytest.fixture(scope="class")
    def panel(self, qapp):
        host = types.SimpleNamespace(
            config=_FakeConfig(),
            current_theme="dark",
            _theme="dark",
        )
        return SettingsPanel(host), host.config

    def test_stepper_range_and_clamp(self, panel):
        p, _ = panel
        st = p._set_toast_bottom
        st.setValue(10)                      # 低于下限 → 夹回 24
        assert st.value() == 24
        st.setValue(999)                     # 高于上限 → 夹回 120
        assert st.value() == 120

    def test_change_persists(self, panel):
        p, cfg = panel
        p._set_toast_bottom.setValue(88)
        assert cfg.get("toast_bottom_offset") == 88
        p._set_toast_max.setValue(5)
        assert cfg.get("toast_max_visible") == 5

    def test_refresh_syncs_without_signal(self, panel):
        """refresh：从配置同步控件值，且不误触发写盘（blockSignals 护栏）"""
        p, cfg = panel
        cfg.set("toast_bottom_offset", 104)
        cfg.set("toast_position", "corner")
        cfg.set("toast_duration", "relaxed")
        cfg.set("toast_max_visible", 2)
        cfg.set("toast_sound", True)
        p.refresh()
        assert p._set_toast_bottom.value() == 104
        assert p._toast_pos_btns["corner"].isChecked()
        assert not p._toast_pos_btns["center"].isChecked()
        assert p._toast_dur_btns["relaxed"].isChecked()
        assert p._set_toast_max.value() == 2
        assert p._set_toast_sound.isChecked()
        # 恢复默认值再刷一遍：分段选中态跟着走
        cfg.set("toast_position", "center")
        p.refresh()
        assert p._toast_pos_btns["center"].isChecked()


class TestToastCardEndToEnd:
    """真窗级：分段点击 → config.json 落盘 → 新值就绪"""

    def test_segment_click_persists_to_disk(self, qapp):
        config = ConfigManager(_tmp_path("e2e.json"))
        w = _build_window(config)
        try:
            w.show()
            _pump(qapp, 500)
            sp = w._page_settings
            assert config.get("toast_position") == "center"    # 出厂默认
            sp._toast_pos_btns["corner"].click()               # 真实交互路径
            _pump(qapp, 100)
            assert config.get("toast_position") == "corner"
            sp._toast_dur_btns["brief"].click()
            _pump(qapp, 100)
            assert config.get("toast_duration") == "brief"
            # 落盘：config.json 真的写进磁盘
            with open(config._json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["toast_position"] == "corner"
            assert data["toast_duration"] == "brief"
        finally:
            w.close()
            w.deleteLater()
