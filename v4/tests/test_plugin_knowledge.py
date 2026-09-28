# -*- coding: utf-8 -*-
"""知识库（知识库.docx）操作能力回归 —— 只读快照 / 只增 / 改删 + 撤销（2026-09-28）。

背景：用户要求「增加 AI 对知识库的操作能力」。知识库和任务/碎片/笔记有一处
**本质差别**，本文件的护栏主要就是围着它转：

  - 任务/碎片/笔记用**自增 id** 寻址，id 永不复用；
  - 知识库段落用**编号（位置）**寻址 —— 删掉第 3 段，原第 5 段就变成第 4 段；
    而 docx 还允许用户直接用 Word/WPS 打开编辑。

即「编号 N」随时可能变成过期引用。因此改删**强制校验内容指纹
``expect_hash``**：对不上就拒绝，宁可失败也不改错段落。

钉死的行为：
  A 只读快照：providers 有 knowledge → 拿到 list；缺失 → 空 list（不抛）
  B 权限门禁：未声明能力 → add_knowledge 返回 0，update / delete 返回
    False / 0，provider 零调用
  C 只声明 write：add_knowledge 可用；update_knowledge / delete_knowledge
    仍被拒（写权限不顺带升级为改删权限）
  D **expect_hash 必填**：None / 空串 / 非字符串 → 拒，provider 零调用
  E 参数护栏：num 必须正整数（拒 0 / 负 / bool / 字符串）；content 必须字符串
  F 返回语义：add 返回编号（只认正整数）；delete 返回撤销令牌（只认真整数）；
    update 只认真 bool
  G provider 抛异常 → 隔离为 0 / False，绝不冒泡
  H 通道自省：manage_sources() 含知识库两个方法
  I DocxManager 索引口径：文档含空行/短段时 ParagraphInfo.index 仍是
    **过滤后位置**（与面板「编号」一致），get_paragraph_text(p.index) 取到的
    就是该段自己的文本
  J DocxManager 原位插入：insert_paragraph_before / after 插到正确位置并
    重建后续索引；append / update / delete 落盘后 reload 一致
本文件不 import PyQt6；docx 用 python-docx 现场造临时文件。
"""

import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from docx import Document                          # noqa: E402

from src.docx_manager import DocxManager           # noqa: E402
from src.plugin_api import PluginContext, PluginData  # noqa: E402


class _Sink:
    """记录调用的假 provider"""

    def __init__(self, ret=True):
        self.calls = []
        self.ret = ret

    def __call__(self, *args):
        self.calls.append(args)
        return self.ret


class _Boom:
    def __call__(self, *args):
        raise RuntimeError("provider 炸了")


class _Log:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)


def _knowledge_manage(ret=True, token=7):
    return {"update_knowledge": _Sink(ret), "delete_knowledge": _Sink(token)}


def _ctx(caps=(), manage=None, logger=None, plugin_id="demo"):
    return PluginContext(
        logger=logger, plugin_id=plugin_id, capabilities=caps,
        write_providers={"knowledge": _Sink(3)},
        manage_providers=(manage if manage is not None
                          else _knowledge_manage()),
    ).for_plugin(plugin_id, "/x", caps)


def _writer(caps=(), manage=None, logger=None):
    """直接建 PluginContext 拿门面（不必经过派生，权限即 caps）"""
    return PluginContext(
        logger=logger, plugin_id="demo", capabilities=caps,
        write_providers={"knowledge": _Sink(3)},
        manage_providers=(manage if manage is not None
                          else _knowledge_manage()),
    ).write


KB_OK = "abc123hash"


# ---------------- A. 只读快照 ----------------
class TestReadSnapshot:
    def test_returns_provider_data(self):
        rows = [{"num": 1, "text": "第一段", "preview": "第一段",
                 "hash": KB_OK}]
        data = PluginData(providers={"knowledge": lambda: rows})
        got = data.knowledge()
        assert got == rows

    def test_missing_provider_yields_empty_list(self):
        assert PluginData(providers={}).knowledge() == []

    def test_provider_exception_is_isolated(self):
        data = PluginData(providers={"knowledge": _Boom()})
        assert data.knowledge() == []

    def test_returned_copy_is_deep(self):
        rows = [{"num": 1, "text": "原文", "hash": KB_OK}]
        data = PluginData(providers={"knowledge": lambda: rows})
        got = data.knowledge()
        got[0]["text"] = "被改了"
        assert rows[0]["text"] == "原文"

    def test_source_name_registered(self):
        assert "knowledge" in PluginData(
            providers={"knowledge": lambda: []}).sources()


