# -*- coding: utf-8 -*-
"""
====================================================================
日报 / 周报草稿  -  FloatPulse 外置插件（weekly-report）
====================================================================
把「这周干了什么」先替用户写一遍：选定时间范围 → 汇总
**已完成任务（按完成时间）+ 本周期碎片（按内容类别分组）+ 番茄计数**
→ 生成 Markdown 草稿，可直接编辑 / 复制 / 另存为 .md / 写入 Obsidian vault。

为什么做成插件而不是内置功能：
  它只读数据、只产文本，不碰悬浮球的任何手势与核心状态，
  是插件边界的教科书案例（见 docs/插件开发说明-2026-09-26.md 第 1 节）。

宿主能力只用到白名单里的这几项（拿不到 ball / main_window / Task 对象）：
  ctx.data.tasks() / fragments() / notes() / pomodoro()  只读快照
  ctx.config["obsidian_vault_path"]                      vault 路径
  ctx.parent_window()                                    对话框父窗口
  ctx.show_toast() / ctx.logger                          轻提示与日志

诚实性约定：番茄计数取自任务的 ``focus_sessions``（按任务累计），
**不是**时间区间统计，因此报告里明确标注，绝不假装是区间数据。
====================================================================
"""

import os
import re
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QDate, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QDateEdit, QFileDialog, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QRadioButton, QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin
from src.plugin_ui import (
    PluginDialog, flash_button, make_hint_label, make_section_label,
)

# ====================================================================
# 常量
# ====================================================================
PLUGIN_ID = "weekly-report"

# 时间范围选项：(键, 界面文案)
RANGE_TODAY = "today"
RANGE_WEEK = "week"
RANGE_7D = "7d"
RANGE_CUSTOM = "custom"
RANGE_OPTIONS = (
    (RANGE_TODAY, "今日（日报）"),
    (RANGE_WEEK, "本周（周一 → 今天）"),
    (RANGE_7D, "最近 7 天"),
    (RANGE_CUSTOM, "自定义区间"),
)

# 碎片内容类别 → 分组标题 + 展示顺序（与 fragment_classifier 的值域对齐）
CATEGORY_TITLES = (
    ("link", "🔗 链接"),
    ("code", "💻 代码"),
    ("path", "📁 路径"),
    ("command", "⌨ 命令行"),
    ("text", "📄 文本"),
)
CATEGORY_ORDER = [key for key, _ in CATEGORY_TITLES]
CATEGORY_LABEL = dict(CATEGORY_TITLES)
OTHER_CATEGORY = "其他"

# 小卡片专用临时笔记标题（md_export 同样跳过它，周报里也不该出现）
TEMP_NOTE_TITLE = "📌 临时笔记"

# vault 内的固定子目录（与 md_export 的 <vault>/FloatPulse/ 布局保持一致）
VAULT_ROOT_NAME = "FloatPulse"
VAULT_SUBDIR = "报告"

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


# ====================================================================
# 纯函数层（无 Qt 依赖，便于单独验证）
# ====================================================================
def as_date(value):
    """把 "YYYY-MM-DD…" 解析成 ``date``；空值 / 脏格式一律返回 None

    与 task_manager._parse_iso_date 的口径一致：解析失败即「无日期」。
    """
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def relative_deadline(deadline, today=None):
    """截止日相对文案：今天 / 明天 / ⚠ 逾期 N 天 / M月D日（周X）

    无日期或解析失败 → 空串（调用方不再拼接）。
    """
    d = as_date(deadline)
    if d is None:
        return ""
    base = today or date.today()
    delta = (d - base).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta < 0:
        return f"⚠ 逾期 {abs(delta)} 天"
    return f"{d.month}月{d.day}日（周{_WEEKDAY_CN[d.weekday()]}）"


