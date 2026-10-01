# -*- coding: utf-8 -*-
"""plugin_market（应用内插件市场纯逻辑）回归钉子。

覆盖：索引用头 / 索引解析（合法 / 致命坏 / 逐条坏都只丢那条）/ 已装
状态分流（pending / updatable / 宁漏报不误报）/ Release 附件按文件名
解析 / 二进制下载（本地 HTTPServer 实测 200/404/拒连/超限/SSRF 闸）/
sha256 校验 / 落盘（原子替换 / 路径穿越拒绝）。

无 GUI 依赖：pytest 直接跑。
"""

import hashlib
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# v4/ 根目录按本文件位置推导（勿写死绝对路径：换机器/改目录名即失效）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import plugin_market as pm          # noqa: E402
from src import net_guard                    # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------- 索引解析 ----------------
def _item(**over):
    base = {
        "id": "kb-search", "name": "站内搜索", "version": "1.0.0",
        "file": "kb-search.fpplug", "size": 1024,
        "sha256": "a" * 64, "description": "全库搜索",
        "capabilities": [], "hotkeys": [],
    }
    base.update(over)
    return base


def test_parse_index_ok():
    items, problems = pm.parse_index(json.dumps({"schema": 1, "plugins": [_item()]}))
    assert problems == []
    assert len(items) == 1
    it = items[0]
    assert it["id"] == "kb-search" and it["file"] == "kb-search.fpplug"
    assert it["sha256"] == "a" * 64 and it["size"] == 1024
    assert it["capabilities"] == [] and it["hotkeys"] == []


def test_parse_index_empty_body():
    items, problems = pm.parse_index("")
    assert items == [] and problems


def test_parse_index_bad_json():
    items, problems = pm.parse_index("not json {")
    assert items == [] and len(problems) == 1


def test_parse_index_top_not_dict():
    items, problems = pm.parse_index("[]")
    assert items == [] and problems


def test_parse_index_no_plugins_key():
    items, problems = pm.parse_index('{"schema": 1}')
    assert items == [] and "plugins" in problems[0]


def test_parse_index_drops_bad_items_individually():
    good = _item()
    bad_id = _item(id="../evil", file="evil.fpplug")
    bad_file = _item(id="ok-plugin", file="a/b.fpplug")
    bad_sha = _item(id="ok2", file="ok2.fpplug", sha256="xyz")
    bad_size = _item(id="ok3", file="ok3.fpplug", size=0)
    not_dict = "junk"
    items, problems = pm.parse_index(json.dumps(
        {"plugins": [good, bad_id, bad_file, bad_sha, bad_size, not_dict]}))
    assert [i["id"] for i in items] == ["kb-search"]
    assert len(problems) == 5


def test_parse_index_caps_and_hotkeys_sanitized():
    it, _ = pm.parse_index(json.dumps({"plugins": [
        _item(capabilities=["network", 42, None], hotkeys=["Ctrl+K", {}])]}))
    assert it[0]["capabilities"] == ["network"]
    assert it[0]["hotkeys"] == ["Ctrl+K"]


def test_parse_index_empty_plugins_list():
    items, problems = pm.parse_index('{"plugins": []}')
    assert items == [] and problems


# ---------------- 已装状态分流 ----------------
def test_installed_state_pending_and_updatable():
    items = [_item(id="a", version="1.1.0", file="a.fpplug"),
             _item(id="b", version="2.0.0", file="b.fpplug"),
             _item(id="c", version="0.9.0", file="c.fpplug")]
    pending, updatable = pm.installed_state(
        items, {"b": "1.5.0", "c": "1.0.0"})
    assert [i["id"] for i in pending] == ["a"]
    assert [i["id"] for i in updatable] == ["b"]      # c 本地 1.0.0 更新，不报


def test_installed_state_unparsable_version_no_false_positive():
    items = [_item(id="a", version="1.1.0", file="a.fpplug")]
    pending, updatable = pm.installed_state(items, {"a": "dev"})
    assert pending == [] and updatable == []          # 本地版本解析失败 → 漏报


def test_installed_state_equal_version_not_updatable():
    items = [_item(id="a", version="1.0.0", file="a.fpplug")]
    _, updatable = pm.installed_state(items, {"a": "1.0.0"})
    assert updatable == []


# ---------------- Release 附件解析 ----------------
def test_find_asset_id():
    body = json.dumps({"assets": [{"name": "x.zip", "id": 1},
                                  {"name": "kb-search.fpplug", "id": 77}]})
    assert pm.find_asset_id(body, "kb-search.fpplug") == 77


