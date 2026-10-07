# -*- coding: utf-8 -*-
"""
====================================================================
轻提示气泡  -  toast
====================================================================
全应用统一轻提示（2026-10-06 重设计，设计稿
``设计稿/轻提示气泡-高仿真-2026-10-06.html``），替代 controls.ScreenToast
的「屏幕顶部纯文字胶囊」。旧入口 ``ScreenToast.show_msg`` 保留为兼容
门面（见 controls.py），内部全部转发到这里。

形态与位置
----------
  · 屏幕底部水平居中（或设置改右下角）、距底缘 56px 基准档——以整屏
    底缘为基准，任务栏守卫保证气泡永不压进任务栏（R3：offset 小于
    任务栏高 + 8 时自动抬高）；
  · 248px 定宽气泡，高度随内容自适应（紧凑单行 / 带正文 / 带动作行），
    正文上限 3 行后截断；
  · 语义七种：success / info / warning / danger / accent（可撤销）/
    neutral / loading。语义色只出现在**左缘 2.5px 色条**与**图标块**
    两处，正文永远中性灰 —— 轻提示不抢焦点的关键。

行为
----
  · 设置项（2026-10-06「设置→全局工具→轻提示」卡，7 键）：总开关 /
    位置 / 距底缘 / 时长档位 / 同屏上限 / 入场动画 / 提示音——经
    ``attach_config`` 注入 ConfigManager 门面逐次读取（未注入走
    DEFAULT_CONFIG 兜底），禁止散落 json 直读；
  · 倒计时：底部 2px 色条，单个 QVariantAnimation（linear）同时驱动
    色条 drain 与自动退场 —— 与真实存活严格同步；hover 冻结、离开续走，
    不用两个 QTimer 互相校准。loading 为常驻型：无倒计时条，
    ``morph()`` 完成后原地变体；
  · 时长策略：KINDS 的 ms 是标准档（3.2s）绝对值，实际驻留按基准档位
    （toast_duration：短 2s / 标准 3.2s / 长 5s）等比缩放——等价于
    错误类 ×1.875、带动作钮 ×1.5625 的固定倍率，倍率不暴露给用户；
    显式传 ms 的调用方（导出长文案 / 悬浮球短提示）不受档位影响；
  · 堆叠：最多 ``toast_max_visible`` 条同时可见，新条在最下、旧条上移；
    满员后新条照常出生、**最早的存活气泡收拢为「+N」胶囊**（R4，
    设计稿 .stack-more），空位腾出后依序恢复（FIFO），N 归零胶囊移除；
  · 动效：入场 280ms 上浮 12px + 淡入（OutCubic 减速）、出场 160ms
    下沉 8px + 淡出（InCubic 加速、无回弹），批量入场错峰 60ms；
    时长走 motion token（toast_in / toast_out / toast_stagger /
    toast_move），reduce_motion 全部落终态；``toast_animation`` 关闭时
    跳过进出补间（直接置位 / 移除），hover 暂停与倒计时条保留（R6，
    可用性底线刻意不给关闭入口）；顶部胶囊时代的 scale .97 微缩不移植
    —— 顶层窗口做不了几何缩放，上浮 + 淡入已足够传达「回应」；
  · 声音：``toast_sound`` 开启且语义为 warning / danger 时
    QApplication.beep() 单次短提示（success / info 静默，R7）；
  · 交互：hover 显露右上角关闭钮；动作钮（主动作 = 语义色文字，
    可选 ghost 副动作 = 次级灰）点击后执行回调并退场。

窗口语义（五条铁律，设计稿「PyQt6 落地要点」）
----------------------------------------------
  ① 每条气泡一个 Tool 顶层窗：FramelessWindowHint | Tool |
     WindowStaysOnTopHint | NoDropShadowWindowHint +
     WA_TranslucentBackground，气泡壳全部 QPainter 自绘，不用 QSS 画底；
  ② 不用 Qt.Popup（WA_NoMouseReplay 是一次性开关、必须在 showEvent
     重装填的项目已知坑）：Tool + WA_ShowWithoutActivating，不抢焦点，
     倒计时到了自然退场；
  ③ 倒计时单动画驱动（见上）；
  ④ 图标走 icon_render 自绘管线（check/info/warning/close/undo/loading），
     禁 emoji；图标色取语义 token；
  ⑤ 位置与堆叠见上；窗口四周留 :data:`SHADOW_M` 透明边距自绘三层阴影，
     阴影边距内不响应鼠标 —— 与 task_reminder_popup 同款取舍（气泡
     存活 ≤6s，且悬停在气泡本体上的交互不受影响）。

token 全部取自 theme.py THEMES（零新色）；字号走 theme.scale_px 跟随
ui_scale 档位。主题切换由 wiring 把 main_window.theme_changed 接到
ToastCenter.set_theme —— 存活中的气泡原地换肤。
====================================================================
"""

from PyQt6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation, pyqtSignal, QRect,
    QRectF, Qt, QTimer, QVariantAnimation,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QApplication, QWidget

from src import motion
from src.app_paths import get_full_screen_geometry, get_screen_geometry
from src.config import DEFAULT_CONFIG
from src.constants import (
    DEFAULT_THEME, FS_SM, RADIUS_CHIP, RADIUS_PANEL,
    TOAST_BASE_MS,
)
from src.icon_render import paint_icon
from src.theme import current_ui_scale, get_colors, scale_px

# ====================================================================
# 视觉尺寸（逻辑像素，全部对齐设计稿解剖图）
# ====================================================================
WIDTH = 248                 # 气泡定宽（等价 Qt 逻辑像素；2026-10-06 三轮收敛
                            # 340→300→272→248，用户实机逐轮反馈收窄）
BOTTOM_GAP = 56             # 距屏幕底缘基准档（= toast_bottom_offset 默认值；
                            # 任务栏高 + 8 守卫见 ToastCenter.relayout）
CORNER_MARGIN = 24          # 右下角模式的水平边距（设置项 R2）
STACK_GAP = 10              # 堆叠间距
MAX_VISIBLE = 3             # 同时可见上限默认档（= toast_max_visible 默认值），
                            # 溢出最早的收拢为「+N」胶囊
