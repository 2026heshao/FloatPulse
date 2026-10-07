# -*- coding: utf-8 -*-
"""键盘焦点态（A4）护栏测试。

覆盖三层：
  1. **结构约束**（静态解析 QSS）：`:focus` 规则只许改 ``border`` / ``outline``，
     严禁出现 padding / margin / min-max 尺寸 —— 一旦出现，焦点态就会改盒模型，
     全项目的几何断言会跟着抖动。这是本项最容易翻车的地方，必须钉死。
  2. **对比度**：环色对"它压在什么底色上"必须 ≥ 3:1（WCAG 非文本前景下限）。
     $primary 在浅色主题面板底上只有约 2.1:1，正是本次引入 $focus_ring 的原因。
  3. **覆盖清单 + 离屏实渲染**：要求列出的控件都有焦点态；并真渲一张
     QPushButton:focus，断言边框像素真的变了（防"写了规则但 Qt 没认"）。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from src.theme import (  # noqa: E402
    THEMES, get_card_window_qss, get_main_window_qss,
)

# ---- 面板底的合成色估计（与 test_theme_contrast 同口径：半透明 panel_fill
#      叠在实底 card_bg_solid 之上；UI 重构 01 后 card_bg_solid = surface）----
_PANEL_BG = {"light": "#FFFFFF", "dark": "#34373C"}

RING_MIN_CONTRAST = 3.0

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}")

# 焦点态里禁止出现的、会改盒模型的属性
_FORBIDDEN_IN_FOCUS = ("padding", "margin", "min-width", "max-width",
                       "min-height", "max-height", "width", "height")

# 必须带焦点态的控件（A4 覆盖清单）
REQUIRED_MAIN = {
    "QPushButton:focus",
    "QPushButton#primaryBtn:focus",
    "QPushButton#secondaryBtn:focus",
    "QPushButton#iconBtn:focus",
    "QPushButton#navBtn:focus",
    "QPushButton#settingsNavBtn:focus",
    "QPushButton#dangerBtn:focus",
    "QPushButton#tableOpenBtn:focus",
    "QListWidget:focus",
    "QCheckBox::indicator:focus",
    "QLineEdit:focus",
    "QComboBox:focus",
    "QPlainTextEdit:focus",
    "QTextEdit:focus",
    # 交互状态批（2026-10-08，清单 B1/A1）：透明/浅底按钮逐枚登记
    # $focus_ring（此前落到通用兜底 $on_primary = 浅色主题的白 → 隐形）；
    # navGroupHeader 同批改 StrongFocus（A1）
    "QPushButton#pluginSegBtn:focus",
    "QPushButton#pluginErrorToggle:focus",
    "QPushButton#pluginMoreBtn:focus",
    "QPushButton#navGroupHeader:focus",
    # 交互状态批第二批（2026-10-08，清单 A2/B2）：自绘行 + 选择器命名收口
    "QFrame#navRow:focus",
    "QPushButton#pluginsPickBtn:focus",
}
REQUIRED_CARD = {
    "QPushButton#sideTabIconBtn:focus",
    "QPushButton#modeBtn:focus",
    "QPushButton#nextBtn:focus",
    "QPushButton#taskAddBtn:focus",
    "QPushButton#cardCloseBtn:focus",
    "QPushButton#navSiteCard:focus",
    "QPushButton#fragCopyBtn:focus",
    "QPushButton#fragDelBtn:focus",
    "QToolButton#appLaunchBtn:focus",
    "QLineEdit#taskInput:focus",
    "QTextEdit#noteEdit:focus",
    "QDateEdit#taskDate:focus",
    "QListWidget#taskList:focus",
    # 交互状态批第二批（2026-10-08，清单 E4/B2）：小卡片素材格三态收口
    "QWidget#assetItem:focus",
}

# 显式 NoFocus 的控件：不该有焦点态死规则（controls.Stepper 的 ± 钮）——
# 有的话说明有人误加了规则。
# （2026-10-08 清单 A1：navGroupHeader 已改 StrongFocus 并登记
#  :focus 规则，从本清单移入 REQUIRED_MAIN 覆盖清单。）
NO_FOCUS_DEAD_RULES = ("QPushButton#stepBtn:focus",)


# ====================================================================
# 解析工具
# ====================================================================
def _clean(qss: str) -> str:
    return _COMMENT_RE.sub("", qss)


def _focus_rules(qss: str):
    """产出 (单个选择器, 声明体)，只保留带 :focus 的"""
    for m in _RULE_RE.finditer(_clean(qss)):
        body = m.group(2)
        for sel in m.group(1).split(","):
            sel = " ".join(sel.split())
            if sel and ":focus" in sel:
                yield sel, body


def _selectors(qss: str) -> set:
    return {sel for sel, _ in _focus_rules(qss)}


def _border_widths(body: str) -> list:
    """取该规则里所有 border 简写的宽度（'none' 记为 0px）"""
    out = []
    for m in re.finditer(r"(?:^|;|\s)border\s*:\s*([^;]+);", body):
        value = m.group(1).strip()
        if value.startswith("none"):
            out.append(0)
            continue
        w = re.match(r"(\d+(?:\.\d+)?)px", value)
        if w:
            out.append(float(w.group(1)))
    return out


def _srgb_to_lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g)
            + 0.0722 * _srgb_to_lin(b))


def _contrast(c1: str, c2: str) -> float:
    a, b = _lum(c1), _lum(c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _all_qss():
    for theme in ("light", "dark"):
        yield theme, "main_window", get_main_window_qss(theme)
        yield theme, "card_window", get_card_window_qss(theme)


# ★ 必须给显式短 id：QSS 全文当 parametrize id 会长达上百 KB，pytest 会拿
#   它去 basetemp 下建每测试临时目录 → 路径超长，**在 setup 阶段就报 error**
#   （表象是「测试没跑就 error」，很容易误判成断言问题）。
_QSS_CASES = list(_all_qss())
_QSS_IDS = ["%s-%s" % (t, n) for t, n, _ in _QSS_CASES]
_QSS_PARAM = pytest.mark.parametrize("theme,qss_name,qss", _QSS_CASES,
                                     ids=_QSS_IDS)


# ====================================================================
# 1. 结构约束：焦点态不得改盒模型
# ====================================================================
@_QSS_PARAM
def test_focus_rules_never_touch_box_model(theme, qss_name, qss):
    offenders = []
    for sel, body in _focus_rules(qss):
        for prop in _FORBIDDEN_IN_FOCUS:
            if re.search(r"(?:^|;|\s)%s\s*:" % re.escape(prop), body):
                offenders.append("%s { %s }" % (sel, prop))
    assert not offenders, (
        "%s/%s 的焦点态改了盒模型属性，会在获得焦点时触发重排：\n  %s\n"
        "修法：焦点态只改 border-color" % (theme, qss_name, "\n  ".join(offenders)))


@_QSS_PARAM
def test_focus_border_width_is_always_one_px(theme, qss_name, qss):
    """边框宽度必须是 1px：>1px 会挤掉内容，等于改盒模型"""
    offenders = []
    for sel, body in _focus_rules(qss):
        for w in _border_widths(body):
            if w not in (0, 1):
                offenders.append("%s { %spx }" % (sel, w))
    assert not offenders, (
        "%s/%s 焦点态边框宽度不是 1px：\n  %s"
        % (theme, qss_name, "\n  ".join(offenders)))


@_QSS_PARAM
def test_focus_rules_kill_native_outline(theme, qss_name, qss):
    """自定义焦点环必须顺手关掉原生 outline，否则两层焦点框叠加"""
    missing = []
    for sel, body in _focus_rules(qss):
        if "::indicator" in sel:
            continue          # 子控件不画原生焦点框
        if "outline" not in body:
            missing.append(sel)
    assert not missing, (
        "%s/%s 以下焦点规则没有 outline:none，会与原生焦点框叠加：\n  %s"
        % (theme, qss_name, "\n  ".join(missing)))


@_QSS_PARAM
def test_no_unconditional_subcontrol_focus_form(theme, qss_name, qss):
    """禁止 ``Widget:focus::subcontrol`` 这种写法 —— 它会被 Qt 无条件应用。

    实测（2026-09-30 变体探针）：``QCheckBox:focus::indicator {border:3px}``
    对「从未聚焦」的复选框同样生效（未聚焦 210 像素、聚焦中 210 像素），
    等于把焦点态变成常态。正确写法是 ``QCheckBox::indicator:focus``
    （未聚焦 0 像素、聚焦中 128 像素）。

    这条极度反直觉（CSS 里两种写法都合法），必须靠护栏拦住 —— 否则下一个人
    照着「:focus 要写在前面」的直觉改回去，界面会静默退化成「所有复选框都高亮」。
    """
    offenders = []
    for sel, _ in _focus_rules(qss):
        if ":focus::" in sel:
            offenders.append(sel)
    assert not offenders, (
        "%s/%s 出现会被无条件应用的写法 ``Widget:focus::subcontrol``：\n  %s\n"
        "修法：改写成 ``Widget::subcontrol:focus``"
        % (theme, qss_name, "\n  ".join(offenders)))


# ====================================================================
# 2. 覆盖清单
# ====================================================================
def test_main_window_focus_coverage():
    for theme in ("light", "dark"):
        sels = _selectors(get_main_window_qss(theme))
        missing = REQUIRED_MAIN - sels
        assert not missing, "%s 主题主窗口缺焦点态：%s" % (theme, sorted(missing))


def test_card_window_focus_coverage():
    for theme in ("light", "dark"):
        sels = _selectors(get_card_window_qss(theme))
        missing = REQUIRED_CARD - sels
        assert not missing, "%s 主题卡片窗缺焦点态：%s" % (theme, sorted(missing))


def test_nofocus_widgets_have_no_dead_focus_rule():
    """NoFocus 的控件不该有焦点规则（否则是"看着有、其实永不触发"的假覆盖）"""
    for theme in ("light", "dark"):
        sels = _selectors(get_main_window_qss(theme))
        dead = [s for s in NO_FOCUS_DEAD_RULES if s in sels]
        assert not dead, "%s 主题存在永不触发的焦点规则：%s" % (theme, dead)


# ====================================================================
# 3. 环色对比度（≥3:1）
# ====================================================================
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focus_ring_token_exists(theme):
    assert THEMES[theme].get("focus_ring"), (
        "%s 主题缺少 focus_ring 主题词（QSS 用严格 substitute，缺键直接 KeyError）"
        % theme)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focus_ring_contrast_against_panel(theme):
    fg = THEMES[theme]["focus_ring"]
    bg = _PANEL_BG[theme]
    ratio = _contrast(fg, bg)
    assert ratio >= RING_MIN_CONTRAST, (
        "%s 主题焦点环 %s 在面板底 %s 上对比度仅 %.2f:1（下限 %.1f）——"
        "焦点环会看不见" % (theme, fg, bg, ratio, RING_MIN_CONTRAST))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focus_ring_matches_secondary_text(theme):
    """focus_ring 与 secondary_text 同值同因（UI 重构 01 后的锚）。

    历史锚（已退役）：旧浅青主色 #5BC0BE 对白面板底仅 2.1:1，彼时
    「$primary 单独当焦点环不够亮」成立，故本文件曾有
    test_primary_alone_would_be_insufficient 钉死 ratio < 3。
    UI 重构 01 把浅色主色改深（#0F6E56，对白底 6.2:1），那条前提消失，
    该断言按其自身注释的预告退役；focus_ring 保留独立 token（语义不与
    「次按钮文字色」绑死），本测试改为钉「两 token 同值」防漂移。
    """
    assert THEMES[theme]["focus_ring"] == THEMES[theme]["secondary_text"], (
        "%s 主题 focus_ring 与 secondary_text 应同值（同因：非文本前景在"
        "面板底上需 ≥3:1，见 A4）" % theme)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_accent_filled_button_focus_uses_on_primary(theme):
    """主色实底按钮的焦点环必须是 $on_primary（深墨），不能被改成 $primary"""
    qss = get_main_window_qss(theme)
    rules = dict(_focus_rules(qss))
    body = rules["QPushButton:focus"]
    assert THEMES[theme]["on_primary"].lower() in body.lower(), (
        "主色实底按钮的焦点环应为 on_primary(%s)，实际：%s"
        % (THEMES[theme]["on_primary"], body.strip()))
    assert THEMES[theme]["primary"].lower() not in body.lower(), (
        "主色实底按钮不该用 $primary 作焦点环（与底色同色 = 看不见）")


def test_carded_buttons_with_border_none_were_compensated():
    """#nextBtn / #taskAddBtn 原为 border:none，现在必须声明 1px 边框

    它们是把 padding 各减 1px 换来这个边框位的（padding+border 总量不变），
    否则焦点态一加边框就会撑大按钮。
    """
    for theme in ("light", "dark"):
        qss = _clean(get_card_window_qss(theme))
        rules = {" ".join(sel.split()): body
                 for sel, body in _RULE_RE.findall(qss)}
        for sel in ("QPushButton#nextBtn", "QPushButton#taskAddBtn"):
            body = rules[sel]
            widths = _border_widths(body)
            assert widths == [1.0], (
                "%s 的 %s 应声明 1px 边框，实际 %s"
                % (theme, sel, widths))


# ====================================================================
# 4. 离屏实渲染：确认 Qt 真的认了 :focus 规则
# ====================================================================
def _app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _has_color(img, x0, y0, x1, y1, target, tol=40):
    from PyQt6.QtGui import QColor
    want = QColor(target)
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            if (abs(c.red() - want.red()) < tol
                    and abs(c.green() - want.green()) < tol
                    and abs(c.blue() - want.blue()) < tol):
                return True
    return False


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_pushbutton_focus_border_renders(theme):
    """真渲一张 QPushButton#secondaryBtn，对焦前后抓边框像素"""
    from PyQt6.QtWidgets import QPushButton, QWidget

    app = _app()
    ring = THEMES[theme]["focus_ring"]
    w = QWidget()
    w.resize(200, 80)
    w.setStyleSheet(get_main_window_qss(theme))
    btn = QPushButton("次按钮", w)
    btn.setObjectName("secondaryBtn")
    btn.move(20, 20)
    btn.resize(120, 36)
    w.show()
    app.processEvents()
    # ★ 窗口 show 时 Qt 会把焦点自动给第一个可聚焦子控件（这里就是 btn），
    #   必须先显式清掉再抓"对焦前"基线，否则基线里已经带着焦点环
    btn.clearFocus()
    app.processEvents()
    assert not btn.hasFocus()

    # ★ 只扫按钮顶边那 6px 高的一条带：secondaryBtn 的**文字色**恰好就是
    #   $secondary_text，浅色主题下与 $focus_ring 同值 —— 把整块按钮圈进
    #   扫描区会因为文字命中而假阳性。文字在垂直中部，顶边一定是纯边框。
    strip = (30, 19, 130, 25)
    before = w.grab().toImage()
    assert not _has_color(before, *strip, ring), "对焦前不该有环色"

    btn.setFocus()
    app.processEvents()
    after = w.grab().toImage()
    assert _has_color(after, *strip, ring), (
        "%s 主题：secondaryBtn 获得焦点后没有画出焦点环（规则没生效）" % theme)

    btn.clearFocus()
    app.processEvents()
    w.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_checkbox_indicator_focus_renders_and_is_conditional(theme):
    """QCheckBox 的焦点环必须「有条件」—— 这条断言是 2026-09-30 翻车后的钉子。

    翻车记录：写成 ``QCheckBox:focus::indicator`` 时 Qt 会**无条件**应用，
    未聚焦的复选框也带着环。当时只断言了「聚焦后有环」→ 断言恒真，
    bug 完全漏掉（直到离屏脚本报出「失焦后环不消失」才暴露）。
    因此这里必须**同时**断言「未聚焦的框没有环」——只测聚焦态是不够的。
    """
    from PyQt6.QtWidgets import QCheckBox, QWidget

    app = _app()
    ring = THEMES[theme]["focus_ring"]
    host = QWidget()
    host.resize(260, 60)
    host.setStyleSheet(get_main_window_qss(theme))
    a = QCheckBox("未聚焦", host)
    a.move(16, 18)
    b = QCheckBox("聚焦中", host)
    b.move(140, 18)
    host.show()
    app.processEvents()
    a.clearFocus()
    b.clearFocus()
    app.processEvents()

    # 只扫 indicator 所在的左侧窄条（宽 20px）：环只有 1px，扫整块会掺进文字
    def strip(cb):
        return cb.x(), cb.y(), cb.x() + 20, cb.y() + cb.height()

    img = host.grab().toImage()
    assert not _has_color(img, *strip(a), ring, tol=30), (
        "未聚焦的复选框也有焦点环 —— 说明 QSS 用了会被无条件应用的 "
        "``:focus::indicator`` 写法，应改成 ``::indicator:focus``")

    b.setFocus()
    app.processEvents()
    assert b.hasFocus(), "offscreen 下复选框应能取得焦点"
    img2 = host.grab().toImage()
    assert _has_color(img2, *strip(b), ring, tol=30), "聚焦的复选框没有焦点环"
    assert not _has_color(img2, *strip(a), ring, tol=30), (
        "聚焦另一个框后，未聚焦的框也被画上了焦点环")
    host.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_toggleswitch_draws_focus_ring(theme):
    """自绘控件 ToggleSwitch 的焦点环（QSS 管不到，靠 paintEvent）"""
    from PyQt6.QtWidgets import QWidget

    from src.controls import ToggleSwitch

    app = _app()
    ring = THEMES[theme]["focus_ring"]
    # 必须是容器里的子控件：顶层窗口在 offscreen 下不一定被激活，
    # 焦点可能落不下去（子控件不受这个影响）
    host = QWidget()
    host.resize(120, 60)
    sw = ToggleSwitch(checked=False, theme=theme, parent=host)
    sw.move(20, 18)
    host.show()
    app.processEvents()
    # 同上：show 会给唯一可聚焦子控件自动聚焦，先清掉再抓基线
    sw.clearFocus()
    app.processEvents()
    assert not sw.hasFocus()

    before = sw.grab().toImage()
    assert not _has_color(before, 0, 0, sw.width(), sw.height(), ring, tol=30)

    sw.setFocus()
    app.processEvents()
    assert sw.hasFocus(), "offscreen 下子控件应能取得焦点"
    after = sw.grab().toImage()
    assert _has_color(after, 0, 0, sw.width(), sw.height(), ring, tol=30), (
        "%s 主题：ToggleSwitch 获得焦点后没有画出焦点环" % theme)
    host.close()
