# -*- coding: utf-8 -*-
"""插件框架铁律回归 —— plugin_api / plugin_loader。

钉死的行为（顺序即优先级，先登记先赢）：
  A 插件目录不存在 → 自动创建，返回空列表，不报错
  B 合法插件 → 动作进注册表、可触发、可挂菜单
  C manifest 缺字段 / JSON 损坏 / 入口缺失 → 跳过该插件 + 日志有 warning
  D requires 不在白名单（requests / ssl）→ 拒绝加载 + 日志写明原因
  E 插件 run() 抛异常 → 注册表兜住，返回 False，其他插件不受影响
  F 两个插件声明同一 hotkey → 后者让位 + 日志有记录
  G 插件抢核心热键（Ctrl+Alt+K / Ctrl+Alt+S）→ 跳过 + 日志有记录
  H 插件商店：load_all() **不再**自动解压；scan_store() 只读 manifest；
    install_from_store() 显式安装；卸载后商店源包仍在、可再装
  I deactivate/activate → 摘掉/恢复动作，且**不重新导入模块**
  J 全程不 import knowledge_ball（插件层与宿主解耦的硬约束）
  K 只读数据门面：provider 缺失/抛错只降级不崩；返回深拷贝，插件改不到宿主数据
  L 插件身份与私有目录：for_plugin 派生、data_dir 自动创建、非法 id 拒绝
  M 热键格式校验：格式非法 → 丢掉热键但保留动作（与被占用时整动作让位不同）
  N 注册表按动作记住插件自己的 ctx，trigger 优先用它
"""

import json
import logging
import os
import sys
import uuid
import zipfile
from types import SimpleNamespace

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.plugin_api import (          # noqa: E402
    ActionRegistry, BallAction, BallPlugin, PluginContext, PluginData,
    is_allowed_requirement, is_safe_plugin_id, is_valid_hotkey,
    normalize_hotkey,
)
from src.plugin_loader import PluginLoader, validate_manifest  # noqa: E402


# ====================================================================
# 夹具与工具
# ====================================================================
class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def env(tmp_path):
    records = []
    logger = logging.getLogger(f"fp_plug_test_{uuid.uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(_Capture(records))

    registry = ActionRegistry(
        logger=logger, reserved_hotkeys=("Ctrl+Alt+K", "Ctrl+Alt+S"))
    ctx = PluginContext(
        logger=logger,
        config={"theme": "dark", "clipboard_max_items": 200},
        show_toast=lambda text, ms=2800: None,
        open_main_window=lambda: None,
        open_card_mode=lambda mode: True,
    )
    plugins_dir = tmp_path / "plugins"
    store_dir = tmp_path / "plugin_store"
    loader = PluginLoader(registry, ctx,
                          plugins_dir=str(plugins_dir), logger=logger,
                          store_dir=str(store_dir))
    return SimpleNamespace(
        registry=registry, ctx=ctx, loader=loader, plugins_dir=plugins_dir,
        store_dir=store_dir, logger=logger, records=records, tmp=tmp_path,
        messages=lambda: [r.getMessage() for r in records],
        warnings=lambda: [r.getMessage() for r in records
                          if r.levelno >= logging.WARNING],
    )


def make_code(plugin_id, action_ids, run_bodies=None, extra="", titles=None):
    """生成一份最小插件源码：每个 action id 一个 BallAction 子类。

    ``titles`` 里某项为 None 表示该类**不声明 title**（用于验证 manifest 兜底）。
    """
    run_bodies = run_bodies or ["CALLS.append(self.id)"] * len(action_ids)
    titles = titles if titles is not None else [f"动作{i}" for i in range(len(action_ids))]
    classes, instances = [], []
    for i, aid in enumerate(action_ids):
        cname = f"Act{i}"
        title_line = "" if titles[i] is None else f'    title = "{titles[i]}"\n'
        classes.append(
            f"class {cname}(BallAction):\n"
            f'    id = "{aid}"\n'
            f"{title_line}"
            f"\n"
            f"    def run(self, ctx):\n"
            f"        {run_bodies[i]}\n"
        )
        instances.append(f"{cname}()")
    return (
        "# -*- coding: utf-8 -*-\n"
        "from src.plugin_api import BallAction, BallPlugin\n"
        "\n"
        "CALLS = []\n"
        "\n"
        + extra
        + "\n".join(classes)
        + "\n\n"
        f"class TestPlugin(BallPlugin):\n"
        f'    id = "{plugin_id}"\n'
        f'    name = "测试插件"\n'
        f'    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        f"        return [{', '.join(instances)}]\n"
    )


def action_spec(aid, title="动作", hotkey=None, menu=True):
    spec = {"id": aid, "title": title, "menu": menu}
    if hotkey:
        spec["hotkey"] = hotkey
    return spec


def manifest_of(plugin_id, actions, requires=None, entry="plugin.py",
                version="1.0.0", name="测试插件"):
    manifest = {"id": plugin_id, "name": name, "version": version,
                "entry": entry, "actions": actions}
    if requires is not None:
        manifest["requires"] = requires
    return manifest


def write_plugin(root, plugin_id, manifest, code):
    folder = root / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    return folder


def module_of(plugin_id):
    """取已导入的插件模块（loader 的模块命名规则）"""
    name = "floatpulse_plugin_" + plugin_id.replace("-", "_").replace(".", "_")
    return sys.modules.get(name)


# ====================================================================
# J. 解耦硬约束
# ====================================================================
def test_plugin_layer_never_imports_knowledge_ball(env):
    # 只检查本次加载「新引入」的模块：全量跑时别的文件可能早已导入 knowledge_ball，
    # 直接断言 "knowledge_ball" not in sys.modules 会被全局状态污染而误判
    before = set(sys.modules)
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]),
                 make_code("demo", ["demo.ping"]))
    env.loader.load_all()
    new_modules = set(sys.modules) - before
    assert not any("knowledge_ball" in m for m in new_modules)


