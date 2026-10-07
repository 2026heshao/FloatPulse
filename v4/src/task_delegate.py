# -*- coding: utf-8 -*-
"""
====================================================================
任务行自定义渲染  -  TaskItemDelegate
====================================================================
用 ``QStyledItemDelegate`` 自绘任务行，替代「拼字符串塞进 QListWidgetItem」
的脆弱做法，并把勾选动画画进 ``paint()`` 内（不新开 overlay）。

为什么用 delegate 而不是 setItemWidget：
  - ``setItemWidget`` 会在每行插一个真实 QWidget，**吃掉鼠标事件**，
    单击选中 / Ctrl·Shift 多选 / 右键 customContextMenuRequested /
    原生键盘与滚轮都要手动转发，回归风险高（正是本模块既有强交互）。
  - delegate 自绘**完全不改** QListWidget 的命中/选择/右键链路，
    ``itemAt()`` / ``selectedItems()`` / ExtendedSelection 全部照旧。

item 数据约定（由任务页 refresh 时写入）：
  - ``Qt.ItemDataRole.UserRole``      : task_id（header 行不写）
  - ``UserRole + 1``（KIND_ROLE）     : KIND_ROW / KIND_HEADER
  - ``UserRole + 2``（ROLE_TITLE）    : 标题文本
  - ``UserRole + 3``（ROLE_REL）      : 相对时间文案（可为空串）；
                                        header 行复用为「计数」（字符串）
  - ``UserRole + 4``（ROLE_STATE）    : 状态枚举（overdue/today/future/none）；
                                        header 行复用为「是否逾期组」
                                        （overdue → 组标题整行 danger 红）
  - ``UserRole + 5``（ROLE_DONE）     : 是否已完成（bool，用于无动画时兜底）

勾选动画四要素（全部按子进度绘制；V3 起四段错峰，见 CHECK_* 轴常量）：
  1. 对勾 ``QPainterPath`` 按描画子进度逐段描绘
  2. 方形圆角框缩放 1.0 → CHECK_BOUNCE_SCALE → 1.0 的回弹（pop 段）
  3. 标题删除线按扫过子进度从左划出（与对勾错峰 80ms）
  4. 整行文字 / 状态色按沉降子进度插值到次级灰（text_placeholder）

动画进度以 **task_id 为键** 存于本 delegate（``self._progress``），
故 refresh() 重建 item 后动画不丢；进度由任务页的面板级
单个 ``QVariantAnimation`` 驱动写入。
====================================================================
"""

import math
import re

from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QPointF, QEvent, pyqtSignal,
)
from PyQt6.QtGui import QColor, QPen, QFont, QFontMetrics
from PyQt6.QtWidgets import QListWidget, QStyledItemDelegate, QStyle

from src.constants import CHECK_ANIM_MS, CHECK_BOUNCE_SCALE
from src.task_manager import (
    STATE_NONE, STATE_OVERDUE, STATE_TODAY, KIND_HEADER,
)
from src import row_hover


# ---- item 角色（相对 Qt.UserRole 偏移；与任务页保持一致）----
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
ROLE_REL = Qt.ItemDataRole.UserRole + 3
ROLE_STATE = Qt.ItemDataRole.UserRole + 4
ROLE_DONE = Qt.ItemDataRole.UserRole + 5

# ---- 行/组标题尺寸（原 2026-10-02 高仿真稿：行高 32、行距 18px、
#      15px 方形圆角勾选框、整行平面背景无圆角。2026-10-06 紧凑改版：
#      行左右内边距 18 → 8 压水平留白（勾选框与输入栏左缘近乎对齐，
#      hover/选中背景本就是整行全宽色块，视觉更整），其余尺寸不动；
#      小卡片任务页（card_window_pages）共用本委托，同步收紧）----
ROW_HEIGHT = 32
HEADER_HEIGHT = 26
CHECK_SIZE = 15            # 勾选框边长（方形圆角，不再是圆形）
CHECK_RADIUS = 3           # 勾选框圆角（稿：border-radius 3px）
LEFT_MARGIN = 8            # 行左内边距（紧凑改版：18 → 8）
RIGHT_MARGIN = 8           # 行右内边距
CHECK_TEXT_GAP = 10        # 勾选框与标题间距（稿：gap 10px）
REL_GAP = 10               # 标题与相对时间最小间距
CAPTION_DROP = 6           # 组标题整体下沉量（稿：上 10px / 下 4px 的非对称留白）

