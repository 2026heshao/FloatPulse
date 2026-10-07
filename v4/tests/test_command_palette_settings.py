# -*- coding: utf-8 -*-
"""命令面板设置层护栏（2026-10-06 设置页「命令面板」卡）。

覆盖口径（任务书 F2 验收项逐条对应）：
  · sanitize_custom_actions   : 垃圾输入逐条过滤 / 重复 id 重排 / 上限 20 /
                                hotkey 非法置空（纯函数，不依赖 QApplication）；
  · sanitize_hotkeys_map      : 合法保留、无修饰键丢弃、非 dict 回 {}；
  · custom_action_entries     : cid / icon / category / keywords 正确；
  · build_registry(custom_actions=...) 含 custom.* 条目且
    validate_registry 零问题（注册表与护栏兼容）；
  · normalize_trigger         : 非法回落 "/"，三种合法值原样；
  · **防假护栏反向验证**       : 喂坏数据必须真的滤掉（清洗器坏掉时红灯）；
  · config 三件套             : 4 新键默认值 / 类型登记 / bool 不进 RANGES /
    触发键白名单同源（schema_version 与 STORE_VERSIONS 不动的回归钉子）；
  · 全局唤醒键（2026-10-06）  : _is_valid_wake_combo 骨架（默认 "Alt+/" 能
    过）/ 直达键与自定义动作查重对唤醒键生效 / 冲突拒绝后捕捉框还原 /
    include_wake=False 自身豁免 / config 默认值钉子。

单跑：
    cd v4 && python -m pytest tests/test_command_palette_settings.py -q
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
if V4 not in sys.path:
    sys.path.insert(0, V4)

from src import command_palette as cp          # noqa: E402
from src import constants                      # noqa: E402
from src.config import DEFAULT_CONFIG, _CONFIG_TYPES  # noqa: E402
from src.main_window import (                   # noqa: E402
    NAV_PAGE_INDEX, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
)
from src.command_palette_settings import (      # noqa: E402
    CAPTURE_EDIT_WIDTH, CommandKeysGrid, CustomActionsPanel,
    HotkeyCaptureEdit,
)

from PyQt6.QtCore import QEvent, QPointF, Qt    # noqa: E402
from PyQt6.QtGui import QFocusEvent, QFontMetrics, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication        # noqa: E402


# ====================================================================
# 1. sanitize_custom_actions
# ====================================================================
def _action(aid=1, atype="url", title="打开 GitHub",
            value="https://github.com", hotkey=""):
    return {"id": aid, "type": atype, "title": title, "value": value,
            "hotkey": hotkey}


class TestSanitizeCustomActions:
    def test_valid_list_passes_through(self):
        raw = [_action(1), _action(2, "folder", "打开 D 盘", "D:\\dir"),
               _action(3, "text", "复制邮箱", "me@example.com", "Ctrl+Alt+9")]
        cleaned = cp.sanitize_custom_actions(raw)
        assert len(cleaned) == 3
        assert cleaned[2]["hotkey"] == "Ctrl+Alt+9"

    def test_non_list_returns_empty(self):
        assert cp.sanitize_custom_actions(None) == []
        assert cp.sanitize_custom_actions("垃圾") == []
        assert cp.sanitize_custom_actions({"id": 1}) == []

    def test_bad_entries_dropped_individually(self):
        """非 dict / type 非法 / title 或 value 空（或非 str）逐条丢弃，
        好条目保留（不能一坏全坏）"""
        raw = [
            "not a dict",                                   # 非 dict
            {"id": 1, "type": "hack", "title": "x", "value": "y"},
            {"id": 1, "type": "url", "title": "", "value": "y"},
            {"id": 1, "type": "url", "title": "  ", "value": "y"},
            {"id": 1, "type": "url", "title": "t", "value": ""},
            {"id": 1, "type": "url", "title": "t", "value": 123},
            {"type": "url", "title": "t", "value": "v"},    # id 缺失 → 重排
            _action(2, "url", "好的", "https://ok.com"),
        ]
        cleaned = cp.sanitize_custom_actions(raw)
        assert len(cleaned) == 2
        assert cleaned[0]["title"] == "t"          # 缺 id 的被重排保留
        assert cleaned[1]["id"] == 2 and cleaned[1]["title"] == "好的"

    def test_missing_or_duplicate_ids_reassigned(self):
        """id 缺失或重复 → 重新分配（从现有 max+1 起，不撞已有 id）"""
        raw = [_action(5, "url", "A", "va"), _action(5, "url", "B", "vb"),
               _action("x", "url", "C", "vc")]
        cleaned = cp.sanitize_custom_actions(raw)
        ids = [a["id"] for a in cleaned]
        assert len(set(ids)) == 3                   # 全不重复
        assert 5 in ids                             # 首条保住原 id
        assert ids[1] == 6 and ids[2] == 7          # 从 max+1 起重排

    def test_invalid_hotkey_blankened(self):
        cleaned = cp.sanitize_custom_actions(
            [_action(1, hotkey="没有修饰键"), _action(2, hotkey=123),
             _action(3, hotkey="Ctrl+Alt+1")])
        assert cleaned[0]["hotkey"] == ""
        assert cleaned[1]["hotkey"] == ""
        assert cleaned[2]["hotkey"] == "Ctrl+Alt+1"

    def test_cap_at_20(self):
        raw = [_action(i + 1, "url", f"T{i}", "v") for i in range(25)]
        cleaned = cp.sanitize_custom_actions(raw)
        assert len(cleaned) == cp.MAX_CUSTOM_ACTIONS == 20

    def test_reverse_garbage_really_filtered(self):
        """反向验证：清洗函数喂纯坏数据必须真的滤掉（防假护栏）"""
        garbage = [_action(i + 1, "url", "", "") for i in range(5)]
        assert cp.sanitize_custom_actions(garbage) == []


# ====================================================================
# 2. sanitize_hotkeys_map / is_valid_combo
# ====================================================================
class TestSanitizeHotkeysMap:
    def test_valid_pairs_kept(self):
        raw = {"page.tasks": "Ctrl+Alt+1", "action.export": "Ctrl+Shift+F9",
               "custom.3": "Win+N"}
        assert cp.sanitize_hotkeys_map(raw) == raw

    def test_no_modifier_dropped(self):
        """无修饰键的组合整对丢弃（全局热键不许抢占普通按键）"""
        raw = {"page.tasks": "1", "action.export": "Ctrl+1"}
        assert cp.sanitize_hotkeys_map(raw) == {"action.export": "Ctrl+1"}

    def test_non_dict_returns_empty(self):
        assert cp.sanitize_hotkeys_map(None) == {}
        assert cp.sanitize_hotkeys_map([("a", "Ctrl+1")]) == {}
        assert cp.sanitize_hotkeys_map("Ctrl+1") == {}

    def test_bad_keys_and_values_dropped(self):
        raw = {"": "Ctrl+1", 42: "Ctrl+2", "ok": "Ctrl+3",
               "bad": "Ctrl+Shift+", "bad2": "Ctrl+Ctrl"}
        cleaned = cp.sanitize_hotkeys_map(raw)
        assert cleaned == {"ok": "Ctrl+3"}

    def test_f_keys_and_modifier_case_insensitive(self):
        assert cp.sanitize_hotkeys_map(
            {"a": "ctrl+alt+f12"}) == {"a": "ctrl+alt+f12"}
        assert cp.is_valid_combo("ctrl+shift+f24") is True
        assert cp.is_valid_combo("Ctrl+Shift+F25") is False

    def test_reverse_garbage_really_filtered(self):
        assert cp.sanitize_hotkeys_map(
            {"x": "A", "y": "Shift", "z": "Ctrl+"}) == {}


# ====================================================================
# 3. custom_action_entries / build_registry / validate_registry
# ====================================================================
class TestCustomEntries:
    def test_entries_fields(self):
        entries = cp.custom_action_entries([
            _action(7, "url", "打开 GitHub", "https://github.com"),
            _action(8, "folder", "打开 D 盘", "D:\\dir"),
            _action(9, "text", "复制邮箱", "me@example.com", "Ctrl+Alt+9"),
        ])
        assert [e.cid for e in entries] == ["custom.7", "custom.8",
                                            "custom.9"]
        assert [e.icon for e in entries] == ["nav", "folder", "clipboard"]
        assert all(e.category == cp.CATEGORY_CUSTOM for e in entries)
        assert [e.keywords for e in entries] == [("url",), ("folder",),
                                                 ("text",)]
        assert entries[0].target == ("custom", "7")

    def test_garbage_in_no_entries_out(self):
        assert cp.custom_action_entries(["垃圾", None, {}]) == []

    def test_build_registry_appends_custom_and_validates(self):
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX,
            custom_actions=[_action(1, "url", "打开 GitHub",
                                    "https://github.com", "Ctrl+Alt+1")])
        custom = [e for e in entries if e.cid.startswith("custom.")]
        assert len(custom) == 1
        assert custom[0].target == ("custom", "1")
        # 设置命令之后追加（任务书既定顺序）
        assert entries.index(custom[0]) > entries.index(next(
            e for e in entries if e.target[0] == "settings"))
        problems = cp.validate_registry(
            entries, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        assert problems == []

    def test_build_registry_without_custom_unchanged(self):
        base = cp.build_registry(NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
                                 NAV_PAGE_INDEX)
        assert not any(e.cid.startswith("custom.") for e in base)
        assert cp.build_registry(NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
                                 NAV_PAGE_INDEX, custom_actions=None) == base
        assert cp.build_registry(NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
                                 NAV_PAGE_INDEX, custom_actions=[]) == base

    def test_execute_entry_custom_dispatch(self):
        calls = []

        class _Host:
            def execute_custom_action(self, action_id):
                calls.append(action_id)
                return True

        entry = cp.CommandEntry(
            cid="custom.1", title="x", icon="nav",
            category=cp.CATEGORY_CUSTOM, target=("custom", "1"))
        assert cp.execute_entry(entry, _Host()) is True
        assert calls == ["1"]


# ====================================================================
# 4. normalize_trigger / TRIGGER_KEYS
# ====================================================================
class TestNormalizeTrigger:
    def test_valid_values_pass_through(self):
        for key in ("/", ";", "`"):
            assert cp.normalize_trigger(key) == key

    def test_invalid_falls_back_to_slash(self):
        assert cp.normalize_trigger("Ctrl+K") == "/"
        assert cp.normalize_trigger("") == "/"
        assert cp.normalize_trigger(None) == "/"
        assert cp.normalize_trigger("///") == "/"

    def test_trigger_keys_shape(self):
        assert constants.TRIGGER_KEYS == ("/", ";", "`")
        assert cp.TRIGGER_KEYS is constants.TRIGGER_KEYS   # re-export 同源


# ====================================================================
# 5. config 三件套回归钉子
# ====================================================================
class TestConfigTrio:
    def test_defaults_and_types(self):
        assert DEFAULT_CONFIG["command_palette_enabled"] is True
        assert DEFAULT_CONFIG["command_palette_trigger"] == "/"
        assert DEFAULT_CONFIG["command_hotkeys"] == {}
        assert DEFAULT_CONFIG["custom_actions"] == []
        # 全局唤醒键（2026-10-06）：默认 "Alt+/" 开箱即用（防手滑改默认的
        # 钉子；改默认值 = 改用户开箱体验，须显式过评审）
        assert DEFAULT_CONFIG["command_palette_global_hotkey"] == "Alt+/"
        assert _CONFIG_TYPES["command_palette_enabled"] is bool
        assert _CONFIG_TYPES["command_palette_trigger"] is str
        assert _CONFIG_TYPES["command_palette_global_hotkey"] is str
        assert _CONFIG_TYPES["command_hotkeys"] is dict
        assert _CONFIG_TYPES["custom_actions"] is list

    def test_bool_not_in_ranges_and_whitelist_tied(self):
        from src.config import _CONFIG_RANGES, _CONFIG_VALUE_WHITELISTS
        assert "command_palette_enabled" not in _CONFIG_RANGES
        assert _CONFIG_VALUE_WHITELISTS[
            "command_palette_trigger"] is constants.TRIGGER_KEYS
        # 唤醒键是自由形态组合键：既无数值范围也无有限合法集
        # （不进白名单——由 is_valid_combo / 注册层校验）
        assert "command_palette_global_hotkey" not in _CONFIG_RANGES
        assert "command_palette_global_hotkey" not in _CONFIG_VALUE_WHITELISTS

    def test_schema_versions_untouched(self):
        """不动 STORE_VERSIONS / schema_version（只加新键默认值的回归钉子）"""
        from src.json_store import STORE_VERSIONS
        assert DEFAULT_CONFIG["schema_version"] == 3
        assert STORE_VERSIONS["config"] == 3


# ====================================================================
# 6. HotkeyCaptureEdit 捕捉态治理 + 命令面板置顶（2026-10-06 修复）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _press_event():
    """构造真实左键 press 事件（直调 mousePressEvent 用；离屏下
    QTest.mouseClick 不稳定，构造事件最确定）。"""
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(5.0, 5.0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


class TestHotkeyCaptureEditCaptureState:
    """捕捉态治理三钉：失焦自动取消 / 全局单活跃 / 提示文案不截断。"""

    def test_focus_out_cancels_capture(self, qapp):
        """主修复路径：捕捉态下焦点被点走 → 还原旧值退出捕捉态
        （修复前旧框永远停在捕捉文案上）"""
        edit = HotkeyCaptureEdit()
        try:
            edit.set_combo("Ctrl+Alt+1")
            edit.mousePressEvent(_press_event())
            assert edit._capturing is True
            assert edit.text() == HotkeyCaptureEdit._CAPTURE_HINT
            edit.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
            assert edit._capturing is False
            assert edit.text() == "Ctrl+Alt+1"
            assert HotkeyCaptureEdit._active is None
        finally:
            edit.deleteLater()

    def test_single_active_edit_mutex(self, qapp):
        """同一时刻至多一个捕捉框：点新框时旧活跃框自动退出并还原"""
        e1 = HotkeyCaptureEdit()
        e2 = HotkeyCaptureEdit()
        try:
            e1.set_combo("Ctrl+Alt+1")
            e2.set_combo("Ctrl+Alt+2")
            e1.mousePressEvent(_press_event())
            assert HotkeyCaptureEdit._active is e1
            e2.mousePressEvent(_press_event())
            assert e1._capturing is False
            assert e1.text() == "Ctrl+Alt+1"       # 旧框还原旧值
            assert e2._capturing is True
            assert HotkeyCaptureEdit._active is e2
            e2._stop_capture()                     # 收尾清类级活跃位
            assert HotkeyCaptureEdit._active is None
        finally:
            e1.deleteLater()
            e2.deleteLater()

    def test_capture_hint_fits_fixed_width(self, qapp):
        """文案宽度护栏：两条提示文案都必须在定宽内完整显示（左右各留
        4px 余量，阈值 130 - 8 = 122px）。修复前「按下组合键…（Esc 取消）」
        被截断成「…（Esc」，改长文案或加宽字体档位都会在这里红灯。"""
        edit = HotkeyCaptureEdit()
        try:
            fm = QFontMetrics(edit.font())
            for hint in (HotkeyCaptureEdit._CAPTURE_HINT,
                         HotkeyCaptureEdit._NEED_MODIFIER_HINT):
                advance = fm.horizontalAdvance(hint)
                assert advance <= CAPTURE_EDIT_WIDTH - 8, (
                    f"提示文案会被 {CAPTURE_EDIT_WIDTH}px 定宽截断："
                    f"{hint!r} 实测 {advance}px")
        finally:
            edit.deleteLater()


class TestPaletteAlwaysOnTop:
    """命令面板窗口 flag 护栏：常置顶压过小卡片/钉屏等置顶工具窗。"""

    def test_window_flags_include_stays_on_top(self, qapp):
        """离屏真构造 CommandPalette（既有先例 test_command_palette.py
        同款），断言 windowFlags 真含 WindowStaysOnTopHint——只改源码
        字符串不改行为时这里会红。"""
        palette = cp.CommandPalette(None, None)
        try:
            assert bool(palette.windowFlags()
                        & Qt.WindowType.WindowStaysOnTopHint)
        finally:
            palette.deleteLater()


# ====================================================================
# 7. 全局唤醒键（2026-10-06）：校验器 / 冲突口径 / 还原路径
# ====================================================================
class _StubConfig:
    """最小 config 替身：get/set/save 面，落盘只计数不写盘。"""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


class _StubHost:
    """最小宿主替身：config + current_theme + show_toast 捕捉。"""

    current_theme = "light"

    def __init__(self, data=None):
        self.config = _StubConfig(data)
        self.toasts = []

    def show_toast(self, text, *args, **kwargs):
        self.toasts.append(text)


class TestGlobalWakeHotkey:
    """全局唤醒键护栏：默认值 Alt+/ 必须能过校验（is_valid_combo 只认
    字母数字末段，会把 "/" 误杀——唤醒键走专用的 _is_valid_wake_combo）；
    冲突口径对直达键 / 自定义动作双侧生效；冲突拒绝后捕捉框 text 复原。"""

    def test_wake_combo_validator(self):
        from src.command_palette_settings import _is_valid_wake_combo
        assert _is_valid_wake_combo("Alt+/") is True        # 默认值必须能过
        assert _is_valid_wake_combo("Ctrl+Alt+F12") is True
        assert _is_valid_wake_combo("Win+K") is True
        assert _is_valid_wake_combo("ctrl+shift+、") is True  # 任意单字符
        assert _is_valid_wake_combo("A") is False           # 无修饰键
        assert _is_valid_wake_combo("Shift") is False       # 纯修饰键
        assert _is_valid_wake_combo("") is False
        assert _is_valid_wake_combo("   ") is False
        assert _is_valid_wake_combo(None) is False
        assert _is_valid_wake_combo(123) is False
        assert _is_valid_wake_combo("Ctrl+Shift+") is False  # 末段缺失
        assert _is_valid_wake_combo("Hyper+1") is False      # 修饰键白名单外
        assert _is_valid_wake_combo("Alt+F25") is False      # F 键越界

    def test_is_valid_combo_still_rejects_slash(self):
        """直达键口径不松：is_valid_combo("Alt+/") 保持 False（松了会破坏
        sanitize_hotkeys_map / sanitize_custom_actions 的既有护栏）"""
        assert cp.is_valid_combo("Alt+/") is False
        assert cp.is_valid_combo("Alt+P") is True

    def test_direct_key_capture_rejected_by_wake(self, qapp):
        """冲突口径：直达键查重对全局唤醒键生效——config 里唤醒键为
        Ctrl+Alt+J 时，给命令绑 Ctrl+Alt+J 必须被拒（config 不落盘 +
        toast 点名冲突方）。注：场景须用直达键口径合法的组合键——默认
        "Alt+/" 过不了 cp.is_valid_combo，直达键侧在冲突检查前就被静默
        还原（唤醒键与直达键天然无 "/" 类冲突面）"""
        host = _StubHost({"command_palette_global_hotkey": "Ctrl+Alt+J",
                          "command_hotkeys": {}})
        grid = CommandKeysGrid(host)
        try:
            edit = grid._edits["page.tasks"]
            grid._on_captured("page.tasks", edit, "Ctrl+Alt+J")
            assert host.config.data.get("command_hotkeys", {}) == {}
            assert host.config.saved == 0
            assert any("冲突" in t and "全局唤醒键" in t
                       for t in host.toasts)
        finally:
            grid.deleteLater()

    def test_direct_key_capture_restore_on_conflict(self, qapp):
        """冲突拒绝后捕捉框 text 复原（还原路径：edit 显示回旧值）"""
        host = _StubHost({"command_palette_global_hotkey": "Ctrl+Alt+J",
                          "command_hotkeys": {"page.tasks": "Ctrl+Alt+1"}})
        grid = CommandKeysGrid(host)
        try:
            edit = grid._edits["page.tasks"]
            grid._on_captured("page.tasks", edit, "Ctrl+Alt+J")
            assert edit.text() == "Ctrl+Alt+1"
        finally:
            grid.deleteLater()

    def test_custom_action_conflict_with_wake(self, qapp):
        """冲突口径：自定义动作表单直达键查重对全局唤醒键生效"""
        host = _StubHost({"command_palette_global_hotkey": "Ctrl+Alt+J"})
        panel = CustomActionsPanel(host)
        try:
            actions = [{"id": 1, "type": "url", "title": "打开 GitHub",
                        "value": "https://github.com", "hotkey": ""}]
            assert panel._hotkey_conflict(actions, "Ctrl+Alt+J") \
                == "全局唤醒键"
            assert panel._hotkey_conflict(actions, "Ctrl+Alt+L") is None
        finally:
            panel.deleteLater()

    def test_wake_capture_not_conflicted_with_itself(self, qapp):
        """唤醒键自身捕获走 include_wake=False：与既有值同键不构成冲突
        （否则重绑同一个键会被「与『全局唤醒键』冲突」拒绝）"""
        host = _StubHost({"command_palette_global_hotkey": "Alt+/",
                          "command_hotkeys": {}})
        grid = CommandKeysGrid(host)
        try:
            assert grid._find_conflict("", "alt+/",
                                       include_wake=False) is None
            assert grid._find_conflict("", "alt+/") == "全局唤醒键"
        finally:
            grid.deleteLater()
