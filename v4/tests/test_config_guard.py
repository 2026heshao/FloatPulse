# -*- coding: utf-8 -*-
"""
====================================================================
config.json 损坏防护与写前滚动备份护栏测试
====================================================================
对应成熟化路线图 1.1 / 1.2 前半：

  * 损坏 JSON → 先备份 .corrupt.bak 再回退默认配置，load_reset_reason="corrupt"
  * 正常加载不受影响（不误备份、不误标记、类型/范围校验行为不变）
  * save() 写前滚动备份：同日只备一次、失败不阻断保存

====================================================================
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from src import data_backups
from src.config import DEFAULT_CONFIG, ConfigManager


def _backup_name(stem: str) -> str:
    """当日滚动备份的文件名：<主干>-<YYYYMMDD>.json"""
    return "%s-%s.json" % (stem, date.today().strftime("%Y%m%d"))


class TestConfigCorruptGuard(unittest.TestCase):
    """config.json 损坏防护（路线图 1.1）"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_cfg_guard_")
        self.path = os.path.join(self.dir, "config.json")
        data_backups.reset_rotation_state()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_text(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_corrupt_json_backed_up_and_reset_to_default(self):
        """损坏 JSON → 生成 .corrupt.bak、标记 corrupt、默认配置可用"""
        self._write_text("{ 这不是合法 json ")
        cm = ConfigManager(self.path)
        self.assertTrue(os.path.exists(self.path + ".corrupt.bak"),
                        "损坏的 config.json 应先备份为 .corrupt.bak")
        self.assertEqual(cm.load_reset_reason, "corrupt")
        # 默认配置可用：热键 / AI key / 主题均回到默认值，不崩溃
        self.assertEqual(cm.get("quick_capture_hotkey"),
                         DEFAULT_CONFIG["quick_capture_hotkey"])
        self.assertEqual(cm.get("ai_cloud_api_key"),
                         DEFAULT_CONFIG["ai_cloud_api_key"])
        self.assertEqual(cm.get("theme"), DEFAULT_CONFIG["theme"])

    def test_non_dict_toplevel_treated_as_corrupt(self):
        """顶层不是 dict（如被写成数组）也按损坏处理：备份 + 标记"""
        self._write_text("[1, 2, 3]")
        cm = ConfigManager(self.path)
        self.assertTrue(os.path.exists(self.path + ".corrupt.bak"))
        self.assertEqual(cm.load_reset_reason, "corrupt")
        self.assertEqual(cm.get("theme"), DEFAULT_CONFIG["theme"])

    def test_missing_file_is_normal_load(self):
        """文件不存在 → 默认配置，无损坏标记"""
        cm = ConfigManager(self.path)
        self.assertIsNone(cm.load_reset_reason)
        self.assertFalse(os.path.exists(self.path + ".corrupt.bak"))

    def test_normal_load_untouched_by_guard(self):
        """正常加载：值原样保留、无备份文件、无损坏标记"""
        cfg = dict(DEFAULT_CONFIG)
        cfg["theme"] = "light"
        cfg["quick_capture_hotkey"] = "Ctrl+Alt+X"
        self._write_text(json.dumps(cfg, ensure_ascii=False))
        cm = ConfigManager(self.path)
        self.assertIsNone(cm.load_reset_reason)
        self.assertEqual(cm.get("theme"), "light")
        self.assertEqual(cm.get("quick_capture_hotkey"), "Ctrl+Alt+X")
        self.assertFalse(os.path.exists(self.path + ".corrupt.bak"))

    def test_per_key_type_error_is_not_corrupt(self):
        """单项类型错误 → 该项回退默认，但不整档判损坏（原校验逻辑不变）"""
        self._write_text(json.dumps({"theme": 123, "ball_size": 999}))
        cm = ConfigManager(self.path)
        self.assertIsNone(cm.load_reset_reason)
        self.assertEqual(cm.get("theme"), DEFAULT_CONFIG["theme"])
        self.assertEqual(cm.get("ball_size"), DEFAULT_CONFIG["ball_size"])
        self.assertFalse(os.path.exists(self.path + ".corrupt.bak"))

    def test_schema_version_default_and_persisted(self):
        """schema_version 默认 1，且随 save() 落盘"""
        self.assertEqual(DEFAULT_CONFIG.get("schema_version"), 1)
        cm = ConfigManager(self.path)
        cm.set("theme", "light")
        cm.save()
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("schema_version"), 1)
        self.assertEqual(data.get("theme"), "light")

    def test_legacy_file_without_schema_version_still_loads(self):
        """存量 config.json（无 schema_version 键）零迁移、用户值保留"""
        legacy = {k: v for k, v in DEFAULT_CONFIG.items()
                  if k != "schema_version"}
        legacy["theme"] = "light"
        self._write_text(json.dumps(legacy, ensure_ascii=False))
        cm = ConfigManager(self.path)
        self.assertIsNone(cm.load_reset_reason)
        self.assertEqual(cm.get("theme"), "light")
        self.assertEqual(cm.get("schema_version"), 1)  # 内存收敛到当前版本


class TestConfigSaveRotate(unittest.TestCase):
    """ConfigManager.save 的写前滚动备份（路线图 1.2 前半接入点之一）"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fp_cfg_rotate_")
        self.path = os.path.join(self.dir, "config.json")
        self.backup_dir = os.path.join(self.dir, "backups")
        data_backups.reset_rotation_state()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _backup_names(self):
        return sorted(os.listdir(self.backup_dir)) \
            if os.path.isdir(self.backup_dir) else []

    def test_first_save_without_prior_file_no_backup(self):
        """首次保存（磁盘尚无旧文件）→ 无事可备，不产生备份"""
        cm = ConfigManager(self.path)
        cm.set("theme", "light")
        cm.save()
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(self._backup_names(), [])

    def test_overwrite_backs_up_once_per_day(self):
        """同日第二次保存起才备份，且当日只备一次（性能 no-op）"""
        cm = ConfigManager(self.path)
        cm.set("theme", "light")
        cm.save()                                  # 第 1 次：无旧文件，不备
        cm.set("theme", "dark")
        cm.save()                                  # 第 2 次：备份第 1 次的内容
        self.assertEqual(self._backup_names(), [_backup_name("config")])
        with open(os.path.join(self.backup_dir, _backup_name("config")),
                  "r", encoding="utf-8") as f:
            backup = json.load(f)
        self.assertEqual(backup.get("theme"), "light")  # 备的是覆盖前内容
        cm.set("theme", "light")
        cm.save()                                  # 第 3 次：当日 no-op
        self.assertEqual(self._backup_names(), [_backup_name("config")])

    def test_save_failure_does_not_raise(self):
        """写盘失败（.tmp 路径被目录占位）不抛异常，与原行为一致"""
        # 把 <config.json>.tmp 替换为目录，使 open(tmp, "w") 抛 IsADirectoryError
        os.makedirs(self.path + ".tmp", exist_ok=True)
        cm = ConfigManager(self.path)
        cm.set("theme", "dark")
        try:
            cm.save()                              # 不应抛出
        except OSError as exc:
            self.fail("save() 在写盘失败时不应抛异常: %s" % exc)


if __name__ == "__main__":
    unittest.main()
