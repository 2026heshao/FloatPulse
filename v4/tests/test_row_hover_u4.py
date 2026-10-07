# -*- coding: utf-8 -*-
"""U4 列表行/委托 hover 插值护栏（2026-10-07 规格 G5）。

契约（规格 §2 G5 / §3 反馈四律 / §6 Token / §7 U4 行）：

  A. 行为侧 —— 行 hover 底色不再是 State_MouseOver 一帧瞬变，而是行级
     hp 进度 120ms OutCubic 插值（motion ``fast`` 档，禁新时长字面量）；
     可打断重定向（中途 leave/move 都从当前值出发）；reduce_motion 开启
     → 0ms 瞬显落终态；anim_speed 档位经 controls.set_ui_speed 广播缩放
     （RowHoverController.set_speed 同口径）。
  B. 性能侧 —— 所有活跃行 tween 由**单 QTimer** 驱动，每帧只对受影响行
     区域 viewport().update(rect)，禁全 viewport 重刷；hover 扫过多行时
     tween 收敛（离开行回落、进入行抬升，同一条时间轴）。
  C. 清零侧 —— scrollbar valueChanged / 拖拽手势（DragEnter）开始时，
     活跃 hp 缓存强制清零并单次 update。
  D. 像素侧 —— rest（hp<=ε）渲染与改动前基线**逐字节一致**（基线由
     改前代码生成，存 v4/tests/baselines/，只许重生成不许放宽比较）；
     hover 端点色与旧端点完全一致（任务行 surface_2 实底 / 素材格
     primary_a08+primary_a30 环 / 碎片行 $primary_a08 合成）。

环境坑对齐（docs 验证口径）：offscreen 平台、QApplication 引用留模块级、
hover 用合成 QHoverEvent sendEvent（不走 QTest.mouseMove，规避离屏硬崩）、
像素断言全部 grab/paint 到 QImage 后比较。
====================================================================
"""

import datetime
import os
import sys
import tempfile

# ★路径自举：本文件必须可单独跑（pytest v4/tests/test_row_hover_u4.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_APP = None


def _app():
    """取（必要时创建）QApplication，引用留模块级防静默崩进程。"""
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


BASELINE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "baselines")

# 插值「不可见」阈值（与 src/row_hover.py ALPHA_EPS 同值；此处独立钉值，
# 两侧漂移即红灯 —— paint 侧省略带与断言口径必须同步改）
_EPS = 0.004


@pytest.fixture()
def restore_motion():
    yield
    from src import motion
    from src.row_hover import RowHoverController
    motion.set_reduce_motion(False)
    RowHoverController.set_speed(1.0)


