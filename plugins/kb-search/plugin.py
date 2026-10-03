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

v1.1.0 增强：
  - K1 范围过滤：搜索框下五个数据源勾选（默认全开=与旧版同路径），
    在全库分数算好后按范围排除 → 勾选即时生效、排序与不过滤一致
  - K2 跳转自愈：碎片/笔记跳转前先模拟目标页的子串过滤（与宿主面板
    同口径），必空（bigram 交叉召回）则只切页显示全部并 toast 说明

v1.3.0 碎片行重构（2026-10-02）：
  - 碎片行 = 3px 类别色条（CATEGORY_TOKENS 真相源，与碎片面板/小卡片
    同源）+ meta 行（碎片 · 来源 · 类别 · 时间）+ 正文 —— kind 不再在
    标题里出现两次，快照里有却被丢弃的 category / created_at 用起来了
  - meta 行颜色用 text_placeholder（时间戳/提示专用 token，11px）
  - 正文 white-space:pre-wrap 保留换行缩进；省略号按需（snippet 是
    原文真子串才加，两侧独立判断）—— 旧版恒加 "…"，82% 的碎片整条
    放得下也被谎报截断
  - 分数不再占行内位置（旧 .score 的 opacity 在 QTextBrowser 静默失效，
    分数混进标题）→ 悬停结果行时 QToolTip 显示
  - 超长内容尾部「展开全文」锚点（fp-expand:<下标>）：点击行内展开/
    收起，与「点标题跳转」走两个互不解析的 scheme，不抢语义
  - 来源只在 ≠ 剪贴板 时显示（97% 的碎片来自剪贴板，恒显示是噪音）
  - 非碎片行（知识库/笔记/任务/素材）head 行与 v1.2 同构
====================================================================
"""

import html
import importlib.util
import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QTextBrowser, QToolTip, QVBoxLayout, QWidget,
)

from src.controls import IconButton, SmoothButton
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

# 「展开全文」锚点用的 scheme：点击是行内展开/收起（改渲染状态），不是
# 跳转，所以必须与 RESULT_SCHEME 区分开 —— 两个 scheme 各有独立解析
# 函数，互相解析返回 None（钉在测试里），点了展开绝不会被当成跳转。
EXPAND_SCHEME = "fp-expand"

# meta 行不显示的默认来源：97% 的碎片来自剪贴板，恒显示是噪音
SOURCE_DEFAULT = "剪贴板"

# ---------------- 搜索历史（K3，v1.2.0） ----------------
# 最近搜索词存插件私有目录（重启不丢）；输入框为空时在下方显示「最近」行。
# 上限刻意收紧：历史是「快速重搜」不是「搜索日志」，防文件无限膨胀。
HISTORY_FILE = "search_history.json"
HISTORY_LIMIT = 10         # 最多保留的搜索词（最新在前，丢最旧）
HISTORY_CHARS = 60         # 单条搜索词的最大字符数

# 富文本里的强调色**不能靠 QSS**：QTextBrowser 的 setHtml 只认行内样式，
# 所以颜色必须由插件自己按主题注入。取不到主题色时的兜底值
# （UI 重构 01 起镜像新版 secondary_text：light #0C5A47 白底 8.2:1，
#   dark #5DCAA5 深底约 7.6:1）。
_ACCENT_FALLBACK = {"light": "#0C5A47", "dark": "#5DCAA5"}
DEFAULT_THEME = "light"

# ---------------- 碎片类别（真相源在宿主，独立分发走字面量兜底）-----
# try-import 与 accent_for 同款约定：宿主在时用 src.fragment_classifier
# 的 CATEGORY_TOKENS / CATEGORY_LABELS（碎片面板与小卡片共用的那张表，
# 在这里再抄一份必然漂移）；import 不到（插件独立分发）才走字面量。
# ⚠ 两份字面量由 test_kb_search_core 用 AST 钉死与 theme.THEMES 同源，
#   宿主改主题色而插件忘同步会直接红灯。
try:
    from src.fragment_classifier import (
        CATEGORY_LABELS as _CAT_LABELS, CATEGORY_TOKENS as _CAT_TOKENS)
except Exception:                             # noqa: BLE001 - 独立分发兜底
    _CAT_TOKENS = {"link": "link", "code": "primary", "path": "warn",
                   "command": "danger", "text": "text_secondary"}
    _CAT_LABELS = {"text": "普通文本", "link": "链接", "code": "代码",
                   "path": "路径", "command": "命令"}

# 类别色条用的主题 token → 色值兜底（light/dark 各一套，与 theme.py 对应
# token 的字面量逐值一致；AST 护栏见 test_fallback_literal_matches_theme）
_CATEGORY_FALLBACK = {
    "light": {"link": "#185FA5", "primary": "#0F6E56", "warn": "#854F0B",
              "danger": "#A32D2D", "text_secondary": "#5F5E5A",
              "text": "#2C2C2A", "text_placeholder": "#6E6D67"},
    "dark":  {"link": "#85B7EB", "primary": "#5DCAA5", "warn": "#EF9F27",
              "danger": "#F09595", "text_secondary": "#A8ADA5",
              "text": "#E9EAE7", "text_placeholder": "#8A8F88"},
}

# ---------------- 统一时间函数（src/time_format.py）----------------
# 笔记面板与搜索结果 meta 行共用同一份实现；import 不到时走
# _fmt_time_fallback（内嵌同款，行为由 test 钉住同源）。
try:
    from src.time_format import format_relative_time as _fmt_time
except Exception:                             # noqa: BLE001 - 独立分发兜底
    _fmt_time = None


def accent_for(theme) -> str:
    """当前主题下「既当链接色又当高亮色」的强调色。

    为什么复用宿主的 ``$secondary_text``（次按钮文字色）：那个 token 的选型
    标准正是「浅底和深底都要读得清」——light 用压深的 #0C5A47（白底 8.2:1），
    dark 用 #5DCAA5（深底约 7.6:1）。早期版本在这里写死了 #0a7d7b，
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


