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
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import make_hint_label

PLUGIN_ID = "ai-assistant"
PAGE_KEY = f"plugin:{PLUGIN_ID}"       # 主窗口页面 key（与 loader 约定一致）

# ---------------- 配置 ----------------
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    # 双后端模型（2026-09-28 用户要求「云端和本地做一个选择入口」）：
    # backend_mode 决定对话实际调用哪套后端；两套配置各自独立保存，
    # 切换/启动本地都不再覆盖云端字段（旧版会冲掉用户填的云端地址）
    "backend_mode": "cloud",      # cloud=云端 API / local=本机 llama-server
    "cloud_base_url": "https://api.deepseek.com/v1",
    "cloud_api_key": "",
    "cloud_model": "deepseek-chat",
    "max_data_chars": 6000,       # 单块数据塞进提示词的最大字符数
    # 规则库（2026-09-28 用户要求「入口让用户自己编辑，不写死」）：
    # [{"text": 规则文本, "enabled": 是否启用}, ...]，随 config.json 落盘，
    # 已启用规则由 build_system_prompt 追加到系统提示词末尾（双后端共用）
    "custom_rules": [],
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


def build_system_prompt(custom_rules, can_manage: bool = False) -> str:
    """基础系统提示词 + 规则库中已启用的用户规则（+ 动作协议）。

    规则库是用户在页面上自己编辑的（config.json 的 custom_rules），
    插件不预置任何条目。脏数据（非 dict / 空文本 / 缺键）一律宽容
    处理：空文本与停用条目跳过，纯字符串条目按启用处理（兼容手改
    配置）。无生效规则 → 返回基础 SYSTEM_PROMPT，行为与旧版一致。

    ``can_manage``：插件是否被授权改删数据（manifest 声明 manage）。
    未授权时**不下发动作协议**——否则模型会承诺一堆做不到的操作。
    """
    rules = []
    for r in custom_rules or []:
        if isinstance(r, dict):
            if not r.get("enabled", True):
                continue
            text = str(r.get("text") or "").strip()
        else:
            text = str(r or "").strip()
        if text:
            rules.append(text)
    prompt = SYSTEM_PROMPT
    if can_manage:
        prompt += ACTION_PROTOCOL
    if not rules:
        return prompt
    joined = "\n".join(f"{i}. {t}" for i, t in enumerate(rules, 1))
    return (prompt
            + "\n\n以下是用户在「规则库」里自定义的规则，每次回答都必须遵守：\n"
            + joined)

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
                # 旧版单后端字段 → 云端字段一次性迁移（v1.3 双后端模型）。
                # 旧版「本地 ready 覆盖 base_url」的缺陷会把 127.0.0.1 写进
                # base_url——这类值不是真实云端配置，迁移时跳过。
                if "cloud_base_url" not in stored:
                    old_url = str(stored.get("base_url") or "").strip()
                    if old_url and "127.0.0.1" not in old_url \
                            and "localhost" not in old_url:
                        cfg["cloud_base_url"] = old_url
                    if stored.get("api_key"):
                        cfg["cloud_api_key"] = stored["api_key"]
                    old_model = str(stored.get("model") or "").strip()
                    if old_model and old_model != "local":
                        cfg["cloud_model"] = old_model
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


def format_knowledge(items, limit: int) -> str:
    """知识库快照 → 「编号 + 首行摘要」清单

    编号是知识库页显示的编号，改 / 删时用它指定段落。每段只给摘要而不是
    全文：知识库是长文文档（几百段很常见），全量塞进提示词会挤掉真正需要
    的上下文；模型要细节时可以让用户指定编号。
    """
    lines = []
    for it in items or []:
        num = it.get("num")
        text = it.get("text") or it.get("preview") or ""
        head = " ".join(str(text).split())[:60]
        lines.append(f"[{num}] {head}")
    if not lines:
        return "（知识库为空）"
    return _truncate("\n".join(lines), limit)


def build_data_block(ctx, kind: str, limit: int) -> str:
    """按指令类型取对应数据并格式化（kind: tasks/fragments/weekly/knowledge）"""
    if kind == "tasks":
        return f"以下是应用内的全部任务数据：\n{format_tasks(ctx.data.tasks(), limit)}"
    if kind == "fragments":
        return (f"以下是应用内的全部碎片（随手记片段）数据：\n"
                f"{format_fragments(ctx.data.fragments(), limit)}")
    if kind == "knowledge":
        return ("以下是知识库（float_data/知识库.docx）的全部段落，"
                "方括号里是编号：\n"
                f"{format_knowledge(ctx.data.knowledge(), limit)}\n\n"
                "要改 / 删某一段就用它的编号；不确定是哪一段就先向用户确认，"
                "不要猜。")
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
    ("查知识库", "knowledge",
     "请根据下面的知识库段落，先用 3-5 句话概括它都在讲什么，"
     "再列出其中看起来最值得展开或最可能需要更新的编号。"),
    ("本周小结", "weekly",
     "请根据下面的任务、碎片、笔记数据，写一段本周工作小结"
     "（分「做了什么 / 进行中 / 建议」三节）。"),
)


# ====================================================================
# 动作协议（2026-09-28 用户要求：AI 能按指令操控应用内数据）
# ====================================================================
# 设计：AI 不直接动手。它在回复末尾输出一个 ```actions 块，插件解析 →
# 校验 → 分级处理（新增类直接执行；改删类弹「待确认卡」，用户点执行才落库）。
#
# 为什么不用原生 function calling：本地 Qwen3-4B 量化版对 JSON schema 的
# 遵循度不稳，云端 DeepSeek 支持好——用提示词约定的动作块两端通吃。
# 代价是小模型偶尔输出格式错误：那种情况**宁可整块不执行**并如实提示，
# 也不去猜它的意图（猜错就是改了用户的数据）。
ACTION_OPS = {
    "add_task": "write", "add_fragment": "write", "add_note": "write",
    "add_knowledge": "write",
    "complete_task": "manage", "reopen_task": "manage",
    "update_task": "manage", "delete_task": "manage",
    "update_fragment": "manage", "delete_fragment": "manage",
    "update_note": "manage", "delete_note": "manage",
    "update_knowledge": "manage", "delete_knowledge": "manage",
}
MAX_ACTIONS = 10          # 单轮动作数上限：防模型刷屏式输出

# 动作 → 目标数据源（用于把 id 校验到快照里的真实记录）
# 知识库的 id 键是 ``num``：知识库段落用「编号」寻址（位置型标识）
_OP_TARGET = {
    "complete_task": ("tasks", "task_id", "任务"),
    "reopen_task": ("tasks", "task_id", "任务"),
    "update_task": ("tasks", "task_id", "任务"),
    "delete_task": ("tasks", "task_id", "任务"),
    "update_fragment": ("fragments", "fragment_id", "碎片"),
    "delete_fragment": ("fragments", "fragment_id", "碎片"),
    "update_note": ("notes", "note_id", "笔记"),
    "delete_note": ("notes", "note_id", "笔记"),
    "update_knowledge": ("knowledge", "num", "知识"),
    "delete_knowledge": ("knowledge", "num", "知识"),
}

