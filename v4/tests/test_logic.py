# -*- coding: utf-8 -*-
"""
Qt 无关业务逻辑单元测试
====================================================================
覆盖纯 Python（无 PyQt6 依赖）的核心模块，可在任意环境直接运行：

  - src/constants.py        : 共享常量 + 文件名净化（任务 2.4 / 3.3）
  - src/fragment_manager.py : 碎片管理（去抖写盘 1.1 / 紧凑 JSON 1.2 / 增删查/去重/淘汰）
  - src/config.py           : 配置读写与校验
  - src/nav_manager.py      : 网址导航（URL 归一化 / 增删查）
  - src/temp_asset_manager.py: 临时素材（落盘文件名净化 3.3）

GUI 面板（*_panel.py / main_window / card_window）依赖 PyQt6，
其冒烟测试见 tests/test_ui_smoke.py（需 pytest-qt，延后到真实环境运行）。
"""

import json
import os
import sys
import tempfile

# pytest 在真实环境中提供；此处允许缺失，便于无 pytest 时直接运行验证。
try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

# 让测试能 import src 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.constants import (  # noqa: E402
    DEFAULT_THEME,
    sanitize_filename,
    NOTE_AUTOSAVE_INTERVAL_MS,
    DATETIME_DATE_LEN,
    FRAGMENT_PREVIEW_LEN,
)
from src.fragment_manager import (  # noqa: E402
    FragmentManager,
    TYPE_CLIPBOARD_TEXT,
    TYPE_FILE_PICKUP,
)
from src.config import (  # noqa: E402
    ConfigManager, DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES,
    sanitize_nav_order, DEFAULT_NAV_ORDER, NAV_PAGE_KEYS,
)
from src.nav_manager import NavManager, NavSite  # noqa: E402
from src import temp_asset_manager as tam  # noqa: E402
from src.task_manager import (  # noqa: E402
    TaskManager,
    task_state,
    format_relative_deadline,
    group_title,
    current_week_end,
    STATE_OVERDUE,
    STATE_TODAY,
    STATE_FUTURE,
    STATE_NONE,
    GROUP_OVERDUE,
    GROUP_TODAY,
    GROUP_WEEK,
    GROUP_LATER,
    GROUP_NONE,
    GROUP_DONE,
)


# ====================================================================
# constants：文件名净化（任务 3.3）
# ====================================================================
class TestSanitizeFilename:
    def test_passthrough_normal(self):
        assert sanitize_filename("report.txt") == "report.txt"

    def test_replaces_illegal_chars(self):
        out = sanitize_filename('a/b:c*?.png')
        for ch in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
            assert ch not in out
        # 扩展名保留
        assert out.endswith(".png")

    def test_strip_whitespace(self):
        assert sanitize_filename("  spaced name  ") == "spaced name"

    def test_empty_fallback(self):
        assert sanitize_filename("") == "file"
        assert sanitize_filename("   ") == "file"

    def test_custom_replacement(self):
        assert sanitize_filename("a:b", replacement="-") == "a-b"


# ====================================================================
# constants：共享常量存在且类型合理（任务 2.4）
# ====================================================================
class TestConstantsValues:
    def test_note_autosave_interval_positive(self):
        assert isinstance(NOTE_AUTOSAVE_INTERVAL_MS, int)
        assert NOTE_AUTOSAVE_INTERVAL_MS > 0

    def test_datetime_slice_constants_consistent(self):
        # "YYYY-MM-DD HH:MM" = 10 位日期 + 1 空格 + 5 位时分 = 16
        assert DATETIME_DATE_LEN == 10
        assert DATETIME_DATE_LEN + 1 + 5 == 16

    def test_fragment_preview_len_positive(self):
        assert isinstance(FRAGMENT_PREVIEW_LEN, int)
        assert FRAGMENT_PREVIEW_LEN > 0


