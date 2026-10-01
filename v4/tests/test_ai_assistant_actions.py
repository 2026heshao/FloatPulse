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
    "knowledge": [
        {"num": 1, "text": "第一条知识正文", "preview": "第一条知识正文",
         "hash": "a1" * 8},
        {"num": 2, "text": "第二条知识正文", "preview": "第二条知识正文",
         "hash": "b2" * 8},
    ],
}


def _block(*items):
    import json
    return "```actions\n%s\n```" % json.dumps({"actions": list(items)})


# ---------------- A. 白名单与分级 ----------------
class TestOpTable:
    def test_write_level_ops(self, plug):
        for op in ("add_task", "add_fragment", "add_note", "add_knowledge"):
            assert plug.ACTION_OPS[op] == "write"

    def test_manage_level_ops(self, plug):
        for op in ("complete_task", "reopen_task", "update_task",
                   "delete_task", "update_fragment", "delete_fragment",
                   "update_note", "delete_note",
                   "update_knowledge", "delete_knowledge",
                   "clear_tasks"):
            assert plug.ACTION_OPS[op] == "manage"

    def test_every_manage_op_needs_id_and_add_ops_do_not(self, plug):
        # clear_tasks 是唯一例外：manage 级但作用于全集，无需 id
        for op in plug.ACTION_OPS:
            if op == "clear_tasks":
                assert op not in plug._OP_TARGET
                continue
            assert (op in plug._OP_TARGET) == (plug.ACTION_OPS[op] == "manage")

    def test_knowledge_targets_resolve_by_num(self, plug):
        for op in ("update_knowledge", "delete_knowledge"):
            assert plug._OP_TARGET[op][0] == "knowledge"
            assert plug._OP_TARGET[op][1] == "num"

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

    def test_clear_tasks_needs_no_id(self, plug):
        """clear_tasks 无参数也合法（2026-10-01 清空任务编造编号问题的修复）"""
        clean, acts, errs = plug.parse_actions(
            "好的。\n" + _block({"op": "clear_tasks"}), SNAP)
        assert errs == []
        assert len(acts) == 1
        assert acts[0]["op"] == "clear_tasks"
        assert acts[0]["level"] == "manage"
        assert clean == "好的。"

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


