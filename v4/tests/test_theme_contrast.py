# -*- coding: utf-8 -*-
"""
主题对比度护栏测试  -  test_theme_contrast
====================================================================
背景（2026-09-23 实测 bug）：
    基础 ``QPushButton`` 用的是 ``background-color: $primary; color: white``。
    而深色主题 ``$primary = #6FFFE9`` 是极浅薄荷色 —— 白字对比度仅 **1.22:1**，
    浅色主题 ``#5BC0BE`` 也只有 2.16:1，任务页「＋ 添加」、笔记页「＋ 新建笔记」
    等按钮文字几乎看不清。

    根因不是某一条规则写错，而是**"主色底"天然是浅色，却按"主色即深色"的直觉
    配了白字**，同族错误一次散落 8 处（基础按钮 / primaryBtn / taskAddBtn /
    nextBtn / modeBtn:checked / tableOpenBtn:hover / navSiteBtn:hover /
    fragCopyBtn:hover）。

本测试钉死两件事：
    1. 主题词 ``on_primary`` / ``on_disabled`` 必须在 light、dark 两份字典里都存在
       —— QSS 用 ``Template.substitute``（严格模式），缺一个键就直接抛 KeyError
    2. 任何**浅色实底**（相对亮度 > LIGHT_BG_LIMIT）都不得配白色/近白文字
       —— 这条正好覆盖上面 8 处，又不会误伤深红底白字的 danger 按钮和
          语义上就该发灰的 disabled 态
"""

import re

import pytest

from src.theme import THEMES, get_card_window_qss, get_main_window_qss, get_menu_qss

# 超过这个相对亮度就算"浅底"，上面绝不能压白字
LIGHT_BG_LIMIT = 0.60
# 小于这个对比度算"文字看不清"（WCAG 正文下限 4.5，留一点余量取 4.0）
MIN_CONTRAST = 4.0

HEX_RE = re.compile(r"#([0-9a-fA-F]{6})")
GRAD_STOP_RE = re.compile(r"stop:\s*[0-9.]+\s*(#[0-9a-fA-F]{6})")
RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}")


def _srgb_to_lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g) + 0.0722 * _srgb_to_lin(b)


