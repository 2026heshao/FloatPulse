# -*- coding: utf-8 -*-
"""站内检索核心（plugins/kb-search/kb_search_core.py）回归 —— 纯逻辑，无 GUI。

为什么值得钉这么细：这套检索是**从零手写的**（依赖白名单只有 PyQt6 + 标准库，
jieba / whoosh 装进来也用不了），没有成熟库替它兜底。而它错起来是「静默错」——
不是崩，而是「搜不到」「搜到了但排在后面」，用户只会觉得「这功能没用」。
所以本文件把三类容易悄悄坏掉的东西钉死：

  A 分词与归一化：全角→半角、英文小写、中文双向最大匹配择优、
    停用字只删「整项都是功能字」的项
  B **索引侧与查询侧同构**——这是自研检索最致命的坑：两边切法不一致时，
    查询项在索引里根本不存在，表现为「明明有这段文字却搜不到」
  C BM25 行为：IDF 恒非负（经典式在 df>N/2 时会变负）、命中越多分越高、
    长文档归一化、同分排序稳定（否则每次查询顺序会跳）
  D 命中片段与高亮区间：span 下标相对片段、重叠区间合并、按区间切分后
    拼回原文必须与原文完全相等（拼错就会显示错位的文本）
"""

import importlib.util
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

CORE_PATH = os.path.join(ROOT, "plugins", "kb-search", "kb_search_core.py")
PLUGIN_PATH = os.path.join(ROOT, "plugins", "kb-search", "plugin.py")
MANIFEST_PATH = os.path.join(ROOT, "plugins", "kb-search", "manifest.json")
_MOD = "fp_test_kb_search_core"


