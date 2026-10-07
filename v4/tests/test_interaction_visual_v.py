# -*- coding: utf-8 -*-
"""交互视觉优化清单 V 批护栏（2026-10-07，docs/交互视觉优化清单-2026-10-07.md）。

覆盖 V1（输入框）/ V2（滚动条状态机）/ V3（勾选四段式）/ V5（设置导航
指示滑块）/ V6（页面切换淡入）/ V7（日历 hover 淡染）/ V8（Tooltip QSS）
/ V9（自绘勾选框）/ V10（Stepper 数值滑动）/ V11（拖拽落点指示线）。
V4（行 hover 插值）见 test_row_hover_u4.py（规格独立成文，含像素基线）。

口径对齐全仓动效批：motion token 唯一时长源（禁字面量旁路）、reduce_motion
→ 0ms 瞬显、anim_speed 档位缩放、rest 渲染零变化（终态端点与被删 QSS 同源）。
护栏单跑可绿；改错值（轴长错配 / QSS 规则回退 / 广播漏接）可红。
====================================================================
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def restore_motion():
    from src import motion
    yield
    motion.set_reduce_motion(False)


# ====================================================================
# V3 任务勾选四段式（task_delegate 时间轴）
# ====================================================================
def test_v3_stage_axis_matches_total_duration():
    """轴长契约：四段子轴必须落在 CHECK_ANIM_MS 总长内且删除线收口于总长。"""
    from src import task_delegate as td
    from src.constants import CHECK_ANIM_MS
    assert td.CHECK_STRIKE_END == CHECK_ANIM_MS, (
        "删除线终点必须等于总长（终态帧所有子进度同时为 1）")
    assert td.CHECK_DRAW_START < td.CHECK_DRAW_END <= CHECK_ANIM_MS
    assert td.CHECK_STRIKE_START < td.CHECK_POP_END <= CHECK_ANIM_MS
    assert td.CHECK_SETTLE_END <= CHECK_ANIM_MS


def test_v3_stages_stagger_and_converge():
    """全局进度 → 子进度：错峰可见（t=46ms 只有 pop 与沉降），终点全 1。"""
    from src import task_delegate as td
    from src.constants import CHECK_ANIM_MS
    t = 0.2 * CHECK_ANIM_MS     # 46ms
    pop = td._stage(t, 0.0, td.CHECK_POP_END)
    draw = td._stage(t, td.CHECK_DRAW_START, td.CHECK_DRAW_END)
    strike = td._stage(t, td.CHECK_STRIKE_START, td.CHECK_STRIKE_END)
    settle = td._stage(t, 0.0, td.CHECK_SETTLE_END)
    assert pop > 0.0 and settle > 0.0, "pop 与沉降应在 46ms 内起步"
    assert draw == 0.0 and strike == 0.0, (
        "对勾（60ms 起）/删除线（80ms 起）必须与 pop 错峰")
    t = CHECK_ANIM_MS
    assert all(td._stage(t, a, b) == 1.0 for a, b in (
        (0.0, td.CHECK_POP_END), (td.CHECK_DRAW_START, td.CHECK_DRAW_END),
        (td.CHECK_STRIKE_START, td.CHECK_STRIKE_END),
        (0.0, td.CHECK_SETTLE_END))), "p=1 时四段子进度必须同时收口为 1"


def test_v3_paint_uses_staged_progress(restore_motion):
    """绘制侧真接线：_paint_row 用错峰子进度画勾选框/删除线（非单 p）。"""
    import inspect
    from src import task_delegate as td
    src = inspect.getsource(td.TaskItemDelegate._paint_row)
    assert "pop_p" in src and "draw_p" in src and "strike_p" in src \
        and "settle_p" in src
    assert "_stage(t" in src, "子进度必须由全局时间轴 t = p*CHECK_ANIM_MS 切出"


# ====================================================================
# V5 设置导航指示滑块
# ====================================================================
class _FakeConfig:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        pass


_KEEP_V_PANEL = []


@pytest.fixture(scope="module")
def settings_panel():
    import types
    from PyQt6.QtTest import QTest
    from src.settings_panel import SettingsPanel
    _app()
    host = types.SimpleNamespace(config=_FakeConfig(), current_theme="dark")
    panel = SettingsPanel(host)
    # 几何断言前提：布局激活（未 show 的控件 geometry 无效，滑块无从落位）
    panel.resize(960, 700)
    panel.show()
    QTest.qWait(60)
    yield panel
    # 受控墓地拆除（2026-10-07，同 test_settings_nav._quiesce 口径）：
    # 面板自产的一次性 overlay 动画垃圾必须在本文件受控点死透，否则
    # 拖到后续文件的 GC 兜底析构会打乱 QUnifiedTimer 簿记 → 动画冻结。
    from PyQt6.QtCore import QAbstractAnimation, QEvent, QTimer
    from PyQt6.QtWidgets import QApplication
    for anim in panel.findChildren(QAbstractAnimation):
        anim.stop()
    for t in panel.findChildren(QTimer):
        t.stop()
    panel.close()
    for _ in range(3):
        QApplication.sendPostedEvents(panel, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
    import gc as _gc
    _gc.collect()
    _KEEP_V_PANEL.append(panel)


def test_v5_indicator_exists_and_tracks(settings_panel):
    from src.settings_panel import SETTINGS_CATEGORIES, _SettingsNavIndicator
    assert isinstance(settings_panel._nav_indicator, _SettingsNavIndicator)
    from PyQt6.QtTest import QTest
    settings_panel.show_category("ai")
    QTest.qWait(220)          # 120ms 滑动播完再断言落点
    rect = settings_panel._nav_indicator_rect("ai")
    ind = settings_panel._nav_indicator
    assert ind.isVisibleTo(settings_panel), "指示滑块应随选中显形"
    assert ind.y() == rect.y(), "落定后滑块应与选中行同位"
    assert settings_panel._cat_btns["ai"].isChecked()
    assert len(settings_panel._cat_btns) == len(SETTINGS_CATEGORIES)


def test_v5_indicator_geometry_follows_checked_button(settings_panel):
    """连切两分类：滑块 y 必须跟到新行（可打断重定向的落点正确性）。"""
    from PyQt6.QtTest import QTest
    keys = list(settings_panel._cat_btns.keys())
    settings_panel.show_category(keys[0])
    QTest.qWait(220)
    y0 = settings_panel._nav_indicator.y()
    settings_panel.show_category(keys[-1])
    QTest.qWait(220)          # 120ms 滑动播完再断言落点
    rect = settings_panel._nav_indicator_rect(keys[-1])
    assert settings_panel._nav_indicator.y() == rect.y() != y0


def test_v5_qss_checked_bg_handed_over():
    """QSS 契约：#settingsNavBtn:checked 不再声明背景色（归滑块绘制）。"""
    import re
    import src.theme as theme
    qss = theme.get_main_window_qss("light")
    m = re.search(r"QPushButton#settingsNavBtn:checked\s*\{([^}]*)\}", qss)
    assert m, "settingsNavBtn:checked 规则消失（被误删）"
    assert "background-color" not in m.group(1), (
        "选中行底仍由 QSS 声明 —— V5 移交契约被回退")


