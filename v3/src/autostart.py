# -*- coding: utf-8 -*-
"""
开机自启管理：写入/读取 HKCU 注册表 Run 键，无需管理员权限。
- 打包运行：注册 exe 自身
- 源码运行：注册 pythonw.exe + knowledge_ball.py
"""

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "FloatPulse"


def _build_command() -> str:
    """构造自启命令行（带引号，防路径空格）"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # v3/
    script = os.path.join(app_dir, "knowledge_ball.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return '"%s" "%s"' % (pythonw, script)


def is_autostart_enabled() -> bool:
    """读取注册表判断当前是否已开启自启"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, _VALUE_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """开启/关闭自启；返回是否成功"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, _VALUE_NAME, 0, winreg.REG_SZ,
                                  _build_command())
            else:
                try:
                    winreg.DeleteValue(k, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
