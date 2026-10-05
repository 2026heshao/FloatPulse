# -*- coding: utf-8 -*-
"""悬浮球拖放落盘 / URL 下载纯函数（2026-10-05 D2/T04 外迁）。

从 ``knowledge_ball.FloatingBall`` 整体平移的六个无状态函数：

  - parse_file_group_descriptor : FileGroupDescriptorW → 文件名（struct 解析）
  - save_file_contents          : FileContents 二进制 → temp_assets/
  - save_mime_image             : MIME 图片 → temp_assets/*.png
  - save_mime_image_by_format   : 指定 MIME 格式 → temp_assets/*
  - is_private_url_host         : SSRF 防护（本地/内网地址判定）
  - detect_image_ext            : magic bytes → 真实图片扩展名
  - download_url                : 四道安全闸的图片下载

铁律（见 docs/拆分计划-wiring-2026-10-05.md §0.3）：

  1. **显式参数**：不读宿主状态——临时目录、大小上限由调用方传入；
  2. **toast 回调**：用户反馈经 ``toast`` 回调上抛，本模块不依赖 UI 对象；
  3. **无 Qt 部件创建**：不建窗口/部件；仅按需创建下载所需的网络对象
     （QNetworkAccessManager/QEventLoop，行为与原实现逐字一致）；
  4. **禁止 import knowledge_ball / main_window / card_window**（防 import 环）。

宿主侧（FloatingBall）保留同签名薄委托，调用点零改动。
"""

import os
import struct
from datetime import datetime
from urllib.parse import urlparse


def parse_file_group_descriptor(mime_data) -> str:
    """从 FileGroupDescriptorW 解析文件名。

    返回第一个文件的文件名，失败返回空字符串。
    """
    fmt = "application/x-qt-windows-mime;value=\"FileGroupDescriptorW\""
    if not mime_data.hasFormat(fmt):
        return ""
    try:
        raw = bytes(mime_data.data(fmt))
        if len(raw) < 4:
            return ""
        # DWORD cItems（文件数量）
        c_items = struct.unpack_from("<I", raw, 0)[0]
        if c_items < 1:
            return ""
        # 每个 FILEDESCRIPTORW 结构从偏移 4 开始
        # cFileName 在结构内偏移 112 处，长度 520 字节（260 WCHAR）
        offset = 4 + 112
        name_bytes = raw[offset:offset + 520]
        # 找第一个 null 终止符
        end = name_bytes.find(b"\x00\x00")
        if end > 0:
            name_bytes = name_bytes[:end]
        return name_bytes.decode("utf-16-le", errors="replace").strip()
    except Exception:
        return ""


def save_file_contents(file_data, file_name: str, tmp_dir: str) -> str:
    """将 FileContents 的二进制数据保存到 temp_assets/。

    file_name 来自 FileGroupDescriptorW，可能为空。
    tmp_dir 由调用方供给（宿主 _get_temp_assets_dir()），函数内只落盘。
    """
    if file_data.isEmpty():
        return ""
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        # 确定文件名和扩展名
        if file_name:
            # 清理文件名中的非法字符（集中规则，见 constants.sanitize_filename）
            from src.constants import sanitize_filename
            file_name = sanitize_filename(file_name)
            ext = os.path.splitext(file_name)[1].lower()
        else:
            ext = ".bin"
            file_name = "browser_file"

        # 如果扩展名不在已知图片格式中，尝试从数据头判断
        if ext not in [".png", ".jpg", ".jpeg", ".gif", ".bmp",
                       ".webp", ".svg", ".tiff", ".ico", ".pdf",
                       ".doc", ".docx", ".txt", ".bin"]:
            ext = ".bin"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tmp_dir, f"{file_name}_{ts}{ext}")

        with open(path, "wb") as f:
            f.write(file_data.data())
        print(f"[DEBUG] FileContents saved: {len(file_data)} bytes -> {path}")
        return path
    except Exception as e:
        print(f"[DEBUG] FileContents save failed: {e}")
        return ""


