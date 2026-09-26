# -*- coding: utf-8 -*-
"""
====================================================================
日志模块  -  AppLogger
====================================================================
统一的日志记录器，支持控制台 + 文件双输出。

设计要点：
  1. 日志文件自动创建在 data/app.log（运行时自动生成，无需打包）
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 日志文件超过 2MB 自动轮转，最多保留 3 个备份
  4. 单例模式，全局共享一个 logger 实例
  5. 捕获未处理异常并记录到日志
  6. 线程安全（logging 模块本身线程安全）

日志级别：
  - DEBUG:    调试信息
  - INFO:     正常运行信息（启动、关闭、关键操作）
  - WARNING:  警告（可恢复的异常、降级处理）
  - ERROR:    错误（影响功能但程序继续运行）
  - CRITICAL: 严重错误（可能导致程序退出）

使用方式：
  from src.logger import get_logger
  logger = get_logger()
  logger.info("程序启动")
  logger.error("文件读取失败", exc_info=True)
====================================================================
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler


# 单例 logger 实例
_logger_instance = None
_log_file_path = None


def init_logger(base_dir: str, level: int = logging.INFO) -> logging.Logger:
    """
    初始化全局日志记录器。

    参数：
      base_dir: 程序根目录（日志文件存放在 base_dir/data/app.log）
      level:    日志级别，默认 INFO

    返回：
      配置好的 logging.Logger 实例
    """
    global _logger_instance, _log_file_path

    # 日志文件路径：base_dir/data/app.log
    log_dir = os.path.join(base_dir, "data")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        pass  # 目录创建失败不阻塞，日志会降级到仅控制台输出
    _log_file_path = os.path.join(log_dir, "app.log")

    logger = logging.getLogger("SnippetFloat")
    logger.setLevel(level)
    # 清除已有 handler（防止重复初始化）
    logger.handlers.clear()

    # 日志格式
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ---- 控制台输出 ----
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # ---- 文件输出（轮转，单文件 2MB，保留 3 个备份）----
    try:
        file_handler = RotatingFileHandler(
            _log_file_path,
            maxBytes=2 * 1024 * 1024,   # 2MB
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # 文件创建失败（权限/磁盘满）→ 仅控制台输出，不阻塞启动
        logger.warning("日志文件创建失败，降级为仅控制台输出")

    # 防止日志向上传播到 root logger
    logger.propagate = False

    _logger_instance = logger
    return logger


def get_logger() -> logging.Logger:
    """
    获取全局 logger 实例。
    若未初始化，返回一个仅控制台输出的临时 logger。
    """
    global _logger_instance
    if _logger_instance is None:
        # 未初始化时返回临时 logger（仅控制台）
        _logger_instance = logging.getLogger("SnippetFloat")
        _logger_instance.setLevel(logging.INFO)
        if not _logger_instance.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                "%Y-%m-%d %H:%M:%S"
            ))
            _logger_instance.addHandler(handler)
    return _logger_instance


def get_log_file_path() -> str:
    """返回日志文件完整路径（未初始化返回空字符串）"""
    return _log_file_path or ""


def install_excepthook():
    """
    安装全局异常钩子，未捕获异常记录到日志并弹窗提示。
    应在程序启动早期调用（QApplication 创建后）。
    """
    logger = get_logger()

    def _hook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 正常退出
        if issubclass(exc_type, KeyboardInterrupt):
            sys.exit(0)
        # 记录到日志
        logger.critical(
            "未捕获异常",
            exc_info=(exc_type, exc_value, exc_tb)
        )
        # 尝试弹窗提示
        try:
            import traceback
            from PyQt6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                QMessageBox.critical(
                    None, "程序异常",
                    f"程序发生未捕获异常：\n\n{msg[-1500:]}\n\n"
                    f"日志已记录到：{get_log_file_path()}"
                )
        except Exception:
            pass

    sys.excepthook = _hook
