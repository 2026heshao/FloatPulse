# -*- coding: utf-8 -*-
"""
====================================================================
启动闪屏  -  splash
====================================================================
主窗口显示前的启动过渡动画。启动序列（数据文件/界面构建/插件加载）
是同步执行的，功能增多后整段可达数秒——闪屏让用户从「白等」变成
「看得见的进度」。

设计要点：
  1. 无边框 + 半透明 + Tool（不进任务栏、show 时不抢焦点）
  2. 玻璃拟态本体：paintEvent 手绘 glass_fill 填充 + primary_border
     描边 + 顶部内高光（translucent 顶层窗的窗口 QSS 背景在渲染
     管线里不可靠——便签玻璃化的同因先例，便签 E1 护栏实证）
  3. 品牌图形 = 悬浮球：主题色渐变球体上下轻浮 + 底部影子随之
     缩放淡出 + 外围旋转弧；全部由同一帧计时器（33ms）驱动
  4. main() 在每个启动阶段之间调 set_stage() 更新文案并 processEvents()
     —— 同步段之间事件循环有机会跑，动画就不会全程冻结
  5. finish()：停帧 → 300ms 淡出 → close + deleteLater；
     入场 160ms 淡入与淡出共用 windowOpacity，finish 时先停入场
  6. 配色在构造时由宿主传入主题色（ConfigManager 建好后即可 show，
     不需要等任何数据/界面就绪）
====================================================================
"""

import math

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QRectF, QPropertyAnimation, QTimer, Qt,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget


def _css_color(value, fallback):
    """把 theme token 的函数式 rgba() 串解析成 QColor。

    QColor 不认函数式 rgba() 串；token 有两种第 4 参写法——
    0-255 整数（glass_fill）与比例小数（primary_border），按 >1
    判别。解析失败回退 fallback，绝不画黑（便签 _css_color 同因）。
    """
    c = QColor(fallback)
    try:
        if not isinstance(value, str) or not value.startswith("rgba("):
            return c
        parts = [t.strip() for t in value[5:value.rindex(")")].split(",")]
        r, g, b = (int(float(x)) for x in parts[:3])
        a = float(parts[3])
        c = QColor(r, g, b)
        c.setAlphaF(a / 255.0 if a > 1 else a)
    except (ValueError, IndexError, TypeError):
        pass
    return c


