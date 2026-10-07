# -*- coding: utf-8 -*-
"""全局「/ 命令面板」 -  command_palette
====================================================================
任意页面按 ``/`` 弹出玻璃拟态命令条：模糊匹配后跳转页面 / 触发宿主动作。

与 Ctrl+K（kb-search 插件页）的**硬性边界**（不得越界）：
  · Ctrl+K 搜**数据内容**（碎片 / 笔记 / 任务 / 素材全文检索）；
  · ``/`` 面板搜**动作与导航**（切页、导出、主题切换、打开设置分类、
    帮助章节深链等宿主动作）。
本模块因此**不引用、不触碰 kb-search 插件任何文件**；与 kb-search 唯一
的交集是两者都挂在主窗口快捷键上，各管各的键位。

实现约束（任务书 A2）：
  · 命令注册表**数据驱动**：``CommandEntry`` 描述 + 纯函数生成 / 过滤 /
    校验，全部可脱离 GUI 单测；
  · 页面跳转复用 nav_order 体系（8 个固定页键 + ``plugin:`` 动态键），
    插件键以「NAV_PAGE_INDEX 现况」为准 —— register/unregister 会同步
    增删该表，其 plugin: 子集就是"此刻真的可跳"的插件页，与 nav 侧
    ``k in _nav_btns`` 防护同口径（键不在表里就不进面板）；
  · 宿主动作一律调 MainWindow **公开 API**（A1 收口后的 D3 只读契约层
    与 show_page / show_settings_page / show_plugin_page / show_help_
    category / export_to_obsidian / apply_external_theme），**禁摸
    _私有成员**；
  · 视觉走 GlassDialog 家族（GlassPanel 玻璃壳 + paintEvent 手绘柔影）；
    图标一律 icon_render 自绘（禁 emoji）；
  · 触发键与直达键可配（2026-10-06 设置页「命令面板」卡）：触发键三选一
    （config.command_palette_trigger，白名单 = constants.TRIGGER_KEYS）、
    命令直达键（config.command_hotkeys，cid → 组合键串）与自定义动作
    （config.custom_actions，url/folder/text 三类，上限 20 条）均落
    config 三件套，读侧一律过本模块的 sanitize_* 纯函数形态校验；
  · 编辑态防误触：``focus_in_text_editor`` 在入口处过滤（QLineEdit /
    QTextEdit / QPlainTextEdit —— QTextBrowser 是 QTextEdit 子类一并
    覆盖）。QLineEdit / QTextEdit 等可编辑控件本身会接受 Qt 的
    ShortcutOverride，``/`` 快捷键根本不触发；只读 QTextBrowser 之类
    不吃 override 的控件由这里二次拦截。
====================================================================
"""

from dataclasses import dataclass
from string import Template

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QTextEdit, QVBoxLayout, QWidget,
)

from src import icon_render
from src.constants import TRIGGER_KEYS
from src.glass import GlassPanel, draw_soft_shadow
from src.controls import SmoothInput
from src.theme import DEFAULT_THEME, get_colors, get_main_window_qss

# ====================================================================
# 纯逻辑层（命令注册表 / 模糊匹配 / 校验 —— 不依赖 QApplication）
# ====================================================================

# 面板分类标签（列表行右侧的灰字）
CATEGORY_PAGE = "页面"
CATEGORY_PLUGIN = "插件"
CATEGORY_ACTION = "动作"
CATEGORY_SETTINGS = "设置"
CATEGORY_HELP = "帮助"
CATEGORY_CUSTOM = "自定义"

# 触发键候选再导出（settings_panel / 测试统一从这里取，constants 只是
# 数据存放点 —— 消费口径以本模块 normalize_trigger 为准）
TRIGGER_KEYS = TRIGGER_KEYS

# 结果列表上限（注册表 ~25 条，命中截断只影响极端模糊查询的列表长度）
MAX_RESULTS = 20

# 自定义动作：合法类型与条数上限（config.custom_actions 读侧清洗口径）
CUSTOM_ACTION_TYPES = ("url", "folder", "text")
MAX_CUSTOM_ACTIONS = 20

# 自定义动作 type → src.icons 图标名（三个名字均已在 icons.py 登记：
# url→nav 链接形、folder→文件夹、text→clipboard 剪贴板）
_CUSTOM_ACTION_ICONS = {"url": "nav", "folder": "folder", "text": "clipboard"}

