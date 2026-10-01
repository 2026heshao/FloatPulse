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
    QFrame, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWidget,
)

from src.controls import IconButton
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
    "asset": "素材",
}

# 结果标题行锚点用的 URL scheme。用自定义 scheme 而不是 http/file：
# 宿主 QTextBrowser 关掉 openExternalLinks 后只发 anchorClicked，
# 不会被系统浏览器抢走。
RESULT_SCHEME = "fp-result"

# 富文本里的强调色**不能靠 QSS**：QTextBrowser 的 setHtml 只认行内样式，
# 所以颜色必须由插件自己按主题注入。取不到主题色时的兜底值。
_ACCENT_FALLBACK = {"light": "#27787A", "dark": "#6FFFE9"}
DEFAULT_THEME = "light"


def accent_for(theme) -> str:
    """当前主题下「既当链接色又当高亮色」的强调色。

    为什么复用宿主的 ``$secondary_text``（次按钮文字色）：那个 token 的选型
    标准正是「浅底和深底都要读得清」——light 压深到 #27787A（白底约 4.6:1），
    dark 直接用 #6FFFE9（深底 14.7:1）。早期版本在这里写死了 #0a7d7b，
    浅色主题下没问题，**深色主题下结果标题几乎看不见**（实测截图确认）。
    """
    theme = theme if theme in _ACCENT_FALLBACK else DEFAULT_THEME
    try:
        from src.theme import get_colors
        color = (get_colors(theme) or {}).get("secondary_text")
        if isinstance(color, str) and color.startswith("#") \
                and len(color) in (4, 7):
            return color
    except Exception:                             # noqa: BLE001 - 取不到就用兜底
        pass
    return _ACCENT_FALLBACK[theme]


def theme_of(host) -> str:
    """从宿主窗口读当前主题名；读不到按 light 处理（不抛）"""
    try:
        theme = getattr(host, "current_theme", None)
    except Exception:                             # noqa: BLE001
        theme = None
    return theme if theme in ("light", "dark") else DEFAULT_THEME


def _host_window(ctx):
    """取宿主窗口；没有 / 抛异常都返回 None（配色退化，不影响功能）"""
    getter = getattr(ctx, "parent_window", None) if ctx is not None else None
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:                             # noqa: BLE001
        return None


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

    # 素材：宿主只给元数据（没有文件路径），所以按**文件名**索引，
    # 与并入前的宿主全库搜索口径一致——用户找的是「那个 Excel」，不是内容
    for asset in (data.assets() if hasattr(data, "assets") else []) or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("original_name") or "").strip()
        if not name:
            continue
        docs.append((f"asset:{asset.get('asset_id')}", "asset",
                     f"素材 · {name}{' · 图片' if asset.get('is_image') else ''}",
                     name))

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
def render_results_html(hits, query, accent=None):
    """把命中列表渲染成一段 HTML。

    - **一律 html.escape**：命中片段是用户自己的原文，里面必然有 ``<``
      ``&``（贴代码是常态），不转义会被 QTextBrowser 当标签吃掉
    - 命中区间用 ``<span>`` 着色加粗；用 ``core.split_by_spans`` 算区间，
      核心层只给下标、不知道渲染方式（控件换了也不用改核心）
    - **每条结果的标题行是一个锚点** ``fp-result:<下标>``：点它就跳转到
      对应面板（下标由渲染顺序决定，调用方必须缓存同一份 hits 列表来反查）
    - ⚠ 锚点标签是**我们自己生成的**，不进 html.escape；用户文本（标题、
      片段）照旧全部转义——两者混在一起时最容易漏掉一处
    - ``accent`` 是主题相关的强调色（QTextBrowser 不认 QSS，只能行内注入）；
      不传则用 light 主题的值，保证纯函数调用方（测试）行为稳定
    - 分数也显示出来（保留 2 位）：调参和判断「为什么这条排前」时有用
    """
    if not hits:
        return ""
    color = accent or _ACCENT_FALLBACK[DEFAULT_THEME]
    blocks = []
    for i, hit in enumerate(hits):
        kind = KIND_LABEL.get(hit.kind, hit.kind or "其它")
        head = html.escape(f"[{kind}] {hit.title}")
        pieces = []
        for text, is_hit in core.split_by_spans(hit.snippet, hit.spans):
            esc = html.escape(text)
            pieces.append(f'<span class="hit">{esc}</span>' if is_hit
                          else esc)
        blocks.append(
            f'<p class="head">'
            f'<a class="res" href="{RESULT_SCHEME}:{i}">{head}</a>'
            f'<span class="score">　{hit.score:.2f}</span></p>'
            f'<p class="body">…{"".join(pieces)}…</p>')
    return ("<style>"
            ".head{font-weight:600;margin:10px 0 2px}"
            ".score{font-size:11px;opacity:.55}"
            ".body{margin:0 0 8px;line-height:1.55}"
            f".hit{{font-weight:700;color:{color}}}"
            f".res{{color:{color};text-decoration:underline}}"
            "</style>" + "".join(blocks))


def parse_result_anchor(url_text):
    """``fp-result:<下标>`` → 下标；不是本插件的锚点返回 ``None``。

    单独抽出来是因为这里错起来是静默的：解析歪一点就会跳到**另一条**结果上，
    用户只会觉得「点了没反应 / 点错了」，不会报错。纯函数便于直接钉住。
    """
    prefix = f"{RESULT_SCHEME}:"
    text = url_text if isinstance(url_text, str) else str(url_text or "")
    if not text.startswith(prefix):
        return None
    try:
        idx = int(text[len(prefix):])
    except ValueError:
        return None
    return idx if idx >= 0 else None


