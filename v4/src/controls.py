# -*- coding: utf-8 -*-
"""
====================================================================
可复用控件  -  controls
====================================================================
给设置页用的轻量控件，统一「玻璃质感 + 悬停/按压反馈」：

  · Stepper   数字步进器：− ［可输入的值］单位 ＋

为什么不用 QSpinBox：
  · 原生上下箭头又小又难点，且和玻璃主题的圆角风格不搭；
  · 这里换成两个 30×30 的圆角按钮，悬浮高亮、按下回弹、到边界自动置灰；
  · 按住不放连续加减，且越按越快（400ms 起跳，先 80ms 一档，约 1.2s 后 45ms）；
  · 数值可直接点进去键入，回车或失焦时按上下限截断。

为什么不用 QSlider（滑条）：
  · 滑条最容易被滚轮误改——鼠标滚设置页时滑条一「吃掉」滚轮就静默跳值；
  · 步进器只在数值框有焦点时才响应滚轮，且 ± 按钮点击意图明确、可长按连发。

小数值设置（如动画速度 1.3x）：内部仍用整数（50~200），通过 divisor/decimals
换算显示（divisor=100、decimals=1 → 130 显示为「1.3」），对外 valueChanged
发出的仍是**内部整数**，调用方按 divisor 换算即可。

样式全部走 theme.py 的 QSS（objectName：stepBtn / stepValue / fieldLabel），
主题切换自动跟随，这里不写死任何颜色。
====================================================================
"""

import math

