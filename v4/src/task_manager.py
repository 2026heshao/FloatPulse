# -*- coding: utf-8 -*-
"""
====================================================================
日程任务管理模块  -  TaskManager
====================================================================
独立封装日程任务的全部业务逻辑，与 UI 层解耦。
与 NoteManager（notes.json）、docx 知识库完全隔离，互不干扰。

设计要点：
  1. 使用与 exe / py 同目录下的 schedule.json 持久化任务数据
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化空任务列表；json 解析异常不崩溃
  4. 每条任务拥有自增且不复用的唯一 task_id 主键
  5. 任务字段：task_id / title / note / deadline / done / created_at / completed_at
  6. 所有增删改先操作内存列表，完毕统一调用 _save() 写盘
  7. 删除严格按 task_id 过滤，禁止标题匹配删除
  8. UI 层只能通过本类公开方法操作，禁止直接读写 schedule.json
  9. 状态判定与分组排序集中在本模块（纯函数、无 Qt 依赖），
     供主窗口任务页 / 小卡片 / 悬浮球角标 / 托盘提醒四处共用，
     保证「同一份数据、同一口径」。

状态口径（C3 统一）：
  - deadline 一律用 ``date.fromisoformat`` 解析；
  - 解析失败（含空串、脏格式）统一视为「无日期」（STATE_NONE）：
    不标红、不计数、不提醒。旧实现中主窗口用字符串比较会把脏日期
    误判为「逾期」、而角标/提醒却跳过——新口径四处彻底对齐。

模块导出：
  - Task                    : 任务数据类
  - TaskManager             : 任务管理器
  - task_state              : 状态判定公共函数
  - format_relative_deadline: 相对时间文案公共函数
  - group_title             : 组标题（含「本周」截止日）公共函数
  - STATE_* / GROUP_* / KIND_* 常量
====================================================================
"""

import os
import json
from datetime import date, datetime, timedelta

from src.json_store import load_records


# ====================================================================
# 状态 / 分组 / item 类型常量（跨模块共享）
# ====================================================================
# ---- 任务状态枚举（task_state 返回值）----
STATE_OVERDUE = "overdue"     # 已逾期（deadline < today）
STATE_TODAY = "today"         # 今天到期
STATE_FUTURE = "future"       # 未来到期
STATE_NONE = "none"           # 无日期 / 日期解析失败

# ---- 分组键（get_tasks_grouped 返回的 group_key）----
GROUP_OVERDUE = "逾期"
GROUP_TODAY = "今天"
GROUP_WEEK = "本周"
GROUP_LATER = "以后"
GROUP_NONE = "无日期"
GROUP_DONE = "已完成"

# 分组展示顺序（固定，与用户拍板一致）
GROUP_ORDER = (
    GROUP_OVERDUE,
    GROUP_TODAY,
    GROUP_WEEK,
    GROUP_LATER,
    GROUP_NONE,
    GROUP_DONE,
)

# ---- QListWidget item 类型（写入 UserRole+1 角色）----
KIND_ROW = 0        # 任务行
KIND_HEADER = 1     # 组标题行（不可选、右键跳过）

# 中文星期（date.weekday(): 周一=0 ... 周日=6）
_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