SHADOW_M = 30               # 窗口四周留给阴影的透明边距（最外环下坠
                            # 12px + 外扩 18px + 抗锯齿余量）
TINT_BAR_W = 2.5            # 左缘语义色条宽（圆头）
TINT_BAR_RATIO = 0.56       # 色条高 = 气泡高的 56%，垂直居中
TINT_BAR_ALPHA = 140        # 倒计时条不透明度（设计稿 opacity .55）
ICON_BLOCK = 28             # 图标软色底块
ICON_PX = 18                # 图标线稿尺寸
ICON_STROKE = 1.8           # 图标笔宽（与设计稿 stroke 1.8 一致）
PAD_L, PAD_R = 16, 14       # 气泡内边距（设计稿 16 / 14）
PAD_T, PAD_B = 12, 14
GAP_ICON_TEXT = 20          # 图标块与文本区间距（设计稿 12；2026-10-06 用户
                            # 两轮实机反馈仍偏紧，一步提到 8pt 网格 2.5 档）
TITLE_FS = FS_SM            # 标题字阶 fs_sm 13
MSG_FS = 12                 # 正文字阶（设计稿 .t-msg 的实际渲染值）
MAX_MSG_LINES = 3           # 正文行数上限，超出截断
CLOSE_SIZE = 24             # 关闭钮命中区
CLOSE_OVERSHOOT = 4         # 关闭钮向右上越出内边距的量（设计稿 -4 margin）
CLOSE_X_PX = 12             # 关闭 X 线稿尺寸
ACT_H = 26                  # 动作钮高（4+4 上下内边距 + 12px 文字行）
ACT_PAD_X = 12
ACT_GAP = 6
ACT_ROW_GAP = 8             # 动作行与正文的间距
ENTER_FLOAT_PX = 12         # 入场上浮幅度
EXIT_FLOAT_PX = 8           # 出场下沉幅度

# 「+N」收拢胶囊（R4，设计稿 .stack-more：11px 粗体 + 4px 12px 内边距 +
# 圆角 99 + 双层阴影）
PILL_FS = 11                # 计数字号
PILL_PAD_X = 12             # 水平内边距
PILL_PAD_Y = 5              # 垂直内边距
PILL_SHADOW_M = 16          # 窗口四周阴影透明边距（双层：外扩 6 + 余量）

# 文本区几何（恒定，与关闭钮占位无关的紧凑口径）
TEXT_X = PAD_L + ICON_BLOCK + GAP_ICON_TEXT          # 56
TEXT_W = WIDTH - PAD_R - CLOSE_SIZE + CLOSE_OVERSHOOT - TEXT_X - 2  # ≈180


# ====================================================================
# 语义表：色 token / 图标形体 / 标准档驻留毫秒（0 = 常驻）
# ====================================================================
# 时长策略（设计稿 + 设置项 R5）：ms 是**标准档**（toast_duration=
# "standard"，基准 3200ms）的绝对驻留；brief / relaxed 档由
# ToastCenter._resolve_lifetime 按基准等比缩放——等价于 错误类 ×1.875、
# 带动作钮 ×1.5625 的固定倍率（倍率不暴露给用户，只暴露三档基准）。
# 显式传 ms > 0 的调用方不走档位；config 键 toast_duration 只在
# ToastCenter 出队时读取（存活气泡不中途改表）。
KINDS = {
    "success": {"token": "success",   "icon": "check",   "ms": 3200},
    "info":    {"token": "link",      "icon": "info",    "ms": 3200},
    "warning": {"token": "warning",   "icon": "warning", "ms": 5000},
    "danger":  {"token": "danger",    "icon": "close",   "ms": 6000},
    "accent":  {"token": "primary",   "icon": "undo",    "ms": 5000},
    "neutral": {"token": "line_2",    "icon": "info",    "ms": 3200},
    "loading": {"token": "primary",   "icon": "loading", "ms": 0},
}


def _scaled_font(px: int, weight: QFont.Weight) -> QFont:
    """气泡自绘字号（跟随 ui_scale 档位；自绘不走 QSS，须自己换算）。"""
    f = QFont()
    f.setPixelSize(max(1, scale_px(px, current_ui_scale())))
    f.setWeight(weight)
    return f


class _ActButton(QAbstractButton):
    """气泡动作钮：透明底 + 语义色文字，hover 淡染 10%（设计稿 .t-act）。

    ghost 变体 = 次级灰文字 + surface_3 hover（副动作 / 忽略类）。
    自绘而不走 QSS：气泡窗口不在主窗 QSS 作用域内，且 12% 淡染需要
    按语义色动态合成，内联绘制更直接。
    """

    def __init__(self, label: str, callback, theme_card, ghost=False):
        super().__init__()
        self._label = str(label)
        self._callback = callback
        self._card = theme_card      # 回读宿主 ToastCard 的主题与语义色
        self._ghost = bool(ghost)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        f = _scaled_font(MSG_FS, QFont.Weight.Bold)
        self._font = f
        fm = QFontMetrics(f)
        self.setFixedSize(int(fm.horizontalAdvance(self._label)) + ACT_PAD_X * 2,
                          ACT_H)
        self.clicked.connect(self._fire)

    def _fire(self):
        cb = self._callback
        if callable(cb):
            cb()
        self._card.dismiss()

    def paintEvent(self, event):
        colors = self._card.colors
        tint = self._card.tint_color()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            bg = QColor(colors["surface_3"]) if self._ghost else \
                QColor(tint.red(), tint.green(), tint.blue(), 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()), RADIUS_CHIP, RADIUS_CHIP)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._font)
        p.setPen(QColor(colors["text_secondary"] if self._ghost else tint))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._label)
        p.end()


