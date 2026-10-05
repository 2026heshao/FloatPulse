# -*- coding: utf-8 -*-
"""
====================================================================
密码保险箱核心  -  vault 插件的纯逻辑层
====================================================================
只依赖 Python 标准库，**不 import PyQt6**（与 kb-search / md_export 同一
约定，pytest 无 GUI 直接跑）。所有路径由调用方注入：Qt 壳传
``ctx.data_dir``，测试传临时目录。

安全模型（设计文档 docs/密码保险箱插件设计-2026-10-01.md）：

  1. **DPAPI（crypt32.dll）+ PBKDF2 双因子**：stdlib 没有对称加密，
     自己写 AES 是行业禁忌 → 用 Windows DPAPI 绑定「本机 + 本账户」；
     主密码经 PBKDF2-HMAC-SHA256（200k 迭代）派生成 32 字节 entropy
     作为 CryptProtectData 的第二因子。换机器 / 换账户 / 不知道主密码
     都解不开。
  2. **meta.json 明文只放解密前必须知道的字段**（schema / kdf / iter /
     salt / verify）。条目数、更新时间属于元数据泄露，一并进密文。
  3. **整库一次加密**：DPAPI 每次调用固定 ~238B 开销，条目级不划算；
     且整库只产生「全成 / 全败」两种状态，没有半成品。
  4. **原子写入**：临时文件 + fsync + ``os.replace``，先写新的再替换，
     断电不会把旧库弄丢。
  5. **DPAPI 失败不静默降级**：加密失败抛 ``VaultError`` 拒绝保存，
     绝不明文落盘；内存态同步回滚，保证「内存 == 磁盘」。
  6. **解密失败统一视为主密码错误**（不区分「密码错」和「换机器」，
     免得给攻击者提供信息）。

明文只在内存里活到 ``lock()`` 为止——Python 字符串无法真正擦除，这一
层防的是「文件被拷走」和「别人打开软件」，不防内存 dump（写进说明，
不夸大）。
====================================================================
"""

import base64
import copy
import ctypes
import hashlib
import hmac
import json
import os
import time

# ====================================================================
# 常量
# ====================================================================
SCHEMA_VERSION = 1
KDF_NAME = "pbkdf2-sha256"
KDF_ITERATIONS = 200_000
KDF_MIN_ITERATIONS = 1_000        # meta.iter 低于此值视为库损坏（防篡改出离谱参数）
SALT_BYTES = 16
ENTROPY_BYTES = 32
META_FILE = "meta.json"
VAULT_FILE = "vault.bin"
SESSION_FILE = "session.bin"     # 「永不锁定」档记住的派生密钥（DPAPI 加密）
TIME_FMT = "%Y-%m-%d %H:%M"

# 记住解锁凭据的 DPAPI 第二因子（固定常量：凭据文件只在本机本账户可解，
# 真正的访问控制来自 DPAPI 的用户绑定，不需要在这里引入第二秘密）
_SESSION_ENTROPY = b"floatpulse-vault-session-v1"

# 掩码固定 8 个点：不随真实长度变化，避免「看掩码猜长度」
MASK = "••••••••"

# 空闲自动锁定的合法档位（分钟），0 = 永不
IDLE_CHOICES = (0, 1, 5, 15)
IDLE_DEFAULT = 5

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class VaultError(Exception):
    """保险箱操作失败（DPAPI 加密失败 / 状态非法等）——必须让用户看到"""


class VaultLockedError(VaultError):
    """未解锁时执行了需要明文的操作"""


# ====================================================================
# 时间与 ID
# ====================================================================
def now_str() -> str:
    """条目时间戳，形如 2026-10-01 17:30（本地时间，分钟精度）"""
    return time.strftime(TIME_FMT)


def next_entry_id(entries) -> str:
    """顺序生成 e0001 风格的 id；跳过已占用的号"""
    used = set()
    top = 0
    for e in entries:
        eid = str((e or {}).get("id", ""))
        used.add(eid)
        if eid.startswith("e") and eid[1:].isdigit():
            top = max(top, int(eid[1:]))
    nid = f"e{top + 1:04d}"
    while nid in used:                       # 理论到不了，防御手改过的库
        top += 1
        nid = f"e{top + 1:04d}"
    return nid


# ====================================================================
# 校验与规整（坏数据一律 ValueError，绝不静默修）
# ====================================================================
def normalize_title(title) -> str:
    if not isinstance(title, str):
        raise ValueError("标题必须是字符串")
    title = title.strip()
    if not title:
        raise ValueError("标题不能为空")
    if len(title) > 200:
        raise ValueError("标题过长（上限 200 字符）")
    return title


