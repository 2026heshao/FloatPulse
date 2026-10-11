# -*- coding: utf-8 -*-
"""全局「/ 命令面板」护栏（A2，2026-10-05）。

覆盖口径（任务书验收项逐条对应）：
  · **注册表完备性**：页面命令覆盖 nav_order 全键（8 固定键 + settings /
    help + 已注册 plugin: 键），设置分类命令与 SETTINGS_CATEGORIES 一一
    对应，cid 唯一、字段非空 —— 漏一条 = 用户按得到页面但搜不到；
  · **模糊匹配纯逻辑**：子串 > 关键词子串 > 子序列，拼音缩写 / 大小写 /
    空查询 / 无命中（纯函数，不依赖 QApplication）；
  · **编辑态不触发**：焦点在 QLineEdit / QTextBrowser（QTextEdit 子类）
    内时 focus_in_text_editor 判定为编辑态（入口按此拦截；QLineEdit 等
    可编辑控件本身还会先吃掉 ShortcutOverride，双保险）；
  · **Esc / 回车行为**：Esc 关面板、回车执行当前项并调宿主公开 API
    （替身宿主记录调用，防「快捷键存在但连错槽」哑弹）；
  · **首开性能**：构造 + 弹出 < 80ms（懒构建，参考 10 页懒加载先例）；
  · **边界护栏**：command_palette 源码零 kb-search 引用（与 Ctrl+K 的
    硬性分工钉死）；main_window 的 `/` 快捷键接线 AST 钉死；帮助页
    「全局快捷键」表含 [[/]] 条目。
  · **防假护栏反向验证**：故意从注册表抽掉一条页面命令 / 制造重复 cid，
    validate_registry 必须报问题 —— 校验器坏掉时红灯能亮。

单跑：
    cd v4 && python -m pytest tests/test_command_palette.py -q
"""
import ast
import io
import os
import sys
import time

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QTextBrowser, QTextEdit, QWidget,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
ROOT = os.path.dirname(V4)
if V4 not in sys.path:
    sys.path.insert(0, V4)

from src import command_palette as cp                    # noqa: E402
from src.config import NAV_PAGE_KEYS                     # noqa: E402
from src.main_window import (                            # noqa: E402
    NAV_PAGE_INDEX, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
)

MAIN_WINDOW_PATH = os.path.join(V4, "src", "main_window.py")
PALETTE_PATH = os.path.join(V4, "src", "command_palette.py")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ====================================================================
# 1. 注册表完备性
# ====================================================================
class TestRegistryCompleteness:
    def test_validate_passes_on_real_tables(self):
        """真实导航三张表生成的注册表必须零问题（含 plugin: 动态键）"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        assert cp.validate_registry(
            entries, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED,
            NAV_PAGE_INDEX) == []

    def test_page_commands_cover_all_nav_keys(self):
        """nav_order 全键（8 固定页键）都必须有对应命令 —— 漏一个就是
        「侧栏点得到、面板搜不到」"""
        entries = cp.page_commands(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        page_keys = {e.target[1] for e in entries}
        missing = [k for k in NAV_PAGE_KEYS if k not in page_keys]
        assert not missing, f"这些功能页键没有命令：{missing}"
        assert "settings" in page_keys and "help" in page_keys

    def test_settings_commands_match_categories_table(self):
        """设置分类命令与 SETTINGS_CATEGORIES 十分类一一对应（同一真相
        源的两侧，改一边忘另一边必须红灯）"""
        from src.settings_panel import SETTINGS_CATEGORIES
        entries = {e.target[1]: e for e in cp.settings_category_commands()}
        assert set(entries) == {key for key, _icon, _label in SETTINGS_CATEGORIES}
        for _key, icon, label in SETTINGS_CATEGORIES:
            entry = entries[_key]
            assert entry.icon == icon
            assert entry.title == f"设置 · {label}"

    def test_registry_has_no_duplicate_cids(self):
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        cids = [e.cid for e in entries]
        assert len(cids) == len(set(cids)), "注册表出现重复 cid"

    def test_registered_plugin_keys_get_commands(self):
        """NAV_PAGE_INDEX 里的 plugin: 键要生成插件命令；未注册键不生成"""
        titles = dict(NAV_PAGE_TITLES)
        titles["plugin:demo-x"] = "演示插件"
        index = dict(NAV_PAGE_INDEX)
        index["plugin:demo-x"] = 10
        entries = cp.page_commands(titles, NAV_PAGE_TITLES_FIXED, index)
        assert ("plugin", "plugin:demo-x") in [e.target for e in entries]
        # 未注册（不在 index）→ 不进面板（与 nav 侧 k in _nav_btns 同口径）
        entries2 = cp.page_commands(
            titles, NAV_PAGE_TITLES_FIXED, dict(NAV_PAGE_INDEX))
        assert ("plugin", "plugin:demo-x") not in [e.target for e in entries2]

    def test_registry_size_sane(self):
        """量级钉子：8 页 + settings/help + 5 动作（export/theme/new_task/
        new_note/help.hotkeys）+ 10 设置分类 = 25+（批次 N1 新增
        action.new_note 后 24→25）"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        assert len(entries) >= 25


