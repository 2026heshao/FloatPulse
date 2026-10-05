# -*- coding: utf-8 -*-
"""
====================================================================
玻璃拟态消息框  -  GlassMessageBox
====================================================================
原生 QMessageBox 的玻璃风格替代品（2026-10 遗留旧 UI 统一专项）。

四个静态工厂与 ``QMessageBox`` 静态方法**签名同构**（parent, title, text），
132 处旧调用点机械替换时调用形态不变，只有两处口径差异：

  1. ``question`` 返回 ``bool``（Yes→True / No→False），
     不再是 ``QMessageBox.StandardButton`` 枚举；
  2. 危险确认（文案含 删除/清空/卸载/覆盖）传 ``danger=True``，
     确认按钮走 ``dangerBtn``（hover 实底白字），且**默认焦点在「取消」**
     —— 防回车误触不可撤销操作。

视觉规格：
  · 壳 = GlassDialog 全套（无边框 + WA_TranslucentBackground + GlassPanel
    玻璃 + 手绘阴影 + 自绘标题栏），objectName 与主窗口一致 → 双主题自动跟随；
  · 版式 = 固定窄体（宽 440）：32px 语义图标 | 正文（word-wrap，>8 行出滚动）
    | 底部按钮行（一律 SmoothButton，禁裸 QPushButton）；
  · 图标零新增：查 ``_ICON_MAP`` 复用 icons.py 既有形体（help / info /
    warning），着色查 ``_ICON_COLOR``（$warning 琥珀令牌 / $danger 实红），
    apply_theme 时按主题重取位图色。

模态策略（**刻意不置顶**）：沿 glass_dialog.py:97-104 的既定决策 ——
置顶弹窗曾盖死原生消息框导致全应用看似锁死。QDialog.exec 的模态
（parent=调用者控件）足以挡住宿主窗口链，无需 WindowStaysOnTopHint。

用法：
    if GlassMessageBox.question(self, "确认清空", "确认清空全部碎片？",
                                danger=True):
        ...
    GlassMessageBox.warning(self, "保存失败", "docx 保存失败。")
====================================================================
"""

import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from src.constants import DEFAULT_THEME
from src.glass_dialog import GlassDialog
from src.icon_render import icon_pixmap
from src.theme import get_colors

# ---- 语义 → 图标形体（icons.py 既有图标，零新增数据）----
_ICON_MAP = {
    "question": "help",         # 圆 + 问号
    "information": "info",      # 圆 + i
    "warning": "warning",       # 三角 + 感叹号
    "critical": "warning",      # 同形体，色拉满红（critical 场景稀少，可接受）
}

# ---- 语义 → 图标着色（theme.py 令牌名；critical 与 warning 靠色深区分）----
_ICON_COLOR = {
    "question": "primary",
    "information": "primary",
    "warning": "warning",
    "critical": "danger",
}

# ---- 尺寸常量 ----
WIDTH = 440                      # 窄体固定宽（GlassDialog.SHADOW_MARGIN 外扩另算）
_TEXT_WIDTH_PX = 324             # 正文可用宽 = 440 - 2*16(阴影) - 2*20(边距) - 32(图标) - 12(间距)
_CHARS_PER_LINE = 24             # 13px 正文下每行约 24 个全角字符
_LINE_HEIGHT = 20                # 13px 正文行高
_MAX_TEXT_LINES = 8              # 超过 8 行出滚动（不再撑高）
_MIN_H = 200
_MAX_H = 340
_BUTTON_ROW_H = 36               # footer 按钮行高（估算用）


def _display_width(seg: str) -> float:
    """按全角/半角估算一段文本的显示宽度（全角=1，ASCII≈0.55，单位=全角格）。"""
    width = 0.0
    for ch in seg:
        width += 1.0 if ord(ch) > 0x2E80 else 0.55
    return width


