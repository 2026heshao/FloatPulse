# -*- coding: utf-8 -*-
"""
====================================================================
大窗口主UI模块  -  MainWindow
====================================================================
生活悬浮球的主窗口，承载复杂批量编辑功能。

设计原则：
  - 小卡片做高频轻量操作，大窗口做复杂批量管理
  - 左侧导航栏 + 右侧 QStackedWidget 五面板切换
  - 无边框圆角窗口 + 自定义标题栏（与小卡片风格一致）
  - 主题系统统一管理（浅色/深色），切换时发 theme_changed 信号
  - 关闭=隐藏（不退出程序，悬浮球仍在运行）

五个面板：
  0. 🧩 碎片工作台  - 碎片列表/筛选/搜索/合并/删除
  1. 📋 日程任务    - 任务输入/列表/批量勾选/右键编辑
  2. 📝 笔记管理    - 笔记列表 + 编辑区 + 自动保存
  3. 📚 知识库     - 段落列表/勾选加入碎片池/段落增删改
  4. ⚙️ 设置       - 主题切换/剪贴板上限/自动隐藏秒数/关于

依赖：
  - task_manager.TaskManager
  - note_manager.NoteManager
  - fragment_manager.FragmentManager
  - docx_manager.DocxManager
  - config.ConfigManager
  - clipboard_monitor.ClipboardMonitor
  - theme.get_main_window_qss
====================================================================
"""

