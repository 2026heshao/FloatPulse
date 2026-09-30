# -*- coding: utf-8 -*-
"""发布三源一致性自检 tools/check_release_consistency.py 回归（纯逻辑，无 GUI）。

为什么值得单测：这个脚本是 Release 的**第一道闸门**——漏报（该红没红）
发出去的就是「安装包 4.7.1、程序自报 4.7.0、CHANGELOG 查无此版」的错版；
误报（该绿报红）则把一次正常发版卡死在 CI。这里用临时仓库钉住各分支：

  A read_app_version：正常 / 引号变体 / 缺常量
  B changelog_find_heading：v 前缀两种格式 / 日期后缀容忍 / [Unreleased] 不误伤
  C check：对齐 / 版本错 / 缺标题
  D main() 退出码与中文输出：0 = 对齐 / 1 = 任一不符 / v 前缀 expect 兼容
  E 真仓库防漂移：以真实 app_version.py 为 expect，CHANGELOG 必须有对应标题
    （谁改了版本号而没同步 CHANGELOG，全量测试套件当场红）
"""

import importlib.util
import os

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根

CRC_PATH = os.path.join(ROOT, "tools", "check_release_consistency.py")


@pytest.fixture(scope="module")
def crc():
    """直载 tools/check_release_consistency.py（纯标准库，无副作用）"""
    spec = importlib.util.spec_from_file_location("fp_test_release_consistency", CRC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# CHANGELOG 现有标题格式样本（方括号版本 + v 前缀 + 日期后缀）
_CHANGELOG = (
    "# Changelog\n"
    "\n"
    "本项目遵循 Keep a Changelog 格式。\n"
    "\n"
    "## [Unreleased]\n"
    "\n"
    "### Changed\n"
    "- 正文里提到 4.7.0 字样不算标题\n"
    "\n"
    "## [v4.7.0] - 2026-09-29\n"
    "\n"
    "### Added\n"
    "- 应用内检查更新\n"
    "\n"
    "## [v4.6.0] - 2026-09-20\n"
)


def _make_repo(tmp_path, version="4.7.0", changelog=_CHANGELOG):
    """构造一个最小的「仓库」：v4/src/app_version.py + CHANGELOG.md"""
    src = tmp_path / "v4" / "src"
    src.mkdir(parents=True)
    (src / "app_version.py").write_text(
        f'# 程序版本（唯一真相源）\nAPP_VERSION = "{version}"\n',
        encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def _paths(repo):
    return (str(repo / "v4" / "src" / "app_version.py"),
            str(repo / "CHANGELOG.md"))


# ------------------------------------------------------ A 版本读取
class TestReadAppVersion:
    def test_reads_double_quoted(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        vf, _ = _paths(repo)
        assert crc.read_app_version(vf) == "4.7.0"

    def test_reads_single_quoted(self, crc, tmp_path):
        vf = tmp_path / "app_version.py"
        vf.write_text("APP_VERSION = '9.9.9'\n", encoding="utf-8")
        assert crc.read_app_version(str(vf)) == "9.9.9"

    def test_missing_constant_exits(self, crc, tmp_path):
        vf = tmp_path / "app_version.py"
        vf.write_text("# 什么都没有\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            crc.read_app_version(str(vf))


# ------------------------------------------------------ B 标题解析
class TestChangelogFindHeading:
    def test_finds_v_prefixed_heading_with_date(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        _, cf = _paths(repo)
        line = crc.changelog_find_heading(cf, "4.7.0")
        assert line == "## [v4.7.0] - 2026-09-29"

    def test_tolerates_v_prefix_in_expect(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        _, cf = _paths(repo)
        assert crc.changelog_find_heading(cf, "v4.7.0") == "## [v4.7.0] - 2026-09-29"

    def test_accepts_heading_without_v_prefix(self, crc, tmp_path):
        repo = _make_repo(tmp_path, changelog="## [4.7.0] - 2026-09-29\n")
        _, cf = _paths(repo)
        assert crc.changelog_find_heading(cf, "4.7.0") == "## [4.7.0] - 2026-09-29"

    def test_tolerates_missing_date_suffix(self, crc, tmp_path):
        repo = _make_repo(tmp_path, changelog="## [v4.7.0]\n")
        _, cf = _paths(repo)
        assert crc.changelog_find_heading(cf, "4.7.0") == "## [v4.7.0]"

    def test_missing_version_returns_empty(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        _, cf = _paths(repo)
        assert crc.changelog_find_heading(cf, "9.9.9") == ""

    def test_unreleased_and_body_mentions_do_not_match(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        _, cf = _paths(repo)
        # 版本号只出现在 [Unreleased] 段或正文里都不算版本标题——
        # 「CHANGELOG 有 4.7.0 字样」不等于「CHANGELOG 有 4.7.0 版本标题」
        unreleased_only = "## [Unreleased]\n\n- 正文里提到 4.7.0 字样\n"
        repo2 = _make_repo(tmp_path / "r2", changelog=unreleased_only)
        _, cf2 = _paths(repo2)
        assert crc.changelog_find_heading(cf2, "4.7.0") == ""
        # 低层级标题（### ）与正文引用也不算
        assert crc.changelog_find_heading(
            cf, "正文里提到 4.7.0 字样不算标题") == ""


# ------------------------------------------------------ C 校验组合
class TestCheck:
    def test_aligned_returns_no_problems(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        vf, cf = _paths(repo)
        assert crc.check("4.7.0", vf, cf) == []

    def test_version_mismatch_is_reported(self, crc, tmp_path):
        repo = _make_repo(tmp_path, version="4.7.1")
        vf, cf = _paths(repo)
        problems = crc.check("4.7.0", vf, cf)
        assert len(problems) == 1
        assert "APP_VERSION 不一致" in problems[0]
        assert "4.7.1" in problems[0] and "4.7.0" in problems[0]

    def test_missing_changelog_heading_is_reported(self, crc, tmp_path):
        repo = _make_repo(tmp_path, changelog="## [Unreleased]\n")
        vf, cf = _paths(repo)
        problems = crc.check("4.7.0", vf, cf)
        assert len(problems) == 1
        assert "CHANGELOG.md 缺版本标题" in problems[0]

    def test_both_wrong_reports_both(self, crc, tmp_path):
        repo = _make_repo(tmp_path, version="5.0.0", changelog="## [Unreleased]\n")
        vf, cf = _paths(repo)
        problems = crc.check("4.7.0", vf, cf)
        assert len(problems) == 2


# ------------------------------------------------------ D CLI 退出码
class TestMainExitCodes:
    def test_aligned_returns_zero(self, crc, tmp_path, capsys):
        repo = _make_repo(tmp_path)
        vf, cf = _paths(repo)
        rc = crc.main(["--expect", "4.7.0", "--version-file", vf,
                       "--changelog", cf])
        out = capsys.readouterr().out
        assert rc == 0
        assert "[OK] 三源对齐" in out

    def test_v_prefixed_expect_returns_zero(self, crc, tmp_path):
        repo = _make_repo(tmp_path)
        vf, cf = _paths(repo)
        assert crc.main(["--expect", "v4.7.0", "--version-file", vf,
                         "--changelog", cf]) == 0

    def test_mismatch_returns_one_with_diff(self, crc, tmp_path, capsys):
        repo = _make_repo(tmp_path, version="4.7.1")
        vf, cf = _paths(repo)
        rc = crc.main(["--expect", "4.7.0", "--version-file", vf,
                       "--changelog", cf])
        out = capsys.readouterr().out
        assert rc == 1
        assert "[X] APP_VERSION 不一致" in out
        assert "终止发布" in out

    def test_missing_heading_returns_one(self, crc, tmp_path, capsys):
        repo = _make_repo(tmp_path, changelog="## [Unreleased]\n")
        vf, cf = _paths(repo)
        rc = crc.main(["--expect", "4.7.0", "--version-file", vf,
                       "--changelog", cf])
        out = capsys.readouterr().out
        assert rc == 1
        assert "[X] CHANGELOG.md 缺版本标题" in out


# ------------------------------------------------------ E 真仓库防漂移
class TestRealRepo:
    def test_current_repo_is_aligned(self, crc):
        """以真实 app_version.py 的 APP_VERSION 为 expect，真 CHANGELOG 必须有对应标题。

        这条是给全量套件的常驻钉子：改了 APP_VERSION 而没加 CHANGELOG
        版本标题，pytest 当场红，不用等 tag 推上 CI 才发现。
        """
        expect = crc.read_app_version(crc.VERSION_FILE)
        assert os.path.isfile(crc.CHANGELOG_FILE)
        assert crc.main(["--expect", expect]) == 0