# ====================================================================
# A. 目录不存在
# ====================================================================
def test_missing_plugins_dir_created_and_empty(env):
    assert not env.plugins_dir.exists()
    assert env.loader.load_all() == []
    assert env.plugins_dir.is_dir()


# ====================================================================
# B. 合法插件
# ====================================================================
def test_valid_plugin_registered_and_triggerable(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping", hotkey="Ctrl+Alt+C")]),
                 make_code("demo", ["demo.ping"]))
    loaded = env.loader.load_all()

    assert [p.plugin_id for p in loaded] == ["demo"]
    action = env.registry.get("demo.ping")
    assert action is not None
    assert action.title == "动作0"
    assert action.hotkey == "Ctrl+Alt+C"
    assert action.menu is True
    assert env.registry.owner_of("demo.ping") == "demo"
    assert [a.id for a in env.registry.menu_actions()] == ["demo.ping"]

    assert env.registry.trigger("demo.ping", env.ctx) is True
    assert module_of("demo").CALLS == ["demo.ping"]


def test_manifest_drives_entry_metadata(env):
    """manifest 是入口声明（hotkey / menu）的唯一真相源；title 由代码优先"""
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping", title="清单标题",
                                                  hotkey="Ctrl+Alt+J", menu=False)]),
                 make_code("demo", ["demo.ping"]))
    env.loader.load_all()
    action = env.registry.get("demo.ping")
    assert action.title == "动作0"                 # 代码显式设置 → 代码优先
    assert action.hotkey == "Ctrl+Alt+J"           # manifest 权威
    assert action.menu is False                    # manifest 权威
    assert env.registry.menu_actions() == []
    assert [a.id for a in env.registry.hotkey_actions()] == ["demo.ping"]


def test_manifest_title_used_as_fallback(env):
    """插件代码没声明 title 时，用 manifest.actions[].title 兜底"""
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping", title="清单标题")]),
                 make_code("demo", ["demo.ping"], titles=[None]))
    env.loader.load_all()
    assert env.registry.get("demo.ping").title == "清单标题"


# ====================================================================
# C. 坏 manifest / 坏入口
# ====================================================================
def test_missing_manifest_field_skipped_but_others_load(env):
    write_plugin(env.plugins_dir, "broken",
                 {"id": "broken", "name": "缺字段"}, make_code("broken", ["x.a"]))
    write_plugin(env.plugins_dir, "good",
                 manifest_of("good", [action_spec("good.ping")]),
                 make_code("good", ["good.ping"]))
    loaded = env.loader.load_all()

    assert [p.plugin_id for p in loaded] == ["good"]
    assert env.registry.get("x.a") is None
    assert any("broken" in m and "version" in m for m in env.warnings())


def test_broken_json_skipped(env):
    folder = env.plugins_dir / "broken"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text("{ not json", encoding="utf-8")
    write_plugin(env.plugins_dir, "good",
                 manifest_of("good", [action_spec("good.ping")]),
                 make_code("good", ["good.ping"]))

    assert [p.plugin_id for p in env.loader.load_all()] == ["good"]
    assert any("读取失败" in m for m in env.warnings())


def test_missing_entry_module_skipped(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")], entry="nope.py"),
                 make_code("demo", ["demo.ping"]))
    assert env.loader.load_all() == []
    assert any("入口模块不存在" in m for m in env.warnings())


def test_entry_outside_plugin_dir_rejected(env):
    manifest = manifest_of("demo", [action_spec("demo.ping")], entry="../evil.py")
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    (env.plugins_dir / "demo").mkdir(parents=True, exist_ok=True)
    (env.plugins_dir / "demo" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert env.loader.load_all() == []
    assert any("entry" in m for m in env.warnings())


def test_no_plugin_class_skipped(env):
    code = ("class NotAPlugin:\n"
            "    pass\n")
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]), code)
    assert env.loader.load_all() == []
    assert any("没有 BallPlugin 子类" in m for m in env.warnings())


def test_import_error_skipped_without_crash(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]),
                 "raise RuntimeError('导入就炸')\n")
    assert env.loader.load_all() == []
    assert any("导入失败" in m for m in env.warnings())
    assert "floatpulse_plugin_demo" not in sys.modules


def test_create_actions_raises_skipped(env):
    code = (
        "from src.plugin_api import BallAction, BallPlugin\n"
        "\n"
        "class Act0(BallAction):\n"
        '    id = "demo.ping"\n'
        '    title = "动作0"\n'
        "\n"
        "    def run(self, ctx):\n"
        "        pass\n"
        "\n"
        "class TestPlugin(BallPlugin):\n"
        '    id = "demo"\n'
        '    name = "测试插件"\n'
        '    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        "        raise RuntimeError('建动作就炸')\n"
    )
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]), code)
    assert env.loader.load_all() == []
    assert any("create_actions 抛出异常" in m for m in env.warnings())


