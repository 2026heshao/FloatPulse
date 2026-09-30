# -*- coding: utf-8 -*-
"""
====================================================================
宿主网络桥  -  plugin_net
====================================================================
给插件提供**受控联网**能力的宿主侧实现。插件永远不 import 网络库
（requires 白名单禁止 socket/ssl/requests），联网一律走本桥：

    插件 --ctx.http_post_json_async()--> 宿主桥（后台线程）
         --on_done(result)--> 插件（回调回 UI 线程）

设计要点：
  - 同步核心 ``http_post_json()`` / ``http_get_json()`` 纯 stdlib
    （urllib），可无 GUI 测试；
  - 异步包装用 QThread，结果经**主线程 Relay** 转发 —— PyQt 对无
    QObject 接收者的 lambda 走 DirectConnection（在工作线程里调），
    直接把 on_done 连到 worker 信号会在线程外回调控件 → 崩溃。
    Relay 活在主线程，信号跨线程自动排队，on_done 必然回 UI 线程；
  - 全部 worker 收进模块级集合防 GC（QThread 被 GC 会直接崩）；
  - 审计日志只记 URL / 耗时 / 状态码，**不记请求体与响应体**
    （可能含用户数据与 API key，日志不是外泄通道）；
  - 响应大小上限 ``max_bytes``（默认 4MB）：LLM 返回一般几十 KB，
    防御性上限防异常端点拖爆内存；
  - SSRF 防护闸（2026-09-30 起）：真正发请求前先过
    ``src.net_guard.guard_url``——仅 http/https、拒绝 localhost /
    *.local 等内网主机名与一切本地/内网 IP（含 IPv4-mapped IPv6）、
    解析失败 fail-closed。拒绝结果与网络失败**同构**（ok=False），
    异步路径照常经回调送达并走现有审计日志（只记 URL 与拒绝原因）。
    宿主可用 ``net_guard.set_allow_private_network(True)`` 放行内网
    （本地 AI 端点等场景，设置项接线留待后续波次）。遗留项：urllib
    默认跟随重定向，本闸只做单请求校验，逐跳校验待补。

本模块 import PyQt6，仅供宿主（knowledge_ball / loader）使用；
插件**不要** import 本模块 —— 走 ctx 桥才是受控通道。
====================================================================
"""

import json
import time
import urllib.error
import urllib.request

from src.net_guard import guard_url

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# 默认响应上限（字节）。timeout 上限与 plugin_api 侧一致（120s）。
DEFAULT_MAX_BYTES = 4 * 1024 * 1024


# ---------------- 同步核心（纯 stdlib，可无 GUI 测试） ----------------
def _http_request(url, headers, timeout, max_bytes, make_request):
    """同步请求公共骨架：永不抛异常，失败归一成 ``ok=False`` 结果 dict。

    POST 与 GET 共用「发请求 → 读响应（限 max_bytes）→ 4xx/5xx 保留
    错误说明体」的路径，差异只在 Request 的构造（由 make_request 提供）。
    """
    result = {"ok": False, "status": 0, "body": "", "error": "",
              "url": str(url or "")}
    # SSRF 闸：真正发请求前先过 net_guard；拒绝时与网络失败**同构**
    # （ok=False），异步路径会照常经 _finish 走现有审计日志
    # （只记 URL 与拒绝原因，不记请求体）。
    guard = guard_url(url)
    if not guard.ok:
        result["error"] = guard.reason
        return result
    try:
        req = make_request()
    except Exception as exc:              # noqa: BLE001 - 构造失败（如 body 序列化）
        result["error"] = repr(exc)
        return result
    for key, val in dict(headers or {}).items():
        try:
            req.add_header(str(key), str(val))
        except Exception:                 # noqa: BLE001 - 坏头直接丢弃
            pass
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                result["error"] = f"响应超过大小上限（>{max_bytes} 字节）"
                return result
            result.update(ok=True, status=int(resp.status),
                          body=raw.decode("utf-8", "replace"))
            return result
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 也读一下 body：LLM API / GitHub API 的错误说明就在里面
        try:
            detail = exc.read(max_bytes + 1).decode("utf-8", "replace")
            if len(detail) > max_bytes:
                detail = ""
        except Exception:                 # noqa: BLE001
            detail = ""
        result.update(status=int(exc.code), body=detail,
                      error=f"HTTP {exc.code}")
        return result
    except Exception as exc:              # noqa: BLE001 - 网络/超时/代理等
        result["error"] = repr(exc)
        return result