def normalize_field(raw) -> dict:
    """单字段规整：{"key": 非空str, "value": str(可空), "secret": bool}"""
    if not isinstance(raw, dict):
        raise ValueError("字段必须是 dict")
    key = raw.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("字段名不能为空")
    key = key.strip()
    if len(key) > 100:
        raise ValueError("字段名过长（上限 100 字符）")
    value = raw.get("value", "")
    if not isinstance(value, str):
        raise ValueError("字段值必须是字符串")
    if len(value) > 20_000:
        raise ValueError("字段值过长（上限 20000 字符）")
    return {"key": key, "value": value, "secret": bool(raw.get("secret", False))}


def normalize_fields(raws) -> list:
    if raws is None:
        return []
    if not isinstance(raws, (list, tuple)):
        raise ValueError("fields 必须是列表")
    return [normalize_field(r) for r in raws]


def normalize_tags(tags) -> list:
    if tags is None:
        return []
    if not isinstance(tags, (list, tuple)):
        raise ValueError("tags 必须是列表")
    out, seen = [], set()
    for t in tags:
        if not isinstance(t, str):
            raise ValueError("标签必须是字符串")
        t = t.strip()[:40]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def normalize_entry(raw: dict) -> dict:
    """整条规整（读库 / restore 时用）；缺 created_at/updated_at 补 now"""
    if not isinstance(raw, dict):
        raise ValueError("条目必须是 dict")
    entry = {
        "id": str(raw.get("id") or next_entry_id([])),
        "title": normalize_title(raw.get("title")),
        "fields": normalize_fields(raw.get("fields")),
        "tags": normalize_tags(raw.get("tags")),
        "created_at": str(raw.get("created_at") or now_str()),
        "updated_at": str(raw.get("updated_at") or raw.get("created_at") or now_str()),
    }
    return entry


# ====================================================================
# KDF：主密码 → 32 字节 entropy（DPAPI 第二因子）
# ====================================================================
def derive_entropy(password: str, salt: bytes,
                   iterations: int = KDF_ITERATIONS) -> bytes:
    if not isinstance(password, str):
        raise ValueError("主密码必须是字符串")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 8:
        raise ValueError("salt 必须是 >=8 字节的 bytes")
    if not isinstance(iterations, int) or iterations < KDF_MIN_ITERATIONS:
        raise ValueError("迭代数非法")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes(salt), iterations, dklen=ENTROPY_BYTES)


def verification_hex(entropy: bytes) -> str:
    """校验值：只存 sha256(entropy)，entropy 本身绝不落盘"""
    if not isinstance(entropy, (bytes, bytearray)):
        raise ValueError("entropy 必须是 bytes")
    return hashlib.sha256(bytes(entropy)).hexdigest()


def verify_password(password: str, salt: bytes, iterations: int,
                    expected_hex: str) -> bool:
    """常数时间比对派生结果与校验值"""
    try:
        entropy = derive_entropy(password, salt, iterations)
    except ValueError:
        return False
    return hmac.compare_digest(verification_hex(entropy), str(expected_hex or ""))


# ====================================================================
# DPAPI 封装（crypt32.dll，ctypes 直调）
# ====================================================================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


_crypt32 = None
_kernel32 = None


def _dlls():
    """懒加载 DLL：保证本模块在任意平台都能安全 import（测试需要）"""
    global _crypt32, _kernel32
    if _crypt32 is None:
        if os.name != "nt":
            raise VaultError("DPAPI 仅在 Windows 上可用")
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _crypt32, _kernel32


def _make_blob(data: bytes):
    """构造 DATA_BLOB。空数据不能传 NULL 指针（实测 WinError 87）：
    给 1 字节缓冲、cbData=0，DPAPI 只按 cbData 读，安全。返回 (blob, 保活引用)"""
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


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    """CryptProtectData：加密失败抛 VaultError（绝不静默降级）"""
    if not isinstance(data, (bytes, bytearray)):
        raise VaultError("待加密数据必须是 bytes")
    data, ent = bytes(data), bytes(entropy or b"")
    crypt32, _ = _dlls()
    blob_in, in_buf = _make_blob(data)
    blob_ent, ent_buf = _make_blob(ent)
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None,                      # 不写描述串（元数据卫生）
        ctypes.byref(blob_ent) if ent else None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise VaultError(f"DPAPI 加密失败（WinError {ctypes.get_last_error()}），已拒绝保存")
    return _blob_to_bytes(blob_out, (in_buf, ent_buf))