@pytest.fixture(scope="module")
def kb():
    """按插件加载器的方式直载核心模块（纯逻辑，不需要 QApplication）"""
    spec = importlib.util.spec_from_file_location(_MOD, CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


def _index(kb, docs):
    idx = kb.SearchIndex()
    for uid, text in docs:
        idx.add(uid, text, kind=uid.split(":")[0], title=uid)
    idx.finalize()
    return idx


# ====================================================================
# A 归一化与分词
# ====================================================================
class TestNormalize:
    def test_fullwidth_to_halfwidth(self, kb):
        """全角数字/字母必须归一：从 Word / 微信粘贴进来的文本大量是全角，
        不归一化就永远搜不到（中文文本处理最容易漏的一步）"""
        assert kb.Tokenizer.normalize("２０２６ＡＢ") == "2026ab"
        assert kb.Tokenizer.normalize("ａ：１") == "a:1"

    def test_english_lowercased(self, kb):
        assert kb.Tokenizer.normalize("BM25 Index") == "bm25 index"

    def test_ideographic_space(self, kb):
        assert kb.Tokenizer.normalize("月报　归档") == "月报 归档"

    def test_dirty_input(self, kb):
        assert kb.Tokenizer.normalize(None) == ""
        assert kb.Tokenizer.normalize(123) == "123"


class TestTokenizer:
    def test_longest_match_wins(self, kb):
        """词典词优先切成长词，而不是拆成两个单字"""
        assert "周报" in kb.tokenize("写周报")
        assert "知识库" in kb.tokenize("更新知识库")

    def test_ascii_token_kept_whole(self, kb):
        """英文/数字/版本号/路径整体成项，不被标点切碎"""
        terms = kb.tokenize("跑 BM25 与 bm25-index v1.2 对比")
        assert "bm25" in terms
        assert "bm25-index" in terms
        assert "v1.2" in terms

    def test_numbers_kept(self, kb):
        assert "2026" in kb.tokenize("2026 年 9 月")

    def test_bigram_covers_out_of_vocab_words(self, kb):
        """词典外的自造词靠字符 bigram 召回（专有名词必然不在词典里）"""
        terms = kb.tokenize("太原家政")
        assert "太原" in terms and "家政" in terms
        assert "原家" in terms                       # 跨词 bigram 也在，用于对齐

    def test_two_char_word_not_double_counted(self, kb):
        """2 字词的 bigram 就是它自己——重复计数会凭空翻倍 tf"""
        terms = kb.tokenize("周报")
        assert terms.count("周报") == 1

    def test_stop_chars_dropped_only_as_whole_term(self, kb):
        """「的」这种整项功能字丢掉；但「报」必须留着（否则「月报」的
        bigram 一起被毁）——判据是整项都无意义才丢"""
        terms = kb.tokenize("我的报表")
        assert "的" not in terms
        assert "报" in terms or "报表" in terms or "月报" in terms

    def test_english_stopwords_dropped(self, kb):
        assert "the" not in kb.tokenize("the report")

    def test_punctuation_skipped(self, kb):
        terms = kb.tokenize("归档、整理；完成。")
        assert all(t.strip() for t in terms)
        assert "、" not in terms and "；" not in terms

    def test_empty_and_whitespace(self, kb):
        assert kb.tokenize("") == []
        assert kb.tokenize("   \n\t ") == []
        assert kb.tokenize(None) == []

    def test_custom_words_accepted(self, kb):
        tk = kb.Tokenizer(user_words=["码上工坊"])
        assert "码上工坊" in tk.terms("加入码上工坊")


# ====================================================================
# B 索引侧与查询侧同构（最致命的坑）
# ====================================================================
class TestIndexQuerySymmetry:
    @pytest.mark.parametrize("query,doc", [
        ("周报", "每周五写周报并归档"),
        ("知识库", "知识库第 3 段讲报价流程"),
        ("太原家政", "太原家政公司的报价模板"),
        ("bm25", "用 BM25 做相关性排序"),
        ("2026", "2026 年 9 月的月报"),
        ("报价单", "客户报价单模板"),
    ])
    def test_query_terms_exist_in_document_terms(self, kb, query, doc):
        """查询项必须能在文档的检索项里找到——否则「明明有这段文字却搜不到」

        这是自研检索最典型、也最难察觉的失败模式，所以逐例对照两侧的输出。
        """
        doc_terms = set(kb.tokenize(doc))
        q_terms = kb.tokenize(query)
        assert q_terms, f"{query!r} 切不出任何检索项"
        assert set(q_terms) & doc_terms, \
            f"{query!r} -> {q_terms} 与 {doc_terms} 无交集"

    def test_out_of_vocab_phrase_is_findable(self, kb):
        """词典里没有的整句也要能搜到（靠 bigram 对齐）"""
        idx = _index(kb, [("note:1", "把项目代号 星尘三号 写进周报")])
        assert idx.search("星尘三号")
        assert idx.search("星尘")


# ====================================================================
# C BM25 打分
# ====================================================================
class TestBM25:
    def test_idf_never_negative(self, kb):
        """经典 IDF 在 df > N/2 时为负 → 常见词反而扣分；这里必须恒 ≥ 0"""
        idx = _index(kb, [(f"note:{i}", "归档") for i in range(4)])
        assert idx._idf("归档") >= 0.0
        assert idx._idf("归档") == pytest.approx(0.0, abs=0.2)

    def test_rare_term_scores_higher_than_common(self, kb):
        """稀有词区分度高于常见词（IDF 的直接效果）"""
        docs = [("note:1", "归档 归档 归档 稀有词"),
                ("note:2", "归档"),
                ("note:3", "归档"),
                ("note:4", "归档"),
                ("note:5", "归档")]
        idx = _index(kb, docs)
        by_uid = {h.uid: h.score for h in idx.search("归档 稀有词")}
        assert by_uid["note:1"] > by_uid["note:2"]

    def test_more_matches_rank_higher(self, kb):
        idx = _index(kb, [
            ("note:1", "月报 归档 复盘"),
            ("note:2", "月报"),
            ("note:3", "复盘"),
        ])
        hits = idx.search("月报 归档 复盘")
        assert hits[0].uid == "note:1"

    def test_length_normalization(self, kb):
        """同样命中一次，短文档应该排在长文档前面（b 参数的作用）"""
        idx = _index(kb, [
            ("note:short", "倒排索引"),
            ("note:long", "倒排索引" + "".join(
                f"无关内容第{i}段，讲的是完全不同的东西。" for i in range(30))),
        ])
        hits = idx.search("倒排索引")
        assert hits[0].uid == "note:short"

    def test_tie_break_is_stable(self, kb):
        """同分顺序必须稳定：否则每次输入都会看到结果跳来跳去"""
        docs = [(f"note:{i}", "同一句话") for i in range(5)]
        idx = _index(kb, docs)
        first = [h.uid for h in idx.search("同一句话")]
        for _ in range(3):
            assert [h.uid for h in idx.search("同一句话")] == first

    def test_empty_query_returns_nothing(self, kb):
        idx = _index(kb, [("note:1", "任意内容")])
        assert idx.search("") == []
        assert idx.search("   ") == []
        assert idx.search(None) == []

    def test_no_match_returns_nothing(self, kb):
        idx = _index(kb, [("note:1", "月报归档")])
        assert idx.search("完全不相干的词") == []

    def test_search_on_empty_index(self, kb):
        idx = kb.SearchIndex()
        idx.finalize()
        assert idx.search("任意") == []
        assert idx.size == 0

    def test_top_n_limits(self, kb):
        idx = _index(kb, [(f"note:{i}", "共同词") for i in range(10)])
        assert len(idx.search("共同词", top_n=3)) == 3
        assert len(idx.search("共同词", top_n=0)) == 0

    def test_kind_filter(self, kb):
        idx = _index(kb, [("note:1", "报价流程"), ("task:2", "报价流程"),
                          ("knowledge:3", "报价流程")])
        kinds = {h.kind for h in idx.search("报价", kind="note")}
        assert kinds == {"note"}

    def test_scores_are_finite_and_sorted(self, kb):
        idx = _index(kb, [(f"note:{i}", f"归档 第{i}份") for i in range(6)])
        hits = idx.search("归档")
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)
        assert all(s > 0 for s in scores)


class TestIndexMutation:
    def test_add_remove_size(self, kb):
        # ⚠ 三个文档的词必须**互不重叠**：早期版本用「内容0/1/2」，它们共享
        # 「内容」，删掉 note:1 后搜「内容1」照样命中 note:0 → 断言假失败
        idx = kb.SearchIndex()
        idx.add("note:0", "苹果")
        idx.add("note:1", "香蕉")
        idx.add("note:2", "葡萄")
        assert idx.size == 3
        assert idx.remove("note:1") is True
        assert idx.size == 2
        assert idx.remove("note:1") is False        # 再删返回 False，不抛
        assert idx.search("香蕉") == []
        assert [h.uid for h in idx.search("葡萄")] == ["note:2"]

    def test_duplicate_uid_overwrites(self, kb):
        idx = kb.SearchIndex()
        idx.add("note:1", "苹果")
        idx.add("note:1", "香蕉")
        assert idx.size == 1
        assert idx.search("苹果") == []
        assert [h.uid for h in idx.search("香蕉")] == ["note:1"]

    def test_clear(self, kb):
        idx = _index(kb, [("note:1", "内容")])
        idx.clear()
        assert idx.size == 0 and idx.term_count == 0
        assert idx.search("内容") == []

    def test_rebuild_via_helper(self, kb):
        """build_index 复用同一个 SearchIndex 对象时必须先清空，
        否则旧文档留在索引里（表现为「删掉的内容还能搜到」）"""
        specs = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        specs.build_index([("note:1", "note", "标题", "苹果内容")], idx)
        specs.build_index([("note:2", "note", "标题", "香蕉内容")], idx)
        assert idx.size == 1
        assert idx.search("苹果") == []
        assert [h.uid for h in idx.search("香蕉")] == ["note:2"]


