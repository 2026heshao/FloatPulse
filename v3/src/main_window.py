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

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QButtonGroup,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QTextEdit,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QMessageBox, QMenu, QSplitter, QApplication,
    QScrollArea, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QInputDialog, QSlider,
    QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
    QSpacerItem, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QPoint, pyqtSignal, QDate, QTimer, QRect, QRectF, QSize, QEvent,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import QColor, QPainter, QAction, QIcon, QPixmap, QShortcut, QKeySequence

from src.theme import get_main_window_qss, get_colors
from src.constants import DEFAULT_THEME
from src.config import sanitize_nav_order, DEFAULT_NAV_ORDER
from src.glass import GlassPanel, NavIndicator
from src.controls import ScreenToast
from src.app_paths import find_icon_file, get_screen_geometry
from src.fragments_panel import FragmentsPanel
from src.tasks_panel import TasksPanel
from src.notes_panel import NotesPanel
from src.knowledge_panel import KnowledgePanel
from src.assets_panel import AssetsPanel
from src.nav_panel import NavPanel
from src.settings_panel import SettingsPanel


# ====================================================================
# 左栏导航：功能页 key → QStackedWidget 固定物理索引
# ====================================================================
# **核心设计约束**：物理索引与功能的映射永不改变（0-6 功能面板按
# QStackedWidget 创建顺序、6 设置、7 软件导航、8 使用说明）。所有
# `_switch_page(数字)` 调用点的语义 = 物理索引 = 功能，全部保持不动；
# 拖动换位改变的**只有左栏按钮的显示顺序**（self._nav_order）。
NAV_PAGE_INDEX = {
    "fragments": 0,    # 碎片工作台
    "tasks": 1,        # 日程任务
    "notes": 2,        # 笔记管理
    "knowledge": 3,    # 知识库
    "assets": 4,       # 临时素材
    "nav": 5,          # 网址导航
    "apps": 7,         # 软件导航
}
# 各功能页按钮文案（key 固定，文案可随 UI 调整）
NAV_PAGE_TITLES = {
    "fragments": "🧩  碎片工作台",
    "tasks": "📋  日程任务",
    "notes": "📝  笔记管理",
    "knowledge": "📚  知识库",
    "assets": "📎  临时素材",
    "apps": "🚀  软件导航",
    "nav": "🌐  网址导航",
}
# 拖拽换位：位移超过该值（像素）才进入拖拽，否则视为普通点击切页
NAV_DRAG_THRESHOLD = 8
# 左栏实时让位：邻居滑开让位动画时长 / 落定滑入动画时长（均随动画速度档位缩放）
NAV_SHIFT_MS = 150
NAV_DROP_MS = 180


