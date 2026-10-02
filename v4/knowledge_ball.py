# -*- coding: utf-8 -*-
"""
====================================================================
知识卡片悬浮球  -  Windows 桌面独立小工具（主程序入口）
====================================================================
基于 PyQt6 开发，模仿电脑管家悬浮球效果。

功能特性：
  1. 圆形悬浮小球，可拖拽、始终置顶、半透明背景
  2. 鼠标悬停悬浮球 → 自动弹出卡片；鼠标离开球+卡片区域 → 自动关闭
  3. 拖拽悬浮球时不显示卡片
  4. 卡片弹窗支持「知识卡片 / 日程任务 / 临时笔记」三模式切换
  5. 右键悬浮球 / 右键卡片 → 退出程序；Esc 只关闭当前表面（卡片/快捕条/
     截图框选/钉图/便签），不退出程序（1.3）
  6. 悬浮球靠近桌面上/下/左/右任一边缘 → 自动吸边隐藏一半；鼠标移近滑出
  7. 单实例限制，防止重复启动
  8. 拖拽文件到悬浮球 → 自动加入碎片池
  9. 被动监听剪贴板 → 自动收集文本/路径碎片
 10. docx 知识库可控写入 + 外部修改检测
 11. 大窗口主UI（碎片工作台/任务/笔记/知识库/设置）
 12. 浅色/深色主题切换

模块划分（已模块化拆分）：
  - SingleInstance      : 单实例锁（single_instance.py）
  - CardWindow          : 卡片弹窗类（card_window.py）
  - MainWindow          : 大窗口主UI（main_window.py）
  - FloatingBall        : 悬浮球类（本文件）
  - TaskManager         : 日程任务管理器（task_manager.py）
  - NoteManager         : 笔记管理器（note_manager.py）
  - FragmentManager     : 碎片管理器（fragment_manager.py）
  - ClipboardMonitor    : 剪贴板监听器（clipboard_monitor.py）
  - DocxManager         : docx 管理器（docx_manager.py）
  - ConfigManager       : 配置管理器（config.py）
  - theme               : 主题系统（theme.py）
  - TrayController      : 系统托盘（tray.py，成熟化 4.3 D1-lite）
  - ConfigHotkeyBinding : 全局热键绑定样板（hotkey_binding.py，成熟化 4.3 D4）
  - main                : 程序入口（本文件）
====================================================================
"""

import sys
import os
import struct
import time
import json
import logging
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QMessageBox,
    QSystemTrayIcon,
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QTimer, QPropertyAnimation, QEasingCurve,
    QRectF, QSequentialAnimationGroup, pyqtProperty, pyqtSignal, QObject,
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QFont, QFontMetrics, QPen, QAction, QCursor,
    QIcon, QRadialGradient,
)

# 引入独立模块
from src.single_instance import SingleInstance
from src.card_window import CardWindow
from src.app_paths import find_icon_file, get_base_dir, get_screen_geometry
from src.task_manager import (
    TaskManager, task_state, bucket_unfinished,
    STATE_TODAY, STATE_OVERDUE,
)
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager, TYPE_CLIPBOARD_TEXT
from src.clipboard_monitor import ClipboardMonitor
from src.docx_manager import DocxManager
from src.temp_asset_manager import TempAssetManager, REJECT_TOO_LARGE
from src.config import ConfigManager
from src.nav_manager import NavManager
from src.main_window import MainWindow
from src.theme import get_menu_qss, get_colors, resolve_theme_name, \
    apply_app_font
from src.controls import ScreenToast
from src.constants import sanitize_filename, DEFAULT_THEME
from src.pomodoro import (
    PomodoroTimer, PHASE_FOCUS, PHASE_BREAK,
    STATE_IDLE, STATE_RUNNING, STATE_PAUSED,
)
from src.ai_server import AI_SERVER


# ====================================================================
# 启动时数据完整性检查：扫描所有 JSON 文件，损坏的记录到日志
# ====================================================================
def _check_data_integrity(data_dir: str, logger):
    """
    启动时扫描 float_data/ 目录下所有 JSON 文件，检测损坏。
    损坏文件记录到日志，不弹窗（各管理器会自动初始化空数据）。
    """
    json_files = [
        "config.json", "schedule.json", "notes.json",
        "fragments.json", "docx_meta.json", "nav.json",
        "temp_assets.json",
    ]
    corrupted = []
    for fname in json_files:
        fpath = os.path.join(data_dir, fname)
        if not os.path.exists(fpath):
            continue  # 缺失文件是正常的（首次运行）
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, (dict, list)):
                raise ValueError(f"Invalid structure: expected dict/list, got {type(data).__name__}")
            logger.debug(f"JSON 完整性检查通过: {fname}")
        except json.JSONDecodeError as e:
            corrupted.append((fname, f"JSON 解析失败: {e}"))
            logger.warning(f"JSON 文件损坏: {fname} - {e}")
        except Exception as e:
            corrupted.append((fname, str(e)))
            logger.warning(f"JSON 文件异常: {fname} - {e}")

    if corrupted:
        # 损坏文件较多时弹窗提示用户
        if len(corrupted) >= 2:
            details = "\n".join(f"  • {f}: {r}" for f, r in corrupted)
            QMessageBox.warning(
                None, "数据完整性检查",
                f"检测到 {len(corrupted)} 个数据文件损坏，已自动重置为空数据：\n\n"
                f"{details}\n\n"
                f"详情请查看日志：float_data/app.log"
            )
        logger.warning(f"启动检查完成：{len(corrupted)} 个文件损坏已重置")
    else:
        logger.info("启动检查完成：所有 JSON 文件完整")


# 全局异常钩子的唯一实现是 src/logger.py 的 install_excepthook()（在 main() 中安装）。
# 此处曾有一份重复实现，会被日志系统初始化时的 install_excepthook() 覆盖 ——
# 已删除，避免后续排查「弹窗行为」时改错文件。


# ====================================================================
# 模块：悬浮球子绘制控件
# ====================================================================
class _BallSurface(QWidget):
    """
    悬浮球子绘制控件：专职负责视觉与动画（宿主只做交互控制）。

    - 宿主用更大的透明 Tool 窗口承载本控件，使放大后的球体与增强投影
      不会超出 64×64 的固定边界而被裁剪。
    - 视觉缩放（scale）与投影增强（glow）均为浮点属性，QPropertyAnimation
      逐帧插值驱动，绝不 round，保证丝滑无顿挫。
    - sequence: 按下缩小(OutCubic) / 拖拽放大(OutCubic) / 悬停放大(OutQuad)
      释放回弹(OutBack)，由宿主按状态计算目标值后调用 animate_scale 叠加实现。
    """

    def __init__(self, ball_size: int, parent: QWidget,
                 theme: str = DEFAULT_THEME):
        super().__init__(parent)
        self._ball_size = ball_size
        self._theme = theme
        self._scale = 1.0      # 视觉缩放倍率（按下/拖拽/悬停 叠加计算的结果）
        self._glow = 0.0       # 投影增强系数（0 普通阴影，1 拖拽增强阴影）
        self._hovered = False
        self._dragging = False
        self._pixmap = None    # 缓存图标，None 未加载 / False 不存在
        self._badge_text = ""  # 右下角徽标文字（空串不绘制）
        self._pulse_seq = None  # 脉冲动画引用（防 GC）
        # 番茄钟进度环（绘制层只收 number 和 color，不做业务逻辑）
        self._ring_progress = 0.0   # 0.0~1.0
        self._ring_active = False   # False → 整层不画（idle 球上无痕迹）
        self._ring_track = None     # QColor 或 None
        self._ring_fill = None      # QColor 或 None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 鼠标全部透传给宿主处理（本控件只绘制，不做交互）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # ---------------- 主题 ----------------
    def set_theme(self, theme_name: str):
        """切换球体配色（浅色/深色主色不同）"""
        if theme_name in ("light", "dark") and theme_name != self._theme:
            self._theme = theme_name
            self.update()

    # ---------------- 对外状态 ----------------
    def set_appearance(self, hovered=None, dragging=None):
        changed = False
        if hovered is not None and hovered != self._hovered:
            self._hovered = hovered
            changed = True
        if dragging is not None and dragging != self._dragging:
            self._dragging = dragging
            changed = True
        if changed:
            self.update()

    # ---------------- 尺寸 / 徽标 / 脉冲 ----------------
    def set_ball_size(self, size: int):
        """外部调整球体直径（设置页）：重载图标缓存并按新尺寸绘制"""
        if int(size) == self._ball_size:
            return
        self._ball_size = int(size)
        self._pixmap = None     # 尺寸变化 → 重新取图
        self.update()

    def set_badge(self, count: int):
        """设置右下角徽标（0 或负数 → 隐藏）。超过 99 显示 99+"""
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        text = "" if count <= 0 else ("99+" if count > 99 else str(count))
        if text != self._badge_text:
            self._badge_text = text
            self.update()

    # ---------------- 番茄钟进度环 ----------------
    def set_ring_colors(self, track: QColor, fill: QColor):
        """设置环的轨道/进度配色（由宿主从主题字典取色后传入）"""
        self._ring_track = QColor(track)
        self._ring_fill = QColor(fill)
        if self._ring_active:
            self.update()

    def set_ring_progress(self, value: float, active: bool):
        """更新进度环（每秒一次即可，不要更高频重绘）。

        - active=False → 整层不画（idle 状态球上不得有任何痕迹）
        - value 夹取 0.0~1.0
        """
        value = max(0.0, min(1.0, float(value)))
        changed = (value != self._ring_progress) or (active != self._ring_active)
        self._ring_progress = value
        self._ring_active = bool(active)
        if changed:
            self.update()

    def pulse(self):
        """光晕脉冲一次：成功反馈（拖入文件 / 剪贴板捕获 / 快速捕捉）"""
        if self._dragging or self._glow > 0.5:
            return
        seq = QSequentialAnimationGroup(self)
        up = QPropertyAnimation(self, b"glow", self)
        up.setDuration(150)
        up.setStartValue(self._glow)
        up.setEndValue(1.0)
        up.setEasingCurve(QEasingCurve.Type.OutQuad)
        down = QPropertyAnimation(self, b"glow", self)
        down.setDuration(360)
        down.setStartValue(1.0)
        down.setEndValue(0.0)
        down.setEasingCurve(QEasingCurve.Type.InOutQuad)
        seq.addAnimation(up)
        seq.addAnimation(down)
        self._pulse_seq = seq     # 持引用防 GC
        seq.start()

    # ---------------- 浮点动画属性 ----------------
    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float):
        self._scale = float(value)
        self.update()

    scale = pyqtProperty(float, _get_scale, _set_scale)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float):
        self._glow = float(value)
        self.update()

    glow = pyqtProperty(float, _get_glow, _set_glow)

    def animate_scale(self, target, dur_ms, curve, on_finished=None):
        anim = QPropertyAnimation(self, b"scale", self)
        anim.setDuration(max(1, int(dur_ms)))
        anim.setStartValue(self._scale)
        anim.setEndValue(float(target))
        anim.setEasingCurve(curve)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        anim.start()
        return anim

    def animate_glow(self, target, dur_ms, curve, on_finished=None):
        anim = QPropertyAnimation(self, b"glow", self)
        anim.setDuration(max(1, int(dur_ms)))
        anim.setStartValue(self._glow)
        anim.setEndValue(float(target))
        anim.setEasingCurve(curve)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        anim.start()
        return anim

    # ---------------- 绘制 ----------------
    def _load_pixmap(self):
        if self._pixmap is not None:
            return
        # 兼容 PyInstaller：图标可能位于 _internal / exe 同级 / 父目录
        icon_path = find_icon_file()
        if icon_path:
            icon = QIcon(icon_path)
            pm = icon.pixmap(self._ball_size * 2, self._ball_size * 2)
            self._pixmap = pm.scaled(
                self._ball_size, self._ball_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self._pixmap = False

    def paintEvent(self, event):
        """球体绘制（分三层，绘制顺序即层序）：
          1. 柔性外阴影 + 接地扁阴影（径向渐变，随 glow 扩散）
          2. ico 图标铺满球体 + 1px 边缘描边（浅底压暗边 / 深底提亮边）
          3. 右下角徽标（未处理任务数）
        """
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        cx = w / 2.0
        cy = h / 2.0
        vis_r = (self._ball_size / 2.0) * self._scale   # 浮点半径，不 round

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._paint_shadow(painter, cx, cy, vis_r, self._glow)
        self._paint_ring(painter, cx, cy, vis_r)
        self._paint_ball(painter, cx, cy, vis_r)
        self._paint_badge(painter, cx, cy, vis_r)
        painter.end()

    def _paint_ring(self, painter, cx, cy, vis_r):
        """番茄钟进度环：球体外圈 vis_r+4 处，12 点方向起顺时针。

        - active=False 整层不画；进度 0 也不画（避免只剩一个点）
        - drawArc 角度单位 1/16 度；负跨角 = 顺时针
        """
        if not self._ring_active or self._ring_fill is None:
            return
        if self._ring_progress <= 0.0:
            return
        r = vis_r + 4.0
        rect = QRectF(cx - r, cy - r, r * 2.0, r * 2.0)
        pen = QPen(self._ring_fill)
        pen.setWidthF(max(2.5, vis_r * 0.12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 12 点方向（90°）起，顺时针扫 progress × 360°
        painter.drawArc(rect, 90 * 16, -int(self._ring_progress * 360 * 16))
        painter.restore()

    # ---------------- 阴影：径向渐变（替代多层同心椭圆）----------------
    # 浅色桌面：靠较深的投影体现"接地"；深色桌面：投影压淡（黑底上黑影子显脏），
    # 改由下面 _paint_ball 里的 1px 亮描边区分球体边界
    _SHADOW = {
        "light": {"alpha": 88, "off": 4.0, "spread": 0.36, "contact": 62},
        "dark":  {"alpha": 72, "off": 3.0, "spread": 0.24, "contact": 46},
    }

    def _paint_shadow(self, painter, cx, cy, vis_r, glow):
        cfg = self._SHADOW.get(self._theme, self._SHADOW["light"])
        alpha = int(cfg["alpha"] * (1.0 + 0.35 * glow))
        off = cfg["off"] + (7.0 - cfg["off"]) * glow        # 拖拽时阴影下移
        spread = cfg["spread"] * (1.0 + 0.85 * glow)
        outer = min(vis_r * (1.0 + spread), self.width() / 2.0 - 2.0)
        # 1) 主阴影：中心在球心下方，边缘渐隐到全透明（无同心圆台阶）
        self._radial_shadow(painter, cx, cy + off, vis_r * 0.92, outer, alpha)
        # 2) 接地阴影：垂直压扁聚在球体下缘，制造"坐实"感
        painter.save()
        painter.translate(cx, cy + vis_r * 0.80)
        painter.scale(1.0, 0.30)
        contact_alpha = int(cfg["contact"] * (1.0 + 0.4 * glow))
        self._radial_shadow(painter, 0.0, 0.0, vis_r * 0.30, vis_r * 1.10,
                            contact_alpha)
        painter.restore()

    def _radial_shadow(self, painter, cx, cy, r_in, r_out, alpha):
        """以 (cx,cy) 为中心、r_out 为半径画一团径向渐隐黑影（r_in 内为峰值）

        单次渐变绘制替代原来的 3~6 层同心椭圆叠加：既消除可见环带，
        也把每帧阴影绘制调用从 6 次降到 2 次。
        """
        if r_out <= 1.0 or alpha <= 0:
            return
        grad = QRadialGradient(cx, cy, r_out)
        pos0 = max(0.0, min(0.95, r_in / r_out))
        for t, k in ((0.00, 1.00), (0.20, 0.70), (0.42, 0.42),
                     (0.64, 0.20), (0.84, 0.07), (1.00, 0.0)):
            col = QColor(0, 0, 0)
            col.setAlpha(int(alpha * k))
            grad.setColorAt(pos0 + (1.0 - pos0) * t, col)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QPointF(cx, cy), r_out, r_out)

    # ---------------- 球体 ----------------
    def _paint_ball(self, painter, cx, cy, vis_r):
        self._load_pixmap()
        if self._pixmap:
            side = vis_r * 2.0
            painter.drawPixmap(
                QRectF(cx - vis_r, cy - vis_r, side, side),
                self._pixmap,
                QRectF(self._pixmap.rect()),
            )
        else:
            # 降级：图标不存在时绘制灯泡 emoji
            painter.setPen(QColor(255, 255, 255, 235))
            painter.setFont(QFont("Microsoft YaHei", 15, QFont.Weight.Bold))
            painter.drawText(
                QRectF(0, 0, self.width(), self.height()),
                Qt.AlignmentFlag.AlignCenter, "💡"
            )
        # 边缘描边：让球体从桌面上"浮起"（浅底压暗边、深底提亮边）
        edge = QColor(255, 255, 255, 34) if self._theme == "dark" \
            else QColor(0, 0, 0, 20)
        pen = QPen(edge)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), vis_r - 0.5, vis_r - 0.5)

    # ---------------- 徽标 ----------------
    def _paint_badge(self, painter, cx, cy, vis_r):
        text = self._badge_text
        if not text:
            return
        d = max(17.0, vis_r * 0.60)            # 徽标高度
        font = QFont("Microsoft YaHei", max(8, int(d * 0.52)), QFont.Weight.Bold)
        text_w = QFontMetrics(font).horizontalAdvance(text)
        w = max(d, text_w + 10.0)              # 数字长时自动变胶囊形
        rect = QRectF(cx + vis_r * 0.68 - w / 2.0,
                      cy + vis_r * 0.68 - d / 2.0, w, d)
        # 白色描边让徽标从暖黄球体上浮起（深浅桌面都清晰）
        pen = QPen(QColor(255, 255, 255, 235))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(0xE5, 0x48, 0x4D)))
        painter.drawRoundedRect(rect, d / 2.0, d / 2.0)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# ====================================================================
