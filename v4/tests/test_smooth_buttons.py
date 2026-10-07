# -*- coding: utf-8 -*-
"""
丝滑化按钮回归  -  test_smooth_buttons
====================================================================
背景（UI 丝滑化清单 S2，2026-10-02）：

    Qt 的 QSS 不支持 CSS ``transition``，``:hover`` / ``:pressed`` 背景
    是一帧跳变。S2 把按钮的背景过渡收编进 ``controls.SmoothButton``：
    先画原生 QSS，再按 ``hp`` / ``pp`` 两个 0→1 属性叠加过渡色（端点
    对照被删的 QSS 旧值，契约记在 ``_SMOOTH_OVERLAYS``）；按下位移改走
    **绘制级** -1px 下沉 + 0.98 微缩。

本文件钉死三层契约：

  A. QSS 侧：命名按钮 ``:hover`` 不得再有 background-color（两套机制
     打架 = 叠色过头）；任何 QPushButton 规则不得再出现 ``margin-top:``
     声明（margin 位移 = 按下态 polish 撑大 sizeHint 并永久缓存，
     拖拽后行高 +1px 的根因，清单 §6.1 红线）。
  B. 映射侧：overlay 端点逐一可解析 —— token 在 light/dark 两主题都
     必须存在且是合法色（拼错 token 不能等到悬停时才黑块）；关键按钮
     的端点值钉死（防止顺手改动造成端点漂移）。
  C. 行为侧：enter/leave/press 驱动动画且**可打断重定向**（复用同一个
     QPropertyAnimation，不排队）；reduce_motion 开启时直接落终态；
     复合按钮（内嵌子控件）不做位移缩放（子控件不走 paintEvent，
     跟着缩会错位）；IconButton 继承 SmoothButton（一处改全收）。
====================================================================
"""

import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QEvent, QAbstractAnimation, QPoint, Qt
from PyQt6.QtGui import QColor, QEnterEvent, QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel

from src import motion
from src.controls import (
    IconButton, ScreenToast, SmoothButton, Stepper, ToggleSwitch,
    _OVERLAY_RADIUS, _SMOOTH_CHECKED, _SMOOTH_OVERLAYS,
)
from src.theme import get_card_window_qss, get_main_window_qss

RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)

_ALL_QSS = [
    get_main_window_qss("light"), get_main_window_qss("dark"),
    get_card_window_qss("light"), get_card_window_qss("dark"),
]


def _rules(qss):
    """粗粒度切分 QSS 规则 -> (选择器, 去注释声明体)，与 test_theme_contrast 同口径。"""
    for m in RULE_RE.finditer(qss):
        body = COMMENT_RE.sub("", m.group(2))
        yield " ".join(m.group(1).split()), body


def _rule_bodies():
    """四份 QSS 的选择器 -> 声明体并集（同名规则以先见者为准，主窗/卡片
    各管各的按钮，天然不撞）。"""
    out = {}
    for qss in _ALL_QSS:
        for sel, body in _rules(qss):
            out.setdefault(sel, body)
    return out


# ====================================================================
# A. QSS 侧契约
# ====================================================================
@pytest.mark.parametrize("qss_name,qss", [
    ("main_window", get_main_window_qss("light")),
    ("main_window_dark", get_main_window_qss("dark")),
    ("card_window", get_card_window_qss("light")),
    ("card_window_dark", get_card_window_qss("dark")),
], ids=["main", "main-dark", "card", "card-dark"])
def test_no_button_rule_uses_margin_displacement(qss_name, qss):
    """B2 红线：QPushButton 规则里不得再有 margin-top 声明（注释提及不算）。"""
    offenders = []
    for sel, body in _rules(qss):
        if ("QPushButton" in sel or "QToolButton" in sel) and \
                re.search(r"margin-top\s*:", body):
            offenders.append(sel)
    assert not offenders, (
        "%s 里 QPushButton 规则用了 margin-top 位移：%s —— 按下态 polish 会把 "
        "sizeHint 算大并永久缓存（拖拽后行高 +1px 的根因）。位移一律走 "
        "SmoothButton 的绘制级下沉" % (qss_name, offenders)
    )


