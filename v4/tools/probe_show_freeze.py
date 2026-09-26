# -*- coding: utf-8 -*-
"""复现探针：全真启动（球+主窗口+热键+剪贴板监视），
模拟「双击悬浮球重开主窗口」，用 UI 心跳看门狗量化事件循环阻塞。

看门狗：QTimer 每 250ms 一跳；相邻两跳间隔 >800ms 时打日志
（QTimer 在事件循环里调度，循环被阻塞时下一跳会迟到，间隔=阻塞时长）。
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.config import ConfigManager
from src.docx_manager import DocxManager
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow
from knowledge_ball import FloatingBall

T0 = time.perf_counter()


def mark(msg):
    print(f"[{time.perf_counter() - T0:8.3f}s] {msg}", flush=True)


def main():
    app = QApplication(sys.argv)
    from src.app_paths import get_base_dir, get_data_dir, get_docx_path
    base_dir = get_base_dir()
    data_dir = get_data_dir(base_dir)

    real_cfg = os.path.join(data_dir, "config.json")
    tmp_dir = tempfile.mkdtemp(prefix="fp_probe_")
    test_cfg = os.path.join(tmp_dir, "config.json")
    if os.path.exists(real_cfg):
        shutil.copy2(real_cfg, test_cfg)
    config = ConfigManager(test_cfg)

    docx_mgr = DocxManager(
        get_docx_path(base_dir),
        os.path.join(data_dir, "docx_meta.json"),
    )
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    temp_mgr = TempAssetManager(base_dir)
    clip = ClipboardMonitor(frag_mgr, config)
    clip.start()

    main_win = MainWindow(
        task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr,
    )
    ball = FloatingBall(
        docx_mgr.get_cards(), task_mgr, note_mgr,
        frag_mgr, docx_mgr, config, clip, main_win, temp_mgr,
    )
    mark("boot complete (ball + main window constructed)")

    # ---- UI 心跳看门狗 ----
    state = {"last": time.perf_counter(), "worst": 0.0}

    def heartbeat():
        now = time.perf_counter()
        gap = (now - state["last"]) * 1000
        state["last"] = now
        if gap > 800:
            state["worst"] = max(state["worst"], gap)
            mark(f"!!! UI 线程阻塞 {gap:.0f}ms")

    hb = QTimer()
    hb.setInterval(250)
    hb.timeout.connect(heartbeat)
    hb.start()

    # ---- 场景：显示 → 隐藏 → 模拟双击重开 x2 ----
    phase = {"n": 0}

    def step():
        phase["n"] += 1
        n = phase["n"]
        if n == 1:
            main_win.show()                 # 启动期首显（对应用户开机）
            mark("step1: main window first show")
            QTimer.singleShot(2500, step)
        elif n == 2:
            main_win.hide()
            app.processEvents()
            mark("step2: main window hidden (back to ball-only)")
            QTimer.singleShot(1500, step)
        elif n in (3, 5):
            t = time.perf_counter()
            ball._open_main_window()        # 与双击完全同路径
            mark(f"step{n}: _open_main_window returned "
                 f"(took {(time.perf_counter() - t) * 1000:.0f}ms)")
            QTimer.singleShot(4000, step)
        elif n == 4:
            main_win.hide()
            app.processEvents()
            mark("step4: hidden again")
            QTimer.singleShot(1500, step)
        else:
            mark(f"done, worst UI block = {state['worst']:.0f}ms")
            app.quit()

    QTimer.singleShot(800, step)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