def _load_plugin_module(kb):
    """plugin.py 里有依赖 PyQt6 的类，但 collect_documents / build_index /
    render_results_html 是纯函数——只取这三个，不构造任何控件。"""
    spec = importlib.util.spec_from_file_location(
        "fp_test_kb_search_plugin_pure", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ====================================================================
# D 命中片段与高亮区间
# ====================================================================
class TestSnippet:
    def test_spans_point_at_the_hit(self, kb):
        text = "每周五写周报并归档本月资料"
        snippet, spans, matched = kb.make_snippet(text, ["周报"])
        assert snippet == text                      # 短文本原样返回
        assert spans == [(4, 6)]
        assert snippet[4:6] == "周报"
        assert matched == ["周报"]

    def test_multiple_terms(self, kb):
        text = "每周五写周报并归档本月资料"
        snippet, spans, matched = kb.make_snippet(text, ["周报", "归档"])
        for start, end in spans:
            assert snippet[start:end] in ("周报", "归档")
        assert matched == ["周报", "归档"]

    def test_long_text_windowed(self, kb):
        text = "前" * 200 + "命中关键词" + "后" * 200
        snippet, spans, _matched = kb.make_snippet(text, ["命中"])
        assert len(snippet) < len(text)
        for start, end in spans:
            assert snippet[start:end] == "命中"

    def test_no_hit_returns_head(self, kb):
        text = "完全不相干的内容"
        snippet, spans, matched = kb.make_snippet(text, ["找不到"])
        assert spans == [] and matched == []
        assert snippet and text.startswith(snippet)

    def test_empty_text(self, kb):
        assert kb.make_snippet("", ["词"]) == ("", [], [])
        assert kb.make_snippet(None, ["词"]) == ("", [], [])

    def test_overlapping_spans_merged(self, kb):
        """「月报」与「报」同时命中时区间重叠，必须合并——否则切出空段"""
        merged = kb._merge_spans([(4, 6), (5, 7), (10, 12), (12, 14)])
        assert merged == [(4, 7), (10, 14)]

    def test_split_reassembles_original(self, kb):
        """按区间切分后拼回去必须与原文**完全相等**（拼错就显示错位的文本）"""
        text = "每周五写周报并归档本月资料"
        _sn, spans, _mt = kb.make_snippet(text, ["周报", "归档"])
        parts = kb.split_by_spans(text, spans)
        assert "".join(p for p, _flag in parts) == text
        flagged = [p for p, flag in parts if flag]
        assert flagged == ["周报", "归档"]

    def test_split_without_spans(self, kb):
        assert kb.split_by_spans("原文", []) == [("原文", False)]
        assert kb.split_by_spans("", []) == []

    def test_split_clamps_out_of_range(self, kb):
        """区间越界要夹紧而不是抛 IndexError（数据被外部改过时会出现）"""
        parts = kb.split_by_spans("abc", [(0, 99)])
        assert "".join(p for p, _f in parts) == "abc"


# ====================================================================
# E 采集与渲染（plugin.py 的纯函数部分）
# ====================================================================
class _FakeData:
    """宿主只读快照替身（字段名照真实 to_dict 抄）"""

    def __init__(self, **over):
        self._d = {
            "knowledge": [{"num": 1, "text": "报价流程见附件 A",
                           "preview": "报价流程", "hash": "aa"},
                          {"num": 2, "text": "", "preview": "", "hash": "bb"}],
            "notes": [{"note_id": 1, "title": "周会记录", "content": "讨论月报"},
                      {"note_id": 2, "title": "", "content": ""}],
            "fragments": [{"fragment_id": 1, "content": "https://example.com",
                           "source": "剪贴板"}],
            "tasks": [{"task_id": 1, "title": "写周报", "note": "带上数据",
                       "deadline": "2026-09-30", "done": False},
                      {"task_id": 2, "title": "", "note": "", "deadline": ""}],
            "assets": [{"asset_id": 1, "original_name": "客户报价单.xlsx",
                        "is_image": False, "size_bytes": 2048,
                        "added_time": "2026-09-28 10:00:00"},
                       {"asset_id": 2, "original_name": "", "is_image": True,
                        "size_bytes": 10, "added_time": "2026-09-28 10:00:00"}],
        }
        self._d.update(over)

    def knowledge(self):
        return self._d["knowledge"]

    def notes(self):
        return self._d["notes"]

    def fragments(self):
        return self._d["fragments"]

    def tasks(self):
        return self._d["tasks"]

    def assets(self):
        return self._d["assets"]


class TestCollectDocuments:
    def test_collects_all_sources(self, kb):
        """五个数据源都要进索引（含并入前由宿主全库搜索覆盖的「素材」）"""
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData())
        kinds = {kind for _uid, kind, _t, _x in docs}
        assert kinds == {"knowledge", "note", "fragment", "task", "asset"}

    def test_knowledge_uid_uses_paragraph_number(self, kb):
        """知识库是位置型数据源，uid 必须带段落编号（与面板口径一致）"""
        mod = _load_plugin_module(kb)
        uids = [uid for uid, kind, _t, _x in
                mod.collect_documents(_FakeData()) if kind == "knowledge"]
        assert uids == ["knowledge:1"]              # 第 2 段正文为空 → 跳过

    def test_blank_entries_skipped(self, kb):
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData())
        assert not any(not text.strip() for _u, _k, _t, text in docs)
        # 无标题且无正文的笔记 / 无标题的任务 / 无文件名的素材都不进索引
        assert not any(uid == "note:2" for uid, _k, _t, _x in docs)
        assert not any(uid == "task:2" for uid, _k, _t, _x in docs)
        assert not any(uid == "asset:2" for uid, _k, _t, _x in docs)

    def test_task_title_searchable(self, kb):
        """任务标题要能被搜到（正文里拼了 title）"""
        mod = _load_plugin_module(kb)
        docs = dict((uid, text) for uid, _k, _t, text in
                    mod.collect_documents(_FakeData()))
        assert "写周报" in docs["task:1"]

    def test_deadline_shown_in_title(self, kb):
        mod = _load_plugin_module(kb)
        titles = {uid: title for uid, _k, title, _x in
                  mod.collect_documents(_FakeData())}
        assert "2026-09-30" in titles["task:1"]

    def test_asset_indexed_by_filename(self, kb):
        """素材只给元数据（没有文件路径），所以按**文件名**索引——
        与并入前宿主全库搜索的口径一致：用户找的是「那个 Excel」"""
        mod = _load_plugin_module(kb)
        docs = dict((uid, text) for uid, _k, _t, text in
                    mod.collect_documents(_FakeData()))
        assert docs["asset:1"] == "客户报价单.xlsx"

    def test_asset_not_leaking_path(self, kb):
        """即便宿主误把 stored_path 塞进快照，插件也不该索引文件路径"""
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData(
            assets=[{"asset_id": 3, "original_name": "报表.xlsx",
                     "stored_path": r"C:\Users\me\Desktop\报表.xlsx",
                     "is_image": False, "size_bytes": 1, "added_time": ""}]))
        text = dict((uid, x) for uid, _k, _t, x in docs)["asset:3"]
        assert "Users" not in text and "Desktop" not in text

    def test_dirty_entries_do_not_break_collection(self, kb):
        """一条坏数据只跳过自己（宿主结构变化 / None 混进来时不能整轮失败）"""
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData(
            notes=[None, "不是 dict", {"note_id": 9, "title": "好的",
                                       "content": "正文"}]))
        assert [uid for uid, _k, _t, _x in docs if uid.startswith("note:")] \
            == ["note:9"]

    def test_dirty_asset_entries_skipped(self, kb):
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData(
            assets=[None, 42, {"asset_id": 7, "original_name": "好文件.pdf"}]))
        assert [uid for uid, _k, _t, _x in docs if uid.startswith("asset:")] \
            == ["asset:7"]

    def test_empty_sources(self, kb):
        mod = _load_plugin_module(kb)
        docs = mod.collect_documents(_FakeData(
            knowledge=[], notes=[], fragments=[], tasks=[], assets=[]))
        assert docs == []

    def test_long_text_truncated(self, kb):
        mod = _load_plugin_module(kb)
        # 只喂一个数据源：否则 docs[0] 会是知识库那条，长度断言对不上
        docs = mod.collect_documents(_FakeData(
            knowledge=[], fragments=[], tasks=[], assets=[],
            notes=[{"note_id": 5, "title": "长文", "content": "字" * 9000}]))
        assert len(docs) == 1
        assert len(docs[0][3]) == mod.PREVIEW_CHARS


