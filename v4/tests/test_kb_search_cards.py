# -*- coding: utf-8 -*-
"""站内搜索 v1.5.0 结果页卡片化重设计回归（docs/站内搜索结果页重设计）。

方案钉四条底线（对应方案的三个症状 + 视觉契约）：
A core 相关度分层：min_score_rel 缺省 0 逐条不变；相对阈值把尾部命中贴
  weak（分数与排序零改动）；is_weak_query 单字/纯数字口径；weak_query
  弱查询只认「标题（碎片另加首行）」，正文顺带命中降级
B 卡片渲染纯函数：render_card_body_html 转义 / 行内高亮 / 按需省略号 /
  换行转 <br>；render_card_body 宽窗口与展开态全文；card_title_for /
  card_time_for 身份锚点
C 卡片 widget（offscreen）：展开只动本卡（他卡几何与滚动位置不动）；
  展开资格（短碎片才给）；主题刷新换色；分数 tooltip；徽章配色对比度
D 页面装配（offscreen）：范围 chips 单选且不可全不选；低相关组默认收起、
  可展开；「显示更多」追加不重渲染旧卡；弱查询空强相关给引导文案；
  「默认搜索笔记」设置在结果层生效；兜底字面量与 theme 同源（AST 钉）
"""

import importlib.util
import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CORE_PATH = os.path.join(ROOT, "plugins", "kb-search", "kb_search_core.py")
PLUGIN_PATH = os.path.join(ROOT, "plugins", "kb-search", "plugin.py")
MOD = "fp_test_kb_search_cards"
_CORE_MOD = "fp_test_kb_search_cards_core"

_APP = None      # ★ 必须留存引用：裸调用被 GC → C++ 实例销毁 → QPixmap qFatal


