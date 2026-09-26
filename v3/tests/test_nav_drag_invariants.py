# -*- coding: utf-8 -*-
"""静态契约测试：左栏拖拽换位的两条"顺序敏感"不变量。

背景（2026-09-24 实测踩坑，两个都是**静默**出错、不抛异常）
----------------------------------------------------------------
1) ``QPushButton`` 的 sizeHint 会被"按下态 + polish"污染并**永久**缓存
   全局 QSS 有 ``QPushButton:pressed { margin-top: 1px; }``。若在按钮
   仍处于 ``setDown(True)`` 时调用 ``style().unpolish/polish``，
   ``sizeHint`` 会被算成"多 1px"（35 → 36）并缓存下来；而 Qt 在
   ``setDown(False)`` 时**只 update 不 updateGeometry**，缓存不会失效
   → 被拖过的那个按钮行高永久 +1px，整列自上而下错位。
   所以 ``_clear_nav_drag_lift`` 必须**先 setDown(False)，再 polish**。

2) ``QLayout.activate()`` 只在布局"脏"时才真正重排
   拖拽落定动画未结束时再次拖拽，停掉的动画会把按钮留在**中间位置**；
   此时若直接 ``activate()``（布局不脏）会空转返回，随后冻结的"槽位 Y"
   就是动画中间值（间距被算错）→ 换成拖拽索引计算全乱、拖到错槽位。
   所以 ``_on_nav_drag_started`` 必须**先 invalidate()，再 activate()**。

本文件不导入 PyQt6，纯 AST 顺序分析。
"""

import ast
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_WINDOW = os.path.join(BASE, "src", "main_window.py")


def _func_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"main_window.py 中找不到方法 {name}()")


def _call_lines(func, attr, arg_is_false=False):
    """返回函数体内所有 ``<expr>.<attr>(...)`` 调用的行号。"""
    lines = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == attr):
            continue
        if arg_is_false:
            has_false = any(
                isinstance(a, ast.Constant) and a.value is False
                for a in node.args)
            if not has_false:
                continue
        lines.append(node.lineno)
    return lines


def _parse_main_window():
    with io.open(MAIN_WINDOW, encoding="utf-8") as f:
        return ast.parse(f.read())


def test_clear_nav_drag_lift_resets_down_before_polish():
    """setDown(False) 必须早于 unpolish/polish（否则 sizeHint 永久 +1px）"""
    tree = _parse_main_window()
    func = _func_node(tree, "_clear_nav_drag_lift")
    set_down = _call_lines(func, "setDown", arg_is_false=True)
    unpolish = _call_lines(func, "unpolish")
    polish = _call_lines(func, "polish")
    assert set_down, (
        "_clear_nav_drag_lift 里找不到 setDown(False)：全局 QSS 的 "
        "QPushButton:pressed{margin-top:1px} 会在按下态 polish 时把 "
        "sizeHint 算大并永久缓存 → 被拖按钮行高 +1px、整列错位"
    )
    assert unpolish and polish, "找不到 unpolish/polish 调用，检查实现"
    assert max(set_down) < min(unpolish), (
        f"setDown(False)（行 {set_down}）必须早于 unpolish（行 {unpolish}）——"
        "先复位按下态再重刷样式，sizeHint 才是正确行高"
    )
    assert max(set_down) < min(polish), (
        f"setDown(False)（行 {set_down}）必须早于 polish（行 {polish}）"
    )


def test_on_nav_drag_started_invalidates_layout_before_activate():
    """进入拖拽前必须 invalidate()+activate()，否则冻结到动画中间几何"""
    tree = _parse_main_window()
    func = _func_node(tree, "_on_nav_drag_started")
    invalidate = _call_lines(func, "invalidate")
    activate = _call_lines(func, "activate")
    assert invalidate, (
        "_on_nav_drag_started 里找不到 invalidate()：QLayout.activate() 只在"
        "布局脏时才真重排，上一轮落定动画被中断后按钮停在中间位置，"
        "空转的 activate() 会让冻结的槽位 Y 取到动画中间值"
    )
    assert activate, "找不到 activate() 调用，检查实现"
    assert min(invalidate) < min(activate), (
        f"invalidate()（行 {invalidate}）必须早于 activate()（行 {activate}）"
    )


def test_restore_nav_layout_uses_explicit_flag_as_truth():
    """_restore_nav_layout 以 _nav_free_layout 为唯一状态真相（不靠 spacer 判定）"""
    tree = _parse_main_window()
    func = _func_node(tree, "_restore_nav_layout")
    src = ast.dump(func)
    assert "_nav_free_layout" in src, (
        "_restore_nav_layout 必须以 self._nav_free_layout 作为'是否仍在自由"
        "布局'的唯一真相 —— 落定滑入路径会先移除 spacer，用 `spacer is None`"
        "判定会导致按钮没被插回布局、设置/说明被顶到上方"
    )
