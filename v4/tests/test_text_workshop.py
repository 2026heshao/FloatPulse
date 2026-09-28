# -*- coding: utf-8 -*-
"""
AI 文本工坊插件 · 纯逻辑层单元测试
====================================================================
覆盖 plugins/ai-text-workshop/plugin.py 中**无 Qt 依赖**的纯函数层：

  - normalize_source   : 原文规整（空 / 非字符串 / 超长截断）
  - build_request      : OpenAI 兼容请求组装（params 来自 ctx.ai.params()：
                         URL 拼接 / 鉴权头 / 消息结构 / 非法参数与空原文
                         的拒绝路径 / 本地模式走宿主端口）
  - parse_reply        : 网络桥结果解析（成功 / 各类错误提示 / 协议错）
  - parse_task_lines   : 「提取待办」行动项解析（三种前缀 / 去重 /
                         （无）标记 / 上限 / 普通句子不误收）
  - first_line_title   : 笔记标题提取
  - manifest 契约       : id / 能力（network+write+ai）/ page / 热键

v1.4.0 起本插件零模型配置：后端参数唯一来源是设置页「AI 总配置」
（经 ctx.ai.params() 实时读取），私有后端配置相关代码已全部删除。
GUI（AiWorkshopPage）走离屏验证脚本 tools/verify_text_workshop.py，
此处不依赖 QApplication，可在任意环境直接运行。
"""

import importlib.util
import json
import os
import sys

# pytest 在真实环境中提供；此处允许缺失，便于无 pytest 时直接运行验证。
try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)
PLUGIN_DIR = os.path.join(ROOT, "plugins", "ai-text-workshop")

# 让插件模块能 import src.*（与 test_logic.py 同一约定）
sys.path.insert(0, BASE)

# 以唯一模块名加载插件入口（避免与其它插件的 plugin.py 在 sys.modules 撞名）
_spec = importlib.util.spec_from_file_location(
    "ai_text_workshop_plugin", os.path.join(PLUGIN_DIR, "plugin.py"))
plugin_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_mod
_spec.loader.exec_module(plugin_mod)

from src.plugin_api import BallAction, BallPlugin  # noqa: E402  (契约可用性)

