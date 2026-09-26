# -*- coding: utf-8 -*-
"""
====================================================================
网址导航面板  -  NavPanel
====================================================================
从 main_window.py 抽出的独立面板，承载网址站点增删改 / 排序 /
右键菜单 / 浏览器打开等业务逻辑。
通过 host（MainWindow）访问导航管理器与样式。

排序采用「实时让位」拖拽（参考 pythonguis DragButton / Animated-ListView）：
拖动项跟随鼠标，越过邻居中点时邻居立即以 QPropertyAnimation 滑开让位，
空档跟着鼠标走；松手后拖动项再滑入空档落位，数据层随后落盘。
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QMenu, QDialog, QFormLayout, QMessageBox,
    QScrollArea, QFrame, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import (
    Qt, QUrl, QPoint, QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PyQt6.QtGui import QDesktopServices, QColor

from src.glass_dialog import make_dialog_buttons

# ---- 行布局几何 ----
ROW_H = 44          # 单行高度
ROW_GAP = 6         # 行间距
LIST_MARGIN = 8     # 列表内边距
STEP = ROW_H + ROW_GAP

DRAG_THRESHOLD = 6  # 按下后位移超过该像素才算开始拖拽
SHIFT_MS = 150      # 邻居让位动画时长
SETTLE_MS = 180     # 落定滑动动画时长


def _repolish(widget):
    """让 QSS 的动态属性（如 dragging）立即生效"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class _NavRow(QFrame):
    """导航列表单行：标题 + URL + 打开按钮。

    鼠标事件转发给 _NavList 统一处理拖拽；「打开」按钮自己消费
    按下事件，不会触发拖拽。
    """

    def __init__(self, list_widget, site):
        super().__init__(list_widget)
        self._list = list_widget
        self.nav_id = site.nav_id
        self.url = site.url
        self.setObjectName("navRow")

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 10, 0)
        h.setSpacing(10)

        title = QLabel(site.title)
        title.setObjectName("navRowTitle")
        title.setToolTip(site.title)
        url = QLabel(site.url)
        url.setObjectName("navRowUrl")
        url.setToolTip(site.url)

        open_btn = QPushButton("打开")
        open_btn.setObjectName("tableOpenBtn")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setFixedHeight(28)
        open_btn.clicked.connect(lambda checked=False, u=self.url: self._list.open_requested(u))

        h.addWidget(title, 2)
        h.addWidget(url, 3)
        h.addWidget(open_btn, 0)

    # ---- 鼠标事件 → 交给列表统一驱动拖拽 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._list.begin_press(self, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._list.press_move(self, event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._list.end_press(self, event.globalPosition().toPoint())
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self._list.context_requested(self, event.globalPos())


