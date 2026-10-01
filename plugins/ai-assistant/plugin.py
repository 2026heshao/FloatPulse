# -*- coding: utf-8 -*-
"""
====================================================================
AI 助手  -  FloatPulse 外置插件（ai-assistant）
====================================================================
「页面插件」+ 声明式联网 / 写权限 / 改删权限 / AI 总配置插件：

  - manifest 声明 ``"page"`` → 主窗口新增「🤖 AI 助手」导航页
    （不占悬浮球右键菜单；热键 Ctrl+Alt+I 直接切到该页）
  - manifest 声明 ``"capabilities": ["network"]`` → 经宿主网络桥联网
  - manifest 声明 ``"capabilities": ["write"]`` → 经 ``ctx.write``
    把回复存成笔记（气泡右键菜单），只增不改删
  - manifest 声明 ``"capabilities": ["manage"]`` → 自然语言改删数据
    （分级确认 + 撤销令牌）
  - manifest 声明 ``"capabilities": ["ai"]`` → 经 ``ctx.ai`` 实时读取
    设置页「🧠 AI 总配置」

会话持久化（v1.9.0，A1）：
  成功问答轮次存进插件私有目录 ``sessions.json``（原子写、单会话 100
  条 / 全局 15 个会话上限），重启恢复上次会话；页顶下拉可切换历史
  会话、＋新开、🗑删除（最后一条=清空内容）。提示类气泡与动作结果卡
  不落档，恢复只重现问答主线。

功能：读应用内任务 / 碎片 / 笔记的只读快照，交给 OpenAI 兼容接口做
总结、分类与问答。

AI 后端（2026-09-29 起收归宿主，本插件**零配置**）：
  后端参数全部来自设置页「🧠 AI 总配置」（云端 / 本地 + 接入插件
  下拉框），经 ``ctx.ai.params()`` 实时读取——设置页改完即刻生效。
  本插件不再有任何模型配置代码（v1.7 之前的私有后端配置、本地
  llama-server 管理器已全部删除）。未接入时对话被拦下并引导去
  设置页配置 + 勾选接入，绝不发出注定失败的请求。

诚实性与安全约定：
  - 发给模型的数据只有用户显式点快捷指令 / 输入框内容，插件**从不**
    在后台静默上传任何数据（app.log 的 [插件网络] 行只有 URL 与耗时）。
  - 写数据只在用户点「📥 存为笔记」/ 确认改删动作时发生
    （``ctx.write`` / ``ctx.manage``，全程审计、删除可撤销）。
  - 宿主本地 llama-server 由设置页启停（Windows JobObject 防孤儿），
    本插件经 ``ctx.ai.stop_local()`` 提供快捷停止入口。
====================================================================
"""

import json
import math
import os
import uuid
from datetime import datetime

from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src import motion
from src.controls import IconButton
from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import PluginDialog, make_hint_label

PLUGIN_ID = "ai-assistant"
PAGE_KEY = f"plugin:{PLUGIN_ID}"       # 主窗口页面 key（与 loader 约定一致）

# ---------------- 配置（仅本插件自己的 UI 偏好；AI 后端在设置页）----------------
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "max_data_chars": 6000,       # 单块数据塞进提示词的最大字符数
    # 规则库：[{"text": 规则文本, "enabled": 是否启用}, ...]，随 config.json
    # 落盘，已启用规则由 build_system_prompt 追加到系统提示词末尾
    "custom_rules": [],
    # 自定义快捷指令（v1.10.0 A3）：["指令文本", ...]，点击直接发给 AI
    # （不附加应用内数据），随 config.json 落盘
    "custom_quick": [],
}

# 自定义快捷指令上限（A3）：与内置 4 条并列展示，多了会把快捷行撑爆
CUSTOM_QUICK_LIMIT = 6
CUSTOM_QUICK_CHARS = 200    # 单条指令文本的最大字符数
CUSTOM_QUICK_LABEL = 18     # 按钮文案截断长度（超出显示省略号）

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

# ---------------- 会话持久化（2026-10-01 A1） ----------------
# 对话成功轮次存进插件私有目录 sessions.json：重启可恢复、支持多会话
# 切换。上限刻意收紧——会话存档是「防丢」不是「全量导出」，防文件膨胀。
SESSIONS_FILE = "sessions.json"
SESSION_LIMIT = 15        # 最多保留的会话数（按最近活跃丢最旧）
SESSION_MSG_LIMIT = 100   # 单会话最多保留的消息条数（丢最旧，保持成对）
SESSION_MSG_CHARS = 6000  # 单条消息落盘的最大字符数（快捷指令数据块较大）

WELCOME_TEXT = (
    "我在。点快捷指令让我读应用内数据做总结，或直接输入问题。\n"
    "AI 后端在 设置 → 🧠 AI 总配置 管理（云端 / 本地一次配置，"
    "所有 AI 插件共用）；「📐 规则库」可写入你的长期偏好，"
    "我每次对话都会遵守。")