class TestRenderResults:
    def test_empty_hits(self, kb):
        mod = _load_plugin_module(kb)
        assert mod.render_results_html([], "查询") == ""

    def test_escapes_user_html(self, kb):
        """命中片段是用户原文，必然含 < & （贴代码是常态）；
        不转义会被 QTextBrowser 当标签吃掉"""
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        idx.add("note:1", "代码 <script>alert(1)</script> 片段", kind="note",
                title="笔记")
        idx.finalize()
        out = mod.render_results_html(idx.search("script"), "script")
        # ⚠ 不能断言 "&lt;script&gt;" 连续出现：命中词 "script" 会被
        # <span class="hit"> 包住，把转义后的尖括号切开
        assert "&lt;" in out and "&gt;" in out
        assert "<script" not in out

    def test_highlight_spans_wrapped(self, kb):
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        idx.add("note:1", "每周五写周报", kind="note", title="笔记")
        idx.finalize()
        out = mod.render_results_html(idx.search("周报"), "周报")
        assert '<span class="hit">周报</span>' in out
        assert "[笔记]" in out

    def test_title_is_an_anchor_with_stable_index(self, kb):
        """标题行必须包成 fp-result:<下标> 锚点，且下标与 hits 顺序一致——
        点错行等于把用户送到错误的内容上"""
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        for i in range(3):
            idx.add(f"note:{i}", f"共同词 第{i}条", kind="note",
                    title=f"标题{i}")
        idx.finalize()
        hits = idx.search("共同词")
        assert len(hits) == 3
        out = mod.render_results_html(hits, "共同词")
        for i in range(len(hits)):
            assert f'href="{mod.RESULT_SCHEME}:{i}"' in out, out
        assert f'href="{mod.RESULT_SCHEME}:{len(hits)}"' not in out

    def test_anchor_text_is_still_escaped(self, kb):
        """锚点标签是我们生成的、不进 escape；但锚点**文本**是用户标题，
        必须照旧转义——两者混在一起最容易漏一处"""
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        idx.add("note:1", "正文 命中词", kind="note", title="<b>粗体</b>标题")
        idx.finalize()
        out = mod.render_results_html(idx.search("命中词"), "命中词")
        assert "&lt;b&gt;粗体&lt;/b&gt;标题" in out
        assert "<b>粗体" not in out
        assert f'href="{mod.RESULT_SCHEME}:0"' in out

    def test_anchor_scheme_is_not_http(self, kb):
        """必须是自定义 scheme：http/file 会被 QTextBrowser 或系统浏览器抢走"""
        mod = _load_plugin_module(kb)
        assert mod.RESULT_SCHEME not in ("http", "https", "file")
        assert ":" not in mod.RESULT_SCHEME

    def test_kind_label_shown(self, kb):
        mod = _load_plugin_module(kb)
        assert mod.KIND_LABEL["knowledge"] == "知识库"
        assert mod.KIND_LABEL["asset"] == "素材"
        assert set(mod.KIND_LABEL) == {"knowledge", "note", "fragment",
                                       "task", "asset"}


