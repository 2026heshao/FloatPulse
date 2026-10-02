# -*- coding: utf-8 -*-
"""
====================================================================
周期任务  -  FloatPulse 外置插件（recurring-tasks）
====================================================================
补上宿主缺的那一件事：**只给规则，到点自动生成任务**。

与宿主已有的能力划清界限（别重复造轮子）：
  - 宿主 `task_reminder_enabled`：**已知具体日期**的任务 → 启动时 + 每日 9:00
    托盘气泡提醒。那是「一次性、已知日期」的提醒。
  - 本插件：**只给规则**（每天 / 每周几 HH:MM / 每月几号 / 每 N 天）→ 自动生成
    任务实例并落到任务页，生成的任务照旧享受宿主的到期提醒。
  两者互补：规则负责「凭空生出任务」，宿主负责「任务到期时叫人」。

调度正确性（三条都是踩过的坑，不是设想）：
  1. **轮询而非一次性定时器**——Qt 定时器在系统休眠期间不累加，睡一晚回来
     定时器不会补触发；一次性定时器还会在跨天时算错。这里用 60s tick
     反复比对「当前时间 vs 规则」，不依赖长定时。
  2. **错过的补跑一次**——程序没开（或休眠）期间错过的触发点，启动后补生成
     **最近一次**（只补最近一次，不无限回溯：连关三天不该冒出三条任务）。
     **但只补「规则创建之后」的触发点**（``created_at`` 水位）：否则今天 10:00
     新建一条「每天 08:00」的规则，会立刻补出一条**昨天**的任务——那条规则
     当时还不存在，谈不上「错过」。
  3. **同一天不重复生成**——每条规则记 `last_fired`（触发日，YYYY-MM-DD 落盘），
     触发过就推进；重复启动程序、一天内多次 tick 都不会再生成。

数据与权限：
  - 规则存 `ctx.data_dir/rules.json`（插件私有目录），原子写（临时文件 + os.replace）
  - 生成任务走 `ctx.write.add_task()`，manifest 声明的能力是 `write`
    （只增：本插件不会改任何已有任务，也不会删任何东西）

诚实性约定：
  - 生成失败（未授权 / 宿主未注入 provider）时**不假装成功**：记 warning + toast
    说清原因，并把该次触发标记为已处理，避免每次 tick 重复打扰。
  - 页面显示的「下次触发」是**推算值**，程序没运行时不会自动生成——页面上
    明确写出来，不让用户误以为关着程序也会长任务。
====================================================================
"""

import calendar
import json
import os
import uuid
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QObject, Qt, QTime, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QTimeEdit,
    QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin
from src.plugin_ui import PluginDialog, make_hint_label, make_section_label

# ====================================================================
# 常量
# ====================================================================
PLUGIN_ID = "recurring-tasks"
# 主窗口插件页 key（与 loader / register_plugin_page 约定一致，别写成裸 PLUGIN_ID）
PAGE_KEY = f"plugin:{PLUGIN_ID}"
RULES_FILENAME = "rules.json"
SCHEMA_VERSION = 1

TICK_MS = 60_000            # 轮询间隔：60s（触发精度到分钟，够用且开销可忽略）
SEARCH_DAYS = 400           # 回溯 / 前视窗口（覆盖最大 365 天的间隔规则）
MAX_RULES = 200             # 规则条数上限（防手写坏文件把 UI 拖死）

KIND_DAILY = "daily"
KIND_WEEKLY = "weekly"
KIND_MONTHLY = "monthly"
KIND_INTERVAL = "interval"
KIND_NWEEKLY = "nweekly"      # 每 N 周（隔周周一这类），v1.1.0
KINDS = (KIND_DAILY, KIND_WEEKLY, KIND_MONTHLY, KIND_INTERVAL, KIND_NWEEKLY)

KIND_LABELS = (
    (KIND_DAILY, "每天"),
    (KIND_WEEKLY, "每周（选星期几）"),
    (KIND_MONTHLY, "每月（选几号）"),
    (KIND_INTERVAL, "每 N 天"),
    (KIND_NWEEKLY, "每 N 周（隔周…）"),
)
KIND_LABEL = dict(KIND_LABELS)

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

# 生成的任务的截止日策略：(值, 文案)；-1 表示不设截止日
DUE_OFFSETS = (
    (-1, "不设截止日"),
    (0, "当天截止"),
    (1, "次日截止"),
    (3, "3 天后截止"),
    (7, "7 天后截止"),
)
DUE_OFFSET_LABEL = dict(DUE_OFFSETS)

MAX_TITLE = 200
MAX_NOTE = 500


# ====================================================================
# 纯函数层（无 Qt 依赖，便于单独验证）
# ====================================================================
def parse_hm(text):
    """解析 "HH:MM" → ``(hour, minute)``；非法返回 None"""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def format_hm(text) -> str:
    """规整为 "HH:MM"（非法返回空串）"""
    hm = parse_hm(text)
    return f"{hm[0]:02d}:{hm[1]:02d}" if hm else ""


def month_days(year: int, month: int) -> int:
    """当月天数（calendar 处理闰年，stdlib）"""
    return calendar.monthrange(year, month)[1]


def as_date(value):
    """把 "YYYY-MM-DD…" 解析成 ``date``；脏值返回 None（与宿主同口径）"""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def parse_moment(value):
    """解析 "YYYY-MM-DD HH:MM" / "YYYY-MM-DD" → ``datetime``；脏值 None"""
    text = str(value or "").strip()
    if len(text) >= 16:
        try:
            return datetime.fromisoformat(text[:16])
        except (ValueError, TypeError):
            pass
    day = as_date(text)
    return datetime.combine(day, datetime.min.time()) if day else None


def _moment_at(day: date, hm) -> datetime:
    """某天的触发时刻（``day`` + ``hm``）"""
    return datetime(day.year, day.month, day.day, hm[0], hm[1])


def rule_start(rule):
    """规则生效起点（创建时刻）；缺失 / 脏值 → None = 不设下限

    只用于「不要为创建之前的日子补生成任务」，见 ``latest_fired_day()``。
    """
    return parse_moment(rule.get("created_at"))