# 直达键组合键形态校验的修饰键白名单（大小写不敏感；不含 Ctrl+K 这种
# 应用内快捷键 —— 直达键必须是全局热键，至少带一个修饰键）
_COMBO_MODIFIERS = frozenset({"ctrl", "alt", "shift", "win", "meta"})


@dataclass(frozen=True)
class CommandEntry:
    """一条命令的静态描述。

    ``target`` 是执行目标**描述符**（不是绑死的 callable）：数据可单测、
    完备性可校验，真正分派集中在 :func:`execute_entry` 一处。
    """

    cid: str              # 唯一 id（"page.tasks" / "settings.export" / ...）
    title: str            # 列表标题（无 emoji 前缀，图标走 icon 字段）
    icon: str             # src.icons 图标名（icon_render 渲染）
    category: str         # 分类标签（CATEGORY_* 常量）
    target: tuple         # ("page", key) / ("plugin", key) / ("settings", key)
                          # / ("help", key) / ("action", name)
    keywords: tuple = ()  # 额外匹配词（拼音缩写 / 英文别名 / 同义词）


# 固定页的拼音缩写 / 英文别名（模糊匹配的额外关键词；中文标题子串本就
# 能命中，这里补的是「rj → 软件导航」这类首字母速敲路径）
_PAGE_PINYIN = {
    "fragments": ("sp", "suipian", "fragments"),
    "tasks": ("rw", "renwu", "tasks"),
    "notes": ("bj", "biji", "notes"),
    "knowledge": ("zs", "zhishi", "knowledge"),
    "assets": ("sc", "sucai", "assets"),
    "apps": ("rj", "ruanjian", "apps"),
    "nav": ("wz", "wangzhi", "nav"),
    "plugins": ("cj", "chajian", "plugins"),
    "settings": ("sz", "shezhi", "settings"),
    "help": ("sm", "shuoming", "help"),
}


def page_commands(nav_titles, fixed_titles, nav_index) -> list:
    """从宿主导航三张表生成页面命令。

    ``nav_titles`` = 固定功能页表（8 键 + 动态 ``plugin:`` 键）；
    ``fixed_titles`` = 设置 / 使用说明这对非拖拽项（**不在**
    NAV_PAGE_INDEX —— 物理索引 6/8 由宿主硬编码，永远可达，不做过期
    过滤）；``nav_index`` = key → 物理索引，只用于插件键的注册态判定
    （register/unregister 会同步增删该表，「key 在表里」与 nav 侧
    ``k in _nav_btns`` 同口径）。

    固定页在前、插件页随后 —— 顺序即空查询时的展示序。
    """
    from src.icons import NAV_ICON, plugin_page_icon

    index = dict(nav_index or {})
    entries = []
    # settings / help：非拖拽固定项，公开入口（show_settings_page /
    # show_page(HELP_PAGE_INDEX)）永远存在 → 无条件进面板
    for key, title in dict(fixed_titles or {}).items():
        entries.append(CommandEntry(
            cid=f"page.{key}", title=str(title),
            icon=NAV_ICON.get(key, "command"), category=CATEGORY_PAGE,
            target=("page", key), keywords=_PAGE_PINYIN.get(key, ())))
    for key, title in dict(nav_titles or {}).items():
        if key not in index or any(e.cid == f"page.{key}" for e in entries):
            continue
        if key.startswith("plugin:"):
            entries.append(CommandEntry(
                cid=f"page.{key}", title=str(title),
                icon=plugin_page_icon(key), category=CATEGORY_PLUGIN,
                target=("plugin", key),
                keywords=(key.removeprefix("plugin:"),)))
        else:
            entries.append(CommandEntry(
                cid=f"page.{key}", title=str(title),
                icon=NAV_ICON.get(key, "command"), category=CATEGORY_PAGE,
                target=("page", key),
                keywords=_PAGE_PINYIN.get(key, ())))
    return entries