from PyQt6.QtCore import (
    QAbstractAnimation, QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt,
    QTimer, QCoreApplication, QVariantAnimation, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QDoubleValidator, QIntValidator,
    QKeySequence, QMouseEvent, QPen, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QCheckBox, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QStyle,
    QStyleOptionButton, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QPropertyAnimation

from src.constants import (RADIUS_CTL, RADIUS_CHIP, RADIUS_PANEL,
                           CHECK_ANIM_MS, CHECK_BOUNCE_SCALE)
from src.qcolor import qcolor as _qc
from src.theme import DEFAULT_THEME, get_colors
from src.toast import ToastCenter
from src import icon_render
from src import motion
from src import row_hover


# ====================================================================
# 丝滑化按钮基类（UI 丝滑化清单 S2，2026-10-02）
# ====================================================================
# Qt 的 QSS 引擎不支持 CSS ``transition``：``:hover`` / ``:pressed`` 的
# 背景色是一帧跳变。所以「丝滑」不能靠改 QSS，只能自绘插值 —— 本节
# 提供的 SmoothButton 在原生 QSS 之上叠加一层过渡色，QSS 侧只需把
# 命名按钮 ``:hover`` / ``:pressed`` 的**背景色**删掉（文字/边框/焦点环
# 仍归 QSS 管），两端点色一一对照记在 _SMOOTH_OVERLAYS 注释里。
# 改 theme.py 的 hover/pressed 端点或这里任何一个 spec，两边必须同步。
_PROGRESS_EPS = 0.005   # 低于该进度的叠加/位移直接省略（省一帧无意义重绘）
_PRESS_SHIFT = 1.0      # 按下下沉 px —— 绘制级位移，绝不碰 margin/padding
_PRESS_SCALE = 0.98     # 按下微缩

# objectName → (hover 端点 spec, press 端点 spec)；spec = (主题 token 名, 255 上限不透明度)
# 端点逐一对照 theme.py 里已删除的 QSS 背景（丝滑化清单 §2.1 的落地契约）：
#   · 不透明端点（$primary_hover / $primary / $danger …）→ (token, 255)
#   · 半透明 a08/a12/a18/a30 端点 → (primary, 255*比例)
#   · secondaryBtn 基底已改 $surface 实底（UI 重构 01），hover 叠 primary 18%
#     淡染 ≈ 网页 ghost 按钮；textBtn 文字钮 hover 用 primary 20% 更弱的淡染
#   · None = 该状态不换底色（维持现状，仅吃按下位移）
#   · None 键 = 未命名 / 未收录按钮的兜底，等价旧的全局 QPushButton:hover/:pressed
_SMOOTH_OVERLAYS = {
    "secondaryBtn":     (("primary", 18), None),
    "textBtn":          (("primary", 20), ("primary", 31)),
    # 2026-10 对比度修复（GlassMessageBox danger 按钮专项拍板）：hover 端点
    # 从 danger a26 淡染改为 $danger_hover 实底（红底白字才读得清，QSS 侧
    # `#dangerBtn:hover { color: white }` 同批同步）；press 保持同实底，
    # 反馈由绘制级下沉/微缩承担。
    "dangerBtn":        (("danger_hover", 255), ("danger_hover", 255)),
    # 小卡片碎片页页脚两个按钮（UI 重构 06）：随页脚一起从「行内彩色小胶囊」
    # 改成高仿真 .mini-foot .ib 的「裸露图标钮」—— 常态透明无边框，hover 只
    # 叠一层淡底。故端点从实底（danger/primary 255）换成 surface_2/3 实色。
    # 注：theme.py 里 #fragDelBtn:hover 的历史 `color: white` 未同步删除
    # （那是文字色；纯图标钮的图标色由 IconButton 的 QIcon::Active 位图负责，
    #  QSS color 不参与绘制），保留以免误触 test_smooth_buttons 的既有契约。
    "fragDelBtn":       (("danger", 26), ("danger", 46)),
    # 小卡片右上角关闭钮（UI 重构 06）：随页眉一起改成高仿真 .mini-head .x 的
    # 中性图标钮 —— 常态透明、hover 只叠一层浅面，红底实心已是历史。
    "cardCloseBtn":     (("surface_2", 255), ("surface_3", 255)),
    # 页脚新增的「编辑」钮，与复制钮同为中性 hover；未收录会掉进兜底
    # （primary_hover 实底）→ hover 变绿，与本页脚风格冲突。
    "fragEditBtn":      (("surface_2", 255), ("surface_3", 255)),
    "iconBtn":          (("primary", 31), ("primary", 46)),
    "iconBtn_danger":   (("danger", 26), ("danger", 46)),   # iconBtn[danger="true"] 属性变体
    "sideTabIconBtn":   (("primary", 31), ("primary", 46)),
    "settingsNavBtn":   (("primary", 20), ("primary", 31)),
    "stepBtn":          (("primary", 46), ("primary", 77)),
    "navSiteCard":      (("primary", 46), ("primary", 77)),
    # 2026-10-02 悬停配色优化：modeBtn 常态是 ghost（$primary_a12），hover
    # 端点从 primary_hover 实底改淡染 —— 否则 ghost→实底突跳，且未选中态
    # 主色文字叠 primary_hover 底几乎不可读；淡染端点叠在选中态实底上与
    # 底同色（视觉 no-op，选中钮不再跳色）。tableOpenBtn 同理保持 ghost 语系。
    "modeBtn":          (("primary", 46), ("primary", 77)),
    "taskAddBtn":       (("primary_hover", 255), ("primary_pressed", 255)),
    "nextBtn":          (("primary_hover", 255), ("primary_pressed", 255)),
    # 同上：碎片页页脚复制钮 → 中性 hover 淡底
    "fragCopyBtn":      (("surface_2", 255), ("surface_3", 255)),
    "tableOpenBtn":     (("primary", 46), ("primary", 77)),
    # S4：侧栏导航行。端点对照 navBtn:hover/:pressed 被删的 a08/a18；
    # 拖拽态（[dragging="true"] 的 a18 底）仍归 QSS（静态状态，无过渡需求）。
    "navBtn":           (("primary", 20), ("primary", 46)),
    # ---- 交互状态批（2026-10-08，清单 B1/C1）----
    # pluginSegBtn / pluginErrorToggle 此前是裸 QPushButton（按下零反馈），
    # 本批收编为 SmoothButton；pluginMoreBtn 本就是 IconButton，但缺登记
    # ——掉进 None 键兜底（primary_hover 实底）会把 QSS 的 surface_2 hover
    # 盖成绿色块，本批一并收口。收编口径：**:hover 背景变化仍归 QSS**
    # （hover 端点 = None，避免与既有 QSS hover 底色两套机制打架——本批
    # 只补「按下」这一缺失反馈，hover 视觉回归 QSS 语义，零回归风险）；
    # press 端点逐一对照各按钮的语义色系：
    #   · pluginSegBtn / pluginMoreBtn：中性面系（checked 态即 surface_3）
    #   · pluginErrorToggle：danger 系淡染（「有问题在这」语系）
    #   · navGroupHeader / helpTocItem：primary 淡染（对照 hover 的 a08）
    "pluginSegBtn":     (None, ("surface_3", 255)),
    "pluginMoreBtn":    (None, ("surface_3", 255)),
    "pluginErrorToggle": (None, ("danger", 46)),
    "navGroupHeader":   (None, ("primary", 31)),
    "helpTocItem":      (None, ("primary", 31)),
    # ---- 登记完整性护栏（U5，2026-10-07）----
    # 护栏（tests/test_smooth_overlay_registry.py）把 theme.py 四份 QSS 里
    # 出现的每一条 `QPushButton#name:hover` 与本表对账，缺登记即红灯 ——
    # 因此「有意走未命名兜底」的按钮也必须**显式**登记（值与兜底一致，
    # 行为零变化），让每一次兜底都是拍板过的，而不是漏配的。
    "primaryBtn":       (("primary_hover", 255), ("primary_pressed", 255)),
    # AI 助手「回到最新」浮钮（theme.py QSS 的 hover 端点是 $surface_2，
    # 站点现无实例化点 —— QSS 与本表先对齐，防未来创建时踩 G7）
    "backToLatestBtn":  (("surface_2", 255), None),
    None:               (("primary_hover", 255), ("primary_pressed", 255)),
}

# objectName → (checked 端点 spec)：选中态底色过渡（U3，2026-10-07）。
# 端点逐一对照 theme.py 里已删除的 QSS `:checked` 背景（契约同
# _SMOOTH_OVERLAYS：改 QSS 的 :checked 底色必须同步这里，反之亦然）。
# 未收录 / None = 选中底色仍归 QSS `:checked`（一帧瞬变，维持现状）。
_SMOOTH_CHECKED = {
    # 分段筛选钮（插件中心「全部/已启用/已停用」）：checked 端点 =
    # $surface_3 实底；hover 仍是 QSS 的 $surface_2（轻收编口径），
    # 选中+悬停时 overlay 盖在 QSS hover 底上正好是「淡出回 hover」。
    "pluginSegBtn": ("surface_3", 255),
    # 模式切换胶囊（设置页 AI 后端 云端/本地、插件页同款）：checked 端点 =
    # $primary 实底（对照 theme.py 已删除的 :checked 背景，主窗+卡片模板
    # 两处同步删）。基态 hover/press 端点见 _SMOOTH_OVERLAYS（46/77% 淡染）。
    "modeBtn": ("primary", 255),
    # 模式切换胶囊（设置页 AI 后端 云端/本地、插件页同款）：checked 端点 =
    # $primary 实底（对照 theme.py 已删除的 :checked 背景，主窗+卡片模板
    # 两处同步删）。基态 hover/press 端点见 _SMOOTH_OVERLAYS（46/77% 淡染）。
}

# overlay 圆角（对照 theme.py 各选择器的 border-radius）；未收录的走全局
# RADIUS_CTL。UI 重构 01 起取 constants.RADIUS_* 与 QSS 的 $r_* 同源 ——
# theme.py 圆角四档收敛后这里的像素值全部随之更新，两边不再可能漂移。
_OVERLAY_RADIUS = {
    "modeBtn": RADIUS_CTL, "cardCloseBtn": RADIUS_CHIP,
    "nextBtn": RADIUS_CTL, "iconBtn": RADIUS_PANEL,
    "sideTabIconBtn": RADIUS_PANEL, "settingsNavBtn": RADIUS_PANEL,
    "tableOpenBtn": RADIUS_CTL,
    "fragCopyBtn": RADIUS_CTL, "fragDelBtn": RADIUS_CTL,
    "fragEditBtn": RADIUS_CTL,
    "navBtn": RADIUS_PANEL, "secondaryBtn": RADIUS_CTL,
    "textBtn": RADIUS_CTL,
    # 交互状态批（2026-10-08，清单 C1）：圆角逐一对照 theme.py 各自的
    # border-radius（chip=4 / panel=8 / ctl=6），overlay 不露出直角。
    "pluginSegBtn": RADIUS_CHIP, "pluginMoreBtn": RADIUS_CHIP,
    "navGroupHeader": RADIUS_PANEL, "helpTocItem": RADIUS_CHIP,
}
_DEFAULT_RADIUS = RADIUS_CTL


class SmoothButton(QPushButton):
    """在原生 QSS 外观之上叠加 hover / press 过渡的按钮基类（清单 S2）。

    原理（清单 §2.1 的 overlay 插补）：先照常画原生 QSS 外观，再用两个
    0→1 的动画属性叠加一层过渡色 —— ``hp``（hover 进度）/ ``pp``（press
    进度）。时长与曲线统一走 motion token（``fast`` / OutCubic），
    ``reduce_motion`` 开启时直接落终态（motion.duration 返回 0）。

    按下反馈（B2）走**绘制级** -1px 下沉 + 0.98 微缩，由 ``pp`` 驱动 ——
    margin/padding 方案已在项目里被证明有害（按下态 polish 会把 sizeHint
    算大并永久缓存，拖拽后行高 +1px，见 theme.py 注释与
    test_nav_drag_invariants.py）。

    QSS 侧约定（与 theme.py 联动，单改一边即回归）：
      · 命名按钮 ``:hover`` / ``:pressed`` 的背景色已从 QSS 删除，overlay
        端点色逐一对照被删旧值（见 _SMOOTH_OVERLAYS 注释）；
      · 文字色 / 边框 / 焦点环仍归 QSS（文档口径：hover 即时起变化，
        只补「到位过程」）；
      · primaryBtn 渐变 hover 属 B5（后置）：QSS 渐变跳变保留，
        overlay 为 (None, None)，仅享受按下位移。

    动画可打断重定向（清单 §6.4）：复用同一个 QPropertyAnimation，
    每次 retarget 都从当前值出发 ``stop() → start()``，连续快速进出
    不会排队。换主题不用通知 —— 端点色在 paint 时按当前主题解析。
    按设置页 ``anim_speed`` 档位缩放：main_window 启动与变更广播时调
    :meth:`set_speed`（类级属性，全进程按钮共享）。
    """

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（main_window 启动 / 设置页变更时调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hp = 0.0
        self._pp = 0.0
        self._dp = 0.0            # 禁用淡出进度（U2）：1 = 完全禁用外观
        self._cp = 0.0            # 选中底色进度（U3）：1 = 完全选中端点色
        self._hover_anim = None
        self._press_anim = None
        self._dp_anim = None
        self._checked_anim = None
        self._child_free = None   # None = 未测定（子控件可能晚于构造加入）
        # ---- U1（2026-10-07）：press/release 改挂信号 ----
        # QAbstractButton.pressed / released 在鼠标左键、键盘 Space/Enter、
        # 触屏（Qt 合成鼠标事件）三条路径上统一发射 —— press 动画从
        # mousePressEvent 挪到这里，键盘激活第一次获得按下反馈。
        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)
        self.toggled.connect(self._on_checked_glide)

    # ---- 动画属性（QPropertyAnimation 写入端）----
    def _get_hp(self):
        return self._hp

    def _set_hp(self, value):
        self._hp = max(0.0, min(1.0, float(value)))
        self.update()

    def _get_pp(self):
        return self._pp

    def _set_pp(self, value):
        self._pp = max(0.0, min(1.0, float(value)))
        self.update()

    def _get_dp(self):
        return self._dp

    def _set_dp(self, value):
        self._dp = max(0.0, min(1.0, float(value)))
        self.update()

    def _get_cp(self):
        return self._cp

    def _set_cp(self, value):
        self._cp = max(0.0, min(1.0, float(value)))
        self.update()

    hp = pyqtProperty(float, _get_hp, _set_hp)
    pp = pyqtProperty(float, _get_pp, _set_pp)
    dp = pyqtProperty(float, _get_dp, _set_dp)
    cp = pyqtProperty(float, _get_cp, _set_cp)

    # ---------------- 状态驱动 ----------------
    def _glide(self, prop, attr, anim_attr, target, kind="fast"):
        """把 attr 插值到 target（0/1）。可打断：从当前值重定向，不排队。

        ``kind`` 取 motion.MOTION 的时长 token 名：进入类反馈用
        ``fast``（120ms），松手回弹/禁用收回用 ``press_out``（150ms，
        比进入略长 —— 回弹更从容）。
        """
        current = getattr(self, attr)
        if abs(target - current) <= _PROGRESS_EPS:
            return
        ms = motion.eased_ms(kind, self._speed)
        if ms <= 0:
            # reduce_motion 总闸 / 档位归零：直接落终态（语义是瞬显，不是缩短）
            setattr(self, attr, float(target))
            self.update()
            return
        anim = getattr(self, anim_attr)
        if anim is None:
            anim = QPropertyAnimation(self, prop, self)
            anim.setEasingCurve(getattr(QEasingCurve.Type, motion.EASE["out"]))
            setattr(self, anim_attr, anim)
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    def enterEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 0.0)
        super().leaveEvent(event)

    # ---- U1：press / release 信号驱动（鼠标/键盘/触屏统一）----
    def _on_pressed(self):
        """按下反馈入口：QAbstractButton.pressed 信号槽。

        三条触发路径都在这里汇合 —— 鼠标左键按下、键盘 Space/Enter
        （keyPressEvent → setDown(true) → pressed）、触屏/笔（Qt 合成
        鼠标事件）。此前挂在 mousePressEvent，键盘激活零反馈（G1/G2）。
        """
        self._glide(b"pp", "_pp", "_press_anim", 1.0)

    def _on_released(self):
        """松手回弹入口：QAbstractButton.released 信号槽。

        时长用 ``press_out``（150ms）而不是进入的 ``fast`` —— 回弹比
        按下更从容（规格 §6 token 表）。吞掉 release 的手势路径（导航
        拖拽落定只 setDown(False) 不发 released）不走这里，仍由
        :meth:`cancel_press_feedback` 显式收回。"""
        if self._pp > _PROGRESS_EPS:
            self._glide(b"pp", "_pp", "_press_anim", 0.0, kind="press_out")

    def cancel_press_feedback(self):
        """显式收回按下反馈（pp 回 0）。

        给"吞掉 release"的手势路径用 —— _NavButton 拖拽落定时只 setDown(False)
        而不调用 super().mouseReleaseEvent，QPropertyAnimation 不会被通知，
        pp 会卡在 1（按钮永久下沉 + 叠色）。"""
        if self._pp > _PROGRESS_EPS:
            self._glide(b"pp", "_pp", "_press_anim", 0.0, kind="press_out")

    # ---- U2：禁用淡出 ----
    def changeEvent(self, event):
        """EnabledChange 时对禁用外观做 120ms 插值（G3「灰了一跳」）。

        三条语义：
        - 正常路径：``dp`` 在 0↔1 间走 ``fast`` 插值；同时若正在禁用，
          press/hover 叠色一并按 ``press_out`` 收回（状态优先级
          **disabled > pressed > hover**，禁用不残留交互反馈）；
        - 批量刷路径：调用方用 blockSignals 包裹 setEnabled（设置页统一
          刷新 / Stepper 批量同步）→ 直接落终态，不播动画（与
          reduce_motion 同语义：瞬显，不是缩短）；
        - reduce_motion 开启：_glide 内部 ms=0 自动瞬显。
        """
        super().changeEvent(event)
        if event.type() != QEvent.Type.EnabledChange:
            return
        if self.signalsBlocked():
            self.snap_disabled_feedback()
            return
        self._glide(b"dp", "_dp", "_dp_anim",
                    0.0 if self.isEnabled() else 1.0)
        if not self.isEnabled() and (self._pp > _PROGRESS_EPS
                                     or self._hp > _PROGRESS_EPS):
            self._glide(b"pp", "_pp", "_press_anim", 0.0, kind="press_out")
            self._glide(b"hp", "_hp", "_hover_anim", 0.0)

    def snap_disabled_feedback(self):
        """禁用反馈直接落终态（批量刷语义，同 reduce_motion：瞬显）。

        按 isEnabled() 立即收敛 ``dp``，press/hover 叠色与进行中的
        动画一并杀掉 —— 禁用覆盖一切交互态。Stepper 批量同步
        （宿主 blockSignals 后 setValue → _sync_buttons）显式调用。"""
        for anim in (self._hover_anim, self._press_anim, self._dp_anim):
            if anim is not None:
                anim.stop()
        self._hp = 0.0
        self._pp = 0.0
        self._dp = 0.0 if self.isEnabled() else 1.0
        self.update()

    # ---- U3：选中底色过渡 ----
    def _on_checked_glide(self, _checked=False):
        """toggled 信号槽：登记了 _SMOOTH_CHECKED 端点的按钮做 cp 插值。"""
        if self.objectName() not in _SMOOTH_CHECKED:
            return
        self._glide(b"cp", "_cp", "_checked_anim",
                    1.0 if self.isChecked() else 0.0)

    def _checked_progress(self):
        """cp 的有效值：动画进行中取 _cp，静止时直接按 isChecked() 落值。

        后一半是 blockSignals 批量 setChecked 的兜底 —— toggled 被吞、
        cp 属性停在旧值，但选中态以 isChecked() 为准直接渲染（与
        ToggleSwitch「无动画时按 isChecked 渲染」同一口径）。"""
        anim = self._checked_anim
        if anim is not None and \
                anim.state() == QAbstractAnimation.State.Running:
            return self._cp
        return 1.0 if self.isChecked() else 0.0

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        if _PROGRESS_EPS < self._dp < 1.0 - _PROGRESS_EPS:
            # U2 禁用淡出中间帧：两份 QSS 外观交叉淡染（见 _paint_disable_fade）
            self._paint_disable_fade(event)
            return
        hover_spec, press_spec = self._overlay_specs()
        checked_spec = _SMOOTH_CHECKED.get(self.objectName())
        spec, progress = None, 0.0
        if self._pp > _PROGRESS_EPS:
            # 状态优先级：pressed 最上（规格 §3）
            spec, progress = press_spec, self._pp
        elif checked_spec is not None:
            # U3：选中底色叠加层，介于 press 与 hover 之间
            cp = self._checked_progress()
            if cp > _PROGRESS_EPS:
                spec, progress = checked_spec, cp
        elif self._hp > _PROGRESS_EPS:
            spec, progress = hover_spec, self._hp
        if spec is None:
            if self._pp > _PROGRESS_EPS:
                # 有按下位移但该按钮 press 不换底色（secondaryBtn/primaryBtn）
                self._paint_native_transformed()
            else:
                super().paintEvent(event)
            return
        painter = None
        if self._pp > _PROGRESS_EPS:
            painter = self._paint_native_transformed()
        else:
            super().paintEvent(event)
            painter = QPainter(self)
        self._paint_overlay(painter, spec, progress)
        painter.end()

    def _paint_disable_fade(self, event):
        """U2 禁用淡出中间帧：可用外观（1-dp）与禁用外观（dp）交叉淡染。

        QSS 的 ``:disabled`` 是一帧跳变且不可插值，中间帧把两份 QSS 外观
        （CE_PushButton 全套：背景/文字/图标，State_Enabled 旗标分别置位
        —— 图标随之取 Normal / Disabled 两张位图）按透明度叠画。dp=0/1
        两端走原生路径逐像素一致，交叉淡染只存在于 120ms 过渡内。按下
        位移/叠色不参与：disabled 覆盖一切交互态，pp/hp 已在 changeEvent
        一并收回。
        """
        painter = QPainter(self)
        dp = self._dp
        painter.save()
        painter.setOpacity(1.0 - dp)
        self._draw_qss_layer(painter, enabled=True)
        painter.restore()
        painter.save()
        painter.setOpacity(dp)
        self._draw_qss_layer(painter, enabled=False)
        painter.restore()
        painter.end()

    def _draw_qss_layer(self, painter, enabled: bool):
        """画一份 QSS 按钮外观（CE_PushButton），State_Enabled 按参置位。

        与 _paint_native_transformed 的区别：painter 由调用方持有并已设好
        透明度（交叉淡染要叠两层），且不画叠色、不做位移。"""
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        if enabled:
            opt.state |= QStyle.StateFlag.State_Enabled
        else:
            opt.state &= ~QStyle.StateFlag.State_Enabled
        self.style().drawControl(QStyle.ControlElement.CE_PushButton,
                                 opt, painter, self)

    def _paint_native_transformed(self):
        """按 ``pp`` 进度下沉 + 微缩后画原生 QSS 外观，返回未 end() 的 painter。

        只对**无子控件**的纯文本/图标按钮生效 —— navSiteCard 这类内嵌
        QLabel 的复合按钮，子控件不走本 paintEvent，跟着缩放会跟背景错位。
        """
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        painter = QPainter(self)
        if self._is_child_free():
            factor = 1.0 - (1.0 - _PRESS_SCALE) * self._pp
            cx, cy = self.width() / 2.0, self.height() / 2.0
            painter.translate(cx, cy + _PRESS_SHIFT * self._pp)
            painter.scale(factor, factor)
            painter.translate(-cx, -cy)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, opt,
                                 painter, self)
        return painter

    def _paint_overlay(self, painter, spec, progress):
        """叠一层过渡色：端点 = spec，不透明度按 progress 插值。"""
        token, max_alpha = spec
        color = self._overlay_color(token)
        color.setAlpha(int(max_alpha * progress))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = _OVERLAY_RADIUS.get(self.objectName(), _DEFAULT_RADIUS)
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        if max_alpha >= 250 and progress > _PROGRESS_EPS:
            # ★实底端点（255）的 overlay 是不透明色块，会把 super().paintEvent
            #   画好的文字与图标一并盖掉 —— 表现为「hover 时字与按钮同色、
            #   内容消失」（2026-10-03 用户报；nextBtn / taskAddBtn / 无名
            #   IconButton 兜底 / cardCloseBtn / fragCopyBtn 等全部实底组
            #   均受影响，淡染端点 <250 不走此分支）。盖完底色后按当前
            #   QSS 状态（:hover 的文字色已由 QSS 反映进 palette）补画一层
            #   label（图标+文字），透明度跟随 progress 与底色同步淡入。
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            painter.setOpacity(max(0.0, min(1.0, float(progress))))
            self.style().drawControl(
                QStyle.ControlElement.CE_PushButtonLabel, opt, painter, self)
            painter.setOpacity(1.0)

    # ---------------- 取色与映射 ----------------
    def _overlay_specs(self):
        name = self.objectName()
        if name == "iconBtn" and self.property("danger") == "true":
            return _SMOOTH_OVERLAYS["iconBtn_danger"]
        return _SMOOTH_OVERLAYS.get(name, _SMOOTH_OVERLAYS[None])

    def _overlay_color(self, token):
        colors = get_colors(self._detect_theme() or DEFAULT_THEME)
        return QColor(str(colors.get(token, colors["primary"])))

    def _is_child_free(self):
        if self._child_free is None:
            self._child_free = not self.findChildren(QWidget)
        return self._child_free

    def _detect_theme(self):
        """依次找：宿主的 ``_theme`` → 父控件链上最近窗口的 ``_theme``。

        后一半给"运行期动态创建、又没接宿主管线"的按钮兜底：创建时沿
        parentWidget 向上爬，爬到主窗/卡片窗/便签窗的 ``_theme`` 就取对
        配色。只认 "light"/"dark"，中途对象挂了同名属性也不误判。
        """
        host = getattr(self, "_host", None)
        if host is not None:
            t = getattr(host, "current_theme", None)
            if t in ("light", "dark"):
                return t
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None