def test_find_asset_id_missing():
    assert pm.find_asset_id('{"assets": []}', "nope.fpplug") == 0
    assert pm.find_asset_id("garbage", "nope.fpplug") == 0
    assert pm.find_asset_id("", "nope.fpplug") == 0


def test_asset_download_url():
    assert pm.asset_download_url(77) == (
        "https://api.github.com/repos/2026heshao/FloatPulse"
        "/releases/assets/77")


def test_headers_have_ua_and_accept():
    assert "User-Agent" in pm.index_headers()
    assert "raw" in pm.index_headers()["Accept"]
    assert pm.download_headers()["Accept"] == "application/octet-stream"


# ---------------- 二进制下载（本地 HTTPServer） ----------------
class _BytesHandler(BaseHTTPRequestHandler):
    payload = b"PK\x03\x04fake-fpplug-bytes"

    def do_GET(self):
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
        elif self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Length", "99999999")
            self.end_headers()
            self.wfile.write(b"x" * (pm.MAX_DOWNLOAD_BYTES + 10))
        else:
            self.send_error(404)

    def log_message(self, *a):      # 静音
        pass


@pytest.fixture()
def server():
    net_guard.set_allow_private_network(True)
    srv = HTTPServer(("127.0.0.1", 0), _BytesHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    net_guard.set_allow_private_network(False)


def test_http_get_bytes_ok(server):
    res = pm.http_get_bytes(server + "/ok", timeout=5)
    assert res["ok"] and res["status"] == 200
    assert res["data"] == b"PK\x03\x04fake-fpplug-bytes"


def test_http_get_bytes_404(server):
    res = pm.http_get_bytes(server + "/missing", timeout=5)
    assert not res["ok"] and res["status"] == 404
    assert "404" in res["error"]


def test_http_get_bytes_too_large(server):
    res = pm.http_get_bytes(server + "/big", timeout=5)
    assert not res["ok"] and "上限" in res["error"]


def test_http_get_bytes_ssrf_guarded():
    res = pm.http_get_bytes("http://localhost/x", timeout=5)
    assert not res["ok"] and res["error"]        # 内网被闸拦，不发请求


def test_http_get_bytes_unreachable():
    res = pm.http_get_bytes("http://127.0.0.1:1/x", timeout=2)
    assert not res["ok"] and res["error"]


# ---------------- sha256 与落盘 ----------------
def test_sha256_ok():
    data = b"hello fpplug"
    good = hashlib.sha256(data).hexdigest()
    assert pm.sha256_ok(data, good)
    assert pm.sha256_ok(data, good.upper())
    assert not pm.sha256_ok(data, "0" * 64)
    assert not pm.sha256_ok(data, "")
    assert not pm.sha256_ok("not bytes", good)


def test_save_to_store_roundtrip(tmp_path):
    data = b"PK\x03\x04content"
    ok, path = pm.save_to_store(data, str(tmp_path), "kb-search.fpplug")
    assert ok and os.path.isfile(path)
    with open(path, "rb") as f:
        assert f.read() == data
    assert not os.path.exists(path + ".market-tmp")   # 原子替换无残留


def test_save_to_store_replaces_old(tmp_path):
    old = tmp_path / "kb-search.fpplug"
    old.write_bytes(b"old-version")
    ok, _ = pm.save_to_store(b"new-version", str(tmp_path), "kb-search.fpplug")
    assert ok and old.read_bytes() == b"new-version"


def test_save_to_store_rejects_traversal(tmp_path):
    ok, why = pm.save_to_store(b"x", str(tmp_path), "sub/dir/x.fpplug")
    assert not ok and "不合法" in why
    ok, why = pm.save_to_store(b"x", "", "x.fpplug")
    assert not ok


def test_save_to_store_rejects_non_fpplug(tmp_path):
    ok, why = pm.save_to_store(b"x", str(tmp_path), "evil.exe")
    assert not ok and "不合法" in why


# ---------------- 真实 fpplug 包结构自证（与本仓插件兼容） ----------------
def test_real_fpplug_is_zip_readable():
    """市场下载的包最终走 loader 安装——用仓库真包自证 zip 路径畅通。"""
    src = os.path.join(REPO_ROOT, "plugin_store", "kb-search.fpplug")
    if not os.path.isfile(src):
        pytest.skip("plugin_store 里没有真包")
    with zipfile.ZipFile(src) as zf:
        assert any(n.endswith("manifest.json") for n in zf.namelist())


# ---------------- 索引用头里的仓库常量一致性 ----------------
def test_urls_come_from_single_source():
    assert "2026heshao/FloatPulse" in pm.INDEX_API_URL
    assert pm.INDEX_API_URL.startswith("https://api.github.com/")
