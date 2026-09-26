# -*- coding: utf-8 -*-
"""主窗口 show 性能剖析：定位双击球重开主窗口时 UI 冻结数秒的元凶。

方法：
  1. 用真实 data/ 数据构建 MainWindow（与 test_init 同路径，配置用临时副本）
  2. 计时各阶段：构造 / 首次 show / 事件循环泵 2s / hide+再次 show
  3. QTimer(50ms) 漂移检测：事件循环被阻塞 >300ms 的区段打印告警
  4. cProfile 抓整个流程的热点函数，按 cumtime 输出 Top 30
"""
import cProfile
import io
import os
import pstats
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QElapsedTimer, QTimer
from PyQt6.QtWidgets import QApplication

from src.config import ConfigManager
from src.docx_manager import DocxManager
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow

T0 = time.perf_counter()


def mark(label):
    print(f"[{time.perf_counter() - T0:8.3f}s] {label}", flush=True)


# ---------------- 漂移检测 ----------------
class DriftDetector:
    """每 50ms 跳动一次；相邻两次间隔 >300ms 视为事件循环被阻塞"""

    def __init__(self):
        self.last = None
        self.worst = 0.0
        self.timer = QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        now = time.perf_counter()
        if self.last is not None:
            gap = (now - self.last) * 1000
            self.worst = max(self.worst, gap)
            if gap > 300:
                print(f"    !! 事件循环阻塞 {gap:.0f}ms", flush=True)
        self.last = now


def main():
    app = QApplication(sys.argv)
    from src.app_paths import get_base_dir
    base_dir = get_base_dir()
    data_dir = os.path.join(base_dir, "data")

    real_cfg = os.path.join(data_dir, "config.json")
    tmp_dir = tempfile.mkdtemp(prefix="fp_prof_")
    test_cfg = os.path.join(tmp_dir, "config.json")
    if os.path.exists(real_cfg):
        shutil.copy2(real_cfg, test_cfg)
    config = ConfigManager(test_cfg)

    mark("config ready")
    docx_mgr = DocxManager(
        os.path.join(base_dir, "知识库.docx"),
        os.path.join(data_dir, "docx_meta.json"),
    )
    docx_mgr.load()
    mark(f"docx loaded ({len(docx_mgr.get_cards())} cards)")

    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    temp_mgr = TempAssetManager(base_dir)
    mark(f"managers ready (tasks={len(task_mgr.get_all_tasks())}, "
         f"notes={len(note_mgr.get_all_notes())}, frags={frag_mgr.count()})")

    clip = ClipboardMonitor(frag_mgr, config)

    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr)
    mark(f"MainWindow.__init__ done")

    drift = DriftDetector()

    # ---- 首次 show + 泵 2s 事件循环 ----
    t = QElapsedTimer()
    t.start()
    win.show()
    mark(f"first show() returned in {t.elapsed()}ms")

    drift.timer.start()
    t.start()
    loop = {"n": 0}

    def pump_stop():
        loop["n"] += 1
        if loop["n"] >= 40:          # 40 x 50ms = 2s
            drift.timer.stop()
            mark(f"2s event loop done, worst gap = {drift.worst:.0f}ms")
            phase2()

    pump = QTimer()
    pump.setInterval(50)
    pump.timeout.connect(pump_stop)
    pump.start()
    app.exec() if False else None      # 不进 exec，用 processEvents 泵

    def pump_loop():
        end = time.perf_counter() + 2.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)
        mark(f"2s event loop done, worst gap = {drift.worst:.0f}ms")
        phase2()

    QTimer.singleShot(0, pump_loop)

    # ---- hide → 再次 show（模拟双击球重开）----
    def phase2():
        t.start()
        win.hide()
        hide_ms = t.elapsed()
        app.processEvents()
        t.start()
        win.show()
        win.raise_()
        win.activateWindow()
        mark(f"re-show: hide={hide_ms}ms, show+raise+activate={t.elapsed()}ms")

        end = time.perf_counter() + 1.5
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)
        mark(f"post-reshow loop done, worst gap = {drift.worst:.0f}ms")
        print("PROFILE-DONE", flush=True)
        app.quit()

    prof = cProfile.Profile()
    prof.enable()
    app.exec()
    prof.disable()

    s = io.StringIO()
    ps = pstats.Stats(prof, stream=s).sort_stats("cumulative")
    ps.print_stats(30)
    out = s.getvalue()
    # 只保留有信息量的行
    for line in out.splitlines():
        if line.strip() and ("cumtime" in line or "/" in line or "{" in line):
            print(line[:200], flush=True)


if __name__ == "__main__":
    main()
