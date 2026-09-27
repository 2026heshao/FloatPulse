# -*- coding: utf-8 -*-
"""
====================================================================
路径与屏幕辅助函数  -  app_paths
====================================================================
集中管理「获取程序根目录 / 查找图标文件 / 获取主屏几何」等
与运行环境（开发 / PyInstaller 打包）相关的公共辅助逻辑。

v2 起项目采用多版本目录结构（v1_baseline / v2 / shared），源码运行时
数据与资源统一指向 **项目根目录**（配置、碎片、笔记、知识库.docx、temp_assets）。

2026-09-27 起数据目录统一收纳进项目根的 **float_data/**（单一标记目录）：

  - 打包运行（frozen）→ exe 所在目录下的 float_data/
  - 源码运行        → 项目根（向上查找，含 float_data 标记目录）
  - 知识库.docx     → float_data/ 知识库.docx（打包后放 exe 同级的 float_data/ 内）
"""

import os
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect


# ---- 数据目录统一标记（唯一真相源：所有数据目录名从这里取） ----
DATA_DIR_NAME = "float_data"        # 数据根目录（json / app.log / temp_assets / 知识库.docx）
TEMP_ASSETS_DIRNAME = "temp_assets"  # 临时素材目录（位于 float_data/ 内）
DOCX_FILENAME = "知识库.docx"         # 知识库文件名（位于 float_data/ 内）


# 项目根识别标记：某目录下存在任意一个同名子目录，即视为项目根
# （保留 data/shared 兼容旧结构回退定位）
_ROOT_MARKERS = ("float_data", "data", "shared")
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


def get_data_dir(base_dir: str = None) -> str:
    """获取数据目录（float_data/），不存在则创建。

    base_dir: 程序根目录；缺省时自动解析（打包=exe 目录，源码=项目根）。
    """
    if base_dir is None:
        base_dir = get_base_dir()
    data_dir = os.path.join(base_dir, DATA_DIR_NAME)
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        pass  # 创建失败不阻塞，由调用方按各自容错策略处理
    return data_dir


def get_temp_assets_dir(base_dir: str = None) -> str:
    """获取临时素材目录（float_data/temp_assets/），不存在则创建。"""
    data_dir = get_data_dir(base_dir)
    assets_dir = os.path.join(data_dir, TEMP_ASSETS_DIRNAME)
    try:
        os.makedirs(assets_dir, exist_ok=True)
    except OSError:
        pass
    return assets_dir


def get_docx_path(base_dir: str = None) -> str:
    """获取知识库.docx 完整路径（float_data/知识库.docx，不检查存在性）。"""
    if base_dir is None:
        base_dir = get_base_dir()
    return os.path.join(base_dir, DATA_DIR_NAME, DOCX_FILENAME)


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
