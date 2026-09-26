# -*- coding: utf-8 -*-
"""Offscreen 功能验证：素材缩略图尺寸设置项（asset_thumb_size）。

验证点：
  A. 默认配置 128px → 一行正好 4 个（用户核心诉求）
  B. 设置页步进改值 → 即时持久化 + delegate 尺寸更新 + 缩略图缓存清空重建
  C. 改小尺寸后每行个数增加（一行 ≥5 个）
  D. apply_thumb_size 直调路径：同值短路、变值生效
  E. 配置三件套：默认值在范围内、非法值被 set() 拒绝

运行方式（必须 offscreen 平台）：
  python tools/run_gui_check.py tools/verify_asset_thumb_setting.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QFontDatabase, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402

PASS = 0


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def per_row_count(lst) -> int:
    """第一行元素个数（与第 0 个同 y 的 item 数）"""
    y0 = lst.visualItemRect(lst.item(0)).y()
    return sum(1 for i in range(lst.count())
               if lst.visualItemRect(lst.item(i)).y() == y0)


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_asset_thumb")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_thumb_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config = ConfigManager(os.path.join(data_dir, "config.json"))

    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)

    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr,
                     config, clip, temp_mgr)
    win.show()
    pump(app, 120)

    panel = win._page_assets
    lst = panel._asset_list

    # 造 9 张真 PNG 素材（内容各不相同，避免被哈希去重拦下）
    for i in range(9):
        p = os.path.join(tmp, f"src_{i}.png")
        pm = QPixmap(64, 48)
        pm.fill()
        from PyQt6.QtGui import QPainter, QColor
        pt = QPainter(pm)
        pt.fillRect(8 + i * 4, 8, 20, 20, QColor(20 * i % 256, 255 - 20 * i % 256, 60 + i * 12 % 180))
        pt.end()
        assert pm.save(p), f"PNG {i} 保存失败"
        assert temp_mgr.add_asset(p, f"图{i}.png"), f"素材 {i} 添加失败"

    # 切到素材页并填充（面板构造时不填充，靠 refresh_page）
    win._stack.setCurrentWidget(panel)
    win.refresh_page("assets")
    pump(app, 200)
    assert lst.count() == 9, f"素材条目数异常: {lst.count()}"

    # ---------------- A. 默认 128px → 一行 4 个 ----------------
    dlg = panel._thumb_delegate
    assert dlg.THUMB_W == 128, f"A. 默认缩略图宽应为 128，实际 {dlg.THUMB_W}"
    assert dlg.CELL_W == 148 and dlg.CELL_H == 148, \
        f"A. 单元尺寸异常: {dlg.CELL_W}x{dlg.CELL_H}"
    n_row = per_row_count(lst)
    assert n_row == 4, f"A. 默认应一行 4 个，实际 {n_row}"
    ok(f"A. 默认 128px：一行 {n_row} 个（用户核心诉求达成）")

    # ---------------- B. 设置页步进改值 → 即时生效 ----------------
    sp = win._page_settings._set_asset_thumb
    cache_mark = "marked-for-invalidation"
    panel._thumb_cache[next(iter(panel._thumb_cache))] = cache_mark  # 塞标记
    sp.setValue(96)
    pump(app, 150)
    assert config.get("asset_thumb_size") == 96, \
        f"B. 配置未持久化: {config.get('asset_thumb_size')}"
    assert os.path.exists(config._json_path), "B. 配置未写盘"
    assert dlg.THUMB_W == 96 and dlg.CELL_W == 116, \
        f"B. delegate 尺寸未更新: {dlg.THUMB_W}/{dlg.CELL_W}"
    assert cache_mark not in panel._thumb_cache.values(), "B. 缩略图缓存未清空"
    # 缓存按新尺寸重建（真 QPixmap 且宽度 == 96）
    pix0 = panel._thumb_cache.get(temp_mgr.get_all_assets()[0].asset_id)
    assert isinstance(pix0, QPixmap) and not pix0.isNull() and pix0.width() == 96, \
        "B. 缓存未按新尺寸重建"
    # 设置控件与配置同步（refresh 后不回跳）
    win._page_settings.refresh()
    assert win._page_settings._set_asset_thumb.value() == 96, "B. refresh 后控件值回跳"
    ok("B. 设置步进 96px：即时持久化 + delegate 更新 + 缓存重建 + 控件同步")

    # ---------------- C. 一行个数随尺寸变多 ----------------
    n_row2 = per_row_count(lst)
    assert n_row2 >= 5, f"C. 96px 应一行 ≥5 个，实际 {n_row2}"
    ok(f"C. 缩小尺寸后一行 {n_row2} 个（每行个数随尺寸变化）")

    # ---------------- D. apply_thumb_size 直调 ----------------
    panel.apply_thumb_size(96)          # 同值 → 短路（不重建缓存）
    assert dlg.THUMB_W == 96, "D. 同值调用不应改动"
    panel.apply_thumb_size(160)         # 变值 → 生效
    exp_h = round(160 * dlg.THUMB_RATIO) + 64
    assert dlg.THUMB_W == 160 and dlg.CELL_H == exp_h, \
        f"D. 160px 尺寸推导异常: {dlg.THUMB_W}/{dlg.CELL_H} (期望 CELL_H={exp_h})"
    ok("D. apply_thumb_size：同值短路、变值生效（160px → 单元 180x170）")

    # ---------------- E. 配置三件套行为 ----------------
    assert config.set("asset_thumb_size", 200) is False, "E. 越界值 200 应被拒绝"
    assert config.set("asset_thumb_size", 60) is False, "E. 越界值 60 应被拒绝"
    assert config.set("asset_thumb_size", "big") is False, "E. 类型错误应被拒绝"
    assert config.set("asset_thumb_size", 80) is True, "E. 合法下界应被接受"
    ok("E. 配置校验：范围 80-160 / int 类型，非法值均被拒")

    # 收尾：不在磁盘留下垃圾配置
    print(f"[DONE] {PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