def test_named_hover_rules_have_no_background_color_left():
    """S2 收编的命名按钮 :hover/:pressed 不得残留 background-color，
    否则 QSS 跳变 + overlay 插值两套机制打架（悬停会叠色过头）。"""
    # 列表行/分组头样式里只有 navBtn 是完整收编（hover 背景过渡也归
    # overlay）；navGroupHeader / helpTocItem / 插件三钮（pluginSegBtn /
    # pluginErrorToggle / pluginMoreBtn）按 2026-10-08 清单 C1 的轻收编
    # 口径：**hover 背景仍归 QSS、只收编按下反馈**（overlay 端点
    # (None, press)），故不在本清单；appLaunchBtn 是 QToolButton
    kept_no_bg = [
        "QPushButton#secondaryBtn:hover", "QPushButton#dangerBtn:hover",
        "QPushButton#iconBtn:hover",
        'QPushButton#iconBtn[danger="true"]:hover',
        "QPushButton#settingsNavBtn:hover", "QPushButton#stepBtn:hover",
        "QPushButton#stepBtn:pressed", "QPushButton#tableOpenBtn:hover",
        "QPushButton#sideTabIconBtn:hover",
        "QPushButton#cardCloseBtn:hover", "QPushButton#nextBtn:hover",
        "QPushButton#taskAddBtn:hover", "QPushButton#navSiteCard:hover",
        "QPushButton#fragCopyBtn:hover", "QPushButton#fragDelBtn:hover",
        "QPushButton#navBtn:hover",
    ]
    # 这些规则原本**只有**背景/margin 一个声明，收编后整条删除 ——
    # 规则若还在，说明有人把跳变加回来了
    deleted = [
        "QPushButton:pressed", "QPushButton#secondaryBtn:pressed",
        "QPushButton#primaryBtn:pressed", "QPushButton#iconBtn:pressed",
        "QPushButton#nextBtn:pressed", "QPushButton#navSiteCard:pressed",
    ]
    bodies = _rule_bodies()
    for sel in kept_no_bg:
        assert sel in bodies, "S2 收编的规则 %s 不见了（误删？）" % sel
        assert "background-color" not in bodies[sel], (
            "%s 仍带 background-color —— 会与 SmoothButton overlay 打架"
            % sel
        )
    for sel in deleted:
        assert sel not in bodies, (
            "规则 %s 应已整条删除（背景过渡归 SmoothButton，margin 位移是红线）"
            % sel
        )


def test_danger_and_close_hover_keep_white_text():
    """收编只动背景，不动文字：danger 系「实底充填」按钮 hover 白字必须仍在。

    2026-10 对比度修复（GlassMessageBox danger 按钮专项拍板）：``#dangerBtn``
    hover 端点改回 $danger_hover 实底（红底上 $danger 红字几乎不可读），
    文字同步改白 —— 与 cardCloseBtn / fragDelBtn 白字契约并轨。
    """
    bodies = _rule_bodies()
    for sel in ("QPushButton#cardCloseBtn:hover", "QPushButton#fragDelBtn:hover",
                "QPushButton#dangerBtn:hover"):
        assert "color: white" in bodies[sel], (
            "%s 丢失了 hover 白字（背景过渡收编时误删了文字契约）" % sel
        )


def test_dangerbtn_is_text_button_no_white_hover():
    """#dangerBtn 静止态仍是文字按钮（UI 重构 01 三级制）：hover 只许改文字
    白、不得自己写 background-color（实底过渡归 SmoothButton overlay）。"""
    bodies = _rule_bodies()
    assert "QPushButton#dangerBtn" in bodies
    # 静止态仍是 $danger 文字 + 透明底（红胶囊不得回归；
    # _rule_bodies 返回的是 $token 已替换后的 QSS，故取 light 主题色值比对）
    from src.theme import get_colors as _gc
    assert ("color: %s" % _gc("light")["danger"]
            in bodies["QPushButton#dangerBtn"])
    assert "color: white" in bodies["QPushButton#dangerBtn:hover"]
    assert "background-color" not in bodies["QPushButton#dangerBtn:hover"]
    # 2026-10 对比度修复：hover/press 端点 = $danger_hover 实底（白字才成立）
    assert _SMOOTH_OVERLAYS["dangerBtn"] == (
        ("danger_hover", 255), ("danger_hover", 255))


# ====================================================================
# B. overlay 端点映射契约
# ====================================================================
def test_overlay_tokens_resolve_in_both_themes():
    """每个 spec 的 token 在 light/dark 都要能解析成合法 QColor ——
    拼错 token 不允许等到运行期悬停才变成黑块。"""
    from src.theme import get_colors
    for theme in ("light", "dark"):
        colors = get_colors(theme)
        for name, specs in _SMOOTH_OVERLAYS.items():
            for spec in specs:
                if spec is None:
                    continue
                token, alpha = spec
                assert token in colors, (
                    "overlay %r 引用了不存在的主题词 %r（%s 主题）"
                    % (name, token, theme)
                )
                c = QColor(str(colors[token]))
                assert c.isValid(), (
                    "overlay %r 的 token %r 在 %s 主题下不是合法色：%r"
                    % (name, token, theme, colors[token])
                )
                assert 0 <= alpha <= 255


