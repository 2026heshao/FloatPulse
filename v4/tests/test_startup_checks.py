# -*- coding: utf-8 -*-
"""启动数据完整性入口壳回归（收尾任务 C1）—— startup_checks。

钉死的行为（自 knowledge_ball.main 下沉，文案逐字保留）：
  A 全部完整 → 只记 info「启动检查完成：所有 JSON 文件完整」，不弹窗
  B 单个损坏 → 记 warning「启动检查完成：1 个文件损坏已重置」，不弹窗
  C ≥2 个损坏 → 弹原生 QMessageBox.warning，标题/正文逐字钉死
    （「检测到 N 个数据文件损坏…」+ 逐行「  • 文件: 原因」+ 日志路径尾注）
  D 扫描段仍是 src/app_paths.check_data_integrity（纯扫描，无 UI 依赖）
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import startup_checks                      # noqa: E402
from src.app_paths import check_data_integrity  # noqa: E402


class _FakeLogger:
    """最小日志替身：按级别收集消息"""

    def __init__(self):
        self.infos = []
        self.warnings = []
        self.debugs = []

    def debug(self, msg, *a):
        self.debugs.append(msg)

    def info(self, msg, *a):
        self.infos.append(msg)

    def warning(self, msg, *a):
        self.warnings.append(msg)


class _FakeMessageBox:
    """QMessageBox 替身：记录 warning 调用（parent, title, text）"""

    def __init__(self):
        self.calls = []

    def warning(self, parent, title, text, *a, **kw):
        self.calls.append((parent, title, text))


def _make_data_dir(tmp_path, files):
    """按 {文件名: 内容对象或原始字符串} 造数据目录"""
    d = tmp_path / "float_data"
    d.mkdir()
    for name, content in files.items():
        if isinstance(content, str):
            (d / name).write_text(content, encoding="utf-8")
        else:
            (d / name).write_text(json.dumps(content), encoding="utf-8")
    return str(d)


def test_all_intact_logs_info_no_popup(tmp_path, monkeypatch):
    fake_mb = _FakeMessageBox()
    monkeypatch.setattr(startup_checks, "QMessageBox", fake_mb)
    logger = _FakeLogger()
    data_dir = _make_data_dir(tmp_path, {"config.json": {"a": 1},
                                         "notes.json": []})
    startup_checks.run_startup_data_integrity_check(data_dir, logger)
    assert fake_mb.calls == []
    assert logger.infos == ["启动检查完成：所有 JSON 文件完整"]
    assert logger.warnings == []


def test_single_corrupted_logs_warning_no_popup(tmp_path, monkeypatch):
    fake_mb = _FakeMessageBox()
    monkeypatch.setattr(startup_checks, "QMessageBox", fake_mb)
    logger = _FakeLogger()
    data_dir = _make_data_dir(tmp_path, {"config.json": "{broken json"})
    startup_checks.run_startup_data_integrity_check(data_dir, logger)
    assert fake_mb.calls == []
    assert logger.warnings[-1] == "启动检查完成：1 个文件损坏已重置"
    assert logger.infos == []


def test_two_corrupted_pops_exact_text(tmp_path, monkeypatch):
    fake_mb = _FakeMessageBox()
    monkeypatch.setattr(startup_checks, "QMessageBox", fake_mb)
    logger = _FakeLogger()
    data_dir = _make_data_dir(tmp_path, {
        "config.json": "{broken",
        "notes.json": "not a json at all",
    })
    startup_checks.run_startup_data_integrity_check(data_dir, logger)
    assert len(fake_mb.calls) == 1
    parent, title, text = fake_mb.calls[0]
    assert parent is None
    assert title == "数据完整性检查"
    # 正文逐字钉死（C1 下沉零变化）：数量行 + 逐行明细 + 日志路径尾注
    lines = text.split("\n")
    assert lines[0] == "检测到 2 个数据文件损坏，已自动重置为空数据："
    assert lines[1] == ""
    detail_lines = [ln for ln in lines if ln.startswith("  • ")]
    assert len(detail_lines) == 2
    assert all((": " in ln.split("  • ", 1)[1]) for ln in detail_lines)
    assert "详情请查看日志：float_data/app.log" in lines
    assert logger.warnings[-1] == "启动检查完成：2 个文件损坏已重置"


def test_scan_segment_still_lives_in_app_paths():
    """扫描段必须仍在 src/app_paths（纯扫描、弹窗不在此处）"""
    import inspect
    src = inspect.getsource(check_data_integrity)
    assert "弹窗不在此处" in src
    assert "QMessageBox." not in src  # docstring 提及放行，代码调用必红