class ToastCard(QWidget):
    """单条轻提示气泡（独立顶层窗，全部自绘）。

    生命周期：``ToastCenter._spawn`` 构造 → ``_enter()`` 入场 →
    倒计时 / hover 暂停 → ``_begin_leave()`` 出场 → ``closed`` 信号。
    应用侧只在 loading 场景额外调 ``morph()``（完成后原地变体）与
    ``dismiss()``（提前收起）。
    """

    closed = pyqtSignal(object)   # 出场动画结束后发一次（参数 = 卡片自身）

    def __init__(self, kind="neutral", title="", msg="",
                 action=None, ghost=None, theme=DEFAULT_THEME, ms=0):
        spec = KINDS.get(kind, KINDS["neutral"])
        self._kind = kind if kind in KINDS else "neutral"
        self._spec = spec
        self._theme = theme if theme in ("light", "dark") else DEFAULT_THEME
        self._title = str(title or "")
        self._msg = str(msg or "")
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self._phase = "idle"          # idle / enter / live / leave
        self._parked = False          # 收拢停放中（堆叠溢出，R4「+N」胶囊）
        self._hover_close = False
        self._target = QPoint(0, 0)   # 当前应落位（窗口坐标，_relayout 维护）
        self._countdown = None        # 0→1 线性（bar + 自动退场共用）
        self._spin = None             # loading 自转 0→360
        self._fade_anim = None
        self._pos_anim = None
        self._move_anim = None
        self._buttons = []

        self._refresh_fonts()
        self._apply_colors()
        self._build_buttons(action, ghost)
        self._resize_for_content()

        # ---- 倒计时（铁律 ③：单动画驱动 bar 与退场，linear 严格同步）----
        lifetime = int(ms) if ms and int(ms) > 0 else spec["ms"]
        self._lifetime = max(0, lifetime)
        if self._lifetime > 0:
            self._countdown = QVariantAnimation(self)
            self._countdown.setDuration(self._lifetime)   # 常驻时长不随动效档缩放
            self._countdown.setStartValue(0.0)            # 缺键值时 currentValue
            self._countdown.setEndValue(1.0)              # 恒为 None 且不会推进
            self._countdown.valueChanged.connect(self._on_tick)
            self._countdown.finished.connect(self._begin_leave)

        # ---- loading 自转（1s/圈，仅 loading 形态启动）----
        self._spin = QVariantAnimation(self)
        self._spin.setDuration(1000)
        self._spin.setLoopCount(-1)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(360.0)
        self._spin.valueChanged.connect(self._on_tick)

    def _on_tick(self, _value):
        """动画帧回调（倒计时 drain 与 loading 自转共用）。

        刻意用绑定方法而不是 ``lambda _v: self.update()``：PyQt 对绑定
        方法槽在接收者 C++ 对象销毁时**自动断连**，lambda 槽则被 sender
        强引用、无此保护——气泡是高频创建销毁的顶层窗，lambda 槽在
        密集出队场景下出现过销毁后仍触发致段错误（faulthandler 实证）。
        """
        if self._phase not in ("idle", "leave"):
            self.update()

    # ---------------- 对外 ----------------
    @property
    def kind(self) -> str:
        return self._kind

    @property
    def card_height(self) -> int:
        """气泡本体高（不含阴影边距）。"""
        return self.height() - SHADOW_M * 2

    @property
    def colors(self) -> dict:
        return self._colors

    def tint_color(self) -> QColor:
        token = self._spec["token"]
        if self._kind == "neutral":
            return QColor(self._colors["line_2"])
        return QColor(self._colors.get(token, self._colors["primary"]))

    def dismiss(self):
        """提前退场（关闭钮 / 动作钮 / 应用侧主动收起）。"""
        self._parked = False
        self._begin_leave()

    def park(self):
        """收拢停放（堆叠溢出，R4）：隐藏并暂停倒计时，空位腾出后恢复。

        与退场的区别：气泡**仍存活**（队列占位、倒计时冻结），恢复时
        重走进场动画并续走剩余时长——收拢不丢提示，只是排队。
        """
        if self._parked or self._phase in ("idle", "leave"):
            return
        self._parked = True
        # 入场 / 补位动画在途时先停掉，避免隐藏后仍有动画驱动 pos
        for anim in (self._fade_anim, self._pos_anim, self._move_anim):
            if anim is not None:
                anim.stop()
        if self._countdown is not None and \
                self._countdown.state() == QVariantAnimation.State.Running:
            self._countdown.pause()
        self._phase = "live"          # 入场在途被收拢：视同已就位
        self.hide()

    def unpark(self):
        """从「+N」胶囊恢复（FIFO 队首）：重走进场并续走倒计时。"""
        if not self._parked:
            return
        self._parked = False
        self._enter()

    def morph(self, kind, title=None, msg=None, ms=None):
        """原地变体（loading → success 之类）：换语义 / 换文案 / 重计时。"""
        if kind not in KINDS or self._phase in ("idle", "leave"):
            return
        self._kind = kind
        self._spec = KINDS[kind]
        if title is not None:
            self._title = str(title)
        if msg is not None:
            self._msg = str(msg)
        lifetime = self._spec["ms"] if ms is None else max(0, int(ms or 0))
        self._lifetime = lifetime
        if lifetime > 0:
            if self._countdown is None:
                self._countdown = QVariantAnimation(self)
                self._countdown.setStartValue(0.0)
                self._countdown.setEndValue(1.0)
                self._countdown.valueChanged.connect(self._on_tick)
                self._countdown.finished.connect(self._begin_leave)
            self._countdown.stop()
            self._countdown.setDuration(lifetime)
            self._countdown.start()
        else:
            if self._countdown is not None:
                self._countdown.stop()
        if self._kind == "loading":
            self._spin.start()
        else:
            self._spin.stop()
        self._apply_colors()
        self._resize_for_content()
        self._relayout_self()

    def set_theme(self, theme: str):
        """主题切换（ToastCenter 广播）：重取色重绘，字号档位一并复核。"""
        if theme in ("light", "dark") and theme != self._theme:
            self._theme = theme
            self._refresh_fonts()
            self._apply_colors()
            self._resize_for_content()
            self._relayout_self()

    # ---------------- 内部：组装 ----------------
    def _refresh_fonts(self):
        self._title_font = _scaled_font(TITLE_FS, QFont.Weight.Bold)
        self._msg_font = _scaled_font(MSG_FS, QFont.Weight.Normal)

    def _apply_colors(self):
        self._colors = get_colors(self._theme)

    def _build_buttons(self, action, ghost):
        row = []
        if ghost and ghost[0]:
            row.append(_ActButton(ghost[0], ghost[1] if len(ghost) > 1 else None,
                                  self, ghost=True))
        if action and action[0]:
            row.append(_ActButton(action[0],
                                  action[1] if len(action) > 1 else None,
                                  self, ghost=False))
        self._buttons = row
        for btn in row:
            btn.setParent(self)
            btn.show()

    def _layout_metrics(self):
        """内容排版量测：返回 (title_h, msg_h, has_msg, has_actions)。"""
        fm_t = QFontMetrics(self._title_font)
        title = fm_t.elidedText(self._title, Qt.TextElideMode.ElideRight,
                                TEXT_W)
        title_h = fm_t.height()
        msg_h = 0
        if self._msg:
            fm_m = QFontMetrics(self._msg_font)
            need = fm_m.boundingRect(
                QRect(0, 0, TEXT_W, 10 ** 4),
                Qt.TextFlag.TextWordWrap, self._msg).height()
            msg_h = min(int(need), MAX_MSG_LINES * fm_m.height())
        return title, title_h, msg_h, bool(self._msg), bool(self._buttons)

    def _resize_for_content(self):
        title, title_h, msg_h, has_msg, has_actions = self._layout_metrics()
        self._elided_title = title
        body_h = title_h
        if has_msg:
            body_h += 2 + msg_h
        if has_actions:
            body_h += ACT_ROW_GAP + ACT_H
        # 内容区高（图标块与文本组互居中的基准；动作行计入区域，
        # 但文本组垂直居中时不含动作行——动作钮恒锚定底部）
        self._content_h = int(max(ICON_BLOCK, body_h))
        card_h = PAD_T + self._content_h + PAD_B
        self.setFixedSize(WIDTH + SHADOW_M * 2, card_h + SHADOW_M * 2)
        self._place_buttons()

    def _place_buttons(self):
        """动作钮右对齐到气泡右内缘（设计稿 .t-actions justify-end）。"""
        if not self._buttons:
            return
        x = WIDTH - PAD_R
        y = SHADOW_M + self.card_height - PAD_B - ACT_H
        for btn in reversed(self._buttons):
            x -= btn.width()
            btn.move(int(x), int(y))
            x -= ACT_GAP

    def _relayout_self(self):
        """本体内容尺寸变化后（morph / set_theme / 字号档位）重排。"""
        self._place_buttons()
        ToastCenter.relayout()

    # ---------------- 内部：几何命中 ----------------
    def _close_rect(self) -> QRectF:
        # 右上角 24×24，向右上越出内边距 4px（设计稿 margin -4）。
        # 返回**窗口坐标**（paint 与鼠标命中同用一份，不再各自补偏移）
        return QRectF(SHADOW_M + WIDTH - PAD_R - CLOSE_SIZE + CLOSE_OVERSHOOT,
                      SHADOW_M + PAD_T - CLOSE_OVERSHOOT,
                      CLOSE_SIZE, CLOSE_SIZE)

    def _countdown_bar_rect(self) -> QRectF:
        # 气泡底缘内收 1px + 2px 条（设计稿 bottom:1px; height:2px），
        # 左右各内收一个 r_panel 落在圆角内缘
        ch = self.card_height
        return QRectF(SHADOW_M + RADIUS_PANEL, SHADOW_M + ch - 3,
                      WIDTH - RADIUS_PANEL * 2, 2)

    # ---------------- 内部：进出与堆叠 ----------------
    def _enter(self):
        speed = ToastCenter.speed()
        self._phase = "enter"
        self.move(self._target + QPoint(0, ENTER_FLOAT_PX))
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        # R6：toast_animation 关闭 → 跳过进出补间直接置位
        # （reduce_motion 由 motion.duration 归零，两条闸独立生效）
        in_ms = motion.duration(motion.MOTION["toast_in"], speed) \
            if ToastCenter.anim_enabled() else 0
        easing = getattr(QEasingCurve.Type, motion.EASE["out"])
        if in_ms <= 0:                      # reduce_motion：瞬显终态
            self.setWindowOpacity(1.0)
            self.move(self._target)
            self._phase = "live"
        else:
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
            self._fade_anim.setDuration(in_ms)
            self._fade_anim.setEasingCurve(easing)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.start()
            self._pos_anim = QPropertyAnimation(self, b"pos", self)
            self._pos_anim.setDuration(in_ms)
            self._pos_anim.setEasingCurve(easing)
            self._pos_anim.setStartValue(self._target + QPoint(0, ENTER_FLOAT_PX))
            self._pos_anim.setEndValue(self._target)
            self._pos_anim.finished.connect(self._on_entered)
            self._pos_anim.start()
        if self._lifetime > 0:
            if self._countdown.state() == QVariantAnimation.State.Paused:
                self._countdown.resume()   # 收拢恢复：续走剩余时长
            else:
                self._countdown.start()
        if self._kind == "loading":
            self._spin.start()

    def _on_entered(self):
        if self._phase == "enter":
            self._phase = "live"

    def _place(self, x: int, y: int, move_ms=None):
        """ToastCenter.relayout 的落位入口：入场中只更新终点，存活中动画平移。

        ``move_ms``：平移补间毫秒；None = toast_move 档（280ms，设置项
        R9 与堆叠补位共用），0 = 直接跳变。``toast_animation`` 关闭时
        无条件跳变（R6）；reduce_motion 经 motion.duration 归零。
        """
        self._target = QPoint(int(x), int(y))
        if self._phase == "enter":
            if self._pos_anim is not None and \
                    self._pos_anim.state() == QVariantAnimation.State.Running:
                self._pos_anim.setEndValue(self._target)
            return
        if self._phase != "live" or self.pos() == self._target:
            return
        if move_ms is None:
            move_ms = motion.duration(motion.MOTION["toast_move"],
                                      ToastCenter.speed())
        if not ToastCenter.anim_enabled():
            move_ms = 0
        if move_ms <= 0:
            self.move(self._target)
            return
        if self._move_anim is None:
            self._move_anim = QPropertyAnimation(self, b"pos", self)
            self._move_anim.setEasingCurve(
                getattr(QEasingCurve.Type, motion.EASE["out"]))
        self._move_anim.stop()
        self._move_anim.setDuration(int(move_ms))
        self._move_anim.setStartValue(self.pos())
        self._move_anim.setEndValue(self._target)
        self._move_anim.start()

    def _begin_leave(self):
        if self._phase in ("idle", "leave"):
            return
        self._phase = "leave"
        if self._countdown is not None:
            self._countdown.stop()
        self._spin.stop()
        # 入场动画在途就被退场（X 快速点按 / 满员挤掉）：旧动画必须显式
        # 停掉，否则两个动画同时驱动 pos / windowOpacity 会互相打架
        for anim in (self._fade_anim, self._pos_anim, self._move_anim):
            if anim is not None:
                anim.stop()
        for btn in self._buttons:
            btn.hide()
        ToastCenter.on_card_leaving(self)
        # R6：toast_animation 关闭 → 跳过出场补间直接移除
        out_ms = motion.duration(motion.MOTION["toast_out"],
                                 ToastCenter.speed()) \
            if ToastCenter.anim_enabled() else 0
        if out_ms <= 0:                     # reduce_motion：直接落终态
            self.hide()
            self.closed.emit(self)
            return
        easing = getattr(QEasingCurve.Type, motion.EASE["in"])
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(out_ms)
        self._fade_anim.setEasingCurve(easing)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_left)
        self._fade_anim.start()
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setDuration(out_ms)
        self._pos_anim.setEasingCurve(easing)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(self._target + QPoint(0, EXIT_FLOAT_PX))
        self._pos_anim.start()

    def _on_left(self):
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    # ---------------- 内部：hover（暂停倒计时 + 显露关闭钮）----------------
    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()
        if self._countdown is not None and \
                self._countdown.state() == QVariantAnimation.State.Running:
            self._countdown.pause()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_close = False
        self.update()
        if self._countdown is not None and \
                self._countdown.state() == QVariantAnimation.State.Paused:
            self._countdown.resume()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        over = self._close_rect().contains(event.position())
        if over != self._hover_close:
            self._hover_close = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self._close_rect().contains(event.position()):
            self.dismiss()
            return
        super().mousePressEvent(event)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        colors = self._colors
        dark = self._theme == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        card = QRectF(SHADOW_M, SHADOW_M, WIDTH, self.card_height)
        radius = float(RADIUS_PANEL)

        # ---- 三层克制阴影（贴地 / 主影 / 远影，设计稿 shadow-t1/t2/t3）----
        # CSS 的 box-shadow 自带 blur（2/12/32px）羽化；QPainter 画的是
        # 硬边矩形，若只用 3 层，卡缘处三层直接叠成 ~22% 的死黑环（实测
        # 像素 198,201,203）。改为 5 环递减α近似高斯衰减：卡缘合成 ≈13%
        # 并向外缓降，远影 reach ≈ 设计稿 12px 位移 + 32px 模糊的可见范围。
        shadow_rgb = (0, 0, 0) if dark else (16, 32, 48)
        k = 2.1 if dark else 1.0
        rings = ((2.0, 1.0, 10), (5.0, 3.0, 8), (9.0, 6.0, 7),
                 (14.0, 9.0, 6), (18.0, 12.0, 4))
        p.setPen(Qt.PenStyle.NoPen)
        for expand, offset_y, alpha in rings:
            r = card.adjusted(-expand, -expand + offset_y,
                              expand, expand + offset_y)
            p.setBrush(QColor(*shadow_rgb, min(255, int(alpha * k))))
            p.drawRoundedRect(r, radius + expand * 0.35, radius + expand * 0.35)

        # ---- 气泡底（实底 surface，弹层不透底）+ 1px 描边 ----
        p.setBrush(QColor(colors["surface"]))
        p.setPen(QPen(QColor(colors["line"]), 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # ---- 深色顶部高光（7% 白，模拟玻璃厚度；对齐 $glass_edge 手法）----
        if dark:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(card.left() + radius, card.top() + 1.0),
                       QPointF(card.right() - radius, card.top() + 1.0))

        # ---- 左缘语义色条：2.5px 圆头、60% 高垂直居中（唯一大色块）----
        tint = self.tint_color()
        bar_h = card.height() * TINT_BAR_RATIO
        bar = QRectF(card.left() + 0.75, card.center().y() - bar_h / 2.0,
                     TINT_BAR_W, bar_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tint)
        p.drawRoundedRect(bar, TINT_BAR_W / 2.0, TINT_BAR_W / 2.0)

        # ---- 图标块：28px 软色底 + 18px 线稿（loading 自转）----
        # 图文对齐（2026-10-06 排版返工）：图标块与文本组**互相垂直居中**
        # 于内容区中心线——旧实现文本硬编码顶部锚定（PAD_T+2），13px 粗体
        # 行高(~18)对 28px 图标块中心偏差 ~4.5px，实机截图实证「文字浮、
        # 图标沉」。三种形态（紧凑/带正文/带动作行）统一走同一中心线。
        # 水平居中（同日二轮返工，用户报「都挤在左边」）：内容组
        # （图标块+间距+文本实际宽度）短于可用宽度时整体水平居中，
        # 长文案文本撑满 TEXT_W 时自然回退左对齐——一个公式两种形态。
        content_top = card.top() + PAD_T
        content_h = self._content_h
        fm_t = QFontMetrics(self._title_font)
        text_w = min(fm_t.horizontalAdvance(self._elided_title), TEXT_W)
        if self._msg:
            fm_m = QFontMetrics(self._msg_font)
            msg_w = min(int(fm_m.boundingRect(
                QRect(0, 0, TEXT_W, 10 ** 4),
                Qt.TextFlag.TextWordWrap, self._msg).width()), TEXT_W)
            text_w = max(text_w, msg_w)
        avail_w = WIDTH - PAD_L - PAD_R
        group_w = ICON_BLOCK + GAP_ICON_TEXT + text_w
        x0 = card.left() + PAD_L + max(0.0, (avail_w - group_w) / 2.0)
        block = QRectF(x0,
                       content_top + (content_h - ICON_BLOCK) / 2.0,
                       ICON_BLOCK, ICON_BLOCK)
        text_x = x0 + ICON_BLOCK + GAP_ICON_TEXT
        if self._kind == "neutral":
            block_bg = QColor(colors["surface_3"])
            icon_color = QColor(colors["text_secondary"])
        else:
            block_bg = QColor(tint.red(), tint.green(), tint.blue(), 31)
            icon_color = tint
        p.setBrush(block_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(block, RADIUS_CHIP, RADIUS_CHIP)

        p.save()
        if self._kind == "loading" and self._spin.state() == \
                QVariantAnimation.State.Running:
            p.translate(block.center())
            p.rotate(float(self._spin.currentValue()))
            p.translate(-block.center())
        icon_rect = block.adjusted(
            (ICON_BLOCK - ICON_PX) / 2.0, (ICON_BLOCK - ICON_PX) / 2.0,
            -(ICON_BLOCK - ICON_PX) / 2.0, -(ICON_BLOCK - ICON_PX) / 2.0)
        paint_icon(p, self._spec["icon"], icon_rect, icon_color, ICON_STROKE)
        p.restore()

        # ---- 文本（与图标块互居中：文本组整体对齐图标中心线）----
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        title_h = QFontMetrics(self._title_font).height()
        msg_h = 0
        if self._msg:
            fm_m = QFontMetrics(self._msg_font)
            msg_h = min(int(fm_m.boundingRect(
                QRect(0, 0, TEXT_W, 10 ** 4),
                Qt.TextFlag.TextWordWrap, self._msg).height()),
                MAX_MSG_LINES * fm_m.height())
        group_h = title_h + ((2 + msg_h) if self._msg else 0)
        group_top = content_top + (content_h - group_h) / 2.0
        p.setFont(self._title_font)
        p.setPen(QColor(colors["text"]))
        p.drawText(QRectF(text_x, group_top, TEXT_W, title_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._elided_title)
        if self._msg:
            p.setFont(self._msg_font)
            p.setPen(QColor(colors["text_secondary"]))
            p.drawText(QRectF(text_x, group_top + title_h + 2, TEXT_W, msg_h),
                       Qt.TextFlag.TextWordWrap
                       | Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignTop,
                       self._msg)

        # ---- 关闭钮：默认隐形，hover 显露（克制）----
        if self.underMouse() and self._phase in ("enter", "live"):
            crect = self._close_rect()
            if self._hover_close:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(colors["surface_3"]))
                p.drawRoundedRect(crect, RADIUS_CHIP, RADIUS_CHIP)
            x_color = colors["text_secondary" if self._hover_close
                             else "text_disabled"]
            x_rect = crect.adjusted((CLOSE_SIZE - CLOSE_X_PX) / 2.0,
                                    (CLOSE_SIZE - CLOSE_X_PX) / 2.0,
                                    -(CLOSE_SIZE - CLOSE_X_PX) / 2.0,
                                    -(CLOSE_SIZE - CLOSE_X_PX) / 2.0)
            paint_icon(p, "close", x_rect, QColor(x_color), 1.3)

        # ---- 底部倒计时条：scaleX 与存活严格同步（常驻型不画）----
        if self._countdown is not None and self._lifetime > 0 and \
                self._phase in ("enter", "live"):
            # Stopped（尚未 start）或边界下 currentValue 可能为 None → 0 = 满条
            raw = self._countdown.currentValue()
            progress = float(raw) if raw is not None else 0.0
            bar_rect = self._countdown_bar_rect()
            remain = QRectF(bar_rect.left(), bar_rect.top(),
                            bar_rect.width() * max(0.0, 1.0 - progress), 2)
            if remain.width() > 0.5:
                p.setPen(Qt.PenStyle.NoPen)
                drain = QColor(tint)
                drain.setAlpha(TINT_BAR_ALPHA)
                p.setBrush(drain)
                p.drawRoundedRect(remain, 1, 1)
        p.end()


# ====================================================================
# 「+N」收拢胶囊（R4，设计稿 .stack-more）
# ====================================================================
class _MorePill(QWidget):
    """堆叠溢出计数胶囊：非交互 Tool 顶层窗，双阴影 + surface 底 + 圆角 99。

    由 ToastCenter 独家持有（N ≥ 1 时存在）：``set_count`` 更新数字与
    尺寸（落位交给 relayout），主题切换走 ``apply_theme`` 原地换色。
    """

    def __init__(self):
        super().__init__(None)
        self._count = 1
        self._theme = DEFAULT_THEME
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._refresh()

    def set_count(self, n: int):
        n = max(1, int(n))
        if n != self._count:
            self._count = n
            self._refresh()

    def apply_theme(self, theme: str):
        if theme in ("light", "dark") and theme != self._theme:
            self._theme = theme
            self._refresh()

    def _refresh(self):
        self._font = _scaled_font(PILL_FS, QFont.Weight.Bold)
        fm = QFontMetrics(self._font)
        body_w = fm.horizontalAdvance(f"+{self._count}") + PILL_PAD_X * 2
        body_h = fm.height() + PILL_PAD_Y * 2
        self.setFixedSize(int(body_w) + PILL_SHADOW_M * 2,
                          int(body_h) + PILL_SHADOW_M * 2)
        self.update()

    def paintEvent(self, event):
        colors = get_colors(self._theme)
        dark = self._theme == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = PILL_SHADOW_M
        body = QRectF(m, m, self.width() - m * 2, self.height() - m * 2)
        # 双层克制阴影（设计稿 shadow-t1 / t2；胶囊小，第三层省略）
        shadow_rgb = (0, 0, 0) if dark else (16, 32, 48)
        p.setPen(Qt.PenStyle.NoPen)
        for expand, offset_y, alpha in ((1.5, 1.0, 56 if dark else 13),
                                        (6.0, 4.0, 66 if dark else 23)):
            r = body.adjusted(-expand, -expand + offset_y,
                              expand, expand + offset_y)
            p.setBrush(QColor(*shadow_rgb, alpha))
            p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        # 胶囊底（surface 实底 + 1px 描边，圆角 99 → 高度半径）
        p.setBrush(QColor(colors["surface"]))
        p.setPen(QPen(QColor(colors["line"]), 1))
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5),
                          body.height() / 2.0, body.height() / 2.0)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._font)
        p.setPen(QColor(colors["text_secondary"]))
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, f"+{self._count}")
        p.end()


