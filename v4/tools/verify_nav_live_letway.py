# -*- coding: utf-8 -*-
"""Offscreen 功能验证：左栏拖拽**实时让位**（邻居越过中点立即滑开 + 落定滑入）。

验证点：
  A. 拖动中实时让位：邻居在**松手前**就已滑到新槽位（_nav_drag_order 已变）
  B. 被拖按钮跟手（不滞后阈值位移）；纵向钳制在首/末槽位之间
  C. 旧插入指示条（_drag_indicator）全程不出现
  D. 落定滑入：松手位置偏离槽位时有滑动动画，结束后精确落位
  E. 拖起又放回：顺序不变、config 零写入
  F. 异常中断（WindowDeactivate）：回滚顺序、按钮回原槽位、布局交还、光标还原
  G. 连续两次拖拽（第一次落定动画未结束）：结果正确、状态干净
  H. 无残留：自由布局已交还、按钮全部回到布局、无 graphicsEffect/动画引用

运行方式（必须 offscreen 平台）：
  QT_QPA_PLATFORM=offscreen python tools/verify_nav_live_letway.py [--shots]
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QFontDatabase, QMouseEvent  # noqa: E402
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
SHOTS = "--shots" in sys.argv
SHOT_DIR = tempfile.mkdtemp(prefix="nav_live_shots_")


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


def ev(btn, etype, global_pos, buttons=Qt.MouseButton.LeftButton):
    e = QMouseEvent(etype, QPointF(btn.mapFromGlobal(global_pos)),
                    QPointF(global_pos), Qt.MouseButton.LeftButton,
                    buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, e)


def press(btn):
    ev(btn, QEvent.Type.MouseButtonPress, btn.mapToGlobal(btn.rect().center()))


def move_to(btn, global_pos):
    ev(btn, QEvent.Type.MouseMove, global_pos)


def release(btn, global_pos):
    ev(btn, QEvent.Type.MouseButtonRelease, global_pos, Qt.MouseButton.NoButton)


def pump(app, ms=0):
    """跑事件循环（动画推进需要）"""
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(win, name):
    if not SHOTS:
        return
    path = os.path.join(SHOT_DIR, name)
    win.grab().save(path)
    print(f"[SHOT] {path}", flush=True)


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_nav_live_letway")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_live_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config_path = os.path.join(data_dir, "config.json")
    config = ConfigManager(config_path)

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

    default_order = list(win._nav_order)
    step = win._nav_btns[default_order[1]].y() - win._nav_btns[default_order[0]].y()
    assert step > 0, "槽位间距异常"

    # 记录槽位几何（后续所有位置断言都以此为准）
    base_y = win._nav_btns[default_order[0]].y()

    # ---------------- A/B/C：拖动中实时让位 ----------------
    dragged = win._nav_btns[default_order[2]]        # 拖第 3 个（notes）
    neighbor = win._nav_btns[default_order[3]]       # 它的下一个邻居（knowledge）
    anchor = dragged.mapToGlobal(dragged.rect().center())
    press(dragged)
    move_to(dragged, anchor + QPoint(0, 20))         # 越过阈值进入拖拽
    assert win._nav_drag_btn is dragged, "未进入拖拽"
    assert win._drag_indicator.isVisible() is False, "C. 插入指示条不应出现"

    # 越过紧邻邻居中点（下移 1 格）
    move_to(dragged, anchor + QPoint(0, int(step * 1.2)))
    pump(app, 220)                                   # 等让位动画播完
    live_order = list(win._nav_drag_order)
    assert live_order != default_order, f"A. 拖动中顺序未实时变化: {live_order}"
    assert live_order.index(dragged.nav_key) == 3, \
        f"A. 让位后槽位不对: {live_order.index(dragged.nav_key)}"
    # 邻居应已滑到新槽位（上层索引 2）
    slot_y = win._nav_btns[default_order[0]].y() + 2 * step
    assert neighbor.y() == slot_y, f"A. 邻居未让位到位: {neighbor.y()} != {slot_y}"
    ok(f"A. 拖动中实时让位：{dragged.nav_key} → 槽位 "
       f"{live_order.index(dragged.nav_key)}，邻居 {neighbor.nav_key} 已滑开")
    shot(win, "letway_1_mid_drag.png")

    # 继续下拖 1 格（越过第二个邻居）
    move_to(dragged, anchor + QPoint(0, int(step * 2.2)))
    pump(app, 220)
    assert win._nav_drag_order.index(dragged.nav_key) == 4, \
        f"A. 第二次让位失败: {win._nav_drag_order}"
    ok("A. 连续越过两个邻居：让位持续生效（空档跟着鼠标走）")

    # B. 跟手：按钮应贴着光标（误差 ≤ 2px），不是滞后一个阈值
    g = anchor + QPoint(0, int(step * 2.2))
    expect_y = win._nav_area.mapFromGlobal(g).y() - (dragged.height() // 2)
    assert abs(dragged.y() - expect_y) <= 2, \
        f"B. 跟手偏差过大: {dragged.y()} vs {expect_y}"
    ok("B. 被拖按钮严格跟手（无阈值滞后）")

    # B. 钳制：拖出列表底部 → 停在最后一个槽位
    last_y = win._nav_btns[default_order[0]].y() + (len(default_order) - 1) * step
    move_to(dragged, anchor + QPoint(0, int(step * 12)))
    pump(app, 200)
    assert dragged.y() == last_y, f"B. 未钳制到末尾槽位: {dragged.y()} != {last_y}"
    assert win._nav_drag_order[-1] == dragged.nav_key
    ok("B. 纵向钳制：拖出列表底部停在最后一个槽位")

    # ---------------- D. 落定滑入 ----------------
    # 把按钮拖到"两槽位之间"再松手（偏离槽位）→ 必须有滑入动画
    move_to(dragged, anchor + QPoint(0, int(step * 1.5)))
    pump(app, 220)
    live_y = dragged.y()
    target_slot = win._nav_drag_order.index(dragged.nav_key)
    assert live_y != base_y + target_slot * step, "D. 前置：松手位置应偏离槽位"
    release(dragged, anchor + QPoint(0, int(step * 1.5)))
    pump(app)
    assert win._nav_free_spacer is None, "D. 落定时应立刻交还布局占位"
    anims = list(win._nav_settle_animations)
    drop = win._nav_drop_anim
    assert anims or drop is not None, "D. 松手位置偏离槽位 → 应有滑入动画"
    shot(win, "letway_2_settling.png")
    pump(app, 600)
    final_ys = [b.y() for b in win._nav_btns.values()]
    order = list(win._nav_order)
    expected = [win._nav_btns[k].y() for k in order]
    assert len(set(expected)) == len(order), "D. 终态槽位重叠"
    assert sorted(final_ys) == sorted(
        [base_y + i * step for i in range(len(order))]), f"D. 终态未落位: {final_ys}"
    cfg2 = ConfigManager(config_path)
    assert cfg2.get("nav_order") == order, "D. config 未落盘"
    ok(f"D. 落定滑入后精确落位并落盘: {order}")
    shot(win, "letway_3_final.png")

    # ---------------- E. 拖起又放回 ----------------
    with open(config_path, "rb") as f:
        cfg_bytes = f.read()
    mtime = os.path.getmtime(config_path)
    held = win._nav_btns[order[1]]
    slot_before = held.y()                      # 布局槽位（拖动前）
    hp = held.mapToGlobal(held.rect().center())
    press(held)
    move_to(held, hp + QPoint(0, 10))
    assert win._nav_drag_btn is held
    released_at = held.y()                      # 拖起后的位置（应偏离槽位）
    assert released_at != slot_before, "E. 前置：拖起后位置应偏离槽位"
    release(held, hp + QPoint(0, 10))
    pump(app, 400)
    assert list(win._nav_order) == order, "E. 顺序不应变化"
    assert os.path.getmtime(config_path) == mtime, "E. config 被写入（不应）"
    with open(config_path, "rb") as f:
        assert f.read() == cfg_bytes, "E. config 内容被改写（不应）"
    assert held.y() == slot_before, f"E. 未回到原槽位: {held.y()} != {slot_before}"
    ok("E. 拖起又放回：顺序/config 零副作用，按钮回原位")

    # ---------------- F. 异常中断回滚 ----------------
    victim = win._nav_btns[list(win._nav_order)[0]]
    vp = victim.mapToGlobal(victim.rect().center())
    press(victim)
    move_to(victim, vp + QPoint(0, int(step * 2.2)))
    pump(app, 200)
    assert win._nav_drag_order != list(win._nav_order), "F. 前置：拖动中顺序应已变"
    win.event(QEvent(QEvent.Type.WindowDeactivate))
    app.processEvents()
    assert win._nav_drag_cursor_active is False, "F. 光标标志未复位"
    assert QApplication.overrideCursor() is None, "F. override 光标栈非空"
    assert win._nav_drag_btn is None and win._nav_free_spacer is None, "F. 拖拽态未复位"
    assert list(win._nav_order) == order, "F. 中断不应改动顺序"
    for i, k in enumerate(order):
        assert win._nav_btns[k].y() == base_y + i * step, f"F. {k} 未回原槽位"
    assert victim.graphicsEffect() is None, "F. 提起投影未清除"
    ok("F. WindowDeactivate 中断：回滚顺序 + 按钮回原槽位 + 布局交还 + 光标还原")

    # ---------------- G. 连续两次拖拽（动画中再次拖拽） ----------------
    first = win._nav_btns[order[0]]
    fp = first.mapToGlobal(first.rect().center())
    press(first)
    move_to(first, fp + QPoint(0, int(step * 1.2)))
    move_to(first, fp + QPoint(0, int(step * 1.5)))
    release(first, fp + QPoint(0, int(step * 1.5)))    # 落定动画未结束就再拖
    app.processEvents()                                # 只推一轮：动画仍在跑
    assert win._nav_settle_animations or win._nav_drop_anim is not None, \
        "G. 前置：第一次落定动画应仍在进行"
    mid_order = list(win._nav_order)
    second = win._nav_btns[mid_order[-1]]
    start_idx = mid_order.index(second.nav_key)
    sp = second.mapToGlobal(second.rect().center())
    press(second)
    move_to(second, sp + QPoint(0, -int(step * 2.5)))
    move_to(second, sp + QPoint(0, -int(step * 3.2)))
    release(second, sp + QPoint(0, -int(step * 3.2)))
    pump(app, 700)
    new_order = list(win._nav_order)
    # 上移 3.2 个槽位 → 索引 = 起点 - round(3.2) = 起点 - 3（下限 0）
    expect_idx = max(0, start_idx - 3)
    assert new_order.index(second.nav_key) == expect_idx, \
        f"G. 第二次拖拽结果错误: {new_order}（期望 {second.nav_key} 落在 {expect_idx}）"
    assert win._nav_free_spacer is None and win._nav_drag_order == [], "G. 状态未收尾"
    assert win._nav_settle_animations == [] and win._nav_drop_anim is None, \
        "G. 动画引用未清空"
    ok(f"G. 连续两次拖拽（动画中再次拖拽）："
       f"{second.nav_key} 由 {start_idx} → {expect_idx}，状态干净")

    # ---------------- H. 无残留 ----------------
    assert win._nav_shift_anims == {}, "H. 让位动画引用未清空"
    for i, k in enumerate(win._nav_order):
        b = win._nav_btns[k]
        assert b.graphicsEffect() is None, f"H. {k} 残留 graphicsEffect"
        assert win._nav_btns_layout.indexOf(b) >= 0, f"H. {k} 未回到布局"
        assert b.y() == base_y + i * step, f"H. {k} 位置异常 {b.y()}"
    assert win._nav_btns_layout.indexOf(win._settings_btn) >= 0, "H. 设置按钮不在布局"
    ok("H. 无残留：布局完整、按钮归位、无 effect/动画引用")

    win._allow_close = True
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    if SHOTS:
        print(f"[SHOTS] {SHOT_DIR}")
    print(f"[DONE] all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
