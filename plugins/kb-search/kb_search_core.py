# -*- coding: utf-8 -*-
"""
====================================================================
站内检索核心  -  kb-search 插件的纯逻辑层
====================================================================
只依赖 Python 标准库，**不 import PyQt6**（与宿主 `md_export.py` 同一约定，
这样 pytest 能在无 GUI 环境直接跑它）。

为什么要自己写分词：插件依赖白名单只有 PyQt6 + 标准库，`jieba` / `whoosh`
这类库装进来也用不了（打包后 import 直接失败）。所以中文检索这条路只能
自己铺：词典 + 双向最大匹配 + 倒排索引 + BM25。

三个不显然的设计决定（都是为了让「没有大词表」也能搜得准）：

  1. **检索项 = 分词得到的词 ∪ 整段的字符 bigram，索引侧与查询侧同构**
     词典只收录常见词，用户笔记里的专有名词（项目代号、客户名）一定不在
     里面。只索引「词」会把这些词切碎后丢掉；只索引单字区分度又太低。
     补上字符 bigram 后，自造词也能对齐（bigram 在**整段**上生成，跨词）：
        查询「月报归档」 → 月报 / 归档 / 报归
        文档「归档月报」 → 归档 / 月报 / 档月
     共享 2 个词、各带一个独有的跨词 bigram —— 于是语序也成了信号
     （调换词序的两句话不再同分）。**索引侧与查询侧必须走同一个
     tokenize()**，否则两边切法不一致，永远调不通（这是自研检索最容易
     踩的坑）。

  2. **停用词只删「整项都是功能字」的检索项**
     「的 / 了 / 是 / 和 / 在」这类去掉能显著提精度；但「报」「表」「单」
     既是构词成分又常单独成词，**不能删**——删了会把「月报」的 bigram
     一起毁掉。判据是「该项的每个字都是功能字」才丢弃。

  3. **IDF 用 ln(1 + 经典式)**，不用经典 ln((N-df+0.5)/(df+0.5))
     经典式在 df > N/2（常见词）时为负，会让「命中更多常见词」的文档反而
     扣分。本插件的数据量小（知识库段落 + 笔记 + 片段，几千条），常见项
     的 df 很容易过半，+1 形式保证 IDF 恒 ≥ 0。

排序之外还提供**命中片段**：取命中项两边的上下文，并给出命中字符区间下标，
让 UI 自己做高亮——核心层不产出富文本标记，因为它不知道调用方用什么控件
（宿主 QLabel 不渲染 Markdown，只能拆成多个控件或自绘）。
====================================================================
"""

import math
import re
from collections import Counter

# ====================================================================
# 字符分类
# ====================================================================
# CJK 统一表意文字 + 扩展 A + 兼容表意
_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
# 日文假名 / 韩文音节：这几类字在中文文本里出现即按「单字」处理
_ALT_RANGES = ((0x3040, 0x30FF), (0xAC00, 0xD7AF))

# ASCII 词字符（英文单词、数字、下划线、版本号、路径、URL、邮箱、
# 时间 09:30、小数 1.5）。最后的标点会被 strip 掉，所以这里可以放宽。
_ASCII_WORD = re.compile(r"[0-9a-z_](?:[0-9a-z_./:@#+\-~]*[0-9a-z_])?")
_ASCII_TRIM = "./:@#+-~"
# ASCII 词内部的分隔符（URL / 路径 / 日期 / 版本号 / 邮箱）
_ASCII_SEP = re.compile(r"[./:@#+\-~_]+")

# 功能字（停用字）：只用来判「整个检索项是否都无意义」。
# ⚠ 刻意很短：多删一个字就多丢一批召回，宁可让 BM25 的 IDF 去压权。
STOP_CHARS = set(
    "的了是和与在也就都很而被把对将为着过之其所以及或者这那有没不我你他她它"
    "们个上下内外中前后里此该等一二三四五六七八九十"
)
# 明确的英文停用词（不进索引，查询侧同样过滤）
STOP_WORDS = frozenset((
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "it", "this", "that", "with", "as", "at",
    "by", "from", "we", "you", "he", "she", "they", "not", "no", "yes",
))

