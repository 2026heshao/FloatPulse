# -*- coding: utf-8 -*-
"""
====================================================================
碎片合并预览对话框  -  MergePreviewDialog
====================================================================
将多个碎片按选中顺序拼接成一段文本，提供预览编辑区（可微调），
并支持「复制到剪贴板」或「存为一条新笔记」两种去向。

从 main_window.py 抽出为独立模块，降低其单文件复杂度。
依赖通过构造参数注入（fragments / note_manager / clipboard_monitor），
不反向依赖 MainWindow，保持高内聚、低耦合。
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QMessageBox
from PyQt6.QtCore import Qt

from src.fragment_manager import TYPE_ICONS


class MergePreviewDialog(QDialog):
    """
    碎片合并预览对话框。

    作用：
      - 将多个碎片按选中顺序拼接成一段文本
      - 提供预览编辑区（用户可微调合并结果）
      - 两个去向：复制到剪贴板 / 存为一条新笔记
    """

    def __init__(self, fragments, note_manager, clipboard_monitor, parent=None):
        super().__init__(parent)
        self._fragments = fragments
        self._note_manager = note_manager
        self._clipboard_monitor = clipboard_monitor

        self.setWindowTitle("合并碎片预览")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(520, 420)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(10)

        # 顶部信息
        info = QLabel(f"🔗 已选 {len(fragments)} 条碎片，合并后预览（可编辑）：")
        info.setObjectName("sectionLabel")
        v.addWidget(info)

        # 来源列表（紧凑显示）
        source_lines = []
        for i, f in enumerate(fragments, 1):
            icon = TYPE_ICONS.get(f.type, "📄")
            preview = f.preview(40)
            source_lines.append(f"{i}. {icon} {preview}")
        source_label = QLabel("\n".join(source_lines))
        source_label.setObjectName("hintLabel")
        source_label.setWordWrap(True)
        v.addWidget(source_label)

        # 预览编辑区（可编辑）
        self._preview_edit = QTextEdit()
        merged = "\n\n".join(f.content for f in fragments)
        self._preview_edit.setPlainText(merged)
        v.addWidget(self._preview_edit, 1)

        # 按钮区
        btns = QHBoxLayout()
        btns.addStretch()

        copy_btn = QPushButton("📋 复制到剪贴板")
        copy_btn.clicked.connect(self._on_copy)
        btns.addWidget(copy_btn)

        save_btn = QPushButton("💾 存为笔记")
        save_btn.setObjectName("secondaryBtn")
        save_btn.clicked.connect(self._on_save_note)
        btns.addWidget(save_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("secondaryBtn")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)

        v.addLayout(btns)

    def _on_copy(self):
        """复制合并内容到剪贴板"""
        text = self._preview_edit.toPlainText()
        if self._clipboard_monitor:
            self._clipboard_monitor.put_text(text)
        QMessageBox.information(self, "已复制", "合并内容已复制到剪贴板。")

    def _on_save_note(self):
        """保存合并内容为一条新笔记"""
        text = self._preview_edit.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "提示", "合并内容为空，无法保存。")
            return
        if self._note_manager:
            self._note_manager.add_note(text)
        QMessageBox.information(self, "已保存", "合并内容已存为一条新笔记。")
        self.accept()