def action_commands() -> list:
    """宿主动作命令（全部经 MainWindow 公开 API 执行，见 execute_entry）。"""
    return [
        CommandEntry(
            cid="action.export", title="导出全部到 Obsidian",
            icon="export", category=CATEGORY_ACTION,
            target=("action", "export"),
            keywords=("daochu", "export", "markdown", "vault")),
        CommandEntry(
            cid="action.theme", title="切换浅色 / 深色主题",
            icon="moon", category=CATEGORY_ACTION,
            target=("action", "toggle_theme"),
            keywords=("zhuti", "theme", "dark", "light", "yuese")),
        CommandEntry(
            cid="action.new_task", title="新建任务",
            icon="plus", category=CATEGORY_ACTION,
            target=("action", "new_task"),
            keywords=("新任务", "待办", "xinjian", "renwu", "new", "task",
                      "todo", "daiban", "daibanshi")),
        CommandEntry(
            cid="action.new_note", title="新建笔记",
            icon="plus", category=CATEGORY_ACTION,
            target=("action", "new_note"),
            keywords=("记笔记", "xinjian", "biji", "new", "note",
                      "jibi", "jb")),
        CommandEntry(
            cid="help.hotkeys", title="查看全局快捷键说明",
            icon="command", category=CATEGORY_HELP,
            target=("help", "hotkeys"),
            keywords=("kuaijiejian", "hotkeys", "help", "shuoming")),
    ]


def settings_category_commands() -> list:
    """「打开设置分类」命令。

    分类表唯一真相源 = ``settings_panel.SETTINGS_CATEGORIES``（惰性
    import：让本模块的纯逻辑部分不被迫背上设置页的重依赖；两表一致性由
    tests/test_command_palette.py 钉死）。
    """
    from src.settings_panel import SETTINGS_CATEGORIES

    return [
        CommandEntry(
            cid=f"settings.{key}", title=f"设置 · {label}",
            icon=icon_name, category=CATEGORY_SETTINGS,
            target=("settings", key), keywords=(key, label))
        for key, icon_name, label in SETTINGS_CATEGORIES
    ]


def build_registry(nav_titles, fixed_titles, nav_index,
                   custom_actions=None) -> list:
    """完整命令注册表：页面（含插件）→ 动作 → 设置分类 → 自定义动作。

    顺序即空查询时的展示序（页面最常跳，排最前）。``custom_actions``
    为非空列表时，把清洗后的自定义动作命令追加在 settings 命令之后
    （与设置页「命令面板」卡的展示顺序一致）。
    """
    entries = (page_commands(nav_titles, fixed_titles, nav_index)
               + action_commands()
               + settings_category_commands())
    if custom_actions:
        entries = entries + custom_action_entries(custom_actions)
    return entries


# ---------------- 自定义动作 / 直达键清洗（纯函数，可单测） ----------------
def is_valid_combo(text) -> bool:
    """组合键串形态校验（轻量纯校验，替代重依赖的 global_hotkey.parse_hotkey
    —— 后者 ctypes.windll 仅 Windows 可用，离屏 / 跨平台测试不可依赖）。

    规则：按 "+" 拆分；修饰键段 ∈ {Ctrl, Alt, Shift, Win, Meta}
    （大小写不敏感）且至少一个；末段为单字符（字母/数字）或 F1-F24。
    """
    if not isinstance(text, str) or not text.strip():
        return False
    tokens = [t.strip() for t in text.split("+") if t.strip()]
    if len(tokens) < 2:
        return False                      # 必须至少带一个修饰键
    mods = [t.lower() for t in tokens[:-1]]
    if any(m not in _COMBO_MODIFIERS for m in mods):
        return False
    last = tokens[-1].lower()
    if len(last) == 1 and (last.isalpha() or last.isdigit()):
        return True
    if (len(last) >= 2 and last[0] == "f" and last[1:].isdigit()
            and 1 <= int(last[1:]) <= 24):
        return True
    return False


