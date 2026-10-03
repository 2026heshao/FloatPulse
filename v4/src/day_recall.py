# -*- coding: utf-8 -*-
"""
====================================================================
按天回溯 · 纯逻辑  -  day_recall
====================================================================
**纯逻辑模块：本文件零 PyQt6 依赖**（与 ``time_format`` / ``nav_layout`` /
``date_grid`` / ``md_export`` 同款约束），因此 pytest 可以直接断言
「跨年跨月的时序合并」「空白天不进活跃天索引」这类规则，不需要离屏
环境、不需要 QApplication。

这条能力回答的问题是「**那天我在干什么**」—— 与按词检索（kb-search
回答「含某词的东西在哪」）正交：一个按时、一个按词。

四份数据源按「天」对齐
======================
项目里的时间分散在四种记录里，且**格式各不相同**：

  · 碎片 ``Fragment.created_at``     "YYYY-MM-DD HH:MM"
  · 任务 ``Task.created_at``         "YYYY-MM-DD HH:MM"
  · 任务 ``Task.completed_at``       "YYYY-MM-DD HH:MM"（未完成是空串）
  · 素材 ``AssetInfo.added_time``    "YYYY-MM-DD HH:MM:SS"（多一段秒）
  · 番茄钟 ``PomodoroSession``       **仅存日期** "YYYY-MM-DD"

前四种只差「有没有秒」，本模块用**前 16 字符切片**统一到分钟口径，
不做严格 strptime 校验 —— 与 ``Fragment.preview`` 等既有一致：脏数据
（手改过的 json）不应炸掉整个回溯视图。番茄钟只有日期，**日期级**即可
满足「那天专注了几次」，故不虚构时分。

为什么不做成第二份索引文件
==========================
护栏要求：不新建索引。本模块是**无状态的纯函数集合**，每天现场从
传入的记录列表分组，不落盘、不缓存 —— 数据源自身的 ``created_at`` /
``added_time`` 就是唯一真相。

边界口径（全部有单测钉住）
==========================
  · 解析失败 / 空值 → 该记录**不进任何一天**（不猜、不兜底到「今天」）
  · 只列 **活跃天**（当天至少有一条记录），空闲的日子不占屏
  · 同一天内按 ``(时间戳, 源序, id)`` 稳定排序，时间相同的条目有确定先后
  · 跨年 / 跨月只影响展示文案（``day_label`` 补年份），不影响分组键

模块导出：
  - SOURCE_*          : 四类记录来源常量
  - DayEvent          : 单条回溯条目
  - DayGroup          : 单个活跃天的聚合
  - day_key           : "YYYY-MM-DD HH:MM[:SS]" → "YYYY-MM-DD"（取不到返回 ""）
  - collect_events    : 四源合并时序
  - group_by_day      : 按活跃天分组
  - build_day_groups  : collect + group 一步到位
  - active_days       : 活跃天倒序列表
  - day_label         : 人性化日期文案（今天 / 昨天 / M 月 D 日 / 跨年补年）
  - format_day_text   : 一天的全部条目 → 可存为笔记的文本
====================================================================
"""

from datetime import date, datetime

# ====================================================================
# 来源常量（DayEvent.source）
# ====================================================================
SOURCE_FRAGMENT = "fragment"       # 碎片（收集 / 快速捕捉）
SOURCE_TASK = "task"               # 任务创建
SOURCE_TASK_DONE = "task_done"     # 任务完成
SOURCE_ASSET = "asset"             # 素材（拖入 / 截屏 / 剪贴板图片）
SOURCE_POMODORO = "pomodoro"       # 番茄钟专注记录

# 来源展示名（UI 与导出文案共用，避免两处各写一份中文）
SOURCE_LABELS = {
    SOURCE_FRAGMENT: "碎片",
    SOURCE_TASK: "任务",
    SOURCE_TASK_DONE: "完成",
    SOURCE_ASSET: "素材",
    SOURCE_POMODORO: "专注",
}

