# -*- coding: utf-8 -*-
"""
====================================================================
临时素材面板  -  AssetsPanel
====================================================================
从 main_window.py 抽出的独立面板，承载临时素材列表 / 双击打开 /
右键删除·另存为 / 清空 / 打开素材文件夹等业务逻辑。
通过 host（MainWindow）访问临时素材管理器与样式。
"""

import os
import shutil

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt

from src.constants import DATETIME_MIN_LEN


class AssetsPanel(QWidget):
    """临时素材面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._temp_asset_manager = host._temp_asset_manager
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = QLabel("📎 临时素材")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._asset_count_label = QLabel("共 0 条 / 上限 10")
        self._asset_count_label.setObjectName("hintLabel")
        header.addWidget(self._asset_count_label)
        v.addLayout(header)

        # ---- 提示 ----
        hint = QLabel("💡 拖拽图片/文件到悬浮球即可自动复制保存到此处")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        open_folder_btn = QPushButton("📁 打开素材文件夹")
        open_folder_btn.setObjectName("secondaryBtn")
        open_folder_btn.clicked.connect(self._on_open_folder)
        toolbar.addWidget(open_folder_btn)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        toolbar.addStretch()

        clear_btn = QPushButton("🗑 清空全部")
        clear_btn.setObjectName("dangerBtn")
        clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(clear_btn)

        v.addLayout(toolbar)

        # ---- 素材列表 ----
        self._asset_list = QListWidget()
        self._asset_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._asset_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._asset_list.customContextMenuRequested.connect(self._on_context_menu)
        self._asset_list.itemDoubleClicked.connect(self._on_double_click)
        v.addWidget(self._asset_list, 1)

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新临时素材列表显示"""
        if self._temp_asset_manager is None:
            return
        self._temp_asset_manager.refresh()

        self._asset_list.clear()
        assets = self._temp_asset_manager.get_all_assets()
        for a in assets:
            icon = "🖼️" if a.is_image else "📄"
            size_str = a.size_display()
            added = a.added_time or ""
            time_display = added[:DATETIME_MIN_LEN] if len(added) >= DATETIME_MIN_LEN else added
            text = f"{icon}  {a.original_name}   · {size_str}   · {time_display}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, a.asset_id)
            tip = (f"原文件名: {a.original_name}\n"
                   f"类型: {'图片' if a.is_image else '文件'}\n"
                   f"大小: {size_str}\n"
                   f"添加时间: {added}\n"
                   f"存储路径: {a.stored_path}")
            item.setToolTip(tip)
            self._asset_list.addItem(item)

        count = self._temp_asset_manager.count()
        max_assets = self._temp_asset_manager._max_assets
        self._asset_count_label.setText(f"共 {count} 条 / 上限 {max_assets}")

    def _on_context_menu(self, pos):
        item = self._asset_list.itemAt(pos)
        if not item:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_open = menu.addAction("📂 打开")
        act_save_as = menu.addAction("💾 另存为...")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除")
        action = menu.exec(self._asset_list.mapToGlobal(pos))
        if action == act_open:
            self._temp_asset_manager.open_asset(aid)
        elif action == act_save_as:
            self._save_as(aid)
        elif action == act_delete:
            if self._temp_asset_manager.delete_asset(aid):
                self.refresh()
                self._host.data_changed.emit("asset")

    def _on_double_click(self, item):
        """双击素材 → 用系统默认程序打开"""
        aid = item.data(Qt.ItemDataRole.UserRole)
        if not self._temp_asset_manager.open_asset(aid):
            QMessageBox.warning(self, "打开失败", "无法打开此素材，文件可能已被删除。")

    def _save_as(self, asset_id: int):
        """另存为：用 QFileDialog 选目标位置，复制一份过去"""
        asset = self._temp_asset_manager.get_asset(asset_id)
        if not asset:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "另存为", asset.original_name, "All Files (*.*)"
        )
        if not target:
            return
        try:
            shutil.copy2(asset.stored_path, target)
            QMessageBox.information(self, "已另存为", f"文件已保存到：\n{target}")
        except OSError as e:
            QMessageBox.warning(self, "另存失败", f"另存失败：{e}")

    def _on_open_folder(self):
        """在资源管理器中打开 temp_assets 文件夹"""
        if self._temp_asset_manager is None:
            return
        folder = self._temp_asset_manager._assets_dir
        try:
            os.startfile(folder)
        except OSError:
            QMessageBox.warning(self, "打开失败", f"无法打开文件夹：\n{folder}")

    def _on_clear(self):
        """清空所有临时素材"""
        if self._temp_asset_manager is None:
            return
        count = self._temp_asset_manager.count()
        if count == 0:
            QMessageBox.information(self, "提示", "当前没有临时素材。")
            return
        ret = QMessageBox.question(
            self, "确认清空",
            f"确认清空所有 {count} 个临时素材？\n（仅删除程序目录下的副本，不影响原文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            cleared = self._temp_asset_manager.clear_all()
            self.refresh()
            self._host.data_changed.emit("asset")
            QMessageBox.information(self, "已清空", f"已清理 {cleared} 个临时素材。")
