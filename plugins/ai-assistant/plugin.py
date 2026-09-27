# -*- coding: utf-8 -*-
"""
====================================================================
AI 助手  -  FloatPulse 外置插件（ai-assistant）
====================================================================
第一个「页面插件」+ 第一个声明式联网插件 + 第一个声明式写权限插件：

  - manifest 声明 ``"page"`` → 主窗口新增「🤖 AI 助手」导航页
    （不占悬浮球右键菜单；热键 Ctrl+Alt+I 直接切到该页）
  - manifest 声明 ``"capabilities": ["network"]`` → 经宿主网络桥联网
  - manifest 声明 ``"capabilities": ["write"]`` → 经 ``ctx.write``
    把回复存成笔记（「📥 存为笔记」按钮），只增不改删

功能：读应用内任务 / 碎片 / 笔记的只读快照，交给 OpenAI 兼容接口做
总结、分类与问答。三种后端统一走 ``/chat/completions`` 协议：

  云端    DeepSeek / 硅基流动（填 base_url + key）
  本地常驻 Ollama(11434) / llama-server(8080)（无需 key）
  本地自带 浏览选 .gguf + llama-server.exe → 插件用 QProcess 拉起
          服务（默认端口 8093），探活就绪后自动接上 —— 与 CodeDrill
          同款的引擎与参数（-ngl 99 全量显卡卸载）

诚实性与安全约定：
  - API key 明文存在插件私有目录（data_dir/config.json）。安全边界 =
    本机用户账户；桌面应用不做私密存储，但这扇门只对用户本人打开。
  - 发给模型的数据只有用户显式点快捷指令 / 输入框内容，插件**从不**
    在后台静默上传任何数据（app.log 的 [插件网络] 行只有 URL 与耗时）。
  - 写数据只在用户点「📥 存为笔记」时发生（``ctx.write``，只增不改删）。
  - 本地推理子进程挂进 Windows Job Object（KILL_ON_JOB_CLOSE）：
    FloatPulse 无论正常退出还是被强杀，内核都会带走 llama-server，
    不会出现「关了程序，进程还占着 2.9GB 显存」的孤儿（CodeDrill 同款方案）。
====================================================================
"""

import json
import os
import sys

from PyQt6.QtCore import QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import make_hint_label

PLUGIN_ID = "ai-assistant"
PAGE_KEY = f"plugin:{PLUGIN_ID}"       # 主窗口页面 key（与 loader 约定一致）

# ---------------- 配置 ----------------
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "max_data_chars": 6000,       # 单块数据塞进提示词的最大字符数
    # 本地自带推理（浏览 gguf + llama-server.exe，插件自己拉起服务）
    "local_server_exe": "",
    "local_gguf": "",
    "local_port": 8093,           # 避开 CodeDrill 的 8080 与 Ollama 的 11434
}

# 后端预设：(显示名, base_url, model, 说明)
BACKEND_PRESETS = (
    ("云端 · DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat",
     "需在 api.deepseek.com 申请 key"),
    ("云端 · 硅基流动", "https://api.siliconflow.cn/v1", "",
     "需在 siliconflow.cn 申请 key"),
    ("本地 · Ollama", "http://127.0.0.1:11434/v1", "qwen3:4b",
     "需先安装 Ollama 并 ollama pull 模型"),
    ("本地 · llama-server", "http://127.0.0.1:8080/v1", "",
     "已手动启动的 llama.cpp 服务（注意是否要求 API key）"),
)

SYSTEM_PROMPT = (
    "你是办公工具 FloatPulse 内置的 AI 助手。用户可能把应用内的任务、"
    "碎片（随手记的片段）、笔记数据发给你。要求：\n"
    "1. 用简体中文回答，简洁直接，结果类内容用 Markdown 列表组织；\n"
    "2. 只依据用户给出的数据回答，绝不虚构不存在的任务或笔记；\n"
    "3. 数据为空时直说「数据为空」，不要编造。"
)

# 上下文历史最多保留的条数（role 消息条数，防 token 无限膨胀）
MAX_HISTORY = 12

