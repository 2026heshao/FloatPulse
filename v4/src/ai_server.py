# -*- coding: utf-8 -*-
"""
====================================================================
宿主 AI 服务管理  -  ai_server
====================================================================
设置页「🧠 AI 总配置」的本地推理后端：用 QProcess 拉起 llama-server、
探活、状态广播、JobObject 防孤儿。是设置页与插件之间「本地服务是否
就绪」的唯一真相源——插件经 ``ctx.ai.params()['local_ready']`` 实时
读取，经 ``ctx.ai.add_listener`` 订阅状态变化。

与插件侧的同名管理器（ai-assistant 8093 / ai-text-workshop 8094）
各自独立实例、互不冲突；宿主实例默认端口 **8095**（config
``ai_local_port``），供「接入 AI 总配置」的插件共用——一处启动，
所有接入插件受益。

探活请求经 ``make_async_poster``（与插件网络桥同一条受控通道）：
后台线程发请求、回调回 UI 线程，审计行为与插件一致。

本模块只被宿主进程引用（设置页 / knowledge_ball 装配），不进插件
依赖白名单约束范围。
====================================================================
"""

import os
import sys
import time

from PyQt6.QtCore import QProcess, QTimer

from src.logger import get_logger

# 探活间隔 / 总超时（秒）
PROBE_INTERVAL_MS = 2500
PROBE_TIMEOUT_S = 90

# 本地模型上下文长度（llama-server -c）：设置页档位 8K/16K/32K/64K，
# 硬边界在此收敛（config._CONFIG_RANGES 同口径，这里兜底防脏值直通命令行）
CTX_MIN, CTX_MAX = 2048, 131072
DEFAULT_CTX_SIZE = 16384

# 状态机取值（与插件侧 LocalServerManager 口径一致）
ST_STOPPED = "stopped"
ST_STARTING = "starting"
ST_READY = "ready"
ST_ERROR = "error"


def sync_loopback_allowlist(cfg) -> int:
    """把 AI 总配置里的本地端点登记进 net_guard 回环白名单（1.5 配套）。

    本地推理与 Ollama 都在 127.0.0.1 上：不登记，插件桥的 SSRF 闸会把
    本地 AI 一并拦掉。登记两处——
      1. 宿主拉起的 llama-server 端口（config ``ai_local_port``）；
      2. 云端 base_url 若指向回环（如 Ollama ``http://127.0.0.1:11434/v1``），
         net_guard 只放行回环主机，非回环 URL 登记会被忽略。

    启动装配（knowledge_ball）与设置页保存后各调一次；幂等。
    cfg 为 ConfigManager 或等价 .get 映射。返回本次登记成功的条数。
    """
    from urllib.parse import urlparse

    from src.net_guard import allow_loopback_endpoint

    registered = 0
    try:
        port = int(cfg.get("ai_local_port", 8095))
    except (TypeError, ValueError):
        port = 8095
    if allow_loopback_endpoint("127.0.0.1", port):
        registered += 1
    try:
        base_url = str(cfg.get("ai_cloud_base_url", "") or "").strip()
        parts = urlparse(base_url)
        if parts.hostname and allow_loopback_endpoint(
                parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)):
            registered += 1
    except (ValueError, TypeError):
        pass
    return registered


def sanitize_ctx_size(val) -> int:
    """上下文长度收敛：非整数回默认，越界 clamp 到 [CTX_MIN, CTX_MAX]"""
    try:
        ctx = int(val)
    except (TypeError, ValueError):
        return DEFAULT_CTX_SIZE
    return max(CTX_MIN, min(CTX_MAX, ctx))


def build_launch_args(gguf: str, port: int, ctx_size: int = DEFAULT_CTX_SIZE,
                      thinking: bool = False) -> list:
    """llama-server 启动参数（纯函数，便于验证）

    -ngl 99 全量显卡卸载、-c 上下文长度（默认 16K，越界自动收敛）——
    与 ai-assistant / CodeDrill 同款实测参数。
    thinking=False 追加 ``--reasoning off`` 显式关闭思维链。★实测
    b10690 + Qwen3.5：``--reasoning-budget 0`` 不被模板遵循（照样吐完整
    思维链、content 为空），``--reasoning off`` 才是真开关——同题实测
    直答 0.3s/14 tokens vs 思维链 5.7s/384 tokens 仍未出答案（2026-10-01）。
    """
    args = ["-m", str(gguf), "--host", "127.0.0.1",
            "--port", str(int(port)), "-ngl", "99",
            "-c", str(sanitize_ctx_size(ctx_size))]
    if not thinking:
        args += ["--reasoning", "off"]
    return args