# ====================================================================
# D. 依赖白名单
# ====================================================================
@pytest.mark.parametrize("bad", ["requests", "ssl", "urllib.request",
                                 "aiohttp", "socket"])
def test_requires_whitelist_rejects(env, bad):
    write_plugin(env.plugins_dir, "netty",
                 manifest_of("netty", [action_spec("netty.ping")],
                             requires=[bad]),
                 make_code("netty", ["netty.ping"]))
    write_plugin(env.plugins_dir, "good",
                 manifest_of("good", [action_spec("good.ping")]),
                 make_code("good", ["good.ping"]))

    assert [p.plugin_id for p in env.loader.load_all()] == ["good"]
    assert any("白名单" in m and bad in m for m in env.warnings())


def test_requires_whitelist_accepts_pyqt6_and_stdlib(env):
    write_plugin(env.plugins_dir, "ok",
                 manifest_of("ok", [action_spec("ok.ping")],
                             requires=["PyQt6.QtCore", "os", "json"]),
                 make_code("ok", ["ok.ping"]))
    assert [p.plugin_id for p in env.loader.load_all()] == ["ok"]


def test_is_allowed_requirement_unit():
    for ok in ("PyQt6", "PyQt6.QtWidgets", "os", "json", "re", "math"):
        assert is_allowed_requirement(ok), ok
    for bad in ("requests", "ssl", "_ssl", "urllib3", "httpx",
                "urllib.request", "aiohttp", "", None, "../os", "os/path"):
        assert not is_allowed_requirement(bad), bad


# ====================================================================
# E. 异常隔离
# ====================================================================
def test_action_run_exception_isolated(env):
    write_plugin(env.plugins_dir, "boom",
                 manifest_of("boom", [action_spec("boom.act")]),
                 make_code("boom", ["boom.act"],
                           run_bodies=["raise RuntimeError('故意炸')"]))
    write_plugin(env.plugins_dir, "good",
                 manifest_of("good", [action_spec("good.ping")]),
                 make_code("good", ["good.ping"]))
    env.loader.load_all()

    assert env.registry.trigger("boom.act", env.ctx) is False   # 被兜住
    assert any("已隔离" in m for m in env.warnings())
    # 其他插件照常工作
    assert env.registry.trigger("good.ping", env.ctx) is True
    assert module_of("good").CALLS == ["good.ping"]


def test_trigger_unknown_and_disabled(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]),
                 make_code("demo", ["demo.ping"]))
    env.loader.load_all()

    assert env.registry.trigger("no.such.action", env.ctx) is False
    assert env.registry.set_enabled("demo.ping", False) is True
    assert env.registry.trigger("demo.ping", env.ctx) is False
    assert env.registry.menu_actions() == []


# ====================================================================
# F. 插件间热键冲突
# ====================================================================
def test_same_hotkey_later_plugin_skipped(env):
    write_plugin(env.plugins_dir, "alpha",
                 manifest_of("alpha", [action_spec("alpha.pick",
                                                   hotkey="Ctrl+Alt+C")]),
                 make_code("alpha", ["alpha.pick"]))
    # 大小写/空格不同也算冲突（归一化后同一入口）
    write_plugin(env.plugins_dir, "beta",
                 manifest_of("beta", [action_spec("beta.pick",
                                                  hotkey=" ctrl + ALT + c ")]),
                 make_code("beta", ["beta.pick"]))
    env.loader.load_all()

    assert env.registry.get("alpha.pick") is not None
    assert env.registry.get("beta.pick") is None          # 后者让位
    assert any("冲突" in m and "beta.pick" in m for m in env.warnings())
    assert env.registry.entry_conflicts() == {}


def test_action_id_duplicate_rejected(env):
    write_plugin(env.plugins_dir, "alpha",
                 manifest_of("alpha", [action_spec("same.id")]),
                 make_code("alpha", ["same.id"]))
    write_plugin(env.plugins_dir, "beta",
                 manifest_of("beta", [action_spec("same.id")]),
                 make_code("beta", ["same.id"]))
    env.loader.load_all()

    assert env.registry.owner_of("same.id") == "alpha"    # 先登记者保留
    assert any("动作 id 重复" in m for m in env.warnings())


# ====================================================================
# G. 核心热键让位
# ====================================================================
@pytest.mark.parametrize("core", ["Ctrl+Alt+K", "ctrl + alt + k", "Ctrl+Alt+S"])
def test_core_hotkey_yields(env, core):
    write_plugin(env.plugins_dir, "thief",
                 manifest_of("thief", [action_spec("thief.act", hotkey=core)]),
                 make_code("thief", ["thief.act"]))
    env.loader.load_all()

    assert env.registry.get("thief.act") is None
    assert any("核心功能占用" in m for m in env.warnings())


def test_registry_reserved_hotkeys_normalized():
    reg = ActionRegistry(logger=None, reserved_hotkeys=(" Ctrl+Alt+K ",))
    assert reg.reserved_hotkeys() == frozenset({"ctrl+alt+k"})
    assert reg.hotkey_blocked_by_reserved("CTRL+ALT+K") is True
    assert reg.hotkey_blocked_by_reserved("Ctrl+Alt+Z") is False
    assert normalize_hotkey(None) == ""


