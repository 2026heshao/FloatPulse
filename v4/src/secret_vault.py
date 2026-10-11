# -*- coding: utf-8 -*-
"""
====================================================================
凭证原文随行加密  -  secret_vault（纯逻辑，零 PyQt6，Windows 专属）
====================================================================
背景（2026-10-08 稳定性轮 P0）：剪贴板凭证哨兵把命中条目按「遮蔽占位」
落盘后，原文此前只留在内存待办队列——用户重启程序后，「••••••（已遮
蔽的凭证）」这条碎片永远无法回看原文。本模块用 Windows DPAPI
（CryptProtectData，CurrentUser 作用域）把原文加密后随碎片落盘：

  - 密文绑定当前 Windows 账户 + 本机（DPAPI 语义），拷到别的机器 /
    换账户后解密必然失败 → 返回 None，UI 提示「无法解密」；
  - 主程序**不得** import plugins/vault 的实现（发布口径：主程序包
    不含插件），故此处独立实现一份最小 ctypes 封装（手法与
    plugins/vault/vault_core.dpapi_* 同源，已验证的数据边界处理：
    空数据给 1 字节缓冲 cbData=0，避免 WinError 87）；
  - 熵（entropy）用应用专属常量：DPAPI 同账户即可解，加一层熵防止
    同账户下其它进程用同一作用域批量试解本应用的密文。

序列化格式：base64(blob)。落盘进 fragments.json 的 ``secret_enc``
字段（Fragment 可选字段，旧数据缺省空串 = 无随行原文，零迁移）。

安全边界：
  - 本模块不打印、不日志任何原文与密钥内容；
  - 解密失败一律返回 None，绝不抛出带原文的异常；
  - 加密失败抛 SecretVaultError，由调用方降级（落盘占位但不存原文）。
====================================================================
"""

import base64
import ctypes
import os

# 应用专属熵（第二因子；语义见模块头）
_ENTROPY = b"FloatPulse/secret_vault/v1"

# CRYPTPROTECT_UI_FORBIDDEN：禁止 DPAPI 弹任何 UI
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretVaultError(Exception):
    """DPAPI 加密失败（绝不静默降级为明文，由调用方决定处置）。"""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


_crypt32 = None
_kernel32 = None


def _dlls():
    """懒加载 DLL：保证本模块在任意平台都能安全 import（测试需要）。"""
    global _crypt32, _kernel32
    if _crypt32 is None:
        if os.name != "nt":
            raise SecretVaultError("DPAPI 仅在 Windows 上可用")
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _crypt32, _kernel32


def _make_blob(data: bytes):
    """构造 DATA_BLOB。空数据不能传 NULL 指针（实测 WinError 87）：
    给 1 字节缓冲、cbData=0，DPAPI 只按 cbData 读，安全。
    返回 (blob, 保活引用)。"""
    if data:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p)), buf
    buf = ctypes.create_string_buffer(b"\0", 1)
    return _DATA_BLOB(0, ctypes.cast(buf, ctypes.c_void_p)), buf


def _blob_to_bytes(blob: _DATA_BLOB, keepalive) -> bytes:
    try:
        if not blob.pbData or not blob.cbData:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _, k32 = _dlls()
        k32.LocalFree(ctypes.c_void_p(blob.pbData))
        del keepalive


def protect(text: str) -> str:
    """加密明文 → base64 密文串。

    失败抛 SecretVaultError（调用方降级为「只落占位、不存原文」）。
    """
    if not isinstance(text, str):
        raise SecretVaultError("待加密内容必须是 str")
    data = text.encode("utf-8")
    crypt32, _ = _dlls()
    blob_in, in_buf = _make_blob(data)
    blob_ent, ent_buf = _make_blob(_ENTROPY)
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None,          # 不写描述串（元数据卫生）
        ctypes.byref(blob_ent),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise SecretVaultError(
            f"DPAPI 加密失败（WinError {ctypes.get_last_error()}）")
    blob = _blob_to_bytes(blob_out, (in_buf, ent_buf))
    if not blob:
        raise SecretVaultError("DPAPI 加密返回空输出")
    return base64.b64encode(blob).decode("ascii")


def unprotect(token: str):
    """解密 base64 密文 → 原文。

    任何失败（换机器 / 换账户 / 数据坏 / 非法 base64）→ 返回 None，
    统一口径，绝不抛出带原文片段的异常。
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        blob = base64.b64decode(token, validate=True)
    except (ValueError, TypeError):
        return None
    if not blob:
        return None
    try:
        crypt32, _ = _dlls()
    except SecretVaultError:
        return None                            # 非 Windows：视为不可解
    blob_in, in_buf = _make_blob(blob)
    blob_ent, ent_buf = _make_blob(_ENTROPY)
    blob_out = _DATA_BLOB()
    descr = ctypes.POINTER(ctypes.c_wchar)()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), ctypes.byref(descr),
        ctypes.byref(blob_ent),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        return None
    try:
        plain = _blob_to_bytes(blob_out, (in_buf, ent_buf))
    finally:
        if descr:
            _dlls()[1].LocalFree(descr)
    if not plain:
        return None
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return None