def test_key_overlay_endpoints_are_pinned():
    """端点值钉死：这些值 = 各按钮的视觉端点契约，
    改任何一个都意味着视觉端点漂移，必须过肉眼校验再改这里。"""
    assert _SMOOTH_OVERLAYS["secondaryBtn"] == (("primary", 18), None)
    # 2026-10 对比度修复：dangerBtn hover/press 端点 = $danger_hover 实底
    assert _SMOOTH_OVERLAYS["dangerBtn"] == (
        ("danger_hover", 255), ("danger_hover", 255))
    assert _SMOOTH_OVERLAYS["iconBtn"] == (("primary", 31), ("primary", 46))
    # 2026-10-02 悬停配色优化：modeBtn 常态是 ghost（$primary_a12），hover
    # 端点从 primary_hover 实底改淡染（46/77，与 stepBtn 同档）—— ghost→
    # 实底突跳 + 未选中态主色文字叠主色底都随此修复；tableOpenBtn 同理
    # 保持 ghost 语系（QSS :hover 只加深描边，见 theme.py）。
    assert _SMOOTH_OVERLAYS["modeBtn"] == (("primary", 46), ("primary", 77))
    assert _SMOOTH_OVERLAYS["tableOpenBtn"] == (("primary", 46), ("primary", 77))
    assert _SMOOTH_OVERLAYS["stepBtn"] == (("primary", 46), ("primary", 77))
    # S4：侧栏导航行（端点 = 被删的 navBtn:hover a08 / 按下 a18）
    assert _SMOOTH_OVERLAYS["navBtn"] == (("primary", 20), ("primary", 46))
    # 实底主色钮：hover/pressed 仍走 primary_hover/pressed 实底端点
    # （token 值已从「压暗」改「轻提亮」，见 test_theme_contrast 的
    # test_primary_hover_is_a_lift_not_a_darken）
    assert _SMOOTH_OVERLAYS["nextBtn"] == (
        ("primary_hover", 255), ("primary_pressed", 255))
    assert _SMOOTH_OVERLAYS["taskAddBtn"] == (
        ("primary_hover", 255), ("primary_pressed", 255))
    # 未命名按钮兜底（primaryBtn 等全局 QPushButton 用）= 同上
    assert _SMOOTH_OVERLAYS[None] == (
        ("primary_hover", 255), ("primary_pressed", 255))
    # S2 收编的按钮都要有圆角契约（overlay 形状跟 QSS border-radius 对齐）
    for name in ("iconBtn", "modeBtn", "nextBtn", "cardCloseBtn"):
        assert name in _OVERLAY_RADIUS
    # UI 重构 01：QSS 圆角四档收敛后全局按钮半径 9 → RADIUS_CTL(6)，
    # overlay 跟着走 constants.RADIUS_*（与 QSS 的 $r_ctl 同源）
    assert _OVERLAY_RADIUS.get("secondaryBtn", 9) == 6


# ====================================================================
# C. 行为（offscreen QApplication）
# ====================================================================
_APP = None


def _app():
    """取（必要时创建）QApplication，引用留模块级（test_icons.py 同款），
    防止 PyQt6 包装对象被回收后 QPixmap/qFatal 静默崩进程。"""
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def btn():
    _app()
    b = SmoothButton("确定")
    b.setObjectName("taskAddBtn")
    yield b
    b.deleteLater()


@pytest.fixture()
def restore_motion():
    yield
    motion.set_reduce_motion(False)
    SmoothButton.set_speed(1.0)
    ScreenToast.set_speed(1.0)


def _enter_event():
    return QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5))


def test_hover_glide_starts_and_can_be_redirected(btn, restore_motion):
    """enter 起动画 → 未完成就 leave = 同一条动画被重定向，不排队（§6.4）。"""
    btn.enterEvent(_enter_event())
    anim = btn._hover_anim
    assert anim is not None, "enter 应启动 hover 动画"
    assert anim.state() == QAbstractAnimation.State.Running
    assert anim.endValue() == 1.0
    assert anim.duration() == motion.eased_ms("fast")
    anim.setCurrentTime(anim.duration())      # 时间轴快进，同步落到终值
    assert btn.hp == 1.0
    btn.leaveEvent(QEvent(QEvent.Type.Leave))
    assert anim.state() == QAbstractAnimation.State.Running
    assert anim.endValue() == 0.0, "离开必须重定向到 0，而不是排队等 hover 走完"


def test_press_and_release_progress(btn, restore_motion):
    """左键按下 pp→1（带位移反馈），松开回弹 →0。"""
    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    assert btn._press_anim is not None, "按下应启动 press 动画"
    assert btn._press_anim.endValue() == 1.0
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 1.0
    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    assert btn._press_anim.endValue() == 0.0


def test_reduce_motion_snaps_to_final_state(btn, restore_motion):
    """reduce_motion 总闸：不动画，直接落终态（语义是瞬显，不是缩短）。"""
    motion.set_reduce_motion(True)
    btn.enterEvent(_enter_event())
    assert btn.hp == 1.0, "减弱动效下 hover 应瞬时到位"
    assert btn._hover_anim is None, "减弱动效下不应创建动画对象"
    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    assert btn.pp == 1.0


def test_speed_scaling_follows_anim_speed(btn, restore_motion):
    """档位缩放走 motion 唯一口径：2.0 档时长减半。"""
    SmoothButton.set_speed(2.0)
    btn.enterEvent(_enter_event())
    assert btn._hover_anim.duration() == motion.duration(120, 2.0)


def test_compound_button_does_not_scale_on_press(btn, restore_motion):
    """内嵌子控件的复合按钮（navSiteCard 型）：子控件不走 paintEvent，
    按下只换底色不做位移缩放，否则文字与背景错位。"""
    child = QLabel("站点标题", btn)
    child.show()
    assert btn._is_child_free() is False
    child.deleteLater()

    plain = SmoothButton("纯文本")
    assert plain._is_child_free() is True
    plain.deleteLater()


