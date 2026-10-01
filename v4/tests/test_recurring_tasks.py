# -*- coding: utf-8 -*-
"""周期任务插件（plugins/recurring-tasks）回归 —— 规则引擎 + 持久化 + 调度。

为什么单独钉这个插件的纯逻辑：它**在后台自动往用户任务列表里写东西**。
写错一次的代价不是「面板显示难看」，而是用户的待办里多出一条莫名其妙的
任务、或者同一件事一天冒出三条。所以本文件围绕三条不得违反的行为：

  1. **同一天只生成一次**（幂等）—— 重复启动程序、一天内多次 tick、
     「立即检查」按多次，都只能产生一条任务。锚点是 ``last_fired``。
  2. **错过的只补最近一次** —— 程序关了三天，启动后不该冒出三条任务，
     只补最近到点的那一次。
  3. **失败不假装成功** —— 未授权 / 写失败时 ``last_fired`` **不推进**，
     但也不能每 60s 重试刷屏：失败日被记进 ``_failed_marks``。

另外钉死两处「静默吃掉用户数据」的坑（都是写文件时踩出来的）：
  - 数据目录不可用时 ``RuleScheduler.load()`` 必须**保留**内存里的规则，
    否则每次切入页面（showEvent → load）都会清空用户刚建的规则；
  - 坏规则文件只降级不删档（原文件保留，用户还能自己修）。

本文件不构造任何窗口；只在测 ``start/stop`` 时借一次 QApplication。
"""

import calendar
import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_DIR = os.path.join(ROOT, "plugins", "recurring-tasks")
PLUGIN_PATH = os.path.join(PLUGIN_DIR, "plugin.py")
MANIFEST_PATH = os.path.join(PLUGIN_DIR, "manifest.json")
_MOD_NAME = "fp_test_recurring_tasks_plugin"


# ====================================================================
# 夹具与替身
# ====================================================================
@pytest.fixture(scope="module")
def rt():
    """按插件加载器的方式直载真实 plugin.py（纯逻辑部分不需要 QApplication）"""
    spec = importlib.util.spec_from_file_location(_MOD_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def qapp():
    """只为 ``QTimer.start/stop`` 借一个应用实例（不建任何窗口）"""
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Log:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, msg):
        self.warnings.append(str(msg))

    def info(self, msg):
        self.infos.append(str(msg))


class _Writer:
    """假写入门面：记录调用，可被指定返回值 / 抛异常"""

    def __init__(self, ret=101, exc=None):
        self.ret = ret
        self.exc = exc
        self.calls = []

    def add_task(self, title, note="", deadline=""):
        self.calls.append((title, note, deadline))
        if self.exc is not None:
            raise self.exc
        return self.ret


class _Ctx:
    """假宿主上下文（只覆盖本插件真正用到的面）"""

    def __init__(self, ret=101, exc=None, data_dir=""):
        self.write = _Writer(ret, exc)
        self.logger = _Log()
        self.data_dir = data_dir
        self.toasts = []
        self._win = None

    def show_toast(self, text, ms=2800):
        self.toasts.append((text, ms))
        return True

    def parent_window(self):
        return self._win


def _rule(rt, **over):
    """构造一条已归一化的合法规则（默认：每天 09:00，不设截止日）"""
    raw = {"title": "写周报", "kind": rt.KIND_DAILY, "time": "09:00",
           "due_days": -1, "enabled": True}
    raw.update(over)
    rule, err = rt.validate_rule(raw)
    assert rule is not None, err
    return rule


def _at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute)


# 2026-01-05 是周一（本机用 date.weekday() 复核，见 test_weekday_premise）
MON = date(2026, 1, 5)


# ====================================================================
# A 纯函数：时间解析
# ====================================================================
class TestTimeParsing:
    def test_parse_hm_normal(self, rt):
        assert rt.parse_hm("09:30") == (9, 30)
        assert rt.parse_hm(" 0:05 ") == (0, 5)
        assert rt.parse_hm("23:59") == (23, 59)

    @pytest.mark.parametrize("bad", ["", "9", "9:5:1", "24:00", "12:60",
                                    "-1:00", "aa:bb", None, 930, "09:3 0"])
    def test_parse_hm_dirty(self, rt, bad):
        assert rt.parse_hm(bad) is None

    def test_format_hm_normalizes(self, rt):
        assert rt.format_hm("9:5") == "09:05"
        assert rt.format_hm("bad") == ""

    def test_as_date(self, rt):
        assert rt.as_date("2026-03-04") == date(2026, 3, 4)
        assert rt.as_date("2026-03-04 10:00") == date(2026, 3, 4)
        assert rt.as_date(date(2026, 3, 4)) == date(2026, 3, 4)
        # 「脏日期 = 无日期」与宿主同口径
        assert rt.as_date("") is None
        assert rt.as_date("2026-3") is None
        assert rt.as_date("2026-13-40") is None
        assert rt.as_date(None) is None
        # datetime 是 date 的子类，别被当 date 直接返回
        assert rt.as_date(datetime(2026, 3, 4, 5, 6)) == date(2026, 3, 4)

    def test_month_days_leap(self, rt):
        assert rt.month_days(2026, 2) == 28
        assert rt.month_days(2024, 2) == 29
        assert rt.month_days(2026, 4) == 30
        assert rt.month_days(2026, 12) == 31
        # 与 stdlib 同源，不是自己拍的常数表
        assert rt.month_days(2024, 2) == calendar.monthrange(2024, 2)[1]

    def test_parse_moment(self, rt):
        assert rt.parse_moment("2026-03-04 10:30") == \
            datetime(2026, 3, 4, 10, 30)
        assert rt.parse_moment("2026-03-04") == datetime(2026, 3, 4, 0, 0)
        assert rt.parse_moment("") is None
        assert rt.parse_moment("不是时间") is None
        assert rt.parse_moment(None) is None

    def test_rule_start_missing_means_no_lower_bound(self, rt):
        assert rt.rule_start({}) is None
        assert rt.rule_start({"created_at": ""}) is None
        assert rt.rule_start({"created_at": "脏"}) is None
        assert rt.rule_start({"created_at": "2026-03-04 10:30"}) == \
            datetime(2026, 3, 4, 10, 30)


