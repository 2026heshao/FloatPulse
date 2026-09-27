# -*- coding: utf-8 -*-
"""插件能力模型回归 —— manifest.capabilities / ctx.http_post_json_async。

钉死的行为：
  A manifest 校验：capabilities 合法值放行 / 未知能力名拒载 /
    非列表拒载 / 缺省 = 空列表（老 manifest 零影响）
  B 能力判定：默认（共享宿主 ctx）无任何能力；for_plugin 派生后按声明生效
  C 桥权限：未声明 network → 调 http_post_json_async 被拒，
    假桥不被调用，但 on_done 仍收到一次 ok=False 的结果
  D 桥参数规整：body 非 dict 拒 / URL 无 scheme 拒 / timeout 越界拒 /
    合法调用时假桥收到 (url, headers, body_dict, timeout, on_done)
  E loader 集成：磁盘插件声明 network → create_actions 收到的 ctx
    has_capability("network") 为 True；不声明 → False
  F 拒绝路径不抛异常（插件崩不了宿主）
本文件不 import PyQt6（桥的宿主实现是注入替身），无 GUI 可跑。
"""

import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.plugin_api import (                       # noqa: E402
    KNOWN_CAPABILITIES, PluginContext,
)
from src.plugin_loader import validate_manifest    # noqa: E402


def _manifest(**extra):
    data = {
        "id": "demo", "name": "演示", "version": "1.0.0", "entry": "plugin.py",
    }
    data.update(extra)
    return data


# ---------------- A：manifest 校验 ----------------
def test_manifest_caps_valid(tmp_path):
    m, err = validate_manifest(_manifest(capabilities=["network"]))
    assert m is not None and err == ""
    assert m["capabilities"] == ["network"]


def test_manifest_caps_default_empty():
    m, err = validate_manifest(_manifest())
    assert m is not None and m["capabilities"] == []


def test_manifest_caps_unknown_rejected():
    m, err = validate_manifest(_manifest(capabilities=["netwrork"]))
    assert m is None and "未知能力" in err


def test_manifest_caps_not_list_rejected():
    m, err = validate_manifest(_manifest(capabilities="network"))
    assert m is None and "字符串列表" in err


def test_manifest_caps_none_treated_as_empty():
    m, err = validate_manifest(_manifest(capabilities=None))
    assert m is not None and m["capabilities"] == []


def test_manifest_caps_dedup_keep_order():
    m, _ = validate_manifest(_manifest(capabilities=["network", "network"]))
    assert m["capabilities"] == ["network"]


def test_known_capabilities_exact_set():
    """能力集合是**显式白名单**：加新能力必须同时更新这条断言。

    2026-09-27 起：network（网络桥） + write（受限写入口）。
    这是故意的硬断言——防止有人随手往 KNOWN_CAPABILITIES 里塞东西，
    每加一项都该是一次有意识的契约变更。
    """
    assert KNOWN_CAPABILITIES == frozenset({"network", "write"})


# ---------------- B：能力判定 ----------------
def test_shared_ctx_has_no_capabilities():
    ctx = PluginContext(logger=None)
    assert ctx.capabilities() == frozenset()
    assert not ctx.has_capability("network")


def test_for_plugin_passes_capabilities_and_bridge():
    calls = []

    def fake_bridge(url, headers, body, timeout, on_done):
        calls.append((url, headers, body, timeout, on_done))
        return True

    base = PluginContext(logger=None, http_post_async=fake_bridge)
    child = base.for_plugin("demo", "/x", ["network"])
    assert child.has_capability("network")
    assert child.capabilities() == frozenset({"network"})
    # 共享实例自身仍然无能力
    assert not base.has_capability("network")


def test_for_plugin_without_caps_has_bridge_but_no_permission():
    base = PluginContext(logger=None, http_post_async=lambda *a: True)
    child = base.for_plugin("demo", "/x")
    assert not child.has_capability("network")


# ---------------- C / D：桥调用与参数规整 ----------------
def test_bridge_rejected_without_capability():
    fired = []
    ctx = PluginContext(logger=None, http_post_async=lambda *a: True)
    ok = ctx.http_post_json_async("https://x.example/v1",
                                  on_done=lambda r: fired.append(r))
    assert ok is False
    assert fired and fired[0]["ok"] is False
    assert "network" in fired[0]["error"]


def test_bridge_rejected_without_host_bridge():
    fired = []
    ctx = PluginContext(logger=None).for_plugin("demo", "/x", ["network"])
    ok = ctx.http_post_json_async("https://x.example/v1",
                                  on_done=lambda r: fired.append(r))
    assert ok is False
    assert fired and "宿主未注入" in fired[0]["error"]


def test_bridge_rejects_bad_url():
    fired = []
    ctx = _ctx_with_bridge(should_not_be_called=True, sink=None)
    ok = ctx.http_post_json_async("ftp://nope", on_done=lambda r: fired.append(r))
    assert ok is False and "http" in fired[0]["error"]