def _contrast(c1: str, c2: str) -> float:
    a, b = _lum(c1), _lum(c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _all_qss():
    for theme in ("light", "dark"):
        yield theme, "main_window", get_main_window_qss(theme)
        yield theme, "card_window", get_card_window_qss(theme)
        yield theme, "menu", get_menu_qss(theme)


# ★ 必须给显式短 id（2026-09-30，A4 焦点态时暴露）：
#   pytest 会把「完整 nodeid」写进 PYTEST_CURRENT_TEST 环境变量，而 nodeid 里
#   带着 parametrize id。原先直接把整份 QSS 当 id（几十 KB），QSS 一旦长过
#   32767 字符就撞上 Windows 环境变量上限 → **测试 body 全过、teardown 却
#   ValueError: the environment variable is longer than 32767 characters**。
#   表象是「断言没问题却报 error」，极易误判成代码 bug。
_QSS_CASES = list(_all_qss())
_QSS_IDS = ["%s-%s" % (theme, name) for theme, name, _ in _QSS_CASES]


def _rules(qss: str):
    """粗粒度切分 QSS 规则 -> (选择器, 声明体)"""
    for m in RULE_RE.finditer(qss):
        selector = " ".join(m.group(1).split())
        yield selector, m.group(2)


def _backgrounds(body: str) -> list:
    """取出该规则所有可能的实底色（纯色 + 渐变 stop），半透明/transparent 忽略"""
    bgs = []
    for m in re.finditer(r"background(?:-color)?\s*:\s*([^;]+);", body):
        value = m.group(1).strip()
        if "gradient" in value:
            bgs.extend(GRAD_STOP_RE.findall(value))
        elif HEX_RE.fullmatch(value):
            bgs.append(value)
        # rgba(...) / transparent / none 一律跳过：会与父级合成，无法静态判定
    return bgs


def _text_colors(body: str) -> list:
    out = []
    for m in re.finditer(r"(?:^|;|\s)color\s*:\s*([^;]+);", body):
        value = m.group(1).strip()
        if HEX_RE.fullmatch(value):
            out.append(value)
        elif value.lower() in ("white", "#fff", "#ffffff"):
            out.append("#FFFFFF")
        # rgba / 主题占位已替换完，其余（如 currentColor）忽略
    return out


# ====================================================================
# 1. 主题词完整性
# ====================================================================
@pytest.mark.parametrize("token", ["on_primary", "on_disabled", "secondary_text"])
def test_on_primary_tokens_exist_in_both_themes(token):
    """两份配色字典都要有 on_primary / on_disabled / secondary_text（substitute 严格模式）"""
    for theme in ("light", "dark"):
        assert token in THEMES[theme], (
            "%s 主题缺少主题词 %r —— QSS 用 Template.substitute，缺键会直接 KeyError"
            % (theme, token)
        )
        assert THEMES[theme][token], "%s 主题的 %r 不能为空" % (theme, token)


def test_on_primary_is_dark_enough_for_light_primary():
    """主色底上的文字必须与主色拉开对比（以防有人把它改回白色）"""
    for theme in ("light", "dark"):
        c = THEMES[theme]
        ratio = _contrast(c["on_primary"], c["primary"])
        assert ratio >= 4.5, (
            "%s 主题 on_primary(%s) 对 primary(%s) 对比度仅 %.2f:1，低于 4.5 —— "
            "主色底上的文字会看不清" % (theme, c["on_primary"], c["primary"], ratio)
        )


# ====================================================================
# 2. 通用护栏：浅底不得压白字
# ====================================================================
@pytest.mark.parametrize("theme,qss_name,qss", _QSS_CASES, ids=_QSS_IDS)
def test_no_white_text_on_light_background(theme, qss_name, qss):
    """浅色实底 + 白字 = 必然看不清（本次 bug 的通用形态）"""
    offenders = []
    for selector, body in _rules(qss):
        fgs = _text_colors(body)
        if not fgs:
            continue
        for bg in _backgrounds(body):
            if _lum(bg) <= LIGHT_BG_LIMIT:
                continue                      # 深底配白字，正确
            for fg in fgs:
                if _lum(fg) < 0.75:
                    continue                  # 深色文字，正确
                ratio = _contrast(fg, bg)
                if ratio < MIN_CONTRAST:
                    offenders.append(
                        "%s { background %s + color %s => %.2f:1 }"
                        % (selector, bg, fg, ratio)
                    )
    assert not offenders, (
        "%s/%s 存在「浅底 + 白字」的低对比组合：\n  %s\n"
        "修法：这些规则的文字色应改用 $on_primary" % (theme, qss_name, "\n  ".join(offenders))
    )


# ====================================================================
# 3. 定点回归：截图里那两个按钮（基础 QPushButton 样式）
# ====================================================================
def test_base_button_text_is_readable_in_both_themes():
    """基础按钮（任务页「＋ 添加」/ 笔记页「＋ 新建笔记」）文字对比度达标"""
    for theme in ("light", "dark"):
        c = THEMES[theme]
        for state in ("primary", "primary_hover", "primary_pressed"):
            ratio = _contrast(c["on_primary"], c[state])
            assert ratio >= 4.0, (
                "%s 主题：按钮 %s 态对比度仅 %.2f:1（底色 %s / 文字 %s）"
                % (theme, state, ratio, c[state], c["on_primary"])
            )


def test_danger_and_disabled_states_still_use_their_own_colors():
    """守卫改动边界：danger 按钮的 hover 白字、disabled 的灰字都不该被顺手改掉"""
    for theme in ("light", "dark"):
        qss = get_main_window_qss(theme)
        assert "QPushButton#dangerBtn:hover" in qss
        danger_body = dict(_rules(qss)).get("QPushButton#dangerBtn:hover", "")
        assert "#FFFFFF" in _text_colors(danger_body), (
            "danger 按钮 hover 应保持白字（深红底）"
        )

        disabled_body = dict(_rules(qss)).get("QPushButton:disabled", "")
        assert THEMES[theme]["on_disabled"].lower() in disabled_body.lower(), (
            "disabled 态应使用 on_disabled 主题词"
        )


# ====================================================================
# 4. 次按钮（secondaryBtn）文字对比度
# ====================================================================
# 次按钮底色是半透明 $primary_a12，静态拿不到合成后的实色。
# 这里按"叠加在卡片实底上"的合成色做估计（留了余量，宁可判严）。
# light: #FFFFFF + rgba(91,192,190,0.12)  ->  #E7F4F4
# dark : #232536 + rgba(111,255,233,0.12) ->  #31374A
_SECONDARY_BG = {
    "light": "#E7F4F4",
    "dark":  "#31374A",
}


def test_secondary_button_text_contrast_meets_wcag():
    """次按钮（淡青底 + 青字）文字对比度必须达标。

    2026-09-27：浅色主题原用 ``color: $primary``(#5BC0BE) 压在淡青底上，
    对比度仅 ~1.8:1，全项目所有次按钮看起来都像禁用态。改用 ``$secondary_text``
    （light 加深为 #27787A）后应显著拉开。
    """
    for theme in ("light", "dark"):
        c = THEMES[theme]
        fg = c["secondary_text"]
        bg = _SECONDARY_BG[theme]
        ratio = _contrast(fg, bg)
        assert ratio >= 4.0, (
            "%s 主题：次按钮文字 secondary_text(%s) 在淡青底(%s)上对比度仅 %.2f:1，"
            "低于 4.0 —— 次按钮会看起来像禁用态" % (theme, fg, bg, ratio)
        )


def test_secondary_button_qss_uses_secondary_text_token():
    """钉死 QSS 用的是 $secondary_text 而不是 $primary（防回退）"""
    for theme in ("light", "dark"):
        qss = get_main_window_qss(theme)
        rules = dict(_rules(qss))
        for sel in ("QPushButton#secondaryBtn", "QPushButton#secondaryBtn:checked"):
            body = rules.get(sel, "")
            assert body, "%s 主题缺少规则 %s" % (theme, sel)
            assert THEMES[theme]["secondary_text"].lower() in body.lower(), (
                "%s 主题 %s 的文字色应为 secondary_text(%s)，实际：%s"
                % (theme, sel, THEMES[theme]["secondary_text"], body.strip())
            )
            assert THEMES[theme]["primary"].lower() not in _text_colors(body), (
                "%s 主题 %s 不应再用 primary 作文字色" % (theme, sel)
            )
