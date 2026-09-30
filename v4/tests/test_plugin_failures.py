# -*- coding: utf-8 -*-
"""插件加载失败可感知 —— FailedPlugin / load_errors() / rescan() 回归。

背景：改造前所有失败路径都是 ``_warn(...) + return None``，失败项不进
``_loaded``，插件中心只能显示「共 0 个插件」。用户放进一个坏插件后无从判断
是目录没找到、manifest 写错、依赖被拒还是代码抛异常。

钉死的行为：
  A 九类失败阶段各自被正确归类（stage 常量与 panel 展示依赖它）
  B 每条失败都带非空原因 + 修复建议 + 可定位 path/folder
  C manifest 已解析的失败项携带 plugin_id / name
  D 成功与失败分别记录，互不干扰（load_all 返回值只含成功项）
  E 全部动作登记失败 → 记为 STAGE_REGISTER_FAILED
  F rescan 重置失败列表，且能加载新放入的插件（不累积陈旧项）
  G registry 属性只读暴露，供插件中心实现单插件启停
  H stage_label 覆盖全部阶段常量（新增阶段忘配中文标签会被抓出）
  I 失败记录与日志双通道：_fail 既写日志也结构化留存
"""

import json
import logging
import os
import sys
import uuid

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.plugin_api import ActionRegistry  # noqa: E402
from src.plugin_loader import (                                    # noqa: E402
    FAIL_HINTS, FailedPlugin, PluginLoader, STAGE_CREATE_ACTIONS_FAILED,
    STAGE_ENTRY_MISSING, STAGE_IMPORT_FAILED, STAGE_INSTANTIATE_FAILED,
    STAGE_MANIFEST_INVALID, STAGE_MANIFEST_READ, STAGE_NO_PLUGIN_CLASS,
    STAGE_REGISTER_FAILED, STAGE_REQUIRES_REJECTED, stage_label,
)


