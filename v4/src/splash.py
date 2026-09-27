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
  2. 自绘：品牌青色旋转弧 + 阶段文案；QTimer(33ms) 驱动画帧
  3. main() 在每个启动阶段之间调 set_stage() 更新文案并 processEvents()
     —— 同步段之间事件循环有机会跑，动画就不会全程冻结
  4. finish()：停帧 → 300ms 淡出 → close + deleteLater
  5. 配色在构造时由宿主传入主题色（ConfigManager 建好后即可 show，
     不需要等任何数据/界面就绪）
====================================================================
"""

from PyQt6.QtCore import (
    QRectF, QPropertyAnimation, QTimer, Qt,
)
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class LaunchSplash(QWidget):
    """启动闪屏窗（单例使用：main() 创建一次，finish 后销毁）"""

    W, H = 300, 170

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
        self._angle = 0            # 旋转弧当前起始角（度）
        self._closing = False

        # 主题色（构造时定死；闪屏存续期间全局主题不会变）
        from src.theme import get_colors
        c = get_colors(theme if theme in ("light", "dark") else "light")
        self._c_bg = QColor(str(c.get("card_bg_solid", "#FFFFFF")))
        self._c_border = QColor(str(c.get("primary_border", "#888888")))
        self._c_primary = QColor(str(c.get("primary_deep", "#3D9E9C")))
        self._c_text = QColor(str(c.get("text", "#2C3E50")))
        self._c_muted = QColor(str(c.get("on_primary", "#FFFFFF")))
        self._c_muted.setAlpha(200)

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

        # 淡出动画
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(300)
        self._fade.finished.connect(self._on_fade_done)

    # ---------------- 生命周期 ----------------
    def start(self):
        """显示并开始动画"""
        self.setWindowOpacity(1.0)
        self.show()
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

        # 圆角卡片底
        body = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        p.setPen(self._c_border)
        p.setBrush(self._c_bg)
        p.drawRoundedRect(body, 12, 12)

        # 品牌标题（上方）
        p.setPen(self._c_primary)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 18, self.W, 26),
                   Qt.AlignmentFlag.AlignHCenter, self._title)

        # 旋转弧（中央）：背景淡弧 + 前景旋转弧
        cx, cy = self.W / 2, self.H / 2 + 12
        r = 26
        arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        track = QColor(self._c_border)
        track.setAlpha(80)
        p.setPen(QPen(track, 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, 0, 360 * 16)
        p.setPen(QPen(self._c_primary, 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, -self._angle * 16, 100 * 16)

        # 阶段文案（下方）
        p.setPen(self._c_text)
        f2 = QFont()
        f2.setPointSize(9)
        p.setFont(f2)
        p.drawText(QRectF(0, self.H - 44, self.W, 24),
                   Qt.AlignmentFlag.AlignHCenter, self._stage_text)
        p.end()