# 知识库改删失败时要提示的常见原因：编号会随删除漂移、docx 还能被 Word
# 外部编辑，所以失败多半是「引用过期」而不是「操作写错了」。
_KB_FAIL_HINT = ("（该段内容可能已变化，或知识库被外部改动过——"
                 "请先到知识库页点「🔄 重新加载」再试）")

ACTION_PROTOCOL = (
    "\n\n== 你可以执行的应用操作 ==\n"
    "当用户**明确要求**修改应用内数据时，除文字回答外，还要在回复**最后**"
    "输出一个动作块（普通问答不要输出）：\n"
    "```actions\n"
    '{"actions":[{"op":"complete_task","id":3,"why":"用户要求标记完成"}]}\n'
    "```\n"
    "可用 op（需要 id 的，id 只能取自上面数据里的真实编号，**禁止编造**）：\n"
    "- add_task{title,note?,deadline?} / add_fragment{content} / "
    "add_note{title,content}\n"
    "- complete_task{id} / reopen_task{id} —— 标记完成 / 取消完成\n"
    "- update_task{id,title?,note?,deadline?} / delete_task{id}\n"
    "- update_fragment{id,content?,source?} / delete_fragment{id}\n"
    "- update_note{id,title?,content?} / delete_note{id}\n"
    "- add_knowledge{content} —— 往知识库（知识库.docx）追加一段"
    "（每段至少 4 个字，过短会被拒绝）\n"
    "- update_knowledge{id,content} / delete_knowledge{id} —— 知识库的 id "
    "就是它方括号里的编号\n"
    "规则：①只在用户明确要求改动时输出动作；②不确定是哪条记录就先问，"
    "不要猜；③一次最多 10 条；④动作块之外照常用文字说明你做了什么。"
)


def describe_action(op: str, args: dict, snapshot: dict | None) -> str:
    """动作 → 人类可读描述（确认卡与结果气泡共用）

    描述会进 QLabel（不渲染 Markdown），所以只用纯文本与【】这类全角符号。
    """
    snap = snapshot or {}

    def _label(kind_key) -> str:
        if kind_key is None:
            return ""
        src, id_key, name = kind_key
        rid = args.get("id")
        for item in snap.get(src) or []:
            if item.get(id_key) == rid:
                head = str(item.get("title") or "").strip()
                if not head:
                    head = " ".join(str(item.get("content") or "").split())[:24]
                if not head:            # 知识库段落用的是 text
                    head = " ".join(str(item.get("text") or "").split())[:24]
                return f"{name} #{rid}「{head}」" if head else f"{name} #{rid}"
        return f"{name} #{rid}"

    if op == "add_task":
        return f"新增任务「{str(args.get('title') or '').strip()}」"
    if op == "add_fragment":
        head = " ".join(str(args.get("content") or "").split())[:24]
        return f"记一条碎片「{head}」"
    if op == "add_note":
        return f"新增笔记「{str(args.get('title') or '').strip()}」"
    if op == "add_knowledge":
        head = " ".join(str(args.get("content") or "").split())[:24]
        return f"往知识库追加一段「{head}」"
    if op == "complete_task":
        return f"把{_label(_OP_TARGET[op])}标记为已完成"
    if op == "reopen_task":
        return f"把{_label(_OP_TARGET[op])}恢复为未完成"
    if op == "update_knowledge":
        return f"改写{_label(_OP_TARGET[op])}的正文"
    if op in ("update_task", "update_fragment", "update_note"):
        fields = [k for k in ("title", "note", "deadline", "content", "source")
                  if args.get(k) is not None]
        return f"修改{_label(_OP_TARGET[op])}（{'、'.join(fields) or '字段'}）"
    if op.startswith("delete_"):
        # 用【】而不是 Markdown 的 **：描述会进 QLabel，星号不会被渲染
        return f"【删除】{_label(_OP_TARGET[op])}"
    return f"{op} {args}"


def _order_actions(actions):
    """动作执行顺序：知识库删除排到最后，且按编号**降序**执行。

    知识库是位置型标识——删掉第 3 段，原来的第 5 段就变成第 4 段。同一批里
    有多个删除时，按升序执行第二个编号就指向了别的段落（宿主有指纹校验会
    拒绝，但那是「本该成功却失败」）。降序执行则前面的编号不受影响。

    其余动作保持原顺序（稳定排序），结果卡里的顺序与模型给的顺序一致。
    """
    def key(pair):
        idx, act = pair
        if act.get("op") == "delete_knowledge":
            num = (act.get("args") or {}).get("id")
            return (1, -(num if isinstance(num, int)
                         and not isinstance(num, bool) else 0))
        return (0,)

    return [act for _, act in sorted(enumerate(actions), key=key)]