# ====================================================================
# 场景构造（同时被基线生成脚本复用 —— 场景代码与护栏对拍必须同源）
# ====================================================================
def _scene_assets(tmpdir):
    _app()      # ★ QApplication 先于 QWidget（离屏硬崩规避）
    """素材网格平铺态 rest 场景：2 行 × 4 列（图片缓存命中 / 文件图标 /
    失效文件三类覆盖），直接 paint 到 QPixmap → QImage。"""
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import (QListWidget, QListWidgetItem, QStyle,
                                 QStyleOptionViewItem)
    from src.assets_panel import _AssetThumbDelegate
    from src.temp_asset_manager import AssetInfo

    def _t(sec):
        base = datetime.datetime(2026, 10, 7, 9, 0, 0)
        return (base + datetime.timedelta(seconds=sec)).strftime(
            "%Y-%m-%d %H:%M:%S")

    def _make_png(name, w, h, color):
        path = os.path.join(tmpdir, name)
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(*color))
        assert img.save(path)
        return path

    def _make_file(name):
        path = os.path.join(tmpdir, name)
        with open(path, "wb") as f:
            f.write(b"x" * 24)
        return path

    # (id, 名称, 路径, is_image, 缩略图预填色|None)
    specs = [
        (1, "截图一.png", _make_png("a.png", 64, 48, (31, 142, 110)),
         True, (31, 142, 110)),
        (2, "截图二.png", _make_png("b.png", 64, 48, (52, 120, 180)),
         True, (52, 120, 180)),
        (3, "会议纪要.txt", _make_file("notes.txt"), False, None),
        (4, "年度报告.pdf", _make_file("report.pdf"), False, None),
        (5, "打包归档.zip", _make_file("bundle.zip"), False, None),
        (6, "丢失文档.doc", os.path.join(tmpdir, "gone.doc"), False, None),
        (7, "演示录像.mp4", _make_file("demo.mp4"), False, None),
        (8, "合影.png", _make_png("c.png", 64, 48, (200, 90, 60)),
         True, (200, 90, 60)),
    ]
    assets = [AssetInfo(i, name, path, is_img, 24, _t(i * 7))
              for i, name, path, is_img, _c in specs]

    lst = QListWidget()
    d = _AssetThumbDelegate(type("H", (), {})(), {}, loader=None)
    # 缩略图预填缓存（命中路径，绕开异步管线，保证逐字节确定性）
    for asset, (_i, _n, _p, _img, rgb) in zip(assets, specs):
        if rgb is not None:
            img = QImage(d.THUMB_W, d.THUMB_H, QImage.Format.Format_RGB32)
            img.fill(QColor(*rgb))
            d._thumbs[asset.asset_id] = QPixmap.fromImage(img)
    # 真实模型链路：素材对象挂 UserRole，逐条 addItem（与真机同构）
    for asset in assets:
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, asset)
        lst.addItem(it)

    pix = QPixmap(4 * d.CELL_W, 2 * d.CELL_H)
    pix.fill()
    painter = QPainter(pix)
    try:
        for row in range(8):
            opt = QStyleOptionViewItem()
            opt.rect = QRect((row % 4) * d.CELL_W, (row // 4) * d.CELL_H,
                             d.CELL_W, d.CELL_H)
            opt.state = QStyle.StateFlag.State_Enabled
            opt.widget = lst
            d.paint(painter, opt, lst.model().index(row, 0))
    finally:
        painter.end()
    return pix.toImage()


def _scene_tasks():
    _app()      # ★ QApplication 先于 QWidget（离屏硬崩规避）
    """任务行 rest 场景：组标题 + 逾期/今天/已完成 三行。"""
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QStyle, \
        QStyleOptionViewItem
    from src.task_delegate import (
        TaskItemDelegate, KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE,
        ROLE_DONE, ROW_HEIGHT, HEADER_HEIGHT)
    from src.task_manager import (
        KIND_ROW, KIND_HEADER, STATE_OVERDUE, STATE_TODAY, STATE_NONE)
    from src.theme import get_colors

    lst = QListWidget()
    d = TaskItemDelegate(get_colors("light"))

    header = QListWidgetItem("逾期")
    header.setData(KIND_ROLE, KIND_HEADER)
    header.setData(ROLE_REL, "2")
    header.setData(ROLE_STATE, STATE_OVERDUE)
    lst.addItem(header)

    def _task(task_id, title, rel, state, done=False):
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, task_id)
        it.setData(KIND_ROLE, KIND_ROW)
        it.setData(ROLE_TITLE, title)
        it.setData(ROLE_REL, rel)
        it.setData(ROLE_STATE, state)
        it.setData(ROLE_DONE, done)
        lst.addItem(it)

    _task(11, "完成季度汇报材料", "逾期 2 天", STATE_OVERDUE)
    _task(12, "回复审阅意见并整理会议记录", "今天", STATE_TODAY)
    _task(13, "整理素材库", "3 天前", STATE_NONE, done=True)

    pix = QPixmap(320, HEADER_HEIGHT + ROW_HEIGHT * 3)
    pix.fill()
    painter = QPainter(pix)
    try:
        for row in range(lst.count()):
            opt = QStyleOptionViewItem()
            h = HEADER_HEIGHT if row == 0 else ROW_HEIGHT
            top = HEADER_HEIGHT if row > 0 else 0
            opt.rect = QRect(0, top if row == 0 else
                             HEADER_HEIGHT + (row - 1) * ROW_HEIGHT,
                             320, h)
            opt.state = QStyle.StateFlag.State_Enabled
            opt.widget = lst
            d.paint(painter, opt, lst.model().index(row, 0))
    finally:
        painter.end()
    return pix.toImage()


