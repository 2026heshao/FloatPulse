# -*- coding: utf-8 -*-
"""崩溃可感知（成熟化 3.2）回归测试。

钉住的行为：
  1. previous_session_abnormal 判定口径（path 可注入，直接喂 tmp 文件）：
     - start 之后有 end → False；start 之后无 end → True
     - 空文件 / 文件不存在 / 无任何标记但日志非空 → False（无法归因不扰民）
     - 多轮会话只看**最后一条** start；轮转截断的误报边界见 logger docstring
  2. mark_session_start / mark_session_end 走真实 logger（init_logger 注入
     tmp 路径）写出标记，并与判定函数闭环
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import logger as applog                         # noqa: E402
from src.logger import (mark_session_end,                # noqa: E402
                        mark_session_start,
                        previous_session_abnormal)

# 真实格式的一行日志（判定只依赖标记子串，不解析整行）
def _line(mark, extra=""):
    return f"2026-09-30 10:00:00 [INFO] knowledge_ball.py:1 - {mark}{extra}\n"


START = applog._SESSION_START_MARK      # [会话] 启动
END = applog._SESSION_END_MARK          # [会话] 正常退出


@pytest.fixture()
def log_file(tmp_path):
    return tmp_path / "app.log"


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# ====================================================================
# 判定口径（注入 path，纯文件读写）
# ====================================================================
class TestPreviousSessionAbnormal:
    def test_start_then_end_ok(self, log_file):
        _write(log_file, _line(START, " v4.7.0") + _line(END))
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_start_without_end_abnormal(self, log_file):
        """有 start 无 end → 上次会话可能异常退出"""
        _write(log_file, _line(START, " v4.7.0") + _line("程序启动"))
        assert previous_session_abnormal(path=str(log_file)) is True

    def test_empty_file_false(self, log_file):
        log_file.write_text("", encoding="utf-8")
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_missing_file_false(self, tmp_path):
        assert previous_session_abnormal(path=str(tmp_path / "nope.log")) is False

    def test_empty_path_false(self):
        assert previous_session_abnormal(path="") is False
        assert previous_session_abnormal(path=None) is False

    def test_no_marks_but_content_false(self, log_file):
        """无任何标记但日志非空 → False（老版本日志无标记，升级首启不误报）"""
        _write(log_file, _line("程序启动") + _line("配置管理器初始化完成"))
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_only_last_start_counts(self, log_file):
        """多轮会话：最后一条 start 之后无 end → True（倒数第二轮正常不算）"""
        _write(log_file, _line(START, " v4.6.0") + _line(END)
               + _line(START, " v4.7.0"))
        assert previous_session_abnormal(path=str(log_file)) is True

    def test_end_after_last_start_counts(self, log_file):
        """start 在 end 之前的老会话不影响：最后一条 start 有 end 收尾 → False"""
        _write(log_file, _line(START, " v4.6.0") + _line(START, " v4.7.0")
               + _line(END))
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_trailing_noise_after_end_false(self, log_file):
        """end 之后的普通退出日志（收尾中出错等）仍属正常会话"""
        _write(log_file, _line(START, " v4.7.0") + _line(END)
               + _line("[退出] 资源收尾完成"))
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_start_mark_beyond_scan_window_false(self, log_file):
        """16KB 尾窗外的 start 视为「无标记」→ False（轮转翻走的会话不误报）"""
        _write(log_file, _line(START, " v4.7.0") + "x" * 20 * 1024)
        assert previous_session_abnormal(path=str(log_file)) is False

    def test_corrupt_bytes_do_not_crash(self, log_file):
        """日志里混入坏字节（errors=replace）不抛错，标记仍在 → True"""
        log_file.write_bytes(
            _line(START, " v4.7.0").encode("utf-8") + b"\xff\xfe garbage")
        assert previous_session_abnormal(path=str(log_file)) is True


# ====================================================================
# 标记函数与真实 logger 闭环
# ====================================================================
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
        """start → 判定 True（当前会话未收尾）；end → 判定 False"""
        mark_session_start()
        assert START in open(real_log_path, encoding="utf-8").read()
        assert previous_session_abnormal(path=real_log_path) is True
        mark_session_end()
        assert previous_session_abnormal(path=real_log_path) is False

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
