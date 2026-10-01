# -*- coding: utf-8 -*-
"""会话标记（成熟化 3.2 残留部分）回归测试。

钉住的行为：
  1. mark_session_start / mark_session_end 走真实 logger（init_logger 注入
     tmp 路径）写出成对标记，圈出日志里的会话区间（人工排障用）
  2. mark_session_end 的日志必须先于其后写的退出日志（收尾排序契约）

（3.2 的 previous_session_abnormal 判定与「上次可能异常退出」托盘气泡
  已按用户要求移除：开发场景强杀退出是常态，判定恒真、每次启动必弹。）
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import logger as applog                                  # noqa: E402
from src.logger import mark_session_end, mark_session_start       # noqa: E402

START = applog._SESSION_START_MARK      # [会话] 启动
END = applog._SESSION_END_MARK          # [会话] 正常退出


class TestSessionMarksWithRealLogger:
    @pytest.fixture()
    def real_log_path(self, tmp_path):
        """init_logger 注入 tmp 目录 → 全局 logger 写 tmp_path/float_data/app.log"""
        logger = applog.init_logger(str(tmp_path), level=logging.INFO)
        yield applog.get_log_file_path()
        # 收尾：关闭文件句柄（Windows 下不关会占住 tmp 文件）
        for h in logger.handlers[:]:
            h.close()
            logger.removeHandler(h)

    def test_marks_roundtrip(self, real_log_path):
        """start 与 end 成对落盘，圈出会话区间"""
        mark_session_start()
        content = open(real_log_path, encoding="utf-8").read()
        assert f"{START} v" in content
        mark_session_end()
        assert END in open(real_log_path, encoding="utf-8").read()

    def test_start_line_carries_version(self, real_log_path):
        from src.app_version import APP_VERSION
        mark_session_start()
        content = open(real_log_path, encoding="utf-8").read()
        assert f"{START} v{APP_VERSION}" in content

    def test_end_before_other_exit_logs(self, real_log_path):
        """mark_session_end 的日志必须先于其后写的退出日志（收尾排序契约）"""
        mark_session_start()
        mark_session_end()
        get_logger = applog.get_logger()
        get_logger.info("程序准备退出（aboutToQuit 信号触发）")
        content = open(real_log_path, encoding="utf-8").read()
        assert content.rfind(END) < content.rfind("aboutToQuit")
