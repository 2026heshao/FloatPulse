# -*- coding: utf-8 -*-
"""
====================================================================
剪贴板被动监听模块  -  ClipboardMonitor
====================================================================
监听系统剪贴板变化（QClipboard.dataChanged 信号），
将用户 Ctrl+C 复制的内容自动归一化进 FragmentManager。

设计要点：
  1. 被动监听，不引入全局鼠标/键盘钩子
  2. 自己写入剪贴板前调用 suppress_next() 标记，避免循环捕获
  3. 自动识别文件路径（os.path.exists() 为真 → 标记为 path 类型）
  4. 多文件复制时按行拆分，每行作为一条独立路径碎片
  5. 短时间内相同内容去重（防止部分应用重复触发 dataChanged）
  6. 通过 fragment_added 信号通知 UI 刷新
  7. 容量上限自动调用 FragmentManager.trim_to_max FIFO 淘汰
  8. 可通过 set_enabled(False) 临时关闭监听

不实现的功能（与方案一致）：
  - 不存图片缩略图（仅文本和路径）
  - 不做应用过滤（避免引入 GetForegroundWindow 复杂度）
====================================================================
"""

import os

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.fragment_manager import (
    FragmentManager,
    TYPE_CLIPBOARD_TEXT,
    TYPE_CLIPBOARD_PATH,
)


class ClipboardMonitor(QObject):
    """
    剪贴板被动监听器。

    信号：
      - fragment_added(int)  : 新增碎片时发射，参数为 fragment_id
      - path_detected(str)   : 检测到文件路径时发射（可用于UI提示）
    """

    fragment_added = pyqtSignal(int)
    path_detected = pyqtSignal(str)

    # 短时间内去重窗口（毫秒），同一内容在此间隔内不重复捕获
    DEDUP_INTERVAL_MS = 800

    def __init__(self, fragment_manager: FragmentManager, config_manager=None):
        super().__init__()
        self._fm = fragment_manager
        self._config = config_manager
        self._clipboard = QApplication.clipboard()
        self._enabled = True
        # 自己写入时设为 True，下次 dataChanged 不处理
        self._suppress_flag = False
        # 上一次捕获的内容文本，用于去重
        self._last_text = ""
        # 监听是否已启动
        self._started = False

    # ---------------- 启停 ----------------
    def start(self):
        """开始监听剪贴板（连接 dataChanged 信号）"""
        if self._started:
            return
        if self._clipboard is not None:
            self._clipboard.dataChanged.connect(self._on_data_changed)
            self._started = True

    def stop(self):
        """停止监听（断开 dataChanged 信号）"""
        if not self._started:
            return
        if self._clipboard is not None:
            try:
                self._clipboard.dataChanged.disconnect(self._on_data_changed)
            except (TypeError, RuntimeError):
                pass
        self._started = False

    def set_enabled(self, enabled: bool):
        """启用/禁用捕获（不切断信号连接，仅不处理）"""
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    # ---------------- 自己写入剪贴板时调用 ----------------
    def suppress_next(self):
        """
        在调用 clipboard.setText() 之前调用本方法，
        下一次 dataChanged 信号将被忽略，避免循环捕获。
        """
        self._suppress_flag = True

    def put_text(self, text: str):
        """
        主动写入剪贴板文本（自动 suppress 下一次 dataChanged）。
        供「碎片合并 → 复制到剪贴板」使用。
        """
        if self._clipboard is not None:
            self.suppress_next()
            self._clipboard.setText(text)

    # ---------------- 信号处理 ----------------
    def _on_data_changed(self):
        """剪贴板变化回调"""
        # 抑制标志：本次是自己写入，跳过
        if self._suppress_flag:
            self._suppress_flag = False
            return
        if not self._enabled:
            return
        if self._clipboard is None:
            return

        try:
            text = self._clipboard.text()
        except Exception:
            return

        if not text:
            return

        # 去重：与上次相同则跳过
        if text == self._last_text:
            return
        self._last_text = text

        # 解析内容：判断是文件路径还是普通文本
        self._parse_and_add(text)

    def _parse_and_add(self, text: str):
        """
        解析剪贴板文本并加入碎片池。
        - 多行文本：检查每行是否为存在的文件路径
          - 全部都是路径 → 每行作为一条路径碎片
          - 部分是路径 → 整体作为一条文本碎片（不拆分）
          - 都不是路径 → 整体作为一条文本碎片
        """
        lines = text.splitlines()
        if len(lines) > 1:
            # 多行：检查是否全部是路径
            all_paths = all(self._is_valid_path(line) for line in lines if line.strip())
            if all_paths:
                # 每行作为一条路径碎片
                for line in lines:
                    line = line.strip()
                    if line:
                        self._add_path_fragment(line)
                return
        # 单行或非全路径：检查单行是否为路径
        single = text.strip()
        if self._is_valid_path(single):
            self._add_path_fragment(single)
        else:
            self._add_text_fragment(text)

    def _add_text_fragment(self, content: str):
        """添加文本碎片"""
        try:
            fid = self._fm.add_clipboard_text(content, source="剪贴板")
            self._trim_if_needed()
            self.fragment_added.emit(fid)
        except Exception:
            pass

    def _add_path_fragment(self, path: str):
        """添加路径碎片"""
        try:
            fid = self._fm.add_clipboard_path(path, source="剪贴板")
            self._trim_if_needed()
            self.fragment_added.emit(fid)
            self.path_detected.emit(path)
        except Exception:
            pass

    def _trim_if_needed(self):
        """检查碎片容量，超限时按 FIFO 淘汰"""
        if self._config is None:
            return
        max_items = self._config.get("clipboard_max_items", 200)
        try:
            self._fm.trim_to_max(int(max_items))
        except Exception:
            pass

    @staticmethod
    def _is_valid_path(text: str) -> bool:
        """判断文本是否为存在的文件/目录路径"""
        text = text.strip()
        if not text:
            return False
        # 去除可能的引号包裹
        if len(text) >= 2 and text[0] in '"\'' and text[-1] == text[0]:
            text = text[1:-1]
        if not text:
            return False
        try:
            return os.path.exists(text)
        except (OSError, ValueError):
            return False

    # ---------------- 工具 ----------------
    def reset_last_text(self):
        """重置去重缓存（切换主题/重新加载时调用）"""
        self._last_text = ""