# ====================================================================
# 内置基础词表（办公场景，2–4 字）
# --------------------------------------------------------------------
# 刻意保持精炼：它的作用是**提高精度**（把「周报」当整体而不是切成两个
# 字），不是召回的主力——召回由 bigram 兜底。所以宁少勿滥，只放高频、
# 歧义小的词；长词优先匹配，靠按长度分桶实现。
# ====================================================================
BASE_WORDS = (
    # 任务 / 日程
    "任务", "待办", "日程", "截止", "逾期", "提醒", "优先级", "子任务",
    "周报", "日报", "月报", "年报", "复盘", "总结", "归档", "周期",
    "今天", "明天", "昨天", "本周", "上周", "下周", "本月", "上月", "下月",
    "周一", "周二", "周三", "周四", "周五", "周六", "周日", "星期",
    # 内容 / 记录
    "笔记", "碎片", "知识库", "文档", "模板", "草稿", "正文", "标题",
    "备注", "标签", "分类", "目录", "附件", "图片", "截图", "链接",
    "会议", "纪要", "记录", "要点", "结论", "方案", "计划", "进度",
    # 业务 / 办公
    "客户", "需求", "报价", "合同", "订单", "发票", "报销", "预算",
    "项目", "版本", "发布", "上线", "测试", "验收", "交付", "迭代",
    "流程", "规范", "标准", "说明", "指南", "手册", "文档归档",
    # 技术
    "插件", "接口", "配置", "参数", "日志", "异常", "报错", "修复",
    "代码", "函数", "模块", "数据", "缓存", "索引", "搜索", "检索",
    "分词", "排序", "性能", "优化", "重构", "测试用例", "数据库",
    # 通用高频
    "时间", "地点", "内容", "问题", "原因", "结果", "方法", "步骤",
    "注意", "重要", "紧急", "完成", "进行", "开始", "结束", "修改",
)


def _word_buckets(words):
    """按长度分桶，便于最大匹配从长到短试（长词优先，避免「周报」被切成「周」「报」）"""
    buckets = {}
    for w in words:
        w = str(w or "").strip()
        if len(w) >= 2:
            buckets.setdefault(len(w), set()).add(w)
    return buckets


