# -*- coding: utf-8 -*-
"""
====================================================================
碎片工作台面板  -  FragmentsPanel
====================================================================
从 main_window.py 抽出的独立面板，承载碎片列表 / 筛选 / 搜索 /
合并 / 转存 / 编辑 / 删除等业务逻辑。

通过构造参数 host（MainWindow）访问业务管理器与跨面板刷新入口，
自身仅持有本面板 UI 控件，降低 main_window 单文件复杂度。

本轮增强（T1/T3/T4/U2/V3）：
  · refresh(preserve_view=True)  刷新保留滚动位置与选中项，列表不再跳回顶部
  · 右侧内嵌预览面板             单击即看全文，免去"右键→详情→关闭"三步
  · 搜索去抖 250ms + 命中高亮    输入不卡，命中的关键词有底色
  · 空状态引导                   无碎片 / 无结果两套文案，不再是一片空白
  · 碎片内容可编辑               右键或预览面板进入玻璃编辑弹窗
====================================================================
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QComboBox, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QFrame, QMessageBox, QTextEdit, QSplitter, QStackedWidget,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QRect
from PyQt6.QtGui import QColor, QFontMetrics, QBrush

from src.fragment_manager import TYPE_LABELS, TYPE_ICONS
from src.fragment_edit_dialog import FragmentEditDialog
from src.glass_dialog import GlassDialog, flash_button, make_separator
from src.merge_preview_dialog import MergePreviewDialog
from src.theme import get_colors
from src.constants import (
    DATETIME_DATE_LEN,
    DATETIME_TIME_START,
    DATETIME_TIME_LEN,
    DATETIME_MIN_LEN,
    FRAGMENT_PREVIEW_LEN,
)


# 搜索去抖间隔（毫秒）：避免每敲一个字符就全量过滤 + 重建列表
SEARCH_DEBOUNCE_MS = 250


# ====================================================================
# 列表项绘制代理：内容截断 + 时间右对齐 + 命中高亮
# ====================================================================
# 条目时间用独立 role 存储，绘制时右对齐固定显示，
# 避免列表被预览面板挤窄后省略号把时间一起吃掉
TIME_ROLE = Qt.ItemDataRole.UserRole + 1


# ====================================================================
class _MatchHighlightDelegate(QStyledItemDelegate):
    """条目绘制：左侧内容（命中处高亮）+ 右侧时间。

    时间用独立 role 存储并右对齐绘制，列表再窄也始终能看到时间；
    内容按剩余宽度用省略号截断。日期分组行没有时间数据，走默认绘制。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyword = ""
        self._base_color = QColor("#E4E8EE")
        self._time_color = QColor("#98A2AE")
        self._hl_bg = QColor(111, 255, 233, 80)
        self._hl_fg = QColor("#0B2B29")

    def set_theme(self, colors: dict):
        """按主题刷新文字色 / 时间色 / 高亮底色（主色半透明 + 深色文字）"""
        self._base_color = QColor(colors.get("text", "#E4E8EE"))
        self._time_color = QColor(colors.get("text_placeholder", "#98A2AE"))
        bg = QColor(colors.get("primary", "#6FFFE9"))
        if not bg.isValid():
            bg = QColor("#6FFFE9")
        bg.setAlpha(85)
        self._hl_bg = bg

    def paint(self, painter, option, index):
        time_text = index.data(TIME_ROLE)
        if not time_text:
            # 日期分组行等：完全交给默认绘制，保持原有观感
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        full_text = str(opt.text)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()

        # 只画背景/选中态：文本由本方法分段绘制
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        if text_rect.width() <= 2:
            return

        # 文字颜色：优先条目自带前景色（路径类的蓝色等）
        color = self._base_color
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg, QBrush) and fg.color().isValid():
            color = fg.color()
        elif isinstance(fg, QColor) and fg.isValid():
            color = fg

        painter.save()
        painter.setClipRect(text_rect)
        painter.setFont(opt.font)
        fm = QFontMetrics(opt.font)
        baseline = (text_rect.top()
                    + (text_rect.height() + fm.ascent() - fm.descent()) // 2)

        # ---- 右侧时间：固定显示，不受内容截断影响 ----
        time_str = str(time_text)
        time_width = fm.horizontalAdvance(time_str)
        right = text_rect.right()
        painter.setPen(self._time_color)
        painter.drawText(
            QRect(right - time_width, text_rect.top(), time_width,
                  text_rect.height()),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            time_str)

        # ---- 左侧内容：命中处高亮，超出可用宽度用省略号截断 ----
        content_right = right - time_width - 10
        kw = self.keyword
        low = full_text.lower()
        needle = kw.lower() if kw else ""
        segments = []
        pos = 0
        if needle:
            while True:
                hit = low.find(needle, pos)
                if hit < 0:
                    segments.append((full_text[pos:], False))
                    break
                if hit > pos:
                    segments.append((full_text[pos:hit], False))
                segments.append((full_text[hit:hit + len(needle)], True))
                pos = hit + len(needle)
        else:
            segments.append((full_text, False))

        x = text_rect.left()
        for seg, is_hit in segments:
            if not seg or x >= content_right:
                continue
            width = fm.horizontalAdvance(seg)
            if x + width > content_right:
                seg = fm.elidedText(seg, Qt.TextElideMode.ElideRight,
                                    content_right - x)
                width = fm.horizontalAdvance(seg)
                is_hit = False
            if is_hit:
                painter.fillRect(
                    QRect(x - 1, baseline - fm.ascent() - 1,
                          width + 2, fm.height()),
                    self._hl_bg)
                painter.setPen(self._hl_fg)
            else:
                painter.setPen(color)
            painter.drawText(x, baseline, seg)
            x += width
        painter.restore()


# ====================================================================
# 空状态（覆盖在列表之上，遮挡列表的空白区域但不遮边框）
# ====================================================================
class _EmptyState(QWidget):
    """列表空态引导：区分「全空」与「筛选无结果」两种场景"""

    def __init__(self, on_clear_filter):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(8)
        v.addStretch()

        self._icon = QLabel("🧩")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet("font-size: 34px;")
        v.addWidget(self._icon)

        self._title = QLabel("")
        self._title.setObjectName("sectionLabel")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._title)

        self._desc = QLabel("")
        self._desc.setObjectName("hintLabel")
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setWordWrap(True)
        v.addWidget(self._desc)

        self._action = QPushButton("清空筛选条件")
        self._action.setObjectName("secondaryBtn")
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.clicked.connect(on_clear_filter)
        self._action.setVisible(False)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._action)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()

    def set_mode(self, mode: str):
        """mode: 'empty'（池子为空） / 'no_result'（筛选或搜索无命中）"""
        if mode == "empty":
            self._icon.setText("🧩")
            self._title.setText("还没有收集到碎片")
            self._desc.setText(
                "复制任意文本、拖入文件，或按 Ctrl+Alt+K 快速捕捉，\n"
                "都会自动收集到这里")
            self._action.setVisible(False)
        else:
            self._icon.setText("🔍")
            self._title.setText("没有匹配的碎片")
            self._desc.setText("换个关键词试试，或清空筛选条件查看全部碎片")
            self._action.setVisible(True)


