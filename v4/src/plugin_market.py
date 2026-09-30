# -*- coding: utf-8 -*-
"""应用内插件市场的纯逻辑（唯一真相源，2026-09-30）。

设计立场（与 update_checker 完全对齐，回应「程序不联网」的产品承诺）：
  - **只在用户点击「🌐 检查在线市场」时联网**：拉一次索引 JSON
    （仓库 marketplace/index.json，经 api.github.com contents 接口
    取 raw），再拉一次 /releases/latest 解析 .fpplug 附件的 asset id；
  - **离线零影响**：任何失败都归一成结果 dict / 错误串，不重试、
    不阻塞，商店的本地安装功能照常；
  - **下载带完整性校验**：索引里带 sha256 与 size，下载后先比哈希
    再落盘，对不上就丢弃，绝不把坏包放进商店目录；
  - **不做静默升级**：已安装的插件不自动下载新版本，只提示「可更新」，
    更不覆盖用户已装的插件目录（install_from_store 的拒绝覆盖口径不变）。

本模块**刻意不 import PyQt6**（与 update_checker.py 同款约束）；
异步包装（QThread）在 UI 层（plugins_panel.py）做。
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from src.app_version import APP_VERSION, parse_version
from src.net_guard import guard_url
from src.plugin_api import is_safe_plugin_id
from src.update_checker import GITHUB_REPO

# 索引的唯一外部来源：仓库内 marketplace/index.json（main 分支）。
# 经 contents 接口 + raw Accept 头取原文（api.github.com 可达性比
# raw.githubusercontent 好，且与更新检查走同一个域名）。
INDEX_PATH = "marketplace/index.json"
INDEX_API_URL = (f"https://api.github.com/repos/{GITHUB_REPO}"
                 f"/contents/{INDEX_PATH}")

# 下载 .fpplug：由 /releases/latest 的 assets 里按文件名解析出 asset id，
# 再经 octet-stream 通道下载（302 到 release-assets CDN，实测可达）。
MARKET_BRANCH = "main"

# 单次轻量请求的超时与下载包大小上限（fpplug 都在几十 KB，4MB 是防御值）
INDEX_TIMEOUT_S = 15.0
RELEASE_TIMEOUT_S = 15.0
DOWNLOAD_TIMEOUT_S = 30.0
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _ua() -> str:
    return f"FloatPulse/{APP_VERSION} (plugin-market)"


def index_headers() -> dict:
    """拉索引用的请求头。Accept=raw 让 contents 接口直接返回文件原文。"""
    return {
        "User-Agent": _ua(),
        "Accept": "application/vnd.github.raw+json",
    }


def download_headers() -> dict:
    """下载 .fpplug 用的请求头（octet-stream 通道）。"""
    return {
        "User-Agent": _ua(),
        "Accept": "application/octet-stream",
    }


# ---------------- 索引解析 ----------------
def parse_index(body) -> tuple:
    """把索引 JSON 文本解析成 ``(items, problems)``。

    - 成功：items 是**已消毒**的条目列表（只保留白名单字段，全部强转/
      校验过；单个条目坏只丢那条，记进 problems，不拖垮整批）；
    - 致命错误（非 JSON / 顶层结构不对 / plugins 不是列表）：
      返回 ``([], [原因])``，调用方据此提示「响应格式异常」。
    """
    if not isinstance(body, str) or not body.strip():
        return [], ["响应为空"]
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return [], ["响应不是合法 JSON"]
    if not isinstance(data, dict):
        return [], ["响应顶层不是对象"]
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return [], ["索引缺少 plugins 列表"]

    items, problems = [], []
    for raw in plugins:
        if not isinstance(raw, dict):
            problems.append("跳过一个非对象条目")
            continue
        pid = raw.get("id")
        if not is_safe_plugin_id(pid):
            problems.append(f"跳过非法 id 条目：{pid!r}")
            continue
        # file 必须是纯文件名（防路径穿越——下载落盘只认商店目录下的名字）
        fname = raw.get("file")
        if (not isinstance(fname, str) or not fname.lower().endswith(".fpplug")
                or "/" in fname or "\\" in fname or fname != os.path.basename(fname)):
            problems.append(f"{pid}: file 字段不是合法包文件名")
            continue
        sha = raw.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha.strip().lower()):
            problems.append(f"{pid}: sha256 缺失或格式不对")
            continue
        size = raw.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            problems.append(f"{pid}: size 缺失或非法")
            continue
        caps = [c for c in (raw.get("capabilities") or [])
                if isinstance(c, str)]
        hotkeys = [h for h in (raw.get("hotkeys") or [])
                   if isinstance(h, str)]
        items.append({
            "id": pid,
            "name": str(raw.get("name") or pid),
            "version": str(raw.get("version") or "?"),
            "file": fname,
            "size": size,
            "sha256": sha.strip().lower(),
            "description": str(raw.get("description") or ""),
            "capabilities": caps,
            "hotkeys": hotkeys,
        })
    if not items and not problems:
        problems = ["索引里没有插件条目"]
    return items, problems


def installed_state(items, installed_versions) -> tuple:
    """按已装版本把索引条目分成 ``(pending, updatable)``。

    ``installed_versions`` 是 ``{plugin_id: 本地版本串}``（缺版本按最老
    处理）。pending = 本地没装的；updatable = 已装但远端版本**严格更新**
    （复用 app_version.parse_version，任一侧解析失败不算可更新——
    宁漏报不误报）。本函数纯计算、无 I/O。
    """
    pending, updatable = [], []
    for it in items:
        local = (installed_versions or {}).get(it["id"], "")
        if not local:
            pending.append(it)
            continue
        a = parse_version(it["version"])
        b = parse_version(local) if isinstance(local, str) else None
        if a is not None and b is not None and a > b:
            updatable.append(it)
    return pending, updatable


# ---------------- Release 附件解析 ----------------
def find_asset_id(release_body, filename) -> int:
    """从 /releases/latest 响应里按**文件名**找附件，返回 asset id。

    索引只存文件名不存 id（每次发版 asset id 都会变），运行时解析。
    找不到 / 响应不合法返回 0（调用方提示「下载地址解析失败」）。
    """
    if not isinstance(release_body, str) or not release_body:
        return 0
    try:
        data = json.loads(release_body)
    except (ValueError, TypeError):
        return 0
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        return 0
    for a in assets:
        if (isinstance(a, dict) and a.get("name") == filename
                and isinstance(a.get("id"), int)):
            return a["id"]
    return 0


def asset_download_url(asset_id: int) -> str:
    """asset id → octet-stream 下载地址（api.github.com，302 到 CDN）。"""
    return (f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/releases/assets/{int(asset_id)}")


# ---------------- 二进制下载（纯 stdlib，永不抛异常） ----------------
def http_get_bytes(url, headers=None, timeout=DOWNLOAD_TIMEOUT_S,
                   max_bytes=MAX_DOWNLOAD_BYTES) -> dict:
    """同步 GET 二进制。契约与 plugin_net._http_request 同构：
    永不抛异常，失败归一成 ``{"ok": False, "error": ...}``；
    成功返回 ``{"ok": True, "status": 200, "data": bytes}``。
    与文本 GET 分开实现的原因：二进制不能走 utf-8 replace 解码。
    """
    result = {"ok": False, "status": 0, "data": b"", "error": "",
              "url": str(url or "")}
    guard = guard_url(url)
    if not guard.ok:
        result["error"] = guard.reason
        return result
    try:
        req = urllib.request.Request(str(url), method="GET")
        for key, val in dict(headers or {}).items():
            req.add_header(str(key), str(val))
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                result["error"] = f"下载超过大小上限（>{max_bytes} 字节）"
                return result
            result.update(ok=True, status=int(resp.status), data=raw)
            return result
    except urllib.error.HTTPError as exc:
        result.update(status=int(exc.code), error=f"HTTP {exc.code}")
        return result
    except Exception as exc:              # noqa: BLE001 - 网络/超时/代理等
        result["error"] = repr(exc)
        return result


def sha256_ok(data: bytes, expected: str) -> bool:
    """下载内容与索引里的 sha256 是否一致（大小写不敏感）。"""
    if not isinstance(data, bytes) or not expected:
        return False
    return hashlib.sha256(data).hexdigest() == expected.strip().lower()


def save_to_store(data: bytes, store_dir: str, filename: str) -> tuple:
    """把下载内容落盘到商店目录，返回 ``(ok, 路径或原因)``。

    - ``filename`` 必须是纯文件名（parse_index 已保证，这里再挡一次）；
    - 同名旧包（本地商店里的旧版本源包）会被**替换**——市场的语义就是
      「拿到索引里的这一版」，替换的只是源包，已安装目录不受影响；
    - 原子写：先写 .tmp 再 os.replace，写一半断电不会留下半个 zip。
    """
    if (not isinstance(filename, str) or not filename.lower().endswith(".fpplug")
            or "/" in filename or "\\" in filename
            or filename != os.path.basename(filename)):
        return False, f"包文件名不合法：{filename!r}"
    if not store_dir:
        return False, "商店目录不可用"
    try:
        os.makedirs(store_dir, exist_ok=True)
        target = os.path.join(store_dir, filename)
        tmp = target + ".market-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return True, target
    except OSError as exc:
        return False, f"写入商店目录失败：{exc}"
