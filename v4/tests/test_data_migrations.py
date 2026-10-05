# -*- coding: utf-8 -*-
"""
====================================================================
数据迁移层与滚动备份护栏测试
====================================================================
对应成熟化路线图 1.2：

  * data_backups.rotate_backup：同日幂等 / 跨日轮转 / 保留 ≤7 份 /
    文件不存在安全 / 清理不波及同目录其他前缀
  * json_store schema 版本层：显式旧版本号 → 逐级迁移；
    无版本号存量数据 → 零迁移；save_records 落盘自动带 data_version

====================================================================
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from src import data_backups, json_store
from src.data_backups import rotate_backup
from src.json_store import (MIGRATIONS, STORE_VERSIONS, STORE_VERSION_KEY,
                            load_records, migrate_data, save_records)


def _today_str() -> str:
    """当日日期（YYYYMMDD），用于拼出预期备份名"""
    return date.today().strftime("%Y%m%d")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class TestRotateBackup(unittest.TestCase):
    """data_backups.rotate_backup：写前按日滚动备份"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_rotate_")
        self.path = os.path.join(self.dir, "fragments.json")
        self.backup_dir = os.path.join(self.dir, "backups")
        data_backups.reset_rotation_state()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rewrite(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def _backup_names(self):
        return sorted(os.listdir(self.backup_dir)) \
            if os.path.isdir(self.backup_dir) else []

    def test_missing_file_is_safe(self):
        """文件不存在（首次写盘）→ 跳过，不抛异常、不建备份目录"""
        self.assertEqual(rotate_backup(self.path), "")
        self.assertEqual(rotate_backup(self.path, day="20260101"), "")
        self.assertFalse(os.path.isdir(self.backup_dir))

    def test_first_rotate_copies_current_content(self):
        """首次轮转：复制当前文件到 backups/<主干>-<YYYYMMDD>.<扩展名>"""
        self._rewrite("内容v1")
        dst = rotate_backup(self.path, day="20260101")
        self.assertEqual(dst, os.path.join(self.backup_dir,
                                           "fragments-20260101.json"))
        self.assertTrue(os.path.isfile(dst))
        with open(dst, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "内容v1")

    def test_same_day_is_idempotent(self):
        """同一天第二次起 no-op：仍只有一份，且是第一次的内容"""
        self._rewrite("内容v1")
        rotate_backup(self.path, day="20260101")
        self._rewrite("内容v2")                   # 修改后再轮转也不备
        self.assertEqual(rotate_backup(self.path, day="20260101"), "")
        self.assertEqual(self._backup_names(), ["fragments-20260101.json"])
        with open(os.path.join(self.backup_dir,
                               "fragments-20260101.json"),
                  "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "内容v1")

    def test_cross_day_rotates_new_copy(self):
        """跨日：新的一天产生新备份，两份并存"""
        self._rewrite("内容v1")
        rotate_backup(self.path, day="20260101")
        self._rewrite("内容v2")
        dst2 = rotate_backup(self.path, day="20260102")
        self.assertEqual(self._backup_names(),
                         ["fragments-20260101.json", "fragments-20260102.json"])
        with open(dst2, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "内容v2")

    def test_keeps_at_most_seven_backups(self):
        """跨 10 天轮转 → 只保留最近 7 份，最旧的被清理"""
        for i in range(1, 11):                     # 20260101 .. 20260110
            self._rewrite("内容%d" % i)
            rotate_backup(self.path, day="2026%04d" % (100 + i))
        names = self._backup_names()
        self.assertEqual(len(names), 7)
        self.assertEqual(names[0], "fragments-20260104.json")  # 前 3 份被清
        self.assertEqual(names[-1], "fragments-20260110.json")

    def test_cleanup_scoped_to_same_stem(self):
        """清理只按同文件名主干匹配，不波及同目录其他文件的备份"""
        other = os.path.join(self.backup_dir, "nav-20260101.json")
        os.makedirs(self.backup_dir, exist_ok=True)
        with open(other, "w", encoding="utf-8") as f:
            f.write("{}")
        for i in range(1, 11):
            self._rewrite("内容%d" % i)
            rotate_backup(self.path, day="2026%04d" % (100 + i))
        self.assertTrue(os.path.exists(other), "其他文件的备份不应被清理")

    def test_failure_returns_empty_without_raise(self):
        """备份失败（backups 目录位被同名文件占用）只返回空串，不抛异常"""
        self._rewrite("内容v1")
        # 占住备份目录位：os.makedirs(backups) 将抛 FileExistsError
        with open(self.backup_dir, "w", encoding="utf-8") as f:
            f.write("not a dir")
        self.assertEqual(rotate_backup(self.path, day="20260101"), "")
        # 失败不登记当日已备，下次保存仍会重试
        self.assertNotIn(os.path.abspath(self.path), data_backups._ROTATED_ON)


class TestStoreVersions(unittest.TestCase):
    """STORE_VERSIONS / MIGRATIONS 注册表"""

    def test_all_stores_registered_at_current_version(self):
        """8 个数据文件全部登记；config 与 DEFAULT_CONFIG['schema_version']
        动态同步（2026-10-03 升 v2 分组阈值默认 120→900；2026-10-05 升 v3
        新增 toast_duration_ms 键），其余 store 仍为 v1。新增迁移时同步改
        这里，别让它退化成「全都写死 1」的摆设。（asset_groups = 素材会话
        堆旁路标注，2026-10-04）"""
        from src.config import DEFAULT_CONFIG
        self.assertEqual(
            set(STORE_VERSIONS),
            {"config", "fragments", "notes", "tasks",
             "groups", "stickies", "assets", "asset_groups"})
        self.assertEqual(STORE_VERSIONS["config"],
                         DEFAULT_CONFIG.get("schema_version"),
                         "config 版本应与 DEFAULT_CONFIG['schema_version'] "
                         "同步升位")
        for store, version in STORE_VERSIONS.items():
            if store == "config":
                continue
            self.assertEqual(version, 1, "%s 应为 v1" % store)

    def test_migrations_table_covers_all_stores(self):
        """每个 store 在 MIGRATIONS 都有表（无内容迁移的 store 允许空表）"""
        self.assertEqual(set(MIGRATIONS), set(STORE_VERSIONS))
        self.assertIn(1, MIGRATIONS["config"],
                      "config v1→v2（分组阈值默认值迁移）应已注册")
        self.assertIn(2, MIGRATIONS["config"],
                      "config v2→v3（新增 toast_duration_ms 键）应已注册")
        self.assertIn(0, MIGRATIONS["fragments"], "示例迁移应挂在 fragments 0→1")


class TestLoadRecordsMigration(unittest.TestCase):
    """load_records 的版本迁移链路"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_migrate_")
        self.path = os.path.join(self.dir, "fragments.json")
        data_backups.reset_rotation_state()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_old_version_data_is_migrated(self):
        """带旧版本号（data_version: 0）→ 示例迁移被调用且结果正确"""
        _write(self.path, {
            STORE_VERSION_KEY: 0,
            "fragments": [
                {"fragment_id": 1, "type": "text", "content": "旧碎片"},
                {"fragment_id": 2, "type": "text", "content": "新碎片",
                 "source": "clipboard"},
            ],
            "next_id": 3,
        })
        records, next_id = load_records(self.path, "fragments", lambda d: d)
        self.assertEqual(next_id, 3)
        self.assertEqual(records[0].get("source"), "",
                         "迁移应为缺失 source 的碎片补空串")
        self.assertEqual(records[1].get("source"), "clipboard",
                         "已有 source 的碎片原样保留（幂等）")

    def test_migration_called_and_load_is_read_only(self):
        """迁移函数确实被注册表调度；迁移结果只回传内存，不立即落盘"""
        calls = []

        def _spy(data):
            calls.append(data)
            return data

        original = json_store.MIGRATIONS.get("notes")
        json_store.MIGRATIONS["notes"] = {0: _spy}
        try:
            path = os.path.join(self.dir, "notes.json")
            _write(path, {STORE_VERSION_KEY: 0, "notes": [], "next_id": 5})
            records, next_id = load_records(path, "notes", lambda d: d)
            self.assertEqual(len(calls), 1, "迁移函数应被调用一次")
            self.assertEqual(records, [])
            self.assertEqual(next_id, 5)
            # 读路径不落盘：文件仍是旧版本号，等调用方正常保存时再带新版本
            self.assertEqual(_read(path).get(STORE_VERSION_KEY), 0)
        finally:
            json_store.MIGRATIONS["notes"] = original

    def test_missing_version_means_no_migration(self):
        """存量文件无 data_version → 视为当前版本，不迁移"""
        path = os.path.join(self.dir, "notes.json")
        _write(path, {"notes": [{"note_id": 1, "raw": True}], "next_id": 2})
        records, _ = load_records(path, "notes", lambda d: d)
        self.assertEqual(records, [{"note_id": 1, "raw": True}])

    def test_current_version_not_migrated(self):
        """data_version == 当前版本 → 不调用任何迁移"""
        calls = []

        def _spy(data):
            calls.append(data)
            return data

        original = json_store.MIGRATIONS.get("notes")
        json_store.MIGRATIONS["notes"] = {0: _spy}
        try:
            path = os.path.join(self.dir, "notes.json")
            _write(path, {STORE_VERSION_KEY: 1, "notes": [], "next_id": 1})
            load_records(path, "notes", lambda d: d)
            self.assertEqual(calls, [])
        finally:
            json_store.MIGRATIONS["notes"] = original

    def test_illegal_version_falls_back_to_current(self):
        """版本号被写成非数字（手工编辑）→ 按当前版本处理，不清空数据"""
        path = os.path.join(self.dir, "notes.json")
        _write(path, {STORE_VERSION_KEY: "abc", "notes": [], "next_id": 1})
        records, _ = load_records(path, "notes", lambda d: d)
        self.assertEqual(records, [])

    def test_migrate_data_direct(self):
        """migrate_data 直调：逐级推进，未注册的中间级原样跳过"""
        data = {"fragments": [{"fragment_id": 1}]}
        out = migrate_data("fragments", data, 0, 1)
        self.assertEqual(out["fragments"][0].get("source"), "")
        # 全部 store 均为 v1：from == to 时原样返回
        same = migrate_data("notes", {"notes": []}, 1, 1)
        self.assertEqual(same, {"notes": []})


class TestSaveRecords(unittest.TestCase):
    """save_records：写前滚动备份 + 自动补 data_version + 原子写"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_save_")
        self.path = os.path.join(self.dir, "notes.json")
        data_backups.reset_rotation_state()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_stamps_data_version(self):
        """显式 store → 落盘自动带 data_version = 当前版本"""
        self.assertTrue(save_records(self.path, {"notes": [], "next_id": 1},
                                     store="notes"))
        self.assertEqual(_read(self.path).get(STORE_VERSION_KEY),
                         STORE_VERSIONS["notes"])

    def test_save_auto_detects_store(self):
        """不传 store → 按 data 里现存的记录键自动识别"""
        path = os.path.join(self.dir, "fragments.json")
        self.assertTrue(save_records(path, {"fragments": [], "next_id": 1}))
        self.assertEqual(_read(path).get(STORE_VERSION_KEY), 1)
        # 未知结构的 data（无任何记录键）→ 不强行补版本号，原样落盘
        plain = os.path.join(self.dir, "plain.json")
        self.assertTrue(save_records(plain, {"hello": "world"}))
        self.assertNotIn(STORE_VERSION_KEY, _read(plain))

    def test_save_does_not_mutate_caller_dict(self):
        """版本号补在副本上，调用方的 data 字典不被污染"""
        data = {"notes": [], "next_id": 1}
        save_records(self.path, data, store="notes")
        self.assertNotIn(STORE_VERSION_KEY, data)

    def test_save_rotates_before_overwrite(self):
        """覆盖写之前先滚动备份旧内容；同日只备一次"""
        _write(self.path, {"notes": ["旧"], "next_id": 2})
        save_records(self.path, {"notes": ["新"], "next_id": 3}, store="notes")
        backup = os.path.join(self.dir, "backups", "notes-%s.json"
                              % _today_str())
        self.assertTrue(os.path.isfile(backup), "覆盖写前应产生当日备份")
        self.assertEqual(_read(backup).get("notes"), ["旧"])
        self.assertEqual(_read(self.path).get("notes"), ["新"])
        save_records(self.path, {"notes": ["更新"], "next_id": 4},
                     store="notes")
        self.assertEqual(_read(backup).get("notes"), ["旧"])  # 仍是最初那份

    def test_save_atomic_no_tmp_left(self):
        """原子写：成功后不残留 .tmp"""
        save_records(self.path, {"notes": [], "next_id": 1}, store="notes")
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_save_failure_returns_false(self):
        """写盘失败（.tmp 路径被目录占位）返回 False，不抛异常"""
        os.makedirs(self.path + ".tmp", exist_ok=True)
        self.assertFalse(save_records(self.path, {"notes": []}, store="notes"))


if __name__ == "__main__":
    unittest.main()
