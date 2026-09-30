# -*- coding: utf-8 -*-
"""
====================================================================
SSRF 防护闸  -  net_guard
====================================================================
插件网络桥（plugin_net）发请求前的 URL 安全闸，**纯标准库**。

背景：插件经宿主桥发 HTTP 请求，若只校验 http(s) 前缀，插件就能打
到本机与内网任意端口（云元数据服务 169.254.169.254、本机管理面板、
局域网设备……）。本模块把宿主下载通道（knowledge_ball._download_url
的四道闸）里的「协议白名单 + 拒绝本地/内网地址」口径抽成公共实现，
并补齐 IPv4-mapped IPv6、未指定地址等边角。

guard_url 判定规则（任一命中即拒）：
  1. 协议白名单：仅 http / https（ftp://、file://、无 scheme 拒）；
  2. 主机名黑名单：``localhost``、``*.localhost``、``*.local``、
     ``*.internal`` 直接拒（不进解析）；
  3. 解析全量校验：socket.getaddrinfo 取主机名的**全部**地址，逐个
     ``is_private_ip`` 判定，任一命中即拒（多条记录时不许漏网）；
  4. 解析失败（域名不存在 / DNS 故障）按拒绝处理（fail-closed）。

is_private_ip 拒绝口径（与宿主下载闸对齐并更严）：
  回环 / 私网（10/8、172.16/12、192.168/16、fc00::/7 等，即
  ``ipaddress.is_private``）/ 链路本地（169.254/16、fe80::/10）/
  保留 / 组播 / 未指定（0.0.0.0、::）；IPv4-mapped IPv6
  （::ffff:127.0.0.1）先还原成 IPv4 再判。

开关：``set_allow_private_network(True)`` 供宿主后续接设置项（放行
内网 LAN），默认 False = 拦截；插件侧没有任何途径触达本开关。

回环白名单：本地 AI（宿主拉起的 llama-server 8095、用户配的 Ollama
127.0.0.1:11434）必须走插件桥，一刀切拦回环会误伤——宿主通过
``allow_loopback_endpoint(host, port)`` **显式登记**允许的回环端点
（见 src/ai_server.sync_loopback_allowlist），只有精确命中登记项的
回环请求才放行，其余回环/内网一律照拒。

遗留项：urllib 默认跟随重定向，本闸只做**单请求**校验；重定向逐跳
重新过闸留待后续波次。
====================================================================
"""

import ipaddress
import socket
from typing import NamedTuple, Optional
from urllib.parse import urlparse

__all__ = ["GuardResult", "allow_loopback_endpoint", "clear_loopback_endpoints",
           "guard_url", "is_local_hostname", "is_private_ip",
           "set_allow_private_network"]


class GuardResult(NamedTuple):
    """guard_url 的判定结果。

    ok     : 是否放行
    reason : 拒绝原因（中文、面向日志）；放行为空串
    host   : 主机名（小写、去 [] 与结尾点）；拿不到为空串
    ip     : 命中拦截的 IP（放行时为首个解析地址）；拿不到为空串
    """

    ok: bool
    reason: str
    host: str
    ip: str


# 模块级开关（见 set_allow_private_network docstring）：默认 False = 拦截
_ALLOW_PRIVATE_NETWORK = False

# 宿主登记的回环白名单：{(规范化 host, port)}，见 allow_loopback_endpoint
_LOOPBACK_ALLOW: set = set()

# 回环主机名的规范化映射：allowlist 只认精确登记项，
# 其余 127.x / 域名形式不走白名单，照旧进解析判定
_LOOPBACK_HOSTS = {
    "localhost": "127.0.0.1",
    "127.0.0.1": "127.0.0.1",
    "::1": "127.0.0.1",
}

# 本地/内网专用主机名后缀：mDNS / hosts 环境里几乎总指向本机或局域网
# （.internal 与宿主下载闸 _is_private_url_host 口径一致，.localhost 为补强）
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal")


def is_local_hostname(host: str) -> bool:
    """主机名是否属于本地/内网专用名（localhost / *.localhost / *.local / *.internal）。

    这类名字直接拒、不进解析；空主机名同样按本地处理（fail-closed）。
    """
    h = str(host or "").strip("[]").strip().lower().rstrip(".")
    if not h:
        return True
    return h == "localhost" or h.endswith(_LOCAL_HOST_SUFFIXES)


def is_private_ip(ip: str) -> bool:
    """判定单个 IP 字面量是否属于本地/内网（拒绝口径见模块头）。

    不是合法 IP 字面量（含空串）一律按私网处理 —— fail-closed。
    """
    try:
        addr = ipaddress.ip_address(str(ip).strip("[]").strip())
    except ValueError:
        return True
    # IPv4-mapped IPv6（::ffff:127.0.0.1）：还原成 IPv4 再判，
    # 不依赖各 Python 版本对 mapped 段的 is_private 口径差异
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            addr = mapped
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_reserved or addr.is_multicast
                or addr.is_unspecified)


