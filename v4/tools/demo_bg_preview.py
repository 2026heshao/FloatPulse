# -*- coding: utf-8 -*-
"""
背景适配对比图生成（一次性工具，用于评估方案）

思路：抓一块真实屏幕内容当背景，在内存里把「四种玻璃方案」叠上去渲染成一张对比图。
不需要窗口真正显示，因此不受沙箱 GUI 环境限制。

四种方案：
  A 现状        半透明 67%，背景原样透出（当前 v2 的做法）
  B 保底        不透明 88%，几乎看不到背景
  C 模糊底      先抓取卡片背后的背景并模糊，再叠 67% 填充（= 真玻璃观感）
  D 模糊底+保底 模糊背景 + 88% 填充

输出：设计稿/验证-背景适配-对比.png
"""
import os
import sys

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "设计稿")

CANVAS_W, CANVAS_H = 1000, 380
CARD_W, CARD_H = 226, 160
GAP = 14
PAD = 22


def blur_pixmap(pm: QPixmap, factor: int = 12) -> QPixmap:
    """降采样再放大 —— 最省的近似高斯模糊（几十微秒级）"""
    if pm.isNull() or pm.width() <= 2 or pm.height() <= 2:
        return pm
    img = pm.toImage()
    small = img.scaled(max(1, img.width() // factor), max(1, img.height() // factor),
                       Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    back = small.scaled(img.width(), img.height(),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(back)


def draw_card(painter: QPainter, rect: QRect, alpha: int, patch: QPixmap,
              title: str, note: str, blurred: bool):
    """画一张玻璃卡片：可选模糊底 + 半透明填充 + 亮描边 + 文字

    patch 是该卡片正下方那块背景（已按卡片尺寸裁好），blurred=True 时先模糊再叠。
    """
    if blurred and not patch.isNull():
        painter.drawPixmap(rect, blur_pixmap(patch))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, alpha))
    painter.drawRoundedRect(rect, 14, 14)

    # 顶部高光带（与 v2 的 GlassPanel 一致：上 28%，白 50%）
    band = QRect(rect.x(), rect.y(), rect.width(), int(rect.height() * 0.28))
    painter.save()
    painter.setClipRect(band)
    painter.setBrush(QColor(255, 255, 255, int(255 * 0.50)))
    painter.drawRoundedRect(rect, 14, 14)
    painter.restore()

    # 1px 亮描边
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QColor(255, 255, 255, 220))
    painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 14, 14)

    # 文字
    painter.setPen(QColor(91, 192, 190))
    f = QFont("Microsoft YaHei", 10)
    f.setBold(True)
    painter.setFont(f)
    painter.drawText(QRect(rect.x() + 16, rect.y() + 14, rect.width() - 32, 20),
                     int(Qt.AlignmentFlag.AlignLeft), title)

    painter.setPen(QColor(44, 62, 80))
    f2 = QFont("Microsoft YaHei", 9)
    painter.setFont(f2)
    painter.drawText(QRect(rect.x() + 16, rect.y() + 40, rect.width() - 32, 70),
                     int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap),
                     "背景上的文字是否清晰可读\n第二行核对叠加干扰\n128 条碎片 09:12")

    painter.setPen(QColor(139, 150, 163))
    f3 = QFont("Microsoft YaHei", 8)
    painter.setFont(f3)
    painter.drawText(QRect(rect.x() + 16, rect.y() + rect.height() - 30,
                           rect.width() - 32, 20),
                     int(Qt.AlignmentFlag.AlignLeft), note)


def main():
    app = QApplication(sys.argv)
    screen = app.primaryScreen()
    geo = screen.availableGeometry()

    row_h = CARD_H + 56
    specs = [
        ("旧 67%（改前）", "背景与文字打架", 172, False),
        ("88% ← 当前值", "本次改动的实际效果", 224, False),
        ("92%", "再实一档", 235, False),
        ("96%", "接近实色", 245, False),
    ]

    # 抓两块不同的真实屏幕区域，验证「深色背景」与「浅色背景」下的表现
    regions = [
        (geo.center().x() - CANVAS_W // 2,
         max(geo.top() + 10, geo.center().y() - 220)),
        (geo.center().x() - CANVAS_W // 2,
         min(max(geo.top() + 10, geo.bottom() - row_h - 10),
             geo.center().y() + 120)),
    ]

    total_h = row_h * len(regions)
    canvas = QPixmap(CANVAS_W, total_h)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(canvas.rect(), QColor(24, 26, 32))

    for r_i, (rx, ry) in enumerate(regions):
        bg = screen.grabWindow(0, rx, ry, CANVAS_W, row_h)
        if bg.isNull():
            print("[警告] 区域 %d 抓屏失败" % (r_i + 1))
            continue
        y0 = r_i * row_h
        p.drawPixmap(0, y0, bg)

        p.setPen(QColor(255, 255, 255, 235))
        f = QFont("Microsoft YaHei", 10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRect(PAD, y0 + 8, CANVAS_W - 2 * PAD, 22),
                   int(Qt.AlignmentFlag.AlignLeft),
                   "背景区域 %d —— 取自真实屏幕" % (r_i + 1))

        for i, (title, note, alpha, blurred) in enumerate(specs):
            rect = QRect(PAD + i * (CARD_W + GAP), y0 + 34, CARD_W, CARD_H)
            local = QRect(rect.x(), rect.y() - y0, rect.width(), rect.height())
            draw_card(p, rect, alpha, bg.copy(local), title, note, blurred)
    p.end()

    path = os.path.join(OUT_DIR, "验证-背景适配-对比.png")
    ok = canvas.save(path)
    print("[输出] %s → %s（%dx%d）" % ("成功" if ok else "失败", path,
                                        canvas.width(), canvas.height()))


if __name__ == "__main__":
    main()
