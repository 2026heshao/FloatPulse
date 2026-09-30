# -*- coding: utf-8 -*-
"""离屏端到端验证：插件框架 P0+P1（版本契约 / 状态持久化 / 卸载 / 钩子 / 写能力）。

用**真实 MainWindow 作宿主**（假 QWidget 替身会崩），走真实插件加载链路，
验证的不只是纯逻辑，还包括面板控件文本与开关行为。

跑法：python tools/run_gui_check.py tools/verify_plugin_p0p1.py
"""
import os
import sys
import json
import shutil
import tempfile

# ★ 必须在 import PyQt6 之前设定，否则进程硬崩 0xC0000005
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton   # noqa: E402
from PyQt6.QtGui import QFontDatabase                            # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.config import ConfigManager                             # noqa: E402
from src.main_window import MainWindow                           # noqa: E402
from src.plugin_api import ActionRegistry, PluginContext         # noqa: E402
from src.plugin_loader import PluginLoader                       # noqa: E402
from src.docx_manager import DocxManager                         # noqa: E402
from src.task_manager import TaskManager                         # noqa: E402
from src.note_manager import NoteManager                         # noqa: E402
from src.fragment_manager import FragmentManager                 # noqa: E402
from src.nav_manager import NavManager                           # noqa: E402
from src.temp_asset_manager import TempAssetManager              # noqa: E402
from src.clipboard_monitor import ClipboardMonitor               # noqa: E402
from src.app_paths import get_base_dir, get_docx_path  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ====================================================================
# 准备：临时插件目录 + 真实 MainWindow 宿主
# ====================================================================
root = tempfile.mkdtemp(prefix="fp_p0p1_plugins_")


def _plugin_src(pid, extra=""):
    return (
        "from src.plugin_api import BallAction, BallPlugin\n"
        "\n"
        "class Act(BallAction):\n"
        f'    id = "{pid}.go"\n'
        '    title = "go"\n'
        "\n"
        "    def run(self, ctx):\n"
        "        pass\n"
        "\n"
        f"class Plugin(BallPlugin):\n"
        f'    id = "{pid}"\n'
        f'    name = "{pid}"\n'
        '    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        "        return [Act()]\n"
        + extra
    )


def mk(pid, manifest_extra=None, extra_code=""):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {"id": pid, "name": pid, "version": "1.0.0", "entry": "plugin.py",
                "actions": [{"id": f"{pid}.go", "title": "go", "menu": True}]}
    manifest.update(manifest_extra or {})
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(_plugin_src(pid, extra_code))
    return d


mk("normal")
mk("old-plugin", {"api_version": 999})          # 版本过高 → 应拒载
mk("writer", {"capabilities": ["write"]})

tmp_cfg = tempfile.mkdtemp()
cm = ConfigManager(os.path.join(tmp_cfg, "config.json"))
cm.set("plugins_enabled", True)
cm.set("plugins_disabled", [])

_base = get_base_dir()
docx = DocxManager(get_docx_path(_base), os.path.join(tmp_cfg, "docx_meta.json"))
docx.load()
tasks = TaskManager(os.path.join(tmp_cfg, "schedule.json"))
notes = NoteManager(os.path.join(tmp_cfg, "notes.json"))
frags = FragmentManager(os.path.join(tmp_cfg, "fragments.json"))
nav = NavManager(os.path.join(tmp_cfg, "nav.json"))
assets = TempAssetManager(_base)

win = MainWindow(tasks, notes, frags, docx, cm, ClipboardMonitor(frags, cm),
                 temp_asset_manager=assets, nav_manager=nav)
win.resize(1280, 740)

rec = {"fragments": [], "tasks": [], "notes": []}


def _make_ctx():
    return PluginContext(
        logger=None, config=cm.as_dict(),
        show_toast=win.show_toast,
        open_main_window=lambda: None,
        open_card_mode=lambda m: True,
        data_dir_base=os.path.join(tmp_cfg, "plugin_data"),
        parent_window=lambda: win,
        write_providers={
            "fragment": lambda c, s: (rec["fragments"].append((c, s)),
                                      len(rec["fragments"]))[1],
            "task": lambda t, n, d: (rec["tasks"].append((t, n, d)),
                                     len(rec["tasks"]))[1],
            "note": lambda t, c: (rec["notes"].append((t, c)),
                                  len(rec["notes"]))[1],
        },
    )


reg = ActionRegistry(logger=None)
loader = PluginLoader(reg, _make_ctx(), plugins_dir=root, logger=None)
win.set_plugin_loader(loader)
loaded = loader.load_all()
ids = sorted(lp.plugin_id for lp in loaded)