class Tokenizer:
    """把文本切成**检索项**列表（词 ∪ 字符 bigram），索引与查询共用。

    ``user_words`` 可以追加自定义词（宿主数据里的高频词后续可注入，
    本插件先把接口留出来）。
    """

    MIN_WORD_LEN = 2
    MAX_WORD_LEN = 8                    # 超过这个长度的词不进词表（多半是误匹配）

    def __init__(self, user_words=()):
        words = list(BASE_WORDS) + list(user_words or ())
        self._buckets = _word_buckets(words)
        self._max_len = min(self.MAX_WORD_LEN,
                            max(self._buckets) if self._buckets else 2)
        self._max_len = max(self._max_len, self.MIN_WORD_LEN)

    # ---------------- 归一化 ----------------
    @staticmethod
    def normalize(text) -> str:
        """全角→半角、英文小写、空白压缩。

        全角数字/字母（用户从 Word 粘贴时很常见）不归一化就永远搜不到，
        这是中文文本处理里最容易漏的一步。
        """
        if not isinstance(text, str):
            text = str(text or "")
        out = []
        for ch in text:
            code = ord(ch)
            if code == 0x3000:                   # 全角空格
                ch = " "
            elif 0xFF01 <= code <= 0xFF5E:       # 全角 ASCII 区
                ch = chr(code - 0xFEE0)
            out.append(ch)
        return "".join(out).lower()

    # ---------------- 字符判定 ----------------
    @staticmethod
    def _is_cjk(ch) -> bool:
        code = ord(ch)
        return any(lo <= code <= hi for lo, hi in _CJK_RANGES)

    @staticmethod
    def _is_alt(ch) -> bool:
        code = ord(ch)
        return any(lo <= code <= hi for lo, hi in _ALT_RANGES)

    @classmethod
    def _is_ideograph(cls, ch) -> bool:
        """按「字」处理的字符（中文/日文/韩文）—— 与 ASCII 词分开走两条路"""
        return cls._is_cjk(ch) or cls._is_alt(ch)

    # ---------------- 分词 ----------------
    def _cut_forward(self, seq):
        """正向最大匹配：从长到短试词表，命中就切走，否则单字成项"""
        out, i, n = [], 0, len(seq)
        while i < n:
            hit = None
            for size in range(min(self._max_len, n - i), 1, -1):
                cand = "".join(seq[i:i + size])
                if cand in self._buckets.get(size, ()):
                    hit = cand
                    break
            if hit is None:
                out.append(seq[i])
                i += 1
            else:
                out.append(hit)
                i += len(hit)
        return out

    def _cut_backward(self, seq):
        """反向最大匹配：从右往左切，用来与正向对比择优"""
        out, i = [], len(seq)
        while i > 0:
            hit = None
            for size in range(min(self._max_len, i), 1, -1):
                cand = "".join(seq[i - size:i])
                if cand in self._buckets.get(size, ()):
                    hit = cand
                    break
            if hit is None:
                out.append(seq[i - 1])
                i -= 1
            else:
                out.insert(0, hit)
                i -= len(hit)
        return out

    @staticmethod
    def _better(a, b):
        """择优：词数少优先 → 单字少优先 → 正向（业界同款启发式）

        词数少 = 切出的长词多 = 更接近人读句子时的心理切分。
        """
        ka = (len(a), sum(1 for t in a if len(t) == 1))
        kb = (len(b), sum(1 for t in b if len(t) == 1))
        return a if ka <= kb else b

    def cut(self, text):
        """对一段已归一化文本做中文分词（返回词列表，含单字回退）"""
        seq = list(text)
        if not seq:
            return []
        return self._better(self._cut_forward(seq), self._cut_backward(seq))

    # ---------------- 检索项 ----------------
    @staticmethod
    def _bigrams(word):
        """字符 bigram（长度为 1 的词没有 bigram）"""
        return [word[i:i + 2] for i in range(len(word) - 1)]

    @staticmethod
    def _all_stop(term) -> bool:
        """整项都是功能字才算停用项（只删单字形态，见模块头注释第 2 条）"""
        return all(ch in STOP_CHARS for ch in term)

    def _segment_terms(self, segment):
        """一个连续汉字段 → 检索项列表（词 + 跨词 bigram）

        bigram 取**整段原文**（而不是逐词生成），因此会跨词：

          - 语序成为信号：「月报归档」得到 报归、「归档月报」得到 档月，
            调换词序的两句话不再同分
          - 兜住词典外的自造词：切成一串单字时单字本身没有 bigram，
            整段拼接/整段取才有（「星尘三号」→ 星尘 / 尘三 / 三号）

        ⚠ 与词完全相同的 bigram（2 字词必然如此）不重复计入，否则该词的
        tf 凭空翻倍，长文档归一化会被算歪。
        """
        words = [w for w in self.cut(segment) if not self._all_stop(w)]
        out = list(words)
        word_set = set(words)
        for bg in self._bigrams(segment):
            if bg not in word_set and not self._all_stop(bg):
                out.append(bg)
        return out

    @staticmethod
    def _split_ascii(word):
        """把长 ASCII 词（URL / 路径 / 日期 / 版本号）再拆出子块

        整词保留是为了「搜 ``bm25-index`` 命中它本身」；拆子块是为了
        「只记得其中一段也能搜到」——在 URL 里找一个 slug、在日期串里找
        年份、在路径里找一个目录名，都是真实用法。不这么做的话
        ``https://example.com/bm25-index`` 会变成一个整项，搜 ``bm25-index``
        永远搜不到（实测踩到）。

        子块长度 < 2 的丢掉（否则 ``1.2`` 会往索引里灌一堆单字符项）。
        """
        if len(word) < 4:
            return []
        parts = [p for p in _ASCII_SEP.split(word)
                 if len(p) >= 2 and p not in STOP_WORDS]
        return parts if len(parts) > 1 else []

    def terms(self, text):
        """文本 → 检索项列表（可重复，用于算词频）

        输出 = 分词结果（词/单字） ∪ 每个汉字段的字符 bigram，
        再剔除「整项都是功能字」的项和英文停用词。
        """
        normalized = self.normalize(text)
        out = []
        pos = 0
        n = len(normalized)
        while pos < n:
            ch = normalized[pos]
            if self._is_ideograph(ch):
                # 收集连续的汉字段，整段一起分词（跨标点会切错）
                end = pos
                while end < n and self._is_ideograph(normalized[end]):
                    end += 1
                out.extend(self._segment_terms(normalized[pos:end]))
                pos = end
                continue
            m = _ASCII_WORD.match(normalized, pos)
            if m:
                word = m.group(0).strip(_ASCII_TRIM)
                # 纯数字也保留：搜「2026」要能命中日期
                if word and word not in STOP_WORDS:
                    out.append(word)
                    out.extend(self._split_ascii(word))
                pos = m.end()
                continue
            pos += 1                    # 标点/空白：跳过
        return out


