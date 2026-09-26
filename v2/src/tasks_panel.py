# -*- coding: utf-8 -*-
"""
====================================================================
日程任务面板  -  TasksPanel
====================================================================
从 main_window.py 抽出的独立面板，承载任务输入 / 列表 / 批量勾选 /
右键编辑等业务逻辑。通过 host（MainWindow）访问任务管理器与样式。
"""

from datetime import date as _date

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QDialog,
    QFormLayout, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class TasksPanel(QWidget):
    """日程任务面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._task_manager = host._task_manager
        self._build_ui()

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

        self._task_deadline = QLineEdit()
        self._task_deadline.setPlaceholderText("截止日期 YYYY-MM-DD")
        self._task_deadline.setFixedWidth(160)
        self._task_deadline.setText(_date.today().isoformat())
        input_bar.addWidget(self._task_deadline)

        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(self._on_add)
        input_bar.addWidget(add_btn)
        v.addLayout(input_bar)

        # ---- 任务列表（多选） ----
        self._task_list = QListWidget()
        self._task_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_list.customContextMenuRequested.connect(self._on_context_menu)
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

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新任务列表显示：未完成任务按 deadline 颜色高亮"""
        today_str = _date.today().isoformat()
        theme = self._host.current_theme

        self._task_list.clear()
        for t in self._task_manager.get_all_tasks():
            status = "✓" if t.done else "☐"
            text = f"{status}  {t.title}"
            if t.deadline:
                text += f"   ｜ 截止: {t.deadline}"
            if t.note:
                text += f"   ｜ 备注: {t.note}"
            text += f"   ｜ 创建: {t.created_at}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, t.task_id)

            if t.done:
                item.setForeground(QColor("#9AA5B1"))
            elif t.deadline:
                if t.deadline < today_str:
                    item.setForeground(
                        QColor("#E74C3C") if theme == "light" else QColor("#FF6B5B")
                    )
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                elif t.deadline == today_str:
                    item.setForeground(
                        QColor("#E67E22") if theme == "light" else QColor("#F39C12")
                    )
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
            self._task_list.addItem(item)

        total = len(self._task_manager.get_all_tasks())
        self._task_count_label.setText(f"共 {total} 条")

    def _on_add(self):
        """添加任务"""
        title = self._task_title_input.text().strip()
        if not title:
            return
        deadline = self._task_deadline.text().strip()
        self._task_manager.add_task(title, "", deadline)
        self._task_title_input.clear()
        self.refresh()
        self._host.data_changed.emit("task")

    def _on_context_menu(self, pos):
        item = self._task_list.itemAt(pos)
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
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
            self._task_manager.toggle_task(task_id)
            self.refresh()
            self._host.data_changed.emit("task")
        elif action == act_edit:
            self._edit_dialog(task)
        elif action == act_delete:
            self._task_manager.delete_task(task_id)
            self.refresh()
            self._host.data_changed.emit("task")

    def _edit_dialog(self, task):
        """编辑任务对话框"""
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
        deadline_edit = QLineEdit(task.deadline)
        deadline_edit.setPlaceholderText("YYYY-MM-DD")

        form.addRow("标题:", title_edit)
        form.addRow("备注:", note_edit)
        form.addRow("截止:", deadline_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        form.addRow(btns)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._task_manager.update_task(
                task.task_id,
                title_edit.text().strip(),
                note_edit.text().strip(),
                deadline_edit.text().strip(),
            )
            self.refresh()
            self._host.data_changed.emit("task")

    def _get_selected_ids(self) -> list:
        """获取选中的任务 id 列表"""
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self._task_list.selectedItems()]

    def _on_batch_toggle(self):
        """批量切换任务完成状态"""
        ids = self._get_selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要操作的任务。")
            return
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
            for tid in done_ids:
                self._task_manager.delete_task(tid)
            self.refresh()
            self._host.data_changed.emit("task")
