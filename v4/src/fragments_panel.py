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

import datetime

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QComboBox, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QFrame, QMessageBox, QTextEdit, QSplitter, QStackedWidget,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QRect
from PyQt6.QtGui import QColor, QFontMetrics, QBrush

from src.fragment_manager import TYPE_LABELS
from src.fragment_classifier import (
    CAT_TEXT, CAT_LINK, CAT_CODE, CAT_PATH, CAT_COMMAND,
    CATEGORY_LABELS, CATEGORY_ORDER, CATEGORY_TOKENS,
)
from src.fragment_edit_dialog import FragmentEditDialog
from src.glass_dialog import GlassDialog, flash_button, make_separator
from src.list_windowing import ListWindowing, attach_scroll_loader
from src.merge_preview_dialog import MergePreviewDialog
from src.theme import FALLBACK_ACCENT, get_colors
from src.controls import tune_list_scrolling, SmoothButton, EmptyState, IconButton, PageTitle
from src import day_recall
from src import paste_helper as _paste_helper
from src.constants import (
    DATETIME_DATE_LEN,
    DATETIME_TIME_START,
    DATETIME_TIME_LEN,
    DATETIME_MIN_LEN,
    FRAGMENT_PREVIEW_LEN,
)


# 搜索去抖间隔（毫秒）：避免每敲一个字符就全量过滤 + 重建列表
SEARCH_DEBOUNCE_MS = 250

# 前台窗口记忆刷新间隔（毫秒）：FloatPulse 不在前台时，低频记录"最近
# 一个非本程序的前台窗口"，供「粘回」还原焦点。600ms 足够跟上用户切窗，
# 单次开销仅一次 ctypes 调用（可忽略）。
FOREIGN_FOREGROUND_POLL_MS = 600


# ====================================================================
# 列表项绘制代理：内容截断 + 时间右对齐 + 命中高亮
# ====================================================================
# 条目时间用独立 role 存储，绘制时右对齐固定显示，
# 避免列表被预览面板挤窄后省略号把时间一起吃掉
TIME_ROLE = Qt.ItemDataRole.UserRole + 1

# 条目内容语义类别（link/code/path/command/text）用独立 role 存储，
# 绘制代理据此在条目左侧画类别色条
CAT_ROLE = Qt.ItemDataRole.UserRole + 2

# 条目前景色对应的主题 token（"primary" / "link" / None=用代理默认色）。
# ★ 存 token 而不是 QColor：条目前景色是**创建时取色**烘进 item 的，
#   QSS 覆盖不到；换主题时必须按 token 重新取色（见 apply_theme），
#   否则列表停留在旧主题配色，要切一次页面（触发 refresh）才恢复。
#   token 与色值同源（light/dark 的 primary 与 link 两主题各取对应
#   token 值，见 theme.THEMES；字面量已按铁律收口到 theme 常量），
#   替换掉原先的 theme 三元表达式。
COLOR_TOKEN_ROLE = Qt.ItemDataRole.UserRole + 3

# 类别 → 主题色 token：真相源已上移到 fragment_classifier.CATEGORY_TOKENS
# （小卡片碎片页也要按同一份映射画类别圆点，放两份必然漂移）。
# 这里保留私有别名，让本模块既有引用零改动。
_CATEGORY_TOKENS = CATEGORY_TOKENS


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
        self._cat_colors = {}          # category -> QColor 类别色条

    def set_theme(self, colors: dict):
        """按主题刷新文字色 / 时间色 / 高亮底色 / 类别色条 / 组间分割线

        主色半透明 + 深色文字；类别色条从主题 token 取色
        （映射见 _CATEGORY_TOKENS，缺 token 时回退次级灰）。
        """
        self._base_color = QColor(colors.get("text", "#E4E8EE"))
        self._time_color = QColor(colors.get("text_placeholder", "#98A2AE"))
        bg = QColor(colors.get("primary", FALLBACK_ACCENT))
        if not bg.isValid():
            bg = QColor(FALLBACK_ACCENT)
        bg.setAlpha(85)
        self._hl_bg = bg
        self._line_color = QColor(str(colors.get("line", "#E4E2DB")))
        self._cat_colors = {}
        for cat, token in _CATEGORY_TOKENS.items():
            c = QColor(colors.get(token, ""))
            self._cat_colors[cat] = c if c.isValid() else QColor("#98A2AE")

    def paint(self, painter, option, index):
        time_text = index.data(TIME_ROLE)
        if not time_text:
            # 日期分组行：默认绘制 + 组间分割线（覆盖矩阵第四轮图2 定稿：
            # 每组日期头上方一条全宽细线；分组必然以 row 0 的组头开头，
            # row>0 的组头前面一定是上一组的碎片行 → 逐组画线，首组自然豁免）
            if index.row() > 0:
                painter.save()
                painter.setPen(self._line_color)
                painter.drawLine(option.rect.left(), option.rect.top(),
                                 option.rect.right(), option.rect.top())
                painter.restore()
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
        # 遮挡修复（2026-10-02，实机截图佐证 + 离屏量化）：全局
        # QListWidget::item 纵向 padding(9px) 会把 28px 行的 text_rect 压到
        # 10px —— 字底被裁、类别色条缩成 6px 小方块（离屏实测
        # text_rect.height()==10）。本行的文字/色条/时间全部由本方法绘制，
        # 纵向改吃整行（基线公式自行居中），横向保留 QSS 的左右缩进。
        text_rect = QRect(text_rect.left(), opt.rect.top(),
                          text_rect.width(), opt.rect.height())
        if text_rect.width() <= 2:
            return

        # ---- 左侧类别色条：按内容语义类别着色（分组行无类别不画）----
        cat = index.data(CAT_ROLE)
        bar_color = self._cat_colors.get(cat) if cat else None
        if bar_color is not None:
            painter.save()
            painter.fillRect(
                QRect(text_rect.left(), text_rect.top() + 2,
                      3, text_rect.height() - 4),
                bar_color)
            painter.restore()

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
# 空状态：2026-10-01 起用 controls.EmptyState 通用组件（A3 通用化），
# 自绘图标 + sectionLabel/hintLabel/secondaryBtn 的 QSS 契约原样保留。
# ====================================================================


