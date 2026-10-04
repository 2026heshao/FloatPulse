# -*- coding: utf-8 -*-
"""
插件设置消费点纯逻辑回归（2026-10-04 插件设置扩展）
====================================================================
四插件接入 manifest.settings（框架层见 test_plugin_settings.py）后，各
自「消费点」的生效值链路用纯逻辑测试钉住——**mock ctx 与常量兜底双向
验证，防假护栏**：

  ai-assistant      clamp_temperature / clamp_max_history /
                    build_request(temperature=…)
  ai-text-workshop  clamp_temperature / clamp_max_tokens /
                    build_request(temperature=…, max_tokens=…)
  vault             normalize_clear_seconds / clear_seconds_for(ctx)
  weekly-report     resolve_default_range(ctx)

每个消费函数都走三条路：
  1. 生效值路径——mock ctx / 传参返回什么就用什么（若消费点改回写死
     常量，本条断言立刻红灯）；
  2. 旧宿主路径——ctx 没有 get_setting 契约时回落模块常量（默认行为
     与设置功能上线前逐字节一致）；
  3. 脏值路径——非法 / 越界 / 抛异常一律回落，绝不放大成任意值。

manifest 契约同时钉死：settings 经 validate_manifest 校验通过、key 集
合、type/min/max/default 与插件常量同源（改常量忘改 manifest 会红灯）。
GUI 页面的接线（_apply_settings / settings_changed 订阅）走离屏验证
tools/verify_plugin_settings.py，此处不依赖 QApplication 事件。
====================================================================
"""

import importlib.util
import json
import os
import sys
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)

# 让插件模块能 import src.*（与 test_text_workshop.py 同一约定）
sys.path.insert(0, BASE)

from src.plugin_loader import validate_manifest  # noqa: E402


