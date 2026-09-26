# -*- coding: utf-8 -*-
"""
====================================================================
数据层加载骨架「行为钉死」测试
====================================================================
用途：为 B1 重构（把 5 个管理器里逐字重复的 `_load()` 抽成公共
`load_records()`）提供行为护栏。

  * 重构**前**：这些断言描述当前实现的既有行为
  * 重构**后**：必须逐条仍然通过（一条不过即回退该项改动）

覆盖：文件缺失 / 顶层结构损坏 / JSON 损坏 / next_id 类型收敛 /
next_id 下界修正 / 原子写不留 .tmp
====================================================================
"""

import json
import os
import shutil
import tempfile
import unittest

from src.fragment_manager import FragmentManager
from src.nav_manager import NavManager
from src.note_manager import NoteManager
from src.task_manager import TaskManager
from src.temp_asset_manager import TempAssetManager


class _LoadBehaviourMixin:
    """公共断言集。子类需提供 `_make()` 与 `records_attr` / `records_key`。"""

    records_attr = ""          # manager 上的记录列表属性名
    records_key = ""           # JSON 里的记录数组键名
    next_id_attr = "_next_id"

    def _make(self):
        raise NotImplementedError

    def _add_one(self, manager):
        """新增一条记录并返回其 id；不支持时返回 None（跳过相关断言）"""
        return None

    def _flush(self, manager):
        """把去抖写盘刷到磁盘（FragmentManager 用 _DebouncedSaver，需显式 flush）"""
        return None

    # ---------------- 工具 ----------------
    def _read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, path, obj):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _backup_exists(self, path):
        d = os.path.dirname(path)
        base = os.path.basename(path)
        return any(n != base and n.startswith(base)
                   for n in os.listdir(d) if n.endswith(".json") or "." in n)

    # ---------------- 用例 ----------------
    def test_a_missing_file_gives_empty_and_next_id_1(self):
        m = self._make()
        self.assertEqual(getattr(m, self.records_attr), [])
        self.assertEqual(getattr(m, self.next_id_attr), 1)

    def test_b_non_dict_toplevel_backs_up_and_empties(self):
        m = self._make()
        path = m._json_path
        self._write(path, [1, 2, 3])          # 顶层是 list，非法
        m2 = self._make()
        self.assertEqual(getattr(m2, self.records_attr), [])
        self.assertEqual(getattr(m2, self.next_id_attr), 1)
        self.assertTrue(self._backup_exists(path), "损坏文件应被备份")

    def test_c_corrupt_json_backs_up_and_empties(self):
        m = self._make()
        path = m._json_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 这不是合法 json")
        m2 = self._make()
        self.assertEqual(getattr(m2, self.records_attr), [])
        self.assertEqual(getattr(m2, self.next_id_attr), 1)
        self.assertTrue(self._backup_exists(path), "损坏文件应被备份")

    def test_d_next_id_string_is_coerced_to_int(self):
        m = self._make()
        path = m._json_path
        self._write(path, {self.records_key: [], "next_id": "abc"})
        m2 = self._make()
        self.assertIsInstance(getattr(m2, self.next_id_attr), int)
        self.assertGreaterEqual(getattr(m2, self.next_id_attr), 1)

    def test_e_next_id_zero_is_raised_to_at_least_1(self):
        m = self._make()
        path = m._json_path
        self._write(path, {self.records_key: [], "next_id": 0})
        m2 = self._make()
        self.assertEqual(getattr(m2, self.next_id_attr), 1)

    def test_f_next_id_below_existing_max_is_raised(self):
        m = self._make()
        new_id = self._add_one(m)
        if new_id is None:
            self.skipTest("该管理器不支持新增记录")
        self._flush(m)
        path = m._json_path
        data = self._read(path)
        data["next_id"] = 0                     # 人为压低，制造 id 冲突
        self._write(path, data)
        m2 = self._make()
        self.assertGreater(getattr(m2, self.next_id_attr), new_id,
                           "next_id 必须被抬到大于已有记录 id")

    def test_g_save_leaves_no_tmp_file(self):
        m = self._make()
        if self._add_one(m) is None:
            self.skipTest("该管理器不支持新增记录")
        self._flush(m)
        self.assertFalse(os.path.exists(m._json_path + ".tmp"),
                         "原子写不应残留 .tmp")

    def test_h_roundtrip_after_reload(self):
        m = self._make()
        if self._add_one(m) is None:
            self.skipTest("该管理器不支持新增记录")
        self._flush(m)
        m2 = self._make()
        self.assertEqual(len(getattr(m2, self.records_attr)), 1)
        self.assertGreaterEqual(getattr(m2, self.next_id_attr), 2)


class TestFragmentLoadBehaviour(_LoadBehaviourMixin, unittest.TestCase):
    records_attr = "_fragments"
    records_key = "fragments"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_guard_frag_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self):
        return FragmentManager(os.path.join(self.dir, "fragments.json"))

    def _add_one(self, manager):
        return manager.add_fragment("text", "护栏内容A")

    def _flush(self, manager):
        manager.flush()                    # 碎片管理器带去抖写盘，需显式刷盘


class TestNoteLoadBehaviour(_LoadBehaviourMixin, unittest.TestCase):
    records_attr = "_notes"
    records_key = "notes"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_guard_note_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self):
        return NoteManager(os.path.join(self.dir, "notes.json"))

    def _add_one(self, manager):
        return manager.add_note("护栏笔记内容")


class TestTaskLoadBehaviour(_LoadBehaviourMixin, unittest.TestCase):
    records_attr = "_tasks"
    records_key = "tasks"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_guard_task_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self):
        return TaskManager(os.path.join(self.dir, "schedule.json"))

    def _add_one(self, manager):
        return manager.add_task("护栏任务", "", "2026-09-23")


class TestNavLoadBehaviour(_LoadBehaviourMixin, unittest.TestCase):
    records_attr = "_groups"
    records_key = "groups"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_guard_nav_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self):
        return NavManager(os.path.join(self.dir, "nav.json"))

    def _add_one(self, manager):
        gid = manager.add_group("护栏分组")
        manager.add_site(gid, "示例站", "https://example.com")
        return 1                                # 首个 site 的 nav_id 为 1


class TestTempAssetLoadBehaviour(_LoadBehaviourMixin, unittest.TestCase):
    records_attr = "_assets"
    records_key = "assets"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_guard_asset_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make(self):
        return TempAssetManager(self.dir)

    def _add_one(self, manager):
        return None                             # add_asset 需要真实源文件，本类不测新增路径


if __name__ == "__main__":
    unittest.main()