@pytest.fixture(scope="module")
def kb():
    spec = importlib.util.spec_from_file_location(_CORE_MOD, CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_CORE_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def plug():
    spec = importlib.util.spec_from_file_location(MOD, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def qapp():
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    yield _APP


def _index(kb, docs):
    idx = kb.SearchIndex()
    for uid, kind, title, text in docs:
        idx.add(uid, text, kind=kind, title=title)
    idx.finalize()
    return idx


def _hit(kb, text, query=None, kind="note", title="笔记 · 测试", uid="note:1"):
    """走真实索引造 Hit，保证 matched/spans 形状与生产一致"""
    idx = _index(kb, [(uid, kind, title, text)])
    hits = idx.search(query or text)
    assert hits, "构造数据必须能命中"
    return hits[0]


# ====================================================================
# A core 相关度分层
# ====================================================================
class TestTierCore:
    def test_hit_default_tier_is_strong(self, kb):
        """tier 缺省 strong：旧调用方（不传新参数）拿到的一律是强相关"""
        hit = _hit(kb, "每周五写周报")
        assert hit.tier == "strong"

    def test_default_search_behavior_unchanged(self, kb):
        """不传分层参数 = v1.4 行为逐条一致（顺序、分数、条数全同）"""
        docs = [(f"note:{i}", "note", f"笔记{i}", f"归档 第{i}份月报")
                for i in range(6)]
        idx = _index(kb, docs)
        base = idx.search("归档 月报")
        tiered = idx.search("归档 月报", min_score_rel=0.0, weak_query=False)
        assert [(h.uid, h.score) for h in base] == \
            [(h.uid, h.score) for h in tiered]
        assert all(h.tier == "strong" for h in tiered)

    def test_relative_threshold_marks_weak_tail(self, kb):
        """score < top×0.18 → weak：命中更多/更稀有词的文档稳在主列表，
        只蹭到高频低 IDF 词的长尾降级"""
        docs = [(f"note:{i}", "note", f"笔记{i}", "归档") for i in range(8)]
        docs.append(("note:x", "note", "稀有词笔记", "稀有词"))
        idx = _index(kb, docs)
        hits = idx.search("归档 稀有词", min_score_rel=0.18)
        by_uid = {h.uid: h for h in hits}
        assert by_uid["note:x"].tier == "strong"
        assert all(h.tier == "weak" for uid, h in by_uid.items()
                   if uid != "note:x")

    def test_tiering_never_changes_order_or_scores(self, kb):
        """分层只贴标记：带阈值的顺序/分数与不带时逐条相同（双 ranking
        排序契约零改动）"""
        docs = [(f"note:{i}", "note", f"笔记{i}", "归档 复盘 月报" * (i + 1))
                for i in range(6)]
        idx = _index(kb, docs)
        plain = idx.search("归档 复盘 月报")
        tiered = idx.search("归档 复盘 月报", min_score_rel=0.18)
        assert [(h.uid, h.score) for h in plain] == \
            [(h.uid, h.score) for h in tiered]
        assert {h.tier for h in tiered} <= {"strong", "weak"}

    def test_tiering_applies_after_global_top(self, kb):
        """阈值按全库 top 算，不受 kind 过滤影响（与 K1 同款口径）"""
        docs = [(f"note:{i}", "note", f"笔记{i}", "归档") for i in range(8)]
        docs.append(("note:x", "note", "稀有词笔记", "稀有词"))
        idx = _index(kb, docs)
        hits = idx.search("归档 稀有词", min_score_rel=0.18, kind="note")
        tiers = {h.uid: h.tier for h in hits}
        assert tiers["note:x"] == "strong"

    def test_negative_threshold_treated_as_zero(self, kb):
        """脏参数宽容：负值等价不分层，不抛"""
        idx = _index(kb, [("note:1", "note", "笔记", "归档")])
        hits = idx.search("归档", min_score_rel=-5.0)
        assert hits and hits[0].tier == "strong"


class TestWeakQuery:
    def test_single_char(self, kb):
        assert kb.is_weak_query("6") is True
        assert kb.is_weak_query("归") is True

    def test_pure_digits_up_to_two(self, kb):
        assert kb.is_weak_query("62") is True
        assert kb.is_weak_query("2026") is False      # 4 位数字有足够区分度
        assert kb.is_weak_query("6.0") is False       # 带点不是纯数字

    def test_normal_queries_not_weak(self, kb):
        assert kb.is_weak_query("归档") is False
        assert kb.is_weak_query("bm25") is False
        assert kb.is_weak_query("月报 归档") is False

    def test_whitespace_and_fullwidth_normalized(self, kb):
        assert kb.is_weak_query("  6  ") is True      # strip
        assert kb.is_weak_query("６") is True         # 全角归一后是单字符
        assert kb.is_weak_query("６２") is True

    def test_empty_is_not_weak(self, kb):
        assert kb.is_weak_query("") is False
        assert kb.is_weak_query("   ") is False
        assert kb.is_weak_query(None) is False

    def test_weak_query_title_match_is_strong(self, kb):
        """弱查询口径：标题字面含查询项 → strong；只在正文 → weak"""
        idx = _index(kb, [
            ("note:1", "note", "笔记 · 报表模板", "写完报告再归档"),
            ("note:2", "note", "笔记 · 会议纪要", "写完报告再归档"),
        ])
        hits = idx.search("报", weak_query=True)
        tiers = {h.uid: h.tier for h in hits}
        assert tiers == {"note:1": "strong", "note:2": "weak"}

    def test_weak_query_fragment_first_line_counts_as_head(self, kb):
        """碎片没有标题，身份锚点是首行：首行含查询项算 strong，
        第二行以后出现的算顺带命中"""
        idx = _index(kb, [
            ("fragment:1", "fragment", "碎片 · 剪贴板",
             "归档是第一行主题\n后面没有再提"),
            ("fragment:2", "fragment", "碎片 · 剪贴板",
             "开头无关的内容\n这里才出现归档一词"),
        ])
        hits = idx.search("归档", weak_query=True)
        tiers = {h.uid: h.tier for h in hits}
        assert tiers == {"fragment:1": "strong", "fragment:2": "weak"}

    def test_weak_query_floor_still_applies(self, kb):
        """弱查询与相对阈值叠加：标题命中的超低分长尾同样降级"""
        docs = [(f"note:{i}", "note", f"归档 {i}", "归档 记录" * (i * 40 + 1))
                for i in range(1, 6)]
        idx = _index(kb, docs)
        hits = idx.search("归档", min_score_rel=0.6, weak_query=True)
        assert all(h.tier in ("strong", "weak") for h in hits)
        strong = [h for h in hits if h.tier == "strong"]
        if strong:
            assert all(h.score >= hits[0].score * 0.6 - 1e-6 for h in strong)


# ====================================================================
# B 卡片渲染纯函数
# ====================================================================
class TestCardBodyRender:
    def test_escapes_user_html(self, kb, plug):
        """命中片段是用户原文，必然含 < &；转义规则与行版同源"""
        hit = _hit(kb, "代码 <script>alert(1)</script> 片段", "script")
        body = plug.render_card_body(hit)
        assert "&lt;" in body and "&gt;" in body
        assert "<script" not in body

    def test_highlight_inline_style(self, kb, plug):
        """命中处 = 强调色 + $primary_a12 底纹 + 加粗（QLabel 行内样式）"""
        hit = _hit(kb, "每周五写周报", "周报")
        body = plug.render_card_body(hit, accent="#123456",
                                     mark_bg="rgba(9, 9, 9, 0.1)")
        assert ('<span style="color:#123456;font-weight:600;'
                'background-color:rgba(9, 9, 9, 0.1)">周报</span>') in body

    def test_default_accent_and_mark_are_light(self, kb, plug):
        """不传色参时用 light 字面量——纯函数调用方行为稳定"""
        hit = _hit(kb, "每周五写周报", "周报")
        body = plug.render_card_body(hit)
        assert plug._ACCENT_FALLBACK["light"] in body
        assert plug._CARD_FALLBACK["light"]["primary_a12"] in body

    def test_ellipsis_on_demand(self, kb, plug):
        """按需省略号契约与行版逐字一致：整段放得下不加，中间截断加两个，
        尾部截断只加一个"""
        full = _hit(kb, "甲" * 60 + "目标词" + "乙" * 60, "目标词")
        assert plug.render_card_body(full).count("…") == 2
        tail = _hit(kb, "目标词" + "乙" * 60, "目标词")
        assert plug.render_card_body(tail).count("…") == 1
        short = _hit(kb, "目标词", "目标词")
        assert "…" not in plug.render_card_body(short)

    def test_newline_becomes_br(self, kb, plug):
        """QLabel rich text 不保留换行空白：\\n 必须转 <br> 才断行"""
        hit = _hit(kb, "第一行目标词\n第二行内容", "目标词")
        body = plug.render_card_body(hit)
        assert "<br>" in body
        assert "\n" not in body.replace("<br>", "")

    def test_wider_window_than_row_version(self, kb, plug):
        """卡片正文窗口 radius 40（行版 26）：同样的命中能看到更多上下文"""
        text = "甲" * 60 + "目标词" + "乙" * 60
        hit = _hit(kb, text, "目标词")
        body = plug.render_card_body(hit)                 # 缺省 radius=40
        assert "甲" * 35 in body
        narrow, _spans, _m = kb.make_snippet(text, ["目标词"], radius=26)
        assert "甲" * 35 not in narrow

    def test_expanded_shows_full_text(self, kb, plug):
        """展开态：全文上屏、高亮按全文坐标系重算、省略号消失"""
        hit = _hit(kb, "目标词" + "乙" * 120, "目标词")
        body = plug.render_card_body(hit, expanded=True)
        assert "…" not in body
        assert "乙" * 20 in body                # 被窗口丢掉的尾部回来了
        assert '<span style="color:' in body    # 高亮仍在

    def test_broken_hit_degrades_not_raises(self, kb, plug):
        """数据被外部改坏（make_snippet 抛）→ 退化为原文前 200 字，不炸页面"""
        hit = _hit(kb, "目标词内容", "目标词")

        class _BoomText(str):
            def split(self, *a, **k):
                raise RuntimeError("boom")
        hit.text = _BoomText("目标词内容")
        body = plug.render_card_body(hit)
        assert body                             # 有兜底输出即可


class TestCardTitleAndTime:
    def test_strips_kind_prefix(self, plug):
        hit = plug.core.Hit("note:1", "note", "笔记 · 周会记录", "正文", 1.0,
                            [], "", [])
        assert plug.card_title_for(hit) == "周会记录"

    def test_non_fragment_keeps_suffix_info(self, plug):
        """任务标题保留「截止」尾巴、素材保留文件名（身份信息不丢）"""
        hit = plug.core.Hit("task:1", "task", "任务 · 周报归档 · 截止 2026-10-16",
                            "周报归档", 1.0, [], "", [])
        assert plug.card_title_for(hit) == "周报归档 · 截止 2026-10-16"

    def test_fragment_title_category_and_first_line(self, plug):
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 剪贴板",
                            "第一行是主题\n第二行", 1.0, [], "", [])
        meta = {"source": "剪贴板", "category": "text", "created_at": ""}
        title = plug.card_title_for(hit, meta)
        assert title.startswith("普通文本 · ")
        assert "第一行是主题" in title

    def test_fragment_default_source_hidden(self, plug):
        """97% 碎片来自剪贴板：默认来源不上标题（v1.3 口径延续）"""
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 剪贴板",
                            "主题行", 1.0, [], "", [])
        meta = {"source": "剪贴板", "category": "link", "created_at": ""}
        title = plug.card_title_for(hit, meta)
        assert "剪贴板" not in title
        assert title.startswith("链接 · ")

    def test_fragment_nondefault_source_shown(self, plug):
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 手记",
                            "主题行", 1.0, [], "", [])
        meta = {"source": "手记", "category": "text", "created_at": ""}
        assert "手记" in plug.card_title_for(hit, meta)

    def test_fragment_without_usable_meta_falls_back(self, plug):
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 剪贴板",
                            "", 1.0, [], "", [])
        assert plug.card_title_for(hit, None) == "碎片"

    def test_fragment_title_first_line_capped(self, plug):
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 剪贴板",
                            "字" * 60, 1.0, [], "", [])
        title = plug.card_title_for(hit, {"category": "", "source": ""})
        assert len(title) <= 40 and title.endswith("…")

    def test_fragment_time_from_meta(self, plug):
        hit = plug.core.Hit("fragment:1", "fragment", "碎片 · 剪贴板", "x",
                            1.0, [], "", [])
        meta = {"source": "", "category": "",
                "created_at": "2025-01-01 08:00"}
        # 跨年时间戳 → format_relative_time 原样返回，断言不依赖真实时钟
        assert plug.card_time_for(hit, meta) == "2025-01-01 08:00"

    def test_non_fragment_has_no_time(self, plug):
        hit = plug.core.Hit("note:1", "note", "笔记 · x", "x", 1.0, [], "", [])
        assert plug.card_time_for(hit, {"created_at": "2025-01-01"}) == ""
        assert plug.card_time_for(hit, None) == ""


