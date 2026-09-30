# -*- coding: utf-8 -*-
"""verify_nav_groups.py — 侧栏「四组 + 多组同时展开 + 动画 + 溢出滚动」验证。

2026-09-29 侧栏重构的**分组契约**验证脚本。它接替了被废止的
``verify_nav_drag.py`` / ``qa_verify_nav_drag.py`` / ``verify_nav_plugin_btn.py``
里那些建立在"8 项平铺、可跨组拖动"之上的断言；拖拽**机制**的深度验证
仍在 ``verify_nav_live_letway.py``（两者互补，都要跑）。

覆盖点：
  A. 默认展开集合 = workbench；只有该组条目可见
  B. 四组标题恒在（收起组也留标题）；箭头是自绘控件
  C. 槽位连续、无重叠、行高不被压扁
  D. ★ 多组同时展开：切 tools / system 不收起 workbench
  E. ★ 展开动画真实存在于中间态（高度与箭头角度都取到中间值，不是瞬变）
  F. 折叠：高度归零 → 条目隐藏；全部折叠是合法状态并落盘
  G. 溢出：注册大量插件页 → 出现滚动条，且条目**不被压扁**（行高保持）
  H. ★ 多组展开下的组内拖拽：只改该组槽位，其它组条目几何完全不动
  I. 选中条守卫：选中页在折叠组里 → 指示条隐藏；展开后回来
  J. 落盘/回读：nav_expanded_groups 持久化
  K. 插件页注册幂等 / 注销 / 再注册

用法（必须 offscreen，**别**直接 python 跑）：
  python tools/run_gui_check.py tools/verify_nav_groups.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QFontDatabase, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402
from src import nav_layout as nl  # noqa: E402

SHOT_DIR = os.path.join(os.path.dirname(BASE), "build", "shots")
os.makedirs(SHOT_DIR, exist_ok=True)
FAILS = []


def check(cond, msg):
    if cond:
        print(f"[OK] {msg}", flush=True)
    else:
        FAILS.append(msg)
        print(f"[!!] {msg}", flush=True)


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def sample_during(app, watch, ms, step=12):
    """在 ms 毫秒内反复泵事件并采样 watch()（用于取证动画中间态）"""
    out = []
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        out.append(watch())
        time.sleep(step / 1000.0)
    return out


def ev(btn, etype, global_pos, buttons=Qt.MouseButton.LeftButton):
    e = QMouseEvent(etype, QPointF(btn.mapFromGlobal(global_pos)),
                    QPointF(global_pos), Qt.MouseButton.LeftButton,
                    buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, e)


def visible_items(win):
    return [k for k, b in win._nav_btns.items() if b.isVisible()]


def geoms(win, keys):
    return {k: (win._nav_btns[k].y(), win._nav_btns[k].height()) for k in keys}


def main():
    app = QApplication(sys.argv)
    QApplication.setApplicationName("nav_groups_check")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_navgrp_")
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    cfg_path = os.path.join(data, "config.json")
    cfg = ConfigManager(cfg_path)
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data, "docx_meta.json"))
    docx.load()
    win = MainWindow(TaskManager(os.path.join(data, "schedule.json")),
                     NoteManager(os.path.join(data, "notes.json")),
                     FragmentManager(os.path.join(data, "fragments.json")),
                     docx, cfg,
                     ClipboardMonitor(FragmentManager(os.path.join(data, "f2.json")), cfg),
                     TempAssetManager(tmp))
    win.resize(920, 620)          # ★ 最小窗口尺寸（最容易溢出的档位）
    win.show()
    pump(app, 1200)               # show 后先泵 ≥1200ms 再谈几何

    hdr = win._nav_group_headers

    # ---------------- A. 默认展开集合 ----------------
    check(win._expanded_groups() == ("workbench",),
          f"A. 默认展开集合 = ('workbench',)（实际 {win._expanded_groups()}）")
    vis = visible_items(win)
    check(set(vis) == {"fragments", "tasks", "notes", "knowledge", "assets"},
          f"A. 可见条目 = 工作台五项（实际 {vis}）")
    check(not win._settings_btn.isVisible() and not win._help_btn.isVisible(),
          "A. 设置/使用说明随 system 组收起而隐藏")

    # ---------------- B. 组标题 + 自绘箭头 ----------------
    check(len(hdr) == 4 and all(h.isVisible() for h in hdr.values()),
          "B. 四个组标题全部可见（收起组也留标题，否则找不到在哪展开）")
    check(all(hasattr(h, "arrow") for h in hdr.values()),
          "B. 组标题带自绘箭头子控件（NavArrow，不依赖字体字形）")
    check(hdr["workbench"].is_expanded() and not hdr["tools"].is_expanded(),
          "B. 箭头展开态与集合一致")
    check(abs(hdr["workbench"].arrow.angle - 90.0) < 1.0
          and abs(hdr["tools"].arrow.angle) < 1.0,
          f"B. 箭头角度 90°/0°（实际 {hdr['workbench'].arrow.angle}"
          f"/{hdr['tools'].arrow.angle}）")
    check(hdr["workbench"].text() == "工作台"
          and "\u25bc" not in hdr["workbench"].text(),
          f"B. 标题文案不含字符箭头（{hdr['workbench'].text()!r}）")

    # ---------------- C. 槽位几何 ----------------
    cv = win._nav_btns_layout
    check(cv.indexOf(win._settings_btn) >= 0,
          "C. 设置按钮仍是布局成员（隐藏 ≠ 移出，indexOf 有效）")
    heights = {b.height() for b in win._nav_btns.values() if b.isVisible()}
    check(len(heights) == 1 and min(heights) >= 33,
          f"C. 可见条目行高一致且未被压扁（{heights}）")
    ys = sorted(b.y() for b in win._nav_btns.values() if b.isVisible())
    step = ys[1] - ys[0]
    check(all(ys[i + 1] - ys[i] == step for i in range(len(ys) - 1)),
          f"C. 槽位间距恒定（step={step}，行间距由 QSS margin-bottom 提供）")
    check(ys[0] >= hdr["workbench"].geometry().bottom(),
          "C. 组内首项排在组标题下方")

    # ---------------- E. ★ 展开动画的中间态取证 ----------------
    tools_items = [win._nav_btns["apps"], win._nav_btns["nav"]]
    probe = hdr["tools"]
    win._on_group_toggled("tools")
    heights_seen = sample_during(app, lambda: tools_items[0].height(), 420)
    pump(app, 500)
    row_h = win._nav_row_height()
    mid_h = [h for h in heights_seen if 0 < h < row_h]
    check(len(mid_h) >= 1,
          f"E. ★ 展开时取到**中间高度**"
          f"（采样 {sorted(set(heights_seen))[:6]}… 目标 {row_h}） → 过渡动画而非瞬变")
    check(tools_items[0].height() == row_h,
          f"E. 动画结束后高度落到目标 {row_h}（实际 {tools_items[0].height()}）")
    check(abs(probe.arrow.angle - 90.0) < 1.0,
          f"E. 箭头转到 90°（实际 {probe.arrow.angle}）")
    check(hdr["tools"].is_expanded(), "E. 展开态已记入集合")

    # ---------------- D. ★ 多组同时展开 ----------------
    check(win._expanded_groups() == ("workbench", "tools"),
          f"D. workbench 未被收起（实际 {win._expanded_groups()}）")
    vis = set(visible_items(win))
    check(vis == {"fragments", "tasks", "notes", "knowledge", "assets",
                  "apps", "nav"},
          f"D. 两组条目同时在侧栏（实际 {sorted(vis)}）")
    win._on_group_toggled("system")
    pump(app, 600)
    check(set(win._expanded_groups()) == {"workbench", "tools", "system"},
          f"D. 三组同时展开（实际 {win._expanded_groups()}）")
    check(win._settings_btn.isVisible() and win._help_btn.isVisible(),
          "D. system 组条目可见")
    check(cfg.get("nav_expanded_groups", None) == list(win._expanded_groups()),
          "J. 展开集合已落盘（list）")
    win.grab().save(os.path.join(SHOT_DIR, "navgrp-920-multi.png"))

    # ---------------- I. 选中条守卫 ----------------
    win._switch_page(6)                      # 设置页（在 system 组，此刻展开）
    pump(app, 300)
    check(win._nav_indicator.isVisible() is True, "I. 展开组内的选中项 → 指示条可见")
    win._on_group_toggled("system")          # 收起它
    pump(app, 600)
    check(win._settings_btn.isVisible() is False, "I. system 组已收起")
    check(win._nav_indicator.isVisible() is False,
          "I. 选中页所在组收起 → 指示条隐藏（不指向看不见的条目）")

    # ---------------- F. 折叠：高度归零 ----------------
    win._on_group_toggled("tools")           # 此刻展开集合 = workbench + tools
    heights_seen = sample_during(app, lambda: tools_items[0].height(), 400)
    pump(app, 400)
    mid_h = [h for h in heights_seen if 0 < h < row_h]
    check(len(mid_h) >= 1,
          f"F. ★ 折叠时也取到中间高度（采样 {sorted(set(heights_seen))[:6]}…）")
    check(tools_items[0].isVisible() is False,
          "F. 折叠结束后条目隐藏（隐藏项不再参与拖拽/指示条）")
    check(hdr["tools"].arrow.angle < 1.0, "F. 箭头转回 0°")

    # 全部折叠 = 合法状态
    win._on_group_toggled("workbench")
    pump(app, 800)
    check(win._expanded_groups() == () and visible_items(win) == [],
          f"F. 全部折叠是合法状态（实际 {win._expanded_groups()}）")
    check(cfg.get("nav_expanded_groups", None) == [],
          "F. 全折叠落盘为空列表（不回退默认，否则重启后自己弹回来）")
    win.grab().save(os.path.join(SHOT_DIR, "navgrp-920-allcollapsed.png"))
    win._on_group_toggled("workbench")
    pump(app, 700)

    # ---------------- G. 溢出 ----------------
    N = 20
    for i in range(N):
        win.register_plugin_page(f"plugin:demo{i}", f"演示插件{i}", QWidget())
    pump(app, 600)
    win._on_group_toggled("plugin_pages")
    pump(app, 1000)
    pg = [k for k in win._nav_order if nl.is_plugin_key(k) and k in win._nav_btns]
    check(len(pg) == N, f"G. 注册了 {N} 个插件页（实际 {len(pg)}）")
    check(all(win._nav_btns[k].isVisible() for k in pg), "G. 插件组条目全部可见")
    bar = win._nav_scroll.verticalScrollBar()
    check(bar.maximum() > 0, f"G. 溢出后出现滚动（maximum={bar.maximum()}）")
    hts = {win._nav_btns[k].height() for k in pg}
    check(len(hts) == 1 and min(hts) >= 33,
          f"G. ★ 溢出条目不被打扁，行高仍为 {hts}（旧实现会压到 17px 直到文字消失）")
    ylist = sorted(win._nav_btns[k].y() for k in pg)
    d = {ylist[i + 1] - ylist[i] for i in range(len(ylist) - 1)}
    check(len(d) == 1, f"G. 溢出时槽位间距仍恒定 {d}")
    bar.setValue(bar.maximum())
    pump(app, 300)
    win.grab().save(os.path.join(SHOT_DIR, "navgrp-920-plugin20-bottom.png"))
    bar.setValue(0)
    pump(app, 300)

    # ---------------- H. ★ 多组展开下的组内拖拽 ----------------
    wb = [k for k in win._nav_order if nl.group_of(k) == "workbench"]
    wb_before = geoms(win, wb)
    order_before = list(win._nav_order)
    first, second = pg[0], pg[1]
    stepPx = win._nav_btns[second].y() - win._nav_btns[first].y()
    drag_btn = win._nav_btns[first]
    anchor = drag_btn.mapToGlobal(drag_btn.rect().center())
    ev(drag_btn, QEvent.Type.MouseButtonPress, anchor)
    ev(drag_btn, QEvent.Type.MouseMove, anchor + QPoint(0, 20))
    check(win._nav_drag_btn is drag_btn, "H. 进入拖拽")
    check(win._nav_drag_group == "plugin_pages",
          f"H. ★ 冻结的是被拖按钮所在组（实际 {win._nav_drag_group}）")
    check(geoms(win, wb) == wb_before,
          "H. ★ 拖拽中其它组条目几何完全不动（spacer 插在本组标题之后）")
    ev(drag_btn, QEvent.Type.MouseMove, anchor + QPoint(0, int(stepPx * 2.2)))
    pump(app, 250)
    mid = list(win._nav_drag_order)
    check(mid.index(first) > 0, f"H. 拖拽中实时让位（{mid[:3]}）")
    ev(drag_btn, QEvent.Type.MouseButtonRelease,
       anchor + QPoint(0, int(stepPx * 2.2)), Qt.MouseButton.NoButton)
    pump(app, 800)
    after = list(win._nav_order)
    check(geoms(win, wb) == wb_before, "H. ★ 落定后其它组条目几何仍不动")
    check(all(order_before.index(k) == after.index(k)
              for k in order_before if nl.group_of(k) != "plugin_pages"),
          "H. ★ 非本组键的槽位完全不动（组内拖拽只改组内相对顺序）")
    check(after.index(first) != order_before.index(first), "H. 组内顺序已变")
    check(sorted(after) == sorted(order_before), "H. 顺序表键集合不变（无丢失）")
    check(cfg.get("nav_order", []) == after, "H. 新顺序已落盘")
    check(win._nav_free_layout is False and win._nav_free_spacer is None,
          "H. 布局已交还，无残留 spacer")

    # ---------------- K. 插件页注册 / 幂等 / 注销 ----------------
    keep_key = pg[1]
    before_count = len(win._nav_btns)
    _ = win.register_plugin_page(keep_key, "演示插件（改名）", QWidget())  # 幂等性靠下方按钮数校验
    pump(app, 300)
    check(len(win._nav_btns) == before_count,
          "K. 重复注册同一 key 不新增按钮（幂等）")
    check(win._nav_btns[keep_key].text() == "演示插件（改名）",
          f"K. 重复注册只更新文案（实际 {win._nav_btns[keep_key].text()!r}）")
    check(win._nav_btns[keep_key].isVisible(),
          "K. 重复注册后按钮仍在展开的插件组里（布局没被摘坏）")
    ok_unreg = win.unregister_plugin_page(keep_key)
    pump(app, 400)
    check(ok_unreg and keep_key not in win._nav_btns,
          "K. 注销插件页：按钮被摘除")
    check(keep_key not in win._nav_order,
          "K. 注销插件页：顺序表里也同步摘除（否则重排会引用已删按钮）")
    check(all(win._nav_btns[k].isVisible()
              for k in win._nav_order
              if nl.is_plugin_key(k) and k in win._nav_btns),
          "K. 注销后其余插件键几何仍然有效（隐藏≠漂浮）")
    idx_back = win.register_plugin_page(keep_key, "演示插件", QWidget())
    pump(app, 500)
    check(idx_back >= 10 and keep_key in win._nav_btns,
          f"K. 注销后再注册走全新注册分支（新索引 {idx_back}）")
    check(win._nav_btns[keep_key].isVisible(), "K. 再注册后按钮可见")

    # ---------------- 主题两版截图 ----------------
    win.apply_external_theme("dark")
    pump(app, 800)
    win.grab().save(os.path.join(SHOT_DIR, "navgrp-920-dark.png"))
    win.apply_external_theme("light")
    pump(app, 800)
    win.grab().save(os.path.join(SHOT_DIR, "navgrp-920-light.png"))
    win.apply_external_theme("dark")
    pump(app, 400)

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