# ====================================================================
# FragmentManager：去抖写盘（任务 1.1）
# ====================================================================
class TestFragmentDebounce:
    def test_flush_persists_pending_writes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fragments.json")
            fm = FragmentManager(path)
            for i in range(20):
                fm.add_clipboard_text(f"content-{i}")
            # 尚未 flush，定时器可能未触发
            fm.flush()
            assert os.path.exists(path)
            data = json.load(open(path, encoding="utf-8"))
            assert len(data["fragments"]) == 20

    def test_save_method_flushes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fragments.json")
            fm = FragmentManager(path)
            fm.add_clipboard_text("hello")
            fm.save()
            data = json.load(open(path, encoding="utf-8"))
            assert len(data["fragments"]) == 1

    def test_close_flushes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fragments.json")
            fm = FragmentManager(path)
            fm.add_clipboard_text("x")
            fm.close()
            assert os.path.exists(path)


# ====================================================================
# FragmentManager：紧凑 JSON（任务 1.2）
# ====================================================================
class TestFragmentCompactJson:
    def test_no_indent_in_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fragments.json")
            fm = FragmentManager(path)
            fm.add_clipboard_text("compact")
            fm.flush()
            raw = open(path, encoding="utf-8").read()
            assert "\n" not in raw
            assert "  " not in raw  # 无缩进空格

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fragments.json")
            fm = FragmentManager(path)
            fm.add_clipboard_text("abc")
            fm.flush()
            fm2 = FragmentManager(path)
            assert fm2.count() == 1
            assert fm2.get_all_fragments()[0].content == "abc"


# ====================================================================
# FragmentManager：增删查 / 局部去重 / 容量淘汰
# ====================================================================
class TestFragmentCrud:
    def test_add_and_count(self):
        fm = FragmentManager(":memory:")
        fid = fm.add_clipboard_text("t1")
        assert fid >= 1
        assert fm.count() == 1

    def test_local_dedup_within_window(self):
        fm = FragmentManager(":memory:")
        fid1 = fm.add_clipboard_text("dup")
        fid2 = fm.add_clipboard_text("dup")
        # 窗口内（末尾 12 条）重复 → 返回已存在 id，不新增
        assert fid1 == fid2
        assert fm.count() == 1

    def test_delete(self):
        fm = FragmentManager(":memory:")
        fid = fm.add_clipboard_text("t")
        assert fm.delete_fragment(fid) is True
        assert fm.count() == 0
        assert fm.delete_fragment(fid) is False

    def test_clear_all(self):
        fm = FragmentManager(":memory:")
        fm.add_clipboard_text("a")
        fm.add_clipboard_text("b")
        assert fm.clear_all() == 2
        assert fm.count() == 0

    def test_trim_to_max_fifo(self):
        fm = FragmentManager(":memory:")
        for i in range(10):
            fm.add_clipboard_text(f"c{i}")
        removed = fm.trim_to_max(5)
        assert removed == 5
        assert fm.count() == 5

    def test_get_by_type(self):
        fm = FragmentManager(":memory:")
        fm.add_clipboard_text("t")
        fm.add_fragment(TYPE_FILE_PICKUP, "p")
        assert fm.count_by_type(TYPE_CLIPBOARD_TEXT) == 1
        assert len(fm.get_fragments_by_type(TYPE_FILE_PICKUP)) == 1


