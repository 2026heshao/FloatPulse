# -*- coding: utf-8 -*-
"""宿主网络桥回归 —— plugin_net.http_post_json（同步核心）+ 异步回调线程。

钉死的行为：
  A 同步核心对真实本地 HTTP 服务：200 成功回文 / 404 归一成
    ok=False + "HTTP 404" + 保留响应体 / 连接拒绝不抛异常
  B UTF-8 请求体与响应体的中文往返
  C 自定义请求头真的发出去（服务端回显验证）
  D 异步 make_async_poster：worker 线程发请求，on_done 在主线程回调
    （QApplication 存在时，回调发生在发起线程 == UI 线程）
本文件的同步部分无 GUI 依赖；异步部分 offscreen 跑。
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.plugin_net import http_post_json, make_async_poster  # noqa: E402


# ---------------- 本地测试服务器 ----------------
class _EchoHandler(BaseHTTPRequestHandler):
    """POST /echo → 回显 JSON；POST /deny → 404；其他 → 404"""

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_POST(self):
        raw = self._read()
        if self.path == "/echo":
            # 回显：请求头里的 X-Probe + 请求体原文
            payload = json.dumps({
                "probe": self.headers.get("X-Probe") or "",
                "echo": raw.decode("utf-8", "replace"),
                "中文": "回显✔",
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = b'{"error": "no such endpoint"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):        # 静默
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


# ---------------- A：同步核心 ----------------
def test_post_ok_roundtrip(server):
    result = http_post_json(server + "/echo", body={"hello": "世界"})
    assert result["ok"] is True and result["status"] == 200
    data = json.loads(result["body"])
    assert data["echo"] == '{"hello": "世界"}'
    assert data["中文"] == "回显✔"


def test_post_custom_header(server):
    result = http_post_json(server + "/echo", headers={"X-Probe": "fp-42"},
                            body={})
    assert result["ok"] is True
    assert json.loads(result["body"])["probe"] == "fp-42"


def test_post_404_normalized(server):
    result = http_post_json(server + "/deny", body={})
    assert result["ok"] is False
    assert result["status"] == 404
    assert "HTTP 404" in result["error"]
    assert "no such endpoint" in result["body"]


def test_post_connection_refused_is_safe():
    # 1 号端口几乎必然拒连；关键是不抛异常，错误归一成 dict
    result = http_post_json("http://127.0.0.1:1/nope", body={},
                            timeout=2.0)
    assert result["ok"] is False
    assert result["status"] == 0
    assert result["error"]


def test_post_bad_body_json_is_safe(server):
    class _Weird:
        def __init__(self):
            self.x = object()     # json.dumps 会炸

    result = http_post_json(server + "/echo", body={"o": _Weird()})
    assert result["ok"] is False and "序列化" in result["error"]


# ---------------- D：异步回调 ----------------
def test_async_poster_calls_back_on_main_thread(server, qapp):
    """发起后 pump 事件循环，on_done 应在同一线程被调并带回结果"""
    from PyQt6.QtCore import QCoreApplication, QThread

    main_thread = QThread.currentThread()
    poster = make_async_poster()
    results = []

    def on_done(res):
        results.append((QThread.currentThread() is main_thread, res))

    ok = poster(server + "/echo", headers={}, body={"q": 1},
                timeout=5.0, on_done=on_done)
    assert ok is True
    deadline = 5000        # ms
    while not results and deadline > 0:
        QCoreApplication.processEvents()
        QThread.msleep(20)
        deadline -= 20
    assert results, "on_done 未在超时内被回调"
    same_thread, res = results[0]
    assert same_thread, "on_done 必须在主线程回调"
    assert res["ok"] is True
    assert json.loads(res["body"])["echo"] == '{"q": 1}'


def test_async_poster_rejects_non_callable(server, qapp):
    poster = make_async_poster()
    assert poster(server + "/echo", body={}, on_done=None) is False


def test_async_poster_no_cross_delivery(server, qapp):
    """连发 3 个请求（不同请求体）：每个 on_done **恰好**收到自己的响应。

    回归钉子：此前 relay 是整个桥共享的单例且 ``relay.got.connect``
    从不移除 → 第 N 个响应会把全部历史请求的回调广播一遍。实测表现 =
    多点几次「保存并测试连接」后，对话回调收到探活的响应，同一回复
    的气泡重复出现多条。
    """
    from PyQt6.QtCore import QCoreApplication, QThread

    poster = make_async_poster()
    received = []                      # (tag, 回显的请求体原文)

    def make_cb(tag):
        def cb(res):
            body = ""
            if res.get("ok"):
                body = json.loads(res["body"])["echo"]
            received.append((tag, body))
        return cb

    for i in range(3):
        assert poster(server + "/echo", body={"tag": str(i)},
                      timeout=5.0, on_done=make_cb(str(i))) is True

    deadline = 5000        # ms
    while len(received) < 3 and deadline > 0:
        QCoreApplication.processEvents()
        QThread.msleep(20)
        deadline -= 20

    # 若广播 bug 回归：received 长度会 > 3（每响应触发 3 个回调）
    assert sorted(received) == [
        ("0", '{"tag": "0"}'),
        ("1", '{"tag": "1"}'),
        ("2", '{"tag": "2"}'),
    ], f"回调次数或内容错乱：{received}"


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