# ---- V3 四段式错峰时间轴（毫秒，2026-10-07 交互视觉清单 §3）----
# 面板仍用**单个** QVariantAnimation 驱动全局进度 p（0→1，总长
# CHECK_ANIM_MS=230，行级进度缓存基建零改动），委托在绘制侧把 p 按
# 时间轴切成四段子进度：「完成」从一个同步瞬态变成有编排的过程 ——
#   勾选圈 pop（220ms 回弹）→ 对勾描画（150ms，错峰 60ms 起）→
#   删除线左→右扫（150ms，错峰 80ms 起）→ 整行文字沉降次级色（120ms）。
# 取消勾选（p 1→0）天然逆放；p=0/1 两端所有子进度同为 0/1，rest 与
# 终态渲染逐字节不变。轴值总长必须与 constants.CHECK_ANIM_MS 对齐。
CHECK_POP_END = 220        # 勾选圈 pop 段终点
CHECK_DRAW_START = 60      # 对勾描画起点（与 pop 错峰）
CHECK_DRAW_END = 210       # 对勾描画终点
CHECK_STRIKE_START = 80    # 删除线起点（与对勾错峰 80ms）
CHECK_STRIKE_END = 230     # 删除线终点（= 总长 CHECK_ANIM_MS）
CHECK_SETTLE_END = 120     # 文字沉降段终点


def _stage(t: float, start: float, end: float) -> float:
    """全局时间 t（ms）→ [start, end] 段的子进度（0..1，越界夹取）。"""
    if end <= start:
        return 1.0 if t >= end else 0.0
    return max(0.0, min(1.0, (t - start) / (end - start)))