def safe_filename(name, maxlen: int = 60) -> str:
    """把任意文本安全化为 Windows 可用文件名（不含扩展名）

    纯函数：同名输入永远得同名输出（幂等的前提）。
    规则：非法字符替换为 ``_`` → 去首尾空白与结尾点 → 保留设备名加前缀
          → 超长截断 → 空串兜底 ``未命名``。
    """
    text = "" if name is None else (name if isinstance(name, str) else str(name))
    text = _ILLEGAL_RE.sub("_", text).strip().rstrip(".").strip()
    if maxlen and len(text) > maxlen:
        text = text[:maxlen].rstrip(". ").strip()
    if text.upper() in _RESERVED_NAMES:
        text = "_" + text
    return text or "未命名"


def one_line(text, limit: int = 80) -> str:
    """把多行内容压成单行摘要（列表里不炸行），超长截断加省略号"""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return flat


def _timestamp(created_at) -> str:
    """把 "YYYY-MM-DD HH:MM" 压成 "MM-DD HH:MM"（列表用）"""
    text = str(created_at or "").strip()
    if len(text) >= 16:
        return text[5:16]
    return text


def resolve_range(key, today, custom_start=None, custom_end=None):
    """把范围选择解析为 ``(start, end, label)``

    - 今日  → (today, today)
    - 本周  → (本周一, today)   ISO 周，周一为第一天
    - 最近7天 → (today-6, today)
    - 自定义 → 传入的起止；起止颠倒时自动交换
    返回的 label 是写进报告标题的中文范围名。
    """
    if key == RANGE_WEEK:
        return today - timedelta(days=today.weekday()), today, "本周"
    if key == RANGE_7D:
        return today - timedelta(days=6), today, "最近 7 天"
    if key == RANGE_CUSTOM:
        start = custom_start or today
        end = custom_end or today
        if start > end:
            start, end = end, start
        return start, end, "自定义区间"
    return today, today, "今日"


