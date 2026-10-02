# -*- coding: utf-8 -*-
"""
====================================================================
卡片弹窗模块  -  CardWindow
====================================================================
小卡片弹窗窗口（440×340），左侧微型 Tab 导航 + 右侧内容区。

Tab 列表（数字序号）：
  1. 📚 知识卡片  - 随机知识卡片，「下一张」切换
  2. 📋 日程任务  - 任务输入 + 列表，右键菜单管理
  3. 📝 临时笔记  - 单条便签，800ms 防抖自动保存
  4.  网址导航  - 平铺展示所有站点，点击跳转浏览器（锁定常驻，仅展示）

统一行为：
  - 鼠标离开「球+卡片」区域 → 自动关闭
  - 拖动卡片或悬浮球期间 → 不自动关闭
  - 记住上次关闭时的 Tab，下次弹出时恢复

特性：
  - 无系统原生标题栏 (FramelessWindowHint)
  - 始终置顶 (WindowStaysOnTopHint)
  - 半透明背景 + 大圆角容器 + 柔和阴影
  - 非交互区域可拖动
  - 右键卡片 → 退出菜单

依赖：
  - task_manager.TaskManager
  - note_manager.NoteManager
  - nav_manager.NavManager
====================================================================
"""

import html
import os
import random
from urllib.parse import urlparse

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QMenu, QToolButton,
    QStackedWidget, QListWidget, QListWidgetItem, QLineEdit, QDateEdit,
    QDialog, QFormLayout, QTextEdit, QScrollArea,
    QFrame, QSizePolicy, QGridLayout, QSystemTrayIcon, QApplication,
)
from PyQt6.QtCore import (
    Qt, QTimer, QDate, QPoint, QSize, pyqtSignal, QVariantAnimation,
    QEasingCurve, QPropertyAnimation, QRectF, QMimeData,
)
from PyQt6.QtGui import QColor, QAction, QDesktopServices, QPainter, QPixmap, QFontMetrics, QFont, QIcon, QDrag, QImageReader, QShortcut, QKeySequence
from PyQt6.QtCore import QUrl

from src.glass_dialog import make_dialog_buttons
from src.task_manager import (
    TaskManager, task_state, format_relative_deadline, format_completed_date,
    group_title, KIND_ROW, KIND_HEADER,
)
from src.task_delegate import (
    TaskItemDelegate, KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE, ROLE_DONE,
)
from src.controls import (
    tune_list_scrolling, SmoothButton, IconButton, IconLabel, UndoBar,
)
from src.note_manager import NoteManager
from src.nav_manager import NavManager
from src.theme import get_card_window_qss, get_menu_qss, get_colors
from src.icon_render import icon as render_icon
from src.glass import GlassPanel, NavIndicator, draw_soft_shadow
from src.app_paths import get_screen_geometry
from src.constants import (
    NOTE_AUTOSAVE_INTERVAL_MS, DEFAULT_THEME, CHECK_ANIM_MS,
    MINI_ICON_MIN, MINI_ICON_MAX, MINI_ICON_DEFAULT, mini_btn_size,
)
from src import motion
from datetime import date as _date


# Tab 定义（纯文字；图标由 _TAB_ICONS 提供自绘名，首字段仅占位）
# UI 重构 03：原 emoji 列已清空 —— 渲染一律走 icons.py 自绘（见 _TAB_ICONS）
_TABS = [
    ("", "碎片"),
    ("", "知识卡片"),
    ("", "日程任务"),
    ("", "临时笔记"),
    ("", "网址导航"),
    ("", "临时素材"),
    ("", "软件导航"),
]
_TAB_KEYS = ["fragment", "card", "task", "note", "nav", "asset", "app"]

# Tab 键 → icons.py 图标名（P1：emoji 字形改自绘，消除字体缺字形时的方框；
# UI 重构 03 起 _TABS 首字段已清空，图标全部由本表提供）
_TAB_ICONS = {
    "fragment": "fragments",
    "card": "knowledge",
    "task": "tasks",
    "note": "notes",
    "nav": "nav",
    "asset": "assets",
    "app": "apps",
}

# 左侧 Tab 栏尺寸（固定宽度，不再展开收起）
_TAB_BAR_WIDTH = 44         # 固定宽度（UI 重构 03：48→44，省 4px 给内容区）
_TAB_BTN_SIZE = 40          # 图标按钮尺寸
_TAB_INDICATOR_W = 3        # 选中竖条指示器宽度
_TAB_INDICATOR_H = 24       # 选中竖条指示器高度

# ===== 软件导航小卡片的图标尺寸 =====
# 此前 icon_px=36 / btn_size=76 是两处独立硬编码，调设置页的「软件卡片尺寸」
# 完全推不动小卡片（那是主窗口的键）。现统一由 ``app_mini_icon_size`` 驱动，
# 范围常量在 src/constants.py（card_window / settings_panel / config 共用）。


def _domain_of_url(url: str) -> str:
    """从 URL 提取展示用域名：去 scheme、去 www.、去端口/路径。
    解析失败或无 host 时回退为去掉 scheme 的原始串。
    """
    raw = (url or "").strip()
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        host = ""
    host = host.strip()
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host
    # 回退：手动去掉 scheme:// 后取路径首段
    stripped = raw.split("://", 1)[-1]
    return stripped.split("/", 1)[0] or raw


# ====================================================================
# 选中 Tab 指示器（圆角竖条，通过 move 驱动滑动）
# ====================================================================
class _TabIndicator(NavIndicator):
    """左侧选中指示器：3px 宽圆角竖条（复用通用的 NavIndicator）。

    保留 set_indicator_y / get_indicator_y 两个旧接口，避免改动调用点。
    """

    def __init__(self, parent=None):
        super().__init__(parent, width=_TAB_INDICATOR_W, height=_TAB_INDICATOR_H)

    def set_indicator_y(self, y: int):
        """直接设置指示器 Y 位置（无动画）"""
        self.snap_to_y(y)

    def get_indicator_y(self) -> int:
        """获取当前指示器 Y 位置"""
        return self.y()