# 模块：悬停轮询空闲降频状态机（纯逻辑，不依赖 Qt，可离线单测）
# ====================================================================
class HoverPollPolicy:
    """悬停检测定时器的空闲降频决策（优化调研 6.1）。

    背景：卡片可见期间 `_hover_check_timer` 以固定 50ms 全速轮询鼠标位置；
    用户盯着卡片阅读（鼠标静止）时这些轮询全是空转。本状态机在不改变
    交互观感的前提下空闲降频：

      - 任何活动（进入/离开/移动、球交互、卡片显隐）→ 立即回到 50ms；
      - 持续静止超过 IDLE_AFTER_MS（1 秒）→ 降到 IDLE_INTERVAL_MS（200ms）。

    最坏感知延迟：降频期间鼠标一动，最多 200ms 后下一次 tick 就会读到新
    位置；且 enterEvent / mouseMoveEvent 本身也会立刻唤醒（note_activity），
    「移近球弹卡」不会可感知迟钝。真值表见 tests/test_ball_poll.py。
    """

    ACTIVE_INTERVAL_MS = 50        # 活动期轮询间隔（= FloatingBall.HOVER_CHECK_INTERVAL）
    IDLE_INTERVAL_MS = 200         # 空闲降频后的轮询间隔（最坏感知延迟上限）
    IDLE_AFTER_MS = 1000           # 持续静止多久后降频

    def __init__(self):
        self.last_activity_ms = 0.0   # 最近一次活动的 monotonic 时间戳（秒）
        self.slowed = False           # 当前是否处于降频态

    @staticmethod
    def next_interval(idle_ms: int, active: bool) -> int:
        """真值表：活动 → 50；静止 <1s → 50；静止 ≥1s → 200"""
        if active or idle_ms < HoverPollPolicy.IDLE_AFTER_MS:
            return HoverPollPolicy.ACTIVE_INTERVAL_MS
        return HoverPollPolicy.IDLE_INTERVAL_MS

    def note_activity(self):
        """记录一次活动：静止计时清零；若已降频则返回应恢复的间隔，否则 None"""
        self.last_activity_ms = time.monotonic()
        if self.slowed:
            self.slowed = False
            return self.ACTIVE_INTERVAL_MS
        return None

    def on_tick(self, now_ms=None) -> int:
        """每次轮询 tick 调用：返回当前应使用的间隔（可能触发降频）。

        now_ms 供测试注入 monotonic 秒值；缺省取当前时间。
        """
        now = time.monotonic() if now_ms is None else now_ms
        idle_ms = int((now - self.last_activity_ms) * 1000)
        interval = self.next_interval(idle_ms, active=False)
        self.slowed = interval != self.ACTIVE_INTERVAL_MS
        return interval