def build_report(tasks, fragments, notes, pomodoro, start, end, *,
                 label="", now=None) -> str:
    """按区间拼装 Markdown 草稿（纯函数，便于验证）

    :param tasks: 任务快照列表（dict，字段见 task_manager.Task.to_dict）
    :param fragments: 碎片快照列表（dict）
    :param notes: 笔记快照列表（dict）
    :param pomodoro: 番茄钟状态快照（dict，可为空）
    :param start, end: 区间（date，含两端）
    :param label: 区间中文名（"今日" / "本周" …）
    :param now: 生成时刻（默认系统当前时间；注入便于测试）
    """
    now = now or datetime.now()
    today = now.date()
    span = (start.isoformat() if start == end
            else f"{start.isoformat()} ~ {end.isoformat()}")
    title_kind = "日报" if start == end else "周报"

    # ---- 1. 已完成任务（按完成时间；完成时间缺失时用截止日/创建日兜底并标注）----
    # 非 dict 的脏条目直接跳过：宁可少一行，也不要一条坏记录让整份报告失败
    done_rows, missing_time, undated_done = [], [], []
    for t in tasks or ():
        if not isinstance(t, dict) or not t.get("done"):
            continue
        title = one_line(t.get("title") or "（无标题）", 120)
        completed = as_date(t.get("completed_at"))
        when = completed or as_date(t.get("deadline")) or as_date(t.get("created_at"))
        if when is None:
            # 三条日期都不可解析（老数据）：**不能静默丢掉**，单独列在文末
            undated_done.append(title)
            continue
        if not (start <= when <= end):
            continue
        row = {
            "title": title,
            "when": when,
            "note": one_line(t.get("note"), 100),
            "sessions": int(t.get("focus_sessions") or 0),
            "exact": completed is not None,
        }
        (done_rows if row["exact"] else missing_time).append(row)
    done_rows.sort(key=lambda r: (r["when"], r["title"]))
    missing_time.sort(key=lambda r: (r["when"], r["title"]))

    # ---- 2. 未完成任务（截止日在区间内或已逾期）----
    open_rows = []
    for t in tasks or ():
        if not isinstance(t, dict) or t.get("done"):
            continue
        due = as_date(t.get("deadline"))
        if due is None or due > end:
            continue
        open_rows.append({
            "title": one_line(t.get("title") or "（无标题）", 120),
            "due": due,
            "rel": relative_deadline(t.get("deadline"), today),
            "sessions": int(t.get("focus_sessions") or 0),
        })
    open_rows.sort(key=lambda r: (r["due"], r["title"]))

    # ---- 3. 本周期碎片（按内容类别分组）----
    buckets, other_count = {}, 0
    for f in fragments or ():
        if not isinstance(f, dict):
            continue
        when = as_date(f.get("created_at"))
        if when is None or not (start <= when <= end):
            continue
        cat = str(f.get("category") or "text")
        buckets.setdefault(cat, []).append(f)
        if cat not in CATEGORY_ORDER:
            other_count += 1
    frag_total = sum(len(v) for v in buckets.values())

    # ---- 4. 本周期更新的笔记（跳过小卡片临时笔记）----
    note_rows = []
    for n in notes or ():
        if not isinstance(n, dict):
            continue
        title = str(n.get("title") or "").strip()
        if not title or title == TEMP_NOTE_TITLE:
            continue
        when = as_date(n.get("update_time")) or as_date(n.get("create_time"))
        if when is None or not (start <= when <= end):
            continue
        note_rows.append((when, title))
    note_rows.sort()

    # ---- 5. 番茄计数 ----
    pomo = pomodoro if isinstance(pomodoro, dict) else {}
    all_sessions = sum(int(t.get("focus_sessions") or 0)
                       for t in (tasks or ()) if isinstance(t, dict))
    done_sessions = sum(r["sessions"] for r in done_rows)
    open_sessions = sum(r["sessions"] for r in open_rows)
    state_cn = {
        "idle": "空闲", "running": "计时中", "paused": "已暂停",
    }.get(str(pomo.get("state") or "idle"), "空闲")
    phase_cn = {
        "focus": "专注", "break": "休息",
    }.get(str(pomo.get("phase") or ""), "")
    remaining = int(pomo.get("remaining_seconds") or 0)
    if state_cn == "计时中":
        state_cn = f"计时中（{phase_cn}，剩余 " \
                   f"{remaining // 60:02d}:{remaining % 60:02d}）"

    # ---- 组装 ----
    data_line = (f"> 数据：已完成任务 {len(done_rows)}"
                 f" ｜ 未完成 {len(open_rows)}"
                 f" ｜ 碎片 {frag_total} ｜ 笔记 {len(note_rows)}")
    if missing_time:
        data_line += f" ｜ 完成时间缺失 {len(missing_time)}（见文末）"
    if undated_done:
        data_line += f" ｜ 无日期已完成 {len(undated_done)}（见文末）"
    head = [
        f"# {title_kind} {span}",
        "",
        f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M')}"
        f" ｜ 范围：{label or span}"
        f" ｜ 来源：FloatPulse（weekly-report 插件）",
        data_line,
        "",
    ]

    body = ["## ✅ 已完成任务（%d）" % len(done_rows), ""]
    if done_rows:
        for r in done_rows:
            extra = f" ｜ {r['note']}" if r["note"] else ""
            body.append(f"- [x] {r['title']} ｜ "
                        f"{r['when'].isoformat()} 完成{extra}")
    else:
        body.append("_（本区间没有已完成的任务）_")
    body.append("")

    body += ["## 🔄 未完成任务（逾期 / 本周期到期）（%d）" % len(open_rows), ""]
    if open_rows:
        for r in open_rows:
            rel = f" ｜ {r['rel']}" if r["rel"] else ""
            body.append(f"- [ ] {r['title']}{rel}")
    else:
        body.append("_（没有逾期或本区间内到期的未完成任务）_")
    body.append("")

    body += ["## 🧩 本周期碎片（%d）" % frag_total, ""]
    if frag_total:
        ordered = [c for c in CATEGORY_ORDER if buckets.get(c)]
        ordered += [c for c in sorted(buckets) if c not in CATEGORY_ORDER]
        for cat in ordered:
            label_cn = CATEGORY_LABEL.get(cat, OTHER_CATEGORY)
            body.append(f"### {label_cn}（{len(buckets[cat])}）")
            body.append("")
            for f in sorted(buckets[cat],
                            key=lambda x: str(x.get("created_at") or "")):
                src = one_line(f.get("source"), 24) or "未知来源"
                body.append(f"- {one_line(f.get('content'), 80)}"
                            f"（{src} ｜ {_timestamp(f.get('created_at'))}）")
            body.append("")
    else:
        body.append("_（本区间没有新碎片）_")
        body.append("")

    body += ["## 📝 本周期更新的笔记（%d）" % len(note_rows), ""]
    if note_rows:
        for _, title in note_rows:
            body.append(f"- {title}")
    else:
        body.append("_（本区间没有笔记更新）_")
    body.append("")

    body += [
        "## 🍅 专注统计",
        "",
        f"- 本周期已完成任务累计专注 **{done_sessions}** 次",
        f"- 未完成任务累计专注 **{open_sessions}** 次",
        f"- 全部任务累计专注 **{all_sessions}** 次",
        f"- 当前番茄钟：{state_cn}",
        "",
        "> 说明：番茄计数取自任务的累计字段（focus_sessions），"
        "是**按任务累计**而非时间区间统计。",
        "",
    ]

    tail = []
    if missing_time:
        tail += ["", "---", "",
                 "※ 以下任务已完成但缺少完成时间，按截止日/创建日归入本区间：", ""]
        for r in missing_time:
            tail.append(f"- [x] {r['title']} ｜ 归入日 {r['when'].isoformat()} ※")
        tail.append("")
    if undated_done:
        tail += ["", "---", "",
                 "※ 以下任务已完成，但没有任何可解析的日期，无法归入任何区间：", ""]
        for title in undated_done:
            tail.append(f"- [x] {title} ※")
        tail.append("")

    return "\n".join(head + body + tail).rstrip() + "\n"


