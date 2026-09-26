# -*- mode: python ; coding: utf-8 -*-
"""
====================================================================
FloatPulse v3 打包配置（onedir + 轻量过滤）
====================================================================
使用方式（在项目根目录执行）：

    python -m PyInstaller --noconfirm --clean FloatPulse.spec

产物：
    dist/FloatPulse/FloatPulse.exe   主程序（含图标）
    dist/FloatPulse/_internal/       PyQt6 等依赖（已剔无用大件）
    打包后需手动补「知识库.docx」到 exe 同级（用户数据，外部可改）

轻量过滤说明（纯 Widgets 应用，全部冒烟兜底）：
    - opengl32sw.dll   19.7MB 软件 OpenGL 渲染器，仅 QML/3D 需要
    - Qt6Pdf.dll        5.1MB PDF 模块（Qt6Gui 若硬依赖则回滚保留）
    - Qt6Svg.dll+插件   0.7MB 项目无 svg 资源
    - translations      ~6MB Qt 翻译（UI 全自绘中文，不需要）
    - ssl/unicodedata   ~14MB 程序只用 socket 不用 ssl（lxml 用
      libcrypto-3.dll 旧命名版，不受 excludes 影响）
====================================================================
"""

import os

try:
    _SPEC_DIR = SPECPATH
except NameError:
    _SPEC_DIR = os.getcwd()

_ROOT = _SPEC_DIR
_V3 = os.path.join(_ROOT, "v3")
_ICON = os.path.join(_ROOT, "FloatPulse.ico")

a = Analysis(
    [os.path.join(_V3, "knowledge_ball.py")],
    pathex=[_V3],
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
