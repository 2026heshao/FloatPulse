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

import json
import os
import re
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QDate, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDateEdit, QFileDialog,
    QHBoxLayout, QLabel, QMenu, QMessageBox, QPlainTextEdit, QRadioButton,
    QVBoxLayout, QWidget,
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
    ("link", "链接"),
    ("code", "代码"),
    ("path", "路径"),
    ("command", "命令行"),
    ("text", "文本"),
)
CATEGORY_ORDER = [key for key, _ in CATEGORY_TITLES]
CATEGORY_LABEL = dict(CATEGORY_TITLES)
OTHER_CATEGORY = "其他"

# 小卡片专用临时笔记标题（md_export 同样跳过它，周报里也不该出现）
TEMP_NOTE_TITLE = "📌 临时笔记"

# vault 内的固定子目录（与 md_export 的 <vault>/FloatPulse/ 布局保持一致）
VAULT_ROOT_NAME = "FloatPulse"
VAULT_SUBDIR = "报告"

# ---------------- 导出历史（v1.2.0，P3） ----------------
# 最近导出的文件记进插件私有目录（重启不丢）；「🕘 最近导出」菜单一键
# 回访。上限收紧：它是「快速回访」不是「导出日志」。
EXPORTS_FILE = "exports.json"
EXPORT_LIMIT = 10          # 最多保留的导出记录（最新在前，丢最旧）
EXPORT_PATH_CHARS = 500    # 单条路径的最大字符数

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")

# ---------------- AI 润色（v1.1.0，P1） ----------------
POLISH_TIMEOUT_S = 90.0
MAX_DRAFT_CHARS = 8000        # 送润色的草稿上限（超长截断并标注）
TEMPERATURE = 0.4

POLISH_SYSTEM_PROMPT = (
    "你是办公工具 FloatPulse 的周报润色助手。规则：\n"
    "1. 只输出润色后的周报本身——不要解释、不要开场白、不要包代码块；\n"
    "2. 保留原文的 Markdown 结构与全部事实（任务名、日期、数量），"
    "绝不虚构原文没有的工作内容；\n"
    "3. 语气专业、简洁，适合直接提交给上级；用简体中文。"
)

POLISH_PROMPT = (
    "请把下面的周报草稿润色成一份可以直接提交的正式周报：\n"
    "- 保留原有事实与数字，把流水账整理成有条理的汇报；\n"
    "- 可以按「本周完成 / 进行中 / 下周计划」重组段落，缺的段落不要编；\n"
    "- 篇幅与原文相当，不要注水。"
)


def build_polish_request(params: dict, draft: str):
    """AI 总配置参数 + 草稿 → (url, headers, body, 错误文案)

    成功时错误文案为空串；失败时前三个返回值都是 None。
    与 ai-text-workshop 的 build_request 同一参数契约（mode / base_url /
    api_key / model / local_port），OpenAI 兼容 /chat/completions 非流式。
    """
    params = params if isinstance(params, dict) else {}
    mode = str(params.get("mode") or "cloud")
    if mode == "local":
        try:
            port = int(params.get("local_port") or 8095)
        except (TypeError, ValueError):
            port = 8095
        base = f"http://127.0.0.1:{port}/v1"
        model = "local"
        key = ""
    else:
        base = str(params.get("base_url") or "").strip().rstrip("/")
        model = str(params.get("model") or "").strip()
        key = str(params.get("api_key") or "").strip()
        if not base:
            return None, None, None, "后端地址为空：到 设置 → AI 总配置 填写"
        if not model:
            return None, None, None, "模型名为空：到 设置 → AI 总配置 填写"
    if not (base.startswith("http://") or base.startswith("https://")):
        return None, None, None, f"后端地址必须以 http:// 或 https:// 开头：{base}"
    text = str(draft or "").strip()
    if not text:
        return None, None, None, "草稿为空：先选好时间范围生成草稿"
    if len(text) > MAX_DRAFT_CHARS:
        text = text[:MAX_DRAFT_CHARS] + f"\n…（超长已截断，原始 {len(text)} 字符）"
    url = f"{base}/chat/completions"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": POLISH_SYSTEM_PROMPT},
            {"role": "user",
             "content": f"{POLISH_PROMPT}\n\n=== 周报草稿 ===\n{text}"},
        ],
        "temperature": TEMPERATURE,
        "stream": False,
    }
    return url, headers, body, ""