# ====================================================================
# G 富文本强调色跟随主题
# ====================================================================
def _srgb_to_lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g) \
        + 0.0722 * _srgb_to_lin(b)


def _contrast(c1: str, c2: str) -> float:
    a, b = _lum(c1), _lum(c2)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class TestThemedAccent:
    """QTextBrowser 内部着色走**行内样式**，宿主的 QSS 完全管不到它。

    所以它不受 ``tests/test_theme_contrast.py``（那条护栏扫的是 QSS）保护，
    必须单独钉。本次修复的原始 bug 就在这里：早期把强调色写死成 #0a7d7b，
    浅色主题正常，**深色主题下结果标题对卡片底色只有 3.05:1，实测截图里
    几乎看不见**。
    """

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_accent_meets_wcag_against_card(self, kb, theme):
        from src.theme import get_colors
        mod = _load_plugin_module(kb)
        accent = mod.accent_for(theme)
        bg = get_colors(theme)["card_bg_solid"]
        assert _contrast(accent, bg) >= 4.5, \
            f"{theme} 主题强调色 {accent} 在卡片底色 {bg} 上对比度不足"

    def test_light_and_dark_accents_differ(self, kb):
        """两个主题必须是不同的色值——写死同一个就是本次修掉的 bug"""
        mod = _load_plugin_module(kb)
        assert mod.accent_for("light") != mod.accent_for("dark")

    def test_old_hardcoded_color_would_fail(self, kb):
        """反向锚定：证明这条护栏真的能抓到旧值（否则它只是装饰）"""
        from src.theme import get_colors
        assert _contrast("#0a7d7b", get_colors("dark")["card_bg_solid"]) < 4.5

    def test_accent_reads_host_token(self, kb):
        """强调色应当取自宿主的 $secondary_text（浅底/深底都要读得清的那个 token）"""
        from src.theme import get_colors
        mod = _load_plugin_module(kb)
        for theme in ("light", "dark"):
            assert mod.accent_for(theme) == get_colors(theme)["secondary_text"]

    def test_accent_unknown_theme_falls_back(self, kb):
        mod = _load_plugin_module(kb)
        assert mod.accent_for(None) == mod.accent_for("light")
        assert mod.accent_for("彩虹") == mod.accent_for("light")
        assert mod.accent_for("") == mod.accent_for("light")

    def test_theme_of_reads_host_property(self, kb):
        mod = _load_plugin_module(kb)

        class _Host:
            current_theme = "dark"
        assert mod.theme_of(_Host()) == "dark"

        class _Weird:
            @property
            def current_theme(self):
                raise RuntimeError("取值就炸")
        assert mod.theme_of(_Weird()) == "light"      # 不许抛
        assert mod.theme_of(None) == "light"
        assert mod.theme_of(object()) == "light"

    def test_render_injects_given_accent(self, kb):
        """accent 参数必须真的写进 HTML（否则切换主题后颜色不变）"""
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        idx.add("note:1", "每周五写周报", kind="note", title="笔记")
        idx.finalize()
        hits = idx.search("周报")
        for accent in ("#123456", "#ABCDEF"):
            out = mod.render_results_html(hits, "周报", accent)
            assert out.count(accent) == 2, out       # .hit 与 .res 各一处

    def test_render_default_accent_is_light(self, kb):
        """不传 accent 时用 light 值——保证纯函数调用方行为稳定"""
        mod = _load_plugin_module(kb)
        idx = kb.SearchIndex()
        idx.add("note:1", "每周五写周报", kind="note", title="笔记")
        idx.finalize()
        out = mod.render_results_html(idx.search("周报"), "周报")
        assert mod.accent_for("light") in out

    # ---- 兜底字面量同源护栏（2026-10-02 补）----------------------------
    # 上面 test_accent_reads_host_token 看着像同源护栏，其实**抓不到漂移**：
    # accent_for 第一步就 ``from src.theme import get_colors``，而测试跑在
    # v4/ 下这个 import 必然成功，于是它比较的是 theme 与它自己 —— 恒真。
    # 真正会漏网的是 ``_ACCENT_FALLBACK``：插件**独立分发**时 import 不到
    # src.theme，只能走这个兜底值；主程序换主题色而插件忘同步，全量测试
    # 依然全绿，真机上插件的高亮色却停在旧值。所以这里绕开运行分支，
    # 直接用 AST 钉死字面量与 theme 同源。

    def test_fallback_literal_matches_theme_tokens(self):
        """_ACCENT_FALLBACK 必须逐档等于 theme 的 secondary_text 字面量。"""
        import ast
        from src.theme import THEMES
        with open(PLUGIN_PATH, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        literal = None
        for node in tree.body:                      # 只认模块级赋值
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_ACCENT_FALLBACK"
                    for t in node.targets):
                literal = ast.literal_eval(node.value)
        assert isinstance(literal, dict), "未找到模块级 _ACCENT_FALLBACK 字面量"
        for theme in ("light", "dark"):
            assert theme in literal, f"_ACCENT_FALLBACK 缺 {theme} 档"
            assert literal[theme] == THEMES[theme]["secondary_text"], (
                f"{theme} 兜底色 {literal[theme]} 与 theme.secondary_text "
                f"{THEMES[theme]['secondary_text']} 漂移"
                f"（插件独立分发时用的就是这个值）")

    def test_fallback_branch_is_reachable(self, kb, monkeypatch):
        """证明兜底分支真会被走到 —— 否则上面那条护栏保护的可能是死代码。

        屏蔽 ``src.theme`` 模拟「插件脱离主程序独立运行」，此时 accent_for
        必须回落到 _ACCENT_FALLBACK，且返回值与字面量一致。
        """
        mod = _load_plugin_module(kb)
        monkeypatch.setitem(sys.modules, "src.theme", None)   # import 即抛
        for theme in ("light", "dark"):
            assert mod.accent_for(theme) == mod._ACCENT_FALLBACK[theme]


