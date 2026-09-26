# -*- mode: python ; coding: utf-8 -*-
"""
====================================================================
FloatPulse 打包配置（onedir）—— 完整版（含 hiddenimports / excludes / UPX 排除）
====================================================================
使用方式（在 v2/ 目录下执行，路径由 spec 内部自动定位）：

    cd v2
    pyinstaller ../shared/packaging/build.spec

打包产物（dist/ 目录）：
  ├── 生活悬浮球.exe        主程序
  ├── FloatPulse.ico        程序图标
  ├── 知识库.docx           知识库数据文件（需自备，放在 exe 同目录）
  └── _internal/            PyQt6 及其他依赖

设计要点：
  1. docx 与 ico 作为外部资源放在 exe 同目录，用户可自由替换 docx
  2. data/ 与 temp_assets/ 目录运行时自动创建，无需打包
  3. 控制台隐藏（console=False），双击 exe 不弹黑窗
  4. 单实例限制由程序内部 Windows mutex 实现
  5. 明确排除 tkinter 等未使用模块以减小体积
  6. 与 UPX 兼容性差的 DLL 已排除（压缩后加载异常）

兼容性说明（2026-09-22 修订）：
  本 spec 早期版本使用 `block_cipher` / `a.zipped_data` / `cipher=` /
  `win_no_prefer_redirects` / `win_private_assemblies` 等参数，这些在
  PyInstaller 6.x 已被移除（本机实测 PyInstaller 6.19.0），旧写法会直接
  报错无法打包。此处已按 6.x 的 API 重写。
  注意：本文件已按 6.x API 修正，但**尚未实际执行过打包验证**。
====================================================================
"""

import os

try:
    _SPEC_DIR = SPECPATH
except NameError:
    _SPEC_DIR = os.getcwd()

_ROOT = os.path.dirname(os.path.dirname(_SPEC_DIR))      # 项目根目录
_V2 = os.path.join(_ROOT, "v2")                           # v2 源码目录
_ICON = os.path.join(_ROOT, "shared", "assets", "FloatPulse.ico")
_DOCX = os.path.join(_ROOT, "知识库.docx")

a = Analysis(
    [os.path.join(_V2, "knowledge_ball.py")],
    pathex=[_V2],
    binaries=[],
    datas=[
        # (源文件, 目标目录)  '.' 表示 exe 同目录
        (_DOCX, '.'),
        (_ICON, '.'),
    ],
    hiddenimports=[
        # 显式声明可能被遗漏的模块
        'src.logger',
        'src.config',
        'src.task_manager',
        'src.note_manager',
        'src.fragment_manager',
        'src.clipboard_monitor',
        'src.docx_manager',
        'src.nav_manager',
        'src.temp_asset_manager',
        'src.single_instance',
        'src.card_window',
        'src.main_window',
        'src.theme',
        'src.app_paths',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的模块，减小体积
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'argparse',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='生活悬浮球',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                    # 使用 UPX 压缩，减小体积
    upx_exclude=[
        # UPX 压缩排除项（某些 dll 压缩后会出问题）
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'python3.dll',
    ],
    runtime_tmpdir=None,
    console=False,               # 隐藏控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,                  # 程序图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'python3.dll',
    ],
    name='生活悬浮球',
)
