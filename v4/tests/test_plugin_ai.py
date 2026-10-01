# -*- coding: utf-8 -*-
"""
AI 总配置 · 逻辑层单元测试
====================================================================
覆盖「🧠 AI 总配置」宿主能力的纯逻辑部分（无 GUI）：

  - KNOWN_CAPABILITIES 含 "ai"（契约级硬断言的配套）
  - AiBackendFacade 门禁：未声明 ai 能力 → 全部安全降级（False/{}）
  - AiBackendFacade 读取：attached 判定透传 plugin_id、params 实时
    快照、provider 异常隔离、非 dict 返回值拒收
  - for_plugin 派生：ai_providers 随派生传递（子实例照常可读）
  - 配置三件套：ai_* 键的默认值 / 类型 / 端口取值范围
  - ai_server：launch args 纯函数、监听器订阅与退订、启动失败
    （程序/模型不存在）只置 error 不抛异常、不产生子进程

GUI（设置页 AI 卡 / 多选下拉框 / 探活）走 tools/verify_ai_backend.py。
"""

import os
import sys

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QProcess                 # noqa: E402

from src.plugin_api import (                       # noqa: E402
    AiBackendFacade, KNOWN_CAPABILITIES, PluginContext,
)
from src.config import (                           # noqa: E402
    DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES,
)
from src.ai_server import (                        # noqa: E402
    AiServerManager, build_launch_args, sanitize_ctx_size, ST_ERROR,
)


class _CaptureLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


def _facade(caps, providers=None, logger=None, plugin_id="p1"):
    return AiBackendFacade(logger=logger, providers=providers,
                           plugin_id=plugin_id, capabilities=caps)


# ====================================================================
# 契约
# ====================================================================
def test_known_capabilities_contains_ai():
    assert "ai" in KNOWN_CAPABILITIES


def test_ctx_has_ai_facade_even_without_capability():
    ctx = PluginContext(logger=None)
    assert ctx.ai is not None
    assert ctx.ai.enabled() is False
    assert ctx.ai.params() == {}


# ====================================================================
# 门禁：未声明 ai 能力 → 安全降级
# ====================================================================
def test_facade_without_capability_denies_everything():
    logger = _CaptureLogger()
    calls = []
    fac = _facade(caps={"network"}, providers={
        "is_attached": lambda pid: True,
        "params": lambda: {"mode": "cloud"},
        "add_listener": lambda fn: calls.append(1) or True,
    }, logger=logger)
    assert fac.is_attached() is False
    assert fac.params() == {}
    assert fac.add_listener(lambda *_a: None) is False
    assert fac.remove_listener(lambda *_a: None) is False
    assert calls == []
    assert any("ai" in w for w in logger.warnings)


def test_facade_without_providers_degrades():
    fac = _facade(caps={"ai"}, providers=None)
    assert fac.is_attached() is False
    assert fac.params() == {}
    assert fac.add_listener(lambda *_a: None) is False


# ====================================================================
# 读取：providers 透传 / 异常隔离 / 脏返回值拒收
# ====================================================================
def test_facade_is_attached_passes_plugin_id():
    seen = []
    fac = _facade(caps={"ai"}, providers={
        "is_attached": lambda pid: seen.append(pid) or (pid == "p1"),
    }, plugin_id="p1")
    assert fac.is_attached() is True
    assert seen == ["p1"]


def test_facade_params_passthrough_and_live():
    holder = {"mode": "cloud", "base_url": "https://a/v1"}
    fac = _facade(caps={"ai"}, providers={"params": lambda: dict(holder)})
    first = fac.params()
    assert first["mode"] == "cloud"
    holder["mode"] = "local"                 # 模拟设置页改配置（实时性）
    assert fac.params()["mode"] == "local"
    assert first["mode"] == "cloud"          # 旧快照不受影响（纯副本）


def test_facade_params_rejects_non_dict():
    fac = _facade(caps={"ai"}, providers={"params": lambda: "nope"})
    assert fac.params() == {}


