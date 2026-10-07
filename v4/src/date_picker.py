# -*- coding: utf-8 -*-
"""
====================================================================
日期选择器弹层与日期框  -  date_picker
====================================================================
把「日程任务」页的截止日期控件从原生 ``QDateEdit`` 的日历弹层，换成
**自绘弹层**，外观对齐 Chromium 原生 date picker（用户提供的截图）。

为什么不继续用原生 QCalendarWidget
==================================
三条硬约束，QSS 一条都改不出来：

  1. **跨月补位日必须显示**（截图里 9/28~9/30 与 11/1~11/8 以浅灰出现）
     —— ``QCalendarWidget`` 的补位格恒为空白，没有开关可开；
  2. 导航区结构不同：截图是「2026年10月▼ + ↑↓ 两个箭头」；
     ``QCalendarWidget`` 是「◀ 2026年10月 ▶ + 年份 spinbox」，这些都是
     它的内部控件，即便用 ``findChild`` 抓出来隐藏，布局仍会留洞；
  3. 底部「清除 / 今天」页脚 —— ``QCalendarWidget`` 没有页脚区，只能往
     它的内部 layout 里塞控件，那是与私有结构的强耦合。

因此弹层整体自绘。``DateField`` 仍继承 ``QDateEdit``（保留 Qt 的焦点态、
QSS 外框、日期值语义、``dateChanged`` 信号），只把弹层换成自己的。

与截图的**两处有意偏离**（记录在案，免得后人当缺陷改回去）
==========================================================
  · 选中日 / 今日 / 页脚文字的强调色用**截图里的蓝**（浅 ``#1A73E8``、
    深 ``#8AB4F8``，即 Chromium 两套主题的实际取值），**不改成产品主色
    绿** —— 用户明确选择「照抄截图蓝色」；
  · 月份选择面板里仍保留底部「清除 / 今天」页脚（Chromium 会隐藏它）。
    保留的理由：本控件整体高度固定，隐藏页脚会在底部留出一块空白。

「无日期」空值
==============
``QDateEdit`` 本身不存在空值，而页脚的「清除」按用户拍板要落成**无日期**
（任务进「无日期」分组）。做法是最小值哨兵 + 特殊值文案：
``setMinimumDate(SENTINEL)`` + ``setSpecialValueText("选择日期")``，
值等于哨兵时控件自动显示占位文案。消费方改用 :meth:`DateField.dateOrNone`。

接口
====
``CalendarPopup``  自绘弹层：``picked(QDate)`` / ``cleared()`` 两个信号，
                   :meth:`hit_test` 暴露「坐标 → 控件」映射供单测穷举。
``DateField``      ``QDateEdit`` 子类，见上方「无日期」段。
====================================================================
"""

from PyQt6.QtCore import (
    QDate, QEvent, QRect, QRectF, QSize,
    Qt, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QDateEdit, QToolButton, QWidget,
)

from src import icon_render, motion
from src.constants import DEFAULT_THEME, FS_SM, FS_XS, RADIUS_CTL, RADIUS_PANEL
from src.date_grid import (
    COLS, DAYS_IN_GRID, MONTH_LABELS, ROWS, WEEKDAY_LABELS,
    add_months, month_cells, month_title, place_popup,
)
from src.glass import draw_soft_shadow
from src.theme import get_colors


# ====================================================================
# 视觉尺寸（逻辑像素；全部写死在这里，widget 层不再散落魔数）
# ====================================================================
POPUP_W = 296                 # 面板宽度（不含阴影透明边距）
SHADOW = 8                    # 面板四周留给柔和阴影的透明边距
HEADER_H = 34                 # 月标题 + 翻月箭头
WEEKDAY_H = 24                # 一 二 三 … 表头
CELL_W = 40                   # 日期格宽（7 × 40 = 280 = POPUP_W - 2×8）
CELL_H = 30                   # 日期格高（6 × 30 = 180）
FOOTER_H = 40                 # 清除 / 今天
GRID_TOP = HEADER_H + WEEKDAY_H
POPUP_H = GRID_TOP + ROWS * CELL_H + FOOTER_H      # 278