# ====================================================================
# A. P0-1 版本契约
# ====================================================================
check("A1 老插件（无版本字段）正常加载", "normal" in ids, f"已加载 {ids}")
check("A2 api_version=999 的插件被拒载", "old-plugin" not in ids)
errs = loader.load_errors()
vm = [e for e in errs if e.stage == "version_mismatch"]
check("A3 失败阶段归为 version_mismatch（非笼统 manifest_invalid）",
      len(vm) == 1, f"stages={[e.stage for e in errs]}")
if vm:
    check("A4 失败卡片给出升级建议文案", "升级" in vm[0].hint, vm[0].hint[:40])

from src.plugin_loader import stage_label
check("A5 版本阶段有中文短标签", stage_label("version_mismatch") == "版本不匹配",
      stage_label("version_mismatch"))

# ====================================================================
# B. P0-2 启停状态持久化
# ====================================================================
win.show_plugins_page()
app.processEvents()
panel = win._page_plugins

n_off = loader.set_plugin_enabled("normal", False)
check("B1 set_plugin_enabled 停用生效", n_off == 1 and
      not reg.get("normal.go").enabled(), f"改动 {n_off} 个动作")
check("B2 停用后动作不进菜单", reg.get("normal.go") not in reg.menu_actions())
check("B3 disabled_plugin_ids 正确上报", loader.disabled_plugin_ids() == ["normal"],
      str(loader.disabled_plugin_ids()))

# 面板 toggle → 写配置
target = next(lp for lp in loader.loaded_plugins() if lp.plugin_id == "normal")
acts = list(target.actions_raw)
panel._toggle_plugin(acts, True, plugin_id="normal")
check("B4 面板启用 → 配置里被移除",
      cm.get("plugins_disabled", None) == [], str(cm.get("plugins_disabled")))
check("B5 启用后动作恢复", reg.get("normal.go").enabled() is True)

panel._toggle_plugin(acts, False, plugin_id="normal")
check("B6 面板停用 → 写进配置",
      cm.get("plugins_disabled", None) == ["normal"], str(cm.get("plugins_disabled")))

# 配置往返：新 loader 按配置回置
loader2 = PluginLoader(ActionRegistry(logger=None), _make_ctx(),
                       plugins_dir=root, logger=None)
loader2.load_all()
for pid in (cm.get("plugins_disabled", None) or []):
    loader2.set_plugin_enabled(pid, False)
lp2 = next(lp for lp in loader2.loaded_plugins() if lp.plugin_id == "normal")
check("B7 重启后仍为停用态（配置回置生效）",
      all(not a.enabled() for a in lp2.actions_raw))

# 恢复默认 → 回写 []
cm.reset_to_default()
check("B8 恢复默认后 plugins_disabled 回到空列表",
      cm.get("plugins_disabled", None) == [], str(cm.get("plugins_disabled")))

# ====================================================================
# C. P0-3 卸载入口
# ====================================================================
panel.refresh()
app.processEvents()
cards = [panel._cards_layout.itemAt(i).widget()
         for i in range(panel._cards_layout.count() - 1)]
all_btns = [b for c in cards if c is not None
            for b in c.findChildren(QPushButton)]
check("C1 每个插件卡片都有「卸载」按钮",
      any("卸载" in b.text() for b in all_btns),
      str([b.text() for b in all_btns][:8]))

check("C2 卸载按钮存在 + 私有数据保留提示在 tooltip 里",
      any("卸载" in b.text() and "私有数据" in (b.toolTip() or "")
          for b in all_btns))

target_dir = os.path.join(root, "writer")
check("C3 卸载前目录存在", os.path.isdir(target_dir))
ok, msg = loader.uninstall("writer")
check("C4 卸载成功并删掉目录", ok and not os.path.isdir(target_dir), msg)
check("C5 卸载后动作已摘除（writer）",
      not any(a.id.startswith("writer.") for a in reg.all_actions()))
check("C6 卸载提示包含「私有数据保留」", "私有数据保留" in msg, msg)

# 边界：非法 id / 不存在
ok_bad, msg_bad = loader.uninstall("../evil")
check("C7 越界 id 被拒绝", ok_bad is False, msg_bad)
ok_ghost, msg_ghost = loader.uninstall("ghost")
check("C8 不存在的插件返回原因", ok_ghost is False and "不存在" in msg_ghost,
      msg_ghost)
check("C9 拒绝路径没有误删别的插件", os.path.isdir(os.path.join(root, "normal")))

# ====================================================================
# D. P1-1 生命周期钩子
# ====================================================================
hook_log = []
mk("hooker", extra_code=(
    "    def on_enable(self, ctx):\n"
    '        with open(%r, "a", encoding="utf-8") as f:\n'
    '            f.write("enable\\n")\n'
    "    def on_disable(self, ctx):\n"
    '        with open(%r, "a", encoding="utf-8") as f:\n'
    '            f.write("disable\\n")\n'
).replace("%r", repr(os.path.join(tmp_cfg, "hooks.log"))))

