# -*- coding: utf-8 -*-
r"""
====================================================================
路径与屏幕辅助函数  -  app_paths
====================================================================
集中管理「获取程序根目录 / 解析数据根目录 / 查找图标文件 / 获取主屏几何」等
与运行环境（开发 / 便携 / 安装版）相关的公共辅助逻辑。

v2 起项目采用多版本目录结构（v1_baseline / v2 / shared），源码运行时
数据与资源统一指向 **项目根目录**（配置、碎片、笔记、知识库.docx、temp_assets）。

2026-09-27 起数据目录统一收纳进 **float_data/**（单一标记目录）。

2026-09-30 起（成熟化 2.2 数据目录与安装解耦）打包运行按 **双轨制** 解析
数据根目录——「程序根目录」与「数据根目录」自此是两个概念：

  - 源码运行   → 项目根 / float_data/（现状不变，开发流程不受影响）
  - 便携版     → frozen 且 exe 同目录存在 portable.marker（zip 组包时写入）
                 → exe 目录 / float_data/（解压即用、数据随身走，现状不变）
  - 安装版     → frozen 且无 marker（Inno 从 dist2 组包，天然没有）
                 → %APPDATA%\FloatPulse / float_data/
                 （数据与安装目录解耦：升级 / 重装换目录数据不跟随丢失；
                   APPDATA 缺失时退 LOCALAPPDATA，再退 exe 目录兜底）

程序自带资源跟随 get_base_dir()（exe 目录 / 项目根，plugins/、plugin_store/
等），float_data/ 下的运行数据跟随 get_data_root()（安装版不再位于安装
目录内）。旧数据的一次性迁移见 src/portable_migrate.py。
"""

import os
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect


# ---- 数据目录统一标记（唯一真相源：所有数据目录名从这里取） ----
DATA_DIR_NAME = "float_data"        # 数据根目录（json / app.log / temp_assets / 知识库.docx）
TEMP_ASSETS_DIRNAME = "temp_assets"  # 临时素材目录（位于 float_data/ 内）
DOCX_FILENAME = "知识库.docx"         # 知识库文件名（位于 float_data/ 内）

# ---- 双轨制（成熟化 2.2）----
# 便携版标记：空文件，与 FloatPulse.exe 同级（build_release.py 组 zip 时写入；
# 安装版从 dist2 组包天然没有）。frozen 且 exe 同目录存在它 = 便携版。
PORTABLE_MARKER_NAME = "portable.marker"
# 安装版数据根目录名：%APPDATA%\FloatPulse（与 Inno 的 AppName 保持一致）
INSTALL_APP_DIR_NAME = "FloatPulse"


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


# ---- 双轨制核心：便携判定与数据根目录解析（纯函数，可注入测试） ----

def is_portable_mode(*, frozen: bool, exe_dir: str) -> bool:
    """便携版判定（纯函数）：frozen 且 exe 同目录存在 portable.marker。

    frozen/exe_dir 由调用方注入（运行时包装见 _current_exe_dir），便于
    测试矩阵覆盖。源码运行恒为 False——源码模式行为与双轨制无关。
    """
    if not frozen or not exe_dir:
        return False
    return os.path.isfile(os.path.join(exe_dir, PORTABLE_MARKER_NAME))


def resolve_data_root(*, frozen: bool, exe_dir: str, env=None) -> str:
    r"""解析数据根目录（float_data/ 的父目录）——纯函数，可注入测试。

    - 源码（frozen=False）→ get_project_root()，现状不变
    - 便携（exe 目录有 marker）→ exe_dir，数据随 exe 走
    - 安装版 → %APPDATA%\<INSTALL_APP_DIR_NAME>；env 里 APPDATA 缺失时
      退 LOCALAPPDATA，再退 exe 目录兜底，保证任何环境都有可用路径

    env: None 时读 os.environ；传 dict（如 {"APPDATA": ...}）可注入测试。
    """
    if not frozen:
        return get_project_root()
    if is_portable_mode(frozen=True, exe_dir=exe_dir):
        return exe_dir
    env = os.environ if env is None else env
    base = env.get("APPDATA") or env.get("LOCALAPPDATA") or ""
    if base:
        return os.path.join(base, INSTALL_APP_DIR_NAME)
    return exe_dir or os.getcwd()  # exe 目录兜底（与便携目录同级仅是巧合路径）


def _current_exe_dir() -> str:
    """运行时 exe 所在目录（仅 frozen 下有意义；口径与 get_base_dir 一致）"""
    return os.path.dirname(sys.executable)


def _is_install_mode() -> bool:
    """运行时安装版判定：frozen 且 exe 同目录无 portable.marker。

    源码运行返回 False（源码模式不走安装版分支，行为与历史完全一致）。
    """
    if not getattr(sys, 'frozen', False):
        return False
    return not is_portable_mode(frozen=True, exe_dir=_current_exe_dir())


def get_data_root() -> str:
    r"""获取数据根目录（float_data/ 的父目录），按当前运行模式自动解析。

    源码 / 便携版 = 程序根目录（get_base_dir() 同值）；
    安装版 = %APPDATA%\FloatPulse。logger / TempAssetManager 等自行拼
    「base_dir + DATA_DIR_NAME」的调用方，在安装版应改传本函数返回值。
    """
    frozen = bool(getattr(sys, 'frozen', False))
    exe_dir = _current_exe_dir() if frozen else ""
    return resolve_data_root(frozen=frozen, exe_dir=exe_dir)


def get_base_dir() -> str:
    r"""获取程序根目录（打包时为 exe 所在目录，源码运行时为项目根目录）。

    注意：双轨制后安装版的数据根目录不再是本函数的返回值（数据在
    %APPDATA%\FloatPulse），运行数据相关路径请用 get_data_dir() /
    get_data_root()；本函数仅用于 plugins/、plugin_store/ 等程序自带资源。
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return get_project_root()


def get_data_dir(base_dir: str = None) -> str:
    r"""获取数据目录（float_data/），不存在则创建。

    base_dir: 程序根目录；缺省时自动解析（打包=exe 目录，源码=项目根）。
              **安装版模式下此参数被忽略**——数据根目录固定解析为
              %APPDATA%\FloatPulse，与安装目录解耦正是双轨制的目标本身。
    """
    if _is_install_mode():
        base_dir = get_data_root()
    elif base_dir is None:
        base_dir = get_base_dir()
    data_dir = os.path.join(base_dir, DATA_DIR_NAME)
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        pass  # 创建失败不阻塞，由调用方按各自容错策略处理
    return data_dir


def get_temp_assets_dir(base_dir: str = None) -> str:
    """获取临时素材目录（float_data/temp_assets/），不存在则创建。

    base_dir 语义同 get_data_dir（安装版模式下被忽略，走 %APPDATA%）。
    """
    data_dir = get_data_dir(base_dir)
    assets_dir = os.path.join(data_dir, TEMP_ASSETS_DIRNAME)
    try:
        os.makedirs(assets_dir, exist_ok=True)
    except OSError:
        pass
    return assets_dir


def get_docx_path(base_dir: str = None) -> str:
    """获取知识库.docx 完整路径（float_data/知识库.docx，不检查存在性）。

    base_dir 语义同 get_data_dir（安装版模式下被忽略，走 %APPDATA%）。
    """
    if _is_install_mode():
        base_dir = get_data_root()
    elif base_dir is None:
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

    图标属程序自带资源，跟随 exe 目录（不受双轨制数据重定向影响）。
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
