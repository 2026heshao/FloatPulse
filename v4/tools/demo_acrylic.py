# -*- coding: utf-8 -*-
"""
背景适配实测工具 —— 把三张卡片拖到不同背景上，比较可读性

用法（任选其一）：
    双击根目录的  启动v2.bat demo
    或命令行：cd v2 && python demo_acrylic.py

三张卡片分别代表：
    A 现状     半透明 67%，背景原样透出
    B 保底     不透明 80%，几乎看不到背景
    C 亚克力   交给 Windows DWM 做真实模糊（当前 Qt 版本能否生效，跑了才知道）

按 Esc 退出。窗口可拖动 —— 请把它们拖到 深色代码 / 白色网页 / 图片 等不同背景上对比。
"""
import ctypes
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

CARD_W, CARD_H = 300, 200
GAP = 16


# ====================================================================
# Win32：系统材质
# ====================================================================
class _AccentPolicy(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WinCompAttrData(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(_AccentPolicy)),
                ("SizeOfData", ctypes.c_size_t)]


ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_WINDOW_CORNER_PREFERENCE = 33


def set_acrylic(hwnd: int, tint_abgr: int = 0x99FFFFFF) -> str:
    user32 = ctypes.windll.user32
    fn = getattr(user32, "SetWindowCompositionAttribute", None)
    if fn is None:
        return "SetWindowCompositionAttribute 不可用（系统过旧）"
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WinCompAttrData)]
    accent = _AccentPolicy(ACCENT_ENABLE_ACRYLICBLURBEHIND,
                           0x20 | 0x40 | 0x80, tint_abgr, 0)
    data = _WinCompAttrData(WCA_ACCENT_POLICY, ctypes.pointer(accent),
                            ctypes.sizeof(accent))
    r = fn(ctypes.c_void_p(hwnd), ctypes.byref(data))
    return "亚克力 SetWindowCompositionAttribute → %s（1=成功）" % r


def set_backdrop(hwnd: int, kind: int = 3) -> str:
    dwm = ctypes.windll.dwmapi
    v = ctypes.c_int(kind)
    r = dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                                  ctypes.byref(v), ctypes.sizeof(v))
    return "云母/亚克力 DwmSetWindowAttribute(38, %d) → %s（0=成功）" % (kind, r)


def set_round_corner(hwnd: int) -> str:
    dwm = ctypes.windll.dwmapi
    v = ctypes.c_int(2)
    r = dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd),
                                  DWMWA_WINDOW_CORNER_PREFERENCE,
                                  ctypes.byref(v), ctypes.sizeof(v))
    return "系统圆角(33) → %s（0=成功）" % r


# ====================================================================
# 可拖动卡片
# ====================================================================
class GlassCard(QWidget):
    def __init__(self, title: str, alpha: int, acrylic: bool = False):
        super().__init__()
        self._alpha = alpha
        self._acrylic = acrylic
        self._drag = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(CARD_W, CARD_H)
        self.setWindowTitle(title)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)

        t = QLabel(title)
        t.setStyleSheet("color:#5BC0BE;font-size:13px;font-weight:600;")
        v.addWidget(t)

        body = QLabel("背景上的文字是否清晰可读\n"
                      "第二行用于核对叠加干扰\n"
                      "128 条碎片 · 09:12 · D:\\桌面\\AI Port")
        body.setStyleSheet("color:#2C3E50;font-size:13px;line-height:170%;")
        body.setWordWrap(True)
        v.addWidget(body)
        v.addStretch()

        tip = QLabel("拖动我 → 放到深色/浅色/网页背景上对比")
        tip.setStyleSheet("color:#8B96A3;font-size:11px;")
        v.addWidget(tip)

    # ---- 拖动窗口 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            QApplication.quit()

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if self._alpha > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, self._alpha))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 14, 14)
            # 顶部高光带
            painter.save()
            painter.setClipRect(rect.x(), rect.y(), rect.width(),
                                int(rect.height() * 0.28))
            painter.setBrush(QColor(255, 255, 255, 128))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 14, 14)
            painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 14, 14)
        painter.end()

    def apply_acrylic(self):
        hwnd = int(self.winId())
        print("  ·", set_backdrop(hwnd, 3))
        print("  ·", set_acrylic(hwnd))
        print("  ·", set_round_corner(hwnd))
        self._alpha = 0          # 交给系统合成，别自绘不透明底
        self.update()


def main():
    app = QApplication(sys.argv)
    screen = app.primaryScreen()
    geo = screen.availableGeometry()

    cards = [
        GlassCard("A 现状：半透明 67%", 172),
        GlassCard("B 保底：不透明 80%", 204),
        GlassCard("C 尝试：系统亚克力", 0, acrylic=True),
    ]

    total_w = CARD_W * len(cards) + GAP * (len(cards) - 1)
    x0 = max(geo.left() + 40, geo.center().x() - total_w // 2)
    y0 = max(geo.top() + 80, geo.center().y() - CARD_H // 2)

    print("=" * 60)
    print(" 背景适配实测：把三张卡片拖到不同背景上对比")
    print(" A=现状(半透明)  B=保底(不透明)  C=系统亚克力")
    print(" 按 Esc 退出")
    print("=" * 60)

    for i, c in enumerate(cards):
        c.move(x0 + i * (CARD_W + GAP), y0)
        c.show()
        if c._acrylic:
            print("[C 亚克力] Win32 调用结果：")
            c.apply_acrylic()
            print("  若 C 卡片看起来仍是普通半透明（没有模糊），说明系统材质在"
                  "当前窗口属性下没生效 —— 这属于已知冲突，改用模糊底方案即可。")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
