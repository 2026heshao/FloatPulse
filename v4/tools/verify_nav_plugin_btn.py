# -*- coding: utf-8 -*-
# ====================================================================
# ⚠ 已废止（OBSOLETE / 2026-09-29）—— 请勿据此判定回归
# --------------------------------------------------------------------
# 本脚本的断言全部建立在旧侧栏模型之上："8 项平铺、可跨组拖拽、槽位下标
# = nav_order 下标 + 1、任意两项都相邻"。2026-09-29 侧栏重构为
# 「四组 + 多组同时展开 + 溢出滚动」后这些前提都不再成立：
#   · 拖拽**只发生在组内**（跨组拖动已按设计取消），"把 apps 拖到最前"
#     这类场景本身不再有意义；
#   · 默认只有 1 个组展开，其余组条目是隐藏态、几何为陈旧值；
#   · ``_freeze_nav_buttons()`` 现在必须传入组名（按被拖按钮所属组冻结）。
#
# 替代脚本（两者互补，都要跑）：
#   tools/verify_nav_groups.py       分组 / 展开集合 / 动画 / 溢出 / 组内拖拽
#                                     / 选中条守卫 / 插件页注册注销
#   tools/verify_nav_live_letway.py  拖拽机制（实时让位、跟手、钳制、落定、
#                                     中断回滚、连续拖拽、无残留）
#
# 文件保留仅为历史留痕。要恢复覆盖请**按新语义重写断言**，
# 而不是撤掉分组或让拖拽跨组。
# ====================================================================
import sys as _sys

print("[OBSOLETE] 本脚本的断言基于旧版平铺侧栏，"
      "已于 2026-09-29 废止。请改跑："
      "tools/verify_nav_groups.py + tools/verify_nav_live_letway.py", flush=True)
_sys.exit(0)

