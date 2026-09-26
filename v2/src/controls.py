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

样式全部走 theme.py 的 QSS（objectName：stepBtn / stepValue / fieldLabel），
主题切换自动跟随，这里不写死任何颜色。
====================================================================
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget


class Stepper(QWidget):
    """数字步进器（替代 QSpinBox 的 ± 按钮组）"""

    valueChanged = pyqtSignal(int)

    BTN_SIZE = 30
    EDIT_WIDTH = 58
    SUFFIX_WIDTH = 26
    HOLD_DELAY_MS = 400      # 按住多久开始连发
    HOLD_FAST_MS = 80        # 连发初速
    HOLD_FASTER_MS = 45      # 加速后的速度
    HOLD_FAST_TICKS = 12     # 连发多少次后加速

    def __init__(self, minimum: int, maximum: int, value: int,
                 suffix: str = "", step: int = 1, parent=None):
        super().__init__(parent)
        self._min = int(minimum)
        self._max = int(maximum)
        self._step = max(1, int(step))
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
        self._edit = QLineEdit(str(self._value))
        self._edit.setObjectName("stepValue")
        self._edit.setFixedSize(self.EDIT_WIDTH, self.BTN_SIZE)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setValidator(QIntValidator(self._min, self._max, self))
        self._edit.setToolTip("可直接输入数值，回车确认（范围 %d ~ %d）"
                              % (self._min, self._max))
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
            v = int(v)
        except (TypeError, ValueError):
            v = self._min
        return max(self._min, min(self._max, v))

    def _sync_buttons(self):
        """到边界时把对应按钮置灰，避免"点了没反应"的困惑"""
        self._btn_minus.setEnabled(self._value > self._min)
        self._btn_plus.setEnabled(self._value < self._max)

    def _commit_edit(self):
        self.setValue(self._clamp(self._edit.text().strip() or self._value))

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
            self._edit.setText(str(self._value))
            return
        self._value = new
        self._edit.setText(str(new))
        self._sync_buttons()
        self.valueChanged.emit(new)

    def step_by(self, direction: int):
        self.setValue(self._value + direction * self._step)

    def setRange(self, minimum: int, maximum: int):
        self._min, self._max = int(minimum), int(maximum)
        self._edit.setValidator(QIntValidator(self._min, self._max, self))
        self.setValue(self._value)
        self._sync_buttons()

    def wheelEvent(self, event):
        """滚轮微调：仅当数值框有焦点时生效，否则交给外层滚动区"""
        if not self._edit.hasFocus():
            event.ignore()
            return
        self.step_by(1 if event.angleDelta().y() > 0 else -1)
        event.accept()
