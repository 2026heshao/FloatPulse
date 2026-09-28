# -*- coding: utf-8 -*-
"""渲染 README 首屏截图（真实 MainWindow，离屏）。

用法：
    python tools/run_gui_check.py tools/shot_readme.py

产出（覆盖 docs/images/ 下的首屏图）：
    preview-main-light.png / preview-main-dark.png   1280×740 主窗口

为什么不复用真实数据
====================================================================
README 是公开页面，而真实 `float_data/` 里是个人数据（剪贴板碎片、
任务、笔记、知识库全文）。本脚本把管理器全部指向**临时目录**，并现场
造一小份中性演示数据（会议要点、网址、命令、代码片段……），
既保证截图里没有任何个人信息，也不会有一次写入碰到真实数据。

侧栏按「工作台 + 工具 + 系统」三组同时展开渲染，正好展示
「多组可同时展开」这条交互；插件组留折叠态，顺便交代还有第四组。
"""

import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QRectF, Qt, QTimer                     # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QFontDatabase,          # noqa: E402
                         QLinearGradient, QPainter, QPixmap)
from PyQt6.QtWidgets import QApplication                         # noqa: E402

from src.config import ConfigManager                             # noqa: E402
from src.docx_manager import DocxManager                         # noqa: E402
from src.fragment_manager import FragmentManager                 # noqa: E402
from src.main_window import MainWindow                           # noqa: E402
from src.nav_manager import NavManager                           # noqa: E402
from src.note_manager import NoteManager                         # noqa: E402
from src.task_manager import TaskManager                         # noqa: E402
from src.temp_asset_manager import TempAssetManager              # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "docs", "images")

WIN_W, WIN_H = 1280, 740
PUMP_MS = 1400

DEMO_FRAGMENTS = [
    ("text", "周会要点：下周三前把报价单模板定稿，接口文档同步给测试"),
    ("link", "https://docs.python.org/3/library/dataclasses.html"),
    ("command", "python -m PyInstaller --noconfirm --clean FloatPulse.spec"),
    ("path", r"D:\work\report\2026-09 月报汇总.pptx"),
    ("code", "def load(path):\n    with open(path, encoding=\"utf-8\") as f:\n        return json.load(f)"),
    ("text", "客户来访时间改到周五上午十点，会议室改三楼"),
]


def load_cjk_font(app):
    """离屏平台不加载任何系统字体，不显式挂字体截图里中文会变方框。

    顺带挂 emoji / 符号字体：页面标题与侧栏按钮的图标都是 emoji
    （🧩 📋 📝 …），只挂 msyh 时它们会渲染成空心方框，README 首屏
    图会很难看。挂不上只是图标退化成方框，不影响截图生成。
    """
    loaded = []
    for cand in (r"C:\Windows\Fonts\msyh.ttc",
                 r"C:\Windows\Fonts\seguiemj.ttf",
                 r"C:\Windows\Fonts\seguisym.ttf"):
        if os.path.exists(cand) and app is not None:
            if QFontDatabase.addApplicationFont(cand) >= 0:
                loaded.append(os.path.basename(cand))
    return loaded


def make_demo_docx(path):
    """造一份演示知识库（不读真实 知识库.docx）"""
    try:
        from docx import Document
    except Exception:
        return
    doc = Document()
    for text in ("项目周报固定在下班前发出，迟发要在群里说明原因",
                 "客户资料统一放 float_data，不要留在桌面",
                 "每次改动先跑 pytest，UI 改动补一张离屏截图",
                 "对外报价保留两位小数，含税价单独标注"):
        doc.add_paragraph(text)
    doc.save(path)


