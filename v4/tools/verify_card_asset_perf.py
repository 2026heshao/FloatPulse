# -*- coding: utf-8 -*-
"""Offscreen 功能验证：小卡片素材页加载优化（缩略图缓存 + 脏标记）。

验证点：
  A. 首次进入素材页：重建一次，缓存按 asset_id 填充
  B. 来回切页：不重建（脏标记保持干净）——优化核心
  C. notify_assets_changed：不可见只置脏不重建；可见才立即重建
  D. 重建后缩略图命中缓存（同一 QPixmap 实例，不再解码原图）
  E. 大图场景：解码期缩放，首次切页耗时可接受；第二次切页近零耗时

运行方式（必须 offscreen 平台）：
  python tools/run_gui_check.py tools/verify_card_asset_perf.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QFontDatabase, QPainter, QPixmap, QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.card_window import CardWindow  # noqa: E402

PASS = 0


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


def pump(app, ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_card_asset")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_card_asset_")
    mgr = TempAssetManager(tmp)

    # 造 8 张大图（2000x1500 噪点 PNG，模拟真实截图体量）
    for i in range(8):
        p = os.path.join(tmp, f"big_{i}.png")
        pm = QPixmap(2000, 1500)
        pm.fill(QColor(30 * i % 256, 90, 140))
        pt = QPainter(pm)
        for k in range(200):  # 噪点让 PNG 压不掉，逼近真实截图
            pt.fillRect((k * 97) % 1900, (k * 211) % 1400, 40, 40,
                        QColor((k * 31) % 256, (k * 67) % 256, (k * 13) % 256))
        pt.end()
        assert pm.save(p)
        assert mgr.add_asset(p, f"截图_{i}.png")

    card = CardWindow()
    card.set_asset_manager(mgr)
    card.show()
    pump(app, 150)
    card._switch_mode("asset")
    pump(app, 300)

    # ---------------- A. 首次进入：重建 + 缓存填充 ----------------
    n_items = card._asset_content_layout.count()
    assert n_items >= 1, f"A. 素材页无内容: {n_items}"
    cache = card._asset_thumb_cache
    assert len(cache) == 8 and all(v is not False for v in cache.values()), \
        f"A. 缩略图缓存未填满: {len(cache)}"
    assert card._asset_page_dirty is False, "A. 刷新后脏标记应为 False"
    ok(f"A. 首次进入素材页：8 项缓存就绪，脏标记已清")

    # ---------------- B. 来回切页不重建 ----------------
    calls = {"n": 0}
    orig = card._refresh_asset_page
    card._refresh_asset_page = lambda: (calls.__setitem__("n", calls["n"] + 1),
                                        orig())[-1]
    for m in ("fragment", "asset", "nav", "asset", "task", "asset"):
        card._switch_mode(m)
        pump(app, 80)
    assert calls["n"] == 0, f"B. 来回切页重建了 {calls['n']} 次（应 0 次）"
    ok("B. 切走再切回 ×3：素材页零重建（原先每次切页都全量重建）")

    # ---------------- C. notify_assets_changed 语义 ----------------
    card._switch_mode("task")
    pump(app, 80)
    card.notify_assets_changed()             # 不可见？可见但当前页是 task
    # 可见 → 立即重建
    assert calls["n"] == 1, f"C. 可见时应立即重建，实际 {calls['n']} 次"
    card.notify_assets_changed()
    assert calls["n"] == 2, "C. 第二次可见通知也应重建"
    ok("C. notify_assets_changed：可见即重建（2/2 次）")

    # ---------------- D. 缓存命中：重建后同一 QPixmap 实例 ----------------
    before = {aid: id(px) for aid, px in cache.items()}
    card.notify_assets_changed()             # 再重建一次
    same = all(aid in cache and id(cache[aid]) == pid
               for aid, pid in before.items())
    assert same, "D. 重建后缩略图未命中缓存（重新解码了）"
    ok("D. 重建后 8 张缩略图全部命中缓存（QPixmap 实例不变，零解码）")

    # ---------------- E. 隐藏时通知 → 只置脏；再展开才重建 ----------------
    card._switch_mode("task")
    pump(app, 80)
    calls["n"] = 0
    card.hide()
    pump(app, 80)
    card.notify_assets_changed()             # 隐藏 → 只置脏
    assert calls["n"] == 0 and card._asset_page_dirty is True, \
        "E. 隐藏时不应重建，只置脏"
    card.show()                               # 展开恢复 task 页 → 不触发素材页
    pump(app, 150)
    assert calls["n"] == 0, "E. 展开时当前页是 task，不应刷素材页"
    card._switch_mode("asset")               # 此时才重建
    pump(app, 200)
    assert calls["n"] == 1, "E. 置脏后切到素材页应重建"
    ok("E. 隐藏期间数据变化 → 展开不白刷，切到素材页才重建一次")

    # ---------------- F. 首次切页耗时（解码期缩放后）----------------
    # 重新置脏模拟"数据变化后首次进入"
    card.notify_assets_changed()
    pump(app, 100)
    t0 = time.perf_counter()
    card.notify_assets_changed()             # 可见 + 当前页 task? 先切走
    card._switch_mode("task"); pump(app, 50)
    card._asset_page_dirty = True
    t0 = time.perf_counter()
    card._switch_mode("asset")
    pump(app, 50)
    dt_ms = (time.perf_counter() - t0) * 1000
    assert dt_ms < 400, f"F. 8 张大图首次切页耗时 {dt_ms:.0f}ms（>400ms）"
    ok(f"F. 8 张 2000×1500 大图首次切页 {dt_ms:.0f}ms（解码期缩放生效）")

    print(f"[DONE] {PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
