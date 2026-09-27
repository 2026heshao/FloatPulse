# -*- coding: utf-8 -*-
"""
====================================================================
AI 助手  -  FloatPulse 外置插件（ai-assistant）
====================================================================
第一个声明式联网插件（manifest.capabilities = ["network"]）：
读应用内任务 / 碎片 / 笔记的只读快照，交给 OpenAI 兼容接口做总结、
分类与问答。本地（Ollama / llama-server）与云端（DeepSeek 等）统一走
``/chat/completions`` 协议，只是 base_url 不同 —— 一套代码双后端。

宿主能力（全部经白名单桥，插件自己不 import 任何网络库）：
  ctx.http_post_json_async()   宿主网络桥（后台线程 + 回调回 UI 线程）
  ctx.data.tasks()/fragments()/notes()   只读数据快照
  ctx.parent_window() / ctx.show_toast() / ctx.logger

诚实性与安全约定：
  - API key 明文存在插件私有目录（data_dir/config.json）。安全边界 =
    本机用户账户；桌面应用不做私密存储，但这扇门只对用户本人打开。
  - 发给模型的数据只有用户显式点快捷指令 / 输入框内容，插件**从不**
    在后台静默上传任何数据（app.log 的 [插件网络] 行只有 URL 与耗时）。
  - 系统提示词禁止模型编造：数据为空就说空，绝不虚构任务。
====================================================================
"""

import json
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import PluginDialog, make_hint_label

PLUGIN_ID = "ai-assistant"

# ---------------- 配置 ----------------
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "max_data_chars": 6000,     # 单块数据塞进提示词的最大字符数
}

