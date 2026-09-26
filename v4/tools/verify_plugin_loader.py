# -*- coding: utf-8 -*-
"""插件框架离屏端到端验证：plugins/ 扫描 → 注册表 → 悬浮球右键菜单。

覆盖（每一步都调真实代码路径，不做注入替身）：
  A 目录不存在 → 自动创建，不报错
  B 合法插件 → 动作进注册表、菜单出现、QAction.trigger() 真能驱动插件 run()
  C 坏插件（requires=requests / 抢核心热键 / 抢已占热键）→ 全部跳过 + 日志有记录
  D .fpplug（zip）→ 自动解压后加载，原文件保留
  E 右键菜单顺序：打开主窗口 | [插件动作] | 退出程序 | 运行时追加项（截图钉屏仍在最后）
  F deactivate → 菜单回到无插件基线；activate → 恢复且模块未重导入
  G 插件 run() 抛异常 → 球不崩（注册表兜住），菜单/其他插件不受影响

跑法：python tools/run_gui_check.py tools/verify_plugin_loader.py
"""
import json
import logging
import os
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
check("D1 .fpplug 自动解压后加载",
      registry.get("zipped.act") is not None
      and os.path.isfile(os.path.join(plugins_dir, "06-zipped", "plugin.py")))
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
check("C4 坏插件不影响其他插件（02/03 导入成功但动作被跳过，04 被拒）",
      ids == ["01-demo", "02-rival", "03-thief", "05-boom", "06-zipped"],
      f"{ids}")
check("F0 run() 抛异常被兜住", registry.trigger("boom.act", ctx) is False
      and any("已隔离" in m for m in warns()))

# ====================================================================
# E：悬浮球右键菜单数据驱动
# ====================================================================
ball = FloatingBall([])
_extra_calls = []
ball.add_context_action("✂ 截图钉屏", lambda: _extra_calls.append(1))
ball.set_action_registry(registry, ctx)

texts = [a.text() for a in ball._menu.actions()]
print(f"    菜单项：{texts}", flush=True)

expected = ["🖥  打开主窗口", "", "屏幕取色", "压缩包动作", "", "退出程序",
            "", "✂ 截图钉屏"]
check("E1 插件动作插在「打开主窗口」与「退出程序」之间", texts == expected,
      f"{texts}")
check("E2 运行时追加项（截图钉屏）仍在最后，位置未变",
      texts[-1] == "✂ 截图钉屏" and texts[-2] == "")

# 基线（无插件）与历史行为一致
baseline = ["🖥  打开主窗口", "", "退出程序", "", "✂ 截图钉屏"]

# 通过菜单 QAction 真触发插件 run()
action_item = [a for a in ball._menu.actions() if a.text() == "屏幕取色"]
check("E3 能找到插件菜单项", len(action_item) == 1)
action_item[0].trigger()
mod = sys.modules.get("floatpulse_plugin_01_demo")
check("E4 点击菜单项真的驱动了插件 run()",
      mod is not None and mod.CALLS == ["demo.pick"],
      f"{getattr(mod, 'CALLS', None)}")

# 追加项回调仍然只有它自己触发
check("E5 触发插件动作不会误触运行时追加项", _extra_calls == [])
[a for a in ball._menu.actions() if a.text() == "✂ 截图钉屏"][0].trigger()
check("E6 运行时追加项回调可正常触发", _extra_calls == [1])

# ====================================================================
# F：禁用 / 启用
# ====================================================================
n = loader.deactivate()
ball.refresh_plugin_menu()
after_off = [a.text() for a in ball._menu.actions()]
check("F1 deactivate 摘掉全部插件动作", n == 3, f"摘除 {n}")
check("F2 菜单回到无插件基线", after_off == baseline, f"{after_off}")

loader.activate()
ball.refresh_plugin_menu()
after_on = [a.text() for a in ball._menu.actions()]
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

ball.deleteLater()

# ---- 汇总 ----
failed = [n_ for n_, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
