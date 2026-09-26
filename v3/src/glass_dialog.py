# -*- coding: utf-8 -*-
"""
====================================================================
统一样式对话框基类  -  GlassDialog
====================================================================
解决"弹窗是系统原生外观、与主窗口风格割裂"的问题。

与主窗口保持一致的 4 个视觉要素：
  1. 无边框窗口 + WA_TranslucentBackground（圆角真正生效，无系统标题栏）
  2. GlassPanel 玻璃壳（半透明填充 + 顶部高光 + 双色描边 + 噪点）
  3. 外圈柔和阴影（paintEvent 手绘，不用 QGraphicsDropShadowEffect）
  4. 自绘标题栏（图标 + 标题 + 副标题 + 右侧关闭按钮，同主窗口标题栏样式）

复用主窗口 QSS（objectName 与主窗口一致：mainWindow / titleBar /
titleBarLabel / iconBtn / primaryBtn / secondaryBtn / hintLabel /
sectionLabel / glassCard / settingsSeparator），因此主题切换自动跟随。

用法：
    dlg = GlassDialog(host, title="碎片详情", size=(620, 520))
    dlg.body_layout.addWidget(...)          # 往内容区加控件
    dlg.add_footer([("📋 复制", "primaryBtn", on_copy),
                    ("关闭", "secondaryBtn", dlg.accept)])
    dlg.exec()
====================================================================
"""

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QIcon, QPainter
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from src.app_paths import find_icon_file
from src.glass import GlassPanel, draw_soft_shadow
from src.theme import DEFAULT_THEME, get_colors, get_main_window_qss


def make_separator() -> QFrame:
    """1px 细分隔线（复用主窗口设置页的分隔线样式）"""
    line = QFrame()
    line.setObjectName("settingsSeparator")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


def flash_button(button: QPushButton, text: str, ms: int = 1400):
    """按钮短暂显示反馈文案后还原（替代弹出一次性 MessageBox 打断操作）"""
    if getattr(button, "_flashing", False):
        return
    old_text = button.text()
    button._flashing = True
    button.setText(text)
    button.setEnabled(False)

    def _restore():
        button.setText(old_text)
        button.setEnabled(True)
        button._flashing = False

    QTimer.singleShot(ms, _restore)


def make_dialog_buttons(dialog: QDialog) -> QDialogButtonBox:
    """构造「保存 | 取消」按钮组并接好 accept/reject。

    各编辑对话框（碎片编辑 / 网址编辑 / 笔记改名 / 任务编辑）共用的样板，
    返回按钮组由调用方自行 addRow / addWidget。
    """
    btns = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    btns.accepted.connect(dialog.accept)
    btns.rejected.connect(dialog.reject)
    return btns