# ---------------- B/C. 权限门禁 ----------------
class TestCapabilityGate:
    def test_no_capability_denies_everything(self):
        w = _writer(caps=(), manage=_knowledge_manage())
        assert w.add_knowledge("内容够长了吧") == 0
        assert w.update_knowledge(1, "新内容够长", KB_OK) is False
        assert w.delete_knowledge(1, KB_OK) == 0

    def test_no_capability_means_zero_provider_calls(self):
        mg = _knowledge_manage()
        w = _writer(caps=(), manage=mg)
        w.delete_knowledge(1, KB_OK)
        w.update_knowledge(1, "新内容够长", KB_OK)
        assert mg["delete_knowledge"].calls == []
        assert mg["update_knowledge"].calls == []

    def test_write_only_allows_add_not_update_delete(self):
        mg = _knowledge_manage()
        w = _writer(caps=["write"], manage=mg)
        assert w.add_knowledge("追加一段够长的内容") == 3
        assert w.update_knowledge(1, "改写内容够长", KB_OK) is False
        assert w.delete_knowledge(1, KB_OK) == 0
        assert mg["delete_knowledge"].calls == []

    def test_manage_implies_write(self):
        w = _writer(caps=["manage"])
        assert w.add_knowledge("追加一段够长的内容") == 3
        assert w.can_manage() is True

    def test_manifest_capability_name_is_known(self):
        from src.plugin_api import KNOWN_CAPABILITIES
        assert "manage" in KNOWN_CAPABILITIES


