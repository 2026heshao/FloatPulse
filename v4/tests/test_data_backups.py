# -*- coding: utf-8 -*-
"""data_backups.snapshot_status 纯逻辑测试（细节强化 P2-8 配套）。

snapshot_status 是设置页「自动快照」被动状态行的数据源：只读、
绝不写、任何失败回落 (0.0, 0)。护栏口径与本模块 rotate_backup 的
「备份失败绝不抛异常」一致——展示路径也绝不能反向拖垮设置页。
"""
import os
import time

from src.data_backups import BACKUPS_DIRNAME, snapshot_status

# 路径自举铁律：一律由 __file__ 推导，禁写死绝对路径
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_backups(tmp_path, names, mtime=None):
    """在 tmp_path/backups/ 下造快照文件；mtime 传 (atime, mtime) 或 None。"""
    backup_dir = tmp_path / BACKUPS_DIRNAME
    backup_dir.mkdir()
    for name in names:
        p = backup_dir / name
        p.write_text("{}", encoding="utf-8")
        if mtime is not None:
            os.utime(p, mtime)
    return backup_dir


class TestSnapshotStatus:
    def test_missing_dir_returns_zero(self, tmp_path):
        """目录不存在（用户从未保存过数据）→ (0.0, 0)，不抛异常。"""
        assert snapshot_status(str(tmp_path)) == (0.0, 0)

    def test_empty_dir_returns_zero(self, tmp_path):
        """目录存在但还没有任何快照 → (0.0, 0)。"""
        (tmp_path / BACKUPS_DIRNAME).mkdir()
        assert snapshot_status(str(tmp_path)) == (0.0, 0)

    def test_counts_files_and_takes_latest_mtime(self, tmp_path):
        """多快照并存：count 数全部文件，mtime 取最大值。"""
        old = time.mktime((2026, 10, 1, 8, 0, 0, 0, 0, -1))
        new = time.mktime((2026, 10, 4, 14, 30, 0, 0, 0, -1))
        _make_backups(tmp_path, ["fragments-20261001.json", "config-20261004.json"])
        backup_dir = tmp_path / BACKUPS_DIRNAME
        os.utime(backup_dir / "fragments-20261001.json", (old, old))
        os.utime(backup_dir / "config-20261004.json", (new, new))

        latest, count = snapshot_status(str(tmp_path))
        assert count == 2
        assert latest == new          # 取最新一份，不是第一份

    def test_subdirectories_not_counted(self, tmp_path):
        """子目录不计入快照数（口径：只算文件）。"""
        backup_dir = _make_backups(tmp_path, ["fragments-20261004.json"])
        (backup_dir / "nested").mkdir()

        _, count = snapshot_status(str(tmp_path))
        assert count == 1

    def test_dirty_path_never_raises(self, tmp_path):
        """把文件路径当目录传（OSError 家族）→ (0.0, 0)，展示层安全。"""
        tricky = tmp_path / "not_a_dir.json"
        tricky.write_text("x", encoding="utf-8")
        assert snapshot_status(str(tricky)) == (0.0, 0)

    def test_relative_path_resolved(self, tmp_path, monkeypatch):
        """相对路径按 abspath 解析（rotate_backup 内部同为 abspath 口径）。"""
        _make_backups(tmp_path, ["fragments-20261004.json"])
        monkeypatch.chdir(tmp_path)
        _, count = snapshot_status(".")
        assert count == 1
