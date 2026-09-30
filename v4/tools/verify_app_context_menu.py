# -*- coding: utf-8 -*-
"""
软件导航右键菜单离屏验证（tools/run_gui_check.py 驱动）。

验证点：
  A. 真实 MainWindow 切到 7 号软件导航页，卡片渲染
  B. 卡片右键：菜单弹出 + 事件就地消费（不再冒泡主窗口兜底菜单）
  C. 页面空白区右键：同上
  D. 动作分发：复制路径进剪贴板 / 移除（确认与拒绝两分支）
  E. 截图 docs/images/验证-软件右键菜单.png

运行：python tools/run_gui_check.py tools/verify_app_context_menu.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.app_paths import get_base_dir, get_data_dir
from src.config import ConfigManager
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow
import src.widget_app_launcher as wal

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "docs", "images")

_checkpoints = []


def check(name, cond):
    _checkpoints.append((name, bool(cond)))
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


def _ctx_event():
    return QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(100, 100))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    base = get_base_dir()
    data = get_data_dir(base)

    # ---- 配置用临时副本（绝不写用户 config.json），预置两条软件 ----
    tmp = tempfile.mkdtemp(prefix="fp_appmenu_verify_")
    test_cfg = os.path.join(tmp, "config.json")
    if os.path.exists(os.path.join(data, "config.json")):
        shutil.copy2(os.path.join(data, "config.json"), test_cfg)
    config = ConfigManager(test_cfg)
    config.set("theme", "light")
    exe_a = sys.executable                       # 真实存在的文件，定位/复制可用
    config.set("apps", [
        {"name": "验证甲", "exe_path": exe_a},
        {"name": "验证乙", "exe_path": "C:/no/such/file.exe"},
    ])
    config.save()

    tm = TaskManager(os.path.join(data, "schedule.json"))
    nm = NoteManager(os.path.join(data, "notes.json"))
    fm = FragmentManager(os.path.join(data, "fragments.json"))
    am = TempAssetManager(base)
    clip = ClipboardMonitor(fm, config, am)
    win = MainWindow(tm, nm, fm, None, config, clip, am)
    win.show()
    app.processEvents()

    # ---- A. 真实切页 + 卡片渲染 ----
    win._switch_page(7)
    app.processEvents()
    page = win._page_app_launcher
    check("A1. 切到 7 号软件导航页", win._stack.currentIndex() == 7)
    # 从 grid layout 取活卡片（findChildren 会拿到 deleteLater 待删的旧实例）
    cards = []
    for i in range(page._grid_layout.count()):
        it = page._grid_layout.itemAt(i)
        w = it.widget() if it else None
        if w is not None and w.objectName() == "appCard":
            cards.append(w)
    check("A2. 卡片数与配置一致", len(cards) == 2)
    check("A3. 页面计数标签", "共 2 个" in page._count_label.text())

    # ---- B. 卡片右键：菜单弹出 + 事件就地消费 ----
    exec_called = []
    orig_exec = wal.QMenu.exec
    wal.QMenu.exec = lambda self, *a, **k: exec_called.append(True)
    try:
        ev = _ctx_event()
        cards[0].contextMenuEvent(ev)
        check("B1. 卡片右键弹出菜单", exec_called == [True])
        check("B2. 事件就地消费（不冒泡主窗口）", ev.isAccepted())

        # ---- C. 空白区右键 ----
        exec_called.clear()
        ev = _ctx_event()
        page.contextMenuEvent(ev)
        check("C1. 空白区右键弹出菜单（新增/管理）", exec_called == [True])
        check("C2. 事件就地消费", ev.isAccepted())
    finally:
        wal.QMenu.exec = orig_exec

    # ---- D1. 复制路径 → 剪贴板 ----
    page._on_card_action(0, "copypath")
    check("D1. 复制路径进剪贴板",
          QApplication.clipboard().text() == exe_a)
    QApplication.clipboard().clear()      # 释放 OLE 引用防 teardown 崩溃

    # ---- D2. 移除-拒绝分支 ----
    orig_q = wal.QMessageBox.question
    wal.QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.No
    try:
        page._on_card_action(0, "remove")
        check("D2. 拒绝移除 → 条目保留", len(page.app_list) == 2)
    finally:
        wal.QMessageBox.question = orig_q

    # ---- D3. 移除-确认分支（写回 config）----
    wal.QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    try:
        page._on_card_action(0, "remove")
        check("D3. 确认移除 → 列表与 config 同步删减",
              len(page.app_list) == 1
              and len(config.get("apps", [])) == 1)
    finally:
        wal.QMessageBox.question = orig_q

    # ---- E. 截图（软件导航页全景）----
    pix = win.grab()
    pix.save(os.path.join(OUT_DIR, "验证-软件右键菜单.png"))

    # ---- 收尾 ----
    win.hide()
    app.processEvents()
    failed = [n for n, ok in _checkpoints if not ok]
    print(f"[DONE] {len(_checkpoints) - len(failed)}/{len(_checkpoints)} 通过")
    if failed:
        print(f"[FAIL] 未通过: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
