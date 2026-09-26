# -*- coding: utf-8 -*-
"""
====================================================================
碎片内容编辑对话框  -  FragmentEditDialog
====================================================================
碎片此前是只读的（数据层没有 update 接口），复制来带了多余换行、
署名、广告尾巴时只能"存为笔记再改"。本对话框提供就地编辑能力。

视觉与主窗口一致：继承 GlassDialog（无边框 + 玻璃壳 + 自绘标题栏）。
保存逻辑通过 on_save 回调注入（返回 True 表示成功），本类不直接依赖
FragmentManager，保持低耦合。
====================================================================
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QLabel, QTextEdit

from src.glass_dialog import GlassDialog, flash_button, make_separator


class FragmentEditDialog(GlassDialog):
    """编辑单条碎片的内容"""

    def __init__(self, fragment, on_save, host=None, parent=None):
        super().__init__(host, title="编辑碎片",
                         subtitle=f"#{fragment.fragment_id}",
                         parent=parent, size=(620, 480))
        self._fragment = fragment
        self._on_save = on_save

        body = self.body_layout

        tip = QLabel("🧩 直接修改下方内容，保存后立即生效")
        tip.setObjectName("hintLabel")
        body.addWidget(tip)

        body.addWidget(make_separator())

        self._edit = QTextEdit()
        self._edit.setPlainText(fragment.content)
        self._edit.setPlaceholderText("碎片内容不能为空")
        self._edit.setTabChangesFocus(False)
        body.addWidget(self._edit, 1)

        btns = self.add_footer([
            ("💾 保存修改", "primaryBtn", None),
            ("取消", "secondaryBtn", self.reject),
        ])
        self._save_btn = btns[0]
        self._save_btn.clicked.connect(self._save)

        self._edit.setFocus()
        # 光标置于文末（多数场景是清理尾部冗余）
        self._edit.moveCursor(QTextCursor.MoveOperation.End)

    def _save(self):
        text = self._edit.toPlainText()
        if not text.strip():
            flash_button(self._save_btn, "⚠️ 内容不能为空")
            return
        if text == self._fragment.content:
            flash_button(self._save_btn, "未做修改")
            return
        ok = bool(self._on_save(text))
        if not ok:
            flash_button(self._save_btn, "⚠️ 保存失败")
            return
        flash_button(self._save_btn, "✅ 已保存", 900)
        QTimer.singleShot(900, self.accept)

    def keyPressEvent(self, event):
        """Ctrl+Enter 保存（文本框内换行留给 Enter）"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._save()
            return
        super().keyPressEvent(event)