# ====================================================================
# H 插件契约
# ====================================================================
class TestPluginContract:
    @pytest.fixture(scope="class")
    def manifest(self):
        import json
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_manifest_passes_real_validator(self, manifest):
        from src.plugin_loader import validate_manifest
        norm, err = validate_manifest(manifest)
        assert err == "", err

    def test_declares_no_capabilities(self, manifest):
        """检索是**只读**功能：不该要 write / manage / network 任何一项"""
        assert manifest.get("capabilities", []) == []

    def test_page_key_matches_convention(self, manifest):
        """页面 key 必须带 plugin: 前缀，否则 show_plugin_page 恒返回 False"""
        assert manifest["page"]["title"].strip()
        assert f"plugin:{manifest['id']}" == "plugin:kb-search"

    def test_hotkey_free(self, manifest):
        """Ctrl+Alt+F 不能撞核心热键（K/S）与其它插件（I/R/W）"""
        import glob
        import json
        from src.plugin_loader import is_valid_hotkey
        hk = manifest["actions"][0]["hotkey"]
        assert is_valid_hotkey(hk)
        others = set()
        for path in glob.glob(os.path.join(ROOT, "plugins", "*", "manifest.json")):
            if os.path.normcase(path) == os.path.normcase(MANIFEST_PATH):
                continue
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            for act in m.get("actions", []) or []:
                if act.get("hotkey"):
                    others.add(act["hotkey"].lower().replace(" ", ""))
        assert hk.lower().replace(" ", "") not in others | {"ctrl+alt+k", "ctrl+alt+s"}

    def test_core_is_stdlib_only(self):
        """核心层必须只依赖标准库 —— 它要能在无 GUI 的 pytest 里直接跑，
        也要保证打包后不 import 任何白名单外的库

        ⚠ 必须用 AST 取**真实 import 语句**：直接搜字符串会把模块头注释里的
        「不 import PyQt6」也判成违规（本用例就被这个坑骗过一次）。
        """
        import ast
        with open(CORE_PATH, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        allowed = {"math", "re", "collections", "__future__"}
        assert mods <= allowed, \
            f"核心层引入了非标准库依赖：{sorted(mods - allowed)}"
        assert {"math", "re"} <= mods, "IDF 与 ASCII 词切分必须用 math / re"

    def test_plugin_identity(self, manifest):
        spec = importlib.util.spec_from_file_location("fp_kbs_probe", PLUGIN_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fp_kbs_probe"] = mod
        spec.loader.exec_module(mod)
        assert mod.PLUGIN_ID == manifest["id"]
        assert mod.PAGE_KEY == f"plugin:{manifest['id']}"
        assert mod.KbSearchPlugin().version == manifest["version"]
        assert callable(mod.KbSearchPlugin().create_page)


# ====================================================================
# I 碎片行重构（v1.3.0）：meta 行 / 类别色条 / 按需省略号 / 展开全文
# ====================================================================
def _frag_hits(kb, text, query=None, title="碎片 · 剪贴板", uid="fragment:1"):
    """构造 fragment 命中：走真实索引，保证 spans/matched 形状与生产一致"""
    idx = kb.SearchIndex()
    idx.add(uid, text, kind="fragment", title=title)
    idx.finalize()
    hits = idx.search(query or text)
    assert hits, "构造数据必须能命中"
    return hits


# 跨年时间戳 → format_relative_time 原样返回，断言不依赖真实时钟
_FULL_META = {"source": "拖拽拾取", "category": "link",
              "created_at": "2025-01-01 08:00"}


class TestFragmentRow:
    def test_kind_appears_once(self, kb):
        """旧版 head 是 "[碎片] 碎片 · 剪贴板"：kind 出现两次、来源当标题。
        新版 meta 行 kind 只在第一位，旧 title 不再渲染"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python")
        assert "[碎片]" not in out
        assert "碎片 · 剪贴板" not in out
        assert ">碎片<" in out          # kind 段仍在：混排结果里认得出是碎片

    def test_meta_row_segments(self, kb):
        """meta 行 = 碎片 · 来源 · 类别 · 时间"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python",
            metas={"fragment:1": _FULL_META})
        assert "碎片 · 拖拽拾取 · 链接 · 2025-01-01 08:00" in out

    def test_source_default_hidden(self, kb):
        """97% 的碎片来自剪贴板，恒显示是噪音 → source=剪贴板 不上屏"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python",
            metas={"fragment:1": {**_FULL_META, "source": "剪贴板"}})
        assert "剪贴板" not in out
        assert "碎片 · 链接 · 2025-01-01 08:00" in out

    @pytest.mark.parametrize("category,token_color", [
        ("link", "#185FA5"), ("code", "#0F6E56"), ("path", "#854F0B"),
        ("command", "#A32D2D"), ("text", "#5F5E5A"),
    ])
    def test_category_bar_fallback_literals(self, kb, category, token_color):
        """色条取 CATEGORY_TOKENS 真相源；不传 colors 时走 light 兜底
        字面量（与 theme.THEMES 逐值同源，AST 护栏见 TestFallbackGuardrails）"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python",
            metas={"fragment:1": {**_FULL_META, "category": category}})
        assert f'bgcolor="{token_color}"' in out

    def test_category_bar_host_colors_win(self, kb):
        """传了宿主主题色 dict 时用真值（主题切换不靠兜底表）"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python",
            metas={"fragment:1": _FULL_META}, colors={"link": "#123456"})
        assert 'bgcolor="#123456"' in out
        assert 'bgcolor="#185FA5"' not in out

    def test_unknown_category_placeholder(self, kb):
        """类别未知 / meta 缺失：不画色条，但占位列保留（行首对齐不塌）"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python",
            metas={"fragment:1": {**_FULL_META, "category": ""}})
        assert '<td width="3"></td>' in out
        assert "bgcolor" not in out
        out2 = mod.render_results_html(
            _frag_hits(kb, "python 脚本整理"), "python")
        assert '<td width="3"></td>' in out2

    def test_prewrap_keeps_newlines(self, kb):
        """正文 white-space:pre-wrap：多行内容不再被旧版压平"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "def a():\n    return 1", "def a"), "def a",
            metas={"fragment:1": _FULL_META})
        assert "white-space:pre-wrap" in out
        # "def" 被命中 span 包住，断言换行所在的后半段原样保留
        assert "a():\n    return 1" in out

    def test_ellipsis_only_when_truncated(self, kb):
        """旧版恒加 "…"（82% 碎片整条放得下也谎报截断）：按需、两侧独立"""
        mod = _load_plugin_module(kb)
        short = _frag_hits(kb, "python")
        assert "…" not in mod.render_results_html(short, "python")
        mid = _frag_hits(kb, "甲" * 40 + "python" + "乙" * 40, query="python")
        assert mod.render_results_html(mid, "python").count("…") == 2
        head = _frag_hits(kb, "python" + "乙" * 40, query="python")
        out = mod.render_results_html(head, "python")
        assert out.count("…") == 1
        # 前导无省略号：fbody 锚点后紧跟命中段
        assert (f'<a class="plain" href="{mod.RESULT_SCHEME}:0">'
                f'<span class="hit">python</span>') in out

    def test_score_not_in_html(self, kb):
        """分数挪进悬停提示：既没有失效的 opacity 也没有分数节点"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(_frag_hits(kb, "python 脚本"), "python")
        assert "opacity" not in out
        assert 'class="score"' not in out

    def test_result_anchor_wraps_meta_and_body(self, kb):
        """整行可点：meta 行与正文各包一个 fp-result 锚点（color:inherit）"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(
            _frag_hits(kb, "python 脚本"), "python",
            metas={"fragment:1": _FULL_META})
        assert out.count(f'href="{mod.RESULT_SCHEME}:0"') == 2

    def test_no_metas_still_renders(self, kb):
        """向后兼容：旧签名（不传 metas/colors/expanded）不抛、正文照常"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(_frag_hits(kb, "python 脚本"), "python")
        assert '<span class="hit">python</span>' in out
        assert mod.accent_for("light") in out