class SmoothInput(QLineEdit):
    """输入框 hover/focus 边框插值 + 焦点外圈（交互视觉清单 V1，2026-10-07）。

    现状：QSS 引擎没有 transition，``QLineEdit:hover`` → ``$primary_a30``、
    ``:focus`` → ``$focus_ring`` 都是 1px 边框一帧跳变，且焦点只有一根
    1px 边框、锚定感弱。本类在原生 QSS 外观之上叠加**绘制级**边框层：

    - hover：边框 ``$panel_edge → $primary_a30`` 120ms OutCubic 插值；
    - focus：边框插到 ``$focus_ring``（100ms，motion ``focus_ring`` 档）
      + 内侧 3px 主色 12% 光圈淡入（画在 border 内侧，**不扩占位** ——
      与 ToggleSwitch 内缩焦点环同一口径；稿的 box-shadow 向外扩在
      Qt 里必然侵入布局，明确不做）；
    - 失焦/离开反向过渡，可打断重定向（同 SmoothButton 的
      stop → start 从当前值出发）；reduce_motion / 档位归零瞬显。

    QSS 侧约定（theme.py）：`QLineEdit[smoothFrame="true"]` 把本类实例的
    基态边框置 1px transparent 纯占位（border-box 几何与 1px 实边框一致，
    sizeHint 零变化），边框颜色全权由本类每帧绘制 —— rest 画
    ``$panel_edge``、hover 端点 ``$primary_a30``、focus 端点
    ``$focus_ring``，与被删旧 QSS 行为同源同位；原生 QLineEdit
    （HotkeyCaptureEdit / 便签输入框等未替换站点）的 ``:hover`` /
    ``:focus`` 边框规则原样保留、不受影响。背景/文字/内边距/selection
    色仍归 QSS；换主题不需要通知（端点色在 paint 时按当前主题解析，
    父链探测与 SmoothButton 同款）。禁用态照常画常态边框（QSS
    ``:disabled`` 只管文字色）。

    接入面（V1）：全站走通用边框样式的 QLineEdit（搜索框 / 表单框 /
    taskInput 等）替换为本类；例外三处不换——Stepper 的 stepValue
    （无边框下划线式样）、HotkeyCaptureEdit（自绘捕捉控件）、便签窗
    输入框（自绘纸面表面，不在通用 QSS 管辖内）。
    """

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（controls.set_ui_speed 统一调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hp = 0.0    # hover 进度：1 = 完全悬停端点（$primary_a30）
        self._fp = 0.0    # focus 进度：1 = 完全焦点态（$focus_ring + 光圈）
        self._hover_anim = None
        self._focus_anim = None
        self._host = None
        # QSS 契约（theme.py `QLineEdit[smoothFrame="true"]`）：基态边框
        # 置 1px transparent 纯占位（border-box 几何不变），边框颜色全权
        # 由本类绘制层每帧画出 —— rest 画 $panel_edge，端点与旧 QSS 同源。
        self.setProperty("smoothFrame", "true")

    def set_host(self, host):
        """主题探测宿主（与 IconButton._host 同语义；一般无需调用）。"""
        self._host = host

    # ---------------- 动画属性 ----------------
    def _get_hp(self):
        return self._hp

    def _set_hp(self, value):
        self._hp = max(0.0, min(1.0, float(value)))
        self.update()

    def _get_fp(self):
        return self._fp

    def _set_fp(self, value):
        self._fp = max(0.0, min(1.0, float(value)))
        self.update()

    hp = pyqtProperty(float, _get_hp, _set_hp)
    fp = pyqtProperty(float, _get_fp, _set_fp)

    def _glide(self, prop, attr, anim_attr, target, kind="fast"):
        """进度插值到 target（0/1），可打断重定向；reduce_motion 瞬显。"""
        current = getattr(self, attr)
        if abs(target - current) <= _PROGRESS_EPS:
            return
        ms = motion.eased_ms(kind, self._speed)
        if ms <= 0:
            setattr(self, attr, float(target))
            self.update()
            return
        anim = getattr(self, anim_attr)
        if anim is None:
            anim = QPropertyAnimation(self, prop, self)
            anim.setEasingCurve(getattr(QEasingCurve.Type, motion.EASE["out"]))
            setattr(self, anim_attr, anim)
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    # ---------------- 状态驱动 ----------------
    def enterEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._glide(b"fp", "_fp", "_focus_anim", 1.0, kind="focus_ring")
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._glide(b"fp", "_fp", "_focus_anim", 0.0, kind="focus_ring")
        super().focusOutEvent(event)

    # ---------------- 绘制 ----------------
    def _detect_theme(self):
        """SmoothButton 同款父链探测（见其方法注释，此处不重复展开）。"""
        host = self._host
        if host is not None:
            t = getattr(host, "current_theme", None)
            if t in ("light", "dark"):
                return t
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        super().paintEvent(event)
        # 边框每帧整圈重画（QSS 基态对 smoothFrame 实例是 1px 透明占位，
        # 这里是边框颜色的唯一来源）：rest 画 $panel_edge（与旧 QSS 基态
        # 同色同位），hover/focus 按进度插值；禁用态画常态边框（QSS
        # :disabled 只管文字色，旧边框在禁用下本来就不变）。
        hp, fp = self._hp, self._fp
        colors = get_colors(self._detect_theme() or DEFAULT_THEME)
        rest = _qc(colors.get("panel_edge", "#D3D1C7"))
        hover = _qc(colors.get("primary_a30", "#0F6E56"))
        focus = _qc(colors.get("focus_ring", "#0C5A47"))
        # 边框：rest →(hp) hover →(fp) focus_ring 两段串联插值。
        # ★alpha 与 RGB 一起插（panel_edge/primary_a30 都是半透明端点，
        #   丢弃 alpha 会把中间帧/端点画成不透明实色，偏离 QSS 端点）。
        def _lerp(a: QColor, b: QColor, t: float) -> QColor:
            return QColor(
                int(round(a.red() + (b.red() - a.red()) * t)),
                int(round(a.green() + (b.green() - a.green()) * t)),
                int(round(a.blue() + (b.blue() - a.blue()) * t)),
                int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
            )
        border = _lerp(_lerp(rest, hover, hp), focus, fp)
        w, h = float(self.width()), float(self.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = RADIUS_CTL
        if self.isEnabled() and fp > _PROGRESS_EPS:
            # 焦点光圈：主色 12% × fp，3px 环画在 border 内侧（1~4px
            # 带），不与正文文字（padding 10px 起）重叠，不扩占位。
            ring = _qc(colors.get("primary", "#0F6E56"))
            ring.setAlpha(int(round(255 * 0.12 * fp)))
            painter.setPen(QPen(ring, 3.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = 1.0 + 1.5   # border 1px + 环宽半幅
            painter.drawRoundedRect(
                QRectF(inset, inset, w - 2 * inset, h - 2 * inset),
                radius, radius)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)
        painter.end()


class SmoothCheckBox(QCheckBox):
    """自绘勾选框（交互视觉清单 V9，2026-10-07）：与 V3 任务勾选同一绘制语言。

    现状：QSS ``::indicator`` 的 hover/checked 是一帧瞬变，且与任务委托的
    「pop + 对勾描画」完成语言不同源。本类接管指示器绘制：

    - hover：边框 ``$primary_a30 → $primary`` 120ms 插值（QSS 端点同源）；
    - 选中：底色 ``$panel_fill → $primary`` 120ms + 指示器 pop 回弹
      （CHECK_BOUNCE_SCALE，sin 半波，与 task_delegate 同参数）+ 对勾
      按四段式时间轴错峰描画（起 80ms / 150ms 画完，同 V3 常量）；
    - 取消勾选全程可逆（时间轴倒放）；blockSignals 批量 setChecked 不播
      动画直接按 isChecked() 渲染（ToggleSwitch 同口径）；
    - 焦点：``::indicator:focus`` 端点 $focus_ring，100ms 插值（画在
      自绘边框上）；reduce_motion / 档位归零瞬显。

    QSS 契约（theme.py，规则置于 ``::indicator:focus`` 之后按序覆盖）：
    ``QCheckBox[smoothCheck="true"]::indicator { border: none;
    background: transparent; }`` —— 只对本类实例关闭原生指示器外观，
    指示器矩形（16px / r_chip 圆角）与文字色、spacing 仍由既有 QSS 提供。
    """

    _speed = 1.0

    # 与 task_delegate 的 V3 时间轴同源（V9 复用同一「勾选完成」语言；
    # 改轴必须两处同步 —— task_delegate 侧有轴长 = CHECK_ANIM_MS 的对齐断言）
    _POP_END = 220
    _DRAW_START = 80
    _DRAW_END = 230

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（controls.set_ui_speed 统一调用）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hp = 0.0     # hover 进度
        self._fp = 0.0     # focus 进度
        self._cp = 0.0     # 选中底色进度
        self._tp = 0.0     # pop/描画时间轴进度（0..1 ↔ CHECK_ANIM_MS）
        self._hover_anim = None
        self._focus_anim = None
        self._cp_anim = None
        self._tl_anim = None
        # QSS 契约：关闭本类实例的原生指示器外观（见类 docstring）
        self.setProperty("smoothCheck", "true")
        self.toggled.connect(self._on_toggled)

    # ---------------- 动画属性 ----------------
    @staticmethod
    def _mk_prop(name):
        attr = "_" + name

        def _get(self):
            return getattr(self, attr)

        def _set(self, value):
            setattr(self, attr, max(0.0, min(1.0, float(value))))
            self.update()

        return pyqtProperty(float, _get, _set)

    hp = _mk_prop("hp")
    fp = _mk_prop("fp")
    cp = _mk_prop("cp")
    tp = _mk_prop("tp")

    def _glide(self, prop, attr, anim_attr, target, kind="fast"):
        """进度插值到 target（0/1），可打断重定向；reduce_motion 瞬显。"""
        current = getattr(self, attr)
        if abs(target - current) <= _PROGRESS_EPS:
            return
        ms = motion.eased_ms(kind, self._speed)
        if ms <= 0:
            setattr(self, attr, float(target))
            self.update()
            return
        anim = getattr(self, anim_attr)
        if anim is None:
            anim = QPropertyAnimation(self, prop, self)
            anim.setEasingCurve(getattr(QEasingCurve.Type, motion.EASE["out"]))
            setattr(self, anim_attr, anim)
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    # ---------------- 状态驱动 ----------------
    def enterEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glide(b"hp", "_hp", "_hover_anim", 0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._glide(b"fp", "_fp", "_focus_anim", 1.0, kind="focus_ring")
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._glide(b"fp", "_fp", "_focus_anim", 0.0, kind="focus_ring")
        super().focusOutEvent(event)

    def _on_toggled(self, _checked=False):
        """toggled 槽：底色（cp）与时间轴（tp）随选中态正放/倒放。

        2026-10-07 实机走查修复：V9 初版只驱动 tp，_cp 是死属性——选中后
        底色永远停在 panel_fill（白），对勾用 on_primary 白笔画在白底上
        完全不可见（软件导航「自动回到主页面」勾选框实锤）。补 cp 驱动。

        blockSignals 批量路径 toggled 被吞 → _timeline_progress /
        _checked_progress 兜底按 isChecked() 直接渲染，动画不播
        （语义同 reduce_motion）。"""
        self._glide(b"cp", "_cp", "_cp_anim",
                    1.0 if self.isChecked() else 0.0)
        self._glide(b"tp", "_tp", "_tl_anim",
                    1.0 if self.isChecked() else 0.0,
                    kind="press_out")

    def _checked_progress(self):
        """底色有效值：动画进行中取 _cp，静止按 isChecked() 落值。"""
        anim = self._cp_anim
        if anim is not None and \
                anim.state() == QAbstractAnimation.State.Running:
            return self._cp
        return 1.0 if self.isChecked() else 0.0

    def _timeline_progress(self):
        """时间轴有效值：动画进行中取 _tp，静止按 isChecked() 落值。"""
        anim = self._tl_anim
        if anim is not None and \
                anim.state() == QAbstractAnimation.State.Running:
            return self._tp
        return 1.0 if self.isChecked() else 0.0

    # ---------------- 绘制 ----------------
    def _detect_theme(self):
        """SmoothInput 同款父链探测（见其方法注释）。"""
        w = self.parentWidget()
        while w is not None:
            t = getattr(w, "_theme", None)
            if t in ("light", "dark"):
                return t
            w = w.parentWidget()
        return None

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        super().paintEvent(event)   # 文字 / spacing / QSS 色照常
        from PyQt6.QtWidgets import QStyleOptionButton, QStyle
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        ind = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, opt, self)
        colors = get_colors(self._detect_theme() or DEFAULT_THEME)
        rest_bg = _qc(colors.get("panel_fill", "#FFFFFF"))
        on_bg = _qc(colors.get("primary", "#0F6E56"))
        rest_border = _qc(colors.get("primary_a30", "#0F6E56"))
        hover_border = _qc(colors.get("primary", "#0F6E56"))
        focus_border = _qc(colors.get("focus_ring", "#0C5A47"))
        symbol = _qc(colors.get("on_primary", "#FFFFFF"))

        def _lerp(a: QColor, b: QColor, t: float) -> QColor:
            return QColor(
                int(round(a.red() + (b.red() - a.red()) * t)),
                int(round(a.green() + (b.green() - a.green()) * t)),
                int(round(a.blue() + (b.blue() - a.blue()) * t)),
                int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
            )

        tp = self._timeline_progress()
        cp_eff = self._checked_progress()
        t_ms = tp * float(CHECK_ANIM_MS)
        pop_p = max(0.0, min(1.0, t_ms / float(self._POP_END)))
        draw_p = max(0.0, min(1.0,
                              (t_ms - self._DRAW_START)
                              / float(self._DRAW_END - self._DRAW_START)))
        scale = 1.0 + (CHECK_BOUNCE_SCALE - 1.0) * math.sin(math.pi * pop_p)

        side = min(ind.width(), ind.height()) * scale
        box = QRectF(
            ind.center().x() - side / 2.0, ind.center().y() - side / 2.0,
            side, side)
        radius = RADIUS_CHIP

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 底色：panel_fill → primary（cp 段）
        bg = _lerp(rest_bg, on_bg, cp_eff)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(box, radius, radius)
        # 边框：primary_a30 →(hp) primary →(fp) focus_ring；选中态描边随
        # cp 一并收到 primary（与 QSS :checked 端点一致）
        border = _lerp(_lerp(rest_border, hover_border, self._hp),
                       focus_border, self._fp)
        border = _lerp(border, on_bg, max(cp_eff, pop_p))
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, radius, radius)
        # 对勾描画（V3 同源两段折线）
        if draw_p > 0.01:
            x, y, w, h = box.x(), box.y(), box.width(), box.height()
            p0 = QPointF(x + w * 0.26, y + h * 0.52)
            p1 = QPointF(x + w * 0.44, y + h * 0.70)
            p2 = QPointF(x + w * 0.76, y + h * 0.32)
            l1 = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
            l2 = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
            total = l1 + l2
            drawn = draw_p * total
            pen = QPen(symbol, max(1.6, side * 0.13))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if drawn <= l1:
                k = drawn / l1 if l1 > 0 else 0.0
                painter.drawLine(p0, QPointF(p0.x() + (p1.x() - p0.x()) * k,
                                             p0.y() + (p1.y() - p0.y()) * k))
            else:
                painter.drawLine(p0, p1)
                k = min(1.0, (drawn - l1) / l2) if l2 > 0 else 1.0
                painter.drawLine(p1, QPointF(p1.x() + (p2.x() - p1.x()) * k,
                                             p1.y() + (p2.y() - p1.y()) * k))
        painter.end()


class _StepperSlideOverlay(QWidget):
    """Stepper 数值滑动的瞬态覆盖层（V10，2026-10-07）。

    盖在数值框上方，把「旧值滑出 + 新值滑入」两个文本画在同一帧时间轴
    上：+ → 内容上移（旧值向上滑出淡出、新值自下方滑入淡入）；− →
    反向。动画期间数值框自身文本为空（父类 setValue 已 setText("")），
    收尾恢复 —— 数据/信号在 setValue 里已同步落位，这里纯表现层。
    WA_TransparentForMouseEvents：滑动的 120ms 里点击/聚焦照常穿透。
    """

    def __init__(self, edit, old_text, new_text, up: bool, ms: int,
                 on_finished):
        super().__init__(edit)
        self._edit = edit
        self._old_text = old_text
        self._new_text = new_text
        self._sign = 1.0 if up else -1.0     # +1 内容上移 / -1 下移
        self._on_finished = on_finished
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                          True)
        self.setGeometry(edit.rect())
        self.show()
        self.raise_()
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(max(1, int(ms)))
        self._anim.setEasingCurve(
            getattr(QEasingCurve.Type, motion.EASE["out"]))
        # 绑定方法连接（拿接收者上下文）：宿主析构时 Qt 自动断开。
        # 禁 lambda —— 无接收者上下文的连接在析构竞态下会在已析构
        # 对象上回调（本文件 PageTitle docstring 同款铁律，段错误经典成因）。
        self._anim.valueChanged.connect(self.update)
        self._anim.finished.connect(self._done)

    def start(self):
        self._anim.start()

    def _done(self):
        self._on_finished()
        self.hide()
        self.deleteLater()

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        p = float(self._anim.currentValue() or 0.0)
        if p <= 0.0:
            return
        colors = get_colors(_detect_widget_theme(self) or DEFAULT_THEME)
        color = _qc(colors.get("text", "#2C2C2A"))
        painter = QPainter(self)
        font = QFont(self._edit.font())
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = float(self.height())
        shift = self._sign * p * h
        # 旧值：向移出方向滑出 + 淡出
        painter.setOpacity(max(0.0, 1.0 - p))
        painter.setPen(color)
        painter.drawText(
            QRectF(0, -shift, self.width(), h),
            int(Qt.AlignmentFlag.AlignCenter), self._old_text)
        # 新值：自移入方向滑入 + 淡入
        painter.setOpacity(p)
        painter.drawText(
            QRectF(0, self._sign * h - shift, self.width(), h),
            int(Qt.AlignmentFlag.AlignCenter), self._new_text)
        painter.setOpacity(1.0)
        painter.end()


