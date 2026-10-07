# -*- coding: utf-8 -*-
"""AI 助手「轻量智能路由」回归（v1.16.0；纯函数 + 替身页面，无头可跑）。

背景（2026-10-04 用户实测）：自由聊天说「用50条鼻炎常识填充知识库」，
AI 回「任务、碎片和笔记数据为空」「请提供知识库的内容」——自由发送路径
``_dispatch(text, None, text)`` 的 data_block=None，模型看不到任何应用
数据；数据只在快捷按钮路径才附带。本文件钉死三件事：

  A detect_data_kinds 纯逻辑：各关键词命中、多命中排序稳定、
    无命中空表、空串 / None 安全、大小写不敏感
  B 防假护栏双向钉子：路由结果**真被消费**——自由发送路径命中的
    kind 真的进了 user_content（数据块在消息里），未命中路径
    data_block 保持 None（行为等价钉子，防「路由写了但没人调」哑弹）；
    自定义指令路径**刻意**不附加数据的语义零改动
  C build_system_prompt：kb_brief 注入 / 缺省不注入、生成类任务指令
    只随 can_manage 下发、custom_rules 兼容不回归
  D 版本钉子：manifest.json 与 plugin.py 的 version 同源 1.16.0
"""

import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                           "ai-assistant", "plugin.py")
MANIFEST_PATH = os.path.join(os.path.dirname(BASE), "plugins",
                             "ai-assistant", "manifest.json")
_MOD_NAME = "fp_test_ai_assistant_smart_route"

NEW_VERSION = "1.16.0"


@pytest.fixture(scope="module")
def plug():
    """按插件加载器的命名方式加载真实 plugin.py（不实例化 QWidget）"""
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- 替身数据 / 上下文 / 页面 ----------------
class _FakeData:
    """应用内数据快照替身（四路数据源齐全，知识库有真段落）"""

    def tasks(self):
        return [{"task_id": 1, "title": "写周报", "done": False}]

    def fragments(self):
        return [{"fragment_id": 2, "content": "随手记的一句话"}]

    def notes(self):
        return [{"note_id": 3, "title": "周会记录", "content": "正文"}]

    def knowledge(self):
        return [{"num": i + 1, "text": f"第{i + 1}条知识正文", "hash": f"h{i}"}
                for i in range(7)]


class _FakeCtx:
    data = _FakeData()


def _fake_page(plug, ctx, text=""):
    """只绑定真方法的轻量替身：绝不实例化 QWidget（无需 QApplication）。

    _dispatch 走到 http_post_json_async 为止（捕获请求体），其余 UI 侧
    （气泡 / 忙态 / 思考动画）全部替身。
    """
    page = SimpleNamespace()
    page._ctx = ctx
    page._busy = False
    page._req_id = 0          # 在途请求代数（中断令牌守卫配套；真 _dispatch 会自增）
    page._cfg = {"max_data_chars": 6000, "custom_rules": []}
    page._history = []
    page._temperature = 0.4
    page._can_manage = lambda: False
    page._attached_params = lambda: {"mode": "cloud", "base_url": "https://x/v1",
                                     "model": "m", "api_key": ""}
    # UI 替身
    page._input = SimpleNamespace(toPlainText=lambda: text, clear=lambda: None)
    page._status = SimpleNamespace(setText=lambda *_a: None)
    page._send_btn = SimpleNamespace(setEnabled=lambda *_a: None)
    page.add_bubble = lambda *_a, **_k: None
    page._set_busy = lambda *_a, **_k: None
    page._show_thinking = lambda: None
    # 真方法
    import types
    page._can_write = types.MethodType(plug.AiChatPage._can_write, page)
    page._on_reply = lambda *_a, **_k: None      # 捕获即止，不消费回复
    page._kb_brief = types.MethodType(plug.AiChatPage._kb_brief, page)
    page._auto_data_block = types.MethodType(
        plug.AiChatPage._auto_data_block, page)
    page._on_send_clicked = types.MethodType(
        plug.AiChatPage._on_send_clicked, page)
    page._dispatch = types.MethodType(plug.AiChatPage._dispatch, page)
    page._send_custom_quick = types.MethodType(
        plug.AiChatPage._send_custom_quick, page)
    # 捕获出站请求
    page.sent = []

    def _post(_url, _headers, body, **_kw):
        page.sent.append(body)
        return True

    ctx.http_post_json_async = _post
    return page