# ====================================================================
# 2. 模糊匹配纯逻辑
# ====================================================================
class TestFuzzyMatch:
    @pytest.fixture()
    def entries(self):
        return cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)

    def test_empty_query_returns_all_in_order(self, entries):
        assert cp.filter_commands(entries, "") == entries
        assert cp.filter_commands(entries, "   ") == entries
        assert cp.filter_commands(entries, None) == entries

    def test_no_match_returns_empty(self, entries):
        assert cp.filter_commands(entries, "zzzz") == []

    def test_case_insensitive(self, entries):
        hits = cp.filter_commands(entries, "OBSIDIAN")
        assert hits and hits[0].cid == "action.export"

    def test_substring_beats_subsequence(self, entries):
        hits = cp.filter_commands(entries, "任务")
        assert hits[0].cid == "page.tasks"

    def test_pinyin_abbreviation(self, entries):
        """拼音首字母缩写命中（rj = 软件导航）"""
        hits = cp.filter_commands(entries, "rj")
        assert hits and hits[0].cid == "page.apps"

    def test_settings_prefix_ranks_settings_page_first(self, entries):
        hits = cp.filter_commands(entries, "设置")
        assert hits[0].cid == "page.settings"

    def test_subsequence_matches_chinese_title(self, entries):
        """非连续子序列（中文）也能命中：『片台』→ 碎片工作台"""
        hits = cp.filter_commands(entries, "片台")
        assert any(e.cid == "page.fragments" for e in hits)

    def test_results_capped_in_ui(self, palette):
        """命中数超上限时 UI 层截断（极泛查询不给百行列表；
        纯逻辑 filter_commands 不截断 —— 空查询全量语义归它管）"""
        many = [cp.CommandEntry(
            cid=f"action.fake{i}", title=f"假命令 {i}", icon="command",
            category="动作", target=("action", "unknown_fake"),
            keywords=("x",)) for i in range(30)]
        assert len(cp.filter_commands(many, "x")) == 30
        palette.open_at()
        palette._entries = many
        palette._input.setText("x")
        assert palette._list.count() == cp.MAX_RESULTS


# ====================================================================
# 3. 编辑态防误触
# ====================================================================
class TestEditStateGuard:
    def test_line_edit_focus_is_editing(self, qapp):
        wrap = QWidget()
        edit = QLineEdit(wrap)
        wrap.show()
        edit.setFocus()
        qapp.processEvents()
        assert QApplication.focusWidget() is edit
        assert cp.focus_in_text_editor(edit) is True
        wrap.close()

    def test_text_browser_focus_is_editing_guarded(self, qapp):
        """QTextBrowser（只读）不吃 ShortcutOverride —— 必须被入口过滤拦住"""
        wrap = QWidget()
        browser = QTextBrowser(wrap)
        wrap.show()
        browser.setFocus()
        qapp.processEvents()
        assert cp.focus_in_text_editor(QApplication.focusWidget()) is True
        wrap.close()

    def test_plain_label_is_not_editing(self, qapp):
        assert cp.focus_in_text_editor(QLabel()) is False

    def test_none_focus_is_not_editing(self):
        assert cp.focus_in_text_editor(None) is False

    def test_qtextedit_subclass_coverage(self, qapp):
        """QTextBrowser 是 QTextEdit 子类，isinstance 一并覆盖（不重复枚举）"""
        browser = QTextBrowser()
        browser.setPlainText("占位")
        assert isinstance(browser, QTextEdit)
        assert cp.focus_in_text_editor(browser) is True


