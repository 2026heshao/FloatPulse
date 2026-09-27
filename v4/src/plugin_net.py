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
  - 同步核心 ``http_post_json()`` 纯 stdlib（urllib），可无 GUI 测试；
  - 异步包装用 QThread，结果经**主线程 Relay** 转发 —— PyQt 对无
    QObject 接收者的 lambda 走 DirectConnection（在工作线程里调），
    直接把 on_done 连到 worker 信号会在线程外回调控件 → 崩溃。
    Relay 活在主线程，信号跨线程自动排队，on_done 必然回 UI 线程；
  - 全部 worker 收进模块级集合防 GC（QThread 被 GC 会直接崩）；
  - 审计日志只记 URL / 耗时 / 状态码，**不记请求体与响应体**
    （可能含用户数据与 API key，日志不是外泄通道）；
  - 响应大小上限 ``max_bytes``（默认 4MB）：LLM 返回一般几十 KB，
    防御性上限防异常端点拖爆内存。

本模块 import PyQt6，仅供宿主（knowledge_ball / loader）使用；
插件**不要** import 本模块 —— 走 ctx 桥才是受控通道。
====================================================================
"""

import json
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# 默认响应上限（字节）。timeout 上限与 plugin_api 侧一致（120s）。
DEFAULT_MAX_BYTES = 4 * 1024 * 1024


# ---------------- 同步核心（纯 stdlib，可无 GUI 测试） ----------------
def http_post_json(url, headers=None, body=None, timeout=30.0,
                   max_bytes=DEFAULT_MAX_BYTES):
    """同步 POST JSON。返回结果 dict（字段见 plugin_api 桥方法注释）。

    本函数**永不抛异常**：任何失败（网络/编码/超限）都归一成
    ``{"ok": False, ..., "error": ...}``，调用方只看 ok 字段。
    """
    result = {"ok": False, "status": 0, "body": "", "error": "",
              "url": str(url or "")}
    try:
        payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        result["error"] = f"body 无法 JSON 序列化：{exc!r}"
        return result
    req = urllib.request.Request(str(url), data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
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
        # 4xx/5xx 也读一下 body：LLM API 的错误说明就在里面
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


# ---------------- 异步包装（QThread + 主线程 Relay） ----------------
class _Relay(QObject):
    """活在主线程的转发器：跨线程信号经它排队回 UI 线程"""
    got = pyqtSignal(object)


class _HttpPostWorker(QThread):
    """后台线程：执行同步请求，emit 结果"""
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


# 存活的 worker 集合（防 GC；QThread 对象被回收会直接崩溃进程）
_ACTIVE_WORKERS = set()


def make_async_poster(logger=None):
    """构造注入 PluginContext 的异步桥函数。

    返回 ``post(url, headers, body, timeout, on_done) -> bool``：
      - 在后台线程发请求，``on_done(result)`` 保证在 UI 线程回调；
      - 返回 False = 没能发起（回调 ok=False 的结果）。
    宿主在**主线程**调用一次 make_async_poster，把返回值塞进
    PluginContext(http_post_async=...)。闭包持有 Relay，宿主持有闭包，
    生命周期即应用生命周期，无需手动清理。
    """
    relay = _Relay()

    def post(url, headers=None, body=None, timeout=30.0, on_done=None):
        result = {"ok": False, "status": 0, "body": "",
                  "error": "", "url": str(url or "")}
        if not callable(on_done):
            if logger is not None:
                logger.warning("[插件网络] POST 被拒：on_done 不是可调用对象")
            return False

        worker = _HttpPostWorker(url, dict(headers or {}), body, timeout)
        started = time.monotonic()

        def _finish(res):
            took = time.monotonic() - started
            if logger is not None:
                logger.info(f"[插件网络] POST {url} -> "
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
            logger.info(f"[插件网络] POST {url} 发起（timeout={timeout}s）")
        worker.start()
        return True

    return post