def test_hotkey_claims_vs_entry_conflicts():
    """占用表含单例；冲突表只留 >=2 个占用者的键"""
    reg = ActionRegistry(logger=None)

    class A(BallAction):
        def run(self, ctx):
            pass

    a1, a2, b = A(), A(), A()
    a1.id, a1.title, a1.hotkey = "a1", "a1", "Ctrl+Alt+C"
    a2.id, a2.title, a2.hotkey = "a2", "a2", "ctrl + alt + c"   # 同一入口
    b.id, b.title, b.hotkey = "b", "b", "Ctrl+Alt+D"
    for act in (a1, a2, b):
        assert reg.register(act)

    assert reg.hotkey_claims() == {"ctrl+alt+c": ["a1", "a2"], "ctrl+alt+d": ["b"]}
    assert reg.entry_conflicts() == {"ctrl+alt+c": ["a1", "a2"]}
    assert reg.hotkey_claims().get("ctrl+alt+d") == ["b"]        # 单例也可查到
    # 禁用后不再占用入口
    reg.set_enabled("a2", False)
    assert reg.entry_conflicts() == {}
    assert reg.hotkey_claims()["ctrl+alt+c"] == ["a1"]


# ====================================================================
# H. 插件商店：*.fpplug 惰性安装（load_all 不再自动解压）
# ====================================================================
def _write_package(path, plugin_id, action_ids=None, hotkey=None,
                   extra_members=None, nested=False):
    """把一份最简插件打成 .fpplug（zip）写到 path"""
    action_ids = action_ids or [f"{plugin_id}.pick"]
    specs = [action_spec(a) for a in action_ids]
    if hotkey:
        specs[0]["hotkey"] = hotkey
    manifest = manifest_of(plugin_id, specs)
    prefix = (plugin_id + "/") if nested else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{prefix}manifest.json",
                    json.dumps(manifest, ensure_ascii=False))
        zf.writestr(f"{prefix}plugin.py",
                    make_code(plugin_id, action_ids))
        zf.writestr(f"{prefix}icon.png", b"\x89PNG\r\n\x1a\n")
        for member, data in (extra_members or {}).items():
            zf.writestr(f"{prefix}{member}", data)
    return path


def test_load_all_no_longer_auto_unpacks_store_packages(env):
    """核心回归钉：load_all() **不再**解压 plugins/ 里的 .fpplug。

    这是「卸载后商店里的包仍在」的前提——否则每次启动都自动重装，
    卸载等于没卸。
    """
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.plugins_dir / "zipped.fpplug", "zipped")

    loaded = env.loader.load_all()

    assert loaded == []
    assert not (env.plugins_dir / "zipped").exists()
    assert env.plugins_dir.joinpath("zipped.fpplug").is_file()   # 原包没被碰
    # 但要提示用户：安装目录里不该放包
    assert any("插件安装目录里发现" in m for m in env.warnings())


def test_scan_store_reads_manifest_without_extracting(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.store_dir / "alpha.fpplug", "alpha",
                   hotkey="Ctrl+Alt+Z")

    entries = env.loader.scan_store()

    assert [e.plugin_id for e in entries] == ["alpha"]
    e = entries[0]
    assert e.usable and not e.installed
    assert e.version == "1.0.0"
    assert e.filename == "alpha.fpplug"
    # 只读：一个字节都没落到磁盘
    assert not (env.plugins_dir / "alpha").exists()
    assert sorted(os.listdir(env.store_dir)) == ["alpha.fpplug"]


def test_scan_store_with_nested_root_layout(env):
    """常见打包失误：zip 里整体套一层目录 → 仍应能读到 manifest"""
    env.store_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.store_dir / "nested.fpplug", "nested", nested=True)

    entries = env.loader.scan_store()

    assert [e.plugin_id for e in entries] == ["nested"]
    assert entries[0].usable