# 日期格里的"选中块"比格子小一圈，才有截图里那种悬浮感
BLOCK_W = 30
BLOCK_H = 26
BLOCK_R = RADIUS_CTL

# 导航按钮（↑↓ / ←→）与页脚按钮的点击热区
NAV_BTN = 28
NAV_BTN_TOP = (HEADER_H - NAV_BTN) // 2
NAV_BTN_GAP = 4
NAV_BTN_MARGIN = 10
MONTH_COLS = 4
MONTH_W = (POPUP_W - 2 * 8) // MONTH_COLS      # 70
MONTH_H = (ROWS * CELL_H) // 3                 # 60

# 页脚两块热区宽度
FOOT_BTN_W = 64

# 「无日期」哨兵与占位文案（DateField 用）
SENTINEL_DATE = QDate(1900, 1, 1)
EMPTY_TEXT = "选择日期"


def _blend(color, alpha: float):
    """把颜色按 alpha 压暗/透明（画 1px 描边与阴影环用）。"""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


# ====================================================================
# 自绘弹层
# ====================================================================
class CalendarPopup(QWidget):
    """自绘日历弹层（Chromium 原生 date picker 外观）。

    两种视图共用一个窗口、同尺寸，切换时**不重建控件**：
      ``day``   6×7 日期网格（含上下月补位）
      ``month`` 4×3 月份网格 + 年份切换（点月标题右侧的 ▼ 进入）

    用 ``Qt.Popup`` 顶层窗口，点击弹层外部自动关闭、不抢主窗口焦点。
    「外点关闭」由两层机制共同保证（缺一不可，见各处注释）：

      1. ``Qt.Popup`` 自带的窗口系统级关闭（正常路径）+ ``WA_NoMouseReplay``
         —— 关掉 Qt 的「外点重放」：否则关弹层的那一下点击会重放给下方
         控件，点在 DateField 上就变成「关闭 → 立刻重开」，表现为永远关
         不掉，重放的点击还可能误触底层按钮；
      2. :meth:`eventFilter` 应用级兜底（弹层可见期间挂在 QApplication 上）
         —— 任何落在弹层与锚点之外的按下都直接关闭。窗口系统那层若因
         透明分层窗口等平台差异没拦住，这层保证弹层绝不漏关。
    """

    picked = pyqtSignal(QDate)      # 选中某天（今天 / 点日期格都会发）
    cleared = pyqtSignal()          # 点了「清除」→ 调用方落成「无日期」

    def __init__(self, parent=None, theme=DEFAULT_THEME):
        super().__init__(parent, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self._theme = theme
        self._colors = get_colors(theme)
        self._mode = "day"
        self._year = 2026
        self._month = 10
        self._selected = None            # (y, m, d) 或 None
        self._today = None               # (y, m, d) 或 None
        self._hover = ("none",)          # 与 hit_test 同构
        self._hover_prog = {}            # V7：hit 键 → hover 淡染进度 0..1
        self._hover_anims = {}           # hit 键 → QVariantAnimation（懒建）
        self._cells = ()
        self._anchor = None              # 打开本弹层的输入框（外点判定豁免它）

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 关掉外点重放（缺省是开的）：关弹层的那一下点击不再投递给下层控件。
        # 没有这行，点输入框关弹层会立刻被重放的 press 重新打开。
        # ★注意：这是**一次性**开关 —— 实测一次「关闭弹层的点击」会让 Qt 把
        # 它复位为 False，所以每次打开前必须在 showEvent 里重新装填。
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.setFixedSize(POPUP_W + 2 * SHADOW, POPUP_H + 2 * SHADOW)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("calendarPopup")
        self._sync_cells()
        self._sync_today()

    # ----------------------------------------------------------------
    # 状态
    # ----------------------------------------------------------------
    def _sync_today(self):
        d = QDate.currentDate()
        self._today = (d.year(), d.month(), d.day())

    def _sync_cells(self):
        self._cells = month_cells(self._year, self._month)
        self._cleanup_hover_anims()   # V7：格子索引重排，淡染进度即清

    def set_theme(self, theme_name: str):
        """换主题：只换色板并重绘（不重建窗口 → 不闪、不丢焦点）。"""
        self._theme = theme_name if theme_name in ("light", "dark") else DEFAULT_THEME
        self._colors = get_colors(self._theme)
        self.update()

    def _c(self, key: str, fallback: str = "#000000") -> QColor:
        return QColor(self._colors.get(key, fallback))

    def configure(self, selected=None, year=None, month=None):
        """打开前同步状态：选中日 ``(y, m, d)`` / ``None``，以及显示的年月。"""
        self._sync_today()
        if year is None or month is None:
            base = selected or self._today
            year, month = base[0], base[1]
        self._year, self._month = int(year), int(month)
        self._selected = tuple(selected)[:3] if selected else None
        self._mode = "day"
        self._hover = ("none",)
        self._sync_cells()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def shown_month(self):
        return self._year, self._month

    @property
    def cells(self):
        return self._cells

    # ----------------------------------------------------------------
    # 布局：全部以"面板左上角"为原点，再加阴影边距
    # ----------------------------------------------------------------
    @staticmethod
    def _shift(rel: QRect) -> QRect:
        return rel.translated(SHADOW, SHADOW)

    def _nav_rect(self, index: int) -> QRect:
        """右上角两个导航钮（0 = 第一个，1 = 第二个）。"""
        right = POPUP_W - NAV_BTN_MARGIN
        x = right - NAV_BTN * (2 - index) - NAV_BTN_GAP * (1 - index)
        return QRect(x, NAV_BTN_TOP, NAV_BTN, NAV_BTN)

    def _title_rect(self) -> QRect:
        return QRect(12, 0, 140, HEADER_H)

    def _footer_rect(self) -> QRect:
        return QRect(0, POPUP_H - FOOTER_H, POPUP_W, FOOTER_H)

    def _clear_rect(self) -> QRect:
        f = self._footer_rect()
        return QRect(f.x() + 12, f.y(), FOOT_BTN_W, FOOTER_H)

    def _today_rect(self) -> QRect:
        f = self._footer_rect()
        return QRect(f.right() - 12 - FOOT_BTN_W, f.y(), FOOT_BTN_W, FOOTER_H)

    def _cell_rect(self, index: int) -> QRect:
        col, row = index % COLS, index // COLS
        return QRect(8 + col * CELL_W, GRID_TOP + row * CELL_H, CELL_W, CELL_H)

    def _month_rect(self, index: int) -> QRect:
        col, row = index % MONTH_COLS, index // MONTH_COLS
        return QRect(8 + col * MONTH_W, GRID_TOP + row * MONTH_H, MONTH_W, MONTH_H)

    def hit_test(self, pos):
        """坐标（弹层窗口坐标）→ 控件描述元组。

        描述元组同时给鼠标事件与单测使用，是「画在哪 = 点在哪」的唯一
        口径：改布局只需要改这里的分区，绘制与命中不会各写一份而错位。
        入参接受 ``QPoint``（事件给的对象）或 ``(x, y)`` 二元组（测试用）。
        """
        x, y = (pos.x(), pos.y()) if hasattr(pos, "x") else (pos[0], pos[1])
        if self._mode == "month":
            if self._shift(self._title_rect()).contains(x, y):
                return ("title",)
            if self._shift(self._nav_rect(0)).contains(x, y):
                return ("year_prev",)
            if self._shift(self._nav_rect(1)).contains(x, y):
                return ("year_next",)
            for i in range(len(MONTH_LABELS)):
                if self._shift(self._month_rect(i)).contains(x, y):
                    return ("month", i + 1)
        else:
            if self._shift(self._title_rect()).contains(x, y):
                return ("title",)
            if self._shift(self._nav_rect(0)).contains(x, y):
                return ("prev_month",)
            if self._shift(self._nav_rect(1)).contains(x, y):
                return ("next_month",)
            for i in range(DAYS_IN_GRID):
                if self._shift(self._cell_rect(i)).contains(x, y):
                    return ("day", i)
        if self._shift(self._clear_rect()).contains(x, y):
            return ("clear",)
        if self._shift(self._today_rect()).contains(x, y):
            return ("today",)
        return ("none",)

    # ----------------------------------------------------------------
    # 交互
    # ----------------------------------------------------------------
    def set_anchor(self, widget):
        """记录锚点控件（打开本弹层的输入框），外点关闭判定要豁免它。"""
        self._anchor = widget

    def show_at(self, anchor, screen):
        """按锚点（输入框全局 ``(x, y, w, h)``）定位并显示。

        ``date_grid.place_popup`` 只算"面板该落在哪"，而本窗口比面板四周各
        多 ``SHADOW`` 像素，所以窗口位置要往回退同样多。
        """
        x, y = place_popup(anchor, (POPUP_W, POPUP_H), screen)
        self.move(x - SHADOW, y - SHADOW)
        self.show()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self.update()

    # ----------------------------------------------------------------
    # 外点关闭（第二层：应用级兜底）
    # ----------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        # 重新装填一次性开关（见 __init__ 注释）：上一次"关闭弹层的点击"
        # 会把它用掉，若不补，第二次打开后点输入框就关不掉了（抖动回归）。
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)     # 同一过滤器装两次会被调两次，先卸再装
            app.installEventFilter(self)

    def hideEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        """弹层可见期间兜底：任何落在弹层与锚点之外的按下 → 关闭弹层。

        正常路径下 ``Qt.Popup`` 在窗口系统层就会拦下外点（且
        ``WA_NoMouseReplay`` 保证不重放），根本走不到这里；这层防的是
        透明分层窗口等平台差异导致窗口系统层漏拦 —— 宁可两层重叠，
        不能一处漏关。不消费事件（返回 False）：真走到兜底时，让这次
        按下按原路由继续走，不做额外裁决。
        """
        if event.type() == QEvent.Type.MouseButtonPress and self.isVisible():
            if isinstance(obj, QWidget):
                if obj is self or self.isAncestorOf(obj):
                    return False            # 弹层自己层级内的按下，自己处理
                anchor = self._anchor
                if anchor is not None and (obj is anchor
                                           or anchor.isAncestorOf(obj)):
                    return False            # 锚点自己负责 toggle（开→关）
                self.close()
        return False

    def mouseMoveEvent(self, event):
        hit = self.hit_test(event.pos())
        if hit != self._hover:
            old = self._hover
            self._hover = hit
            self._glide_cell_hover(old, 0.0)
            self._glide_cell_hover(hit, 1.0)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != ("none",):
            old = self._hover
            self._hover = ("none",)
            self._glide_cell_hover(old, 0.0)
            self.update()
        super().leaveEvent(event)

    # ---- V7（2026-10-07 交互视觉清单）：格子 hover 淡染 120ms 插值 ----
    # 现状是 cal_hover_bg 一帧直画；改为每格独立 hover 进度（hit 键为
    # 索引），旧格回落、新格抬升并行。仅 day/month 格子参与淡染；导航钮
    # 与页脚保持瞬时（范围外，维持原状）。rest（进度 0）不画底 → 与改动
    # 前 rest 渲染逐字节一致；端点（进度 1）= cal_hover_bg 全值 → 端点
    # 不漂移。reduce_motion / 档位归零经 motion 直接瞬显。
    _HOVER_CELL_TAGS = ("day", "month")

    def _glide_cell_hover(self, hit, target: float):
        """把 hit 格子的 hover 进度插值到 target；非格子命中忽略。

        每键一条独立 QVariantAnimation（懒建、可打断重定向），键经闭包
        绑定写进 _hover_prog；reduce_motion（ms=0）直接落位。
        """
        if hit[0] not in self._HOVER_CELL_TAGS:
            return
        key = hit
        current = self._hover_prog.get(key, 0.0)
        if abs(target - current) <= 0.004:
            return
        ms = motion.eased_ms("fast", 1.0)
        if ms <= 0:
            anim = self._hover_anims.get(key)
            if anim is not None:
                anim.stop()
            if target <= 0.0:
                self._hover_prog.pop(key, None)
            else:
                self._hover_prog[key] = float(target)
            self.update()
            return
        anim = self._hover_anims.get(key)
        if anim is None:
            anim = QVariantAnimation(self)
            anim.valueChanged.connect(
                lambda v, k=key: self._set_cell_hover(k, float(v)))
            self._hover_anims[key] = anim
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    def _set_cell_hover(self, key, value: float):
        value = max(0.0, min(1.0, value))
        if value <= 0.004 and self._hover != key:
            self._hover_prog.pop(key, None)   # 回落干净即出表（不残留）
        else:
            self._hover_prog[key] = value
        self.update()

    def _cleanup_hover_anims(self):
        """换月 / 换视图 / 关闭重开时清空进度与动画（格子索引会重排）。"""
        for anim in self._hover_anims.values():
            anim.stop()
        self._hover_anims.clear()
        self._hover_prog.clear()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        # 四周 8px 透明阴影边距在弹层窗口内、面板外 —— 用户视角就是
        # 「弹层旁边」，点这里应关闭，而不是被 hit_test=("none") 无声吞掉。
        # （窗口系统层把这类点击算作"点在弹层内"，不会触发外点关闭。）
        if not QRectF(SHADOW, SHADOW, POPUP_W, POPUP_H).contains(event.position()):
            self.close()
            event.accept()
            return
        hit = self.hit_test(event.pos())
        self._activate(hit)
        event.accept()

    def _activate(self, hit):
        """执行一次命中结果（鼠标与键盘共用）。"""
        tag = hit[0]
        if tag == "day":
            cell = self._cells[hit[1]]
            self._emit_day(cell[0], cell[1], cell[2])
        elif tag == "prev_month":
            self._step_month(-1)
        elif tag == "next_month":
            self._step_month(1)
        elif tag == "title":
            self._mode = "month" if self._mode == "day" else "day"
            self._hover = ("none",)
            self._cleanup_hover_anims()   # V7：视图切换，进度即清
            self.update()
        elif tag == "year_prev":
            self._year -= 1
            self._sync_cells()
            self.update()
        elif tag == "year_next":
            self._year += 1
            self._sync_cells()
            self.update()
        elif tag == "month":
            self._month = hit[1]
            self._sync_cells()
            self._mode = "day"
            self._hover = ("none",)
            self.update()
        elif tag == "clear":
            self.cleared.emit()
            self.close()
        elif tag == "today":
            self._sync_today()
            self._emit_day(*self._today)

    def _step_month(self, delta: int):
        self._year, self._month = add_months(self._year, self._month, delta)
        self._sync_cells()
        self.update()

    def _emit_day(self, year: int, month: int, day: int):
        self._selected = (year, month, day)
        self.picked.emit(QDate(year, month, day))
        self.close()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
            return
        if self._mode == "day":
            if key == Qt.Key.Key_PageUp:
                self._step_month(-1)
                return
            if key == Qt.Key.Key_PageDown:
                self._step_month(1)
                return
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                       Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._move_selection(key)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                if self._selected:
                    self._emit_day(*self._selected)
                return
        super().keyPressEvent(event)

    def _move_selection(self, key):
        """方向键在日网格里移动（跨月时自动翻月）。"""
        if self._selected is None:
            self._selected = self._today
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                Qt.Key.Key_Up: -COLS, Qt.Key.Key_Down: COLS}[key]
        for i, cell in enumerate(self._cells):
            if tuple(cell)[:3] == self._selected:
                target = i + step
                break
        else:
            target = 0
        if 0 <= target < DAYS_IN_GRID:
            c = self._cells[target]
            self._selected = (c[0], c[1], c[2])
            if (c[0], c[1]) != (self._year, self._month):
                # 走到补位日（跨月）时跟着翻月，选中状态才留得住
                self._year, self._month = c[0], c[1]
                self._sync_cells()
        self.update()

    # ----------------------------------------------------------------
    # 绘制
    # ----------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        panel = QRectF(SHADOW, SHADOW, POPUP_W, POPUP_H)
        draw_soft_shadow(painter, panel, RADIUS_PANEL + 2, layers=7,
                         max_alpha=52, offset_y=3.0)
        painter.setPen(_blend(self._c("cal_popup_edge"), 1.0))
        painter.setBrush(self._c("cal_popup_bg"))
        painter.drawRoundedRect(panel, RADIUS_PANEL + 2, RADIUS_PANEL + 2)

        if self._mode == "day":
            self._paint_day_view(painter)
        else:
            self._paint_month_view(painter)
        self._paint_footer(painter)
        painter.end()

    def _paint_day_view(self, painter):
        self._paint_title(painter, month_title(self._year, self._month))
        self._paint_nav_buttons(painter, ("chevron_up", "chevron_down"))
        # 表头（用 cal_weekday 而不是 cal_muted：前者是"要读清"的灰，
        # 后者专给跨月补位日，见 theme.py 的令牌注释）
        painter.setFont(self._font(FS_XS))
        painter.setPen(self._c("cal_weekday"))
        for col, label in enumerate(WEEKDAY_LABELS):
            r = self._shift(QRect(8 + col * CELL_W, HEADER_H, CELL_W, WEEKDAY_H))
            painter.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)
        # 日期格
        for i, cell in enumerate(self._cells):
            self._paint_day_cell(painter, i, cell)

    def _paint_day_cell(self, painter, index, cell):
        year, month, day, in_month = cell
        rect = self._shift(self._cell_rect(index))
        block = QRect(rect.x() + (CELL_W - BLOCK_W) // 2,
                      rect.y() + (CELL_H - BLOCK_H) // 2, BLOCK_W, BLOCK_H)
        key = (year, month, day)
        selected = self._selected == key
        is_today = self._today == key
        hover_prog = (self._hover_prog.get(("day", index), 0.0)
                      if in_month else 0.0)

        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._c("cal_accent"))
            painter.drawRoundedRect(QRectF(block), BLOCK_R, BLOCK_R)
        elif hover_prog > 0.004:
            # V7：hover 淡染按进度画 cal_hover_bg（rest=0 不画，端点=1 全值）
            hover_bg = self._c("cal_hover_bg")
            hover_bg.setAlphaF(max(0.0, min(1.0, hover_prog))
                               * hover_bg.alphaF())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_bg)
            painter.drawRoundedRect(QRectF(block), BLOCK_R, BLOCK_R)
        if is_today and not selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(self._c("cal_accent"))
            painter.drawRoundedRect(QRectF(block).adjusted(0.5, 0.5, -0.5, -0.5),
                                    BLOCK_R, BLOCK_R)

        if selected:
            color = self._c("cal_on_accent")
        elif not in_month:
            color = self._c("cal_muted")
        elif is_today:
            color = self._c("cal_accent")
        else:
            color = self._c("cal_text")
        painter.setFont(self._font(FS_SM))
        painter.setPen(color)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(day))

    def _paint_month_view(self, painter):
        self._paint_title(painter, "%d年" % self._year)
        self._paint_nav_buttons(painter, ("chevron_left", "chevron_right"))
        for i, label in enumerate(MONTH_LABELS):
            rect = self._shift(self._month_rect(i))
            block = QRect(rect.x() + 8, rect.y() + (MONTH_H - BLOCK_H) // 2,
                          MONTH_W - 16, BLOCK_H)
            selected = (i + 1) == self._month
            hover_prog = self._hover_prog.get(("month", i + 1), 0.0)
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._c("cal_accent"))
                painter.drawRoundedRect(QRectF(block), BLOCK_R, BLOCK_R)
            elif hover_prog > 0.004:
                # V7：同 day 格，hover 按进度淡染
                hover_bg = self._c("cal_hover_bg")
                hover_bg.setAlphaF(max(0.0, min(1.0, hover_prog))
                                   * hover_bg.alphaF())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(hover_bg)
                painter.drawRoundedRect(QRectF(block), BLOCK_R, BLOCK_R)
            painter.setFont(self._font(FS_SM))
            painter.setPen(self._c("cal_on_accent") if selected
                           else self._c("cal_text"))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_title(self, painter, text):
        rect = self._shift(self._title_rect())
        painter.setFont(self._font(FS_SM, bold=True))
        painter.setPen(self._c("cal_title"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter
                                   | Qt.AlignmentFlag.AlignLeft), text)
        fm = painter.fontMetrics()
        icon = QRect(rect.x() + fm.horizontalAdvance(text) + 5,
                     rect.y() + (HEADER_H - 14) // 2, 14, 14)
        icon_render.paint_icon(painter, "caret_down", icon, self._c("cal_nav_icon"))

    def _paint_nav_buttons(self, painter, names):
        for index, name in enumerate(names):
            rect = self._shift(self._nav_rect(index))
            if self._hover_matches(index):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._c("cal_hover_bg"))
                painter.drawRoundedRect(QRectF(rect), RADIUS_CTL, RADIUS_CTL)
            side = 16
            box = QRect(rect.x() + (NAV_BTN - side) // 2,
                        rect.y() + (NAV_BTN - side) // 2, side, side)
            icon_render.paint_icon(painter, name, box, self._c("cal_nav_icon"))

    def _hover_matches(self, index) -> bool:
        if self._mode == "month":
            return self._hover == ("year_prev" if index == 0 else "year_next",)
        return self._hover == ("prev_month" if index == 0 else "next_month",)

    def _paint_footer(self, painter):
        footer = self._shift(self._footer_rect())
        painter.setPen(_blend(self._c("cal_divider"), 1.0))
        painter.drawLine(footer.left(), footer.top(),
                         footer.right(), footer.top())
        painter.setFont(self._font(FS_SM))
        for tag, rect in (("clear", self._shift(self._clear_rect())),
                          ("today", self._shift(self._today_rect()))):
            if self._hover == (tag,):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._c("cal_hover_bg"))
                painter.drawRoundedRect(QRectF(rect).adjusted(2, 6, -2, -6),
                                        RADIUS_CTL, RADIUS_CTL)
            painter.setPen(self._c("cal_accent"))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                             "清除" if tag == "clear" else "今天")

    def _font(self, pixel: int, bold: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(pixel)
        f.setBold(bold)
        return f

    def sizeHint(self) -> QSize:
        return QSize(POPUP_W + 2 * SHADOW, POPUP_H + 2 * SHADOW)


# ====================================================================
# 日期输入框
# ====================================================================
class DateField(QDateEdit):
    """任务页的截止日期输入框：显示 ``2026/10/02`` + 右侧日历图标。

    · 原生上下微调钮关掉（``setButtonSymbols``），右侧改为一个自绘日历图标
      （``icon_render`` 出图，与全站图标同一套数据）；
    · 点框内任意位置或图标都能开弹层（原生 QDateEdit 只有箭头能开）；
    · 只读：日期只能从弹层选，避免手输产生非法日期（原先要靠
      「非法/留空自动回退今天」兜底，现在这条兜底路径直接消失）。
    """

    ICON_SIDE = 15
    ICON_BOX = 22
    ICON_RIGHT = 5

    def __init__(self, host=None, theme=None, parent=None,
                 object_name: str = "taskDate"):
        super().__init__(parent)
        self._host = host
        # theme 显式传入优先：小卡片（CardWindow）只有私有 ``_theme``，
        # 没有 ``current_theme`` 可读，靠实参把当时主题带进来。
        self._theme = theme if theme in ("light", "dark") else self._resolve_theme()
        self._popup = None
        self._popup_theme = None

        self.setObjectName(object_name)
        self.setCalendarPopup(False)              # 原生弹层退役
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setDisplayFormat("yyyy/MM/dd")
        self.setMinimumDate(SENTINEL_DATE)
        self.setSpecialValueText(EMPTY_TEXT)
        self.setWrapping(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setDate(QDate.currentDate())
        self.lineEdit().setReadOnly(True)

        self._icon_btn = QToolButton(self)
        self._icon_btn.setObjectName("taskDateIcon")
        self._icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._icon_btn.setFixedSize(self.ICON_BOX, self.ICON_BOX)
        self._icon_btn.setStyleSheet(
            "QToolButton#taskDateIcon { border: none; background: transparent;"
            " padding: 0; }")
        self._icon_btn.clicked.connect(self.open_popup)
        self._sync_icon()

        signal = getattr(host, "theme_changed", None)
        if callable(getattr(signal, "connect", None)):
            signal.connect(self._on_theme_changed)

    # ----------------------------------------------------------------
    # 主题
    # ----------------------------------------------------------------
    def _resolve_theme(self) -> str:
        name = getattr(self._host, "current_theme", None)
        return name if name in ("light", "dark") else DEFAULT_THEME

    def _colors(self) -> dict:
        return get_colors(self._theme)

    def _sync_icon(self):
        self._icon_btn.setIcon(icon_render.icon(
            "calendar", self.ICON_SIDE, self._colors().get("text_secondary",
                                                           "#5F5E5A")))

    def _on_theme_changed(self, theme_name=None):
        self.apply_theme(theme_name)

    def apply_theme(self, theme_name=None):
        """刷新图标与弹层配色。

        ``theme_name`` 可省：主窗面板的 ``apply_theme()`` 是无参的，此时
        主题从宿主现读（与 ``controls.IconButton`` 同一口径）。
        """
        if theme_name in ("light", "dark"):
            self._theme = theme_name
        elif theme_name is None:
            self._theme = self._resolve_theme()
        self._sync_icon()
        if self._popup is not None:
            self._popup.set_theme(self._theme)
            self._popup_theme = self._theme

    # ----------------------------------------------------------------
    # 值
    # ----------------------------------------------------------------
    def dateOrNone(self):
        """选中日期；「无日期」时返回 ``None``（供任务落库用）。"""
        d = self.date()
        return None if d == SENTINEL_DATE else d

    def setDateOrNone(self, value):
        """``QDate`` / ``None`` 都能设（``None`` = 无日期）。"""
        if value is None:
            self.setDate(SENTINEL_DATE)
        else:
            self.setDate(value)

    def isEmpty(self) -> bool:
        return self.date() == SENTINEL_DATE

    # ----------------------------------------------------------------
    # 弹层
    # ----------------------------------------------------------------
    def open_popup(self):
        popup = self._ensure_popup()
        if popup.isVisible():
            popup.close()
            return
        base = self.dateOrNone()
        selected = (base.year(), base.month(), base.day()) if base else None
        popup.configure(selected=selected)
        popup.show_at(self._anchor_rect(), self._screen_rect())
        self._popup_theme = self._theme

    def _ensure_popup(self):
        if self._popup is None:
            popup = CalendarPopup(self.window(), self._theme)
            popup.set_anchor(self)   # 外点关闭判定豁免锚点自己（toggle 归这里）
            popup.picked.connect(self._on_picked)
            popup.cleared.connect(self._on_cleared)
            self._popup = popup
        elif self._popup_theme != self._theme:
            self._popup.set_theme(self._theme)
            self._popup_theme = self._theme
        return self._popup

    def _on_picked(self, qdate: QDate):
        # setDate 自身就会发 dateChanged（值变了才发），不要再手工 emit 一次
        self.setDate(qdate)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_cleared(self):
        self.setDate(SENTINEL_DATE)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def popup(self):
        """当前弹层（未创建时为 None）；供测试与主题刷新使用。"""
        return self._popup

    def _anchor_rect(self):
        top_left = self.mapToGlobal(self.rect().topLeft())
        return (top_left.x(), top_left.y(), self.width(), self.height())

    def _screen_rect(self):
        screen = self.screen()
        if screen is None:
            return (0, 0, 1920, 1080)
        geo = screen.availableGeometry()
        return (geo.x(), geo.y(), geo.width(), geo.height())

    # ----------------------------------------------------------------
    # 事件
    # ----------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.open_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter, Qt.Key.Key_Down,
                           Qt.Key.Key_F4):
            self.open_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        y = max(0, (self.height() - self.ICON_BOX) // 2)
        self._icon_btn.move(self.width() - self.ICON_BOX - self.ICON_RIGHT, y)

    def focusInEvent(self, event):
        """取消 QAbstractSpinBox 聚焦时自动选中「年」段的行为。

        本控件只读、点击即开弹层，文本框里留一段反白选中毫无意义，反而
        像「可编辑」的误导。super() 之后再 deselect 一次即可（Qt 是在
        focusIn 里选中的，顺序不能反过来）。
        """
        super().focusInEvent(event)
        self.lineEdit().deselect()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._icon_btn.setEnabled(self.isEnabled())

    def hideEvent(self, event):
        if self._popup is not None and self._popup.isVisible():
            self._popup.close()
        super().hideEvent(event)
