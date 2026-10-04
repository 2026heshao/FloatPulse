# -*- coding: utf-8 -*-
"""插件独立设置离屏验证（2026-10-04）——校验 + 真实渲染截图（light / dark）。

跑法：python tools/verify_plugin_settings.py
产物：build/shots/plugin-settings-dialog-{light,dark}.png（设置弹层）
      build/shots/plugin-settings-page-{light,dark}.png（插件中心页，
      kb-search 卡片带「设置」按钮）

验证项：
  A. kb-search 真实包的 manifest.settings 校验通过（端到端样例）
  B. 有 settings 的插件卡片有「设置」按钮，无 settings 的没有
     （2026-10-04 扩展后：五插件有、recurring-tasks 无）
  C. 弹层按 schema 渲染：bool→ToggleSwitch / int·float→Stepper /
     enum→QComboBox，初始值来自插件私有目录的生效值
  D. 保存流：改值 → set_one 落盘（插件私有目录）→ ctx.settings_changed
     收到改动 key 列表；未改动的项不写盘
  E. light / dark 两主题下弹层与插件中心页截图落盘
  F. 四插件（ai-assistant / ai-text-workshop / vault / weekly-report）
     的弹层渲染 + 保存流 + settings_changed 生效链（页面状态即时更新；
     weekly-report 按设计不订阅，验证新开对话框按生效值勾选）
"""
import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtGui import QFontDatabase                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox          # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.config import ConfigManager                         # noqa: E402
from src.main_window import MainWindow                       # noqa: E402
from src.plugin_api import ActionRegistry, PluginContext     # noqa: E402
from src.plugin_loader import PluginLoader                   # noqa: E402
from src.docx_manager import DocxManager                     # noqa: E402
from src.task_manager import TaskManager                     # noqa: E402
from src.note_manager import NoteManager                     # noqa: E402
from src.fragment_manager import FragmentManager             # noqa: E402
from src.nav_manager import NavManager                       # noqa: E402
from src.temp_asset_manager import TempAssetManager          # noqa: E402
from src.clipboard_monitor import ClipboardMonitor           # noqa: E402
from src.app_paths import get_base_dir, get_docx_path        # noqa: E402
from src.controls import IconButton, Stepper, ToggleSwitch   # noqa: E402
from src.plugin_settings import settings_file                # noqa: E402
from src.plugins_panel import (                              # noqa: E402
    PluginsPanel, build_plugin_settings_dialog,
)
from src.plugin_loader import validate_manifest              # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


