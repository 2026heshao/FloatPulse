# -*- coding: utf-8 -*-
"""
====================================================================
日期选择器 · 纯逻辑  -  date_grid
====================================================================
**纯逻辑模块：本文件零 PyQt6 依赖**（与 ``nav_layout`` / ``icons`` /
``md_export`` / ``app_version`` 同款约束），因此 pytest 可以直接断言
「某年某月的 28 号落在第 6 行第 1 格」，不需要离屏环境、不需要
QApplication。

为什么要把这一层单独切出来
==========================
自绘日历弹层里真正会出错、而且出错后**很难靠肉眼发现**的只有两件事：

  1. **月矩阵** —— 某月 1 号该落在第几列、42 格里哪些是上/下月补位。
     算错一格的表现是「整个日历错位一天」，而截图里 28 号在第一格还是
     第二格，人眼扫过去不一定反应得过来；
  2. **弹层定位** —— 贴输入框右缘落下、下方放不下就翻到上方、横向越界
     要贴边。算错的表现是「弹层跑到屏幕外」，而离屏测试里的屏幕尺寸是
     虚拟的，更难复现。

两者都是**纯函数**：输入定则输出唯一。切成独立模块后 widget 层只剩
「把矩阵画出来 + 收鼠标事件」，正确性由 pytest 一处钉住。

口径（对齐 Chromium 原生 date picker，即本次要复刻的截图）
=========================================================
  · 一周从**周一**起（zh-CN 惯例），表头 一 二 三 四 五 六 日；
  · 固定 6 行 × 7 列 = 42 格，**含上/下月补位日**。Qt 原生
    ``QCalendarWidget`` 不显示补位日（补位处是空白），这是本次自绘
    而非改 QSS 的直接原因之一；
  · 行数恒定 6 行：不随月份长短伸缩，弹层高度才不会逐月跳动。
====================================================================
"""

from calendar import monthrange
from datetime import date as _date, timedelta

# 一周 7 列、固定 6 行 = 42 格（含上下月补位）
COLS = 7
ROWS = 6
DAYS_IN_GRID = COLS * ROWS

# 周一为一周之始（与 ``date.weekday()`` 的 0=周一 同口径，免换算）
WEEK_START = 0

# 表头文案（周一 → 周日），与 Chromium zh-CN 一致
WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

# 月份选择面板里的 12 个月（与月标题「2026年10月」的写法保持一致）
MONTH_LABELS = ("1月", "2月", "3月", "4月", "5月", "6月",
                "7月", "8月", "9月", "10月", "11月", "12月")


# ====================================================================
# 校验与月份算术
# ====================================================================
def _check_month(year, month):
    """把 ``(year, month)`` 收敛成两个 int，非法值直接抛 ValueError。

    刻意不「容错回退当前月」：日历算错一整格是比崩溃更难查的缺陷，
    非法输入必须在这里就炸出来，而不是在界面上悄悄画错。
    """
    try:
        y, m = int(year), int(month)
    except (TypeError, ValueError):
        raise ValueError("年月必须是整数：%r / %r" % (year, month))
    if not 1 <= m <= 12:
        raise ValueError("月份必须在 1~12：%r" % (month,))
    return y, m


def add_months(year: int, month: int, delta: int):
    """按月平移 ``delta``（可正可负），返回 ``(year, month)``。

    跨年自动进位/退位；月号恒在 1~12（走 divmod 的 floor 语义，
    负数月份也不会溢出成 0 或 -3）。
    """
    y, m = _check_month(year, month)
    total = y * 12 + (m - 1) + int(delta)
    years, month_index = divmod(total, 12)
    return years, month_index + 1


def days_in_month(year: int, month: int) -> int:
    """该月天数（闰年 2 月由 ``calendar.monthrange`` 兜住）。"""
    y, m = _check_month(year, month)
    return monthrange(y, m)[1]


def month_title(year: int, month: int) -> str:
    """弹层月标题文案：``2026年10月``（不补零，与截图一致）。"""
    y, m = _check_month(year, month)
    return "%d年%d月" % (y, m)


# ====================================================================
# 月矩阵
# ====================================================================
def month_cells(year: int, month: int):
    """产出本期弹层要画的 42 格：``((y, m, d, in_month), ...)``，行优先。

    行优先 = 先第一行（周一~周日）从左到右，再第二行 —— 与
    ``QGridLayout`` / 自绘时的 ``i // 7``、``i % 7`` 完全同序，
    widget 层可以直接按下标摆位，不需要二次换算。
    """
    y, m = _check_month(year, month)
    first = _date(y, m, 1)
    start = first - timedelta(days=first.weekday() - WEEK_START)
    cells = []
    for i in range(DAYS_IN_GRID):
        d = start + timedelta(days=i)
        cells.append((d.year, d.month, d.day, (d.year, d.month) == (y, m)))
    return tuple(cells)


def is_same_day(a, b) -> bool:
    """两个 ``(y, m, d)`` 三元组是否为同一天（``None`` 一律不等）。

    弹层里要拿「今天」和「选中日」跟每一格比对，比对次数是 42×2；
    写成三元组比较是为了让 widget 层不必构造 42 个 ``QDate``。
    """
    if a is None or b is None:
        return False
    return tuple(a)[:3] == tuple(b)[:3]


# ====================================================================
# 弹层定位
# ====================================================================
def place_popup(anchor, popup, screen, gap: int = 6, margin: int = 8):
    """算出弹层左上角坐标 ``(x, y)``（全局坐标，整数）。

    参数一律用 ``(x, y, w, h)`` 四元组，**不依赖 QRect** —— 保持本模块
    零 Qt 依赖，同时让「下方放得下吗 / 横向越界吗」这两条判断可被单测
    穷举。

    规则（对齐 Chromium）：
      1. 横向与输入框**右缘对齐**（弹层比输入框宽，多出来的部分向左侧
         展开），再夹到屏幕 ``margin`` 内；
      2. 纵向默认落在输入框**下方** ``gap``；放不下则翻到上方
         ``gap``；上方也放不下时退回屏幕顶部 ``margin``（宁可压住输入
         框，也不把弹层推出可视区）。
    """
    ax, ay, aw, ah = (int(v) for v in anchor)
    pw, ph = int(popup[0]), int(popup[1])
    sx, sy, sw, sh = (int(v) for v in screen)

    x = ax + aw - pw
    x = max(sx + margin, min(x, sx + sw - margin - pw))
    if x < sx + margin:                       # 弹层比屏幕还宽时的兜底
        x = sx + margin

    y = ay + ah + gap
    if y + ph > sy + sh - margin:             # 下方放不下 → 翻到上方
        y = ay - gap - ph
    if y < sy + margin:                       # 上方也放不下 → 贴顶
        y = sy + margin
    return x, y