# ====================================================================
# ConfigManager：类型/范围校验 + 持久化
# ====================================================================
class TestConfigManager:
    def test_default_value(self):
        # 默认主题由 constants.DEFAULT_THEME 统一决定（当前为 dark）
        cm = ConfigManager(":memory:")
        assert cm.get("theme") == DEFAULT_THEME == "dark"

    def test_set_valid(self):
        cm = ConfigManager(":memory:")
        assert cm.set("clipboard_max_items", 300) is True
        assert cm.get("clipboard_max_items") == 300

    def test_set_invalid_type_rejected(self):
        cm = ConfigManager(":memory:")
        assert cm.set("clipboard_max_items", "not-int") is False
        assert cm.get("clipboard_max_items") == 200  # 默认

    def test_set_out_of_range_rejected(self):
        cm = ConfigManager(":memory:")
        assert cm.set("auto_hide_seconds", 999) is False
        assert cm.get("auto_hide_seconds") == 3  # 默认

    def test_clipboard_capture_images_contract(self):
        """Y2 新增配置项：默认开启、类型为 bool、非法值被拒"""
        cm = ConfigManager(":memory:")
        assert DEFAULT_CONFIG.get("clipboard_capture_images") is True
        assert _CONFIG_TYPES.get("clipboard_capture_images") is bool
        assert cm.get("clipboard_capture_images") is True
        assert cm.set("clipboard_capture_images", "yes") is False
        assert cm.set("clipboard_capture_images", False) is True
        assert cm.get("clipboard_capture_images") is False

    def test_every_default_key_has_type_entry(self):
        """配置项三件套一致性：DEFAULT_CONFIG 里每个键都必须登记类型。

        新增配置项时容易漏掉 _CONFIG_TYPES，导致该键失去类型校验
        （json 里塞个字符串也会被原样接受）——这里做成常驻护栏。
        """
        missing = sorted(set(DEFAULT_CONFIG) - set(_CONFIG_TYPES))
        assert missing == [], f"以下配置项缺少 _CONFIG_TYPES 登记: {missing}"

    def test_range_keys_are_typed_and_valid(self):
        """范围表里的键必须存在于默认配置，且 (lo, hi) 合法"""
        for key, rng in _CONFIG_RANGES.items():
            assert key in DEFAULT_CONFIG, f"范围表含未知配置项: {key}"
            lo, hi = rng
            assert lo <= hi, f"{key} 范围颠倒: {rng}"
            assert lo <= DEFAULT_CONFIG[key] <= hi, \
                f"{key} 默认值 {DEFAULT_CONFIG[key]} 不在范围 {rng} 内"

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            cm = ConfigManager(path)
            # 刻意写一个「非默认值」，才能验证持久化而不是在验证默认值
            cm.set("theme", "light")
            cm.save()
            cm2 = ConfigManager(path)
            assert cm2.get("theme") == "light"

    def test_corrupt_file_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json ")
            cm = ConfigManager(path)
            assert cm.get("theme") == DEFAULT_THEME


