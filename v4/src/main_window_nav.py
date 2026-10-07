# -*- coding: utf-8 -*-
"""MainWindow 左栏导航机器 mixin（D2 上帝类拆分，拆分计划-wiring-2026-10-05 T03）。

从 ``src/main_window.py`` 整段外迁：侧栏构建（_build_side_bar）、侧栏宽度
分割手柄、分组展开/折叠动画、导航顺序持久化与重排、拖拽换位实时让位、
选中指示条（_build_side_bar → _move_nav_indicator）。

**mixin 铁律**（拆分计划 §1.2 / §7 T03）：
  1. 不得声明任何 ``pyqtSignal``（一个 QObject 派生链只允许一份信号表）；
  2. 不得定义 ``__init__``（初始化全部留在主类 MainWindow.__init__，
     ``_nav_*`` 状态字段由主类持有，mixin 只经 ``self`` 消费）；
  3. 禁止 import 宿主模块 ``src.main_window`` —— 导航三张钉死表
     （NAV_PAGE_INDEX / NAV_PAGE_TITLES / NAV_PAGE_TITLES_FIXED）必须
     以字面量留在 main_window.py 模块级（test_icons 钉死），mixin 经
     主类类属性透传（``self.NAV_PAGE_INDEX``，同一 dict 对象）访问；
  4. 三个拖拽不变量锚点方法（``_clear_nav_drag_lift`` /
     ``_on_nav_drag_started`` / ``_restore_nav_layout``）**不在本文件** ——
     test_nav_drag_invariants 以纯 AST 只扫 main_window.py 文本，
     它们必须以 FunctionDef 留在主类体内。
"""

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QSize, pyqtSignal, QObject,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
    QSequentialAnimationGroup,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout,
    QButtonGroup, QFrame, QApplication, QScrollArea, QSpacerItem, QSizePolicy,
    QStackedWidget,
)

from src.theme import get_colors
from src.constants import DEFAULT_THEME
from src import icon_render
from src.icons import NAV_ICON
from src.app_version import APP_VERSION
from src.config import sanitize_nav_order, DEFAULT_NAV_ORDER
from src.nav_layout import (
    NAV_GROUPS, NAV_GROUP_TITLES, NAV_FIXED_ITEM_GROUP, NAV_FIXED_ITEM_ORDER,
    NAV_DEFAULT_EXPANDED, group_of, is_plugin_key,
    sanitize_expanded_groups, toggle_group, split_by_group,
    reorder_within_group,
)
from src.glass import NavIndicator, NavGroupHeader
from src import smooth_scrollbar
from src.controls import SmoothButton

# 侧栏导航图标的渲染尺寸（逻辑像素；与 13px 字号的视觉重量对齐）
NAV_ICON_SIZE = 16

# 非拖拽固定项的文案（设置 / 使用说明）。与 NAV_PAGE_TITLES 分开存放，
# 因为这两项不在 NAV_PAGE_INDEX 里 —— 它们由 nav_layout.NAV_FIXED_ITEM_GROUP
# 归入 system 组，键名是宿主内部逻辑名（不是物理页面索引），
# 混进 NAV_PAGE_TITLES 会让"遍历功能页键"的代码多出两个需要特判的项。

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


