# -*- coding: utf-8 -*-
"""周期任务 v1.2.0 增强（R2 周末顺延 + R3 导出导入 + R4 上次生成记录）。

钉住五条底线：
1. R2 老规则零变化：shift_weekend 缺省时 rule_fires_shifted 与
   rule_fires_on 在五种 kind 下逐日等价；
2. R2 顺延语义：周六/日的触发日 → 下周一；双周末日顺延到同一周一
   只生成一条（幂等合并）；
3. R3 导入合并：逐条校验、rule_id 撞车跳过（现役优先）、坏条目计数、
   MAX_RULES 上限、原列表不被修改；
4. R4 生成记录：_fire 成功后 last_task 落档，validate_rule 编辑透传；
5. validate_rule shift_weekend 字段缺省 False / 显式保留。
"""

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PLUGIN_PATH = os.path.join(ROOT, "plugins", "recurring-tasks", "plugin.py")
_MOD = "fp_test_recurring_enhance"


@pytest.fixture(scope="module")
def rt():
    spec = importlib.util.spec_from_file_location(_MOD, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD] = mod
    spec.loader.exec_module(mod)
    return mod


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


class _Writer:
    def __init__(self, ret=101):
        self.ret = ret
        self.calls = []

    def add_task(self, title, note="", deadline=""):
        self.calls.append((title, note, deadline))
        return self.ret


class _Ctx:
    def __init__(self, ret=101, data_dir=""):
        self.write = _Writer(ret)
        self.logger = _Log()
        self.data_dir = data_dir

    def show_toast(self, text, ms=2800, **kwargs):
        pass

    def parent_window(self):
        return None


def _rule(rt, **over):
    raw = {"title": "写周报", "kind": rt.KIND_DAILY, "time": "09:00",
           "due_days": -1, "enabled": True}
    raw.update(over)
    rule, err = rt.validate_rule(raw)
    assert rule is not None, err
    return rule


# ---------------- R2：周末顺延 ----------------

class TestShiftWeekend:
    def test_default_off_matches_raw_predicate(self, rt):
        """shift 缺省（老规则）→ 新旧谓词 400 天逐日等价（零变化）"""
        start = date(2026, 1, 1)
        rules = [
            _rule(rt, kind=rt.KIND_DAILY),
            _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[0, 5, 6]),
            _rule(rt, kind=rt.KIND_MONTHLY, monthdays=[1, 15, 31]),
            _rule(rt, kind=rt.KIND_INTERVAL, interval_days=14,
                  anchor_date="2026-01-03"),
            _rule(rt, kind=rt.KIND_NWEEKLY, interval_weeks=2,
                  weekdays=[1], anchor_date="2026-01-05"),
        ]
        for rule in rules:
            for i in range(400):
                d = start + timedelta(days=i)
                assert rt.rule_fires_shifted(rule, d) == \
                    rt.rule_fires_on(rule, d), (rule["kind"], d)

    def test_saturday_moves_to_next_monday(self, rt):
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5],
                     shift_weekend=True)
        sat = date(2026, 1, 3)            # 2026-01-03 是周六
        assert sat.weekday() == 5
        mon = date(2026, 1, 5)
        assert not rt.rule_fires_shifted(rule, sat)
        assert rt.rule_fires_shifted(rule, mon)

    def test_sunday_moves_to_next_monday(self, rt):
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[6],
                     shift_weekend=True)
        sun = date(2026, 1, 4)
        mon = date(2026, 1, 5)
        assert not rt.rule_fires_shifted(rule, sun)
        assert rt.rule_fires_shifted(rule, mon)

    def test_double_weekend_collision_merges(self, rt, tmp_path):
        """周六+周日双触发都顺延到同一周一 → 只生成一条任务"""
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5, 6],
                     time="09:00", shift_weekend=True)
        mon = date(2026, 1, 5)            # 补跑日=下周一
        now = datetime(2026, 1, 5, 10, 0)
        due = rt.pending_day(rule, now)
        assert due == mon
        store = rt.RuleStore(str(tmp_path))
        sched = rt.RuleScheduler(store, logger=_Log())
        sched.bind(_Ctx(101))
        sched.set_rules([rule])
        created = sched.tick(now)
        assert len(created) == 1
        # 再 tick：last_fired 已 >= due → 不再生成（撞车合并为一条）
        assert sched.tick(now) == []

    def test_latest_fired_day_uses_shifted(self, rt):
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5],
                     time="09:00", shift_weekend=True,
                     created_at="2025-12-01 08:00")
        now = datetime(2026, 1, 5, 10, 0)   # 周一 10:00
        assert rt.latest_fired_day(rule, now) == date(2026, 1, 5)

    def test_next_fire_at_uses_shifted(self, rt):
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5],
                     time="09:00", shift_weekend=True)
        now = datetime(2026, 1, 3, 12, 0)   # 周六（已过点）
        nxt = rt.next_fire_at(rule, now)
        assert nxt.date() == date(2026, 1, 5)   # 下周一

    def test_format_rule_suffix(self, rt):
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5],
                     shift_weekend=True)
        assert "周末顺延到下周一" in rt.format_rule(rule)
        rule2 = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[5])
        assert "周末顺延" not in rt.format_rule(rule2)

    def test_validate_default_false(self, rt):
        rule = _rule(rt, kind=rt.KIND_DAILY)
        assert rule["shift_weekend"] is False

    def test_deadline_uses_shifted_day(self, rt, tmp_path):
        """顺延生成的任务截止日 = 顺延后的周一 + 偏移"""
        rule = _rule(rt, kind=rt.KIND_WEEKLY, weekdays=[6],
                     time="09:00", due_days=1, shift_weekend=True)
        store = rt.RuleStore(str(tmp_path))
        sched = rt.RuleScheduler(store, logger=_Log())
        ctx = _Ctx(101)
        sched.bind(ctx)
        sched.set_rules([rule])
        now = datetime(2026, 1, 5, 10, 0)
        created = sched.tick(now)
        assert len(created) == 1
        # 触发日=周一 01-05 → 截止 = 01-06
        assert ctx.write.calls[0][2] == "2026-01-06"


