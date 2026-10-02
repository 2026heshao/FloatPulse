# -*- coding: utf-8 -*-
"""controls.EmptyState（UI 强化方案 A3 通用化）回归钉子。

纯控件级测试：构造契约 / 两态切换 / attach_to 覆盖层几何 / 鼠标穿透 /
★「内部不得出现 pageTitle」护栏（verify_icons_render H 段断言每页恰好
一个 PageTitle，EmptyState 混进去会让 8 页全红）。
8 页真窗口的空态可见性与截图走 tools/verify_empty_state.py（离屏端到端）。
"""


from src import icons

_APP = None


def _app():
    """进程内单例 QApplication（引用必须留在模块级，见 test_icons 的坑）"""
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ====================================================================
# 1. 构造契约
# ====================================================================
def test_construction_fields_and_object_name():
    from PyQt6.QtCore import Qt

    from src.controls import EmptyState
    _app()
    es = EmptyState("tasks", "还没有任务", "在上方输入待办",
                    object_name="demoEmpty")
    assert es.objectName() == "demoEmpty"
    assert es._title.text() == "还没有任务"
    assert es._desc.text() == "在上方输入待办"
    assert es.action is None and es._action is None
    # 无动作钮 → 覆盖层对鼠标全透明（右键/滚轮落到列表本体）
    assert es.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_qss_object_names_unchanged_from_fragments_legacy():
    """标题 sectionLabel / 提示 hintLabel / 动作 secondaryBtn —— 老契约。"""
    from src.controls import EmptyState
    _app()
    es = EmptyState("fragments", "t", "h", action_text="清空筛选条件")
    assert es._title.objectName() == "sectionLabel"
    assert es._desc.objectName() == "hintLabel"
    assert es.action.objectName() == "secondaryBtn"
    # 有动作钮 → 必须可交互（不透明）
    assert not es.testAttribute(
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute
        .WA_TransparentForMouseEvents)


def test_action_click_wires_callback():
    """fragments 旧契约：_empty_state._action 点击触发清空筛选。"""
    from src.controls import EmptyState
    _app()
    hits = []
    es = EmptyState("fragments", "t", "h", action_text="清空筛选条件",
                    on_action=lambda: hits.append(1))
    assert hasattr(es, "_action") and es._action is es.action
    es.action.click()
    assert hits == [1]


def test_no_page_title_inside():
    """★ EmptyState 内不得出现 pageTitle（H 段每页恰一个 PageTitle）。"""
    from PyQt6.QtWidgets import QLabel

    from src.controls import EmptyState, PageTitle
    _app()
    es = EmptyState("notes", "t", "h")
    assert not [w for w in es.findChildren(QLabel)
                if w.objectName() == "pageTitle"]
    assert not es.findChildren(PageTitle)


def test_default_page_icons_are_registered():
    """8 页 + 两态共用的图标名必须在 icons.py 登记（漏一个=KeyError）。"""
    for name in ("fragments", "tasks", "notes", "knowledge", "assets",
                 "nav", "plugins", "apps", "search"):
        assert icons.has_icon(name), name


# ====================================================================
# 2. 两态切换
# ====================================================================
def test_set_state_swaps_icon_title_hint_action():
    from src.controls import EmptyState
    _app()
    es = EmptyState("fragments", "a", "ha", action_text="清空筛选条件")
    es.set_state("search", "b", "hb", show_action=True)
    assert es.icon_label.icon_name == "search"
    assert es._title.text() == "b" and es._desc.text() == "hb"
    assert es.action.isVisibleTo(es)
    es.set_state("fragments", "a", "ha", show_action=False)
    assert not es.action.isVisibleTo(es)
    assert es.icon_label.icon_name == "fragments"


def test_apply_theme_changes_icon_color():
    from src.controls import EmptyState
    from src.theme import get_colors
    _app()
    es = EmptyState("tasks", "t", "h")
    es.apply_theme("dark")
    assert es.icon_label.color.name().upper() == \
        get_colors("dark")["text_placeholder"].upper()


# ====================================================================
# 3. attach_to 覆盖层
# ====================================================================
def test_attach_to_follows_host_resize():
    from PyQt6.QtWidgets import QListWidget

    from src.controls import EmptyState
    _app()
    lst = QListWidget()
    lst.resize(400, 300)
    lst.show()
    _app().processEvents()
    es = EmptyState("notes", "t", "h")
    es.attach_to(lst)
    assert es.parentWidget() is lst
    assert not es.isVisible()                    # 初始隐藏，调用方控制显隐
    lst.resize(640, 480)
    _app().processEvents()
    assert (es.geometry().width(), es.geometry().height()) == (640, 480)


def test_overlay_without_action_passes_mouse_to_list():
    """覆盖层无动作钮时鼠标穿透：右键/滚轮落到列表本体。"""
    from PyQt6.QtWidgets import QListWidget

    from src.controls import EmptyState
    _app()
    lst = QListWidget()
    es = EmptyState("tasks", "t", "h")
    es.attach_to(lst)
    es.show()
    # WA_TransparentForMouseEvents 下，命中测试直接穿透到父列表
    child = lst.childAt(es.geometry().center())
    assert child is not es


# ====================================================================
# 4. ★ parent 误传护栏（2026-10-02 实战：屏幕左上角闪黑框）
# ====================================================================
def test_no_positional_args_beyond_hint_in_emptystate_calls():
    """全仓 EmptyState 调用只准前 3 个位置参数（icon/title/hint）。

    第 4 个位置参数是 ``action_text``，**不是** ``parent``。nav_panel 曾写成
    ``EmptyState("nav", "暂无站点", "...", self)`` —— 于是 ``self``（_NavList）被
    当成动作钮文本、``parent`` 恒为 None，空态控件成为顶层窗口：首次进入网址
    导航页（行列表还没填、``_relayout`` 里 show 了一次）会在屏幕左上角闪一个
    黑框，数据到位后 hide 即消失。改成关键字 ``parent=`` 修复。

    ★ 断言式护栏：源码文本层面禁止位置传参，任何调用点复发即红。
    """
    import ast
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skip = {"__pycache__", "build", "build2", "dist", "dist2", ".pytest_cache"}
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn_node = node.func
                name = getattr(fn_node, "id", None) or getattr(
                    fn_node, "attr", None)
                if name == "EmptyState" and len(node.args) > 3:
                    bad.append(f"{os.path.relpath(path, root)}:{node.lineno}")
    assert not bad, (
        "EmptyState 第 4 个位置参数是 action_text 不是 parent，"
        f"请改用关键字 action_text=/parent=：{bad}")


def test_nav_empty_state_is_child_not_toplevel():
    """网址导航空态必须挂在 _NavList 上，且不得是顶层窗口（黑框复现钉子）。"""
    from src.nav_panel import _NavList
    _app()
    lst = _NavList(None)
    es = lst._empty_label
    assert es.parentWidget() is lst
    assert not es.isWindow()
    assert es.action is None                    # 位置参数没被当成动作钮
    es.show()                                   # 即便被 show 也不能变成独立弹窗
    assert not es.isWindow()
