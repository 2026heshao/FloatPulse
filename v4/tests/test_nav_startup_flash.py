# -*- coding: utf-8 -*-
"""启动期导航栏「闪跳」回归（2026-10-12 用户报障）。

症状
----
用户报「每次启动，主窗口刚加载完成时，导航栏会闪跳一下」。

根因（逐帧实测，非推断）
------------------------
插件页装配（`PluginWiring.apply_plugins` → `load_all` 实测 ~94ms）被刻意
推迟到主窗显示后 ~960ms（2026-10-08 启动提速的代价）。于是：

  · 主窗露脸时侧栏**还没有插件按钮**（8 行）；
  · ~960ms 后 5 个出厂插件页逐个 `register_plugin_page`，每个都走
    `_apply_nav_order(save=True)` —— 实测 **5 次重排 / 5 次落定动画 /
    5 次 config 落盘**；
  · 用户配置里 `plugin_pages` 组是展开的 → 侧栏整列一次性长出 5 行，
    「设置/说明」下移 **190px**、滚动条 max 0→90。

逐帧指纹实测（用户真实 config）：
    t= 27ms hash=2bdde1bd content_h=596 vbar= 0 设置_y=420 按钮= 8
    t=816ms hash=5994ba7f content_h=686 vbar=90 设置_y=610 按钮=13
                          ↑ 一帧之内整列跳变（就是用户看到的那一下）

修复
----
`show` **之前**按纯 manifest 预扫（`PluginLoader.scan_page_specs`，实测
~1.6ms，不 import 插件模块）预留导航槽位（`reserve_plugin_pages`：整批
只 1 次重排、`save=False`），侧栏首帧即终态；随后真页注册走**幂等分支**
只换 widget —— 实测 0 次重排、0px 位移、像素指纹不变。

本文件钉住的契约
----------------
1. `scan_page_specs` 只读 manifest、且与 `_candidate_dirs` 同序；
2. `reserve_plugin_pages` 整批一次重排、**不落盘**、幂等；
3. 预留后 `register_plugin_page` 走幂等分支：不重排、不动已有按钮几何；
4. 侧栏几何在「预留 → 装载」前后完全不变（本文件的核心断言）；
5. 加载失败的插件，其预留槽位被回收（不留点开空白的幽灵页）。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.plugin_loader import MANIFEST_NAME, PluginLoader  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402

# 用户真实配置（float_data/config.json 实读）——闪跳的触发条件：
# 插件组展开 + 有插件页，才会"整列长出来"
USER_GROUPS = ["workbench", "tools", "plugin_pages", "system"]
USER_ORDER = ["fragments", "plugins", "notes", "assets", "knowledge",
              "tasks", "apps", "nav", "plugin:ai-assistant",
              "plugin:kb-search", "plugin:recurring-tasks",
              "plugin:ai-text-workshop", "plugin:vault"]

_APP = None


def _app():
    """QApplication 引用必须留模块级防 GC（见 test_icons 的硬崩教训）。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_plugins_dir(tmp, specs):
    """造一个插件目录：``specs = [(id, title), ...]``（title=None 表示无 page）"""
    pdir = os.path.join(tmp, "plugins")
    os.makedirs(pdir, exist_ok=True)
    for pid, title in specs:
        d = os.path.join(pdir, pid)
        os.makedirs(d, exist_ok=True)
        man = {"id": pid, "name": pid, "version": "1.0.0", "entry": "plugin.py"}
        if title is not None:
            man["page"] = {"title": title}
        with open(os.path.join(d, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(man, f)
        with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
            f.write("# 占位入口，本文件不 import 它\n")
    return pdir


def _build_window(config):
    tmp = tempfile.mkdtemp(prefix="fp_navflash_test_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    tm = TaskManager(os.path.join(data_dir, "schedule.json"))
    nm = NoteManager(os.path.join(data_dir, "notes.json"))
    fm = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(fm, config)
    am = TempAssetManager(tmp)
    return MainWindow(tm, nm, fm, None, config, clip, am)


@pytest.fixture
def qapp():
    return _app()


@pytest.fixture
def user_win(qapp):
    """按用户真实配置（插件组展开 + 5 插件页 order）建的主窗，未 show。"""
    tmp = tempfile.mkdtemp(prefix="fp_navflash_cfg_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    config.set("theme", "light")
    config.set("nav_expanded_groups", USER_GROUPS)
    config.set("nav_order", USER_ORDER)
    w = _build_window(config)
    yield w, config
    w.close()
    w.deleteLater()


# ====================================================================
# 1. scan_page_specs：纯 manifest 预扫
# ====================================================================
def test_scan_page_specs_reads_only_manifests(qapp):
    """只返回声明了 page 的插件，且**不 import 插件模块**。"""
    tmp = tempfile.mkdtemp(prefix="fp_scan_")
    pdir = _make_plugins_dir(tmp, [
        ("a-page", "甲页"), ("b-nopage", None), ("c-page", "丙页"),
    ])
    loader = PluginLoader(registry=None, ctx=None, plugins_dir=pdir)
    specs = loader.scan_page_specs()
    assert specs == [("a-page", "甲页"), ("c-page", "丙页")]
    # 预扫没有触发真实加载（_loaded 仍空）
    assert loader.loaded_plugins() == []


def test_scan_page_specs_sorted_like_candidate_dirs(qapp):
    """顺序必须与 _candidate_dirs（目录名排序）一致 —— 预留顺序 == 真实
    注册顺序，否则物理索引会错位。"""
    tmp = tempfile.mkdtemp(prefix="fp_scan2_")
    pdir = _make_plugins_dir(tmp, [
        ("zeta", "Z"), ("alpha", "A"), ("mid", "M"),
    ])
    loader = PluginLoader(registry=None, ctx=None, plugins_dir=pdir)
    ids = [pid for pid, _ in loader.scan_page_specs()]
    assert ids == ["alpha", "mid", "zeta"]


def test_scan_page_specs_skips_invalid_manifest(qapp):
    """非法 manifest（缺字段）不预留 —— 它本来也加载不了。"""
    tmp = tempfile.mkdtemp(prefix="fp_scan3_")
    pdir = os.path.join(tmp, "plugins")
    good = os.path.join(pdir, "good")
    bad = os.path.join(pdir, "bad")
    for d in (good, bad):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(good, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump({"id": "good", "name": "g", "version": "1.0.0",
                   "entry": "plugin.py", "page": {"title": "好"}}, f)
    with open(os.path.join(bad, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump({"id": "bad"}, f)          # 缺 name/version/entry
    loader = PluginLoader(registry=None, ctx=None, plugins_dir=pdir)
    assert loader.scan_page_specs() == [("good", "好")]


def test_scan_page_specs_tolerates_broken_json(qapp):
    """坏 JSON 不许抛（启动路径上不能因一个坏插件崩）。"""
    tmp = tempfile.mkdtemp(prefix="fp_scan4_")
    pdir = os.path.join(tmp, "plugins")
    d = os.path.join(pdir, "broken")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, MANIFEST_NAME), "w", encoding="utf-8") as f:
        f.write("{ not json")
    loader = PluginLoader(registry=None, ctx=None, plugins_dir=pdir)
    assert loader.scan_page_specs() == []


def test_scan_page_specs_empty_dir_returns_empty(qapp):
    tmp = tempfile.mkdtemp(prefix="fp_scan5_")
    pdir = os.path.join(tmp, "plugins")
    os.makedirs(pdir, exist_ok=True)
    loader = PluginLoader(registry=None, ctx=None, plugins_dir=pdir)
    assert loader.scan_page_specs() == []


# ====================================================================
# 2. reserve_plugin_pages：整批一次重排、不落盘、幂等
# ====================================================================
def test_reserve_creates_buttons_and_pages_without_saving(user_win):
    win, config = user_win
    n = len(win._nav_btns)
    saved = {"n": 0}
    orig_save = config.save

    def counting_save(*a, **k):
        saved["n"] += 1
        return orig_save(*a, **k)
    config.save = counting_save
    try:
        added = win.reserve_plugin_pages([("p1", "页一"), ("p2", "页二")])
    finally:
        config.save = orig_save

    assert added == 2
    assert len(win._nav_btns) == n + 2
    assert "plugin:p1" in win._nav_btns
    assert win.NAV_PAGE_INDEX["plugin:p1"] >= 10   # 插件页段
    # 预留**不落盘**（占位顺序不该写进 nav_order）
    assert saved["n"] == 0


def test_reserve_performs_exactly_one_relayout(user_win):
    """★ 核心收益：N 个槽位只重排 1 次（逐个 register 是 N 次）。"""
    win, _ = user_win
    calls = {"n": 0}
    orig = win._relayout_nav

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)
    win._relayout_nav = counting
    win.reserve_plugin_pages([("p1", "一"), ("p2", "二"), ("p3", "三")])
    assert calls["n"] == 1


def test_reserve_is_idempotent(user_win):
    win, _ = user_win
    first = win.reserve_plugin_pages([("p1", "一")])
    second = win.reserve_plugin_pages([("p1", "一")])
    assert first == 1 and second == 0
    assert len([k for k in win._nav_btns if k == "plugin:p1"]) == 1


def test_reserve_strips_emoji_title_like_register(user_win):
    """预留与真页注册必须同源剥 emoji，否则注册时文案再变一次（按钮抖动）。"""
    win, _ = user_win
    win.reserve_plugin_pages([("p1", "🤖 AI 助手")])
    assert win.NAV_PAGE_TITLES["plugin:p1"] == "AI 助手"
    assert win._nav_btns["plugin:p1"].text() == "AI 助手"


def test_reserve_handles_empty_specs(user_win):
    win, _ = user_win
    assert win.reserve_plugin_pages([]) == 0
    assert win.reserve_plugin_pages(None) == 0


# ====================================================================
# 3. 核心契约：预留后注册不再移动侧栏（=闪跳消除）
# ====================================================================
def test_register_after_reserve_does_not_move_existing_buttons(user_win, qapp):
    """★ 本文件的核心断言（复现用户症状的那一条）。

    预留 5 个槽位 → 模拟延迟加载逐页注册 → 侧栏「设置/说明」的 Y 与
    内容高度必须**一动不动**（修复前实测下移 190px、滚动条 0→90）。
    """
    win, _ = user_win
    specs = [("p1", "一"), ("p2", "二"), ("p3", "三"),
             ("p4", "四"), ("p5", "五")]
    win.reserve_plugin_pages(specs)
    win.show()
    qapp.processEvents()

    before = {
        "h": win._nav_content.height(),
        "vbar": win._nav_scroll.verticalScrollBar().maximum(),
        "settings_y": win._settings_btn.y(),
        "help_y": win._help_btn.y(),
        "n": len(win._nav_btns),
    }
    # 真实装载：幂等分支（按钮与索引都已存在）
    for pid, title in specs:
        win.register_plugin_page(f"plugin:{pid}", title, QWidget())
    qapp.processEvents()

    after = {
        "h": win._nav_content.height(),
        "vbar": win._nav_scroll.verticalScrollBar().maximum(),
        "settings_y": win._settings_btn.y(),
        "help_y": win._help_btn.y(),
        "n": len(win._nav_btns),
    }
    assert after == before, (
        f"注册后侧栏发生位移（闪跳回归）：before={before} after={after}")


def test_register_after_reserve_triggers_no_relayout(user_win, qapp):
    """幂等分支不应重排（重排 = 布局抖动源）。"""
    win, _ = user_win
    specs = [("p1", "一"), ("p2", "二")]
    win.reserve_plugin_pages(specs)
    win.show()
    qapp.processEvents()
    calls = {"n": 0}
    orig = win._relayout_nav

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)
    win._relayout_nav = counting
    for pid, title in specs:
        win.register_plugin_page(f"plugin:{pid}", title, QWidget())
    assert calls["n"] == 0, "预留后注册仍触发重排 —— 闪跳修复失效"


def test_reserve_then_register_swaps_placeholder_for_real_page(user_win, qapp):
    """占位页必须被真页就地换掉（索引不变、内容可辨）。"""
    win, _ = user_win
    win.reserve_plugin_pages([("p1", "一")])
    idx = win.NAV_PAGE_INDEX["plugin:p1"]
    placeholder = win._stack.widget(idx)
    real = QWidget()
    real.setObjectName("theRealPage")
    win.register_plugin_page("plugin:p1", "一", real)
    assert win.NAV_PAGE_INDEX["plugin:p1"] == idx
    assert win._stack.widget(idx) is real
    assert placeholder is not real


# ====================================================================
# 4. 幽灵页回收
# ====================================================================
def test_drop_reserved_page_removes_slot(user_win, qapp):
    """加载失败的插件：预留槽位必须回收（否则是点开空白的幽灵页）。"""
    win, _ = user_win
    win.reserve_plugin_pages([("dead", "死的")])
    assert "plugin:dead" in win._nav_btns
    assert win.drop_reserved_page("dead") is True
    assert "plugin:dead" not in win._nav_btns
    assert "plugin:dead" not in win.NAV_PAGE_INDEX


def test_drop_unknown_reserved_page_returns_false(user_win):
    win, _ = user_win
    assert win.drop_reserved_page("nope") is False


# ====================================================================
# 5. 装配接线（防"写了方法没接上"）
# ====================================================================
def test_main_assembly_reserves_before_show():
    """main() 必须真的调用预留，且**早于** show 启动链（AST 断言）。

    预留若排在 show 之后，侧栏仍会先以无插件状态露脸 —— 闪跳照旧。
    """
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "knowledge_ball.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    assert "reserve_pages_before_show" in called, \
        "main() 未调用 reserve_pages_before_show —— 首屏后跳未修复"
    # 预留必须先于 wire_startup_relay（后者才触发 show）
    i_reserve = src.index("reserve_pages_before_show()")
    i_relay = src.index("wire_startup_relay(")
    assert i_reserve < i_relay, \
        "预留调用排在 wire_startup_relay 之后 → 侧栏仍会先露脸再跳"


def test_wiring_reserve_method_exists():
    from src.wiring import PluginWiring
    assert callable(getattr(PluginWiring, "reserve_pages_before_show", None))


class _StubLoader:
    def __init__(self, specs):
        self._specs = specs

    def scan_page_specs(self):
        return list(self._specs)


class _StubWindow:
    def __init__(self):
        self.reserved = None

    def reserve_plugin_pages(self, specs):
        self.reserved = list(specs)
        return len(self.reserved)


def _wiring(config, loader, win):
    from src.wiring import PluginWiring
    return PluginWiring(loader=loader, registry=None, ctx=None,
                        hotkey_mgr=None, config_manager=config,
                        main_window=win, ball=None)


def test_reserve_skips_disabled_plugins(user_win):
    """★ 被停用的插件不预留 —— 否则它会白占一行（点开是空白幽灵页）。

    与 `_register_plugin_pages` 的跳过口径必须一致：那一步跳过停用插件，
    这一步若照旧预留，收尾回收虽能兜住，但首帧会先多出一行再消失。
    """
    _, config = user_win
    config.set("plugins_disabled", ["p2"])
    loader = _StubLoader([("p1", "一"), ("p2", "二"), ("p3", "三")])
    win = _StubWindow()
    w = _wiring(config, loader, win)
    n = w.reserve_pages_before_show()
    assert n == 2
    assert win.reserved == [("p1", "一"), ("p3", "三")]


def test_reserve_skipped_when_plugin_master_switch_off(user_win):
    """插件总闸关闭 → 一个槽位都不预留（页面本来就不该出现）。"""
    _, config = user_win
    config.set("plugins_enabled", False)
    loader = _StubLoader([("p1", "一")])
    win = _StubWindow()
    w = _wiring(config, loader, win)
    assert w.reserve_pages_before_show() == 0
    assert win.reserved is None


def test_reserve_swallows_loader_exception(user_win):
    """预扫抛异常不许挡住启动（预留只是锦上添花）。"""
    _, config = user_win

    class _Boom:
        def scan_page_specs(self):
            raise RuntimeError("boom")

    win = _StubWindow()
    w = _wiring(config, _Boom(), win)
    assert w.reserve_pages_before_show() == 0
    assert win.reserved is None


def test_reserve_tolerates_host_without_the_method(user_win):
    """宿主没有 reserve_plugin_pages（旧替身）时安全跳过。"""
    _, config = user_win
    loader = _StubLoader([("p1", "一")])

    class _Bare:
        pass

    w = _wiring(config, loader, _Bare())
    assert w.reserve_pages_before_show() == 0
