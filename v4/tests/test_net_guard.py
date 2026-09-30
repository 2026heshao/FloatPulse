# -*- coding: utf-8 -*-
"""net_guard SSRF 防护闸回归 —— 判定规则 + plugin_net 集成面。

钉死的行为：
  A 纯函数 is_private_ip（表驱动）：回环/私网/链路本地/保留/组播/
    未指定/IPv4-mapped IPv6 全部判私；公网放行；非法字面量 fail-closed
  B 主机名黑名单：localhost / *.localhost / *.local / *.internal
    直接拒（不进解析）
  C guard_url 全流程（monkeypatch socket.getaddrinfo）：解析出多条
    地址任一命中即拒 / 公网放行 / scheme 白名单 / 解析失败拒
  D 开关：set_allow_private_network(True) 放行内网（协议白名单仍生效），
    且每条用例前后都恢复默认拦截
  E plugin_net 集成面：内网 URL 在桥上被拦 —— 返回与既有 _deny 完全
    同构的五键 dict（ok/status/body/error/url），异步路径照常回调并
    走现有审计日志（记 URL 与拒绝原因，不记请求体）
A-D 纯标准库无 Qt；E 部分 import src.plugin_net（含 PyQt6，offscreen 跑）。
"""

import ipaddress
import os
import socket
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src import net_guard   # noqa: E402


@pytest.fixture(autouse=True)
def _reset_private_switch():
    """每条用例前后都恢复默认拦截：不依赖文件执行顺序、不吃开关泄漏。"""
    net_guard.set_allow_private_network(False)
    net_guard.clear_loopback_endpoints()
    yield
    net_guard.set_allow_private_network(False)
    net_guard.clear_loopback_endpoints()


def _patch_resolve(monkeypatch, *ips, error=None, calls=None):
    """把 socket.getaddrinfo 换成假解析器：返回 *ips（或抛 error）。

    calls 传 list 时记录每次被解析的主机名，用于断言「没进解析」。
    """
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if calls is not None:
            calls.append(host)
        if error is not None:
            raise error
        out = []
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            if addr.version == 6:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                            (ip, 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "",
                            (ip, 0)))
        return out

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)


# ---------------- A：is_private_ip 纯函数 ----------------
@pytest.mark.parametrize("ip,expected", [
    # 回环
    ("127.0.0.1", True), ("127.8.8.8", True),
    # 私网三大段
    ("10.1.2.3", True), ("172.16.0.1", True), ("172.31.255.255", True),
    ("192.168.1.1", True),
    ("172.32.0.1", False),                 # 172.16/12 之外
    # 链路本地 / 未指定 / 保留 / 组播
    ("169.254.1.1", True), ("0.0.0.0", True), ("240.0.0.1", True),
    ("255.255.255.255", True), ("224.0.0.1", True),
    # IPv6：回环 / 未指定 / ULA（fc00::/7）/ 链路本地 / 组播
    ("::1", True), ("::", True), ("fc00::1", True), ("fd12::1", True),
    ("fe80::1", True), ("ff02::1", True),
    # IPv4-mapped IPv6：还原成 IPv4 判定
    ("::ffff:127.0.0.1", True), ("::ffff:10.0.0.5", True),
    ("::ffff:169.254.9.9", True), ("::ffff:93.184.216.34", False),
    # 公网放行
    ("93.184.216.34", False), ("8.8.8.8", False), ("1.1.1.1", False),
    ("2606:2800:220:1:248:1893:25c8:1946", False),
    # 非法字面量 fail-closed
    ("", True), ("not-an-ip", True), ("999.1.1.1", True),
])
def test_is_private_ip_table(ip, expected):
    assert net_guard.is_private_ip(ip) is expected


# ---------------- B：主机名黑名单 ----------------
def test_local_hostnames_rejected_without_resolve(monkeypatch):
    calls = []
    _patch_resolve(monkeypatch, "93.184.216.34", calls=calls)
    for host in ("localhost", "LOCALHOST", "foo.localhost", "bar.local",
                 "baz.internal", "localhost."):
        g = net_guard.guard_url(f"http://{host}/x")
        assert not g.ok, host
        assert g.host == host.rstrip(".").lower()
        assert "拦截" in g.reason
    assert calls == [], "黑名单应在解析前直接拒绝"


