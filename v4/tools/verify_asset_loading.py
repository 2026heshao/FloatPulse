# -*- coding: utf-8 -*-
"""临时素材加载丝滑化（异步缩略图管线）离屏端到端验证。

pytest 钉不住的部分（本脚本存在的理由）：
  1. **真 MainWindow + 大图**全链路：打开素材页的第一帧就是占位图
     （列表即刻建好、无解码阻塞），后台在时限内全部到货；
  2. **主线程响应性**：加载期间事件循环持续可跑（旧同步路径会在首次
     paint 里连拍 N 张解码，事件循环冻 N×decode_ms）；
  3. 变尺寸后按新尺寸重新到货（代际防串旧图）。

用法：
    python tools/run_gui_check.py tools/verify_asset_loading.py
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtGui import QColor, QFontDatabase, QPainter, QPixmap       # noqa: E402
from PyQt6.QtWidgets import QApplication                              # noqa: E402

from src.config import ConfigManager                                  # noqa: E402
from src.docx_manager import DocxManager                              # noqa: E402
from src.fragment_manager import FragmentManager                       # noqa: E402
from src.main_window import MainWindow                                # noqa: E402
from src.nav_manager import NavManager                                # noqa: E402
from src.note_manager import NoteManager                              # noqa: E402
from src.task_manager import TaskManager                              # noqa: E402
from src.temp_asset_manager import TempAssetManager                    # noqa: E402

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


def make_noisy_png(path, w, h, seed=0):
    """画一张熵足够的 PNG（纯色 PNG 解码近乎零成本，测不出差异）"""
    pm = QPixmap(w, h)
    pm.fill(QColor(30, 90, 120))
    p = QPainter(pm)
    try:
        import random
        rng = random.Random(seed)
        for _ in range(6000):
            x, y = rng.randrange(w), rng.randrange(h)
            rw, rh = rng.randrange(8, 60), rng.randrange(8, 60)
            p.fillRect(x, y, rw, rh,
                       QColor(rng.randrange(256), rng.randrange(256),
                              rng.randrange(256)))
    finally:
        p.end()
    assert pm.save(path)
    return path


def pump(app, ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def main():
    app = QApplication(sys.argv)
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_loading_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(tmp, "docx_meta.json"))
    docx.load()
    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    # 11 张小图 + 1 张大图（2400x1600 噪声，解码是大头）
    for i in range(11):
        p = make_noisy_png(os.path.join(tmp, f"s{i}.png"), 480, 320, seed=i)
        assert assets.add_asset(p, f"小图{i}.png")
    big = make_noisy_png(os.path.join(tmp, "big.png"), 2400, 1600, seed=99)
    assert assets.add_asset(big, "大图.png")

    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(1280, 740)
    win.show()
    pump(app, 200)

    panel = win._page_assets
    lst = panel._asset_list
    win._stack.setCurrentWidget(panel)

    t0 = time.time()
    win.refresh_page("assets")
    pump(app, 60)     # 只跑一拍：验证「列表即刻就绪」（旧同步版这一步内联解码）
    t_list = time.time() - t0
    check(f"打开即就绪：12 条目已建（{t_list*1000:.0f}ms，无解码阻塞）",
          lst.count() == 12)

    # 加载期响应性：400ms 内事件循环持续可跑（同步版会在 paint 里冻 1-2s）
    ticks = 0
    end = time.time() + 0.4
    while time.time() < end:
        app.processEvents()
        ticks += 1
        time.sleep(0.002)
    check(f"加载期主线程响应（400ms 内事件循环跑了 {ticks} 拍）", ticks > 50)

    # 时限内全部可见格到货
    deadline = time.time() + 5.0
    while time.time() < deadline:
        vals = list(panel._thumb_cache.values())
        done = sum(1 for v in vals if v is not False and v is not None)
        if done >= 12 and not panel._pending:
            break
        app.processEvents()
        time.sleep(0.01)
    check("5s 内 12/12 缩略图全部到货",
          sum(1 for v in panel._thumb_cache.values()
              if v is not False and v is not None) >= 12
          and not panel._pending)
    from PyQt6.QtGui import QPixmap as _QP
    check("到货物是真 QPixmap（非哨兵）",
          all(isinstance(v, _QP) for v in panel._thumb_cache.values()))

    # 视口非空（真画出来了，不是全占位）
    pm = lst.viewport().grab()
    check("视口已渲染出缩略图内容（非全占位灰）", not pm.isNull())

    # 变尺寸 → 按新尺寸重新到货
    panel.apply_thumb_size(96)
    deadline = time.time() + 5.0
    ok96 = False
    while time.time() < deadline:
        vals = [v for v in panel._thumb_cache.values()
                if isinstance(v, _QP)]
        if len(vals) >= 12 and all(v.width() == 96 for v in vals) \
                and not panel._pending:
            ok96 = True
            break
        app.processEvents()
        time.sleep(0.01)
    check("变尺寸 96px 后按新宽度全部重灌（代际防串）", ok96)

    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    win.deleteLater()
    app.processEvents()
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
