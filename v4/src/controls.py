# -*- coding: utf-8 -*-
"""
====================================================================
可复用控件  -  controls
====================================================================
给设置页用的轻量控件，统一「玻璃质感 + 悬停/按压反馈」：

  · Stepper   数字步进器：− ［可输入的值］单位 ＋

为什么不用 QSpinBox：
  · 原生上下箭头又小又难点，且和玻璃主题的圆角风格不搭；
  · 这里换成两个 30×30 的圆角按钮，悬浮高亮、按下回弹、到边界自动置灰；
  · 按住不放连续加减，且越按越快（400ms 起跳，先 80ms 一档，约 1.2s 后 45ms）；
  · 数值可直接点进去键入，回车或失焦时按上下限截断。

为什么不用 QSlider（滑条）：
  · 滑条最容易被滚轮误改——鼠标滚设置页时滑条一「吃掉」滚轮就静默跳值；
  · 步进器只在数值框有焦点时才响应滚轮，且 ± 按钮点击意图明确、可长按连发。

小数值设置（如动画速度 1.3x）：内部仍用整数（50~200），通过 divisor/decimals
换算显示（divisor=100、decimals=1 → 130 显示为「1.3」），对外 valueChanged
发出的仍是**内部整数**，调用方按 divisor 换算即可。

样式全部走 theme.py 的 QSS（objectName：stepBtn / stepValue / fieldLabel），
主题切换自动跟随，这里不写死任何颜色。
====================================================================
"""

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QPointF, QPoint, QRectF, Qt, QTimer,
    QVariantAnimation, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QDoubleValidator, QIntValidator,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QPropertyAnimation

from src.app_paths import get_screen_geometry
from src.constants import (RADIUS_CTL, RADIUS_CHIP, RADIUS_PANEL, UNDO_BAR_MS)
from src.glass import _to_color   # QSS 风格颜色字符串（含 rgba）→ QColor
from src.theme import DEFAULT_THEME, get_colors
from src import icon_render
from src import motion


# ====================================================================
# 丝滑化按钮基类（UI 丝滑化清单 S2，2026-10-02）
# ====================================================================
# Qt 的 QSS 引擎不支持 CSS ``transition``：``:hover`` / ``:pressed`` 的
# 背景色是一帧跳变。所以「丝滑」不能靠改 QSS，只能自绘插值 —— 本节
# 提供的 SmoothButton 在原生 QSS 之上叠加一层过渡色，QSS 侧只需把
# 命名按钮 ``:hover`` / ``:pressed`` 的**背景色**删掉（文字/边框/焦点环
# 仍归 QSS 管），两端点色一一对照记在 _SMOOTH_OVERLAYS 注释里。
# 改 theme.py 的 hover/pressed 端点或这里任何一个 spec，两边必须同步。
_PROGRESS_EPS = 0.005   # 低于该进度的叠加/位移直接省略（省一帧无意义重绘）
_PRESS_SHIFT = 1.0      # 按下下沉 px —— 绘制级位移，绝不碰 margin/padding
_PRESS_SCALE = 0.98     # 按下微缩

# objectName → (hover 端点 spec, press 端点 spec)；spec = (主题 token 名, 255 上限不透明度)
# 端点逐一对照 theme.py 里已删除的 QSS 背景（丝滑化清单 §2.1 的落地契约）：
#   · 不透明端点（$primary_hover / $primary / $danger …）→ (token, 255)
#   · 半透明 a08/a12/a18/a30 端点 → (primary, 255*比例)
#   · secondaryBtn 基底已改 $surface 实底（UI 重构 01），hover 叠 primary 18%
#     淡染 ≈ 网页 ghost 按钮；textBtn 文字钮 hover 用 primary 20% 更弱的淡染
#   · None = 该状态不换底色（维持现状，仅吃按下位移）
#   · None 键 = 未命名 / 未收录按钮的兜底，等价旧的全局 QPushButton:hover/:pressed
_SMOOTH_OVERLAYS = {
    "secondaryBtn":     (("primary", 18), None),
    "textBtn":          (("primary", 20), ("primary", 31)),
    "dangerBtn":        (("danger", 26), ("danger", 46)),
    # 小卡片碎片页页脚两个按钮（UI 重构 06）：随页脚一起从「行内彩色小胶囊」
    # 改成高仿真 .mini-foot .ib 的「裸露图标钮」—— 常态透明无边框，hover 只
    # 叠一层淡底。故端点从实底（danger/primary 255）换成 surface_2/3 实色。
    # 注：theme.py 里 #fragDelBtn:hover 的历史 `color: white` 未同步删除
    # （那是文字色；纯图标钮的图标色由 IconButton 的 QIcon::Active 位图负责，
    #  QSS color 不参与绘制），保留以免误触 test_smooth_buttons 的既有契约。
    "fragDelBtn":       (("danger", 26), ("danger", 46)),
    # 小卡片右上角关闭钮（UI 重构 06）：随页眉一起改成高仿真 .mini-head .x 的
    # 中性图标钮 —— 常态透明、hover 只叠一层浅面，红底实心已是历史。
    "cardCloseBtn":     (("surface_2", 255), ("surface_3", 255)),
    # 页脚新增的「编辑」钮，与复制钮同为中性 hover；未收录会掉进兜底
    # （primary_hover 实底）→ hover 变绿，与本页脚风格冲突。
    "fragEditBtn":      (("surface_2", 255), ("surface_3", 255)),
    "iconBtn":          (("primary", 31), ("primary", 46)),
    "iconBtn_danger":   (("danger", 26), ("danger", 46)),   # iconBtn[danger="true"] 属性变体
    "sideTabIconBtn":   (("primary", 31), ("primary", 46)),
    "settingsNavBtn":   (("primary", 20), ("primary", 31)),
    "stepBtn":          (("primary", 46), ("primary", 77)),
    "navSiteCard":      (("primary", 46), ("primary", 77)),
    # 2026-10-02 悬停配色优化：modeBtn 常态是 ghost（$primary_a12），hover
    # 端点从 primary_hover 实底改淡染 —— 否则 ghost→实底突跳，且未选中态
    # 主色文字叠 primary_hover 底几乎不可读；淡染端点叠在选中态实底上与
    # 底同色（视觉 no-op，选中钮不再跳色）。tableOpenBtn 同理保持 ghost 语系。
    "modeBtn":          (("primary", 46), ("primary", 77)),
    "taskAddBtn":       (("primary_hover", 255), ("primary_pressed", 255)),
    "nextBtn":          (("primary_hover", 255), ("primary_pressed", 255)),
    # 同上：碎片页页脚复制钮 → 中性 hover 淡底
    "fragCopyBtn":      (("surface_2", 255), ("surface_3", 255)),
    "tableOpenBtn":     (("primary", 46), ("primary", 77)),
    "undoUndoBtn":      (("primary_lite", 255), ("primary_lite", 255)),
    # S4：侧栏导航行。端点对照 navBtn:hover/:pressed 被删的 a08/a18；
    # 拖拽态（[dragging="true"] 的 a18 底）仍归 QSS（静态状态，无过渡需求）。
    "navBtn":           (("primary", 20), ("primary", 46)),
    None:               (("primary_hover", 255), ("primary_pressed", 255)),
}

