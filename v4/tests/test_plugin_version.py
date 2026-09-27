# -*- coding: utf-8 -*-
"""插件版本契约回归 —— manifest.api_version / min_app_version（P0-1）。

钉死的行为：
  A app_version 工具：parse_version / is_version_ge 的边界
  B manifest 缺字段 → 按 api_version=1 / min_app_version="" 处理（老插件零影响）
  C api_version 非 int / bool / <=0 → 拒载；> PLUGIN_API_VERSION → 拒载
  D min_app_version 格式非法 → 拒载；高于宿主 → 拒载；相等/更低 → 放行
  E 失败阶段：STAGE_VERSION_MISMATCH 有中文标签 + 修复建议
  F loader 集成：磁盘上声明 api_version=999 的插件不加载且进 _failed
本文件不 import PyQt6（纯逻辑 + 假注册表），无 GUI 可跑。
"""

import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.app_version import (                      # noqa: E402
    APP_VERSION, PLUGIN_API_VERSION, is_version_ge, parse_version,
)
from src.plugin_loader import (                    # noqa: E402
    FAIL_HINTS, STAGE_VERSION_MISMATCH, stage_label, validate_manifest,
)


def _manifest(**extra):
    data = {
        "id": "demo", "name": "演示", "version": "1.0.0", "entry": "plugin.py",
    }
    data.update(extra)
    return data


# ---------------- A：版本工具 ----------------
def test_parse_version_basic():
    assert parse_version("4.6.0") == (4, 6, 0)
    assert parse_version(" 1.2.3 ") == (1, 2, 3)
    assert parse_version("1") == (1,)


@pytest.mark.parametrize("bad", ["", "4.x", "4.6.0-rc1", "-1.0", "4..6", None, 460])
def test_parse_version_rejects_bad(bad):
    assert parse_version(bad) is None


def test_is_version_ge_semantics():
    assert is_version_ge("4.6.0", "4.6.0") is True      # 相等放行
    assert is_version_ge("4.6.0", "4.5.9") is True      # 更高放行
    assert is_version_ge("4.6.0", "4.6.1") is False     # 更低拒绝
    assert is_version_ge("4.6.0", "5.0.0") is False
    assert is_version_ge("4.6", "4.6.0") is True        # 段数不等价补齐


def test_is_version_ge_lenient_on_unparsable():
    """任一侧解析不了 → 放行（版本是可选的护栏，不该拦死加载路径）"""
    assert is_version_ge(APP_VERSION, "foo") is True
    assert is_version_ge("foo", "4.6.0") is True


# ---------------- B：缺字段的默认值 ----------------
def test_missing_version_fields_default():
    m, err = validate_manifest(_manifest())
    assert m is not None and err == ""
    assert m["api_version"] == 1
    assert m["min_app_version"] == ""


def test_none_treated_as_default():
    m, err = validate_manifest(_manifest(api_version=None, min_app_version=None))
    assert m is not None and err == ""
    assert m["api_version"] == 1 and m["min_app_version"] == ""


# ---------------- C：api_version 校验 ----------------
def test_api_version_equal_host_ok():
    m, err = validate_manifest(_manifest(api_version=PLUGIN_API_VERSION))
    assert m is not None and m["api_version"] == PLUGIN_API_VERSION


def test_api_version_above_host_rejected():
    m, err = validate_manifest(_manifest(api_version=PLUGIN_API_VERSION + 1))
    assert m is None and "api_version" in err


@pytest.mark.parametrize("bad", ["1", 0, -3, True, 1.5, []])
def test_api_version_invalid_rejected(bad):
    m, err = validate_manifest(_manifest(api_version=bad))
    assert m is None and "api_version" in err


# ---------------- D：min_app_version 校验 ----------------
def test_min_app_version_below_or_equal_ok():
    m, err = validate_manifest(_manifest(min_app_version="1.0.0"))
    assert m is not None and m["min_app_version"] == "1.0.0"


def test_min_app_version_equal_current_ok():
    m, err = validate_manifest(_manifest(min_app_version=APP_VERSION))
    assert m is not None and err == ""


def test_min_app_version_above_host_rejected():
    m, err = validate_manifest(_manifest(min_app_version="99.0.0"))
    assert m is None and "FloatPulse >=" in err


def test_min_app_version_bad_format_rejected():
    m, err = validate_manifest(_manifest(min_app_version="v4.next"))
    assert m is None and "点分数字" in err


def test_min_app_version_non_string_rejected():
    m, err = validate_manifest(_manifest(min_app_version=4.6))
    assert m is None and "字符串" in err


def test_min_app_version_blank_means_unlimited():
    m, err = validate_manifest(_manifest(min_app_version="   "))
    assert m is not None and m["min_app_version"] == ""


# ---------------- E：失败阶段文案 ----------------
def test_version_stage_has_label_and_hint():
    assert stage_label(STAGE_VERSION_MISMATCH) == "版本不匹配"
    assert FAIL_HINTS[STAGE_VERSION_MISMATCH].strip()


# ---------------- F：loader 集成 ----------------
_PLUGIN_SOURCE = '''
from src.plugin_api import BallPlugin


class Plugin(BallPlugin):
    id = "probe-ver"
    name = "probe"
    version = "1.0.0"

    def create_actions(self, ctx):
        return []
'''


def _write_plugin(tmp_path, folder, manifest_extra):
    d = tmp_path / folder
    d.mkdir()
    manifest = {"id": folder, "name": folder, "version": "1.0.0",
                "entry": "plugin.py", "actions": []}
    manifest.update(manifest_extra)
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "plugin.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
    return d


def _load(tmp_path):
    from src.plugin_api import ActionRegistry, PluginContext
    from src.plugin_loader import PluginLoader
    loader = PluginLoader(ActionRegistry(logger=None), PluginContext(logger=None),
                          plugins_dir=str(tmp_path))
    return loader, loader.load_all()


def test_loader_rejects_future_api_version(tmp_path):
    _write_plugin(tmp_path, "probe-ver", {"api_version": 999})
    loader, loaded = _load(tmp_path)
    assert loaded == []
    errors = loader.load_errors()
    assert len(errors) == 1
    assert errors[0].stage == STAGE_VERSION_MISMATCH


def test_loader_accepts_legacy_manifest(tmp_path):
    """老插件（无版本字段）必须零影响地加载成功"""
    _write_plugin(tmp_path, "probe-ver", {})
    loader, loaded = _load(tmp_path)
    assert [lp.plugin_id for lp in loaded] == ["probe-ver"]
    assert loaded[0].manifest["api_version"] == 1