def _last_messages(page):
    """取捕获请求里的 messages 列表（build_request 产出的 body）。

    宿主契约（plugin_api.http_post_json_async）：body 必须是 dict（桥负责
    JSON 序列化，非 dict 直接拒发）——所以正常路径捕获到的就是 dict；
    保留 str 分支只作容错（防有人回退成手写 json.dumps 传串仍可解析）。
    """
    body = page.sent[-1]
    if isinstance(body, (str, bytes, bytearray)):
        body = json.loads(body)
    return body["messages"]


def _last_user_content(page):
    return _last_messages(page)[-1]["content"]


# ---------------- A. detect_data_kinds 纯逻辑 ----------------
class TestDetectDataKinds:
    def test_knowledge_keywords(self, plug):
        for t in ("把知识库整理一下", "帮我看看第几段落最长",
                  "生成50条鼻炎常识存到知识库"):
            assert plug.detect_data_kinds(t) == ["knowledge"], t

    def test_tasks_keywords(self, plug):
        for t in ("总结一下我的任务", "今天有哪些待办", "my TODO list",
                  "快到截止日期了吗"):
            assert plug.detect_data_kinds(t) == ["tasks"], t

    def test_fragments_keywords(self, plug):
        for t in ("整理碎片", "随手记的东西能翻出来吗"):
            assert plug.detect_data_kinds(t) == ["fragments"], t

    def test_notes_keywords(self, plug):
        assert plug.detect_data_kinds("把笔记整理成周报") == ["notes"]

    def test_multi_hit_fixed_order(self, plug):
        # 命中顺序与消息里的出现顺序无关，按 tasks/fragments/notes/knowledge
        got = plug.detect_data_kinds("知识库和笔记、碎片、任务都整理一下")
        assert got == ["tasks", "fragments", "notes", "knowledge"]
        got2 = plug.detect_data_kinds("常识、笔记、待办")
        assert got2 == ["tasks", "notes", "knowledge"]

    def test_no_hit_returns_empty(self, plug):
        for t in ("今天天气怎么样", "帮我写一首诗", "1+1等于几"):
            assert plug.detect_data_kinds(t) == [], t

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_and_none_safe(self, plug, bad):
        assert plug.detect_data_kinds(bad) == []

    def test_case_insensitive(self, plug):
        assert plug.detect_data_kinds("TODO 里有几件事") == ["tasks"]
        assert plug.detect_data_kinds("Todo List 检查") == ["tasks"]

    def test_deterministic_and_pure(self, plug):
        t = "任务 笔记 常识"
        first = plug.detect_data_kinds(t)
        assert first == plug.detect_data_kinds(t) == plug.detect_data_kinds(t)
        # 无副作用：同一输入重复调用结果一致（列表元素与顺序都钉死）
        assert first == ["tasks", "notes", "knowledge"]

    def test_order_table_matches_known_kinds(self, plug):
        """钉死返回值域：四类、无 weekly（weekly 只属于快捷指令）"""
        assert plug._DATA_KIND_ORDER == ("tasks", "fragments", "notes",
                                         "knowledge")
        assert all(k in plug._DATA_KIND_ORDER
                   for k, _ in plug._KIND_KEYWORDS)


# ---------------- B. 路由结果真被消费（防假护栏双向钉子） ----------------
class TestRouteConsumed:
    def test_hit_kind_lands_in_user_content(self, plug):
        """「把知识库整理一下」→ 数据块真的进了 user_content"""
        page = _fake_page(plug, _FakeCtx(), "把知识库整理一下")
        page._on_send_clicked()
        assert len(page.sent) == 1
        content = _last_user_content(page)
        assert content.startswith("把知识库整理一下")
        assert plug.AUTO_DATA_NOTE in content          # 自动附上说明
        assert "[1] 第1条知识正文" in content           # 知识库编号摘要块

    def test_multi_hit_blocks_all_attached_in_order(self, plug):
        page = _fake_page(plug, _FakeCtx(), "任务和笔记都看看")
        page._on_send_clicked()
        content = _last_user_content(page)
        idx_task = content.find("以下是应用内的全部任务数据")
        idx_note = content.find("全部笔记数据")
        assert idx_task != -1 and idx_note != -1
        assert idx_task < idx_note                      # 固定顺序

    def test_no_hit_keeps_byte_equivalence(self, plug):
        """无关键词自由聊天 = 现状逐字节等价：user_content 就是原文"""
        text = "今天天气怎么样"
        page = _fake_page(plug, _FakeCtx(), text)
        page._on_send_clicked()
        assert _last_user_content(page) == text

    def test_system_prompt_carries_kb_brief_in_request(self, plug):
        """系统提示词在真实发送链路里注入了知识库概况"""
        page = _fake_page(plug, _FakeCtx(), "随便聊聊")
        page._on_send_clicked()
        system = _last_messages(page)[0]["content"]
        assert "知识库概况" in system
        assert "现有 7 段" in system

    def test_custom_quick_still_sends_no_data(self, plug):
        """自定义指令路径零改动：含关键词也**刻意**不附加应用内数据"""
        page = _fake_page(plug, _FakeCtx(), "占位")
        page._busy = False
        page._send_custom_quick("把我的笔记总结一下")
        content = _last_user_content(page)
        assert content == "把我的笔记总结一下"
        assert plug.AUTO_DATA_NOTE not in content

    def test_kb_brief_absent_when_source_missing(self, plug):
        """无 knowledge 数据源（如老宿主）→ 概况不注入、发送照常"""

        class _NoKb:
            def tasks(self):
                return []

            def fragments(self):
                return []

            def notes(self):
                return []

        class _CtxNoKb:
            data = _NoKb()

        page = _fake_page(plug, _CtxNoKb(), "随便聊聊")
        page._on_send_clicked()
        assert "知识库概况" not in _last_messages(page)[0]["content"]