class _NavButton(QPushButton):
    """左栏功能页导航按钮：普通点击切页 + 纵向拖动换位。

    - press 记录起点 → move 位移超过 ``NAV_DRAG_THRESHOLD`` 才进入拖拽，
      未超阈值的 press-release 仍是一次正常的页面切换点击（不破坏现有点击）；
    - 进入拖拽后通过信号把轨迹交给 MainWindow 处理（预览插入位置 / 落定），
      release 不再触发 click（避免拖完又切页）。
    """

    drag_started = pyqtSignal(object)            # self
    drag_moved = pyqtSignal(object, QPoint)      # self, 全局坐标
    drag_finished = pyqtSignal(object, QPoint)   # self, 全局坐标

    def __init__(self, text: str, nav_key, parent=None):
        super().__init__(text, parent)
        self.nav_key = nav_key        # 功能页 key（设置/说明按钮为 None → 不可拖）
        self._press_global = None
        self._is_dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.nav_key:
            self._press_global = event.globalPosition().toPoint()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global is not None and not self._is_dragging:
            delta = event.globalPosition().toPoint() - self._press_global
            if (abs(delta.y()) > NAV_DRAG_THRESHOLD
                    or abs(delta.x()) > NAV_DRAG_THRESHOLD):
                self._is_dragging = True
                self.drag_started.emit(self)
        if self._is_dragging:
            self.drag_moved.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_dragging and event.button() == Qt.MouseButton.LeftButton:
            # 拖拽落定：不调用 super → 不触发 clicked（拖完不切页）
            self._is_dragging = False
            self._press_global = None
            self.setDown(False)
            self.drag_finished.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        self._press_global = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)


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
    ball_size_changed = pyqtSignal(int)          # 悬浮球球体直径变更
    hide_on_fullscreen_changed = pyqtSignal(bool)  # 全屏应用自动隐藏开关变更
    quick_capture_changed = pyqtSignal()         # 快速捕捉设置（开关/热键）变更

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
        self._search_dialog = None   # 全库搜索对话框（懒创建）

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

        # 左栏拖拽换位状态：
        # - 光标采用「配对防护」：``_nav_drag_cursor_active`` 为 True 才允许
        #   restore，防止 restoreOverrideCursor 弹错栈 / release 事件丢失
        #   导致光标永久卡在"抓手"；
        # - 落定动画持有引用防 GC；中断旧动画防止新一轮拖拽叠加错乱。
        self._nav_drag_cursor_active = False   # 抓手 override 光标是否由我们压入
        self._nav_drag_btn = None              # 当前被拖按钮（None = 非拖拽中）
        self._nav_drag_opacity_effect = None   # 遗留字段（现用 QSS dragging 属性 + 投影）
        self._nav_settle_animations = []       # 换位落定滑动动画（保持引用）
        self._drag_indicator_anim = None       # 遗留字段（插入指示条已废弃）
        # 实时让位（拖动中邻居立即滑开）相关状态：
        self._nav_free_layout = False          # True = 按钮已脱离布局自由定位
        self._nav_free_spacer = None           # 自由布局期间顶住垂直空间的占位项
        self._nav_drag_order = []              # 拖拽中的显示顺序（拖拽前 = _nav_order）
        self._nav_slot_ys = []                 # 拖拽冻结的各槽位 Y（侧栏坐标）
        self._nav_slot_h = 0                   # 拖拽冻结的按钮行高
        self._nav_shift_anims = {}             # 邻居让位动画：btn -> QPropertyAnimation
        self._nav_drop_anim = None             # 落定滑入动画（拖起又放回时也用它）
        self._nav_drag_grab_dy = 0             # 光标在按钮内的纵向偏移（以按下点为准）

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
        if self._clipboard_monitor is not None:
            trimmed_sig = getattr(self._clipboard_monitor, "fragments_trimmed", None)
            if trimmed_sig is not None:
                trimmed_sig.connect(self._on_fragments_trimmed)
        # 启动页面：按设置恢复上次浏览的页面，或默认首页（碎片工作台）
        self._switch_page(self._initial_page_index())

    # ==================================================================
    # 轻提示条（Toast）
    # ==================================================================
    def show_toast(self, text: str, ms: int = 2800):
        """操作反馈提示：屏幕顶部居中的独立顶层浮窗。

        2026-09-24 改造：此前是主窗口内的子控件（底部状态条），主窗口
        最小化/收进托盘时提示就看不见了。统一改为 ScreenToast —— 屏幕
        级顶层窗口，无论窗口状态如何都直接显示在屏幕最上方。
        """
        ScreenToast.show_msg(text, self.current_theme, ms)

    def _on_fragments_trimmed(self, count: int):
        """碎片池超限自动淘汰时通报用户（此前是静默删除，用户不知道数据少了）"""
        self.show_toast(f"🧩 碎片池已达上限，自动清理了 {count} 条最早的碎片")

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

        # Ctrl+K: 全库统一搜索（碎片/笔记/任务/素材聚合）
        search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        search_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        search_shortcut.activated.connect(self._open_global_search)

        # Ctrl+1~7: 切到左栏显示顺序第 N 个功能页（快捷键跟随位置：
        # 拖动换位后 Ctrl+N 指向新排到第 N 位的那个功能页）
        for i in range(7):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i+1}"), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda n=i: self._switch_to_nav_slot(n))

    def _open_global_search(self):
        """打开全库统一搜索对话框（懒创建，复用实例，样式跟随当前主题）"""
        if self._search_dialog is None:
            from src.global_search_dialog import GlobalSearchDialog
            self._search_dialog = GlobalSearchDialog(
                self._fragment_manager, self._note_manager,
                self._task_manager, self._temp_asset_manager,
                parent=self, host=self)
            self._search_dialog.jump_requested.connect(self._on_search_jump)
        # 主题可能在两次打开之间被切换 → 重新套一遍主窗口 QSS 与玻璃壳配色
        self._search_dialog.apply_theme()
        self._search_dialog.open_and_focus()

    def _on_search_jump(self, page_idx: int, keyword: str):
        """搜索结果跳转：切页并尽量把关键词带入该面板的搜索框"""
        self._switch_page(page_idx)
        try:
            if page_idx == 0 and hasattr(self._page_fragments, "_frag_search"):
                # 走专用入口：立即过滤，不等 250ms 搜索去抖
                if hasattr(self._page_fragments, "apply_external_keyword"):
                    self._page_fragments.apply_external_keyword(keyword)
                else:
                    self._page_fragments._frag_search.setText(keyword)
            elif page_idx == 2 and hasattr(self._page_notes, "_note_search"):
                self._page_notes._note_search.setText(keyword)
        except Exception:
            pass

    def _quit_app(self):
        """退出整个程序：重置页面为首页，然后退出"""
        # 落盘未决的笔记编辑（自动保存防抖窗口内可能仍有待写数据）
        try:
            panel = getattr(self, '_page_notes', None)
            if panel is not None:
                panel.flush_pending_save()
        except Exception:
            pass
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

    def event(self, event):
        """事件兜底：左栏拖拽换位期间窗口失活/隐藏 → 强制安全退出拖拽态。

        正常路径由 ``_on_nav_drag_finished`` 收尾；但拖拽中若发生
        Alt+Tab / 系统快捷键切窗 / 窗口被收起等，release 事件可能丢失，
        override 光标会永久卡在"抓手"。这里在任何异常信号出现时强制
        复位（幂等，与正常收尾路径互不冲突）。
        """
        et = event.type()
        if et in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            if (self._nav_drag_cursor_active or self._nav_drag_btn is not None
                    or self._nav_free_spacer is not None):
                self._force_end_nav_drag()
        return super().event(event)

    def changeEvent(self, event):
        """窗口状态变化（最大化/还原，含任务栏/系统快捷键触发）时同步 UI"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_max_btn()
            self._apply_window_state_margins()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 左栏拖拽期间窗口尺寸变化 → 槽位冻结值失效，直接回滚退出拖拽
        if self._nav_free_spacer is not None:
            self._force_end_nav_drag()

    def closeEvent(self, event):
        """关闭窗口的智能处理：
        - _allow_close=True（程序主动退出）→ 直接关闭
        - close_to_tray=True（默认）→ 最小化到托盘（托盘左键/右键可随时唤回）
        - close_to_tray=False → 沿用旧规则：
            悬浮球可见 → 只隐藏主窗口（保持后台运行）
            悬浮球不可见 → 退出程序（避免无窗口的僵尸进程）
        """
        if self._allow_close:
            event.accept()
            return
        if self._config.get("close_to_tray", True):
            # 托盘图标常驻，始终有唤回入口 → 隐藏到托盘
            event.ignore()
            self._hide_aux_windows()
            self.hide()
            return
        # 兼容旧行为：按悬浮球可见性决定隐藏还是退出
        ball_visible = self._config.get("ball_visible", True)
        if ball_visible:
            # 悬浮球还在 → 只隐藏主窗口
            event.ignore()
            self._hide_aux_windows()
            self.hide()
        else:
            # 悬浮球已隐藏 → 退出程序（否则用户无法再次唤起）
            event.accept()
            self._quit_app()

    def _hide_aux_windows(self):
        """主窗口收进托盘时一并收起附属顶层窗口（目前只有 Ctrl+K 全局搜索框）"""
        dlg = getattr(self, "_search_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.hide()

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

        # 导航按钮组（互斥）：addButton 的 id = 物理索引（与功能绑定不变）
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        # 选中指示条：贴在侧栏左边缘，随选中项平滑滑动（点击切页时移动）
        self._nav_indicator = NavIndicator(side)
        self._nav_indicator.set_color(QColor(get_colors(self._theme)["primary"]))
        self._nav_indicator.raise_()

        group_top = QLabel("WORKBENCH")
        group_top.setObjectName("sideBarTitle")
        v.addWidget(group_top)

        # 按显示顺序创建 7 个功能页按钮（拖动换位只改这里的顺序，
        # 每个按钮绑定的物理索引不变）。布局槽位约定：group_top 占 0、
        # 功能页占 1..7，设置/说明/版本号固定在功能页之后。
        self._nav_btns_layout = v
        self._nav_area = side
        self._nav_btns = {}
        self._nav_order = self._load_nav_order()
        for key in self._nav_order:
            btn = self._make_nav_button(NAV_PAGE_TITLES[key],
                                        NAV_PAGE_INDEX[key], key)
            self._nav_btns[key] = btn
            v.addWidget(btn)

        # 设置（物理索引 6）：固定，不参与拖动换位
        self._settings_btn = self._make_nav_button("⚙️  设置", 6)
        v.addWidget(self._settings_btn)
        # 软件导航按钮别名（历史引用点保留）
        self._app_launcher_btn = self._nav_btns.get("apps")

        # 弹性空白：把使用说明和版本号压到底部
        v.addStretch()

        # 使用说明按钮（与其它页面一致，参与页面切换，索引 8）
        v.addWidget(self._make_nav_button("❓  使用说明", 8))

        # 底部版本信息
        ver = QLabel("v2.0 · PyQt6")
        ver.setObjectName("sideBarFoot")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ver)

        # 拖拽插入位置预览条：**已废弃**（实时让位取代），保留对象仅为兼容
        # 既有验证脚本对 `_drag_indicator` 的存在性/隐藏态断言，永不显示。
        self._drag_indicator = QWidget(side)
        self._drag_indicator.setObjectName("navDragIndicatorLegacy")
        self._drag_indicator.hide()

        return side

    def _make_nav_button(self, text: str, page_index: int,
                         nav_key=None) -> QPushButton:
        """创建导航按钮：**点击**切页；功能页按钮（nav_key 非空）可拖动换位。

        旧实现是鼠标进入按钮即切页，鼠标从侧栏横扫而过会连跳 4~5 页、
        转场动画层层叠加，观感失控。改为点击切页后，hover 只保留
        背景高亮与 2px 右移的视觉反馈。
        """
        btn = _NavButton(text, nav_key)
        btn.setObjectName("navBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("pageIndex", page_index)
        btn.clicked.connect(lambda _checked=False, idx=page_index: self._switch_page(idx))
        # id 用固定物理索引 → _switch_page 高亮 / last_page_index 全部自然正确
        self._nav_group.addButton(btn, page_index)
        if nav_key:
            btn.drag_started.connect(self._on_nav_drag_started)
            btn.drag_moved.connect(self._on_nav_drag_moved)
            btn.drag_finished.connect(self._on_nav_drag_finished)
        return btn

    # ---------------- nav_order 读取与校验 ----------------
    def _load_nav_order(self) -> list:
        """读取左栏显示顺序：非法（非 list/长度≠7/非 key 排列）→ 回退默认。"""
        order = sanitize_nav_order(self._config.get("nav_order", []))
        if order is None:
            return list(DEFAULT_NAV_ORDER)
        return order

    def _apply_nav_order(self, order: list, save: bool = False):
        """把显示顺序落到位：重排按钮布局 + 同步指示器与选中态 + 落定滑动动画。

        只挪按钮，物理索引不动；save=True 时写盘持久化。
        重排前后记录各按钮几何，位置变化的按钮用 QPropertyAnimation
        （OutCubic，时长随全局动画速度档位缩放）从旧位置滑到新位置，
        而不是瞬间跳位。新一轮重排会先中断上一轮动画，防止叠加错乱。
        """
        v = self._nav_btns_layout
        # 中断上一轮落定动画（动画中用户再次换位 → 直接跳到终态再重排）
        self._abort_nav_settle_animations()
        # 记录重排前各按钮位置（父级坐标）
        old_pos = {key: btn.pos() for key, btn in self._nav_btns.items()}
        # 先把 7 个功能页按钮全部移出布局，再按新顺序插回槽位 1..7
        for btn in self._nav_btns.values():
            v.removeWidget(btn)
        for i, key in enumerate(order):
            v.insertWidget(1 + i, self._nav_btns[key])
        # 强制布局立即生效：Qt 布局是惰性应用的，不 activate 的话下面
        # 读取的 btn.pos() 仍是旧几何，落定动画的起止值会算错
        v.activate()
        self._nav_order = list(order)
        self._sync_nav_selection()
        if save:
            self._save_nav_order(order)
        self._start_nav_settle_animations(old_pos)

    def _save_nav_order(self, order: list):
        """把左栏显示顺序写入 config 并落盘（失败静默，不阻断 UI）"""
        try:
            self._config.set("nav_order", list(order))
            self._config.save()
        except Exception:
            pass

    def _nav_anim_ms(self, base_ms: float) -> int:
        """按全局动画速度档位缩放导航动画时长（档位越大越快 → 时长越短）。

        与任务面板 ``CHECK_ANIM_MS / anim_speed`` 的缩放口径保持一致。
        """
        speed = self.anim_speed
        if speed <= 0:
            speed = 1.0
        return max(0, int(round(base_ms / speed)))

    def _start_nav_settle_animations(self, old_pos: dict):
        """落定动画：位置变化的按钮从旧位置平滑滑到新位置。"""
        duration = self._nav_anim_ms(220)
        if duration <= 0:
            return
        anims = []
        for key, btn in self._nav_btns.items():
            old = old_pos.get(key)
            new = btn.pos()
            if old is None or old == new:
                continue   # 位置没变的按钮不做动画
            anim = QPropertyAnimation(btn, b"pos", self)
            anim.setDuration(duration)
            anim.setStartValue(old)
            anim.setEndValue(new)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(self._on_nav_settle_anim_done)
            anims.append(anim)
        if not anims:
            return
        self._nav_settle_animations = anims
        for anim in anims:
            anim.start()

    def _on_nav_settle_anim_done(self):
        """单个落定动画结束 → 从引用列表移除（允许 Python 侧回收）。"""
        anim = self.sender()
        if anim in self._nav_settle_animations:
            self._nav_settle_animations.remove(anim)
        anim.deleteLater()

    def _abort_nav_settle_animations(self):
        """中断所有进行中的落定动画（按钮直接停在当前位置/终态）。"""
        for anim in list(self._nav_settle_animations):
            try:
                anim.stop()   # stop 会触发 finished → 自动移出列表
            except RuntimeError:
                pass   # 底层 C++ 对象已被回收，忽略
        self._nav_settle_animations = []

    def _sync_nav_selection(self):
        """重排后同步选中态与指示条（当前显示页不变，指示条跟到新位置）"""
        btn = self._nav_group.button(self._stack.currentIndex())
        if btn is not None:
            btn.setChecked(True)
            self._move_nav_indicator(btn, animate=False)

    def _switch_to_nav_slot(self, n: int):
        """Ctrl+N：切到左栏显示顺序第 N 个功能页（快捷键跟随位置）。"""
        try:
            key = self._nav_order[n]
        except (IndexError, TypeError, AttributeError):
            return
        self._switch_page(NAV_PAGE_INDEX[key])

    # ---------------- 拖拽换位 ----------------
    def _acquire_nav_drag_cursor(self):
        """压入"抓手" override 光标（配对防护）。

        setOverrideCursor/restoreOverrideCursor 是压栈/弹栈模型：
        若上一次拖拽异常退出（release 丢失）导致残留未还原，这里先
        restore 一次再压入，保证光标栈不错位；压入后置位自己的标志。
        """
        if self._nav_drag_cursor_active:
            QApplication.restoreOverrideCursor()
        QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
        self._nav_drag_cursor_active = True

    def _release_nav_drag_cursor(self):
        """安全还原"抓手" override 光标：只有自己压过才 restore，然后清标志。"""
        if self._nav_drag_cursor_active:
            QApplication.restoreOverrideCursor()
            self._nav_drag_cursor_active = False

    def _apply_nav_drag_lift(self, btn):
        """被拖按钮"提起"视觉：QSS dragging 属性（主色底 + 描边）。

        ⚠ 这里**不能**用 QGraphicsDropShadowEffect：effect 会参与
        sizeHint 计算且被布局缓存，去掉后布局仍按"带投影"的高度排布
        → 每轮拖拽后按钮行高 +1px（2026-09-24 实测踩坑）。
        侧栏是布局驱动，投影交给 QSS 表达即可。
        """
        self._clear_nav_drag_lift()
        if btn is None:
            return
        btn.setProperty("dragging", True)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _clear_nav_drag_lift(self):
        """清除被拖按钮的"提起"视觉（属性 + 失效布局缓存）。

        ⚠ 顺序很关键：必须**先** ``setDown(False)`` 再重刷样式。
        全局 QSS 有 ``QPushButton:pressed { margin-top: 1px; }``，按下态下
        ``unpolish/polish`` 会把 ``sizeHint`` 算成"多 1px"（35 → 36）并缓存；
        而 Qt 在 ``setDown(False)`` 时**不会**失效这个缓存（只 ``update``，
        不 ``updateGeometry``）→ 被拖过的按钮行高**永久** +1px，整列自上而下
        错位（2026-09-24 用离屏探针逐步二分锁定）。先复位按下态再 polish，
        重算出的 sizeHint 才是正确的行高。
        """
        btn = self._nav_drag_btn
        if btn is None:
            return
        try:
            btn.setDown(False)          # ← 必须最先做（见上）
            btn.setProperty("dragging", False)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            # 属性切换可能改到 sizeHint，主动失效避免布局吃缓存
            btn.updateGeometry()
        except RuntimeError:
            pass
        self._nav_drag_opacity_effect = None

    # ---------------- 实时让位：拖拽期间按钮脱离布局自由定位 ----------------
    def _freeze_nav_buttons(self) -> bool:
        """把 7 个功能页按钮从布局中摘出、按当前几何自由定位。

        原位插一个等高 spacer 顶住垂直空间，设置/说明/版本号不会被顶上移；
        按钮几何保持不变，随后由拖拽逻辑直接改 y 实现"空档跟着鼠标走"。
        """
        v = self._nav_btns_layout
        keys = list(self._nav_order)
        btns = [self._nav_btns[k] for k in keys]
        if not btns:
            return False
        geos = [QRect(b.geometry()) for b in btns]
        total = (geos[-1].y() + geos[-1].height()) - geos[0].y()
        for b in btns:
            v.removeWidget(b)
        spacer = QSpacerItem(0, total, QSizePolicy.Policy.Fixed,
                             QSizePolicy.Policy.Fixed)
        v.insertSpacerItem(1, spacer)
        self._nav_free_spacer = spacer
        self._nav_free_layout = True
        v.activate()
        for b, g in zip(btns, geos):
            b.setGeometry(g)
            b.show()
        self._nav_slot_ys = [g.y() for g in geos]
        self._nav_slot_h = geos[0].height()
        self._nav_drag_order = list(self._nav_order)
        return True

    def _restore_nav_layout(self):
        """把按钮按 ``_nav_order`` 重新插回布局，并移除占位 spacer（幂等）。

        以 ``_nav_free_layout`` 为唯一状态真相：即使 spacer 已被提前移除
        （落定滑入路径会先移除它），这里也必须把按钮插回布局，否则设置/
        说明/版本号会被布局顶到上方。
        """
        if not self._nav_free_layout:
            return
        v = self._nav_btns_layout
        if self._nav_free_spacer is not None:
            v.removeItem(self._nav_free_spacer)
            self._nav_free_spacer = None
        for i, key in enumerate(self._nav_order):
            v.insertWidget(1 + i, self._nav_btns[key])
        self._nav_free_layout = False
        v.activate()
        # 拖拽态样式会让 QPushButton 的 sizeHint 内部缓存 +1px（Qt 私有缓存，
        # polish/updateGeometry 不一定失效）→ 交还布局后按冻结的槽位几何钉死，
        # 保证侧栏精确回到拖拽前的排版
        for i, key in enumerate(self._nav_order):
            b = self._nav_btns[key]
            b.setGeometry(b.x(), self._nav_slot_y(i), b.width(), self._nav_slot_h)
        v.invalidate()

    def _nav_slot_y(self, index: int) -> int:
        """槽位 Y（拖拽冻结值）"""
        ys = self._nav_slot_ys
        if not ys:
            return 0
        return ys[max(0, min(index, len(ys) - 1))]

    def _nav_slot_step(self) -> int:
        """相邻槽位间距"""
        ys = self._nav_slot_ys
        if len(ys) >= 2:
            return max(1, ys[1] - ys[0])
        btn = next(iter(self._nav_btns.values()), None)
        return max(1, btn.height() if btn is not None else 1)

    def _reorder_nav_live(self, btn, target_idx: int):
        """越过邻居中点 → 邻居立即滑开让位（空档跟着鼠标走）。

        只改 ``_nav_drag_order``（拖拽中的顺序），拖动中不落盘；
        数据落盘统一在松手时进行。
        """
        key = btn.nav_key
        order = self._nav_drag_order
        cur = order.index(key)
        if cur == target_idx:
            return
        order.pop(cur)
        order.insert(target_idx, key)
        duration = self._nav_anim_ms(NAV_SHIFT_MS)
        for i, k in enumerate(order):
            b = self._nav_btns[k]
            if b is btn:
                continue
            target_y = self._nav_slot_y(i)
            if b.y() == target_y:
                continue
            if duration <= 0:
                b.move(b.x(), target_y)
                continue
            anim = self._nav_shift_anims.get(b)
            if anim is None:
                anim = QPropertyAnimation(b, b"pos", b)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._nav_shift_anims[b] = anim
            anim.stop()
            anim.setDuration(duration)
            anim.setStartValue(QPoint(b.x(), b.y()))
            anim.setEndValue(QPoint(b.x(), target_y))
            anim.start()

    def _settle_nav_shift_anims(self, keep=None):
        """中断让位动画：其它按钮吸附到当前顺序对应的槽位。

        ``keep``（被拖按钮）不动 —— 它要停在"松手位置"，由落定滑入动画
        带走（把它的位置吸附到槽位会让落定动画的起止值相等而消失）。
        """
        for anim in list(self._nav_shift_anims.values()):
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._nav_shift_anims.clear()
        for i, k in enumerate(self._nav_drag_order):
            b = self._nav_btns[k]
            if b is keep:
                continue
            b.move(b.x(), self._nav_slot_y(i))

    def _revert_nav_drag(self):
        """异常中断回滚：丢弃拖拽中的顺序，按钮回到 ``_nav_order`` 槽位并交还布局。"""
        anim = self._nav_drop_anim
        self._nav_drop_anim = None
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass
        for anim in list(self._nav_shift_anims.values()):
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._nav_shift_anims.clear()
        if self._nav_free_layout:
            for i, key in enumerate(self._nav_order):
                b = self._nav_btns[key]
                b.move(b.x(), self._nav_slot_y(i))
        self._nav_drag_order = []
        self._restore_nav_layout()

    def _animate_nav_drop(self, btn, target_y: int):
        """落定滑入：按钮从松手位置平滑滑到最终槽位，滑完再把布局交还。"""
        duration = self._nav_anim_ms(NAV_DROP_MS)
        if duration <= 0:
            btn.move(btn.x(), target_y)
            self._restore_nav_layout()
            return
        anim = QPropertyAnimation(btn, b"pos", btn)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(QPoint(btn.x(), btn.y()))
        anim.setEndValue(QPoint(btn.x(), target_y))
        anim.finished.connect(self._on_nav_drop_anim_done)
        self._nav_drop_anim = anim
        anim.start()

    def _on_nav_drop_anim_done(self):
        """落定滑入结束 → 清引用 + 把按钮交还布局（此后随窗口布局正常排布）"""
        anim = self.sender()
        if anim is self._nav_drop_anim:
            self._nav_drop_anim = None
        anim.deleteLater()
        self._restore_nav_layout()

    def _on_nav_drag_started(self, btn):
        """进入拖拽：按钮脱离布局自由跟随鼠标，邻居越过中点即滑开让位。

        先做防御性复位（上一次拖拽若有残留状态，含未落定的自由布局与动画）。
        """
        self._abort_nav_settle_animations()   # 动画中再次拖拽 → 先停旧动画
        # 停掉的动画会把按钮留在中间位置 → 先按布局吸附回槽位，
        # 否则下面冻结的"槽位 Y"是动画中间值（间距被算错），拖拽判定全乱。
        # ⚠ 必须先 invalidate()：QLayout.activate() 只在布局"脏"时才真正
        #   重排，否则直接空转返回，按钮仍停在动画中间位置（2026-09-24 实测）。
        self._nav_btns_layout.invalidate()
        self._nav_btns_layout.activate()
        # 防御：上一轮拖拽若未正常收尾（自由布局未交还），先回滚
        if self._nav_free_spacer is not None:
            self._revert_nav_drag()
        self._nav_drag_btn = btn
        if not self._freeze_nav_buttons():
            self._nav_drag_btn = None
            return
        self._nav_drag_grab_dy = self._nav_drag_press_offset(btn)
        btn.raise_()
        self._acquire_nav_drag_cursor()
        self._apply_nav_drag_lift(btn)

    def _nav_drag_press_offset(self, btn) -> int:
        """光标在按钮内的纵向偏移（以**按下点**为准）。

        用越过阈值后的移动事件点算，会让按钮整体滞后一个阈值位移，
        跟手感变差（与网址导航拖拽同一坑）。
        """
        press = getattr(btn, "_press_global", None)
        if press is None:
            return btn.height() // 2
        return btn.mapFromGlobal(press).y()

    def _on_nav_drag_moved(self, btn, global_pos):
        """拖拽中：按钮跟手；越过邻居中点时邻居立即滑开让位（空档跟鼠标走）。

        纵向钳制在首/末槽位之间，拖到设置/说明区域也不会跑出按钮列表。
        """
        if self._nav_free_spacer is None or btn is not self._nav_drag_btn:
            return
        ys = self._nav_slot_ys
        if not ys:
            return
        y = self._nav_area.mapFromGlobal(global_pos).y() - self._nav_drag_grab_dy
        y = max(ys[0], min(y, ys[-1]))
        btn.move(btn.x(), y)
        idx = int(round((y - ys[0]) / self._nav_slot_step()))
        idx = max(0, min(idx, len(self._nav_drag_order) - 1))
        if idx != self._nav_drag_order.index(btn.nav_key):
            self._reorder_nav_live(btn, idx)

    def _on_nav_drag_finished(self, btn, global_pos):
        """拖拽落定：交还布局 → 重排（被拖按钮滑入空档）→ 持久化。

        光标还原放在最前：即使后续换位逻辑出错，光标也不会卡在"抓手"。
        """
        self._release_nav_drag_cursor()
        self._clear_nav_drag_lift()
        self._nav_drag_btn = None
        # 防御：确保按钮自身拖拽态复位（正常路径 mouseReleaseEvent 已复位，
        # 这里兜底异常交界，严禁 _is_dragging 残留为 True）
        btn._is_dragging = False
        btn._press_global = None
        btn.setDown(False)
        if not self._nav_free_layout:
            return
        new_order = list(self._nav_drag_order)
        # 其它按钮吸附到让位后的槽位（让位动画可能还在跑）；被拖按钮
        # 必须停在松手位置，留给落定滑入动画
        self._settle_nav_shift_anims(keep=btn)
        self._nav_drag_order = []
        drag_pos = QPoint(btn.x(), btn.y())   # 松手时的实时位置（落定动画起点）
        # 只移除占位 spacer，**不**把按钮先插回旧顺序的布局：
        # 否则邻居会被 Layout 拽回旧槽位再滑一次（松手处二次抖动）。
        # _apply_nav_order 会按新顺序把按钮插回布局并补落定动画。
        # 注意 _nav_free_layout 保持 True，直到 _restore_nav_layout 真正交还布局
        if self._nav_free_spacer is not None:
            self._nav_btns_layout.removeItem(self._nav_free_spacer)
            self._nav_free_spacer = None
        if new_order == list(self._nav_order):
            # 位置未变（拖起又放回）→ 滑回原槽位，不落盘
            key = btn.nav_key
            if key in self._nav_order:
                self._animate_nav_drop(btn, self._nav_slot_y(self._nav_order.index(key)))
            else:
                self._restore_nav_layout()
            return
        btn.move(drag_pos)
        self._apply_nav_order(new_order, save=True)
        self._nav_free_layout = False   # _apply_nav_order 已按新顺序交还布局

    def _force_end_nav_drag(self):
        """异常路径兜底：窗口失活/隐藏/缩放时强制退出拖拽态（幂等）。

        覆盖 release 事件可能丢失的场景（Alt+Tab、系统快捷键切窗、
        窗口被收起），把光标 / 视觉反馈 / 布局 / 按钮拖拽态全部复位，
        保证任何路径退出拖拽后光标都能还原、按钮不卡在自由布局里。
        """
        if (not self._nav_drag_cursor_active and self._nav_drag_btn is None
                and self._nav_free_spacer is None):
            return   # 非拖拽态：幂等早退
        self._release_nav_drag_cursor()
        self._clear_nav_drag_lift()
        btn = self._nav_drag_btn
        self._nav_drag_btn = None
        self._revert_nav_drag()
        if btn is not None:
            try:
                btn._is_dragging = False
                btn._press_global = None
                btn.setDown(False)
            except RuntimeError:
                pass


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

        # 动画速度档位：初始化 + 变更时透传给任务面板（勾选动画时长缩放）
        try:
            self._page_tasks.set_anim_speed(self.anim_speed)
        except (AttributeError, TypeError, ValueError):
            pass
        self.anim_speed_changed.connect(self._page_tasks.set_anim_speed)

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
        # 记录最后浏览页面（供下次启动恢复；说明页 8 不计入）
        self._remember_last_page(index)
        # 刷新对应面板
        self._refresh_page(index)

    def _remember_last_page(self, index: int):
        """记录当前页面索引到配置（D3：记住上次页面功能）。

        - 说明页（索引 8）不记录，避免下次启动直接落在说明页
        - 值未变化时不写盘，避免频繁 I/O
        """
        if index == 8:
            return
        try:
            if self._config.get("last_page_index", 0) != index:
                self._config.set("last_page_index", int(index))
                self._config.save()
        except Exception:
            pass

    def _initial_page_index(self) -> int:
        """计算启动时应打开的页面索引。

        - 开启「启动时恢复上次页面」→ 返回上次浏览的页面（0-7，越界回退首页）
        - 未开启（默认）→ 返回首页（碎片工作台）
        """
        if self._config.get("restore_last_page", False):
            idx = self._config.get("last_page_index", 0)
            if isinstance(idx, int) and 0 <= idx <= 7:
                return idx
        return 0

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
        # 任务面板行委托为自绘，配色需显式同步（QSS 无法覆盖 delegate 绘制）
        if getattr(self, "_page_tasks", None) is not None:
            self._page_tasks.apply_theme()
        # 全局搜索对话框是懒创建的独立顶层窗口，已创建时同样要跟随主题
        if getattr(self, "_search_dialog", None) is not None:
            self._search_dialog.apply_theme()
        # 设置页的自绘开关（ToggleSwitch）与主题按钮也要跟随主题
        if getattr(self, "_page_settings", None) is not None:
            self._page_settings.apply_theme()
        # 素材网格的 delegate 是动态取色，重绘即可跟随主题
        if getattr(self, "_page_assets", None) is not None:
            self._page_assets.apply_theme()

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

    @property
    def current_theme(self) -> str:
        """当前主题名（"light" / "dark"）。

        必须是 property：子面板一律以属性方式访问
        （``self._host.current_theme``）。此前是普通方法，调用方拿到的是
        bound method 对象，``get_colors()`` 查表失败后静默回退默认主题，
        导致浅色主题下碎片列表代理、任务列表、知识库面板都取了深色配色
        （文字 #E4E8EE 落在浅色底上几乎不可见）。
        """
        return self._theme

    @property
    def anim_speed(self) -> float:
        """当前动画速度档位（0.5-2.0）。

        供任务面板等子控件按 ``CHECK_ANIM_MS / anim_speed`` 缩放动画时长，
        使勾选动画与悬浮球动画速度档位一致。
        """
        try:
            speed = float(self._config.get("anim_speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    # ==================================================================
    # 使用说明页面（QStackedWidget 第 9 页，索引 8，F1 切换）
    # ==================================================================
    @staticmethod
    def _help_html() -> str:
        """使用说明富文本内容（与各页面功能保持同步更新）"""
        return """
        <p>FloatPulse 是一款常驻桌面的悬浮球效率工具：所有碎片、任务、笔记、素材都保存在本机，不联网、不登录。以下说明按「快捷键 → 悬浮球 → 小卡片 → 各功能面板 → 托盘与设置」的顺序展开。</p>

        <h3>⌨️ 全局快捷键</h3>
        <p>• <b>Esc</b>：退出程序（主窗口与小卡片中都生效）<br>
        • <b>Ctrl+W / Ctrl+H</b>：隐藏主窗口（程序继续在托盘后台运行）<br>
        • <b>Ctrl+T</b>：切换浅色 / 深色主题<br>
        • <b>Ctrl+K</b>：全库统一搜索（碎片 / 任务 / 笔记 / 素材，双击结果跳转到对应面板）<br>
        • <b>F1</b>：进入使用说明页；再按一次返回进入前的页面<br>
        • <b>Ctrl+1 ~ Ctrl+7</b>：依次切换到 碎片 / 任务 / 笔记 / 知识库 / 素材 / 网址导航 / 设置<br>
        • <b>Ctrl+Alt+K</b>：呼出「快速捕捉」迷你输入条（回车存入碎片池，Esc 关闭；热键与开关可在设置中修改）</p>

        <h3>💠 悬浮球</h3>
        <p>• <b>鼠标悬停</b>：自动弹出小卡片（首次随机抽一张知识卡，之后恢复上次离开时的页签）<br>
        • <b>左键点击</b>：卡片已弹出 → 切到下一张；未弹出 → 随机抽一张并弹出<br>
        • <b>滚轮</b>：上一张 / 下一张顺序翻卡，手不用离开球<br>
        • <b>拖拽</b>：按住左键拖动位置，松手自动吸附到最近的屏幕边缘<br>
        • <b>空闲自动隐藏</b>：贴边静止若干秒后半隐藏到边缘，鼠标移近自动滑出（秒数见设置）<br>
        • <b>右键</b>：菜单 → 显示主窗口 / 退出程序<br>
        • <b>拖文件或图片到球上</b>：自动复制收录到「临时素材」<br>
        • <b>尺寸与外观</b>：球体即应用图标本体，大小与动画速度都在设置里调节</p>

        <h3>🃏 小卡片</h3>
        <p>• <b>七个页签</b>（左侧竖排图标）：🧩 碎片 / 💡 知识卡片 / 📋 日程任务 / 📝 临时笔记 / 🌐 网址导航 / 📎 临时素材 / 🚀 软件导航<br>
        • <b>弹出与收起</b>：悬停球自动弹出；鼠标移出「球 + 卡片」区域后自动收起（勾选设置里的「小卡片保持显示」可常驻）<br>
        • <b>拖动卡片</b>：在顶部空白条或内容空白处按住左键拖动整张卡片，悬浮球同步跟随、保持相对位置<br>
        • <b>模式记忆</b>：收起时所处的页签会被记住，下次弹出直接回到该页签<br>
        • <b>临时笔记</b>：停止输入 800ms 自动保存，关闭也不会丢字<br>
        • <b>日程任务</b>：可直接勾选完成，右键菜单编辑 / 删除<br>
        • <b>网址导航 / 软件导航 / 素材</b>：卡片里可直接打开，编辑仍在主窗口<br>
        • <b>右键卡片空白处</b>：退出程序</p>

        <h3>🧩 碎片工作台</h3>
        <p>• <b>自动收集</b>：复制文本、复制文件路径时自动入库（被过滤的应用除外）；按 Ctrl+Alt+K 也可手动快速捕捉<br>
        • <b>类型筛选</b>：全部类型 / 📋 剪贴板文本 / 📁 剪贴板路径 / 📥 文件拾取 / 📚 知识段落<br>
        • <b>搜索</b>：输入即筛（去抖 250ms），命中的关键词在条目里高亮<br>
        • <b>预览</b>：选中左侧条目，右侧显示完整内容，可「📋 复制」「✏️ 编辑」<br>
        • <b>多选批量</b>：勾选多条后可「🔗 合并选中」（可合并成一条并直接存为笔记）、「📋 复制选中」、「🗑 删除选中」、「清空全部」<br>
        • <b>右键单条</b>：查看详情 / 编辑内容 / 复制内容 / 存为笔记 / 加入知识库 / 添加至网址导航 / 删除</p>

        <h3>📋 日程任务</h3>
        <p>• <b>新增</b>：填写标题 + 选择截止日期 → 点「➕ 添加」<br>
        • <b>分组顺序</b>：逾期 → 今天 → 本周（明天 ~ 本周日）→ 以后 → 无日期 → 已完成<br>
        • <b>颜色</b>：<b>红色</b>已逾期 &nbsp; <b>橙色</b>今日到期 &nbsp; <b>灰色</b>已完成<br>
        • <b>批量操作</b>：「✓ 批量完成」「🗑 批量删除」「清除已完成」<br>
        • <b>右键单条</b>：标记完成 / 取消完成 / 编辑 / 删除<br>
        • <b>到期提醒</b>：程序启动后与每日 9:00 通过托盘气泡提示（可在设置中关闭）</p>

        <h3>📝 笔记管理</h3>
        <p>• <b>新建 / 删除</b>：「＋ 新建笔记」、「🗑 删除当前」<br>
        • <b>列表</b>：只显示标题；鼠标悬停可看到 标题 + 修改时间 + 内容预览<br>
        • <b>改名</b>：双击列表项或按 F2 就地改名；按内容自动生成的标题会随内容更新，手动改过的则不再自动变（右键可切回「🔄 标题跟随内容」或「🔒 锁定标题」）<br>
        • <b>自动保存</b>：停止输入 800ms 落盘；退出程序前会强制保存未落盘的改动<br>
        • <b>状态栏</b>：显示「刚刚 / N 分钟前 / 3 小时前 / 昨天 HH:MM / N 天前」，有未保存改动时前面加「● 未保存」<br>
        • <b>搜索</b>：匹配标题与内容；若正在编辑的笔记被搜索条件过滤掉，状态栏会明确提示<br>
        • <b>右键</b>：编辑标题… / 删除此笔记</p>

        <h3>📚 知识库</h3>
        <p>• <b>数据来源</b>：程序目录下的「知识库.docx」，主窗口与卡片共用。文件名固定，须与主程序放同一文件夹：<b>源码运行放项目根</b>（与 data/ 同级），<b>打包后放 exe 同目录</b>；改名或移走会导致知识卡片无内容（程序仍可运行）<br>
        • <b>外部编辑</b>：可直接用 Word/WPS 打开该 docx 修改并保存 → 面板提示「⚠️ 检测到外部修改」时点「🔄 重新加载」即生效，程序启动时也会自动检测。每个非空段落（去空格后 ≥ 4 字）就是一张知识卡片，过短段落自动忽略<br>
        • <b>重新加载</b>：「🔄 重新加载」重新读取 docx<br>
        • <b>新增内容</b>：「➕ 新增知识」追加到 docx 末尾；「📥 加入碎片池」把选中段落送进碎片工作台<br>
        • <b>右键段落</b>：编辑 / 删除 / 在此后新增 / 加入碎片池（删除与重新加载有玻璃风格确认框）<br>
        • <b>搜索</b>：输入去抖 250ms 实时过滤段落，无结果时显示占位提示；双击段落可直接编辑<br>
        • <b>状态提示</b>：检测到 docx 被外部程序改动时提示「⚠️ 检测到外部修改，建议重新加载」。data/docx_meta.json 是程序自动维护的指纹缓存，请勿手工编辑</p>

        <h3>📎 临时素材</h3>
        <p>• <b>收录方式</b>：拖图片 / 文件到悬浮球；复制图片到剪贴板（Excel、Word 一类图文混排仍按文本收集）<br>
        • <b>打开</b>：双击用系统默认程序打开；右键 打开 / 另存为 / 删除<br>
        • <b>批量</b>：「📁 打开素材文件夹」「🔄 刷新」「🗑 清空全部」<br>
        • <b>容量</b>：条数上限与保留天数在「设置 → 临时素材上限 / 素材保留天数」调整（0 天表示不按天数清理）</p>

        <h3>🌐 网址导航</h3>
        <p>• <b>添加站点</b>：填名称 + URL，地址会自动补全 http:// 或 https://<br>
        • <b>整理</b>：拖拽行可排序；右键站点：打开 / 编辑 / 删除<br>
        • <b>一键收集</b>：碎片工作台里 URL 类碎片右键 →「添加至网址导航」<br>
        • <b>卡片入口</b>：小卡片的「🌐 网址导航」页签可直接点开，编辑仍在主窗口</p>

        <h3>🚀 软件导航</h3>
        <p>• <b>启动</b>：左键点击卡片即用系统默认方式启动对应的 exe（需要管理员权限的程序会提示提权启动）<br>
        • <b>管理</b>：「📋 管理软件列表」中新增 / 编辑 / 删除条目，可填 名称、exe 路径、图标（.ico / .png）、备注<br>
        • <b>卡片尺寸</b>：「设置 → 软件卡片尺寸」调节（60 ~ 140px，实时生效）<br>
        • <b>小卡片</b>：卡片的「🚀 软件导航」页签是同一份列表，可直接启动</p>

        <h3>🔔 托盘与后台</h3>
        <p>• <b>单击托盘图标</b>：显示 / 隐藏主窗口<br>
        • <b>右键托盘图标</b>：显示 / 隐藏主窗口、显示 / 隐藏悬浮球、退出程序<br>
        • <b>关闭主窗口</b>：默认最小化到托盘不退出（可在设置中改为直接退出）<br>
        • <b>单实例运行</b>：重复启动会唤醒已在运行的窗口，不会开出第二个进程</p>

        <h3>⚙️ 设置</h3>
        <p>• <b>主题外观</b>：浅色 / 深色一键切换<br>
        • <b>行为配置</b>：剪贴板历史上限、悬浮球自动隐藏秒数、显示悬浮球、小卡片保持显示、临时素材上限与保留天数、素材缩略图大小、软件卡片尺寸、动画速度、悬浮球大小、开机自动启动、启动时恢复上次页面、关闭主窗口最小化到托盘、剪贴板过滤应用、自动收集剪贴板图片、任务到期提醒、全屏应用时自动隐藏悬浮球<br>
        • <b>全局快速捕捉</b>：开关 + 自定义热键（格式如 Ctrl+Alt+K，被占用时会提示）<br>
        • <b>改动即生效</b>：所有设置实时保存，无需手动保存；「↺ 恢复默认设置」恢复全部默认值（软件导航条目、窗口与悬浮球位置会保留）</p>

        <h3>💡 数据与迁移</h3>
        <p>• 全部数据都在本地：程序目录的 <b>data/</b>（碎片 / 任务 / 笔记 / 素材索引 / 配置）与 <b>知识库.docx</b><br>
        • 换电脑时把整个程序文件夹拷走即可，数据跟着走<br>
        • 程序不联网、不登录、不上传任何内容，断网状态下所有功能照常可用</p>
        """

    def _build_help_page(self):
        """构建使用说明页面（嵌入 QStackedWidget，与其它页面同层级切换）"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(12)

        # 标题
        title = QLabel("📖 FloatPulse 使用说明")
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


