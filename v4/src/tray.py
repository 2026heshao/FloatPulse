# -*- coding: utf-8 -*-
"""
====================================================================
系统托盘控制器 - TrayController（D1-lite：自 knowledge_ball 抽出）
====================================================================
职责：托盘图标创建/常驻、左键点击行为、右键菜单构建（主窗口显隐 /
悬浮球显隐 / 便签子菜单 / 退出）、气泡提示（首次收进托盘提示、config
损坏重置告知）。任务提醒调度、版本更新检查、
番茄钟气泡等业务接线仍留在装配处（knowledge_ball.main），经
show_message() 发托盘气泡——本类只做托盘本体，不持有业务数据。

依赖注入面（显式传参，无全局态）：
  __init__(icon)        —— 仅创建图标。时序与原实现一致：早于单实例检测
                           （第二实例启动时也会短暂创建托盘图标再退出，
                           属既有行为，本类不改变它）。
  attach(main_window, config)      —— 窗口/配置就绪：接点击与气泡信号。
  build_menu(...)                  —— 菜单回调闭包就绪：构建右键菜单。

闭包依赖通过公开回调注入（避免装配处反向直戳托盘内部）：
  toggle_main_window      : 菜单「显示 / 隐藏主窗口」（无参）
  toggle_ball_visibility  : 菜单「显示 / 隐藏悬浮球」（无参；含 ball_visible
                            配置落盘，语义由装配处闭包保证）
  quit_app                : 菜单「退出程序」（= _safe_quit）
  sticky_manager          : 便签子菜单数据源（只调其公开 API：
                            get_all / raise_sticky / bring_all_to_front /
                            close_all）
====================================================================
"""

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from src.constants import DEFAULT_THEME
from src.icon_render import icon as render_icon
from src.logger import get_logger
from src.theme import get_colors, resolve_theme_name


# 便签种类 → 自绘图标名（UI 重构 03：托盘便签项补图标位，替代原「📋/📄」
# 文字前缀）。kind 取值见 Sticky.KINDS；未知种类兜底 notes。
_STICKY_KIND_ICON = {"note": "notes", "task": "tasks", "fragment": "fragments"}


