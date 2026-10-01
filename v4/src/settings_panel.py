# -*- coding: utf-8 -*-
"""
====================================================================
设置面板  -  SettingsPanel
====================================================================
从 main_window.py 抽出的独立面板，承载主题 / 行为配置 / 关于 /
日志查看等设置项 UI 与交互逻辑。
通过 host（MainWindow）访问配置管理器、主题切换、各业务信号与
软件导航页面实例。

2026-09-29 起页内带左侧分类导航（SETTINGS_CATEGORIES）：每个分类
独立成滚动页挂在 QStackedWidget 上，「恢复默认设置」移到页底常驻栏。
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QStackedWidget,
    QLineEdit, QComboBox,
    QMenu, QCheckBox, QWidgetAction, QButtonGroup,
    QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
# ★ QAction 在 QtGui 而不是 QtWidgets：本文件第 70 行用它给下拉框做占位项，
#   此前漏了这行导入 → MainWindow 构造走到设置页就 NameError，
#   程序直接起不来（2026-09-29 11:07 修）。
# ★ QDesktopServices：关于页「📂 打开日志」用系统文件管理器开数据目录。
from PyQt6.QtGui import QAction, QDesktopServices


from src.controls import IconButton, PageTitle, Stepper, ToggleSwitch
from src.glass_dialog import make_separator
from src.plugin_net import make_async_getter, make_async_poster
from src.ai_server import AI_SERVER, ST_READY, ST_STARTING, sync_loopback_allowlist
from src.app_version import APP_VERSION
from src.app_paths import get_data_dir
from src.theme import resolve_theme_name, apply_app_font, UI_SCALE_VALUES
from src import motion
from src.update_checker import (RELEASES_API_URL, RELEASES_PAGE_URL,
                                CHECK_TIMEOUT_S, check_headers, extract_tag,
                                is_newer)
from src import autostart


# 设置页内部分类导航：顺序即左栏展示顺序，(key, 图标, 名称)。
# 8 个行为配置分类沿用原单页分组的语义（不合并不改名），
# 「关于」从滚动页尾独立成分类，「恢复默认设置」移至页底常驻栏。
SETTINGS_CATEGORIES = (
    ("appearance", "🎨", "外观"),
    ("ball", "🔵", "悬浮球"),
    ("clipboard", "📋", "剪贴板与碎片"),
    ("assets", "🖼", "临时素材"),
    ("tools", "⚡", "全局工具"),
    ("system", "🚀", "启动与系统"),
    ("export", "📤", "导出"),
    ("ai", "🧠", "AI 配置"),
    ("about", "ℹ️", "关于"),
)


class PluginsPickButton(QPushButton):
    """「接入插件」多选选择器：按钮 + 下拉菜单内嵌勾选框。

    用户要求：下拉选择、可多选。用 QMenu + QCheckBox（原生控件自己
    处理点击）而不是 QComboBox 勾选条目——后者要吞弹层鼠标事件才能
    不收起，真实环境下点击路径不稳定（实测勾选失效）；QWidgetAction
    里的复选框点击不收起菜单，连续多选天然可靠。

    信号 ``changed(list[str])``：勾选集合变化时发出（元素为插件 id）。
    按钮文案聚合已勾选插件名，空时显示占位提示。
    """

    changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__("（未勾选任何插件）", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu = QMenu(self)
        self._boxes = {}          # plugin_id -> QCheckBox
        self._block = False       # reload 批量重建时静音 toggled
        self.setMenu(self._menu)

    def reload(self, selected, candidates):
        """重建菜单条目。candidates: [(plugin_id, 展示名)]；selected: 已勾选"""
        self._block = True
        self._menu.clear()
        self._boxes = {}
        sel = set(selected or [])
        for pid, name in candidates or []:
            box = QCheckBox(name if pid == name else f"{name}（{pid}）",
                            self._menu)
            box.setChecked(pid in sel)
            box.toggled.connect(
                lambda _on, p=pid: self._on_toggled(p))
            wa = QWidgetAction(self._menu)
            wa.setDefaultWidget(box)
            self._menu.addAction(wa)
            self._boxes[pid] = box
        if not candidates:
            empty = QAction("（暂无可接入的 AI 插件）", self._menu)
            empty.setEnabled(False)
            self._menu.addAction(empty)
        self._block = False
        self._update_text()

    def selected(self) -> list:
        """当前勾选的插件 id 列表（按条目顺序）"""
        return [pid for pid, box in self._boxes.items() if box.isChecked()]

    def _on_toggled(self, pid: str):
        if not self._block:
            self._update_text()
            self.changed.emit(self.selected())

    def _update_text(self):
        names = [box.text() for box in self._boxes.values()
                 if box.isChecked()]
        self.setText("、".join(names) if names else "（未勾选任何插件）")


class SettingsPanel(QWidget):
    """设置面板"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._config = host._config
        self._row_sep = {}          # 行 widget → 其下方分隔线（显隐联动用）
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
            sep = make_separator()
            vbox.addWidget(sep)
            # 登记行与分隔线的从属关系：整行隐藏时（如 AI 本地配置区）
            # 分隔线必须跟着藏，否则卡片里会留下一串悬空横线撑出空档
            self._row_sep[row] = sep
        return row

    def _build_ui(self):
        page = self
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        page_title = PageTitle("settings", "设置", self._host)
        outer.addWidget(page_title)
        outer.addSpacing(8)

        # ---- 中部：左分类导航 + 右分类内容，每个分类独立滚动 ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        body.addWidget(self._build_nav_rail())
        divider = QFrame()
        divider.setObjectName("settingsNavDivider")
        divider.setFixedWidth(1)
        body.addWidget(divider)
        self._cat_stack = QStackedWidget()
        body.addWidget(self._cat_stack, 1)
        outer.addLayout(body, 1)
        self._cat_vboxes = {}

        # 分类顺序 = SETTINGS_CATEGORIES；组内行顺序 = 功能亲密度
        # （同组相邻项最常被一起调整），沿用原单页语义不合并不改名。
        self._toggles = []
        add_row = self._add_row
        group = self._group_card

        # ================= 1. 外观（外观与主题） =================
        cv = self._new_category_page("appearance")
        gv = group(cv, "🎨 外观与主题")

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
        self._set_theme_follow = QPushButton("🖥 跟随系统")
        self._set_theme_follow.setObjectName("secondaryBtn")
        self._set_theme_follow.setCheckable(True)
        self._set_theme_follow.setFixedHeight(30)
        self._set_theme_follow.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_theme_follow.clicked.connect(lambda: self._on_set_theme("follow"))
        theme_row.addWidget(self._set_theme_follow)
        add_row(gv, "主题外观", "浅色 / 深色 / 跟随系统（系统深浅色变化时自动换），切换即时生效",
                theme_ctl)

        # 界面缩放（3.5 活字缩放）：固定档位下拉——只缩放全局字号不缩放
        # px 布局（低风险取舍见 theme.BASE_FONT_PT 注释），改动即时生效
        # （重设 QApplication 字号并走主题刷新链全量重绘，无需重启）。
        self._set_ui_scale = QComboBox()
        for pct in UI_SCALE_VALUES:
            self._set_ui_scale.addItem(f"{pct}%", int(pct))
        self._set_ui_scale.setCurrentIndex(
            max(0, self._set_ui_scale.findData(
                int(self._config.get("ui_scale", 100)))))
        self._set_ui_scale.currentIndexChanged.connect(self._on_ui_scale_changed)
        add_row(gv, "界面缩放",
                "整体字号缩放，改动即时生效；部分固定像素间距不随缩放，"
                "85–130% 观感最佳",
                self._set_ui_scale)

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

        # 减弱动效（#14）：总闸在 motion.duration 单点生效——导航展开/掉落、
        # 任务勾选、卡片窗口的过渡一律瞬显；悬浮球自身的动画不在此口径内
        self._set_reduce_motion = ToggleSwitch(
            checked=bool(self._config.get("reduce_motion", False)))
        self._set_reduce_motion.toggled.connect(self._on_reduce_motion_changed)
        add_row(gv, "减弱动效", "导航展开、任务勾选等界面过渡直接瞬显"
                "（悬浮球自身动画不受影响）",
                self._set_reduce_motion)

        # 数值类用增减按钮（Stepper）而非滑条 ——
        # 滑条会在鼠标滚设置页时被滚轮静默改值，步进器只在数值框聚焦时才吃滚轮。
        self._set_card_size = Stepper(60, 140, self._config.get("app_card_size", 96),
                                      suffix="px", step=4)
        self._set_card_size.valueChanged.connect(self._on_card_size_changed)
        add_row(gv, "软件卡片尺寸", "导航页卡片大小，每档 4px，可长按 ± 连续调整",
                self._set_card_size, last=True)

        # ================= 2. 悬浮球 =================
        cv = self._new_category_page("ball")
        gv = group(cv, "🔵 悬浮球")

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
        cv = self._new_category_page("clipboard")
        gv = group(cv, "📋 剪贴板与碎片")

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
        cv = self._new_category_page("assets")
        gv = group(cv, "🖼 临时素材")

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
        cv = self._new_category_page("tools")
        gv = group(cv, "⚡ 全局工具")

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
        cv = self._new_category_page("system")
        gv = group(cv, "🚀 启动与系统")

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
        add_row(gv, "任务提醒",
                "启动时及每日 9:00 托盘气泡，汇总逾期 / 今日到期 / 未安排日期的"
                "未完成任务，点击直达任务页",
                self._set_task_reminder)

        # 重看引导（3.4）：置回未完成态并弹出同一欢迎向导（复用首启
        # 状态链——向导关闭时 mark_done 落盘，不会产生残留态）
        self._onboard_btn = IconButton("refresh", text="重看引导", icon_size=14,
                                       object_name="secondaryBtn")
        self._onboard_btn.setFixedHeight(30)
        self._onboard_btn.clicked.connect(self._on_replay_onboarding)
        add_row(gv, "新手引导", "重新弹出三步欢迎向导"
                "（热键速查 / 悬浮球用法 / AI 说明）",
                self._onboard_btn, last=True)

        # ================= 7. 导出 =================
        # 单列一组而非并入现有组：现有六组各管一类"行为配置"，
        # 导出是「数据出口」而非行为开关，且需要承载路径 + 操作两个控件，
        # 并入任一组都会破坏该组"同类项相邻"的语义。
        gv = group(self._new_category_page("export"), "📤 导出")

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

        # ================= 8. AI 总配置 =================
        # 插件 AI 后端的单一真相源（2026-09-29）：云端 / 本地只配一次，
        # 接入哪些插件由用户在下拉框勾选（勾选 = 授权）。接入的插件经
        # ctx.ai 实时读取这里的配置，不再各自维护后端设置；未接入的
        # 插件照旧用各自私有配置（向后兼容，互不影响）。
        gv = group(self._new_category_page("ai"), "🧠 AI 总配置")

        # ---- 后端模式：云端 / 本地（modeBtn checked 高亮，与插件页同款）----
        mode_ctl = QWidget()
        mode_row = QHBoxLayout(mode_ctl)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self._ai_mode_cloud = QPushButton("☁ 云端", self)
        self._ai_mode_local = QPushButton("💻 本地", self)
        for _b in (self._ai_mode_cloud, self._ai_mode_local):
            _b.setObjectName("modeBtn")
            _b.setCheckable(True)
            _b.setFixedHeight(28)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_mode_cloud.clicked.connect(
            lambda: self._on_ai_mode("cloud"))
        self._ai_mode_local.clicked.connect(
            lambda: self._on_ai_mode("local"))
        mode_row.addWidget(self._ai_mode_cloud)
        mode_row.addWidget(self._ai_mode_local)
        self._ai_row_mode = add_row(
            gv, "后端模式", "云端 API 或本机 llama-server；切换即显示对应配置区",
            mode_ctl)

        # ---- 云端字段（OpenAI 兼容 /chat/completions；仅云端模式显示）----
        self._ai_url = QLineEdit(str(self._config.get("ai_cloud_base_url", "")
                                     or ""))
        self._ai_url.setPlaceholderText("https://api.deepseek.com/v1")
        self._ai_url.setFixedWidth(240)
        self._ai_row_url = add_row(
            gv, "云端地址", "OpenAI 兼容接口；回环地址（Ollama 等）可免 key",
            self._ai_url)
        self._ai_key = QLineEdit(str(self._config.get("ai_cloud_api_key", "")
                                     or ""))
        self._ai_key.setPlaceholderText("API key（回环地址可留空）")
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setFixedWidth(240)
        self._ai_row_key = add_row(gv, "云端 Key", "云端服务的 API key；只存本机配置文件",
                                   self._ai_key)
        self._ai_model = QLineEdit(str(self._config.get("ai_cloud_model", "")
                                       or ""))
        self._ai_model.setPlaceholderText("模型名，如 deepseek-chat")
        self._ai_model.setFixedWidth(240)
        self._ai_row_model = add_row(gv, "云端模型", "模型名，如 deepseek-chat / qwen-plus",
                                     self._ai_model)

        # ---- 本地字段（宿主自己拉起 llama-server；仅本地模式显示）----
        exe_ctl = QWidget()
        exe_row = QHBoxLayout(exe_ctl)
        exe_row.setContentsMargins(0, 0, 0, 0)
        exe_row.setSpacing(8)
        self._ai_exe = QLineEdit(str(self._config.get("ai_local_server_exe",
                                                      "") or ""))
        self._ai_exe.setPlaceholderText("llama-server.exe 路径")
        self._ai_exe.setFixedWidth(160)
        exe_row.addWidget(self._ai_exe)
        exe_btn = QPushButton("浏览…")
        exe_btn.setObjectName("secondaryBtn")
        exe_btn.setFixedHeight(30)
        exe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exe_btn.clicked.connect(
            lambda: self._on_ai_browse(self._ai_exe, "程序 (*.exe)"))
        exe_row.addWidget(exe_btn)
        self._ai_row_exe = add_row(gv, "本地程序", "llama.cpp 的 llama-server.exe 路径",
                                   exe_ctl)

        gguf_ctl = QWidget()
        gguf_row = QHBoxLayout(gguf_ctl)
        gguf_row.setContentsMargins(0, 0, 0, 0)
        gguf_row.setSpacing(8)
        self._ai_gguf = QLineEdit(str(self._config.get("ai_local_gguf", "")
                                      or ""))
        self._ai_gguf.setPlaceholderText("模型文件（.gguf）")
        self._ai_gguf.setFixedWidth(160)
        gguf_row.addWidget(self._ai_gguf)
        gguf_btn = QPushButton("浏览…")
        gguf_btn.setObjectName("secondaryBtn")
        gguf_btn.setFixedHeight(30)
        gguf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gguf_btn.clicked.connect(
            lambda: self._on_ai_browse(self._ai_gguf, "GGUF 模型 (*.gguf)"))
        gguf_row.addWidget(gguf_btn)
        self._ai_row_gguf = add_row(gv, "本地模型", ".gguf 模型文件路径", gguf_ctl)

        self._ai_port = QLineEdit(str(self._config.get("ai_local_port", 8095)
                                      or 8095))
        self._ai_port.setFixedWidth(72)
        self._ai_row_port = add_row(
            gv, "本地端口", "宿主本地服务端口（默认 8095，避开插件自管端口）",
            self._ai_port)

        local_ctl = QWidget()
        local_row = QHBoxLayout(local_ctl)
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.setSpacing(8)
        self._ai_local_btn = QPushButton("启动本地服务", self)
        self._ai_local_btn.setObjectName("primaryBtn")
        self._ai_local_btn.setFixedHeight(30)
        self._ai_local_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_local_btn.clicked.connect(self._on_ai_local_toggle)
        local_row.addWidget(self._ai_local_btn)
        self._ai_local_status = QLabel("本地服务未运行")
        self._ai_local_status.setObjectName("hintLabel")
        local_row.addWidget(self._ai_local_status, 1)
        self._ai_row_local = add_row(
            gv, "本地服务", "启动后所有接入插件共用；退出程序自动结束、不占显存",
            local_ctl)

        # ---- 保存并测试 ----
        self._ai_save_btn = IconButton("save", text="保存并测试连接", icon_size=14,
                                       object_name="primaryBtn", parent=self,
                                       off_color="on_primary",
                                       hover_color="on_primary")
        self._ai_save_btn.setFixedHeight(30)
        self._ai_save_btn.clicked.connect(self._on_ai_save_test)
        add_row(gv, "保存并测试连接", "配置落盘；云端发 1-token 探活，本地拉起服务并探活",
                self._ai_save_btn)
        self._ai_status = QLabel("")
        self._ai_status.setObjectName("hintLabel")
        self._ai_status.setMinimumHeight(18)
        gv.addWidget(self._ai_status)

        # ---- 接入插件（多选下拉：已安装 + 已启用 + 声明 ai 能力）----
        self._ai_plugins_combo = PluginsPickButton(self)
        self._ai_plugins_combo.setFixedWidth(240)
        self._ai_plugins_combo.changed.connect(self._on_ai_plugins_changed)
        self._ai_plugins_reload()
        add_row(gv, "接入插件",
                "勾选哪些插件，哪些就改用这里配置的 AI 后端（设置改完即生效）；"
                "未勾选的插件继续用自己的配置",
                self._ai_plugins_combo, last=True)

        # 宿主 AI 服务状态跟随（设置页常驻；销毁时退订防死引用）
        AI_SERVER.add_listener(self._on_ai_local_status)
        self.destroyed.connect(
            lambda: AI_SERVER.remove_listener(self._on_ai_local_status))
        self._ai_apply_mode_ui()

        # ---- 恢复默认：全局操作 → 页底常驻栏 ----
        # 不属于任何分类，放 outer 层常驻可见：跨分类的全局动作不用翻找。
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(8)
        # 所有设置项均已实时持久化（改动即写盘并联动），无"保存"按钮
        self._reset_btn = QPushButton("↺ 恢复默认设置")
        self._reset_btn.setObjectName("secondaryBtn")
        self._reset_btn.setFixedHeight(32)
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setToolTip("所有设置恢复默认值；软件导航条目、窗口/悬浮球位置会保留")
        self._reset_btn.clicked.connect(self._on_reset_settings)
        foot.addStretch()
        foot.addWidget(self._reset_btn)
        outer.addWidget(make_separator())
        outer.addSpacing(4)
        outer.addLayout(foot)

        # ================= 关于 =================
        about_box = self._group_box()
        ab_v = QVBoxLayout(about_box)
        ab_v.setContentsMargins(14, 10, 14, 12)
        ab_v.setSpacing(6)

        about_title = QLabel("ℹ️ 关于")
        about_title.setObjectName("sectionLabel")
        ab_v.addWidget(about_title)

        about_text = QLabel(
            f"生活悬浮球 v{APP_VERSION} | PyQt6 + python-docx\n"
            "功能：知识卡片 / 日程任务 / 临时笔记 / 碎片合并\n\n"
            "快捷键：Esc 退出 | Ctrl+W/H 隐藏 | Ctrl+T 主题 | Ctrl+K 站内搜索\n"
            "F1 使用说明 | Ctrl+1~7 切换面板 | Ctrl+Alt+K 快速捕捉\n"
            "Ctrl+Alt+S 截图钉屏 | 右键悬浮球 / 右键卡片也可退出"
        )
        about_text.setObjectName("hintLabel")
        about_text.setWordWrap(True)
        ab_v.addWidget(about_text)

        av = self._new_category_page("about")

        # ---- 软件更新（手动检查：点击时才联网一次，离线零影响）----
        # 产品承诺「程序不联网」不变：不点按钮就零网络请求（自动检查见下，
        # 同样只 GET Releases latest，失败静默）；发现新版只给下载页入口，
        # 不自动下载（立场详见 update_checker 模块注释）。
        gv = group(av, "🔄 软件更新")

        self._ver_label = QLabel(self._version_label_text())
        self._ver_label.setObjectName("hintLabel")
        add_row(gv, "当前版本", "与 CHANGELOG、Release tag 三处同步维护",
                self._ver_label)

        # 自动检查（2.3）：启动后延迟静默查一次/天，托盘气泡被动提示；
        # 勾选行为与其它开关一致——toggled 即写配置并落盘
        self._set_auto_update = self._toggle("auto_check_updates", True)
        self._set_auto_update.toggled.connect(self._on_auto_update_toggled)
        add_row(gv, "自动检查更新",
                "启动后每天最多静默检查一次新版本（失败不提示、"
                "发现新版只弹一次托盘气泡），不自动下载",
                self._set_auto_update)

        upd_ctl = QWidget()
        upd_row = QHBoxLayout(upd_ctl)
        upd_row.setContentsMargins(0, 0, 0, 0)
        upd_row.setSpacing(8)
        self._upd_btn = IconButton("search", text="检查更新", icon_size=14,
                                   object_name="secondaryBtn")
        self._upd_btn.setFixedHeight(30)
        self._upd_btn.clicked.connect(self._on_check_update)
        upd_row.addWidget(self._upd_btn)
        self._upd_open_btn = QPushButton("🌐 打开下载页")
        self._upd_open_btn.setObjectName("secondaryBtn")
        self._upd_open_btn.setFixedHeight(30)
        self._upd_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upd_open_btn.setVisible(False)
        self._upd_open_btn.clicked.connect(self._on_open_downloads)
        upd_row.addWidget(self._upd_open_btn)
        add_row(gv, "检查更新",
                "点击时才访问一次 GitHub Releases API（不携带任何本机数据）；"
                "发现新版只给下载页入口，不自动下载；离线不影响任何功能",
                upd_ctl)
        self._upd_status = QLabel("")
        self._upd_status.setObjectName("hintLabel")
        self._upd_status.setMinimumHeight(18)
        self._upd_status.setWordWrap(True)
        gv.addWidget(self._upd_status)
        self._upd_getter = None      # 懒创建（首次点击时建异步 GET 桥）

        # ---- 日志入口（3.2）：崩溃可感知的配套——出问题得有地方看现场 ----
        log_ctl = QWidget()
        log_row = QHBoxLayout(log_ctl)
        log_row.setContentsMargins(0, 0, 0, 0)
        log_row.setSpacing(8)
        self._open_log_btn = QPushButton("📂 打开日志")
        self._open_log_btn.setObjectName("secondaryBtn")
        self._open_log_btn.setFixedHeight(30)
        self._open_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_log_btn.clicked.connect(self._on_open_log)
        log_row.addWidget(self._open_log_btn)
        add_row(gv, "打开日志",
                "在文件管理器中打开数据目录 float_data/（app.log 在里面，"
                "报障时可整份提供给开发者）",
                log_ctl, last=True)

        av.addWidget(about_box)

        # 每个分类页尾统一补 stretch：卡片顶对齐、不随窗口高度拉伸
        for cat_v in self._cat_vboxes.values():
            cat_v.addStretch()

        # 初始落在第一个分类（不做跨会话记忆——设置访问短平快）
        self.show_category(SETTINGS_CATEGORIES[0][0])

    # ---- 页内分类导航 ----
    def _build_nav_rail(self) -> QWidget:
        """左分类导航栏：按 SETTINGS_CATEGORIES 逐项建钮，QButtonGroup 互斥"""
        rail = QWidget()
        rv = QVBoxLayout(rail)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self._cat_btns = {}
        self._cat_index = {}
        self._cat_group = QButtonGroup(self)
        self._cat_group.setExclusive(True)
        for idx, (key, icon, label) in enumerate(SETTINGS_CATEGORIES):
            btn = QPushButton(f"{icon}  {label}")
            btn.setObjectName("settingsNavBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, k=key: self.show_category(k))
            self._cat_group.addButton(btn)
            self._cat_btns[key] = btn
            self._cat_index[key] = idx
            rv.addWidget(btn)
        rv.addStretch()
        rail.setFixedWidth(140)
        return rail

    def _new_category_page(self, key: str) -> QVBoxLayout:
        """为分类 key 建一个滚动页（挂入 stack），返回其内容竖直布局。

        边距沿用原单页 scroll 的约定：右留 8px 给滚动条；
        页尾 stretch 由 _build_ui 末尾统一补。
        """
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
        scroll.setWidget(inner)
        self._cat_stack.addWidget(scroll)
        self._cat_vboxes[key] = v
        return v

    def show_category(self, key: str):
        """切到指定分类；未知 key 静默忽略（导航点击与外部深链共用此入口）"""
        btn = self._cat_btns.get(key)
        if btn is None:
            return
        btn.setChecked(True)   # QButtonGroup 互斥，自动取消上一个选中
        self._cat_stack.setCurrentIndex(self._cat_index[key])

    def apply_theme(self):
        """主题切换：同步开关配色与主题按钮选中态（由主窗口 _apply_theme 调用）"""
        theme = self._host.current_theme
        for sw in getattr(self, "_toggles", []):
            sw.set_theme(theme)
        if hasattr(self, "_set_theme_light"):
            # 三态：显式 light/dark 亮对应钮；"follow"（跟随系统）亮第三钮
            self._set_theme_light.setChecked(theme == "light")
            self._set_theme_dark.setChecked(theme == "dark")
            self._set_theme_follow.setChecked(theme == "follow")

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
        self._set_theme_follow.setChecked(self._host.current_theme == "follow")
        # 版本行提示：静默检查可能在面板关闭期间写入 latest_known_version
        self._ver_label.setText(self._version_label_text())
        if hasattr(self, '_set_auto_update'):
            self._set_auto_update.blockSignals(True)
            self._set_auto_update.setChecked(
                bool(self._config.get("auto_check_updates", True)))
            self._set_auto_update.blockSignals(False)
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
        # AI 总配置：恢复默认等批量重置后，把字段 / 模式 / 接入下拉框同步回来
        if hasattr(self, '_ai_url'):
            self._ai_refresh_fields()
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
        if hasattr(self, '_set_reduce_motion'):
            self._set_reduce_motion.blockSignals(True)
            self._set_reduce_motion.setChecked(
                bool(self._config.get("reduce_motion", False)))
            self._set_reduce_motion.blockSignals(False)
        motion.set_reduce_motion(
            bool(self._config.get("reduce_motion", False)))
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
        """设置面板切换主题（light / dark / follow 三态）"""
        if theme_name == "follow":
            self._on_set_follow_theme()
            return
        self._host.apply_external_theme(theme_name)
        self.refresh()

    def _on_set_follow_theme(self):
        """切到「跟随系统」（3.1）。

        主窗口 apply_external_theme 只收 light / dark，follow 在这里
        落配置，再驱动主窗口走既有换主题路径：_apply_theme 重取配色
        （get_colors 内部现读系统 scheme），theme_changed 广播具体主题
        名给球 / 卡片 / 便签 / 快捕条 / 截图钉屏。
        """
        host = self._host
        self._config.set("theme", "follow")
        self._config.save()
        if host._theme != "follow":
            host._theme = "follow"
            host._apply_theme()
            host.theme_changed.emit(resolve_theme_name("follow"))
        self.refresh()

    def _on_ui_scale_changed(self, index: int):
        """「界面缩放」档位变更（3.5 活字缩放）：落盘 + 立即重设全局字号。

        与「跟随系统」同一条刷新链：apply_app_font 重设 QApplication
        字号（未显式指定 font-size 的控件自动重排），再走主窗口
        _apply_theme + theme_changed 广播让 QSS 全量重载、球 / 卡片 /
        便签 / 快捕条 / 截图钉屏同步。ui_scale 只缩放字号不缩放 px 布局，
        刻意的低风险取舍（见 theme.BASE_FONT_PT）。
        """
        scale = self._set_ui_scale.itemData(index)
        if scale is None or int(scale) == self._config.get("ui_scale", 100):
            return
        self._config.set("ui_scale", int(scale))
        self._config.save()
        apply_app_font(int(scale))
        host = self._host
        apply_theme = getattr(host, "_apply_theme", None)
        if callable(apply_theme):
            apply_theme()
        theme_signal = getattr(host, "theme_changed", None)
        if theme_signal is not None:
            theme_signal.emit(resolve_theme_name(
                getattr(host, "_theme", "dark")))

    def _on_replay_onboarding(self):
        """「🔄 重看引导」（3.4）：置回未完成态并弹出同一欢迎向导。

        复用首启状态链：mark_show_again 置 first_run_done=False 落盘，
        向导关闭（完成 / Esc / 跳过）时 mark_done 置回 True——看完不会
        「下次启动又弹」。
        """
        from src import onboarding
        onboarding.mark_show_again(self._config)
        dlg = onboarding.WelcomeDialog(host=self._host, parent=self)
        dlg.finished.connect(
            lambda _result: onboarding.mark_done(self._config))
        dlg.exec()
        dlg.deleteLater()

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

    # ---- 🧠 AI 总配置（2026-09-29：插件 AI 后端单一真相源） ----
    def _ai_poster(self):
        """懒创建探活桥（与插件网络桥同一条受控通道，UI 线程回调）"""
        if getattr(self, "_ai_poster_fn", None) is None:
            self._ai_poster_fn = make_async_poster()
        return self._ai_poster_fn

    def _ai_refresh_fields(self):
        """把配置值同步进 AI 卡控件（恢复默认 / 外部改配置后调用）"""
        self._ai_url.setText(str(self._config.get("ai_cloud_base_url", "") or ""))
        self._ai_key.setText(str(self._config.get("ai_cloud_api_key", "") or ""))
        self._ai_model.setText(str(self._config.get("ai_cloud_model", "") or ""))
        self._ai_exe.setText(str(self._config.get("ai_local_server_exe", "") or ""))
        self._ai_gguf.setText(str(self._config.get("ai_local_gguf", "") or ""))
        self._ai_port.setText(str(self._config.get("ai_local_port", 8095) or 8095))
        self._ai_apply_mode_ui()
        self._ai_plugins_reload()

    def _ai_apply_mode_ui(self):
        """当前 ai_backend_mode 反映到模式按钮，并**只显示对应配置区**：

        云端模式 → 云端地址/Key/模型；本地模式 → 程序/模型/端口/本地服务。
        分隔线登记在 _row_sep 里，随所属行一起显隐——只藏行不藏线的话，
        卡片里会留下一串悬空横线撑出空档（拆页后 AI 独占一页尤其明显）。
        """
        local = self._config.get("ai_backend_mode", "cloud") == "local"
        self._ai_mode_local.setChecked(local)
        self._ai_mode_cloud.setChecked(not local)
        for row, show in (
            (self._ai_row_url, not local), (self._ai_row_key, not local),
            (self._ai_row_model, not local), (self._ai_row_exe, local),
            (self._ai_row_gguf, local), (self._ai_row_port, local),
            (self._ai_row_local, local),
        ):
            row.setVisible(show)
            sep = self._row_sep.get(row)
            if sep is not None:
                sep.setVisible(show)

    def _on_ai_mode(self, mode: str):
        """云端 / 本地模式切换：即时落盘（探活在保存并测试时做）"""
        if self._config.get("ai_backend_mode") != mode:
            self._config.set("ai_backend_mode", mode)
            self._config.save()
        self._ai_apply_mode_ui()

    def _on_ai_browse(self, line_edit, name_filter: str):
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择文件", "", name_filter)
        if path:
            line_edit.setText(path)

    def _ai_collect(self) -> dict:
        """AI 卡当前值 → 配置键值 dict（不落盘，落盘在保存/启动处）"""
        try:
            port = int(self._ai_port.text().strip() or 8095)
        except ValueError:
            port = 8095
        return {
            "ai_backend_mode": ("local"
                                if self._ai_mode_local.isChecked()
                                else "cloud"),
            "ai_cloud_base_url": self._ai_url.text().strip(),
            "ai_cloud_api_key": self._ai_key.text().strip(),
            "ai_cloud_model": self._ai_model.text().strip(),
            "ai_local_server_exe": self._ai_exe.text().strip(),
            "ai_local_gguf": self._ai_gguf.text().strip(),
            "ai_local_port": port,
        }

    def _on_ai_save_test(self):
        """保存 AI 总配置；云端发 1-token 探活，本地拉起服务（状态行走广播）"""
        cfg = self._ai_collect()
        for key, val in cfg.items():
            self._config.set(key, val)
        self._config.save()
        # 本地端点（llama-server / 回环 base_url）登记进插件桥回环白名单（1.5 配套）
        sync_loopback_allowlist(self._config)
        if cfg["ai_backend_mode"] == "local":
            self._ai_status.setText("配置已保存，正在拉起本地服务…")
            AI_SERVER.start(self._ai_poster(), cfg["ai_local_server_exe"],
                            cfg["ai_local_gguf"], cfg["ai_local_port"])
            return
        base = cfg["ai_cloud_base_url"].rstrip("/")
        model = cfg["ai_cloud_model"]
        if not base or not model:
            self._ai_status.setText("⚠ 地址或模型名为空，已保存但无法测试")
            return
        if not (base.startswith("http://") or base.startswith("https://")):
            self._ai_status.setText(f"⚠ 地址必须以 http:// 或 https:// 开头：{base}")
            return
        self._ai_status.setText("配置已保存，正在测试连接…")
        headers = ({"Authorization": f"Bearer {cfg['ai_cloud_api_key']}"}
                   if cfg["ai_cloud_api_key"] else {})
        body = {"model": model, "max_tokens": 1, "temperature": 0,
                "messages": [{"role": "user", "content": "ping"}]}
        ok = self._ai_poster()(
            f"{base}/chat/completions", headers, body, 15.0,
            self._on_ai_probe_done)
        if not ok:
            # 桥拒绝时也会回调一次 ok=False 的结果，这里只兜底恢复
            self._ai_status.setText("⚠ 探活请求未能发出（详见 app.log）")

    def _on_ai_probe_done(self, result: dict):
        """云端探活回调（UI 线程）：✓ / ✗ + 针对性提示"""
        if result.get("ok"):
            self._ai_status.setText("✓ 连接成功，接入的插件即刻可用")
            return
        err = str(result.get("error") or "未知错误")
        hint = ""
        if "401" in err or "403" in err:
            hint = "（key 缺失或无效）"
        elif "404" in err:
            hint = "（地址或模型名不对，地址应以 /v1 结尾）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（超时，可重试一次）"
        elif "refused" in err.lower():
            hint = "（端口没有服务在听）"
        self._ai_status.setText(f"✗ 连接失败：{err}{hint}")

    def _on_ai_local_toggle(self):
        """启动 / 停止宿主本地 AI 服务（先落盘当前字段再启动）"""
        if AI_SERVER.running:
            AI_SERVER.stop()
            return
        cfg = self._ai_collect()
        for key, val in cfg.items():
            self._config.set(key, val)
        self._config.save()
        AI_SERVER.start(self._ai_poster(), cfg["ai_local_server_exe"],
                        cfg["ai_local_gguf"], cfg["ai_local_port"])

    def _on_ai_local_status(self, status: str, detail: str):
        """宿主 AI 服务状态广播 → 按钮文案 + 状态行"""
        self._ai_local_status.setText(detail or status)
        self._ai_local_btn.setText(
            "停止本地服务" if status in (ST_READY, ST_STARTING)
            else "启动本地服务")

    def _ai_candidate_plugins(self) -> list:
        """接入下拉框的候选：已安装 + 已启用 + manifest 声明 ai 能力"""
        loader = getattr(self._host, "_plugin_loader", None)
        if loader is None:
            return []
        disabled = set(self._config.get("plugins_disabled", []) or [])
        out = []
        try:
            plugins = loader.loaded_plugins()
        except Exception:                 # noqa: BLE001 - 面板不能因插件崩
            return []
        for lp in plugins or []:
            manifest = getattr(lp, "manifest", None) or {}
            caps = manifest.get("capabilities") or []
            if "ai" not in caps:
                continue
            pid = getattr(lp, "plugin_id", "")
            if not pid or pid in disabled:
                continue
            out.append((pid, str(manifest.get("name") or pid)))
        return out

    def _ai_plugins_reload(self):
        """重建接入插件下拉框（选项 = 候选；勾选态 = 配置里的 ai_plugins）"""
        self._ai_plugins_combo.reload(
            self._config.get("ai_plugins", []) or [],
            self._ai_candidate_plugins())

    def _on_ai_plugins_changed(self, selected: list):
        """接入集合变化：即时落盘（插件下一发请求即生效，无需重启）"""
        self._config.set("ai_plugins", list(selected))
        self._config.save()

    # ---- 软件更新（手动检查；离线零影响，立场见 update_checker 注释） ----
    def _on_check_update(self):
        """点「检查更新」：一次 GET 拉 Releases latest，回调在 UI 线程比对"""
        if self._upd_getter is None:
            self._upd_getter = make_async_getter()
        self._upd_btn.setEnabled(False)
        self._upd_open_btn.setVisible(False)
        self._upd_status.setText("正在检查更新…")
        ok = self._upd_getter(RELEASES_API_URL, check_headers(),
                              CHECK_TIMEOUT_S, self._on_update_result)
        if not ok:
            # 桥拒绝时也会回调一次 ok=False 的结果，这里只兜底恢复按钮
            self._upd_status.setText("⚠ 检查请求未能发出（详见 app.log）")

    def _on_update_result(self, result: dict):
        """更新检查回调（UI 线程）：新版 → 提示 + 下载页出口；按原因给文案"""
        self._upd_btn.setEnabled(True)
        if not result.get("ok"):
            err = str(result.get("error") or "未知错误")
            if "HTTP 403" in err:
                hint = "（GitHub API 限流，每小时 60 次，请稍后再试）"
            elif "timed out" in err.lower() or "timeout" in err.lower():
                hint = "（网络超时，可重试一次）"
            else:
                hint = "（无法连接 GitHub，离线不影响任何功能）"
            self._upd_status.setText(f"✗ 检查失败：{err}{hint}")
            return
        tag = extract_tag(result.get("body") or "")
        if not tag:
            self._upd_status.setText("⚠ 响应格式异常，请稍后再试")
            return
        if is_newer(tag):
            self._upd_status.setText(
                f"🆕 发现新版本 {tag}（当前 v{APP_VERSION}），可打开下载页获取")
            self._upd_open_btn.setVisible(True)
        else:
            self._upd_status.setText(f"✓ 已是最新（当前 v{APP_VERSION}）")

    def _on_open_downloads(self):
        """系统浏览器打开 Releases 页；失败在状态行提示而非弹窗打断"""
        try:
            os.startfile(RELEASES_PAGE_URL)
            self._upd_status.setText("已在浏览器打开下载页")
        except OSError as exc:
            self._upd_status.setText(f"✗ 打开浏览器失败：{exc!r}")

    def _version_label_text(self) -> str:
        """「当前版本」行文案：静默检查发现过新版本（≠ 当前版本）时旁注提示。

        不弹窗——用户下次打开设置页自然看到；等版本号追上后提示自动消失。
        """
        known = str(self._config.get("latest_known_version", "") or "").strip()
        if known and known not in (f"v{APP_VERSION}", APP_VERSION):
            return f"v{APP_VERSION}（有新版本 {known}）"
        return f"v{APP_VERSION}"

    def _on_auto_update_toggled(self, on: bool):
        """自动检查更新开关：即时落盘（下次启动的静默检查读此值）"""
        self._config.set("auto_check_updates", bool(on))
        self._config.save()

    def _on_open_log(self):
        """「📂 打开日志」（3.2）：系统文件管理器打开数据目录 float_data/。

        打开目录而非 app.log 文件本身——文件管理器里还能顺带看到
        config.json / 备份等现场；路径取自 app_paths 的统一入口。
        """
        QDesktopServices.openUrl(QUrl.fromLocalFile(get_data_dir()))

    def _on_reset_settings(self):
        """恢复默认设置：二次确认 → 重置配置 → 广播全部联动信号 → 刷新面板"""
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "将把所有设置恢复为默认值（主题、剪贴板、悬浮球行为等）。\n"
            "软件导航条目、窗口位置、悬浮球位置会保留；\n"
            "插件中心里单独停用过的插件会一并恢复为启用。\n\n确定继续？",
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

    def _on_reduce_motion_changed(self, checked: bool):
        """减弱动效开关：即时持久化 + 翻转 motion 总闸（界面下一帧即瞬显）"""
        checked = bool(checked)
        if checked != bool(self._config.get("reduce_motion", False)):
            self._config.set("reduce_motion", checked)
            self._config.save()
        motion.set_reduce_motion(checked)

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