def category_color_for(category, theme=DEFAULT_THEME, colors=None) -> str:
    """碎片内容类别 → 色条色值（渲染端唯一取色入口）。

    ``colors`` 是宿主主题色 dict（SearchPage 每次渲染时取），取得到就
    用真值；取不到（独立分发 / 调用方省略）走 _CATEGORY_FALLBACK 字面量。
    未知类别返回 ""（渲染端画空占位列对齐，不画色条）。
    """
    token = _CAT_TOKENS.get(category or "", "")
    if not token:
        return ""
    if isinstance(colors, dict):
        value = colors.get(token)
        if isinstance(value, str) and value.startswith("#"):
            return value
    table = _CATEGORY_FALLBACK.get(
        theme if theme in _CATEGORY_FALLBACK else DEFAULT_THEME, {})
    return table.get(token, "")


def _fmt_time_fallback(ts) -> str:
    """内嵌时间格式化：只在 import 不到 src.time_format 时被用。

    与宿主 time_format.format_relative_time 七段口径逐段一致
    （test_time_fallback_matches_host 钉行为同源）。
    """
    from datetime import datetime
    if not ts:
        return "未知时间"
    try:
        dt = datetime.strptime(str(ts).strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return ts
    secs = (datetime.now() - dt).total_seconds()
    if secs < 0:
        return ts                      # 时间在当前之后（系统时钟被改）→ 原样
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    if secs < 86400 * 2:
        return f"昨天 {dt.strftime('%H:%M')}"
    if secs < 86400 * 7:
        return f"{int(secs // 86400)} 天前"
    if dt.year == datetime.now().year:
        return dt.strftime("%m-%d %H:%M")
    return ts


def meta_time_for(ts) -> str:
    """meta 行的时间段文案：宿主在时走统一实现，独立分发走内嵌同款"""
    fn = _fmt_time or _fmt_time_fallback
    try:
        return fn(ts)
    except Exception:                         # noqa: BLE001 - 时间段不许炸渲染
        return str(ts or "")


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


# ---------------- 搜索历史存档（K3） ----------------
def sanitize_history(raw) -> list:
    """搜索历史消毒：非字符串/空白丢弃、截断、去重（保先出现）、上限裁剪"""
    items, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        text = str(item or "").strip()[:HISTORY_CHARS]
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= HISTORY_LIMIT:
            break
    return items


def load_history(ctx) -> list:
    """读搜索历史；文件缺失/写坏降级为空表，原文件保留供手工抢救"""
    data_dir = getattr(ctx, "data_dir", None)
    path = os.path.join(data_dir, HISTORY_FILE) if data_dir else ""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sanitize_history(json.load(f))
        except (OSError, ValueError):
            pass
    return []


def save_history(ctx, items) -> bool:
    """写搜索历史（临时文件 + os.replace 原子替换）；失败只返回 False"""
    data_dir = getattr(ctx, "data_dir", None)
    path = os.path.join(data_dir, HISTORY_FILE) if data_dir else ""
    if not path:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(sanitize_history(items), f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


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


def collect_fragment_meta(data):
    """碎片元数据旁路：``fragment:<id>`` → {source, category, created_at}。

    为什么走旁路而不是改 collect_documents / Hit：Hit 是 ``__slots__``
    纯数据类（附加属性会 AttributeError），collect_documents 的四元组
    形状被索引与多处测试钉死；meta 只影响显示，采集失败返回空表 ——
    显示降级为「无 meta 行 / 无色条」（正文照搜），不能拖垮搜索页。
    """
    out = {}
    try:
        frags = data.fragments() or []
    except Exception:                         # noqa: BLE001
        return out
    for frag in frags:
        if not isinstance(frag, dict):
            continue
        fid = frag.get("fragment_id")
        if fid is None:
            continue
        out[f"fragment:{fid}"] = {
            "source": str(frag.get("source") or ""),
            "category": str(frag.get("category") or ""),
            "created_at": str(frag.get("created_at") or ""),
        }
    return out


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
def render_results_html(hits, query, accent=None, metas=None, colors=None,
                        expanded=None):
    """把命中列表渲染成一段 HTML。

    - **一律 html.escape**：命中片段是用户自己的原文，里面必然有 ``<``
      ``&``（贴代码是常态），不转义会被 QTextBrowser 当标签吃掉
    - 命中区间用 ``<span>`` 着色加粗；用 ``core.split_by_spans`` 算区间，
      核心层只给下标、不知道渲染方式（控件换了也不用改核心）
    - **每条结果是一个锚点** ``fp-result:<下标>``：点它就跳转到对应面板
      （下标由渲染顺序决定，调用方必须缓存同一份 hits 列表来反查）
    - ⚠ 锚点标签是**我们自己生成的**，不进 html.escape；用户文本（标题、
      片段）照旧全部转义——两者混在一起时最容易漏掉一处
    - ``accent`` 是主题相关的强调色（QTextBrowser 不认 QSS，只能行内注入）；
      不传则用 light 主题的值，保证纯函数调用方（测试）行为稳定
    - v1.3.0：碎片行走 ``_render_fragment_row``（类别色条 + meta 行 +
      pre-wrap 正文），其余数据源 head 行与 v1.2 同构。
      ``metas`` 是 ``collect_fragment_meta`` 的返回值（碎片元数据旁路），
      ``colors`` 是宿主主题色 dict，``expanded`` 是处于展开态的下标集合；
      三者都可省略 —— 省略时碎片行退化为「无色条 / 无 meta 信息段」，
      调用向后兼容
    - 分数不再渲染进 HTML（旧版 .score 的 opacity 在 QTextBrowser 静默
      失效，分数混进标题）：悬停结果行由 SearchPage._on_link_hovered
      用 QToolTip 显示
    """
    if not hits:
        return ""
    color = accent or _ACCENT_FALLBACK[DEFAULT_THEME]
    mc = _token_color("text_placeholder", colors)
    tc = _token_color("text", colors)
    metas = metas if isinstance(metas, dict) else {}
    expanded = expanded if isinstance(expanded, (set, frozenset)) else set()
    blocks = []
    for i, hit in enumerate(hits):
        if hit.kind == "fragment":
            blocks.append(_render_fragment_row(
                i, hit, metas.get(hit.uid), mc, tc, colors, expanded))
            continue
        kind = KIND_LABEL.get(hit.kind, hit.kind or "其它")
        head = html.escape(f"[{kind}] {hit.title}")
        blocks.append(
            f'<p class="head">'
            f'<a class="res" href="{RESULT_SCHEME}:{i}">{head}</a></p>'
            f'<p class="body">{_body_html(hit.snippet, hit.spans, hit.text)}'
            f'</p>')
    return ("<style>"
            ".head{font-weight:600;margin:10px 0 2px}"
            ".body{margin:0 0 8px;line-height:1.55}"
            f".hit{{font-weight:700;color:{color}}}"
            f".res{{color:{color};text-decoration:underline}}"
            f".meta{{color:{mc};font-size:11px;margin:0 0 2px}}"
            f".fbody{{margin:0;line-height:1.5;color:{tc};"
            "white-space:pre-wrap}"
            f".exp{{color:{mc};font-size:11px;margin:2px 0 0}}"
            "a.plain{color:inherit;text-decoration:none}"
            f"a.explink{{color:{mc};text-decoration:underline}}"
            "</style>" + "".join(blocks))


def _token_color(token, colors) -> str:
    """主题 token → 色值：宿主色表优先，取不到走字面量兜底（不抛）"""
    if isinstance(colors, dict):
        value = colors.get(token)
        if isinstance(value, str) and value.startswith("#"):
            return value
    return _CATEGORY_FALLBACK.get(DEFAULT_THEME, {}).get(token, "")


def _body_html(snippet, spans, full_text=""):
    """正文 HTML：命中处高亮 + **按需**省略号（两侧独立判断）。

    旧版恒加 "…"：82% 的碎片整条放得下也被谎报截断（"…Python…"）。
    现在只有 snippet 真是 full_text 的中间截断时才在对应侧加省略号。
    """
    pieces = "".join(
        f'<span class="hit">{html.escape(text)}</span>' if is_hit
        else html.escape(text)
        for text, is_hit in core.split_by_spans(snippet, spans))
    full = full_text if isinstance(full_text, str) else ""
    lead = "…" if full and not full.startswith(snippet) else ""
    tail = "…" if full and not full.endswith(snippet) else ""
    return f"{lead}{pieces}{tail}"


def _render_fragment_row(index, hit, meta, mc, tc, colors, expanded):
    """碎片行：3px 类别色条 + meta 行（碎片 · 来源 · 类别 · 时间）+ 正文。

    - 色条真相源是 CATEGORY_TOKENS（与碎片面板/小卡片同一张表）；
      meta 缺失或类别未知时画空占位列，与其他行保持对齐
    - meta 行与正文都包 fp-result 锚点（整行可点；``color:inherit``
      让链接不抢文字色）。「展开全文」是独立的 fp-expand 锚点
    - meta 行 kind 恒在第一位：混排结果里认出「这是碎片」全靠它
      （碎片行没有标题文字，色条是内容类别、不是数据源）
    - 展开态正文用全文重算高亮（``_full_text_with_spans``）
    """
    is_expanded = index in expanded
    if is_expanded:
        body_text, spans = _full_text_with_spans(hit)
        body = _body_html(body_text, spans, "")
    else:
        body = _body_html(hit.snippet, hit.spans, hit.text)
    meta = meta if isinstance(meta, dict) else {}
    bits = [KIND_LABEL.get(hit.kind, hit.kind or "其它")]
    source = str(meta.get("source") or "").strip()
    if source and source != SOURCE_DEFAULT:
        bits.append(source)
    label = _CAT_LABELS.get(meta.get("category") or "")
    if label:
        bits.append(label)
    created = str(meta.get("created_at") or "").strip()
    if created:
        # 空时间戳直接省略该段（「未知时间」是 notes_panel 列表的语义，
        # 搜索 meta 行缺失即省略，不占位）
        stamp = meta_time_for(created)
        if stamp:
            bits.append(stamp)
    link = f"{RESULT_SCHEME}:{index}"
    bar = category_color_for(meta.get("category"), colors=colors)
    parts = ['<table width="100%" cellspacing="0" cellpadding="0"><tr>',
             '<td width="3"></td>' if not bar
             else f'<td width="3" bgcolor="{bar}"></td>',
             '<td style="padding-left:8px">',
             f'<p class="meta"><a class="plain" href="{link}">'
             f'{html.escape(" · ".join(bits))}</a></p>',
             f'<p class="fbody"><a class="plain" href="{link}">'
             f'{body}</a></p>']
    if hit.text and hit.snippet != hit.text:
        lbl = "收起" if is_expanded else "展开全文"
        parts.append(f'<p class="exp"><a class="explink" '
                     f'href="{EXPAND_SCHEME}:{index}">{lbl}</a></p>')
    parts.append("</td></tr></table>")
    return "".join(parts)


def _full_text_with_spans(hit):
    """展开态：全文 + 命中高亮区间。

    复用 core.make_snippet 把 radius 拉到全长 —— 它的 lo/hi 钳制逻辑
    天然返回整段原文与全文坐标系下的 spans，UI 层不用重写定位。
    没有字面命中的（bigram 交叉召回）spans 为空，展示全文不高亮。
    """
    text = hit.text if isinstance(hit.text, str) else str(hit.text or "")
    try:
        snippet, spans, _matched = core.make_snippet(
            text, hit.matched, radius=max(len(text), 1))
    except Exception:                         # noqa: BLE001
        return text, []
    return snippet, spans


def _parse_scheme_anchor(url_text, scheme):
    """``<scheme>:<下标>`` → 下标；不是该 scheme 的锚点返回 ``None``。

    单独抽出来是因为这里错起来是静默的：解析歪一点就会跳到**另一条**
    结果上，用户只会觉得「点了没反应 / 点错了」，不会报错。纯函数便于
    直接钉住。
    """
    prefix = f"{scheme}:"
    text = url_text if isinstance(url_text, str) else str(url_text or "")
    if not text.startswith(prefix):
        return None
    try:
        idx = int(text[len(prefix):])
    except ValueError:
        return None
    return idx if idx >= 0 else None


def parse_result_anchor(url_text):
    """``fp-result:<下标>`` → 下标；不是本插件的锚点返回 ``None``"""
    return _parse_scheme_anchor(url_text, RESULT_SCHEME)


def parse_expand_anchor(url_text):
    """``fp-expand:<下标>`` → 下标；不是展开锚点返回 ``None``。

    与 fp-result **互不解析**（钉在测试里：两个解析函数对对方的 scheme
    都返回 None）—— 解析串了就会「点了展开却跳走了」，同样是静默错。
    """
    return _parse_scheme_anchor(url_text, EXPAND_SCHEME)


def pick_jump_keyword(matched, fallback=""):
    """跳转时带进目标面板搜索框的关键词。

    目标面板（碎片 / 笔记）的搜索框是**原样子串过滤**：拿整条查询
    「月报 归档」去过滤，什么都匹配不上，表现为「切过去列表是空的」。
    所以优先取**命中的检索项里最长的一个**——``hit.matched`` 里的项由
    ``make_snippet`` 保证在原文里字面出现过，拿它过滤必有结果。
    """
    items = [m for m in (matched or ()) if isinstance(m, str) and m]
    return max(items, key=len) if items else str(fallback or "").strip()


def target_filter_matches(kind, keyword, data):
    """预判「把关键词带进目标面板后，子串过滤是否还有内容」（K2 自愈）。

    碎片 / 笔记两类跳转会把关键词写进目标页搜索框做**原样子串过滤**
    （碎片：content/source；笔记：title/content，均不分大小写——与
    宿主面板实现同口径）。但检索召回可能来自 bigram 交叉命中（查询词
    与原文用词不同），这种关键词带过去过滤必然为空，表现为「跳过去
    列表是空的」。这里用同一份只读快照**模拟同样的过滤**：空 → 调用
    侧改跳「空关键词」（= 只切页显示全部）并说明原因。

    返回 True（有匹配）/ False（必为空）/ None（该 kind 不带关键词，
    不适用）。``data`` 读取失败一律 None（宁多带一次关键词也不误判）。
    """
    kw = str(keyword or "").strip().lower()
    if not kw:
        return True                      # 空关键词 = 不过滤
    if kind == "fragment":
        try:
            frags = data.fragments() or []
        except Exception:                 # noqa: BLE001
            return None
        for f in frags:
            if not isinstance(f, dict):
                continue
            if kw in str(f.get("content") or "").lower() \
                    or kw in str(f.get("source") or "").lower():
                return True
        return False
    if kind == "note":
        try:
            notes = data.notes() or []
        except Exception:                 # noqa: BLE001
            return None
        for n in notes:
            if not isinstance(n, dict):
                continue
            if kw in str(n.get("title") or "").lower() \
                    or kw in str(n.get("content") or "").lower():
                return True
        return False
    return None


# ====================================================================
# 动作
# ====================================================================
class OpenSearchAction(BallAction):
    """打开站内搜索页（在宿主主窗口里）；宿主无页面机制时退回提示弹窗"""

    id = f"{PLUGIN_ID}.open"
    title = "站内搜索"

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
        # 碎片元数据旁路（uid → source/category/created_at），重建索引时刷新
        self._frag_meta = {}
        # 处于「展开全文」态的结果下标。下标是渲染序，hits 一换代意义
        # 就变，所以 _run_search 每次都要清空
        self._expanded = set()
        # 搜索历史（K3）：最新在前，存插件私有目录
        self._history = load_history(ctx)
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
        title = QLabel("站内搜索")
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

        # ---- 最近搜索行（K3）：输入框为空时显示，点击重搜、右键删单条 ----
        self._recent_row = QWidget(self)
        recent_lay = QHBoxLayout(self._recent_row)
        recent_lay.setContentsMargins(0, 0, 0, 0)
        recent_lay.setSpacing(6)
        root.addWidget(self._recent_row)
        self._rebuild_recent()

        # ---- 范围行（K1）：五个数据源勾选，默认全开 ----
        # 全开时 search 走「不过滤」原路径（与旧版逐条一致）；在索引层
        # 之后按范围排除，勾选切换即时生效，无需重建索引。
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(make_hint_label("范围"))
        self._kind_checks = {}
        for kind_key, label in KIND_LABEL.items():
            chk = QCheckBox(label)
            chk.setChecked(True)
            chk.setToolTip("只在勾选的数据源里出结果（索引始终覆盖全部）")
            chk.toggled.connect(self._on_kind_toggled)
            self._kind_checks[kind_key] = chk
            filter_row.addWidget(chk)
        filter_row.addStretch(1)
        root.addLayout(filter_row)

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
        # 悬停锚点时 highlighted 发出 href（离开时发空串）→ 显示分数 tooltip
        self._view.highlighted.connect(self._on_link_hovered)
        box_lay.addWidget(self._view)
        root.addWidget(box, 1)

        self._empty = QLabel("输入关键词开始搜索\n\n"
                             "· 支持中文短语与英文单词混合，例如「月报 归档」\n"
                             "· 命中处加粗着色，悬停结果行可查看相关度分数\n"
                             "· 点结果标题跳转到对应面板（知识库会定位到那一段）\n"
                             "· 超长碎片点「展开全文」查看全部内容")
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
            self._stat.setText(f"读取数据失败：{exc!r}")
            self._ctx.logger.warning(f"[{PLUGIN_ID}] 采集文档失败：{exc!r}")
            return
        self._docs = len(docs)
        build_index(docs, self._index)
        # 碎片 meta 旁路：与索引同一份快照，失败只降级显示（见函数 docstring）
        self._frag_meta = collect_fragment_meta(self._ctx.data)
        self.index_ready.emit(len(docs))
        if verbose:
            self._toast(f"索引已重建（{len(docs)} 条）")

    def _on_index_ready(self, count):
        self._docs = count
        self._stat.setText(f"已索引 {count} 条 ｜ 检索项 {self._index.term_count} 个"
                           f" ｜ 输入即搜")

    # ---------------- 搜索 ----------------
    def _on_text_changed(self, _text):
        self._sync_recent_visible()               # 有输入就藏「最近」行
        self._debounce.start()                    # 去抖，避免每敲一个字搜一次

    def _on_kind_toggled(self, _checked=False):
        """勾选变化 → 立即重搜（结果层过滤，无需重建索引）"""
        if self._input.text().strip():
            self._run_search()

    def _selected_kinds(self):
        """勾选的数据源列表；全开 = []（search 不过滤，与旧路径一致）"""
        return [k for k, chk in self._kind_checks.items() if chk.isChecked()]

    def _run_search(self):
        query = self._input.text().strip()
        if not query:
            self._hits = []
            self._view.setHtml("")
            self._empty.setVisible(True)
            self._sync_recent_visible()
            return
        kinds = self._selected_kinds()
        if not kinds:
            # 一个源都不勾 = 范围为空，直接给空结果（不发检索）
            self._hits = []
            self._empty.setVisible(True)
            self._view.setHtml("")
            self._stat.setText("没有勾选任何数据源 ｜ 至少勾选一个再搜")
            return
        kind_arg = kinds if len(kinds) < len(KIND_LABEL) else None
        try:
            hits = self._index.search(query, top_n=MAX_RESULTS,
                                      kind=kind_arg)
        except Exception as exc:                  # noqa: BLE001
            self._hits = []
            self._stat.setText(f"检索失败：{exc!r}")
            return
        # ⚠ 必须与 render_results_html 用的是**同一个列表**：锚点 href 里存的
        # 是它在列表里的下标，渲染完再改列表就会点错行
        self._hits = hits
        self._expanded.clear()     # 下标是渲染序，hits 换代必须清展开态
        self._empty.setVisible(not hits)
        self._view.setHtml(render_results_html(
            hits, query, self._accent, self._frag_meta,
            self._theme_colors(), self._expanded))
        self._remember_history(query)             # K3：有效检索记入历史
        scope = ""
        if kind_arg is not None:
            labels = " / ".join(KIND_LABEL[k] for k in kinds)
            scope = f" ｜ 范围：{labels}"
        if hits:
            self._stat.setText(f"命中 {len(hits)} 条（最多显示 {MAX_RESULTS} 条）"
                               f" ｜ 索引 {self._docs} 条{scope}")
        else:
            self._stat.setText(f"没有匹配「{query}」的内容 ｜ 索引 {self._docs} 条"
                               f"{scope}")

    # ---------------- 最近搜索（K3） ----------------
    def _remember_history(self, word: str):
        """有效检索后记入历史：去重插前、截断、落盘、重建「最近」行"""
        word = str(word or "").strip()[:HISTORY_CHARS]
        if not word:
            return
        if word in self._history:
            self._history.remove(word)
        self._history.insert(0, word)
        self._history = self._history[:HISTORY_LIMIT]
        save_history(self._ctx, self._history)
        self._rebuild_recent()
        self._sync_recent_visible()

    def _rebuild_recent(self):
        """按历史重建「最近」行（标签 + 词按钮们 + 清空）；无历史整行隐藏"""
        lay = self._recent_row.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._history:
            self._recent_row.setVisible(False)   # 无历史整行隐藏
            return
        cap = QLabel("最近", self._recent_row)
        cap.setObjectName("hintLabel")
        lay.addWidget(cap)
        for word in self._history:
            btn = SmoothButton(word, self._recent_row)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"重新搜索「{word}」\n右键可删除这条历史")
            btn.clicked.connect(
                lambda _=False, w_=word: self._search_from_history(w_))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, w_=word: self._history_menu(b, w_, pos))
            lay.addWidget(btn)
        clear = SmoothButton("清空", self._recent_row)
        clear.setObjectName("secondaryBtn")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setToolTip("清空全部搜索历史")
        clear.clicked.connect(lambda _=False: self._clear_history())
        lay.addWidget(clear)
        lay.addStretch(1)

    def _sync_recent_visible(self):
        """「最近」行只在输入框为空且有历史时显示"""
        self._recent_row.setVisible(
            bool(self._history) and not self._input.text().strip())

    def _search_from_history(self, word: str):
        """点历史词：顶到最前并落盘 → 填入输入框（去抖后自动重搜）"""
        if word in self._history:
            self._history.remove(word)
        self._history.insert(0, word)
        self._history = self._history[:HISTORY_LIMIT]
        save_history(self._ctx, self._history)
        self._input.setText(word)     # textChanged → 去抖 → _run_search

    def _history_menu(self, btn, word: str, pos):
        """历史词右键菜单：删除单条"""
        menu = QMenu(self)
        act = menu.addAction(f"删除「{word}」")
        act.triggered.connect(lambda: self._remove_history(word))
        menu.exec(btn.mapToGlobal(pos))

    def _remove_history(self, word: str):
        """删除一条历史并落盘重建"""
        if word in self._history:
            self._history.remove(word)
            save_history(self._ctx, self._history)
        self._rebuild_recent()
        self._sync_recent_visible()

    def _clear_history(self):
        """清空全部历史并落盘"""
        self._history = []
        save_history(self._ctx, self._history)
        self._rebuild_recent()
        self._sync_recent_visible()

    # ---------------- 跳转 ----------------
    def _on_anchor(self, url):
        """锚点分发：fp-expand → 行内展开/收起；fp-result → 跳转面板。

        两个 scheme 各有独立解析函数且**互不解析**（见 parse_expand_anchor），
        所以点「展开全文」绝不会被当成跳转，反之亦然。宿主侧只暴露一个
        **公开入口** ``show_search_result(kind, keyword, num)``（与
        ``show_plugin_page`` 同款约定）；宿主没有这个入口时提示一句，
        不让点击静默失败。
        """
        text = url.toString() if hasattr(url, "toString") else str(url)
        idx = parse_expand_anchor(text)
        if idx is not None:
            if 0 <= idx < len(self._hits):
                if idx in self._expanded:
                    self._expanded.discard(idx)
                else:
                    self._expanded.add(idx)
                self._rerender_results()
            return
        idx = parse_result_anchor(text)
        if idx is None or idx >= len(self._hits):
            return
        self._jump_to(self._hits[idx])

    def _theme_colors(self):
        """宿主主题色 dict（QTextBrowser 内部只认行内样式，颜色必须每次
        渲染时从主题取）；取不到返回 None，渲染端走字面量兜底"""
        try:
            from src.theme import get_colors
            return get_colors(theme_of(_host_window(self._ctx)))
        except Exception:                     # noqa: BLE001
            return None

    def _rerender_results(self):
        """展开/收起后的重渲染：不重建索引、不记历史、不动命中列表"""
        if not self._hits:
            return
        self._view.setHtml(render_results_html(
            self._hits, self._input.text().strip(), self._accent,
            self._frag_meta, self._theme_colors(), self._expanded))

    def _on_link_hovered(self, href):
        """悬停结果行 → QToolTip 显示相关度分数。

        v1.3.0 起分数不再占行内位置（旧 .score 的 opacity 在 QTextBrowser
        静默失效，分数混进标题）。highlighted 在离开锚点时发空串 → 隐藏。
        """
        text = href.toString() if hasattr(href, "toString") else str(href or "")
        idx = parse_result_anchor(text)
        if idx is None or not (0 <= idx < len(self._hits)):
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(),
                          f"相关度 {self._hits[idx].score:.2f}", self._view)

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
        # K2 跳转自愈：碎片/笔记的目标页按「原样子串」过滤关键词，而
        # 检索召回可能来自 bigram 交叉命中——那种关键词带过去必然过滤
        # 为空。先模拟过滤：必空 → 只切页（空关键词=显示全部）并说明。
        if target_filter_matches(hit.kind, keyword, self._ctx.data) is False:
            self._toast("命中来自字符相关性，目标页按该词过滤不到——"
                        "已切到该页并显示全部内容")
            keyword = ""
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
    version = "1.3.0"

    def create_actions(self, ctx):
        return [OpenSearchAction()]

    def create_page(self, ctx):
        return SearchPage(ctx)