# ====================================================================
# 模块：悬浮球类
# ====================================================================
class FloatingBall(QWidget):
    """
    圆形悬浮球。

    交互逻辑：
      - 鼠标悬停悬浮球 → 自动弹出卡片（恢复上次关闭时的模式）
      - 鼠标离开「球 + 卡片」区域 → 自动关闭卡片（三模式统一）
      - 按下悬浮球 → 快速缩小（按下态）；拖拽开始(位移>6px) → 放大+增强投影并立即收回卡片
      - 拖拽卡片过程中卡片保持显示，不自动关闭（CardWindow.is_locked 返回 True）
      - 点击悬浮球（未拖动）→ 知识卡片模式切换下一张
      - 右键悬浮球 → 菜单（打开主窗口 / 退出程序）
      - 拖到桌面上/下/左/右任一边缘释放 → 吸边隐藏一半；鼠标移近滑出
      - 拖文件到悬浮球 → 自动加入碎片池
    """

    BALL_SIZE = 64                # 悬浮球球体直径（默认值，运行时由配置 ball_size 覆盖）
    SHADOW_MARGIN = 24            # 宿主窗口预留画布边距（容纳放大球体+增强投影）
    HOST_SIZE = BALL_SIZE + SHADOW_MARGIN * 2   # 宿主窗口边长（默认值，运行时用 _host_size）
    MIN_BALL_SIZE = 48            # 球体直径下限（与 config.ball_size 范围一致）
    MAX_BALL_SIZE = 88            # 球体直径上限
    POS_SAVE_DELAY = 600          # 拖拽结束后位置落盘防抖（毫秒）
    EDGE_THRESHOLD = 40           # 吸边触发距离（像素，以球心到边缘距离计）
    HIDE_HALF = 32                # 吸边后隐藏的像素数（露出球体另一半）
    HOVER_CHECK_INTERVAL = 50     # 悬停检测定时器间隔（毫秒）
    HOVER_LEAVE_COUNT = 2         # 连续多少次检测到离开才关闭

    # 拖拽/缩放/吸附 参数
    DRAG_THRESHOLD = 6            # 判定拖拽开始的最小位移（曼哈顿距离，像素）
    PRESS_SHRINK = 0.08           # 按下态快速缩小比例
    DRAG_ENLARGE = 0.08           # 拖拽态额外放大比例
    HOVER_GROW = 0.04             # 悬停态微放大比例
    RUBBER_DAMP = 0.25            # 越界后橡皮筋跟随衰减系数（1/4）
    RUBBER_OVERRUN = 1.5          # 允许越界量（倍于宿主窗口宽度）

    # 信号
    request_quit = pyqtSignal()   # 请求退出程序
    # 番茄钟相位计满（phase=focus/break, bound_title=绑定任务标题）
    pomodoro_phase_finished = pyqtSignal(str, str)
    # 番茄钟状态变更中继（str=新状态）：外部接线入口（全屏让位对齐）——
    # 此前外部直接戳 ball._pomodoro.state_changed 私有成员（D3 穿透清零）
    pomodoro_state_changed = pyqtSignal(str)

    def __init__(self, cards, task_manager=None, note_manager=None,
                 fragment_manager=None, docx_manager=None,
                 config_manager=None, clipboard_monitor=None,
                 main_window=None, temp_asset_manager=None):
        super().__init__()
        self._cards = cards
        self._task_manager = task_manager
        self._note_manager = note_manager
        self._fragment_manager = fragment_manager
        self._docx_manager = docx_manager
        self._config = config_manager
        self._clipboard_monitor = clipboard_monitor
        self._main_window = main_window
        self._temp_asset_manager = temp_asset_manager
        # 主题取 config 原始值（可能是 "follow"）→ 解析成具体主题再进
        # 内部链路：_BallSurface / 卡片窗口都按 light|dark 显式比对，
        # 解析统一收口在 theme.resolve_theme_name（3.1 跟随系统）
        self._theme = (resolve_theme_name(config_manager.get("theme", DEFAULT_THEME))
                       if config_manager else DEFAULT_THEME)
        # 动画速度档位（0.5-2.0，统一缩放各类动画时长）
        try:
            self._anim_speed = float(config_manager.get("anim_speed", 1.0)) \
                if config_manager else 1.0
            self._anim_speed = max(0.5, min(2.0, self._anim_speed))
        except (TypeError, ValueError):
            self._anim_speed = 1.0

        # 交互状态
        self._pressed = False            # 左键按下态
        self._dragging = False           # 拖拽态（位移超过阈值后）
        self._moved = False              # 本次按下是否发生过有效移动
        self._drag_offset = QPoint()     # 按下时鼠标相对宿主左上角的偏移
        self._press_pos = QPoint()       # 按下时鼠标全局坐标
        self._card_offset = QPoint()     # 卡片相对悬浮球的偏移（协同移动用）
        self._hovered = False
        self._hidden_to_edge = False
        self._edge_side = None
        self._anim = None                # 宿主位移动画（吸边/滑出）
        self._out_count = 0
        # 空闲吸边自动隐藏总开关（设置页可关）：关闭后球始终完整显示，
        # 贴边不再半隐藏。启动时由 _apply_auto_hide_config() 从配置读取覆盖。
        self._auto_hide_enabled = True
        # 全屏应用让位（B8）：全屏时自动隐藏、退出全屏恢复，不改变用户的手动隐藏意愿
        self._fs_hidden = False
        # 未吸边隐藏时的"正常位置"（C4）：落盘用它，避免存下半个在屏外的坐标
        self._normal_pos = None
        # 球体尺寸（C1 可配置）：宿主窗口 = 球径 + 两侧投影留白
        self._ball_size = self._resolve_ball_size()
        self._host_size = self._ball_size + self.SHADOW_MARGIN * 2

        self._init_window()
        self._init_card_window()
        self._init_context_menu()
        self._init_hover_timer()
        self._init_pomodoro()

        # 接受文件拖拽
        self.setAcceptDrops(True)

        # 启动位置恢复：读取上次保存位置，无记录则放主屏左缘垂直居中
        self.move(self._resolve_initial_position())
        self._normal_pos = self.pos()
        # 任务徽标（A4）：启动即按当前任务数据点亮
        self.refresh_badge()

    # ---------------- 初始化 ----------------
    def _resolve_ball_size(self) -> int:
        """球体直径：读配置并夹到合法区间（配置缺失/异常时回退默认 64）"""
        size = self.BALL_SIZE
        if self._config is not None:
            try:
                size = int(self._config.get("ball_size", self.BALL_SIZE))
            except (TypeError, ValueError):
                size = self.BALL_SIZE
        return max(self.MIN_BALL_SIZE, min(self.MAX_BALL_SIZE, size))

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedSize(self._host_size, self._host_size)
        # 注意：不使用 QGraphicsDropShadowEffect，
        # 它与 WA_TranslucentBackground 在 Windows 上会导致
        # UpdateLayeredWindowIndirect 报错。阴影改由子控件手动绘制。
        # 子绘制控件铺满宿主，负责视觉与动画；本宿主只做交互控制。
        self._surface = _BallSurface(self._ball_size, self, theme=self._theme)
        self._surface.setGeometry(0, 0, self._host_size, self._host_size)

    def _init_card_window(self):
        self._card_window = CardWindow(theme=self._theme)
        self._card_window.set_cards(self._cards)
        if self._task_manager:
            self._card_window.set_task_manager(self._task_manager)
        if self._note_manager:
            self._card_window.set_note_manager(self._note_manager)
        # 卡片拖动时悬浮球同步跟随，保持二者相对位置
        self._card_window.card_moved.connect(self._on_card_moved)
        # 在卡片上按下左键 → 先校准相对偏移，避免球跟错位置
        self._card_window.card_drag_started.connect(self._on_card_drag_started)
        # 卡片拖动松手 → 恢复球的空闲吸边计时（拖动期间是暂停的）
        self._card_window.card_drag_finished.connect(self._on_card_drag_finished)

    def _init_context_menu(self):
        """右键菜单骨架：打开主窗口 | [番茄钟项] | [插件动作] | 追加项 | 退出程序"""
        self._menu = QMenu(self)
        self._menu.setStyleSheet(get_menu_qss(self._theme))
        # 动作注册表与插件上下文（由 main() 注入；未注入时菜单退化为内置两项）
        self._action_registry = None
        self._plugin_ctx = None
        # 运行时追加项（add_context_action）：text / callback / separator_before / QAction
        self._extra_context_actions = []
        self._rebuild_context_menu()

    def _rebuild_context_menu(self):
        """按当前注册表重建右键菜单。

        顺序：打开主窗口 → [番茄钟项] → [插件功能子菜单] → 运行时追加项 → 退出程序。
        「退出程序」固定垫底（2026-09-27 用户要求）；
        截图钉屏等追加项保持插在退出程序之前。
        插件动作自 2026-10-01 起收进「🧩 插件功能」子菜单（Win11「新建 >」
        同款层级，用户拍板）：一级菜单不再随安装插件数量线性变长。
        """
        self._menu.clear()

        # 打开主窗口
        open_main_action = QAction("🖥  打开主窗口", self._menu)
        open_main_action.triggered.connect(self._open_main_window)
        self._menu.addAction(open_main_action)

        # 番茄钟菜单项（按状态显隐；功能关闭时整组不出现）
        pom_entries = self._pomodoro_menu_entries()
        if pom_entries:
            self._menu.addSeparator()
            for text, callback in pom_entries:
                act = QAction(text, self._menu)
                act.triggered.connect(callback)
                self._menu.addAction(act)

        # 插件功能子菜单（注册表数据驱动：menu=True 且启用中的动作）。
        # 子菜单显式套同一份 QSS（与托盘便签子菜单同款做法，确保玻璃观感一致）
        plugin_actions = (self._action_registry.menu_actions()
                          if self._action_registry is not None else [])
        if plugin_actions:
            self._menu.addSeparator()
            plugin_menu = QMenu("🧩 插件功能", self._menu)
            plugin_menu.setStyleSheet(get_menu_qss(self._theme))
            for act in plugin_actions:
                qa = QAction(act.title or act.id, plugin_menu)
                icon_path = getattr(act, "icon_path", None)
                if icon_path and os.path.isfile(icon_path):
                    qa.setIcon(QIcon(icon_path))
                qa.triggered.connect(
                    lambda _checked=False, aid=act.id: self._trigger_action(aid))
                plugin_menu.addAction(qa)
            self._menu.addMenu(plugin_menu)

        # 运行时追加项（add_context_action，如截图钉屏）
        for entry in self._extra_context_actions:
            actions = self._menu.actions()
            if entry["separator_before"] and actions and not actions[-1].isSeparator():
                self._menu.addSeparator()
            act = QAction(entry["text"], self._menu)
            act.triggered.connect(entry["callback"])
            self._menu.addAction(act)
            entry["action"] = act

        # 退出程序（固定在最底部）
        actions = self._menu.actions()
        if actions and not actions[-1].isSeparator():
            self._menu.addSeparator()
        exit_action = QAction("退出程序", self._menu)
        exit_action.triggered.connect(self._request_quit)
        self._menu.addAction(exit_action)

    def _trigger_action(self, action_id):
        """触发插件动作（插件的异常由注册表兜住，不会波及悬浮球）"""
        if self._action_registry is not None:
            self._action_registry.trigger(action_id, self._plugin_ctx)

    # ---------------- 动作注册表（插件框架接线）----------------
    def set_action_registry(self, registry, ctx=None):
        """注入动作注册表与插件上下文，并重建右键菜单"""
        self._action_registry = registry
        self._plugin_ctx = ctx
        self._rebuild_context_menu()

    def refresh_plugin_menu(self):
        """插件动作变化（启用/禁用/重载）后重建右键菜单的唯一刷新入口"""
        self._rebuild_context_menu()

    # ---------------- 公开门面（D3 穿透清零 2026-09-30）----------------
    @property
    def card_window(self) -> CardWindow:
        """小卡片窗口公开访问器。

        宿主（main()）此前一律 ``ball._card_window.xxx`` 直戳私有成员，
        升为公开特性后走此处；小卡片自身的操作继续走 CardWindow 公开 API。
        """
        return self._card_window

    def open_main_window(self):
        """公开入口：打开主窗口（供插件上下文等外部调用）"""
        self._open_main_window()

    def show_card_mode(self, mode: str) -> bool:
        """公开入口：弹出小卡片并切到指定模式

        mode：fragment / task / note / nav / asset / app
        """
        w = self._card_window
        w.switch_mode(mode)
        w.popup_near(self._ball_visual_rect())
        return True

    def _open_main_window(self):
        """打开大窗口主UI（带毫秒级打点：定位真机"打开未响应数秒"阻塞段）"""
        if self._main_window is not None:
            from src.logger import get_logger
            t0 = time.perf_counter()
            self._main_window.show()
            t1 = time.perf_counter()
            self._main_window.raise_()
            self._main_window.activateWindow()
            t2 = time.perf_counter()
            get_logger().info(
                f"[主窗口] 打开: show={(t1 - t0) * 1000:.0f}ms "
                f"raise+activate={(t2 - t1) * 1000:.0f}ms"
            )
            # 300ms 后确认事件循环存活（若期间被阻塞，看门狗会另行记录）
            def _alive():
                get_logger().info(
                    f"[主窗口] 打开完成, visible={self._main_window.isVisible()}")
            QTimer.singleShot(300, _alive)

    def add_context_action(self, text, callback, separator_before=True):
        """运行时向右键菜单追加动作（兼容入口，如：截图钉屏）。

        callback 无参调用；separator_before 决定是否在动作前加分隔线
        （若菜单末尾已是分隔线则不重复加）。
        追加项排在菜单末尾（与历史行为一致），插件动作不受影响。
        """
        entry = {"text": text, "callback": callback,
                 "separator_before": bool(separator_before), "action": None}
        self._extra_context_actions.append(entry)
        self._rebuild_context_menu()
        return entry["action"]

    # ---------------- 番茄钟（进度环 + 菜单 + 任务绑定）----------------
    def _init_pomodoro(self):
        """创建番茄钟计时器并接线（配置读取/信号连接/环配色）。"""
        self._pomodoro_enabled = True
        self._pomodoro = PomodoroTimer(self)
        self._pomodoro.ticked.connect(self._on_pomodoro_ticked)
        self._pomodoro.phase_changed.connect(self._on_pomodoro_phase_changed)
        self._pomodoro.state_changed.connect(self._on_pomodoro_state_changed)
        self._pomodoro.finished.connect(self._on_pomodoro_finished)
        # 状态变更中继到公开信号：必须排在内部处理器之后连接，保证外部槽
        # 仍在内部同步之后运行（与原外部直连 state_changed 的顺序一致）
        self._pomodoro.state_changed.connect(self.pomodoro_state_changed)
        self._apply_ring_colors()
        self.apply_pomodoro_config()

    def _apply_ring_colors(self):
        """按当前主题取环配色：专注=主色深档，休息=成功绿。"""
        colors = get_colors(self._theme)
        track = QColor(str(colors.get("text", "#2C3E50")))
        track.setAlpha(60)
        focus_fill = QColor(str(colors.get("primary_deep", "#3D9E9C")))
        break_fill = QColor(str(colors.get("success", "#1F8A4C")))
        self._surface.set_ring_colors(track, focus_fill)
        # 相位切换时换填充色（休息相位用 break_fill）
        if self._pomodoro.phase == PHASE_BREAK:
            self._surface.set_ring_colors(track, break_fill)
        self._ring_break_fill = break_fill
        self._ring_track = track

    def apply_pomodoro_config(self):
        """设置页/恢复默认后重读配置：时长、总开关、菜单与环全量同步。

        关闭总开关时：停止计时、清环清 tooltip、菜单项移除。
        """
        cfg = self._config
        self._pomodoro_enabled = bool(cfg.get("pomodoro_enabled", True)) if cfg else True
        focus_min = int(cfg.get("pomodoro_focus_minutes", 25)) if cfg else 25
        break_min = int(cfg.get("pomodoro_break_minutes", 5)) if cfg else 5
        auto_break = bool(cfg.get("pomodoro_auto_break", False)) if cfg else False
        self._pomodoro.configure(focus_minutes=focus_min,
                                 break_minutes=break_min,
                                 auto_break=auto_break)
        if not self._pomodoro_enabled and self._pomodoro.state != STATE_IDLE:
            self._pomodoro.stop()
        self._update_pomodoro_visuals()
        self._rebuild_context_menu()

    def start_focus(self, bound_task_id=None, title=""):
        """开始一次专注（公开入口：球菜单 / 任务页右键「专注此任务」）。

        - 功能关闭 → 轻提示后忽略
        - running 中 → 拒绝叠加（不打破当前计时）
        - paused / idle → 从满时长起跑
        """
        if not self._pomodoro_enabled:
            self._show_toast("番茄钟已在设置中关闭")
            return
        if self._pomodoro.state == STATE_RUNNING:
            self._show_toast("已有进行中的计时，请先结束再开始")
            return
        self._pomodoro.bound_task_id = bound_task_id
        self._pomodoro.bound_title = str(title or "")
        self._pomodoro.start(PHASE_FOCUS)

    def pomodoro_state(self) -> dict:
        """公开入口：番茄钟当前状态快照（纯 dict，供插件等外部读取）。

        功能关闭 / 计时器缺失时返回 idle 形态的空值，不抛异常。
        """
        t = getattr(self, "_pomodoro", None)
        enabled = bool(getattr(self, "_pomodoro_enabled", False))
        if not enabled or t is None:
            return {"enabled": False, "state": STATE_IDLE, "phase": PHASE_FOCUS,
                    "remaining_seconds": 0, "progress": 0.0,
                    "focus_minutes": 0, "break_minutes": 0,
                    "auto_break": False, "bound_task_id": None,
                    "bound_title": ""}
        return {
            "enabled": True,
            "state": t.state,                       # property
            "phase": t.phase,                       # property
            "remaining_seconds": int(t.remaining_seconds()),   # 方法
            "progress": float(t.progress()),                   # 方法
            "focus_minutes": int(t.focus_minutes),  # property
            "break_minutes": int(t.break_minutes),  # property
            "auto_break": bool(t.auto_break),       # property
            "bound_task_id": t.bound_task_id,
            "bound_title": str(t.bound_title or ""),
        }

    def pomodoro_busy(self) -> bool:
        """是否有番茄钟计时会话在身（running/paused 均算）。

        期间悬浮球不参与全屏自动让位（用户约定 2026-09-27：
        计时时环在显示倒计时进度，藏起来就看不到了）。
        """
        t = getattr(self, "_pomodoro", None)
        return (bool(getattr(self, "_pomodoro_enabled", False))
                and t is not None
                and t.state in (STATE_RUNNING, STATE_PAUSED))

    def _pomodoro_toggle(self):
        """菜单「开始/暂停/继续」三态入口。"""
        if self._pomodoro.state == STATE_IDLE:
            self.start_focus()
        else:
            self._pomodoro.toggle()

    def _pomodoro_stop(self):
        """菜单「结束计时」：回到 idle（不计数）。"""
        self._pomodoro.stop()
        self._pomodoro.bound_task_id = None
        self._pomodoro.bound_title = ""

    def _pomodoro_menu_entries(self):
        """按当前状态产出右键菜单项 [(text, callback), ...]；关闭时返回空。"""
        if not getattr(self, "_pomodoro_enabled", False):
            return []
        timer = self._pomodoro
        state = timer.state
        is_break = timer.phase == PHASE_BREAK
        if state == STATE_IDLE:
            return [("🍅 开始专注", self.start_focus)]
        if state == STATE_RUNNING:
            return [
                ("⏸ 暂停休息" if is_break else "⏸ 暂停专注", self._pomodoro_toggle),
                ("⏹ 结束计时", self._pomodoro_stop),
            ]
        # paused
        return [
            ("▶ 继续休息" if is_break else "▶ 继续专注", self._pomodoro_toggle),
            ("⏹ 结束计时", self._pomodoro_stop),
        ]

    def _format_mmss(self, seconds: int) -> str:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _pomodoro_tooltip_text(self):
        timer = self._pomodoro
        state = timer.state
        if state == STATE_IDLE or not self._pomodoro_enabled:
            return ""
        remaining = self._format_mmss(timer.remaining_seconds())
        if state == STATE_PAUSED:
            return f"⏸ 已暂停 · 剩余 {remaining}"
        if timer.phase == PHASE_BREAK:
            return f"☕ 休息中 · 剩余 {remaining}"
        return f"🍅 专注中 · 剩余 {remaining}"

    def _update_pomodoro_visuals(self):
        """环 + tooltip 一次性同步（ticked/状态/相位变化共用）。"""
        timer = self._pomodoro
        active = (self._pomodoro_enabled
                  and timer.state in (STATE_RUNNING, STATE_PAUSED))
        self._surface.set_ring_progress(timer.progress(), active)
        self.setToolTip(self._pomodoro_tooltip_text())

    def _on_pomodoro_ticked(self, _remaining: int):
        self._update_pomodoro_visuals()

    def _on_pomodoro_phase_changed(self, phase: str):
        # 相位切换 → 换环色（休息绿）并同步 tooltip
        colors = getattr(self, "_ring_track", None)
        if colors is not None:
            fill = (self._ring_break_fill if phase == PHASE_BREAK
                    else QColor(str(get_colors(self._theme).get(
                        "primary_deep", "#3D9E9C"))))
            self._surface.set_ring_colors(colors, fill)
        self._update_pomodoro_visuals()

    def _on_pomodoro_state_changed(self, _state: str):
        # 状态变化 → 菜单项组随显隐重建（菜单通常处于关闭态，开销可忽略）
        self._update_pomodoro_visuals()
        self._rebuild_context_menu()
        # 番茄钟计时期间暂停闲置自动隐藏；结束/停止后恢复原节律。
        # 若开始计时那一刻球正半隐藏在屏幕边缘，滑回屏内把环亮出来
        if self.pomodoro_busy():
            t = getattr(self, "_idle_hide_timer", None)
            if t is not None:
                t.stop()
            if getattr(self, "_hidden_to_edge", False):
                self._slide_out_from_edge()
        else:
            self._start_idle_hide_timer()

    def _on_pomodoro_finished(self, phase: str):
        """相位计满：脉冲反馈 + 轻提示 + 任务番茄计数 + 托盘气泡（经信号）。"""
        self.pulse()
        if phase == PHASE_FOCUS:
            title = self._pomodoro.bound_title
            tid = self._pomodoro.bound_task_id
            counted = None
            if tid is not None and self._task_manager is not None:
                counted = self._task_manager.add_focus_session(tid)
            if counted:
                self._show_toast(f"🍅 专注完成「{title}」（累计 {counted} 个番茄）")
            else:
                self._show_toast("🍅 专注完成，休息一下！")
            # 绑定只服务一次专注，完成后清掉
            self._pomodoro.bound_task_id = None
            self._pomodoro.bound_title = ""
            self.pomodoro_phase_finished.emit(PHASE_FOCUS, title)
        else:
            self._show_toast("☕ 休息结束，开始新的专注吧")
            self.pomodoro_phase_finished.emit(PHASE_BREAK, "")
        # auto_break 时 start(BREAK) 已把状态推回 running；
        # 否则 _tick 已落回 idle。菜单/环/tooltip 在 state_changed 里同步。

    def _request_quit(self):
        """请求退出程序"""
        self.request_quit.emit()

    # ---------------- 主题切换 ----------------
    def apply_theme(self, theme_name: str):
        """外部切换主题时调用"""
        if theme_name not in ("light", "dark"):
            return
        self._theme = theme_name
        # 球体渐变配色（浅色/深色主色不同）
        if self._surface is not None:
            self._surface.set_theme(theme_name)
        # 番茄钟进度环配色随主题（环在球外圈，颜色取自主题字典）
        if getattr(self, "_pomodoro", None) is not None:
            self._apply_ring_colors()
        # 右键菜单 QSS
        self._menu.setStyleSheet(get_menu_qss(theme_name))
        # 小卡片主题
        if hasattr(self, '_card_window'):
            self._card_window.apply_theme(theme_name)
        # 触发重绘
        self.update()

    # ---------------- 文件拖拽拾取 ----------------
    # 浏览器拖拽图片时 MIME 格式映射：format → 扩展名
    _IMAGE_MIME_MAP = {
        "image/png":  ".png",
        "image/jpeg": ".jpg",
        "image/jpg":  ".jpg",
        "image/gif":  ".gif",
        "image/bmp":  ".bmp",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/tiff": ".tiff",
        "image/x-icon": ".ico",
    }

    def _get_temp_assets_dir(self) -> str:
        """获取临时素材目录（与 TempAssetManager 使用同一根目录，两版共用）"""
        if self._temp_asset_manager is not None:
            return self._temp_asset_manager.get_assets_dir()
        # 回退：统一走 float_data/temp_assets（打包运行时为 exe 目录下）
        from src.app_paths import get_temp_assets_dir
        return get_temp_assets_dir()

    def dragEnterEvent(self, event):
        """
        接受拖拽：
          - 本地文件 URL（资源管理器拖文件）
          - 浏览器图片原始数据（image/png, image/jpeg 等）
          - 浏览器拖图片时也可能带 HTTP URL
        """
        md = event.mimeData()
        if md.hasUrls():
            event.acceptProposedAction()
        elif md.hasImage():
            # 浏览器拖图片：MIME 里直接带图片原始数据
            event.acceptProposedAction()
        else:
            # 检查是否包含浏览器图片 MIME 格式
            for fmt in self._IMAGE_MIME_MAP:
                if md.hasFormat(fmt):
                    event.acceptProposedAction()
                    return
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        """
        拖拽释放（按优先级处理）：
          1. 本地文件路径（资源管理器拖文件）→ 直接复制
          2. FileContents（浏览器拖图片/文件，OLE 格式）→ 直接提取二进制
          3. 图片原始数据（image/png 等 MIME）→ 保存为文件
          4. HTTP URL（最后手段）→ 尝试下载
        """
        md = event.mimeData()

        added_assets = 0
        added_frags = 0
        rejected_large = 0          # 因超过单文件体积上限被拒的素材数

        # ---- 1. 本地文件 URL（资源管理器拖文件）----
        local_files = []
        http_urls = []
        if md.hasUrls():
            for url in md.urls():
                local_path = url.toLocalFile()
                if local_path:
                    local_files.append(local_path)
                else:
                    url_str = url.toString()
                    if url_str and (url_str.startswith("http://")
                                    or url_str.startswith("https://")):
                        http_urls.append(url_str)

        # ---- 1a. 应用类文件（exe/lnk）→ 应用启动器 ----
        # 与 LaunchDeck 同款交互：拖入即收藏；不进素材池/碎片
        added_apps = 0
        dup_apps = 0
        app_paths = [p for p in local_files
                     if p.lower().endswith((".exe", ".lnk"))]
        normal_files = [p for p in local_files
                        if not p.lower().endswith((".exe", ".lnk"))]
        if app_paths and self._config is not None:
            from src.widget_app_launcher import make_app_from_path
            apps = self._config.get("apps", [])
            if not isinstance(apps, list):
                apps = []
            existed = {a.get("exe_path", "") for a in apps
                       if isinstance(a, dict)}
            for path in app_paths:
                app_item = make_app_from_path(path)
                if app_item is None:
                    continue
                if app_item["exe_path"] in existed:
                    dup_apps += 1
                    continue
                apps.append(app_item)
                existed.add(app_item["exe_path"])
                added_apps += 1
            if added_apps > 0:
                self._config.set("apps", apps)
                self._config.save()

        # ---- 1b. 其他本地文件 → 临时素材 + 碎片拾取（原逻辑不变）----
        for path in normal_files:
            if self._temp_asset_manager is not None:
                res = self._temp_asset_manager.add_asset(path)
                if res > 0:
                    added_assets += 1
                elif res == REJECT_TOO_LARGE:
                    rejected_large += 1
            if self._fragment_manager is not None:
                self._fragment_manager.add_file_pickup(path)
                added_frags += 1

        # ---- 2. FileContents（浏览器拖图片/文件，OLE 格式）----
        # 这是浏览器缓存里的原始文件二进制数据，不需要网络下载
        if not local_files and md.hasFormat(
                "application/x-qt-windows-mime;value=\"FileContents\""):
            file_data = md.data(
                "application/x-qt-windows-mime;value=\"FileContents\"")
            file_name = self._parse_file_group_descriptor(md)
            saved_path = self._save_file_contents(file_data, file_name)
            if saved_path:
                if self._temp_asset_manager is not None:
                    res = self._temp_asset_manager.add_asset(saved_path)
                    if res > 0:
                        added_assets += 1
                    elif res == REJECT_TOO_LARGE:
                        rejected_large += 1
                if self._fragment_manager is not None:
                    self._fragment_manager.add_file_pickup(saved_path)
                    added_frags += 1
                try:
                    os.remove(saved_path)
                except OSError:
                    pass

        # ---- 3. 图片原始数据（image/png 等 MIME）----
        if not local_files and added_assets == 0:
            saved_path = ""
            if md.hasImage():
                saved_path = self._save_mime_image(md)
            if not saved_path:
                for fmt, ext in self._IMAGE_MIME_MAP.items():
                    if md.hasFormat(fmt):
                        saved_path = self._save_mime_image_by_format(
                            md, fmt, ext)
                        if saved_path:
                            break

            if saved_path:
                if self._temp_asset_manager is not None:
                    res = self._temp_asset_manager.add_asset(saved_path)
                    if res > 0:
                        added_assets += 1
                    elif res == REJECT_TOO_LARGE:
                        rejected_large += 1
                if self._fragment_manager is not None:
                    self._fragment_manager.add_file_pickup(saved_path)
                    added_frags += 1
                try:
                    os.remove(saved_path)
                except OSError:
                    pass

        # ---- 4. HTTP URL（最后手段）----
        if not local_files and added_assets == 0 and http_urls:
            for url_str in http_urls:
                saved_path = self._download_url(url_str)
                if saved_path:
                    if self._temp_asset_manager is not None:
                        res = self._temp_asset_manager.add_asset(saved_path)
                        if res > 0:
                            added_assets += 1
                        elif res == REJECT_TOO_LARGE:
                            rejected_large += 1
                    if self._fragment_manager is not None:
                        self._fragment_manager.add_file_pickup(saved_path)
                        added_frags += 1
                    try:
                        os.remove(saved_path)
                    except OSError:
                        pass

        # 通知主窗口刷新对应面板
        if self._main_window is not None:
            if added_assets > 0:
                self._main_window.refresh_temp_assets()
                # 同步刷新小卡片素材页（可见立即重建，隐藏则置脏待下次进入）
                self._card_window.notify_assets_changed()
            if added_frags > 0:
                self._main_window.refresh_fragments()
            if added_apps > 0:
                # 软件导航页（索引 7）：内部做 load_apps_from_config + reload_settings
                self._main_window.refresh_apps_page()
        # 同步刷新小卡片软件页（仅可见且停留在软件页时重建；
        # 隐藏时无需处理——每次切入 app 页都会重读 config）
        if (added_apps > 0 and self._card_window is not None
                and self._card_window.isVisible()
                and self._card_window.current_mode == "app"):
            self._card_window.refresh_page("app")

        # Toast 提示（重复拖入 added_apps=0 但 dup_apps>0 时也要有反馈）
        if added_apps > 0 or dup_apps > 0:
            if added_apps > 0:
                tip = f"已添加 {added_apps} 个应用到启动器"
                if dup_apps > 0:
                    tip += f"（{dup_apps} 个已存在，跳过）"
            else:
                tip = f"{dup_apps} 个应用已在启动器中，跳过"
            self._show_toast(tip)
        elif added_assets > 0:
            tip = f"已收录 {added_assets} 个素材"
            if rejected_large > 0:
                tip += f"（{rejected_large} 个超过体积上限，已跳过）"
            self._show_toast(tip)
        elif rejected_large > 0:
            self._show_toast(
                f"⚠️ {rejected_large} 个文件超过素材体积上限，未收进素材池")

        # 成功反馈（A4）：球体光晕脉冲一次，不用读 Toast 也知道"接住了"
        if (added_assets > 0 or added_frags > 0
                or added_apps > 0 or dup_apps > 0):
            self.pulse()

        event.acceptProposedAction()

    def _parse_file_group_descriptor(self, mime_data) -> str:
        """
        从 FileGroupDescriptorW 解析文件名。
        返回第一个文件的文件名，失败返回空字符串。
        """
        fmt = "application/x-qt-windows-mime;value=\"FileGroupDescriptorW\""
        if not mime_data.hasFormat(fmt):
            return ""
        try:
            raw = bytes(mime_data.data(fmt))
            if len(raw) < 4:
                return ""
            # DWORD cItems（文件数量）
            c_items = struct.unpack_from("<I", raw, 0)[0]
            if c_items < 1:
                return ""
            # 每个 FILEDESCRIPTORW 结构从偏移 4 开始
            # cFileName 在结构内偏移 112 处，长度 520 字节（260 WCHAR）
            offset = 4 + 112
            name_bytes = raw[offset:offset + 520]
            # 找第一个 null 终止符
            end = name_bytes.find(b"\x00\x00")
            if end > 0:
                name_bytes = name_bytes[:end]
            return name_bytes.decode("utf-16-le", errors="replace").strip()
        except Exception:
            return ""

    def _save_file_contents(self, file_data, file_name: str) -> str:
        """
        将 FileContents 的二进制数据保存到 temp_assets/。
        file_name 来自 FileGroupDescriptorW，可能为空。
        """
        if file_data.isEmpty():
            return ""
        try:
            tmp_dir = self._get_temp_assets_dir()
            os.makedirs(tmp_dir, exist_ok=True)
            # 确定文件名和扩展名
            if file_name:
                # 清理文件名中的非法字符（集中规则，见 constants.sanitize_filename）
                file_name = sanitize_filename(file_name)
                ext = os.path.splitext(file_name)[1].lower()
            else:
                ext = ".bin"
                file_name = "browser_file"

            # 如果扩展名不在已知图片格式中，尝试从数据头判断
            if ext not in [".png", ".jpg", ".jpeg", ".gif", ".bmp",
                           ".webp", ".svg", ".tiff", ".ico", ".pdf",
                           ".doc", ".docx", ".txt", ".bin"]:
                ext = ".bin"

            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(tmp_dir, f"{file_name}_{ts}{ext}")

            with open(path, "wb") as f:
                f.write(file_data.data())
            print(f"[DEBUG] FileContents saved: {len(file_data)} bytes -> {path}")
            return path
        except Exception as e:
            print(f"[DEBUG] FileContents save failed: {e}")
            return ""

    def _save_mime_image(self, mime_data) -> str:
        """从 MIME 图片数据保存为临时文件，返回路径"""
        from PyQt6.QtGui import QImage
        from datetime import datetime
        img = QImage(mime_data.imageData())
        if img.isNull():
            return ""
        tmp_dir = self._get_temp_assets_dir()
        os.makedirs(tmp_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tmp_dir, f"browser_img_{ts}.png")
        img.save(path, "PNG")
        return path

    def _save_mime_image_by_format(self, mime_data, fmt: str, ext: str) -> str:
        """从指定 MIME 格式保存为临时文件，返回路径"""
        from datetime import datetime
        data = mime_data.data(fmt)
        if data.isEmpty():
            return ""
        tmp_dir = self._get_temp_assets_dir()
        os.makedirs(tmp_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tmp_dir, f"browser_img_{ts}{ext}")
        with open(path, "wb") as f:
            f.write(data.data())
        return path

    # ---------------- URL 下载（安全加固版） ----------------
    DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024   # 单次下载上限 20MB

    @staticmethod
    def _is_private_url_host(host: str) -> bool:
        """拦截本地/内网地址（SSRF 防护）：解析失败一律拒绝"""
        import ipaddress
        import socket
        if not host:
            return True
        h = host.strip("[]").lower()
        if h == "localhost" or h.endswith(".local") or h.endswith(".internal"):
            return True
        try:
            infos = socket.getaddrinfo(h, None)
        except (socket.gaierror, OSError):
            return True
        for info in infos:
            ip = info[4][0]
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                return True
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast):
                return True
        return False

    @staticmethod
    def _detect_image_ext(raw: bytes) -> str:
        """按文件头（magic bytes）识别真实图片类型；非图片返回空串"""
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if raw.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if raw.startswith(b"BM"):
            return ".bmp"
        if raw.startswith(b"\x00\x00\x01\x00"):
            return ".ico"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return ".webp"
        return ""

    def _download_url(self, url: str) -> str:
        """下载 HTTP(S) 图片 URL 到临时文件，返回路径；被拦截或失败返回空串

        安全闸门：① 仅 http/https ② 拒绝本地/内网地址 ③ 20MB 上限 ④ magic bytes 校验
        """
        from datetime import datetime
        from urllib.parse import urlparse
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
        from PyQt6.QtCore import QEventLoop, QTimer, QUrl

        try:
            # 闸门 1：协议白名单
            u = urlparse(url)
            if u.scheme.lower() not in ("http", "https"):
                self._show_toast("⚠️ 仅支持 http/https 图片链接")
                return ""
            # 闸门 2：拒绝本地/内网地址
            if self._is_private_url_host(u.hostname or ""):
                self._show_toast("⚠️ 已拦截本地/内网地址")
                return ""

            tmp_dir = self._get_temp_assets_dir()
            os.makedirs(tmp_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            manager = QNetworkAccessManager()
            request = QNetworkRequest()
            request.setUrl(QUrl(url))
            request.setRawHeader(b"User-Agent",
                b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            request.setRawHeader(b"Accept",
                b"image/webp,image/apng,image/*,*/*;q=0.8")
            request.setRawHeader(b"Referer", url.encode())

            reply = manager.get(request)
            loop = QEventLoop()
            reply.finished.connect(loop.quit)

            # 闸门 3：下载进度超限即中止
            def _on_progress(received, total):
                if (received > self.DOWNLOAD_MAX_BYTES
                        or (total > 0 and total > self.DOWNLOAD_MAX_BYTES)):
                    reply.abort()
            reply.downloadProgress.connect(_on_progress)

            # 15 秒超时（超时中止 → finished 触发 → loop 退出）
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(reply.abort)
            timer.start(15000)

            loop.exec()
            timer.stop()

            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    print(f"[DEBUG] Download failed/aborted: {reply.errorString()}")
                    return ""
                raw = bytes(reply.readAll())
            finally:
                reply.deleteLater()

            # 闸门 3 复核：落盘前再查一次总大小
            if len(raw) > self.DOWNLOAD_MAX_BYTES:
                print("[DEBUG] Download exceeded size limit")
                self._show_toast("⚠️ 图片超过 20MB，已取消")
                return ""

            # 闸门 4：按真实文件头识别类型；非图片一律拒绝（并按真实类型定扩展名）
            ext = self._detect_image_ext(raw)
            if not ext:
                print("[DEBUG] Downloaded content is not an image")
                self._show_toast("⚠️ 下载内容不是图片，已取消")
                return ""

            path = os.path.join(tmp_dir, f"url_img_{ts}{ext}")
            with open(path, "wb") as f:
                f.write(raw)
            print(f"[DEBUG] Downloaded: {len(raw)} bytes -> {path}")
            return path
        except Exception as e:
            print(f"[DEBUG] Download exception: {e}")
            return ""

    # ---------------- 缩放 / 投影动画 ----------------
    def _dur(self, ms: int) -> int:
        """按动画速度档位缩放时长（档位越大越快，时长越短）"""
        return max(1, int(ms / self._anim_speed))

    def set_anim_speed(self, speed: float):
        """外部（设置页步进器）实时更新动画速度档位"""
        try:
            self._anim_speed = max(0.5, min(2.0, float(speed)))
        except (TypeError, ValueError):
            self._anim_speed = 1.0

    def _base_scale(self) -> float:
        """由当前悬停/按下/拖拽状态叠加计算的目标缩放（共享同一公式）"""
        delta = 0.0
        if self._hovered:
            delta += self.HOVER_GROW          # 悬停 +4%
        if self._pressed and not self._dragging:
            delta -= self.PRESS_SHRINK        # 按下 -8%
        if self._dragging:
            delta += self.DRAG_ENLARGE        # 拖拽 +8%
        return 1.0 * (1.0 + delta)

    def _animate_scale(self, target, dur_ms, curve, on_finished=None):
        self._surface.animate_scale(target, self._dur(int(dur_ms)), curve, on_finished)

    def _animate_glow(self, target, dur_ms, curve):
        self._surface.animate_glow(target, self._dur(int(dur_ms)), curve)

    # ---------------- 几何辅助 ----------------
    def _ball_center(self) -> QPointF:
        """球体中心（宿主中心）的屏幕坐标，浮点"""
        return QPointF(self.pos().x() + self._host_size / 2.0,
                       self.pos().y() + self._host_size / 2.0)

    def _ball_visual_rect(self) -> QRectF:
        """球体可见圆的外接矩形（屏幕坐标），用于卡片定位与悬停检测"""
        c = self._ball_center()
        r = self._ball_size / 2.0
        return QRectF(c.x() - r, c.y() - r, r * 2.0, r * 2.0)

    def _ball_hit(self, global_pos) -> bool:
        """判断全局坐标点是否落在球体（含轻微手感余量）内"""
        lx = global_pos.x() - self.frameGeometry().x()
        ly = global_pos.y() - self.frameGeometry().y()
        dx = lx - self._host_size / 2.0
        dy = ly - self._host_size / 2.0
        r = self._ball_size / 2.0 + 6.0
        return dx * dx + dy * dy <= r * r

    # ---------------- 位置恢复 ----------------
    def _resolve_initial_position(self) -> QPoint:
        """启动位置：读取上次保存位置；无记录则放主屏左缘垂直居中；越界则夹回屏内"""
        screen = get_screen_geometry()
        S = self._host_size
        default = QPoint(screen.left(), screen.top() + (screen.height() - S) // 2)
        saved = self._config.get("ball_position", None) if self._config else None
        if isinstance(saved, (list, tuple)) and len(saved) >= 2:
            x = max(screen.left(), min(int(saved[0]), screen.right() - S))
            y = max(screen.top(), min(int(saved[1]), screen.bottom() - S))
            return QPoint(x, y)
        return default

    def _save_position(self):
        """把最终位置写入配置，供下次启动恢复。

        用 _normal_pos（吸边隐藏之前的正常位置）而非当前坐标——半隐藏时
        窗口有一半在屏幕外，直接存当前坐标会让下次启动的球"贴着边"。
        """
        if not self._config:
            return
        p = self._normal_pos if self._normal_pos is not None else self.pos()
        self._config.set("ball_position", [int(p.x()), int(p.y())])
        self._config.save()

    def _schedule_position_save(self):
        """拖拽结束后延迟落盘：连续拖动只写一次，避免频繁写盘"""
        if hasattr(self, '_pos_save_timer') and self._pos_save_timer is not None:
            self._pos_save_timer.start()

    def save_position_now(self):
        """立即落盘（退出前由主程序调用，兜底防抖窗口内未写的位置）"""
        if hasattr(self, '_pos_save_timer') and self._pos_save_timer is not None:
            self._pos_save_timer.stop()
        self._save_position()

    # ---------------- Toast 提示 ----------------
    def _show_toast(self, text: str, duration_ms: int = 1500):
        """操作反馈提示：屏幕顶部居中的顶层浮窗（2026-09-24 改造）。

        此前是球上方的 _ToastLabel 气泡 —— 球一隐藏（吸边/被设置关掉）提示
        就跟着没了，且球贴近屏幕顶部时气泡还会出屏。统一改为 ScreenToast：
        独立顶层窗口，固定屏幕顶部居中，与球的状态无关。
        """
        ScreenToast.show_msg(text, self._theme, max(1500, duration_ms))

    # ---------------- 鼠标事件 ----------------
    def showEvent(self, event):
        super().showEvent(event)
        # 位置容错：吸边隐藏状态下不重置，否则位置完全跑出屏幕时拉回默认位置
        self._ensure_ball_on_screen()
        self._start_idle_hide_timer()

    def _ensure_ball_on_screen(self):
        """
        窗口层级容错：屏幕分辨率变化 / 多屏坐标漂移导致球体完全跑出屏幕时，
        拉回到启动默认位置。吸边隐藏状态下不触发。
        """
        if self._hidden_to_edge:
            return
        try:
            screen = get_screen_geometry()
            c = self._ball_center()
            if (c.x() < screen.left() or c.x() > screen.right()
                    or c.y() < screen.top() or c.y() > screen.bottom()):
                self.move(self._resolve_initial_position())
        except Exception:
            # 容错本身不能再引起新崩溃
            pass

    def enterEvent(self, event):
        self._update_hover(QCursor.pos())

    def leaveEvent(self, event):
        # 鼠标真正离开窗口：必然离开球体
        self._update_hover_maybe(False, QCursor.pos())

    def _update_hover(self, gpos):
        self._update_hover_maybe(self._ball_hit(gpos), gpos)

    def _update_hover_maybe(self, in_ball, gpos):
        # 进入/离开/球上移动都算「活动」：静止计时清零，已降频则立即回 50ms。
        # 注意要放在 in_ball == _hovered 早退之前——鼠标在球上持续移动时
        # 悬停态不变，但同样是需要全速轮询的活动。
        self._note_poll_activity()
        if in_ball == self._hovered:
            return
        self._hovered = in_ball
        if in_ball:
            self._surface.set_appearance(hovered=True)
            self._idle_hide_timer.stop()
            if not (self._pressed or self._dragging):
                self._animate_scale(self._base_scale(), 160,
                                    QEasingCurve.Type.OutQuad)
            if self._hidden_to_edge:
                self._slide_out_from_edge()
            elif not (self._pressed or self._dragging):
                self._show_card_on_hover()
        else:
            self._surface.set_appearance(hovered=False)
            if not (self._pressed or self._dragging):
                self._animate_scale(self._base_scale(), 200,
                                    QEasingCurve.Type.OutBack)
            self._start_idle_hide_timer()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if not self._ball_hit(event.globalPosition().toPoint()):
            # 落在宿主透明边距上：不当作点选/拖拽
            event.ignore()
            return
        # 记录窗口当前位置与鼠标全局位置，进入「按下态」
        self._press_pos = event.globalPosition().toPoint()
        self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
        self._pressed = True
        self._dragging = False
        self._moved = False
        if self._card_window.isVisible():
            self._card_offset = self._card_window.pos() - self.pos()
        # 按下态：快速缩小约 8%（OutCubic，~100ms）
        self._surface.set_appearance(dragging=False)
        self._animate_scale(self._base_scale(), 100, QEasingCurve.Type.OutCubic)
        event.accept()   # 接受鼠标事件以获得隐式抓取，拖出窗口不断流

    def mouseMoveEvent(self, event):
        gpos = event.globalPosition().toPoint()
        # 按钮按住时的拖拽逻辑
        if (self._pressed or self._dragging) and (event.buttons() & Qt.MouseButton.LeftButton):
            if not self._dragging and (gpos - self._press_pos).manhattanLength() > self.DRAG_THRESHOLD:
                self._start_drag()
            if self._dragging:
                self._move_drag_follow(gpos)
            event.accept()
            return
        # 无按钮 → 悬停状态更新
        self._update_hover(gpos)
        event.accept()

    def _start_drag(self):
        """判定拖拽开始：取消按下态，进入拖拽态并立即收回呼出面板"""
        self._dragging = True
        self._pressed = False
        self._moved = True
        self._surface.set_appearance(dragging=True)
        # 拖拽态：额外放大约 8% 并增强投影（OutCubic）
        self._animate_scale(self._base_scale(), 140, QEasingCurve.Type.OutCubic)
        self._animate_glow(1.0, 150, QEasingCurve.Type.OutQuad)
        self._hover_check_timer.stop()
        if self._card_window.isVisible():
            self._card_window.hide()
        self._out_count = 0
        # 从吸边半隐藏态被拖出时先复位边缘状态：否则 _hidden_to_edge 残留会让
        # 近边判定与出屏容错被跳过（C4 位置逻辑的配套修复）
        self._hidden_to_edge = False
        self._edge_side = None
        self._idle_hide_timer.stop()

    def _move_drag_follow(self, gpos):
        """拖拽中窗口跟随鼠标（带橡皮筋越界衰减）"""
        raw = gpos - self._drag_offset
        screen = get_screen_geometry()
        S = self._host_size
        x = self._rubber_axis(raw.x(), screen.left(), screen.right() - S)
        y = self._rubber_axis(raw.y(), screen.top(), screen.bottom() - S)
        self.move(QPoint(int(x), int(y)))

    def _rubber_axis(self, v, lo, hi):
        """界内原值、越界衰减 1/4，并允许少量越界（防止彻底被拉出屏幕）"""
        if v < lo:
            return max(lo - (lo - v) * self.RUBBER_DAMP,
                       lo - self.RUBBER_OVERRUN * self._host_size)
        if v > hi:
            return min(hi + (v - hi) * self.RUBBER_DAMP,
                       hi + self.RUBBER_OVERRUN * self._host_size)
        return v

    def _on_card_drag_started(self):
        """在卡片上按下左键：校准球的相对偏移，并暂停空闲吸边隐藏。

        `_card_offset` 原先只在**按球**时记录，直接拖卡片时它还是初始
        QPoint(0,0)，于是 `_on_card_moved` 会把球 move 到卡片左上角、
        被卡片盖住 —— 表现为"拖卡片时球消失，关掉卡片球又从卡片左上角冒出来"。
        改为拖动起点实时校准，两条路径（先按球 / 直接拖卡片）都能拿到真值。
        """
        if not self._card_window.isVisible():
            return
        self._card_offset = self._card_window.pos() - self.pos()
        self._idle_hide_timer.stop()

    def _on_card_moved(self):
        """卡片拖动时悬浮球同步跟随，保持二者相对位置"""
        if not self._card_window.isVisible():
            return
        self.move(self._card_window.pos() - self._card_offset)
        # 球被卡片"带着走"也是一次显式的位置变更：若此前处于吸边半隐藏态，
        # 必须一并复位边缘状态 —— 否则 _hidden_to_edge 残留会让近边判定
        # （_is_near_edge / _on_idle_hide_timeout）永久失效，球再也吸不了边，
        # 且之后鼠标靠近会误触发一次"从边缘滑出"动画（与拖球同源的问题）
        if self._hidden_to_edge:
            self._hidden_to_edge = False
            self._edge_side = None
        self._normal_pos = self.pos()
        self._schedule_position_save()

    def _on_card_drag_finished(self):
        """卡片拖动结束：按需恢复球的空闲吸边隐藏（近边才启动计时）"""
        self._start_idle_hide_timer()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_moved = self._moved
        self._pressed = False
        self._dragging = False
        self._moved = False
        self._card_offset = QPoint()
        # 释放：先取消按下态；若是拖拽则结束拖拽态并把阴影恢复原样（OutBack 回弹）
        self._surface.set_appearance(dragging=False)
        self._animate_scale(self._base_scale(), 200, QEasingCurve.Type.OutBack)
        self._animate_glow(0.0, 180, QEasingCurve.Type.OutQuad)
        if was_moved:
            # 拖拽结束 → 进入边缘吸附
            self._snap_to_edge()
            # 兜底落盘（C4）：即使吸附动画被后续操作打断，位置也已记住
            self._schedule_position_save()
            # 松手后若贴边，启动空闲隐藏计时（到点后半隐藏进入边缘）
            self._start_idle_hide_timer()
            if self._card_window.isVisible():
                self._out_count = 0
                self._hover_check_timer.start(self.HOVER_CHECK_INTERVAL)
            else:
                self._hover_check_timer.stop()
        elif not self._card_window.is_locked():
            if self._card_window.isVisible():
                # 卡片已弹出但停在别的模式（任务/碎片页）时先切回卡片模式，
                # 否则 next_card() 只改了内容，界面停在原页 → 点击看似无反馈（B9）
                if self._card_window.current_mode != "card":
                    self._card_window.switch_mode("card")
                self._card_window.next_card()
            else:
                self._card_window.show_next_random()
                self._card_window.popup_near(self._ball_visual_rect())
            self._out_count = 0
            self._hover_check_timer.start(self.HOVER_CHECK_INTERVAL)
        event.accept()

    def wheelEvent(self, event):
        """球体滚轮：上滚上一张 / 下滚下一张知识卡（B3）

        成熟悬浮球的标配手势——手不用离开球就能翻卡。
        卡片未弹出时先弹出；已弹出但停在别的模式时先切回卡片模式。
        """
        if self._dragging or self._pressed:
            event.ignore()
            return
        if not self._ball_hit(event.globalPosition().toPoint()):
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        self._step_card(-1 if delta > 0 else 1)
        event.accept()

    def _step_card(self, direction: int):
        """按方向翻知识卡（direction = -1 上一张 / +1 下一张）

        首次（卡片未弹出）用滚轮唤出时落到顺序首张并直接停在卡片页，
        保证"滚轮 = 顺序翻卡"这条语义始终成立；点击球的随机换卡不受影响。
        """
        w = self._card_window
        if not w.isVisible():
            w.step_card(0)
            w.current_mode = "card"   # 让 popup_near 直接落在卡片页
            w.popup_near(self._ball_visual_rect())
        else:
            if w.current_mode != "card":
                w.switch_mode("card")
            w.step_card(direction)
        self._out_count = 0
        self._hover_check_timer.start(self.HOVER_CHECK_INTERVAL)

    def mouseDoubleClickEvent(self, event):
        """双击球体 → 打开主窗口（B3）"""
        if (event.button() == Qt.MouseButton.LeftButton
                and self._ball_hit(event.globalPosition().toPoint())):
            self._open_main_window()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _snap_to_edge(self):
        """
        松手后立即吸附到最近的屏幕左/右边缘（以球心所在水平半区判断）。
        垂直位置保持不变并夹回可用区域。用 OutBack 产生先过冲再回落的弹性归位。
        """
        screen = get_screen_geometry()
        S = self._host_size
        mid = (screen.left() + screen.right()) / 2.0
        x = screen.left() if self._ball_center().x() <= mid else screen.right() - S
        y = self.y()
        if y < screen.top():
            y = int(screen.top())
        elif y > screen.bottom() - S:
            y = int(screen.bottom() - S)
        else:
            y = int(y)
        def _on_snap_finished():
            # 吸附到位的位置即"正常位置"（C4），随后落盘；并启动空闲隐藏计时——
            # 松手后球仍在屏幕中央、计时未启动时在此重启，实现无需再点鼠标即自动半隐藏
            self._normal_pos = self.pos()
            self._save_position()
            self._start_idle_hide_timer()
        self._animate_pos(QPoint(int(x), y), 220, QEasingCurve.Type.OutBack,
                          on_finished=_on_snap_finished)

    def _animate_pos(self, target, dur_ms, curve, on_finished=None):
        """宿主位移动画（吸边/滑出/吸附共用），自动托管唯一 _anim"""
        if self._anim is not None:
            try:
                self._anim.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(self._dur(int(dur_ms)))
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(curve)
        if on_finished is not None:
            self._anim.finished.connect(on_finished)
        self._anim.start()

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    # ---------------- 悬停卡片显示 / 关闭 ----------------
    def _note_poll_activity(self):
        """轮询状态机打点：出现活动（进入/离开/移动/弹卡）即恢复全速轮询。

        仅在策略处于降频态时返回间隔，避免每次鼠标事件都无谓触碰定时器。
        """
        policy = getattr(self, '_poll_policy', None)
        if policy is None:
            return
        interval = policy.note_activity()
        if interval is not None:
            self._apply_poll_interval(interval)

    def _apply_poll_interval(self, interval: int):
        """把轮询间隔落到悬停检测定时器（运行中的定时器用 start() 重启生效）"""
        timer = self._hover_check_timer
        if timer is None or timer.interval() == interval:
            return
        if timer.isActive():
            timer.start(interval)
        else:
            timer.setInterval(interval)

    def _show_card_on_hover(self):
        if not self._card_window.isVisible():
            if not self._card_window.has_shown_content():
                self._card_window.show_next_random()
            self._card_window.popup_near(self._ball_visual_rect())
        self._out_count = 0
        self._note_poll_activity()   # 卡片显隐切换 = 活动（滑出唤起路径也经此处）
        self._hover_check_timer.start(self.HOVER_CHECK_INTERVAL)

    def _hide_card_faded(self):
        """卡片收起：140ms 淡出后隐藏（透明度复位，保证下次弹出正常）"""
        w = self._card_window
        if not w.isVisible():
            return
        # 定位线索：真机日志确认收回链路是否真正走到（偶发滞留 bug 排查）
        from src.logger import get_logger
        get_logger().debug("卡片自动收回")
        anim = QPropertyAnimation(w, b"windowOpacity", self)
        anim.setDuration(140)
        anim.setStartValue(w.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self._on_card_fade_done)
        self._card_fade_anim = anim  # 持引用防 GC
        anim.start()

    def _on_card_fade_done(self):
        w = self._card_window
        w.setWindowOpacity(1.0)
        w.hide()

    def _check_hover_state(self):
        try:
            # 空闲降频：按状态机决定本次应使用的间隔（静止超 1s 降为 200ms）
            self._apply_poll_interval(self._poll_policy.on_tick())
            # 悬浮球正在被拖动 → 不检测（拖动期间卡片保持显示）
            if self._dragging:
                return
            # 卡片正在被拖动 → 不检测（CardWindow.is_locked 返回 True）
            if self._card_window.is_locked():
                return
            # 小卡片保持显示模式 → 跳过自动关闭
            if self._config and self._config.get("card_always_show", False):
                return
            pos = QCursor.pos()
            in_ball = self._ball_hit(pos)
            in_card = (self._card_window.geometry().contains(pos)
                       if self._card_window.isVisible() else False)
            if in_ball or in_card:
                self._out_count = 0
            else:
                self._out_count += 1
                if self._out_count >= self.HOVER_LEAVE_COUNT:
                    self._hide_card_faded()
                    self._hover_check_timer.stop()
                    self._out_count = 0
        except Exception:
            # PyQt 槽内异常会被打印吞掉、但本次计数逻辑被中断——真机上表现为
            # 「定时器还在跑、计数却永不达标 → 卡片永久滞留」。这里显式兜底：
            # 记录异常 + 计数照常递增，达到阈值同样走收回 + 停表 + 清零。
            from src.logger import get_logger
            get_logger().error("卡片自动收回检测异常", exc_info=True)
            self._out_count += 1
            if self._out_count >= self.HOVER_LEAVE_COUNT:
                self._hide_card_faded()
                self._hover_check_timer.stop()
                self._out_count = 0

    def _card_watchdog_tick(self):
        """卡片收回看门狗（自愈）：卡片可见但悬停检测定时器已停 → 重新拉起。

        正常路径下「卡片可见」与「hover 检测定时器运行」总是同时成立；
        偶发时序（某条交互路径漏 start / 误 stop）会让二者脱钩、卡片永久
        滞留。看门狗常驻每秒巡检一次，发现脱钩即重启检测定时器——
        无论哪条路径出问题，最多 1 秒自愈，且不改变任何正常交互时序。

        自愈条件（全部满足才拉起）：
        - 卡片可见
        - hover 检测定时器未在运行
        - 悬浮球未在拖动、卡片未在拖动（is_locked）
        - 未开启「小卡片保持显示」（尊重常驻，不强行收回）
        """
        try:
            if not self._card_window.isVisible():
                return
            if self._hover_check_timer.isActive():
                return
            if self._dragging or self._card_window.is_locked():
                return
            if self._config and self._config.get("card_always_show", False):
                return
            self._out_count = 0
            self._hover_check_timer.start(self.HOVER_CHECK_INTERVAL)
            from src.logger import get_logger
            get_logger().debug("看门狗：重新拉起卡片自动收回检测")
        except Exception:
            # 看门狗自身绝不允许抛异常（否则 QTimer 槽中断，自愈失效）
            pass

    # ---------------- 四向吸边隐藏 ----------------
    def _init_hover_timer(self):
        self._hover_check_timer = QTimer(self)
        self._hover_check_timer.setInterval(self.HOVER_CHECK_INTERVAL)
        self._hover_check_timer.timeout.connect(self._check_hover_state)
        # 空闲降频状态机（纯逻辑在 HoverPollPolicy，类里只留接线）：
        # 鼠标静止超 1s → 200ms；任何活动立即回 50ms（见 _note_poll_activity）
        self._poll_policy = HoverPollPolicy()

        # 看门狗自愈：常驻每秒巡检一次，发现「卡片可见但 hover 检测已停」
        # 即重新拉起（见 _card_watchdog_tick），兜住偶发时序导致的卡片滞留
        self._card_watchdog = QTimer(self)
        self._card_watchdog.setInterval(1000)
        self._card_watchdog.timeout.connect(self._card_watchdog_tick)
        self._card_watchdog.start()

        self._idle_hide_timer = QTimer(self)
        self._idle_hide_timer.setSingleShot(True)
        # 自动隐藏秒数与总开关从配置读取
        self._apply_auto_hide_config()
        self._idle_hide_timer.timeout.connect(self._on_idle_hide_timeout)

        # 位置落盘防抖（C4）：拖动结束后延迟写入，拖动过程中不写盘
        self._pos_save_timer = QTimer(self)
        self._pos_save_timer.setSingleShot(True)
        self._pos_save_timer.setInterval(self.POS_SAVE_DELAY)
        self._pos_save_timer.timeout.connect(self._save_position)

    def _apply_auto_hide_config(self):
        """从配置读取空闲吸边自动隐藏的秒数与总开关（启动时调用）。

        开关关闭时只停表、不改位置——启动瞬间球尚未吸边，
        不存在"卡在半隐藏态"的情况（运行中切换走 set_auto_hide_enabled）。
        """
        if self._config:
            seconds = self._config.get("auto_hide_seconds", 3)
            enabled = bool(self._config.get("auto_hide_enabled", True))
        else:
            seconds, enabled = 3, True
        self._auto_hide_enabled = enabled
        if hasattr(self, '_idle_hide_timer') and self._idle_hide_timer is not None:
            self._idle_hide_timer.setInterval(int(seconds) * 1000)

    def set_auto_hide_seconds(self, seconds: int):
        """外部（设置页）实时更新空闲吸边隐藏秒数。若定时器正在计时则用新间隔重启。"""
        if hasattr(self, '_idle_hide_timer') and self._idle_hide_timer is not None:
            self._idle_hide_timer.setInterval(max(1, int(seconds)) * 1000)
            if self._idle_hide_timer.isActive():
                self._idle_hide_timer.start()  # 重启以应用新计时

    def set_auto_hide_enabled(self, enabled: bool):
        """外部（设置页）实时开关空闲吸边自动隐藏。

        关闭时：立即停表；若球此刻正停在半隐藏状态，滑回屏内恢复完整显示——
        否则球会永久卡在半个屏外，只有鼠标移近才滑出，与"关闭自动隐藏"的语义相反。
        打开时：若球正贴边则立即重新开始计时，无需等下一次交互。
        """
        enabled = bool(enabled)
        self._auto_hide_enabled = enabled
        timer = getattr(self, '_idle_hide_timer', None)
        if timer is None:
            return
        timer.stop()
        if enabled:
            self._start_idle_hide_timer()       # 贴边时重新开始计时
        elif self._hidden_to_edge:
            self._slide_out_from_edge()         # 解除半隐藏，恢复完整显示

    def _is_near_edge(self):
        if self._hidden_to_edge:
            return False
        screen = get_screen_geometry()
        # 以窗口边界到屏幕对应边缘的距离判定（而非球心）：
        # 拖拽吸附后窗口会正好贴边（边界距离≈0），需能正确判定为「近边」以触发半隐藏
        x = self.pos().x()
        y = self.pos().y()
        S = self._host_size
        d = min(x - screen.left(),
                screen.right() - (x + S),
                y - screen.top(),
                screen.bottom() - (y + S))
        return d <= self.EDGE_THRESHOLD

    def _start_idle_hide_timer(self):
        # 总开关关闭时永不启动：所有触发点（悬停离开、拖拽结束、吸边完成）
        # 统一走这里，一处判定即可全链路生效
        if not self._auto_hide_enabled:
            self._idle_hide_timer.stop()
            return
        # 番茄钟计时中（含暂停）不自动隐藏：环在显示倒计时进度
        # （用户约定 2026-09-27：开启番茄钟时关闭悬浮球自动隐藏）
        if self.pomodoro_busy():
            self._idle_hide_timer.stop()
            return
        if self._is_near_edge():
            self._idle_hide_timer.start()
        else:
            self._idle_hide_timer.stop()

    def _on_idle_hide_timeout(self):
        if self._hidden_to_edge:
            return
        if self._hovered or self._dragging:
            return
        self._check_edge_hide()

    def _check_edge_hide(self):
        """空闲时靠近任一边缘 → 吸边隐藏球体一半。以窗口边界到屏幕边缘距离判断。"""
        screen = get_screen_geometry()
        S = self._host_size
        x = self.pos().x()
        y = self.pos().y()
        d_left = x - screen.left()
        d_right = screen.right() - (x + S)
        d_top = y - screen.top()
        d_bottom = screen.bottom() - (y + S)
        min_dist = min(d_left, d_right, d_top, d_bottom)
        if min_dist > self.EDGE_THRESHOLD:
            self._hidden_to_edge = False
            self._edge_side = None
            return
        # 半隐藏前的"正常位置"（C4）：落盘与滑出都用它，避免存下半个屏外坐标
        self._normal_pos = self.pos()
        if min_dist == d_left:
            target = QPoint(int(screen.left() - S / 2.0), self.y())
            self._animate_to(target, 'left')
        elif min_dist == d_right:
            target = QPoint(int(screen.right() - S / 2.0), self.y())
            self._animate_to(target, 'right')
        elif min_dist == d_top:
            target = QPoint(self.x(), int(screen.top() - S / 2.0))
            self._animate_to(target, 'top')
        else:
            target = QPoint(self.x(), int(screen.bottom() - S / 2.0))
            self._animate_to(target, 'bottom')

    def _animate_to(self, target: QPoint, edge_side: str, on_finished=None):
        def _on_finished():
            self._hidden_to_edge = True
            if on_finished is not None:
                on_finished()
        self._edge_side = edge_side
        self._animate_pos(target, 220, QEasingCurve.Type.OutCubic,
                          on_finished=_on_finished)

    def _slide_out_from_edge(self):
        """鼠标移近时，悬浮球从吸边的半隐藏状态滑出到屏幕内"""
        screen = get_screen_geometry()
        S = self._host_size
        half = S / 2.0
        if self._edge_side == 'left':
            target = QPoint(int(screen.left() - half + self._ball_size / 2.0 + 2), self.y())
        elif self._edge_side == 'right':
            target = QPoint(int(screen.right() - half - self._ball_size / 2.0 - 2), self.y())
        elif self._edge_side == 'top':
            target = QPoint(self.x(), int(screen.top() - half + self._ball_size / 2.0 + 2))
        elif self._edge_side == 'bottom':
            target = QPoint(self.x(), int(screen.bottom() - half - self._ball_size / 2.0 - 2))
        else:
            return

        self._hidden_to_edge = False

        def _on_slide_out_finished():
            # 滑出后的位置即"正常位置"（C4），下次落盘/再隐藏都以它为准
            self._normal_pos = self.pos()
            if not self._dragging and self._ball_hit(QCursor.pos()):
                self._show_card_on_hover()
            else:
                self._start_idle_hide_timer()

        self._animate_pos(target, 220, QEasingCurve.Type.OutCubic,
                          on_finished=_on_slide_out_finished)

    # ---------------- 公开接口 ----------------
    def apply_ball_size(self, size: int):
        """设置页调整球体直径（C1）：重建宿主尺寸、保持球心不动并落盘"""
        try:
            size = max(self.MIN_BALL_SIZE, min(self.MAX_BALL_SIZE, int(size)))
        except (TypeError, ValueError):
            return
        if size == self._ball_size:
            return
        center = self._ball_center()            # 保持球心不变，视觉上不跳位
        self._ball_size = size
        self._host_size = size + self.SHADOW_MARGIN * 2
        self._surface.set_ball_size(size)
        self.setFixedSize(self._host_size, self._host_size)
        self._surface.setGeometry(0, 0, self._host_size, self._host_size)
        self.move(QPoint(int(center.x() - self._host_size / 2.0),
                         int(center.y() - self._host_size / 2.0)))
        self._ensure_ball_on_screen()
        self._normal_pos = self.pos()
        self._save_position()
        # 卡片若正显示，按新球体位置重新贴靠
        if self._card_window.isVisible():
            self._card_window.popup_near(self._ball_visual_rect())

    def set_fullscreen_hidden(self, hidden: bool):
        """全屏应用让位（B8）：全屏时隐藏球与卡片，退出全屏恢复。

        只影响"因全屏而隐藏"这一种情况——若用户本就把球设为隐藏
        （ball_visible=False），退出全屏也不会把它显示出来。
        """
        if hidden:
            if not self.isVisible():
                return
            self._fs_hidden = True
            if self._card_window.isVisible():
                self._card_window.hide()
            self.hide()
        else:
            if not self._fs_hidden:
                return
            self._fs_hidden = False
            if self._config is None or self._config.get("ball_visible", True):
                self.show()

    def refresh_badge(self):
        """刷新球体徽标（A4）：显示"今日到期 + 已逾期未完成"任务数。

        口径与任务页 / 小卡片 / 托盘提醒**完全一致**（统一走 task_state）：
        脏日期解析失败 → 视为无日期，不计入、不标红。
        """
        count = 0
        if self._task_manager is not None:
            try:
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                for t in self._task_manager.get_all_tasks():
                    if t.done:
                        continue
                    state, _delta = task_state(t.deadline, today)
                    if state in (STATE_TODAY, STATE_OVERDUE):
                        count += 1
            except Exception:
                count = 0
        if self._surface is not None:
            self._surface.set_badge(count)

    def pulse(self):
        """成功反馈（A4）：球体光晕脉冲一次（拖入文件 / 剪贴板捕获 / 快速捕捉）"""
        if self.isVisible() and self._surface is not None:
            self._surface.pulse()

    def update_cards(self, cards):
        """外部更新知识卡片列表"""
        self._cards = cards
        if hasattr(self, '_card_window'):
            self._card_window.set_cards(cards)


# ====================================================================
# 程序入口
# ====================================================================
# 说明：`_get_base_dir` / `_find_icon_file` 两个薄包装已删除（成熟化 4.3）
# —— 它们只是 src/app_paths.get_base_dir / find_icon_file 的转发，
# 现直接使用 app_paths 公开函数；`_shutdown_once`（幂等退出收尾）与
# `_check_data_integrity`（启动数据检查）属装配编排，随 wiring.py 拆分一并迁移。

def _shutdown_once(state, steps) -> bool:
    """退出收尾统一入口（1.4）：幂等执行收尾步骤，单步失败只告警不阻断。

    _safe_quit（托盘退出）与 aboutToQuit（事件循环结束）都会触发收尾，
    二者收口到这里，靠 state["done"] 保证连调多次只有第一次生效。

    state : {"done": bool} 收尾状态标记（首次调用置 True）
    steps : [(描述, 可调用)]；可调用抛任何异常（含组件尚未创建的
            NameError——启动早期异常退出时收尾仍可能被触发）都只写日志
    返回  True=本次实际执行；False=已收尾过，幂等跳过
    """
    if state.get("done"):
        return False
    state["done"] = True
    from src.logger import get_logger
    log = get_logger()
    for desc, fn in steps:
        try:
            fn()
        except Exception as exc:                  # noqa: BLE001
            log.warning(f"[退出] {desc}失败：{exc}")
    log.info("[退出] 资源收尾完成（热键注销 / AI 本地服务 / 剪贴板监听）")
    return True


def main():
    app = QApplication(sys.argv)
    # 禁用"最后一个窗口关闭时自动退出"——悬浮球/主窗口可能同时隐藏，
    # 程序应保持后台运行，仅通过显式退出（托盘菜单/悬浮球右键/closeEvent）退出
    app.setQuitOnLastWindowClosed(False)

    # ---- 设置程序图标（影响任务栏和窗口标题栏图标）----
    _icon = QIcon()
    _icon_path = find_icon_file()
    if _icon_path:
        _icon = QIcon(_icon_path)
        app.setWindowIcon(_icon)

    # ---- 系统托盘图标（任务栏通知区域；D1-lite 拆至 src/tray.py）----
    # 点击托盘图标：主窗口显隐切换（不退出程序）。创建时序与原实现一致：
    # 早于单实例检测；点击/气泡接线在窗口就绪后 tray.attach()，菜单在
    # 回调闭包就绪后 tray.build_menu() 完成。
    from src.tray import TrayController
    tray = TrayController(icon=_icon)

    # ---- 单实例检测 ----
    singleton = SingleInstance()
    if not singleton.acquire():
        # 已有实例在运行 → 发送唤醒信号让首个实例显示窗口，然后静默退出
        SingleInstance.signal_show()
        sys.exit(0)

    # 首个实例：创建命名事件，用于接收后续实例的唤醒信号
    singleton.create_event()

    # ---- 定位数据文件（统一收纳进 float_data/，路径函数见 src/app_paths.py）----
    from src.app_paths import get_data_dir, get_docx_path, get_data_root
    base_dir = get_base_dir()
    # 2.2 双轨：logger / TempAssetManager 等自行拼「base_dir + float_data」
    # 的调用方，安装版要改传数据根（源码/便携下与 base_dir 同值，行为不变）
    data_base = get_data_root()
    # 2.2 安装版数据迁移：旧版数据写在 exe 同目录 float_data/，安装版数据根
    # 在 %APPDATA%\FloatPulse。必须先于任何数据文件创建询问一次（检测零
    # 副作用；便携/源码/已迁移过一律静默跳过，同意后旧目录改名留备份）。
    from src.portable_migrate import maybe_prompt_migrate
    _migrate_result = maybe_prompt_migrate(exe_dir=base_dir)
    data_dir = get_data_dir(base_dir)
    docx_path = get_docx_path(base_dir)
    schedule_path = os.path.join(data_dir, "schedule.json")
    notes_path = os.path.join(data_dir, "notes.json")
    fragments_path = os.path.join(data_dir, "fragments.json")
    docx_meta_path = os.path.join(data_dir, "docx_meta.json")
    config_path = os.path.join(data_dir, "config.json")

    # ---- 初始化日志系统（自动创建 float_data/app.log）----
    from src.logger import (init_logger, install_excepthook, get_logger,
                            mark_session_start, mark_session_end)
    logger = init_logger(data_base, level=logging.INFO)
    install_excepthook()  # 替换全局异常钩子为带日志记录的版本
    mark_session_start()   # [会话] 启动 vX.Y.Z（与 aboutToQuit 的正常退出标记成对）
    logger.info("=" * 50)
    logger.info("程序启动")
    logger.info(f"base_dir = {base_dir}")
    logger.info(f"data_dir = {data_dir}")
    if _migrate_result:
        logger.info("[迁移] 旧版数据迁移结果：%s", _migrate_result)

    # ---- 配置管理器 ----
    config_manager = ConfigManager(config_path)
    logger.info("配置管理器初始化完成")
    # 3.5 界面缩放：建窗口前按 config 应用全局字号（活字缩放）——
    # 所有窗口随后以缩放后的字号构建；设置页改动走 settings_panel 的
    # 同款链路即时生效，无需重启（见 theme.apply_app_font）。
    apply_app_font(config_manager.get("ui_scale", 100))
    # 3.1 跟随系统：config 可能存 "follow"——启动链路各窗口统一吃解析后
    # 的具体主题（light/dark），"follow" 原始值只留在 config 与主窗口；
    # 运行期切换走 main_window.theme_changed 广播（同样发具体主题名）。
    _resolved_theme = resolve_theme_name(
        config_manager.get("theme", DEFAULT_THEME))

    # ---- 启动闪屏 + 分阶段打点 ----
    # 启动序列是同步的，功能/插件增多后可达数秒：闪屏让等待可见，
    # [启动] 打点让「慢在哪」可直接从 app.log 定位（2026-09-27）。
    # _mark 在每个阶段开始时调用：结算上一段耗时 → 更新闪屏文案 →
    # 泵一次事件循环（动画在同步段之间才有机会转起来）。
    from src.splash import LaunchSplash
    splash = LaunchSplash(theme=_resolved_theme)
    splash.start()
    _boot = {"t0": time.monotonic(), "last": time.monotonic(),
             "stage": "初始准备"}

    def _mark(stage: str):
        now = time.monotonic()
        logger.info(
            f"[启动] {_boot['stage']} 耗时 {(now - _boot['last']) * 1000:.0f}ms"
            f"（累计 {(now - _boot['t0']) * 1000:.0f}ms）")
        _boot["last"] = now
        _boot["stage"] = stage
        splash.set_stage(f"{stage}…")
        app.processEvents()

    _mark("检查数据完整性")

    # ---- 启动时数据完整性检查 ----
    _check_data_integrity(data_dir, logger)

    # ---- docx 管理器 ----
    _mark("加载知识库")
    docx_manager = DocxManager(docx_path, docx_meta_path)
    paragraphs, err = docx_manager.load()
    if err:
        QMessageBox.warning(
            None, "知识库加载失败",
            err + "\n\n程序仍可启动，但知识卡片模式将提示无内容。"
        )
        cards = []
    else:
        cards = docx_manager.get_cards()
        if not cards:
            QMessageBox.information(
                None, "提示",
                "已在「知识库.docx」中找到，但未提取到有效知识卡片。\n"
                "（有效段落需非空且长度 ≥ 4 个字符）\n\n"
                "程序仍可启动，知识卡片模式将提示无内容。"
            )

    # ---- 外部修改检测 ----
    if docx_manager.check_external_modification():
        ret = QMessageBox.question(
            None, "检测到外部修改",
            "「知识库.docx」在外部被修改，是否重新加载？\n\n"
            "点击「Yes」重新加载文档；点击「No」保留上次内存版本。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if ret == QMessageBox.StandardButton.Yes:
            docx_manager.reload()
            cards = docx_manager.get_cards()

    # ---- 业务管理器（与悬浮球、大窗口共享同一实例）----
    _mark("读取数据文件")
    task_manager = TaskManager(schedule_path)
    note_manager = NoteManager(notes_path)
    fragment_manager = FragmentManager(fragments_path)

    # ---- 桌面便签（几何存独立 stickies.json，与 notes/schedule 生命周期解耦）----
    from src.sticky_notes import StickyStore, StickyNoteManager
    sticky_store = StickyStore(
        os.path.join(data_dir, "stickies.json"),
        note_ids=lambda: {n.note_id for n in note_manager.get_all_notes()},
        task_ids=lambda: {t.task_id for t in task_manager.get_all_tasks()},
        fragment_ids=lambda: {f.fragment_id
                              for f in fragment_manager.get_all_fragments()},
    )
    sticky_manager = StickyNoteManager(
        note_manager, sticky_store,
        theme=_resolved_theme,
        task_manager=task_manager,
        fragment_manager=fragment_manager)

    # ---- 临时素材管理器（拖图片/文件到悬浮球时复制保存）----
    _mark("初始化素材与导航")
    temp_asset_manager = TempAssetManager(
        data_base,
        max_assets=config_manager.get("temp_asset_max_count", 50),
        max_days=config_manager.get("temp_asset_max_days", 30),
        max_file_mb=config_manager.get("temp_asset_max_file_mb", 50),
    )

    # ---- 网址导航管理器 ----
    nav_manager = NavManager(os.path.join(data_dir, "nav.json"))

    # ---- 剪贴板监听 ----
    # temp_asset_manager 一并注入：剪贴板里的图片直接进素材池（Y2）
    clipboard_monitor = ClipboardMonitor(
        fragment_manager, config_manager, temp_asset_manager)
    clipboard_monitor.start()

    # ---- 大窗口主UI ----
    _mark("构建主窗口界面")
    main_window = MainWindow(
        task_manager, note_manager, fragment_manager,
        docx_manager, config_manager, clipboard_monitor,
        temp_asset_manager, nav_manager
    )
    # 桌面便签管理器注入宿主（面板经 @property sticky_manager 晚绑定读取）
    main_window.sticky_manager = sticky_manager

    # ---- 悬浮球 ----
    _mark("初始化悬浮球")
    ball = FloatingBall(
        cards, task_manager, note_manager,
        fragment_manager, docx_manager, config_manager,
        clipboard_monitor, main_window, temp_asset_manager
    )
    # 根据配置决定悬浮球是否显示（默认显示）
    if config_manager.get("ball_visible", True):
        ball.show()

    # ---- 全屏应用检测（B8）：全屏时自动让位，退出全屏恢复 ----
    _mark("接线与托盘")
    from src.fullscreen_watcher import FullscreenWatcher
    fs_watcher = FullscreenWatcher(
        exclude_hwnds=lambda: (int(ball.winId()), int(main_window.winId())))

    def _apply_fullscreen_watch():
        """按配置启停全屏检测（设置页开关变更时重新应用）"""
        if config_manager.get("hide_on_fullscreen", True):
            if not fs_watcher.is_running():
                fs_watcher.start()
        else:
            if fs_watcher.is_running():
                fs_watcher.stop()
            ball.set_fullscreen_hidden(False)

    def _on_fullscreen_changed(is_fs: bool):
        """前台全屏应用出现/退出 → 悬浮球自动让位/恢复

        例外：番茄钟计时中（含暂停）不让位——球上进度环在显示
        倒计时，藏起来就看不到剩余时间了（用户约定 2026-09-27）。
        """
        if is_fs and ball.pomodoro_busy():
            get_logger().info("全屏检测：番茄钟计时中，悬浮球保持可见不让位")
            return
        ball.set_fullscreen_hidden(is_fs)
        get_logger().info(f"全屏检测：{'进入全屏，悬浮球让位' if is_fs else '退出全屏，悬浮球恢复'}")

    fs_watcher.fullscreen_changed.connect(_on_fullscreen_changed)
    _apply_fullscreen_watch()

    def _sync_fullscreen_for_pomodoro(_state: str):
        """番茄钟开始/结束 → 与全屏让位状态对齐。

        计时开始时若正处于全屏，取消让位把球亮出来；
        计时结束/停止时若仍处于全屏，补做让位。
        """
        if not fs_watcher.is_running() or not fs_watcher.is_fullscreen():
            return
        ball.set_fullscreen_hidden(not ball.pomodoro_busy())

    # 番茄钟状态变更 → 与全屏让位状态对齐（公开中继信号，勿戳 ball._pomodoro 私有成员）
    ball.pomodoro_state_changed.connect(_sync_fullscreen_for_pomodoro)

    # ---- 托盘点击/气泡接线（D1-lite：实现在 src/tray.py）----
    # —— 原内联 _on_tray_activated 已随托盘本体拆出，行为不变：单击切换
    #    主窗口显隐（带日志），忽略双击/右键；气泡点击 → 主窗口任务页。
    tray.attach(main_window, config_manager)

    # ---- 3.3 首次「关窗收进托盘」→ 托盘气泡提示一次 ----
    # 关主窗口默认收进托盘（close_to_tray=True），但此前无任何说明，
    # 新用户容易以为程序丢了。用 config 的 tray_hint_shown 防重复，
    # 提示后置 True 并落盘。主窗口只发信号（hidden_to_tray），
    # 不直接戳托盘/配置——分层与既有通信模式一致。
    main_window.hidden_to_tray.connect(tray.notify_hidden_to_tray)

    # ---- 1.1 启动一次性托盘告知：config 损坏重置 ----
    # 此前损坏是静默回退默认，用户的热键/AI key/主题无痕迹丢失；
    # 损坏文件已由 ConfigManager 备份为 .corrupt.bak，这里只负责告知。
    # （3.2 的「上次可能异常退出」气泡已按用户要求移除：开发/调试场景
    #  强杀退出是常态，判定恒真、每次启动必弹成噪音。）
    tray.notify_startup_once()

    # ==================================================================
    # 信号槽桥梁：大小窗口数据双向同步
    # ==================================================================
    # 0. 注入 nav_manager / config_manager / asset_manager / fragment_manager 到小卡片
    ball.card_window.set_nav_manager(nav_manager)
    ball.card_window.set_config_manager(config_manager)
    ball.card_window.set_asset_manager(temp_asset_manager)
    ball.card_window.set_fragment_manager(fragment_manager)

    # ---- 退出显式收尾（1.4）：热键注销 / AI 本地服务 / 剪贴板监听 ----
    # 各组件在下方集成段才创建（晚绑定），首次调用必然发生在事件循环期
    _shutdown_state = {"done": False}

    def _shutdown_resources():
        """幂等收尾：三个热键管理器注销 + AI_SERVER.stop + 剪贴板监听停止"""
        _shutdown_once(_shutdown_state, [
            # global_hotkey.py：程序退出前务必 unregister_all()，否则
            # 组合键残留占用到进程结束
            ("快捕条热键注销", lambda: hotkey_mgr.unregister_all()),
            ("截图热键注销", lambda: shot_hotkey_mgr.unregister_all()),
            ("插件热键注销", lambda: plugin_hotkey_mgr.unregister_all()),
            # AI 本地服务（llama-server）：退出时主动停止，不再只靠 JobObject 兜底
            ("AI 本地服务停止", lambda: AI_SERVER.stop()),
            # 剪贴板监听断开（stop 自身幂等：未启动时直接返回）
            ("剪贴板监听停止", lambda: clipboard_monitor.stop()),
        ])

    # 安全退出函数：重置卡片状态为默认首页，再退出程序
    def _safe_quit():
        _shutdown_resources()
        # 关闭截图覆盖层与全部钉图（V4 截图钉屏；晚绑定：集成代码在其后定义）
        try:
            screenshot_pin.close_all()
        except Exception:
            pass
        # 桌面便签：几何立即落盘后收掉全部窗口（笔记数据保留）
        try:
            sticky_manager.save_now()
            sticky_manager.close_all()
        except Exception:
            pass
        # 重置卡片窗口状态为默认首页
        ball.card_window.reset_to_home()
        main_window.allow_close = True
        QApplication.quit()

    # 注（1.3）：不再注册「全局 Esc → 退出程序」快捷键——此前任何窗口按
    # Esc 都会杀掉整个进程，与桌面软件惯例相反。Esc 语义逐表面收口：
    # 卡片 / 快捕条 / 截图框选 / 钉图 / 便签各自关闭或取消，主窗口 Esc 无动作。

    # ---- 托盘右键菜单（F1；D1-lite：构建在 src/tray.py）----
    # 显示/隐藏主窗口、显示/隐藏悬浮球、📌 便签子菜单、退出程序。
    # 闭包依赖经公开回调注入（TrayController 不持有业务对象之外的全局态）。
    def _toggle_main_window():
        if main_window.isVisible():
            main_window.hide()
        else:
            main_window.show()
            main_window.raise_()
            main_window.activateWindow()

    def _toggle_ball_visibility():
        visible = not ball.isVisible()
        ball.setVisible(visible)
        config_manager.set("ball_visible", visible)
        config_manager.save()

    tray.build_menu(
        toggle_main_window=_toggle_main_window,
        toggle_ball_visibility=_toggle_ball_visibility,
        quit_app=_safe_quit,
        sticky_manager=sticky_manager,
    )

    # ---- 任务到期提醒（启动时 + 每日 9:00 托盘气泡）----
    def _msecs_until_next(hour: int) -> int:
        """距下一个指定整点的毫秒数（用于每日定时）"""
        from datetime import datetime, timedelta
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return int((target - now).total_seconds() * 1000)

    def _check_task_reminders():
        """扫描未完成任务并托盘气泡提醒（三桶口径）。

        ★2026-09-30 修：此前只提醒「今日到期 / 已逾期」两项，导致
        **无截止日的未完成任务永远不会被提醒**（用户反馈"未完成任务不提示"，
        实测其 5 条未完成任务 deadline 全为空串 → 命中 STATE_NONE → 静默）。

        三桶（顺序即气泡内展示顺序）：
          · 已逾期   —— STATE_OVERDUE
          · 今日到期 —— STATE_TODAY
          · 未安排日期 —— STATE_NONE 且未完成（无日期 / 日期写坏）

        任务状态仍统一走 task_state（脏日期解析失败 → 无日期，不标红、
        不进逾期桶），分桶由纯函数 bucket_unfinished 固化，**不新增也不
        改写任何状态口径**；球体徽标（refresh_badge）保持「逾期 + 今日
        到期」原口径不变。
        """
        ball.refresh_badge()   # 顺带刷新球体徽标（跨天后"今日到期"口径会变）
        if not config_manager.get("task_reminder_enabled", True):
            return
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        overdue, due, undated = bucket_unfinished(
            task_manager.get_all_tasks(), today)
        total = len(overdue) + len(due) + len(undated)
        if not total:
            return
        lines = []
        for label, bucket in (("【已逾期】", overdue),
                              ("【今日到期】", due),
                              ("【未安排日期】", undated)):
            if not bucket:
                continue
            lines.append(label)
            lines.extend(f"· {t.title}" for t in bucket[:5])
            if len(bucket) > 5:
                lines.append(f"· …另有 {len(bucket) - 5} 项")
        tray.show_message(
            f"任务提醒（{total} 项未完成）",
            "\n".join(lines),
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )
        get_logger().info(
            f"任务提醒已弹出：逾期 {len(overdue)}，今日到期 {len(due)}，"
            f"未安排日期 {len(undated)}")

    def _schedule_daily_reminder():
        """每日 9:00 检查一次（自循环重排）"""
        _check_task_reminders()
        QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)

    # 提醒气泡点击 → 显示主窗口并切到任务页（D1-lite：随托盘拆至
    # TrayController._on_message_clicked，setup 时已接线）
    QTimer.singleShot(4000, _check_task_reminders)          # 启动 4 秒后首次检查
    QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)  # 之后每天 9:00

    # ---- 2.3 启动后延迟静默检查更新（默认开，每天至多一次）----
    # 立场不变：不自动下载、失败静默、不携带任何本机数据。延迟 15 秒
    # 错开启动高峰；频率判定走 update_checker 的纯逻辑（should_check_now
    # / mark_checked），托盘只在真的发现新版本时打扰一次。
    def _silent_update_check():
        from src import update_checker
        from src.app_version import APP_VERSION
        from src.plugin_net import make_async_getter
        if not update_checker.should_check_now(config_manager):
            return
        # 发起即记账（成功失败都算当天已查），写盘在这里负责
        update_checker.mark_checked(config_manager)
        config_manager.save()

        def _on_silent_result(result: dict):
            if not result.get("ok"):
                return                  # 离线 / 限流：静默，不扰民
            tag = update_checker.extract_tag(result.get("body") or "")
            if not tag or not update_checker.is_newer(tag):
                return                  # 无新版：不动 latest_known_version
            config_manager.set("latest_known_version", tag)
            config_manager.save()
            tray.show_message(
                f"发现新版本 {tag}",
                f"当前 v{APP_VERSION}——到 设置 → 关于 查看更新内容",
                QSystemTrayIcon.MessageIcon.Information, 8000)
            get_logger().info(
                f"[更新] 静默检查发现新版本 {tag}（当前 v{APP_VERSION}）")

        getter = make_async_getter()
        if not getter(update_checker.RELEASES_API_URL,
                      update_checker.check_headers(),
                      update_checker.CHECK_TIMEOUT_S, _on_silent_result):
            pass  # 桥拒绝时也会回调一次 ok=False 的结果，静默即可

    QTimer.singleShot(15000, _silent_update_check)

    # ---- 全局快速捕捉条（热键呼出 → 一句话进碎片池）----
    _mark("注册热键")
    from src.global_hotkey import GlobalHotkeyManager
    from src.hotkey_binding import ConfigHotkeyBinding, reapply_hotkey_bindings
    from src.quick_capture import QuickCaptureWindow

    hotkey_mgr = GlobalHotkeyManager()
    app.eventDispatcher().installNativeEventFilter(hotkey_mgr)

    quick_capture = QuickCaptureWindow(fragment_manager, theme=_resolved_theme, config_manager=config_manager)

    # D4 样板收敛：配置驱动的单键绑定（注销 → hide 钩子 → 开关判定 → 注册）
    quick_capture_hotkey = ConfigHotkeyBinding(
        hotkey_mgr, config_manager,
        enabled_key="quick_capture_enabled", hotkey_key="quick_capture_hotkey",
        default_hotkey="Ctrl+Alt+K", callback=quick_capture.toggle,
        fail_log="全局热键注册失败（可能被占用）：{hotkey}",
        pre_hooks=(quick_capture.hide,))

    main_window.quick_capture_changed.connect(quick_capture_hotkey.reapply)
    main_window.theme_changed.connect(quick_capture.apply_theme)
    # 快速捕捉提交成功 → 球体脉冲反馈（A4）
    quick_capture.capture_submitted.connect(lambda _text: ball.pulse())
    quick_capture_hotkey.reapply()

    # ---- 截图钉屏（Ctrl+Alt+S → 框选 → 置顶参考浮窗，V4）----
    # 用独立 GlobalHotkeyManager：快捕条重注册会 unregister_all()，
    # 共用实例会把截图热键一起踢掉
    from src.screenshot_pin import ScreenshotPinController

    shot_hotkey_mgr = GlobalHotkeyManager()
    app.eventDispatcher().installNativeEventFilter(shot_hotkey_mgr)
    screenshot_pin = ScreenshotPinController(
        theme=_resolved_theme
    )

    # D4 样板收敛：配置驱动的单键绑定（注销 → 开关判定 → 注册）
    screenshot_hotkey = ConfigHotkeyBinding(
        shot_hotkey_mgr, config_manager,
        enabled_key="screenshot_enabled", hotkey_key="screenshot_hotkey",
        default_hotkey="Ctrl+Alt+S", callback=screenshot_pin.start_capture,
        fail_log="截图热键注册失败（可能被占用）：{hotkey}")

    screenshot_hotkey.reapply()
    main_window.screenshot_changed.connect(screenshot_hotkey.reapply)
    main_window.theme_changed.connect(screenshot_pin.apply_theme)
    # 悬浮球右键菜单入口
    ball.add_context_action("✂ 截图钉屏", screenshot_pin.start_capture)

    # ---- 番茄钟（球体进度环 + 右键菜单 + 任务绑定，V4）----
    def _on_pomodoro_phase_finished(phase, title):
        """相位计满 → 托盘气泡（非模态）+ 主窗口任务页刷新（番茄计数变了）"""
        if phase == PHASE_FOCUS:
            body = f"专注完成「{title}」，休息一下！" if title else "专注完成，休息一下！"
            tray.show_message(
                "🍅 番茄钟", body,
                QSystemTrayIcon.MessageIcon.Information, 6000)
            main_window.refresh_tasks()
        get_logger().info(f"[番茄钟] 相位完成: phase={phase}, task={title or '自由专注'}")

    ball.pomodoro_phase_finished.connect(_on_pomodoro_phase_finished)
    main_window.pomodoro_changed.connect(ball.apply_pomodoro_config)
    # 任务页右键「专注此任务」→ 球体开始绑定式专注
    main_window.task_focus_requested.connect(ball.start_focus)

    # ---- 悬浮球插件系统（外置专精功能：<base_dir>/plugins/ 下的插件包）----
    # 边界：球本体 / 卡片 6 模式 / 拖放分流 / 六大内置功能一律不插件化，
    #       只把「新增的专精单一功能」外置。详见 docs/插件开发说明.md
    _mark("加载插件")
    # 1.5 配套：本地 AI 端点（llama-server / 回环 base_url）登记进插件桥
    # 回环白名单，否则 SSRF 闸会把本地 AI 请求一并拦掉
    from src.ai_server import sync_loopback_allowlist
    sync_loopback_allowlist(config_manager)
    from src.plugin_api import ActionRegistry, PluginContext, PluginData
    from src.plugin_loader import PluginLoader
    from src.plugin_net import make_async_poster

    plugin_registry = ActionRegistry(
        logger=get_logger(),
        reserved_hotkeys=(
            config_manager.get("quick_capture_hotkey", "Ctrl+Alt+K"),
            config_manager.get("screenshot_hotkey", "Ctrl+Alt+S"),
        ),
    )

    # 只读数据快照：provider 一律返回**新建的 dict 副本**，
    # PluginData 再 deepcopy 一次才交给插件 —— 插件改不到宿主对象。
    # 数据源故意只给只读的「任务 / 碎片 / 笔记 / 番茄 / 知识库」，
    # 不暴露球与主窗口本体。
    plugin_data = PluginData(
        logger=get_logger(),
        providers={
            "tasks": lambda: [t.to_dict() for t in task_manager.get_all_tasks()],
            "fragments": lambda: [f.to_dict()
                                  for f in fragment_manager.get_all_fragments()],
            "notes": lambda: [n.to_dict() for n in note_manager.get_all_notes()],
            "pomodoro": ball.pomodoro_state,
            # 知识库：num 从 1 开始（与知识库面板「编号」一致）；
            # hash 是段落指纹，供写入侧校验「还是我看到的这一段吗」
            "knowledge": lambda: [
                {"num": i + 1, "text": p.text, "preview": p.preview,
                 "hash": p.hash}
                for i, p in enumerate(docx_manager.get_paragraphs())],
            # 临时素材：只给元数据，**不给 stored_path**——素材本体在宿主
            # 私有目录里，插件拿不到也不该拿（依赖白名单没有文件系统）
            "assets": lambda: [
                {"asset_id": a.asset_id, "original_name": a.original_name,
                 "is_image": a.is_image, "size_bytes": a.size_bytes,
                 "added_time": a.added_time}
                for a in temp_asset_manager.get_all_assets()],
        },
    )

    def _kb_blocked():
        """知识库写入前的安全闸：检测到外部改动就拒绝写。

        知识库是用户可直接用 Word/WPS 打开编辑的 docx。外部改过之后内存
        模型已过期，而 ``DocxManager.save()`` 是**整篇回写**——此时落盘会把
        用户在 Word 里的改动整篇覆盖掉。宁可拒绝并要求先重新加载。

        write 与 manage 两组 provider 共用（故定义在外层）。
        """
        if docx_manager.check_external_modification():
            get_logger().warning(
                "[插件] 知识库检测到外部修改，已拒绝写入"
                "（请先在知识库页点「🔄 重新加载」）")
            return True
        return False

    def _kb_paragraph(num):
        """知识库编号（1 起）→ ParagraphInfo；非法或越界返回 None"""
        if not isinstance(num, int) or isinstance(num, bool) or num < 1:
            return None
        items = docx_manager.get_paragraphs()
        if num > len(items):
            return None
        return items[num - 1]

    def _make_write_providers():
        """插件写入口的宿主实现（2026-09-27 受限写能力）。

        四个 provider 各自包一层「写库 + 刷新 UI」，插件拿不到管理器本体：
          - 只增不改删：这里**刻意不提供** update / delete
          - 碎片 source 由 PluginWriter 补 ``插件:<id>``，落库可追溯
          - 写成功立即刷新对应面板与悬浮球徽标，与卡片数据变更走同一链路
          - 知识库额外过 ``_kb_blocked()`` 安全闸
        """
        def _add_fragment(content, source):
            fid = fragment_manager.add_fragment(
                TYPE_CLIPBOARD_TEXT, content, source)
            main_window.refresh_fragments()
            return fid

        def _add_task(title, note, deadline):
            tid = task_manager.add_task(title, note, deadline)
            main_window.refresh_tasks()
            ball.refresh_badge()          # 任务数变了，球体徽标同步
            return tid

        def _add_note(title, content):
            nid = note_manager.add_note(content, title)
            main_window.refresh_notes()
            return nid

        def _add_knowledge(content):
            """追加一段知识，返回编号（从 1 开始，与面板一致）；失败 0"""
            if _kb_blocked():
                return 0
            idx = docx_manager.append_paragraph(content)
            if idx < 0:
                return 0
            if not docx_manager.save():
                # 落盘失败 → 丢弃内存改动，避免面板显示磁盘上没有的段落
                docx_manager.reload()
                return 0
            main_window.refresh_knowledge()
            return idx + 1

        return {"fragment": _add_fragment, "task": _add_task,
                "note": _add_note, "knowledge": _add_knowledge}

    def _make_manage_providers():
        """插件管理入口的宿主实现（2026-09-28 数据管理能力）。

        改 / 删都包一层「改库 + 刷新 UI」；删除前抓整条快照进撤销栈，
        插件拿到的是**撤销令牌**（>0 = 成功），可经 ``undo_delete`` 恢复。

        撤销走"重新插入"路径（宿主 add_* 是唯一入口，不去碰内部 id 分配），
        恢复后编号可能是新的，但内容与关键状态（完成态 / 专注次数）原样还原。
        知识库段落例外：按删除前的 0 基位置 ``insert_paragraph_before`` 放回
        原位（结构化文档里位置本身就是信息）。
        安全三层：能力声明（manage）→ 插件侧分级确认（改删需用户点确认）
        → 这里的内容护栏 + 撤销栈 + 每次操作进审计日志。
        """
        undo_stack = []          # [{"token","kind","payload","used"}]
        undo_seq = [1]           # 单调递增的令牌序号

        def _push_undo(kind, payload):
            token = undo_seq[0]
            undo_seq[0] += 1
            undo_stack.append({"token": token, "kind": kind,
                               "payload": payload, "used": False})
            if len(undo_stack) > 50:      # 容量上限：只留最近 50 次删除
                undo_stack.pop(0)
            return token

        def _take_undo(token):
            """取出未用过的令牌记录并标记已用（一令牌只能用一次）"""
            for rec in undo_stack:
                if rec["token"] == token and not rec["used"]:
                    rec["used"] = True
                    return rec
            return None

        # ---------- 任务 ----------
        def _update_task(tid, title, note, deadline):
            task = task_manager.get_task(tid)
            if task is None:
                return False
            cur = task.to_dict()          # 部分更新：None = 保留原值
            ok = task_manager.update_task(
                tid,
                cur["title"] if title is None else title,
                cur["note"] if note is None else note,
                cur["deadline"] if deadline is None else deadline)
            if ok:
                main_window.refresh_tasks()
            return bool(ok)

        def _set_task_done(tid, done):
            ok = task_manager.set_done(tid, done)
            if ok:
                main_window.refresh_tasks()
                ball.refresh_badge()
            return bool(ok)

        def _delete_task(tid):
            task = task_manager.get_task(tid)
            if task is None:
                return 0
            payload = task.to_dict()
            if not task_manager.delete_task(tid):
                return 0
            main_window.refresh_tasks()
            ball.refresh_badge()
            return _push_undo("task", payload)

        # ---------- 碎片 ----------
        def _update_fragment(fid, content, source):
            ok = fragment_manager.update_fragment(fid, content=content,
                                                 source=source)
            if ok:
                main_window.refresh_fragments()
            return bool(ok)

        def _delete_fragment(fid):
            frag = fragment_manager.get_fragment(fid)
            if frag is None:
                return 0
            payload = frag.to_dict()
            if not fragment_manager.delete_fragment(fid):
                return 0
            main_window.refresh_fragments()
            return _push_undo("fragment", payload)

        # ---------- 笔记 ----------
        def _update_note(nid, title, content):
            cur = note_manager.get_note(nid)
            if cur is None:
                return False
            ok = note_manager.update_note(
                nid, cur.content if content is None else content, title=title)
            if ok:
                main_window.refresh_notes()
            return bool(ok)

        def _delete_note(nid):
            cur = note_manager.get_note(nid)
            if cur is None:
                return 0
            payload = cur.to_dict()
            if not note_manager.delete_note(nid):
                return 0
            main_window.refresh_notes()
            return _push_undo("note", payload)

        # ---------- 知识库（位置型标识：必须校验内容指纹） ----------
        # 编号会随删除前移，docx 又能被外部编辑，所以「编号 N」可能是过期
        # 引用。这里比对调用方回传的段落指纹，对不上就拒改拒删——宁可失败
        # 也不改错段落。
        def _update_knowledge(num, content, expect_hash):
            if _kb_blocked():
                return False
            cur = _kb_paragraph(num)
            if cur is None or cur.hash != expect_hash:
                get_logger().warning(
                    f"[插件] 知识库第 {num} 段指纹不匹配，已拒绝修改"
                    "（内容可能已变化）")
                return False
            if not docx_manager.update_paragraph_text(num - 1, content):
                return False
            if not docx_manager.save():
                docx_manager.reload()
                return False
            main_window.refresh_knowledge()
            return True

        def _delete_knowledge(num, expect_hash):
            if _kb_blocked():
                return 0
            cur = _kb_paragraph(num)
            if cur is None or cur.hash != expect_hash:
                get_logger().warning(
                    f"[插件] 知识库第 {num} 段指纹不匹配，已拒绝删除"
                    "（内容可能已变化）")
                return 0
            # pos 记**删除前**的 0 基位置：撤销时照它放回原位
            payload = {"text": cur.text, "pos": num - 1}
            if not docx_manager.delete_paragraph(num - 1):
                return 0
            if not docx_manager.save():
                docx_manager.reload()
                return 0
            main_window.refresh_knowledge()
            return _push_undo("knowledge", payload)

        # ---------- 撤销 ----------
        def _undo_delete(token):
            rec = _take_undo(token)
            if rec is None:
                return False
            kind, p = rec["kind"], rec["payload"]
            if kind == "knowledge":
                if _kb_blocked():
                    return False
                text = p.get("text") or ""
                pos = int(p.get("pos") or 0)
                items = docx_manager.get_paragraphs()
                if pos < 0 or pos > len(items):
                    pos = len(items)          # 越界（别处又改过）→ 追加到末尾
                if pos < len(items):
                    new_idx = docx_manager.insert_paragraph_before(pos, text)
                else:
                    new_idx = docx_manager.append_paragraph(text)
                if new_idx < 0:
                    return False
                if not docx_manager.save():
                    docx_manager.reload()
                    return False
                main_window.refresh_knowledge()
                return True
            if kind == "task":
                new_id = task_manager.add_task(
                    p.get("title") or "", p.get("note") or "",
                    p.get("deadline") or "")
                if not new_id:
                    return False
                if p.get("done"):
                    task_manager.set_done(new_id, True)
                for _ in range(int(p.get("focus_sessions") or 0)):
                    task_manager.add_focus_session(new_id, 1)
                main_window.refresh_tasks()
                ball.refresh_badge()
            elif kind == "fragment":
                new_id = fragment_manager.add_fragment(
                    p.get("type") or TYPE_CLIPBOARD_TEXT,
                    p.get("content") or "", p.get("source") or "撤销恢复")
                if not new_id:
                    return False
                main_window.refresh_fragments()
            else:                             # note
                new_id = note_manager.add_note(p.get("content") or "",
                                               p.get("title") or "")
                if not new_id:
                    return False
                main_window.refresh_notes()
            return True

        return {
            "update_task": _update_task, "set_task_done": _set_task_done,
            "delete_task": _delete_task,
            "update_fragment": _update_fragment,
            "delete_fragment": _delete_fragment,
            "update_note": _update_note, "delete_note": _delete_note,
            "update_knowledge": _update_knowledge,
            "delete_knowledge": _delete_knowledge,
            "undo_delete": _undo_delete,
        }

    def _make_ai_providers():
        """AI 总配置 provider（2026-09-29 设置页「🧠 AI 总配置」）。

        插件单一真相源：设置页配好云端 / 本地 + 下拉框勾选接入插件后，
        声明 ``capabilities=["ai"]`` 且被勾选的插件经 ``ctx.ai`` 实时读取。
        **params 每次调用都实时读配置**——设置页改完即生效，插件无需
        重建页面或重启程序；is_attached 按插件 id 查 ``ai_plugins`` 列表。
        未接入的插件照旧用各自私有配置（向后兼容），互不影响。
        """
        def _is_attached(plugin_id):
            attached = config_manager.get("ai_plugins", []) or []
            return str(plugin_id or "") in attached

        def _params():
            try:
                port = int(config_manager.get("ai_local_port", 8095) or 8095)
            except (TypeError, ValueError):
                port = 8095
            return {
                "mode": str(config_manager.get("ai_backend_mode", "cloud")
                            or "cloud"),
                "base_url": str(config_manager.get("ai_cloud_base_url", "")
                                or "").strip(),
                "api_key": str(config_manager.get("ai_cloud_api_key", "") or ""),
                "model": str(config_manager.get("ai_cloud_model", "")
                             or "").strip(),
                "local_port": port,
                "local_ready": AI_SERVER.status == "ready",
                "local_status": AI_SERVER.status,
                "local_detail": AI_SERVER.detail,
            }

        def _add_listener(fn):
            return AI_SERVER.add_listener(fn)

        def _remove_listener(fn):
            return AI_SERVER.remove_listener(fn)

        def _stop_local():
            AI_SERVER.stop()
            return True

        return {
            "is_attached": _is_attached, "params": _params,
            "add_listener": _add_listener, "remove_listener": _remove_listener,
            "stop_local": _stop_local,
        }

    plugin_ctx = PluginContext(
        logger=get_logger(),
        config=config_manager.as_dict(),        # 只读快照，插件改不了宿主配置
        show_toast=main_window.show_toast,
        open_main_window=ball.open_main_window,
        open_card_mode=ball.show_card_mode,
        data=plugin_data,
        # 插件私有可写目录：<float_data>/plugins/<插件id>/（首次访问自动创建）
        data_dir_base=os.path.join(data_dir, "plugins"),
        # 插件弹自定义对话框时的父窗口（保证居中、不被主窗口压住）
        parent_window=lambda: main_window,
        # 宿主网络桥（2026-09-27 能力模型）：只有 manifest 声明
        # capabilities=["network"] 的插件才能经它联网（PluginContext 判定），
        # 请求在后台线程跑、回调回 UI 线程；审计日志见 [插件网络]
        http_post_async=make_async_poster(logger=get_logger()),
        # 受限写入口（2026-09-27）：只有声明 capabilities=["write"] 的插件
        # 才能经 ctx.write 新增碎片/任务/笔记。只增不改删，内容有长度护栏，
        # 写成功后刷新对应面板（与卡片数据变更走同一条链路）。
        write_providers=_make_write_providers(),
        # 数据管理入口（2026-09-28）：只有声明 capabilities=["manage"] 的插件
        # 才能经 ctx.manage 改/删既有任务、碎片、笔记。删除返回撤销令牌，
        # undo_delete 可恢复；每次操作进审计日志（app.log 的 [插件管理]）。
        manage_providers=_make_manage_providers(),
        # AI 总配置（2026-09-29）：只有声明 capabilities=["ai"] 且在设置页
        # 下拉框被勾选接入的插件，才能经 ctx.ai 实时读取总配置（params 每
        # 次调用实时读，设置页改完即生效）；本地服务状态由 AI_SERVER 广播。
        ai_providers=_make_ai_providers(),
    )
    plugin_loader = PluginLoader(plugin_registry, plugin_ctx, logger=get_logger())

    # 独立 GlobalHotkeyManager：快捕条重注册会 unregister_all()，
    # 共用实例会把插件热键一起踢掉
    plugin_hotkey_mgr = GlobalHotkeyManager()
    app.eventDispatcher().installNativeEventFilter(plugin_hotkey_mgr)
    ball.set_action_registry(plugin_registry, plugin_ctx)
    # 插件中心页面数据通道：主窗口经只读属性访问 loaded_plugins()
    main_window.set_plugin_loader(plugin_loader)

    def _apply_plugin_hotkeys():
        """按当前注册表绑定插件热键；核心热键优先级最高，冲突的插件让位。

        D4 样板收敛：注销 + 注册 + 失败告警走 reapply_hotkey_bindings，
        这里只负责按注册表筛出绑定表（冲突让位 / 停用跳过）。
        """
        core_norm = {
            str(config_manager.get("quick_capture_hotkey", "Ctrl+Alt+K")).strip().lower().replace(" ", ""),
            str(config_manager.get("screenshot_hotkey", "Ctrl+Alt+S")).strip().lower().replace(" ", ""),
        }
        bindings = []
        for act in plugin_registry.all_actions():
            hotkey = act.declared_hotkey()
            if not hotkey or not act.enabled():
                continue
            if hotkey in core_norm:
                get_logger().warning(
                    f"[插件] 热键与核心功能冲突，插件让位：{act.hotkey}（{act.id}）")
                continue
            bindings.append((
                act.hotkey,
                lambda aid=act.id: plugin_registry.trigger(aid, plugin_ctx),
                f"[插件] 热键注册失败（可能被占用）：{act.hotkey}（{act.id}）",
            ))
        reapply_hotkey_bindings(plugin_hotkey_mgr, bindings)

    def _apply_plugins(_enabled=None):
        """插件总闸：开 → 加载/登记；关 → 摘动作 + 摘页面（模块仍驻留）"""
        if config_manager.get("plugins_enabled", True):
            plugin_loader.load_all()
            _apply_disabled_plugins()
            _register_plugin_pages()
        else:
            _unregister_all_plugin_pages()
            plugin_loader.deactivate()
        ball.refresh_plugin_menu()
        _apply_plugin_hotkeys()

    def _apply_disabled_plugins():
        """按配置回置「被单独停用」的插件状态（2026-09-27）。

        插件中心的启停开关此前只改内存（重启即复原，用户对「停用」的预期落空），
        现在开关写入 config 的 ``plugins_disabled``，这里在登记完成后统一回置。
        未知 id 静默跳过——配置里残留已删除插件的 id 是正常情况。
        """
        for pid in (config_manager.get("plugins_disabled", None) or []):
            if not isinstance(pid, str) or not pid:
                continue
            n = plugin_loader.set_plugin_enabled(pid, False)
            if not n:
                get_logger().info(
                    f"[插件] 配置里记录了停用 {pid}，但该插件未加载（已忽略）")

    def _register_plugin_pages():
        """页面插件：manifest.page → 主窗口导航页（2026-09-27）。

        create_page 抛异常只跳过该插件，绝不拖垮加载（与动作同级容错）。
        register_plugin_page 幂等，插件中心「重新扫描」重入安全。
        被**单独停用**的插件（plugins_disabled）跳过——动作已被回置摘除，
        页面若照常注册就会出现「停用了页面还挂在导航栏」（2026-09-27 修复）。
        """
        disabled = {p for p in (
            config_manager.get("plugins_disabled", None) or [])
            if isinstance(p, str) and p}
        for lp in plugin_loader.loaded_plugins():
            if lp.plugin_id in disabled:
                get_logger().info(f"[插件] 页面跳过（插件被停用）：{lp.plugin_id}")
                continue
            page_spec = lp.manifest.get("page")
            if not page_spec:
                continue
            page_key = f"plugin:{lp.plugin_id}"
            try:
                widget = lp.plugin.create_page(lp.ctx)
                if widget is None:
                    get_logger().warning(
                        f"[插件] 声明了 page 但 create_page 返回空，跳过：{lp.plugin_id}")
                    continue
                main_window.register_plugin_page(page_key, page_spec["title"], widget)
                lp.page_key = page_key     # 插件热键动作经 parent_window 切页用
                get_logger().info(f"[插件] 页面已注入主窗口：{page_key}")
            except Exception as exc:       # noqa: BLE001 - 页面失败不拖垮插件系统
                get_logger().warning(
                    f"[插件] 页面注入失败，跳过：{lp.plugin_id}（{exc!r}）",
                    exc_info=True)

    def _unregister_all_plugin_pages():
        """总闸关闭 → 把全部插件页从主窗口摘掉（2026-09-27）。

        此前总闸关只 deactivate 摘动作/热键，页面与导航键残留在主窗口。
        page_key 记录在 LoadedPlugin 上（注册时写入），逐个注销后清空；
        独立 try 容错——页面注销失败不阻断总闸关闭流程。
        """
        unreg = getattr(main_window, "unregister_plugin_page", None)
        if not callable(unreg):
            return
        for lp in plugin_loader.loaded_plugins():
            key = getattr(lp, "page_key", None)
            if not key:
                continue
            try:
                unreg(key)
            except Exception:              # noqa: BLE001 - 单页失败不阻断
                get_logger().warning(f"[插件] 页面注销失败：{key}", exc_info=True)
            try:
                lp.page_key = None
            except Exception:              # noqa: BLE001
                pass

    def _refresh_core_hotkey_reservation():
        """核心热键变更 → 刷新保留集并重绑插件热键（插件始终让位）"""
        plugin_registry.reserve_hotkeys((
            config_manager.get("quick_capture_hotkey", "Ctrl+Alt+K"),
            config_manager.get("screenshot_hotkey", "Ctrl+Alt+S"),
        ))
        _apply_plugin_hotkeys()

    main_window.plugins_changed.connect(_apply_plugins)
    main_window.quick_capture_changed.connect(_refresh_core_hotkey_reservation)
    main_window.screenshot_changed.connect(_refresh_core_hotkey_reservation)
    _apply_plugins()

    # 1. 小卡片退出请求 → 安全退出程序
    ball.card_window.request_quit.connect(_safe_quit)

    # 1.1 悬浮球右键退出 → 安全退出程序
    ball.request_quit.connect(_safe_quit)

    # 2. 小卡片数据变更 → 大窗口刷新对应面板（若可见）
    def _on_card_data_changed(kind):
        if kind == "task":
            main_window.refresh_tasks()
            ball.refresh_badge()      # 任务增删/完成后同步球体徽标（A4）
        elif kind == "note":
            main_window.refresh_notes()
        elif kind == "asset":
            # 素材变更要刷素材页（原实现误刷碎片页，导致大窗口素材列表不更新）
            main_window.refresh_temp_assets()
        elif kind == "fragment":
            main_window.refresh_fragments()
    ball.card_window.data_changed.connect(_on_card_data_changed)

    # 3. 大窗口主题切换 → 悬浮球 + 小卡片应用主题
    main_window.theme_changed.connect(ball.apply_theme)
    # 3b. 主题切换 → 桌面便签全部换肤
    main_window.theme_changed.connect(sticky_manager.apply_theme)

    # 3c. 系统深浅色变化 → 「跟随系统」模式整链路换肤（3.1）
    # 只复用既有换主题路径：主窗口 _apply_theme 重取配色（get_colors 内部
    # 经 resolve_theme_name 现读系统 scheme），theme_changed 再把具体主题
    # 名广播给球 / 卡片 / 便签 / 快捕条 / 截图钉屏；显式 light/dark 模式忽略。
    def _on_system_scheme_changed(_scheme):
        if config_manager.get("theme", DEFAULT_THEME) != "follow":
            return
        resolved = resolve_theme_name("follow")
        if main_window.current_theme == "follow":
            main_window.reapply_theme()
        main_window.theme_changed.emit(resolved)
        get_logger().info(f"[主题] 系统深浅色变化，跟随系统 → {resolved}")

    from PyQt6.QtGui import QGuiApplication as _QGuiApp
    _scheme_hints = _QGuiApp.instance().styleHints() if _QGuiApp.instance() else None
    if _scheme_hints is not None and hasattr(_scheme_hints, "colorSchemeChanged"):
        _scheme_hints.colorSchemeChanged.connect(_on_system_scheme_changed)

    # 4. 剪贴板新增碎片 → 大窗口刷新碎片页面（若可见）+ 球体脉冲反馈
    def _on_fragment_added(_content=None):
        main_window.refresh_fragments()
        ball.pulse()                  # 成功反馈（A4）
    clipboard_monitor.fragment_added.connect(_on_fragment_added)

    # 4b. 剪贴板图片入库（Y2）→ 刷新素材页面 + 球体脉冲 + 轻提示
    def _on_clipboard_image(_asset_id=None):
        main_window.refresh_temp_assets()
        ball.card_window.notify_assets_changed()
        main_window.show_toast("🖼 剪贴板图片已存入素材池（素材页可查看）")
        ball.pulse()
    if getattr(clipboard_monitor, "image_captured", None) is not None:
        clipboard_monitor.image_captured.connect(_on_clipboard_image)

    # 5. 大窗口数据变更 → 小卡片刷新
    def _on_main_data_changed(kind):
        if kind == "task":
            ball.refresh_badge()      # 大窗口任务变更 → 球体徽标同步（A4）
            if ball.card_window.isVisible():
                ball.card_window.refresh_page("task")
        elif kind == "knowledge":
            # 知识库编辑后重新加载卡片并同步到小卡片
            new_cards = docx_manager.get_cards()
            ball.update_cards(new_cards)
        elif kind == "nav" and ball.card_window.isVisible():
            # 网址导航编辑后刷新小卡片导航页
            ball.card_window.refresh_page("nav")
        elif kind == "asset":
            # 临时素材变更后刷新小卡片素材页（可见立即重建，隐藏则置脏）
            ball.card_window.notify_assets_changed()
        elif kind == "fragment" and ball.card_window.isVisible():
            # 碎片变更后刷新小卡片碎片页
            ball.card_window.refresh_page("fragment")
        elif kind in ("note", "task"):
            # 笔记/任务被删除后，对应桌面便签自动关闭（孤儿窗口不留）
            sticky_manager.validate_open_windows()
    main_window.data_changed.connect(_on_main_data_changed)

    # 5b. 任务便签里改了备注/完成态 → 走主窗口 data_changed 刷新任务页
    #（data_changed("task") 又会触发上面 validate_open_windows，无副作用）
    sticky_manager.task_data_changed.connect(
        lambda: main_window.data_changed.emit("task"))

    # 6. 主窗口悬浮球开关 → 显示/隐藏悬浮球
    main_window.ball_visibility_changed.connect(
        lambda visible: ball.setVisible(visible)
    )

    # 7. 主窗口小卡片保持显示开关 → 实时应用并刷新卡片关闭按钮可见性
    def _on_card_always_show_changed(always_show: bool):
        # 配置已由 main_window 保存；这里刷新关闭按钮与内容净空（两者必须一起变，
        # 否则按钮出现了、内容却没让位，又会压住首行）
        if ball.card_window.isVisible():
            ball.card_window.refresh_always_show_layout()
    main_window.card_always_show_changed.connect(_on_card_always_show_changed)

    # 8. 主窗口临时素材上限变更 → 更新管理器并清理过期素材
    def _on_asset_limits_changed(max_count: int, max_days: int, max_file_mb: int):
        temp_asset_manager.update_limits(
            max_assets=max_count, max_days=max_days, max_file_mb=max_file_mb)
        # 清理后刷新大小窗口的素材页
        main_window.refresh_temp_assets()
        ball.card_window.notify_assets_changed()
        get_logger().info(
            f"临时素材上限已更新: max_count={max_count}, max_days={max_days}, "
            f"max_file_mb={max_file_mb}")
    main_window.asset_limits_changed.connect(_on_asset_limits_changed)

    # 8.5 主窗口动画速度档位变更 → 实时应用到悬浮球（统一缩放动画时长）
    main_window.anim_speed_changed.connect(ball.set_anim_speed)

    # 8.6 主窗口空闲吸边隐藏秒数变更 → 实时应用到悬浮球
    main_window.auto_hide_seconds_changed.connect(ball.set_auto_hide_seconds)

    # 8.6b 主窗口自动隐藏总开关变更 → 实时应用到悬浮球（关闭时停表并把半隐藏的球滑回屏内）
    main_window.auto_hide_enabled_changed.connect(ball.set_auto_hide_enabled)

    # 8.7 主窗口悬浮球大小变更 → 实时应用到悬浮球（保持球心不动，位置即落盘）
    main_window.ball_size_changed.connect(ball.apply_ball_size)

    # 8.7b 主窗口小卡片图标大小变更 → 实时应用到小卡片软件导航页
    main_window.mini_icon_size_changed.connect(
        ball.card_window.apply_icon_size)

    # 8.8 主窗口全屏让位开关变更 → 启停全屏检测
    main_window.hide_on_fullscreen_changed.connect(
        lambda _enabled: _apply_fullscreen_watch())

    # ---- UI 心跳看门狗：事件循环阻塞 >800ms 时记录（诊断卡顿/未响应）----
    _mark("最后准备")
    # QTimer 在主线程事件循环里调度；循环被长任务阻塞时下一跳会迟到，
    # 相邻两跳的间隔 = 实际阻塞时长。平时零输出，只在真卡顿时留痕。
    _hb_state = {"last": time.monotonic()}

    def _ui_heartbeat():
        now = time.monotonic()
        gap = (now - _hb_state["last"]) * 1000
        _hb_state["last"] = now
        if gap > 800:
            get_logger().warning(
                f"[UI心跳] 事件循环阻塞 {gap:.0f}ms（本条在阻塞结束后补记）")

    _hb_timer = QTimer()
    _hb_timer.setInterval(250)
    _hb_timer.timeout.connect(_ui_heartbeat)
    _hb_timer.start()

    # ---- 启动时直接显示主窗口 ----
    main_window.show()
    _mark("显示主窗口")
    get_logger().info("主窗口已显示，进入事件循环")
    # 启动收尾：结算最后一段 → 总耗时日志 → 闪屏淡出
    now = time.monotonic()
    logger.info(f"[启动] 全部完成 总计 {now - _boot['t0']:.2f}s")
    splash.finish()

    # ---- 3.4 首启引导：首次使用时弹出三步欢迎向导（设置页「🚀 启动与
    # 系统 → 🔄 重看引导」可重看，同一 dialog）----
    # 延迟 800ms：不抢启动闪屏淡出的风头，主窗口先完整露脸。
    from src import onboarding
    _onboarding_ref = {"dlg": None}

    def _show_onboarding():
        # 防重入：已开着（设置页路径）就置前，不再叠一个
        existing = _onboarding_ref["dlg"]
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dlg = onboarding.WelcomeDialog(host=main_window, parent=main_window)
        _onboarding_ref["dlg"] = dlg
        dlg.exec()
        # 关闭即落盘：完成 / Esc / 跳过 / 标题栏 × 任何路径都置 True，
        # 之后不再骚扰（设置页「重看引导」路径在 settings_panel 里
        # 经 finished 信号走同一个 mark_done，闭环同源）
        onboarding.mark_done(config_manager)
        dlg.deleteLater()
        _onboarding_ref["dlg"] = None

    if onboarding.should_show(config_manager):
        QTimer.singleShot(800, _show_onboarding)

    # ---- 唤醒信号：第二个实例启动时通过命名事件唤醒本实例 ----
    # 使用后台线程阻塞等待命名事件（事件驱动），替代 300ms 持续轮询，
    # 消除内存常驻时的空闲 CPU 占用（任务 6.1）。
    def _on_wakeup_signal():
        """收到唤醒信号 → 显示悬浮球和主窗口（在主线程执行）"""
        get_logger().info("收到唤醒信号，显示窗口")
        # 显示悬浮球（可能被用户隐藏了）
        ball.setVisible(True)
        # 显示主窗口
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()

    _wakeup_stop = {"flag": False}

    class _WakeupBridge(QObject):
        """跨线程信号桥：后台线程 emit → 自动排队（QueuedConnection）到主线程。

        后台线程里 QTimer.singleShot 永不触发（无线程事件循环驱动，
        2026-10-01 探针实测 FIRED=False）——第二实例唤醒曾因此静默失效，
        必须经主线程 QObject 的信号槽切回。
        """

        woke_up = pyqtSignal()    # ★ 信号必须是类属性（漏写=AttributeError）

        def show_windows(self):
            _on_wakeup_signal()

    _wakeup_bridge = _WakeupBridge()
    _wakeup_bridge.woke_up.connect(_wakeup_bridge.show_windows)

    def _wakeup_worker():
        """后台线程：阻塞等待唤醒事件，收到后经信号桥切回主线程处理"""
        while not _wakeup_stop["flag"]:
            if singleton.wait_for_signal(lambda: _wakeup_stop["flag"]):
                # 切回 Qt 主线程执行窗口显示
                _wakeup_bridge.woke_up.emit()

    _wakeup_thread = threading.Thread(target=_wakeup_worker, daemon=True)
    _wakeup_thread.start()

    # ---- 退出诊断日志 ----
    def _on_about_to_quit():
        # 正常退出标记：必须排在所有退出日志之前（见 mark_session_end），
        # 与启动标记圈出本次会话的日志区间，供人工排障
        mark_session_end()
        get_logger().info("程序准备退出（aboutToQuit 信号触发）")
        # 显式收尾（1.4，幂等）：若 _safe_quit 已收尾过则直接跳过
        try:
            _shutdown_resources()
        except Exception:
            pass
        # 通知唤醒后台线程停止（避免退出后仍阻塞等待）
        try:
            _wakeup_stop["flag"] = True
        except Exception:
            pass
        # 强制落盘所有未决碎片变更（去抖窗口内可能仍有待写数据）
        try:
            if fragment_manager is not None:
                fragment_manager.flush()
        except Exception:
            pass
        # 强制落盘未决的笔记编辑（防抖窗口内可能仍有待写数据）
        try:
            main_window.flush_pending_notes()
        except Exception:
            pass
        # 立即落盘悬浮球位置（C4）：兜底 600ms 防抖窗口内尚未写入的位置
        try:
            ball.save_position_now()
        except Exception:
            pass
        # 立即落盘主窗口几何：同样是兜底防抖窗口（窗口在托盘态时不可见）
        try:
            _save_geom = getattr(main_window, "save_geometry_now", None)
            if _save_geom is not None:
                _save_geom()
        except Exception:
            pass
    app.aboutToQuit.connect(_on_about_to_quit)

    # ---- 进入事件循环 ----
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