# ====================================================================
# B 纯函数：规则命中（rule_fires_on）
# ====================================================================
class TestRuleFiresOn:
    def test_weekday_premise(self):
        """下面所有「周几」断言都建立在这条前提上，先钉死它"""
        assert MON.weekday() == 0

    def test_daily_always_fires(self, rt):
        r = _rule(rt)
        for d in (date(2026, 1, 1), date(2026, 2, 28), MON):
            assert rt.rule_fires_on(r, d) is True

    def test_weekly_hits_selected_only(self, rt):
        r = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[0, 2])   # 周一、周三
        assert rt.rule_fires_on(r, MON) is True
        assert rt.rule_fires_on(r, MON + timedelta(days=2)) is True
        assert rt.rule_fires_on(r, MON + timedelta(days=1)) is False

    def test_monthly_exact_day(self, rt):
        r = _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[15])
        assert rt.rule_fires_on(r, date(2026, 3, 15)) is True
        assert rt.rule_fires_on(r, date(2026, 3, 14)) is False

    def test_monthly_31_falls_back_to_month_end(self, rt):
        """「31 号」规则遇到短月份 → 当月最后一天触发（否则 2 月永远不触发）"""
        r = _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[31])
        assert rt.rule_fires_on(r, date(2026, 2, 28)) is True     # 平年 2 月
        assert rt.rule_fires_on(r, date(2026, 2, 27)) is False
        assert rt.rule_fires_on(r, date(2024, 2, 29)) is True     # 闰年 2 月
        assert rt.rule_fires_on(r, date(2026, 4, 30)) is True     # 30 天月
        assert rt.rule_fires_on(r, date(2026, 4, 29)) is False
        assert rt.rule_fires_on(r, date(2026, 3, 31)) is True

    def test_monthly_30_in_february_also_falls_back(self, rt):
        """30 号在 2 月同样顺延到月末（判据是 d > 当月天数）"""
        r = _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[30])
        assert rt.rule_fires_on(r, date(2026, 2, 28)) is True
        assert rt.rule_fires_on(r, date(2026, 2, 27)) is False

    def test_interval_from_anchor(self, rt):
        r = _rule(rt, kind=rt.KIND_INTERVAL, interval_days=7,
                  anchor_date="2026-03-01")
        assert rt.rule_fires_on(r, date(2026, 3, 1)) is True
        assert rt.rule_fires_on(r, date(2026, 3, 8)) is True
        assert rt.rule_fires_on(r, date(2026, 3, 9)) is False
        # anchor 之前不触发（含 anchor 是未来的脏数据）
        assert rt.rule_fires_on(r, date(2026, 2, 28)) is False

    def test_unknown_kind_never_fires(self, rt):
        assert rt.rule_fires_on({"kind": "nope"}, date(2026, 3, 1)) is False


