# -*- coding: utf-8 -*-
"""
====================================================================
列表行/委托 hover 插值控制器  -  row_hover（U4，2026-10-07 规格 G5）
====================================================================
把 QStyledItemDelegate 行级 hover 底色的「State_MouseOver 一帧瞬变」
统一升级为 **行级 hp 进度 120ms OutCubic 插值**（规格 §3 反馈四律 /
§6 Token）。三个自绘 hover 底色的 delegate（任务行 / 素材格 / 碎片行）
共用本控制器；笔记行等 hover 归 QSS 的 delegate 不接入（不强加）。

设计要点
--------
1. **行级 hp 缓存**：以 ``index.row()`` 为键存 0~1 进度；模型增删/重置/
   layoutChanged 即清空（refresh 重建列表后残留进度不得污染新行）。
2. **单 QTimer 驱动全部活跃 tween**（性能红线）：每帧只对受影响行区域
   ``viewport().update(rect)``，禁全 viewport 重刷；hover 扫过多行时
   离开行回落、进入行抬升共用一条时间轴，自然收敛。
3. **可打断重定向**：retarget 始终从当前 hp 出发（stop → start 语义，
   与 SmoothButton._glide 同口径），快速进出不排队不残留。
4. **降级可用**：时长一律经 ``motion.eased_ms("fast", speed)`` 唯一口
   —— reduce_motion 开 → 返回 0 → 直接落终态（瞬显，不是缩短）；
   anim_speed 档位经 :meth:`RowHoverController.set_speed` 全局缩放，
   由 ``controls.set_ui_speed`` 广播接线（与 ToggleSwitch._thp 同法）。
5. **rest 零差异**：paint 侧经 :func:`hover_amount` 取进度，hp<=
   ``ALPHA_EPS`` 时调用方走原 rest 分支 —— 非 hover 态渲染与旧版
   逐字节一致（平铺态红线）。
6. **滚动/拖拽清零**：attach 时挂 view 的纵/横 scrollbar
   ``valueChanged`` 与 viewport 的 DragEnter —— 触发即清空活跃缓存并
   单次 update（规格 §7 U4 强制项）。

事件来源：viewport 的 HoverEnter/HoverMove/HoverLeave（QAbstractItemView
的 viewport 天生开启 hover）；paint 侧首次取进度时懒挂接（delegate 首帧
才认识自己的 view，attach 幂等可反复调用）。
====================================================================
"""

import time

from PyQt6.QtCore import QObject, QTimer, QEvent
from PyQt6.QtWidgets import QStyle

from src import motion

# 推进节拍（60fps 刷新档）：这是**采样步长**不是时长 token —— 所有
# 时长/缓动仍唯一走 motion（fast=120ms OutCubic），不在此新增字面量。
_FRAME_MS = 16

# 进度「不可见」阈值（≈1/255）：低于它 paint 侧直接走原 rest 路径，
# 保证非 hover 态与旧版逐字节一致。护栏测试独立钉了同值，两侧必须同步。
ALPHA_EPS = 0.004


def _out_cubic(t: float) -> float:
    """OutCubic 缓动（motion.EASE["out"] 的解析式；timer 驱动按帧采样）。"""
    return 1.0 - (1.0 - t) ** 3


