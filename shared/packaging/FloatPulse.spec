# -*- mode: python ; coding: utf-8 -*-
"""
====================================================================
FloatPulse 打包配置（onedir）—— 精简版
====================================================================
使用方式（在 v2/ 目录下执行，路径由 spec 内部自动定位，与 cwd 无关）：

    cd v2
    pyinstaller ../shared/packaging/FloatPulse.spec

打包产物：
    dist/FloatPulse/FloatPulse.exe     主程序
    dist/FloatPulse/FloatPulse.ico     程序图标（datas 带入 exe 同级）
    dist/FloatPulse/_internal/         PyQt6 等依赖

分发约定（与 README 一致）：
  1. 把整个 dist/FloatPulse 文件夹拷给用户
  2. exe 必须与「知识库.docx」同目录（用户自备）
  3. data/ 与 temp_assets/ 运行时自动创建在 exe 同级目录

设计要点：
  - onedir（目录）模式：启动快（1~2s），比 --onefile 更少触发杀软误报
  - 路径全部基于 SPECPATH 推导，spec 与源码分目录存放也不会错位
  - 旧版本里的绝对路径（D:/桌面/python案例/...）已失效，此处已修正
====================================================================
"""

import os

# spec 所在目录（PyInstaller 注入 SPECPATH；缺失时退回 cwd）
try:
    _SPEC_DIR = SPECPATH
except NameError:
    _SPEC_DIR = os.getcwd()

_ROOT = os.path.dirname(os.path.dirname(_SPEC_DIR))     # 项目根目录
_V2 = os.path.join(_ROOT, "v2")                          # v2 源码目录
_ICON = os.path.join(_ROOT, "shared", "assets", "FloatPulse.ico")

a = Analysis(
    [os.path.join(_V2, "knowledge_ball.py")],
    pathex=[_V2],
    binaries=[],
    datas=[(_ICON, ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FloatPulse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FloatPulse',
)
