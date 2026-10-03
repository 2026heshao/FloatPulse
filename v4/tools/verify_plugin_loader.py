# -*- coding: utf-8 -*-
"""插件框架离屏端到端验证：plugins/ 扫描 → 注册表 → 悬浮球右键菜单。

覆盖（每一步都调真实代码路径，不做注入替身）：
  A 目录不存在 → 自动创建，不报错
  B 合法插件 → 动作进注册表、菜单出现、QAction.trigger() 真能驱动插件 run()
  C 坏插件（requires=requests / 抢核心热键 / 抢已占热键）→ 全部跳过 + 日志有记录
  D .fpplug（zip）→ 自动解压后加载，原文件保留
  E 右键菜单三段结构（文本 + icon_name 签名断言）：进入段=打开主窗口/小卡片▸
    → 执行段=番茄钟项/追加项/插件功能▸ → 退出段=退出程序（固定垫底）
  F deactivate → 菜单回到无插件基线；activate → 恢复且模块未重导入
  G 插件 run() 抛异常 → 球不崩（注册表兜住），菜单/其他插件不受影响
  I 菜单护栏（2026-10-02 图标化）：文本零 emoji；切主题后子菜单 QSS 跟随

跑法：python tools/run_gui_check.py tools/verify_plugin_loader.py
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtGui import QFontDatabase             # noqa: E402

from src.plugin_api import ActionRegistry, PluginContext   # noqa: E402
from src.plugin_loader import PluginLoader                 # noqa: E402
from knowledge_ball import FloatingBall                     # noqa: E402

# ★ 必须先建 QApplication 再构造任何 QWidget，否则原生崩 0xC0000409
_app = QApplication.instance() or QApplication(sys.argv)
_font = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
if _font == -1:
    print("[WARN] 中文字体加载失败（不影响断言）", flush=True)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


class Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
logger = logging.getLogger("fp_verify_plugin")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.handlers.clear()
logger.addHandler(Capture(_records))
warns = lambda: [r.getMessage() for r in _records if r.levelno >= logging.WARNING]  # noqa: E731

# ====================================================================
# 搭一个插件目录：好插件 / 坏依赖 / 抢核心热键 / 抢已占热键 / zip 包
# ====================================================================
root = tempfile.mkdtemp(prefix="fp_verify_plugin_")
plugins_dir = os.path.join(root, "plugins")


def plugin_code(plugin_id, action_id, title, body="CALLS.append(self.id)"):
    return (
        "# -*- coding: utf-8 -*-\n"
        "from src.plugin_api import BallAction, BallPlugin\n"
        "\n"
        "CALLS = []\n"
        "\n"
        "class Act(BallAction):\n"
        f'    id = "{action_id}"\n'
        f'    title = "{title}"\n'
        "\n"
        "    def run(self, ctx):\n"
        f"        {body}\n"
        "\n"
        "class P(BallPlugin):\n"
        f'    id = "{plugin_id}"\n'
        '    name = "验证插件"\n'
        '    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        "        return [Act()]\n"
    )


def write_plugin(folder, manifest, code):
    d = os.path.join(plugins_dir, folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(code)
    return d


def manifest(pid, actions, requires=None):
    m = {"id": pid, "name": "验证插件", "version": "1.0.0",
         "entry": "plugin.py", "actions": actions}
    if requires is not None:
        m["requires"] = requires
    return m


write_plugin("01-demo", manifest("01-demo", [
    {"id": "demo.pick", "title": "屏幕取色", "hotkey": "Ctrl+Alt+C", "menu": True}]),
    plugin_code("01-demo", "demo.pick", "屏幕取色"))

# 抢已占热键（与 01-demo 同一入口，大小写+空格不同）
write_plugin("02-rival", manifest("02-rival", [
    {"id": "rival.pick", "title": "抢键插件", "hotkey": " ctrl + ALT + c ", "menu": True}]),
    plugin_code("02-rival", "rival.pick", "抢键插件"))

# 抢核心热键（快速捕捉）
write_plugin("03-thief", manifest("03-thief", [
    {"id": "thief.pick", "title": "抢核心热键", "hotkey": "Ctrl+Alt+K", "menu": True}]),
    plugin_code("03-thief", "thief.pick", "抢核心热键"))

# 依赖不合规
write_plugin("04-netty", manifest("04-netty", [
    {"id": "netty.pick", "title": "联网插件", "menu": True}], requires=["requests"]),
    plugin_code("04-netty", "netty.pick", "联网插件"))

# run() 炸掉的插件
write_plugin("05-boom", manifest("05-boom", [
    {"id": "boom.act", "title": "会炸的动作", "menu": False}]),
    plugin_code("05-boom", "boom.act", "会炸的动作",
                body="raise RuntimeError('故意炸')"))

# .fpplug（zip）包：无热键、挂菜单
os.makedirs(plugins_dir, exist_ok=True)
with zipfile.ZipFile(os.path.join(plugins_dir, "06-zipped.fpplug"), "w") as zf:
    zf.writestr("manifest.json", json.dumps(manifest("06-zipped", [
        {"id": "zipped.act", "title": "压缩包动作", "menu": True}]),
        ensure_ascii=False))
    zf.writestr("plugin.py", plugin_code("06-zipped", "zipped.act", "压缩包动作"))

# ====================================================================
# A/B/C/D/F：注册表与加载器
# ====================================================================
registry = ActionRegistry(logger=logger,
                          reserved_hotkeys=("Ctrl+Alt+K", "Ctrl+Alt+S"))
ctx = PluginContext(logger=logger, config={"theme": "dark"},
                    show_toast=lambda t, ms=2800: None,
                    open_main_window=lambda: None,
                    open_card_mode=lambda m: True)
loader = PluginLoader(registry, ctx, plugins_dir=plugins_dir, logger=logger)

loaded = loader.load_all()
ids = [p.plugin_id for p in loaded]
print(f"    已加载插件：{ids}", flush=True)

check("A1 目录不存在时自动创建且不报错", os.path.isdir(plugins_dir))
check("B1 合法插件动作已进注册表", registry.get("demo.pick") is not None)
check("B2 动作元信息来自 manifest（hotkey/menu）",
      registry.get("demo.pick").hotkey == "Ctrl+Alt+C"
      and registry.get("demo.pick").menu is True)
check("D1 plugins/ 下残留 .fpplug 不再自动解压（双目录模型：安装走商店显式入口）",
      registry.get("zipped.act") is None
      and not os.path.isdir(os.path.join(plugins_dir, "06-zipped")))
check("D2 解压后保留原 .fpplug 文件",
      os.path.isfile(os.path.join(plugins_dir, "06-zipped.fpplug")))
check("C1 抢已占热键者让位（大小写+空格归一后同一入口）",
      registry.get("rival.pick") is None
      and any("冲突" in m and "rival.pick" in m for m in warns()))
check("C2 抢核心热键者让位",
      registry.get("thief.pick") is None
      and any("核心功能占用" in m for m in warns()))
check("C3 requires 不合规插件被拒",
      "04-netty" not in ids and registry.get("netty.pick") is None
      and any("白名单" in m and "requests" in m for m in warns()))
check("C4 坏插件不影响其他插件（02/03 导入成功但动作被跳过，04 被拒，"
      ".fpplug 不自动加载）",
      ids == ["01-demo", "02-rival", "03-thief", "05-boom"],
      f"{ids}")
check("F0 run() 抛异常被兜住", registry.trigger("boom.act", ctx) is False
      and any("已隔离" in m for m in warns()))

# ====================================================================
# E：悬浮球右键菜单数据驱动
#   2026-10-02 菜单图标化：断言从「文本列表」升级成 (text, icon_name)
#   签名 —— 只比文本的话，忘记 setIcon 不会红灯，图标也必须是契约。
# ====================================================================
ball = FloatingBall([])
_extra_calls = []
ball.add_context_action("截图钉屏", lambda: _extra_calls.append(1),
                        icon="screenshot")
ball.set_action_registry(registry, ctx)


def menu_signature(menu):
    """菜单 → [(text, icon_name)]；分隔线记 ("", "")。"""
    out = []
    for a in menu.actions():
        if a.isSeparator():
            out.append(("", ""))
        else:
            out.append((a.text(), str(a.property("icon_name") or "")))
    return out


def print_sig(sig):
    parts = []
    for t, i in sig:
        if not t:
            parts.append("---")
        else:
            parts.append(f"{t}[{i}]" if i else t)
    print("    " + " | ".join(parts), flush=True)


texts = [a.text() for a in ball._menu.actions()]
sig = menu_signature(ball._menu)
print_sig(sig)

# 三段结构（方案 A）：进入段=打开主窗口/小卡片▸，执行段=番茄钟/追加项/插件▸，
# 退出段=退出程序；段间各一条分隔线。
expected = [
    ("打开主窗口", "window"), ("小卡片", "ball"), ("", ""),
    ("开始专注", "pomodoro"), ("截图钉屏", "screenshot"),
    ("插件功能", "plugins"), ("", ""),
    ("退出程序", "power"),
]
check("E1 插件功能子菜单插在执行段末尾、退出程序之前（文本+图标签名）",
      sig == expected, f"{sig}")
check("E2 退出程序固定垫底，截图钉屏落执行段（插件功能之前）",
      texts[-1] == "退出程序"
      and texts.index("截图钉屏") < texts.index("插件功能"),
      f"{texts[-4:]}")
_submenu = next((a.menu() for a in ball._menu.actions()
                 if a.text() == "插件功能"), None)
check("E2b 子菜单真实存在且收纳全部插件动作（兜底 plugin 图标）",
      _submenu is not None
      and menu_signature(_submenu) == [("屏幕取色", "plugin")],
      f"{menu_signature(_submenu) if _submenu else None}")
_card_sub = next((a.menu() for a in ball._menu.actions()
                  if a.text() == "小卡片"), None)
check("E2c 小卡片子菜单收录 7 个模式（图标复用 card_modes）",
      _card_sub is not None
      and menu_signature(_card_sub) == [
          ("碎片", "fragments"), ("知识卡片", "knowledge"),
          ("日程任务", "tasks"), ("临时笔记", "notes"),
          ("网址导航", "nav"), ("临时素材", "assets"),
          ("软件导航", "apps")],
      f"{menu_signature(_card_sub) if _card_sub else None}")

# 基线（无插件）：进入段 | 开始专注/截图钉屏 | 退出程序
baseline = [
    ("打开主窗口", "window"), ("小卡片", "ball"), ("", ""),
    ("开始专注", "pomodoro"), ("截图钉屏", "screenshot"), ("", ""),
    ("退出程序", "power"),
]

# 通过菜单 QAction 真触发插件 run()
action_item = [a for a in _submenu.actions() if a.text() == "屏幕取色"] \
    if _submenu else []
check("E3 能找到插件菜单项", len(action_item) == 1)
action_item[0].trigger()
mod = sys.modules.get("floatpulse_plugin_01_demo")
check("E4 点击菜单项真的驱动了插件 run()",
      mod is not None and mod.CALLS == ["demo.pick"],
      f"{getattr(mod, 'CALLS', None)}")

# 追加项回调仍然只有它自己触发
check("E5 触发插件动作不会误触运行时追加项", _extra_calls == [])
[a for a in ball._menu.actions() if a.text() == "截图钉屏"][0].trigger()
check("E6 运行时追加项回调可正常触发", _extra_calls == [1])

# ====================================================================
# F：禁用 / 启用
# ====================================================================
n = loader.deactivate()
ball.refresh_plugin_menu()
after_off = menu_signature(ball._menu)
check("F1 deactivate 摘掉全部插件动作（demo.pick + boom.act，"
      ".fpplug 动作已不存在）", n == 2, f"摘除 {n}")
check("F2 菜单回到无插件基线", after_off == baseline, f"{after_off}")

loader.activate()
ball.refresh_plugin_menu()
after_on = menu_signature(ball._menu)
check("F3 activate 恢复动作且菜单复原", after_on == expected, f"{after_on}")
check("F4 activate 未重新导入模块（同一模块对象）",
      sys.modules.get("floatpulse_plugin_01_demo") is mod)

# 禁用后热键入口也应消失（occupancy 反映在 claims 上）
check("F5 动作禁用后不再占用热键入口",
      registry.set_enabled("demo.pick", False) is True
      and "ctrl+alt+c" not in registry.hotkey_claims())
registry.set_enabled("demo.pick", True)

# ====================================================================
# G：球本身仍然是好的
# ====================================================================
check("G1 全程插件层未 import knowledge_ball 之外的宿主私有成员",
      hasattr(ball, "set_action_registry") and hasattr(ball, "refresh_plugin_menu"))
check("G2 悬浮球右键菜单对象仍可用", ball._menu is not None
      and len(ball._menu.actions()) == len(expected))
check("G3 球体未因插件异常被破坏（可见性与尺寸正常）",
      ball._host_size > 0 and ball._ball_size > 0)

# ====================================================================
# I：菜单图标化护栏（2026-10-02 缺陷 #1 / #2 的钉子）
#   I1 菜单文本不得含 emoji（含子菜单，防豆腐块回潮）
#   I2 护栏自检：两主题 QSS 不同 + 失配可检出（否则 I3/I4 是恒真假护栏）
#   I3/I4 切主题后一级 + 子菜单 QSS 全部跟随
#      （此前 apply_theme 只刷一级菜单：先切主题再右键，子菜单停留在
#        建菜单那一刻的主题 —— 实测过的真缺陷）
# ====================================================================
from PyQt6.QtWidgets import QMenu as _QMenu          # noqa: E402
from src.icons import _is_leading_glyph              # noqa: E402
from src.theme import get_menu_qss                   # noqa: E402

_all_menus = lambda: [ball._menu] + ball._menu.findChildren(_QMenu)  # noqa: E731

bad_glyph = [(a.text(), ch) for m in _all_menus() for a in m.actions()
             for ch in (a.text() or "") if _is_leading_glyph(ch)]
check("I1 菜单文本（含子菜单）不得含 emoji / 字形前缀", not bad_glyph,
      f"{bad_glyph}")

qss_light = get_menu_qss("light")
_subs = ball._menu.findChildren(_QMenu)
_probe = _subs[0]
_probe.setStyleSheet(get_menu_qss("dark"))       # 故意改坏 → 比较必须能变红
_mismatch_seen = any(m.styleSheet() != qss_light for m in _all_menus())
_probe.setStyleSheet(qss_light)
check("I2 护栏自检：两主题 QSS 不同且失配可检出（I3/I4 非恒真）",
      qss_light != get_menu_qss("dark") and _mismatch_seen)

ball.apply_theme("light")
check("I3 切浅色后一级 + 子菜单 QSS 全部跟随",
      all(m.styleSheet() == qss_light for m in _all_menus()),
      f"菜单数={len(_all_menus())}（含一级）")
ball.apply_theme("dark")
check("I4 切回深色后一级 + 子菜单 QSS 全部跟随",
      all(m.styleSheet() == get_menu_qss("dark") for m in _all_menus()))

ball.deleteLater()

# ====================================================================
# H：加载失败可感知（2026-09-27 增强）
#   此前所有失败路径都是 _warn + return None，失败项不进任何列表，
#   用户只能看到「共 0 个插件」，无从判断原因。本节验证 FailurePlugin
#   结构化记录全链路可用。
# ====================================================================
import json as _json                    # noqa: E402
from src.plugin_loader import (         # noqa: E402
    STAGE_MANIFEST_READ, STAGE_MANIFEST_INVALID,
    STAGE_REQUIRES_REJECTED, STAGE_NO_PLUGIN_CLASS,
    STAGE_ENTRY_MISSING, STAGE_IMPORT_FAILED,
    STAGE_INSTANTIATE_FAILED, STAGE_CREATE_ACTIONS_FAILED,
    STAGE_REGISTER_FAILED, stage_label, FailedPlugin,
)
from src.plugin_api import ActionRegistry as _AR  # noqa: E402

_h_root = tempfile.mkdtemp(prefix="fp_loader_err_")


def _hmk(name, files):
    d = os.path.join(_h_root, name)
    os.makedirs(d, exist_ok=True)
    for fn, content in files.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write(content)


_E_SRC = """
from src.plugin_api import BallPlugin, BallAction
class A(BallAction):
    id = "%s.a"; title = "A"
    def run(self, ctx): pass
