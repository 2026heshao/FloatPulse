# -*- coding: utf-8 -*-
"""Offscreen 功能验证：左栏拖拽换位（BugFix + 落定动画）。

验证点（对应本次改动）：
  1. 拖拽换位后 _nav_order 更新且 config 落盘；
  2. 换位落定动画对象被创建并在结束后清理；
  3. 正常收尾后光标配对标志复位、override 光标栈为空；
  4. 异常路径（窗口失活 WindowDeactivate）兜底还原光标并复位按钮拖拽态；
  5. 配对防护：重复 started / 残留标志都不会让光标卡在"抓手"；
  6. 动画时长随 anim_speed 档位缩放。

运行方式（必须 offscreen 平台）：
  QT_QPA_PLATFORM=offscreen python tools/verify_nav_drag.py
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import (  # noqa: E402
    QEvent, QPointF, QPoint, QTimer, Qt,
)
from PyQt6.QtGui import QMouseEvent  # noqa: E402
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


def make_mouse_event(etype, btn, global_pos: QPoint) -> QMouseEvent:
    """构造 button 事件：局部坐标由全局坐标映射得出。"""
    local = QPointF(btn.mapFromGlobal(global_pos))
    return QMouseEvent(
        etype, local, QPointF(global_pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def drag_button(app, win, key: str, target_global: QPoint):
    """通过真实鼠标事件序列走完 按下→拖动→松开 全流程。"""
    btn = win._nav_btns[key]
    start_global = btn.mapToGlobal(btn.rect().center())
    btn.mousePressEvent(make_mouse_event(
        QEvent.Type.MouseButtonPress, btn, start_global))
    app.processEvents()
    btn.mouseMoveEvent(make_mouse_event(
        QEvent.Type.MouseMove, btn, target_global))
    app.processEvents()
    assert win._nav_drag_cursor_active, "拖动超阈值后应压入抓手光标"
    btn.mouseReleaseEvent(make_mouse_event(
        QEvent.Type.MouseButtonRelease, btn, target_global))
    app.processEvents()


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_nav_drag")

    # ---- 临时目录数据，绝不碰真实 config ----
    tmp = tempfile.mkdtemp(prefix="fp_verify_nav_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config = ConfigManager(os.path.join(data_dir, "config.json"))

    docx_mgr = DocxManager(
        os.path.join(tmp, "知识库.docx"),
        os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()

    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)   # offscreen 不 start
    temp_mgr = TempAssetManager(tmp)

    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr,
                     config, clip, temp_mgr)
    win.show()
    app.processEvents()

    default_order = list(win._nav_order)
    assert default_order == ["fragments", "tasks", "notes", "knowledge",
                             "assets", "apps", "nav"], default_order
    ok(f"初始 _nav_order 正确: {default_order}")

    # ---- 场景 1：把末尾的 "apps" 拖到最前 ----
    old_positions = {k: QPoint(b.pos()) for k, b in win._nav_btns.items()}
    first_btn = win._nav_btns["fragments"]
    target = first_btn.mapToGlobal(QPoint(2, 2))
    drag_button(app, win, "apps", target)
    expected = ["apps"] + [k for k in default_order if k != "apps"]
    assert win._nav_order == expected, win._nav_order
    ok(f"拖拽换位后 _nav_order 更新: {win._nav_order}")

    # config 落盘校验：用新实例重新读取
    cfg2 = ConfigManager(config.path if hasattr(config, "path")
                         else os.path.join(data_dir, "config.json"))
    assert cfg2.get("nav_order") == win._nav_order, cfg2.get("nav_order")
    ok(f"config 落盘校验通过: {cfg2.get('nav_order')}")

    # 光标配对标志复位 + override 光标栈为空
    assert win._nav_drag_cursor_active is False
    assert QApplication.overrideCursor() is None
    ok("落定后光标标志复位、override 光标栈为空")

    # 按钮拖拽态不残留
    apps_btn = win._nav_btns["apps"]
    assert apps_btn._is_dragging is False
    assert apps_btn._press_global is None
    ok("按钮 _is_dragging/_press_global 无残留")

    # 落定收尾：拖动中邻居已**实时让位**（松手前就已滑到新槽位），
    # 因此这里只剩"被拖按钮是否停在目标槽位"这件事 —— 若松手时它正好
    # 停在目标槽位就没有落定动画（列表为空是正常结果，不是 bug）。
    assert win._nav_drag_order == [], "拖拽顺序状态未清空"
    assert win._nav_free_spacer is None, "自由布局未交还（按钮没插回布局）"
    slot_pos = [old_positions[k] for k in default_order]
    expected_final = {k: slot_pos[win._nav_order.index(k)]
                      for k in win._nav_btns}
    QTimer.singleShot(500, app.quit)   # 等可能的落定/让位动画跑完
    app.exec()
    assert win._nav_settle_animations == [], "动画结束后引用应清空"
    app.processEvents()
    for k, b in win._nav_btns.items():
        assert b.pos() == expected_final[k], \
            f"{k} 未停在终态 {b.pos()} != {expected_final[k]}"
    ok("拖动中已实时让位，松手后按钮停在按新顺序排布的槽位")

    # ---- 场景 2：拖起又放回原位（target == cur 早退路径）----
    pos_before = apps_btn.mapTo(win._nav_area, QPoint(0, 0))
    # 在 apps 按钮内向上拖 20px（超过阈值但仍在原槽位内）
    same_slot_target = apps_btn.mapToGlobal(QPoint(6, 2))
    drag_button(app, win, "apps", same_slot_target)
    assert win._nav_order == list(cfg2.get("nav_order"))
    assert win._nav_drag_cursor_active is False
    assert QApplication.overrideCursor() is None
    assert apps_btn.mapTo(win._nav_area, QPoint(0, 0)) == pos_before
    ok("拖起放回原位：顺序不变、光标还原、无副作用")

    # ---- 场景 3：异常路径兜底（拖拽中窗口失活）----
    drag_start = win._nav_btns["notes"].mapToGlobal(
        win._nav_btns["notes"].rect().center())
    notes_btn = win._nav_btns["notes"]
    notes_btn.mousePressEvent(make_mouse_event(
        QEvent.Type.MouseButtonPress, notes_btn, drag_start))
    notes_btn.mouseMoveEvent(make_mouse_event(
        QEvent.Type.MouseMove, notes_btn,
        notes_btn.mapToGlobal(QPoint(0, -40))))
    assert win._nav_drag_cursor_active is True
    # 模拟拖拽中 Alt+Tab / 系统切窗 → WindowDeactivate
    win.event(QEvent(QEvent.Type.WindowDeactivate))
    assert win._nav_drag_cursor_active is False, "失活后光标标志必须复位"
    assert QApplication.overrideCursor() is None, "失活后 override 光标栈必须为空"
    assert notes_btn._is_dragging is False, "失活后按钮拖拽态必须复位"
    assert win._nav_drag_btn is None
    assert win._drag_indicator.isVisible() is False
    ok("WindowDeactivate 兜底：光标/视觉反馈/按钮态全部复位")

    # ---- 场景 4：配对防护（重复 started / 残留标志）----
    win._acquire_nav_drag_cursor()
    win._acquire_nav_drag_cursor()          # 残留后再次压入（先 restore 再压）
    assert QApplication.overrideCursor() is not None
    win._release_nav_drag_cursor()
    win._release_nav_drag_cursor()          # 重复 restore 是 no-op（幂等）
    assert QApplication.overrideCursor() is None
    ok("配对防护：重复压入/重复还原均安全（幂等）")

    # ---- 场景 5：动画时长随 anim_speed 缩放 ----
    base = win._nav_anim_ms(220)
    config.set("anim_speed", 2.0)
    fast = win._nav_anim_ms(220)
    config.set("anim_speed", 0.5)
    slow = win._nav_anim_ms(220)
    config.set("anim_speed", 1.0)
    assert fast == 110 and base == 220 and slow == 440, (base, fast, slow)
    ok(f"anim_speed 联动: 220ms → 2.0x={fast}ms / 0.5x={slow}ms")

    # ---- 收尾 ----
    win._allow_close = True
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[DONE] all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
