# -*- coding: utf-8 -*-
"""
====================================================================
统一相对时间格式化  -  time_format
====================================================================
把 "YYYY-MM-DD HH:MM" 时间戳转成展示文案，笔记面板（notes_panel）与
kb-search 插件结果页共用 —— 此前面板侧只有 ≥7 天原样返回 16 字符
全格式一档，实测 80% 的碎片落在这档，搜索结果 meta 行被顶得很长；
插件侧如果再抄一份必然漂移，所以收敛到这一个纯逻辑模块。

七段口径：
  刚刚 → N 分钟前 → N 小时前 → 昨天 HH:MM → N 天前
  → 同年 MM-DD HH:MM → 跨年原样（16 字符全格式）

设计要点：
  1. 纯 Python 标准库，禁止 import PyQt6（与 nav_layout / md_export
     同一约定，pytest 在无 GUI 环境直接跑）
  2. 对任意输入安全：None / 空串 / 坏格式 / 未来时间都不抛异常
  3. ``now`` 参数仅供测试注入，生产调用不传
====================================================================
"""

from datetime import datetime

TS_FORMAT = "%Y-%m-%d %H:%M"


def format_relative_time(ts, now=None) -> str:
    """时间戳 → 展示文案；解析失败原样返回（与 notes_panel 原实现同约定）"""
    if not ts:
        return "未知时间"
    try:
        dt = datetime.strptime(str(ts).strip(), TS_FORMAT)
    except ValueError:
        return ts
    ref = now if isinstance(now, datetime) else datetime.now()
    secs = (ref - dt).total_seconds()
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
    if dt.year == ref.year:
        return dt.strftime("%m-%d %H:%M")
    return ts