import os
import sys
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QButtonGroup,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QTextEdit,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QMessageBox, QMenu, QSplitter, QApplication,
    QScrollArea, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QInputDialog, QSlider,
)
from PyQt6.QtCore import (
    Qt, QPoint, pyqtSignal, QDate, QTimer, QRect, QRectF, QSize, QEvent,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import QColor, QPainter, QAction, QIcon, QPixmap, QShortcut, QKeySequence

from src.theme import get_main_window_qss, get_colors
from src.constants import DEFAULT_THEME
from src.glass import GlassPanel, NavIndicator
from src.fragment_manager import TYPE_LABELS, TYPE_ICONS
from src.app_paths import get_base_dir, find_icon_file, get_screen_geometry
from src.merge_preview_dialog import MergePreviewDialog
from src.fragments_panel import FragmentsPanel
from src.tasks_panel import TasksPanel
from src.notes_panel import NotesPanel
from src.knowledge_panel import KnowledgePanel
from src.assets_panel import AssetsPanel
from src.nav_panel import NavPanel
from src.settings_panel import SettingsPanel


class MainWindow(QWidget):
    """生活悬浮球大窗口主UI"""

    # ---- 窗口尺寸常量 ----
    DEFAULT_WIDTH = 920
    DEFAULT_HEIGHT = 620
    # 当前默认尺寸即最小尺寸：窗口可放大自适应，不可小于默认尺寸
    MIN_WIDTH = DEFAULT_WIDTH
    MIN_HEIGHT = DEFAULT_HEIGHT
    SIDE_BAR_WIDTH = 168
    TITLE_BAR_HEIGHT = 48
    SHADOW_MARGIN = 18           # 阴影留白边距
    WINDOW_RADIUS = 14           # 窗口圆角（与设计稿一致）

    # ---- 信号 ----
    theme_changed = pyqtSignal(str)   # 主题切换时发射，参数为 "light"/"dark"
    data_changed = pyqtSignal(str)    # 数据变更时发射，参数为数据类型标识
    ball_visibility_changed = pyqtSignal(bool)  # 悬浮球显示/隐藏切换
    card_always_show_changed = pyqtSignal(bool)  # 小卡片保持显示模式切换
    asset_limits_changed = pyqtSignal(int, int)  # 临时素材上限变更（max_count, max_days）
    anim_speed_changed = pyqtSignal(float)       # 悬浮球动画速度变更
    auto_hide_seconds_changed = pyqtSignal(int)  # 悬浮球空闲吸边隐藏秒数变更

    def __init__(self, task_manager, note_manager, fragment_manager,
                 docx_manager, config_manager, clipboard_monitor,
                 temp_asset_manager=None, nav_manager=None):
        super().__init__()
        # 业务管理器实例（与悬浮球共享同一实例）
        self._task_manager = task_manager
        self._note_manager = note_manager
        self._fragment_manager = fragment_manager
        self._docx_manager = docx_manager
        self._config = config_manager
        self._clipboard_monitor = clipboard_monitor
        self._temp_asset_manager = temp_asset_manager
        self._nav_manager = nav_manager

        # 当前主题
        self._theme = self._config.get("theme", DEFAULT_THEME)

        # 拖动状态
        self._dragging = False
        self._drag_offset = QPoint()
        # 惰性还原状态：最大化时按下标题栏不立即还原窗口，
        # 等真正开始拖动才还原（修复双击标题栏闪动卡死 BUG）
        self._drag_pending_restore = False
        self._drag_press_ratio = 0.0

        # 最大化前的正常窗口几何（还原时精确恢复，不强制回默认尺寸）
        self._normal_geometry = None

        # 边缘缩放状态（方向字符串，None 表示非缩放中）
        self._resizing = None
        self._resize_start_global = QPoint()
        self._resize_start_geom = QRect()

        # 允许关闭标志（程序退出时使用）
        self._allow_close = False

        # 使用说明页面（F1 切换用）：记录进入说明页前的页面索引
        self._page_before_help = 0

        # 软件导航页面（嵌入 QStackedWidget 的页面组件，非弹窗）
        self._page_app_launcher = None

        # 窗口入场动画（仅首次显示播放一次）
        self._entrance_played = False
        self._entrance_anims = []

        # 初始化
        self._init_window()
        self._init_ui()
        self._init_shortcuts()
        self._init_context_menu()
        self._apply_theme()
        # 安装子控件事件过滤器：防止缩放指针残留在子控件上
        self._install_cursor_filter()
        # 默认首页：碎片工作台
        self._stack.setCurrentIndex(0)
        self.refresh_page("fragments")

    # ==================================================================
    # 快捷键初始化
    # ==================================================================
    def _init_shortcuts(self):
        """初始化主窗口快捷键"""
        # ESC: 退出整个程序（主窗口打开时也能生效）
        quit_shortcut = QShortcut(QKeySequence("Escape"), self)
        quit_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        quit_shortcut.activated.connect(self._quit_app)

        # Ctrl+W 或 Ctrl+H: 隐藏主窗口（不用 Ctrl+Q，它是 Qt 默认退出快捷键）
        hide_shortcut1 = QShortcut(QKeySequence("Ctrl+W"), self)
        hide_shortcut1.setContext(Qt.ShortcutContext.ApplicationShortcut)
        hide_shortcut1.activated.connect(self.hide)
        hide_shortcut2 = QShortcut(QKeySequence("Ctrl+H"), self)
        hide_shortcut2.setContext(Qt.ShortcutContext.ApplicationShortcut)
        hide_shortcut2.activated.connect(self.hide)

        # Ctrl+T: 切换主题
        theme_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        theme_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        theme_shortcut.activated.connect(self._toggle_theme)

        # F1: 进入/退出使用说明页面（应用级快捷键）
        help_shortcut = QShortcut(QKeySequence("F1"), self)
        help_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        help_shortcut.activated.connect(self._toggle_help_page)

        # Ctrl+1~7: 切换到对应页面
        for i in range(7):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i+1}"), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda idx=i: self._switch_page(idx))

    def _quit_app(self):
        """退出整个程序：重置页面为首页，然后退出"""
        # 重置页面状态为默认首页（碎片工作台）
        if hasattr(self, '_stack'):
            self._stack.setCurrentIndex(0)
        self._allow_close = True
        QApplication.quit()

    def _init_context_menu(self):
        """初始化右键菜单：退出程序"""
        self._menu = QMenu(self)
        self._menu.setStyleSheet(get_main_window_qss(self._theme))

        show_ball_action = QAction("显示/隐藏悬浮球", self._menu)
        show_ball_action.triggered.connect(self._toggle_ball_visibility)
        self._menu.addAction(show_ball_action)

        self._menu.addSeparator()

        quit_action = QAction("退出程序", self._menu)
        quit_action.triggered.connect(self._quit_app)
        self._menu.addAction(quit_action)

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    def _toggle_ball_visibility(self):
        """切换悬浮球显示/隐藏"""
        # 从 config 读取当前状态并切换
        current = self._config.get("ball_visible", True)
        new_val = not current
        self._config.set("ball_visible", new_val)
        self._config.save()
        self.ball_visibility_changed.emit(new_val)

    # ==================================================================
    # 窗口初始化
    # ==================================================================
    def _init_window(self):
        """窗口标志：无边框 + 普通窗口层级（不置顶）"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        # 开启鼠标跟踪：悬停时实时检测边缘并切换缩放指针
        self.setMouseTracking(True)
        # 初始位置：屏幕中央偏左，确保完全在可视区内
        self._ensure_on_screen(init=True)
        # 修复：无边框窗口默认缺少 WS_MINIMIZEBOX/WS_MAXIMIZEBOX 样式，
        # 导致点击任务栏按钮只能激活/还原、无法最小化/最大化隐藏
        try:
            import ctypes
            GWL_STYLE = -16
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_SYSMENU = 0x00080000
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_STYLE,
                style | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
        except Exception:
            pass

    # ==================================================================
    # 窗口层级容错：确保窗口位置始终在屏幕可视区内
    # ==================================================================
    def _ensure_on_screen(self, init: bool = False):
        """
        将窗口位置修正到主屏可视区内。
        - init=True：首次显示，主动定位到屏幕中央
        - init=False：仅做边界修正，保留用户原位置
        屏幕分辨率变化 / 外接显示器拔出 / 多屏坐标漂移等场景下兜底。
        """
        try:
            screen = get_screen_geometry()
            if init:
                # 初始居中
                x = screen.left() + (screen.width() - self.width()) // 2
                y = screen.top() + (screen.height() - self.height()) // 2
                self.move(max(screen.left(), x), max(screen.top(), y))
                return
            # 仅做边界修正
            pos = self.pos()
            w = self.width()
            h = self.height()
            x = pos.x()
            y = pos.y()
            # 至少保证窗口 100px 宽度在屏内，避免完全跑出屏幕
            if x + w - 100 < screen.left():
                x = screen.left()
            if x + 100 > screen.right():
                x = screen.right() - w
            if y + h - 60 < screen.top():
                y = screen.top()
            if y + 60 > screen.bottom():
                y = screen.bottom() - h
            self.move(max(screen.left(), x), max(screen.top(), y))
        except Exception:
            # 任何异常都不应阻塞窗口显示
            pass

    def showEvent(self, event):
        """窗口显示前确保位置在屏幕内；首次显示播放入场动画"""
        super().showEvent(event)
        self._ensure_on_screen(init=False)
        QTimer.singleShot(0, self._init_nav_indicator_position)
        self._play_entrance_animation()

    def _init_nav_indicator_position(self):
        """布局完成后把指示条对齐到当前选中项（不带动画，避免从 0 滑下来）"""
        if not hasattr(self, "_nav_group"):
            return
        btn = self._nav_group.checkedButton()
        if btn is None and hasattr(self, "_stack"):
            btn = self._nav_group.button(self._stack.currentIndex())
        self._move_nav_indicator(btn, animate=False)

    def _play_entrance_animation(self):
        """首次显示：淡入 + 10px 上移（网页级的进入观感，只播一次）"""
        if self._entrance_played:
            return
        self._entrance_played = True
        end_pos = self.pos()
        start_pos = QPoint(end_pos.x(), end_pos.y() + 10)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(200)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(260)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setWindowOpacity(0.0)
        self.move(start_pos)
        fade.start()
        slide.start()
        self._entrance_anims = [fade, slide]

    # ==================================================================
    # 全屏/还原切换
    # ==================================================================
    def _toggle_maximize(self):
        """
        全屏/还原切换：最大化铺满屏幕 ⇄ 还原到最大化前的尺寸位置。

        修复：还原时不再强制回到默认尺寸，而是精确恢复用户最大化前的
        自定义几何（未记录时才回退默认尺寸居中），消除大小来回跳变。
        """
        if self.isMaximized():
            self.showNormal()
            # 延迟到窗口状态切换完成后再恢复记忆几何，
            # 避免连续几何变更叠加造成卡顿/画面重叠
            QTimer.singleShot(0, self._restore_normal_geometry)
        else:
            # 最大化前记住当前正常几何（normalGeometry 在最大化时
            # 也能返回还原态几何，这里在非最大化分支直接取 geometry）
            geom = self._normal_geometry if self._normal_geometry else self.geometry()
            self._normal_geometry = QRect(geom)
            self.showMaximized()

    def _restore_normal_geometry(self):
        """
        还原为最大化前记住的正常几何。

        - 有记忆几何 → 精确恢复（尺寸 + 位置），不做强制居中
        - 无记忆几何 → 回退默认尺寸并居中
        - 不调用 repaint()，避免与 changeEvent 重绘叠加造成闪动
        """
        if self._normal_geometry is not None and not self._normal_geometry.isNull():
            geom = self._normal_geometry
            # 保证恢复位置在屏幕可视区内（防止分辨率变化后跑出屏幕）
            screen = get_screen_geometry()
            x = max(screen.left(), min(geom.x(), screen.right() - geom.width()))
            y = max(screen.top(), min(geom.y(), screen.bottom() - geom.height()))
            self.setGeometry(x, y, geom.width(), geom.height())
        else:
            self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
            self._center_on_screen()

    def _center_on_screen(self):
        """窗口居中到主屏工作区"""
        try:
            screen = get_screen_geometry()
            x = screen.left() + (screen.width() - self.width()) // 2
            y = screen.top() + (screen.height() - self.height()) // 2
            self.move(max(screen.left(), x), max(screen.top(), y))
        except Exception:
            pass

    def _update_max_btn(self):
        """根据窗口状态更新全屏/还原按钮的图标和提示"""
        if not hasattr(self, '_max_btn'):
            return
        if self.isMaximized():
            self._max_btn.setText("❐")
            self._max_btn.setToolTip("还原默认尺寸")
        else:
            self._max_btn.setText("⛶")
            self._max_btn.setToolTip("最大化窗口")

    def _apply_window_state_margins(self):
        """最大化时去除阴影边距（避免四周留白），还原时恢复"""
        if not hasattr(self, '_outer_layout'):
            return
        maximized = self.isMaximized()
        margin = 0 if maximized else self.SHADOW_MARGIN
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)

    def paintEvent(self, event):
        """手动绘制窗口外圈柔和阴影（最大化时不绘制）。

        不用 QGraphicsDropShadowEffect：其在窗口状态切换/缩放时
        有离屏缓存残影 bug，且大尺寸重渲染卡顿。
        """
        if self.isMaximized():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.SHADOW_MARGIN
        # 阴影基于容器区域（窗口内缩一个阴影边距），向下偏移模拟光源
        base = QRectF(self.rect()).adjusted(m, m, -m, -m)
        rings = 8  # 多圈半透明圆角矩形叠加成柔和渐变
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(rings, 0, -1):
            t = i / rings                       # 外圈 t→1，内圈 t→0
            expand = 2.0 + (m - 2.0) * t        # 内圈贴边，外圈扩到窗口边缘
            alpha = int(6 + 42 * (1.0 - t) ** 1.5)  # 内浓外淡
            r = base.adjusted(-expand, -expand + 4.0, expand, expand + 4.0)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(r, self.WINDOW_RADIUS, self.WINDOW_RADIUS)
        painter.end()
        super().paintEvent(event)

    def changeEvent(self, event):
        """窗口状态变化（最大化/还原，含任务栏/系统快捷键触发）时同步 UI"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_max_btn()
            self._apply_window_state_margins()

    def closeEvent(self, event):
        """关闭窗口的智能处理：
        - _allow_close=True（程序主动退出）→ 直接关闭
        - 悬浮球可见 → 只隐藏主窗口（保持后台运行）
        - 悬浮球不可见 → 退出程序（避免无窗口的僵尸进程）
        """
        if self._allow_close:
            event.accept()
            return
        # 检查悬浮球是否可见
        ball_visible = self._config.get("ball_visible", True)
        if ball_visible:
            # 悬浮球还在 → 只隐藏主窗口
            event.ignore()
            self.hide()
        else:
            # 悬浮球已隐藏 → 退出程序（否则用户无法再次唤起）
            event.accept()
            self._quit_app()

    def _init_ui(self):
        """构建主UI：阴影容器 + 标题栏 + 侧栏 + 内容区"""
        # 主容器：玻璃壳由 GlassPanel 手绘（半透明填充 + 顶部高光 + 双色描边 + 噪点），
        # 外圈阴影仍由本窗口 paintEvent 手绘（避免 QGraphicsDropShadowEffect 掉帧）
        self._container = GlassPanel(self, radius=self.WINDOW_RADIUS)
        self._container.setObjectName("mainWindow")

        # 用 QGridLayout 让容器跟随窗口大小，并留出阴影边距
        # （保存引用：最大化时需把边距动态改为 0）
        outer = QGridLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(
            self.SHADOW_MARGIN, self.SHADOW_MARGIN,
            self.SHADOW_MARGIN, self.SHADOW_MARGIN
        )
        outer.addWidget(self._container)
        # 注意：不使用 QGraphicsDropShadowEffect —— 它在窗口最大化/还原
        # 切换和缩放时会产生离屏缓存残影（鼠标划过按钮触发重绘时显形），
        # 且大尺寸离屏重渲染导致卡顿。阴影改为在 paintEvent 中手动绘制。

        # 主布局
        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部标题栏
        layout.addWidget(self._build_title_bar())

        # 中部：侧栏 + 内容区
        center = QHBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        center.addWidget(self._build_side_bar())
        center.addWidget(self._build_content_area(), 1)
        layout.addLayout(center, 1)

    # ==================================================================
    # 顶部标题栏
    # ==================================================================
    def _build_title_bar(self):
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(self.TITLE_BAR_HEIGHT)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 16, 0)
        h.setSpacing(8)

        # 左侧标题：FloatPulse 图标 + 文字
        # 用 QIcon 加载（比 QPixmap 更可靠），候选位置见 _find_icon_file
        icon_path = find_icon_file()
        if icon_path:
            _qicon = QIcon(icon_path)
            pm = _qicon.pixmap(22, 22)
            if not pm.isNull():
                icon_label = QLabel()
                icon_label.setPixmap(pm)
                icon_label.setFixedSize(22, 22)
                icon_label.setScaledContents(True)
                h.addWidget(icon_label)
        title = QLabel("生活悬浮球")
        title.setObjectName("titleBarLabel")
        h.addWidget(title)

        sub = QLabel("v2.0")
        sub.setObjectName("titleBarSub")
        h.addWidget(sub)
        h.addStretch()

        # 主题切换按钮
        self._theme_btn = QPushButton("🌙")
        self._theme_btn.setObjectName("iconBtn")
        self._theme_btn.setToolTip("切换深色/浅色主题")
        self._theme_btn.setFixedSize(36, 36)
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.clicked.connect(self._toggle_theme)
        h.addWidget(self._theme_btn)

        # 最小化按钮
        min_btn = QPushButton("—")
        min_btn.setObjectName("iconBtn")
        min_btn.setToolTip("最小化")
        min_btn.setFixedSize(36, 36)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.clicked.connect(self.showMinimized)
        h.addWidget(min_btn)

        # 全屏/还原切换按钮：最大化铺满屏幕 ⇄ 还原默认尺寸
        self._max_btn = QPushButton("⛶")
        self._max_btn.setObjectName("iconBtn")
        self._max_btn.setToolTip("最大化窗口")
        self._max_btn.setFixedSize(36, 36)
        self._max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._max_btn.clicked.connect(self._toggle_maximize)
        h.addWidget(self._max_btn)

        # 关闭按钮（触发 closeEvent 智能判断：悬浮球可见则隐藏，不可见则退出）
        close_btn = QPushButton("×")
        close_btn.setObjectName("iconBtn")
        close_btn.setProperty("danger", "true")
        close_btn.setToolTip("关闭窗口")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)

        return bar

    # ==================================================================
    # 左侧导航栏
    # ==================================================================
    def _build_side_bar(self):
        side = QWidget()
        side.setObjectName("sideBar")
        side.setFixedWidth(self.SIDE_BAR_WIDTH)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(4)

        # 导航按钮组（互斥）
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        # 选中指示条：贴在侧栏左边缘，随选中项平滑滑动（点击切页时移动）
        self._nav_indicator = NavIndicator(side)
        self._nav_indicator.set_color(QColor(get_colors(self._theme)["primary"]))
        self._nav_indicator.raise_()

        group_top = QLabel("WORKBENCH")
        group_top.setObjectName("sideBarTitle")
        v.addWidget(group_top)

        # 上半段导航：碎片 / 任务 / 笔记 / 知识库 / 素材
        nav_items_top = [
            ("🧩  碎片工作台", 0),
            ("📋  日程任务",  1),
            ("📝  笔记管理",  2),
            ("📚  知识库",    3),
            ("📎  临时素材",  4),
        ]
        for text, idx in nav_items_top:
            v.addWidget(self._make_nav_button(text, idx))

        # 软件导航按钮（嵌入 QStackedWidget 第8页，索引 7）
        self._app_launcher_btn = self._make_nav_button("🚀  软件导航", 7)
        v.addWidget(self._app_launcher_btn)

        # 网址导航 / 设置：紧跟软件导航，不再分下半段
        nav_items_bottom = [
            ("🌐  网址导航",  5),
            ("⚙️  设置",      6),
        ]
        for text, idx in nav_items_bottom:
            v.addWidget(self._make_nav_button(text, idx))

        # 弹性空白：把使用说明和版本号压到底部
        v.addStretch()

        # 使用说明按钮（与其它页面一致，参与页面切换，索引 8）
        v.addWidget(self._make_nav_button("❓  使用说明", 8))

        # 底部版本信息
        ver = QLabel("v2.0 · PyQt6")
        ver.setObjectName("sideBarFoot")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ver)

        return side

    def _make_nav_button(self, text: str, page_index: int) -> QPushButton:
        """创建导航按钮：**点击**切页。

        旧实现是鼠标进入按钮即切页，鼠标从侧栏横扫而过会连跳 4~5 页、
        转场动画层层叠加，观感失控。改为点击切页后，hover 只保留
        背景高亮与 2px 右移的视觉反馈。
        """
        btn = QPushButton(text)
        btn.setObjectName("navBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("pageIndex", page_index)
        btn.clicked.connect(lambda _checked=False, idx=page_index: self._switch_page(idx))
        self._nav_group.addButton(btn, page_index)
        return btn

    def _move_nav_indicator(self, btn, animate: bool = True):
        """把选中指示条移动到指定导航按钮的垂直中心"""
        indicator = getattr(self, "_nav_indicator", None)
        if indicator is None or btn is None:
            return
        parent = indicator.parentWidget()
        if parent is None:
            return
        top_left = btn.mapTo(parent, QPoint(0, 0))
        target_y = top_left.y() + (btn.height() - indicator.height()) / 2.0
        if animate:
            indicator.move_to_y(target_y)
        else:
            indicator.snap_to_y(target_y)

    # ==================================================================
    # 右侧内容区（QStackedWidget 五面板）
    # ==================================================================
    def _build_content_area(self):
        content = QWidget()
        content.setObjectName("contentArea")
        v = QVBoxLayout(content)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        self._stack = QStackedWidget()

        # 七个面板：碎片 / 任务 / 笔记 / 知识库 / 临时素材 / 网址导航 / 设置
        self._page_fragments = self._build_fragments_page()
        self._page_tasks = self._build_tasks_page()
        self._page_notes = self._build_notes_page()
        self._page_knowledge = self._build_knowledge_page()
        self._page_assets = self._build_assets_page()
        self._page_nav = self._build_nav_page()
        self._page_settings = self._build_settings_page()
        self._page_app_launcher = self._build_app_launcher_page()
        self._page_help = self._build_help_page()

        self._stack.addWidget(self._page_fragments)   # 0
        self._stack.addWidget(self._page_tasks)        # 1
        self._stack.addWidget(self._page_notes)       # 2
        self._stack.addWidget(self._page_knowledge)    # 3
        self._stack.addWidget(self._page_assets)       # 4
        self._stack.addWidget(self._page_nav)          # 5
        self._stack.addWidget(self._page_settings)     # 6
        self._stack.addWidget(self._page_app_launcher) # 7
        self._stack.addWidget(self._page_help)         # 8

        # 软件导航页面信号：启动软件后请求回到首页
        self._page_app_launcher.request_switch_to_home.connect(
            lambda: self._switch_page(0)
        )

        v.addWidget(self._stack)
        return content

    # ==================================================================
    # 五个面板（占位实现，P1-8 任务填充完整功能）
    # ==================================================================
    def _build_fragments_page(self):
        """碎片工作台面板：委托给独立的 FragmentsPanel"""
        return FragmentsPanel(self)

    # ---- 碎片工作台业务方法 ----
    def _build_tasks_page(self):
        """日程任务面板：委托给独立的 TasksPanel"""
        return TasksPanel(self)

    def _build_notes_page(self):
        """笔记管理面板：委托给独立的 NotesPanel"""
        return NotesPanel(self)

    def _build_knowledge_page(self):
        """知识库面板：委托给独立的 KnowledgePanel"""
        return KnowledgePanel(self)

    def _build_assets_page(self):
        """临时素材面板：委托给独立的 AssetsPanel"""
        return AssetsPanel(self)

    # ==================================================================
    # 网址导航面板
    # ==================================================================
    def _build_nav_page(self):
        """网址导航面板：委托给独立的 NavPanel"""
        return NavPanel(self)

    def _build_settings_page(self):
        """设置面板：委托给独立的 SettingsPanel"""
        return SettingsPanel(self)

    # ---- 软件导航页面 ----
    def _build_app_launcher_page(self):
        """构建软件导航页面（嵌入 QStackedWidget 第8页）。"""
        from src.widget_app_launcher import AppLauncherPage
        return AppLauncherPage(
            self._config, parent=self, theme=self._theme
        )

    # ---- 设置业务方法 ----
    # ==================================================================
    # 页面切换
    # ==================================================================
    def _switch_page(self, index: int):
        """切换到指定页面，并刷新对应面板数据"""
        self._stack.setCurrentIndex(index)
        # 默认选中对应的导航按钮，并让指示条滑过去
        btn = self._nav_group.button(index)
        if btn is not None:
            btn.setChecked(True)
            self._move_nav_indicator(btn)
        # 刷新对应面板
        self._refresh_page(index)

    def _refresh_page(self, index: int):
        """刷新指定页面数据（按面板名委托给 refresh_page）"""
        names = ["fragments", "tasks", "notes", "knowledge", "assets", "nav", "settings"]
        if 0 <= index < len(names):
            self.refresh_page(names[index])
        elif index == 7:
            # 软件导航页面：重新加载配置并刷新 UI
            if self._page_app_launcher:
                self._page_app_launcher.load_apps_from_config()
                self._page_app_launcher.reload_settings()

    # ==================================================================
    # 公开接口：供外部调用刷新指定面板
    # ==================================================================
    def refresh_page(self, name: str):
        """按面板名刷新对应面板（仅当该面板当前可见时刷新）。

        面板名：fragments / tasks / notes / knowledge / assets / nav / settings
        """
        index = {
            "fragments": 0, "tasks": 1, "notes": 2, "knowledge": 3,
            "assets": 4, "nav": 5, "settings": 6,
        }.get(name)
        if index is None or self._stack.currentIndex() != index:
            return
        # 所有面板已抽离为独立 panel，统一调用其 refresh()
        panel = {
            "fragments": self._page_fragments,
            "tasks": self._page_tasks,
            "notes": self._page_notes,
            "knowledge": self._page_knowledge,
            "assets": self._page_assets,
            "nav": self._page_nav,
            "settings": self._page_settings,
        }.get(name)
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()

    def refresh_fragments(self):
        """外部通知碎片数据变化时调用"""
        self.refresh_page("fragments")

    def refresh_tasks(self):
        """外部通知任务数据变化时调用"""
        self.refresh_page("tasks")

    def refresh_notes(self):
        """外部通知笔记数据变化时调用"""
        self.refresh_page("notes")

    def refresh_knowledge(self):
        """外部通知知识库变化时调用"""
        self.refresh_page("knowledge")

    def refresh_temp_assets(self):
        """外部通知临时素材变化时调用（拖文件到悬浮球后）"""
        self.refresh_page("assets")

    def refresh_nav(self):
        """外部通知网址导航变化时调用"""
        self.refresh_page("nav")

    def show_fragments_page(self):
        """打开并跳转到碎片工作台"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(0)

    def show_tasks_page(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(1)

    def show_notes_page(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(2)

    def show_knowledge_page(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(3)

    def show_assets_page(self):
        """快速跳转到临时素材面板"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(4)

    def show_nav_page(self):
        """快速跳转到网址导航面板"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(5)

    def show_settings_page(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(6)

    # ==================================================================
    # 主题系统
    # ==================================================================
    def _apply_theme(self):
        """应用当前主题的 QSS + 玻璃壳配色"""
        colors = get_colors(self._theme)
        qss = get_main_window_qss(self._theme)
        self._container.setStyleSheet(qss)
        # 玻璃壳（填充/描边/高光/噪点）由 GlassPanel 手绘，需同步配色
        if isinstance(self._container, GlassPanel):
            self._container.apply_theme(colors)
        self._sync_nav_indicator_color(colors)
        # 更新主题切换按钮图标
        self._theme_btn.setText("☀️" if self._theme == "dark" else "🌙")
        # 更新右键菜单样式
        if hasattr(self, '_menu'):
            self._menu.setStyleSheet(qss)
        # 同步更新软件导航页面的主题
        if hasattr(self, '_page_app_launcher') and self._page_app_launcher is not None:
            self._page_app_launcher._theme = self._theme
            self._page_app_launcher._apply_style()
        # 使用说明页正文颜色跟随主题
        self._apply_help_content_color()

    def _sync_nav_indicator_color(self, colors=None):
        """同步导航指示条颜色（主题切换时调用）"""
        if not hasattr(self, '_nav_indicator'):
            return
        colors = colors if colors is not None else get_colors(self._theme)
        self._nav_indicator.set_color(QColor(colors["primary"]))

    def _toggle_theme(self):
        """切换浅色/深色主题"""
        self._theme = "dark" if self._theme == "light" else "light"
        self._config.set("theme", self._theme)
        self._config.save()
        self._apply_theme()
        # 通知外部（悬浮球、小卡片）刷新主题
        self.theme_changed.emit(self._theme)

    def apply_external_theme(self, theme_name: str):
        """外部（如设置面板）切换主题时调用"""
        if theme_name not in ("light", "dark"):
            return
        if theme_name == self._theme:
            return
        self._theme = theme_name
        self._config.set("theme", self._theme)
        self._config.save()
        self._apply_theme()
        # 与 _toggle_theme 行为对齐：必须广播主题变更，
        # 否则从设置面板切主题时悬浮球、小卡片、菜单仍停留在旧主题
        self.theme_changed.emit(self._theme)

    def current_theme(self) -> str:
        return self._theme

    # ==================================================================
    # 使用说明页面（QStackedWidget 第 9 页，索引 8，F1 切换）
    # ==================================================================
    @staticmethod
    def _help_html() -> str:
        """使用说明富文本内容（与各页面功能保持同步更新）"""
        return """
        <h3>⌨️ 快捷键</h3>
        <p>• <b>Esc</b>：退出程序<br>
        • <b>Ctrl+W / Ctrl+Q</b>：隐藏主窗口<br>
        • <b>Ctrl+T</b>：切换浅色/深色主题<br>
        • <b>F1</b>：进入/退出使用说明页<br>
        • <b>Ctrl+1~7</b>：快速切换到对应面板（碎片/任务/笔记/知识库/素材/网址导航/设置）</p>

        <h3> 悬浮球</h3>
        <p>• <b>鼠标悬停</b>：自动弹出小卡片（恢复上次关闭时的模式）<br>
        • <b>鼠标离开</b>：球+卡片区域外即自动关闭卡片<br>
        • <b>左键点击</b>：切换下一张知识卡片<br>
        • <b>拖拽悬浮球</b>：移动位置；拖动期间卡片保持显示不关闭<br>
        • <b>拖到屏幕边缘</b>：自动吸边隐藏一半；鼠标移近滑出<br>
        • <b>右键悬浮球</b>：打开主窗口 / 退出程序<br>
        • <b>拖文件/图片到悬浮球</b>：自动收录到临时素材（上限10个）</p>

        <h3>🃏 小卡片</h3>
        <p>• <b>三种模式</b>：知识卡片 / 日程任务 / 临时笔记（顶部切换）<br>
        • <b>三模式统一</b>：鼠标离开球+卡片区域即自动关闭<br>
        • <b>卡片可拖动</b>：在顶部空白条或内容区空白处按住左键可拖动整个卡片<br>
        • <b>协同移动</b>：拖动卡片或悬浮球时，二者一起移动保持相对位置<br>
        • <b>拖动期间不关闭</b>：拖动卡片或悬浮球时卡片保持显示<br>
        • <b>模式记忆</b>：关闭时所处的模式会被记住，下次悬停弹出时恢复<br>
        • <b>日程任务</b>：可直接勾选完成，右键菜单编辑/删除<br>
        • <b>临时笔记</b>：输入后 800ms 自动保存，关闭也不丢数据<br>
        • <b>右键卡片</b>：退出程序</p>

        <h3>🧩 碎片工作台</h3>
        <p>• <b>自动收集</b>：复制/划词时自动收集文本碎片<br>
        • <b>按日期分组</b>：同一天的碎片归在一组<br>
        • <b>双击碎片</b>：查看完整内容<br>
        • <b>右键碎片</b>：复制 / 删除 / 存为笔记 / 加入知识库<br>
        • <b>多选 + 合并</b>：选中多条碎片后点击「合并选中」可存为笔记<br>
        • <b>筛选 + 搜索</b>：顶部可按类型筛选或关键词搜索</p>

        <h3>📋 日程任务</h3>
        <p>• 输入标题 + 截止日期后点击「添加」<br>
        • <b>红色</b>：已逾期 &nbsp; <b>橙色</b>：今日到期 &nbsp; <b>灰色</b>：已完成<br>
        • 右键任务：编辑 / 删除</p>

        <h3>📝 笔记管理</h3>
        <p>• 左侧列表显示所有笔记（标题 + 时间）<br>
        • 点击列表项 → 右侧编辑区修改 → 自动保存<br>
        • 顶部搜索框可搜索标题和内容<br>
        • 右键笔记：删除</p>

        <h3> 知识库</h3>
        <p>• 从「知识库.docx」加载段落<br>
        • 顶部「➕ 新增知识」按钮：追加到 docx 末尾<br>
        • 右键段落：编辑 / 删除 / 在此后新增 / 加入碎片池<br>
        • 搜索框：实时过滤段落</p>

        <h3>📎 临时素材</h3>
        <p>• 拖图片/文件到悬浮球 → 自动收录（上限10个，超出淘汰最旧）<br>
        • 双击：用系统默认程序打开<br>
        • 右键：打开 / 另存为 / 删除<br>
        • 「打开素材文件夹」：资源管理器打开 temp_assets/</p>

        <h3>🌐 网址导航</h3>
        <p>• <b>主窗口</b>：分组管理 / 站点增删改 / 拖拽排序<br>
        • URL 自动补全 http:// 或 https://<br>
        • 点击「打开」按钮 → 系统默认浏览器打开链接<br>
        • 碎片工作台 URL 碎片右键 → 「添加至网址导航」<br>
        • <b>小卡片 Tab 4</b>：仅展示和跳转，编辑在主窗口</p>

        <h3>⚙️ 设置</h3>
        <p>• 主题切换（浅色/深色）<br>
        • 剪贴板历史上限<br>
        • 悬浮球自动隐藏秒数<br>
        • 悬浮球显示开关（可隐藏/显示悬浮球）</p>
        """

    def _build_help_page(self):
        """构建使用说明页面（嵌入 QStackedWidget，与其它页面同层级切换）"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(12)

        # 标题
        title = QLabel("📖 生活悬浮球 - 使用说明")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        # 说明内容（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QLabel { background: transparent; }"
        )

        self._help_content = QLabel()
        self._help_content.setWordWrap(True)
        self._help_content.setTextFormat(Qt.TextFormat.RichText)
        self._help_content.setText(self._help_html())
        self._apply_help_content_color()
        scroll.setWidget(self._help_content)
        v.addWidget(scroll, 1)
        return page

    def _apply_help_content_color(self):
        """使用说明正文颜色跟随主题（切主题时由 _apply_theme 调用）"""
        if getattr(self, "_help_content", None) is None:
            return
        text_color = get_colors(self._theme).get("text", "#2C3E50")
        self._help_content.setStyleSheet(
            f"font-size: 13px; line-height: 1.6; color: {text_color}; background: transparent;"
        )

    def _toggle_help_page(self):
        """F1 切换：进入使用说明页 / 返回进入前的页面"""
        if self._stack.currentIndex() == 8:
            self._switch_page(self._page_before_help)
        else:
            self._page_before_help = self._stack.currentIndex()
            self._switch_page(8)

    # ==================================================================
    # 无边框窗口拖动 + 边缘自由缩放
    # ==================================================================
    RESIZE_EDGE = 18  # 边缘缩放感应宽度（与阴影边距一致，透明边缘区可拖拽缩放）
    CORNER_SIZE = 24  # 四角对角线缩感应区（稍大于边缘，提升四角命中率）

    _CURSOR_MAP = {
        "left":         Qt.CursorShape.SizeHorCursor,
        "right":        Qt.CursorShape.SizeHorCursor,
        "top":          Qt.CursorShape.SizeVerCursor,
        "bottom":       Qt.CursorShape.SizeVerCursor,
        "top-left":     Qt.CursorShape.SizeFDiagCursor,
        "bottom-right": Qt.CursorShape.SizeFDiagCursor,
        "top-right":    Qt.CursorShape.SizeBDiagCursor,
        "bottom-left":  Qt.CursorShape.SizeBDiagCursor,
    }

    # ==================================================================
    # 子控件指针残留防护：遍历安装事件过滤器
    # ==================================================================
    def _install_cursor_filter(self):
        """为所有子控件递归安装事件过滤器，鼠标进入子控件时主动清除主窗口缩放指针。
        这是解决"缩放指针残留在按钮/列表等内容区控件上卡住不消失"的核心方案。"""
        def _recursive_install(widget: QWidget):
            widget.installEventFilter(self)
            widget.setMouseTracking(True)  # 子控件也开启鼠标跟踪，指针切换更实时
            for child in widget.findChildren(QWidget):
                _recursive_install(child)
        _recursive_install(self)

    def eventFilter(self, obj, event):
        """事件过滤器：鼠标进入任意子控件 → 清除主窗口的缩放指针残留"""
        etype = event.type()
        # 鼠标进入子控件 / 子控件内部移动 时，都清理一次主窗口指针
        if etype == QEvent.Type.Enter:
            # 只清理缩放指针残留；页面切换改由导航按钮的 clicked 信号驱动
            # （原先鼠标进入即切页，横扫侧栏会连跳多页）
            self._clear_resize_cursor()
        elif etype == QEvent.Type.MouseMove and obj is not self:
            # 子控件内部鼠标移动时，若主窗口还有缩放指针则清理
            if self.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
                self._clear_resize_cursor()
        return super().eventFilter(obj, event)

    def _clear_resize_cursor(self):
        """安全清除主窗口上的缩放指针（带状态校验，避免重复调用）"""
        if self.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
            # 非缩放拖动中才清除，避免缩放拖动过程中指针被误还原导致闪烁
            if not self._resizing:
                self.unsetCursor()

    # ==================================================================
    # 边缘缩放命中检测（改进版：更精准的判定 + 最大化快速短路）
    # ==================================================================
    def _edge_hit(self, pos) -> str:
        """判断位置是否落在窗口边缘缩放区。返回方向字符串，非边缘返回空串。

        精度改进：
        1. 最大化直接短路返回（之前已有，此处保留）
        2. 四角使用 CORNER_SIZE 稍大的命中区，提升对角线缩放命中率
        3. 先判断四角再判断四边，避免靠近四角时被四边先匹配导致方向错误
        4. 使用局部坐标与窗口尺寸严格比较，排除浮点数误差
        """
        if self.isMaximized():
            return ""  # 最大化时不允许边缘缩放
        e = self.RESIZE_EDGE
        c = self.CORNER_SIZE
        x, y = int(pos.x()), int(pos.y())
        w, h = self.width(), self.height()
        # 先判四角（命中区稍大，优先于四边）
        in_left = x <= c
        in_right = x >= w - c
        in_top = y <= c
        in_bottom = y >= h - c
        if in_top and in_left:
            return "top-left"
        if in_top and in_right:
            return "top-right"
        if in_bottom and in_left:
            return "bottom-left"
        if in_bottom and in_right:
            return "bottom-right"
        # 再判四边（标准宽度 e）
        if x <= e:
            return "left"
        if x >= w - e:
            return "right"
        if y <= e:
            return "top"
        if y >= h - e:
            return "bottom"
        return ""

    def _update_resize_cursor(self, pos):
        """悬停时根据边缘方向切换鼠标指针形状。

        改进：
        1. 进入非边缘区前先判断光标是否真正被主窗口设置过（WA_SetCursor）
        2. 子控件命中检测：若当前位置下方有子控件（非主窗口本身），直接清除缩放指针
        3. 保留 shape 去重逻辑，避免重复 setCursor 造成的 Windows 下指针抖动
        """
        # 若该位置下是子控件（通过 childAt 查），不显示缩放指针，直接清理并返回
        child = self.childAt(pos.toPoint())
        if child is not None and child is not self._container and child is not self:
            self._clear_resize_cursor()
            return
        edge = self._edge_hit(pos)
        if edge:
            shape = self._CURSOR_MAP[edge]
            # WA_SetCursor=True 表示主窗口设置过指针；否则 cursor() 返回的是默认继承值
            if not self.testAttribute(Qt.WidgetAttribute.WA_SetCursor) or self.cursor().shape() != shape:
                self.setCursor(shape)
        else:
            self._clear_resize_cursor()

    def _do_resize(self, global_pos):
        """按住边缘拖动：按全局位移计算新窗口几何（受最小尺寸约束）"""
        dx = global_pos.x() - self._resize_start_global.x()
        dy = global_pos.y() - self._resize_start_global.y()
        start = self._resize_start_geom
        x, y = start.x(), start.y()
        w, h = start.width(), start.height()
        edge = self._resizing
        if "right" in edge:
            w = max(self.MIN_WIDTH, w + dx)
        if "bottom" in edge:
            h = max(self.MIN_HEIGHT, h + dy)
        if "left" in edge:
            w = max(self.MIN_WIDTH, w - dx)
            x = start.right() + 1 - w  # 锁定右边界
        if "top" in edge:
            h = max(self.MIN_HEIGHT, h - dy)
            y = start.bottom() + 1 - h  # 锁定下边界
        self.setGeometry(x, y, w, h)

    def mousePressEvent(self, event):
        """左键按下：优先边缘缩放，其次标题栏拖动。按下时先清理一次残留指针。"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 开始交互前主动清一次指针：防止上次交互的指针状态未还原
            if not self._resizing:
                self._clear_resize_cursor()
            # 1. 边缘缩放区 → 开始自由缩放
            edge = self._edge_hit(event.position())
            if edge:
                self._resizing = edge
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_geom = self.geometry()
                # 缩放开始时强制设置一次指针，避免瞬时切到错误形状
                self.setCursor(self._CURSOR_MAP[edge])
                event.accept()
                return
            # 2. 标题栏区域 = 阴影边距 ~ 阴影边距 + 标题栏高度
            #    （最大化时边距为 0，动态计算避免内容区顶部误判为拖动）
            margin = 0 if self.isMaximized() else self.SHADOW_MARGIN
            y = event.position().y()
            if y <= self.TITLE_BAR_HEIGHT + margin:
                # 拖动窗口开始时：确保没有缩放指针残留
                self._clear_resize_cursor()
                if self.isMaximized():
                    # 最大化状态下按下标题栏 → 不立即还原窗口！
                    # 仅记录"待还原"状态，等 mouseMoveEvent 检测到真正拖动
                    # 才还原。这样双击（按下+双击事件）不会触发
                    # "还原→又最大化"的来回闪动（修复双击标题栏抖动卡死）
                    self._drag_pending_restore = True
                    self._drag_press_ratio = (event.position().x()
                                              / max(1, self.width()))
                self._dragging = True
                self._drag_offset = (event.globalPosition().toPoint()
                                     - self.frameGeometry().topLeft())
                event.accept()
            else:
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击标题栏：全屏/还原切换（边缘区不触发）"""
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._edge_hit(event.position()):
                margin = 0 if self.isMaximized() else self.SHADOW_MARGIN
                y = event.position().y()
                if y <= self.TITLE_BAR_HEIGHT + margin:
                    self._toggle_maximize()
                    # 切换最大化后立即清理指针（最大化没有边缘缩放，防止残留）
                    QTimer.singleShot(0, self._clear_resize_cursor)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        """拖动中：移动窗口 / 缩放窗口；悬停时：切换边缘指针。

        额外防护：鼠标未按住且有子控件时，强制清除主窗口上的缩放指针，
        防止从边缘快速滑入内容区时的指针残影。
        """
        if self._resizing and (event.buttons() & Qt.MouseButton.LeftButton):
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
        elif self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            # 惰性还原：最大化窗口被真正拖动时才还原尺寸位置
            # （按下不动 / 双击时不还原，避免窗口大小来回跳变）
            if self._drag_pending_restore:
                self._drag_pending_restore = False
                self.showNormal()
                # 先恢复记忆几何（或默认尺寸），再把窗口挂到鼠标下方
                self._restore_normal_geometry()
                gx = event.globalPosition().x()
                gy = event.globalPosition().y()
                self.move(int(gx - self.width() * self._drag_press_ratio),
                          int(gy - self.TITLE_BAR_HEIGHT / 2))
                # 窗口跳转后重算拖动偏移，保证继续拖动跟手
                self._drag_offset = (event.globalPosition().toPoint()
                                     - self.frameGeometry().topLeft())
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            # 非拖动状态：更新指针，且若下方是子控件则额外清一次（双保险）
            self._update_resize_cursor(event.position())
            child = self.childAt(event.position().toPoint())
            if child is not None and child is not self._container and child is not self:
                self._clear_resize_cursor()
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """释放鼠标：结束拖动/缩放，立即清理指针。

        无论事件在哪个子控件上方，释放后都必须确保缩放指针被清除，
        这是解决"松开鼠标后指针还保持缩放形状"的关键一步。
        """
        released_resizing = bool(self._resizing)
        released_dragging = bool(self._dragging)
        if self._resizing:
            self._resizing = None
            event.accept()
        elif self._dragging:
            self._dragging = False
            # 未发生真正拖动就松开（如单击/双击标题栏）→ 取消待还原标记
            self._drag_pending_restore = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)
            return
        # 释放后清理指针：优先根据当前位置判断，若释放点在子控件上则无条件清
        if released_resizing or released_dragging:
            child = self.childAt(event.position().toPoint())
            if child is not None and child is not self._container and child is not self:
                self._clear_resize_cursor()
            else:
                self._update_resize_cursor(event.position())
            # 兜底：延迟 10ms 再清一次，处理 OS 级指针缓存导致的偶发残留
            QTimer.singleShot(10, self._clear_resize_cursor)

    def leaveEvent(self, event):
        """鼠标离开窗口：恢复默认指针。

        与原实现的区别：
        1. 调用统一的 _clear_resize_cursor() 而非直接 unsetCursor，
           确保不会在缩放拖动中途误清除导致指针抖动。
        2. 离开时若仍然 WA_SetCursor（极少数情况），再做一次强制 unset。
        """
        self._clear_resize_cursor()
        # 强制兜底：离开窗口后必须无自定义指针
        if self.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
            # 仅当不在缩放拖动中才强制清除
            if not self._resizing:
                self.unsetCursor()
        super().leaveEvent(event)

    def enterEvent(self, event):
        """鼠标重新进入窗口：若有残留指针立即清除，根据当前位置重建正确形状。"""
        self._clear_resize_cursor()
        super().enterEvent(event)


