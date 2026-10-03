# -*- coding: utf-8 -*-
"""启动闪屏充能动画（2026-10-03 方案 D 重写）单元测试（offscreen）。

钉死的行为（与 高仿真设计稿-2026-10-03 的 QPainter 施工基准对齐）：
  1. 几何：320×184 固定尺寸、圆角 14
  2. set_stage(text, i, total)：文案 / 计数 / 充能目标三同步；
     空文案不覆盖；档位夹取（越界回 0/total）
  3. 充能链（因果动画）：光点在途不充能，链播完 frac 收敛到 index/total
  4. 快机节奏：未完链立即结算再开新链（跳格但不丢档）
  5. 满格苏醒只播一次；after_wake 在苏醒完成后触发（三段接力焦点
     交接点）；苏醒后才注册的回调下一跳立即触发
  6. finish：淡出关闭 + 幂等；补发未触发的交接回调（主窗必显兜底）
  7. reduce_motion：跳格充能零动画、交接立即放行、finish 直接 close
  8. 接线回归：knowledge_ball.py 三段接力编排 + 阶段总数与 _mark
     调用数钉死同步（增删 _mark 必须同步 _BOOT_STAGE_TOTAL）
"""

import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest                        # noqa: E402
from PyQt6.QtWidgets import QApplication              # noqa: E402

from src import motion                                # noqa: E402
from src.splash import LaunchSplash                   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _chain_ms() -> int:
    """光点 + 充能链的标定时长（180+200）"""
    return motion.MOTION["spark_fly"] + motion.MOTION["progress_tween"]


def _wait_chain(extra_ms: int = 0):
    """等一条充能链播完（1.6× 裕量，抵 offscreen 调度抖动）"""
    QTest.qWait(int(_chain_ms() * 1.6) + extra_ms)


# ====================================================================
# 1. 几何（QPainter 施工基准）
# ====================================================================
class TestGeometry:
    def test_fixed_size_320x184(self, qapp):
        s = LaunchSplash()
        assert (s.W, s.H) == (320, 184)
        assert s.width() == 320 and s.height() == 184

    def test_radius_14(self, qapp):
        assert LaunchSplash.RADIUS == 14