# ====================================================================
# C 纯函数：最近触发日 / 待生成日（幂等 + 补跑）
# ====================================================================
class TestPendingDay:
    def test_today_after_time_fires_today(self, rt):
        r = _rule(rt)
        assert rt.latest_fired_day(r, _at(date(2026, 3, 4), 9, 0)) == \
            date(2026, 3, 4)
        assert rt.latest_fired_day(r, _at(date(2026, 3, 4), 9, 1)) == \
            date(2026, 3, 4)

    def test_today_before_time_falls_back_to_yesterday(self, rt):
        r = _rule(rt)
        assert rt.latest_fired_day(r, _at(date(2026, 3, 4), 8, 59)) == \
            date(2026, 3, 3)

    def test_bad_time_has_no_fire_day(self, rt):
        assert rt.latest_fired_day({"kind": rt.KIND_DAILY, "time": "x"},
                                  _at(date(2026, 3, 4), 10)) is None

    def test_weekly_looks_back_to_last_occurrence(self, rt):
        r = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[0])       # 每周一 09:00
        # 周三 10:00 → 最近一次是本周一
        wed = MON + timedelta(days=2)
        assert rt.latest_fired_day(r, _at(wed, 10)) == MON
        # 周一 08:00（还没到点）→ 上周一
        assert rt.latest_fired_day(r, _at(MON, 8)) == MON - timedelta(days=7)

    def test_interval_catch_up(self, rt):
        r = _rule(rt, kind=rt.KIND_INTERVAL, interval_days=14,
                  anchor_date="2026-03-01")
        # 03-20 不命中 → 往回找到 03-15
        assert rt.latest_fired_day(r, _at(date(2026, 3, 20), 10)) == \
            date(2026, 3, 15)

    # ---- pending_day：真正决定「要不要生成」 ----
    def test_pending_when_never_fired(self, rt):
        r = _rule(rt)
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) == date(2026, 3, 4)

    def test_idempotent_same_day(self, rt):
        """今天已生成过 → 再 tick 也不再生成（重复启动 / 多次 tick）"""
        r = _rule(rt, last_fired="2026-03-04")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) is None
        assert rt.pending_day(r, _at(date(2026, 3, 4), 23, 59)) is None

    def test_last_fired_in_future_also_idempotent(self, rt):
        """last_fired 比 due 新（用户改过系统时间）→ 也不生成，别倒退刷任务"""
        r = _rule(rt, last_fired="2026-03-10")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) is None

    def test_catch_up_only_latest(self, rt):
        """关三天 → 只补最近一次，不冒出三条（核心行为）"""
        r = _rule(rt, last_fired="2026-03-01")
        pending = rt.pending_day(r, _at(date(2026, 3, 4), 10))
        assert pending == date(2026, 3, 4)      # 只补今天这一次
        # 推进后立刻不再 pending —— 一天一条，不多不少
        r["last_fired"] = pending.isoformat()
        assert rt.pending_day(r, _at(date(2026, 3, 4), 11)) is None

    def test_before_time_not_pending_if_yesterday_done(self, rt):
        r = _rule(rt, last_fired="2026-03-03")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 8, 0)) is None

    def test_disabled_rule_never_pending(self, rt):
        r = _rule(rt, enabled=False)
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) is None

    def test_pending_day_ignores_dirty_last_fired(self, rt):
        """last_fired 是脏值 → 当作「从没生成过」，宁可补一次也别永久哑火"""
        r = _rule(rt)
        r["last_fired"] = "不是日期"
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) == date(2026, 3, 4)

    # ---- 创建时刻水位：不为「规则还没存在」的日子补跑 ----
    def test_new_rule_does_not_backfill_past_day(self, rt):
        """今天 10:00 新建「每天 08:00」→ 不许补出昨天的任务（真实 UX 坑）"""
        r = _rule(rt, created_at="2026-03-04 10:00")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10, 1)) is None

    def test_new_rule_fires_today_only_if_time_not_yet_passed(self, rt):
        """08:00 建的「每天 09:00」→ 今天 09:00 照常触发（水位只挡创建之前）"""
        r = _rule(rt, created_at="2026-03-04 08:00")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 9, 30)) == \
            date(2026, 3, 4)

    def test_new_rule_created_after_its_time_starts_tomorrow(self, rt):
        """10:00 新建「每天 09:00」→ 今天不补，明天 09:00 才是第一次"""
        r = _rule(rt, created_at="2026-03-04 10:00")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 23, 59)) is None
        assert rt.pending_day(r, _at(date(2026, 3, 5), 9, 30)) == \
            date(2026, 3, 5)

    def test_created_at_none_keeps_old_backfill_behaviour(self, rt):
        """没有 created_at（老文件 / 手写文件）→ 不设下限，行为与从前一致"""
        r = _rule(rt)
        assert r["created_at"] == ""
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) == date(2026, 3, 4)

    def test_backfill_stops_at_creation_watermark(self, rt):
        """水位只挡「创建之前」，创建之后错过的仍照补（关程序两天回来）"""
        r = _rule(rt, created_at="2026-03-02 08:00", last_fired="2026-03-02")
        assert rt.pending_day(r, _at(date(2026, 3, 4), 10)) == date(2026, 3, 4)
        # 水位当天但规则时刻早于创建时刻 → 那天也不算错过
        r2 = _rule(rt, created_at="2026-03-04 23:00", last_fired="")
        assert rt.pending_day(r2, _at(date(2026, 3, 5), 1, 0)) is None


