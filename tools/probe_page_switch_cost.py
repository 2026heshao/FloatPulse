# -*- coding: utf-8 -*-
"""页面切换/刷新耗时实测（2026-10-10，排查「面板快捷键跳页卡 1-2s」）。

用**真实 float_data 数据**建 MainWindow，逐页测量 show_settings_page /
show_page / _switch_page 的真实耗时，找出拖慢的那一页。

用法：python tools/probe_page_switch_cost.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
V4 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "v4")
sys.path.insert(0, V4)

from PyQt6.QtWidgets import QApplication          # noqa: E402

app = QApplication([])

from src.config import ConfigManager               # noqa: E402
from src.docx_manager import DocxManager           # noqa: E402
from src.fragment_manager import FragmentManager   # noqa: E402
from src.task_manager import TaskManager           # noqa: E402
from src.note_manager import NoteManager           # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow             # noqa: E402

REAL = os.path.join(os.path.dirname(V4), "float_data")
tmp = tempfile.mkdtemp(prefix="fp_switch_probe_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)


def main():
    # 配置用真实一份（只读复制），数据用真实 json 的副本，避免污染原数据
    import shutil
    cfg_src = os.path.join(REAL, "config.json")
    cfg_dst = os.path.join(tmp, "config.json")
    if os.path.exists(cfg_src):
        shutil.copy2(cfg_src, cfg_dst)
    for name, dst in (("fragments.json", data_dir), ("schedule.json", data_dir),
                      ("notes.json", data_dir), ("temp_assets.json", data_dir)):
        src = os.path.join(REAL, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, name))

    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    config = ConfigManager(cfg_dst)
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)

    t0 = time.perf_counter()
    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                     clip, temp_mgr)
    print(f"MainWindow 构建: {(time.perf_counter()-t0)*1000:.0f}ms "
          f"（碎片 {len(frag_mgr.get_all_fragments())} 条）")

    win.show()
    app.processEvents()
    time.sleep(0.4)
    app.processEvents()

    # 等预热跑完（否则第一跳会把预热成本算进来）
    for _ in range(40):
        app.processEvents()
        time.sleep(0.03)

    pages = [("fragments", 0), ("tasks", 1), ("notes", 2), ("knowledge", 3),
             ("assets", 4), ("nav", 5), ("settings", 6), ("apps", 7),
             ("plugins", 9)]
    print("\n-- 首跳（懒加载已预热，测纯切换+刷新）--")
    for name, idx in pages:
        win.hide()
        app.processEvents()
        t0 = time.perf_counter()
        win.show_page(idx)
        app.processEvents()
        ms = (time.perf_counter() - t0) * 1000
        flag = "  <<< 慢" if ms > 200 else ""
        print(f"  show_page({idx:2d}) {name:10s}: {ms:7.0f}ms{flag}")

    print("\n-- 直接走热键路径 show_settings_page（用户绑 Ctrl+Q）--")
    for _ in range(3):
        win.hide()
        app.processEvents()
        t0 = time.perf_counter()
        win.show_settings_page("clipboard")
        app.processEvents()
        ms = (time.perf_counter() - t0) * 1000
        print(f"  show_settings_page: {ms:7.0f}ms")

    print("\n-- 已可见时重复跳页（模拟面板回车跳转）--")
    for name, idx in pages:
        t0 = time.perf_counter()
        win.show_page(idx)
        app.processEvents()
        ms = (time.perf_counter() - t0) * 1000
        flag = "  <<< 慢" if ms > 200 else ""
        print(f"  show_page({idx:2d}) {name:10s}: {ms:7.0f}ms{flag}")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