"""
verify_nav_plugin_btn.py — 插件页导航键 × 拖拽换位布局验证

用户实测（2026-09-28）：换位后「设置」震荡一下、插件页键（AI 助手）
与「设置」重叠。根因：_apply_nav_order 按 _nav_btns.values() 全量摘除
布局项，但插件页键也在 _nav_btns 里（不参与换位、不在 order）——
被摘下后无人插回 → 布局塌缩一行 → 设置上移 + 插件键自由漂浮重叠。

运行：python tools/run_gui_check.py tools/verify_nav_plugin_btn.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication       # noqa: E402
from PyQt6.QtGui import QFontDatabase                   # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.config import ConfigManager                    # noqa: E402
from src.main_window import MainWindow                  # noqa: E402
from src.docx_manager import DocxManager                # noqa: E402
from src.task_manager import TaskManager                # noqa: E402
from src.note_manager import NoteManager                # noqa: E402
from src.fragment_manager import FragmentManager        # noqa: E402
from src.nav_manager import NavManager                  # noqa: E402
from src.temp_asset_manager import TempAssetManager     # noqa: E402
from src.clipboard_monitor import ClipboardMonitor      # noqa: E402
from src.app_paths import get_base_dir, get_docx_path    # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


root = tempfile.mkdtemp(prefix="fp_nav_btn_")
tmp_cfg = tempfile.mkdtemp()
cm = ConfigManager(os.path.join(tmp_cfg, "config.json"))

_base = get_base_dir()
docx = DocxManager(get_docx_path(_base), os.path.join(tmp_cfg, "docx_meta.json"))
docx.load()
frags = FragmentManager(os.path.join(tmp_cfg, "fragments.json"))
win = MainWindow(TaskManager(os.path.join(tmp_cfg, "schedule.json")),
                 NoteManager(os.path.join(tmp_cfg, "notes.json")),
                 frags, docx, cm, ClipboardMonitor(frags, cm),
                 temp_asset_manager=TempAssetManager(_base),
                 nav_manager=NavManager(os.path.join(root, "nav.json")))
win.resize(1280, 740)
win.show()
app.processEvents()
app.processEvents()

lay = win._nav_btns_layout
settings_btn = win._settings_btn


def geo_y(btn):
    return btn.geometry().y()


# ---------- 基线：注册插件页（复刻 AI 助手键） ----------
from PyQt6.QtWidgets import QWidget                     # noqa: E402

idx = win.register_plugin_page("plugin:demo", "🤖 AI 助手", QWidget())
app.processEvents()
btn = win._nav_btns["plugin:demo"]
check("A1. 插件页键已注册并进布局（夹在功能键与设置之间）",
      idx >= 10 and btn.parentWidget() is settings_btn.parentWidget(),
      f"idx={idx}")

check("A2. 插件键位于设置键上方且不重叠",
      geo_y(btn) + btn.height() <= geo_y(settings_btn) + 1,
      f"btn_y={geo_y(btn)} h={btn.height()} settings_y={geo_y(settings_btn)}")

settings_y0 = geo_y(settings_btn)
help_btn = [b for b in settings_btn.parentWidget().findChildren(type(settings_btn))
            if "使用说明" in b.text()][0]
help_y0 = geo_y(help_btn)


# ---------- B. 换位后：设置/说明不动、插件键不漂移 ----------
order = list(win._nav_order)
order[0], order[1] = order[1], order[0]          # 换前两个键
win._apply_nav_order(order, save=True)
app.processEvents()
app.processEvents()

check("B1. ★换位后设置键 y 不变（此前被布局塌缩顶上来再弹回=震荡）",
      geo_y(settings_btn) == settings_y0,
      f"before={settings_y0} after={geo_y(settings_btn)}")

check("B2. ★换位后使用说明键 y 不变（同塌缩受害者）",
      geo_y(help_btn) == help_y0,
      f"before={help_y0} after={geo_y(help_btn)}")

check("B3. ★插件页键仍在布局里（geometry 有效、没变成自由漂浮）",
      btn.parentWidget() is settings_btn.parentWidget()
      and geo_y(btn) > 0
      and lay.indexOf(btn) >= 0,
      f"y={geo_y(btn)} layout_idx={lay.indexOf(btn)}")

check("B4. 插件键与设置键不重叠（截图 bug 消失）",
      geo_y(btn) + btn.height() <= geo_y(settings_btn) + 1,
      f"btn_bottom={geo_y(btn) + btn.height()} settings_y={geo_y(settings_btn)}")

# ---------- C. 换位后插件键仍紧贴末位功能键之下 ----------
# 注意：插件键参与换位后 order 里也含 plugin: 键，order[-1] 可能就是
# 插件键自己——必须取**最后一个固定功能键**作参照。
_last_fixed = [k for k in order if not k.startswith("plugin:")][-1]
_last_bottom = (win._nav_btns[_last_fixed].geometry().y()
                + win._nav_btns[_last_fixed].height())
check("C1. 插件键紧跟末位功能键（间距 ≤ 布局 spacing，无空隙无重叠）",
      0 <= geo_y(btn) - _last_bottom <= max(2, lay.spacing() + 1),
      f"last_fn={_last_fixed} last_fn_bottom={_last_bottom} "
      f"btn_y={geo_y(btn)} spacing={lay.spacing()}")

# ---------- D. 拖拽冻结/恢复路径同样安全 ----------
ok_d = win._freeze_nav_buttons()
app.processEvents()
settings_y_frozen = geo_y(settings_btn)
win._restore_nav_layout()
app.processEvents()
check("D1. 冻结→恢复后设置键 y 不变（spacer 撑住功能键空间）",
      ok_d and geo_y(settings_btn) == settings_y0,
      f"frozen={settings_y_frozen} restored={geo_y(settings_btn)}")
check("D2. 冻结→恢复后插件键 y 不变",
      geo_y(btn) == settings_y0 - btn.height() - lay.spacing()
      or geo_y(btn) < settings_y0,
      f"btn_y={geo_y(btn)} settings_y={geo_y(settings_btn)}")

# ---------- E. 拖拽落定端到端（真实回调） ----------
drag_key = order[0]
drag_btn = win._nav_btns[drag_key]
from PyQt6.QtCore import QPoint  # noqa: E402

# 注入替身 + 调真实回调（离屏驱动惯例）：press → drag_started → moved → finished
drag_btn._press_global = drag_btn.mapToGlobal(QPoint(10, 10))
win._on_nav_drag_started(drag_btn)
target_y = win._nav_slot_ys[-1]           # 拖到最末槽位
global_target = win._nav_area.mapToGlobal(
    QPoint(drag_btn.x(), target_y + win._nav_drag_grab_dy))
win._on_nav_drag_moved(drag_btn, global_target)
win._on_nav_drag_finished(drag_btn, global_target)
app.processEvents()
app.processEvents()

check("E1. 拖拽落定：被拖键到了末槽位（换位生效）",
      win._nav_order[-1] == drag_key,
      f"order={win._nav_order}")

check("E2. 拖拽落定后设置键 y 仍不变（无震荡）",
      geo_y(settings_btn) == settings_y0,
      f"after_drag={geo_y(settings_btn)} baseline={settings_y0}")

check("E3. 拖拽落定后插件键不漂移不重叠",
      geo_y(btn) + btn.height() <= geo_y(settings_btn) + 1
      and lay.indexOf(btn) >= 0,
      f"y={geo_y(btn)} layout_idx={lay.indexOf(btn)}")

win._force_end_nav_drag()                 # 清场，防动画残留

# ====================================================================
# F. 插件页键参与换位（2026-09-28 用户要求：AI 助手先行，后续页面插件一致）
# ====================================================================
check("F1. 插件页键可拖（nav_key 非空 + 拖拽信号已连）",
      btn.nav_key == "plugin:demo"
      and btn._is_dragging is not None,          # 信号连接无法直接断言，drag 态存在即可
      f"nav_key={btn.nav_key!r}")

# 注销再注册第二个插件，验证多插件 + 记忆位置
idx2 = win.register_plugin_page("plugin:second", "🧩 第二插件", QWidget())
app.processEvents()
btn2 = win._nav_btns["plugin:second"]
check("F2. 第二个插件键注册（同样可拖）",
      idx2 == 11 and btn2.nav_key == "plugin:second",
      f"idx={idx2} nav_key={btn2.nav_key!r}")

# F2 注册多了一个键，设置键自然下移——「不震荡」基线必须在此重取
settings_y1 = geo_y(settings_btn)

# 拖 demo 键到第 3 槽位（真实回调端到端），落定后顺序落盘
drag_btn = win._nav_btns["plugin:demo"]
drag_btn._press_global = drag_btn.mapToGlobal(QPoint(10, 10))
win._on_nav_drag_started(drag_btn)
target_y = win._nav_slot_ys[2]
global_target = win._nav_area.mapToGlobal(
    QPoint(drag_btn.x(), target_y + win._nav_drag_grab_dy))
win._on_nav_drag_moved(drag_btn, global_target)
win._on_nav_drag_finished(drag_btn, global_target)
app.processEvents()

check("F3. ★插件键拖到中间槽位：order 更新且含插件 key",
      win._nav_order[2] == "plugin:demo"
      and "plugin:second" in win._nav_order,
      f"order={win._nav_order}")

saved = cm._config.get("nav_order") if hasattr(cm, "_config") else None
check("F4. ★换位顺序已落盘（config nav_order 含插件 key）",
      isinstance(saved, list) and "plugin:demo" in saved
      and saved[2] == "plugin:demo",
      f"saved={saved}")

check("F5. 换位后设置键仍不震荡（回归钉，基线取自 F2 注册后）",
      geo_y(settings_btn) == settings_y1,
      f"y={geo_y(settings_btn)} baseline={settings_y1}")

# ---------- F6. 模拟重启：新 MainWindow 同 config → 记忆位置沿用 ----------
win._force_end_nav_drag()
win.hide()
win2 = MainWindow(TaskManager(os.path.join(root, "schedule2.json")),
                  NoteManager(os.path.join(root, "notes2.json")),
                  FragmentManager(os.path.join(root, "fragments2.json")),
                  docx, cm, ClipboardMonitor(
                      FragmentManager(os.path.join(root, "fragments2.json")), cm),
                  temp_asset_manager=TempAssetManager(_base),
                  nav_manager=NavManager(os.path.join(root, "nav2.json")))
win2.resize(1280, 740)
win2.show()
app.processEvents()
app.processEvents()

check("F6. 重启后 sidebar 只建固定键（插件键待注册补建）",
      "plugin:demo" not in win2._nav_btns
      and "plugin:second" not in win2._nav_btns
      and len(win2._nav_order) == 10          # 8 固定 + demo + second 记忆
      and [k for k in win2._nav_order if not k.startswith("plugin:")]
      and sum(1 for k in win2._nav_order if k.startswith("plugin:")) == 2,
      f"order={win2._nav_order}")

win2.register_plugin_page("plugin:demo", "🤖 AI 助手", QWidget())
app.processEvents()
btn_r = win2._nav_btns["plugin:demo"]
check("F7. ★重启注册后插件键回到记忆位置（第 3 槽位，不回尾部）",
      win2._nav_order[2] == "plugin:demo"
      and win2._nav_btns_layout.indexOf(btn_r) == 3,   # slot = index+1
      f"order={win2._nav_order} layout_idx={win2._nav_btns_layout.indexOf(btn_r)}")

check("F8. 重启后插件键仍可拖（nav_key 非空）",
      btn_r.nav_key == "plugin:demo",
      f"nav_key={btn_r.nav_key!r}")

check("F10. ★注册第一插件不挤掉未注册插件键的记忆位置",
      "plugin:second" in win2._nav_order,
      f"order={win2._nav_order}")

idx2r = win2.register_plugin_page("plugin:second", "🧩 第二插件", QWidget())
app.processEvents()
check("F11. 重启注册第二插件回到记忆位置（末位）",
      idx2r == 11 and win2._nav_order[-1] == "plugin:second"
      and win2._nav_btns["plugin:second"].nav_key == "plugin:second",
      f"idx={idx2r} order={win2._nav_order}")

# ---------- F9. 注销插件页：键移除 + 顺序表同步 ----------
ok_un = win2.unregister_plugin_page("plugin:demo")
app.processEvents()
check("F9. 注销后键消失、order 移除（不残留死引用）",
      ok_un and "plugin:demo" not in win2._nav_btns
      and "plugin:demo" not in win2._nav_order
      and win2._nav_btns_layout.indexOf(btn_r) == -1,
      f"in_btns={'plugin:demo' in win2._nav_btns} "
      f"in_order={'plugin:demo' in win2._nav_order}")

win2._force_end_nav_drag()

# ---------- G. 显示前注册（启动时序回归钉：首开导航键错乱、拖一下才恢复） ----------
# 根因：register_plugin_page 在窗口 show 之前走 _apply_nav_order，
# 落定动画以 show 前未校准的几何为起止值，show 后布局不再主动重排，
# 按钮被动画钉死在错位上。修复=不可见时不播动画。
from PyQt6.QtTest import QTest                              # noqa: E402

win3 = MainWindow(TaskManager(os.path.join(root, "schedule3.json")),
                  NoteManager(os.path.join(root, "notes3.json")),
                  FragmentManager(os.path.join(root, "fragments3.json")),
                  docx, cm, ClipboardMonitor(
                      FragmentManager(os.path.join(root, "fragments3.json")), cm),
                  temp_asset_manager=TempAssetManager(_base),
                  nav_manager=NavManager(os.path.join(root, "nav3.json")))
win3.resize(1280, 740)
idx3 = win3.register_plugin_page("plugin:demo2", "🧩 演示", QWidget())   # show 之前
# ★核心不变量：窗口不可见时绝不播落定动画（动画会把按钮钉在 show 前
#   未校准的几何上 → 首开错乱、拖一下才恢复；离屏几何前后一致复现不了
#   错位本身，故直接钉「动画未启动」这个行为）
check("G0. 显示前注册不启动落定动画（首开错乱根因钉）",
      win3._nav_settle_animations == [],
      f"anims={len(win3._nav_settle_animations)}")
win3.show()
for _ in range(5):
    app.processEvents()
QTest.qWait(350)                 # 越过可能的落定动画时长（修复后不会启动）
for _ in range(3):
    app.processEvents()

btns3 = [win3._nav_btns[k] for k in win3._nav_order if k in win3._nav_btns]
ys3 = [b.geometry().y() for b in btns3]
steps3 = [ys3[i + 1] - ys3[i] for i in range(len(ys3) - 1)]
check("G1. 显示前注册：show 后按钮等距无错位（动画未钉错位）",
      len(set(steps3)) == 1 and ys3 == sorted(ys3),
      f"steps={steps3} ys={ys3}")

check("G2. 显示前注册：插件键在布局里、顺序追加末位",
      win3._nav_btns_layout.indexOf(win3._nav_btns["plugin:demo2"]) >= 0
      and win3._nav_order[-1] == "plugin:demo2",
      f"order={win3._nav_order} "
      f"layout_idx={win3._nav_btns_layout.indexOf(win3._nav_btns['plugin:demo2'])}")

check("G3. 显示前注册：插件键与设置键不重叠",
      win3._nav_btns["plugin:demo2"].geometry().y()
      + win3._nav_btns["plugin:demo2"].height()
      <= win3._settings_btn.geometry().y() + 1,
      f"btn_bottom={win3._nav_btns['plugin:demo2'].geometry().y() + win3._nav_btns['plugin:demo2'].height()} "
      f"settings_y={win3._settings_btn.geometry().y()}")

# 反向钉：可见状态重排必须照常播落定动画（守卫只限不可见期）
_o = list(win3._nav_order)
_o[0], _o[1] = _o[1], _o[0]
win3._apply_nav_order(_o, save=False)
check("G4. 可见状态重排仍播落定动画（守卫不过度抑制）",
      len(win3._nav_settle_animations) > 0,
      f"anims={len(win3._nav_settle_animations)}")
win3._abort_nav_settle_animations()
win3._apply_nav_order(list(win3._nav_order), save=False)   # 换回，清场
win3._abort_nav_settle_animations()

print()
failed = [r for r in results if not r[1]]
print(f"[DONE] {len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    sys.exit(1)
sys.exit(0)
