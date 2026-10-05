# -*- coding: utf-8 -*-
r"""portable_migrate（安装版旧数据迁移）回归。

覆盖分支（全部注入 tmp 目录，不碰真实仓库数据；弹窗经 GlassMessageBox mock，
offscreen 安全，绝不真弹框）：

  A detect_legacy_data：命中 / 便携源码模式不迁移 / 旧目录缺失 /
    旧目录无用户数据（纯 app.log 空壳）/ 新位置已有数据 / 新位置为空仍命中
  B migrate_legacy_data：整树复制完整性 + 旧目录改名留备份 /
    复制半途失败清理目标且源完好 / 目标已有数据拒绝 / 摘要无效 / 改名失败回滚
  C maybe_prompt_migrate：同意执行迁移 / 拒绝一切不动 / 未命中不弹窗
"""

import os
import sys

# 让测试能 import src 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import glass_message_box as _gmb

import src.portable_migrate as pm


# 旧数据树样本：config + 三个数据 json + 知识库.docx + temp_assets/ +
# plugins/<id>/（嵌套），共 7 个文件
_LEGACY_FILES = {
    "config.json": '{"theme":"dark"}',
    "schedule.json": "[]",
    "notes.json": "[]",
    "fragments.json": "[]",
    pm.DOCX_FILENAME: "docx-bytes",
    os.path.join("temp_assets", "a.png"): "png-bytes",
    os.path.join("plugins", "demo", "main.py"): "print('demo')",
}


def _make_legacy_dir(root: str) -> None:
    """在 root 下造一份典型旧版 float_data/（文件清单见 _LEGACY_FILES）"""
    for rel, data in _LEGACY_FILES.items():
        abs_p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write(data)


def _make_env(tmp_path):
    """标准场景：exe 安装目录 + 安装版数据根目录。

    返回 (exe_dir, data_root, src, dst)；data_root 即 float_data/ 的父目录
    （%APPDATA%\\FloatPulse，与 get_data_root() 同语义）。
    """
    exe_dir = tmp_path / "Programs" / "FloatPulse"
    exe_dir.mkdir(parents=True)
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    data_root = appdata / "FloatPulse"
    src = exe_dir / "float_data"
    dst = data_root / "float_data"
    return str(exe_dir), str(data_root), str(src), str(dst)


