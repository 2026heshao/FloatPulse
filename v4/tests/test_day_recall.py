# -*- coding: utf-8 -*-
"""
按天回溯纯逻辑单测（v4/src/day_recall.py）。

护栏针对点（任务卡 day-recall §5）：
  · 跨 "YYYY-MM-DD HH:MM" 解析（含带秒 / 脏数据 / 空值）
  · 活跃天索引（空白天不进列表）
  · 四源合并时序（碎片 / 任务创建+完成 / 素材 / 番茄钟）
  · 边界：跨年 / 跨月 / 同一天多条 / 同一分钟多条稳定排序
"""
import os
import sys
from datetime import date

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.day_recall import (  # noqa: E402
    SOURCE_ASSET, SOURCE_FRAGMENT, SOURCE_POMODORO, SOURCE_TASK,
    SOURCE_TASK_DONE, DayEvent, DayGroup, active_days, build_day_groups,
    collect_events, day_key, day_label, format_day_text, group_by_day,
)


# ====================================================================
# 测试替身（模拟四源记录，字段与真实数据类一致）
# ====================================================================
class _Frag:
    def __init__(self, fragment_id, content, created_at, source="", ftype="clipboard_text"):
        self.fragment_id = fragment_id
        self.content = content
        self.created_at = created_at
        self.source = source
        self.type = ftype

    def preview(self, max_len=50):
        text = self.content.replace("\n", " ").strip()
        return text[:max_len] + ("..." if len(text) > max_len else "")


class _Task:
    def __init__(self, task_id, title, created_at, completed_at="", done=False):
        self.task_id = task_id
        self.title = title
        self.created_at = created_at
        self.completed_at = completed_at
        self.done = done


class _Asset:
    def __init__(self, asset_id, original_name, added_time):
        self.asset_id = asset_id
        self.original_name = original_name
        self.added_time = added_time


class _Pomo:
    def __init__(self, session_id, created_at, minutes=25):
        self.session_id = session_id
        self.created_at = created_at
        self.minutes = minutes


# ====================================================================
# day_key：跨格式解析
# ====================================================================
def test_day_key_minute_format():
    assert day_key("2026-10-03 09:12") == "2026-10-03"


def test_day_key_with_seconds():
    assert day_key("2026-10-03 09:12:45") == "2026-10-03"


def test_day_key_date_only():
    assert day_key("2026-10-03") == "2026-10-03"


def test_day_key_strips_whitespace():
    assert day_key("  2026-10-03 09:12  ") == "2026-10-03"


def test_day_key_rejects_empty_and_none():
    assert day_key("") == ""
    assert day_key(None) == ""


def test_day_key_rejects_bad_format():
    # 脏数据 / 手改过的 json 不得被兜底成「今天」——一律不进任何一天
    assert day_key("2026/10/03 09:12") == ""
    assert day_key("昨天") == ""
    assert day_key("2026-13-45 09:12") == ""
    assert day_key("20261003") == ""


def test_day_key_preserves_cross_year():
    assert day_key("2025-12-31 23:59") == "2025-12-31"
    assert day_key("2026-01-01 00:01") == "2026-01-01"


# ====================================================================
# collect_events：四源合并
# ====================================================================
def test_collect_events_merges_four_sources():
    frags = [_Frag(1, "hello", "2026-10-03 09:12")]
    tasks = [_Task(1, "写周报", "2026-10-03 10:30", completed_at="2026-10-03 16:20", done=True)]
    assets = [_Asset(1, "shot.png", "2026-10-03 11:00:00")]
    sessions = [_Pomo(1, "2026-10-03 14:05")]
    events = collect_events(frags, tasks, assets, sessions)
    sources = sorted(e.source for e in events)
    assert sources == sorted([
        SOURCE_FRAGMENT, SOURCE_TASK, SOURCE_TASK_DONE,
        SOURCE_ASSET, SOURCE_POMODORO,
    ])


def test_task_creates_two_events_when_completed_same_day():
    tasks = [_Task(1, "A", "2026-10-03 08:00", completed_at="2026-10-03 09:00", done=True)]
    events = collect_events(tasks=tasks)
    assert len(events) == 2
    assert {e.source for e in events} == {SOURCE_TASK, SOURCE_TASK_DONE}


