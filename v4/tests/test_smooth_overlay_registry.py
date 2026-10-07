# -*- coding: utf-8 -*-
"""
登记完整性护栏  -  test_smooth_overlay_registry
====================================================================
背景（按钮交互反馈系统化升级 U5，2026-10-07，规格文档 §2 G7 / §7）：

    SmoothButton 的 hover/press 端点色由 objectName 在
    ``controls._SMOOTH_OVERLAYS`` 里解析，未收录的名字掉进 ``None`` 键
    兜底（primary_hover 实底）—— 新增一个按钮名漏登记，hover 就会
    「变绿块」，fragEditBtn 踩过一次（2026-10）。这类回归是**静默**
    的：QSS 写了、控件建了，唯独登记表没人提醒。

本文件把对账钉成红灯：

  A. 正向对账：theme.py 四份 QSS（主窗/卡片 × light/dark）里出现的
     每一条 ``QPushButton#name:hover`` 选择器，name 必须在
     _SMOOTH_OVERLAYS 里**显式**收录 —— 连「有意走兜底」的（primaryBtn）
     也要显式登记（值与兜底一致，行为零变化），让每一次兜底都是
     拍板过的，而不是漏配的。
  B. 自检：扫描器必须真的扫得到（连一条 hover 都扫不到 = 口径失效，
     等价假护栏）。

假护栏识别口径（护栏必须能红）：临时摘掉登记表任意一个键（如
``_SMOOTH_OVERLAYS.pop("stepBtn")``）→ test_every_button_hover_selector_
is_registered 当场红灯；还原即绿。该反向验证已人工执行过一次
（2026-10-07 落地时验证，见 CHANGELOG 对应条目）。
====================================================================
"""

import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.controls import _SMOOTH_OVERLAYS  # noqa: E402
from src.theme import get_card_window_qss, get_main_window_qss  # noqa: E402

# 与 test_theme_contrast / test_smooth_buttons 同口径的 QSS 粗切分正则
_RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}")
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# 只对账 QPushButton 的 :hover（QWidget#navSplitHandle:hover、
# QFrame#navRow:hover、QWidget#fragItemRow / #assetItem:hover 这些
# 非按钮 hover、以及 QToolButton#appLaunchBtn 完整 QSS 系不在
# SmoothButton overlay 体系管辖内，自然也不吃兜底绿块）
_HOVER_NAME_RE = re.compile(r"\bQPushButton#([A-Za-z0-9_]+):hover\b")


def _hover_selector_names(qss):
    """一份 QSS 里出现的全部 `QPushButton#name:hover` 的 name 集合。

    注释先剥再匹配：规则之间的说明注释会被正则切分器并进下一条规则
    的选择器组（theme.py 既有口径），不剥会把注释里的字样误当选择器。
    复合选择器按逗号拆开逐段匹配。
    """
    names = set()
    for m in _RULE_RE.finditer(qss):
        selector = _COMMENT_RE.sub("", m.group(1))
        for part in selector.split(","):
            hit = _HOVER_NAME_RE.search(part)
            if hit:
                names.add(hit.group(1))
    return names


def _all_hover_names():
    """四份 QSS 的并集（与 test_smooth_buttons 的 _ALL_QSS 同名单）。"""
    names = set()
    for get_qss in (get_main_window_qss, get_card_window_qss):
        for theme in ("light", "dark"):
            names |= _hover_selector_names(get_qss(theme))
    return names


# ====================================================================
# A. 正向对账：QSS 的每一条 QPushButton :hover 都必须已登记
# ====================================================================
def test_every_button_hover_selector_is_registered():
    names = _all_hover_names()
    assert names, (
        "QSS 扫描器失效：四份 QSS 里连一条 QPushButton#*:hover 都没扫到 —— "
        "扫描正则或 theme.py 结构变了，护栏已瞎，必须先修扫描器"
    )
    missing = sorted(name for name in names if name not in _SMOOTH_OVERLAYS)
    assert not missing, (
        "以下 QPushButton :hover 选择器未在 controls._SMOOTH_OVERLAYS 登记，"
        "hover 会掉进未命名兜底（primary_hover 实底绿块）——G7 静默视觉回归："
        "%s\n新增按钮必须在登记表显式收录；hover 背景仍归 QSS 的轻收编口径"
        "登记 (None, press端点)；有意走兜底的也要显式登记（值与兜底一致）。"
        % missing
    )


def test_scanner_sees_known_landmarks():
    """扫描器自检（防假护栏）：几个历史名点必须扫得到。"""
    names = _all_hover_names()
    for landmark in ("primaryBtn", "secondaryBtn", "iconBtn", "stepBtn",
                     "navBtn", "cardCloseBtn", "fragCopyBtn"):
        assert landmark in names, (
            "扫描器漏掉了已知锚点 %s —— 正则/切分口径变了，先修护栏" % landmark
        )


def test_registered_tokens_resolve_in_both_themes():
    """登记表扩容后（U5 新增 primaryBtn / backToLatestBtn），端点 token
    在 light/dark 都必须可解析 —— 兜底复用 test_smooth_buttons 的全表
    遍历，这里只钉 U5 两个新键本身。"""
    from src.theme import get_colors

    for name in ("primaryBtn", "backToLatestBtn"):
        assert name in _SMOOTH_OVERLAYS, "U5 显式登记键 %s 丢失" % name
    for theme in ("light", "dark"):
        colors = get_colors(theme)
        for token, _alpha in _SMOOTH_OVERLAYS["primaryBtn"]:
            assert token in colors
        for spec in _SMOOTH_OVERLAYS["backToLatestBtn"]:
            if spec is None:
                continue
            assert spec[0] in colors, (
                "backToLatestBtn 端点 %r 在 %s 主题不可解析" % (spec[0], theme)
            )


def test_explicit_fallback_entries_match_none_fallback():
    """「显式登记的兜底」值必须与未命名兜底一致（否则就不是零变化登记，
    而是一次未评审的视觉变更）。"""
    assert _SMOOTH_OVERLAYS["primaryBtn"] == _SMOOTH_OVERLAYS[None]
