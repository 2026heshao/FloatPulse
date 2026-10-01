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
    QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QDoubleValidator, QIntValidator,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QPropertyAnimation

from src.app_paths import get_screen_geometry
from src.constants import UNDO_BAR_MS
from src.glass import _to_color   # QSS 风格颜色字符串（含 rgba）→ QColor
from src.theme import DEFAULT_THEME, get_colors
from src import icon_render


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

        self._btn = QPushButton("撤销")
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
    FADE_IN_MS, FADE_OUT_MS = 140, 200

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
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(self.FADE_IN_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

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
        self.move(screen.left() + (screen.width() - int(w)) // 2,
                  screen.top() + self.TOP_GAP)

        self._timer.stop()
        self._anim.stop()
        self._fade_target = 1.0
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self):
        self._anim.stop()
        self._fade_target = 0.0
        self._anim.setDuration(self.FADE_OUT_MS)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_done(self):
        if self._fade_target <= 0.0 and self.windowOpacity() <= 0.02:
            self.hide()

    def paintEvent(self, event):
        colors = get_colors(self._theme)
        # glass_fill 是 QSS rgba 字符串，QColor 不认 —— 必须经 _to_color 解析
        fill = _to_color(colors["glass_fill"])
        fill.setAlpha(232)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(colors["primary_a30"]), 1))
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


class IconButton(QPushButton):
    """自绘图标按钮（UI 强化方案 A2 的 P1 批次）：纯图标或「图标+文字」。

    ★ 最重要的设计约束：**objectName 原样保留站点既有值**。QSS 里
    ``#iconBtn`` / ``#cardCloseBtn`` / ``#stepBtn`` / ``#sideTabIconBtn`` /
    ``#secondaryBtn`` 等契约（背景、悬停、按压、焦点环）全部继续由
    theme.py 管辖，本类只负责「画哪个图标、当前用什么颜色」—— 也就是
    QSS 的 ``color:`` 管不到的 QIcon 位图。

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

    def _detect_theme(self):
        """依次找：宿主的 ``_theme`` → 父控件链上最近窗口的 ``_theme``。

        后一半是给"运行期动态创建、又没接宿主管线"的按钮兜底（插件页的
        规则行删除钮等）：它们创建时沿着 parentWidget 向上爬，爬到主窗/
        卡片窗/便签窗的 ``_theme`` 就立即取对配色，不必等下一次主题广播。
        只认 "light"/"dark"，中途对象挂了同名属性也不误判。
        """
        if self._host is not None:
            t = getattr(self._host, "_theme", None)
            if t in ("light", "dark"):
                return t
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None

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
            self._action = QPushButton(action_text)
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