# ====================================================================
# V6 主窗页面切换淡入
# ====================================================================
def test_v6_switch_page_fades_page_content():
    """AST 钉子：_switch_page 必须对 stack 当前页调 fade_in_once，时长走
    motion base 档（禁字面量旁路）。"""
    import inspect
    from src import main_window
    import textwrap
    src = textwrap.dedent(
        inspect.getsource(main_window.MainWindow._switch_page))
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    hits = [c for c in calls
            if getattr(c.func, "id", "") == "fade_in_once"]
    assert hits, "_switch_page 缺少 fade_in_once 接线（V6 被回退）"
    src_text = ast.unparse(hits[0])
    assert 'eased_ms("base"' in src_text or 'eased_ms(\'base\'' in src_text, (
        "页切换淡入时长必须走 motion base token：%s" % src_text)


# ====================================================================
# V8 QToolTip 全局主题化
# ====================================================================
def test_v8_tooltip_qss_present_both_accent_families():
    import src.theme as theme
    for theme_name in ("light", "dark"):
        colors = theme.get_colors(theme_name)
        qss = theme.get_main_window_qss(theme_name)
        assert "QToolTip {" in qss, "%s 主题缺 QToolTip 定义" % theme_name
        block = qss.split("QToolTip {", 1)[1].split("}", 1)[0]
        # 成品 QSS 已做 $token 替换：实底/描边必须等于当主题的
        # $surface / $line（token 旁路或写死他色即红）
        assert colors["surface"] in block, (
            "%s Tooltip 实底未跟随 $surface" % theme_name)
        assert colors["line"] in block, (
            "%s Tooltip 描边未跟随 $line" % theme_name)
        assert "border-radius: 6" in block


