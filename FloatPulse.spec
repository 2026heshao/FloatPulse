# -*- mode: python ; coding: utf-8 -*-
"""
====================================================================
FloatPulse v4 打包配置（onedir + 轻量过滤，2026-09-27 自 v3 切换）
====================================================================
使用方式（在项目根目录执行）：

    python -m PyInstaller --noconfirm --clean --distpath dist2 --workpath build2 FloatPulse.spec

产物：
    dist2/FloatPulse/FloatPulse.exe   主程序（含图标）
    dist2/FloatPulse/_internal/       PyQt6 等依赖（已剔无用大件）
    打包后需手动补「知识库.docx」到 exe 同级的 float_data/ 文件夹内
    （用户数据，外部可改；float_data/ 其余内容运行时自动生成）

轻量过滤说明（纯 Widgets 应用，全部冒烟兜底）：
    - opengl32sw.dll   19.7MB 软件 OpenGL 渲染器，仅 QML/3D 需要
    - Qt6Pdf.dll        5.1MB PDF 模块（Qt6Gui 若硬依赖则回滚保留）
    - Qt6Svg.dll+插件   0.7MB 项目无 svg 资源
    - translations      ~6MB Qt 翻译（UI 全自绘中文，不需要）
    - ssl/unicodedata   ~14MB 程序只用 socket 不用 ssl（lxml 用
      libcrypto-3.dll 旧命名版，不受 excludes 影响）

体积优化结论（2026-09-27 复核，优化调研清单 4.4）
--------------------------------------------------------------------
实测产物 75MB。构成（du -sm）：PyQt6 38MB / python312.dll 7MB / lxml 7MB /
OpenSSL 三件套合计 12MB（libcrypto-3-x64.dll 6MB + libcrypto-3.dll 5MB +
libssl-3-x64.dll 1MB）/ base_library.zip 2MB。

  1. 「再排一些标准库大件」这条路已经走完：实测 tkinter / unittest /
     email / http / xmlrpc / pydoc / doctest / lib2to3 / setuptools / numpy
     **一个都没被打进 dist**（PyInstaller 已自动排除），无可再排。
  2. strip 保持 False，且本项目**不该打开**：
     PyInstaller 6.19.0 的 building/utils.py 直接 shell 调用外部 strip
     （`cmd = ["strip", *strip_options, cached_name]`），未找到可执行文件时
     只 `logger.warning("Failed to run strip on %r!")` 并**保留原二进制**。
     本机 PATH 无 strip（MSYS2 也没装 binutils），CI 的 windows-latest 同理
     → 开了等于没开，白多一堆 warning；反过来，若某台机器恰好带 strip
     （部分 MSYS2 / Git Bash 环境），剥 Qt6*.dll、python312.dll 的符号
     有破坏运行时的实际风险。收益为零、风险非零，故不启用。
  3. 两个 libcrypto 命名变体不能删其一：libcrypto-3-x64.dll 服务于
     CPython 的 _hashlib/_ssl（本项目 hashlib.sha256 在用），
     libcrypto-3.dll 随 lxml 一起进包（python-docx 依赖 lxml）。
     两者消费者不同，都在用。【待核实】仅真机跑打包产物才能确认能否精简，
     本轮不动。
  4. optimize 由 0 调 1/2 只影响 PYZ（base_library.zip ≈1.3MB），
     收益 <0.5%，却会剥掉 docstring / assert，不划算。
  → 结论：维持现状，不再为体积改动打包配置。
====================================================================
"""

import os

try:
    _SPEC_DIR = SPECPATH
except NameError:
    _SPEC_DIR = os.getcwd()

_ROOT = _SPEC_DIR
_SRC = os.path.join(_ROOT, "v4")
_ICON = os.path.join(_ROOT, "FloatPulse.ico")

a = Analysis(
    [os.path.join(_SRC, "knowledge_ball.py")],
    pathex=[_SRC],
    binaries=[],
    datas=[(_ICON, ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["ssl", "unicodedata"],
    noarchive=False,
    optimize=0,
)

# ---- 轻量过滤（匹配 dist 内相对路径，小写 + 正斜杠归一） ----
_SKIP_PREFIXES = (
    "pyqt6/qt6/bin/opengl32sw.dll",
    "pyqt6/qt6/bin/qt6pdf.dll",
    "pyqt6/qt6/bin/qt6svg.dll",
    "pyqt6/qt6/plugins/iconengines",
    "pyqt6/qt6/plugins/imageformats/qsvg",
    "pyqt6/qt6/translations",
)


def _keep(dest_name: str) -> bool:
    low = dest_name.lower().replace("\\", "/")
    return not low.startswith(_SKIP_PREFIXES)


a.binaries = [b for b in a.binaries if _keep(b[0])]
a.datas = [d for d in a.datas if _keep(d[0])]

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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='FloatPulse',
)
