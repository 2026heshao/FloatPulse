# -*- coding: utf-8 -*-
"""插件受限写能力回归 —— ctx.write（P1-2）。

钉死的行为：
  A 权限门禁：未声明 write 的插件调三个方法 → 一律返回 0，provider 不被调用
  B 声明 write → 正常写入，返回值是 provider 给的 id
  C 内容护栏：空串 / 纯空白 / 非字符串 → 拒写返回 0；
    超长 → 截断到 MAX_CONTENT_LEN 后照写（不拒写）
  D 参数规整：add_fragment 的 source 缺省补 "插件:<id>"；
    add_task / add_note 的可选参数非字符串 → 空串
  E provider 异常 / 返回非数字 → 返回 0，不抛异常
  F manifest：capabilities 含 write 放行；未知能力名仍拒载
  G for_plugin 派生：写权限按派生时的声明逐实例判定，共享实例无权
  H 只增不改删：PluginWriter 上没有 update / delete 之类的方法
本文件不 import PyQt6（provider 是鸭子类型替身），无 GUI 可跑。
"""

import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.plugin_api import (                    # noqa: E402
    KNOWN_CAPABILITIES, PluginContext, PluginWriter,
)
from src.plugin_loader import validate_manifest  # noqa: E402


class _Sink:
    """记录调用的假 provider"""

    def __init__(self, ret=101):
        self.calls = []
        self.ret = ret

    def __call__(self, *args):
        self.calls.append(args)
        return self.ret


def _ctx(caps=(), providers=None, plugin_id="demo"):
    return PluginContext(
        logger=None, plugin_id=plugin_id, capabilities=caps,
        write_providers=providers or {}).for_plugin(plugin_id, "/x", caps)


def _providers():
    return {"fragment": _Sink(11), "task": _Sink(22), "note": _Sink(33)}


# ---------------- A：权限门禁 ----------------
def test_without_capability_all_writes_rejected():
    sinks = _providers()
    ctx = _ctx(caps=(), providers=sinks)
    assert ctx.write.add_fragment("嗨") == 0
    assert ctx.write.add_task("任务") == 0
    assert ctx.write.add_note("标题", "正文") == 0
    for s in sinks.values():
        assert s.calls == []                    # provider 一次都没被调用


def test_capability_check_is_per_instance():
    """共享宿主 ctx 无能力；同 provider 下派生实例才有权"""
    sinks = _providers()
    base = PluginContext(logger=None, write_providers=sinks)
    assert base.has_capability("write") is False
    assert base.write.add_fragment("x") == 0
    child = base.for_plugin("demo", "/x", ["write"])
    assert child.has_capability("write") is True
    assert child.write.add_fragment("x") == 11


def test_write_enabled_flag():
    assert _ctx(caps=[]).write.enabled() is False
    assert _ctx(caps=["write"]).write.enabled() is True


# ---------------- B：正常写入 ----------------
def test_fragment_write_returns_id_and_passes_source():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    assert ctx.write.add_fragment("要记住的事", source="手动") == 11
    assert sinks["fragment"].calls == [("要记住的事", "手动")]


def test_fragment_source_defaults_to_plugin_id():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks, plugin_id="weekly-report")
    ctx.write.add_fragment("片段")
    assert sinks["fragment"].calls == [("片段", "插件:weekly-report")]


def test_task_write_passes_three_args():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    assert ctx.write.add_task("交周报", note="附数据", deadline="2026-10-01") == 22
    assert sinks["task"].calls == [("交周报", "附数据", "2026-10-01")]


def test_note_write_argument_order_is_title_then_content():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    assert ctx.write.add_note("标题", "正文内容") == 33
    assert sinks["note"].calls == [("标题", "正文内容")]


# ---------------- C：内容护栏 ----------------
@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None, 123, ["x"]])
def test_blank_or_non_string_rejected(bad):
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    assert ctx.write.add_fragment(bad) == 0
    assert sinks["fragment"].calls == []


def test_long_content_truncated_not_rejected():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    long_text = "字" * (PluginWriter.MAX_CONTENT_LEN + 500)
    assert ctx.write.add_fragment(long_text) == 11
    written, _src = sinks["fragment"].calls[0]
    assert len(written) == PluginWriter.MAX_CONTENT_LEN


def test_content_is_stripped():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    ctx.write.add_fragment("  两边有空白  ")
    assert sinks["fragment"].calls[0][0] == "两边有空白"


# ---------------- D：可选参数宽容处理 ----------------
def test_optional_args_non_string_become_empty():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    ctx.write.add_task("任务", note=None, deadline=12345)
    assert sinks["task"].calls == [("任务", "", "")]
    ctx.write.add_note("标题", content=["不是字符串"])
    assert sinks["note"].calls == [("标题", "")]


def test_task_with_no_optional_args():
    sinks = _providers()
    ctx = _ctx(caps=["write"], providers=sinks)
    assert ctx.write.add_task("只有标题") == 22
    assert sinks["task"].calls == [("只有标题", "", "")]


