# -*- coding: utf-8 -*-
"""凭证哨兵 v4 批次回归（2026-10-08 稳定性轮 P0）。

覆盖四块新逻辑：
  1. config v3→v4 迁移：clipboard_guard_enabled 默认开启（只改写等于
     旧默认 False 的值；幂等；与 STORE_VERSIONS / DEFAULT_CONFIG 钉死）
  2. 排除规则：sanitize_guard_patterns 清洗（非法正则丢弃 / 去重 /
     上限）+ matches_exclusion 命中判定
  3. 随行密文：mask 落盘 → secret_enc（DPAPI base64）→ 揭示往返；
     回溯改判 allow 清密文；手改内容清密文；敏感内容自动过期清理
  4. DPAPI 封装（secret_vault）：往返 / 非法输入 / 垃圾密文

剪贴板侧沿用 test_clipboard_monitor 的全替身手法，不碰真实剪贴板。
"""
import datetime as _dt
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

from src import secret_guard  # noqa: E402
from src import secret_vault  # noqa: E402
from src import json_store  # noqa: E402
from src.config import DEFAULT_CONFIG  # noqa: E402
from src.fragment_manager import (  # noqa: E402
    Fragment, FragmentManager,
)
import src.clipboard_monitor as clm  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402

SECRET_TEXT = "my token: ghp_" + "A1b2C3d4E5f6G7h8I9j0" + " keep"
MASKED = secret_guard.MASK_PLACEHOLDER
NOW = _dt.datetime(2026, 10, 8, 12, 0)


# ====================================================================
# 1. config v3 → v4 迁移
# ====================================================================
class TestMigrationV3ToV4:
    def test_versions_pinned(self):
        """schema_version 三方一致：STORE_VERSIONS = DEFAULT_CONFIG = 4"""
        assert json_store.STORE_VERSIONS["config"] == 4
        assert DEFAULT_CONFIG["schema_version"] == 4

    def test_registered_at_3(self):
        assert 3 in json_store.MIGRATIONS["config"]

    def test_false_rewritten_to_true(self):
        out = json_store._migrate_config_3_to_4(
            {"clipboard_guard_enabled": False})
        assert out["clipboard_guard_enabled"] is True

    def test_true_untouched(self):
        out = json_store._migrate_config_3_to_4(
            {"clipboard_guard_enabled": True})
        assert out["clipboard_guard_enabled"] is True

    def test_missing_key_not_added(self):
        """全新键无旧值可改：迁移不做 setdefault（缺省由 DEFAULT_CONFIG 兜底）"""
        out = json_store._migrate_config_3_to_4({"theme": "dark"})
        assert "clipboard_guard_enabled" not in out
        assert out["theme"] == "dark"

    def test_idempotent(self):
        data = {"clipboard_guard_enabled": False}
        once = json_store._migrate_config_3_to_4(data)
        twice = json_store._migrate_config_3_to_4(once)
        assert twice["clipboard_guard_enabled"] is True

    def test_default_config_is_on(self):
        """P0 验收：默认值必须已开启（迁移的目标态与之钉死）"""
        assert DEFAULT_CONFIG["clipboard_guard_enabled"] is True
        assert json_store._CONFIG_V3_NEW_GUARD_ENABLED == \
            DEFAULT_CONFIG["clipboard_guard_enabled"]

    def test_new_keys_in_default_config(self):
        assert DEFAULT_CONFIG["clipboard_guard_expiry_hours"] == 72
        assert DEFAULT_CONFIG["clipboard_guard_exclude_patterns"] == []


# ====================================================================
# 2. 排除规则清洗与匹配
# ====================================================================
class TestSanitizeGuardPatterns:
    def test_non_list_returns_empty(self):
        assert secret_guard.sanitize_guard_patterns(None) == ([], [])
        assert secret_guard.sanitize_guard_patterns("abc") == ([], [])

    def test_invalid_regex_dropped(self):
        valid, compiled = secret_guard.sanitize_guard_patterns(
            ["(?i)example\\.com", "([unclosed", "", 123])
        assert valid == ["(?i)example\\.com"]
        assert len(compiled) == 1

    def test_dedup_keeps_order(self):
        valid, _ = secret_guard.sanitize_guard_patterns(["a+b", "b+c", "a+b"])
        assert valid == ["a+b", "b+c"]

    def test_limit(self):
        pats = [f"pat{i}" for i in range(30)]
        valid, compiled = secret_guard.sanitize_guard_patterns(pats)
        assert len(valid) == secret_guard.EXCLUDE_PATTERNS_MAX
        assert len(compiled) == secret_guard.EXCLUDE_PATTERNS_MAX

    def test_matches_exclusion(self):
        _, compiled = secret_guard.sanitize_guard_patterns(
            ["(?i)example[._]com"])
        assert secret_guard.matches_exclusion(
            "see https://Example.com/docs", compiled)
        assert not secret_guard.matches_exclusion("nothing here", compiled)
        assert not secret_guard.matches_exclusion("x", [])


