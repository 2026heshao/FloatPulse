# -*- coding: utf-8 -*-
"""凭证哨兵（secret_guard）纯逻辑回归：正则 / 香农熵 / 误判白名单 / 遮蔽。

零 PyQt6，只测纯字符串判定（写入路径拦截在 test_clipboard_guard 里）。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src import secret_guard as sg  # noqa: E402


# 造样例：刻意不用真实凭证，全部是结构合法的假串
GH = "ghp_" + "a1B2c3D4" * 5
GHO = "gho_" + "Z9y8X7w6" * 5
GH_USR = "ghpousr_" + "Qw3Er4Ty" * 4
SK = "sk-" + "Xy9zAb2C" * 5
SK_WS = "sk-ws-" + "a1B2c3D4e5F6g7H8"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
PEM = "-----BEGIN RSA PRIVATE KEY-----"
ENTROPY = "aB3dE5gH7jK9lM1nO3pQ5rS7tU9vW1xY"
SHA40 = "a3f9c2e1b4d6a8f0c2e4b6d8a0f2c4e6b8d0a2f4"
SHA64 = "a3f9c2e1b4d6a8f0c2e4b6d8a0f2c4e6b8d0a2f4c5e7f9a1b3d5e7f9c1a3b5d7"
UUID = "550e8400-e29b-41d4-a716-446655440000"


# ====================================================================
# 1. 命中：已知凭证形态
# ====================================================================
class TestDetectPositives:
    @pytest.mark.parametrize("name,text,rule", [
        ("github_ghp", GH, "github_token"),
        ("github_gho", GHO, "github_token"),
        ("github_usr", GH_USR, "github_token"),
        ("openai", SK, "openai_key"),
        ("openai_ws", SK_WS, "openai_key"),
        ("aws", AWS, "aws_key"),
        ("pem", PEM, "pem"),
        ("jwt", JWT, "jwt"),
        ("entropy", ENTROPY, "entropy"),
    ])
    def test_known_secret_hits(self, name, text, rule):
        hits = sg.detect(text)
        assert hits, f"{name} 未命中"
        assert hits[0]["rule"] == rule

    def test_bearer_token_hits_on_capture_group(self):
        text = f"Authorization: Bearer {JWT}"
        hits = sg.detect(text)
        assert hits
        # 命中原文应是密钥本体，不含 "Bearer " 前缀
        assert hits[0]["match"] == JWT
        assert "Bearer" not in hits[0]["match"]

    def test_contains_secret_matches_detect(self):
        assert sg.contains_secret(GH) is True
        assert sg.contains_secret("普通文本") is False

    def test_secret_embedded_in_sentence(self):
        text = f"我的 key 是 {SK}，注意保密"
        assert sg.contains_secret(text) is True
        hits = sg.detect(text)
        assert text[hits[0]["start"]:hits[0]["end"]] == SK


# ====================================================================
# 2. 白名单：形似密钥其实不是
# ====================================================================
class TestWhitelist:
    def test_git_sha40_passes(self):
        assert sg.contains_secret(SHA40) is False

    def test_git_sha64_passes(self):
        assert sg.contains_secret(SHA64) is False

    def test_uuid_passes(self):
        assert sg.contains_secret(UUID) is False

    def test_pure_digits_long_id_passes(self):
        assert sg.contains_secret("12345678901234567890123456789012") is False

    @pytest.mark.parametrize("text", [
        f"commit {SHA40}",
        f"rebase {SHA40}",
        f"cherry-pick {SHA40}",
        f"已合并 commit {SHA64} 到主干",
        f"git rebase --onto {SHA40} main",
    ])
    def test_vcs_context_passes(self, text):
        assert sg.contains_secret(text) is False

    def test_non_sha_hex_passes_only_via_context(self):
        """非 SHA 长度的 hex 串：裸着会被拦，带 commit 上下文才放行。

        这条专钉上下文白名单**本身**有效（40/64 hex 有无上下文都放行，
        换掉关键词它们照样过，无法反向验证上下文逻辑）。
        """
        hex52 = "a3f9c2e1b4d6a8f0c2e4b6d8a0f2c4e6b8d0a2f4a3f9c2e1b4d6"
        assert len(hex52) == 52
        assert sg.contains_secret(hex52) is True          # 裸串命中
        assert sg.contains_secret(f"commit {hex52}") is False   # 上下文放行
        assert sg.contains_secret(f"rebase --onto {hex52}") is False

    def test_low_entropy_long_string_passes(self):
        """32+ 长度但低熵（重复字符）不算凭证。"""
        assert sg.contains_secret("a" * 40) is False
        assert sg.contains_secret("ab" * 20) is False

    def test_single_char_class_long_word_passes(self):
        """纯小写长单词串（URL 路径）字符类单一 → 放行。"""
        long_words = "https://example.com/some/long/path/words/inside/here"
        assert sg.contains_secret(long_words) is False

    def test_normal_text_never_hits(self):
        for text in ("今天天气不错", "def foo(): return 42",
                     "C:\\Users\\me\\documents\\report.docx", "hello world"):
            assert sg.contains_secret(text) is False, text


# ====================================================================
# 3. 香农熵
# ====================================================================
class TestEntropy:
    def test_empty_is_zero(self):
        assert sg.shannon_entropy("") == 0.0

    def test_single_repeated_char_is_zero(self):
        assert sg.shannon_entropy("aaaaaaaa") == 0.0

    def test_high_entropy_above_threshold(self):
        assert sg.shannon_entropy(ENTROPY) >= sg.ENTROPY_MIN_BITS

    def test_entropy_is_bits_per_char(self):
        # 两个等概率字符 → 每字符 1 bit
        assert sg.shannon_entropy("ab") == pytest.approx(1.0)


# ====================================================================
# 4. 遮蔽
# ====================================================================
class TestMask:
    def test_mask_replaces_secret_keeps_context(self):
        text = f"我的 key 是 {SK}"
        masked = sg.mask_text(text)
        assert SK not in masked
        assert sg.MASK_PLACEHOLDER in masked
        assert "我的 key 是" in masked

    def test_mask_multiple_secrets(self):
        text = f"{GH} 和 {SK}"
        masked = sg.mask_text(text)
        assert GH not in masked
        assert SK not in masked
        assert masked.count(sg.MASK_PLACEHOLDER) == 2

    def test_mask_no_hit_returns_original(self):
        text = "普通文本，没有凭证"
        assert sg.mask_text(text) == text


# ====================================================================
# 5. 处置收敛
# ====================================================================
class TestSanitizeMode:
    @pytest.mark.parametrize("value", ["deny", "mask", "allow"])
    def test_valid_modes_pass(self, value):
        assert sg.sanitize_mode(value) == value

    @pytest.mark.parametrize("value", ["bogus", "", None, 123, [], "MASK"])
    def test_invalid_falls_back_to_default(self, value):
        assert sg.sanitize_mode(value) == sg.DEFAULT_MODE

    def test_default_mode_is_mask(self):
        # 默认遮蔽 = 命中先保守落占位，绝不静默丢弃原文
        assert sg.DEFAULT_MODE == sg.MODE_MASK
        assert sg.DEFAULT_MODE in sg.GUARD_MODES


# ====================================================================
# 6. 去重与边界
# ====================================================================
class TestDedupAndEdges:
    def test_one_secret_reported_once(self):
        """JWT 同时满足 jwt 与 entropy → 只报最具体的一条。"""
        hits = sg.detect(JWT)
        assert len(hits) == 1
        assert hits[0]["rule"] == "jwt"

    def test_empty_and_none_safe(self):
        assert sg.detect("") == []
        assert sg.detect(None) == []

    def test_hits_sorted_by_position(self):
        text = f"{GH} then {SK}"
        hits = sg.detect(text)
        positions = [h["start"] for h in hits]
        assert positions == sorted(positions)

    def test_bearer_without_token_not_hit(self):
        # 只有 "Bearer " 没有 token → 不命中（避免误伤说明文字）
        assert sg.contains_secret("Authorization: Bearer") is False