class TestExpandFullText:
    def test_truncated_row_has_expand_anchor(self, kb):
        mod = _load_plugin_module(kb)
        hits = _frag_hits(kb, "python" + "尾" * 120, query="python")
        out = mod.render_results_html(hits, "python")
        assert f'href="{mod.EXPAND_SCHEME}:0"' in out
        assert "展开全文" in out

    def test_full_snippet_has_no_anchor(self, kb):
        """snippet 已是全文（82% 的情况）→ 不给展开锚点，不添噪音"""
        mod = _load_plugin_module(kb)
        out = mod.render_results_html(_frag_hits(kb, "python"), "python")
        assert mod.EXPAND_SCHEME not in out

    def test_expanded_shows_full_text_and_collapse(self, kb):
        """展开态：全文上屏、高亮按全文坐标系重算、锚点变「收起」、
        省略号全部消失"""
        mod = _load_plugin_module(kb)
        hits = _frag_hits(kb, "python" + "尾" * 120, query="python")
        out = mod.render_results_html(hits, "python", expanded={0})
        assert "收起" in out
        assert "展开全文" not in out
        assert "…" not in out
        assert hits[0].text.endswith("尾" * 20)      # 构造 sanity
        assert "尾" * 20 in out                       # 被截断丢掉的尾部回来了
        assert '<span class="hit">python</span>' in out

    def test_collapsed_again_shows_expand(self, kb):
        """expanded 不含下标 = 片段态：「展开全文」回来、「收起」不出现"""
        mod = _load_plugin_module(kb)
        hits = _frag_hits(kb, "python" + "尾" * 120, query="python")
        out = mod.render_results_html(hits, "python")
        assert "展开全文" in out and "收起" not in out

    def test_expand_parse(self, kb):
        mod = _load_plugin_module(kb)
        assert mod.parse_expand_anchor("fp-expand:3") == 3
        assert mod.parse_expand_anchor("fp-expand:0") == 0
        assert mod.parse_expand_anchor("fp-expand:-1") is None
        assert mod.parse_expand_anchor("fp-expand:x") is None
        assert mod.parse_expand_anchor("") is None
        assert mod.parse_expand_anchor(None) is None

    def test_schemes_do_not_cross_parse(self, kb):
        """两个 scheme 互不解析：解析串了 = 点展开却跳走（静默错）"""
        mod = _load_plugin_module(kb)
        assert mod.parse_result_anchor("fp-expand:0") is None
        assert mod.parse_expand_anchor("fp-result:0") is None