OUT_DIR = os.path.join(BASE, "..", "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

tmp_root = tempfile.mkdtemp(prefix="fp_verify_plugin_settings_")
tmp_cfg = tempfile.mkdtemp(prefix="fp_verify_plugin_settings_cfg_")
# 插件私有数据目录（设置 settings.json 落这里，绝不碰真实 float_data）
data_root = os.path.join(tmp_root, "float_data")
plugins_data_base = os.path.join(data_root, "plugins")

cm = ConfigManager(os.path.join(tmp_cfg, "config.json"))
cm.set("plugins_enabled", True)
cm.set("theme", "light")

_base = get_base_dir()
docx = DocxManager(get_docx_path(_base), os.path.join(tmp_cfg, "docx_meta.json"))
docx.load()
frags = FragmentManager(os.path.join(tmp_cfg, "fragments.json"))
win = MainWindow(TaskManager(os.path.join(tmp_cfg, "schedule.json")),
                 NoteManager(os.path.join(tmp_cfg, "notes.json")),
                 frags, docx, cm, ClipboardMonitor(frags, cm),
                 temp_asset_manager=TempAssetManager(_base),
                 nav_manager=NavManager(os.path.join(tmp_cfg, "nav.json")))
win.resize(1280, 740)

ctx = PluginContext(logger=None, config=cm.as_dict(),
                    show_toast=win.show_toast,
                    parent_window=lambda: win,
                    # 插件私有可写目录 → 临时目录（与生产 knowledge_ball 同构）
                    data_dir_base=plugins_data_base)
loader = PluginLoader(ActionRegistry(logger=None), ctx,
                      # 加载真实插件包（kb-search 带 settings 声明）
                      plugins_dir=os.path.join(_base, "plugins"),
                      logger=None,
                      store_dir=os.path.join(_base, "plugin_store"))
win.set_plugin_loader(loader)
loaded = loader.load_all()
app.processEvents()

kb_lp = next((p for p in loaded if p.plugin_id == "kb-search"), None)

# ---------- A. 真实包 manifest.settings 校验 ----------
check("A1. kb-search 加载成功", kb_lp is not None)
if kb_lp is not None:
    m, err = validate_manifest(kb_lp.manifest)
    ok_a2 = m is not None and len(m.get("settings") or []) == 3
    detail = err if m is None else f"keys={[e['key'] for e in m['settings']]}"
    check("A2. manifest.settings 校验通过", ok_a2, detail)
    check("A3. ctx 拿到设置 schema",
          len(getattr(kb_lp.ctx, "manifest_settings", ()) or ()) == 3)

# ---------- B. 卡片设置按钮存在性 ----------
panel = PluginsPanel(win)
panel.refresh()
app.processEvents()


def _find_card(plugin_id):
    for card, lp in panel._card_records:
        if getattr(lp, "plugin_id", "") == plugin_id:
            return card
    return None


def _has_settings_button(card):
    return bool([b for b in card.findChildren(IconButton)
                 if b.text() == "设置"])


kb_card = _find_card("kb-search")
check("B1. kb-search 卡片有「设置」按钮",
      kb_card is not None and _has_settings_button(kb_card))

# B2（2026-10-04 扩展后）：五个带 settings 的插件都有按钮
SETTINGS_PIDS = ("kb-search", "ai-assistant", "ai-text-workshop",
                 "vault", "weekly-report")
with_btn = {pid for pid in SETTINGS_PIDS
            if _has_settings_button(_find_card(pid))}
check("B2. 五个带 settings 的插件卡片都有「设置」按钮",
      with_btn == set(SETTINGS_PIDS), f"有按钮：{sorted(with_btn)}")
rt_card = _find_card("recurring-tasks")
check("B3. 未声明 settings 的插件（recurring-tasks）无设置按钮",
      rt_card is not None and not _has_settings_button(rt_card))

# ---------- C. 弹层表单 ----------
dlg = build_plugin_settings_dialog(win, kb_lp, data_dir_base=plugins_data_base)
check("C1. 弹层构建成功（kb-search）", dlg is not None)
if dlg is not None:
        w = dlg._widgets
        check("C2. 控件类型与 schema 对应",
              isinstance(w["max_results"][0], Stepper)
              and isinstance(w["search_notes"][0], ToggleSwitch)
              and isinstance(w["ranking"][0], QComboBox))
        check("C3. 初始值 = schema 默认值",
              w["max_results"][0].value() == 60
              and w["search_notes"][0].isChecked() is True
              and w["ranking"][0].currentText() == "bm25")

        # ---------- D. 保存流（落临时插件私有目录） ----------
        seen = []
        kb_lp.ctx.settings_changed.connect(seen.append)
        w["max_results"][0].setValue(30)
        w["ranking"][0].setCurrentText("tfidf")
        dlg._on_save()
        saved = {}
        path = settings_file(plugins_data_base, "kb-search")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
        check("D1. 改动落盘（max_results=30, ranking=tfidf）",
              saved.get("max_results") == 30 and saved.get("ranking") == "tfidf",
              json.dumps(saved, ensure_ascii=False))
        check("D2. settings_changed 收到改动 key 列表",
              seen == [["max_results", "ranking"]], repr(seen))
        check("D3. 未改动的项不写盘（search_notes 缺省）",
              "search_notes" not in saved)
        dlg.deleteLater()

# ---------- F. 四插件：弹层渲染 + 保存流 + settings_changed 生效链 ----------
# （2026-10-04 插件设置扩展：ai-assistant / ai-text-workshop / vault /
#   weekly-report 接入真实设置项。页面先建（完成订阅）→ 弹层保存 →
#   信号同步派发 → 断言页面状态已按生效值更新；weekly-report 按设计
#   不订阅（短命对话框），改为「保存后新开对话框按生效值勾选」验证。）
CONSUMER_PIDS = ("ai-assistant", "ai-text-workshop", "vault", "weekly-report")
_pages = []          # 持引用防 GC（订阅槽挂在页面上）


def _get_lp(pid):
    return next((p for p in loaded if p.plugin_id == pid), None)


def _read_saved(pid):
    path = settings_file(plugins_data_base, pid)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _check_widget_types(pid, lp, dlg4):
    ws = dlg4._widgets
    bad = []
    for entry in lp.ctx.manifest_settings:
        w0 = ws[entry["key"]][0]
        want = {"bool": ToggleSwitch, "int": Stepper, "float": Stepper,
                "enum": QComboBox}[entry["type"]]
        if not isinstance(w0, want):
            bad.append(f"{entry['key']}≠{entry['type']}")
    check(f"F.{pid} 控件类型与 schema 一一对应", not bad, " ".join(bad))


for pid in CONSUMER_PIDS:
    lp = _get_lp(pid)
    if lp is None:
        check(f"F.{pid} 插件已加载", False)
        continue
    seen4 = []
    lp.ctx.settings_changed.connect(seen4.append)
    page4 = lp.plugin.create_page(lp.ctx)
    _pages.append(page4)

    if pid == "ai-assistant":
        dlg4 = build_plugin_settings_dialog(
            win, lp, data_dir_base=plugins_data_base)
        check(f"F.{pid} 弹层构建成功", dlg4 is not None)
        _check_widget_types(pid, lp, dlg4)
        # float Stepper 内部值 = 真值 ×100（divisor=100 / decimals=2）
        dlg4._widgets["temperature"][0].setValue(80)       # → 0.8
        dlg4._widgets["max_history"][0].setValue(20)
        dlg4._on_save()
        saved = _read_saved(pid)
        check(f"F.{pid} 改动落盘",
              saved.get("temperature") == 0.8 and saved.get("max_history") == 20,
              json.dumps(saved, ensure_ascii=False))
        check(f"F.{pid} settings_changed 携改动 key",
              seen4 == [["temperature", "max_history"]], repr(seen4))
        check(f"F.{pid} 生效链：页面状态即时更新",
              page4._temperature == 0.8 and page4._max_history == 20,
              f"temperature={page4._temperature} max_history={page4._max_history}")
        dlg4.deleteLater()

    elif pid == "ai-text-workshop":
        dlg4 = build_plugin_settings_dialog(
            win, lp, data_dir_base=plugins_data_base)
        check(f"F.{pid} 弹层构建成功", dlg4 is not None)
        _check_widget_types(pid, lp, dlg4)
        dlg4._widgets["temperature"][0].setValue(90)       # → 0.9
        dlg4._widgets["max_tokens"][0].setValue(1024)
        dlg4._on_save()
        saved = _read_saved(pid)
        check(f"F.{pid} 改动落盘",
              saved.get("temperature") == 0.9 and saved.get("max_tokens") == 1024,
              json.dumps(saved, ensure_ascii=False))
        check(f"F.{pid} settings_changed 携改动 key",
              seen4 == [["temperature", "max_tokens"]], repr(seen4))
        check(f"F.{pid} 生效链：页面状态即时更新",
              page4._temperature == 0.9 and page4._max_tokens == 1024,
              f"temperature={page4._temperature} max_tokens={page4._max_tokens}")
        dlg4.deleteLater()

    elif pid == "vault":
        dlg4 = build_plugin_settings_dialog(
            win, lp, data_dir_base=plugins_data_base)
        check(f"F.{pid} 弹层构建成功", dlg4 is not None)
        _check_widget_types(pid, lp, dlg4)
        dlg4._widgets["clipboard_clear_seconds"][0].setCurrentText("60")
        dlg4._on_save()
        saved = _read_saved(pid)
        check(f"F.{pid} 改动落盘",
              saved.get("clipboard_clear_seconds") == "60",
              json.dumps(saved, ensure_ascii=False))
        check(f"F.{pid} settings_changed 携改动 key",
              seen4 == [["clipboard_clear_seconds"]], repr(seen4))
        check(f"F.{pid} 生效链：页面等待秒数即时更新",
              page4._clear_seconds == 60,
              f"clear_seconds={page4._clear_seconds}")
        dlg4.deleteLater()

    elif pid == "weekly-report":
        dlg4 = build_plugin_settings_dialog(
            win, lp, data_dir_base=plugins_data_base)
        check(f"F.{pid} 弹层构建成功", dlg4 is not None)
        _check_widget_types(pid, lp, dlg4)
        dlg4._widgets["default_range"][0].setCurrentText("today")
        dlg4._on_save()
        saved = _read_saved(pid)
        check(f"F.{pid} 改动落盘",
              saved.get("default_range") == "today",
              json.dumps(saved, ensure_ascii=False))
        check(f"F.{pid} settings_changed 携改动 key",
              seen4 == [["default_range"]], repr(seen4))
        # 设计为不订阅（短命对话框）：生效链由下方「新开对话框」验证
        dlg4.deleteLater()

# weekly-report 的对话框消费验证（需要 import 插件模块，放段外统一做）
_lp_weekly = _get_lp("weekly-report")
if _lp_weekly is not None:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "verify_weekly_settings",
        os.path.join(_base, "plugins", "weekly-report", "plugin.py"))
    _mod = _ilu.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    _rep = _mod.ReportDialog(_lp_weekly.ctx)
    _pages.append(_rep)
    check("F.weekly-report 新开对话框按生效值勾选（today）",
          _rep._radios["today"].isChecked()
          and not _rep._radios["week"].isChecked()
          and _rep._default_range == "today")
    _rep.deleteLater()