# ====================================================================
# V1 SmoothInput（QSS 契约 + 端点 alpha 保真）
# ====================================================================
def test_v1_qss_smooth_frame_override_after_focus_rule():
    """smoothFrame 覆盖规则必须排在 QLineEdit:focus 之后（同特异性按序取胜），
    且原生 QLineEdit 的 :focus 环保留（HotkeyCaptureEdit 等不受影响）。"""
    import src.theme as theme
    qss = theme.get_main_window_qss("light")
    assert "QLineEdit:focus" in qss, "原生 QLineEdit :focus 环被误删"
    assert 'QLineEdit[smoothFrame="true"]' in qss
    assert qss.index("QLineEdit:focus") \
        < qss.index('QLineEdit[smoothFrame="true"]'), (
        "smoothFrame 覆盖规则必须在 :focus 组之后（否则基态边框又实了）")


def test_v1_border_alpha_interpolates_to_token_endpoints(restore_motion):
    """端点保真：hover 端点边框必须保留 $primary_a30 的半透明语义 ——
    若插值丢 alpha，端点会画成**不透明实底主色**（签名：边框像素 ==
    $primary 实色）。正确实现下端点 = 30% 主色合成，与实底主色可分。"""
    from PyQt6.QtGui import QColor, QPixmap
    from src.controls import SmoothInput
    from src.theme import get_colors
    _app()
    w = SmoothInput()
    w.resize(200, 32)
    w.show()
    _app().processEvents()
    colors = get_colors("dark")     # 无宿主 → DEFAULT_THEME(dark) 父链兜底
    primary = QColor(str(colors["primary"]))

    def snap_border(hp, fp):
        w._hp = hp
        w._fp = fp
        pix = QPixmap(w.size())
        pix.fill()
        w.render(pix)
        return pix.toImage().pixelColor(0, 16)

    rest = snap_border(0.0, 0.0)
    hover = snap_border(1.0, 0.0)
    assert rest != hover, "hover 端点与常态无像素差异 —— 边框插值未生效"
    assert (hover.red(), hover.green(), hover.blue()) != (
        primary.red(), primary.green(), primary.blue()), (
        "hover 端点画成了不透明 $primary 实底 —— 边框插值丢了 alpha 通道")


def test_v1_speed_broadcast_reaches_smooth_input():
    from src import controls
    controls.set_ui_speed(1.5)
    try:
        assert controls.SmoothInput._speed == pytest.approx(1.5)
    finally:
        controls.set_ui_speed(1.0)


# ====================================================================
# V9 SmoothCheckBox
# ====================================================================
def test_v9_qss_smooth_check_override_order():
    import src.theme as theme
    qss = theme.get_main_window_qss("light")
    assert "QCheckBox::indicator:focus" in qss
    assert 'QCheckBox[smoothCheck="true"]::indicator' in qss
    assert qss.index("QCheckBox::indicator:focus") \
        < qss.index('QCheckBox[smoothCheck="true"]::indicator')


def test_v9_indicator_self_drawn_state_change(restore_motion):
    """选中态与 rest 像素必须不同（自绘落地），时间轴中段可感。"""
    from PyQt6.QtGui import QPixmap
    from src.controls import SmoothCheckBox, set_ui_speed
    _app()
    set_ui_speed(1.0)
    w = SmoothCheckBox("测试")
    w.resize(140, 24)
    w.show()
    _app().processEvents()

    def snap():
        pix = QPixmap(w.size())
        pix.fill()
        w.render(pix)
        return pix.toImage()

    rest = snap()
    w.setChecked(True)
    w._cp = 1.0
    w._tp = 1.0
    checked = snap()
    diff = sum(1 for yy in range(rest.height()) for xx in range(24)
               if rest.pixelColor(xx, yy) != checked.pixelColor(xx, yy))
    assert diff > 0, "选中指示器无像素变化 —— 自绘未生效（QSS 覆盖规则被回退？）"
    w._tp = 0.5
    mid = snap()
    assert any(mid.pixelColor(xx, 12) != checked.pixelColor(xx, 12)
               for xx in range(16)), "时间轴中段应可见描画过程"