def dpapi_unprotect(blob: bytes, entropy: bytes):
    """CryptUnprotectData：失败返回 None（主密码错 / 换机器 / 数据坏，统一口径）"""
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        return None
    ent = bytes(entropy or b"")
    crypt32, _ = _dlls()
    blob_in, in_buf = _make_blob(bytes(blob))
    blob_ent, ent_buf = _make_blob(ent)
    blob_out = _DATA_BLOB()
    descr = ctypes.POINTER(ctypes.c_wchar)()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), ctypes.byref(descr),
        ctypes.byref(blob_ent) if ent else None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        return None
    try:
        return _blob_to_bytes(blob_out, (in_buf, ent_buf))
    finally:
        if descr:                                          # 系统分配的描述串，必须释放
            _dlls()[1].LocalFree(descr)


# ====================================================================
# 小工具（quick 取用 / 排序 / 搜索 / 设置）
# ====================================================================
def mask_value(value: str) -> str:
    """固定长度掩码——不随真实长度变化，防「看掩码猜长度」"""
    return MASK


def first_secret_value(entry: dict):
    """快速取用：返回条目里第一个 secret 字段的值；没有则 None"""
    for f in (entry or {}).get("fields", []):
        if f.get("secret") and f.get("value"):
            return f["value"]
    return None


def matches_query(entry: dict, query: str) -> bool:
    """搜索命中：标题 + **非 secret** 字段值（不搜密文，防肩窥）。
    ASCII 大小写不敏感；空白查询恒命中（= 列出全部）"""
    q = str(query or "").strip().casefold()
    if not q:
        return True
    e = entry or {}
    if q in str(e.get("title", "")).casefold():
        return True
    for f in e.get("fields", []):
        if not f.get("secret") and q in str(f.get("value", "")).casefold():
            return True
    return False


def search_entries(entries, query: str) -> list:
    return [copy.deepcopy(e) for e in (entries or []) if matches_query(e, query)]


def sort_entries(entries) -> list:
    """更新时间降序（同分钟内保持原相对顺序）"""
    return sorted(copy.deepcopy(entries or []),
                  key=lambda e: str(e.get("updated_at", "")), reverse=True)


def sanitize_idle_minutes(value, default: int = IDLE_DEFAULT) -> int:
    """空闲锁定档位消毒：只接受 (0,1,5,15)，其余回默认。0 = 永不"""
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return v if v in IDLE_CHOICES else default


def load_settings(data_dir: str) -> dict:
    """读插件私有设置（settings.json）；缺失/损坏回默认值"""
    path = os.path.join(data_dir or "", "settings.json")
    out = {"idle_minutes": IDLE_DEFAULT}
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                out["idle_minutes"] = sanitize_idle_minutes(
                    raw.get("idle_minutes"), IDLE_DEFAULT)
        except (OSError, ValueError):
            pass
    return out


def save_settings(data_dir: str, settings: dict) -> bool:
    """写插件私有设置（原子替换）；失败只返回 False"""
    path = os.path.join(data_dir or "", "settings.json")
    if not path or path == "settings.json":
        return False
    payload = {"idle_minutes": sanitize_idle_minutes(
        (settings or {}).get("idle_minutes"), IDLE_DEFAULT)}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ====================================================================
