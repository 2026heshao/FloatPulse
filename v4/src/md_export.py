# -*- coding: utf-8 -*-
"""
====================================================================
Obsidian Markdown 导出模块  -  md_export
====================================================================
把 FloatPulse 里的笔记 / 碎片 / 任务**单向导出**成 Obsidian vault 可用的
Markdown 文件，目的是打破数据孤岛：用户在悬浮球里随手写的东西，不应该被
锁死在只有 FloatPulse 能读的 json 里。

设计要点：
  1. **纯逻辑层，禁止 import PyQt6**（便于 pytest 直接跑）。
     数据来源是「鸭子类型」对象（Note / Fragment / Task 均可，
     测试里用 SimpleNamespace 代替），本模块不持有任何管理器实例。
  2. **单向导出**：只写 md，不读回、不做冲突检测、不做定时导出。
     因此重复导出同一份数据 == 覆盖同名文件，天然幂等。
  3. 目录布局固定在 vault 下的 FloatPulse/ 子目录内，避免污染用户 vault 根目录：
       <vault>/FloatPulse/笔记/<标题>.md      每篇笔记一个文件
       <vault>/FloatPulse/碎片/YYYY-MM-DD.md  按天聚合（几百条碎片不会炸出几百个文件）
       <vault>/FloatPulse/任务/任务汇总.md    单文件汇总（任务流动性强，不适合每任务一个文件）
  4. **编码铁律**：一律 ``open(path, "w", encoding="utf-8", newline="\\n")``。
     Windows 下不显式指定 newline 会写出 \\r\\n，Obsidian 部分插件会把 \\r 显示成异常符号。
  5. **任何单条记录渲染/写入失败都进 errors 并继续处理下一条**，
     不中途抛出中断整批导出。

模块导出：
  - safe_filename(name, maxlen=60)           Windows 文件名安全化（结果稳定）
  - build_frontmatter(...)                   YAML frontmatter 生成
  - render_note(note)                        -> (filename, markdown)
  - render_fragment_day(date_str, fragments) -> (filename, markdown)
  - render_tasks(tasks)                      -> (filename, markdown)
  - export_all(vault_dir, notes, fragments, tasks, opts) -> ExportResult
  - ExportResult                             files_written / overwritten / skipped / errors / total_ms
====================================================================
"""

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date

# ---- 外部中文标签映射（均为纯 Python 模块；防御性导入，缺失时回退原值） ----
try:
    from src.fragment_manager import TYPE_LABELS as _TYPE_LABELS
except Exception:                                    # noqa: BLE001
    _TYPE_LABELS = {}

try:
    from src.fragment_classifier import CATEGORY_LABELS as _CATEGORY_LABELS
except Exception:                                    # noqa: BLE001
    _CATEGORY_LABELS = {}

try:
    from src.task_manager import format_relative_deadline as _relative_deadline
except Exception:                                    # noqa: BLE001
    _relative_deadline = None

try:
    from src.note_manager import Note as _Note
    TEMP_NOTE_TITLE = _Note.TEMP_NOTE_TITLE
except Exception:                                    # noqa: BLE001
    TEMP_NOTE_TITLE = "📌 临时笔记"


# ====================================================================
# 常量
# ====================================================================
EXPORT_ROOT_NAME = "FloatPulse"      # vault 内的固定子目录（不污染 vault 根）
SUBDIR_NOTES = "笔记"
SUBDIR_FRAGMENTS = "碎片"
SUBDIR_TASKS = "任务"
TASKS_FILENAME = "任务汇总.md"

TAG_VALUE = "FloatPulse"             # frontmatter 固定 tag
SOURCE_NOTE = "笔记"
SOURCE_FRAGMENT = "碎片"
SOURCE_TASK = "任务"
UNKNOWN_DAY = "未知日期"              # created_at 缺失/畸形时的聚合日

FALLBACK_FILENAME = "untitled"       # 安全化后为空时的兜底名

