# -*- coding: utf-8 -*-
"""小卡片 Tab 快速连点一致性 · 离屏冒烟（run_gui_check.py 包装）。

背景（2026-10-01）：滑动转场移除前的错位复现序列——
转场期间真实 stack 停在源页且被隐藏，快速连点读到过期 currentIndex：
  fragment(初始) → note → task → fragment（第一段转场的源页）
旧代码必然终态：选中键=fragment、页面=note（键与页永久错位）。
本脚本沿真实按钮 .click() 信号路径连点，逐点断言四方一致：
  stack.currentIndex == checked 按钮索引 == _last_mode 对应索引，且 stack 可见。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from src.card_window import CardWindow, _TAB_KEYS  # noqa: E402

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


def idx_of(mode):
    return _TAB_KEYS.index(mode)


cw = CardWindow()
cw.show()
for _ in range(12):
    app.processEvents()          # 派发 showEvent → 指示器就绪

check("A0 卡片窗可见", cw.isVisible())
check("A1 转场内部已随滑动动画一并移除（无 _page_transition_anim 残留）",
      not hasattr(cw, "_page_transition_anim")
      and not hasattr(CardWindow, "_animate_page_transition")
      and not hasattr(CardWindow, "_finalize_page_transition"))

# ---- B：复现旧错位序列（沿真实信号路径，不泵事件模拟"点得快"）----
print("== B 旧错位序列：fragment→note→task→fragment（中途不泵事件）==", flush=True)
for mode in ("note", "task", "fragment"):
    btn = cw._tab_buttons[idx_of(mode)]
    btn.click()

for _ in range(12):
    app.processEvents()          # 旧代码在这里之后才暴露错位

final = "fragment"
ok_b = (cw._stack.currentIndex() == idx_of(final)
        and cw._last_mode == final
        and cw._stack.isVisible())
for i, btn in enumerate(cw._tab_buttons):
    if btn.isChecked() != (i == idx_of(final)):
        ok_b = False
check("B1 终态四方一致（stack==checked==last_mode==fragment，stack 可见）", ok_b,
      f"stack={cw._stack.currentIndex()} last={cw._last_mode} "
      f"visible={cw._stack.isVisible()}")

# ---- C：全页快速轮巡两圈（含反向），每点即时断言 ----
print("== C 全页轮巡两圈 ==", flush=True)
seq = (_TAB_KEYS + list(reversed(_TAB_KEYS)))
seq = [m for m in seq for _ in range(1)]  # 逐点
all_ok = True
bad = ""
for mode in seq:
    cw._tab_buttons[idx_of(mode)].click()
    i = idx_of(mode)
    if (cw._stack.currentIndex() != i or cw._last_mode != mode
            or not cw._stack.isVisible()
            or cw._tab_buttons[i].isChecked() is not True):
        all_ok = False
        bad = f"{mode}: stack={cw._stack.currentIndex()} last={cw._last_mode}"
        break
for _ in range(12):
    app.processEvents()
check("C1 轮巡全程逐点一致且泵事件后仍一致", all_ok, bad)

# ---- D：同页重复点击（幂等）----
print("== D 同页重复点击 ==", flush=True)
for _ in range(3):
    cw._tab_buttons[idx_of("task")].click()
for _ in range(6):
    app.processEvents()
check("D1 重复点击同页不破坏状态",
      cw._stack.currentIndex() == idx_of("task") and cw._last_mode == "task"
      and cw._stack.isVisible())

n_pass = sum(1 for _, ok in _results if ok)
print(f"== {n_pass}/{len(_results)} passed ==", flush=True)
sys.exit(0 if n_pass == len(_results) else 1)
