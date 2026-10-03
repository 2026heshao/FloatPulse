# -*- coding: utf-8 -*-
"""渲染 README 首屏截图（真实 MainWindow，离屏）——「一裸一妆」口径（2026-10-03）。

用法：
    python tools/run_gui_check.py tools/shot_readme.py
    （或直接 python tools/shot_readme.py；脚本自带 offscreen 设置）

产出（覆盖 docs/images/ 后自动复制到 assets/images/）：
    preview-main-light.png   浅色 + 默认强调色 + 无壁纸 ——「开箱即用就这样」
    preview-main-dark.png    深色 + 靛青强调色 + 壁纸 ——「能改成什么样」

⚠ 输出目录陷阱：OUT_DIR 是 docs/images/，而 .gitignore:33 整目录忽略
docs/（项目铁律：docs 不入库），README 实际读的是 assets/images/ ——
本脚本跑完会用 shutil.copy2 自动把两张图覆盖过去（bash cp 对中文路径
不可靠，必须 Python 复制）。

为什么不复用真实数据
====================================================================
README 是公开页面，而真实 `float_data/` 里是个人数据（剪贴板碎片、
任务、笔记、知识库全文）。本脚本把管理器全部指向**临时目录**，并现场
造一小份中性演示数据（会议要点、网址、命令、代码片段……），
既保证截图里没有任何个人信息，也不会有一次写入碰到真实数据。

唯一的例外是壁纸：`wallpaper.resolve_path` 只认真实数据根的
`float_data/backgrounds/`（导进临时目录会静默不生效），所以演示壁纸
必须 `wallpaper.import_image()` 导入真实目录——跑完 `prune_unused([])`
清掉，不留垃圾文件。config 本身指向临时目录，改了也不落盘。

侧栏按「工作台 + 工具 + 系统」三组同时展开渲染，正好展示
「多组可同时展开」这条交互；插件组留折叠态，顺便交代还有第四组。
"""

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QRectF, Qt, QTimer                     # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QFontDatabase,          # noqa: E402
                         QLinearGradient, QPainter, QPixmap,
                      QRadialGradient)                           # noqa: E402
from PyQt6.QtWidgets import QApplication                         # noqa: E402

from src import wallpaper                                        # noqa: E402
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
ASSETS_DIR = os.path.join(PROJECT, "assets", "images")

WIN_W, WIN_H = 1280, 740
PUMP_MS = 1400

DARK_ACCENT = "indigo"   # 深色「妆」档强调色：靛青（深底上 #A3ABF5 亮而不刺）

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