# 时间戳 → 日期所需的字符数（"YYYY-MM-DD"）
DATE_LEN = 10
# 时间戳 → 分钟所需的字符数（"YYYY-MM-DD HH:MM"）
MINUTE_LEN = 16


def day_key(ts) -> str:
    """把任意时间戳收敛成 ``"YYYY-MM-DD"``；取不到返回空串。

    - 前 10 字符必须是 ``YYYY-MM-DD`` 形状（``date.fromisoformat`` 认），
      否则返回 "" —— 脏数据 / 空值不进任何一天，而不是兜底到「今天」
    - ``"YYYY-MM-DD HH:MM"`` 与 ``"YYYY-MM-DD HH:MM:SS"`` 两种格式都吃
    """
    if ts is None:
        return ""
    text = str(ts).strip()
    if len(text) < DATE_LEN:
        return ""
    head = text[:DATE_LEN]
    try:
        date.fromisoformat(head)
    except ValueError:
        return ""
    return head


def _minute_ts(ts) -> str:
    """取分钟精度时间戳（"YYYY-MM-DD HH:MM"）用于同天排序；不足则回退。

    排序只需**字典序**正确 —— ``"YYYY-MM-DD HH:MM"`` 的字典序与时间序
    一致，无需转成 datetime 再比（少一层解析、也少一处能抛异常的地方）。
    """
    text = str(ts or "").strip()
    return text[:MINUTE_LEN]


def _minute_clock(ts) -> str:
    """取时间戳的 ``"HH:MM"`` 部分用于展示；取不到返回空串。"""
    text = str(ts or "").strip()
    if len(text) < MINUTE_LEN:
        return ""
    return text[11:MINUTE_LEN]


# ====================================================================
# 数据载体
# ====================================================================
class DayEvent:
    """单条按天回溯条目（四源归一后的统一结构）。

    - ``source``  ∈ SOURCE_*
    - ``ts``      原始时间戳（分钟精度用于展示与排序）
    - ``title``   列表主文案（碎片是预览、任务是标题、素材是文件名…）
    - ``detail``  次要文案（可为空）
    - ``ref_id``  回指源记录的 id（碎片 fragment_id / 任务 task_id…），
                  供 UI 双击跳转；无对应记录时为 None
    """

    __slots__ = ("source", "ts", "title", "detail", "ref_id")

    def __init__(self, source, ts, title, detail="", ref_id=None):
        self.source = source
        self.ts = ts
        self.title = title
        self.detail = detail
        self.ref_id = ref_id

    @property
    def time_text(self) -> str:
        """展示用 "HH:MM"（无时分时返回空串）"""
        return _minute_clock(self.ts)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def sort_key(self):
        """同一天内的稳定排序键：时间 → 来源序 → id。

        时间只有分钟精度，同一分钟内的多条必须靠来源序 + id 定先后，
        否则「刷新一次顺序变一次」（dict / 列表顺序漂移）。
        """
        order = _SOURCE_ORDER.get(self.source, 99)
        rid = self.ref_id if isinstance(self.ref_id, int) else -1
        return (_minute_ts(self.ts), order, rid)

    def __repr__(self):   # pragma: no cover - 调试用
        return "DayEvent(%s, %s, %r)" % (self.source, self.ts, self.title)


# 同一天内来源的展示先后（碎片 → 任务 → 完成 → 素材 → 专注）
_SOURCE_ORDER = {
    SOURCE_FRAGMENT: 0,
    SOURCE_TASK: 1,
    SOURCE_TASK_DONE: 2,
    SOURCE_ASSET: 3,
    SOURCE_POMODORO: 4,
}