def rule_fires_on(rule, day: date) -> bool:
    """规则在 ``day`` 这一天是否应该触发（只看日期，不看时刻）"""
    kind = rule.get("kind")
    if kind == KIND_DAILY:
        return True
    if kind == KIND_WEEKLY:
        return day.weekday() in (rule.get("weekdays") or [])
    if kind == KIND_MONTHLY:
        dim = month_days(day.year, day.month)
        for d in rule.get("monthdays") or []:
            if d == day.day:
                return True
            # 「31 号」规则遇到只有 28/30 天的月份 → 当月最后一天触发，
            # 否则 2 月的月报永远不会出现
            if d > dim and day.day == dim:
                return True
        return False
    if kind == KIND_INTERVAL:
        anchor = as_date(rule.get("anchor_date"))
        n = int(rule.get("interval_days") or 0)
        if anchor is None or n <= 0 or day < anchor:
            return False
        return (day - anchor).days % n == 0
    if kind == KIND_NWEEKLY:
        # 每 N 周 + 星期几多选：以 anchor_date 所在的「周一开头的周」为第
        # 0 周期，week_index % N == 0 的周里的选中星期几触发。
        # 周索引按周一对齐（不能按 (day-anchor).days//7 算——锚点在周三时，
        # 下周一会被算进同一个"周"）。
        anchor = as_date(rule.get("anchor_date"))
        n = int(rule.get("interval_weeks") or 0)
        if anchor is None or n <= 0 or day < anchor:
            return False
        if day.weekday() not in (rule.get("weekdays") or []):
            return False
        week0 = anchor - timedelta(days=anchor.weekday())
        week1 = day - timedelta(days=day.weekday())
        return (week1 - week0).days // 7 % n == 0
    return False


def rule_fires(rule, day: date) -> bool:
    """规则在 ``day`` 「原始触发」且未被周末顺延挪走（R2，v1.2.0）。

    ``shift_weekend`` 关闭（老规则缺省）= 与 ``rule_fires_on`` 完全一致，
    **老规则零变化**；开启时周六 / 日的触发日被挪到下周一，周末本身不再算数。
    """
    if not rule_fires_on(rule, day):
        return False
    if not rule.get("shift_weekend"):
        return True
    return day.weekday() < 5


def rule_fires_shifted(rule, day: date) -> bool:
    """开启顺延后 ``day`` 是否为「实际生成日」：自身触发，或由上周末顺延而来。

    顺延目标**只有周一**：周六 → 下周一（+2 天），周日 → 下周一（+1 天）。
    两个周末日顺延到同一个周一也没关系：``pending_day`` 用
    ``last_fired >= due`` 做幂等，同一天只会生成一条任务（撞车自动合并）。
    """
    if rule_fires(rule, day):
        return True
    if not rule.get("shift_weekend") or day.weekday() != 0:
        return False
    for back in (1, 2):                       # 昨天=周日 / 前天=周六
        src = day - timedelta(days=back)
        if src.weekday() >= 5 and rule_fires_on(rule, src):
            return True
    return False


def latest_fired_day(rule, now: datetime, lookback_days: int = SEARCH_DAYS):
    """返回「截至 now 已经到点」的最近一个触发日；没有则 None

    两段判定：
      ① 今天该触发且时刻已过 → 今天
      ② 否则往回找最近一个触发日 —— 这就是「补跑错过的那一次」的来源

    **创建时刻水位**（``created_at``）：只认规则创建之后到点的触发点。
    否则「今天 10:00 新建一条每天 08:00 的规则」会立刻补出一条**昨天**的任务——
    那不是「错过」，那条规则当时还不存在。没有 ``created_at`` 的老文件/手写文件
    不设下限，行为与从前一致。

    注意：它回答的是「最近一次该触发的日子」，**不判断是否已经生成过**
    （那是 ``pending_day()`` 的事，靠 rule["last_fired"] 比较）。
    """
    hm = parse_hm(rule.get("time"))
    if hm is None:
        return None
    today = now.date()
    start = rule_start(rule)
    if rule_fires_shifted(rule, today) and (now.hour, now.minute) >= hm \
            and (start is None or _moment_at(today, hm) >= start):
        return today
    day = today - timedelta(days=1)
    for _ in range(lookback_days):
        if start is not None and _moment_at(day, hm) < start:
            return None                    # 再往前都早于规则创建，不算错过
        if rule_fires_shifted(rule, day):
            return day
        day -= timedelta(days=1)
    return None


def pending_day(rule, now: datetime):
    """该规则此刻**需要生成**哪一天的实例；不需要则 None

    幂等判据：``last_fired`` 已 >= 最近触发日 → 不再生成。
    因此「重复启动程序」「一天内多次 tick」都不会重复生成；
    而「程序关了三天」也只会补最近一次，不会冒出三条。
    """
    if not rule.get("enabled", True):
        return None
    due = latest_fired_day(rule, now)
    if due is None:
        return None
    last = as_date(rule.get("last_fired"))
    if last is not None and last >= due:
        return None
    return due


def next_fire_at(rule, now: datetime):
    """下一次触发时刻（页面展示用；纯推算，不代表程序会在那时自动运行）"""
    hm = parse_hm(rule.get("time"))
    if hm is None:
        return None
    day = now.date()
    for _ in range(SEARCH_DAYS + 1):          # 含今天
        if rule_fires_shifted(rule, day):
            candidate = datetime.combine(day, datetime.min.time()).replace(
                hour=hm[0], minute=hm[1])
            if candidate > now:
                return candidate
        day += timedelta(days=1)
    return None


def deadline_for(rule, fire_day: date) -> str:
    """生成任务的截止日字符串；``due_days == -1`` 表示不设截止日"""
    offset = rule.get("due_days", -1)
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        return ""
    if offset < 0:
        return ""
    return (fire_day + timedelta(days=offset)).isoformat()


def format_rule(rule) -> str:
    """规则的中文摘要（列表里一行说清「什么时候、干什么」）"""
    text = _format_rule_core(rule)
    if rule.get("shift_weekend"):
        text += "（周末顺延到下周一）"
    return text


def _format_rule_core(rule) -> str:
    """format_rule 的主体（shift_weekend 后缀在外面统一追加）"""
    hm = format_hm(rule.get("time")) or "--:--"
    kind = rule.get("kind")
    if kind == KIND_DAILY:
        return f"每天 {hm}"
    if kind == KIND_WEEKLY:
        days = sorted(set(rule.get("weekdays") or []))
        text = "、".join(f"周{WEEKDAY_LABELS[d]}" for d in days if 0 <= d <= 6)
        return f"每{text} {hm}" if text else f"每周 {hm}"
    if kind == KIND_MONTHLY:
        days = sorted(set(rule.get("monthdays") or []))
        text = "、".join(f"{d} 号" for d in days)
        suffix = "（月末自动顺延）" if any(d > 28 for d in days) else ""
        return f"每月 {text} {hm}{suffix}" if text else f"每月 {hm}"
    if kind == KIND_INTERVAL:
        n = int(rule.get("interval_days") or 0)
        anchor = as_date(rule.get("anchor_date"))
        base = f"每 {n} 天 {hm}"
        return f"{base}（自 {anchor.isoformat()} 起）" if anchor else base
    if kind == KIND_NWEEKLY:
        n = int(rule.get("interval_weeks") or 0)
        days = sorted(set(rule.get("weekdays") or []))
        text = "、".join(f"周{WEEKDAY_LABELS[d]}" for d in days if 0 <= d <= 6)
        head = f"每 {n} 周"
        return f"{head} {text} {hm}" if text else f"{head} {hm}"
    return f"未知规则类型 {kind!r}"