def make_wallpaper_png(path):
    """程序生成一张演示壁纸（深青灰渐变 + 三枚柔光大圆）。

    只用代码画，不引入任何外部图片资源文件；深色底上过 GlassPanel
    的主题色遮罩（veil）后仍能看出图案层次。
    """
    pm = QPixmap(WIN_W, WIN_H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, WIN_W, WIN_H)
    grad.setColorAt(0.0, QColor("#24343C"))
    grad.setColorAt(0.55, QColor("#1C2830"))
    grad.setColorAt(1.0, QColor("#141E24"))
    p.fillRect(pm.rect(), QBrush(grad))
    for cx, cy, r, base in ((180, 120, 300, (46, 126, 128)),
                            (WIN_W - 160, 90, 260, (64, 140, 150)),
                            (WIN_W * 0.55, WIN_H - 100, 340, (40, 110, 120))):
        g = QRadialGradient(cx, cy, r)
        col = QColor(base[0], base[1], base[2], 70)
        g.setColorAt(0.0, col)
        g.setColorAt(1.0, QColor(base[0], base[1], base[2], 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    p.end()
    pm.save(path, "PNG")


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
    这里铺一层中性渐变再叠加窗口（「壁纸」已是窗口内的真实功能，
    背板不再画彩色圆假装壁纸——裸档就该是素背景）。"""
    out = QPixmap(pixmap.size())
    painter = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    if dark:
        grad.setColorAt(0.0, QColor("#20262C"))
        grad.setColorAt(1.0, QColor("#171C21"))
    else:
        grad.setColorAt(0.0, QColor("#EDEFF2"))
        grad.setColorAt(1.0, QColor("#DEE2E6"))
    painter.fillRect(out.rect(), QBrush(grad))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    fonts = load_cjk_font(app)

    tmp = tempfile.mkdtemp(prefix="fp_shot_")
    config, docx, tasks, notes, frags, nav, assets = build_managers(tmp)

    # 演示壁纸：导入真实数据根（resolve_path 只认 get_backgrounds_dir()，
    # 导进 tmp 会静默不生效）；跑完 prune_unused([]) 清掉、配置复原。
    wp_tmp = os.path.join(tmp, "demo-wallpaper.png")
    make_wallpaper_png(wp_tmp)
    wp_name, err = wallpaper.import_image(wp_tmp)
    if err:
        print("[!] 壁纸导入失败（深色图退化为无壁纸）: %s" % err)
        wp_name = ""

    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(WIN_W, WIN_H)
    win.show()
    print("[i] 已挂字体: %s" % (", ".join(fonts) if fonts else "无（中文/图标会变方框）"))
    print("[i] 演示数据目录: %s" % tmp)
    print("[i] 演示壁纸文件: %s" % (wp_name or "（无）"))

    shots = [
        ("light", {"accent": "default", "accent_custom": "",
                   "wallpaper": ""}),                       # 裸：开箱即用
        # veil 默认 82%（遮罩越重越接近纯色底）——演示档降到 40 让壁纸
        # 图案看得清，同时保留文字可读的底色
        ("dark", {"accent": DARK_ACCENT, "accent_custom": "",
                  "wallpaper": wp_name, "wallpaper_mode": "cover",
                  "wallpaper_opacity": 100, "wallpaper_blur": 0,
                  "wallpaper_veil": 40}),                   # 妆：能改成什么样
    ]

    state = {"i": 0}

    def shoot():
        theme, overrides = shots[state["i"]]
        for key, value in overrides.items():
            config.set(key, value)
        # 主题：★ _apply_theme() 读的是实例属性 self._theme（不是配置），
        #   只改 config 不会换肤；两处都要写。config 指向临时目录，
        #   所以这次写入不会污染真实配置。_apply_theme 内部先
        #   sync_theme_extras 把 accent / 壁纸推进绘制层，顺序已对。
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
            compose(win.grab(), theme == "dark").save(path)
            print("[OK] %s  %dx%d" % (path, win.width(), win.height()))
            state["i"] += 1
            if state["i"] < len(shots):
                QTimer.singleShot(700, shoot)
            else:
                # 收尾：壁纸配置复原 + 真实 backgrounds 目录里的演示图清掉
                config.set("wallpaper", "")
                if wp_name:
                    removed = wallpaper.prune_unused([])
                    print("[i] 已清理演示壁纸 %d 个文件" % removed)
                win.hide()
                copy_to_assets()
                app.quit()

        QTimer.singleShot(600, grab)

    QTimer.singleShot(PUMP_MS, shoot)
    app.exec()


def copy_to_assets():
    """docs/ 被 .gitignore 整目录忽略，README 读的是 assets/images/——
    两张图必须复制过去覆盖（中文路径 bash cp 不可靠，用 shutil）。"""
    for name in ("preview-main-light.png", "preview-main-dark.png"):
        src = os.path.join(OUT_DIR, name)
        dst = os.path.join(ASSETS_DIR, name)
        if os.path.isfile(src):
            os.makedirs(ASSETS_DIR, exist_ok=True)
            shutil.copy2(src, dst)
            print("[OK] → %s" % dst)
        else:
            print("[!] 缺少 %s，未复制" % src)


if __name__ == "__main__":
    main()
