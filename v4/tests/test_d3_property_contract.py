# -*- coding: utf-8 -*-
"""D3 只读契约层钉子：MainWindow 新公开只读属性形态静态核验（纯 AST，不 import PyQt6）。

背景
----
成熟化路线图 4.3 / 拆分计划-wiring-2026-10-05 §3.3：子面板此前大量
``self._host._config`` / ``self._host._container`` 等私有名穿透。
T01 在 MainWindow 主类体补 8 个只读 @property + ``apply_window_opacity()``
公开门面；T02 把 43 处调用点换到公开名。本文件钉死：

1. 8 个新 property 必须存在且为 ``@property`` 形态
   （``config`` / ``note_manager`` / ``fragment_manager`` / ``task_manager``
   / ``temp_asset_manager`` / ``container`` / ``page_assets`` /
   ``page_app_launcher``）；
2. 其中 ``container`` / ``page_assets`` / ``page_app_launcher`` 不得退化成
   普通方法 —— 否则子面板拿到 bound method 静默回退
   （2026-09-23 current_theme 实锤 bug，见 test_host_attribute_contract 病历）；
3. ``apply_window_opacity()`` 公开门面必须存在；
4. 体例钉死：新 property 必须用 ``getattr(self, "_x", None)`` 兜底
   （防替身/早期构造期 AttributeError）；
5. 命名冲突防回归：主类体内不得出现同名普通方法/类属性覆盖
   （property 与方法同名在类体内是后写覆盖，属静默破坏）。

T02 落地后追加：src/ 内 ``self._host._`` 直取归零 + getattr 私有名白名单。
"""

import ast
import glob
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_WINDOW = os.path.join(BASE, "src", "main_window.py")

# T01 新增的 8 个只读契约 property（拆分计划 §3.3）
D3_READONLY_PROPS = (
    "config",
    "note_manager",
    "fragment_manager",
    "task_manager",
    "temp_asset_manager",
    "container",
    "page_assets",
    "page_app_launcher",
)


def _parse(path):
    with io.open(path, encoding="utf-8") as f:
        return ast.parse(f.read())


def _collect_main_window_class_body():
    """返回 MainWindow 主类体的直接子节点分类：
    (properties, methods, class_attrs)
    只看 ClassDef 直接子节点（与 test_mini_icon_size 同口径），
    不含 mixin 搬走后的成员 —— 契约要求公开面集中在主类体。
    """
    tree = _parse(MAIN_WINDOW)
    props, methods, class_attrs = {}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef):
                    is_prop = False
                    for dec in stmt.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id == "property":
                            is_prop = True
                    if is_prop:
                        props[stmt.name] = stmt
                    else:
                        methods.add(stmt.name)
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            class_attrs.add(tgt.id)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    class_attrs.add(stmt.target.id)
            break
    return props, methods, class_attrs


PROPS, METHODS, CLASS_ATTRS = _collect_main_window_class_body()


def test_d3_readonly_properties_exist():
    """8 个新只读契约 property 必须以 @property 形态存在于 MainWindow 主类体。"""
    missing = [n for n in D3_READONLY_PROPS if n not in PROPS]
    assert not missing, (
        "MainWindow 缺少 D3 只读契约 property：\n  " + "\n  ".join(missing)
        + "\n（体例照 sticky_manager：@property + getattr(self, \"_x\", None) 兜底）"
    )


def test_d3_props_are_not_plain_methods():
    """container / page_assets / page_app_launcher 不得是普通方法——
    子面板按属性访问，拿到 bound method 会静默回退（实锤病历见模块 docstring）。
    """
    for name in ("container", "page_assets", "page_app_launcher"):
        assert name not in METHODS, (
            f"MainWindow.{name} 必须是 @property 而非普通方法"
            f"（子面板按属性访问会拿到方法对象）"
        )


def test_apply_window_opacity_facade_exists():
    """apply_window_opacity 公开门面必须是普通方法（settings_panel 2072/2124 调用）。"""
    assert "apply_window_opacity" in METHODS, (
        "MainWindow.apply_window_opacity() 缺失（D3 公开门面，"
        "替代 settings_panel 对 _apply_window_opacity 的私有穿透）"
    )


def test_d3_props_use_getattr_fallback():
    """体例钉死：直取私有底的 6 个 property 必须用 getattr(self, "_x", None) 兜底。

    与 sticky_manager 同款；直接 ``return self._x`` 在替身/早期构造期
    会抛 AttributeError，破坏既有 ``is not None`` 判空语义。
    注：page_assets / page_app_launcher 是懒加载委托（拆分计划 §3.3
    "必须委托既有 @property，禁止旁路 _ensure_page_built"），不适用本条。
    """
    getattr_backed = [n for n in D3_READONLY_PROPS
                      if n not in ("page_assets", "page_app_launcher")]
    for name in getattr_backed:
        fn = PROPS[name]
        src = ast.dump(fn)
        assert "getattr" in src, (
            f"MainWindow.{name} property 体未用 getattr 兜底"
            f"（体例要求 getattr(self, \"_{name}\", None)）"
        )


