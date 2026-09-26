# -*- coding: utf-8 -*-
"""
====================================================================
路径与屏幕辅助函数  -  app_paths
====================================================================
集中管理「获取程序根目录 / 查找图标文件 / 获取主屏几何」等
与运行环境（开发 / PyInstaller 打包）相关的公共辅助逻辑。

v2 起项目采用多版本目录结构（v1_baseline / v2 / shared），源码运行时
数据与资源统一指向 **项目根目录**，两个版本共用同一份 data/
（配置、碎片、笔记、知识库.docx、temp_assets）：

  - 打包运行（frozen）→ exe 所在目录（与部署约定一致，不受目录结构影响）
  - 源码运行        → 向上查找项目根（含 data / v1_baseline / shared 的目录）
"""

import os
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect


# 项目根识别标记：某目录下存在任意一个同名子目录，即视为项目根
_ROOT_MARKERS = ("data", "v1_baseline", "shared")
# 向上查找的最大层级（src → 版本目录 → 项目根，留足余量）
_MAX_UP_LEVELS = 4


def get_project_root() -> str:
    """源码运行时定位项目根目录（两版共用的数据/资源目录）。

    从本文件所在目录逐级向上查找，命中 _ROOT_MARKERS 的目录即视为项目根；
    最多向上 4 级。找不到时回退为 src/ 的父目录（即当前版本目录），
    保证任何情况下都有可用路径，不抛异常。
    """
    here = os.path.dirname(os.path.abspath(__file__))      # .../<version>/src
    cur = here
    for _ in range(_MAX_UP_LEVELS):
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        cur = parent
        for marker in _ROOT_MARKERS:
            if os.path.isdir(os.path.join(cur, marker)):
                return cur
    return os.path.dirname(here)


def get_base_dir() -> str:
    """获取程序根目录（打包时为 exe 所在目录，源码运行时为项目根目录）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return get_project_root()


def find_icon_file() -> str:
    """在多个候选位置查找图标文件，返回找到的第一个有效路径（找不到返回空串）。

    打包（PyInstaller onedir）后 ico 可能位于：
      - sys._MEIPASS（_internal）
      - exe 同级 / exe 父目录
      - png 备用
    源码运行时：项目根的 shared/assets（v2 归档后的统一位置），
    并保留图标直放项目根的旧路径候选，兼容迁移前的目录布局。
    """
    candidates = []
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(os.path.join(meipass, "FloatPulse.ico"))
            candidates.append(os.path.join(meipass, "FloatPulse.png"))
        candidates.append(os.path.join(exe_dir, "FloatPulse.ico"))
        candidates.append(os.path.join(exe_dir, "FloatPulse.png"))
        candidates.append(os.path.join(exe_dir, "_internal", "FloatPulse.ico"))
        candidates.append(os.path.join(os.path.dirname(exe_dir), "FloatPulse.ico"))
    else:
        base = get_base_dir()
        candidates.append(os.path.join(base, "shared", "assets", "FloatPulse.ico"))
        candidates.append(os.path.join(base, "shared", "assets", "FloatPulse.png"))
        candidates.append(os.path.join(base, "FloatPulse.ico"))
        candidates.append(os.path.join(base, "FloatPulse.png"))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def get_screen_geometry() -> QRect:
    """获取主屏可用工作区。无屏幕环境回退到默认矩形，避免崩溃。"""
    screen = QApplication.primaryScreen()
    if screen is not None:
        return screen.availableGeometry()
    return QRect(0, 0, 1920, 1080)