# overlay 圆角（对照 theme.py 各选择器的 border-radius）；未收录的走全局
# RADIUS_CTL。UI 重构 01 起取 constants.RADIUS_* 与 QSS 的 $r_* 同源 ——
# theme.py 圆角四档收敛后这里的像素值全部随之更新，两边不再可能漂移。
_OVERLAY_RADIUS = {
    "modeBtn": RADIUS_CTL, "cardCloseBtn": RADIUS_CHIP,
    "nextBtn": RADIUS_CTL, "iconBtn": RADIUS_PANEL,
    "sideTabIconBtn": RADIUS_PANEL, "settingsNavBtn": RADIUS_PANEL,
    "tableOpenBtn": RADIUS_CTL, "undoUndoBtn": RADIUS_CTL,
    "fragCopyBtn": RADIUS_CTL, "fragDelBtn": RADIUS_CTL,
    "fragEditBtn": RADIUS_CTL,
    "navBtn": RADIUS_PANEL, "secondaryBtn": RADIUS_CTL,
    "textBtn": RADIUS_CTL,
}
_DEFAULT_RADIUS = RADIUS_CTL


class SmoothButton(QPushButton):
    """在原生 QSS 外观之上叠加 hover / press 过渡的按钮基类（清单 S2）。

    原理（清单 §2.1 的 overlay 插补）：先照常画原生 QSS 外观，再用两个
    0→1 的动画属性叠加一层过渡色 —— ``hp``（hover 进度）/ ``pp``（press
    进度）。时长与曲线统一走 motion token（``fast`` / OutCubic），
    ``reduce_motion`` 开启时直接落终态（motion.duration 返回 0）。

    按下反馈（B2）走**绘制级** -1px 下沉 + 0.98 微缩，由 ``pp`` 驱动 ——
    margin/padding 方案已在项目里被证明有害（按下态 polish 会把 sizeHint
    算大并永久缓存，拖拽后行高 +1px，见 theme.py 注释与
    test_nav_drag_invariants.py）。

    QSS 侧约定（与 theme.py 联动，单改一边即回归）：
      · 命名按钮 ``:hover`` / ``:pressed`` 的背景色已从 QSS 删除，overlay
        端点色逐一对照被删旧值（见 _SMOOTH_OVERLAYS 注释）；
      · 文字色 / 边框 / 焦点环仍归 QSS（文档口径：hover 即时起变化，
        只补「到位过程」）；
      · primaryBtn 渐变 hover 属 B5（后置）：QSS 渐变跳变保留，
        overlay 为 (None, None)，仅享受按下位移。

    动画可打断重定向（清单 §6.4）：复用同一个 QPropertyAnimation，
    每次 retarget 都从当前值出发 ``stop() → start()``，连续快速进出
    不会排队。换主题不用通知 —— 端点色在 paint 时按当前主题解析。
    按设置页 ``anim_speed`` 档位缩放：main_window 启动与变更广播时调
    :meth:`set_speed`（类级属性，全进程按钮共享）。
    """

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（main_window 启动 / 设置页变更时调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hp = 0.0
        self._pp = 0.0
        self._hover_anim = None
        self._press_anim = None
        self._child_free = None   # None = 未测定（子控件可能晚于构造加入）

    # ---- 动画属性（QPropertyAnimation 写入端）----
    def _get_hp(self):
        return self._hp

    def _set_hp(self, value):
        self._hp = max(0.0, min(1.0, float(value)))
        self.update()

    def _get_pp(self):
        return self._pp

    def _set_pp(self, value):
        self._pp = max(0.0, min(1.0, float(value)))
        self.update()

    hp = pyqtProperty(float, _get_hp, _set_hp)
    pp = pyqtProperty(float, _get_pp, _set_pp)

    # ---------------- 状态驱动 ----------------
    def _glide(self, prop, attr, anim_attr, target):
        """把 attr 插值到 target（0/1）。可打断：从当前值重定向，不排队。"""
        current = getattr(self, attr)
        if abs(target - current) <= _PROGRESS_EPS:
            return
        ms = motion.eased_ms("fast", self._speed)
        if ms <= 0:
            # reduce_motion 总闸 / 档位归零：直接落终态（语义是瞬显，不是缩短）
            setattr(self, attr, float(target))
            self.update()
            return
        anim = getattr(self, anim_attr)
        if anim is None:
            anim = QPropertyAnimation(self, prop, self)
            anim.setEasingCurve(getattr(QEasingCurve.Type, motion.EASE["out"]))
            setattr(self, anim_attr, anim)
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    def enterEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.isDown():
            self._glide(b"pp", "_pp", "_press_anim", 1.0)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._pp > _PROGRESS_EPS:
            self._glide(b"pp", "_pp", "_press_anim", 0.0)

    def cancel_press_feedback(self):
        """显式收回按下反馈（pp 回 0）。

        给"吞掉 release"的手势路径用 —— _NavButton 拖拽落定时只 setDown(False)
        而不调用 super().mouseReleaseEvent，QPropertyAnimation 不会被通知，
        pp 会卡在 1（按钮永久下沉 + 叠色）。"""
        if self._pp > _PROGRESS_EPS:
            self._glide(b"pp", "_pp", "_press_anim", 0.0)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        hover_spec, press_spec = self._overlay_specs()
        spec, progress = None, 0.0
        if self._pp > _PROGRESS_EPS:
            spec, progress = press_spec, self._pp
        elif self._hp > _PROGRESS_EPS:
            spec, progress = hover_spec, self._hp
        if spec is None:
            if self._pp > _PROGRESS_EPS:
                # 有按下位移但该按钮 press 不换底色（secondaryBtn/primaryBtn）
                self._paint_native_transformed()
            else:
                super().paintEvent(event)
            return
        painter = None
        if self._pp > _PROGRESS_EPS:
            painter = self._paint_native_transformed()
        else:
            super().paintEvent(event)
            painter = QPainter(self)
        self._paint_overlay(painter, spec, progress)
        painter.end()

    def _paint_native_transformed(self):
        """按 ``pp`` 进度下沉 + 微缩后画原生 QSS 外观，返回未 end() 的 painter。

        只对**无子控件**的纯文本/图标按钮生效 —— navSiteCard 这类内嵌
        QLabel 的复合按钮，子控件不走本 paintEvent，跟着缩放会跟背景错位。
        """
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        painter = QPainter(self)
        if self._is_child_free():
            factor = 1.0 - (1.0 - _PRESS_SCALE) * self._pp
            cx, cy = self.width() / 2.0, self.height() / 2.0
            painter.translate(cx, cy + _PRESS_SHIFT * self._pp)
            painter.scale(factor, factor)
            painter.translate(-cx, -cy)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, opt,
                                 painter, self)
        return painter

    def _paint_overlay(self, painter, spec, progress):
        """叠一层过渡色：端点 = spec，不透明度按 progress 插值。"""
        token, max_alpha = spec
        color = self._overlay_color(token)
        color.setAlpha(int(max_alpha * progress))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = _OVERLAY_RADIUS.get(self.objectName(), _DEFAULT_RADIUS)
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)

    # ---------------- 取色与映射 ----------------
    def _overlay_specs(self):
        name = self.objectName()
        if name == "iconBtn" and self.property("danger") == "true":
            return _SMOOTH_OVERLAYS["iconBtn_danger"]
        return _SMOOTH_OVERLAYS.get(name, _SMOOTH_OVERLAYS[None])

    def _overlay_color(self, token):
        colors = get_colors(self._detect_theme() or DEFAULT_THEME)
        return QColor(str(colors.get(token, colors["primary"])))

    def _is_child_free(self):
        if self._child_free is None:
            self._child_free = not self.findChildren(QWidget)
        return self._child_free

    def _detect_theme(self):
        """依次找：宿主的 ``_theme`` → 父控件链上最近窗口的 ``_theme``。

        后一半给"运行期动态创建、又没接宿主管线"的按钮兜底：创建时沿
        parentWidget 向上爬，爬到主窗/卡片窗/便签窗的 ``_theme`` 就取对
        配色。只认 "light"/"dark"，中途对象挂了同名属性也不误判。
        """
        host = getattr(self, "_host", None)
        if host is not None:
            t = getattr(host, "_theme", None)
            if t in ("light", "dark"):
                return t
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None


