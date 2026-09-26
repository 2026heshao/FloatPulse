# -*- coding: utf-8 -*-
"""
====================================================================
全局快速捕捉条  -  QuickCaptureWindow
====================================================================
热键呼出的迷你输入框：一句话 → 碎片池，Esc 消失。
不开主窗口即可随手记一笔。

设计要点：
  1. 无边框 + 置顶 + Tool 提示窗（不在任务栏出现）
  2. 回车提交：fragment_manager.add_clipboard_text(content, source="快速捕捉")
  3. Esc 或失焦自动隐藏（Esc 被 QLineEdit 消费，需 eventFilter 拦截）
  4. 左端「⠿」手柄可拖动移动位置，拖动后位置落盘记住
  5. 显示位置：有上次拖动位置则恢复（夹取屏内），否则鼠标所在屏幕居中
  6. 样式跟随主题（apply_theme 由外部在主题切换时调用）
====================================================================
"""

from PyQt6.QtCore import Qt, QPoint, QRect, QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QCursor, QKeyEvent, QShortcut, QKeySequence
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QLabel

from src.logger import get_logger
from src.theme import get_colors


class QuickCaptureWindow(QWidget):
    """全局快速捕捉输入条"""

    capture_submitted = pyqtSignal(str)   # 回车提交时发射（内容文本）
    capture_hidden = pyqtSignal()         # 隐藏时发射（可用于提示）

    WIDTH = 560
    HEIGHT = 56

    def __init__(self, fragment_manager, theme: str = "dark", parent=None,
                 config_manager=None):
        super().__init__(parent)
        self._fm = fragment_manager
        self._theme = theme
        self._config = config_manager
        self._drag_offset = QPoint()      # 手柄拖动：全局点 → 窗口左上偏移
        self._drag_active = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool          # 不在任务栏显示
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # 左端拖动手柄：整条输入区是 QLineEdit（拖动=选中文本，不能占用）。
        # QLabel 自身忽略鼠标按下 → 事件上抛到窗口 mousePressEvent 统一处理，
        # 窗口随即 grabMouse() 独占后续移动（不依赖事件过滤器拦截语义）。
        self._handle = QLabel("\u280b")
        self._handle.setObjectName("qcDragHandle")
        self._handle.setFixedWidth(26)
        self._handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self._handle.setToolTip("按住拖动，移动输入条位置")
        layout.addWidget(self._handle)

        self._input = QLineEdit()
        self._input.setPlaceholderText("⚡ 快速捕捉：输入后回车存入碎片池，Esc 关闭")
        self._input.setFixedHeight(self.HEIGHT - 12)
        self._input.returnPressed.connect(self._submit)
        # Esc 由 QLineEdit 自行消费（窗口收不到 keyPressEvent），过滤器兜底拦截
        self._input.installEventFilter(self)
        layout.addWidget(self._input, 1)

        hint = QLabel("Enter ↵")
        hint.setObjectName("quickCaptureHint")
        layout.addWidget(hint)

        # Esc 关闭三重保险之一：窗口级 QShortcut（焦点在任意子控件上都触发）
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self._close_via_esc)

        self.apply_theme(self._theme)

    # ---------------- 显示 / 隐藏 ----------------
    def show_centered(self):
        """显示输入条：有上次拖动位置则恢复，否则鼠标所在屏幕居中"""
        get_logger().info("[快捕] show_centered 进入：config=%s", self._config is not None)
        if not self._restore_saved_pos():
            self._place_centered()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        get_logger().info("[快捕] 已显示 pos=(%d,%d) active=%s input_focus=%s",
                          self.x(), self.y(), self.isActiveWindow(), self._input.hasFocus())
        # 300ms 后复查焦点状态（验证 Windows 前台锁是否拦截了激活）
        QTimer.singleShot(300, self._log_focus_state)

    def _log_focus_state(self):
        if self.isVisible():
            get_logger().info("[快捕] 300ms复查: active=%s input_focus=%s",
                              self.isActiveWindow(), self._input.hasFocus())

    def _place_centered(self):
        """鼠标所在屏幕居中定位（多显示器友好）"""
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.WIDTH) // 2,
                geo.y() + (geo.height() - self.HEIGHT) // 3,
            )

    def _restore_saved_pos(self) -> bool:
        """恢复上次拖动保存的位置（校验在屏内并夹取到可见区）"""
        if self._config is None:
            get_logger().info("[快捕] 无配置管理器，跳过位置恢复")
            return False
        pos = self._config.get("quick_capture_pos", None)
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return False
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError):
            return False
        screen = QGuiApplication.screenAt(QPoint(x, y))
        if screen is None:
            get_logger().info("[快捕] 上次位置(%d,%d)不在任何屏幕，回退居中", x, y)
            return False          # 上次位置所在屏已拔掉 → 回退居中
        geo = screen.availableGeometry()
        x = max(geo.left(), min(x, geo.right() - self.WIDTH))
        y = max(geo.top(), min(y, geo.bottom() - self.HEIGHT))
        self.move(x, y)
        get_logger().info("[快捕] 恢复上次位置 (%d,%d)", x, y)
        return True

    def _save_pos(self):
        """拖动释放后落盘当前位置"""
        if self._config is None:
            return
        self._config.set("quick_capture_pos", [self.x(), self.y()])
        self._config.save()

    def toggle(self):
        """热键切换：可见 → 隐藏；隐藏 → 显示"""
        get_logger().info("[快捕] 热键 toggle：当前可见=%s", self.isVisible())
        if self.isVisible():
            self.hide()
        else:
            self.show_centered()

    # ---------------- 提交 ----------------
    def _submit(self):
        text = self._input.text().strip()
        if not text:
            self.hide()
            return
        try:
            self._fm.add_clipboard_text(text, source="快速捕捉")
        except Exception:
            pass  # 写入失败不崩溃，窗口照常关闭
        self._input.clear()
        self.hide()
        self.capture_submitted.emit(text)

    # ---------------- 事件 ----------------
    def _handle_rect(self) -> QRect:
        """手柄在窗口坐标系中的矩形"""
        return QRect(self._handle.mapTo(self, QPoint(0, 0)), self._handle.size())

    def mousePressEvent(self, event):
        """手柄区域按下 → 开始拖动并 grabMouse 独占后续鼠标事件。

        QLabel 自身忽略 press，事件上抛到窗口；输入框区域点击不进此分支。
        """
        if (event.button() == Qt.MouseButton.LeftButton
                and self._handle_rect().contains(event.position().toPoint())):
            self._drag_active = True
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            self.grabMouse()
            get_logger().info("[快捕] 拖动开始 offset=%s", self._drag_offset)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self.releaseMouse()
            self._save_pos()
            get_logger().info("[快捕] 拖动结束 pos=(%d,%d) 已落盘", self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _close_via_esc(self):
        """Esc 关闭（QShortcut / 过滤器 / keyPressEvent 三路共用）"""
        get_logger().info("[快捕] Esc 关闭（active=%s）", self.isActiveWindow())
        self._input.clear()
        self.hide()
        self.capture_hidden.emit()

    def eventFilter(self, obj, event):
        """拦截 _input 的 Key_Escape（QLineEdit 自行消费 Esc，窗口收不到）。

        注意：过滤器不得访问构造期尚未创建的属性——在 Qt 事件分发内抛
        Python 异常会触发 PyQt6 原生崩溃（0xC0000409，无 traceback）。
        """
        if (obj is self._input and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self._close_via_esc()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._close_via_esc()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        """点击其他区域自动收起"""
        super().focusOutEvent(event)
        if self.isVisible():
            get_logger().info("[快捕] 失焦自动隐藏")
            self.hide()
            self.capture_hidden.emit()

    # ---------------- 主题 ----------------
    def apply_theme(self, theme_name: str):
        """跟随主题刷新配色"""
        self._theme = theme_name
        c = get_colors(theme_name)
        self._input.setStyleSheet(
            f"QLineEdit {{"
            f" background: {c.get('card_bg_solid', '#FFFFFF')};"
            f" color: {c.get('text', '#2C3E50')};"
            f" border: 1px solid {c.get('primary_border', 'transparent')};"
            f" border-radius: 14px;"
            f" padding: 0 16px;"
            f" font-size: 14px;"
            f" selection-background-color: {c.get('primary', '#5BC0BE')};"
            f" }}"
            f"QLineEdit::placeholder {{ color: {c.get('text_placeholder', '#AAB4BF')}; }}"
        )
        hint = self.findChild(QLabel, "quickCaptureHint")
        if hint is not None:
            hint.setStyleSheet(
                f"color: {c.get('text_secondary', '#8B96A3')};"
                f"font-size: 11px; padding-right: 12px; background: transparent;"
            )
        handle = self.findChild(QLabel, "qcDragHandle")
        if handle is not None:
            handle.setStyleSheet(
                f"color: {c.get('text_secondary', '#8B96A3')};"
                f"font-size: 15px; background: transparent;"
                f"#qcDragHandle:hover {{ color: {c.get('primary', '#5BC0BE')}; }}"
            )