def test_icon_button_inherits_smooth_button():
    """IconButton（30+ 处实例化）必须继承 SmoothButton —— 图标钮的
    hover/press 过渡靠继承免费获得，而不是每处手改。"""
    from PyQt6.QtWidgets import QPushButton
    assert issubclass(IconButton, SmoothButton)
    assert issubclass(IconButton, QPushButton)


def test_object_name_preserved_and_overlay_specs_resolve(btn):
    """objectName 契约（IconButton 同款铁律）：SmoothButton 不得改名；
    spec 解析按 objectName 命中映射。"""
    assert btn.objectName() == "taskAddBtn"
    hover, press = btn._overlay_specs()
    assert hover == ("primary_hover", 255) and press == ("primary_pressed", 255)


def test_icon_button_danger_variant_uses_danger_overlay():
    """iconBtn[danger="true"] 属性变体走 danger 系端点（而非 primary）。"""
    _app()
    b = IconButton("close", object_name="iconBtn")
    b.setProperty("danger", "true")
    hover, press = b._overlay_specs()
    assert hover[0] == "danger" and press[0] == "danger"
    b.deleteLater()


# ====================================================================
# D. ScreenToast 兼容门面（2026-10-06：实现整体迁往 src/toast.py 的
#    ToastCard + ToastCenter —— 底部居中语义气泡，进出场契约由
#    tests/test_toast.py 承接；此处只钉「旧入口转发语义不变」）
# ====================================================================
def test_screen_toast_show_msg_delegates_to_toast_center(restore_motion):
    """旧入口 show_msg(text, theme, ms) → ToastCenter.push(neutral)。"""
    from src import toast as toast_mod
    _app()
    calls = []
    orig = toast_mod.ToastCenter.push

    def _spy(cls, **kw):
        calls.append(kw)

    toast_mod.ToastCenter.push = classmethod(_spy)
    try:
        ScreenToast.show_msg("钉图已复制", "light", 2600)
        ScreenToast.show_msg("已复制路径", "dark", 800)
    finally:
        toast_mod.ToastCenter.push = orig
    assert len(calls) == 2
    assert calls[0]["kind"] == "neutral"
    assert calls[0]["title"] == "钉图已复制"
    assert calls[0]["theme"] == "light"
    assert calls[0]["ms"] == 2600
    assert calls[1]["ms"] == 800


def test_screen_toast_set_speed_broadcasts_to_center(restore_motion):
    """动效档位广播：ScreenToast.set_speed 必须转达 ToastCenter。"""
    from src import toast as toast_mod
    _app()
    ScreenToast.set_speed(1.5)
    assert ScreenToast._speed == pytest.approx(1.5)
    assert toast_mod.ToastCenter.speed() == pytest.approx(1.5)


# ====================================================================
# E. S4 补遗：拖拽收尾 + 滚动手感
# ====================================================================
def test_cancel_press_feedback_resets_progress(btn, restore_motion):
    """拖拽落定等"吞 release"的路径必须能显式收回按下反馈，pp 不卡 1。"""
    from PyQt6.QtTest import QTest
    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 1.0
    btn.cancel_press_feedback()
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 0.0, "吞掉 release 后按下反馈必须可显式收回"


def test_tune_list_scrolling_pixel_mode_and_step():
    """L3：像素级滚动 + 28px 步长（网页般连续手感，不再按行卡顿）。"""
    from PyQt6.QtWidgets import QListWidget
    _app()
    view = QListWidget()
    from src.controls import SMOOTH_SCROLL_STEP_PX, tune_list_scrolling
    tune_list_scrolling(view)
    assert (view.verticalScrollMode()
            == view.verticalScrollMode().ScrollPerPixel)
    assert view.verticalScrollBar().singleStep() == SMOOTH_SCROLL_STEP_PX
    view.deleteLater()


# ====================================================================
# F. 实底 hover 内容可见性（2026-10-03 用户报障）
# ====================================================================
# 实底 255 端点的 overlay 是不透明色块，旧实现直接盖在 QSS 外观上 ——
# hover 一瞬间文字/图标全消失（「下一张」「添加」与全部无名按钮）。
# 修复两件套：① _paint_overlay 在实底端点补画 CE_PushButtonLabel；
# ② IconButton hover 图标色缺省改 auto（_auto_hover_color 按端点现算）。
# 本节把「hover 后内容必须还在」钉成护栏。

def _wcag_lum(c):
    """WCAG 2.x 相对亮度（与 test_theme_contrast 同公式，独立实现）。"""
    def lin(u):
        u /= 255.0
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    return (0.2126 * lin(c.red()) + 0.7152 * lin(c.green())
            + 0.0722 * lin(c.blue()))


def _wcag_contrast(a, b):
    la, lb = _wcag_lum(a), _wcag_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_auto_hover_color_solid_endpoint_picks_on_primary():
    """auto 哨兵（未显式传 hover_color）：实底 primary_hover 端点上取
    on_primary —— 字面量钉死，防「主色图标画在主色底上隐形」回归。"""
    from src.theme import get_colors
    _app()
    b = IconButton("plus", text="新建笔记")     # 无名 → None 兜底实底组
    try:
        assert b._hover_spec is None, "未传 hover_color 应落 auto 哨兵"
        light = get_colors("light")
        dark = get_colors("dark")
        # 端点 primary_hover light=#227A64（深）→ 白；dark=#68CFAC（浅）
        # → 深墨。与 QSS 同底文字色（$on_primary）选色一致。
        assert b._auto_hover_color(light) == "#FFFFFF"
        assert b._auto_hover_color(dark) == "#04342C"
    finally:
        b.deleteLater()