# ====================================================================
# 3. 随行密文与过期（FragmentManager 层）
# ====================================================================
class TestFragmentSecret:
    def _fm(self, tmp_path):
        return FragmentManager(str(tmp_path / "fragments.json"))

    def test_attach_and_get(self, tmp_path):
        fm = self._fm(tmp_path)
        fid = fm.add_clipboard_text("masked")
        assert fm.attach_secret(fid, "YWJj") is True
        assert fm.get_secret_blob(fid) == "YWJj"
        assert fm.guarded_fragments()[0].fragment_id == fid

    def test_attach_empty_rejected(self, tmp_path):
        fm = self._fm(tmp_path)
        fid = fm.add_clipboard_text("masked")
        assert fm.attach_secret(fid, "") is False
        assert fm.get_secret_blob(fid) == ""

    def test_clear_secret(self, tmp_path):
        fm = self._fm(tmp_path)
        fid = fm.add_clipboard_text("masked")
        fm.attach_secret(fid, "blob")
        assert fm.clear_secret(fid) is True
        assert fm.get_secret_blob(fid) == ""
        assert fm.clear_secret(fid) is False    # 已空，无变化

    def test_roundtrip_dict(self, tmp_path):
        fm = self._fm(tmp_path)
        fid = fm.add_clipboard_text("masked")
        fm.attach_secret(fid, "blob-xyz")
        frag2 = Fragment.from_dict(fm.get_fragment(fid).to_dict())
        assert frag2.secret_enc == "blob-xyz"

    def test_update_content_clears_blob(self, tmp_path):
        fm = self._fm(tmp_path)
        fid = fm.add_clipboard_text("masked")
        fm.attach_secret(fid, "blob")
        fm.update_fragment(fid, content="user edited")
        assert fm.get_secret_blob(fid) == ""

    def test_from_dict_default_empty(self):
        """旧数据无 secret_enc 字段 → 空串（零迁移）"""
        f = Fragment.from_dict({"fragment_id": 1, "type": "clipboard_text",
                                "content": "x", "source": "",
                                "created_at": "2026-10-08 10:00"})
        assert f.secret_enc == ""


class TestPurgeExpiredGuarded:
    def _fm_with_old_and_fresh(self, tmp_path):
        fm = FragmentManager(str(tmp_path / "fragments.json"))
        old_id = fm.add_clipboard_text("masked-old")
        fm.get_fragment(old_id).created_at = "2026-01-01 00:00"
        fm.attach_secret(old_id, "blob-old")
        fresh_id = fm.add_clipboard_text("masked-fresh")
        fm.get_fragment(fresh_id).created_at = "2099-01-01 00:00"
        fm.attach_secret(fresh_id, "blob-fresh")
        plain_id = fm.add_clipboard_text("plain-old")
        fm.get_fragment(plain_id).created_at = "2026-01-01 00:00"
        return fm, old_id, fresh_id, plain_id

    def test_purge_deletes_only_expired_guarded(self, tmp_path):
        fm, old_id, fresh_id, plain_id = self._fm_with_old_and_fresh(tmp_path)
        expired = fm.purge_expired_guarded(72, now=NOW)
        assert expired == [old_id]
        assert fm.get_fragment(old_id) is None
        assert fm.get_fragment(fresh_id) is not None
        assert fm.get_fragment(plain_id) is not None   # 普通碎片不受影响

    def test_zero_hours_noop(self, tmp_path):
        fm, old_id, _fresh, _plain = self._fm_with_old_and_fresh(tmp_path)
        assert fm.purge_expired_guarded(0) == []
        assert fm.get_fragment(old_id) is not None

    def test_unparseable_date_kept(self, tmp_path):
        fm = FragmentManager(str(tmp_path / "fragments.json"))
        fid = fm.add_clipboard_text("masked")
        fm.get_fragment(fid).created_at = "not-a-date"
        fm.attach_secret(fid, "blob")
        assert fm.purge_expired_guarded(72, now=NOW) == []
        assert fm.get_fragment(fid) is not None


# ====================================================================
# 4. 监听链路（全替身，不碰真实剪贴板）
# ====================================================================
class _StubQApp:
    _clip = None

    @classmethod
    def clipboard(cls):
        return cls._clip


class FakeMimeData:
    def hasImage(self):
        return False

    def hasFormat(self, _fmt):
        return False