def format_next(rule, now: datetime) -> str:
    """「下次触发」的相对文案"""
    nxt = next_fire_at(rule, now)
    if nxt is None:
        return "—"
    delta = (nxt.date() - now.date()).days
    hm = nxt.strftime("%H:%M")
    if delta == 0:
        return f"今天 {hm}"
    if delta == 1:
        return f"明天 {hm}"
    return f"{nxt.month}月{nxt.day}日（周{WEEKDAY_LABELS[nxt.weekday()]}）{hm}"


def validate_rule(raw):
    """校验并归一化一条规则。

    返回 ``(rule | None, error)``：合法 → 归一化 dict；否则原因字符串。
    所有字段都在这里定死形态，UI 与存储层不再各自判一遍。
    """
    if not isinstance(raw, dict):
        return None, "规则不是 JSON 对象"

    title = str(raw.get("title") or "").strip()
    if not title:
        return None, "任务标题不能为空"
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE]

    note = str(raw.get("note") or "").strip()[:MAX_NOTE]

    kind = raw.get("kind")
    if kind not in KINDS:
        return None, f"规则类型非法：{kind!r}"

    hm = format_hm(raw.get("time"))
    if not hm:
        return None, f"时间非法（须为 HH:MM）：{raw.get('time')!r}"

    out = {
        "rule_id": str(raw.get("rule_id") or uuid.uuid4().hex[:12]),
        "title": title,
        "note": note,
        "enabled": bool(raw.get("enabled", True)),
        "kind": kind,
        "time": hm,
        "last_fired": "",
        # 创建时刻 = 「补跑」的时间下限（见 latest_fired_day）。
        # **这里刻意不兜底填「现在」**：读老文件/手写文件时凭空盖一个「刚刚创建」，
        # 会让该规则永远不为过去补跑，而且每次启动水位都在变；缺省留空 =
        # 不设下限，行为与老版本一致。
        "created_at": str(raw.get("created_at") or "").strip(),
        # 周末顺延（R2）：周六 / 日的触发日挪到下周一生成；缺省 False，
        # 老规则没有这个键 = 行为与从前完全一致
        "shift_weekend": bool(raw.get("shift_weekend", False)),
    }

    if kind == KIND_WEEKLY:
        raw_days = raw.get("weekdays")
        if not isinstance(raw_days, (list, tuple)) or not raw_days:
            return None, "每周规则至少要选一个星期几"
        days = []
        for d in raw_days:
            if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6:
                return None, f"星期几取值非法（0=周一 … 6=周日）：{d!r}"
            if d not in days:
                days.append(d)
        out["weekdays"] = sorted(days)

    elif kind == KIND_NWEEKLY:
        raw_days = raw.get("weekdays")
        if not isinstance(raw_days, (list, tuple)) or not raw_days:
            return None, "每 N 周规则至少要选一个星期几"
        days = []
        for d in raw_days:
            if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6:
                return None, f"星期几取值非法（0=周一 … 6=周日）：{d!r}"
            if d not in days:
                days.append(d)
        out["weekdays"] = sorted(days)
        n = raw.get("interval_weeks")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 52:
            return None, f"间隔周数必须在 1–52 之间：{n!r}"
        # 锚点 = 创建当周（周一开头）；编辑保留原锚点，周期不漂移
        anchor = (as_date(raw.get("anchor_date"))
                  or datetime.now().date() - timedelta(
                      days=datetime.now().weekday()))
        out["interval_weeks"] = n
        out["anchor_date"] = anchor.isoformat()

    elif kind == KIND_MONTHLY:
        raw_days = raw.get("monthdays")
        if not isinstance(raw_days, (list, tuple)) or not raw_days:
            return None, "每月规则至少要选一个号数"
        days = []
        for d in raw_days:
            if not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 31:
                return None, f"号数取值非法（1–31）：{d!r}"
            if d not in days:
                days.append(d)
        out["monthdays"] = sorted(days)

    elif kind == KIND_INTERVAL:
        n = raw.get("interval_days")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 365:
            return None, f"间隔天数必须在 1–365 之间：{n!r}"
        anchor = as_date(raw.get("anchor_date")) or datetime.now().date()
        out["interval_days"] = n
        out["anchor_date"] = anchor.isoformat()

    offset = raw.get("due_days", -1)
    if not isinstance(offset, int) or isinstance(offset, bool) \
            or not -1 <= offset <= 365:
        return None, f"截止日偏移非法（-1 或 0–365）：{offset!r}"
    out["due_days"] = offset

    last = as_date(raw.get("last_fired"))
    out["last_fired"] = last.isoformat() if last else ""
    # 最近一次生成记录（R4）：编辑 / 导入时透传——out 是白名单重建，
    # 不在这里显式搬运的话，编辑一次规则就会把「上次生成的任务」冲掉
    last_task = raw.get("last_task")
    if isinstance(last_task, dict):
        out["last_task"] = {
            "title": str(last_task.get("title") or "")[:MAX_TITLE],
            "at": str(last_task.get("at") or "")[:32],
        }
    return out, ""


def normalize_rules(raw_list):
    """批量归一化（读盘用）：丢掉坏条目并收集原因，保证一条坏的拖不垮全部"""
    rules, errors = [], []
    for i, item in enumerate(raw_list or ()):
        rule, err = validate_rule(item)
        if rule is None:
            errors.append(f"第 {i + 1} 条规则已跳过：{err}")
            continue
        rules.append(rule)
        if len(rules) >= MAX_RULES:
            errors.append(f"规则数超过上限 {MAX_RULES}，其余已忽略")
            break
    return rules, errors


def import_rules(existing, raw_data):
    """把外部 JSON 并进现有规则（R3，v1.2.0）：逐条校验 + rule_id 去重。

    返回 ``(merged, added, skipped, bad)``：merged 是合并后的完整列表
    （原列表不被修改）；rule_id 与现有或本批撞车 → 跳过（现役优先，
    不覆盖用户已经在跑的规则）；坏条目只计数。上限 MAX_RULES 照守。
    """
    if isinstance(raw_data, dict):
        raw_list = raw_data.get("rules")
    elif isinstance(raw_data, list):
        raw_list = raw_data
    else:
        raw_list = None
    merged = [dict(r) for r in existing or []]
    known = {r.get("rule_id") for r in merged}
    added = skipped = bad = 0
    for item in raw_list or []:
        rule, _err = validate_rule(item)
        if rule is None:
            bad += 1
            continue
        if rule["rule_id"] in known:
            skipped += 1
            continue
        known.add(rule["rule_id"])
        merged.append(rule)
        added += 1
        if len(merged) >= MAX_RULES:
            break
    return merged, added, skipped, bad


