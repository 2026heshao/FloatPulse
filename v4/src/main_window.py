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

页面（物理索引 = NAV_PAGE_INDEX，永不变；左栏显示顺序由 nav_order 决定）：
  0. 碎片工作台   - 碎片列表/筛选/搜索/合并/删除
  1. 日程任务     - 任务输入/列表/批量勾选/右键编辑
  2. 笔记管理     - 笔记列表 + 编辑区 + 自动保存
  3. 知识库       - 段落列表/勾选加入碎片池/段落增删改
  4. 临时素材     - 图片/文件收录、打开、另存、清理
  5. 网址导航     - 站点增删改 + 拖拽排序
  6. 设置         - 左分类导航 + 每类独立滚动页
  7. 软件导航     - 软件列表/启动/图标
  8. 使用说明     - F1 切换（HELP_PAGE_INDEX，不参与「记住上次页面」）
  9. 插件中心     - 插件页与知识库搜索入口（2026-09-27 新增）

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

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QButtonGroup,
    QFrame, QMenu, QApplication, QFileDialog,
    QScrollArea, QSpacerItem, QSizePolicy, QTextBrowser,
)
from PyQt6.QtCore import (
    Qt, QPoint, pyqtSignal, QTimer, QRect, QRectF, QEvent,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
    QSequentialAnimationGroup, QSize,
)
from PyQt6.QtGui import (QColor, QFont, QPainter, QAction, QIcon, QShortcut,
                         QKeySequence)

from src.theme import get_main_window_qss, get_colors, next_theme_on_toggle
from src.constants import DEFAULT_THEME
from src import motion
from src import controls
from src import icon_render
from src.icons import NAV_ICON, plugin_page_icon, strip_leading_emoji
from src.app_version import APP_VERSION
from src.config import (
    sanitize_nav_order, DEFAULT_NAV_ORDER, LAST_PAGE_INDEX_MAX,
)
from src.nav_layout import (
    NAV_GROUPS, NAV_GROUP_TITLES, NAV_FIXED_ITEM_GROUP, NAV_FIXED_ITEM_ORDER,
    NAV_DEFAULT_EXPANDED, group_of, is_plugin_key,
    sanitize_expanded_groups, toggle_group, split_by_group,
    reorder_within_group,
)
from src.glass import GlassPanel, NavIndicator, NavGroupHeader
from src import appearance
from src.controls import IconButton, PageTitle, ScreenToast, SmoothButton
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
    "plugins": 9,      # 插件中心（2026-09-27 新增）
}
# 使用说明页的物理索引（QStackedWidget 第 9 个，F1 切换；不参与「记住上次页面」）
HELP_PAGE_INDEX = 8
# 各功能页按钮文案（key 固定，文案可随 UI 调整）
# ★ 2026-09-30（UI 强化 A2）：文案**不再带 emoji 图标前缀**。图标改由
#   ``src/icons.py`` 自绘、以 setIcon 挂在按钮上，原因有两层：
#     1) emoji 字形来自系统 emoji 字体，离屏渲染 / 精简系统 / 字体缺失时
#        一律退化成空心方框（README 首屏截图长期带着这种方框）；
#     2) 位图图标无法用 QSS 着色，emoji 的字形颜色也不受 token 控制 ——
#        选中态"变主色"只能靠重设 pixmap，字符做不到。
#   图标名由 ``NAV_ICON`` 映射（键名与图形解耦），渲染尺寸 NAV_ICON_SIZE。
NAV_PAGE_TITLES = {
    "fragments": "碎片工作台",
    "tasks": "日程任务",
    "notes": "笔记管理",
    "knowledge": "知识库",
    "assets": "临时素材",
    "apps": "软件导航",
    "nav": "网址导航",
    "plugins": "插件中心",
}
# 侧栏导航图标的渲染尺寸（逻辑像素；与 13px 字号的视觉重量对齐）
NAV_ICON_SIZE = 16

# 非拖拽固定项的文案（设置 / 使用说明）。与 NAV_PAGE_TITLES 分开存放，
# 因为这两项不在 NAV_PAGE_INDEX 里 —— 它们由 nav_layout.NAV_FIXED_ITEM_GROUP
# 归入 system 组，键名是宿主内部逻辑名（不是物理页面索引），
# 混进 NAV_PAGE_TITLES 会让"遍历功能页键"的代码多出两个需要特判的项。
NAV_PAGE_TITLES_FIXED = {
    "settings": "设置",
    "help": "使用说明",
}
# 拖拽换位：位移超过该值（像素）才进入拖拽，否则视为普通点击切页
NAV_DRAG_THRESHOLD = 8
# 左栏实时让位：邻居滑开让位动画时长 / 落定滑入动画时长（均随动画速度档位缩放）
NAV_SHIFT_MS = 150
NAV_DROP_MS = 180
# 侧栏分组展开/折叠动画（2026-09-29）：条目逐条做 maximumHeight 过渡，
# 等价于 web 的 max-height 过渡；展开比折叠略长（展开要"长出来"，
# 折叠要"收回去"，同长时收的过程会显得拖沓）。
# 2026-10-01 丝滑度调优（离屏探针取证）：280/210/18 → 220/170/12，
# 缓动 OutQuint→OutCubic —— OutQuint 对 39px 行高在 ~105ms 后余下
# 175ms 只挪 1px，叠上 stagger 观感是"急起—长尾漂移"；OutCubic 220ms
# 的静止尾巴 <1 帧。motion.MOTION 的 slow/stagger 是全局语义档，
# 与这里的本地实现常量解耦（不再同值）。
NAV_GROUP_EXPAND_MS = 220      # 展开时长
NAV_GROUP_COLLAPSE_MS = 170    # 折叠时长
NAV_GROUP_STAGGER_MS = 12      # 逐条错峰（stagger），最多叠加 4~5 条
NAV_ARROW_MS = 240             # 箭头旋转时长


