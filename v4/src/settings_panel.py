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
    QScrollArea, QFrame,
    QLineEdit,
    QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt

from src.controls import Stepper, ToggleSwitch
from src.glass_dialog import make_separator
from src import autostart


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

    def _group_card(self, parent_v, title: str):
        """创建设置分组卡片（标题 + 行容器），返回其内部竖直布局。

        分组只负责视觉边界与标题，行内容仍由 ``_add_row`` 追加，
        所有分组的行样式因此完全一致；调用方拿到返回的 vbox 直接加行即可。
        """
        box = self._group_box()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(14, 10, 14, 12)
        bv.setSpacing(0)
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        bv.addWidget(label)
        gap = QWidget()
        gap.setFixedHeight(6)
        bv.addWidget(gap)
        parent_v.addWidget(box)
        return bv

    def _toggle(self, key: str, default: bool) -> ToggleSwitch:
        """创建跟随主题的开关并登记（主题切换时统一 set_theme）"""
        sw = ToggleSwitch(self._config.get(key, default),
                          theme=self._host.current_theme)
        self._toggles.append(sw)
        return sw

    def _add_row(self, vbox, title: str, desc: str, widget, tip: str = "",
                 last: bool = False):
        """行式设置项：左侧「标题 + 描述」，右侧控件（垂直居中），行间细分隔线。

        形式对齐参考设计稿（2026-09-24）：勾选类用开关、数字类用步进器，
        全部右对齐；说明文字从 tooltip 提升为可见的描述行。

        ``last=True`` 表示这是所在分组的最后一行——不再追加分隔线，
        否则分组底部会多出一条悬空横线（卡片边框本身已提供视觉收束）。
        """
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 7, 0, 7)
        h.setSpacing(12)
        text_v = QVBoxLayout()
        text_v.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("settingTitle")
        d = QLabel(desc)
        d.setObjectName("settingDesc")
        d.setWordWrap(True)
        text_v.addWidget(t)
        text_v.addWidget(d)
        h.addLayout(text_v, 1)
        if tip:
            widget.setToolTip(tip)
        h.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter)
        vbox.addWidget(row)
        if not last:
            vbox.addWidget(make_separator())

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

        page_title = QLabel("⚙️ 设置")
        page_title.setObjectName("pageTitle")
        v.addWidget(page_title)

        # 分组顺序：外观 → 悬浮球 → 剪贴板 → 素材 → 全局工具 → 启动系统
        # 组内行顺序 = 功能亲密度（同组相邻项最常被一起调整）
        self._toggles = []
        add_row = self._add_row
        group = self._group_card

        # ================= 1. 外观与主题 =================
        gv = group(v, "🎨 外观与主题")

        theme_ctl = QWidget()
        theme_row = QHBoxLayout(theme_ctl)
        theme_row.setContentsMargins(0, 0, 0, 0)
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
        add_row(gv, "主题外观", "浅色 / 深色两套配色，切换即时生效", theme_ctl)

        # anim_speed 存浮点（0.5~2.0），Stepper 内部用整数 50~200，
        # divisor=100 / decimals=1 → 显示「1.3」，对外仍发内部整数。
        init_speed = self._config.get("anim_speed", 1.0)
        init_ticks = int(round(max(0.5, min(2.0, init_speed)) * 100))
        self._set_anim_speed = Stepper(50, 200, init_ticks,
                                       suffix="x", step=10,
                                       divisor=100, decimals=1)
        self._set_anim_speed.valueChanged.connect(self._on_anim_speed_changed)
        add_row(gv, "动画速度", "悬浮球与勾选动画的统一倍速，每档 0.1x",
                self._set_anim_speed)

        # 数值类用增减按钮（Stepper）而非滑条 ——
        # 滑条会在鼠标滚设置页时被滚轮静默改值，步进器只在数值框聚焦时才吃滚轮。
        self._set_card_size = Stepper(60, 140, self._config.get("app_card_size", 96),
                                      suffix="px", step=4)
        self._set_card_size.valueChanged.connect(self._on_card_size_changed)
        add_row(gv, "软件卡片尺寸", "导航页卡片大小，每档 4px，可长按 ± 连续调整",
                self._set_card_size, last=True)

        # ================= 2. 悬浮球 =================
        gv = group(v, "🔵 悬浮球")

        self._set_ball_visible = self._toggle("ball_visible", True)
        self._set_ball_visible.toggled.connect(self._on_ball_visibility_changed)
        add_row(gv, "显示悬浮球", "桌面上的球体入口，隐藏后可从系统托盘唤回",
                self._set_ball_visible)

        # 悬浮球大小（C1）：改球径时保持球心不动，投影留白自动适配
        self._set_ball_size = Stepper(48, 88, self._config.get("ball_size", 64),
                                      suffix="px", step=8)
        self._set_ball_size.valueChanged.connect(self._on_ball_size_changed)
        add_row(gv, "悬浮球大小", "球体直径，每档 8px，调整时保持球心不动",
                self._set_ball_size)

        # 自动隐藏总开关（本项为秒数的上位开关，关闭时秒数行灰化）
        self._set_auto_hide_enabled = self._toggle("auto_hide_enabled", True)
        self._set_auto_hide_enabled.toggled.connect(self._on_auto_hide_enabled_changed)
        add_row(gv, "自动隐藏", "贴边静止一段时间后半隐藏；关闭后球始终完整显示",
                self._set_auto_hide_enabled)

        self._set_auto_hide = Stepper(1, 60,
                                      self._config.get("auto_hide_seconds", 3),
                                      suffix="秒")
        self._set_auto_hide.valueChanged.connect(self._on_spin_changed)
        add_row(gv, "自动隐藏延迟", "贴边静止多少秒后开始半隐藏，鼠标靠近即恢复",
                self._set_auto_hide)
        self._sync_auto_hide_rows()   # 按开关初值决定秒数行是否可编辑

        # 全屏应用让位（B8）：全屏视频/游戏/演示时不与画面争抢注意力
        self._set_hide_fullscreen = self._toggle("hide_on_fullscreen", True)
        self._set_hide_fullscreen.toggled.connect(self._on_hide_fullscreen_changed)
        add_row(gv, "全屏应用让位", "检测到全屏应用时自动隐藏悬浮球，退出后恢复",
                self._set_hide_fullscreen)

        self._set_card_always_show = self._toggle("card_always_show", False)
        self._set_card_always_show.toggled.connect(self._on_card_always_show_preview)
        add_row(gv, "小卡片保持显示", "卡片不自动关闭，常驻在悬浮球旁",
                self._set_card_always_show)

        # 悬浮球外置插件总闸：关掉后插件动作不挂菜单、不绑热键（模块仍驻留）
        self._set_plugins = self._toggle("plugins_enabled", True)
        self._set_plugins.toggled.connect(self._on_plugins_changed)
        add_row(gv, "悬浮球插件", "启用 plugins/ 目录里的外置插件包；"
                                  "新放入的插件包需重启程序",
                self._set_plugins, last=True)

        # ================= 3. 剪贴板与碎片 =================
        gv = group(v, "📋 剪贴板与碎片")

        self._set_clipboard_max = Stepper(10, 10000,
                                          self._config.get("clipboard_max_items", 200),
                                          suffix="条", step=10)
        self._set_clipboard_max.valueChanged.connect(self._on_spin_changed)
        add_row(gv, "剪贴板历史上限", "达到上限后自动清理最早的碎片",
                self._set_clipboard_max)

        self._set_clipboard_filter = QLineEdit()
        apps = self._config.get("clipboard_filter_apps", []) or []
        self._set_clipboard_filter.setText(", ".join(str(a) for a in apps))
        self._set_clipboard_filter.setPlaceholderText("如：WeChat, Weixin, chrome")
        self._set_clipboard_filter.setMinimumWidth(190)
        self._set_clipboard_filter.editingFinished.connect(self._on_clipboard_filter_changed)
        add_row(gv, "剪贴板过滤", "这些进程里的复制不会进碎片池（逗号分隔，即时生效）",
                self._set_clipboard_filter)

        # 剪贴板图片自动入素材池（Y2）：纯图片复制（截图/复制图片）时生效
        self._set_clipboard_images = self._toggle("clipboard_capture_images", True)
        self._set_clipboard_images.toggled.connect(self._on_clipboard_images_changed)
        add_row(gv, "剪贴板自动捕获图片", "无文本时图片自动进素材池；图文混排仍按文本收集",
                self._set_clipboard_images, last=True)

        # ================= 4. 临时素材 =================
        gv = group(v, "🖼 临时素材")

        self._set_temp_asset_max_count = Stepper(
            5, 500, self._config.get("temp_asset_max_count", 50),
            suffix="个", step=5)
        self._set_temp_asset_max_count.valueChanged.connect(self._on_spin_changed)
        add_row(gv, "临时素材上限", "超出上限后自动清理最早的素材",
                self._set_temp_asset_max_count)

        # 单文件体积上限：防止误拖大文件（视频/镜像）时同步复制占满磁盘
        self._set_temp_asset_max_file = Stepper(
            0, 2048, self._config.get("temp_asset_max_file_mb", 50),
            suffix="MB", step=10)
        self._set_temp_asset_max_file.valueChanged.connect(self._on_spin_changed)
        add_row(gv, "单个素材体积上限", "超过该大小的文件不会复制进素材池；0 表示不限制",
                self._set_temp_asset_max_file)

        self._set_temp_asset_max_days = Stepper(
            0, 365, self._config.get("temp_asset_max_days", 30), suffix="天")
        self._set_temp_asset_max_days.valueChanged.connect(self._on_spin_changed)
        add_row(gv, "素材保留天数", "0 表示不按天数自动清理",
                self._set_temp_asset_max_days)

        # 素材缩略图大小：决定临时素材网格每行个数（默认 128px → 一行 4 个）
        self._set_asset_thumb = Stepper(
            80, 160, self._config.get("asset_thumb_size", 128),
            suffix="px", step=8)
        self._set_asset_thumb.valueChanged.connect(self._on_asset_thumb_changed)
        add_row(gv, "素材缩略图大小", "临时素材网格单元尺寸，每档 8px，调小可一行显示更多",
                self._set_asset_thumb, last=True)

        # ================= 5. 全局工具 =================
        gv = group(v, "⚡ 全局工具")

        self._set_quick_capture = self._toggle("quick_capture_enabled", True)
        self._set_quick_capture.toggled.connect(self._on_quick_capture_changed)
        add_row(gv, "全局快速捕捉", "任意界面按热键呼出迷你输入条，回车即存入碎片池",
                self._set_quick_capture)

        self._set_capture_hotkey = QLineEdit()
        self._set_capture_hotkey.setText(self._config.get("quick_capture_hotkey", "Ctrl+Alt+K"))
        self._set_capture_hotkey.setPlaceholderText("如：Ctrl+Alt+K")
        self._set_capture_hotkey.setMinimumWidth(190)
        self._set_capture_hotkey.editingFinished.connect(self._on_capture_hotkey_changed)
        add_row(gv, "快速捕捉热键", "格式 Ctrl+Alt+K，需含修饰键；被占用时会提示",
                self._set_capture_hotkey)

        # 截图钉屏（V4）：开关 + 热键
        self._set_screenshot = self._toggle("screenshot_enabled", True)
        self._set_screenshot.toggled.connect(self._on_screenshot_changed)
        add_row(gv, "截图钉屏", "按热键框选屏幕区域，钉成置顶参考浮窗",
                self._set_screenshot)

        self._set_screenshot_hotkey = QLineEdit()
        self._set_screenshot_hotkey.setText(self._config.get("screenshot_hotkey", "Ctrl+Alt+S"))
        self._set_screenshot_hotkey.setPlaceholderText("如：Ctrl+Alt+S")
        self._set_screenshot_hotkey.setMinimumWidth(190)
        self._set_screenshot_hotkey.editingFinished.connect(self._on_screenshot_hotkey_changed)
        add_row(gv, "截图热键", "格式 Ctrl+Alt+S，不能与快速捕捉热键相同",
                self._set_screenshot_hotkey)

        # 番茄钟（V4）：总开关 + 时长两档 + 自动休息
        self._set_pomodoro = self._toggle("pomodoro_enabled", True)
        self._set_pomodoro.toggled.connect(self._on_pomodoro_changed)
        add_row(gv, "番茄钟", "悬浮球外圈进度环计时，右键球体开始/暂停/结束",
                self._set_pomodoro)

        self._set_pomodoro_focus = Stepper(
            1, 120, self._config.get("pomodoro_focus_minutes", 25),
            suffix="分钟")
        self._set_pomodoro_focus.valueChanged.connect(self._on_pomodoro_time_changed)
        add_row(gv, "专注时长", "每个番茄的专注分钟数（1-120）",
                self._set_pomodoro_focus)

        self._set_pomodoro_break = Stepper(
            1, 60, self._config.get("pomodoro_break_minutes", 5),
            suffix="分钟")
        self._set_pomodoro_break.valueChanged.connect(self._on_pomodoro_time_changed)
        add_row(gv, "休息时长", "专注结束后的休息分钟数（1-60）",
                self._set_pomodoro_break)

        self._set_pomodoro_auto = self._toggle("pomodoro_auto_break", False)
        self._set_pomodoro_auto.toggled.connect(self._on_pomodoro_changed)
        add_row(gv, "自动进入休息", "专注计满后不回 idle，直接开始休息相位",
                self._set_pomodoro_auto, last=True)

        # ================= 6. 启动与系统 =================
        gv = group(v, "🚀 启动与系统")

        self._set_autostart = self._toggle("autostart", False)
        self._set_autostart.setChecked(autostart.is_autostart_enabled())
        self._set_autostart.toggled.connect(self._on_autostart_changed)
        add_row(gv, "开机自启", "随 Windows 登录启动（写注册表 Run 项，无需管理员权限）",
                self._set_autostart)

        self._set_restore_last_page = self._toggle("restore_last_page", False)
        self._set_restore_last_page.toggled.connect(self._on_restore_last_page_changed)
        add_row(gv, "启动时恢复上次页面", "下次打开回到关闭前停留的页面",
                self._set_restore_last_page)

        self._set_close_to_tray = self._toggle("close_to_tray", True)
        self._set_close_to_tray.toggled.connect(self._on_close_to_tray_changed)
        add_row(gv, "关闭即收进托盘", "点 ✕ 不退出、常驻后台，从托盘图标唤回",
                self._set_close_to_tray)

        self._set_task_reminder = self._toggle("task_reminder_enabled", True)
        self._set_task_reminder.toggled.connect(self._on_task_reminder_changed)
        add_row(gv, "任务到期提醒", "启动时及每日 9:00 托盘气泡，点击直达任务页",
                self._set_task_reminder, last=True)

        # ================= 7. 导出 =================
        # 单列一组而非并入现有组：现有六组各管一类"行为配置"，
        # 导出是「数据出口」而非行为开关，且需要承载路径 + 操作两个控件，
        # 并入任一组都会破坏该组"同类项相邻"的语义。
        gv = group(v, "📤 导出")

        vault_ctl = QWidget()
        vault_row = QHBoxLayout(vault_ctl)
        vault_row.setContentsMargins(0, 0, 0, 0)
        vault_row.setSpacing(8)
        self._set_vault_path = QLabel(self._vault_path_text())
        self._set_vault_path.setObjectName("hintLabel")
        self._set_vault_path.setMinimumWidth(170)
        self._set_vault_path.setToolTip(
            "导出目录；导出结果写入其下的 FloatPulse 文件夹（笔记 / 碎片 / 任务）")
        vault_row.addWidget(self._set_vault_path)
        self._set_vault_choose = QPushButton("更改目录")
        self._set_vault_choose.setObjectName("secondaryBtn")
        self._set_vault_choose.setFixedHeight(30)
        self._set_vault_choose.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_vault_choose.clicked.connect(self._on_choose_vault_dir)
        vault_row.addWidget(self._set_vault_choose)
        add_row(gv, "导出位置", "Obsidian vault 根目录；内容写入其下的 FloatPulse 文件夹",
                vault_ctl)

        self._set_export_btn = QPushButton("导出到 Obsidian")
        self._set_export_btn.setObjectName("secondaryBtn")
        self._set_export_btn.setFixedHeight(30)
        self._set_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_export_btn.setToolTip(
            "把笔记 / 碎片 / 任务导出为 Markdown；重复导出覆盖同名文件")
        self._set_export_btn.clicked.connect(self._on_export_clicked)
        add_row(gv, "一键导出", "笔记 / 碎片 / 任务导出为 Markdown，重复导出覆盖同名文件",
                self._set_export_btn, last=True)

        # ---- 恢复默认：全局操作，不属于任何分组 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        # 所有设置项均已实时持久化（改动即写盘并联动），无"保存"按钮
        reset_btn = QPushButton("↺ 恢复默认设置")
        reset_btn.setObjectName("secondaryBtn")
        reset_btn.setFixedHeight(32)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setToolTip("所有设置恢复默认值；软件导航条目、窗口/悬浮球位置会保留")
        reset_btn.clicked.connect(self._on_reset_settings)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        v.addLayout(btn_row)

        # ================= 关于 =================
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
            "快捷键：Esc 退出 | Ctrl+W/H 隐藏 | Ctrl+T 主题 | Ctrl+K 全库搜索\n"
            "F1 使用说明 | Ctrl+1~7 切换面板 | Ctrl+Alt+K 快速捕捉\n"
            "Ctrl+Alt+S 截图钉屏 | 右键悬浮球 / 右键卡片也可退出"
        )
        about_text.setObjectName("hintLabel")
        about_text.setWordWrap(True)
        ab_v.addWidget(about_text)

        v.addWidget(about_box)

        v.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def apply_theme(self):
        """主题切换：同步开关配色与主题按钮选中态（由主窗口 _apply_theme 调用）"""
        theme = self._host.current_theme
        for sw in getattr(self, "_toggles", []):
            sw.set_theme(theme)
        if hasattr(self, "_set_theme_light"):
            self._set_theme_light.setChecked(theme == "light")
            self._set_theme_dark.setChecked(theme == "dark")

    def _sync_auto_hide_rows(self):
        """同步「自动隐藏」相关行的可用性：总开关关闭时秒数步进器灰化。

        用灰化而非隐藏——布局位置稳定，用户看得出「这里有设置、只是当前不生效」，
        重新打开开关时也不用手忙脚乱找位置。
        """
        if not hasattr(self, '_set_auto_hide_enabled'):
            return
        enabled = bool(self._set_auto_hide_enabled.isChecked())
        if hasattr(self, '_set_auto_hide'):
            self._set_auto_hide.setEnabled(enabled)

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新设置面板当前值

        注意：同步控件值时必须屏蔽信号——Stepper.setValue 会触发
        valueChanged → _on_spin_changed，后者会读取"尚未同步"的其他控件
        旧值并写回配置，导致恢复默认等批量刷新被部分回滚。
        """
        self._set_theme_light.setChecked(self._host.current_theme == "light")
        self._set_theme_dark.setChecked(self._host.current_theme == "dark")
        for stepper, key, default in (
            (self._set_clipboard_max, "clipboard_max_items", 200),
            (self._set_auto_hide, "auto_hide_seconds", 3),
            (self._set_temp_asset_max_count, "temp_asset_max_count", 50),
            (self._set_temp_asset_max_file, "temp_asset_max_file_mb", 50),
            (self._set_temp_asset_max_days, "temp_asset_max_days", 30),
        ):
            stepper.blockSignals(True)
            stepper.setValue(self._config.get(key, default))
            stepper.blockSignals(False)
        # 自动隐藏总开关：先同步开关值，再据此重算秒数行的可用性
        if hasattr(self, '_set_auto_hide_enabled'):
            self._set_auto_hide_enabled.blockSignals(True)
            self._set_auto_hide_enabled.setChecked(
                bool(self._config.get("auto_hide_enabled", True)))
            self._set_auto_hide_enabled.blockSignals(False)
            self._sync_auto_hide_rows()
        if hasattr(self, '_set_card_always_show'):
            self._set_card_always_show.setChecked(self._config.get("card_always_show", False))
        if hasattr(self, '_set_ball_visible'):
            self._set_ball_visible.setChecked(self._config.get("ball_visible", True))
        if hasattr(self, '_set_restore_last_page'):
            self._set_restore_last_page.setChecked(self._config.get("restore_last_page", False))
        if hasattr(self, '_set_close_to_tray'):
            self._set_close_to_tray.setChecked(self._config.get("close_to_tray", True))
        if hasattr(self, '_set_clipboard_images'):
            self._set_clipboard_images.setChecked(
                self._config.get("clipboard_capture_images", True))
        if hasattr(self, '_set_task_reminder'):
            self._set_task_reminder.setChecked(self._config.get("task_reminder_enabled", True))
        if hasattr(self, '_set_quick_capture'):
            self._set_quick_capture.setChecked(self._config.get("quick_capture_enabled", True))
        if hasattr(self, '_set_capture_hotkey'):
            self._set_capture_hotkey.setText(self._config.get("quick_capture_hotkey", "Ctrl+Alt+K"))
        if hasattr(self, '_set_screenshot'):
            self._set_screenshot.blockSignals(True)
            self._set_screenshot.setChecked(self._config.get("screenshot_enabled", True))
            self._set_screenshot.blockSignals(False)
        if hasattr(self, '_set_screenshot_hotkey'):
            self._set_screenshot_hotkey.setText(self._config.get("screenshot_hotkey", "Ctrl+Alt+S"))
        if hasattr(self, '_set_pomodoro'):
            self._set_pomodoro.blockSignals(True)
            self._set_pomodoro.setChecked(self._config.get("pomodoro_enabled", True))
            self._set_pomodoro.blockSignals(False)
        if hasattr(self, '_set_pomodoro_focus'):
            self._set_pomodoro_focus.blockSignals(True)
            self._set_pomodoro_focus.setValue(int(self._config.get("pomodoro_focus_minutes", 25)))
            self._set_pomodoro_focus.blockSignals(False)
        if hasattr(self, '_set_pomodoro_break'):
            self._set_pomodoro_break.blockSignals(True)
            self._set_pomodoro_break.setValue(int(self._config.get("pomodoro_break_minutes", 5)))
            self._set_pomodoro_break.blockSignals(False)
        if hasattr(self, '_set_pomodoro_auto'):
            self._set_pomodoro_auto.blockSignals(True)
            self._set_pomodoro_auto.setChecked(self._config.get("pomodoro_auto_break", False))
            self._set_pomodoro_auto.blockSignals(False)
        if hasattr(self, '_set_plugins'):
            self._set_plugins.blockSignals(True)
            self._set_plugins.setChecked(self._config.get("plugins_enabled", True))
            self._set_plugins.blockSignals(False)
        if hasattr(self, '_set_clipboard_filter'):
            apps = self._config.get("clipboard_filter_apps", []) or []
            self._set_clipboard_filter.setText(", ".join(str(a) for a in apps))
        if hasattr(self, '_set_card_size'):
            self._set_card_size.blockSignals(True)
            self._set_card_size.setValue(int(self._config.get("app_card_size", 96)))
            self._set_card_size.blockSignals(False)
        if hasattr(self, '_set_anim_speed'):
            self._set_anim_speed.blockSignals(True)
            sp = self._config.get("anim_speed", 1.0)
            self._set_anim_speed.setValue(int(round(max(0.5, min(2.0, sp)) * 100)))
            self._set_anim_speed.blockSignals(False)
        if hasattr(self, '_set_ball_size'):
            self._set_ball_size.blockSignals(True)
            self._set_ball_size.setValue(self._config.get("ball_size", 64))
            self._set_ball_size.blockSignals(False)
        if hasattr(self, '_set_asset_thumb'):
            self._set_asset_thumb.blockSignals(True)
            self._set_asset_thumb.setValue(int(self._config.get("asset_thumb_size", 128)))
            self._set_asset_thumb.blockSignals(False)
        if hasattr(self, '_set_hide_fullscreen'):
            self._set_hide_fullscreen.setChecked(self._config.get("hide_on_fullscreen", True))
        if hasattr(self, '_set_vault_path'):
            # 导出目录可能被「恢复默认」清空或被别的入口改写，刷新时同步展示
            self._set_vault_path.setText(self._vault_path_text())

    def _on_set_theme(self, theme_name: str):
        """设置面板切换主题"""
        self._host.apply_external_theme(theme_name)
        self.refresh()

    def _on_spin_changed(self):
        """
        四个数字设置（剪贴板上限/自动隐藏秒数/素材上限/素材天数）变更时
        即时持久化到磁盘，改动即生效。
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
        max_file_mb = int(self._set_temp_asset_max_file.value())
        old_count = self._config.get("temp_asset_max_count", 50)
        old_days = self._config.get("temp_asset_max_days", 30)
        old_file_mb = self._config.get("temp_asset_max_file_mb", 50)
        limits_changed = (max_count != old_count or max_days != old_days
                          or max_file_mb != old_file_mb)
        if limits_changed:
            self._config.set("temp_asset_max_count", max_count)
            self._config.set("temp_asset_max_days", max_days)
            self._config.set("temp_asset_max_file_mb", max_file_mb)
            changed = True

        if changed:
            self._config.save()
            # 广播联动
            if limits_changed:
                self._host.asset_limits_changed.emit(
                    max_count, max_days, max_file_mb)
            self._host.auto_hide_seconds_changed.emit(hide)

    def _on_auto_hide_enabled_changed(self, checked: bool):
        """自动隐藏总开关：即时持久化、联动秒数行灰化并广播（悬浮球停表/恢复显示）"""
        enabled = bool(checked)
        if enabled != self._config.get("auto_hide_enabled", True):
            self._config.set("auto_hide_enabled", enabled)
            self._config.save()
        self._sync_auto_hide_rows()
        self._host.auto_hide_enabled_changed.emit(enabled)

    def _on_ball_visibility_changed(self, checked: bool):
        """悬浮球显示/隐藏切换（立即生效并持久化）"""
        is_visible = bool(checked)
        old = self._config.get("ball_visible", True)
        if is_visible != old:
            self._config.set("ball_visible", is_visible)
            self._config.save()
        self._host.ball_visibility_changed.emit(is_visible)

    def _on_card_always_show_preview(self, checked: bool):
        """开关切换时实时预览效果（无需点保存即生效）"""
        always_show = bool(checked)
        old = self._config.get("card_always_show", False)
        if always_show != old:
            self._config.set("card_always_show", always_show)
            self._config.save()
            self._host.card_always_show_changed.emit(always_show)

    def _on_autostart_changed(self, checked: bool):
        """开机自启开关：即时写注册表，失败回滚开关状态"""
        enabled = bool(checked)
        ok = autostart.set_autostart(enabled)
        if not ok:
            QMessageBox.warning(self, "设置失败",
                                "写入开机自启注册表失败，请检查系统权限。")
            self._set_autostart.blockSignals(True)
            self._set_autostart.setChecked(not enabled)
            self._set_autostart.blockSignals(False)

    def _on_restore_last_page_changed(self, checked: bool):
        """启动页面设置：即时持久化，无需点保存按钮"""
        enabled = bool(checked)
        if enabled != self._config.get("restore_last_page", False):
            self._config.set("restore_last_page", enabled)
            self._config.save()

    def _on_clipboard_filter_changed(self):
        """剪贴板过滤应用：焦点离开或回车时解析文本并即时持久化。

        解析规则：中英文逗号/分号均为分隔符，去重去空，
        保留用户输入原样（大小写、是否带 .exe 由匹配端做归一化）。
        """
        raw = self._set_clipboard_filter.text()
        apps = []
        for part in raw.replace("，", ",").replace("；", ";").replace(";", ",").split(","):
            part = part.strip()
            if part and part not in apps:
                apps.append(part)
        if apps != (self._config.get("clipboard_filter_apps", []) or []):
            self._config.set("clipboard_filter_apps", apps)
            self._config.save()

    def _on_clipboard_images_changed(self, checked: bool):
        """剪贴板图片收集开关：即时持久化（监听器每次捕获时实时读配置）"""
        enabled = bool(checked)
        if enabled != self._config.get("clipboard_capture_images", True):
            self._config.set("clipboard_capture_images", enabled)
            self._config.save()

    def _on_close_to_tray_changed(self, checked: bool):
        """关闭到托盘开关：即时持久化（closeEvent 每次实时读配置，无需广播）"""
        enabled = bool(checked)
        if enabled != self._config.get("close_to_tray", True):
            self._config.set("close_to_tray", enabled)
            self._config.save()

    def _on_task_reminder_changed(self, checked: bool):
        """任务到期提醒开关：即时持久化（提醒触发时实时读配置）"""
        enabled = bool(checked)
        if enabled != self._config.get("task_reminder_enabled", True):
            self._config.set("task_reminder_enabled", enabled)
            self._config.save()

    def _on_quick_capture_changed(self, checked: bool):
        """快速捕捉开关：即时持久化并广播（主流程重注册/注销热键）"""
        enabled = bool(checked)
        if enabled != self._config.get("quick_capture_enabled", True):
            self._config.set("quick_capture_enabled", enabled)
            self._config.save()
            self._host.quick_capture_changed.emit()

    def _on_capture_hotkey_changed(self):
        """快速捕捉热键编辑：校验格式与冲突后持久化并广播重注册"""
        text = self._set_capture_hotkey.text().strip()
        old = self._config.get("quick_capture_hotkey", "Ctrl+Alt+K")
        if text == old:
            return
        from src.global_hotkey import parse_hotkey
        if parse_hotkey(text) is None:
            QMessageBox.warning(self, "热键无效",
                                f"「{text}」不是有效的热键组合。\n"
                                "格式如 Ctrl+Alt+K，需含 Ctrl/Alt/Shift/Win 修饰键。")
            self._set_capture_hotkey.setText(old)
            return
        if text == self._config.get("screenshot_hotkey", "Ctrl+Alt+S"):
            QMessageBox.warning(self, "热键冲突",
                                f"「{text}」已被截图钉屏占用，请换一个组合。")
            self._set_capture_hotkey.setText(old)
            return
        self._config.set("quick_capture_hotkey", text)
        self._config.save()
        self._host.quick_capture_changed.emit()

    def _on_screenshot_changed(self, checked: bool):
        """截图钉屏开关：即时持久化并广播（主流程重注册/注销热键）"""
        enabled = bool(checked)
        if enabled != self._config.get("screenshot_enabled", True):
            self._config.set("screenshot_enabled", enabled)
            self._config.save()
            self._host.screenshot_changed.emit()

    def _on_plugins_changed(self, checked: bool):
        """悬浮球插件总闸：即时持久化并广播（主流程启用/禁用插件动作）"""
        enabled = bool(checked)
        if enabled != self._config.get("plugins_enabled", True):
            self._config.set("plugins_enabled", enabled)
            self._config.save()
            self._host.plugins_changed.emit(enabled)

    def _on_screenshot_hotkey_changed(self):
        """截图热键编辑：校验格式与冲突后持久化并广播重注册"""
        text = self._set_screenshot_hotkey.text().strip()
        old = self._config.get("screenshot_hotkey", "Ctrl+Alt+S")
        if text == old:
            return
        from src.global_hotkey import parse_hotkey
        if parse_hotkey(text) is None:
            QMessageBox.warning(self, "热键无效",
                                f"「{text}」不是有效的热键组合。\n"
                                "格式如 Ctrl+Alt+S，需含 Ctrl/Alt/Shift/Win 修饰键。")
            self._set_screenshot_hotkey.setText(old)
            return
        if text == self._config.get("quick_capture_hotkey", "Ctrl+Alt+K"):
            QMessageBox.warning(self, "热键冲突",
                                f"「{text}」已被快速捕捉占用，请换一个组合。")
            self._set_screenshot_hotkey.setText(old)
            return
        self._config.set("screenshot_hotkey", text)
        self._config.save()
        self._host.screenshot_changed.emit()

    def _on_pomodoro_changed(self, _checked=None):
        """番茄钟开关/自动休息：即时持久化并广播（悬浮球应用配置）"""
        self._config.set("pomodoro_enabled",
                         bool(self._set_pomodoro.isChecked()))
        self._config.set("pomodoro_auto_break",
                         bool(self._set_pomodoro_auto.isChecked()))
        self._config.save()
        self._host.pomodoro_changed.emit()

    def _on_pomodoro_time_changed(self, _value=None):
        """番茄钟时长步进：即时持久化并广播"""
        self._config.set("pomodoro_focus_minutes",
                         int(self._set_pomodoro_focus.value()))
        self._config.set("pomodoro_break_minutes",
                         int(self._set_pomodoro_break.value()))
        self._config.save()
        self._host.pomodoro_changed.emit()

    # ---- 导出到 Obsidian ----
    def _vault_path_text(self) -> str:
        """导出目录的展示文案：为空显示「未选择」，过长省略中段。"""
        vault = str(self._config.get("obsidian_vault_path", "") or "")
        if not vault:
            return "未选择"
        if len(vault) <= 30:
            return vault
        return vault[:14] + "..." + vault[-13:]

    def _on_choose_vault_dir(self):
        """选择 Obsidian vault 目录；用户取消则不改动任何状态。

        仅记住路径，不触发导出——导出由「导出到 Obsidian」按钮或各面板
        右键菜单发起（避免选完目录就意外开始写盘）。
        """
        cur = str(self._config.get("obsidian_vault_path", "") or "")
        start = cur if cur and os.path.isdir(cur) else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择 Obsidian vault 目录", start)
        if not path:
            return                          # 取消：不导出、不报错
        self._config.set("obsidian_vault_path", path)
        self._config.save()
        self.refresh()

    def _on_export_clicked(self):
        """一键导出：转交宿主公开方法（提示与容错都在那里，避免逻辑分叉）"""
        self._host.export_to_obsidian()
        self.refresh()

    # 恢复默认设置时保留的键：属于用户数据/环境状态，不属于"设置"
    _RESET_PRESERVE_KEYS = (
        "apps",                 # 软件导航条目（用户录入的数据）
        "ball_position",        # 悬浮球屏幕位置
        "main_window_geometry", # 主窗口位置大小
        "last_page_index",      # 上次浏览页面（与启动行为联动）
        "nav_order",            # 左栏功能页显示顺序（用户自定义排序偏好）
    )

    def _on_reset_settings(self):
        """恢复默认设置：二次确认 → 重置配置 → 广播全部联动信号 → 刷新面板"""
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "将把所有设置恢复为默认值（主题、剪贴板、悬浮球行为等）。\n"
            "软件导航条目、窗口位置、悬浮球位置会保留。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # 1. 备份需保留的键 → 重置 → 回写
        preserved = {k: self._config.get(k) for k in self._RESET_PRESERVE_KEYS}
        self._config.reset_to_default()
        for key, val in preserved.items():
            self._config.set(key, val)
        self._config.save()

        # 2. 广播联动（与各设置项单独修改时的行为一致）
        self._host.apply_external_theme(self._config.get("theme", "light"))
        self._host.ball_visibility_changed.emit(self._config.get("ball_visible", True))
        self._host.card_always_show_changed.emit(self._config.get("card_always_show", False))
        self._host.asset_limits_changed.emit(
            self._config.get("temp_asset_max_count", 50),
            self._config.get("temp_asset_max_days", 30),
            self._config.get("temp_asset_max_file_mb", 50),
        )
        self._host.auto_hide_seconds_changed.emit(self._config.get("auto_hide_seconds", 3))
        self._host.auto_hide_enabled_changed.emit(
            self._config.get("auto_hide_enabled", True))
        self._host.anim_speed_changed.emit(self._config.get("anim_speed", 1.0))
        self._host.ball_size_changed.emit(self._config.get("ball_size", 64))
        self._host.hide_on_fullscreen_changed.emit(
            self._config.get("hide_on_fullscreen", True))
        if self._host._page_app_launcher is not None:
            self._host._page_app_launcher.apply_card_size(self._config.get("app_card_size", 96))
        if getattr(self._host, "_page_assets", None) is not None:
            self._host._page_assets.apply_thumb_size(
                self._config.get("asset_thumb_size", 128))
        self._host.quick_capture_changed.emit()  # 热键/开关可能被重置，重注册
        self._host.screenshot_changed.emit()     # 截图热键/开关同理
        self._host.plugins_changed.emit(
            self._config.get("plugins_enabled", True))  # 插件总闸同理
        self._host.pomodoro_changed.emit()       # 番茄钟开关/时长同理

        # 3. 刷新面板控件（含自启勾选框——注册表未被本次重置触及）
        self.refresh()
        QMessageBox.information(self, "已恢复", "所有设置已恢复为默认值。")

    def _on_card_size_changed(self, value: int):
        """卡片尺寸步进：即时持久化并刷新导航页卡片"""
        value = int(value)
        if value != int(self._config.get("app_card_size", 96)):
            self._config.set("app_card_size", value)
            self._config.save()
        if self._host._page_app_launcher is not None:
            self._host._page_app_launcher.apply_card_size(value)

    def _on_asset_thumb_changed(self, value: int):
        """素材缩略图尺寸步进：即时持久化并刷新素材网格（含缩略图缓存重建）"""
        value = int(value)
        if value != int(self._config.get("asset_thumb_size", 128)):
            self._config.set("asset_thumb_size", value)
            self._config.save()
        if getattr(self._host, "_page_assets", None) is not None:
            self._host._page_assets.apply_thumb_size(value)

    def _on_anim_speed_changed(self, value: int):
        """动画速度步进：即时持久化并广播到悬浮球（value 为内部整数，1/100 档）"""
        speed = round(value / 100.0, 2)
        if speed != self._config.get("anim_speed", 1.0):
            self._config.set("anim_speed", speed)
            self._config.save()
        self._host.anim_speed_changed.emit(speed)

    def _on_ball_size_changed(self, value: int):
        """悬浮球大小：即时持久化并广播（悬浮球重建宿主尺寸，保持球心不动）"""
        value = int(value)
        if value != self._config.get("ball_size", 64):
            self._config.set("ball_size", value)
            self._config.save()
        self._host.ball_size_changed.emit(value)

    def _on_hide_fullscreen_changed(self, checked: bool):
        """全屏应用自动隐藏开关：即时持久化并广播（主流程启停全屏检测）"""
        enabled = bool(checked)
        if enabled != self._config.get("hide_on_fullscreen", True):
            self._config.set("hide_on_fullscreen", enabled)
            self._config.save()
        self._host.hide_on_fullscreen_changed.emit(enabled)
