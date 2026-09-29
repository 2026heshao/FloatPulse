# -*- coding: utf-8 -*-
"""左侧导航栏的分组、展开集合与铺开顺序计算（**纯逻辑，禁 import PyQt6**）。

为什么单独成模块
================
侧栏从「一条平铺列表」改成「四组 + 可多组同时展开 + 溢出滚动」之后，
"哪个键属于哪一组、当前展开了哪几组、要按什么顺序铺开"这些问题
**与 Qt 无关**，抽出来就能脱离 GUI 跑 pytest。

侧栏是布局代码里最容易出静默缺陷的地方（顺序错位、键凭空丢失、
跨组顺序被组内拖拽打乱），而离屏脚本跑一次要好几秒 —— 纯函数测试
毫秒级，能把这类问题钉在提交前。

与既有 ``nav_order`` 的关系（**不引入任何配置迁移**）
=====================================================
``nav_order``（config.json 里那条扁平顺序表）**格式与语义完全不变**：
仍然是"全部固定键 + 插件键"的一条一维顺序，仍是拖拽换位的落盘目标。
分组只影响**渲染** —— 按组把这条顺序表切成若干段，依次铺开。

这样做的代价是「跨组拖动」失去意义（组内拖拽天然只改组内相对顺序），
收益是既有配置、既有拖拽落盘路径、既有 ``_nav_order`` 消费者全部不用动。

四个分组
========
``workbench`` 工作台：碎片 / 日程任务 / 笔记 / 知识库 / 临时素材
``tools``     工具：  网址导航 / 软件导航
``plugin_pages`` 插件：全部 ``plugin:<id>`` 页面插件（动态，数量无上限）
``system``    系统：  插件中心 / 设置 / 使用说明

「设置」「使用说明」是**非拖拽**项（不进 ``nav_order``），由宿主在建
侧栏时直接放进 ``system`` 组，见 ``NAV_FIXED_ITEM_GROUP``。

展开集合的语义（2026-09-29 第二版：取消手风琴）
==============================================
第一版是"任何时刻恰好一组展开"的手风琴。用户要求**多组可同时展开**，
所以展开态从"一个组"变成"一个组集合"：``nav_expanded_groups`` 存
一个字符串列表，顺序无关，合法性由 :func:`sanitize_expanded_groups`
统一收敛。

★ 空集合是**合法状态**（全部折叠）。侧栏会只剩四行组标题，这是用户
点出来的，属于正常表现，不应被"容错"回默认 —— 但"配置里全是非法组名"
（写坏了）会回退默认，两者必须区分开。
"""

# 分组 id（顺序 = 侧栏从上到下的铺开顺序）
NAV_GROUPS = ("workbench", "tools", "plugin_pages", "system")

# 组标题文案（侧栏显示用；折叠箭头是自绘子控件，不在文案里）
NAV_GROUP_TITLES = {
    "workbench": "工作台",
    "tools": "工具",
    "plugin_pages": "插件",
    "system": "系统",
}

# 各组的**固定**键（不含动态插件键、不含 settings/help 这对非拖拽项）
_BASE_GROUP_KEYS = {
    "workbench": ("fragments", "tasks", "notes", "knowledge", "assets"),
    "tools": ("nav", "apps"),
    "plugin_pages": (),          # 全部由 plugin: 前缀动态判定
    "system": ("plugins",),      # 插件中心（物理索引 9）
}

# 非拖拽固定项 → 归属组。key 是宿主内部的逻辑名（不是物理页面索引）
NAV_FIXED_ITEM_GROUP = {"settings": "system", "help": "system"}
# 固定项在组内的排列顺序（组内可拖键之后）
NAV_FIXED_ITEM_ORDER = ("settings", "help")

# 插件页键前缀（与 main_window.register_plugin_page 的约定一致）
NAV_PLUGIN_PREFIX = "plugin:"

# 首次启动默认展开的组（也是配置"全是非法值"时的回退）
NAV_DEFAULT_EXPANDED = ("workbench",)


def is_plugin_key(key) -> bool:
    """是否为插件页键（``plugin:<插件id>``）"""
    return isinstance(key, str) and key.startswith(NAV_PLUGIN_PREFIX)


def group_of(key):
    """键 → 归属组 id；无法归属（拼写错误 / 未知键）返回 None。

    None 是**有意的**：调用方据此把脏键丢掉，而不是落到某个默认组里 ——
    落到默认组会让拼错的键静默出现在侧栏上，比直接不见更难查。
    """
    if not isinstance(key, str):
        return None
    if key in NAV_FIXED_ITEM_GROUP:
        return NAV_FIXED_ITEM_GROUP[key]
    if is_plugin_key(key):
        return "plugin_pages"
    for group, keys in _BASE_GROUP_KEYS.items():
        if key in keys:
            return group
    return None


