# -*- coding: utf-8 -*-
"""
====================================================================
三段接力启动编排  -  startup_relay
====================================================================
自 knowledge_ball.py main() 原段外迁（2026-10-08 拆分轮）。2026-10-03
方案 D 的接力序列原样搬运，宿主依赖全部参数注入，禁 import
knowledge_ball（装配层单向依赖）：

  ① 第 11 档打点（_mark 由宿主 main() 负责）：闪屏充能满格并苏醒
     （弹跳 + 光晕）；
  ② 苏醒完成 → 主窗 show（入场 fade/rise，焦点交接）；
  ③ show 后 splash_hold(120ms) 闪屏才淡出（splash_out）、
     ball_delay(160ms) 悬浮球 OutBack 压轴浮现（球心不动 0.7→1.0）。

焦点先交接后离场；reduce_motion 下三段全瞬显（时长归零口径）。
另挂 5s 兜底：闪屏链路万一卡死（异常/平台差异）强制显示主窗。
====================================================================
"""

from PyQt6.QtCore import QTimer

from src import motion
from src.logger import get_logger


def wire_startup_relay(*, splash, main_window, ball, config_manager,
                       boot_speed, boot, logger, deferred_plugin_load):
    """接线三段接力：苏醒 → show 主窗 → 编排闪屏淡出与悬浮球压轴。

    boot        : main() 的 _boot 打点字典（读 t0 出「全部完成」耗时）
    logger      : main() 的启动 logger（与 [启动] 打点同一输出口径）
    deferred_plugin_load : 插件延迟加载回调（幂等，宿主闭包）
    """
    _relay_shown = {"flag": False}

    def _reveal_main_window():
        """满格苏醒完成 → show 主窗 → 编排闪屏淡出与悬浮球压轴（幂等）"""
        if _relay_shown["flag"]:
            return
        _relay_shown["flag"] = True
        main_window.show()
        get_logger().info("主窗口已显示，进入事件循环")
        import time
        now = time.monotonic()
        logger.info(f"[启动] 全部完成 总计 {now - boot['t0']:.2f}s")
        QTimer.singleShot(
            motion.duration(motion.MOTION["splash_hold"], boot_speed),
            splash.finish)
        if config_manager.get("ball_visible", True):
            QTimer.singleShot(
                motion.duration(motion.MOTION["ball_delay"], boot_speed),
                ball.play_startup_pop)
        # 插件延迟加载：入场编排收尾后 800ms 再加载（球浮现已完成）
        QTimer.singleShot(
            motion.duration(motion.MOTION["ball_delay"], boot_speed) + 800,
            deferred_plugin_load)

    splash.after_wake(_reveal_main_window)
    # 兜底：闪屏链路万一卡死（异常/平台差异），5s 强制显示主窗
    QTimer.singleShot(5000, _reveal_main_window)