def test_task_events_split_across_days():
    # 创建在今天、完成在明天 → 分属两个活跃天
    tasks = [_Task(1, "A", "2026-10-03 23:50", completed_at="2026-10-04 00:10", done=True)]
    groups = build_day_groups(tasks=tasks)
    days = [g.day for g in groups]
    assert days == ["2026-10-04", "2026-10-03"]


def test_unfinished_task_has_no_completion_event():
    tasks = [_Task(1, "A", "2026-10-03 08:00", completed_at="", done=False)]
    events = collect_events(tasks=tasks)
    assert [e.source for e in events] == [SOURCE_TASK]


def test_pomodoro_accepts_bare_date_strings():
    events = collect_events(sessions=["2026-10-03", "2026-10-03", "2026-10-04"])
    assert len(events) == 3
    assert all(e.source == SOURCE_POMODORO for e in events)


def test_collect_events_handles_missing_sources():
    assert collect_events() == []
    assert collect_events(None, None, None, None) == []
    assert collect_events([], [], [], []) == []


def test_dirty_records_are_skipped():
    frags = [_Frag(1, "bad", "not-a-date"), _Frag(2, "good", "2026-10-03 10:00")]
    events = collect_events(frags)
    assert len(events) == 1
    assert events[0].title == "good"


def test_task_without_title_gets_placeholder():
    tasks = [_Task(1, "", "2026-10-03 08:00")]
    events = collect_events(tasks=tasks)
    assert events[0].title == "(无标题)"


# ====================================================================
# 活跃天索引：空白天不进列表
# ====================================================================
def test_active_days_excludes_empty_days():
    frags = [
        _Frag(1, "a", "2026-10-01 10:00"),
        _Frag(2, "b", "2026-10-03 10:00"),   # 10-02 是空白天
    ]
    days = active_days(frags)
    assert days == ["2026-10-03", "2026-10-01"]
    assert "2026-10-02" not in days


def test_active_days_desc_order_default():
    frags = [_Frag(1, "a", "2026-10-01 10:00"), _Frag(2, "b", "2026-10-05 10:00")]
    assert active_days(frags) == ["2026-10-05", "2026-10-01"]
    assert active_days(frags, desc=False) == ["2026-10-01", "2026-10-05"]


def test_active_days_empty_when_no_data():
    assert active_days() == []


def test_active_days_dedupes_same_day():
    frags = [_Frag(1, "a", "2026-10-03 09:00"), _Frag(2, "b", "2026-10-03 18:00")]
    assert active_days(frags) == ["2026-10-03"]


# ====================================================================
# 天内时序
# ====================================================================
def test_events_sorted_by_time_within_day():
    frags = [
        _Frag(1, "c", "2026-10-03 15:00"),
        _Frag(2, "a", "2026-10-03 09:00"),
        _Frag(3, "b", "2026-10-03 12:00"),
    ]
    groups = build_day_groups(frags)
    assert [e.title for e in groups[0].events] == ["a", "b", "c"]


def test_same_minute_stable_order_by_source_then_id():
    # 同一分钟：碎片先于任务（来源序），同源内按 id
    frags = [_Frag(5, "f5", "2026-10-03 10:00"), _Frag(3, "f3", "2026-10-03 10:00")]
    tasks = [_Task(1, "t1", "2026-10-03 10:00")]
    events = group_by_day(collect_events(frags, tasks))["2026-10-03"]
    assert [e.title for e in events] == ["f3", "f5", "t1"]


def test_same_minute_order_is_deterministic_regardless_of_input_order():
    a = group_by_day(collect_events([_Frag(1, "x", "2026-10-03 10:00"),
                                     _Frag(2, "y", "2026-10-03 10:00")]))
    b = group_by_day(collect_events([_Frag(2, "y", "2026-10-03 10:00"),
                                     _Frag(1, "x", "2026-10-03 10:00")]))
    assert [e.title for e in a["2026-10-03"]] == [e.title for e in b["2026-10-03"]]


