# -*- coding: utf-8 -*-
"""密码保险箱核心（plugins/vault/vault_core.py）回归 —— 纯逻辑，无 GUI。

这是**存密码的库**，错起来的代价不是「功能不可用」而是「凭证泄露 /
数据锁死」，所以本文件把四类东西钉死：

  A 校验层：坏数据（空标题/空字段名/非字符串值）一律 ValueError，
    绝不静默修——静默修掉的恰是用户以为存进去了的东西
  B 加密与存储：DPAPI 往返（ASCII/中文/空串/4KB）、错误 entropy 解不出、
    meta.json 明文只含五个字段、vault.bin 与 meta 里**永不出现明文**
  C 状态机：missing→ready、unlock 失败统一 False 不泄信息、lock 丢明文、
    DPAPI 失败拒绝保存且内存回滚（内存==磁盘）
  D 安全语义：搜索永不命中 secret 字段值（防肩窥）、掩码固定长度
    （防猜长度）、改密必须换 salt
"""

import base64
import importlib.util
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

CORE_PATH = os.path.join(ROOT, "plugins", "vault", "vault_core.py")
MANIFEST_PATH = os.path.join(ROOT, "plugins", "vault", "manifest.json")
PLUGIN_PATH = os.path.join(ROOT, "plugins", "vault", "plugin.py")
_MOD = "fp_test_vault_core"

WIN_ONLY = pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows")