def sanitize_custom_actions(raw) -> list:
    """清洗 config.custom_actions：非法条目逐条丢弃，返回干净的 list[dict]。

    丢弃条件（逐条独立判定）：非 dict / type 不在 CUSTOM_ACTION_TYPES /
    title 或 value 非法（非 str 或去空白后为空）/ hotkey 非法串置 ""。
    id 缺失、非正整数或与既有 id 重复 → 重新分配（从现有 max+1 起递增找空位）。
    上限 MAX_CUSTOM_ACTIONS（20）条，超出丢弃。
    """
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen_ids = set()
    next_id = 1
    for item in raw:
        if len(cleaned) >= MAX_CUSTOM_ACTIONS:
            break
        if not isinstance(item, dict):
            continue
        atype = item.get("type")
        title = item.get("title")
        value = item.get("value")
        if atype not in CUSTOM_ACTION_TYPES:
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        hotkey = item.get("hotkey", "")
        if not is_valid_combo(hotkey):
            hotkey = ""
        aid = item.get("id")
        if not isinstance(aid, int) or isinstance(aid, bool) or aid < 1 \
                or aid in seen_ids:
            aid = next_id               # 缺失/重复 → 从现有 max+1 起重排
        while aid in seen_ids:
            aid += 1
        seen_ids.add(aid)
        next_id = max(next_id, aid + 1)
        cleaned.append({"id": aid, "type": atype, "title": title,
                        "value": value, "hotkey": hotkey})
    return cleaned


def sanitize_hotkeys_map(raw) -> dict:
    """清洗 config.command_hotkeys（cid → 组合键串映射）。

    键须为非空 str，值须过 :func:`is_valid_combo` 形态校验（至少一个
    修饰键 + 单字符 / F1-F24 末段）；无效整对丢弃，非 dict 回 {}。
    """
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for cid, combo in raw.items():
        if not isinstance(cid, str) or not cid:
            continue
        if not is_valid_combo(combo):
            continue
        cleaned[cid] = combo
    return cleaned


def custom_action_entries(actions) -> list:
    """清洗后的自定义动作列表 → CommandEntry 列表。

    cid = ``custom.<id>``；图标按 type 映射（url→nav / folder→folder /
    text→clipboard）；keywords 带上 type 便于按「网址」「文件夹」类词检索。
    输入先过 :func:`sanitize_custom_actions`（幂等，垃圾数据进不来）。
    """
    entries = []
    for a in sanitize_custom_actions(actions):
        entries.append(CommandEntry(
            cid=f"custom.{a['id']}", title=a["title"],
            icon=_CUSTOM_ACTION_ICONS[a["type"]],
            category=CATEGORY_CUSTOM, target=("custom", str(a["id"])),
            keywords=(a["type"],)))
    return entries


def normalize_trigger(raw):
    """触发键归一：raw 在 TRIGGER_KEYS 内原样返回，否则回落 "/"。"""
    return raw if raw in TRIGGER_KEYS else "/"


# ---------------- 模糊匹配（纯函数，可单测） ----------------
def _is_subsequence(needle: str, haystack: str) -> bool:
    """``needle`` 的字符是否按序出现在 ``haystack`` 中（不要求连续）。"""
    it = iter(haystack)
    return all(ch in it for ch in needle)


def _subsequence_score(needle: str, haystack: str) -> int:
    """子序列命中打分：越紧凑（最长连续段越长）越高 —— 首字母缩写与
    「真正想打的前缀」比散落命中更值钱。不命中返回 0。"""
    if not _is_subsequence(needle, haystack):
        return 0
    run = best = 0
    prev = None
    pos = 0
    for ch in needle:
        idx = haystack.find(ch, pos)
        run = run + 1 if (prev is not None and idx == prev + 1) else 1
        best = max(best, run)
        prev = idx
        pos = idx + 1
    return 20 + best * 6


def match_score(entry: CommandEntry, query: str) -> int:
    """query 对单条命令的命中分（0 = 不命中）。

    优先级：标题子串(100) > 关键词子串(80) > 标题子序列(20+) >
    关键词子序列(15+)；全小写比较（中文不受影响，英文别名大小写无关）。
    """
    q = (query or "").strip().lower()
    if not q:
        return 0
    title = entry.title.lower()
    if q in title:
        return 100
    for kw in entry.keywords:
        if q in str(kw).lower():
            return 80
    score = _subsequence_score(q, title)
    if score:
        return score
    for kw in entry.keywords:
        score = _subsequence_score(q, str(kw).lower())
        if score:
            return 15 + score - 20     # 关键词子序列整体压到标题档之下
    return 0


def filter_commands(entries, query: str) -> list:
    """按 query 过滤排序；空 query → 全量原序。同分保持注册表序（稳定）。"""
    q = (query or "").strip().lower()
    if not q:
        return list(entries)
    scored = []
    for i, entry in enumerate(entries):
        score = match_score(entry, q)
        if score > 0:
            scored.append((-score, i, entry))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _, _, e in scored]