# ====================================================================
# D 纯函数：下次触发 / 截止日 / 中文摘要
# ====================================================================
class TestDisplayHelpers:
    def test_next_fire_today_then_tomorrow(self, rt):
        r = _rule(rt)
        assert rt.next_fire_at(r, _at(date(2026, 3, 4), 8)) == \
            _at(date(2026, 3, 4), 9)
        assert rt.next_fire_at(r, _at(date(2026, 3, 4), 10)) == \
            _at(date(2026, 3, 5), 9)

    def test_next_fire_weekly(self, rt):
        r = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[0])
        # 周一 10:00 → 下周一 09:00
        assert rt.next_fire_at(r, _at(MON, 10)) == _at(MON + timedelta(days=7), 9)

    def test_next_fire_interval_beyond_week(self, rt):
        r = _rule(rt, kind=rt.KIND_INTERVAL, interval_days=14,
                  anchor_date="2026-03-01")
        assert rt.next_fire_at(r, _at(date(2026, 3, 20), 10)) == \
            _at(date(2026, 3, 29), 9)

    def test_next_fire_bad_rule_is_none(self, rt):
        assert rt.next_fire_at({"kind": rt.KIND_DAILY, "time": ""},
                               _at(date(2026, 3, 4), 10)) is None

    def test_format_next_wording(self, rt):
        r = _rule(rt)
        assert rt.format_next(r, _at(date(2026, 3, 4), 8)) == "今天 09:00"
        assert rt.format_next(r, _at(date(2026, 3, 4), 10)) == "明天 09:00"
        text = rt.format_next(r, _at(date(2026, 3, 4), 10))
        assert "明天" in text

    def test_format_next_none_is_dash(self, rt):
        assert rt.format_next({"kind": rt.KIND_DAILY, "time": "x"},
                              _at(date(2026, 3, 4), 10)) == "—"

    def test_deadline_offset(self, rt):
        fire = date(2026, 3, 4)
        assert rt.deadline_for(_rule(rt, due_days=-1), fire) == ""
        assert rt.deadline_for(_rule(rt, due_days=0), fire) == "2026-03-04"
        assert rt.deadline_for(_rule(rt, due_days=3), fire) == "2026-03-07"

    def test_deadline_dirty_offset_is_empty(self, rt):
        assert rt.deadline_for({"due_days": "x"}, date(2026, 3, 4)) == ""

    def test_format_rule_texts(self, rt):
        assert rt.format_rule(_rule(rt)) == "每天 09:00"
        assert "周一" in rt.format_rule(
            _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[0]))
        monthly = rt.format_rule(
            _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[1, 15]))
        assert "1 号" in monthly and "15 号" in monthly
        # 含 >28 的号数要提示顺延，否则用户以为 31 号在 2 月会消失
        assert "月末自动顺延" in rt.format_rule(
            _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[31]))
        assert "月末自动顺延" not in monthly
        assert "每 7 天" in rt.format_rule(
            _rule(rt, kind=rt.KIND_INTERVAL, interval_days=7,
                  anchor_date="2026-03-01"))
        # 未知类型不能崩
        assert "未知规则类型" in rt.format_rule({"kind": "nope"})


# ====================================================================
# E validate_rule：一条脏规则都不许放进来
# ====================================================================
class TestValidateRule:
    def test_non_dict_rejected(self, rt):
        assert rt.validate_rule([])[0] is None
        assert rt.validate_rule(None)[0] is None

    def test_title_required(self, rt):
        assert rt.validate_rule({"kind": "daily", "time": "09:00"})[1]
        assert rt.validate_rule({"title": "   ", "kind": "daily",
                                 "time": "09:00"})[0] is None

    def test_title_and_note_truncated(self, rt):
        r, err = rt.validate_rule({"title": "x" * 250, "note": "y" * 900,
                                   "kind": "daily", "time": "09:00"})
        assert err == ""
        assert len(r["title"]) == rt.MAX_TITLE
        assert len(r["note"]) == rt.MAX_NOTE

    def test_kind_must_be_known(self, rt):
        assert rt.validate_rule({"title": "a", "kind": "yearly",
                                 "time": "09:00"})[0] is None

    def test_time_normalized_or_rejected(self, rt):
        r, err = rt.validate_rule({"title": "a", "kind": "daily",
                                   "time": "9:5"})
        assert err == "" and r["time"] == "09:05"
        assert rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "25:00"})[0] is None

    def test_weekly_needs_at_least_one_day(self, rt):
        assert rt.validate_rule({"title": "a", "kind": "weekly",
                                 "time": "09:00", "weekdays": []})[0] is None
        assert rt.validate_rule({"title": "a", "kind": "weekly",
                                 "time": "09:00"})[0] is None

    def test_weekly_days_dedup_and_sorted(self, rt):
        r, err = rt.validate_rule({"title": "a", "kind": "weekly",
                                   "time": "09:00", "weekdays": [4, 0, 4]})
        assert err == "" and r["weekdays"] == [0, 4]

    @pytest.mark.parametrize("bad", [-1, 7, "1", None, True, 1.0])
    def test_weekly_day_bounds(self, rt, bad):
        assert rt.validate_rule({"title": "a", "kind": "weekly",
                                 "time": "09:00",
                                 "weekdays": [bad]})[0] is None

    @pytest.mark.parametrize("bad", [0, 32, "15", None, False])
    def test_monthly_day_bounds(self, rt, bad):
        assert rt.validate_rule({"title": "a", "kind": "monthly",
                                 "time": "09:00",
                                 "monthdays": [bad]})[0] is None

    def test_monthly_needs_day(self, rt):
        assert rt.validate_rule({"title": "a", "kind": "monthly",
                                 "time": "09:00", "monthdays": []})[0] is None

    @pytest.mark.parametrize("bad", [0, -3, 366, "7", True, None])
    def test_interval_bounds(self, rt, bad):
        assert rt.validate_rule({"title": "a", "kind": "interval",
                                 "time": "09:00",
                                 "interval_days": bad})[0] is None

    def test_interval_anchor_defaults_to_today(self, rt):
        r, err = rt.validate_rule({"title": "a", "kind": "interval",
                                   "time": "09:00", "interval_days": 7})
        assert err == ""
        assert r["anchor_date"] == datetime.now().date().isoformat()
        # 脏 anchor → 退回今天，而不是拒掉整条规则
        r2, _ = rt.validate_rule({"title": "a", "kind": "interval",
                                  "time": "09:00", "interval_days": 7,
                                  "anchor_date": "不是日期"})
        assert r2["anchor_date"] == datetime.now().date().isoformat()

    @pytest.mark.parametrize("bad", [-2, 366, "3", True, None])
    def test_due_days_bounds(self, rt, bad):
        assert rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "09:00", "due_days": bad})[0] is None

    def test_due_days_default_is_none_deadline(self, rt):
        r, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "09:00"})
        assert r["due_days"] == -1

    def test_rule_id_generated_or_kept(self, rt):
        r1, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                  "time": "09:00"})
        assert r1["rule_id"]
        r2, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                  "time": "09:00", "rule_id": "fixed1"})
        assert r2["rule_id"] == "fixed1"
        # 两次生成的 id 不能撞
        r3, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                  "time": "09:00"})
        assert r1["rule_id"] != r3["rule_id"]

    def test_last_fired_normalized(self, rt):
        r, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "09:00",
                                 "last_fired": "2026-03-04 10:00"})
        assert r["last_fired"] == "2026-03-04"
        r2, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                  "time": "09:00", "last_fired": "脏"})
        assert r2["last_fired"] == ""

    def test_created_at_kept_but_never_invented(self, rt):
        """创建时刻是补跑水位：给了就留，没给就留空（不许凭空盖「现在」）"""
        r, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "09:00",
                                 "created_at": "2026-03-04 10:00"})
        assert r["created_at"] == "2026-03-04 10:00"
        r2, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                  "time": "09:00"})
        assert r2["created_at"] == ""

    def test_enabled_is_bool(self, rt):
        r, _ = rt.validate_rule({"title": "a", "kind": "daily",
                                 "time": "09:00", "enabled": 0})
        assert r["enabled"] is False


