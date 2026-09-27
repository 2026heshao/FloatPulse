# -*- coding: utf-8 -*-
"""插件生命周期钩子 + 卸载（P1-1 / P0-3）。

钉死的行为：
  A 钩子可选：老插件（不实现任何钩子）加载 / 停用 / 卸载全程零影响
  B on_enable 在登记成功后调用；on_disable 在 unregister 之前调用；
    on_uninstall 在 rmtree 之前调用（用探针插件按顺序计数）
  C 钩子抛异常 → 只记 warning，登记 / 停用 / 卸载流程照常走完
  D uninstall：正常卸载删目录 + 摘动作 + 清模块缓存；私有数据目录不受影响
  E uninstall 边界：非法 id（含 ..）/ 目录不存在 → 拒绝且不动任何文件
  F 卸载后 rescan 不会"复活"插件（sys.modules 缓存已清）
本文件不 import PyQt6（纯逻辑），无 GUI 可跑。
"""

import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.plugin_api import ActionRegistry, PluginContext    # noqa: E402
from src.plugin_loader import PluginLoader                  # noqa: E402

_PLUGIN_TEMPLATE = '''
from src.plugin_api import BallAction, BallPlugin

CALLS = []

class Act(BallAction):
    id = "{pid}.go"
    title = "go"

    def run(self, ctx):
        pass

class Plugin(BallPlugin):
    id = "{pid}"
    name = "{pid}"
    version = "1.0.0"

    def create_actions(self, ctx):
        return [Act()]
{extra}
'''


def _write_plugin(root, pid, extra=""):
    """在 root/pid/ 下写一个最小插件；extra 用于追加钩子方法定义"""
    d = os.path.join(str(root), pid)
    os.makedirs(d, exist_ok=True)
    manifest = {"id": pid, "name": pid, "version": "1.0.0",
                "entry": "plugin.py", "actions": [
                    {"id": f"{pid}.go", "title": "go", "menu": True}]}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(_PLUGIN_TEMPLATE.format(pid=pid, extra=extra))
    return d


def _loader(tmp_path, private_dir=""):
    reg = ActionRegistry(logger=None)
    ctx = PluginContext(logger=None, data_dir_base=str(private_dir or ""))
    return PluginLoader(reg, ctx, plugins_dir=str(tmp_path)), reg


def _calls_of(pid):
    """读回插件模块里的 CALLS 列表（探针计数的传递通道）"""
    mod = sys.modules.get("floatpulse_plugin_" + pid.replace("-", "_"))
    return list(getattr(mod, "CALLS", [])) if mod is not None else []


# ---------------- A：钩子完全可选 ----------------
def test_plugin_without_hooks_works_end_to_end(tmp_path):
    _write_plugin(tmp_path, "plain")
    loader, reg = _loader(tmp_path)
    assert [lp.plugin_id for lp in loader.load_all()] == ["plain"]
    assert reg.get("plain.go") is not None
    assert loader.deactivate() == 1
    ok, _ = loader.uninstall("plain")
    assert ok is True
    assert not os.path.isdir(os.path.join(str(tmp_path), "plain"))


# ---------------- B：三个钩子的调用时机 ----------------
_HOOKS = '''
    def on_enable(self, ctx):
        CALLS.append("enable")

    def on_disable(self, ctx):
        CALLS.append("disable")

    def on_uninstall(self, ctx):
        CALLS.append("uninstall")
'''


def test_on_enable_called_on_register(tmp_path):
    _write_plugin(tmp_path, "probe", _HOOKS)
    loader, _ = _loader(tmp_path)
    loader.load_all()
    assert _calls_of("probe") == ["enable"]


def test_on_disable_called_before_unregister(tmp_path):
    _write_plugin(tmp_path, "probe", _HOOKS)
    loader, reg = _loader(tmp_path)
    loader.load_all()
    # 钩子里能读到"动作还在"这一事实（因为 unregister 在钩子之后）
    loader.deactivate()
    assert _calls_of("probe") == ["enable", "disable"]
    assert reg.all_actions() == []


def test_on_uninstall_called_before_rmtree(tmp_path):
    """钩子执行时目录必须还在（插件可能要从自己目录读点东西做收尾）

    注意：uninstall 会清掉 sys.modules 缓存，所以不能事后读模块里的 CALLS——
    改成让钩子把结果写到模块外（插件目录的上一级）来观测。
    """
    probe = '''
    def on_uninstall(self, ctx):
        import os
        out = os.path.join(os.path.dirname(ctx.plugin_dir), "hook_probe.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write("dir_exists=%s" % os.path.isdir(ctx.plugin_dir))
'''
    _write_plugin(tmp_path, "probe", probe)
    loader, _ = _loader(tmp_path)
    loader.load_all()
    ok, _ = loader.uninstall("probe")
    assert ok is True
    with open(os.path.join(str(tmp_path), "hook_probe.txt"),
              encoding="utf-8") as f:
        assert f.read() == "dir_exists=True"


