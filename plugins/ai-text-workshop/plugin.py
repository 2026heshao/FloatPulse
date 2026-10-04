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

结果历史与指令收藏（v1.5.0，W1/W2）：
  成功动作自动进「🕘 历史」（存插件私有目录 history.json，最近 20 条），
  关页不再丢结果——点历史条目填回结果区，可编辑/复制/再落库；
  常用自定义指令「★ 收藏指令」存成按钮（10 条上限），点击填入输入框。

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
import os
import re
import uuid
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMenu, QPlainTextEdit,
    QScrollArea, QVBoxLayout, QWidget, QLineEdit,
)
from src.controls import SmoothButton
from src.plugin_api import BallAction, BallPlugin, PluginContext
from src.plugin_ui import (
    flash_button, make_hint_label, make_section_label,
)

PLUGIN_ID = "ai-text-workshop"
PAGE_KEY = f"plugin:{PLUGIN_ID}"       # 主窗口页面 key（与 loader 约定一致）

# 单次发给模型的原文上限（超长截断并标注）
MAX_SOURCE_CHARS = 8000
# 生成参数默认值（v1.6.0 起可在插件中心「设置」调整，本常量作旧宿主兜底；
# 工坊动作都是短单轮转换，后端地址等仍在设置页）
TEMPERATURE = 0.4
MAX_TOKENS = 2048
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.1, 1.0
MAX_TOKENS_MIN, MAX_TOKENS_MAX = 256, 4096
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
    ("polish_email", "润色成邮件",
     "把待处理文本润色成一封得体的工作邮件（第一行是「主题：…」，"
     "随后是正文）。保持原意，语气专业礼貌，只输出邮件本身。"),
    ("translate_zh", "翻成中文",
     "把待处理文本翻译成简体中文，保留原有分段，只输出译文。"),
    ("translate_en", "翻成英文",
     "把待处理文本翻译成英文，保留原有分段，只输出译文。"),
    ("summarize", "总结要点",
     "把待处理文本总结成 Markdown 要点列表，不超过 8 条，只输出要点。"),
    ("formalize", "改写正式",
     "把待处理文本改写成正式、书面的表达，保持原意与篇幅，"
     "只输出改写结果。"),
    ("extract_tasks", "提取待办",
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


def clamp_temperature(raw, fallback=TEMPERATURE):
    """插件设置 temperature 的生效值钳制（纯函数，测试钉行为）。

    非数字回落 fallback（旧宿主无契约 / 脏值都走这里）；越界钳到
    [0.1, 1.0]——存储层已按 schema 收窄，这里防的是手改 settings.json。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return max(TEMPERATURE_MIN, min(TEMPERATURE_MAX, value))


def clamp_max_tokens(raw, fallback=MAX_TOKENS):
    """插件设置 max_tokens 的生效值钳制（纯函数，测试钉行为）。

    非整数回落 fallback；钳到 [256, 4096]——8G 显存本机跑 7B 级模型时
    输出上限直接影响可用性与速度，不放开无界值。
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(MAX_TOKENS_MIN, min(MAX_TOKENS_MAX, value))


def build_request(params: dict, instruction: str, source: str,
                  max_source_chars: int = 8000,
                  temperature=None, max_tokens=None):
    """AI 总配置参数 + 指令 + 原文 → (url, headers, body, 错误文案)

    成功时错误文案为空串；失败时前三个返回值都是 None。
    ``params`` 来自 ``ctx.ai.params()``（字段契约见 plugin_api）：
    mode / base_url / api_key / model / local_port。
    OpenAI 兼容 /chat/completions，非流式单轮（无对话历史）。
    Content-Type 由宿主网络桥负责，headers 只放鉴权。
    ``temperature`` / ``max_tokens`` 来自插件设置生效值；None（旧宿主 /
    未传）走默认常量。
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
            return None, None, None, "后端地址为空：到 设置 → AI 总配置 填写"
        if not model:
            return None, None, None, "模型名为空：到 设置 → AI 总配置 填写"
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
        "temperature": clamp_temperature(temperature),
        "max_tokens": clamp_max_tokens(max_tokens),
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
            hint = "（key 缺失或无效？到 设置 → AI 总配置 检查）"
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
# 结果历史 + 自定义指令收藏（v1.5.0，W1/W2）
# 存插件私有目录 history.json：动作结果可回看/再落库（关页即丢是
# v1.4 最大的使用痛点），常用自定义指令可收藏成按钮。上限刻意收紧
# ——历史条目里结果截 6000 字、源文摘要只留 120 字，防文件膨胀。
# ====================================================================
HISTORY_FILE = "history.json"
HISTORY_LIMIT = 20      # 最多保留的历史条数（丢最旧）
PRESET_LIMIT = 10       # 最多收藏的自定义指令条数（丢最旧）
HISTORY_RESULT_CHARS = 6000   # 单条历史保留的结果字符数
HISTORY_SRC_CHARS = 120       # 历史条目里的源文摘要字符数（只做辨识）
PRESET_CHARS = 200            # 单条收藏指令的字符数


def _store_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sanitize_workshop_store(raw) -> dict:
    """history.json 消毒：脏数据宽容降级（坏条目整条丢弃，绝不抛）"""
    store = {"history": [], "presets": []}
    if not isinstance(raw, dict):
        return store
    history = []
    for h in raw.get("history") or []:
        if not isinstance(h, dict):
            continue
        result = str(h.get("result") or "")[:HISTORY_RESULT_CHARS]
        if not result.strip():
            continue
        history.append({
            "ts": str(h.get("ts") or "")[:32],
            "key": str(h.get("key") or "")[:40],
            "label": str(h.get("label") or "")[:40] or "动作",
            "instruction": str(h.get("instruction") or "")[:PRESET_CHARS],
            "source": str(h.get("source") or "")[:HISTORY_SRC_CHARS],
            "result": result,
        })
    store["history"] = history[:HISTORY_LIMIT]
    presets = []
    seen = set()
    for p in raw.get("presets") or []:
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "").strip()[:PRESET_CHARS]
        if not text or text in seen:
            continue
        seen.add(text)
        presets.append({"id": str(p.get("id") or "")[:40] or uuid.uuid4().hex[:12],
                        "text": text})
    store["presets"] = presets[:PRESET_LIMIT]
    return store


def load_workshop_store(ctx) -> dict:
    """读历史存档；文件缺失/写坏只降级为空档，原文件保留供抢救"""
    path = (os.path.join(ctx.data_dir, HISTORY_FILE)
            if getattr(ctx, "data_dir", "") else "")
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sanitize_workshop_store(json.load(f))
        except (OSError, ValueError):
            pass
    return sanitize_workshop_store(None)


def save_workshop_store(ctx, store: dict) -> bool:
    """写历史存档（临时文件 + os.replace 原子替换）；失败只告警不抛"""
    path = (os.path.join(ctx.data_dir, HISTORY_FILE)
            if getattr(ctx, "data_dir", "") else "")
    if not path:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger = getattr(ctx, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"[AI 文本工坊] 历史存档写入失败：{exc}")
            except Exception:               # noqa: BLE001
                pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def fmt_history_ts(ts: str, today: str = "") -> str:
    """存档时间戳 → 短展示（今天显示 HH:MM，往年今天显示 M-D）"""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    today = today or _store_now()[:10]
    if ts[:10] == today:
        return dt.strftime("%H:%M")
    return f"{dt.month}-{dt.day}"


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
        # 结果历史 + 指令收藏（W1/W2）：插件私有目录 history.json
        self._store = load_workshop_store(ctx)
        self._pending_meta = None   # 在途请求元信息（成功后落历史）

        # 插件独立设置（v1.6.0）：读生效值 + 订阅插件中心保存通知。
        # 订阅按宿主铁律走守卫模式——旧宿主没有该信号时自然跳过。
        self._temperature = TEMPERATURE
        self._max_tokens = MAX_TOKENS
        self._apply_settings()
        self._subscribe_settings()
        self.setObjectName("pluginPage")   # 吃主窗口 QSS 的实底（theme.py）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 整页滚动兜底：窗口偏矮时出滚动条而不是挤压控件（实测踩过的坑）
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
            btn = SmoothButton(label, self)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(instruction)
            btn.clicked.connect(
                lambda _checked=False, k=key, p=instruction:
                    self._run_action(k, p))
            self._action_btns[key] = btn
            action_row.addWidget(btn)
        action_row.addStretch(1)
        # 「🕘 历史」开关（W1）：展开/收起历史卡
        self._history_btn = SmoothButton("历史", self)
        self._history_btn.setObjectName("secondaryBtn")
        self._history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_btn.setToolTip("最近 20 次动作结果，点击可回看再利用")
        self._history_btn.clicked.connect(self._toggle_history)
        action_row.addWidget(self._history_btn)
        lay.addLayout(action_row)

        # ---- 历史卡（W1）：默认收起；行按钮点击=结果填回结果区 ----
        self._history_card = QFrame(self)
        self._history_card.setObjectName("glassCard")
        hc = QVBoxLayout(self._history_card)
        hc.setContentsMargins(16, 12, 16, 12)
        hc.setSpacing(6)
        hist_head = QHBoxLayout()
        hist_head.addWidget(make_section_label("最近结果"))
        hist_head.addWidget(make_hint_label("点击一条填回结果区（可编辑/复制/落库）；右键删除该条"))
        hist_head.addStretch(1)
        self._hist_clear_btn = SmoothButton("清空历史", self)
        self._hist_clear_btn.setObjectName("secondaryBtn")
        self._hist_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hist_clear_btn.clicked.connect(self._clear_history)
        hist_head.addWidget(self._hist_clear_btn)
        hc.addLayout(hist_head)
        self._hist_empty = make_hint_label("还没有历史：执行一次动作后自动记录")
        hc.addWidget(self._hist_empty)
        self._hist_body = QWidget(self._history_card)
        self._hist_lay = QVBoxLayout(self._hist_body)
        self._hist_lay.setContentsMargins(0, 0, 0, 0)
        self._hist_lay.setSpacing(4)
        hc.addWidget(self._hist_body)
        self._history_scroll = QScrollArea(self._history_card)
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._history_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._history_scroll.setWidget(self._hist_body)
        self._history_scroll.setMaximumHeight(220)
        hc.addWidget(self._history_scroll)
        self._history_card.setVisible(False)
        lay.addWidget(self._history_card)

        # ---- 自定义指令行 ----
        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        self._custom_edit = QLineEdit(self)
        self._custom_edit.setPlaceholderText(
            "自定义指令（如：把这段话改写成三条朋友圈文案）…")
        self._custom_edit.returnPressed.connect(self._run_custom)
        custom_row.addWidget(self._custom_edit, 1)
        self._custom_btn = SmoothButton("执行", self)
        self._custom_btn.setObjectName("primaryBtn")
        self._custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._custom_btn.clicked.connect(self._run_custom)
        custom_row.addWidget(self._custom_btn)
        self._fav_btn = SmoothButton("收藏指令", self)
        self._fav_btn.setObjectName("secondaryBtn")
        self._fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fav_btn.setToolTip(
            "把输入框里的指令收藏成按钮（右键收藏按钮可删除）")
        self._fav_btn.clicked.connect(self._save_preset)
        custom_row.addWidget(self._fav_btn)
        lay.addLayout(custom_row)

        # ---- 收藏指令行（W2）：点按钮=填入输入框，右键=删除该收藏 ----
        self._preset_widget = QWidget(self)
        self._preset_lay = QHBoxLayout(self._preset_widget)
        self._preset_lay.setContentsMargins(0, 0, 0, 0)
        self._preset_lay.setSpacing(6)
        lay.addWidget(self._preset_widget)

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
        self._grab_btn = SmoothButton("带入剪贴板", self)
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
        self._copy_btn = SmoothButton("复制结果", self)
        self._frag_btn = SmoothButton("存为碎片", self)
        self._note_btn = SmoothButton("存为笔记", self)
        self._tasks_btn = SmoothButton("转任务", self)
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
            self._tasks_btn.setToolTip("先执行「提取待办」，识别到行动项后可用")

        self._refresh_presets()                 # 收藏按钮行（W2）
        self._refresh_history()                 # 历史卡空态/行（W1）

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
                "尚未接入 AI：到 设置 → AI 总配置 配好云端或本地后端，"
                "并在「接入插件」里勾选本插件")
        else:
            self._ai_hint.setText(
                "已接入设置页「AI 总配置」，后端改动即时生效")

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

    # ---------------- 插件设置（v1.6.0） ----------------
    def _get_setting(self, key, fallback):
        """读一个设置项生效值；旧宿主没有 get_setting 契约 → 常量兜底"""
        getter = getattr(self._ctx, "get_setting", None)
        if not callable(getter):
            return fallback
        try:
            value = getter(key, fallback)
        except Exception:                         # noqa: BLE001
            return fallback
        return fallback if value is None else value

    def _apply_settings(self):
        """把设置生效值读进页面状态（构建时 / 收到变更通知时）"""
        self._temperature = clamp_temperature(
            self._get_setting("temperature", TEMPERATURE))
        self._max_tokens = clamp_max_tokens(
            self._get_setting("max_tokens", MAX_TOKENS))

    def _subscribe_settings(self):
        """订阅插件中心的保存通知（守卫模式；订阅失败只影响实时性）"""
        sig = getattr(self._ctx, "settings_changed", None)
        connect = getattr(sig, "connect", None) if sig is not None else None
        if callable(connect):
            try:
                connect(self._on_settings_changed)
            except Exception:                     # noqa: BLE001
                pass

    def _on_settings_changed(self, _keys=None):
        """插件中心保存设置后：重读生效值，下一次动作即用新参数"""
        self._apply_settings()

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
                f"已带入剪贴板 "
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
                "尚未接入 AI：到 设置 → AI 总配置 配置并勾选本插件")
            return
        if params.get("mode") == "local" and not params.get("local_ready"):
            # 宿主本地服务的启停入口在设置页，这里只引导不代启
            self._status.setText(
                "宿主本地服务未就绪：到 设置 → AI 总配置 启动")
            return
        src_text = self._src_edit.toPlainText()
        url, headers, body, err = build_request(
            params, instruction, src_text, MAX_SOURCE_CHARS,
            temperature=self._temperature, max_tokens=self._max_tokens)
        if url is None:
            self._status.setText(err)
            return
        # 在途元信息：成功后落历史（W1）。label 供历史行辨识；
        # src 只留 120 字摘要做辨识，正文不进存档（隐私口径同日志）。
        label = dict((k, lbl) for k, lbl, _i in WORK_ACTIONS).get(key, "自定义")
        src = src_text.strip()[:HISTORY_SRC_CHARS]
        self._pending_meta = {
            "key": key, "label": label,
            "instruction": instruction if from_custom else "",
            "source": src,
        }
        # W3：源文超长会在 build_request 里按上限截断——处理中就明说，
        # 别等用户对比结果才发现被砍（截断标注在请求文本末尾，模型可见）
        if len(src_text.strip()) > MAX_SOURCE_CHARS:
            busy_text = (f"处理中…（源文超 {MAX_SOURCE_CHARS} 字，"
                         "已按上限截断发送；模型响应通常几秒）")
        else:
            busy_text = "处理中…（模型响应通常几秒）"
        self._set_busy(True, busy_text)
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
        meta = self._pending_meta or {}
        self._pending_meta = None
        reply, err = parse_reply(result)
        if err:
            self._status.setText(err)
            return
        self._apply_result(reply, key)
        # 历史存档（W1）：只记成功动作
        self._record_history(meta.get("key") or key,
                             meta.get("label") or "动作",
                             meta.get("instruction") or "",
                             meta.get("source") or "", reply)
        if key == "extract_tasks":
            count = len(parse_task_lines(reply))
            self._status.setText(
                f"识别到 {count} 条行动项" + (
                    "，点「转任务」写入任务列表" if count else ""))
        else:
            self._status.setText(f"完成，共 {len(reply)} 字")
        if from_custom:
            self._custom_edit.clear()

    def _apply_result(self, reply: str, key: str):
        """结果填进结果区并刷新「转任务」可用态（正常回复 / 历史回填共用）"""
        self._result_edit.setPlainText(reply)
        self._copy_btn.setEnabled(True)
        if key == "extract_tasks":
            count = len(parse_task_lines(reply))
            if self._can_write():
                self._tasks_btn.setEnabled(count > 0)
                self._tasks_btn.setToolTip(
                    f"把识别到的 {count} 条行动项写入任务列表"
                    if count else "未识别到行动项")
        else:
            if self._can_write():
                self._tasks_btn.setEnabled(False)

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

    # ---------------- 结果历史 + 指令收藏（W1/W2） ----------------
    @staticmethod
    def _hist_preview(text: str, limit: int = 60) -> str:
        """历史行预览：首个非空行，剥 Markdown 标记后截断"""
        for ln in str(text or "").splitlines():
            ln = ln.strip().strip("#*-> ").strip()
            if ln:
                return ln[:limit] + ("…" if len(ln) > limit else "")
        return "（空结果）"

    def _toggle_history(self):
        """展开/收起历史卡（每次展开都按存档重建行）"""
        will_show = not self._history_card.isVisible()
        if will_show:
            self._refresh_history()
        self._history_card.setVisible(will_show)

    def _refresh_history(self):
        """按存档重建历史行（点击=填回结果区，右键=删除该条）"""
        for row in getattr(self, "_hist_rows", []):
            self._hist_lay.removeWidget(row)
            row.deleteLater()
        self._hist_rows = []
        entries = self._store["history"]
        self._hist_empty.setVisible(not entries)
        self._hist_body.setVisible(bool(entries))
        self._history_scroll.setVisible(bool(entries))
        for i, h in enumerate(entries):
            ts = fmt_history_ts(h.get("ts") or "")
            head = f"[{h.get('label') or '动作'}{' ' + ts if ts else ''}]"
            preview = self._hist_preview(h.get("result") or "")
            btn = SmoothButton(f"{head} {preview}", self._hist_body)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(str(h.get("result") or "")[:400])
            btn.clicked.connect(
                lambda _checked=False, idx=i: self._use_history(idx))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, idx=i: self._history_item_menu(b, idx, pos))
            self._hist_lay.addWidget(btn)
            self._hist_rows.append(btn)

    def _history_item_menu(self, btn, idx: int, pos):
        menu = QMenu(self)
        act = menu.addAction("删除这条历史")
        act.triggered.connect(lambda: self._delete_history(idx))
        menu.exec(btn.mapToGlobal(pos))

    def _use_history(self, idx: int):
        """历史条目 → 结果区（可编辑/复制/落库；转任务按内容重新判定）"""
        if idx < 0 or idx >= len(self._store["history"]):
            return
        h = self._store["history"][idx]
        self._apply_result(str(h.get("result") or ""), h.get("key") or "")
        src = str(h.get("source") or "")
        self._status.setText("已从历史填入结果（可编辑/复制/落库）"
                             + (f"｜源文摘要：{src}" if src else ""))

    def _delete_history(self, idx: int):
        if idx < 0 or idx >= len(self._store["history"]):
            return
        del self._store["history"][idx]
        save_workshop_store(self._ctx, self._store)
        self._refresh_history()

    def _clear_history(self):
        if not self._store["history"]:
            return
        self._store["history"] = []
        save_workshop_store(self._ctx, self._store)
        self._refresh_history()
        self._status.setText("历史已清空")

    def _record_history(self, key: str, label: str, instruction: str,
                        source: str, result: str):
        """成功动作落历史（最前插入，超限丢最旧）并落盘"""
        self._store["history"].insert(0, {
            "ts": _store_now(), "key": key, "label": label,
            "instruction": str(instruction or "")[:PRESET_CHARS],
            "source": str(source or "")[:HISTORY_SRC_CHARS],
            "result": str(result or "")[:HISTORY_RESULT_CHARS]})
        self._store["history"] = self._store["history"][:HISTORY_LIMIT]
        save_workshop_store(self._ctx, self._store)
        if self._history_card.isVisible():
            self._refresh_history()

    def _save_preset(self):
        """把输入框当前指令收藏成按钮（去重、上限丢最旧）"""
        text = self._custom_edit.text().strip()
        if not text:
            self._status.setText("先在输入框写好指令，再点「收藏指令」")
            return
        presets = self._store["presets"]
        if any(p["text"] == text for p in presets):
            self._status.setText("这条指令已在收藏里")
            return
        presets.insert(0, {"id": uuid.uuid4().hex[:12], "text": text})
        self._store["presets"] = presets[:PRESET_LIMIT]
        save_workshop_store(self._ctx, self._store)
        self._refresh_presets()
        self._status.setText("已收藏：点下方按钮即可填入，右键按钮可删除")

    def _refresh_presets(self):
        """按存档重建收藏按钮行（无收藏时整行隐藏）"""
        while self._preset_lay.count():
            item = self._preset_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        presets = self._store["presets"]
        self._preset_widget.setVisible(bool(presets))
        for p in presets:
            text = str(p.get("text") or "")
            btn = SmoothButton(f"{text[:16]}" + ("…" if len(text) > 16 else ""),
                              self._preset_widget)
            btn.setObjectName("secondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{text}\n\n点击填入输入框（可改后执行）；右键删除收藏")
            btn.clicked.connect(
                lambda _checked=False, t=text: self._use_preset(t))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, pid=p["id"]: self._preset_menu(b, pid, pos))
            self._preset_lay.addWidget(btn)
        self._preset_lay.addStretch(1)

    def _preset_menu(self, btn, pid: str, pos):
        menu = QMenu(self)
        act = menu.addAction("删除这条收藏")
        act.triggered.connect(lambda: self._delete_preset(pid))
        menu.exec(btn.mapToGlobal(pos))

    def _use_preset(self, text: str):
        """收藏指令填入输入框（不自动执行，改完再跑）"""
        self._custom_edit.setText(text)
        self._custom_edit.setFocus()
        self._status.setText("已填入收藏指令，可修改后执行")

    def _delete_preset(self, pid: str):
        self._store["presets"] = [p for p in self._store["presets"]
                                  if p.get("id") != pid]
        save_workshop_store(self._ctx, self._store)
        self._refresh_presets()

    # ---------------- 结果出口 ----------------
    def _result_text(self) -> str:
        return self._result_edit.toPlainText().strip()

    def _do_copy(self):
        text = self._result_text()
        if not text:
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:                      # 离屏/无剪贴板环境兜底
            self._status.setText("当前环境没有剪贴板，复制失败")
            return
        clipboard.setText(text)
        self._status.setText(f"已复制 {len(text)} 字到剪贴板")
        flash_button(self._copy_btn, "已复制")

    def _do_fragment(self):
        text = self._result_text()
        if not text:
            self._status.setText("还没有结果可保存")
            return
        fid = self._ctx.write.add_fragment(text)
        if fid:
            self._status.setText("已存为碎片（可在碎片页查看）")
            flash_button(self._frag_btn, "已存碎片")
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
            self._status.setText(f"已存为笔记：{title}")
            flash_button(self._note_btn, "已存笔记")
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
                f"已写入 {written}/{len(tasks)} 条任务（可在日程任务页查看）")
            flash_button(self._tasks_btn, f"已转 {written} 条")
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
    title = "AI 文本工坊"

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
    version = "1.6.0"

    def create_actions(self, ctx) -> list:
        return [WorkshopAction()]

    def create_page(self, ctx: PluginContext):
        return AiWorkshopPage(ctx)
