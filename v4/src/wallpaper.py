# -*- coding: utf-8 -*-
"""
====================================================================
壁纸管理模块  -  wallpaper
====================================================================
自定义背景图的**文件侧**逻辑：存放目录、导入去重、删除、参数收敛。

与本项目其它数据模块同一口径：
  · **纯 Python，禁 import PyQt6** —— 判定图片能不能读交给 UI 层的
    QPixmap/QPixmapReader；本模块只负责「文件放哪、叫什么、怎么清理」，
    因此可以在无 GUI 环境下直接单测。
  · 目录真相源是 `app_paths.get_backgrounds_dir()`（跟 `float_data` 走，
    便携版/安装版各自落位），本模块不另起一套路径逻辑。

放进 `float_data/backgrounds/`，而不是只存原始路径：
  用户在桌面挑一张图，几天后删了——只存路径就会出现「设置还在、图没了」。
  导入即复制到数据目录，原图此后与本程序无关。
"""

from __future__ import annotations

import hashlib
import os
import shutil

from src.app_paths import get_backgrounds_dir

# ====================================================================
# 参数约定（UI 与绘制层共用的唯一口径）
# ====================================================================
DEFAULT_MODE = "cover"
DEFAULT_OPACITY = 100      # 图片自身不透明度（%）
DEFAULT_BLUR = 0           # 模糊强度（px，0=不模糊）
DEFAULT_VEIL = 82          # 主题色遮罩（%，越高越接近纯色底）

MAX_BLUR = 40
MAX_FILE_MB = 20           # 超过这个体积直接拒绝导入（加载会拖慢换页）

MODES = ("cover", "contain", "stretch", "tile", "center")
MODE_LABELS = {
    "cover":   "填充（裁切铺满）",
    "contain": "适应（完整显示）",
    "stretch": "拉伸（可能变形）",
    "tile":    "平铺",
    "center":  "居中（原始大小）",
}

SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

# 常见图片格式的魔数前缀（够用来挡住「改名成 .png 的 txt」）
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"BM", ".bmp"),
    (b"RIFF", ".webp"),   # RIFF....WEBP
)


def is_supported_path(path) -> bool:
    """按扩展名粗筛（真正的解码能力由 UI 层兜底）"""
    if not isinstance(path, str) or not path:
        return False
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXT


def _sniff_ext(path: str) -> str:
    """读魔数猜真实格式；认不出来返回空串"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        return ""
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    return ""


# ====================================================================
# 参数收敛
# ====================================================================
def sanitize_spec(name=None, mode=None, opacity=None, blur=None,
                  veil=None) -> dict:
    """把任意输入收敛成合法的壁纸参数组（永远返回一个完整 dict）。

    手改 config.json 塞垃圾值时也不应炸 UI：非法项各自回落默认值，
    而不是整体拒绝。
    """
    def _pct(value, default):
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, min(100, v))

    clean_name = ""
    if isinstance(name, str) and name.strip():
        clean_name = safe_name(name.strip())

    clean_mode = mode if mode in MODES else DEFAULT_MODE

    return {
        "name":    clean_name,
        "mode":    clean_mode,
        "opacity": _pct(opacity, DEFAULT_OPACITY),
        "blur":    max(0, min(MAX_BLUR, _int_or(blur, DEFAULT_BLUR))),
        "veil":    _pct(veil, DEFAULT_VEIL),
    }


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_name(name) -> str:
    """剥掉一切路径成分，只留纯文件名（防目录穿越）。

    ``../../evil.png`` / ``C:\\x\\y.png`` 进来都只剩 ``evil.png``；
    且调用方只会拿到 `list_images()` 里存在的名字，双重保险。
    """
    if not isinstance(name, str):
        return ""
    return os.path.basename(name.replace("\\", "/").strip())


# ====================================================================
# 文件管理
# ====================================================================
def list_images(base_dir=None) -> list:
    """列出壁纸目录里的全部图片文件名（按名字排序，不含子目录）"""
    directory = get_backgrounds_dir(base_dir)
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    return sorted(n for n in entries
                  if os.path.isfile(os.path.join(directory, n))
                  and is_supported_path(n))


def resolve_path(name, base_dir=None) -> str:
    """文件名 → 绝对路径；不存在/非法一律返回空串。

    刻意要求 ``name`` 必须出现在 `list_images()` 里（等同目錄白名单），
    而不是直接 join —— 这样即使将来有人绕过了 safe_name 也读不到外层文件。
    """
    clean = safe_name(name)
    if not clean or clean not in list_images(base_dir):
        return ""
    return os.path.join(get_backgrounds_dir(base_dir), clean)


def import_image(src_path, base_dir=None) -> tuple:
    """把一张外部图片复制进壁纸目录，返回 ``(文件名, 错误信息)``。

    - 成功：``("ab12cd34ef56.png", "")``
    - 失败：``("", "不支持的格式")`` 一类可读原因，由 UI 直接展示

    文件名 = 内容 sha1 前 12 位 + 真实扩展名 → 同一张图重复导入零冗余，
    内容变了自然会落到新文件。
    """
    if not isinstance(src_path, str) or not src_path:
        return ("", "路径为空")
    if not os.path.isfile(src_path):
        return ("", "文件不存在")
    try:
        size_mb = os.path.getsize(src_path) / (1024.0 * 1024.0)
    except OSError:
        return ("", "无法读取文件")
    if size_mb > MAX_FILE_MB:
        return ("", f"文件过大（{size_mb:.1f}MB，上限 {MAX_FILE_MB}MB）")

    ext = _sniff_ext(src_path)
    if not ext:
        return ("", "不是有效的图片文件")

    try:
        digest = hashlib.sha1()
        with open(src_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ("", "无法读取文件内容")

    filename = digest.hexdigest()[:12] + ext
    target = os.path.join(get_backgrounds_dir(base_dir), filename)
    if not os.path.isfile(target):
        try:
            shutil.copy2(src_path, target)
        except OSError as exc:
            return ("", f"复制失败：{exc.strerror or exc}")
    return (filename, "")


def remove_image(name, base_dir=None) -> bool:
    """删除一张壁纸；不存在返回 False（幂等，不抛异常）"""
    path = resolve_path(name, base_dir)
    if not path:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def prune_unused(keep_names, base_dir=None) -> int:
    """清掉不在 ``keep_names`` 里的壁纸，返回删除数量。

    给「设置页图片列表」用：用户反复导入又不删，目录会攒垃圾。
    """
    keep = {safe_name(n) for n in (keep_names or ())}
    removed = 0
    for name in list_images(base_dir):
        if name not in keep and remove_image(name, base_dir):
            removed += 1
    return removed