def _load_plugin(plugin_id, mod_name):
    """以唯一模块名加载插件入口（避免 sys.modules 撞名）"""
    path = os.path.join(ROOT, "plugins", plugin_id, "plugin.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _manifest(plugin_id):
    with open(os.path.join(ROOT, "plugins", plugin_id, "manifest.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _ctx_with(get_setting):
    """有 get_setting 契约的宿主 ctx（mock，生效值注入点）"""
    return types.SimpleNamespace(get_setting=get_setting)


def _ctx_old_host():
    """旧宿主：连 get_setting 属性都没有（兜底路径的靶子）"""
    return types.SimpleNamespace()


def _ctx_boom():
    """get_setting 抛异常的 ctx（读取失败不许反噬插件）"""
    def boom(key, fallback=None):
        raise RuntimeError("settings 读取失败")
    return types.SimpleNamespace(get_setting=boom)


ai_assistant = _load_plugin("ai-assistant", "ai_assistant_plugin_settings")
workshop = _load_plugin("ai-text-workshop", "ai_text_workshop_plugin_settings")
vault = _load_plugin("vault", "vault_plugin_settings")
weekly = _load_plugin("weekly-report", "weekly_report_plugin_settings")


# ====================================================================
# manifest 契约：settings schema 与插件常量同源
# ====================================================================
def test_ai_assistant_manifest_settings_match_constants():
    manifest, err = validate_manifest(_manifest("ai-assistant"))
    assert err == ""
    entries = {e["key"]: e for e in manifest["settings"]}
    assert set(entries) == {"temperature", "max_history"}
    t = entries["temperature"]
    assert t["type"] == "float" and t["default"] == 0.4
    assert t["min"] == ai_assistant.TEMPERATURE_MIN
    assert t["max"] == ai_assistant.TEMPERATURE_MAX
    assert t["default"] == ai_assistant.DEFAULT_TEMPERATURE
    h = entries["max_history"]
    assert h["type"] == "int" and h["default"] == 12
    assert h["min"] == ai_assistant.MAX_HISTORY_MIN
    assert h["max"] == ai_assistant.MAX_HISTORY_MAX
    assert h["default"] == ai_assistant.MAX_HISTORY


def test_workshop_manifest_settings_match_constants():
    manifest, err = validate_manifest(_manifest("ai-text-workshop"))
    assert err == ""
    entries = {e["key"]: e for e in manifest["settings"]}
    assert set(entries) == {"temperature", "max_tokens"}
    t = entries["temperature"]
    assert t["type"] == "float" and t["default"] == 0.4
    assert t["min"] == workshop.TEMPERATURE_MIN
    assert t["max"] == workshop.TEMPERATURE_MAX
    assert t["default"] == workshop.TEMPERATURE
    m = entries["max_tokens"]
    assert m["type"] == "int" and m["default"] == 2048
    assert m["min"] == workshop.MAX_TOKENS_MIN
    assert m["max"] == workshop.MAX_TOKENS_MAX
    assert m["default"] == workshop.MAX_TOKENS


def test_vault_manifest_settings_match_constants():
    manifest, err = validate_manifest(_manifest("vault"))
    assert err == ""
    entries = {e["key"]: e for e in manifest["settings"]}
    assert set(entries) == {"clipboard_clear_seconds"}
    e = entries["clipboard_clear_seconds"]
    assert e["type"] == "enum"
    assert e["choices"] == list(vault.CLEAR_SECONDS_CHOICES)
    # 默认值 = 常量兜底的字符串形态（enum 存字符串，存储层口径）
    assert e["default"] == str(vault.CLIPBOARD_CLEAR_SECONDS)


def test_weekly_manifest_settings_match_constants():
    manifest, err = validate_manifest(_manifest("weekly-report"))
    assert err == ""
    entries = {e["key"]: e for e in manifest["settings"]}
    assert set(entries) == {"default_range"}
    e = entries["default_range"]
    assert e["type"] == "enum"
    assert e["choices"] == [weekly.RANGE_TODAY, weekly.RANGE_WEEK]
    assert e["default"] == weekly.RANGE_WEEK


# ====================================================================
# ai-assistant：温度 + 上下文条数
# ====================================================================
def test_ai_clamp_temperature_effective_value_used():
    assert ai_assistant.clamp_temperature(0.8) == 0.8
    assert ai_assistant.clamp_temperature(0.15) == 0.15
    assert ai_assistant.clamp_temperature(1.0) == 1.0


def test_ai_clamp_temperature_bounds():
    assert ai_assistant.clamp_temperature(0.05) == 0.1
    assert ai_assistant.clamp_temperature(5.0) == 1.0


def test_ai_clamp_temperature_fallback_on_dirty():
    assert ai_assistant.clamp_temperature(None) == ai_assistant.DEFAULT_TEMPERATURE
    assert ai_assistant.clamp_temperature("hot") == ai_assistant.DEFAULT_TEMPERATURE


def test_ai_clamp_max_history_effective_and_bounds():
    assert ai_assistant.clamp_max_history(20) == 20
    assert ai_assistant.clamp_max_history(30) == 30
    assert ai_assistant.clamp_max_history(1) == 2
    assert ai_assistant.clamp_max_history(99) == 30


def test_ai_clamp_max_history_odd_becomes_even():
    """历史按 user/assistant 成对追加：奇数条会把最旧一对拆散"""
    assert ai_assistant.clamp_max_history(7) == 6
    assert ai_assistant.clamp_max_history(13) == 12


def test_ai_clamp_max_history_fallback_on_dirty():
    assert ai_assistant.clamp_max_history("x") == ai_assistant.MAX_HISTORY
    assert ai_assistant.clamp_max_history(None) == ai_assistant.MAX_HISTORY


def test_ai_build_request_temperature_dual_path():
    """生效值真被消费（传 0.9 用 0.9）；不传回落常量（防消费点写死）"""
    params = {"mode": "cloud", "base_url": "https://x/v1", "model": "m",
              "api_key": "k"}
    messages = [{"role": "user", "content": "hi"}]
    _u, _h, body = ai_assistant.build_request(params, messages, temperature=0.9)
    assert body["temperature"] == 0.9
    _u, _h, body = ai_assistant.build_request(params, messages)
    assert body["temperature"] == ai_assistant.DEFAULT_TEMPERATURE
    # 钳制在 build_request 内部也成立（页面漏钳不会发出越界请求）
    _u, _h, body = ai_assistant.build_request(params, messages, temperature=9)
    assert body["temperature"] == 1.0


# ====================================================================
# ai-text-workshop：发散度 + 单次输出上限
# ====================================================================
def _workshop_body(**over):
    params = {"mode": "cloud", "base_url": "https://x/v1", "model": "m",
              "api_key": "k"}
    _u, _h, body, err = workshop.build_request(params, "总结", "原文", **over)
    assert err == ""
    return body


def test_workshop_build_request_dual_path():
    body = _workshop_body(temperature=0.7, max_tokens=512)
    assert body["temperature"] == 0.7 and body["max_tokens"] == 512
    body = _workshop_body()          # 不传 = 旧宿主路径，回落常量
    assert body["temperature"] == workshop.TEMPERATURE
    assert body["max_tokens"] == workshop.MAX_TOKENS


def test_workshop_build_request_clamps():
    body = _workshop_body(temperature=3.0, max_tokens=99999)
    assert body["temperature"] == 1.0 and body["max_tokens"] == 4096
    body = _workshop_body(temperature=0.01, max_tokens=1)
    assert body["temperature"] == 0.1 and body["max_tokens"] == 256


def test_workshop_clamp_temperature_dirty_fallback():
    assert workshop.clamp_temperature(None) == workshop.TEMPERATURE
    assert workshop.clamp_temperature("hot") == workshop.TEMPERATURE


def test_workshop_clamp_max_tokens_bounds_and_dirty():
    assert workshop.clamp_max_tokens(100) == 256
    assert workshop.clamp_max_tokens(4096) == 4096
    assert workshop.clamp_max_tokens("x") == workshop.MAX_TOKENS
    assert workshop.clamp_max_tokens(None) == workshop.MAX_TOKENS


# ====================================================================
# vault：清剪贴板等待
# ====================================================================
def test_vault_normalize_clear_seconds_accepts_choices():
    assert vault.normalize_clear_seconds("10") == 10
    assert vault.normalize_clear_seconds("30") == 30
    assert vault.normalize_clear_seconds("60") == 60
    assert vault.normalize_clear_seconds(30) == 30    # 手改文件的整数形态


def test_vault_normalize_clear_seconds_fallback():
    assert vault.normalize_clear_seconds("45") == vault.CLIPBOARD_CLEAR_SECONDS
    assert vault.normalize_clear_seconds("1") == vault.CLIPBOARD_CLEAR_SECONDS
    assert vault.normalize_clear_seconds(None) == vault.CLIPBOARD_CLEAR_SECONDS
    assert vault.normalize_clear_seconds(600) == vault.CLIPBOARD_CLEAR_SECONDS


def test_vault_clear_seconds_for_effective_value_used():
    assert vault.clear_seconds_for(_ctx_with(lambda k, fb: "60")) == 60
    assert vault.clear_seconds_for(_ctx_with(lambda k, fb: "10")) == 10


def test_vault_clear_seconds_for_old_host_fallback():
    assert vault.clear_seconds_for(_ctx_old_host()) == vault.CLIPBOARD_CLEAR_SECONDS
    assert vault.clear_seconds_for(None) == vault.CLIPBOARD_CLEAR_SECONDS


def test_vault_clear_seconds_for_dirty_and_raise_fallback():
    assert vault.clear_seconds_for(_ctx_with(lambda k, fb: "45")) == \
        vault.CLIPBOARD_CLEAR_SECONDS
    assert vault.clear_seconds_for(_ctx_boom()) == vault.CLIPBOARD_CLEAR_SECONDS


# ====================================================================
# weekly-report：默认统计范围
# ====================================================================
def test_weekly_resolve_default_range_effective_value_used():
    assert weekly.resolve_default_range(
        _ctx_with(lambda k, fb: "today")) == weekly.RANGE_TODAY
    assert weekly.resolve_default_range(
        _ctx_with(lambda k, fb: "week")) == weekly.RANGE_WEEK


def test_weekly_resolve_default_range_rejects_non_schema_choices():
    """7d/custom 是界面里可选的范围，但设置 schema 只开放两档作默认值"""
    assert weekly.resolve_default_range(
        _ctx_with(lambda k, fb: "7d")) == weekly.RANGE_WEEK
    assert weekly.resolve_default_range(
        _ctx_with(lambda k, fb: "custom")) == weekly.RANGE_WEEK


def test_weekly_resolve_default_range_old_host_fallback():
    assert weekly.resolve_default_range(_ctx_old_host()) == weekly.RANGE_WEEK
    assert weekly.resolve_default_range(None) == weekly.RANGE_WEEK


def test_weekly_resolve_default_range_dirty_and_raise_fallback():
    assert weekly.resolve_default_range(
        _ctx_with(lambda k, fb: "tomorrow")) == weekly.RANGE_WEEK
    assert weekly.resolve_default_range(_ctx_boom()) == weekly.RANGE_WEEK


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