class TestNormalizeRules:
    def test_bad_items_skipped_with_reason(self, rt):
        good = {"title": "好的", "kind": "daily", "time": "09:00"}
        rules, errors = rt.normalize_rules(
            [good, {"title": "", "kind": "daily", "time": "09:00"}, good])
        assert len(rules) == 2
        assert len(errors) == 1 and "第 2 条" in errors[0]

    def test_empty_input(self, rt):
        assert rt.normalize_rules(None) == ([], [])
        assert rt.normalize_rules([]) == ([], [])

    def test_max_rules_capped(self, rt):
        raw = [{"title": f"r{i}", "kind": "daily", "time": "09:00"}
               for i in range(rt.MAX_RULES + 5)]
        rules, errors = rt.normalize_rules(raw)
        assert len(rules) == rt.MAX_RULES
        assert any("上限" in e for e in errors)


# ====================================================================
# F RuleStore：原子写 / 坏文件降级 / 不可持久化
# ====================================================================
class TestRuleStore:
    def test_unavailable_when_no_dir(self, rt):
        st = rt.RuleStore("")
        assert st.available is False
        assert st.path == ""
        assert st.load() == ([], [])
        assert st.save([_rule(rt)]) is False

    def test_roundtrip(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        assert st.available is True
        rule = _rule(rt, title="月报归档")
        assert st.save([rule]) is True
        rules, warnings = st.load()
        assert warnings == []
        assert [r["title"] for r in rules] == ["月报归档"]
        assert rules[0]["rule_id"] == rule["rule_id"]

    def test_payload_shape(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        st.save([_rule(rt)])
        with open(st.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == rt.SCHEMA_VERSION
        assert "updated_at" in data
        assert isinstance(data["rules"], list)

    def test_save_creates_missing_dir(self, rt, tmp_path):
        target = tmp_path / "deep" / "nested"
        st = rt.RuleStore(str(target))
        assert st.save([_rule(rt)]) is True
        assert os.path.isfile(st.path)

    def test_no_tmp_left_behind(self, rt, tmp_path):
        """原子写（临时文件 + replace）不能留下 .tmp 残骸"""
        st = rt.RuleStore(str(tmp_path))
        st.save([_rule(rt)])
        st.save([_rule(rt), _rule(rt, title="第二")])
        left = [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]
        assert left == []

    def test_corrupt_json_degrades_and_keeps_file(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(st.path, "w", encoding="utf-8") as f:
            f.write("{不是 JSON")
        rules, warnings = st.load()
        assert rules == []
        assert warnings and "读取失败" in warnings[0]
        # 原文件必须保留，用户还有机会手工抢救
        assert os.path.isfile(st.path)

    def test_bare_list_compat(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(st.path, "w", encoding="utf-8") as f:
            json.dump([{"title": "裸数组", "kind": "daily", "time": "09:00"}], f)
        rules, warnings = st.load()
        assert warnings == []
        assert [r["title"] for r in rules] == ["裸数组"]

    def test_wrong_toplevel_degrades(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(st.path, "w", encoding="utf-8") as f:
            json.dump(123, f)
        rules, warnings = st.load()
        assert rules == [] and warnings

    def test_partial_bad_rules_load_rest(self, rt, tmp_path):
        """一条坏规则拖不垮全部（用户手改文件写错一条是常态）"""
        st = rt.RuleStore(str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(st.path, "w", encoding="utf-8") as f:
            json.dump({"rules": [
                {"title": "好的", "kind": "daily", "time": "09:00"},
                {"title": "坏的", "kind": "daily", "time": "99:99"},
            ]}, f, ensure_ascii=False)
        rules, warnings = st.load()
        assert [r["title"] for r in rules] == ["好的"]
        assert len(warnings) == 1

    def test_missing_file_is_not_a_warning(self, rt, tmp_path):
        st = rt.RuleStore(str(tmp_path))
        assert st.load() == ([], [])

    def test_save_failure_returns_false(self, rt, tmp_path):
        """目录建不出来（父级是个文件）→ 返回 False，不能抛异常"""
        blocker = tmp_path / "blocker"
        blocker.write_text("我是文件，不是目录", encoding="utf-8")
        st = rt.RuleStore(str(blocker / "sub"))
        assert st.available is True          # 路径非空 → 仍认为「可持久化」
        assert st.save([_rule(rt)]) is False  # 但真写的时候失败


# ====================================================================
# G RuleScheduler：tick 的幂等 / 补跑 / 失败隔离
# ====================================================================
class TestScheduler:
    def _sched(self, rt, tmp_path, ret=101, exc=None):
        store = rt.RuleStore(str(tmp_path))
        sched = rt.RuleScheduler(store, logger=_Log())
        sched.bind(_Ctx(ret, exc, str(tmp_path)))
        return sched, store

    def test_tick_generates_and_persists(self, rt, tmp_path):
        sched, store = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        created = sched.tick(_at(date(2026, 3, 4), 10))
        assert len(created) == 1
        rule, task_id, day = created[0]
        assert task_id == "101"
        assert day == "2026-03-04"
        # last_fired 必须推进并落盘，否则重启后会重复生成
        assert sched.rules()[0]["last_fired"] == "2026-03-04"
        rules, _ = store.load()
        assert rules[0]["last_fired"] == "2026-03-04"

    def test_tick_passes_note_and_deadline(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt, note="附：带上上周数据", due_days=1)])
        sched.tick(_at(date(2026, 3, 4), 10))
        assert sched._ctx.write.calls == [
            ("写周报", "附：带上上周数据", "2026-03-05")]

    def test_second_tick_is_noop(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        assert len(sched.tick(_at(date(2026, 3, 4), 10))) == 1
        assert sched.tick(_at(date(2026, 3, 4), 10, 30)) == []
        assert sched.tick(_at(date(2026, 3, 4), 23, 59)) == []
        assert len(sched._ctx.write.calls) == 1        # 一天只写一次

    def test_next_day_fires_again(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        sched.tick(_at(date(2026, 3, 4), 10))
        sched.tick(_at(date(2026, 3, 5), 10))
        assert len(sched._ctx.write.calls) == 2

    def test_check_now_equals_tick(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        assert len(sched.check_now()) == 1

    def test_disabled_rule_skipped(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt, enabled=False)])
        assert sched.tick(_at(date(2026, 3, 4), 10)) == []
        assert sched._ctx.write.calls == []

    def test_write_rejected_marks_failed_and_stops_retrying(self, rt, tmp_path):
        """未授权（add_task 返回 0）→ 不推进 last_fired，但不许每 60s 重试刷屏"""
        sched, _ = self._sched(rt, tmp_path, ret=0)
        sched.set_rules([_rule(rt)])
        events = []
        sched.failed.connect(events.append)
        assert sched.tick(_at(date(2026, 3, 4), 10)) == []
        assert len(events) == 1 and "生成失败" in events[0]
        assert sched.rules()[0]["last_fired"] == ""     # 没假装成功
        assert len(sched._ctx.write.calls) == 1
        sched.tick(_at(date(2026, 3, 4), 10, 30))
        assert len(sched._ctx.write.calls) == 1         # 当天不再重试

    def test_write_exception_isolated(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path, exc=RuntimeError("桥炸了"))
        sched.set_rules([_rule(rt)])
        assert sched.tick(_at(date(2026, 3, 4), 10)) == []
        assert sched.rules()[0]["last_fired"] == ""
        assert any("写入抛异常" in w for w in sched._logger.warnings)

    def test_ctx_none_reports_failure(self, rt, tmp_path):
        store = rt.RuleStore(str(tmp_path))
        sched = rt.RuleScheduler(store, logger=_Log())
        sched.set_rules([_rule(rt)])
        assert sched.tick(_at(date(2026, 3, 4), 10)) == []
        assert any("上下文不可用" in w for w in sched._logger.warnings)

    def test_dirty_rule_does_not_break_others(self, rt, tmp_path):
        """一条算不出来的脏规则不能拖垮同批其他规则"""
        sched, _ = self._sched(rt, tmp_path)
        dirty = {"rule_id": "dirty", "title": "脏", "kind": rt.KIND_INTERVAL,
                 "time": "09:00", "interval_days": "abc", "enabled": True}
        sched.set_rules([dirty, _rule(rt, title="好的")])
        created = sched.tick(_at(date(2026, 3, 4), 10))
        assert [c[0]["title"] for c in created] == ["好的"]
        assert any("规则计算失败" in w for w in sched._logger.warnings)

    def test_created_emits_fired_signal(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        got = []
        sched.fired.connect(got.append)
        sched.tick(_at(date(2026, 3, 4), 10))
        assert len(got) == 1 and "写周报" in got[0]

    def test_no_write_when_nothing_pending(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt, last_fired="2026-03-04")])
        assert sched.tick(_at(date(2026, 3, 4), 10)) == []
        assert sched._ctx.write.calls == []

    # ---- 规则读写 / 生命周期 ----
    def test_rules_returns_copy(self, rt, tmp_path):
        sched, _ = self._sched(rt, tmp_path)
        sched.set_rules([_rule(rt)])
        sched.rules().append({"title": "偷偷加的"})
        assert len(sched.rules()) == 1

    def test_load_keeps_memory_rules_when_not_persistable(self, rt):
        """数据目录不可用 → load() 不许清空内存里的规则（会吃掉用户刚建的）"""
        sched = rt.RuleScheduler(rt.RuleStore(""), logger=_Log())
        sched.set_rules([_rule(rt)])
        assert sched.store_available() is False
        assert sched.load() == []
        assert len(sched.rules()) == 1

    def test_load_reads_disk_when_available(self, rt, tmp_path):
        store = rt.RuleStore(str(tmp_path))
        store.save([_rule(rt, title="磁盘上的")])
        sched = rt.RuleScheduler(store, logger=_Log())
        sched.load()
        assert [r["title"] for r in sched.rules()] == ["磁盘上的"]

    def test_start_stop(self, rt, tmp_path, qapp):
        sched, _ = self._sched(rt, tmp_path)
        assert sched.is_running() is False
        sched.start()
        assert sched.is_running() is True
        sched.start()                       # 重复 start 不叠加
        sched.stop()
        assert sched.is_running() is False
        sched.stop()                        # 重复 stop 不抛


# ====================================================================
# H 插件契约：manifest ↔ plugin.py ↔ loader 三方一致
# ====================================================================
class TestPluginContract:
    @pytest.fixture(scope="class")
    def manifest(self):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_manifest_passes_real_validator(self, manifest):
        from src.plugin_loader import validate_manifest
        norm, err = validate_manifest(manifest)
        assert err == "", err
        assert norm["id"] == "recurring-tasks"

    def test_declares_write_only(self, manifest):
        """只增不改：能力声明必须是 write，别顺手要 manage（那是改删权限）"""
        assert manifest["capabilities"] == ["write"]

    def test_page_declared(self, manifest, rt):
        assert manifest["page"]["title"].strip()
        assert rt.PLUGIN_ID == manifest["id"]
        # 页面 key 必须带 plugin: 前缀，否则 show_plugin_page 永远返回 False
        assert rt.PAGE_KEY == f"plugin:{manifest['id']}"

    def test_action_ids_match(self, manifest, rt):
        declared = {a["id"] for a in manifest["actions"]}
        assert declared == {f"{rt.PLUGIN_ID}.manage",
                            f"{rt.PLUGIN_ID}.check-now"}
        actions = rt.RecurringTasksPlugin().create_actions(None)
        assert {a.id for a in actions} == declared

    def test_hotkeys_are_valid(self, manifest, rt):
        from src.plugin_loader import is_valid_hotkey
        for a in manifest["actions"]:
            hk = a.get("hotkey")
            if hk:
                assert is_valid_hotkey(hk), f"{a['id']} 的热键非法：{hk}"

    def test_plugin_identity(self, rt, manifest):
        p = rt.RecurringTasksPlugin()
        assert (p.id, p.name) == ("recurring-tasks", "周期任务")
        assert p.version == manifest["version"]

    def test_hooks_are_callable(self, rt):
        p = rt.RecurringTasksPlugin()
        for name in ("on_enable", "on_disable", "create_page"):
            assert callable(getattr(p, name))

    def test_no_forbidden_imports(self):
        """依赖白名单：只许 PyQt6 + 标准库（ssl/socket/requests 一律不许）"""
        with open(PLUGIN_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        for bad in ("import socket", "import ssl", "import requests",
                    "import urllib", "import http.client",
                    "import asyncio", "subprocess"):
            assert bad not in src, f"插件不得使用 {bad}"


# ====================================================================
# I 插件级单例：ensure_scheduler 幂等（rescan 不能并存多个调度器）
# ====================================================================
class TestEnsureSchedulerIdempotent:
    def test_same_instance_rebound(self, rt, tmp_path):
        saved = dict(rt._STATE)
        try:
            ctx1 = _Ctx(data_dir=str(tmp_path))
            ctx2 = _Ctx(data_dir=str(tmp_path))
            s1 = rt.ensure_scheduler(ctx1)
            s2 = rt.ensure_scheduler(ctx2)
            assert s1 is s2                      # 绝不新建第二个 QTimer
            assert s1._ctx is ctx2               # 但上下文要换成最新的
        finally:
            rt._STATE.update(saved)


# ====================================================================
# J 每 N 周规则（v1.1.0，R1）
# ====================================================================
class TestNWeekly:
    def test_fires_on_selected_weekdays_every_n_weeks(self, rt):
        # 锚点 2026-09-28（周一）为第 0 周；每 2 周周一触发
        rule = {"kind": "nweekly", "title": "复盘", "time": "09:00",
                "weekdays": [0], "interval_weeks": 2,
                "anchor_date": "2026-09-28"}
        assert rt.rule_fires_on(rule, date(2026, 9, 28)) is True    # 第 0 周
        assert rt.rule_fires_on(rule, date(2026, 10, 5)) is False   # 第 1 周
        assert rt.rule_fires_on(rule, date(2026, 10, 12)) is True   # 第 2 周
        assert rt.rule_fires_on(rule, date(2026, 10, 13)) is False  # 非周一

    def test_week_index_aligned_to_monday(self, rt):
        # 锚点在周三：同周的周五与**下周一**不能算同一个"周"
        rule = {"kind": "nweekly", "title": "x", "time": "09:00",
                "weekdays": [0, 2], "interval_weeks": 2,
                "anchor_date": "2026-09-30"}       # 周三
        assert rt.rule_fires_on(rule, date(2026, 10, 2)) is False  # 周五=第0周非周三
        # 下周一（10-06）是第 1 周 → 不触发；第 2 周周一（10-13 的下周一=10-12? 注意锚点周三，第0周=09-28~10-04）
        assert rt.rule_fires_on(rule, date(2026, 10, 5)) is False   # 第1周周一
        assert rt.rule_fires_on(rule, date(2026, 10, 12)) is True   # 第2周周一
        assert rt.rule_fires_on(rule, date(2026, 10, 14)) is True   # 第2周周三

    def test_before_anchor_never_fires(self, rt):
        rule = {"kind": "nweekly", "title": "x", "time": "09:00",
                "weekdays": [0], "interval_weeks": 1,
                "anchor_date": "2026-09-28"}
        assert rt.rule_fires_on(rule, date(2026, 9, 21)) is False

    def test_validate_ok_and_defaults(self, rt):
        raw = {"kind": "nweekly", "title": "隔周复盘", "time": "10:00",
               "weekdays": [0, 4], "interval_weeks": 2}
        rule, err = rt.validate_rule(raw)
        assert rule is not None and err == ""
        assert rule["weekdays"] == [0, 4]
        assert rule["interval_weeks"] == 2
        # 锚点自动 = 创建当周的周一
        anchor = rt.as_date(rule["anchor_date"])
        assert anchor.weekday() == 0

    def test_validate_rejects_bad_weeks_and_days(self, rt):
        base = {"kind": "nweekly", "title": "x", "time": "09:00",
                "weekdays": [0], "interval_weeks": 2}
        rule, err = rt.validate_rule(dict(base, interval_weeks=0))
        assert rule is None and "1–52" in err
        rule, err = rt.validate_rule(dict(base, interval_weeks=53))
        assert rule is None and "1–52" in err
        rule, err = rt.validate_rule(dict(base, weekdays=[]))
        assert rule is None and "至少" in err

    def test_editing_keeps_anchor_no_drift(self, rt):
        raw = {"kind": "nweekly", "title": "x", "time": "09:00",
               "weekdays": [0], "interval_weeks": 2,
               "anchor_date": "2026-06-01"}
        rule, _ = rt.validate_rule(raw)
        assert rule["anchor_date"] == "2026-06-01"   # 显式锚点不被改写

    def test_format_rule(self, rt):
        rule = {"kind": "nweekly", "time": "09:00", "weekdays": [0, 2],
                "interval_weeks": 2}
        text = rt.format_rule(rule)
        assert "每 2 周" in text and "周一" in text and "周三" in text

    def test_next_fire_skips_off_weeks(self, rt):
        now = datetime(2026, 9, 28, 10, 0)           # 周一 10:00（09:00 已过）
        rule = {"kind": "nweekly", "title": "x", "time": "09:00",
                "weekdays": [0], "interval_weeks": 2,
                "anchor_date": "2026-09-28", "enabled": True}
        nxt = rt.next_fire_at(rule, now)
        assert nxt == datetime(2026, 10, 12, 9, 0)   # 第 2 周周一

    def test_pending_day_catch_up(self, rt):
        rule = {"kind": "nweekly", "title": "x", "time": "09:00",
                "weekdays": [0], "interval_weeks": 2,
                "anchor_date": "2026-09-28", "enabled": True,
                "last_fired": ""}
        # 昨天周一（第 2 周）09:00 已到点而没生成 → 补最近一次 = 昨天
        now2 = datetime(2026, 10, 13, 10, 0)
        assert rt.pending_day(rule, now2) == date(2026, 10, 12)

    def test_manifest_kinds_description(self, rt):
        assert "nweekly" in rt.KINDS
        assert dict(rt.KIND_LABELS)["nweekly"] == "每 N 周（隔周…）"