def test_facade_provider_exception_isolated():
    logger = _CaptureLogger()

    def _boom():
        raise RuntimeError("boom")

    fac = _facade(caps={"ai"}, providers={
        "is_attached": _boom, "params": _boom,
        "add_listener": _boom, "remove_listener": _boom,
    }, logger=logger)
    assert fac.is_attached() is False
    assert fac.params() == {}
    assert fac.add_listener(lambda *_a: None) is False
    assert fac.remove_listener(lambda *_a: None) is False
    assert len(logger.warnings) >= 4


def test_facade_listeners_roundtrip():
    added, removed = [], []
    fac = _facade(caps={"ai"}, providers={
        "add_listener": lambda fn: added.append(fn) or True,
        "remove_listener": lambda fn: removed.append(fn) or True,
    })
    fn = lambda *a: None                     # noqa: E731
    assert fac.add_listener(fn) is True
    assert fac.remove_listener(fn) is True
    assert added == [fn] and removed == [fn]


def test_facade_sources_self_inspection():
    fac = _facade(caps={"ai"}, providers={"params": lambda: {}})
    assert fac.sources() == ("params",)


# ====================================================================
# for_plugin 派生：ai_providers 随派生传递
# ====================================================================
def test_for_plugin_preserves_ai_providers():
    ctx = PluginContext(logger=None, ai_providers={
        "is_attached": lambda pid: pid == "kid",
        "params": lambda: {"mode": "cloud", "base_url": "https://x/v1",
                           "api_key": "", "model": "m", "local_port": 8095,
                           "local_ready": False, "local_status": "stopped",
                           "local_detail": ""},
    })
    child = ctx.for_plugin("kid", "/tmp/dir", ["ai"])
    assert child.has_capability("ai")
    assert child.ai.is_attached() is True
    assert child.ai.params()["base_url"] == "https://x/v1"
    # 未声明 ai 能力的派生实例：门禁生效
    plain = ctx.for_plugin("kid", "/tmp/dir", ["network"])
    assert plain.ai.is_attached() is False
    assert plain.ai.params() == {}


# ====================================================================
# 配置三件套：ai_* 键
# ====================================================================
def test_config_defaults_have_ai_keys():
    assert DEFAULT_CONFIG["ai_backend_mode"] == "cloud"
    assert DEFAULT_CONFIG["ai_cloud_base_url"].startswith("https://")
    assert DEFAULT_CONFIG["ai_cloud_api_key"] == ""
    assert DEFAULT_CONFIG["ai_cloud_model"] == "deepseek-chat"
    assert DEFAULT_CONFIG["ai_local_server_exe"] == ""
    assert DEFAULT_CONFIG["ai_local_gguf"] == ""
    assert DEFAULT_CONFIG["ai_local_port"] == 8095
    assert DEFAULT_CONFIG["ai_local_thinking"] is False
    assert DEFAULT_CONFIG["ai_local_ctx_size"] == 16384
    assert DEFAULT_CONFIG["ai_plugins"] == []


def test_config_types_have_ai_keys():
    for key in ("ai_backend_mode", "ai_cloud_base_url", "ai_cloud_api_key",
                "ai_cloud_model", "ai_local_server_exe", "ai_local_gguf"):
        assert _CONFIG_TYPES[key] is str, key
    assert _CONFIG_TYPES["ai_local_port"] is int
    assert _CONFIG_TYPES["ai_local_thinking"] is bool
    assert _CONFIG_TYPES["ai_local_ctx_size"] is int
    assert _CONFIG_TYPES["ai_plugins"] is list


def test_config_range_has_ai_local_port():
    lo, hi = _CONFIG_RANGES["ai_local_port"]
    assert lo == 1024 and hi == 65535
    lo, hi = _CONFIG_RANGES["ai_local_ctx_size"]
    assert lo == 2048 and hi == 131072
    assert "ai_local_thinking" not in _CONFIG_RANGES     # bool 不进 RANGES


# ====================================================================
# ai_server：纯函数与监听器 / 启动失败路径（无 GUI、无子进程）
# ====================================================================
def test_build_launch_args():
    args = build_launch_args("C:/m.gguf", 8095)
    assert args[0] == "-m" and "C:/m.gguf" in args
    assert "--port" in args and "8095" in args
    assert "-ngl" in args and "99" in args
    assert "-c" in args and "16384" in args
    # 默认关思维链：--reasoning off（★实测 budget 0 不被 Qwen3.5 模板遵循）
    i = args.index("--reasoning")
    assert args[i + 1] == "off"


