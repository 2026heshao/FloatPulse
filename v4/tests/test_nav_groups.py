# -*- coding: utf-8 -*-
"""侧栏分组（nav_layout）纯逻辑测试：分组归属、展开集合、铺开显隐、组内重排。

背景（2026-09-29 侧栏重构）
--------------------------
侧栏从"一条平铺列表"改成「四组 + 可多组同时展开 + 溢出滚动」。分组只影响
**渲染**，``nav_order`` 仍是同一条一维顺序表、仍是拖拽落盘目标 —— 所以这一层
一旦有缺陷，表现是**静默**的：某个键凭空不见、组内拖拽顺手打乱别的组、
拼错的键静默落到默认组。这些都不抛异常，只有肉眼盯着侧栏才发现。

本文件不导入 PyQt6（nav_layout 本身就是纯逻辑），毫秒级。
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src import nav_layout as nl                      # noqa: E402
from src.config import NAV_PAGE_KEYS, DEFAULT_NAV_ORDER   # noqa: E402


# ---------------- 分组归属 ----------------
def test_every_fixed_page_key_has_a_group():
    """★ 每个固定功能页键都必须能归属到某一组。

    漏一个的后果是**静默丢失**：``group_of`` 返回 None，渲染时被丢掉，
    侧栏上那个按钮就此消失，没有任何报错。新增页面时最容易忘记同步
    nav_layout —— 这条断言就是那道防线。
    """
    missing = [k for k in NAV_PAGE_KEYS if nl.group_of(k) is None]
    assert not missing, f"这些固定页键没有归属组（会被静默丢出侧栏）：{missing}"


def test_default_nav_order_keys_all_grouped():
    """默认顺序表里的键也全部有归属（防止只在 order 里出现的键被吞掉）"""
    missing = [k for k in DEFAULT_NAV_ORDER if nl.group_of(k) is None]
    assert not missing, f"DEFAULT_NAV_ORDER 里这些键无归属：{missing}"


def test_plugin_key_goes_to_plugin_group():
    assert nl.group_of("plugin:ai-assistant") == "plugin_pages"
    assert nl.is_plugin_key("plugin:kb-search") is True
    assert nl.is_plugin_key("fragments") is False


def test_unknown_key_returns_none_not_default_group():
    """拼错的键返回 None（**有意**不给默认组，免得脏键静默出现在侧栏）"""
    assert nl.group_of("fragmetns") is None
    assert nl.group_of("") is None
    assert nl.group_of(None) is None
    assert nl.group_of(123) is None


def test_fixed_items_belong_to_system_group():
    assert nl.group_of("settings") == "system"
    assert nl.group_of("help") == "system"


# ---------------- 展开集合的收敛 ----------------
def test_sanitize_expanded_groups_filters_and_orders():
    """非法组名被过滤、去重、并按 NAV_GROUPS 顺序归一"""
    got = nl.sanitize_expanded_groups(["system", "bogus", "workbench", "system"])
    assert got == ("workbench", "system")
    # 入参顺序不影响结果（集合语义）
    assert nl.sanitize_expanded_groups(["tools", "workbench"]) == \
        nl.sanitize_expanded_groups(["workbench", "tools"])


def test_sanitize_expanded_groups_all_invalid_falls_back():
    """全是非法组名 = 配置被写坏 → 回退默认（否则侧栏会一片空白且查不出原因）"""
    for raw in (["nope"], [1, 2], {"x": 1}.keys(), ["Workbench"]):
        assert nl.sanitize_expanded_groups(raw) == tuple(nl.NAV_DEFAULT_EXPANDED)
    assert nl.sanitize_expanded_groups(None) == tuple(nl.NAV_DEFAULT_EXPANDED)
    assert nl.sanitize_expanded_groups(3) == tuple(nl.NAV_DEFAULT_EXPANDED)


def test_sanitize_expanded_groups_empty_is_legal_all_collapsed():
    """★ 空集合是**合法状态**（用户主动全部折叠），不能回退默认。

    这一条与上一条是同一个函数里最容易写错的分支：两者必须分开 ——
    "空"既可能是用户意图，也可能是过滤后为空。判据是**原值本身是否为空**。
    """
    assert nl.sanitize_expanded_groups([]) == ()
    assert nl.sanitize_expanded_groups(()) == ()
    assert nl.sanitize_expanded_groups(set()) == ()


def test_sanitize_expanded_groups_accepts_single_string():
    """标量入参容错：旧格式字符串 / 手写配置也能吃下"""
    assert nl.sanitize_expanded_groups("tools") == ("tools",)
    assert nl.sanitize_expanded_groups("bogus") == tuple(nl.NAV_DEFAULT_EXPANDED)


# ---------------- 多组同时展开：切换 ----------------
def test_toggle_group_does_not_touch_other_groups():
    """★ 核心语义：切一个组**不影响**其它组（这是"取消手风琴"的全部意义）"""
    cur = ("workbench",)
    cur = nl.toggle_group(cur, "tools")
    assert cur == ("workbench", "tools")
    cur = nl.toggle_group(cur, "system")
    assert set(cur) == {"workbench", "tools", "system"}
    # 再点 workbench → 只收它
    assert nl.toggle_group(cur, "workbench") == ("tools", "system")


def test_toggle_group_allows_all_collapsed_and_restores():
    cur = ("workbench",)
    assert nl.toggle_group(cur, "workbench") == ()
    assert nl.toggle_group((), "workbench") == ("workbench",)


def test_toggle_group_ignores_unknown_group():
    assert nl.toggle_group(("tools",), "nope") == ("tools",)


def test_toggle_group_result_is_nav_groups_ordered():
    """结果顺序恒定（= 铺开顺序），不随点击先后漂移 —— 落盘才可比对"""
    a = nl.toggle_group(("system",), "workbench")
    b = nl.toggle_group(("workbench",), "system")
    assert a == b == ("workbench", "system")


def test_group_is_expanded_accepts_scalar_and_collection():
    assert nl.group_is_expanded("tools", "tools") is True
    assert nl.group_is_expanded("tools", "system") is False
    assert nl.group_is_expanded(("tools", "system"), "system") is True
    assert nl.group_is_expanded((), "system") is False
    assert nl.group_is_expanded(None, "system") is False


# ---------------- 铺开（显隐） ----------------
def test_visible_keys_union_of_expanded_groups():
    """多组同时展开 → 可见键是各组之并，顺序仍跟随 order"""
    order = list(DEFAULT_NAV_ORDER)
    reg = set(order)
    got = nl.visible_keys(order, reg, ("workbench", "tools"))
    assert set(got) == {"fragments", "tasks", "notes", "knowledge",
                        "assets", "nav", "apps"}
    # 顺序 = order 里的相对顺序，而不是组顺序
    assert got == [k for k in order if k in set(got)]
    assert nl.visible_keys(order, reg, ()) == []


def test_visible_keys_skips_unregistered():
    """order 里有 key 但尚未注册（启动期插件未装配）→ 不能铺进来。

    铺进来会得到一条**空白行**（没有按钮的槽位），比"暂时没有"更糟。
    """
    order = list(DEFAULT_NAV_ORDER) + ["plugin:ghost"]
    got = nl.visible_keys(order, {"fragments", "tasks"}, ("workbench",))
    assert "tasks" in got and "notes" not in got
    assert nl.visible_keys(order, set(), ("plugin_pages",)) == []


def test_group_items_appends_fixed_items_only_for_expanded_groups():
    order = list(DEFAULT_NAV_ORDER)
    sys_items = nl.group_items(order, set(order), ("system",))
    assert sys_items[-2:] == ["settings", "help"], f"实际 {sys_items}"
    wb_items = nl.group_items(order, set(order), ("workbench",))
    assert "settings" not in wb_items and "help" not in wb_items
    both = nl.group_items(order, set(order), ("workbench", "system"))
    assert both[-2:] == ["settings", "help"]
    assert nl.group_items(order, set(order), ()) == []


def test_group_items_fixed_order_after_draggables():
    order = list(DEFAULT_NAV_ORDER) + ["plugin:x"]
    items = nl.group_items(order, set(order), ("plugin_pages",))
    assert items == ["plugin:x"]


# ---------------- 组内重排 ----------------
def test_reorder_within_group_only_moves_that_group_slots():
    order = ["fragments", "tasks", "notes", "knowledge", "assets",
             "apps", "nav", "plugins", "plugin:a"]
    new = nl.reorder_within_group(order, "tools", ["nav", "apps"])
    assert new is not None
    # tools 组换了顺序
    assert new.index("nav") < new.index("apps")
    # ★ 其它键的**槽位**完全不动（组内拖拽不得顺手打乱别的组）
    others = ["fragments", "tasks", "notes", "knowledge", "assets",
              "plugins", "plugin:a"]
    for k in others:
        assert order.index(k) == new.index(k), f"{k} 的槽位被动了"
    assert sorted(new) == sorted(order)


def test_reorder_within_group_returns_none_on_set_mismatch():
    order = ["fragments", "tasks", "notes", "knowledge", "assets",
             "apps", "nav", "plugins"]
    # 少一个键
    assert nl.reorder_within_group(order, "tools", ["nav"]) is None
    # 多一个键
    assert nl.reorder_within_group(order, "tools", ["nav", "apps", "x"]) is None
    # 拿别组的键来冒充
    assert nl.reorder_within_group(order, "tools", ["nav", "notes"]) is None
    # 非 list/tuple
    assert nl.reorder_within_group(order, "tools", "navapps") is None
    assert nl.reorder_within_group("not-a-list", "tools", ["nav", "apps"]) is None


def test_reorder_within_group_ignores_unregistered_plugin_in_order():
    """顺序表里留着的未注册插件键不属于任何组，不参与组内集合校验"""
    order = ["apps", "nav", "plugin:a"]
    new = nl.reorder_within_group(order, "tools", ["nav", "apps"])
    assert new is not None
    assert new.index("plugin:a") == order.index("plugin:a")


# ---------------- split_by_group ----------------
def test_split_by_group_keeps_relative_order_and_drops_unknown():
    order = ["notes", "fragments", "apps", "nav", "plugin:z", "typo-key"]
    by = nl.split_by_group(order)
    assert by["workbench"] == ["notes", "fragments"]
    assert by["tools"] == ["apps", "nav"]
    assert by["plugin_pages"] == ["plugin:z"]
    assert by["system"] == []
    assert all("typo-key" not in v for v in by.values())


def test_split_by_group_accepts_subset():
    by = nl.split_by_group(["fragments", "tasks", "notes"], ["notes", "fragments"])
    assert by["workbench"] == ["notes", "fragments"]


def test_nav_groups_order_is_stable_and_covers_dict():
    """NAV_GROUPS 的顺序 = 侧栏自上而下的铺开顺序，且与标题表一一对应"""
    assert nl.NAV_GROUPS == ("workbench", "tools", "plugin_pages", "system")
    assert set(nl.NAV_GROUP_TITLES) == set(nl.NAV_GROUPS)


def test_no_arrow_glyphs_in_layout_module():
    """★ 折叠箭头已改为自绘（glass.NavArrow），nav_layout 里不该再有箭头字符。

    留一条防线：曾经用过 ▼/▶ 字符，而 ▶（U+25B6）不在中文字体里，
    渲染成常挂的"豆腐块"。若将来有人把字符箭头塞回来，这条会拦下。
    """
    src = open(os.path.join(BASE, "src", "nav_layout.py"), encoding="utf-8").read()
    for ch in ("▼", "▶", "▾", "▸"):
        assert ch not in src, f"nav_layout 里出现了字符箭头 {ch}（应由 NavArrow 自绘）"