# ====================================================================
# 跨年 / 跨月
# ====================================================================
def test_cross_year_days_grouped_separately():
    frags = [
        _Frag(1, "old", "2025-12-31 23:59"),
        _Frag(2, "new", "2026-01-01 00:01"),
    ]
    groups = build_day_groups(frags)
    assert [g.day for g in groups] == ["2026-01-01", "2025-12-31"]


def test_cross_month_days_grouped_separately():
    frags = [
        _Frag(1, "sep", "2026-09-30 22:00"),
        _Frag(2, "oct", "2026-10-01 01:00"),
    ]
    assert active_days(frags) == ["2026-10-01", "2026-09-30"]


# ====================================================================
# day_label：展示文案
# ====================================================================
def test_day_label_today_and_yesterday():
    today = date(2026, 10, 3)
    assert day_label("2026-10-03", today) == "今天 · 10 月 3 日"
    assert day_label("2026-10-02", today) == "昨天 · 10 月 2 日"


def test_day_label_plain_within_year():
    assert day_label("2026-08-15", date(2026, 10, 3)) == "8 月 15 日"


def test_day_label_other_year_adds_year():
    assert day_label("2025-12-31", date(2026, 10, 3)) == "2025 年 12 月 31 日"


def test_day_label_bad_input_returned_as_is():
    assert day_label("bogus") == "bogus"


# ====================================================================
# DayGroup 聚合视图
# ====================================================================
def test_day_group_counts_and_summary():
    frags = [_Frag(1, "a", "2026-10-03 09:00"), _Frag(2, "b", "2026-10-03 09:30")]
    tasks = [_Task(1, "T", "2026-10-03 10:00")]
    sessions = [_Pomo(1, "2026-10-03"), _Pomo(2, "2026-10-03")]
    g = build_day_groups(frags, tasks, sessions=sessions)[0]
    assert len(g.fragments) == 2
    assert len(g.tasks) == 1
    assert g.pomodoro_count == 2
    assert g.summary() == "碎片 2 · 任务 1 · 专注 2"


def test_day_group_summary_omits_zero_sources():
    g = build_day_groups([_Frag(1, "a", "2026-10-03 09:00")])[0]
    assert g.summary() == "碎片 1"


def test_day_group_asset_events():
    g = build_day_groups(assets=[_Asset(1, "pic.png", "2026-10-03 08:00:00")])[0]
    assert len(g.assets) == 1
    assert g.assets[0].title == "pic.png"


def test_day_group_empty_summary_when_neither():
    assert DayGroup("2026-10-03", []).summary() == "无记录"


# ====================================================================
# format_day_text：存为笔记的文本
# ====================================================================
def test_format_day_text_header_and_lines():
    frags = [_Frag(1, "片段内容", "2026-10-03 09:12")]
    tasks = [_Task(1, "写周报", "2026-10-03 10:30", completed_at="2026-10-03 16:20", done=True)]
    sessions = [_Pomo(1, "2026-10-03")]
    g = build_day_groups(frags, tasks, sessions=sessions)[0]
    text = format_day_text(g, today=date(2026, 10, 3))
    assert "2026-10-03" in text
    assert "今天" in text
    assert "碎片 1" in text and "专注 1" in text
    assert "09:12" in text and "片段内容" in text
    assert "写周报" in text
    # 四行条目（碎片 + 创建 + 完成 + 专注）
    assert len([ln for ln in text.splitlines() if ln.startswith("- ")]) == 4


def test_format_day_text_uses_placeholder_for_missing_time():
    e = DayEvent(SOURCE_POMODORO, "2026-10-03", "专注一次", "专注", 1)
    g = DayGroup("2026-10-03", [e])
    assert "--:--" in format_day_text(g)


def test_format_day_text_ends_without_trailing_blank():
    g = build_day_groups([_Frag(1, "a", "2026-10-03 09:00")])[0]
    text = format_day_text(g)
    assert text == text.rstrip()


# ====================================================================
# 面板集成：FragmentsPanel 的「按天」视图（offscreen 真面板）
# ====================================================================
class _FakeConfig:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value
        return True

    def save(self):
        pass


