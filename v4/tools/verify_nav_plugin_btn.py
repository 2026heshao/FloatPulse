# -*- coding: utf-8 -*-
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

from PyQt6.QtWidgets import QApplication, QLabel       # noqa: E402
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

# ---------- C. 换位后插件键仍紧贴第 8 功能键之下 ----------
_last_bottom = (win._nav_btns[order[-1]].geometry().y()
                + win._nav_btns[order[-1]].height())
check("C1. 插件键紧跟末位功能键（间距 ≤ 布局 spacing，无空隙无重叠）",
      0 <= geo_y(btn) - _last_bottom <= max(2, lay.spacing() + 1),
      f"last_fn_bottom={_last_bottom} btn_y={geo_y(btn)} spacing={lay.spacing()}")

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
from PyQt6.QtCore import QPoint, QPointF, QEvent  # noqa: E402

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

print()
failed = [r for r in results if not r[1]]
print(f"[DONE] {len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    sys.exit(1)
sys.exit(0)
