# -*- coding: utf-8 -*-
"""
====================================================================
Windows shell 大图标提取  -  win_icons
====================================================================
只用 stdlib（ctypes），不引第三方依赖。

**为什么不用 Qt 现成的？**
``QFileIconProvider`` + ``QIcon.pixmap()`` **从不放大** —— 源帧小于请求尺寸时
原样返回。对 ``.lnk`` 快捷方式更极端：无论请求 40 / 76 / 108 / 120，实测
（2026-10-02，真实 Windows 平台 125% DPI）**一律只回 40×40**；而
``QIcon.availableSizes()`` 谎报有 320 帧、``actualSize(108)`` 也谎称能给
108 —— 上游因此无从察觉。用户看到的现象就是「软件卡片跟着设置放大了，
里面的图标纹丝不动」。

**本模块做什么**
shell 的 **jumbo 图像列表**（``SHGetImageList(SHIL_JUMBO)``）能给 256×256
真高分图，``.lnk`` 同样适用（index 由 ``SHGFI_SYSICONINDEX`` 取得，系统
图像列表共用同一套索引，这是 MSDN 明写的契约）。取回 HICON 后经
``GetIconInfo`` + ``GetDIBits`` 转成 QImage（ARGB32）。

**硬约束**
  - 非 Windows / API 缺失 / 取图失败 → 一律返回 ``None``，由调用方回落 Qt
    路径；本模块**永不抛异常**（拿不到大图标不该影响界面）。
  - 结果按路径缓存：QImage 与目标尺寸无关，缩放交给调用方（拖尺寸步进器
    时不会每档都重跑一次 shell 调用）。
"""

import ctypes
import os
import sys

from PyQt6.QtGui import QImage

IS_WINDOWS = sys.platform == "win32"

# SHGetFileInfo 的标志：只要系统图标索引，不要 HICON（避免句柄泄漏）
SHGFI_SYSICONINDEX = 0x000004000
# 系统图像列表尺寸档：jumbo = 256×256
SHIL_JUMBO = 0x4
# GetIcon 参数：保留 alpha
ILD_TRANSPARENT = 0x1

# IImageList 的 IID {46EB5926-582E-4017-9FDF-E8998DAA0950}
_GUID_IIMAGELIST = (0x46EB5926, 0x582E, 0x4017,
                    (0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50))
# IImageList::GetIcon 在 vtable 中的下标：IUnknown 3 个方法 + 前 7 个 IImageList
# 方法（Add / ReplaceIcon / SetOverlayImage / Replace / AddMasked / Draw /
# Remove）之后即 GetIcon。实测校验过。
_VTBL_GET_ICON = 10
# QImage 格式：BI_RGB 32bpp 的字节序与 ARGB32 一致（小端下 BGRA）
_BYTES_PER_PIXEL = 4

_CACHE_MAX = 64

_ready = None          # None=未探测 / False=不可用 / True=可用
_apis = None           # 已绑定原型的 ctypes 句柄
_cache = {}            # 规范化路径 -> QImage | None


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", ctypes.c_void_p),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", ctypes.c_ulong),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_int), ("xHotspot", ctypes.c_ulong),
        ("yHotspot", ctypes.c_ulong), ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
        ("bmBits", ctypes.c_void_p),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]


def _bind():
    """一次性绑定 Win32 原型；任一步失败即判定不可用（返回 None）。"""
    global _apis
    if _apis is not None:
        return _apis
    if not IS_WINDOWS:
        return None
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        shell32.SHGetFileInfoW.restype = ctypes.c_void_p
        shell32.SHGetFileInfoW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_ulong,
            ctypes.POINTER(_SHFILEINFOW), ctypes.c_uint, ctypes.c_uint]
        shell32.SHGetImageList.restype = ctypes.c_long
        shell32.SHGetImageList.argtypes = [
            ctypes.c_int, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]

        user32.GetIconInfo.restype = ctypes.c_int
        user32.GetIconInfo.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(_ICONINFO)]
        user32.DestroyIcon.restype = ctypes.c_int
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]

        gdi32.GetObjectW.restype = ctypes.c_int
        gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_void_p]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_void_p, ctypes.POINTER(_BITMAPINFOHEADER), ctypes.c_uint]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.restype = ctypes.c_int
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteObject.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

        _apis = {"shell32": shell32, "user32": user32, "gdi32": gdi32}
    except Exception:
        _apis = None
    return _apis


