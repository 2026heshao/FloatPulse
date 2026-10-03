# -*- coding: utf-8 -*-
"""
====================================================================
凭证哨兵（Secret Guard） - 纯逻辑模块，零 PyQt6
====================================================================
剪贴板里的东西会自动进碎片明文池。43 天里实测 **18 条**（碎片 17 +
笔记 1）复制的是 API key / token 这类凭证，事后才被手工改标成
「[已清理的凭证]」——也就是说凭证已经在 `fragments.json` 里明文躺过
18 次，这是**已发生的事故**，不是风险假设。

本模块负责「这段文本里有没有凭证」的判定，只做纯字符串/正则/熵计算，
不 import 任何 UI 库，可被独立单测。写入路径的拦截与 UI 交互放在
`clipboard_monitor`（落盘前调用）与 `fragments_panel`（回溯入口）。

判定口径（保守优先，宁可多问不可误杀）
  - **命中即命中**：正则匹到已知凭证形态（GitHub token / OpenAI key /
    AWS access key id / Bearer token / PEM 私钥 / JWT / 高熵长串）；
  - **白名单放行**：git commit SHA（40/64 hex）、UUID、纯数字长 ID 这类
    看起来像密钥其实不是的东西，以及上下文里出现 `commit` / `rebase`
    / `cherry-pick` 等词（±20 字符窗口）的 hex 串，一律放行。
====================================================================
"""

import math
import re

# ====================================================================
# 命中后的三种处置（用户三选一，本模块只表达语义，不负责持久化）
# ====================================================================
MODE_DENY = "deny"        # 不记录（整条丢弃）
MODE_MASK = "mask"        # 记录为「••••••（已遮蔽）」占位
MODE_ALLOW = "allow"      # 仅本次记录原文（本次放行，不改配置）

# 合法处置集合（配置白名单与「本次记住」校验共用）
GUARD_MODES = (MODE_DENY, MODE_MASK, MODE_ALLOW)

# 默认处置走 MASK：命中时**先保守落占位**，绝不静默丢弃原文
# （用户看不到的删除等同于功能故障）；随后由 UI 给回溯入口让用户改判。
DEFAULT_MODE = MODE_MASK

# 遮蔽占位文案（用户可见；无 emoji，纯圆点 + 中文）
MASK_PLACEHOLDER = "••••••（已遮蔽的凭证）"