def validate_registry(entries, nav_titles, fixed_titles, nav_index) -> list:
    """注册表完备性自检（护栏用）：返回问题清单，空 = 通过。

    检查：已注册页键全覆盖 / cid 唯一 / 字段非空 / target 结构合法。
    """
    problems = []
    entries = list(entries)
    page_keys = {e.target[1] for e in entries
                 if len(e.target) == 2 and e.target[0] in ("page", "plugin")}
    titles, fixed, index = dict(nav_titles or {}), dict(fixed_titles or {}), \
        dict(nav_index or {})
    # 必须覆盖的页键 = 已注册的功能页（含插件）∪ 设置/说明这对固定项
    # （fixed 项不在 index 里 —— 见 page_commands 的说明 —— 无条件必查）
    required = {k for k in titles if k in index} | set(fixed)
    for key in sorted(required):
        if key not in page_keys:
            problems.append(f"缺少页面命令：{key}")
    cids = set()
    for e in entries:
        if e.cid in cids:
            problems.append(f"cid 重复：{e.cid}")
        cids.add(e.cid)
        if not e.title or not e.icon or not e.category:
            problems.append(f"字段为空：{e.cid}")
        if len(e.target) != 2 or not e.target[1]:
            problems.append(f"target 非法：{e.cid} -> {e.target!r}")
    return problems


# ---------------- 执行分派（只走宿主公开 API） ----------------
def execute_entry(entry, host) -> bool:
    """按 ``target`` 分派到宿主公开 API。未知目标 / 宿主缺能力 → False。

    公开面清单（A1 收口后核实）：
      show_page(idx) / show_settings_page(cat) / show_plugin_page(key)
      / show_help_category(key) / export_to_obsidian() /
      apply_external_theme(name) / focus_new_task() / focus_new_note() /
      current_theme / HELP_PAGE_INDEX / NAV_PAGE_INDEX。
    """
    if entry is None or len(entry.target) != 2:
        return False
    kind, key = entry.target
    if kind == "plugin":
        return bool(host.show_plugin_page(key))
    if kind == "settings":
        host.show_settings_page(key)
        return True
    if kind == "page":
        if key == "settings":
            host.show_settings_page()
            return True
        index = dict(getattr(host, "NAV_PAGE_INDEX", {}) or {}).get(key)
        if index is None:
            return False
        host.show_page(index)
        return True
    if kind == "help":
        host.show_page(int(getattr(host, "HELP_PAGE_INDEX", 8)))
        host.show_help_category(key)
        return True
    if kind == "action":
        return _run_action(key, host)
    if kind == "custom":
        # 自定义动作：宿主公开 API（main_window.execute_custom_action）
        return bool(host.execute_custom_action(key))
    return False


def _run_action(name: str, host) -> bool:
    """动作名 → 宿主公开 API。未登记的动作名返回 False（不猜）。"""
    if name == "export":
        host.export_to_obsidian()
        return True
    if name == "toggle_theme":
        target = "light" if host.current_theme == "dark" else "dark"
        host.apply_external_theme(target)
        return True
    if name == "new_task":
        host.focus_new_task()
        return True
    if name == "new_note":
        host.focus_new_note()
        return True
    return False


# ---------------- 编辑态防误触 ----------------
def focus_in_text_editor(widget) -> bool:
    """焦点控件是否处于文本编辑态（``/`` 应原样输入，不得弹面板）。

    QTextBrowser 是 QTextEdit 子类，一并覆盖；``None``（无焦点）不拦。
    """
    if widget is None:
        return False
    return isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit))


# ====================================================================
# GUI 层（玻璃拟态命令条）
# ====================================================================

# 面板专属 QSS（主窗 QSS 之外的增量；token 取自 get_colors，双主题各算一份）
_PALETTE_QSS = """
#paletteInput {
    background: $input_bg; color: $text;
    border: 1px solid $line_2; border-radius: ${r_ctl}px;
    padding: 7px 10px; font-size: $fs_sm;
}
#paletteInput:focus { border: 1px solid $focus_ring; }
#paletteList { background: transparent; outline: none; }
#paletteList::item { border-radius: ${r_ctl}px; margin: 1px 2px; }
#paletteList::item:selected { background: $primary_a12; }
#paletteList::item:hover { background: $primary_a08; }
#paletteRowTitle { color: $text; font-size: $fs_sm; background: transparent; }
#paletteRowCat { color: $text_placeholder; font-size: $fs_xs;
    background: transparent; }
#paletteEmpty { color: $text_placeholder; font-size: $fs_sm;
    background: transparent; }
#paletteSep { background: $hair; }
"""


