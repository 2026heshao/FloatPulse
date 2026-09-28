# -*- coding: utf-8 -*-
"""AI 助手「动作协议」纯函数回归（不构造窗口，无头可跑）。

背景（2026-09-28 用户需求）：AI 助手要能按用户指令改应用内数据。
插件侧不做原生 function calling——本地 Qwen3-4B 量化版对 JSON schema
遵循度不稳，改成「回复末尾输出 ```actions 块」的提示词约定，两端通吃。
本文件钉死解析器的容错契约（它决定了 AI 有没有可能改错数据）：

  A 动作白名单与分级（add_* 直接执行 / 改删必须二次确认）
  B parse_actions 容错：坏 JSON 整块不执行、单条不合格只丢那条、
    id 必须真实存在于快照（挡模型编造编号）、参数类型错要丢、
    超上限截断、bool 不算整数 id
  C 正文语义：找到动作块 → 剥掉后返回**可能是空串**的正文（纯动作回复
    不能把 JSON 当正文显示）；没找到 → 原样返回
  D 正文里的普通 JSON 示例不得被误当动作块删掉
  E describe_action 产出的是给 QLabel 看的纯文本（不能有 Markdown 标记）
  F build_system_prompt 只在授权 manage 时才下发协议

**任何情况下都不猜**——少做比做错好。
"""

import importlib.util
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "ai-assistant", "plugin.py")
_MOD_NAME = "fp_test_ai_assistant_plugin"


@pytest.fixture(scope="module")
def plug():
    """按插件加载器的命名方式加载真实 plugin.py（纯函数，不需 QApplication）"""
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


SNAP = {
    "tasks": [{"task_id": 3, "title": "写周报", "done": False},
              {"task_id": 7, "title": "买咖啡豆", "done": True}],
    "fragments": [{"fragment_id": 2, "content": "https://a.com"}],
    "notes": [{"note_id": 5, "title": "周会记录", "content": "正文"}],
}


def _block(*items):
    import json
    return "```actions\n%s\n```" % json.dumps({"actions": list(items)})


# ---------------- A. 白名单与分级 ----------------
class TestOpTable:
    def test_write_level_ops(self, plug):
        for op in ("add_task", "add_fragment", "add_note"):
            assert plug.ACTION_OPS[op] == "write"

    def test_manage_level_ops(self, plug):
        for op in ("complete_task", "reopen_task", "update_task",
                   "delete_task", "update_fragment", "delete_fragment",
                   "update_note", "delete_note"):
            assert plug.ACTION_OPS[op] == "manage"

    def test_every_manage_op_needs_id_and_add_ops_do_not(self, plug):
        for op in plug.ACTION_OPS:
            assert (op in plug._OP_TARGET) == (plug.ACTION_OPS[op] == "manage")

    def test_action_upper_bound_is_sane(self, plug):
        assert plug.MAX_ACTIONS >= 1