# 本地服务探活间隔 / 总超时（秒）
PROBE_INTERVAL_MS = 2500
PROBE_TIMEOUT_S = 90


# ====================================================================
# Windows Job Object：把 llama-server 与本进程「同生共死」交给内核
# （精简自 CodeDrill runtime/job_object.py 的实测实现，MIT 自用）
# ====================================================================
def assign_to_job(pid: int) -> bool:
    """把子进程挂进 KILL_ON_JOB_CLOSE 的 Job。

    本进程无论正常退出、被强杀还是崩溃，内核关闭 Job 句柄时都会
    终止里面的子进程 —— 不靠 atexit（被强杀时它不会执行）。
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


# ====================================================================
# 配置读写（插件私有目录 data_dir/config.json）
# ====================================================================
def load_config(ctx) -> dict:
    """读插件配置；缺的字段用默认值补齐（前向兼容新字段）"""
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(ctx.data_dir, CONFIG_FILE) if ctx.data_dir else ""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for key in DEFAULT_CONFIG:
                    if key in stored:
                        cfg[key] = stored[key]
        except (OSError, ValueError) as exc:
            ctx.logger.warning(f"[AI 助手] 配置读取失败，用默认值：{exc}")
    return cfg


def save_config(ctx, cfg: dict) -> bool:
    """写插件配置；data_dir 不可用 / 写失败返回 False（调用方提示）"""
    path = os.path.join(ctx.data_dir, CONFIG_FILE) if ctx.data_dir else ""
    if not path:
        return False
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError as exc:
        ctx.logger.warning(f"[AI 助手] 配置写入失败：{exc}")
        return False


# ====================================================================
# 数据 → 提示词素材（ctx.data 给的是 deepcopy 快照，随便拼）
# ====================================================================
def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原始长度 {len(text)} 字符）"


def format_tasks(tasks, limit: int) -> str:
    """任务快照 → 紧凑清单（待办在前，带截止日期标注）"""
    lines = []
    for t in tasks or []:
        title = str(t.get("title") or "").strip() or "（无标题）"
        done = bool(t.get("done"))
        deadline = str(t.get("deadline") or "").strip()
        focus = t.get("focus_sessions") or 0
        mark = "[已完成]" if done else "[待办]"
        tail = f"，截止 {deadline}" if deadline else ""
        tail += f"，专注 {focus} 个番茄" if focus else ""
        lines.append(f"- {mark} {title}{tail}")
    if not lines:
        return "（暂无任务）"
    return _truncate("\n".join(lines), limit)


def format_fragments(fragments, limit: int) -> str:
    """碎片快照 → 按类别分组的片段清单"""
    groups = {}
    for f in fragments or []:
        cat = str(f.get("category") or f.get("type") or "text")
        content = str(f.get("content") or "").strip()
        if content:
            groups.setdefault(cat, []).append(content.splitlines()[0][:80])
    if not groups:
        return "（暂无碎片）"
    lines = []
    for cat, items in groups.items():
        lines.append(f"[{cat}] 共 {len(items)} 条：")
        lines.extend(f"  - {it}" for it in items[:20])
    return _truncate("\n".join(lines), limit)


def format_notes(notes, limit: int) -> str:
    """笔记快照 → 标题 + 正文前几行"""
    lines = []
    for n in notes or []:
        title = str(n.get("title") or "").strip() or "（无标题）"
        head = " ".join(str(n.get("content") or "").split())[:120]
        lines.append(f"- {title}：{head}" if head else f"- {title}")
    if not lines:
        return "（暂无笔记）"
    return _truncate("\n".join(lines), limit)


def build_data_block(ctx, kind: str, limit: int) -> str:
    """按指令类型取对应数据并格式化（kind: tasks/fragments/weekly）"""
    if kind == "tasks":
        return f"以下是应用内的全部任务数据：\n{format_tasks(ctx.data.tasks(), limit)}"
    if kind == "fragments":
        return (f"以下是应用内的全部碎片（随手记片段）数据：\n"
                f"{format_fragments(ctx.data.fragments(), limit)}")
    # weekly：三样全上
    return ("以下是应用内工作相关的全部数据。\n\n"
            f"== 任务 ==\n{format_tasks(ctx.data.tasks(), limit)}\n\n"
            f"== 碎片 ==\n{format_fragments(ctx.data.fragments(), limit)}\n\n"
            f"== 笔记 ==\n{format_notes(ctx.data.notes(), limit)}")


# 快捷指令：(按钮文案, 数据类型, 指令全文)
QUICK_COMMANDS = (
    ("总结任务", "tasks",
     "请总结我的任务：待办有哪些、截止日期紧不紧、有没有拖延风险，"
     "最后给 1-3 条优先级建议。"),
    ("整理碎片", "fragments",
     "请把我记的碎片按主题归类整理，指出哪些看起来可以转成任务或笔记。"),
    ("本周小结", "weekly",
     "请根据下面的任务、碎片、笔记数据，写一段本周工作小结"
     "（分「做了什么 / 进行中 / 建议」三节）。"),
)


# ====================================================================
# OpenAI 兼容协议（/chat/completions，非流式）
# ====================================================================
def build_request(cfg: dict, messages: list, max_tokens=None):
    """配置 + 消息 → (url, headers, body)；配置不完整返回 (None, None, 错误)"""
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    model = str(cfg.get("model") or "").strip()
    if not base:
        return None, None, "后端地址为空：请在「后端设置」里填 base_url"
    if not model:
        return None, None, "模型名为空：请在「后端设置」里填模型名"
    if not (base.startswith("http://") or base.startswith("https://")):
        return None, None, f"base_url 必须以 http:// 或 https:// 开头：{base}"
    url = f"{base}/chat/completions"
    headers = {}
    key = str(cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "messages": messages,
            "temperature": 0.4, "stream": False}
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    return url, headers, body


def parse_reply(result: dict):
    """桥结果 → (回复文本, None) 或 (None, 错误描述)"""
    if not result.get("ok"):
        err = str(result.get("error") or "未知错误")
        detail = (result.get("body") or "").strip()[:200]
        hint = ""
        if "HTTP 401" in err or "HTTP 403" in err:
            hint = ("（key 缺失或无效？本地服务若要求认证，可在其配置里关闭；"
                    "更简单的做法：用下方「本地推理」自己拉起一个无认证服务）")
        elif "HTTP 404" in err:
            hint = ("（base_url 或模型名不对？确认端口上跑的确实是 OpenAI "
                    "兼容服务——llama-server 应以 .../v1 结尾）")
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（本地模型首次加载较慢，可重试一次）"
        elif "refused" in err.lower():
            hint = "（端口没有服务在听——服务没启动，或端口号填错了）"
        return None, f"请求失败：{err}{hint}" + (f"\n{detail}" if detail else "")
    try:
        data = json.loads(result.get("body") or "")
        reply = data["choices"][0]["message"]["content"]
        return str(reply).strip(), None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"响应格式不符合 OpenAI 协议：{exc!r}"


# ====================================================================
# 本地 llama-server 生命周期（模块级单例：页面重建不丢服务状态）
# ====================================================================
class LocalServerManager:
    """QProcess 拉起 llama-server + 探活 + JobObject 防孤儿。

    状态机：stopped → starting →（探活通过）ready /（退出或失败）stopped·error
    状态变化回调所有订阅者（页面据此刷新状态行与按钮可用性）。
    探活走宿主网络桥（POST 1-token 请求），与对话同一条受控通道。
    """

    def __init__(self):
        self._proc = None            # QProcess（模块级持有，页面无关）
        self._ctx = None             # 探活用的 PluginContext
        self._timer = None           # 探活 QTimer
        self._deadline = 0.0         # 探活总超时（time.monotonic 秒）
        self._busy_probe = False     # 上一次探活未返回
        self.port = 0
        self.status = "stopped"      # stopped / starting / ready / error
        self.detail = ""
        self._listeners = []         # callable(status, detail)

    # ---- 订阅 ----
    def add_listener(self, fn):
        self._listeners.append(fn)
        fn(self.status, self.detail)          # 立即同步一次当前状态

    def remove_listener(self, fn):
        """页面销毁时退订（rescan 重建页面会反复构造 AiChatPage）"""
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def _emit(self, status: str, detail: str = ""):
        self.status, self.detail = status, detail
        for fn in list(self._listeners):
            try:
                fn(status, detail)
            except Exception:                 # noqa: BLE001 - 回调异常不反噬
                pass

    # ---- 启动 ----
    def start(self, ctx, exe: str, gguf: str, port: int):
        if self._proc is not None:
            self._emit("starting", "服务已在启动/运行中")
            return
        exe, gguf = exe.strip().strip('"'), gguf.strip().strip('"')
        if not os.path.isfile(exe):
            self._emit("error", f"llama-server 程序不存在：{exe}")
            return
        if not os.path.isfile(gguf):
            self._emit("error", f"模型文件不存在：{gguf}")
            return
        self._ctx = ctx
        self.port = int(port)

        proc = QProcess()
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyRead.connect(self._drain_output)     # 防管道积压卡死
        proc.started.connect(lambda: assign_to_job(proc.processId()))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(
            lambda err: self._emit("error", f"进程错误：{err}"))
        proc.start(exe, ["-m", gguf, "--host", "127.0.0.1",
                         "--port", str(self.port),
                         "-ngl", "99", "-c", "8192"])
        self._proc = proc
        self._emit("starting",
                   f"启动中…（首次加载模型可能需要几十秒）端口 {self.port}")

        # 探活循环
        import time
        self._deadline = time.monotonic() + PROBE_TIMEOUT_S
        self._timer = QTimer()
        self._timer.setInterval(PROBE_INTERVAL_MS)
        self._timer.timeout.connect(self._probe_once)
        self._timer.start()
        QTimer.singleShot(0, self._probe_once)          # 先立刻探一次

    # ---- 探活 ----
    def _probe_once(self):
        import time
        if self.status == "ready" or self._proc is None:
            self._stop_probe()
            return
        if time.monotonic() > self._deadline:
            self._stop_probe()
            self._emit("error",
                       f"启动超时（{PROBE_TIMEOUT_S}s 内未就绪）。"
                       f"可在插件目录 logs 里查 llama-server 输出。")
            return
        if self._busy_probe or self._ctx is None:
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
                self._emit("ready", f"本地服务就绪（127.0.0.1:{self.port}）")
            # 未就绪（连接拒绝=模型加载中）→ 等下一轮

        try:
            self._ctx.http_post_json_async(url, {}, body, 8.0, on_done)
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
        if self.status == "ready":
            self._emit("stopped", "本地服务已停止")
        elif code == 0:
            self._emit("stopped", "本地服务已退出")
        else:
            self._emit("error", f"本地服务异常退出（code={code}）"
                                f"——常见原因：显存不足 / 端口被占 / gguf 损坏")
        del was

    # ---- 停止 ----
    def stop(self):
        if self._proc is None:
            self._emit("stopped", "本地服务未在运行")
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


# 模块级单例：页面因「重新扫描」重建时服务与状态不丢
LOCAL_SERVER = LocalServerManager()


# ====================================================================
# 输入框：Enter 发送，Shift+Enter 换行（与 WorkBuddy 输入框一致）
# ====================================================================
class ChatInput(QPlainTextEdit):
    """Enter 直发；Shift+Enter 保留默认换行行为"""

    submit_requested = pyqtSignal()

    def keyPressEvent(self, event):
        from PyQt6.QtGui import QKeyEvent
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


# ====================================================================
# 主页面（嵌入主窗口导航；create_page 返回它）
# ====================================================================
class AiChatPage(QWidget):
    """聊天页：设置卡（收起）+ 消息流 + 快捷指令 + 输入区 + 状态行"""

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._cfg = load_config(ctx)
        self._history = []          # 成功轮次 [{"role","content"}, ...]
        self._pending_user = ""     # 在途请求的用户消息（成功后落进历史）
        self._busy = False

        root = QVBoxLayout(self)
        self.setObjectName("pluginPage")   # 吃主窗口 QSS 的实底（theme.py）
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---- 后端设置卡（默认收起；内容超高时卡内纵向滚动，不挤爆页面）----
        # 内容体放在 QScrollArea 里：本卡两个区块共 9 行输入 + 2 行按钮，
        # 自然高度 ~530px，主窗口偏矮时（最小 920×620）布局会把卡片压瘪、
        # 把消息流挤没，底部按钮被裁掉（2026-09-27 用户反馈）。
        # 卡片最高 430px，超出部分卡内滚动；QSS 已有 QScrollArea 视口
        # 透明规则，玻璃卡底色不受影响。
        self._settings_card = QFrame(self)
        self._settings_card.setObjectName("glassCard")
        card_lay = QVBoxLayout(self._settings_card)
        card_lay.setContentsMargins(16, 14, 16, 14)
        card_lay.setSpacing(0)

        _settings_body = QWidget()          # 卡内滚动内容体
        form = QVBoxLayout(_settings_body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        # 区块 A：在线 / 常驻后端
        cap_a = QLabel("后端服务")
        cap_a.setObjectName("sectionLabel")
        form.addWidget(cap_a)
        preset_row = QHBoxLayout()
        preset_lab = QLabel("预设")
        preset_lab.setObjectName("fieldLabel")
        preset_row.addWidget(preset_lab)
        self._preset = QComboBox()
        for name, _url, _model, _tip in BACKEND_PRESETS:
            self._preset.addItem(name)
        self._preset.addItem("自定义")
        self._preset.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self._preset, 1)
        form.addLayout(preset_row)

        self._url_edit = QLineEdit(str(self._cfg.get("base_url") or ""))
        self._url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        self._key_edit = QLineEdit(str(self._cfg.get("api_key") or ""))
        self._key_edit.setPlaceholderText("云端 API key（本地服务留空）")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._model_edit = QLineEdit(str(self._cfg.get("model") or ""))
        self._model_edit.setPlaceholderText("模型名，如 deepseek-chat")
        for label, widget in (("地址", self._url_edit),
                              ("Key", self._key_edit),
                              ("模型", self._model_edit)):
            form.addLayout(self._field_row(label, widget))

        from src.plugin_ui import make_separator
        form.addWidget(make_separator())

        # 区块 B：本地推理（浏览 gguf，插件自己拉起 llama-server）
        cap_b = QLabel("本地推理（自带服务，无需 key）")
        cap_b.setObjectName("sectionLabel")
        form.addWidget(cap_b)
        form.addWidget(make_hint_label(
            "选择 llama.cpp 的 llama-server.exe 与 .gguf 模型文件，"
            "点「启动」由本插件拉起本地服务（默认端口 8093，"
            "退出 FloatPulse 时自动结束）。"))

        self._exe_edit = QLineEdit(str(self._cfg.get("local_server_exe") or ""))
        self._exe_edit.setPlaceholderText("llama-server.exe 路径（可浏览选择）")
        self._gguf_edit = QLineEdit(str(self._cfg.get("local_gguf") or ""))
        self._gguf_edit.setPlaceholderText("模型文件路径（.gguf）")
        self._port_edit = QLineEdit(str(self._cfg.get("local_port") or 8093))
        self._port_edit.setFixedWidth(72)
        form.addLayout(self._field_row(
            "程序", self._exe_edit,
            ("浏览…", lambda: self._browse_file(self._exe_edit, "程序 (*.exe)"))))
        form.addLayout(self._field_row(
            "模型", self._gguf_edit,
            ("浏览…", lambda: self._browse_file(
                self._gguf_edit, "GGUF 模型 (*.gguf)"))))
        port_row = self._field_row("端口", self._port_edit, stretch=False)
        form.addLayout(port_row)

        local_btn_row = QHBoxLayout()
        self._local_btn = QPushButton("启动本地服务")
        self._local_btn.setObjectName("primaryBtn")
        self._local_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._local_btn.clicked.connect(self._toggle_local_server)
        local_btn_row.addWidget(self._local_btn)
        self._local_status = make_hint_label("本地服务未运行")
        local_btn_row.addWidget(self._local_status, 1)
        form.addLayout(local_btn_row)

        # 保存并测试
        save_row = QHBoxLayout()
        self._save_btn = QPushButton("保存并测试连接")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_and_test)
        save_row.addWidget(self._save_btn)
        save_row.addStretch()
        form.addLayout(save_row)

        # 滚动包装：内容体 → 卡片（纵向滚动兜底）
        _settings_scroll = QScrollArea(self._settings_card)
        _settings_scroll.setWidgetResizable(True)
        _settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        _settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _settings_scroll.setWidget(_settings_body)
        card_lay.addWidget(_settings_scroll)
        self._settings_card.setMaximumHeight(430)

        root.addWidget(self._settings_card)
        self._settings_card.setVisible(False)

        # ---- 消息流（滚动区）----
        self._stream_host = QWidget(self)
        self._stream = QVBoxLayout(self._stream_host)
        self._stream.setContentsMargins(0, 0, 0, 0)
        self._stream.setSpacing(8)
        self._stream.addStretch()
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._stream_host)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)

        # ---- 快捷指令 + 设置开关 ----
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for text, kind, prompt in QUICK_COMMANDS:
            btn = QPushButton(text, self)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=kind, p=prompt:
                                self.send_quick(k, p))
            quick_row.addWidget(btn)
        quick_row.addStretch()
        self._toggle_btn = QPushButton("⚙ 后端设置", self)
        self._toggle_btn.setObjectName("secondaryBtn")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_settings)
        quick_row.addWidget(self._toggle_btn)
        root.addLayout(quick_row)

        # ---- 输入区（Enter 发送 / Shift+Enter 换行）----
        input_row = QHBoxLayout()
        self._input = ChatInput(self)
        self._input.setPlaceholderText(
            "问点什么…（Enter 发送，Shift+Enter 换行）")
        self._input.setFixedHeight(68)
        input_row.addWidget(self._input, 1)
        self._send_btn = QPushButton("发送", self)
        self._send_btn.setObjectName("primaryBtn")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self._send_btn)
        root.addLayout(input_row)

        self._status = make_hint_label("")
        root.addWidget(self._status)

        self._input.submit_requested.connect(self._on_send_clicked)

        self.add_bubble(
            "AI", "我在。点快捷指令让我读应用内数据做总结，或直接输入问题。\n"
                  "首次使用：点右下角「⚙ 后端设置」选好后端，"
                  "或用「本地推理」选 gguf 一键启动。")

        # 本地服务状态跟随（模块级单例：页面重建后状态不丢）
        LOCAL_SERVER.add_listener(self._on_local_status)
        # 页面销毁时退订，避免 _listeners 里累积已销毁页面的死引用
        self.destroyed.connect(
            lambda: LOCAL_SERVER.remove_listener(self._on_local_status))

    # ---------------- 小工具 ----------------
    @staticmethod
    def _field_row(label_text, widget, browse=None, stretch=True) -> QHBoxLayout:
        row = QHBoxLayout()
        lab = QLabel(label_text)
        lab.setObjectName("fieldLabel")
        lab.setFixedWidth(36)
        row.addWidget(lab)
        row.addWidget(widget, 1 if stretch else 0)
        if browse is not None:
            text, slot = browse
            btn = QPushButton(text)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        elif not stretch:
            row.addStretch(1)     # 定宽控件不带 stretch：label 贴左、右留白
        return row

    def _browse_file(self, line_edit: QLineEdit, name_filter: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", name_filter)
        if path:
            line_edit.setText(path)

    # ---------------- 气泡 ----------------
    def add_bubble(self, role: str, text: str):
        """往消息流追加一张卡片（角色行 + 全文，对话流式）

        AI 回复额外带一个「📥 存为笔记」按钮——这是插件受限写能力
        （``ctx.write``，需 manifest 声明 ``capabilities: ["write"]``）的
        实际用途：把整理结果一键落进笔记，不必让用户手动复制粘贴。
        未声明能力时按钮点了也只会提示失败（宿主侧安全拒绝），
        因此这里按 has_capability 决定是否显示，避免给用户假按钮。
        """
        card = QFrame(self._stream_host)
        card.setObjectName("glassCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(4)
        role_label = QLabel("你" if role == "你"
                            else "AI 助手" if role == "AI" else role)
        role_label.setObjectName("fieldLabel")
        body_label = QLabel(text)
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(role_label)
        box.addWidget(body_label)

        if role == "AI" and text.strip() and self._can_write():
            row = QHBoxLayout()
            row.addStretch()
            save_btn = QPushButton("📥 存为笔记")
            save_btn.setObjectName("secondaryBtn")
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setToolTip("把这条回复存成一条笔记（标题自动取自首行）")
            save_btn.clicked.connect(
                lambda _checked=False, t=text, b=save_btn: self._save_as_note(t, b))
            row.addWidget(save_btn)
            box.addLayout(row)

        # 插到末尾的 stretch 之前，保证消息从顶部排布
        self._stream.insertWidget(self._stream.count() - 1, card)
        self._scroll_to_bottom()

    def _can_write(self) -> bool:
        """本插件是否被授权写入（manifest 声明了 write 能力）"""
        try:
            return bool(self._ctx.has_capability("write"))
        except Exception:                            # noqa: BLE001
            return False

    def _save_as_note(self, text: str, btn=None):
        """把 AI 回复存成一条笔记（标题取首行，截断到 40 字）"""
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        head = lines[0] if lines else "AI 助手回复"
        title = head[:40] + ("…" if len(head) > 40 else "")
        nid = self._ctx.write.add_note(title, text)
        if nid:
            self._status.setText(f"已存为笔记：{title}")
            if btn is not None:
                btn.setEnabled(False)
                btn.setText("✅ 已存为笔记")
            self._ctx.show_toast("已存为笔记（可在笔记页查看）")
        else:
            self._status.setText("存入笔记失败（详见 app.log）")

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    # ---------------- 发送 ----------------
    def _on_send_clicked(self):
        if self._busy:
            self._status.setText("正在处理上一条…")
            return
        text = self._input.toPlainText().strip()
        if not text:
            self._status.setText("先输入内容再发送")
            return
        self._input.clear()
        self._dispatch(text, None, text)

    def send_quick(self, kind: str, prompt: str):
        """快捷指令：指令 + 应用内数据一起发给模型"""
        if self._busy:
            self._status.setText("正在处理上一条…")
            return
        data = build_data_block(self._ctx, kind,
                                int(self._cfg.get("max_data_chars") or 6000))
        self._dispatch(prompt, data, prompt)

    def _dispatch(self, display_text: str, data_block, prompt: str):
        """统一发送入口；data_block 非空 = 快捷指令（附加数据）"""
        user_content = prompt
        if data_block:
            user_content = f"{prompt}\n\n{data_block}"
        messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                    + self._history
                    + [{"role": "user", "content": user_content}])
        url, headers, body = build_request(self._cfg, messages)
        if url is None:
            self.add_bubble("提示", body)      # body 在此路径是错误文案
            self._settings_card.setVisible(True)
            return

        self.add_bubble("你", display_text)
        self._set_busy(True, "思考中…")
        self._pending_user = user_content
        ok = self._ctx.http_post_json_async(
            url, headers, body, timeout=120.0, on_done=self._on_reply)
        if not ok:
            # 桥拒绝时也会回调一次 ok=False 的结果，这里只兜底恢复状态
            self._set_busy(False)

    def _on_reply(self, result: dict):
        self._set_busy(False)
        reply, err = parse_reply(result)
        if err:
            self.add_bubble("提示", err)
            return
        self.add_bubble("AI", reply)
        # 成功轮次才进历史；超限丢最旧的（保留偶数条，问答成对）
        self._history.append({"role": "user",
                              "content": self._pending_user})
        self._history.append({"role": "assistant", "content": reply})
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]
        self._pending_user = ""

    # ---------------- 设置卡 ----------------
    def _toggle_settings(self):
        self._settings_card.setVisible(not self._settings_card.isVisible())

    def _apply_preset(self, index: int):
        """选预设 → 自动填地址与模型（自定义 = 不动现有值）"""
        if index < 0 or index >= len(BACKEND_PRESETS):
            return
        _name, url, model, _tip = BACKEND_PRESETS[index]
        self._url_edit.setText(url)
        if model:
            self._model_edit.setText(model)

    def _collect_settings(self) -> bool:
        """读设置卡 → 配置；有变化则落盘。返回是否有变化"""
        try:
            port = int(self._port_edit.text().strip() or 8093)
            if not (1024 <= port <= 65535):
                port = 8093
        except ValueError:
            port = 8093
        new = {
            "base_url": self._url_edit.text().strip(),
            "api_key": self._key_edit.text().strip(),
            "model": self._model_edit.text().strip(),
            "max_data_chars": self._cfg.get("max_data_chars", 6000),
            "local_server_exe": self._exe_edit.text().strip(),
            "local_gguf": self._gguf_edit.text().strip(),
            "local_port": port,
        }
        if new == self._cfg:
            return False
        self._cfg = new
        return save_config(self._ctx, new)

    def _save_and_test(self):
        """保存全部配置 + 发一个 1-token 探活请求，给用户明确反馈"""
        changed = self._collect_settings()
        self._status.setText("配置已保存" if changed else "配置未变化")
        url, headers, body = build_request(
            self._cfg, [{"role": "user", "content": "ping"}], max_tokens=1)
        if url is None:
            self._status.setText(f"⚠ {body}")     # body 在此路径是错误文案
            return
        self._save_btn.setEnabled(False)
        old_text = self._save_btn.text()
        self._save_btn.setText("测试中…")

        def on_done(result):
            self._save_btn.setEnabled(True)
            self._save_btn.setText(old_text)
            if result.get("ok"):
                self._status.setText(
                    f"✓ 连接成功（{self._cfg.get('model')}），可以开始对话")
            else:
                _, err = parse_reply(result)
                self._status.setText(f"✗ {err or '连接失败'}")

        self._ctx.http_post_json_async(url, headers, body, 20.0, on_done)

    # ---------------- 本地推理 ----------------
    def _toggle_local_server(self):
        if LOCAL_SERVER.running:
            LOCAL_SERVER.stop()
            return
        # 先把设置卡里的路径/端口落盘，再启动
        self._collect_settings()
        LOCAL_SERVER.start(self._ctx,
                           self._cfg.get("local_server_exe", ""),
                           self._cfg.get("local_gguf", ""),
                           int(self._cfg.get("local_port") or 8093))

    def _on_local_status(self, status: str, detail: str):
        """本地服务状态 → 状态行 + 按钮文案；就绪时自动接上该后端"""
        self._local_status.setText(detail or status)
        if status == "ready":
            self._local_btn.setText("停止本地服务")
            base = f"http://127.0.0.1:{LOCAL_SERVER.port}/v1"
            self._url_edit.setText(base)
            if not self._model_edit.text().strip():
                self._model_edit.setText("local")
            self._collect_settings()
            self._status.setText("✓ 本地服务就绪，已自动切换到本地后端")
        elif status == "starting":
            self._local_btn.setText("取消 / 停止")
        else:
            self._local_btn.setText("启动本地服务")

    # ---------------- 杂项 ----------------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._status.setText(text)

    def _clear_chat(self):
        self._history = []
        self._pending_user = ""
        while self._stream.count() > 1:          # 留着末尾 stretch
            item = self._stream.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.add_bubble("AI", "对话已清空。")


# ====================================================================
# 动作与插件
# ====================================================================
class ChatAction(BallAction):
    id = f"{PLUGIN_ID}.chat"
    title = "🤖 AI 助手"

    def run(self, ctx: PluginContext):
        # 热键路径：run() 在原生事件过滤器里被调用，UI 操作必须延后到
        # 下一轮事件循环，防重入（项目铁律，见插件开发说明第 12 节）
        QTimer.singleShot(0, lambda: self._go(ctx))

    def _go(self, ctx: PluginContext):
        win = ctx.parent_window()
        show = getattr(win, "show_plugin_page", None)
        if callable(show) and show(PAGE_KEY):
            return
        ctx.open_main_window()    # 兜底：页面不在（插件页未注入）时至少开窗口


class AiAssistantPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "AI 助手"
    version = "1.1.0"

    def create_actions(self, ctx) -> list:
        return [ChatAction()]

    def create_page(self, ctx: PluginContext):
        return AiChatPage(ctx)