def default_file_stem(start, end) -> str:
    """另存为的默认文件名（不含扩展名）"""
    if start == end:
        return safe_filename(f"日报-{start.isoformat()}")
    return safe_filename(f"周报-{start.isoformat()}_{end.isoformat()}")


# ====================================================================
# 预览对话框
# ====================================================================
class ReportDialog(PluginDialog):
    """范围选择 + 草稿预览 + 三个输出动作（复制 / 另存为 / 写入 vault）

    外观继承宿主的 ``PluginDialog``（玻璃壳 + 圆角 + 自绘标题栏 + 柔和阴影），
    与主窗口共用同一份 QSS，因此跟随 light/dark 主题自动换肤。
    """

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, title="日报 / 周报草稿",
                         subtitle="由当前数据生成，可编辑后导出",
                         parent=parent, size=(860, 620))
        # 基类已把 ctx 存进 self._plugin_ctx；这里沿用本插件惯用的 _ctx 名字
        self._ctx = ctx
        self._today = date.today()
        self._build_ui()
        self._regenerate()

    # ---------------- 界面 ----------------
    def _build_ui(self):
        body = self.body_layout
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(10)

        # ---- 第一行：时间范围（整行一个玻璃卡片，与设置页分组观感一致）----
        range_card = QWidget()
        range_card.setObjectName("glassCard")
        rc = QHBoxLayout(range_card)
        rc.setContentsMargins(14, 10, 14, 10)
        rc.setSpacing(10)

        # 标题 + 单选：先把控件全部建好并设初值，**最后再接信号**，
        # 否则 setChecked 会立刻触发 toggled → 处理器去访问尚未创建的日期控件
        rc.addWidget(make_section_label("时间范围"))
        self._group = QButtonGroup(self)
        self._radios = {}
        for key, text in RANGE_OPTIONS:
            rb = QRadioButton(text)
            self._group.addButton(rb)
            self._radios[key] = rb
            rc.addWidget(rb)
        self._radios[RANGE_WEEK].setChecked(True)

        week_start = self._today - timedelta(days=6)
        self._from = QDateEdit()
        self._to = QDateEdit()
        self._from.setCalendarPopup(True)
        self._to.setCalendarPopup(True)
        self._from.setDisplayFormat("yyyy-MM-dd")
        self._to.setDisplayFormat("yyyy-MM-dd")
        self._from.setDate(QDate.fromString(week_start.isoformat(), "yyyy-MM-dd"))
        self._to.setDate(QDate.fromString(self._today.isoformat(), "yyyy-MM-dd"))
        for w in (self._from, self._to):
            w.setEnabled(False)                 # 仅「自定义区间」可用
            w.setFixedWidth(120)
        rc.addWidget(QLabel("起"))
        rc.addWidget(self._from)
        rc.addWidget(QLabel("止"))
        rc.addWidget(self._to)
        rc.addStretch(1)
        body.addWidget(range_card)

        # ---- 预览区：套一层卡片，与范围行同一视觉语言 ----
        preview_card = QWidget()
        preview_card.setObjectName("glassCard")
        pc = QVBoxLayout(preview_card)
        pc.setContentsMargins(14, 12, 14, 12)
        pc.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(make_section_label("草稿预览"))
        head.addWidget(make_hint_label("可直接编辑，导出的是当前内容"))
        head.addStretch(1)
        pc.addLayout(head)

        self._text = QPlainTextEdit()
        # QPlainTextEdit 在主题 QSS 里已有通用规则（边框/圆角/焦点态），
        # 不另设 objectName，以免样式落空
        self._text.setFont(QFont("Microsoft YaHei UI", 10))
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        pc.addWidget(self._text, 1)
        body.addWidget(preview_card, 1)

        # ---- 状态行：固定高度占位，避免文字出现/消失导致按钮行跳动 ----
        self._status = QLabel("")
        self._status.setObjectName("hintLabel")
        self._status.setMinimumHeight(20)
        body.addWidget(self._status)

        # ---- 底部按钮行（右对齐，与其他对话框一致）----
        # vault 未配置 → 置灰并说明原因（不弹窗、不阻断其它按钮）
        vault_ready = bool(self._vault_path())
        created = self.add_footer([
            ("关闭", "secondaryBtn", self.accept),
            ("📋 复制到剪贴板", "secondaryBtn", self._do_copy),
            ("💾 另存为 .md…", "secondaryBtn", self._do_save_as),
            ("🗂 写入 Obsidian vault", "primaryBtn", self._do_vault),
        ])
        self._close_btn, self._copy_btn, self._save_btn, self._vault_btn = created
        if not vault_ready:
            self._vault_btn.setEnabled(False)
            self._vault_btn.setToolTip(
                "未配置 Obsidian vault 路径：请到 设置 → 📤 导出 → 更改目录")

        # 信号接线统一放到控件就绪之后。
        # 注意：按钮的 clicked 已在 add_footer 内部接好，这里**不要再接一次**
        # （重复接线会让每次点击触发两次，落盘/弹窗都会翻倍）。
        for rb in self._radios.values():
            rb.toggled.connect(self._on_range_changed)
        self._from.dateChanged.connect(self._regenerate)
        self._to.dateChanged.connect(self._regenerate)

    # ---------------- 范围与生成 ----------------
    def _current_key(self) -> str:
        for key, rb in self._radios.items():
            if rb.isChecked():
                return key
        return RANGE_WEEK

    def _on_range_changed(self, _checked=False):
        custom = self._current_key() == RANGE_CUSTOM
        self._from.setEnabled(custom)
        self._to.setEnabled(custom)
        self._regenerate()

    def _range(self):
        key = self._current_key()
        custom_start = custom_end = None
        if key == RANGE_CUSTOM:
            custom_start = self._from.date().toPyDate()
            custom_end = self._to.date().toPyDate()
        return resolve_range(key, self._today, custom_start, custom_end)

    def _regenerate(self, *_args):
        start, end, label = self._range()
        data = self._ctx.data
        # 数据源一个都没有时不必逐项去问（否则一次刷新刷出 4 条 warning）
        if data.sources():
            tasks = data.tasks()
            fragments = data.fragments()
            notes = data.notes()
            pomodoro = data.pomodoro()
        else:
            tasks, fragments, notes, pomodoro = [], [], [], {}
        text = build_report(tasks, fragments, notes, pomodoro,
                            start, end, label=label)
        if not data.sources():
            text += ("\n> ⚠ 宿主未注入任何数据源，以上内容为空"
                     "（需要 tasks / fragments / notes）。\n")
        self._text.setPlainText(text)
        self._status.setText(f"已生成：{start.isoformat()} ~ {end.isoformat()}")

    # ---------------- 输出动作 ----------------
    def _vault_path(self) -> str:
        raw = str(self._ctx.config.get("obsidian_vault_path", "") or "").strip()
        return raw

    def _do_copy(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:                      # 离屏/无剪贴板环境兜底
            self._status.setText("⚠ 当前环境没有剪贴板，复制失败")
            return
        text = self._text.toPlainText()
        clipboard.setText(text)
        self._status.setText(f"✅ 已复制 {len(text)} 字到剪贴板")
        flash_button(self._copy_btn, "✅ 已复制")

    def _default_dir(self) -> str:
        vault = self._vault_path()
        if vault and os.path.isdir(vault):
            return vault
        return os.path.expanduser("~")

    def _do_save_as(self):
        start, end, _ = self._range()
        suggested = os.path.join(self._default_dir(),
                                 default_file_stem(start, end) + ".md")
        path, _filter = QFileDialog.getSaveFileName(
            self, "另存为 Markdown", suggested, "Markdown (*.md);;所有文件 (*)")
        if not path:
            self._status.setText("已取消另存为")
            return
        if not path.lower().endswith(".md"):
            path += ".md"
        if self._write_file(path):
            self._status.setText(f"✅ 已保存：{path}")

    def _do_vault(self):
        vault = self._vault_path()
        if not vault:
            self._status.setText("⚠ 未配置 Obsidian vault 路径")
            return
        target_dir = os.path.join(vault, VAULT_ROOT_NAME, VAULT_SUBDIR)
        start, end, _ = self._range()
        path = os.path.join(target_dir, default_file_stem(start, end) + ".md")
        if os.path.isfile(path):
            answer = QMessageBox.question(
                self, "文件已存在",
                f"目标文件已存在，是否覆盖？\n\n{path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._status.setText("已取消写入")
                return
        if self._write_file(path):
            self._status.setText(f"✅ 已写入 vault：{path}")

    def _write_file(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # 与 md_export 同一口径：utf-8 + LF，避免 Windows 下 CRLF 混入 vault
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self._text.toPlainText())
            return True
        except OSError as exc:
            QMessageBox.warning(self, "写入失败", f"无法写入文件：\n{path}\n\n{exc}")
            self._status.setText(f"⚠ 写入失败：{exc}")
            return False


# ====================================================================
# 动作与插件
# ====================================================================
class DraftReportAction(BallAction):
    """生成日报 / 周报草稿"""

    id = f"{PLUGIN_ID}.draft"
    title = "📝 生成日报 / 周报草稿"

    def run(self, ctx):
        ctx.logger.info(f"[{PLUGIN_ID}] 打开草稿窗口，数据源="
                        f"{ctx.data.sources() or '（无）'}")
        # 热键路径下 run() 是在原生事件过滤器里被调用的，直接开模态框有重入风险；
        # 菜单路径下也只是延后一个事件循环回合，代价可忽略
        holder = {}

        def _open():
            parent = ctx.parent_window()
            dialog = ReportDialog(ctx, parent)
            holder["dialog"] = dialog          # 防止被 GC 提前回收
            dialog.exec()
            holder.pop("dialog", None)

        QTimer.singleShot(0, _open)


class WeeklyReportPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "日报 / 周报草稿"
    version = "1.0.0"

    def create_actions(self, ctx):
        return [DraftReportAction()]
