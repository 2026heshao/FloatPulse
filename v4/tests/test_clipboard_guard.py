# -*- coding: utf-8 -*-
"""凭证哨兵写入路径拦截（clipboard_monitor + fragments_panel）集成回归。

沿用 test_clipboard_monitor 的替身配方：不碰真实剪贴板，dataChanged 手动
驱动，前台进程名 monkeypatch 为空串。本文件专测 clipboard-guard：

  1. 默认关闭 = 与既有行为逐项等价（护栏 §1）
  2. 命中三种处置：mask / deny / allow
  3. 白名单放行（commit SHA / UUID / 数字 ID 不落拦截）
  4. 「本次记住」只影响本进程、不写持久配置
  5. 待办队列 + 回溯改判 resolve_guard
  6. 审核计数器（凭证进明文池次数）
"""
import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

import src.clipboard_monitor as clm  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src import secret_guard as sg  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402


GH = "ghp_" + "a1B2c3D4" * 5
SK = "sk-" + "Xy9zAb2C" * 5
JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
SHA40 = "a3f9c2e1b4d6a8f0c2e4b6d8a0f2c4e6b8d0a2f4"
UUID = "550e8400-e29b-41d4-a716-446655440000"


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
    dataChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._text = ""

    def text(self):
        return self._text

    def mimeData(self):
        return FakeMimeData()

    def setText(self, t):
        self._text = t
        self.dataChanged.emit()


class FakeConfig:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def reset_audit():
    """每个测试前清空进程级审核计数，避免相互污染。"""
    ClipboardMonitor._audit.update(
        {"plain": 0, "masked": 0, "denied": 0, "allowed": 0})
    yield


def make_monitor(monkeypatch, *, config=None):
    clip = FakeClipboard()
    monkeypatch.setattr(clm, "QApplication", _StubQApp)
    monkeypatch.setattr(_StubQApp, "_clip", clip)
    monkeypatch.setattr(clm, "_get_foreground_process_name", lambda: "")
    fm = FragmentManager(":memory:")
    mon = ClipboardMonitor(fm, config_manager=config)
    mon.start()
    events = []
    mon.secret_guarded.connect(events.append)
    return mon, clip, fm, events


def paste_text(clip, text):
    clip._text = text
    clip.dataChanged.emit()


def guard_cfg(mode="mask", enabled=True):
    return FakeConfig({"clipboard_guard_enabled": enabled,
                       "clipboard_guard_mode": mode})


# ====================================================================
# 1. 默认关闭 = 既有行为等价
# ====================================================================
class TestDefaultOff:
    def test_disabled_by_default_no_config(self, qapp, monkeypatch):
        """未注入配置 → 不拦截，凭证照常落盘（与旧行为等价）。"""
        mon, clip, fm, events = make_monitor(monkeypatch, config=None)
        paste_text(clip, GH)
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == GH
        assert events == []
        assert ClipboardMonitor.guard_audit()["masked"] == 0

    def test_explicit_false_config_equivalent_to_old(self, qapp, monkeypatch):
        """enabled=False → 逐项等价于无哨兵。"""
        cfg = FakeConfig({"clipboard_guard_enabled": False})
        mon, clip, fm, events = make_monitor(monkeypatch, config=cfg)
        paste_text(clip, GH)
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == GH
        assert events == []

    def test_normal_text_unaffected_when_enabled(self, qapp, monkeypatch):
        """开启哨兵但内容是普通文本 → 原样落盘。"""
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        paste_text(clip, "普通文本，没有凭证")
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == "普通文本，没有凭证"
        assert events == []


# ====================================================================
# 2. 三种处置
# ====================================================================
class TestModes:
    def test_mask_mode_stores_placeholder(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="mask"))
        paste_text(clip, f"我的 key 是 {SK}")
        assert fm.count() == 1
        content = fm.get_all_fragments()[0].content
        assert SK not in content                 # 原文没进明文池
        assert sg.MASK_PLACEHOLDER in content
        assert "我的 key 是" in content           # 上下文保留，用户看得出是哪条
        assert len(events) == 1
        assert events[0]["mode"] == "mask"
        assert ClipboardMonitor.guard_audit()["masked"] == 1

    def test_deny_mode_does_not_store(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="deny"))
        paste_text(clip, GH)
        assert fm.count() == 0                    # 整条不落盘
        assert len(events) == 1                   # 但通报 UI（非静默删除）
        assert events[0]["mode"] == "deny"
        assert ClipboardMonitor.guard_audit()["denied"] == 1

    def test_allow_mode_stores_plaintext_once(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="allow"))
        paste_text(clip, GH)
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == GH   # 本次记录原文
        assert events[0]["mode"] == "allow"
        assert ClipboardMonitor.guard_audit()["allowed"] == 1

    def test_invalid_mode_falls_back_to_mask(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="bogus"))
        paste_text(clip, SK)
        content = fm.get_all_fragments()[0].content
        assert SK not in content
        assert sg.MASK_PLACEHOLDER in content