def test_bridge_rejects_non_dict_body():
    fired = []
    ctx = PluginContext(logger=None,
                        http_post_async=lambda *a, **k: True
                        ).for_plugin("demo", "/x", ["network"])
    ok = ctx.http_post_json_async("https://x.example", body=[1, 2],
                                  on_done=lambda r: fired.append(r))
    assert ok is False and "dict" in fired[0]["error"]


def test_bridge_rejects_bad_timeout():
    fired = []
    ctx = PluginContext(logger=None,
                        http_post_async=lambda *a, **k: True
                        ).for_plugin("demo", "/x", ["network"])
    ok = ctx.http_post_json_async("https://x.example", timeout=999,
                                  on_done=lambda r: fired.append(r))
    assert ok is False and "timeout" in fired[0]["error"]


def test_bridge_forwards_normalized_args():
    calls = []
    sink = []

    def fake_bridge(url, headers, body, timeout, on_done):
        calls.append((url, dict(headers), dict(body), timeout))
        return True

    ctx = PluginContext(logger=None, http_post_async=fake_bridge
                        ).for_plugin("demo", "/x", ["network"])
    ok = ctx.http_post_json_async(
        "https://api.example/v1/chat/completions",
        headers={"Authorization": "Bearer k"},
        body={"model": "m", "messages": []},
        timeout=15.0, on_done=lambda r: sink.append(r))
    assert ok is True and not sink          # 已发起，不立即回调
    url, headers, body, timeout = calls[0]
    assert url == "https://api.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert body == {"model": "m", "messages": []}
    assert timeout == 15.0


def test_bridge_exception_isolated():
    fired = []

    def boom(*a, **k):
        raise RuntimeError("bridge down")

    ctx = PluginContext(logger=None, http_post_async=boom
                        ).for_plugin("demo", "/x", ["network"])
    ok = ctx.http_post_json_async("https://x.example",
                                  on_done=lambda r: fired.append(r))
    assert ok is False and fired[0]["ok"] is False


def _ctx_with_bridge(should_not_be_called, sink):
    """构造一个桥；若 should_not_be_called 而桥被调，测试自行失败"""
    def bridge(*a, **k):
        raise AssertionError("桥不应被调用")
    return PluginContext(logger=None, http_post_async=bridge
                         ).for_plugin("demo", "/x", ["network"])


# ---------------- E：loader 集成 ----------------
_PLUGIN_WITH_NET = '''
from src.plugin_api import BallAction, BallPlugin

class ProbeAction(BallAction):
    id = "probe.cap"
    title = "probe"
    def run(self, ctx):
        pass

class Plugin(BallPlugin):
    id = "probe-caps"
    name = "probe"
    version = "1.0.0"
    def create_actions(self, ctx):
        ProbeAction.caps_at_create = sorted(ctx.capabilities())
        ProbeAction.bridge_present = ctx._http_post_async is not None
        return [ProbeAction()]
'''

_PLUGIN_WITHOUT_NET = _PLUGIN_WITH_NET  # 内容相同，manifest 决定能力


def _make_plugin(tmp_path, folder, manifest_extra, source):
    d = tmp_path / folder
    d.mkdir()
    manifest = {"id": folder, "name": folder, "version": "1.0.0",
                "entry": "plugin.py", "actions": []}
    manifest.update(manifest_extra)
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "plugin.py").write_text(source, encoding="utf-8")
    return d


def test_loader_grants_declared_capability(tmp_path):
    from src.plugin_loader import PluginLoader
    from src.plugin_api import ActionRegistry

    _make_plugin(tmp_path, "probe-caps",
                 {"capabilities": ["network"]}, _PLUGIN_WITH_NET)
    fired = []
    host = PluginContext(logger=None, http_post_async=lambda *a: fired.append(a))
    reg = ActionRegistry(logger=None)
    loader = PluginLoader(reg, host, plugins_dir=str(tmp_path))
    loaded = loader.load_all()
    assert [lp.plugin_id for lp in loaded] == ["probe-caps"]
    from floatpulse_plugin_probe_caps import ProbeAction   # noqa: F401
    import sys as _sys
    probe = _sys.modules["floatpulse_plugin_probe_caps"].ProbeAction
    assert probe.caps_at_create == ["network"]
    assert probe.bridge_present is True


def test_loader_no_capability_by_default(tmp_path):
    from src.plugin_loader import PluginLoader
    from src.plugin_api import ActionRegistry

    _make_plugin(tmp_path, "probe-caps", {}, _PLUGIN_WITHOUT_NET)
    host = PluginContext(logger=None, http_post_async=lambda *a: None)
    reg = ActionRegistry(logger=None)
    loader = PluginLoader(reg, host, plugins_dir=str(tmp_path))
    loaded = loader.load_all()
    assert [lp.plugin_id for lp in loaded] == ["probe-caps"]
    import sys as _sys
    probe = _sys.modules["floatpulse_plugin_probe_caps"].ProbeAction
    assert probe.caps_at_create == []
