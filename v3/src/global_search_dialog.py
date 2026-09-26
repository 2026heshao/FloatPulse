# -*- coding: utf-8 -*-
"""
====================================================================
全库统一搜索对话框  -  GlobalSearchDialog
====================================================================
跨 碎片 / 笔记 / 任务 / 临时素材 聚合检索；双击结果跳转对应面板。
输入去抖 250ms，纯内存过滤，无磁盘 IO。

视觉与主窗口一致：继承 GlassDialog（无边框 + 玻璃壳 + 自绘标题栏 +
自绘外圈阴影），QSS 直接复用主窗口容器样式表，主题切换自动跟随。
====================================================================
"""

from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut

from src.glass_dialog import GlassDialog, make_separator
from src.theme import DEFAULT_THEME, get_colors

PREVIEW_LEN = 80
PER_GROUP_LIMIT = 50

# (组名, 对应主窗口页面索引)
GROUPS = [
    ("🧩 碎片", 0),
    ("📋 任务", 1),
    ("📝 笔记", 2),
    ("🗂 素材", 4),
]


class GlobalSearchDialog(GlassDialog):
    """全库搜索：Esc 关闭 / 双击或回车跳转"""

    jump_requested = pyqtSignal(int, str)   # (page_index, keyword)

    def __init__(self, fragment_manager, note_manager, task_manager,
                 temp_asset_manager, parent=None, host=None):
        super().__init__(host=host, title="全库搜索", subtitle="Ctrl+K",
                         parent=parent, size=(720, 604))
        self._fm = fragment_manager
        self._nm = note_manager
        self._tm = task_manager
        self._am = temp_asset_manager

        self._jump_page = None
        self._jump_keyword = ""

        body = self.body_layout

        self._input = QLineEdit()
        self._input.setObjectName("searchInput")
        self._input.setPlaceholderText("🔍 搜索碎片 / 笔记 / 任务 / 素材...")
        self._input.setFixedHeight(36)
        self._input.setToolTip("输入即搜（去抖 250ms），双击结果跳转到对应面板")
        self._input.textChanged.connect(self._on_text_changed)
        body.addWidget(self._input)

        body.addWidget(make_separator())

        self._tree = QTreeWidget()
        self._tree.setObjectName("searchTree")
        self._tree.setHeaderLabels(["内容", "详情"])
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setIndentation(16)
        self._tree.setFrameShape(QFrame.Shape.NoFrame)
        header = self._tree.header()
        header.setFixedHeight(34)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self._tree.itemActivated.connect(self._jump)
        self._tree.itemDoubleClicked.connect(self._jump)
        body.addWidget(self._tree, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._hint = QLabel("输入关键词开始搜索")
        self._hint.setObjectName("hintLabel")
        footer.addWidget(self._hint)
        footer.addStretch()
        shortcut_tip = QLabel("双击 / 回车跳转 · Esc 关闭")
        shortcut_tip.setObjectName("hintLabel")
        footer.addWidget(shortcut_tip)
        body.addLayout(footer)

        # 输入去抖
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._do_search)

        # Esc 关闭对话框（不触发主窗口的退出快捷键）
        esc = QShortcut(QKeySequence("Escape"), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close)

    # ---- 搜索 ----
    def apply_theme(self):
        """主题切换：套主窗口 QSS + 玻璃壳配色，并刷新组标题颜色（item 颜色非 QSS 管辖）

        注意：基类 ``__init__`` 末尾就会调用一次 apply_theme，此时本类的控件
        还没建好，所以下面必须用 getattr 容错。
        """
        super().apply_theme()
        inp = getattr(self, "_input", None)
        if inp is not None and inp.text().strip():
            self._do_search()

    def _on_text_changed(self, _text):
        self._debounce.start()

    def open_and_focus(self):
        """打开并聚焦输入框（保留上次关键词便于修改）"""
        self._input.setFocus()
        self._input.selectAll()
        self._do_search()
        self.show()
        self.raise_()
        self.activateWindow()

    def _group_text_color(self) -> QColor:
        """组标题用次要文字色（跟随当前主题）"""
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        return QColor(get_colors(theme).get("text_secondary", "#8A8F98"))

    def _do_search(self):
        kw = self._input.text().strip().lower()
        self._tree.clear()
        self._jump_page = None
        if not kw:
            self._hint.setText("输入关键词开始搜索")
            return

        group_color = self._group_text_color()
        total = 0
        for group_name, page_idx in GROUPS:
            hits = self._search_group(group_name, kw)
            if not hits:
                continue
            top = QTreeWidgetItem([group_name, f"{len(hits)} 条"])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            # 组节点不可跳转：加粗 + 次要色，与子项区分
            font = top.font(0)
            font.setBold(True)
            for col in (0, 1):
                top.setFont(col, font)
                top.setForeground(col, group_color)
            self._tree.addTopLevelItem(top)
            for preview, detail, in hits[:PER_GROUP_LIMIT]:
                child = QTreeWidgetItem([preview, detail])
                child.setData(0, Qt.ItemDataRole.UserRole,
                              (page_idx, self._input.text().strip()))
                child.setToolTip(0, preview)
                child.setToolTip(1, detail)
                top.addChild(child)
            top.setExpanded(True)
            total += len(hits)

        shown = min(total, PER_GROUP_LIMIT * len(GROUPS))
        self._hint.setText(f"共命中 {total} 条"
                           + (f"（每组最多显示 {PER_GROUP_LIMIT} 条）" if total > shown else ""))

    def _search_group(self, group_name, kw):
        """按组检索，返回 [(预览文本, 详情), ...]"""
        hits = []
        try:
            if group_name.startswith("🧩"):
                for f in self._fm.get_all_fragments():
                    c = (f.content or "")
                    if kw in c.lower():
                        hits.append((self._preview(c),
                                     f"{f.type} · {f.created_at}"))
            elif group_name.startswith("📋"):
                for t in self._tm.get_all_tasks():
                    text = (t.title or "") + " " + (t.note or "")
                    if kw in text.lower():
                        mark = "✔ " if t.done else ""
                        hits.append((mark + self._preview(t.title or ""),
                                     f"{'已完成' if t.done else '进行中'}"
                                     + (f" · 截止 {t.deadline}" if t.deadline else "")))
            elif group_name.startswith("📝"):
                for n in self._nm.get_all_notes():
                    text = (n.title or "") + " " + (n.content or "")
                    if kw in text.lower():
                        hits.append((self._preview(n.title or n.content or ""),
                                     f"更新于 {n.update_time}"))
            elif group_name.startswith("🗂"):
                if self._am is not None:
                    for a in self._am.get_all_assets():
                        if kw in (a.original_name or "").lower():
                            size_kb = a.size_bytes / 1024.0
                            size_s = (f"{size_kb:.0f} KB" if size_kb < 1024
                                      else f"{size_kb/1024:.1f} MB")
                            hits.append((a.original_name or "",
                                         f"{'图片' if a.is_image else '文件'} · {size_s} · {a.added_time}"))
        except Exception:
            pass
        return hits

    @staticmethod
    def _preview(text: str) -> str:
        text = (text or "").replace("\n", " ").strip()
        return text[:PREVIEW_LEN] + ("..." if len(text) > PREVIEW_LEN else "")

    # ---- 跳转 ----
    def _jump(self, item, _col):
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return  # 组节点不可跳转
        page_idx, keyword = data
        self._jump_page = page_idx
        self._jump_keyword = keyword
        # 修复：此前只 accept() 而没有 emit，主窗口的 _on_search_jump 永远收不到
        # → 双击结果实际不会跳转。这里补发信号（外部同时保留 result_jump 兼容）
        self.jump_requested.emit(page_idx, keyword)
        self.accept()

    # 供外部读取跳转结果
    def result_jump(self):
        return (self._jump_page, self._jump_keyword) if self._jump_page is not None else None
