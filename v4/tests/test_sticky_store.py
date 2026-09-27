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


# ---------------- 任务锚点（kind="task"）----------------
@pytest.fixture()
def task_ids():
    """模拟 TaskManager：可增删的 task_id 集合"""
    return {"ids": {10, 20}}


@pytest.fixture()
def dual_store(tmp_path, note_ids, task_ids):
    s = StickyStore(str(tmp_path / "stickies.json"),
                    note_ids=lambda: note_ids["ids"],
                    task_ids=lambda: task_ids["ids"])
    yield s


def test_default_kind_is_note(dual_store):
    """不传 kind → 兼容为笔记便签（旧调用路径零改动）"""
    rec = dual_store.upsert(1, 0, 0, 280, 200)
    assert rec.kind == "note"
    assert dual_store.get_by_anchor("note", 1) is rec
    assert dual_store.get_by_anchor("task", 1) is None


def test_task_kind_roundtrip(tmp_path, note_ids, task_ids):
    """task 便签：独立 id 空间，重启后 kind 与几何一致"""
    p = str(tmp_path / "s.json")
    s1 = StickyStore(p, note_ids=lambda: note_ids["ids"],
                     task_ids=lambda: task_ids["ids"])
    rec = s1.upsert(10, 11, 22, 280, 200, kind="task")
    assert rec.kind == "task"
    s2 = StickyStore(p, note_ids=lambda: note_ids["ids"],
                     task_ids=lambda: task_ids["ids"])
    got = s2.get_by_anchor("task", 10)
    assert got is not None and got.kind == "task"
    assert (got.x, got.y) == (11, 22)
    # 同一数字 id 在两种 kind 下互不串扰
    assert s2.get_by_anchor("note", 10) is None


def test_same_id_two_kinds_coexist(dual_store):
    """note_id=1 与 task_id=1 可同时各钉一条（kind 隔离 id 空间）"""
    n = dual_store.upsert(1, 0, 0, 280, 200)
    t = dual_store.upsert(1, 30, 30, 280, 200, kind="task")
    assert n.sticky_id != t.sticky_id
    assert dual_store.count() == 2
    assert dual_store.get_by_anchor("task", 1) is t


def test_task_orphan_purged(dual_store, task_ids):
    """任务被删后其便签记录按 kind 精确清理，笔记记录不受影响"""
    dual_store.upsert(1, 0, 0, 280, 200)                      # note
    dual_store.upsert(10, 0, 0, 280, 200, kind="task")        # task
    task_ids["ids"].discard(10)
    assert dual_store.purge_orphans() == 1
    assert dual_store.count() == 1
    assert dual_store.get_by_anchor("note", 1) is not None
    assert dual_store.get_by_anchor("task", 10) is None


def test_task_orphan_purged_without_task_ids(tmp_path, note_ids):
    """store 不支持任务（task_ids=None）→ 存量 task 记录加载即清"""
    p = str(tmp_path / "s.json")
    data = {"stickies": [{
        "sticky_id": 1, "note_id": 10, "x": 0, "y": 0,
        "w": 280, "h": 200, "kind": "task",
    }], "next_id": 2}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    s = StickyStore(p, note_ids=lambda: note_ids["ids"])   # 无 task_ids
    assert s.count() == 0


def test_kind_invalid_falls_back_to_note():
    rec = Sticky.from_dict({"sticky_id": 1, "note_id": 5,
                            "kind": "bogus"})
    assert rec.kind == "note"


def test_legacy_record_without_kind_is_note(tmp_path, note_ids):
    """旧版 stickies.json（无 kind 键）加载后语义不变"""
    p = str(tmp_path / "s.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"stickies": [{
            "sticky_id": 1, "note_id": 2, "x": 5, "y": 6,
            "w": 280, "h": 200,
        }], "next_id": 2}, f)
    s = StickyStore(p, note_ids=lambda: note_ids["ids"])
    rec = s.get_by_note_id(2)
    assert rec is not None and rec.kind == "note"