# ---------- E. light / dark 截图（弹层覆盖五个插件 + 插件中心页） ----------
for theme in ("light", "dark"):
    cm.set("theme", theme)
    win._theme = theme
    win._apply_theme()
    win.show_plugins_page()
    app.processEvents()
    app.processEvents()
    page_path = os.path.join(OUT_DIR, f"plugin-settings-page-{theme}.png")
    ok = win.grab().save(page_path)
    print(f"[{'OK' if ok else 'FAIL'}] saved {page_path}")
    for pid in SETTINGS_PIDS:
        lp_t = _get_lp(pid)
        if lp_t is None:
            continue
        shot_dlg = build_plugin_settings_dialog(
            win, lp_t, data_dir_base=plugins_data_base)
        if shot_dlg is None:
            continue
        shot_dlg.show()
        app.processEvents()
        app.processEvents()
        dlg_path = os.path.join(
            OUT_DIR, f"plugin-settings-dialog-{pid}-{theme}.png")
        ok = shot_dlg.grab().save(dlg_path)
        print(f"[{'OK' if ok else 'FAIL'}] saved {dlg_path}")
        shot_dlg.close()
        shot_dlg.deleteLater()

panel.deleteLater()
shutil.rmtree(tmp_root, ignore_errors=True)
shutil.rmtree(tmp_cfg, ignore_errors=True)

n_ok = sum(1 for _name, ok in results if ok)
print(f"[SUMMARY] {n_ok}/{len(results)} checks passed")
sys.exit(0 if n_ok == len(results) else 1)
