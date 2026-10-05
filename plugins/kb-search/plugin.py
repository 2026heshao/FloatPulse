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
  2. **命中高亮走 rich text 行内样式**：宿主 QLabel 不渲染 Markdown，
     高亮只能靠拼控件或自绘；v1.3 起用 QTextBrowser 的 HTML 子集，
     v1.5.0 卡片化后每条结果是独立 ResultCard（QLabel rich text 消费
     行内样式），命中处 span 着色 + $primary_a12 底纹。

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

v1.4.0 插件独立设置（2026-10-04）：
  - manifest.settings 声明三项：max_results（int，默认 60）/
    search_notes（bool，默认开）/ ranking（enum：bm25 / tfidf，默认 bm25）。
    宿主插件中心卡片的「设置」按钮弹层编辑，存储落插件私有目录
    （settings.json），不进宿主主配置；default 即 v1.3 的既有行为
  - 页面按 ctx.get_setting 读生效值（旧宿主无此契约 → 走常量兜底），
    并订阅 ctx.settings_changed：插件中心保存后勾选态与检索参数即时刷新，
    不必重开页面

v1.5.0 结果页卡片化重设计（2026-10-05，方案见 docs/站内搜索结果页重设计）：
  三个实测痛点，三个结构性修法：
  1. **弱相关大段正文涌入首页**（查 "6" 时 2026 日期顺带命中 97% 碎片）：
     core 加相关度分层——min_score_rel 相对分数下限 + is_weak_query 弱查询
     口径（单字/纯数字只把标题/首行/文件名级命中算强相关），weak 命中
     折叠进「低相关」组，默认收起
  2. **「展开全文」整页弹跳**：根因是展开=整份结果 setHtml 重渲染（滚动
     位置丢、其余结果全部重排）。现每条结果一张独立 ResultCard（QWidget），
     展开状态自持在卡上，切展开只重排本卡内部——其余卡几何纹丝不动；
     超长碎片（>800 字）不給行内展开，改「在面板中打开」看全文（守住
     「不做数据沉淀层」红线）
  3. **结果连成一面墙**：QTextBrowser 单份 HTML → QScrollArea + 卡片列
     （卡距 8px、kind 徽章、左缘色条、命中行内高亮），来源过滤五勾选行
     → 单选 chips；「显示更多」按页追加卡片，旧卡不重渲染