def _session_now() -> str:
    """会话存档用的时间戳（秒精度本地时间，仅用于排序与展示）"""
    return datetime.now().isoformat(timespec="seconds")


def _fresh_session() -> dict:
    """新建一个空会话记录"""
    now = _session_now()
    return {"id": uuid.uuid4().hex[:12], "title": "新会话",
            "created_at": now, "updated_at": now, "messages": []}


def session_title_from(text: str) -> str:
    """由首条用户消息取会话标题（首个非空行，截 24 字）"""
    for ln in str(text or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln[:24]
    return "新会话"


def sanitize_sessions(raw) -> dict:
    """会话存档消毒：脏数据**宽容降级**（坏会话/坏消息整条丢弃，绝不抛）。

    与 RuleStore 同立场：存档文件是唯一数据，任何结构意外都按「没有」
    处理；角色必须是 user/assistant、内容非空、逐条截断到上限，
    最后按最近活跃排序截断到 SESSION_LIMIT。
    """
    store = {"version": 1, "current_id": "", "sessions": []}
    if not isinstance(raw, dict):
        return store
    sessions = []
    for s in raw.get("sessions") or []:
        if not isinstance(s, dict):
            continue
        msgs = []
        for m in s.get("messages") or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = str(m.get("content") or "")[:SESSION_MSG_CHARS]
            if not content.strip():
                continue
            msgs.append({"role": role, "content": content,
                         "display": str(m.get("display")
                                        or "")[:SESSION_MSG_CHARS]})
        if not msgs:
            continue
        sid = str(s.get("id") or "")[:40]
        if not sid:
            continue
        sessions.append({
            "id": sid,
            "title": str(s.get("title") or "").strip()[:60] or "会话",
            "created_at": str(s.get("created_at") or "")[:32],
            "updated_at": str(s.get("updated_at") or "")[:32],
            "messages": msgs[-SESSION_MSG_LIMIT:],
        })
    # id 去重（撞 id 保先出现的）；按最近活跃降序，截到上限
    seen, uniq = set(), []
    for s in sessions:
        if s["id"] not in seen:
            seen.add(s["id"])
            uniq.append(s)
    uniq.sort(key=lambda s: s["updated_at"], reverse=True)
    store["sessions"] = uniq[:SESSION_LIMIT]
    cur = str(raw.get("current_id") or "")
    if cur not in {s["id"] for s in store["sessions"]}:
        cur = store["sessions"][0]["id"] if store["sessions"] else ""
    store["current_id"] = cur
    return store


def load_sessions(ctx) -> dict:
    """读会话存档；文件缺失/写坏只降级为空档，原文件保留供手工抢救"""
    path = os.path.join(ctx.data_dir, SESSIONS_FILE) if ctx.data_dir else ""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sanitize_sessions(json.load(f))
        except (OSError, ValueError) as exc:
            try:
                ctx.logger.warning(f"[AI 助手] 会话存档读取失败，按空档启动"
                                   f"（原文件保留）：{exc}")
            except Exception:               # noqa: BLE001 - 日志不可用不反噬
                pass
    return sanitize_sessions(None)


def save_sessions(ctx, store: dict) -> bool:
    """写会话存档（临时文件 + os.replace 原子替换）；失败只告警不抛"""
    path = os.path.join(ctx.data_dir, SESSIONS_FILE) if ctx.data_dir else ""
    if not path:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        try:
            ctx.logger.warning(f"[AI 助手] 会话存档写入失败：{exc}")
        except Exception:                   # noqa: BLE001
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ---------------- 会话导出（v1.10.0 A2）/ 自定义指令（A3） ----------------
_EXPORT_BAD_CHARS = '\\/:*?"<>|\r\n\t'   # Windows 文件名非法字符 + 控制空白


def safe_export_name(title: str) -> str:
    """会话标题 → 安全的导出文件名主干（非法字符替换、截断、空回退）"""
    name = "".join("_" if ch in _EXPORT_BAD_CHARS else ch
                   for ch in str(title or "")).strip().strip(".")[:40]
    if not name or set(name) <= {"_"}:   # 全是替换符 = 没有可读内容
        return "会话"
    return name


def session_to_markdown(session: dict) -> str:
    """会话存档 → Markdown 文本（导出用；纯函数不碰 UI）。

    用户消息优先用 display（界面文案，快捷指令不带数据块），
    AI 消息用 content；空内容跳过。文件末尾恒以单个换行收尾。
    """
    sess = session if isinstance(session, dict) else {}
    title = str(sess.get("title") or "").strip() or "会话"
    msgs = [m for m in sess.get("messages") or []
            if isinstance(m, dict) and str(m.get("content") or "").strip()]
    lines = [f"# AI 助手会话：{title}", "",
             f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
             f" ｜ 消息 {len(msgs)} 条", ""]
    for m in msgs:
        role = "你" if m.get("role") == "user" else "AI"
        if m.get("role") == "user":
            body = (str(m.get("display") or "").strip()
                    or str(m.get("content") or "").strip())
        else:
            body = str(m.get("content") or "").strip()
        lines += ["---", "", f"**{role}：**", "", body, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def sanitize_custom_quick(raw) -> list:
    """自定义快捷指令消毒：非字符串/空文本丢弃、截断、去重、上限裁剪"""
    items, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        text = str(item or "").strip()[:CUSTOM_QUICK_CHARS]
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= CUSTOM_QUICK_LIMIT:
            break
    return items


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
                # 后端字段（cloud_* / local_* / backend_mode）已收归设置页「AI 总配置」；旧配置文件里的这些键不在
                # DEFAULT_CONFIG 里，合并时自然被忽略，无需迁移代码。
                for key in DEFAULT_CONFIG:
                    if key in stored:
                        cfg[key] = stored[key]
        except (OSError, ValueError) as exc:
            ctx.logger.warning(f"[AI 助手] 配置读取失败，用默认值：{exc}")
    # 自定义指令直接进 UI 重建，读入时就消毒（脏数据宽容降级）
    cfg["custom_quick"] = sanitize_custom_quick(cfg.get("custom_quick"))
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
def build_request(params: dict, messages: list, max_tokens=None):
    """AI 总配置参数 + 消息 → (url, headers, body)；参数不完整返回 (None, None, 错误)

    ``params`` 来自 ``ctx.ai.params()``（设置页「🧠 AI 总配置」实时快照），
    mode 决定调用哪套后端：
    - cloud → base_url / api_key / model（OpenAI 兼容 API）
    - local → 宿主本地 llama-server（127.0.0.1:{local_port}/v1，模型名固定
      "local"——llama-server 忽略该字段；就绪与否由 params["local_ready"]
      把关，调用方在发请求前拦截）
    """
    params = params if isinstance(params, dict) else {}
    if params.get("mode") == "local":
        try:
            port = int(params.get("local_port") or 8095)
        except (TypeError, ValueError):
            port = 8095
        base = f"http://127.0.0.1:{port}/v1"
        model = "local"
        key = ""
    else:
        base = str(params.get("base_url") or "").strip().rstrip("/")
        model = str(params.get("model") or "").strip()
        key = str(params.get("api_key") or "").strip()
        if not base:
            return None, None, "后端地址为空：到 设置 → 🧠 AI 总配置 填写"
        if not model:
            return None, None, "模型名为空：到 设置 → 🧠 AI 总配置 填写"
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
            hint = "（key 缺失或无效？到 设置 → 🧠 AI 总配置 检查）"
        elif "HTTP 404" in err:
            hint = ("（地址或模型名不对？地址应以 /v1 结尾，"
                    "端口上跑的须是 OpenAI 兼容服务）")
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（模型首次加载较慢，可重试一次）"
        elif "refused" in err.lower():
            hint = ("（端口没有服务在听——本地服务没启动？"
                    "到 设置 → 🧠 AI 总配置 启动）")
        return None, f"请求失败：{err}{hint}" + (f"\n{detail}" if detail else "")
    try:
        data = json.loads(result.get("body") or "")
        reply = data["choices"][0]["message"]["content"]
        return str(reply).strip(), None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"响应格式不符合 OpenAI 协议：{exc!r}"


# ====================================================================
# 输入框：Enter 发送，Shift+Enter 换行（与 WorkBuddy 输入框一致）
# ====================================================================
class ChatInput(QPlainTextEdit):
    """Enter 直发；Shift+Enter 保留默认换行行为"""

    submit_requested = pyqtSignal()

    def keyPressEvent(self, event):
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


class ThinkingDots(QWidget):
    """「思考中」三点自绘动画（v1.11.0）：连续弹跳波，零字体依赖。

    替换 2026-09-28 版的 ``●○`` 字符帧——几何符号在 msyh 下有豆腐块风险
    （项目铁律：图形一律自绘），且 4 帧轮换观感生硬。三点按正弦相位做
    **垂直弹跳 + 透明度呼吸**（33ms/帧 ≈ 30fps，周期 900ms，三点各差 1/3
    相位）；颜色取宿主主题 ``$primary`` 并订阅 theme_changed 跟随换色；
    **减弱动效开启时不启动计时器**，静止显示三点（透明度呈阶梯状，仍可读）。
    """

    PERIOD_MS = 900          # 一个完整波形的周期
    TICK_MS = 33             # ~30fps
    DOT_R = 3.4              # 点半径（px）
    GAP = 13.0               # 相邻点中心间距（px）
    BOUNCE = 3.0             # 弹跳幅度（px）
    _IDLE_PHASE = 0.125      # 静止模式下的相位（三点透明度呈阶梯，不呆板）

    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._phase_ms = int(self.PERIOD_MS * self._IDLE_PHASE)
        self._color = self._resolve_color()
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._subscribe_theme()
        self.setFixedSize(
            int(self.GAP * 2 + self.DOT_R * 2 + 8),
            int(self.DOT_R * 2 + self.BOUNCE * 2 + 6))
        if not motion.reduce_motion():
            self._timer.start()

    # ---------------- 主题取色 ----------------
    def _resolve_color(self) -> QColor:
        """宿主主题 $primary；拿不到时用 light 主色兜底（不画黑）"""
        try:
            from src.theme import get_colors
            theme = "light"
            win = self._host()
            t = getattr(win, "current_theme", None)
            if t in ("light", "dark"):
                theme = t
            c = (get_colors(theme) or {}).get("primary")
            if isinstance(c, str) and c.startswith("#") and len(c) in (4, 7):
                return QColor(c)
        except Exception:                     # noqa: BLE001 - 取不到就用兜底
            pass
        return QColor("#27787A")

    def _host(self):
        getter = getattr(self._ctx, "parent_window", None) if self._ctx \
            else None
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:                     # noqa: BLE001
            return None

    def _subscribe_theme(self):
        """主题切换跟随（IconButton/PageTitle 同款 callable 守卫）"""
        try:
            sig = getattr(self._host(), "theme_changed", None)
            if sig is not None and callable(getattr(sig, "connect", None)):
                sig.connect(self._on_theme_changed)
        except Exception:                     # noqa: BLE001 - 订阅失败只影响配色
            pass

    def _on_theme_changed(self, *_args):
        self._color = self._resolve_color()
        self.update()

    # ---------------- 动画 ----------------
    def _tick(self):
        self._phase_ms = (self._phase_ms + self.TICK_MS) % self.PERIOD_MS
        self.update()

    def stop(self):
        """停止动画（拆除气泡时调；双保险，控件销毁本会带走计时器）"""
        self._timer.stop()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def phase_ms(self) -> int:
        """当前波形相位（ms）；测试与 verify 用"""
        return self._phase_ms

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base_y = self.height() / 2.0 + self.BOUNCE / 2.0
        x0 = (self.width() - self.GAP * 2) / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            ph = ((self._phase_ms / self.PERIOD_MS) + i / 3.0) % 1.0
            wave = max(0.0, math.sin(ph * 2.0 * math.pi))   # 只取正半周
            color = QColor(self._color)
            color.setAlphaF(min(1.0, 0.35 + 0.65 * wave))
            painter.setBrush(color)
            r = self.DOT_R
            painter.drawEllipse(
                QPointF(x0 + i * self.GAP, base_y - wave * self.BOUNCE),
                r, r)


# ====================================================================
# 主页面（嵌入主窗口导航；create_page 返回它）
# ====================================================================
class QuickAddDialog(PluginDialog):
    """「添加自定义快捷指令」小对话框（A3）：单行输入，回车或按钮提交

    继承 PluginDialog（铁律：插件弹窗必须走它）；text() 供调用方取
    已清洗文本。测试可单独构造（ctx 传 None 走默认主题），不 exec。
    """

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, title="添加快捷指令",
                         subtitle=f"最多 {CUSTOM_QUICK_LIMIT} 条，随插件配置保存",
                         parent=parent, size=(440, 190))
        hint = QLabel("指令会作为你的消息直接发给 AI（不附加应用内数据）。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("指令内容，如：帮我把下面这段话改通顺")
        self.edit.returnPressed.connect(self.accept)
        self.body_layout.addWidget(hint)
        self.body_layout.addWidget(self.edit)
        self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            ("添加", "primaryBtn", self.accept),
        ])
        self.edit.setFocus()

    def text(self) -> str:
        """清洗后的输入文本（去首尾空白）"""
        return self.edit.text().strip()