def http_post_json(url, headers=None, body=None, timeout=30.0,
                   max_bytes=DEFAULT_MAX_BYTES):
    """同步 POST JSON。返回结果 dict（字段见 plugin_api 桥方法注释）。

    本函数**永不抛异常**：任何失败（网络/编码/超限）都归一成
    ``{"ok": False, ..., "error": ...}``，调用方只看 ok 字段。
    """
    try:
        payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return {"ok": False, "status": 0, "body": "", "error":
                f"body 无法 JSON 序列化：{exc!r}", "url": str(url or "")}

    def make_request():
        req = urllib.request.Request(str(url), data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        return req

    return _http_request(url, headers, timeout, max_bytes, make_request)


def http_get_json(url, headers=None, timeout=30.0,
                  max_bytes=DEFAULT_MAX_BYTES):
    """同步 GET（2026-09-29 起：应用内「检查更新」拉 GitHub Releases API）。

    与 ``http_post_json`` 同一套永不抛异常契约；不发 body、不设
    Content-Type。调用方自行带 User-Agent（GitHub API 强制要求）。
    """
    def make_request():
        return urllib.request.Request(str(url), method="GET")

    return _http_request(url, headers, timeout, max_bytes, make_request)


def http_get_bytes(url, headers=None, timeout=30.0,
                   max_bytes=DEFAULT_MAX_BYTES):
    """同步 GET **二进制**（2026-09-30 起：插件市场下载 .fpplug 用）。

    与文本 GET 分开的原因：二进制不能走 utf-8 replace 解码（会损坏
    zip 字节流）。其余契约逐字一致：永不抛异常、SSRF 闸、限长、
    4xx/5xx 保留错误码。
    """
    result = {"ok": False, "status": 0, "data": b"", "error": "",
              "url": str(url or "")}
    guard = guard_url(url)
    if not guard.ok:
        result["error"] = guard.reason
        return result
    try:
        req = urllib.request.Request(str(url), method="GET")
        for key, val in dict(headers or {}).items():
            try:
                req.add_header(str(key), str(val))
            except Exception:             # noqa: BLE001 - 坏头直接丢弃
                pass
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                result["error"] = f"下载超过大小上限（>{max_bytes} 字节）"
                return result
            result.update(ok=True, status=int(resp.status), data=raw)
            return result
    except urllib.error.HTTPError as exc:
        result.update(status=int(exc.code), error=f"HTTP {exc.code}")
        return result
    except Exception as exc:              # noqa: BLE001 - 网络/超时/代理等
        result["error"] = repr(exc)
        return result


# ---------------- 异步包装（QThread + 主线程 Relay） ----------------
class _Relay(QObject):
    """活在主线程的转发器：跨线程信号经它排队回 UI 线程"""
    got = pyqtSignal(object)


class _HttpPostWorker(QThread):
    """后台线程：执行同步 POST，emit 结果"""
    done = pyqtSignal(object)

    def __init__(self, url, headers, body, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._headers = headers
        self._body = body
        self._timeout = timeout

    def run(self):
        self.done.emit(http_post_json(self._url, self._headers,
                                      self._body, self._timeout))


class _HttpGetWorker(QThread):
    """后台线程：执行同步 GET，emit 结果"""
    done = pyqtSignal(object)

    def __init__(self, url, headers, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._headers = headers
        self._timeout = timeout

    def run(self):
        self.done.emit(http_get_json(self._url, self._headers,
                                     self._timeout))


class _HttpGetBytesWorker(QThread):
    """后台线程：执行同步二进制 GET，emit 结果（插件市场下载 .fpplug）"""
    done = pyqtSignal(object)

    def __init__(self, url, headers, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._headers = headers
        self._timeout = timeout

    def run(self):
        self.done.emit(http_get_bytes(self._url, self._headers,
                                      self._timeout))


# 存活的 worker 集合（防 GC；QThread 对象被回收会直接崩溃进程）
_ACTIVE_WORKERS = set()


def _spawn_async(logger, method_label, worker, on_done, timeout, url):
    """POST / GET 共用的异步管道，行为逐字对齐 make_async_poster 原实现。

    线程模型（2026-09-27 修）：relay **每请求独立**——worker 在后台
    线程 emit ``done``，本请求自己的 relay（主线程创建的 QObject）把
    信号排队回 UI 线程。此前 relay 是整个桥共享的单例且
    ``relay.got.connect(_finish)`` 从不移除，第 N 个响应会把**全部**
    历史请求的回调广播一遍（实测：多点几次「保存并测试」后，对话
    回调收到探活的响应，气泡重复出现；日志同一秒爆出 N 条完成行）。

    返回 False = 没能发起（不回调）；True = 已启动，结果经 on_done 回 UI 线程。
    """
    if not callable(on_done):
        if logger is not None:
            logger.warning(f"[插件网络] {method_label} 被拒：on_done 不是可调用对象")
        return False

    relay = _Relay()      # 每请求一个：响应只送达自己的回调（见 docstring）
    started = time.monotonic()

    def _finish(res):
        took = time.monotonic() - started
        if logger is not None:
            logger.info(f"[插件网络] {method_label} {url} -> "
                        f"{'HTTP ' + str(res.get('status', 0)) if res.get('ok') else res.get('error', '?')}"
                        f"（{took:.1f}s）")
        try:
            on_done(dict(res))
        except Exception as exc:      # noqa: BLE001 - 插件回调异常不反噬
            if logger is not None:
                logger.warning(f"[插件网络] on_done 回调异常：{exc!r}")

    # worker 线程 emit → relay（主线程）排队 → _finish 回 UI 线程
    worker.done.connect(lambda res: relay.got.emit(res))
    relay.got.connect(_finish)
    worker.finished.connect(lambda: _ACTIVE_WORKERS.discard(worker))
    _ACTIVE_WORKERS.add(worker)
    if logger is not None:
        logger.info(f"[插件网络] {method_label} {url} 发起（timeout={timeout}s）")
    worker.start()
    return True


def make_async_poster(logger=None):
    """构造注入 PluginContext 的异步 POST 桥函数。

    返回 ``post(url, headers, body, timeout, on_done) -> bool``：
      - 在后台线程发请求，``on_done(result)`` 保证在 UI 线程回调；
      - 返回 False = 没能发起（回调 ok=False 的结果）。
    宿主在**主线程**调用一次 make_async_poster，把返回值塞进
    PluginContext(http_post_async=...)。闭包持有日志器，
    生命周期即应用生命周期，无需手动清理。
    """

    def post(url, headers=None, body=None, timeout=30.0, on_done=None):
        worker = _HttpPostWorker(url, dict(headers or {}), body, timeout)
        return _spawn_async(logger, "POST", worker, on_done, timeout, url)

    return post


def make_async_getter(logger=None):
    """构造异步 GET 桥（2026-09-29 起：设置页「检查更新」用）。

    返回 ``get(url, headers=None, timeout=30.0, on_done=None) -> bool``，
    与 POST 桥同一条管道（每请求独立 relay / 存活集防 GC / UI 线程回调），
    只是不发 body、不设 Content-Type；调用方自带 User-Agent。
    """

    def get(url, headers=None, timeout=30.0, on_done=None):
        worker = _HttpGetWorker(url, dict(headers or {}), timeout)
        return _spawn_async(logger, "GET", worker, on_done, timeout, url)

    return get


def make_async_bytes_getter(logger=None):
    """构造异步**二进制** GET 桥（2026-09-30 起：插件市场下载 .fpplug）。

    与 make_async_getter 同一条管道（每请求独立 relay / 存活集防 GC /
    UI 线程回调），差别只在结果里是 ``data: bytes`` 而不是 ``body: str``。
    """

    def get_bytes(url, headers=None, timeout=30.0, on_done=None):
        worker = _HttpGetBytesWorker(url, dict(headers or {}), timeout)
        return _spawn_async(logger, "GET-BIN", worker, on_done, timeout, url)

    return get_bytes
