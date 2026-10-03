# -*- coding: utf-8 -*-
"""
====================================================================
碎片规则清仓 · 纯逻辑  -  inbox_triage
====================================================================
**纯逻辑模块：本文件零 PyQt6 依赖**（与 ``asset_group`` / ``day_recall`` /
``time_format`` 同款约束），因此 pytest 可以直接断言「三条件逐条边界」
「脏数据容错」「实时派生」这类规则，不需要离屏环境、不需要 QApplication。

背景（为什么需要它）
====================
43 天里碎片 **226 条** vs 笔记 **3 条**，转化率 1.3% —— 碎片池只进不出。
面对一池子陈旧碎片，用户需要的是**一份可复核的「清仓建议清单」**，
而不是一键清空：

  · 清单**只读展示**，本身不含任何删除按钮（删不删由用户逐条决定）；
  · 每条独立二次确认，删除只走既有的 ``FragmentManager.delete_fragment``；
  · **不做批量删除、不调 rotate_backup、不做快照/回收站/软删除**；
  · 清单**每次实时算**（本模块是**无状态纯函数集合**，不落盘、不缓存）——
    缓存与真实数据不一致会建议错，比不算更危险。

判定规则（三个条件 **AND**，缺一即不候选）
==========================================
  1. **≥ N 天**：``created_at`` 距 ``now`` 至少 ``min_days`` 天（默认 30）
  2. **从未被搜索命中**：``hit_count == 0``（持久字段，见 fragment_manager）
  3. **长度短**：``len(content)`` 不超过 ``max_len``（默认 40 字符）

三条同时满足才进建议清单。天数口径与项目既有相对时间一致（``time_format``
同约定）：**按绝对秒数差算**，``"YYYY-MM-DD HH:MM"`` 解析失败一律**不候选**
（不猜、不兜底）——脏数据被建议删掉才是真正的数据事故。

模块导出：
  - DEFAULT_MIN_DAYS   : 默认天数阈值（30）
  - DEFAULT_MAX_LEN    : 默认长度阈值（40）
  - parse_created_at   : "YYYY-MM-DD HH:MM" → datetime（失败返回 None）
  - age_days           : 时间戳距 now 的整天数（解析失败返回 None）
  - is_stale           : 单条是否命中建议规则（三条件 AND）
  - collect_candidates : 批量筛出候选（保持传入顺序）
  - candidate_reason   : 人类可读的「为什么建议清这条」文案
====================================================================
"""

from datetime import datetime

# 默认天数阈值：碎片超过 30 天且从未被搜索命中、内容又短，才建议清
DEFAULT_MIN_DAYS = 30
# 默认长度阈值（字符数）：短碎片（含大量一次性复制的碎片）优先
DEFAULT_MAX_LEN = 40

# 碎片 created_at 的落盘格式（fragment_manager 同源）
TS_FORMAT = "%Y-%m-%d %H:%M"

# 一天的秒数（年龄口径：绝对秒数差 // 86400）
_SECONDS_PER_DAY = 86400


def parse_created_at(ts):
    """解析 ``"YYYY-MM-DD HH:MM"`` → datetime；解析失败 / 空值返回 None。

    失败**不抛异常**：碎片时间可能被手改 / 损坏，清仓规则必须对坏数据
    容错（坏数据一律**不候选**，宁可漏建议也不误删）。
    """
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.strptime(str(ts).strip(), TS_FORMAT)
    except (ValueError, TypeError):
        return None


def _as_datetime(now):
    """``now`` 参数归一成 datetime；None / 非法 → ``datetime.now()``。

    ``now`` 仅供测试注入，生产调用不传。
    """
    if isinstance(now, datetime):
        return now
    return datetime.now()


def age_days(fragment, now=None):
    """碎片创建时间距 now 的整天数（向下取整）；取不到返回 None。

    - 解析失败 / 缺 ``created_at`` → None（调用方视为「不候选」）
    - 未来时间（系统时钟被改）→ 返回**负数**，天然落在 ``>= min_days``
      之外 → 不候选，无需特判
    """
    ts = getattr(fragment, "created_at", "") or ""
    dt = parse_created_at(ts)
    if dt is None:
        return None
    secs = (_as_datetime(now) - dt).total_seconds()
    return int(secs // _SECONDS_PER_DAY)


def _hit_count(fragment) -> int:
    """取碎片 hit_count，脏值 / 缺字段收敛为 0（0 = 从未被搜索命中）。"""
    raw = getattr(fragment, "hit_count", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _content_len(fragment) -> int:
    """取碎片内容长度（None 视作空串）；``content`` 非字符串时安全转换。"""
    content = getattr(fragment, "content", "")
    if content is None:
        return 0
    return len(str(content))


def is_stale(fragment, now=None, min_days=DEFAULT_MIN_DAYS,
             max_len=DEFAULT_MAX_LEN) -> bool:
    """单条是否命中建议清理规则（三条件 **AND**）：

      1. 年龄 ≥ ``min_days`` 天
      2. ``hit_count == 0``（从未被搜索命中）
      3. 内容长度 ≤ ``max_len``

    任一条不满足 → False（不候选）。时间解析失败 → False。
    """
    days = age_days(fragment, now)
    if days is None or days < min_days:
        return False
    if _hit_count(fragment) != 0:
        return False
    return _content_len(fragment) <= max_len


def collect_candidates(fragments, now=None, min_days=DEFAULT_MIN_DAYS,
                       max_len=DEFAULT_MAX_LEN) -> list:
    """批量筛出建议清理的碎片，**保持传入顺序**。

    ``fragments`` 传 None / 空都安全（返回空列表）。**实时计算，不缓存**
    —— 数据变了再调一次结果就跟着变。
    """
    out = []
    for f in fragments or ():
        if is_stale(f, now, min_days, max_len):
            out.append(f)
    return out


def candidate_reason(fragment, now=None, min_days=DEFAULT_MIN_DAYS,
                     max_len=DEFAULT_MAX_LEN) -> str:
    """人类可读的「为什么建议清这条」文案（供 UI 逐条显示）。

    即便调用方未先过 :func:`is_stale`，本函数也只描述**当前实际**的天数 /
    命中次数 / 长度，不做判定之外的美化。时间解析失败时给出明确提示。
    """
    days = age_days(fragment, now)
    if days is None:
        return "创建时间缺失或无法解析，无法判断"

    parts = ["已存放 %d 天" % days]
    hits = _hit_count(fragment)
    parts.append("从未被搜索命中" if hits == 0 else "已被搜索命中 %d 次" % hits)
    parts.append("仅 %d 字" % _content_len(fragment))
    return "、".join(parts)


# ====================================================================
# 阈值合法性（供 UI 侧步进器 / 配置读取兜底，避免越界值建议错）
# ====================================================================
def sanitize_min_days(value, default=DEFAULT_MIN_DAYS) -> int:
    """把天数阈值收敛为正整数；非法 / 越界（<1）回退默认。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


def sanitize_max_len(value, default=DEFAULT_MAX_LEN) -> int:
    """把长度阈值收敛为正整数；非法 / 越界（<1）回退默认。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default
