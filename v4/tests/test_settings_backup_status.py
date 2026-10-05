# -*- coding: utf-8 -*-
"""设置页「自动快照」被动状态行测试（2026-10-04 细节强化 P2-8）。

钉三层：
  1. 行存在——`_backup_status` 标签在面板上且文案非空（防构建期静默漏建）；
  2. 现场口径——有快照 → 「最近一次：…（现存 N 份）」；无快照目录 →
     「暂无快照…」（get_data_dir 打桩，不依赖真实数据目录）；
  3. 过期防线——show_category("about") 必须重算一次，切其它分类不重算
     （面板长驻整个会话，快照会话内持续产生）。

红线：只读展示零入口——状态行绝不允许出现任何可触发备份操作的控件。
"""
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QLabel  # noqa: E402

from src.data_backups import BACKUPS_DIRNAME  # noqa: E402
from src.settings_panel import SettingsPanel  # noqa: E402


class _FakeConfig:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel(qapp):
    host = types.SimpleNamespace(config=_FakeConfig(), current_theme="dark")
    return SettingsPanel(host)


def _stub_data_dir(monkeypatch, tmp_path):
    import src.settings_panel as sp
    monkeypatch.setattr(sp, "get_data_dir", lambda: str(tmp_path))


# ====================================================================
# 1. 行存在
# ====================================================================
def test_backup_status_label_exists(panel):
    assert isinstance(panel._backup_status, QLabel)
    assert panel._backup_status.text()          # 构建期就算了一次，文案非空


# ====================================================================
# 2. 现场口径
# ====================================================================
def test_no_backup_dir_shows_placeholder(panel, monkeypatch, tmp_path):
    _stub_data_dir(monkeypatch, tmp_path)       # tmp_path 下无 backups/
    panel._refresh_backup_status()
    assert "暂无快照" in panel._backup_status.text()


def test_snapshots_present_formats_latest(panel, monkeypatch, tmp_path):
    backup_dir = tmp_path / BACKUPS_DIRNAME
    backup_dir.mkdir()
    fixed = time.mktime((2026, 10, 4, 14, 30, 0, 0, 0, -1))
    for name in ("fragments-20261001.json", "config-20261004.json"):
        p = backup_dir / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (fixed, fixed))
    _stub_data_dir(monkeypatch, tmp_path)

    panel._refresh_backup_status()
    text = panel._backup_status.text()
    assert "最近一次：" in text
    assert "2026-10-04 14:30" in text
    assert "现存 2 份" in text


# ====================================================================
# 3. 过期防线：切到「关于」才重算
# ====================================================================
def test_show_about_refreshes_other_categories_dont(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "_refresh_backup_status",
                        lambda: calls.append(1))
    panel.show_category("ai")
    assert calls == []                          # 其它分类不重算
    panel.show_category("about")
    assert len(calls) == 1                      # 切「关于」恰好重算一次
