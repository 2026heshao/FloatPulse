# -*- coding: utf-8 -*-
"""插件页新元素真实截图（light / dark）——卸载按钮 + 写能力标记 + 版本失败卡片。

跑法：python tools/run_gui_check.py tools/shot_plugins_p0p1.py
产物：build/shots/plugins-p0p1-{light,dark}.png
"""
import os
import sys
import json
import shutil
import tempfile

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
from src.clipboard_monitor import ClipboardMonitor            # noqa: E402
from src.app_paths import get_base_dir, get_docx_path  # noqa: E402

OUT_DIR = os.path.join(BASE, "..", "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

root = tempfile.mkdtemp(prefix="fp_shot_p0p1_")

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


def mk(pid, name, ver, desc, manifest_extra=None, hotkey=None):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {
        "id": pid, "name": name, "version": ver, "entry": "plugin.py",
        "requires": ["PyQt6"],
        "description": desc,
        "actions": [{"id": f"{pid}.go", "title": f"{name}动作",
                     "hotkey": hotkey, "menu": True}],
    }
    manifest.update(manifest_extra or {})
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(_SRC.replace("__PID__", pid).replace("__TITLE__", f"{name}动作")
                .replace("__DESC__", desc).replace("__NAME__", name)
                .replace("__VER__", ver))
    return d


# 1) 声明 network + write（两张能力标记）
mk("ai-assistant", "AI 助手", "1.2.0",
   "本地 / 云端双后端的对话助手：读应用内数据做总结、分类与问答",
   {"capabilities": ["network", "write"], "page": {"title": "🤖 AI 助手"}},
   hotkey="Ctrl+Alt+I")
# 2) 仅 write
mk("quick-note", "速记归档", "1.0.3",
   "把选中的文字一键存成笔记或碎片。",
   {"capabilities": ["write"]}, hotkey="Ctrl+Alt+Q")
# 3) 无能力（对照：不显示能力行）
mk("color-picker", "屏幕取色器", "1.2",
   "按下热键即可吸取屏幕上任意位置的颜色，复制为 HEX。",
   hotkey="Ctrl+Alt+C")
# 4) 版本不匹配（新失败阶段卡片）
mk("future-plugin", "未来插件", "9.9.9",
   "用到了更新版本的宿主契约。", {"api_version": 99})

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
                      plugins_dir=root, logger=None)
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
    path = os.path.join(OUT_DIR, f"plugins-p0p1-{theme}.png")
    win.grab().save(path)
    print(f"[OK] saved {path}")

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(tmp_cfg, ignore_errors=True)
print("[DONE]")