# ====================================================================
# 管理器：堆叠 / 错峰 / 主题与档位广播（类级单点，无实例）
# ====================================================================
class ToastCenter:
    """轻提示队列管理器（类级状态，模块加载即可用，无需实例）。

    - ``push()``：唯一入队口。``toast_enabled`` 关闭时静默丢弃（R1，
      不排队不发声；重新开启不补发积压，R10）。第一条立即进场，同批
      后续按 toast_stagger（60ms）错峰，避免同帧齐跳；
    - 堆叠上限 ``toast_max_visible``：满员后新条照常出生、最早的存活
      气泡收拢为「+N」胶囊（R4），空位腾出后依序恢复（FIFO）；
    - ``attach_config``：main_window 启动时注入 ConfigManager 门面，
      未注入走 DEFAULT_CONFIG 兜底；
    - ``set_theme`` / ``set_speed``：wiring 与 controls.set_ui_speed 的
      广播入口；``on_setting_changed``：设置页轻提示项变更入口（R9）。
    """

    _toasts = []          # 已出生且未关闭的气泡（含收拢停放中），旧 → 新
    _pending = []         # 错峰待出生队列
    _stagger_timer = None
    _theme = DEFAULT_THEME
    _speed = 1.0
    _config_facade = None  # ConfigManager 门面（attach_config 注入）
    _pill = None           # 「+N」收拢胶囊（N ≥ 1 时存在）

    # ---------------- 配置门面（R1/R2/R3/R4/R6/R7 数据源）----------------
    @classmethod
    def attach_config(cls, config_manager):
        """注入 ConfigManager 门面（main_window 启动时调用；None = 脱钩）。

        逐次弹出时读取（改设置即时生效，无需广播）；未注入或读取异常
        一律回 DEFAULT_CONFIG 默认值，测试与早期构造期零负担。
        """
        cls._config_facade = config_manager

    @classmethod
    def _cfg(cls, key: str):
        cm = cls._config_facade
        if cm is not None:
            try:
                val = cm.get(key)
            except Exception:              # noqa: BLE001 - 脏门面不拖垮弹出
                val = None
            if val is not None:
                return val
        return DEFAULT_CONFIG.get(key)

    @classmethod
    def enabled(cls) -> bool:
        """总开关（R1）。"""
        return bool(cls._cfg("toast_enabled"))

    @classmethod
    def anim_enabled(cls) -> bool:
        """入场/出场补间开关（R6）；reduce_motion 由 motion.duration 独立归零。"""
        return bool(cls._cfg("toast_animation"))

    @classmethod
    def max_visible(cls) -> int:
        """同屏上限（1-5 夹取；配置层已有 RANGES，此处防脏门面）。"""
        try:
            v = int(cls._cfg("toast_max_visible"))
        except (TypeError, ValueError):
            v = MAX_VISIBLE
        return max(1, min(5, v))

    @classmethod
    def bottom_offset(cls) -> int:
        """距屏幕底缘设定值（24-120 夹取；任务栏守卫在 relayout 再抬一层）。"""
        try:
            v = int(cls._cfg("toast_bottom_offset"))
        except (TypeError, ValueError):
            v = BOTTOM_GAP
        return max(24, min(120, v))

    @classmethod
    def _resolve_lifetime(cls, kind: str, ms: int) -> int:
        """实际驻留毫秒：显式 ms > 0 直接用；否则按基准档位等比缩放。

        KINDS 的 ms 是标准档绝对值（3200/5000/6000），brief/relaxed 档
        等比缩放（基准表 constants.TOAST_BASE_MS），等价于错误类 ×1.875、
        带动作钮 ×1.5625 的固定倍率；loading（0）恒常驻。
        """
        if ms and int(ms) > 0:
            return int(ms)
        spec_ms = KINDS.get(kind, KINDS["neutral"])["ms"]
        if spec_ms <= 0:
            return 0
        dur = str(cls._cfg("toast_duration"))
        base = TOAST_BASE_MS.get(dur) or TOAST_BASE_MS["standard"]
        return max(1, round(spec_ms * base / TOAST_BASE_MS["standard"]))

    # ---------------- 入队 ----------------
    @classmethod
    def push(cls, kind="neutral", title="", msg="", ms=0,
             action=None, ghost=None, theme=""):
        """显示一条轻提示（全应用唯一入口）。

        ``action`` / ``ghost``：``(按钮文字, 回调)`` 元组；``ms`` 传 0
        按语义表 + 基准档位定驻留（loading 恒为常驻）。总开关关闭时
        静默丢弃返回 None（R1）。返回卡片实例（测试用）。
        """
        if kind not in KINDS:
            kind = "neutral"
        if theme in ("light", "dark"):
            cls._theme = theme
        if not cls.enabled():
            return None                 # R1：不显示、不排队、不发声
        cls._pending.append(dict(kind=kind, title=title, msg=msg, ms=ms,
                                 action=action, ghost=ghost))
        if cls._stagger_timer is not None and cls._stagger_timer.isActive():
            return None
        cls._drain()
        return cls._visible_cards()[-1] if cls._visible_cards() else None

    @classmethod
    def _drain(cls):
        """错峰出队：每次放行一条；还有余量则按 toast_stagger 再约。

        满员不拦新条——出生后由 _reconcile 把最早的存活气泡收拢进
        「+N」胶囊（R4）；warning / danger 且提示音开启时单声 beep（R7）。
        """
        if not cls._pending:
            return
        item = cls._pending.pop(0)
        # 时长策略（R5 基准档位在此读取）：显式 ms > 0 原样保留，否则按
        # KINDS 标准档 × 基准档位等比缩放；loading（0）恒常驻。
        kind = item.get("kind", "neutral")
        if kind not in KINDS:
            kind = "neutral"
        item["ms"] = cls._resolve_lifetime(kind, item.get("ms", 0))
        card = ToastCard(theme=cls._theme, **item)
        card.closed.connect(cls._on_closed)
        cls._toasts.append(card)
        cls._reconcile()          # 满员即收拢最早的存活气泡（R4）+ 胶囊计数
        cls.relayout()
        card._enter()
        if cls._cfg("toast_sound") and card.kind in ("warning", "danger"):
            QApplication.beep()
        if cls._pending:
            delay = motion.duration(motion.MOTION["toast_stagger"],
                                    cls._speed)
            if cls._stagger_timer is None:
                cls._stagger_timer = QTimer(QApplication.instance())
                cls._stagger_timer.setSingleShot(True)
                cls._stagger_timer.timeout.connect(cls._drain)
            cls._stagger_timer.start(max(1, delay))

    # ---------------- 收拢 / 恢复（R4）----------------
    @classmethod
    def _visible_cards(cls) -> list:
        """当前可见气泡（入场 / 存活中，不含收拢停放与退场中）。"""
        return [t for t in cls._toasts
                if not t._parked and t._phase in ("idle", "enter", "live")]

    @classmethod
    def _overflow_cards(cls) -> list:
        """收拢停放中的气泡（旧 → 新，恢复顺序）。"""
        return [t for t in cls._toasts if t._parked]

    @classmethod
    def _reconcile(cls):
        """把可见集合收敛到 max_visible：超出收拢最早者，不足放行停放者。"""
        cap = cls.max_visible()
        while len(cls._visible_cards()) > cap:
            for card in cls._toasts:
                if not card._parked and \
                        card._phase in ("idle", "enter", "live"):
                    card.park()
                    break
        while cls._overflow_cards() and len(cls._visible_cards()) < cap:
            cls._overflow_cards()[0].unpark()
        cls._update_pill()

    @classmethod
    def _update_pill(cls):
        """「+N」胶囊生命周期：N ≥ 1 存在并跟随计数，N 归零移除（R4）。"""
        n = len(cls._overflow_cards())
        if n <= 0:
            if cls._pill is not None:
                cls._pill.hide()
                cls._pill.deleteLater()
                cls._pill = None
            return
        if cls._pill is None:
            cls._pill = _MorePill()
            cls._pill.show()
        cls._pill.set_count(n)
        cls._pill.apply_theme(cls._theme)

    # ---------------- 布局 ----------------
    @classmethod
    def relayout(cls):
        """自下而上重排：新条在最下、旧条上移（退场与收拢停放的不占位）。

        位置（R2/R3）：横向 center = 工作区居中 / corner = 右下角
        （margin 24px）；纵向以整屏底缘为基准减 toast_bottom_offset，
        任务栏守卫 = max(任务栏高 + 8, 设定值)。存活气泡的平移补间走
        toast_move（280ms；动画开关关闭 / reduce_motion 直接跳变）。
        """
        live = cls._visible_cards()
        screen = get_screen_geometry()
        full = get_full_screen_geometry()
        taskbar_h = max(0, full.bottom() - screen.bottom())
        eff_offset = max(taskbar_h + 8, cls.bottom_offset())
        corner = str(cls._cfg("toast_position")) == "corner"
        if corner:
            x = screen.right() + 1 - CORNER_MARGIN - (WIDTH + SHADOW_M * 2)
        else:
            x = screen.left() + (screen.width() - (WIDTH + SHADOW_M * 2)) // 2
        card_bottom = full.bottom() + 1 - eff_offset    # 最新条的卡片底缘
        top = card_bottom
        for card in reversed(live):
            top = card_bottom - card.card_height - SHADOW_M
            card._place(x, top)
            card_bottom = top + SHADOW_M - STACK_GAP      # 上一条的卡片底缘
        if cls._pill is not None:
            pill = cls._pill
            px = (x + (WIDTH + SHADOW_M * 2) - pill.width() if corner
                  else screen.left() + (screen.width() - pill.width()) // 2)
            pill.move(int(px), int(top - STACK_GAP - pill.height()))

    @classmethod
    def on_card_leaving(cls, card):
        """退场即刻移出队列并闭合空隙：收拢条优先补位，否则队列放行。"""
        if card in cls._toasts:
            cls._toasts.remove(card)
        cls._reconcile()
        cls.relayout()

    @classmethod
    def _on_closed(cls, card):
        if card in cls._toasts:
            cls._toasts.remove(card)
        cls._reconcile()
        cls.relayout()

    # ---------------- 设置页入口（R9）----------------
    @classmethod
    def on_setting_changed(cls, key: str):
        """设置页轻提示项变更入口：位置 / 底缘 / 上限即时生效。

        位置与底缘变更以 toast_move（280ms）补间把存活气泡平移到新位
        （动画开关关闭时由 _place 直接跳变）；上限变更即时收拢 / 放行。
        时长 / 开关 / 动画 / 声音为逐次弹出读取，无需此处处理（R5/R10）。
        """
        if key in ("toast_position", "toast_bottom_offset"):
            cls.relayout()
        elif key == "toast_max_visible":
            cls._reconcile()
            cls.relayout()

    # ---------------- 广播 ----------------
    @classmethod
    def set_theme(cls, theme: str):
        """主题切换：存活气泡与胶囊原地换肤，后续新气泡跟随。"""
        if theme in ("light", "dark"):
            cls._theme = theme
            for card in list(cls._toasts):
                card.set_theme(theme)
            if cls._pill is not None:
                cls._pill.apply_theme(theme)

    @classmethod
    def set_speed(cls, speed):
        """动效档位广播（controls.set_ui_speed 调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    @classmethod
    def speed(cls) -> float:
        return cls._speed

    @classmethod
    def theme(cls) -> str:
        return cls._theme

    @classmethod
    def live_cards(cls) -> tuple:
        """当前可见气泡（含入场中，不含收拢停放与退场中）——测试断言用。"""
        return tuple(cls._visible_cards())

    @classmethod
    def overflow_cards(cls) -> tuple:
        """收拢停放中的气泡——R4 测试断言用。"""
        return tuple(cls._overflow_cards())

    @classmethod
    def pill_count(cls) -> int:
        """「+N」胶囊当前计数（无胶囊为 0）——测试断言用。"""
        return cls._pill._count if cls._pill is not None else 0

    # ---------------- 测试与收尾 ----------------
    @classmethod
    def _reset_all(cls):
        """硬清场（测试隔离用）：立刻销毁全部气泡、胶囊、错峰队列与
        配置门面（防止替身配置跨用例串台）。"""
        cls._pending.clear()
        if cls._stagger_timer is not None:
            cls._stagger_timer.stop()
        for card in list(cls._toasts):
            card.hide()
            card.deleteLater()
        cls._toasts.clear()
        if cls._pill is not None:
            cls._pill.hide()
            cls._pill.deleteLater()
            cls._pill = None
        cls._config_facade = None