def _estimate_lines(text: str) -> int:
    """按每行 ``_CHARS_PER_LINE`` 个全角格估算正文行数（含显式换行）。"""
    lines = 0
    for seg in str(text).split("\n"):
        lines += max(1, math.ceil(_display_width(seg) / _CHARS_PER_LINE))
    return max(1, lines)


def _resolve_theme(parent) -> str:
    """沿 parent 链解析主题名（调用点零参数的关键）。

    回退链：``current_theme``（MainWindow / 面板 host）→ ``_host``.current_theme
    （GlassDialog 全家把宿主存 ``_host``——「清仓建议」弹窗当 parent、消息框
    嵌套时都靠这步命中）→ ``_theme``（widget_app_launcher 等桌面小部件，
    无 _host 只有 self._theme）→ DEFAULT_THEME（parent=None 或链上全无主题
    属性）。只认 "light"/"dark"，链上对象挂了同名属性但值非法时不误判。
    """
    obj = parent
    seen = 0
    while obj is not None and seen < 16:      # 上限防异常长的 parent 环
        seen += 1
        theme = getattr(obj, "current_theme", None)
        if theme in ("light", "dark"):
            return theme
        host = getattr(obj, "_host", None)     # GlassDialog 存宿主的地方
        if host is not None:
            theme = getattr(host, "current_theme", None)
            if theme in ("light", "dark"):
                return theme
        theme = getattr(obj, "_theme", None)
        if theme in ("light", "dark"):
            return theme
        parent_attr = getattr(obj, "parent", None)
        if not callable(parent_attr):
            break
        obj = parent_attr()                    # parent() 返回 None 即到顶
    return DEFAULT_THEME


class _ThemeHost:
    """给 GlassDialog 喂主题的最小宿主壳。

    GlassDialog 内部多处走 ``host.current_theme``（标题栏关闭钮取色 /
    apply_theme / paintEvent 阴影深浅）。GlassMessageBox 的主题来自
    ``_resolve_theme(parent)``，与真实 host 无关 —— 用这个 3 行壳把
    解析结果伪装成宿主属性，基类全链路零改动。
    """

    def __init__(self, theme: str):
        self._theme = theme

    @property
    def current_theme(self) -> str:
        return self._theme