def set_allow_private_network(allowed: bool) -> None:
    """设置是否放行本地/内网地址 —— **给宿主用的模块级开关**，默认 False=拦截。

    预留给宿主接设置项（后续波次）：例如高级用户声明信任局域网时，
    宿主在设置变更时调用本函数放行。插件侧只拿到 guard_url 的判定
    结果，没有任何途径改这个开关。
    True 时跳过 IP 层判定，协议白名单与主机名检查仍然生效。
    """
    global _ALLOW_PRIVATE_NETWORK
    _ALLOW_PRIVATE_NETWORK = bool(allowed)


def allow_loopback_endpoint(host: str, port: int) -> bool:
    """登记一个允许插件访问的回环端点 —— **只供宿主调用**（本地 AI 场景）。

    - host 仅接受 127.0.0.1 / localhost / ::1（规范化后统一记 127.0.0.1），
      其余主机名忽略并返回 False——白名单只为「宿主自己拉起的本机服务」服务；
    - port 为整数端口；重复登记幂等；
    - 返回是否登记成功。
    """
    norm = _LOOPBACK_HOSTS.get(str(host or "").strip("[]").strip().lower().rstrip("."))
    if norm is None:
        return False
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return False
    if not (0 < port_i < 65536):
        return False
    _LOOPBACK_ALLOW.add((norm, port_i))
    return True


def clear_loopback_endpoints() -> None:
    """清空回环白名单（测试隔离用）。"""
    _LOOPBACK_ALLOW.clear()


def guard_url(url: str) -> GuardResult:
    """对 URL 做发请求前的 SSRF 判定；拒绝理由中文、面向日志。

    本函数只做判定，唯一的系统调用是 getaddrinfo（域名解析），
    不发任何真实请求。默认拦截本地/内网；宿主可通过
    ``set_allow_private_network(True)`` 关闭 IP 层判定（协议白名单
    与主机名检查不受开关影响）。
    """
    url_s = str(url or "").strip()
    if not url_s:
        return GuardResult(False, "URL 为空，已拒绝", "", "")
    try:
        parts = urlparse(url_s)
    except ValueError as exc:            # noqa: BLE001 - 畸形 URL（如坏 IPv6 字面量）
        return GuardResult(False, f"URL 无法解析，已拒绝：{exc!r}", "", "")

    # 闸 1：协议白名单
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return GuardResult(False,
                           f"仅支持 http/https 链接（收到 {scheme or '无 scheme'}）",
                           "", "")

    # 端口：显式端口优先，缺省按 scheme 补齐（端口解析失败按缺省兜底）
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = 80 if scheme == "http" else 443

    # 闸 2：主机名黑名单
    host = (parts.hostname or "").strip("[]").lower().rstrip(".")
    if not host:
        return GuardResult(False, "URL 缺少主机名，已拒绝", "", "")

    # 宿主显式放行（高级开关）：跳过 IP 层判定（含回环白名单判定），
    # 协议白名单仍生效——语义是「宿主自担风险全放行」
    if _ALLOW_PRIVATE_NETWORK:
        return GuardResult(True, "", host, "")

    # 回环主机：默认拒绝路径上的精确放行——只有「宿主显式登记」的
    # host:port 才过（本地 AI）；未登记的回环一律拒
    norm_loopback: Optional[str] = _LOOPBACK_HOSTS.get(host)
    if norm_loopback is not None:
        if (norm_loopback, port) in _LOOPBACK_ALLOW:
            return GuardResult(True, "", host, norm_loopback)
        return GuardResult(False,
                           f"已拦截本地/内网地址（主机名 {host}，未在宿主登记白名单）",
                           host, "")

    if is_local_hostname(host):
        return GuardResult(False, f"已拦截本地/内网地址（主机名 {host}）",
                           host, "")

    # 闸 3：解析全部地址，任一命中即拒
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError) as exc:
        # 闸 4：解析失败 = 域名不存在/DNS 故障，fail-closed
        return GuardResult(False, f"域名解析失败，已拒绝：{host}（{exc}）",
                           host, "")
    if not infos:
        return GuardResult(False, f"域名解析无结果，已拒绝：{host}", host, "")
    for info in infos:
        ip = str(info[4][0])
        if is_private_ip(ip):
            return GuardResult(False, f"已拦截本地/内网地址：{host} -> {ip}",
                               host, ip)
    return GuardResult(True, "", host, str(infos[0][4][0]))