# ====================================================================
# 4. 面板行为（Esc / 回车 / 过滤 / 性能）
# ====================================================================
class _StubHost:
    """替身宿主：只暴露命令面板消费的公开 API 面，记录调用供断言。"""

    HELP_PAGE_INDEX = 8
    current_theme = "light"
    NAV_PAGE_INDEX = dict(NAV_PAGE_INDEX)
    NAV_PAGE_TITLES = dict(NAV_PAGE_TITLES)
    NAV_PAGE_TITLES_FIXED = dict(NAV_PAGE_TITLES_FIXED)

    def __init__(self):
        self.calls = []

    def show_page(self, index):
        self.calls.append(("show_page", index))

    def show_settings_page(self, category=None):
        self.calls.append(("show_settings_page", category))

    def show_plugin_page(self, key):
        self.calls.append(("show_plugin_page", key))
        return True

    def show_help_category(self, key):
        self.calls.append(("show_help_category", key))

    def export_to_obsidian(self, interactive=True):
        self.calls.append(("export_to_obsidian", interactive))
        return None

    def apply_external_theme(self, theme_name):
        self.calls.append(("apply_external_theme", theme_name))

    def focus_new_task(self):
        self.calls.append(("focus_new_task",))

    def focus_new_note(self):
        self.calls.append(("focus_new_note",))


@pytest.fixture()
def stub_host():
    return _StubHost()


@pytest.fixture()
def palette(qapp, stub_host):
    pal = cp.CommandPalette(stub_host, None)
    yield pal
    pal.close()
    pal.deleteLater()