def _scene_fragments():
    _app()      # ★ QApplication 先于 QWidget（离屏硬崩规避）
    """碎片行 rest 场景：日期组行 + 命中高亮 + 普通行。"""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QStyle, \
        QStyleOptionViewItem
    from src.fragments_panel import _MatchHighlightDelegate, TIME_ROLE, \
        CAT_ROLE
    from src.fragment_classifier import CAT_TEXT, CAT_CODE
    from src.theme import get_colors

    lst = QListWidget()
    d = _MatchHighlightDelegate(lst)
    d.set_theme(get_colors("light"))
    d.keyword = "配置"

    date_row = QListWidgetItem("今天")
    lst.addItem(date_row)

    def _frag(text, time_txt, cat):
        it = QListWidgetItem(text)
        it.setData(TIME_ROLE, time_txt)
        it.setData(CAT_ROLE, cat)
        lst.addItem(it)

    _frag("修改主题配置文件后重启应用", "10:24", CAT_TEXT)
    _frag("整理素材库日常归档脚本", "09:12", CAT_CODE)

    pix = QPixmap(400, 28 * lst.count())
    pix.fill()
    painter = QPainter(pix)
    try:
        for row in range(lst.count()):
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, row * 28, 400, 28)
            opt.state = QStyle.StateFlag.State_Enabled
            opt.widget = lst
            d.paint(painter, opt, lst.model().index(row, 0))
    finally:
        painter.end()
    return pix.toImage()


