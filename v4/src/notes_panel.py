# -*- coding: utf-8 -*-
"""
====================================================================
笔记管理面板  -  NotesPanel
====================================================================
从 main_window.py 抽出的独立面板，承载笔记列表 / 编辑区 / 自动保存 /
搜索 / 重命名等业务逻辑。通过 host（MainWindow）访问笔记管理器与样式。
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QMenu, QTextEdit,
    QSplitter, QDialog,
    QAbstractItemView, QStyledItemDelegate,
)
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPen

from src.glass_dialog import make_dialog_buttons
from src.glass_message_box import GlassMessageBox
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
from src.time_format import format_relative_time
from src.theme import DEFAULT_THEME, get_colors
from src.icon_render import icon as render_icon
from src.controls import (tune_list_scrolling, EmptyState, IconButton,
                          PageTitle, attach_page_search_shortcut, SmoothInput)

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
            painter.setPen(QPen(_to_color(colors["primary_a30"]), 1))
            # panel_fill 是 QSS rgba 字符串，QColor 不认 —— 必须经 _to_color
            # 解析（直接 QColor("rgba(...)") 会得到无效色，绘制出来是黑色）
            painter.setBrush(_to_color(colors["panel_fill"]))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(2, 1, -2, -1),
                                    8, 8)
            painter.restore()
            return
        super().paint(painter, option, index)


def _format_relative_time(ts: str) -> str:
    """统一实现见 src/time_format.py（与 kb-search 结果页共用同一份）。

    口径 2026-10-02 起有一处可见变化：≥7 天不再原样返回 16 字符全格式，
    同年显示 MM-DD HH:MM、跨年才原样（其余六档与原实现逐字一致）。
    """
    return format_relative_time(ts)


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
        title = PageTitle("notes", "笔记管理", self._host)
        header.addWidget(title)
        header.addStretch()
        self._note_count_label = QLabel("共 0 条")
        self._note_count_label.setObjectName("hintLabel")
        header.addWidget(self._note_count_label)
        v.addLayout(header)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        new_btn = IconButton("plus", text="新建笔记", icon_size=14)
        new_btn.clicked.connect(self._on_new)
        toolbar.addWidget(new_btn)

        del_btn = IconButton("trash", text="删除当前", icon_size=14,
                             object_name="dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(del_btn)

        toolbar.addStretch()

        self._note_status_label = QLabel("")
        self._note_status_label.setObjectName("hintLabel")
        toolbar.addWidget(self._note_status_label)

        v.addLayout(toolbar)

        # ---- 搜索框 ----
        self._note_search = SmoothInput()
        self._note_search.setPlaceholderText("搜索标题或内容...")
        self._note_search.textChanged.connect(self.refresh)
        v.addWidget(self._note_search)

        # Ctrl+F：页级聚焦搜索框。2026-10-07 批次 N3 的页内写法已于
        # 2026-10-08（清单 A4）抽成公共 helper（controls.attach_page_
        # search_shortcut），插件中心 / 知识库 / 碎片工作台与本页共用
        # 一份实现：WidgetWithChildrenShortcut 限定页内作用域，不占全局
        # 命名空间（接入时的排查结论：main_window 全局键为 Ctrl+W/H/T/K、
        # F1、Ctrl+1~8 与触发键（默认 "/"），全局热键默认表亦无 Ctrl+F）。
        attach_page_search_shortcut(self, self._note_search)

        # ---- 左右分栏：列表 + 编辑区 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._note_list = QListWidget()
        tune_list_scrolling(self._note_list)  # 丝滑化清单 L3：像素级滚动 + 统一步长
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

        # 空态引导（A3 通用化）：叠在列表之上的 EmptyState 覆盖层，
        # 随列表 resize 贴合（attach_to 自带 eventFilter）；无动作钮 →
        # 鼠标全透明，右键菜单/滚轮照常落到列表
        self._note_empty_label = EmptyState(
            "notes", "还没有笔记",
            "点上方「新建笔记」开始记录，自动保存不怕丢")
        self._note_empty_label.attach_to(self._note_list)

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

    # ---- 状态栏统一入口（批次 N4 / N5）----
    def _set_status_text(self, text: str, unsaved: bool = False) -> None:
        """状态栏文本唯一写入点。

        - ``unsaved=True``（"● 未保存" 两态）→ 用主题 warning 语义色内联
          着色（色值取当刻 ``get_colors``，light/dark 各自跟随）；
        - 正常态清回空样式（还原 QSS hintLabel 灰字，不残留上次的色）。
        """
        if unsaved:
            colors = get_colors(
                getattr(self._host, "current_theme", None) or DEFAULT_THEME)
            self._note_status_label.setStyleSheet(f"color: {colors['warning']};")
        else:
            self._note_status_label.setStyleSheet("")
        self._note_status_label.setText(text)

    def _status_word_suffix(self) -> str:
        """字数后缀（N5）：取编辑区明文字符数；空内容省略（不显示"0 字"）。"""
        count = len(self._note_edit.toPlainText())
        return f" · {count} 字" if count else ""

    def _focus_search(self) -> None:
        """Ctrl+F：聚焦页内搜索框并全选既有词（便于直接覆盖输入）。"""
        self._note_search.setFocus()
        self._note_search.selectAll()

    def focus_new_note(self) -> None:
        """「新建笔记」公开委托（命令面板 action.new_note 落点）。

        复用 :meth:`_on_new` 的既有交互（清空编辑区 + 聚焦，输入后自动
        保存落盘），零新交互模式。
        """
        self._on_new()

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
                self._set_status_text(
                    "● 未保存 · 当前笔记不在搜索结果中", unsaved=True)
            elif filtered_out:
                self._set_status_text("当前笔记不在搜索结果中")
            elif not self._note_save_timer.isActive():
                self._set_status_editing()

        if not notes:
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self._current_note_id = None

    def _update_empty_state(self, shown: int, keyword: str):
        """列表无条目时显示空态引导（A3：图标+标题+提示）"""
        if shown > 0:
            self._note_empty_label.hide()
            return
        if keyword:
            self._note_empty_label.set_state(
                "search", f"没有匹配「{keyword}」的笔记",
                "换个关键词，或清空搜索框查看全部笔记")
        else:
            self._note_empty_label.set_state(
                "notes", "还没有笔记",
                "点上方「新建笔记」开始记录，自动保存不怕丢")
        self._note_empty_label.setGeometry(self._note_list.rect())
        self._note_empty_label.show()
        self._note_empty_label.raise_()

    def _set_status_editing(self) -> bool:
        """状态栏显示当前笔记的最近修改时间（相对时间）+ 字数"""
        note = self._note_manager.get_note(self._current_note_id)
        if note is None:
            return False
        self._set_status_text(
            f"编辑中：{_format_relative_time(note.update_time)}"
            f"{self._status_word_suffix()}")
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
        self._set_status_text("已重命名")
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
        self._set_status_text(
            f"编辑中：{_format_relative_time(note.update_time)}"
            f"{self._status_word_suffix()}")

    def _on_text_changed(self):
        """文本变化 → 启动防抖定时器"""
        if self._loading_note:
            return
        if self._current_note_id is None and not self._note_edit.toPlainText().strip():
            return
        self._note_save_timer.start()
        self._set_status_text(f"● 未保存{self._status_word_suffix()}",
                              unsaved=True)

    def _on_save(self):
        """自动保存当前笔记"""
        if self._current_note_id is None:
            content = self._note_edit.toPlainText()
            if content.strip():
                self._current_note_id = self._note_manager.add_note(content)
                self.refresh()
                self._set_status_text("已保存")
                self._host.data_changed.emit("note")
        else:
            content = self._note_edit.toPlainText()
            if not self._note_manager.update_note(self._current_note_id, content):
                if content.strip():
                    self._current_note_id = self._note_manager.add_note(content)
                    self.refresh()
            else:
                self._set_status_text("已保存")
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
        self._set_status_text("新建笔记，输入内容自动保存")

    def _on_delete(self):
        """删除当前笔记（临时笔记不可删除，需给出明确反馈）"""
        if self._current_note_id is None:
            GlassMessageBox.information(self, "提示", "未选中任何笔记。")
            return
        note = self._note_manager.get_note(self._current_note_id)
        if note is None:
            self._current_note_id = None
            self.refresh()
            GlassMessageBox.information(self, "提示", "该笔记已不存在，列表已刷新。")
            return
        if note.title == Note.TEMP_NOTE_TITLE:
            GlassMessageBox.information(
                self, "提示",
                "「📌 临时笔记」是悬浮球小卡片的专用笔记，不可删除。"
            )
            return
        if GlassMessageBox.question(
                self, "确认删除",
                "确认删除当前笔记？",
                danger=True):
            if not self._note_manager.delete_note(self._current_note_id):
                GlassMessageBox.warning(self, "删除失败", "笔记未能删除，请重试。")
                return
            self._current_note_id = None
            self._loading_note = True
            self._note_edit.clear()
            self._loading_note = False
            self.refresh()
            self._set_status_text("已删除")
            self._host.data_changed.emit("note")

    def _on_context_menu(self, pos):
        """笔记列表右键菜单：编辑标题 / 标题跟随开关 / 删除"""
        item = self._note_list.itemAt(pos)
        if not item:
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        target = self._note_manager.get_note(note_id)
        menu = QMenu(self)
        menu.setStyleSheet(self._host.container.styleSheet())
        act_rename = menu.addAction("编辑标题...")
        # 临时笔记标题固定，不提供跟随开关
        act_title_auto = None
        if target is not None and target.title != Note.TEMP_NOTE_TITLE:
            act_title_auto = menu.addAction(
                "锁定标题（不随内容更新）" if target.title_auto
                else "标题跟随内容"
            )
        act_delete = menu.addAction("删除此笔记")
        # 删除项危险语义：Qt 菜单无法按 action 单独设文字色，
        # 用 danger 色的自绘 trash 图标承载（UI 重构 05）
        act_delete.setIcon(render_icon(
            "trash", 14,
            get_colors(getattr(self._host, "current_theme", None)
                       or DEFAULT_THEME)["danger"]))
        menu.addSeparator()
        act_sticky = menu.addAction("钉到桌面")
        act_export = menu.addAction("导出全部到 Obsidian")
        action = menu.exec(self._note_list.mapToGlobal(pos))
        if action == act_rename:
            self._rename_dialog(note_id)
        elif act_title_auto is not None and action == act_title_auto:
            if self._note_manager.set_title_auto(note_id, not target.title_auto):
                self._set_status_text(
                    "已设为跟随内容" if target.title_auto else "已锁定标题"
                )
                self.refresh()
                self._host.data_changed.emit("note")
            else:
                GlassMessageBox.warning(self, "操作失败", "该笔记的标题不可调整。")
        elif action == act_delete:
            if target is None:
                self.refresh()
                return
            if target.title == Note.TEMP_NOTE_TITLE:
                GlassMessageBox.information(
                    self, "提示",
                    "「📌 临时笔记」是悬浮球小卡片的专用笔记，不可删除。"
                )
                return
            if GlassMessageBox.question(
                    self, "确认删除", "确认删除此笔记？",
                    danger=True):
                if not self._note_manager.delete_note(note_id):
                    GlassMessageBox.warning(self, "删除失败", "笔记未能删除，请重试。")
                    return
                if self._current_note_id == note_id:
                    self._current_note_id = None
                    self._loading_note = True
                    self._note_edit.clear()
                    self._loading_note = False
                self.refresh()
                self._set_status_text("已删除")
                self._host.data_changed.emit("note")
        elif action == act_sticky:
            self._pin_sticky(note_id)
        elif action == act_export:
            # 导出实现统一在宿主（三个面板共用，避免三份逻辑分叉）
            self._host.export_to_obsidian()

    def _pin_sticky(self, note_id: int):
        """把当前笔记钉成桌面便签（管理器由宿主晚绑定注入；None 给轻提示）"""
        manager = self._host.sticky_manager
        if manager is None:
            GlassMessageBox.information(self, "提示", "便签功能尚未就绪。")
            return
        ok, reason = manager.open(note_id)
        if not ok and reason == "limit":
            self._host.show_toast(
                f"便签最多同时钉 {manager.MAX_STICKIES} 个，请先关闭一些",
                kind="warning")

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

        title_edit = SmoothInput(note.title or "")
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
