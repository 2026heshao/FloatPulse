# -*- coding: utf-8 -*-
"""周期任务插件离屏端到端验证 + light/dark 真截图。

覆盖（每一步都走真实代码路径，不用替身糊过去）：
  A 真实 plugins/ 目录被真加载器扫到：动作进注册表、热键不与核心冲突、
    manifest 只声明 write（改删能力必须被拒）
  B **启动即补跑**：预置一条「3 天前该触发但没跑过」的规则 → 加载插件
    后任务列表里**恰好多出一条**（只补最近一次，不冒出三条）
  C 幂等：再点「立即检查」任务数不变；last_fired 已落盘
  D 真实写入链路：生成的任务标题/备注/截止日与规则一致，落在真 TaskManager
  E 页面真身：注册进主窗口导航（索引 10+）、show_plugin_page 能切过去、
    规则卡片数与规则数一致、空态/计数文案正确
  F RuleDialog 真身：继承 PluginDialog；类型切换时参数区可见性正确；
    非法输入只提示不关窗；保存后 result_rule() 给出归一化规则
  G 视觉：页面与对话框在 light / dark 各截一张真图（win.grab() / dlg.grab()）

跑法：python tools/run_gui_check.py tools/verify_recurring_tasks.py
产物：build/shots/recurring-{page,dialog}-{light,dark}.png
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta

# ★ 必须在 import PyQt6 之前设定，否则进程硬崩
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication                          # noqa: E402
from PyQt6.QtGui import QFontDatabase                             # noqa: E402

from src.config import ConfigManager                              # noqa: E402
from src.main_window import MainWindow                            # noqa: E402
from src.plugin_api import ActionRegistry, PluginContext, PluginData  # noqa: E402
from src.plugin_loader import PluginLoader                        # noqa: E402
from src.docx_manager import DocxManager                          # noqa: E402
from src.task_manager import TaskManager                          # noqa: E402
from src.note_manager import NoteManager                          # noqa: E402
from src.fragment_manager import FragmentManager                  # noqa: E402
from src.nav_manager import NavManager                            # noqa: E402
from src.temp_asset_manager import TempAssetManager               # noqa: E402
from src.clipboard_monitor import ClipboardMonitor                # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

OUT_DIR = os.path.join(ROOT, "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

PLUGIN_ID = "recurring-tasks"
PAGE_KEY = f"plugin:{PLUGIN_ID}"
ACTION_MANAGE = f"{PLUGIN_ID}.manage"
ACTION_CHECK = f"{PLUGIN_ID}.check-now"

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


def pump(ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
logger = logging.getLogger("fp_verify_recurring_tasks")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.handlers.clear()
logger.addHandler(_Capture(_records))


# ====================================================================
# 搭环境
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_rt_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)

cm = ConfigManager(os.path.join(data_dir, "config.json"))
cm.set("plugins_enabled", True)

docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                   os.path.join(data_dir, "docx_meta.json"))
docx.load()
tasks = TaskManager(os.path.join(data_dir, "schedule.json"))
notes = NoteManager(os.path.join(data_dir, "notes.json"))
frags = FragmentManager(os.path.join(data_dir, "fragments.json"))
nav = NavManager(os.path.join(data_dir, "nav.json"))
assets = TempAssetManager(tmp)
clip = ClipboardMonitor(frags, cm)

win = MainWindow(tasks, notes, frags, docx, cm, clip,
                 temp_asset_manager=assets, nav_manager=nav)
win.resize(1280, 740)
win.show()
pump(300)

# ---- 预置规则（覆盖四类「该不该生成」）----
#   r1 每天 00:01，上次生成在 3 天前 → 补跑今天这一次
#   r2 每月 1/15 号 23:58，从没生成过 → 只补最近一次（9/15），不回溯到 9/1
#   r3 已停用 → 一条都不生成
#   r4 每周（非今天的那天），已生成过最近一次 → 不提前触发
plugin_data_dir = os.path.join(data_dir, "plugins", PLUGIN_ID)
os.makedirs(plugin_data_dir, exist_ok=True)
today = date.today()
stale = (today - timedelta(days=3)).isoformat()

other_wd = (today.weekday() + 3) % 7
back = (today.weekday() - other_wd) % 7 or 7      # 最近一次该星期几（不含今天）
other_day = (today - timedelta(days=back)).isoformat()

RULES = [
    {"rule_id": "r1", "title": "写周报", "note": "带上本周数据",
     "enabled": True, "kind": "daily", "time": "00:01", "due_days": 1,
     "last_fired": stale, "created_at": "2026-09-01 09:00"},
    {"rule_id": "r2", "title": "月报归档", "note": "",
     "enabled": True, "kind": "monthly", "monthdays": [1, 15], "time": "23:58",
     "due_days": -1, "last_fired": "", "created_at": "2026-09-01 09:00"},
    {"rule_id": "r3", "title": "已停用的规则", "note": "",
     "enabled": False, "kind": "daily", "time": "00:01", "due_days": -1,
     "last_fired": "", "created_at": "2026-09-01 09:00"},
    {"rule_id": "r4", "title": "不提前触发的规则", "note": "",
     "enabled": True, "kind": "weekly", "weekdays": [other_wd], "time": "00:01",
     "due_days": -1, "last_fired": other_day,
     "created_at": "2026-09-01 09:00"},
    # r5 模拟「今天才新建、且创建时刻晚于规则时刻」→ 不许为创建之前补跑
    {"rule_id": "r5", "title": "新建当天不补跑", "note": "",
     "enabled": True, "kind": "daily", "time": "00:01", "due_days": -1,
     "last_fired": "", "created_at": f"{today.isoformat()} 23:59"},
]
with open(os.path.join(plugin_data_dir, "rules.json"), "w",
          encoding="utf-8", newline="\n") as f:
    json.dump({"version": 1, "updated_at": "2026-09-01 09:00:00",
               "rules": RULES}, f, ensure_ascii=False, indent=2)

# ---- 真实接线（与 knowledge_ball 的装配方式一致）----
def _add_task(title, note, deadline):
    tid = tasks.add_task(title, note, deadline)
    win.refresh_tasks()
    return tid


def _update_task(task_id, title, note, deadline):
    tasks.update_task(task_id, title, note, deadline)
    return True


registry = ActionRegistry(
    logger=logger,
    reserved_hotkeys=(cm.get("quick_capture_hotkey", "Ctrl+Alt+K"),
                      cm.get("screenshot_hotkey", "Ctrl+Alt+S")))
plugin_data = PluginData(providers={
    "tasks": lambda: [t.to_dict() for t in tasks.get_all_tasks()],
    "fragments": lambda: [f.to_dict() for f in frags.get_all_fragments()],
    "notes": lambda: [n.to_dict() for n in notes.get_all_notes()],
})
ctx = PluginContext(
    logger=logger,
    config=cm.as_dict(),
    show_toast=win.show_toast,
    open_main_window=lambda: None,
    open_card_mode=lambda mode: True,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    write_providers={"task": _add_task},
    manage_providers={"update_task": _update_task},
)
loader = PluginLoader(registry, ctx, plugins_dir=os.path.join(ROOT, "plugins"))
win.set_plugin_loader(loader)

# ====================================================================
# B. 真实加载 → on_enable → 启动即补跑
# ====================================================================
before = len(tasks.get_all_tasks())
loaded = loader.load_all()
pump(400)                       # 让 on_enable / 单次定时器跑完
after = len(tasks.get_all_tasks())

ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 recurring-tasks", PLUGIN_ID in ids, f"{ids}")

act = registry.get(ACTION_MANAGE)
check("A2 面板动作已进注册表且挂菜单", act is not None and act.menu is True)
check("A3 热键 Ctrl+Alt+R 未与核心冲突",
      act is not None and act.hotkey == "Ctrl+Alt+R"
      and ACTION_MANAGE in [a.id for a in registry.hotkey_actions()],
      f"{getattr(act, 'hotkey', None)}")
check("A4 立即检查动作也在注册表", registry.get(ACTION_CHECK) is not None)

sub_ctx = registry.context_of(ACTION_MANAGE)
check("A5 注册表记住了插件自己的上下文",
      sub_ctx is not None and sub_ctx.plugin_id == PLUGIN_ID)

# 只声明 write → 改删必须被拒（能力门禁，不是「没注入通道」）
check("A6 只声明 write：add_task 可用",
      sub_ctx.write.add_task("（验证用）权限探针", "", "") > 0)
check("A7 只声明 write：manage.update_task 被拒",
      sub_ctx.manage.update_task(1, title="不该改到") is False,
      "manage 未被门禁拦住")

check(f"B1 启动即补跑：任务数 {before} → {after}（两条该补的，各补一次）",
      after == before + 2, f"before={before} after={after}")

gen = [t for t in tasks.get_all_tasks() if t.title == "写周报"]
check("B2 补跑生成的任务标题正确", len(gen) == 1, f"{[t.title for t in tasks.get_all_tasks()]}")
if gen:
    t0 = gen[0]
    check("B3 备注写进任务", t0.note == "带上本周数据", repr(t0.note))
    check("B4 截止日 = 触发日 + 1 天",
          str(t0.deadline)[:10] == (today + timedelta(days=1)).isoformat(),
          f"{t0.deadline}")
check("B5 停用的规则没有生成任务",
      not [t for t in tasks.get_all_tasks() if t.title == "已停用的规则"])
check("B6 已生成过最近一次的周规则 → 不提前触发",
      not [t for t in tasks.get_all_tasks() if t.title == "不提前触发的规则"])
check("B7 每月规则只补一条（不把 9/1 也补出来）",
      len([t for t in tasks.get_all_tasks() if t.title == "月报归档"]) == 1)
check("B8 新建当天、创建时刻晚于规则时刻 → 不为创建之前补跑",
      not [t for t in tasks.get_all_tasks() if t.title == "新建当天不补跑"])

# ====================================================================
# C. 幂等 + 落盘
# ====================================================================
plug = sys.modules.get("floatpulse_plugin_" + PLUGIN_ID.replace("-", "_"))
if plug is None:
    cands = [k for k in sys.modules if PLUGIN_ID in k]
    plug = sys.modules.get(cands[0]) if cands else None
check("C0 插件模块可从 sys.modules 取到", plug is not None,
      f"{[k for k in sys.modules if 'recurring' in k]}")

sched = plug._STATE["scheduler"]
n1 = len(tasks.get_all_tasks())
check("C1 再检查一次：不再重复生成", sched.check_now() == []
      and len(tasks.get_all_tasks()) == n1, f"n={n1}")

with open(os.path.join(plugin_data_dir, "rules.json"), encoding="utf-8") as f:
    saved = json.load(f)
by_id = {r["rule_id"]: r for r in saved["rules"]}
check("C2 last_fired 已推进并落盘（幂等的锚点）",
      by_id["r1"]["last_fired"] == today.isoformat(),
      by_id["r1"].get("last_fired"))
check("C3 月报规则补的是最近一次月度触发日（15 号），不是月初那次",
      by_id["r2"]["last_fired"] == "2026-09-15", by_id["r2"].get("last_fired"))
check("C4 不提前触发的规则 last_fired 保持原值",
      by_id["r4"]["last_fired"] == other_day,
      f"{by_id['r4'].get('last_fired')} != {other_day}")
check("C5 调度器正在运行", sched.is_running() is True)
check("C6 数据目录可用（规则能持久化）", sched.store_available() is True)

# ====================================================================
# E. 页面真身
# ====================================================================
page = plug._STATE.get("page")
if page is None:                      # load_all 不建页面，按宿主接线方式补建
    page = plug.RecurringTasksPlugin().create_page(sub_ctx)
    win.register_plugin_page(PAGE_KEY, "🔁 周期任务", page)
    plug._STATE["page"] = page
pump(200)

check("E1 页面注册进主窗口（物理索引 10+）",
      win.show_plugin_page(PAGE_KEY) is True)
pump(300)

cards = page.findChildren(object, "pluginCard")
check("E2 规则卡片数 = 规则数（5 条）", len(cards) == 5, f"{len(cards)}")
check("E3 计数文案正确", "共 5 条规则" in page._count.text(), page._count.text())
check("E4 状态行显示调度与目录状态",
      "运行中" in page._state_label.text()
      and "可写" in page._state_label.text(), page._state_label.text())
check("E5 有规则时空态隐藏", page._empty.isVisible() is False)

# 点「立即检查」（全部已生成 → 提示没有到点）
page._on_check_now()
pump(200)
check("E6 立即检查：无到点规则时不新增任务",
      len(tasks.get_all_tasks()) == n1)

# 停用 → 落盘；再启用回来（走真实的卡片按钮回调）
def _enabled_of(rule_id):
    for r in sched.rules():
        if r["rule_id"] == rule_id:
            return r.get("enabled", True)
    return None


def _rule_of(rule_id):
    for r in sched.rules():
        if r["rule_id"] == rule_id:
            return dict(r)
    return None


page._on_toggle(_rule_of("r2"))
pump(150)
check("E7 停用规则后 enabled 落盘为 False", _enabled_of("r2") is False,
      f"{_enabled_of('r2')}")
page._on_toggle(_rule_of("r2"))
pump(150)
check("E8 再切回启用", _enabled_of("r2") is True, f"{_enabled_of('r2')}")

# 删除规则（QMessageBox 替身自动确认）→ 卡片减少
_real_mb = plug.QMessageBox


class _FakeMB:
    StandardButton = _real_mb.StandardButton

    @staticmethod
    def question(*_a, **_k):
        return _real_mb.StandardButton.Yes


plug.QMessageBox = _FakeMB
page._on_delete(_rule_of("r3"))
pump(150)
check("E9 删除规则后从列表消失", _rule_of("r3") is None
      and len(sched.rules()) == 4, f"{[r['rule_id'] for r in sched.rules()]}")
plug.QMessageBox = _real_mb

# ====================================================================
# F. RuleDialog 真身
# ====================================================================
from src.plugin_ui import PluginDialog                            # noqa: E402

dlg = plug.RuleDialog(sub_ctx, None, win)
dlg.show()                 # isVisible 只有在真显示后才可信（子控件可见性）
pump(250)
check("F1 对话框继承官方 PluginDialog", isinstance(dlg, PluginDialog))
check("F2 默认类型=每天 → 三个参数区全隐藏",
      not dlg._week_row.isVisible() and not dlg._month_row.isVisible()
      and not dlg._interval_row.isVisible())
check("F3 空标题 → 预览区报错、不崩",
      dlg._error.text().startswith("⚠"), dlg._error.text())

dlg._title.setText("交房租")
dlg._kind.setCurrentIndex(dlg._kind.findData("monthly"))
pump(60)
check("F4 切到「每月」→ 只有号数区可见",
      dlg._month_row.isVisible() and not dlg._week_row.isVisible()
      and not dlg._interval_row.isVisible())
check("F5 号数为空 → 报错提示",
      "至少要选一个号数" in dlg._error.text(), dlg._error.text())

dlg._month_edit.setText("1，15, 31")
pump(60)
preview = dlg._preview.text()
check("F6 全角逗号 / 空格都能解析，预览含月末顺延提示",
      dlg._error.text() == "" and "每月" in preview
      and "月末自动顺延" in preview, preview)

dlg._month_edit.setText("abc")
pump(60)
check("F7 非法号数 → 报错且不关窗", dlg._error.text().startswith("⚠"))
dlg._month_edit.setText("1,31")
pump(60)

dlg._kind.setCurrentIndex(dlg._kind.findData("weekly"))
dlg._week_checks[4].setChecked(True)          # 周五
pump(60)
check("F8 切到「每周」→ 勾了周五后预览含周五",
      "周五" in dlg._preview.text(), dlg._preview.text())

dlg._kind.setCurrentIndex(dlg._kind.findData("interval"))
dlg._interval.setText("14")
pump(60)
check("F9 切到「每 N 天」→ 预览含间隔",
      "每 14 天" in dlg._preview.text(), dlg._preview.text())

dlg._title.setText("复盘")
dlg._kind.setCurrentIndex(dlg._kind.findData("weekly"))
dlg._due.setCurrentIndex(dlg._due.findData(3))
pump(60)
dlg._on_save()
pump(60)
rule = dlg.result_rule()
check("F10 保存成功 → result_rule 给出归一化规则",
      rule is not None and rule["title"] == "复盘"
      and rule["kind"] == "weekly" and rule["weekdays"] == [4]
      and rule["due_days"] == 3 and rule["time"] == "09:00",
      f"{rule}")
check("F11 新建对话框不会带上 last_fired，但会记下创建时刻（补跑水位）",
      rule is not None and rule["last_fired"] == ""
      and len(rule["created_at"]) >= 16, f"{rule and rule['created_at']!r}")

# 编辑已有规则：预填 + 保留 rule_id / last_fired
edit_dlg = plug.RuleDialog(sub_ctx, dict(sched.rules()[0]), win)
edit_dlg.show()
pump(250)
check("F12 编辑时预填原值", edit_dlg._title.text() == "写周报"
      and edit_dlg._month_row.isVisible() is False)
edit_dlg._on_save()
pump(60)
erule = edit_dlg.result_rule()
check("F13 编辑保留 rule_id 与 last_fired",
      erule is not None and erule["rule_id"] == "r1"
      and erule["last_fired"] == today.isoformat(),
      f"{erule.get('rule_id') if erule else None}"
      f"/{erule.get('last_fired') if erule else None}")
edit_dlg.close()
dlg.close()
pump(100)

# ====================================================================
# G. 双主题真截图
# ====================================================================
win.show_plugin_page(PAGE_KEY)
pump(400)
for theme in ("light", "dark"):
    cm.set("theme", theme)
    win._theme = theme
    win._apply_theme()
    pump(300)
    p = os.path.join(OUT_DIR, f"recurring-page-{theme}.png")
    win.grab().save(p)
    print(f"    saved {p}", flush=True)

    d = plug.RuleDialog(sub_ctx, None, win)
    d._title.setText("交房租")
    d._kind.setCurrentIndex(d._kind.findData("monthly"))
    d._month_edit.setText("1,15,31")
    pump(200)
    d.show()
    pump(250)
    p = os.path.join(OUT_DIR, f"recurring-dialog-{theme}.png")
    d.grab().save(p)
    print(f"    saved {p}", flush=True)
    d.close()

check("G1 四张截图全部落盘",
      all(os.path.isfile(os.path.join(
          OUT_DIR, f"recurring-{k}-{t}.png"))
          for k in ("page", "dialog") for t in ("light", "dark")))

# 主题跟随的可观测证据：卡片样式规则确实在宿主 QSS 里
qss = win._container.styleSheet()
check("G2 宿主 QSS 覆盖插件卡样式（pluginCard / pluginEmptyHint）",
      "pluginCard" in qss and "pluginEmptyHint" in qss, f"qss_len={len(qss)}")

# ====================================================================
win.close()
pump(150)
shutil.rmtree(tmp, ignore_errors=True)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