class Stepper(QWidget):
    """数字步进器（替代 QSpinBox / QSlider 的 ± 按钮组）"""

    valueChanged = pyqtSignal(int)

    BTN_SIZE = 30
    EDIT_WIDTH = 58
    SUFFIX_WIDTH = 26
    HOLD_DELAY_MS = 400      # 按住多久开始连发
    HOLD_FAST_MS = 80        # 连发初速
    HOLD_FASTER_MS = 45      # 加速后的速度
    HOLD_FAST_TICKS = 12     # 连发多少次后加速

    def __init__(self, minimum: int, maximum: int, value: int,
                 suffix: str = "", step: int = 1, parent=None,
                 divisor: int = 1, decimals: int = 0):
        super().__init__(parent)
        self._min = int(minimum)
        self._max = int(maximum)
        self._step = max(1, int(step))
        self._divisor = max(1, int(divisor))    # 内部值 = 显示值 × divisor
        self._decimals = max(0, int(decimals))  # 显示几位小数（0 = 纯整数）
        self._value = self._clamp(value)
        self._hold_dir = 0
        self._hold_tick = 0
        self._suppress_click = False

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        # 字形：P1 起改自绘 minus / plus 图标（icons.py）。原先 U+2212/ASCII+
        # 是在系统字体里挑"最平衡的一对"（全角 －/＋ 在雅黑下不是过淡就是过粗），
        # 自绘后彻底摆脱字体差异 —— 缺字形环境与正常环境同一张脸，
        # QSS #stepBtn 的 font-size/font-weight 自然失效（无害保留）。
        self._btn_minus = self._make_btn("minus", "减小（可长按连续调整）")
        self._edit = QLineEdit(self._fmt(self._value))
        self._edit.setObjectName("stepValue")
        self._edit.setFixedSize(self.EDIT_WIDTH, self.BTN_SIZE)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setValidator(self._make_validator())
        self._edit.setToolTip("可直接输入数值，回车确认（范围 %s ~ %s）"
                              % (self._fmt(self._min), self._fmt(self._max)))
        self._edit.editingFinished.connect(self._commit_edit)
        self._edit.returnPressed.connect(self._commit_edit)
        self._btn_plus = self._make_btn("plus", "增大（可长按连续调整）")

        self._suffix = QLabel(suffix)
        self._suffix.setObjectName("fieldLabel")
        self._suffix.setFixedWidth(self.SUFFIX_WIDTH)
        self._suffix.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        h.addWidget(self._btn_minus)
        h.addWidget(self._edit)
        h.addWidget(self._suffix)
        h.addWidget(self._btn_plus)
        h.addStretch(1)

        # 单击（release 时触发一次）与长按连发：长按后抑制收尾那一次 click
        self._btn_minus.clicked.connect(lambda: self._on_click_step(-1))
        self._btn_plus.clicked.connect(lambda: self._on_click_step(1))
        self._btn_minus.pressed.connect(lambda: self._begin_hold(-1))
        self._btn_plus.pressed.connect(lambda: self._begin_hold(1))
        for btn in (self._btn_minus, self._btn_plus):
            btn.released.connect(self._end_hold)

        self._repeat = QTimer(self)
        self._repeat.setSingleShot(True)
        self._repeat.timeout.connect(self._on_repeat)

        self._sync_buttons()

    # ---------------- 内部 ----------------
    def _make_btn(self, icon_name: str, tip: str) -> "IconButton":
        """± 钮：30×30 固定尺寸、objectName=stepBtn（QSS 契约保持）

        （返回类型加引号：IconButton 定义在本文件更靠后处，注解延迟求值）
        """
        btn = IconButton(icon_name, size=self.BTN_SIZE, icon_size=13,
                         object_name="stepBtn", tooltip=tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _clamp(self, v) -> int:
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = self._min
        return max(self._min, min(self._max, v))

    def _fmt(self, v: int) -> str:
        """内部整数 → 显示文本（decimals=0 时原样输出）"""
        if self._decimals <= 0:
            return str(int(v))
        return f"{v / self._divisor:.{self._decimals}f}"

    def _parse(self, text: str) -> int:
        """显示文本 → 内部整数；解析失败时保持当前值"""
        text = (text or "").strip()
        if not text:
            return self._value
        try:
            if self._decimals <= 0:
                return int(text)
            return int(round(float(text) * self._divisor))
        except (TypeError, ValueError):
            return self._value

    def _make_validator(self):
        """按显示精度选校验器：整数用 QIntValidator，小数用 QDoubleValidator"""
        if self._decimals > 0:
            return QDoubleValidator(self._min / self._divisor,
                                    self._max / self._divisor,
                                    self._decimals, self)
        return QIntValidator(self._min, self._max, self)

    def _sync_buttons(self):
        """到边界时把对应按钮置灰，避免"点了没反应"的困惑"""
        self._btn_minus.setEnabled(self._value > self._min)
        self._btn_plus.setEnabled(self._value < self._max)

    def _commit_edit(self):
        self.setValue(self._clamp(self._parse(self._edit.text())))

    def _on_click_step(self, direction: int):
        if self._suppress_click:
            self._suppress_click = False
            return
        self.step_by(direction)

    def _begin_hold(self, direction: int):
        self._hold_dir = direction
        self._hold_tick = 0
        self._suppress_click = False
        self._repeat.start(self.HOLD_DELAY_MS)

    def _end_hold(self):
        self._repeat.stop()
        # 长按期间已经连发过 → 丢弃收尾的这一次 click，避免多走一格
        self._suppress_click = self._hold_tick > 0
        self._hold_dir = 0

    def _on_repeat(self):
        if not self._hold_dir:
            return
        self._hold_tick += 1
        self.step_by(self._hold_dir)
        self._repeat.start(self.HOLD_FAST_MS
                           if self._hold_tick <= self.HOLD_FAST_TICKS
                           else self.HOLD_FASTER_MS)

    # ---------------- 对外 ----------------
    def value(self) -> int:
        return self._value

    def setValue(self, value: int):
        """设置数值；仅在真正变化时发信号（与 QSpinBox 行为一致）"""
        new = self._clamp(value)
        if new == self._value:
            self._edit.setText(self._fmt(self._value))
            return
        self._value = new
        self._edit.setText(self._fmt(new))
        self._sync_buttons()
        self.valueChanged.emit(new)

    def step_by(self, direction: int):
        self.setValue(self._value + direction * self._step)

    def setRange(self, minimum: int, maximum: int):
        self._min, self._max = int(minimum), int(maximum)
        self._edit.setValidator(self._make_validator())
        self.setValue(self._value)
        self._sync_buttons()

    def wheelEvent(self, event):
        """滚轮微调：仅当数值框有焦点时生效，否则交给外层滚动区。

        这样鼠标滚设置页时不会被无意改动；有焦点时按 step 走一档
        （而非 1 个内部单位），与点击 ± 的粒度保持一致。
        """
        if not self._edit.hasFocus():
            event.ignore()
            return
        self.step_by(1 if event.angleDelta().y() > 0 else -1)
        event.accept()


class UndoBar(QWidget):
    """误勾撤销提示条（A3）。

    作为任务面板 / 小卡片任务页的**浮动子控件**：底部居中、``raise_()``、
    ``UNDO_BAR_MS`` 后自动隐藏；含「撤销」按钮，命中后 emit
    ``undo_clicked(task_id)``。

    - 两处入口（大窗口任务页 / 小卡片任务页）共用本组件，避免重复实现；
    - **只保留最近一次操作**：新的 ``show_for`` 会覆盖旧的并重置计时；
    - 颜色一律走 theme.py 的 ``#undoBar / #undoBarLabel / #undoUndoBtn``
      QSS（由宿主容器的样式表级联），组件不写死颜色。
    """

    undo_clicked = pyqtSignal(int)   # 点击撤销，参数为 task_id
    TIMEOUT_MS = UNDO_BAR_MS
    HEIGHT = 36
    BOTTOM_GAP = 12                  # 距父控件底部间距
    SIDE_GAP = 12                    # 距父控件左右最小间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_id = None
        self.setObjectName("undoBar")
        self.setFixedHeight(self.HEIGHT)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(10)

        self._label = QLabel("")
        self._label.setObjectName("undoBarLabel")
        h.addWidget(self._label)

        self._btn = SmoothButton("撤销")
        self._btn.setObjectName("undoUndoBtn")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setToolTip("撤销刚才的完成操作")
        self._btn.clicked.connect(self._on_clicked)
        h.addWidget(self._btn)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.setVisible(False)

    # ---------------- 对外 ----------------
    def show_for(self, task_id: int, title: str = ""):
        """显示撤销条（覆盖上一次），并在超时后自动隐藏。"""
        self._task_id = int(task_id)
        self._label.setText(f"已完成「{title}」")
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(self.TIMEOUT_MS)

    def update_position(self):
        """宿主尺寸变化时重新贴底居中（由宿主的 resizeEvent 调用）。"""
        if self.isVisible():
            self._reposition()

    # ---------------- 内部 ----------------
    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        hint_w = self.sizeHint().width()
        max_w = max(120, parent.width() - self.SIDE_GAP * 2)
        width = min(hint_w, max_w)
        self.setFixedWidth(width)
        x = (parent.width() - width) // 2
        y = parent.height() - self.HEIGHT - self.BOTTOM_GAP
        self.move(max(0, x), max(0, y))

    def _on_clicked(self):
        task_id = self._task_id
        self.hide()
        if task_id is not None:
            self.undo_clicked.emit(int(task_id))


class ToggleSwitch(QAbstractButton):
    """设置页开关（替代 QCheckBox 的视觉形式）：胶囊轨道 + 滑动圆点。

    - 选中 = 主题主色轨道 + ``on_primary`` 深色圆点 —— 主色是浅色，
      压白点对比不足（与 QSS 对比度护栏同一原则，自绘里同样遵守）；
    - 未选中 = 中性灰轨道 + 白色圆点，悬停时轨道略加深；
    - 切换有 120ms 滑动动画；**无动画进行时按 isChecked() 直接渲染**，
      因此 blockSignals 下批量 setChecked（refresh/恢复默认）也能立即
      显示正确状态，不依赖信号；
    - 颜色走 theme.get_colors，宿主面板在主题切换时调用 set_theme()。
    """

    TRACK_W, TRACK_H = 44, 24
    KNOB = 18
    MARGIN = 3
    ANIM_MS = 120

    def __init__(self, checked: bool = False, theme: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._theme = theme or DEFAULT_THEME
        self._progress = 1.0 if checked else 0.0
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._on_toggled)

    # ---------------- 对外 ----------------
    def set_theme(self, theme: str):
        """主题切换时更新配色（由宿主面板统一调用）"""
        if theme and theme != self._theme:
            self._theme = theme
            self.update()

    # ---------------- 焦点态（UI 强化方案 A4）----------------
    def focusInEvent(self, event):
        # 焦点环由 paintEvent 自绘（QSS 管不到 QAbstractButton 的自绘内容）
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    # ---------------- 内部 ----------------
    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value):
        self._progress = float(value)
        self.update()

    def paintEvent(self, event):
        colors = get_colors(self._theme)
        w, h = self.TRACK_W, self.TRACK_H
        running = self._anim.state() == QVariantAnimation.State.Running
        progress = self._progress if running else (1.0 if self.isChecked() else 0.0)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # 轨道
        if self.isChecked():
            track = QColor(colors["primary"])
        else:
            track = QColor(colors["text_disabled"])
            if self.underMouse():
                track = track.darker(112)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)

        # 圆点
        if self.isChecked():
            knob = QColor(colors["on_primary"])
        else:
            knob = QColor("#FFFFFF")
        span = w - self.KNOB - self.MARGIN * 2
        x = self.MARGIN + progress * span
        p.setBrush(knob)
        p.drawEllipse(QPointF(x + self.KNOB / 2.0, h / 2.0),
                      self.KNOB / 2.0, self.KNOB / 2.0)

        # 焦点环（A4）：控件尺寸固定 44×24 不能变，所以环**内缩**绘制，
        # 不扩占位（扩了会让设置页每一行高 2px，牵动大量几何断言）。
        # 环色用 $focus_ring 而不是 $primary —— 未选中态的轨道是
        # $text_disabled（浅灰），$primary 压上去同样只有约 1.6:1。
        if self.hasFocus():
            ring_px = 1.6
            inset = ring_px / 2.0
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(colors["focus_ring"]), ring_px))
            p.drawRoundedRect(
                QRectF(inset, inset, w - ring_px, h - ring_px),
                (h - ring_px) / 2.0, (h - ring_px) / 2.0)
            p.setPen(Qt.PenStyle.NoPen)
        p.end()


