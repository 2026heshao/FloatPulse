# -*- coding: utf-8 -*-
"""
====================================================================
碎片工作台面板  -  FragmentsPanel
====================================================================
从 main_window.py 抽出的独立面板，承载碎片列表 / 筛选 / 搜索 /
合并 / 转存 / 删除等业务逻辑。

通过构造参数 host（MainWindow）访问业务管理器与跨面板刷新入口，
自身仅持有本面板 UI 控件，降低 main_window 单文件复杂度。
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QDialog, QFrame, QMessageBox, QTextEdit,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor

from src.fragment_manager import TYPE_LABELS, TYPE_ICONS
from src.merge_preview_dialog import MergePreviewDialog
from src.constants import (
    DATETIME_DATE_LEN,
    DATETIME_TIME_START,
    DATETIME_TIME_LEN,
    DATETIME_MIN_LEN,
    FRAGMENT_PREVIEW_LEN,
)


class FragmentsPanel(QWidget):
    """碎片工作台面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        # 缓存业务管理器引用（引用在程序生命周期内不变）
        self._fragment_manager = host._fragment_manager
        self._note_manager = host._note_manager
        self._docx_manager = host._docx_manager
        self._nav_manager = host._nav_manager
        self._clipboard_monitor = host._clipboard_monitor
        self._build_ui()

    # ---- UI 构建 ----
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = QLabel("🧩 碎片工作台")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._frag_count_label = QLabel("共 0 条")
        self._frag_count_label.setObjectName("hintLabel")
        header.addWidget(self._frag_count_label)
        v.addLayout(header)

        # ---- 工具栏：筛选 + 搜索 + 刷新 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._frag_filter = QComboBox()
        self._frag_filter.addItem("全部类型", "all")
        self._frag_filter.addItem("📋 剪贴板文本", "clipboard_text")
        self._frag_filter.addItem("📁 剪贴板路径", "clipboard_path")
        self._frag_filter.addItem("📥 文件拾取",   "file_pickup")
        self._frag_filter.addItem("📚 知识段落",   "knowledge_segment")
        self._frag_filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self._frag_filter)

        self._frag_search = QLineEdit()
        self._frag_search.setPlaceholderText("🔍 搜索碎片内容...")
        self._frag_search.textChanged.connect(self.refresh)
        toolbar.addWidget(self._frag_search, 1)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)
        v.addLayout(toolbar)

        # ---- 碎片列表（多选） ----
        self._frag_list = QListWidget()
        self._frag_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._frag_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._frag_list.customContextMenuRequested.connect(self._on_context_menu)
        self._frag_list.itemDoubleClicked.connect(self._on_double_click)
        v.addWidget(self._frag_list, 1)

        # ---- 底部按钮栏 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        merge_btn = QPushButton("🔗 合并选中")
        merge_btn.clicked.connect(self._on_merge)
        bottom.addWidget(merge_btn)

        copy_btn = QPushButton("📋 复制选中")
        copy_btn.setObjectName("secondaryBtn")
        copy_btn.clicked.connect(self._on_copy)
        bottom.addWidget(copy_btn)

        bottom.addStretch()

        del_btn = QPushButton("🗑 删除选中")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        bottom.addWidget(del_btn)

        clear_btn = QPushButton("清空全部")
        clear_btn.setObjectName("dangerBtn")
        clear_btn.clicked.connect(self._on_clear)
        bottom.addWidget(clear_btn)

        v.addLayout(bottom)

    # ---- 刷新入口（供 host.refresh_page 调用） ----
    def refresh(self):
        """刷新碎片列表显示：按日期分组，每条只显示内容+时间(时分)"""
        ftype = self._frag_filter.currentData()
        keyword = self._frag_search.text().strip()
        theme = self._host.current_theme

        if ftype == "all":
            fragments = (self._fragment_manager.search_fragments(keyword)
                         if keyword else self._fragment_manager.get_all_fragments())
        else:
            all_of_type = self._fragment_manager.get_fragments_by_type(ftype)
            if keyword:
                kw = keyword.lower()
                fragments = [f for f in all_of_type
                             if kw in f.content.lower() or kw in f.source.lower()]
            else:
                fragments = all_of_type

        self._frag_list.clear()

        current_date = None
        for f in fragments:
            created = f.created_at or ""
            date_part = created[:DATETIME_DATE_LEN] if len(created) >= DATETIME_DATE_LEN else created
            time_part = created[DATETIME_TIME_START:DATETIME_TIME_START + DATETIME_TIME_LEN] if len(created) >= DATETIME_MIN_LEN else created[DATETIME_TIME_START:]

            if date_part != current_date:
                current_date = date_part
                date_item = QListWidgetItem(f"📅  {date_part}")
                date_item.setData(Qt.ItemDataRole.UserRole, None)
                flags = date_item.flags()
                date_item.setFlags(flags & ~Qt.ItemFlag.ItemIsSelectable
                                   & ~Qt.ItemFlag.ItemIsEnabled)
                date_item.setForeground(
                    QColor("#5BC0BE") if theme == "light" else QColor("#6FFFE9")
                )
                f_font = date_item.font()
                f_font.setBold(True)
                date_item.setFont(f_font)
                date_item.setSizeHint(QSize(0, 30))
                self._frag_list.addItem(date_item)

            preview_text = f.preview(FRAGMENT_PREVIEW_LEN)
            text = f"   {preview_text}    {time_part}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, f.fragment_id)
            if f.type in ("clipboard_path", "file_pickup"):
                item.setForeground(QColor("#1976D2") if theme == "light"
                                   else QColor("#64B5F6"))
            icon = TYPE_ICONS.get(f.type, "📄")
            label = TYPE_LABELS.get(f.type, "未知")
            tip_lines = [f"{icon} 类型: {label}"]
            if f.source:
                tip_lines.append(f"🔗 来源: {f.source}")
            tip_lines.append(f"🕐 时间: {created}")
            tip_lines.append(f"📝 内容:\n{f.content}")
            item.setToolTip("\n".join(tip_lines))
            self._frag_list.addItem(item)

        total = self._fragment_manager.count()
        self._frag_count_label.setText(f"显示 {len(fragments)} 条 / 共 {total} 条")
        self._host.data_changed.emit("fragment")

    # ---- 右键菜单 ----
    def _on_context_menu(self, pos):
        item = self._frag_list.itemAt(pos)
        if not item:
            return
        fid = item.data(Qt.ItemDataRole.UserRole)
        if fid is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_detail = menu.addAction("🔍 查看详情")
        act_copy = menu.addAction("📋 复制内容")
        menu.addSeparator()
        act_to_note = menu.addAction("💾 存为笔记")
        act_to_kb = menu.addAction("📚 加入知识库")
        act_to_nav = menu.addAction("🌐 添加至网址导航")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除")
        action = menu.exec(self._frag_list.mapToGlobal(pos))
        if action == act_detail:
            self._show_detail(fid)
        elif action == act_copy:
            frag = self._fragment_manager.get_fragment(fid)
            if frag:
                self._clipboard_monitor.put_text(frag.content)
        elif action == act_to_note:
            self._to_note(fid)
        elif action == act_to_kb:
            self._to_knowledge(fid)
        elif action == act_to_nav:
            self._to_nav(fid)
        elif action == act_delete:
            if self._fragment_manager.delete_fragment(fid):
                self.refresh()

    def _to_note(self, fragment_id: int):
        """将碎片转存为一条新笔记"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return
        title_text = frag.content.replace("\n", " ").strip()[:20]
        title = f"💾 {title_text}{'...' if len(frag.content) > 20 else ''}"
        self._note_manager.add_note(frag.content, title=title)
        self._host.refresh_page("notes")
        self._host.data_changed.emit("note")
        QMessageBox.information(self, "已转存", f"碎片已存为新笔记：\n{title}")

    def _to_knowledge(self, fragment_id: int):
        """将碎片内容追加到知识库 docx 末尾"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return
        ret = QMessageBox.question(
            self, "确认加入知识库",
            f"将以下碎片内容追加到知识库末尾？\n\n"
            f"{frag.content[:80]}{'...' if len(frag.content) > 80 else ''}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        new_idx = self._docx_manager.append_paragraph(frag.content)
        if new_idx >= 0:
            if self._docx_manager.save():
                self._host.refresh_page("knowledge")
                self._host.data_changed.emit("knowledge")
                QMessageBox.information(
                    self, "已加入",
                    f"碎片已追加为知识库段落（编号 {new_idx+1}）。"
                )
            else:
                QMessageBox.warning(self, "保存失败", "docx 保存失败。")
        else:
            QMessageBox.warning(self, "失败", "追加段落失败。")

    def _to_nav(self, fragment_id: int):
        """将 URL 碎片添加到网址导航"""
        if not self._nav_manager:
            QMessageBox.warning(self, "提示", "网址导航管理器未初始化")
            return
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return
        url = frag.content.strip()
        groups = self._nav_manager.get_groups()
        if not groups:
            self._nav_manager.add_group("默认")
            groups = self._nav_manager.get_groups()
        gid = groups[0].group_id
        _, existing = self._nav_manager.find_site_by_url(url)
        if existing:
            QMessageBox.information(self, "已存在", f"该 URL 已在网址导航中：\n{existing.title}")
            return
        title = url.split("//")[-1].split("/")[0] if "//" in url else url[:20]
        self._nav_manager.add_site(gid, title, url)
        self._host.refresh_page("nav")
        self._host.data_changed.emit("nav")
        QMessageBox.information(self, "已添加", f"已将 URL 添加到网址导航：\n{title}")

    def _on_double_click(self, item):
        fid = item.data(Qt.ItemDataRole.UserRole)
        if fid is None:
            return
        self._show_detail(fid)

    def _show_detail(self, fragment_id):
        """碎片详情对话框：显示完整内容 + 元数据 + 复制按钮"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("碎片详情")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.resize(560, 460)

        v = QVBoxLayout(dialog)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(10)

        icon = TYPE_ICONS.get(frag.type, "")
        label = TYPE_LABELS.get(frag.type, "未知")
        meta_lines = [
            f"{icon} 类型: {label}",
            f"🆔 ID: {frag.fragment_id}",
            f"🕐 时间: {frag.created_at}",
        ]
        if frag.source:
            meta_lines.append(f"🔗 来源: {frag.source}")
        meta_label = QLabel("\n".join(meta_lines))
        meta_label.setObjectName("hintLabel")
        meta_label.setWordWrap(True)
        v.addWidget(meta_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        v.addWidget(sep)

        content_label = QLabel("📝 完整内容：")
        content_label.setObjectName("sectionLabel")
        v.addWidget(content_label)

        content_edit = QTextEdit()
        content_edit.setReadOnly(True)
        content_edit.setPlainText(frag.content)
        v.addWidget(content_edit, 1)

        btns = QHBoxLayout()
        btns.addStretch()

        copy_btn = QPushButton("📋 复制全部内容")
        copy_btn.clicked.connect(
            lambda: self._clipboard_monitor.put_text(frag.content)
            or QMessageBox.information(dialog, "已复制", "完整内容已复制到剪贴板。")
        )
        btns.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("secondaryBtn")
        close_btn.clicked.connect(dialog.accept)
        btns.addWidget(close_btn)

        v.addLayout(btns)
        dialog.exec()

    def _get_selected_ids(self) -> list:
        """获取当前选中的碎片 id 列表（过滤日期分组标题行 None id）"""
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self._frag_list.selectedItems()
                if item.data(Qt.ItemDataRole.UserRole) is not None]

    def _on_merge(self):
        """合并选中的碎片"""
        ids = self._get_selected_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "提示", "请至少选择 2 条碎片进行合并。")
            return
        fragments = self._fragment_manager.get_fragments_by_ids(ids)
        if not fragments:
            return
        dialog = MergePreviewDialog(fragments, self._note_manager,
                                    self._clipboard_monitor, self)
        dialog.exec()

    def _on_copy(self):
        """复制选中的碎片内容到剪贴板"""
        ids = self._get_selected_ids()
        if not ids:
            return
        fragments = self._fragment_manager.get_fragments_by_ids(ids)
        text = "\n\n".join(f.content for f in fragments)
        self._clipboard_monitor.put_text(text)

    def _on_delete(self):
        """删除选中的碎片"""
        ids = self._get_selected_ids()
        if not ids:
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确认删除选中的 {len(ids)} 条碎片？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._fragment_manager.delete_fragments(ids)
            self.refresh()

    def _on_clear(self):
        """清空全部碎片"""
        ret = QMessageBox.question(
            self, "确认清空",
            "确认清空全部碎片？此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._fragment_manager.clear_all()
            self.refresh()