class FakeClipboard(QObject):
    """QClipboard 替身：text/mimeData 可编程，dataChanged 手动发射。"""

    dataChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._text = ""

    def text(self):
        return self._text

    def mimeData(self):
        return FakeMimeData()


class FakeConfig:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture()
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def build_monitor(monkeypatch, tmp_path, config):
    clip = FakeClipboard()
    monkeypatch.setattr(clm, "QApplication", _StubQApp)
    monkeypatch.setattr(_StubQApp, "_clip", clip)
    monkeypatch.setattr(clm, "_get_foreground_process_name", lambda: "")
    fm = FragmentManager(str(tmp_path / "fragments.json"))
    mon = ClipboardMonitor(fm, config_manager=config)
    mon.start()
    mon._expiry_timer.stop()   # 测试不跑真实 1 小时巡检
    return mon, clip, fm


def paste_text(clip, text):
    clip._text = text
    clip.dataChanged.emit()


class TestMonitorMaskFlow:
    def test_masked_capture_attaches_encrypted_original(
            self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_guard_enabled": True,
                          "clipboard_guard_mode": "mask"})
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        assert fm.count() == 1
        frag = fm.get_all_fragments()[0]
        # 落盘内容 = 遮蔽占位（含上下文文本）
        assert MASKED in frag.content
        assert "ghp_" not in frag.content
        # 随行密文存在且可解回原文（揭示往返）
        assert frag.secret_enc
        assert secret_vault.unprotect(frag.secret_enc) == SECRET_TEXT
        # 待办队列有记录
        assert mon.guard_pending_count() == 1

    def test_resolve_allow_clears_blob_and_writes_original(
            self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_guard_enabled": True,
                          "clipboard_guard_mode": "mask"})
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        frag = fm.get_all_fragments()[0]
        fid = frag.fragment_id
        # 回溯改判为「仅本次记录原文」
        assert mon.resolve_guard(0, secret_guard.MODE_ALLOW) is True
        frag2 = fm.get_fragment(fid)
        assert frag2.content == SECRET_TEXT
        assert fm.get_secret_blob(fid) == ""     # 原文明文在池 → 密文清除
        assert mon.guard_pending_count() == 0

    def test_deny_captures_nothing(self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_guard_enabled": True,
                          "clipboard_guard_mode": "deny"})
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        assert fm.count() == 0
        assert mon.guard_pending_count() == 1
        assert mon.guard_pending()[0]["fragment_id"] is None

    def test_disabled_keeps_plaintext(self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_guard_enabled": False})
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        assert fm.get_all_fragments()[0].content == SECRET_TEXT
        assert mon.guard_pending_count() == 0

    def test_exclusion_pattern_allows_secret_like_text(
            self, qapp, monkeypatch, tmp_path):
        """用户排除规则命中的文本：即便形似密钥也放行收录，不进队列"""
        cfg = FakeConfig({
            "clipboard_guard_enabled": True,
            "clipboard_guard_mode": "mask",
            "clipboard_guard_exclude_patterns": ["my token"],
        })
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        assert fm.get_all_fragments()[0].content == SECRET_TEXT
        assert mon.guard_pending_count() == 0

    def test_sweep_expired_via_monitor(self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_guard_enabled": True,
                          "clipboard_guard_mode": "mask",
                          "clipboard_guard_expiry_hours": 72})
        mon, clip, fm = build_monitor(monkeypatch, tmp_path, cfg)
        paste_text(clip, SECRET_TEXT)
        fid = fm.get_all_fragments()[0].fragment_id
        fm.get_fragment(fid).created_at = "2026-01-01 00:00"
        mon.sweep_expired_guarded()
        assert fm.get_fragment(fid) is None


# ====================================================================
# 5. DPAPI 封装（secret_vault）
# ====================================================================
@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows")
class TestSecretVault:
    def test_roundtrip(self):
        token = secret_vault.protect(SECRET_TEXT)
        assert token and token.isascii()
        assert secret_vault.unprotect(token) == SECRET_TEXT
        assert SECRET_TEXT not in token          # 密文不含明文

    def test_bad_inputs_return_none(self):
        assert secret_vault.unprotect("") is None
        assert secret_vault.unprotect(None) is None
        assert secret_vault.unprotect("not-base64!!") is None

    def test_garbage_blob_returns_none(self):
        import base64
        junk = base64.b64encode(b"garbage-not-dpapi").decode()
        assert secret_vault.unprotect(junk) is None

    def test_protect_rejects_non_str(self):
        with pytest.raises(secret_vault.SecretVaultError):
            secret_vault.protect(b"bytes-input")