with open(os.path.join(PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as _f:
    MANIFEST = json.load(_f)


def _params(**over):
    """ctx.ai.params() 的标准云端返回（测试基准）"""
    params = {"mode": "cloud", "base_url": "https://api.deepseek.com/v1",
              "api_key": "sk-test", "model": "deepseek-chat",
              "local_port": 8095}
    params.update(over)
    return params


# ====================================================================
# manifest 与插件类契约
# ====================================================================
def test_manifest_id_matches_plugin():
    assert MANIFEST["id"] == plugin_mod.PLUGIN_ID == "ai-text-workshop"


def test_manifest_declares_network_write_and_ai():
    caps = set(MANIFEST.get("capabilities") or [])
    assert {"network", "write", "ai"} <= caps


def test_manifest_declares_nav_page():
    """页面插件形态（与 ai-assistant 同）：page.title 非空，热键不挂右键菜单"""
    title = str((MANIFEST.get("page") or {}).get("title") or "")
    assert title.strip()
    actions = MANIFEST.get("actions") or []
    assert all(a.get("menu") is False for a in actions)


def test_manifest_action_declared_with_hotkey():
    actions = MANIFEST.get("actions") or []
    ids = {a.get("id") for a in actions}
    assert f"{plugin_mod.PLUGIN_ID}.open" in ids
    hotkeys = {a.get("hotkey") for a in actions}
    # 不与核心保留热键冲突（快速捕捉 / 截图钉屏）
    assert "Ctrl+Alt+K" not in hotkeys and "Ctrl+Alt+S" not in hotkeys


def test_work_actions_keys_unique_and_extract_instruction():
    keys = [k for k, _label, _inst in plugin_mod.WORK_ACTIONS]
    assert len(keys) == len(set(keys))
    extract = dict((k, i) for k, _l, i in plugin_mod.WORK_ACTIONS)["extract_tasks"]
    assert "- [ ]" in extract


def test_plugin_classes_are_contract_subclasses():
    assert issubclass(plugin_mod.AiTextWorkshopPlugin, BallPlugin)
    assert issubclass(plugin_mod.WorkshopAction, BallAction)


def test_plugin_has_no_backend_config_code():
    """v1.4.0 起模型配置收归设置页：私有后端配置的符号必须全部消失"""
    for gone in ("BACKEND_PRESETS", "DEFAULT_CONFIG", "load_config",
                 "save_config", "backend_configured", "LocalServerManager",
                 "CONFIG_FILE"):
        assert not hasattr(plugin_mod, gone), gone


# ====================================================================
# normalize_source
# ====================================================================
def test_normalize_source_rejects_non_string_and_blank():
    assert plugin_mod.normalize_source(None) is None
    assert plugin_mod.normalize_source(123) is None
    assert plugin_mod.normalize_source("   \n  ") is None


def test_normalize_source_strips():
    assert plugin_mod.normalize_source("  hello \n") == "hello"


def test_normalize_source_truncates_with_marker():
    out = plugin_mod.normalize_source("a" * 100, cap=10)
    assert out.startswith("a" * 10)
    assert "100" in out and "截断" in out


def test_normalize_source_no_cap_keeps_all():
    assert plugin_mod.normalize_source("a" * 50, cap=0) == "a" * 50


# ====================================================================
# build_request（params 来自 ctx.ai.params()）
# ====================================================================
def test_build_request_happy_path():
    url, headers, body, err = plugin_mod.build_request(
        _params(), "总结要点", "  一段原文 \n", max_source_chars=8000)
    assert err == ""
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert headers == {"Authorization": "Bearer sk-test"}
    assert body["model"] == "deepseek-chat"
    assert body["stream"] is False
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user"]
    user_content = body["messages"][1]["content"]
    assert "总结要点" in user_content and "一段原文" in user_content


def test_build_request_strips_trailing_slash_of_base():
    url, _h, _b, err = plugin_mod.build_request(
        _params(base_url="https://example.com/v1/"), "x", "y")
    assert err == "" and url == "https://example.com/v1/chat/completions"


def test_build_request_empty_base_and_model_rejected():
    assert plugin_mod.build_request(_params(base_url=""), "x", "y")[3]
    assert plugin_mod.build_request(_params(model=""), "x", "y")[3]


def test_build_request_bad_scheme_rejected():
    _u, _h, _b, err = plugin_mod.build_request(
        _params(base_url="ftp://x"), "x", "y")
    assert "http" in err


def test_build_request_empty_source_rejected():
    _u, _h, _b, err = plugin_mod.build_request(_params(), "x", "   \n")
    assert "原文为空" in err


def test_build_request_long_source_truncated():
    _u, _h, body, err = plugin_mod.build_request(
        _params(), "x", "a" * 20000, max_source_chars=100)
    assert err == ""
    assert "20000" in body["messages"][1]["content"]


def test_build_request_dirty_params_treated_as_cloud():
    """mode 缺失/脏值按云端处理，不抛异常"""
    _u, _h, body, err = plugin_mod.build_request(
        {"mode": None, "base_url": "https://x/v1", "model": "m"}, "x", "y")
    assert err == ""
    assert body["model"] == "m"


# ====================================================================
# build_request · 本地模式（mode="local" → 宿主本地 llama-server）
# ====================================================================
def test_build_request_local_mode():
    params = _params(mode="local", local_port=8095, api_key="")
    url, headers, body, err = plugin_mod.build_request(params, "总结", "原文")
    assert err == ""
    assert url == "http://127.0.0.1:8095/v1/chat/completions"
    assert headers == {}                     # 本地服务不注入鉴权头
    assert body["model"] == "local"          # llama-server 忽略模型名


def test_build_request_local_bad_port_falls_back_to_default():
    params = _params(mode="local", local_port="bad")
    url, _h, _b, err = plugin_mod.build_request(params, "x", "y")
    assert err == "" and ":8095/v1/chat/completions" in url


def test_build_request_local_ignores_cloud_fields():
    """本地模式不读云端地址——总配置里两套参数独立"""
    params = _params(mode="local", base_url="", model="")
    url, _h, _b, err = plugin_mod.build_request(params, "x", "y")
    assert err == "" and url.startswith("http://127.0.0.1:")


# ====================================================================
# parse_reply
# ====================================================================
def test_parse_reply_ok():
    body = json.dumps({"choices": [{"message": {"content": "  结果 "}}]})
    reply, err = plugin_mod.parse_reply({"ok": True, "body": body})
    assert err is None and reply == "结果"


def test_parse_reply_http_401_hint():
    reply, err = plugin_mod.parse_reply(
        {"ok": False, "error": "HTTP 401 Unauthorized", "body": ""})
    assert reply is None and "key" in err


def test_parse_reply_timeout_hint():
    _reply, err = plugin_mod.parse_reply(
        {"ok": False, "error": "Connection timed out", "body": ""})
    assert "重试" in err


def test_parse_reply_refused_hint():
    _reply, err = plugin_mod.parse_reply(
        {"ok": False, "error": "Connection refused", "body": ""})
    assert "服务" in err


def test_parse_reply_bad_json_is_protocol_error():
    reply, err = plugin_mod.parse_reply({"ok": True, "body": "not-json"})
    assert reply is None and "OpenAI 协议" in err


def test_parse_reply_missing_choices_is_protocol_error():
    reply, err = plugin_mod.parse_reply(
        {"ok": True, "body": json.dumps({"choices": []})})
    assert reply is None and "OpenAI 协议" in err


# ====================================================================
# parse_task_lines
# ====================================================================
def test_parse_task_lines_checkbox_format():
    reply = "- [ ] 给客户回邮件\n- [ ] 交周报\n"
    assert plugin_mod.parse_task_lines(reply) == ["给客户回邮件", "交周报"]


def test_parse_task_lines_numbered_and_bullet_fallback():
    reply = "1. 打印合同\n- 复印身份证\n"
    assert plugin_mod.parse_task_lines(reply) == ["打印合同", "复印身份证"]


def test_parse_task_lines_checked_box_still_collected():
    reply = "- [x] 已完成的事\n"
    assert plugin_mod.parse_task_lines(reply) == ["已完成的事"]


def test_parse_task_lines_dedupe_case_insensitive():
    reply = "- [ ] Email Bob\n- [ ] email bob\n"
    assert plugin_mod.parse_task_lines(reply) == ["Email Bob"]


def test_parse_task_lines_no_task_marks():
    for mark in ("（无）", "(无)", "无"):
        assert plugin_mod.parse_task_lines(mark) == []


def test_parse_task_lines_ignores_prose_and_headers():
    reply = "# 待办清单\n这是普通句子，不是任务。\n- [ ] 真任务\n"
    assert plugin_mod.parse_task_lines(reply) == ["真任务"]


def test_parse_task_lines_cap():
    reply = "\n".join(f"- [ ] 任务{i}" for i in range(30))
    assert len(plugin_mod.parse_task_lines(reply)) == 20


def test_parse_task_lines_empty_reply():
    assert plugin_mod.parse_task_lines("") == []
    assert plugin_mod.parse_task_lines(None) == []


# ====================================================================
# first_line_title
# ====================================================================
def test_first_line_title_takes_first_nonempty_and_strips_heading():
    assert plugin_mod.first_line_title("\n## 主题：周报\n正文") == "主题：周报"
    assert plugin_mod.first_line_title("hello world") == "hello world"


def test_first_line_title_truncates():
    assert len(plugin_mod.first_line_title("长" * 50)) == 41   # 40 字 + …


def test_first_line_title_empty_fallback():
    assert plugin_mod.first_line_title("   ") == "AI 文本工坊结果"


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