def build_managers(tmp):
    """全部指向临时目录的演示数据集"""
    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx_path = os.path.join(tmp, "知识库.docx")
    make_demo_docx(docx_path)
    docx = DocxManager(docx_path, os.path.join(tmp, "docx_meta.json"))
    docx.load()

    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    for ftype, content in DEMO_FRAGMENTS:
        frags.add_fragment(ftype, content, source="剪贴板")

    tasks.add_task("整理本周项目进度", "把三个里程碑的完成度写进周报", "2026-09-29")
    tasks.add_task("导出上季度数据", "", "2026-10-03")
    tasks.add_task("给测试同事讲一遍新接口", "带接口文档和示例请求", "2026-10-08")

    notes.add_note("会议纪要：确认了下阶段先做数据迁移，再补权限模块。",
                   "9 月复盘")
    notes.add_note("读到一句话：先让流程跑通，再谈优化。", "随手记")

    gid = nav.add_group("常用")
    nav.add_site(gid, "Python 文档", "https://docs.python.org/3/")
    nav.add_site(gid, "GitHub", "https://github.com/")

    return config, docx, tasks, notes, frags, nav, assets


def compose(pixmap, dark: bool):
    """半透明窗口直接 grab 出来带 alpha，查看器里透明区会显示成黑；
    这里先铺一层"模拟壁纸"再叠加窗口，接近真实观感。"""
    out = QPixmap(pixmap.size())
    painter = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    if dark:
        grad.setColorAt(0.0, QColor("#2A3540"))
        grad.setColorAt(0.55, QColor("#222C35"))
        grad.setColorAt(1.0, QColor("#1B242C"))
    else:
        grad.setColorAt(0.0, QColor("#E9EFF4"))
        grad.setColorAt(0.55, QColor("#DCE5EC"))
        grad.setColorAt(1.0, QColor("#D0DBE4"))
    painter.fillRect(out.rect(), QBrush(grad))
    painter.setPen(Qt.PenStyle.NoPen)
    if dark:
        painter.setBrush(QColor(79, 195, 192, 150))
        painter.drawEllipse(QRectF(-140, -120, 560, 400))
        painter.setBrush(QColor(126, 143, 224, 140))
        painter.drawEllipse(QRectF(pixmap.width() - 400, -80, 560, 420))
        painter.setBrush(QColor(239, 177, 131, 120))
        painter.drawEllipse(QRectF(pixmap.width() * 0.40, pixmap.height() - 240, 620, 460))
    else:
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
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    fonts = load_cjk_font(app)

    tmp = tempfile.mkdtemp(prefix="fp_shot_")
    config, docx, tasks, notes, frags, nav, assets = build_managers(tmp)

    shots = [("light", False), ("dark", True)]     # (主题, 壁纸是否深色)
    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(WIN_W, WIN_H)
    win.show()
    print("[i] 已挂字体: %s" % (", ".join(fonts) if fonts else "无（中文/图标会变方框）"))
    print("[i] 演示数据目录: %s" % tmp)

    state = {"i": 0}

    def shoot():
        theme = shots[state["i"]][0]
        dark = shots[state["i"]][1]
        # 主题：★ _apply_theme() 读的是实例属性 self._theme（不是配置），
        #   只改 config 不会换肤；两处都要写。config 指向临时目录，
        #   所以这次写入不会污染真实配置。
        win._theme = theme
        win._config.set("theme", theme)
        win._apply_theme()
        # 三组同时展开：正好展示「多组可同时展开」，插件组留折叠
        win._current_expanded_groups = ("workbench", "tools", "system")
        try:
            win._relayout_nav(win._nav_order)
        except Exception as exc:                     # 接口变动时别静默
            print("[!] 侧栏展开失败: %r" % (exc,))
        win._switch_page(0)

        def grab():
            path = os.path.join(OUT_DIR, "preview-main-%s.png" % theme)
            compose(win.grab(), dark).save(path)
            print("[OK] %s  %dx%d" % (path, win.width(), win.height()))
            state["i"] += 1
            if state["i"] < len(shots):
                QTimer.singleShot(700, shoot)
            else:
                win.hide()
                app.quit()

        QTimer.singleShot(600, grab)

    QTimer.singleShot(PUMP_MS, shoot)
    app.exec()


if __name__ == "__main__":
    main()