# ====================================================================
# NavManager：URL 归一化 + 增删查
# ====================================================================
class TestNavManager:
    def test_url_normalization_adds_https(self):
        s = NavSite(1, "百度", "baidu.com")
        assert s.url == "https://baidu.com"

    def test_url_keeps_existing_scheme(self):
        s = NavSite(2, "x", "http://example.com")
        assert s.url == "http://example.com"

    def test_add_and_get_site(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nav.json")
            nm = NavManager(path)
            gid = nm.add_group("常用")
            sid = nm.add_site(gid, "百度", "baidu.com")
            group = nm.get_group(gid)
            assert group is not None
            sites = group.sites
            assert any(s.nav_id == sid for s in sites)
            assert any(s.url == "https://baidu.com" for s in sites)


# ====================================================================
# TempAssetManager：落盘文件名净化（任务 3.3 扩展落盘点）
# ====================================================================
class TestTempAssetSanitize:
    def test_add_asset_sanitizes_stored_filename(self):
        with tempfile.TemporaryDirectory() as d:
            # base_dir 为程序主目录；素材落在 base_dir/temp_assets/
            am = tam.TempAssetManager(d)
            # 制造一个真实存在的源文件（文件名合法，可落盘）
            src = os.path.join(d, "legal_source.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("data")

            # 模拟「源文件 basename 含 Windows 非法字符」的场景
            # （真实场景下此类文件名无法在 Windows 创建，故用 monkeypatch 触发净化分支）
            real_basename = os.path.basename
            def fake_basename(p):
                if p == src:
                    return "bad:name*?.txt"
                return real_basename(p)
            os.path.basename = fake_basename
            try:
                aid = am.add_asset(src)
            finally:
                os.path.basename = real_basename

            assert aid > 0
            asset = am.get_asset(aid)
            # stored_path 的文件名部分不得含非法字符
            fname = os.path.basename(asset.stored_path)
            for ch in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
                assert ch not in fname
            # original_name 也需净化（用于另存为默认名等下游使用）
            for ch in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
                assert ch not in asset.original_name
            assert os.path.exists(asset.stored_path)


# ====================================================================
# TempAssetManager：拖拽去重（任务 6.3）
# ====================================================================
class TestTempAssetDedup:
    def test_duplicate_content_returns_same_id(self):
        """内容相同的文件重复拖入 → 复用已有记录，不新增不重复落盘"""
        with tempfile.TemporaryDirectory() as d:
            am = tam.TempAssetManager(d)
            src1 = os.path.join(d, "a.txt")
            with open(src1, "w", encoding="utf-8") as f:
                f.write("same content")
            src2 = os.path.join(d, "b.txt")  # 不同文件名，相同内容
            with open(src2, "w", encoding="utf-8") as f:
                f.write("same content")

            aid1 = am.add_asset(src1)
            assert aid1 > 0
            before = am.count()
            aid2 = am.add_asset(src2)
            # 内容相同 → 返回已有 id，且总数不增加
            assert aid2 == aid1
            assert am.count() == before

    def test_distinct_content_adds_new(self):
        """内容不同的文件 → 各自独立入库"""
        with tempfile.TemporaryDirectory() as d:
            am = tam.TempAssetManager(d)
            p1 = os.path.join(d, "x.txt")
            with open(p1, "w", encoding="utf-8") as f:
                f.write("content one")
            p2 = os.path.join(d, "y.txt")
            with open(p2, "w", encoding="utf-8") as f:
                f.write("content two entirely")

            aid1 = am.add_asset(p1)
            aid2 = am.add_asset(p2)
            assert aid1 != aid2
            assert am.count() == 2

    def test_dedup_survives_reload(self):
        """去重索引在重新加载后仍然有效"""
        import importlib
        with tempfile.TemporaryDirectory() as d:
            am = tam.TempAssetManager(d)
            p = os.path.join(d, "z.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("reload content")
            am.add_asset(p)
            # 重建管理器（模拟重启），加载已有元数据
            importlib.reload(tam)
            am2 = tam.TempAssetManager(d)
            assert am2.count() == 1
            # 再次拖入相同内容 → 仍应去重
            aid = am2.add_asset(p)
            assert aid == 1
            assert am2.count() == 1


# ====================================================================
# task_manager：task_state 状态判定（C3 口径统一）
# ====================================================================
class TestTaskState:
    TODAY = "2026-09-23"   # 周三

    def test_overdue_negative_delta(self):
        state, delta = task_state("2026-09-20", self.TODAY)
        assert state == STATE_OVERDUE
        assert delta == -3

    def test_today_zero_delta(self):
        state, delta = task_state("2026-09-23", self.TODAY)
        assert state == STATE_TODAY
        assert delta == 0

    def test_future_positive_delta(self):
        state, delta = task_state("2026-09-25", self.TODAY)
        assert state == STATE_FUTURE
        assert delta == 2

    def test_empty_deadline_is_none(self):
        assert task_state("", self.TODAY) == (STATE_NONE, None)
        assert task_state(None, self.TODAY) == (STATE_NONE, None)
        assert task_state("   ", self.TODAY) == (STATE_NONE, None)

    def test_dirty_deadline_is_none(self):
        # 脏格式（旧行为会被字符串比较误判为逾期）→ 统一视为无日期
        assert task_state("2026/09/20", self.TODAY) == (STATE_NONE, None)
        assert task_state("not-a-date", self.TODAY) == (STATE_NONE, None)
        assert task_state("2026-13-40", self.TODAY) == (STATE_NONE, None)

    def test_default_today_uses_system(self):
        # 不传 today 时使用系统当天：今天应判为 STATE_TODAY
        import datetime as _dt
        state, delta = task_state(_dt.date.today().isoformat())
        assert state == STATE_TODAY
        assert delta == 0


# ====================================================================
# task_manager：format_relative_deadline 相对时间文案（B3）
# ====================================================================
class TestFormatRelativeDeadline:
    TODAY = "2026-09-23"   # 周三

    def test_today(self):
        assert format_relative_deadline("2026-09-23", self.TODAY) == "今天"

    def test_tomorrow(self):
        assert format_relative_deadline("2026-09-24", self.TODAY) == "明天"

    def test_overdue_days(self):
        assert format_relative_deadline("2026-09-20", self.TODAY) == "逾期3天"

    def test_future_month_day_weekday(self):
        # 2026-09-25 是周五
        assert format_relative_deadline("2026-09-25", self.TODAY) == "9月25日（周五）"

    def test_empty_returns_blank(self):
        assert format_relative_deadline("", self.TODAY) == ""
        assert format_relative_deadline("dirty", self.TODAY) == ""

    def test_week_end_and_title(self):
        # 2026-09-23 周三 → 本周日为 2026-09-27
        assert current_week_end(self.TODAY).isoformat() == "2026-09-27"
        assert group_title(GROUP_WEEK, self.TODAY) == "本周（至 9 月 27 日）"
        # 非本周组标题不含日期
        assert group_title(GROUP_OVERDUE, self.TODAY) == "逾期"


# ====================================================================
# task_manager：completed_at 字段（写入/清空/兼容旧 JSON）
# ====================================================================
class TestCompletedAt:
    def test_new_task_has_empty_completed_at(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TaskManager(os.path.join(d, "schedule.json"))
            tid = tm.add_task("t", "", "2026-09-23")
            assert tm.get_task(tid).completed_at == ""

    def test_set_done_true_writes_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TaskManager(os.path.join(d, "schedule.json"))
            tid = tm.add_task("t", "", "")
            assert tm.set_done(tid, True) is True
            task = tm.get_task(tid)
            assert task.done is True
            # 形如 "YYYY-MM-DD HH:MM"
            assert len(task.completed_at) == 16
            assert task.completed_at[10] == " "

    def test_set_done_false_clears(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TaskManager(os.path.join(d, "schedule.json"))
            tid = tm.add_task("t", "", "")
            tm.set_done(tid, True)
            tm.set_done(tid, False)
            task = tm.get_task(tid)
            assert task.done is False
            assert task.completed_at == ""

    def test_toggle_task_maintains_completed_at(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TaskManager(os.path.join(d, "schedule.json"))
            tid = tm.add_task("t", "", "")
            tm.toggle_task(tid)
            assert tm.get_task(tid).completed_at != ""
            tm.toggle_task(tid)
            assert tm.get_task(tid).completed_at == ""

    def test_reload_preserves_completed_at(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            tm = TaskManager(path)
            tid = tm.add_task("t", "", "")
            tm.set_done(tid, True)
            stamp = tm.get_task(tid).completed_at
            tm2 = TaskManager(path)
            assert tm2.get_task(tid).completed_at == stamp

    def test_legacy_json_without_key_loads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tasks": [{
                    "task_id": 1, "title": "old", "note": "",
                    "deadline": "", "done": False, "created_at": "2026-01-01 08:00",
                }], "next_id": 2}, f)
            tm = TaskManager(path)
            assert tm.get_task(1).completed_at == ""


# ====================================================================
# task_manager：get_tasks_grouped 分组顺序与「本周」边界
# ====================================================================
class TestGetTasksGrouped:
    TODAY = "2026-09-23"   # 周三 → 本周日 2026-09-27

    def _tm(self, d):
        return TaskManager(os.path.join(d, "schedule.json"))

    def test_group_order_and_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._tm(d)
            tm.add_task("overdue", "", "2026-09-20")
            tm.add_task("today", "", "2026-09-23")
            tm.add_task("tomorrow", "", "2026-09-24")   # 明天 → 本周
            tm.add_task("houtian", "", "2026-09-25")    # 后天 → 本周
            tm.add_task("sunday", "", "2026-09-27")     # 本周日 → 本周
            tm.add_task("monday", "", "2026-09-28")     # 下周一 → 以后
            tm.add_task("nodate", "", "")
            done_id = tm.add_task("done", "", "2026-09-21")
            tm.set_done(done_id, True)

            grouped = tm.get_tasks_grouped(today=self.TODAY)
            keys = [k for k, _ in grouped]
            assert keys == [GROUP_OVERDUE, GROUP_TODAY, GROUP_WEEK,
                            GROUP_LATER, GROUP_NONE, GROUP_DONE]

            by_key = {k: [t.title for t in tasks] for k, tasks in grouped}
            assert by_key[GROUP_OVERDUE] == ["overdue"]
            assert by_key[GROUP_TODAY] == ["today"]
            assert by_key[GROUP_WEEK] == ["tomorrow", "houtian", "sunday"]
            assert by_key[GROUP_LATER] == ["monday"]
            assert by_key[GROUP_NONE] == ["nodate"]
            assert by_key[GROUP_DONE] == ["done"]

    def test_dirty_deadline_goes_to_none_group(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._tm(d)
            tm.add_task("dirty", "", "2026/09/20")
            grouped = tm.get_tasks_grouped(today=self.TODAY)
            keys = [k for k, _ in grouped]
            assert keys == [GROUP_NONE]
            assert [t.title for t in grouped[0][1]] == ["dirty"]

    def test_within_group_sorted_by_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._tm(d)
            tm.add_task("late", "", "2026-09-27")
            tm.add_task("early", "", "2026-09-24")
            tm.add_task("mid", "", "2026-09-25")
            grouped = tm.get_tasks_grouped(today=self.TODAY)
            week = [tasks for k, tasks in grouped if k == GROUP_WEEK][0]
            assert [t.title for t in week] == ["early", "mid", "late"]

    def test_none_group_sorted_by_created_at(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._tm(d)
            for name in ["a", "b", "c"]:
                tm.add_task(name, "", "")
            grouped = tm.get_tasks_grouped(today=self.TODAY)
            none = [tasks for k, tasks in grouped if k == GROUP_NONE][0]
            assert [t.title for t in none] == ["a", "b", "c"]

    def test_empty_groups_omitted(self):
        with tempfile.TemporaryDirectory() as d:
            tm = self._tm(d)
            tm.add_task("only", "", "2026-09-23")
            grouped = tm.get_tasks_grouped(today=self.TODAY)
            assert [k for k, _ in grouped] == [GROUP_TODAY]


# ====================================================================
# config：nav_order 左栏排序解析/校验/回退
# ====================================================================
class TestNavOrderSanitize:
    def test_default_order_is_valid(self):
        out = sanitize_nav_order(DEFAULT_NAV_ORDER)
        assert out == DEFAULT_NAV_ORDER
        assert out is not DEFAULT_NAV_ORDER   # 返回副本
        assert len(NAV_PAGE_KEYS) == 7
        assert set(DEFAULT_NAV_ORDER) == set(NAV_PAGE_KEYS)

    def test_valid_permutation(self):
        raw = ["nav", "apps", "assets", "knowledge", "notes", "tasks", "fragments"]
        assert sanitize_nav_order(raw) == raw

    def test_empty_means_never_customized(self):
        # 空列表 = 从未自定义 → 校验返回 None，调用方回退默认顺序
        assert sanitize_nav_order([]) is None

    def test_wrong_length_rejected(self):
        assert sanitize_nav_order(["a", "b"]) is None
        assert sanitize_nav_order(["fragments"] * 7) is None   # 重复 key

    def test_missing_key_rejected(self):
        raw = ["fragments", "tasks", "notes", "knowledge", "assets", "apps"]
        assert sanitize_nav_order(raw) is None   # 缺 "nav"

    def test_unknown_key_rejected(self):
        raw = ["fragments", "tasks", "notes", "knowledge", "assets",
               "apps", "bogus"]
        assert sanitize_nav_order(raw) is None

    def test_non_string_items_rejected(self):
        raw = ["fragments", 1, "notes", "knowledge", "assets", "apps", "nav"]
        assert sanitize_nav_order(raw) is None

    def test_non_list_rejected(self):
        assert sanitize_nav_order("fragments") is None
        assert sanitize_nav_order(None) is None
        assert sanitize_nav_order({"fragments": 0}) is None


if __name__ == "__main__":
    # 允许在无 pytest 的环境下直接运行验证
    import traceback
    # 收集本模块中所有 Test* 类的测试方法并执行
    failed = 0
    ran = 0
    mod = sys.modules[__name__]
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for m in dir(inst):
                if m.startswith("test_"):
                    ran += 1
                    try:
                        getattr(inst, m)()
                    except Exception:
                        failed += 1
                        print(f"[FAIL] {name}.{m}")
                        traceback.print_exc()
    print(f"\n运行 {ran} 个测试，失败 {failed} 个")
    sys.exit(1 if failed else 0)
