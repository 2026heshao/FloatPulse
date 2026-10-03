# -*- coding: utf-8 -*-
"""
任务提醒弹窗  -  task_reminder_popup
====================================================================
自绘顶层提醒卡片，替代任务提醒此前走的原生托盘气泡
（``QSystemTrayIcon.showMessage`` → 系统 toast：样式不可控、各 Windows
版本观感不一，用户 2026-10-02 点名重设计）。

触发链路**零改动**：knowledge_ball 仍在「启动 +4 秒 + 每日 9:00」用
``bucket_unfinished`` 做三桶分桶（已逾期 / 今日到期 / 未安排日期），
本模块只消费分好桶的**标题串列表**，不碰任何状态口径与配置开关
（``task_reminder_enabled`` 仍由调用方把关）。

呈现（2026-10-02 用户拍板）：
  · 360px 无边框卡片（$surface 底 + 柔和阴影），屏幕右下角滑入淡入；
  · 头部：铃铛（``icons.bell``，语气色圆底）+「任务提醒」+「N 项未完成」
    徽标（有逾期 → danger 色系，否则 primary 色系）+ 关闭钮（iconBtn）；
  · 三桶分节：色点 + 节名 + 计数 + 至多 :data:`PER_BUCKET` 条标题
    （超出显示「…另有 N 项」，完整标题进 tooltip）；
  · 页脚：「知道了」（secondaryBtn ghost）+「打开任务页」（primaryBtn
    实底，hover 走全局「轻提亮」overlay）；点卡片空白处同样跳转；
  · :data:`AUTO_DISMISS_MS` 自动收起，鼠标悬停**暂停倒计时**
    （QElapsedTimer 记剩余量，移开从剩余量续时）；
  · 进出场 = 淡入淡出 + 位移（motion token）；``reduce_motion`` 开启
    直接落终态。动画**不做**速度档位缩放——本弹窗每天至多两次，
    档位只对高频 toast/按钮有意义（ScreenToast 差异点，见其 set_speed）；
  · 不抢焦点（WA_ShowWithoutActivating + Tool）、不进任务栏。

单例复用（ScreenToast 同款）：重复弹直接换内容重计时，不堆叠窗口。
语气色（danger/primary/中性）由 :meth:`_apply_palette` 按主题令牌内联
取色；theme.py QSS 只管中性骨架（#taskReminderCard 等），两主题自动
跟随。
====================================================================
"""

