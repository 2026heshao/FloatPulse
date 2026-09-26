# -*- coding: utf-8 -*-
"""StickyStore 纯逻辑回归（不依赖 GUI Widgets，QObject 均不创建）。

覆盖：增删改查 / note_id 唯一性 / 重复 upsert 复用同一记录 /
孤儿清理（删笔记后对应 sticky 被清）/ stickies.json 损坏备份回退 /
几何写入读回一致 / Sticky.from_dict 非法值收敛。
数量上限（MAX_STICKIES 拒绝）属窗口生命周期，由离屏脚本
tools/verify_sticky_notes.py 驱动真实 open() 路径覆盖。
"""
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.sticky_notes import Sticky, StickyStore


@pytest.fixture()
def note_ids():
    """模拟 NoteManager：可增删的 note_id 集合"""
    return {"ids": {1, 2, 3}}


@pytest.fixture()
def store(tmp_path, note_ids):
    s = StickyStore(str(tmp_path / "stickies.json"),
                    note_ids=lambda: note_ids["ids"])
    yield s
    # 无需清理：tmp_path 由 pytest 回收


# ---------------- 增删改查 ----------------
def test_upsert_new_and_read_back(store):
    rec = store.upsert(1, 10, 20, 280, 200)
    assert store.count() == 1
    got = store.get(rec.sticky_id)
    assert (got.x, got.y, got.w, got.h) == (10, 20, 280, 200)
    assert got.note_id == 1


def test_upsert_by_note_id_reuses_record(store):
    """同一 note_id 重复 upsert → 复用同一条记录（唯一性）"""
    first = store.upsert(1, 10, 20, 280, 200)
    second = store.upsert(1, 50, 60, 300, 240)
    assert first.sticky_id == second.sticky_id
    assert store.count() == 1
    assert (second.x, second.y) == (50, 60)


def test_remove_and_remove_by_note(store):
    rec = store.upsert(1, 0, 0, 280, 200)
    assert store.remove_by_note_id(999) is False
    assert store.remove(rec.sticky_id) is True
    assert store.count() == 0
    assert store.remove(rec.sticky_id) is False


def test_clear_all(store):
    for nid in (1, 2, 3):
        store.upsert(nid, 0, 0, 280, 200)
    store.clear_all()
    assert store.count() == 0
    assert store.get_all() == []


def test_get_by_note_id(store):
    store.upsert(2, 5, 6, 280, 200)
    assert store.get_by_note_id(2) is not None
    assert store.get_by_note_id(99) is None


# ---------------- 持久化与几何读回一致 ----------------
def test_geometry_roundtrip(tmp_path, note_ids):
    store = StickyStore(str(tmp_path / "s.json"), note_ids=lambda: note_ids["ids"])
    store.upsert(1, 111, 222, 333, 444, opacity=60, always_on_top=False)
    # 重新加载（模拟重启）：几何逐字段一致
    store2 = StickyStore(str(tmp_path / "s.json"), note_ids=lambda: note_ids["ids"])
    rec = store2.get_by_note_id(1)
    assert rec is not None
    assert (rec.x, rec.y, rec.w, rec.h) == (111, 222, 333, 444)
    assert rec.opacity == 60 and rec.always_on_top is False


def test_opacity_clamped(store):
    rec = store.upsert(1, 0, 0, 280, 200, opacity=5)
    assert rec.opacity == 10          # 下界收敛
    rec2 = store.upsert(1, 0, 0, 280, 200, opacity=500)
    assert rec2.opacity == 100        # 上界收敛


# ---------------- 孤儿清理 ----------------
def test_orphan_purged_on_load(tmp_path, note_ids):
    """note_id 已不存在的记录在加载时被直接丢弃"""
    store = StickyStore(str(tmp_path / "s.json"), note_ids=lambda: note_ids["ids"])
    store.upsert(1, 0, 0, 280, 200)
    store.upsert(2, 8, 8, 280, 200)
    assert store.count() == 2
    note_ids["ids"].discard(1)        # 笔记 1 被删
    store2 = StickyStore(str(tmp_path / "s.json"), note_ids=lambda: note_ids["ids"])
    assert store2.count() == 1
    assert store2.get_by_note_id(1) is None
    assert store2.get_by_note_id(2) is not None


def test_purge_orphans_runtime(store, note_ids):
    store.upsert(3, 0, 0, 280, 200)
    note_ids["ids"].discard(3)
    assert store.purge_orphans() == 1
    assert store.count() == 0


# ---------------- 损坏文件回退 ----------------
def test_corrupt_json_backed_up_not_crash(tmp_path, note_ids):
    """json 损坏 → 备份原文件后从空开始，其他数据不受影响"""
    path = str(tmp_path / "s.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{this is not json")
    store = StickyStore(path, note_ids=lambda: note_ids["ids"])
    assert store.count() == 0         # 不崩溃、空数据
    # 备份文件存在（json_store 骨架行为）
    backups = [f for f in os.listdir(tmp_path) if f != "s.json"]
    assert backups, "损坏文件应被备份"
    # 且随后可正常写入
    store.upsert(1, 0, 0, 280, 200)
    assert store.count() == 1


def test_corrupt_toplevel_not_dict(tmp_path, note_ids):
    path = str(tmp_path / "s.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)       # 顶层不是 dict
    store = StickyStore(path, note_ids=lambda: note_ids["ids"])
    assert store.count() == 0


# ---------------- from_dict 非法值收敛 ----------------
def test_from_dict_invalid_values():
    rec = Sticky.from_dict({
        "sticky_id": "abc", "note_id": None,
        "x": "x", "y": None, "w": -5, "h": 0,
        "opacity": "oops", "always_on_top": 1,
    })
    assert rec.sticky_id == 0
    assert rec.note_id == 0
    assert rec.w >= 160 and rec.h >= 120     # 尺寸夹到安全下界
    assert 10 <= rec.opacity <= 100
    assert rec.always_on_top is True


def test_missing_file_starts_empty(tmp_path, note_ids):
    store = StickyStore(str(tmp_path / "none.json"), note_ids=lambda: note_ids["ids"])
    assert store.count() == 0
    assert store.get_all() == []
