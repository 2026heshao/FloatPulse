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
  5. 右键悬浮球 / 右键卡片 / 按 Esc 键 → 退出程序
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
  - main                : 程序入口（本文件）
====================================================================
"""

import sys
import os
import struct
import traceback
import json
import logging
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QMessageBox,
    QGraphicsDropShadowEffect, QSystemTrayIcon,
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QTimer, QPropertyAnimation, QEasingCurve,
    QRectF, QSequentialAnimationGroup, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QFont, QFontMetrics, QPen, QAction, QCursor,
    QShortcut, QKeySequence, QIcon, QPixmap, QPainterPath, QRadialGradient,
)

# 引入独立模块
from src.single_instance import SingleInstance
from src.card_window import CardWindow
from src.app_paths import get_screen_geometry
from src.task_manager import (
    TaskManager, task_state, STATE_TODAY, STATE_OVERDUE,
)
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.docx_manager import DocxManager
from src.config import ConfigManager
from src.nav_manager import NavManager
from src.main_window import MainWindow
from src.theme import get_menu_qss
from src.controls import ScreenToast
from src.constants import sanitize_filename, DEFAULT_THEME


# ====================================================================
# 启动时数据完整性检查：扫描所有 JSON 文件，损坏的记录到日志
# ====================================================================
def _check_data_integrity(data_dir: str, logger):
    """
    启动时扫描 data/ 目录下所有 JSON 文件，检测损坏。
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
                f"详情请查看日志：data/app.log"
            )
        logger.warning(f"启动检查完成：{len(corrupted)} 个文件损坏已重置")
    else:
        logger.info("启动检查完成：所有 JSON 文件完整")


