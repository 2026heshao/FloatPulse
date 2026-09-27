# -*- coding: utf-8 -*-
"""
verify_plugin_store.py — 插件商店（商店 / 安装分离 + 惰性安装）验证

背景（2026-09-27 晚，用户需求）：
  「我是开发者，我不能一点卸载文件直接没了。需要新增一个插件商店文件夹，
  和一个链接应用的插件文件夹（当前这个），提前下载好的插件压缩包放商店里，
  解压使用的放插件文件夹里，点击卸载后商店里还有。」

  由此确立双目录模型：
    plugin_store/   .fpplug 源包，**永不被自动解压**
    plugins/        解压后的插件文件夹，唯一被 load_all() 加载的位置
  卸载只删 plugins/<id>/ → 商店源包保留 → 卡片回落「未安装」，可再装。

验证手法：真实 PluginLoader + 真实 PluginsPanel（离屏），端到端驱动。
运行：python tools/run_gui_check.py tools/verify_plugin_store.py
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, r"D:\桌面\AI Port\FloatPulse\v4")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from src.plugin_api import ActionRegistry                      # noqa: E402
from src.plugin_loader import PluginLoader, StoreEntry         # noqa: E402
from src.plugins_panel import PluginsPanel                     # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ====================================================================
# 夹具：临时项目根 + 商店包
# ====================================================================
_root = tempfile.mkdtemp(prefix="fp_store_verify_")
_plugins = os.path.join(_root, "plugins")
_store = os.path.join(_root, "plugin_store")
os.makedirs(_plugins, exist_ok=True)
os.makedirs(_store, exist_ok=True)

_PLUGIN_SRC = """# -*- coding: utf-8 -*-
from src.plugin_api import BallPlugin, BallAction


class Hello(BallAction):
    id = "%s.hello"
    title = "打招呼"

    def run(self, ctx):
        pass


class P(BallPlugin):
    def create_actions(self, ctx):
        return [Hello()]