class TestFragmentMetaCollect:
    def test_collects_three_fields(self, kb):
        mod = _load_plugin_module(kb)
        data = _FakeData(fragments=[
            {"fragment_id": 7, "content": "x", "source": "拖拽拾取",
             "category": "path", "created_at": "2026-09-30 21:00"},
            {"fragment_id": 8, "content": "y"},        # 缺字段 → 空串兜底
        ])
        meta = mod.collect_fragment_meta(data)
        assert meta["fragment:7"] == {"source": "拖拽拾取", "category": "path",
                                      "created_at": "2026-09-30 21:00"}
        assert meta["fragment:8"] == {"source": "", "category": "",
                                      "created_at": ""}

    def test_skips_dirty_entries(self, kb):
        mod = _load_plugin_module(kb)
        data = _FakeData(fragments=[None, 42, {"content": "no id"},
                                     {"fragment_id": 9, "content": "ok"}])
        assert list(mod.collect_fragment_meta(data)) == ["fragment:9"]

    def test_broken_snapshot_returns_empty(self, kb):
        """快照读取失败只降级显示（无 meta 行），不能拖垮搜索页"""
        mod = _load_plugin_module(kb)

        class _Boom:
            def fragments(self):
                raise RuntimeError("boom")

        assert mod.collect_fragment_meta(_Boom()) == {}


# ====================================================================
# J 兜底字面量同源护栏（v1.3.0，仿 accent 三件套：AST 钉 + 兜底可达）
# ====================================================================
def _module_level_literal(name):
    """从 plugin.py 源码用 AST 取模块级字面量。

    为什么绕开运行分支：测试跑在 v4/ 下 try-import 必然成功，
    运行时断言比较的是「theme 与它自己」，恒真 —— 真正会漏网的是
    插件独立分发时才走到的兜底字面量（accent 三钉的老教训）。
    用 walk 而不是只扫 tree.body：_CAT_TOKENS/_CAT_LABELS 的兜底赋值
    在 try/except 的 handler 里，不在模块顶层。
    """
    import ast
    with open(PLUGIN_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    return None


class TestFallbackGuardrails:
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_category_fallback_matches_theme_tokens(self, theme):
        """_CATEGORY_FALLBACK 逐 token 等于 theme 真值。插件独立分发时
        用的就是这个值：宿主改主题色而插件忘同步 → 真机色条停旧值，
        全量测试却照样全绿 —— 只有 AST 钉字面量能抓"""
        from src.theme import THEMES
        literal = _module_level_literal("_CATEGORY_FALLBACK")
        assert isinstance(literal, dict), "未找到 _CATEGORY_FALLBACK 字面量"
        for token in ("link", "primary", "warn", "danger",
                      "text_secondary", "text", "text_placeholder"):
            assert literal[theme][token] == THEMES[theme][token], (
                f"{theme}.{token} 兜底色 {literal[theme][token]} 与 theme "
                f"{THEMES[theme][token]} 漂移")

    def test_category_tables_fallback_matches_host(self):
        """独立分发时的 _CAT_TOKENS/_CAT_LABELS 字面量 = 宿主真相表"""
        from src.fragment_classifier import CATEGORY_LABELS, CATEGORY_TOKENS
        assert _module_level_literal("_CAT_TOKENS") == CATEGORY_TOKENS
        assert _module_level_literal("_CAT_LABELS") == CATEGORY_LABELS

    def test_category_fallback_branch_reachable(self, kb, monkeypatch):
        """屏蔽 src.fragment_classifier 模拟独立分发 → 必须回落字面量表
        且渲染不抛（否则护栏保护的是死代码）"""
        _load_plugin_module(kb)                  # 常规实例先就位
        monkeypatch.setitem(sys.modules, "src.fragment_classifier", None)
        reloaded = _load_plugin_module(kb)       # 重新 exec → 走 except 分支
        assert reloaded._CAT_TOKENS == _module_level_literal("_CAT_TOKENS")
        assert reloaded._CAT_LABELS["text"] == "普通文本"
        out = reloaded.render_results_html(
            _frag_hits(kb, "python 脚本"), "python",
            metas={"fragment:1": _FULL_META})
        assert 'bgcolor="#185FA5"' in out        # 兜底表真被用上了

    def test_time_uses_host_implementation(self, kb):
        """正常路径 meta_time_for 就是 src.time_format 那个函数（同源）"""
        from src import time_format
        mod = _load_plugin_module(kb)
        assert mod._fmt_time is time_format.format_relative_time
        assert mod.meta_time_for("2026-09-01 09:00") == \
            time_format.format_relative_time("2026-09-01 09:00")

    def test_time_fallback_branch_reachable(self, kb, monkeypatch):
        """屏蔽 src.time_format → 内嵌实现顶上，基础行为一致"""
        _load_plugin_module(kb)
        monkeypatch.setitem(sys.modules, "src.time_format", None)
        reloaded = _load_plugin_module(kb)
        assert reloaded._fmt_time is None
        assert reloaded.meta_time_for("2025-01-01 08:00") == "2025-01-01 08:00"
        assert reloaded.meta_time_for("") == "未知时间"
        assert reloaded.meta_time_for("坏格式") == "坏格式"