class TrayController:
    """系统托盘：图标 + 点击 + 右键菜单 + 气泡提示。"""

    TOOLTIP = "生活悬浮球"

    def __init__(self, icon: QIcon):
        # 创建即设图标/提示/可见（与原实现逐字等价）
        self._icon_widget = QSystemTrayIcon()
        self._icon_widget.setIcon(icon)
        self._icon_widget.setToolTip(self.TOOLTIP)
        self._icon_widget.setVisible(True)
        self._main_window = None
        self._config = None

    # ---------------- 装配（依赖就绪后调用，两段式，对应原实现顺序）----------------
    def attach(self, main_window, config):
        """主窗口与配置就绪：接托盘点击 / 气泡点击信号。

        对应原实现中 activated / messageClicked 的连接时机（菜单构建之前），
        此后 notify_hidden_to_tray / notify_startup_once 即可用。
        """
        self._main_window = main_window
        self._config = config
        self._icon_widget.activated.connect(self._on_activated)
        self._icon_widget.messageClicked.connect(self._on_message_clicked)

    def build_menu(self, toggle_main_window, toggle_ball_visibility, quit_app,
                   sticky_manager):
        """右键菜单构建（回调闭包就绪后调用）。

        toggle_main_window      : 菜单「显示 / 隐藏主窗口」回调（无参）
        toggle_ball_visibility  : 菜单「显示 / 隐藏悬浮球」回调（无参，
                                  ball_visible 配置落盘由回调负责）
        quit_app                : 菜单「退出程序」回调（= _safe_quit）
        sticky_manager          : 便签子菜单数据源（只调其公开 API：
                                  get_all / raise_sticky / bring_all_to_front /
                                  close_all）
        """
        menu = QMenu()
        self._tray_menu = menu   # setContextMenu 不接管所有权，必须自持引用
        act_main = menu.addAction("显示 / 隐藏主窗口")
        act_ball = menu.addAction("显示 / 隐藏悬浮球")

        # ---- 便签子菜单：列出已钉便签 + 全部置前 / 全部关闭 ----
        # UI 重构 03：托盘菜单一律纯文字，去 emoji 前缀
        sticky_menu = QMenu("便签", menu)

        def _rebuild_sticky_menu():
            sticky_menu.clear()
            # 图标取当前主题次级文字色（config 缺失时回落 DEFAULT_THEME）
            theme = resolve_theme_name(
                self._config.get("theme", DEFAULT_THEME)
                if self._config else DEFAULT_THEME)
            icon_color = get_colors(theme)["text_secondary"]
            entries = sticky_manager.get_all()
            if not entries:
                empty = sticky_menu.addAction("（暂无便签）")
                empty.setEnabled(False)
            else:
                for sid, title, _aid, kind in entries:
                    act = sticky_menu.addAction(title[:24])
                    act.setIcon(render_icon(
                        _STICKY_KIND_ICON.get(kind, "notes"), 14, icon_color))
                    act.triggered.connect(
                        lambda _checked=False, s=sid: sticky_manager.raise_sticky(s))
                sticky_menu.addSeparator()
                front = sticky_menu.addAction("全部置前")
                front.triggered.connect(sticky_manager.bring_all_to_front)
            close_all = sticky_menu.addAction("全部关闭")
            close_all.triggered.connect(sticky_manager.close_all)

        sticky_menu.aboutToShow.connect(_rebuild_sticky_menu)
        menu.addMenu(sticky_menu)
        menu.addSeparator()
        act_quit = menu.addAction("退出程序")
        act_main.triggered.connect(toggle_main_window)
        act_ball.triggered.connect(toggle_ball_visibility)
        act_quit.triggered.connect(quit_app)
        self._icon_widget.setContextMenu(menu)

    # ---------------- 点击行为 ----------------
    def _on_activated(self, reason):
        """托盘图标单击 → 切换主窗口显示/隐藏（不退出程序）。

        只响应单击（Trigger），忽略双击/右键——与原实现一致。
        """
        get_logger().info(
            f"托盘点击: reason={reason}, 主窗口可见={self._main_window.isVisible()}")
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self._main_window.isVisible():
                self._main_window.hide()
                get_logger().info("托盘点击 → 隐藏主窗口")
            else:
                self._main_window.show()
                self._main_window.raise_()
                self._main_window.activateWindow()
                get_logger().info("托盘点击 → 显示主窗口")

    def _on_message_clicked(self):
        """点击提醒气泡 → 显示主窗口并切到任务页"""
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()
        self._main_window.show_page(1)

    # ---------------- 气泡提示 ----------------
    def show_message(self, title, body,
                     icon=QSystemTrayIcon.MessageIcon.Information, ms=6000):
        """托盘气泡统一出口（任务提醒 / 版本更新 / 番茄钟完成等经此发出）"""
        self._icon_widget.showMessage(title, body, icon, ms)

    def notify_hidden_to_tray(self):
        """3.3 首次「关窗收进托盘」→ 气泡提示一次（tray_hint_shown 防重复）。

        主窗口只发 hidden_to_tray 信号；提示与落盘都收在托盘侧。
        """
        if self._config.get("tray_hint_shown", False):
            return
        self.show_message(
            "已收进托盘",
            "程序仍在后台运行——从托盘图标或悬浮球随时找回。",
            QSystemTrayIcon.MessageIcon.Information, 6000)
        self._config.set("tray_hint_shown", True)
        self._config.save()
        get_logger().info("首次收进托盘提示已展示（tray_hint_shown → True）")

    def notify_startup_once(self):
        """启动一次性告知：config.json 损坏重置 → 托盘警示。

        损坏文件已由 ConfigManager 备份为 .corrupt.bak，这里只负责告知。
        （3.2 的「上次可能异常退出」气泡已按用户要求移除——开发场景
        强杀退出是常态，判定恒真、每次必弹成噪音；日志里的 [会话]
        成对标记仍可人工排障。）
        """
        if getattr(self._config, "load_reset_reason", None) == "corrupt":
            self.show_message(
                "设置已重置",
                "config.json 已损坏，程序已恢复默认设置；"
                "原文件备份为 float_data/config.json.corrupt.bak。",
                QSystemTrayIcon.MessageIcon.Warning, 8000)
            get_logger().warning("[启动] config.json 损坏，已回退默认配置并托盘提示用户")