@pytest.fixture(scope="module")
def vc():
    """按插件加载器的方式直载核心模块（纯逻辑，不需要 QApplication）"""
    spec = importlib.util.spec_from_file_location(_MOD, CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tmpdir_(tmp_path):
    return str(tmp_path)


def make_vault(vc, tmpdir_, password="master-pw-1", populate=None):
    """建库 + 解锁的常用前置；populate(entries) 可塞初始条目"""
    v = vc.Vault(tmpdir_)
    v.create(password)
    if populate:
        for title, fields, tags in populate:
            v.add(title, fields, tags)
    return v


def sample_fields(**overrides):
    fields = [
        {"key": "用户名", "value": "2026heshao", "secret": False},
        {"key": "密码", "value": "p@ssw0rd-中文", "secret": True},
        {"key": "备注", "value": "PAT 已作废", "secret": False},
    ]
    return fields


# ====================================================================
# A 校验层
# ====================================================================
class TestNormalize:
    def test_title_strips(self, vc):
        assert vc.normalize_title("  GitHub  ") == "GitHub"

    def test_title_empty_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_title("   ")

    def test_title_non_str_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_title(123)

    def test_title_too_long_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_title("长" * 201)

    def test_field_empty_key_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_field({"key": "  ", "value": "x"})

    def test_field_non_str_value_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_field({"key": "k", "value": 42})

    def test_field_value_may_be_empty(self, vc):
        f = vc.normalize_field({"key": "空值", "value": ""})
        assert f["value"] == "" and f["secret"] is False

    def test_field_secret_coerced_to_bool(self, vc):
        assert vc.normalize_field({"key": "k", "value": "v", "secret": 1})["secret"] is True
        assert vc.normalize_field({"key": "k", "value": "v"})["secret"] is False

    def test_fields_none_becomes_empty_list(self, vc):
        assert vc.normalize_fields(None) == []
        assert vc.normalize_tags(None) == []

    def test_tags_dedupe_and_cap(self, vc):
        out = vc.normalize_tags(["开发", " 开发 ", "", "x" * 50])
        assert out == ["开发", "x" * 40]

    def test_tags_non_str_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.normalize_tags([1])

    def test_normalize_entry_fills_defaults(self, vc):
        e = vc.normalize_entry({"title": "t"})
        assert e["id"] and e["created_at"] and e["updated_at"]
        assert e["fields"] == [] and e["tags"] == []

    def test_next_entry_id_increments(self, vc):
        entries = [{"id": "e0001"}, {"id": "e0003"}]
        assert vc.next_entry_id(entries) == "e0004"
        assert vc.next_entry_id([]) == "e0001"


# ====================================================================
# B1 KDF
# ====================================================================
class TestKdf:
    def test_deterministic_same_salt(self, vc):
        salt = os.urandom(16)
        a = vc.derive_entropy("pw", salt, 2000)
        b = vc.derive_entropy("pw", salt, 2000)
        assert a == b

    def test_different_salt_differs(self, vc):
        a = vc.derive_entropy("pw", os.urandom(16), 2000)
        b = vc.derive_entropy("pw", os.urandom(16), 2000)
        assert a != b

    def test_different_password_differs(self, vc):
        salt = os.urandom(16)
        assert vc.derive_entropy("pw1", salt, 2000) != vc.derive_entropy("pw2", salt, 2000)

    def test_length_32(self, vc):
        assert len(vc.derive_entropy("pw", os.urandom(16), 2000)) == 32

    def test_unicode_password(self, vc):
        ent = vc.derive_entropy("主密码✋", os.urandom(16), 2000)
        assert len(ent) == 32

    def test_verification_hex_matches(self, vc):
        ent = vc.derive_entropy("pw", os.urandom(16), 2000)
        import hashlib
        assert vc.verification_hex(ent) == hashlib.sha256(ent).hexdigest()

    def test_verify_password_true_and_false(self, vc):
        salt = os.urandom(16)
        ent = vc.derive_entropy("pw", salt, 2000)
        vhex = vc.verification_hex(ent)
        assert vc.verify_password("pw", salt, 2000, vhex) is True
        assert vc.verify_password("bad", salt, 2000, vhex) is False
        assert vc.verify_password("pw", salt, 2000, "") is False

    def test_bad_params_rejected(self, vc):
        with pytest.raises(ValueError):
            vc.derive_entropy("pw", b"short", 2000)
        with pytest.raises(ValueError):
            vc.derive_entropy("pw", os.urandom(16), 10)      # 低于 KDF_MIN_ITERATIONS


# ====================================================================
# B2 DPAPI
# ====================================================================
@WIN_ONLY
class TestDpapi:
    def test_roundtrip_ascii(self, vc):
        ent = os.urandom(32)
        blob = vc.dpapi_protect(b"ascii secret", ent)
        assert vc.dpapi_unprotect(blob, ent) == b"ascii secret"

    def test_roundtrip_chinese(self, vc):
        ent = os.urandom(32)
        blob = vc.dpapi_protect("中文机密内容".encode("utf-8"), ent)
        assert vc.dpapi_unprotect(blob, ent) == "中文机密内容".encode("utf-8")

    def test_roundtrip_empty(self, vc):
        ent = os.urandom(32)
        blob = vc.dpapi_protect(b"", ent)
        assert blob and vc.dpapi_unprotect(blob, ent) == b""

    def test_roundtrip_4kb(self, vc):
        ent = os.urandom(32)
        data = os.urandom(4096)
        assert vc.dpapi_unprotect(vc.dpapi_protect(data, ent), ent) == data

    def test_wrong_entropy_returns_none(self, vc):
        blob = vc.dpapi_protect(b"x", os.urandom(32))
        assert vc.dpapi_unprotect(blob, os.urandom(32)) is None

    def test_missing_entropy_returns_none(self, vc):
        blob = vc.dpapi_protect(b"x", os.urandom(32))
        assert vc.dpapi_unprotect(blob, b"") is None

    def test_entropy_symmetry(self, vc):
        blob = vc.dpapi_protect(b"x", b"")                # 无 entropy 加密
        assert vc.dpapi_unprotect(blob, os.urandom(32)) is None

    def test_garbage_blob_returns_none(self, vc):
        assert vc.dpapi_unprotect(b"garbage-not-a-blob", os.urandom(32)) is None
        assert vc.dpapi_unprotect(b"", os.urandom(32)) is None

    def test_overhead_positive(self, vc):
        blob = vc.dpapi_protect(b"x", os.urandom(32))
        assert len(blob) > 200                             # 实测固定开销 ~238B

    def test_non_bytes_rejected(self, vc):
        with pytest.raises(vc.VaultError):
            vc.dpapi_protect("str-not-bytes", os.urandom(32))


# ====================================================================
# C1 状态机：missing / ready / corrupt + create
# ====================================================================
class TestStatusCreate:
    def test_fresh_dir_missing(self, vc, tmpdir_):
        assert vc.Vault(tmpdir_).status() == "missing"

    def test_create_then_ready(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        assert vc.Vault(tmpdir_).status() == "ready"

    def test_create_twice_rejected(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        with pytest.raises(vc.VaultError):
            vc.Vault(tmpdir_).create("another")

    def test_create_empty_password_rejected(self, vc, tmpdir_):
        with pytest.raises(ValueError):
            vc.Vault(tmpdir_).create("")

    def test_create_non_str_password_rejected(self, vc, tmpdir_):
        with pytest.raises(ValueError):
            vc.Vault(tmpdir_).create(None)

    def test_meta_only_corrupt(self, vc, tmpdir_):
        os.makedirs(tmpdir_, exist_ok=True)
        with open(os.path.join(tmpdir_, vc.META_FILE), "w", encoding="utf-8") as f:
            f.write("{}")
        assert vc.Vault(tmpdir_).status() == "corrupt"

    def test_meta_bad_json_corrupt(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        os.remove(os.path.join(tmpdir_, vc.VAULT_FILE))
        with open(os.path.join(tmpdir_, vc.META_FILE), "w", encoding="utf-8") as f:
            f.write("not json{")
        assert vc.Vault(tmpdir_).status() == "corrupt"

    def test_vault_only_corrupt(self, vc, tmpdir_):
        os.makedirs(tmpdir_, exist_ok=True)
        with open(os.path.join(tmpdir_, vc.VAULT_FILE), "wb") as f:
            f.write(b"leftover")
        assert vc.Vault(tmpdir_).status() == "corrupt"

    def test_meta_missing_keys_corrupt(self, vc, tmpdir_):
        os.makedirs(tmpdir_, exist_ok=True)
        with open(os.path.join(tmpdir_, vc.META_FILE), "w", encoding="utf-8") as f:
            json.dump({"schema": 1}, f)
        assert vc.Vault(tmpdir_).status() == "corrupt"

    def test_create_on_corrupt_archives_old_files(self, vc, tmpdir_):
        os.makedirs(tmpdir_, exist_ok=True)
        with open(os.path.join(tmpdir_, vc.VAULT_FILE), "wb") as f:
            f.write(b"old-broken")
        v = vc.Vault(tmpdir_)
        v.create("pw")
        leftovers = [n for n in os.listdir(tmpdir_) if ".corrupt-" in n]
        assert leftovers and any(n.startswith(vc.VAULT_FILE) for n in leftovers)
        assert v.unlock("pw") is True or v.locked is False

    def test_meta_exactly_five_fields(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        with open(os.path.join(tmpdir_, vc.META_FILE), encoding="utf-8") as f:
            meta = json.load(f)
        assert set(meta.keys()) == {"schema", "kdf", "iter", "salt_b64", "verify_sha256"}
        assert meta["schema"] == 1 and meta["kdf"] == "pbkdf2-sha256"
        assert meta["iter"] == 200_000
        assert len(base64.b64decode(meta["salt_b64"])) == 16

    def test_meta_verify_matches_kdf(self, vc, tmpdir_):
        make_vault(vc, tmpdir_, password="pw-x")
        with open(os.path.join(tmpdir_, vc.META_FILE), encoding="utf-8") as f:
            meta = json.load(f)
        salt = base64.b64decode(meta["salt_b64"])
        assert vc.verify_password("pw-x", salt, meta["iter"], meta["verify_sha256"])


# ====================================================================
# C2 状态机：unlock / lock
# ====================================================================
class TestUnlockLock:
    def test_locked_by_default(self, vc, tmpdir_):
        assert vc.Vault(tmpdir_).locked is True

    def test_unlock_correct_password(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        v = vc.Vault(tmpdir_)
        assert v.unlock("master-pw-1") is True
        assert v.locked is False

    def test_unlock_wrong_password_false(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        v = vc.Vault(tmpdir_)
        assert v.unlock("wrong") is False
        assert v.locked is True

    def test_unlock_missing_vault_false(self, vc, tmpdir_):
        assert vc.Vault(tmpdir_).unlock("pw") is False

    def test_lock_drops_entries(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t1", sample_fields(), [])])
        assert v.entry_count == 1
        v.lock()
        assert v.locked and v.entry_count == 0

    def test_unsaved_change_lost_after_lock(self, vc, tmpdir_):
        """lock 丢的是内存态——没落盘的改动本来就该丢（设计语义）"""
        v = make_vault(vc, tmpdir_)
        v._entries.append({"id": "hack", "title": "ghost"})
        v.lock()
        v.unlock("master-pw-1")
        assert all(e["id"] != "hack" for e in v.entries)

    def test_double_unlock_ok(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        assert v.unlock("master-pw-1") is True
        assert v.unlock("master-pw-1") is True

    def test_unlock_after_failed_attempt(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        assert v.unlock("nope") is False
        assert v.unlock("master-pw-1") is True

    def test_garbage_vault_bin_unlock_false(self, vc, tmpdir_):
        make_vault(vc, tmpdir_)
        v = vc.Vault(tmpdir_)
        with open(v.vault_path, "wb") as f:
            f.write(b"garbage-not-a-blob")
        assert v.unlock("master-pw-1") is False

    def test_unlocked_entries_are_copies(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        snap = v.entries
        snap[0]["title"] = "mutated"
        assert v.entries[0]["title"] == "t"

    def test_entries_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).entries

    def test_plaintext_never_hits_disk(self, vc, tmpdir_):
        """安全钉：整库密文 + meta 里都不能出现明文标题/字段值"""
        v = make_vault(vc, tmpdir_, populate=[("GitHub 主账号", sample_fields(), ["开发"])])
        v.lock()
        raw = open(v.vault_path, "rb").read()
        assert b"GitHub" not in raw
        assert "主账号".encode("utf-8") not in raw
        assert "2026heshao".encode("utf-8") not in raw
        meta_raw = open(v.meta_path, "rb").read()
        assert b"GitHub" not in meta_raw

    def test_corrupt_content_degrades_not_crashes(self, vc, tmpdir_):
        """DPAPI 能解开但内容是坏 JSON → 降级空库 + 留档，不崩"""
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        v.lock()
        meta = json.load(open(v.meta_path, encoding="utf-8"))
        salt = base64.b64decode(meta["salt_b64"])
        entropy = vc.derive_entropy("master-pw-1", salt, meta["iter"])
        blob = vc.dpapi_protect(b"this is not json{{{", entropy)
        with open(v.vault_path, "wb") as f:
            f.write(blob)
        v2 = vc.Vault(tmpdir_)
        assert v2.unlock("master-pw-1") is True
        assert v2.degraded is True and v2.entry_count == 0
        leftovers = [n for n in os.listdir(tmpdir_) if ".corrupt-" in n]
        assert leftovers


# ====================================================================
# C3 保存（原子性 / 失败回滚）
# ====================================================================
class TestSave:
    def test_save_persists_across_instances(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("持久条目", sample_fields(), [])])
        v.lock()
        v2 = vc.Vault(tmpdir_)
        assert v2.unlock("master-pw-1") is True
        assert v2.entries[0]["title"] == "持久条目"

    def test_no_tmp_leftover(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert not os.path.exists(v.vault_path + ".tmp")
        assert not os.path.exists(v.meta_path + ".tmp")

    def test_save_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).save()

    def test_dpapi_failure_rolls_back_memory(self, vc, tmpdir_, monkeypatch):
        """安全钉：加密失败 → 拒绝保存 + 旧密文原封不动 + 内存回滚"""
        v = make_vault(vc, tmpdir_, populate=[("t1", sample_fields(), [])])
        blob_before = open(v.vault_path, "rb").read()

        def boom(data, entropy):
            raise vc.VaultError("simulated dpapi failure")

        monkeypatch.setattr(vc, "dpapi_protect", boom)
        with pytest.raises(vc.VaultError):
            v.add("t2", sample_fields(), [])
        assert v.entry_count == 1                      # 内存回滚：新条目消失
        assert [e["title"] for e in v.entries] == ["t1"]
        assert open(v.vault_path, "rb").read() == blob_before

    def test_payload_structure(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        meta = json.load(open(v.meta_path, encoding="utf-8"))
        entropy = vc.derive_entropy("master-pw-1",
                                    base64.b64decode(meta["salt_b64"]), meta["iter"])
        plain = vc.dpapi_unprotect(open(v.vault_path, "rb").read(), entropy)
        payload = json.loads(plain.decode("utf-8"))
        assert set(payload.keys()) == {"schema", "entries", "updated_at"}
        assert payload["schema"] == 1
        assert payload["entries"][0]["title"] == "t"

    def test_empty_vault_save_ok(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        v.save()
        assert os.path.isfile(v.vault_path)

    def test_repeated_save_idempotent_size(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        v.save()
        s1 = os.path.getsize(v.vault_path)
        v.save()
        s2 = os.path.getsize(v.vault_path)
        assert s1 == s2                                # 整库重写，非追加


# ====================================================================
# D1 CRUD
# ====================================================================
class TestCrud:
    def test_add_returns_full_entry(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        e = v.add("GitHub 主账号", sample_fields(), ["开发"])
        assert e["id"] == "e0001"
        assert e["title"] == "GitHub 主账号"
        assert len(e["fields"]) == 3
        assert e["tags"] == ["开发"]
        assert e["created_at"] and e["updated_at"]

    def test_ids_increment(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        assert v.add("a").get("id") == "e0001"
        assert v.add("b").get("id") == "e0002"
        assert v.add("c").get("id") == "e0003"

    def test_add_empty_title_rejected(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        with pytest.raises(ValueError):
            v.add("   ")

    def test_add_bad_field_rejected(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        with pytest.raises(ValueError):
            v.add("t", [{"key": "", "value": "v"}])

    def test_add_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).add("t")

    def test_update_title_keeps_fields(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), ["tag1"])])
        eid = v.entries[0]["id"]
        u = v.update(eid, title="新标题")
        assert u["title"] == "新标题"
        assert len(u["fields"]) == 3 and u["tags"] == ["tag1"]

    def test_update_fields_partial_semantics(self, vc, tmpdir_):
        """update 只传 None 的字段不动（与宿主 manage 同语义）"""
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), ["tag1"])])
        eid = v.entries[0]["id"]
        u = v.update(eid, fields=[{"key": "k", "value": "v", "secret": False}])
        assert u["fields"][0]["key"] == "k" and u["title"] == "t"

    def test_update_nothing_returns_current(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        eid = v.entries[0]["id"]
        assert v.update(eid)["title"] == "t"

    def test_update_nonexistent_returns_none(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        assert v.update("e9999", title="x") is None

    def test_update_touches_updated_at(self, vc, tmpdir_, monkeypatch):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        eid = v.entries[0]["id"]
        created = v.entries[0]["created_at"]
        monkeypatch.setattr(vc, "now_str", lambda: "2030-01-01 00:00")
        u = v.update(eid, title="t2")
        assert u["updated_at"] == "2030-01-01 00:00"
        assert u["created_at"] == created

    def test_get_entry(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        eid = v.entries[0]["id"]
        assert v.get(eid)["title"] == "t"
        assert v.get("e9999") is None

    def test_delete_returns_entry(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        eid = v.entries[0]["id"]
        deleted = v.delete(eid)
        assert deleted["id"] == eid and v.entry_count == 0

    def test_delete_nonexistent_returns_none(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        assert v.delete("e9999") is None

    def test_delete_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).delete("e0001")

    def test_restore_reinserts_same_id(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), ["tag"])])
        deleted = v.delete(v.entries[0]["id"])
        r = v.restore(deleted)
        assert r["id"] == deleted["id"] and r["title"] == "t" and r["tags"] == ["tag"]

    def test_restore_reassigns_colliding_id(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        v.add("a")
        deleted = v.delete("e0001")
        v.add("b")                                        # 占用 e0001
        r = v.restore(deleted)
        assert r["id"] != "e0001" and v.entry_count == 2

    def test_crud_persisted(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        e = v.add("存活条目", sample_fields(), [])
        v.update(e["id"], title="存活条目2")
        v.delete(e["id"])
        v.restore(e)                                       # 回到内存
        v2 = vc.Vault(tmpdir_)
        v2.unlock("master-pw-1")
        assert [x["title"] for x in v2.entries] == ["存活条目"]


# ====================================================================
# D2 改密
# ====================================================================
class TestChangePassword:
    def test_wrong_old_false(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert v.change_password("bad", "new-pw") is False
        assert v.unlock("master-pw-1") is True or v.locked is False

    def test_change_ok_and_data_survives(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert v.change_password("master-pw-1", "new-pw") is True
        assert v.entries[0]["title"] == "t"
        v.lock()
        v2 = vc.Vault(tmpdir_)
        assert v2.unlock("master-pw-1") is False
        assert v2.unlock("new-pw") is True
        assert v2.entries[0]["title"] == "t"

    def test_salt_rotates(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        meta_before = json.load(open(v.meta_path, encoding="utf-8"))
        assert v.change_password("master-pw-1", "new-pw") is True
        meta_after = json.load(open(v.meta_path, encoding="utf-8"))
        assert meta_after["salt_b64"] != meta_before["salt_b64"]
        assert meta_after["verify_sha256"] != meta_before["verify_sha256"]

    def test_new_empty_rejected(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        with pytest.raises(ValueError):
            v.change_password("master-pw-1", "")

    def test_change_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).change_password("a", "b")


# ====================================================================
# D3 搜索（安全语义）
# ====================================================================
class TestSearch:
    def test_matches_title(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("GitHub 主账号", sample_fields(), [])])
        assert len(v.search("GitHub")) == 1

    def test_matches_plain_field_value(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert len(v.search("2026heshao")) == 1

    def test_never_matches_secret_value(self, vc, tmpdir_):
        """安全钉：secret 值不进搜索（防肩窥）"""
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert v.search("p@ssw0rd-中文") == []

    def test_case_insensitive_ascii(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("GitHub Repo", sample_fields(), [])])
        assert len(v.search("github repo")) == 1

    def test_no_match_empty(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        assert v.search("zzz-not-there") == []

    def test_blank_query_returns_all(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        v.add("a")
        v.add("b")
        assert len(v.search("")) == 2
        assert len(v.search("   ")) == 2

    def test_search_when_locked_raises(self, vc, tmpdir_):
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).search("x")

    def test_search_returns_copies(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        hits = v.search("t")
        hits[0]["title"] = "mutated"
        assert v.entries[0]["title"] == "t"


# ====================================================================
# E 小工具与设置
# ====================================================================
class TestHelpers:
    def test_mask_fixed_length(self, vc):
        """安全钉：掩码不随真实长度变化（防看掩码猜长度）"""
        assert vc.mask_value("a") == vc.mask_value("a" * 100) == vc.MASK

    def test_first_secret_value(self, vc):
        e = {"fields": sample_fields()}
        assert vc.first_secret_value(e) == "p@ssw0rd-中文"

    def test_first_secret_value_none(self, vc):
        assert vc.first_secret_value({"fields": [{"key": "k", "value": "v", "secret": False}]}) is None
        assert vc.first_secret_value(None) is None

    def test_sort_entries_updated_desc(self, vc):
        entries = [
            {"id": "e1", "title": "旧", "updated_at": "2026-01-01 10:00"},
            {"id": "e2", "title": "新", "updated_at": "2026-02-01 10:00"},
        ]
        assert [e["id"] for e in vc.sort_entries(entries)] == ["e2", "e1"]

    def test_now_str_format(self, vc):
        import re
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", vc.now_str())

    def test_sanitize_idle_valid(self, vc):
        for v in (0, 1, 5, 15):
            assert vc.sanitize_idle_minutes(v) == v

    def test_sanitize_idle_invalid_defaults(self, vc):
        assert vc.sanitize_idle_minutes(7) == 5
        assert vc.sanitize_idle_minutes(-1) == 5
        assert vc.sanitize_idle_minutes("abc") == 5
        assert vc.sanitize_idle_minutes(None) == 5

    def test_sanitize_idle_str_and_custom_default(self, vc):
        assert vc.sanitize_idle_minutes("15") == 15
        assert vc.sanitize_idle_minutes("x", default=15) == 15

    def test_settings_roundtrip(self, vc, tmpdir_):
        assert vc.save_settings(tmpdir_, {"idle_minutes": 15}) is True
        assert vc.load_settings(tmpdir_) == {"idle_minutes": 15}

    def test_settings_missing_file_default(self, vc, tmpdir_):
        assert vc.load_settings(tmpdir_) == {"idle_minutes": 5}

    def test_settings_corrupt_file_default(self, vc, tmpdir_):
        os.makedirs(tmpdir_, exist_ok=True)
        with open(os.path.join(tmpdir_, "settings.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        assert vc.load_settings(tmpdir_) == {"idle_minutes": 5}

    def test_settings_zero_means_never(self, vc, tmpdir_):
        assert vc.save_settings(tmpdir_, {"idle_minutes": 0}) is True
        assert vc.load_settings(tmpdir_)["idle_minutes"] == 0

    def test_settings_bad_value_sanitized(self, vc, tmpdir_):
        assert vc.save_settings(tmpdir_, {"idle_minutes": 999}) is True
        assert vc.load_settings(tmpdir_)["idle_minutes"] == 5


# ====================================================================
# F 导出
# ====================================================================
class TestExport:
    def test_export_when_locked_raises(self, vc, tmpdir_):
        import os as _os
        with pytest.raises(vc.VaultLockedError):
            vc.Vault(tmpdir_).export_json(_os.path.join(tmpdir_, "x.json"))

    def test_export_contains_entries(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_, populate=[("导出条目", sample_fields(), [])])
        out = os.path.join(tmpdir_, "export.json")
        v.export_json(out)
        data = json.load(open(out, encoding="utf-8"))
        assert data["entries"][0]["title"] == "导出条目"
        assert "exported_at" in data

    def test_export_is_plaintext_by_design(self, vc, tmpdir_):
        """导出文件必须可直接读——这是功能（换机迁移），风险由 UI 二次确认拦截"""
        v = make_vault(vc, tmpdir_, populate=[("t", sample_fields(), [])])
        out = os.path.join(tmpdir_, "export.json")
        v.export_json(out)
        assert "p@ssw0rd-中文" in open(out, encoding="utf-8").read()

    def test_export_no_tmp_leftover(self, vc, tmpdir_):
        v = make_vault(vc, tmpdir_)
        out = os.path.join(tmpdir_, "export.json")
        v.export_json(out)
        assert not os.path.exists(out + ".tmp")


# ====================================================================
# G 依赖护栏（AST）
# ====================================================================
class TestAst:
    def test_no_qt_import(self, vc):
        """核心层严禁 import PyQt6（注释里提到也不算——只认真实 import 语句）"""
        import ast
        tree = ast.parse(open(CORE_PATH, encoding="utf-8").read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert "PyQt6" not in mods
        assert not any(m.startswith("PyQt6") for m in mods)

    def test_only_stdlib(self, vc):
        import ast
        tree = ast.parse(open(CORE_PATH, encoding="utf-8").read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        allowed = {"base64", "copy", "ctypes", "hashlib", "hmac", "json",
                   "os", "time", "__future__"}
        assert mods <= allowed, f"核心层引入了非白名单依赖：{sorted(mods - allowed)}"

    def test_required_modules_present(self, vc):
        import ast
        tree = ast.parse(open(CORE_PATH, encoding="utf-8").read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert {"ctypes", "hashlib", "hmac", "json"} <= mods

    def test_manifest_shape(self):
        """manifest 零能力声明 + 两个热键动作 + page 声明"""
        manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
        assert manifest["id"] == "vault"
        assert manifest["entry"] == "plugin.py"
        assert manifest["capabilities"] == []
        assert os.path.isfile(PLUGIN_PATH)
        ids = [a["id"] for a in manifest["actions"]]
        assert ids == ["vault.open", "vault.quick"]
        hotkeys = {a["id"]: a.get("hotkey") for a in manifest["actions"]}
        assert hotkeys["vault.open"] == "Ctrl+Alt+V"
        assert hotkeys["vault.quick"] == "Ctrl+Alt+B"
        assert manifest["page"]["title"]
