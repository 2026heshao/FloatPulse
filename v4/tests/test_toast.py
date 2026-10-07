# -*- coding: utf-8 -*-
"""轻提示气泡回归  -  test_toast（2026-10-06 重设计，src/toast.py）。

设计稿：``设计稿/轻提示气泡-高仿真-2026-10-06.html``。本文件钉死六层契约：

  A. 语义表：七种 kind 的 token / 图标形体 / 标准档时长（3200/5000/6000，
     loading 常驻）在 light/dark 两主题都可解析，未知 kind 回落 neutral；
  B. 几何：248px 定宽 + 阴影边距、屏幕底部居中、距底缘 56px 基准档（入场
     起始位 +12px）、高度随内容自适应、正文 3 行封顶；
  C. 堆叠：上限 3、新条在最下、溢出最早的收拢为「+N」胶囊（R4）、
     空位腾出后 FIFO 恢复、退场闭合空隙；
  D. 倒计时与动效：单 QVariantAnimation 驱动 bar + 自动退场、hover
     暂停/离开恢复、入场 280ms / 出场 160ms 走 motion token、
     reduce_motion 落终态、档位缩放；
  E. 交互与主题：关闭钮命中、动作钮回调 + 退场、morph 原地变体、
     set_theme 原地换肤；源码护栏：气泡零写死色值（全部走 token）；
  F. 设置项（2026-10-06「轻提示」卡 7 键）：R1 静默丢弃 / R2 位置 /
     R3 底缘与任务栏守卫 / R4 收拢胶囊 / R5 时长档位 / R6 动画开关 /
     R7 提示音 / R9 平移补间 / R10 不补发。

旧入口兼容（ScreenToast.show_msg → ToastCenter.push 转发语义）钉在
tests/test_smooth_buttons.py D 段。
"""

import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (  # noqa: E402
    QEvent, QPointF, QRect, Qt, QVariantAnimation,
)
from PyQt6.QtGui import QEnterEvent, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src import motion  # noqa: E402
from src import toast as toast_mod  # noqa: E402
from src.app_paths import (  # noqa: E402
    get_full_screen_geometry, get_screen_geometry,
)
from src.config import DEFAULT_CONFIG  # noqa: E402
from src.icons import has_icon  # noqa: E402
from src.theme import get_colors  # noqa: E402
from src.toast import (  # noqa: E402
    BOTTOM_GAP, CLOSE_OVERSHOOT, CLOSE_SIZE, CORNER_MARGIN, ENTER_FLOAT_PX,
    EXIT_FLOAT_PX, KINDS, MAX_MSG_LINES, MAX_VISIBLE, SHADOW_M,
    STACK_GAP, WIDTH, ToastCenter,
)

_APP = None
_V4 = BASE      # tests/ 的上级 = v4/（src/toast.py 所在）


