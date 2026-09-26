# -*- coding: utf-8 -*-
"""静态架构测试：主窗口对子面板暴露的属性访问契约。

背景（2026-09-23 实锤 bug）
--------------------------------
``MainWindow.current_theme`` 曾是**普通方法**，但 6 处子面板调用点
（fragments_panel / knowledge_panel / settings_panel / tasks_panel）都按
属性访问 ``self._host.current_theme``。拿到的是 bound method 对象：

* ``get_colors(<method>)`` 查表失败 → **静默回退默认主题 dark**，
  于是浅色主题下碎片列表代理文字用 #E4E8EE 落在浅色底上几乎不可见；
* ``self._host.current_theme == "light"`` 恒为 False，设置页主题单选钮
  永远不反映当前主题。

这类"方法被当属性用"不会抛异常，只会静默出错（wrong colour / wrong
branch），因此值得用静态检查钉死 —— 本文件不导入 PyQt6，纯 AST 分析。

规则
----
对 ``MainWindow`` 中每一个被外部（src/*.py、knowledge_ball.py）以
``self._host.<name>`` **按值访问**（即后面不跟括号）的成员，
必须在以下之一里成立：

  1. 是 ``@property``（含 ``x.setter``）；
  2. 是类级赋值（如 ``data_changed = pyqtSignal(...)``、类常量）；
  3. 是实例属性（``self.<name> = ...`` 在类体内被赋值过）；
  4. 白名单显式登记（用于 connect 回调等确实要拿方法对象的场景）。
"""

import ast
import glob
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE, "src")
MAIN_WINDOW = os.path.join(SRC_DIR, "main_window.py")
HOST_ATTR = "_host"

# 确实需要"方法对象"的场景（connect 回调等）在此显式登记，避免误报。
METHOD_VALUE_ALLOWLIST = set()


def _parse(path):
    with io.open(path, encoding="utf-8") as f:
        return ast.parse(f.read())


def _collect_main_window_members():
    """返回 (properties, class_attrs, inst_attrs, methods)"""
    tree = _parse(MAIN_WINDOW)
    props, class_attrs, inst_attrs, methods = set(), set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            class_attrs.add(tgt.id)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    class_attrs.add(stmt.target.id)
                elif isinstance(stmt, ast.FunctionDef):
                    methods.add(stmt.name)
                    for dec in stmt.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id == "property":
                            props.add(stmt.name)
                        elif isinstance(dec, ast.Attribute) and dec.attr == "setter":
                            props.add(
                                dec.value.id if isinstance(dec.value, ast.Name) else "")
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            base = node.value
            if isinstance(base, ast.Name) and base.id == "self":
                inst_attrs.add(node.attr)
    return props, class_attrs, inst_attrs, methods


def _host_value_accesses():
    """扫描全部源码，找出按值访问的 ``self._host.<name>``。

    返回 {文件: set(成员名)}；后跟括号调用（``self._host.refresh_page(...)``）
    以及作为调用实参传给函数（``get_colors(self._host.current_theme)``）
    都算"按值访问"，因为 Python 里两者拿到的都是同一个对象。
    """
    result = {}
    files = sorted(glob.glob(os.path.join(SRC_DIR, "*.py")))
    kb = os.path.join(BASE, "knowledge_ball.py")
    if os.path.exists(kb):
        files.append(kb)
    for path in files:
        tree = _parse(path)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            inner = node.value
            if not (isinstance(inner, ast.Attribute) and inner.attr == HOST_ATTR):
                continue
            if isinstance(inner.value, ast.Name) and inner.value.id == "self":
                # 被调用 → 正常方法调用，跳过
                if isinstance(parents.get(node), ast.Call) \
                        and parents[node].func is node:
                    continue
                result.setdefault(os.path.basename(path), set()).add(node.attr)
    return result


PROPS, CLASS_ATTRS, INST_ATTRS, METHODS = _collect_main_window_members()


def test_current_theme_is_property():
    """current_theme 必须是 property（子面板一律按属性访问）"""
    assert "current_theme" in PROPS, (
        "MainWindow.current_theme 必须是 @property —— 否则子面板拿到 "
        "bound method，get_colors() 会静默回退默认主题（浅色主题文字不可见）"
    )


def test_anim_speed_is_property():
    """anim_speed 必须是 property（同类约定）"""
    assert "anim_speed" in PROPS


def test_no_method_accessed_as_attribute_on_host():
    """不得把 MainWindow 的普通方法当属性访问（会静默拿到方法对象）"""
    offenders = []
    for filename, names in _host_value_accesses().items():
        for name in sorted(names):
            if name in METHOD_VALUE_ALLOWLIST:
                continue
            if name in PROPS or name in CLASS_ATTRS or name in INST_ATTRS:
                continue
            if name in METHODS:
                offenders.append(f"{filename} -> self._host.{name}")
    assert not offenders, (
        "以下位置把 MainWindow 的普通方法当属性使用（拿到 bound method 而非值，"
        "不会报错但逻辑会静默走错分支）：\n  " + "\n  ".join(offenders)
        + "\n修法：给 MainWindow 的该成员加 @property，或改调用点加 ()。"
    )


def test_host_access_scan_is_not_empty():
    """护栏：扫描逻辑本身要能扫到东西，否则上面的测试形同虚设"""
    accesses = _host_value_accesses()
    assert accesses, "未扫到任何 self._host.<attr> 访问，检查扫描逻辑"
    assert "current_theme" in accesses.get("fragments_panel.py", set()), \
        "已知 fragments_panel 按值访问 current_theme，扫描结果异常"