def save_mime_image(mime_data, tmp_dir: str) -> str:
    """从 MIME 图片数据保存为临时文件，返回路径"""
    from PyQt6.QtGui import QImage
    img = QImage(mime_data.imageData())
    if img.isNull():
        return ""
    os.makedirs(tmp_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(tmp_dir, f"browser_img_{ts}.png")
    img.save(path, "PNG")
    return path


def save_mime_image_by_format(mime_data, fmt: str, ext: str,
                              tmp_dir: str) -> str:
    """从指定 MIME 格式保存为临时文件，返回路径"""
    data = mime_data.data(fmt)
    if data.isEmpty():
        return ""
    os.makedirs(tmp_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(tmp_dir, f"browser_img_{ts}{ext}")
    with open(path, "wb") as f:
        f.write(data.data())
    return path


def is_private_url_host(host: str) -> bool:
    """拦截本地/内网地址（SSRF 防护）：解析失败一律拒绝"""
    import ipaddress
    import socket
    if not host:
        return True
    h = host.strip("[]").lower()
    if h == "localhost" or h.endswith(".local") or h.endswith(".internal"):
        return True
    try:
        infos = socket.getaddrinfo(h, None)
    except (socket.gaierror, OSError):
        return True
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return True
    return False


def detect_image_ext(raw: bytes) -> str:
    """按文件头（magic bytes）识别真实图片类型；非图片返回空串"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if raw.startswith(b"BM"):
        return ".bmp"
    if raw.startswith(b"\x00\x00\x01\x00"):
        return ".ico"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


def download_url(url: str, tmp_dir: str, max_bytes: int,
                 toast=None) -> str:
    """下载 HTTP(S) 图片 URL 到临时文件，返回路径；被拦截或失败返回空串

    安全闸门：① 仅 http/https ② 拒绝本地/内网地址 ③ 大小上限 ④ magic bytes 校验

    toast : 可选回调（宿主 FloatingBall._show_toast），用于拦截原因的用户
    反馈；None 时静默拒绝（与原实现仅在宿主侧永不为 None 的口径一致）。
    """
    from PyQt6.QtNetwork import (QNetworkAccessManager, QNetworkRequest,
                                 QNetworkReply)
    from PyQt6.QtCore import QEventLoop, QTimer, QUrl

    try:
        # 闸门 1：协议白名单
        u = urlparse(url)
        if u.scheme.lower() not in ("http", "https"):
            if toast is not None:
                toast("⚠️ 仅支持 http/https 图片链接")
            return ""
        # 闸门 2：拒绝本地/内网地址
        if is_private_url_host(u.hostname or ""):
            if toast is not None:
                toast("⚠️ 已拦截本地/内网地址")
            return ""

        os.makedirs(tmp_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        manager = QNetworkAccessManager()
        request = QNetworkRequest()
        request.setUrl(QUrl(url))
        request.setRawHeader(b"User-Agent",
            b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        request.setRawHeader(b"Accept",
            b"image/webp,image/apng,image/*,*/*;q=0.8")
        request.setRawHeader(b"Referer", url.encode())

        reply = manager.get(request)
        loop = QEventLoop()
        reply.finished.connect(loop.quit)

        # 闸门 3：下载进度超限即中止
        def _on_progress(received, total):
            if (received > max_bytes
                    or (total > 0 and total > max_bytes)):
                reply.abort()
        reply.downloadProgress.connect(_on_progress)

        # 15 秒超时（超时中止 → finished 触发 → loop 退出）
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(15000)

        loop.exec()
        timer.stop()

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                print(f"[DEBUG] Download failed/aborted: {reply.errorString()}")
                return ""
            raw = bytes(reply.readAll())
        finally:
            reply.deleteLater()

        # 闸门 3 复核：落盘前再查一次总大小
        if len(raw) > max_bytes:
            print("[DEBUG] Download exceeded size limit")
            if toast is not None:
                toast("⚠️ 图片超过 20MB，已取消")
            return ""

        # 闸门 4：按真实文件头识别类型；非图片一律拒绝（并按真实类型定扩展名）
        ext = detect_image_ext(raw)
        if not ext:
            print("[DEBUG] Downloaded content is not an image")
            if toast is not None:
                toast("⚠️ 下载内容不是图片，已取消")
            return ""

        path = os.path.join(tmp_dir, f"url_img_{ts}{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        print(f"[DEBUG] Downloaded: {len(raw)} bytes -> {path}")
        return path
    except Exception as e:
        print(f"[DEBUG] Download exception: {e}")
        return ""