class _NavList(QWidget):
    """平铺站点行的容器，负责手工布局 + 实时让位拖拽动画。

    坐标约定：所有行位置都是本容器坐标；拖拽跟手用
    mapFromGlobal(光标) 计算，天然兼容拖拽中途的自动滚动。
    """

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._rows = []            # 展示顺序 = 当前视觉顺序
        self._shift_anims = {}     # row -> QPropertyAnimation（邻居让位）

        # ⚠ 必须给 parent：无 parent 的 QLabel 是顶层窗口，
        # 一旦 show 会变成独立弹窗（2026-09-24 用户实测踩坑）
        self._empty_label = QLabel("暂无站点，先在上方添加一个吧", self)
        self._empty_label.setObjectName("hintLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

        # ---- 拖拽状态 ----
        self._press_row = None
        self._press_gpos = None
        self._dragging = False
        self._drag_row = None
        self._grab_dy = 0          # 拖拽起点光标相对行的纵向偏移

    # ------------------------------------------------------------------
    # 行管理
    # ------------------------------------------------------------------
    def set_sites(self, sites):
        if self._dragging:
            self._abort_drag()
        for anim in self._shift_anims.values():
            anim.stop()
        self._shift_anims.clear()
        for row in self._rows:
            row.deleteLater()
        self._rows = []

        for site in sites:
            row = _NavRow(self, site)
            row.show()
            self._rows.append(row)

        self._empty_label.setVisible(not sites)
        self._relayout()

    def _relayout(self):
        """按当前顺序摆放所有行（宽度自适应容器）"""
        w = max(self.width(), 120)
        row_w = w - 2 * LIST_MARGIN
        for i, row in enumerate(self._rows):
            row.setGeometry(LIST_MARGIN, LIST_MARGIN + i * STEP, row_w, ROW_H)
        if self._rows:
            height = LIST_MARGIN * 2 + len(self._rows) * ROW_H + (len(self._rows) - 1) * ROW_GAP
            self._empty_label.setGeometry(0, 0, 0, 0)
            self._empty_label.hide()
        else:
            height = 72
            self._empty_label.setGeometry(0, 0, w, height)
            self._empty_label.show()
            self._empty_label.raise_()
        self.setMinimumHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._dragging:
            # 拖拽中只更新宽度，不重排纵向位置
            row_w = max(self.width(), 120) - 2 * LIST_MARGIN
            for row in self._rows:
                row.resize(row_w, ROW_H)
                row.move(LIST_MARGIN, row.y())
        else:
            self._relayout()

    def _slot_y(self, index):
        return LIST_MARGIN + index * STEP

    # ------------------------------------------------------------------
    # 拖拽（实时让位）
    # ------------------------------------------------------------------
    def begin_press(self, row, gpos):
        self._press_row = row
        self._press_gpos = gpos

    def press_move(self, row, gpos):
        if row is not self._press_row or self._press_gpos is None:
            return
        if not self._dragging:
            if abs(gpos.y() - self._press_gpos.y()) < DRAG_THRESHOLD:
                return
            self._start_drag(row, gpos)

        # 跟手：容器坐标 = 光标全局位置 - 起点偏移（滚动时依然准确）
        n = len(self._rows)
        y = self.mapFromGlobal(gpos).y() - self._grab_dy
        y = max(LIST_MARGIN, min(y, self._slot_y(n - 1)))
        row.move(LIST_MARGIN, y)

        # 越过邻居中点（最近槽位）→ 邻居滑开让位
        idx = max(0, min(round((y - LIST_MARGIN) / STEP), n - 1))
        if idx != self._rows.index(row):
            self._reorder_live(row, idx)

        self._autoscroll(gpos)

    def _start_drag(self, row, gpos):
        self._dragging = True
        self._drag_row = row
        # 以"按下那一刻"的光标偏移为基准（而不是已越过阈值的当前位置），
        # 否则拖拽行会整体滞后一个阈值位移，跟手感变差
        self._grab_dy = self.mapFromGlobal(self._press_gpos).y() - row.y()
        row.setProperty("dragging", True)
        _repolish(row)
        row.raise_()
        row.setCursor(Qt.CursorShape.ClosedHandCursor)
        # 抬起投影：让被拖行明显"浮"在其他行之上
        shadow = QGraphicsDropShadowEffect(row)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 120))
        row.setGraphicsEffect(shadow)
        row.grabMouse()   # 移出行范围后事件仍派发给该行

    def _reorder_live(self, row, target_idx):
        cur = self._rows.index(row)
        self._rows.pop(cur)
        self._rows.insert(target_idx, row)
        for i, r in enumerate(self._rows):
            if r is row:
                continue
            target_y = self._slot_y(i)
            if r.y() == target_y:
                continue
            anim = self._shift_anims.get(r)
            if anim is None:
                anim = QPropertyAnimation(r, b"pos", r)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._shift_anims[r] = anim
            anim.stop()
            anim.setDuration(SHIFT_MS)
            anim.setStartValue(QPoint(r.x(), r.y()))
            anim.setEndValue(QPoint(r.x(), target_y))
            anim.start()

    def end_press(self, row, gpos):
        if row is not self._press_row:
            return
        self._press_row = None
        self._press_gpos = None
        if not self._dragging:
            return
        self._dragging = False
        self._drag_row = None
        row.releaseMouse()
        row.setProperty("dragging", False)
        _repolish(row)
        row.setCursor(Qt.CursorShape.ArrowCursor)
        row.setGraphicsEffect(None)

        # 数据先落盘（信号只刷新小卡片导航页，不影响本面板），再做视觉滑入
        self._panel.commit_order([r.nav_id for r in self._rows])

        anim = QPropertyAnimation(row, b"pos", row)
        anim.setDuration(SETTLE_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(QPoint(row.x(), row.y()))
        anim.setEndValue(QPoint(LIST_MARGIN, self._slot_y(self._rows.index(row))))
        anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _abort_drag(self):
        """外部强制重建行时（如 refresh），把拖拽状态复位"""
        row = self._drag_row
        self._dragging = False
        self._drag_row = None
        self._press_row = None
        self._press_gpos = None
        if row is None:
            return
        try:
            row.releaseMouse()
        except RuntimeError:
            pass
        row.setProperty("dragging", False)
        _repolish(row)
        row.setCursor(Qt.CursorShape.ArrowCursor)
        row.setGraphicsEffect(None)
        row.move(LIST_MARGIN, self._slot_y(self._rows.index(row) if row in self._rows else 0))

    def _autoscroll(self, gpos):
        """拖到视口上下边缘时缓慢滚动"""
        area = self._panel._nav_scroll
        vp = area.viewport()
        top = vp.mapToGlobal(QPoint(0, 0)).y()
        bottom = top + vp.height()
        sb = area.verticalScrollBar()
        if gpos.y() < top + 28:
            sb.setValue(sb.value() - 8)
        elif gpos.y() > bottom - 28:
            sb.setValue(sb.value() + 8)

    # ------------------------------------------------------------------
    # 业务转发
    # ------------------------------------------------------------------
    def open_requested(self, url):
        self._panel._open_url(url)

    def context_requested(self, row, gpos):
        self._panel._show_row_menu(row.nav_id, gpos)


class NavPanel(QWidget):
    """网址导航面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._nav_manager = host._nav_manager
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = QLabel("🌐 网址导航")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._nav_count_label = QLabel("共 0 个站点")
        self._nav_count_label.setObjectName("hintLabel")
        header.addWidget(self._nav_count_label)
        v.addLayout(header)

        # ---- 添加站点行 ----
        add_row = QHBoxLayout()
        add_row.setSpacing(6)

        self._nav_title_input = QLineEdit()
        self._nav_title_input.setPlaceholderText("站点名称...")
        self._nav_url_input = QLineEdit()
        self._nav_url_input.setPlaceholderText("URL（如 baidu.com，自动补全 https://）")

        nav_add_btn = QPushButton("添加站点")
        nav_add_btn.setObjectName("taskAddBtn")
        nav_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nav_add_btn.clicked.connect(self._on_add_site)

        add_row.addWidget(self._nav_title_input, 1)
        add_row.addWidget(self._nav_url_input, 2)
        add_row.addWidget(nav_add_btn)
        v.addLayout(add_row)

        # ---- 站点列表（滚动区 + 动画行容器）----
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_list = _NavList(self)
        self._nav_scroll.setWidget(self._nav_list)
        v.addWidget(self._nav_scroll, 1)

        # ---- 底部提示 ----
        hint = QLabel("右键站点：打开 / 编辑 / 删除 | 拖拽行可排序 | URL 自动补全 http/https")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

    # ---- 拖拽排序落盘（由 _NavList 在松手时调用）----
    def commit_order(self, ordered_ids):
        if not self._nav_manager:
            return
        if self._nav_manager.reorder_sites_flat(ordered_ids):
            self._host.data_changed.emit("nav")

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新网址导航面板（简化版，平铺所有站点）"""
        if not self._nav_manager:
            return
        sites = self._nav_manager.get_all_sites_flat()
        self._nav_list.set_sites(sites)
        self._nav_count_label.setText(f"共 {len(sites)} 个站点")

    def _on_add_site(self):
        """添加站点（简化版，自动放入默认分组）"""
        if not self._nav_manager:
            return
        title = self._nav_title_input.text().strip()
        url = self._nav_url_input.text().strip()
        if not title or not url:
            QMessageBox.warning(self, "提示", "请填写站点名称和 URL")
            return
        self._nav_manager.add_site_simple(title, url)
        self._nav_title_input.clear()
        self._nav_url_input.clear()
        self.refresh()
        self._host.data_changed.emit("nav")

    def _show_row_menu(self, nav_id, gpos):
        if not self._nav_manager:
            return
        g, site = self._nav_manager.find_site_by_id(nav_id)
        if not site:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_open = menu.addAction("🌐 打开")
        act_edit = menu.addAction("✏️ 编辑...")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除")

        action = menu.exec(gpos)
        if action == act_open:
            self._open_url(site.url)
        elif action == act_edit:
            self._edit_site(site)
        elif action == act_delete:
            self._nav_manager.delete_site_simple(nav_id)
            self.refresh()
            self._host.data_changed.emit("nav")

    def _edit_site(self, site):
        """编辑站点（简化版，自动查找所在分组）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑站点")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setFixedSize(360, 160)

        form = QFormLayout(dialog)
        form.setContentsMargins(20, 20, 20, 16)
        form.setSpacing(10)

        title_edit = QLineEdit(site.title)
        url_edit = QLineEdit(site.url)

        form.addRow("标题:", title_edit)
        form.addRow("URL:", url_edit)

        form.addRow(make_dialog_buttons(dialog))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._nav_manager.update_site_simple(site.nav_id, title_edit.text(), url_edit.text())
            self.refresh()
            self._host.data_changed.emit("nav")

    def _open_url(self, url: str):
        """用系统默认浏览器打开 URL（优先 os.startfile，回退 QDesktopServices）"""
        if not url:
            return
        try:
            os.startfile(url)
            return
        except Exception:
            pass
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass
