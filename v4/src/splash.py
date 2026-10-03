# -*- coding: utf-8 -*-
"""
====================================================================
启动闪屏  -  splash
====================================================================
主窗口显示前的启动过渡动画。启动序列（数据文件/界面构建/插件加载）
是同步执行的，功能增多后整段可达数秒——闪屏让用户从「白等」变成
「看得见的进度」。

2026-10-03 方案 D 重写（高仿真设计稿 → QPainter 施工基准）：
  1. 无边框 + 半透明 + Tool（不进任务栏、show 时不抢焦点）
  2. 玻璃拟态本体：paintEvent 手绘 glass_fill 填充 + primary_border
     描边 + 顶部内高光（translucent 顶层窗的窗口 QSS 背景在渲染
     管线里不可靠——便签玻璃化的同因先例，便签 E1 护栏实证）
  3. 充能球（因果动画）：每阶段一颗光点从进度环端飞向球心
     （InCubic 吸入），到达才充能——球体 r/glow 与前景环按
     progress_tween 插值；阶段打点快于动画链长时未完链立即结算
     再开新链（跳格但不乱、不丢档，真实节奏「第 6 档长驻」可视）
  4. 满格苏醒（只播一次）：球 scale 1→1.12→1 弹跳 + 光晕扩散；
     完成后触发 after_wake 回调，main() 据此 show 主窗（三段接力：
     苏醒 → 主窗入场 → splash_hold 后闪屏才淡出）
  5. 文案交叉淡变（fast 档）、计数与环同步、版本号右下角
  6. 全部时长经 motion.duration(ms, anim_speed) 缩放；reduce_motion
     下零动画（球静止中位、跳格充能、文案硬切、finish 直接 close）
  7. main() 在每个启动阶段调 set_stage(text, i, total) 并
     processEvents()——同步段之间事件循环有机会跑，动画不冻结
====================================================================
"""

import math
import time

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QRectF, QPropertyAnimation, QTimer, Qt,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from src import motion
from src.app_version import APP_VERSION


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

    W, H = 320, 184
    RADIUS = 14            # 卡片圆角
    BALL_CX = W / 2        # 球心 x（=160）
    BALL_CY = 102          # 球心 y（浮动只作用于球与环，影子钉在地面）
    RING_R = 26.0          # 进度环半径
    RING_W = 2.5           # 进度环线宽
    R_MIN, R_MAX = 9.0, 17.0   # 球体半径随充能 9→17
    GLOW_MIN = 0.55            # 未充能时的基础亮度下限
    HL_FROM = 0.72             # 高光点起现的 glow 阈值（第 4 档起）
    FLOAT_AMP = 2.5            # 浮动振幅（px），周期 1.5s
    FLOAT_PERIOD_MS = 1500.0

    def __init__(self, theme: str = "light",
                 title: str = "FloatPulse · 生活悬浮球",
                 anim_speed: float = 1.0):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.W, self.H)

        self._title = title
        self._anim_speed = motion.sanitize_speed(anim_speed)
        self._closing = False

        # ---- 充能状态机 ----
        self._frac = 0.0          # 当前展示的充能进度（唯一动画量：
        self._target = 0.0        #   r/glow/环弧 全部由它线性导出）
        self._spark = None        # {"t0": ms, "x":, "y":} 环端→球心在途
        self._tween = None        # {"t0": ms, "from":, "to":} 充能插值
        self._counter = 0         # 左下角计数（与环同步的已结算档位）
        self._total = 0           # 阶段总数（0 → 不画计数）
        self._frames = 0          # 帧计数（验证帧驱动存活的观测点）

        # ---- 文案交叉淡变 ----
        self._stage_text = "正在启动…"
        self._text_alpha = 1.0
        self._prev_text = ""
        self._prev_alpha = 0.0
        self._text_t0 = None

        # ---- 满格苏醒（只播一次）----
        self._woke = False        # 苏醒动画已开始（防重播）
        self._wake_t0 = None      # 苏醒动画起点（None = 不在播）
        self._wake_done = False   # 苏醒完成（after_wake 的放行条件）
        self._wake_cbs = []       # 焦点交接回调（三段接力 ①→②）

        # ---- 主题色（构造时定死；闪屏存续期间全局主题不会变）----
        from src.theme import get_colors
        c = get_colors(theme if theme in ("light", "dark") else "light")
        self._c_bg = _css_color(c.get("glass_fill"), "#F2FFFFFF")
        self._c_border = _css_color(c.get("primary_border"), "#5BC0BE")
        self._c_primary = QColor(str(c.get("primary", "#5BC0BE")))
        self._c_primary_deep = QColor(str(c.get("primary_deep", "#3D9E9C")))
        self._c_text = QColor(str(c.get("text", "#2C3E50")))
        self._c_placeholder = QColor(str(c.get("text_placeholder", "#6E6D67")))
        self._c_shadow = _css_color(c.get("shadow"), "#64000000")
        hi = QColor(255, 255, 255)
        hi.setAlpha(70 if theme == "light" else 28)
        self._c_highlight = hi
        self._hl_alpha = 0.5 if theme == "light" else 0.4   # 高光点满档 α

        # 居屏幕中上（主窗口大概率随后出现在附近，视觉动线连贯）
        try:
            from src.app_paths import get_screen_geometry
            scr = get_screen_geometry()
            self.move(scr.center().x() - self.W // 2,
                      scr.center().y() - self.H // 2 - 40)
        except Exception:
            pass

        # 动画帧驱动（33ms ≈ 30fps，足够顺滑且省电）
        self._float_t0 = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_frame)

        # 淡入（base 档）与淡出（splash_out 档）共用 windowOpacity——
        # finish 时先停入场，免得抢 windowOpacity
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._on_fade_done)

    # ---------------- 内部口径 ----------------
    def _ms(self, token: str) -> int:
        """token 时长按 anim_speed 缩放（reduce_motion 下归 0）"""
        return motion.duration(motion.MOTION[token], self._anim_speed)

    @staticmethod
    def _clock_ms() -> float:
        return time.monotonic() * 1000.0

    def _ring_tip_pos(self, frac: float) -> QPointF:
        """前景弧端点（12 点起顺时针 frac×360°），球心坐标系"""
        ang = math.radians(90.0 - 360.0 * frac)
        return QPointF(self.RING_R * math.cos(ang), -self.RING_R * math.sin(ang))

    # ---------------- 生命周期 ----------------
    def start(self):
        """显示并开始动画（base 档淡入；reduce_motion 直接全显）"""
        self.setWindowOpacity(0.0)
        self.show()
        fin = self._ms("base")
        if fin > 0:
            self._fade_in.setDuration(fin)
            self._fade_in.start()
        else:
            self.setWindowOpacity(1.0)
        self._float_t0 = self._clock_ms()
        self._timer.start()

    def set_stage(self, text: str, index: int, total: int):
        """推进启动档位（main() 每阶段调用）：光点从环端飞向球心，
        到达才把球体/前景环充能到 index/total——因果动画。

        上一条链未完成时立即结算到其目标再开新链（快机节奏保护：
        跳格但不乱、不丢档）；文案变化走交叉淡变；计数即时同步。
        """
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            total = 0
            index = 0
        try:
            index = max(0, min(int(index), total))
        except (TypeError, ValueError):
            index = self._counter
        if total:
            self._total = total
            self._counter = index
        # ---- 上一条链立即结算（快机：跳格但不乱、不丢档）----
        self._settle_chain()
        if text and text != self._stage_text:
            self._morph_text(text)
        target = (index / total) if total else 0.0
        if self._ms("spark_fly") <= 0 or self._ms("progress_tween") <= 0:
            # 减弱动效：跳格充能（零动画），满格即标记苏醒完成
            self._frac = self._target = target
            if target >= 1.0:
                self._start_wake()
            self.update()
            return
        self._target = target
        tip = self._ring_tip_pos(self._frac)
        self._spark = {"t0": self._clock_ms(),
                       "x": tip.x(), "y": tip.y()}
        self.update()

    def after_wake(self, cb):
        """满格苏醒完成后调用 cb（三段接力 ①→② 焦点交接点）。

        苏醒已完成（或 reduce_motion 跳过）则下一跳事件循环立即执行。
        """
        if self._wake_done:
            QTimer.singleShot(0, cb)
        else:
            self._wake_cbs.append(cb)

    def finish(self):
        """收尾：结算在途链 → 补发交接回调 → 淡出关闭（幂等）。

        reduce_motion 直接 close（零动画口径）。淡出前先把在途充能链
        结算到目标并放行未触发的 after_wake——主窗显示不因收尾竞争
        而丢失。
        """
        from PyQt6.sip import isdeleted
        if self._closing or isdeleted(self):
            return
        self._closing = True
        self._settle_chain()
        self._mark_awake()             # 兜底：交接不能丢
        self._timer.stop()
        self._fade_in.stop()           # 入场未完就收：先停，免抢 opacity
        if motion.reduce_motion():
            self.close()
            self.deleteLater()
            return
        self._fade.setDuration(max(1, self._ms("splash_out")))
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self):
        self.close()
        self.deleteLater()

    # ---------------- 充能链状态机 ----------------
    def _settle_chain(self):
        """在途链立即结算到目标（快机节奏：跳格但不乱、不丢档）"""
        if self._tween is not None or self._spark is not None:
            self._frac = self._target
        self._spark = None
        self._tween = None

    def _start_tween(self):
        """光点到达 → 球体 r/glow + 前景环向目标插值"""
        self._spark = None
        self._tween = {"t0": self._clock_ms(),
                       "from": self._frac, "to": self._target}

    def _start_wake(self):
        """满格苏醒（只播一次）：弹跳 + 光晕；完成即放行交接回调"""
        self._target = max(self._target, 1.0)
        self._frac = 1.0
        self._woke = True
        self._wake_t0 = self._clock_ms() if self._ms("wake_halo") > 0 else None
        if self._wake_t0 is None:
            self._mark_awake()

    def _mark_awake(self):
        """苏醒完成：放行焦点交接回调（幂等；先标记再异步触发）"""
        self._wake_t0 = None
        self._wake_done = True
        cbs, self._wake_cbs = self._wake_cbs, []
        for cb in cbs:
            QTimer.singleShot(0, cb)

    def _morph_text(self, text: str):
        """阶段文案交叉淡变（fast 档；时长 0 时硬切）"""
        self._prev_text = self._stage_text
        self._prev_alpha = self._text_alpha
        self._stage_text = text
        if self._ms("fast") > 0:
            self._text_alpha = 0.0
            self._text_t0 = self._clock_ms()
        else:
            self._text_alpha = 1.0
            self._prev_alpha = 0.0
            self._prev_text = ""
            self._text_t0 = None
        self.update()

    # ---------------- 帧驱动 ----------------
    def _on_frame(self):
        self._frames += 1
        now = self._clock_ms()

        # 光点在途 → 到达后转入充能插值
        if self._spark is not None:
            ms = self._ms("spark_fly")
            if ms <= 0 or (now - self._spark["t0"]) >= ms:
                self._start_tween()
        if self._tween is not None:
            ms = self._ms("progress_tween")
            t = 1.0 if ms <= 0 else (now - self._tween["t0"]) / ms
            t = max(0.0, min(1.0, t))
            a, b = self._tween["from"], self._tween["to"]
            self._frac = a + (b - a) * t
            if t >= 1.0:
                self._frac = b
                self._tween = None
                if b >= 1.0 and not self._woke:
                    self._start_wake()
        # 苏醒动画播完 → 放行焦点交接
        if self._wake_t0 is not None:
            ms = self._ms("wake_halo")
            if ms <= 0 or (now - self._wake_t0) >= ms:
                self._mark_awake()
        # 文案交叉淡变收敛
        if self._text_t0 is not None:
            ms = self._ms("fast")
            t = 1.0 if ms <= 0 else (now - self._text_t0) / ms
            if t >= 1.0:
                self._text_t0 = None
                self._text_alpha = 1.0
                self._prev_alpha = 0.0
                self._prev_text = ""
            else:
                self._text_alpha = t
                self._prev_alpha = 1.0 - t
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = self._clock_ms()

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

        # ---- 充能球场景（球心坐标系）----
        frac = self._frac
        glow = self.GLOW_MIN + (1.0 - self.GLOW_MIN) * frac
        cx, cy = self.BALL_CX, self.BALL_CY
        # 浮动：±2.5px 正弦（周期 1.5s）；reduce_motion 静止中位
        if self._float_t0 is not None and not motion.reduce_motion():
            phase = ((now - self._float_t0) % self.FLOAT_PERIOD_MS) \
                / self.FLOAT_PERIOD_MS * 2 * math.pi
        else:
            phase = 0.0
        lift = math.sin(phase) * self.FLOAT_AMP
        by = cy + lift
        t_float = (lift + self.FLOAT_AMP) / (self.FLOAT_AMP * 2)  # 0 低→1 高

        # 苏醒弹跳：scale 1→1.12→1（sin 半波）
        scale = 1.0
        if self._wake_t0 is not None:
            ms_pop = self._ms("wake_pop")
            tp = 1.0 if ms_pop <= 0 else (now - self._wake_t0) / ms_pop
            tp = max(0.0, min(1.0, tp))
            scale = 1.0 + 0.12 * math.sin(math.pi * tp)

        # 影子：钉在地面（y=球心+31）；rx 13→16.4 随充能、-4 随浮动，α 联动
        shadow = QColor(self._c_shadow)
        shadow.setAlphaF(shadow.alphaF() * (1.0 - 0.45 * t_float))
        p.setBrush(shadow)
        p.drawEllipse(QPointF(cx, cy + 31.0),
                      13.0 + 3.4 * frac - 4.0 * t_float, 3.4)

        # 苏醒光晕：r 20→36、α 0.55→0（球后、环底）
        if self._wake_t0 is not None:
            ms_halo = self._ms("wake_halo")
            th = 1.0 if ms_halo <= 0 else (now - self._wake_t0) / ms_halo
            th = max(0.0, min(1.0, th))
            halo = QColor(self._c_primary)
            halo.setAlphaF(0.55 * (1.0 - th))
            p.setPen(QPen(halo, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r_halo = 20.0 + 16.0 * th
            p.drawEllipse(QPointF(cx, by), r_halo, r_halo)

        # 进度环：底环 primary@15% + 前景弧（12 点起顺时针 frac×360°）
        ring_rect = QRectF(cx - self.RING_R, by - self.RING_R,
                           self.RING_R * 2, self.RING_R * 2)
        track = QColor(self._c_primary)
        track.setAlphaF(0.15)
        p.setPen(QPen(track, self.RING_W, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(ring_rect, 0, 360 * 16)
        if frac > 0.0001:
            p.setPen(QPen(self._c_primary, self.RING_W, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawArc(ring_rect, 90 * 16, -int(360 * frac * 16))
            # 端点亮点 r3 + 0.35α 光圈 r6
            tip = self._ring_tip_pos(frac)
            tip_pos = QPointF(cx + tip.x(), by + tip.y())
            tip_halo = QColor(self._c_primary)
            tip_halo.setAlphaF(0.35)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tip_halo)
            p.drawEllipse(tip_pos, 6.0, 6.0)
            p.setBrush(self._c_primary)
            p.drawEllipse(tip_pos, 3.0, 3.0)

        # 球体：径向渐变（高光偏左上）——中心 = deep 向白 mix 0.22×glow，
        # 边缘 = deep×glow；半径随充能 9→17 × 苏醒弹跳
        ball_r = (self.R_MIN + (self.R_MAX - self.R_MIN) * frac) * scale
        deep = self._c_primary_deep
        mix = 0.22 * glow
        center_c = QColor(
            int(deep.red() + (255 - deep.red()) * mix),
            int(deep.green() + (255 - deep.green()) * mix),
            int(deep.blue() + (255 - deep.blue()) * mix))
        edge_c = QColor(int(deep.red() * glow), int(deep.green() * glow),
                        int(deep.blue() * glow))
        grad = QRadialGradient(QPointF(cx - ball_r * 0.36, by - ball_r * 0.44),
                               ball_r * 1.9)
        grad.setColorAt(0.0, center_c)
        grad.setColorAt(1.0, edge_c)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, by), ball_r, ball_r)

        # 高光点：glow≥0.72（第 4 档）起浮现
        if glow >= self.HL_FROM:
            hl = QColor(255, 255, 255)
            hl.setAlphaF((glow - self.HL_FROM) / (1.0 - self.HL_FROM)
                         * self._hl_alpha)
            p.setBrush(hl)
            p.drawEllipse(QPointF(cx - 6.1, by - 7.1), 3.5, 3.5)

        # 光点：环端 → 球心，InCubic（ease_t = t²）吸入
        if self._spark is not None:
            ms = self._ms("spark_fly")
            t = 1.0 if ms <= 0 else (now - self._spark["t0"]) / ms
            t = max(0.0, min(1.0, t))
            e = t * t
            sx = self._spark["x"] * (1.0 - e)
            sy = self._spark["y"] * (1.0 - e)
            spark_c = QColor(self._c_primary)
            spark_c.setAlphaF(0.9 - 0.36 * t)      # α 0.9 → 0.54
            p.setBrush(spark_c)
            p.drawEllipse(QPointF(cx + sx, by + sy), 3.0 - 1.5 * t,
                          3.0 - 1.5 * t)

        # 阶段文案（下方，交叉淡变双槽）
        f2 = QFont()
        f2.setPointSize(9)
        p.setFont(f2)
        text_rect = QRectF(0, 142, self.W, 22)
        align = (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        if self._prev_alpha > 0.001 and self._prev_text:
            prev_c = QColor(self._c_text)
            prev_c.setAlphaF(self._prev_alpha)
            p.setPen(prev_c)
            p.drawText(text_rect, align, self._prev_text)
        cur_c = QColor(self._c_text)
        if self._text_alpha < 0.999:
            cur_c.setAlphaF(max(0.05, self._text_alpha))
        p.setPen(cur_c)
        p.drawText(text_rect, align, self._stage_text)

        # 计数（左下）与版本号（右下）
        f3 = QFont()
        f3.setPixelSize(11)
        p.setFont(f3)
        p.setPen(self._c_placeholder)
        if self._total > 0:
            p.drawText(
                QRectF(14, self.H - 16, 100, 13),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{self._counter} / {self._total}")
        p.drawText(
            QRectF(self.W - 114, self.H - 16, 100, 13),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"v{APP_VERSION}")
        p.end()