====================================================================
"""

import html
import importlib.util
import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QScrollArea, QVBoxLayout, QWidget,
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

# ---------------- 结果卡片列（v1.5.0） ----------------
PAGE_SIZE = 20                      # 主列表每页卡片数（「显示更多」步长）
CARD_SNIPPET_RADIUS = 40            # 卡片正文窗口半径（v1.3 行版是 26）
INLINE_EXPAND_MAX_CHARS = 800       # 行内「展开」只服务短文；超长走面板跳转
SEARCH_MIN_SCORE_REL = 0.18         # 相对分数下限：score < top×0.18 进低相关组

# 数据源标识 → 展示名（kind 徽章 / chips / 尾行「在 xx 面板打开」共用）
KIND_LABEL = {
    "knowledge": "知识库",
    "note": "笔记",
    "fragment": "碎片",
    "task": "任务",
    "asset": "素材",
}

# 数据源标识 → 主题 token（kind 徽章文字色与非碎片卡的左缘色条共用；
# 碎片卡的色条仍走内容类别 CATEGORY_TOKENS，不在此表）
_KIND_TOKEN = {
    "knowledge": "primary",
    "note": "link",
    "fragment": "text_secondary",
    "task": "warn",
    "asset": "danger",
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

# 空态引导文案（v1.5.0：与卡片列的新交互口径一致）
EMPTY_HINT = ("输入关键词开始搜索\n\n"
              "· 支持中文短语与英文单词混合，例如「月报 归档」\n"
              "· 强相关结果以卡片呈现，正文顺带命中的低相关结果自动折叠\n"
              "· 点结果标题或「在面板中打开」跳转到对应面板（知识库会定位到那一段）\n"
              "· 短碎片可「展开」在卡内看全文，超长碎片请进面板查看")

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

# 卡片容器视觉 token 兜底（v1.5.0，与 theme.py 对应 token 字面量逐值一致；
# AST 护栏见 test_kb_search_cards.TestCardFallbackGuardrails）
_CARD_FALLBACK = {
    "light": {"card_bg_solid": "#FFFFFF", "line": "#E4E2DB",
              "line_2": "#D3D1C7",
              "list_item_hover": "rgba(15, 110, 86, 0.08)",
              "primary_a12": "rgba(15, 110, 86, 0.12)",
              "on_primary": "#FFFFFF"},
    "dark":  {"card_bg_solid": "#1E2126", "line": "#33373D",
              "line_2": "#454A52",
              "list_item_hover": "rgba(93, 202, 165, 0.08)",
              "primary_a12": "rgba(93, 202, 165, 0.12)",
              "on_primary": "#04342C"},
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


def _with_alpha(hex_color, alpha: float = 0.12) -> str:
    """纯色 hex → 同色低透明度 rgba（kind 徽章的浅底）。

    只对既有 token 色加透明度，**不引入新色值**（与 theme.py 的 *_alpha
    token 同思路）；解析不了原样返回，调用端退化为无底色徽章。
    """
    h = str(hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return str(hex_color or "")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return str(hex_color or "")
    return f"rgba({r}, {g}, {b}, {int(round(alpha * 255))})"


def _card_token(token, colors, theme=DEFAULT_THEME) -> str:
    """卡片视觉 token → 色值：宿主色表优先，取不到按主题走字面量兜底。

    查找顺序：文字/类别色系在 _CATEGORY_FALLBACK，容器色系（卡底/描边/
    hover/高亮底纹）在 _CARD_FALLBACK；两处都没有返回 ""（调用端退化）。
    """
    if isinstance(colors, dict):
        value = colors.get(token)
        if isinstance(value, str) and (value.startswith("#")
                                       or value.startswith("rgba(")):
            return value
    theme = theme if theme in ("light", "dark") else DEFAULT_THEME
    for table in (_CATEGORY_FALLBACK, _CARD_FALLBACK):
        if token in table.get(theme, {}):
            return table[theme][token]
    return ""


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
      失效，分数混进标题）。v1.5.0 起页面走卡片列，本函数退役为兼容层
      （纯函数测试与旧调用方仍可用），分数改为整卡 setToolTip
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


def _ellipsis_for(snippet, full_text):
    """按需省略号：只有 snippet 真是 full_text 的中间截断时才加（两侧独立）。

    旧版恒加 "…"：82% 的碎片整条放得下也被谎报截断（"…Python…"）。
    行版 HTML 与卡片 rich text 共用这一份判断，避免两处口径漂移。
    """
    full = full_text if isinstance(full_text, str) else ""
    lead = "…" if full and not full.startswith(snippet) else ""
    tail = "…" if full and not full.endswith(snippet) else ""
    return lead, tail


def _body_html(snippet, spans, full_text=""):
    """正文 HTML（QTextBrowser 行版，v1.5.0 起退役为兼容层）：
    命中处高亮 + **按需**省略号（_ellipsis_for）。"""
    pieces = "".join(
        f'<span class="hit">{html.escape(text)}</span>' if is_hit
        else html.escape(text)
        for text, is_hit in core.split_by_spans(snippet, spans))
    lead, tail = _ellipsis_for(snippet, full_text)
    return f"{lead}{pieces}{tail}"


def render_card_body_html(snippet, spans, full_text="", accent=None,
                          mark_bg=None):
    """单卡正文 rich text（QLabel 消费，v1.5.0）：命中行内高亮 + 按需省略号。

    - 转义规则与 ``_body_html`` 同源：用户文本一律 html.escape，命中区间
      由 core.split_by_spans 给出（核心层不知道渲染方式）
    - 高亮 = 强调色文字 + ``$primary_a12`` 底纹（QLabel 不认 QSS class，
      行内样式是唯一通道）；``mark_bg`` 缺省用 light 字面量，纯函数调用方
      （测试）行为稳定
    - QLabel rich text 不保留换行空白：``\\n`` 转 ``<br>``（多空格缩进会
      被折叠——卡片列的既定取舍，超长带缩进的正文请进面板看原文）
    """
    color = accent or _ACCENT_FALLBACK[DEFAULT_THEME]
    mark = mark_bg or _CARD_FALLBACK[DEFAULT_THEME]["primary_a12"]

    def _piece(text):
        return html.escape(text).replace("\n", "<br>")

    pieces = "".join(
        f'<span style="color:{color};font-weight:600;'
        f'background-color:{mark}">{_piece(text)}</span>' if is_hit
        else _piece(text)
        for text, is_hit in core.split_by_spans(snippet, spans))
    lead, tail = _ellipsis_for(snippet, full_text)
    return f"{lead}{pieces}{tail}"


def render_card_body(hit, expanded=False, accent=None, mark_bg=None,
                     radius=CARD_SNIPPET_RADIUS):
    """一条命中结果的卡片正文 rich text（v1.5.0）。

    - 片段态：用 ``radius``（默认 40，比行版 26 宽）现算窗口片段——
      search() 时缓存的 hit.snippet 是 26 半径的，卡片列有空间放宽
    - 展开态：全文 + 按全文坐标系重算的高亮（复用 ``_full_text_with_spans``）
    - 现算失败（数据被外部改坏）退化为原文前 200 字，不让单卡炸页面
    """
    try:
        if expanded:
            text, spans = _full_text_with_spans(hit)
            return render_card_body_html(text, spans, "", accent, mark_bg)
        snippet, spans, _matched = core.make_snippet(
            hit.text, hit.matched, radius=radius)
        return render_card_body_html(snippet, spans, hit.text, accent, mark_bg)
    except Exception:                         # noqa: BLE001 - 单卡不许炸页面
        text = hit.text if isinstance(hit.text, str) else str(hit.text or "")
        return html.escape(text[:200])


def _strip_kind_prefix(hit):
    """剥掉 collect_documents 拼的「kind · 」前缀——徽章已标数据源，
    标题里再出现一遍是 v1.2 的旧噪音（"[笔记] 笔记 · xxx"）。"""
    label = KIND_LABEL.get(hit.kind, "")
    title = str(hit.title or "")
    prefix = f"{label} · "
    return title[len(prefix):] if label and title.startswith(prefix) else title


def card_title_for(hit, meta=None):
    """卡片头行标题：给每张卡一个「身份」锚点。

    - 非碎片：hit.title 剥掉 kind 前缀（任务标题 / 知识库段号 / 素材
      文件名都在其中）
    - 碎片没有标题：身份 = 类别名 · 来源（≠剪贴板时）· 首行截断 32 字
      （97% 碎片来自剪贴板，恒显示来源是噪音——沿用 v1.3 口径）
    """
    if hit.kind != "fragment":
        return _strip_kind_prefix(hit) or KIND_LABEL.get(hit.kind, "其它")
    meta = meta if isinstance(meta, dict) else {}
    bits = []
    label = _CAT_LABELS.get(meta.get("category") or "")
    if label:
        bits.append(label)
    source = str(meta.get("source") or "").strip()
    if source and source != SOURCE_DEFAULT:
        bits.append(source)
    head = str(hit.text or "").split("\n", 1)[0].strip()
    if head:
        bits.append(head[:32] + ("…" if len(head) > 32 else ""))
    return " · ".join(bits) if bits else KIND_LABEL.get(hit.kind, "碎片")


def card_time_for(hit, meta=None):
    """卡片头行右侧的相对时间。目前只有碎片带 created_at（元数据旁路）；
    其余数据源快照里没有可展示的时间字段，返回空串（时间标签整体省略）。"""
    if hit.kind != "fragment":
        return ""
    meta = meta if isinstance(meta, dict) else {}
    created = str(meta.get("created_at") or "").strip()
    if not created:
        return ""
    return meta_time_for(created)


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
# 结果卡片（v1.5.0）：一条结果一张独立 widget
# ====================================================================
class _ElidedTitle(QLabel):
    """卡头标题：宽度不足时 QFontMetrics 补省略号（QLabel 没有原生 ellipsis）。

    整段包 fp-result 锚点，点击跳转对应面板（与正文/尾行同一分发口）。
    resizeEvent 里重排——布局给它的宽度变了才需要重新裁字；文本没变时
    setText 直接返回，不会形成重排死循环。
    """

    def __init__(self, page, index, text, colors, theme):
        super().__init__()
        self._page = page
        self._index = index
        self._full = str(text or "")
        self._colors = colors
        self._theme = theme
        self.setWordWrap(False)
        font = self.font()
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(page._on_card_link)
        self._reflow()

    def apply_theme(self, colors, theme):
        self._colors = colors
        self._theme = theme
        self._reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        text_color = _card_token("text", self._colors, self._theme)
        width = max(12, self.width() - 2)
        elided = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, width)
        rich = (f'<a href="{RESULT_SCHEME}:{self._index}" '
                f'style="color:{text_color};text-decoration:none">'
                f'{html.escape(elided)}</a>')
        if self.text() != rich:
            self.setText(rich)


class ResultCard(QFrame):
    """一条结果一张卡（v1.5.0）。

    结构（自左向右 / 自上而下）：
      左缘 3px 色条 —— 碎片 = 内容类别色（CATEGORY_TOKENS 真相源，
                        category_color_for 单一取色入口）；其他数据源 =
                        kind 主题 token；未知类别画透明占位槽（对齐不塌）
      头行          —— kind 徽章（token 色 + 12% 同色浅底）+ 标题（省略号）
                       + 相对时间（11px placeholder，碎片专属）
      正文          —— 窗口片段（radius 40），命中处行内高亮，整段包
                       fp-result 锚点
      尾行          —— 「展开/收起」（fp-expand，仅短碎片）+「在 xx 面板
                       打开 →」（fp-result）；相关度分数在整卡 setToolTip

    展开状态**自持**在卡上（旧版全局 _expanded 下标集退役）：toggle 只
    换本卡正文与尾行文本，QScrollArea 里其余卡的几何纹丝不动、滚动位置
    不丢——「展开全文整页弹跳」的根因（整份结果 setHtml 重渲染）从结构
    上消失。超长碎片（>INLINE_EXPAND_MAX_CHARS）不給行内展开，走「在
    面板中打开」看全文（不做数据沉淀层）。
    """

    def __init__(self, page, index, hit, meta, colors, theme):
        super().__init__()
        self.setObjectName("kbSearchCard")
        self._page = page
        self._index = index
        self._hit = hit
        self._meta = meta if isinstance(meta, dict) else {}
        self._colors = colors
        self._theme = theme
        self._expanded = False
        self.setToolTip(f"相关度 {hit.score:.2f}")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._bar = QFrame()
        self._bar.setFixedWidth(3)
        root.addWidget(self._bar)
        col = QVBoxLayout()
        col.setContentsMargins(13, 9, 12, 9)
        col.setSpacing(4)
        root.addLayout(col)

        # ---- 头行：徽章 + 标题 + 时间 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        self._badge = QLabel(KIND_LABEL.get(hit.kind, hit.kind or "其它"))
        head.addWidget(self._badge)
        self._title = _ElidedTitle(
            page, index, card_title_for(hit, self._meta), colors, theme)
        head.addWidget(self._title, 1)
        self._time = QLabel(card_time_for(hit, self._meta))
        head.addWidget(self._time)
        head.addStretch(0)                    # 时间贴右（无时间时标题吃满）
        col.addLayout(head)

        # ---- 正文 / 尾行 ----
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._body.setOpenExternalLinks(False)
        self._body.linkActivated.connect(page._on_card_link)
        col.addWidget(self._body)
        self._foot = QLabel()
        self._foot.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._foot.setOpenExternalLinks(False)
        self._foot.linkActivated.connect(page._on_card_link)
        col.addWidget(self._foot)

        self.apply_theme(colors, theme)

    # ---------------- 主题 ----------------
    def apply_theme(self, colors, theme):
        """主题色刷新：容器/色条/徽章/时间/尾行重设样式，正文与标题重渲染
        （命中高亮与链接色都在 rich text 行内，必须重出文本）。"""
        self._colors = colors
        self._theme = theme
        card = _card_token("card_bg_solid", colors, theme)
        line = _card_token("line", colors, theme)
        hover = _card_token("list_item_hover", colors, theme)
        self.setStyleSheet(
            f"QFrame#kbSearchCard{{background-color:{card};"
            f"border:1px solid {line};border-radius:8px;}}"
            f"QFrame#kbSearchCard:hover{{background-color:{hover};}}")
        bar = self._bar_color(colors, theme)
        self._bar.setStyleSheet(
            f"background-color:{bar or 'transparent'};border:none;"
            "border-top-left-radius:8px;border-bottom-left-radius:8px;")
        kind_color = _card_token(
            _KIND_TOKEN.get(self._hit.kind, "text_secondary"), colors, theme)
        self._badge.setStyleSheet(
            f"color:{kind_color};background-color:{_with_alpha(kind_color)};"
            "border-radius:4px;font-size:10px;font-weight:600;"
            "padding:1px 8px;")
        ph = _card_token("text_placeholder", colors, theme)
        self._time.setStyleSheet(f"color:{ph};font-size:11px;")
        self._foot.setStyleSheet(f"color:{ph};font-size:12px;")
        self._title.apply_theme(colors, theme)
        self._refresh_body()
        self._refresh_foot()

    def _bar_color(self, colors, theme) -> str:
        """左缘色条：碎片走内容类别（真相源 CATEGORY_TOKENS），其余走
        kind token；取不到返回 ""（透明占位槽，卡头仍对齐）。"""
        if self._hit.kind == "fragment":
            return category_color_for(
                self._meta.get("category"), theme=theme, colors=colors)
        return _card_token(
            _KIND_TOKEN.get(self._hit.kind, ""), colors, theme)

    # ---------------- 内容 ----------------
    def _refresh_body(self):
        body = render_card_body(
            self._hit, expanded=self._expanded,
            accent=self._page._accent,
            mark_bg=_card_token("primary_a12", self._colors, self._theme))
        text_color = _card_token("text", self._colors, self._theme)
        self._body.setText(
            f'<a href="{RESULT_SCHEME}:{self._index}" '
            f'style="color:{text_color};text-decoration:none">{body}</a>')

    def _refresh_foot(self):
        i = self._index
        ph = _card_token("text_placeholder", self._colors, self._theme)
        parts = []
        if self._can_expand():
            lbl = "收起" if self._expanded else "展开"
            parts.append(f'<a href="{EXPAND_SCHEME}:{i}" '
                         f'style="color:{ph};text-decoration:none">{lbl}</a>')
        label = KIND_LABEL.get(self._hit.kind, "对应")
        parts.append(f'<a href="{RESULT_SCHEME}:{i}" '
                     f'style="color:{self._page._accent};'
                     f'text-decoration:none">在{label}面板打开 →</a>')
        self._foot.setText(" ｜ ".join(parts))

    def _can_expand(self) -> bool:
        """行内展开只服务**短**碎片：snippet 已是全文（大多数）或正文超长
        （>800 字，防卡片被撑成新的一面墙）都不给。"""
        if self._hit.kind != "fragment":
            return False
        text = self._hit.text if isinstance(self._hit.text, str) \
            else str(self._hit.text or "")
        if not text or len(text) > INLINE_EXPAND_MAX_CHARS:
            return False
        snippet = self._hit.snippet if isinstance(self._hit.snippet, str) \
            else ""
        return snippet != text

    def toggle_expand(self):
        """卡内展开/收起：只重排本卡（其余卡几何不动、滚动位置不丢）。"""
        if not self._can_expand():
            return False
        self._expanded = not self._expanded
        self._refresh_body()
        self._refresh_foot()
        return True


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
        # 检索顺序的命中列表：锚点 href 里存的是它的下标，点击时靠它反查。
        # 列表只被整体替换、从不重排（分层只贴 tier 标记），下标始终有效
        self._hits = []
        # 相关度分层（v1.5.0）：存 self._hits 里的下标，与卡片一一对应
        self._strong_idx = []
        self._weak_idx = []
        self._shown = 0                    # 主列表已渲染的强相关卡数
        # 卡片列（v1.5.0）：主列表与低相关组的 ResultCard 集合
        self._cards = []
        self._weak_cards = []
        self._weak_open = False
        # 碎片元数据旁路（uid → source/category/created_at），重建索引时刷新
        self._frag_meta = {}
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
        # 插件独立设置（v1.4.0）：读生效值 + 订阅插件中心的保存通知。
        # 订阅按宿主铁律走守卫模式——旧宿主没有该信号时自然跳过。
        self._apply_settings()
        settings_sig = getattr(ctx, "settings_changed", None)
        connect = getattr(settings_sig, "connect", None) \
            if settings_sig is not None else None
        if callable(connect):
            try:
                connect(self._on_settings_changed)
            except Exception:                     # noqa: BLE001 - 订阅失败只影响实时性
                pass

    # ---------------- 插件设置（v1.4.0） ----------------
    def _get_setting(self, key, fallback):
        """读一个设置项生效值；旧宿主没有 get_setting 契约 → 常量兜底"""
        getter = getattr(self._ctx, "get_setting", None)
        if not callable(getter):
            return fallback
        try:
            value = getter(key, fallback)
        except Exception:                         # noqa: BLE001
            return fallback
        return fallback if value is None else value

    def _apply_settings(self):
        """把三项设置的生效值读进页面状态（构建时 / 收到变更通知时）"""
        raw_max = self._get_setting("max_results", MAX_RESULTS)
        try:
            self._max_results = max(1, int(raw_max))
        except (TypeError, ValueError):
            self._max_results = MAX_RESULTS
        self._search_notes = bool(self._get_setting("search_notes", True))
        ranking = self._get_setting("ranking", "bm25")
        self._ranking = ranking if ranking == "tfidf" else "bm25"

    def _on_settings_changed(self, _keys=None):
        """插件中心保存设置后：刷新状态并应用（检索参数 + 笔记范围）。

        「默认搜索笔记」不再对应勾选框（v1.5.0 chips 单选化）：关掉时在
        结果层剔除笔记命中，选「笔记」chip 仍可显式只搜笔记。
        有查询词时按新参数立即重搜。
        """
        self._apply_settings()
        if self._input.text().strip():
            self._run_search()

    def _on_theme_changed(self, *_args):
        """主题切换 → 换强调色并刷新已有卡片样式（不重建索引、不重搜，
        数据没变——遍历卡片刷样式比整页 setHtml 便宜得多）"""
        self._accent = accent_for(theme_of(_host_window(self._ctx)))
        self._sync_chip_styles()
        if self._hits:
            colors = self._theme_colors()
            theme = theme_of(_host_window(self._ctx))
            for card in self._cards + self._weak_cards:
                card.apply_theme(colors, theme)

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
            "点结果标题或「在面板中打开」跳转到对应面板"))
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

        # ---- 范围 chips（K1，v1.5.0 单选化）----
        # chip 即过滤：默认「全部」（search 走不过滤原路径，与旧版逐条
        # 一致）；点单个数据源 chip 只看该源，点击即时重搜，无需重建索引。
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        chips_row.addWidget(make_hint_label("范围"))
        self._kind_chips = {}
        self._chip_key = "all"
        for kind_key, label in [("all", "全部")] + list(KIND_LABEL.items()):
            chip = SmoothButton(label)
            chip.setObjectName("kbSearchChip")
            chip.setCheckable(True)
            chip.setChecked(kind_key == "all")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("混排全部数据源" if kind_key == "all" else
                            "只看这一类数据源（索引始终覆盖全部）")
            chip.clicked.connect(
                lambda _=False, k=kind_key: self._on_chip_clicked(k))
            self._kind_chips[kind_key] = chip
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        root.addLayout(chips_row)
        self._sync_chip_styles()

        self._stat = QLabel("正在建立索引…")
        self._stat.setObjectName("hintLabel")
        root.addWidget(self._stat)

        # ---- 结果卡片列（v1.5.0）：QScrollArea + 垂直卡片布局 ----
        # 一条结果一张独立 ResultCard：展开/追加都只动自己的卡，不再有
        # 整份 setHtml 重渲染导致的滚动位置丢失与整列重排。
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_box = QWidget()
        self._results_lay = QVBoxLayout(self._results_box)
        self._results_lay.setContentsMargins(0, 0, 0, 0)
        self._results_lay.setSpacing(8)
        self._scroll.setWidget(self._results_box)

        # 低相关折叠组（默认收起）：弱相关命中收纳于此，不与强相关抢首屏
        self._weak_bar = SmoothButton("低相关")
        self._weak_bar.setObjectName("secondaryBtn")
        self._weak_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._weak_bar.setToolTip("这些结果只在正文里顺带命中（如日期里的数字），"
                                  "与查询关联较弱")
        self._weak_bar.setVisible(False)
        self._weak_bar.clicked.connect(lambda _=False: self._toggle_weak())
        self._results_lay.addWidget(self._weak_bar)
        self._weak_box = QWidget(self._results_box)
        self._weak_lay = QVBoxLayout(self._weak_box)
        self._weak_lay.setContentsMargins(0, 0, 0, 0)
        self._weak_lay.setSpacing(8)
        self._weak_box.setVisible(False)
        self._results_lay.addWidget(self._weak_box)

        # 分页：追加下一页强相关卡片（旧卡不重渲染、几何不动）
        self._more_btn = SmoothButton("显示更多")
        self._more_btn.setObjectName("secondaryBtn")
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setVisible(False)
        self._more_btn.clicked.connect(lambda _=False: self._show_more())
        self._results_lay.addWidget(self._more_btn)
        self._results_lay.addStretch(1)

        root.addWidget(self._scroll, 1)

        self._empty = QLabel(EMPTY_HINT)
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

    # ---------------- 范围 chips（v1.5.0 单选化） ----------------
    def _on_chip_clicked(self, key):
        """点 chip 切换范围：即时重搜（结果层过滤，无需重建索引）。

        单选语义，不允许「全部不选」：重复点当前 chip 恢复选中态
        （QPushButton checkable 点自己会被 toggle 成未选中）。
        """
        if key not in self._kind_chips:
            return
        if self._chip_key == key:
            self._kind_chips[key].setChecked(True)
            return
        self._chip_key = key
        for k, chip in self._kind_chips.items():
            chip.setChecked(k == key)
        if self._input.text().strip():
            self._run_search()

    def _selected_kinds(self):
        """选中的数据源列表；「全部」= 全部 kind（search 不过滤，与旧路径一致）"""
        if self._chip_key == "all":
            return list(KIND_LABEL.keys())
        return [self._chip_key]

    def _chip_qss(self, colors, theme) -> str:
        """范围 chip 的整套样式（含 :checked 态）——一套文本按状态切换，
        主题切换时整体重设即可，无需逐 chip 判断选中态换色。"""
        card = _card_token("card_bg_solid", colors, theme)
        line2 = _card_token("line_2", colors, theme)
        text2 = _card_token("text_secondary", colors, theme)
        primary = _card_token("primary", colors, theme)
        on_primary = _card_token("on_primary", colors, theme)
        return (f"QPushButton#kbSearchChip{{background-color:{card};"
                f"border:1px solid {line2};border-radius:999px;"
                f"color:{text2};padding:3px 14px;font-size:12px;}}"
                f"QPushButton#kbSearchChip:checked{{background-color:{primary};"
                f"border-color:{primary};color:{on_primary};}}")

    def _sync_chip_styles(self):
        colors = self._theme_colors()
        theme = theme_of(_host_window(self._ctx))
        qss = self._chip_qss(colors, theme)
        for chip in self._kind_chips.values():
            chip.setStyleSheet(qss)

    # ---------------- 结果卡片列（v1.5.0） ----------------
    def _on_card_link(self, href):
        """卡片里所有链接（标题/正文/尾行/展开）的统一入口：
        QLabel linkActivated 给的是字符串，转 QUrl 走 _on_anchor 单点分发。"""
        self._on_anchor(QUrl(href))

    def _append_card(self, hit_index, colors, theme):
        """按 self._hits 里的下标造一张卡，插到低相关组之前（主列表尾部）。"""
        card = ResultCard(self, hit_index, self._hits[hit_index],
                          self._frag_meta.get(self._hits[hit_index].uid),
                          colors, theme)
        self._results_lay.insertWidget(
            self._results_lay.indexOf(self._weak_bar), card)
        self._cards.append(card)
        return card

    def _clear_cards(self):
        """清空全部结果卡（新搜索 / 清空输入时）；常驻的低相关组与分页
        按钮只隐藏不销毁。"""
        for card in self._cards + self._weak_cards:
            self._results_lay.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._weak_cards = []
        self._shown = 0
        self._weak_open = False
        self._weak_bar.setVisible(False)
        self._weak_box.setVisible(False)
        self._more_btn.setVisible(False)

    def _set_weak_bar_text(self):
        n = len(self._weak_idx)
        state = "收起" if self._weak_open else "展开"
        self._weak_bar.setText(
            f"{state}低相关 {n} 条（正文顺带命中，如日期里的数字）")

    def _toggle_weak(self):
        self._weak_open = not self._weak_open
        self._weak_box.setVisible(self._weak_open)
        self._set_weak_bar_text()

    def _rebuild_weak_section(self, colors, theme):
        """低相关组重建：有弱相关命中才亮出折叠条；卡是否上屏跟
        _weak_open 走（默认收起）。"""
        for card in self._weak_cards:
            self._results_lay.removeWidget(card)
            card.deleteLater()
        self._weak_cards = []
        if not self._weak_idx:
            self._weak_bar.setVisible(False)
            self._weak_box.setVisible(False)
            self._weak_open = False
            return
        self._weak_bar.setVisible(True)
        self._weak_box.setVisible(self._weak_open)
        self._set_weak_bar_text()
        for i in self._weak_idx:
            card = ResultCard(self, i, self._hits[i],
                              self._frag_meta.get(self._hits[i].uid),
                              colors, theme)
            self._weak_lay.addWidget(card)
            self._weak_cards.append(card)

    def _update_more_button(self):
        remaining = len(self._strong_idx) - self._shown
        self._more_btn.setVisible(remaining > 0)
        if remaining > 0:
            self._more_btn.setText(f"显示更多（剩余 {remaining} 条）")

    def _show_more(self):
        """追加下一页强相关卡：只 insertWidget 新卡，旧卡不重渲染。"""
        colors = self._theme_colors()
        theme = theme_of(_host_window(self._ctx))
        for i in self._strong_idx[self._shown:self._shown + PAGE_SIZE]:
            self._append_card(i, colors, theme)
        self._shown = min(self._shown + PAGE_SIZE, len(self._strong_idx))
        self._update_more_button()

    def _render_cards(self, weak_query=False):
        """按当前分层结果整列重建（搜索换代时才走；展开/追加走增量路径）"""
        self._clear_cards()
        colors = self._theme_colors()
        theme = theme_of(_host_window(self._ctx))
        self._empty.setVisible(not self._hits)
        for i in self._strong_idx[:PAGE_SIZE]:
            self._append_card(i, colors, theme)
        self._shown = min(PAGE_SIZE, len(self._strong_idx))
        self._rebuild_weak_section(colors, theme)
        self._update_more_button()

    def _run_search(self):
        query = self._input.text().strip()
        if not query:
            self._hits = []
            self._strong_idx = []
            self._weak_idx = []
            self._clear_cards()
            self._empty.setVisible(True)
            self._sync_recent_visible()
            return
        kinds = self._selected_kinds()
        if not kinds:
            # 防御分支（chips 单选语义下到不了）：范围为空直接给空结果
            self._hits = []
            self._strong_idx = []
            self._weak_idx = []
            self._clear_cards()
            self._empty.setVisible(True)
            self._stat.setText("没有选中任何数据源范围 ｜ 至少选择一个再搜")
            return
        kind_arg = kinds if len(kinds) < len(KIND_LABEL) else None
        weak_q = core.is_weak_query(query)
        try:
            hits = self._index.search(
                query, top_n=self._max_results, kind=kind_arg,
                ranking=self._ranking, min_score_rel=SEARCH_MIN_SCORE_REL,
                weak_query=weak_q)
        except Exception as exc:                  # noqa: BLE001
            self._hits = []
            self._strong_idx = []
            self._weak_idx = []
            self._clear_cards()
            self._stat.setText(f"检索失败：{exc!r}")
            return
        if not self._search_notes and self._chip_key != "note":
            # 「默认搜索笔记」设置（v1.4.0）：chips 化后在结果层剔除，
            # 显式选「笔记」chip 仍可只搜笔记
            hits = [h for h in hits if h.kind != "note"]
        # ⚠ self._hits 与卡片下标绑定：只整体替换、从不重排，锚点反查才稳
        self._hits = hits
        self._strong_idx = [i for i, h in enumerate(hits)
                            if h.tier == "strong"]
        self._weak_idx = [i for i, h in enumerate(hits) if h.tier == "weak"]
        self._render_cards(weak_q)
        self._remember_history(query)             # K3：有效检索记入历史
        scope = ""
        if kind_arg is not None:
            labels = " / ".join(KIND_LABEL[k] for k in kinds)
            scope = f" ｜ 范围：{labels}"
        n_strong, n_weak = len(self._strong_idx), len(self._weak_idx)
        if not hits:
            self._stat.setText(f"没有匹配「{query}」的内容 ｜ 索引 {self._docs} 条"
                               f"{scope}")
        elif not n_strong:
            self._stat.setText(
                "没有强相关结果——换个更具体的关键词试试"
                "（单字 / 纯数字只匹配标题与开头）"
                f" ｜ 索引 {self._docs} 条{scope}")
        else:
            folded = f" ｜ 低相关 {n_weak} 条已折叠" if n_weak else ""
            self._stat.setText(f"强相关 {n_strong} 条{folded}"
                               f" ｜ 索引 {self._docs} 条{scope}")

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
        """锚点分发：fp-expand → 卡内展开/收起；fp-result → 跳转面板。

        两个 scheme 各有独立解析函数且**互不解析**（见 parse_expand_anchor），
        所以点「展开」绝不会被当成跳转，反之亦然。fp-expand 的状态自持在
        ResultCard 上（v1.5.0）：这里只按下标找到卡再 toggle，展开只动那
        一张卡。宿主侧只暴露一个**公开入口** ``show_search_result(kind,
        keyword, num)``（与 ``show_plugin_page`` 同款约定）；宿主没有这个
        入口时提示一句，不让点击静默失败。
        """
        text = url.toString() if hasattr(url, "toString") else str(url)
        idx = parse_expand_anchor(text)
        if idx is not None:
            if 0 <= idx < len(self._hits):
                card = self._card_for_hit(idx)
                if card is not None:
                    card.toggle_expand()
            return
        idx = parse_result_anchor(text)
        if idx is None or idx >= len(self._hits):
            return
        self._jump_to(self._hits[idx])

    def _card_for_hit(self, hit_index):
        """下标 → 承载它的 ResultCard（主列表 + 低相关组都找）"""
        for card in self._cards + self._weak_cards:
            if card._index == hit_index:
                return card
        return None

    def _theme_colors(self):
        """宿主主题色 dict（卡片样式颜色每次从主题取）；取不到返回 None，
        渲染端走字面量兜底"""
        try:
            from src.theme import get_colors
            return get_colors(theme_of(_host_window(self._ctx)))
        except Exception:                     # noqa: BLE001
            return None

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
    version = "1.5.0"

    def create_actions(self, ctx):
        return [OpenSearchAction()]

    def create_page(self, ctx):
        return SearchPage(ctx)