def _detect_widget_theme(widget):
    """SmoothButton._detect_theme 的模块级版本（父链爬 _theme）。"""
    w = widget.parentWidget()
    while w is not None:
        t = getattr(w, "_theme", None)
        if t in ("light", "dark"):
            return t
        w = w.parentWidget()
    return None


class Stepper(QWidget):
    """数字步进器（替代 QSpinBox / QSlider 的 ± 按钮组）"""

    valueChanged = pyqtSignal(int)

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（controls.set_ui_speed 统一调用，V10）。"""
        cls._speed = motion.sanitize_speed(speed)

    BTN_SIZE = 30
    EDIT_WIDTH = 58
    SUFFIX_WIDTH = 26
    HOLD_DELAY_MS = 400      # 按住多久开始连发
    HOLD_FAST_MS = 80        # 连发初速
    HOLD_FASTER_MS = 45      # 加速后的速度
    HOLD_FAST_TICKS = 12     # 连发多少次后加速

    def __init__(self, minimum: int, maximum: int, value: int,
                 suffix: str = "", step: int = 1, parent=None,
                 divisor: int = 1, decimals: int = 0):
        super().__init__(parent)
        self._min = int(minimum)
        self._max = int(maximum)
        self._step = max(1, int(step))
        self._divisor = max(1, int(divisor))    # 内部值 = 显示值 × divisor
        self._decimals = max(0, int(decimals))  # 显示几位小数（0 = 纯整数）
        self._value = self._clamp(value)
        self._hold_dir = 0
        self._hold_tick = 0
        self._suppress_click = False
        self._slide_overlay = None   # V10：进行中的数值滑动覆盖层（至多一个）

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        # 字形：P1 起改自绘 minus / plus 图标（icons.py）。原先 U+2212/ASCII+
        # 是在系统字体里挑"最平衡的一对"（全角 －/＋ 在雅黑下不是过淡就是过粗），
        # 自绘后彻底摆脱字体差异 —— 缺字形环境与正常环境同一张脸，
        # QSS #stepBtn 的 font-size/font-weight 自然失效（无害保留）。
        self._btn_minus = self._make_btn("minus", "减小（可长按连续调整）")
        self._edit = QLineEdit(self._fmt(self._value))
        self._edit.setObjectName("stepValue")
        self._edit.setFixedSize(self.EDIT_WIDTH, self.BTN_SIZE)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setValidator(self._make_validator())
        self._edit.setToolTip("可直接输入数值，回车确认（范围 %s ~ %s）"
                              % (self._fmt(self._min), self._fmt(self._max)))
        self._edit.editingFinished.connect(self._commit_edit)
        self._edit.returnPressed.connect(self._commit_edit)
        self._btn_plus = self._make_btn("plus", "增大（可长按连续调整）")

        self._suffix = QLabel(suffix)
        self._suffix.setObjectName("fieldLabel")
        self._suffix.setFixedWidth(self.SUFFIX_WIDTH)
        self._suffix.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        h.addWidget(self._btn_minus)
        h.addWidget(self._edit)
        h.addWidget(self._suffix)
        h.addWidget(self._btn_plus)
        h.addStretch(1)

        # 单击（release 时触发一次）与长按连发：长按后抑制收尾那一次 click
        self._btn_minus.clicked.connect(lambda: self._on_click_step(-1))
        self._btn_plus.clicked.connect(lambda: self._on_click_step(1))
        self._btn_minus.pressed.connect(lambda: self._begin_hold(-1))
        self._btn_plus.pressed.connect(lambda: self._begin_hold(1))
        for btn in (self._btn_minus, self._btn_plus):
            btn.released.connect(self._end_hold)

        self._repeat = QTimer(self)
        self._repeat.setSingleShot(True)
        self._repeat.timeout.connect(self._on_repeat)

        self._sync_buttons()

    # ---------------- 内部 ----------------
    def _make_btn(self, icon_name: str, tip: str) -> "IconButton":
        """± 钮：30×30 固定尺寸、objectName=stepBtn（QSS 契约保持）

        （返回类型加引号：IconButton 定义在本文件更靠后处，注解延迟求值）
        """
        btn = IconButton(icon_name, size=self.BTN_SIZE, icon_size=13,
                         object_name="stepBtn", tooltip=tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _clamp(self, v) -> int:
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = self._min
        return max(self._min, min(self._max, v))

    def _fmt(self, v: int) -> str:
        """内部整数 → 显示文本（decimals=0 时原样输出）"""
        if self._decimals <= 0:
            return str(int(v))
        return f"{v / self._divisor:.{self._decimals}f}"

    def _parse(self, text: str) -> int:
        """显示文本 → 内部整数；解析失败时保持当前值"""
        text = (text or "").strip()
        if not text:
            return self._value
        try:
            if self._decimals <= 0:
                return int(text)
            return int(round(float(text) * self._divisor))
        except (TypeError, ValueError):
            return self._value

    def _make_validator(self):
        """按显示精度选校验器：整数用 QIntValidator，小数用 QDoubleValidator"""
        if self._decimals > 0:
            return QDoubleValidator(self._min / self._divisor,
                                    self._max / self._divisor,
                                    self._decimals, self)
        return QIntValidator(self._min, self._max, self)

    def _sync_buttons(self):
        """到边界时把对应按钮置灰，避免"点了没反应"的困惑

        U2（2026-10-07）：± 钮的置灰走 SmoothButton 的禁用淡出（120ms
        交叉淡染）；宿主用 blockSignals 包裹 setValue 批量刷（设置页
        统一刷新路径）时直接落终态 —— 子按钮自己的信号没被 block，
        changeEvent 探测不到批量语义，必须在这里替它拍板。"""
        self._btn_minus.setEnabled(self._value > self._min)
        self._btn_plus.setEnabled(self._value < self._max)
        if self.signalsBlocked():
            self._btn_minus.snap_disabled_feedback()
            self._btn_plus.snap_disabled_feedback()

    def _commit_edit(self):
        # V10：用户键入后回车/失焦提交 —— 数值来自打字本身，不做滑动
        self.setValue(self._clamp(self._parse(self._edit.text())),
                      animate=False)

    def _on_click_step(self, direction: int):
        if self._suppress_click:
            self._suppress_click = False
            return
        self.step_by(direction)

    def _begin_hold(self, direction: int):
        self._hold_dir = direction
        self._hold_tick = 0
        self._suppress_click = False
        self._repeat.start(self.HOLD_DELAY_MS)

    def _end_hold(self):
        self._repeat.stop()
        # 长按期间已经连发过 → 丢弃收尾的这一次 click，避免多走一格
        self._suppress_click = self._hold_tick > 0
        self._hold_dir = 0

    def _on_repeat(self):
        if not self._hold_dir:
            return
        self._hold_tick += 1
        # V10：长按连发路径直落终态不播滑动（规格 §2 V10 明确口径）
        self.step_by(self._hold_dir, animate=False)
        self._repeat.start(self.HOLD_FAST_MS
                           if self._hold_tick <= self.HOLD_FAST_TICKS
                           else self.HOLD_FASTER_MS)

    # ---------------- 对外 ----------------
    def value(self) -> int:
        return self._value

    def setValue(self, value: int, animate: bool = True):
        """设置数值；仅在真正变化时发信号（与 QSpinBox 行为一致）。

        V10（2026-10-07 交互视觉清单）：点击 ± / 滚轮等**单步**变更时
        数值文本 120ms 上/下滑动（方向与 ± 一致：+ 旧值上滑出、新值自
        下方滑入；− 反向）。直落终态的路径（语义同 reduce_motion）：
        长按连发（_on_repeat）、用户键入提交（_commit_edit）、宿主
        blockSignals 批量刷、已有滑动进行中（防叠影）。
        """
        new = self._clamp(value)
        if new == self._value:
            self._edit.setText(self._fmt(self._value))
            return
        old = self._value
        self._value = new
        self._sync_buttons()
        self.valueChanged.emit(new)
        ms = (motion.eased_ms("fast", self._speed)
              if animate and not self.signalsBlocked() else 0)
        if ms > 0 and self._slide_overlay is None:
            self._edit.setText("")
            self._slide_overlay = _StepperSlideOverlay(
                self._edit, self._fmt(old), self._fmt(new),
                up=(new > old), ms=ms,
                on_finished=lambda: self._finish_slide())
            self._slide_overlay.start()
            return
        self._edit.setText(self._fmt(new))

    def _finish_slide(self):
        """滑动收尾：恢复显示当前值（连发/批量变更插队时以 _value 为准）。"""
        self._slide_overlay = None
        self._edit.setText(self._fmt(self._value))

    def step_by(self, direction: int, animate: bool = True):
        self.setValue(self._value + direction * self._step, animate=animate)

    def setRange(self, minimum: int, maximum: int):
        self._min, self._max = int(minimum), int(maximum)
        self._edit.setValidator(self._make_validator())
        self.setValue(self._value, animate=False)
        self._sync_buttons()

    def wheelEvent(self, event):
        """滚轮微调：仅当数值框有焦点时生效，否则交给外层滚动区。

        这样鼠标滚设置页时不会被无意改动；有焦点时按 step 走一档
        （而非 1 个内部单位），与点击 ± 的粒度保持一致。
        """
        if not self._edit.hasFocus():
            event.ignore()
            return
        self.step_by(1 if event.angleDelta().y() > 0 else -1)
        event.accept()


class ToggleSwitch(QAbstractButton):
    """设置页开关（替代 QCheckBox 的视觉形式）：胶囊轨道 + 滑动圆点。

    - 选中 = 主题主色轨道 + ``on_primary`` 深色圆点 —— 主色是浅色，
      压白点对比不足（与 QSS 对比度护栏同一原则，自绘里同样遵守）；
    - 未选中 = 中性灰轨道 + 白色圆点，悬停时轨道略加深；
    - 切换有 120ms 滑动动画；**无动画进行时按 isChecked() 直接渲染**，
      因此 blockSignals 下批量 setChecked（refresh/恢复默认）也能立即
      显示正确状态，不依赖信号；
    - 颜色走 theme.get_colors，宿主面板在主题切换时调用 set_theme()。
    """

    TRACK_W, TRACK_H = 44, 24
    KNOB = 18
    MARGIN = 3
    ANIM_MS = 120

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（set_ui_speed 统一调用，与 SmoothButton 同口径）。"""
        cls._speed = motion.sanitize_speed(speed)

    def __init__(self, checked: bool = False, theme: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._theme = theme or DEFAULT_THEME
        self._progress = 1.0 if checked else 0.0
        self._thp = 0.0     # 未选中轨道 hover 进度（U3）：1 = 完全加深
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._anim.valueChanged.connect(self._on_anim)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(
            getattr(QEasingCurve.Type, motion.EASE["out"]))
        self._hover_anim.valueChanged.connect(self._on_track_hover)
        self.toggled.connect(self._on_toggled)

    # ---------------- 对外 ----------------
    def set_theme(self, theme: str):
        """主题切换时更新配色（由宿主面板统一调用）"""
        if theme and theme != self._theme:
            self._theme = theme
            self.update()

    # ---------------- 焦点态（UI 强化方案 A4）----------------
    def focusInEvent(self, event):
        # 焦点环由 paintEvent 自绘（QSS 管不到 QAbstractButton 的自绘内容）
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    # ---------------- 未选中轨道 hover 插值（U3）----------------
    def enterEvent(self, event):
        self._glide_track_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glide_track_hover(0.0)
        super().leaveEvent(event)

    def _glide_track_hover(self, target: float):
        """轨道 hover 加深进度插值：可打断重定向，reduce_motion 瞬显。"""
        current = self._thp
        if abs(target - current) <= 0.005:
            return
        ms = motion.eased_ms("fast", self._speed)
        if ms <= 0:
            self._thp = float(target)
            self.update()
            return
        anim = self._hover_anim
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.start()

    def _on_track_hover(self, value):
        self._thp = float(value)
        self.update()

    # ---------------- 内部 ----------------
    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value):
        self._progress = float(value)
        self.update()

    def paintEvent(self, event):
        colors = get_colors(self._theme)
        w, h = self.TRACK_W, self.TRACK_H
        running = self._anim.state() == QVariantAnimation.State.Running
        progress = self._progress if running else (1.0 if self.isChecked() else 0.0)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # 轨道
        if self.isChecked():
            track = QColor(colors["primary"])
        else:
            track = QColor(colors["text_disabled"])
            if self._thp > 0.0:
                # U3（2026-10-07）：未选中轨道 hover 加深从 darker(112)
                # 一帧瞬变改为 120ms 插值 —— 进度由 enter/leave 驱动
                # （_glide_track_hover），快速进出可打断、不残留。
                deep = QColor(colors["text_disabled"]).darker(112)
                t = max(0.0, min(1.0, self._thp))
                track = QColor(
                    int(round(track.red() + (deep.red() - track.red()) * t)),
                    int(round(track.green()
                              + (deep.green() - track.green()) * t)),
                    int(round(track.blue()
                              + (deep.blue() - track.blue()) * t)),
                )
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)

        # 圆点
        if self.isChecked():
            knob = QColor(colors["on_primary"])
        else:
            knob = QColor("#FFFFFF")
        span = w - self.KNOB - self.MARGIN * 2
        x = self.MARGIN + progress * span
        p.setBrush(knob)
        p.drawEllipse(QPointF(x + self.KNOB / 2.0, h / 2.0),
                      self.KNOB / 2.0, self.KNOB / 2.0)

        # 焦点环（A4）：控件尺寸固定 44×24 不能变，所以环**内缩**绘制，
        # 不扩占位（扩了会让设置页每一行高 2px，牵动大量几何断言）。
        # 环色用 $focus_ring 而不是 $primary —— 未选中态的轨道是
        # $text_disabled（浅灰），$primary 压上去同样只有约 1.6:1。
        if self.hasFocus():
            ring_px = 1.6
            inset = ring_px / 2.0
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(colors["focus_ring"]), ring_px))
            p.drawRoundedRect(
                QRectF(inset, inset, w - ring_px, h - ring_px),
                (h - ring_px) / 2.0, (h - ring_px) / 2.0)
            p.setPen(Qt.PenStyle.NoPen)
        p.end()