# Windows 文件名非法字符（含控制字符由正则另行剔除）
_ILLEGAL_CHARS = '\\/:*?"<>|'
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
# Windows 保留设备名（不区分大小写；带扩展名同样非法）
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


# ====================================================================
# 文件名安全化
# ====================================================================
def safe_filename(name, maxlen: int = 60) -> str:
    """把任意文本安全化为可用的 Windows 文件名（不含扩展名）。

    处理规则（**纯函数，同名输入永远得同名输出**，这是重复导出幂等的前提）：
      1. None → 空串；非字符串 → ``str()`` 转换
      2. 剔除全部控制字符（0x00-0x1F、0x7F），非法字符 ``\\ / : * ? " < > |`` 替换为 ``_``
      3. 去首尾空白；去掉结尾的点（Windows 下结尾点会被静默丢弃，导致同名碰撞）
      4. 命中 Windows 保留设备名（CON / NUL / COM1 …）→ 加 ``_`` 前缀
      5. 超过 ``maxlen`` 截断；截断后若结尾是点或空格再次清理
      6. 结果为空 → 返回 ``FALLBACK_FILENAME``

    :param name: 原始名称（标题 / 日期串等）
    :param maxlen: 截断长度上限，默认 60
    :return: 安全化后的文件名（不含扩展名），非空
    """
    if name is None:
        text = ""
    elif isinstance(name, str):
        text = name
    else:
        text = str(name)

    # 2. 控制字符剔除 + 非法字符替换
    text = _ILLEGAL_RE.sub("_", text)

    # 3. 首尾空白与结尾点
    text = text.strip()
    text = text.rstrip(".")
    text = text.strip()

    # 5. 截断（截断后可能又露出结尾点/空格）
    if maxlen is not None and maxlen > 0 and len(text) > maxlen:
        text = text[:maxlen]
        text = text.rstrip(". ").strip()

    # 4. 保留设备名
    if text.upper() in _RESERVED_NAMES:
        text = "_" + text

    # 6. 兜底
    if not text:
        return FALLBACK_FILENAME
    return text


# ====================================================================
# YAML frontmatter
# ====================================================================
def _yaml_str(value) -> str:
    """把值序列化为 YAML 双引号字符串（转义 ``\\`` 与 ``"``）。

    统一加引号的理由：时间戳（``2026-09-27 12:30``）、含 ``:`` / ``#`` 的
    任意文本在 YAML 里都可能被误解析，加引号是最省心的稳法。
    """
    s = "" if value is None else str(value)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_frontmatter(created="", updated="", source=None, category=None,
                      tags=(TAG_VALUE,)) -> str:
    """生成 YAML frontmatter 三横线块（不含结尾换行）。

    - ``tags`` 用 Obsidian 的 YAML 列表语法 ``tags: [FloatPulse]``，**不写成字符串**
    - ``source`` / ``category`` 为 None 时整行省略（笔记没有来源/类别概念）
    - 空值字段（created/updated）也省略，避免产生 ``created: ""`` 噪音
    """
    lines = ["---"]
    if created:
        lines.append(f"created: {_yaml_str(created)}")
    if updated:
        lines.append(f"updated: {_yaml_str(updated)}")
    if tags:
        lines.append("tags: [" + ", ".join(str(t) for t in tags) + "]")
    if source is not None:
        lines.append(f"source: {_yaml_str(source)}")
    if category is not None:
        lines.append(f"category: {_yaml_str(category)}")
    lines.append("---")
    return "\n".join(lines)


def _escape_leading_fence(body: str) -> str:
    """内容体以 ``---`` 开头时转义开头的连续短横。

    不处理的后果：正文首行 ``---`` 紧跟在 frontmatter 的收尾 ``---`` 之后，
    部分解析器会把两个围栏当成同一个未闭合的 frontmatter 块，元数据被吞掉。
    转义方式：把开头的连续 ``-`` 逐个前置反斜杠（``\\-\\-\\-``），
    CommonMark 下仍渲染为 ``---``，但不再被识别为围栏。
    """
    stripped = body.lstrip("\r\n")
    if not stripped.startswith("---"):
        return body
    i = 0
    while i < len(stripped) and stripped[i] == "-":
        i += 1
    escaped = "".join("\\" + ch for ch in stripped[:i]) + stripped[i:]
    return escaped