# ====================================================================
# 右侧内嵌预览面板
# ====================================================================
# 就地编辑自动保存的去抖毫秒数（与笔记面板/临时笔记的自动保存同节奏）
_EDIT_SAVE_MS = 800


class _PreviewPane(QWidget):
    """单击碎片即显示完整内容，且**就地可编辑**（停止输入自动保存）

    v 行为变更（2026-10-03）：原「只读预览 + 编辑按钮 → 玻璃弹窗」改为
    预览区直接编辑——按钮触发的弹窗链路已删（右键菜单「编辑内容」与
    详情弹窗的编辑入口保留）。保存语义：停止输入 800ms 自动写回
    （FragmentManager.update_fragment，内容变化时类别自动重算）；
    切换选中 / 无变化 / 空内容的边界见 _commit_edit。
    """

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

        # 就地编辑：预览区即编辑区
        self._content = QTextEdit()
        self._content.setReadOnly(False)
        pv.addWidget(self._content, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._copy_btn = SmoothButton("复制")
        self._copy_btn.setObjectName("secondaryBtn")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.addWidget(self._copy_btn)
        btns.addStretch()
        pv.addLayout(btns)

        self._copy_btn.clicked.connect(self._on_copy)

        # 自动保存：停止输入 800ms 落盘；切换选中 / 收起预览前先 flush
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_EDIT_SAVE_MS)
        self._save_timer.timeout.connect(self._commit_edit)
        self._content.textChanged.connect(self._on_content_changed)
        self._stack.addWidget(page)

        self.show_fragment(None)

    # ---- 对外 ----
    def show_fragment(self, frag):
        """frag 为 None 时回到占位页；切换前先把未保存的修改落盘"""
        if self._frag is not None and self._save_timer.isActive():
            self._save_timer.stop()
            self._commit_edit()
        self._frag = frag
        if frag is None:
            self._stack.setCurrentIndex(0)
            return
        label = TYPE_LABELS.get(frag.type, "未知")
        self._title.setText(f"{label}  #{frag.fragment_id}")
        meta = f"{frag.created_at or '—'}"
        if frag.source:
            meta += f"　{frag.source}"
        self._meta.setText(meta)
        text = frag.content or ""
        self._stat.setText(f"{len(text)} 字")
        # 文本一致时不重写：就地编辑自动保存后 refresh 会回到这里，
        # 重设 setPlainText 会把用户光标打回开头（连续打长段必踩）
        if self._content.toPlainText() != text:
            self._content.blockSignals(True)
            self._content.setPlainText(text)
            self._content.blockSignals(False)
        self._stack.setCurrentIndex(1)

    # ---- 内部 ----
    def _on_copy(self):
        if self._frag is None:
            return
        self._panel._copy_content(self._frag)
        flash_button(self._copy_btn, "已复制")

    def _on_content_changed(self):
        """就地编辑：字数实时刷新；停止输入 800ms 后自动保存"""
        if self._frag is None:
            return
        self._stat.setText(f"{len(self._content.toPlainText())} 字")
        self._save_timer.start()

    def _commit_edit(self):
        """把预览区当前文本写回碎片（无变化不落盘、不刷列表）"""
        frag = self._frag
        if frag is None:
            return
        text = self._content.toPlainText()
        if text == (frag.content or ""):
            return
        if not text.strip():
            # 空内容 update_fragment 拒收（防空碎片）：原文保留在数据层，
            # 编辑框暂留用户输入，切换/重载时由 show_fragment 复原
            return
        if self._panel._fragment_manager.update_fragment(
                frag.fragment_id, content=text):
            fresh = self._panel._fragment_manager.get_fragment(frag.fragment_id)
            if fresh is not None:
                self._frag = fresh          # category 已随内容重算
            # 左侧行预览文本与类别圆点跟着内容走（保留浏览位置与选中）
            self._panel.refresh(preserve_view=True)


# ====================================================================
# 按天回溯视图（day-recall）
# ====================================================================
# 回溯条目行的角色：存来源标签 / 时间 / ref_id，供 delegate 与双击跳转用
DAY_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 10
DAY_TIME_ROLE = Qt.ItemDataRole.UserRole + 11
DAY_REF_ROLE = Qt.ItemDataRole.UserRole + 12


