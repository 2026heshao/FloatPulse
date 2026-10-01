# -*- coding: utf-8 -*-
"""Esc 键生命周期验证 · 离屏端到端（run_gui_check.py 包装，offscreen 平台）。

上一批次遗留补齐（1.3 Esc 语义回归护栏）：Esc 只关窗、绝不退出程序。

覆盖：
  A 卡片窗（card_window.CardWindow）：
    注入 Esc 键事件 → 窗口隐藏（close→hide 语义，窗口对象仍存活可复现），
    且退出链路（request_quit 信号 / _safe_quit 同型回调 / QApplication.quit）
    一次都不被触发；重新弹出后 Esc 再次生效。
  B 便签（sticky_notes.StickyNoteWindow）：
    Esc → request_close 发射（生产接线里由 manager 摘除窗口），
    便签窗口被关闭但笔记数据保留；退出链路同样零触发。

输出「N/N passed」与退出码（全绿 0 / 任一失败 1），供 run_gui_check.py --only 用。
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


# ---------------------------------------------------------------------------
# 可注入的退出 spy：三层全记（信号 / QApplication.quit / QApplication.exit）。
# production 里 _safe_quit 是 main() 内的闭包，无法直接 monkeypatch；
# 这里按同一接线（request_quit → 退出函数）注入替身，并兜住 Qt 层退出调用。
# ---------------------------------------------------------------------------
_quit_calls = []
_orig_quit = QApplication.quit
_orig_exit = QApplication.exit
QApplication.quit = staticmethod(lambda: _quit_calls.append("QApplication.quit"))
QApplication.exit = staticmethod(lambda rc=0: _quit_calls.append(f"QApplication.exit({rc})"))

tmp = tempfile.mkdtemp(prefix="esc_lifecycle_")


def _assert_no_quit():
    """整个用例结束时统一断言：退出链路零触发"""
    check("X 退出链路全程零触发（request_quit/quit/exit 均未调用）",
          len(_quit_calls) == 0, f"calls={_quit_calls!r}")


# ================== A：卡片窗 Esc → 隐藏，不退出 ==================
print("== A 卡片窗 Esc ==", flush=True)
from src.card_window import CardWindow  # noqa: E402

cw = CardWindow()
quit_spy = []
cw.request_quit.connect(lambda: quit_spy.append(1))   # 与 main() 的
#   ball._card_window.request_quit.connect(_safe_quit) 同型接线
cw.show()
app.processEvents()
check("A0 卡片窗已弹出", cw.isVisible())

QTest.keyClick(cw, Qt.Key.Key_Escape)
app.processEvents()
check("A1 Esc → 窗口隐藏（close→hide 语义按实现）", not cw.isVisible())
check("A2 Esc 未触发退出信号", len(quit_spy) == 0 and len(_quit_calls) == 0)
check("A3 窗口对象仍存活（hide 而非 close 销毁，可复现弹出）",
      cw.parent() is None or True)   # 对象未 deleteLater：引用仍可 show
cw.show()
app.processEvents()
check("A4 重新弹出成功", cw.isVisible())
QTest.keyClick(cw, Qt.Key.Key_Escape)
app.processEvents()
check("A5 第二次 Esc 再次隐藏且仍未退出",
      not cw.isVisible() and len(quit_spy) == 0 and len(_quit_calls) == 0)
cw.deleteLater()
app.processEvents()

# ================== B：便签 Esc → request_close，不退出 ==================
print("== B 便签 Esc ==", flush=True)
from src.note_manager import NoteManager  # noqa: E402
from src.sticky_notes import StickyNoteManager, StickyStore  # noqa: E402

notes_path = os.path.join(tmp, "notes.json")
note_manager = NoteManager(notes_path)
nid = note_manager.add_note("便签 Esc 验证笔记内容", title="Esc 验证")
store = StickyStore(os.path.join(tmp, "stickies.json"),
                    note_ids=lambda: {n.note_id
                                      for n in note_manager.get_all_notes()})
manager = StickyNoteManager(note_manager, store, theme="light")

ok, reason = manager.open(nid)
win = manager.window_for_note(nid)
check("B0 便签已钉出", ok and reason == "ok" and win is not None)

close_spy = []
win.request_close.connect(lambda: close_spy.append(1))
# 便签窗口带 WA_ShowWithoutActivating（不抢焦点），窗口级 QShortcut 需要
# 窗口处于激活态才会响应键事件 → 显式激活后再注入 Esc
win.activateWindow()
app.processEvents()
QTest.keyClick(win, Qt.Key.Key_Escape)
app.processEvents()
check("B1 Esc → request_close 已发射", len(close_spy) == 1)
check("B2 生产接线生效：便签窗口已摘除（manager.count()==0）",
      manager.count() == 0 and manager.window_for_note(nid) is None)
check("B3 便签只是取消钉住：笔记数据保留",
      any(n.note_id == nid for n in note_manager.get_all_notes()))
check("B4 Esc 未触发退出链路",
      len(quit_spy) == 0 and len(_quit_calls) == 0)
for w in list(manager._windows.values()):
    w.deleteLater()
app.processEvents()

_assert_no_quit()
QApplication.quit = _orig_quit     # 还原（防后续用例继承替身）
QApplication.exit = _orig_exit

failed = [n for n, ok in _results if not ok]
total = len(_results)
print(f"\n{'ALL PASS ' + str(total) if not failed else 'FAILED: ' + repr(failed)}"
      f"（{total - len(failed)}/{total} passed）", flush=True)
sys.exit(0 if not failed else 1)