# ---------------- E：异常与脏返回值隔离 ----------------
def test_provider_exception_isolated():
    def boom(*a):
        raise RuntimeError("db down")

    ctx = _ctx(caps=["write"], providers={"fragment": boom})
    assert ctx.write.add_fragment("x") == 0     # 不抛异常


@pytest.mark.parametrize("ret", [None, "不是数字", -5, 0, 3.7])
def test_provider_dirty_return_becomes_zero(ret):
    ctx = _ctx(caps=["write"], providers={"fragment": lambda *a: ret})
    assert ctx.write.add_fragment("x") == 0


def test_missing_provider_returns_zero():
    ctx = _ctx(caps=["write"], providers={"fragment": lambda *a: 1})
    assert ctx.write.add_task("任务") == 0       # task 通道未注入


def test_non_callable_provider_ignored():
    ctx = _ctx(caps=["write"], providers={"fragment": "不是函数"})
    assert ctx.write.add_fragment("x") == 0
    assert ctx.write.sources() == ()


def test_sources_lists_injected_channels():
    ctx = _ctx(caps=["write"], providers=_providers())
    assert ctx.write.sources() == ("fragment", "note", "task")


# ---------------- F：manifest 校验 ----------------
def _manifest(**extra):
    data = {"id": "demo", "name": "演示", "version": "1.0.0",
            "entry": "plugin.py"}
    data.update(extra)
    return data


def test_manifest_accepts_write_capability():
    m, err = validate_manifest(_manifest(capabilities=["write"]))
    assert m is not None and err == ""
    assert m["capabilities"] == ["write"]


def test_manifest_accepts_both_capabilities():
    m, err = validate_manifest(_manifest(capabilities=["network", "write"]))
    assert m is not None
    assert m["capabilities"] == ["network", "write"]


def test_manifest_still_rejects_unknown_capability():
    m, err = validate_manifest(_manifest(capabilities=["writ"]))
    assert m is None and "未知能力" in err


def test_known_capabilities_set():
    # 2026-09-29 起 + ai（设置页「AI 总配置」，授权在设置页下拉框勾选）
    assert KNOWN_CAPABILITIES == frozenset(
        {"network", "write", "manage", "ai"})


# ---------------- G：loader 集成 ----------------
_PLUGIN_SRC = '''
from src.plugin_api import BallAction, BallPlugin

SEEN = {}

class Act(BallAction):
    id = "__PID__.go"
    title = "go"

    def run(self, ctx):
        pass

class Plugin(BallPlugin):
    id = "__PID__"
    name = "__PID__"
    version = "1.0.0"

    def create_actions(self, ctx):
        SEEN["caps"] = sorted(ctx.capabilities())
        SEEN["enabled"] = ctx.write.enabled()
        SEEN["rid"] = ctx.write.add_fragment("来自插件")
        return [Act()]
'''


def _write_plugin(tmp_path, pid, caps):
    import json
    d = tmp_path / pid
    d.mkdir()
    manifest = {"id": pid, "name": pid, "version": "1.0.0",
                "entry": "plugin.py",
                "actions": [{"id": f"{pid}.go", "title": "go", "menu": True}]}
    if caps is not None:
        manifest["capabilities"] = caps
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "plugin.py").write_text(
        _PLUGIN_SRC.replace("__PID__", pid), encoding="utf-8")
    return d


def _load(tmp_path, sinks):
    from src.plugin_api import ActionRegistry
    from src.plugin_loader import PluginLoader
    host = PluginContext(logger=None, write_providers=sinks)
    loader = PluginLoader(ActionRegistry(logger=None), host,
                          plugins_dir=str(tmp_path))
    return loader, loader.load_all()


def test_loader_grants_write_when_declared(tmp_path):
    sinks = _providers()
    _write_plugin(tmp_path, "writer", ["write"])
    _loader, loaded = _load(tmp_path, sinks)
    assert [lp.plugin_id for lp in loaded] == ["writer"]
    seen = sys.modules["floatpulse_plugin_writer"].SEEN
    assert seen["enabled"] is True
    assert seen["rid"] == 11
    assert sinks["fragment"].calls == [("来自插件", "插件:writer")]


def test_loader_denies_write_without_declaration(tmp_path):
    sinks = _providers()
    _write_plugin(tmp_path, "reader", None)
    _loader, loaded = _load(tmp_path, sinks)
    assert [lp.plugin_id for lp in loaded] == ["reader"]
    seen = sys.modules["floatpulse_plugin_reader"].SEEN
    assert seen["enabled"] is False
    assert seen["rid"] == 0
    assert sinks["fragment"].calls == []         # 什么都没写进去


# ---------------- H：只增不改删 ----------------
def test_writer_has_no_mutating_methods():
    """确保没有偷偷加上 update / delete / remove 之类的破坏性入口"""
    public = {n for n in dir(PluginWriter) if not n.startswith("_")}
    for forbidden in ("update", "delete", "remove", "edit", "modify",
                      "set", "clear"):
        assert forbidden not in public
    # 只有三个写入方法
    assert {"add_fragment", "add_task", "add_note"} <= public
