# -*- coding: utf-8 -*-
"""Ruff 静态检查护栏（成熟化路线图 4.1）。

把「ruff 零告警」做成测试套件的一部分：任何人带入 E/F/W 级问题
（未用 import、未定义名、f-string 缺占位符、无效转义等）时，本地
pytest 直接指出，不必等 CI 闸门变红。

- 与 CI（tests.yml 的 lint job）完全同一命令：仓库根下 `ruff check .`，
  规则选型与 per-file 豁免理由集中在仓库根 ruff.toml。
- 跳过策略：环境未安装 ruff 时 pytest.skip——贡献者没有 ruff 也能跑全量测试。
"""
import os
import subprocess
import sys
from importlib.util import find_spec

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BASE)


@pytest.mark.skipif(find_spec("ruff") is None,
                    reason="未安装 ruff，跳过 lint 护栏（pip install ruff 后启用）")
def test_ruff_check_clean():
    """ruff check . 退出码必须为 0（零告警）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, (
        "ruff check 发现违规（规则与豁免理由见仓库根 ruff.toml），摘要如下：\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