class ScreenToast(QWidget):
    """屏幕级顶部通知：独立顶层窗口，主窗口隐藏/最小化时依然可见。

    背景：此前的操作反馈（碎片自动清理、截图收录素材、链接拦截警告等）
    分别挂在主窗口（容器内子控件）与悬浮球（球上方气泡）上，宿主一隐藏
    提示就跟着消失。统一改为屏幕顶部居中的顶层浮窗 —— 位置与任何窗口
    的可见性无关。

    - 全程单例：新提示直接替换旧提示，不堆叠；
    - 鼠标完全穿透、不抢焦点（WA_TransparentForMouseEvents +
      WA_ShowWithoutActivating）、不进任务栏（Tool）；
    - 配色走 get_colors 跟随主题，底色比窗口内玻璃更实（alpha 232，
      要压住任意壁纸保证可读）。
    """

    MARGIN_X, PAD_Y = 18, 11
    TOP_GAP = 20              # 距屏幕顶部的距离
    FLOAT_PX = 10             # 进出场位移幅度：进场自下上浮 / 出场向下沉没

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（main_window 经 controls.set_ui_speed 调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    _instance = None

    @classmethod
    def show_msg(cls, text: str, theme: str = "", ms: int = 2600):
        """显示一条屏幕顶部通知（模块级入口，单例复用）"""
        inst = cls._instance
        if inst is None:
            inst = cls()
            cls._instance = inst
        if theme:
            inst._theme = theme
        inst.popup(text, max(800, int(ms)))
        return inst

    def __init__(self):
        super().__init__(None)
        self._theme = DEFAULT_THEME
        self._text = ""
        self._fade_target = 1.0
        self._final_y = self.TOP_GAP
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 进出场 = 透明度 + 位移两条动画并行（丝滑化清单 F6：上浮淡入 /
        # 下沉淡出）。时长/曲线走 motion token（base / OutCubic）；
        # reduce_motion 下 motion.eased_ms 返回 0 → popup/_fade_out 直接落终态。
        easing = getattr(QEasingCurve.Type, motion.EASE["out"])
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setEasingCurve(easing)
        self._fade_anim.finished.connect(self._on_anim_done)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setEasingCurve(easing)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    # ---------------- 显示 ----------------
    def popup(self, text: str, ms: int):
        self._text = text
        font = QFont(self.font())
        font.setPointSize(10)
        fm = QFontMetrics(font)
        screen = get_screen_geometry()
        max_w = max(240, int(screen.width() * 0.7))
        w = min(max_w, fm.horizontalAdvance(text) + self.MARGIN_X * 2 + 8)
        h = fm.height() + self.PAD_Y * 2
        self.setFixedSize(int(w), int(h))
        x = screen.left() + (screen.width() - int(w)) // 2
        self._final_y = screen.top() + self.TOP_GAP
        self.move(x, self._final_y)

        self._timer.stop()
        self._fade_anim.stop()
        self._pos_anim.stop()
        self._fade_target = 1.0
        duration = max(1, motion.eased_ms("base", self._speed))
        if motion.duration(180, self._speed) <= 0:
            # reduce_motion：瞬显终态，不排队任何动画
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            self._timer.start(ms)
            return
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._pos_anim.setDuration(duration)
        self._pos_anim.setStartValue(self.pos() + QPoint(0, self.FLOAT_PX))
        self._pos_anim.setEndValue(self.pos())
        self._fade_anim.start()
        self._pos_anim.start()
        self._timer.start(ms)

    def _fade_out(self):
        self._fade_anim.stop()
        self._pos_anim.stop()
        self._fade_target = 0.0
        duration = max(1, motion.eased_ms("base", self._speed))
        if motion.duration(180, self._speed) <= 0:
            self.setWindowOpacity(0.0)
            self.hide()
            return
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._pos_anim.setDuration(duration)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(self.pos() + QPoint(0, self.FLOAT_PX))
        self._fade_anim.start()
        self._pos_anim.start()

    def _on_anim_done(self):
        if self._fade_target <= 0.0 and self.windowOpacity() <= 0.02:
            self.hide()


