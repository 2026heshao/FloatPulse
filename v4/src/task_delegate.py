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
  - ``UserRole + 3``（ROLE_REL）      : 相对时间文案（可为空串）
  - ``UserRole + 4``（ROLE_STATE）    : 状态枚举（overdue/today/future/none）
  - ``UserRole + 5``（ROLE_DONE）     : 是否已完成（bool，用于无动画时兜底）

勾选动画四要素（全部按进度 p 绘制）：
  1. 对勾 ``QPainterPath`` 按 p 逐段描绘
  2. 圆框缩放 1.0 → CHECK_BOUNCE_SCALE → 1.0 的回弹
  3. 标题删除线按 p 从左划出
  4. 整行文字 / 状态色按 p 插值到次级灰（task_done）

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
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle

from src.constants import CHECK_BOUNCE_SCALE
from src.task_manager import (
    STATE_NONE, STATE_OVERDUE, STATE_TODAY, KIND_ROW, KIND_HEADER,
)


# ---- item 角色（相对 Qt.UserRole 偏移；与任务页保持一致）----
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
ROLE_REL = Qt.ItemDataRole.UserRole + 3
ROLE_STATE = Qt.ItemDataRole.UserRole + 4
ROLE_DONE = Qt.ItemDataRole.UserRole + 5

# ---- 行/组标题尺寸 ----
ROW_HEIGHT = 34
HEADER_HEIGHT = 26
CHECK_SIZE = 16            # 勾选框圆直径
LEFT_MARGIN = 8            # 行左内边距
RIGHT_MARGIN = 10          # 行右内边距
CHECK_TEXT_GAP = 10        # 勾选框与标题间距
REL_GAP = 10               # 标题与相对时间最小间距


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
        """勾选框命中/绘制矩形（行内左侧圆形）。"""
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
            hit = True
        elif title_rect.contains(pos):
            # 带 Ctrl / Shift 时让位给多选，避免误触发完成
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.ShiftModifier):
                return False
            hit = True
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
        """组标题：小号灰字 + 计数（计数由任务页拼进 DisplayRole）。"""
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        font = QFont(painter.font())
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(_to_qcolor(self._colors.get("text_placeholder", "#AAB4BF")))
        rect = option.rect.adjusted(LEFT_MARGIN + 2, 0, -RIGHT_MARGIN, 0)
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(text),
        )

    def _resolve_progress(self, task_id, done) -> float:
        """取有效进度：有动画进度用动画值，否则按 done 兜底（1/0）。"""
        p = self._progress.get(int(task_id) if isinstance(task_id, int) else task_id)
        if p is None:
            p = 1.0 if done else 0.0
        return max(0.0, min(1.0, float(p)))

    def _row_colors(self, state) -> tuple:
        """行内两处取色（标题色与状态徽标色**解耦**）。

        - 标题主色 ``normal_color``：仅逾期保留 ``task_overdue`` 红
          （真正的紧急信号）；今天 / 未来 / 无日期一律用主题主文字色
          ``text``——避免多条今日任务把整个列表刷成强调橙、可读性差。
        - 时间徽标色 ``rel_base``（右侧"今天 / 逾期N天"）：逾期红、
          今天橙、其余次级灰——状态信息只由徽标表达，不丢失。
        """
        c = self._colors
        if state == STATE_OVERDUE:
            normal_color = _to_qcolor(c.get("task_overdue", "#E74C3C"))
        else:
            normal_color = _to_qcolor(c.get("text", "#2C3E50"))

        if state == STATE_OVERDUE:
            rel_base = _to_qcolor(c.get("task_overdue", "#E74C3C"))
        elif state == STATE_TODAY:
            rel_base = _to_qcolor(c.get("task_today", "#E67E22"))
        else:
            rel_base = _to_qcolor(c.get("text_secondary", "#8B96A3"))
        return normal_color, rel_base

    def _paint_row(self, painter, option, index):
        rect = option.rect
        task_id = index.data(Qt.ItemDataRole.UserRole)
        done = bool(index.data(ROLE_DONE))
        state = index.data(ROLE_STATE) or STATE_NONE
        title = str(index.data(ROLE_TITLE) or "")
        rel = str(index.data(ROLE_REL) or "")
        p = self._resolve_progress(task_id, done)

        c = self._colors
        done_color = _to_qcolor(c.get("task_done", "#9AA5B1"))
        normal_color, rel_base = self._row_colors(state)

        # ---- 行背景：选中 / 悬浮 ----
        if option.state & QStyle.StateFlag.State_Selected:
            bg = _to_qcolor(c.get("list_item_selected", "rgba(91,192,190,0.16)"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            bg = _to_qcolor(c.get("list_item_hover", "rgba(91,192,190,0.08)"))
        else:
            bg = None
        if bg is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(
                QRectF(rect.adjusted(1, 1, -1, -1)), 9.0, 9.0)

        # ---- 勾选框（圆框 + 回弹 + 对勾）----
        cb_rect = self._checkbox_rect(rect)
        self._paint_checkbox(painter, cb_rect, p)

        # ---- 文字颜色：normal → done 按 p 插值 ----
        text_color = _lerp_color(normal_color, done_color, p)

        # 字体
        font = QFont(painter.font())
        font.setPixelSize(13)
        base_fm = QFontMetrics(font)
        rel_font = QFont(font)
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

        # 删除线：按 p 从左划出
        if p > 0.001:
            line_w = base_fm.horizontalAdvance(elided) * p
            line_y = rect.center().y() + 1
            pen = QPen(text_color, 1.3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(text_left, line_y),
                             QPointF(text_left + line_w, line_y))

        # 相对时间
        if rel:
            painter.setFont(rel_font)
            painter.setPen(_lerp_color(rel_base, done_color, p))
            painter.drawText(
                QRect(rel_x, rect.top(), rel_w, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                rel,
            )

    def _paint_checkbox(self, painter, cb_rect: QRect, p: float):
        check_color = _to_qcolor(self._colors.get("task_check", "#1F8A4C"))
        idle_border = _to_qcolor(self._colors.get("text_secondary", "#8B96A3"))

        # 回弹：1.0 → CHECK_BOUNCE_SCALE → 1.0
        scale = 1.0 + (CHECK_BOUNCE_SCALE - 1.0) * math.sin(math.pi * p)
        center = cb_rect.center()
        r = (cb_rect.width() * scale) / 2.0
        circle = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)

        # 填充随 p 淡入
        if p > 0.001:
            fill = QColor(check_color)
            fill.setAlphaF(min(1.0, p))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(circle)

        # 描边：idle → check 插值
        pen = QPen(_lerp_color(idle_border, check_color, p), 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle)

        # 对勾按 p 逐段描绘
        if p > 0.01:
            self._draw_check(painter, circle, p)

    def _draw_check(self, painter, circle: QRectF, p: float):
        """在圆内按进度 p 描绘对勾（两段折线，按总长比例分配）。"""
        x, y, w, h = circle.x(), circle.y(), circle.width(), circle.height()
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

        pen = QPen(QColor(255, 255, 255), max(1.5, w * 0.14))
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