class _NavButton(SmoothButton):
    """左栏功能页导航按钮：普通点击切页 + 纵向拖动换位。

    基类 SmoothButton（清单 S2/S4）：hover/press 背景走 overlay 插值、
    按下为绘制级下沉 —— navBtn 不再有 QSS 按下态背景/margin 变化，
    拖拽期间 sizeHint 恒定，行高 +1px 问题从根上消失。

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
            # 拖拽落定吞掉了 release（不调 super），SmoothButton 的按下
            # 过渡不会被 mouseReleaseEvent 收回 —— 必须显式回弹
            self.cancel_press_feedback()
            self.drag_finished.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        self._press_global = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)


class _NavSplitHandle(QWidget):
    """导航栏右侧的分割手柄：横向拖动调宽侧栏，双击恢复默认宽度。

    手柄只管手势、不管布局：按下/拖动/松手把原始全局 x 发给 MainWindow，
    由宿主夹取范围、套用宽度并落盘（``_on_split_*`` 三件套）。
    样式见 theme.py 的 navSplitHandle（平时隐形，悬停/拖动显线）。
    """

    split_pressed = pyqtSignal(int)     # 全局 x（按下）
    split_moved = pyqtSignal(int)       # 全局 x（拖动中实时）
    split_released = pyqtSignal()
    split_double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setProperty("dragging", True)
            self._repolish()
            # grabMouse：拖出细手柄后也持续收到 move，不会跟丢
            self.grabMouse()
            self.split_pressed.emit(int(event.globalPosition().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.split_moved.emit(int(event.globalPosition().x()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setProperty("dragging", False)
            self._repolish()
            self.releaseMouse()
            self.split_released.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.split_double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def _repolish(self):
        """dragging 动态属性变了 → 让 QSS 的 [dragging=...] 选择器重新生效"""
        st = self.style()
        st.unpolish(self)
        st.polish(self)
        self.update()


class MainWindow(QWidget):
    """生活悬浮球大窗口主UI"""

    # ---- 窗口尺寸常量 ----
    # 默认尺寸（2026-09-27 由 920×620 调大）：功能持续增加后，旧尺寸下
    # 碎片列表一屏仅 9 行、长文本普遍截断，且右侧预览区偏窄
    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 740
    # 最小尺寸 = 调大前的默认尺寸（布局已验证过的安全下限）。
    # 默认值与最小值**解耦**：默认变大后用户仍可手动缩小，不被硬下限卡住
    MIN_WIDTH = 920
    MIN_HEIGHT = 620
    SIDE_BAR_WIDTH = 168        # 默认宽度（可拖侧栏右侧分割手柄调整）
    SIDE_BAR_MIN_W = 140        # 拖动下限：再窄组标题/按钮文字开始截断
    SIDE_BAR_MAX_W = 280        # 拖动上限：再宽明显挤压内容区阅读体验
    TITLE_BAR_HEIGHT = 48
    SHADOW_MARGIN = 18           # 阴影留白边距
    WINDOW_RADIUS = 14           # 窗口圆角（与设计稿一致）
    # 默认尺寸按屏幕可用工作区钳制时预留的四周留白（小屏兜底，防开出屏幕外）
    SCREEN_SAFE_MARGIN = 40
    # 窗口几何落盘防抖间隔（毫秒）：连续拖动/缩放期间只写一次盘
    GEOMETRY_SAVE_DELAY = 600

    # ---- 信号 ----
    theme_changed = pyqtSignal(str)   # 主题切换时发射，参数为 "light"/"dark"
    data_changed = pyqtSignal(str)    # 数据变更时发射，参数为数据类型标识
    ball_visibility_changed = pyqtSignal(bool)  # 悬浮球显示/隐藏切换
    card_always_show_changed = pyqtSignal(bool)  # 小卡片保持显示模式切换
    asset_limits_changed = pyqtSignal(int, int, int)  # 临时素材上限变更（max_count, max_days, max_file_mb）
    anim_speed_changed = pyqtSignal(float)       # 悬浮球动画速度变更
    auto_hide_seconds_changed = pyqtSignal(int)  # 悬浮球空闲吸边隐藏秒数变更
    auto_hide_enabled_changed = pyqtSignal(bool)  # 悬浮球空闲吸边自动隐藏总开关变更
    ball_size_changed = pyqtSignal(int)          # 悬浮球球体直径变更
    mini_icon_size_changed = pyqtSignal(int)    # 小卡片软件导航页图标边长变更
    hide_on_fullscreen_changed = pyqtSignal(bool)  # 全屏应用自动隐藏开关变更
    screenshot_changed = pyqtSignal()            # 截图钉屏设置（开关/热键）变更
    plugins_changed = pyqtSignal(bool)           # 悬浮球外置插件总闸变更
    pomodoro_changed = pyqtSignal()              # 番茄钟设置（开关/时长/自动休息）变更
    task_focus_requested = pyqtSignal(int, str)  # 任务页请求对某任务开始专注（task_id, title）
    hidden_to_tray = pyqtSignal()                # 主窗口被收进托盘时发射（3.3：宿主提示一次）

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
        # 减弱动效总闸（#14）：启动即按配置置位，motion.duration 单点生效
        motion.set_reduce_motion(bool(config_manager.get("reduce_motion", False)))
        # 按钮丝滑过渡（清单 S2）：启动按档位置位；设置页变更走广播接线
        controls.set_ui_speed(config_manager.get("anim_speed", 1.0))
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
        # 窗口几何落盘：防抖定时器（懒创建）+ 初始化/恢复期间抑制保存标志
        self._geom_save_timer = None
        self._restoring_geometry = False

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
        # 本次拖拽发生在哪个组（组内拖拽只改组内相对顺序；2026-09-29 分组）
        self._nav_drag_group = None
        # 侧栏分组展开/折叠动画（QParallelAnimationGroup 列表，保持引用防 GC）
        self._nav_group_anims = []

        # 边缘缩放状态（方向字符串，None 表示非缩放中）
        self._resizing = None
        self._resize_start_global = QPoint()
        self._resize_start_geom = QRect()

        # 允许关闭标志（程序退出时使用）
        self._allow_close = False

        # 使用说明页面（F1 切换用）：记录进入说明页前的页面索引
        self._page_before_help = 0

        # ---- 页面懒加载状态（2026-10-01 启动丝滑化）----
        # 10 个固定页里只有首页(0)在构造期同步构建；其余页先以空占位
        # QWidget 占住 QStackedWidget 槽位（物理索引契约 0-9 + 插件页 10+
        # 不变），show 后由 QTimer 逐页后台构建替换（_start_lazy_warmup）。
        # 页属性全部 property 化：任何外部访问都会同步构建真页（幂等），
        # 对测试/宿主/信号完全透明。
        self._lazy_builders = {}      # index -> builder（_build_content_area 填充）
        self._real_pages = {}         # index -> 已构建真页
        self._lazy_placeholders = {}  # index -> 占位 widget
        self._lazy_pending = []       # 预热待构建序列
        self._lazy_warm_timer = None
        self._lazy_building = set()   # 正在构建中的页（防重入递归）

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

    # ---- 桌面便签管理器（knowledge_ball.main() 晚绑定注入；未注入为 None）----
    # 面板通过本 @property 读取（铁律：host 只读属性必须 property，
    # 否则子面板拿到 bound method 并静默回退）
    @property
    def sticky_manager(self):
        return getattr(self, "_sticky_manager", None)

    @sticky_manager.setter
    def sticky_manager(self, manager):
        self._sticky_manager = manager

    # ==================================================================
    # 导出到 Obsidian（唯一实现；设置页按钮与三个面板右键菜单共用）
    # ==================================================================
    def export_to_obsidian(self, interactive: bool = True):
        """把笔记 / 碎片 / 任务**单向**导出为 Markdown 到 Obsidian vault。

        - vault 路径取 ``config.obsidian_vault_path``；为空且 ``interactive``
          → 弹目录选择框，选完记住；**取消则直接返回（不导出、不报错）**
        - 导出范围取 ``config.export_notes / export_fragments / export_tasks``
        - ``interactive`` 时用 show_toast 给轻提示（含文件数与失败条数）
        - 导出是**只读操作**：不删改任何 json 数据；同名 md 按语义覆盖

        :return: ``md_export.ExportResult``；用户取消选择目录时返回 ``None``
        """
        from src import md_export

        vault = str(self._config.get("obsidian_vault_path", "") or "")
        if not vault:
            if not interactive:
                return None
            start_dir = os.path.expanduser("~")
            vault = QFileDialog.getExistingDirectory(
                self, "选择 Obsidian vault 目录", start_dir)
            if not vault:
                return None                  # 用户取消：不导出、不报错
            self._config.set("obsidian_vault_path", vault)
            self._config.save()

        opts = {
            "export_notes": bool(self._config.get("export_notes", True)),
            "export_fragments": bool(self._config.get("export_fragments", True)),
            "export_tasks": bool(self._config.get("export_tasks", True)),
        }
        result = md_export.export_all(
            vault,
            self._note_manager.get_all_notes(),
            self._fragment_manager.get_all_fragments(),
            self._task_manager.get_all_tasks(),
            opts,
        )

        if interactive:
            if result.errors:
                _ident, reason = result.errors[0]
                self.show_toast(
                    f"导出完成：共 {len(result.files_written)} 个文件，"
                    f"失败 {len(result.errors)} 条（{reason}）", 4200)
            else:
                self.show_toast(
                    f"已导出到 Obsidian：共 {len(result.files_written)} 个文件",
                    3200)
        return result

    def _on_fragments_trimmed(self, count: int):
        """碎片池超限自动淘汰时通报用户（此前是静默删除，用户不知道数据少了）"""
        self.show_toast(f"碎片池已达上限，自动清理了 {count} 条最早的碎片")

    # ==================================================================
    # 快捷键初始化
    # ==================================================================
    def _init_shortcuts(self):
        """初始化主窗口快捷键"""
        # 注（1.3）：主窗口 Esc 不绑定任何动作——旧实现把 Esc 绑成
        # ApplicationShortcut 退出整个程序，与应用内其他表面的 Esc 语义
        # （关卡/取消）冲突且易误杀进程。退出只走托盘菜单 / 悬浮球右键。

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

        # Ctrl+K: 站内搜索（知识库 / 笔记 / 碎片 / 任务 / 素材聚合）
        # 入口指向 kb-search 插件页（2026-09-29 全库搜索并入插件）。
        # 插件停用 / 加载失败时不做第二套 UI，只提示一句该怎么恢复。
        search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        search_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        search_shortcut.activated.connect(self._open_search_entry)

        # Ctrl+1~8: 切到左栏显示顺序第 N 个功能页（快捷键跟随位置：
        # 拖动换位后 Ctrl+N 指向新排到第 N 位的那个功能页）
        for i in range(8):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i+1}"), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda n=i: self._switch_to_nav_slot(n))

    # ==================================================================
    # 站内搜索（Ctrl+K）
    # ------------------------------------------------------------------
    # 2026-09-29：原内置的「全库搜索对话框」整体并入 kb-search 插件，
    # 宿主这里只保留两个职责——**打开入口**与**跳转回面板**。
    #
    # 为什么搜索做成插件而不是内置页（不是偷懒，是架构决定）：
    #   0-9 的内置页槽位已被占满（见 NAV_PAGE_INDEX 与 LAST_PAGE_INDEX_MAX），
    #   而「物理索引与功能的映射永不改变」是本文件的**核心约束**；10+ 才是
    #   约定给插件的段位（show_plugin_page 里 idx < 10 直接 False）。
    # ==================================================================
    SEARCH_PLUGIN_ID = "kb-search"

    def _open_search_entry(self):
        """Ctrl+K / 悬浮球菜单：打开站内搜索插件页。

        插件被停用 / 加载失败时**不做第二套 UI**（用户主动停用即视为放弃
        这项功能），只提示一句恢复方式——否则 Ctrl+K 毫无反应，用户会
        以为程序坏了。
        """
        try:
            if self.show_plugin_page(f"plugin:{self.SEARCH_PLUGIN_ID}"):
                return
        except Exception as exc:                      # noqa: BLE001
            self._log_warn(f"[搜索] 打开插件页失败：{exc!r}")
        try:
            self.show_toast("站内搜索插件未启用：请到「插件中心」启用后重试")
        except Exception:                             # noqa: BLE001
            pass

    @staticmethod
    def _log_warn(msg: str):
        """写宿主 app.log（懒 import，避免 main_window 顶层多一个依赖环风险）"""
        try:
            from src.logger import get_logger
            get_logger().warning(msg)
        except Exception:                             # noqa: BLE001
            pass

    def show_search_result(self, kind: str, keyword: str, num=None) -> bool:
        """搜索结果跳转——站内搜索插件经 ``ctx.parent_window()`` 调用的**公开入口**。

        （与 ``show_plugin_page`` 同款的「约定公开入口」写法：插件不碰
        宿主私有属性，宿主换实现也不影响插件。）

        - ``kind``：插件侧数据源标识，映射到物理页索引
        - ``num``：只有知识库用（位置型数据源）→ 定位到第 num 段
        - 未知 kind → 返回 False，让插件侧静默降级

        知识库额外做定位：它是段落列表，几百段时不定位等于「搜到了也找不到」；
        其余页面清单短，切过去肉眼可及。
        """
        idx = {"fragment": 0, "task": 1, "note": 2,
               "knowledge": 3, "asset": 4}.get(str(kind or ""))
        if idx is None:
            return False
        try:
            self._switch_page(idx)                    # 内部已同步刷新该面板
        except Exception as exc:                      # noqa: BLE001
            self._log_warn(f"[搜索] 跳转失败 kind={kind} -> {exc!r}")
            return False
        self._bring_keyword_to_page(idx, keyword, num)
        return True

    def _bring_keyword_to_page(self, page_idx: int, keyword: str, num=None):
        """跳转后把关键词 / 段号带进目标面板。

        整体兜底：带入失败**不影响已经完成的切页**（用户至少到了对的页面），
        所以这里只记日志、不向上抛。
        """
        keyword = str(keyword or "").strip()
        try:
            if page_idx == 0 and hasattr(self._page_fragments, "_frag_search"):
                # 走专用入口：立即过滤，不等 250ms 搜索去抖
                if hasattr(self._page_fragments, "apply_external_keyword"):
                    self._page_fragments.apply_external_keyword(keyword)
                else:
                    self._page_fragments._frag_search.setText(keyword)
            elif page_idx == 2 and hasattr(self._page_notes, "_note_search"):
                self._page_notes._note_search.setText(keyword)
            elif page_idx == 3 and num is not None:
                panel = getattr(self, "_page_knowledge", None)
                if panel is not None:
                    panel.locate_paragraph(num)
        except Exception as exc:                      # noqa: BLE001
            self._log_warn(f"[搜索] 带入关键词失败 page={page_idx} -> {exc!r}")

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
        w, h = self._compute_default_size()
        self.resize(w, h)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        # 开启鼠标跟踪：悬停时实时检测边缘并切换缩放指针
        self.setMouseTracking(True)
        # 初始位置：优先恢复上次几何；无有效记忆时居中到屏幕
        # _restoring_geometry 用于抑制构造期 resize/move 事件触发落盘
        self._restoring_geometry = True
        try:
            if not self._restore_saved_geometry():
                self._ensure_on_screen(init=True)
        finally:
            self._restoring_geometry = False
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

    # ==================================================================
    # 窗口几何：默认尺寸钳制 / 上次几何恢复 / 防抖落盘
    # ==================================================================
    def _compute_default_size(self) -> tuple:
        """默认尺寸：放得下就用理想值，放不下才按工作区收缩。

        - 屏幕 ≥ 默认尺寸 → 直接用 DEFAULT_WIDTH/HEIGHT（不缩水，保证选定值生效）
        - 屏幕偏小 → 收缩到「工作区 - 两倍安全留白」，但不低于 MIN_WIDTH/HEIGHT
          （宁可略超屏也不让布局被压坏）
        """
        w, h = self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT
        try:
            scr = get_screen_geometry()
            if scr.width() > 0 and scr.height() > 0:
                if w > scr.width():
                    w = max(self.MIN_WIDTH,
                            scr.width() - self.SCREEN_SAFE_MARGIN * 2)
                if h > scr.height():
                    h = max(self.MIN_HEIGHT,
                            scr.height() - self.SCREEN_SAFE_MARGIN * 2)
        except Exception:
            pass
        return w, h

    def _parse_saved_geometry(self):
        """解析配置里的几何字符串 "x,y,w,h"。

        非法 / 缺失 / 尺寸小于最小尺寸 → 返回 None（回退默认尺寸更安全）
        """
        raw = self._config.get("main_window_geometry", "")
        if not raw or not isinstance(raw, str):
            return None
        parts = raw.split(",")
        if len(parts) != 4:
            return None
        try:
            x, y, w, h = (int(float(p)) for p in parts)
        except (TypeError, ValueError):
            return None
        if w < self.MIN_WIDTH or h < self.MIN_HEIGHT:
            return None
        return QRect(x, y, w, h)

    def _restore_saved_geometry(self) -> bool:
        """恢复上次窗口几何；无有效记忆 / 屏幕异常时返回 False（调用方回退居中）。

        恢复值一律做钳制：分辨率变小、外接屏拔掉、记忆值过大时,
        都不能把窗口放到屏幕外或撑得比工作区还大。
        """
        geom = self._parse_saved_geometry()
        if geom is None:
            return False
        try:
            scr = get_screen_geometry()
        except Exception:
            return False
        if scr.width() <= 0 or scr.height() <= 0:
            return False
        w = max(self.MIN_WIDTH, min(geom.width(), scr.width()))
        h = max(self.MIN_HEIGHT, min(geom.height(), scr.height()))
        x = max(scr.left(), min(geom.x(), scr.right() - w))
        y = max(scr.top(), min(geom.y(), scr.bottom() - h))
        self.setGeometry(x, y, w, h)
        return True

    def _schedule_save_geometry(self):
        """窗口尺寸/位置变化后防抖落盘（连续拖动/缩放只写一次）"""
        if self._restoring_geometry:
            return
        if self._geom_save_timer is None:
            self._geom_save_timer = QTimer(self)
            self._geom_save_timer.setSingleShot(True)
            self._geom_save_timer.setInterval(self.GEOMETRY_SAVE_DELAY)
            self._geom_save_timer.timeout.connect(self._save_geometry)
        self._geom_save_timer.start()

    def _save_geometry(self, force: bool = False):
        """把当前窗口几何写入配置。

        - 不可见时不写（``force=True`` 供退出兜底时跳过该判定）
        - 最大化 / 最小化时不写（避免把最大化尺寸记成正常尺寸）
        - 尺寸异常（小于最小尺寸）时不写
        - 与已存值相同则不写盘（省 I/O）
        """
        if self._geom_save_timer is not None:
            self._geom_save_timer.stop()
        if self._restoring_geometry:
            return
        if not force and not self.isVisible():
            return
        if self.isMaximized() or self.isMinimized():
            return
        g = self.geometry()
        if g.width() < self.MIN_WIDTH or g.height() < self.MIN_HEIGHT:
            return
        value = f"{g.x()},{g.y()},{g.width()},{g.height()}"
        if value == self._config.get("main_window_geometry", ""):
            return
        self._config.set("main_window_geometry", value)
        self._config.save()

    def save_geometry_now(self):
        """立即落盘窗口几何（程序退出前由主程序兜底调用，防抖窗口内也不丢）"""
        self._save_geometry(force=True)

    def showEvent(self, event):
        """窗口显示前确保位置在屏幕内；首次显示播放入场动画并启动页面预热"""
        super().showEvent(event)
        self._ensure_on_screen(init=False)
        QTimer.singleShot(0, self._init_nav_indicator_position)
        self._play_entrance_animation()
        # 启动丝滑化：构造期只建首页，show 后逐页后台构建（一次性）
        self._start_lazy_warmup()

    def _init_nav_indicator_position(self):
        """布局完成后把指示条对齐到当前选中项（不带动画，避免从 0 滑下来）"""
        if not hasattr(self, "_nav_group"):
            return
        btn = self._nav_group.checkedButton()
        if btn is None and hasattr(self, "_stack"):
            btn = self._nav_group.button(self._stack.currentIndex())
        self._move_nav_indicator(btn, animate=False)

    def _target_window_opacity(self):
        """主窗口目标不透明度（0.5~1.0），来自配置 window_opacity 百分比"""
        try:
            pct = int(self._config.get("window_opacity", 100))
        except (TypeError, ValueError):
            pct = 100
        return max(0.5, min(1.0, pct / 100.0))

    def _apply_window_opacity(self):
        """把配置里的窗口透明度应用到主窗（设置页步进 / 恢复默认时调用）"""
        self.setWindowOpacity(self._target_window_opacity())

    def _play_entrance_animation(self):
        """首次显示：淡入 + 16px 上移（「浮起来」的品牌观感，只播一次）

        时长收口到 motion token（enter_fade / enter_rise，2026-10-03）；
        reduce_motion 下入场瞬显（不再设 0 透明度起点，避免闪黑）。
        """
        if self._entrance_played:
            return
        self._entrance_played = True
        fade_ms = motion.duration(motion.MOTION["enter_fade"], self.anim_speed)
        rise_ms = motion.duration(motion.MOTION["enter_rise"], self.anim_speed)
        if motion.reduce_motion() or fade_ms <= 0 or rise_ms <= 0:
            self.setWindowOpacity(self._target_window_opacity())
            return
        end_pos = self.pos()
        start_pos = QPoint(end_pos.x(), end_pos.y() + 16)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(fade_ms)
        fade.setStartValue(0.0)
        # 淡入终点 = 配置的目标透明度（用户设了半透明时入场直接到位）
        fade.setEndValue(self._target_window_opacity())
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(rise_ms)
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
            self.resize(*self._compute_default_size())
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
            self._max_btn.set_icon_name("restore")
            self._max_btn.setToolTip("还原窗口")
        else:
            self._max_btn.set_icon_name("maximize")
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
        # 尺寸变化 → 防抖落盘（最大化/最小化由 _save_geometry 自行跳过）
        self._schedule_save_geometry()

    def moveEvent(self, event):
        super().moveEvent(event)
        # 位置变化（用户拖动标题栏）→ 防抖落盘
        self._schedule_save_geometry()

    def closeEvent(self, event):
        """关闭窗口的智能处理：
        - _allow_close=True（程序主动退出）→ 直接关闭
        - close_to_tray=True（默认）→ 最小化到托盘（托盘左键/右键可随时唤回）
        - close_to_tray=False → 沿用旧规则：
            悬浮球可见 → 只隐藏主窗口（保持后台运行）
            悬浮球不可见 → 退出程序（避免无窗口的僵尸进程）
        """
        # 关闭/隐藏/退出前立即落盘，兜住防抖窗口内尚未写入的尺寸位置
        self._save_geometry()
        if self._allow_close:
            event.accept()
            return
        if self._config.get("close_to_tray", True):
            # 托盘图标常驻，始终有唤回入口 → 隐藏到托盘
            event.ignore()
            self.hide()
            # 3.3：通知宿主（首次时托盘气泡提示一次，tray_hint_shown 防重复）
            self.hidden_to_tray.emit()
            return
        # 兼容旧行为：按悬浮球可见性决定隐藏还是退出
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

        # 中部：侧栏 + 分割手柄（拖动调宽侧栏）+ 内容区
        center = QHBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        center.addWidget(self._build_side_bar())
        self._nav_split = self._build_nav_split()
        center.addWidget(self._nav_split)
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

        # ★ 版本号读 app_version.APP_VERSION（2026-09-29 修，原因同侧栏底部版本号）
        sub = QLabel(f"v{APP_VERSION}")
        sub.setObjectName("titleBarSub")
        h.addWidget(sub)
        h.addStretch()

        # 主题切换按钮（🌙/☀️ 一钮两态：换主题时 _apply_theme 换图形）
        self._theme_btn = IconButton("moon", size=36, icon_size=16,
                                     object_name="iconBtn", host=self,
                                     tooltip="切换深色/浅色主题")
        self._theme_btn.clicked.connect(self._toggle_theme)
        h.addWidget(self._theme_btn)

        # 最小化按钮
        min_btn = IconButton("minimize", size=36, icon_size=16,
                             object_name="iconBtn", host=self, tooltip="最小化")
        min_btn.clicked.connect(self.showMinimized)
        h.addWidget(min_btn)

        # 全屏/还原切换按钮：最大化铺满屏幕 ⇄ 还原默认尺寸
        self._max_btn = IconButton("maximize", size=36, icon_size=16,
                                   object_name="iconBtn", host=self,
                                   tooltip="最大化窗口")
        self._max_btn.clicked.connect(self._toggle_maximize)
        h.addWidget(self._max_btn)

        # 关闭按钮（触发 closeEvent 智能判断：悬浮球可见则隐藏，不可见则退出）
        close_btn = IconButton("close", size=36, icon_size=16,
                               object_name="iconBtn", host=self, danger=True,
                               tooltip="关闭窗口")
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)

        return bar

    # ==================================================================
    # 左侧导航栏
    # ==================================================================
    def _build_side_bar(self):
        """构建左栏：品牌字 + 可滚动的「四组导航」+ 固定底部版本号。

        ★ 2026-09-29 重构（侧栏容量问题）：从"一条平铺列表"改为
        「四组分类 + 手风琴折叠（任何时刻恰好一组展开）+ 溢出全局滚动」。
        动因：页面插件数量无上限，而 920×620 最小窗口下侧栏只放得下
        约 13 项，超出的会被 Qt **静默压扁**（35px → 17px → 文字消失），
        不报错、不隐藏、无滚动条 —— 用户看到的只是"东西不见了"。

        为什么组标题与条目**同处一个扁平布局**（而非每组一个嵌套
        QVBoxLayout）：拖拽换位用**绝对坐标 + grabMouse** 实现，坐标基准
        必须是连续、可预测的纵向序列；嵌套布局会让"槽位 Y"取决于各子布局
        的边界，既有验证脚本对 ``_nav_btns_layout.indexOf()`` 的断言也全部
        失效。扁平序列 = 组标题与条目交错，两类问题一起规避。

        全部条目**始终留在布局里**，靠 ``setVisible`` 控制显隐：Qt 的
        QBoxLayout 会把隐藏项当作空项跳过（零高度），而 ``indexOf`` 仍然
        返回真实下标 —— 冻结/交还布局的槽位运算因此不用区分"折叠"这件事。
        """
        side = QWidget()
        side.setObjectName("sideBar")
        # 宽度可拖（右侧分割手柄）：读用户上一次保存的宽度，缺失/越界回退默认
        self._side_bar = side
        side.setFixedWidth(self._clamped_side_width())
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

        # 品牌字：原为 "WORKBENCH"，与分组标题「工作台」重复读着像两件事，
        # 改为产品名（2026-09-29）。
        group_top = QLabel("FLOAT PULSE")
        group_top.setObjectName("sideBarTitle")
        v.addWidget(group_top)

        # ---- 滚动容器：品牌字与版本号固定，中间四组可滚 ----
        # widgetResizable=True → 内容宽度跟随视口（168px 侧栏里横向不滚）；
        # 纵向是否出滚动条由内容的 sizeHint 与视口高度比较决定（按需出现）。
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setObjectName("navScroll")
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._nav_content = QWidget()
        self._nav_content.setObjectName("navScrollContent")
        self._nav_scroll.setWidget(self._nav_content)
        v.addWidget(self._nav_scroll, 1)
        # 滚动后选中条要跟着走（否则停在旧位置指向别的条目）
        self._nav_scroll.verticalScrollBar().valueChanged.connect(
            self._on_nav_scrolled)

        # 内容布局 = 组标题与条目交错的扁平序列（槽位 0..N-1，末尾 stretch）
        # ★ spacing = 0 而入每条**自带 margin**：条目间距由 QSS 的
        #   `margin-bottom` 提供，这样"条目的高度"里就包含了它下方的空隙 ——
        #   折叠动画把 maximumHeight 动到 0 时，行与空隙**一起**收干净。
        #   若用 layout spacing，折叠动画结束时总会残留下 N×spacing 的空档，
        #   收尾那一下会"啪"地跳一下（web 里 max-height 过渡不会有这问题）。
        cv = QVBoxLayout(self._nav_content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # ★ 对外契约：`_nav_btns_layout` 仍是"导航键所在的垂直布局"，
        #   `_nav_area` 仍是"按钮几何的坐标基准"。两者语义未变，只是宿主
        #   从 sideBar 换成了滚动内容 —— 拖拽仍是绝对坐标，逻辑不用改。
        self._nav_btns_layout = cv
        self._nav_area = self._nav_content

        # ---- 组标题（多组可同时展开：点标题切换本组，不影响其它组）----
        self._nav_group_headers = {}
        for grp in NAV_GROUPS:
            hd = NavGroupHeader(NAV_GROUP_TITLES[grp])
            hd.setObjectName("navGroupHeader")
            hd.clicked.connect(
                lambda _checked=False, g=grp: self._on_group_toggled(g))
            self._nav_group_headers[grp] = hd
            cv.addWidget(hd)

        self._nav_btns = {}
        self._nav_fixed_btns = {}
        self._nav_order = self._load_nav_order()
        for key in self._nav_order:
            if is_plugin_key(key):
                # 插件页键由 register_plugin_page 在插件装配时补建
                # （此时插件尚未加载）；order 里保留它的记忆位置
                continue
            btn = self._make_nav_button(NAV_PAGE_TITLES[key],
                                        NAV_PAGE_INDEX[key], key,
                                        icon_name=NAV_ICON.get(key))
            self._nav_btns[key] = btn

        # 设置（物理索引 6）：固定，不参与拖动换位
        self._settings_btn = self._make_nav_button(
            NAV_PAGE_TITLES_FIXED["settings"], 6,
            icon_name=NAV_ICON["settings"])
        self._nav_fixed_btns["settings"] = self._settings_btn
        # 软件导航按钮别名（历史引用点保留）
        self._app_launcher_btn = self._nav_btns.get("apps")

        # 使用说明按钮（与其它页面一致，参与页面切换，索引 8）
        self._help_btn = self._make_nav_button(
            NAV_PAGE_TITLES_FIXED["help"], 8,
            icon_name=NAV_ICON["help"])
        self._nav_fixed_btns["help"] = self._help_btn

        # 首次铺开：按分组序列落位 + 按当前展开集合显隐（首帧不做动画）
        self._current_expanded_groups = sanitize_expanded_groups(
            self._config.get("nav_expanded_groups", list(NAV_DEFAULT_EXPANDED)))
        self._relayout_nav(self._nav_order)
        cv.addStretch()

        # 底部版本信息（固定在滚动区之外，始终可见）
        # ★ 版本号取 app_version.APP_VERSION（单一真相源，2026-09-29 修）：
        #   此前硬编码 "v2.0"，程序都已经 4.6.0 了侧栏还显示 v2.0 ——
        #   截图里一眼可见（README 首屏图也带着这个错），属于用户可见的
        #   对外口径错误，而 app_version.py 的注释明确要求它与 CHANGELOG /
        #   Release tag 对齐，所以这里必须读常量而不是再写一遍字面量。
        ver = QLabel(f"v{APP_VERSION} · PyQt6")
        ver.setObjectName("sideBarFoot")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ver)

        # 拖拽插入位置预览条：**已废弃**（实时让位取代），保留对象仅为兼容
        # 既有验证脚本对 `_drag_indicator` 的存在性/隐藏态断言，永不显示。
        self._drag_indicator = QWidget(side)
        self._drag_indicator.setObjectName("navDragIndicatorLegacy")
        self._drag_indicator.hide()

        return side

    # ---------------- 侧栏宽度分割手柄（拖动调宽 / 双击恢复默认） ----------------
    def _build_nav_split(self) -> QWidget:
        """侧栏与内容区之间的可拖动分割手柄（外观走 theme.py 的 navSplitHandle）"""
        handle = _NavSplitHandle()
        handle.setObjectName("navSplitHandle")   # QSS 选择器靠它（漏了样式全失效）
        handle.setFixedWidth(6)
        handle.setCursor(Qt.CursorShape.SizeHorCursor)
        handle.setToolTip("拖动调整导航栏宽度；双击恢复默认")
        handle.split_pressed.connect(self._on_split_pressed)
        handle.split_moved.connect(self._on_split_moved)
        handle.split_released.connect(self._on_split_released)
        handle.split_double_clicked.connect(self._on_split_reset)
        # 拖动状态基准（press 总在 move 之前，这里是兜底初值）
        self._split_press_x = 0
        self._split_base_w = self.SIDE_BAR_WIDTH
        return handle

    def _clamped_side_width(self) -> int:
        """读配置的侧栏宽度并夹取到合法区间（缺失/非法 → 默认宽度）"""
        try:
            w = int(self._config.get("side_bar_width", self.SIDE_BAR_WIDTH))
        except (TypeError, ValueError):
            w = self.SIDE_BAR_WIDTH
        return max(self.SIDE_BAR_MIN_W, min(self.SIDE_BAR_MAX_W, w))

    def _on_split_pressed(self, global_x: int):
        self._split_press_x = global_x
        self._split_base_w = self._side_bar.width()

    def _on_split_moved(self, global_x: int):
        """拖动中：按位移实时套用新宽度（夹取在 _apply_side_width 里）"""
        self._apply_side_width(self._split_base_w + global_x - self._split_press_x)

    def _on_split_released(self):
        self._save_side_width()

    def _on_split_reset(self):
        """双击手柄：恢复默认宽度并落盘"""
        self._apply_side_width(self.SIDE_BAR_WIDTH)
        self._save_side_width()

    def _apply_side_width(self, width) -> int:
        """夹取并套用侧栏宽度（拖动实时调用）。返回夹取后的值。

        指示条贴侧栏左缘、纵向位置只随条目行高变化 —— 宽度改变不影响
        它的坐标，无需额外刷新；导航拖拽换位的槽位运算取实时几何，
        下一次拖拽自然按新宽度计算。
        """
        w = max(self.SIDE_BAR_MIN_W, min(self.SIDE_BAR_MAX_W, int(width)))
        if self._side_bar.width() != w:
            self._side_bar.setFixedWidth(w)
        return w

    def _save_side_width(self):
        """侧栏宽度落盘（与已存值相同则不写盘，省 I/O）"""
        w = self._side_bar.width()
        if self._config.get("side_bar_width", 0) != w:
            self._config.set("side_bar_width", w)
            self._config.save()

    # ---------------- 侧栏分组：展开集合 + 动画 + 组序列铺开 ----------------
    def _expanded_groups(self) -> tuple:
        """当前展开的组集合（非法/未初始化 → 回退默认）"""
        return sanitize_expanded_groups(
            getattr(self, "_current_expanded_groups", NAV_DEFAULT_EXPANDED))

    def _on_group_toggled(self, group: str):
        """点击组标题：**只切换本组**（多组可同时展开）+ 落盘 + 动画。

        ★ 与第一版手风琴的区别：这里不再"展开一个收其余"。全折叠也允许
        （再点一次已展开的组即可），空集合是合法持久化状态。
        """
        if group not in NAV_GROUPS:
            return
        new_groups = toggle_group(self._expanded_groups(), group)
        expanding = group in new_groups
        self._current_expanded_groups = new_groups
        try:
            self._config.set("nav_expanded_groups", list(new_groups))
            self._config.save()
        except Exception:      # noqa: BLE001 - 落盘失败不影响本次交互
            pass
        # 先滚动再动画：滚动目标按"动画开始前"的几何算，展开的内容只会往
        # 下长，不会把刚滚到的标题又顶出去
        if expanding:
            self._scroll_nav_group_into_view(group)
        self._animate_nav_group(group, expanding)
        # 选中页若不在展开集合里，指示条此时应隐藏（别指向看不见的条目）
        self._sync_nav_selection()

    def _scroll_nav_group_into_view(self, group: str):
        """该组标题若不在视口内，滚到视口顶部（在视口内则不动，避免乱跳）"""
        hd = self._nav_group_headers.get(group)
        bar = self._nav_scroll.verticalScrollBar()
        if hd is None or bar is None:
            return
        top = bar.value()
        vp_h = self._nav_scroll.viewport().height()
        y = hd.y()
        if y >= top and y + hd.height() <= top + vp_h:
            return          # 已完整可见
        target = y - 4
        bar.setValue(max(bar.minimum(), min(target, bar.maximum())))

    # ---- 分组展开/折叠动画（web 级 max-height 过渡 + 逐条 stagger）----
    def _nav_row_height(self) -> int:
        """单条导航行的目标高度（= QSS 里 padding+border+文字+margin-bottom）"""
        probe = next(iter(self._nav_btns.values()), None)
        h = probe.sizeHint().height() if probe is not None else 0
        if h <= 0:
            h = self._nav_slot_h or 39
        return max(1, int(h))

    def _nav_group_item_widgets(self, group: str) -> list:
        """某组当前**已建**的全部条目部件（可拖键 + 组内固定项）"""
        out = [self._nav_btns[k] for k in self._nav_btns
               if group_of(k) == group]
        for key in NAV_FIXED_ITEM_ORDER:
            if NAV_FIXED_ITEM_GROUP.get(key) == group:
                btn = self._nav_fixed_btns.get(key)
                if btn is not None:
                    out.append(btn)
        return out

    def _stop_nav_group_anims(self):
        """中断所有分组展开/折叠动画，并把条目钉在"该处于"的终态。

        中断必须落终态：动画停在半高会让布局里留下几行"半截"的按钮，
        而拖拽的槽位计算是拿实测几何做的 —— 半截行高算出来的槽位间距
        是错的。所以这里统一按当前展开集合强制刷一遍显隐。
        """
        for anim in list(self._nav_group_anims):
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._nav_group_anims = []
        self._apply_nav_visibility()

    def _stop_nav_group_anims_keep_pose(self, group: str):
        """停掉**指定组**的在飞动画，但不落终态、不刷显隐 —— 重定向起播专用。

        与 ``_stop_nav_group_anims``（拖拽/重铺布局用，必须落终态）的区别：
        这里只拆动画机，条目停在当前视觉姿态，交给随后的新动画接管；
        别的组在飞的动画不动（多组可同开，A 组重定向不该打断 B 组）。
        ``QAbstractAnimation.stop()`` 不发 finished，收尾钩子不会误触发；
        组归属用动画对象上的动态属性 ``_nav_group`` 标记（见下）。
        """
        kept = []
        for anim in list(self._nav_group_anims):
            if anim.property("_nav_group") != group:
                kept.append(anim)
                continue
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._nav_group_anims = kept

    def _animate_nav_group(self, group: str, expand: bool):
        """展开/折叠某组的条目：逐条 maximumHeight 过渡（等价 web 的 max-height）。

        为什么逐条动画、而不是给整组套一个容器：
        侧栏是**扁平布局**（组标题与条目交错），拖拽的槽位运算依赖
        ``indexOf`` 直接命中条目 —— 插入嵌套容器会让整个槽位契约崩掉。
        逐条动画在扁平布局里能得到同样的"整组长出来/收回去"的观感。

        逐条还额外送了一个 web 里很常见的 **stagger**：第 i 条延迟
        i×NAV_GROUP_STAGGER_MS 起步，收放时像"层层推开/卷起"，
        比整块同时缩放灵动得多。

        ★ 2026-10-01 丝滑度重构（离屏探针三场景取证）：
        1. 重定向代替打断：先 ``_stop_nav_group_anims_keep_pose``（本组
           在飞动画停在当前姿态、别组不受影响），再从**当前视觉高度**
           起播 —— 连续点击不再"啪"地跳回满高/0 再重放。
        2. 先压高再显形：``setMaximumHeight(start_v)`` 必须先于
           ``setVisible(True)``。反过来的话 stagger 延迟期与中断重播的
           首帧都会以"未约束满高"亮出（探针 B/C 实锤的闪变来源）。
        3. OutQuint→OutCubic + 时长收紧 280/210/18→220/170/12：39px 行高
           下 OutQuint ~105ms 就走完 97%，余下 175ms 只挪 1px，观感是
           "急起—长尾漂移"；OutCubic 220ms 的静止尾巴 <1 帧。
        """
        items = self._nav_group_item_widgets(group)
        hd = self._nav_group_headers.get(group)
        if hd is not None:
            hd.set_expanded(expand, animate=True,
                            duration=self._nav_anim_ms(NAV_ARROW_MS))
        if not items:
            return
        duration = self._nav_anim_ms(
            NAV_GROUP_EXPAND_MS if expand else NAV_GROUP_COLLAPSE_MS)
        stagger = self._nav_anim_ms(NAV_GROUP_STAGGER_MS)
        row_h = self._nav_row_height()
        # 只停本组在飞动画且不落终态（重定向；别组的动画照飞）
        self._stop_nav_group_anims_keep_pose(group)
        if duration <= 0:
            # 动画档位关闭：直接到终态（保持"0 动画"设置下的零延迟手感）
            for w in items:
                w.setMaximumHeight(16777215 if expand else 0)
                w.setVisible(expand)
            return
        parallel = QParallelAnimationGroup(self)
        parallel.setProperty("_nav_group", group)
        for i, w in enumerate(items):
            # 起播值 = 当前视觉高度：隐藏=0；可见且未约束=满行高；
            # 在飞=被动画压住的当前 maximumHeight（重定向从半途续走）
            if not w.isVisible():
                cur = 0
            else:
                mh = w.maximumHeight()
                cur = row_h if mh > row_h else int(mh)
            start_v = max(0, min(cur, row_h))
            # 动画期间必须可见（可见性不参与动画，隐藏了就看不到"长出来"），
            # 但必须先把起点钉住再显形 —— 顺序不能反，否则满高闪一帧
            w.setMaximumHeight(start_v)
            w.setVisible(True)
            prop = QPropertyAnimation(w, b"maximumHeight", self)
            prop.setDuration(duration)
            prop.setEasingCurve(QEasingCurve.Type.OutCubic)
            prop.setStartValue(start_v)
            prop.setEndValue(row_h if expand else 0)
            delay = stagger * i
            if delay > 0:
                seq = QSequentialAnimationGroup(self)
                seq.addPause(delay)
                seq.addAnimation(prop)
                parallel.addAnimation(seq)
            else:
                parallel.addAnimation(prop)
        parallel.finished.connect(
            lambda g=group, e=expand: self._on_nav_group_anim_done(g, e))
        self._nav_group_anims.append(parallel)
        parallel.start()

    def _on_nav_group_anim_done(self, group: str, expand: bool):
        """单组动画收尾：钉死显隐、清引用、刷新选中条。

        收尾必须**先**隐藏再解除高度约束：反过来会在同一帧里露出"满高"的
        条目，折叠动作末尾闪一下。
        """
        items = self._nav_group_item_widgets(group)
        for w in items:
            if expand:
                w.setMaximumHeight(16777215)
                w.setVisible(True)
            else:
                w.setVisible(False)
                w.setMaximumHeight(16777215)
        self._nav_group_anims = [a for a in self._nav_group_anims
                                 if a is not self.sender()]
        self._sync_nav_selection()

    def _nav_item_group_order(self, order=None, group=None) -> list:
        """指定组（默认=被拖拽/首个展开组）里**可拖键**的当前顺序。

        拖拽的槽位数组与这里一一对齐。★ 传 ``group`` 时按该组取——
        多组同时展开时，"当前展开组"不再唯一，拖拽必须明确知道自己
        在拖哪一组（否则会取到别的组的条目，槽位全错）。
        """
        if group is None:
            group = self._expanded_groups()[0] if self._expanded_groups() else None
        if group is None:
            return []
        pool = [k for k in (order if order is not None else self._nav_order)
                if k in self._nav_btns]
        return split_by_group(pool, pool).get(group, [])

    def _relayout_nav(self, order: list):
        """按分组把「组标题 + 条目」铺进内容布局（幂等，顺序表语义不变）。

        序列 = 依次四个组：标题，然后该组的可拖条目，然后该组的固定项
        （设置/使用说明）。隐藏组只收起条目，标题始终在 —— 否则折叠后
        用户找不到"在哪展开"。
        """
        cv = self._nav_btns_layout
        # 拖拽残留的占位 spacer 必须先摘掉，否则下面的下标全部偏移
        if self._nav_free_spacer is not None:
            cv.removeItem(self._nav_free_spacer)
            self._nav_free_spacer = None
        seq = self._nav_sequence(order)
        # 先全部摘出（含上一轮序列），再按新序列插回 —— 顺序表里键的
        # 增减都能被正确吞掉，不依赖任何"槽位约定"
        for w in self._nav_managed_widgets():
            cv.removeWidget(w)
        for i, (kind, ref) in enumerate(seq):
            cv.insertWidget(i, self._nav_widget(kind, ref))
        self._nav_layout_seq = seq
        self._nav_order = list(order)
        cv.activate()
        # 进行中的展开/折叠动画必须先落终态：动画把条目的 maximumHeight
        # 压在 0~row_h 之间，此时重排会按"半截高度"排布，而且动画还会
        # 继续改几何 → 重排结果被动画覆盖。
        # ★ 必须在 insertWidget **之后**：落终态会走 _apply_nav_visibility
        #   → setVisible(True)，此时按钮必须已带父级 —— 首次铺开时按钮
        #   还是无父级的裸控件，提前 setVisible 会把它们变成真实顶层
        #   窗口，在屏幕左上角闪现一排小黑窗（2026-09-29 启动闪窗排查）。
        self._stop_nav_group_anims()
        self._nav_free_layout = False

    def _nav_sequence(self, order: list) -> list:
        """完整铺开序列 ``[(kind, ref), ...]``：``header`` / ``item``。

        item 的 ref = ``_nav_btns`` 的键或 ``_nav_fixed_btns`` 的键；
        两类键在 ``group_of`` 里都有归属（固定项由 NAV_FIXED_ITEM_GROUP
        指定），所以这里统一按组切分，不需要分支。
        """
        pool = [k for k in order if k in self._nav_btns]
        by_group = split_by_group(pool, pool)
        seq = []
        for grp in NAV_GROUPS:
            seq.append(("header", grp))
            for key in by_group.get(grp, ()):
                seq.append(("item", key))
            for key in NAV_FIXED_ITEM_ORDER:
                if NAV_FIXED_ITEM_GROUP.get(key) == grp:
                    seq.append(("item", key))
        return seq

    def _nav_managed_widgets(self) -> list:
        """受重排管辖的全部部件（组标题 + 条目 + 组内固定项）"""
        out = list(self._nav_group_headers.values())
        out += list(self._nav_btns.values())
        out += list(self._nav_fixed_btns.values())
        return out

    def _nav_widget(self, kind: str, ref):
        """(kind, ref) → 部件：header 取组标题，item 先查可拖键再查固定项"""
        if kind == "header":
            return self._nav_group_headers[ref]
        btn = self._nav_btns.get(ref)
        return btn if btn is not None else self._nav_fixed_btns[ref]

    def _apply_nav_visibility(self):
        """按展开集合**即时**刷新显隐（无动画）——初始铺开与中断落终态用。

        隐藏用 ``setVisible(False)`` 而非移出布局：QBoxLayout 把隐藏项当空项
        跳过（不占高度），而 ``indexOf`` 仍返回真实下标 → 冻结/交还布局的
        槽位运算不必关心"折叠"。隐藏项在 ``_move_nav_indicator`` 里被跳过，
        选中条也不会指到看不见的条目上。

        同时把 ``maximumHeight`` 解除约束 —— 动画会在动画中把它压成 0~row_h
        之间的值，这里负责"回到自然高度"（否则下一次布局仍按被压的高度排）。
        """
        groups = self._expanded_groups()
        for key, btn in getattr(self, "_nav_btns", {}).items():
            btn.setMaximumHeight(16777215)
            btn.setVisible(group_of(key) in groups)
        for key, btn in getattr(self, "_nav_fixed_btns", {}).items():
            btn.setMaximumHeight(16777215)
            btn.setVisible(NAV_FIXED_ITEM_GROUP.get(key) in groups)
        for grp, hd in getattr(self, "_nav_group_headers", {}).items():
            hd.setMaximumHeight(16777215)
            hd.set_expanded(grp in groups, animate=False)
        cv = getattr(self, "_nav_btns_layout", None)
        if cv is not None:
            cv.invalidate()
            cv.activate()

    def _nav_header_index(self, group: str) -> int:
        """组标题在内容布局中的下标（-1 = 未找到）"""
        hd = self._nav_group_headers.get(group)
        if hd is None:
            return -1
        return self._nav_btns_layout.indexOf(hd)

    def _on_nav_scrolled(self, _value: int):
        """滚动条变化 → 选中条跟随（动画在这些坐标里是噪音，直接落位）"""
        if getattr(self, "_stack", None) is None:
            return      # 侧栏先于内容区构建，构建期的滚动事件直接忽略
        btn = self._nav_group.button(self._stack.currentIndex())
        if btn is not None:
            self._move_nav_indicator(btn, animate=False)


    def _apply_button_icon(self, btn, colors=None):
        """给导航按钮装／刷新自绘图标（Off 态=次要文字色，On 态=主色）。

        ★ On 态交给 Qt：``QIcon.State.On`` 的 pixmap 被 ``setCheckable(True)``
        的按钮在选中时自动取用（2026-09-30 离屏实测确认）。因此"选中变主色"
        **不需要接 toggled 信号**，也就不会出现"漏接信号导致选中后图标
        不变色"这类静默缺陷。此处只需在图标的**来源色**变了（换主题）时
        重设一次。
        """
        name = btn.property("iconName")
        if not name:
            return
        colors = colors or get_colors(getattr(self, "_theme", DEFAULT_THEME))
        btn.setIcon(icon_render.icon(
            name, NAV_ICON_SIZE,
            colors["text_secondary"], on_color=colors["primary"]))
        btn.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))

    def _apply_nav_icons(self, colors=None):
        """按主题重刷全部导航按钮图标（换主题时调用）。

        图标是 QPixmap，**QSS 换主题刷不到** —— 不显式重设的话，切到深色
        主题后图标会停在浅色主题的取色上（浅色取色落到深底上就是"看不见"）。
        """
        colors = colors or get_colors(getattr(self, "_theme", DEFAULT_THEME))
        btns = list(getattr(self, "_nav_btns", {}).values())
        btns += list(getattr(self, "_nav_fixed_btns", {}).values())
        for btn in btns:
            self._apply_button_icon(btn, colors)

    def _make_nav_button(self, text: str, page_index: int,
                         nav_key=None, icon_name=None) -> QPushButton:
        """创建导航按钮：**点击**切页；功能页按钮（nav_key 非空）可拖动换位。

        旧实现是鼠标进入按钮即切页，鼠标从侧栏横扫而过会连跳 4~5 页、
        转场动画层层叠加，观感失控。改为点击切页后，hover 只保留
        背景高亮与 2px 右移的视觉反馈。

        ``icon_name`` 非空时挂自绘图标。按钮上的 ``iconName`` 动态属性是
        图标的**唯一真相源**：换主题要重取色重绘时靠它反查该画哪个图标，
        不必另建一张 {按钮: 图标名} 平行表（平行表正是"新增按钮忘了登记
        导致图标不跟随主题"的温床）。
        """
        btn = _NavButton(text, nav_key)
        btn.setObjectName("navBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("pageIndex", page_index)
        if icon_name:
            btn.setProperty("iconName", icon_name)
            self._apply_button_icon(btn)
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
        """读取左栏显示顺序：非法（非 list/长度≠8/非 key 排列）→ 回退默认。"""
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

        ★ 2026-09-29 分组重构后：重排完全交给 ``_relayout_nav``（按分组序列
        铺开），本方法只负责"动画 + 选中态 + 落盘"这三件事。顺序表
        ``nav_order`` 仍是**一条一维顺序**，语义与落盘格式都没变。
        """
        # 中断上一轮落定动画（动画中用户再次换位 → 直接跳到终态再重排）
        self._abort_nav_settle_animations()
        # 记录重排前各按钮位置（父级坐标）；隐藏项几何是陈旧值，不参与动画
        old_pos = {key: btn.pos() for key, btn in self._nav_btns.items()
                   if btn.isVisible()}
        # order 里可能含尚未注册的插件 key（启动时序：sidebar 先建、
        # 插件装配在后）——布局只铺真正已建按钮的键；但顺序表与落盘
        # **保留完整顺序**：register_plugin_page 传进来的就是完整列表，
        # 先注册的插件不得把后注册插件的记忆位置挤掉（2026-09-28）。
        # 拖拽落定传入的是已注册键子集（拖拽只发生在已建按钮上），
        # 未注册键随之从顺序表掉出属预期——插件恢复注册时会重新入表。
        self._relayout_nav(order)
        self._sync_nav_selection()
        if save:
            self._save_nav_order(order)
        # ⚠ 窗口尚未显示（启动装配期 register_plugin_page 走到这里）时
        #   **绝不播落定动画**：此时几何未经 show/polish 校准，动画会把
        #   按钮钉在错位上，而 show 后主布局不再主动重排 → 首开导航键
        #   错乱、拖一下换位才恢复（2026-09-28 用户实测）。显示状态下
        #   重排（插件中心运行时启停/换位）才需要且才值得做动画。
        if self.isVisible():
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

        口径统一走 ``src.motion``（UI 强化方案 A1）——原先这里与
        ``card_window`` / ``tasks_panel`` 各写了一份缩放式，现已收敛到
        ``motion.duration`` 唯一实现。返回值 0 表示「不做动画」，
        调用侧据此跳过动画注册。
        """
        return max(0, motion.duration(base_ms, self.anim_speed))

    def _start_nav_settle_animations(self, old_pos: dict):
        """落定动画：位置变化的按钮从旧位置平滑滑到新位置。"""
        duration = self._nav_anim_ms(220)
        if duration <= 0:
            return
        anims = []
        for key, btn in self._nav_btns.items():
            old = old_pos.get(key)
            if old is None or not btn.isVisible():
                continue   # 折叠组的条目：无旧坐标 / 现在不可见，不参与动画
            new = btn.pos()
            if old == new:
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
            self._move_nav_indicator(btn)
            self._move_nav_indicator(btn, animate=False)

    def _switch_to_nav_slot(self, n: int):
        """Ctrl+N：切到左栏显示顺序第 N 个功能页（快捷键跟随位置）。"""
        try:
            key = self._nav_order[n]
        except (IndexError, TypeError, AttributeError):
            return
        if key not in NAV_PAGE_INDEX:
            return
        # 顺序表可能含尚未注册的插件 key（无按钮无页面），Ctrl+N 跳过
        if key.startswith("plugin:") and key not in self._nav_btns:
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
    def _freeze_nav_buttons(self, group: str) -> bool:
        """把**被拖按钮所在组**的功能页条目从布局中摘出、按当前几何自由定位。

        ★ 分组是"被拖按钮所属的组"，**不是"第一个展开组"** —— 多组可同时
        展开后，"当前展开组"不再唯一；按"第一个展开组"取会取到别的组的
        条目，槽位数组与拖拽目标错位（2026-09-29）。

        原位插一个等高 spacer 顶住垂直空间（插在该组标题之后），其它组的
        标题/条目与固定项不会被推动；按钮几何保持不变，随后由拖拽逻辑
        直接改 y 实现"空档跟着鼠标走"。

        ★ 坐标基准是 ``_nav_area``（滚动内容部件），不是视口 —— 因此
        mapFromGlobal 得到的坐标**天然与滚动偏移无关**，拖拽期间不需要
        冻结/锁定滚动条（2026-09-29 的关键简化）。
        """
        v = self._nav_btns_layout
        keys = self._nav_item_group_order(group=group)
        btns = [self._nav_btns[k] for k in keys]
        if not btns:
            return False
        geos = [QRect(b.geometry()) for b in btns]
        total = (geos[-1].y() + geos[-1].height()) - geos[0].y()
        for b in btns:
            v.removeWidget(b)
        # spacer 插在该组标题之后。下标**必须在摘除之后**重新取：隐藏项的
        # 布局项仍在计数里，摘除会改动标题之后的下标
        hdr_idx = self._nav_header_index(group)
        spacer = QSpacerItem(0, total, QSizePolicy.Policy.Fixed,
                             QSizePolicy.Policy.Fixed)
        v.insertSpacerItem((hdr_idx + 1) if hdr_idx >= 0 else 0, spacer)
        self._nav_free_spacer = spacer
        self._nav_free_layout = True
        self._nav_drag_group = group
        v.activate()
        for b, g in zip(btns, geos):
            b.setGeometry(g)
            b.show()
        self._nav_slot_ys = [g.y() for g in geos]
        self._nav_slot_h = geos[0].height()
        # 拖拽序只含展开组的已注册键（与 slot_ys 一一对齐）
        self._nav_drag_order = list(keys)
        return True

    def _restore_nav_layout(self):
        """把按钮按 ``_nav_order`` 重新铺回布局（幂等）。

        以 ``_nav_free_layout`` 为唯一状态真相：即使 spacer 已被提前移除
        （落定滑入路径会先移除它），这里也必须把按钮交还布局，否则
        设置/说明/版本号会被布局顶到上方。
        """
        if not self._nav_free_layout:
            return
        self._relayout_nav(self._nav_order)
        self._nav_drag_group = None

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
            for i, key in enumerate(
                    self._nav_item_group_order(group=self._nav_drag_group)):
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
        # 分组展开/折叠动画同理：必须停在终态，否则冻结到的槽位是动画中间值
        self._stop_nav_group_anims()
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
        # ★ 按**被拖按钮所属组**冻结，而不是"第一个展开组"：多组可同时
        #   展开后后者不再唯一，会取到别的组的条目 → 槽位全错
        if not self._freeze_nav_buttons(group_of(btn.nav_key)):
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
        group = self._nav_drag_group or group_of(btn.nav_key)
        new_group_order = list(self._nav_drag_order)
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
        # 组内拖拽**只改组内相对顺序**：其它组与未注册插件键的槽位不动
        # （reorder_within_group 的契约；集合对不上返回 None → 不落盘）
        new_order = reorder_within_group(self._nav_order, group, new_group_order)
        if new_order is None:
            self._restore_nav_layout()
            return
        if new_group_order == self._nav_item_group_order(group=group):
            # 位置未变（拖起又放回）→ 滑回原槽位，不落盘
            slot = self._nav_item_group_order(group=group).index(btn.nav_key)
            self._animate_nav_drop(btn, self._nav_slot_y(slot))
            return
        btn.move(drag_pos)
        self._apply_nav_order(new_order, save=True)
        self._nav_free_layout = False   # _apply_nav_order 已按新顺序交还布局
        self._nav_drag_group = None

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
        """把选中指示条移动到指定导航按钮的垂直中心。

        ★ 分组/滚动（2026-09-29）后新增两条守卫：
        1. 按钮属于**收起的组**（不可见）→ 隐藏指示条。否则它会停在上一处
           位置，指向一个当前页面并不在的条目，看起来像"高亮错了"。
        2. 按钮被滚出视口 → 也隐藏。指示条的父级是侧栏，不受滚动裁剪，
           不守卫的话会画到品牌字或版本号上面。
        """
        indicator = getattr(self, "_nav_indicator", None)
        if indicator is None or btn is None:
            return
        parent = indicator.parentWidget()
        if parent is None:
            return
        if not btn.isVisible():
            indicator.hide()
            return
        top_left = btn.mapTo(parent, QPoint(0, 0))
        target_y = top_left.y() + (btn.height() - indicator.height()) / 2.0
        scroll = getattr(self, "_nav_scroll", None)
        if scroll is not None:
            vp = scroll.viewport()
            vy0 = scroll.mapTo(parent, QPoint(0, 0)).y()
            vy1 = vy0 + vp.height()
            if target_y + indicator.height() < vy0 or target_y > vy1:
                indicator.hide()
                return
        indicator.show()
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

        # ★ 2026-10-01 启动丝滑化（懒加载 + show 后后台预热）：
        # 这里 10 个页面全部同步构建曾是启动最重的同步段（实测 2s+，
        # 占整个启动 ~90%）。现在只有首页(0)同步构建——它决定首屏观感；
        # 其余 9 页用空占位 QWidget 占住**物理槽位**（QStackedWidget 索引
        # 契约 0-9 + 插件页 10+ 逐字节不变），show 后由 QTimer 逐页后台
        # 构建替换（_start_lazy_warmup）；用户在预热完成前切页会被
        # _ensure_page_built 同步兜底。页属性全部 property 化：外部访问
        # （测试/宿主/信号）即触发幂等构建，行为与从前等价。
        self._lazy_builders = {
            0: self._build_fragments_page,   # 首页也走统一 ensure 路径
            1: self._build_tasks_page,
            2: self._build_notes_page,
            3: self._build_knowledge_page,
            4: self._build_assets_page,
            5: self._build_nav_page,
            6: self._build_settings_page,
            7: self._build_app_launcher_page,
            8: self._build_help_page,
            9: self._build_plugins_page,
        }
        for idx in range(10):
            if idx == 0:
                # 首页：首屏观感所在，构造期同步构建
                self._stack.addWidget(self._ensure_page_built(0))
            else:
                ph = QWidget()
                ph.setObjectName(f"lazyPagePlaceholder{idx}")
                self._stack.addWidget(ph)
                self._lazy_placeholders[idx] = ph

        # 软件导航页/任务页的信号接线挪到 _after_page_built（真页首次
        # 构建完成时执行一次，行为与原构造期接线等价）

        v.addWidget(self._stack)
        return content

    # ---------------- 页面懒加载（2026-10-01 启动丝滑化） ----------------
    def _ensure_page_built(self, index: int):
        """确保指定页已真实构建并占住 stack 槽位（幂等，返回页 widget）。

        - 已构建 → 直接返回
        - 未构建 → 调 builder 构建、记入 _real_pages、把占位 widget
          replaceWidget 换成真页（**物理索引不变**）
        页属性 property 与 _switch_page 都走这里；任何外部访问都拿到
        可用的真页，懒加载对外部完全透明。index 无 builder（如插件页
        10+，走 register_plugin_page 直建）时返回 None。
        """
        w = self._real_pages.get(index)
        if w is not None:
            return w
        builder = self._lazy_builders.get(index)
        if builder is None:
            return None
        # ★重入守卫：页构造内部可能回调宿主并再次访问页属性（如
        # TasksPanel 构造里触发 ensure(1)），此时 _real_pages[1] 尚未
        # 写入 → 无限递归。重入时返回 None，调用方以 `is not None`
        # 容忍（可见页刷新、面板刷新路径均已判空）。
        if index in self._lazy_building:
            return None
        self._lazy_building.add(index)
        try:
            w = builder()
        finally:
            self._lazy_building.discard(index)
        self._real_pages[index] = w
        ph = self._lazy_placeholders.pop(index, None)
        if ph is not None and self._stack.widget(index) is ph:
            # QStackedWidget 没有 replaceWidget（那是 QLayout 的 API），
            # 等价做法 = 先插真页到同槽位、再移除占位（index 不漂移）；
            # 并守住 currentIndex 不被插入/移除动作扰动（预热期间
            # 用户看到的页面必须纹丝不动）。
            cur = self._stack.currentWidget()
            self._stack.insertWidget(index, w)
            self._stack.removeWidget(ph)
            ph.deleteLater()
            if cur is ph:
                self._stack.setCurrentWidget(w)
            elif self._stack.currentWidget() is not cur:
                self._stack.setCurrentWidget(cur)
        self._after_page_built(index, w)
        return w

    def _after_page_built(self, index: int, w):
        """每页首次构建完成后的接线（原来写在 _build_content_area 构造期，
        懒加载后必须推迟到真页存在时执行一次）"""
        if index == 7:
            # 软件导航页面信号：启动软件后请求回到首页
            w.request_switch_to_home.connect(lambda: self._switch_page(0))
        elif index == 1:
            # 动画速度档位：初始化 + 变更时透传给任务面板（勾选动画时长缩放）
            try:
                w.set_anim_speed(self.anim_speed)
            except (AttributeError, TypeError, ValueError):
                pass
            self.anim_speed_changed.connect(w.set_anim_speed)
            # 按钮/通知等动效同步跟档（controls.set_ui_speed 单一入口，全进程生效）
            controls.set_ui_speed(self.anim_speed)
            self.anim_speed_changed.connect(controls.set_ui_speed)

    def _start_lazy_warmup(self):
        """show 后逐页后台预热：每拍构建一页并替换占位（一次性）。

        预热让「切到任何页都无感」；构建顺序 = 页码序（settings 等重页
        靠后，常用页先就绪）。单页构建是同步重活（约百 ms），放进
        QTimer 拍里让事件循环在页与页之间喘息，主窗不冻结。
        """
        if self._lazy_warm_timer is not None:
            return
        self._lazy_pending = sorted(self._lazy_placeholders)
        timer = QTimer(self)
        timer.setInterval(40)
        timer.timeout.connect(self._warm_one_page)
        timer.start()
        self._lazy_warm_timer = timer

    def _warm_one_page(self):
        if not self._lazy_pending:
            self._lazy_warm_timer.stop()
            self._lazy_warm_timer = None
            return
        idx = self._lazy_pending.pop(0)
        try:
            self._ensure_page_built(idx)
        except Exception as e:        # noqa: BLE001 - 单页预热失败不崩进程
            # PyQt6 对槽内未捕获异常默认 qFatal 中止；预热是锦上添花，
            # 失败留痕即可，页面首次真正切到时还会再走一次构建
            from src.logger import get_logger
            get_logger().warning(f"第 {idx} 页后台预热失败（切页时将重试）: {e}")

    @property
    def _page_fragments(self):
        return self._ensure_page_built(0)

    @property
    def _page_tasks(self):
        return self._ensure_page_built(1)

    @property
    def _page_notes(self):
        return self._ensure_page_built(2)

    @property
    def _page_knowledge(self):
        return self._ensure_page_built(3)

    @property
    def _page_assets(self):
        return self._ensure_page_built(4)

    @property
    def _page_nav(self):
        return self._ensure_page_built(5)

    @property
    def _page_settings(self):
        return self._ensure_page_built(6)

    @property
    def _page_app_launcher(self):
        return self._ensure_page_built(7)

    @property
    def _page_help(self):
        return self._ensure_page_built(8)

    @property
    def _page_plugins(self):
        return self._ensure_page_built(9)

    @staticmethod
    def _pump_boot():
        """主窗口构建期间泵一次事件循环。

        仅在 __init__ 构建页面的同步流程中调用：此时窗口未 show、
        无定时器、无信号连接，队列里只有绘制事件——泵帧没有重入
        风险，却能让启动闪屏（src/splash.py）的旋转动画不冻结。
        """
        QApplication.processEvents()

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

    # ---- 插件中心页面 ----
    def _build_plugins_page(self):
        """插件中心面板：展示已安装插件与使用说明（9 号页，只读展示）"""
        from src.plugins_panel import PluginsPanel
        return PluginsPanel(self)

    def set_plugin_loader(self, loader):
        """注入悬浮球插件加载器（knowledge_ball 启动时调用一次）。

        PluginsPanel 经由 plugin_loader 属性只读访问，不持强引用之外的操作。
        """
        self._plugin_loader = loader

    @property
    def plugin_loader(self):
        """插件加载器（未注入时为 None；面板按 None 渲染空态）"""
        return getattr(self, "_plugin_loader", None)

    # ---- 插件页面注入（2026-09-27：manifest.page → 主窗口导航页） ----
    def register_plugin_page(self, key: str, title: str, widget) -> int:
        """注册插件提供的导航页面（knowledge_ball 对页面插件调用）。

        - 物理索引 = QStackedWidget 追加到末尾（0-9 为固定页，插件页 10+）
        - 侧栏按钮**参与拖拽换位**（2026-09-28 用户要求：AI 助手先行，
          后续所有页面插件一致）：顺序记进 ``nav_order`` 持久化，新插件
          默认排在末位（设置之前），重启后沿用用户调整过的位置
        - **不写入** ``last_page_index``（config 范围上限 9）
        - **幂等**：同一 key 重复注册（插件中心「重新扫描」会重跑装配）
          只替换页面内容，索引与按钮保持稳定，旧页面被安全销毁
        返回物理索引。
        """
        # ★ 2026-09-30（UI 强化 A2）：插件 manifest 的标题常自带 emoji 前缀
        #   （"🤖 AI 助手"）。图标改由宿主自绘后，这类前缀不但多余，还会因
        #   系统 emoji 字体缺失退化成豆腐块 —— 统一在这里剥掉。
        # ★ 2026-10-02（P5 插件图标）：出厂五插件改挂专属图标——icons 表
        #   PLUGIN_ICONS 按插件 id 映射（plugin_page_icon 消费）。宿主仍不
        #   认识任意插件的语义，映射查不到（第三方插件 / 未收录的 id）一律
        #   落回通用占位 PLUGIN_PAGE_ICON，兜底口径与占位时代完全一致。
        title = strip_leading_emoji(title)
        old_index = NAV_PAGE_INDEX.get(key)
        # ⚠ 幂等判定必须以「本实例已持有该键的按钮」为准：
        #   NAV_PAGE_INDEX 是模块级 dict，跨 MainWindow 实例共享（测试 /
        #   多窗口场景会建第二个实例）——只看 old_index 会让新实例误判
        #   「已注册」，只换 widget 不建按钮，插件导航键永远出不来。
        if old_index is not None and key in self._nav_btns:
            old = self._stack.widget(old_index)
            if old is not None:
                self._stack.removeWidget(old)
                old.deleteLater()
            self._stack.insertWidget(old_index, widget)
            # 侧栏按钮已存在：只更新文案
            # ⚠ 原实现写的是 ``for b, k in self._nav_btns.items():
            #   if k == key: b.setText(...)`` —— _nav_btns 是 {key: btn}，
            #   这样 unpack 出来 b 是**键**、k 是按钮，``k == key`` 恒为
            #   False，文案更新静默失效（"重复注册只换 widget 不换索引"
            #   的契约里漏了这一半）。2026-09-29 由离屏脚本 K 组断言抓出。
            NAV_PAGE_TITLES[key] = title
            old_btn = self._nav_btns.get(key)
            if old_btn is not None:
                old_btn.setText(title)
            return old_index

        index = self._stack.count()
        NAV_PAGE_TITLES[key] = title
        NAV_PAGE_INDEX[key] = index
        self._stack.addWidget(widget)
        btn = self._make_nav_button(title, index, nav_key=key,   # 参与换位
                                    icon_name=plugin_page_icon(key))
        self._nav_btns[key] = btn
        # 顺序：config 里记过位置（上次会话拖过）→ 沿用；新插件 → 追加
        # 到末位（设置之前）。_apply_nav_order 统一重排并交还布局。
        if key not in self._nav_order:
            self._nav_order.append(key)
        self._apply_nav_order(list(self._nav_order), save=True)
        return index

    def show_plugin_page(self, key: str) -> bool:
        """打开主窗口并切到插件页面（插件热键动作经 ctx.parent_window()
        调用的**约定公开入口**，与 show_xxx_page 系列同款行为）。

        key = 注册时的 ``plugin:<插件id>``；未知 key 返回 False。
        """
        idx = NAV_PAGE_INDEX.get(key)
        if idx is None or idx < 10:      # 只允许切到插件页段
            return False
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(idx)
        return True

    def unregister_plugin_page(self, key: str) -> bool:
        """注销插件提供的导航页面（停用 / 卸载页面插件时由宿主调用）。

        与 register_plugin_page 成对。三处停用路径——插件中心启停开关、
        启动回放 ``plugins_disabled``、总闸关闭——页面此前都无人摘除，
        导致「停用了导航键和页面还挂在主窗口」；本方法补齐注销环节。

        索引策略：**空占位补槽**而非直接 removeWidget——QStackedWidget
        移除中间页会把后续索引整体前移，连坐其他插件页的
        ``NAV_PAGE_INDEX`` 与 ``_nav_group`` 按钮 id 映射；占位页无按钮
        不可达，成本可忽略。注销后同步清除两个模块级 dict 项，重新启用
        时 register_plugin_page 走全新注册分支（新索引、新按钮）。
        正在显示该页时先切回首页。key 未注册返回 False（可安全重入）。
        """
        idx = NAV_PAGE_INDEX.pop(key, None)
        if idx is None or idx < 10:      # 未知 key；固定页段防御性回滚
            if idx is not None:
                NAV_PAGE_INDEX[key] = idx
            return False
        NAV_PAGE_TITLES.pop(key, None)
        if self._stack.currentIndex() == idx:
            self._switch_page(0)
        old = self._stack.widget(idx)
        if old is not None:
            self._stack.removeWidget(old)
            self._stack.insertWidget(idx, QWidget())   # 占位：索引全稳定
            old.deleteLater()
        btn = self._nav_btns.pop(key, None)
        if btn is not None:
            self._nav_group.removeButton(btn)
            self._nav_btns_layout.removeWidget(btn)
            btn.deleteLater()
        # 顺序表同步移除并重排落盘（插件键参与换位后它是 order 成员，
        # 留着会让 _apply_nav_order 引用已删除的按钮）
        if key in self._nav_order:
            self._nav_order.remove(key)
            self._apply_nav_order(list(self._nav_order), save=True)
        return True

    def rebuild_plugin_page(self, plugin_id: str) -> bool:
        """重新注册指定插件的页面（插件中心重新启用某插件时调用）。

        页面实例由插件自己的 create_page 产生（与 knowledge_ball 启动装配
        同一契约），这里只负责「要页面 → 塞回主窗口」。插件未加载、未声明
        page 或 create_page 失败一律返回 False，绝不把插件问题抛进 UI 操作。
        register_plugin_page 幂等，重复重建只换 widget 不换索引。
        """
        loader = self.plugin_loader
        if loader is None:
            return False
        for lp in loader.loaded_plugins():
            if lp.plugin_id != plugin_id:
                continue
            spec = getattr(lp, "manifest", {}).get("page")
            if not spec:
                return False
            try:
                widget = lp.plugin.create_page(lp.ctx)
            except Exception:              # noqa: BLE001 - 页面失败不拖垮 UI
                return False
            if widget is None:
                return False
            key = f"plugin:{plugin_id}"
            self.register_plugin_page(key, spec["title"], widget)
            try:
                lp.page_key = key          # 热键动作经 parent_window 切页用
            except Exception:              # noqa: BLE001 - 辅助标记，失败无害
                pass
            return True
        return False

    # ---- 设置业务方法 ----
    # ==================================================================
    # 页面切换
    # ==================================================================
    def _switch_page(self, index: int):
        """切换到指定页面，并刷新对应面板数据"""
        # 懒加载兜底：后台预热没轮到该页时（用户提前切页）同步构建真页；
        # 插件页 10+ 无 builder，此处为无害空操作
        self._ensure_page_built(index)
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

        - 说明页（索引 HELP_PAGE_INDEX=8）不记录，避免下次启动直接落在说明页
        - 插件页（物理索引 > LAST_PAGE_INDEX_MAX）不记录：config 的
          ``_CONFIG_RANGES`` 对 last_page_index 有上限校验，越界值会被
          静默丢弃——显式跳过，避免每次切页都做一次注定失败的写盘
        - 值未变化时不写盘，避免频繁 I/O
        """
        if index == HELP_PAGE_INDEX or index > LAST_PAGE_INDEX_MAX:
            return
        try:
            if self._config.get("last_page_index", 0) != index:
                self._config.set("last_page_index", int(index))
                self._config.save()
        except Exception:
            pass

    def _initial_page_index(self) -> int:
        """计算启动时应打开的页面索引。

        - 开启「启动时恢复上次页面」→ 返回上次浏览的页面
          （0..LAST_PAGE_INDEX_MAX，越界/说明页回退首页）
        - 未开启（默认）→ 返回首页（碎片工作台）

        范围上限取自 config.LAST_PAGE_INDEX_MAX，与写入侧的
        ``_CONFIG_RANGES`` 同源——此前两处各自写死 7，页面增加到 9 后
        插件中心既存不下也恢复不了。
        """
        if self._config.get("restore_last_page", False):
            idx = self._config.get("last_page_index", 0)
            if (isinstance(idx, int) and 0 <= idx <= LAST_PAGE_INDEX_MAX
                    and idx != HELP_PAGE_INDEX):
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
        elif index == 9:
            # 插件中心：切页走指纹守卫刷新——数据没变就零重建。
            # 此前每次切页都整页重建卡片（全窗最大面积重绘），在
            # WA_TranslucentBackground 半透明壳下是 DWM 黑帧闪现的
            # 触发面（用户报障：点插件中心导航键闪黑窗）。
            # 数据变化（启停/安装/卸载/重扫）由面板内部调用全量 refresh()。
            panel = self._page_plugins
            if panel is not None and hasattr(panel, "refresh_if_stale"):
                panel.refresh_if_stale()
            elif panel is not None and hasattr(panel, "refresh"):
                panel.refresh()

    # ==================================================================
    # 公开接口：供外部调用刷新指定面板
    # ==================================================================
    def refresh_page(self, name: str):
        """按面板名刷新对应面板（仅当该面板当前可见时刷新）。

        面板名：fragments / tasks / notes / knowledge / assets / nav /
        settings / plugins
        """
        index = {
            "fragments": 0, "tasks": 1, "notes": 2, "knowledge": 3,
            "assets": 4, "nav": 5, "settings": 6, "plugins": 9,
        }.get(name)
        if index is None or self._stack.currentIndex() != index:
            return
        # 所有面板已抽离为独立 panel，统一调用其 refresh()
        # ★按需访问页属性（懒加载）：此处若用字典字面量会把全部页
        # property 一口气求值 → 构造期强推全页构建 → builder 内部
        # 再触发 ensure 形成 8 层互相递归。守卫已保证只有可见页
        # 才走到这里，可见页必然已构建（_switch_page 入口先 ensure）。
        if name == "fragments":
            panel = self._page_fragments
        elif name == "tasks":
            panel = self._page_tasks
        elif name == "notes":
            panel = self._page_notes
        elif name == "knowledge":
            panel = self._page_knowledge
        elif name == "assets":
            panel = self._page_assets
        elif name == "nav":
            panel = self._page_nav
        elif name == "settings":
            panel = self._page_settings
        else:
            panel = self._page_plugins
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

    # ---------------- 公开 API（D3 穿透清零 2026-09-30）----------------
    # 宿主（knowledge_ball）此前直接戳 _switch_page / _refresh_page /
    # _allow_close / _apply_theme / _page_notes 私有成员，
    # 这里升为公开方法/特性，私有成员保持不动。
    def show_page(self, index: int):
        """公开入口：切换到指定页面并刷新对应面板（托盘气泡点击等外部调用）。

        与 show_plugins_page 同族；索引含义见 _refresh_page 的页面表。
        """
        self._switch_page(index)

    def refresh_apps_page(self):
        """公开入口：软件导航页重载配置并刷新 UI（拖入 exe/lnk 收藏后调用）。

        注意语义与 refresh_page(name) 不同：不受"页面当前可见"限制，
        无条件重载——与原内部 _refresh_page(7) 逐字等价。
        """
        self._refresh_page(7)

    def flush_pending_notes(self):
        """公开入口：落盘未决的笔记编辑（退出前调用，兜自动保存防抖窗口）。

        笔记面板缺失时静默跳过（与退出路径的 getattr 容错一致）。
        """
        panel = getattr(self, '_page_notes', None)
        if panel is not None:
            panel.flush_pending_save()

    def reapply_theme(self):
        """公开入口：按当前主题重新应用 QSS/配色（跟随系统深浅色变化时调用）。

        不写配置、不广播 theme_changed——广播时机由调用方决定，
        语义与原内部 _apply_theme() 逐字等价。
        """
        self._apply_theme()

    def refresh_appearance(self):
        """强调色 / 壁纸改动后的统一刷新（设置面板调用）。

        与 :meth:`reapply_theme` 的区别：那里刻意不广播（广播时机由调用
        方决定），而改了强调色必须广播 —— 悬浮球、小卡片、右键菜单的
        配色都是各自监听 ``theme_changed`` 后再 ``get_colors()`` 取的，
        不广播它们会整批停在旧强调色上。
        """
        self._apply_theme()
        self.theme_changed.emit(self._theme)

    @property
    def allow_close(self) -> bool:
        """是否允许下一次 closeEvent 直接关闭（程序主动退出路径用）。

        宿主 _safe_quit 此前直接写 `_allow_close` 私有成员，升为特性。
        """
        return self._allow_close

    @allow_close.setter
    def allow_close(self, value: bool):
        self._allow_close = bool(value)


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

    def show_settings_page(self, category=None):
        """跳转设置页；category 可选，直接落到设置页内部分类
        （SettingsPanel.SETTINGS_CATEGORIES 的 key，非法值由其自行忽略）"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(6)
        if category is not None:
            panel = getattr(self, "_page_settings", None)
            if panel is not None:
                panel.show_category(category)

    def show_plugins_page(self):
        """打开并跳转到插件中心（补齐其余面板都有的 show_xxx_page 入口）。

        用 NAV_PAGE_INDEX 查表而非写死索引——插件页物理索引 9 与「设置页 6」
        不连续，写死数字最容易在页面增删时埋雷。
        """
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(NAV_PAGE_INDEX["plugins"])

    # ==================================================================
    # 主题系统
    # ==================================================================
    def _apply_theme(self):
        """应用当前主题的 QSS + 玻璃壳配色"""
        # 主题扩展（2026-10-03）：**必须排在第一次 get_colors 之前** ——
        # sync 会把 accent 推进 theme 模块，之后所有取色（含下面的 QSS）
        # 才带得上新强调色；顺序反了就会用到上一次的值。
        bg_spec = appearance.sync_theme_extras(self._config)
        colors = get_colors(self._theme)
        # 自定义背景图挂在玻璃壳的绘制管线里（未配置时是一次布尔判断的开销）
        if isinstance(self._container, GlassPanel):
            self._container.set_background(
                bg_spec["path"], bg_spec["mode"], bg_spec["opacity"],
                bg_spec["blur"], bg_spec["veil"],
                colors.get("bg", "#FAFAF8"))
        qss = get_main_window_qss(self._theme)
        self._container.setStyleSheet(qss)
        # 玻璃壳（填充/描边/高光/噪点）由 GlassPanel 手绘，需同步配色
        if isinstance(self._container, GlassPanel):
            self._container.apply_theme(colors)
        self._sync_nav_indicator_color(colors)
        # 导航图标由 QPixmap 承载，同样**不在 QSS 的管辖范围**里 —— 不显式
        # 重设的话，换到深色主题后图标会停在浅色主题的取色上（浅色落到
        # 深底上就是"看不见"）。与碎片列表代理、知识库警告文字同一类坑。
        self._apply_nav_icons(colors)
        # 更新主题切换按钮图标（🌙/☀️ 一钮两态）
        self._theme_btn.set_icon_name("sun" if self._theme == "dark" else "moon")
        # P1 动作图标按钮的位图颜色同样不在 QSS 管辖内 —— 全窗一网打尽统一
        # 重取色（各面板/设置页/插件页的 IconButton 都在这棵控件树下；
        # 标题栏几个虽已订阅 theme_changed，重复 apply 一次是无害幂等）。
        for btn in self.findChildren(IconButton):
            btn.apply_theme(self._theme)
        # 更新右键菜单样式
        if hasattr(self, '_menu'):
            self._menu.setStyleSheet(qss)
        # 同步更新软件导航页面的主题（懒加载：只刷已构建页——未构建页
        # 之后构建时自会用当前主题，无需提前触发构建）
        if 7 in self._real_pages:
            self._real_pages[7]._theme = self._theme
            self._real_pages[7]._apply_style()
        # 使用说明页正文颜色跟随主题
        self._apply_help_content_color()
        # 任务面板行委托为自绘，配色需显式同步（QSS 无法覆盖 delegate 绘制）
        if 1 in self._real_pages:
            self._real_pages[1].apply_theme()
        # 碎片列表同理：代理字色 + 条目前景色（日期行/路径行）都是创建时
        # 取色烘进 item 的，QSS 刷不到 —— 不显式同步的话切主题后列表停在
        # 旧配色（深色字落在浅色底上几乎不可见），要切一次页面才恢复。
        if 0 in self._real_pages:
            self._real_pages[0].apply_theme()
        # 知识库面板：只有"检测到外部修改"警告文字是 inline stylesheet 着色
        if 3 in self._real_pages:
            self._real_pages[3].apply_theme()
        # 设置页的自绘开关（ToggleSwitch）与主题按钮也要跟随主题
        if 6 in self._real_pages:
            self._real_pages[6].apply_theme()
        # 素材网格的 delegate 是动态取色，重绘即可跟随主题
        if 4 in self._real_pages:
            self._real_pages[4].apply_theme()

    def _sync_nav_indicator_color(self, colors=None):
        """同步导航指示条与**分组箭头**的颜色（主题切换时调用）。

        箭头是自绘的（NavArrow），不在 QSS 覆盖范围内 —— 换主题时若不
        显式同步，浅色主题下会留一枚深色三角（反之亦然）。
        """
        if not hasattr(self, '_nav_indicator'):
            return
        colors = colors if colors is not None else get_colors(self._theme)
        self._nav_indicator.set_color(QColor(colors["primary"]))
        for hd in getattr(self, "_nav_group_headers", {}).values():
            hd.set_arrow_color(QColor(colors["text_placeholder"]),
                               QColor(colors["text_secondary"]))

    def _toggle_theme(self):
        """切换浅色/深色主题。

        「跟随系统」下**不落盘**（成熟化路线图遗留备忘的修复）：Ctrl+T
        只把本会话翻到相反主题，config 仍是 "follow"，重启后回到跟随——
        否则一按快捷键就把跟随偏好静默固化成显式主题，与功能语义相悖。
        落盘与否 + 下一站是哪个主题，判定收敛在
        :func:`theme.next_theme_on_toggle`（纯逻辑，可脱离 GUI 单测）。
        """
        try:
            config_value = self._config.get("theme", DEFAULT_THEME)
        except Exception:                         # noqa: BLE001 - 展示层兜底
            config_value = DEFAULT_THEME
        persist, applied = next_theme_on_toggle(config_value, self._theme)
        self._theme = applied
        if persist:
            self._config.set("theme", applied)
            self._config.save()
        self._apply_theme()
        # 通知外部（悬浮球、小卡片）刷新主题
        self.theme_changed.emit(applied)
        if not persist:
            # 不落盘的会话翻转必须说破，否则用户以为已永久切换
            self.show_toast("已临时切换为%s主题（仍跟随系统，重启后恢复）"
                            % ("深色" if applied == "dark" else "浅色"))

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

        供任务面板等子控件按 ``motion.duration(CHECK_ANIM_MS, anim_speed)``
        缩放动画时长，使勾选动画与悬浮球动画速度档位一致。
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
        """使用说明富文本源稿（与各页面功能保持同步更新）。

        2026-10-02 观感升级：项目符号从「<p> 里手写 • + <br> 换行」改为
        真列表（<ul>/<li>，Qt 原生缩进与圆点），热键写 [[键位]] 标记、
        渲染时由 _decorate_help_html 转成等宽键帽——源稿保持纯静态，
        主题色一律运行时注入。
        2026-10-02 内容补充：新增「截图钉屏 / 插件中心 / 内置插件」三章
        （此前这三个功能整块没有文档）；悬浮球右键菜单、日程任务
        「专注此任务」、笔记「钉到桌面」按实际界面补齐。
        2026-10-03 内容形态升级：按每章内容的性质选用六种信息形态——
        导语（PHCOLOR 灰字定位句）、步骤 <ol>、并列 <ul>、热键两列表、
        提示块（bgcolor=TIPBG 通栏底色 + PRIMCOLOR 前缀）、色块行与
        等宽路径（DANGERCOLOR/WARNCOLOR/PHCOLOR）；主题色一律写成
        固定占位 token，渲染时由 _decorate_help_html replace 成主题
        真值，源稿保持纯静态。16 个 <h3> 标题与各章锚点文案逐字不动
        （test_help_nav 钉两表一致与切片语义）。
        """
        return """
        <p>FloatPulse 是一款常驻桌面的悬浮球效率工具：所有碎片、任务、笔记、素材都保存在本机，不联网、不登录。</p>
        <p><b>三步上手</b>：</p>
        <ol>
        <li>正常复制文字或文件 → 碎片工作台<b>自动入库</b>，随手翻随手取；</li>
        <li>把图片或文件<b>拖到悬浮球上</b> → 收进「临时素材」；</li>
        <li>有截止日期的事 → 「日程任务」添加一条，到期托盘会提醒。</li>
        </ol>
        <p>左栏点分类直达对应页面；[[F1]] 随时回到本页。</p>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　本页不参与「记住上次页面」——从说明页退出总是回到进入前的那个分类。</td></tr></table>

        <h3>全局快捷键</h3>
        <table width="100%">
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Esc]]</td><td style="padding:4px 0;">退出程序（主窗口与小卡片中都生效）</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Ctrl+W]] / [[Ctrl+H]]</td><td style="padding:4px 0;">隐藏主窗口（程序继续在托盘后台运行）</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Ctrl+T]]</td><td style="padding:4px 0;">切换浅色 / 深色主题</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Ctrl+K]]</td><td style="padding:4px 0;">站内搜索（知识库 / 笔记 / 碎片 / 任务 / 素材，点结果标题跳转到对应面板）</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[F1]]</td><td style="padding:4px 0;">进入使用说明页；再按一次返回进入前的页面</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Ctrl+1]] ~ [[Ctrl+8]]</td><td style="padding:4px 0;">依次切换到左栏第 1~8 个功能页（含插件中心）</td></tr>
        <tr><td width="31%" style="white-space:nowrap; padding:4px 14px 4px 0;">[[Ctrl+Alt+S]]</td><td style="padding:4px 0;">截图钉屏——框选屏幕区域，松开即生成置顶参考浮窗（详见「截图钉屏」分类）</td></tr>
        </table>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　截图钉屏的热键与开关可在「设置 → 全局工具」修改；其它全局键为固定键位。</td></tr></table>

        <h3>悬浮球</h3>
        <p style="color:PHCOLOR; font-size:12px;">桌面角落的那颗球：看卡、翻卡、收文件、跑番茄钟，全在球上完成。</p>
        <ul>
        <li><b>鼠标悬停</b>：自动弹出小卡片（首次随机抽一张知识卡，之后恢复上次离开时的页签）</li>
        <li><b>左键点击</b>：卡片已弹出 → 切到下一张；未弹出 → 随机抽一张并弹出</li>
        <li><b>滚轮</b>：上一张 / 下一张顺序翻卡，手不用离开球</li>
        <li><b>拖拽</b>：按住左键拖动位置，松手自动吸附到最近的屏幕边缘</li>
        <li><b>空闲自动隐藏</b>：贴边静止若干秒后半隐藏到边缘，鼠标移近自动滑出（秒数见设置）</li>
        <li><b>拖文件或图片到球上</b>：自动复制收录到「临时素材」</li>
        <li><b>番茄钟</b>：设置开启后球体外圈显示进度环，右键菜单可开始 / 暂停 / 结束（参数见「设置 → 番茄钟」）</li>
        <li><b>右键菜单</b>：三段式——打开主窗口 / 小卡片 ▸（7 个模式直达）；番茄钟控制、截图钉屏、插件功能 ▸；退出程序</li>
        <li><b>尺寸与外观</b>：球体即应用图标本体，大小与动画速度都在设置里调节</li>
        </ul>

        <h3>小卡片</h3>
        <p style="color:PHCOLOR; font-size:12px;">悬浮球弹出的七页迷你面板：轻量看内容、随手记，编辑仍回主窗口。</p>
        <ul>
        <li><b>七个页签</b>（左侧竖排图标）：碎片 / 知识卡片 / 日程任务 / 临时笔记 / 网址导航 / 临时素材 / 软件导航</li>
        <li><b>弹出与收起</b>：悬停球自动弹出；鼠标移出「球 + 卡片」区域后自动收起（勾选设置里的「小卡片保持显示」可常驻）</li>
        <li><b>拖动卡片</b>：在顶部空白条或内容空白处按住左键拖动整张卡片，悬浮球同步跟随、保持相对位置</li>
        <li><b>模式记忆</b>：收起时所处的页签会被记住，下次弹出直接回到该页签</li>
        <li><b>临时笔记</b>：停止输入 800ms 自动保存，关闭也不会丢字</li>
        <li><b>日程任务</b>：可直接勾选完成，右键菜单编辑 / 删除</li>
        <li><b>网址导航 / 软件导航 / 素材</b>：卡片里可直接打开，编辑仍在主窗口</li>
        <li><b>右键卡片空白处</b>：退出程序</li>
        </ul>

        <h3>碎片工作台</h3>
        <p style="color:PHCOLOR; font-size:12px;">本机的「随手存」池子：复制即入库，需要时搜出来、合起来、存成笔记。</p>
        <ul>
        <li><b>自动收集</b>：复制文本、复制文件路径时自动入库</li>
        <li><b>类型筛选</b>：全部类型 / 剪贴板文本 / 剪贴板路径 / 文件拾取 / 知识段落</li>
        <li><b>搜索</b>：输入即筛（去抖 250ms），命中的关键词在条目里高亮</li>
        <li><b>预览</b>：选中左侧条目，右侧显示完整内容，可直接修改（停止输入 800ms 自动保存），也可「复制」</li>
        <li><b>多选批量</b>：勾选多条后可「合并选中」（可合并成一条并直接存为笔记）、「复制选中」、「删除选中」、「清空全部」</li>
        <li><b>右键单条</b>：查看详情 / 编辑内容 / 复制内容 / 存为笔记 / 加入知识库 / 添加至网址导航 / 删除</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　个别应用里复制不生效？它可能被过滤了——过滤名单在「设置 → 剪贴板与碎片」。</td></tr></table>

        <h3>日程任务</h3>
        <p style="color:PHCOLOR; font-size:12px;">按截止日自动分组的事清单：逾期红、今日橙，到期托盘提醒。</p>
        <p><b>新增一条任务</b>：</p>
        <ol>
        <li>填写任务标题；</li>
        <li>点日期框弹出日历选截止日期（周一开头，点月标题右侧 ▼ 可直选月份；「清除」= 无日期，「今天」= 回今天）；</li>
        <li>点「添加」。</li>
        </ol>
        <p><b>分组与颜色</b>：</p>
        <ul>
        <li><b>分组顺序</b>：逾期 → 今天 → 本周（明天 ~ 本周日）→ 以后 → 无日期 → 已完成</li>
        <li><b>颜色</b>：<span style="background-color:DANGERCOLOR; font-size:11px;">&nbsp;&nbsp;&nbsp;&nbsp;</span>红色已逾期　<span style="background-color:WARNCOLOR; font-size:11px;">&nbsp;&nbsp;&nbsp;&nbsp;</span>橙色今日到期　<span style="background-color:PHCOLOR; font-size:11px;">&nbsp;&nbsp;&nbsp;&nbsp;</span>灰色已完成</li>
        <li><b>行尾文案</b>：未完成显示相对截止（今天 / 明天 / 逾期N天 / M月D日（周X））；<b>已完成只显示完成日期</b>，不再显示逾期</li>
        <li><b>批量操作</b>：「批量完成」「批量删除」「清除已完成」</li>
        <li><b>右键单条</b>：标记完成 / 取消完成 / 编辑 / 删除；「专注此任务」会让悬浮球进度环开始绑定式专注（番茄钟）</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　到期提醒在程序启动后与每日 9:00 通过托盘气泡提示，可在设置中关闭。</td></tr></table>

        <h3>笔记管理</h3>
        <p style="color:PHCOLOR; font-size:12px;">带自动保存的本地笔记：停止输入 800ms 即落盘，还能钉成桌面便签。</p>
        <ul>
        <li><b>新建 / 删除</b>：「新建笔记」「删除当前」</li>
        <li><b>列表</b>：只显示标题；鼠标悬停可看到 标题 + 修改时间 + 内容预览</li>
        <li><b>改名</b>：双击列表项或按 F2 就地改名</li>
        <li><b>自动保存</b>：停止输入 800ms 落盘；退出程序前会强制保存未落盘的改动</li>
        <li><b>状态栏</b>：显示「刚刚 / N 分钟前 / 3 小时前 / 昨天 HH:MM / N 天前」，有未保存改动时前面加「● 未保存」</li>
        <li><b>搜索</b>：匹配标题与内容；若正在编辑的笔记被搜索条件过滤掉，状态栏会明确提示</li>
        <li><b>桌面便签</b>：右键笔记 → 钉到桌面，把当前笔记钉成常驻桌面的便签（同时有数量上限，可从托盘唤回管理）</li>
        <li><b>右键</b>：编辑标题… / 删除此笔记</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　按内容自动生成的标题会随内容更新；手动改过的标题不再自动变——右键菜单可随时切换「标题跟随内容」/「锁定标题（不随内容更新）」。</td></tr></table>

        <h3>知识库</h3>
        <p style="color:PHCOLOR; font-size:12px;">一本 Word 文档就是卡片库：外部编辑，程序里看卡，改完重载即生效。</p>
        <p><b>外部编辑流程</b>：</p>
        <ol>
        <li>用 Word/WPS 打开程序目录 <span style="font-family:Consolas,'Courier New',monospace">float_data/</span> 内的「知识库.docx」直接修改（源码运行放项目根 float_data/ 内，打包后放 exe 同目录 float_data/ 内）；</li>
        <li>保存文档；</li>
        <li>面板提示「检测到外部修改，建议重新加载」时点「重新加载」即生效（程序启动时也会自动检测）。</li>
        </ol>
        <ul>
        <li><b>卡片粒度</b>：每个非空段落（去空格后 ≥ 4 字）就是一张知识卡片，过短段落自动忽略</li>
        <li><b>重新加载</b>：点「重新加载」重新读取 docx</li>
        <li><b>新增内容</b>：「新增知识」追加到 docx 末尾；「加入碎片池」把选中段落送进碎片工作台</li>
        <li><b>右键段落</b>：编辑 / 删除 / 在此后新增 / 加入碎片池（删除与重新加载有玻璃风格确认框）</li>
        <li><b>搜索</b>：输入去抖 250ms 实时过滤段落，无结果时显示占位提示；双击段落可直接编辑</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">注意</b>　「知识库.docx」文件名固定，改名或移走会导致知识卡片无内容（程序仍可运行）；<span style="font-family:Consolas,'Courier New',monospace">float_data/docx_meta.json</span> 是程序自动维护的指纹缓存，请勿手工编辑。</td></tr></table>

        <h3>临时素材</h3>
        <p style="color:PHCOLOR; font-size:12px;">拖进来的图片和文件的落脚点：有上限、有保质期，到点自动清理。</p>
        <ul>
        <li><b>收录方式</b>：拖图片 / 文件到悬浮球；复制图片到剪贴板（Excel、Word 一类图文混排仍按文本收集）</li>
        <li><b>打开</b>：双击用系统默认程序打开；右键 打开 / 另存为 / 删除</li>
        <li><b>批量</b>：「打开素材文件夹」「刷新」「清空全部」</li>
        <li><b>容量</b>：条数上限与保留天数在「设置 → 临时素材上限 / 素材保留天数」调整</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　保留天数填 0 表示不按天数清理，只受条数上限约束。</td></tr></table>

        <h3>网址导航</h3>
        <p style="color:PHCOLOR; font-size:12px;">常用的网址一格一站，碎片里的链接也能一键收进来。</p>
        <ul>
        <li><b>添加站点</b>：填名称 + URL，地址会自动补全 http:// 或 https://</li>
        <li><b>整理</b>：拖拽行可排序；右键站点：打开 / 编辑 / 删除</li>
        <li><b>一键收集</b>：碎片工作台里 URL 类碎片右键 →「添加至网址导航」</li>
        <li><b>卡片入口</b>：小卡片的「网址导航」页签可直接点开，编辑仍在主窗口</li>
        </ul>

        <h3>软件导航</h3>
        <p style="color:PHCOLOR; font-size:12px;">常用软件的一键启动台：点卡片即启动，图标尺寸随你调。</p>
        <ul>
        <li><b>启动</b>：左键点击卡片即用系统默认方式启动对应的 exe（需要管理员权限的程序会提示提权启动）</li>
        <li><b>管理</b>：点「管理软件列表」新增 / 编辑 / 删除条目，可填 名称、exe 路径、图标（.ico / .png）、备注</li>
        <li><b>卡片尺寸</b>：「设置 → 软件卡片尺寸」调节（60 ~ 140px，实时生效）</li>
        <li><b>小卡片</b>：卡片的「软件导航」页签是同一份列表，可直接启动</li>
        </ul>

        <h3>截图钉屏</h3>
        <p style="color:PHCOLOR; font-size:12px;">把屏幕一角「钉」在最上面当参考图：对照着抄内容、比尺寸都方便。</p>
        <p><b>钉一张参考图</b>：</p>
        <ol>
        <li>按 [[Ctrl+Alt+S]]（可在设置 → 全局工具改键 / 关闭，或右键悬浮球 → 截图钉屏）；</li>
        <li>全屏变暗进入框选，拖选要钉住的范围；</li>
        <li>松开即钉屏，生成置顶参考浮窗。</li>
        </ol>
        <ul>
        <li><b>钉图浮窗</b>：置顶参考窗——左键拖动移动位置，右下角抓手等比例改窗框大小，双击关闭</li>
        <li><b>批注</b>：画笔 / 箭头 / 马赛克，[[Ctrl+Z]] 撤销；复制 / 保存时自动合成批注</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　Esc 关闭钉图——钉图期间全局 Esc 已被拦截，只关钉图，不会误退整个程序；框选阶段 Esc 或右键 = 取消。</td></tr></table>

        <h3>插件中心</h3>
        <p style="color:PHCOLOR; font-size:12px;">装插件、管插件的地方；商店与本地 .fpplug 都从这进。</p>
        <ul>
        <li><b>入口</b>：左栏「插件中心」（[[Ctrl+8]]）</li>
        <li><b>本地插件</b>：每张卡显示版本 / 描述 / 能力 / 动作热键，可启用 / 停用、卸载；插件多时可用顶部搜索框（按插件名 / 动作名）与「全部 / 已启用 / 已停用」分段筛选；卡片的 ⋯ 菜单里有插件 ID、依赖、注册页面与「复制插件 ID」；加载失败的插件收在一条可点击展开的提示条里，不影响其它插件</li>
        <li><b>插件商店</b>：独立窗口列出商店目录里的 .fpplug 可安装包，每个包一个「安装」按钮，已装的标记「已安装」</li>
        <li><b>手动安装</b>：把 .fpplug 插件包放进安装目录或商店目录，点「重新扫描」即可用</li>
        <li><b>管理</b>：「打开插件目录」定位插件文件夹；「重新扫描」重新装配全部插件</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　总闸在「设置 → 悬浮球 → 悬浮球插件总闸」：关闭后所有插件一律不加载。</td></tr></table>

        <h3>内置插件</h3>
        <p style="color:PHCOLOR; font-size:12px;">出厂自带的六个插件：搜索、周期任务、AI 三件套、保险箱与周报。</p>
        <ul>
        <li><b>站内搜索</b>（[[Ctrl+Alt+F]]）：知识库 / 笔记 / 碎片 / 任务 / 素材全文检索，自研中文分词 + 倒排索引 + BM25 排序，命中片段带高亮，点结果标题跳转；[[Ctrl+K]] 是同一入口</li>
        <li><b>周期任务</b>（[[Ctrl+Alt+R]]）：只给规则（每天 / 每周几 / 每月几号 / 每 N 天 / 每 N 周），到点自动生成任务；错过的补一次、同一天不重复生成</li>
        <li><b>AI 助手</b>（[[Ctrl+Alt+I]]）：本地 / 云端双后端的对话助手，读应用内任务、碎片、笔记做总结、分类与问答；多会话记录重启不丢，可导出 Markdown，支持自定义快捷指令与规则库（后端一次配置、所有 AI 插件共用，见「设置 → AI 配置」）</li>
        <li><b>密码保险箱</b>（[[Ctrl+Alt+V]]）：只活在本机的密码收纳，自定义字段 + DPAPI 与主密码双因子加密；[[Ctrl+Alt+B]] 快速取用，复制 30 秒后自动清剪贴板，不联网不同步</li>
        <li><b>文本工坊</b>（[[Ctrl+Alt+T]]）：剪贴板一键 AI 加工——润色成邮件、翻译、总结要点、改写正式、提取待办并转任务；原文自动带入（超长自动截断并提示），结果可存碎片 / 存笔记 / 转任务，自带历史与收藏指令</li>
        <li><b>日报 / 周报草稿</b>（[[Ctrl+Alt+W]]）：一键汇总这段时间的任务 / 碎片 / 番茄生成 Markdown 草稿，可再交 AI 润色成正式周报（动作型插件，无独立页面，热键或插件中心触发）</li>
        </ul>

        <h3>托盘与后台</h3>
        <p style="color:PHCOLOR; font-size:12px;">关了窗口它还在：托盘常驻、单实例，行为都在这一章。</p>
        <ul>
        <li><b>单击托盘图标</b>：显示 / 隐藏主窗口</li>
        <li><b>右键托盘图标</b>：显示 / 隐藏主窗口、显示 / 隐藏悬浮球、退出程序</li>
        <li><b>关闭主窗口</b>：默认最小化到托盘不退出（可在设置中改为直接退出）</li>
        <li><b>单实例运行</b>：重复启动会唤醒已在运行的窗口，不会开出第二个进程</li>
        </ul>

        <h3>设置</h3>
        <p style="color:PHCOLOR; font-size:12px;">十个分类各管一摊，改动即生效；恢复默认的入口在页底常驻栏。</p>
        <ul>
        <li><b>分类导航</b>：外观 / 悬浮球 / 剪贴板与碎片 / 临时素材 / 全局工具 / 番茄钟 / 启动与系统 / 导出 / AI 配置 / 关于，点左侧分类即切换，右侧只显示当前分类、各自独立滚动</li>
        <li><b>外观</b>：浅色 / 深色主题一键切换、动画速度、软件卡片尺寸</li>
        <li><b>悬浮球</b>：显示悬浮球、球体大小、自动隐藏（总开关 + 延迟秒数）、全屏应用让位、小卡片保持显示、悬浮球插件总闸</li>
        <li><b>剪贴板与碎片</b>：历史上限、过滤应用（逗号分隔）、自动收集剪贴板图片</li>
        <li><b>临时素材</b>：条数上限、单文件体积上限、保留天数、缩略图大小</li>
        <li><b>全局工具</b>：截图钉屏（开关 + 热键）</li>
        <li><b>番茄钟</b>：开关、专注时长（1-120 分钟）、休息时长（1-60 分钟）、自动进入休息；计时由悬浮球外圈进度环呈现，右键球体开始 / 暂停 / 结束</li>
        <li><b>启动与系统</b>：开机自启、启动时恢复上次页面、关闭即收进托盘、任务提醒（汇总逾期 / 今日到期 / 未安排日期的未完成任务）</li>
        <li><b>导出</b>：选定 Obsidian vault 目录后，一键把笔记 / 碎片 / 任务导出为 Markdown（重复导出覆盖同名文件）</li>
        <li><b>AI 配置</b>：云端 / 本地后端<b>一次配置、所有接入的 AI 插件共用</b>——云端填 OpenAI 兼容地址 / Key / 模型；本地选 llama-server.exe 与 .gguf 模型文件、可一键「启动本地服务」（退出程序自动结束）；「保存并测试连接」配置落盘并即时探活；「接入插件」勾选哪些插件，哪些就改用这套后端（改完即生效，未勾选的插件继续用自己的配置）</li>
        <li><b>关于</b>：版本信息与快捷键速查；点「检查更新」仅在你点击时访问一次 GitHub Releases API（不携带任何本机数据），发现新版只给下载页入口、不自动下载，离线不影响任何功能</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　所有设置实时保存，无需手动保存；「↺ 恢复默认设置」在<b>页底常驻栏</b>（任何分类下都可见），恢复全部默认值（软件导航条目、窗口与悬浮球位置会保留）。</td></tr></table>

        <h3>数据与迁移</h3>
        <p style="color:PHCOLOR; font-size:12px;">数据全在本机一个文件夹里：拷走文件夹 = 完整备份。</p>
        <ul>
        <li>全部数据都在本地：程序目录的 <span style="font-family:Consolas,'Courier New',monospace">float_data/</span>（碎片 / 任务 / 笔记 / 素材索引 / 配置 / 日志 / 临时素材 / 知识库.docx）</li>
        <li>换电脑时把整个程序文件夹拷走即可，数据跟着走</li>
        <li>程序不联网、不登录、不上传任何内容，断网状态下所有功能照常可用</li>
        </ul>
        <table width="100%"><tr><td bgcolor="TIPBG" style="padding:9px 12px; font-size:12px;"><b style="color:PRIMCOLOR">提示</b>　定期备份 = 复制一份程序文件夹（或仅 <span style="font-family:Consolas,'Courier New',monospace">float_data/</span>），粘到哪里都行。</td></tr></table>
        """

    # ------------------------------------------------------------------
    # 说明页内部分类导航（2026-10-02：长滚动 → 与设置页同款次导航）。
    # 顺序即左栏展示顺序，(key, 图标名, 名称)；图标全部取 icons.py 既有
    # 字形（零新增）。章节正文不在此表 —— _help_section_html 从整篇
    # _help_html() 按 <h3> 切片而来，正文一字不动（内容与界面文案的逐条
    # 同步见 CHANGELOG 2026-10-02「帮助页正文同步」段，本轮改壳不改稿）。
    # 「总览」= 第一个 <h3> 之前的导语段，key 直取；其余 16 项的名称必须
    #   与正文 <h3> 标题逐字一致（test_help_nav 钉两表一致）。
    # ------------------------------------------------------------------
    HELP_CATEGORIES = (
        ("overview", "help", "总览"),
        ("hotkeys", "command", "全局快捷键"),
        ("ball", "ball", "悬浮球"),
        ("card", "window", "小卡片"),
        ("fragments", "fragments", "碎片工作台"),
        ("tasks", "tasks", "日程任务"),
        ("notes", "notes", "笔记管理"),
        ("knowledge", "knowledge", "知识库"),
        ("assets", "assets", "临时素材"),
        ("nav", "nav", "网址导航"),
        ("apps", "apps", "软件导航"),
        ("shot", "screenshot", "截图钉屏"),
        ("pluginhub", "plugins", "插件中心"),
        ("builtin", "store", "内置插件"),
        ("tray", "power", "托盘与后台"),
        ("settings", "settings", "设置"),
        ("data", "folder", "数据与迁移"),
    )

    # 目录分组（2026-10-03 目录化设计）：左栏从 17 枚按钮换成 6 组纯文字
    # 目录。组名只用于目录展示；成员键序必须与 HELP_CATEGORIES 严格同序
    # （test_help_nav 双向钉死：并集 = 17 键、拼接序 = 主表键序）。
    HELP_TOC_GROUPS = (
        ("开始使用", ("overview", "hotkeys")),
        ("悬浮球与小卡片", ("ball", "card")),
        ("功能面板", ("fragments", "tasks", "notes", "knowledge", "assets")),
        ("导航与工具", ("nav", "apps", "shot")),
        ("插件", ("pluginhub", "builtin")),
        ("系统与数据", ("tray", "settings", "data")),
    )
    HELP_TOC_WIDTH = 168   # 目录栏宽（设计稿 §四；含自身滚动条）

    @classmethod
    def _help_html_sections(cls) -> dict:
        """把整篇 _help_html() 按 <h3> 切成 {章节标题: 含 h3 的片段}。

        str.find 走查而不用 re.split：正文是手写富文本，只认「<h3>…
        </h3>」字面对；「有头无尾」的损坏内容并进前一章（运行时容错，
        结构性错误由 test_help_nav 在 CI 拦住）。
        """
        html = cls._help_html()
        marks = []
        i = html.find("<h3>")
        while i >= 0:
            j = html.find("</h3>", i)
            if j < 0:
                break
            marks.append((i, html[i + 4:j].strip()))
            i = html.find("<h3>", j)
        out = {"overview": html[:marks[0][0]].strip() if marks else html.strip()}
        for idx, (start, title) in enumerate(marks):
            end = marks[idx + 1][0] if idx + 1 < len(marks) else len(html)
            out[title] = html[start:end].strip()
        return out

    @classmethod
    def _help_section_html(cls, key: str) -> str:
        """按分类 key 取章节 HTML；总览取导语段，其余按左栏名称对 <h3>。

        查不到返回空串 —— 标题表与正文失同步时页面不崩（空白由
        test_help_nav 的两表一致断言在 CI 阶段拦住）。
        """
        sections = cls._help_html_sections()
        if key == "overview":
            return sections.get("overview", "")
        label = dict((c[0], c[2]) for c in cls.HELP_CATEGORIES).get(key, "")
        return sections.get(label, "")

    def _build_help_page(self):
        """构建使用说明页面（嵌入 QStackedWidget，与其它页面同层级切换）

        2026-10-02 重构：13 个章节此前挤在一条长滚动里，改成与设置页
        同款「左侧分类导航 + 右侧分类页」，每章独立滚动。
        2026-10-03 目录化：17 枚 settingsNavBtn 按钮换成 6 组纯文字目录
        （TOC，形态=目录不是按钮），右侧 17 章合并为**单个 QTextBrowser
        长滚动**——点目录跳章（动画滚动条），滚动正文时目录高亮跟随
        （scrollspy）。正文管线零改动：切片原文缓存 _help_raw，
        [[热键]] 键帽与主题色统一在 _decorate_help_html 注入。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 0, 8, 0)
        outer.setSpacing(8)

        # 标题与设置页同款：PageTitle 顶格通栏
        outer.addWidget(PageTitle("help", "FloatPulse 使用说明", self))
        outer.addSpacing(8)

        # ---- 中部：左目录 + 1px 分隔线 + 右单页长滚动 ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        body.addWidget(self._build_help_toc_rail())
        divider = QFrame()
        divider.setObjectName("settingsNavDivider")
        divider.setFixedWidth(1)
        body.addWidget(divider)
        browser = QTextBrowser()
        browser.setOpenLinks(False)      # 正文无链接，点击不做导航
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; }")
        browser.verticalScrollBar().valueChanged.connect(self._help_scrollspy)
        browser.verticalScrollBar().rangeChanged.connect(self._help_scrollspy)
        self._help_browser = browser
        body.addWidget(browser, 1)
        outer.addLayout(body, 1)

        # ---- 正文组装：17 章切片合并，每章前置命名锚点 ch_<key> ----
        self._help_raw = {}   # key → 切片原文；装饰与取色在 _apply_help_content_color 统一做
        for key, _icon, _label in self.HELP_CATEGORIES:
            self._help_raw[key] = self._help_section_html(key)
        # scrollspy / 跳章动画状态（章内定位缓存随视口宽重算，见 _help_locate_chapters）
        self._help_chapter_y = {}
        self._help_chapter_geo = None
        self._help_active_key = None
        self._help_scroll_anim = None
        self._apply_help_content_color()
        first = self.HELP_CATEGORIES[0][0]
        self._help_toc_btns[first].setChecked(True)
        self._help_active_key = first
        return page

    def _build_help_toc_rail(self) -> QWidget:
        """说明页左目录（TOC）：6 组纯文字目录，替代 17 枚按钮（2026-10-03）。

        目录项是 checkable QPushButton（objectName=helpTocItem，纯 QSS：
        常态 text_secondary 纯文字无图标无底色块、hover primary_a08 淡染、
        选中 accent_soft 底 + 主色加粗 + 左缘 3px 竖条——与 settingsNavBtn
        按钮形态明确区分）。目录自身可滚，小窗高度不再裁切。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(self.HELP_TOC_WIDTH)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        self._help_toc_scroll = scroll
        inner = QWidget()
        rv = QVBoxLayout(inner)
        rv.setContentsMargins(0, 0, 4, 2)
        rv.setSpacing(0)
        labels = dict((c[0], c[2]) for c in self.HELP_CATEGORIES)
        self._help_toc_btns = {}
        self._help_toc_group = QButtonGroup(self)
        self._help_toc_group.setExclusive(True)
        for gi, (group_name, keys) in enumerate(self.HELP_TOC_GROUPS):
            if gi > 0:
                # 组间分隔（用户反馈 2026-10-03）：纯文字列表 17 项容易
                # 糊成一条，组与组之间加 1px hairline——复用 settingsNavDivider
                # 统一钩子（$hair 中性色）；首组贴页标题不加。
                rv.addSpacing(7)
                gdiv = QFrame()
                gdiv.setObjectName("settingsNavDivider")
                gdiv.setFixedHeight(1)
                rv.addWidget(gdiv)
                rv.addSpacing(2)
            glabel = QLabel(group_name)
            glabel.setObjectName("helpTocGroup")
            # 组标题细节提示（用户反馈 2026-10-03）：主色小刻度（3×10 圆角
            # 竖条，primary_a30——与选中项左缘竖条同语系但降一档权重）+
            # 1.5px 字距（QSS 无 letter-spacing，走 QFont；中文目录的
            # 「小型大写间距」等价物），让 11px 灰字读作组头而非目录项。
            f = glabel.font()
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
            glabel.setFont(f)
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(6)
            tick = QFrame()
            tick.setObjectName("helpTocTick")
            tick.setFixedSize(3, 10)
            rh.addWidget(tick, 0, Qt.AlignmentFlag.AlignVCenter)
            rh.addWidget(glabel, 0, Qt.AlignmentFlag.AlignVCenter)
            rh.addStretch(1)
            rv.addWidget(row)
            for key in keys:
                btn = QPushButton(labels[key])
                btn.setObjectName("helpTocItem")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda _checked=False, k=key: self.show_help_category(k))
                self._help_toc_group.addButton(btn)
                self._help_toc_btns[key] = btn
                rv.addWidget(btn)
        rv.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def show_help_category(self, key: str):
        """跳到指定章节（目录点击与外部深链共用入口）；未知 key 静默忽略。

        选中态交给目录按钮（QButtonGroup 互斥）；正文跳章 = 章节块定位
        + 动画 scrollbar.value（QTextBrowser.scrollToAnchor 是瞬跳，平滑
        滚动靠动画实现；reduce_motion / 时长归零直接落位，扫块失败回退
        原生 scrollToAnchor）。动画在途时 scrollspy 挂起（终点即目标章，
        避免中途误高亮），连点由 _help_stop_scroll_anim 掐掉上一条。
        """
        btn = self._help_toc_btns.get(key)
        if btn is None:
            return
        btn.setChecked(True)
        self._help_active_key = key
        sb = self._help_browser.verticalScrollBar()
        top = self._help_chapter_top(key)
        if top is None:
            self._help_stop_scroll_anim()
            self._help_browser.scrollToAnchor(f"ch_{key}")
            return
        end = max(0, min(int(top) - 6, sb.maximum()))
        ms = motion.duration(motion.MOTION["base"], self.anim_speed)
        if ms <= 0 or motion.reduce_motion():
            self._help_stop_scroll_anim()
            sb.setValue(end)
            return
        self._help_stop_scroll_anim()
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(ms)
        anim.setStartValue(sb.value())
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._help_scroll_anim_done)
        self._help_scroll_anim = anim
        anim.start()

    def _help_scroll_anim_done(self):
        """跳章动画收尾：解锁 scrollspy 并按终点位置同步一次高亮"""
        self._help_scroll_anim = None
        self._help_scrollspy()

    def _help_stop_scroll_anim(self):
        """掐掉在途跳章动画（stop 会发 finished，先摘引用防重入）"""
        anim, self._help_scroll_anim = self._help_scroll_anim, None
        if anim is not None:
            anim.stop()

    def _help_chapter_top(self, key: str):
        """章节标题块的文档 y 坐标（缓存随视口宽/文档高失效重算）"""
        self._help_locate_chapters()
        return self._help_chapter_y.get(key)

    def _help_locate_chapters(self):
        """扫块定位 17 个章节标题（h3 渲染为文本=章节名的块）。

        动画跳章需要先拿到章节的文档 y；标题文本与左栏名逐字同源
        （两表一致由 test_help_nav 钉死），按块文本匹配安全，每章只取
        首次出现。视口宽或文档高变了才重扫（resize / 重渲染自动失效）。
        """
        browser = self._help_browser
        geo = (browser.viewport().width(),
               browser.document().size().height())
        if self._help_chapter_geo == geo and self._help_chapter_y:
            return
        labels = dict((c[2], c[0]) for c in self.HELP_CATEGORIES)
        layout = browser.document().documentLayout()
        ys = {}
        block = browser.document().firstBlock()
        while block.isValid():
            key = labels.get(block.text().strip())
            if key and key not in ys:
                ys[key] = layout.blockBoundingRect(block).top()
            block = block.next()
        # 总览没有 h3 标题块（导语段），文档顶就是它的章首
        ys.setdefault("overview", 0.0)
        self._help_chapter_y = ys
        self._help_chapter_geo = geo

    def _help_scrollspy(self, _value=None):
        """正文滚动 → 视口顶部所在章节 → 目录高亮跟随（scrollspy）。

        跳章动画在途时挂起（终点本身就是目标章节）；高亮变化时把目录
        项滚到可见（ensureWidgetVisible，目录自身可滚）。
        """
        if (self._help_scroll_anim is not None
                or not getattr(self, "_help_toc_btns", None)):
            return
        self._help_locate_chapters()
        pos = self._help_browser.verticalScrollBar().value() + 40.0
        current = None
        for key, _icon, _label in self.HELP_CATEGORIES:
            y = self._help_chapter_y.get(key)
            if y is not None and y <= pos:
                current = key
        if current is None:
            current = self.HELP_CATEGORIES[0][0]
        if current == self._help_active_key:
            return
        self._help_active_key = current
        btn = self._help_toc_btns.get(current)
        if btn is not None:
            btn.setChecked(True)   # QButtonGroup 互斥，自动取消上一个
            self._help_toc_scroll.ensureWidgetVisible(btn, 0, 16)

    def _help_full_html(self) -> str:
        """17 章切片合并为单文档（每章前置命名锚点 ch_<key>）——
        单 QTextBrowser 渲染的唯一原文来源，主题色另行注入。"""
        return "".join(
            f'<a name="ch_{key}"></a>{self._help_raw.get(key, "")}'
            for key, _icon, _label in self.HELP_CATEGORIES)

    def _apply_help_content_color(self):
        """使用说明正文重渲染（切主题时由 _apply_theme 调用）。

        Qt 富文本不吃 QSS，颜色只能行内注入；每次主题变化都用
        _decorate_help_html 从 17 章合并原文重新装饰——原文是唯一真相
        源，避免上次注入的行内样式叠进本次输出。重渲染会重置滚动位置：
        按滚动比例恢复（读在哪一章附近，换主题后还在哪一章附近）。
        """
        browser = getattr(self, "_help_browser", None)
        if browser is None:
            return
        colors = get_colors(self._theme)
        sb = browser.verticalScrollBar()
        ratio = (sb.value() / sb.maximum()) if sb.maximum() > 0 else 0.0
        browser.setHtml(self._decorate_help_html(self._help_full_html(),
                                                 colors))
        sb.setValue(int(ratio * sb.maximum()))
        self._help_chapter_geo = None   # 新文档宽高未定 → 下次定位重扫
        self._help_scrollspy()

    @staticmethod
    def _decorate_help_html(raw: str, colors: dict) -> str:
        """把说明页切片原文装饰成最终富文本（主题色 + 热键键帽 + 形态）。

        - [[键位]] → 等宽字体键帽样式（bg_level2 底 + text 字色），
          未闭合的 [[ 按原文保留（容错，结构性错误由 test_help_nav 拦）；
        - <h3> 章节标题染主题主色；
        - 形态占位 token（2026-10-03，源稿零主题色的固定字面量）：
          TIPBG→bg_level2（提示块底）、PHCOLOR→text_placeholder（导语
          灰字与灰色色块）、PRIMCOLOR→primary（提示前缀）、
          DANGERCOLOR→danger、WARNCOLOR→warn（任务色块行红 / 橙）。
        """
        bg = colors.get("bg_level2", "#F1EFE8")
        fg = colors.get("text", "#2C2C2A")
        primary = colors.get("primary", "#0F6E56")
        raw = (raw
               .replace("TIPBG", bg)
               .replace("PHCOLOR", colors.get("text_placeholder", "#6E6D67"))
               .replace("PRIMCOLOR", primary)
               .replace("DANGERCOLOR", colors.get("danger", "#A32D2D"))
               .replace("WARNCOLOR", colors.get("warn", "#854F0B")))
        cap = ('<span style="font-family:Consolas,\'Courier New\',monospace; '
               'background-color:%s; color:%s;">&nbsp;%s&nbsp;</span>')
        out = []
        rest = raw
        while True:
            head, sep, tail = rest.partition("[[")
            if not sep:
                out.append(rest)
                break
            key, sep2, rest2 = tail.partition("]]")
            if not sep2:
                out.append(head + sep + tail)   # 未闭合按原文保留
                break
            out.append(head)
            out.append(cap % (bg, fg, key.strip()))
            rest = rest2
        html = "".join(out)
        # 章节化（2026-10-03 目录化：单页长滚动的章界视觉）——标题上距
        # 22px 拉开章间距；标题下 1px 分隔线。Qt 富文本不支持块级 border
        # 与 <hr>，背景色细行最薄也要 ~10px（最小行高钳制）——用「透明
        # 单元格的 border-bottom」实现：块高 ~11px 但可见的只有 1px 线
        # （离屏像素竖扫验证：整行唯一暗行）。
        line = colors.get("line", "#E4E2DB")
        divider = (f'<table width="100%" cellspacing="0" cellpadding="0">'
                   f'<tr><td style="border-bottom:1px solid {line}; '
                   f'font-size:1px;">&nbsp;</td></tr></table>')
        html = (html
                .replace("<h3>", '<h3 style="margin-top:22px;">'
                         '<span style="color:%s; font-size:15px;">' % primary)
                .replace("</h3>", "</span></h3>" + divider))
        return html

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