# ====================================================================
# C 卡片 widget（offscreen）
# ====================================================================
def _card_hit(kb, text, query=None, kind="fragment", uid="fragment:1"):
    return _hit(kb, text, query, kind=kind, uid=uid,
                title="碎片 · 剪贴板" if kind == "fragment" else "笔记 · 测试")


class TestResultCardWidget:
    @pytest.fixture()
    def colors(self, plug):
        return None            # 走字面量兜底，测试行为稳定

    def _card(self, plug, kb, text, query=None, index=0, kind="fragment",
              meta=None):
        hit = _card_hit(kb, text, query, kind=kind, uid=f"{kind}:1")
        page = types.SimpleNamespace(_accent="#0C5A47",
                                     _on_card_link=lambda href: None)
        return plug.ResultCard(page, index, hit, meta, None, "light")

    def test_expand_only_moves_own_card(self, qapp, plug, kb, colors):
        """展开某张卡：其余卡的几何纹丝不动（v1.5 的核心承诺）——
        把可展开卡放在末尾，展开前后其他卡 geometry 逐字节相同"""
        from PyQt6.QtWidgets import QWidget, QVBoxLayout
        host = QWidget()
        host.resize(560, 700)
        lay = QVBoxLayout(host)
        lay.setSpacing(8)
        cards = [self._card(plug, kb, "普通短卡内容", index=i) for i in range(2)]
        tail = self._card(plug, kb, "目标词" + "乙" * 120, "目标词", index=2)
        for c in cards + [tail]:
            lay.addWidget(c)
        host.show()
        qapp.processEvents()
        before = [c.geometry().getRect() for c in cards]
        assert tail._can_expand()
        assert tail.toggle_expand() is True
        qapp.processEvents()
        assert [c.geometry().getRect() for c in cards] == before
        assert "乙" * 20 in tail._body.text()      # 全文上屏
        assert "收起" in tail._foot.text()
        host.deleteLater()

    def test_collapse_restores_snippet(self, qapp, plug, kb):
        card = self._card(plug, kb, "目标词" + "乙" * 120, "目标词")
        card.toggle_expand()
        assert "收起" in card._foot.text()
        card.toggle_expand()
        assert "展开" in card._foot.text()
        assert "乙" * 60 not in card._body.text()  # 只在全文里的尾部收走了
        assert "…" in card._body.text()            # 回到窗口片段态

    def test_expand_eligibility(self, qapp, plug, kb):
        """行内展开只给**短**碎片：全文片段、超 800 字、非碎片都不给"""
        assert self._card(plug, kb, "目标词" + "乙" * 120, "目标词")._can_expand()
        assert not self._card(plug, kb, "目标词整段放得下", "目标词")._can_expand()
        assert not self._card(plug, kb, "目标词" + "乙" * 900,
                              "目标词")._can_expand()
        note = self._card(plug, kb, "目标词" + "乙" * 120, "目标词", kind="note")
        assert not note._can_expand()              # 非碎片不给行内展开

    def test_expand_blocked_returns_false(self, qapp, plug, kb):
        """不可展开的卡 toggle 是 no-op（fp-expand 走到也不出错）"""
        card = self._card(plug, kb, "目标词整段放得下", "目标词")
        assert card.toggle_expand() is False

    def test_score_tooltip_on_card(self, qapp, plug, kb):
        card = self._card(plug, kb, "目标词" + "乙" * 40, "目标词")
        assert card.toolTip().startswith("相关度 ")

    def test_apply_theme_restyles_card(self, qapp, plug, kb):
        """主题刷新：容器/徽章/正文随主题换色，展开态保持不变"""
        card = self._card(plug, kb, "目标词" + "乙" * 120, "目标词")
        card.toggle_expand()
        card.apply_theme(None, "dark")
        assert plug._CARD_FALLBACK["dark"]["card_bg_solid"] in card.styleSheet()
        assert plug._CATEGORY_FALLBACK["dark"]["text_secondary"] \
            in card._badge.styleSheet()            # 碎片徽章 = text_secondary
        assert "收起" in card._foot.text()          # 展开态跨主题保持
        dark_body = card._body.text()
        assert plug._CATEGORY_FALLBACK["dark"]["text"] in dark_body

    def test_badge_text_is_kind_label(self, qapp, plug, kb):
        card = self._card(plug, kb, "目标词" + "乙" * 40, "目标词")
        assert card._badge.text() == "碎片"

    def test_title_uses_identity_and_anchor(self, qapp, plug, kb):
        """头行标题 = 类别 · 首行，整段包 fp-result 锚点"""
        card = self._card(plug, kb, "主题行内容" + "乙" * 40, "主题行",
                          meta={"source": "剪贴板", "category": "text",
                                "created_at": ""})
        rich = card._title.text()
        assert f'href="{plug.RESULT_SCHEME}:0"' in rich
        assert "普通文本" in rich

    def test_anchor_schemes_on_links(self, qapp, plug, kb):
        """尾行两个链接各走各的 scheme：展开 fp-expand、跳转 fp-result"""
        card = self._card(plug, kb, "目标词" + "乙" * 120, "目标词")
        foot = card._foot.text()
        assert f'href="{plug.EXPAND_SCHEME}:0"' in foot
        assert f'href="{plug.RESULT_SCHEME}:0"' in foot

    def test_link_clicks_route_to_page(self, qapp, plug, kb):
        """卡片所有链接统一走 page._on_card_link（QUrl 单点分发）"""
        got = []
        page = types.SimpleNamespace(_accent="#0C5A47",
                                     _on_card_link=got.append)
        hit = _card_hit(kb, "目标词" + "乙" * 120, "目标词")
        card = plug.ResultCard(page, 3, hit, None, None, "light")
        card._foot.linkActivated.emit(f"{plug.RESULT_SCHEME}:3")
        card._foot.linkActivated.emit(f"{plug.EXPAND_SCHEME}:3")
        assert got == [f"{plug.RESULT_SCHEME}:3", f"{plug.EXPAND_SCHEME}:3"]

    def test_full_text_hit_has_no_expand_link(self, qapp, plug, kb):
        """snippet 已是全文（82% 的情况）→ 尾行没有展开锚点，不添噪音"""
        card = self._card(plug, kb, "目标词整段放得下", "目标词")
        assert plug.EXPAND_SCHEME not in card._foot.text()