def parse_ai_reply(result: dict):
    """网络桥结果 → (回复文本, None) 或 (None, 错误文案)"""
    if not result.get("ok"):
        err = str(result.get("error") or "未知错误")
        detail = (result.get("body") or "").strip()[:200]
        hint = ""
        if "HTTP 401" in err or "HTTP 403" in err:
            hint = "（key 缺失或无效？到 设置 → AI 总配置 检查）"
        elif "HTTP 404" in err:
            hint = "（地址或模型名不对？地址应以 /v1 结尾）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（模型首次加载较慢，可重试一次）"
        elif "refused" in err.lower():
            hint = "（端口没有服务在听——本地服务没启动，或端口号不对）"
        return None, f"请求失败：{err}{hint}" + (f"\n{detail}" if detail else "")
    try:
        data = json.loads(result.get("body") or "")
        reply = data["choices"][0]["message"]["content"]
        return str(reply).strip(), None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"响应格式不符合 OpenAI 协议：{exc!r}"


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
        return f"逾期 {abs(delta)} 天"
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


# ---------------- 导出历史存档（P3） ----------------
def sanitize_exports(raw) -> list:
    """导出历史消毒：脏条目丢弃、路径截断、去重（保最新）、上限裁剪"""
    items, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()[:EXPORT_PATH_CHARS]
        if not path or path in seen:
            continue
        seen.add(path)
        items.append({"path": path,
                      "at": str(item.get("at") or "")[:16]})
        if len(items) >= EXPORT_LIMIT:
            break
    return items


def load_exports(ctx) -> list:
    """读导出历史；文件缺失 / 写坏降级为空表（原文件保留）"""
    data_dir = getattr(ctx, "data_dir", None)
    path = os.path.join(data_dir, EXPORTS_FILE) if data_dir else ""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sanitize_exports(json.load(f))
        except (OSError, ValueError):
            pass
    return []


