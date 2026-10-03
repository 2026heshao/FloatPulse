# -*- coding: utf-8 -*-
"""
====================================================================
外观总管  -  appearance
====================================================================
把「强调色」与「壁纸」两份 config 取值，收口成一条对外只有两个函数的通道。

为什么要单独一层：
  · **强调色**要把 config 值推进 `theme.set_accent()`，之后全仓数十处
    `get_colors()` 自动带上 —— 这意味着它必须在**任何取色之前**发生。
    入口散在 main_window / knowledge_ball / settings_panel 三处，各自
    手写一遍必然漏一二个。
  · **壁纸**是「文件 + 参数」两件事：文件名来自 config，绝对路径只在
    `wallpaper.resolve_path()` 里合法化了才给出去（目录穿越在这一层截住）。
  · 这一层刻意**不 import PyQt6**：它是纯「读配置 → 产出参数」，绘制由
    调用方各自完成（主窗走 GlassPanel，小卡片走自己的 paintEvent）。

调用纪律：任何「主题要重新应用」的时刻，第一步必须是
:func:`sync_theme_extras`，第二步才是 `get_colors()` —— 顺序反了会拿到
上一次的强调色。
"""

from __future__ import annotations

from src import theme
from src import wallpaper


def wallpaper_spec(config, base_dir=None) -> dict:
    """读 config 产出一份可直接喂给绘制层的壁纸参数。

    返回 dict 恒含 5 键：``name`` / ``mode`` / ``opacity`` / ``blur`` /
    ``veil`` / ``path``。``path`` 为空串表示「当前没有可用壁纸」，调用方
    据此决定要不要把自己画成半透明。
    """
    spec = wallpaper.sanitize_spec(
        name=config.get("wallpaper", ""),
        mode=config.get("wallpaper_mode", wallpaper.DEFAULT_MODE),
        opacity=config.get("wallpaper_opacity", wallpaper.DEFAULT_OPACITY),
        blur=config.get("wallpaper_blur", wallpaper.DEFAULT_BLUR),
        veil=config.get("wallpaper_veil", wallpaper.DEFAULT_VEIL),
    )
    spec["path"] = (wallpaper.resolve_path(spec["name"], base_dir)
                    if spec["name"] else "")
    return spec


def sync_theme_extras(config) -> dict:
    """把 config 里的强调色推进 theme 模块；顺带返回壁纸参数。

    ``config`` 需要支持 ``.get(key, default)`` —— ConfigManager 与
    测试替身（dict 包一层 get）都满足。
    """
    theme.set_accent(config.get("accent", theme.get_accent()[0]),
                     config.get("accent_custom", "") or "")
    return wallpaper_spec(config)