def test_scan_store_reports_bad_package_without_crashing(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    (env.store_dir / "junk.fpplug").write_bytes(b"not a zip at all")
    _write_package(env.store_dir / "good.fpplug", "good")

    entries = env.loader.scan_store()

    # 好包在前，坏包也不消失（用户需要知道它为什么装不了）
    by_name = {e.filename: e for e in entries}
    assert set(by_name) == {"junk.fpplug", "good.fpplug"}
    assert by_name["good.fpplug"].usable
    assert not by_name["junk.fpplug"].usable
    assert "zip" in by_name["junk.fpplug"].error


def test_scan_store_missing_dir_creates_nothing_and_returns_empty(env):
    assert not env.store_dir.exists()
    assert env.loader.scan_store() == []
    # scan 是纯读操作：不该顺手建目录
    assert not env.store_dir.exists()


def test_install_from_store_extracts_and_loads(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.store_dir / "zipped.fpplug", "zipped",
                   hotkey="Ctrl+Alt+Z")
    assert env.loader.scan_store()[0].installed is False

    ok, msg = env.loader.install_from_store("zipped")

    assert ok, msg
    assert (env.plugins_dir / "zipped" / "manifest.json").is_file()
    assert (env.plugins_dir / "zipped" / "icon.png").is_file()
    # 源包原封不动
    assert (env.store_dir / "zipped.fpplug").is_file()
    # 装完即可被加载
    loaded = env.loader.load_all()
    assert [p.plugin_id for p in loaded] == ["zipped"]
    action = env.registry.get("zipped.pick")
    assert action.icon_path and action.icon_path.endswith("icon.png")
    # 商店条目转为「已安装」
    assert env.loader.scan_store()[0].installed is True


def test_install_from_store_unknown_id_rejected(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    ok, msg = env.loader.install_from_store("nope")
    assert not ok
    assert "nope" in msg


def test_install_from_store_bad_id_rejected(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    ok, msg = env.loader.install_from_store("../evil")
    assert not ok
    assert "非法" in msg


def test_install_refuses_to_overwrite_installed_plugin(env):
    """已装插件不被商店包覆盖——保护用户在安装目录里的改动"""
    env.store_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.store_dir / "dupe.fpplug", "dupe")
    assert env.loader.install_from_store("dupe")[0] is True

    marker = env.plugins_dir / "dupe" / "local_edit.txt"
    marker.write_text("本地改动", encoding="utf-8")
    ok, msg = env.loader.install_from_store("dupe")

    assert not ok
    assert "已安装" in msg
    assert marker.read_text(encoding="utf-8") == "本地改动"


def test_install_package_rejects_path_outside_store(env):
    """install_package 对外暴露：商店目录之外的 zip 一律拒绝解压"""
    env.store_dir.mkdir(parents=True, exist_ok=True)
    outside = env.tmp / "outside.fpplug"
    _write_package(outside, "outside")

    ok, msg = env.loader.install_package(str(outside))

    assert not ok
    assert "不在商店目录内" in msg
    assert not (env.plugins_dir / "outside").exists()


def test_install_package_id_mismatch_rejected(env):
    env.store_dir.mkdir(parents=True, exist_ok=True)
    pkg = _write_package(env.store_dir / "real.fpplug", "real")
    ok, msg = env.loader.install_package(str(pkg), expect_id="other")
    assert not ok
    assert "不匹配" in msg


def test_install_from_store_zip_slip_member_rejected(env):
    """zip-slip：压缩包内的路径穿越成员必须被剔除，不能写到插件目录之外"""
    env.store_dir.mkdir(parents=True, exist_ok=True)
    _write_package(env.store_dir / "slip.fpplug", "slip",
                   extra_members={"../../evil.txt": "pwned"})

    ok, _msg = env.loader.install_from_store("slip")

    assert ok
    assert (env.plugins_dir / "slip" / "plugin.py").is_file()
    assert not (env.tmp / "evil.txt").exists()
    assert not (env.tmp.parent / "evil.txt").exists()
    assert any("非法路径成员" in m for m in env.warnings())


def test_uninstall_keeps_store_package_and_allows_reinstall(env):
    """整套闭环：装 → 卸 → 商店包还在 → 还能再装。

    这条钉住的正是用户提出的核心诉求：
    「点击卸载后商店里还有，这样顺手就可以做插件市场了」。
    """
    env.store_dir.mkdir(parents=True, exist_ok=True)
    pkg = _write_package(env.store_dir / "cycle.fpplug", "cycle",
                         hotkey="Ctrl+Alt+Y")
    assert env.loader.install_from_store("cycle")[0] is True
    assert [p.plugin_id for p in env.loader.load_all()] == ["cycle"]

    ok, _msg = env.loader.uninstall("cycle")

    assert ok
    assert not (env.plugins_dir / "cycle").exists()
    assert pkg.is_file()                                  # ★ 商店源包仍在
    assert env.registry.all_actions() == []
    entry = env.loader.scan_store()[0]
    assert entry.installed is False                       # 卡片回落「未安装」

    # 再装一次仍然可用（模块缓存已被 uninstall 清掉，能真正重新导入）
    assert env.loader.install_from_store("cycle")[0] is True
    assert [p.plugin_id for p in env.loader.load_all()] == ["cycle"]


# ====================================================================
# I. 禁用 / 启用（模块不重导入）
# ====================================================================
def test_deactivate_activate_without_reimport(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]),
                 make_code("demo", ["demo.ping"]))
    env.loader.load_all()
    module_before = module_of("demo")
    assert module_before is not None

    assert env.loader.deactivate() == 1
    assert env.registry.all_actions() == []

    env.loader.activate()
    assert module_of("demo") is module_before          # 未重新导入
    assert [a.id for a in env.registry.all_actions()] == ["demo.ping"]


def test_load_all_is_idempotent(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]),
                 make_code("demo", ["demo.ping"]))
    env.loader.load_all()
    env.loader.load_all()
    env.loader.load_all()
    assert [a.id for a in env.registry.all_actions()] == ["demo.ping"]
    assert len(env.loader.loaded_plugins()) == 1


# ====================================================================
# 其他边界
# ====================================================================
def test_undeclared_action_skipped(env):
    """create_actions 产出的动作必须在 manifest.actions 里声明，否则跳过"""
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.declared")]),
                 make_code("demo", ["demo.declared", "demo.ghost"]))
    env.loader.load_all()

    assert env.registry.get("demo.declared") is not None
    assert env.registry.get("demo.ghost") is None
    assert any("未在 manifest.actions 里声明" in m for m in env.warnings())