def _looks_like_actions(raw: str) -> bool:
    """这段 JSON 是否「长得像动作块」（dict 且含 actions 数组）

    用于兜底识别路径的准入判断：普通问答里出现的 JSON 示例（用户贴一段
    配置问「这是什么」）不能因为我们认得出 JSON 就从正文里删掉。
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("actions"), list)


def _extract_action_json(text: str) -> str:
    """从回复里取出动作块的 JSON 文本；``""`` = 没有动作块

    识别顺序（从明确到宽松）：
      ① ```` ```actions ```` 围栏 —— 协议约定的标准写法，见到即认：**整段**
         内容都当作动作块（哪怕不是对象、哪怕 JSON 坏了），交给调用方报错，
         而不是当正文显示给用户
      ② ```` ```json ```` 围栏 —— 取最后一个「像动作块」的
      ③ 裸 JSON —— 取最后一个「像动作块」的
    只取最后一个：容忍模型在结论后再补一段动作块。
    """
    import re
    fenced = re.findall(r"```actions\s*(.*?)\s*```", text, re.S)
    if fenced:
        return fenced[-1]
    for cand in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)[::-1]:
        if _looks_like_actions(cand):
            return cand
    left, right = text.rfind("{"), text.rfind("}")
    if 0 <= left < right:
        cand = text[left:right + 1]
        if _looks_like_actions(cand):
            return cand
    return ""


def parse_actions(text: str, snapshot: dict | None = None):
    """回复文本 → (干净正文, [动作], [未执行原因])

    每个动作是 ``{"op","args","level","desc"}``；``level`` 决定处理方式
    （write = 直接执行，manage = 需用户确认）。

    容错策略（本地小模型输出不稳）：JSON 坏 / 结构不对 → 整块不产出动作
    并给出原因；单条不合格（未知 op / id 编造 / 参数类型错 / 超额）→ 只丢
    那一条。**任何情况下都不猜**——少做比做错好。

    正文语义：没找到动作块 → 原样返回；找到动作块 → 返回**剥掉动作块后的
    正文（可能是空串）**。空串表示"这条回复除了动作什么都没有"，调用方
    应据此不渲染空气泡，而不是把 JSON 当正文显示出来。
    """
    snap = snapshot or {}
    text = text or ""
    raw = _extract_action_json(text)
    if not raw.strip():
        return text, [], []
    # 正文去掉动作块（用户看的是结论，不是 JSON）
    clean = text.replace(raw, "").strip()
    clean = clean.replace("```actions", "").replace("```json", "")
    clean = clean.replace("```", "").strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return clean, [], [f"动作块不是合法 JSON（{exc}），未执行"]
    if not isinstance(data, dict):
        return clean, [], ["动作块必须是 JSON 对象，未执行"]
    items = data.get("actions")
    if not isinstance(items, list):
        return clean, [], ["动作块缺少 actions 数组，未执行"]

    actions, errors = [], []
    if len(items) > MAX_ACTIONS:
        errors.append(f"动作条数 {len(items)} 超过上限 {MAX_ACTIONS}，"
                      f"只取前 {MAX_ACTIONS} 条")
        items = items[:MAX_ACTIONS]
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"动作必须是对象，跳过：{item!r}")
            continue
        op = str(item.get("op") or "").strip()
        if op not in ACTION_OPS:
            errors.append(f"未知动作「{op}」，跳过")
            continue
        args = {"id": item.get("id")} if "id" in item else {}
        for key in ("title", "note", "deadline", "content", "source"):
            if key in item:
                args[key] = item[key]
        if op in _OP_TARGET:
            rid = args.get("id")
            if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
                errors.append(f"{op} 的 id 不是正整数，跳过：{rid!r}")
                continue
            src, id_key, _name = _OP_TARGET[op]
            items_src = snap.get(src) or []
            target = next((it for it in items_src
                           if it.get(id_key) == rid), None)
            if target is None:
                errors.append(f"{op} 的目标 #{rid} 不在当前数据里"
                              "（可能已删除或编号编造），跳过")
                continue
            if op in ("update_knowledge", "delete_knowledge"):
                # 知识库是位置型 + 外部可编辑的：把快照里的段落指纹一并带上，
                # 宿主比对不上就拒绝——防止编号漂移后改错段落。
                expect = target.get("hash")
                if not isinstance(expect, str) or not expect:
                    errors.append(f"{op} 无法取得第 {rid} 段的内容指纹，跳过")
                    continue
                args["expect_hash"] = expect
                if op == "update_knowledge":
                    val = args.get("content")
                    if not isinstance(val, str) or not val.strip():
                        errors.append("update_knowledge 缺少有效的 content，跳过")
                        continue
        else:
            args.pop("id", None)
            need = "title" if op in ("add_task", "add_note") else "content"
            val = args.get(need)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{op} 缺少有效的 {need}，跳过")
                continue
        for key, val in list(args.items()):
            if key != "id" and val is not None and not isinstance(val, str):
                errors.append(f"{op} 的 {key} 必须是字符串，跳过该条")
                args = None
                break
        if args is None:
            continue
        actions.append({"op": op, "args": args,
                        "level": ACTION_OPS[op],
                        "desc": describe_action(op, args, snap)})
    return clean, actions, errors


# ====================================================================
# OpenAI 兼容协议（/chat/completions，非流式）
# ====================================================================
def build_request(cfg: dict, messages: list, max_tokens=None):
    """配置 + 消息 → (url, headers, body)；配置不完整返回 (None, None, 错误)

    backend_mode 决定调用哪套后端（2026-09-28 双后端模型）：
    - cloud → cloud_base_url/cloud_api_key/cloud_model（OpenAI 兼容 API）
    - local → 本机 llama-server（127.0.0.1:{local_port}/v1，模型名固定
      "local"——llama-server 忽略该字段；服务是否就绪由调用方把关）
    """
    if cfg.get("backend_mode") == "local":
        try:
            port = int(cfg.get("local_port") or 8093)
        except (TypeError, ValueError):
            port = 8093
        base = f"http://127.0.0.1:{port}/v1"
        model = "local"
        key = ""
    else:
        base = str(cfg.get("cloud_base_url") or "").strip().rstrip("/")
        model = str(cfg.get("cloud_model") or "").strip()
        key = str(cfg.get("cloud_api_key") or "").strip()
        if not base:
            return None, None, "云端地址为空：点「⚙ 后端设置」填写云端地址"
        if not model:
            return None, None, "模型名为空：点「⚙ 后端设置」填写模型名"
    if not (base.startswith("http://") or base.startswith("https://")):
        return None, None, f"后端地址必须以 http:// 或 https:// 开头：{base}"
    url = f"{base}/chat/completions"
    headers = {}
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
            hint = ("（云端地址或模型名不对？确认端口上跑的确实是 OpenAI "
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


class BubbleLabel(QLabel):
    """气泡正文标签：宽度贴合内容、高度随换行自适应。

    两个实测坑（2026-09-28 用户反馈「气泡不协调」）：
    ① wordWrap 的 QLabel 在布局里 sizeHint 偏好极窄宽度 → 气泡塌成
       窄条、文字挤成两三字一行；故调用方按字体度量算出「内容自然
       宽度」作下限（见 add_bubble），短消息紧凑、长消息到上限换行。
    ② 卡片走 alignment（不拉伸）时布局只按 sizeHint 定高、不吃
       heightForWidth → 长文案被截断（欢迎语 7 行只出 2 行）；故宽度
       落定/变化时按实际宽度回算高度下限，resize 后自动纠偏。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self):
        w = self.width()
        if w <= 0:
            return
        need = self.heightForWidth(w)
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)


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
        # 云端后端是否可用（2026-09-28 用户要求「断开连接」控制）。
        # True=可发；「断开连接」置 False → 非本地请求被拦下并提示。
        # 本地 llama-server 不受它影响（本地有独立的启停状态机）；
        # 探活成功（保存并测试 / 本地 ready 自动接管）都会把它置回 True。
        self._cloud_active = True
        # 思考动画（2026-09-28 用户要求）：请求在途时消息流里挂一张
        # 「打字中」气泡，三点做往返波；回复到达（或失败）即拆掉。
        # QSS：chatBubbleThinking / chatThinkingDots（theme.py）
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(180)
        self._think_timer.timeout.connect(self._tick_thinking)
        self._think_bubble = None       # 在途气泡 QFrame（无在途时为 None）
        self._think_label = None        # 三点 QLabel（动画帧写给它）
        self._think_phase = 0
        # 已存为笔记的回复文本（气泡右键菜单据此显示「已存为笔记」并禁用）
        self._noted_texts = set()
        # 最近一张待确认卡（改删动作等用户点「执行」；测试与调试用引用）
        self._pending_confirm = None

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

        # 区块 A：云端 API（与本地推理两套配置独立保存，互不覆盖）
        cap_a = QLabel("云端 API")
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

        self._url_edit = QLineEdit(str(self._cfg.get("cloud_base_url") or ""))
        self._url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        self._key_edit = QLineEdit(str(self._cfg.get("cloud_api_key") or ""))
        self._key_edit.setPlaceholderText("API key")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._model_edit = QLineEdit(str(self._cfg.get("cloud_model") or ""))
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

        # 保存并测试 + 断开连接（2026-09-28 用户要求：云端要有启动/暂停式控制）
        save_row = QHBoxLayout()
        self._save_btn = QPushButton("保存并测试连接")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_and_test)
        save_row.addWidget(self._save_btn)
        # 「断开」= 停止用该云端后端发请求（配置保留，不丢用户填的 key）；
        # 重新点「保存并测试连接」探活成功即恢复。本地服务不受影响。
        self._disconnect_btn = QPushButton("断开连接")
        self._disconnect_btn.setObjectName("secondaryBtn")
        self._disconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._disconnect_btn.setToolTip(
            "停用当前云端后端（配置保留）；对话将提示后端不可用，"
            "点「保存并测试连接」可重新接上")
        self._disconnect_btn.clicked.connect(self._disconnect_cloud)
        save_row.addWidget(self._disconnect_btn)
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

        # ---- 规则库卡（2026-09-28 用户要求：入口让用户自己编辑，不写死）----
        # 与后端设置卡同款结构：glassCard + 内容超高卡内滚动。规则条目
        # 存 config.json 的 custom_rules，随「💾 保存规则」落盘；已启用
        # 条目由 build_system_prompt 在每次对话时追加到系统提示词。
        self._rules_rows = []           # [{"wrap","check","edit"}, ...]
        self._rules_card = QFrame(self)
        self._rules_card.setObjectName("glassCard")
        rc_lay = QVBoxLayout(self._rules_card)
        rc_lay.setContentsMargins(16, 14, 16, 14)
        rc_lay.setSpacing(0)

        _rules_body = QWidget()          # 卡内滚动内容体
        rules_outer = QVBoxLayout(_rules_body)
        rules_outer.setContentsMargins(0, 0, 0, 0)
        rules_outer.setSpacing(8)

        cap_r = QLabel("规则库")
        cap_r.setObjectName("sectionLabel")
        rules_outer.addWidget(cap_r)
        # hint 必须开自动换行：QLabel 默认单行 sizeHint 会把内容体撑得
        # 比滚动视口宽，右侧「🗑」按钮和文案会被横向裁掉（离屏实测）
        _rules_hint = make_hint_label(
            "自定义规则会追加到每次对话的系统提示词末尾（云端 / 本地后端"
            "共用），用来固定你的长期偏好。每条一行；勾选＝生效，取消勾选"
            "＝暂停不删除。")
        _rules_hint.setWordWrap(True)
        rules_outer.addWidget(_rules_hint)

        self._rules_form = QVBoxLayout()
        self._rules_form.setSpacing(6)
        rules_outer.addLayout(self._rules_form)

        rules_btn_row = QHBoxLayout()
        self._rule_add_btn = QPushButton("＋ 添加规则", self)
        self._rule_add_btn.setObjectName("secondaryBtn")
        self._rule_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # clicked 会把 checked=False 当首个位置参数传入 → 用 lambda 挡住，
        # 否则 _add_rule_row 的 text 形参吃进 False，凭空多出一行 "False"
        self._rule_add_btn.clicked.connect(
            lambda _checked=False: self._add_rule_row())
        rules_btn_row.addWidget(self._rule_add_btn)
        rules_btn_row.addStretch()
        self._rule_save_btn = QPushButton("💾 保存规则", self)
        self._rule_save_btn.setObjectName("primaryBtn")
        self._rule_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rule_save_btn.clicked.connect(self._save_rules)
        rules_btn_row.addWidget(self._rule_save_btn)
        rules_outer.addLayout(rules_btn_row)

        self._rules_scroll = QScrollArea(self._rules_card)
        self._rules_scroll.setWidgetResizable(True)
        self._rules_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rules_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rules_scroll.setWidget(_rules_body)
        rc_lay.addWidget(self._rules_scroll)
        # 高度上限：内容超 360 卡内纵向滚动（与后端设置卡同策略）；下限
        # （随内容自适应）由 _sync_rules_height 设定——QScrollArea 自身
        # sizeHint 在本卡会算出 ~72px 的瘪高度，不能只靠它（离屏实测）。
        self._rules_card.setMaximumHeight(360)

        root.addWidget(self._rules_card)
        self._rules_card.setVisible(False)

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
        # 停止模型服务（仅本地服务运行中显示）：聊天主界面直接可停，
        # 不必展开后端设置卡——用户反馈「连接后一直跑在后台」没有顺手的停止入口
        self._stop_model_btn = QPushButton("⏹ 停止模型服务", self)
        self._stop_model_btn.setObjectName("secondaryBtn")
        self._stop_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_model_btn.setToolTip(
            "结束 llama-server 进程并释放显存；需要时再到后端设置里重新启动")
        self._stop_model_btn.clicked.connect(LOCAL_SERVER.stop)
        self._stop_model_btn.setVisible(False)   # ready/starting 才显示
        quick_row.addWidget(self._stop_model_btn)
        # 后端选择入口（2026-09-28 用户建议）：云端 / 本地一键切换，
        # 当前模式高亮；两套配置独立保存，切换不丢任何一方。
        # objectName=modeBtn 复用 theme.py 既有的 checked 样式（主色填充）
        self._mode_cloud_btn = QPushButton("云端", self)
        self._mode_local_btn = QPushButton("本地", self)
        for _b in (self._mode_cloud_btn, self._mode_local_btn):
            _b.setObjectName("modeBtn")
            _b.setCheckable(True)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode_cloud_btn.setToolTip(
            "对话走云端 API（地址 / 模型在 ⚙ 后端设置里配置）")
        self._mode_local_btn.setToolTip(
            "对话走本机 llama-server（未启动时点它会带你去启动）")
        self._mode_cloud_btn.clicked.connect(lambda: self._switch_mode("cloud"))
        self._mode_local_btn.clicked.connect(lambda: self._switch_mode("local"))
        quick_row.addWidget(self._mode_cloud_btn)
        quick_row.addWidget(self._mode_local_btn)
        self._apply_mode_ui()            # 初始高亮当前模式
        self._toggle_btn = QPushButton("⚙ 后端设置", self)
        self._toggle_btn.setObjectName("secondaryBtn")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_settings)
        quick_row.addWidget(self._toggle_btn)
        # 规则库入口（与 ⚙ 后端设置并排）：展开/收起规则编辑卡
        self._rules_btn = QPushButton("📐 规则库", self)
        self._rules_btn.setObjectName("secondaryBtn")
        self._rules_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rules_btn.setToolTip(
            "自定义规则：每次对话都会追加到系统提示词；勾选生效、取消暂停")
        self._rules_btn.clicked.connect(self._toggle_rules)
        quick_row.addWidget(self._rules_btn)
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
                  "或用「本地推理」选 gguf 一键启动；「📐 规则库」"
                  "可写入你的长期偏好，我每次对话都会遵守。")

        # 本地服务状态跟随（模块级单例：页面重建后状态不丢；
        # add_listener 内部会立即回放当前状态，新页面按钮/显隐自动对齐）
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
        """往消息流追加一张左右气泡（2026-09-28 用户要求：一左一右对话式）。

        - 「你」→ 窄卡**靠右** + 主色底（chatBubbleUser，文字用 on_primary
          保证主色上的对比度）；AI / 提示 → 窄卡**靠左** + 中性底
        - 宽度贴合内容：按字体度量算自然宽度作下限（长文本到上限换行），
          高度由 BubbleLabel 在 resize 后按实际宽度回算，不裁字
        - 「存为笔记」改为**右键菜单**（2026-09-28 用户要求：不再常驻
          对话框）：仅 AI 气泡挂 CustomContextMenu，需 manifest 声明
          ``capabilities: ["write"]``；未声明就不挂，避免假菜单项
        """
        user = (role == "你")
        card = QFrame(self._stream_host)
        card.setObjectName("chatBubbleUser" if user else
                           "chatBubbleAI" if role == "AI" else "chatBubbleHint")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)
        body_label = BubbleLabel(text)
        body_label.setObjectName("chatBubbleText" if user else "chatBubbleAiText")
        body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(body_label)

        # 窄卡：可视区 78%（构造早期宽度未知时退 640），上限 900 防大屏一行过长
        vis_w = self._scroll.viewport().width() if self._scroll else 0
        max_card = int(vis_w * 0.78) if vis_w >= 480 else 640
        max_card = max(280, min(max_card, 900))
        card.setMaximumWidth(max_card)
        # 宽度下限 = 内容**不换行**时最宽一行（QLabel wordWrap 的 sizeHint
        # 是窄启发值，用它会让气泡塌成窄条——2026-09-28 实测 fm.boundingRect
        # 的 TextWordWrap 也不吃 rect 宽度，只能用 horizontalAdvance 逐行量）
        inner_max = max_card - 28
        body_label.ensurePolished()
        fm = QFontMetrics(body_label.font())
        natural = max((fm.horizontalAdvance(ln) for ln in
                       (text or " ").splitlines()), default=0)
        body_label.setMinimumWidth(min(max(natural, 40), inner_max))

        if role == "AI" and text.strip() and self._can_write():
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.setToolTip("右键可把这条回复存为笔记")
            card.customContextMenuRequested.connect(
                lambda pos, c=card, t=text: self._bubble_menu(c, t, pos))

        # 对齐行容器：不用 insertWidget 的 alignment——QLayoutItem 走对齐时
        # 布局只按 sizeHint 定高、不吃 heightForWidth（长回复末行被裁，实测）
        self._add_row(card, right=user)

    def _add_row(self, card, right: bool = False):
        """把卡片按左右对齐插进消息流（行容器：[stretch, card] 或 [card, stretch]）

        高度交由布局按 heightForWidth 正常计算；卡片不被拉伸（stretch
        吃掉剩余空间）。气泡 / 确认卡 / 结果卡共用这一条插入路径。
        """
        row_host = QWidget(self._stream_host)
        rl = QHBoxLayout(row_host)
        rl.setContentsMargins(0, 0, 0, 0)
        if right:
            rl.addStretch(1)
            rl.addWidget(card)
        else:
            rl.addWidget(card)
            rl.addStretch(1)
        self._stream.insertWidget(self._stream.count() - 1, row_host)
        self._scroll_to_bottom()

    def _bubble_menu(self, card, text: str, pos):
        """AI 气泡右键菜单（2026-09-28 用户要求：存为笔记不常驻对话框）"""
        self._build_bubble_menu(text).exec(card.mapToGlobal(pos))

    def _build_bubble_menu(self, text: str) -> QMenu:
        """构造气泡右键菜单（拆出来便于离屏断言，exec 会阻塞测试）"""
        menu = QMenu(self)
        done = text in self._noted_texts
        act = menu.addAction("✅ 已存为笔记" if done else "📥 存为笔记")
        act.setEnabled(not done)
        act.triggered.connect(lambda: self._save_as_note(text))
        return menu

    # ---------------- 动作执行（2026-09-28 数据操控） ----------------
    # 链路：AI 输出动作块 → parse_actions 校验（op 白名单 + id 必须真实存在）
    # → 分级处理：新增类直接执行；改删类弹确认卡，用户点「执行」才落库。
    # AI 永远拿不到管理器本体，只能经 ctx.write / ctx.manage 白名单方法。
    def _snapshot(self) -> dict:
        """当前数据快照（动作校验用：id 必须是真实存在的记录）

        知识库段落带 ``hash``：校验通过后由 ``parse_actions`` 绑定进动作，
        供宿主比对——编号是位置型的，删除会前移。
        """
        try:
            return {"tasks": self._ctx.data.tasks(),
                    "fragments": self._ctx.data.fragments(),
                    "notes": self._ctx.data.notes(),
                    "knowledge": self._ctx.data.knowledge()}
        except Exception:                       # noqa: BLE001 - 校验降级为无数据
            return {}

    def _handle_actions(self, actions):
        """分级处理动作：新增直接做，改删先要用户确认。

        授权前置守卫：协议只在授权时才下发，但模型仍可能硬输出动作块
        （尤其本地小模型）。此时**不要**把注定被宿主拒绝的动作渲染成
        确认卡（用户点了「执行」只会看到一排失败），而应如实说明未执行。
        """
        if not self._can_write():
            self.add_bubble("提示", "当前未授权修改数据（插件未声明 "
                                    "write/manage 能力），以下动作未执行：\n- "
                            + "\n- ".join(a["desc"] for a in actions))
            return
        direct = [a for a in actions if a["level"] == "write"]
        risky = [a for a in actions if a["level"] == "manage"]
        if risky and not self._can_manage():
            self.add_bubble("提示", "当前未授权改删数据（插件未声明 "
                                    "manage 能力），以下动作未执行：\n- "
                            + "\n- ".join(a["desc"] for a in risky))
            risky = []
        if direct:
            self._execute_and_report(direct)
        if risky:
            self._add_confirm_card(risky)

    def _run_action(self, act):
        """执行一个已校验的动作 → (成功, 描述, 撤销令牌)

        能力未声明时宿主侧会安全拒绝（返回 0 / False），这里如实报告失败，
        不吞错也不重试——重试可能造成重复写入。
        """
        op, a = act["op"], act["args"]
        desc = act.get("desc") or op
        w, m = self._ctx.write, self._ctx.manage
        try:
            if op == "add_task":
                rid = w.add_task(a.get("title") or "", a.get("note") or "",
                                 a.get("deadline") or "")
                return rid > 0, (f"已新增任务 #{rid}" if rid else "新增任务失败"), 0
            if op == "add_fragment":
                rid = w.add_fragment(a.get("content") or "")
                return rid > 0, (f"已记入碎片 #{rid}" if rid else "记碎片失败"), 0
            if op == "add_note":
                rid = w.add_note(a.get("title") or "", a.get("content") or "")
                return rid > 0, (f"已新增笔记 #{rid}" if rid else "新增笔记失败"), 0
            if op == "add_knowledge":
                num = w.add_knowledge(a.get("content") or "")
                if num:
                    return True, f"已追加到知识库（编号 {num}）", 0
                return False, "追加知识库失败" + _KB_FAIL_HINT, 0
            if op == "complete_task":
                ok = m.set_task_done(a["id"], True)
                return ok, (f"已完成 {desc}" if ok else f"操作失败：{desc}"), 0
            if op == "reopen_task":
                ok = m.set_task_done(a["id"], False)
                return ok, (f"已恢复 {desc}" if ok else f"操作失败：{desc}"), 0
            if op == "update_task":
                ok = m.update_task(a["id"], title=a.get("title"),
                                   note=a.get("note"),
                                   deadline=a.get("deadline"))
                return ok, (f"已修改 {desc}" if ok else
                            f"未生效（可能无变化或失败）：{desc}"), 0
            if op == "update_fragment":
                ok = m.update_fragment(a["id"], content=a.get("content"),
                                       source=a.get("source"))
                return ok, (f"已修改 {desc}" if ok else
                            f"未生效（可能无变化或失败）：{desc}"), 0
            if op == "update_note":
                ok = m.update_note(a["id"], title=a.get("title"),
                                   content=a.get("content"))
                return ok, (f"已修改 {desc}" if ok else
                            f"未生效（可能无变化或失败）：{desc}"), 0
            if op == "update_knowledge":
                ok = m.update_knowledge(a["id"], a.get("content") or "",
                                        a.get("expect_hash") or "")
                if ok:
                    return True, f"已改写 {desc}", 0
                return False, f"未改写 {desc}{_KB_FAIL_HINT}", 0
            if op == "delete_knowledge":
                token = m.delete_knowledge(a["id"], a.get("expect_hash") or "")
                if token > 0:
                    return True, f"已删除 {desc}", token
                return False, f"未删除 {desc}{_KB_FAIL_HINT}", 0
            if op in ("delete_task", "delete_fragment", "delete_note"):
                fn = {"delete_task": m.delete_task,
                      "delete_fragment": m.delete_fragment,
                      "delete_note": m.delete_note}[op]
                token = fn(a["id"])
                if token > 0:
                    return True, f"已删除 {desc}", token
                return False, f"删除失败：{desc}", 0
        except Exception as exc:               # noqa: BLE001 - 单条失败不影响其余
            return False, f"{op} 执行异常：{exc!r}", 0
        return False, f"未知动作：{op}", 0

    def _execute_and_report(self, actions):
        """执行一批动作 → 结果卡（删除成功的带「↩ 撤销」）

        执行前先排序：知识库删除按编号降序排到末尾（见 ``_order_actions``），
        否则同批多个删除会因为编号前移而互相踩。
        """
        lines, tokens = [], []
        for act in _order_actions(actions):
            ok, msg, token = self._run_action(act)
            lines.append(("✅ " if ok else "✗ ") + msg)
            if token:
                tokens.append(token)
        self._add_result_card(lines, tokens)

    def _add_result_card(self, lines, tokens):
        """执行结果卡（靠左；有删除成功则带撤销按钮）"""
        card = QFrame(self._stream_host)
        card.setObjectName("chatBubbleHint")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)
        lab = BubbleLabel("\n".join(lines))
        lab.setObjectName("chatBubbleAiText")
        box.addWidget(lab)
        if tokens:
            row = QHBoxLayout()
            undo_btn = QPushButton("↩ 撤销删除")
            undo_btn.setObjectName("secondaryBtn")
            undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            undo_btn.setToolTip("把刚删掉的内容恢复回来（编号可能变成新的）")
            undo_btn.clicked.connect(
                lambda _=False, t=list(tokens), b=undo_btn:
                self._undo_deletes(t, b))
            row.addWidget(undo_btn)
            row.addStretch()
            box.addLayout(row)
        self._add_row(card)
        return card

    def _undo_deletes(self, tokens, btn):
        """恢复删除（逐令牌撤销，结果写回按钮）"""
        done = sum(1 for t in tokens if self._ctx.manage.undo_delete(t))
        btn.setEnabled(False)
        if done == len(tokens):
            btn.setText(f"✅ 已恢复 {done} 条")
            self._status.setText(f"已恢复 {done} 条内容（编号可能变为新的）")
        else:
            btn.setText(f"部分恢复 {done}/{len(tokens)}")
            self._status.setText("部分内容恢复失败，详见 app.log")

    def _add_confirm_card(self, actions):
        """改删类动作的确认卡：列出将执行的操作，点「执行」才落库"""
        card = QFrame(self._stream_host)
        card.setObjectName("chatBubbleHint")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)
        head = QLabel(f"⚠ 待确认：将执行 {len(actions)} 个操作")
        head.setObjectName("fieldLabel")
        box.addWidget(head)
        body = BubbleLabel("\n".join(f"- {a['desc']}" for a in actions))
        body.setObjectName("chatBubbleAiText")
        box.addWidget(body)

        row = QHBoxLayout()
        yes = QPushButton("执行")
        yes.setObjectName("primaryBtn")
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        no = QPushButton("取消")
        no.setObjectName("secondaryBtn")
        no.setCursor(Qt.CursorShape.PointingHandCursor)

        def on_yes():
            yes.setEnabled(False)
            no.setEnabled(False)
            head.setText(f"已确认，执行 {len(actions)} 个操作")
            self._execute_and_report(actions)

        def on_no():
            yes.setEnabled(False)
            no.setEnabled(False)
            head.setText("已取消（未执行任何操作）")

        yes.clicked.connect(on_yes)
        no.clicked.connect(on_no)
        row.addWidget(yes)
        row.addWidget(no)
        row.addStretch()
        box.addLayout(row)
        self._add_row(card)
        self._pending_confirm = {"card": card, "actions": list(actions),
                                 "yes": yes, "no": no}
        return card

    def _can_write(self) -> bool:
        """本插件是否被授权写入（manifest 声明了 write 能力）"""
        try:
            return bool(self._ctx.has_capability("write"))
        except Exception:                            # noqa: BLE001
            return False

    def _can_manage(self) -> bool:
        """本插件是否被授权改删数据（manifest 声明 manage；否则不下发协议）"""
        try:
            return bool(self._ctx.manage.can_manage())
        except Exception:                            # noqa: BLE001
            return False

    def _save_as_note(self, text: str):
        """把 AI 回复存成一条笔记（标题取首行，截断到 40 字）

        经右键菜单触发（不再常驻按钮）；存过的文本记进 _noted_texts，
        菜单项随即变为「✅ 已存为笔记」并禁用，防重复存。
        """
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        head = lines[0] if lines else "AI 助手回复"
        title = head[:40] + ("…" if len(head) > 40 else "")
        nid = self._ctx.write.add_note(title, text)
        if nid:
            self._noted_texts.add(text)
            self._status.setText(f"已存为笔记：{title}")
            self._ctx.show_toast("已存为笔记（可在笔记页查看）")
        else:
            self._status.setText("存入笔记失败（详见 app.log）")

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    # ---------------- 思考动画（2026-09-28 用户要求） ----------------
    # 三点往返波（亮点位 L→R 再 R→L，无生硬跳变）；纯文本帧不写死颜色，
    # 颜色由 QSS 的 chatThinkingDots 出（主色，随主题走）
    _THINK_FRAMES = ("●  ○  ○", "○  ●  ○", "○  ○  ●", "○  ●  ○")

    def _show_thinking(self):
        """消息流里挂一张「打字中」气泡（AI 侧靠左；重复调用幂等）"""
        if self._think_bubble is not None:
            return
        card = QFrame(self._stream_host)
        card.setObjectName("chatBubbleThinking")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        self._think_label = QLabel(self._THINK_FRAMES[0])
        self._think_label.setObjectName("chatThinkingDots")
        lay.addWidget(self._think_label)
        self._think_bubble = card
        self._think_phase = 0
        self._think_timer.start()
        # 与 add_bubble 同款：插到末尾 stretch 之前，保证贴在最底部
        self._stream.insertWidget(
            self._stream.count() - 1, card, 0, Qt.AlignmentFlag.AlignLeft)
        self._scroll_to_bottom()

    def _tick_thinking(self):
        """动画帧推进（180ms/帧，_THINK_FRAMES 循环）"""
        if self._think_label is not None:
            self._think_phase = (self._think_phase + 1) % len(self._THINK_FRAMES)
            self._think_label.setText(self._THINK_FRAMES[self._think_phase])

    def _hide_thinking(self):
        """拆掉思考气泡（计时器停 + 先摘出布局再销毁，消息流计数立即回落）"""
        self._think_timer.stop()
        card, self._think_bubble, self._think_label = (
            self._think_bubble, None, None)
        if card is not None:
            self._stream.removeWidget(card)
            card.setParent(None)   # 立即摘出视觉树（removeWidget 只动布局不动父级）
            card.deleteLater()

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
        messages = ([{"role": "system",
                      "content": build_system_prompt(
                          self._cfg.get("custom_rules"),
                          can_manage=self._can_manage())}]
                    + self._history
                    + [{"role": "user", "content": user_content}])
        # 本地模式但服务未就绪：明确引导，不发注定 refused 的请求
        if self._cfg.get("backend_mode") == "local" \
                and LOCAL_SERVER.status != "ready":
            self.add_bubble(
                "提示", "本地服务未运行：点「⚙ 后端设置 → 启动本地服务」，"
                        "或点上方「云端」切回云端后端。")
            self._settings_card.setVisible(True)
            return
        url, headers, body = build_request(self._cfg, messages)
        if url is None:
            self.add_bubble("提示", body)      # body 在此路径是错误文案
            self._settings_card.setVisible(True)
            return
        # 断开闸门：只拦云端（本地服务有自己的启停状态机，不走这里）
        if not self._cloud_active and not self._is_local_url(url):
            self.add_bubble(
                "提示", "云端后端已断开连接，请求未发送。\n"
                        "点「⚙ 后端设置 → 保存并测试连接」可重新接上；"
                        "或用「本地推理」启动本地服务。")
            self._status.setText("云端后端已断开，请求未发送")
            self._settings_card.setVisible(True)
            return

        self.add_bubble("你", display_text)
        self._set_busy(True, "思考中…")
        self._show_thinking()
        self._pending_user = user_content
        ok = self._ctx.http_post_json_async(
            url, headers, body, timeout=120.0, on_done=self._on_reply)
        if not ok:
            # 桥拒绝时也会回调一次 ok=False 的结果，这里只兜底恢复状态
            self._hide_thinking()
            self._set_busy(False)

    def _on_reply(self, result: dict):
        self._hide_thinking()
        self._set_busy(False)
        reply, err = parse_reply(result)
        if err:
            self.add_bubble("提示", err)
            return
        # 动作块：AI 可能既给结论又给动作。正文被剥空（纯动作回复）时
        # **不要**渲染空气泡、更不要把 JSON 当正文显示——结论由下面
        # 的结果卡 / 确认卡承担。
        clean, actions, action_errs = parse_actions(reply, self._snapshot())
        body = clean.strip()
        if body:
            self.add_bubble("AI", body)
        elif not (actions or action_errs):
            self.add_bubble("AI", reply)       # 异常兜底：别吞掉模型的话
            body = reply
        # 成功轮次才进历史；超限丢最旧的（保留偶数条，问答成对）。
        # 历史里不放动作块 JSON，避免模型下一轮照抄格式刷动作。
        self._history.append({"role": "user", "content": self._pending_user})
        self._history.append({"role": "assistant",
                              "content": body or "（已按要求操作应用数据）"})
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]
        self._pending_user = ""
        if action_errs:
            self.add_bubble("提示", "以下动作未被执行：\n- "
                                    + "\n- ".join(action_errs))
        if actions:
            self._handle_actions(actions)

    # ---------------- 设置卡 ----------------
    def _toggle_settings(self):
        self._settings_card.setVisible(not self._settings_card.isVisible())

    # ---------------- 规则库（用户自编辑，非写死） ----------------
    def _toggle_rules(self):
        """展开/收起规则库卡；每次展开都从配置重建行（未保存的编辑即弃）。"""
        if not self._rules_card.isVisible():
            self._rebuild_rule_rows()
        self._rules_card.setVisible(not self._rules_card.isVisible())
        if self._rules_card.isVisible():
            # 等布局给出真实视口宽再算高度（提示换行行数才准）
            QTimer.singleShot(0, self._sync_rules_height)

    def _sync_rules_height(self):
        """规则卡高度跟随内容（滚动区下限 ≤330，超出交给卡内滚动）。

        QScrollArea 自身 sizeHint 在本卡会算出 ~72px 的瘪高度；显式按
        内容体 heightForWidth 设滚动区下限，行数增减与提示换行都自适应，
        避免「💾 保存规则」按钮被卡片下边缘截断（离屏实测）。
        """
        body = self._rules_scroll.widget()
        lay = body.layout() if body is not None else None
        if lay is None:
            return
        w = max(360, self._rules_scroll.viewport().width())
        h = lay.heightForWidth(w) if lay.hasHeightForWidth() \
            else body.sizeHint().height()
        self._rules_scroll.setMinimumHeight(min(h + 2, 330))

    def _rebuild_rule_rows(self):
        """清空现有行 → 按 cfg 重建；零规则时给一行空行降低上手门槛。"""
        for row in self._rules_rows:
            self._rules_form.removeWidget(row["wrap"])
            row["wrap"].deleteLater()
        self._rules_rows = []
        rules = self._cfg.get("custom_rules") or []
        if not rules:
            self._add_rule_row()
            return
        for r in rules:
            if isinstance(r, dict):
                self._add_rule_row(str(r.get("text") or ""),
                                   bool(r.get("enabled", True)))
            else:
                self._add_rule_row(str(r or ""))

    def _add_rule_row(self, text: str = "", enabled: bool = True):
        """追加一条规则行：勾选（启用）+ 单行文本 + 🗑 删除。

        文本框内回车 = 直接保存（单行规则，改完顺手落盘最顺手）。
        """
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        chk = QCheckBox()
        chk.setChecked(bool(enabled))
        chk.setToolTip("取消勾选＝暂停这条规则（不删除）")
        edit = QLineEdit(str(text))
        edit.setPlaceholderText("输入一条规则，如：回答保持简洁，不超过 200 字")
        edit.returnPressed.connect(self._save_rules)
        del_btn = QPushButton("🗑", wrap)
        del_btn.setObjectName("secondaryBtn")
        del_btn.setFixedWidth(40)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("删除这条规则")
        del_btn.clicked.connect(
            lambda _checked=False, w=wrap: self._remove_rule_row(w))
        row.addWidget(chk)
        row.addWidget(edit, 1)
        row.addWidget(del_btn)
        self._rules_form.addWidget(wrap)
        self._rules_rows.append({"wrap": wrap, "check": chk, "edit": edit})
        self._sync_rules_height()

    def _remove_rule_row(self, wrap: QWidget):
        """删除一条规则行（仅动 UI；点「💾 保存规则」才落盘）。"""
        for row in self._rules_rows:
            if row["wrap"] is wrap:
                self._rules_form.removeWidget(wrap)
                wrap.deleteLater()
                self._rules_rows.remove(row)
                self._sync_rules_height()
                return

    def _collect_rules(self) -> list:
        """读所有规则行 → [{text, enabled}]；空文本行丢弃。"""
        rules = []
        for row in self._rules_rows:
            text = row["edit"].text().strip()
            if not text:
                continue
            rules.append({"text": text, "enabled": row["check"].isChecked()})
        return rules

    def _save_rules(self):
        """收集规则行 → 落盘 config.json，状态行反馈生效条数。"""
        rules = self._collect_rules()
        self._cfg = {**self._cfg, "custom_rules": rules}
        if save_config(self._ctx, self._cfg):
            n_on = sum(1 for r in rules if r.get("enabled"))
            self._status.setText(
                f"规则已保存（生效 {n_on} / 共 {len(rules)} 条）")
        else:
            self._status.setText("规则保存失败（详见 app.log）")

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
            "backend_mode": self._cfg.get("backend_mode", "cloud"),
            "cloud_base_url": self._url_edit.text().strip(),
            "cloud_api_key": self._key_edit.text().strip(),
            "cloud_model": self._model_edit.text().strip(),
            "max_data_chars": self._cfg.get("max_data_chars", 6000),
            # 规则库字段原样携带：这里只收集后端设置，冲掉用户的规则就是事故
            "custom_rules": self._cfg.get("custom_rules", []),
            "local_server_exe": self._exe_edit.text().strip(),
            "local_gguf": self._gguf_edit.text().strip(),
            "local_port": port,
        }
        if new == self._cfg:
            return False
        self._cfg = new
        return save_config(self._ctx, new)

    def _save_and_test(self):
        """保存全部配置 + 发一个 1-token 探活请求，给用户明确反馈

        探活对象恒为云端（「测试连接」测的是云端 API 配置）；
        本地服务的可用性由它自己的探活状态机负责。
        """
        changed = self._collect_settings()
        self._status.setText("配置已保存" if changed else "配置未变化")
        probe_cfg = {**self._cfg, "backend_mode": "cloud"}
        url, headers, body = build_request(
            probe_cfg, [{"role": "user", "content": "ping"}], max_tokens=1)
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
                # 探活成功 = 后端可用，恢复云端闸门（覆盖「断开」后的重连）
                self._cloud_active = True
                self._status.setText(
                    f"✓ 连接成功（{self._cfg.get('model')}），可以开始对话")
            else:
                _, err = parse_reply(result)
                self._status.setText(f"✗ {err or '连接失败'}")

        self._ctx.http_post_json_async(url, headers, body, 20.0, on_done)

    # ---------------- 云端断开（2026-09-28 用户要求） ----------------
    @staticmethod
    def _is_local_url(url: str) -> bool:
        """是否指向本机的服务（断开闸门只拦云端，本地服务不受影响）"""
        u = (url or "").lower()
        return "://127.0.0.1" in u or "://localhost" in u

    def _disconnect_cloud(self):
        """断开云端后端：停发请求，配置与 key 原样保留（用户不用重填）"""
        if not self._cloud_active:
            self._status.setText("云端后端本来就是断开状态")
            return
        self._cloud_active = False
        self._status.setText(
            "已断开云端连接（配置保留）；重新接上请点「保存并测试连接」")

    # ---------------- 后端模式切换（2026-09-28 用户建议） ----------------
    def _apply_mode_ui(self):
        """把当前 backend_mode 反映到选择按钮（checked 高亮）"""
        local = self._cfg.get("backend_mode") == "local"
        self._mode_local_btn.setChecked(local)
        self._mode_cloud_btn.setChecked(not local)

    def _switch_mode(self, mode: str):
        """云端/本地一键切换（配置即时落盘）。

        目标侧不可用时仍切模式（尊重用户选择），但展开设置卡引导：
        本地未就绪 → 引导启动服务；云端没配地址 → 引导填写。
        """
        if self._cfg.get("backend_mode") != mode:
            self._cfg = {**self._cfg, "backend_mode": mode}
            save_config(self._ctx, self._cfg)
        self._apply_mode_ui()
        if mode == "local" and LOCAL_SERVER.status != "ready":
            self._settings_card.setVisible(True)
            self._status.setText("已选本地后端：先在「本地推理」里启动服务")
            return
        if mode == "cloud" \
                and not str(self._cfg.get("cloud_base_url") or "").strip():
            self._settings_card.setVisible(True)
            self._status.setText(
                "已选云端后端：请填写地址与 key 后「保存并测试连接」")
            return
        self._status.setText("当前后端：本机 llama-server" if mode == "local"
                             else "当前后端：云端 API")

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
        """本地服务状态 → 状态行 + 按钮文案；就绪自动接管后端

        - ready → backend_mode 切 local（双后端配置独立，云端字段不动）
        - stopped/error → 正用本地时自动回落云端（云端没配则留本地）
        停止按钮显隐：ready/starting（运行或拉起中）显示，其余隐藏——
        停止入口常驻聊天界面，用完一键释放显存（2026-09-27 用户反馈）。
        """
        self._local_status.setText(detail or status)
        self._stop_model_btn.setVisible(status in ("ready", "starting"))
        if status == "ready":
            self._local_btn.setText("停止本地服务")
            if self._cfg.get("backend_mode") != "local":
                self._cfg = {**self._cfg, "backend_mode": "local"}
                save_config(self._ctx, self._cfg)
                self._apply_mode_ui()
            self._cloud_active = True   # 本地接管后端，云端断开闸门复位
            self._status.setText("✓ 本地服务就绪，已切换到本地后端")
        elif status == "starting":
            self._local_btn.setText("取消 / 停止")
        else:
            self._local_btn.setText("启动本地服务")
            if status in ("stopped", "error") \
                    and self._cfg.get("backend_mode") == "local":
                if str(self._cfg.get("cloud_base_url") or "").strip():
                    self._cfg = {**self._cfg, "backend_mode": "cloud"}
                    save_config(self._ctx, self._cfg)
                    self._apply_mode_ui()
                    self._status.setText("本地服务已停止，已切回云端后端")

    # ---------------- 杂项 ----------------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._status.setText(text)

    def _clear_chat(self):
        self._history = []
        self._pending_user = ""
        self._hide_thinking()                   # 在途气泡也得一起拆
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
    version = "1.4.0"

    def create_actions(self, ctx) -> list:
        return [ChatAction()]

    def create_page(self, ctx: PluginContext):
        return AiChatPage(ctx)