def test_auto_hover_color_washed_endpoint_keeps_primary():
    """淡染端点（iconBtn/stepBtn 系 a12/a46 淡底）维持主色图标 —— 既有
    视觉契约不随本次修复漂移；中性浅面实色组（surface_2/3）返回 None，
    由调用方回退常态 off 色。"""
    from src.theme import get_colors
    _app()
    colors = get_colors("light")
    wash = IconButton("moon", size=36, icon_size=16, object_name="iconBtn")
    solid_surface = IconButton("copy", object_name="fragCopyBtn")
    try:
        assert wash._hover_spec is None
        assert wash._auto_hover_color(colors) == colors["primary"]
        # fragCopyBtn 站点实际显式传了 hover_color="text"；这里只钉
        # auto 算法本身对 surface 实底端点的判定
        assert solid_surface._auto_hover_color(colors) is None
    finally:
        wash.deleteLater()
        solid_surface.deleteLater()


def test_auto_hover_color_none_endpoint_keeps_off_and_no_crash():
    """hover 端点登记为 None 的 IconButton（2026-10-08 清单 C1 的
    pluginMoreBtn）悬停不得崩溃 —— 2026-10-07 用户报障：_auto_hover_color
    直接解包 ``_overlay_specs()[0]``，None 端点在 enterEvent 抛
    TypeError。契约：None 端点 → 返回 None（调用方回退 off 色），且
    enter/leave 全链路可走。"""
    _app()
    # 与站点同参：插件卡右上角「⋯」钮（plugins_panel.py IconButton("more")）
    b = IconButton("more", size=20, icon_size=14,
                   object_name="pluginMoreBtn")
    try:
        assert b._overlay_specs()[0] is None, (
            "pluginMoreBtn 的 hover 端点应登记为 None（hover 视觉归 QSS）")
        assert b._auto_hover_color({}) is None
        # 全链路冒烟：enterEvent → _refresh_icon → _auto_hover_color，
        # 修复前此处抛 TypeError
        b._hovered = True
        b._refresh_icon()
        b._hovered = False
        b._refresh_icon()
    finally:
        b.deleteLater()


def _count_content_px(img, endpoint, inset=4, tol=60):
    """数与端点色距离 > tol 的内容像素（跳过圆角边缘的 inset 带）。"""
    diff = 0
    er, eg, eb = endpoint.red(), endpoint.green(), endpoint.blue()
    for yy in range(inset, img.height() - inset):
        for xx in range(inset, img.width() - inset):
            c = img.pixelColor(xx, yy)
            if ((c.red() - er) ** 2 + (c.green() - eg) ** 2
                    + (c.blue() - eb) ** 2) > tol ** 2:
                diff += 1
    return diff


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_solid_hover_keeps_label_painted(theme):
    """绘制层护栏：实底组按钮在 hover 终态（_hp=1）grab 里必须有内容
    像素 —— overlay 补画 label 缺失时本测试红灯（2026-10-03 回归钉）。"""
    from src.theme import get_colors, get_main_window_qss
    _app()
    b = SmoothButton("下一张")
    b.setObjectName("nextBtn")
    b.setStyleSheet(get_main_window_qss(theme))
    b.resize(120, 34)
    b._hp = 1.0
    try:
        img = b.grab().toImage()
        endpoint = QColor(str(get_colors(theme)["primary_hover"]))
        assert _count_content_px(img, endpoint) >= 50, (
            "%s 主题：nextBtn hover 终态整板只剩端点色，文字被 overlay "
            "盖掉（_paint_overlay 的实底 label 补画丢失？）" % theme
        )
    finally:
        b.deleteLater()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_solid_hover_keeps_iconbutton_content(theme):
    """IconButton 同款（图标+文字）：hover 终态两者都必须可见 ——
    _auto_hover_color 选色错误（图标融底）时红灯。"""
    from src.theme import get_colors, get_main_window_qss
    _app()
    b = IconButton("plus", text="新建笔记")
    b.setStyleSheet(get_main_window_qss(theme))
    b.resize(120, 34)
    b._hovered = True
    b._refresh_icon()
    b._hp = 1.0
    try:
        img = b.grab().toImage()
        endpoint = QColor(str(get_colors(theme)["primary_hover"]))
        assert _count_content_px(img, endpoint) >= 50, (
            "%s 主题：无名 IconButton hover 终态无内容像素（图标/文字"
            "被盖或融底）" % theme
        )
    finally:
        b.deleteLater()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_solid_hover_text_contrast_contract(theme):
    """token 层护栏：实底端点（hover/pressed）与文字色 $on_primary 的
    WCAG 对比度 ≥4.5 —— 端点改值必须同步验证文字仍可读。"""
    from src.theme import get_colors
    colors = get_colors(theme)
    on = QColor(str(colors["on_primary"]))
    for token in ("primary", "primary_hover", "primary_pressed"):
        bg = QColor(str(colors[token]))
        ratio = _wcag_contrast(on, bg)
        assert ratio >= 4.5, (
            "%s 主题：on_primary 对 %s（%s）对比度 %.2f < 4.5 —— 实底钮"
            "文字不可读" % (theme, token, colors[token], ratio)
        )


