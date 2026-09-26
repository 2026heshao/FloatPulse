# -*- coding: utf-8 -*-
"""临时脚本（截图用，验证后可删）：把 v2 的主窗口 / 卡片 / 悬浮球渲染存成 PNG。

用法：
    python _shot_ui.py light
    python _shot_ui.py dark
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from src.app_paths import get_base_dir
from src.card_window import CardWindow
from src.clipboard_monitor import ClipboardMonitor
from src.config import ConfigManager
from src.docx_manager import DocxManager
from src.fragment_manager import FragmentManager
from src.main_window import MainWindow
from src.nav_manager import NavManager
from src.note_manager import NoteManager
from src.task_manager import TaskManager
from src.temp_asset_manager import TempAssetManager

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT, "设计稿")
THEME = sys.argv[1] if len(sys.argv) > 1 else "light"


def _compose(pixmap):
    """把带透明通道的窗口截图合成到"模拟桌面"上。

    半透明窗口直接 grab 出来是带 alpha 的，透明区在图片查看器里会显示成黑，
    因此这里先铺一层渐变 + 三个色块（模拟壁纸），再叠加窗口本身，
    这样看到的才接近真实运行时的观感。
    """
    out = QPixmap(pixmap.size())
    painter = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    grad.setColorAt(0.0, QColor("#E9EFF4"))
    grad.setColorAt(0.55, QColor("#DCE5EC"))
    grad.setColorAt(1.0, QColor("#D0DBE4"))
    painter.fillRect(out.rect(), QBrush(grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(79, 195, 192, 210))
    painter.drawEllipse(QRectF(-140, -120, 560, 400))
    painter.setBrush(QColor(126, 143, 224, 190))
    painter.drawEllipse(QRectF(pixmap.width() - 400, -80, 560, 420))
    painter.setBrush(QColor(239, 177, 131, 200))
    painter.drawEllipse(QRectF(pixmap.width() * 0.40, pixmap.height() - 240, 620, 460))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


def main():
    app = QApplication(sys.argv)
    base = get_base_dir()
    data = os.path.join(base, "data")

    config = ConfigManager(os.path.join(data, "config.json"))
    # 只改内存值，不 save()，避免污染真实配置
    config.set("theme", THEME)

    docx = DocxManager(os.path.join(base, "知识库.docx"),
                       os.path.join(data, "docx_meta.json"))
    docx.load()
    cards = docx.get_cards()
    tasks = TaskManager(os.path.join(data, "schedule.json"))
    notes = NoteManager(os.path.join(data, "notes.json"))
    frags = FragmentManager(os.path.join(data, "fragments.json"))
    nav = NavManager(os.path.join(data, "nav.json"))
    assets = TempAssetManager(base)
    clip = ClipboardMonitor(frags, config)

    win = MainWindow(tasks, notes, frags, docx, config, clip,
                     temp_asset_manager=assets, nav_manager=nav)
    win.show()
    # 让导航指示条落位
    QTimer.singleShot(50, lambda: win._switch_page(0))

    card = CardWindow(theme=THEME)
    card.set_cards(cards)
    card.set_fragment_manager(frags)
    card.set_task_manager(tasks)
    card.set_note_manager(notes)
    card.set_nav_manager(nav)
    card.set_config_manager(config)
    card.set_asset_manager(assets)

    def grab_main():
        _compose(win.grab()).save(
            os.path.join(OUT_DIR, "v2实机-主窗口-%s.png" % THEME))
        # 顺带出一张设置页（步进器 / 滑杆 / 分组卡片的落地效果）
        win._switch_page(6)
        QTimer.singleShot(600, grab_settings)

    def grab_settings():
        _compose(win.grab()).save(
            os.path.join(OUT_DIR, "v2实机-设置页-%s.png" % THEME))
        win._switch_page(0)
        card.popup_near(QRect(560, 300, 112, 112))
        QTimer.singleShot(800, grab_card)

    def grab_card():
        _compose(card.grab()).save(
            os.path.join(OUT_DIR, "v2实机-卡片-%s.png" % THEME))
        card.hide()
        grab_ball()

    def grab_ball():
        """同一张图里放两个球：左=常态，右=悬停（光晕扩散 + 放大）"""
        from knowledge_ball import _BallSurface

        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        holder.resize(300, 150)

        idle = _BallSurface(64, holder, theme=THEME)
        idle.setGeometry(20, 16, 112, 112)

        hover = _BallSurface(64, holder, theme=THEME)
        hover.setGeometry(168, 16, 112, 112)
        hover.set_appearance(hovered=True)
        hover._glow = 0.55
        hover._scale = 1.05

        holder.show()

        def shoot():
            _compose(holder.grab()).save(
                os.path.join(OUT_DIR, "v2实机-悬浮球-%s.png" % THEME))
            print("[OK] all screenshots saved, theme=%s" % THEME)
            holder.hide()
            win.hide()
            app.quit()

        QTimer.singleShot(350, shoot)

    QTimer.singleShot(1100, grab_main)
    app.exec()


if __name__ == "__main__":
    main()