# ====================================================================
# 基线生成入口（改前代码跑一次；基线只许重生成、不许放宽比较）
# ====================================================================
def generate_baselines():
    _app()
    os.makedirs(BASELINE_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        _scene_assets(td).save(
            os.path.join(BASELINE_DIR, "u4_assets_flat_rest.png"))
    _scene_tasks().save(os.path.join(BASELINE_DIR, "u4_task_rows_rest.png"))
    _scene_fragments().save(
        os.path.join(BASELINE_DIR, "u4_frag_rows_rest.png"))


def _load_baseline(name):
    from PyQt6.QtGui import QImage
    path = os.path.join(BASELINE_DIR, name)
    assert os.path.exists(path), (
        "U4 基线缺失：%s —— 用改前代码跑 generate_baselines() 生成" % name)
    img = QImage(path)
    assert not img.isNull()
    return img


def _assert_same_image(img, baseline, name):
    if img.size() != baseline.size():
        raise AssertionError(
            "%s 尺寸漂移：new=%s baseline=%s" % (name, img.size(),
                                                baseline.size()))
    diff = []
    for yy in range(img.height()):
        for xx in range(img.width()):
            a, b = img.pixelColor(xx, yy), baseline.pixelColor(xx, yy)
            if a.rgba() != b.rgba():
                diff.append((xx, yy, a.name(), b.name()))
                if len(diff) >= 8:
                    break
        if len(diff) >= 8:
            break
    assert not diff, (
        "%s rest 渲染与改前基线不一致（byte-identical 红线）：前 %d 处差异 "
        "= %s" % (name, len(diff), diff))


# ====================================================================
# 行视图测试基建：TaskListWidget + TaskItemDelegate（真实模型/视图链路）
# ====================================================================
def _make_task_view(rows=(("task", 11, "完成季度汇报材料"),
                          ("task", 12, "回复审阅意见"),
                          ("task", 13, "整理素材库"))):
    _app()      # ★ QApplication 必须先于任何 QWidget（离屏硬崩规避）
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem
    from src.task_delegate import (
        TaskItemDelegate, KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE,
        ROLE_DONE)
    from src.task_manager import KIND_ROW, KIND_HEADER, STATE_NONE
    from src.theme import get_colors

    lst = QListWidget()
    d = TaskItemDelegate(get_colors("light"), lst)
    lst.setItemDelegate(d)
    # 高度 100：内容(26+3*32=122) 高于视口 → 滚动条有量程（滚动清零用例
    # 需要 valueChanged 真的触发；内容不超视口时 setValue 是 no-op）
    lst.resize(300, 100)

    def _add(kind, *args):
        it = QListWidgetItem()
        if kind == "header":
            it.setData(KIND_ROLE, KIND_HEADER)
            it.setData(ROLE_REL, "3")
        else:
            task_id, title = args
            it.setData(Qt.ItemDataRole.UserRole, task_id)
            it.setData(KIND_ROLE, KIND_ROW)
            it.setData(ROLE_TITLE, title)
            it.setData(ROLE_REL, "今天")
            it.setData(ROLE_STATE, STATE_NONE)
            it.setData(ROLE_DONE, False)
        lst.addItem(it)

    _add("header")
    for kind, *rest in rows:
        _add(kind, *rest)
    lst.show()
    _app().processEvents()
    return lst, d


def _hover(lst, etype, pos):
    """合成 hover 事件直发 viewport（离屏不走 QTest.mouseMove，防硬崩）。"""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QHoverEvent
    from PyQt6.QtWidgets import QApplication
    ev = QHoverEvent(etype, QPointF(pos), QPointF(pos))
    QApplication.sendEvent(lst.viewport(), ev)


def _row_pos(lst, row):
    return lst.visualRect(lst.model().index(row, 0)).center()


def _settle(lst, ms=400):
    """推进事件循环直至 tween 落定（qWait 驱动真实 QTimer）。"""
    from PyQt6.QtTest import QTest
    QTest.qWait(ms)


# ====================================================================
# A. 行为侧：进入/离开/重定向/reduce_motion/档位缩放
# ====================================================================
class _FakeClock:
    """可控单调时钟：替身 row_hover.time，让 tween 帧推进完全确定
    （真实 QTest.qWait 窗口在冷启动/高负载下可能一帧都没 tick，中间帧
    断言偶发假红 —— 2026-10-07 复合跑实测；时间推 1s 一帧落终值语义
    与生产 _tick 相同，仅由测试手动驱动帧）。"""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


def _manual_tick(ctrl, clock, advance_s):
    """推假时钟并手动驱动一帧（绕开真实 QTimer 的调度抖动）。"""
    clock.t += advance_s
    ctrl._tick()


def test_hover_enter_interpolates_and_settles(restore_motion, monkeypatch):
    """enter → hp 从 0 渐增（中间帧 0<hp<1），时长走完后落终值 1。"""
    import src.row_hover as rh
    clock = _FakeClock()
    monkeypatch.setattr(rh, "time", clock)
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _manual_tick(d._hover, clock, 0.040)
        mid = d._hover.hp_for_row(1)
        assert _EPS < mid < 1.0, (
            "hover 进入中间帧 hp=%.3f —— 应处于 0~1 插值中（瞬变则恒为 0/1）"
            % mid)
        _manual_tick(d._hover, clock, 1.0)
        assert d._hover.hp_for_row(1) == 1.0, "hover 应落终值 1"
    finally:
        lst.deleteLater()


def test_hover_move_between_rows_converges(restore_motion, monkeypatch):
    """hover 扫过多行：离开行回落 + 进入行抬升共享单 QTimer，收敛不残留。"""
    import src.row_hover as rh
    clock = _FakeClock()
    monkeypatch.setattr(rh, "time", clock)
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _manual_tick(d._hover, clock, 0.040)
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 2))
        _manual_tick(d._hover, clock, 0.040)
        mid2 = d._hover.hp_for_row(2)
        assert _EPS < mid2 < 1.0, (
            "新行应处于 0~1 插值中（瞬变则恒为 0/1）：%s" % mid2)
        mid1 = d._hover.hp_for_row(1)
        assert _EPS < mid1 < 0.5, (
            "旧行应从离开前值回落中（瞬变则恒为 0/1）：%s" % mid1)
        _manual_tick(d._hover, clock, 1.0)
        assert d._hover.hp_for_row(2) == 1.0
        assert d._hover.hp_for_row(1) <= _EPS, "旧行必须回落干净"
        assert d._hover.active_rows() in ([], [2]), (
            "tween 收敛后活跃表只允许终态行或空表：%s" % d._hover.active_rows())
    finally:
        lst.deleteLater()