# ====================================================================
# 已知凭证形态
# ====================================================================
# 顺序即优先级：先具体（前缀明确）后泛化（高熵串）
_RULES = (
    # GitHub token：ghp_ / gho_ / ghu_ / ghs_ / ghr_ 及 ghpousr_ 这类复合前缀
    ("github_token", re.compile(r"\bgh[pousr][a-z]*_[A-Za-z0-9]{16,}\b")),
    # OpenAI 风格 sk-（含 sk-ws- / sk-proj- 等变体）；- 后面必须够长才算
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    # AWS access key id：AKIA / ASIA + 16 位大写字母数字
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    # Authorization: Bearer <token>
    ("bearer", re.compile(r"\bBearer\s+([A-Za-z0-9_\-\.=]{20,})")),
    # PEM 私钥头
    ("pem", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----")),
    # JWT：三段 base64url，中间段（claims）必须够长，避免误伤 a.b.c 普通串
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # 泛化：32+ 长的高熵 base64/hex 串（最后兜底，靠熵阈值 + 白名单收敛）
    ("entropy", re.compile(r"\b[A-Za-z0-9+/_\-]{32,}={0,2}\b")),
)

# 熵规则（"entropy"）的最小长度与香农熵阈值（bits/char）
ENTROPY_MIN_LEN = 32
ENTROPY_MIN_BITS = 3.5

# ====================================================================
# 误判白名单
# ====================================================================
# 纯数字长 ID（时间戳 / 主键）：只有数字，无字母，一定不是密钥
_PURE_DIGITS = re.compile(r"^[0-9]+$")
# UUID（8-4-4-4-12 hex）：正则泛化规则会匹到，但不是凭证
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# 纯 hex 串（git commit SHA 就是 40/64 位 hex，无熵可谈但形态像密钥）
_PURE_HEX = re.compile(r"^[0-9a-fA-F]+$")

# 上下文关键词窗口：命中串前后 ±20 字符内出现这些词 → 判为版本控制语境，放行
_CONTEXT_WORDS = ("commit", "rebase", "cherry-pick", "cherrypick", "sha",
                  "merge", "revision", "revisions", "gitsha")
_CONTEXT_WINDOW = 20


def shannon_entropy(text: str) -> float:
    """香农熵（bits/char）。空串返回 0.0。"""
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    ent = 0.0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(p)
    return ent


def _in_vcs_context(text: str, start: int, end: int) -> bool:
    """命中片段（text[start:end]）±窗口内是否出现版本控制关键词。"""
    lo = max(0, start - _CONTEXT_WINDOW)
    hi = min(len(text), end + _CONTEXT_WINDOW)
    window = text[lo:hi].lower()
    return any(word in window for word in _CONTEXT_WORDS)


def _whitelisted(token: str, text: str, start: int, end: int) -> bool:
    """单个命中片段是否应放行（误判白名单）。"""
    if _PURE_DIGITS.match(token):
        return True
    if _UUID.match(token):
        return True
    # 纯 hex：git commit SHA / 内容哈希。40 或 64 位是明确 SHA 形态；
    # 其余纯 hex 长串也无熵价值，且 ±20 字符有版本控制词时同样放行。
    if _PURE_HEX.match(token):
        if len(token) in (40, 64):
            return True
        if _in_vcs_context(text, start, end):
            return True
        # 纯 hex 且长度不是 SHA 形态：仍需高熵才认（短 hex 说明串放行）
    # 版本控制语境下的任意命中（如混进 SHA 说明的 base64）一律放行
    if _in_vcs_context(text, start, end):
        return True
    return False


def _is_credential_like(token: str) -> bool:
    """高熵兜底规则的加严闸门：必须「够长 + 够熵 + 混合字符类」。

    纯小写单词串成的长 URL 路径也能过熵阈值，但只有单一字符类
    （无数字/无大写），一定不是密钥 —— 真实 key 至少两位类混合。
    """
    if len(token) < ENTROPY_MIN_LEN:
        return False
    if shannon_entropy(token) < ENTROPY_MIN_BITS:
        return False
    has_lower = any(c.islower() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_digit = any(c.isdigit() for c in token)
    classes = int(has_lower) + int(has_upper) + int(has_digit)
    return classes >= 2


def detect(text: str):
    """检测文本中的凭证。

    :return: 命中清单（list[dict]），每项
             ``{"rule": 规则名, "match": 命中原文, "start": 起点, "end": 终点}``；
             无命中返回空列表。白名单豁免已在内部扣除。
    """
    if not text or not isinstance(text, str):
        return []
    hits = []
    for rule_name, pattern in _RULES:
        for m in pattern.finditer(text):
            if rule_name == "bearer":
                # 命中原文是 "Bearer xxx"，实际密钥是捕获组
                token = m.group(1)
                start, end = m.start(1), m.end(1)
            else:
                token = m.group(0)
                start, end = m.start(), m.end()
            if rule_name == "entropy" and not _is_credential_like(token):
                continue
            if _whitelisted(token, text, start, end):
                continue
            hits.append({
                "rule": rule_name,
                "match": token,
                "start": start,
                "end": end,
            })
    # 同一段文本被多条规则命中（如 JWT 同时过 jwt 与 entropy）只留最靠前
    # 起始的那条（规则表按「具体 → 泛化」排序，更具体者先出现）。
    hits.sort(key=lambda h: (h["start"], h["end"]))
    deduped = []
    covered_end = -1
    for h in hits:
        if h["start"] < covered_end:
            continue                 # 与上一条重叠：已被覆盖
        deduped.append(h)
        covered_end = h["end"]
    return deduped


def contains_secret(text: str) -> bool:
    """文本是否含凭证（便捷入口，语义等价 ``bool(detect(text))``）。"""
    return bool(detect(text))


def mask_text(text: str) -> str:
    """把文本里所有命中的凭证替换成遮蔽占位，返回脱敏后的文本。

    未被命中的内容原样保留——用户仍能看到「复制的是哪一段」，
    只是密钥本体不落明文。
    """
    hits = detect(text)
    if not hits:
        return text
    # 从后往前替换，避免下标漂移
    out = text
    for h in sorted(hits, key=lambda x: x["start"], reverse=True):
        out = out[:h["start"]] + MASK_PLACEHOLDER + out[h["end"]:]
    return out


def sanitize_mode(value, default: str = DEFAULT_MODE) -> str:
    """把任意配置值收敛为合法处置；非法值回退 default。"""
    if isinstance(value, str) and value in GUARD_MODES:
        return value
    return default