class NavChromeMixin:
    """左栏导航机器（构建 / 分组动画 / 顺序持久化 / 拖拽换位 / 指示条）。

    铁律见模块 docstring：无 pyqtSignal、无 __init__、只经 self 消费
    宿主状态、禁止 import 宿主模块。
    """

    # ---- 宿主（MainWindow）依赖面裸注解：仅类型声明，不建类属性、
    # ---- 零运行时行为、MRO 零变化（pyright reportAttributeAccessIssue 消音）。
    _theme: str
    _config: Any
    _stack: QStackedWidget
    _nav_shift_anims: dict
    _nav_anim_ms: Callable[[float], int]
    _switch_page: Callable[[int], None]
    sender: Callable[[], QObject]
    isVisible: Callable[..., bool]
    NAV_PAGE_INDEX: dict
    NAV_PAGE_TITLES: dict
    NAV_PAGE_TITLES_FIXED: dict
    SIDE_BAR_WIDTH: int
    SIDE_BAR_MIN_W: int
    SIDE_BAR_MAX_W: int

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
        # V2（2026-10-07 交互视觉清单）：滚动条状态机——闲置 6px 半透明 →
        # 悬停/滚动中 8px 加深 → 静止 600ms 回落（替换 navScroll 专属 QSS）
        smooth_scrollbar.install_on(self._nav_scroll)
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
            btn = self._make_nav_button(self.NAV_PAGE_TITLES[key],
                                        self.NAV_PAGE_INDEX[key], key,
                                        icon_name=NAV_ICON.get(key))
            self._nav_btns[key] = btn

        # 设置（物理索引 6）：固定，不参与拖动换位
        self._settings_btn = self._make_nav_button(
            self.NAV_PAGE_TITLES_FIXED["settings"], 6,
            icon_name=NAV_ICON["settings"])
        self._nav_fixed_btns["settings"] = self._settings_btn
        # 软件导航按钮别名（历史引用点保留）
        self._app_launcher_btn = self._nav_btns.get("apps")

        # 使用说明按钮（与其它页面一致，参与页面切换，索引 8）
        self._help_btn = self._make_nav_button(
            self.NAV_PAGE_TITLES_FIXED["help"], 8,
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
        4. ★ 2026-10-06 逐拍同步内容部件高度（_sync_nav_content_height 挂
           valueChanged）：溢出态下 widgetResizable 跟随滞后一拍，逐帧
           maximumHeight 增长会把布局在旧部件高度上过约束一拍，亏空被挤
           到上方条目 → 被点击组头逐帧上下弹跳（用户报「导航栏自动加长
           时分组震荡」）。每拍写入后立刻校高，亏空窗口归零。
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
            # 动画档位关闭：直接到终态（保持"0 动画"设置下的零延迟手感）。
            # 终态写入后同拍校高：无动画路径同样存在"慢一拍"窗口
            for w in items:
                w.setMaximumHeight(16777215 if expand else 0)
                w.setVisible(expand)
            self._sync_nav_content_height()
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
            # ★ 逐拍同步（防震荡，机制见 _sync_nav_content_height 文档）：
            #   实测 valueChanged 触发时属性**已经写入**（0 不匹配），此刻
            #   取 sizeHint 是含本拍的最新值 —— 在布局激活前把内容部件校
            #   到位，"部件高度慢一拍"的过约束窗口就不存在了。折叠同挂：
            #   收起方向的富余虽然只造成"滚动条晚一拍"这种轻微失真，一并
            #   校掉更干净。
            prop.valueChanged.connect(self._sync_nav_content_height)
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
        # 解除约束后 sizeHint 理论上不变（行高==sizeHint），但为防测量
        # 偏差留下"慢一拍"窗口，这里同拍再校一次（幂等，无变化即空转）
        self._sync_nav_content_height()
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
        # 进行中的展开/折叠动画必须先落终态：动画把条目的 maximumHeight
        # 压在 0~row_h 之间，此时重排会按"半截高度"排布，而且动画还会
        # 继续改几何 → 重排结果被动画覆盖。
        # ★ 必须在 insertWidget **之后**：落终态会走 _apply_nav_visibility
        #   → setVisible(True)，此时按钮必须已带父级 —— 首次铺开时按钮
        #   还是无父级的裸控件，提前 setVisible 会把它们变成真实顶层
        #   窗口，在屏幕左上角闪现一排小黑窗（2026-09-29 启动闪窗排查）。
        # ★ 必须在 activate **之前**（2026-10-05 用户实测：插件停用→再
        #   启用后导航键重叠错乱）：新注册的插件键此前从未 show 过，而
        #   QBoxLayout 的 sizeHint **不统计隐藏项** —— 不先显形，activate
        #   拿到的内容高度少了这一行；布局把条目压扁挤进旧高度（相邻行
        #   重叠），落定动画再把错位坐标钉成终点值，之后没有任何布局再
        #   跑，错乱就此定格。
        self._stop_nav_group_anims()
        # ★ QScrollArea(widgetResizable) 对内容部件的跟随 resize 是**异步**
        #   的——条目增减后 sizeHint 立刻变化，但 _nav_content 还握着旧
        #   高度。先把内容部件校到 widgetResizable 将会给的高度，activate
        #   才能按真实高度铺开，落定动画的终点才是最终位置。
        self._sync_nav_content_height()
        cv.activate()
        self._nav_free_layout = False

    def _sync_nav_content_height(self):
        """把导航滚动内容部件立刻校到 widgetResizable 迟早会给它的高度。

        QScrollArea 的 widgetResizable 跟随是异步的（事件循环里才发生），
        而 _relayout_nav 的调用方往往紧接着取几何（落定动画终点、指示条
        对位、拖拽槽位冻结），等不得。规则与 QScrollArea 一致：内容高度
        = max(布局 sizeHint，视口高度)——内容矮于视口时被拉伸铺满，超出
        时出滚动条。宽度不动（视口宽度由滚动区自己同步，横向不滚）。

        ★ 2026-10-06 起它还是分组展开/折叠动画的**逐拍同步器**：每拍
        maximumHeight 写入后由 valueChanged 直接调用本方法（见
        _animate_nav_group），把"部件高度比 sizeHint 慢一拍"的窗口压到
        零 —— 没有这个同步，溢出态下每拍增长都会让布局在旧部件高度上
        过约束一拍，亏空被 qGeomCalc 挤到上方条目上再弹回，被点击组头
        就会逐帧上下弹跳（用户报「导航栏自动加长时分组震荡」的根因）。
        注意不能用"起播前一次 resize 预长"代替：widgetResizable 会在
        Resize 事件里**同步**把部件钳回 max(sizeHint, 视口)，预长当场被
        吞掉（离屏探针实锤），只有跟在属性写入之后的同拍校高才站得住。
        """
        sa = getattr(self, "_nav_scroll", None)
        content = getattr(self, "_nav_content", None)
        if sa is None or content is None:
            return
        hint = content.sizeHint().height()
        if hint <= 0:
            return
        target = max(hint, sa.viewport().height())
        if content.height() != target:
            content.resize(content.width(), target)

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

    # 注：``_nav_anim_ms`` 保留在主类 MainWindow（test_motion 的接线钉子
    # 要求它是 main_window.py 文本内的唯一入口；mixin 经 self 调用）。

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
        if key not in self.NAV_PAGE_INDEX:
            return
        # 顺序表可能含尚未注册的插件 key（无按钮无页面），Ctrl+N 跳过
        if key.startswith("plugin:") and key not in self._nav_btns:
            return
        self._switch_page(self.NAV_PAGE_INDEX[key])

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
        # ★ 占位 spacer 留在布局里，这里**不要**摘（2026-10-05 用户实测：
        #   导航键往组外拉动一松手，导航栏闪动一下、像"重新分布的残影"）。
        #   位置未变路径（往组外拖被钳回组边缘槽位是典型）走
        #   _animate_nav_drop —— 落定动画跑完才 _restore_nav_layout 交还
        #   布局；这期间 _clear_nav_drag_lift 的 updateGeometry() 投递的
        #   LayoutRequest 一旦触发 activate()，布局处于"脏 + 被拖组按钮
        #   未交还"态：该组塌缩、下方整栏上移，动画结束再弹回 = 闪动
        #   （离屏实测 settings 444→249 持续约 180ms 后复原）。spacer 顶住
        #   空间则中途任何布局激活都无害；_relayout_nav 开头本来就会摘它，
        #   _apply_nav_order / _restore_nav_layout 两条收尾路径都经过那里。
        #   同理也不把按钮先插回旧顺序的布局：否则邻居会被 Layout 拽回
        #   旧槽位再滑一次（松手处二次抖动）。_nav_free_layout 保持 True
        #   直到 _restore_nav_layout 真正交还布局。
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

