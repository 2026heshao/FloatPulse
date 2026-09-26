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
    QAbstractItemView, QStyledItemDelegate, QStyle,
)
from PyQt6.QtCore import QRectF, Qt, QTimer, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen

import os
from datetime import datetime

from src.glass_dialog import make_dialog_buttons
from src.glass import _to_color   # QSS 风格颜色字符串（含 rgba）→ QColor
from src.constants import (
    NOTE_AUTOSAVE_INTERVAL_MS,
    DATETIME_DATE_LEN,
    DATETIME_TIME_START,
    DATETIME_TIME_LEN,
    DATETIME_MIN_LEN,
    NOTE_PREVIEW_LEN,
)
from src.note_manager import Note
from src.theme import DEFAULT_THEME, get_colors

# 手动命名标题的长度上限（与重命名对话框一致）
_TITLE_MAX_LEN = 50

# 时间戳格式（各 manager 统一用 strftime 生成）
_TS_FORMAT = "%Y-%m-%d %H:%M"


class _NoteListDelegate(QStyledItemDelegate):
    """笔记标题列表委托：编辑态不绘制文本（修复内联改名时的文字叠影）。

    内联编辑器（QLineEdit）的底色是半透明玻璃，且编辑器比行窄 —— 编辑期间
    若仍绘制原标题，文字会从编辑器四周透出叠影（2026-09-24 用户截图）。

    ⚠ 不能用 option.state & State_Editing 判断：实测（Qt 6.7.1）编辑期间
    paint 的 state 只带 Selected 不带 Editing。改用 createEditor/closeEditor
    跟踪「正在编辑的行号」。
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._editing_row = None
        self.closeEditor.connect(lambda *_: setattr(self, "_editing_row", None))

    def createEditor(self, parent, option, index):
        self._editing_row = index.row()
        return super().createEditor(parent, option, index)

    def paint(self, painter, option, index):
        if self._editing_row == index.row():
            theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
            colors = get_colors(theme)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(colors["primary_a30"]), 1))
            # panel_fill 是 QSS rgba 字符串，QColor 不认 —— 必须经 _to_color
            # 解析（直接 QColor("rgba(...)") 会得到无效色，绘制出来是黑色）
            painter.setBrush(_to_color(colors["panel_fill"]))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(2, 1, -2, -1),
                                    8, 8)
            painter.restore()
            return
        super().paint(painter, option, index)


def _format_relative_time(ts: str) -> str:
    """把 "YYYY-MM-DD HH:MM" 转成相对时间描述；解析失败则原样返回"""
    if not ts:
        return "未知时间"
    try:
        dt = datetime.strptime(ts.strip(), _TS_FORMAT)
    except ValueError:
        return ts
    secs = (datetime.now() - dt).total_seconds()
    if secs < 0:
        return ts                      # 时间在当前之后（系统时钟被改）→ 原样显示
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
    return ts


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
        # 内联改名：双击列表项 或 选中后按 F2 直接编辑标题
        self._note_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._note_list.itemChanged.connect(self._on_item_title_edited)
        # 编辑态不绘制原标题（半透明编辑器下会文字叠影，见 _NoteListDelegate）
        self._note_list.setItemDelegate(_NoteListDelegate(self._host))
        splitter.addWidget(self._note_list)

        # 空态提示：悬浮在列表之上，仅在无条目时显示
        self._note_empty_label = QLabel(self._note_list)
        self._note_empty_label.setObjectName("hintLabel")
        self._note_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._note_empty_label.setWordWrap(True)
        self._note_empty_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._note_empty_label.hide()
        self._note_list.installEventFilter(self)

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
        # 列表重建期间抑制 itemChanged（防止误判为用户改名）
        self._suppress_item_changed = False
        # 防重入：setCurrentItem 触发的保存有可能再次请求刷新，重入会打乱列表
        self._refreshing = False

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新笔记列表显示：仅标题，支持关键字搜索（标题+内容）"""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._refresh_impl()
        finally:
            self._refreshing = False

    def _refresh_impl(self):
        current_id = self._current_note_id
        keyword = self._note_search.text().strip().lower()
        # 重建列表期间抑制 itemChanged（防止把程序化设置文本误判为用户改名）
        self._suppress_item_changed = True
        try:
            self._note_list.clear()
            notes = self._note_manager.get_all_notes()
            shown = 0
            current_in_list = False
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
                # 列表项只显示标题（不带图标、不带日期）；时间保留在悬浮提示里
                item = QListWidgetItem(title)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, n.note_id)
                preview = (n.content or "").replace("\n", " ").strip()[:NOTE_PREVIEW_LEN]
                item.setToolTip(f"标题: {title}\n时间: {time_display}\n内容预览: {preview}")
                self._note_list.addItem(item)
                if n.note_id == current_id:
                    current_in_list = True
                    self._note_list.setCurrentItem(item)
                shown += 1
        finally:
            self._suppress_item_changed = False

        if keyword:
            self._note_count_label.setText(f"显示 {shown} / 共 {len(notes)} 条")
        else:
            self._note_count_label.setText(f"共 {len(notes)} 条")

        self._update_empty_state(shown, keyword)

        # ---- 状态栏：搜索把当前笔记过滤掉时必须提示，避免"以为在改别的" ----
        if current_id is not None:
            filtered_out = bool(keyword) and not current_in_list
            if filtered_out and self._note_save_timer.isActive():
                self._note_status_label.setText("● 未保存 · 当前笔记不在搜索结果中")
            elif filtered_out:
                self._note_status_label.setText("当前笔记不在搜索结果中")
            elif not self._note_save_timer.isActive():
                self._set_status_editing()

        if not notes:
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self._current_note_id = None

    def _update_empty_state(self, shown: int, keyword: str):
        """列表无条目时显示空态占位文案"""
        if shown > 0:
            self._note_empty_label.hide()
            return
        self._note_empty_label.setText(
            f"没有匹配「{keyword}」的笔记" if keyword
            else "还没有笔记，点上方「＋ 新建笔记」开始"
        )
        self._note_empty_label.setGeometry(self._note_list.rect())
        self._note_empty_label.show()
        self._note_empty_label.raise_()

    def eventFilter(self, obj, event):
        """列表尺寸变化时让空态提示贴合列表区域"""
        if obj is self._note_list and event.type() == QEvent.Type.Resize:
            self._note_empty_label.setGeometry(self._note_list.rect())
        return super().eventFilter(obj, event)

    def _set_status_editing(self) -> bool:
        """状态栏显示当前笔记的最近修改时间（相对时间）"""
        note = self._note_manager.get_note(self._current_note_id)
        if note is None:
            return False
        self._note_status_label.setText(f"编辑中：{_format_relative_time(note.update_time)}")
        return True

    def _on_item_title_edited(self, item):
        """列表项内联改名提交（双击 / F2）

        注意：本方法是 itemChanged 的槽，此刻 item（信号发送者）仍在派发中，
        直接 refresh() 会 clear() 掉它 → 延后到事件循环再重建列表。
        """
        if self._suppress_item_changed:
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        note = self._note_manager.get_note(note_id)
        if note is None:
            QTimer.singleShot(0, self.refresh)
            return
        new_title = item.text().strip()[:_TITLE_MAX_LEN]
        if not new_title:
            # 清空标题 → 回到自动标题（内容前 N 字）
            changed = self._note_manager.update_note(note_id, note.content, title="")
        elif new_title != note.title:
            changed = self._note_manager.update_title(note_id, new_title)
        else:
            return
        if not changed:
            return
        self._note_status_label.setText("已重命名")
        QTimer.singleShot(0, self._after_title_change)

    def _after_title_change(self):
        """内联改名后的收尾：重建列表并广播数据变更"""
        self.refresh()
        self._host.data_changed.emit("note")

    def flush_pending_save(self):
        """把防抖窗口内尚未落盘的编辑立即写盘（退出/切页兜底）"""
        if self._note_save_timer.isActive():
            self._note_save_timer.stop()
            self._on_save()

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
        self._note_status_label.setText(f"编辑中：{_format_relative_time(note.update_time)}")

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
        """删除当前笔记（临时笔记不可删除，需给出明确反馈）"""
        if self._current_note_id is None:
            QMessageBox.information(self, "提示", "未选中任何笔记。")
            return
        note = self._note_manager.get_note(self._current_note_id)
        if note is None:
            self._current_note_id = None
            self.refresh()
            QMessageBox.information(self, "提示", "该笔记已不存在，列表已刷新。")
            return
        if note.title == Note.TEMP_NOTE_TITLE:
            QMessageBox.information(
                self, "提示",
                "「📌 临时笔记」是悬浮球小卡片的专用笔记，不可删除。"
            )
            return
        ret = QMessageBox.question(
            self, "确认删除",
            "确认删除当前笔记？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            if not self._note_manager.delete_note(self._current_note_id):
                QMessageBox.warning(self, "删除失败", "笔记未能删除，请重试。")
                return
            self._current_note_id = None
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self.refresh()
            self._note_status_label.setText("已删除")
            self._host.data_changed.emit("note")

    def _on_context_menu(self, pos):
        """笔记列表右键菜单：编辑标题 / 标题跟随开关 / 删除"""
        item = self._note_list.itemAt(pos)
        if not item:
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        target = self._note_manager.get_note(note_id)
        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_rename = menu.addAction("✏️ 编辑标题...")
        # 临时笔记标题固定，不提供跟随开关
        act_title_auto = None
        if target is not None and target.title != Note.TEMP_NOTE_TITLE:
            act_title_auto = menu.addAction(
                "🔒 锁定标题（不随内容更新）" if target.title_auto
                else "🔄 标题跟随内容"
            )
        act_delete = menu.addAction("🗑 删除此笔记")
        action = menu.exec(self._note_list.mapToGlobal(pos))
        if action == act_rename:
            self._rename_dialog(note_id)
        elif act_title_auto is not None and action == act_title_auto:
            if self._note_manager.set_title_auto(note_id, not target.title_auto):
                self._note_status_label.setText(
                    "已设为跟随内容" if target.title_auto else "已锁定标题"
                )
                self.refresh()
                self._host.data_changed.emit("note")
            else:
                QMessageBox.warning(self, "操作失败", "该笔记的标题不可调整。")
        elif action == act_delete:
            if target is None:
                self.refresh()
                return
            if target.title == Note.TEMP_NOTE_TITLE:
                QMessageBox.information(
                    self, "提示",
                    "「📌 临时笔记」是悬浮球小卡片的专用笔记，不可删除。"
                )
                return
            ret = QMessageBox.question(
                self, "确认删除", "确认删除此笔记？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ret == QMessageBox.StandardButton.Yes:
                if not self._note_manager.delete_note(note_id):
                    QMessageBox.warning(self, "删除失败", "笔记未能删除，请重试。")
                    return
                if self._current_note_id == note_id:
                    self._current_note_id = None
                    self._loading_note = True
                    self._note_edit.clear()
                    self._loading_note = False
                self.refresh()
                self._note_status_label.setText("已删除")
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

        hint = QLabel(f"请输入新标题（1-{_TITLE_MAX_LEN} 字，留空则回到自动标题）：")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

        title_edit = QLineEdit(note.title or "")
        title_edit.setMaxLength(_TITLE_MAX_LEN)
        title_edit.returnPressed.connect(dialog.accept)
        v.addWidget(title_edit)

        v.addWidget(make_dialog_buttons(dialog))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_title = title_edit.text().strip()
            if not new_title:
                # 留空 → 恢复自动标题（内容前 N 字）
                if self._note_manager.update_note(note_id, note.content, title=""):
                    self.refresh()
                    self._host.data_changed.emit("note")
            elif new_title != note.title:
                if self._note_manager.update_title(note_id, new_title):
                    self.refresh()
                    self._host.data_changed.emit("note")