# ====================================================================
# G. 按钮交互反馈系统化升级（2026-10-07 规格文档，批次 U1/U2/U3/U6）
# ====================================================================
# U1 press/release 改挂 QAbstractButton.pressed / released 信号 ——
#    鼠标左键、键盘 Space/Enter、触屏（Qt 合成鼠标事件）三路统一；
#    松手回弹改用 press_out token（150ms，比 fast 略长）。
# U2 禁用淡出 —— EnabledChange 时 dp 0↔1 插值（120ms 交叉淡染），
#    blockSignals 批量刷直接落终态（语义同 reduce_motion）。
# U3 选中态过渡 —— 分段钮 checked 底色 cp 插值；ToggleSwitch 未选中
#    轨道 hover 从 darker(112) 瞬变改插值。
# U6 命中区外扩 —— IconButton 命中区四向外扩 4px（事件级判定），
#    布局/绘制/几何零变化。

def _images_differ(img_a, img_b, tol=10, min_diff_px=5):
    """抽样比对两张图像是否不同（像素级护栏的公共判定）。"""
    if img_a.size() != img_b.size():
        return True
    diff = 0
    for yy in range(0, img_a.height(), 2):
        for xx in range(0, img_a.width(), 2):
            ca, cb = img_a.pixelColor(xx, yy), img_b.pixelColor(xx, yy)
            if ((ca.red() - cb.red()) ** 2 + (ca.green() - cb.green()) ** 2
                    + (ca.blue() - cb.blue()) ** 2) > tol * tol:
                diff += 1
                if diff >= min_diff_px:
                    return True
    return False


# ---------------- U1：键盘 / 触屏 press 统一 ----------------
def test_keyboard_space_press_drives_press_progress(btn, restore_motion):
    """G1 红线：键盘 Space 激活必须出现按下反馈（press 信号统一接入）。"""
    from PyQt6.QtWidgets import QApplication
    _app()
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                      Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, press)
    assert btn._press_anim is not None, "键盘按下应启动 press 动画"
    assert btn._press_anim.endValue() == 1.0
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 1.0
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(btn, release)
    assert btn._press_anim.endValue() == 0.0, "键盘松开应回弹"


def test_release_rebound_uses_press_out_token(btn, restore_motion):
    """松手回弹时长 = press_out（150ms，规格 §6），不再复用 fast。"""
    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    assert btn._press_anim.duration() == motion.eased_ms("press_out")
    assert motion.MOTION["press_out"] > motion.MOTION["fast"]


def test_hover_still_uses_fast_token(btn, restore_motion):
    """进入反馈仍走 fast（120ms）—— press_out 只服务回弹方向。"""
    btn.enterEvent(_enter_event())
    assert btn._hover_anim.duration() == motion.eased_ms("fast")


# ---------------- U2：禁用淡出 ----------------
def test_disable_fade_glides_and_redirects(btn, restore_motion):
    """setEnabled(False) → dp 0→1 插值；复用同一条动画可重定向回 0。"""
    btn.setEnabled(False)
    assert btn._dp_anim is not None, "禁用应启动淡出动画"
    assert btn._dp_anim.endValue() == 1.0
    btn._dp_anim.setCurrentTime(btn._dp_anim.duration())
    assert btn.dp == 1.0
    btn.setEnabled(True)
    assert btn._dp_anim.endValue() == 0.0, "恢复可用必须重定向回可用外观"
    btn._dp_anim.setCurrentTime(btn._dp_anim.duration())
    assert btn.dp == 0.0


def test_disable_while_pressed_fades_feedback_out(btn, restore_motion):
    """状态优先级 disabled > pressed：禁用时 press 反馈一并收回。"""
    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 1.0
    btn.setEnabled(False)
    assert btn._press_anim.endValue() == 0.0, "禁用必须收回按下反馈"
    btn._press_anim.setCurrentTime(btn._press_anim.duration())
    assert btn.pp == 0.0


def test_reduce_motion_disables_fade_snaps(btn, restore_motion):
    """reduce_motion 开启：禁用淡出瞬显落终态，不建动画对象。"""
    motion.set_reduce_motion(True)
    btn.setEnabled(False)
    assert btn.dp == 1.0
    assert btn._dp_anim is None


def test_block_signals_batch_disable_snaps_without_animation(btn,
                                                             restore_motion):
    """批量刷语义：blockSignals 包裹的 setEnabled 直接落终态（规格 U2）。"""
    btn.blockSignals(True)
    btn.setEnabled(False)
    btn.blockSignals(False)
    assert btn.dp == 1.0, "批量刷应直接落禁用终态"
    assert btn._dp_anim is None, "批量刷不应创建淡出动画"