def test_non_ballaction_return_ignored(env):
    code = (
        "from src.plugin_api import BallPlugin\n"
        "\n"
        "class TestPlugin(BallPlugin):\n"
        '    id = "demo"\n'
        '    name = "测试插件"\n'
        '    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        '        return ["我不是动作"]\n'
    )
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]), code)
    env.loader.load_all()
    assert env.registry.all_actions() == []
    assert any("非 BallAction" in m for m in env.warnings())


def test_create_actions_wrong_type_skipped(env):
    code = (
        "from src.plugin_api import BallPlugin\n"
        "\n"
        "class TestPlugin(BallPlugin):\n"
        '    id = "demo"\n'
        '    name = "测试插件"\n'
        '    version = "1.0.0"\n'
        "\n"
        "    def create_actions(self, ctx):\n"
        '        return "不是列表"\n'
    )
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.ping")]), code)
    env.loader.load_all()
    assert any("返回值不是列表" in m for m in env.warnings())


def test_validate_manifest_unit():
    ok, err = validate_manifest({"id": "a", "name": "n", "version": "1",
                                 "entry": "plugin.py"})
    assert ok is not None and err == ""
    assert ok["requires"] == [] and ok["actions"] == []

    for bad in (None, [], {},
                {"id": "a", "name": "n", "version": "1"},
                {"id": "../x", "name": "n", "version": "1", "entry": "p.py"},
                {"id": "a", "name": "n", "version": "1", "entry": "p.py",
                 "requires": "os"},
                {"id": "a", "name": "n", "version": "1", "entry": "p.py",
                 "actions": [{"id": "x"}]}):
        got, why = validate_manifest(bad)
        assert got is None and why


def test_context_config_is_readonly_snapshot(env):
    ctx = env.ctx
    assert dict(ctx.config)["theme"] == "dark"
    hostile = PluginContext(logger=env.logger, config={"theme": "dark"},
                            show_toast=None)
    assert hostile.show_toast("x") is False           # 能力缺失安全降级
    assert hostile.open_card_mode("task") is False


def test_registry_logger_none_is_silent():
    """注册表/上下文允许 logger=None（无 GUI 环境下静默不崩）"""
    reg = ActionRegistry(logger=None)
    ctx = PluginContext(logger=None)

    class A(BallAction):
        id = "a"
        title = "a"

        def run(self, ctx):
            raise RuntimeError("x")

    assert reg.register(A()) is True
    assert reg.trigger("a", ctx) is False
    assert reg.register(A()) is False                  # 重复 id


def test_ballaction_enabled_toggle():
    class A(BallAction):
        id = "a"
        title = "a"
        menu = True

        def run(self, ctx):
            pass

    act = A()
    assert act.enabled() is True
    act.set_enabled(False)
    assert act.enabled() is False
    act.set_enabled(True)
    assert act.enabled() is True
    assert isinstance(A(), BallPlugin) is False        # 类型层次正确