def test_page_props_delegate_to_lazy_property():
    """page_assets / page_app_launcher 必须委托既有懒加载 @property（幂等建真页），
    不得旁路 _ensure_page_built 直读实例属性。"""
    for name, priv in (("page_assets", "_page_assets"),
                       ("page_app_launcher", "_page_app_launcher")):
        fn = PROPS[name]
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        assert priv in attrs, (
            f"MainWindow.{name} 必须委托既有懒加载 property {priv}"
            f"（访问即同步建真页，禁止旁路 _ensure_page_built）"
        )


def test_d3_props_no_name_shadowing():
    """防回归：主类体内不得出现同名普通方法/类属性覆盖新 property。"""
    for name in D3_READONLY_PROPS:
        assert name not in METHODS, (
            f"MainWindow.{name} 同时以普通方法存在——类体后写覆盖 property，属静默破坏"
        )
        assert name not in CLASS_ATTRS, (
            f"MainWindow.{name} 同时以类属性存在——覆盖 property，属静默破坏"
        )


# ======================================================================
# T02：D3 穿透归零（拆分计划-wiring-2026-10-05 §7 T02 验证项）
# ======================================================================
SRC_DIR = os.path.join(BASE, "src")

# getattr 私有名白名单：plugins_panel 903 豁免——MainWindow 上本就没有
# _rebuild_context_menu 钩子，恒走 plugins_changed 公开信号回退路径
# （拆分计划 §5 UNCLEAR 3），命中即取方法对象后判 callable。
GETATTR_PRIVATE_ALLOWLIST = {
    ("plugins_panel.py", "_rebuild_context_menu"),
}


def _host_private_accesses():
    """扫描 src/*.py，返回 [(文件, 形态, 成员名, 行号)]。

    形态 "direct"：``self._host._x`` 直取；
    形态 "getattr"：``getattr(self._host, "_x", …)``。
    """
    hits = []
    for path in sorted(glob.glob(os.path.join(SRC_DIR, "*.py"))):
        fname = os.path.basename(path)
        with io.open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_") \
                    and not node.attr.startswith("__"):
                inner = node.value
                if isinstance(inner, ast.Attribute) and inner.attr == "_host" \
                        and isinstance(inner.value, ast.Name) \
                        and inner.value.id == "self":
                    hits.append((fname, "direct", node.attr, node.lineno))
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "getattr" and node.args:
                target = node.args[0]
                if isinstance(target, ast.Attribute) and target.attr == "_host" \
                        and isinstance(target.value, ast.Name) \
                        and target.value.id == "self" \
                        and isinstance(node.args[1], ast.Constant) \
                        and isinstance(node.args[1].value, str) \
                        and node.args[1].value.startswith("_") \
                        and not node.args[1].value.startswith("__"):
                    hits.append((fname, "getattr", node.args[1].value, node.lineno))
    return hits


def test_no_host_private_penetration_in_src():
    """D3 禁令：src/ 内面板不得再直取/ getattr 宿主私有名（T02 收口）。

    全部改走 MainWindow 的公开 @property（config / container /
    note_manager / fragment_manager / task_manager / temp_asset_manager /
    page_assets / page_app_launcher / plugin_loader / current_theme /
    reapply_theme / apply_window_opacity）。
    """
    offenders = []
    for fname, kind, member, lineno in _host_private_accesses():
        if (fname, member) in GETATTR_PRIVATE_ALLOWLIST:
            continue
        offenders.append(f"{fname}:{lineno} {kind} self._host.{member}")
    assert not offenders, (
        "src/ 内残留宿主私有名穿透（D3 禁令），请改走公开 API：\n  "
        + "\n  ".join(offenders)
    )


def test_penetration_scan_is_not_empty():
    """护栏：豁免项 plugins_panel._rebuild_context_menu 必须真实存在——
    否则说明扫描逻辑或白名单失效（豁免被无声删掉时本测试提醒更新清单）。"""
    hits = _host_private_accesses()
    matched = [h for h in hits
               if (h[0], h[2]) in GETATTR_PRIVATE_ALLOWLIST]
    assert matched, (
        "未扫到任何白名单豁免项（plugins_panel._rebuild_context_menu）——"
        "若该豁免已移除，请同步清空 GETATTR_PRIVATE_ALLOWLIST"
    )