# ====================================================================
# 全局异常钩子：未捕获异常时显示对话框，避免静默崩溃
# ====================================================================
def _install_global_excepthook():
    """安装全局异常钩子，未捕获异常弹窗提示而非静默崩溃"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.exit(0)
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        # 打印到 stderr 便于调试
        try:
            sys.stderr.write(msg)
        except Exception:
            pass
        # 尝试弹窗提示（若 QApplication 已存在）
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "程序异常",
                    f"程序发生未捕获异常：\n\n{msg[-1500:]}"
                )
        except Exception:
            pass
    sys.excepthook = _hook


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
        icon_path = _find_icon_file()
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
        self._paint_ball(painter, cx, cy, vis_r)
        self._paint_badge(painter, cx, cy, vis_r)
        painter.end()

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
        self._theme = (config_manager.get("theme", DEFAULT_THEME)
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
        """右键菜单：打开主窗口 / 退出程序"""
        self._menu = QMenu(self)
        self._menu.setStyleSheet(get_menu_qss(self._theme))

        # 打开主窗口
        open_main_action = QAction("🖥  打开主窗口", self._menu)
        open_main_action.triggered.connect(self._open_main_window)
        self._menu.addAction(open_main_action)

        self._menu.addSeparator()

        # 退出程序
        exit_action = QAction("退出程序", self._menu)
        exit_action.triggered.connect(self._request_quit)
        self._menu.addAction(exit_action)

    def _open_main_window(self):
        """打开大窗口主UI"""
        if self._main_window is not None:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()

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
        # 回退：统一走项目根（打包运行时为 exe 目录）
        from src.app_paths import get_base_dir
        base = get_base_dir()
        tmp_dir = os.path.join(base, "temp_assets")
        os.makedirs(tmp_dir, exist_ok=True)
        return tmp_dir

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
                if self._temp_asset_manager.add_asset(path) > 0:
                    added_assets += 1
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
                    if self._temp_asset_manager.add_asset(saved_path) > 0:
                        added_assets += 1
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
                    if self._temp_asset_manager.add_asset(saved_path) > 0:
                        added_assets += 1
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
                        if self._temp_asset_manager.add_asset(saved_path) > 0:
                            added_assets += 1
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
                self._main_window._refresh_page(7)
        # 同步刷新小卡片软件页（仅可见且停留在软件页时重建；
        # 隐藏时无需处理——每次切入 app 页都会重读 config）
        if (added_apps > 0 and self._card_window is not None
                and self._card_window.isVisible()
                and self._card_window._last_mode == "app"):
            self._card_window._refresh_app_page()

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
            self._show_toast(f"已收录 {added_assets} 个素材")

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
            w._last_mode = "card"     # 让 popup_near 直接落在卡片页
            w.popup_near(self._ball_visual_rect())
        else:
            if w._last_mode != "card":
                w._switch_mode("card")
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
    def _show_card_on_hover(self):
        if not self._card_window.isVisible():
            if not self._card_window.has_shown_content():
                self._card_window.show_next_random()
            self._card_window.popup_near(self._ball_visual_rect())
        self._out_count = 0
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

        # 看门狗自愈：常驻每秒巡检一次，发现「卡片可见但 hover 检测已停」
        # 即重新拉起（见 _card_watchdog_tick），兜住偶发时序导致的卡片滞留
        self._card_watchdog = QTimer(self)
        self._card_watchdog.setInterval(1000)
        self._card_watchdog.timeout.connect(self._card_watchdog_tick)
        self._card_watchdog.start()

        self._idle_hide_timer = QTimer(self)
        self._idle_hide_timer.setSingleShot(True)
        # 自动隐藏秒数从配置读取
        self._apply_auto_hide_seconds()
        self._idle_hide_timer.timeout.connect(self._on_idle_hide_timeout)

        # 位置落盘防抖（C4）：拖动结束后延迟写入，拖动过程中不写盘
        self._pos_save_timer = QTimer(self)
        self._pos_save_timer.setSingleShot(True)
        self._pos_save_timer.setInterval(self.POS_SAVE_DELAY)
        self._pos_save_timer.timeout.connect(self._save_position)

    def _apply_auto_hide_seconds(self):
        """从配置读取空闲吸边隐藏秒数应用到定时器（启动时调用）"""
        seconds = (self._config.get("auto_hide_seconds", 3)
                   if self._config else 3)
        if hasattr(self, '_idle_hide_timer') and self._idle_hide_timer is not None:
            self._idle_hide_timer.setInterval(int(seconds) * 1000)

    def set_auto_hide_seconds(self, seconds: int):
        """外部（设置页）实时更新空闲吸边隐藏秒数。若定时器正在计时则用新间隔重启。"""
        if hasattr(self, '_idle_hide_timer') and self._idle_hide_timer is not None:
            self._idle_hide_timer.setInterval(max(1, int(seconds)) * 1000)
            if self._idle_hide_timer.isActive():
                self._idle_hide_timer.start()  # 重启以应用新计时

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
def _get_base_dir() -> str:
    """获取程序根目录。

    统一委托 src.app_paths.get_base_dir()：
      - 打包运行 → exe 所在目录
      - 源码运行 → 项目根目录（v1_baseline / v2 / shared 的公共父目录），
        使 v1 与 v2 共用同一份 data/、知识库.docx、temp_assets/
    """
    from src.app_paths import get_base_dir
    return get_base_dir()


def _find_icon_file() -> str:
    """查找图标文件（统一委托 src.app_paths.find_icon_file）。

    源码运行时优先在项目根 shared/assets/ 下查找，
    打包后依次尝试 _internal、exe 同级、exe 父目录，png 作为备用。
    """
    from src.app_paths import find_icon_file
    return find_icon_file()


def main():
    # ---- 安装全局异常钩子：未捕获异常弹窗提示而非静默崩溃 ----
    _install_global_excepthook()

    app = QApplication(sys.argv)
    # 禁用"最后一个窗口关闭时自动退出"——悬浮球/主窗口可能同时隐藏，
    # 程序应保持后台运行，仅通过显式退出（右键/Esc/closeEvent）退出
    app.setQuitOnLastWindowClosed(False)

    # ---- 设置程序图标（影响任务栏和窗口标题栏图标）----
    _icon = QIcon()
    _icon_path = _find_icon_file()
    if _icon_path:
        _icon = QIcon(_icon_path)
        app.setWindowIcon(_icon)

    # ---- 系统托盘图标（任务栏通知区域）----
    # 点击托盘图标：悬浮球隐藏时显示，可见时隐藏（不退出程序）
    _tray_icon = QSystemTrayIcon()
    _tray_icon.setIcon(_icon)
    _tray_icon.setToolTip("生活悬浮球")
    _tray_icon.setVisible(True)

    # ---- 单实例检测 ----
    singleton = SingleInstance()
    if not singleton.acquire():
        # 已有实例在运行 → 发送唤醒信号让首个实例显示窗口，然后静默退出
        SingleInstance.signal_show()
        sys.exit(0)

    # 首个实例：创建命名事件，用于接收后续实例的唤醒信号
    singleton.create_event()

    # ---- 定位数据文件 ----
    base_dir = _get_base_dir()
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    docx_path = os.path.join(base_dir, "知识库.docx")
    schedule_path = os.path.join(data_dir, "schedule.json")
    notes_path = os.path.join(data_dir, "notes.json")
    fragments_path = os.path.join(data_dir, "fragments.json")
    docx_meta_path = os.path.join(data_dir, "docx_meta.json")
    config_path = os.path.join(data_dir, "config.json")

    # ---- 初始化日志系统（自动创建 data/app.log）----
    from src.logger import init_logger, install_excepthook, get_logger
    logger = init_logger(base_dir, level=logging.INFO)
    install_excepthook()  # 替换全局异常钩子为带日志记录的版本
    logger.info("=" * 50)
    logger.info("程序启动")
    logger.info(f"base_dir = {base_dir}")
    logger.info(f"data_dir = {data_dir}")

    # ---- 配置管理器 ----
    config_manager = ConfigManager(config_path)
    logger.info("配置管理器初始化完成")

    # ---- 启动时数据完整性检查 ----
    _check_data_integrity(data_dir, logger)

    # ---- docx 管理器 ----
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
    task_manager = TaskManager(schedule_path)
    note_manager = NoteManager(notes_path)
    fragment_manager = FragmentManager(fragments_path)

    # ---- 临时素材管理器（拖图片/文件到悬浮球时复制保存）----
    from src.temp_asset_manager import TempAssetManager
    temp_asset_manager = TempAssetManager(
        base_dir,
        max_assets=config_manager.get("temp_asset_max_count", 50),
        max_days=config_manager.get("temp_asset_max_days", 30),
    )

    # ---- 网址导航管理器 ----
    nav_manager = NavManager(os.path.join(data_dir, "nav.json"))

    # ---- 剪贴板监听 ----
    # temp_asset_manager 一并注入：剪贴板里的图片直接进素材池（Y2）
    clipboard_monitor = ClipboardMonitor(
        fragment_manager, config_manager, temp_asset_manager)
    clipboard_monitor.start()

    # ---- 大窗口主UI ----
    main_window = MainWindow(
        task_manager, note_manager, fragment_manager,
        docx_manager, config_manager, clipboard_monitor,
        temp_asset_manager, nav_manager
    )

    # ---- 悬浮球 ----
    ball = FloatingBall(
        cards, task_manager, note_manager,
        fragment_manager, docx_manager, config_manager,
        clipboard_monitor, main_window, temp_asset_manager
    )
    # 根据配置决定悬浮球是否显示（默认显示）
    if config_manager.get("ball_visible", True):
        ball.show()

    # ---- 全屏应用检测（B8）：全屏时自动让位，退出全屏恢复 ----
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
        """前台全屏应用出现/退出 → 悬浮球自动让位/恢复"""
        ball.set_fullscreen_hidden(is_fs)
        get_logger().info(f"全屏检测：{'进入全屏，悬浮球让位' if is_fs else '退出全屏，悬浮球恢复'}")

    fs_watcher.fullscreen_changed.connect(_on_fullscreen_changed)
    _apply_fullscreen_watch()

    # ---- 托盘图标点击 → 切换主窗口显示/隐藏（不退出程序）----
    def _on_tray_activated(reason):
        # 只响应单击（左键点击），忽略双击/右键
        get_logger().info(f"托盘点击: reason={reason}, 主窗口可见={main_window.isVisible()}")
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if main_window.isVisible():
                main_window.hide()
                get_logger().info("托盘点击 → 隐藏主窗口")
            else:
                main_window.show()
                main_window.raise_()
                main_window.activateWindow()
                get_logger().info("托盘点击 → 显示主窗口")
    _tray_icon.activated.connect(_on_tray_activated)

    # ==================================================================
    # 信号槽桥梁：大小窗口数据双向同步
    # ==================================================================
    # 0. 注入 nav_manager / config_manager / asset_manager / fragment_manager 到小卡片
    ball._card_window.set_nav_manager(nav_manager)
    ball._card_window.set_config_manager(config_manager)
    ball._card_window.set_asset_manager(temp_asset_manager)
    ball._card_window.set_fragment_manager(fragment_manager)

    # 安全退出函数：重置卡片状态为默认首页，再退出程序
    def _safe_quit():
        # 重置卡片窗口状态为默认首页
        ball._card_window._last_mode = "fragment"
        ball._card_window._switch_mode("fragment")
        main_window._allow_close = True
        QApplication.quit()

    # 全局 ESC 快捷键：通过安全退出程序
    quit_shortcut = QShortcut(QKeySequence("Escape"), app)
    quit_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    quit_shortcut.activated.connect(_safe_quit)

    # ---- 托盘右键菜单（F1）：显示/隐藏主窗口、显示/隐藏悬浮球、退出程序 ----
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

    _tray_menu = QMenu()
    _act_main = _tray_menu.addAction("显示 / 隐藏主窗口")
    _act_ball = _tray_menu.addAction("显示 / 隐藏悬浮球")
    _tray_menu.addSeparator()
    _act_quit = _tray_menu.addAction("退出程序")
    _act_main.triggered.connect(_toggle_main_window)
    _act_ball.triggered.connect(_toggle_ball_visibility)
    _act_quit.triggered.connect(_safe_quit)
    _tray_icon.setContextMenu(_tray_menu)

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
        """扫描今日到期与逾期未完成任务，有则托盘气泡提醒。

        口径与任务页 / 小卡片 / 球体徽标**完全一致**（统一走 task_state）：
        脏日期解析失败 → 视为无日期，不计入。
        """
        ball.refresh_badge()   # 顺带刷新球体徽标（跨天后"今日到期"口径会变）
        if not config_manager.get("task_reminder_enabled", True):
            return
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        due, overdue = [], []
        for t in task_manager.get_all_tasks():
            if t.done:
                continue
            state, _delta = task_state(t.deadline, today)
            if state == STATE_TODAY:
                due.append(t)
            elif state == STATE_OVERDUE:
                overdue.append(t)
        if not due and not overdue:
            return
        lines = []
        if due:
            lines.append("【今日到期】")
            lines.extend(f"· {t.title}" for t in due[:5])
        if overdue:
            lines.append("【已逾期】")
            lines.extend(f"· {t.title}" for t in overdue[:5])
        total = len(due) + len(overdue)
        _tray_icon.showMessage(
            f"任务提醒（{total} 项待处理）",
            "\n".join(lines),
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )
        get_logger().info(f"任务提醒已弹出：今日到期 {len(due)}，逾期 {len(overdue)}")

    def _schedule_daily_reminder():
        """每日 9:00 检查一次（自循环重排）"""
        _check_task_reminders()
        QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)

    def _on_message_clicked():
        """点击提醒气泡 → 显示主窗口并切到任务页"""
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        main_window._switch_page(1)
    _tray_icon.messageClicked.connect(_on_message_clicked)

    QTimer.singleShot(4000, _check_task_reminders)          # 启动 4 秒后首次检查
    QTimer.singleShot(_msecs_until_next(9), _schedule_daily_reminder)  # 之后每天 9:00

    # ---- 全局快速捕捉条（热键呼出 → 一句话进碎片池）----
    from src.global_hotkey import GlobalHotkeyManager
    from src.quick_capture import QuickCaptureWindow

    hotkey_mgr = GlobalHotkeyManager()
    app.eventDispatcher().installNativeEventFilter(hotkey_mgr)

    quick_capture = QuickCaptureWindow(fragment_manager, theme=config_manager.get("theme", "light"), config_manager=config_manager)

    def _apply_quick_capture():
        """按当前配置重注册快速捕捉热键（开关/热键串变更/恢复默认时调用）"""
        hotkey_mgr.unregister_all()
        quick_capture.hide()
        if not config_manager.get("quick_capture_enabled", True):
            return
        hotkey_text = config_manager.get("quick_capture_hotkey", "Ctrl+Alt+K")
        if not hotkey_mgr.register(hotkey_text, quick_capture.toggle):
            get_logger().warning(f"全局热键注册失败（可能被占用）：{hotkey_text}")

    main_window.quick_capture_changed.connect(_apply_quick_capture)
    main_window.theme_changed.connect(quick_capture.apply_theme)
    # 快速捕捉提交成功 → 球体脉冲反馈（A4）
    quick_capture.capture_submitted.connect(lambda _text: ball.pulse())
    _apply_quick_capture()

    # 1. 小卡片退出请求 → 安全退出程序
    ball._card_window.request_quit.connect(_safe_quit)

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
    ball._card_window.data_changed.connect(_on_card_data_changed)

    # 3. 大窗口主题切换 → 悬浮球 + 小卡片应用主题
    main_window.theme_changed.connect(ball.apply_theme)

    # 4. 剪贴板新增碎片 → 大窗口刷新碎片页面（若可见）+ 球体脉冲反馈
    def _on_fragment_added(_content=None):
        main_window.refresh_fragments()
        ball.pulse()                  # 成功反馈（A4）
    clipboard_monitor.fragment_added.connect(_on_fragment_added)

    # 4b. 剪贴板图片入库（Y2）→ 刷新素材页面 + 球体脉冲 + 轻提示
    def _on_clipboard_image(_asset_id=None):
        main_window.refresh_temp_assets()
        ball._card_window.notify_assets_changed()
        main_window.show_toast("🖼 剪贴板图片已存入素材池（素材页可查看）")
        ball.pulse()
    if getattr(clipboard_monitor, "image_captured", None) is not None:
        clipboard_monitor.image_captured.connect(_on_clipboard_image)

    # 5. 大窗口数据变更 → 小卡片刷新
    def _on_main_data_changed(kind):
        if kind == "task":
            ball.refresh_badge()      # 大窗口任务变更 → 球体徽标同步（A4）
            if ball._card_window.isVisible():
                ball._card_window._refresh_task_list()
        elif kind == "knowledge":
            # 知识库编辑后重新加载卡片并同步到小卡片
            new_cards = docx_manager.get_cards()
            ball.update_cards(new_cards)
        elif kind == "nav" and ball._card_window.isVisible():
            # 网址导航编辑后刷新小卡片导航页
            ball._card_window._refresh_nav_page()
        elif kind == "asset":
            # 临时素材变更后刷新小卡片素材页（可见立即重建，隐藏则置脏）
            ball._card_window.notify_assets_changed()
        elif kind == "fragment" and ball._card_window.isVisible():
            # 碎片变更后刷新小卡片碎片页
            ball._card_window._refresh_fragment_page()
    main_window.data_changed.connect(_on_main_data_changed)

    # 6. 主窗口悬浮球开关 → 显示/隐藏悬浮球
    main_window.ball_visibility_changed.connect(
        lambda visible: ball.setVisible(visible)
    )

    # 7. 主窗口小卡片保持显示开关 → 实时应用并刷新卡片关闭按钮可见性
    def _on_card_always_show_changed(always_show: bool):
        # 配置已由 main_window 保存；这里刷新关闭按钮与内容净空（两者必须一起变，
        # 否则按钮出现了、内容却没让位，又会压住首行）
        if ball._card_window.isVisible():
            ball._card_window.refresh_always_show_layout()
    main_window.card_always_show_changed.connect(_on_card_always_show_changed)

    # 8. 主窗口临时素材上限变更 → 更新管理器并清理过期素材
    def _on_asset_limits_changed(max_count: int, max_days: int):
        temp_asset_manager.update_limits(max_assets=max_count, max_days=max_days)
        # 清理后刷新大小窗口的素材页
        main_window.refresh_temp_assets()
        ball._card_window.notify_assets_changed()
        get_logger().info(f"临时素材上限已更新: max_count={max_count}, max_days={max_days}")
    main_window.asset_limits_changed.connect(_on_asset_limits_changed)

    # 8.5 主窗口动画速度档位变更 → 实时应用到悬浮球（统一缩放动画时长）
    main_window.anim_speed_changed.connect(ball.set_anim_speed)

    # 8.6 主窗口空闲吸边隐藏秒数变更 → 实时应用到悬浮球
    main_window.auto_hide_seconds_changed.connect(ball.set_auto_hide_seconds)

    # 8.7 主窗口悬浮球大小变更 → 实时应用到悬浮球（保持球心不动，位置即落盘）
    main_window.ball_size_changed.connect(ball.apply_ball_size)

    # 8.8 主窗口全屏让位开关变更 → 启停全屏检测
    main_window.hide_on_fullscreen_changed.connect(
        lambda _enabled: _apply_fullscreen_watch())

    # ---- 启动时直接显示主窗口 ----
    main_window.show()
    get_logger().info("主窗口已显示，进入事件循环")

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

    def _wakeup_worker():
        """后台线程：阻塞等待唤醒事件，收到后切回主线程处理"""
        while not _wakeup_stop["flag"]:
            if singleton.wait_for_signal(lambda: _wakeup_stop["flag"]):
                # 切回 Qt 主线程执行窗口显示
                QTimer.singleShot(0, _on_wakeup_signal)

    _wakeup_thread = threading.Thread(target=_wakeup_worker, daemon=True)
    _wakeup_thread.start()

    # ---- 退出诊断日志 ----
    def _on_about_to_quit():
        get_logger().info("程序准备退出（aboutToQuit 信号触发）")
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
            _panel = getattr(main_window, '_page_notes', None)
            if _panel is not None:
                _panel.flush_pending_save()
        except Exception:
            pass
        # 立即落盘悬浮球位置（C4）：兜底 600ms 防抖窗口内尚未写入的位置
        try:
            ball.save_position_now()
        except Exception:
            pass
    app.aboutToQuit.connect(_on_about_to_quit)

    # ---- 进入事件循环 ----
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
