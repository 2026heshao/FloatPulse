# -*- coding: utf-8 -*-
"""
====================================================================
日志模块  -  AppLogger
====================================================================
统一的日志记录器，支持控制台 + 文件双输出。

设计要点：
  1. 日志文件自动创建在 float_data/app.log（运行时自动生成，无需打包）
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 日志文件超过 2MB 自动轮转，最多保留 3 个备份
  4. 单例模式，全局共享一个 logger 实例
  5. 捕获未处理异常：完整堆栈写日志，弹窗只给摘要 + 日志路径（不泄露环境信息）
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
      base_dir: 程序根目录（日志文件存放在 base_dir/float_data/app.log）
      level:    日志级别，默认 INFO

    返回：
      配置好的 logging.Logger 实例
    """
    global _logger_instance, _log_file_path

    # 日志文件路径：base_dir/float_data/app.log（目录名以 app_paths 为唯一真相源）
    from src.app_paths import DATA_DIR_NAME
    log_dir = os.path.join(base_dir, DATA_DIR_NAME)
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
    安装全局异常钩子：**完整堆栈只写日志，弹窗只给可操作的摘要**。

    应在 QApplication 创建后、日志系统初始化后调用。设计约定（优化项 3.4）：
      1. 完整 traceback 的唯一记录点是日志文件（float_data/app.log）——
         用户报障时把日志附上即可，不必让堆栈显示在屏幕上
      2. 弹窗正文只说「出了什么错 + 完整信息在哪个文件」，不再把整段堆栈
         （含用户名、安装路径）贴到屏幕，避免用户截图/录屏外传时泄露环境信息
      3. 同一异常（类型 + 消息相同）在本次运行内只弹一次：未捕获异常多来自
         定时器回调，不去重会变成「弹窗风暴」，反而让程序没法用
    """
    logger = get_logger()
    shown = set()          # 已弹过的摘要集合（仅本次运行有效，不落盘）

    def _hook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 正常退出
        if issubclass(exc_type, KeyboardInterrupt):
            sys.exit(0)
        # 完整堆栈 → 日志（这是唯一保留完整信息的地方）
        logger.critical(
            "未捕获异常",
            exc_info=(exc_type, exc_value, exc_tb)
        )
        # 弹窗：仅摘要 + 日志路径
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is None:
                return
            summary = f"{exc_type.__name__}: {exc_value}".strip()
            if len(summary) > 200:
                summary = summary[:200] + "…"
            if summary in shown:
                return
            shown.add(summary)

            log_path = get_log_file_path() or "float_data/app.log"
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("程序异常")
            box.setText("程序遇到一个未处理的错误，已捕获，程序会继续运行。")
            box.setInformativeText(
                f"{summary}\n\n"
                f"完整错误信息已记录到日志：\n{log_path}"
            )
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
        except Exception:
            pass

    sys.excepthook = _hook