# ---------------- C：guard_url 全流程 ----------------
@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "192.168.0.10", "172.16.5.5", "169.254.3.3",
    "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0", "::",
])
def test_guard_rejects_resolved_private_ip(monkeypatch, ip):
    _patch_resolve(monkeypatch, ip)
    g = net_guard.guard_url("http://host.example/x")
    assert not g.ok
    assert g.host == "host.example" and g.ip == ip
    assert "拦截" in g.reason


def test_guard_rejects_mixed_resolved(monkeypatch):
    """解析出多条地址：公网 + 内网混合，任一命中即拒（防多记录漏网）"""
    _patch_resolve(monkeypatch, "93.184.216.34", "10.0.0.7")
    g = net_guard.guard_url("http://rebind.example/x")
    assert not g.ok
    assert g.ip == "10.0.0.7" and "10.0.0.7" in g.reason


def test_guard_allows_public(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    g = net_guard.guard_url("http://api.example/v1/x")
    assert g.ok and g.reason == ""
    assert g.host == "api.example" and g.ip == "93.184.216.34"


@pytest.mark.parametrize("url", [
    "ftp://host.example/file",
    "file:///etc/passwd",
    "host.example/no-scheme",
    "http:///no-host",
    "",
])
def test_guard_scheme_and_shape(monkeypatch, url):
    """scheme 白名单 / 缺主机名 / 空 URL：全部拒绝且不进解析"""
    calls = []
    _patch_resolve(monkeypatch, "93.184.216.34", calls=calls)
    g = net_guard.guard_url(url)
    assert not g.ok and g.reason
    assert calls == []


def test_guard_rejects_unresolvable(monkeypatch):
    """解析失败（域名不存在）= fail-closed，理由里带主机名"""
    _patch_resolve(monkeypatch,
                   error=socket.gaierror(11001, "getaddrinfo failed"))
    g = net_guard.guard_url("http://no-such-host.example/x")
    assert not g.ok
    assert g.host == "no-such-host.example"
    assert "解析失败" in g.reason


def test_guard_real_literals_without_dns():
    """真实 getaddrinfo（IP 字面量不产生 DNS 查询，离线可跑）：
    回环字面量必拒（未登记白名单，主机名阶段即拒）、公网字面量放行并回填 ip"""
    g = net_guard.guard_url("http://127.0.0.1:9/x")
    assert not g.ok and "白名单" in g.reason and g.ip == ""
    g2 = net_guard.guard_url("http://93.184.216.34/x")
    assert g2.ok and g2.ip == "93.184.216.34"


# ---------------- D：宿主开关 ----------------
def test_allow_private_switch(monkeypatch):
    _patch_resolve(monkeypatch, "127.0.0.1")
    assert not net_guard.guard_url("http://127.0.0.1:8080/x").ok
    net_guard.set_allow_private_network(True)
    g = net_guard.guard_url("http://127.0.0.1:8080/x")
    assert g.ok and g.host == "127.0.0.1"        # IP 层放行
    # 协议白名单不受开关影响
    assert not net_guard.guard_url("file:///etc/passwd").ok


# ---------------- E：plugin_net 集成面 ----------------
class _FakeLogger:
    """审计日志桩：收集所有 info/warning 行"""

    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(str(msg))

    def warning(self, msg):
        self.lines.append(str(msg))


def test_sync_post_private_url_denied_same_shape():
    """同步核心：内网 URL 被拦，返回与既有失败结果同构的五键 dict"""
    from src.plugin_net import http_post_json

    url = "http://169.254.169.254/latest/meta-data/"
    res = http_post_json(url, body={"k": "v"}, timeout=5.0)
    assert set(res) == {"ok", "status", "body", "error", "url"}
    assert res["ok"] is False and res["status"] == 0
    assert res["body"] == "" and res["url"] == url
    assert "拦截" in res["error"]


def test_sync_get_private_url_denied():
    from src.plugin_net import http_get_json

    res = http_get_json("http://localhost:9/x", timeout=5.0)
    assert res["ok"] is False and res["status"] == 0
    assert res["body"] == "" and res["error"]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_async_bridge_denies_and_audits(qapp):
    """异步桥：内网 URL 照常回调同构拒绝结果；审计日志记 URL+原因、
    不记请求体（日志不是外泄通道）"""
    from PyQt6.QtCore import QCoreApplication, QThread
    from src.plugin_net import make_async_poster

    url = "http://localhost:9/metadata"
    logger = _FakeLogger()
    poster = make_async_poster(logger=logger)
    results = []
    ok = poster(url, body={"token": "secret-payload"}, timeout=5.0,
                on_done=results.append)
    assert ok is True                     # 已发起（拒绝结果经回调送达）
    deadline = 5000                       # ms
    while not results and deadline > 0:
        QCoreApplication.processEvents()
        QThread.msleep(20)
        deadline -= 20
    assert results, "拒绝结果未回调"
    res = results[0]
    assert set(res) == {"ok", "status", "body", "error", "url"}
    assert res["ok"] is False and res["status"] == 0
    assert res["url"] == url and res["body"] == ""
    joined = "\n".join(logger.lines)
    assert url in joined and res["error"] in joined
    assert "secret-payload" not in joined  # 请求体不进审计日志


# ---------------- F：回环白名单（宿主登记制，1.5 配套）----------------
class TestLoopbackAllowlist:
    def test_registered_endpoint_passes_without_resolve(self, monkeypatch):
        calls = []
        _patch_resolve(monkeypatch, calls=calls)   # 放行路径不应进解析
        assert net_guard.allow_loopback_endpoint("127.0.0.1", 8095) is True
        res = net_guard.guard_url("http://127.0.0.1:8095/v1/chat/completions")
        assert res.ok is True and calls == []

    def test_localhost_form_matches_127_registration(self):
        net_guard.allow_loopback_endpoint("127.0.0.1", 8095)
        assert net_guard.guard_url("http://localhost:8095/health").ok is True

    def test_unregistered_loopback_port_rejected(self):
        net_guard.allow_loopback_endpoint("127.0.0.1", 8095)
        res = net_guard.guard_url("http://127.0.0.1:11434/v1")
        assert res.ok is False and "白名单" in res.reason

    def test_ipv6_loopback_registration(self):
        net_guard.allow_loopback_endpoint("::1", 8095)
        assert net_guard.guard_url("http://[::1]:8095/health").ok is True

    def test_non_loopback_registration_ignored(self, monkeypatch):
        assert net_guard.allow_loopback_endpoint("192.168.1.5", 80) is False
        _patch_resolve(monkeypatch, "192.168.1.5")
        res = net_guard.guard_url("http://192.168.1.5/admin")
        assert res.ok is False

    def test_bad_args_rejected(self):
        assert net_guard.allow_loopback_endpoint("127.0.0.1", 0) is False
        assert net_guard.allow_loopback_endpoint("127.0.0.1", 70000) is False
        assert net_guard.allow_loopback_endpoint("127.0.0.1", "x") is False

    def test_clear_works(self):
        net_guard.allow_loopback_endpoint("127.0.0.1", 8095)
        net_guard.clear_loopback_endpoints()
        assert net_guard.guard_url("http://127.0.0.1:8095/").ok is False


# ---------------- G：sync_loopback_allowlist（AI 总配置 → 白名单）----------------
class TestSyncLoopbackAllowlist:
    def test_local_ai_endpoints_registered(self):
        from src.ai_server import sync_loopback_allowlist
        cfg = {"ai_local_port": 8095,
               "ai_cloud_base_url": "http://127.0.0.1:11434/v1"}
        assert sync_loopback_allowlist(cfg) == 2
        assert net_guard.guard_url("http://127.0.0.1:8095/v1").ok is True
        assert net_guard.guard_url("http://127.0.0.1:11434/v1/tags").ok is True

    def test_non_loopback_base_url_ignored(self):
        from src.ai_server import sync_loopback_allowlist
        cfg = {"ai_local_port": 8095,
               "ai_cloud_base_url": "https://api.deepseek.com/v1"}
        assert sync_loopback_allowlist(cfg) == 1
        assert net_guard.guard_url("http://127.0.0.1:8095/").ok is True

    def test_bad_config_values_fall_back(self):
        from src.ai_server import sync_loopback_allowlist
        cfg = {"ai_local_port": "not-a-port", "ai_cloud_base_url": ""}
        assert sync_loopback_allowlist(cfg) == 1   # 端口回退默认 8095