# 后端预设：(显示名, base_url, model, 说明)
# 本地两个预设与本机常用推理服务对齐：Ollama 常驻 11434；
# llama-server（llama.cpp）默认 8080 —— CodeDrill 拉起的就是它。
BACKEND_PRESETS = (
    ("云端 · DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat",
     "需在 api.deepseek.com 申请 key"),
    ("云端 · 硅基流动", "https://api.siliconflow.cn/v1", "",
     "需在 siliconflow.cn 申请 key"),
    ("本地 · Ollama", "http://127.0.0.1:11434/v1", "qwen3:4b",
     "需先安装 Ollama 并 ollama pull 模型"),
    ("本地 · llama-server", "http://127.0.0.1:8080/v1", "",
     "llama.cpp 的 llama-server，或 CodeDrill 已启动的本地服务"),
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
def build_request(cfg: dict, messages: list):
    """配置 + 消息 → (url, headers, body)；配置不完整返回 (None, None, 错误)"""
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    model = str(cfg.get("model") or "").strip()
    if not base:
        return None, None, "后端地址为空：请点「后端设置」填 base_url"
    if not model:
        return None, None, "模型名为空：请点「后端设置」填模型名"
    if not (base.startswith("http://") or base.startswith("https://")):
        return None, None, f"base_url 必须以 http:// 或 https:// 开头：{base}"
    url = f"{base}/chat/completions"
    headers = {}
    key = str(cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "messages": messages,
            "temperature": 0.4, "stream": False}
    return url, headers, body


def parse_reply(result: dict):
    """桥结果 → (回复文本, None) 或 (None, 错误描述)"""
    if not result.get("ok"):
        err = str(result.get("error") or "未知错误")
        detail = (result.get("body") or "").strip()[:200]
        hint = ""
        if "HTTP 401" in err or "HTTP 403" in err:
            hint = "（key 缺失或无效？本地后端不需要 key）"
        elif "HTTP 404" in err:
            hint = "（base_url 或模型名不对？本地服务在跑吗？）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（本地模型首次加载较慢，可重试一次）"
        return None, f"请求失败：{err}{hint}" + (f"\n{detail}" if detail else "")
    try:
        data = json.loads(result.get("body") or "")
        reply = data["choices"][0]["message"]["content"]
        return str(reply).strip(), None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"响应格式不符合 OpenAI 协议：{exc!r}"


# ====================================================================
# 聊天对话框
# ====================================================================
class ChatDialog(PluginDialog):
    """聊天界面：设置卡（默认收起）+ 消息流 + 快捷指令 + 输入区"""

    def __init__(self, ctx):
        super().__init__(ctx, title="AI 助手",
                         subtitle="总结任务 · 整理碎片 · 随手问",
                         size=(720, 640))
        self._ctx = ctx
        self._cfg = load_config(ctx)
        self._history = []          # 成功轮次 [{"role","content"}, ...]
        self._pending_user = ""     # 在途请求的用户消息（成功后落进历史）
        self._busy = False

        root = self.body_layout

        # ---- 后端设置卡（默认收起；动态显隐控件必须给 parent —— 铁律）----
        self._settings_card = QFrame(self.body)
        self._settings_card.setObjectName("glassCard")
        form = QVBoxLayout(self._settings_card)
        form.setContentsMargins(12, 10, 12, 10)
        form.setSpacing(6)

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
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(36)
            row.addWidget(lab)
            row.addWidget(widget, 1)
            form.addLayout(row)
        form.addWidget(make_hint_label(
            "本地服务（Ollama / llama-server）无需 key；配置只存本机。"))
        root.addWidget(self._settings_card)
        self._settings_card.setVisible(False)

        # ---- 消息流（滚动区）----
        self._stream_host = QWidget(self.body)      # 滚动内容容器
        self._stream = QVBoxLayout(self._stream_host)
        self._stream.setContentsMargins(0, 0, 0, 0)
        self._stream.setSpacing(8)
        self._stream.addStretch()
        self._scroll = QScrollArea(self.body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._stream_host)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)

        # ---- 快捷指令行 ----
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for text, kind, prompt in QUICK_COMMANDS:
            btn = QPushButton(text, self.body)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=kind, p=prompt:
                                self.send_quick(k, p))
            quick_row.addWidget(btn)
        quick_row.addStretch()
        root.addLayout(quick_row)

        # ---- 输入区 ----
        input_row = QHBoxLayout()
        self._input = QPlainTextEdit(self.body)
        self._input.setPlaceholderText("问点什么…（Ctrl+Enter 发送）")
        self._input.setFixedHeight(64)
        input_row.addWidget(self._input, 1)
        self._send_btn = QPushButton("发送", self.body)
        self._send_btn.setObjectName("primaryBtn")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self._send_btn)
        root.addLayout(input_row)

        self._status = make_hint_label("")
        root.addWidget(self._status)

        # Ctrl+Enter 发送（Enter 留给换行）
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(
            self._on_send_clicked)

        self.add_footer([
            ("后端设置", "secondaryBtn", self._toggle_settings),
            ("清空对话", "secondaryBtn", self._clear_chat),
            ("关闭", "secondaryBtn", self.accept),
        ])

        self.add_bubble(
            "AI", "我在。点下面的快捷指令让我读应用内数据做总结，"
                  "或直接输入问题。\n首次使用请先点「后端设置」选好后端。")

    # ---------------- 气泡 ----------------
    def add_bubble(self, role: str, text: str):
        """往消息流追加一张卡片（角色行 + 全文，对话流式）"""
        card = QFrame(self._stream_host)
        card.setObjectName("glassCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(4)
        role_label = QLabel(f"{role} · " + ("你" if role == "你" else "AI 助手"
                                            if role == "AI" else role))
        role_label.setObjectName("fieldLabel")
        body_label = QLabel(text)
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(role_label)
        box.addWidget(body_label)
        # 插到末尾的 stretch 之前，保证消息从顶部排布
        self._stream.insertWidget(self._stream.count() - 1, card)
        self._scroll_to_bottom()

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
            url, headers, body, timeout=90.0, on_done=self._on_reply)
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

    # ---------------- 状态与杂项 ----------------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._status.setText(text)

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

    def _clear_chat(self):
        self._history = []
        self._pending_user = ""
        while self._stream.count() > 1:          # 留着末尾 stretch
            item = self._stream.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.add_bubble("AI", "对话已清空。")

    def accept(self):
        """关闭前把设置卡里的值存掉（改过才写盘）"""
        if self._collect_settings():
            self._ctx.show_toast("AI 助手：配置已保存")
        super().accept()

    def _collect_settings(self) -> bool:
        """读设置卡 → 配置；有变化则落盘。返回是否有变化"""
        new = {
            "base_url": self._url_edit.text().strip(),
            "api_key": self._key_edit.text().strip(),
            "model": self._model_edit.text().strip(),
            "max_data_chars": self._cfg.get("max_data_chars", 6000),
        }
        if new == self._cfg:
            return False
        self._cfg = new
        return save_config(self._ctx, new)


# ====================================================================
# 动作与插件
# ====================================================================
class ChatAction(BallAction):
    id = f"{PLUGIN_ID}.chat"
    title = "🤖 AI 助手"

    def run(self, ctx: PluginContext):
        # 热键路径：run() 在原生事件过滤器里被调用，开模态框必须延后
        # 到下一轮事件循环，防重入（项目铁律，见插件开发说明第 12 节）
        QTimer.singleShot(0, lambda: self._open(ctx))

    def _open(self, ctx: PluginContext):
        dlg = ChatDialog(ctx)
        dlg.exec()


class AiAssistantPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "AI 助手"
    version = "1.0.0"

    def create_actions(self, ctx) -> list:
        return [ChatAction()]
