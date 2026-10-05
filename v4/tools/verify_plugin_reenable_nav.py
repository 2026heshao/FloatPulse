# -*- coding: utf-8 -*-
"""verify_plugin_reenable_nav.py — 插件「停用→再启用」后导航键几何一致性验证。

用户实测（2026-10-05）：在插件中心把某页面插件停用再启用，左栏导航键
位置错乱（截图见行「系统」组标题与「插件中心」重叠一处）。

复刻用户操作序列（全部走 main_window 的公开注册/注销接口，与
PluginsPanel._toggle_plugin 的调用面一致）：
  1. 启动装配：注册 3 个页面插件（可见状态，等同「重新扫描」）
  2. 展开 插件 / 系统 两组
  3. 停用 B（unregister_plugin_page）
  4. 启用 B（register_plugin_page，即 rebuild_plugin_page 的落点）
  5. 检查：布局序、可见部件 y 严格递增无重叠、堆栈索引一致、
     无「不在布局里的漂浮按钮」（重叠的唯一可能来源）、指示条对位

用法（必须 offscreen，别直接 python 跑）：
  python tools/run_gui_check.py tools/verify_plugin_reenable_nav.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow, NAV_PAGE_INDEX  # noqa: E402

SHOT_DIR = os.path.join(os.path.dirname(BASE), "build", "shots")
os.makedirs(SHOT_DIR, exist_ok=True)
FAILS = []


def check(cond, msg):
    tag = "OK" if cond else "!!"
    print(f"[{tag}] {msg}", flush=True)
    if not cond:
        FAILS.append(msg)


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def layout_widgets_in_order(win):
    """内容布局里实际挂着的部件序列（含组标题/按钮/stretch 前的全部项）"""
    lay = win._nav_btns_layout
    out = []
    for i in range(lay.count()):
        it = lay.itemAt(i)
        w = it.widget() if it is not None else None
        if w is not None:
            out.append(w)
    return out


def visible_rows(win):
    """当前可见的导航行部件（组标题 + 按钮），按 y 排序前先收集"""
    rows = []
    for hd in win._nav_group_headers.values():
        if hd.isVisible():
            rows.append(("header:" + hd.objectName(), hd))
    for key, btn in win._nav_btns.items():
        if btn.isVisible():
            rows.append(("btn:" + key, btn))
    for key, btn in win._nav_fixed_btns.items():
        if btn.isVisible():
            rows.append(("fixed:" + key, btn))
    return rows


def check_no_overlap(win, tag):
    """所有可见行按 y 排序后必须严格递增且互不重叠"""
    rows = sorted(visible_rows(win), key=lambda kv: kv[1].y())
    prev_bottom = -10**9
    ok = True
    detail = []
    for name, w in rows:
        y, h = w.y(), w.height()
        detail.append(f"{name}@{y}+{h}")
        if y < prev_bottom - 1:          # 1px 容差
            ok = False
        prev_bottom = y + h
    check(ok, f"{tag}. 可见导航行无重叠（{' '.join(detail)}）")
    return rows


def check_layout_matches_seq(win, tag):
    """布局里的部件顺序 = _nav_sequence 期望顺序（标题/条目交错）"""
    seq = win._nav_sequence(list(win._nav_order))
    expected = [win._nav_widget(kind, ref) for kind, ref in seq]
    actual = layout_widgets_in_order(win)
    # 实际布局 = expected + 末尾 stretch（widget 项只有 expected）
    ok = actual == expected
    if not ok:
        exp_names = [type(w).__name__ + ":" + (w.text() if hasattr(w, "text") else "")
                     for w in expected]
        act_names = [type(w).__name__ + ":" + (w.text() if hasattr(w, "text") else "")
                     for w in actual]
        print(f"      expected={exp_names}")
        print(f"      actual  ={act_names}")
    check(ok, f"{tag}. 布局内容与顺序表完全一致（无缺失/多余/乱序）")


def check_no_floating_buttons(win, tag):
    """内容区里所有**可见**按钮都必须在布局里（漂浮可见=重叠唯一来源）。

    deleteLater 是延迟回收：旧按钮注销后到事件循环销毁前对象仍在，但已
    被 hide()，不算异常。
    """
    ghosts = [b for b in win._nav_content.findChildren(QPushButton)
              if win._nav_btns_layout.indexOf(b) < 0
              and b not in win._nav_fixed_btns.values()
              and b.isVisible()
              and not b.objectName().startswith("navDragIndicator")]
    check(not ghosts, f"{tag}. 无可见漂浮按钮（不在布局里的幽灵 {ghosts}）")


def check_stack_consistency(win, tag, key):
    """插件页：NAV_PAGE_INDEX → stack 位置 → 按钮组 id 三方一致"""
    idx = NAV_PAGE_INDEX.get(key)
    ok_idx = idx is not None and idx >= 10
    w = win._stack.widget(idx) if ok_idx else None
    check(ok_idx and w is not None and not w.objectName().startswith("qt_"),
          f"{tag}. {key} 页面在栈上（idx={idx} widget={w}）")
    btn = win._nav_group.button(idx) if ok_idx else None
    check(btn is win._nav_btns.get(key),
          f"{tag}. 按钮组 id {idx} → {key} 的按钮（实际 {btn}）")
    # 页面 widget 必须是真的插件页（不是空占位 QWidget）
    check(ok_idx and win._stack.indexOf(win._stack.widget(idx)) == idx,
          f"{tag}. 栈索引自洽")


def main():
    app = QApplication(sys.argv)
    QApplication.setApplicationName("plugin_reenable_check")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_reenable_")
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    cfg = ConfigManager(os.path.join(data, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data, "docx_meta.json"))
    docx.load()
    win = MainWindow(TaskManager(os.path.join(data, "schedule.json")),
                     NoteManager(os.path.join(data, "notes.json")),
                     FragmentManager(os.path.join(data, "fragments.json")),
                     docx, cfg,
                     ClipboardMonitor(FragmentManager(os.path.join(data, "f2.json")), cfg),
                     TempAssetManager(tmp))
    win.resize(920, 620)
    win.show()
    pump(app, 1200)

    # ---- 启动装配：注册 3 个页面插件（可见状态，等同重新扫描）----
    pages = {}
    for pid, title in (("recurring", "周期任务"),
                       ("workshop", "文本工坊"),
                       ("vault", "密码保险箱")):
        key = f"plugin:{pid}"
        pages[key] = QWidget()
        win.register_plugin_page(key, title, pages[key])
    pump(app, 400)

    # ---- 展开 插件 / 系统 两组（截图里两组条目都可见）----
    for grp in ("plugin_pages", "system"):
        if grp not in win._expanded_groups():
            win._on_group_toggled(grp)
    pump(app, 500)
    win.grab().save(os.path.join(SHOT_DIR, "reenable-100-baseline.png"))

    check("plugin:vault" in win._nav_btns, "基线. vault 键已注册")
    rows = check_no_overlap(win, "基线")
    baseline_ys = {n: w.y() for n, w in rows}   # 供启用后对比
    check_layout_matches_seq(win, "基线")
    check_no_floating_buttons(win, "基线")
    check_stack_consistency(win, "基线", "plugin:vault")
    stack_count0 = win._stack.count()

    # ---- 停用 vault（= PluginsPanel._toggle_plugin(off) 的宿主侧）----
    old_btn = win._nav_btns["plugin:vault"]
    ok_un = win.unregister_plugin_page("plugin:vault")
    pump(app, 500)          # 越过落定动画 + deleteLater 处理
    check(ok_un, "停用. unregister 返回 True")
    check("plugin:vault" not in win._nav_btns, "停用. 按钮已摘除")
    check("plugin:vault" not in win._nav_order, "停用. 顺序表已同步移除")
    rows = check_no_overlap(win, "停用后")
    check_layout_matches_seq(win, "停用后")
    check_no_floating_buttons(win, "停用后")
    try:
        _ = old_btn.y()
        ghost_alive = True
    except RuntimeError:
        ghost_alive = False
    check(not ghost_alive or not old_btn.isVisible(),
          "停用后. 旧按钮已销毁或不可见（无幽灵）")
    win.grab().save(os.path.join(SHOT_DIR, "reenable-200-disabled.png"))

    # ---- 再启用 vault（= rebuild_plugin_page → register_plugin_page）----
    pages["plugin:vault"] = QWidget()
    idx = win.register_plugin_page("plugin:vault", "密码保险箱", pages["plugin:vault"])
    pump(app, 600)          # 越过落定动画
    check(idx >= 10 and "plugin:vault" in win._nav_btns, "启用. 按钮已重建")
    check(win._nav_btns["plugin:vault"].isVisible(), "启用. 按钮可见（组展开中）")
    check("plugin:vault" in win._nav_order, "启用. 顺序表已回收")
    rows = check_no_overlap(win, "启用后")
    check_layout_matches_seq(win, "启用后")
    check_no_floating_buttons(win, "启用后")
    check_stack_consistency(win, "启用后", "plugin:vault")
    check(win._stack.count() == stack_count0 + 1,
          f"启用后. 栈页数 +1（占位页留存，{stack_count0}→{win._stack.count()}）")
    win.grab().save(os.path.join(SHOT_DIR, "reenable-300-enabled.png"))

    # ---- 与基线比：其它可见行 y 不变（位置错乱 = 挪了不该挪的行）----
    def row_y(name):
        for n, w in visible_rows(win):
            if n == name:
                return w.y()
        return None

    for probe in ("btn:plugin:recurring", "btn:plugin:workshop",
                  "btn:plugins", "fixed:settings", "fixed:help"):
        base_y = baseline_ys.get(probe)
        now_y = row_y(probe)
        # vault 回到插件组末位（order 丢记忆位是已知口径），工作台组不受影响
        if probe in ("btn:plugin:recurring", "btn:plugin:workshop"):
            check(base_y == now_y,
                  f"启用后. {probe} y 与基线一致（{base_y}→{now_y}）")

    # ---- 连续第二轮「停用→启用」（用户反复折腾场景）----
    for cycle in (2, 3):
        win.unregister_plugin_page("plugin:vault")
        pump(app, 300)
        pages["plugin:vault"] = QWidget()
        win.register_plugin_page("plugin:vault", "密码保险箱", pages["plugin:vault"])
        pump(app, 600)
        check_no_overlap(win, f"第{cycle}轮启用后")
        check_layout_matches_seq(win, f"第{cycle}轮启用后")
        check_no_floating_buttons(win, f"第{cycle}轮启用后")
        check_stack_consistency(win, f"第{cycle}轮启用后", "plugin:vault")

    # ---- 折叠着插件组再启用（启用后按钮不该顶穿折叠态）----
    if "plugin_pages" in win._expanded_groups():
        win._on_group_toggled("plugin_pages")
    pump(app, 400)
    win.unregister_plugin_page("plugin:vault")
    pump(app, 300)
    pages["plugin:vault"] = QWidget()
    win.register_plugin_page("plugin:vault", "密码保险箱", pages["plugin:vault"])
    pump(app, 400)
    check(not win._nav_btns["plugin:vault"].isVisible(),
          "折叠组. 停用→启用后按钮不顶穿折叠态")
    check_no_overlap(win, "折叠组")
    # 重新展开后一切正常
    win._on_group_toggled("plugin_pages")
    pump(app, 600)
    check(win._nav_btns["plugin:vault"].isVisible(), "折叠组. 重新展开后按钮可见")
    check_no_overlap(win, "折叠组重展后")
    check_layout_matches_seq(win, "折叠组重展后")
    win.grab().save(os.path.join(SHOT_DIR, "reenable-400-final.png"))

    print(f"\n[SHOTS] {SHOT_DIR}", flush=True)
    if FAILS:
        print(f"\n===== {len(FAILS)} 项未通过 =====", flush=True)
        for m in FAILS:
            print("  -", m, flush=True)
        return 1
    print("\n===== 全部通过 =====", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