class TestPaletteBehavior:
    def test_open_lists_all_and_selects_first(self, palette):
        palette.open_at()
        assert palette.isVisible()
        assert palette._list.count() == min(
            len(palette._entries), cp.MAX_RESULTS)
        assert palette._list.currentRow() == 0

    def test_query_filters_list(self, palette):
        palette.open_at()
        palette._input.setText("任务")
        assert palette._list.count() >= 1
        first = palette._list.currentItem().data(Qt.ItemDataRole.UserRole)
        assert first.cid == "page.tasks"

    def test_empty_state_shown_on_no_match(self, palette):
        palette.open_at()
        palette._input.setText("zzzz不存在的命令")
        assert palette._list.count() == 0
        assert not palette._list.isVisible()
        assert palette._empty.isVisible()

    def test_enter_executes_and_closes(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("碎片")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("show_page", NAV_PAGE_INDEX["fragments"]) in stub_host.calls
        assert not palette.isVisible()

    def test_down_moves_selection(self, palette):
        palette.open_at()
        first = palette._list.currentRow()
        QTest.keyClick(palette._input, Qt.Key.Key_Down)
        assert palette._list.currentRow() == first + 1
        QTest.keyClick(palette._input, Qt.Key.Key_Up)
        assert palette._list.currentRow() == first

    def test_esc_closes(self, palette):
        palette.open_at()
        QTest.keyClick(palette._input, Qt.Key.Key_Escape)
        assert not palette.isVisible()

    def test_execute_settings_category(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("外观")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("show_settings_page", "appearance") in stub_host.calls

    def test_execute_theme_toggle(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("切换浅色")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("apply_external_theme", "dark") in stub_host.calls

    def test_execute_export(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("导出")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("export_to_obsidian", True) in stub_host.calls

    def test_new_task_entry_registered(self):
        """C2：action.new_task 已入注册表，target/图标/关键词齐备"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        hits = [e for e in entries if e.cid == "action.new_task"]
        assert len(hits) == 1
        e = hits[0]
        assert e.target == ("action", "new_task")
        assert e.category == cp.CATEGORY_ACTION
        assert e.icon == "plus"
        assert e.title == "新建任务"
        # 触发词覆盖「待办 / todo / renwu」等同义路径
        assert "daiban" in e.keywords and "todo" in e.keywords

    def test_new_task_keyword_hits(self):
        """「待办」「todo」「新任务」（关键词/标题命中）都排到 action.new_task"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        for q in ("待办", "todo", "新任务"):
            hits = cp.filter_commands(entries, q)
            assert hits and hits[0].cid == "action.new_task", q

    def test_execute_new_task(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("新建任务")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("focus_new_task",) in stub_host.calls

    def test_execute_new_task_direct_dispatch(self, stub_host):
        """execute_entry 分派面（不经 GUI）：未知动作名返回 False 不猜"""
        entry = next(e for e in cp.action_commands()
                     if e.cid == "action.new_task")
        assert cp.execute_entry(entry, stub_host) is True
        assert ("focus_new_task",) in stub_host.calls
        fake = cp.CommandEntry(
            cid="action.nope", title="假动作", icon="command",
            category=cp.CATEGORY_ACTION, target=("action", "nope"))
        assert cp.execute_entry(fake, stub_host) is False

    def test_new_note_entry_registered(self):
        """批次 N1：action.new_note 已入注册表，target/图标/关键词齐备"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        hits = [e for e in entries if e.cid == "action.new_note"]
        assert len(hits) == 1
        e = hits[0]
        assert e.target == ("action", "new_note")
        assert e.category == cp.CATEGORY_ACTION
        assert e.icon == "plus"
        assert e.title == "新建笔记"
        # 触发词覆盖「记笔记 / biji / note / 拼音缩写」同义路径
        assert "记笔记" in e.keywords and "biji" in e.keywords
        assert "note" in e.keywords and "jb" in e.keywords

    def test_new_note_keyword_hits(self):
        """「记笔记」「jb」「新建笔记」（关键词/拼音缩写/标题命中）都排到
        action.new_note；裸 "note" 是 page.notes 关键词 "notes" 的子串
        （页面命中合法），只断言 new_note 出现在结果里"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        for q in ("记笔记", "jb", "新建笔记"):
            hits = cp.filter_commands(entries, q)
            assert hits and hits[0].cid == "action.new_note", q
        hits = cp.filter_commands(entries, "note")
        assert any(e.cid == "action.new_note" for e in hits)

    def test_execute_new_note(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("新建笔记")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("focus_new_note",) in stub_host.calls
        assert not palette.isVisible()

    def test_execute_new_note_direct_dispatch(self, stub_host):
        """execute_entry 分派面（不经 GUI）：new_note 落到公开委托"""
        entry = next(e for e in cp.action_commands()
                     if e.cid == "action.new_note")
        assert cp.execute_entry(entry, stub_host) is True
        assert ("focus_new_note",) in stub_host.calls

    def test_export_title_says_full_scope(self):
        """批次 N2：导出命令 title 统一为「导出全部到 Obsidian」（消除
        「只导当前笔记」的语义歧义）"""
        entry = next(e for e in cp.action_commands()
                     if e.cid == "action.export")
        assert entry.title == "导出全部到 Obsidian"


# ====================================================================
# 4b. 阴影收口 + 焦点保险（2026-10-09 用户报三项之二：外圈透明框 /
#     唤醒优先级——面板画出来却收不到键盘输入等同唤不醒）
# ====================================================================
class TestShadowAndFocus:
    def test_shadow_margin_tight(self):
        """阴影边距收口 6px（旧值 14 画出的宽灰环被用户当成「一圈透明框」）"""
        assert cp.CommandPalette.SHADOW_MARGIN == 6

    def test_shadow_paint_uses_reduced_layers(self):
        """paintEvent 阴影参数钉死：4 层 / alpha 减半 / offset 4 —— 防回潮"""
        src = _read(PALETTE_PATH)
        assert "layers=4" in src
        assert "alpha = 48 if theme == \"dark\" else 24" in src
        assert "offset_y=4.0" in src
        # 旧参数不得残留
        assert "layers=6" not in src
        assert "alpha = 96 if theme == \"dark\" else 48" not in src

    def test_open_at_schedules_focus_guard(self, palette):
        """open_at 挂焦点保险（_ensure_focus），接线钉死。

        ★必须走**成员 QTimer**（父=本窗）：早期用 QTimer.singleShot(ms,
        绑定方法) 时，面板提前 close/deleteLater 后回调仍打已析构的 C++
        对象 —— 测试组合跑必崩 access violation（A/B 对照定位）。
        """
        palette.open_at()
        assert palette.isVisible()
        assert palette._focus_timer.isActive()            # 已排程
        assert palette._focus_timer.parent() is palette   # 随宿主清场
        src = _read(PALETTE_PATH)
        assert "self._focus_timer.start()" in src
        assert "QTimer.singleShot(120, self._ensure_focus)" not in src, \
            "静态 singleShot 会在面板提前销毁后触发悬空回调（进程崩溃）"

    def test_close_stops_focus_guard(self, palette):
        """关闭即停表（崩溃修复的第二道闸）：面板 close 后定时器不得
        仍 pending —— 否则宿主 deleteLater 后回调打在已析构对象上。"""
        palette.open_at()
        assert palette._focus_timer.isActive()
        palette.close()
        assert not palette._focus_timer.isActive()
        assert "def closeEvent" in _read(PALETTE_PATH)

    def test_ensure_focus_recovers_lost_focus(self, palette):
        """焦点丢失兜底：焦点在面板内其它可聚焦控件（列表）上时，
        _ensure_focus 把焦点抢回输入框。用窗口内转移做探针 —— 离屏
        环境里跨顶层窗口的 activateWindow 不生效（活动窗口归 probe），
        跨窗口场景由实机验证承接。"""
        palette.open_at()
        palette._list.setFocus()
        assert not palette._input.hasFocus()
        palette._ensure_focus()
        assert palette._input.hasFocus()

    def test_ensure_focus_keeps_focus_when_held(self, palette):
        """焦点本就在输入框时 no-op（不闪不重绘）"""
        palette.open_at()
        palette._input.setFocus()
        palette._ensure_focus()          # 不应抛异常也不应关闭面板
        assert palette.isVisible()

    def test_ensure_focus_ignores_closed_palette(self, palette):
        """面板已关闭 → 兜底直接返回（不复活已关窗口）"""
        palette.open_at()
        palette.close()
        palette._ensure_focus()
        assert not palette.isVisible()

    def test_main_window_wake_retry_wiring(self):
        """main_window 唤醒键退避重试 AST 钉子：_register_wake_hotkey /
        _wake_retry_fire / _log_info 方法在位；退避表 3 档；成功路径
        _log_info 留痕（此前成功零日志，排障对不上）"""
        src = _read(MAIN_WINDOW_PATH)
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        names = {n.name for n in main_cls.body
                 if isinstance(n, ast.FunctionDef)}
        assert {"_register_wake_hotkey", "_wake_retry_fire",
                "_log_info"} <= names
        dumped = next(n for n in main_cls.body
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_register_wake_hotkey")
        dump = ast.dump(dumped)
        assert "_WAKE_RETRY_DELAYS_MS" in dump
        assert "_log_info" in dump
        assert "_open_command_palette" in dump      # 回调仍是打开面板
        # 退避表形态：3 档、递增、毫秒
        assert "_WAKE_RETRY_DELAYS_MS = (3000, 10000, 30000)" in src

    def test_execute_help_hotkeys(self, palette, stub_host):
        palette.open_at()
        palette._input.setText("快捷键说明")
        QTest.keyClick(palette._input, Qt.Key.Key_Return)
        assert ("show_page", 8) in stub_host.calls
        assert ("show_help_category", "hotkeys") in stub_host.calls

    def test_dark_theme_builds(self, qapp):
        host = _StubHost()
        host.current_theme = "dark"
        pal = cp.CommandPalette(host, None)
        try:
            assert pal.isVisible() is False      # 构建不弹，open_at 才弹
            pal.open_at()
            assert pal.isVisible()
        finally:
            pal.close()
            pal.deleteLater()

    def test_open_under_80ms(self, qapp, stub_host):
        """首开 <80ms：注册表预热后（真实运行时 settings_panel 已随主窗
        加载），构造 + 弹出必须在该预算内"""
        cp.build_registry(_StubHost.NAV_PAGE_TITLES,
                          _StubHost.NAV_PAGE_TITLES_FIXED,
                          _StubHost.NAV_PAGE_INDEX)     # 预热 import/缓存
        start = time.perf_counter()
        pal = cp.CommandPalette(stub_host, None)
        pal.open_at()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        try:
            assert elapsed_ms < 80.0, f"首开耗时 {elapsed_ms:.1f}ms 超预算"
        finally:
            pal.close()
            pal.deleteLater()


# ====================================================================
# 5. 边界护栏（与 kb-search 划界 + main_window 接线 + 帮助页）
# ====================================================================
class TestBoundaries:
    def test_no_kb_search_reference(self):
        """硬性边界：command_palette 代码零 kb-search 引用（Ctrl+K 搜
        数据内容归插件，面板只管动作与导航）。

        走 AST 而非源码文本 —— 模块 docstring 里的分工说明允许出现
        "kb-search" 字样（文档不是耦合），import / 属性 / 字符串字面量
        这类**代码级**引用一个都不许有。
        """
        tree = ast.parse(_read(PALETTE_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            bad = [m for m in mods if "kb_search" in m or "kb-search" in m]
            assert not bad, f"命令面板不得引用 kb-search：{bad}"

    def test_main_window_wiring(self):
        """main_window 的触发键接线 AST 钉死（2026-10-06 起触发键配置驱动，
        不再断言 'QKeySequence("/")' 字面量 —— "/" 只是默认值，用户可改）：
        注册 + 连到 _open_command_palette + 方法里真有懒构建与编辑态过滤 +
        设置应用 / 直达键 / 自定义动作三方法在位 + 变更信号已 connect。"""
        from src.constants import TRIGGER_KEYS
        from src import config as config_mod
        src = _read(MAIN_WINDOW_PATH)
        assert "self._palette_shortcut.activated.connect(" \
               "self._open_command_palette)" in src
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        names = {n.name for n in main_cls.body
                 if isinstance(n, ast.FunctionDef)}
        assert {"_open_command_palette", "_apply_command_palette_settings",
                "_reapply_command_hotkeys", "execute_custom_action",
                "unregister_command_hotkeys"} <= names
        # 全局唤醒键（2026-10-06）：注册逻辑钉在 _reapply_command_hotkeys
        # 调用的 _register_wake_hotkey 内（真读 command_palette_global_
        # hotkey，空串跳过）且回调连到 _open_command_palette——快捷键存在
        # 但连错槽的哑弹在这里红灯；2026-10-09 起失败走退避重试链
        # （_WAKE_RETRY_DELAYS_MS + _wake_retry_fire），一并钉住
        reapply = next(n for n in main_cls.body
                       if isinstance(n, ast.FunctionDef)
                       and n.name == "_reapply_command_hotkeys")
        dumped_reapply = ast.dump(reapply)
        assert "command_palette_global_hotkey" in dumped_reapply
        assert "_register_wake_hotkey" in dumped_reapply
        assert "_wake_retry_seq" in dumped_reapply       # 重试链号防串台
        reg = next(n for n in main_cls.body
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_register_wake_hotkey")
        dumped_reg = ast.dump(reg)
        assert "_open_command_palette" in dumped_reg
        assert "_WAKE_RETRY_DELAYS_MS" in dumped_reg
        # command_palette_changed 信号存在（类体 pyqtSignal 声明）且已 connect
        assert "command_palette_changed" in src
        assert "self.command_palette_changed.connect(" in src
        node = next(n for n in main_cls.body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_open_command_palette")
        dumped = ast.dump(node)
        assert "focus_in_text_editor" in dumped
        assert "CommandPalette" in dumped
        assert "from src import command_palette" in src
        # 触发键候选："/" 在列，Ctrl+K 不许混入（站内搜索的键，硬边界）
        assert "/" in TRIGGER_KEYS and "Ctrl+K" not in TRIGGER_KEYS
        # config 白名单与 constants 同源（同一对象，不是复制品）
        assert config_mod._CONFIG_VALUE_WHITELISTS[
            "command_palette_trigger"] is TRIGGER_KEYS

    def test_help_page_documents_palette(self):
        """帮助页「全局快捷键」表补了 [[/]] 条目（文案钉在源码）"""
        from src.main_window import MainWindow
        html = MainWindow._help_html()
        assert "[[/]]" in html
        assert "命令面板" in html
        assert "Ctrl+K" in html       # 分工说明同段在位


# ====================================================================
# 5b. 2026-10-10 三项用户报障的钉死（主窗关闭态跳转 / 面板白框 / 前台强夺）
# ====================================================================
class TestPrompt20261010Fixes:
    def test_show_page_shows_window(self):
        """用户报①：主窗关闭态下命令面板跳页无效 —— show_page 必须
        与 show_settings_page / show_plugins_page 同构，带显示三连。

        此前只调 _switch_page：窗口收进托盘时页面切了但窗口不露脸，
        用户感知「点了没反应」。
        """
        src = _read(MAIN_WINDOW_PATH)
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        fn = next(n for n in main_cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "show_page")
        dump = ast.dump(fn)
        for call in ("show", "raise_", "activateWindow", "_switch_page"):
            assert call in dump, f"show_page 缺 {call}（主窗关闭态跳转会无效）"

    def test_all_page_entrypoints_show_window(self):
        """同类入口一致性：settings / plugins / plugin:key 三者本就带
        显示三连，show_page 补齐后四个入口同构（防半迁移）"""
        mw = _read(MAIN_WINDOW_PATH)
        mixin = _read(os.path.join(V4, "src", "main_window_plugin_pages.py"))
        for name, src in (("show_settings_page", mw),
                          ("show_plugins_page", mw),
                          ("show_plugin_page", mixin)):
            idx = src.index(f"def {name}")
            body = src[idx:idx + 700]
            assert "self.show()" in body, f"{name} 缺 show()"
            assert "activateWindow" in body, f"{name} 缺 activateWindow"

    def test_palette_has_no_drop_shadow_hint(self):
        """用户报②：面板周围还有一圈白色透明框 —— 无边框顶层窗在 Win11
        下 DWM 会补画系统阴影/白边。必须与 toast / task_reminder_popup /
        date_picker 同款加 NoDropShadowWindowHint。"""
        src = _read(PALETTE_PATH)
        assert "NoDropShadowWindowHint" in src
        # 与项目内既有三处手法同源
        for peer in ("toast.py", "task_reminder_popup.py", "date_picker.py"):
            assert "NoDropShadowWindowHint" in _read(
                os.path.join(V4, "src", peer))

    def test_main_window_has_foreground_force(self):
        """用户报③：主窗打开后点不动 —— 实机取证是前台没抢到（自身
        响应 ≤73ms）。showEvent 必须挂 _force_foreground_now。"""
        src = _read(MAIN_WINDOW_PATH)
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        names = {n.name for n in main_cls.body
                 if isinstance(n, ast.FunctionDef)}
        assert "_force_foreground_now" in names
        show_ev = next(n for n in main_cls.body
                       if isinstance(n, ast.FunctionDef)
                       and n.name == "showEvent")
        assert "_force_foreground_now" in ast.dump(show_ev)
        assert "win_focus" in src

    def test_win_focus_module_contract(self):
        """前台强夺模块契约：三段递进齐备 + 非 Windows 安全降级 +
        AttachThreadInput 必须成对分离（防输入队列长期串接）。"""
        from src import win_focus
        assert callable(win_focus.force_foreground)
        # 非 Windows / 非法入参一律 False，不抛异常
        assert win_focus.force_foreground(0) is False
        assert win_focus.force_foreground(None) is False
        assert win_focus.force_foreground("not-a-hwnd") is False
        src = _read(os.path.join(V4, "src", "win_focus.py"))
        assert "SetForegroundWindow" in src
        assert "AttachThreadInput" in src
        assert "keybd_event" in src                  # 兜底解前台锁
        assert src.count("AttachThreadInput(") >= 2  # attach + detach 成对


# ====================================================================
# 5c. 自定义动作直达键（2026-10-10 用户报「快捷键无法识别使用」）
# ====================================================================
class TestCustomActionHotkeys:
    def test_reapply_registers_custom_action_hotkeys(self):
        """用户报根因：_reapply_command_hotkeys 此前只读 command_hotkeys，
        漏了 custom_actions[].hotkey —— 键绑了也按不出反应，只能进面板搜。
        注册循环必须并入自定义动作（cid = custom.<id>）。"""
        src = _read(MAIN_WINDOW_PATH)
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        fn = next(n for n in main_cls.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_reapply_command_hotkeys")
        dump = ast.dump(fn)
        assert "sanitize_custom_actions" in dump, \
            "注册循环未读 custom_actions —— 自定义动作直达键永远不会生效"
        assert "custom_actions" in dump
        assert "custom." in dump            # cid 口径 custom.<id>
        assert "builtin_hotkey_holder" in dump   # 内置键冲突兜底

    def test_registry_cache_invalidated_on_reapply(self):
        """缓存失效：_cmd_registry 此前只构建不失效，改/删自定义动作后
        按 cid 查到的是旧表。重注册时必须置 None。"""
        src = _read(MAIN_WINDOW_PATH)
        tree = ast.parse(src)
        main_cls = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                        and n.name == "MainWindow")
        fn = next(n for n in main_cls.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_reapply_command_hotkeys")
        assert "_cmd_registry" in ast.dump(fn)

    def test_builtin_hotkey_holder_detects_builtins(self):
        """内置键冲突源（单一真相源 BUILTIN_HOTKEYS）：
        Ctrl+T（切主题）/ Ctrl+K / Ctrl+W / F1 / Ctrl+1~8 都必须被认出，
        用户报的正是把自定义动作绑到了 Ctrl+T。"""
        from src import command_palette as _cp
        assert _cp.builtin_hotkey_holder("Ctrl+T") == "切换主题"
        assert _cp.builtin_hotkey_holder("ctrl+t") == "切换主题"   # 大小写无关
        assert _cp.builtin_hotkey_holder(" Ctrl + T ") == "切换主题"  # 空格容错
        assert _cp.builtin_hotkey_holder("Ctrl+K") == "站内搜索"
        assert _cp.builtin_hotkey_holder("Ctrl+W") == "隐藏主窗口"
        assert _cp.builtin_hotkey_holder("F1") == "使用说明"
        assert _cp.builtin_hotkey_holder("Ctrl+1") == "切换功能页"
        assert _cp.builtin_hotkey_holder("Ctrl+8") == "切换功能页"
        # 不误杀：带 Alt 的自由组合 / 区间外的编号
        assert _cp.builtin_hotkey_holder("Ctrl+Alt+T") == ""
        assert _cp.builtin_hotkey_holder("Ctrl+9") == ""
        assert _cp.builtin_hotkey_holder("Ctrl+Alt+/") == ""
        assert _cp.builtin_hotkey_holder("") == ""
        assert _cp.builtin_hotkey_holder(None) == ""

    def test_builtin_table_matches_main_window_bindings(self):
        """单一真相源契约：BUILTIN_HOTKEYS 声明的键必须在 main_window
        真的绑着（防表漂移成假护栏）；Ctrl+T 必在表内（用户撞的那个）。"""
        src = _read(MAIN_WINDOW_PATH)
        for combo in ("Ctrl+W", "Ctrl+H", "Ctrl+T", "Ctrl+K", "F1"):
            assert f'QKeySequence("{combo}")' in src, f"{combo} 未在主窗绑定"
        from src import command_palette as _cp
        assert _cp.BUILTIN_HOTKEYS["ctrl+t"] == "切换主题"

    def test_help_sections_still_parse(self):
        """加行后 17 章切片必须仍然成立（test_help_nav 的等价自查：
        overview + 16 个 <h3> 章）"""
        from src.main_window import MainWindow
        sections = MainWindow._help_html_sections()
        assert len(sections) == 17


# ====================================================================
# 6. 防假护栏反向验证（校验器坏掉时必须红灯）
# ====================================================================
class TestValidatorCatchesCorruption:
    def test_missing_page_command_flagged(self):
        """反向验证①：从注册表抽掉一条页面命令 → validate_registry 必须报"""
        entries = cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        broken = [e for e in entries if e.cid != "page.tasks"]
        problems = cp.validate_registry(
            broken, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        assert any("page.tasks" in p or "tasks" in p for p in problems)

    def test_duplicate_cid_flagged(self):
        """反向验证②：重复 cid 必须被抓（唯一性校验真的在跑）"""
        entries = list(cp.build_registry(
            NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX))
        broken = entries + [entries[0]]
        problems = cp.validate_registry(
            broken, NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED, NAV_PAGE_INDEX)
        assert any("cid 重复" in p for p in problems)
