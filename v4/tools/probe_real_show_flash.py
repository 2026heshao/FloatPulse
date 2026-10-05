# -*- coding: utf-8 -*-
"""probe_real_show_flash.py — 真实配置 + 真实窗口的「黑窗闪现」取证。

复刻：启动接力（splash→主窗→球）+ 用户日常「关到托盘→再唤回主窗」循环。
主窗置顶且不抢焦点（WA_ShowWithoutActivating），PIL 以 ~30fps 截屏：
  · 检测 40~700px 的近黑连通块（小黑窗特征）出现/消失时刻
  · 逐帧统计 主窗/闪屏/球 三个矩形内的平均亮度
输出对齐 show/finish/pop 打点的时间线。
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)     # 强制真实窗口

import numpy as np                           # noqa: E402
from PIL import ImageGrab                    # noqa: E402
from PyQt6.QtCore import Qt, QTimer          # noqa: E402
from PyQt6.QtGui import QFontDatabase        # noqa: E402
from PyQt6.QtWidgets import QApplication     # noqa: E402

from src.app_paths import get_data_dir, get_base_dir  # noqa: E402
from src.config import ConfigManager          # noqa: E402
from src.docx_manager import DocxManager      # noqa: E402
from src.task_manager import TaskManager      # noqa: E402
from src.note_manager import NoteManager      # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow        # noqa: E402
from src.splash import LaunchSplash          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "build", "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication(sys.argv)
if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

# ---- 真实配置的副本（保留外观设置，不污染用户配置）----
real_data = get_data_dir(get_base_dir())
tmp = tempfile.mkdtemp(prefix="fp_realshow_")
data = os.path.join(tmp, "data")
os.makedirs(data, exist_ok=True)
for f in ("config.json", "nav.json"):
    src = os.path.join(real_data, f)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(data, f))
cfg = ConfigManager(os.path.join(data, "config.json"))
docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                   os.path.join(data, "docx_meta.json"))
docx.load()

T0 = time.monotonic()
marks = []


def mark(msg):
    marks.append((time.monotonic() - T0, msg))
    print(f"[{time.monotonic() - T0:.3f}s] {msg}", flush=True)


# ---- 复刻启动接力（真实 anim_speed）----
splash = LaunchSplash(anim_speed=2.0)
splash.start()
mark("splash.start")

win = MainWindow(TaskManager(os.path.join(data, "schedule.json")),
                 NoteManager(os.path.join(data, "notes.json")),
                 FragmentManager(os.path.join(data, "fragments.json")),
                 docx, cfg,
                 ClipboardMonitor(FragmentManager(os.path.join(data, "f2.json")), cfg),
                 TempAssetManager(tmp))
win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
win.resize(1280, 740)
mark("main_window built")

from knowledge_ball import FloatingBall  # noqa: E402
ball = FloatingBall([], TaskManager(os.path.join(data, "s2.json")),
                    NoteManager(os.path.join(data, "n2.json")),
                    FragmentManager(os.path.join(data, "f3.json")),
                    docx, cfg, None, win, TempAssetManager(tmp))
mark("ball built")


def _show_main():
    win.show()          # 不 raise_/activate —— 不抢用户焦点
    mark("main_window.show #1")


def _finish_splash():
    splash.finish()
    mark("splash.finish")


def _pop_ball():
    ball.play_startup_pop()
    mark("ball.play_startup_pop")


def _hide_main():
    win.hide()
    mark("main_window.hide")


def _reshow_main():
    win.show()
    mark("main_window.show #2 (托盘唤回模拟)")


QTimer.singleShot(200, _show_main)
QTimer.singleShot(320, _finish_splash)
QTimer.singleShot(360, _pop_ball)
QTimer.singleShot(1600, _hide_main)
QTimer.singleShot(2400, _reshow_main)

end = time.monotonic() + 4.5
frames = []
while time.monotonic() < end:
    frames.append((time.monotonic() - T0, ImageGrab.grab()))
    app.processEvents()
    time.sleep(0.003)
mark(f"截屏 {len(frames)} 帧")

# ---- 分析 ----
DPR = app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1.0
CELL = 16          # 粗粒化单元
MIN_CELLS = 9      # ≥(16*3)^2 ≈ 48px 见方的黑块才认定（过滤文字/图标）


def dark_regions(img):
    """返回 [(bbox, cells)]：40~700px 的近黑连通块（4 邻接 BFS）"""
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    h, w = a.shape
    gh, gw = h // CELL, w // CELL
    grid = (a[:gh * CELL, :gw * CELL].reshape(gh, CELL, gw, CELL)
            .mean(axis=(1, 3)) < 10)
    seen = np.zeros_like(grid, dtype=bool)
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if not grid[gy, gx] or seen[gy, gx]:
                continue
            stack, cells = [(gy, gx)], []
            seen[gy, gx] = True
            while stack:
                cy, cx = stack.pop()
                cells.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if (0 <= ny < gh and 0 <= nx < gw and grid[ny, nx]
                            and not seen[ny, nx]):
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            ys = [c[0] for c in cells]
            xs = [c[1] for c in cells]
            bh = (max(ys) - min(ys) + 1) * CELL
            bw = (max(xs) - min(xs) + 1) * CELL
            if len(cells) >= MIN_CELLS and 40 <= bh <= 700 and 40 <= bw <= 700:
                out.append((min(xs) * CELL, min(ys) * CELL, bw, bh))
    return out


print("\n===== 黑块事件 =====", flush=True)
prev_boxes = None
for i, (t, img) in enumerate(frames):
    boxes = dark_regions(img)
    if boxes and prev_boxes != boxes:
        img.save(os.path.join(OUT, f"realshow-hit-{i:03d}-t{t:.2f}.png"))
        print(f"帧{i:3} t={t:.3f}s 黑块: {boxes}", flush=True)
    prev_boxes = boxes
print("分析完成", flush=True)