class LaunchSplash(QWidget):
    """启动闪屏窗（单例使用：main() 创建一次，finish 后销毁）"""

    W, H = 300, 170
    RADIUS = 14          # 卡片圆角
    BALL_R = 17          # 悬浮球半径
    ORBIT_R = 30         # 旋转弧半径
    FADE_IN_MS = 160

    def __init__(self, theme: str = "light",
                 title: str = "FloatPulse · 生活悬浮球"):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.W, self.H)

        self._title = title
        self._stage_text = "正在启动…"
        self._angle = 0            # 旋转弧当前起始角（度；兼作浮动相位源）
        self._closing = False

        # 主题色（构造时定死；闪屏存续期间全局主题不会变）
        from src.theme import get_colors
        c = get_colors(theme if theme in ("light", "dark") else "light")
        self._c_bg = _css_color(c.get("glass_fill"), "#F2FFFFFF")
        self._c_border = _css_color(c.get("primary_border"), "#5BC0BE")
        self._c_primary = QColor(str(c.get("primary_deep", "#3D9E9C")))
        self._c_primary_lite = QColor(str(c.get("primary", "#5BC0BE")))
        self._c_text = QColor(str(c.get("text", "#2C3E50")))
        self._c_shadow = _css_color(c.get("shadow"), "#64000000")
        hi = QColor(255, 255, 255)
        hi.setAlpha(70 if theme == "light" else 28)
        self._c_highlight = hi

        # 居屏幕中上（主窗口大概率随后出现在附近，视觉动线连贯）
        try:
            from src.app_paths import get_screen_geometry
            scr = get_screen_geometry()
            self.move(scr.center().x() - self.W // 2,
                      scr.center().y() - self.H // 2 - 40)
        except Exception:
            pass

        # 动画帧驱动（33ms ≈ 30fps，足够顺滑且省电）
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_frame)

        # 淡入（入场）与淡出（收尾）共用 windowOpacity——finish 时先停入场
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setDuration(self.FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(300)
        self._fade.finished.connect(self._on_fade_done)

    # ---------------- 生命周期 ----------------
    def start(self):
        """显示并开始动画（含 160ms 淡入）"""
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_in.start()
        self._timer.start()

    def set_stage(self, text: str):
        """更新阶段文案（main() 在每个阶段开始时调用）"""
        if text and text != self._stage_text:
            self._stage_text = text
            self.update()

    def finish(self):
        """停止动画并淡出关闭（幂等：重复调用/已销毁均安全返回）"""
        from PyQt6.sip import isdeleted
        if self._closing or isdeleted(self):
            return
        self._closing = True
        self._timer.stop()
        self._fade_in.stop()           # 入场未完就收：先停，免得抢 windowOpacity
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self):
        self.close()
        self.deleteLater()

    def _on_frame(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 玻璃卡片底（本手绘不靠窗口 QSS——translucent 顶层窗不可靠）
        body = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        p.setPen(self._c_border)
        p.setBrush(self._c_bg)
        p.drawRoundedRect(body, self.RADIUS, self.RADIUS)
        # 顶部 1px 内高光：玻璃上缘的反光细节
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c_highlight)
        p.drawRoundedRect(QRectF(2, 2, self.W - 4, 1), 0.5, 0.5)

        # 品牌标题（上方）
        p.setPen(self._c_primary)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 18, self.W, 26),
                   Qt.AlignmentFlag.AlignHCenter, self._title)

        # ---- 悬浮球品牌图形（中央）----
        cx = self.W / 2
        cy = self.H / 2 + 10
        # 浮动：随 _angle 正弦上下 2.5px（周期 = 弧一整圈 1.5s）
        phase = math.radians(self._angle)
        lift = math.sin(phase) * 2.5
        # 影子：球升影子缩小变淡（悬浮感的关键参照物）
        t = (lift + 2.5) / 5.0                       # 0 最低 → 1 最高
        sw = (30 - 8 * t) / 2.0                      # 半宽 15 → 11
        sh = 3.6 - 1.2 * t
        shadow = QColor(self._c_shadow)
        shadow.setAlpha(int(56 * (1.0 - t * 0.6)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(cx, cy + self.ORBIT_R + 16), sw, sh)

        # 环绕弧：背景淡弧 + 前景旋转弧（_angle 帧驱动 → B1 护栏口径）
        r = self.ORBIT_R
        arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        track = QColor(self._c_border)
        track.setAlpha(80)
        p.setPen(QPen(track, 3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, 0, 360 * 16)
        p.setPen(QPen(self._c_primary, 3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, -self._angle * 16, 100 * 16)

        # 球体：径向渐变（高光偏左上 30%）+ 主题色两档
        br = self.BALL_R
        by = cy + lift
        grad = QRadialGradient(QPointF(cx - br * 0.35, by - br * 0.45),
                               br * 1.9)
        grad.setColorAt(0.0, self._c_primary_lite)
        grad.setColorAt(1.0, self._c_primary)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, by), br, br)
        # 高光点
        hl = QColor(255, 255, 255)
        hl.setAlpha(130)
        p.setBrush(hl)
        p.drawEllipse(QPointF(cx - br * 0.38, by - br * 0.42),
                      br * 0.22, br * 0.16)

        # 阶段文案（下方）
        p.setPen(self._c_text)
        f2 = QFont()
        f2.setPointSize(9)
        p.setFont(f2)
        p.drawText(QRectF(0, self.H - 40, self.W, 24),
                   Qt.AlignmentFlag.AlignHCenter, self._stage_text)
        p.end()
