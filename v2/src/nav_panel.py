# -*- coding: utf-8 -*-
"""
====================================================================
网址导航面板  -  NavPanel
====================================================================
从 main_window.py 抽出的独立面板，承载网址站点增删改 / 排序 /
右键菜单 / 浏览器打开等业务逻辑。
通过 host（MainWindow）访问导航管理器与样式。
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QTableWidget, QTableWidgetItem, QMenu, QDialog,
    QFormLayout, QDialogButtonBox, QMessageBox, QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices


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

        # ---- 站点列表 ----
        self._nav_table = QTableWidget()
        self._nav_table.setColumnCount(3)
        self._nav_table.setHorizontalHeaderLabels(["标题", "URL", "操作"])
        self._nav_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._nav_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._nav_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._nav_table.horizontalHeader().resizeSection(2, 90)
        self._nav_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._nav_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._nav_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._nav_table.customContextMenuRequested.connect(self._on_context_menu)
        self._nav_table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        # 拖拽排序落盘：InternalMove 只改视觉行序，必须回写数据层，
        # 否则下一次 refresh() 会按原数据重建，表现为"拖完就复位"
        self._nav_table.model().rowsMoved.connect(self._on_rows_moved)
        v.addWidget(self._nav_table, 1)

        # ---- 底部提示 ----
        hint = QLabel("右键站点：打开 / 编辑 / 删除 | 拖拽行可排序 | URL 自动补全 http/https")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

    # ---- 拖拽排序 ----
    def _on_rows_moved(self, parent, start, end, dest, row):
        """表格拖拽结束：把新的行顺序写回 NavManager，并按真实数据重绘。

        setCellWidget 放置的「打开」按钮不会跟随行移动，因此必须重绘一次，
        保证按钮绑定的 URL 与当前行一致。
        """
        if not self._nav_manager:
            return
        ordered_ids = []
        for r in range(self._nav_table.rowCount()):
            item = self._nav_table.item(r, 0)
            if item is not None:
                ordered_ids.append(item.data(Qt.ItemDataRole.UserRole))
        if self._nav_manager.reorder_sites_flat(ordered_ids):
            self._host.data_changed.emit("nav")
        self.refresh()

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新网址导航面板（简化版，平铺所有站点）"""
        if not self._nav_manager:
            return
        sites = self._nav_manager.get_all_sites_flat()

        self._nav_table.setRowCount(len(sites))
        for row, site in enumerate(sites):
            title_item = QTableWidgetItem(site.title)
            title_item.setData(Qt.ItemDataRole.UserRole, site.nav_id)
            url_item = QTableWidgetItem(site.url)
            open_btn = QPushButton("打开")
            open_btn.setObjectName("tableOpenBtn")
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.setFixedHeight(28)
            open_btn.clicked.connect(lambda checked=False, url=site.url: self._open_url(url))
            self._nav_table.setItem(row, 0, title_item)
            self._nav_table.setItem(row, 1, url_item)
            self._nav_table.setCellWidget(row, 2, open_btn)

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

    def _on_context_menu(self, pos):
        if not self._nav_manager:
            return
        item = self._nav_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        nav_id = self._nav_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        g, site = self._nav_manager.find_site_by_id(nav_id)
        if not site:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_open = menu.addAction("🌐 打开")
        act_edit = menu.addAction("✏️ 编辑...")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除")

        action = menu.exec(self._nav_table.mapToGlobal(pos))
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

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        form.addRow(btns)

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
