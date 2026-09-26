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
    QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QDoubleValidator, QIntValidator,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)
from PyQt6.QtCore import QPropertyAnimation

from src.app_paths import get_screen_geometry
from src.constants import UNDO_BAR_MS
from src.glass import _to_color   # QSS 风格颜色字符串（含 rgba）→ QColor
from src.theme import DEFAULT_THEME, get_colors


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

        # 字形：用 U+2212（真正的减号）+ ASCII 加号，配 17px/700（见 theme.py）。
        # 实测全角「－」在雅黑下会掉成一条又短又淡的横线，全角「＋」又偏粗，
        # 两者不匹配；这组在 30×30 按钮里最平衡（对比图见 docs/）。
        self._btn_minus = self._make_btn("\u2212", "减小（可长按连续调整）")
        self._edit = QLineEdit(self._fmt(self._value))
        self._edit.setObjectName("stepValue")
        self._edit.setFixedSize(self.EDIT_WIDTH, self.BTN_SIZE)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setValidator(self._make_validator())
        self._edit.setToolTip("可直接输入数值，回车确认（范围 %s ~ %s）"
                              % (self._fmt(self._min), self._fmt(self._max)))
        self._edit.editingFinished.connect(self._commit_edit)
        self._edit.returnPressed.connect(self._commit_edit)
        self._btn_plus = self._make_btn("+", "增大（可长按连续调整）")

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
    def _make_btn(self, text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("stepBtn")
        btn.setFixedSize(self.BTN_SIZE, self.BTN_SIZE)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
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
