# -*- coding: utf-8 -*-
"""插件中心「插件设置」UI 护栏（2026-10-04）——设置按钮存在性 + 弹层表单行为。

覆盖任务书要求的三类护栏之一（UI 侧）：
  - 设置按钮存在性：声明了非空 settings 的插件卡片**有**「设置」按钮，
    没声明的**没有**（与能力三档同款声明式模型）；按钮必须是 IconButton
    （禁裸 QPushButton，test_plugin_button_feedback 同源纪律）
  - 弹层表单：控件类型与 schema 一一对应（bool→ToggleSwitch /
    int·float→Stepper / enum→QComboBox）、初始值来自磁盘生效值
  - 保存流：改值 → set_one 落盘 → ctx.settings_changed 收到改动 key 列表；
    取消丢弃；无改动不发信号
"""

import json
import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QComboBox  # noqa: E402

from src import plugin_settings                                    # noqa: E402
from src.controls import IconButton, Stepper, ToggleSwitch         # noqa: E402
from src.plugin_api import PluginContext                           # noqa: E402
from src.plugin_loader import LoadedPlugin, validate_manifest      # noqa: E402
from src.plugins_panel import (                                    # noqa: E402
    PluginsPanel, PluginSettingsDialog, build_plugin_settings_dialog,
)

_APP = None