# ====================================================================
# 公共状态函数（纯逻辑，无 Qt 依赖）
# ====================================================================
def _parse_iso_date(value):
    """把 "YYYY-MM-DD" 解析为 ``date``；空值 / 脏格式一律返回 None。

    - 空串、None、非法格式、非字符串 → None（视为「无日期」）
    - 这是全局唯一的时间解析入口，确保四处口径一致
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def task_state(deadline: str, today: str | None = None):
    """返回 ``(state, days_delta)``。

    - ``state`` ∈ {"overdue", "today", "future", "none"}
    - ``days_delta`` = (deadline 日期 - today).days：
      逾期为负数（如 -3 表示逾期 3 天）、今天 = 0、未来 > 0；
      无日期 / 解析失败 → ``None``。
    - ``today`` 缺省或非法时回退到系统当天。

    解析失败即视为 ``STATE_NONE``（不标红、不计数、不提醒）。
    """
    d = _parse_iso_date(deadline)
    if d is None:
        return (STATE_NONE, None)
    base = _parse_iso_date(today)
    if base is None:
        base = date.today()
    delta = (d - base).days
    if delta < 0:
        return (STATE_OVERDUE, delta)
    if delta == 0:
        return (STATE_TODAY, delta)
    return (STATE_FUTURE, delta)


def format_relative_deadline(deadline: str, today: str | None = None) -> str:
    """UI 相对时间文案：今天 / 明天 / 逾期N天 / M月D日（周X）。

    无日期或解析失败 → 返回空串（调用方不再拼接）。
    """
    d = _parse_iso_date(deadline)
    if d is None:
        return ""
    base = _parse_iso_date(today)
    if base is None:
        base = date.today()
    delta = (d - base).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta < 0:
        return f"逾期{abs(delta)}天"
    return f"{d.month}月{d.day}日（周{_WEEKDAY_CN[d.weekday()]}）"


def current_week_end(today: str | None = None) -> date:
    """本周日（自然周以周日为界，含今天）。

    今天即周日时返回今天本身。
    """
    base = _parse_iso_date(today)
    if base is None:
        base = date.today()
    return base + timedelta(days=(6 - base.weekday()) % 7)


def group_title(group_key: str, today: str | None = None) -> str:
    """组标题文案（不含计数，计数由 UI 追加）。

    - ``GROUP_WEEK`` → 「本周（至 9 月 27 日）」
    - 其余组直接返回组名
    """
    if group_key == GROUP_WEEK:
        end = current_week_end(today)
        return f"本周（至 {end.month} 月 {end.day} 日）"
    return group_key


# ====================================================================
# 任务数据类
# ====================================================================
class Task:
    """单条任务的数据载体"""

    def __init__(self, task_id, title, note, deadline, done, created_at,
                 completed_at=""):
        self.task_id = task_id            # 唯一主键，自增不复用
        self.title = title                # 任务标题
        self.note = note                  # 备注
        self.deadline = deadline          # 截止日期 "YYYY-MM-DD"
        # 预留：deadline_time（"HH:MM" 时分精度）本轮不做（用户决策）；
        # 未来若支持，仅需在此字段旁扩展，分组/状态口径仍按 deadline 日期判定。
        self.done = done                  # 是否完成
        self.created_at = created_at      # 创建时间 "YYYY-MM-DD HH:MM"
        # 完成时间 "YYYY-MM-DD HH:MM"；未完成为空串（存量 JSON 无该键 → 空串）
        self.completed_at = completed_at

    def to_dict(self):
        """序列化为字典（用于写 json）"""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "note": self.note,
            "deadline": self.deadline,
            "done": self.done,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化（用于读 json），带类型校验防止损坏数据崩溃"""
        return cls(
            task_id=int(d.get("task_id", 0) or 0),
            title=str(d.get("title", "")),
            note=str(d.get("note", "")),
            deadline=str(d.get("deadline", "")),
            done=bool(d.get("done", False)),
            created_at=str(d.get("created_at", "")),
            # 存量 JSON 无该键 → 自动补空串，无需迁移脚本
            completed_at=str(d.get("completed_at", "")),
        )