def test_build_launch_args_thinking_on():
    on = build_launch_args("C:/m.gguf", 8095, thinking=True)
    assert "--reasoning" not in on          # 开思考：不传任何 reasoning 旗标
    assert "-c" in on and "16384" in on
    assert on[:5] == build_launch_args("C:/m.gguf", 8095)[:5]  # 其余一致


def test_build_launch_args_ctx_clamped():
    def ctx_of(**kw):
        args = build_launch_args("m", 1, **kw)
        return args[args.index("-c") + 1]   # 默认尾部追加思维链旗标，按下标取

    assert ctx_of(ctx_size=100) == "2048"
    assert ctx_of(ctx_size=999999) == "131072"
    assert ctx_of(ctx_size="abc") == "16384"
    assert ctx_of(ctx_size=None) == "16384"


def test_sanitize_ctx_size_bounds():
    assert sanitize_ctx_size(8192) == 8192
    assert sanitize_ctx_size("32768") == 32768
    assert sanitize_ctx_size(0) == 2048
    assert sanitize_ctx_size([]) == 16384


def test_server_listener_add_remove_and_immediate_sync():
    srv = AiServerManager()
    seen = []
    assert srv.add_listener(lambda s, d: seen.append((s, d))) is True
    assert seen == [("stopped", "")]         # 订阅即回放当前状态
    fn = lambda *a: None                     # noqa: E731
    assert srv.add_listener(fn) is True
    assert srv.remove_listener(fn) is True
    assert srv.remove_listener(fn) is False  # 重复退订安全返回 False


def test_server_emit_broadcasts_to_listeners():
    srv = AiServerManager()
    got = []
    srv.add_listener(lambda s, d: got.append((s, d)))
    srv._emit("starting", "启动中")
    assert srv.status == "starting" and srv.detail == "启动中"
    assert got[-1] == ("starting", "启动中")


def test_server_start_missing_exe_sets_error_without_process():
    srv = AiServerManager()
    got = []
    srv.add_listener(lambda s, d: got.append((s, d)))

    def _post(*_a):
        raise AssertionError("文件不存在时不该发探活请求")

    srv.start(_post, "C:/definitely-not-here.exe", "C:/no.gguf", 8095)
    assert srv.status == ST_ERROR
    assert "程序不存在" in srv.detail
    assert srv.running is False
    assert got[-1][0] == ST_ERROR


def test_server_start_missing_gguf_sets_error():
    srv = AiServerManager()
    srv.start(lambda *a: True, __file__, "C:/no.gguf", 8095)
    assert srv.status == ST_ERROR
    assert "模型文件不存在" in srv.detail


def test_server_start_without_post_fn_sets_error():
    srv = AiServerManager()
    srv.start(None, __file__, __file__, 8095)
    assert srv.status == ST_ERROR
    assert "探活" in srv.detail


def test_server_restart_when_not_running_starts_directly():
    srv = AiServerManager()
    srv.restart(lambda *_: True, "Z:/no_such_exe.exe", "Z:/no.gguf", 8095)
    assert srv.status == ST_ERROR        # 未运行 → pending 被直接消费进 start
    assert srv._pending is None
    assert srv._proc is None             # 未产生子进程


def test_server_restart_overrides_pending_and_guards():
    srv = AiServerManager()
    srv._proc = object()                 # 模拟运行中 → restart 只挂 pending
    srv.stop = lambda: None              # 屏蔽真实停止（object 无 terminate）
    srv.restart(lambda *_: True, "a.exe", "a.gguf", 8095, 32768, True)
    assert srv._pending is not None and srv._pending[4] == 32768
    srv.restart(lambda *_: True, "b.exe", "b.gguf", 8096, 65536, False)
    assert srv._pending[1] == "b.exe"    # 重复调用以后一次参数为准
    srv._proc = None
    srv._launch_pending()                # 手动消费：无进程则放行启动
    assert srv._pending is None
    assert srv.status == ST_ERROR        # b.exe 不存在 → error 路径不抛