# ====================================================================
# 夹具
# ====================================================================
class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def env(tmp_path):
    records = []
    logger = logging.getLogger(f"fp_fail_test_{uuid.uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(_Capture(records))

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    registry = ActionRegistry(logger=logger)
    loader = PluginLoader(registry, ctx=None,
                          plugins_dir=str(plugins_dir), logger=logger)
    return {
        "dir": plugins_dir, "registry": registry, "loader": loader,
        "records": records,
    }


def write_plugin(root, folder, manifest, source=None, extra_files=None):
    """在 root/<folder>/ 下写一个插件包（manifest 可传 dict 或原始字符串）"""
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    text = manifest if isinstance(manifest, str) else json.dumps(
        manifest, ensure_ascii=False)
    (d / "manifest.json").write_text(text, encoding="utf-8")
    if source is not None:
        (d / "plugin.py").write_text(source, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        (d / name).write_text(content, encoding="utf-8")
    return d


_OK_SRC = """
from src.plugin_api import BallPlugin, BallAction
class A(BallAction):
    id = "%s"
    title = "动作"
    def run(self, ctx):
        pass
class P(BallPlugin):
    def create_actions(self, ctx):
        return [A()]
"""


def ok_manifest(pid, action_id=None, hotkey=None, requires=None):
    return {
        "id": pid, "name": pid, "version": "1.0", "entry": "plugin.py",
        "requires": requires or [],
        "actions": [{"id": action_id or f"{pid}.a", "title": "动作",
                     "hotkey": hotkey, "menu": True}],
    }


# ====================================================================
# A 九类失败阶段归类
# ====================================================================
def test_all_failure_stages_classified(env):
    root, loader = env["dir"], env["loader"]

    write_plugin(root, "e-read", "{ not json")
    write_plugin(root, "e-invalid", {"id": "e-invalid", "version": "1.0"})
    write_plugin(root, "e-req", ok_manifest("e-req", requires=["requests"]),
                 source="x=1\n")
    write_plugin(root, "e-entry", {
        "id": "e-entry", "name": "E", "version": "1.0",
        "entry": "nope.py", "requires": [], "actions": [],
    })
    write_plugin(root, "e-import", ok_manifest("e-import"),
                 source="raise RuntimeError('boom')\n")
    write_plugin(root, "e-noclass", ok_manifest("e-noclass"),
                 source="x = 1\n")
    write_plugin(root, "e-init", ok_manifest("e-init"), source="""
from src.plugin_api import BallPlugin
class P(BallPlugin):
    def __init__(self):
        raise ValueError('ctor boom')
    def create_actions(self, ctx):
        return []
""")
    write_plugin(root, "e-actions", ok_manifest("e-actions"), source="""
from src.plugin_api import BallPlugin
class P(BallPlugin):
    def create_actions(self, ctx):
        raise KeyError('actions boom')
""")
    write_plugin(root, "e-ok", ok_manifest("e-ok"),
                 source=_OK_SRC % "e-ok.a")

    loader.load_all()
    stages = {e.folder: e.stage for e in loader.load_errors()}

    assert stages["e-read"] == STAGE_MANIFEST_READ
    assert stages["e-invalid"] == STAGE_MANIFEST_INVALID
    assert stages["e-req"] == STAGE_REQUIRES_REJECTED
    assert stages["e-entry"] == STAGE_ENTRY_MISSING
    assert stages["e-import"] == STAGE_IMPORT_FAILED
    assert stages["e-noclass"] == STAGE_NO_PLUGIN_CLASS
    assert stages["e-init"] == STAGE_INSTANTIATE_FAILED
    assert stages["e-actions"] == STAGE_CREATE_ACTIONS_FAILED
    assert "e-ok" not in stages


# ====================================================================
# B / C 失败项携带完整可展示信息
# ====================================================================
def test_failed_entries_carry_actionable_info(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "broken", "{ not json")
    write_plugin(root, "net", ok_manifest("net", requires=["socket"]),
                 source="x=1\n")
    loader.load_all()

    errors = loader.load_errors()
    assert errors and all(isinstance(e, FailedPlugin) for e in errors)

    for e in errors:
        assert e.reason, "失败原因不能为空"
        assert e.hint, "修复建议不能为空（面板直接展示）"
        assert e.path and e.folder, "必须可定位"

    # manifest 已解析成功的那条带 plugin_id / name
    net = next(e for e in errors if e.folder == "net")
    assert net.plugin_id == "net" and net.name == "net"


def test_has_errors_reflects_state(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "good", ok_manifest("good"), source=_OK_SRC % "good.a")
    loader.load_all()
    assert loader.has_errors() is False
    write_plugin(root, "bad", "{ not json")
    loader.rescan()
    assert loader.has_errors() is True


# ====================================================================
# D 成功与失败互不干扰
# ====================================================================
def test_success_and_failure_are_separate_channels(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "good", ok_manifest("good"), source=_OK_SRC % "good.a")
    write_plugin(root, "bad", "{ not json")
    loaded = loader.load_all()

    assert [p.plugin_id for p in loaded] == ["good"]
    assert [e.folder for e in loader.load_errors()] == ["bad"]
    assert loader.registry.get("good.a") is not None


# ====================================================================
# E 动作全部登记失败
# ====================================================================
def test_zero_registered_actions_recorded_as_failure(env):
    root, loader = env["dir"], env["loader"]
    # 先占住目标 id，让后登记者全部被拒
    write_plugin(root, "first", ok_manifest("first", action_id="dup.a"),
                 source=_OK_SRC % "dup.a")
    loader.load_all()

    write_plugin(root, "second", ok_manifest("second", action_id="dup.a"),
                 source=_OK_SRC % "dup.a")
    loader.rescan()

    stages = {e.folder: e.stage for e in loader.load_errors()}
    assert stages.get("second") == STAGE_REGISTER_FAILED
    second = next(p for p in loader.loaded_plugins()
                  if p.plugin_id == "second")
    assert second.actions_ok == 0
    assert any("登记被拒" in w or "全局唯一" in w for w in second.warnings), \
        second.warnings


# ====================================================================
# F rescan
# ====================================================================
def test_rescan_loads_new_plugin_and_resets_errors(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "aa", ok_manifest("aa", hotkey="Ctrl+Alt+1"),
                 source=_OK_SRC % "aa.a")
    loader.load_all()
    assert len(loader.loaded_plugins()) == 1

    # rescan 后失败列表不应累积：先制造一个失败，再放一个成功
    write_plugin(root, "zz-bad", "{ not json")
    loader.rescan()
    assert [e.folder for e in loader.load_errors()] == ["zz-bad"]

    write_plugin(root, "bb", ok_manifest("bb", hotkey="Ctrl+Alt+2"),
                 source=_OK_SRC % "bb.a")
    loader.rescan()
    ids = sorted(p.plugin_id for p in loader.loaded_plugins())
    assert ids == ["aa", "bb"]
    assert [e.folder for e in loader.load_errors()] == ["zz-bad"]
    # 两个插件都应正常登记（不同热键）
    assert all(p.actions_ok == 1 for p in loader.loaded_plugins())


# ====================================================================
# G registry 只读暴露
# ====================================================================
def test_registry_property_exposed(env):
    assert isinstance(PluginLoader.registry, property)
    assert env["loader"].registry is env["registry"]


# ====================================================================
# H stage_label 全覆盖
# ====================================================================
def test_every_stage_has_label_and_hint():
    stages = (
        STAGE_MANIFEST_READ, STAGE_MANIFEST_INVALID, STAGE_REQUIRES_REJECTED,
        STAGE_ENTRY_MISSING, STAGE_IMPORT_FAILED, STAGE_NO_PLUGIN_CLASS,
        STAGE_INSTANTIATE_FAILED, STAGE_CREATE_ACTIONS_FAILED,
        STAGE_REGISTER_FAILED,
    )
    for s in stages:
        assert stage_label(s) != "加载失败", f"{s} 缺少专属中文标签"
        assert FAIL_HINTS.get(s), f"{s} 缺少修复建议"
    assert stage_label("unknown-stage") == "加载失败"     # 兜底


# ====================================================================
# I 双通道：日志 + 结构化
# ====================================================================
def test_fail_writes_both_log_and_structure(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "broken", "{ not json")
    loader.load_all()

    assert len(loader.load_errors()) == 1
    msgs = [r.getMessage() for r in env["records"]]
    assert any("manifest 读取失败" in m for m in msgs), msgs


# ====================================================================
# 额外：LoadedPlugin.warnings / actions_ok 存在（面板状态标签依赖）
# ====================================================================
def test_loaded_plugin_exposes_status_fields(env):
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "good", ok_manifest("good"), source=_OK_SRC % "good.a")
    loader.load_all()
    lp = loader.loaded_plugins()[0]
    assert lp.actions_ok == 1
    assert lp.warnings == []
    assert lp.registered is True


def test_manifest_drift_warning_collected(env):
    """manifest 与 create_actions 对不上 → 记进 warnings（此前只在日志里）"""
    root, loader = env["dir"], env["loader"]
    write_plugin(root, "drift", ok_manifest("drift", action_id="drift.declared"),
                 source=_OK_SRC % "drift.other")
    loader.load_all()
    lp = loader.loaded_plugins()[0]
    assert lp.actions_ok == 0
    assert any("未提供实现" in w for w in lp.warnings)
    assert any("未在 manifest.actions" in w for w in lp.warnings)