# ---------------- B2. 知识库动作（位置型标识 + 指纹绑定） ----------------
class TestKnowledgeActions:
    def test_add_knowledge_is_write_level(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "add_knowledge", "content": "新增一条知识正文"}), SNAP)
        assert errs == []
        assert acts[0]["level"] == "write"
        assert "新增一条知识正文" in acts[0]["desc"]

    def test_add_knowledge_needs_non_empty_content(self, plug):
        for bad in (None, "", "   ", 123):
            _, acts, errs = plug.parse_actions(
                _block({"op": "add_knowledge", "content": bad}), SNAP)
            assert acts == []
            assert errs

    def test_delete_knowledge_binds_snapshot_hash(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_knowledge", "id": 2}), SNAP)
        assert errs == []
        assert acts[0]["level"] == "manage"
        assert acts[0]["args"]["expect_hash"] == "b2" * 8

    def test_update_knowledge_binds_hash_and_content(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "update_knowledge", "id": 1,
                    "content": "改写后的知识正文"}), SNAP)
        assert errs == []
        assert acts[0]["args"]["expect_hash"] == "a1" * 8
        assert acts[0]["args"]["content"] == "改写后的知识正文"

    def test_update_knowledge_requires_content(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "update_knowledge", "id": 1}), SNAP)
        assert acts == []
        assert any("content" in e for e in errs)

    @pytest.mark.parametrize("bad", [123, ["x"], {"a": 1}, ""])
    def test_update_knowledge_rejects_bad_content_type(self, plug, bad):
        _, acts, errs = plug.parse_actions(
            _block({"op": "update_knowledge", "id": 1, "content": bad}), SNAP)
        assert acts == []
        assert errs

    def test_knowledge_num_must_exist_in_snapshot(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_knowledge", "id": 99}), SNAP)
        assert acts == []
        assert any("99" in e for e in errs)

    def test_knowledge_num_zero_is_rejected(self, plug):
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_knowledge", "id": 0}), SNAP)
        assert acts == []
        assert errs

    def test_missing_hash_in_snapshot_drops_action(self, plug):
        """快照里没带指纹 → 无法保证不改错段落，宁可丢掉这条"""
        snap = {"knowledge": [{"num": 1, "text": "无指纹段落"}]}
        _, acts, errs = plug.parse_actions(
            _block({"op": "delete_knowledge", "id": 1}), snap)
        assert acts == []
        assert any("指纹" in e for e in errs)

    def test_add_knowledge_has_no_id(self, plug):
        _, acts, _ = plug.parse_actions(
            _block({"op": "add_knowledge", "content": "新增知识", "id": 1}),
            SNAP)
        assert "id" not in acts[0]["args"]

    def test_knowledge_desc_is_plain_text(self, plug):
        _, acts, _ = plug.parse_actions(
            _block({"op": "delete_knowledge", "id": 1}), SNAP)
        desc = acts[0]["desc"]
        assert "**" not in desc
        assert "删除" in desc
        assert "第一条知识正文" in desc

    def test_update_knowledge_desc_names_the_paragraph(self, plug):
        _, acts, _ = plug.parse_actions(
            _block({"op": "update_knowledge", "id": 2,
                    "content": "改写后的正文"}), SNAP)
        assert "第二条知识正文" in acts[0]["desc"]


class TestDeleteOrdering:
    """知识库删除必须降序执行：删第 3 段会让原第 5 段变成第 4 段"""

    def _acts(self, plug, *pairs):
        return [{"op": op, "args": {"id": num}} for op, num in pairs]

    def test_knowledge_deletes_go_last_descending(self, plug):
        acts = self._acts(plug, ("delete_knowledge", 3),
                          ("add_task", 0),
                          ("delete_knowledge", 7),
                          ("delete_knowledge", 5))
        ordered = plug._order_actions(acts)
        got = [(a["op"], a["args"].get("id")) for a in ordered]
        assert got == [("add_task", 0), ("delete_knowledge", 7),
                       ("delete_knowledge", 5), ("delete_knowledge", 3)]

    def test_other_actions_keep_relative_order(self, plug):
        acts = self._acts(plug, ("delete_note", 9), ("add_task", 0),
                          ("update_task", 4))
        assert plug._order_actions(acts) == acts

    def test_empty_batch_is_safe(self, plug):
        assert plug._order_actions([]) == []

    def test_bool_or_junk_num_does_not_crash(self, plug):
        acts = [{"op": "delete_knowledge", "args": {"id": True}},
                {"op": "delete_knowledge", "args": {"id": "x"}},
                {"op": "delete_knowledge", "args": {"id": 2}}]
        ordered = plug._order_actions(acts)
        assert [a["args"]["id"] for a in ordered][0] == 2


# ---------------- B3. 知识库数据块 ----------------
class TestKnowledgeDataBlock:
    def test_format_lists_num_and_head(self, plug):
        text = plug.format_knowledge(
            [{"num": 1, "text": "第一条知识正文"}, {"num": 2,
                                              "text": "第二条知识正文"}], 6000)
        assert "[1] 第一条知识正文" in text
        assert "[2] 第二条知识正文" in text

    def test_format_handles_empty_and_none(self, plug):
        assert plug.format_knowledge([], 6000) == "（知识库为空）"
        assert plug.format_knowledge(None, 6000) == "（知识库为空）"

    def test_format_truncates(self, plug):
        rows = [{"num": i, "text": "内容" * 30} for i in range(1, 200)]
        text = plug.format_knowledge(rows, 500)
        assert "已截断" in text

    def test_format_single_line_head(self, plug):
        text = plug.format_knowledge(
            [{"num": 1, "text": "第一行\n第二行"}], 6000)
        assert "第一行 第二行" in text

    def test_build_data_block_mentions_num(self, plug):
        class _Data:
            def knowledge(self):
                return [{"num": 7, "text": "第七段内容"}]

        class _Ctx:
            data = _Data()

        block = plug.build_data_block(_Ctx(), "knowledge", 6000)
        assert "[7] 第七段内容" in block
        assert "编号" in block

    def test_quick_commands_include_knowledge(self, plug):
        kinds = [k for _, k, _ in plug.QUICK_COMMANDS]
        assert "knowledge" in kinds


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

    def test_clear_tasks_desc_counts_snapshot(self, plug):
        desc = plug.describe_action("clear_tasks", {}, SNAP)
        assert "清空" in desc and "2 条" in desc
        assert "**" not in desc          # QLabel 不渲染 Markdown


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

    def test_protocol_forbids_fabricated_success(self, plug):
        """2026-10-01 实测：模型谎报「已清空」但动作全被跳过——协议必须明令禁止"""
        prompt = plug.build_system_prompt(None, can_manage=True)
        assert "禁止声称已删除" in prompt
        assert "clear_tasks{}" in prompt


# ---------------- F. 执行层：clear_tasks（无 QApplication，桩替身） ----------------
class _FakeManage:
    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.deleted = []
        self.tokens = []

    def delete_task(self, tid):
        if tid in self.fail_ids:
            return 0
        token = tid * 10
        self.deleted.append(tid)
        self.tokens.append(token)
        return token


class _FakeData:
    def tasks(self):
        return [{"task_id": 3, "title": "甲"}, {"task_id": 7, "title": "乙"},
                {"task_id": 9, "title": "丙"}]

    def fragments(self):
        return []

    def notes(self):
        return []

    def knowledge(self):
        return []


class _FakeCtx:
    def __init__(self, fail_ids=()):
        self.manage = _FakeManage(fail_ids)
        self.data = _FakeData()
        self.write = None                   # clear 分支不碰 write，占位即可


def _fake_page(plug, ctx, cards=None):
    """只绑定真方法的轻量替身：绝不实例化 QWidget（无需 QApplication）"""
    import types
    from types import SimpleNamespace
    page = SimpleNamespace()
    page._ctx = ctx
    page._snapshot = types.MethodType(plug.AiChatPage._snapshot, page)
    page._run_action = types.MethodType(plug.AiChatPage._run_action, page)
    page._execute_and_report = types.MethodType(
        plug.AiChatPage._execute_and_report, page)
    page._add_result_card = types.MethodType(
        lambda self, lines, toks: cards.append((list(lines), list(toks))),
        page) if cards is not None else None
    return page


class TestRunActionClearTasks:
    def test_clear_all_returns_token_list(self, plug):
        page = _fake_page(plug, _FakeCtx())
        ok, msg, tokens = page._run_action(
            {"op": "clear_tasks", "args": {}, "desc": "清空全部任务"})
        assert ok is True
        assert "3 条" in msg
        assert tokens == [30, 70, 90]        # 每条一个撤销令牌
        assert page._ctx.manage.deleted == [3, 7, 9]

    def test_clear_partial_failure_reports_honestly(self, plug):
        page = _fake_page(plug, _FakeCtx(fail_ids={7}))
        ok, msg, tokens = page._run_action(
            {"op": "clear_tasks", "args": {}})
        assert ok is True                    # 部分成功也算成功，但如实报告
        assert "2/3" in msg
        assert tokens == [30, 90]

    def test_clear_empty_list_is_honest_noop(self, plug):
        ctx = _FakeCtx()
        ctx.data = _FakeData()
        page = _fake_page(plug, ctx)
        page._snapshot = lambda: {"tasks": []}
        ok, msg, tokens = page._run_action(
            {"op": "clear_tasks", "args": {}})
        assert ok is True
        assert "空" in msg
        assert tokens == []

    def test_execute_and_report_extends_tokens(self, plug):
        """_execute_and_report 必须把 list 令牌逐个收集（否则清空后没法撤销）"""
        cards = []
        page = _fake_page(plug, _FakeCtx(), cards)
        page._execute_and_report(
            [{"op": "clear_tasks", "args": {}, "desc": "清空全部任务",
              "level": "manage"}])
        lines, tokens = cards[0]
        assert tokens == [30, 70, 90]
        assert any("已清空全部任务" in ln for ln in lines)