class DayGroup:
    """单个活跃天的聚合：日期键 + 当天全部条目（按时间正序）。"""

    __slots__ = ("day", "events")

    def __init__(self, day, events):
        self.day = day
        self.events = list(events)

    def __len__(self):
        return len(self.events)

    def count_by_source(self, source) -> int:
        return sum(1 for e in self.events if e.source == source)

    @property
    def fragments(self):
        return [e for e in self.events if e.source == SOURCE_FRAGMENT]

    @property
    def tasks(self):
        return [e for e in self.events
                if e.source in (SOURCE_TASK, SOURCE_TASK_DONE)]

    @property
    def assets(self):
        return [e for e in self.events if e.source == SOURCE_ASSET]

    @property
    def pomodoros(self):
        return [e for e in self.events if e.source == SOURCE_POMODORO]

    @property
    def pomodoro_count(self) -> int:
        """当天专注次数（番茄记录每命中一条算一次）。"""
        return self.count_by_source(SOURCE_POMODORO)

    def summary(self) -> str:
        """一行摘要：「碎片 N · 任务 M · 素材 K · 专注 P」（0 的项省略）。

        四项全 0 时返回「无记录」（正常不会出现——空白天不进索引）。
        """
        parts = []
        for source, label in ((SOURCE_FRAGMENT, "碎片"),
                              (SOURCE_TASK, "任务"),
                              (SOURCE_ASSET, "素材"),
                              (SOURCE_POMODORO, "专注")):
            n = self.count_by_source(source)
            if n:
                parts.append("%s %d" % (label, n))
        # 任务完成单独并进「任务」计数，不重复列 "完成"
        return " · ".join(parts) if parts else "无记录"

    def __repr__(self):   # pragma: no cover - 调试用
        return "DayGroup(%s, %d)" % (self.day, len(self.events))


# ====================================================================
# 四源合并（时序）
# ====================================================================
def _fragment_events(fragments) -> list:
    out = []
    for f in fragments or ():
        ts = getattr(f, "created_at", "") or ""
        if not day_key(ts):
            continue
        preview = ""
        try:
            preview = f.preview(60)
        except Exception:
            preview = str(getattr(f, "content", "") or "")[:60]
        source_desc = str(getattr(f, "source", "") or "")
        detail = str(getattr(f, "type", "") or "")
        if source_desc:
            detail = ("%s · %s" % (detail, source_desc)) if detail else source_desc
        out.append(DayEvent(SOURCE_FRAGMENT, ts, preview or "(空)",
                            detail, getattr(f, "fragment_id", None)))
    return out


def _task_events(tasks) -> list:
    """任务产两类事件：**创建**（created_at）与**完成**（completed_at）。

    未完成任务的 completed_at 是空串 → 自然被 day_key 挡掉，不产生事件。
    """
    out = []
    for t in tasks or ():
        tid = getattr(t, "task_id", None)
        title = str(getattr(t, "title", "") or "").strip()
        created = getattr(t, "created_at", "") or ""
        if day_key(created):
            out.append(DayEvent(SOURCE_TASK, created, title or "(无标题)",
                                "创建", tid))
        completed = getattr(t, "completed_at", "") or ""
        if day_key(completed):
            out.append(DayEvent(SOURCE_TASK_DONE, completed,
                                title or "(无标题)", "完成", tid))
    return out


def _asset_events(assets) -> list:
    out = []
    for a in assets or ():
        ts = getattr(a, "added_time", "") or ""
        if not day_key(ts):
            continue
        name = str(getattr(a, "original_name", "") or "").strip() or "(未命名)"
        out.append(DayEvent(SOURCE_ASSET, ts, name, "素材",
                            getattr(a, "asset_id", None)))
    return out


def _pomodoro_events(sessions) -> list:
    """番茄钟记录 → 事件（每条专注算一次）。

    ``sessions`` 可以是 ``PomodoroSession`` 列表（取 ``created_at``），
    也可以是裸日期字符串列表 —— 后者兼容插件 / 测试直接喂日期。
    """
    out = []
    for s in sessions or ():
        if isinstance(s, str):
            ts, ref = s, None
        else:
            ts = getattr(s, "created_at", "") or ""
            ref = getattr(s, "session_id", None)
        if not day_key(ts):
            continue
        out.append(DayEvent(SOURCE_POMODORO, ts, "专注一次", "专注", ref))
    return out


def collect_events(fragments=None, tasks=None, assets=None,
                   sessions=None) -> list:
    """四源 → 单一事件列表（**不做分组、不排序**，排序在 group_by_day）。

    任何一源传 None / 空都安全（缺哪源就不出哪源的事件）。
    """
    events = []
    events.extend(_fragment_events(fragments))
    events.extend(_task_events(tasks))
    events.extend(_asset_events(assets))
    events.extend(_pomodoro_events(sessions))
    return events