def assign_to_job(pid: int) -> bool:
    """把子进程挂进 KILL_ON_JOB_CLOSE 的 Job（Windows 内核级防孤儿）。

    本进程无论正常退出、被强杀还是崩溃，内核关闭 Job 句柄时都会
    终止里面的子进程——不靠 atexit（被强杀时它不会执行）。
    非 Windows / 任何一步失败 → 返回 False（只失去保护，不影响启动）。
    """
    if sys.platform != "win32" or not pid:
        return False
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("r_op", "w_op", "o_op", "r_tr", "w_tr", "o_tr")]

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("per_proc_user_time", ctypes.c_longlong),
                ("per_job_user_time", ctypes.c_longlong),
                ("limit_flags", ctypes.c_uint32),
                ("min_wss", ctypes.c_size_t),
                ("max_wss", ctypes.c_size_t),
                ("active_procs", ctypes.c_uint32),
                ("affinity", ctypes.c_size_t),
                ("priority_class", ctypes.c_uint32),
                ("sched_class", ctypes.c_uint32),
            ]

        class _Ext(ctypes.Structure):
            _fields_ = [
                ("basic", _Basic),
                ("io", _IO),
                ("proc_mem", ctypes.c_size_t),
                ("job_mem", ctypes.c_size_t),
                ("peak_proc_mem", ctypes.c_size_t),
                ("peak_job_mem", ctypes.c_size_t),
            ]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _Ext()
        info.basic.limit_flags = 0x00002000      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            return False
        h = k32.OpenProcess(0x0100 | 0x0001, False, pid)   # SET_QUOTA|TERMINATE
        if not h:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(job, h))
        finally:
            k32.CloseHandle(h)    # 进程句柄用完即关；**Job 句柄故意不关**
    except Exception:             # noqa: BLE001 - 保护失败只降级
        return False