def test_stepper_boundary_batch_refresh_snaps(restore_motion):
    """Stepper 批量同步：宿主 block 包裹 setValue → 子按钮禁用淡出直接
    落终态（子按钮自身信号没被 block，changeEvent 探测不到批量语义，
    必须由 _sync_buttons 显式拍板）。"""
    _app()
    s = Stepper(0, 10, 0)
    minus = s._btn_minus
    assert not minus.isEnabled(), "边界初值下 − 钮应置灰"
    s.blockSignals(True)
    s.setValue(6)
    s.blockSignals(False)
    assert minus.isEnabled()
    assert minus.dp == 0.0, "批量刷恢复可用应直接落终态"
    # 构造期边界置灰会合法地创建过一次 dp 动画对象；批量刷的语义是
    # 「不播动画」—— 断言不存在运行中的淡出，而不是对象不存在。
    assert (minus._dp_anim is None
            or minus._dp_anim.state() != QAbstractAnimation.State.Running), (
        "批量刷不应在子按钮上播淡出动画")
    s.deleteLater()


def test_stepper_boundary_gray_pixels_differ(restore_motion):
    """边界置灰像素对比（规格 U2 护栏）：同一 − 钮，禁用端点与可用端点
    的抓帧必须不同（图标 text_disabled ↔ text_secondary）。"""
    _app()
    s = Stepper(0, 10, 0)
    minus = s._btn_minus
    if minus._dp_anim is not None:
        minus._dp_anim.setCurrentTime(minus._dp_anim.duration())
    img_off = minus.grab().toImage()
    s.setValue(5)
    if minus._dp_anim is not None:
        minus._dp_anim.setCurrentTime(minus._dp_anim.duration())
    img_on = minus.grab().toImage()
    assert _images_differ(img_off, img_on), (
        "Stepper 边界置灰前后 − 钮像素无差异 —— 禁用淡出没有落到外观"
    )
    s.deleteLater()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_disable_fade_cross_fade_pixels(theme):
    """禁用淡出中间帧 = 可用/禁用两份 QSS 外观交叉淡染：dp=0 / 0.5 / 1
    三帧两两不同（少了哪一层都会在某组比较里撞车）。"""
    _app()
    b = SmoothButton("下一张")
    b.setObjectName("nextBtn")
    b.setStyleSheet(get_main_window_qss(theme))
    b.resize(120, 34)
    try:
        img_on = b.grab().toImage()
        b.setEnabled(False)
        anim = b._dp_anim
        assert anim is not None
        anim.setCurrentTime(max(1, anim.duration() // 2))
        img_mid = b.grab().toImage()
        anim.setCurrentTime(anim.duration())
        img_off = b.grab().toImage()
        assert _images_differ(img_on, img_off), "禁用端点像素无差异"
        assert _images_differ(img_on, img_mid), (
            "%s：淡出中间帧与可用端点相同 —— 交叉淡染的禁用层没画" % theme)
        assert _images_differ(img_mid, img_off), (
            "%s：淡出中间帧与禁用端点相同 —— 交叉淡染的可用层没画" % theme)
    finally:
        b.deleteLater()


# ---------------- U3：选中态过渡（分段钮）----------------
def _seg_button():
    _app()
    b = SmoothButton("全部")
    b.setObjectName("pluginSegBtn")
    b.setCheckable(True)
    return b


def test_segmented_checked_tint_glides(restore_motion):
    """分段钮选中底色：点击选中 → cp 0→1 插值；再点取消 → 重定向回 0。"""
    b = _seg_button()
    try:
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        assert b.isChecked()
        assert b._checked_anim is not None, "选中应启动底色过渡"
        assert b._checked_anim.endValue() == 1.0
        b._checked_anim.setCurrentTime(b._checked_anim.duration())
        assert b.cp == 1.0
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        assert not b.isChecked()
        assert b._checked_anim.endValue() == 0.0, "取消选中必须回弹"
    finally:
        b.deleteLater()


def test_segmented_checked_batch_set_checked_snaps(restore_motion):
    """blockSignals 批量 setChecked：toggled 被吞 → 无动画，paint 按
    isChecked 直接落终态（与 ToggleSwitch 同一口径）。"""
    b = _seg_button()
    try:
        b.blockSignals(True)
        b.setChecked(True)
        b.blockSignals(False)
        assert b._checked_anim is None, "批量选中不应创建过渡动画"
        assert b._checked_progress() == 1.0
    finally:
        b.deleteLater()


def test_checked_endpoint_registered():
    """checked 端点契约钉死（U3）：pluginSegBtn = $surface_3、
    modeBtn = $primary 实底（2026-10-07 收编）。"""
    assert _SMOOTH_CHECKED == {
        "pluginSegBtn": ("surface_3", 255),
        "modeBtn": ("primary", 255),
    }


def test_checked_qss_background_removed():
    """theme.py 的 #pluginSegBtn:checked / #modeBtn:checked 不得再有
    background-color —— 选中底色过渡归 overlay，QSS 留底 = 两套机制
    打架（S2 同款红线）。"""
    bodies = _rule_bodies()
    for sel in ("QPushButton#pluginSegBtn:checked",
                "QPushButton#modeBtn:checked"):
        assert sel in bodies, "%s 规则不见了（误删？）" % sel
        assert "background-color" not in bodies[sel]


# ---------------- U3：ToggleSwitch 轨道 hover 插值 ----------------
def test_toggle_track_hover_interpolates(restore_motion):
    """未选中轨道 hover 加深：enter → 插值到 1，leave → 重定向回 0。"""
    _app()
    sw = ToggleSwitch(checked=False)
    try:
        sw.enterEvent(_enter_event())
        assert sw._hover_anim.state() == QAbstractAnimation.State.Running
        assert sw._hover_anim.endValue() == 1.0
        sw._hover_anim.setCurrentTime(sw._hover_anim.duration())
        assert sw._thp == 1.0
        sw.leaveEvent(QEvent(QEvent.Type.Leave))
        assert sw._hover_anim.endValue() == 0.0, "离开必须重定向回常态轨道"
    finally:
        sw.deleteLater()


def test_toggle_track_hover_pixel_fade():
    """hover 加深是渐变：进度 0 / 1 两端抓帧必须不同（darker(112) 的
    插值替代，不是干脆删掉加深）。"""
    _app()
    sw = ToggleSwitch(checked=False, theme="light")
    try:
        sw._thp = 0.0
        sw.update()
        img_rest = sw.grab().toImage()
        sw._thp = 1.0
        sw.update()
        img_hover = sw.grab().toImage()
        assert _images_differ(img_rest, img_hover), (
            "未选中轨道 hover 进度 0/1 像素无差异 —— hover 加深失效")
    finally:
        sw.deleteLater()


def test_toggle_track_hover_snaps_under_reduce_motion(restore_motion):
    """reduce_motion 总闸：轨道 hover 加深瞬显，不建动画。"""
    motion.set_reduce_motion(True)
    _app()
    sw = ToggleSwitch(checked=False)
    try:
        sw.enterEvent(_enter_event())
        assert sw._thp == 1.0, "减弱动效下 hover 应瞬时到位"
        assert sw._hover_anim.state() != QAbstractAnimation.State.Running
    finally:
        sw.deleteLater()


# ---------------- U6：命中区外扩 ----------------
def test_icon_button_hit_pad_geometry_unchanged():
    """U6 红线：外扩只存在于事件层 —— sizeHint/geometry 零变化，
    _hit_rect 恰好比 rect 四向外扩 HIT_PAD=4px。"""
    _app()
    b = IconButton("close", size=24, object_name="iconBtn")
    try:
        assert b.size().width() == 24 and b.size().height() == 24
        assert b.rect().width() == 24 and b.rect().height() == 24
        assert not b.rect().contains(QPoint(-3, 12)), "采样点应在 rect 外"
        assert b._hit_rect().contains(QPoint(-3, 12)), (
            "rect 外 3px 的点必须落在事件级命中区内")
        assert IconButton.HIT_PAD == 4
    finally:
        b.deleteLater()


def test_icon_button_click_3px_outside_rect_reaches():
    """边界外 3px 的点击必须可达（触屏 32px 标准的最低验收线），
    且外扩带之外的点击保持不可达（不吞邻居）。"""
    from PyQt6.QtWidgets import QWidget
    _app()
    host = QWidget()
    host.resize(96, 72)
    clicked = []
    b = IconButton("close", size=24, parent=host)
    b.move(28, 18)      # rect x∈[28,52) y∈[18,42)，外扩带 x∈[24,56)
    b.clicked.connect(lambda: clicked.append(True))
    host.show()
    _app().processEvents()
    try:
        # 外扩带内、rect 外 3px：x=25（rect 左缘 28 - 3）
        QTest.mouseClick(host, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(25, 30))
        assert clicked, "命中区外扩后，rect 外 3px 的点击必须触发按钮"
        # 外扩带之外（x=20 < 24）：不得误触
        clicked.clear()
        QTest.mouseClick(host, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(20, 30))
        assert not clicked, "外扩带之外的点击不应触发按钮"
    finally:
        b.deleteLater()
        host.deleteLater()


def test_icon_button_inside_click_still_reaches():
    """rect 内的正常点击路径不受外扩过滤器影响（回归钉）。"""
    from PyQt6.QtWidgets import QWidget
    _app()
    host = QWidget()
    host.resize(96, 72)
    clicked = []
    b = IconButton("close", size=24, parent=host)
    b.move(28, 18)
    b.clicked.connect(lambda: clicked.append(True))
    host.show()
    _app().processEvents()
    try:
        QTest.mouseClick(b, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert clicked, "rect 内点击必须照常触发"
    finally:
        b.deleteLater()
        host.deleteLater()


def test_icon_button_no_parent_hit_filter_is_safe():
    """无父控件的图标钮（顶层创建后 reparent）不炸：过滤器可安全重挂。"""
    _app()
    b = IconButton("close", size=24)
    try:
        assert b._filtered_parent is None
        from PyQt6.QtWidgets import QWidget
        host = QWidget()
        b.setParent(host)
        assert b._filtered_parent is host, "reparent 后过滤器必须重挂到新父"
        host.deleteLater()
    finally:
        b.deleteLater()