# ====================================================================
# K. 只读数据门面 PluginData
# ====================================================================
def _mk_logger(sink):
    logger = logging.getLogger(f"fp_plug_t_{uuid.uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(_Capture(sink))
    return logger


def test_plugin_data_named_accessors_and_sources():
    data = PluginData(providers={
        "tasks": lambda: [{"task_id": 1}],
        "fragments": lambda: [{"fragment_id": 1}],
        "notes": lambda: [{"note_id": 1}],
        "pomodoro": lambda: {"state": "idle"},
    })
    assert data.sources() == ("fragments", "notes", "pomodoro", "tasks")
    assert data.has("tasks") is True and data.has("users") is False
    assert data.tasks() == [{"task_id": 1}]
    assert data.fragments() == [{"fragment_id": 1}]
    assert data.notes() == [{"note_id": 1}]
    assert data.pomodoro() == {"state": "idle"}
    # 数据源存在但返回 None → 仍然安全降级为空容器
    assert PluginData(providers={"tasks": lambda: None}).tasks() == []


def test_plugin_data_empty_is_safe():
    data = PluginData(logger=None)
    assert data.sources() == ()
    assert data.fetch("tasks") is None
    assert data.tasks() == [] and data.fragments() == []
    assert data.notes() == [] and data.pomodoro() == {}


def test_plugin_data_missing_source_warns():
    sink = []
    data = PluginData(logger=_mk_logger(sink), providers={"tasks": lambda: []})
    assert data.fragments() == []
    assert any("数据源不可用" in r.getMessage() for r in sink)


def test_plugin_data_provider_exception_isolated():
    sink = []

    def boom():
        raise RuntimeError("provider 炸了")

    data = PluginData(logger=_mk_logger(sink), providers={"tasks": boom})
    assert data.tasks() == []                       # 不把异常抛给插件
    assert any("数据源读取失败" in r.getMessage() for r in sink)


def test_plugin_data_returns_deep_copy():
    """插件拿到的是副本：改它碰不到宿主数据（只读语义是真的，不是口号）"""
    source = {"tasks": [{"task_id": 1, "title": "原值", "tags": ["a"]}]}
    data = PluginData(providers={"tasks": lambda: source["tasks"]})
    got = data.tasks()
    got[0]["title"] = "被插件改了"
    got[0]["tags"].append("b")
    assert source["tasks"][0]["title"] == "原值"
    assert source["tasks"][0]["tags"] == ["a"]


def test_plugin_data_ignores_non_callable_provider():
    data = PluginData(providers={"tasks": [1, 2], "ok": lambda: 1})
    assert data.sources() == ("ok",)


# ====================================================================
# L. 插件身份与私有目录
# ====================================================================
def test_for_plugin_shares_capabilities_binds_identity(tmp_path):
    data = PluginData(providers={"tasks": lambda: []})
    base = PluginContext(logger=None, config={"theme": "light"}, data=data,
                         data_dir_base=str(tmp_path / "plugdata"))
    assert base.plugin_id == "" and base.data_dir == ""   # 共享上下文无身份

    child = base.for_plugin("demo", "/somewhere/demo")
    assert child.plugin_id == "demo"
    assert child.plugin_dir == "/somewhere/demo"
    assert dict(child.config)["theme"] == "light"         # 能力共享
    assert child.data is base.data                        # 同一份数据门面
    created = child.data_dir
    assert created and os.path.isdir(created)
    assert os.path.basename(created) == "demo"


def test_data_dir_rejects_unsafe_or_unconfigured(tmp_path):
    base = PluginContext(logger=None, data_dir_base=str(tmp_path))
    assert base.for_plugin("../evil").data_dir == ""
    assert base.for_plugin("").data_dir == ""
    assert PluginContext(logger=None).for_plugin("demo").data_dir == ""


def test_parent_window_safe_degradation():
    assert PluginContext(logger=None).parent_window() is None
    marker = object()
    ctx = PluginContext(logger=None, parent_window=lambda: marker)
    assert ctx.parent_window() is marker
    assert ctx.for_plugin("d").parent_window() is marker   # 派生后仍共享

    def boom():
        raise RuntimeError("x")

    assert PluginContext(logger=None, parent_window=boom).parent_window() is None


def test_is_safe_plugin_id_unit():
    for ok in ("a", "A1", "abc-1.2_x", "x" * 64):
        assert is_safe_plugin_id(ok) is True, ok
    for bad in ("", None, 1, ".", "..", "a..b", ".x", "-x", "a/b", "a\\b",
                "x" * 65):
        assert is_safe_plugin_id(bad) is False, bad


# ====================================================================
# M. 热键格式校验
# ====================================================================
@pytest.mark.parametrize("text", [
    "Ctrl+Alt+W", "ctrl+w", "Alt+Shift+F5", "Win+Ctrl+Q",
    "Control+1", "Ctrl+Alt+F24", "shift+alt+Z",
])
def test_is_valid_hotkey_accepts(text):
    assert is_valid_hotkey(text) is True, text


@pytest.mark.parametrize("text", [
    None, 1, "", "K", "Ctrl", "Ctrl+Alt", "Ctrl+K+Alt", "Ctrl+Alt+",
    "Ctrl+F99", "Ctrl+F0", "Ctrl+Alt+KK",
])
def test_is_valid_hotkey_rejects(text):
    assert is_valid_hotkey(text) is False, text


def test_loader_invalid_hotkey_drops_hotkey_keeps_action(env):
    """格式非法 → 只丢热键，动作仍在菜单（与被占用时整动作让位是两种处置）"""
    write_plugin(env.plugins_dir, "badkey",
                 manifest_of("badkey", [action_spec("badkey.go", hotkey="K")]),
                 make_code("badkey", ["badkey.go"]))
    env.loader.load_all()
    act = env.registry.get("badkey.go")
    assert act is not None
    assert act.hotkey is None
    assert [a.id for a in env.registry.menu_actions()] == ["badkey.go"]
    assert not env.registry.hotkey_actions()
    assert any("热键格式非法" in m for m in env.warnings())


def test_loader_valid_hotkey_kept(env):
    write_plugin(env.plugins_dir, "goodkey",
                 manifest_of("goodkey", [action_spec("goodkey.go",
                                                     hotkey="Ctrl+Alt+W")]),
                 make_code("goodkey", ["goodkey.go"]))
    env.loader.load_all()
    act = env.registry.get("goodkey.go")
    assert act.hotkey == "Ctrl+Alt+W"
    assert [a.id for a in env.registry.hotkey_actions()] == ["goodkey.go"]


# ====================================================================
# N. 注册表记住每个动作所属插件的上下文
# ====================================================================
def test_registry_remembers_per_action_ctx():
    seen = []
    reg = ActionRegistry(logger=None)
    own_ctx = PluginContext(logger=None, plugin_id="plug-a")

    class A(BallAction):
        id = "plug-a.go"
        title = "go"

        def run(self, ctx):
            seen.append(getattr(ctx, "plugin_id", None))

    assert reg.register(A(), "plug-a", own_ctx) is True
    other = PluginContext(logger=None, plugin_id="plug-b")
    assert reg.trigger("plug-a.go", other) is True
    assert seen == ["plug-a"]            # 用插件自己的 ctx，不用调用方传的
    assert reg.context_of("plug-a.go") is own_ctx
    assert reg.unregister("plug-a") == 1
    assert reg.context_of("plug-a.go") is None


def test_trigger_falls_back_to_passed_ctx():
    """注册时没给 ctx（兼容旧调用）→ 仍用调用方传入的共享上下文"""
    seen = []
    reg = ActionRegistry(logger=None)

    class A(BallAction):
        id = "a"
        title = "a"

        def run(self, ctx):
            seen.append(ctx)

    reg.register(A(), "")
    shared = PluginContext(logger=None)
    assert reg.trigger("a", shared) is True
    assert seen == [shared]
    assert reg.clear() == 1
    assert reg.trigger("a", shared) is False


def test_loader_passes_derived_ctx_to_plugin(env):
    """create_actions 与 run 拿到的是同一个「绑定了插件身份」的上下文"""
    code = (
        "# -*- coding: utf-8 -*-\n"
        "from src.plugin_api import BallAction, BallPlugin\n"
        "\n"
        "SEEN = []\n"
        "\n"
        "class Act(BallAction):\n"
        '    id = "derived.go"\n'
        '    title = "go"\n'
        "    def run(self, ctx):\n"
        "        SEEN.append(('run', ctx.plugin_id, ctx.plugin_dir))\n"
        "\n"
        "class TestPlugin(BallPlugin):\n"
        '    id = "derived"\n'
        "    def create_actions(self, ctx):\n"
        "        SEEN.append(('create', ctx.plugin_id, ctx.plugin_dir))\n"
        "        return [Act()]\n"
    )
    write_plugin(env.plugins_dir, "derived",
                 manifest_of("derived", [action_spec("derived.go")]), code)
    env.loader.load_all()
    mod = module_of("derived")
    assert mod.SEEN == [("create", "derived", str(env.plugins_dir / "derived"))]
    env.registry.trigger("derived.go")
    assert mod.SEEN[-1] == ("run", "derived", str(env.plugins_dir / "derived"))


def test_context_logger_never_none():
    """宿主没给日志器时也退化为 NullHandler：插件可以无条件调 ctx.logger.*"""
    ctx = PluginContext(logger=None)
    assert ctx.logger is not None
    ctx.logger.info("不能炸")                     # 关键：非 None 才敢直接调
    ctx.logger.warning("也不能炸")
    assert ctx.for_plugin("d").logger is not None


def test_plugin_using_ctx_logger_survives_null_logger():
    """曾把进程干崩的真实场景：logger=None + 插件 run() 里第一行就写日志。

    AttributeError 发生在 Qt 槽里会被解释器升级成 qFatal（0xC0000409），
    所以这里必须断言 trigger 返回 True，而不是「没抛异常」。
    """
    reg = ActionRegistry(logger=None)
    ctx = PluginContext(logger=None)
    seen = []

    class A(BallAction):
        id = "a"
        title = "a"

        def run(self, ctx):
            ctx.logger.info("插件日志")
            seen.append(True)

    assert reg.register(A(), "") is True
    assert reg.trigger("a", ctx) is True
    assert seen == [True]


# ====================================================================
# L. 按插件 id 统一启停（plugins_disabled 配置回置的落点）
# ====================================================================
def test_loader_set_plugin_enabled_roundtrip(env):
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.a"), action_spec("demo.b")]),
                 make_code("demo", ["demo.a", "demo.b"]))
    env.loader.load_all()
    assert len(env.registry.all_actions()) == 2

    assert env.loader.set_plugin_enabled("demo", False) == 2
    assert [a.enabled() for a in env.registry.all_actions()] == [False, False]
    # 停用的动作不进菜单、不绑热键
    assert env.registry.menu_actions() == []
    assert env.loader.disabled_plugin_ids() == ["demo"]

    assert env.loader.set_plugin_enabled("demo", True) == 2
    assert [a.enabled() for a in env.registry.all_actions()] == [True, True]
    assert env.loader.disabled_plugin_ids() == []