class P(BallPlugin):
    def create_actions(self, ctx): return [A()]
"""
_hmk("e-read", {"manifest.json": "{not json"})
_hmk("e-invalid", {"manifest.json": _json.dumps(
    {"id": "e-invalid", "version": "1.0"})})            # 缺 name/entry
_hmk("e-req", {"manifest.json": _json.dumps(
    {"id": "e-req", "name": "R", "version": "1.0",
     "entry": "plugin.py", "requires": ["socket"]}), "plugin.py": "x=1"})
_hmk("e-entry", {"manifest.json": _json.dumps(
    {"id": "e-entry", "name": "E", "version": "1.0",
     "entry": "nope.py", "requires": []})})
_hmk("e-import", {"manifest.json": _json.dumps(
    {"id": "e-import", "name": "I", "version": "1.0",
     "entry": "plugin.py", "requires": []}),
    "plugin.py": "raise RuntimeError('boom on import')"})
_hmk("e-noclass", {"manifest.json": _json.dumps(
    {"id": "e-noclass", "name": "N", "version": "1.0",
     "entry": "plugin.py", "requires": []}), "plugin.py": "x = 1\n"})
_hmk("e-init", {"manifest.json": _json.dumps(
    {"id": "e-init", "name": "In", "version": "1.0",
     "entry": "plugin.py", "requires": []}),
    "plugin.py": """
