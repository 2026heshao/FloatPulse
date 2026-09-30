# -*- coding: utf-8 -*-
r"""数据目录双轨制（成熟化 2.2）——app_paths 运行模式解析回归。

覆盖矩阵（注入 frozen / exe_dir / env 与 tmp 目录，不碰真实仓库数据）：

  A resolve_data_root 纯函数：源码 / 便携 marker / 安装版 /
    无 APPDATA 兜底（LOCALAPPDATA → exe 目录）
  B is_portable_mode：marker 有无、源码恒 False
  C 安装版运行时重定向：get_data_dir / get_docx_path / get_temp_assets_dir
    走 %APPDATA%\FloatPulse，且显式 base_dir 被忽略（解耦正是目标）
  D 便携版钉子：行为与双轨制之前完全一致（防回归）
  E 源码模式钉子：frozen=False 时一切照旧（显式 base_dir 照常生效）
"""

import os
import sys

# 让测试能 import src 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.app_paths as ap

PROJECT_ROOT = ap.get_project_root()


def _go_frozen(monkeypatch, exe_dir):
    """把运行环境伪装成 PyInstaller frozen（用例结束自动还原）"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        str(os.path.join(str(exe_dir), "FloatPulse.exe")),
                        raising=False)


# ------------------------------------------------------ A resolve_data_root
class TestResolveDataRoot:
    def test_source_mode_unchanged(self, tmp_path):
        """源码模式：恒返回项目根，与 exe_dir / env 无关（现状不变）"""
        assert ap.resolve_data_root(frozen=False, exe_dir=str(tmp_path),
                                    env={}) == PROJECT_ROOT

    def test_portable_marker_returns_exe_dir(self, tmp_path):
        """便携：exe 同目录有 portable.marker → 数据根 = exe 目录"""
        (tmp_path / ap.PORTABLE_MARKER_NAME).write_text("", encoding="utf-8")
        assert ap.resolve_data_root(frozen=True, exe_dir=str(tmp_path),
                                    env={}) == str(tmp_path)

    def test_install_mode_appdata(self, tmp_path):
        """安装版：无 marker + 有 APPDATA → %APPDATA%\\FloatPulse"""
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        appdata = tmp_path / "appdata"
        appdata.mkdir()
        assert ap.resolve_data_root(frozen=True, exe_dir=str(exe_dir),
                                    env={"APPDATA": str(appdata)}
                                    ) == str(appdata / "FloatPulse")

    def test_install_mode_falls_back_to_localappdata(self, tmp_path):
        """安装版：APPDATA 缺失退 LOCALAPPDATA"""
        exe_dir = tmp_path / "exe"
        exe_dir.mkdir()
        local = tmp_path / "local"
        local.mkdir()
        assert ap.resolve_data_root(
            frozen=True, exe_dir=str(exe_dir),
            env={"APPDATA": "", "LOCALAPPDATA": str(local)}
        ) == str(local / "FloatPulse")

    def test_install_mode_falls_back_to_exe_dir(self, tmp_path):
        """安装版：APPDATA / LOCALAPPDATA 全缺失再退 exe 目录兜底"""
        exe_dir = tmp_path / "exe"
        exe_dir.mkdir()
        assert ap.resolve_data_root(
            frozen=True, exe_dir=str(exe_dir),
            env={"APPDATA": "", "LOCALAPPDATA": ""}) == str(exe_dir)

    def test_env_none_reads_os_environ(self, tmp_path, monkeypatch):
        """env 缺省时读 os.environ（运行时口径）"""
        exe_dir = tmp_path / "exe"
        exe_dir.mkdir()
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert ap.resolve_data_root(frozen=True, exe_dir=str(exe_dir)
                                    ) == str(tmp_path / "FloatPulse")


# ------------------------------------------------------ B is_portable_mode
class TestIsPortableMode:
    def test_source_mode_is_never_portable(self, tmp_path):
        assert ap.is_portable_mode(frozen=False, exe_dir=str(tmp_path)) is False

    def test_frozen_with_marker(self, tmp_path):
        (tmp_path / ap.PORTABLE_MARKER_NAME).write_text("", encoding="utf-8")
        assert ap.is_portable_mode(frozen=True, exe_dir=str(tmp_path)) is True

    def test_frozen_without_marker(self, tmp_path):
        assert ap.is_portable_mode(frozen=True, exe_dir=str(tmp_path)) is False

    def test_empty_exe_dir_is_safe(self):
        """exe_dir 为空串不抛异常（防御口径）"""
        assert ap.is_portable_mode(frozen=True, exe_dir="") is False


# ------------------------------------ C 安装版运行时重定向（get_* 系列）
class TestInstallModeRedirect:
    def test_get_data_dir_redirects_to_appdata(self, monkeypatch, tmp_path):
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        appdata = tmp_path / "appdata"
        appdata.mkdir()
        _go_frozen(monkeypatch, exe_dir)
        monkeypatch.setenv("APPDATA", str(appdata))
        data_dir = ap.get_data_dir()          # 无参自动解析
        assert data_dir == os.path.join(str(appdata), "FloatPulse", "float_data")
        assert os.path.isdir(data_dir)        # 缺省创建语义保持

    def test_explicit_base_dir_ignored_in_install_mode(self, monkeypatch, tmp_path):
        """安装版下显式 base_dir（exe 目录）被忽略——解耦正是本设计目标"""
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        appdata = tmp_path / "appdata"
        appdata.mkdir()
        _go_frozen(monkeypatch, exe_dir)
        monkeypatch.setenv("APPDATA", str(appdata))
        data_dir = ap.get_data_dir(str(exe_dir))
        assert data_dir == os.path.join(str(appdata), "FloatPulse", "float_data")
        assert ap.get_docx_path(str(exe_dir)) == os.path.join(
            data_dir, ap.DOCX_FILENAME)
        assert ap.get_temp_assets_dir() == os.path.join(
            data_dir, ap.TEMP_ASSETS_DIRNAME)

    def test_get_base_dir_stays_program_dir(self, monkeypatch, tmp_path):
        """安装版下 get_base_dir() 仍返回 exe 目录（plugins/ 等程序资源跟随）"""
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        _go_frozen(monkeypatch, exe_dir)
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        assert ap.get_base_dir() == str(exe_dir)

    def test_get_data_root_runtime(self, monkeypatch, tmp_path):
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        appdata = tmp_path / "appdata"
        appdata.mkdir()
        _go_frozen(monkeypatch, exe_dir)
        monkeypatch.setenv("APPDATA", str(appdata))
        assert ap.get_data_root() == str(appdata / "FloatPulse")


# ------------------------------------------ D 便携版钉子（现有行为不变）
class TestPortableModePins:
    def test_portable_behavior_unchanged(self, monkeypatch, tmp_path):
        """便携模式：数据、docx、base_dir 全部与双轨制之前口径一致"""
        exe_dir = tmp_path / "FloatPulse"
        exe_dir.mkdir()
        (exe_dir / ap.PORTABLE_MARKER_NAME).write_text("", encoding="utf-8")
        _go_frozen(monkeypatch, exe_dir)
        assert ap.is_portable_mode(frozen=True, exe_dir=str(exe_dir)) is True
        assert ap.get_base_dir() == str(exe_dir)
        assert ap.get_data_dir() == os.path.join(str(exe_dir), "float_data")
        assert ap.get_data_dir(str(exe_dir)) == os.path.join(
            str(exe_dir), "float_data")
        assert ap.get_docx_path(str(exe_dir)) == os.path.join(
            str(exe_dir), "float_data", ap.DOCX_FILENAME)
        assert ap.get_temp_assets_dir(str(exe_dir)) == os.path.join(
            str(exe_dir), "float_data", ap.TEMP_ASSETS_DIRNAME)

    def test_frozen_without_marker_is_install_mode(self, monkeypatch, tmp_path):
        """无 marker 的 frozen = 安装版（Inno dist2 组包路径）"""
        exe_dir = tmp_path / "Programs" / "FloatPulse"
        exe_dir.mkdir(parents=True)
        _go_frozen(monkeypatch, exe_dir)
        assert ap._is_install_mode() is True


# ------------------------------------------ E 源码模式钉子（现状不变）
class TestSourceModePins:
    def test_explicit_base_dir_wins(self, tmp_path):
        """源码模式：显式 base_dir 照常生效（历史语义，一个字都不能变）"""
        assert ap.get_data_dir(str(tmp_path)) == os.path.join(
            str(tmp_path), "float_data")
        assert ap.get_docx_path(str(tmp_path)) == os.path.join(
            str(tmp_path), "float_data", ap.DOCX_FILENAME)

    def test_is_install_mode_false_when_not_frozen(self):
        assert ap._is_install_mode() is False

    def test_get_data_root_source_mode(self):
        assert ap.get_data_root() == PROJECT_ROOT