class ScreenToast:
    """屏幕级轻提示 · 兼容门面（2026-10-06 重设计收口）。

    实现整体迁往 :mod:`src.toast`（ToastCard + ToastCenter：屏幕底部
    居中 248px 定宽气泡、语义色、图标、动作钮、底部倒计时条、最多
    3 条堆叠 —— 设计稿 ``设计稿/轻提示气泡-高仿真-2026-10-06.html``）。
    本类只剩类级转发：旧调用点 ``ScreenToast.show_msg(text, theme, ms)``
    的三参形态不变（纯文本映射为 neutral 变体），``set_speed`` 继续作为
    动效档位广播入口并把档位转达 ToastCenter。
    """

    _speed = 1.0

    @classmethod
    def set_speed(cls, speed):
        """动画档位广播入口（main_window 经 controls.set_ui_speed 调用）。"""
        cls._speed = motion.sanitize_speed(speed)
        ToastCenter.set_speed(speed)

    @classmethod
    def show_msg(cls, text: str, theme: str = "", ms: int = 2600,
                 kind: str = "neutral", action=None):
        """显示一条屏幕底部轻提示（实现转发 src/toast.py）。

        ``kind`` / ``action`` 是重设计新增的可选参数：语义变体
        （success/info/warning/danger/accent/neutral/loading）与动作钮
        ``(文字, 回调)``；旧的三参调用完全不受影响。时长下限沿用旧口径
        800ms，0 按语义表默认驻留（仅显式传 ms<=0 的新代码会走到）。
        """
        return ToastCenter.push(
            kind=kind, title=str(text),
            ms=max(800, int(ms)) if ms and int(ms) > 0 else 0,
            action=action,
            theme=theme if theme in ("light", "dark") else "")


