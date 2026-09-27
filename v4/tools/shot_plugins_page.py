# -*- coding: utf-8 -*-
"""插件中心页真实截图（light / dark 双版）——验证失败区、状态标签、启停开关观感。

跑法：python tools/run_gui_check.py tools/shot_plugins_page.py
产物：build/shots/plugins-{light,dark}.png
"""
import os
import sys
import json
import shutil
import tempfile

# ★ 必须在 import PyQt6 之前设定，否则进程硬崩 0xC0000005
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication                      # noqa: E402
from PyQt6.QtGui import QFontDatabase                         # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.config import ConfigManager                          # noqa: E402
from src.main_window import MainWindow                        # noqa: E402
from src.plugin_api import ActionRegistry                     # noqa: E402
from src.plugin_loader import PluginLoader                    # noqa: E402
from src.docx_manager import DocxManager                      # noqa: E402
from src.task_manager import TaskManager                      # noqa: E402
from src.note_manager import NoteManager                      # noqa: E402
from src.fragment_manager import FragmentManager              # noqa: E402
from src.nav_manager import NavManager                        # noqa: E402
from src.temp_asset_manager import TempAssetManager           # noqa: E402
from src.clipboard_monitor import ClipboardMonitor            # noqa: E402
from src.app_paths import get_base_dir, get_data_dir, get_docx_path  # noqa: E402

OUT_DIR = os.path.join(BASE, "..", "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 构造「1 好 + 1 抢热键（部分生效） + 2 坏」的插件目录 ----
root = tempfile.mkdtemp(prefix="fp_shot_plugins_")


def mk(name, files):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for fn, content in files.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write(content)


_PLUGIN = """
from src.plugin_api import BallPlugin, BallAction
class A(BallAction):
    id = "%s"
    title = "%s"
    def run(self, ctx): pass
class P(BallPlugin):
    \"\"\"%s\"\"\"
    def create_actions(self, ctx): return [A()]
"""


def _src(aid, title):
    """生成入口模块源码；action id 必须与 manifest 声明完全一致，
    否则加载器会判「manifest 声明了动作 X 但 create_actions 未提供实现」。"""
    return _PLUGIN % (aid, title, title)


mk("color-picker", {
    "manifest.json": json.dumps({
        "id": "color-picker", "name": "屏幕取色器", "version": "1.2",
        "entry": "plugin.py", "requires": ["PyQt6"],
        "description": "按下热键即可吸取屏幕上任意位置的颜色，复制为 HEX。",
        "actions": [{"id": "color-picker.pick", "title": "吸取屏幕颜色",
                     "hotkey": "Ctrl+Alt+C", "menu": True}],
    }, ensure_ascii=False),
    "plugin.py": _src("color-picker.pick", "吸取屏幕颜色"),
    "使用说明.md": "# 屏幕取色器\n\n按下 Ctrl+Alt+C 后，鼠标位置会实时显示颜色预览。\n",
})
mk("quick-translate", {
    "manifest.json": json.dumps({
        "id": "quick-translate", "name": "划词翻译", "version": "0.9",
        "entry": "plugin.py", "requires": [],
        "description": "选中文字后按热键，在浮窗中显示中英互译结果。",
        "actions": [
            {"id": "quick-translate.tr", "title": "翻译选中文字",
             "hotkey": "Ctrl+Alt+T", "menu": True},
            {"id": "quick-translate.extra", "title": "发音朗读（无热键）",
             "hotkey": None, "menu": True},
        ],
    }, ensure_ascii=False),
    "plugin.py": _src("quick-translate.tr", "翻译选中文字"),
    "使用说明.md": "# 划词翻译\n\n选中任意文字，按 Ctrl+Alt+T 查看译文。\n",
})
mk("broken-manifest", {"manifest.json": "{ this is not valid json"})
mk("needs-network", {
    "manifest.json": json.dumps({
        "id": "needs-network", "name": "天气小组件", "version": "1.0",
        "entry": "plugin.py", "requires": ["requests"],
        "description": "在卡片上显示所在地天气。",
        "actions": [],
    }, ensure_ascii=False),
    "plugin.py": "x = 1\n",
})

# ---- 建窗口（真实 MainWindow 作宿主） ----
tmp_cfg = tempfile.mkdtemp()
cfg_path = os.path.join(tmp_cfg, "config.json")
cm = ConfigManager(cfg_path)
cm.set("plugins_enabled", True)

_base = get_base_dir()
_data = get_data_dir(_base)
docx = DocxManager(get_docx_path(_base), os.path.join(tmp_cfg, "docx_meta.json"))
docx.load()
tasks = TaskManager(os.path.join(tmp_cfg, "schedule.json"))
notes = NoteManager(os.path.join(tmp_cfg, "notes.json"))
frags = FragmentManager(os.path.join(tmp_cfg, "fragments.json"))
nav = NavManager(os.path.join(tmp_cfg, "nav.json"))
assets = TempAssetManager(_base)
clip = ClipboardMonitor(frags, cm)

win = MainWindow(tasks, notes, frags, docx, cm, clip,
                 temp_asset_manager=assets, nav_manager=nav)
win.resize(1280, 740)

reg = ActionRegistry()
loader = PluginLoader(reg, ctx=None, plugins_dir=root)
loader.load_all()
win.set_plugin_loader(loader)

# 再手动制造「部分生效」：加一个抢同一热键的插件，后登记会让位
mk("rival", {
    "manifest.json": json.dumps({
        "id": "rival", "name": "翻译增强", "version": "1.0",
        "entry": "plugin.py", "requires": [],
        "description": "同热键的竞争插件，后登记会让位。",
        "actions": [{"id": "rival.tr", "title": "翻译增强", "hotkey": "Ctrl+Alt+T",
                     "menu": True}],
    }, ensure_ascii=False),
    "plugin.py": _src("rival.tr", "翻译增强"),
})
loader.rescan()

win.show_plugins_page()
app.processEvents()


for theme in ("light", "dark"):
    cm.set("theme", theme)
    win._theme = theme
    win._apply_theme()
    win.refresh_page("plugins")
    app.processEvents()
    app.processEvents()
    path = os.path.join(OUT_DIR, f"plugins-{theme}.png")
    win.grab().save(path)
    print(f"[OK] saved {path}")

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(tmp_cfg, ignore_errors=True)
print("[DONE]")
