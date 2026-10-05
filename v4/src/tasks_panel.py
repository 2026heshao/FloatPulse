# -*- coding: utf-8 -*-
"""
====================================================================
日程任务面板  -  TasksPanel
====================================================================
从 main_window.py 抽出的独立面板，承载任务输入 / 列表 / 批量勾选 /
右键编辑等业务逻辑。通过 host（MainWindow）访问任务管理器与样式。

本轮（日程任务体感与功能优化）在本面板落地的能力：
  - B1 截止日期改为日历选择器（QDateEdit）
  - A1/A2 行内勾选框 + 完成反馈动画（自定义 delegate 自绘），
    动画时长 = motion.duration(CHECK_ANIM_MS, anim_speed)
  - B3 相对时间提示（今天/明天/逾期N天/M月D日（周X））
  - C1 分组排序（get_tasks_grouped：逾期→今天→本周→以后→无日期→已完成）
  - C3 状态判定统一（task_state，脏日期视为无日期）

勾选后的顺序（用户拍板）：先在**原位**播完对勾+删除线动画，
动画结束后再延时约 250ms 才重建列表，让用户看清操作结果；
撤销条在点下勾选的**当下立即**显示，不等移组。
====================================================================
"""

from datetime import date as _date

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QDialog,
    QFormLayout, QDateEdit,
)
from PyQt6.QtCore import Qt, QDate, QTimer, QVariantAnimation, QEasingCurve

from src.glass_dialog import make_dialog_buttons
from src.glass_message_box import GlassMessageBox
from src.list_windowing import ListWindowing, attach_scroll_loader
from src.task_manager import (
    task_state, format_relative_deadline, format_completed_date, group_title,
    KIND_ROW, KIND_HEADER, GROUP_OVERDUE, STATE_OVERDUE, STATE_NONE,
)
from src.task_delegate import (
    TaskItemDelegate, KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE, ROLE_DONE,
)
from src.controls import tune_list_scrolling, SmoothButton, EmptyState, IconButton, PageTitle
from src.constants import CHECK_ANIM_MS
from src.date_picker import DateField
from src import motion
from src.theme import get_colors


