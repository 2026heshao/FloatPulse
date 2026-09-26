# -*- coding: utf-8 -*-
"""
====================================================================
知识库面板  -  KnowledgePanel
====================================================================
从 main_window.py 抽出的独立面板，承载知识库段落列表 / 搜索 /
增删改 / 加入碎片池 / 重新加载等业务逻辑。
通过 host（MainWindow）访问 docx 管理器、碎片管理器与样式。
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QTextEdit,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer

from src.constants import PARAGRAPH_PREVIEW_LEN
from src.glass_dialog import GlassDialog

# 搜索去抖毫秒数（与碎片页 SEARCH_DEBOUNCE_MS 同值；两面板各自本地定义，避免跨面板耦合）
SEARCH_DEBOUNCE_MS = 250


class KnowledgePanel(QWidget):
    """知识库面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._docx_manager = host._docx_manager
        self._fragment_manager = host._fragment_manager
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = QLabel("📚 知识库")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._kb_count_label = QLabel("共 0 段")
        self._kb_count_label.setObjectName("hintLabel")
        header.addWidget(self._kb_count_label)
        v.addLayout(header)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        add_btn = QPushButton("➕ 新增知识")
        add_btn.clicked.connect(self._on_append)
        toolbar.addWidget(add_btn)

        add_frag_btn = QPushButton("📥 加入碎片池")
        add_frag_btn.setObjectName("secondaryBtn")
        add_frag_btn.clicked.connect(self._on_add_to_fragments)
        toolbar.addWidget(add_frag_btn)

        reload_btn = QPushButton("🔄 重新加载")
        reload_btn.setObjectName("secondaryBtn")
        reload_btn.clicked.connect(self._on_reload)
        toolbar.addWidget(reload_btn)

        toolbar.addStretch()

        self._kb_modify_label = QLabel("")
        self._kb_modify_label.setObjectName("hintLabel")
        toolbar.addWidget(self._kb_modify_label)

        v.addLayout(toolbar)

        # ---- 搜索框 ----
        self._kb_search = QLineEdit()
        self._kb_search.setPlaceholderText("🔍 搜索段落内容...")
        # 搜索输入只做本地过滤（外部修改检测走 recheck=True 路径，避免每敲一字算一次 docx 哈希）
        # 输入去抖：停顿 SEARCH_DEBOUNCE_MS 才真正刷新，敲字过程不重建列表
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(
            lambda: self.refresh(preserve_view=False, recheck=False))
        self._kb_search.textChanged.connect(self._on_search_changed)
        v.addWidget(self._kb_search)

        # ---- 段落列表（多选） ----
        self._kb_list = QListWidget()
        self._kb_list.setObjectName("kbList")
        # 长列表（300+ 段）：垂直滚动条常驻，滑块样式见 theme.py 的 #kbList 规则
        self._kb_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._kb_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._kb_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._kb_list.customContextMenuRequested.connect(self._on_context_menu)
        self._kb_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        v.addWidget(self._kb_list, 1)

        # ---- 底部提示 ----
        hint = QLabel("双击段落：编辑 · 右键段落：编辑 / 删除 / 在此后新增 / 加入碎片池")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

    # ---- 刷新入口 ----
    def _on_search_changed(self, _text: str):
        """搜索输入去抖：停顿 SEARCH_DEBOUNCE_MS 后才刷新列表"""
        self._search_timer.start()

    def _on_item_double_clicked(self, item):
        """双击段落 → 直接打开编辑（无结果占位行没有段落号，忽略）"""
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self._edit_paragraph(index)

    def refresh(self, preserve_view: bool = True, recheck: bool = True):
        """刷新知识库段落列表

        preserve_view=True 时保留滚动位置与选中项（编辑/删除后列表不跳顶）；
        搜索等结果集变化的路径传 preserve_view=False（回顶部，与碎片页一致）。
        recheck=False 时跳过外部修改检测（搜索输入等高频路径），
        仅在页面刷新 / 重新加载等低频路径做完整检测。
        """
        keyword = self._kb_search.text().strip().lower()
        bar = self._kb_list.verticalScrollBar()
        scroll_value = bar.value() if preserve_view else 0
        selected_idx = {item.data(Qt.ItemDataRole.UserRole)
                        for item in self._kb_list.selectedItems()}
        if not preserve_view:
            selected_idx = set()
        self._kb_list.clear()
        paragraphs = self._docx_manager.get_paragraphs()
        shown = 0
        for p in paragraphs:
            if keyword and keyword not in (p.text or "").lower():
                continue
            preview_text = (p.text or "").replace("\n", " ").strip()[:PARAGRAPH_PREVIEW_LEN]
            text = f"[{p.index+1}] {preview_text}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, p.index)
            item.setToolTip(p.text)
            self._kb_list.addItem(item)
            if p.index in selected_idx:
                item.setSelected(True)
            shown += 1
        if keyword:
            self._kb_count_label.setText(f"显示 {shown} / 共 {len(paragraphs)} 段")
            if shown == 0:
                # 无结果占位提示（不可选中，双击/右键均被 UserRole=None 守卫挡下）
                placeholder = QListWidgetItem("（无匹配段落，换个关键词试试）")
                placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._kb_list.addItem(placeholder)
        else:
            self._kb_count_label.setText(f"共 {len(paragraphs)} 段")
        bar.setValue(min(scroll_value, bar.maximum()))

        if not recheck:
            return

        theme = self._host.current_theme
        if self._docx_manager.check_external_modification():
            self._kb_modify_label.setText("⚠️ 检测到外部修改，建议重新加载")
            self._kb_modify_label.setStyleSheet("color: #E67E22;" if theme == "light"
                                                else "color: #F39C12;")
        else:
            self._kb_modify_label.setText("✓ 文件无外部修改")
            self._kb_modify_label.setStyleSheet("")

    def _on_context_menu(self, pos):
        item = self._kb_list.itemAt(pos)
        if not item:
            return
        index = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_edit = menu.addAction("✏️ 编辑此段...")
        act_add_frag = menu.addAction("📥 加入碎片池")
        menu.addSeparator()
        act_insert = menu.addAction("➕ 在此后新增段落...")
        act_delete = menu.addAction("🗑 删除此段")
        action = menu.exec(self._kb_list.mapToGlobal(pos))

        if action == act_edit:
            self._edit_paragraph(index)
        elif action == act_add_frag:
            self._add_to_fragments(index)
        elif action == act_insert:
            self._insert_after(index)
        elif action == act_delete:
            self._delete_paragraph(index)

    def _edit_paragraph(self, index: int):
        """编辑段落对话框（玻璃风格，与主窗口一致）"""
        text = self._docx_manager.get_paragraph_text(index)
        if not text:
            return
        dlg = GlassDialog(self._host, title=f"编辑段落 {index + 1}",
                          size=(520, 400))
        edit = QTextEdit()
        edit.setPlainText(text)
        dlg.body_layout.addWidget(edit, 1)

        btns = dlg.add_footer([
            ("💾 保存", "primaryBtn", None),
            ("取消", "secondaryBtn", dlg.reject),
        ])

        def _save():
            new_text = edit.toPlainText().strip()
            if not new_text or new_text == text:
                dlg.reject()
                return
            if self._docx_manager.update_paragraph_text(index, new_text):
                if self._docx_manager.save():
                    dlg.accept()
                    self.refresh()
                    self._host.data_changed.emit("knowledge")
                else:
                    QMessageBox.warning(self, "保存失败", "docx 保存失败，请检查文件权限。")
            else:
                QMessageBox.warning(self, "修改失败", "段落修改失败。")

        btns[0].clicked.connect(_save)
        dlg.exec()

    def _insert_after(self, index: int):
        """在指定段落后新增段落（玻璃风格）"""
        dlg = GlassDialog(self._host, title=f"在段落 {index + 1} 后新增",
                          size=(520, 400))
        edit = QTextEdit()
        edit.setPlaceholderText("输入新段落内容...")
        dlg.body_layout.addWidget(edit, 1)

        btns = dlg.add_footer([
            ("💾 保存", "primaryBtn", None),
            ("取消", "secondaryBtn", dlg.reject),
        ])

        def _save():
            new_text = edit.toPlainText().strip()
            if not new_text:
                dlg.reject()
                return
            new_idx = self._docx_manager.insert_paragraph_after(index, new_text)
            if new_idx >= 0:
                if self._docx_manager.save():
                    dlg.accept()
                    self.refresh()
                    self._host.data_changed.emit("knowledge")
                else:
                    QMessageBox.warning(self, "保存失败", "docx 保存失败。")
            else:
                QMessageBox.warning(self, "新增失败", "段落新增失败。")

        btns[0].clicked.connect(_save)
        dlg.exec()

    def _delete_paragraph(self, index: int):
        """删除段落"""
        if not self._confirm("确认删除",
                             f"确认删除段落 {index + 1}？此操作将修改 docx 文件。",
                             "🗑 删除"):
            return
        if self._docx_manager.delete_paragraph(index):
            if self._docx_manager.save():
                self.refresh()
                self._host.data_changed.emit("knowledge")
            else:
                QMessageBox.warning(self, "保存失败", "docx 保存失败，已尝试回滚备份。")
                self._docx_manager.restore_backup()
                self._docx_manager.reload()
                self.refresh()

    def _confirm(self, title: str, text: str, confirm_label: str = "确认") -> bool:
        """玻璃风格确认框（替代原生 QMessageBox.question）"""
        dlg = GlassDialog(self._host, title=title, size=(440, 220))
        msg = QLabel(text)
        msg.setWordWrap(True)
        dlg.body_layout.addWidget(msg, 1)

        result = {"ok": False}
        btns = dlg.add_footer([
            (confirm_label, "dangerBtn", None),
            ("取消", "secondaryBtn", dlg.reject),
        ])

        def _ok():
            result["ok"] = True
            dlg.accept()

        btns[0].clicked.connect(_ok)
        dlg.exec()
        return result["ok"]

    def _add_to_fragments(self, index: int):
        """加入段落到碎片池"""
        text = self._docx_manager.get_paragraph_text(index)
        if not text:
            return
        fid = self._fragment_manager.add_knowledge_segment(text, source=f"知识库#{index+1}")
        QMessageBox.information(self, "已加入", f"段落已加入碎片池（id={fid}）。")

    def _on_add_to_fragments(self):
        """批量加入选中段落到碎片池"""
        ids = [item.data(Qt.ItemDataRole.UserRole)
               for item in self._kb_list.selectedItems()]
        ids = [i for i in ids if i is not None]   # 过滤"无匹配段落"占位行
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要加入的段落。")
            return
        count = 0
        for idx in ids:
            text = self._docx_manager.get_paragraph_text(idx)
            if text:
                self._fragment_manager.add_knowledge_segment(text, source=f"知识库#{idx+1}")
                count += 1
        QMessageBox.information(self, "已加入", f"已加入 {count} 段到碎片池。")

    def _on_append(self):
        """新增知识：弹窗输入内容，追加到 docx 末尾（玻璃风格）"""
        dlg = GlassDialog(self._host, title="新增知识", size=(520, 420))
        hint = QLabel("📝 输入新知识内容（将追加到知识库末尾）：")
        hint.setObjectName("sectionLabel")
        dlg.body_layout.addWidget(hint)

        edit = QTextEdit()
        edit.setPlaceholderText("输入新段落内容...")
        dlg.body_layout.addWidget(edit, 1)

        btns = dlg.add_footer([
            ("💾 保存", "primaryBtn", None),
            ("取消", "secondaryBtn", dlg.reject),
        ])

        def _save():
            new_text = edit.toPlainText().strip()
            if not new_text:
                QMessageBox.information(self, "提示", "内容为空，未新增。")
                return
            new_idx = self._docx_manager.append_paragraph(new_text)
            if new_idx >= 0:
                if self._docx_manager.save():
                    dlg.accept()
                    self.refresh()
                    self._host.data_changed.emit("knowledge")
                    QMessageBox.information(
                        self, "新增成功",
                        f"已追加为新段落（编号 {new_idx+1}）。"
                    )
                else:
                    QMessageBox.warning(self, "保存失败", "docx 保存失败，请检查文件权限。")
            else:
                QMessageBox.warning(self, "新增失败", "段落追加失败。")

        btns[0].clicked.connect(_save)
        dlg.exec()

    def _on_reload(self):
        """重新加载 docx"""
        if not self._confirm("确认重新加载",
                             "重新加载将丢弃当前未保存的内存修改，"
                             "并重新读取 docx 文件。确认？",
                             "🔄 重新加载"):
            return
        paragraphs, err = self._docx_manager.reload()
        if err:
            QMessageBox.warning(self, "加载失败", err)
        else:
            self.refresh()
            self._host.data_changed.emit("knowledge")