# ====================================================================
# 任务管理器
# ====================================================================
class TaskManager:
    """
    日程任务管理器。

    对外提供增删改查接口，内部维护内存任务列表，
    修改完毕统一调用 _save() 写入磁盘 json 文件。
    UI 层禁止直接读写 schedule.json 文件。
    """

    def __init__(self, json_path: str):
        self._json_path = json_path          # schedule.json 完整路径
        self._tasks = []                     # 内存任务列表
        self._next_id = 1                    # 下一个自增 task_id（不复用）
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """从磁盘加载任务数据（骨架见 json_store.load_records）。

        - 文件不存在 → 初始化空列表
        - json 解析异常或结构损坏 → 备份原文件后初始化空列表，不崩溃
        """
        self._tasks, self._next_id = load_records(
            self._json_path, "tasks", Task.from_dict,
            min_next_id=lambda ts: max((t.task_id for t in ts), default=0) + 1,
        )

    def _save(self):
        """
        统一保存：将内存任务列表一次性写入磁盘 json。
        所有增删改操作完成后调用本方法。
        """
        data = {
            "tasks": [t.to_dict() for t in self._tasks],
            "next_id": self._next_id,
        }
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            # 原子写入：先写临时文件，再替换原文件，避免写一半损坏
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            # 写盘失败不崩溃（可在此处加日志）
            pass

    # ---------------- 增删改查 ----------------
    def add_task(self, title: str, note: str, deadline: str) -> int:
        """
        新建任务。
        返回新任务的 task_id。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        task = Task(
            task_id=self._next_id,
            title=title,
            note=note,
            deadline=deadline,
            done=False,
            created_at=now,
            completed_at="",
        )
        self._tasks.append(task)
        self._next_id += 1
        self._save()
        return task.task_id

    def update_task(self, task_id: int, title: str, note: str, deadline: str) -> bool:
        """
        编辑任务（严格按 task_id 查找）。
        """
        for t in self._tasks:
            if t.task_id == task_id:
                t.title = title
                t.note = note
                t.deadline = deadline
                self._save()
                return True
        return False

    def set_done(self, task_id: int, done: bool) -> bool:
        """幂等设置任务完成状态（严格按 task_id 查找）。

        - ``done=True``  → 写入当前时间到 ``completed_at``
        - ``done=False`` → 清空 ``completed_at``
        - 状态未变化时保持幂等（不刷新 ``completed_at``，但补全缺失的完成时间）
        """
        for t in self._tasks:
            if t.task_id == task_id:
                new_done = bool(done)
                if t.done == new_done:
                    # 幂等：状态未变。仅在「已完成但缺完成时间」时补全一次
                    if new_done and not t.completed_at:
                        t.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                        self._save()
                    return True
                t.done = new_done
                t.completed_at = (datetime.now().strftime("%Y-%m-%d %H:%M")
                                  if new_done else "")
                self._save()
                return True
        return False

    def toggle_task(self, task_id: int) -> bool:
        """切换任务完成状态（兼容包装，内部同步维护 completed_at）"""
        t = self.get_task(task_id)
        if t is None:
            return False
        return self.set_done(task_id, not t.done)

    def delete_task(self, task_id: int) -> bool:
        """
        删除任务。
        严格按 task_id 过滤，禁止使用标题匹配删除。
        """
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.task_id != task_id]
        if len(self._tasks) < before:
            self._save()
            return True
        return False

    def get_all_tasks(self):
        """返回全部任务，按 task_id 升序（创建顺序）"""
        return sorted(self._tasks, key=lambda t: t.task_id)

    def get_task(self, task_id: int):
        """按 task_id 获取单条任务，不存在返回 None"""
        for t in self._tasks:
            if t.task_id == task_id:
                return t
        return None

    # ---------------- 分组排序 ----------------
    def get_tasks_grouped(self, today: str | None = None):
        """按分组返回任务：``[(group_key, [Task, ...]), ...]``。

        - 组顺序：``overdue → today → week → later → none → done``
        - 组内：有日期按 deadline 升序（无日期置后）；无日期组按 created_at 升序
        - 「本周」= 明天 → 本周日（自然周，周日为界）；
          为避免出现"任何组都装不下"的日期空洞，明天（today+1）
          一并归入「本周」（今天/明天均属本周剩余时段）。
        - 只返回非空分组（空组不展示，避免列表出现空标题）
        """
        base = _parse_iso_date(today)
        if base is None:
            base = date.today()
        base_str = base.isoformat()
        week_end_delta = (6 - base.weekday()) % 7   # 今天距本周日的天数

        buckets = {key: [] for key in GROUP_ORDER}
        for t in self.get_all_tasks():
            if t.done:
                buckets[GROUP_DONE].append(t)
                continue
            state, delta = task_state(t.deadline, base_str)
            if state == STATE_OVERDUE:
                buckets[GROUP_OVERDUE].append(t)
            elif state == STATE_TODAY:
                buckets[GROUP_TODAY].append(t)
            elif state == STATE_NONE:
                buckets[GROUP_NONE].append(t)
            else:  # future
                if 1 <= delta <= week_end_delta:
                    buckets[GROUP_WEEK].append(t)
                else:
                    buckets[GROUP_LATER].append(t)

        def _deadline_key(t):
            # 有日期按 deadline 升序；无日期排在最后（用 date.max 占位）
            d = _parse_iso_date(t.deadline)
            return (d is None, d or date.max)

        for key in (GROUP_OVERDUE, GROUP_TODAY, GROUP_WEEK,
                    GROUP_LATER, GROUP_DONE):
            buckets[key].sort(key=_deadline_key)
        # 无日期组：按创建时间升序（创建越早越靠前）
        buckets[GROUP_NONE].sort(key=lambda t: str(t.created_at or ""))

        return [(key, buckets[key]) for key in GROUP_ORDER if buckets[key]]