def test_hover_leave_redirects_to_zero(restore_motion):
    """中途 leave：重定向到 0（从当前值出发），最终完全回落。"""
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 30)
        _hover(lst, QEvent.Type.HoverLeave, _row_pos(lst, 1))
        _settle(lst, 30)
        assert d._hover.hp_for_row(1) < 1.0, "leave 后应向下插值"
        _settle(lst, 400)
        assert d._hover.hp_for_row(1) <= _EPS, "离开必须归零"
    finally:
        lst.deleteLater()


def test_reduce_motion_snaps_to_endpoint(restore_motion):
    """reduce_motion 开 → 0ms 瞬显（语义是瞬显，不是缩短）：无 tween。"""
    from PyQt6.QtCore import QEvent
    from src import motion
    motion.set_reduce_motion(True)
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        assert d._hover.hp_for_row(1) == 1.0, "减弱动效下 hover 应瞬时到位"
        assert not d._hover.is_animating(), "减弱动效下不应有活跃 tween"
        _hover(lst, QEvent.Type.HoverLeave, _row_pos(lst, 1))
        assert d._hover.hp_for_row(1) <= _EPS, "瞬显离开同样直接归零"
    finally:
        lst.deleteLater()


def test_anim_speed_scales_duration_via_motion(restore_motion):
    """档位缩放走 motion 唯一口径：2.0 档 → fast=120ms 缩成 60ms。"""
    from PyQt6.QtCore import QEvent
    from src import motion
    from src.row_hover import RowHoverController
    RowHoverController.set_speed(2.0)
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        assert d._hover.last_duration == motion.duration(120, 2.0), (
            "tween 时长必须经 motion.duration 缩放，禁旁路字面量")
        _settle(lst, 200)
        assert d._hover.hp_for_row(1) == 1.0, "2.0 档 60ms 应早已落定"
    finally:
        lst.deleteLater()


def test_set_ui_speed_broadcast_reaches_row_hover():
    """controls.set_ui_speed 单一入口必须覆盖 RowHoverController（U4 接线）。"""
    from src import controls, row_hover
    controls.set_ui_speed(1.5)
    try:
        assert row_hover.RowHoverController._speed == pytest.approx(1.5)
    finally:
        controls.set_ui_speed(1.0)


# ====================================================================
# B. 清零侧：滚动 / 拖拽
# ====================================================================
def test_scroll_clears_active_hover_cache(restore_motion):
    """scrollbar valueChanged → 活跃 hp 缓存强制清零（护栏断言）。"""
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 400)
        assert d._hover.hp_for_row(1) == 1.0
        sb = lst.verticalScrollBar()
        assert sb.maximum() > 0, "场景必须可滚（否则 setValue 是 no-op）"
        sb.setValue(sb.value() + 10)
        assert d._hover.active_rows() == [], (
            "滚动后活跃 hover 缓存必须为空（强制清零）")
        assert d._hover.hp_for_row(1) <= _EPS
        # 清零后鼠标再动（真实 hover 事件）→ 从 0 重新插值，交互恢复
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 400)
        assert d._hover.hp_for_row(1) == 1.0, (
            "滚动清零后真实鼠标移动必须恢复 hover 插值")
    finally:
        lst.deleteLater()


def test_drag_enter_clears_active_hover_cache(restore_motion):
    """拖拽手势开始（DragEnter）→ 活跃 hp 缓存清零。

    ★ 直发合成 DragEnter 给真实 view 会闯进 Qt 拖拽机内状态，离屏
    必崩（实测 access violation）—— 改在控制器 eventFilter 层面直调，
    只钉「DragEnter → 清零」这条自家 wiring，不碰 Qt dnd 内核。
    另注：Qt 只把拖拽事件投递给 setAcceptDrops(True) 的控件，不接
    受拖放的列表（任务/碎片列表现状）永远收不到 DragEnter。"""
    from PyQt6.QtCore import QPoint, QEvent, Qt, QMimeData
    from PyQt6.QtGui import QDragEnterEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 400)
        assert d._hover.hp_for_row(1) == 1.0
        ev = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, QMimeData(),
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        d._hover.eventFilter(lst.viewport(), ev)   # 直调自家过滤器分支
        assert d._hover.active_rows() == [], "拖拽开始必须清零 hover 缓存"
    finally:
        lst.deleteLater()


