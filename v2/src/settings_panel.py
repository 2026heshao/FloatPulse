# -*- coding: utf-8 -*-
"""
====================================================================
设置面板  -  SettingsPanel
====================================================================
从 main_window.py 抽出的独立面板，承载主题 / 行为配置 / 关于 /
日志查看等设置项 UI 与交互逻辑。
通过 host（MainWindow）访问配置管理器、主题切换、各业务信号与
软件导航页面实例。
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QGridLayout, QCheckBox, QSlider,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from src.controls import Stepper


class SettingsPanel(QWidget):
    """设置面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._config = host._config
        self._build_ui()

    # ---------------- 小工具 ----------------
    @staticmethod
    def _field_label(text: str, width: int = 108) -> QLabel:
        """表单左侧的字段名（次级文字色，比正文轻一档）"""
        lab = QLabel(text)
        lab.setObjectName("fieldLabel")
        lab.setFixedWidth(width)
        return lab

    @staticmethod
    def _group_box() -> QWidget:
        """设置分组卡片：半透明内嵌面板（QSS 见 theme.py 的 #settingsGroup）"""
        box = QWidget()
        box.setObjectName("settingsGroup")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return box

    @staticmethod
    def _value_chip(text: str, width: int = 52) -> QLabel:
        """数值胶囊（滑杆右侧显示当前值）"""
        chip = QLabel(text)
        chip.setObjectName("valueChip")
        chip.setFixedWidth(width)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return chip

    def _build_ui(self):
        page = self
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(8)

        title = QLabel("⚙️ 设置")
        title.setObjectName("pageTitle")
        v.addWidget(title)

        # ---- 主题设置 ----
        theme_box = self._group_box()
        tb_v = QVBoxLayout(theme_box)
        tb_v.setContentsMargins(14, 10, 14, 12)
        tb_v.setSpacing(8)

        theme_title = QLabel("🎨 主题外观")
        theme_title.setObjectName("sectionLabel")
        tb_v.addWidget(theme_title)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        self._set_theme_light = QPushButton("☀️ 浅色主题")
        self._set_theme_light.setObjectName("secondaryBtn")
        self._set_theme_light.setCheckable(True)
        self._set_theme_light.setFixedHeight(30)
        self._set_theme_light.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_theme_light.clicked.connect(lambda: self._on_set_theme("light"))
        theme_row.addWidget(self._set_theme_light)

        self._set_theme_dark = QPushButton("🌙 深色主题")
        self._set_theme_dark.setObjectName("secondaryBtn")
        self._set_theme_dark.setCheckable(True)
        self._set_theme_dark.setFixedHeight(30)
        self._set_theme_dark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_theme_dark.clicked.connect(lambda: self._on_set_theme("dark"))
        theme_row.addWidget(self._set_theme_dark)
        theme_row.addStretch()
        tb_v.addLayout(theme_row)

        v.addWidget(theme_box)

        # ---- 行为设置 ----
        behavior_box = self._group_box()
        bb_v = QVBoxLayout(behavior_box)
        bb_v.setContentsMargins(14, 10, 14, 12)
        bb_v.setSpacing(10)

        behavior_title = QLabel("⚙️ 行为配置")
        behavior_title.setObjectName("sectionLabel")
        bb_v.addWidget(behavior_title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 1)

        self._set_clipboard_max = Stepper(10, 10000,
                                          self._config.get("clipboard_max_items", 200),
                                          suffix="条", step=10)
        form.addWidget(self._field_label("剪贴板历史上限:"), 0, 0)
        form.addWidget(self._set_clipboard_max, 0, 1)

        self._set_auto_hide = Stepper(1, 60,
                                      self._config.get("auto_hide_seconds", 3),
                                      suffix="秒")
        form.addWidget(self._field_label("悬浮球自动隐藏:"), 1, 0)
        form.addWidget(self._set_auto_hide, 1, 1)

        self._set_ball_visible = QCheckBox("显示悬浮球")
        self._set_ball_visible.setChecked(self._config.get("ball_visible", True))
        self._set_ball_visible.stateChanged.connect(self._on_ball_visibility_changed)
        form.addWidget(self._set_ball_visible, 2, 1)

        self._set_card_always_show = QCheckBox("小卡片保持显示（不自动关闭）")
        self._set_card_always_show.setChecked(self._config.get("card_always_show", False))
        self._set_card_always_show.stateChanged.connect(self._on_card_always_show_preview)
        form.addWidget(self._set_card_always_show, 3, 1)

        self._set_temp_asset_max_count = Stepper(
            5, 500, self._config.get("temp_asset_max_count", 50),
            suffix="个", step=5)
        form.addWidget(self._field_label("临时素材上限:"), 4, 0)
        form.addWidget(self._set_temp_asset_max_count, 4, 1)

        self._set_temp_asset_max_days = Stepper(
            0, 365, self._config.get("temp_asset_max_days", 30), suffix="天")
        self._set_temp_asset_max_days.setToolTip("0 表示不按天数自动清理")
        form.addWidget(self._field_label("素材保留天数:"), 5, 0)
        form.addWidget(self._set_temp_asset_max_days, 5, 1)

        # 数字类设置即时持久化（任务 6.2）：改动即写盘，不依赖保存按钮
        self._set_clipboard_max.valueChanged.connect(self._on_spin_changed)
        self._set_auto_hide.valueChanged.connect(self._on_spin_changed)
        self._set_temp_asset_max_count.valueChanged.connect(self._on_spin_changed)
        self._set_temp_asset_max_days.valueChanged.connect(self._on_spin_changed)

        self._set_card_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._set_card_size_slider.setRange(60, 140)
        self._set_card_size_slider.setValue(self._config.get("app_card_size", 96))
        self._set_card_size_slider.setFixedHeight(24)
        self._set_card_size_slider.setMinimumWidth(140)
        self._set_card_size_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_card_size_slider.setToolTip("调整软件导航页面的卡片大小，实时生效")
        self._set_card_size_slider.valueChanged.connect(self._on_card_size_changed)

        self._set_card_size_label = self._value_chip(
            f"{self._set_card_size_slider.value()}px")

        cs_row = QHBoxLayout()
        cs_row.setSpacing(8)
        cs_row.addWidget(self._set_card_size_slider, 1)
        cs_row.addWidget(self._set_card_size_label)
        form.addWidget(self._field_label("软件卡片尺寸:"), 6, 0)
        form.addLayout(cs_row, 6, 1)

        self._set_anim_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._set_anim_speed_slider.setRange(50, 200)
        init_speed = self._config.get("anim_speed", 1.0)
        self._set_anim_speed_slider.setValue(int(round(max(0.5, min(2.0, init_speed)) * 100)))
        self._set_anim_speed_slider.setFixedHeight(24)
        self._set_anim_speed_slider.setMinimumWidth(140)
        self._set_anim_speed_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_anim_speed_slider.setToolTip("悬浮球动画速度档位：0.5 慢速 - 2.0 快速，实时生效")
        self._set_anim_speed_slider.valueChanged.connect(self._on_anim_speed_changed)

        self._set_anim_speed_label = self._value_chip(
            f"{self._set_anim_speed_slider.value() / 100.0:.1f}x")

        as_row = QHBoxLayout()
        as_row.setSpacing(8)
        as_row.addWidget(self._set_anim_speed_slider, 1)
        as_row.addWidget(self._set_anim_speed_label)
        form.addWidget(self._field_label("动画速度:"), 7, 0)
        form.addLayout(as_row, 7, 1)

        bb_v.addLayout(form)

        save_btn = QPushButton("💾 保存配置")
        save_btn.setObjectName("primaryBtn")
        save_btn.setFixedHeight(32)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        bb_v.addWidget(save_btn)

        v.addWidget(behavior_box)

        about_box = self._group_box()
        ab_v = QVBoxLayout(about_box)
        ab_v.setContentsMargins(14, 10, 14, 12)
        ab_v.setSpacing(6)

        about_title = QLabel("ℹ️ 关于")
        about_title.setObjectName("sectionLabel")
        ab_v.addWidget(about_title)

        about_text = QLabel(
            "生活悬浮球 v2.0 | PyQt6 + python-docx\n"
            "功能：知识卡片 / 日程任务 / 临时笔记 / 碎片合并\n\n"
            "快捷键：Esc 退出 | Ctrl+W/Q 隐藏 | Ctrl+T 主题 | F1 帮助 | Ctrl+1~7 切换面板\n"
            "右键悬浮球 / 右键卡片也可退出"
        )
        about_text.setObjectName("hintLabel")
        about_text.setWordWrap(True)
        ab_v.addWidget(about_text)

        v.addWidget(about_box)

        v.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新设置面板当前值"""
        self._set_theme_light.setChecked(self._host.current_theme == "light")
        self._set_theme_dark.setChecked(self._host.current_theme == "dark")
        self._set_clipboard_max.setValue(self._config.get("clipboard_max_items", 200))
        self._set_auto_hide.setValue(self._config.get("auto_hide_seconds", 3))
        if hasattr(self, '_set_temp_asset_max_count'):
            self._set_temp_asset_max_count.setValue(
                self._config.get("temp_asset_max_count", 50))
        if hasattr(self, '_set_temp_asset_max_days'):
            self._set_temp_asset_max_days.setValue(
                self._config.get("temp_asset_max_days", 30))
        if hasattr(self, '_set_card_always_show'):
            self._set_card_always_show.setChecked(self._config.get("card_always_show", False))
        if hasattr(self, '_set_ball_visible'):
            self._set_ball_visible.setChecked(self._config.get("ball_visible", True))
        if hasattr(self, '_set_card_size_slider'):
            self._set_card_size_slider.blockSignals(True)
            self._set_card_size_slider.setValue(self._config.get("app_card_size", 96))
            self._set_card_size_slider.blockSignals(False)
            self._set_card_size_label.setText(f"{self._set_card_size_slider.value()}px")
        if hasattr(self, '_set_anim_speed_slider'):
            self._set_anim_speed_slider.blockSignals(True)
            sp = self._config.get("anim_speed", 1.0)
            self._set_anim_speed_slider.setValue(int(round(max(0.5, min(2.0, sp)) * 100)))
            self._set_anim_speed_slider.blockSignals(False)
            self._set_anim_speed_label.setText(f"{self._set_anim_speed_slider.value() / 100.0:.1f}x")

    def _on_set_theme(self, theme_name: str):
        """设置面板切换主题"""
        self._host.apply_external_theme(theme_name)
        self.refresh()

    def _on_spin_changed(self):
        """
        四个数字设置（剪贴板上限/自动隐藏秒数/素材上限/素材天数）变更时
        即时持久化到磁盘（任务 6.2），无需依赖「保存配置」按钮。
        仅当值相对当前配置发生实际变化时才写入并广播，避免无意义写盘。
        """
        changed = False

        clip = int(self._set_clipboard_max.value())
        if clip != self._config.get("clipboard_max_items", 200):
            self._config.set("clipboard_max_items", clip)
            changed = True

        hide = int(self._set_auto_hide.value())
        if hide != self._config.get("auto_hide_seconds", 3):
            self._config.set("auto_hide_seconds", hide)
            changed = True

        max_count = int(self._set_temp_asset_max_count.value())
        max_days = int(self._set_temp_asset_max_days.value())
        old_count = self._config.get("temp_asset_max_count", 50)
        old_days = self._config.get("temp_asset_max_days", 30)
        if max_count != old_count or max_days != old_days:
            self._config.set("temp_asset_max_count", max_count)
            self._config.set("temp_asset_max_days", max_days)
            changed = True

        if changed:
            self._config.save()
            # 广播联动（与原「保存配置」按钮行为一致）
            if hide != self._config.get("auto_hide_seconds", 3):
                pass  # 已在上面 set 完成
            if max_count != old_count or max_days != old_days:
                self._host.asset_limits_changed.emit(max_count, max_days)
            self._host.auto_hide_seconds_changed.emit(hide)

    def _on_save(self):
        """保存配置"""
        self._config.set("clipboard_max_items", self._set_clipboard_max.value())
        self._config.set("auto_hide_seconds", self._set_auto_hide.value())
        new_always_show = self._set_card_always_show.isChecked()
        old_always_show = self._config.get("card_always_show", False)
        self._config.set("card_always_show", new_always_show)
        self._config.set("ball_visible", self._set_ball_visible.isChecked())
        new_max_count = self._set_temp_asset_max_count.value()
        new_max_days = self._set_temp_asset_max_days.value()
        old_max_count = self._config.get("temp_asset_max_count", 50)
        old_max_days = self._config.get("temp_asset_max_days", 30)
        self._config.set("temp_asset_max_count", new_max_count)
        self._config.set("temp_asset_max_days", new_max_days)
        self._config.save()
        if new_always_show != old_always_show:
            self._host.card_always_show_changed.emit(new_always_show)
        if new_max_count != old_max_count or new_max_days != old_max_days:
            self._host.asset_limits_changed.emit(new_max_count, new_max_days)
        self._host.auto_hide_seconds_changed.emit(int(self._set_auto_hide.value()))
        QMessageBox.information(self, "已保存", "配置已保存，部分设置需重启生效。")

    def _on_ball_visibility_changed(self, state):
        """悬浮球显示/隐藏切换（立即生效并持久化）"""
        is_visible = (state == Qt.CheckState.Checked.value)
        old = self._config.get("ball_visible", True)
        if is_visible != old:
            self._config.set("ball_visible", is_visible)
            self._config.save()
        self._host.ball_visibility_changed.emit(is_visible)

    def _on_card_always_show_preview(self, state):
        """勾选/取消勾选时实时预览效果（无需点保存即生效）"""
        always_show = bool(state)
        old = self._config.get("card_always_show", False)
        if always_show != old:
            self._config.set("card_always_show", always_show)
            self._config.save()
            self._host.card_always_show_changed.emit(always_show)

    def _on_card_size_changed(self, value: int):
        """卡片尺寸滑动条拖动时实时处理并持久化、刷新导航页卡片"""
        self._set_card_size_label.setText(f"{value}px")
        self._config.set("app_card_size", int(value))
        self._config.save()
        if self._host._page_app_launcher is not None:
            self._host._page_app_launcher.apply_card_size(int(value))

    def _on_anim_speed_changed(self, value: int):
        """动画速度滑动条拖动时实时处理并广播到悬浮球"""
        speed = value / 100.0
        self._set_anim_speed_label.setText(f"{speed:.1f}x")
        self._config.set("anim_speed", round(speed, 2))
        self._config.save()
        self._host.anim_speed_changed.emit(round(speed, 2))