from PyQt6.QtCore import (
    QElapsedTimer, QEasingCurve, QPoint, QRectF, QPropertyAnimation, Qt,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import QFontMetrics, QPainter, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.constants import DEFAULT_THEME
from src.controls import IconButton, SmoothButton
from src.glass import draw_soft_shadow
from src.icon_render import icon as render_icon
from src import motion
from src.theme import get_colors, get_main_window_qss

# ---- 视觉尺寸（逻辑像素）----
CARD_W = 360                 # 卡片宽（不含阴影透明边距）
SHADOW = 8                   # 四周留给柔和阴影的透明边距
BELL_RING = 32               # 头部铃铛圆底直径
EDGE_GAP = 24                # 距屏幕右/下边缘的距离
FLOAT_PX = 12                # 进出场位移幅度（进场自下上浮 / 出场下沉）
TITLE_ELIDE_W = 316          # 标题省略宽度（360 - 2×14 页边 - 14 缩进）
PER_BUCKET = 3               # 每桶最多展示的标题条数
AUTO_DISMISS_MS = 12_000     # 自动收起倒计时（悬停暂停）

# 桶定义：顺序即展示顺序；tone = 内联取色的语气键
_BUCKETS = (
    ("overdue", "已逾期", "danger"),
    ("due", "今日到期", "primary"),
    ("undated", "未安排日期", "muted"),
)

_TONE_TOKEN = {"danger": "danger", "primary": "primary", "muted": "text_secondary"}


def build_rows(overdue, due, undated, per_bucket=PER_BUCKET):
    """三桶标题串列表 → 展示行数据（纯逻辑，pytest 直测）。

    返回 ``[{"key","label","tone","count","shown","extra"}, ...]``，空桶
    直接跳过（与旧气泡口径一致：没内容的桶不出现节标题）。
    """
    rows = []
    for key, label, tone in _BUCKETS:
        titles = {"overdue": overdue, "due": due, "undated": undated}[key]
        if not titles:
            continue
        shown = list(titles[:max(0, int(per_bucket))])
        rows.append({
            "key": key, "label": label, "tone": tone,
            "count": len(titles), "shown": shown,
            "extra": len(titles) - len(shown),
        })
    return rows


def elide_title(text, fm, width=TITLE_ELIDE_W):
    """标题超宽省略（纯函数；完整文本走 tooltip）"""
    return fm.elidedText(text or "", Qt.TextElideMode.ElideRight, int(width))


class TaskReminderPopup(QWidget):
    """任务提醒顶层卡片（单例，见 :func:`popup_instance`）。"""

    open_requested = pyqtSignal()   # 「打开任务页」或点卡片空白处
    dismissed = pyqtSignal()        # 弹窗完全收起（动画结束）后发一次

    _instance = None

    @classmethod
    def popup_instance(cls):
        """单例访问（ScreenToast 同款）：重复弹换内容重计时，不堆叠。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__(None)
        self._theme = DEFAULT_THEME
        self._rows = []
        self._total = 0
        self._remaining = AUTO_DISMISS_MS
        self._fade_target = 1.0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # ---- 卡片与骨架 ----
        self._card = QFrame(self)
        self._card.setObjectName("taskReminderCard")
        self._bell_ring = QLabel()
        self._bell_ring.setFixedSize(BELL_RING, BELL_RING)
        self._bell_ring.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._head = QLabel("任务提醒")
        self._head.setObjectName("taskRemindHead")
        self._chip = QLabel()
        self._close_btn = IconButton("close", size=26, icon_size=12,
                                     object_name="iconBtn", tooltip="关闭")
        self._close_btn.clicked.connect(self.dismiss)

        header = QHBoxLayout()
        header.setContentsMargins(14, 12, 12, 10)
        header.setSpacing(10)
        header.addWidget(self._bell_ring)
        header.addWidget(self._head)
        header.addStretch(1)
        header.addWidget(self._chip)
        header.addWidget(self._close_btn)

        self._sep = QFrame()
        self._sep.setFixedHeight(1)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(14, 10, 14, 12)
        self._body.setSpacing(10)

        self._later_btn = SmoothButton("知道了")
        self._later_btn.setObjectName("secondaryBtn")
        self._open_btn = SmoothButton("打开任务页")
        self._open_btn.setObjectName("primaryBtn")
        self._later_btn.clicked.connect(self.dismiss)
        self._open_btn.clicked.connect(self._open)

        # 页脚必须是 QFrame：QSS 的 objectName 边框规则（#taskRemindFoot
        # 顶部分隔线）只认控件，挂在布局上不生效。
        foot_frame = QFrame()
        foot_frame.setObjectName("taskRemindFoot")
        foot = QHBoxLayout(foot_frame)
        foot.setContentsMargins(14, 10, 14, 12)
        foot.setSpacing(8)
        foot.addStretch(1)
        foot.addWidget(self._later_btn)
        foot.addWidget(self._open_btn)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        card_lay.addLayout(header)
        card_lay.addWidget(self._sep)
        card_lay.addLayout(self._body)
        card_lay.addWidget(foot_frame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        outer.addWidget(self._card)

        # ---- 进出场动画（ScreenToast 同款：淡入淡出 + 位移并行）----
        easing = getattr(QEasingCurve.Type, motion.EASE["out"])
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setEasingCurve(easing)
        self._fade_anim.finished.connect(self._on_anim_done)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setEasingCurve(easing)

        # ---- 自动收起（悬停暂停）----
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._clock = QElapsedTimer()

        self._apply_palette()

    # ---------------- 对外 API ----------------
    def configure(self, overdue, due, undated, theme=""):
        """换内容重计时并弹出（单例复用入口）。

        ``overdue/due/undated``：各桶的**任务标题串列表**（顺序保留）；
        ``theme``："light"/"dark"，空串维持当前主题。
        """
        if theme:
            self._theme = theme
        self._rows = build_rows(overdue, due, undated)
        self._total = sum(r["count"] for r in self._rows)
        self._apply_palette()
        self._rebuild_body()
        self._resize_and_place()
        self._raise_and_animate()
        self._arm(AUTO_DISMISS_MS)
        return self

    def set_theme(self, theme):
        """主题切换时由宿主调用（弹窗常驻期间主题变了不至于花脸）。"""
        if theme in ("light", "dark") and theme != self._theme:
            self._theme = theme
            self._apply_palette()

    # ---------------- 组装 ----------------
    def _apply_palette(self):
        """主题令牌 → QSS 骨架 + 内联语气色（每次 configure/set_theme 重放）。"""
        colors = get_colors(self._theme)
        self.setStyleSheet(get_main_window_qss(self._theme))

        tone = "danger" if any(r["tone"] == "danger" for r in self._rows) \
            else "primary"
        tone_color = colors[_TONE_TOKEN[tone]]
        ring_bg = colors["danger_alpha" if tone == "danger" else "primary_a12"]
        ring_bg = _alpha_over(ring_bg, colors["surface"])

        self._bell_ring.setStyleSheet(
            "background-color:%s;border-radius:%dpx;" % (ring_bg, BELL_RING // 2))
        self._bell_ring.setPixmap(render_icon("bell", 16, tone_color).pixmap(16, 16))
        self._chip.setText("%d 项未完成" % self._total if self._total
                           else "暂无未完成")
        self._chip.setStyleSheet(
            "color:%s;background-color:%s;border-radius:9px;"
            "padding:2px 8px;font-size:11px;" % (tone_color, ring_bg))
        self._sep.setStyleSheet(
            "background-color:%s;border:none;" % colors["hair"])

    def _rebuild_body(self):
        """按 build_rows 的数据重建三桶分节（每次 configure 全量重建）。"""
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())

        fm = QFontMetrics(self._head.font())
        colors = get_colors(self._theme)
        for row in self._rows:
            color = colors[_TONE_TOKEN[row["tone"]]]

            head_row = QHBoxLayout()
            head_row.setSpacing(7)
            dot = QLabel()
            dot.setFixedSize(7, 7)
            dot.setStyleSheet(
                "background-color:%s;border-radius:3px;" % color)
            label = QLabel(row["label"])
            label.setStyleSheet(
                "color:%s;font-size:12px;font-weight:600;" % color)
            count = QLabel(str(row["count"]))
            count.setObjectName("taskRemindCount")
            head_row.addWidget(dot)
            head_row.addWidget(label)
            head_row.addWidget(count)
            head_row.addStretch(1)
            head_widget = QWidget()
            head_widget.setLayout(head_row)
            self._body.addWidget(head_widget)

            for title in row["shown"]:
                title_label = QLabel(elide_title(title, fm))
                title_label.setObjectName("taskRemindTitle")
                title_label.setToolTip(title)
                title_label.setContentsMargins(14, 0, 0, 0)
                self._body.addWidget(title_label)

            if row["extra"] > 0:
                more = QLabel("…另有 %d 项" % row["extra"])
                more.setObjectName("taskRemindOverflow")
                more.setContentsMargins(14, 0, 0, 0)
                self._body.addWidget(more)

        self._body.addStretch(1)

    # ---------------- 尺寸与位置 ----------------
    def _resize_and_place(self):
        self._card.setFixedWidth(CARD_W)
        self._card.adjustSize()
        h = self._card.sizeHint().height()
        self.setFixedSize(CARD_W + 2 * SHADOW, h + 2 * SHADOW)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() + 1 - self.width() - EDGE_GAP,
                  screen.bottom() + 1 - self.height() - EDGE_GAP)

    # ---------------- 显示与收起 ----------------
    def _raise_and_animate(self):
        self._timer.stop()
        self._fade_anim.stop()
        self._pos_anim.stop()
        self._fade_target = 1.0
        if motion.duration(180) <= 0:      # reduce_motion：瞬显终态
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            return
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        duration = max(1, motion.eased_ms("base"))
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._pos_anim.setDuration(duration)
        self._pos_anim.setStartValue(self.pos() + QPoint(0, FLOAT_PX))
        self._pos_anim.setEndValue(self.pos())
        self._fade_anim.start()
        self._pos_anim.start()

    def dismiss(self):
        """收起（知道了 / 关闭钮 / 自动倒计时到点共用出口）。"""
        self._timer.stop()
        self._fade_anim.stop()
        self._pos_anim.stop()
        self._fade_target = 0.0
        if not self.isVisible() or motion.duration(180) <= 0:
            self.hide()
            self.setWindowOpacity(1.0)
            self.dismissed.emit()
            return
        duration = max(1, motion.eased_ms("base"))
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._pos_anim.setDuration(duration)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(self.pos() + QPoint(0, FLOAT_PX))
        self._fade_anim.start()
        self._pos_anim.start()

    def _on_anim_done(self):
        if self._fade_target <= 0.0 and self.windowOpacity() <= 0.02:
            self.hide()
            self.setWindowOpacity(1.0)
            self.dismissed.emit()

    def _open(self):
        """「打开任务页」/ 点卡片空白处：发信号后收起自己。"""
        self.open_requested.emit()
        self.dismiss()

    # ---------------- 自动收起（悬停暂停）----------------
    def _arm(self, ms):
        """以剩余量 ms 重新武装倒计时，并开始计时。"""
        self._remaining = max(500, int(ms))
        self._clock.start()
        self._timer.start(self._remaining)

    def enterEvent(self, event):
        """悬停暂停：记下剩余量并停表（移开从剩余量续时）。"""
        if self._timer.isActive():
            self._remaining = max(500, self._remaining - self._clock.elapsed())
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible() and not self._timer.isActive():
            self._arm(self._remaining)
        super().leaveEvent(event)

    # ---------------- 绘制与交互 ----------------
    def paintEvent(self, event):
        """只画卡片背后的柔和阴影；卡片本体是子 QFrame（QSS 上色）。"""
        painter = QPainter(self)
        # 卡片几何已含 SHADOW 外边距（外层布局 margins），直接以其为影基线
        draw_soft_shadow(painter, QRectF(self._card.geometry()),
                         10, layers=6, max_alpha=44, offset_y=3.0)
        painter.end()

    def mouseReleaseEvent(self, event):
        """点卡片空白处 = 打开任务页（按钮自己消费点击，不会走到这里）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._open()
            return
        super().mouseReleaseEvent(event)


def _clear_layout(layout):
    """递归清空子布局（_rebuild_body 的兜底分支）。"""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _alpha_over(rgba_value, base_hex):
    """rgba() 令牌在 base 底色上的合成色（QColor 不认 CSS 函数式写法，
    走 theme.get_colors 的原始字符串手拆；base 传 surface 十六进制）。"""
    text = str(rgba_value)
    if text.startswith("rgba"):
        inner = text[text.index("(") + 1:text.index(")")].split(",")
        r, g, b = (int(v) for v in inner[:3])
        a = float(inner[3])
        br, bg, bb = _hex_rgb(base_hex)
        return "#%02X%02X%02X" % (
            round(r * a + br * (1 - a)),
            round(g * a + bg * (1 - a)),
            round(b * a + bb * (1 - a)))
    return text


def _hex_rgb(hex_color):
    h = str(hex_color).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def popup_instance():
    """模块级单例访问（与 show_reminder 同级的转发入口）。"""
    return TaskReminderPopup.popup_instance()


def show_reminder(overdue, due, undated, theme=""):
    """模块级入口：显示/刷新任务提醒弹窗（单例）。返回弹窗实例。"""
    inst = TaskReminderPopup.popup_instance()
    return inst.configure(overdue, due, undated, theme=theme)