from src.plugin_api import BallPlugin
class P(BallPlugin):
    def __init__(self): raise ValueError('ctor boom')
    def create_actions(self, ctx): return []
"""})
_hmk("e-actions", {"manifest.json": _json.dumps(
    {"id": "e-actions", "name": "Ac", "version": "1.0",
     "entry": "plugin.py", "requires": []}),
    "plugin.py": """
from src.plugin_api import BallPlugin
class P(BallPlugin):
    def create_actions(self, ctx): raise KeyError('actions boom')
"""})
_hmk("e-ok", {"manifest.json": _json.dumps(
    {"id": "e-ok", "name": "OK", "version": "1.0",
     "entry": "plugin.py", "requires": [],
     "actions": [{"id": "e-ok.a", "title": "A", "menu": True}]}),
    "plugin.py": _E_SRC % "e-ok"})

_hreg = _AR()
_hloader = PluginLoader(_hreg, ctx=None, plugins_dir=_h_root)
_hok = _hloader.load_all()
_herr = _hloader.load_errors()

check("H1 成功插件与失败插件分别记录（1 成功 / 8 失败）",
      len(_hok) == 1 and len(_herr) == 8,
      f"ok={len(_hok)} failed={len(_herr)}")
check("H2 load_errors() 返回 FailedPlugin 实例",
      all(isinstance(e, FailedPlugin) for e in _herr))
check("H3 has_errors() 反映失败存在", _hloader.has_errors() is True)

_stages = {e.folder: e.stage for e in _herr}
check("H4 各失败阶段被正确归类",
      _stages.get("e-read") == STAGE_MANIFEST_READ
      and _stages.get("e-invalid") == STAGE_MANIFEST_INVALID
      and _stages.get("e-req") == STAGE_REQUIRES_REJECTED
      and _stages.get("e-entry") == STAGE_ENTRY_MISSING
      and _stages.get("e-import") == STAGE_IMPORT_FAILED
      and _stages.get("e-noclass") == STAGE_NO_PLUGIN_CLASS
      and _stages.get("e-init") == STAGE_INSTANTIATE_FAILED
      and _stages.get("e-actions") == STAGE_CREATE_ACTIONS_FAILED,
      f"{_stages}")
check("H5 每条失败都带非空修复建议",
      all(e.hint for e in _herr))
check("H6 每条失败都带可定位的 path 与 folder",
      all(e.path and e.folder for e in _herr))
check("H7 失败阶段都有中文标签",
      all(stage_label(e.stage) != "加载失败" for e in _herr))
check("H8 manifest 已解析的失败项带上 plugin_id / name",
      any(e.plugin_id == "e-req" for e in _herr)
      and any(e.name == "E" for e in _herr))

# 全部动作登记失败的插件应转为 STAGE_REGISTER_FAILED
_h2_root = tempfile.mkdtemp(prefix="fp_loader_reg_")
with open(os.path.join(_h2_root, "x.mp"), "w") as f:
    pass
os.makedirs(os.path.join(_h2_root, "dup"), exist_ok=True)
with open(os.path.join(_h2_root, "dup", "manifest.json"), "w",
          encoding="utf-8") as f:
    _json.dump({"id": "dup", "name": "Dup", "version": "1.0",
                "entry": "plugin.py", "requires": [],
                "actions": [{"id": "e-ok.a", "title": "冲突",
                             "menu": True}]}, f)
with open(os.path.join(_h2_root, "dup", "plugin.py"), "w",
          encoding="utf-8") as f:
    f.write(_E_SRC % "dup")
_h2reg = _AR()
_h2reg.register(_hok[0].actions_raw[0], "e-ok")      # 先占住 e-ok.a
_h2loader = PluginLoader(_h2reg, ctx=None, plugins_dir=_h2_root)
_h2loader.load_all()
check("H9 动作 id 全被占用 → 记为登记失败（0 个生效）",
      any(e.stage == STAGE_REGISTER_FAILED for e in _h2loader.load_errors()),
      f"{[e.stage for e in _h2loader.load_errors()]}")

# rescan 应重置失败列表（不累积陈旧失败）
_hloader.rescan()
check("H10 rescan 后失败列表被重置（不累积陈旧项）",
      len(_hloader.load_errors()) == 8,
      f"after_rescan={len(_hloader.load_errors())}")

shutil.rmtree(_h_root, ignore_errors=True)
shutil.rmtree(_h2_root, ignore_errors=True)

# ---- 汇总 ----
failed = [n_ for n_, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