class RowHoverController(QObject):
    """一个 delegate 一份：行级 hover 进度缓存 + 单定时器 tween 驱动。"""

    _speed = 1.0    # anim_speed 缩放系数（set_ui_speed 广播写入口径）

    @classmethod
    def set_speed(cls, speed) -> None:
        """动画档位广播入口（controls.set_ui_speed 统一调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, parent=None, hoverable=None):
        super().__init__(parent)
        self._view = None
        self._hp = {}        # row -> float(0..1) 当前进度（终态也驻留）
        self._tweens = {}    # row -> [start, target, t0, dur_ms]
        self._hover_row = None
        self._hoverable = hoverable   # Optional[Callable[[QModelIndex], bool]]
        self.last_duration = 0        # 最近一次 retarget 的时长（测试/诊断）
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # ---------------- 挂接（幂等，paint 首帧懒接） ----------------
    def attach(self, view) -> None:
        """绑定宿主 view：viewport 事件过滤器 + scrollbar/模型清零挂接。"""
        if view is None or self._view is view:
            return
        self._view = view
        viewport = view.viewport()
        if viewport is not None:
            viewport.installEventFilter(self)
        for sb in (view.verticalScrollBar(), view.horizontalScrollBar()):
            if sb is not None:
                sb.valueChanged.connect(self._on_scroll)
        model = view.model()
        if model is not None:
            model.rowsInserted.connect(self._on_model_changed)
            model.rowsAboutToBeRemoved.connect(self._on_model_changed)
            model.modelReset.connect(self._on_model_changed)
            model.layoutChanged.connect(self._on_model_changed)

    # ---------------- paint 侧取数 ----------------
    def hp_for_row(self, row: int) -> float:
        """行当前 hover 进度（0~1；无记录 = 0，调用方走原 rest 分支）。"""
        return self._hp.get(int(row), 0.0)

    def active_rows(self) -> list:
        """有进度或 tween 的行号（滚动/拖拽清零的护栏断言口）。"""
        return sorted(set(self._hp) | set(self._tweens))

    def is_animating(self) -> bool:
        return bool(self._tweens)

    # ---------------- 事件 ----------------
    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        et = event.type()
        if et == QEvent.Type.HoverEnter or et == QEvent.Type.HoverMove:
            self._on_move(event.position().toPoint())
        elif et == QEvent.Type.HoverLeave:
            self._on_leave()
        elif et == QEvent.Type.DragEnter:
            # 拖拽手势开始：hover 语义失效，强制清零（规格 §7 U4）
            self.clear_all("drag")
        return False

    def _on_move(self, pos) -> None:
        view = self._view
        if view is None or view.model() is None:
            return
        index = view.indexAt(pos)
        row = index.row() if index.isValid() else None
        if row == self._hover_row:
            return
        old, self._hover_row = self._hover_row, row
        if old is not None:
            self.retarget(old, 0.0)
        if row is not None:
            if self._hoverable is None or self._hoverable(index):
                self.retarget(row, 1.0)
            else:
                # 组标题等不可 hover 行：确保无残留进度
                self.retarget(row, 0.0)

    def _on_leave(self) -> None:
        if self._hover_row is not None:
            self.retarget(self._hover_row, 0.0)
            self._hover_row = None

    def _on_scroll(self, *_):
        self.clear_all("scroll")

    def _on_model_changed(self, *_):
        self.clear_all("model")

    # ---------------- tween 核心 ----------------
    def retarget(self, row: int, target: float) -> None:
        """把某行进度重定向到 target（0 或 1），从当前值出发、可打断。"""
        row = int(row)
        target = 1.0 if target >= 0.5 else 0.0
        current = self._hp.get(row, 0.0)
        if abs(target - current) <= ALPHA_EPS:
            self._finish_row(row, target)
            return
        ms = motion.eased_ms("fast", RowHoverController._speed)
        self.last_duration = ms
        if ms <= 0:
            # reduce_motion / 极速档：0ms 瞬显（语义是瞬显，不是缩短）
            self._tweens.pop(row, None)
            self._finish_row(row, target)
            self._update_rows([row])
            return
        self._tweens[row] = [current, target, time.monotonic(), ms]
        if not self._timer.isActive():
            self._timer.start()

    def _finish_row(self, row: int, value: float) -> None:
        self._tweens.pop(row, None)
        if value <= ALPHA_EPS:
            self._hp.pop(row, None)
        else:
            self._hp[row] = min(1.0, float(value))

    def _tick(self) -> None:
        """单 QTimer 帧：推进所有活跃 tween，只 update 受影响行区域。"""
        if not self._tweens:
            self._maybe_stop_timer()
            return
        now = time.monotonic()
        changed, done = [], []
        for row, (start, target, t0, ms) in self._tweens.items():
            k = (now - t0) / (ms / 1000.0)
            if k >= 1.0:
                self._hp[row] = target
                done.append(row)
                changed.append(row)
            else:
                self._hp[row] = start + (target - start) * _out_cubic(k)
                changed.append(row)
        for row in done:
            if self._hp.get(row, 0.0) <= ALPHA_EPS:
                del self._hp[row]
            self._tweens.pop(row, None)
        self._maybe_stop_timer()
        if changed:
            self._update_rows(changed)

    def _maybe_stop_timer(self) -> None:
        if not self._tweens and self._timer.isActive():
            self._timer.stop()

    def _update_rows(self, rows) -> None:
        """性能红线：只重绘受影响行的可视矩形（Qt 会合并成一次重绘）。"""
        view = self._view
        if view is None or view.model() is None:
            return
        viewport = view.viewport()
        if viewport is None:
            return
        for row in rows:
            index = view.model().index(int(row), 0)
            if not index.isValid():
                continue
            rect = view.visualRect(index)
            if rect.isValid():
                viewport.update(rect)

    # ---------------- 清零 ----------------
    def clear_all(self, reason: str = "") -> bool:
        """清空全部活跃 hp 缓存（滚动/拖拽/模型重建共用）。

        ``reason`` 为 ``"scroll"`` / ``"drag"`` 时（规格 §7 U4 的强制
        清零路径）补**单次**全视口 update；``"model"``（refresh 重建/
        控件销毁时的行增删）不主动重绘 —— 视图随后自有重绘，销毁路径
        碰 viewport 反而会炸（离屏实测：QListWidget 析构期
        rowsAboutToBeRemoved → update 崩进程，故此处的 viewport 访问
        一并加了亡引用防护）。返回是否真的清掉了东西。
        """
        had = bool(self._hp or self._tweens)
        self._tweens.clear()
        self._hp.clear()
        self._hover_row = None
        self._maybe_stop_timer()
        if had and reason in ("scroll", "drag"):
            try:
                viewport = self._view.viewport() if self._view else None
                if viewport is not None:
                    viewport.update()      # 规格口径：单次 update
            except RuntimeError:
                pass                       # 宿主 view 已在析构，忽略
        return had


def hover_amount(controller: RowHoverController, option, index) -> float:
    """paint 侧统一取数入口：懒挂接 + 选中态优先 + 行进度。

    - 选中行恒返 0（状态优先级 selected > hover，与三个 delegate 既有
      分支序一致，hover 叠色只服务非选中行）；
    - option.widget 为 None（离屏直绘）时 attach 无操作，进度为 0
      —— rest 路径与旧版逐字节一致。
    """
    controller.attach(getattr(option, "widget", None))
    if option.state & QStyle.StateFlag.State_Selected:
        return 0.0
    return controller.hp_for_row(index.row())