# ====================================================================
# 素材项组件：支持拖拽出去（拖拽时携带文件路径）
# ====================================================================
class _AssetItemWidget(QWidget):
    """单个素材项，显示缩略图/图标 + 文件名，支持拖拽取出"""

    DOUBLE_CLICK_THRESHOLD = 300  # 双击判定时间（毫秒）

    def __init__(self, asset, parent=None, theme: str = DEFAULT_THEME,
                 thumb_cache: dict | None = None):
        super().__init__(parent)
        self._asset = asset
        self._theme = theme
        self._thumb_cache = thumb_cache if thumb_cache is not None else {}
        self._drag_start = None
        self._last_click_time = 0
        self._is_valid = os.path.exists(asset.stored_path) if asset else False

        self.setFixedSize(72, 84)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._is_valid
                       else Qt.CursorShape.ForbiddenCursor)
        self.setToolTip(self._build_tooltip())

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 4, 2, 2)
        v.setSpacing(2)

        # 图标/缩略图区域
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(64, 64)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setObjectName("assetIcon")
        self._load_icon()
        v.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # 文件名
        name = asset.original_name if asset else ""
        if len(name) > 10:
            name = name[:9] + "…"
        self._name_label = QLabel(name)
        self._name_label.setObjectName("assetName")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(False)
        v.addWidget(self._name_label)

        if not self._is_valid:
            self._name_label.setStyleSheet("color: #999;")

    def _build_tooltip(self):
        if not self._asset:
            return ""
        tip = f"{self._asset.original_name}\n"
        tip += f"大小: {self._asset.size_display()}\n"
        tip += f"收录: {self._asset.added_time}\n"
        tip += "拖拽取出 | 双击打开 | 右键菜单"
        if not self._is_valid:
            tip += "\n文件已失效"
        return tip

    def _load_icon(self):
        """加载缩略图（缩略图走缓存 + 解码期缩放，大图不卡）。

        UI 重构 03：非图片文件类型图 / 失效提示 / 解码失败三处原用 emoji 占位，
        对应图标位依赖 01 包的 ``warning`` / ``image`` / ``file_*`` 自绘图标，
        本包先去 emoji（图标位暂留空），待 01 合并后补齐自绘。
        """
        if not self._is_valid or not self._asset.is_image:
            return

        aid = self._asset.asset_id
        cached = self._thumb_cache.get(aid)
        if cached is None:
            # 缓存未命中：QImageReader 解码期先缩到 2x 目标尺寸，
            # 避免整图载入内存（截图 PNG 可达数 MB）
            reader = QImageReader(self._asset.stored_path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and (size.width() > 128 or size.height() > 128):
                scale = 128 / max(size.width(), size.height())
                reader.setScaledSize(QSize(int(size.width() * scale),
                                           int(size.height() * scale)))
            img = reader.read()
            if not img.isNull():
                pix = QPixmap.fromImage(img).scaled(
                    64, 64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                cached = pix
            else:
                cached = False
            self._thumb_cache[aid] = cached
        if cached is not False:
            self._icon_label.setPixmap(cached)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_valid:
            self._drag_start = event.pos()
        # 双击检测
        if event.button() == Qt.MouseButton.LeftButton:
            import time
            now = int(time.time() * 1000)
            if now - self._last_click_time < self.DOUBLE_CLICK_THRESHOLD:
                self._on_double_click()
                self._last_click_time = 0
            else:
                self._last_click_time = now
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start is None or not self._is_valid:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 10:
            return
        # 构造拖拽
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._asset.stored_path)])
        mime.setText(self._asset.stored_path)
        drag.setMimeData(mime)
        # 图片设置拖拽预览
        if self._asset.is_image:
            preview = QPixmap(self._asset.stored_path)
            if not preview.isNull():
                drag.setPixmap(preview.scaled(
                    80, 80,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))
                drag.setHotSpot(QPoint(40, 40))
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _on_double_click(self):
        """双击用系统默认程序打开"""
        if self._is_valid:
            try:
                os.startfile(self._asset.stored_path)
            except Exception:
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(self._asset.stored_path))
                except Exception:
                    pass

    def contextMenuEvent(self, event):
        """右键菜单"""
        if not self._asset:
            return
        menu = QMenu(self)
        # 跟随当前主题（原先硬编码 light，深色主题下会弹出白底菜单）
        menu.setStyleSheet(get_menu_qss(self._theme))

        act_open = menu.addAction("打开")
        act_copy = menu.addAction("复制路径")
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        # 删除项危险语义：Qt 菜单无法按 action 单独设文字色，
        # 用 danger 色的自绘 trash 图标承载（UI 重构 03）
        act_delete.setIcon(
            render_icon("trash", 14, get_colors(self._theme)["danger"]))

        action = menu.exec(event.globalPos())
        if action == act_open:
            self._on_double_click()
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            cb.setText(self._asset.stored_path)
        elif action == act_delete:
            # 通过顶层 CardWindow 处理数据层删除
            top = self.window()
            if isinstance(top, CardWindow):
                top._delete_asset(self._asset.asset_id)


