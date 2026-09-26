# -*- coding: utf-8 -*-
"""
====================================================================
笔记管理面板  -  NotesPanel
====================================================================
从 main_window.py 抽出的独立面板，承载笔记列表 / 编辑区 / 自动保存 /
搜索 / 重命名等业务逻辑。通过 host（MainWindow）访问笔记管理器与样式。
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QTextEdit,
    QSplitter, QDialog, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from src.constants import (
    NOTE_AUTOSAVE_INTERVAL_MS,
    DATETIME_DATE_LEN,
    DATETIME_TIME_START,
    DATETIME_TIME_LEN,
    DATETIME_MIN_LEN,
    NOTE_PREVIEW_LEN,
)


class NotesPanel(QWidget):
    """笔记管理面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._note_manager = host._note_manager
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = QLabel("📝 笔记管理")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._note_count_label = QLabel("共 0 条")
        self._note_count_label.setObjectName("hintLabel")
        header.addWidget(self._note_count_label)
        v.addLayout(header)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        new_btn = QPushButton("＋ 新建笔记")
        new_btn.clicked.connect(self._on_new)
        toolbar.addWidget(new_btn)

        del_btn = QPushButton("🗑 删除当前")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(del_btn)

        toolbar.addStretch()

        self._note_status_label = QLabel("")
        self._note_status_label.setObjectName("hintLabel")
        toolbar.addWidget(self._note_status_label)

        v.addLayout(toolbar)

        # ---- 搜索框 ----
        self._note_search = QLineEdit()
        self._note_search.setPlaceholderText("🔍 搜索标题或内容...")
        self._note_search.textChanged.connect(self.refresh)
        v.addWidget(self._note_search)

        # ---- 左右分栏：列表 + 编辑区 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._note_list = QListWidget()
        self._note_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._note_list.customContextMenuRequested.connect(self._on_context_menu)
        self._note_list.currentItemChanged.connect(self._on_selected)
        splitter.addWidget(self._note_list)

        self._note_edit = QTextEdit()
        self._note_edit.setPlaceholderText("选择左侧笔记查看/编辑，或点「新建笔记」开始...")
        self._note_edit.textChanged.connect(self._on_text_changed)
        splitter.addWidget(self._note_edit)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 600])
        v.addWidget(splitter, 1)

        # 自动保存防抖定时器
        self._note_save_timer = QTimer(self)
        self._note_save_timer.setSingleShot(True)
        self._note_save_timer.setInterval(NOTE_AUTOSAVE_INTERVAL_MS)
        self._note_save_timer.timeout.connect(self._on_save)
        # 加载笔记时防止触发自动保存
        self._loading_note = False
        self._current_note_id = None

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新笔记列表显示：标题 + 修改时间，支持关键字搜索（标题+内容）"""
        current_id = self._current_note_id
        keyword = self._note_search.text().strip().lower()
        self._note_list.clear()
        notes = self._note_manager.get_all_notes()
        shown = 0
        for n in notes:
            if keyword:
                in_title = keyword in (n.title or "").lower()
                in_content = keyword in (n.content or "").lower()
                if not (in_title or in_content):
                    continue
            title = n.title or "（无标题）"
            updated = n.update_time or ""
            date_part = updated[:DATETIME_DATE_LEN] if len(updated) >= DATETIME_DATE_LEN else updated
            time_part = updated[DATETIME_TIME_START:DATETIME_TIME_START + DATETIME_TIME_LEN] if len(updated) >= DATETIME_MIN_LEN else updated[DATETIME_TIME_START:]
            time_display = f"{date_part} {time_part}".strip()
            text = f"📝 {title}   · {time_display}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, n.note_id)
            preview = (n.content or "").replace("\n", " ").strip()[:NOTE_PREVIEW_LEN]
            item.setToolTip(f"标题: {title}\n时间: {time_display}\n内容预览: {preview}")
            self._note_list.addItem(item)
            if n.note_id == current_id:
                self._note_list.setCurrentItem(item)
            shown += 1

        if keyword:
            self._note_count_label.setText(f"显示 {shown} / 共 {len(notes)} 条")
        else:
            self._note_count_label.setText(f"共 {len(notes)} 条")

        if not notes:
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self._current_note_id = None

    def _on_selected(self, current, previous):
        """列表项切换：加载笔记内容"""
        if current is None:
            return
        note_id = current.data(Qt.ItemDataRole.UserRole)
        note = self._note_manager.get_note(note_id)
        if not note:
            return
        if self._note_save_timer.isActive():
            self._note_save_timer.stop()
            self._on_save()
        self._loading_note = True
        self._current_note_id = note_id
        self._note_edit.setPlainText(note.content)
        self._loading_note = False
        self._note_status_label.setText(f"编辑中: {note.update_time}")

    def _on_text_changed(self):
        """文本变化 → 启动防抖定时器"""
        if self._loading_note:
            return
        if self._current_note_id is None and not self._note_edit.toPlainText().strip():
            return
        self._note_save_timer.start()
        self._note_status_label.setText("● 未保存")

    def _on_save(self):
        """自动保存当前笔记"""
        if self._current_note_id is None:
            content = self._note_edit.toPlainText()
            if content.strip():
                self._current_note_id = self._note_manager.add_note(content)
                self.refresh()
                self._note_status_label.setText("已保存")
                self._host.data_changed.emit("note")
        else:
            content = self._note_edit.toPlainText()
            if not self._note_manager.update_note(self._current_note_id, content):
                if content.strip():
                    self._current_note_id = self._note_manager.add_note(content)
                    self.refresh()
            else:
                self._note_status_label.setText("已保存")
                self._host.data_changed.emit("note")

    def _on_new(self):
        """新建笔记"""
        if self._note_save_timer.isActive():
            self._note_save_timer.stop()
            self._on_save()
        self._current_note_id = None
        self._loading_note = True
        self._note_edit.clear()
        self._loading_note = False
        self._note_edit.setFocus()
        self._note_status_label.setText("新建笔记，输入内容自动保存")

    def _on_delete(self):
        """删除当前笔记"""
        if self._current_note_id is None:
            QMessageBox.information(self, "提示", "未选中任何笔记。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            "确认删除当前笔记？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._note_manager.delete_note(self._current_note_id)
            self._current_note_id = None
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self.refresh()
            self._host.data_changed.emit("note")

    def _on_context_menu(self, pos):
        """笔记列表右键菜单：编辑标题 / 删除"""
        item = self._note_list.itemAt(pos)
        if not item:
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_rename = menu.addAction("✏️ 编辑标题...")
        act_delete = menu.addAction("🗑 删除此笔记")
        action = menu.exec(self._note_list.mapToGlobal(pos))
        if action == act_rename:
            self._rename_dialog(note_id)
        elif action == act_delete:
            ret = QMessageBox.question(
                self, "确认删除", "确认删除此笔记？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._note_manager.delete_note(note_id)
                if self._current_note_id == note_id:
                    self._current_note_id = None
                    self._loading_note = True
                    self._note_edit.clear()
                    self._loading_note = False
                self.refresh()
                self._host.data_changed.emit("note")

    def _rename_dialog(self, note_id: int):
        """编辑笔记标题对话框"""
        note = self._note_manager.get_note(note_id)
        if not note:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑笔记标题")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setFixedSize(360, 140)

        v = QVBoxLayout(dialog)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(10)

        hint = QLabel("请输入新标题（1-50 字）：")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

        title_edit = QLineEdit(note.title or "")
        title_edit.setMaxLength(50)
        title_edit.returnPressed.connect(dialog.accept)
        v.addWidget(title_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        v.addWidget(btns)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_title = title_edit.text().strip()
            if new_title and new_title != note.title:
                self._note_manager.update_title(note_id, new_title)
                self.refresh()
                self._host.data_changed.emit("note")
