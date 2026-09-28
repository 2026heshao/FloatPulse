# -*- coding: utf-8 -*-
"""
====================================================================
站内搜索  -  FloatPulse 外置插件（kb-search）
====================================================================
在产品定位内做一件事：**把散在四个地方的文字一次搜出来**。

  知识库段落 / 笔记 / 碎片 / 任务  →  一个框，一次输入，按相关度排序

与「全局文件搜索」划清界限（那是明确不做的：交给 Everything / Flow
Launcher）：本插件**只索引宿主自己的数据**，不碰文件系统、不建全盘索引。

技术选型（细节见 kb_search_core.py 的模块头）：
  - 依赖白名单只有 PyQt6 + 标准库 → jieba / whoosh 装进来也用不了，
    所以中文分词 + 倒排索引 + BM25 全部自己实现（纯 stdlib）
  - 索引项 = 分词词 ∪ 字符 bigram，索引侧与查询侧同构 → 词典外的
    专有名词也能召回
  - 只读：manifest 的 capabilities 是空数组，插件不写任何数据

界面的两个务实决定：
  1. **索引是懒建的 + 可手动重建**：数据变了（新记了笔记）需要重建才能
     搜到；搜索页每次切入时**自动重建**（数据量小，几千条毫秒级），
     另给一个「重建索引」按钮兜底。
  2. **结果用 QTextBrowser 渲染 HTML 而不是 QLabel 拼控件**：宿主 QLabel
     不渲染 Markdown，高亮只能靠拼多个控件或自绘；QTextBrowser 支持
     HTML 子集，一个控件就能把「命中处加粗着色」做对，代码量也最小。
====================================================================
"""

import html
import importlib.util
import os
import sys

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QTextBrowser, QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin
from src.plugin_ui import make_hint_label