def sanitize_expanded_groups(raw, default=NAV_DEFAULT_EXPANDED) -> tuple:
    """收敛「当前展开的组集合」配置值 → 按 :data:`NAV_GROUPS` 顺序去重的元组。

    规则（三条对应的场景各不相同，别合并）：
      · 不是 list/tuple/set → 回退 ``default``（配置文件被写坏/旧格式）；
      · 是集合 → 过滤掉非法组名、去重；
      · 过滤后为空 **且原值非空** → 全是非法组名 → 回退 ``default``；
      · 过滤后为空 **且原值本为空** → 保留空集合 = 用户主动全折叠。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return tuple(default)
    wanted = set()
    for item in raw:
        if item in NAV_GROUPS:
            wanted.add(item)
    if not wanted:
        return tuple(default) if raw else ()
    return tuple(g for g in NAV_GROUPS if g in wanted)


def group_is_expanded(expanded, group: str) -> bool:
    """该组当前是否展开（把标量/集合两种入参都接受，容错调用方）"""
    if isinstance(expanded, str):
        return group == expanded
    try:
        return group in expanded
    except TypeError:
        return False


def toggle_group(expanded, group: str) -> tuple:
    """点击组标题后的新展开集合（展开↔折叠该组，其余组不受影响）。

    ★ 这是"多组同时展开"的核心：切一个组不动其它组。全折叠（空集合）
    是合法返回值。
    """
    if group not in NAV_GROUPS:
        return sanitize_expanded_groups(expanded)
    current = set(sanitize_expanded_groups(expanded))
    if group in current:
        current.discard(group)
    else:
        current.add(group)
    return tuple(g for g in NAV_GROUPS if g in current)


def split_by_group(order, keys=None) -> dict:
    """把顺序表按组切分 → ``{组id: [键...]}``（组内保持 order 里的相对顺序）。

    ``keys`` 为 None 时切分整个 ``order``；也可传一个子集（例如"已注册的
    键"）来只切分实际存在的那部分。不属于任何组的键被丢弃。
    """
    pool = list(order) if keys is None else list(keys)
    out = {g: [] for g in NAV_GROUPS}
    for key in pool:
        group = group_of(key)
        if group is None:
            continue
        out[group].append(key)
    return out


def visible_keys(order, registered, expanded) -> list:
    """当前**会铺进侧栏**的可拖键 = 已展开组 ∩ 已注册，顺序跟随 ``order``。

    ``registered`` 是"此刻真的有按钮的键"（启动期插件尚未装配时，
    ``order`` 里会有 key 但没按钮；把它们铺进来会得到一条空白行）。
    ``expanded`` 可以是集合（多组展开），也可以是单个组名字符串。
    """
    pool = set(registered or ())
    return [k for k in order
            if group_is_expanded(expanded, group_of(k)) and k in pool]


def group_items(order, registered, expanded,
                fixed_items=NAV_FIXED_ITEM_ORDER) -> list:
    """展开组要铺开的**完整项清单**（可拖键 + 组内非拖拽固定项）。

    非拖拽项排在可拖键之后：设置/使用说明是"页脚性质"的入口，
    用户拖拽时它们不参与让位，放在末尾最不容易被误认为可拖。
    """
    items = visible_keys(order, registered, expanded)
    for key in fixed_items:
        if group_is_expanded(expanded, NAV_FIXED_ITEM_GROUP.get(key)):
            items.append(key)
    return items


def reorder_within_group(order, group: str, new_group_order) -> list:
    """把 ``order`` 里属于 ``group`` 的槽位按 ``new_group_order`` 重排。

    组内拖拽**只改组内相对顺序**：不属于该组的键（其它组、尚未注册的
    插件键）位置完全不动 —— 否则一次组内拖拽会顺手打乱别的组。

    返回新的完整顺序表；若 ``new_group_order`` 与 ``order`` 里的该组键
    不是同一个集合，返回 None（调用方保留原顺序并记一条 warning，
    **不静默吞掉**）。
    """
    if not isinstance(order, list) or not isinstance(new_group_order, (list, tuple)):
        return None
    slots = [k for k in order if group_of(k) == group]
    new = list(new_group_order)
    if len(slots) != len(new) or sorted(slots) != sorted(new):
        return None
    pool = iter(new)
    return [next(pool) if group_of(k) == group else k for k in order]
