# -*- coding: utf-8 -*-
"""图标体系（UI 强化方案 A2）护栏测试。

覆盖三层：
  1. **纯数据健全性**（``src/icons.py``）：名称唯一、路径可解析、命令在
     白名单内、坐标不越出 24 视框、覆盖清单与导航键表一致。
  2. **解析器健壮性**：SVG path d 子集解析器对脏数据必须**报错而不是
     静默跳过** —— 不支持的字母被跳过会让图标悄悄画成完全另一个形状，
     这是本项最阴的失败模式。
  3. **渲染层与控件接线**（``src/icon_render`` / ``controls.IconLabel`` /
     ``controls.PageTitle``）：缓存键语义、未登记名称抛 KeyError、
     标题文字标签的 objectName 口径不变（QSS 命中不能变）。

为什么 icons.py 必须零 Qt 依赖
==============================
纯 pytest 毫秒级即可跑完数据健全性；一旦 import Qt，这些断言就要挂在
离屏环境上，而离屏脚本跑一次要好几秒。此约束由 AST 断言钉死。
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from src import icons  # noqa: E402
from src.nav_layout import NAV_FIXED_ITEM_GROUP, _BASE_GROUP_KEYS  # noqa: E402

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_src(rel: str) -> ast.Module:
    path = os.path.join(_V4, rel)
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _module_level_dict_keys(rel: str, name: str):
    """从源码里取模块级 ``NAME = {...}`` 的字面量键（不 import 该模块）。

    用 AST 而不是 import：``main_window`` 的 import 会拖起整棵 GUI 依赖树，
    而这里只想知道"那张表里写了哪些键"。也顺手证明这些键**以字面量形式**
    存在于源码中（不是运行时算出来的）。
    """
    tree = _parse_src(rel)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id == name
                        and isinstance(node.value, ast.Dict)):
                    return [k.value for k in node.value.keys]
    raise AssertionError("在 %s 里找不到模块级字面量字典 %s" % (rel, name))


_APP = None


def _app():
    """取（必要时创建）QApplication，并**把引用留存在模块级**。

    ★ 这里必须留引用，不能写成 ``QApplication.instance() or QApplication([])``
      就直接在调用点丢弃返回值 —— 这是本文件 2026-09-30 排查了半天的硬崩：

      PyQt6 下 ``QApplication([])`` 返回的 Python 包装对象**持有 C++ 实例的
      所有权**。调用方若不接住返回值，函数一返回引用计数就归零 → C++ 侧的
      QApplication 被销毁；紧接着任何 ``QPixmap`` / ``QWidget`` 构造都会
      触发 Qt 的 ``qFatal("QPixmap: Must construct a QGuiApplication before
      a QPixmap")`` → 进程 abort。

      症状极具误导性：pytest **收集到 62 项、打完 51 个点后在第一个 Qt
      测试上静默死掉**，没有 traceback、没有失败摘要，退出码 127。而本文件
      前 51 项全是纯逻辑不碰 Qt，所以崩点恰好落在"第一个 Qt 测试"上，
      看起来像渲染层的问题 —— 其实是测试脚手架的问题。

      对照组 ``test_keyboard_focus.py`` 用的是同一个 helper，但它写的是
      ``app = _app()``，引用留在调用方，所以一直没事。
    """
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


# ====================================================================
# 1. 数据健全性
# ====================================================================
def test_icon_paths_is_non_empty_dict_of_str():
    assert isinstance(icons.ICON_PATHS, dict)
    assert icons.ICON_PATHS
    for name, d in icons.ICON_PATHS.items():
        assert isinstance(name, str) and name
        assert isinstance(d, str) and d.strip(), "图标 %r 的路径数据为空" % name


def test_icon_names_matches_paths_and_is_unique():
    names = icons.icon_names()
    assert isinstance(names, tuple)
    assert len(names) == len(icons.ICON_PATHS)
    assert len(set(names)) == len(names)
    assert set(names) == set(icons.ICON_PATHS)


def test_every_path_parses_with_allowed_commands():
    allowed = {"M", "L", "H", "V", "C", "Q", "Z"}
    for name, d in icons.ICON_PATHS.items():
        cmds = icons.parse_path(d)
        assert cmds, "图标 %r 解析出 0 条命令" % name
        for cmd, args in cmds:
            assert cmd.upper() in allowed, "图标 %r 用了不支持的命令 %r" % (name, cmd)
            assert len(args) == icons._ARGC[cmd.upper()]


def test_every_coordinate_stays_inside_viewbox():
    """全部坐标必须落在 24 视框内 —— 手写坐标写错一位就会画到框外。"""
    for name, d in icons.ICON_PATHS.items():
        for cmd, args in icons.parse_path(d):
            for v in args:
                assert 0.0 <= v <= float(icons.ICON_SIZE), (
                    "图标 %r 的命令 %s 有坐标 %.2f 越出 0..%d 视框"
                    % (name, cmd, v, icons.ICON_SIZE))


def test_argc_table_covers_every_supported_command():
    # _ARGC 的键就是解析器支持的全集；漏一个会在 parse_path 里 KeyError
    assert set(icons._ARGC) == {"M", "L", "H", "V", "C", "Q", "Z"}


# ====================================================================
# 2. 解析器健壮性：宁可炸也不静默少画
# ====================================================================
@pytest.mark.parametrize("bad", [
    "",                       # 空串
    "   ",                    # 纯空白
    "M",                      # 只有命令没有参数
    "10,10",                  # 数字出现在任何命令之前
    "M10,10 L20",             # 结尾残留半组参数
])
def test_parse_path_rejects_malformed(bad):
    with pytest.raises(ValueError):
        icons.parse_path(bad)


@pytest.mark.parametrize("bad_letter", ["A", "S", "T", "X", "a", "s"])
def test_parse_path_rejects_unsupported_commands(bad_letter):
    """★ 未支持的命令字母必须报错。

    分词正则只认它认识的字母，**不认识的会被 finditer 直接跳过** ——
    "M10,10 A5,5 0 0 1 20,20" 里的 A 及其参数会被当成上一命令的裸坐标组
    吃下去，结果是路径画出来了但完全不是作者想画的形状，且不报任何错。
    """
    with pytest.raises(ValueError):
        icons.parse_path("M10,10 %s5,5 0 0 1 20,20" % bad_letter)


def test_parse_path_rejects_illegal_characters():
    with pytest.raises(ValueError):
        icons.parse_path("M0,0 #5,5")


def test_parse_path_accepts_exponent_numbers():
    """科学计数法里的 e/E 不能被误判成非法命令字母。"""
    cmds = icons.parse_path("M0,0L1e1,1E1")
    assert cmds[-1] == ("L", [10.0, 10.0])


def test_parse_path_handles_relative_and_implicit_commands():
    # 小写相对 + M 之后裸坐标组按 L（SVG 规范）
    cmds = icons.parse_path("m10,10 l5,0 0,5 h-5 v-5 z")
    assert cmds[0] == ("m", [10.0, 10.0])
    assert cmds[1] == ("l", [5.0, 0.0])
    assert cmds[2] == ("l", [0.0, 5.0])
    assert cmds[3] == ("h", [-5.0])
    assert cmds[4] == ("v", [-5.0])
    assert cmds[5] == ("z", [])


def test_parse_path_m_implies_l_for_bare_coordinates():
    cmds = icons.parse_path("M0,0 1,1 2,2")
    assert cmds == [("M", [0.0, 0.0]), ("L", [1.0, 1.0]), ("L", [2.0, 2.0])]


def test_parse_path_supports_quadratic_and_close():
    cmds = icons.parse_path("M0,0 Q5,5 10,0 Z")
    assert cmds == [("M", [0.0, 0.0]), ("Q", [5.0, 5.0, 10.0, 0.0]), ("Z", [])]


# ====================================================================
# 3. 几何构造助手
# ====================================================================
def test_circle_helper_is_closed_and_parsable():
    d = icons.circle(12.0, 12.0, 5.0)
    cmds = icons.parse_path(d)
    assert cmds[-1][0] == "Z"
    # 4 段三次贝塞尔（每段一个 C）+ 一个 M + 一个 Z
    assert sum(1 for c, _ in cmds if c == "C") == 4


def test_rrect_helper_is_closed_and_parsable():
    cmds = icons.parse_path(icons.rrect(2.0, 3.0, 10.0, 8.0, 2.0))
    assert cmds[0][0] == "M"
    assert cmds[-1][0] == "Z"
    assert sum(1 for c, _ in cmds if c == "C") == 4


def test_poly_helper_rejects_odd_arity():
    with pytest.raises(ValueError):
        icons.poly(1.0, 2.0, 3.0)
    with pytest.raises(ValueError):
        icons.poly(1.0, 2.0)


def test_dot_helper_is_a_zero_length_subpath():
    assert icons.parse_path(icons.dot(3.0, 4.0)) == [("M", [3.0, 4.0]), ("Z", [])]


# ====================================================================
# 4. 零 Qt 依赖（AST 断言）
# ====================================================================
def test_icons_module_imports_only_stdlib_re_math():
    """``src/icons.py`` 的顶层 import 只能是 ``re`` 与 ``math``。

    这是"数据健全性可以脱离 GUI 跑 pytest"的全部前提；一旦有人为了图方便
    在这里 import PyQt6，下面那批断言就要挂在离屏环境上。
    （``math`` 是 2026-10-01 P1 批次放行的：``arc()`` 助手求贝塞尔逼近的
    三角函数必须用它，仍是零 GUI 依赖的纯标准库。）
    """
    tree = _parse_src(os.path.join("src", "icons.py"))
    mods = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods == {"re", "math"}, \
        "src/icons.py 顶层 import 变成了 %s" % sorted(mods)


def test_no_pyqt_import_or_symbol_in_icons_code():
    """★ 必须用 AST 看**真实代码**，不能对源码搜字符串。

    模块头有一整段"为什么不上 SVG 资源"的说明，里面反复出现 PyQt6 /
    PyQt6.QtSvg 字样 —— 直接 ``assert "PyQt6" not in src`` 会把这段解释
    自身判成违规（本测试第一版就是这么挂的，与 ``test_kb_search_core``
    里那条"直接搜字符串会把模块头注释算进去"的注释是同一类坑）。
    """
    tree = _parse_src(os.path.join("src", "icons.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("PyQt6"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("PyQt6"), node.module
        elif isinstance(node, ast.Name):
            assert "PyQt6" not in node.id, node.id
        elif isinstance(node, ast.Attribute):
            assert "PyQt6" not in node.attr, node.attr


# ====================================================================
# 5. 文案前缀剥离
# ====================================================================
@pytest.mark.parametrize("raw,want", [
    ("🧩  碎片工作台", "碎片工作台"),
    ("🔌  插件中心", "插件中心"),
    ("⚙  设置", "设置"),
    ("⚙️ 设置", "设置"),
    ("❓  使用说明", "使用说明"),
    ("🤖 AI 助手", "AI 助手"),
    ("✂ 文本工坊", "文本工坊"),        # 杂项符号与装饰符号区
    ("⏰ 周期任务", "周期任务"),         # 技术与杂项符号区
    ("▶ 播放", "播放"),                # 几何图形区
    ("碎片工作台", "碎片工作台"),        # 本来就没有前缀
    ("  两侧空白  ", "两侧空白"),
    ("① 第一步", "① 第一步"),          # 带圈数字不是图标，不动
], ids=[
    "puzzle-replace", "plug", "gear", "gear-vs16", "question", "robot",
    "scissors", "alarm", "triangle", "no-prefix", "whitespace",
    "circled-digit",
])
def test_strip_leading_emoji(raw, want):
    assert icons.strip_leading_emoji(raw) == want


@pytest.mark.parametrize("raw", ["🚀", "🧩📋", "⚙️"])
def test_strip_leading_emoji_keeps_pure_icon_text(raw):
    """整串都是图标时原样保留 —— 剥成空串会让导航项变成不可识别的空条。"""
    assert icons.strip_leading_emoji(raw) == raw


@pytest.mark.parametrize("raw", ["", None, 0, 12.5])
def test_strip_leading_emoji_passes_through_non_str(raw):
    assert icons.strip_leading_emoji(raw) == raw


def test_strip_leading_emoji_only_strips_a_leading_cluster():
    # 中间的图标不能被剥掉（正文里的 emoji 属于 P2，不动）
    assert icons.strip_leading_emoji("碎片有个 🧩 图标") == "碎片有个 🧩 图标"


# ====================================================================
# 6. 覆盖清单：图标表 ↔ 导航键表
# ====================================================================
def test_nav_icon_covers_all_builtin_nav_keys():
    from_nav_layout = set(NAV_FIXED_ITEM_GROUP)
    for keys in _BASE_GROUP_KEYS.values():
        from_nav_layout.update(keys)
    assert set(icons.NAV_ICON) == from_nav_layout, (
        "NAV_ICON 与 nav_layout 的固定键不一致：多 %s，少 %s"
        % (sorted(set(icons.NAV_ICON) - from_nav_layout),
           sorted(from_nav_layout - set(icons.NAV_ICON))))


def test_main_window_tables_agree_with_nav_layout_and_icon_map():
    """三张表必须互相一致（AST 取字面量，不 import GUI）。

    这是"新增导航页忘了配图标"这类静默缺陷的钉子：新页必须在
    ``NAV_PAGE_INDEX`` / ``NAV_PAGE_TITLES`` / ``icons.NAV_ICON`` 三处都登记。
    """
    idx_keys = set(_module_level_dict_keys(
        os.path.join("src", "main_window.py"), "NAV_PAGE_INDEX"))
    title_keys = set(_module_level_dict_keys(
        os.path.join("src", "main_window.py"), "NAV_PAGE_TITLES"))
    fixed_keys = set(_module_level_dict_keys(
        os.path.join("src", "main_window.py"), "NAV_PAGE_TITLES_FIXED"))
    assert idx_keys == title_keys, "NAV_PAGE_INDEX 与 NAV_PAGE_TITLES 键不一致"
    assert set(icons.NAV_ICON) == idx_keys | fixed_keys


def test_every_nav_icon_value_names_a_real_icon():
    for key, name in icons.NAV_ICON.items():
        assert icons.has_icon(name), "导航键 %r 指向未登记的图标 %r" % (key, name)
    assert icons.has_icon(icons.PLUGIN_PAGE_ICON)


def test_has_icon_is_defensive():
    assert icons.has_icon("tasks") is True
    assert icons.has_icon("nope") is False
    assert icons.has_icon(None) is False
    assert icons.has_icon(123) is False


# ====================================================================
# 7. 渲染层（Qt）
# ====================================================================
def test_icon_pixmap_returns_painted_pixmap():
    from src import icon_render
    _app()
    pm = icon_render.icon_pixmap("tasks", 24, "#123456")
    assert pm.width() >= 24 and pm.height() >= 24
    img = pm.toImage()
    painted = sum(1 for y in range(img.height()) for x in range(img.width())
                  if img.pixelColor(x, y).alpha() > 0)
    assert painted > 0, "icon_pixmap 画出来是空的"


def test_icon_pixmap_cache_key_includes_size_and_color():
    from src import icon_render
    _app()
    a = icon_render.icon_pixmap("tasks", 20, "#111111")
    b = icon_render.icon_pixmap("tasks", 20, "#111111")
    assert a is b, "同参数应命中同一 QPixmap"
    assert a is not icon_render.icon_pixmap("tasks", 20, "#222222"), "异色不应共用"
    assert a is not icon_render.icon_pixmap("tasks", 24, "#111111"), "异尺寸不应共用"


def test_icon_pixmap_cache_key_normalizes_color_notations():
    """同色的不同写法（#fff / QColor）必须落到同一个缓存键上。"""
    from PyQt6.QtGui import QColor

    from src import icon_render
    _app()
    a = icon_render.icon_pixmap("nav", 20, "#112233")
    b = icon_render.icon_pixmap("nav", 20, QColor("#112233"))
    assert a is b


def test_icon_path_raises_on_unknown_name():
    from src import icon_render
    _app()
    with pytest.raises(KeyError):
        icon_render.icon_path("这个名字不存在")


def test_icon_has_both_on_and_off_pixmaps():
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QIcon

    from src import icon_render
    _app()
    ic = icon_render.icon("tasks", 16, "#FF0000", on_color="#0000FF")
    off = ic.pixmap(QSize(16, 16), QIcon.Mode.Normal, QIcon.State.Off)
    on = ic.pixmap(QSize(16, 16), QIcon.Mode.Normal, QIcon.State.On)
    assert off.toImage() != on.toImage(), "On / Off 两态必须成像不同"


def test_current_dpr_is_safe_without_screen():
    from src import icon_render
    # 有 app 时应返回正数；没有 app 时返回 1.0 —— 两条路都不能抛
    assert icon_render.current_dpr() > 0


# ====================================================================
# 8. 控件接线（IconLabel / PageTitle）
# ====================================================================
def test_icon_label_color_property_and_setter():
    from src.controls import IconLabel
    _app()
    lbl = IconLabel("tasks", 18, "#123456")
    assert lbl.color.name().upper() == "#123456"
    lbl.set_color("#654321")
    assert lbl.color.name().upper() == "#654321"
    assert lbl.icon_name == "tasks"


def test_page_title_keeps_page_title_object_name():
    """★ 文字标签必须仍挂 ``pageTitle`` —— QSS 命中口径不能变。"""
    from src.controls import PageTitle
    _app()
    row = PageTitle("fragments", "碎片工作台")
    assert row.label.objectName() == "pageTitle"
    assert row.text() == "碎片工作台"
    assert row.icon_name == "fragments"


def test_page_title_falls_back_to_default_theme_without_host():
    """host 为 None 时回退 DEFAULT_THEME 的取色，且显式 apply_theme 仍生效。"""
    from src.constants import DEFAULT_THEME
    from src.controls import PageTitle
    from src.theme import get_colors
    _app()
    row = PageTitle("tasks", "日程任务")
    assert row.text() == "日程任务"
    assert row.icon.color.name().upper() == \
        get_colors(DEFAULT_THEME)["text"].upper()
    row.apply_theme("light")
    assert row.icon.color.name().upper() == \
        get_colors("light")["text"].upper()


def test_page_title_host_without_theme_signal_is_tolerated():
    from PyQt6.QtCore import QObject

    from src.controls import PageTitle
    _app()

    class Bare(QObject):
        pass

    row = PageTitle("notes", "笔记管理", Bare())
    assert row.text() == "笔记管理"


def test_page_title_tolerates_non_signal_theme_changed_attribute():
    """★ 宿主有 ``theme_changed`` **属性但不是信号**时不许炸。

    只判 ``is not None`` 会漏掉这一形态：``test_ui_scale.py`` 的宿主替身就是
    ``SimpleNamespace(theme_changed=SimpleNamespace(...))``，属性在、``connect``
    不在 —— 2026-09-30 全量 pytest 因此挂了三个 error。
    """
    import types

    from src.controls import PageTitle
    _app()

    host = types.SimpleNamespace(
        theme_changed=types.SimpleNamespace(connect=None, emit=None),
        _theme="light")
    row = PageTitle("settings", "设置", host)
    assert row.text() == "设置"
    # 非信号属性必须被忽略，显式换主题仍要走通
    row.apply_theme("dark")
    from src.theme import get_colors
    assert row.icon.color.name().upper() == \
        get_colors("dark")["text"].upper()

    # 只带 emit、连 connect 属性都没有的形态（test_ui_scale 的真实替身）
    host2 = types.SimpleNamespace(
        theme_changed=types.SimpleNamespace(emit=lambda name: None),
        _theme="dark")
    row2 = PageTitle("notes", "笔记管理", host2)
    assert row2.text() == "笔记管理"


def test_page_title_subscribes_to_theme_changed_and_follows():
    from PyQt6.QtCore import QObject, pyqtSignal

    from src.controls import PageTitle
    from src.theme import get_colors
    _app()

    class Host(QObject):
        theme_changed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self._theme = "light"

    host = Host()
    row = PageTitle("tasks", "日程任务", host)
    assert row.icon.color.name().upper() == \
        get_colors("light")["text"].upper()

    host._theme = "dark"
    host.theme_changed.emit("dark")
    assert row.icon.color.name().upper() == \
        get_colors("dark")["text"].upper()


# ====================================================================
# 9. IconButton 接线（P1 动作按钮，2026-10-01 批次）
# ====================================================================
P1_ACTION_ICONS = {"pin", "trash", "refresh", "edit", "save", "search",
                   "close", "minimize", "maximize", "restore",
                   "moon", "sun", "plus", "minus"}


def test_p1_action_icons_are_registered():
    """P1 的 14 个动作图标必须全部登记（漏一个 = 位点替换处 KeyError）。"""
    missing = P1_ACTION_ICONS - set(icons.ICON_PATHS)
    assert not missing, "缺少动作图标：%s" % sorted(missing)


def test_icon_button_keeps_object_name_and_square_size():
    """★ objectName 原样透传 —— QSS ``#iconBtn`` 等契约不能破。"""
    from src.controls import IconButton
    _app()
    btn = IconButton("trash", size=36, icon_size=16, object_name="iconBtn",
                     tooltip="关闭窗口", danger=True)
    assert btn.objectName() == "iconBtn"
    assert (btn.width(), btn.height()) == (36, 36)
    assert btn.toolTip() == "关闭窗口"
    assert btn.property("danger") == "true"     # QSS [danger="true"] 选择器
    assert btn.icon_name == "trash"


def test_icon_button_text_mode_keeps_text_and_no_fixed_size():
    from src.controls import IconButton
    _app()
    btn = IconButton("refresh", text="刷新", checkable=True)
    assert btn.text() == "刷新"
    assert btn.isCheckable()
    # 文字模式不设固定尺寸（宽度交给布局 + QSS padding；QWIDGETSIZE_MAX）
    assert btn.maximumSize().width() == 16777215


def test_icon_button_two_state_and_disabled_pixmaps():
    """Off=text_secondary / On=primary / Disabled=text_disabled 三态齐全。"""
    from PyQt6.QtGui import QIcon

    from src.controls import IconButton
    _app()
    btn = IconButton("tasks", icon_size=16, checkable=True)
    ic = btn.icon()
    pm_off = ic.pixmap(32, 32, QIcon.Mode.Normal, QIcon.State.Off)
    pm_on = ic.pixmap(32, 32, QIcon.Mode.Normal, QIcon.State.On)
    pm_dis = ic.pixmap(32, 32, QIcon.Mode.Disabled)
    assert not pm_off.isNull() and not pm_on.isNull() and not pm_dis.isNull()
    assert pm_off.toImage() != pm_on.toImage(), "On/Off 两态取色没有区分"


def test_icon_button_hover_swaps_off_state_pixmap():
    from src.controls import IconButton
    _app()
    btn = IconButton("search", icon_size=16)
    pm_rest = btn.icon().pixmap(32, 32).toImage()
    btn._hovered = True
    btn._refresh_icon()
    pm_hover = btn.icon().pixmap(32, 32).toImage()
    btn._hovered = False
    btn._refresh_icon()
    assert pm_hover != pm_rest, "悬停没有换 hover 色位图"
    assert btn.icon().pixmap(32, 32).toImage() == pm_rest, "移出没有还原"


def test_icon_button_set_icon_name_swaps_glyph():
    from src.controls import IconButton
    _app()
    btn = IconButton("moon", icon_size=16)
    pm_moon = btn.icon().pixmap(32, 32).toImage()
    btn.set_icon_name("sun")
    assert btn.icon_name == "sun"
    assert btn.icon().pixmap(32, 32).toImage() != pm_moon


def test_icon_button_subscribes_to_host_theme_changed():
    from PyQt6.QtCore import QObject, pyqtSignal

    from src import icon_render
    from src.controls import IconButton
    from src.theme import get_colors
    _app()

    class Host(QObject):
        theme_changed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self._theme = "light"

    host = Host()
    btn = IconButton("refresh", icon_size=32, host=host)
    pm = btn.icon().pixmap(32, 32).toImage()
    ref_light = icon_render.icon_pixmap(
        "refresh", 32, get_colors("light")["text_secondary"]).toImage()
    assert pm == ref_light
    host._theme = "dark"
    host.theme_changed.emit("dark")
    ref_dark = icon_render.icon_pixmap(
        "refresh", 32, get_colors("dark")["text_secondary"]).toImage()
    assert btn.icon().pixmap(32, 32).toImage() == ref_dark


def test_icon_button_tolerates_non_signal_theme_changed_attribute():
    """SimpleNamespace 替身（connect=None / 只有 emit）不许炸 —— PageTitle 同款。"""
    import types

    from src.controls import IconButton
    _app()
    host = types.SimpleNamespace(
        theme_changed=types.SimpleNamespace(connect=None, emit=None),
        _theme="light")
    btn = IconButton("save", icon_size=16, host=host)
    assert btn.icon_name == "save"
    btn.apply_theme("dark")     # 显式喂主题仍要走通


def test_icon_button_detects_theme_from_parent_chain():
    """运行期动态创建、没接宿主管线的按钮沿父链找 ``_theme`` 兜底。"""
    from PyQt6.QtWidgets import QWidget

    from src.controls import IconButton
    _app()

    class Inner(QWidget):
        pass

    class Panel(QWidget):
        _theme = "dark"

    panel = Panel()
    inner = Inner(panel)
    btn = IconButton("trash", parent=inner)     # host=None、隔一层
    assert btn._detect_theme() == "dark"
    # 链上出现非主题取值的同名属性也不许误判
    panel._theme = "blue-ish"
    assert btn._detect_theme() is None


def test_icon_render_icon_disabled_pixmap_uses_given_color():
    from PyQt6.QtGui import QIcon

    from src import icon_render
    _app()
    ic = icon_render.icon("tasks", 16, "#123456", disabled_color="#654321")
    pm = ic.pixmap(32, 32, QIcon.Mode.Disabled).toImage()
    ref = icon_render.icon_pixmap("tasks", 16, "#654321").toImage()
    assert pm == ref