# ---------------- R3：导出 / 导入合并 ----------------

class TestImportRules:
    def test_dict_and_bare_list(self, rt):
        existing = [_rule(rt)]
        r2 = _rule(rt, title="另一条", kind=rt.KIND_WEEKLY, weekdays=[1])
        for payload in ({"rules": [r2]}, [r2]):
            merged, added, skipped, bad = rt.import_rules(existing, payload)
            assert added == 1 and skipped == 0 and bad == 0
            assert len(merged) == 2

    def test_none_and_garbage(self, rt):
        existing = [_rule(rt)]
        for payload in (None, "x", 42, {}, {"rules": None}):
            merged, added, skipped, bad = rt.import_rules(existing, payload)
            assert (added, skipped, bad) == (0, 0, 0)
            assert merged == existing

    def test_duplicate_id_skipped_existing_wins(self, rt):
        old = _rule(rt)                    # _rule 每次生成新 uuid，这里同 id 复制
        old["title"] = "现役版本"
        dup = dict(old)
        dup["title"] = "导入版本"
        merged, added, skipped, bad = rt.import_rules([old], [dup])
        assert (added, skipped, bad) == (0, 1, 0)
        assert merged[0]["title"] == "现役版本"   # 现役优先，不被覆盖

    def test_bad_entries_counted(self, rt):
        bad_item = {"title": "", "kind": rt.KIND_DAILY, "time": "09:00"}
        merged, added, skipped, bad = rt.import_rules(
            [], [bad_item, "junk", _rule(rt)])
        assert (added, skipped, bad) == (1, 0, 2)

    def test_existing_not_mutated(self, rt):
        existing = [_rule(rt)]
        snapshot = json.dumps(existing, ensure_ascii=False)
        rt.import_rules(existing, [_rule(rt, title="新")])
        assert json.dumps(existing, ensure_ascii=False) == snapshot

    def test_cap_max_rules(self, rt):
        existing = []
        raws = [{"title": f"t{i}", "kind": rt.KIND_DAILY, "time": "09:00"}
                for i in range(rt.MAX_RULES + 10)]
        merged, added, _s, _b = rt.import_rules(existing, raws)
        assert len(merged) == rt.MAX_RULES


# ---------------- R4：上次生成记录 ----------------

class TestLastTask:
    def test_fire_records_last_task(self, rt, tmp_path):
        rule = _rule(rt, title="写周报")
        store = rt.RuleStore(str(tmp_path))
        sched = rt.RuleScheduler(store, logger=_Log())
        sched.bind(_Ctx(101))
        sched.set_rules([rule])
        created = sched.tick(datetime(2026, 1, 5, 10, 0))
        assert len(created) == 1
        lt = rule["last_task"]
        assert lt["title"] == "写周报"
        assert lt["at"] == "2026-01-05 10:00"   # 跟随 tick 的时钟参数

    def test_validate_passthrough_and_sanitize(self, rt):
        rule, err = rt.validate_rule({
            "title": "t", "kind": rt.KIND_DAILY, "time": "09:00",
            "last_task": {"title": "旧任务" * 200, "at": "2026-01-01 08:00"}})
        assert len(rule["last_task"]["title"]) == rt.MAX_TITLE   # 截 200 字符
        assert rule["last_task"]["title"].startswith("旧任务")
        assert rule["last_task"]["at"] == "2026-01-01 08:00"

    def test_validate_drops_dirty_last_task(self, rt):
        for bad in ("x", 42, ["list"]):
            rule, _ = rt.validate_rule({
                "title": "t", "kind": rt.KIND_DAILY, "time": "09:00",
                "last_task": bad})
            assert "last_task" not in rule

    def test_rule_file_roundtrip_keeps_last_task(self, rt, tmp_path):
        store = rt.RuleStore(str(tmp_path))
        rule = _rule(rt)
        rule["last_task"] = {"title": "写周报", "at": "2026-01-05 10:00"}
        assert store.save([rule])
        rules, warnings = store.load()
        assert warnings == []
        assert rules[0]["last_task"]["at"] == "2026-01-05 10:00"
        assert rules[0]["shift_weekend"] is False
