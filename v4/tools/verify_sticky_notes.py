# -*- coding: utf-8 -*-
"""桌面便签多钉 · 离屏端到端验证（run_gui_check.py 包装，offscreen 平台）。

覆盖：
  A 连开 3 个便签：geometry 全在可用屏幕内且互不重叠
  B 移动窗口 → 600ms 防抖到期 → stickies.json 几何已更新
  C close 后再 open：位置尺寸还原
  D 数量上限：≥MAX_STICKIES 时拒绝新增（返回 limit，不建窗口）
  E 主题切换：标题栏背景色真实变化（widget.grab() 取像素比色）
  F 删除笔记 → validate_open_windows 自动关闭孤儿便签
  G 任务便签：open_task / 截止日徽章 / 备注写回 schedule.json /
    标记完成 / 关开还原 / 删任务自动关窗

跑法：python tools/run_gui_check.py tools/verify_sticky_notes.py
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.note_manager import NoteManager
from src.sticky_notes import StickyStore, StickyNoteManager

app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


tmp = tempfile.mkdtemp(prefix="sticky_verify_")
notes_path = os.path.join(tmp, "notes.json")
stickies_path = os.path.join(tmp, "stickies.json")

note_manager = NoteManager(notes_path)
note_ids = [note_manager.add_note(f"便签测试笔记 {i} 内容内容内容", title=f"便签 {i}")
            for i in range(25)]

store = StickyStore(stickies_path, note_ids=lambda: {
    n.note_id for n in note_manager.get_all_notes()})
manager = StickyNoteManager(note_manager, store, theme="light")

screen = app.primaryScreen().availableGeometry()
print(f"== 屏幕 {screen.width()}x{screen.height()} ==", flush=True)

# ================== A：连开 3 个，屏内且互不重叠 ==================
print("== A 连开 3 个便签 ==", flush=True)
wins = []
for nid in note_ids[:3]:
    ok, reason = manager.open(nid)
    assert ok, f"open({nid}) 失败: {reason}"
    wins.append(manager.window_for_note(nid))
check("A1 打开 3 个成功", manager.count() == 3)

rects = [QRect(w.x(), w.y(), w.width(), w.height()) for w in wins]


def in_screen(r):
    return (r.left() >= screen.left() and r.top() >= screen.top()
            and r.right() <= screen.right() and r.bottom() <= screen.bottom())


check("A2 三个几何均在屏幕内", all(in_screen(r) for r in rects),
      "; ".join(f"({r.x()},{r.y()},{r.width()}x{r.height()})" for r in rects))
overlap = any(rects[i].intersects(rects[j])
              for i in range(3) for j in range(i + 1, 3))
check("A3 三窗互不重叠", not overlap)

# ================== B：移动 + 缩放 → 防抖落盘 ==================
print("== B 防抖落盘 ==", flush=True)
w0 = wins[0]
w0.resize(320, 240)
# 目标位置取屏内（按最终尺寸算，保证右缘不出屏；离屏可用区较小）
nx = screen.right() - w0.width() - 8
ny = screen.top() + 240
w0.move(nx, ny)
w0._geo_timer.start()
QTest.qWait(750)                      # 等 600ms 防抖到期
store2 = StickyStore(stickies_path, note_ids=lambda: {
    n.note_id for n in note_manager.get_all_notes()})
rec0 = store2.get_by_note_id(note_ids[0])
check("B1 移动缩放后 stickies.json 已更新",
      rec0 is not None and (rec0.x, rec0.y, rec0.w, rec0.h)
      == (w0.x(), w0.y(), w0.width(), w0.height()),
      f"json=({rec0.x},{rec0.y},{rec0.w}x{rec0.h})" if rec0 else "无记录")

# ================== C：close 后再 open，几何还原 ==================
print("== C 关闭后重开还原 ==", flush=True)
saved = (rec0.x, rec0.y, rec0.w, rec0.h)
manager.close_by_note(note_ids[0])
check("C1 关闭后窗口摘除", manager.count() == 2)
ok, _ = manager.open(note_ids[0])
w0b = manager.window_for_note(note_ids[0])
check("C2 重开后位置尺寸还原",
      ok and (w0b.x(), w0b.y(), w0b.width(), w0b.height()) == saved,
      f"now=({w0b.x()},{w0b.y()},{w0b.width()}x{w0b.height()})")

# ================== D：数量上限 ==================
print("== D 上限拒绝 ==", flush=True)
# 当前 2 个，开到上限
for nid in note_ids[1:manager.MAX_STICKIES]:
    manager.open(nid)
check("D1 达到上限数量", manager.count() == manager.MAX_STICKIES,
      f"count={manager.count()}")
ok, reason = manager.open(note_ids[20])
check("D2 超上限被拒（返回 limit，不新增窗口）",
      ok is False and reason == "limit" and manager.count() == manager.MAX_STICKIES)

# ================== E：主题切换 → 标题栏像素变色 ==================
print("== E 主题像素比色 ==", flush=True)
w_light = manager.window_for_note(note_ids[1])
app.processEvents()


def title_pixel(win):
    img = win.grab().toImage()
    # 取样标题栏中段（避开左侧文字与右侧关闭钮）。便签已改为玻璃圆角
    # 容器（标题栏透明融入 glass_fill），必须从**整窗** grab 取色才能
    # 拿到真实合成底色——抓 _title_bar 本体只会得到透明底+分隔线，
    # 两主题无差，E1 判据恒假
    return img.pixelColor(int(img.width() * 0.55), 13)


c_light = title_pixel(w_light)
manager.apply_theme("dark")
app.processEvents()
c_dark = title_pixel(w_light)
diff = (abs(c_light.red() - c_dark.red())
        + abs(c_light.green() - c_dark.green())
        + abs(c_light.blue() - c_dark.blue()))
check("E1 标题栏背景色真实变化（像素比色）", diff >= 20,
      f"light={c_light.name()} dark={c_dark.name()} diff={diff}")
manager.apply_theme("light")

# ================== F：删笔记 → 孤儿便签自动关闭 ==================
print("== F 孤儿便签清理 ==", flush=True)
nid_orphan = note_ids[2]
check("F1 目标便签在开", manager.window_for_note(nid_orphan) is not None)
note_manager.delete_note(nid_orphan)
manager.validate_open_windows()
check("F2 删除笔记后便签窗口自动关闭",
      manager.window_for_note(nid_orphan) is None)
store3 = StickyStore(stickies_path, note_ids=lambda: {
    n.note_id for n in note_manager.get_all_notes()})
check("F3 stickies.json 中孤儿记录已清", store3.get_by_note_id(nid_orphan) is None)

# ================== G：任务便签 ==================
print("== G 任务便签 ==", flush=True)
from datetime import datetime, timedelta

from src.task_manager import TaskManager, format_relative_deadline, task_state

schedule_path = os.path.join(tmp, "schedule.json")
task_manager = TaskManager(schedule_path)
dl_overdue = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
dl_today = datetime.now().strftime("%Y-%m-%d")
tid_overdue = task_manager.add_task("逾期任务", "备注A", dl_overdue)
tid_today = task_manager.add_task("今日任务", "备注B", dl_today)

store_t = StickyStore(
    os.path.join(tmp, "stickies_t.json"),
    note_ids=lambda: {n.note_id for n in note_manager.get_all_notes()},
    task_ids=lambda: {t.task_id for t in task_manager.get_all_tasks()})
manager_t = StickyNoteManager(note_manager, store_t, theme="light",
                              task_manager=task_manager)
task_events = []
manager_t.task_data_changed.connect(lambda: task_events.append(1))

ok, reason = manager_t.open_task(tid_overdue)
check("G1 open_task 成功", ok and reason == "ok", f"reason={reason}")
wt = manager_t.window_for("task", tid_overdue)
check("G2 窗口 kind=task 且徽章存在", wt is not None and wt._chip is not None)
check("G3 徽章文本 = 相对截止（逾期着红由 state 决定）",
      wt._chip.text() == f"📅 {format_relative_deadline(dl_overdue)}",
      f"chip={wt._chip.text()!r}")
state, _ = task_state(dl_overdue)
check("G4 overdue 状态判定正确", state == "overdue", f"state={state}")

# 备注编辑 → 800ms 防抖 → 写回 schedule.json + task_data_changed 广播
before_events = len(task_events)
wt._editor.setPlainText("备注A-便签里改过")
QTest.qWait(1000)
t_after = task_manager.get_task(tid_overdue)
check("G5 备注写回 TaskManager", t_after.note == "备注A-便签里改过",
      f"note={t_after.note!r}")
check("G6 task_data_changed 已广播", len(task_events) > before_events)

# 标记完成（走真实注入回调）→ 徽章变「已完成」
ok = wt._toggle_cb()
wt._refresh_task_chip()
check("G7 toggle 回调生效且徽章更新",
      ok and task_manager.get_task(tid_overdue).done
      and wt._chip.text() == "✔ 已完成",
      f"chip={wt._chip.text()!r}")
task_manager.set_done(tid_overdue, False)   # 复位

# 关闭重开：几何还原（与笔记便签同一套 stickies 机制，kind=task 独立存取）
pos0 = (wt.x(), wt.y(), wt.width(), wt.height())
manager_t.close(wt.sticky_id())
ok, _ = manager_t.open_task(tid_overdue)
wt2 = manager_t.window_for("task", tid_overdue)
check("G8 关闭重开几何还原",
      ok and (wt2.x(), wt2.y(), wt2.width(), wt2.height()) == pos0)

# 托盘 get_all 四元组带 kind
entries = [e for e in manager_t.get_all() if e[3] == "task"]
check("G9 get_all 返回 (sid, title, anchor_id, kind)", len(entries) >= 1)

# 删任务 → 孤儿任务便签自动关闭
task_manager.delete_task(tid_today)
manager_t.validate_open_windows()
check("G10 删任务后便签自动关闭",
      manager_t.window_for("task", tid_today) is None)
manager_t.close_all()

# ================== H：碎片直钉（不自动收录笔记） ==================
print("== H 碎片直钉便签 ==", flush=True)
from src.fragment_manager import FragmentManager

frag_mgr = FragmentManager(os.path.join(tmp, "fragments.json"))
fid_pin = frag_mgr.add_fragment("text", "碎片直钉内容：钉成便签但别建笔记",
                                source="probe")
n_notes_before = len(note_manager.get_all_notes())

store_f = StickyStore(
    os.path.join(tmp, "stickies_f.json"),
    note_ids=lambda: {n.note_id for n in note_manager.get_all_notes()},
    fragment_ids=lambda: {f.fragment_id
                          for f in frag_mgr.get_all_fragments()})
manager_f = StickyNoteManager(note_manager, store_f, theme="light",
                              fragment_manager=frag_mgr)

ok, reason = manager_f.open_fragment(fid_pin)
check("H1 碎片直钉成功", ok and reason == "ok", f"reason={reason}")
check("H2 ★ 未自动收录进笔记管理",
      len(note_manager.get_all_notes()) == n_notes_before,
      f"notes={len(note_manager.get_all_notes())} before={n_notes_before}")
wf = manager_f.window_for("fragment", fid_pin)
check("H3 窗口 kind=fragment", wf is not None and wf.kind() == "fragment")
check("H4 标题取内容前缀", wf.title_text().startswith("碎片直钉内容"),
      f"title={wf.title_text()!r}")
wf._editor.setPlainText("碎片内容被便签改过")
QTest.qWait(1000)
check("H5 编辑写回碎片",
      frag_mgr.get_fragment(fid_pin).content == "碎片内容被便签改过",
      f"content={frag_mgr.get_fragment(fid_pin).content!r}")
check("H6 标题随内容刷新", wf.title_text().startswith("碎片内容被便签改过"),
      f"title={wf.title_text()!r}")
frag_mgr.delete_fragment(fid_pin)
manager_f.validate_open_windows()
check("H7 删碎片后便签自动关闭",
      manager_f.window_for("fragment", fid_pin) is None)
manager_f.close_all()

# 清场
manager.close_all()
for w in list(manager._windows.values()):
    w.deleteLater()
app.processEvents()

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