def set_ui_speed(speed):
    """设置页 anim_speed 档位广播的**模块级单一入口**：一次调用同步
    controls 内所有走 motion 缩放的动效控件（现有 SmoothButton /
    ScreenToast，后续 S4 若新增同样在此登记）。main_window 启动与
    anim_speed_changed 时调用，替代逐类 set_speed。"""
    SmoothButton.set_speed(speed)
    ScreenToast.set_speed(speed)


# 滚轮步长（px）：Qt 默认按字体行高步进，列表滚动一格一格"卡顿"；
# 28px ≈ 一行半的视差，配合像素级滚动是网页般的连续手感（清单 L3）。
SMOOTH_SCROLL_STEP_PX = 28


def tune_list_scrolling(view):
    """列表/滚动区滚轮手感统一（丝滑化清单 L3）：像素级滚动 + 固定步长。

    只调 QAbstractItemView 的滚动属性，不碰内容与选择行为；QSS 的
    滚动条样式不受影响。素材网格已有按单元格高度的定制步长（assets_panel），
    不走本入口。"""
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.verticalScrollBar().setSingleStep(SMOOTH_SCROLL_STEP_PX)

    def paintEvent(self, event):
        colors = get_colors(self._theme)
        # glass_fill 是 QSS rgba 字符串，QColor 不认 —— 必须经 _to_color 解析
        fill = _to_color(colors["glass_fill"])
        fill.setAlpha(232)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # primary_a30 是 QSS rgba 字符串，QColor 不认（无效色 → 画成黑环）
        p.setPen(QPen(_to_color(colors["primary_a30"]), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        p.setPen(QColor(colors["text"]))
        f = QFont(self.font())
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


# ====================================================================
# 图标小部件（UI 强化方案 A2）
# ====================================================================
class IconLabel(QWidget):
    """只画一个自绘图标的小部件（零文字，尺寸即图标尺寸）。

    为什么不直接 ``QLabel.setPixmap``：那样得自己处理设备像素比，换主题
    还要重新生成 pixmap 再 setPixmap；自绘只在 paintEvent 里取色，
    ``set_color`` 后 update() 一次就够（跟随主题的成本从"重建资源"降到
    "重画一次"）。
    """

    def __init__(self, name, size=18, color=None, parent=None):
        super().__init__(parent)
        self._name = name
        self._size = int(size)
        self._color = (QColor(color) if color is not None
                       else QColor(140, 148, 166))
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @property
    def icon_name(self) -> str:
        return self._name

    @property
    def color(self) -> QColor:
        """当前取色（只读；换主题链路断言用）。"""
        return QColor(self._color)

    def set_icon_name(self, name):
        self._name = name
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        icon_render.paint_icon(
            p, self._name, QRectF(0.0, 0.0, float(self._size), float(self._size)),
            self._color)
        p.end()


class IconButton(SmoothButton):
    """自绘图标按钮（UI 强化方案 A2 的 P1 批次）：纯图标或「图标+文字」。

    ★ 最重要的设计约束：**objectName 原样保留站点既有值**。QSS 里
    ``#iconBtn`` / ``#cardCloseBtn`` / ``#stepBtn`` / ``#sideTabIconBtn`` /
    ``#secondaryBtn`` 等契约（背景、悬停、按压、焦点环）全部继续由
    theme.py 管辖，本类只负责「画哪个图标、当前用什么颜色」—— 也就是
    QSS 的 ``color:`` 管不到的 QIcon 位图。

    基类 SmoothButton（丝滑化清单 S2）补齐 hover/press 的背景过渡与
    按下位移 —— 端点色由 objectName 在 _SMOOTH_OVERLAYS 里解析。

    颜色三态（对应 QSS 的 color / :hover 色 / :checked 色）：
      · 常态 ``off_color``   缺省 ``text_secondary``
      · 悬停 ``hover_color`` 缺省 ``primary``（``danger=True`` 时 ``danger``）
      · 选中 ``on_color``    缺省 ``primary``（checkable 按钮的 On 态位图）
    取值可以是**主题 token 名**（"danger" —— 每次 refresh 按当前主题解析，
    换主题自动跟随），也可以是 "#RRGGBB" 字面量（主题无关，如危险钮悬停
    的白）。禁用态一律 ``text_disabled``（Stepper 到边界置灰同源）。

    换主题：宿主带 ``theme_changed`` 信号就自动订阅（PageTitle 同款
    ``callable`` 守卫）；没有信号的对话框/便签由宿主显式调
    :meth:`apply_theme`，或接受构造时取色（短命对象，可接受）。
    """

    def __init__(self, icon_name, size=0, icon_size=16, object_name=None,
                 host=None, tooltip="", text="", checkable=False,
                 off_color=None, hover_color=None, on_color=None,
                 danger=False, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._icon_size = max(4, int(icon_size))
        self._host = host
        self._off_spec = off_color or "text_secondary"
        self._hover_spec = hover_color or ("danger" if danger else "primary")
        self._on_spec = on_color or "primary"
        self._theme_override = None
        self._hovered = False
        if object_name:
            self.setObjectName(object_name)
        if size:
            # 纯图标钮（标题栏/侧 Tab/Stepper）：站点既有固定尺寸原样保留
            self.setFixedSize(int(size), int(size))
        if checkable:
            self.setCheckable(True)
        if danger:
            # 与 QSS `QPushButton#iconBtn[danger="true"]:hover` 的属性选择器配套
            self.setProperty("danger", "true")
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_icon()
        # ★ 仅当 ``theme_changed`` 真的可 connect 时才订阅（PageTitle 同款）：
        #   测试替身常写成 ``SimpleNamespace(theme_changed=...)``，属性存在
        #   但没有 ``connect``，直接订阅会 AttributeError。
        signal = getattr(host, "theme_changed", None)
        if callable(getattr(signal, "connect", None)):
            signal.connect(self._on_theme_changed)

    # ---------------- 对外 ----------------
    @property
    def icon_name(self) -> str:
        return self._icon_name

    def set_icon_name(self, name):
        """换图形（🌙/☀️、⛶/❐ 这类一钮两态的动态按钮用）。"""
        self._icon_name = name
        self._refresh_icon()

    def apply_theme(self, theme=None):
        """按主题重取图标颜色。宿主没有 ``theme_changed`` 信号时由宿主显式
        调（GlassDialog.apply_theme / CardWindow._apply_style / 便签窗）。"""
        if theme:
            self._theme_override = theme
        self._refresh_icon()

    # ---------------- 内部 ----------------
    @staticmethod
    def _resolve(spec, colors):
        """token 名 → 当前主题色值；其它（#RRGGBB / QColor）原样放行。"""
        if isinstance(spec, str) and spec in colors:
            return colors[spec]
        return spec

    def _refresh_icon(self):
        theme = (self._theme_override
                 or self._detect_theme() or DEFAULT_THEME)
        colors = get_colors(theme)
        off = self._resolve(self._off_spec, colors)
        if self._hovered:
            off = self._resolve(self._hover_spec, colors)
        self.setIcon(icon_render.icon(
            self._icon_name, self._icon_size, off,
            on_color=self._resolve(self._on_spec, colors),
            disabled_color=colors["text_disabled"]))

    def _on_theme_changed(self, _theme=None):
        self._refresh_icon()

    # ---------------- 悬停变色 ----------------
    # QSS 的 :hover 只能改 color:，管不到 QIcon 位图 —— 悬停时把 Off 态
    # 位图换成 hover 色重设一次（pixmap 按「名称+尺寸+颜色」缓存，进出
    # 各只付一次生成成本，之后是字典查找）。
    def enterEvent(self, event):
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)


class EmptyState(QWidget):
    """列表空态引导（UI 强化方案 A3）：自绘图标 + 标题 + 提示 + 可选动作钮。

    两种用法：
      ①普通部件进布局——fragments 的 holder_grid 叠放、assets 的 _stack
      空态页、plugins 的外层 vbox；
      ②:meth:`attach_to` 叠成列表控件的**覆盖层**——notes / tasks /
      knowledge / 软件导航用这种：列表本体一行代码不动，不碰任何
      ``itemAt(0)`` / ``count()`` 定值断言，也不进会被整体销毁重建的
      布局（软件导航的 ``_clear_layout`` 会清空整张网格）。

    QSS 契约沿用 fragments 旧版 ``_EmptyState``：标题 ``sectionLabel`` /
    提示 ``hintLabel`` / 动作钮 ``secondaryBtn``；图标走 A2 自绘体系
    （缺字形环境不再出豆腐块）。★内部**禁止**出现 ``pageTitle`` 字样 ——
    verify_icons_render 的 H 段断言每页恰好一个 PageTitle。
    """

    def __init__(self, icon, title, hint, action_text=None, on_action=None,
                 icon_size=40, object_name=None, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(8)
        v.addStretch()

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        self.icon_label = IconLabel(icon, icon_size)
        icon_row.addWidget(self.icon_label)
        icon_row.addStretch()
        v.addLayout(icon_row)

        self._title = QLabel(title)
        self._title.setObjectName("sectionLabel")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._title)

        self._desc = QLabel(hint)
        self._desc.setObjectName("hintLabel")
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setWordWrap(True)
        v.addWidget(self._desc)

        self._action = None
        if action_text:
            self._action = SmoothButton(action_text)
            self._action.setObjectName("secondaryBtn")
            self._action.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_action is not None:
                self._action.clicked.connect(on_action)
            self._action.setVisible(False)
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(self._action)
            btn_row.addStretch()
            v.addLayout(btn_row)

        v.addStretch()
        if object_name:
            self.setObjectName(object_name)
        # 无动作钮的覆盖层对鼠标全透明：盖在列表上时右键菜单/滚轮照常
        # 落到列表本体（notes/tasks/knowledge 的空态没有可点的东西）
        if self._action is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                              True)

    # ---------------- 对外 ----------------
    @property
    def action(self):
        """动作钮（fragments 旧版 ``_empty_state._action`` 契约的承接）"""
        return self._action

    def set_state(self, icon, title, hint, show_action=False):
        """切换空态内容（如 fragments 的「全空」⇄「筛选无结果」两态）"""
        self.icon_label.set_icon_name(icon)
        self._title.setText(title)
        self._desc.setText(hint)
        if self._action is not None:
            self._action.setVisible(show_action)

    def attach_to(self, host):
        """叠成 ``host``（列表控件/容器）的覆盖层：随其 resize 贴合。

        初始隐藏，由调用方按数据有无显式 ``setVisible`` / ``raise_``。
        """
        self.setParent(host)
        self.setGeometry(host.rect())
        host.installEventFilter(self)
        self.hide()
        self.raise_()

    def apply_theme(self, theme=None):
        """图标取色跟主题（宿主 apply_theme 链里顺手调一次）。"""
        self.icon_label.set_color(
            get_colors(theme or DEFAULT_THEME)["text_placeholder"])

    # ---------------- 内部 ----------------
    def eventFilter(self, obj, event):
        if (obj is self.parentWidget()
                and event.type() == QEvent.Type.Resize):
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, event)