@pytest.fixture(scope="module")
def qapp():
    """★ 模块级全局持有 QApplication（局部版被 GC 后 QPixmap qFatal）。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


_SETTINGS = [
    {"key": "max_results", "label": "最大返回数", "type": "int",
     "default": 20, "min": 1, "max": 100},
    {"key": "search_notes", "label": "搜索笔记", "type": "bool",
     "default": True},
    {"key": "engine", "label": "排序算法", "type": "enum",
     "default": "bm25", "choices": ["bm25", "tfidf"]},
    {"key": "threshold", "label": "阈值", "type": "float",
     "default": 0.5, "min": 0.0, "max": 1.0},
]


class _FakeHost:
    """最小宿主替身：面板 refresh 只读 plugin_loader 与 _config；
    弹层取主题只读 current_theme。"""

    class _Cfg:
        def get(self, k, d=None):
            return d

    current_theme = "light"

    def __init__(self):
        self.plugin_loader = None
        self._config = self._Cfg()

    @property
    def config(self):
        return self._config


def _make_lp(tmp_path, settings=None, plugin_id="demo", with_ctx=True):
    """构造 LoadedPlugin 形状的替身（manifest 经 validate_manifest 归一化）"""
    raw = {
        "id": plugin_id, "name": "演示插件", "version": "1.0.0",
        "entry": "main.py", "settings": settings if settings is not None else [],
        "actions": [{"id": f"{plugin_id}.act", "title": "做件事"}],
    }
    manifest, err = validate_manifest(raw)
    assert manifest is not None, err
    ctx = None
    if with_ctx:
        ctx = PluginContext(logger=None, data_dir_base=str(tmp_path),
                            plugin_id=plugin_id,
                            manifest_settings=tuple(manifest["settings"]))
    return LoadedPlugin(plugin_id, "演示插件", "1.0.0",
                        str(tmp_path / plugin_id),
                        types.SimpleNamespace(), manifest, ctx=ctx)


# ====================================================================
# 卡片上的「设置」按钮
# ====================================================================
class TestSettingsButtonOnCard:
    def test_card_with_settings_has_button(self, qapp, tmp_path):
        panel = PluginsPanel(_FakeHost())
        card = panel._make_card(_make_lp(tmp_path, _SETTINGS))
        setting_btns = [b for b in card.findChildren(IconButton)
                        if b.text() == "设置"]
        assert len(setting_btns) == 1
        # 反向验证：文本匹配到的确实是按钮控件（防 findChildren 空转假绿）
        assert isinstance(setting_btns[0], QPushButton)
        panel.deleteLater()
        card.deleteLater()

    def test_card_without_settings_has_no_button(self, qapp, tmp_path):
        panel = PluginsPanel(_FakeHost())
        card = panel._make_card(_make_lp(tmp_path, []))
        assert not [b for b in card.findChildren(QPushButton)
                    if b.text() == "设置"]
        panel.deleteLater()
        card.deleteLater()

    def test_click_opens_settings_dialog(self, qapp, tmp_path, monkeypatch):
        panel = PluginsPanel(_FakeHost())
        lp = _make_lp(tmp_path, _SETTINGS)
        opened = []
        monkeypatch.setattr(PluginSettingsDialog, "exec",
                            lambda self: opened.append(self._plugin_id))
        panel._on_open_plugin_settings(lp)
        assert opened == ["demo"]
        panel.deleteLater()

    def test_click_without_settings_is_noop(self, qapp, tmp_path):
        panel = PluginsPanel(_FakeHost())
        # 无设置项 → build 返回 None → 不弹窗（双保险路径）
        panel._on_open_plugin_settings(_make_lp(tmp_path, []))
        panel.deleteLater()


# ====================================================================
# 弹层表单
# ====================================================================
class TestSettingsDialogForm:
    def _dialog(self, tmp_path, settings=None):
        return build_plugin_settings_dialog(
            _FakeHost(), _make_lp(tmp_path, settings or _SETTINGS),
            data_dir_base=str(tmp_path))

    def test_build_rejects_plugin_without_settings(self, tmp_path):
        assert self._dialog_for(tmp_path, []) is None
        assert self._dialog_for(tmp_path, None) is None

    def _dialog_for(self, tmp_path, settings):
        return build_plugin_settings_dialog(
            _FakeHost(), _make_lp(tmp_path, settings),
            data_dir_base=str(tmp_path))

    def test_widget_types_match_schema(self, qapp, tmp_path):
        dlg = self._dialog(tmp_path)
        assert dlg is not None
        assert [e["key"] for e in dlg._entries] == \
            ["max_results", "search_notes", "engine", "threshold"]
        w = dlg._widgets
        assert isinstance(w["max_results"][0], Stepper)
        assert isinstance(w["search_notes"][0], ToggleSwitch)
        assert isinstance(w["engine"][0], QComboBox)
        assert isinstance(w["threshold"][0], Stepper)
        dlg.deleteLater()

    def test_initial_values_from_schema_defaults(self, qapp, tmp_path):
        dlg = self._dialog(tmp_path)
        assert dlg._widgets["max_results"][0].value() == 20
        assert dlg._widgets["search_notes"][0].isChecked() is True
        assert dlg._widgets["engine"][0].currentText() == "bm25"
        assert dlg._widgets["threshold"][0].value() == 50   # 内部值 = 0.5×100
        dlg.deleteLater()

    def test_initial_values_from_disk(self, qapp, tmp_path):
        plugin_settings.set_one("demo", _SETTINGS, "max_results", 66,
                                str(tmp_path))
        plugin_settings.set_one("demo", _SETTINGS, "search_notes", False,
                                str(tmp_path))
        plugin_settings.set_one("demo", _SETTINGS, "engine", "tfidf",
                                str(tmp_path))
        dlg = self._dialog(tmp_path)
        assert dlg._widgets["max_results"][0].value() == 66
        assert dlg._widgets["search_notes"][0].isChecked() is False
        assert dlg._widgets["engine"][0].currentText() == "tfidf"
        dlg.deleteLater()

    def test_getters_return_schema_typed_values(self, qapp, tmp_path):
        dlg = self._dialog(tmp_path)
        assert dlg._widgets["max_results"][1]() == 20          # int
        assert dlg._widgets["search_notes"][1]() is True       # bool
        assert dlg._widgets["engine"][1]() == "bm25"           # str
        assert dlg._widgets["threshold"][1]() == 0.5           # float
        assert isinstance(dlg._widgets["threshold"][1](), float)
        dlg.deleteLater()


# ====================================================================
# 保存 / 取消
# ====================================================================
class TestSettingsDialogSave:
    def _saved_json(self, tmp_path):
        path = plugin_settings.settings_file(str(tmp_path), "demo")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_save_writes_and_emits_signal(self, qapp, tmp_path):
        lp = _make_lp(tmp_path, _SETTINGS)
        dlg = build_plugin_settings_dialog(_FakeHost(), lp,
                                           data_dir_base=str(tmp_path))
        seen = []
        sig = getattr(lp.ctx, "settings_changed", None)
        getattr(sig, "connect")(seen.append)
        dlg._widgets["max_results"][0].setValue(88)
        dlg._widgets["engine"][0].setCurrentText("tfidf")
        dlg._on_save()
        assert self._saved_json(tmp_path)["max_results"] == 88
        assert seen == [["max_results", "engine"]]     # 改动 key 列表
        assert dlg.result() == dlg.DialogCode.Accepted
        dlg.deleteLater()

    def test_cancel_discards(self, qapp, tmp_path):
        dlg = build_plugin_settings_dialog(
            _FakeHost(), _make_lp(tmp_path, _SETTINGS),
            data_dir_base=str(tmp_path))
        dlg._widgets["max_results"][0].setValue(99)
        dlg.reject()
        assert not os.path.isfile(
            plugin_settings.settings_file(str(tmp_path), "demo"))

    def test_save_without_change_emits_nothing(self, qapp, tmp_path):
        lp = _make_lp(tmp_path, _SETTINGS)
        dlg = build_plugin_settings_dialog(_FakeHost(), lp,
                                           data_dir_base=str(tmp_path))
        seen = []
        getattr(lp.ctx.settings_changed, "connect")(seen.append)
        dlg._on_save()                       # 什么都没改
        assert seen == []
        # 未写盘：值 == default 时 set_one 不该被调
        assert not os.path.isfile(
            plugin_settings.settings_file(str(tmp_path), "demo"))
        dlg.deleteLater()

    def test_emit_skipped_when_ctx_missing(self, qapp, tmp_path):
        lp = _make_lp(tmp_path, _SETTINGS, with_ctx=False)
        dlg = build_plugin_settings_dialog(_FakeHost(), lp,
                                           data_dir_base=str(tmp_path))
        dlg._widgets["max_results"][0].setValue(77)
        dlg._on_save()                       # 无 ctx：只落盘，不发信号不炸
        assert self._saved_json(tmp_path)["max_results"] == 77
        dlg.deleteLater()

    def test_float_save_roundtrip(self, qapp, tmp_path):
        dlg = build_plugin_settings_dialog(
            _FakeHost(), _make_lp(tmp_path, _SETTINGS),
            data_dir_base=str(tmp_path))
        dlg._widgets["threshold"][0].setValue(75)    # 内部值 → 0.75
        dlg._on_save()
        assert self._saved_json(tmp_path)["threshold"] == 0.75
        dlg.deleteLater()