def set_ui_speed(speed):
    """设置页 anim_speed 档位广播的**模块级单一入口**：一次调用同步
    controls 内所有走 motion 缩放的动效控件（SmoothButton 与
    ToggleSwitch 的滑动/轨道 hover、ScreenToast —— 后者把档位转达
    src/toast.ToastCenter，新气泡的进出场/错峰随之缩放），以及
    src/row_hover 的列表行 hover 插值（U4，delegate 行级 hp tween）。
    main_window 启动与 anim_speed_changed 时调用，替代逐类 set_speed。"""
    SmoothButton.set_speed(speed)
    ToggleSwitch.set_speed(speed)
    SmoothInput.set_speed(speed)
    SmoothCheckBox.set_speed(speed)
    Stepper.set_speed(speed)
    ScreenToast.set_speed(speed)
    row_hover.RowHoverController.set_speed(speed)
    # V2：滚动条状态机过渡时长随档位缩放（src/smooth_scrollbar）
    from src.smooth_scrollbar import SmoothScrollBar as _SSB
    _SSB.set_speed(speed)


# 滚轮步长（px）：Qt 默认按字体行高步进，列表滚动一格一格"卡顿"；
# 28px ≈ 一行半的视差，配合像素级滚动是网页般的连续手感（清单 L3）。
SMOOTH_SCROLL_STEP_PX = 28


def tune_list_scrolling(view):
    """列表/滚动区滚轮手感统一（丝滑化清单 L3）：像素级滚动 + 固定步长。

    只调 QAbstractItemView 的滚动属性，不碰内容与选择行为；QSS 的
    滚动条样式不受影响。素材网格已有按单元格高度的定制步长（assets_panel），
    不走本入口。
    （2026-10-08 清单 F2：函数体里曾嵌着一段引用 self._theme/self._text
    的 chip 类残留 paintEvent —— 无人调用、也无法被调用（普通函数不是
    方法），属误导维护者的死代码，已删除。）
    """
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.verticalScrollBar().setSingleStep(SMOOTH_SCROLL_STEP_PX)