# ====================================================================
# C. 语义侧：组标题不参与 / rest 像素零差异路径
# ====================================================================
def test_task_header_row_is_not_hoverable(restore_motion):
    """组标题行不参与 hover 插值（旧口径：header 无 hover 底色）。"""
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 0))
        _settle(lst, 200)
        assert d._hover.hp_for_row(0) <= _EPS, "组标题行不得有 hover 进度"
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 200)
        assert d._hover.hp_for_row(1) == 1.0, "任务行 hover 不受影响"
    finally:
        lst.deleteLater()


def test_model_rebuild_clears_stale_hover(restore_motion):
    """refresh 重建列表后残留 hp 不得污染新行（模型变更即清零）。"""
    from PyQt6.QtCore import QEvent
    lst, d = _make_task_view()
    try:
        _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
        _settle(lst, 400)
        assert d._hover.hp_for_row(1) == 1.0
        lst.clear()
        _app().processEvents()
        assert d._hover.active_rows() == [], "模型重建必须清空 hover 缓存"
    finally:
        lst.deleteLater()


def test_view_teardown_with_active_hover_does_not_crash(restore_motion):
    """控件销毁路径（rowsAboutToBeRemoved → 清零）不得反碰 viewport：
    QListWidget 析构期清零若 update 半死视口会崩进程（离屏实测）。"""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtTest import QTest
    lst, d = _make_task_view()
    _hover(lst, QEvent.Type.HoverMove, _row_pos(lst, 1))
    _settle(lst, 400)
    assert d._hover.hp_for_row(1) == 1.0
    lst.deleteLater()
    QTest.qWait(50)          # 让 deferred delete 真正执行（含模型拆行）
    assert d._hover.active_rows() == []


# ====================================================================
# D. 像素侧：rest byte-identical 基线 + 端点/中间帧
# ====================================================================
def test_assets_flat_rest_byte_identical():
    """素材网格平铺态 rest：与改前基线逐字节一致（既定红线）。"""
    _app()
    with tempfile.TemporaryDirectory() as td:
        img = _scene_assets(td)
    _assert_same_image(img, _load_baseline("u4_assets_flat_rest.png"),
                       "素材平铺态")


def test_task_rows_rest_byte_identical():
    """任务行 rest：与改前基线逐字节一致。"""
    _app()
    _assert_same_image(_scene_tasks(), _load_baseline("u4_task_rows_rest.png"),
                       "任务行")


def test_fragments_rows_rest_byte_identical():
    """碎片行 rest：与改前基线逐字节一致。"""
    _app()
    _assert_same_image(_scene_fragments(),
                       _load_baseline("u4_frag_rows_rest.png"), "碎片行")


def test_task_row_hover_endpoint_is_surface_2():
    """端点钉值：任务行 hp=1 的行底 = surface_2 实底（与旧端点同值）。"""
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem
    from PyQt6.QtCore import QRect
    from src.theme import get_colors
    _app()
    lst, d = _make_task_view()
    try:
        colors = get_colors("light")
        d._hover._hp[1] = 1.0        # 直写进度（paint 只读 hp，等价终态帧）
        pix = QPixmap(300, 32)
        pix.fill()
        p = QPainter(pix)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 300, 32)
        opt.state = QStyle.StateFlag.State_Enabled
        opt.widget = lst
        d.paint(p, opt, lst.model().index(1, 0))
        p.end()
        img = pix.toImage()
        expected = QColor(str(colors["surface_2"]))
        sample = img.pixelColor(290, 16)     # 行尾留白区（无内容像素）
        assert sample == expected, (
            "hp=1 行底应恰为 surface_2 实底（%s），实取 %s —— 端点漂移"
            % (expected.name(), sample.name()))
    finally:
        lst.deleteLater()