class _DayRecallView(QWidget):
    """「按天」视图：选一天 → 时序还原当天碎片 + 任务 + 素材 + 专注。

    设计约束（任务卡护栏）：
      · 只列表**活跃天**（``day_recall.active_days``），空闲日不占屏；
      · 直接在四源自身的 ``created_at`` / ``added_time`` 上分组，不建
        第二份索引文件；
      · 全部聚合逻辑在纯逻辑模块 ``src.day_recall``，本类只做渲染与
        事件转发（单测不打 UI 也能覆盖核心规则）；
      · 「把这一堆存为笔记」复用现有 ``NoteManager.add_note`` 通道。
    """

    def __init__(self, panel: "FragmentsPanel"):
        super().__init__()
        self._panel = panel
        self._host = panel._host
        self._groups = []          # 当前 [DayGroup, ...]
        self._build_ui()

    # ---- UI ----
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 顶部：日期选择 + 当天摘要 + 存为笔记
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._day_combo = QComboBox()
        self._day_combo.setMinimumContentsLength(16)
        self._day_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._day_combo.currentIndexChanged.connect(self._on_day_changed)
        bar.addWidget(self._day_combo)

        self._day_summary = QLabel("")
        self._day_summary.setObjectName("hintLabel")
        bar.addWidget(self._day_summary)
        bar.addStretch()

        self._save_note_btn = IconButton("save", text="存为笔记", icon_size=14,
                                         object_name="secondaryBtn")
        self._save_note_btn.setToolTip("把当天的全部条目整理成一条笔记")
        self._save_note_btn.clicked.connect(self._on_save_note)
        bar.addWidget(self._save_note_btn)
        v.addLayout(bar)

        # 当天条目列表（按时间正序）
        self._day_list = QListWidget()
        tune_list_scrolling(self._day_list)
        self._day_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection)
        self._day_list.itemDoubleClicked.connect(self._on_day_item_activated)
        v.addWidget(self._day_list, 1)

        # 空态引导（覆盖层，跟随列表尺寸）
        self._day_empty = EmptyState(
            "calendar", "还没有可回溯的记录",
            "收集碎片、添加任务、拖入素材或完成一次专注后，\n"
            "这里会按天把当天发生过什么还原出来")
        self._day_empty.attach_to(self._day_list)

    # ---- 数据重建 ----
    def rebuild(self):
        """从四源现取数据，重建活跃天索引与当天的条目列表。

        每次进入该视图 / 收到 data_changed 时调用；无缓存、无落盘。
        """
        fragments = self._fragments()
        tasks = self._tasks()
        assets = self._assets()
        sessions = self._pomodoro_sessions()
        self._groups = day_recall.build_day_groups(
            fragments, tasks, assets, sessions)

        prev = self._day_combo.currentData()
        self._day_combo.blockSignals(True)
        self._day_combo.clear()
        for g in self._groups:
            self._day_combo.addItem(
                "%s（%d）" % (day_recall.day_label(g.day), len(g.events)),
                g.day)
        # 尽量保留原先选中的那一天；那天没了就回落到最新一天
        if prev is not None:
            idx = self._day_combo.findData(prev)
            if idx >= 0:
                self._day_combo.setCurrentIndex(idx)
        self._day_combo.blockSignals(False)

        self._refresh_current_day()
        shown = self._day_combo.count()
        self._day_empty.setVisible(shown == 0)
        if shown == 0:
            self._day_summary.setText("")
            self._day_list.clear()
            self._day_empty.setGeometry(self._day_list.rect())
            self._day_empty.raise_()

    def _assets(self):
        """取当前素材列表（管理器由宿主晚绑定注入；缺省给空列表）。"""
        manager = getattr(self._host, "_temp_asset_manager", None)
        if manager is None:
            return []
        try:
            return manager.get_all_assets()
        except Exception:
            return []

    def _fragments(self):
        """取全部碎片（管理器缺省时给空列表，单测替身不炸）。"""
        manager = getattr(self._host, "_fragment_manager", None)
        if manager is None:
            return []
        try:
            return manager.get_all_fragments()
        except Exception:
            return []

    def _tasks(self):
        """取全部任务（管理器缺省时给空列表）。"""
        manager = getattr(self._host, "_task_manager", None)
        if manager is None:
            return []
        try:
            return manager.get_all_tasks()
        except Exception:
            return []

    def _pomodoro_sessions(self):
        """取番茄钟专注记录列表（宿主/悬浮球未提供时给空列表）。

        当前版本番茄钟只按任务累计 ``focus_sessions``（不落逐次记录），
        故此处通常为空 —— 一旦未来悬浮球提供逐次会话列表（带
        ``created_at``），本方法即自动接上，无需改 UI。
        """
        provider = getattr(self._host, "pomodoro_sessions", None)
        if callable(provider):
            try:
                return list(provider())
            except Exception:
                return []
        return []

    # ---- 渲染当天 ----
    def _current_group(self):
        day = self._day_combo.currentData()
        for g in self._groups:
            if g.day == day:
                return g
        return None

    def _refresh_current_day(self):
        group = self._current_group()
        self._day_list.clear()
        if group is None:
            self._day_summary.setText("")
            return
        self._day_summary.setText(group.summary())
        colors = get_colors(self._host.current_theme)
        for e in group.events:
            item = QListWidgetItem(
                self._day_row_text(e))
            item.setData(DAY_SOURCE_ROLE, e.source)
            item.setData(DAY_TIME_ROLE, e.time_text)
            item.setData(DAY_REF_ROLE, e.ref_id)
            tip = ["%s · %s" % (e.source_label, e.time_text or "—")]
            if e.detail:
                tip.append(e.detail)
            item.setToolTip("\n".join(tip))
            item.setForeground(QColor(colors.get("text", "#E4E8EE")))
            item.setSizeHint(QSize(0, 28))
            self._day_list.addItem(item)

    @staticmethod
    def _day_row_text(e) -> str:
        """条目行文案：``HH:MM  [来源] 主文案（细节）``。"""
        t = e.time_text or "--:--"
        suffix = "（%s）" % e.detail if e.detail else ""
        return "  %s   [%s] %s%s" % (t, e.source_label, e.title, suffix)

    # ---- 交互 ----
    def _on_day_changed(self, _idx):
        self._refresh_current_day()

    def _on_day_item_activated(self, item):
        """双击条目 → 能跳的跳回原记录（碎片/任务），其余给提示。"""
        source = item.data(DAY_SOURCE_ROLE)
        ref_id = item.data(DAY_REF_ROLE)
        if source in (day_recall.SOURCE_FRAGMENT,) and ref_id is not None:
            self._panel._show_detail(ref_id)
        elif source in (day_recall.SOURCE_TASK,
                        day_recall.SOURCE_TASK_DONE) and ref_id is not None:
            self._host.show_toast("任务已在「日程任务」页，可按标题查找")
        elif source == day_recall.SOURCE_ASSET:
            self._host.show_toast("素材在「临时素材」页")
        elif source == day_recall.SOURCE_POMODORO:
            self._host.show_toast("专注记录来自番茄钟")

    def _on_save_note(self):
        """把当天全部条目组装成文本，走现有笔记通道存为一条笔记。"""
        group = self._current_group()
        if group is None or len(group) == 0:
            QMessageBox.information(self, "提示", "当天没有可保存的记录。")
            return
        text = day_recall.format_day_text(group)
        title = "%s 回顾" % group.day
        note_id = self._host._note_manager.add_note(text, title=title)
        if note_id:
            self._host.refresh_page("notes")
            self._host.data_changed.emit("note")
            self._host.show_toast("已存为笔记")

    def apply_theme(self):
        """跟主题刷新条目文字色与空态图标。"""
        self._refresh_current_day()
        self._day_empty.apply_theme(getattr(self._host, "current_theme", None))


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
        # 一键粘回（reuse 卡）：纯 ctypes 执行器 + 重复复制计数埋点
        self._paste_helper = _paste_helper.PasteHelper()
        self._reuse_counter = _paste_helper.default_counter()
        # 最近一个"非本程序"的前台窗口句柄（粘回时还原焦点目标）
        self._last_foreign_hwnd = 0
        # 窗口化渲染状态（成熟化 3.6）：行描述符表 + 分块决策状态机
        self._rows = None                  # 行描述符表（refresh 时重建）
        self._row_pos = 0                  # 已建到的描述符下标
        self._windowing = ListWindowing()  # 分块决策（首屏块大小/追加/重置）
        self._building = False             # 重建中标志：屏蔽滚动触发的追加
        self._build_ui()
        self._start_foreground_tracker()

    # ---- UI 构建 ----
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = PageTitle("fragments", "碎片工作台", self._host)
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
        self._frag_filter.addItem("剪贴板文本", "clipboard_text")
        self._frag_filter.addItem("剪贴板路径", "clipboard_path")
        self._frag_filter.addItem("文件拾取",   "file_pickup")
        self._frag_filter.addItem("知识段落",   "knowledge_segment")
        self._frag_filter.currentIndexChanged.connect(
            lambda _i: self.refresh(preserve_view=False))
        toolbar.addWidget(self._frag_filter)

        # ---- 第二筛选轴：内容语义类别（link/code/path/command/text）----
        # 与 _frag_filter（来源渠道 type）互不替代，AND 组合筛选
        self._frag_category = QComboBox()
        self._frag_category.addItem("全部内容", "all")
        labels = {CAT_LINK: "链接", CAT_CODE: "代码", CAT_PATH: "路径",
                  CAT_COMMAND: "命令", CAT_TEXT: "文本"}
        for cat in CATEGORY_ORDER:
            self._frag_category.addItem(labels.get(cat, cat), cat)
        self._frag_category.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._frag_category.setMinimumContentsLength(4)
        self._frag_category.currentIndexChanged.connect(
            lambda _i: self.refresh(preserve_view=False))
        toolbar.addWidget(self._frag_category)

        self._frag_search = QLineEdit()
        self._frag_search.setPlaceholderText("搜索碎片内容...")
        self._frag_search.setClearButtonEnabled(True)
        self._frag_search.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self._frag_search, 1)

        # ---- 视图切换：列表 / 按天（day-recall）----
        # 默认「列表」= 现有行为，切到「按天」才走新分支（护栏 §1）。
        # 用 checkable IconButton 承担（panel 禁裸 QPushButton）。
        # 注：勾选态在 _center_stack 建好之后再设（见下方），否则 toggled
        # 回调会在 stack 存在前触发。
        self._day_btn = IconButton("calendar", text="按天", icon_size=14,
                                   object_name="secondaryBtn", checkable=True)
        self._day_btn.setToolTip("按天回溯：选一天，看当天碎片 / 任务 / 素材 / 专注")
        self._day_btn.toggled.connect(self._on_day_view_toggled)
        toolbar.addWidget(self._day_btn)

        preview_visible = bool(self._host._config.get(
            "fragment_preview_visible", True))
        self._preview_btn = SmoothButton("预览")
        self._preview_btn.setObjectName("secondaryBtn")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setChecked(preview_visible)
        self._preview_btn.setToolTip("显示 / 隐藏右侧内容预览面板")
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.toggled.connect(self._on_preview_toggled)
        toolbar.addWidget(self._preview_btn)

        refresh_btn = IconButton("refresh", text="刷新", icon_size=14,
                                 object_name="secondaryBtn")
        refresh_btn.clicked.connect(lambda: self.refresh(preserve_view=True))
        toolbar.addWidget(refresh_btn)
        v.addLayout(toolbar)

        # ---- 中部：列表 + 内嵌预览（可拖动分隔条）----
        self._frag_list = QListWidget()
        tune_list_scrolling(self._frag_list)  # 丝滑化清单 L3：像素级滚动 + 统一步长
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

        # 窗口化加载：滚动接近底部时追加下一块（≤FULL_THRESHOLD 行全量直建，
        # 本回调里的 windowed 检查会让它直接跳过）
        attach_scroll_loader(self._frag_list, self._extend_list_rows)

        # 列表 + 空态叠放（空态透明背景，列表边框就是容器边框）
        list_holder = QWidget()
        holder_grid = QGridLayout(list_holder)
        holder_grid.setContentsMargins(0, 0, 0, 0)
        holder_grid.addWidget(self._frag_list, 0, 0)

        self._empty_state = EmptyState(
            "fragments", "还没有收集到碎片",
            "复制任意文本、拖入文件，或按 Ctrl+Alt+K 快速捕捉，\n"
            "都会自动收集到这里",
            action_text="清空筛选条件", on_action=self._clear_filters)
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

        # ---- 中部两页：0=列表（现有） / 1=按天回溯（新分支）----
        # 默认停在列表页；两页各自持有控件，切页不动数据、不重建列表。
        self._day_view = _DayRecallView(self)
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(self._splitter)
        self._center_stack.addWidget(self._day_view)
        self._center_stack.setCurrentIndex(0)
        v.addWidget(self._center_stack, 1)

        # 恢复「按天」偏好：stack 已就绪后才设勾选态（配置默认 False =
        # 列表视图，与改动前等价）。设完补建一次当天数据。
        if bool(self._host._config.get("fragment_day_view", False)):
            self._day_btn.setChecked(True)
            self._center_stack.setCurrentIndex(1)
            self._day_view.rebuild()

        # 搜索去抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(
            lambda: self.refresh(preserve_view=False))

        # ---- 底部按钮栏 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        merge_btn = SmoothButton("合并选中")
        merge_btn.clicked.connect(self._on_merge)
        bottom.addWidget(merge_btn)

        copy_btn = SmoothButton("复制选中")
        copy_btn.setObjectName("secondaryBtn")
        copy_btn.clicked.connect(self._on_copy)
        bottom.addWidget(copy_btn)

        # 粘回选中：复制到剪贴板 + 还原焦点 + 发 Ctrl+V（reuse 卡）
        paste_btn = IconButton("copy", text="粘回选中", icon_size=14,
                               object_name="secondaryBtn")
        paste_btn.setToolTip("复制到剪贴板，并自动粘回到你刚才用的窗口")
        paste_btn.clicked.connect(self._on_paste)
        bottom.addWidget(paste_btn)

        bottom.addStretch()

        del_btn = IconButton("trash", text="删除选中", icon_size=14,
                             object_name="dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        bottom.addWidget(del_btn)

        clear_btn = SmoothButton("清空全部")
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

    # ---- 视图切换：列表 / 按天 ----
    def _on_day_view_toggled(self, checked: bool):
        """切到「按天」分支：中部换页到回溯视图并即时重建当天数据。

        列表页的控件与滚动位置原样保留（QStackedWidget 只切可见页），
        切回来不需要重新筛选；只有首次进入 / 数据变更时才重建回溯视图。
        偏好落盘（fragment_day_view），下次启动沿用。
        """
        self._center_stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self._day_view.rebuild()
        config = self._host._config
        if bool(checked) != config.get("fragment_day_view", False):
            config.set("fragment_day_view", bool(checked))
            config.save()

    # ---- 空态 / 筛选 ----
    def _clear_filters(self):
        """一键清空搜索词与两个筛选下拉（空态里的按钮入口）

        两个下拉都要 blockSignals 包住，避免 setCurrentIndex 触发
        currentIndexChanged 造成多次重复 refresh。
        """
        self._frag_search.blockSignals(True)
        self._frag_search.clear()
        self._frag_search.blockSignals(False)
        self._frag_filter.blockSignals(True)
        self._frag_filter.setCurrentIndex(0)
        self._frag_filter.blockSignals(False)
        self._frag_category.blockSignals(True)
        self._frag_category.setCurrentIndex(0)
        self._frag_category.blockSignals(False)
        self.refresh(preserve_view=False)

    def _update_empty_state(self, shown_count: int, has_filter: bool):
        if shown_count > 0:
            self._empty_state.setVisible(False)
            return
        if has_filter:
            self._empty_state.set_state(
                "search", "没有匹配的碎片",
                "类型、内容、关键词三个筛选条件放宽一些，\n"
                "或清空筛选条件查看全部碎片", show_action=True)
        else:
            self._empty_state.set_state(
                "fragments", "还没有收集到碎片",
                "复制任意文本、拖入文件，或按 Ctrl+Alt+K 快速捕捉，\n"
                "都会自动收集到这里", show_action=False)
        # 与列表严格同尺寸（隐藏期间布局不会调整它的几何）
        self._empty_state.setGeometry(self._frag_list.geometry())
        self._empty_state.setVisible(True)
        self._empty_state.raise_()

    # ---- 主题同步（供 host._apply_theme 调用） ----
    def apply_theme(self):
        """换主题时把列表配色就地刷新一遍（不重建数据）。

        必须存在的原因：列表有两处颜色**不经 QSS**——
          1. 绘制代理的字色/时间色/高亮色/类别色条（``_delegate.set_theme``）
          2. 条目前景色（日期分组行=主色、路径行=link 色，创建时烘进 item）
        换主题只重设 QSS 时这两处都还是旧配色：浅色主题下深色字（#E4E8EE）
        落在白底上几乎不可见，用户必须切一次页面（触发 refresh）才恢复。

        只按 token 重取色 + 重绘，不走 refresh()：换主题与数据无关，
        重建列表会白跑一遍筛选/搜索，还会多发一次 data_changed。
        """
        colors = get_colors(self._host.current_theme)
        self._delegate.set_theme(colors)

        lst = self._frag_list
        lst.setUpdatesEnabled(False)
        try:
            for i in range(lst.count()):
                token = lst.item(i).data(COLOR_TOKEN_ROLE)
                if not token:
                    continue          # 普通条目：前景色留空，由代理取 _base_color
                color = QColor(colors.get(token, ""))
                if color.isValid():
                    lst.item(i).setForeground(color)
        finally:
            lst.setUpdatesEnabled(True)
        lst.viewport().update()
        # 空态图标是自绘位图，同样不在 QSS 管辖内（A3）
        self._empty_state.apply_theme(getattr(self._host, "current_theme",
                                              None))
        # 按天视图的条目文字色同样烘进 item，换主题须就地重刷
        self._day_view.apply_theme()

    # ---- 刷新入口（供 host.refresh_page 调用） ----
    def refresh(self, preserve_view: bool = True):
        """刷新碎片列表显示：按日期分组，每条只显示内容+时间(时分)。

        preserve_view=True 时保留滚动位置与选中项 —— 删除/编辑后列表不会
        跳回顶部，可以接着操作下一条；搜索/筛选变化时传 False，结果集
        变化较大，回到顶部更符合预期。

        渲染走窗口化（成熟化 3.6）：≤FULL_THRESHOLD(200) 行全量直建与旧
        实现一致；超出则首屏只建 FIRST_CHUNK(120) 行，滚动接近底部续建。
        """
        ftype = self._frag_filter.currentData()
        cat = self._frag_category.currentData()
        keyword = self._frag_search.text().strip()
        theme = self._host.current_theme
        colors = get_colors(theme)

        # 筛选链：先按来源渠道 type、再按内容语义 category（AND 组合），
        # 最后套搜索关键词（与旧单轴行为保持一致：无关键词时跳过搜索）
        if ftype == "all":
            fragments = self._fragment_manager.get_all_fragments()
        else:
            fragments = self._fragment_manager.get_fragments_by_type(ftype)
        if cat != "all":
            fragments = [f for f in fragments if f.category == cat]
        if keyword:
            kw = keyword.lower()
            fragments = [f for f in fragments
                         if kw in f.content.lower() or kw in f.source.lower()]

        # 记录视图状态（滚动位置 + 选中项）
        scroll_value = self._frag_list.verticalScrollBar().value()
        selected_ids = set(self._get_selected_ids()) if preserve_view else set()

        # 窗口化渲染（成熟化 3.6）：先把碎片序列展开成纯 Python 行描述符表
        # （无 Qt 对象，几百条也只是微秒级），再按块建行——≤FULL_THRESHOLD 行
        # 全量直建（与旧实现完全一致），超出则首屏只建 FIRST_CHUNK 行，
        # 滚动接近底部经 _extend_list_rows 续建
        rows = self._build_row_specs(fragments)

        # 重建期间屏蔽信号：避免 clear() 触发 selectionChanged 把预览面板闪空
        self._building = True
        self._frag_list.blockSignals(True)
        self._frag_list.clear()
        self._rows = rows
        self._row_pos = 0
        try:
            built_ids = set()
            n = self._windowing.reset(len(rows))
            built_ids = self._build_list_rows(n, colors)
            # 保留选中项：被选碎片若落在未建区间，续建到全部包含——
            # 「删除/合并后选中项保持」的语义不因窗口化而缩水
            if preserve_view and selected_ids:
                remaining = selected_ids - built_ids
                while remaining:
                    n = self._windowing.extend()
                    if n == 0:
                        break
                    built_ids |= self._build_list_rows(n, colors)
                    remaining = selected_ids - built_ids
        finally:
            self._building = False
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
                                 bool(keyword) or ftype != "all"
                                 or cat != "all")
        # 正停留在「按天」视图时同步重建（数据变更 → 当天条目跟着变）
        if self._day_btn.isChecked():
            self._day_view.rebuild()
        self._host.data_changed.emit("fragment")

    # ---- 窗口化渲染：行描述符表 / 行工厂 / 分块建行 ----
    def _build_row_specs(self, fragments) -> list:
        """把筛选后的碎片序列展开成行描述符表（纯 Python，不建 Qt 对象）：

        ("header", date_part) 日期分组行 / ("frag", fragment) 碎片行。
        分组规则与旧内联实现一致：日期段变化处插一个组头。
        """
        rows = []
        current_date = None
        for f in fragments:
            created = f.created_at or ""
            date_part = (created[:DATETIME_DATE_LEN]
                         if len(created) >= DATETIME_DATE_LEN else created)
            if date_part != current_date:
                current_date = date_part
                rows.append(("header", date_part))
            rows.append(("frag", f))
        return rows

    @staticmethod
    def _day_label(date_part: str) -> str:
        """分组头的人性化日期（覆盖矩阵第四轮图2 定稿）：
        今天 / 昨天 相对化，其余「M 月 D 日」，跨年补年份。
        解析失败原样返回 —— 数据被手改过也不能炸整个列表。"""
        try:
            day = datetime.date.fromisoformat(date_part)
        except ValueError:
            return date_part
        today = datetime.date.today()
        md = f"{day.month} 月 {day.day} 日"
        if day == today:
            return f"今天 · {md}"
        if (today - day).days == 1:
            return f"昨天 · {md}"
        if day.year != today.year:
            return f"{day.year} 年 {md}"
        return md

    def _make_row_item(self, row, colors):
        """把一条行描述符建成 QListWidgetItem（条目属性与旧内联实现一致）"""
        if row[0] == "header":
            # 组头样式（覆盖矩阵第四轮图2 定稿）：人性化灰字小标（11px、
            # text_placeholder、不加粗），替换原先的 ISO 主色加粗
            date_item = QListWidgetItem(f"  {self._day_label(row[1])}")
            date_item.setData(Qt.ItemDataRole.UserRole, None)
            flags = date_item.flags()
            date_item.setFlags(flags & ~Qt.ItemFlag.ItemIsSelectable
                               & ~Qt.ItemFlag.ItemIsEnabled)
            date_item.setData(COLOR_TOKEN_ROLE, "text_placeholder")
            date_item.setForeground(QColor(colors["text_placeholder"]))
            f_font = date_item.font()
            f_font.setPixelSize(11)
            date_item.setFont(f_font)
            date_item.setSizeHint(QSize(0, 30))
            return date_item

        f = row[1]
        created = f.created_at or ""
        time_part = (created[DATETIME_TIME_START:
                             DATETIME_TIME_START + DATETIME_TIME_LEN]
                     if len(created) >= DATETIME_MIN_LEN
                     else created[DATETIME_TIME_START:])
        preview_text = f.preview(FRAGMENT_PREVIEW_LEN)
        # 置顶条目加文字前缀标记（不用 emoji，避免离屏/精简系统字体缺失）
        if f.pinned:
            preview_text = "★ " + preview_text
        # 内容与时间分开：时间存独立 role，由绘制代理右对齐固定显示
        item = QListWidgetItem(f"   {preview_text}")
        item.setData(Qt.ItemDataRole.UserRole, f.fragment_id)
        item.setData(TIME_ROLE, time_part)
        item.setData(CAT_ROLE, f.category)
        if f.type in ("clipboard_path", "file_pickup"):
            item.setData(COLOR_TOKEN_ROLE, "link")
            item.setForeground(QColor(colors["link"]))
        item.setSizeHint(QSize(0, 28))
        label = TYPE_LABELS.get(f.type, "未知")
        cat_label = CATEGORY_LABELS.get(f.category, f.category)
        tip_lines = [f"类型: {label}", f"类别: {cat_label}"]
        if f.pinned:
            tip_lines.append("已置顶（不参与自动淘汰）")
        if f.source:
            tip_lines.append(f"来源: {f.source}")
        tip_lines.append(f"时间: {created}")
        tip_lines.append(f"内容:\n{f.content}")
        item.setToolTip("\n".join(tip_lines))
        return item

    def _build_list_rows(self, count: int, colors) -> set:
        """把行描述符表里接下来 count 行建成 item 追加进列表。

        返回本次建入的碎片 id 集合（组头行无 id，不进集合），
        供 refresh 的「保留选中项」续建判断使用。
        """
        built_ids = set()
        for _ in range(count):
            row = self._rows[self._row_pos]
            self._row_pos += 1
            self._frag_list.addItem(self._make_row_item(row, colors))
            if row[0] == "frag":
                built_ids.add(row[1].fragment_id)
        return built_ids

    def _extend_list_rows(self):
        """滚动接近底部 → 追加下一块（attach_scroll_loader 的回调入口）。

        重建中 / 未窗口化（小列表全量直建）/ 已建满时直接跳过。
        """
        if self._building or self._rows is None:
            return
        if not self._windowing.windowed:
            return
        n = self._windowing.extend()
        if n > 0:
            self._build_list_rows(n, get_colors(self._host.current_theme))

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
        frag = self._fragment_manager.get_fragment(fid)
        act_pin = menu.addAction("取消置顶" if (frag and frag.pinned)
                                 else "置顶")
        act_detail = menu.addAction("查看详情")
        act_edit = menu.addAction("编辑内容")
        act_copy = menu.addAction("复制内容")
        act_paste = menu.addAction("粘回到刚才的窗口")
        menu.addSeparator()
        act_to_note = menu.addAction("存为笔记")
        act_to_kb = menu.addAction("加入知识库")
        act_to_nav = menu.addAction("添加至网址导航")
        act_to_sticky = menu.addAction("钉为便签")
        act_export = menu.addAction("导出到 Obsidian")
        menu.addSeparator()
        # 手动归类子菜单（自动分类判错时的纠正入口）
        cat_menu = menu.addMenu("归类为")
        cat_actions = {}
        for c in CATEGORY_ORDER:
            act = cat_menu.addAction(CATEGORY_LABELS.get(c, c))
            act.setCheckable(True)
            act.setChecked(self._frag_current_category(fid) == c)
            cat_actions[act] = c
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        action = menu.exec(self._frag_list.mapToGlobal(pos))
        if action == act_pin:
            if self._fragment_manager.toggle_pinned(fid):
                self.refresh(preserve_view=True)
        elif action == act_detail:
            self._show_detail(fid)
        elif action == act_edit:
            self._edit_fragment(fid)
        elif action == act_copy:
            frag = self._fragment_manager.get_fragment(fid)
            if frag:
                self._copy_content(frag)
        elif action == act_paste:
            self._paste_fragment(fid)
        elif action == act_to_note:
            self._to_note(fid)
        elif action == act_to_sticky:
            self._to_sticky(fid)
        elif action == act_export:
            # 导出实现统一在宿主（三个面板共用，避免三份逻辑分叉）
            self._host.export_to_obsidian()
        elif action == act_to_kb:
            self._to_knowledge(fid)
        elif action == act_to_nav:
            self._to_nav(fid)
        elif action in cat_actions:
            if self._fragment_manager.set_category(fid, cat_actions[action]):
                self.refresh(preserve_view=True)
        elif action == act_delete:
            if self._fragment_manager.delete_fragment(fid):
                self.refresh()

    def _frag_current_category(self, fragment_id) -> str:
        """取碎片当前内容语义类别（右键归类菜单勾选态用）"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        return frag.category if frag is not None else ""

    # ---- 复制 / 编辑 ----
    def _copy_content(self, frag):
        """复制单条碎片内容到剪贴板"""
        self._clipboard_monitor.put_text(frag.content)
        self._reuse_counter.record(frag.content)

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

    def _to_note_id(self, fragment_id: int):
        """碎片转存为新笔记的公共路径（存为笔记 / 钉为便签共用），返回 note_id"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if not frag:
            return None
        title_text = frag.content.replace("\n", " ").strip()[:20]
        title = f"{title_text}{'...' if len(frag.content) > 20 else ''}"
        note_id = self._note_manager.add_note(frag.content, title=title)
        self._host.refresh_page("notes")
        self._host.data_changed.emit("note")
        return note_id

    def _to_note(self, fragment_id: int):
        """将碎片转存为一条新笔记"""
        title = self._to_note_id(fragment_id)
        if title is None:
            return
        note = self._note_manager.get_note(title)
        QMessageBox.information(
            self, "已转存",
            f"碎片已存为新笔记：\n{note.title if note else ''}")

    def _to_sticky(self, fragment_id: int):
        """碎片 → 直接钉成桌面便签（锚定碎片本身，内容写回碎片）。

        2026-10-01 用户拍板取消自动收录：钉便签**不再转存新笔记**，
        笔记库不被污染；「存为笔记」仍是显式的独立入口。
        """
        manager = self._host.sticky_manager
        if manager is None:
            QMessageBox.information(self, "提示", "便签功能尚未就绪。")
            return
        ok, reason = manager.open_fragment(fragment_id)
        if ok:
            self._host.show_toast("已钉为桌面便签")
        elif reason == "limit":
            self._host.show_toast(
                f"便签最多同时钉 {manager.MAX_STICKIES} 个，请先关闭一些")
        elif reason == "missing":
            self._host.show_toast("碎片已不存在")
        else:
            QMessageBox.information(self, "提示", "便签功能尚未就绪。")

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
        label = TYPE_LABELS.get(frag.type, "未知")
        card = QFrame()
        card.setObjectName("glassCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        meta_rows = [
            ("类型", label),
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
        content_label = QLabel("完整内容")
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
            ("复制全部内容", "primaryBtn", None),
            ("编辑内容", "secondaryBtn", None, "edit"),
            ("关闭", "secondaryBtn", dlg.accept),
        ])

        def _copy_all():
            self._copy_content(frag)
            flash_button(btns[0], "已复制")

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
        self._reuse_counter.record(text)

    # ---- 一键粘回（reuse 卡）----
    def _start_foreground_tracker(self):
        """低频记录"非本程序的前台窗口"，供「粘回」还原焦点。

        为什么不等点击时再取前台窗口：点击发生在 FloatPulse 面板内，
        此时前台窗口就是本程序自己，取到的目标毫无意义。故必须持续跟
        踪"用户最近一次用的外部窗口"——本程序不在前台时才刷新句柄。
        """
        self._fg_timer = QTimer(self)
        self._fg_timer.setInterval(FOREIGN_FOREGROUND_POLL_MS)
        self._fg_timer.timeout.connect(self._poll_foreign_foreground)
        self._fg_timer.start()

    def _self_window(self):
        """返回本面板所属顶层窗口（无则回退宿主）"""
        win = self.window()
        return win if win is not None else self._host

    def _poll_foreign_foreground(self):
        """本程序不在前台时，把当前前台窗口记为「最近外部窗口」。

        自己在前台（用户正在操作 FloatPulse）时**不覆盖**，否则会把
        目标刷成本程序自身。
        """
        win = self._self_window()
        try:
            if win is not None and win.isActiveWindow():
                return
        except Exception:
            pass
        hwnd = self._paste_helper.capture_foreground()
        if hwnd:
            self._last_foreign_hwnd = hwnd

    def _on_paste(self):
        """粘回选中碎片：复制 → 还原焦点 → Ctrl+V；失败降级为仅复制。"""
        ids = self._get_selected_ids()
        if not ids:
            self._host.show_toast("请先选中一条碎片")
            return
        fragments = self._fragment_manager.get_fragments_by_ids(ids)
        if not fragments:
            return
        text = "\n\n".join(f.content for f in fragments)
        self._paste_text(text)

    def _paste_fragment(self, fragment_id):
        """右键菜单入口：粘回单条碎片内容"""
        frag = self._fragment_manager.get_fragment(fragment_id)
        if frag is None:
            return
        self._paste_text(frag.content)

    def _paste_text(self, text: str):
        """粘回公共路径：复制到剪贴板 + 还原焦点发键，失败降级为仅复制。"""
        # 埋点：同一内容的重复复制计数（产品指标）
        self._reuse_counter.record(text)
        self._clipboard_monitor.put_text(text)

        # 用户关掉了自动粘贴 → 只复制（与旧「复制」等价）
        if not bool(self._host._config.get("fragment_paste_enabled", True)):
            self._host.show_toast("已复制到剪贴板")
            return

        hwnd = self._last_foreign_hwnd
        ok, reason = self._paste_helper.paste_to(hwnd)
        if ok:
            return
        # 降级：内容已在剪贴板，提示用户手动粘贴（不卡住、不抛异常）
        if reason == _paste_helper.REASON_NOT_WINDOWS:
            self._host.show_toast("已复制到剪贴板（当前系统不支持自动粘贴）")
        else:
            self._host.show_toast("已复制到剪贴板，请手动 Ctrl+V 粘贴")

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
