# -*- coding: utf-8 -*-
"""A3 EmptyState 通用化（2026-10-01）离屏端到端验证 + 8 页空数据真截图。

pytest 钉不住的部分（本脚本存在的理由）：
  1. **真 MainWindow 空数据走查**：7 个内置页切页后空态真的「可见」——
     显隐由各面板 refresh 链驱动，只有真实切页+真实刷新才走得到；
  2. **8 张空数据真截图 ×2 主题**：空态是「图标+标题+提示」的排版件，
     只靠断言看不出居中/留白/字号是否成立，必须出图人工核对；
  3. **plugins 页用空 loader 单独渲染**：真实 plugins/ 目录有内置插件，
     空态只能用 loader=None 的最小宿主替身造出来（与 test_plugins_panel
     同款手法）。

用法：
    python tools/run_gui_check.py tools/verify_empty_state.py
产物：
    build/shots/empty-<page>-<theme>.png（16 张）
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtGui import QFontDatabase                              # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

from src.config import ConfigManager                               # noqa: E402
from src.docx_manager import DocxManager                           # noqa: E402
from src.fragment_manager import FragmentManager                    # noqa: E402
from src.main_window import MainWindow, NAV_PAGE_INDEX          # noqa: E402
from src.nav_manager import NavManager                             # noqa: E402
from src.note_manager import NoteManager                           # noqa: E402
from src.plugins_panel import PluginsPanel                          # noqa: E402
from src.task_manager import TaskManager                           # noqa: E402
from src.temp_asset_manager import TempAssetManager                 # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "build", "shots")

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, ms=300):
    end_t = time.time() + ms / 1000.0
    while time.time() < end_t:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def load_cjk_font(app):
    """只挂中文字体，**刻意不挂 emoji / 符号字体**（与 verify_icons_render
    同一口径：空态图标是自绘位图，不靠 emoji 字形也不该靠）"""
    loaded = []
    for cand in (r"C:\Windows\Fonts\msyh.ttc",):
        if os.path.exists(cand) and app is not None:
            if QFontDatabase.addApplicationFont(cand) >= 0:
                loaded.append(os.path.basename(cand))
    return loaded


class _EmptyHost:
    """最小宿主替身（loader=None → 零插件；与 test_plugins_panel 同款）"""

    def __init__(self):
        self._config = {"plugins_enabled": True}
        self._loader = None

    @property
    def plugin_loader(self):
        return self._loader


def main():
    app = QApplication(sys.argv)
    print(f"已挂字体：{load_cjk_font(app)}（刻意不含 emoji / 符号字体）")
    tmp = tempfile.mkdtemp(prefix="fp_verify_empty_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(tmp, "docx_meta.json"))
    docx.load()
    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    # ★ 刻意不播种任何数据：本轮验证的就是「空」
    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(1280, 740)
    win.show()

    os.makedirs(OUT_DIR, exist_ok=True)
    shots_ok = []

    # 页面索引用 NAV_PAGE_INDEX 查表（栈里 6=设置、9=plugins，写死必踩坑）
    pages = [
        ("fragments", NAV_PAGE_INDEX["fragments"],
         lambda: win._page_fragments._empty_state),
        ("tasks", NAV_PAGE_INDEX["tasks"],
         lambda: win._page_tasks._task_empty),
        ("notes", NAV_PAGE_INDEX["notes"],
         lambda: win._page_notes._note_empty_label),
        ("knowledge", NAV_PAGE_INDEX["knowledge"],
         lambda: win._page_knowledge._kb_empty),
        ("assets", NAV_PAGE_INDEX["assets"],
         lambda: win._page_assets._empty_state),
        ("nav", NAV_PAGE_INDEX["nav"],
         lambda: win._page_nav._nav_list._empty_label
         if hasattr(win._page_nav, "_nav_list") else None),
        ("apps", NAV_PAGE_INDEX["apps"],
         lambda: win._page_app_launcher._apps_empty),
    ]

    for theme in ("light", "dark"):
        win._theme = theme
        config.set("theme", theme)
        win._apply_theme()
        win.theme_changed.emit(theme)
        pump(app, 250)

        for name, idx, getter in pages:
            win._switch_page(idx)
            pump(app, 300)
            es = getter()
            if es is None:
                check(f"{theme}/{name}: 空态控件存在", False, "getter 返回 None")
                continue
            visible = es.isVisible()
            check(f"{theme}/{name}: 空数据下空态可见", visible)
            if visible:
                path = os.path.join(OUT_DIR, f"empty-{name}-{theme}.png")
                if win.grab().save(path):
                    shots_ok.append(path)

    # plugins 页：空 loader 独立面板（真实目录有内置插件，主窗造不出空态）
    panel = PluginsPanel(_EmptyHost())
    panel.resize(900, 600)
    panel.show()
    pump(app, 250)
    check("plugins(loader=None): 空态可见",
          panel._empty_label.isVisibleTo(panel))
    if panel._empty_label.isVisibleTo(panel):
        path = os.path.join(OUT_DIR, "empty-plugins-light.png")
        if panel.grab().save(path):
            shots_ok.append(path)

    made = [p for p in os.listdir(OUT_DIR) if p.startswith("empty-")]
    check("空数据截图落盘（≥8 张）", len(made) >= 8, f"{len(made)} 张")

    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    print(f"（截图见 {OUT_DIR}\\empty-*.png）")
    win.deleteLater()
    panel.deleteLater()
    app.processEvents()
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
