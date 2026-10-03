# -*- coding: utf-8 -*-
"""
====================================================================
动效 token 与时长口径  -  motion
====================================================================
UI 强化方案 A1（2026-09-30）：全仓 26 处 ``setDuration`` + 35 处缓动手写、
且 ``anim_speed`` 缩放的算术式在 card_window / tasks_panel / main_window
各写了一份。本模块把「时长 token / 缓动名 / 档位缩放口径」收敛为**唯一入口**，
为后续动效改造（含 reduce_motion 开关）提供单一改动点。

设计约束
--------
**本模块严格不 import PyQt6**（与 ``nav_layout.py`` / ``md_export.py`` /
``app_version.py`` 同款约束）→ pytest 可直接 import 单测，无需 offscreen。
因此 ``EASE`` 用**字符串键**而不是 ``QEasingCurve.Type`` 枚举；调用侧自行做一次
``getattr(QEasingCurve.Type, motion.EASE["out"])``。

唯一缩放口径
------------
``duration(ms, speed) == max(0, int(ms / speed))``，speed 非法或非正回退 1.0
（中性档，避免「档位为 0 → 时长爆炸」），并以 ``MIN_SPEED`` 兜底防除零。
注意：这统一了原先 main_window 的 ``round()`` 写法 → 仅在档位为非整数
（如 0.6/1.3）时可能与旧值差 1ms，测试与肉眼均不可见。
====================================================================
"""

import math

# ---- 时长 token（毫秒）：语义化档位，新增动效一律从这里取，禁止再写字面量 ----
# base / slow / stagger 是全局语义档；main_window 的 NAV_GROUP_* 系列已于
# 2026-10-01 独立调优（220/170/12）并与这里解耦 —— 全局档仍服务其余动效，
# 侧栏本地常量只服务侧栏自身的丝滑度，两边数值不再要求一致。
MOTION = {
    "instant": 0,      # 无动画（调用侧据此跳过动画注册 / 直接落终态）
    "fast": 120,       # 悬停 / 按下等即时反馈
    "base": 180,       # 通用位移、淡入（= NAV_DROP_MS）
    "slow": 280,       # 展开 / 折叠等长过渡（侧栏分组已解耦：220/170）
    "stagger": 18,     # 逐条错峰步长（侧栏分组已解耦：12）

    # ---- 启动链路（高仿真设计稿 2026-10-03 方案 D）----
    "spark_fly": 180,       # 闪屏光点：环端 → 球心（InCubic 吸入）
    "progress_tween": 200,  # 光点到达后：球体 r/glow + 前景环插值
    "wake_pop": 220,        # 满格苏醒：球弹跳（sin 半波）
    "wake_halo": 320,       # 满格苏醒：光晕扩散
    "splash_out": 240,      # 闪屏淡出（主窗入场播 splash_hold 后才启动）
    "splash_hold": 120,     # 主窗 show → 闪屏淡出的焦点交接延迟
    "enter_fade": 200,      # 主窗入场淡入（原字面量收口）
    "enter_rise": 320,      # 主窗入场 16px 上浮（原字面量收口）
    "ball_delay": 160,      # 主窗 show → 悬浮球浮现延迟
    "ball_pop": 260,        # 悬浮球 OutBack 弹性浮现
}

# ---- 缓动名（Qt ``QEasingCurve.Type`` 的成员名）----
EASE = {
    "out": "OutCubic",        # 通用：进入 / 落位
    "out_quint": "OutQuint",  # 强调：分组展开等需要「急起缓停」的场景
    "in_out": "InOutCubic",   # 往复：展开 ↔ 折叠共用一条曲线
    "in": "InCubic",          # 吸入：闪屏光点飞向球心（加速汇聚）
    "out_back": "OutBack",    # 过冲：悬浮球浮现的弹性回弹
}

# ---- 档位（anim_speed）合法区间，与 config.py 的 _CONFIG_RANGES 对齐 ----
SPEED_MIN = 0.5
SPEED_MAX = 2.0
# 缩放系数下限：仅用于兜底防除零，正常路径由 SPEED_MIN 保证
MIN_SPEED = 0.01


def sanitize_speed(speed) -> float:
    """把外部传入的档位规整为合法缩放系数。

    非法（None / 非数值 / NaN / inf）或非正值一律回退 ``1.0``（中性档）；
    正的小数按 ``MIN_SPEED`` 兜底，避免 ``ms / speed`` 溢出。
    注意这里**不做** 0.5~2.0 的区间夹取——那是配置层
    （``config._CONFIG_RANGES``）的职责，动效层再夹一次会掩盖配置错误。
    """
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return max(MIN_SPEED, value)


# 减弱动效总闸（#14，2026-10-01）：True 时 duration() 一律归零。
# 全应用只此一个开关位——A1 已把散落的时长缩放收敛进本模块，这里翻转
# 一次，导航展开/掉落、任务勾选、卡片窗口的所有过渡全部瞬显。
_REDUCE_MOTION = False


def set_reduce_motion(on) -> None:
    """设置减弱动效开关（启动时读配置 / 设置页翻转时调用）。"""
    global _REDUCE_MOTION
    _REDUCE_MOTION = bool(on)


def reduce_motion() -> bool:
    """当前是否处于「减弱动效」状态（测试与调用侧自检用）。"""
    return _REDUCE_MOTION


def duration(ms, speed=1.0) -> int:
    """基准时长按动画档位缩放（档位越大越快 → 时长越短）。

    全应用唯一口径，等价于 ``max(0, int(ms / sanitize_speed(speed)))``。
    返回值下限为 0：``int`` 截断 + ``max(0, ...)`` 保证不会出现负时长；
    调用侧若需要「至少 1ms」（如 QPropertyAnimation 不接受 0），
    自行 ``max(1, ...)``——保持与本函数解耦。

    ★ 减弱动效开启时**一律返回 0**（含脏输入）：语义是「过渡直接瞬显」，
    而不是「按比例缩短」——部分缩短会让动画停在中间帧，观感更差。
    """
    if _REDUCE_MOTION:
        return 0
    try:
        base = float(ms)
    except (TypeError, ValueError):
        return 0
    return max(0, int(base / sanitize_speed(speed)))


def eased_ms(kind: str, speed=1.0) -> int:
    """按 token 名取基准时长并缩放。

    未知 token 直接 ``KeyError``（不静默回退）——拼错的 token 要在开发期
    立刻暴露，而不是变成一个「看起来能跑」的默认时长。
    """
    return duration(MOTION[kind], speed)
