# -*- coding: utf-8 -*-
"""离屏功能验证：主窗口默认尺寸调大 + 窗口几何记忆（2026-09-27）。

覆盖：
  A. 尺寸常量：默认 1280×740 / 最小 920×620，且最小 < 默认（两者解耦）
  B. 默认尺寸按屏幕可用工作区钳制：大屏用默认值、中屏收缩、小屏不低于最小值
  C. 首次启动（配置无记忆）→ 用默认尺寸并居中
  D. 尺寸/位置变化 → 防抖落盘，写入 "x,y,w,h" 且与实际几何一致
  E. 几何未变化 → 不重复写盘（省 I/O）
  F. 非法/损坏记忆值（空串 / "abc" / "1,2" / 小于最小尺寸）→ 回退默认，不崩
  G. 有效记忆值 → 窗口按记忆几何恢复（覆盖 _init_window 真实启动路径）
  H. 记忆值超出工作区（换小屏 / 拔外接屏）→ 尺寸与位置均被钳制进工作区
  I. 最大化状态 → 不落盘（避免把最大化尺寸记成正常尺寸）
  J. 不可见时 save_geometry_now() 仍能落盘（退出兜底）

屏幕几何由替身注入（offscreen 平台默认 800×800，无法代表真实分辨率），
playbook：off 屏一律 python tools/run_gui_check.py <本脚本>
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import src.main_window as mw  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402

PASS = 0
_REAL_SCREEN = mw.get_screen_geometry   # 保留原始函数，测试中途换替身


def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


def pump(app, ms=0):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def fake_screen(rect: QRect):
    """注入屏幕可用工作区替身（影响默认尺寸钳制 / 恢复钳制 / 居中）"""
    mw.get_screen_geometry = lambda: QRect(rect)


def build_window(tmp: str, config: ConfigManager):
    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(tmp, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(tmp, "schedule.json"))
    note_mgr = NoteManager(os.path.join(tmp, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(tmp, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)
    return MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr,
                      config, clip, temp_mgr)


def main():
    global PASS
    app = QApplication(sys.argv)
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_geom_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    cfg_path = os.path.join(data_dir, "config.json")

    # ---------------- A. 尺寸常量 ----------------
    assert MainWindow.DEFAULT_WIDTH == 1280, f"A. 默认宽 {MainWindow.DEFAULT_WIDTH}"
    assert MainWindow.DEFAULT_HEIGHT == 740, f"A. 默认高 {MainWindow.DEFAULT_HEIGHT}"
    assert MainWindow.MIN_WIDTH == 920, f"A. 最小宽 {MainWindow.MIN_WIDTH}"
    assert MainWindow.MIN_HEIGHT == 620, f"A. 最小高 {MainWindow.MIN_HEIGHT}"
    assert MainWindow.MIN_WIDTH < MainWindow.DEFAULT_WIDTH, \
        "A. 最小尺寸必须小于默认尺寸（两者解耦，窗口可缩小）"
    assert MainWindow.MIN_HEIGHT < MainWindow.DEFAULT_HEIGHT, \
        "A. 最小尺寸必须小于默认尺寸"
    ok("A. 常量：默认 1280×740 / 最小 920×620（解耦，可缩小）")

    # ---------------- B. 默认尺寸按工作区钳制 ----------------
    config = ConfigManager(cfg_path)
    win = build_window(tmp, config)
    try:
        fake_screen(QRect(0, 0, 1536, 816))          # 用户真实屏（125% 缩放下逻辑分辨率）
        assert win._compute_default_size() == (1280, 740), \
            f"B. 放得下时应原样使用默认值，实际 {win._compute_default_size()}"

        fake_screen(QRect(0, 0, 1024, 768))          # 中屏：宽不够 → 收缩；高够 → 不缩
        assert win._compute_default_size() == (944, 740), \
            f"B. 中屏宽应收缩到 944、高保持 740，实际 {win._compute_default_size()}"

        fake_screen(QRect(0, 0, 800, 600))           # 小屏：不低于最小尺寸
        assert win._compute_default_size() == (920, 620), \
            f"B. 小屏应回落到最小尺寸 920×620，实际 {win._compute_default_size()}"
        ok("B. 默认尺寸钳制：1536×816→1280×740 / 1024×768→944×740 / 800×600→920×620")

        # ---------------- C. 无记忆 → 默认尺寸居中 ----------------
        fake_screen(QRect(0, 0, 1536, 816))
        config.set("main_window_geometry", "")
        assert win._restore_saved_geometry() is False, "C. 空串记忆不应恢复几何"
        win.resize(*win._compute_default_size())
        win._ensure_on_screen(init=True)
        g = win.geometry()
        assert (g.width(), g.height()) == (1280, 740), f"C. 尺寸 {g.width()}x{g.height()}"
        assert (g.x(), g.y()) == (128, 38), f"C. 居中位置应为 (128,38)，实际 ({g.x()},{g.y()})"
        ok(f"C. 无记忆启动：{g.width()}×{g.height()} 居中于 ({g.x()},{g.y()})")

        # ---------------- F. 非法记忆值容错 ----------------
        for bad in ("", "abc", "1,2", "0,0,10,10", "1,2,3,4,5", None):
            config.set("main_window_geometry", bad if bad is not None else "")
            assert win._restore_saved_geometry() is False, f"F. 非法值 {bad!r} 不应恢复几何"
        ok("F. 非法记忆值（空串/abc/字段不足/小于最小尺寸）全部回退默认，不崩")

        # ---------------- H. 记忆值超屏 → 钳制 ----------------
        fake_screen(QRect(0, 0, 1536, 816))
        config.set("main_window_geometry", "100,50,2000,1200")
        assert win._restore_saved_geometry() is True, "H. 超屏记忆值应被钳制而非丢弃"
        g = win.geometry()
        assert (g.width(), g.height()) == (1536, 816), f"H. 尺寸应钳到工作区，实际 {g.width()}x{g.height()}"
        assert (g.x(), g.y()) == (0, 0), f"H. 位置应钳回工作区，实际 ({g.x()},{g.y()})"
        ok("H. 超屏记忆值 2000×1200 → 钳制为 1536×816 且位置回到 (0,0)")

        # ---------------- G. 有效记忆 → 真实启动路径恢复 ----------------
        config.set("main_window_geometry", "120,60,1100,700")
        config.save()
        config2 = ConfigManager(cfg_path)        # 重新读取磁盘，模拟下次启动
        assert config2.get("main_window_geometry") == "120,60,1100,700", "G. 记忆值未落盘"
        win2 = build_window(tmp, config2)
        try:
            g = win2.geometry()
            assert (g.x(), g.y(), g.width(), g.height()) == (120, 60, 1100, 700), \
                f"G. 启动未恢复记忆几何，实际 {g.x()},{g.y()},{g.width()},{g.height()}"
            ok("G. 重启恢复记忆几何：120,60,1100,700（走 _init_window 真实路径）")

            # ---------------- D. 变化 → 防抖落盘 ----------------
            save_calls = []
            real_save = config2.save

            def counting_save():
                save_calls.append(1)
                real_save()

            config2.save = counting_save
            win2.show()
            # 等窗口入场动画播完：动画未结束时 move() 会被动画终值覆盖
            pump(app, 1200)
            win2.resize(1150, 720)
            win2.move(180, 80)
            pump(app, 900)                        # 超过 GEOMETRY_SAVE_DELAY
            assert win2.pos().x() == 180 and win2.pos().y() == 80, \
                f"D. 前置条件不成立：窗口未真正移动，pos={win2.pos()}"
            g = win2.geometry()
            expect = f"{g.x()},{g.y()},{g.width()},{g.height()}"
            raw = config2.get("main_window_geometry", "")
            assert raw == expect, f"D. 落盘值 {raw!r} 与实际几何 {expect!r} 不一致"
            assert raw.endswith("1150,720"), f"D. 落盘尺寸异常：{raw!r}"
            assert len(save_calls) == 1, f"D. 防抖应只写 1 次，实际 {len(save_calls)} 次"

            # ---------------- E. 未变化 → 不写盘 ----------------
            win2._save_geometry()
            assert len(save_calls) == 1, "E. 几何未变化时不应重复写盘"
            ok(f"D+E. 防抖落盘 1 次（180,80,1150,720）；重复调用不写盘")

            # ---------------- I. 最大化不落盘 ----------------
            config2.set("main_window_geometry", "")
            real_is_max = win2.isMaximized
            win2.isMaximized = lambda: True       # 替身：模拟最大化态
            try:
                win2._save_geometry()
                assert config2.get("main_window_geometry", "") == "", \
                    "I. 最大化时不应把最大化尺寸记成正常尺寸"
            finally:
                win2.isMaximized = real_is_max
            ok("I. 最大化状态不落盘（保留最大化前的几何记忆）")

            # ---------------- J. 不可见也能强制落盘 ----------------
            win2.showNormal()
            pump(app, 400)
            win2.resize(1000, 680)
            win2.move(150, 70)
            pump(app, 60)
            win2.hide()
            pump(app, 60)
            win2.save_geometry_now()
            g = win2.geometry()
            raw = config2.get("main_window_geometry", "")
            assert raw == f"{g.x()},{g.y()},{g.width()},{g.height()}", \
                f"J. 退出兜底落盘值与实际几何不一致：{raw!r}"
            assert raw.endswith("1000,680"), f"J. 尺寸异常：{raw!r}"
            ok(f"J. 窗口不可见时 save_geometry_now() 仍落盘：{raw}")
        finally:
            win2.close()
            win2.deleteLater()
    finally:
        mw.get_screen_geometry = _REAL_SCREEN
        win.close()
        win.deleteLater()

    print(f"\n全部通过：{PASS} 项")
    print("DONE")


if __name__ == "__main__":
    main()
