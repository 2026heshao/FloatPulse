# -*- coding: utf-8 -*-
"""
====================================================================
AI 文本工坊  -  FloatPulse 外置插件（ai-text-workshop）
====================================================================
「剪贴板一键 AI 加工」，页面插件（与 ai-assistant 同形态）：

  - manifest 声明 ``"page"`` → 主窗口侧栏新增「✂ 文本工坊」导航页
  - 热键 Ctrl+Alt+T（或悬浮球右键菜单）→ 切到工坊页并**自动带入
    剪贴板内容**到原文框（showEvent + 模块级待处理标记）
  - 六种办公文本动作：✉ 润色成邮件 / 🌐 翻成中文 / 🌐 翻成英文 /
    📌 总结要点 / 🎩 改写正式 / ✅ 提取待办（可一键转任务）
  - 外加一条自定义指令输入框，覆盖按钮之外的长尾需求

结果可复制、可存为碎片、可存为笔记，提取出的待办可直接写入任务
列表——别家的文本工具处理完就结束了，这里的结果直接进应用数据链路。

AI 后端（2026-09-29 起收归宿主，本插件**零配置**）：
  后端参数全部来自设置页「🧠 AI 总配置」（云端 / 本地 + 接入插件
  下拉框），经 ``ctx.ai.params()`` 实时读取——设置页改完即刻生效。
  本插件不再有任何模型配置代码（v1.3 之前的私有后端配置、本地
  llama-server 管理器已全部删除）。未接入时动作被拦下并引导去
  设置页配置 + 勾选接入，绝不发出注定失败的请求。

安全与诚实约定（与 ai-assistant 同一口径）：
  - 只在用户显式点动作按钮 / 执行自定义指令时才发请求；插件从不
    在后台静默上传任何数据（app.log 的 [插件网络] 行只有 URL 与耗时）
  - 发给模型的内容只有「原文框里的文本 + 动作指令」，绝不附带
    应用内任务/碎片/笔记数据
  - 日志只记动作名与结果长度，**不记原文与结果内容**
  - 写数据只在用户点「存为碎片 / 存为笔记 / 转任务」时发生
    （ctx.write，只增不改删）
====================================================================
"""

import json
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget, QLabel, QLineEdit,
)

from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import (
    flash_button, make_hint_label, make_section_label,
)

PLUGIN_ID = "ai-text-workshop"
PAGE_KEY = f"plugin:{PLUGIN_ID}"       # 主窗口页面 key（与 loader 约定一致）

# 单次发给模型的原文上限（超长截断并标注）
MAX_SOURCE_CHARS = 8000
# 生成参数（工坊动作都是短单轮转换，固定即可；后端参数在设置页）
TEMPERATURE = 0.4
MAX_TOKENS = 2048
# 请求超时（秒）
REQUEST_TIMEOUT_S = 90.0

SYSTEM_PROMPT = (
    "你是办公工具 FloatPulse 的文本处理助手。规则：\n"
    "1. 只输出处理结果本身——不要解释、不要开场白与结尾语、"
    "不要把结果包进代码块；\n"
    "2. 用简体中文（翻译成英文的任务除外）；\n"
    "3. 忠实于原文，绝不虚构原文没有的信息。"
)

# 内置动作：(键, 按钮文案, 指令全文)。指令里以「待处理文本」指代原文，
# 真实请求时由 build_request 拼进 user 消息。
WORK_ACTIONS = (
    ("polish_email", "✉ 润色成邮件",
     "把待处理文本润色成一封得体的工作邮件（第一行是「主题：…」，"
     "随后是正文）。保持原意，语气专业礼貌，只输出邮件本身。"),
    ("translate_zh", "🌐 翻成中文",
     "把待处理文本翻译成简体中文，保留原有分段，只输出译文。"),
    ("translate_en", "🌐 翻成英文",
     "把待处理文本翻译成英文，保留原有分段，只输出译文。"),
    ("summarize", "📌 总结要点",
     "把待处理文本总结成 Markdown 要点列表，不超过 8 条，只输出要点。"),
    ("formalize", "🎩 改写正式",
     "把待处理文本改写成正式、书面的表达，保持原意与篇幅，"
     "只输出改写结果。"),
    ("extract_tasks", "✅ 提取待办",
     "从待处理文本里提取所有行动项／待办事项，每行一条，写成 Markdown "
     "任务列表（每行以「- [ ] 」开头）。每条尽量一句话、以动词开头。"
     "不要输出任何其他内容；没有行动项时只输出（无）。"),
)

