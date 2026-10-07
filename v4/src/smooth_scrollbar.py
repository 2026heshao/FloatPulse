# -*- coding: utf-8 -*-
"""
====================================================================
平滑滚动条状态机  -  smooth_scrollbar（交互视觉清单 V2，2026-10-07）
====================================================================
把 QSS 滚动条的三条既有定义（全局 / navScroll / kbList）收敛为一个
自绘 QScrollBar 子类 + 状态机：

    闲置          6px · ``$line`` 60% 半透明
    悬停 / 滚动中  8px · ``$line_2`` 实色（120ms 插值）
    静止 600ms    回落闲置态

为什么自绘而不调 QSS：QSS 引擎没有 transition，宽度/颜色都只能一帧
跳变；handle 宽度还受 QSS ``width`` 盒模型约束，状态机需要绘制级自由
度。本控件完全接管 ``paintEvent``（轨道透明、只画 handle），QSS 对
其实例不再生效 —— 三处 QSS 定义随之退役。

性能口径：状态切换共用**单条** QVariantAnimation（ap 进度 0..1，
120ms OutCubic，可打断重定向）；「滚动中」由 scrollbar 自身
``valueChanged`` 驱动（滚轮 / 拖拽 / 键盘 / 程序滚动全覆盖），静止
600ms QTimer 回落 —— 与设计稿 §2 演示脚本同参数。

降级：reduce_motion / anim_speed 档位归零 → ap 直接落位（瞬显）；
档位缩放经 :meth:`SmoothScrollBar.set_speed`（controls.set_ui_speed
广播接线）。颜色按父链 ``_theme`` 现取，换主题零接线。

接入面：``install(view)`` 一行挂到 QAbstractScrollArea（navScroll /
kbList 两站）；其余列表继续走全局 QSS（视觉为常驻 10px 滑块，不在
本批收敛范围）。
====================================================================
"""

from PyQt6.QtCore import QEasingCurve, QRectF, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QScrollBar

from src import motion
from src.theme import DEFAULT_THEME, get_colors


IDLE_WIDTH = 6            # 闲置 handle 宽（逻辑 px）
ACTIVE_WIDTH = 8          # 悬停 / 滚动中 handle 宽
BAR_WIDTH = 10            # 滚动条控件自身宽度（布局槽，与全局 QSS 一致）
IDLE_ALPHA = 0.60         # 闲置色透明度（$line 的 60%）
IDLE_TIMEOUT_MS = 600     # 静止回落延迟（设计稿 §2 同参数）
TRANSITION_TOKEN = "base"  # 过渡时长 token（180ms；hover/滚动共用一条）

_EPS = 0.004


class SmoothScrollBar(QScrollBar):
    """自绘滚动条：闲置 6px 半透明 → 悬停/滚动中 8px 加深 → 600ms 回落。"""

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（controls.set_ui_speed 统一调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(BAR_WIDTH)
        self._ap = 0.0            # 激活进度：0 闲置 / 1 悬停或滚动中
        self._anim = None
        self._scrolling = False
        self._hovered = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_TIMEOUT_MS)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        self.valueChanged.connect(self._on_value_changed)
        self.rangeChanged.connect(lambda *_: self.update())

    # ---------------- 对外 ----------------
    @staticmethod
    def install(view):
        """挂到 QAbstractScrollArea（QListWidget / QScrollArea 通吃）。

        创建并 ``setVerticalScrollBar`` 替换原生纵条 —— Qt 官方替换口，
        不碰视口/滚动行为；横向滚动条保持既有 QSS 语义不动。"""
        bar = SmoothScrollBar(view)
        view.setVerticalScrollBar(bar)
        return bar

    # ---------------- 状态机 ----------------
    def _on_value_changed(self, _value):
        """任何滚动来源（滚轮/拖拽/键盘/程序）统一汇合点：激活 + 续 600ms。"""
        self._scrolling = True
        self._idle_timer.start()
        self._set_active(1.0)

    def _on_idle_timeout(self):
        self._scrolling = False
        if not self._hovered:
            self._set_active(0.0)

    def enterEvent(self, event):  # noqa: N802 (Qt 命名)
        self._hovered = True
        self._idle_timer.stop()
        self._set_active(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt 命名)
        self._hovered = False
        if not self._scrolling:
            self._set_active(0.0)
        else:
            self._idle_timer.start()   # 滚动中移出 → 交给静止计时器回落
        super().leaveEvent(event)

    def _set_active(self, target: float):
        """ap 插值到 target（0/1）。可打断重定向；reduce_motion 瞬显。"""
        if abs(target - self._ap) <= _EPS:
            return
        ms = motion.eased_ms(TRANSITION_TOKEN, self._speed)
        if ms <= 0:
            self._ap = float(target)
            self.update()
            return
        if self._anim is None:
            anim = QVariantAnimation(self)
            anim.setEasingCurve(
                getattr(QEasingCurve.Type, motion.EASE["out"]))
            anim.valueChanged.connect(self._on_ap)
            self._anim = anim
        anim = self._anim
        anim.stop()
        anim.setStartValue(self._ap)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    def _on_ap(self, value):
        self._ap = max(0.0, min(1.0, float(value)))
        self.update()

    # ---------------- 绘制 ----------------
    def _detect_theme(self):
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        if self.maximum() <= self.minimum():
            return                     # 无可滚范围：整条不画（同 QSS 空态）
        colors = get_colors(self._detect_theme() or DEFAULT_THEME)
        idle = QColor(str(colors.get("line", "#E4E2DB")))
        idle.setAlphaF(IDLE_ALPHA)
        active = QColor(str(colors.get("line_2", "#D3D1C7")))
        ap = self._ap
        color = QColor(
            int(round(idle.red() + (active.red() - idle.red()) * ap)),
            int(round(idle.green() + (active.green() - idle.green()) * ap)),
            int(round(idle.blue() + (active.blue() - idle.blue()) * ap)),
            int(round(idle.alpha() + (active.alpha() - idle.alpha()) * ap)),
        )
        w = IDLE_WIDTH + (ACTIVE_WIDTH - IDLE_WIDTH) * ap
        # 滑块轨道矩形：上下各留 QSS 同款 2px margin，水平居中
        track_h = self.height() - 4
        avail = max(1, track_h)
        prop = self.pageStep() / max(1, self.maximum() - self.minimum() + 1)
        handle_h = max(int(prop * avail), 24)
        handle_h = min(handle_h, avail)
        if self.maximum() > self.minimum():
            scrollable = self.maximum() - self.minimum()
            top = 2 + int((self.value() / scrollable)
                          * (avail - handle_h))
        else:
            top = 2
        x = (self.width() - w) / 2.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(x, float(top), w, float(handle_h)), 4.0, 4.0)
        painter.end()


def install_on(view):
    """模块级便捷入口：``SmoothScrollBar.install(view)`` 同义。"""
    return SmoothScrollBar.install(view)
