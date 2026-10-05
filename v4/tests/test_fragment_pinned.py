# -*- coding: utf-8 -*-
"""
碎片置顶（原 reuse 卡保留部分）单测 —— 纯逻辑覆盖：

  · ``Fragment.pinned`` 字段零迁移（老 fragments.json 无键 → False）
  · ``FragmentManager`` 的置顶排序 / 淘汰豁免 / 置顶开关
    （`set_pinned` / `toggle_pinned`）

历史注：本文件原名 ``test_reuse_paste.py``，还覆盖「一键粘回」的
焦点记录 / 还原 / 发键执行器全链路。粘回功能已于 2026-10-05 经用户
拍板整体删除（真机不可靠 + 无区分反馈），相关用例随底层模块一起
移除；置顶与淘汰豁免是同一张卡里独立成立的能力，保留在此。

反向验证点（任务卡 §4 必做）：
  · ``from_dict`` 的 pinned 默认值改成 True → ``test_from_dict_missing_pinned_defaults_false`` 红
  · ``trim_to_max`` 不含置顶豁免 → ``test_trim_keeps_pinned`` 红
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fragment_manager import Fragment, FragmentManager  # noqa: E402


# ====================================================================
# Fragment.pinned —— 零迁移 + 序列化
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