# ---------------- D. expect_hash 必填 ----------------
class TestHashRequired:
    def test_update_refuses_without_hash(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        for bad in (None, "", "   ", 123, 1.5, [], {}):
            assert w.update_knowledge(1, "改写内容够长", bad) is False
        assert mg["update_knowledge"].calls == []

    def test_delete_refuses_without_hash(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        for bad in (None, "", "   ", 9, [KB_OK]):
            assert w.delete_knowledge(1, bad) == 0
        assert mg["delete_knowledge"].calls == []

    def test_hash_is_passed_through_verbatim(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        assert w.delete_knowledge(4, KB_OK) == 7
        assert mg["delete_knowledge"].calls == [(4, KB_OK)]

    def test_update_passes_all_three_args(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        assert w.update_knowledge(4, "改写后的正文", KB_OK) is True
        assert mg["update_knowledge"].calls == [(4, "改写后的正文", KB_OK)]


# ---------------- E. 参数护栏 ----------------
class TestArgGuards:
    def test_num_must_be_positive_int(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        for bad in (0, -1, True, False, "3", 3.0, None):
            assert w.delete_knowledge(bad, KB_OK) == 0
            assert w.update_knowledge(bad, "内容够长啦", KB_OK) is False
        assert mg["delete_knowledge"].calls == []

    def test_content_must_be_string(self):
        mg = _knowledge_manage()
        w = _writer(caps=["manage"], manage=mg)
        for bad in (None, 123, [1], {}):
            assert w.update_knowledge(1, bad, KB_OK) is False
        assert mg["update_knowledge"].calls == []

    def test_add_content_guards(self):
        w = _writer(caps=["write"])
        assert w.add_knowledge("") == 0
        assert w.add_knowledge("   ") == 0
        assert w.add_knowledge(None) == 0
        assert w.add_knowledge(123) == 0

    def test_add_truncates_overlong_content(self):
        sink = _Sink(1)
        w = PluginContext(
            logger=None, plugin_id="demo", capabilities=["write"],
            write_providers={"knowledge": sink},
        ).write
        w.add_knowledge("字" * (w.MAX_CONTENT_LEN + 100))
        assert len(sink.calls[0][0]) == w.MAX_CONTENT_LEN


# ---------------- F. 返回语义 ----------------
class TestReturnSemantics:
    def test_add_only_accepts_positive_int(self):
        for ret, expect in ((9, 9), (0, 0), (-2, 0), (True, 0), (2.5, 0),
                            ("9", 0), (None, 0)):
            w = PluginContext(
                logger=None, plugin_id="demo", capabilities=["write"],
                write_providers={"knowledge": _Sink(ret)},
            ).write
            assert w.add_knowledge("内容够长的啦") == expect

    def test_delete_token_must_be_real_int(self):
        for ret, expect in ((5, 5), (0, 0), (-1, 0), (True, 0), (1.5, 0),
                            ("5", 0)):
            w = _writer(caps=["manage"],
                        manage={"delete_knowledge": _Sink(ret)})
            assert w.delete_knowledge(1, KB_OK) == expect

    def test_update_only_accepts_real_bool(self):
        for ret, expect in ((True, True), (False, False), (1, False),
                            ("ok", False), (None, False)):
            w = _writer(caps=["manage"],
                        manage={"update_knowledge": _Sink(ret)})
            assert w.update_knowledge(1, "内容够长的", KB_OK) is expect


# ---------------- G. 异常隔离 ----------------
class TestExceptionIsolation:
    def test_provider_exception_is_isolated(self):
        w = _writer(caps=["manage"],
                    manage={"delete_knowledge": _Boom(),
                            "update_knowledge": _Boom()})
        assert w.delete_knowledge(1, KB_OK) == 0
        assert w.update_knowledge(1, "内容够长的", KB_OK) is False

    def test_add_provider_exception_is_isolated(self):
        w = PluginContext(
            logger=None, plugin_id="demo", capabilities=["write"],
            write_providers={"knowledge": _Boom()},
        ).write
        assert w.add_knowledge("内容够长的啦") == 0

    def test_missing_channel_is_rejected(self):
        w = PluginContext(
            logger=None, plugin_id="demo", capabilities=["manage"],
            write_providers={}, manage_providers={},
        ).write
        assert w.add_knowledge("内容够长的啦") == 0
        assert w.delete_knowledge(1, KB_OK) == 0


# ---------------- H. 通道自省 ----------------
class TestSources:
    def test_manage_sources_lists_knowledge_ops(self):
        w = _writer(caps=["manage"])
        assert set(w.manage_sources()) >= {"update_knowledge",
                                          "delete_knowledge"}

    def test_write_sources_lists_knowledge(self):
        assert "knowledge" in _writer(caps=["write"]).sources()


# ---------------- I / J. DocxManager 索引与写入 ----------------
def _make_docx(path, texts):
    doc = Document()
    for t in texts:
        doc.add_paragraph(t)
    doc.save(path)


class TestDocxIndex:
    """文档含空行 / 过短段落时，index 必须是过滤后位置"""

    def _mgr(self, tmp_path):
        p = os.path.join(tmp_path, "知识库.docx")
        # 第 1 段空、第 2 段过短（<4）、第 3/4 段有效
        _make_docx(p, ["", "短", "第一条有效知识", "第二条有效知识"])
        m = DocxManager(p, os.path.join(tmp_path, "docx_meta.json"))
        paras, err = m.load()
        assert err is None
        return m, paras

    def test_short_and_empty_paragraphs_are_filtered(self, tmp_path):
        _, paras = self._mgr(str(tmp_path))
        assert [p.text for p in paras] == ["第一条有效知识", "第二条有效知识"]

    def test_index_is_filtered_position_not_raw(self, tmp_path):
        _, paras = self._mgr(str(tmp_path))
        assert [p.index for p in paras] == [0, 1]

    def test_index_addresses_its_own_text(self, tmp_path):
        m, paras = self._mgr(str(tmp_path))
        for p in paras:
            assert m.get_paragraph_text(p.index) == p.text

    def test_delete_uses_filtered_position(self, tmp_path):
        m, paras = self._mgr(str(tmp_path))
        assert m.delete_paragraph(paras[0].index) is True
        assert m.save() is True
        assert [p.text for p in m.get_paragraphs()] == ["第二条有效知识"]

    def test_update_uses_filtered_position(self, tmp_path):
        m, paras = self._mgr(str(tmp_path))
        assert m.update_paragraph_text(paras[1].index, "第二条改过了") is True
        assert m.save() is True
        m.reload()
        assert m.get_paragraph_text(1) == "第二条改过了"


class TestDocxInsert:
    def _mgr(self, tmp_path):
        p = os.path.join(tmp_path, "知识库.docx")
        _make_docx(p, ["甲段内容", "乙段内容", "丙段内容"])
        m = DocxManager(p, os.path.join(tmp_path, "docx_meta.json"))
        m.load()
        return m

    def test_insert_after_position(self, tmp_path):
        m = self._mgr(str(tmp_path))
        idx = m.insert_paragraph_after(0, "甲后插入")
        assert idx == 1
        assert [p.text for p in m.get_paragraphs()] == [
            "甲段内容", "甲后插入", "乙段内容", "丙段内容"]
        assert [p.index for p in m.get_paragraphs()] == [0, 1, 2, 3]

    def test_insert_before_position(self, tmp_path):
        m = self._mgr(str(tmp_path))
        idx = m.insert_paragraph_before(1, "乙前插入")
        assert idx == 1
        assert [p.text for p in m.get_paragraphs()] == [
            "甲段内容", "乙前插入", "乙段内容", "丙段内容"]
        assert [p.index for p in m.get_paragraphs()] == [0, 1, 2, 3]

    def test_insert_before_first(self, tmp_path):
        m = self._mgr(str(tmp_path))
        assert m.insert_paragraph_before(0, "新的首段") == 0
        assert [p.text for p in m.get_paragraphs()][0] == "新的首段"
        assert m.save() is True
        m.reload()
        assert [p.text for p in m.get_paragraphs()][0] == "新的首段"

    def test_insert_rejects_too_short_text(self, tmp_path):
        m = self._mgr(str(tmp_path))
        assert m.insert_paragraph_before(0, "短") == -1
        assert m.insert_paragraph_after(0, "") == -1

    def test_insert_rejects_out_of_range(self, tmp_path):
        m = self._mgr(str(tmp_path))
        assert m.insert_paragraph_before(99, "合法内容啊") == -1
        assert m.insert_paragraph_after(-1, "合法内容啊") == -1

    def test_append_then_reload_roundtrip(self, tmp_path):
        m = self._mgr(str(tmp_path))
        assert m.append_paragraph("丁段新内容") == 3
        assert m.save() is True
        m.reload()
        assert [p.text for p in m.get_paragraphs()] == [
            "甲段内容", "乙段内容", "丙段内容", "丁段新内容"]

    def test_delete_then_restore_at_position(self, tmp_path):
        """删除后按原位恢复：这才是「撤销」而不是「挪到末尾」"""
        m = self._mgr(str(tmp_path))
        assert m.delete_paragraph(1) is True          # 删掉「乙段内容」
        assert m.insert_paragraph_before(1, "乙段内容") == 1
        assert m.save() is True
        m.reload()
        assert [p.text for p in m.get_paragraphs()] == [
            "甲段内容", "乙段内容", "丙段内容"]

    def test_delete_last_then_append_restores_end(self, tmp_path):
        m = self._mgr(str(tmp_path))
        pos = 2
        assert m.delete_paragraph(pos) is True
        items = m.get_paragraphs()
        assert pos == len(items)                     # 删的是最后一段
        assert m.append_paragraph("丙段内容") == len(items)
        assert [p.text for p in m.get_paragraphs()][-1] == "丙段内容"


class TestHashContract:
    """指纹契约：同文本同哈希、改文本换哈希（宿主靠它判断引用是否过期）"""

    def test_hash_is_stable_and_content_bound(self, tmp_path):
        p = os.path.join(tmp_path, "知识库.docx")
        _make_docx(p, ["知识条目一", "知识条目二"])
        m = DocxManager(p, os.path.join(tmp_path, "docx_meta.json"))
        m.load()
        h1 = m.get_paragraphs()[0].hash
        assert h1 == m.get_paragraphs()[0].hash        # 稳定
        assert h1 != m.get_paragraphs()[1].hash        # 区隔内容
        m.update_paragraph_text(0, "知识条目一改过")
        assert m.get_paragraphs()[0].hash != h1        # 改文换哈希

    def test_hash_ignores_surrounding_whitespace(self, tmp_path):
        p = os.path.join(tmp_path, "知识库.docx")
        _make_docx(p, ["  知识条目  "])
        m = DocxManager(p, os.path.join(tmp_path, "docx_meta.json"))
        m.load()
        assert m.get_paragraphs()[0].hash == DocxManager._hash_text("知识条目")


def test_tmpdir_helper_is_usable():
    """哨兵：确认临时目录可写（失败时其它 docx 用例会给误导性错误）"""
    with tempfile.TemporaryDirectory() as d:
        assert os.path.isdir(d)
