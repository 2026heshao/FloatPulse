# -*- coding: utf-8 -*-
"""QA 独立验证：左栏拖拽换位修复 + 落定动画（第 2 层严过关）。

覆盖 verify_nav_drag.py 未覆盖的场景：
  A. 真实鼠标事件拖拽换位 → order/config 落盘（独立复跑）
  B. 光标/不透明度效果/按钮拖拽态 三重复位断言
  C. WindowDeactivate 兜底 → 复位后正常点击切页仍可用
  D. 拖拽中 hide → show：不崩、光标还原、nav_order 不变
  E. 连续两次拖拽（第二次在第一次落定动画未结束时发起）
  F. 位置未变的按钮不创建动画；动画终值 = 目标槽位
  G. anim_speed 联动：实际动画对象 duration 按比例变化
  H. 拖起放回原位：无动画、config 文件内容与 mtime 均无写入副作用

运行：QT_QPA_PLATFORM=offscreen python tools/qa_verify_nav_drag.py
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
    local = QPointF(btn.mapFromGlobal(global_pos))
    return QMouseEvent(
        etype, local, QPointF(global_pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def drag_button(app, win, key: str, target_global: QPoint):
    """真实鼠标事件序列：press → move 超阈值 → release。"""
    btn = win._nav_btns[key]
    start_global = btn.mapToGlobal(btn.rect().center())
    btn.mousePressEvent(make_mouse_event(
        QEvent.Type.MouseButtonPress, btn, start_global))
    app.processEvents()
    btn.mouseMoveEvent(make_mouse_event(
        QEvent.Type.MouseMove, btn, target_global))
    app.processEvents()
    btn.mouseReleaseEvent(make_mouse_event(
        QEvent.Type.MouseButtonRelease, btn, target_global))
    app.processEvents()


def assert_drag_state_reset(win, tag: str):
    """拖拽态三重复位断言：光标标志 / override 光标栈 / 不透明度效果。"""
    assert win._nav_drag_cursor_active is False, f"{tag}: 光标标志未复位"
    assert QApplication.overrideCursor() is None, \
        f"{tag}: override 光标栈非空 → 鼠标会卡在抓手"
    assert win._nav_drag_btn is None, f"{tag}: _nav_drag_btn 未清空"
    assert win._drag_indicator.isVisible() is False, f"{tag}: 指示条未隐藏"
    for k, b in win._nav_btns.items():
        assert b._is_dragging is False, f"{tag}: {k}._is_dragging 残留"
        assert b._press_global is None, f"{tag}: {k}._press_global 残留"
        assert b.graphicsEffect() is None, \
            f"{tag}: {k} 上残留 QGraphicsOpacityEffect"


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("qa_verify_nav_drag")


    tmp = tempfile.mkdtemp(prefix="fp_qa_nav_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config_path = os.path.join(data_dir, "config.json")
    config = ConfigManager(config_path)

    docx_mgr = DocxManager(
        os.path.join(tmp, "知识库.docx"),
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
    app.processEvents()

    default_order = list(win._nav_order)

    # ---- A. 真实鼠标事件拖拽换位：把末尾 apps 拖到最前 ----
    old_positions = {k: QPoint(b.pos()) for k, b in win._nav_btns.items()}
    first_btn = win._nav_btns["fragments"]
    drag_button(app, win, "apps", first_btn.mapToGlobal(QPoint(2, 2)))
    expected = ["apps"] + [k for k in default_order if k != "apps"]
    assert win._nav_order == expected, win._nav_order
    ok(f"A. 拖拽换位 _nav_order 更新: {win._nav_order}")

    cfg2 = ConfigManager(config_path)
    assert cfg2.get("nav_order") == win._nav_order
    ok("A. config 落盘：新实例读回一致")

    assert win._nav_drag_order == [] and win._nav_free_spacer is None, \
        "A. 拖拽状态未收尾（自由布局未交还）"
    assert_drag_state_reset(win, "A")
    ok("B. 拖拽后光标/提起视觉/按钮态三重复位，无残留 effect")

    # ---- F. 动力定位：所有按钮停在按新顺序排布的槽位 ----
    # 拖动中邻居已实时让位（松手前就在新槽位），所以落定时不需要"整表重排"，
    # 只有松手位置偏离目标槽位时才有 1 个落定滑入动画。
    slot_pos = [old_positions[k] for k in default_order]
    expected_final = {k: slot_pos[list(win._nav_order).index(k)]
                      for k in win._nav_btns}
    QTimer.singleShot(600, app.exit)
    app.exec()
    assert win._nav_settle_animations == [], "动画结束后引用应清空"
    app.processEvents()
    for k, b in win._nav_btns.items():
        assert b.pos() == expected_final[k], \
            f"F. {k} 终态 {b.pos()} != 槽位 {expected_final[k]}"
    ok("F. 按钮全部停在按新顺序排布的槽位")

    # ---- H. 拖起放回原位：无动画、config 无写入副作用 ----
    with open(config_path, "rb") as f:
        cfg_bytes_before = f.read()
    mtime_before = os.path.getmtime(config_path)
    pos_before = win._nav_btns["apps"].mapTo(win._nav_area, QPoint(0, 0))
    # 在 apps 按钮内移动 20px（超阈值进入拖拽，但仍在原槽位区间 → 早退）
    apps_btn = win._nav_btns["apps"]
    drag_button(app, win, "apps", apps_btn.mapToGlobal(QPoint(6, 2)))
    assert win._nav_order == expected, "H. 顺序不应变化"
    assert win._nav_settle_animations == [], "H. 放回原位不应创建动画"
    assert apps_btn.mapTo(win._nav_area, QPoint(0, 0)) == pos_before
    assert_drag_state_reset(win, "H")
    assert os.path.getmtime(config_path) == mtime_before, \
        "H. config 文件 mtime 变化 → 有写入副作用"
    with open(config_path, "rb") as f:
        assert f.read() == cfg_bytes_before, "H. config 文件内容被改写"
    ok("H. 拖起放回原位：无动画、config 零写入副作用")

    # ---- C. 拖拽中 WindowDeactivate 兜底 → 之后正常点击切页仍可用 ----
    notes_btn = win._nav_btns["notes"]
    drag_start = notes_btn.mapToGlobal(notes_btn.rect().center())
    notes_btn.mousePressEvent(make_mouse_event(
        QEvent.Type.MouseButtonPress, notes_btn, drag_start))
    notes_btn.mouseMoveEvent(make_mouse_event(
        QEvent.Type.MouseMove, notes_btn,
        notes_btn.mapToGlobal(QPoint(0, -40))))
    assert win._nav_drag_cursor_active is True, "C. 拖拽中应压入光标"
    win.event(QEvent(QEvent.Type.WindowDeactivate))
    assert_drag_state_reset(win, "C")
    ok("C. WindowDeactivate：光标/不透明度/按钮态/指示条全部复位")

    # 复位后正常点击切页仍可用（notes 的 page index = 2）
    win._nav_btns["notes"].click()
    app.processEvents()
    assert win._stack.currentIndex() == 2, \
        f"C. 复位后点击切页失效, currentIndex={win._stack.currentIndex()}"
    ok("C. 兜底复位后正常点击切页仍可用 (currentIndex=2)")

    # ---- D. 拖拽中 hide → show：不崩、光标还原、nav_order 不变 ----
    order_before = list(win._nav_order)
    knowledge_btn = win._nav_btns["knowledge"]
    drag_start = knowledge_btn.mapToGlobal(knowledge_btn.rect().center())
    knowledge_btn.mousePressEvent(make_mouse_event(
        QEvent.Type.MouseButtonPress, knowledge_btn, drag_start))
    knowledge_btn.mouseMoveEvent(make_mouse_event(
        QEvent.Type.MouseMove, knowledge_btn,
        knowledge_btn.mapToGlobal(QPoint(0, -50))))
    assert win._nav_drag_cursor_active is True
    win.hide()
    app.processEvents()
    assert_drag_state_reset(win, "D-hide")
    win.show()
    app.processEvents()
    assert win._nav_order == order_before, "D. hide 前后 nav_order 不应变化"
    assert knowledge_btn._is_dragging is False
    # hide→show 后页面切换仍可用
    knowledge_btn.click()
    app.processEvents()
    assert win._stack.currentIndex() == 3
    ok("D. 拖拽中 hide→show：不崩、光标还原、nav_order 不变、切页可用")

    # ---- E. 连续两次拖拽（第二次在第一次落定动画未结束时发起）----
    # 回到默认顺序并立即落定（避免用动画中的中间位置计算拖拽坐标）
    win._apply_nav_order(list(default_order), save=False)
    win._abort_nav_settle_animations()
    app.processEvents()
    # ⚠ 实时让位设计下，松手点正好落在槽位上时按钮**已经在原地**（拖动中
    #   邻居已让位）→ 不会产生滑入动画。这里把松手点下移半个槽位，制造
    #   "偏离槽位"的落点，才能让"落定动画未结束"这个前置条件成立。
    step = (win._nav_btns[default_order[1]].y()
            - win._nav_btns[default_order[0]].y())
    assert step > 0, "E. 槽位间距异常"
    off_slot = (win._nav_btns["fragments"].mapToGlobal(QPoint(2, 2))
                + QPoint(0, int(step * 0.5)))
    # 第一次拖拽：notes 拖到最前 → release 后产生落定动画
    drag_button(app, win, "notes", off_slot)
    assert win._nav_order[0] == "notes", win._nav_order
    assert win._nav_settle_animations or win._nav_drop_anim is not None, \
        "E. 前置：第一次拖拽松手点偏离槽位 → 应有滑入动画在跑"
    assert QApplication.overrideCursor() is None, "E. 第一次拖后光标残留"
    # 第二次拖拽紧接发起（第一次落定动画未结束）：
    # 只用未动的按钮（knowledge 静止）做坐标锚点，nav 拖到 knowledge 之前
    drag_button(app, win, "nav",
                win._nav_btns["knowledge"].mapToGlobal(QPoint(2, 2)))
    assert win._nav_order.index("nav") < win._nav_order.index("knowledge"), \
        win._nav_order
    assert_drag_state_reset(win, "E")
    # 让事件循环跑完，确保无残留动画引用、无崩溃
    QTimer.singleShot(800, app.exit)
    app.exec()
    assert win._nav_settle_animations == [], "E. 最终动画引用应清空"
    ok(f"E. 连续两次拖拽（动画中再次拖拽）：结果正确 {win._nav_order}，"
       f"无光标残留、动画引用清空")

    # ---- G. anim_speed 联动：实际动画对象 duration 缩放 ----
    config.set("anim_speed", 2.0)
    win._apply_nav_order(list(default_order), save=False)
    fast = [a.duration() for a in win._nav_settle_animations]
    assert fast and all(d == 110 for d in fast), fast
    config.set("anim_speed", 0.5)
    win._apply_nav_order(list(reversed(default_order)), save=False)
    slow = [a.duration() for a in win._nav_settle_animations]
    assert slow and all(d == 440 for d in slow), slow
    config.set("anim_speed", 1.0)
    win._abort_nav_settle_animations()
    ok(f"G. anim_speed 联动：实际动画 duration 2.0x={fast[0]}ms, "
       f"0.5x={slow[0]}ms")

    # ---- 收尾 ----
    win._allow_close = True
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[DONE] QA all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
