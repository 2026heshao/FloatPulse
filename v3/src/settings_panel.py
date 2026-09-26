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

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame,
    QLineEdit,
    QMessageBox,
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

    def _toggle(self, key: str, default: bool) -> ToggleSwitch:
        """创建跟随主题的开关并登记（主题切换时统一 set_theme）"""
        sw = ToggleSwitch(self._config.get(key, default),
                          theme=self._host.current_theme)
        self._toggles.append(sw)
        return sw

    def _add_row(self, vbox, title: str, desc: str, widget, tip: str = ""):
        """行式设置项：左侧「标题 + 描述」，右侧控件（垂直居中），行间细分隔线。

        形式对齐参考设计稿（2026-09-24）：勾选类用开关、数字类用步进器，
        全部右对齐；说明文字从 tooltip 提升为可见的描述行。
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

        # ---- 行为设置（行式布局：标题+描述居左，控件居右，行间分隔线）----
        behavior_box = self._group_box()
        bb_v = QVBoxLayout(behavior_box)
        bb_v.setContentsMargins(14, 10, 14, 12)
        bb_v.setSpacing(0)

        behavior_title = QLabel("⚙️ 行为配置")
        behavior_title.setObjectName("sectionLabel")
        bb_v.addWidget(behavior_title)
        head_gap = QWidget()
        head_gap.setFixedHeight(6)
        bb_v.addWidget(head_gap)

        self._toggles = []
        add_row = self._add_row

        self._set_clipboard_max = Stepper(10, 10000,
                                          self._config.get("clipboard_max_items", 200),
                                          suffix="条", step=10)
        add_row(bb_v, "剪贴板历史上限", "达到上限后自动清理最早的碎片",
                self._set_clipboard_max)

        self._set_auto_hide = Stepper(1, 60,
                                      self._config.get("auto_hide_seconds", 3),
                                      suffix="秒")
        add_row(bb_v, "悬浮球自动隐藏", "贴边静止一段时间后半隐藏，鼠标靠近即恢复",
                self._set_auto_hide)

        self._set_ball_visible = self._toggle("ball_visible", True)
        self._set_ball_visible.toggled.connect(self._on_ball_visibility_changed)
        add_row(bb_v, "显示悬浮球", "桌面上的球体入口，隐藏后可从系统托盘唤回",
                self._set_ball_visible)

        self._set_card_always_show = self._toggle("card_always_show", False)
        self._set_card_always_show.toggled.connect(self._on_card_always_show_preview)
        add_row(bb_v, "小卡片保持显示", "卡片不自动关闭，常驻在悬浮球旁",
                self._set_card_always_show)

        self._set_temp_asset_max_count = Stepper(
            5, 500, self._config.get("temp_asset_max_count", 50),
            suffix="个", step=5)
        add_row(bb_v, "临时素材上限", "超出上限后自动清理最早的素材",
                self._set_temp_asset_max_count)

        self._set_temp_asset_max_days = Stepper(
            0, 365, self._config.get("temp_asset_max_days", 30), suffix="天")
        add_row(bb_v, "素材保留天数", "0 表示不按天数自动清理",
                self._set_temp_asset_max_days)

        # 素材缩略图大小：决定临时素材网格每行个数（默认 128px → 一行 4 个）
        self._set_asset_thumb = Stepper(
            80, 160, self._config.get("asset_thumb_size", 128),
            suffix="px", step=8)
        self._set_asset_thumb.valueChanged.connect(self._on_asset_thumb_changed)
        add_row(bb_v, "素材缩略图大小", "临时素材网格单元尺寸，每档 8px，调小可一行显示更多",
                self._set_asset_thumb)

        # 数字类设置改动即持久化：写盘并广播，无需任何保存动作
        self._set_clipboard_max.valueChanged.connect(self._on_spin_changed)
        self._set_auto_hide.valueChanged.connect(self._on_spin_changed)
        self._set_temp_asset_max_count.valueChanged.connect(self._on_spin_changed)
        self._set_temp_asset_max_days.valueChanged.connect(self._on_spin_changed)

        # 卡片尺寸 / 动画速度：用增减按钮（Stepper）而非滑条 ——
        # 滑条会在鼠标滚设置页时被滚轮静默改值，步进器只在数值框聚焦时才吃滚轮。
        self._set_card_size = Stepper(60, 140, self._config.get("app_card_size", 96),
                                      suffix="px", step=4)
        self._set_card_size.valueChanged.connect(self._on_card_size_changed)
        add_row(bb_v, "软件卡片尺寸", "导航页卡片大小，每档 4px，可长按 ± 连续调整",
                self._set_card_size)

        # anim_speed 存浮点（0.5~2.0），Stepper 内部用整数 50~200，
        # divisor=100 / decimals=1 → 显示「1.3」，对外仍发内部整数。
        init_speed = self._config.get("anim_speed", 1.0)
        init_ticks = int(round(max(0.5, min(2.0, init_speed)) * 100))
        self._set_anim_speed = Stepper(50, 200, init_ticks,
                                       suffix="x", step=10,
                                       divisor=100, decimals=1)
        self._set_anim_speed.valueChanged.connect(self._on_anim_speed_changed)
        add_row(bb_v, "动画速度", "悬浮球与勾选动画的统一倍速，每档 0.1x",
                self._set_anim_speed)

        self._set_autostart = self._toggle("autostart", False)
        self._set_autostart.setChecked(autostart.is_autostart_enabled())
        self._set_autostart.toggled.connect(self._on_autostart_changed)
        add_row(bb_v, "开机自启", "随 Windows 登录启动（写注册表 Run 项，无需管理员权限）",
                self._set_autostart)

        self._set_restore_last_page = self._toggle("restore_last_page", False)
        self._set_restore_last_page.toggled.connect(self._on_restore_last_page_changed)
        add_row(bb_v, "启动时恢复上次页面", "下次打开回到关闭前停留的页面",
                self._set_restore_last_page)

        self._set_clipboard_filter = QLineEdit()
        apps = self._config.get("clipboard_filter_apps", []) or []
        self._set_clipboard_filter.setText(", ".join(str(a) for a in apps))
        self._set_clipboard_filter.setPlaceholderText("如：WeChat, Weixin, chrome")
        self._set_clipboard_filter.setMinimumWidth(190)
        self._set_clipboard_filter.editingFinished.connect(self._on_clipboard_filter_changed)
        add_row(bb_v, "剪贴板过滤", "这些进程里的复制不会进碎片池（逗号分隔，即时生效）",
                self._set_clipboard_filter)

        self._set_close_to_tray = self._toggle("close_to_tray", True)
        self._set_close_to_tray.toggled.connect(self._on_close_to_tray_changed)
        add_row(bb_v, "关闭即收进托盘", "点 ✕ 不退出、常驻后台，从托盘图标唤回",
                self._set_close_to_tray)

        # 剪贴板图片自动入素材池（Y2）：纯图片复制（截图/复制图片）时生效
        self._set_clipboard_images = self._toggle("clipboard_capture_images", True)
        self._set_clipboard_images.toggled.connect(self._on_clipboard_images_changed)
        add_row(bb_v, "剪贴板自动捕获图片", "无文本时图片自动进素材池；图文混排仍按文本收集",
                self._set_clipboard_images)

        self._set_task_reminder = self._toggle("task_reminder_enabled", True)
        self._set_task_reminder.toggled.connect(self._on_task_reminder_changed)
        add_row(bb_v, "任务到期提醒", "启动时及每日 9:00 托盘气泡，点击直达任务页",
                self._set_task_reminder)

        self._set_quick_capture = self._toggle("quick_capture_enabled", True)
        self._set_quick_capture.toggled.connect(self._on_quick_capture_changed)
        add_row(bb_v, "全局快速捕捉", "任意界面按热键呼出迷你输入条，回车即存入碎片池",
                self._set_quick_capture)

        self._set_capture_hotkey = QLineEdit()
        self._set_capture_hotkey.setText(self._config.get("quick_capture_hotkey", "Ctrl+Alt+K"))
        self._set_capture_hotkey.setPlaceholderText("如：Ctrl+Alt+K")
        self._set_capture_hotkey.setMinimumWidth(190)
        self._set_capture_hotkey.editingFinished.connect(self._on_capture_hotkey_changed)
        add_row(bb_v, "快速捕捉热键", "格式 Ctrl+Alt+K，需含修饰键；被占用时会提示",
                self._set_capture_hotkey)

        # 悬浮球大小（C1）：改球径时保持球心不动，投影留白自动适配
        self._set_ball_size = Stepper(48, 88, self._config.get("ball_size", 64),
                                      suffix="px", step=8)
        self._set_ball_size.valueChanged.connect(self._on_ball_size_changed)
        add_row(bb_v, "悬浮球大小", "球体直径，每档 8px，调整时保持球心不动",
                self._set_ball_size)

        # 全屏应用让位（B8）：全屏视频/游戏/演示时不与画面争抢注意力
        self._set_hide_fullscreen = self._toggle("hide_on_fullscreen", True)
        self._set_hide_fullscreen.toggled.connect(self._on_hide_fullscreen_changed)
        add_row(bb_v, "全屏应用让位", "检测到全屏应用时自动隐藏悬浮球，退出后恢复",
                self._set_hide_fullscreen)

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
        bb_v.addLayout(btn_row)

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
            "快捷键：Esc 退出 | Ctrl+W/H 隐藏 | Ctrl+T 主题 | Ctrl+K 全库搜索\n"
            "F1 使用说明 | Ctrl+1~7 切换面板 | Ctrl+Alt+K 快速捕捉\n"
            "右键悬浮球 / 右键卡片也可退出"
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
            (self._set_temp_asset_max_days, "temp_asset_max_days", 30),
        ):
            stepper.blockSignals(True)
            stepper.setValue(self._config.get(key, default))
            stepper.blockSignals(False)
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
        old_count = self._config.get("temp_asset_max_count", 50)
        old_days = self._config.get("temp_asset_max_days", 30)
        if max_count != old_count or max_days != old_days:
            self._config.set("temp_asset_max_count", max_count)
            self._config.set("temp_asset_max_days", max_days)
            changed = True

        if changed:
            self._config.save()
            # 广播联动
            if hide != self._config.get("auto_hide_seconds", 3):
                pass  # 已在上面 set 完成
            if max_count != old_count or max_days != old_days:
                self._host.asset_limits_changed.emit(max_count, max_days)
            self._host.auto_hide_seconds_changed.emit(hide)

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
        """快速捕捉热键编辑：校验格式后持久化并广播重注册"""
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
        self._config.set("quick_capture_hotkey", text)
        self._config.save()
        self._host.quick_capture_changed.emit()

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
        )
        self._host.auto_hide_seconds_changed.emit(self._config.get("auto_hide_seconds", 3))
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
