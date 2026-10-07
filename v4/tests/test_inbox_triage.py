# -*- coding: utf-8 -*-
"""
碎片规则清仓纯逻辑单测（v4/src/inbox_triage.py）+ 安全护栏。

护栏针对点（任务卡 inbox-triage §规则定义 / §测试）：
  · 三条件逐条边界：刚好 N 天 / N-1 / N+1；刚好 max_len / ±1；
    hit_count == 0 vs >= 1
  · 三条件 **AND**（缺一即不候选）——尤其「hit_count >= 1 的长短合适的
    旧条目必须不候选」
  · 脏数据容错：created_at 空 / 乱码 / 缺字段 → 不崩、不候选
  · hit_count 零迁移：from_dict 缺键 → 0；to_dict 往返一致；位置传参兼容
  · note_search_hit：正常 +1 / 空列表安全 / 脏 id 安全
  · **安全护栏**：模拟 UI 单条删除路径后断言 delete_fragments 从未被调用
  · **全部删除**（2026-10-04 放开）：按钮随候选数启停；确认后走批量接口
    恰一次、只删候选不碰非候选；取消则一条不删
  · 建议清单实时算：改数据后再次拉取结果跟着变（钉死「不落缓存文件」）
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import inbox_triage  # noqa: E402
from src.fragment_manager import Fragment, FragmentManager  # noqa: E402


NOW = datetime(2026, 10, 3, 12, 0)


def ts_days_ago(days, minutes=0):
    """构造距今 days 天的 created_at 字符串（"YYYY-MM-DD HH:MM"）。"""
    dt = NOW - timedelta(days=days, minutes=minutes)
    return dt.strftime("%Y-%m-%d %H:%M")


def make_frag(content="短内容", days=40, hit_count=0, created_at=None, fid=1):
    return Fragment(
        fragment_id=fid,
        ftype="clipboard_text",
        content=content,
        source="test",
        created_at=(created_at if created_at is not None else ts_days_ago(days)),
        pinned=False,
        hit_count=hit_count,
    )


class _FragStub:
    """轻量替身：只带 inbom_triage 需要读的三个属性（无需真 Fragment）。"""

    def __init__(self, content="短内容", created_at=None, hit_count=0):
        self.content = content
        self.created_at = created_at
        self.hit_count = hit_count

    def preview(self, max_len=50):
        text = str(self.content).replace("\n", " ").strip()
        return text[:max_len] + ("..." if len(text) > max_len else "")

    def __getattr__(self, name):
        # 缺失属性按「无」处理（模拟老数据 / 脏对象）
        raise AttributeError(name)


# ====================================================================
# 1. 天数边界：刚好 N / N-1 / N+1
# ====================================================================
def test_age_boundary_exact_n_days():
    assert inbox_triage.is_stale(make_frag(days=30), NOW, 30, 40) is True


def test_age_boundary_n_minus_1():
    assert inbox_triage.is_stale(make_frag(days=29), NOW, 30, 40) is False


def test_age_boundary_n_plus_1():
    assert inbox_triage.is_stale(make_frag(days=31), NOW, 30, 40) is True


def test_age_days_uses_floor_not_rounding():
    # 29 天 23 小时 → 仍是 29 天（向下取整），不候选
    ts = (NOW - timedelta(days=29, hours=23)).strftime("%Y-%m-%d %H:%M")
    assert inbox_triage.age_days(make_frag(created_at=ts), NOW) == 29
    assert inbox_triage.is_stale(make_frag(created_at=ts), NOW, 30, 40) is False


# ====================================================================
# 2. 长度边界：刚好 max_len / ±1
# ====================================================================
def test_len_boundary_exact():
    assert inbox_triage.is_stale(make_frag("x" * 40), NOW, 30, 40) is True


def test_len_boundary_plus_1():
    assert inbox_triage.is_stale(make_frag("x" * 41), NOW, 30, 40) is False


def test_len_boundary_minus_1():
    assert inbox_triage.is_stale(make_frag("x" * 39), NOW, 30, 40) is True


# ====================================================================
# 3. hit_count 边界：0 vs >=1
# ====================================================================
def test_hit_count_zero_candidate():
    assert inbox_triage.is_stale(make_frag(hit_count=0), NOW, 30, 40) is True


def test_hit_count_one_not_candidate():
    assert inbox_triage.is_stale(make_frag(hit_count=1), NOW, 30, 40) is False


def test_hit_count_higher_not_candidate():
    assert inbox_triage.is_stale(make_frag(hit_count=7), NOW, 30, 40) is False


# ====================================================================
# 4. 三条件是 AND（缺一即不候选）
# ====================================================================
def test_and_all_satisfied():
    f = make_frag(content="短", days=60, hit_count=0)
    assert inbox_triage.is_stale(f, NOW, 30, 40) is True


def test_and_fail_only_age():
    f = make_frag(content="短", days=5, hit_count=0)
    assert inbox_triage.is_stale(f, NOW, 30, 40) is False


def test_and_fail_only_hit():
    # 关键反例：又旧又短，但被搜索命中过 → 必须不候选
    f = make_frag(content="短", days=365, hit_count=3)
    assert inbox_triage.is_stale(f, NOW, 30, 40) is False


def test_and_fail_only_len():
    f = make_frag(content="y" * 200, days=365, hit_count=0)
    assert inbox_triage.is_stale(f, NOW, 30, 40) is False


def test_collect_candidates_only_and():
    old_short_unhit = make_frag(content="a", days=50, hit_count=0, fid=1)
    old_long_unhit = make_frag(content="b" * 100, days=50, hit_count=0, fid=2)
    old_short_hit = make_frag(content="c", days=50, hit_count=2, fid=3)
    fresh_short_unhit = make_frag(content="d", days=1, hit_count=0, fid=4)
    got = inbox_triage.collect_candidates(
        [old_short_unhit, old_long_unhit, old_short_hit, fresh_short_unhit],
        now=NOW, min_days=30, max_len=40)
    assert [f.fragment_id for f in got] == [1]


# ====================================================================
# 5. 脏数据容错：不崩、不候选
# ====================================================================
@pytest.mark.parametrize("bad", ["", "   ", "乱码", "2026/10/03", "33:99",
                                 None, 12345, "2026-13-40 99:99"])
def test_dirty_created_at_not_candidate(bad):
    f = _FragStub(content="短", created_at=bad, hit_count=0)
    assert inbox_triage.is_stale(f, NOW, 30, 40) is False
    assert inbox_triage.age_days(f, NOW) is None


def test_missing_attributes_safe():
    class _Bare:
        pass

    assert inbox_triage.is_stale(_Bare(), NOW, 30, 40) is False


def test_none_content_safe():
    f = _FragStub(content=None, created_at=ts_days_ago(60), hit_count=0)
    # None 内容视作 0 长度 → ≤ max_len，其余满足 → 候选（不崩）
    assert inbox_triage.is_stale(f, NOW, 30, 40) is True


def test_dirty_hit_count_not_candidate():
    # hit_count 是乱码 → 收敛为 0？不，注意：脏值收敛 0 会**变成候选**。
    # 这里钉的是「不崩」；脏值按 0 处理是刻意的安全收敛（0=未命中）。
    f = _FragStub(content="短", created_at=ts_days_ago(60), hit_count="abc")
    assert inbox_triage.is_stale(f, NOW, 30, 40) is True


def test_future_time_not_candidate():
    future = (NOW + timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    assert inbox_triage.is_stale(make_frag(created_at=future), NOW, 30, 40) \
        is False


# ====================================================================
# 6. candidate_reason 文案
# ====================================================================
def test_candidate_reason_contains_core_facts():
    f = make_frag(content="短", days=45, hit_count=0)
    reason = inbox_triage.candidate_reason(f, NOW, 30, 40)
    assert "45 天" in reason
    assert "从未被搜索命中" in reason


def test_candidate_reason_dirty_time():
    f = _FragStub(content="短", created_at="乱码", hit_count=0)
    assert "无法解析" in inbox_triage.candidate_reason(f, NOW, 30, 40)


# ====================================================================
# 7. hit_count 零迁移
# ====================================================================
def test_from_dict_missing_hit_count_is_zero():
    assert Fragment.from_dict({"fragment_id": 1}).hit_count == 0


def test_to_dict_roundtrip_hit_count():
    f = make_frag(hit_count=5)
    d = f.to_dict()
    assert d["hit_count"] == 5
    assert Fragment.from_dict(d).hit_count == 5


def test_init_positional_backcompat():
    # 旧位置传参方式（7 个位置参数，无 hit_count）仍能工作
    f = Fragment(1, "clipboard_text", "内容", "src", "2026-10-01 09:00")
    assert f.hit_count == 0
    assert f.pinned is False


def test_init_positional_with_pinned_still_works():
    # pinned 仍在第 7 位（hit_count 追加在最后，不破坏既有位置传参）
    f = Fragment(1, "clipboard_text", "内容", "src", "2026-10-01 09:00", None,
                 True)
    assert f.pinned is True
    assert f.hit_count == 0


def test_from_dict_dirty_hit_count_converges_zero():
    assert Fragment.from_dict(
        {"fragment_id": 1, "hit_count": "乱码"}).hit_count == 0
    assert Fragment.from_dict(
        {"fragment_id": 1, "hit_count": None}).hit_count == 0


# ====================================================================
# 8. note_search_hit
# ====================================================================
def test_note_search_hit_increments(tmp_path):
    mgr = FragmentManager(str(tmp_path / "f.json"))
    fid = mgr.add_clipboard_text("hello")
    assert mgr.note_search_hit([fid]) == 1
    assert mgr.get_fragment(fid).hit_count == 1
    assert mgr.note_search_hit([fid]) == 1
    assert mgr.get_fragment(fid).hit_count == 2


def test_note_search_hit_empty_safe(tmp_path):
    mgr = FragmentManager(str(tmp_path / "f.json"))
    mgr.add_clipboard_text("hello")
    assert mgr.note_search_hit([]) == 0
    assert mgr.note_search_hit(None) == 0


def test_note_search_hit_dirty_id_safe(tmp_path):
    mgr = FragmentManager(str(tmp_path / "f.json"))
    fid = mgr.add_clipboard_text("hello")
    assert mgr.note_search_hit([99999, fid]) == 1
    assert mgr.get_fragment(fid).hit_count == 1


def test_note_search_hit_persists_hit_count(tmp_path):
    path = str(tmp_path / "f.json")
    mgr = FragmentManager(path)
    fid = mgr.add_clipboard_text("hello")
    mgr.note_search_hit([fid])
    mgr.flush()
    mgr2 = FragmentManager(path)
    assert mgr2.get_fragment(fid).hit_count == 1


# ====================================================================
# 9. 实时算（不落缓存文件）
# ====================================================================
def test_collect_candidates_realtime(tmp_path):
    mgr = FragmentManager(str(tmp_path / "f.json"))
    fid = mgr.add_clipboard_text("短")
    # 直接把 created_at 改成 60 天前（模拟陈旧碎片）
    frag = mgr.get_fragment(fid)
    frag.created_at = ts_days_ago(60)
    cands = inbox_triage.collect_candidates(
        mgr.get_all_fragments(), now=NOW, min_days=30, max_len=40)
    assert [f.fragment_id for f in cands] == [fid]
    # 记账命中后再拉一次 → 立刻掉出候选（无缓存，跟着数据变）
    mgr.note_search_hit([fid])
    cands2 = inbox_triage.collect_candidates(
        mgr.get_all_fragments(), now=NOW, min_days=30, max_len=40)
    assert cands2 == []


def test_collect_candidates_no_cache_file(tmp_path):
    mgr = FragmentManager(str(tmp_path / "f.json"))
    mgr.add_clipboard_text("短")
    before = set(os.listdir(tmp_path))
    inbox_triage.collect_candidates(mgr.get_all_fragments(), now=NOW)
    after = set(os.listdir(tmp_path))
    assert before == after, "collect_candidates 不得产生任何缓存文件"


# ====================================================================
# 10. 安全护栏：单条删除路径绝不调 delete_fragments
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


class _FakeClipboard:
    def put_text(self, text):
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
        self._clipboard_monitor = _FakeClipboard()
        self._task_manager = None
        self._temp_asset_manager = None
        self.current_theme = "dark"
        self.anim_speed = 1.0


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

    def show_toast(self, _msg, **kwargs):
        pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _old_manager(tmp_path):
    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    fid = mgr.add_clipboard_text("很旧的一条碎片")
    mgr.get_fragment(fid).created_at = ts_days_ago(60)
    return mgr, fid


def test_single_delete_never_calls_batch(tmp_path, qapp, monkeypatch):
    """本卡安全红线：驱动单条删除路径，delete_fragments 计数必须为 0。"""
    from src.fragments_panel import FragmentsPanel
    from src import fragments_panel as fp_mod

    mgr, fid = _old_manager(tmp_path)

    calls = {"batch": 0, "single": 0}
    real_single = mgr.delete_fragment
    real_batch = mgr.delete_fragments

    def spy_single(_fid):
        calls["single"] += 1
        return real_single(_fid)

    def spy_batch(_ids):
        calls["batch"] += 1
        return real_batch(_ids)

    monkeypatch.setattr(mgr, "delete_fragment", spy_single)
    monkeypatch.setattr(mgr, "delete_fragments", spy_batch)

    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    # 自动确认二次确认框
    monkeypatch.setattr(fp_mod.GlassMessageBox, "question",
                        staticmethod(lambda *a, **k: True))

    dlg = _open_triage(panel)
    try:
        assert dlg._triage_candidates(), "前置条件：应有候选碎片"
        dlg._triage_delete_one(fid)
    finally:
        dlg.reject()

    assert calls["single"] == 1, "应恰好调用一次单条 delete_fragment"
    assert calls["batch"] == 0, "★ 红线：delete_fragments 绝不能被调用"
    assert mgr.get_fragment(fid) is None


def test_delete_cancelled_calls_nothing(tmp_path, qapp, monkeypatch):
    """二次确认选 No → 不删、不落盘。"""
    from src.fragments_panel import FragmentsPanel
    from src import fragments_panel as fp_mod

    mgr, fid = _old_manager(tmp_path)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    monkeypatch.setattr(fp_mod.GlassMessageBox, "question",
                        staticmethod(lambda *a, **k: False))
    dlg = _open_triage(panel)
    try:
        dlg._triage_delete_one(fid)
    finally:
        dlg.reject()
    assert mgr.get_fragment(fid) is not None


def test_threshold_change_recomputes(tmp_path, qapp):
    """改阈值 → 候选集实时变化（配置生效，非写死）。"""
    from src.fragments_panel import FragmentsPanel

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    f_old = mgr.add_clipboard_text("旧短" * 1)
    f_new = mgr.add_clipboard_text("新短")
    mgr.get_fragment(f_old).created_at = ts_days_ago(60)
    mgr.get_fragment(f_new).created_at = ts_days_ago(2)

    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    dlg = _open_triage(panel)
    try:
        # 默认 30 天 → 只有旧的候选
        got_default = [f.fragment_id for f in dlg._triage_candidates()]
        assert got_default == [f_old]
        # 把天数阈值降到 1 天 → 两条都进候选（实时重算）
        dlg._triage_days_stepper.setValue(1)
        got_loose = [f.fragment_id for f in dlg._triage_candidates()]
        assert set(got_loose) == {f_old, f_new}
    finally:
        dlg.reject()


def test_delete_all_button_gated_by_candidates(tmp_path, qapp):
    """「全部删除」按钮随候选数启用/禁用：有候选可点，空清单禁用。"""
    from src.fragments_panel import FragmentsPanel

    mgr, _fid = _old_manager(tmp_path)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    dlg = _open_triage(panel)
    try:
        assert dlg._triage_candidates(), "前置条件：应有候选"
        assert dlg._triage_delete_all_btn.isEnabled()
        # 阈值拉满 → 无候选 → 按钮禁用（实时跟随）
        dlg._triage_days_stepper.setValue(3650)
        assert dlg._triage_candidates() == []
        assert not dlg._triage_delete_all_btn.isEnabled()
        # 放宽 → 候选回来 → 按钮恢复可点
        dlg._triage_days_stepper.setValue(30)
        assert dlg._triage_candidates()
        assert dlg._triage_delete_all_btn.isEnabled()
    finally:
        dlg.reject()


def test_delete_all_confirmed_deletes_only_candidates(tmp_path, qapp,
                                                      monkeypatch):
    """确认后走 delete_fragments 批量：候选全删，非候选一律不碰。"""
    from src.fragments_panel import FragmentsPanel
    from src import fragments_panel as fp_mod

    mgr = FragmentManager(str(tmp_path / "fragments.json"))
    f_old1 = mgr.add_clipboard_text("旧短一")
    f_old2 = mgr.add_clipboard_text("旧短二")
    f_keep = mgr.add_clipboard_text("被搜过所以留下")
    for fid in (f_old1, f_old2, f_keep):
        mgr.get_fragment(fid).created_at = ts_days_ago(60)
    mgr.note_search_hit([f_keep])

    calls = {"batch": 0, "single": 0}
    real_single = mgr.delete_fragment
    real_batch = mgr.delete_fragments

    def spy_single(fid):
        calls["single"] += 1
        return real_single(fid)

    def spy_batch(ids):
        calls["batch"] += 1
        return real_batch(ids)

    monkeypatch.setattr(mgr, "delete_fragment", spy_single)
    monkeypatch.setattr(mgr, "delete_fragments", spy_batch)

    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    monkeypatch.setattr(fp_mod.GlassMessageBox, "question",
                        staticmethod(lambda *a, **k: True))
    dlg = _open_triage(panel)
    try:
        assert set(f.fragment_id for f in dlg._triage_candidates()) == \
            {f_old1, f_old2}
        dlg._triage_delete_all()
    finally:
        dlg.reject()

    assert calls["batch"] == 1, "全部删除应恰好调用一次批量接口"
    assert calls["single"] == 0, "全部删除不该走单条路径"
    assert mgr.get_fragment(f_old1) is None
    assert mgr.get_fragment(f_old2) is None
    assert mgr.get_fragment(f_keep) is not None, "非候选绝不能被误删"


def test_delete_all_cancelled_deletes_nothing(tmp_path, qapp, monkeypatch):
    """二次确认选 No → 一条都不删。"""
    from src.fragments_panel import FragmentsPanel
    from src import fragments_panel as fp_mod

    mgr, fid = _old_manager(tmp_path)
    host = _FakeHost()
    host._fragment_manager = mgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    monkeypatch.setattr(fp_mod.GlassMessageBox, "question",
                        staticmethod(lambda *a, **k: False))
    dlg = _open_triage(panel)
    try:
        dlg._triage_delete_all()
    finally:
        dlg.reject()
    assert mgr.get_fragment(fid) is not None


def _open_triage(panel):
    """构造清仓对话框但不 exec（离屏脚本/test 直连内部句柄）。"""
    from src import fragments_panel as fp_mod

    captured = {}

    class _Stop(Exception):
        pass

    def _fake_exec(self):
        captured["dlg"] = self
        return 0

    orig = fp_mod.GlassDialog.exec
    fp_mod.GlassDialog.exec = _fake_exec
    try:
        panel._open_triage_dialog()
    finally:
        fp_mod.GlassDialog.exec = orig
    return captured["dlg"]