# 记住解锁（「永不锁定」档）：派生密钥经 DPAPI 落盘，重启自动开箱
# --------------------------------------------------------------------
# 安全语义：session.bin 由 DPAPI（绑定本机 + 本 Windows 账户）保护，
# 效果 = 「能登录这个 Windows 账户即可开箱」。主密码本身不落盘；
# 换机器 / 换账户 / 手动上锁 / 切走「永不」档都会使其失效。
# ====================================================================
def load_session(data_dir: str):
    """读记住的派生密钥（DPAPI 解密）；缺失 / 损坏 / 环境变化 → None"""
    path = os.path.join(data_dir or "", SESSION_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    plain = dpapi_unprotect(blob, _SESSION_ENTROPY)
    if not plain or len(plain) != ENTROPY_BYTES:
        return None
    return plain


def save_session(data_dir: str, entropy: bytes) -> bool:
    """记住派生密钥（DPAPI 加密后原子落盘）；失败返回 False（不阻塞解锁）"""
    entropy = bytes(entropy)
    if len(entropy) != ENTROPY_BYTES:
        return False
    path = os.path.join(data_dir or "", SESSION_FILE)
    if not path or path == SESSION_FILE:
        return False
    try:
        blob = dpapi_protect(entropy, _SESSION_ENTROPY)
    except VaultError:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def clear_session(data_dir: str) -> bool:
    """清除记住的解锁凭据（手动上锁 / 切走「永不」档时调用）"""
    path = os.path.join(data_dir or "", SESSION_FILE)
    if not os.path.isfile(path):
        return True
    try:
        os.remove(path)
        return True
    except OSError:
        return False


# ====================================================================
# Vault：状态机（missing → ready → unlocked ⇄ locked）+ 存储
# ====================================================================
class Vault:
    """保险箱实例。一个 data_dir 一个实例；所有路径注入，零 Qt 依赖。

    生命周期：``status()=="missing"`` → ``create(pw)`` → ``unlock(pw)``
    → CRUD（自动落盘）→ ``lock()``。``unlock`` 失败一律返回 False。
    """

    def __init__(self, data_dir: str):
        self._dir = str(data_dir or "")
        self._meta = None            # dict：schema/kdf/iter/salt/verify
        self._entropy = None         # bytes：解锁后驻留内存，lock 时丢弃
        self._entries = []           # list[dict]：明文条目
        self._updated_at = None
        self.degraded = False        # 解密成功但内容损坏 → 已降级为空库

    # ---------- 路径 ----------
    @property
    def data_dir(self) -> str:
        return self._dir

    @property
    def meta_path(self) -> str:
        return os.path.join(self._dir, META_FILE)

    @property
    def vault_path(self) -> str:
        return os.path.join(self._dir, VAULT_FILE)

    # ---------- 状态 ----------
    @property
    def locked(self) -> bool:
        return self._entropy is None

    @property
    def entry_count(self) -> int:
        return len(self._entries) if not self.locked else 0

    @property
    def entries(self) -> list:
        """明文条目快照（deepcopy，外部改不动内存态）；未解锁抛错"""
        self._ensure_unlocked()
        return copy.deepcopy(self._entries)

    @property
    def updated_at(self):
        return self._updated_at

    def status(self) -> str:
        """missing=首次使用 / ready=可解锁 / corrupt=文件不齐或损坏"""
        has_meta, has_vault = os.path.isfile(self.meta_path), os.path.isfile(self.vault_path)
        if has_meta and has_vault:
            try:
                self._load_meta()
                return "ready"
            except (VaultError, ValueError):
                return "corrupt"
        if not has_meta and not has_vault:
            return "missing"
        return "corrupt"                       # 只剩一半 → 无法解密

    # ---------- 首次创建 ----------
    def create(self, password: str) -> None:
        if not isinstance(password, str) or not password:
            raise ValueError("主密码不能为空")
        if self.status() == "ready":
            raise VaultError("保险箱已存在")
        self._archive_corrupt()                # corrupt 状态下重建前先留档
        salt = os.urandom(SALT_BYTES)
        entropy = derive_entropy(password, salt, KDF_ITERATIONS)
        meta = {
            "schema": SCHEMA_VERSION,
            "kdf": KDF_NAME,
            "iter": KDF_ITERATIONS,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "verify_sha256": verification_hex(entropy),
        }
        self._ensure_dir()
        _atomic_write_bytes(self.meta_path,
                            json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
        self._meta = meta
        self._entropy = entropy
        self._entries = []
        self._updated_at = now_str()
        self.degraded = False
        self.save()                            # 空库也立刻落盘（vault.bin 就位）

    # ---------- 解锁 / 锁定 ----------
    def unlock(self, password: str) -> bool:
        if self.status() != "ready":
            return False
        meta = self._load_meta()
        salt = base64.b64decode(str(meta.get("salt_b64", "")))
        iterations = int(meta.get("iter") or 0)
        if not verify_password(password, salt, iterations,
                               meta.get("verify_sha256", "")):
            return False                        # 主密码错（统一口径，不细究原因）
        entropy = derive_entropy(password, salt, iterations)
        return self._open_with(meta, entropy)

    def unlock_entropy(self, entropy: bytes) -> bool:
        """用派生密钥直接解锁（记住解锁路径：跳过主密码验证与 KDF）。

        仅供「永不锁定」档的自动开箱使用：密钥来自 DPAPI 保护的
        session.bin，解不开 vault.bin（改密后 / 换环境）一律返回 False。
        """
        if self.status() != "ready" or not entropy:
            return False
        try:
            meta = self._load_meta()
        except VaultError:
            return False
        return self._open_with(meta, bytes(entropy))

    @property
    def session_key(self):
        """当前派生密钥快照（记住解锁用）；锁定态返回 None"""
        return bytes(self._entropy) if self._entropy is not None else None

    def _open_with(self, meta: dict, entropy: bytes) -> bool:
        """用已确定的派生密钥解开 vault.bin（unlock / unlock_entropy 共用）"""
        try:
            with open(self.vault_path, "rb") as f:
                blob = f.read()
        except OSError:
            return False
        plain = dpapi_unprotect(blob, entropy)
        if plain is None:
            return False                        # 同上：不区分「密码错」与「换机器」
        try:
            payload = json.loads(plain.decode("utf-8"))
            entries = payload["entries"]
            if not isinstance(entries, list):
                raise ValueError("entries 必须是列表")
            entries = [normalize_entry(e) for e in entries]
        except (ValueError, KeyError, TypeError):
            # DPAPI 解开了但内容坏 → 留档后降级为空库（不崩、不静默抹掉原文件）
            self._archive_file(self.vault_path, suffix="corrupt")
            self._entropy, self._entries = entropy, []
            self._updated_at = now_str()
            self._meta = meta
            self.degraded = True
            return True
        self._meta, self._entropy = meta, entropy
        self._entries = entries
        self._updated_at = str(payload.get("updated_at") or now_str())
        self.degraded = False
        return True

    def lock(self) -> None:
        """丢弃内存明文与派生密钥（Python 字符串无法真擦除，做到能做的）"""
        self._entropy = None
        self._entries = []
        self._updated_at = None

    # ---------- CRUD（每个变更自动落盘；落盘失败回滚内存） ----------
    def get(self, entry_id: str):
        self._ensure_unlocked()
        for e in self._entries:
            if e.get("id") == entry_id:
                return copy.deepcopy(e)
        return None

    def search(self, query: str) -> list:
        """标题 + 非 secret 字段值检索（命中快照，未解锁抛错）"""
        self._ensure_unlocked()
        return search_entries(self._entries, query)

    def add(self, title, fields=None, tags=None) -> dict:
        entry = normalize_entry({"id": "", "title": title, "fields": fields, "tags": tags})
        return self._mutate(lambda: self._do_add(entry))

    def update(self, entry_id: str, title=None, fields=None, tags=None):
        self._ensure_unlocked()
        if title is None and fields is None and tags is None:
            return self.get(entry_id)
        patch = {}
        if title is not None:
            patch["title"] = normalize_title(title)
        if fields is not None:
            patch["fields"] = normalize_fields(fields)
        if tags is not None:
            patch["tags"] = normalize_tags(tags)
        return self._mutate(lambda: self._do_update(entry_id, patch))

    def delete(self, entry_id: str):
        self._ensure_unlocked()
        return self._mutate(lambda: self._do_delete(entry_id))

    def restore(self, entry: dict, index=None) -> dict:
        """撤销删除：放回原位置；id 撞车时换新 id"""
        self._ensure_unlocked()
        data = copy.deepcopy(dict(entry or {}))
        return self._mutate(lambda: self._do_restore(data, index))

    # ---------- 保存 / 导出 / 改密 ----------
    def save(self) -> None:
        """整库加密落盘（原子）。未解锁 / DPAPI 失败都抛 VaultError"""
        self._ensure_unlocked()
        payload = {"schema": SCHEMA_VERSION, "entries": self._entries,
                   "updated_at": now_str()}
        plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        blob = dpapi_protect(plain, self._entropy)
        self._ensure_dir()
        _atomic_write_bytes(self.vault_path, blob)
        self._updated_at = payload["updated_at"]

    def export_json(self, path: str) -> None:
        """导出**明文** JSON（换机迁移用）。二次确认由 UI 负责，这里只管写"""
        self._ensure_unlocked()
        payload = {"schema": SCHEMA_VERSION, "exported_at": now_str(),
                   "entries": self._entries}
        _atomic_write_bytes(path, json.dumps(
            payload, ensure_ascii=False, indent=1).encode("utf-8"))

    def change_password(self, old: str, new: str) -> bool:
        """改主密码：重派生 + 换 salt + 整库重加密。锁定时抛 VaultLockedError"""
        self._ensure_unlocked()
        meta = self._load_meta()
        salt = base64.b64decode(str(meta.get("salt_b64", "")))
        if not verify_password(old, salt, int(meta.get("iter") or 0),
                               meta.get("verify_sha256", "")):
            return False
        if not isinstance(new, str) or not new:
            raise ValueError("新主密码不能为空")
        new_salt = os.urandom(SALT_BYTES)
        new_entropy = derive_entropy(new, new_salt, KDF_ITERATIONS)
        payload = {"schema": SCHEMA_VERSION, "entries": self._entries,
                   "updated_at": now_str()}
        plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        blob = dpapi_protect(plain, new_entropy)
        self._ensure_dir()
        # 两连 replace 之间有极小失败窗口（换密瞬间断电会锁死），量级毫秒，
        # 权衡后接受：顺序=先 vault 后 meta，保证「meta 说能解」时一定真能解
        _atomic_write_bytes(self.vault_path, blob)
        new_meta = {
            "schema": SCHEMA_VERSION,
            "kdf": KDF_NAME,
            "iter": KDF_ITERATIONS,
            "salt_b64": base64.b64encode(new_salt).decode("ascii"),
            "verify_sha256": verification_hex(new_entropy),
        }
        _atomic_write_bytes(self.meta_path,
                            json.dumps(new_meta, ensure_ascii=False, indent=1).encode("utf-8"))
        self._meta, self._entropy = new_meta, new_entropy
        self._updated_at = payload["updated_at"]
        return True

    # ---------- 内部 ----------
    def _ensure_unlocked(self) -> None:
        if self.locked:
            raise VaultLockedError("保险箱处于锁定状态")

    def _ensure_dir(self) -> None:
        if self._dir:
            os.makedirs(self._dir, exist_ok=True)

    def _load_meta(self) -> dict:
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError) as exc:
            raise VaultError("meta.json 不可读") from exc
        required = ("schema", "kdf", "iter", "salt_b64", "verify_sha256")
        if not isinstance(meta, dict) or any(k not in meta for k in required):
            raise VaultError("meta.json 结构不完整")
        if int(meta.get("iter") or 0) < KDF_MIN_ITERATIONS:
            raise VaultError("meta.json 迭代数非法")
        try:
            if len(base64.b64decode(str(meta.get("salt_b64", "")))) != SALT_BYTES:
                raise VaultError("meta.json salt 长度非法")
        except (ValueError, TypeError) as exc:
            raise VaultError("meta.json salt 非法") from exc
        return meta

    def _archive_corrupt(self) -> None:
        """重建前把残留文件改名留档（.corrupt-<ts>），绝不一刀切删除"""
        self._archive_file(self.meta_path, suffix="corrupt")
        self._archive_file(self.vault_path, suffix="corrupt")

    def _archive_file(self, path: str, suffix: str) -> None:
        if path and os.path.isfile(path):
            try:
                os.replace(path, f"{path}.{suffix}-{time.strftime('%Y%m%d-%H%M%S')}")
            except OSError:
                pass

    def _mutate(self, fn):
        """变更 + 落盘的统一通道：落盘失败回滚内存，保证内存==磁盘"""
        self._ensure_unlocked()
        snapshot = copy.deepcopy(self._entries)
        prev_updated = self._updated_at
        result = fn()
        try:
            self.save()
        except VaultError:
            self._entries = snapshot
            self._updated_at = prev_updated
            raise
        return result

    def _do_add(self, entry: dict) -> dict:
        entry["id"] = next_entry_id(self._entries)
        self._entries.append(entry)
        return copy.deepcopy(entry)

    def _do_update(self, entry_id: str, patch: dict):
        for i, e in enumerate(self._entries):
            if e.get("id") == entry_id:
                e.update(patch)
                e["updated_at"] = now_str()
                return copy.deepcopy(e)
        return None

    def _do_delete(self, entry_id: str):
        for i, e in enumerate(self._entries):
            if e.get("id") == entry_id:
                return self._entries.pop(i)
        return None

    def _do_restore(self, data: dict, index) -> dict:
        ids = {e.get("id") for e in self._entries}
        if data.get("id") in ids:
            data["id"] = next_entry_id(self._entries)
        entry = normalize_entry(data)
        pos = len(self._entries)
        if isinstance(index, int) and 0 <= index <= len(self._entries):
            pos = index
        self._entries.insert(pos, entry)
        return copy.deepcopy(entry)


# ====================================================================
# 磁盘原语
# ====================================================================
def _atomic_write_bytes(path: str, data: bytes) -> None:
    """临时文件 + fsync + os.replace：断电不会半新半旧，更不会丢旧库"""
    if not path:
        raise VaultError("写入路径为空")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