class AiServerManager:
    """宿主级 llama-server 生命周期：启动 / 探活 / 停止 / 状态广播。

    状态机：stopped → starting →（探活通过）ready /（退出或失败）error。
    状态变化回调所有订阅者（设置页状态行、接入插件的页面据此刷新）；
    回调保证在 UI 线程执行（探活走异步桥，relay 已保证）。

    ``post_fn`` 由宿主注入（``make_async_poster`` 产出）：
    ``post(url, headers, body, timeout, on_done) -> bool``。
    """

    def __init__(self):
        self._proc = None            # QProcess（模块级持有，与页面无关）
        self._post_fn = None         # 探活用的异步 POST 函数（宿主注入）
        self._timer = None           # 探活 QTimer
        self._deadline = 0.0         # 探活总超时（time.monotonic 秒）
        self._busy_probe = False     # 上一次探活未返回
        self.port = 0
        self.ctx_size = DEFAULT_CTX_SIZE   # 最近一次启动所用参数（变化检测用）
        self.thinking = False
        self._pending = None         # restart 待启动参数元组（旧实例退出后消费）
        self.status = ST_STOPPED     # stopped / starting / ready / error
        self.detail = ""
        self._listeners = []         # callable(status, detail)

    # ---- 订阅 ----
    def add_listener(self, fn) -> bool:
        """订阅状态变化；立即同步一次当前状态（新页面/设置卡自动对齐）"""
        if not callable(fn):
            return False
        self._listeners.append(fn)
        try:
            fn(self.status, self.detail)
        except Exception:             # noqa: BLE001 - 订阅者异常不反噬
            pass
        return True

    def remove_listener(self, fn) -> bool:
        """退订（设置页/插件页面销毁时必须调，防死引用累积）"""
        try:
            self._listeners.remove(fn)
            return True
        except ValueError:
            return False

    def _emit(self, status: str, detail: str = ""):
        self.status, self.detail = status, detail
        for fn in list(self._listeners):
            try:
                fn(status, detail)
            except Exception as e:    # noqa: BLE001 - 回调异常不反噬
                get_logger().warning(f"AI 状态订阅者回调异常: {e}")

    # ---- 启动 ----
    def start(self, post_fn, exe: str, gguf: str, port: int,
              ctx_size: int = DEFAULT_CTX_SIZE, thinking: bool = False):
        """拉起 llama-server 并开始探活；文件不存在等失败只置 error 不抛"""
        if self._proc is not None:
            self._emit(ST_STARTING, "服务已在启动/运行中")
            return
        exe, gguf = exe.strip().strip('"'), gguf.strip().strip('"')
        if not os.path.isfile(exe):
            self._emit(ST_ERROR, f"llama-server 程序不存在：{exe}")
            return
        if not os.path.isfile(gguf):
            self._emit(ST_ERROR, f"模型文件不存在：{gguf}")
            return
        if not callable(post_fn):
            self._emit(ST_ERROR, "宿主未注入探活通道，无法确认服务就绪")
            return
        self._post_fn = post_fn
        self.port = int(port)
        self.ctx_size = sanitize_ctx_size(ctx_size)
        self.thinking = bool(thinking)

        proc = QProcess()
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyRead.connect(self._drain_output)     # 防管道积压卡死
        proc.started.connect(lambda: assign_to_job(proc.processId()))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error_occurred)
        proc.start(exe, build_launch_args(gguf, self.port,
                                          self.ctx_size, self.thinking))
        self._proc = proc
        self._emit(ST_STARTING,
                   f"启动中…（首次加载模型可能需要几十秒）端口 {self.port}")

        # 探活循环
        self._deadline = time.monotonic() + PROBE_TIMEOUT_S
        self._timer = QTimer()
        self._timer.setInterval(PROBE_INTERVAL_MS)
        self._timer.timeout.connect(self._probe_once)
        self._timer.start()
        QTimer.singleShot(0, self._probe_once)          # 先立刻探一次

    # ---- 探活 ----
    def _probe_once(self):
        if self.status == ST_READY or self._proc is None:
            self._stop_probe()
            return
        if time.monotonic() > self._deadline:
            self._stop_probe()
            self._emit(ST_ERROR,
                       f"启动超时（{PROBE_TIMEOUT_S}s 内未就绪）。"
                       f"常见原因：显存不足 / 端口被占 / gguf 损坏")
            return
        if self._busy_probe or self._post_fn is None:
            return
        url = f"http://127.0.0.1:{self.port}/v1/chat/completions"
        body = {"model": "local", "max_tokens": 1, "temperature": 0,
                "messages": [{"role": "user", "content": "ping"}]}
        self._busy_probe = True

        def on_done(_result):
            self._busy_probe = False
            if self._proc is None:
                return
            if _result.get("ok"):
                self._stop_probe()
                self._emit(ST_READY, f"本地服务就绪（127.0.0.1:{self.port}）")
            # 未就绪（连接拒绝=模型加载中）→ 等下一轮

        try:
            self._post_fn(url, {}, body, 8.0, on_done)
        except Exception:         # noqa: BLE001
            self._busy_probe = False

    def _stop_probe(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    # ---- 输出与退出 ----
    def _drain_output(self):
        """必须持续读走输出：llama-server 刷日志，PIPE 积满会卡死进程"""
        if self._proc is not None:
            self._proc.readAll()          # 诊断暂不落盘，仅防积压

    def _on_finished(self, code, _status):
        was = self._proc
        self._stop_probe()
        self._proc = None
        if self.status == ST_READY:
            self._emit(ST_STOPPED, "本地服务已停止")
        elif code == 0:
            self._emit(ST_STOPPED, "本地服务已退出")
        else:
            self._emit(ST_ERROR, f"本地服务异常退出（code={code}）"
                                 f"——常见原因：显存不足 / 端口被占 / gguf 损坏")
        # 在 finished 信号发射途中不能直接丢最后一个引用（可能 C++ 对象
        # 被析构导致 wrapped-object-deleted）；排队销毁才是安全时机
        if was is not None:
            was.deleteLater()
        if self._pending is not None:
            # restart 流程：旧实例已退场，稍候以新参数拉起（给句柄释放留缓冲）
            QTimer.singleShot(300, self._launch_pending)

    # ---- 停止 ----
    def stop(self):
        if self._proc is None:
            self._emit(ST_STOPPED, "本地服务未在运行")
            return
        proc = self._proc
        proc.terminate()
        # 强杀定时器必须盯住**这个旧实例**：若它在 3s 内退出、restart
        # 随即拉起新实例，旧写法判 self._proc 会把新实例误杀
        def _kill_this():
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
        QTimer.singleShot(3000, _kill_this)   # 3s 不退才强杀

    def _on_error_occurred(self, err):
        """进程错误（exe 缺失 → FailedToStart 等）。

        FailedToStart 后进程不会再发 finished——必须就地清掉 _proc 并
        停探活，否则 start() 永远被「已在运行」守卫拒绝，状态机卡死。
        """
        self._stop_probe()
        if err == QProcess.ProcessError.FailedToStart:
            self._proc = None
            self._emit(ST_ERROR, f"进程启动失败（{err}）"
                                 f"——检查 exe / 模型路径与权限")
            get_logger().warning("llama-server FailedToStart，已复位状态机")
        else:
            self._emit(ST_ERROR, f"进程错误：{err}")

    # ---- 平滑重启 ----
    def restart(self, post_fn, exe: str, gguf: str, port: int,
                ctx_size: int = DEFAULT_CTX_SIZE, thinking: bool = False):
        """以新参数重启本地服务（设置页改参专用）。

        运行中：先 stop，等旧实例真正退出（finished → ST_STOPPED/ST_ERROR）
        再以新参数拉起——terminate() 在 Windows 上对无窗口控制台程序可能
        迟迟不生效（靠 3s 强杀兜底），固定延迟猜不准，状态驱动才可靠。
        未运行：直接以新参数 start。重复调用以后一次参数为准。
        """
        self._pending = (post_fn, exe, gguf, port, ctx_size, thinking)
        if self._proc is None:
            self._launch_pending()
        else:
            self.stop()

    def _launch_pending(self):
        pend, self._pending = self._pending, None
        if pend and self._proc is None:
            self.start(*pend)     # 期间被手动拉起则丢弃 pending，防双启

    @property
    def running(self) -> bool:
        return self._proc is not None


# 模块级单例：设置页重建、插件页面重建都不丢服务状态
AI_SERVER = AiServerManager()