# ====================================================================
# 2. set_stage：文案 / 计数 / 目标
# ====================================================================
class TestSetStage:
    def test_text_counter_target_sync(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("构建主窗口界面…", 6, 11)
        assert s._stage_text == "构建主窗口界面…"
        assert (s._counter, s._total) == (6, 11)
        assert abs(s._target - 6 / 11) < 1e-9
        s.finish()

    def test_empty_text_keeps_text_but_advances(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("检查数据完整性…", 2, 11)
        s.set_stage("", 3, 11)
        assert s._stage_text == "检查数据完整性…"
        assert s._counter == 3
        s.finish()

    def test_index_clamped_into_total(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("加载插件…", 99, 11)
        assert s._counter == 11
        s.set_stage("最后准备…", -5, 11)
        assert s._counter == 0
        s.finish()


# ====================================================================
# 3. 充能链（因果动画）与快机结算
# ====================================================================
class TestChargeChain:
    def test_charge_only_after_spark_arrives(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("加载知识库…", 3, 11)
        assert s._spark is not None          # 光点已在途
        QTest.qWait(60)                      # 仍在光点段（180ms）内
        assert s._frac < s._target + 1e-9    # 到达才充能
        _wait_chain()
        assert abs(s._frac - 3 / 11) < 1e-6
        s.finish()

    def test_rapid_marks_settle_then_restart(self, qapp):
        """快机节奏：未完链立即结算，新链直达新档（跳格但不丢档）"""
        s = LaunchSplash()
        s.start()
        s.set_stage("初始化悬浮球…", 7, 11)
        QTest.qWait(50)                      # 链在途（光点未到）
        s.set_stage("接线与托盘…", 9, 11)
        _wait_chain()
        assert abs(s._frac - 9 / 11) < 1e-6
        assert s._counter == 9
        s.finish()


# ====================================================================
# 4. 满格苏醒 + 三段接力交接
# ====================================================================
class TestWake:
    def test_wake_once_and_after_wake_fires(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("显示主窗口…", 11, 11)
        fired = []
        s.after_wake(lambda: fired.append(1))
        QTest.qWait(_chain_ms() + motion.MOTION["wake_halo"] * 2 + 200)
        assert s._woke and s._wake_done
        assert abs(s._frac - 1.0) < 1e-6
        assert fired == [1]
        # 苏醒完成后才注册的回调：下一跳立即触发（幂等不重播苏醒）
        s.after_wake(lambda: fired.append(2))
        QTest.qWait(60)
        assert fired == [1, 2]
        s.finish()


# ====================================================================
# 5. finish 收尾
# ====================================================================
class TestFinish:
    def test_finish_fades_out_and_idempotent(self, qapp):
        from PyQt6.sip import isdeleted
        s = LaunchSplash()
        s.start()
        s.set_stage("显示主窗口…", 11, 11)
        QTest.qWait(_chain_ms() + motion.MOTION["wake_halo"] * 2 + 200)
        s.finish()
        # 轮询等待淡出完成（offscreen 调度有抖动，固定等待会偶发超时）
        left = 3000
        while left > 0 and not isdeleted(s) and s.isVisible():
            QTest.qWait(50)
            left -= 50
        assert isdeleted(s) or not s.isVisible()
        s.finish()                            # 幂等：重复调用不炸
        QApplication.processEvents()

    def test_finish_releases_pending_wake_callback(self, qapp):
        """收尾兜底：交接回调不能因 finish 先于苏醒而丢失"""
        s = LaunchSplash()
        s.start()
        s.set_stage("初始化素材与导航…", 5, 11)   # 未满格
        fired = []
        s.after_wake(lambda: fired.append(1))
        s.finish()
        QTest.qWait(80)
        assert fired == [1]


# ====================================================================
# 6. reduce_motion：零动画口径
# ====================================================================
class TestReduceMotion:
    def test_zero_anim_chain_and_instant_close(self, qapp):
        from PyQt6.sip import isdeleted
        motion.set_reduce_motion(True)
        try:
            s = LaunchSplash()
            s.start()
            s.set_stage("检查数据完整性…", 2, 11)
            assert abs(s._frac - 2 / 11) < 1e-9      # 跳格充能
            s.set_stage("显示主窗口…", 11, 11)
            assert s._wake_done                      # 苏醒零动画直接完成
            fired = []
            s.after_wake(lambda: fired.append(1))
            QApplication.processEvents()
            assert fired == [1]
            s.finish()
            QTest.qWait(60)
            assert isdeleted(s) or not s.isVisible() # 直接 close，不淡出
        finally:
            motion.set_reduce_motion(False)


# ====================================================================
# 7. 绘制冒烟：真实渲染 + 圆角外透明
# ====================================================================
class TestPaint:
    def test_render_and_transparent_corner(self, qapp):
        s = LaunchSplash()
        s.start()
        s.set_stage("最后准备…", 10, 11)
        QTest.qWait(60)                       # 交叉淡变 / 光点画几帧
        img = s.grab().toImage()
        assert img.pixelColor(0, 0).alpha() == 0
        # y=55：标题区之下、进度环顶点之上的空白玻璃区
        center = img.pixelColor(img.width() // 2, 55)
        bg = s._c_bg
        diff = (abs(center.red() - bg.red())
                + abs(center.green() - bg.green())
                + abs(center.blue() - bg.blue()))
        assert center.alpha() >= 190 and diff <= 60
        s.finish()


# ====================================================================
# 8. 接线回归：knowledge_ball.py 三段接力（源码钉死）
# ====================================================================
class TestRelayWiring:
    @pytest.fixture(scope="class")
    def src(self):
        with open(os.path.join(BASE, "knowledge_ball.py"),
                  encoding="utf-8") as f:
            return f.read()

    def test_stage_total_matches_mark_calls(self, src):
        """阶段总数与 main() 的 _mark 调用数钉死同步（设计稿 §三）"""
        marks = re.findall(r'_mark\("', src)
        m = re.search(r"_BOOT_STAGE_TOTAL = (\d+)", src)
        assert m, "_BOOT_STAGE_TOTAL 常量缺失"
        assert len(marks) == int(m.group(1)) == 11

    def test_relay_order_and_tokens(self, src):
        assert 'splash.set_stage(f"{stage}…", _boot["n"], _BOOT_STAGE_TOTAL)' \
            in src
        assert "after_wake(_reveal_main_window)" in src
        # 旧直接 show 序列已移除：show 只发生在苏醒回调 / 兜底里
        assert "main_window.show()\n    _mark(" not in src
        assert src.index('_mark("显示主窗口")') \
            < src.index("def _reveal_main_window")
        assert "QTimer.singleShot(5000, _reveal_main_window)" in src
        assert 'motion.MOTION["splash_hold"]' in src
        assert 'motion.MOTION["ball_delay"]' in src
        assert "play_startup_pop" in src
