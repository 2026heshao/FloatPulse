# -*- coding: utf-8 -*-
"""B2 走查脚本：全部合法档位离屏截图（活字缩放观感检查，2026-10-05）。

背景：150% 档实测溢出（设置页步进器「- 数值 +」的加号被挤出卡片右缘），
档位上限已收窄到 130%（见 theme.UI_SCALE_VALUES）；本脚本对
UI_SCALE_VALUES 的每个档位：
  1. apply_app_font(scale)（登记档位 + 全局字号）
  2. 隔离临时数据目录建 MainWindow（不碰真实 float_data）
  3. 抓「首页 / 设置页」两张截图 → build/shots/ui_scale_<scale>_*.png
  4. 输出每档窗口内容尺寸，供 130%/150% 溢出目检

运行：QT_QPA_PLATFORM=offscreen python tools/verify_ui_scale_walkthrough.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer                          # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QLinearGradient,  # noqa: E402
                         QPainter, QPixmap)
from PyQt6.QtWidgets import QApplication                 # noqa: E402

from src.theme import apply_app_font, UI_SCALE_VALUES    # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "build", "shots")


def compose(pixmap):
    """透明窗口截图合成到模拟桌面上（与 tools/shot_ui.py 同款做法）。"""
    out = QPixmap(pixmap.size())
    painter = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    grad.setColorAt(0.0, QColor("#E9EFF4"))
    grad.setColorAt(1.0, QColor("#D0DBE4"))
    painter.fillRect(out.rect(), QBrush(grad))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


def build_window(tmp):
    """隔离数据目录里搭一个真 MainWindow（与 qa_verify_nav_drag 同款）。"""
    from src.clipboard_monitor import ClipboardMonitor
    from src.config import ConfigManager
    from src.docx_manager import DocxManager
    from src.fragment_manager import FragmentManager
    from src.main_window import MainWindow
    from src.note_manager import NoteManager
    from src.task_manager import TaskManager
    from src.temp_asset_manager import TempAssetManager

    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config = ConfigManager(os.path.join(data_dir, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data_dir, "docx_meta.json"))
    docx.load()
    task = TaskManager(os.path.join(data_dir, "schedule.json"))
    note = NoteManager(os.path.join(data_dir, "notes.json"))
    frag = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag, config)
    assets = TempAssetManager(tmp)
    return MainWindow(task, note, frag, docx, config, clip, assets)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    os.makedirs(OUT_DIR, exist_ok=True)
    failures = []
    for scale in UI_SCALE_VALUES:
        tmp = tempfile.mkdtemp(prefix="fp_scale_walk_%d_" % scale)
        apply_app_font(scale)               # 登记档位 + 全局字号
        win = build_window(tmp)
        win.show()
        app.processEvents()
        refresh = getattr(win, "_apply_theme", None)
        if callable(refresh):
            refresh()                       # 强制按当前档位重生成 QSS
            app.processEvents()

        def shoot_home(scale=scale, win=win):
            out = os.path.join(OUT_DIR,
                               "ui_scale_%d_home.png" % scale)
            compose(win.grab()).save(out)
            print("[OK] %d%% 首页 → %s（内容区 %dx%d）"
                  % (scale, out, win.width(), win.height()))

        def shoot_settings(scale=scale, win=win):
            win._switch_page(6)             # 设置页（与 shot_ui 同页号）
            app.processEvents()

            def snap():
                out = os.path.join(OUT_DIR,
                                   "ui_scale_%d_settings.png" % scale)
                compose(win.grab()).save(out)
                print("[OK] %d%% 设置页 → %s" % (scale, out))
                win.hide()
                win.deleteLater()

            QTimer.singleShot(400, snap)

        QTimer.singleShot(700, shoot_home)
        QTimer.singleShot(900, shoot_settings)
        # 每档位跑约 2 秒事件循环等截图落盘
        deadline = __import__("time").time() + 2.5
        while __import__("time").time() < deadline:
            app.processEvents()
    apply_app_font(100)                     # 还原，不留档位污染
    print("\n==== 走查截图完成：%d 档 × 2 页 ====" % len(UI_SCALE_VALUES))
    if failures:
        for f in failures:
            print("[FAIL] %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    __import__("sys").exit(main())