def _to_qcolor(value, fallback: str = "#000000") -> QColor:
    """把主题色值（#RRGGBB / rgba(...) / 命名色）安全转成 QColor。"""
    if isinstance(value, QColor):
        return QColor(value)
    text = str(value).strip()
    color = QColor(text)
    if color.isValid():
        return color
    # QColor 对 CSS 的 rgba() 未必解析 → 手动兜底
    m = re.match(r"rgba?\(([^)]*)\)", text)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            r = int(float(parts[0]))
            g = int(float(parts[1]))
            b = int(float(parts[2]))
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return QColor(r, g, b, int(round(a * 255)))
        except (ValueError, IndexError):
            pass
    return QColor(fallback)


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """两个颜色按 t（0→c1，1→c2）线性插值，含 alpha。"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(c1.red() + (c2.red() - c1.red()) * t)),
        int(round(c1.green() + (c2.green() - c1.green()) * t)),
        int(round(c1.blue() + (c2.blue() - c1.blue()) * t)),
        int(round(c1.alpha() + (c2.alpha() - c1.alpha()) * t)),
    )


class TaskItemDelegate(QStyledItemDelegate):
    """任务列表行渲染委托（组标题 + 任务行 + 勾选动画）。"""

    # 命中勾选框 / 标题时发射，参数为 task_id
    toggle_requested = pyqtSignal(int)

    def __init__(self, colors: dict | None = None, parent=None):
        super().__init__(parent)
        self._colors = dict(colors) if colors else {}
        # 勾选动画进度：task_id -> float(0..1)（跨 refresh 存活）
        self._progress = {}
        # U4（2026-10-07 规格 G5）：行级 hover 进度缓存 —— hover 底色从
        # State_MouseOver 一帧瞬变改为 120ms OutCubic 插值（单 QTimer
        # 驱动、滚动/拖拽清零）。组标题行不参与（旧口径：header 无底色）。
        self._hover = row_hover.RowHoverController(
            hoverable=lambda idx: idx.data(KIND_ROLE) != KIND_HEADER)

    # ---------------- 对外 ----------------
    def set_colors(self, colors: dict):
        """主题切换时更新配色（组件不写死颜色）。"""
        self._colors = dict(colors) if colors else {}
        parent = self.parent()
        if parent is not None and hasattr(parent, "viewport"):
            parent.viewport().update()

    def set_check_progress(self, task_id: int, progress: float):
        """写入某任务的勾选动画进度（0→1 完成，1→0 取消）。"""
        self._progress[int(task_id)] = max(0.0, min(1.0, float(progress)))

    def clear_progress(self, task_id: int):
        """清除某任务的动画进度（恢复为按 done 兜底）。"""
        self._progress.pop(int(task_id), None)

    def clear_progress_except(self, task_id=None):
        """清除所有动画进度，仅保留指定 task_id（None 表示全部清除）。

        任务页在 ``refresh()`` 时调用：只要没有正在动画的任务，就把过期
        进度全部清掉，避免「上下文菜单/批量操作改了完成态、但旧动画进度
        仍把该行画成已完成」的脏状态。
        """
        if task_id is None:
            self._progress.clear()
            return
        keep = int(task_id)
        for key in list(self._progress.keys()):
            if key != keep:
                del self._progress[key]

    # ---------------- 尺寸与命中几何 ----------------
    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt 命名)
        kind = index.data(KIND_ROLE)
        height = HEADER_HEIGHT if kind == KIND_HEADER else ROW_HEIGHT
        width = option.rect.width() if option.rect.isValid() else 0
        return QSize(max(0, width), height)

    def _checkbox_rect(self, rect: QRect) -> QRect:
        """勾选框命中/绘制矩形（行内左侧方形圆角框）。"""
        left = rect.left() + LEFT_MARGIN
        cy = rect.center().y()
        return QRect(left, cy - CHECK_SIZE // 2, CHECK_SIZE, CHECK_SIZE)

    def _title_rect(self, rect: QRect) -> QRect:
        """标题命中矩形（勾选框右侧文本区）。"""
        text_left = rect.left() + LEFT_MARGIN + CHECK_SIZE + CHECK_TEXT_GAP
        text_right = rect.right() - RIGHT_MARGIN
        return QRect(text_left, rect.top(),
                     max(1, text_right - text_left), rect.height())

    # ---------------- 事件：命中勾选框 / 标题 → 请求切换完成 ----------------
    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        # 只处理左键释放；右键等一律返回 False 让事件继续冒泡（原生右键菜单）
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if index.data(KIND_ROLE) == KIND_HEADER:      # 组标题：不作响应
            return False

        try:
            pos = event.position().toPoint()
        except AttributeError:
            return False

        cb_rect = self._checkbox_rect(option.rect)
        title_rect = self._title_rect(option.rect)
        if cb_rect.contains(pos):
            pass  # 复选框命中，放行
        elif title_rect.contains(pos):
            # 带 Ctrl / Shift 时让位给多选，避免误触发完成
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.ShiftModifier):
                return False
        else:
            return False

        task_id = index.data(Qt.ItemDataRole.UserRole)
        # 仅对合法 int task_id 生效（header / 异常值跳过）
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return False
        self.toggle_requested.emit(int(task_id))
        return True

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if index.data(KIND_ROLE) == KIND_HEADER:
            self._paint_header(painter, option, index)
        else:
            self._paint_row(painter, option, index)
        painter.restore()

    def _paint_header(self, painter, option, index):
        """组标题：小号灰字 + 等宽计数（逾期组整行转 danger 红）。

        标签取 DisplayRole，计数取 ROLE_REL（面板写入；旧数据无 ROLE_REL
        时只画标签）。2026-10-02 高仿真稿：标签与计数之间 8px 间隙，
        计数用等宽字体；不再共用「标签 · 计数」一个字符串。
        """
        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        count = str(index.data(ROLE_REL) or "")
        overdue = index.data(ROLE_STATE) == STATE_OVERDUE
        label_color = _to_qcolor(
            self._colors.get("danger" if overdue else "text_placeholder",
                             "#A32D2D" if overdue else "#6E6D67"))
        # 稿的 CSS：.n 恒为 --text-3 —— 计数不随逾期组变红
        count_color = _to_qcolor(self._colors.get("text_placeholder", "#6E6D67"))

        rect = option.rect.adjusted(LEFT_MARGIN, CAPTION_DROP,
                                    -RIGHT_MARGIN, 0)
        font = QFont(painter.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(label_color)
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )
        if count:
            mono = QFont("Consolas")
            mono.setPixelSize(11)
            label_w = QFontMetrics(font).horizontalAdvance(label)
            painter.setFont(mono)
            painter.setPen(count_color)
            painter.drawText(
                QRect(rect.left() + label_w + 8, rect.top(),
                      rect.width() - label_w - 8, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                count,
            )

    def _resolve_progress(self, task_id, done) -> float:
        """取有效进度：有动画进度用动画值，否则按 done 兜底（1/0）。"""
        p = self._progress.get(int(task_id) if isinstance(task_id, int) else task_id)
        if p is None:
            p = 1.0 if done else 0.0
        return max(0.0, min(1.0, float(p)))

    def _row_colors(self, state) -> tuple:
        """行内两处取色（标题色与行尾徽标色**解耦**，2026-10-02 高仿真稿）。

        - 标题恒用主题主文字色 ``text``：状态信息只由行尾徽标表达，
          逾期标题不再整体标红（稿中逾期行标题是正文字色，红色只在
          「逾期 N 天」徽标上）。
        - 行尾徽标：逾期 → ``danger``、今天 → ``warn``、其余 →
          ``text_placeholder``；已完成行按动画进度插值到完成灰。
        """
        c = self._colors
        normal_color = _to_qcolor(c.get("text", "#2C2C2A"))

        if state == STATE_OVERDUE:
            rel_base = _to_qcolor(c.get("danger", "#A32D2D"))
        elif state == STATE_TODAY:
            rel_base = _to_qcolor(c.get("warn", "#854F0B"))
        else:
            rel_base = _to_qcolor(c.get("text_placeholder", "#6E6D67"))
        return normal_color, rel_base

    def _paint_row(self, painter, option, index):
        rect = option.rect
        task_id = index.data(Qt.ItemDataRole.UserRole)
        done = bool(index.data(ROLE_DONE))
        state = index.data(ROLE_STATE) or STATE_NONE
        title = str(index.data(ROLE_TITLE) or "")
        rel = str(index.data(ROLE_REL) or "")
        p = self._resolve_progress(task_id, done)
        # V3：全局进度 → 四段错峰子进度（轴长 = CHECK_ANIM_MS，见常量注释）
        t = p * CHECK_ANIM_MS
        pop_p = _stage(t, 0.0, CHECK_POP_END)
        draw_p = _stage(t, CHECK_DRAW_START, CHECK_DRAW_END)
        strike_p = _stage(t, CHECK_STRIKE_START, CHECK_STRIKE_END)
        settle_p = _stage(t, 0.0, CHECK_SETTLE_END)

        c = self._colors
        done_color = _to_qcolor(c.get("text_placeholder", "#6E6D67"))
        normal_color, rel_base = self._row_colors(state)

        # ---- 行背景：选中 / 悬浮（稿：整行平面色块，方角不缩进）----
        # U4（2026-10-07 规格 G5）：hover 底色由 State_MouseOver 一帧瞬变
        # 改为行级 hp 进度插值（120ms OutCubic，motion fast 档）。端点
        # 不变：hp=1 时仍是不透明 surface_2 整行色块；hp<=ε 走原 rest
        # 分支 —— 非 hover 态渲染与旧版逐字节一致。selected > hover。
        if option.state & QStyle.StateFlag.State_Selected:
            bg = _to_qcolor(c.get("accent_soft", "#E1F5EE"))
        else:
            hp = row_hover.hover_amount(self._hover, option, index)
            if hp > row_hover.ALPHA_EPS:
                bg = _to_qcolor(c.get("surface_2", "#F5F4F0"))
                bg.setAlpha(int(round(255.0 * hp)))
            else:
                bg = None
        if bg is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRect(QRectF(rect))
        if option.state & QStyle.StateFlag.State_Selected:
            # 选中态再描一圈 1px 主色内框（稿：outline 1px accent, offset -1px）
            painter.setPen(QPen(_to_qcolor(c.get("primary", "#0F6E56")), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5))

        # ---- 勾选框（方形圆角框 + 回弹 + 对勾）----
        cb_rect = self._checkbox_rect(rect)
        self._paint_checkbox(painter, cb_rect, pop_p, draw_p)

        # ---- 文字颜色：normal → done 按沉降段子进度插值 ----
        text_color = _lerp_color(normal_color, done_color, settle_p)

        # 字体
        font = QFont(painter.font())
        font.setPixelSize(13)
        base_fm = QFontMetrics(font)
        # 行尾相对时间用等宽字体（稿：--mono）——数字变化时不抖动，
        # 且与标题的正文字体拉开层级
        rel_font = QFont("Consolas")
        rel_font.setPixelSize(11)
        rel_fm = QFontMetrics(rel_font)

        # 相对时间占据右侧（右对齐）
        rel_w = rel_fm.horizontalAdvance(rel) if rel else 0
        text_left = rect.left() + LEFT_MARGIN + CHECK_SIZE + CHECK_TEXT_GAP
        rel_x = rect.right() - RIGHT_MARGIN - rel_w
        title_w = max(20, rel_x - REL_GAP - text_left)

        painter.setFont(font)
        painter.setPen(text_color)
        elided = base_fm.elidedText(title, Qt.TextElideMode.ElideRight, title_w)
        painter.drawText(
            QRect(text_left, rect.top(), title_w, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )

        # 删除线：按扫过子进度从左划出（与对勾错峰）
        if strike_p > 0.001:
            line_w = base_fm.horizontalAdvance(elided) * strike_p
            line_y = rect.center().y() + 1
            pen = QPen(text_color, 1.3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(text_left, line_y),
                             QPointF(text_left + line_w, line_y))

        # 相对时间
        if rel:
            painter.setFont(rel_font)
            painter.setPen(_lerp_color(rel_base, done_color, settle_p))
            painter.drawText(
                QRect(rel_x, rect.top(), rel_w, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                rel,
            )

    def _paint_checkbox(self, painter, cb_rect: QRect, pop_p: float,
                        draw_p: float):
        """方形圆角勾选框（高仿真稿）：未完成 = line_2 描边空框；
        完成 = primary 实底 + on_primary 对勾（浅主题白勾 / 深主题墨绿勾，
        与稿的 #FFFFFF / #04342C 一致）。回弹走 pop 段子进度，对勾描画
        走描画段子进度（V3 四段错峰）。"""
        fill_color = _to_qcolor(self._colors.get("primary", "#0F6E56"))
        idle_border = _to_qcolor(self._colors.get("line_2", "#D3D1C7"))
        symbol_color = _to_qcolor(self._colors.get("on_primary", "#FFFFFF"))

        # 回弹：1.0 → CHECK_BOUNCE_SCALE → 1.0（pop 段内完成）
        scale = 1.0 + (CHECK_BOUNCE_SCALE - 1.0) * math.sin(math.pi * pop_p)
        center = cb_rect.center()
        side = cb_rect.width() * scale
        box = QRectF(center.x() - side / 2.0, center.y() - side / 2.0,
                     side, side)

        # 填充随 pop 子进度淡入
        if pop_p > 0.001:
            fill = QColor(fill_color)
            fill.setAlphaF(min(1.0, pop_p))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, CHECK_RADIUS, CHECK_RADIUS)

        # 描边：idle(line_2) → primary 插值（pop 段）
        pen = QPen(_lerp_color(idle_border, fill_color, pop_p), 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, CHECK_RADIUS, CHECK_RADIUS)

        # 对勾按描画子进度逐段描绘
        if draw_p > 0.01:
            self._draw_check(painter, box, draw_p, symbol_color)

    def _draw_check(self, painter, box: QRectF, p: float,
                    color: QColor | None = None):
        """在框内按进度 p 描绘对勾（两段折线，按总长比例分配）。"""
        x, y, w, h = box.x(), box.y(), box.width(), box.height()
        p0 = QPointF(x + w * 0.26, y + h * 0.52)
        p1 = QPointF(x + w * 0.44, y + h * 0.70)
        p2 = QPointF(x + w * 0.76, y + h * 0.32)

        def _dist(a, b):
            return math.hypot(b.x() - a.x(), b.y() - a.y())

        l1, l2 = _dist(p0, p1), _dist(p1, p2)
        total = l1 + l2
        if total <= 0:
            return
        drawn = p * total

        pen = QPen(QColor(color) if color is not None else QColor(255, 255, 255),
                   max(1.5, w * 0.14))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if drawn <= l1:
            t = drawn / l1 if l1 > 0 else 0.0
            end = QPointF(p0.x() + (p1.x() - p0.x()) * t,
                          p0.y() + (p1.y() - p0.y()) * t)
            painter.drawLine(p0, end)
        else:
            painter.drawLine(p0, p1)
            t = min(1.0, (drawn - l1) / l2) if l2 > 0 else 1.0
            end = QPointF(p1.x() + (p2.x() - p1.x()) * t,
                          p1.y() + (p2.y() - p1.y()) * t)
            painter.drawLine(p1, end)


class TaskListWidget(QListWidget):
    """任务列表容器（清单 A3，2026-10-08）：主窗任务页与小卡片任务页共用。

    背景：委托的 ``editorEvent`` 只处理鼠标释放，``QListWidget`` 原生
    只给方向键移动当前行——用户能用键盘"走到"任务上，但走到后勾选、
    右键菜单全够不着。本容器把缺的键盘路径补齐（两处入口共用一份实现，
    避免主窗/小卡片重复）：

    - **Space / Enter（含小键盘 Enter）**：对当前行发勾选切换 —— 直接
      沿用委托既有的 ``toggle_requested`` 信号通道，勾选动画/延时重建
      逻辑零改动；
    - **Shift+F10 / Menu 键**：在当前行位置弹右键菜单 —— 发射内建的
      ``customContextMenuRequested(QPoint)``（坐标语义与鼠标右键一致，
      viewport 坐标），两处宿主页各自已接好的菜单处理器原样复用，
      菜单实现零重复；
    - 方向键移动当前行走 QListWidget 原生逻辑，不在此覆盖；组标题行
      （KIND_HEADER / 无合法 task_id）对 Space/Enter 不响应。
    """

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._toggle_current_row():
                event.accept()
                return
        elif key == Qt.Key.Key_Menu or (
                key == Qt.Key.Key_F10 and
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self._open_menu_on_current_row():
                event.accept()
                return
        super().keyPressEvent(event)

    # ---------------- 内部 ----------------
    def _toggle_current_row(self) -> bool:
        """对当前行发勾选切换（经委托的 toggle_requested 通道）。"""
        index = self.currentIndex()
        if not index.isValid():
            return False
        if index.data(KIND_ROLE) == KIND_HEADER:
            return False
        task_id = index.data(Qt.ItemDataRole.UserRole)
        # 仅对合法 int task_id 生效（与委托 editorEvent 的守卫一致）
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return False
        delegate = self.itemDelegate()
        if delegate is None or not hasattr(delegate, "toggle_requested"):
            return False
        delegate.toggle_requested.emit(int(task_id))
        return True

    def _open_menu_on_current_row(self) -> bool:
        """在当前行中心弹右键菜单（复用宿主页已接的菜单处理器）。"""
        index = self.currentIndex()
        if not index.isValid():
            return False
        # 与 customContextMenuRequested 的坐标契约一致（viewport 坐标）
        self.customContextMenuRequested.emit(self.visualRect(index).center())
        return True