class CommandPalette(QDialog):
    """全局命令面板：顶部搜索框 + 结果列表，↑↓ 选择 / 回车执行 / Esc 关闭。

    窗口形态 = Qt.Popup（点外面自动关）+ 无边框 + 半透明（圆角真生效）
    + 常置顶（压过小卡片/钉屏等置顶工具窗），
    玻璃壳复用 GlassPanel，配色/QSS 跟随宿主当前主题。
    """

    SHADOW_MARGIN = 14
    RADIUS = 12
    WIDTH = 560
    HEIGHT = 420
    ROW_HEIGHT = 36

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._entries = []
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        # ---- 玻璃壳 + 阴影边距（体例照 GlassDialog）----
        self._container = GlassPanel(self, radius=self.RADIUS)
        self._container.setObjectName("mainWindow")
        outer = QGridLayout(self)
        m = self.SHADOW_MARGIN
        outer.setContentsMargins(m, m, m, m)
        outer.addWidget(self._container)

        self._build_body()
        self.apply_theme()

    # ---------------- 界面构建 ----------------
    def _build_body(self):
        """构建面板主体（搜索行 + 分隔线 + 结果列表 / 空态）。"""
        root = QVBoxLayout(self._container)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        search_row = QHBoxLayout()
        self._search_icon = QLabel()
        self._search_icon.setFixedSize(18, 18)
        search_row.addWidget(self._search_icon)
        self._input = SmoothInput()
        self._input.setObjectName("paletteInput")
        self._input.setPlaceholderText("跳转页面或执行动作（↑↓ 选择，回车执行）")
        self._input.setClearButtonEnabled(True)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._on_query_changed)
        self._input.returnPressed.connect(self.execute_current)
        search_row.addWidget(self._input, 1)
        root.addLayout(search_row)

        self._sep = QFrame()
        self._sep.setObjectName("paletteSep")
        self._sep.setFixedHeight(1)
        root.addWidget(self._sep)

        self._list = QListWidget()
        self._list.setObjectName("paletteList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerItem)
        self._list.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self._list, 1)

        self._empty = QLabel("没有匹配的命令")
        self._empty.setObjectName("paletteEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        root.addWidget(self._empty, 1)

        # 注册表初始构建（面板本体懒构建，首次按触发键才创建）；
        # 弹出时 open_at 会先 reload_registry 按宿主配置现算一遍
        # （自定义动作 / 直达键变更无需主动广播），这里只做首次预热。
        self.reload_registry()

    # ---------------- 数据刷新 ----------------
    def reload_registry(self):
        """按宿主导航三张表 + config.custom_actions 重建命令注册表。

        公开方法：设置页改动自定义动作后无需主动广播——open_at 每次弹出
        前调一次（build_registry 是纯 Python，量级 ~25 条，微秒级）。
        """
        host = self._host
        cfg = getattr(host, "config", None)
        custom = (cfg.get("custom_actions", []) or []) if cfg is not None \
            else None
        self._entries = build_registry(
            getattr(host, "NAV_PAGE_TITLES", {}) or {},
            getattr(host, "NAV_PAGE_TITLES_FIXED", {}) or {},
            getattr(host, "NAV_PAGE_INDEX", {}) or {},
            custom_actions=custom)

    # ---------------- 主题 ----------------
    def apply_theme(self):
        """复用主窗 QSS + 玻璃壳配色 + 面板专属增量（双主题各算一份）。"""
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        colors = get_colors(theme)
        host_container = getattr(self._host, "container", None)
        qss = host_container.styleSheet() if host_container is not None else ""
        if not qss:
            qss = get_main_window_qss(theme)
        self._container.setStyleSheet(
            qss + Template(_PALETTE_QSS).safe_substitute(colors))
        self._container.apply_theme(colors)
        self._search_icon.setPixmap(icon_render.icon_pixmap(
            "search", 18, colors["text_secondary"]))
        # 行图标是位图，QSS 刷不到 —— 重建列表换新配色
        self._on_query_changed(self._input.text())

    def _on_query_changed(self, text: str):
        """按当前 query 重建结果列表（条目量级 ~25，重建零压力）。"""
        hits = filter_commands(self._entries, text)[:MAX_RESULTS]
        self._list.clear()
        colors = get_colors(getattr(self._host, "current_theme", None)
                            or DEFAULT_THEME)
        for entry in hits:
            self._add_row(entry, colors)
        self._empty.setVisible(not hits)
        self._list.setVisible(bool(hits))
        if hits:
            self._list.setCurrentRow(0)

    def _add_row(self, entry: CommandEntry, colors: dict):
        """一行结果：自绘图标 + 标题 + 右侧键位徽标（可选）+ 分类灰字。"""
        item = QListWidgetItem(self._list)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setPixmap(icon_render.icon_pixmap(
            entry.icon, 16, colors["text_secondary"]))
        title = QLabel(entry.title)
        title.setObjectName("paletteRowTitle")
        cat = QLabel(entry.category)
        cat.setObjectName("paletteRowCat")
        h.addWidget(icon_lbl)
        h.addWidget(title)
        h.addStretch()
        # 直达键徽标：该命令绑了全局直达键时在分类灰字前展示键位串
        # （小字同 paletteRowCat 样式）。config 每次重建行时现读，
        # 设置页改动后无需任何缓存失效逻辑。
        combo = self._host_hotkeys().get(entry.cid)
        if combo:
            badge = QLabel(str(combo))
            badge.setObjectName("paletteRowCat")
            h.addWidget(badge)
        h.addWidget(cat)
        item.setSizeHint(QSize(0, self.ROW_HEIGHT))
        self._list.setItemWidget(item, row)

    def _host_hotkeys(self) -> dict:
        """宿主 config 的 command_hotkeys（过形态校验）；无宿主配置回 {}。"""
        cfg = getattr(self._host, "config", None)
        if cfg is None:
            return {}
        return sanitize_hotkeys_map(cfg.get("command_hotkeys", {}) or {})

    # ---------------- 键盘 / 交互 ----------------
    def eventFilter(self, obj, event):
        """搜索框按键接管：↑↓ 移动选择、回车执行、Esc 关闭。"""
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.execute_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Esc 关闭（Popup 常态下系统已处理，这里是离屏 / 兜底路径）。"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int):
        count = self._list.count()
        if count <= 0:
            return
        row = self._list.currentRow() + delta
        self._list.setCurrentRow(max(0, min(row, count - 1)))

    def _on_item_clicked(self, item):
        self._execute_item(item)

    def _execute_item(self, item) -> bool:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if execute_entry(entry, self._host):
            self.close()
            return True
        return False      # 分派失败（未知目标）保持面板打开，不给假反馈

    def execute_current(self) -> bool:
        """执行当前选中命令；成功后关闭面板。"""
        item = self._list.currentItem()
        return self._execute_item(item) if item is not None else False

    # ---------------- 打开 / 关闭 ----------------
    def open_at(self):
        """刷新注册表（自定义动作/直达键现算）→ 清空查询 → 定位弹出。"""
        self.reload_registry()
        self._input.clear()
        self._on_query_changed("")
        host = self._host
        if isinstance(host, QWidget):
            geo = host.frameGeometry()
        else:
            screen = QApplication.primaryScreen()
            geo = (screen.availableGeometry() if screen is not None
                   else self.screen().geometry())
        center = geo.center() if geo is not None else QPoint(0, 0)
        x = center.x() - self.width() // 2
        y = geo.top() + int(geo.height() * 0.22)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.FocusReason.PopupFocusReason)

    # ---------------- 阴影 ----------------
    def paintEvent(self, event):
        """手绘外圈柔和阴影（与 GlassDialog 同款做法）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.SHADOW_MARGIN
        base = QRectF(self.rect()).adjusted(m, m, -m, -m)
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        alpha = 96 if theme == "dark" else 48
        draw_soft_shadow(painter, base, self.RADIUS,
                         layers=6, max_alpha=alpha, offset_y=5.0)
        painter.end()
        super().paintEvent(event)