class GlassDialog(QDialog):
    """与主窗口同款玻璃风格的对话框基类"""

    SHADOW_MARGIN = 16          # 阴影留白边距
    RADIUS = 14                 # 圆角（与主窗口一致）
    TITLE_BAR_HEIGHT = 48       # 标题栏高度（与主窗口一致）
    DEFAULT_SIZE = (600, 500)

    def __init__(self, host=None, title: str = "", subtitle: str = "",
                 parent=None, size=None):
        super().__init__(parent)
        self._host = host
        self._drag_offset = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint   # 保持与旧详情弹窗一致的层级
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        w, h = size if size else self.DEFAULT_SIZE
        self.resize(int(w), int(h))
        self.setWindowTitle(title)          # 供任务栏/无障碍读取，不显示

        # ---- 玻璃壳 + 阴影边距 ----
        self._container = GlassPanel(self, radius=self.RADIUS)
        self._container.setObjectName("mainWindow")
        outer = QGridLayout(self)
        m = self.SHADOW_MARGIN
        outer.setContentsMargins(m, m, m, m)
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar(title, subtitle))

        # ---- 内容区（子类/调用方往 body_layout 里加控件）----
        self.body = QWidget()
        root.addWidget(self.body, 1)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 16, 20, 16)
        self.body_layout.setSpacing(10)

        self.apply_theme()

    # ---------------- 标题栏 ----------------
    def _build_title_bar(self, title: str, subtitle: str) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(self.TITLE_BAR_HEIGHT)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 12, 0)
        h.setSpacing(8)

        icon_path = find_icon_file()
        if icon_path:
            pm = QIcon(icon_path).pixmap(22, 22)
            if not pm.isNull():
                icon_label = QLabel()
                icon_label.setPixmap(pm)
                icon_label.setFixedSize(22, 22)
                icon_label.setScaledContents(True)
                h.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setObjectName("titleBarLabel")
        h.addWidget(title_label)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("titleBarSub")
            h.addWidget(sub)

        h.addStretch()

        close_btn = QPushButton("×")
        close_btn.setObjectName("iconBtn")
        close_btn.setProperty("danger", "true")
        close_btn.setToolTip("关闭")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        h.addWidget(close_btn)

        # 标题栏可拖动窗口：给整条栏及其文字标签安装事件过滤器
        self._drag_target = bar
        for w in (bar, title_label) + tuple(
                c for c in bar.children() if isinstance(c, QLabel)):
            w.installEventFilter(self)
        return bar

    # ---------------- 拖动 ----------------
    def eventFilter(self, obj, event):
        """标题栏按下拖动 → 移动窗口（无边框窗口必须自己实现）"""
        is_title_part = (obj is self._drag_target
                         or (isinstance(obj, QWidget)
                             and obj.parent() is self._drag_target))
        if is_title_part:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._drag_offset = (event.globalPosition().toPoint()
                                         - self.frameGeometry().topLeft())
                    return True
            elif et == QEvent.Type.MouseMove:
                if self._drag_offset is not None and \
                        (event.buttons() & Qt.MouseButton.LeftButton):
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    return True
            elif et == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(obj, event)

    # ---------------- 主题 ----------------
    def apply_theme(self):
        """复用主窗口 QSS + 玻璃壳配色，保证弹窗与主窗口观感一致

        ⚠ 子类若重写本方法：基类 ``__init__`` 末尾就会先调用一次，此时子类
        的控件尚未创建 → 重写里访问自身控件必须用 ``getattr(self, "x", None)``
        容错，否则会 AttributeError（全局搜索对话框踩过）。
        """
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        qss = ""
        host_container = getattr(self._host, "_container", None)
        if host_container is not None:
            qss = host_container.styleSheet()
        if not qss:
            qss = get_main_window_qss(theme)
        self._container.setStyleSheet(qss)
        self._container.apply_theme(get_colors(theme))

    # ---------------- 底部按钮区 ----------------
    def add_footer(self, buttons) -> list:
        """底部按钮栏：buttons = [(文案, objectName 或 None, 槽函数), ...]

        返回按钮列表（顺序与入参一致），便于调用方后续改文案/状态
        （如复制按钮临时显示"已复制"）。
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        created = []
        for text, obj_name, slot in buttons:
            btn = QPushButton(text)
            if obj_name:
                btn.setObjectName(obj_name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if slot is not None:
                btn.clicked.connect(slot)
            row.addWidget(btn)
            created.append(btn)
        self.body_layout.addLayout(row)
        return created

    # ---------------- 阴影 ----------------
    def paintEvent(self, event):
        """手绘外圈柔和阴影（与主窗口同款做法）"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.SHADOW_MARGIN
        base = QRectF(self.rect()).adjusted(m, m, -m, -m)
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        # 深色桌面下阴影需更重才有层次（与 GlassWindowRoot 的策略一致）
        alpha = 96 if theme == "dark" else 48
        draw_soft_shadow(painter, base, self.RADIUS,
                         layers=6, max_alpha=alpha, offset_y=5.0)
        painter.end()
        super().paintEvent(event)
