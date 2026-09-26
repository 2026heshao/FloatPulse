# -*- coding: utf-8 -*-
"""
====================================================================
单实例锁模块  -  SingleInstance
====================================================================
使用 Windows 内核互斥量 (CreateMutex) 实现单实例限制。
防止用户重复双击 exe 开出多个悬浮球。

设计要点：
  1. 调用 acquire() 尝试获取全局互斥量
  2. 返回 True 表示是首个实例，可以启动
  3. 返回 False 表示已有实例在运行
  4. 异常情况下放行，避免阻塞启动
  5. 当第二个实例启动时，通过命名事件通知首个实例显示窗口
     （解决悬浮球隐藏 + 主窗口关闭后无法重新唤起的问题）
====================================================================
"""

import ctypes
import sys


class SingleInstance:
    """Windows 互斥量单实例锁 + 命名事件唤醒"""

    # 互斥量/事件名称后缀：开发环境与打包环境区分，避免互相冲突
    _SUFFIX = "_pkg" if getattr(sys, 'frozen', False) else "_dev"
    # 互斥量名称（唯一标识本程序）
    _MUTEX_NAME = f"Global\\KnowledgeCardBall_SingleInstance_v1{_SUFFIX}"
    # 命名事件名称（用于通知首个实例显示窗口）
    _EVENT_NAME = f"Global\\KnowledgeCardBall_ShowSignal_v1{_SUFFIX}"

    def __init__(self):
        self._handle = None
        self._event_handle = None

    def acquire(self) -> bool:
        """
        尝试获取全局互斥量。
        返回 True 表示是首个实例，可以启动；
        返回 False 表示已有实例在运行。
        """
        try:
            ERROR_ALREADY_EXISTS = 183
            self._handle = ctypes.windll.kernel32.CreateMutexW(
                None, False, self._MUTEX_NAME
            )
            if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                return False
            return True
        except Exception:
            # 异常情况下放行，避免阻塞启动
            return True

    def create_event(self):
        """
        首个实例创建命名事件（用于接收第二个实例的唤醒信号）。
        在 acquire() 成功后调用。
        """
        try:
            # 创建手动重置事件，初始无信号
            self._event_handle = ctypes.windll.kernel32.CreateEventW(
                None, True, False, self._EVENT_NAME
            )
        except Exception:
            self._event_handle = None

    def check_signal(self) -> bool:
        """
        检查是否收到唤醒信号（非阻塞）。
        返回 True 表示收到信号，需要显示窗口。
        收到后自动重置事件状态。
        """
        if self._event_handle is None:
            return False
        try:
            # WaitForSingleObject(handle, 0) 非阻塞检查
            # 返回 WAIT_OBJECT_0 (0) 表示有信号
            result = ctypes.windll.kernel32.WaitForSingleObject(
                self._event_handle, 0
            )
            if result == 0:
                # 手动重置事件，需要手动 ResetEvent
                ctypes.windll.kernel32.ResetEvent(self._event_handle)
                return True
            return False
        except Exception:
            return False

    def wait_for_signal(self, stop_flag):
        """
        阻塞等待唤醒信号（事件驱动，替代轮询以降低空闲 CPU）。
        后台线程调用：阻塞直到事件变为有信号或 stop_flag 被置位。
        收到信号后重置事件并返回 True；被要求停止时返回 False。

        :param stop_flag: 一个 callable，返回 True 表示应停止等待（程序退出）
        """
        if self._event_handle is None:
            return False
        try:
            WAIT_OBJECT_0 = 0
            WAIT_FAILED = 0xFFFFFFFF
            INFINITE = 0xFFFFFFFF
            # 以 500ms 为粒度阻塞等待，便于及时响应退出请求
            while not stop_flag():
                result = ctypes.windll.kernel32.WaitForSingleObject(
                    self._event_handle, 500
                )
                if result == WAIT_OBJECT_0:
                    # 手动重置事件，收到后重置，避免重复触发
                    ctypes.windll.kernel32.ResetEvent(self._event_handle)
                    return True
                if result == WAIT_FAILED:
                    return False
            return False
        except Exception:
            return False

    @classmethod
    def signal_show(cls):
        """
        第二个实例调用：向首个实例发送唤醒信号，通知其显示窗口。
        """
        try:
            # OpenEvent 打开已存在的命名事件
            EVENT_MODIFY_STATE = 0x0002
            handle = ctypes.windll.kernel32.OpenEventW(
                EVENT_MODIFY_STATE, False, cls._EVENT_NAME
            )
            if handle:
                # SetEvent 设置事件为有信号状态
                ctypes.windll.kernel32.SetEvent(handle)
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def release(self):
        """释放互斥量和事件句柄"""
        try:
            if self._handle:
                ctypes.windll.kernel32.CloseHandle(self._handle)
                self._handle = None
            if self._event_handle:
                ctypes.windll.kernel32.CloseHandle(self._event_handle)
                self._event_handle = None
        except Exception:
            pass