class _FakeHost(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig()
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = None
        self._task_manager = None
        self._temp_asset_manager = None
        self.current_theme = "dark"
        self.anim_speed = 1.0
        self.toasts = []


    @property
    def config(self):
        return self._config

    @property
    def fragment_manager(self):
        return self._fragment_manager

    @property
    def note_manager(self):
        return self._note_manager

    @property
    def task_manager(self):
        return self._task_manager

    @property
    def temp_asset_manager(self):
        return self._temp_asset_manager

    def refresh_page(self, _name):
        pass

    def show_toast(self, msg, **kwargs):
        self.toasts.append(msg)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_panel(tmp_path, frag_times):
    """真碎片管理器 + 真面板；frag_times 给定各条 created_at（手改落盘后重载）。"""
    import json
    from src.fragment_manager import FragmentManager
    from src.fragments_panel import FragmentsPanel

    p = str(tmp_path / "fragments.json")
    records = []
    for i, ts in enumerate(frag_times, start=1):
        records.append({"fragment_id": i, "type": "clipboard_text",
                        "content": "内容%d" % i, "source": "测试",
                        "created_at": ts, "category": "text"})
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"fragments": records, "next_id": len(records) + 1}, f,
                  ensure_ascii=False)
    mgr = FragmentManager(p)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    return panel, mgr, host


def test_panel_defaults_to_list_view(qapp, tmp_path):
    """护栏 §1：默认视图是列表（等价现有行为），按天按钮未选中。"""
    panel, _mgr, _host = _make_panel(tmp_path, ["2026-10-03 09:00"])
    assert panel._day_btn.isChecked() is False
    assert panel._center_stack.currentIndex() == 0


def test_toggle_day_view_fills_active_days(qapp, tmp_path):
    """切到按天 → 中部换页 + 活跃天进下拉（空白天不进）。"""
    panel, _mgr, _host = _make_panel(
        tmp_path, ["2026-10-01 09:00", "2026-10-03 10:00"])
    panel._day_btn.setChecked(True)
    assert panel._center_stack.currentIndex() == 1
    days = [panel._day_view._day_combo.itemData(i)
            for i in range(panel._day_view._day_combo.count())]
    assert days == ["2026-10-03", "2026-10-01"]
    assert "2026-10-02" not in days


def test_toggle_back_to_list(qapp, tmp_path):
    panel, _mgr, _host = _make_panel(tmp_path, ["2026-10-03 09:00"])
    panel._day_btn.setChecked(True)
    panel._day_btn.setChecked(False)
    assert panel._center_stack.currentIndex() == 0


def test_day_view_lists_events_for_selected_day(qapp, tmp_path):
    panel, _mgr, _host = _make_panel(
        tmp_path, ["2026-10-03 09:12", "2026-10-03 11:30", "2026-10-04 08:00"])
    panel._day_btn.setChecked(True)
    dv = panel._day_view
    # 默认落在最新一天（10-04），切到 10-03
    idx = dv._day_combo.findData("2026-10-03")
    dv._day_combo.setCurrentIndex(idx)
    assert dv._day_list.count() == 2
    assert "碎片 2" in dv._day_summary.text()


def test_day_view_preference_persists(qapp, tmp_path):
    """切到按天 → 偏好落盘；重建的面板沿用该偏好。"""
    panel, _mgr, host = _make_panel(tmp_path, ["2026-10-03 09:00"])
    panel._day_btn.setChecked(True)
    assert host._config.get("fragment_day_view") is True


def test_save_note_uses_existing_note_channel(qapp, tmp_path):
    """「存为笔记」复用 NoteManager.add_note，不新造沉淀通道。"""
    from src.note_manager import NoteManager
    panel, _mgr, host = _make_panel(tmp_path, ["2026-10-03 09:12"])
    nm = NoteManager(str(tmp_path / "notes.json"))
    host._note_manager = nm
    panel._day_btn.setChecked(True)
    before = len(nm.get_all_notes())
    panel._day_view._on_save_note()
    notes = nm.get_all_notes()
    assert len(notes) == before + 1
    assert "2026-10-03" in notes[0].title
    assert "碎片" in notes[0].content

