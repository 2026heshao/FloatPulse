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
from PyQt6.QtCore import Qt, QPoint, QPointF, QTimer, QPropertyAnimation, QEasingCurve, QRectF, pyqtProperty, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QFont, QAction, QCursor,
    QShortcut, QKeySequence, QIcon, QPixmap, QPainterPath,
)

# 引入独立模块
from src.single_instance import SingleInstance
from src.card_window import CardWindow
from src.app_paths import get_screen_geometry
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.docx_manager import DocxManager
from src.config import ConfigManager
from src.nav_manager import NavManager
from src.main_window import MainWindow
from src.theme import get_menu_qss
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
# 模块：Toast 气泡提示
# ====================================================================
class _ToastLabel(QWidget):
    """悬浮球上方弹出的气泡提示，自动消失"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(False)
        # 不拦截鼠标事件，让鼠标穿透到下层
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self._text = ""
        self._fade_anim = None

    def show_text(self, text: str, duration_ms: int = 1500):
        """显示提示文字，duration_ms 后自动消失"""
        self._text = text
        self._timer.stop()

        # 计算尺寸
        font = QFont("Microsoft YaHei", 12)
        fm = self.fontMetrics() if self.font() else None
        # 用临时 QPainter 测量文字宽度
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        pad_x = 16
        pad_y = 10
        w = text_w + pad_x * 2
        h = fm.height() + pad_y * 2
        w = max(w, 80)

        self.setFixedSize(w, h)

        # 定位到父窗口（悬浮球）正上方
        parent = self.parentWidget()
        if parent:
            px = parent.pos().x() + (parent.width() - w) // 2
            py = parent.pos().y() - h - 6
            self.move(px, py)

        # 淡入动画
        self._opacity = 0.0
        self._fade_anim = QPropertyAnimation(self, b"_toast_opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._fade_anim.start()

        self.show()

        # 停留一段时间后淡出
        self._timer.start(duration_ms)

    def _get_opacity(self) -> float:
        return getattr(self, '_opacity', 1.0)

    def _set_opacity(self, value: float):
        self._opacity = value
        self.update()

    toast_opacity = pyqtProperty(float, _get_opacity, _set_opacity)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        opacity = self._get_opacity()

        # 圆角矩形背景
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)

        bg = QColor(50, 50, 50, int(220 * opacity))
        painter.setBrush(QBrush(bg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 8, 8)

        # 文字
        painter.setPen(QColor(255, 255, 255, int(240 * opacity)))
        font = QFont("Microsoft YaHei", 12)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


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
        """球体绘制：ico 图标完全覆盖球体（不再手绘玻璃球样式）。
        仅保留外阴影（悬停/拖拽时增强），球体本身即图标。
        """
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        cx = w / 2.0
        cy = h / 2.0
        ball_r = self._ball_size / 2.0
        scale = self._scale
        glow = self._glow
        vis_r = ball_r * scale          # 球体实际绘制半径（浮点，不 round）

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # --- 外阴影：多层同心椭圆模拟模糊，随 glow 增强（偏移变大、范围变宽）---
        off_y = 2.5 + (8.0 - 2.5) * glow
        outer = vis_r * (1.0 + (0.18 + 0.37 * glow))
        outer = min(outer, w / 2.0 - 3.0)
        rings = int(round(3.0 + 3.0 * glow))    # 3~6 层
        painter.setPen(Qt.PenStyle.NoPen)
        for j in range(1, rings + 1):
            t = j / rings
            r = vis_r + (outer - vis_r) * t
            alpha = int(46.0 * (1.0 - 0.6 * t))
            painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
            painter.drawEllipse(QRectF(cx - r, cy - r + off_y, r * 2.0, r * 2.0))

        # --- 球体：ico 图标铺满整个球体，完全覆盖手绘样式 ---
        self._load_pixmap()
        if self._pixmap:
            icon_side = vis_r * 2.0
            painter.drawPixmap(
                QRectF(cx - vis_r, cy - vis_r, icon_side, icon_side),
                self._pixmap,
                QRectF(self._pixmap.rect()),
            )
        else:
            # 降级：图标不存在时绘制灯泡 emoji
            painter.setPen(QColor(255, 255, 255, 235))
            font = QFont("Microsoft YaHei", 15, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(
                QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "💡"
            )
        painter.end()


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

    BALL_SIZE = 64                # 悬浮球球体直径
    SHADOW_MARGIN = 24            # 宿主窗口预留画布边距（容纳放大球体+增强投影）
    HOST_SIZE = BALL_SIZE + SHADOW_MARGIN * 2   # 宿主窗口边长
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
        self._toast = None               # Toast 提示标签
        self._hovered = False
        self._hidden_to_edge = False
        self._edge_side = None
        self._anim = None                # 宿主位移动画（吸边/滑出）
        self._out_count = 0

        self._init_window()
        self._init_card_window()
        self._init_context_menu()
        self._init_hover_timer()

        # 接受文件拖拽
        self.setAcceptDrops(True)

        # 启动位置恢复：读取上次保存位置，无记录则放主屏左缘垂直居中
        self.move(self._resolve_initial_position())

    # ---------------- 初始化 ----------------
    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedSize(self.HOST_SIZE, self.HOST_SIZE)
        # 注意：不使用 QGraphicsDropShadowEffect，
        # 它与 WA_TranslucentBackground 在 Windows 上会导致
        # UpdateLayeredWindowIndirect 报错。阴影改由子控件手动绘制。
        # 子绘制控件铺满宿主，负责视觉与动画；本宿主只做交互控制。
        self._surface = _BallSurface(self.BALL_SIZE, self, theme=self._theme)
        self._surface.setGeometry(0, 0, self.HOST_SIZE, self.HOST_SIZE)

    def _init_card_window(self):
        self._card_window = CardWindow(theme=self._theme)
        self._card_window.set_cards(self._cards)
        if self._task_manager:
            self._card_window.set_task_manager(self._task_manager)
        if self._note_manager:
            self._card_window.set_note_manager(self._note_manager)
        # 卡片拖动时悬浮球同步跟随，保持二者相对位置
        self._card_window.card_moved.connect(self._on_card_moved)

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

        for path in local_files:
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
                # 同步刷新小卡片素材页（若可见）
                if self._card_window.isVisible():
                    self._card_window._refresh_asset_page()
            if added_frags > 0:
                self._main_window.refresh_fragments()

        # Toast 提示
        if added_assets > 0:
            self._show_toast(f"已收录 {added_assets} 个素材")

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

    def _download_url(self, url: str) -> str:
        """尝试下载 HTTP URL 到临时文件，返回路径"""
        from datetime import datetime
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
        from PyQt6.QtCore import QEventLoop, QTimer, QUrl

        try:
            tmp_dir = self._get_temp_assets_dir()
            os.makedirs(tmp_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 从 URL 提取扩展名（支持 ? 参数前 + 无点号的情况）
            url_path = url.lower().split("?")[0]
            ext = ".png"
            for e in ["jpg", "jpeg", "png", "gif", "bmp", "webp",
                       "svg", "tiff", "ico"]:
                if e in url_path:
                    ext = "." + e
                    break
            path = os.path.join(tmp_dir, f"url_img_{ts}{ext}")

            # 用 QNetworkAccessManager 下载（更接近浏览器行为）
            manager = QNetworkAccessManager()
            request = QNetworkRequest()
            request.setUrl(QUrl(url))
            # 添加浏览器常用的请求头
            request.setRawHeader(b"User-Agent",
                b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            request.setRawHeader(b"Accept",
                b"image/webp,image/apng,image/*,*/*;q=0.8")
            request.setRawHeader(b"Referer", url.encode())

            reply = manager.get(request)
            loop = QEventLoop()
            reply.finished.connect(loop.quit)

            # 超时处理
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(15000)  # 15 秒超时

            loop.exec()

            if timer.isActive():
                timer.stop()
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    data = reply.readAll()
                    with open(path, "wb") as f:
                        f.write(data.data())
                    print(f"[DEBUG] Downloaded: {len(data)} bytes -> {path}")
                    reply.deleteLater()
                    return path
                else:
                    print(f"[DEBUG] Download failed: {reply.errorString()}")
            else:
                print(f"[DEBUG] Download timeout")

            reply.deleteLater()
            return ""
        except Exception as e:
            print(f"[DEBUG] Download exception: {e}")
            return ""

    # ---------------- 缩放 / 投影动画 ----------------
    def _dur(self, ms: int) -> int:
        """按动画速度档位缩放时长（档位越大越快，时长越短）"""
        return max(1, int(ms / self._anim_speed))

    def set_anim_speed(self, speed: float):
        """外部（设置页滑动条）实时更新动画速度档位"""
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
        return QPointF(self.pos().x() + self.HOST_SIZE / 2.0,
                       self.pos().y() + self.HOST_SIZE / 2.0)

    def _ball_visual_rect(self) -> QRectF:
        """球体可见圆的外接矩形（屏幕坐标），用于卡片定位与悬停检测"""
        c = self._ball_center()
        r = self.BALL_SIZE / 2.0
        return QRectF(c.x() - r, c.y() - r, r * 2.0, r * 2.0)

    def _ball_hit(self, global_pos) -> bool:
        """判断全局坐标点是否落在球体（含轻微手感余量）内"""
        lx = global_pos.x() - self.frameGeometry().x()
        ly = global_pos.y() - self.frameGeometry().y()
        dx = lx - self.HOST_SIZE / 2.0
        dy = ly - self.HOST_SIZE / 2.0
        r = self.BALL_SIZE / 2.0 + 6.0
        return dx * dx + dy * dy <= r * r

    # ---------------- 位置恢复 ----------------
    def _resolve_initial_position(self) -> QPoint:
        """启动位置：读取上次保存位置；无记录则放主屏左缘垂直居中；越界则夹回屏内"""
        screen = get_screen_geometry()
        S = self.HOST_SIZE
        default = QPoint(screen.left(), screen.top() + (screen.height() - S) // 2)
        saved = self._config.get("ball_position", None) if self._config else None
        if isinstance(saved, (list, tuple)) and len(saved) >= 2:
            x = max(screen.left(), min(int(saved[0]), screen.right() - S))
            y = max(screen.top(), min(int(saved[1]), screen.bottom() - S))
            return QPoint(x, y)
        return default

    def _save_position(self):
        """吸附结束后把最终位置写入配置，供下次启动恢复"""
        if not self._config:
            return
        p = self.pos()
        self._config.set("ball_position", [int(p.x()), int(p.y())])
        self._config.save()

    # ---------------- Toast 提示 ----------------
    def _show_toast(self, text: str, duration_ms: int = 1500):
        """在悬浮球上方弹出气泡提示，duration_ms 后自动消失"""
        if not hasattr(self, '_toast') or self._toast is None:
            self._toast = _ToastLabel(self)
        self._toast.show_text(text, duration_ms)

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

    def _move_drag_follow(self, gpos):
        """拖拽中窗口跟随鼠标（带橡皮筋越界衰减）"""
        raw = gpos - self._drag_offset
        screen = get_screen_geometry()
        S = self.HOST_SIZE
        x = self._rubber_axis(raw.x(), screen.left(), screen.right() - S)
        y = self._rubber_axis(raw.y(), screen.top(), screen.bottom() - S)
        self.move(QPoint(int(x), int(y)))

    def _rubber_axis(self, v, lo, hi):
        """界内原值、越界衰减 1/4，并允许少量越界（防止彻底被拉出屏幕）"""
        if v < lo:
            return max(lo - (lo - v) * self.RUBBER_DAMP,
                       lo - self.RUBBER_OVERRUN * self.HOST_SIZE)
        if v > hi:
            return min(hi + (v - hi) * self.RUBBER_DAMP,
                       hi + self.RUBBER_OVERRUN * self.HOST_SIZE)
        return v

    def _on_card_moved(self):
        """卡片拖动时悬浮球同步跟随，保持二者相对位置"""
        if self._card_window.isVisible():
            self.move(self._card_window.pos() - self._card_offset)

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

    def _snap_to_edge(self):
        """
        松手后立即吸附到最近的屏幕左/右边缘（以球心所在水平半区判断）。
        垂直位置保持不变并夹回可用区域。用 OutBack 产生先过冲再回落的弹性归位。
        """
        screen = get_screen_geometry()
        S = self.HOST_SIZE
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
            # 保存位置，并启动空闲隐藏计时——吸附到位后（松手时球仍在屏幕中央、
            # 计时未启动）在此重启，实现吸附后无需再点鼠标即自动半隐藏
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

    def _check_hover_state(self):
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
                self._card_window.hide()
                self._hover_check_timer.stop()
                self._out_count = 0

    # ---------------- 四向吸边隐藏 ----------------
    def _init_hover_timer(self):
        self._hover_check_timer = QTimer(self)
        self._hover_check_timer.setInterval(self.HOVER_CHECK_INTERVAL)
        self._hover_check_timer.timeout.connect(self._check_hover_state)

        self._idle_hide_timer = QTimer(self)
        self._idle_hide_timer.setSingleShot(True)
        # 自动隐藏秒数从配置读取
        self._apply_auto_hide_seconds()
        self._idle_hide_timer.timeout.connect(self._on_idle_hide_timeout)

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
        S = self.HOST_SIZE
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
        S = self.HOST_SIZE
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
        S = self.HOST_SIZE
        half = S / 2.0
        if self._edge_side == 'left':
            target = QPoint(int(screen.left() - half + self.BALL_SIZE / 2.0 + 2), self.y())
        elif self._edge_side == 'right':
            target = QPoint(int(screen.right() - half - self.BALL_SIZE / 2.0 - 2), self.y())
        elif self._edge_side == 'top':
            target = QPoint(self.x(), int(screen.top() - half + self.BALL_SIZE / 2.0 + 2))
        elif self._edge_side == 'bottom':
            target = QPoint(self.x(), int(screen.bottom() - half - self.BALL_SIZE / 2.0 - 2))
        else:
            return

        self._hidden_to_edge = False

        def _on_slide_out_finished():
            if not self._dragging and self._ball_hit(QCursor.pos()):
                self._show_card_on_hover()
            else:
                self._start_idle_hide_timer()

        self._animate_pos(target, 220, QEasingCurve.Type.OutCubic,
                          on_finished=_on_slide_out_finished)

    # ---------------- 公开接口 ----------------
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
    clipboard_monitor = ClipboardMonitor(fragment_manager, config_manager)
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

    # 1. 小卡片退出请求 → 安全退出程序
    ball._card_window.request_quit.connect(_safe_quit)

    # 1.1 悬浮球右键退出 → 安全退出程序
    ball.request_quit.connect(_safe_quit)

    # 2. 小卡片数据变更 → 大窗口刷新对应面板（若可见）
    def _on_card_data_changed(kind):
        if kind == "task":
            main_window.refresh_tasks()
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

    # 4. 剪贴板新增碎片 → 大窗口刷新碎片页面（若可见）
    clipboard_monitor.fragment_added.connect(
        lambda _: main_window.refresh_fragments()
    )

    # 5. 大窗口数据变更 → 小卡片刷新
    def _on_main_data_changed(kind):
        if kind == "task" and ball._card_window.isVisible():
            ball._card_window._refresh_task_list()
        elif kind == "knowledge":
            # 知识库编辑后重新加载卡片并同步到小卡片
            new_cards = docx_manager.get_cards()
            ball.update_cards(new_cards)
        elif kind == "nav" and ball._card_window.isVisible():
            # 网址导航编辑后刷新小卡片导航页
            ball._card_window._refresh_nav_page()
        elif kind == "asset" and ball._card_window.isVisible():
            # 临时素材变更后刷新小卡片素材页
            ball._card_window._refresh_asset_page()
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
        # 配置已由 main_window 保存，这里只需刷新小卡片关闭按钮的可见性
        if ball._card_window.isVisible():
            ball._card_window._close_btn.setVisible(always_show)
    main_window.card_always_show_changed.connect(_on_card_always_show_changed)

    # 8. 主窗口临时素材上限变更 → 更新管理器并清理过期素材
    def _on_asset_limits_changed(max_count: int, max_days: int):
        temp_asset_manager.update_limits(max_assets=max_count, max_days=max_days)
        # 清理后刷新大小窗口的素材页
        main_window.refresh_temp_assets()
        if ball._card_window.isVisible():
            ball._card_window._refresh_asset_page()
        get_logger().info(f"临时素材上限已更新: max_count={max_count}, max_days={max_days}")
    main_window.asset_limits_changed.connect(_on_asset_limits_changed)

    # 8.5 主窗口动画速度档位变更 → 实时应用到悬浮球（统一缩放动画时长）
    main_window.anim_speed_changed.connect(ball.set_anim_speed)

    # 8.6 主窗口空闲吸边隐藏秒数变更 → 实时应用到悬浮球
    main_window.auto_hide_seconds_changed.connect(ball.set_auto_hide_seconds)

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
    app.aboutToQuit.connect(_on_about_to_quit)

    # ---- 进入事件循环 ----
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
