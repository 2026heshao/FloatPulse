# -*- coding: utf-8 -*-
"""程序更新检查的纯逻辑（唯一真相源，2026-09-29）。

设计立场（回应「程序不联网」的产品承诺）：
  - **只做手动检查**：设置 → 关于 → 「🔍 检查更新」，仅在用户点击那
    一刻向 GitHub Releases API 发一次 GET（/releases/latest，只返回
    最新 tag，不携带任何本机数据、无账号、无遥测）；
  - **离线零影响**：请求失败 → 状态行一句「检查失败」，不重试、不
    阻塞、不弹窗，断网时其余功能照常；
  - **发现新版不自动下载**：只提供「打开下载页」，下载安装由用户决定。

本模块**刻意不 import PyQt6**（与 app_version.py 同款约束），
纯逻辑测试与工具脚本无 GUI 也能引用。
"""

import json
from datetime import date as _date

from src.app_version import APP_VERSION, parse_version

# 更新信息的唯一外部来源（tag 驱动发布：CI 打 vX.Y.Z tag → Release）
GITHUB_REPO = "2026heshao/FloatPulse"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

# 检查更新单次超时（秒）：一次轻量 GET，失败就失败，不让用户等
CHECK_TIMEOUT_S = 15.0


def check_headers() -> dict:
    """请求头。GitHub API 强制要求 User-Agent；不带会被 403。"""
    return {
        "User-Agent": f"FloatPulse/{APP_VERSION} (release-check)",
        "Accept": "application/vnd.github+json",
    }


def parse_tag(tag) -> tuple:
    """把 Release tag 解析成版本元组：``"v4.7.0"`` → ``(4, 7, 0)``。

    容忍 v/V 前缀与 ``-beta`` / ``-rc.1`` 后缀（取 ``-`` 前的数字段）；
    非法（空 / 非点分数字）返回 ``None``，交由调用方提示格式异常。
    复用 app_version.parse_version，与宿主/插件的版本语义同源。
    """
    if not isinstance(tag, str):
        return None
    core = tag.strip().lstrip("vV").split("-", 1)[0].strip()
    return parse_version(core)


def is_newer(latest_tag, current=APP_VERSION) -> bool:
    """latest_tag 是否比当前程序版本**新**（严格大于；等版本不算）。

    任一侧解析失败一律 False（宁可漏报不可误报——让用户白跑下载页
    比错过一次更新更伤信任）；段数不齐按补零对齐（(4,7) ≡ (4,7,0)）。
    """
    a = parse_tag(latest_tag)
    b = parse_version(current) if isinstance(current, str) else None
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def extract_tag(response_body) -> str:
    """从 /releases/latest 响应体里取 ``tag_name``；取不到返回空串。

    响应是 GitHub 的 JSON（``{"tag_name": "v4.7.0", ...}``）；body 截断
    / 非 JSON / 字段缺失都归一成空串，调用方据此提示「响应格式异常」。
    """
    if not isinstance(response_body, str) or not response_body:
        return ""
    try:
        data = json.loads(response_body)
    except (ValueError, TypeError):
        return ""
    tag = data.get("tag_name") if isinstance(data, dict) else None
    return tag if isinstance(tag, str) else ""


# ====================================================================
# 被动提示（2.3）：启动后每天至多一次的静默检查判定（纯逻辑，无 I/O）
# ====================================================================
def should_check_now(cfg, today=None) -> bool:
    """判断「现在」是否应该做一次静默更新检查。

    ``cfg`` 只需提供 ``get(key, default)`` 接口（ConfigManager / dict
    替身均可）。规则（频率护栏，宁少勿扰）：
      - ``auto_check_updates`` 为 False → False（用户明确关掉就不查）
      - ``last_update_check`` 已是今天 → False（每天至多一次）
      - 其余（含从未检查过的空值）→ True
    ``today`` 可注入 "YYYY-MM-DD" 字符串（测试用）；缺省取本机今天。
    本函数不发任何网络请求，也不写配置。
    """
    if not cfg.get("auto_check_updates", True):
        return False
    if today is None:
        today = _date.today().isoformat()
    last = cfg.get("last_update_check", "") or ""
    return last != today


def mark_checked(cfg, today=None):
    """把「今天已检查过」记入 cfg（仅内存；写盘由调用方负责 save）。

    无论后续请求成功与否，调用方都应在**发起检查时**调用本函数——
    失败静默的立场意味着失败的检查同样消耗掉当天的配额，避免每次
    启动都对着打不通的网络重试。``today`` 可注入（测试用）。
    """
    if today is None:
        today = _date.today().isoformat()
    cfg.set("last_update_check", str(today))