class AiChatPage(QWidget):
    """聊天页：规则库卡（收起）+ 消息流 + 快捷指令 + 输入区 + 状态行

    AI 后端参数实时来自设置页「🧠 AI 总配置」（ctx.ai），本页零后端配置。
    """

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._cfg = load_config(ctx)
        self._history = []          # 成功轮次 [{"role","content"}, ...]
        self._pending_user = ""     # 在途请求的用户消息（成功后落进历史）
        self._pending_display = ""  # 在途请求的界面文案（气泡/会话存档用）
        self._busy = False
        # 会话持久化（A1）：多会话存档 + 重启恢复。self._session 永远指向
        # _store["sessions"] 里的一条（聊天页至少有一个会话，删到最后一条
        # = 清空内容而非删除）。
        self._store = load_sessions(ctx)
        if not self._store["sessions"]:
            s = _fresh_session()
            self._store["sessions"] = [s]
            self._store["current_id"] = s["id"]
        cur = next((s for s in self._store["sessions"]
                    if s["id"] == self._store["current_id"]),
                   self._store["sessions"][0])
        self._store["current_id"] = cur["id"]
        self._session = cur
        # 思考动画（2026-09-28 用户要求；v1.11.0 起为自绘三点连续波）：
        # 请求在途时消息流里挂一张「打字中」气泡，回复到达（或失败）即拆掉。
        # 气泡造型 QSS：chatBubbleThinking（theme.py）；三点由 ThinkingDots
        # 自绘（颜色取主题 $primary、跟随 theme_changed、reduce_motion 静止）
        self._think_bubble = None       # 在途气泡 QFrame（无在途时为 None）
        self._think_dots = None         # 自绘三点控件（ThinkingDots）
        # 已存为笔记的回复文本（气泡右键菜单据此显示「已存为笔记」并禁用）
        self._noted_texts = set()
        # 最近一张待确认卡（改删动作等用户点「执行」；测试与调试用引用）
        self._pending_confirm = None

        root = QVBoxLayout(self)
        self.setObjectName("pluginPage")   # 吃主窗口 QSS 的实底（theme.py）
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

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
        self._rule_add_btn = IconButton("plus", text="添加规则", icon_size=14,
                                        object_name="secondaryBtn", parent=self)
        # clicked 会把 checked=False 当首个位置参数传入 → 用 lambda 挡住，
        # 否则 _add_rule_row 的 text 形参吃进 False，凭空多出一行 "False"
        self._rule_add_btn.clicked.connect(
            lambda _checked=False: self._add_rule_row())
        rules_btn_row.addWidget(self._rule_add_btn)
        rules_btn_row.addStretch()
        self._rule_save_btn = IconButton("save", text="保存规则", icon_size=14,
                                         object_name="primaryBtn", parent=self,
                                         off_color="on_primary",
                                         hover_color="on_primary")
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

        # ---- 会话栏（A1）：下拉切换 + 新开 + 删除 ----
        sess_row = QHBoxLayout()
        sess_row.setSpacing(6)
        self._session_combo = QComboBox(self)
        self._session_combo.setObjectName("chatSessionCombo")
        self._session_combo.setToolTip(
            "切换历史会话（对话记录存在插件私有目录，重启不丢）")
        self._session_combo.currentIndexChanged.connect(self._on_session_combo)
        sess_row.addWidget(self._session_combo, 1)
        self._session_new_btn = IconButton("plus", icon_size=14,
                                           object_name="secondaryBtn",
                                           parent=self, tooltip="新开一个会话")
        self._session_new_btn.clicked.connect(
            lambda _checked=False: self._new_session())
        sess_row.addWidget(self._session_new_btn)
        self._session_del_btn = IconButton("trash", icon_size=14,
                                           object_name="secondaryBtn",
                                           parent=self, tooltip="删除当前会话")
        self._session_del_btn.clicked.connect(
            lambda _checked=False: self._delete_session())
        sess_row.addWidget(self._session_del_btn)
        # 导出当前会话（A2）：Markdown 文件（utf-8 + LF），选路径后落盘
        self._session_export_btn = IconButton("save", icon_size=14,
                                              object_name="secondaryBtn",
                                              parent=self,
                                              tooltip="导出当前会话为 Markdown 文件")
        self._session_export_btn.clicked.connect(
            lambda _checked=False: self._export_session())
        sess_row.addWidget(self._session_export_btn)
        root.addLayout(sess_row)

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
        # 自定义快捷指令（A3）：动态区 + 「＋」添加，位于内置指令与
        # stretch 之间；内容由 _rebuild_custom_quick 按 config 重建
        self._custom_quick_host = QWidget(self)
        custom_lay = QHBoxLayout(self._custom_quick_host)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(8)
        quick_row.addWidget(self._custom_quick_host)
        quick_row.addStretch()
        # 停止模型服务（仅本地服务运行中显示）：聊天主界面直接可停，
        # 不必展开后端设置卡——用户反馈「连接后一直跑在后台」没有顺手的停止入口
        self._stop_model_btn = QPushButton("⏹ 停止模型服务", self)
        self._stop_model_btn.setObjectName("secondaryBtn")
        self._stop_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_model_btn.setToolTip(
            "结束宿主本地 llama-server 进程并释放显存；"
            "需要时到 设置 → 🧠 AI 总配置 重新启动")
        self._stop_model_btn.clicked.connect(self._stop_host_server)
        self._stop_model_btn.setVisible(False)   # ready/starting 才显示
        quick_row.addWidget(self._stop_model_btn)
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

        self._refresh_session_combo()
        self._rebuild_stream_from_session()
        self._rebuild_custom_quick()

        # 宿主本地服务状态跟随（ctx.ai 订阅 AI_SERVER 广播；UI 线程回调；
        # add_listener 内部会立即回放当前状态，新页面按钮/显隐自动对齐）
        self._ctx.ai.add_listener(self._on_local_status)
        # 页面销毁时退订，避免 _listeners 里累积已销毁页面的死引用
        self.destroyed.connect(
            lambda: self._ctx.ai.remove_listener(self._on_local_status))

    # ---------------- 会话管理（A1） ----------------
    def _refresh_session_combo(self):
        """下拉框按存档重建（条目=最近活跃降序）；期间屏蔽信号防回环"""
        combo = self._session_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            for s in self._store["sessions"]:
                combo.addItem(s["title"], s["id"])
            idx = combo.findData(self._session["id"])
            combo.setCurrentIndex(max(0, idx))
        finally:
            combo.blockSignals(False)

    def _on_session_combo(self, index: int):
        """下拉切换会话：切存档指针、重建消息流（在途请求期间拒绝）"""
        if self._busy:
            self._refresh_session_combo()      # 回弹到当前会话
            self._status.setText("正在处理上一条…，稍后再切换会话")
            return
        sid = self._session_combo.itemData(index)
        if not sid or sid == self._session["id"]:
            return
        target = next((s for s in self._store["sessions"]
                       if s["id"] == sid), None)
        if target is None:
            self._refresh_session_combo()
            return
        self._session = target
        self._store["current_id"] = sid
        save_sessions(self._ctx, self._store)
        self._rebuild_stream_from_session()

    def _new_session(self):
        """新开会话（旧的保留在存档里，可随时切回）"""
        if self._busy:
            self._status.setText("正在处理上一条…，稍后再开新会话")
            return
        s = _fresh_session()
        self._store["sessions"].insert(0, s)
        self._store["sessions"] = self._store["sessions"][:SESSION_LIMIT]
        self._store["current_id"] = s["id"]
        self._session = s
        save_sessions(self._ctx, self._store)
        self._refresh_session_combo()
        self._rebuild_stream_from_session()

    def _delete_session(self):
        """删除当前会话；只剩最后一条时清空内容而非删除（页内恒有会话）"""
        if self._busy:
            self._status.setText("正在处理上一条…，稍后再删会话")
            return
        if len(self._store["sessions"]) <= 1:
            self._session["messages"] = []
            self._session["title"] = "新会话"
            self._session["updated_at"] = _session_now()
            save_sessions(self._ctx, self._store)
            self._refresh_session_combo()
            self._rebuild_stream_from_session()
            return
        self._store["sessions"] = [
            s for s in self._store["sessions"]
            if s["id"] != self._session["id"]]
        self._session = self._store["sessions"][0]
        self._store["current_id"] = self._session["id"]
        save_sessions(self._ctx, self._store)
        self._refresh_session_combo()
        self._rebuild_stream_from_session()

    def _rebuild_stream_from_session(self):
        """按当前会话存档重建消息流（启动恢复 / 切换 / 新建共用）。

        气泡显示用 display（用户侧界面文案，快捷指令不带数据块），
        模型上下文用 content；提示类气泡（连接引导 / 动作结果卡）不
        落档，恢复后只重现问答主线。
        """
        self._history = []
        self._pending_user = ""
        self._pending_display = ""
        self._hide_thinking()
        while self._stream.count() > 1:          # 留着末尾 stretch
            item = self._stream.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for m in self._session["messages"]:
            if m["role"] == "user":
                self.add_bubble("你", m.get("display") or m["content"])
            else:
                self.add_bubble("AI", m["content"])
            self._history.append({"role": m["role"], "content": m["content"]})
        self._history = self._history[-MAX_HISTORY:]
        if not self._session["messages"]:
            self.add_bubble("AI", WELCOME_TEXT)

    def _persist_turn(self, user_content: str, user_display: str,
                      assistant_text: str):
        """成功轮次写进当前会话存档并落盘（标题/活跃时间/上限裁剪同步）"""
        sess = self._session
        sess["messages"].append({"role": "user", "content": user_content,
                                 "display": user_display})
        sess["messages"].append({"role": "assistant",
                                 "content": assistant_text,
                                 "display": assistant_text})
        if len(sess["messages"]) > SESSION_MSG_LIMIT:
            sess["messages"] = sess["messages"][-SESSION_MSG_LIMIT:]
            if len(sess["messages"]) % 2:       # 保持问答成对
                sess["messages"] = sess["messages"][1:]
        if sess["title"] in ("", "新会话"):
            sess["title"] = session_title_from(user_display)
        sess["updated_at"] = _session_now()
        # 按最近活跃排序后裁掉最旧会话；当前会话刚活跃必然幸存
        self._store["sessions"].sort(
            key=lambda s: s["updated_at"], reverse=True)
        self._store["sessions"] = self._store["sessions"][:SESSION_LIMIT]
        self._store["current_id"] = self._session["id"]
        save_sessions(self._ctx, self._store)
        self._refresh_session_combo()

    # ---------------- 会话导出（A2） ----------------
    def _export_session(self):
        """当前会话 → Markdown 文件（QFileDialog 选路径，utf-8 + LF）"""
        if not (self._session.get("messages") or []):
            self._status.setText("当前会话还没有可导出的内容")
            return
        default = safe_export_name(self._session.get("title") or "") + ".md"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出会话为 Markdown", default, "Markdown 文档 (*.md)")
        if not path:
            return                              # 用户取消：静默返回
        if not path.lower().endswith(".md"):
            path += ".md"
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(session_to_markdown(self._session))
        except OSError as exc:
            self._status.setText(f"导出失败：{exc}")
            return
        self._status.setText(f"已导出：{path}")
        try:
            self._ctx.show_toast("会话已导出为 Markdown 文件")
        except Exception:                       # noqa: BLE001 - 提示失败不反噬
            pass

    # ---------------- 自定义快捷指令（A3） ----------------
    def _rebuild_custom_quick(self):
        """按 config 重建自定义指令按钮 + 「＋」添加按钮（整组重建）"""
        lay = self._custom_quick_host.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for prompt in self._cfg.get("custom_quick") or []:
            label = prompt[:CUSTOM_QUICK_LABEL] + \
                ("…" if len(prompt) > CUSTOM_QUICK_LABEL else "")
            btn = QPushButton(label, self._custom_quick_host)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"自定义指令：{prompt}\n点击直接发送；右键删除")
            btn.clicked.connect(
                lambda _=False, p=prompt: self._send_custom_quick(p))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, p=prompt: self._custom_quick_menu(b, p, pos))
            lay.addWidget(btn)
        plus = IconButton("plus", icon_size=14,
                          object_name="secondaryBtn",
                          parent=self._custom_quick_host,
                          tooltip=f"添加自定义快捷指令"
                                  f"（最多 {CUSTOM_QUICK_LIMIT} 条）")
        plus.clicked.connect(lambda _checked=False: self._add_custom_quick())
        lay.addWidget(plus)

    def _send_custom_quick(self, prompt: str):
        """自定义指令点击：作为用户消息直接发送（不附加应用内数据）"""
        if self._busy:
            self._status.setText("正在处理上一条…")
            return
        self._dispatch(prompt, None, prompt)

    def _custom_quick_menu(self, btn, prompt: str, pos):
        """自定义指令右键菜单：删除该条（从 config 移除并落盘重建）"""
        menu = QMenu(self)
        act = menu.addAction(f"🗑 删除「{prompt[:CUSTOM_QUICK_LABEL]}」")
        act.triggered.connect(lambda: self._remove_custom_quick(prompt))
        menu.exec(btn.mapToGlobal(pos))

    def _remove_custom_quick(self, prompt: str):
        """删除一条自定义指令（按文本匹配，重名只删一条）"""
        items = [p for p in self._cfg.get("custom_quick") or []
                 if p != prompt]
        self._cfg = {**self._cfg, "custom_quick": items}
        if save_config(self._ctx, self._cfg):
            self._rebuild_custom_quick()
            self._status.setText("已删除自定义指令")
        else:
            self._status.setText("删除失败（配置写入失败，详见 app.log）")

    def _add_custom_quick(self):
        """弹小对话框添加一条自定义指令；超上限/空文本拦截并提示"""
        current = self._cfg.get("custom_quick") or []
        if len(current) >= CUSTOM_QUICK_LIMIT:
            self._status.setText(
                f"自定义指令已达上限（{CUSTOM_QUICK_LIMIT} 条），"
                "先右键删除一条")
            return
        dlg = QuickAddDialog(self._ctx, parent=self.window())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.text().strip()[:CUSTOM_QUICK_CHARS]
        if not text:
            self._status.setText("指令内容为空，未添加")
            return
        self._cfg = {**self._cfg, "custom_quick": current + [text]}
        if save_config(self._ctx, self._cfg):
            self._rebuild_custom_quick()
            self._status.setText(f"已添加自定义指令（共 {len(current) + 1} 条）")
        else:
            self._status.setText("添加失败（配置写入失败，详见 app.log）")

    def add_bubble(self, role: str, text: str):
        """往消息流追加一张左右气泡（2026-09-28 用户要求：一左一右对话式）。

        - 「你」→ 窄卡**靠右** + 主色底（chatBubbleUser，文字用 on_primary
          保证主色上的对比度）；AI / 提示 → 窄卡**靠左** + 中性底
        - 宽度贴合内容：按字体度量算自然宽度作下限（长文本到上限换行），
          高度由 BubbleLabel 在 resize 后按实际宽度回算，不裁字
        - 「存为笔记」改为**右键菜单**（2026-09-28 用户要求：不再常驻
          对话框）：仅 AI 气泡挂 CustomContextMenu，需 manifest 声明
          ``capabilities: ["write"]``；未声明就不挂，避免假菜单项

        （2026-09-29 修复：AI 总配置迁移删私有设置卡时被连带误删，恢复自
        c0783f2 —— 本方法被 12 处调用，缺失会导致页面注入失败、入口消失）
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

    # ---------------- 思考动画（2026-09-28 用户要求；v1.11.0 自绘化） ----------------

    def _show_thinking(self):
        """消息流里挂一张「打字中」气泡（AI 侧靠左；重复调用幂等）"""
        if self._think_bubble is not None:
            return
        card = QFrame(self._stream_host)
        card.setObjectName("chatBubbleThinking")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        self._think_dots = ThinkingDots(self._ctx)
        lay.addWidget(self._think_dots)
        self._think_bubble = card
        # 与 add_bubble 同款：插到末尾 stretch 之前，保证贴在最底部
        self._stream.insertWidget(
            self._stream.count() - 1, card, 0, Qt.AlignmentFlag.AlignLeft)
        self._scroll_to_bottom()

    def _hide_thinking(self):
        """拆掉思考气泡（动画计时器随控件 stop/销毁；消息流计数立即回落）"""
        card, dots, self._think_bubble, self._think_dots = (
            self._think_bubble, self._think_dots, None, None)
        if dots is not None:
            dots.stop()
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

    # ---------------- AI 总配置接入（2026-09-29） ----------------
    def _attached_params(self):
        """AI 后端参数（唯一来源：设置页「🧠 AI 总配置」）→ 未接入返回 None

        每次发请求前都重查（is_attached / params 都是实时读宿主配置），
        设置页改完 / 勾选或取消勾选，下一发请求即生效。
        未声明 ai 能力（老宿主）或宿主未注入通道时安全返回 None。
        """
        try:
            if self._ctx.has_capability("ai") and self._ctx.ai.is_attached():
                params = self._ctx.ai.params()
                if params:
                    return params
        except Exception:                 # noqa: BLE001 - 读取失败按未接入
            pass
        return None

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
        # 后端唯一来源：设置页「AI 总配置」。未接入 / 本地未就绪都只引导，
        # 绝不发出注定失败的请求（宿主服务的启停入口在设置页）。
        params = self._attached_params()
        if params is None:
            self.add_bubble(
                "提示", "尚未接入 AI：到 设置 → 🧠 AI 总配置 配好云端或本地"
                        "后端，并在「接入插件」里勾选本插件。")
            return
        if params.get("mode") == "local" and not params.get("local_ready"):
            self.add_bubble(
                "提示", "宿主本地服务未就绪：到 设置 → 🧠 AI 总配置 启动。")
            return
        url, headers, body = build_request(params, messages)
        if url is None:
            self.add_bubble("提示", body)      # body 在此路径是错误文案
            return

        self.add_bubble("你", display_text)
        self._set_busy(True, "思考中…")
        self._show_thinking()
        self._pending_user = user_content
        self._pending_display = display_text
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
        # 会话存档（A1）：同一内容落盘，供重启恢复 / 多会话切换
        self._persist_turn(self._pending_user, self._pending_display,
                           body or "（已按要求操作应用数据）")
        self._pending_user = ""
        self._pending_display = ""
        if action_errs:
            self.add_bubble("提示", "以下动作未被执行：\n- "
                                    + "\n- ".join(action_errs))
        if actions:
            self._handle_actions(actions)


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
        del_btn = IconButton("trash", icon_size=16, object_name="secondaryBtn",
                             parent=wrap, tooltip="删除这条规则")
        del_btn.setFixedWidth(40)
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

    def _on_local_status(self, status: str, detail: str):
        """宿主本地服务状态广播（ctx.ai 订阅）→ ⏹ 按钮显隐 + 状态行

        - ready/starting 显示「⏹ 停止模型服务」——停止入口常驻聊天界面，
          用完一键释放显存（2026-09-27 用户反馈）
        - ready 就绪提示；error 带原因展示（服务启停的真正入口在设置页）
        """
        self._stop_model_btn.setVisible(status in ("ready", "starting"))
        if status == "ready":
            self._status.setText("✓ 宿主本地服务就绪，接入的插件即刻可用")
        elif status == "error" and detail:
            self._status.setText(f"⚠ {detail}")

    def _stop_host_server(self):
        """⏹ 停止宿主本地服务（经 ctx.ai 门面；未授权时安全拒绝）"""
        try:
            self._ctx.ai.stop_local()
        except Exception:                 # noqa: BLE001 - 停止失败不反噬页面
            self._status.setText("停止宿主本地服务失败（详见 app.log）")

    # ---------------- 杂项 ----------------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._status.setText(text)

    def _clear_chat(self):
        """清空当前会话（A1 语义：只清内容，会话本身保留在存档里）"""
        self._history = []
        self._pending_user = ""
        self._pending_display = ""
        self._session["messages"] = []
        self._session["updated_at"] = _session_now()
        save_sessions(self._ctx, self._store)
        self._hide_thinking()                   # 在途气泡也得一起拆
        while self._stream.count() > 1:          # 留着末尾 stretch
            item = self._stream.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.add_bubble("AI", "当前会话已清空。")


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
    version = "1.11.0"

    def create_actions(self, ctx) -> list:
        return [ChatAction()]

    def create_page(self, ctx: PluginContext):
        return AiChatPage(ctx)