def test_v9_set_checked_drives_cp_without_manual_poking(restore_motion):
    """回归（2026-10-07 实机走查，软件导航「自动回到主页面」勾选框）：
    setChecked(True) 必须自动驱动底色 cp —— 选中指示器变 primary 绿底。
    此前 V9 初版只驱动 tp，_cp 是死属性：选中态白底 + on_primary 白勾
    完全不可见，旧测试靠手改 _cp=_tp=1.0 糊过去。"""
    from PyQt6.QtCore import QAbstractAnimation
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtTest import QTest
    from src.controls import SmoothCheckBox, set_ui_speed
    _app()
    set_ui_speed(1.0)
    w = SmoothCheckBox("测试")
    w.resize(140, 24)
    w.show()
    _app().processEvents()

    w.setChecked(True)
    QTest.qWait(180)          # cp 120ms 动画走完
    assert w.isChecked()
    assert w._checked_progress() == 1.0, "选中底色有效值未到 1 —— cp 驱动缺失"
    assert w._cp_anim is not None and \
        w._cp_anim.state() != QAbstractAnimation.State.Running

    pix = QPixmap(w.size())
    pix.fill()
    w.render(pix)
    greens = sum(
        1 for yy in range(4, 20) for xx in range(2, 20)
        if (lambda c: c.green() > c.red() + 30 and c.green() > c.blue() + 30)
        (pix.toImage().pixelColor(xx, yy)))
    assert greens > 8, "选中指示器没有 primary 绿底像素 —— cp 驱动回归"

    w.setChecked(False)
    QTest.qWait(300)          # tp 倒放 230ms 走完
    assert w._checked_progress() == 0.0
    assert w._timeline_progress() == 0.0


def test_v9_speed_broadcast_reaches_smooth_checkbox():
    from src import controls
    controls.set_ui_speed(1.5)
    try:
        assert controls.SmoothCheckBox._speed == pytest.approx(1.5)
    finally:
        controls.set_ui_speed(1.0)


# ====================================================================
# V2 SmoothScrollBar 状态机
# ====================================================================
def test_v2_scrollbar_state_machine(restore_motion):
    """闲置(6px 淡) → 滚动中(8px 深) → 静止 600ms 回落；双向像素渐变。"""
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem
    from src.smooth_scrollbar import SmoothScrollBar
    _app()
    lst = QListWidget()
    lst.resize(200, 100)
    for i in range(50):
        lst.addItem(QListWidgetItem("item %d" % i))
    bar = SmoothScrollBar.install(lst)
    lst.show()
    _app().processEvents()

    def snap():
        pix = QPixmap(bar.size())
        pix.fill()
        bar.render(pix)
        return pix.toImage()

    def diff(a, b):
        return sum(1 for yy in range(a.height()) for xx in range(a.width())
                   if a.pixelColor(xx, yy) != b.pixelColor(xx, yy))

    idle = snap()
    bar._on_value_changed(30)
    QTest.qWait(300)          # base 档过渡播完
    active = snap()
    assert bar._ap > 0.9, "滚动后应激活（ap→1）"
    assert diff(idle, active) > 0, "激活态与闲置态无像素差异 —— 状态机未生效"
    QTest.qWait(1000)         # 600ms 静止 + 回落过渡
    assert bar._ap < 0.1, "静止 600ms 后必须回落闲置态"
    assert diff(active, snap()) > 0, "回落未落到像素层"


def test_v2_speed_broadcast_reaches_scrollbar():
    from src import controls
    from src.smooth_scrollbar import SmoothScrollBar
    controls.set_ui_speed(1.5)
    try:
        assert SmoothScrollBar._speed == pytest.approx(1.5)
    finally:
        controls.set_ui_speed(1.0)


def test_v2_qss_legacy_blocks_retired():
    """navScroll / kbList 两份滚动条 QSS 必须退役（收敛为自绘控件）。"""
    import src.theme as theme
    qss = theme.get_main_window_qss("light")
    assert "QScrollArea#navScroll QScrollBar" not in qss
    assert "QListWidget#kbList QScrollBar" not in qss