# ====================================================================
# 卡片弹窗类
# ====================================================================
class CardWindow(QWidget):

    data_changed = pyqtSignal(str)
    request_quit = pyqtSignal()
    card_moved = pyqtSignal()
    card_drag_started = pyqtSignal()   # 在卡片上按下左键（可能开始拖动）
    card_drag_finished = pyqtSignal()  # 卡片拖动松手
    card_closed = pyqtSignal()   # 保持显示模式下用户点击关闭按钮

    WINDOW_WIDTH = 440
    WINDOW_HEIGHT = 340
    CARD_MARGIN = 16             # 窗口四周阴影留白（卡片内容仍是 440×340）
    CARD_RADIUS = 12             # 卡片圆角（UI 重构 03：22→12，小窗配大圆角不划算）
    CONTENT_MARGIN = 16          # 内容区四周内边距
    CLOSE_BTN_SIZE = 22          # 常驻模式的右上角关闭按钮
    CLOSE_BTN_MARGIN = 8         # 该按钮距卡片右/上边缘的内边距
    CLOSE_BTN_RESERVE = 26       # 常驻模式下内容区顶部净空，给关闭按钮让位

    def __init__(self, theme: str = DEFAULT_THEME):
        super().__init__()
        self._theme = theme
        self._cards = []
        self._current_index = -1
        self._seq_index = -1      # 滚轮顺序翻卡下标（与 next_card 的随机策略区分）
        self._task_manager = None
        self._note_manager = None
        self._nav_manager = None
        self._config_manager = None
        self._asset_manager = None
        self._fragment_manager = None
        # 素材页优化：缩略图缓存（asset_id -> QPixmap|False）+ 脏标记
        # （切页只在数据变化后首次重建，避免每次切页同步解码全部原图）
        self._asset_thumb_cache = {}
        self._asset_page_dirty = True
        self._current_note_id = None
        self._loading_note = False
        self._last_mode = "fragment"
        self._dragging = False
        # 小卡片软件图标边长；None = 尚未从配置读入（mini_icon_size 懒读兜底）
        self._mini_icon_size = None
        self._drag_offset = QPoint()
        self._note_save_timer = QTimer(self)
        self._note_save_timer.setSingleShot(True)
        self._note_save_timer.setInterval(NOTE_AUTOSAVE_INTERVAL_MS)
        self._note_save_timer.timeout.connect(self._on_save_note)

        # 日程任务：勾选动画状态（与主窗口任务页同构）
        self._task_anim_task_id = None      # 正在动画的 task_id（None=空闲）
        self._task_undo_target = None       # (task_id, prev_done)
        self._task_anim = None              # QVariantAnimation（_build_task_page 创建）
        self._task_rebuild_timer = None     # 延时重建定时器

        # 指示器初始化守卫（防止首次显示时动画到错误位置）
        self._indicator_ready = False

        self._init_window()
        self._init_ui()
        self._init_context_menu()

        # Esc → 关闭卡片（1.3）：与鼠标离开自动关闭同语义（hide），不退出
        # 程序。窗口级 QShortcut，焦点在任意子控件（含输入框）上都触发。
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.hide)

    # ---------------- 初始化 ----------------
    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 窗口 = 卡片内容 + 四周阴影留白（阴影由 paintEvent 手绘）
        self.setFixedSize(self.WINDOW_WIDTH + self.CARD_MARGIN * 2,
                          self.WINDOW_HEIGHT + self.CARD_MARGIN * 2)

    def paintEvent(self, event):
        """手绘卡片外圈柔和阴影。

        不用 QGraphicsDropShadowEffect：它与 WA_TranslucentBackground 组合
        会走离屏渲染，拖动/切页时明显掉帧（大窗口早已因此改为自绘）。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = get_colors(self._theme)
        alpha = 96 if colors.get("_name") == "dark" else 60
        m = self.CARD_MARGIN
        draw_soft_shadow(
            painter,
            QRectF(m, m, self.WINDOW_WIDTH, self.WINDOW_HEIGHT),
            self.CARD_RADIUS,
            layers=6, max_alpha=alpha, offset_y=6.0,
        )
        painter.end()

    def _init_ui(self):
        # 玻璃壳容器：半透明填充 + 顶部高光带 + 双色描边 + 噪点（glass.py 手绘）
        self._container = GlassPanel(self, radius=self.CARD_RADIUS)
        self._container.setObjectName("cardContainer")
        self._container.setGeometry(self.CARD_MARGIN, self.CARD_MARGIN,
                                    self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        outer = QHBoxLayout(self._container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 左侧固定图标导航栏 + 滑动指示器 ----
        self._side_tab = QWidget()
        self._side_tab.setObjectName("sideTabBar")
        self._side_tab.setFixedWidth(_TAB_BAR_WIDTH)
        side_v = QVBoxLayout(self._side_tab)
        side_v.setContentsMargins(0, 12, 0, 12)
        side_v.setSpacing(4)

        # 选中竖条指示器（用 QWidget + paintEvent 实现平滑滑动）
        self._indicator = _TabIndicator(self._side_tab)
        self._indicator.setFixedSize(_TAB_INDICATOR_W, _TAB_INDICATOR_H)
        self._indicator.raise_()

        # 图标按钮（始终显示，固定布局）
        self._tab_buttons = []
        for i, (_emoji, name) in enumerate(_TABS):
            key = _TAB_KEYS[i]
            btn = IconButton(_TAB_ICONS[key], size=_TAB_BTN_SIZE, icon_size=18,
                             object_name="sideTabIconBtn", checkable=True,
                             tooltip=name)
            # 点击切换 Tab（hover 只保留高亮）：鼠标扫过左侧栏不会再连续误切
            btn.clicked.connect(lambda _checked=False, k=key: self._switch_mode(k))
            self._tab_buttons.append(btn)
            side_v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        side_v.addStretch()

        # 指示器滑动动画已内置在 NavIndicator（260ms OutQuint），此处不再单独维护

        outer.addWidget(self._side_tab)

        # ---- 右侧内容区 ----
        content_area = QWidget()
        content_area.setObjectName("contentArea")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(self.CONTENT_MARGIN, self.CONTENT_MARGIN,
                                          self.CONTENT_MARGIN, self.CONTENT_MARGIN)
        content_layout.setSpacing(8)
        self._content_layout = content_layout   # 常驻模式要动态加大顶部净空

        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)

        # 页面0：碎片（新增）
        self._stack.addWidget(self._build_fragment_page())
        # 页面1：知识卡片
        self._stack.addWidget(self._build_card_page())
        # 页面2：日程任务
        self._stack.addWidget(self._build_task_page())
        # 页面3：临时笔记
        self._stack.addWidget(self._build_note_page())
        # 页面4：网址导航
        self._stack.addWidget(self._build_nav_page())
        # 页面5：临时素材
        self._stack.addWidget(self._build_asset_page())
        # 页面6：软件导航（小卡片内嵌只读浏览页，图标+名称，点击启动）
        self._stack.addWidget(self._build_app_page())

        # 给所有页面铺实色背景：grab() 快照不再黑底，切页也不透黑
        self._apply_pages_background()

        outer.addWidget(content_area, 1)

        # 保持显示模式下的关闭按钮（右上角，默认隐藏）
        self._close_btn = IconButton("close", size=self.CLOSE_BTN_SIZE,
                                     icon_size=12, object_name="cardCloseBtn",
                                     off_color="danger", hover_color="#FFFFFF",
                                     tooltip="关闭卡片", parent=self._container)
        self._close_btn.apply_theme(self._theme)
        self._close_btn.clicked.connect(self._on_close_button_clicked)
        self._close_btn.setVisible(False)
        self._place_close_button()

        self._apply_style()

        # 初始化指示器颜色（匹配主题主色）
        from src.theme import get_colors
        colors = get_colors(self._theme)
        self._indicator.set_color(QColor(colors["primary"]))

        # 阴影由窗口 paintEvent 手绘（多层圆角矩形，见 paintEvent 注释）

        self._switch_mode("fragment")

    # ==================================================================
    # 碎片页面（小卡片：仅复制 + 删除 + 拖拽复制）
    # ==================================================================
    def _build_fragment_page(self):
        """构建碎片工作台小卡片页面"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 滚动区域包裹碎片列表（仅纵向滚动，禁止横向滚动条）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("fragScrollArea")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_widget = QWidget()
        scroll_v = QVBoxLayout(scroll_widget)
        scroll_v.setContentsMargins(0, 0, 0, 0)
        scroll_v.setSpacing(0)

        self._frag_content_layout = QVBoxLayout()
        self._frag_content_layout.setSpacing(8)
        scroll_v.addLayout(self._frag_content_layout)
        scroll_v.addStretch()
        scroll.setWidget(scroll_widget)
        v.addWidget(scroll)
        return page

    def _refresh_fragment_page(self):
        """刷新碎片列表：每条显示内容预览 + 复制/删除按钮"""
        # 清除旧内容
        while self._frag_content_layout.count():
            item = self._frag_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._fragment_manager:
            empty = QLabel("暂无碎片")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._frag_content_layout.addWidget(empty)
            return

        fragments = self._fragment_manager.get_all_fragments()
        if not fragments:
            empty = QLabel("暂无碎片，复制文本即可收集")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._frag_content_layout.addWidget(empty)
            return

        # 取最近 20 条显示（小卡片不宜过多）
        display_frags = fragments[:20]

        # 预览文本可用宽度：内容区360 - 按钮(40×2) - 间距(6×2) - 行边距(左4右12) - 滚动条预留12
        text_width = self.WINDOW_WIDTH - _TAB_BAR_WIDTH - 32 - 40 * 2 - 6 * 2 - 4 - 12 - 12
        font = QFont("Microsoft YaHei")
        font.setPixelSize(14)  # 与 QSS 中 fragPreview 字号一致
        fm = QFontMetrics(font)

        for frag in display_frags:
            row = QWidget()
            row.setObjectName("fragItemRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 2, 12, 2)  # 右侧多留空间，防止删除按钮被卡片边缘遮挡
            row_layout.setSpacing(6)

            # 内容预览（按可用宽度省略截断，允许显示不完全）
            preview = fm.elidedText(
                frag.preview(max_len=120),
                Qt.TextElideMode.ElideRight, text_width)
            label = QLabel(preview)
            label.setObjectName("fragPreview")
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(label)

            # 复制按钮
            copy_btn = SmoothButton("复制")
            copy_btn.setObjectName("fragCopyBtn")
            copy_btn.setFixedSize(40, 24)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setToolTip("复制碎片内容")
            copy_btn.clicked.connect(lambda checked=False, c=frag.content: self._copy_fragment(c))
            row_layout.addWidget(copy_btn)

            # 删除按钮
            del_btn = SmoothButton("删除")
            del_btn.setObjectName("fragDelBtn")
            del_btn.setFixedSize(40, 24)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip("删除此碎片")
            del_btn.clicked.connect(lambda checked=False, fid=frag.fragment_id: self._delete_fragment(fid))
            row_layout.addWidget(del_btn)

            self._frag_content_layout.addWidget(row)

    def _copy_fragment(self, content: str):
        """复制碎片内容到剪贴板"""
        QApplication.clipboard().setText(content)

    def _delete_fragment(self, fragment_id: int):
        """删除碎片并刷新列表"""
        if self._fragment_manager:
            self._fragment_manager.delete_fragment(fragment_id)
            self._refresh_fragment_page()
            self.data_changed.emit("fragment")

    def _flash_tray_msg(self, msg: str):
        """通过托盘图标显示短暂提示（如果存在）"""
        try:
            app = QApplication.instance()
            if app:
                for w in app.topLevelWidgets():
                    if isinstance(w, QSystemTrayIcon):
                        w.showMessage("生活悬浮球", msg, QSystemTrayIcon.MessageIcon.NoIcon, 1500)
                        break
        except Exception:
            pass

    def _make_page_title(self, icon_name: str, text: str) -> QWidget:
        """页标题 = 自绘图标 + 文字。

        文字仍挂 ``#titleLabel`` —— 沿用 card_window QSS 口径，零样式回归；
        图标取当前主题 ``primary`` 色（换主题时在 :meth:`_apply_style` 重取）。
        """
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(IconLabel(icon_name, 15,
                                get_colors(self._theme)["primary"]))
        label = QLabel(text)
        label.setObjectName("titleLabel")
        lay.addWidget(label)
        lay.addStretch()
        return row

    # ==================================================================
    # 知识卡片页面
    # ==================================================================
    def _build_card_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(10)

        v.addWidget(self._make_page_title("knowledge", "知识卡片"))

        self._content_label = QLabel()
        self._content_label.setObjectName("contentLabel")
        self._content_label.setWordWrap(True)
        self._content_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._content_label.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self._content_label, 1)

        self._next_btn = SmoothButton("下一张")
        self._next_btn.setObjectName("nextBtn")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self.next_card)
        v.addWidget(self._next_btn, 0, Qt.AlignmentFlag.AlignRight)
        return page

    # ==================================================================
    # 日程任务页面
    # ==================================================================
    def _build_task_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self._task_title_input = QLineEdit()
        self._task_title_input.setObjectName("taskInput")
        self._task_title_input.setPlaceholderText("输入任务标题，回车添加...")
        self._task_title_input.returnPressed.connect(self._on_add_task)

        self._task_deadline = QDateEdit()
        self._task_deadline.setObjectName("taskDate")
        self._task_deadline.setCalendarPopup(True)
        self._task_deadline.setDisplayFormat("yyyy-MM-dd")
        self._task_deadline.setDate(QDate.currentDate())
        self._task_deadline.setFixedWidth(120)

        self._task_add_btn = SmoothButton("添加")
        self._task_add_btn.setObjectName("taskAddBtn")
        self._task_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._task_add_btn.clicked.connect(self._on_add_task)

        input_bar.addWidget(self._task_title_input, 1)
        input_bar.addWidget(self._task_deadline)
        input_bar.addWidget(self._task_add_btn)
        v.addLayout(input_bar)

        self._task_list = QListWidget()
        tune_list_scrolling(self._task_list)  # 丝滑化清单 L3：像素级滚动 + 统一步长
        self._task_list.setObjectName("taskList")
        self._task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_list.customContextMenuRequested.connect(self._on_task_context_menu)
        # 自定义行渲染委托（与主窗口任务页共用同一实现）
        self._task_delegate = TaskItemDelegate(get_colors(self._theme),
                                               self._task_list)
        self._task_delegate.toggle_requested.connect(self._on_task_toggle_requested)
        self._task_list.setItemDelegate(self._task_delegate)
        v.addWidget(self._task_list, 1)

        hint = QLabel("点击勾选框完成 | 右键任务：编辑 / 删除")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

        # 误勾撤销条（浮动子控件，贴底居中）
        self._task_undo_bar = UndoBar(page)
        self._task_undo_bar.undo_clicked.connect(self._on_task_undo)

        # 勾选动画 + 延时重建定时器
        self._task_anim = QVariantAnimation(self)
        self._task_anim.setStartValue(0.0)
        self._task_anim.setEndValue(1.0)
        self._task_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._task_anim.valueChanged.connect(self._on_task_anim_tick)
        self._task_anim.finished.connect(self._on_task_anim_finished)

        self._task_rebuild_timer = QTimer(self)
        self._task_rebuild_timer.setSingleShot(True)
        self._task_rebuild_timer.timeout.connect(self._refresh_task_list)
        return page

    # ==================================================================
    # 临时笔记页面
    # ==================================================================
    def _build_note_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        self._note_edit = QTextEdit()
        self._note_edit.setObjectName("noteEdit")
        self._note_edit.setPlaceholderText("临时笔记：随手记录，自动保存...")
        self._note_edit.textChanged.connect(self._on_note_text_changed)
        v.addWidget(self._note_edit, 1)
        return page

    # ==================================================================
    # 网址导航页面（锁定常驻，仅跳转展示）
    # ==================================================================
    def _build_nav_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        v.addWidget(self._make_page_title("nav", "网址导航"))

        # 可滚动区域展示分组和站点（仅纵向滚动，禁止横向滚动条）
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_scroll.setObjectName("navScroll")
        self._nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._nav_content = QWidget()
        self._nav_content.setObjectName("navContent")
        self._nav_content_layout = QVBoxLayout(self._nav_content)
        self._nav_content_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_content_layout.setSpacing(8)
        self._nav_scroll.setWidget(self._nav_content)

        v.addWidget(self._nav_scroll, 1)

        hint = QLabel("点击站点用浏览器打开 | 编辑请打开主窗口")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _refresh_nav_page(self):
        """刷新网址导航页面显示（不分分组，平铺所有站点）
        双列宽卡片：主标题 + 域名副标题（2026-09-25 用户拍板方案C）。
        超长标题省略号截断，tooltip 显示完整标题 + URL，点击整卡打开浏览器。
        """
        # 清除旧内容
        while self._nav_content_layout.count():
            item = self._nav_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._nav_manager:
            empty = QLabel("暂无网址，请在主窗口添加")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._nav_content_layout.addWidget(empty)
            return

        sites = self._nav_manager.get_all_sites_flat()
        if not sites:
            empty = QLabel("暂无网址，请在主窗口添加")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._nav_content_layout.addWidget(empty)
            return

        # 可用宽度推导：容器440 - 侧栏48 - 内容区margin16×2 - 页面margin4×2
        #   = 352；再预留纵向滚动条 ~12（出现时 viewport 变窄，这是最坏情况）
        available_width = self.WINDOW_WIDTH - _TAB_BAR_WIDTH - 32 - 8 - 12
        cols = 2
        spacing = 6
        card_w = (available_width - spacing * (cols - 1)) // cols  # 截断估算用
        card_h = 46

        # 按钮字体度量（标题 13px，与 QSS navSiteCardTitle 一致）
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        fm = QFontMetrics(font)

        # 双列宽卡片：QPushButton 作壳（自带点击/hover），内部叠 标题+域名 两行 QLabel。
        # 高度固定、宽度不写死 → 由 QGridLayout 两列均分实际 viewport 宽，永不溢出
        grid_container = QWidget()
        grid_container.setObjectName("navContent")
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(spacing)
        grid.setVerticalSpacing(spacing)

        for i, site in enumerate(sites):
            card = SmoothButton()
            card.setObjectName("navSiteCard")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(f"{site.title}\n{site.url}" if site.title != site.url else site.url)
            card.setFixedHeight(card_h)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.clicked.connect(lambda checked=False, url=site.url: self._open_url(url))

            inner = QVBoxLayout(card)
            inner.setContentsMargins(10, 5, 10, 5)
            inner.setSpacing(0)

            title = QLabel(fm.elidedText(site.title, Qt.TextElideMode.ElideRight, card_w - 20))
            title.setObjectName("navSiteCardTitle")
            domain = QLabel(fm.elidedText(_domain_of_url(site.url), Qt.TextElideMode.ElideRight, card_w - 20))
            domain.setObjectName("navSiteCardDomain")

            inner.addWidget(title)
            inner.addWidget(domain)

            grid.addWidget(card, i // cols, i % cols)

        self._nav_content_layout.addWidget(grid_container)
        self._nav_content_layout.addStretch()

    def _open_url(self, url: str):
        """用系统默认浏览器打开 URL（优先 os.startfile，回退 QDesktopServices）"""
        if not url:
            return
        # 优先使用 os.startfile（Windows 系统级打开，更稳定）
        try:
            os.startfile(url)
            return
        except Exception:
            pass
        # 回退到 QDesktopServices
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # ==================================================================
    # 软件导航页面（小卡片内嵌只读浏览：图标 + 名称，点击启动）
    # ==================================================================
    def _build_app_page(self):
        """
        构建软件导航小卡片页面。

        - 只读浏览：与主窗口 AppLauncherPage 共用同一份 config["apps"] 数据
        - 仅显示软件图标 + 软件名称（QToolButton 文字在图标下方）
        - 点击卡片 → 调用公共 launch_app() 异步启动（含 740 提权处理）
        - exe 失效的条目置灰禁用
        - 新增/编辑/删除仍只在主窗口导航页完成，本页无任何管理入口
        """
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        v.addWidget(self._make_page_title("apps", "软件导航"))

        # 可滚动区域展示软件网格（仅纵向滚动）
        self._app_scroll = QScrollArea()
        self._app_scroll.setWidgetResizable(True)
        self._app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._app_scroll.setObjectName("appScroll")
        self._app_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._app_content = QWidget()
        self._app_content.setObjectName("appContent")
        self._app_grid = QGridLayout(self._app_content)
        self._app_grid.setContentsMargins(0, 0, 4, 0)
        self._app_grid.setSpacing(6)
        # 网格从左上角开始排列：列不拉伸铺满容器宽度，
        # 按钮按行从左到右、从上到下逐一排列（修复居中/散开问题）
        self._app_grid.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._app_scroll.setWidget(self._app_content)

        v.addWidget(self._app_scroll, 1)

        hint = QLabel("点击卡片启动软件 | 编辑请打开主窗口")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _refresh_app_page(self):
        """刷新软件导航小卡片页面：重读 config apps 并重建网格。"""
        # 清空旧网格内容
        while self._app_grid.count():
            item = self._app_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 数据来源：宿主 ConfigManager（与主窗口导航页共用）
        apps = []
        if self._config_manager:
            raw = self._config_manager.get("apps", [])
            if isinstance(raw, list):
                apps = raw

        if not apps:
            empty = QLabel("暂无软件，请在主窗口软件导航中添加")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._app_grid.addWidget(empty, 0, 0)
            return

        # 图标/名称工具来自 widget_app_launcher（与主窗口卡片同一套）
        from src.widget_app_launcher import (
            extract_exe_icon, load_icon_pixmap, draw_placeholder_icon, launch_app,
        )

        # 小卡片内容区可用宽度：440 - 侧栏48 - 内容边距32 - 内层边距8 ≈ 352
        icon_px = self.mini_icon_size
        btn_size = mini_btn_size(icon_px)
        available = self.WINDOW_WIDTH - _TAB_BAR_WIDTH - 40
        cols = max(1, available // (btn_size + 6))

        placed = 0  # 实际放置计数（非法条目跳过不留洞）
        for app in apps:
            # 容错：字段残缺自动补默认（禁止闪退）
            if not isinstance(app, dict):
                continue
            name = str(app.get("name") or "未命名")
            exe_path = str(app.get("exe_path") or "")
            icon_path = str(app.get("icon_path") or "")
            valid = bool(exe_path) and os.path.exists(exe_path)

            # 图标：自定义图标 > exe 内嵌图标 > 占位图标
            pixmap = None
            if icon_path and os.path.exists(icon_path):
                pixmap = load_icon_pixmap(icon_path, icon_px)
            if pixmap is None or pixmap.isNull():
                pixmap = extract_exe_icon(exe_path, icon_px)
            if pixmap is None or pixmap.isNull():
                pixmap = draw_placeholder_icon(icon_px)

            btn = QToolButton()
            btn.setObjectName("appLaunchBtn")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(icon_px, icon_px))
            btn.setIcon(QIcon(pixmap))
            # 名称过长截断（超6字符省略）
            display = name if len(name) <= 6 else name[:5] + "…"
            btn.setText(display)
            btn.setFixedSize(btn_size, btn_size)
            btn.setToolTip(f"{name}\n{exe_path}")

            if valid:
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda checked=False, a=dict(app): launch_app(a, parent=self)
                )
            else:
                # exe 失效：置灰禁用，悬浮提示说明原因
                btn.setEnabled(False)
                btn.setToolTip(f"{name}\n{exe_path}\n可执行文件不存在")

            self._app_grid.addWidget(btn, placed // cols, placed % cols)
            placed += 1

    # ==================================================================
    # 临时素材页面（网格布局，支持拖拽取出）
    # ==================================================================
    def _build_asset_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        # 顶部标题 + 计数
        header = QHBoxLayout()
        header.addWidget(self._make_page_title("assets", "临时素材"))
        header.addStretch()
        self._asset_count_label = QLabel("共 0 个")
        self._asset_count_label.setObjectName("hintLabel")
        header.addWidget(self._asset_count_label)
        v.addLayout(header)

        # 可滚动区域
        self._asset_scroll = QScrollArea()
        self._asset_scroll.setWidgetResizable(True)
        self._asset_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._asset_scroll.setObjectName("assetScroll")

        self._asset_content = QWidget()
        self._asset_content.setObjectName("assetContent")
        self._asset_content_layout = QVBoxLayout(self._asset_content)
        self._asset_content_layout.setContentsMargins(0, 0, 0, 0)
        self._asset_content_layout.setSpacing(8)
        self._asset_scroll.setWidget(self._asset_content)

        v.addWidget(self._asset_scroll, 1)

        hint = QLabel("拖拽素材到任意位置即可取出 | 双击打开 | 右键菜单")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _refresh_asset_page(self):
        """刷新临时素材页面（数据变化后首次进入才调用，平常切页零开销）"""
        self._asset_page_dirty = False
        # 清除旧内容
        while self._asset_content_layout.count():
            item = self._asset_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        if not self._asset_manager:
            empty = QLabel("暂无素材，拖文件到悬浮球收录")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._asset_content_layout.addWidget(empty)
            self._asset_count_label.setText("共 0 个")
            return

        assets = self._asset_manager.get_all_assets()
        # 清理已删除素材的缩略图缓存
        valid_ids = {a.asset_id for a in assets}
        for key in list(self._asset_thumb_cache):
            if key not in valid_ids:
                del self._asset_thumb_cache[key]
        if not assets:
            empty = QLabel("暂无素材，拖文件到悬浮球收录")
            empty.setObjectName("hintLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._asset_content_layout.addWidget(empty)
            self._asset_count_label.setText("共 0 个")
            return

        # 网格布局：每行 4 个
        grid_container = QWidget()
        grid_container.setObjectName("assetGrid")
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        cols = 4
        for i, asset in enumerate(assets):
            item_widget = _AssetItemWidget(asset, theme=self._theme,
                                           thumb_cache=self._asset_thumb_cache)
            grid.addWidget(item_widget, i // cols, i % cols)
        # 补齐末行空白，让网格居中对齐
        total = len(assets)
        last_row = (total - 1) // cols
        if total % cols != 0:
            for j in range(total % cols, cols):
                placeholder = QWidget()
                placeholder.setFixedSize(72, 84)
                grid.addWidget(placeholder, last_row, j)

        self._asset_content_layout.addWidget(grid_container)
        self._asset_content_layout.addStretch()
        self._asset_count_label.setText(f"共 {total} 个")

    @staticmethod
    def _clear_layout(layout):
        """递归清除布局中的所有项"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                CardWindow._clear_layout(item.layout())

    def _delete_asset(self, asset_id: int):
        """删除素材（由 _AssetItemWidget 右键菜单调用）"""
        if not self._asset_manager:
            return
        if self._asset_manager.delete_asset(asset_id):
            self._refresh_asset_page()
            self.data_changed.emit("asset")

    def notify_assets_changed(self):
        """外部素材数据变化入口：置脏标记；卡片可见时立即重建。

        不可见时只置脏 —— 下次切到素材页/展开卡片时才重建，
        避免隐藏状态下白白解码缩略图。
        """
        self._asset_page_dirty = True
        if self.isVisible():
            self._refresh_asset_page()

    # ---------------- 样式 ----------------
    def _apply_pages_background(self):
        """让 QStackedWidget 的所有页面保持透明 —— 背景由 GlassPanel 统一负责。

        历史遗留：早期为了规避 grab() 快照黑底、切页透黑，曾给每个页面铺
        card_bg_solid 实色底；现在容器层已由 GlassPanel 画好玻璃填充/高光/噪点，
        页面再铺实色会把玻璃效果整块盖掉，因此必须改回透明。

        注意：QScrollArea 的 viewport 也要靠 QSS 设为透明（见 theme.py），
        否则在深色系统主题下会露出系统 Base 色（#1e1e1e）形成黑块。
        """
        stack = getattr(self, "_stack", None)
        if stack is None:
            return
        transparent = QColor(0, 0, 0, 0)
        for i in range(stack.count()):
            page = stack.widget(i)
            if page is None:
                continue
            page.setAutoFillBackground(False)
            pal = page.palette()
            pal.setColor(pal.ColorRole.Window, transparent)
            pal.setColor(pal.ColorRole.Base, transparent)
            page.setPalette(pal)
            # 提前 polish：从未显示过的页面在被 grab() 时，样式表还没作用到
            # 子控件，快照会拿到"未换肤"的渲染（实测输入框 placeholder
            # 颜色偏深，转场结束时会看见一次轻微跳变）。这里先趟一遍，
            # 保证任何页面第一次被拍照时都已是最终外观。
            page.ensurePolished()
            for child in page.findChildren(QWidget):
                child.ensurePolished()

    def _apply_style(self):
        self._container.setStyleSheet(get_card_window_qss(self._theme))
        # 玻璃壳配色（填充/描边/高光/噪点）由 GlassPanel 手绘，需同步
        if isinstance(self._container, GlassPanel):
            self._container.apply_theme(get_colors(self._theme))
        # P1：图标按钮（侧 Tab + 关闭钮）的位图颜色不在 QSS 管辖内
        for btn in self.findChildren(IconButton):
            btn.apply_theme(self._theme)
        # 页标题的自绘图标（IconLabel）同样不在 QSS 管辖内
        for ic in self.findChildren(IconLabel):
            ic.set_color(QColor(get_colors(self._theme)["primary"]))

    def apply_theme(self, theme_name: str):
        if theme_name not in ("light", "dark"):
            return
        if theme_name == self._theme:
            return
        self._theme = theme_name
        self._apply_style()
        self._apply_pages_background()
        self._menu.setStyleSheet(get_menu_qss(self._theme))
        # 同步指示器颜色
        colors = get_colors(theme_name)
        self._indicator.set_color(QColor(colors["primary"]))
        # 同步任务行委托配色（自绘，需手动刷新）
        if getattr(self, "_task_delegate", None) is not None:
            self._task_delegate.set_colors(colors)

    def showEvent(self, event):
        """窗口显示后初始化指示器位置，并刷新当前页数据"""
        super().showEvent(event)
        # 延迟一帧，确保布局完成后再计算位置
        QTimer.singleShot(0, self._init_indicator_position)
        # 刷新当前页数据（确保展开时显示最新内容）
        mode = getattr(self, '_last_mode', 'card')
        if mode == "fragment":
            self._refresh_fragment_page()
        elif mode == "task":
            self._refresh_task_list()
        elif mode == "note":
            self._load_temp_note()
        elif mode == "nav":
            self._refresh_nav_page()
        elif mode == "asset":
            if self._asset_page_dirty:      # 脏才重建，平常切页零开销
                self._refresh_asset_page()
        elif mode == "app":
            self._refresh_app_page()

    def _init_context_menu(self):
        self._menu = QMenu(self)
        self._menu.setStyleSheet(get_menu_qss(self._theme))
        exit_action = QAction("退出程序", self._menu)
        exit_action.triggered.connect(self._request_quit)
        self._menu.addAction(exit_action)

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    def eventFilter(self, obj, event):
        """Tab 切换已改为点击触发（见 _tab_buttons 的 clicked 连接）。

        保留 eventFilter 以兼容外部安装，但不再在鼠标进入时切页 ——
        否则鼠标从卡片左侧栏扫过会连续切换好几个 Tab。
        """
        return super().eventFilter(obj, event)

    # ---------------- 模式切换 ----------------
    def switch_mode(self, mode: str):
        """公开入口：切换卡片模式（fragment/task/note/nav/asset/app）

        供宿主与插件上下文调用——外部不要直接调 _switch_mode（私有成员契约）。
        """
        self._switch_mode(mode)

    # ---------------- 公开 API（D3 穿透清零 2026-09-30）----------------
    # 宿主（knowledge_ball）此前直接戳 _last_mode / _switch_mode /
    # _refresh_*_page 私有成员，这里升为公开特性/方法，私有成员保持不动。
    @property
    def current_mode(self) -> str:
        """当前卡片模式（fragment/card/task/note/nav/asset/app）。

        只读场景替代 ``._last_mode`` 读取；setter 仅裸置模式值（不触发
        切页/转场），供 popup_near 前预置落点（滚轮唤出直接落卡片页）。
        """
        return self._last_mode

    @current_mode.setter
    def current_mode(self, mode: str):
        self._last_mode = mode

    def reset_to_home(self):
        """公开入口：重置回默认首页（fragment）——安全退出路径用。"""
        self._last_mode = "fragment"
        self._switch_mode("fragment")

    def refresh_page(self, kind: str):
        """公开入口：按页种类刷新（task / nav / fragment / app）。

        与主窗口 refresh_page(name) 命名对齐；是否需要刷新（如仅可见时）
        由调用方判定，这里不做可见性过滤——语义与原私有刷新方法一致。
        """
        refreshers = {
            "task": self._refresh_task_list,
            "nav": self._refresh_nav_page,
            "fragment": self._refresh_fragment_page,
            "app": self._refresh_app_page,
        }
        refresher = refreshers.get(kind)
        if refresher is not None:
            refresher()

    @property
    def mini_icon_size(self) -> int:
        """小卡片软件导航页的图标边长（像素）。

        懒读配置：``set_config_manager`` 在构造之后才注入，所以不能放__init__；
        未注入时回退默认 36（与历史硬编码值一致）。取值已钳制在
        MINI_ICON_MIN..MAX，脏配置/越界值不会撑破 352px 的内容区。
        """
        if self._mini_icon_size is not None:
            return self._mini_icon_size
        raw = MINI_ICON_DEFAULT
        if self._config_manager is not None:
            try:
                raw = int(self._config_manager.get("app_mini_icon_size",
                                                  MINI_ICON_DEFAULT))
            except (TypeError, ValueError):
                raw = MINI_ICON_DEFAULT
        return max(MINI_ICON_MIN, min(MINI_ICON_MAX, raw))

    def apply_icon_size(self, value: int):
        """公开入口：设置页改「小卡片图标大小」后实时应用（D3 门面）。

        值未变则直接返回（避免长按 ± 时反复重建网格）；
        仅当小卡片正停在软件导航页且可见时才重建——不可见时下一次
        ``_apply_mode_init("app")`` 自然会读到新值，无需提前重建。
        """
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        value = max(MINI_ICON_MIN, min(MINI_ICON_MAX, value))
        if value == self._mini_icon_size:
            return
        self._mini_icon_size = value
        if self.isVisible() and self.current_mode == "app":
            self._refresh_app_page()

    def _switch_mode(self, mode: str):
        """切换 Tab 模式（即时切换，无页面转场）。

        曾经做过快照合成式滑动转场（三代实现，两代翻车史见 git），最终移除：
        转场期间真实 stack 停在源页索引且被隐藏，动画结束才 setCurrentIndex。
        快速连点时本方法读到**过期的 currentIndex**，有两类错位：
        ① 第二击点回转场源页 → idx==old_idx 走直切分支，旧转场 finished
           稍后仍把 stack 设成旧目标页 → 选中键与页面永久错位；
        ② 第二段转场从过期源页起跳 → 视觉跳变。
        页面即切 + 指示器 260ms 滑动（NavIndicator）已足够跟手，
        且同步切换从构造上杜绝「键与页错位」。
        """
        if mode != "note":
            self._note_save_timer.stop()
        self._last_mode = mode

        try:
            idx = _TAB_KEYS.index(mode)
        except ValueError:
            idx = 0

        # 更新 Tab 按钮选中状态
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == idx)

        # 滑动指示器到目标位置
        self._move_indicator_to(idx)

        self._stack.setCurrentIndex(idx)
        self._apply_mode_init(mode)

    def _apply_mode_init(self, mode: str):
        """模式切换标签的内容初始化（新页可见后调用）"""
        if mode == "fragment":
            self._refresh_fragment_page()
        elif mode == "task":
            self._refresh_task_list()
        elif mode == "note":
            self._load_temp_note()
        elif mode == "nav":
            self._refresh_nav_page()
        elif mode == "asset":
            if self._asset_page_dirty:      # 脏才重建，平常切页零开销
                self._refresh_asset_page()
        elif mode == "app":
            self._refresh_app_page()

    def _move_indicator_to(self, idx: int):
        """将选中指示器平滑滑动到指定 Tab 索引位置"""
        if not self._indicator_ready:
            # 指示器未就绪时（初始化阶段），直接设置位置不做动画
            return
        if idx < 0 or idx >= len(self._tab_buttons):
            return
        btn = self._tab_buttons[idx]
        # 计算指示器目标 Y 坐标（按钮中心对齐）
        btn_geom = btn.geometry()
        target_y = btn_geom.y() + btn_geom.height() // 2 - _TAB_INDICATOR_H // 2

        # NavIndicator 内置 260ms OutQuint 滑动动画
        self._indicator.move_to_y(target_y)

    def _init_indicator_position(self):
        """初始化指示器位置（在 showEvent 后调用）"""
        if not self._tab_buttons:
            return
        idx = self._stack.currentIndex()
        if idx < 0 or idx >= len(self._tab_buttons):
            return
        btn = self._tab_buttons[idx]
        btn_geom = btn.geometry()
        target_y = btn_geom.y() + btn_geom.height() // 2 - _TAB_INDICATOR_H // 2
        # 直接设置，不带动画
        self._indicator.set_indicator_y(target_y)
        # 标记指示器已就绪，允许后续动画
        self._indicator_ready = True

    def is_locked(self) -> bool:
        """卡片是否处于「拖动中」（拖动期间宿主不做悬停自动收回）。

        ★自愈（2026-09-30 修「小卡片偶发永久滞留 = 变成常驻」）：
        `_dragging` 只在 mouseReleaseEvent 里清零。一旦 release 丢失
        （拖动中途切到别的窗口 / 弹出模态 / 抬起点落到别处），标志位会
        **永久为 True**，后果是宿主的两条收回路径**双双失效**——
        `_check_hover_state` 与 `_card_watchdog_tick` 都以 is_locked()
        早退，卡片就此常驻，只有再按一次卡片才能解锁。

        这里用**物理按键状态**做交叉校验：标志位为 True 但左键实际并未
        按下 → 判定为丢失的 release，清标志并补发 card_drag_finished。
        正常拖动期间左键始终按下，走不到这条分支，交互零变化。
        """
        if not self._dragging:
            return False
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._dragging = False
            self.card_drag_finished.emit()
            return False
        return True

    # ---------------- 数据注入 ----------------
    def set_cards(self, cards):
        self._cards = cards if cards else []

    def set_task_manager(self, tm: TaskManager):
        self._task_manager = tm

    def set_note_manager(self, nm: NoteManager):
        self._note_manager = nm

    def set_nav_manager(self, nm: NavManager):
        self._nav_manager = nm

    def set_config_manager(self, cm):
        self._config_manager = cm
        # 换配置源 → 缓存的图标尺寸作废，让 mini_icon_size 按新源重读
        self._mini_icon_size = None

    def set_asset_manager(self, am):
        """注入临时素材管理器"""
        self._asset_manager = am
        self._asset_page_dirty = True   # 管理器就绪/替换 → 素材页需重建

    def set_fragment_manager(self, fm):
        """注入碎片管理器"""
        self._fragment_manager = fm

    def is_always_show(self) -> bool:
        """是否处于保持显示模式"""
        if self._config_manager:
            return self._config_manager.get("card_always_show", False)
        return False

    def _place_close_button(self):
        """把关闭按钮端正地放在卡片右上角内侧。

        按钮父对象是容器（440×340），坐标必须用**容器坐标系**：
        原来写成 CARD_MARGIN + WINDOW_WIDTH - 30，等于 426 → 按钮右边缘到 448，
        超出容器 8px 被裁掉一半，看起来"位置不正"。
        """
        m = self.CLOSE_BTN_MARGIN
        self._close_btn.move(self.WINDOW_WIDTH - self._close_btn.width() - m, m)
        self._close_btn.setVisible(self.is_always_show())
        self._close_btn.raise_()

    def _apply_always_show_margin(self):
        """常驻模式给内容区顶部留出关闭按钮的净空，避免压住首行内容"""
        if getattr(self, '_content_layout', None) is None:
            return
        base = self.CONTENT_MARGIN
        top = base + (self.CLOSE_BTN_RESERVE if self.is_always_show() else 0)
        self._content_layout.setContentsMargins(base, top, base, base)

    def _on_close_button_clicked(self):
        """保持显示模式下点击关闭按钮"""
        self.hide()
        self.card_closed.emit()

    def refresh_always_show_layout(self):
        """常驻模式开关变化时刷新关闭按钮与内容净空（设置页勾选即时生效）"""
        self._apply_always_show_margin()
        self._place_close_button()

    def has_shown_content(self) -> bool:
        return self._current_index >= 0

    # ---------------- 知识卡片 ----------------
    def show_next_random(self):
        if not self._cards:
            self._content_label.setText(
                '<div style="line-height:180%;color:#999;">'
                '（暂无知识卡片，请检查「知识库.docx」内容）</div>'
            )
            return
        if len(self._cards) > 1:
            new_index = self._current_index
            while new_index == self._current_index:
                new_index = random.randint(0, len(self._cards) - 1)
            self._current_index = new_index
        else:
            self._current_index = 0
        safe_text = html.escape(self._cards[self._current_index])
        self._content_label.setText(
            f'<div style="line-height:180%;">{safe_text}</div>'
        )

    def next_card(self):
        self.show_next_random()

    def step_card(self, direction: int):
        """按顺序上/下一张知识卡（球体滚轮用；点击球仍是随机换卡）

        与 show_next_random 的区别：这里按 _cards 的顺序循环前进/后退，
        让"滚轮上滚 = 上一张、下滚 = 下一张"有明确的前后关系。
        """
        if not self._cards:
            self.show_next_random()
            return
        total = len(self._cards)
        idx = self._seq_index
        if idx < 0 or idx >= total:
            idx = self._current_index if self._current_index >= 0 else 0
        self._seq_index = (idx + int(direction)) % total
        self._current_index = self._seq_index
        safe_text = html.escape(self._cards[self._current_index])
        self._content_label.setText(
            f'<div style="line-height:180%;">{safe_text}</div>'
        )

    def popup_near(self, ball_rect):
        self._switch_mode(self._last_mode)
        screen = get_screen_geometry()
        # 间距按「卡片内容边缘」计算，窗口四周的阴影留白不计入
        gap = 12
        x = ball_rect.left() - self.WINDOW_WIDTH - gap - self.CARD_MARGIN
        y = ball_rect.top() - (self.height() - ball_rect.height()) // 2
        from_left = True
        if x < screen.left():
            x = ball_rect.right() + gap - self.CARD_MARGIN
            from_left = False
        if y < screen.top():
            y = screen.top() + 10
        if y + self.height() > screen.bottom():
            y = screen.bottom() - self.height() - 10
        self.move(int(x), int(y))
        # 常驻显示模式：内容区顶部让出净空，关闭按钮端正落在卡片右上角
        self._apply_always_show_margin()
        self._place_close_button()
        self.show()
        self.raise_()
        self.activateWindow()
        # 弹入：从球所在方向滑入 14px 并淡入
        self._play_pop_in(14 if from_left else -14)

    def _play_pop_in(self, from_dx: int = 14):
        """弹入动画：淡入 + 从球的方向位移 14px 汇合（180~220ms）。

        这是"卡片从球里长出来"的关键一笔，替代原来的硬 show()。
        """
        end_pos = self.pos()
        start_pos = QPoint(end_pos.x() + from_dx, end_pos.y())

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(180)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(220)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setWindowOpacity(0.0)
        self.move(start_pos)
        fade.start()
        slide.start()
        self._pop_anims = [fade, slide]

    # ---------------- 日程任务 ----------------
    def _on_add_task(self):
        if not self._task_manager:
            return
        title = self._task_title_input.text().strip()
        if not title:
            return
        deadline = self._task_deadline.date().toString("yyyy-MM-dd")
        self._task_manager.add_task(title, "", deadline)
        self._task_title_input.clear()
        self._refresh_task_list()
        self.data_changed.emit("task")

    def _refresh_task_list(self):
        """按分组重建卡片任务列表（与主窗口同口径、同渲染）。"""
        self._task_list.clear()
        if not self._task_manager:
            return
        self._task_delegate.set_colors(get_colors(self._theme))
        self._task_delegate.clear_progress_except(self._task_anim_task_id)

        today = _date.today().isoformat()
        for group_key, tasks in self._task_manager.get_tasks_grouped(today):
            header_item = QListWidgetItem(
                f"{group_title(group_key, today)}  ·  {len(tasks)}")
            header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header_item.setData(KIND_ROLE, KIND_HEADER)
            self._task_list.addItem(header_item)
            for t in tasks:
                state, _delta = task_state(t.deadline, today)
                item = QListWidgetItem("")
                item.setData(Qt.ItemDataRole.UserRole, t.task_id)
                item.setData(KIND_ROLE, KIND_ROW)
                item.setData(ROLE_TITLE, t.title)
                # 已完成 → 行尾显示完成日期（不显示逾期等截止状态）
                item.setData(ROLE_REL,
                              format_completed_date(t.completed_at) if t.done
                              else format_relative_deadline(t.deadline, today))
                item.setData(ROLE_STATE, state)
                item.setData(ROLE_DONE, bool(t.done))
                self._task_list.addItem(item)

    # ---- 卡片任务：行内勾选 + 动画 + 撤销 ----
    def _task_anim_speed(self) -> float:
        """读取动画速度档位（与小卡片宿主 config 一致），异常回退 1.0。"""
        try:
            speed = float(self._config_manager.get("anim_speed", 1.0)) \
                if self._config_manager else 1.0
        except (TypeError, ValueError, AttributeError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    def _on_task_toggle_requested(self, task_id: int):
        """单击勾选框 / 标题 → 切换完成态并播放动画。"""
        if not self._task_manager:
            return
        task = self._task_manager.get_task(task_id)
        if task is None:
            return
        prev_done = bool(task.done)
        new_done = not prev_done
        if not self._task_manager.set_done(task_id, new_done):
            return

        if self._task_anim_task_id is not None \
                and self._task_anim_task_id != task_id:
            self._task_delegate.set_check_progress(
                self._task_anim_task_id, float(self._task_anim.endValue()))

        self._task_undo_target = (task_id, prev_done)

        self._task_anim.stop()
        self._task_rebuild_timer.stop()
        self._task_anim_task_id = task_id
        start = 0.0 if new_done else 1.0
        end = 1.0 if new_done else 0.0
        self._task_delegate.set_check_progress(task_id, start)
        # 时长口径统一走 src.motion（UI 强化方案 A1），此处只保留「至少 1ms」
        duration = max(1, motion.duration(CHECK_ANIM_MS, self._task_anim_speed()))
        self._task_anim.setDuration(duration)
        self._task_anim.setStartValue(start)
        self._task_anim.setEndValue(end)
        self._task_list.viewport().update()
        self._task_anim.start()

        self._task_undo_bar.show_for(task_id, task.title)
        self.data_changed.emit("task")

    def _on_task_anim_tick(self, value):
        if self._task_anim_task_id is None:
            return
        self._task_delegate.set_check_progress(self._task_anim_task_id, float(value))
        self._task_list.viewport().update()

    def _on_task_anim_finished(self):
        self._task_anim_task_id = None
        self._task_rebuild_timer.start(250)

    def _on_task_undo(self, task_id: int):
        """撤销最近一次完成操作。"""
        if not self._task_manager:
            return
        prev_done = False
        if self._task_undo_target and self._task_undo_target[0] == task_id:
            prev_done = bool(self._task_undo_target[1])
        self._task_manager.set_done(task_id, prev_done)
        self._task_undo_target = None
        self._stop_task_animations()
        self._refresh_task_list()
        self.data_changed.emit("task")

    def _stop_task_animations(self):
        """停止动画、清空进度与撤销状态（非动画路径的数据变更）。"""
        if self._task_anim is not None:
            self._task_anim.stop()
        if self._task_rebuild_timer is not None:
            self._task_rebuild_timer.stop()
        self._task_anim_task_id = None
        self._task_undo_target = None
        if getattr(self, "_task_undo_bar", None) is not None:
            self._task_undo_bar.hide()
        if getattr(self, "_task_delegate", None) is not None:
            self._task_delegate.clear_progress_except(None)

    def _on_task_context_menu(self, pos):
        item = self._task_list.itemAt(pos)
        if not item or not self._task_manager:
            return
        # 组标题行跳过
        if item.data(KIND_ROLE) != KIND_ROW:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return
        task = self._task_manager.get_task(task_id)
        if not task:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._menu.styleSheet())
        act_toggle = menu.addAction("取消完成" if task.done else "标记完成")
        act_edit = menu.addAction("编辑...")
        menu.addSeparator()
        act_delete = menu.addAction("删除")

        action = menu.exec(self._task_list.mapToGlobal(pos))
        if action == act_toggle:
            self._stop_task_animations()
            self._task_manager.set_done(task_id, not task.done)
            self._refresh_task_list()
            self.data_changed.emit("task")
        elif action == act_edit:
            self._edit_task(task)
        elif action == act_delete:
            self._stop_task_animations()
            self._task_manager.delete_task(task_id)
            self._refresh_task_list()
            self.data_changed.emit("task")

    def _edit_task(self, task):
        if not self._task_manager:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑任务")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setFixedSize(320, 200)

        form = QFormLayout(dialog)
        form.setContentsMargins(20, 20, 20, 16)
        form.setSpacing(10)

        title_edit = QLineEdit(task.title)
        note_edit = QLineEdit(task.note)
        note_edit.setPlaceholderText("备注（可选）")
        deadline_edit = QDateEdit()
        deadline_edit.setCalendarPopup(True)
        deadline_edit.setDisplayFormat("yyyy-MM-dd")
        if task.deadline:
            d = QDate.fromString(task.deadline, "yyyy-MM-dd")
            if d.isValid():
                deadline_edit.setDate(d)
            else:
                orig = f"（原始截止: {task.deadline}）"
                note_edit.setText(f"{task.note} {orig}" if task.note else orig)

        form.addRow("标题:", title_edit)
        form.addRow("备注:", note_edit)
        form.addRow("截止:", deadline_edit)

        form.addRow(make_dialog_buttons(dialog))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._task_manager.update_task(
                task.task_id,
                title_edit.text(),
                note_edit.text(),
                deadline_edit.date().toString("yyyy-MM-dd"),
            )
            self._refresh_task_list()
            self.data_changed.emit("task")

    # ---------------- 临时笔记 ----------------
    def _load_temp_note(self):
        if not self._note_manager:
            return
        self._loading_note = True
        try:
            temp = self._note_manager.get_temp_note()
            if temp is not None:
                self._current_note_id = temp.note_id
                self._note_edit.setPlainText(temp.content)
            else:
                self._current_note_id = None
                self._note_edit.clear()
        except Exception:
            self._current_note_id = None
            self._note_edit.clear()
        self._loading_note = False
        self._note_edit.setFocus()

    def _on_note_text_changed(self):
        if self._loading_note:
            return
        self._note_save_timer.start()

    def _on_save_note(self):
        if not self._note_manager:
            return
        content = self._note_edit.toPlainText()
        if self._current_note_id is None or self._note_manager.get_note(self._current_note_id) is None:
            try:
                temp = self._note_manager.get_temp_note()
                self._current_note_id = temp.note_id if temp else None
            except Exception:
                self._current_note_id = None
        if self._current_note_id is None:
            return
        self._note_manager.update_note(self._current_note_id, content, title=None)
        self.data_changed.emit("note")

    # ---------------- 退出 ----------------
    def _request_quit(self):
        self.request_quit.emit()

    # ==================================================================
    # 卡片拖动
    # ==================================================================
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            # 通知宿主机（悬浮球）此刻卡片与球的真实相对位置 —— 球侧据此跟随，
            # 否则直接拖卡片时球会失去参照被甩到卡片左上角（见 _on_card_moved）
            self.card_drag_started.emit()
            event.accept()

    def mouseMoveEvent(self, event):
        # 左键已松开却没收到 release（失焦 / 模态 / 跨窗释放）→ 立即解锁，
        # 不等到下一次 is_locked() 轮询（与 is_locked 的自愈同一根因）
        if self._dragging and not (event.buttons() & Qt.MouseButton.LeftButton):
            self._dragging = False
            self.card_drag_finished.emit()
            return
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self.card_moved.emit()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self._dragging = False
            if was_dragging:
                self.card_drag_finished.emit()
            event.accept()