# ====================================================================
# 右侧内嵌预览面板
# ====================================================================
class _PreviewPane(QWidget):
    """单击碎片即显示完整内容（免去右键 → 详情 → 关闭三步）"""

    def __init__(self, panel: "FragmentsPanel"):
        super().__init__()
        self._panel = panel
        self._frag = None

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 4, 0)
        v.setSpacing(0)
        self._stack = QStackedWidget()
        v.addWidget(self._stack)

        # --- 占位页 ---
        placeholder = QLabel("选中左侧任意一条碎片\n这里会显示完整内容")
        placeholder.setObjectName("hintLabel")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        self._stack.addWidget(placeholder)

        # --- 内容页 ---
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 10, 0)
        pv.setSpacing(8)

        self._title = QLabel("")
        self._title.setObjectName("sectionLabel")
        self._meta = QLabel("")
        self._meta.setObjectName("hintLabel")
        self._meta.setWordWrap(True)

        head = QHBoxLayout()
        head.addWidget(self._title)
        head.addStretch()
        self._stat = QLabel("")
        self._stat.setObjectName("hintLabel")
        head.addWidget(self._stat)

        pv.addLayout(head)
        pv.addWidget(self._meta)
        pv.addWidget(make_separator())

        self._content = QTextEdit()
        self._content.setReadOnly(True)
        pv.addWidget(self._content, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._copy_btn = QPushButton("📋 复制")
        self._copy_btn.setObjectName("secondaryBtn")
        self._edit_btn = QPushButton("✏️ 编辑")
        self._edit_btn.setObjectName("secondaryBtn")
        for b in (self._copy_btn, self._edit_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(b)
        btns.addStretch()
        pv.addLayout(btns)

        self._copy_btn.clicked.connect(self._on_copy)
        self._edit_btn.clicked.connect(self._on_edit)
        self._stack.addWidget(page)

        self.show_fragment(None)

    # ---- 对外 ----
    def show_fragment(self, frag):
        """frag 为 None 时回到占位页"""
        self._frag = frag
        if frag is None:
            self._stack.setCurrentIndex(0)
            return
        icon = TYPE_ICONS.get(frag.type, "📄")
        label = TYPE_LABELS.get(frag.type, "未知")
        self._title.setText(f"{icon} {label}  #{frag.fragment_id}")
        meta = f"🕐 {frag.created_at or '—'}"
        if frag.source:
            meta += f"　🔗 {frag.source}"
        self._meta.setText(meta)
        text = frag.content or ""
        self._stat.setText(f"{len(text)} 字")
        self._content.setPlainText(text)
        self._stack.setCurrentIndex(1)

    # ---- 内部 ----
    def _on_copy(self):
        if self._frag is None:
            return
        self._panel._copy_content(self._frag)
        flash_button(self._copy_btn, "✅ 已复制")

    def _on_edit(self):
        if self._frag is None:
            return
        self._panel._edit_fragment(self._frag.fragment_id)


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

        # ---- 工具栏：筛选 + 搜索 + 预览开关 + 刷新 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._frag_filter = QComboBox()
        self._frag_filter.addItem("全部类型", "all")
        self._frag_filter.addItem("📋 剪贴板文本", "clipboard_text")
        self._frag_filter.addItem("📁 剪贴板路径", "clipboard_path")
        self._frag_filter.addItem("📥 文件拾取",   "file_pickup")
        self._frag_filter.addItem("📚 知识段落",   "knowledge_segment")
        self._frag_filter.currentIndexChanged.connect(
            lambda _i: self.refresh(preserve_view=False))
        toolbar.addWidget(self._frag_filter)

        self._frag_search = QLineEdit()
        self._frag_search.setPlaceholderText("🔍 搜索碎片内容...")
        self._frag_search.setClearButtonEnabled(True)
        self._frag_search.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self._frag_search, 1)

        preview_visible = bool(self._host._config.get(
            "fragment_preview_visible", True))
        self._preview_btn = QPushButton("👁 预览")
        self._preview_btn.setObjectName("secondaryBtn")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setChecked(preview_visible)
        self._preview_btn.setToolTip("显示 / 隐藏右侧内容预览面板")
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.toggled.connect(self._on_preview_toggled)
        toolbar.addWidget(self._preview_btn)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.clicked.connect(lambda: self.refresh(preserve_view=True))
        toolbar.addWidget(refresh_btn)
        v.addLayout(toolbar)

        # ---- 中部：列表 + 内嵌预览（可拖动分隔条）----
        self._frag_list = QListWidget()
        self._frag_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._frag_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._frag_list.customContextMenuRequested.connect(self._on_context_menu)
        self._frag_list.itemDoubleClicked.connect(self._on_double_click)
        self._frag_list.itemSelectionChanged.connect(self._sync_preview)
        # 预览面板会挤窄列表 → 长文本若允许横向滚动，底部会多出一条横条。
        # 关掉横向滚动，长文本按右侧省略号截断（全文有 tooltip 与右侧预览兜底）。
        self._frag_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frag_list.setTextElideMode(Qt.TextElideMode.ElideRight)

        self._delegate = _MatchHighlightDelegate(self._frag_list)
        self._frag_list.setItemDelegate(self._delegate)

        # 列表 + 空态叠放（空态透明背景，列表边框就是容器边框）
        list_holder = QWidget()
        holder_grid = QGridLayout(list_holder)
        holder_grid.setContentsMargins(0, 0, 0, 0)
        holder_grid.addWidget(self._frag_list, 0, 0)

        self._empty_state = _EmptyState(self._clear_filters)
        self._empty_state.setVisible(False)
        holder_grid.addWidget(self._empty_state, 0, 0)

        self._preview = _PreviewPane(self)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(1)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(list_holder)
        self._splitter.addWidget(self._preview)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([620, 330])
        self._preview.setVisible(preview_visible)
        v.addWidget(self._splitter, 1)

        # 搜索去抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(
            lambda: self.refresh(preserve_view=False))

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

    # ---- 搜索去抖 ----
    def _on_search_text_changed(self, _text: str):
        """输入时只重启定时器，停手 250ms 后才真正过滤（避免逐字符全量重建）"""
        self._search_timer.start()

    def apply_external_keyword(self, keyword: str):
        """外部（全局搜索跳转）带入关键词：立即过滤，不等去抖"""
        self._frag_search.setText(keyword)
        self._search_timer.stop()
        self.refresh(preserve_view=False)

    # ---- 预览开关 ----
    def _on_preview_toggled(self, checked: bool):
        self._preview.setVisible(bool(checked))
        config = self._host._config
        if bool(checked) != config.get("fragment_preview_visible", True):
            config.set("fragment_preview_visible", bool(checked))
            config.save()

    # ---- 空态 / 筛选 ----
    def _clear_filters(self):
        """一键清空搜索词与类型筛选（空态里的按钮入口）"""
        self._frag_search.blockSignals(True)
        self._frag_search.clear()
        self._frag_search.blockSignals(False)
        self._frag_filter.blockSignals(True)
        self._frag_filter.setCurrentIndex(0)
        self._frag_filter.blockSignals(False)
        self.refresh(preserve_view=False)

    def _update_empty_state(self, shown_count: int, has_filter: bool):
        if shown_count > 0:
            self._empty_state.setVisible(False)
            return
        self._empty_state.set_mode("no_result" if has_filter else "empty")
        # 与列表严格同尺寸（隐藏期间布局不会调整它的几何）
        self._empty_state.setGeometry(self._frag_list.geometry())
        self._empty_state.setVisible(True)
        self._empty_state.raise_()

    # ---- 刷新入口（供 host.refresh_page 调用） ----
    def refresh(self, preserve_view: bool = True):
        """刷新碎片列表显示：按日期分组，每条只显示内容+时间(时分)。

        preserve_view=True 时保留滚动位置与选中项 —— 删除/编辑后列表不会
        跳回顶部，可以接着操作下一条；搜索/筛选变化时传 False，结果集
        变化较大，回到顶部更符合预期。
        """
        ftype = self._frag_filter.currentData()
        keyword = self._frag_search.text().strip()
        theme = self._host.current_theme
        colors = get_colors(theme)

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

        # 记录视图状态（滚动位置 + 选中项）
        scroll_value = self._frag_list.verticalScrollBar().value()
        selected_ids = set(self._get_selected_ids()) if preserve_view else set()

        # 重建期间屏蔽信号：避免 clear() 触发 selectionChanged 把预览面板闪空
        self._frag_list.blockSignals(True)
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
            # 内容与时间分开：时间存独立 role，由绘制代理右对齐固定显示
            item = QListWidgetItem(f"   {preview_text}")
            item.setData(Qt.ItemDataRole.UserRole, f.fragment_id)
            item.setData(TIME_ROLE, time_part)
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

        self._frag_list.blockSignals(False)

        # 恢复选中项（仅仍存在的条目）
        if selected_ids:
            for i in range(self._frag_list.count()):
                entry = self._frag_list.item(i)
                if entry.data(Qt.ItemDataRole.UserRole) in selected_ids:
                    entry.setSelected(True)
        # 恢复滚动位置（不保留视图时回到顶部）
        bar = self._frag_list.verticalScrollBar()
        bar.setValue(min(scroll_value, bar.maximum()) if preserve_view else 0)

        # 高亮代理同步关键词与主题
        self._delegate.keyword = keyword
        self._delegate.set_theme(colors)
        self._frag_list.viewport().update()

        # 预览面板同步（重建期间信号被屏蔽，这里手动同步一次）
        self._sync_preview()

        total = self._fragment_manager.count()
        self._frag_count_label.setText(f"显示 {len(fragments)} 条 / 共 {total} 条")
        self._update_empty_state(len(fragments),
                                 bool(keyword) or ftype != "all")
        self._host.data_changed.emit("fragment")

    # ---- 预览同步 ----
    def _sync_preview(self):
        """把当前条目推给右侧预览面板（无选中 → 占位页）"""
        item = self._frag_list.currentItem()
        fid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if fid is None:
            # currentItem 可能是日期分组行，退化为取第一条选中的真实碎片
            ids = self._get_selected_ids()
            fid = ids[0] if ids else None
        frag = self._fragment_manager.get_fragment(fid) if fid is not None else None
        self._preview.show_fragment(frag)

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
        act_edit = menu.addAction("✏️ 编辑内容")
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
        elif action == act_edit:
            self._edit_fragment(fid)
        elif action == act_copy:
            frag = self._fragment_manager.get_fragment(fid)
            if frag:
                self._copy_content(frag)
        elif action == act_to_note:
            self._to_note(fid)
        elif action == act_to_kb:
            self._to_knowledge(fid)
        elif action == act_to_nav:
            self._to_nav(fid)
        elif action == act_delete:
            if self._fragment_manager.delete_fragment(fid):
                self.refresh()

    # ---- 复制 / 编辑 ----
    def _copy_content(self, frag):
        """复制单条碎片内容到剪贴板"""
        self._clipboard_monitor.put_text(frag.content)

    def _edit_fragment(self, fragment_id):
        """编辑碎片内容（保存后刷新列表与预览，保留浏览位置）"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return

        def _save(text: str) -> bool:
            return self._fragment_manager.update_fragment(fragment_id,
                                                          content=text)

        dlg = FragmentEditDialog(frag, _save, host=self._host, parent=self)
        dlg.exec()
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
        """碎片详情对话框：与主窗口同款玻璃风格（无边框 + 自绘标题栏 + 阴影）"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return

        dlg = GlassDialog(self._host, title="碎片详情",
                          subtitle=f"#{frag.fragment_id}", size=(620, 520))
        body = dlg.body_layout

        # ---- 元信息卡片（标签/值两列对齐）----
        icon = TYPE_ICONS.get(frag.type, "📄")
        label = TYPE_LABELS.get(frag.type, "未知")
        card = QFrame()
        card.setObjectName("glassCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        meta_rows = [
            ("类型", f"{icon} {label}"),
            ("时间", frag.created_at or "—"),
        ]
        if frag.source:
            meta_rows.append(("来源", frag.source))

        for r, (key, value) in enumerate(meta_rows):
            k_label = QLabel(key)
            k_label.setObjectName("hintLabel")
            k_label.setFixedWidth(32)
            v_label = QLabel(value)
            v_label.setWordWrap(True)
            v_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(k_label, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(v_label, r, 1)
        grid.setColumnStretch(1, 1)
        body.addWidget(card)

        # ---- 内容标题行：标题 + 字数统计 ----
        head = QHBoxLayout()
        content_label = QLabel("📄 完整内容")
        content_label.setObjectName("sectionLabel")
        head.addWidget(content_label)
        head.addStretch()
        text = frag.content or ""
        stat = QLabel(f"{len(text)} 字 · {text.count(chr(10)) + 1} 行")
        stat.setObjectName("hintLabel")
        head.addWidget(stat)
        body.addLayout(head)

        body.addWidget(make_separator())

        # ---- 内容区 ----
        content_edit = QTextEdit()
        content_edit.setReadOnly(True)
        content_edit.setPlainText(text)
        body.addWidget(content_edit, 1)

        # ---- 底部按钮 ----
        btns = dlg.add_footer([
            ("📋 复制全部内容", "primaryBtn", None),
            ("✏️ 编辑内容", "secondaryBtn", None),
            ("关闭", "secondaryBtn", dlg.accept),
        ])

        def _copy_all():
            self._copy_content(frag)
            flash_button(btns[0], "✅ 已复制")

        def _edit_from_detail():
            # 先关详情，再经事件循环空闲时机开编辑窗，避免模态嵌套
            dlg.accept()
            QTimer.singleShot(0, lambda: self._edit_fragment(frag.fragment_id))

        btns[0].clicked.connect(_copy_all)
        btns[1].clicked.connect(_edit_from_detail)
        dlg.exec()

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
                                    self._clipboard_monitor,
                                    host=self._host, parent=self)
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
