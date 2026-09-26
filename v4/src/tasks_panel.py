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
    动画时长 = CHECK_ANIM_MS / anim_speed
  - A3 误勾撤销条（UndoBar，5 秒）
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
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QDialog,
    QFormLayout, QDialogButtonBox, QMessageBox, QDateEdit,
)
from PyQt6.QtCore import Qt, QDate, QTimer, QVariantAnimation, QEasingCurve

from src.glass_dialog import make_dialog_buttons
from src.task_manager import (
    task_state, format_relative_deadline, group_title,
    KIND_ROW, KIND_HEADER,
)
from src.task_delegate import (
    TaskItemDelegate, KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE, ROLE_DONE,
)
from src.controls import UndoBar
from src.constants import CHECK_ANIM_MS
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
        # 最近一次操作（供撤销还原）：(task_id, prev_done)
        self._undo_target = None
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
        title = QLabel("📋 日程任务")
        title.setObjectName("pageTitle")
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

        # B1：截止日期改为日历选择器（非法/留空自动回退今天）
        self._task_deadline = QDateEdit()
        self._task_deadline.setCalendarPopup(True)
        self._task_deadline.setDisplayFormat("yyyy-MM-dd")
        self._task_deadline.setDate(QDate.currentDate())
        self._task_deadline.setFixedWidth(150)
        self._task_deadline.setToolTip("点击选择截止日期（默认今天）")
        input_bar.addWidget(self._task_deadline)

        # 用全角「＋」而非 emoji「➕」：emoji 走彩色字形，不跟随 QSS 的文字色
        add_btn = QPushButton("＋ 添加")
        add_btn.clicked.connect(self._on_add)
        input_bar.addWidget(add_btn)
        v.addLayout(input_bar)

        # ---- 任务列表（多选 + 自定义行委托） ----
        self._task_list = QListWidget()
        self._task_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_list.customContextMenuRequested.connect(self._on_context_menu)

        # 自定义行渲染委托：勾选框 / 标题 / 相对时间 / 状态色 全部自绘
        self._delegate = TaskItemDelegate(
            get_colors(self._host.current_theme), self._task_list)
        self._delegate.toggle_requested.connect(self._on_toggle_requested)
        self._task_list.setItemDelegate(self._delegate)
        v.addWidget(self._task_list, 1)

        # ---- 底部批量操作 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        toggle_btn = QPushButton("✓ 批量完成")
        toggle_btn.setObjectName("secondaryBtn")
        toggle_btn.clicked.connect(self._on_batch_toggle)
        bottom.addWidget(toggle_btn)

        del_btn = QPushButton("🗑 批量删除")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._on_batch_delete)
        bottom.addWidget(del_btn)

        bottom.addStretch()

        clear_done_btn = QPushButton("清除已完成")
        clear_done_btn.setObjectName("secondaryBtn")
        clear_done_btn.clicked.connect(self._on_clear_done)
        bottom.addWidget(clear_done_btn)

        v.addLayout(bottom)

        # ---- 误勾撤销条（浮动子控件，底部居中） ----
        self._undo_bar = UndoBar(self)
        self._undo_bar.undo_clicked.connect(self._on_undo)

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
        """全量重建任务列表：按分组顺序渲染组标题 + 任务行。"""
        # 主题可能变化 → 同步 delegate 配色；并清理过期动画进度
        self._delegate.set_colors(get_colors(self._host.current_theme))
        self._delegate.clear_progress_except(self._anim_task_id)

        today = _date.today().isoformat()
        self._task_list.clear()

        grouped = self._task_manager.get_tasks_grouped(today)
        for group_key, tasks in grouped:
            header_item = QListWidgetItem(
                f"{group_title(group_key, today)}  ·  {len(tasks)}")
            # 组标题：不可选中（多选/批量不会误伤）
            header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header_item.setData(KIND_ROLE, KIND_HEADER)
            self._task_list.addItem(header_item)

            for t in tasks:
                state, _delta = task_state(t.deadline, today)
                item = QListWidgetItem("")
                item.setData(Qt.ItemDataRole.UserRole, t.task_id)
                item.setData(KIND_ROLE, KIND_ROW)
                item.setData(ROLE_TITLE, t.title)
                item.setData(ROLE_REL,
                              format_relative_deadline(t.deadline, today))
                item.setData(ROLE_STATE, state)
                item.setData(ROLE_DONE, bool(t.done))
                self._task_list.addItem(item)

        total = len(self._task_manager.get_all_tasks())
        self._task_count_label.setText(f"共 {total} 条")

    def apply_theme(self):
        """主题切换时更新行配色（由 MainWindow 调用）。"""
        self._delegate.set_colors(get_colors(self._host.current_theme))
        self._task_list.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._undo_bar.update_position()

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

        self._undo_target = (task_id, prev_done)

        # 启动勾选动画（完成：0→1；取消完成：1→0）
        self._anim.stop()
        self._rebuild_timer.stop()
        self._anim_task_id = task_id
        start = 0.0 if new_done else 1.0
        end = 1.0 if new_done else 0.0
        self._delegate.set_check_progress(task_id, start)
        duration = max(1, int(CHECK_ANIM_MS / max(0.01, self._anim_speed)))
        self._anim.setDuration(duration)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._task_list.viewport().update()
        self._anim.start()

        # 撤销条立即显示（不必等移组）
        self._undo_bar.show_for(task_id, task.title)

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

    def _on_undo(self, task_id: int):
        """撤销最近一次完成操作（还原为操作前的完成态）。"""
        prev_done = False
        if self._undo_target and self._undo_target[0] == task_id:
            prev_done = bool(self._undo_target[1])
        self._task_manager.set_done(task_id, prev_done)
        self._undo_target = None

        # 停止动画、清理进度并立即重建
        self._anim.stop()
        self._rebuild_timer.stop()
        self._anim_task_id = None
        self._delegate.clear_progress_except(None)
        self.refresh()
        self._host.data_changed.emit("task")

    # ==================================================================
    # 添加 / 编辑 / 右键 / 批量
    # ==================================================================
    def _on_add(self):
        """添加任务"""
        title = self._task_title_input.text().strip()
        if not title:
            return
        deadline = self._task_deadline.date().toString("yyyy-MM-dd")
        self._task_manager.add_task(title, "", deadline)
        self._task_title_input.clear()
        self.refresh()
        self._host.data_changed.emit("task")

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
        act_edit = menu.addAction("✏️ 编辑...")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除")
        action = menu.exec(self._task_list.mapToGlobal(pos))

        if action == act_toggle:
            self._stop_animations()
            self._task_manager.set_done(task_id, not task.done)
            self.refresh()
            self._host.data_changed.emit("task")
        elif action == act_edit:
            self._edit_dialog(task)
        elif action == act_delete:
            self._stop_animations()
            self._task_manager.delete_task(task_id)
            self.refresh()
            self._host.data_changed.emit("task")

    def _stop_animations(self):
        """停止正在播放的动画并清空进度（用于非动画路径的数据变更）。"""
        self._anim.stop()
        self._rebuild_timer.stop()
        self._anim_task_id = None
        self._undo_target = None
        self._undo_bar.hide()
        self._delegate.clear_progress_except(None)

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
            QMessageBox.information(self, "提示", "请先选择要操作的任务。")
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
            QMessageBox.information(self, "提示", "请先选择要删除的任务。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确认删除选中的 {len(ids)} 条任务？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._stop_animations()
            for tid in ids:
                self._task_manager.delete_task(tid)
            self.refresh()
            self._host.data_changed.emit("task")

    def _on_clear_done(self):
        """清除所有已完成的任务"""
        done_ids = [t.task_id for t in self._task_manager.get_all_tasks() if t.done]
        if not done_ids:
            QMessageBox.information(self, "提示", "没有已完成的任务。")
            return
        ret = QMessageBox.question(
            self, "确认清除",
            f"确认清除 {len(done_ids)} 条已完成的任务？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._stop_animations()
            for tid in done_ids:
                self._task_manager.delete_task(tid)
            self.refresh()
            self._host.data_changed.emit("task")
