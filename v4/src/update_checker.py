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