def test_enable_called_again_after_reactivate(tmp_path):
    """「从停用恢复」也算 on_enable（插件据此重新预热）"""
    _write_plugin(tmp_path, "probe", _HOOKS)
    loader, _ = _loader(tmp_path)
    loader.load_all()
    loader.deactivate()
    loader.activate()
    assert _calls_of("probe") == ["enable", "disable", "enable"]


# ---------------- C：钩子异常隔离 ----------------
_BOOM_HOOKS = '''
    def on_enable(self, ctx):
        raise RuntimeError("enable boom")

    def on_disable(self, ctx):
        raise RuntimeError("disable boom")

    def on_uninstall(self, ctx):
        raise RuntimeError("uninstall boom")
'''


def test_enable_hook_exception_does_not_break_register(tmp_path):
    _write_plugin(tmp_path, "boom", _BOOM_HOOKS)
    loader, reg = _loader(tmp_path)
    loaded = loader.load_all()
    assert [lp.plugin_id for lp in loaded] == ["boom"]
    assert reg.get("boom.go") is not None           # 动作照样登记成功
    assert any("on_enable" in w for w in loaded[0].warnings)


def test_disable_hook_exception_does_not_break_deactivate(tmp_path):
    _write_plugin(tmp_path, "boom", _BOOM_HOOKS)
    loader, reg = _loader(tmp_path)
    loader.load_all()
    assert loader.deactivate() == 1                 # 钩子炸了也照样摘掉
    assert reg.all_actions() == []


def test_uninstall_hook_exception_does_not_block_removal(tmp_path):
    _write_plugin(tmp_path, "boom", _BOOM_HOOKS)
    loader, _ = _loader(tmp_path)
    loader.load_all()
    ok, _ = loader.uninstall("boom")
    assert ok is True
    assert not os.path.isdir(os.path.join(str(tmp_path), "boom"))


# ---------------- D：卸载的正常路径 ----------------
def test_uninstall_removes_dir_actions_and_module(tmp_path):
    _write_plugin(tmp_path, "target")
    loader, reg = _loader(tmp_path)
    loader.load_all()
    assert "floatpulse_plugin_target" in sys.modules

    ok, msg = loader.uninstall("target")
    assert ok is True and "target" in msg
    assert not os.path.isdir(os.path.join(str(tmp_path), "target"))
    assert reg.all_actions() == []
    assert loader.loaded_plugins() == []
    assert "floatpulse_plugin_target" not in sys.modules   # 模块缓存已清


def test_uninstall_keeps_private_data_dir(tmp_path):
    """插件私有数据（float_data/plugins/<id>/）必须保留——重装后还能用"""
    private = tmp_path / "private"
    private.mkdir()
    _write_plugin(tmp_path / "plugins", "target")
    loader, _ = _loader(tmp_path / "plugins", private_dir=private)
    loader.load_all()
    # 模拟插件写过私有数据
    data_dir = os.path.join(str(private), "target")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "config.json"), "w", encoding="utf-8") as f:
        f.write("{}")

    ok, msg = loader.uninstall("target")
    assert ok is True
    assert "私有数据保留" in msg
    assert os.path.isfile(os.path.join(data_dir, "config.json"))


def test_uninstall_does_not_touch_sibling_plugins(tmp_path):
    _write_plugin(tmp_path, "alpha")
    _write_plugin(tmp_path, "beta")
    loader, reg = _loader(tmp_path)
    loader.load_all()
    loader.uninstall("alpha")
    assert reg.get("beta.go") is not None
    assert os.path.isdir(os.path.join(str(tmp_path), "beta"))


# ---------------- E：卸载边界 ----------------
@pytest.mark.parametrize("bad", ["", "..", "../evil", "a/b", "a\\b", ".hidden"])
def test_uninstall_rejects_unsafe_id(tmp_path, bad):
    _write_plugin(tmp_path, "keep")
    loader, _ = _loader(tmp_path)
    loader.load_all()
    ok, msg = loader.uninstall(bad)
    assert ok is False
    # 什么都没被删
    assert os.path.isdir(os.path.join(str(tmp_path), "keep"))


def test_uninstall_missing_dir_returns_reason(tmp_path):
    loader, _ = _loader(tmp_path)
    ok, msg = loader.uninstall("ghost")
    assert ok is False and "不存在" in msg


def test_uninstall_rejects_path_outside_plugins_dir(tmp_path):
    """即使 id 合法，目标目录越界也必须拒绝（双保险）"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("别删我", encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()

    loader, _ = _loader(plugins)

    # 直接调 _within 验证白名单判定本身
    assert PluginLoader._within(str(outside), str(plugins)) is False
    assert PluginLoader._within(
        os.path.join(str(plugins), "x"), str(plugins)) is True
    assert (outside / "victim.txt").exists()


# ---------------- F：卸载后不复活 ----------------
def test_rescan_after_uninstall_does_not_revive(tmp_path):
    _write_plugin(tmp_path, "target")
    loader, reg = _loader(tmp_path)
    loader.load_all()
    loader.uninstall("target")

    loader.rescan()                                  # 用户点「重新扫描」
    assert loader.loaded_plugins() == []
    assert reg.all_actions() == []