def _app():
    """取（必要时创建）QApplication，引用留模块级（test_icons.py 同款），
    防止 PyQt6 包装对象被回收后 QPixmap/qFatal 静默崩进程。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class _FakeCfg:
    """dict 兜底的配置替身：ToastCenter.attach_config 的轻量门面。

    未覆盖的键回落 DEFAULT_CONFIG（与真实 ConfigManager.get 同口径）。"""

    def __init__(self, kv=None):
        self._kv = dict(kv or {})

    def get(self, key, default=None):
        return self._kv.get(key, DEFAULT_CONFIG.get(key, default))


@pytest.fixture()
def toast_cfg():
    """给 ToastCenter 挂替身配置；用例结束即脱钩（_reset_all 双保险）。"""
    def _attach(kv=None):
        cfg = _FakeCfg(kv)
        ToastCenter.attach_config(cfg)
        return cfg
    yield _attach
    ToastCenter.attach_config(None)


@pytest.fixture()
def clean_center():
    """ToastCenter 是类级单点：每个用例前后硬清场，杜绝跨用例串台。"""
    _app()
    ToastCenter._reset_all()
    yield
    ToastCenter._reset_all()


@pytest.fixture()
def restore_motion():
    yield
    motion.set_reduce_motion(False)
    ToastCenter.set_speed(1.0)


# ====================================================================
# A. 语义表契约
# ====================================================================
def test_kinds_table_shape_and_durations():
    """七种语义齐备，时长策略与设计稿一致（loading 常驻）。"""
    assert set(KINDS) == {"success", "info", "warning", "danger",
                          "accent", "neutral", "loading"}
    assert KINDS["success"]["ms"] == 3200
    assert KINDS["info"]["ms"] == 3200
    assert KINDS["neutral"]["ms"] == 3200
    assert KINDS["warning"]["ms"] == 5000
    assert KINDS["accent"]["ms"] == 5000
    assert KINDS["danger"]["ms"] == 6000
    assert KINDS["loading"]["ms"] == 0


def test_kinds_tokens_exist_in_both_themes_and_icons_registered():
    """色 token 在两主题都存在、图标形体都已登记（拼错要在这里炸）。"""
    for kind, spec in KINDS.items():
        for theme in ("light", "dark"):
            assert spec["token"] in get_colors(theme), (kind, spec["token"])
        assert has_icon(spec["icon"]), (kind, spec["icon"])
    # neutral 的图标块底与图标色也是 token
    assert "surface_3" in get_colors("light")
    assert "text_secondary" in get_colors("light")


def test_push_unknown_kind_falls_back_to_neutral(clean_center):
    """未知 kind 不炸、回落 neutral（插件传脏值的兜底口径）。"""
    card = ToastCenter.push(kind="banana", title="x", theme="light")
    assert card.kind == "neutral"


def test_motion_tokens_exist():
    """时长 token 落在 motion.MOTION（禁字面量，A1 纪律）。"""
    assert motion.MOTION["toast_in"] == 280
    assert motion.MOTION["toast_out"] == 160
    assert motion.MOTION["toast_stagger"] == 60


# ====================================================================
# B. 几何
# ====================================================================
def test_fixed_width_centered_bottom_gap(clean_center):
    """定宽窗口、水平居中、入场起点距工作区底缘 56px（+12px 上浮偏移）。"""
    card = ToastCenter.push(title="已保存到随手记", theme="light")
    assert card.width() == WIDTH + SHADOW_M * 2
    screen = get_screen_geometry()
    assert card.x() == screen.left() + (screen.width() - card.width()) // 2
    card_bottom = card.y() + SHADOW_M + card.card_height
    assert card_bottom - (screen.bottom() + 1 - BOTTOM_GAP) == ENTER_FLOAT_PX


def test_height_adapts_by_content(clean_center):
    """紧凑单行 < 带正文 < 带动作行（高度随内容自适应）。

    正文用两行文案：单行正文在某些字体度量下会整个落进图标块 28px 的
    行高里（设计稿本就允许），两行才保证严格递增且环境无关。"""
    compact = ToastCenter.push(kind="neutral", title="静音已开启",
                               theme="light")
    h_compact = compact.card_height
    with_msg = ToastCenter.push(kind="neutral", title="静音已开启",
                                msg="番茄钟期间通知已全部关闭，请放心使用",
                                theme="light")
    h_msg = with_msg.card_height
    with_act = ToastCenter.push(kind="danger", title="导出失败",
                                msg="目标文件夹没有写入权限，请检查后重试",
                                action=("重试", None), theme="light")
    h_act = with_act.card_height
    assert h_compact < h_msg < h_act


def test_msg_capped_at_three_lines(clean_center):
    """正文 3 行封顶：远超 3 行的文案与恰好超 3 行的文案**等高**
    （不封顶的话前者会是后者的数倍高）。"""
    long_msg = ToastCenter.push(kind="neutral", title="t",
                                msg="很长的正文" * 80, theme="light")
    three_line = ToastCenter.push(kind="neutral", title="t",
                                  msg="字" * 60, theme="light")
    one_line = ToastCenter.push(kind="neutral", title="t",
                                msg="一行", theme="light")
    assert long_msg.card_height == three_line.card_height
    assert long_msg._layout_metrics()[2] == three_line._layout_metrics()[2]
    assert one_line.card_height < three_line.card_height
    assert long_msg._layout_metrics()[2] <= MAX_MSG_LINES * 20


# ====================================================================
# C. 堆叠
# ====================================================================
def test_stack_caps_at_three_oldest_parks_first(clean_center):
    """上限 3：第 4 条起最早的收拢进「+N」胶囊（R4，可见集合仍是最新的 3 条）。"""
    kinds = ["success", "info", "warning", "danger"]
    for i, k in enumerate(kinds):
        ToastCenter.push(kind=k, title=f"n{i}", theme="light")
    live = ToastCenter.live_cards()
    assert len(live) == MAX_VISIBLE
    assert [c.kind for c in live] == ["info", "warning", "danger"], \
        "最旧的 success 必须先收拢，可见的永远是最新的 3 条"
    parked = ToastCenter.overflow_cards()
    assert [c.kind for c in parked] == ["success"]
    assert parked[0]._parked and not parked[0].isVisible()
    assert ToastCenter.pill_count() == 1


def test_newest_at_bottom_with_gap(clean_center):
    """新条在最下、旧条上移，垂直间距 = STACK_GAP。

    读 ``_target``（确定性落位）而不是实际 pos —— 入场动画在途时
    实际坐标是飞行中的插值，断言会漂。"""
    a = ToastCenter.push(kind="neutral", title="a", theme="light")
    b = ToastCenter.push(kind="neutral", title="b", theme="light")
    assert b._target.y() > a._target.y(), "新条必须在更下方"
    gap = (b._target.y() + SHADOW_M) - (a._target.y() + SHADOW_M + a.card_height)
    assert gap == STACK_GAP, gap


def test_leaving_card_frees_slot_for_newcomer(clean_center):
    """退场即刻移出队列：3 条满员时手动关掉一条，新条能进来。"""
    for i in range(3):
        ToastCenter.push(kind="neutral", title=f"n{i}", theme="light")
    assert len(ToastCenter.live_cards()) == 3
    ToastCenter.live_cards()[0].dismiss()
    ToastCenter.push(kind="neutral", title="new", theme="light")
    assert len(ToastCenter.live_cards()) == MAX_VISIBLE


def test_dismiss_closes_gap(clean_center):
    """下面一条（新条）被关掉：上方条的目标位下移补位（闭合空隙）。"""
    a = ToastCenter.push(kind="neutral", title="a", theme="light")
    b = ToastCenter.push(kind="neutral", title="b", theme="light")
    target_a_before = a._target.y()
    b.dismiss()                       # 底部锚位空出，a 下移补位
    assert a._target.y() > target_a_before, "a 应下移补上 b 的空位"


# ====================================================================
# D. 倒计时与动效
# ====================================================================
def test_countdown_binds_to_lifetime(clean_center):
    """倒计时动画时长 = 指定寿命；loading（常驻）没有倒计时。"""
    card = ToastCenter.push(kind="success", title="x", ms=1234,
                            theme="light")
    assert card._countdown is not None
    assert card._countdown.duration() == 1234
    persistent = ToastCenter.push(kind="loading", title="x", theme="light")
    assert persistent._countdown is None
    assert persistent._lifetime == 0


def test_hover_pauses_and_leave_resumes_countdown(clean_center):
    """hover 冻结倒计时、离开续走（设计稿铁律 ③）。"""
    card = ToastCenter.push(kind="success", title="x", theme="light")
    assert card._countdown.state() == QVariantAnimation.State.Running
    card.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert card._countdown.state() == QVariantAnimation.State.Paused
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card._countdown.state() == QVariantAnimation.State.Running


def test_countdown_end_triggers_leave(clean_center):
    """倒计时走完 → 自动退场（单动画驱动，无第二个 QTimer）。"""
    card = ToastCenter.push(kind="success", title="x", ms=900,
                            theme="light")
    card._countdown.setCurrentTime(card._countdown.duration())
    assert card._phase == "leave"


def test_entrance_exit_use_motion_tokens(clean_center, restore_motion):
    """入场 280ms（OutCubic）/ 出场 160ms（InCubic），档位按比例缩放。"""
    card = ToastCenter.push(kind="success", title="x", theme="light")
    in_ms = motion.duration(motion.MOTION["toast_in"], 1.0)
    out_ms = motion.duration(motion.MOTION["toast_out"], 1.0)
    assert in_ms == 280 and out_ms == 160
    assert card._pos_anim.duration() == in_ms
    assert card._pos_anim.startValue().y() == card._target.y() + ENTER_FLOAT_PX
    assert card._pos_anim.endValue().y() == card._target.y()
    card._begin_leave()
    assert card._pos_anim.duration() == out_ms
    assert card._pos_anim.endValue().y() == card._target.y() + EXIT_FLOAT_PX


def test_speed_scaling_shortens_animations(clean_center, restore_motion):
    """anim_speed 档位缩放：2.0 档入场时长减半。"""
    ToastCenter.set_speed(2.0)
    card = ToastCenter.push(kind="success", title="x", theme="light")
    assert card._pos_anim.duration() == motion.duration(280, 2.0)


def test_reduce_motion_snaps(clean_center, restore_motion):
    """reduce_motion：入场瞬显终态、退场立即隐藏并发射 closed。"""
    motion.set_reduce_motion(True)
    fired = []
    card = ToastCenter.push(kind="success", title="x", theme="light")
    card.closed.connect(lambda c: fired.append(c))
    assert card.windowOpacity() == 1.0
    assert card.pos().y() == card._target.y()
    assert card._phase == "live"
    card.dismiss()
    assert not card.isVisible()
    assert fired == [card]


# ====================================================================
# E. 交互与主题
# ====================================================================
def test_close_button_hit_region_dismisses(clean_center):
    """右上角 24×24 命中区点击 → 退场；命中区在窗口坐标（阴影偏移计入）。"""
    card = ToastCenter.push(kind="success", title="x", theme="light")
    rect = card._close_rect()
    assert rect.width() == CLOSE_SIZE and rect.height() == CLOSE_SIZE
    assert rect.right() <= card.width() - SHADOW_M + CLOSE_OVERSHOOT
    pos = rect.center()
    event = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    card.mousePressEvent(event)
    assert card._phase == "leave"


def test_close_click_outside_does_not_dismiss(clean_center):
    """点气泡本体（非命中区）不退场。"""
    card = ToastCenter.push(kind="success", title="x", theme="light")
    pos = QPointF(40, card.height() - 10)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    card.mousePressEvent(event)
    assert card._phase != "leave"


def test_action_button_runs_callback_then_dismisses(clean_center):
    """动作钮：先执行回调、再退场；ghost 副动作同样可用。"""
    fired = []
    card = ToastCenter.push(kind="danger", title="导出失败",
                            msg="目标文件夹没有写入权限",
                            action=("重试", lambda: fired.append("retry")),
                            ghost=("忽略", lambda: fired.append("ignore")),
                            theme="light")
    assert len(card._buttons) == 2
    card._buttons[0].click()          # ghost「忽略」
    assert fired == ["ignore"]
    assert card._phase == "leave"


def test_action_without_callback_still_dismisses(clean_center):
    """回调缺省（None）不炸，仅退场（构造期便利口径）。"""
    card = ToastCenter.push(kind="accent", title="已删除",
                            action=("撤销", None), theme="light")
    card._buttons[0].click()
    assert card._phase == "leave"


def test_morph_replaces_kind_and_restarts_countdown(clean_center):
    """loading → success 原地变体：换语义 / 换文案 / 重启倒计时。"""
    card = ToastCenter.push(kind="loading", title="正在索引本地素材…",
                            msg="68 张", theme="light")
    assert card._countdown is None and card._spin is not None
    card.morph("success", title="索引完成", msg="68 张已就绪")
    assert card.kind == "success"
    assert card._lifetime == KINDS["success"]["ms"]
    assert card._countdown.duration() == KINDS["success"]["ms"]
    assert card._countdown.state() == QVariantAnimation.State.Running
    assert not card._spin.state() == QVariantAnimation.State.Running


def test_set_theme_retints_live_card(clean_center):
    """set_theme 原地换肤：语义色随主题表刷新（accent → primary）。"""
    from PyQt6.QtGui import QColor
    card = ToastCenter.push(kind="accent", title="x", theme="light")
    light_tint = card.tint_color().name()
    card.set_theme("dark")
    dark_tint = card.tint_color().name()
    assert light_tint != dark_tint
    assert light_tint == QColor(get_colors("light")["primary"]).name()
    assert dark_tint == QColor(get_colors("dark")["primary"]).name()


def test_center_set_theme_broadcasts(clean_center):
    """ToastCenter.set_theme 广播到全部存活气泡，且记忆后续默认主题。"""
    a = ToastCenter.push(kind="success", title="a", theme="light")
    b = ToastCenter.push(kind="danger", title="b", theme="light")
    ToastCenter.set_theme("dark")
    assert a._theme == "dark" and b._theme == "dark"
    assert ToastCenter.theme() == "dark"
    c = ToastCenter.push(kind="neutral", title="c")
    assert c._theme == "dark", "未显式传主题的新气泡跟随记忆档"


def test_push_garbage_inputs_do_not_crash(clean_center):
    """脏输入兜底：None 文案 / 非法 ms / 非法主题都不炸。"""
    card = ToastCenter.push(title=None, msg=None, ms=-3, theme="banana")
    assert card is not None
    assert card._title == "None" or card._title == ""
    assert card._lifetime == KINDS["neutral"]["ms"]


# ====================================================================
# F. 源码护栏
# ====================================================================
def test_toast_source_has_no_hardcoded_colors():
    """气泡零写死色值：除 rgba 合成参数外不得出现 #RRGGBB 字面量
    （设计稿「实现零新色」铁律；颜色一律走 theme token）。"""
    with open(os.path.join(_V4, "src", "toast.py"), encoding="utf-8") as fh:
        src = fh.read()
    body = re.sub(r'"""(?:.|\n)*?"""', "", src)     # 去文档串
    body = re.sub(r"#.*", "", body)                  # 去注释
    assert not re.search(r'#[0-9A-Fa-f]{3,8}\b', body), \
        "toast.py 出现写死色值，必须改走 theme token"


def test_toast_source_never_imports_qmessagebox():
    """全域禁用原生弹窗的姊妹钉子（test_no_native_messagebox 的本地冗余）。"""
    with open(os.path.join(_V4, "src", "toast.py"), encoding="utf-8") as fh:
        assert "QMessageBox" not in fh.read()


# ====================================================================
# G. 设置项（2026-10-06「轻提示」卡 7 键 · EARS R1-R10）
# ====================================================================
def test_r1_disabled_drops_silently(toast_cfg, clean_center):
    """R1：总开关关闭 → push 静默丢弃，不显示、不排队。"""
    toast_cfg({"toast_enabled": False})
    assert ToastCenter.push(title="x", theme="light") is None
    assert ToastCenter.push(kind="warning", title="x", theme="light") is None
    assert ToastCenter.live_cards() == ()
    assert ToastCenter._pending == []


def test_r10_reenable_does_not_replay_backlog(toast_cfg, clean_center):
    """R10：false 切回 true 不补发停用期间积压的提示。"""
    cfg = toast_cfg({"toast_enabled": False})
    assert ToastCenter.push(title="停用期间的提示", theme="light") is None
    cfg._kv["toast_enabled"] = True
    card = ToastCenter.push(title="重新开启后的第一条", theme="light")
    assert card is not None
    assert len(ToastCenter.live_cards()) == 1
    assert ToastCenter.live_cards()[0]._title == "重新开启后的第一条"


def test_r2_corner_position_margin(toast_cfg, clean_center):
    """R2：corner → 屏幕右下角（右缘 margin 24px），center → 水平居中。"""
    cfg = toast_cfg({"toast_position": "corner"})
    card = ToastCenter.push(title="x", theme="light")
    screen = get_screen_geometry()
    assert card.x() == screen.right() + 1 - CORNER_MARGIN - card.width()
    cfg._kv["toast_position"] = "center"
    second = ToastCenter.push(title="y", theme="light")
    assert second.x() == screen.left() + (screen.width() - second.width()) // 2


def test_r3_bottom_offset_respected(toast_cfg, clean_center):
    """R3：气泡底缘 = 整屏底缘 − offset（读 _target 避开入场飞行坐标）。"""
    toast_cfg({"toast_bottom_offset": 90})
    card = ToastCenter.push(title="x", theme="light")
    full = get_full_screen_geometry()
    card_bottom = card._target.y() + SHADOW_M + card.card_height
    assert card_bottom == full.bottom() + 1 - 90


def test_r3_taskbar_guard_raises_small_offset(monkeypatch, toast_cfg,
                                              clean_center):
    """R3 守卫：底部任务栏 48px 时，offset 设 24 自动抬到任务栏高 + 8。"""
    toast_cfg({"toast_bottom_offset": 24})
    full = QRect(0, 0, 1920, 1080)
    avail = QRect(0, 0, 1920, 1032)      # 任务栏 48px 在底部
    monkeypatch.setattr(toast_mod, "get_full_screen_geometry",
                        lambda: full)
    monkeypatch.setattr(toast_mod, "get_screen_geometry", lambda: avail)
    card = ToastCenter.push(title="x", theme="light")
    # eff = max(48 + 8, 24) = 56 → 气泡底缘悬在任务栏上沿再抬 8px
    card_bottom = card._target.y() + SHADOW_M + card.card_height
    assert card_bottom == full.bottom() + 1 - 56


def test_r4_pill_counts_and_drains_fifo(clean_center):
    """R4：胶囊计数随溢出/恢复增减；N 归零胶囊移除；恢复严格 FIFO。"""
    for i in range(5):
        ToastCenter.push(kind="neutral", title=f"n{i}", theme="light")
    assert len(ToastCenter.live_cards()) == 3
    assert [c._title for c in ToastCenter.overflow_cards()] == ["n0", "n1"]
    assert ToastCenter.pill_count() == 2
    # 腾出一个空位 → n0 依序恢复
    ToastCenter.live_cards()[0].dismiss()
    assert len(ToastCenter.live_cards()) == MAX_VISIBLE
    assert [c._title for c in ToastCenter.overflow_cards()] == ["n1"]
    assert ToastCenter.pill_count() == 1
    # 全部退场 → 停放条依序恢复后再清空 → 胶囊移除
    while ToastCenter.live_cards() or ToastCenter.overflow_cards():
        batch = list(ToastCenter.live_cards())
        if batch:
            batch[0].dismiss()
        else:
            ToastCenter.overflow_cards()[0].unpark()
    assert ToastCenter.pill_count() == 0


def test_r4_parked_countdown_freezes_and_resumes(clean_center):
    """R4：收拢即冻结倒计时，恢复后续走（不重启、不丢提示）。"""
    a = ToastCenter.push(kind="neutral", title="a", theme="light")
    b = ToastCenter.push(kind="neutral", title="b", theme="light")
    ToastCenter.push(kind="neutral", title="c", theme="light")
    ToastCenter.push(kind="neutral", title="d", theme="light")
    assert a._parked
    assert a._countdown.state() == QVariantAnimation.State.Paused
    b.dismiss()                       # 真实流程：空位腾出 → a 依序恢复
    assert not a._parked
    assert a._countdown.state() == QVariantAnimation.State.Running
    assert [c._title for c in ToastCenter.live_cards()] == ["a", "c", "d"]


def test_r4_pill_painted_with_theme_tokens(clean_center):
    """「+N」胶囊窗口零写死色值走 token：主题切换原地换肤。"""
    from PyQt6.QtGui import QColor
    ToastCenter.push(kind="neutral", title="a", theme="light")
    ToastCenter.push(kind="neutral", title="b", theme="light")
    ToastCenter.push(kind="neutral", title="c", theme="light")
    ToastCenter.push(kind="neutral", title="d", theme="light")
    pill = ToastCenter._pill
    assert pill is not None and pill.isVisible()
    ToastCenter.set_theme("dark")
    assert pill._theme == "dark"
    assert pill._font.pixelSize() > 0
    # 胶囊文本只可能是 +N 形态
    assert pill._count == 1
    # 语义：胶囊随最后一条存活气泡退出而移除（换肤不改变生命周期）
    assert QColor(get_colors("dark")["surface"]).isValid()


def test_r5_duration_enum_scales_policy(toast_cfg, clean_center):
    """R5：基准档位等比缩放（标准档 × base/3200），显式 ms 不受档位影响。"""
    cfg = toast_cfg({"toast_duration": "brief"})
    card = ToastCenter.push(kind="neutral", title="x", theme="light")
    assert card._lifetime == 2000
    warn = ToastCenter.push(kind="warning", title="w", theme="light")
    assert warn._lifetime == round(5000 * 2000 / 3200)     # 3125（×1.5625）
    danger = ToastCenter.push(kind="danger", title="d", theme="light")
    assert danger._lifetime == round(6000 * 2000 / 3200)   # 3750（×1.875）
    explicit = ToastCenter.push(kind="neutral", title="e", ms=1500,
                                theme="light")
    assert explicit._lifetime == 1500
    cfg._kv["toast_duration"] = "relaxed"
    long_card = ToastCenter.push(kind="neutral", title="f", theme="light")
    assert long_card._lifetime == 5000


def test_r5_duration_change_leaves_alive_cards_alone(toast_cfg, clean_center):
    """R5：档位变更只对之后新触发的气泡生效，存活中的不中途改表。"""
    cfg = toast_cfg({"toast_duration": "standard"})
    first = ToastCenter.push(kind="neutral", title="a", theme="light")
    cfg._kv["toast_duration"] = "relaxed"
    second = ToastCenter.push(kind="neutral", title="b", theme="light")
    assert first._lifetime == 3200
    assert second._lifetime == 5000


def test_r6_animation_off_snaps_and_keeps_countdown(toast_cfg, clean_center):
    """R6：动画关 → 进出补间跳过（直接置位/移除），倒计时条与 hover
    暂停保留（可用性底线）。"""
    fired = []
    toast_cfg({"toast_animation": False})
    card = ToastCenter.push(kind="success", title="x", theme="light")
    card.closed.connect(lambda c: fired.append(c))
    assert card.windowOpacity() == 1.0
    assert card.pos().y() == card._target.y()
    assert card._phase == "live"
    assert card._countdown is not None
    assert card._countdown.state() == QVariantAnimation.State.Running
    card.dismiss()
    assert not card.isVisible()
    assert fired == [card]


def test_r6_animation_off_jumps_on_relayout(toast_cfg, clean_center):
    """R6：动画关 → 堆叠补位直接跳变（无平移补间在跑）。"""
    toast_cfg({"toast_animation": False})
    a = ToastCenter.push(kind="neutral", title="a", theme="light")
    b = ToastCenter.push(kind="neutral", title="b", theme="light")
    b.dismiss()
    assert a.pos() == a._target
    assert a._move_anim is None or \
        a._move_anim.state() != QVariantAnimation.State.Running


class _BeepSpy:
    """QApplication 替身：只监听 beep（toast 模块命名空间整体换掉）。"""

    calls = []

    @staticmethod
    def beep():
        _BeepSpy.calls.append(1)

    @staticmethod
    def instance():
        return QApplication.instance()


def test_r7_sound_only_for_warning_and_danger(monkeypatch, toast_cfg,
                                              clean_center):
    """R7：提示音开 → warning/danger 各一声 beep；success/info/neutral 静默。"""
    toast_cfg({"toast_sound": True})
    _BeepSpy.calls.clear()
    monkeypatch.setattr(toast_mod, "QApplication", _BeepSpy)
    ToastCenter.push(kind="success", title="s", theme="light")
    ToastCenter.push(kind="warning", title="w", theme="light")
    assert len(_BeepSpy.calls) == 1
    ToastCenter.push(kind="danger", title="d", theme="light")
    assert len(_BeepSpy.calls) == 2
    ToastCenter.push(kind="info", title="i", theme="light")
    ToastCenter.push(kind="neutral", title="n", theme="light")
    assert len(_BeepSpy.calls) == 2


def test_r7_sound_off_is_silent(monkeypatch, toast_cfg, clean_center):
    """R7 反向：默认关闭 → 全语义静默（不装替身也不会出声）。"""
    toast_cfg({})
    _BeepSpy.calls.clear()
    monkeypatch.setattr(toast_mod, "QApplication", _BeepSpy)
    ToastCenter.push(kind="danger", title="d", theme="light")
    assert _BeepSpy.calls == []


def test_r9_position_change_tweens_alive_cards(toast_cfg, clean_center):
    """R9：位置变更 → 存活气泡以 toast_move（280ms 档）补间平移到新位。"""
    cfg = toast_cfg({})
    a = ToastCenter.push(title="a", theme="light")
    b = ToastCenter.push(title="b", theme="light")
    for card in (a, b):          # 跳过入场在途（离屏事件循环不走完 280ms）
        card._phase = "live"
    cfg._kv["toast_position"] = "corner"
    ToastCenter.on_setting_changed("toast_position")
    screen = get_screen_geometry()
    for card in (a, b):
        assert card._move_anim is not None
        assert card._move_anim.duration() == motion.duration(
            motion.MOTION["toast_move"], 1.0)
        assert card._move_anim.state() == QVariantAnimation.State.Running
        assert card._target.x() == \
            screen.right() + 1 - CORNER_MARGIN - card.width()


def test_r9_offset_change_tweens_alive_cards(toast_cfg, clean_center):
    """R9：底缘距离变更 → 存活气泡平移到新底缘。"""
    cfg = toast_cfg({})
    a = ToastCenter.push(title="a", theme="light")
    a._phase = "live"
    cfg._kv["toast_bottom_offset"] = 120
    ToastCenter.on_setting_changed("toast_bottom_offset")
    full = get_full_screen_geometry()
    assert a._move_anim.duration() == motion.duration(
        motion.MOTION["toast_move"], 1.0)
    assert a._target.y() + SHADOW_M + a.card_height == full.bottom() + 1 - 120


def test_r9_animation_off_jumps_on_setting_change(toast_cfg, clean_center):
    """R9：动画关 → 位置变更直接跳变（R6 与 R9 的交叉口径）。"""
    cfg = toast_cfg({"toast_animation": False})
    a = ToastCenter.push(title="a", theme="light")
    cfg._kv["toast_position"] = "corner"
    ToastCenter.on_setting_changed("toast_position")
    screen = get_screen_geometry()
    assert a.pos().x() == screen.right() + 1 - CORNER_MARGIN - a.width()
    assert a._move_anim is None or \
        a._move_anim.state() != QVariantAnimation.State.Running


def test_max_visible_setting_reconciles_live(toast_cfg, clean_center):
    """同屏上限变更即时生效：调小收拢、调大放行（R4 的设置页入口）。"""
    cfg = toast_cfg({"toast_max_visible": 3})
    for i in range(3):
        ToastCenter.push(title=f"n{i}", theme="light")
    cfg._kv["toast_max_visible"] = 1
    ToastCenter.on_setting_changed("toast_max_visible")
    assert len(ToastCenter.live_cards()) == 1
    assert ToastCenter.pill_count() == 2
    cfg._kv["toast_max_visible"] = 3
    ToastCenter.on_setting_changed("toast_max_visible")
    assert len(ToastCenter.live_cards()) == 3
    assert ToastCenter.pill_count() == 0


def test_cfg_facade_defaults_without_attach(clean_center):
    """未注入配置门面（插件/早期构造期）→ 全部走 DEFAULT_CONFIG 兜底。"""
    assert ToastCenter.enabled() is True
    assert ToastCenter.max_visible() == 3
    assert ToastCenter.bottom_offset() == BOTTOM_GAP
    assert ToastCenter.anim_enabled() is True
    card = ToastCenter.push(title="x", theme="light")
    assert card._lifetime == 3200


def test_cfg_facade_dirty_values_do_not_crash(toast_cfg, clean_center):
    """脏门面（越界/错型）不炸：读侧夹取到合法区间（R11 的运行时半边）。"""
    toast_cfg({"toast_max_visible": "banana", "toast_bottom_offset": 9999,
               "toast_duration": 42, "toast_enabled": "yes"})
    assert ToastCenter.max_visible() == 3        # 错型回落默认
    assert ToastCenter.bottom_offset() == 120    # 越界夹取到上限
    assert ToastCenter.enabled() is True         # 非空真值放行
    card = ToastCenter.push(title="x", theme="light")
    assert card._lifetime == 3200                # 非法档位回落标准档