class PageTitle(QWidget):
    """页面标题：自绘图标 + 标题文字。

    ★ 唯一的设计约束：**文字仍是 ``QLabel#pageTitle``**。图标是独立子控件，
    不参与文字度量 —— 因此 ``theme.py`` 里 ``QLabel#pageTitle`` 的 QSS 与
    所有既有几何/样式断言口径零变化（把标题换成"一个自绘整体"会同时改掉
    文字度量与 QSS 命中，是没必要的风险）。

    换主题：宿主带 ``theme_changed`` 信号就自动订阅（与 ``settings_panel``
    订阅宿主信号同一范式）。★ 订阅用的是 **PageTitle 自身的绑定方法**而不是
    lambda —— 绑定方法让 Qt 在本部件析构时自动断开；lambda 没有接收者上下文，
    宿主活得比面板久时会在已析构对象上回调（段错误的经典成因）。
    """

    def __init__(self, icon_name, text, host=None, icon_size=18,
                 spacing=8, parent=None):
        super().__init__(parent)
        self._host = host
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        self.icon = IconLabel(icon_name, icon_size)
        lay.addWidget(self.icon)
        self.label = QLabel(text)
        self.label.setObjectName("pageTitle")
        lay.addWidget(self.label)
        self.apply_theme()
        # ★ 仅当 ``theme_changed`` 真的可 connect 时才订阅。
        #   只判 ``is not None`` 不够：测试替身常写成
        #   ``SimpleNamespace(theme_changed=SimpleNamespace(emit=...))`` —— 属性
        #   存在但没有 ``connect``（2026-09-30 test_ui_scale.py 的三个 error
        #   即此）。判 ``callable`` 连 ``connect=None`` 的形态也一并挡住。
        #   与 settings_panel._on_ui_scale_changed 的 ``callable`` 守卫同口径。
        signal = getattr(host, "theme_changed", None)
        if callable(getattr(signal, "connect", None)):
            signal.connect(self._on_theme_changed)

    # ---- 便捷访问（省得调用方层层 .label）----
    def text(self) -> str:
        return self.label.text()

    def set_text(self, text):
        self.label.setText(text)

    def set_icon_name(self, name):
        self.icon.set_icon_name(name)

    @property
    def icon_name(self) -> str:
        return self.icon.icon_name

    # ---- 主题 ----
    def _on_theme_changed(self, _theme=None):
        self.apply_theme()

    def apply_theme(self, theme=None):
        """按主题重取图标颜色。宿主没有 ``theme_changed`` 信号时由调用方显式调
        （例如 ``AppLauncherPage._apply_style()`` 已经在换主题时被宿主调用）。"""
        theme = theme or getattr(self._host, "_theme", None) or DEFAULT_THEME
        self.icon.set_color(get_colors(theme)["text"])