# ====================================================================
# 按活跃天分组
# ====================================================================
def group_by_day(events) -> dict:
    """``[DayEvent]`` → ``{"YYYY-MM-DD": [DayEvent, ...]}``（天内已排序）。

    只保留**有事件的活跃天**；空白天天然不会出现在结果里。
    """
    buckets = {}
    for e in events or ():
        key = day_key(getattr(e, "ts", ""))
        if not key:
            continue
        buckets.setdefault(key, []).append(e)
    for key in buckets:
        buckets[key].sort(key=lambda e: e.sort_key())
    return buckets


def build_day_groups(fragments=None, tasks=None, assets=None,
                     sessions=None, desc=True):
    """一步到位：四源 → ``[DayGroup, ...]``。

    ``desc=True`` 时按日期倒序（最新的一天在最上，回溯视图默认口径）；
    ``desc=False`` 按日期正序。天内的条目恒为时间正序。
    """
    buckets = group_by_day(
        collect_events(fragments, tasks, assets, sessions))
    days = sorted(buckets.keys(), reverse=desc)
    return [DayGroup(d, buckets[d]) for d in days]


def active_days(fragments=None, tasks=None, assets=None, sessions=None,
                desc=True) -> list:
    """活跃天（"YYYY-MM-DD" 字符串）列表，默认倒序。

    **空闲的日子不出现在这里** —— 这正是「按活跃天而非自然天建索引」
    的护栏落点。列表页只渲染这些天，不铺满日历。
    """
    buckets = group_by_day(
        collect_events(fragments, tasks, assets, sessions))
    return sorted(buckets.keys(), reverse=desc)


# ====================================================================
# 展示文案
# ====================================================================
def day_label(day: str, today=None) -> str:
    """活跃天的展示文案：今天 / 昨天 / M 月 D 日（跨年补年份）。

    解析失败原样返回 —— 数据被手改过也不能炸整个视图（与
    ``fragments_panel._day_label`` 同一约定）。
    ``today`` 仅供测试注入（``date`` 或 "YYYY-MM-DD"），生产不传。
    """
    try:
        d = date.fromisoformat(str(day))
    except (ValueError, TypeError):
        return str(day)
    ref = _as_date(today)
    md = "%d 月 %d 日" % (d.month, d.day)
    if ref is not None:
        delta = (ref - d).days
        if delta == 0:
            return "今天 · %s" % md
        if delta == 1:
            return "昨天 · %s" % md
    if ref is None or d.year != ref.year:
        # 跨年（或与参照年不同）→ 补年份，避免「1 月 3 日」指向不明
        if d.year != (ref or date.today()).year:
            return "%d 年 %s" % (d.year, md)
    return md


def _as_date(value):
    """``today`` 参数归一成 ``date``；None / 非法 → None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:DATE_LEN])
    except (ValueError, TypeError):
        return None


def format_day_text(group: DayGroup, today=None) -> str:
    """把一个活跃天的全部条目组装成可**存为笔记**的纯文本。

    输出形如::

        【2026-10-03 · 今天 · 碎片 3 · 任务 1 · 专注 2】
        - 09:12  [碎片] 复制的一段文字…
        - 10:30  [任务] 写周报（创建）
        - 14:05  [专注] 专注一次
        - 16:20  [完成] 写周报（完成）

    复用现有写入通道（``NoteManager.add_note``）—— 本函数只产文本，
    落盘由调用方走既有笔记入口，不新造沉淀层。
    """
    head = group.day
    label = day_label(group.day, today)
    header = "【%s · %s · %s】" % (head, label, group.summary())
    lines = [header]
    for e in group.events:
        t = e.time_text or "--:--"
        suffix = "（%s）" % e.detail if e.detail else ""
        lines.append("- %s  [%s] %s%s"
                     % (t, e.source_label, e.title, suffix))
    return "\n".join(lines)