class GlassMessageBox(GlassDialog):
    """消息框版 GlassDialog：固定窄体版式（32px 图标 | 正文 | 按钮行）。

    不复用编辑器型 600×500：宽度固定 440，高度按正文行数自适应
    （每行约 20px，超过 8 行正文转入滚动区，弹窗高度封顶）。
    """

    def __init__(self, parent=None, title: str = "", text: str = "",
                 *, icon_kind: str = "information", theme: str = None):
        # ---- 主题解析（须在 super().__init__ 前：基类构造末尾就会 apply_theme）----
        self._icon_kind = (icon_kind if icon_kind in _ICON_MAP
                           else "information")
        self._theme_name = (theme if theme in ("light", "dark")
                            else _resolve_theme(parent))
        self._text = str(text or "")
        self._lines = _estimate_lines(self._text)

        lines_capped = min(self._lines, _MAX_TEXT_LINES)
        content_h = max(32, lines_capped * _LINE_HEIGHT)
        height = (GlassDialog.SHADOW_MARGIN * 2          # 阴影留白
                  + GlassDialog.TITLE_BAR_HEIGHT          # 标题栏
                  + 32                                    # body 上下边距
                  + content_h                             # 正文区
                  + 10                                    # 布局间距
                  + _BUTTON_ROW_H                         # 按钮行
                  + 6)                                    # 余量
        height = max(_MIN_H, min(_MAX_H, int(height)))

        super().__init__(_ThemeHost(self._theme_name), title=title,
                         parent=parent, size=(WIDTH, height))

        # ---- 正文区：图标 | 文本（超 8 行套滚动）----
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(32, 32)
        self._icon_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self._text_label = QLabel(self._text)
        self._text_label.setObjectName("hintLabel")
        self._text_label.setWordWrap(True)

        row = QWidget()
        row_l = QVBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(0)
        inner = QWidget()
        inner_l = QHBoxLayout(inner)
        inner_l.setContentsMargins(0, 0, 0, 0)
        inner_l.setSpacing(12)
        inner_l.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)
        inner_l.addWidget(self._text_label, 1)
        row_l.addWidget(inner)
        row_l.addStretch()

        if self._lines > _MAX_TEXT_LINES:
            # 长文本：滚动省略（弹窗高度封顶，内容不丢）
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setStyleSheet(
                "QScrollArea{background:transparent;border:none;}"
                "QWidget#qt_scrollarea_viewport{background:transparent;}")
            scroll.setWidget(row)
            self.body_layout.addWidget(scroll, 1)
        else:
            self.body_layout.addWidget(row, 1)

        self._retint_icon()

    # ---------------- 主题 ----------------
    def apply_theme(self):
        """复用 GlassDialog 全套 QSS，再按当前主题重取语义图标位图色。"""
        super().apply_theme()
        self._retint_icon()

    def _retint_icon(self):
        """按 ``_ICON_COLOR`` 令牌重画 32px 语义图标（QSS 管不到位图）。"""
        label = getattr(self, "_icon_label", None)
        if label is None:
            # 基类 __init__ 末尾首次 apply_theme 时本类控件尚未创建 → 天然跳过
            return
        colors = get_colors(self._theme_name)
        token = _ICON_COLOR[self._icon_kind]
        color = colors.get(token, colors["primary"])
        label.setPixmap(icon_pixmap(_ICON_MAP[self._icon_kind], 32, color))

    # ---------------- 静态工厂（与 QMessageBox 静态方法签名同构）----------------
    @staticmethod
    def question(parent, title: str, text: str, *,
                 ok_text: str = "确认", cancel_text: str = "取消",
                 danger: bool = False, theme: str = None) -> bool:
        """模态确认框。返回 True（确认）/ False（取消 / ESC）。

        danger=True 时确认按钮 objectName="dangerBtn"（hover 实底白字），
        否则 primaryBtn；取消恒为 secondaryBtn。**默认焦点在「取消」**
        —— 防回车误触危险操作（danger 与普通确认统一此口径，已拍板）。
        """
        box = GlassMessageBox(parent, title, text, icon_kind="question",
                              theme=theme)
        ok_name = "dangerBtn" if danger else "primaryBtn"
        btns = box.add_footer([
            (ok_text, ok_name, box.accept),
            (cancel_text, "secondaryBtn", box.reject),
        ])
        btns[1].setFocus()          # 默认焦点：取消侧
        box.exec()
        return box.result() == QDialog.DialogCode.Accepted

    @staticmethod
    def information(parent, title: str, text: str, *,
                    theme: str = None) -> None:
        """单「知道了」确认钮的提示框（成功回执类调用点请改用 ScreenToast）。"""
        GlassMessageBox._exec_notice(parent, title, text,
                                     "information", theme)

    @staticmethod
    def warning(parent, title: str, text: str, *,
                theme: str = None) -> None:
        """警告框（琥珀图标；操作失败类不降级 toast —— 错误需确认才消失）。"""
        GlassMessageBox._exec_notice(parent, title, text, "warning", theme)

    @staticmethod
    def critical(parent, title: str, text: str, *,
                 theme: str = None) -> None:
        """错误框（实红图标，与 warning 同形体靠色深区分）。"""
        GlassMessageBox._exec_notice(parent, title, text, "critical", theme)

    # ---------------- 内部 ----------------
    @staticmethod
    def _exec_notice(parent, title: str, text: str,
                     icon_kind: str, theme: str = None) -> None:
        """information / warning / critical 共用：单「知道了」primaryBtn。"""
        box = GlassMessageBox(parent, title, text, icon_kind=icon_kind,
                              theme=theme)
        (btn,) = box.add_footer([("知道了", "primaryBtn", box.accept)])
        btn.setFocus()
        box.exec()
