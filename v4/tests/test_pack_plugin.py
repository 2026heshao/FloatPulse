# -*- coding: utf-8 -*-
"""插件打包器 tools/pack_plugin.py 回归（纯逻辑，无 GUI，无头可跑）。

为什么值得单测：打包器是插件**分发链路的入口**——它放过一个坏包，
用户会在插件中心看到「加载失败」而不是在打包时就被告知；它多打一个
``__pycache__``，包里就多几十 KB 垃圾。所以这里既钉校验规则，
也钉「包能被真实 PluginLoader 认出来并装进去」这条端到端契约。

  A check_plugin：manifest / 依赖白名单 / 热键 / action id / entry 存在性
  B collect_files：排除缓存、隐藏文件、已有包文件
  C pack_plugin：结构（套一层 <id>/）、覆盖策略、--check-only、自校验
  D 端到端：真 loader scan_store 认包 → install_from_store 装上 → 可加载
  E main() 退出码：0 = 成功 / 1 = 失败
"""

import importlib.util
import json
import os
import sys
import zipfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PACK_PATH = os.path.join(ROOT, "tools", "pack_plugin.py")


@pytest.fixture(scope="module")
def pack():
    """直载 tools/pack_plugin.py（它的 ROOT/V4_DIR 推断在 import 期执行）"""
    spec = importlib.util.spec_from_file_location("fp_test_pack_plugin", PACK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_plugin(tmp_path, name="demo", **over):
    """构造一个最小合法插件目录，over 里的键覆盖 manifest 字段"""
    pdir = tmp_path / name
    pdir.mkdir(exist_ok=True)
    manifest = {
        "id": name,
        "name": "演示插件",
        "version": "1.0.0",
        "entry": "plugin.py",
        "requires": ["PyQt6"],
        "description": "打包器测试用",
        "actions": [{"id": f"{name}.go", "title": "做件事",
                     "hotkey": "Ctrl+Alt+D", "menu": True}],
    }
    manifest.update(over)
    (pdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pdir / "plugin.py").write_text(
        "from src.plugin_api import BallPlugin, BallAction\n"
        f"class A(BallAction):\n    id = \"{manifest['actions'][0]['id']}\"\n"
        f"    title = \"{manifest['actions'][0]['title']}\"\n"
        "    def run(self, ctx): pass\n"
        "class P(BallPlugin):\n    def create_actions(self, ctx): return [A()]\n",
        encoding="utf-8")
    (pdir / "使用说明.md").write_text("# 演示\n\n这是个测试插件。\n",
                                      encoding="utf-8")
    return pdir


# ---------------------------------------------------------------- A 校验
class TestCheckPlugin:
    def test_valid_plugin_passes(self, pack, tmp_path):
        manifest, errors = pack.check_plugin(str(_make_plugin(tmp_path)))
        assert errors == []
        assert manifest["id"] == "demo"

    def test_denied_requires_rejected(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path, requires=["PyQt6", "requests"])
        _, errors = pack.check_plugin(str(pdir))
        assert any("白名单" in e and "requests" in e for e in errors)
        # 提示里要给出替代路径，否则用户只知道「不行」
        assert any("http_post_json_async" in e for e in errors)

    def test_bad_hotkey_rejected(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path, actions=[
            {"id": "demo.go", "title": "做件事", "hotkey": "Ctrl", "menu": True}])
        _, errors = pack.check_plugin(str(pdir))
        assert any("热键格式非法" in e for e in errors)

    def test_duplicate_action_id_rejected(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path, actions=[
            {"id": "demo.go", "title": "A", "menu": True},
            {"id": "demo.go", "title": "B", "menu": True}])
        _, errors = pack.check_plugin(str(pdir))
        assert any("重复" in e for e in errors)

    def test_missing_entry_rejected(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path, entry="not_there.py")
        _, errors = pack.check_plugin(str(pdir))
        assert any("entry 指向的文件不存在" in e for e in errors)

    def test_missing_manifest_rejected(self, pack, tmp_path):
        pdir = tmp_path / "empty"
        pdir.mkdir()
        manifest, errors = pack.check_plugin(str(pdir))
        assert manifest is None
        assert any("没有 manifest.json" in e for e in errors)

    def test_broken_json_rejected(self, pack, tmp_path):
        pdir = tmp_path / "broken"
        pdir.mkdir()
        (pdir / "manifest.json").write_text("{ not json", encoding="utf-8")
        _, errors = pack.check_plugin(str(pdir))
        assert any("合法 JSON" in e for e in errors)

    def test_non_utf8_manifest_gives_encoding_hint(self, pack, tmp_path):
        pdir = tmp_path / "gbk"
        pdir.mkdir()
        (pdir / "manifest.json").write_bytes(
            '{"id": "gbk", "name": "中文名"}'.encode("gbk"))
        _, errors = pack.check_plugin(str(pdir))
        assert any("UTF-8" in e and "GBK" in e for e in errors)


# ------------------------------------------------------- B 文件收集
class TestCollectFiles:
    def test_excludes_cache_and_hidden(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        cache = pdir / "__pycache__"
        cache.mkdir()
        (cache / "plugin.cpython-312.pyc").write_bytes(b"\x00\x01")
        (pdir / ".DS_Store").write_bytes(b"\x00")
        (pdir / "debug.log").write_text("noise", encoding="utf-8")
        (pdir / "old.fpplug").write_bytes(b"PK\x03\x04")
        files = pack.collect_files(str(pdir))
        assert files == ["manifest.json", "plugin.py", "使用说明.md"]

    def test_includes_subdirs(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        sub = pdir / "assets"
        sub.mkdir()
        (sub / "icon.png").write_bytes(b"\x89PNG")
        assert "assets/icon.png" in pack.collect_files(str(pdir))


# ------------------------------------------------------- C 打包
class TestPackPlugin:
    def test_pack_creates_prefixed_structure(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        out = tmp_path / "store"
        ok, msg, info = pack.pack_plugin(str(pdir), str(out))
        assert ok, msg
        assert info["files"] == 3
        pkg = out / "demo.fpplug"
        assert pkg.exists()
        with zipfile.ZipFile(pkg) as zf:
            names = zf.namelist()
        # 与内置包一致：套一层 <id>/ 前缀
        assert "demo/manifest.json" in names
        assert "demo/plugin.py" in names
        assert "demo/使用说明.md" in names
        assert not any(n.startswith("__pycache__") for n in names)

    def test_existing_package_rejected_without_force(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        out = tmp_path / "store"
        ok1, _, _ = pack.pack_plugin(str(pdir), str(out))
        assert ok1
        original = (out / "demo.fpplug").read_bytes()
        ok2, msg, _ = pack.pack_plugin(str(pdir), str(out))
        assert not ok2
        assert "--force" in msg
        assert (out / "demo.fpplug").read_bytes() == original   # 原包未被破坏

    def test_force_overwrites(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        out = tmp_path / "store"
        pack.pack_plugin(str(pdir), str(out))
        (pdir / "extra.txt").write_text("more", encoding="utf-8")
        ok, msg, info = pack.pack_plugin(str(pdir), str(out), force=True)
        assert ok, msg
        assert info["files"] == 4

    def test_check_only_writes_nothing(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        out = tmp_path / "store"
        ok, msg, info = pack.pack_plugin(str(pdir), str(out), check_only=True)
        assert ok and info["check_only"] is True
        assert not (out / "demo.fpplug").exists()

    def test_invalid_plugin_never_writes_package(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path, requires=["aiohttp"])
        out = tmp_path / "store"
        ok, msg, info = pack.pack_plugin(str(pdir), str(out))
        assert not ok and info["errors"]
        assert not (out / "demo.fpplug").exists()

    def test_missing_dir_fails_cleanly(self, pack, tmp_path):
        ok, msg, _ = pack.pack_plugin(str(tmp_path / "nope"), str(tmp_path / "o"))
        assert not ok and "不存在" in msg


# ------------------------------------------------------- D 端到端
class TestEndToEndWithRealLoader:
    def test_packed_bundle_installable_by_loader(self, pack, tmp_path):
        """打完的包必须能被真 loader 认出并装上——这是分发的底线契约"""
        from src.plugin_api import ActionRegistry
        from src.plugin_loader import PluginLoader

        pdir = _make_plugin(tmp_path)
        store = tmp_path / "store"
        plugins_dir = tmp_path / "plugins"
        ok, msg, _ = pack.pack_plugin(str(pdir), str(store))
        assert ok, msg

        loader = PluginLoader(ActionRegistry(), ctx=None,
                              plugins_dir=str(plugins_dir),
                              store_dir=str(store))
        # 商店能列出这个包，且状态为「未安装」
        entries = [e for e in loader.scan_store() if e.plugin_id == "demo"]
        assert len(entries) == 1
        assert entries[0].usable and not entries[0].installed

        # 安装 → 目录生成 → 能加载出插件
        ok_inst, msg_inst = loader.install_from_store("demo")
        assert ok_inst, msg_inst
        assert (plugins_dir / "demo" / "manifest.json").is_file()
        loader.rescan()
        assert [lp.plugin_id for lp in loader.loaded_plugins()] == ["demo"]

        # 装完再扫商店：该包应变「已安装」（面板据此不再重复列出）
        entries2 = [e for e in loader.scan_store() if e.plugin_id == "demo"]
        assert entries2 and entries2[0].installed


# ------------------------------------------------------- E CLI
class TestMainExitCodes:
    def test_main_ok_returns_zero(self, pack, tmp_path, capsys):
        pdir = _make_plugin(tmp_path)
        rc = pack.main([str(pdir), "-o", str(tmp_path / "store")])
        assert rc == 0
        assert "已打包" in capsys.readouterr().out

    def test_main_invalid_returns_one(self, pack, tmp_path, capsys):
        pdir = _make_plugin(tmp_path, requires=["requests"])
        rc = pack.main([str(pdir), "-o", str(tmp_path / "store")])
        assert rc == 1
        assert "校验未通过" in capsys.readouterr().out

    def test_main_check_only_returns_zero(self, pack, tmp_path):
        pdir = _make_plugin(tmp_path)
        rc = pack.main([str(pdir), "--check-only", "-o", str(tmp_path / "s")])
        assert rc == 0
