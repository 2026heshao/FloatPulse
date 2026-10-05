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

from PyQt6.QtCore import QPointF, QEvent, QAbstractAnimation, Qt
from PyQt6.QtGui import QColor, QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel

from src import motion
from src.controls import (
    IconButton, ScreenToast, SmoothButton, _OVERLAY_RADIUS, _SMOOTH_OVERLAYS,
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
    # 列表行/分组头样式里只有 navBtn 已收编（navGroupHeader 因
    # glass.py↔controls.py 导入环暂缓；appLaunchBtn 是 QToolButton）
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
# D. ScreenToast 进出场（清单 F6）
# ====================================================================
@pytest.fixture()
def toast(restore_motion):
    _app()
    inst = ScreenToast.show_msg("测试")
    inst._timer.stop()          # 单例跨测试共用：先掐掉上一次的定时器
    yield inst
    inst._timer.stop()
    inst.hide()


def test_toast_popup_floats_up_and_fades_in(toast):
    """进场 = 上浮 + 淡入并行，时长/曲线走 motion base 档（F6）。"""
    from PyQt6.QtCore import QAbstractAnimation
    assert toast._fade_anim.duration() == motion.eased_ms("base")
    assert toast._pos_anim.duration() == motion.eased_ms("base")
    assert toast._pos_anim.state() == QAbstractAnimation.State.Running
    assert toast._pos_anim.endValue().y() == toast._final_y
    assert toast._pos_anim.startValue().y() == toast._final_y + toast.FLOAT_PX
    toast._fade_anim.setCurrentTime(toast._fade_anim.duration())
    toast._pos_anim.setCurrentTime(toast._pos_anim.duration())
    assert toast.windowOpacity() == 1.0
    assert toast.pos().y() == toast._final_y


def test_toast_fade_out_sinks_down(toast):
    """出场 = 下沉 + 淡出；动画结束后隐藏（单例复用的收尾契约）。"""
    toast._fade_anim.setCurrentTime(toast._fade_anim.duration())
    toast._pos_anim.setCurrentTime(toast._pos_anim.duration())
    toast._fade_out()
    assert toast._fade_anim.endValue() == 0.0
    assert toast._pos_anim.endValue().y() == toast._final_y + toast.FLOAT_PX
    toast._fade_anim.setCurrentTime(toast._fade_anim.duration())
    assert toast.windowOpacity() == 0.0
    assert not toast.isVisible(), "淡出完成必须隐藏，等待下一次单例复用"


def test_toast_reduce_motion_snaps(toast):
    """reduce_motion：进出场直接落终态，不创建运行中的动画。"""
    motion.set_reduce_motion(True)
    toast.popup("测试", 8000)
    assert toast.windowOpacity() == 1.0
    assert toast.pos().y() == toast._final_y
    toast._fade_out()
    assert not toast.isVisible()


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