# 默认分词器（模块级单例，避免每次查询重建词表分桶）
_DEFAULT = Tokenizer()


def tokenize(text):
    """便捷入口：用默认词表取检索项（索引与查询都用它）"""
    return _DEFAULT.terms(text)


# ====================================================================
# 检索结果
# ====================================================================
class Hit:
    """一条命中结果（纯数据，UI 直接拿来渲染）"""

    __slots__ = ("uid", "kind", "title", "text", "score", "matched",
                 "snippet", "spans")

    def __init__(self, uid, kind, title, text, score, matched, snippet, spans):
        self.uid = uid                # 调用方给的稳定标识（如 "knowledge:3"）
        self.kind = kind              # 数据源标识（knowledge / note / ...）
        self.title = title            # 展示用标题
        self.text = text              # 原文（未截断）
        self.score = score            # BM25 分
        self.matched = matched        # 命中的检索项（去重、按出现顺序）
        self.snippet = snippet        # 命中上下文片段（纯文本）
        self.spans = spans            # snippet 里命中项的 (start, end) 区间

    def __repr__(self):               # pragma: no cover - 只为调试可读
        return (f"Hit(uid={self.uid!r}, score={self.score:.3f}, "
                f"matched={self.matched[:4]!r})")


# ====================================================================
# 倒排索引 + BM25
# ====================================================================
class SearchIndex:
    """倒排索引（term → 文档词频）与 BM25 打分。

    BM25 参数取业界默认 k1=1.5 / b=0.75：
      - k1 控制词频饱和（一个词出现 20 次不比出现 5 次重要多少）
      - b  控制长度归一化（长文档天然命中多，要压一压）
    数据量小时调参收益很低，用默认值最稳。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, tokenizer=None):
        self.k1 = k1
        self.b = b
        self._tk = tokenizer or _DEFAULT
        self._postings = {}          # term -> {uid: tf}
        self._docs = {}              # uid -> (kind, title, text, length)
        self._finalized = False
        self._avgdl = 0.0
        self._n = 0

    # ---------------- 建索引 ----------------
    def add(self, uid, text, kind="", title=""):
        """加一条文档。``uid`` 必须唯一（重复 uid 覆盖旧内容）"""
        uid = str(uid)
        if uid in self._docs:
            self.remove(uid)
        terms = self._tk.terms(text)
        self._docs[uid] = (str(kind or ""), str(title or ""),
                           text if isinstance(text, str) else str(text or ""),
                           len(terms))
        for term, tf in Counter(terms).items():
            self._postings.setdefault(term, {})[uid] = tf
        self._finalized = False
        return len(terms)

    def remove(self, uid):
        """移除一条文档（增量重建搜索结果时用）"""
        uid = str(uid)
        if uid not in self._docs:
            return False
        del self._docs[uid]
        for term in list(self._postings):
            bucket = self._postings[term]
            if bucket.pop(uid, None) is not None and not bucket:
                del self._postings[term]
        self._finalized = False
        return True

    def clear(self):
        self._postings.clear()
        self._docs.clear()
        self._finalized = False

    def finalize(self):
        """统计平均文档长度与文档数（每次 add/remove 后由 search 自动调用）"""
        self._n = len(self._docs)
        total = sum(d[3] for d in self._docs.values())
        self._avgdl = (total / self._n) if self._n else 0.0
        self._finalized = True

    # ---------------- 查询 ----------------
    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def term_count(self) -> int:
        return len(self._postings)

    def _idf(self, term) -> float:
        """ln(1 + (N - df + 0.5) / (df + 0.5))，恒 ≥ 0（见模块头第 3 条）"""
        df = len(self._postings.get(term, ()))
        if df <= 0:
            return 0.0
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query, top_n: int = 20, kind=None):
        """返回 ``[Hit, ...]``（按分数降序，分数相同按标题稳定排序）

        ``kind`` 给定时只在该数据源内检索（UI 的分类筛选）。
        """
        empty = []
        qterms = self._tk.terms(query)
        if not qterms or not self._docs:
            return empty
        if not self._finalized:
            self.finalize()

        # 查询项去重但保留顺序（命中项列表给 UI 展示，顺序要稳定）
        uniq = []
        for t in qterms:
            if t not in uniq:
                uniq.append(t)

        scores = {}
        for term in uniq:
            bucket = self._postings.get(term)
            if not bucket:
                continue
            idf = self._idf(term)
            if idf <= 0:
                continue
            for uid, tf in bucket.items():
                doc_len = self._docs[uid][3]
                denom = tf + self.k1 * (
                    1.0 - self.b + self.b * (doc_len / self._avgdl
                                             if self._avgdl else 1.0))
                scores[uid] = scores.get(uid, 0.0) + idf * (
                    tf * (self.k1 + 1.0)) / denom

        if not scores:
            return empty

        hits = []
        for uid, score in scores.items():
            kind_, title, text, _dl = self._docs[uid]
            if kind is not None and kind_ != kind:
                continue
            snippet, spans, matched = make_snippet(
                text, [t for t in uniq if uid in self._postings.get(t, ())])
            hits.append(Hit(uid=uid, kind=kind_, title=title, text=text,
                            score=score, matched=matched, snippet=snippet,
                            spans=spans))
        # 分数降序；同分用 uid 兜底，保证结果稳定（否则每次查询顺序会跳）
        hits.sort(key=lambda h: (-h.score, h.uid))
        return hits[:max(0, int(top_n))]


# ====================================================================
# 命中片段（高亮区间）
# ====================================================================
SNIPPET_RADIUS = 26          # 命中点前后各取多少字符


def make_snippet(text, terms, radius: int = SNIPPET_RADIUS):
    """定位命中项，返回 ``(片段, [(start, end)], [命中的检索项])``

    - 片段是**原文的子串**（不加工，调用方按 spans 自行高亮）
    - `spans` 下标相对片段起点（不是原文）
    - 优先选**第一个命中的位置**做锚点（用户看到的上下文与查询最相关）
    - 命中项按「在片段里出现的位置」排序；同一位置的长项优先（
      「月报」比「报」更具体，高亮时应该盖住短的）
    """
    text = text if isinstance(text, str) else str(text or "")
    if not text:
        return "", [], []

    lowered = _DEFAULT.normalize(text)
    found = []                       # (pos, term)
    for term in terms or ():
        start = lowered.find(term)
        if start >= 0:
            found.append((start, term))
    if not found:
        # 查询项都没在原文字面出现（bigram 交叉命中等）→ 退回首段
        head = text[:radius * 2]
        return head, [], []

    found.sort(key=lambda item: (item[0], -len(item[1])))
    anchor = found[0][0]
    lo = max(0, anchor - radius)
    hi = min(len(text), anchor + radius + max(len(t) for _p, t in found))

    snippet = text[lo:hi]
    spans = []
    for pos, term in found:
        s, e = pos - lo, pos - lo + len(term)
        if e <= 0 or s >= len(snippet):
            continue
        s, e = max(0, s), min(len(snippet), e)
        spans.append((s, e))
    spans = _merge_spans(spans)

    matched = []
    for _pos, term in found:
        if term not in matched:
            matched.append(term)
    return snippet, spans, matched


def _merge_spans(spans):
    """合并重叠/相邻区间（高亮时相邻区间合成一段，避免切出空段）"""
    if not spans:
        return []
    spans = sorted(spans)
    out = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(pair) for pair in out]


def split_by_spans(text, spans):
    """按高亮区间把文本切成 ``[(片段, 是否命中), ...]``

    UI 用它把一段文本渲染成「普通 label + 高亮 label」交替——宿主 QLabel
    不渲染 Markdown，所以高亮只能靠拼控件实现，这一步就交给核心层算好。
    """
    parts, cursor = [], 0
    for start, end in spans or ():
        start, end = max(0, start), min(len(text), end)
        if start > cursor:
            parts.append((text[cursor:start], False))
        if end > start:
            parts.append((text[start:end], True))
        cursor = max(cursor, end)
    if cursor < len(text):
        parts.append((text[cursor:], False))
    return parts