class TestBadgeContrast:
    """徽章文字色对其卡底（+12% 同色浅底）必须过 WCAG AA——
    徽章是新增色组合，QSS 护栏（test_theme_contrast）扫不到它"""

    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("kind", ["knowledge", "note", "fragment",
                                      "task", "asset"])
    def test_badge_text_meets_wcag_on_card(self, plug, theme, kind):
        from src.theme import get_colors
        token = plug._KIND_TOKEN[kind]
        fg = plug._card_token(token, None, theme)
        bg_hex = get_colors(theme)["card_bg_solid"]
        tinted = _blend_over(fg, bg_hex, 0.12)
        assert _contrast(fg, tinted) >= 4.5, (
            f"{theme}/{kind} 徽章 {fg} 对浅底 {tinted} 对比度不足")


def _blend_over(fg_hex, bg_hex, alpha):
    """前景以 alpha 叠在纯色底上（与 _with_alpha 的渲染效果同式）"""
    def _rgb(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    fr, fg_, fb = _rgb(fg_hex)
    br, bg_, bb = _rgb(bg_hex)
    return "#{:02X}{:02X}{:02X}".format(
        *(round(a * alpha + b * (1 - alpha))
          for a, b in ((fr, br), (fg_, bg_), (fb, bb))))


def _lum(hex_color: str) -> float:
    def _lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(c1: str, c2: str) -> float:
    a, b = _lum(c1), _lum(c2)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# ====================================================================
# D 页面装配（offscreen）
# ====================================================================
def _page_ctx(notes=None, fragments=None):
    data = types.SimpleNamespace(
        tasks=lambda: [], fragments=lambda: fragments or [],
        notes=lambda: notes or [], knowledge=lambda: [], assets=lambda: [])
    return types.SimpleNamespace(
        plugin_id="kb-search", data=data,
        parent_window=lambda: None, show_toast=lambda *a, **k: None,
        logger=types.SimpleNamespace(warning=lambda *a, **k: None,
                                     info=lambda *a, **k: None))


class TestSearchPageCards:
    @pytest.fixture()
    def page(self, qapp, plug):
        # 数据设计（口径见各用例）：
        #   - 25 条笔记都含「内容」→ 常规查询出满一页 + 分页余量；
        #   - 只有 note:1 标题带「6」，只有 note:2 / 碎片 1 的**正文**带
        #     独立数字 → 弱查询「6」：强 1 条、弱 2 条；
        #   - 没有任何标题带「1」→ 弱查询「1」零强命中 → 引导文案；
        #   - 碎片 2 约 140 字：常规查询强命中（BM25 长度归一后仍 > 阈值），
        #     窗口片段截断 → 可行内展开，且把结果列撑到可滚动。
        notes = (
            [{"note_id": 1, "title": "批次 6 清单", "content": "正常内容记录"},
             {"note_id": 2, "title": "项目内容记录",
              "content": "日期是 2026 年 6 月 1 日"}]
            + [{"note_id": i, "title": "项目批次类目综述",
                "content": f"编号 {i} 的项目内容记录"}
               for i in range(10, 33)])
        fragments = [
            {"fragment_id": 1, "content": "普通记录第一行\n在第 6 条附注里出现",
             "source": "剪贴板", "category": "text",
             "created_at": "2025-01-01 08:00"},
            {"fragment_id": 2, "content": "开头内容记录" + "甲" * 120
             + "结尾内容记录",
             "source": "手记", "category": "text",
             "created_at": "2025-01-02 09:00"},
        ]
        ctx = _page_ctx(notes=notes, fragments=fragments)
        w = plug.SearchPage(ctx)
        w.show()
        qapp.processEvents()
        yield w
        w.deleteLater()

    def test_chips_single_select_default_all(self, page, plug):
        """默认「全部」chip：等价旧版五源全开（search 不过滤）"""
        assert set(page._kind_chips) == {"all", *plug.KIND_LABEL}
        assert page._chip_key == "all"
        assert page._selected_kinds() == list(plug.KIND_LABEL.keys())

    def test_chip_click_filters_and_restates_scope(self, page):
        page._input.setText("内容")
        page._on_chip_clicked("note")
        assert page._hits and all(h.kind == "note" for h in page._hits)
        assert "范围" in page._stat.text()
        page._on_chip_clicked("all")
        kinds = {h.kind for h in page._hits}
        assert "fragment" in kinds

    def test_reclick_active_chip_never_empties_scope(self, page):
        """单选语义不允许「全部不选」：再点当前 chip 恢复选中态"""
        page._input.setText("内容")
        page._on_chip_clicked("note")
        page._kind_chips["note"].click()          # 模拟用户再点一下
        assert page._chip_key == "note"
        assert page._kind_chips["note"].isChecked()

    def test_weak_tier_folds_body_only_hits(self, page):
        """弱查询「6」：标题命中的 25 条笔记进主列表，正文顺带命中的
        碎片折叠进低相关组（默认收起）"""
        page._input.setText("6")
        page._run_search()
        assert page._strong_idx and page._weak_idx
        assert all(page._hits[i].tier == "strong" for i in page._strong_idx)
        assert "低相关" in page._stat.text() and "已折叠" in page._stat.text()
        assert not page._weak_box.isVisible()     # 默认收起
        assert page._weak_bar.isVisible()
        assert len(page._weak_cards) == len(page._weak_idx)
        # 主列表卡与低相关卡不重叠
        main_ids = {id(c) for c in page._cards}
        assert main_ids.isdisjoint({id(c) for c in page._weak_cards})

    def test_weak_toggle_expands_group(self, page):
        page._input.setText("6")
        page._run_search()
        page._weak_bar.click()
        assert page._weak_box.isVisible()
        assert "收起" in page._weak_bar.text()
        page._weak_bar.click()
        assert not page._weak_box.isVisible()
        assert "展开" in page._weak_bar.text()

    def test_weak_query_without_strong_shows_guidance(self, page):
        """弱查询零强命中：引导换更具体的关键词，不误报「没有匹配」。

        「1」只出现在 note:2 正文（「1 日」），没有任何标题带它 →
        全部命中降级进低相关组，主列表空但低相关组在。"""
        page._input.setText("1")
        page._run_search()
        assert page._hits and not page._strong_idx
        assert page._weak_idx
        assert "没有强相关结果" in page._stat.text()
        assert not page._empty.isVisible()        # 低相关组在，不算全空

    def test_more_button_appends_without_rerender(self, page, plug):
        """「显示更多」只追加新卡：旧卡实例原样保留、滚动位置不动"""
        page._input.setText("内容")
        page._run_search()
        page._debounce.stop()      # 同上：防去抖二次重搜换掉卡片实例
        assert len(page._cards) == plug.PAGE_SIZE
        old_ids = [id(c) for c in page._cards]
        sb = page._scroll.verticalScrollBar()
        sb.setValue(30)
        qapp_process()
        before = sb.value()
        page._more_btn.click()
        assert [id(c) for c in page._cards[:len(old_ids)]] == old_ids
        assert sb.value() == before
        assert not page._more_btn.isVisible()      # 26 条全部出完（25 笔记+1 碎片）

    def test_expand_via_anchor_keeps_scroll(self, qapp, page, plug):
        """fp-expand 锚点 → 卡内展开：滚动位置不变（整页弹跳的根因已除）"""
        page._input.setText("内容")
        page._run_search()
        page._show_more()          # 碎片卡可能排在第二页，先全部铺出来
        page._debounce.stop()      # setText 起的去抖 220ms 后会重搜并重建卡片，
                                   # 卡片身份断言必须钉在这一次渲染上（时序教训）
        sb = page._scroll.verticalScrollBar()
        # 等布局就绪（滚动范围非零）再取基准——否则 setValue 被钳到 0，
        # 断言变成碰运气（首跑字体加载会改变布局时序）
        for _ in range(10):
            qapp_process()
            if sb.maximum() > 0:
                break
        sb.setValue(40)
        qapp_process()
        before = sb.value()
        assert before > 0, f"前置失败：内容应可滚动（max={sb.maximum()}）"
        frag_idx = next(i for i, h in enumerate(page._hits)
                        if h.kind == "fragment")
        page._on_anchor(_qurl(f"{plug.EXPAND_SCHEME}:{frag_idx}"))
        qapp_process()
        assert sb.value() == before
        card = page._card_for_hit(frag_idx)
        assert card is not None and "收起" in card._foot.text()
        assert "甲" * 50 in card._body.text()      # 全文上屏

    def test_jump_anchor_still_dispatches(self, page, plug):
        """fp-result 锚点仍走 _jump_to（宿主缺跳转入口 → toast 兜底）"""
        page._input.setText("内容")
        page._run_search()
        page._ctx = types.SimpleNamespace(
            plugin_id="kb-search", data=page._ctx.data,
            parent_window=lambda: None,
            show_toast=lambda msg, ms=0: setattr(page, "_last_toast", msg),
            logger=types.SimpleNamespace(warning=lambda *a, **k: None,
                                         info=lambda *a, **k: None))
        page._on_anchor(_qurl(f"{plug.RESULT_SCHEME}:0"))
        assert "跳转" in getattr(page, "_last_toast", "")

    def test_search_notes_setting_filters_note_hits(self, page):
        """「默认搜索笔记」设置：关掉后「全部」范围剔除笔记，显式选
        「笔记」chip 仍可只搜笔记"""
        page._input.setText("内容")
        page._search_notes = False
        page._run_search()
        assert page._hits and all(h.kind != "note" for h in page._hits)
        page._on_chip_clicked("note")
        assert page._hits and all(h.kind == "note" for h in page._hits)

    def test_empty_state_uses_card_hint(self, page, plug):
        assert page._empty.text() == plug.EMPTY_HINT
        page._input.setText("不存在的词组合xyz")
        page._run_search()
        assert page._empty.isVisible() and not page._cards
        assert "没有匹配" in page._stat.text()

    def test_index_ready_stat_unchanged(self, page):
        page.rebuild_index()
        assert "已索引" in page._stat.text()


def qapp_process():
    _APP.processEvents()


def _qurl(text):
    from PyQt6.QtCore import QUrl
    return QUrl(text)
