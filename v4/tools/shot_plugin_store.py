# -*- coding: utf-8 -*-
"""插件商店区真实截图（light / dark）——可安装卡片 + 安装按钮。

跑法：python tools/run_gui_check.py tools/shot_plugin_store.py
产物：build/shots/plugins-store-{light,dark}.png
"""
import os
import sys
import json
import shutil
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication                      # noqa: E402
from PyQt6.QtGui import QFontDatabase                         # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.config import ConfigManager                          # noqa: E402
from src.main_window import MainWindow                        # noqa: E402
from src.plugin_api import ActionRegistry, PluginContext      # noqa: E402
from src.plugin_loader import PluginLoader                    # noqa: E402
from src.docx_manager import DocxManager                      # noqa: E402
from src.task_manager import TaskManager                      # noqa: E402
from src.note_manager import NoteManager                      # noqa: E402
from src.fragment_manager import FragmentManager              # noqa: E402
from src.nav_manager import NavManager                        # noqa: E402
from src.temp_asset_manager import TempAssetManager           # noqa: E402
from src.clipboard_monitor import ClipboardMonitor             # noqa: E402
from src.app_paths import get_base_dir, get_docx_path          # noqa: E402

OUT_DIR = os.path.join(BASE, "..", "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

root = tempfile.mkdtemp(prefix="fp_shot_store_")
plugins_dir = os.path.join(root, "plugins")
store_dir = os.path.join(root, "plugin_store")
os.makedirs(plugins_dir, exist_ok=True)
os.makedirs(store_dir, exist_ok=True)

_SRC = '''
from src.plugin_api import BallAction, BallPlugin


class Act(BallAction):
    id = "__PID__.go"
    title = "__TITLE__"

    def run(self, ctx):
        pass


class Plugin(BallPlugin):
    """__DESC__"""

    id = "__PID__"
    name = "__NAME__"
    version = "__VER__"

    def create_actions(self, ctx):
        return [Act()]
'''


def make_package(pid, name, ver, desc, extra=None, hotkey=None, nested=False):
    manifest = {
        "id": pid, "name": name, "version": ver, "entry": "plugin.py",
        "requires": ["PyQt6"], "description": desc,
        "actions": [{"id": f"{pid}.go", "title": f"{name}动作",
                     "hotkey": hotkey, "menu": True}],
    }
    manifest.update(extra or {})
    prefix = (pid + "/") if nested else ""
    pkg = os.path.join(store_dir, f"{pid}.fpplug")
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr(f"{prefix}manifest.json",
                    json.dumps(manifest, ensure_ascii=False))
        zf.writestr(f"{prefix}plugin.py", _SRC.replace("__PID__", pid)
                    .replace("__TITLE__", f"{name}动作")
                    .replace("__DESC__", desc).replace("__NAME__", name)
                    .replace("__VER__", ver))
        zf.writestr(f"{prefix}使用说明.md", f"# {name}\n\n{desc}\n")
    return pkg


def install_direct(pid, name, ver, desc, extra=None, hotkey=None):
    """直接把插件文件夹写进安装目录（模拟「已安装」态）"""
    d = os.path.join(plugins_dir, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {
        "id": pid, "name": name, "version": ver, "entry": "plugin.py",
        "requires": ["PyQt6"], "description": desc,
        "actions": [{"id": f"{pid}.go", "title": f"{name}动作",
                     "hotkey": hotkey, "menu": True}],
    }
    manifest.update(extra or {})
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(_SRC.replace("__PID__", pid)
                .replace("__TITLE__", f"{name}动作")
                .replace("__DESC__", desc).replace("__NAME__", name)
                .replace("__VER__", ver))
    with open(os.path.join(d, "使用说明.md"), "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\n{desc}\n")


# 商店里：3 个可装 + 1 个已装 + 1 个坏包
make_package("ai-assistant", "AI 助手", "1.2.0",
             "本地 / 云端双后端的对话助手：读应用内数据做总结、分类与问答",
             {"capabilities": ["network", "write"]}, hotkey="Ctrl+Alt+I")
make_package("quick-note", "速记归档", "1.0.3",
             "把选中的文字一键存成笔记或碎片。",
             {"capabilities": ["write"]}, hotkey="Ctrl+Alt+Q")
make_package("color-picker", "屏幕取色器", "1.2",
             "按下热键即可吸取屏幕上任意位置的颜色，复制为 HEX。",
             hotkey="Ctrl+Alt+C")
# 一个「已安装」的：包在商店 + 文件夹在安装目录
make_package("installed-demo", "已装演示", "1.0.0",
             "这个包既在商店、也已解压安装。", hotkey="Ctrl+Alt+Y")
install_direct("installed-demo", "已装演示", "1.0.0",
               "这个包既在商店、也已解压安装。", hotkey="Ctrl+Alt+Y")
with open(os.path.join(store_dir, "broken.fpplug"), "wb") as f:
    f.write(b"not a zip at all")

tmp_cfg = tempfile.mkdtemp()
cm = ConfigManager(os.path.join(tmp_cfg, "config.json"))
cm.set("plugins_enabled", True)

_base = get_base_dir()
docx = DocxManager(get_docx_path(_base), os.path.join(tmp_cfg, "docx_meta.json"))
docx.load()
frags = FragmentManager(os.path.join(tmp_cfg, "fragments.json"))
win = MainWindow(TaskManager(os.path.join(tmp_cfg, "schedule.json")),
                 NoteManager(os.path.join(tmp_cfg, "notes.json")),
                 frags, docx, cm, ClipboardMonitor(frags, cm),
                 temp_asset_manager=TempAssetManager(_base),
                 nav_manager=NavManager(os.path.join(tmp_cfg, "nav.json")))
win.resize(1280, 740)

ctx = PluginContext(logger=None, config=cm.as_dict(),
                    show_toast=win.show_toast,
                    parent_window=lambda: win)
loader = PluginLoader(ActionRegistry(logger=None), ctx,
                      plugins_dir=plugins_dir, logger=None,
                      store_dir=store_dir)
win.set_plugin_loader(loader)
loader.load_all()

win.show_plugins_page()
app.processEvents()

for theme in ("light", "dark"):
    cm.set("theme", theme)
    win._theme = theme
    win._apply_theme()
    win.refresh_page("plugins")
    app.processEvents()
    app.processEvents()
    path = os.path.join(OUT_DIR, f"plugins-store-{theme}.png")
    win.grab().save(path)
    print(f"[OK] saved {path}")

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(tmp_cfg, ignore_errors=True)
print("[DONE]")