# ---- 同目录纯逻辑模块 ----
# 不能直接 `import kb_search_core`：加载器只把 entry 文件按路径加载，
# 插件目录并不在 sys.path 上。所以显式按文件路径加载，模块名加插件前缀
# 避免与宿主/其它插件的模块撞名。
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str):
    mod_name = f"floatpulse_plugin_kb_search_{name}"
    path = os.path.join(_HERE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load_sibling("kb_search_core")

PLUGIN_ID = "kb-search"
PAGE_KEY = f"plugin:{PLUGIN_ID}"
SEARCH_DEBOUNCE_MS = 220            # 输入去抖（与宿主搜索同量级）
MAX_RESULTS = 60
PREVIEW_CHARS = 4000                # 单条入库正文上限（防一条长文拖慢索引）

# 数据源标识 → 展示名
KIND_LABEL = {
    "knowledge": "知识库",
    "note": "笔记",
    "fragment": "碎片",
    "task": "任务",
}


# ====================================================================
# 数据采集：把四类宿主数据摊成 (uid, kind, title, text)
# ====================================================================
def collect_documents(data):
    """从只读快照里取出所有可检索文档。

    每个数据源的字段名不同、还都可能缺（宿主结构调整 / 空数组），所以
    一律 ``.get`` 取 + 兜底，**一条坏数据只跳过自己**，不让整轮采集失败。
    """
    docs = []

    def _text(*parts):
        return "\n".join(str(p).strip() for p in parts if p)

    # 知识库：唯一「位置型」数据源，uid 用段落编号（与面板编号口径一致）
    for item in (data.knowledge() if hasattr(data, "knowledge") else []) or []:
        if not isinstance(item, dict):
            continue
        num = item.get("num")
        body = item.get("text") or item.get("preview") or ""
        if not body:
            continue
        docs.append((f"knowledge:{num}", "knowledge",
                     f"知识库 · 第 {num} 段", body))

    for note in (data.notes() if hasattr(data, "notes") else []) or []:
        if not isinstance(note, dict):
            continue
        title = note.get("title") or "（无标题）"
        body = _text(note.get("content"))
        if not body and title == "（无标题）":
            continue
        docs.append((f"note:{note.get('note_id')}", "note",
                     f"笔记 · {title}", _text(title, body)))

    for frag in (data.fragments() if hasattr(data, "fragments") else []) or []:
        if not isinstance(frag, dict):
            continue
        body = _text(frag.get("content"))
        if not body:
            continue
        src = frag.get("source") or ""
        docs.append((f"fragment:{frag.get('fragment_id')}", "fragment",
                     f"碎片 · {src or '未标来源'}", body))

    for task in (data.tasks() if hasattr(data, "tasks") else []) or []:
        if not isinstance(task, dict):
            continue
        title = task.get("title") or ""
        if not title:
            continue
        due = str(task.get("deadline") or "")[:10]
        # 截止日也进正文：这样「2026-09」一次捞出当月该交的一批任务
        body = _text(title, task.get("note"), due)
        suffix = f" · 截止 {due}" if due else ""
        if task.get("done"):
            suffix += " · 已完成"
        docs.append((f"task:{task.get('task_id')}", "task",
                     f"任务 · {title}{suffix}", body))

    # 单条正文过长时截断：索引成本与噪声都主要来自这里
    return [(uid, kind, title, text[:PREVIEW_CHARS])
            for uid, kind, title, text in docs]


def build_index(docs, index=None):
    """（重）建索引；传入已有 index 时先清空再填（复用对象）"""
    idx = index if index is not None else core.SearchIndex()
    idx.clear()
    for uid, kind, title, text in docs:
        idx.add(uid, text, kind=kind, title=title)
    idx.finalize()
    return idx


# ====================================================================
# 结果渲染（HTML 高亮）
# ====================================================================
def render_results_html(hits, query):
    """把命中列表渲染成一段 HTML。

    - **一律 html.escape**：命中片段是用户自己的原文，里面必然有 ``<``
      ``&``（贴代码是常态），不转义会被 QTextBrowser 当标签吃掉
    - 命中区间用 ``<span>`` 着色加粗；用 ``core.split_by_spans`` 算区间，
      核心层只给下标、不知道渲染方式（控件换了也不用改核心）
    - 分数也显示出来（保留 2 位）：调参和判断「为什么这条排前」时有用
    """
    if not hits:
        return ""
    blocks = []
    for hit in hits:
        kind = KIND_LABEL.get(hit.kind, hit.kind or "其它")
        head = html.escape(f"[{kind}] {hit.title}")
        pieces = []
        for text, is_hit in core.split_by_spans(hit.snippet, hit.spans):
            esc = html.escape(text)
            pieces.append(f'<span class="hit">{esc}</span>' if is_hit
                          else esc)
        blocks.append(
            f'<p class="head">{head}'
            f'<span class="score">　{hit.score:.2f}</span></p>'
            f'<p class="body">…{"".join(pieces)}…</p>')
    return ("<style>"
            ".head{font-weight:600;margin:10px 0 2px}"
            ".score{font-size:11px;opacity:.55}"
            ".body{margin:0 0 8px;line-height:1.55}"
            ".hit{font-weight:700;color:#0a7d7b}"
            ".empty{opacity:.6}"
            "</style>" + "".join(blocks))


# ====================================================================
# 动作
# ====================================================================
class OpenSearchAction(BallAction):
    """打开站内搜索页（在宿主主窗口里）；宿主无页面机制时退回提示弹窗"""

    id = f"{PLUGIN_ID}.open"
    title = "🔍 站内搜索"

    def run(self, ctx):
        # 热键路径下 run() 在原生事件过滤器里被调用，UI 操作必须延后一轮
        # 事件循环（项目铁律，见插件开发说明第 12 节）
        QTimer.singleShot(0, lambda: self._go(ctx))

    def _go(self, ctx):
        host = ctx.parent_window()
        show = getattr(host, "show_plugin_page", None) if host else None
        if callable(show):
            try:
                if show(PAGE_KEY):
                    return
            except Exception as exc:              # noqa: BLE001
                ctx.logger.warning(f"[{PLUGIN_ID}] 打开插件页失败：{exc!r}")
        try:
            ctx.show_toast("当前宿主版本不支持插件页面，站内搜索暂不可用")
        except Exception:                         # noqa: BLE001
            pass


# ====================================================================
# 页面
# ====================================================================
class SearchPage(QWidget):
    """一个输入框 + 结果区；数据在切入页面时重建索引"""

    index_ready = pyqtSignal(int)                 # 参数：文档条数

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self.setObjectName("pluginPage")
        self._index = core.SearchIndex()
        self._docs = 0
        self._build_ui()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run_search)
        self.index_ready.connect(self._on_index_ready)

    # ---------------- 界面 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("🔍 站内搜索")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addWidget(make_hint_label(
            "搜知识库 / 笔记 / 碎片 / 任务的正文；只索引本程序内的数据，不扫硬盘"))
        head.addStretch(1)
        root.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("输入关键词，中文英文都行（支持多词，按相关度排序）")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._run_search)
        bar.addWidget(self._input, 1)

        self._rebuild_btn = QPushButton("🔄 重建索引")
        self._rebuild_btn.setObjectName("secondaryBtn")
        self._rebuild_btn.setToolTip("数据变了（新记了笔记等）后点一下即可搜到最新内容")
        self._rebuild_btn.clicked.connect(lambda: self.rebuild_index(verbose=True))
        bar.addWidget(self._rebuild_btn)
        root.addLayout(bar)

        self._stat = QLabel("正在建立索引…")
        self._stat.setObjectName("hintLabel")
        root.addWidget(self._stat)

        box = QFrame()
        box.setObjectName("glassCard")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(10, 8, 10, 8)
        self._view = QTextBrowser()
        self._view.setObjectName("kbSearchResults")
        self._view.setOpenExternalLinks(False)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        box_lay.addWidget(self._view)
        root.addWidget(box, 1)

        self._empty = QLabel("输入关键词开始搜索\n\n"
                             "· 支持中文短语与英文单词混合，例如「月报 归档」\n"
                             "· 命中处加粗着色，括号里是相关度分数")
        self._empty.setObjectName("pluginEmptyHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        root.addWidget(self._empty)

    # ---------------- 索引 ----------------
    def rebuild_index(self, verbose=False):
        """重扫宿主数据并重建索引（切页 / 点按钮时调）

        顺序有讲究：**先建好索引再发信号**——`_on_index_ready` 会去读
        `term_count`，反过来的话它读到的是上一轮（或空）的统计。
        """
        try:
            docs = collect_documents(self._ctx.data)
        except Exception as exc:                  # noqa: BLE001 - 采集失败不崩页面
            self._stat.setText(f"⚠ 读取数据失败：{exc!r}")
            self._ctx.logger.warning(f"[{PLUGIN_ID}] 采集文档失败：{exc!r}")
            return
        self._docs = len(docs)
        build_index(docs, self._index)
        self.index_ready.emit(len(docs))
        if verbose:
            self._toast(f"✅ 索引已重建（{len(docs)} 条）")

    def _on_index_ready(self, count):
        self._docs = count
        self._stat.setText(f"已索引 {count} 条 ｜ 检索项 {self._index.term_count} 个"
                           f" ｜ 输入即搜")

    # ---------------- 搜索 ----------------
    def _on_text_changed(self, _text):
        self._debounce.start()                    # 去抖，避免每敲一个字搜一次

    def _run_search(self):
        query = self._input.text().strip()
        if not query:
            self._view.setHtml("")
            self._empty.setVisible(True)
            return
        try:
            hits = self._index.search(query, top_n=MAX_RESULTS)
        except Exception as exc:                  # noqa: BLE001
            self._stat.setText(f"⚠ 检索失败：{exc!r}")
            return
        self._empty.setVisible(not hits)
        self._view.setHtml(render_results_html(hits, query))
        if hits:
            self._stat.setText(f"命中 {len(hits)} 条（最多显示 {MAX_RESULTS} 条）"
                               f" ｜ 索引 {self._docs} 条")
        else:
            self._stat.setText(f"没有匹配「{query}」的内容 ｜ 索引 {self._docs} 条")

    def focus_input(self):
        self._input.setFocus()
        self._input.selectAll()

    def _toast(self, text, ms=2600):
        try:
            self._ctx.show_toast(text, ms)
        except Exception:                         # noqa: BLE001
            pass

    def showEvent(self, event):
        """每次切入都重建索引：数据随时会变（新笔记 / 改知识库），
        数据量小（几千条）重建成本可忽略，比做增量同步简单且不会错。"""
        super().showEvent(event)
        self.rebuild_index()
        QTimer.singleShot(0, self.focus_input)


# ====================================================================
# 插件
# ====================================================================
class KbSearchPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "站内搜索"
    version = "1.0.0"

    def create_actions(self, ctx):
        return [OpenSearchAction()]

    def create_page(self, ctx):
        return SearchPage(ctx)
