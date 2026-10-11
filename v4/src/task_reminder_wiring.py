# -*- coding: utf-8 -*-
"""
====================================================================
任务到期提醒编排  -  task_reminder_wiring
====================================================================
自 knowledge_ball.py main() 原段外迁（2026-10-08 拆分轮）：启动时 +
每日 9:00 的三桶提醒扫描、自绘提醒卡片唤起、跳转定位锚点与「打开
任务页」接线。宿主依赖（config/task/ball/main_window）全部参数注入，
本模块禁 import knowledge_ball（装配层单向依赖，wiring 同规）。

行为与原内联实现逐字一致（2026-10-08 提醒闭环已含）：
  · _check_task_reminders：ball.refresh_badge() 先行（徽标与提醒三桶
    脱钩但计数同步）；开关关闭静默；三桶口径由 bucket_unfinished
    纯函数固化；弹出后记录首条逾期（无逾期则今日到期）标题作定位锚点
  · _schedule_daily_reminder：每日 9:00 自循环重排
  · open_requested → show 主窗 + 切任务页 + focus_task_by_title 定位
    （失败静默：任务可能已被删 / 改名，此时只切页不打扰）
====================================================================
"""

from PyQt6.QtCore import QTimer

from src import task_reminder_popup
from src.constants import DEFAULT_THEME
from src.logger import get_logger
from src.task_manager import bucket_unfinished
from src.theme import resolve_theme_name


def wire_task_reminders(*, config_manager, task_manager, ball, main_window):
    """接线任务提醒：启动 4s 后首次检查 + 每日 9:00 定时（自循环重排）。"""

    def _msecs_until_next(hour: int) -> int:
        """距下一个指定整点的毫秒数（用于每日定时）"""
        from datetime import datetime, timedelta
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return int((target - now).total_seconds() * 1000)

    # 跳转定位锚点（dict 避免闭包 nonlocal；见 _check_task_reminders）
    _reminder_state = {"focus_title": ""}

    def _check_task_reminders():
        """扫描未完成任务并弹自绘提醒卡片（三桶口径）。

        ★2026-09-30 修：此前只提醒「今日到期 / 已逾期」两项，导致
        **无截止日的未完成任务永远不会被提醒**（用户反馈"未完成任务不提示"，
        实测其 5 条未完成任务 deadline 全为空串 → 命中 STATE_NONE → 静默）。

        ★2026-10-02 换呈现层：原生托盘气泡（系统 toast，样式不可控）→
        自绘弹窗 task_reminder_popup（用户点名重设计）；触发时机、
        task_reminder_enabled 开关与三桶口径全部不变，分桶仍由纯函数
        bucket_unfinished 固化；球体徽标（refresh_badge）★2026-10-07
        改为「全部未完成任务数」，与提醒三桶口径脱钩。

        三桶（顺序即弹窗内展示顺序）：
          · 已逾期   —— STATE_OVERDUE
          · 今日到期 —— STATE_TODAY
          · 未安排日期 —— STATE_NONE 且未完成（无日期 / 日期写坏）

        任务状态仍统一走 task_state（脏日期解析失败 → 无日期，不标红、
        不进逾期桶）。
        """
        ball.refresh_badge()   # 顺带刷新球体徽标（未完成数变了即同步）
        if not config_manager.get("task_reminder_enabled", True):
            return
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        overdue, due, undated = bucket_unfinished(
            task_manager.get_all_tasks(), today)
        total = len(overdue) + len(due) + len(undated)
        if not total:
            return
        task_reminder_popup.show_reminder(
            overdue=[t.title for t in overdue],
            due=[t.title for t in due],
            undated=[t.title for t in undated],
            theme=resolve_theme_name(
                config_manager.get("theme", DEFAULT_THEME)),
        )
        # 跳转定位锚点：记住首条逾期任务标题（无逾期则首条今日到期），
        # 供「打开任务页」滚动定位（2026-10-08 提醒闭环）
        _reminder_state["focus_title"] = (
            overdue[0].title if overdue else
            due[0].title if due else "")
        get_logger().info(
            f"任务提醒已弹出：逾期 {len(overdue)}，今日到期 {len(due)}，"
            f"未安排日期 {len(undated)}")

    def _schedule_daily_reminder():
        """每日 9:00 检查一次（自循环重排）"""
        _check_task_reminders()
        QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)

    # 提醒卡片「打开任务页」/ 点卡片空白处 → 显示主窗口并切到任务页，
    # 并滚动定位到首条逾期任务（2026-10-08 提醒闭环；定位失败静默，
    # 任务可能已被删 / 改名，此时只切页不打扰）
    def _open_task_page_from_reminder():
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        main_window.show_page(1)
        focus_title = _reminder_state.get("focus_title", "")
        if focus_title:
            tasks_panel = getattr(main_window, "_page_tasks", None)
            located = False
            if tasks_panel is not None and hasattr(
                    tasks_panel, "focus_task_by_title"):
                located = tasks_panel.focus_task_by_title(focus_title)
            get_logger().info(
                "[提醒] 跳转任务页定位%s：%s"
                % ("成功" if located else "失败（未找到该任务）", focus_title))

    task_reminder_popup.popup_instance().open_requested.connect(
        _open_task_page_from_reminder)
    QTimer.singleShot(4000, _check_task_reminders)          # 启动 4 秒后首次检查
    QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)  # 之后每天 9:00