def test_facade_stop_local_denied_without_capability():
    logger = _CaptureLogger()
    called = []
    fac = _facade(caps={"network"}, providers={
        "stop_local": lambda: called.append(1) or True}, logger=logger)
    assert fac.stop_local() is False
    assert called == []
    assert any("ai" in w for w in logger.warnings)


def test_facade_stop_local_passthrough():
    called = []
    fac = _facade(caps={"ai"}, providers={
        "stop_local": lambda: called.append(1) or True})
    assert fac.stop_local() is True
    assert called == [1]


def test_facade_stop_local_without_provider_degrades():
    fac = _facade(caps={"ai"}, providers=None)
    assert fac.stop_local() is False


def test_server_stop_without_process_is_safe():
    srv = AiServerManager()
    srv.stop()                                # 不抛异常即可
    assert srv.status == "stopped"


# ---------------- 2026-10-01 修复回归：强杀定时器误杀新实例 ----------------
class _FakeProc:
    """duck-typing QProcess：记录 terminate/kill 调用，可指定 state()"""

    def __init__(self, state):
        self._state = state
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def state(self):
        return self._state


def test_stop_kill_timer_tracks_old_instance(monkeypatch):
    """stop() 的 3s 强杀定时器只盯**当时那个实例**。

    旧实例 3s 内退出 → restart 拉起新实例 → 定时器到点时绝不能
    kill 掉新实例（2026-10-01 修复前判 self._proc，会误杀）。
    """
    captured = {}

    class _T:
        @staticmethod
        def singleShot(ms, fn):
            captured["ms"] = ms
            captured["fn"] = fn

    monkeypatch.setattr("src.ai_server.QTimer", _T)
    srv = AiServerManager()
    old = _FakeProc(QProcess.ProcessState.Running)
    srv._proc = old
    srv.stop()
    assert old.terminated is True
    assert captured["ms"] == 3000
    # 旧实例快速退出（finished → _proc=None），restart 拉起新实例
    old._state = QProcess.ProcessState.NotRunning
    new = _FakeProc(QProcess.ProcessState.Running)
    srv._proc = new
    captured["fn"]()                          # 过期定时器到点
    assert new.killed is False                # 新实例安然无恙
    # 对照场景：旧实例 3s 后仍在运行 → 仍要被强杀
    srv2 = AiServerManager()
    stubborn = _FakeProc(QProcess.ProcessState.Running)
    srv2._proc = stubborn
    srv2.stop()
    captured["fn"]()
    assert stubborn.killed is True
    # 新实例 running 中直接 stop → 再到点只对新实例本身操作
    srv3 = AiServerManager()
    cur = _FakeProc(QProcess.ProcessState.Running)
    srv3._proc = cur
    srv3.stop()
    captured["fn"]()
    assert cur.killed is True


def test_error_occurred_failed_to_start_resets_state_machine():
    """FailedToStart 不发 finished → 必须就地清 _proc，否则 start()
    被「已在运行」守卫拒绝，状态机永久卡死（2026-10-01 修复前行为）。"""
    from types import SimpleNamespace

    srv = AiServerManager()
    got = []
    srv.add_listener(lambda s, d: got.append((s, d)))
    srv._proc = object()
    srv._timer = SimpleNamespace(stop=lambda: None, deleteLater=lambda: None)
    srv._on_error_occurred(QProcess.ProcessError.FailedToStart)
    assert srv._proc is None
    assert srv.running is False
    assert srv.status == ST_ERROR
    assert "启动失败" in srv.detail
    # 守卫确实解除：start 不再报「已在启动/运行中」（exe 不存在走另一分支）
    srv.start(lambda *a: None, "C:/definitely-not-here.exe", "C:/no.gguf", 8095)
    assert "已在启动" not in srv.detail
    # 非 FailedToStart（如 Crashed）：等 finished 清理，_proc 不动
    srv2 = AiServerManager()
    marker = object()
    srv2._proc = marker
    srv2._on_error_occurred(QProcess.ProcessError.Crashed)
    assert srv2._proc is marker
    assert srv2.status == ST_ERROR


if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[OK]  {name}")
            except Exception:                                     # noqa: BLE001
                fails += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    print("== 全部通过 ==" if not fails else f"== {fails} 个失败 ==")
    sys.exit(1 if fails else 0)