# ------------------------------------------------------ A 检测
class TestDetect:
    def test_hit_returns_summary(self, tmp_path):
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        s = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        assert s is not None
        assert s["src"] == src
        assert s["dst"] == dst
        assert s["files"] == len(_LEGACY_FILES)

    def test_detect_has_no_side_effect(self, tmp_path):
        """检测阶段零副作用：不创建数据根目录（ConfigManager 之前可安全调用）"""
        exe_dir, data_root, src, _dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        assert os.path.isdir(data_root) is False

    def test_runtime_default_uses_get_data_root(self, tmp_path, monkeypatch):
        """缺省注入时走运行时解析（需伪装 frozen：安装版下才有 APPDATA 数据根）"""
        exe_dir, _data_root, src, _dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        appdata = tmp_path / "appdata"          # _make_env 已创建，直接用
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable",
                            os.path.join(exe_dir, "FloatPulse.exe"),
                            raising=False)
        monkeypatch.setenv("APPDATA", str(appdata))
        s = pm.detect_legacy_data(True, exe_dir=exe_dir)
        assert s is not None
        assert s["dst"] == os.path.join(str(appdata), "FloatPulse", "float_data")

    def test_not_install_mode_returns_none(self, tmp_path):
        """便携 / 源码模式一律不迁移"""
        exe_dir, data_root, src, _dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        assert pm.detect_legacy_data(False, exe_dir=exe_dir,
                                     data_root=data_root) is None

    def test_src_missing_returns_none(self, tmp_path):
        exe_dir, data_root, _src, _dst = _make_env(tmp_path)
        assert pm.detect_legacy_data(True, exe_dir=exe_dir,
                                     data_root=data_root) is None

    def test_migrated_backup_dir_not_detected(self, tmp_path):
        """已迁移过（旧目录已改名 .migrated-*）→ 不再命中"""
        exe_dir, data_root, src, _dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        os.rename(src, src + ".migrated-20260930-120000")
        assert pm.detect_legacy_data(True, exe_dir=exe_dir,
                                     data_root=data_root) is None

    def test_src_without_user_data_returns_none(self, tmp_path):
        """纯 app.log 的空壳目录不算有历史数据，不打扰用户"""
        exe_dir, data_root, src, _dst = _make_env(tmp_path)
        os.makedirs(src)
        with open(os.path.join(src, "app.log"), "w", encoding="utf-8") as f:
            f.write("log")
        assert pm.detect_legacy_data(True, exe_dir=exe_dir,
                                     data_root=data_root) is None

    def test_dst_nonempty_returns_none(self, tmp_path):
        """新位置已有数据（已在用）→ 绝不覆盖"""
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        os.makedirs(dst)
        with open(os.path.join(dst, "config.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        assert pm.detect_legacy_data(True, exe_dir=exe_dir,
                                     data_root=data_root) is None

    def test_dst_empty_dir_still_hits(self, tmp_path):
        """新位置空目录（get_data_dir 先建出来的空壳）仍可迁移"""
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        os.makedirs(dst)
        s = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        assert s is not None and s["dst"] == dst


# ------------------------------------------------------ B 迁移
class TestMigrate:
    def test_full_tree_copy_and_rename(self, tmp_path):
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        s = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        r = pm.migrate_legacy_data(s)
        assert r["ok"] is True
        assert r["files"] == len(_LEGACY_FILES)
        # 目标整树完整：顶层文件 + 嵌套子目录 + 内容一致
        for rel, data in _LEGACY_FILES.items():
            abs_p = os.path.join(dst, rel)
            assert os.path.isfile(abs_p), abs_p
            with open(abs_p, "r", encoding="utf-8") as f:
                assert f.read() == data
        # 旧目录只改名不删除，留后悔药
        assert os.path.isdir(src) is False
        assert r["backup"].startswith(src + ".migrated-")
        assert os.path.isfile(os.path.join(r["backup"], "config.json"))

    def test_copy_failure_cleans_dst_keeps_src(self, tmp_path, monkeypatch):
        """复制半途失败（mock copytree 抛错）：目标清理、源数据原样"""
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)

        def boom(src_dir, dst_dir, **kwargs):
            os.makedirs(dst_dir, exist_ok=True)          # 模拟写了半截
            with open(os.path.join(dst_dir, "config.json"), "w",
                      encoding="utf-8") as f:
                f.write("{half")
            raise OSError("模拟磁盘满")

        monkeypatch.setattr(pm.shutil, "copytree", boom)
        s = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        r = pm.migrate_legacy_data(s)
        assert r["ok"] is False
        assert "模拟磁盘满" in r["error"]
        assert os.path.isdir(dst) is False               # 半成品已清理
        assert os.path.isfile(os.path.join(src, "config.json"))  # 源原样
        assert os.path.isfile(os.path.join(src, "temp_assets", "a.png"))
        # 失败后下次启动可重试：detect 重新命中
        s2 = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        assert s2 is not None

    def test_rename_failure_cleans_dst_keeps_src(self, tmp_path, monkeypatch):
        """改名失败（如旧目录被占用）：目标回滚清理、源数据原样"""
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)

        def boom(a, b):
            raise OSError("目录被占用")

        monkeypatch.setattr(pm.os, "rename", boom)
        s = pm.detect_legacy_data(True, exe_dir=exe_dir, data_root=data_root)
        r = pm.migrate_legacy_data(s)
        assert r["ok"] is False
        assert os.path.isdir(dst) is False
        assert os.path.isdir(src) is True                # 源绝不动
        assert os.path.isfile(os.path.join(src, pm.DOCX_FILENAME))

    def test_dst_nonempty_refused(self, tmp_path):
        """目标已有数据：拒绝执行，源数据原样"""
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        os.makedirs(dst)
        with open(os.path.join(dst, "config.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        r = pm.migrate_legacy_data(
            {"src": src, "dst": dst, "files": len(_LEGACY_FILES)})
        assert r["ok"] is False
        assert "拒绝覆盖" in r["error"]
        assert os.path.isfile(os.path.join(src, "config.json"))

    def test_invalid_summary(self, tmp_path):
        assert pm.migrate_legacy_data(None)["ok"] is False
        assert pm.migrate_legacy_data({})["ok"] is False
        assert pm.migrate_legacy_data(
            {"src": str(tmp_path / "nope"), "dst": str(tmp_path / "d"),
             "files": 0})["ok"] is False


# ------------------------------------------------------ C 弹窗接线
class TestMaybePrompt:
    def _fake_answer(self, monkeypatch, answer, calls):
        def fake_question(*args, **kwargs):
            calls.append(args)
            return answer
        # 2026-10 统一改造：迁移弹窗已换 GlassMessageBox（模块内惰性导入），
        # patch 目标随之迁到 src.glass_message_box.GlassMessageBox
        monkeypatch.setattr(_gmb.GlassMessageBox, "question",
                            staticmethod(fake_question))
        monkeypatch.setattr(_gmb.GlassMessageBox, "warning",
                            staticmethod(lambda *a, **k: None))

    def test_yes_executes_migration(self, tmp_path, monkeypatch):
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        calls = []
        self._fake_answer(monkeypatch, True, calls)
        r = pm.maybe_prompt_migrate(None, install_mode=True,
                                    exe_dir=exe_dir, data_root=data_root)
        assert r is not None and r["ok"] is True
        assert len(calls) == 1
        text = str(calls[0][2])  # question(parent, 标题, 正文, ...)
        # 文案要点：说明旧数据、询问迁移、承诺保留备份
        assert "迁移" in text and "保留备份" in text
        assert os.path.isdir(dst) is True
        assert os.path.isdir(src) is False               # 已改名留备份
        assert r["backup"].startswith(src + ".migrated-")

    def test_no_declines_without_touching_anything(self, tmp_path, monkeypatch):
        exe_dir, data_root, src, dst = _make_env(tmp_path)
        _make_legacy_dir(src)
        calls = []
        self._fake_answer(monkeypatch, False, calls)
        r = pm.maybe_prompt_migrate(None, install_mode=True,
                                    exe_dir=exe_dir, data_root=data_root)
        assert r is None
        assert os.path.isdir(src) is True                # 源原样
        assert os.path.isdir(dst) is False               # 目标未建
        assert len(calls) == 1

    def test_no_hit_no_dialog(self, tmp_path, monkeypatch):
        """未命中（已迁移 / 非安装版）不弹窗、不执行"""
        exe_dir, data_root, _src, _dst = _make_env(tmp_path)
        calls = []
        self._fake_answer(monkeypatch, True, calls)
        assert pm.maybe_prompt_migrate(None, install_mode=True,
                                       exe_dir=exe_dir,
                                       data_root=data_root) is None
        assert pm.maybe_prompt_migrate(None, install_mode=False,
                                       exe_dir=exe_dir,
                                       data_root=data_root) is None
        assert calls == []
