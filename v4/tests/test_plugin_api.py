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
  H .fpplug（zip）→ 自动解压到 plugins/<id>/ 后加载，原文件保留
  I deactivate/activate → 摘掉/恢复动作，且**不重新导入模块**
  J 全程不 import knowledge_ball（插件层与宿主解耦的硬约束）
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
    ActionRegistry, BallAction, BallPlugin, PluginContext,
    is_allowed_requirement, normalize_hotkey,
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
    loader = PluginLoader(registry, ctx,
                          plugins_dir=str(plugins_dir), logger=logger)
    return SimpleNamespace(
        registry=registry, ctx=ctx, loader=loader, plugins_dir=plugins_dir,
        logger=logger, records=records, tmp=tmp_path,
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
# H. .fpplug 自动解压
# ====================================================================
def test_fpplug_auto_unpack_keeps_original(env):
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    package = env.plugins_dir / "zipped.fpplug"
    manifest = manifest_of("zipped", [action_spec("zipped.pick", hotkey="Ctrl+Alt+Z")])
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("plugin.py", make_code("zipped", ["zipped.pick"]))
        zf.writestr("icon.png", b"\x89PNG\r\n\x1a\n")

    loaded = env.loader.load_all()

    assert [p.plugin_id for p in loaded] == ["zipped"]
    assert (env.plugins_dir / "zipped" / "manifest.json").is_file()
    assert (env.plugins_dir / "zipped" / "icon.png").is_file()
    assert package.is_file()                               # 原包保留
    action = env.registry.get("zipped.pick")
    assert action.icon_path and action.icon_path.endswith("icon.png")


def test_fpplug_with_nested_root_unpacked(env):
    """常见打包失误：zip 里整体套一层目录 → 仍应能解压到 plugins/<id>/"""
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_of("nested", [action_spec("nested.pick")])
    with zipfile.ZipFile(env.plugins_dir / "nested.fpplug", "w") as zf:
        zf.writestr("nested/manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("nested/plugin.py", make_code("nested", ["nested.pick"]))

    assert [p.plugin_id for p in env.loader.load_all()] == ["nested"]
    assert (env.plugins_dir / "nested" / "plugin.py").is_file()


def test_bad_zip_skipped(env):
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    (env.plugins_dir / "junk.fpplug").write_bytes(b"not a zip at all")
    assert env.loader.load_all() == []
    assert any("不是合法的 zip 包" in m for m in env.warnings())


def test_fpplug_zip_slip_member_rejected(env):
    """zip-slip：压缩包内的路径穿越成员必须被剔除，不能写到插件目录之外"""
    env.plugins_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_of("slip", [action_spec("slip.pick")])
    with zipfile.ZipFile(env.plugins_dir / "slip.fpplug", "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("plugin.py", make_code("slip", ["slip.pick"]))
        zf.writestr("../../evil.txt", "pwned")

    env.loader.load_all()

    assert (env.plugins_dir / "slip" / "plugin.py").is_file()
    assert not (env.tmp / "evil.txt").exists()
    assert not (env.tmp.parent / "evil.txt").exists()
    assert any("非法路径成员" in m for m in env.warnings())


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