# ====================================================================
# V7 日历格子 hover 淡染
# ====================================================================
def test_v7_calendar_cell_hover_fades_and_clears(restore_motion):
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtTest import QTest
    from src import motion
    from src.date_picker import (CELL_H, CELL_W, COLS, GRID_TOP, SHADOW,
                                 CalendarPopup)
    _app()
    motion.set_reduce_motion(False)
    cal = CalendarPopup()
    cal.configure(selected=(2026, 10, 6))
    cal.show()
    _app().processEvents()
    i = 9
    cx = SHADOW + 8 + (i % COLS) * CELL_W + CELL_W // 2
    cy = SHADOW + GRID_TOP + (i // COLS) * CELL_H + CELL_H // 2

    def move(x, y):
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
                         Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        _app().sendEvent(cal, ev)

    move(cx, cy)
    QTest.qWait(20)
    mid = dict(cal._hover_prog)
    QTest.qWait(250)
    end = dict(cal._hover_prog)
    assert end.get(("day", i), 0.0) == 1.0, "hover 端点进度必须为 1"
    assert 0.0 < mid.get(("day", i), 0.0) < 1.0 or len(mid) <= 2, (
        "中间帧进度异常：%s" % mid)
    move(4, 4)
    QTest.qWait(300)
    assert cal._hover_prog == {}, "离开后进度必须清干净"


def test_v7_reduce_motion_snaps_cell_hover():
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from src import motion
    from src.date_picker import (CELL_H, CELL_W, COLS, GRID_TOP, SHADOW,
                                 CalendarPopup)
    _app()
    motion.set_reduce_motion(True)
    try:
        cal = CalendarPopup()
        cal.configure(selected=(2026, 10, 6))
        cal.show()
        _app().processEvents()
        i = 3
        cx = SHADOW + 8 + (i % COLS) * CELL_W + CELL_W // 2
        cy = SHADOW + GRID_TOP + (i // COLS) * CELL_H + CELL_H // 2
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(cx, cy),
                         QPointF(cx, cy), Qt.MouseButton.NoButton,
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        _app().sendEvent(cal, ev)
        assert cal._hover_prog.get(("day", i)) == 1.0, (
            "减弱动效下 hover 应瞬显到位")
        assert not any(a.state() and a.state().value
                       for a in cal._hover_anims.values()), "不应有活跃动画"
    finally:
        motion.set_reduce_motion(False)


# ====================================================================
# V10 Stepper 数值滑动
# ====================================================================
def test_v10_stepper_slide_and_direct_paths(restore_motion):
    from PyQt6.QtTest import QTest
    from src.controls import Stepper, set_ui_speed
    _app()
    set_ui_speed(1.0)
    st = Stepper(0, 100, 10, suffix="秒")
    st.resize(200, 40)
    st.show()
    _app().processEvents()
    st.step_by(1)
    assert st._slide_overlay is not None, "单击 ± 应播数值滑动"
    assert st._edit.text() == "", "滑动期间数值框文本应由覆盖层接管"
    assert st.value() == 11, "数据必须在 setValue 即时落位（表现层不动数据）"
    QTest.qWait(260)
    assert st._slide_overlay is None and st._edit.text() == "11"
    st.step_by(-1, animate=False)
    assert st._slide_overlay is None and st._edit.text() == "10", (
        "animate=False 路径必须直落终态")


def test_v10_stepper_repeat_path_skips_slide(restore_motion):
    from src.controls import Stepper, set_ui_speed
    _app()
    set_ui_speed(1.0)
    st = Stepper(0, 100, 10)
    st.resize(200, 40)
    st.show()
    _app().processEvents()
    st._begin_hold(1)
    st._on_repeat()               # 连发第一拍
    assert st._slide_overlay is None, "长按连发路径必须直落终态（规格 §2 V10）"
    assert st.value() == 11
    st._end_hold()


def test_v10_speed_broadcast_reaches_stepper():
    from src import controls
    controls.set_ui_speed(1.5)
    try:
        assert controls.Stepper._speed == pytest.approx(1.5)
    finally:
        controls.set_ui_speed(1.0)


# ====================================================================
# V11 拖拽落点指示线
# ====================================================================
def test_v11_nav_list_indicator_states(restore_motion):
    """rest 零绘制 → 拖拽常显 → 落定淡出；指示线 y 锚定落点槽位。"""
    import src.nav_panel as nav_panel
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import QRect
    _app()
    lst = nav_panel._NavList(type("P", (), {})())
    lst.resize(300, 200)

    class _Row:
        """行桩：_relayout/resizeEvent 路径会调 setGeometry/resize/move。"""

        def __init__(self):
            self._y = 0

        def y(self):
            return self._y

        def setGeometry(self, x, y, w, h):
            self._y = y

        def move(self, x, y):
            self._y = y

        def resize(self, w, h):
            pass

        def geometry(self):
            return QRect(0, self._y, 100, 44)

    rows = [_Row() for _ in range(3)]
    lst._rows = rows
    lst._drag_row = rows[1]

    def snap():
        pix = QPixmap(lst.size())
        pix.fill()
        lst.render(pix)
        return pix.toImage()

    rest = snap()
    lst._dragging = True
    dragging = snap()
    assert dragging != rest or lst._indicator_alpha == 1.0, (
        "拖拽态指示线应可见")
    # 落定淡出
    lst._dragging = False
    lst._on_indicator_fade(0.0)
    assert lst._indicator_alpha == 0.0


# ====================================================================
# 档位广播完整性（V 批新控件一并接入 set_ui_speed 单一入口）
# ====================================================================
def test_set_ui_speed_broadcasts_all_v_controls():
    import inspect
    from src import controls
    src = inspect.getsource(controls.set_ui_speed)
    for name in ("SmoothInput", "SmoothCheckBox", "Stepper",
                 "SmoothScrollBar", "RowHoverController"):
        assert name in src, "set_ui_speed 漏接 %s（档位缩放断链）" % name