def is_available() -> bool:
    """本机是否可用（非 Windows 或 API 绑定失败 → False）。"""
    global _ready
    if _ready is None:
        _ready = _bind() is not None
    return _ready


def _shell_icon_index(api, path):
    info = _SHFILEINFOW()
    rc = api["shell32"].SHGetFileInfoW(
        path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_SYSICONINDEX)
    if not rc:
        return None
    return info.iIcon


def _hicon_to_image(api, hicon):
    """HICON → QImage（ARGB32）。任一环节失败返 None。"""
    gdi32, user32 = api["gdi32"], api["user32"]
    ii = _ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return None
    try:
        hbm = ii.hbmColor or ii.hbmMask
        if not hbm:
            return None
        bm = _BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(_BITMAP), ctypes.byref(bm)):
            return None
        w, h = int(bm.bmWidth), int(bm.bmHeight)
        if w <= 0 or h <= 0:
            return None

        hdr = _BITMAPINFOHEADER()
        hdr.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        hdr.biWidth = w
        hdr.biHeight = -h            # 负数 = top-down，省一次翻转
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = 0        # BI_RGB
        buf = ctypes.create_string_buffer(w * h * _BYTES_PER_PIXEL)

        # ★ GetDIBits 必须给 DC —— 传 NULL 会直接失败（实测踩过）
        memdc = gdi32.CreateCompatibleDC(None)
        if not memdc:
            return None
        try:
            got = gdi32.GetDIBits(memdc, hbm, 0, h, buf,
                                  ctypes.byref(hdr), 0)
        finally:
            gdi32.DeleteDC(memdc)
        if not got:
            return None

        # copy() 是必需的：QImage 不接管 buffer 所有权，buf 出栈即悬空
        return QImage(buf, w, h, w * _BYTES_PER_PIXEL,
                      QImage.Format.Format_ARGB32).copy()
    finally:
        # GetIconInfo 会新建两份位图，调用方负责回收
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)


def _guid_iimagelist():
    """构造 IImageList 的 IID 结构体。"""
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = (_GUID_IIMAGELIST[0], _GUID_IIMAGELIST[1],
                                 _GUID_IIMAGELIST[2])
    for i, b in enumerate(_GUID_IIMAGELIST[3]):
        g.Data4[i] = b
    return g


def _fetch(api, path):
    index = _shell_icon_index(api, path)
    if index is None:
        return None
    piml = ctypes.c_void_p()
    guid = _guid_iimagelist()
    hr = api["shell32"].SHGetImageList(SHIL_JUMBO, ctypes.byref(guid),
                                       ctypes.byref(piml))
    if hr or not piml:
        return None
    # 调 COM 接口必须走 vtable；GetIcon 的返回码非 0 表示该索引在
    # jumbo 列表里没有对应项（常见于本身没有 256px 帧的程序）
    vtbl = ctypes.cast(
        piml, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_icon = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p))(vtbl[_VTBL_GET_ICON])
    hicon = ctypes.c_void_p()
    hr2 = get_icon(piml, index, ILD_TRANSPARENT, ctypes.byref(hicon))
    if hr2 or not hicon:
        return None
    try:
        img = _hicon_to_image(api, hicon)
    finally:
        api["user32"].DestroyIcon(hicon)
    if img is None or img.isNull():
        return None
    return img


def shell_icon_image(path):
    """取 ``path`` 的 shell 大图标（QImage，通常 256×256）；失败返回 None。

    结果按规范化路径缓存（含失败结果 —— 拿不到就不要再问第二次）。
    本函数永不抛异常。
    """
    if not path or not is_available():
        return None
    try:
        key = os.path.normpath(str(path))
    except Exception:
        return None
    if key in _cache:
        return _cache[key]
    img = None
    try:
        if os.path.exists(key):
            img = _fetch(_bind(), key)
    except Exception:
        img = None
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = img
    return img


def clear_cache():
    """清空缓存（测试用；也留给「图标变了要重取」的场景）。"""
    _cache.clear()