# ====================================================================
# 页内搜索快捷键（清单 A4，2026-10-08）
# ====================================================================
def attach_page_search_shortcut(page, line_edit):
    """Ctrl+F：页级聚焦搜索框（4 个带搜索框的面板统一入口）。

    - 笔记页 2026-10-07 批次 N3 的写法抽成公共 helper：插件中心、
      知识库、碎片工作台与笔记页共用一份实现；
    - 作用域用 ``WidgetWithChildrenShortcut`` 限定在面板页内——焦点在
      页内才触发，**不占全局命名空间**（主窗 Ctrl+W/H/T/K、F1、
      Ctrl+1~8、命令面板触发键与 Ctrl+K 均不受影响；全局热键默认表
      亦无 Ctrl+F，笔记页接入时已排查过）；
    - 聚焦后全选既有词，便于直接覆盖输入。
    返回创建的 QShortcut（一般忽略；调用方要追加行为时可再 connect）。
    """
    shortcut = QShortcut(QKeySequence("Ctrl+F"), page)
    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    shortcut.activated.connect(line_edit.setFocus)
    shortcut.activated.connect(line_edit.selectAll)
    return shortcut


class _FadeInOnceController(QObject):
    """fade_in_once 的动画控制器（10-07 硬崩家族铁律的兑现形态）。

    以目标 widget 为父 → widget 析构时控制器同灭，valueChanged/finished
    连接的接收者就是控制器自身（绑定方法），Qt 自动断开 —— 杜绝
    「无接收者闭包在析构竞态下于半死 effect/widget 上回调」的
    access violation（PageTitle / Stepper 滑层同款铁律）。
    """

    def __init__(self, widget, ms: int):
        super().__init__(widget)
        self._widget = widget
        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(0.0)
        widget.setGraphicsEffect(self._effect)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(int(ms))
        self._anim.setEasingCurve(
            getattr(QEasingCurve.Type, motion.EASE["out"]))
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._done)
        self._anim.start()

    def stop(self):
        self._anim.stop()

    def _tick(self, value):
        self._effect.setOpacity(max(0.0, min(1.0, float(value))))

    def _done(self):
        # 摘掉 effect（回到无覆盖层常态，避免常驻合成开销）
        self._widget.setGraphicsEffect(None)
        if getattr(self._widget, "_fade_in_anim", None) is self:
            self._widget._fade_in_anim = None
        self.deleteLater()


def fade_in_once(widget, ms: int) -> None:
    """一次性淡入（清单 D1，2026-10-08）：显隐终态语义不变，只补「到位过程」。

    约定（与 D1 硬约束对齐）：
    - **调用方负责先把终态落位**（setVisible(True) / setCurrentIndex）——
      本函数只做表现层透明度插值，可见性断言/数据流零影响；
    - ``ms <= 0``（reduce_motion 总闸 / 档位归零，``motion.duration`` 口径）
      或目标不可见时直接跳过（瞬时切换语义）；
    - 重复触发可打断：复用 widget 上的旧控制器，stop 后重建（不排队）；
    - 不设模态、不 grabMouse，动画期间交互照常。
    """
    if ms is None or int(ms) <= 0 or not widget.isVisible():
        return
    # 打断旧动画：先把旧 effect 摘掉，避免两个动画写同一个透明度
    old = getattr(widget, "_fade_in_anim", None)
    if old is not None:
        old.stop()
        widget._fade_in_anim = None
    widget.setGraphicsEffect(None)
    widget._fade_in_anim = _FadeInOnceController(widget, int(ms))


# ====================================================================
# 图标小部件（UI 强化方案 A2）
# ====================================================================
class IconLabel(QWidget):
    """只画一个自绘图标的小部件（零文字，尺寸即图标尺寸）。

    为什么不直接 ``QLabel.setPixmap``：那样得自己处理设备像素比，换主题
    还要重新生成 pixmap 再 setPixmap；自绘只在 paintEvent 里取色，
    ``set_color`` 后 update() 一次就够（跟随主题的成本从"重建资源"降到
    "重画一次"）。
    """

    def __init__(self, name, size=18, color=None, parent=None):
        super().__init__(parent)
        self._name = name
        self._size = int(size)
        self._color = (QColor(color) if color is not None
                       else QColor(140, 148, 166))
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @property
    def icon_name(self) -> str:
        return self._name

    @property
    def color(self) -> QColor:
        """当前取色（只读；换主题链路断言用）。"""
        return QColor(self._color)

    def set_icon_name(self, name):
        self._name = name
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        icon_render.paint_icon(
            p, self._name, QRectF(0.0, 0.0, float(self._size), float(self._size)),
            self._color)
        p.end()


class IconButton(SmoothButton):
    """自绘图标按钮（UI 强化方案 A2 的 P1 批次）：纯图标或「图标+文字」。

    ★ 最重要的设计约束：**objectName 原样保留站点既有值**。QSS 里
    ``#iconBtn`` / ``#cardCloseBtn`` / ``#stepBtn`` / ``#sideTabIconBtn`` /
    ``#secondaryBtn`` 等契约（背景、悬停、按压、焦点环）全部继续由
    theme.py 管辖，本类只负责「画哪个图标、当前用什么颜色」—— 也就是
    QSS 的 ``color:`` 管不到的 QIcon 位图。

    基类 SmoothButton（丝滑化清单 S2）补齐 hover/press 的背景过渡与
    按下位移 —— 端点色由 objectName 在 _SMOOTH_OVERLAYS 里解析。

    命中区外扩（U6，2026-10-07）：24px 视觉图标钮的命中区向外扩
    ``HIT_PAD``（4px，事件级矩形判定，见 eventFilter）—— 触屏标准的
    32px 最小命中尺寸，不改布局、不改绘制、几何断言零变化。

    颜色三态（对应 QSS 的 color / :hover 色 / :checked 色）：
      · 常态 ``off_color``   缺省 ``text_secondary``
      · 悬停 ``hover_color`` 缺省 ``primary``（``danger=True`` 时 ``danger``）
      · 选中 ``on_color``    缺省 ``primary``（checkable 按钮的 On 态位图）
    取值可以是**主题 token 名**（"danger" —— 每次 refresh 按当前主题解析，
    换主题自动跟随），也可以是 "#RRGGBB" 字面量（主题无关，如危险钮悬停
    的白）。禁用态一律 ``text_disabled``（Stepper 到边界置灰同源）。

    换主题：宿主带 ``theme_changed`` 信号就自动订阅（PageTitle 同款
    ``callable`` 守卫）；没有信号的对话框/便签由宿主显式调
    :meth:`apply_theme`，或接受构造时取色（短命对象，可接受）。
    """

    HIT_PAD = 4    # 命中区四向外扩 px（24px 钮 → 32px 命中，触屏标准）

    def __init__(self, icon_name, size=0, icon_size=16, object_name=None,
                 host=None, tooltip="", text="", checkable=False,
                 off_color=None, hover_color=None, on_color=None,
                 danger=False, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._icon_size = max(4, int(icon_size))
        self._host = host
        self._off_spec = off_color or "text_secondary"
        # hover 图标缺省色：显式传参优先；danger 系维持 danger；其余给
        # 「auto」哨兵 —— 刷新时按 overlay 端点现算（实底端点上画 primary
        # 图标会融进底色，2026-10-03，见 _auto_hover_color）。
        self._hover_spec = hover_color or ("danger" if danger else None)
        self._on_spec = on_color or "primary"
        self._theme_override = None
        self._hovered = False
        self._filtered_parent = None    # U6：命中区外扩的过滤器宿主（旧父）
        if object_name:
            self.setObjectName(object_name)
        if size:
            # 纯图标钮（标题栏/侧 Tab/Stepper）：站点既有固定尺寸原样保留
            self.setFixedSize(int(size), int(size))
        if checkable:
            self.setCheckable(True)
        if danger:
            # 与 QSS `QPushButton#iconBtn[danger="true"]:hover` 的属性选择器配套
            self.setProperty("danger", "true")
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_icon()
        # ★ 仅当 ``theme_changed`` 真的可 connect 时才订阅（PageTitle 同款）：
        #   测试替身常写成 ``SimpleNamespace(theme_changed=...)``，属性存在
        #   但没有 ``connect``，直接订阅会 AttributeError。
        signal = getattr(host, "theme_changed", None)
        if callable(getattr(signal, "connect", None)):
            signal.connect(self._on_theme_changed)
        # U6：构造时父控件已定（parent 形参在 super().__init__ 里生效），
        # 立刻挂命中区外扩过滤器；此后 setParent 换父由 event() 的
        # ParentChange 分支重挂。
        self._rebind_parent_filter()

    # ---------------- 对外 ----------------
    @property
    def icon_name(self) -> str:
        return self._icon_name

    def set_icon_name(self, name):
        """换图形（🌙/☀️、⛶/❐ 这类一钮两态的动态按钮用）。"""
        self._icon_name = name
        self._refresh_icon()

    def apply_theme(self, theme=None):
        """按主题重取图标颜色。宿主没有 ``theme_changed`` 信号时由宿主显式
        调（GlassDialog.apply_theme / CardWindow._apply_style / 便签窗）。"""
        if theme:
            self._theme_override = theme
        self._refresh_icon()

    # ---------------- 内部 ----------------
    @staticmethod
    def _resolve(spec, colors):
        """token 名 → 当前主题色值；其它（#RRGGBB / QColor）原样放行。"""
        if isinstance(spec, str) and spec in colors:
            return colors[spec]
        return spec

    def _refresh_icon(self):
        theme = (self._theme_override
                 or self._detect_theme() or DEFAULT_THEME)
        colors = get_colors(theme)
        off = self._resolve(self._off_spec, colors)
        if self._hovered:
            hover = self._resolve(self._hover_spec, colors)
            if hover is None:
                # 「auto」哨兵：按 overlay 悬停端点现算（见 _auto_hover_color）
                hover = self._auto_hover_color(colors) or off
            off = hover
        self.setIcon(icon_render.icon(
            self._icon_name, self._icon_size, off,
            on_color=self._resolve(self._on_spec, colors),
            disabled_color=colors["text_disabled"]))

    def _auto_hover_color(self, colors):
        """hover 图标缺省色（无显式 hover_color 时按 overlay 端点现算）。

        旧缺省「恒 primary」在两类端点上出问题（2026-10-03）：
        - **主色实底**（未命名 IconButton 兜底 primary_hover）—— primary
          图标画在主色实底上融进底色 → 取 ``on_primary``（与主按钮 QSS
          :hover 的文字色同源）；
        - **中性浅面实色**（cardCloseBtn / fragCopyBtn 系 surface_2/3）
          —— 图标跟端点 token 会与底同色隐形 → 返回 None，调用方回退
          常态 off 色（浅面上 secondary 色可读，也是 06 号重构「hover
          只叠一层浅面」的既定视觉）。
        - **淡染主色**（iconBtn / stepBtn / sideTabIconBtn 系）→ 端点
          token 色 primary，与 QSS ``:hover`` 文字色一致、与旧缺省等价。
        - **hover 端点 None**（2026-10-08 清单 C1 起 pluginMoreBtn 等
          「hover 视觉归 QSS」的按钮，端点登记为 None 避免与 QSS hover
          底两套机制打架）→ 返回 None，调用方回退常态 off 色 —— 与
          SmoothButton.paintEvent 的 ``spec is None`` 分支同口径（QSS
          管 hover 底，图标色不跟随）。
        """
        # ★取 [0]（hover 端点）：图标色跟随的是悬停底色的归宿；[1] 是
        #   press 端点，仅因现有表项 hover/press 同族才结果碰巧一致，
        #   对未来 hover/press 异族的表项（如 textBtn 之类再扩一组）
        #   是陷阱 —— 2026-10-03 修正。
        hover_spec = self._overlay_specs()[0]
        if hover_spec is None:
            # 端点未登记（None = hover 不换底色）→ 图标维持 off 色。
            # 2026-10-07 用户报障：pluginMoreBtn 是 IconButton，其登记
            # hover 端点为 None，直接解包会在 enterEvent 崩溃。
            return None
        hover_token, hover_alpha = hover_spec
        if hover_alpha >= 250:
            if str(hover_token).startswith("primary"):
                return colors["on_primary"]
            return None                       # 中性浅面 → 维持 off 色
        return colors.get(hover_token, colors["primary"])

    def _on_theme_changed(self, _theme=None):
        self._refresh_icon()

    # ---------------- 命中区外扩（U6，2026-10-07）----------------
    # Qt 只把「落点在控件 rect 内」的鼠标事件投递给控件本体 —— 图标钮
    # 视觉 24px、触屏标准要求命中 ≥32px，外扩的环带落在父控件坐标系里。
    # 做法：在父控件上装事件过滤器，代收环带内的按下/松开并映射回按钮
    # 本体（合成本地坐标事件）。**不改布局、不改绘制、sizeHint/geometry
    # 零变化** —— 命中区是事件层的概念，与视觉层完全解耦。
    def event(self, ev):
        if ev.type() == QEvent.Type.ParentChange:
            # 换父时重挂过滤器（旧父解绑防悬挂）
            self._rebind_parent_filter()
        return super().event(ev)

    def _rebind_parent_filter(self):
        """把命中区代收过滤器挂到当前父控件（无父时什么都不装）。"""
        if self._filtered_parent is not None:
            self._filtered_parent.removeEventFilter(self)
            self._filtered_parent = None
        p = self.parentWidget()
        if p is not None:
            p.installEventFilter(self)
            self._filtered_parent = p

    def _hit_rect(self):
        """命中矩形：自身 rect 四向外扩 HIT_PAD px（事件级判定用）。"""
        pad = self.HIT_PAD
        return self.rect().adjusted(-pad, -pad, pad, pad)

    def eventFilter(self, obj, event):
        # 半析构防御：QApplication 销毁 / fixture 清理阶段，Qt 可能对
        # 「C++ 对象仍活、Python 包装被重建」的 IconButton 派发事件 ——
        # 新包装没有实例属性，摸 _filtered_parent 会 AttributeError（V 批
        # 测试 teardown 阶段实锤）。拿不到状态就按「无命中区外扩」处理，
        # 事件走原路由，行为与未装过滤器一致。
        filtered_parent = getattr(self, "_filtered_parent", None)
        et = event.type()
        if (filtered_parent is not None and obj is filtered_parent
                and self.isVisible()
                and self.isEnabled()
                and et in (QEvent.Type.MouseButtonPress,
                           QEvent.Type.MouseButtonRelease,
                           QEvent.Type.MouseButtonDblClick)):
            local = self.mapFromParent(event.position().toPoint())
            if self._hit_rect().contains(local):
                # 环带内代收：把落点夹取进 rect —— QAbstractButton 的
                # hitButton 只认 rect 内坐标，原始环带坐标会被它忽略。
                # 若落点其实被兄弟控件截走，Qt 派发给兄弟、进不到本
                # 过滤器，不会误吞别人的点击。
                r = self.rect()
                cx = min(max(float(local.x()), float(r.left())),
                         float(r.right()))
                cy = min(max(float(local.y()), float(r.top())),
                         float(r.bottom()))
                mapped = QMouseEvent(
                    et, QPointF(cx, cy), event.globalPosition(),
                    event.button(), event.buttons(), event.modifiers())
                QCoreApplication.sendEvent(self, mapped)
                return True    # 已消费，父控件不再处理
        return super().eventFilter(obj, event)

    # ---------------- 悬停变色 ----------------
    # QSS 的 :hover 只能改 color:，管不到 QIcon 位图 —— 悬停时把 Off 态
    # 位图换成 hover 色重设一次（pixmap 按「名称+尺寸+颜色」缓存，进出
    # 各只付一次生成成本，之后是字典查找）。
    def enterEvent(self, event):
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)