# ---------------- C. build_system_prompt / build_kb_brief ----------------
class TestSystemPrompt:
    def test_kb_brief_injected_when_given(self, plug):
        brief = plug.build_kb_brief([{"num": 1, "text": "第一条"},
                                     {"num": 2, "text": "第二条"}])
        assert brief.startswith("知识库概况")
        assert "现有 2 段" in brief
        assert "[1] 第一条" in brief and "[2] 第二条" in brief
        prompt = plug.build_system_prompt(None, kb_brief=brief)
        assert brief in prompt

    def test_kb_brief_omitted_by_default(self, plug):
        assert "知识库概况" not in plug.build_system_prompt(None)

    def test_kb_brief_empty_or_dirty_not_injected(self, plug):
        assert plug.build_kb_brief([]) == ""
        assert plug.build_kb_brief(None) == ""
        assert plug.build_kb_brief(["脏数据", 3, None]) == ""
        # 空串 brief = 不追加任何段落
        base = plug.build_system_prompt(None)
        assert plug.build_system_prompt(None, kb_brief="") == base

    def test_kb_brief_caps_at_five_items(self, plug):
        rows = [{"num": i + 1, "text": f"第{i + 1}条"} for i in range(9)]
        brief = plug.build_kb_brief(rows)
        assert "现有 9 段" in brief
        assert "[5] 第5条" in brief
        assert "[6] 第6条" not in brief

    def test_data_honesty_rule_in_base_prompt(self, plug):
        """「没附上的数据没读过、别索要粘贴」指令进基础提示词（无条件）"""
        prompt = plug.build_system_prompt(None)
        assert "只有随消息附上的数据块才是你能看到的真实数据" in prompt
        assert "不要向用户索要粘贴" in prompt

    def test_generation_directive_only_with_manage(self, plug):
        """生成类任务指令与动作协议绑定：can_manage=False 不出现（无 add_*）"""
        assert "add_* 动作批量产出" in plug.build_system_prompt(None,
                                                              can_manage=True)
        assert "add_* 动作批量产出" not in plug.build_system_prompt(None)

    def test_custom_rules_unchanged_with_kb_brief(self, plug):
        """规则库兼容不回归：规则仍追加在提示词末尾（含 kb_brief 时）"""
        rules = [{"text": "回答保持简洁", "enabled": True},
                 {"text": "停用的规则", "enabled": False}]
        base = plug.build_system_prompt(rules)
        with_brief = plug.build_system_prompt(rules, kb_brief="知识库概况：x")
        assert "回答保持简洁" in base and with_brief.endswith("回答保持简洁")
        assert "停用的规则" not in with_brief
        # kb_brief 只插入在规则之前，规则段位置不漂移
        assert with_brief.index("知识库概况") < with_brief.index(
            "以下是用户在「规则库」里自定义的规则")

    def test_manage_grants_keep_all_sections_ordered(self, plug):
        prompt = plug.build_system_prompt([{"text": "规则甲", "enabled": True}],
                                          can_manage=True,
                                          kb_brief="知识库概况：x")
        i_proto = prompt.index("== 你可以执行的应用操作 ==")
        i_brief = prompt.index("知识库概况")
        i_rules = prompt.index("以下是用户在「规则库」里自定义的规则")
        assert i_proto < i_brief < i_rules


# ---------------- D. 版本钉子 ----------------
class TestVersionPin:
    def test_plugin_version_is_1_16_0(self, plug):
        assert plug.AiAssistantPlugin.version == NEW_VERSION

    def test_manifest_version_matches_plugin(self, plug):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["version"] == NEW_VERSION
        assert manifest["version"] == plug.AiAssistantPlugin.version