# 提取待办结果里的「无行动项」标记（与指令口径一致）
NO_TASK_MARKS = frozenset({"(无)", "（无）", "无"})

# 行动项行解析：接受 - [ ] / - / 1. 三种前缀（模型偶尔不守格式，
# 宁可多收一条也别丢用户的待办）
_TASK_LINE_RE = re.compile(r"^(?:-\s*\[[ xX]?\]?\s*|\d+[.、)]\s*|[-•]\s+)(.+)$")

# 热键触发的「带入剪贴板」待处理标记：动作先置位再切页，页面 showEvent
# 里消费。模块级（页面因「重新扫描」重建后标记也不丢）。
PENDING_HOTKEY = {"flag": False}


# ====================================================================
# 纯函数层（无 Qt 依赖，可无 GUI 环境直接 pytest）
# ====================================================================
def normalize_source(text, cap: int = 8000):
    """原文规整：非字符串 / 空白 → None；超长截断并标注原始长度"""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    if cap and cap > 0 and len(t) > cap:
        return t[:cap] + f"\n…（超长已截断，原始 {len(t)} 字符）"
    return t


def build_request(params: dict, instruction: str, source: str,
                  max_source_chars: int = 8000):
    """AI 总配置参数 + 指令 + 原文 → (url, headers, body, 错误文案)

    成功时错误文案为空串；失败时前三个返回值都是 None。
    ``params`` 来自 ``ctx.ai.params()``（字段契约见 plugin_api）：
    mode / base_url / api_key / model / local_port。
    OpenAI 兼容 /chat/completions，非流式单轮（无对话历史）。
    Content-Type 由宿主网络桥负责，headers 只放鉴权。
    """
    params = params if isinstance(params, dict) else {}
    mode = str(params.get("mode") or "cloud")
    if mode == "local":
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
            return None, None, None, "后端地址为空：到 设置 → 🧠 AI 总配置 填写"
        if not model:
            return None, None, None, "模型名为空：到 设置 → 🧠 AI 总配置 填写"
    if not (base.startswith("http://") or base.startswith("https://")):
        return None, None, None, f"后端地址必须以 http:// 或 https:// 开头：{base}"
    text = normalize_source(source, max_source_chars)
    if text is None:
        return None, None, None, "原文为空：先粘贴或输入要处理的文本"

    url = f"{base}/chat/completions"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"{instruction}\n\n=== 待处理文本 ===\n{text}"},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    return url, headers, body, ""


def parse_reply(result: dict):
    """网络桥结果 → (回复文本, None) 或 (None, 错误文案)"""
    if not result.get("ok"):
        err = str(result.get("error") or "未知错误")
        detail = (result.get("body") or "").strip()[:200]
        hint = ""
        if "HTTP 401" in err or "HTTP 403" in err:
            hint = "（key 缺失或无效？到 设置 → 🧠 AI 总配置 检查）"
        elif "HTTP 404" in err:
            hint = "（地址或模型名不对？地址应以 /v1 结尾）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = "（模型首次加载较慢，可重试一次）"
        elif "refused" in err.lower():
            hint = "（端口没有服务在听——本地服务没启动，或端口号不对）"
        return None, f"请求失败：{err}{hint}" + (f"\n{detail}" if detail else "")
    try:
        data = json.loads(result.get("body") or "")
        reply = data["choices"][0]["message"]["content"]
        return str(reply).strip(), None
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"响应格式不符合 OpenAI 协议：{exc!r}"