"""


def make_package(filename, plugin_id, name="商店插件", version="1.0.0",
                 hotkey="Ctrl+Alt+8", nested=False,
                 extra_members=None, description="来自商店的示例插件"):
    """把一个最简插件打成 .fpplug 写进商店目录"""
    manifest = {
        "id": plugin_id, "name": name, "version": version,
        "entry": "plugin.py", "requires": [],
        "description": description,
        "actions": [{"id": f"{plugin_id}.hello", "title": "打招呼",
                     "hotkey": hotkey, "menu": True}],
    }
    prefix = (plugin_id + "/") if nested else ""
    path = os.path.join(_store, filename)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{prefix}manifest.json",
                    json.dumps(manifest, ensure_ascii=False))
        zf.writestr(f"{prefix}plugin.py", _PLUGIN_SRC % plugin_id)
        zf.writestr(f"{prefix}icon.png", b"\x89PNG\r\n\x1a\n")
        for member, data in (extra_members or {}).items():
            zf.writestr(f"{prefix}{member}", data)
    return path


_pkg = make_package("demo.fpplug", "demo")
make_package("nested.fpplug", "nested", name="套层插件",
             hotkey="Ctrl+Alt+9", nested=True)
with open(os.path.join(_store, "junk.fpplug"), "wb") as f:
    f.write(b"not a zip at all")
# 商店目录里的非 .fpplug 文件应被忽略
with open(os.path.join(_store, "readme.txt"), "w", encoding="utf-8") as f:
    f.write("说明书，不是插件包")

_reg = ActionRegistry()
_loader = PluginLoader(_reg, ctx=None, plugins_dir=_plugins, store_dir=_store)


# ====================================================================
# A. 目录模型
# ====================================================================
check("A1. PluginLoader.store_dir 是 property（与 plugins_dir 同款，不可当方法调用）",
      isinstance(PluginLoader.store_dir, property),
      type(PluginLoader.store_dir).__name__)

check("A2. plugins_dir 与 store_dir 是两个不同目录（商店 / 安装分离）",
      os.path.normcase(_loader.plugins_dir) != os.path.normcase(_loader.store_dir),
      f"{os.path.basename(_loader.plugins_dir)} vs "
      f"{os.path.basename(_loader.store_dir)}")

check("A3. store_dir 默认落在 <base_dir>/plugin_store",
      os.path.basename(os.path.normpath(PluginLoader(_reg, ctx=None).store_dir))
      == "plugin_store",
      PluginLoader(_reg, ctx=None).store_dir)

check("A4. ensure_store_dir 创建目录且幂等",
      _loader.ensure_store_dir() and _loader.ensure_store_dir()
      and os.path.isdir(_store),
      _store)


# ====================================================================
# B. 惰性扫描：只读 manifest，绝不解压
# ====================================================================
entries = _loader.scan_store()
by_file = {e.filename: e for e in entries}

check("B1. 商店扫描：3 个 .fpplug 全部入列，非包文件被忽略",
      set(by_file) == {"demo.fpplug", "nested.fpplug", "junk.fpplug"},
      f"entries={sorted(by_file)}")

check("B2. 合法包解析出 id / 版本 / 描述，且 usable=True",
      by_file["demo.fpplug"].usable
      and by_file["demo.fpplug"].plugin_id == "demo"
      and by_file["demo.fpplug"].version == "1.0.0"
      and "示例插件" in by_file["demo.fpplug"].description,
      repr(by_file["demo.fpplug"]))

check("B3. 套层目录的包也能解析（兼容常见打包失误）",
      by_file["nested.fpplug"].usable
      and by_file["nested.fpplug"].plugin_id == "nested",
      repr(by_file["nested.fpplug"]))

check("B4. 坏包不消失、不抛异常，带着 error 留在列表里",
      not by_file["junk.fpplug"].usable
      and "zip" in by_file["junk.fpplug"].error,
      f"error={by_file['junk.fpplug'].error!r}")

check("B5. ★扫描是纯只读：一个字节都没解压到安装目录",
      os.listdir(_plugins) == [],
      f"plugins_dir={os.listdir(_plugins)}")

check("B6. ★扫描不移动/不删除商店源包",
      sorted(os.listdir(_store)) == ["demo.fpplug", "junk.fpplug",
                                     "nested.fpplug", "readme.txt"],
      f"store={sorted(os.listdir(_store))}")

check("B7. StoreEntry.installed 初始为 False（尚未安装）",
      by_file["demo.fpplug"].installed is False,
      f"installed={by_file['demo.fpplug'].installed}")


# ====================================================================
# C. ★核心回归：load_all() 不再自动解压
# ====================================================================
loaded_before = _loader.load_all()
check("C1. ★load_all() 不自动解压商店包（此前会静默装上，导致卸载等于没卸）",
      loaded_before == [] and os.listdir(_plugins) == [],
      f"loaded={[p.plugin_id for p in loaded_before]} "
      f"plugins_dir={os.listdir(_plugins)}")

# 用户把包错放进安装目录 -> 应提示引导去商店，但仍不自动装
shutil.copy2(_pkg, os.path.join(_plugins, "demo.fpplug"))
_loader2 = PluginLoader(_reg, ctx=None, plugins_dir=_plugins,
                        store_dir=_store)
_msgs = []


class _Cap:
    """最小 logger 替身，收集日志消息"""

    class _RL:
        pass

    def info(self, m):
        _msgs.append(("info", m))

    def warning(self, m, *a, **k):
        _msgs.append(("warn", m))


_loader2._logger = _Cap()
_loader2.load_all()
check("C2. 安装目录里的 .fpplug 只提示、不自动解压（引导用户移到商店）",
      any("插件安装目录里发现" in m for _lvl, m in _msgs)
      and os.listdir(_plugins) == ["demo.fpplug"],
      f"msgs={[m[:40] for _l, m in _msgs]}")
os.remove(os.path.join(_plugins, "demo.fpplug"))


# ====================================================================
# D. 显式安装
# ====================================================================
ok_d1, msg_d1 = _loader.install_from_store("demo")
check("D1. install_from_store 成功解压到 plugins/<id>/",
      ok_d1 and os.path.isfile(os.path.join(_plugins, "demo", "manifest.json"))
      and os.path.isfile(os.path.join(_plugins, "demo", "icon.png")),
      msg_d1)

check("D2. ★安装后商店源包原封不动（不移动、不删除）",
      os.path.isfile(_pkg),
      _pkg)

loaded_now = _loader.load_all()
check("D3. 安装后 load_all() 能扫到并加载该插件",
      [p.plugin_id for p in loaded_now] == ["demo"],
      f"loaded={[p.plugin_id for p in loaded_now]}")

_act = _reg.get("demo.hello")
check("D4. 插件动作已登记，图标路径指向解压后的 icon.png",
      _act is not None
      and bool(_act.icon_path)
      and _act.icon_path.endswith("icon.png"),
      f"icon={getattr(_act, 'icon_path', None)}")

check("D5. 安装后商店条目 installed 转为 True",
      {e.filename: e.installed
       for e in _loader.scan_store()}["demo.fpplug"] is True,
      f"installed={_loader.scan_store()[0].installed}")

ok_d6, msg_d6 = _loader.install_from_store("demo")
check("D6. 重复安装被拒（不覆盖已装内容）",
      not ok_d6 and "已安装" in msg_d6,
      msg_d6)

ok_d7, msg_d7 = _loader.install_from_store("no-such-plugin")
check("D7. 商店里没有的 id 被拒且消息里带上 id",
      not ok_d7 and "no-such-plugin" in msg_d7,
      msg_d7)

ok_d8, msg_d8 = _loader.install_from_store("../evil")
check("D8. 非法 id（目录穿越）被拒",
      not ok_d8 and "非法" in msg_d8,
      msg_d8)


# ====================================================================
# E. ★闭环：卸载后商店包仍在，可再装
# ====================================================================
_ok_u, _msg_u = _loader.uninstall("demo")
check("E1. 卸载成功删掉 plugins/<id>/",
      _ok_u and not os.path.isdir(os.path.join(_plugins, "demo")),
      _msg_u)

check("E2. ★★卸载后商店源包仍在（用户核心诉求：卸载不能把源包删了）",
      os.path.isfile(_pkg),
      f"exists={os.path.isfile(_pkg)}")

check("E3. 卸载后动作已从注册表摘除",
      _reg.get("demo.hello") is None and _reg.all_actions() == [],
      f"actions={[a.id for a in _reg.all_actions()]}")

check("E4. 卸载后商店条目回落为「未安装」（卡片可再点安装）",
      all(not e.installed for e in _loader.scan_store()
          if e.filename == "demo.fpplug"),
      f"installed={[e.installed for e in _loader.scan_store()]}")

ok_e5, msg_e5 = _loader.install_from_store("demo")
check("E5. ★重装成功（模块缓存已清，能真正重新导入）",
      ok_e5 and [p.plugin_id for p in _loader.load_all()] == ["demo"]
      and _reg.get("demo.hello") is not None,
      msg_e5)


# ====================================================================
# F. zip-slip 防护（复用 _safe_extract）
# ====================================================================
make_package("slip.fpplug", "slip", hotkey="Ctrl+Alt+5",
             extra_members={"../../pwned.txt": "pwned"})
ok_f, _msg_f = _loader.install_from_store("slip")
check("F1. 含路径穿越成员的包：正常文件解出，越界成员被剔除",
      ok_f
      and os.path.isfile(os.path.join(_plugins, "slip", "plugin.py"))
      and not os.path.exists(os.path.join(_root, "pwned.txt"))
      and not os.path.exists(os.path.join(os.path.dirname(_root), "pwned.txt")),
      f"ok={ok_f} pwned={os.path.exists(os.path.join(_root, 'pwned.txt'))}")


# ====================================================================
# G. 面板 UI：商店区 / 安装按钮 / 目录标签
# ====================================================================
class FakeHost:
    """最小宿主替身：refresh 只读 plugin_loader 与 _config"""

    class _Cfg:
        def get(self, k, d=None):
            return d

    def __init__(self, loader=None):
        self.plugin_loader = loader
        self._config = self._Cfg()


panel = PluginsPanel(FakeHost(_loader))
app.processEvents()

btns = [b.text() for b in panel.findChildren(QPushButton)]
check("G1. 工具栏含「打开插件商店」按钮",
      any("打开插件商店" in t for t in btns),
      f"btns={btns}")

check("G2. 商店区容器存在，且商店有包时可见",
      panel._store_box is not None and not panel._store_box.isHidden(),
      f"hidden={getattr(panel._store_box, 'isHidden', lambda: '?')()}")

check("G3. 商店目录绝对路径已写在面板上（用户可核对）",
      _store in panel._store_dir_label.text(),
      panel._store_dir_label.text())

check("G4. 安装目录绝对路径同样写出（两个目录不混淆）",
      _plugins in panel._dir_label.text(),
      panel._dir_label.text())

check("G5. 计数标签含「商店可安装 N 个」",
      "商店可安装" in panel._count_label.text(),
      panel._count_label.text())

store_texts = "\n".join(w.text() for w in panel._store_box.findChildren(QLabel))
check("G6. 商店卡片展示「未安装」「已安装」「包不合法」三种状态",
      "未安装" in store_texts and "已安装" in store_texts
      and "包不合法" in store_texts,
      f"has 未安装={'未安装' in store_texts} "
      f"已安装={'已安装' in store_texts} "
      f"不合法={'包不合法' in store_texts}")

_install_btns = [b for b in panel._store_box.findChildren(QPushButton)
                 if "安装" in b.text()]
_enabled = [b.text() for b in _install_btns if b.isEnabled()]
_disabled = [b.text() for b in _install_btns if not b.isEnabled()]
check("G7. 未安装的包有可点「⬇ 安装」；已安装 →「✓ 已安装」，坏包 →「⊘ 无法安装」，二者均禁用",
      _enabled == ["⬇ 安装"]
      and sorted(_disabled) == ["⊘ 无法安装", "✓ 已安装", "✓ 已安装"],
      f"enabled={_enabled} disabled={sorted(_disabled)}")

check("G8. 面板任何 QLabel 都不再出现旧文案「plugins/ 文件夹」式误导",
      "放进 plugins/ 目录" not in "\n".join(
          w.text() for w in panel.findChildren(QLabel)),
      "已改为商店目录引导")


# ====================================================================
# H. 面板安装按钮端到端（真走 _on_install 的 loader 调用，跳过弹窗）
# ====================================================================
_loader.uninstall("nested")            # 确保 nested 未装
_ok_h, _msg_h = _loader.install_from_store("nested")
check("H1. 面板对应的 loader 调用可安装套层包",
      _ok_h and os.path.isfile(os.path.join(_plugins, "nested", "plugin.py")),
      _msg_h)


# ====================================================================
# I. 无需加载器时降级不崩
# ====================================================================
try:
    p_none = PluginsPanel(FakeHost(None))
    p_none.refresh()
    ok_i = "共 0 个插件" in p_none._count_label.text()
    err_i = ""
except Exception as exc:                                   # noqa: BLE001
    ok_i, err_i = False, f"{type(exc).__name__}: {exc}"
check("I1. 无 loader 时面板不崩、计数 0、商店区隐藏",
      ok_i and p_none._store_box.isHidden(),
      err_i or p_none._count_label.text())


class LegacyLoader:
    """旧 loader：没有 store_dir / scan_store"""

    @property
    def plugins_dir(self):
        return _plugins

    def loaded_plugins(self):
        return []


try:
    p_legacy = PluginsPanel(FakeHost(LegacyLoader()))
    p_legacy.refresh()
    ok_j = "共 0 个插件" in p_legacy._count_label.text() \
        and "暂不可用" in p_legacy._store_dir_label.text()
    err_j = ""
except Exception as exc:                                   # noqa: BLE001
    ok_j, err_j = False, f"{type(exc).__name__}: {exc}"
check("I2. 旧 loader（无 store_dir/scan_store）面板降级不崩",
      ok_j, err_j or p_legacy._store_dir_label.text())


shutil.rmtree(_root, ignore_errors=True)

print()
failed = [r for r in results if not r[1]]
print(f"[DONE] {len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    sys.exit(1)
sys.exit(0)
