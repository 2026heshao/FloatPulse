# -*- coding: utf-8 -*-
"""probe_toplevel_show.py — 抓「无父级部件被 show 成顶层窗口」（黑窗闪现元凶）。

历史同因：nav 按钮未带父级时 setVisible(True) → 顶层窗口闪现一排小黑窗
（2026-09-29）；nav_panel 空态无 parent → 左上角闪黑框（2026-09-24）。
用户再报：主窗口弹出瞬间有一颗小黑窗闪动后消失。本探针猴子补丁
QWidget.setVisible，凡 parentWidget() 为 None 的部件要被显示就打印
类型/objectName/调用栈，再人工甄别哪个是无意顶层窗。
"""
import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402
from PyQt6.QtGui import QFontDatabase  # noqa: E402

_orig_setVisible = QWidget.setVisible
_shown_toplevels = []


def traced_setVisible(self, on):
    try:
        if on and self.parentWidget() is None and self.isWindow():
            name = self.objectName()
            key = (type(self).__name__, name)
            _shown_toplevels.append(key)
            print(f"\n[TOP-LEVEL SHOW] {type(self).__name__} "
                  f"objectName={name!r} visibleChildren={self.isVisible()}",
                  flush=True)
            stack = traceback.extract_stack(limit=10)[:-1]
            for fr in stack[-6:]:
                print(f"    {os.path.basename(fr.filename)}:{fr.lineno} "
                      f"{fr.name}", flush=True)
    except RuntimeError:
        pass
    return _orig_setVisible(self, on)


QWidget.setVisible = traced_setVisible


def main():
    app = QApplication(sys.argv)
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
    tmp = tempfile.mkdtemp(prefix="fp_tlshw_")
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    from src.config import ConfigManager
    from src.docx_manager import DocxManager
    from src.task_manager import TaskManager
    from src.note_manager import NoteManager
    from src.fragment_manager import FragmentManager
    from src.clipboard_monitor import ClipboardMonitor
    from src.temp_asset_manager import TempAssetManager
    from src.main_window import MainWindow

    cfg = ConfigManager(os.path.join(data, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data, "docx_meta.json"))
    docx.load()
    win = MainWindow(TaskManager(os.path.join(data, "schedule.json")),
                     NoteManager(os.path.join(data, "notes.json")),
                     FragmentManager(os.path.join(data, "fragments.json")),
                     docx, cfg,
                     ClipboardMonitor(FragmentManager(os.path.join(data, "f2.json")), cfg),
                     TempAssetManager(tmp))
    win.resize(1280, 740)

    print("\n===== 第一次 show（启动） =====", flush=True)
    win.show()
    end = time.time() + 2.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)

    print("\n===== hide → 再 show（再次打开主窗口） =====", flush=True)
    win.hide()
    end = time.time() + 0.3
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
    win.show()
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)

    # 汇总：全部被显示过的无父级顶层部件（去重计数）
    from collections import Counter
    print("\n===== 汇总 =====", flush=True)
    for (cls, name), n in Counter(_shown_toplevels).most_common():
        print(f"  {n:3}× {cls} {name!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