def parse_task_lines(reply, cap: int = 20) -> list:
    """「提取待办」的回复 → 行动项标题列表

    接受 ``- [ ] `` / ``- `` / ``1. `` 三种行前缀，去重（忽略大小写），
    上限 cap 条。「（无）」等标记返回空列表。只认行前缀结构，
    正文里的普通句子不会被误收。
    """
    out, seen = [], set()
    for raw in str(reply or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line or line.startswith("#"):
            continue
        if line in NO_TASK_MARKS:
            continue
        m = _TASK_LINE_RE.match(line)
        if not m:
            continue
        title = m.group(1).strip().strip("`* ").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= cap:
            break
    return out


def first_line_title(text: str, maxlen: int = 40) -> str:
    """取结果首行做笔记标题（超长截断加省略号，空兜底）"""
    for ln in str(text or "").splitlines():
        head = ln.strip().strip("# ").strip()
        if head:
            return head[:maxlen] + ("…" if len(head) > maxlen else "")
    return "AI 文本工坊结果"


# ====================================================================
# 工坊页面（嵌入主窗口导航；create_page 返回它）
# ====================================================================
class AiWorkshopPage(QWidget):
    """工坊页：动作行 + 自定义指令 + 原文/结果双编辑区 + 落库按钮行

    AI 后端参数全部实时来自设置页「🧠 AI 总配置」（ctx.ai.params()），
    本页没有任何模型配置 UI；接入态提示条在每次切页（showEvent）时
    刷新——已接入显示绿色提示，未接入显示引导文案。

    原文框在两种时机自动带入剪贴板：① 热键/菜单动作切入本页时
    （PENDING_HOTKEY 标记，showEvent 消费，无条件覆盖）；② 本页
    可见且原文为空时（首次切进来顺手指一下）。手动入口是
    「📥 带入剪贴板」按钮，随时覆盖。
    """

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._busy = False
        self.setObjectName("pluginPage")   # 吃主窗口 QSS 的实底（theme.py）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 整页滚动兜底：窗口偏矮时出滚动条而不是挤压控件（实测踩过的坑）
        from PyQt6.QtWidgets import QScrollArea
        self._page_scroll = QScrollArea(self)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _page_body = QWidget(self._page_scroll)
        self._page_scroll.setWidget(_page_body)
        root.addWidget(self._page_scroll)

        lay = QVBoxLayout(_page_body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ---- 接入态提示条（showEvent 随设置页勾选实时刷新）----
        self._ai_hint = make_hint_label("")
        self._ai_hint.setWordWrap(True)
        lay.addWidget(self._ai_hint)

        # ---- 动作按钮行 ----
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(make_section_label("动作"))
        self._action_btns = {}
        for key, label, instruction in WORK_ACTIONS:
            btn = QPushButton(label, self)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(instruction)
            btn.clicked.connect(
                lambda _checked=False, k=key, p=instruction:
                    self._run_action(k, p))
            self._action_btns[key] = btn
            action_row.addWidget(btn)
        action_row.addStretch(1)
        lay.addLayout(action_row)

        # ---- 自定义指令行 ----
        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        self._custom_edit = QLineEdit(self)
        self._custom_edit.setPlaceholderText(
            "自定义指令（如：把这段话改写成三条朋友圈文案）…")
        self._custom_edit.returnPressed.connect(self._run_custom)
        custom_row.addWidget(self._custom_edit, 1)
        self._custom_btn = QPushButton("执行", self)
        self._custom_btn.setObjectName("primaryBtn")
        self._custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._custom_btn.clicked.connect(self._run_custom)
        custom_row.addWidget(self._custom_btn)
        lay.addLayout(custom_row)

        # ---- 原文区（可编辑，头部带「带入剪贴板」手动入口） ----
        src_card = QFrame(self)
        src_card.setObjectName("glassCard")
        sc = QVBoxLayout(src_card)
        sc.setContentsMargins(16, 12, 16, 12)
        sc.setSpacing(6)
        src_head = QHBoxLayout()
        src_head.addWidget(make_section_label("原文"))
        src_head.addWidget(make_hint_label("打开时自动带入剪贴板，可编辑"))
        src_head.addStretch(1)
        self._grab_btn = QPushButton("📥 带入剪贴板", self)
        self._grab_btn.setObjectName("secondaryBtn")
        self._grab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._grab_btn.clicked.connect(self._on_grab_clicked)
        src_head.addWidget(self._grab_btn)
        sc.addLayout(src_head)
        self._src_edit = QPlainTextEdit()
        self._src_edit.setFont(QFont("Microsoft YaHei UI", 10))
        self._src_edit.setFixedHeight(96)
        self._src_edit.setPlaceholderText("把要处理的文本粘贴到这里…")
        sc.addWidget(self._src_edit)
        lay.addWidget(src_card)

        # ---- 结果区（可编辑，处理完可改再存） ----
        result_card = QFrame(self)
        result_card.setObjectName("glassCard")
        rc = QVBoxLayout(result_card)
        rc.setContentsMargins(16, 12, 16, 12)
        rc.setSpacing(6)
        rc.addWidget(make_section_label("结果（可编辑后再复制或保存）"))
        self._result_edit = QPlainTextEdit()
        self._result_edit.setFont(QFont("Microsoft YaHei UI", 10))
        self._result_edit.setPlaceholderText("点上方动作按钮，结果会出现在这里…")
        rc.addWidget(self._result_edit, 1)
        lay.addWidget(result_card, 1)

        # ---- 状态行：固定高度占位，避免按钮行跳动 ----
        self._status = make_hint_label("")
        self._status.setMinimumHeight(20)
        lay.addWidget(self._status)

        # ---- 落库按钮行（右对齐；未授权 write 时不显示假按钮） ----
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch(1)
        self._copy_btn = QPushButton("📋 复制结果", self)
        self._frag_btn = QPushButton("📥 存为碎片", self)
        self._note_btn = QPushButton("📝 存为笔记", self)
        self._tasks_btn = QPushButton("✅ 转任务", self)
        self._tasks_btn.setObjectName("primaryBtn")
        for btn in (self._copy_btn, self._frag_btn, self._note_btn):
            btn.setObjectName("secondaryBtn")
        for btn in (self._copy_btn, self._frag_btn, self._note_btn,
                    self._tasks_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            foot.addWidget(btn)
        self._copy_btn.clicked.connect(self._do_copy)
        self._frag_btn.clicked.connect(self._do_fragment)
        self._note_btn.clicked.connect(self._do_note)
        self._tasks_btn.clicked.connect(self._do_tasks)
        lay.addLayout(foot)

        self._copy_btn.setEnabled(False)        # 无结果时复制无意义
        if not self._can_write():
            for btn in (self._frag_btn, self._note_btn, self._tasks_btn):
                btn.setVisible(False)
        else:
            self._tasks_btn.setEnabled(False)
            self._tasks_btn.setToolTip("先执行「✅ 提取待办」，识别到行动项后可用")

    # ---------------- showEvent：切页带入剪贴板 + 接入态刷新 ----------------
    def showEvent(self, event):
        super().showEvent(event)
        if PENDING_HOTKEY["flag"]:
            PENDING_HOTKEY["flag"] = False
            self._prefill_clipboard()
        elif not self._src_edit.toPlainText().strip():
            self._prefill_clipboard()
        # 接入态每次切页重查（设置页勾选/取消/改配置，切回来即见最新态）
        params = self._attached_params()
        if params is None:
            self._ai_hint.setText(
                "⚠ 尚未接入 AI：到 设置 → 🧠 AI 总配置 配好云端或本地后端，"
                "并在「接入插件」里勾选本插件")
        else:
            self._ai_hint.setText(
                "🧠 已接入设置页「AI 总配置」，后端改动即时生效")

    # ---------------- AI 总配置接入（唯一后端来源） ----------------
    def _attached_params(self):
        """已接入设置页「AI 总配置」→ 返回宿主参数 dict；未接入 → None

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

    # ---------------- 界面小件 ----------------
    def _prefill_clipboard(self) -> bool:
        """原文框带入剪贴板文本；返回是否真的带入了内容"""
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        if text and text.strip():
            self._src_edit.setPlainText(text.strip())
            return True
        return False

    def _on_grab_clicked(self):
        if self._prefill_clipboard():
            self._status.setText(
                f"✓ 已带入剪贴板 "
                f"{len(self._src_edit.toPlainText().strip())} 字")
        else:
            self._status.setText("剪贴板里没有文本")

    def _can_write(self) -> bool:
        """本插件是否被授权写入（manifest 声明了 write 能力）"""
        try:
            return bool(self._ctx.has_capability("write"))
        except Exception:                            # noqa: BLE001
            return False

    # ---------------- 执行 ----------------
    def _run_custom(self):
        if self._busy:
            self._status.setText("正在处理上一条…")
            return
        instruction = self._custom_edit.text().strip()
        if not instruction:
            self._status.setText("先输入自定义指令，或点上方动作按钮")
            return
        self._run_action("custom", instruction, from_custom=True)

    def _run_action(self, key: str, instruction: str, from_custom: bool = False):
        """统一执行入口：取总配置 → 组请求 → 宿主网络桥 → 回调落结果"""
        if self._busy:
            self._status.setText("正在处理上一条…")
            return
        params = self._attached_params()
        if params is None:
            self._status.setText(
                "尚未接入 AI：到 设置 → 🧠 AI 总配置 配置并勾选本插件")
            return
        if params.get("mode") == "local" and not params.get("local_ready"):
            # 宿主本地服务的启停入口在设置页，这里只引导不代启
            self._status.setText(
                "宿主本地服务未就绪：到 设置 → 🧠 AI 总配置 启动")
            return
        url, headers, body, err = build_request(
            params, instruction, self._src_edit.toPlainText(),
            MAX_SOURCE_CHARS)
        if url is None:
            self._status.setText(err)
            return
        self._set_busy(True, "处理中…（模型响应通常几秒）")
        self._logger_action(key)
        ok = self._ctx.http_post_json_async(
            url, headers, body, timeout=REQUEST_TIMEOUT_S,
            on_done=lambda result, k=key, c=from_custom:
                self._on_reply(result, k, c))
        if not ok:
            # 桥拒绝时也会回调一次 ok=False 的结果，这里只兜底恢复状态
            self._set_busy(False)

    def _logger_action(self, key: str):
        """动作留痕：只记动作名，不记原文（隐私边界）"""
        logger = getattr(self._ctx, "logger", None)
        if logger is not None:
            logger.info(f"[AI 文本工坊] 执行动作 {key}")

    def _on_reply(self, result: dict, key: str, from_custom: bool):
        self._set_busy(False)
        reply, err = parse_reply(result)
        if err:
            self._status.setText(err)
            return
        self._result_edit.setPlainText(reply)
        self._copy_btn.setEnabled(True)
        if key == "extract_tasks":
            count = len(parse_task_lines(reply))
            if self._can_write():
                self._tasks_btn.setEnabled(count > 0)
                self._tasks_btn.setToolTip(
                    f"把识别到的 {count} 条行动项写入任务列表"
                    if count else "未识别到行动项")
            self._status.setText(
                f"✓ 识别到 {count} 条行动项" + (
                    "，点「✅ 转任务」写入任务列表" if count else ""))
        else:
            if self._can_write():
                self._tasks_btn.setEnabled(False)
            self._status.setText(f"✓ 完成，共 {len(reply)} 字")
        if from_custom:
            self._custom_edit.clear()

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        for btn in self._action_btns.values():
            btn.setEnabled(not busy)
        self._custom_btn.setEnabled(not busy)
        self._custom_edit.setEnabled(not busy)
        if self._can_write():
            # 落库按钮在处理中也锁住；转任务另有「有无行动项」的独立开关
            for btn in (self._copy_btn, self._frag_btn, self._note_btn):
                btn.setEnabled(not busy and bool(
                    self._result_edit.toPlainText().strip()))
            if busy:
                self._tasks_btn.setEnabled(False)
        self._status.setText(text)

    # ---------------- 结果出口 ----------------
    def _result_text(self) -> str:
        return self._result_edit.toPlainText().strip()

    def _do_copy(self):
        text = self._result_text()
        if not text:
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:                      # 离屏/无剪贴板环境兜底
            self._status.setText("⚠ 当前环境没有剪贴板，复制失败")
            return
        clipboard.setText(text)
        self._status.setText(f"✓ 已复制 {len(text)} 字到剪贴板")
        flash_button(self._copy_btn, "✓ 已复制")

    def _do_fragment(self):
        text = self._result_text()
        if not text:
            self._status.setText("还没有结果可保存")
            return
        fid = self._ctx.write.add_fragment(text)
        if fid:
            self._status.setText("✓ 已存为碎片（可在碎片页查看）")
            flash_button(self._frag_btn, "✓ 已存碎片")
            self._ctx.show_toast("已存为碎片（可在碎片页查看）")
        else:
            self._status.setText("存为碎片失败（详见 app.log）")

    def _do_note(self):
        text = self._result_text()
        if not text:
            self._status.setText("还没有结果可保存")
            return
        title = first_line_title(text)
        nid = self._ctx.write.add_note(title, text)
        if nid:
            self._status.setText(f"✓ 已存为笔记：{title}")
            flash_button(self._note_btn, "✓ 已存笔记")
            self._ctx.show_toast("已存为笔记（可在笔记页查看）")
        else:
            self._status.setText("存为笔记失败（详见 app.log）")

    def _do_tasks(self):
        """把「提取待办」结果里的行动项逐条写入任务列表（无截止日期）"""
        if not self._tasks_btn.isEnabled():
            return
        tasks = parse_task_lines(self._result_text())
        if not tasks:
            self._status.setText("未识别到行动项")
            return
        written = 0
        for title in tasks:
            if self._ctx.write.add_task(title):
                written += 1
        if written:
            self._status.setText(
                f"✓ 已写入 {written}/{len(tasks)} 条任务（可在日程任务页查看）")
            flash_button(self._tasks_btn, f"✓ 已转 {written} 条")
            self._tasks_btn.setEnabled(False)   # 防重复点击造成重复任务
            self._tasks_btn.setToolTip("已写入，如需修改请到任务页")
            self._ctx.show_toast(f"已写入 {written} 条任务")
        else:
            self._status.setText("写入任务失败（详见 app.log）")


# ====================================================================
# 动作与插件
# ====================================================================
class WorkshopAction(BallAction):
    """切到文本工坊页（热键 Ctrl+Alt+T；页面缺席时兜底开主窗口）"""

    id = f"{PLUGIN_ID}.open"
    title = "✂ AI 文本工坊"

    def run(self, ctx: PluginContext):
        # 置「带入剪贴板」标记：页面 showEvent 消费（见 AiWorkshopPage）
        PENDING_HOTKEY["flag"] = True
        ctx.logger.info(f"[AI 文本工坊] 切到工坊页，数据源="
                        f"{ctx.data.sources() or '（无）'}")
        # 热键路径：run() 在原生事件过滤器里被调用，UI 操作必须延后到
        # 下一轮事件循环，防重入（项目铁律，见插件开发说明第 12 节）
        QTimer.singleShot(0, lambda: self._go(ctx))

    def _go(self, ctx: PluginContext):
        win = ctx.parent_window()
        show = getattr(win, "show_plugin_page", None)
        if callable(show) and show(PAGE_KEY):
            return
        ctx.open_main_window()    # 兜底：页面不在（插件页未注入）时至少开窗口


class AiTextWorkshopPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "AI 文本工坊"
    version = "1.4.0"

    def create_actions(self, ctx) -> list:
        return [WorkshopAction()]

    def create_page(self, ctx: PluginContext):
        return AiWorkshopPage(ctx)
