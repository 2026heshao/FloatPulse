# -*- coding: utf-8 -*-
"""
====================================================================
碎片合并预览对话框  -  MergePreviewDialog
====================================================================
将多个碎片按选中顺序拼接成一段文本，提供预览编辑区（可微调），
并支持「复制到剪贴板」或「存为一条新笔记」两种去向。

视觉与主窗口统一：继承 GlassDialog（无边框 + 玻璃壳 + 自绘标题栏），
不再使用系统原生标题栏，避免与主窗口风格割裂。
依赖通过构造参数注入（fragments / note_manager / clipboard_monitor），
不反向依赖 MainWindow，保持高内聚、低耦合。
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QTextEdit

from src.fragment_manager import TYPE_ICONS
from src.glass_dialog import GlassDialog, flash_button, make_separator


class MergePreviewDialog(GlassDialog):
    """
    碎片合并预览对话框。

    作用：
      - 将多个碎片按选中顺序拼接成一段文本
      - 提供预览编辑区（用户可微调合并结果）
      - 两个去向：复制到剪贴板 / 存为一条新笔记
    """

    def __init__(self, fragments, note_manager, clipboard_monitor,
                 host=None, parent=None):
        super().__init__(host, title="合并碎片预览",
                         subtitle=f"{len(fragments)} 条", parent=parent,
                         size=(620, 520))
        self._fragments = fragments
        self._note_manager = note_manager
        self._clipboard_monitor = clipboard_monitor

        body = self.body_layout

        # 顶部信息（标题 + 条数统计）
        head = QHBoxLayout()
        info = QLabel("🔗 合并结果预览（可直接编辑）")
        info.setObjectName("sectionLabel")
        head.addWidget(info)
        head.addStretch()
        tip = QLabel("顺序为列表中选中的先后顺序")
        tip.setObjectName("hintLabel")
        head.addWidget(tip)
        body.addLayout(head)

        # 来源列表（紧凑显示）
        source_lines = []
        for i, f in enumerate(fragments, 1):
            icon = TYPE_ICONS.get(f.type, "📄")
            source_lines.append(f"{i}. {icon} {f.preview(40)}")
        source_label = QLabel("\n".join(source_lines))
        source_label.setObjectName("hintLabel")
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(source_label)

        body.addWidget(make_separator())

        # 预览编辑区（可编辑）
        self._preview_edit = QTextEdit()
        self._preview_edit.setPlainText("\n\n".join(f.content for f in fragments))
        body.addWidget(self._preview_edit, 1)

        # 按钮区
        btns = self.add_footer([
            ("📋 复制到剪贴板", "primaryBtn", None),
            ("💾 存为笔记", "secondaryBtn", None),
            ("关闭", "secondaryBtn", self.reject),
        ])
        self._copy_btn, self._save_btn = btns[0], btns[1]
        self._copy_btn.clicked.connect(self._on_copy)
        self._save_btn.clicked.connect(self._on_save_note)

    def _on_copy(self):
        """复制合并内容到剪贴板（按钮内反馈，不再弹提示框打断）"""
        text = self._preview_edit.toPlainText()
        if self._clipboard_monitor:
            self._clipboard_monitor.put_text(text)
        flash_button(self._copy_btn, "✅ 已复制")

    def _on_save_note(self):
        """保存合并内容为一条新笔记"""
        text = self._preview_edit.toPlainText()
        if not text.strip():
            flash_button(self._save_btn, "⚠️ 内容为空")
            return
        if self._note_manager:
            self._note_manager.add_note(text)
        flash_button(self._save_btn, "✅ 已存为笔记", 900)
        QTimer.singleShot(900, self.accept)
