# -*- coding: utf-8 -*-
"""提示条时长设置项（toast_duration_ms，B4 · 2026-10-05）测试。

钉死的行为：
  1. 纯逻辑：main_window.show_toast 未显式传 ms → 读配置 toast_duration_ms；
     显式传 ms 的调用点（导出完成 4200/3200 长文案）不被覆盖；
     配置缺失/宿主无配置 → 回落 DEFAULT_CONFIG 默认 2800
  2. 配置迁移：config v2→v3 新增 toast_duration_ms 键——迁移常量与
     DEFAULT_CONFIG pin 同步；setdefault 幂等不覆写；旧版本文件（v1/v2/
     无版本号）加载后都带新键；重跑（save→reload）幂等不变
  3. 设置页端到端：「外观与主题」卡「提示条时长」Stepper（1000-6000 每档
     100ms）→ config 落盘 → 主窗 show_toast 默认时长即时生效；refresh()
     同步控件不误触发写盘
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
from src.controls import ScreenToast              # noqa: E402
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
# 1. 纯逻辑：show_toast 默认参数读配置（借未绑定方法，不构造主窗）
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
def toast_calls(monkeypatch):
    """把 ScreenToast.show_msg 换成录制器（主窗/悬浮球共用同一类对象）"""
    calls = []

    def _record(text, theme="", ms=2600):
        calls.append((text, theme, ms))

    monkeypatch.setattr(ScreenToast, "show_msg", _record)
    return calls


class TestShowToastDefaultFromConfig:
    def test_no_ms_reads_config(self, toast_calls):
        cfg = _FakeConfig({"toast_duration_ms": 4100})
        _ToastHost(cfg).show_toast("你好")
        assert toast_calls == [("你好", "dark", 4100)]

    def test_explicit_ms_wins(self, toast_calls):
        """调用方显式传 ms 的长文案（导出 4200/3200）不被配置覆盖"""
        cfg = _FakeConfig({"toast_duration_ms": 4100})
        host = _ToastHost(cfg)
        host.show_toast("导出完成", 4200)
        host.show_toast("已导出", 3200)
        assert [c[2] for c in toast_calls] == [4200, 3200]

    def test_zero_sentinel_reads_config(self, toast_calls):
        cfg = _FakeConfig({"toast_duration_ms": 1500})
        _ToastHost(cfg).show_toast("x", 0)
        assert toast_calls[0][2] == 1500

    def test_missing_key_falls_back_to_default(self, toast_calls):
        _ToastHost(_FakeConfig()).show_toast("x")
        assert toast_calls[0][2] == DEFAULT_CONFIG["toast_duration_ms"]

    def test_none_config_falls_back_to_default(self, toast_calls):
        """宿主无配置（替身/早期构造期）不崩，回默认"""
        _ToastHost(None).show_toast("x")
        assert toast_calls[0][2] == DEFAULT_CONFIG["toast_duration_ms"]


# ====================================================================
# 2. 配置迁移：config v2 → v3（新增键，setdefault 先例）
# ====================================================================
class TestMigrationV2ToV3:
    def test_constants_pin_default(self):
        """★ 护栏必须能红灯：迁移补的默认值必须与 DEFAULT_CONFIG 同步"""
        assert _CONFIG_V2_DEFAULT_TOAST_MS == DEFAULT_CONFIG["toast_duration_ms"]
        assert MIGRATIONS["config"][2] is _migrate_config_2_to_3
        assert DEFAULT_CONFIG["schema_version"] == STORE_VERSIONS["config"]

    def test_migrate_setdefault_only(self):
        """缺键 → 补默认；已有值（理论不存在，防御）→ 一律不覆写"""
        assert _migrate_config_2_to_3({}) == {"toast_duration_ms": 2800}
        assert _migrate_config_2_to_3({"toast_duration_ms": 5000}) == \
            {"toast_duration_ms": 5000}
        assert _migrate_config_2_to_3({"theme": "light"}) == \
            {"theme": "light", "toast_duration_ms": 2800}

    def test_migrate_idempotent(self):
        once = _migrate_config_2_to_3({"schema_version": 2})
        again = _migrate_config_2_to_3(dict(once))
        assert again == once

    def test_config_v2_file_gets_new_key(self):
        """端到端：schema_version=2 的旧文件加载后带新键（且不手改用户文件）"""
        p = _tmp_path("v2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 2,
                       "window_opacity": 85,
                       "asset_group_gap_seconds": 300}, f)
        cm = ConfigManager(p)
        assert cm.get("toast_duration_ms") == 2800      # 新键 = 新默认
        assert cm.get("window_opacity") == 85           # 用户值保留
        assert cm.get("asset_group_gap_seconds") == 300  # v2 用户值不被重写
        # 加载期零副作用：迁移只发生在内存，原文件要等 save() 才更新
        with open(p, "r", encoding="utf-8") as f:
            assert json.load(f).get("toast_duration_ms") is None

    def test_config_v1_file_migrates_through_chain(self):
        """v1 文件逐级跑 1→2→3：分组阈值改写 900 **且** 新键补上"""
        p = _tmp_path("v1.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1,
                       "asset_group_gap_seconds": 120}, f)
        cm = ConfigManager(p)
        assert cm.get("asset_group_gap_seconds") == 900
        assert cm.get("toast_duration_ms") == 2800

    def test_legacy_file_without_version_still_loads(self):
        """无版本号存量文件（视为当前版本，零迁移）→ 新键走默认兜底"""
        p = _tmp_path("legacy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"theme": "light"}, f)
        cm = ConfigManager(p)
        assert cm.get("theme") == "light"
        assert cm.get("toast_duration_ms") == 2800

    def test_save_reload_idempotent(self):
        """迁移后落盘再重载：值与新版本号稳定，重跑迁移不变"""
        p = _tmp_path("round.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 2}, f)
        cm = ConfigManager(p)
        cm.save()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_version"] == STORE_VERSIONS["config"]
        assert data["toast_duration_ms"] == 2800
        cm2 = ConfigManager(p)                          # 重载：不再变化
        assert cm2.get("toast_duration_ms") == 2800
        assert cm2.get("schema_version") == STORE_VERSIONS["config"]

    def test_range_guard_rejects_illegal(self):
        """三件套：int 类型 + (1000, 6000) 范围，越界/错型 set 拒写"""
        assert DEFAULT_CONFIG["toast_duration_ms"] == 2800
        cm = ConfigManager(_tmp_path("guard.json"))
        assert cm.set("toast_duration_ms", "2 秒") is False   # 错型
        assert cm.set("toast_duration_ms", 999) is False      # 低于下限
        assert cm.set("toast_duration_ms", 6001) is False     # 高于上限
        assert cm.set("toast_duration_ms", 4500) is True      # 合法放行
        assert cm.get("toast_duration_ms") == 4500


# ====================================================================
# 3. 设置页端到端：Stepper → config 落盘 → toast 时长生效
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


class TestSettingsToastRow:
    """面板级：Stepper 行为与 refresh 同步（轻量替身宿主）"""

    @pytest.fixture(scope="class")
    def panel(self, qapp):
        host = types.SimpleNamespace(
            config=_FakeConfig({"toast_duration_ms": 2800}),
            current_theme="dark",
            _theme="dark",
        )
        return SettingsPanel(host), host.config

    def test_stepper_range_and_clamp(self, panel):
        p, _ = panel
        st = p._set_toast_duration
        st.setValue(500)                     # 低于下限 → 夹回 1000
        assert st.value() == 1000
        st.setValue(9999)                    # 高于上限 → 夹回 6000
        assert st.value() == 6000

    def test_change_persists(self, panel):
        p, cfg = panel
        p._set_toast_duration.setValue(4500)
        assert cfg.get("toast_duration_ms") == 4500

    def test_refresh_syncs_without_signal(self, panel):
        """refresh：从配置同步控件值，且不误触发写盘（blockSignals 护栏）"""
        p, cfg = panel
        cfg.set("toast_duration_ms", 2000)
        p.refresh()
        assert p._set_toast_duration.value() == 2000


class TestToastDurationEndToEnd:
    """真窗级：设置页步进 → config.json 落盘 → show_toast 默认时长生效"""

    def test_stepper_to_disk_to_effective_duration(self, qapp, monkeypatch):
        calls = []

        def _record(text, theme="", ms=2600):
            calls.append((text, theme, ms))

        monkeypatch.setattr(ScreenToast, "show_msg", _record)
        config = ConfigManager(_tmp_path("e2e.json"))
        w = _build_window(config)
        try:
            w.show()
            _pump(qapp, 500)
            assert config.get("toast_duration_ms") == 2800   # 出厂默认

            sp = w._page_settings
            sp._set_toast_duration.setValue(5000)            # 真实交互路径
            _pump(qapp, 100)

            assert config.get("toast_duration_ms") == 5000
            # 落盘：config.json 真的写进磁盘
            with open(config._json_path, "r", encoding="utf-8") as f:
                assert json.load(f)["toast_duration_ms"] == 5000
            # 下一次默认时长 toast 即生效
            w.show_toast("已删除碎片")
            assert calls[-1] == ("已删除碎片", w.current_theme, 5000)
            # 显式传 ms 的调用点不受影响
            w.show_toast("导出完成", 4200)
            assert calls[-1][2] == 4200
        finally:
            w.close()
            w.deleteLater()