# ====================================================================
# 3. 白名单放行
# ====================================================================
class TestWhitelistIntegration:
    @pytest.mark.parametrize("text", [
        SHA40, UUID, "12345678901234567890123456789012",
        f"commit {SHA40}", f"rebase {SHA40}",
    ])
    def test_whitelisted_passes_through(self, qapp, monkeypatch, text):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        paste_text(clip, text)
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == text  # 原文原样
        assert events == []
        assert ClipboardMonitor.guard_audit()["masked"] == 0


# ====================================================================
# 4. 「本次记住」（不写持久配置）
# ====================================================================
class TestRememberThisSession:
    def test_remember_overrides_config_without_writing(self, qapp, monkeypatch):
        cfg = guard_cfg(mode="mask")
        mon, clip, fm, events = make_monitor(monkeypatch, config=cfg)
        mon.remember_guard_mode("allow")
        paste_text(clip, GH)
        assert fm.get_all_fragments()[0].content == GH    # 本次记住生效
        # 未回写配置（仅本进程）
        assert cfg.get("clipboard_guard_enabled") is True
        assert cfg.get("clipboard_guard_mode") == "mask"

    def test_remember_survives_across_pastes(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        mon.remember_guard_mode("deny")
        paste_text(clip, GH)
        assert fm.count() == 0
        paste_text(clip, SK)
        assert fm.count() == 0                            # 仍按记住的 deny


# ====================================================================
# 5. 待办队列 + 回溯改判
# ====================================================================
class TestQueueAndResolve:
    def test_queue_records_hits(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        paste_text(clip, GH)
        paste_text(clip, SK)
        assert mon.guard_pending_count() == 2

    def test_resolve_allow_restores_plaintext(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        paste_text(clip, GH)
        assert mon.guard_pending_count() == 1
        assert mon.resolve_guard(0, "allow") is True
        assert mon.guard_pending_count() == 0
        # 同一条碎片被改回原文
        contents = [f.content for f in fm.get_all_fragments()]
        assert GH in contents

    def test_resolve_deny_deletes_fragment(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        paste_text(clip, GH)
        assert fm.count() == 1
        assert mon.resolve_guard(0, "deny") is True
        assert fm.count() == 0

    def test_resolve_mask_keeps_placeholder(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="allow"))
        paste_text(clip, GH)
        assert fm.get_all_fragments()[0].content == GH
        assert mon.resolve_guard(0, "mask") is True
        assert sg.MASK_PLACEHOLDER in fm.get_all_fragments()[0].content

    def test_resolve_allow_from_denied_writes_back(self, qapp, monkeypatch):
        """deny 模式下被丢弃的条目，回溯选 allow 应补写回池。"""
        mon, clip, fm, events = make_monitor(
            monkeypatch, config=guard_cfg(mode="deny"))
        paste_text(clip, GH)
        assert fm.count() == 0
        assert mon.resolve_guard(0, "allow") is True
        assert fm.count() == 1
        assert fm.get_all_fragments()[0].content == GH

    def test_resolve_bad_index_returns_false(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        assert mon.resolve_guard(0, "allow") is False
        paste_text(clip, GH)
        assert mon.resolve_guard(5, "allow") is False


# ====================================================================
# 6. 队列有界
# ====================================================================
class TestQueueBound:
    def test_queue_capped(self, qapp, monkeypatch):
        mon, clip, fm, events = make_monitor(monkeypatch, config=guard_cfg())
        monkeypatch.setattr(ClipboardMonitor, "GUARD_QUEUE_MAX", 3)
        for i in range(6):
            paste_text(clip, f"{GH}{i}")     # 内容各不相同，避免去重
        assert mon.guard_pending_count() <= 3


# ====================================================================
# 7. 性能：落盘延迟增量 < 5ms
# ====================================================================
class TestPerformance:
    def test_latency_increment_under_5ms(self, qapp, monkeypatch):
        """量「复制 → 落盘」延迟增量：长文本（无命中）过哨兵的开销。

        开关关闭为基线，开启后为带哨兵；差值即延迟增量，须 < 5ms。
        """
        long_text = ("这是一段很长的普通文本，用来说明性能。 " * 60).strip()
        assert not sg.contains_secret(long_text)

        def measure(config):
            mon, clip, fm, events = make_monitor(monkeypatch, config=config)
            # 预热
            for _ in range(3):
                paste_text(clip, "warmup-%d" % time.perf_counter_ns())
            t0 = time.perf_counter()
            n = 200
            for i in range(n):
                paste_text(clip, f"{long_text}-{i}")
            return (time.perf_counter() - t0) / n * 1000.0   # ms/次

        base = measure(FakeConfig({"clipboard_guard_enabled": False}))
        armed = measure(guard_cfg())
        delta = armed - base
        # 记录实测值，便于人工核对（pytest -s 可见）
        print(f"\nlatency base={base:.4f}ms armed={armed:.4f}ms "
              f"delta={delta:.4f}ms")
        assert delta < 5.0, f"延迟增量 {delta:.3f}ms 超过 5ms 阈值"