# ---------------- B. parse_actions 容错 ----------------
class TestParseActions:
    def test_no_block_returns_text_untouched(self, plug):
        text = "这是普通回答，没有动作块。"
        assert plug.parse_actions(text, SNAP) == (text, [], [])

    def test_valid_add_task_is_write_level(self, plug):
        clean, acts, errs = plug.parse_actions(
            "已建好。\n" + _block({"op": "add_task", "title": "写季度总结"}),
            SNAP)
        assert errs == []
        assert len(acts) == 1
        assert acts[0]["op"] == "add_task"
        assert acts[0]["level"] == "write"
        assert clean == "已建好。"

    def test_valid_manage_op_carries_real_title(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "complete_task", "id": 3}), SNAP)
        assert errs == []
        assert acts[0]["level"] == "manage"
        assert "写周报" in acts[0]["desc"]

    @pytest.mark.parametrize("rid", [999, 0, -1, True, "3", 3.0])
    def test_bad_id_is_dropped(self, plug, rid):
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_task", "id": rid}), SNAP)
        assert acts == []
        assert errs

    def test_unknown_op_is_dropped(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "drop_database", "id": 3}), SNAP)
        assert acts == []
        assert any("未知动作" in e for e in errs)

    def test_broken_json_executes_nothing(self, plug):
        _, acts, errs = plug.parse_actions(
            '一些结论\n```actions\n{"actions":[{"op":"add_task", }\n```', SNAP)
        assert acts == []
        assert errs

    def test_non_dict_payload_executes_nothing(self, plug):
        _, acts, errs = plug.parse_actions(
            "```actions\n[1, 2, 3]\n```", SNAP)
        assert acts == []
        assert errs

    def test_missing_actions_key_executes_nothing(self, plug):
        _, acts, errs = plug.parse_actions(
            '```actions\n{"ops": []}\n```', SNAP)
        assert acts == []
        assert errs

    def test_over_limit_is_truncated(self, plug):
        items = [{"op": "add_fragment", "content": "x%d" % i}
                 for i in range(plug.MAX_ACTIONS + 5)]
        _, acts, errs = plug.parse_actions(_block(*items), SNAP)
        assert len(acts) == plug.MAX_ACTIONS
        assert any("超过上限" in e for e in errs)

    def test_non_string_title_is_dropped(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "add_task", "title": 123}), SNAP)
        assert acts == []
        assert errs

    def test_blank_title_is_dropped(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "add_note", "title": "   ", "content": "x"}), SNAP)
        assert acts == []
        assert errs

    def test_mixed_batch_keeps_legal_items(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "add_task", "title": "合法新增"},
                   {"op": "delete_note", "id": 888}), SNAP)
        assert len(acts) == 1
        assert acts[0]["op"] == "add_task"
        assert errs

    def test_extra_id_on_add_op_is_ignored(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "add_task", "title": "有 id 也不该有", "id": 3}), SNAP)
        assert errs == []
        assert "id" not in acts[0]["args"]

    def test_no_snapshot_means_no_valid_target(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_task", "id": 3}), None)
        assert acts == []
        assert errs

    def test_non_dict_item_is_skipped(self, plug):
        _, acts, errs = plug.parse_actions(
            '```actions\n{"actions":["nope"]}\n```', SNAP)
        assert acts == []
        assert errs


# ---------------- C. 正文语义 ----------------
class TestCleanText:
    def test_pure_action_reply_yields_empty_body(self, plug):
        """纯动作回复必须返回空正文——否则调用方会把 JSON 当正文显示"""
        clean, acts, errs = plug.parse_actions(
            _block({"op": "delete_task", "id": 7}), SNAP)
        assert clean == ""
        assert len(acts) == 1
        assert errs == []

    def test_body_is_stripped_of_fence_markers(self, plug):
        clean, _, _ = plug.parse_actions(
            "结论在此。\n" + _block({"op": "add_fragment", "content": "x"}),
            SNAP)
        assert "```" not in clean
        assert "{" not in clean
        assert clean == "结论在此。"

    def test_plain_json_example_is_not_an_action_block(self, plug):
        """用户贴一段 JSON 问「这是什么」——不能把 JSON 从正文里删掉"""
        text = '这段配置就是 {"name": "FloatPulse", "version": 4} 的意思。'
        assert plug.parse_actions(text, SNAP) == (text, [], [])

    def test_json_fence_without_actions_key_is_kept(self, plug):
        text = '示例如下：\n```json\n{"name": "FloatPulse"}\n```'
        clean, acts, errs = plug.parse_actions(text, SNAP)
        assert acts == []
        assert "FloatPulse" in clean

    def test_empty_text_is_safe(self, plug):
        assert plug.parse_actions("", SNAP) == ("", [], [])


# ---------------- D. 描述文本 ----------------
class TestDescribe:
    def test_delete_desc_has_no_markdown(self, plug):
        _, acts, _ = plug.parse_actions(
            _block({"op": "delete_note", "id": 5}), SNAP)
        desc = acts[0]["desc"]
        assert "**" not in desc
        assert "删除" in desc
        assert "周会记录" in desc

    def test_label_falls_back_to_bare_id_when_snapshot_missing(self, plug):
        desc = plug.describe_action("delete_task", {"id": 42}, {})
        assert "42" in desc


# ---------------- E. 提示词 ----------------
class TestPrompt:
    _MARK = "== 你可以执行的应用操作 =="

    def test_protocol_only_when_manage_granted(self, plug):
        assert self._MARK in plug.build_system_prompt(None, can_manage=True)
        assert self._MARK not in plug.build_system_prompt(None, can_manage=False)

    def test_protocol_defaults_to_off(self, plug):
        assert self._MARK not in plug.build_system_prompt(None)

    def test_protocol_lists_every_op(self, plug):
        prompt = plug.build_system_prompt(None, can_manage=True)
        for op in plug.ACTION_OPS:
            assert op in prompt
