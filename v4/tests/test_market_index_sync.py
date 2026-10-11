# -*- coding: utf-8 -*-
"""插件市场索引再生与确定性打包回归（2026-10-10 根因修复）。

背景（在线市场「sha256 不符，已丢弃」根因）：
  index.json 此前手工钉死，插件源码演进后 Release 附件哈希 ≠ 索引
  哈希，市场下载全部被完整性校验丢弃。修复两件套：
  1. pack_plugin 确定性打包（成员 mtime 固定 + compresslevel=9）
     ⇒ 同源码重打包逐字节相同，索引哈希才守得住；
  2. tools/sync_market_index.py 从源码自动再生索引 + --check 脱同步红灯。

钉住的行为（纯 stdlib，无 GUI 可跑）：
  1. 确定性：同一插件目录打两次 → 包字节与 sha256 完全一致
  2. zip 成员时间戳固定为 1980-01-01（不随文件 mtime 漂移）
  3. sync：再生条目含 id/version/file/size/sha256 且哈希 == 实际包
  4. --check：一致 → 0；篡改 committed 哈希 → 1 且原因指出字段
  5. index_matches 忽略 updated 日期（CI 不红在日期漂移上）
"""
import importlib.util
import json
import os
import sys
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(os.path.dirname(BASE), "tools")
for p in (BASE, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

from pack_plugin import pack_plugin, _ZIP_FIXED_DATE  # noqa: E402
import sync_market_index as smi                       # noqa: E402


def _load_tool_module():
    """按路径加载 sync_market_index（tools 不是包，直接 spec 加载）"""
    spec = importlib.util.spec_from_file_location(
        "sync_market_index_tool", os.path.join(TOOLS, "sync_market_index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ====================================================================
# 1. 确定性打包
# ====================================================================
class TestDeterministicPacking:
    def test_same_source_same_bytes(self, tmp_path):
        """同一插件目录打两次 → sha256 逐字节一致"""
        src = os.path.join(TOOLS, "..", "plugins", "kb-search")
        ok1, _, info1 = pack_plugin(src, str(tmp_path / "a"), force=True)
        ok2, _, info2 = pack_plugin(src, str(tmp_path / "b"), force=True)
        assert ok1 and ok2
        h1 = info1["package"]
        h2 = info2["package"]
        with open(h1, "rb") as f:
            d1 = f.read()
        with open(h2, "rb") as f:
            d2 = f.read()
        assert d1 == d2

    def test_fixed_member_timestamp(self, tmp_path):
        """zip 成员时间戳固定 1980-01-01（不随源文件 mtime 漂移）"""
        src = os.path.join(TOOLS, "..", "plugins", "kb-search")
        ok, _, info = pack_plugin(src, str(tmp_path), force=True)
        assert ok
        with zipfile.ZipFile(info["package"]) as zf:
            for zi in zf.infolist():
                assert zi.date_time == _ZIP_FIXED_DATE

    def test_package_still_loads(self, tmp_path):
        """确定性收口不破坏包结构：manifest 可解析、id 匹配"""
        src = os.path.join(TOOLS, "..", "plugins", "kb-search")
        ok, _, info = pack_plugin(src, str(tmp_path), force=True)
        assert ok
        with zipfile.ZipFile(info["package"]) as zf:
            data = json.loads(zf.read("kb-search/manifest.json")
                              .decode("utf-8"))
        assert data["id"] == "kb-search"

    def test_source_bytes_normalized(self):
        """打包字节走 git index blob：工作树行尾（历史 CRLF）不影响产物。

        2026-10-11 实锤：plugin.py 在 index 里是 LF、工作树还是 CRLF，
        打包器若读工作区字节，同一提交在开发机与 CI 打出两种哈希，
        索引必然脱同步。收口后 source bytes == index blob 字节。
        """
        import subprocess
        rel = "plugins/kb-search/plugin.py"
        raw = open(os.path.join(BASE, "..", rel), "rb").read()
        blob = subprocess.run(
            ["git", "cat-file", "blob", f":{rel}"], capture_output=True,
            cwd=os.path.join(BASE, ".."))
        assert blob.returncode == 0
        packed = __import__("pack_plugin")._source_bytes(
            os.path.join(BASE, "..", "plugins", "kb-search"), "plugin.py")
        assert packed == blob.stdout          # 与 git 规范化内容一致
        # 若工作树与 index 行尾不同，显式证明走的是 index 路径
        if raw != blob.stdout:
            assert packed != raw              # 不被工作区行尾污染


# ====================================================================
# 2. 索引再生与校验
# ====================================================================
class TestSyncIndex:
    def test_build_entries_from_source(self, tmp_path, monkeypatch):
        """再生条目字段齐备，sha256 == 实际打出的包"""
        mod = _load_tool_module()
        monkeypatch.setattr(mod, "PLUGINS_DIR",
                            os.path.join(TOOLS, "..", "plugins"))
        monkeypatch.setattr(mod, "STORE_DIR", str(tmp_path))
        entries, problems = mod.build_entries()
        assert entries and not problems
        by_id = {e["id"]: e for e in entries}
        assert "kb-search" in by_id
        e = by_id["kb-search"]
        assert e["file"] == "kb-search.fpplug"
        assert e["size"] > 0
        pkg = os.path.join(str(tmp_path), "kb-search.fpplug")
        import hashlib
        with open(pkg, "rb") as f:
            assert e["sha256"] == hashlib.sha256(f.read()).hexdigest()

    def test_index_matches_ignores_updated(self, tmp_path, monkeypatch):
        """比对忽略 updated 日期：只比 plugins 数组"""
        entries = [{"id": "x", "version": "1.0.0", "file": "x.fpplug",
                    "size": 10, "sha256": "a" * 64}]
        committed = {"schema": 1, "updated": "2020-01-01",
                     "plugins": entries}
        ok, reasons = smi.index_matches(entries, committed)
        assert ok and reasons == []

    def test_index_mismatch_reports_fields(self):
        """篡改哈希/版本 → 不一致且原因逐字段点名"""
        entries = [{"id": "x", "version": "2.0.0", "file": "x.fpplug",
                    "size": 10, "sha256": "a" * 64}]
        committed = {"plugins": [{
            "id": "x", "version": "1.0.0", "file": "x.fpplug",
            "size": 10, "sha256": "b" * 64}]}
        ok, reasons = smi.index_matches(entries, committed)
        assert not ok
        assert any("version" in r for r in reasons)
        assert any("sha256" in r for r in reasons)

    def test_committed_index_current(self):
        """仓库内已提交索引必须与当前源码一致（本仓库自身的红灯）"""
        entries, problems = smi.build_entries()
        # build_entries 会把包打进真实 plugin_store（本仓流程如此），
        # 这里只验证索引比对，不动 STORE_DIR 的产物
        committed = smi.load_committed_index()
        ok, reasons = smi.index_matches(entries, committed)
        assert ok, f"marketplace/index.json 已脱同步：{reasons}"
