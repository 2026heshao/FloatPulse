# -*- coding: utf-8 -*-
"""小卡片碎片页（UI 重构 06）纯逻辑护栏。

只测不依赖 QApplication 的部分：
  · 时间切分 ``_fragment_time_text``（与主窗口碎片工作台同口径）
  · 类别取色单一真相源（classifier.CATEGORY_TOKENS）
  · ``_card_shell_qss`` 生成的圆点规则 / 页脚钮 / 页眉页脚壳
交互层（单击复制、页脚动作、拖动不误复制）见
``tools/verify_card_fragment_page.py``（离屏，需要 QApplication）。
"""
import pytest

from src.card_window import (
    FRAG_DOT_SIZE, FRAG_FOOT_HEIGHT, FRAG_HEAD_HEIGHT, FRAG_ROW_HEIGHT,
    _card_shell_qss, _fragment_time_text,
)
from src.fragment_classifier import CATEGORY_ORDER, CATEGORY_TOKENS
from src.theme import THEMES, get_colors


# ---------------- 时间切分 ----------------
@pytest.mark.parametrize("raw,expect", [
    ("2026-10-02 09:41:00", "09:41"),
    ("2026-10-02 09:41", "09:41"),
    ("2026-10-02 23:59:59.123", "23:59"),
    ("", ""),
    (None, ""),
    ("2026-10-02", ""),          # 没有时分 → 空串（与主窗口同口径）
    ("随便一段没有时间的文本", ""),   # 退化分支：不足 11 字符 → 空串
    ("2026-10-02 09", "09"),      # 退化分支：取下标 11 之后
])
def test_fragment_time_text(raw, expect):
    assert _fragment_time_text(raw) == expect


def test_fragment_time_text_never_raises():
    """任意输入不得抛异常（数据层脏值 / 超短串是高危输入）"""
    for raw in ("x", "2026", 123, None, "2026-10-02 09:4"):
        assert isinstance(_fragment_time_text(raw), str)


# ---------------- 尺寸常量（对齐设计稿 CSS px）----------------
def test_fragment_shell_heights():
    assert (FRAG_HEAD_HEIGHT, FRAG_FOOT_HEIGHT, FRAG_ROW_HEIGHT) == (36, 34, 26)
    assert FRAG_DOT_SIZE == 5


# ---------------- 类别取色 ----------------
def test_category_tokens_cover_every_category():
    """每个合法类别都要有主题 token，且 token 在两套主题里都存在"""
    for cat in CATEGORY_ORDER:
        token = CATEGORY_TOKENS.get(cat)
        assert token, f"类别 {cat} 缺 token 映射"
        for theme in ("light", "dark"):
            assert token in get_colors(theme), f"{theme} 缺 token {token}"


def test_category_tokens_single_source():
    """碎片工作台保留的私有别名必须就是 classifier 的那一张表（防两份漂移）"""
    from src.fragments_panel import _CATEGORY_TOKENS
    assert _CATEGORY_TOKENS is CATEGORY_TOKENS


def test_category_tokens_values_are_stable():
    """钉死色系语义：链接=蓝 / 代码=主色 / 路径=橙 / 命令=红 / 文本=次级灰"""
    assert CATEGORY_TOKENS == {
        "link": "link", "code": "primary", "path": "warn",
        "command": "danger", "text": "text_secondary",
    }


# ---------------- 生成 QSS ----------------
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_qss_has_dot_rule_per_category(theme):
    colors = get_colors(theme)
    qss = _card_shell_qss(colors)
    for cat in CATEGORY_ORDER:
        sel = f'QLabel#fragCatDot[cat="{cat}"]'
        assert sel in qss, f"{theme}: 缺 {cat} 的圆点规则（会退化成灰点）"
        assert f"background-color: {colors[CATEGORY_TOKENS[cat]]};" in qss


def test_qss_has_unknown_category_fallback():
    """未知类别兜底色：规则在，颜色是 text_secondary（不能是透明）"""
    colors = get_colors("light")
    qss = _card_shell_qss(colors)
    assert "QLabel#fragCatDot {" in qss
    head = qss.split("QLabel#fragCatDot {", 1)[1].split("}", 1)[0]
    assert f"background-color: {colors['text_secondary']};" in head


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_qss_has_fragment_shell_rules(theme):
    """页眉 / 行 / 页脚三段壳 + 页脚裸露图标钮：规则一条都不能少"""
    qss = _card_shell_qss(get_colors(theme))
    for sel in ("QWidget#miniHead", "QLabel#miniHeadTitle", "QLabel#miniHeadCount",
                "QWidget#fragItemRow", 'QWidget#fragItemRow[selected="true"]',
                "QLabel#fragPreview", "QLabel#fragTime",
                "QWidget#miniFoot", "QLabel#miniFootHint",
                "QPushButton#fragEditBtn:focus"):
        assert sel in qss, f"{theme}: 缺规则 {sel}"
    # 页脚三钮常态必须是透明底（不再是行内彩色小胶囊）
    block = qss.split("QPushButton#fragCopyBtn, QPushButton#fragDelBtn,", 1)
    assert len(block) == 2, "页脚钮选择器组不见了"
    assert "background-color: transparent;" in block[1].split("}", 1)[0]


def test_qss_row_has_static_transparent_left_border():
    """常态左边框恒为 2px 透明 —— 选中态只换颜色，文字横向不跳动"""
    colors = get_colors("light")
    qss = _card_shell_qss(colors)
    assert "border-left: 2px solid transparent;" in qss
    assert f"border-left: 2px solid {colors['primary']};" in qss


def test_qss_uses_line_not_primary_for_fragment_hairlines():
    """页眉/页脚分隔线用中性 $line（设计稿），不是泛绿的 $primary_a30"""
    colors = get_colors("light")
    qss = _card_shell_qss(colors)
    assert f"border-bottom: 1px solid {colors['line']};" in qss
    assert f"border-top: 1px solid {colors['line']};" in qss


def test_theme_dicts_own_the_tokens_used_by_shell():
    """生成 QSS 用到的 token 必须在两套主题里都有（substitute/取值不落空）"""
    need = ["surface_2", "surface_3", "line", "text", "text_secondary",
            "text_placeholder", "primary", "accent_soft", "danger", "link",
            "warn", "focus_ring", "fs_sm", "fs_xs", "r_ctl"]
    for theme in ("light", "dark"):
        colors = get_colors(theme)
        missing = [k for k in need if k not in colors]
        assert not missing, f"{theme} 缺 {missing}"
        assert set(THEMES[theme]) >= set(need)