# ====================================================================
# 存储层（纯逻辑，路径注入 —— 便于单测，不依赖 Qt）
# ====================================================================
class RuleStore:
    """规则持久化：``<data_dir>/rules.json``，原子写，坏文件降级为空"""

    def __init__(self, data_dir: str, logger=None):
        self._dir = str(data_dir or "")
        self._logger = logger

    @property
    def available(self) -> bool:
        """插件私有目录是否可用（宿主未注入 → 规则只能存在内存里）"""
        return bool(self._dir)

    @property
    def path(self) -> str:
        return os.path.join(self._dir, RULES_FILENAME) if self._dir else ""

    def load(self):
        """读规则：文件不存在 / 坏 JSON / 条目不合法 → 降级并返回 ``(rules, warnings)``

        绝不抛异常：读不出来就当作「还没有规则」，用户重新建即可。
        """
        warnings = []
        path = self.path
        if not path or not os.path.isfile(path):
            return [], warnings
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            warnings.append(f"规则文件读取失败（{exc}）；已按空规则启动，"
                            f"原文件保留：{path}")
            return [], warnings
        if isinstance(data, dict):
            raw_rules = data.get("rules")
        elif isinstance(data, list):            # 兼容裸数组写法
            raw_rules = data
        else:
            warnings.append("规则文件结构不对（顶层既不是对象也不是数组）")
            return [], warnings
        rules, errors = normalize_rules(raw_rules)
        warnings.extend(errors)
        return rules, warnings

    def save(self, rules) -> bool:
        """原子落盘（临时文件 + os.replace），失败返回 False

        与宿主数据层同口径：先写同目录临时文件再替换，避免断电/崩溃留下半截 JSON。
        """
        path = self.path
        if not path:
            return False
        payload = {"version": SCHEMA_VERSION,
                   "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "rules": list(rules or ())}
        tmp = path + ".tmp"
        try:
            os.makedirs(self._dir, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except OSError as exc:
            self._warn(f"规则落盘失败：{exc}")
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[{PLUGIN_ID}] {msg}")


# ====================================================================
# 调度层：轮询 + 补跑 + 幂等
# ====================================================================
class RuleScheduler(QObject):
    """60s 轮询规则，到点调用 ``ctx.write.add_task`` 生成任务。

    信号 ``fired(str)``：一次成功生成（文案给页面/toast 用）；
    ``failed(str)``：一次生成失败（文案说明原因）。
    """

    fired = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, store: RuleStore, logger=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._logger = logger
        self._ctx = None
        self._rules = []
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)
        # 已尝试过但失败的触发日（规则 id → 日期）。避免未授权 / 写失败时
        # 每 60s 重复尝试并刷日志；日期变化后自动失效（不需要清理）。
        self._failed_marks = {}

    # ---------------- 规则读写 ----------------
    def bind(self, ctx):
        """绑定/刷新宿主上下文（rescan 会派生新 ctx，这里以最后一次为准）"""
        self._ctx = ctx

    def load(self):
        """从磁盘重读规则（返回读取告警列表）

        数据目录不可用时**保留内存里的规则**——否则每次切入页面
        （``showEvent`` 会调本方法）都会把用户刚建的规则清空。
        """
        if not self._store.available:
            return []
        rules, warnings = self._store.load()
        self._rules = rules
        for w in warnings:
            self._warn(w)
        return warnings

    def store_available(self) -> bool:
        """规则能否持久化（False = 宿主未注入插件数据目录，仅存内存）"""
        return self._store.available

    def rules(self):
        return list(self._rules)

    def save(self) -> bool:
        return self._store.save(self._rules)

    def set_rules(self, rules):
        self._rules = list(rules or ())

    # ---------------- 生命周期 ----------------
    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self._info(f"调度启动（间隔 {TICK_MS // 1000}s，规则 {len(self._rules)} 条）")

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
            self._info("调度已停止（插件被停用）")

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ---------------- 核心：tick ----------------
    def tick(self, now=None):
        """检查所有规则并生成到点的任务。

        返回本次生成的 ``[(rule, task_id, fire_day_str), ...]``（便于测试断言）。
        任何单条规则出错都不影响其他规则（与宿主「一条坏插件不拖垮别的」同精神）。
        """
        now = now or datetime.now()
        created = []
        for rule in self._rules:
            try:
                day = pending_day(rule, now)
            except Exception as exc:              # noqa: BLE001 - 脏规则不拖垮调度
                self._warn(f"规则计算失败，已跳过：{rule.get('rule_id')} -> {exc!r}")
                continue
            if day is None:
                continue
            if self._failed_marks.get(rule.get("rule_id")) == day.isoformat():
                continue                          # 这一天已经试过且失败过
            ok, detail = self._fire(rule, day, now)
            if ok:
                created.append((rule, detail, day.isoformat()))
            else:
                self._failed_marks[rule.get("rule_id")] = day.isoformat()
                self.failed.emit(f"「{rule.get('title')}」生成失败：{detail}")
                self._warn(f"生成失败（{rule.get('title')} / {day}）：{detail}")
        if created:
            self.save()
        return created

    def _fire(self, rule, fire_day: date, now: datetime | None = None):
        """生成一条任务并推进 ``last_fired``。返回 ``(ok, task_id 或原因)``"""
        now = now or datetime.now()
        if self._ctx is None:
            return False, "插件上下文不可用"
        deadline = deadline_for(rule, fire_day)
        try:
            task_id = self._ctx.write.add_task(
                rule.get("title", ""), rule.get("note", ""), deadline)
        except Exception as exc:                  # noqa: BLE001 - 桥异常不反噬调度
            return False, f"写入抛异常 {exc!r}"
        if not task_id:
            return False, ("宿主拒绝了写入（常见原因：插件未声明 write 能力，"
                           "或宿主未开放数据写入）")
        # 成功才推进 last_fired —— 幂等的锚点
        rule["last_fired"] = fire_day.isoformat()
        # 最近一次生成记录（R4）：给页面卡片显示「上次生成了哪条任务」。
        # 标题取生成那一刻的规则标题——之后改了规则标题也不会混淆历史。
        rule["last_task"] = {
            "title": rule.get("title", ""),
            "at": now.strftime("%Y-%m-%d %H:%M"),
        }
        self.fired.emit(f"已生成任务「{rule.get('title', '')}」"
                        f"（{fire_day.isoformat()} 的周期）")
        self._info(f"生成任务 #{task_id}：{rule.get('title')} "
                   f"（规则 {rule.get('rule_id')}，触发日 {fire_day}）")
        return True, str(task_id)

    def check_now(self):
        """手动「立即检查」：绕过 60s 等待直接跑一次"""
        return self.tick()

    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[{PLUGIN_ID}] {msg}")

    def _info(self, msg: str):
        if self._logger is not None:
            self._logger.info(f"[{PLUGIN_ID}] {msg}")


# ====================================================================
# 插件级单例状态（rescan 会重复触发钩子，必须幂等）
# ====================================================================
_STATE = {
    "ctx": None,          # 最近一次绑定的宿主上下文
    "store": None,        # RuleStore
    "scheduler": None,    # RuleScheduler
    "page": None,         # 当前挂着的页面（tick 后刷新它）
    "toast_shown": False, # 本次进程内是否已提示过「数据目录不可用」
}


def ensure_scheduler(ctx):
    """取得（必要时创建）调度器，并绑定最新 ctx。

    幂等是硬要求：插件中心「重新扫描」会 deactivate → activate，
    钩子被重复调用；若每次 on_enable 都新建 QTimer，同一台机器上就会
    并存多个调度器（虽有 last_fired 兜底，但属于设计缺陷）。
    """
    sched = _STATE["scheduler"]
    if sched is None:
        store = RuleStore(ctx.data_dir, ctx.logger)
        sched = RuleScheduler(store, logger=ctx.logger)
        sched.load()
        _STATE["store"] = store
        _STATE["scheduler"] = sched
        if not store.available:
            _warn_no_dir(ctx)
    _STATE["ctx"] = ctx
    sched.bind(ctx)
    return sched


def _warn_no_dir(ctx):
    """插件私有目录不可用 → 说清后果，不假装能存（每次进程只提示一次）"""
    if _STATE.get("toast_shown"):
        return
    _STATE["toast_shown"] = True
    logger = getattr(ctx, "logger", None)
    if logger is not None:
        logger.warning(f"[{PLUGIN_ID}] 插件数据目录不可用，规则无法持久化"
                       f"（重启后丢失）")
    try:
        ctx.show_toast("周期任务：无法写入插件数据目录，规则只保存在内存中",
                       4200)
    except Exception:                             # noqa: BLE001
        pass


# ====================================================================
# 规则编辑对话框
# ====================================================================
class RuleDialog(PluginDialog):
    """新建 / 编辑一条周期规则（继承宿主 PluginDialog，主题自动跟随）"""

    def __init__(self, ctx, rule=None, parent=None):
        super().__init__(ctx, title="周期规则",
                         subtitle="到点自动生成任务",
                         parent=parent, size=(660, 520))
        self._ctx = ctx
        self._editing = dict(rule) if rule else None
        self._build_ui()
        self._load(rule)

    # ---------------- 界面 ----------------
    def _build_ui(self):
        body = self.body_layout
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(10)

        card = QFrame()
        card.setObjectName("glassCard")
        form = QVBoxLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        # 任务标题
        row = QHBoxLayout()
        row.addWidget(make_section_label("任务标题"))
        self._title = QLineEdit()
        self._title.setPlaceholderText("例如：周会准备 / 月报归档 / 交房租")
        row.addWidget(self._title, 1)
        form.addLayout(row)

        # 备注
        row_note = QHBoxLayout()
        row_note.addWidget(make_section_label("备注"))
        self._note = QLineEdit()
        self._note.setPlaceholderText("可留空（会写进生成的任务备注里）")
        row_note.addWidget(self._note, 1)
        form.addLayout(row_note)

        # 规则类型 + 时间
        row2 = QHBoxLayout()
        row2.addWidget(make_section_label("触发规则"))
        self._kind = QComboBox()
        for key, text in KIND_LABELS:
            self._kind.addItem(text, key)
        row2.addWidget(self._kind, 1)
        row2.addWidget(QLabel("时间"))
        self._time = QTimeEdit()
        self._time.setDisplayFormat("HH:mm")
        self._time.setTime(QTime(9, 0))
        row2.addWidget(self._time)
        form.addLayout(row2)

        # 参数区：按类型切换（每周 / 每月 / 每 N 天）
        self._param_box = QFrame()
        pv = QVBoxLayout(self._param_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)

        # —— 每周几
        self._week_row = QWidget()
        wr = QHBoxLayout(self._week_row)
        wr.setContentsMargins(0, 0, 0, 0)
        wr.setSpacing(6)
        wr.addWidget(make_section_label("星期"))
        self._week_checks = []
        for i, label in enumerate(WEEKDAY_LABELS):
            cb = QCheckBox(f"周{label}")
            self._week_checks.append(cb)
            wr.addWidget(cb)
        wr.addStretch(1)
        pv.addWidget(self._week_row)

        # —— 每月几号
        self._month_row = QWidget()
        mr = QHBoxLayout(self._month_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(6)
        mr.addWidget(make_section_label("每月号数"))
        self._month_edit = QLineEdit()
        self._month_edit.setPlaceholderText("逗号分隔，如 1,15,31（不足 31 天的月份自动顺延到月末）")
        mr.addWidget(self._month_edit, 1)
        pv.addWidget(self._month_row)

        # —— 每 N 天
        self._interval_row = QWidget()
        ir = QHBoxLayout(self._interval_row)
        ir.setContentsMargins(0, 0, 0, 0)
        ir.setSpacing(6)
        ir.addWidget(make_section_label("间隔天数"))
        self._interval = QLineEdit()
        self._interval.setPlaceholderText("1–365，如 14")
        self._interval.setFixedWidth(90)
        ir.addWidget(self._interval)
        ir.addWidget(make_hint_label("从今天起算；程序没开时错过的那一次会在启动后补上"))
        ir.addStretch(1)
        pv.addWidget(self._interval_row)

        # —— 每 N 周（v1.1.0）：间隔周数 + 复用上面那排星期勾选
        self._nweeks_row = QWidget()
        nr = QHBoxLayout(self._nweeks_row)
        nr.setContentsMargins(0, 0, 0, 0)
        nr.setSpacing(6)
        nr.addWidget(make_section_label("间隔周数"))
        self._nweeks = QLineEdit()
        self._nweeks.setPlaceholderText("1–52，如 2（= 隔周）")
        self._nweeks.setFixedWidth(90)
        nr.addWidget(self._nweeks)
        nr.addWidget(make_hint_label(
            "从本周期（创建那周）起算，勾选的星期几到点各生成一条"))
        nr.addStretch(1)
        pv.addWidget(self._nweeks_row)

        form.addWidget(self._param_box)

        # 截止日策略
        row3 = QHBoxLayout()
        row3.addWidget(make_section_label("生成任务的截止日"))
        self._due = QComboBox()
        for value, text in DUE_OFFSETS:
            self._due.addItem(text, value)
        self._due.setCurrentIndex(0)
        row3.addWidget(self._due)
        row3.addWidget(make_hint_label("只影响生成出来的任务，不影响触发时刻"))
        row3.addStretch(1)
        form.addLayout(row3)

        # 周末顺延（R2）：周六 / 日的触发日挪到下周一
        shift_row = QHBoxLayout()
        self._shift = QCheckBox("周末触发日顺延到下周一（休息日不生成任务）")
        self._shift.setToolTip(
            "勾选后，落在周六 / 日的触发日改到下一个周一的同一时刻生成；"
            "多个周末日顺延到同一个周一也只生成一条任务")
        self._shift.toggled.connect(self._refresh_preview)
        shift_row.addWidget(self._shift)
        shift_row.addStretch(1)
        form.addLayout(shift_row)

        body.addWidget(card)

        # 预览行：让用户确认「下次触发」长什么样
        self._preview = QLabel("")
        self._preview.setObjectName("hintLabel")
        self._preview.setMinimumHeight(20)
        body.addWidget(self._preview)

        self._error = QLabel("")
        self._error.setObjectName("pluginErrorHint")
        self._error.setWordWrap(True)
        body.addWidget(self._error)
        body.addStretch(1)

        self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            ("保存", "primaryBtn", self._on_save),
        ])

        self._kind.currentIndexChanged.connect(self._on_kind_changed)
        for w in (self._title, self._note, self._month_edit, self._interval,
                  self._nweeks):
            w.textChanged.connect(self._refresh_preview)
        self._time.timeChanged.connect(self._refresh_preview)
        for cb in self._week_checks:
            cb.toggled.connect(self._refresh_preview)
        self._due.currentIndexChanged.connect(self._refresh_preview)
        self._on_kind_changed()

    def _on_kind_changed(self, *_args):
        kind = self._kind.currentData()
        self._week_row.setVisible(kind in (KIND_WEEKLY, KIND_NWEEKLY))
        self._month_row.setVisible(kind == KIND_MONTHLY)
        self._interval_row.setVisible(kind == KIND_INTERVAL)
        self._nweeks_row.setVisible(kind == KIND_NWEEKLY)
        self._refresh_preview()

    # ---------------- 数据 <-> 控件 ----------------
    def _load(self, rule):
        if not rule:
            self._refresh_preview()
            return
        self._title.setText(rule.get("title", ""))
        self._note.setText(rule.get("note", ""))
        idx = self._kind.findData(rule.get("kind"))
        if idx >= 0:
            self._kind.setCurrentIndex(idx)
        hm = parse_hm(rule.get("time"))
        if hm:
            self._time.setTime(QTime(hm[0], hm[1]))
        for d in rule.get("weekdays") or []:
            if 0 <= d < 7:
                self._week_checks[d].setChecked(True)
        if rule.get("monthdays"):
            self._month_edit.setText(",".join(str(d) for d in rule["monthdays"]))
        if rule.get("interval_days"):
            self._interval.setText(str(rule["interval_days"]))
        if rule.get("interval_weeks"):
            self._nweeks.setText(str(rule["interval_weeks"]))
        idx = self._due.findData(rule.get("due_days", -1))
        if idx >= 0:
            self._due.setCurrentIndex(idx)
        self._shift.setChecked(bool(rule.get("shift_weekend", False)))
        self._on_kind_changed()
        self._refresh_preview()

    def _collect(self):
        """从控件收集一条规则原始 dict（校验交给 validate_rule）"""
        raw = {
            "title": self._title.text(),
            "note": self._note.text(),
            "kind": self._kind.currentData(),
            "time": self._time.time().toString("HH:mm"),
            "due_days": self._due.currentData(),
            "shift_weekend": self._shift.isChecked(),
            "enabled": True,
        }
        if self._editing:
            raw["rule_id"] = self._editing.get("rule_id")
            raw["created_at"] = self._editing.get("created_at")
            raw["last_fired"] = self._editing.get("last_fired", "")
            raw["enabled"] = self._editing.get("enabled", True)
            # 「上次生成记录」透传：编辑不改历史（validate_rule 会搬运）
            if isinstance(self._editing.get("last_task"), dict):
                raw["last_task"] = self._editing.get("last_task")
        else:
            # 新建：记下创建时刻，作为「补跑」的下限——不给创建之前的日子
            # 补生成任务（否则 10:00 建一条「每天 08:00」会立刻冒出一条昨天的）
            raw["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        kind = raw["kind"]
        if kind in (KIND_WEEKLY, KIND_NWEEKLY):
            raw["weekdays"] = [i for i, cb in enumerate(self._week_checks)
                               if cb.isChecked()]
        elif kind == KIND_MONTHLY:
            raw["monthdays"] = self._parse_month_days()
        elif kind == KIND_INTERVAL:
            raw["interval_days"] = self._parse_interval()
            anchor = as_date((self._editing or {}).get("anchor_date"))
            raw["anchor_date"] = (anchor or datetime.now().date()).isoformat()
        if kind == KIND_NWEEKLY:
            raw["interval_weeks"] = self._parse_nweeks()
            anchor = as_date((self._editing or {}).get("anchor_date"))
            if anchor is None:
                today = datetime.now().date()
                anchor = today - timedelta(days=today.weekday())
            raw["anchor_date"] = anchor.isoformat()
        return raw

    def _parse_month_days(self):
        "把 1,15,31 解析成 [1,15,31]（非法项返回 None 交给校验报错）"
        text = self._month_edit.text().replace("，", ",").strip()
        if not text:
            return []
        days = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                days.append(int(part))
            except ValueError:
                return None
        return days

    def _parse_interval(self):
        text = self._interval.text().strip()
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    def _parse_nweeks(self):
        text = self._nweeks.text().strip()
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    def _refresh_preview(self, *_args):
        rule, err = validate_rule(self._collect())
        if rule is None:
            self._preview.setText("")
            self._error.setText(f"{err}")
            return
        rule["enabled"] = True
        self._error.setText("")
        offset = rule.get("due_days", -1)
        due_text = ("不设截止日" if offset < 0
                    else f"截止日 = 触发日 + {offset} 天")
        self._preview.setText(
            f"规则：{format_rule(rule)} ｜ 下次触发：{format_next(rule, datetime.now())}"
            f" ｜ {due_text}")

    # ---------------- 保存 ----------------
    def _on_save(self):
        rule, err = validate_rule(self._collect())
        if rule is None:
            self._error.setText(f"{err}")
            return
        self._result = rule
        self.accept()

    def result_rule(self):
        """保存后的规则（未保存时为 None）"""
        return getattr(self, "_result", None)


# ====================================================================
# 插件页面
# ====================================================================
class CycleTasksPage(QWidget):
    """周期任务页：规则列表 + 新建/编辑/启停/删除 + 立即检查"""

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self.setObjectName("pluginPage")
        self._scheduler = ensure_scheduler(ctx)
        self._build_ui()
        self._scheduler.fired.connect(self._on_fired)
        self._scheduler.failed.connect(self._on_failed)
        self.reload_rules()

    # ---------------- 界面 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("周期任务")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addWidget(make_hint_label(
            "只给规则，到点自动生成任务；宿主「任务到期提醒」照旧生效"))
        head.addStretch(1)
        self._count = QLabel("")
        self._count.setObjectName("hintLabel")
        head.addWidget(self._count)
        root.addLayout(head)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        add_btn = QPushButton("新建规则")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(add_btn)

        check_btn = QPushButton("立即检查")
        check_btn.setObjectName("secondaryBtn")
        check_btn.setToolTip("不等 60s 轮询，马上按规则检查一次")
        check_btn.clicked.connect(self._on_check_now)
        toolbar.addWidget(check_btn)

        export_btn = QPushButton("导出规则")
        export_btn.setObjectName("secondaryBtn")
        export_btn.setToolTip("把全部规则导出成 JSON 文件（备份 / 换机用）")
        export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(export_btn)

        import_btn = QPushButton("导入规则")
        import_btn.setObjectName("secondaryBtn")
        import_btn.setToolTip("从导出的 JSON 文件并入规则（重复 rule_id 自动跳过）")
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)

        self._state_label = QLabel("")
        self._state_label.setObjectName("hintLabel")
        toolbar.addWidget(self._state_label)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # 滚动区：规则卡片
        scroll = QScrollArea()
        scroll.setObjectName("pluginsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("pluginsContainer")
        self._list = QVBoxLayout(container)
        self._list.setContentsMargins(0, 0, 8, 0)
        self._list.setSpacing(10)
        self._empty = QLabel(
            "还没有周期规则\n\n"
            "点「新建规则」加一条，例如：\n"
            "  · 每周五 15:00 —— 写周报\n"
            "  · 每月 1 号 09:00 —— 归档上月资料\n"
            "  · 每 14 天 09:00 —— 复盘\n\n"
            "到点会自动在任务页生成任务；程序没运行时错过的那一次，"
            "下次启动后补上（只补最近一次）")
        self._empty.setObjectName("pluginEmptyHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("padding: 40px;")
        self._empty.setWordWrap(True)
        self._list.addWidget(self._empty)
        self._list.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        foot = QLabel("提示：「下次触发」是推算值——程序没运行时不会自动生成，"
                      "重启后补跑最近一次；新建的规则从保存那一刻起算，"
                      "不为创建之前的日子补任务（规则文件见插件数据目录）")
        foot.setObjectName("hintLabel")
        foot.setWordWrap(True)
        root.addWidget(foot)

    # ---------------- 列表渲染 ----------------
    def reload_rules(self):
        """重建规则列表（清空时先 hide 再 deleteLater，避免残影）"""
        for i in reversed(range(self._list.count())):
            item = self._list.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None and w is not self._empty:
                self._list.takeAt(i)
                w.hide()
                w.deleteLater()

        sched = self._scheduler
        rules = sched.rules()
        self._empty.setVisible(not rules)
        enabled = sum(1 for r in rules if r.get("enabled", True))
        self._count.setText(f"共 {len(rules)} 条规则，启用 {enabled} 条"
                            if rules else "暂无规则")
        state = "运行中" if sched.is_running() else "已停止"
        writable = "可写" if sched.store_available() else "不可写（规则仅存内存）"
        self._state_label.setText(f"调度：{state} ｜ 数据目录：{writable}")

        now = datetime.now()
        for rule in rules:
            self._list.insertWidget(self._list.count() - 1,
                                    self._make_card(rule, now))

    def _make_card(self, rule, now) -> QWidget:
        card = QFrame()
        card.setObjectName("pluginCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        name = QLabel(rule.get("title", ""))
        name.setObjectName("pluginCardTitle")
        head.addWidget(name)
        tag = QLabel("已启用" if rule.get("enabled", True) else "已停用")
        tag.setObjectName("pluginStatusOn" if rule.get("enabled", True)
                          else "pluginStatusOff")
        head.addWidget(tag)
        head.addStretch(1)
        rid = QLabel(rule.get("rule_id", ""))
        rid.setObjectName("pluginCardId")
        head.addWidget(rid)
        v.addLayout(head)

        line = QLabel(f"规则：{format_rule(rule)}"
                      f"　下次触发：{format_next(rule, now)}"
                      f"　截止日：{DUE_OFFSET_LABEL.get(rule.get('due_days', -1), '—')}")
        line.setObjectName("pluginCardDesc")
        line.setWordWrap(True)
        v.addWidget(line)

        last = rule.get("last_fired") or "从未生成"
        note = rule.get("note") or ""
        # R4：上次生成的任务标题 + 时刻（生成那一刻的规则标题，改标题不混淆历史）
        last_task = rule.get("last_task") or {}
        task_title = str(last_task.get("title") or "").strip()
        task_at = str(last_task.get("at") or "").strip()
        meta_bits = [f"上次生成：{last}"]
        if task_title:
            meta_bits.append("任务「" + task_title + "」"
                             + (f"（{task_at}）" if task_at else ""))
        if note:
            meta_bits.append(f"备注：{note}")
        meta = QLabel("　".join(meta_bits))
        meta.setObjectName("pluginCardId")
        meta.setWordWrap(True)
        v.addWidget(meta)

        bottom = QHBoxLayout()
        bottom.addStretch(1)

        toggle = QPushButton("停用" if rule.get("enabled", True) else "启用")
        toggle.setObjectName("secondaryBtn")
        toggle.clicked.connect(
            lambda _checked=False, r=dict(rule): self._on_toggle(r))
        bottom.addWidget(toggle)

        edit = QPushButton("编辑")
        edit.setObjectName("secondaryBtn")
        edit.clicked.connect(lambda _checked=False, r=dict(rule): self._on_edit(r))
        bottom.addWidget(edit)

        delete = QPushButton("删除")
        delete.setObjectName("secondaryBtn")
        delete.clicked.connect(
            lambda _checked=False, r=dict(rule): self._on_delete(r))
        bottom.addWidget(delete)

        v.addLayout(bottom)
        return card

    # ---------------- 操作 ----------------
    def _replace_rule(self, rule):
        """按 rule_id 写入调度器并落盘（不存在则追加）"""
        sched = self._scheduler
        rules = sched.rules()
        for i, r in enumerate(rules):
            if r.get("rule_id") == rule.get("rule_id"):
                rules[i] = rule
                break
        else:
            rules.append(rule)
        sched.set_rules(rules)
        if not sched.save():
            self._toast("规则已生效，但写入磁盘失败（重启后会丢失）")
        self.reload_rules()

    def _on_add(self):
        dlg = RuleDialog(self._ctx, None, self._dialog_parent())
        if dlg.exec() and dlg.result_rule():
            self._replace_rule(dlg.result_rule())
            self._toast("规则已保存")

    def _on_edit(self, rule):
        dlg = RuleDialog(self._ctx, rule, self._dialog_parent())
        if dlg.exec() and dlg.result_rule():
            self._replace_rule(dlg.result_rule())
            self._toast("规则已更新")

    def _on_toggle(self, rule):
        rule["enabled"] = not rule.get("enabled", True)
        self._replace_rule(rule)

    def _on_delete(self, rule):
        answer = QMessageBox.question(
            self, "删除规则",
            f"确定删除规则「{rule.get('title')}」吗？\n\n"
            f"规则：{format_rule(rule)}\n"
            f"注意：已经生成的任务不会被删除（它们已经是普通任务）。\n\n"
            f"此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        sched = self._scheduler
        rules = [r for r in sched.rules()
                 if r.get("rule_id") != rule.get("rule_id")]
        sched.set_rules(rules)
        sched.save()
        self.reload_rules()
        self._toast("已删除规则")

    def _on_check_now(self):
        created = self._scheduler.check_now()
        self.reload_rules()
        if created:
            self._toast(f"已生成 {len(created)} 条任务")
        else:
            self._toast("没有到点的规则（或今天已经生成过）")
        self._refresh_host_tasks()

    # ---------------- 导出 / 导入（R3） ----------------
    def _on_export(self):
        """全部规则 → JSON 文件（QFileDialog 选路径，utf-8 + LF）"""
        rules = self._scheduler.rules()
        if not rules:
            self._toast("还没有可导出的规则")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出周期任务规则", "周期任务规则.json", "JSON 文件 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        payload = {
            "version": SCHEMA_VERSION,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rules": rules,
        }
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            self._toast(f"导出失败：{exc}")
            return
        self._toast(f"已导出 {len(rules)} 条规则")

    def _read_json_file(self, path):
        """读导入文件 → JSON 对象；读取失败 toast 并返回 None"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            self._toast(f"读取失败：{exc}")
            return None

    def _on_import(self):
        """从 JSON 文件并入规则：逐条校验、rule_id 去重（现役优先）"""
        path, _sel = QFileDialog.getOpenFileName(
            self, "导入周期任务规则", "", "JSON 文件 (*.json)")
        if not path:
            return
        data = self._read_json_file(path)
        if data is None:
            return
        merged, added, skipped, bad = import_rules(
            self._scheduler.rules(), data)
        if added == 0 and skipped == 0 and bad == 0:
            self._toast("文件里没有可导入的规则")
            return
        parts = [f"新增 {added} 条"]
        if skipped:
            parts.append(f"跳过重复 {skipped} 条")
        if bad:
            parts.append(f"坏数据 {bad} 条")
        if added:
            self._scheduler.set_rules(merged)
            if not self._scheduler.save():
                self._toast("规则已生效，但写入磁盘失败（重启后会丢失）")
            self.reload_rules()
        self._toast("导入完成：" + "，".join(parts))

    # ---------------- 调度器信号 ----------------
    def _on_fired(self, text):
        self._toast(text)
        if self.isVisible():          # 页面没显示时不重建卡片（切入时会重读）
            self.reload_rules()
        self._refresh_host_tasks()

    def _on_failed(self, text):
        self._toast(f"{text}", 4200)

    def _refresh_host_tasks(self):
        """生成任务后让宿主任务页 / 悬浮球徽标同步（拿不到就静默跳过）"""
        _refresh_host_tasks(self._ctx)

    def _dialog_parent(self):
        try:
            return self._ctx.parent_window() or self
        except Exception:                          # noqa: BLE001
            return self

    def _toast(self, text, ms=2800):
        _safe_toast(self._ctx, text, ms)

    def showEvent(self, event):
        """每次切入本页都重读一遍（规则文件可能被手工改过）"""
        super().showEvent(event)
        self._scheduler.load()
        self.reload_rules()


# ====================================================================
# 动作与插件
# ====================================================================
class ManageRulesAction(BallAction):
    """打开周期任务页（在宿主主窗口里）；宿主不支持页面时退回弹窗说明"""

    id = f"{PLUGIN_ID}.manage"
    title = "周期任务"

    def run(self, ctx):
        ensure_scheduler(ctx)
        holder = {}

        def _open():
            host = ctx.parent_window()
            show = getattr(host, "show_plugin_page", None) if host else None
            if callable(show):
                try:
                    if show(PAGE_KEY):
                        return
                except Exception as exc:           # noqa: BLE001
                    ctx.logger.warning(f"[{PLUGIN_ID}] 打开插件页失败：{exc!r}")
            # 兜底：宿主没有页面机制时，用弹窗给出关键信息
            _fallback_dialog(ctx, holder)

        QTimer.singleShot(0, _open)


class CheckNowAction(BallAction):
    """立即按规则检查一次（不等轮询）"""

    id = f"{PLUGIN_ID}.check-now"
    title = "立即检查周期任务"

    def run(self, ctx):
        sched = ensure_scheduler(ctx)
        created = sched.check_now()
        _safe_toast(ctx, f"周期任务：已生成 {len(created)} 条任务" if created
                    else "周期任务：没有到点的规则（或今天已经生成过）")
        if created:
            QTimer.singleShot(0, lambda: _refresh_host_tasks(ctx))


def _safe_toast(ctx, text, ms=2800):
    """toast 是「锦上添花」，抛异常不能反噬动作本身"""
    try:
        ctx.show_toast(text, ms)
    except Exception:                             # noqa: BLE001
        pass


def _refresh_host_tasks(ctx):
    """让宿主任务页 / 悬浮球徽标同步（拿不到 refresh_tasks 就静默跳过）"""
    try:
        host = ctx.parent_window()
    except Exception:                             # noqa: BLE001
        host = None
    fn = getattr(host, "refresh_tasks", None) if host else None
    if callable(fn):
        try:
            fn()
        except Exception:                         # noqa: BLE001
            pass


def _fallback_dialog(ctx, holder):
    """宿主不支持插件页面时的降级说明弹窗（保证动作不是「点了没反应」）"""
    dlg = PluginDialog(ctx, title="周期任务",
                       subtitle="本版本宿主未提供插件页面", size=(560, 380))
    text = QLabel(
        "当前宿主版本不支持插件页面，无法在这里管理规则。\n\n"
        "你仍然可以使用：\n"
        "  · 悬浮球右键 → 立即检查周期任务\n"
        "  · 热键 Ctrl+Alt+R\n\n"
        "规则文件位置：\nfloat_data/plugins/recurring-tasks/rules.json")
    text.setWordWrap(True)
    text.setObjectName("hintLabel")
    dlg.body_layout.addWidget(text)
    dlg.add_footer([("关闭", "primaryBtn", dlg.accept)])
    holder["dialog"] = dlg
    dlg.exec()
    holder.pop("dialog", None)


class RecurringTasksPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "周期任务"
    version = "1.2.0"

    def create_actions(self, ctx):
        return [ManageRulesAction(), CheckNowAction()]

    # ---------------- 生命周期钩子 ----------------
    def on_enable(self, ctx):
        """插件启用（首次加载 / 从停用恢复）→ 启动轮询调度。

        注意：rescan 会重复触发本钩子，``ensure_scheduler`` 幂等，
        不会并存多个 QTimer。
        """
        sched = ensure_scheduler(ctx)
        sched.start()
        sched.check_now()          # 启动即补跑：补上程序没运行期间错过的最近一次

    def on_disable(self, ctx):
        """插件停用 / 总闸关闭 → 停掉定时器（不落盘、不生成）"""
        sched = _STATE.get("scheduler")
        if sched is not None:
            sched.stop()

    def create_page(self, ctx):
        """页面插件入口（manifest "page" 声明触发）"""
        page = CycleTasksPage(ctx)
        _STATE["page"] = page
        ensure_scheduler(ctx).start()
        return page
