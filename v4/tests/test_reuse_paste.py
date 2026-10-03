# -*- coding: utf-8 -*-
"""
一键粘回（reuse 卡）单测 —— 覆盖两块纯逻辑：

  A. ``src/paste_helper.py``：焦点记录 / 还原 / 发键 / 降级 全链路
     （全部用替身注入，不依赖真 Windows、不发真按键）。
  B. ``Fragment.pinned`` 字段零迁移 + ``FragmentManager`` 的置顶排序 /
     淘汰豁免 / 置顶开关（`set_pinned` / `toggle_pinned`）。

反向验证点（任务卡 §4 必做）：
  · ``from_dict`` 的 pinned 默认值改成 True → ``test_from_dict_missing_pinned_defaults_false`` 红
  · ``trim_to_max`` 不含置顶豁免 → ``test_trim_keeps_pinned`` 红
  · ``paste_to`` 把还原顺序颠倒 / 不校验窗口 → 对应用例红
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import paste_helper as ph  # noqa: E402
from src.paste_helper import (  # noqa: E402
    REASON_NOT_WINDOWS, REASON_NO_FOREGROUND, REASON_RESTORE_FAILED,
    REASON_SEND_FAILED, PasteHelper, ReuseCounter,
)
from src.fragment_manager import Fragment, FragmentManager  # noqa: E402


@pytest.fixture
def no_backend(monkeypatch):
    """强制探测不到平台后端（模拟非 Windows / API 缺失）。"""
    monkeypatch.setattr(ph, "_get_apis", lambda: None)


# ====================================================================
# 替身：可控的 Win32 后端
# ====================================================================
class _FakeApis:
    def __init__(self, foreground=1234, is_window=True, set_ok=True,
                 send_ok=True):
        self._foreground = foreground
        self._is_window = is_window
        self._set_ok = set_ok
        self._send_ok = send_ok
        self.calls = []          # 记录调用顺序，供顺序断言

    def get_foreground_window(self):
        self.calls.append("get")
        return self._foreground

    def is_window(self, hwnd):
        self.calls.append(("is_window", hwnd))
        return self._is_window

    def set_foreground_window(self, hwnd):
        self.calls.append(("set", hwnd))
        return self._set_ok

    def send_ctrl_v(self):
        self.calls.append("send")
        return self._send_ok


def _helper(apis, **kw):
    return PasteHelper(apis=apis, sleep=lambda _s: None, **kw)


# ====================================================================
# A. PasteHelper —— 成功路径
# ====================================================================
def test_capture_foreground_returns_handle():
    apis = _FakeApis(foreground=4242)
    assert _helper(apis).capture_foreground() == 4242


def test_capture_foreground_zero_when_no_apis(no_backend):
    assert PasteHelper().capture_foreground() == 0


def test_paste_to_success_order_is_capture_set_send():
    """成功路径：先校验窗口 → 还原焦点 → 发键（顺序不能乱）。"""
    apis = _FakeApis(foreground=99)
    ok, reason = _helper(apis).paste_to(99)
    assert ok is True and reason is None
    # 顺序：is_window(校验) 在 set(还原) 之前，set 在 send 之前
    assert apis.calls[0] == ("is_window", 99)
    assert apis.calls[1] == ("set", 99)
    assert apis.calls[2] == "send"


def test_paste_to_settles_before_send():
    """还原焦点后必须等窗口激活再发键（避免落到旧目标）。"""
    slept = []
    apis = _FakeApis()
    PasteHelper(apis=apis, sleep=lambda s: slept.append(s)).paste_to(7)
    assert slept and slept[0] > 0


# ====================================================================
# A. PasteHelper —— 降级 / 失败路径（不抛异常）
# ====================================================================
def test_paste_to_not_windows_degrades(no_backend):
    ok, reason = PasteHelper().paste_to(1)
    assert ok is False and reason == REASON_NOT_WINDOWS


def test_paste_to_zero_hwnd_degrades():
    apis = _FakeApis()
    ok, reason = _helper(apis).paste_to(0)
    assert ok is False and reason == REASON_NO_FOREGROUND
    # 无效句柄不得尝试还原焦点
    assert not any(call == "set" or isinstance(call, tuple) and call[0] == "set"
                   for call in apis.calls)


def test_paste_to_dead_window_degrades():
    """窗口句柄无效（IsWindow=False）→ 不还原、不发键。"""
    apis = _FakeApis(is_window=False)
    ok, reason = _helper(apis).paste_to(555)
    assert ok is False and reason == REASON_NO_FOREGROUND
    assert "send" not in apis.calls


def test_paste_to_restore_failed_degrades():
    apis = _FakeApis(set_ok=False)
    ok, reason = _helper(apis).paste_to(1)
    assert ok is False and reason == REASON_RESTORE_FAILED
    assert "send" not in apis.calls


def test_paste_to_send_failed_degrades():
    apis = _FakeApis(send_ok=False)
    ok, reason = _helper(apis).paste_to(1)
    assert ok is False and reason == REASON_SEND_FAILED


def test_paste_to_never_raises_on_api_exception():
    """后端抛异常也不得把异常泄漏给调用方。"""
    class _Boom(_FakeApis):
        def set_foreground_window(self, hwnd):
            raise RuntimeError("boom")

    ok, reason = _helper(_Boom()).paste_to(1)
    assert ok is False and reason == REASON_SEND_FAILED


def test_available_flag_reflects_backend(no_backend):
    assert _helper(_FakeApis()).available is True
    assert PasteHelper().available is False


# ====================================================================
# B. ReuseCounter —— 重复复制计数埋点
# ====================================================================
def test_counter_first_copy_is_one():
    c = ReuseCounter()
    assert c.record("hello") == 1
    assert c.duplicate_total() == 0


def test_counter_counts_duplicates():
    c = ReuseCounter()
    c.record("hello")
    assert c.record("hello") == 2
    assert c.record("hello") == 3
    assert c.duplicate_total() == 2
    assert c.count_of("hello") == 3


def test_counter_distinguishes_contents():
    c = ReuseCounter()
    c.record("a")
    c.record("b")
    c.record("b")
    assert c.unique_contents() == 2
    assert c.repeated_contents() == 1
    assert c.duplicate_total() == 1


def test_counter_none_safe():
    c = ReuseCounter()
    assert c.record(None) == 1
    assert c.count_of(None) == 1


def test_counter_reset():
    c = ReuseCounter()
    c.record("x")
    c.record("x")
    c.reset()
    assert c.count_of("x") == 0 and c.duplicate_total() == 0


# ====================================================================
# C. Fragment.pinned —— 零迁移 + 序列化
# ====================================================================
def test_new_fragment_pinned_defaults_false():
    f = Fragment(1, "clipboard_text", "a", "s", "2026-10-03 09:00")
    assert f.pinned is False


def test_from_dict_missing_pinned_defaults_false():
    """老 fragments.json 无 pinned 键 → False（零迁移核心断言）。"""
    f = Fragment.from_dict({
        "fragment_id": 1, "type": "clipboard_text", "content": "a",
        "source": "s", "created_at": "2026-10-03 09:00", "category": "text",
    })
    assert f.pinned is False


def test_from_dict_reads_pinned_true():
    f = Fragment.from_dict({"fragment_id": 1, "pinned": True})
    assert f.pinned is True


def test_from_dict_pinned_dirty_value_coerced():
    f = Fragment.from_dict({"fragment_id": 1, "pinned": "yes"})
    assert f.pinned is True
    f2 = Fragment.from_dict({"fragment_id": 2, "pinned": 0})
    assert f2.pinned is False


def test_to_dict_roundtrip_pinned():
    f = Fragment(1, "clipboard_text", "a", "s", "2026-10-03 09:00",
                 pinned=True)
    d = f.to_dict()
    assert d["pinned"] is True
    assert Fragment.from_dict(d).pinned is True


def test_to_dict_has_eight_keys():
    """键集**恰好**这 8 个 —— 兼作「schema 无意漂移」的闸门。

    本断言在 2026-10-03 第 6 卡（inbox-triage）加 ``hit_count`` 时如实变红
    （7 → 8），是护栏按设计工作的证据，不是误报。日后若再加字段，同样应当
    在这里显式扩容 —— 而不是让 to_dict 静默多出键。
    """
    f = Fragment(1, "clipboard_text", "a", "s", "2026-10-03 09:00")
    assert set(f.to_dict().keys()) == {
        "fragment_id", "type", "content", "source", "created_at",
        "category", "pinned", "hit_count"}


# ====================================================================
# D. FragmentManager —— 置顶开关 / 排序 / 淘汰豁免
# ====================================================================
def _manager(tmp_path):
    return FragmentManager(str(tmp_path / "fragments.json"))


def test_set_pinned_and_toggle(tmp_path):
    m = _manager(tmp_path)
    fid = m.add_clipboard_text("内容A")
    assert m.set_pinned(fid, True) is True
    assert m.get_fragment(fid).pinned is True
    # 无变化 → False，不重复写
    assert m.set_pinned(fid, True) is False
    assert m.toggle_pinned(fid) is False
    assert m.get_fragment(fid).pinned is False


def test_set_pinned_missing_id_returns_false(tmp_path):
    m = _manager(tmp_path)
    assert m.set_pinned(999, True) is False


def test_get_pinned_and_count(tmp_path):
    m = _manager(tmp_path)
    a = m.add_clipboard_text("A")
    b = m.add_clipboard_text("B")
    m.set_pinned(b, True)
    assert [f.fragment_id for f in m.get_pinned_fragments()] == [b]
    assert m.pinned_count() == 1
    assert a != b


def test_pinned_sorted_first(tmp_path):
    """置顶碎片排在最前，即使它更老。"""
    m = _manager(tmp_path)
    old = m.add_clipboard_text("老内容")
    m.set_pinned(old, True)
    for i in range(3):
        m.add_clipboard_text("新内容%d" % i)
    assert m.get_all_fragments()[0].fragment_id == old


def test_trim_keeps_pinned(tmp_path):
    """淘汰必须放过置顶碎片（核心护栏）。"""
    m = _manager(tmp_path)
    keep = m.add_clipboard_text("要保留的置顶")
    m.set_pinned(keep, True)
    # 再加 5 条，置顶的是最早的一条 —— 若不含豁免它必被淘汰
    for i in range(5):
        m.add_clipboard_text("普通%d" % i)
    assert m.count() == 6
    removed = m.trim_to_max(3)
    assert removed == 3
    assert m.get_fragment(keep) is not None, "置顶碎片不应被淘汰"
    assert m.count() == 3


def test_trim_when_all_pinned_removes_nothing(tmp_path):
    m = _manager(tmp_path)
    for i in range(3):
        fid = m.add_clipboard_text("钉住%d" % i)
        m.set_pinned(fid, True)
    assert m.trim_to_max(1) == 0
    assert m.count() == 3


def test_trim_without_pinned_unchanged_behavior(tmp_path):
    """无置顶时淘汰行为与改动前一致（FIFO 最早先走）。"""
    m = _manager(tmp_path)
    ids = [m.add_clipboard_text("内容%d" % i) for i in range(5)]
    removed = m.trim_to_max(2)
    assert removed == 3
    left = {f.fragment_id for f in m.get_all_fragments()}
    assert left == {ids[-1], ids[-2]}


def test_pinned_persists_roundtrip(tmp_path):
    """置顶状态落盘后重载仍在。"""
    path = str(tmp_path / "fragments.json")
    m = FragmentManager(path)
    fid = m.add_clipboard_text("持久化")
    m.set_pinned(fid, True)
    m.flush()
    m2 = FragmentManager(path)
    assert m2.get_fragment(fid).pinned is True