class TasksPanel(QWidget):
    """日程任务面板"""

    # 勾选动画播完后，延时多少毫秒才重建列表（让用户看清结果）
    REBUILD_DELAY_MS = 250

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._task_manager = host._task_manager
        self._anim_speed = self._resolve_anim_speed()
        # 当前正在播放勾选动画的 task_id（None 表示空闲）
        self._anim_task_id = None
        # 窗口化渲染状态（成熟化 3.6）：行描述符表 + 分块决策状态机
        self._rows = None                  # 行描述符表（refresh 时重建）
        self._row_pos = 0                  # 已建到的描述符下标
        self._windowing = ListWindowing()  # 分块决策（首屏块大小/追加/重置）
        self._building = False             # 重建中标志：屏蔽滚动触发的追加
        self._build_ui()
        self._init_animation()

    # ==================================================================
    # 动画速度（接入全局 anim_speed）
    # ==================================================================
    def _resolve_anim_speed(self) -> float:
        """从 host 读取动画速度档位（0.5-2.0），异常回退 1.0。"""
        try:
            speed = float(getattr(self._host, "anim_speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    def set_anim_speed(self, speed: float):
        """外部（MainWindow.anim_speed_changed）透传动画速度档位。"""
        try:
            self._anim_speed = max(0.5, min(2.0, float(speed)))
        except (TypeError, ValueError):
            self._anim_speed = 1.0

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = PageTitle("tasks", "日程任务", self._host)
        header.addWidget(title)
        header.addStretch()
        self._task_count_label = QLabel("共 0 条")
        self._task_count_label.setObjectName("hintLabel")
        header.addWidget(self._task_count_label)
        v.addLayout(header)

        # ---- 输入区 ----
        input_bar = QHBoxLayout()
        input_bar.setSpacing(8)

        self._task_title_input = QLineEdit()
        self._task_title_input.setPlaceholderText("输入任务标题，回车添加...")
        self._task_title_input.returnPressed.connect(self._on_add)
        input_bar.addWidget(self._task_title_input, 1)

        # B1 → 2026-10-02：截止日期换成**自绘日历弹层**的日期框
        # （原生 QDateEdit 弹层按 Chromium date picker 外观重做，见
        # src/date_picker.py）。新增的两条用户可见行为：
        #   · 页脚「清除」把日期置空 = 该任务落「无日期」分组；
        #   · 页脚「今天」一键回到今天。
        # 只读：日期只能从弹层选，原先「手输非法值再回退今天」的兜底路径
        # 随之消失（DateField 内部就是 QDateEdit，值语义不变）。
        self._task_deadline = DateField(self._host, parent=self)
        self._task_deadline.setFixedWidth(150)
        self._task_deadline.setToolTip("点击选择截止日期（可点「清除」设为无日期）")
        input_bar.addWidget(self._task_deadline)

        # 「添加」是主操作钮；P1 起带自绘 plus 图标（原先弃用 emoji「➕」是
        # 因为彩色字形不跟随 QSS 文字色，自绘后这一点天然成立）。
        # 基础 QPushButton = 主色底 + on_primary 文字，图标同色才不发灰。
        add_btn = IconButton("plus", text="添加", icon_size=14,
                             off_color="on_primary", hover_color="on_primary")
        add_btn.clicked.connect(self._on_add)
        input_bar.addWidget(add_btn)
        v.addLayout(input_bar)

        # ---- 任务列表（多选 + 自定义行委托） ----
        self._task_list = QListWidget()
        tune_list_scrolling(self._task_list)  # 丝滑化清单 L3：像素级滚动 + 统一步长
        self._task_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_list.customContextMenuRequested.connect(self._on_context_menu)

        # 自定义行渲染委托：勾选框 / 标题 / 相对时间 / 状态色 全部自绘
        self._delegate = TaskItemDelegate(
            get_colors(self._host.current_theme), self._task_list)
        self._delegate.toggle_requested.connect(self._on_toggle_requested)
        self._task_list.setItemDelegate(self._delegate)

        # 窗口化加载：滚动接近底部时追加下一块（≤FULL_THRESHOLD 行全量直建，
        # 本回调里的 windowed 检查会让它直接跳过）
        attach_scroll_loader(self._task_list, self._extend_list_rows)
        v.addWidget(self._task_list, 1)

        # 空态引导（A3）：覆盖层叠在列表上（列表本体不动，itemAt/count
        # 断言零影响）；无动作钮 → 鼠标全透明，右键菜单照常
        self._task_empty = EmptyState(
            "tasks", "还没有任务",
            "在上方输入待办回车添加，到期会在悬浮球提醒你")
        self._task_empty.attach_to(self._task_list)

        # ---- 底部批量操作 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        toggle_btn = SmoothButton("批量完成")
        toggle_btn.setObjectName("secondaryBtn")
        toggle_btn.clicked.connect(self._on_batch_toggle)
        bottom.addWidget(toggle_btn)

        del_btn = IconButton("trash", text="批量删除", icon_size=14,
                             object_name="dangerBtn")
        del_btn.clicked.connect(self._on_batch_delete)
        bottom.addWidget(del_btn)

        bottom.addStretch()

        clear_done_btn = SmoothButton("清除已完成")
        clear_done_btn.setObjectName("secondaryBtn")
        clear_done_btn.clicked.connect(self._on_clear_done)
        bottom.addWidget(clear_done_btn)

        v.addLayout(bottom)

    def _init_animation(self):
        """初始化面板级单个勾选动画与延时重建定时器。"""
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)
        self._anim.finished.connect(self._on_anim_finished)

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self.refresh)

    # ==================================================================
    # 刷新（分组渲染）
    # ==================================================================
    def refresh(self):
        """全量重建任务列表：按分组顺序渲染组标题 + 任务行。

        渲染走窗口化（成熟化 3.6）：≤FULL_THRESHOLD(200) 行全量直建与旧
        实现一致；超出则首屏只建 FIRST_CHUNK(120) 行，滚动接近底部续建。
        """
        # 主题可能变化 → 同步 delegate 配色；并清理过期动画进度
        self._delegate.set_colors(get_colors(self._host.current_theme))
        self._delegate.clear_progress_except(self._anim_task_id)

        today = _date.today().isoformat()
        # 展开行描述符（纯 Python，不建 Qt 对象）：
        # ("header", group_key, n) 组标题行 / ("task", task, state, rel) 任务行
        rows = []
        for group_key, tasks in self._task_manager.get_tasks_grouped(today):
            rows.append(("header", group_key, len(tasks)))
            for t in tasks:
                state, _delta = task_state(t.deadline, today)
                rows.append(("task", t, state, self._rel_text(t, today)))

        self._building = True
        self._task_list.clear()
        self._rows = rows
        self._row_pos = 0
        try:
            n = self._windowing.reset(len(rows))
            self._build_list_rows(n, today)
        finally:
            self._building = False

        total = len(self._task_manager.get_all_tasks())
        self._task_count_label.setText(f"共 {total} 条")
        # 空态引导跟随（A3）：列表一件不剩时显示
        self._task_empty.setVisible(self._task_list.count() == 0)
        if self._task_empty.isVisible():
            self._task_empty.setGeometry(self._task_list.rect())
            self._task_empty.raise_()

    # ---- 窗口化渲染：分块建行 / 追加回调 ----
    def _build_list_rows(self, count: int, today: str):
        """把行描述符表里接下来 count 行建成 item 追加进列表"""
        for _ in range(count):
            row = self._rows[self._row_pos]
            self._row_pos += 1
            if row[0] == "header":
                group_key, n = row[1], row[2]
                # 组标题：标签进 DisplayRole；计数与「是否逾期组」走独立
                # 角色，由 delegate 分字体/分色绘制（2026-10-02 高仿真稿：
                # 标签 + 等宽计数，逾期组整行 danger 红）。
                # 组标题本身不可选中（多选/批量不会误伤）
                header_item = QListWidgetItem(group_title(group_key, today))
                header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header_item.setData(KIND_ROLE, KIND_HEADER)
                header_item.setData(ROLE_REL, str(n))
                header_item.setData(
                    ROLE_STATE,
                    STATE_OVERDUE if group_key == GROUP_OVERDUE else STATE_NONE)
                self._task_list.addItem(header_item)
            else:
                _, t, state, rel = row
                item = QListWidgetItem("")
                item.setData(Qt.ItemDataRole.UserRole, t.task_id)
                item.setData(KIND_ROLE, KIND_ROW)
                item.setData(ROLE_TITLE, t.title)
                item.setData(ROLE_REL, rel)
                item.setData(ROLE_STATE, state)
                item.setData(ROLE_DONE, bool(t.done))
                self._task_list.addItem(item)

    def _extend_list_rows(self):
        """滚动接近底部 → 追加下一块（attach_scroll_loader 的回调入口）。

        重建中 / 未窗口化（小列表全量直建）/ 已建满时直接跳过。
        """
        if self._building or self._rows is None:
            return
        if not self._windowing.windowed:
            return
        n = self._windowing.extend()
        if n > 0:
            self._build_list_rows(n, _date.today().isoformat())

    def _rel_text(self, t, today: str) -> str:
        """行尾右侧文案：已完成→完成日期，未完成→相对截止时间；
        均追加番茄累计（番茄×N，0 次不显示）。"""
        if t.done:
            rel = format_completed_date(t.completed_at)
        else:
            rel = format_relative_deadline(t.deadline, today)
        n = getattr(t, "focus_sessions", 0) or 0
        if n > 0:
            tomato = f"番茄×{n}"
            rel = f"{rel} · {tomato}" if rel else tomato
        return rel

    def apply_theme(self):
        """主题切换时更新行配色（由 MainWindow 调用）。"""
        self._delegate.set_colors(get_colors(self._host.current_theme))
        self._task_list.viewport().update()
        # 日期框的自绘图标 + 日历弹层同样不在 QSS 管辖内，必须手动刷
        self._task_deadline.apply_theme()

    # ==================================================================
    # 行内勾选：数据变更 → 动画 → 延时重建 → 双视图 & 角标刷新
    # ==================================================================
    def _on_toggle_requested(self, task_id: int):
        """单击勾选框 / 标题（无修饰键）→ 切换完成态并播放动画。"""
        task = self._task_manager.get_task(task_id)
        if task is None:
            return
        prev_done = bool(task.done)
        new_done = not prev_done
        if not self._task_manager.set_done(task_id, new_done):
            return

        # 若上一个动画被打断（切换了别的任务），先把它的进度定格到终值
        if self._anim_task_id is not None and self._anim_task_id != task_id:
            self._delegate.set_check_progress(
                self._anim_task_id, float(self._anim.endValue()))

        # 启动勾选动画（完成：0→1；取消完成：1→0）
        self._anim.stop()
        self._rebuild_timer.stop()
        self._anim_task_id = task_id
        start = 0.0 if new_done else 1.0
        end = 1.0 if new_done else 0.0
        self._delegate.set_check_progress(task_id, start)
        # 时长口径统一走 src.motion（UI 强化方案 A1），此处只保留「至少 1ms」
        duration = max(1, motion.duration(CHECK_ANIM_MS, self._anim_speed))
        self._anim.setDuration(duration)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._task_list.viewport().update()
        self._anim.start()

        # 数据变更立即广播（角标 / 小卡片 / 托盘口径同步）
        self._host.data_changed.emit("task")

    def _on_anim_tick(self, value):
        if self._anim_task_id is None:
            return
        self._delegate.set_check_progress(self._anim_task_id, float(value))
        self._task_list.viewport().update()

    def _on_anim_finished(self):
        """动画播完 → 延时约 250ms 再重建（不立即移入已完成组）。"""
        self._anim_task_id = None
        self._rebuild_timer.start(self.REBUILD_DELAY_MS)

    # ==================================================================
    # 添加 / 编辑 / 右键 / 批量
    # ==================================================================
    def _on_add(self):
        """添加任务"""
        title = self._task_title_input.text().strip()
        if not title:
            return
        d = self._task_deadline.dateOrNone()
        self._task_manager.add_task(title, "", d.toString("yyyy-MM-dd") if d else "")
        self._task_title_input.clear()
        self.refresh()
        self._host.data_changed.emit("task")
        # 轻提示反馈（2026-10-05）：添加任务此前静默，长标题截断展示
        shown = title if len(title) <= 16 else title[:15] + "…"
        self._host.show_toast(f"已添加任务：{shown}")

    def _on_context_menu(self, pos):
        item = self._task_list.itemAt(pos)
        if item is None:
            return
        # 组标题行跳过（UserRole 不是 int）
        if item.data(KIND_ROLE) != KIND_ROW:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return
        task = self._task_manager.get_task(task_id)
        if not task:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_toggle = menu.addAction("取消完成" if task.done else "标记完成")
        # 番茄钟绑定：右键直接对该任务开始一次专注（悬浮球进度环可见）
        act_focus = menu.addAction("专注此任务")
        # 任务便签：把任务（截止日徽章+备注）钉成桌面常驻浮窗
        act_sticky = menu.addAction("钉为便签")
        act_edit = menu.addAction("编辑...")
        act_export = menu.addAction("导出到 Obsidian")
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        action = menu.exec(self._task_list.mapToGlobal(pos))

        if action == act_toggle:
            self._stop_animations()
            self._task_manager.set_done(task_id, not task.done)
            self.refresh()
            self._host.data_changed.emit("task")
        elif action == act_focus:
            self._host.task_focus_requested.emit(task_id, task.title)
        elif action == act_sticky:
            self._pin_sticky(task_id)
        elif action == act_edit:
            self._edit_dialog(task)
        elif action == act_export:
            # 导出实现统一在宿主（三个面板共用，避免三份逻辑分叉）
            self._host.export_to_obsidian()
        elif action == act_delete:
            self._stop_animations()
            self._task_manager.delete_task(task_id)
            self.refresh()
            self._host.data_changed.emit("task")
            self._host.show_toast("已删除任务")

    def _stop_animations(self):
        """停止正在播放的动画并清空进度（用于非动画路径的数据变更）。"""
        self._anim.stop()
        self._rebuild_timer.stop()
        self._anim_task_id = None
        self._delegate.clear_progress_except(None)

    def _pin_sticky(self, task_id: int):
        """把任务钉成桌面便签（管理器由宿主晚绑定注入；None 给轻提示）"""
        manager = self._host.sticky_manager
        if manager is None:
            GlassMessageBox.information(self, "提示", "便签功能尚未就绪。")
            return
        ok, reason = manager.open_task(task_id)
        if not ok and reason == "limit":
            self._host.show_toast(
                f"便签最多同时钉 {manager.MAX_STICKIES} 个，请先关闭一些")
        elif not ok and reason == "missing":
            self._host.show_toast("任务不存在或已被删除")

    def _edit_dialog(self, task):
        """编辑任务对话框（截止日期用日历选择器）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑任务")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setFixedSize(360, 220)

        form = QFormLayout(dialog)
        form.setContentsMargins(20, 20, 20, 16)
        form.setSpacing(10)

        title_edit = QLineEdit(task.title)
        note_edit = QLineEdit(task.note)
        note_edit.setPlaceholderText("备注（可选）")
        deadline_edit = QDateEdit()
        deadline_edit.setCalendarPopup(True)
        deadline_edit.setDisplayFormat("yyyy-MM-dd")
        d = QDate.fromString(task.deadline, "yyyy-MM-dd")
        deadline_edit.setDate(d if d.isValid() else QDate.currentDate())
        if task.deadline and not d.isValid():
            note_edit.setPlaceholderText(f"原截止（无效）: {task.deadline}")

        form.addRow("标题:", title_edit)
        form.addRow("备注:", note_edit)
        form.addRow("截止:", deadline_edit)

        form.addRow(make_dialog_buttons(dialog))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._task_manager.update_task(
                task.task_id,
                title_edit.text().strip(),
                note_edit.text().strip(),
                deadline_edit.date().toString("yyyy-MM-dd"),
            )
            self.refresh()
            self._host.data_changed.emit("task")

    def _get_selected_ids(self) -> list:
        """获取选中的任务 id 列表（跳过组标题等非任务项）"""
        ids = []
        for item in self._task_list.selectedItems():
            tid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(tid, bool) or not isinstance(tid, int):
                continue
            ids.append(tid)
        return ids

    def _on_batch_toggle(self):
        """批量切换任务完成状态"""
        ids = self._get_selected_ids()
        if not ids:
            GlassMessageBox.information(self, "提示", "请先选择要操作的任务。")
            return
        self._stop_animations()
        for tid in ids:
            self._task_manager.toggle_task(tid)
        self.refresh()
        self._host.data_changed.emit("task")

    def _on_batch_delete(self):
        """批量删除任务"""
        ids = self._get_selected_ids()
        if not ids:
            GlassMessageBox.information(self, "提示", "请先选择要删除的任务。")
            return
        if GlassMessageBox.question(
                self, "确认删除",
                f"确认删除选中的 {len(ids)} 条任务？",
                danger=True):
            self._stop_animations()
            for tid in ids:
                self._task_manager.delete_task(tid)
            self.refresh()
            self._host.data_changed.emit("task")
            self._host.show_toast(f"已删除 {len(ids)} 条任务")

    def _on_clear_done(self):
        """清除所有已完成的任务"""
        done_ids = [t.task_id for t in self._task_manager.get_all_tasks() if t.done]
        if not done_ids:
            GlassMessageBox.information(self, "提示", "没有已完成的任务。")
            return
        if GlassMessageBox.question(
                self, "确认清除",
                f"确认清除 {len(done_ids)} 条已完成的任务？",
                danger=True):
            self._stop_animations()
            for tid in done_ids:
                self._task_manager.delete_task(tid)
            self.refresh()
            self._host.data_changed.emit("task")
            self._host.show_toast(f"已清除 {len(done_ids)} 条已完成任务")