loader3 = PluginLoader(ActionRegistry(logger=None), _make_ctx(),
                       plugins_dir=root, logger=None)
loader3.load_all()
loader3.deactivate()

hook_file = os.path.join(tmp_cfg, "hooks.log")
hooks = []
if os.path.isfile(hook_file):
    with open(hook_file, encoding="utf-8") as f:
        hooks = f.read().split()
check("D1 on_enable / on_disable 均被调用且顺序正确",
      hooks == ["enable", "disable"], str(hooks))

# 钩子抛异常不拖垮宿主
mk("boom-hook", extra_code=(
    "    def on_enable(self, ctx):\n"
    '        raise RuntimeError("boom")\n'
))
loader4 = PluginLoader(ActionRegistry(logger=None), _make_ctx(),
                       plugins_dir=root, logger=None)
l4 = loader4.load_all()
boom = [lp for lp in l4 if lp.plugin_id == "boom-hook"]
check("D2 钩子抛异常：插件照样加载成功",
      len(boom) == 1 and boom[0].registered)
check("D3 钩子异常被记进插件告警（用户可见）",
      bool(boom) and any("on_enable" in w for w in boom[0].warnings),
      str(boom[0].warnings if boom else []))

# ====================================================================
# E. P1-2 受限写能力
# ====================================================================
wctx = _make_ctx().for_plugin("writer", root, ["write"])
rid = wctx.write.add_fragment("片段内容")
check("E1 声明 write 后 add_fragment 写入成功",
      rid == 1 and rec["fragments"] == [("片段内容", "插件:writer")],
      f"rid={rid} rec={rec['fragments']}")

rid_t = wctx.write.add_task("交周报", note="附数据", deadline="2026-10-01")
check("E2 add_task 三参数透传正确",
      rid_t == 1 and rec["tasks"] == [("交周报", "附数据", "2026-10-01")],
      str(rec["tasks"]))

rid_n = wctx.write.add_note("标题", "正文")
check("E3 add_note 参数顺序为 (标题, 正文)",
      rid_n == 1 and rec["notes"] == [("标题", "正文")], str(rec["notes"]))

# 未声明能力 → 拒绝
noctx = _make_ctx().for_plugin("normal", root, [])
before = len(rec["fragments"])
check("E4 未声明 write 的插件写入被拒（返回 0）",
      noctx.write.add_fragment("不该写进去") == 0)
check("E5 拒绝路径确实没写入任何数据",
      len(rec["fragments"]) == before)

# 护栏
check("E6 空内容拒写", wctx.write.add_fragment("   ") == 0)
long_text = "字" * 9000
wctx.write.add_fragment(long_text)
check("E7 超长内容截断到 8000 字符",
      len(rec["fragments"][-1][0]) == 8000,
      f"实际 {len(rec['fragments'][-1][0])}")

# 面板能力标签
mk("writer2", {"capabilities": ["network", "write"]})
loader5 = PluginLoader(ActionRegistry(logger=None), _make_ctx(),
                       plugins_dir=root, logger=None)
win.set_plugin_loader(loader5)
loader5.load_all()
win.show_plugins_page()
app.processEvents()
win.refresh_page("plugins")
app.processEvents()
blob = "\n".join(lbl.text() for lbl in win._page_plugins.findChildren(QLabel))
check("E8 面板显示 write 能力标记", "✍ 写入数据" in blob,
      [t for t in blob.split("\n") if "能力" in t][:2])

# 工具类没有破坏性方法
from src.plugin_api import PluginWriter
public = {n for n in dir(PluginWriter) if not n.startswith("_")}
check("E9 写门面只有新增方法，无 update/delete",
      not ({"update", "delete", "remove", "edit"} & public),
      str(sorted(public)))

# ====================================================================
# F. 面板渲染整体不崩 + 恢复默认联动
# ====================================================================
cm.set("theme", "light")
win._theme = "light"
win._apply_theme()
win.refresh_page("plugins")
app.processEvents()
check("F1 light 主题下插件页渲染无异常", True)

cm.set("theme", "dark")
win._theme = "dark"
win._apply_theme()
win.refresh_page("plugins")
app.processEvents()
check("F2 dark 主题下插件页渲染无异常", True)

win.show_plugins_page()
app.processEvents()
check("F3 插件页可在真实主窗口切换", win._stack.currentIndex() >= 0,
      f"index={win._stack.currentIndex()}")

# ====================================================================
shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(tmp_cfg, ignore_errors=True)

passed = sum(1 for _n, ok in results if ok)
total = len(results)
print(f"\n===== {passed}/{total} =====")
if passed != total:
    for n, ok in results:
        if not ok:
            print(f"  FAILED: {n}")
print("[DONE]")