class EmptyState(QWidget):
    """列表空态引导（UI 强化方案 A3）：自绘图标 + 标题 + 提示 + 可选动作钮。

    两种用法：
      ①普通部件进布局——fragments 的 holder_grid 叠放、assets 的 _stack
      空态页、plugins 的外层 vbox；
      ②:meth:`attach_to` 叠成列表控件的**覆盖层**——notes / tasks /
      knowledge / 软件导航用这种：列表本体一行代码不动，不碰任何
      ``itemAt(0)`` / ``count()`` 定值断言，也不进会被整体销毁重建的
      布局（软件导航的 ``_clear_layout`` 会清空整张网格）。

    QSS 契约沿用 fragments 旧版 ``_EmptyState``：标题 ``sectionLabel`` /
    提示 ``hintLabel`` / 动作钮 ``secondaryBtn``；图标走 A2 自绘体系
    （缺字形环境不再出豆腐块）。★内部**禁止**出现 ``pageTitle`` 字样 ——
    verify_icons_render 的 H 段断言每页恰好一个 PageTitle。
    """

    def __init__(self, icon, title, hint, action_text=None, on_action=None,
                 icon_size=40, object_name=None, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(8)
        v.addStretch()

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        self.icon_label = IconLabel(icon, icon_size)
        icon_row.addWidget(self.icon_label)
        icon_row.addStretch()
        v.addLayout(icon_row)

        self._title = QLabel(title)
        self._title.setObjectName("sectionLabel")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._title)

        self._desc = QLabel(hint)
        self._desc.setObjectName("hintLabel")
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setWordWrap(True)
        v.addWidget(self._desc)

        self._action = None
        if action_text:
            self._action = SmoothButton(action_text)
            self._action.setObjectName("secondaryBtn")
            self._action.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_action is not None:
                self._action.clicked.connect(on_action)
            self._action.setVisible(False)
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(self._action)
            btn_row.addStretch()
            v.addLayout(btn_row)

        v.addStretch()
        if object_name:
            self.setObjectName(object_name)
        # 无动作钮的覆盖层对鼠标全透明：盖在列表上时右键菜单/滚轮照常
        # 落到列表本体（notes/tasks/knowledge 的空态没有可点的东西）
        if self._action is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                              True)

    # ---------------- 对外 ----------------
    @property
    def action(self):
        """动作钮（fragments 旧版 ``_empty_state._action`` 契约的承接）"""
        return self._action

    def set_state(self, icon, title, hint, show_action=False):
        """切换空态内容（如 fragments 的「全空」⇄「筛选无结果」两态）"""
        self.icon_label.set_icon_name(icon)
        self._title.setText(title)
        self._desc.setText(hint)
        if self._action is not None:
            self._action.setVisible(show_action)

    def attach_to(self, host):
        """叠成 ``host``（列表控件/容器）的覆盖层：随其 resize 贴合。

        初始隐藏，由调用方按数据有无显式 ``setVisible`` / ``raise_``。
        """
        self.setParent(host)
        self.setGeometry(host.rect())
        host.installEventFilter(self)
        self.hide()
        self.raise_()

    def apply_theme(self, theme=None):
        """图标取色跟主题（宿主 apply_theme 链里顺手调一次）。"""
        self.icon_label.set_color(
            get_colors(theme or DEFAULT_THEME)["text_placeholder"])

    # ---------------- 内部 ----------------
    def eventFilter(self, obj, event):
        if (obj is self.parentWidget()
                and event.type() == QEvent.Type.Resize):
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, event)


class PageTitle(QWidget):
    """页面标题：自绘图标 + 标题文字。

    ★ 唯一的设计约束：**文字仍是 ``QLabel#pageTitle``**。图标是独立子控件，
    不参与文字度量 —— 因此 ``theme.py`` 里 ``QLabel#pageTitle`` 的 QSS 与
    所有既有几何/样式断言口径零变化（把标题换成"一个自绘整体"会同时改掉
    文字度量与 QSS 命中，是没必要的风险）。

    换主题：宿主带 ``theme_changed`` 信号就自动订阅（与 ``settings_panel``
    订阅宿主信号同一范式）。★ 订阅用的是 **PageTitle 自身的绑定方法**而不是
    lambda —— 绑定方法让 Qt 在本部件析构时自动断开；lambda 没有接收者上下文，
    宿主活得比面板久时会在已析构对象上回调（段错误的经典成因）。
    """

    def __init__(self, icon_name, text, host=None, icon_size=18,
                 spacing=8, parent=None):
        super().__init__(parent)
        self._host = host
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        self.icon = IconLabel(icon_name, icon_size)
        lay.addWidget(self.icon)
        self.label = QLabel(text)
        self.label.setObjectName("pageTitle")
        lay.addWidget(self.label)
        self.apply_theme()
        # ★ 仅当 ``theme_changed`` 真的可 connect 时才订阅。
        #   只判 ``is not None`` 不够：测试替身常写成
        #   ``SimpleNamespace(theme_changed=SimpleNamespace(emit=...))`` —— 属性
        #   存在但没有 ``connect``（2026-09-30 test_ui_scale.py 的三个 error
        #   即此）。判 ``callable`` 连 ``connect=None`` 的形态也一并挡住。
        #   与 settings_panel._on_ui_scale_changed 的 ``callable`` 守卫同口径。
        signal = getattr(host, "theme_changed", None)
        if callable(getattr(signal, "connect", None)):
            signal.connect(self._on_theme_changed)

    # ---- 便捷访问（省得调用方层层 .label）----
    def text(self) -> str:
        return self.label.text()

    def set_text(self, text):
        self.label.setText(text)

    def set_icon_name(self, name):
        self.icon.set_icon_name(name)

    @property
    def icon_name(self) -> str:
        return self.icon.icon_name

    # ---- 主题 ----
    def _on_theme_changed(self, _theme=None):
        self.apply_theme()

    def apply_theme(self, theme=None):
        """按主题重取图标颜色。宿主没有 ``theme_changed`` 信号时由调用方显式调
        （例如 ``AppLauncherPage._apply_style()`` 已经在换主题时被宿主调用）。"""
        theme = theme or getattr(self._host, "current_theme", None) or DEFAULT_THEME
        self.icon.set_color(get_colors(theme)["text"])
