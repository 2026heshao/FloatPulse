# -*- coding: utf-8 -*-
"""悬浮球拖入 exe/lnk → 应用启动器（LaunchDeck 同款交互）验证。

A make_app_from_path：.lnk 存本身/名称=文件名、.exe 名称回退、其他类型拒绝
B launch_app .lnk 分支：走 os.startfile（patch 验证，不真启动）
C dropEvent 分流：exe/lnk 进 config["apps"] + 去重 + 刷新主窗口软件页(7)；
  txt 照旧进素材/碎片替身；重复拖入跳过并提示
D config 落盘校验（重开 ConfigManager 读取）

跑法：python tools/run_gui_check.py tools/verify_ball_drop_apps.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPointF, QUrl, Qt
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_app = QApplication.instance() or QApplication(sys.argv)  # 离屏必须先建 app

import knowledge_ball as kb
from src import widget_app_launcher
from src.config import ConfigManager

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


# ---- 造测试文件（临时目录，不碰真实 data/）----
tmp = tempfile.mkdtemp(prefix="fp_drop_")


def same_path(a: str, b: str) -> bool:
    """斜杠形式无关的路径比较（QUrl 往返 + os.path.join 会混用 / 与 \\）。"""
    return os.path.normpath(a).lower() == os.path.normpath(b).lower()
lnk_path = os.path.join(tmp, "测试软件.lnk")
with open(lnk_path, "w", encoding="utf-8") as f:
    f.write("fake lnk")
exe_path = os.path.join(tmp, "portable.exe")
with open(exe_path, "wb") as f:
    f.write(b"MZ")
txt_path = os.path.join(tmp, "note.txt")
with open(txt_path, "w", encoding="utf-8") as f:
    f.write("hello")

cfg = ConfigManager(os.path.join(tmp, "config.json"))

# ================= A. make_app_from_path =================
app = widget_app_launcher.make_app_from_path(lnk_path)
check("A1 lnk 存本身且名称=文件名",
      app == {"name": "测试软件", "exe_path": lnk_path}, repr(app))

app2 = widget_app_launcher.make_app_from_path(exe_path)
check("A2 exe 无版本资源→名称回退文件名",
      app2 == {"name": "portable", "exe_path": exe_path}, repr(app2))

check("A3 txt 拒绝（不进启动器）",
      widget_app_launcher.make_app_from_path(txt_path) is None)
check("A4 路径不存在拒绝",
      widget_app_launcher.make_app_from_path(
          os.path.join(tmp, "no.exe")) is None)

# ================= B. launch_app .lnk 分支 =================
calls = []
_orig_startfile = os.startfile
os.startfile = lambda p, *a, **k: calls.append(p)
try:
    ok = widget_app_launcher.launch_app(
        {"name": "测试软件", "exe_path": lnk_path}, parent=None)
finally:
    os.startfile = _orig_startfile
check("B1 lnk 走 os.startfile 异步打开",
      ok is True and calls == [lnk_path], f"ok={ok} calls={calls}")


# ================= C. dropEvent 分流（注入替身 + 真实回调） =================
class StubFrag:
    def __init__(self):
        self.picks = []

    def add_file_pickup(self, p):
        self.picks.append(p)


class StubAssets:
    def __init__(self):
        self.adds = []

    def add_asset(self, p):
        self.adds.append(p)
        return 1


class StubMW:
    def __init__(self):
        self.refreshed = []

    def refresh_temp_assets(self):
        pass

    def refresh_fragments(self):
        pass

    def _refresh_page(self, i):
        self.refreshed.append(i)


frag, assets, mw = StubFrag(), StubAssets(), StubMW()
ball = kb.FloatingBall([], fragment_manager=frag, config_manager=cfg,
                       temp_asset_manager=assets, main_window=mw)

# Toast 落记录，不真弹窗（离屏无字体/无焦点）
toasts = []
kb.ScreenToast.show_msg = lambda text, theme, dur=None, *a, **k: \
    toasts.append(text)


def drop(paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    ev = QDropEvent(QPointF(0, 0), Qt.DropAction.CopyAction, md,
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    ball.dropEvent(ev)


drop([lnk_path, exe_path, txt_path])

apps_now = cfg.get("apps", [])
check("C1 apps 收录 lnk+exe 共 2 条", len(apps_now) == 2, repr(apps_now))
check("C2 lnk 条目字段正确",
      any(same_path(a.get("exe_path", ""), lnk_path)
          and a.get("name") == "测试软件" for a in apps_now))
check("C3 txt 照旧进碎片替身",
      len(frag.picks) == 1 and same_path(frag.picks[0], txt_path),
      repr(frag.picks))
check("C4 txt 照旧进素材替身",
      len(assets.adds) == 1 and same_path(assets.adds[0], txt_path),
      repr(assets.adds))
check("C5 主窗口软件页刷新 _refresh_page(7)", mw.refreshed == [7],
      repr(mw.refreshed))
check("C6 toast 文案（数量+去处）",
      any("已添加 2 个应用到启动器" in t for t in toasts), repr(toasts))

drop([lnk_path])  # 重复拖入
check("C7 去重：重复 exe_path 不再添加",
      len(cfg.get("apps", [])) == 2)
check("C8 重复拖入提示（已在启动器中，跳过）",
      any("已在启动器中，跳过" in t for t in toasts), repr(toasts))

# ================= D. config 落盘 =================
cfg2 = ConfigManager(os.path.join(tmp, "config.json"))
check("D1 config.json 落盘 apps=2", len(cfg2.get("apps", [])) == 2)

# ---- 汇总 ----
failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