def _compose(frontmatter: str, body: str) -> str:
    """拼接 frontmatter 与正文：中间空一行，结尾恰好一个换行。"""
    body = _escape_leading_fence(body or "")
    text = frontmatter + "\n\n" + body
    return text.rstrip("\n") + "\n"


# ====================================================================
# 三个渲染器
# ====================================================================
def _note_title(note) -> str:
    title = getattr(note, "title", "") or ""
    return str(title)


def render_note(note):
    """单篇笔记 → ``(filename, markdown)``。正文直接放笔记内容。"""
    title = _note_title(note)
    filename = safe_filename(title) + ".md"
    frontmatter = build_frontmatter(
        created=getattr(note, "create_time", "") or "",
        updated=getattr(note, "update_time", "") or "",
        source=SOURCE_NOTE,
    )
    body = getattr(note, "content", "") or ""
    return filename, _compose(frontmatter, str(body))


def _fragment_time(frag) -> str:
    """从 ``created_at``（"YYYY-MM-DD HH:MM"）取 "HH:MM" 部分。"""
    ts = str(getattr(frag, "created_at", "") or "")
    return ts[11:16] if len(ts) >= 16 else ts


def render_fragment_day(date_str, fragments):
    """某一天的全部碎片 → ``(filename, markdown)``。

    每条碎片以二级标题区分，标题里带 **时间 / 来源 type / 内容类别 category**
    三个维度（两个维度都要体现，不是只写一个）。
    """
    items = list(fragments or [])
    filename = safe_filename(date_str) + ".md"
    frontmatter = build_frontmatter(
        created=str(date_str or ""),
        updated=str(date_str or ""),
        source=SOURCE_FRAGMENT,
    )
    lines = [f"# {date_str} 碎片", "", f"共 {len(items)} 条"]
    for frag in items:
        type_label = _TYPE_LABELS.get(getattr(frag, "type", ""),
                                      getattr(frag, "type", "") or "")
        cat_label = _CATEGORY_LABELS.get(getattr(frag, "category", ""),
                                         getattr(frag, "category", "") or "")
        lines.append("")
        lines.append(f"## {_fragment_time(frag)} · {type_label} · {cat_label}")
        lines.append("")
        lines.append(str(getattr(frag, "content", "") or ""))
    return filename, _compose(frontmatter, "\n".join(lines))


def _cell(value) -> str:
    """Markdown 表格单元格转义：``|`` → ``\\|``，换行 → ``<br>``。"""
    s = "" if value is None else str(value)
    return s.replace("\r", "").replace("\n", "<br>").replace("|", "\\|")


def _deadline_text(task) -> str:
    """截止日文案：原始日期 +（可用时）相对时间标签。"""
    raw = str(getattr(task, "deadline", "") or "")
    if not raw:
        return "—"
    if _relative_deadline is not None:
        try:
            rel = _relative_deadline(raw)
        except Exception:                            # noqa: BLE001
            rel = ""
        if rel:
            return f"{raw}（{rel}）"
    return raw