def save_exports(ctx, items) -> bool:
    """写导出历史（临时文件 + os.replace 原子替换）；失败只返回 False"""
    data_dir = getattr(ctx, "data_dir", None)
    path = os.path.join(data_dir, EXPORTS_FILE) if data_dir else ""
    if not path:
        return False
    tmp = path + ".tmp"
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(sanitize_exports(items), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def record_export(ctx, path, at=None) -> bool:
    """记一笔导出：同路径提到最前（不重复）、截上限、落盘"""
    text = str(path or "").strip()[:EXPORT_PATH_CHARS]
    if not text:
        return False
    items = load_exports(ctx)
    items = [it for it in items if it["path"] != text]
    stamp = (at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    items.insert(0, {"path": text, "at": stamp})
    return save_exports(ctx, items[:EXPORT_LIMIT])


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


def normalize_include(include) -> dict:
    """段落开关归一化（P2）：None = 全开（与旧版逐字节一致）；脏值忽略"""
    base = {"done": True, "open": True, "fragments": True,
            "notes": True, "pomodoro": True}
    if isinstance(include, dict):
        for key in base:
            if key in include:
                base[key] = bool(include[key])
    return base


def build_report(tasks, fragments, notes, pomodoro, start, end, *,
                 label="", now=None, include=None) -> str:
    """按区间拼装 Markdown 草稿（纯函数，便于验证）

    :param tasks: 任务快照列表（dict，字段见 task_manager.Task.to_dict）
    :param fragments: 碎片快照列表（dict）
    :param notes: 笔记快照列表（dict）
    :param pomodoro: 番茄钟状态快照（dict，可为空）
    :param start, end: 区间（date，含两端）
    :param label: 区间中文名（"今日" / "本周" …）
    :param now: 生成时刻（默认系统当前时间；注入便于测试）
    :param include: 段落开关（P2，v1.2.0）：{"done","open","fragments",
      "notes","pomodoro"} → bool；None / 缺键 = True（向后兼容）。
      关掉的段落整段不输出；统计行只列保留段落的计数。
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
    inc = normalize_include(include)
    data_parts = []
    if inc["done"]:
        data_parts.append(f"已完成任务 {len(done_rows)}")
    if inc["open"]:
        data_parts.append(f"未完成 {len(open_rows)}")
    if inc["fragments"]:
        data_parts.append(f"碎片 {frag_total}")
    if inc["notes"]:
        data_parts.append(f"笔记 {len(note_rows)}")
    data_line = ("> 数据：" + " ｜ ".join(data_parts) if data_parts
                 else "> 数据：（各段落均已关闭，只输出标题）")
    if inc["done"] and missing_time:
        data_line += f" ｜ 完成时间缺失 {len(missing_time)}（见文末）"
    if inc["done"] and undated_done:
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

    body = []
    if inc["done"]:
        body += ["## 已完成任务（%d）" % len(done_rows), ""]
        if done_rows:
            for r in done_rows:
                extra = f" ｜ {r['note']}" if r["note"] else ""
                body.append(f"- [x] {r['title']} ｜ "
                            f"{r['when'].isoformat()} 完成{extra}")
        else:
            body.append("_（本区间没有已完成的任务）_")
        body.append("")

    if inc["open"]:
        body += ["## 未完成任务（逾期 / 本周期到期）（%d）" % len(open_rows),
                 ""]
        if open_rows:
            for r in open_rows:
                rel = f" ｜ {r['rel']}" if r["rel"] else ""
                body.append(f"- [ ] {r['title']}{rel}")
        else:
            body.append("_（没有逾期或本区间内到期的未完成任务）_")
        body.append("")

    if inc["fragments"]:
        body += ["## 本周期碎片（%d）" % frag_total, ""]
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

    if inc["notes"]:
        body += ["## 本周期更新的笔记（%d）" % len(note_rows), ""]
        if note_rows:
            for _, title in note_rows:
                body.append(f"- {title}")
        else:
            body.append("_（本区间没有笔记更新）_")
        body.append("")

    if inc["pomodoro"]:
        body += [
            "## 专注统计",
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
    if inc["done"] and missing_time:
        tail += ["", "---", "",
                 "※ 以下任务已完成但缺少完成时间，按截止日/创建日归入本区间：", ""]
        for r in missing_time:
            tail.append(f"- [x] {r['title']} ｜ 归入日 {r['when'].isoformat()} ※")
        tail.append("")
    if inc["done"] and undated_done:
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
        self._polish_busy = False      # AI 润色在途（防重复点击）
        self._last_generated = ""      # 最近一次按范围生成的原文（覆盖确认用）
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
        # 段落开关（P2）：任务（完成+未完成）/ 碎片 / 笔记；专注统计恒保留。
        # 勾选即时重生成——预览内容就是导出内容，开关效果所见即所得。
        self._inc_tasks = QCheckBox("任务")
        self._inc_frag = QCheckBox("碎片")
        self._inc_notes = QCheckBox("笔记")
        for cb in (self._inc_tasks, self._inc_frag, self._inc_notes):
            cb.setChecked(True)
            cb.setToolTip("取消勾选后，报告里不再包含这一段落")
            head.addWidget(cb)
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
            ("最近导出", "secondaryBtn", self._show_recent),
            ("关闭", "secondaryBtn", self.accept),
            ("复制到剪贴板", "secondaryBtn", self._do_copy),
            ("AI 润色", "secondaryBtn", self._do_polish),
            ("另存为 .md…", "secondaryBtn", self._do_save_as, "save"),
            ("写入 Obsidian vault", "primaryBtn", self._do_vault),
        ])
        (self._recent_btn, self._close_btn, self._copy_btn,
         self._polish_btn, self._save_btn, self._vault_btn) = created
        if not vault_ready:
            self._vault_btn.setEnabled(False)
            self._vault_btn.setToolTip(
                "未配置 Obsidian vault 路径：请到 设置 → 导出 → 更改目录")

        # 信号接线统一放到控件就绪之后。
        # 注意：按钮的 clicked 已在 add_footer 内部接好，这里**不要再接一次**
        # （重复接线会让每次点击触发两次，落盘/弹窗都会翻倍）。
        for rb in self._radios.values():
            rb.toggled.connect(self._on_range_changed)
        self._from.dateChanged.connect(self._regenerate)
        self._to.dateChanged.connect(self._regenerate)
        # 段落开关（P2）：必须在三个勾选框**都建好后**再接信号——
        # 接好后 setChecked 才不会在半初始化状态触发 _regenerate
        for cb in (self._inc_tasks, self._inc_frag, self._inc_notes):
            cb.toggled.connect(self._regenerate)

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

    def _include(self) -> dict:
        """三勾选 → build_report 的段落开关（任务勾选同时控制完成/未完成）"""
        return {"done": self._inc_tasks.isChecked(),
                "open": self._inc_tasks.isChecked(),
                "fragments": self._inc_frag.isChecked(),
                "notes": self._inc_notes.isChecked(),
                "pomodoro": True}

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
                            start, end, label=label, include=self._include())
        if not data.sources():
            text += ("\n> 宿主未注入任何数据源，以上内容为空"
                     "（需要 tasks / fragments / notes）。\n")
        self._text.setPlainText(text)
        self._last_generated = text
        self._status.setText(f"已生成：{start.isoformat()} ~ {end.isoformat()}")

    # ---------------- AI 润色（v1.1.0，P1） ----------------
    def _attached_params(self):
        """AI 总配置参数（唯一后端来源）；未接入返回 None（同 AI 插件口径）"""
        try:
            if self._ctx.has_capability("ai") and self._ctx.ai.is_attached():
                params = self._ctx.ai.params()
                if params:
                    return params
        except Exception:                     # noqa: BLE001 - 读取失败按未接入
            pass
        return None

    def _do_polish(self):
        """把当前预览内容交给 AI 润色，结果回填预览区。

        手动编辑过的草稿（与最近生成原文不一致）先弹确认再覆盖——
        润色是「整段替换」，不能悄悄毁掉用户手改的内容。
        """
        if self._polish_busy:
            self._status.setText("正在润色上一份…")
            return
        draft = self._text.toPlainText().strip()
        if not draft:
            self._status.setText("草稿为空：先选好时间范围生成草稿")
            return
        params = self._attached_params()
        if params is None:
            self._status.setText(
                "尚未接入 AI：到 设置 → AI 总配置 配好云端或本地后端，"
                "并在「接入插件」里勾选本插件")
            return
        if params.get("mode") == "local" and not params.get("local_ready"):
            self._status.setText("宿主本地服务未就绪：到 设置 → AI 总配置 启动")
            return
        if draft != self._last_generated.strip():
            answer = QMessageBox.question(
                self, "覆盖手动编辑？",
                "当前草稿有手动编辑，AI 润色会整段替换预览内容。\n\n"
                "（原文随时可改时间范围重新生成找回）是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._status.setText("已取消 AI 润色")
                return
        url, headers, body, err = build_polish_request(params, draft)
        if url is None:
            self._status.setText(err)
            return
        self._polish_busy = True
        self._polish_btn.setEnabled(False)
        self._status.setText("AI 润色中…（模型响应通常几秒）")
        self._ctx.logger.info(f"[{PLUGIN_ID}] AI 润色：草稿 {len(draft)} 字")
        ok = self._ctx.http_post_json_async(
            url, headers, body, timeout=POLISH_TIMEOUT_S,
            on_done=self._on_polish_reply)
        if not ok:
            self._on_polish_reply({"ok": False, "error": "网络桥拒绝请求"})

    def _on_polish_reply(self, result: dict):
        self._polish_busy = False
        self._polish_btn.setEnabled(True)
        text, err = parse_ai_reply(result)
        if err:
            self._status.setText(err)
            return
        if not text:
            self._status.setText("AI 返回了空结果，预览未改动")
            return
        self._text.setPlainText(text)
        self._last_generated = text          # 润色稿成为新基准
        self._status.setText("AI 润色完成，已替换预览"
                             "（原文可改时间范围重新生成找回）")

    # ---------------- 输出动作 ----------------
    def _vault_path(self) -> str:
        raw = str(self._ctx.config.get("obsidian_vault_path", "") or "").strip()
        return raw

    def _do_copy(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:                      # 离屏/无剪贴板环境兜底
            self._status.setText("当前环境没有剪贴板，复制失败")
            return
        text = self._text.toPlainText()
        clipboard.setText(text)
        self._status.setText(f"已复制 {len(text)} 字到剪贴板")
        flash_button(self._copy_btn, "已复制")

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
            record_export(self._ctx, path)     # P3：记入最近导出
            self._status.setText(f"已保存：{path}")

    def _do_vault(self):
        vault = self._vault_path()
        if not vault:
            self._status.setText("未配置 Obsidian vault 路径")
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
            record_export(self._ctx, path)     # P3：vault 写入也算导出
            self._status.setText(f"已写入 vault：{path}")

    # ---------------- 最近导出（P3） ----------------
    def _show_recent(self):
        """「🕘 最近导出」菜单：最近导出的文件 → 点击打开所在文件夹"""
        menu = QMenu(self)
        items = load_exports(self._ctx)
        if not items:
            empty = menu.addAction("（还没有导出记录）")
            empty.setEnabled(False)
        for it in items:
            name = os.path.basename(it["path"]) or it["path"]
            act = menu.addAction(f"{name}（{it['at'] or '时间未知'}）")
            act.setToolTip(it["path"])
            act.triggered.connect(
                lambda _=False, p=it["path"]: self._open_export(p))
        if items:
            menu.addSeparator()
            clear = menu.addAction("清空导出记录")
            clear.triggered.connect(self._clear_exports)
        menu.exec(self._recent_btn.mapToGlobal(
            self._recent_btn.rect().bottomLeft()))

    def _open_export(self, path: str):
        """打开一条导出记录所在的文件夹（资源管理器）"""
        folder = os.path.dirname(path) or path
        if not os.path.isdir(folder):
            self._status.setText(f"文件所在文件夹不存在：{folder}")
            return
        try:
            os.startfile(folder)               # Windows 资源管理器
        except OSError as exc:
            self._status.setText(f"打开文件夹失败：{exc}")

    def _clear_exports(self):
        if save_exports(self._ctx, []):
            self._status.setText("已清空导出记录")
        else:
            self._status.setText("清空失败（数据目录不可用）")

    def _write_file(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # 与 md_export 同一口径：utf-8 + LF，避免 Windows 下 CRLF 混入 vault
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self._text.toPlainText())
            return True
        except OSError as exc:
            QMessageBox.warning(self, "写入失败", f"无法写入文件：\n{path}\n\n{exc}")
            self._status.setText(f"写入失败：{exc}")
            return False


# ====================================================================
# 动作与插件
# ====================================================================
class DraftReportAction(BallAction):
    """生成日报 / 周报草稿"""

    id = f"{PLUGIN_ID}.draft"
    title = "生成日报 / 周报草稿"

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
    version = "1.2.0"

    def create_actions(self, ctx):
        return [DraftReportAction()]