def test_loader_set_plugin_enabled_unknown_id_is_noop(env):
    """配置里残留已删除插件的 id 是正常情况，不该报错也不该影响别的插件"""
    write_plugin(env.plugins_dir, "demo",
                 manifest_of("demo", [action_spec("demo.a")]),
                 make_code("demo", ["demo.a"]))
    env.loader.load_all()
    assert env.loader.set_plugin_enabled("ghost", False) == 0
    assert env.registry.get("demo.a").enabled() is True
    assert env.loader.disabled_plugin_ids() == []


def test_loader_set_plugin_enabled_before_load_is_safe(env):
    """还没 load_all（插件未登记）时调用 → 返回 0，不抛异常"""
    assert env.loader.set_plugin_enabled("demo", False) == 0


def test_loader_set_plugin_enabled_isolates_single_plugin(env):
    """停用 A 插件不能顺带把 B 插件也关掉"""
    write_plugin(env.plugins_dir, "alpha",
                 manifest_of("alpha", [action_spec("alpha.a")]),
                 make_code("alpha", ["alpha.a"]))
    write_plugin(env.plugins_dir, "beta",
                 manifest_of("beta", [action_spec("beta.b")]),
                 make_code("beta", ["beta.b"]))
    env.loader.load_all()
    env.loader.set_plugin_enabled("alpha", False)
    assert env.registry.get("alpha.a").enabled() is False
    assert env.registry.get("beta.b").enabled() is True
    assert env.loader.disabled_plugin_ids() == ["alpha"]