def test_assets_cell_hover_progress_changes_pixels():
    """素材格 hover 中间帧像素渐变：hp=0 / 0.5 / 1 三帧两两不同。"""
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtWidgets import (QListWidget, QListWidgetItem, QStyle,
                                 QStyleOptionViewItem)
    from src.assets_panel import _AssetThumbDelegate
    _app()
    lst = QListWidget()
    d = _AssetThumbDelegate(type("H", (), {})(), {}, loader=None)
    try:
        # 挂一个最小素材替身（paint 只读这些成员；文件失效路径画
        # panel_fill 底 + 图标，hover 叠色在其上，渐变可断言）。
        # ★ 必须带 size_display()——paint 元信息行会调用，缺了会让
        #   paint 半途抛错（painter 未 restore，下一帧直接段错误）。
        from types import SimpleNamespace
        asset = SimpleNamespace(
            asset_id=1, original_name="丢失.doc", is_image=False,
            stored_path="", added_time="", size_display=lambda: "24 B")
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, asset)
        lst.addItem(it)
        frames = []
        for hp in (0.0, 0.5, 1.0):
            d._hover._hp[0] = hp
            pix = QPixmap(d.CELL_W, d.CELL_H)
            pix.fill()
            p = QPainter(pix)
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, d.CELL_W, d.CELL_H)
            opt.state = QStyle.StateFlag.State_Enabled
            opt.widget = lst
            d.paint(p, opt, lst.model().index(0, 0))
            p.end()
            frames.append(pix.toImage())
        assert _img_differs(frames[0], frames[1]), (
            "hp=0 与 hp=0.5 像素相同 —— 插值中间帧没有落到外观")
        assert _img_differs(frames[1], frames[2]), (
            "hp=0.5 与 hp=1 像素相同 —— 端点没有落到外观")
    finally:
        lst.deleteLater()


def _img_differs(a, b, tol=6, min_px=4):
    if a.size() != b.size():
        return True
    diff = 0
    for yy in range(a.height()):
        for xx in range(a.width()):
            ca, cb = a.pixelColor(xx, yy), b.pixelColor(xx, yy)
            if ((ca.red() - cb.red()) ** 2 + (ca.green() - cb.green()) ** 2
                    + (ca.blue() - cb.blue()) ** 2) > tol * tol:
                diff += 1
                if diff >= min_px:
                    return True
    return False


def test_fragments_hover_endpoint_composite_matches_primary_a08():
    """碎片行 hp=1 叠色端点：与 QSS $primary_a08 的白底合成一致（±2）。"""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtWidgets import (QListWidget, QListWidgetItem, QStyle,
                                 QStyleOptionViewItem)
    from src.fragments_panel import _MatchHighlightDelegate, TIME_ROLE, \
        CAT_ROLE
    from src.fragment_classifier import CAT_TEXT
    from src.theme import get_colors
    _app()
    lst = QListWidget()
    d = _MatchHighlightDelegate(lst)
    d.set_theme(get_colors("light"))
    it = QListWidgetItem("修改主题配置文件后重启应用")
    it.setData(TIME_ROLE, "10:24")
    it.setData(CAT_ROLE, CAT_TEXT)
    lst.addItem(it)
    try:
        d._hover._hp[0] = 1.0
        pix = QPixmap(400, 28)
        pix.fill(QColor("#FFFFFF"))
        p = QPainter(pix)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 400, 28)
        opt.state = QStyle.StateFlag.State_Enabled
        opt.widget = lst
        d.paint(p, opt, lst.model().index(0, 0))
        p.end()
        img = pix.toImage()
        primary = QColor(str(get_colors("light")["primary"]))
        a = 20 / 255.0            # $primary_a08 → int(0.08*255)=20（同口径）
        expect = QColor(
            int(round(primary.red() * a + 255 * (1 - a))),
            int(round(primary.green() * a + 255 * (1 - a))),
            int(round(primary.blue() * a + 255 * (1 - a))))
        # 采样行内右端留白（避开文字/时间/类别条）
        sample = img.pixelColor(300, 14)
        assert (abs(sample.red() - expect.red()) <= 2
                and abs(sample.green() - expect.green()) <= 2
                and abs(sample.blue() - expect.blue()) <= 2), (
            "碎片行 hover 端点合成色 %s 偏离 $primary_a08 期望 %s"
            % (sample.name(), expect.name()))
    finally:
        lst.deleteLater()
