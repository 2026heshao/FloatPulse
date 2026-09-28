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

# 探活间隔 / 总超时（秒）
PROBE_INTERVAL_MS = 2500
PROBE_TIMEOUT_S = 90

# 状态机取值（与插件侧 LocalServerManager 口径一致）
ST_STOPPED = "stopped"
ST_STARTING = "starting"
ST_READY = "ready"
ST_ERROR = "error"


def build_launch_args(gguf: str, port: int) -> list:
    """llama-server 启动参数（纯函数，便于验证）

    -ngl 99 全量显卡卸载、-c 8192 上下文——与 ai-assistant /
    CodeDrill 同款实测参数。
    """
    return ["-m", str(gguf), "--host", "127.0.0.1",
            "--port", str(int(port)), "-ngl", "99", "-c", "8192"]


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
            except Exception:         # noqa: BLE001 - 回调异常不反噬
                pass

    # ---- 启动 ----
    def start(self, post_fn, exe: str, gguf: str, port: int):
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

        proc = QProcess()
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyRead.connect(self._drain_output)     # 防管道积压卡死
        proc.started.connect(lambda: assign_to_job(proc.processId()))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(
            lambda err: self._emit(ST_ERROR, f"进程错误：{err}"))
        proc.start(exe, build_launch_args(gguf, self.port))
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
        self._stop_probe()
        was = self._proc
        self._proc = None
        if self.status == ST_READY:
            self._emit(ST_STOPPED, "本地服务已停止")
        elif code == 0:
            self._emit(ST_STOPPED, "本地服务已退出")
        else:
            self._emit(ST_ERROR, f"本地服务异常退出（code={code}）"
                                 f"——常见原因：显存不足 / 端口被占 / gguf 损坏")
        del was

    # ---- 停止 ----
    def stop(self):
        if self._proc is None:
            self._emit(ST_STOPPED, "本地服务未在运行")
            return
        self._proc.terminate()
        QTimer.singleShot(3000, self._kill_if_alive)    # 3s 不退才强杀

    def _kill_if_alive(self):
        if self._proc is not None and self._proc.state() != \
                QProcess.ProcessState.NotRunning:
            self._proc.kill()

    @property
    def running(self) -> bool:
        return self._proc is not None


# 模块级单例：设置页重建、插件页面重建都不丢服务状态
AI_SERVER = AiServerManager()