def pick_jump_keyword(matched, fallback=""):
    """跳转时带进目标面板搜索框的关键词。

    目标面板（碎片 / 笔记）的搜索框是**原样子串过滤**：拿整条查询
    「月报 归档」去过滤，什么都匹配不上，表现为「切过去列表是空的」。
    所以优先取**命中的检索项里最长的一个**——``hit.matched`` 里的项由
    ``make_snippet`` 保证在原文里字面出现过，拿它过滤必有结果。
    """
    items = [m for m in (matched or ()) if isinstance(m, str) and m]
    return max(items, key=len) if items else str(fallback or "").strip()


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
        # 渲染顺序的命中列表：锚点 href 里存的是它的下标，点击时靠它反查
        self._hits = []
        self._build_ui()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run_search)
        self.index_ready.connect(self._on_index_ready)
        # 富文本的强调色要跟着主题走（QSS 管不到 QTextBrowser 内部），
        # 所以订阅宿主的 theme_changed，切换后立即重渲染已有结果
        self._accent = accent_for(theme_of(_host_window(ctx)))
        theme_sig = getattr(_host_window(ctx), "theme_changed", None)
        if theme_sig is not None and hasattr(theme_sig, "connect"):
            try:
                theme_sig.connect(self._on_theme_changed)
            except Exception:                     # noqa: BLE001 - 订阅失败只影响配色
                pass

    def _on_theme_changed(self, *_args):
        """主题切换 → 换强调色并重渲染（不重建索引，数据没变）"""
        self._accent = accent_for(theme_of(_host_window(self._ctx)))
        if self._input.text().strip():
            self._run_search()

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
            "搜知识库 / 笔记 / 碎片 / 任务 / 素材；"
            "点结果标题跳转到对应面板"))
        head.addStretch(1)
        root.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("输入关键词，中文英文都行（支持多词，按相关度排序）")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._run_search)
        bar.addWidget(self._input, 1)

        self._rebuild_btn = IconButton("refresh", text="重建索引", icon_size=14,
                                       object_name="secondaryBtn",
                                       tooltip="数据变了（新记了笔记等）后点一下即可搜到最新内容")
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
        self._view.setOpenExternalLinks(False)   # 自定义 scheme 只发信号
        self._view.setOpenLinks(False)           # 不让浏览器自己导航
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.anchorClicked.connect(self._on_anchor)
        box_lay.addWidget(self._view)
        root.addWidget(box, 1)

        self._empty = QLabel("输入关键词开始搜索\n\n"
                             "· 支持中文短语与英文单词混合，例如「月报 归档」\n"
                             "· 命中处加粗着色，右边的数字是相关度分数\n"
                             "· 点结果标题跳转到对应面板（知识库会定位到那一段）")
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
            self._hits = []
            self._view.setHtml("")
            self._empty.setVisible(True)
            return
        try:
            hits = self._index.search(query, top_n=MAX_RESULTS)
        except Exception as exc:                  # noqa: BLE001
            self._hits = []
            self._stat.setText(f"⚠ 检索失败：{exc!r}")
            return
        # ⚠ 必须与 render_results_html 用的是**同一个列表**：锚点 href 里存的
        # 是它在列表里的下标，渲染完再改列表就会点错行
        self._hits = hits
        self._empty.setVisible(not hits)
        self._view.setHtml(render_results_html(hits, query, self._accent))
        if hits:
            self._stat.setText(f"命中 {len(hits)} 条（最多显示 {MAX_RESULTS} 条）"
                               f" ｜ 索引 {self._docs} 条")
        else:
            self._stat.setText(f"没有匹配「{query}」的内容 ｜ 索引 {self._docs} 条")

    # ---------------- 跳转 ----------------
    def _on_anchor(self, url):
        """点结果标题 → 跳转到对应面板（QTextBrowser 的锚点回调）。

        宿主侧只暴露一个**公开入口** ``show_search_result(kind, keyword, num)``
        （与 ``show_plugin_page`` 同款约定）；宿主没有这个入口时提示一句，
        不让点击静默失败。
        """
        text = url.toString() if hasattr(url, "toString") else str(url)
        idx = parse_result_anchor(text)
        if idx is None or idx >= len(self._hits):
            return
        self._jump_to(self._hits[idx])

    def _jump_to(self, hit):
        """把一条命中转成宿主跳转调用"""
        host = self._ctx.parent_window()
        jump = getattr(host, "show_search_result", None) if host else None
        if not callable(jump):
            self._toast("当前宿主版本不支持结果跳转，请升级后再试")
            return
        num = None
        if hit.kind == "knowledge":
            # uid 形如 "knowledge:3"——知识库是位置型数据源，段号就是它的地址
            try:
                num = int(str(hit.uid).split(":", 1)[1])
            except (IndexError, ValueError):
                num = None
        keyword = pick_jump_keyword(hit.matched, self._input.text())
        try:
            if not jump(hit.kind, keyword, num):
                self._toast("这条结果没有对应的面板，无法跳转")
        except Exception as exc:                  # noqa: BLE001
            self._ctx.logger.warning(f"[{PLUGIN_ID}] 结果跳转失败：{exc!r}")
            self._toast("跳转失败，详见日志")

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