def _focus_text(task) -> str:
    try:
        n = int(getattr(task, "focus_sessions", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return f"🍅×{n}" if n > 0 else ""

def render_tasks(tasks):
    """全部任务 → ``(filename, markdown)``（单文件汇总）。

    按是否完成分组，**未完成在前**；每行带 deadline 与 focus_sessions。
    """
    items = list(tasks or [])
    filename = TASKS_FILENAME
    today = date.today().isoformat()
    frontmatter = build_frontmatter(created=today, updated=today,
                                    source=SOURCE_TASK)
    undone = [t for t in items if not bool(getattr(t, "done", False))]
    done = [t for t in items if bool(getattr(t, "done", False))]

    lines = ["# 任务汇总", "",
             f"共 {len(items)} 条（未完成 {len(undone)} / 已完成 {len(done)}）"]
    for group_title, group in (("未完成", undone), ("已完成", done)):
        lines += ["", f"## {group_title}", ""]
        if not group:
            lines.append("_（无）_")
            continue
        lines.append("| 任务 | 截止 | 番茄 | 备注 |")
        lines.append("|---|---|---|---|")
        for task in group:
            lines.append("| {} | {} | {} | {} |".format(
                _cell(getattr(task, "title", "") or ""),
                _cell(_deadline_text(task)),
                _cell(_focus_text(task)),
                _cell(getattr(task, "note", "") or ""),
            ))
    return filename, _compose(frontmatter, "\n".join(lines))


# ====================================================================
# 导出编排
# ====================================================================
@dataclass
class ExportResult:
    """一次导出的结果汇总。

    - ``files_written``: 本次写入的文件绝对路径（按导出顺序）
    - ``overwritten``:   写入前已存在、被本次覆盖的文件路径（单向导出语义：覆盖）
    - ``skipped``:       被跳过的记录 ``(原因, 标识)``，如临时笔记
    - ``errors``:        失败明细 ``(标识, 原因)``；单条失败不中断整批
    - ``total_ms``:      总耗时（毫秒）
    """
    vault_dir: str = ""
    root_dir: str = ""
    files_written: list = field(default_factory=list)
    overwritten: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        """给 UI/日志用的一句话摘要。"""
        if self.errors:
            return (f"共 {len(self.files_written)} 个文件，"
                    f"失败 {len(self.errors)} 条")
        return f"共 {len(self.files_written)} 个文件"


def _validate_vault(vault_dir) -> str:
    """校验导出目录，返回错误原因（None 表示合法）。"""
    if not vault_dir or not isinstance(vault_dir, str):
        return "未选择导出目录（vault 路径为空）"
    if not os.path.exists(vault_dir):
        return f"导出目录不存在：{vault_dir}"
    if not os.path.isdir(vault_dir):
        return f"导出路径不是文件夹：{vault_dir}"
    return None


def _write_text(path: str, text: str):
    """写文件：强制 utf-8 + LF（Windows 下必须显式指定 newline）。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _group_fragments_by_day(fragments) -> dict:
    """按 ``created_at`` 的日期部分聚合碎片，保持「日期 → 列表」顺序稳定。"""
    groups = {}
    for frag in fragments or []:
        ts = str(getattr(frag, "created_at", "") or "")
        day = ts[:10] if len(ts) >= 10 else UNKNOWN_DAY
        if not day:
            day = UNKNOWN_DAY
        groups.setdefault(day, []).append(frag)
    return groups


def _note_id_of(note) -> int:
    """安全取 note_id（畸形值一律当作 0，不让排序炸掉整批导出）。"""
    try:
        return int(getattr(note, "note_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _unique_note_names(notes) -> dict:
    """为笔记分配稳定文件名：``id(note) -> filename``。

    - 按 ``note_id`` 升序分配（与 UI 的按更新时间排序无关），保证同一批输入
      每次得到完全相同的文件名 → 重复导出幂等
    - 同名冲突时追加 ``-<note_id>``，仍冲突再追加序号
    """
    used = {}
    mapping = {}
    ordered = sorted(notes, key=_note_id_of)
    for note in ordered:
        base = safe_filename(_note_title(note))
        name = base
        if name in used:
            name = f"{base}-{_note_id_of(note)}"
            k = 2
            while name in used:
                name = f"{base}-{_note_id_of(note)}-{k}"
                k += 1
        used[name] = note
        mapping[id(note)] = name
    return mapping


def export_all(vault_dir, notes=None, fragments=None, tasks=None, opts=None):
    """把笔记 / 碎片 / 任务导出到 ``vault_dir``，返回 :class:`ExportResult`。

    :param vault_dir: Obsidian vault 根目录（内部会建 ``FloatPulse/`` 子目录）
    :param notes:     笔记对象序列（Note 或等价鸭子类型）
    :param fragments: 碎片对象序列
    :param tasks:     任务对象序列
    :param opts:      开关字典，键 ``export_notes`` / ``export_fragments`` /
                      ``export_tasks``（缺省均视为 True）
    :return: :class:`ExportResult`

    容错承诺：vault 非法 → 只记一条 error 并返回空结果；
    任一条记录渲染/写入失败 → 记入 errors 后继续，不中断整批。
    """
    started = time.perf_counter()
    opts = opts or {}
    result = ExportResult(vault_dir=str(vault_dir or ""))

    err = _validate_vault(vault_dir)
    if err:
        result.errors.append((str(vault_dir or ""), err))
        result.total_ms = (time.perf_counter() - started) * 1000.0
        return result

    root = os.path.join(vault_dir, EXPORT_ROOT_NAME)
    result.root_dir = root

    def _emit(path: str, text: str):
        """写一个文件并登记结果（覆盖时同时记入 overwritten）。"""
        if os.path.exists(path):
            result.overwritten.append(path)
        _write_text(path, text)
        result.files_written.append(path)

    # ---------------- 笔记 ----------------
    note_list = list(notes or [])
    if opts.get("export_notes", True):
        try:
            os.makedirs(os.path.join(root, SUBDIR_NOTES), exist_ok=True)
        except OSError as exc:
            result.errors.append((os.path.join(root, SUBDIR_NOTES),
                                  f"创建目录失败：{exc}"))
        else:
            names = _unique_note_names(note_list)
            for note in note_list:
                title = _note_title(note)
                if title == TEMP_NOTE_TITLE:
                    # 临时笔记是写了一半的草稿，不是用户的正式笔记 → 跳过
                    result.skipped.append(("临时笔记", title))
                    continue
                try:
                    filename, markdown = render_note(note)
                    # 用分配好的稳定名，保证同名笔记不互相覆盖
                    filename = names.get(id(note), filename[:-3]) + ".md"
                    _emit(os.path.join(root, SUBDIR_NOTES, filename), markdown)
                except Exception as exc:             # noqa: BLE001
                    result.errors.append((f"笔记 {title!r}", repr(exc)))

    # ---------------- 碎片（按天聚合） ----------------
    if opts.get("export_fragments", True):
        try:
            os.makedirs(os.path.join(root, SUBDIR_FRAGMENTS), exist_ok=True)
        except OSError as exc:
            result.errors.append((os.path.join(root, SUBDIR_FRAGMENTS),
                                  f"创建目录失败：{exc}"))
        else:
            groups = _group_fragments_by_day(fragments)
            for day, day_frags in groups.items():
                try:
                    filename, markdown = render_fragment_day(day, day_frags)
                    _emit(os.path.join(root, SUBDIR_FRAGMENTS, filename),
                          markdown)
                except Exception as exc:             # noqa: BLE001
                    result.errors.append((f"碎片 {day}", repr(exc)))

    # ---------------- 任务（单文件汇总） ----------------
    if opts.get("export_tasks", True):
        try:
            os.makedirs(os.path.join(root, SUBDIR_TASKS), exist_ok=True)
        except OSError as exc:
            result.errors.append((os.path.join(root, SUBDIR_TASKS),
                                  f"创建目录失败：{exc}"))
        else:
            task_list = list(tasks or [])
            if task_list:                            # 空数据不产出空汇总文件
                try:
                    filename, markdown = render_tasks(task_list)
                    _emit(os.path.join(root, SUBDIR_TASKS, filename), markdown)
                except Exception as exc:             # noqa: BLE001
                    result.errors.append(("任务汇总", repr(exc)))

    result.total_ms = (time.perf_counter() - started) * 1000.0
    return result
