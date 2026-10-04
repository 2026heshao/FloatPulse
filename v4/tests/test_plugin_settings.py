# -*- coding: utf-8 -*-
"""插件独立设置护栏（2026-10-04）——manifest.settings 校验 + 存储合并语义 + ctx 契约。

三层契约，全部「从合法基座出发只改一处」构造用例（护栏必须能红灯，
防假护栏）：
  A validate_manifest 的 settings 校验：合法形态放行、非法形态逐条拒载，
    错误信息指明第几条哪个字段
  B 存储层 plugin_settings：磁盘值与 schema 合并（脏值静默回落 default、
    遗留键原样保留）、原子写、plugin_id 防护、禁 import 清单（PyQt6 /
    config / json_store 一律不得出现）
  C ctx 契约：get_setting 合并语义 + settings_changed 信号（守卫模式
    可订阅、重复订阅幂等、订阅者异常隔离、loader 把 schema 派生进 ctx）
"""

import ast
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)

from src import plugin_settings                                    # noqa: E402
from src.plugin_api import PluginContext, PluginSignal             # noqa: E402
from src.plugin_loader import (                                    # noqa: E402
    MAX_PLUGIN_SETTINGS, PluginLoader, validate_manifest,
)


def _base_manifest(**extra):
    data = {
        "id": "demo", "name": "演示插件", "version": "1.0.0",
        "entry": "main.py",
        "actions": [{"id": "demo.act", "title": "做件事"}],
    }
    data.update(extra)
    return data


# 经 validate_manifest 归一化后的合法 schema（存储层 / ctx 测试共用——
# 归一化会给 int/float 补 min/max，测试断言按归一化后的形状写）
_VALID_SETTINGS = [
    {"key": "max_results", "label": "最大返回数", "type": "int",
     "default": 20, "min": 1, "max": 100},
    {"key": "search_notes", "label": "搜索笔记", "type": "bool",
     "default": True},
    {"key": "engine", "label": "排序算法", "type": "enum",
     "default": "bm25", "choices": ["bm25", "tfidf"]},
    {"key": "threshold", "label": "阈值", "type": "float",
     "default": 0.5, "min": 0.0, "max": 1.0},
]

_SCHEMA = validate_manifest(_base_manifest(settings=_VALID_SETTINGS))[0]["settings"]
_DEFAULTS = {e["key"]: e["default"] for e in _SCHEMA}


# ====================================================================
# A. validate_manifest：settings 校验
# ====================================================================
class TestManifestSettingsValid:
    def test_without_settings_normalized_to_empty(self):
        m, err = validate_manifest(_base_manifest())
        assert m is not None and err == ""
        assert m["settings"] == []

    def test_empty_list_and_none_are_legal(self):
        for raw in ([], None):
            m, err = validate_manifest(_base_manifest(settings=raw))
            assert m is not None and err == ""
            assert m["settings"] == []

    def test_valid_entries_normalized(self):
        m, err = validate_manifest(_base_manifest(settings=_VALID_SETTINGS))
        assert m is not None and err == ""
        assert m["settings"] == _SCHEMA
        # 归一化形状：bool 不带 min/max，enum 带 choices，数值带边界
        assert m["settings"][1] == {"key": "search_notes",
                                    "label": "搜索笔记",
                                    "type": "bool", "default": True}
        assert m["settings"][2]["choices"] == ["bm25", "tfidf"]

    def test_entry_field_not_shadowed_by_settings_loop(self):
        """回归钉子：settings 校验循环的变量名曾覆盖外层 entry（入口路径），
        返回值里 entry 变成 settings 条目 dict，插件加载直接 TypeError。"""
        m, err = validate_manifest(_base_manifest(settings=_VALID_SETTINGS))
        assert m is not None and err == ""
        assert m["entry"] == "main.py"
        assert isinstance(m["entry"], str)

    def test_missing_min_max_get_defaults(self):
        m, _ = validate_manifest(_base_manifest(settings=[
            {"key": "n", "label": "数量", "type": "int", "default": 3}]))
        assert m["settings"][0]["min"] == 0
        assert m["settings"][0]["max"] == 100000

    def test_float_accepts_int_default(self):
        m, err = validate_manifest(_base_manifest(settings=[
            {"key": "ratio", "label": "比例", "type": "float",
             "default": 1, "min": 0, "max": 2}]))
        assert m is not None and err == ""
        assert m["settings"][0]["default"] == 1

    def test_twelve_entries_allowed(self):
        many = [{"key": f"k{i}", "label": f"项{i}", "type": "bool",
                 "default": False} for i in range(MAX_PLUGIN_SETTINGS)]
        m, err = validate_manifest(_base_manifest(settings=many))
        assert m is not None and err == ""
        assert len(m["settings"]) == MAX_PLUGIN_SETTINGS


class TestManifestSettingsInvalid:
    """每个用例 = 合法基座 + 一处改动 → 必须拒载（红灯可复现）"""

    def _reject(self, raw, fragment=""):
        m, err = validate_manifest(_base_manifest(settings=raw))
        assert m is None, f"应拒载却通过：{raw!r}"
        assert fragment in err, f"错误信息缺「{fragment}」：{err}"

    def test_not_a_list(self):
        self._reject("int", "settings 必须是条目列表")

    def test_too_many_entries(self):
        many = [{"key": f"k{i}", "label": "项", "type": "bool",
                 "default": False} for i in range(MAX_PLUGIN_SETTINGS + 1)]
        self._reject(many, "settings 条数超限")

    def test_entry_not_dict(self):
        self._reject([{"key": "a", "label": "甲", "type": "bool",
                       "default": False}, 42], "settings[1] 不是 JSON 对象")

    def test_key_illegal_forms(self):
        for bad in ("", "MaxResults", "1st", "has space", "a" * 65, None):
            self._reject([{"key": bad, "label": "甲", "type": "bool",
                           "default": False}], "settings[0].key 非法")

    def test_key_duplicate(self):
        dup = {"key": "a", "label": "甲", "type": "bool", "default": False}
        self._reject([dup, dict(dup)], "settings[1].key 与其他条目重复")

    def test_label_empty_or_too_long(self):
        self._reject([{"key": "a", "label": "", "type": "bool",
                       "default": False}], "settings[0].label")
        self._reject([{"key": "a", "label": "字" * 31, "type": "bool",
                       "default": False}], "settings[0].label 超长")

    def test_type_missing_or_unknown(self):
        for bad in (None, "str", "BOOL", "boolean"):
            self._reject([{"key": "a", "label": "甲", "type": bad,
                           "default": False}], "settings[0].type 非法")

    def test_bool_default_must_be_bool(self):
        # 1 会被 isinstance(x, bool) 放过吗？不会——int 不是 bool。
        # 但反过来 int 条目的 default 写 true 常见于手滑，必须拒。
        self._reject([{"key": "a", "label": "甲", "type": "bool",
                       "default": 1}], "settings[0].default 必须是 bool")

    def test_int_default_rejects_bool(self):
        # bool 是 int 子类：True 落在 [0,1] 内若不做显式挡截会静默生效
        self._reject([{"key": "a", "label": "甲", "type": "int",
                       "default": True, "min": 0, "max": 1}],
                     "settings[0].default 不能是 bool")

    def test_int_default_rejects_float(self):
        self._reject([{"key": "a", "label": "甲", "type": "int",
                       "default": 1.5, "min": 0, "max": 2}],
                     "settings[0].default 必须是整数")

    def test_int_min_must_be_int(self):
        self._reject([{"key": "a", "label": "甲", "type": "int",
                       "default": 1, "min": 0.5, "max": 2}],
                     "settings[0].min 必须是整数")

    def test_default_out_of_bounds(self):
        self._reject([{"key": "a", "label": "甲", "type": "int",
                       "default": 9, "min": 1, "max": 5}],
                     "settings[0].default 9 越界")
        self._reject([{"key": "a", "label": "甲", "type": "float",
                       "default": 1.5, "min": 0.0, "max": 1.0}],
                     "settings[0].default 1.5 越界")

    def test_min_greater_than_max(self):
        self._reject([{"key": "a", "label": "甲", "type": "int",
                       "default": 5, "min": 10, "max": 1}],
                     "settings[0].min 不能大于 max")

    def test_enum_default_must_be_in_choices(self):
        self._reject([{"key": "a", "label": "甲", "type": "enum",
                       "default": "x", "choices": ["y", "z"]}],
                     "settings[0].default 必须是 choices 之一")

    def test_enum_choices_empty_or_missing(self):
        for choices in ([], None):
            self._reject([{"key": "a", "label": "甲", "type": "enum",
                           "default": "x", "choices": choices}],
                         "settings[0].choices 必须是非空字符串列表")

    def test_enum_choices_non_string_or_blank(self):
        self._reject([{"key": "a", "label": "甲", "type": "enum",
                       "default": "x", "choices": ["x", 2]}],
                     "settings[0].choices 含非字符串或空白项")
        self._reject([{"key": "a", "label": "甲", "type": "enum",
                       "default": "x", "choices": ["x", "  "]}],
                     "settings[0].choices 含非字符串或空白项")

    def test_enum_choices_duplicate(self):
        self._reject([{"key": "a", "label": "甲", "type": "enum",
                       "default": "x", "choices": ["x", "x"]}],
                     "settings[0].choices 含重复项")

    def test_enum_choices_over_ten(self):
        self._reject([{"key": "a", "label": "甲", "type": "enum",
                       "default": "c0",
                       "choices": [f"c{i}" for i in range(11)]}],
                     "settings[0].choices 超限")


class TestKbSearchSample:
    """端到端样例：kb-search 的 manifest.settings 必须合法且形状正确"""

    def test_kb_search_manifest_settings(self):
        with open(os.path.join(ROOT, "plugins", "kb-search", "manifest.json"),
                  encoding="utf-8") as f:
            m, err = validate_manifest(json.load(f))
        assert m is not None and err == ""
        schema = {e["key"]: e for e in m["settings"]}
        assert set(schema) == {"max_results", "search_notes", "ranking"}
        assert schema["max_results"]["type"] == "int"
        assert schema["max_results"]["default"] == 60
        assert schema["search_notes"]["type"] == "bool"
        assert schema["search_notes"]["default"] is True
        assert schema["ranking"]["choices"] == ["bm25", "tfidf"]
        assert schema["ranking"]["default"] == "bm25"


# ====================================================================
# B. 存储层：合并语义 / 原子写 / 防护
# ====================================================================
class TestPluginSettingsStore:
    def test_missing_file_returns_defaults(self, tmp_path):
        vals = plugin_settings.get_all("demo", _SCHEMA, str(tmp_path))
        assert vals == _DEFAULTS

    def test_valid_disk_value_wins(self, tmp_path):
        plugin_settings.set_one("demo", _SCHEMA, "max_results", 33,
                                str(tmp_path))
        vals = plugin_settings.get_all("demo", _SCHEMA, str(tmp_path))
        assert vals["max_results"] == 33
        assert vals["search_notes"] == _DEFAULTS["search_notes"]

    def test_dirty_values_silently_fall_back(self, tmp_path):
        path = plugin_settings.settings_file(str(tmp_path), "demo")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"max_results": "33",          # 类型不符
                       "search_notes": 1,            # int ≠ bool
                       "engine": "bert",             # 不在 choices
                       "threshold": 1.5},            # 越界
                      f)
        vals = plugin_settings.get_all("demo", _SCHEMA, str(tmp_path))
        assert vals == _DEFAULTS

    def test_out_of_bounds_int_falls_back(self, tmp_path):
        plugin_settings.set_one("demo", _SCHEMA, "max_results", 99999,
                                str(tmp_path))
        vals = plugin_settings.get_all("demo", _SCHEMA, str(tmp_path))
        assert vals["max_results"] == _DEFAULTS["max_results"]

    def test_float_entry_accepts_int_disk_value(self, tmp_path):
        plugin_settings.set_one("demo", _SCHEMA, "threshold", 1,
                                str(tmp_path))
        vals = plugin_settings.get_all("demo", _SCHEMA, str(tmp_path))
        assert vals["threshold"] == 1

    def test_legacy_keys_preserved_after_set_one(self, tmp_path):
        path = plugin_settings.settings_file(str(tmp_path), "demo")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"max_results": 5, "legacy_from_old_version": [1, 2]}, f)
        plugin_settings.set_one("demo", _SCHEMA, "max_results", 9,
                                str(tmp_path))
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["max_results"] == 9
        assert raw["legacy_from_old_version"] == [1, 2]

    def test_set_one_rejects_undeclared_key(self, tmp_path):
        ok, msg = plugin_settings.set_one("demo", _SCHEMA, "hack", 1,
                                          str(tmp_path))
        assert ok is False and "声明" in msg
        assert not os.path.isfile(
            plugin_settings.settings_file(str(tmp_path), "demo"))

    def test_set_one_rejects_bad_value(self, tmp_path):
        for key, value in (("max_results", 0), ("max_results", "33"),
                           ("search_notes", 1), ("engine", "nope"),
                           ("threshold", "0.1")):
            ok, msg = plugin_settings.set_one("demo", _SCHEMA, key, value,
                                              str(tmp_path))
            assert ok is False, (key, value)
        # 全部被拒 → 没有文件落地
        assert not os.path.isfile(
            plugin_settings.settings_file(str(tmp_path), "demo"))

    def test_unsafe_plugin_id_rejected(self, tmp_path):
        for pid in ("", None, "../evil", "a/b"):
            ok, msg = plugin_settings.set_one(pid, _SCHEMA, "max_results", 5,
                                              str(tmp_path))
            assert ok is False, pid

    def test_get_all_unsafe_id_returns_defaults(self, tmp_path):
        vals = plugin_settings.get_all("../evil", _SCHEMA, str(tmp_path))
        assert vals == _DEFAULTS

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        plugin_settings.set_one("demo", _SCHEMA, "max_results", 7,
                                str(tmp_path))
        assert not os.path.isfile(
            plugin_settings.settings_file(str(tmp_path), "demo") + ".tmp")

    def test_corrupt_json_reads_as_empty_then_overwritten(self, tmp_path):
        path = plugin_settings.settings_file(str(tmp_path), "demo")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{oops not json")
        assert plugin_settings.get_all("demo", _SCHEMA, str(tmp_path)) \
            == _DEFAULTS
        ok, _ = plugin_settings.set_one("demo", _SCHEMA, "max_results", 8,
                                        str(tmp_path))
        assert ok
        assert plugin_settings.get_all("demo", _SCHEMA, str(tmp_path)) \
            ["max_results"] == 8

    def test_empty_schema_is_noop(self, tmp_path):
        assert plugin_settings.get_all("demo", [], str(tmp_path)) == {}
        ok, _msg = plugin_settings.set_one("demo", [], "x", 1, str(tmp_path))
        assert ok is False

    def test_no_base_dir_returns_defaults(self):
        # base 为空串 = 宿主未注入目录 → 降级为全 default，不抛异常
        assert plugin_settings.get_all("demo", _SCHEMA, "") == _DEFAULTS


def _import_modules(source_text):
    """源码文本里 import 涉及的模块路径集合（AST 级，能红灯）。

    ``import a.b`` → ``a.b``；``from x import y`` → ``x`` 与 ``x.y``——
    只有这样才能抓住 ``from src import config`` 这种「顶层名是 src、
    真正引入的是 config」的违规写法。
    """
    tree = ast.parse(source_text)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.level == 0:
            mods.add(node.module)
            for alias in node.names:
                mods.add(f"{node.module}.{alias.name}")
    return mods


def _forbidden_module(mods) -> str:
    """命中禁运清单的模块路径；没有返回空串"""
    for mod in mods:
        top = mod.split(".")[0]
        if top == "PyQt6":
            return mod
        if mod in ("config", "json_store") \
                or mod.startswith("src.config") \
                or mod.startswith("src.json_store"):
            return mod
    return ""


class TestStorageSourceContract:
    """存储层的依赖纪律：禁 PyQt6 / config / json_store（AST 级，能红灯）"""

    def test_source_has_no_pyqt6_or_forbidden_imports(self):
        src_path = os.path.join(BASE, "src", "plugin_settings.py")
        with open(src_path, encoding="utf-8") as f:
            mods = _import_modules(f.read())
        assert _forbidden_module(mods) == "", sorted(mods)
        # 反向验证：扫描器真能报出三种违规写法（防扫描器自身失效）。
        # PyQt6 的探针给「命中即返回」断言用 startswith——mods 是 set，
        # 两个元素都被禁时返回哪个取决于哈希序，钉死具体值会假红。
        assert _forbidden_module(_import_modules(
            "import json_store\n")) == "json_store"
        assert _forbidden_module(_import_modules(
            "from src import config\n")) == "src.config"
        assert _forbidden_module(_import_modules(
            "from PyQt6.QtWidgets import QWidget\n")).startswith("PyQt6")


# ====================================================================
# C. ctx 契约：get_setting / settings_changed / loader 派生
# ====================================================================
class TestPluginSignal:
    def test_connect_emit_disconnect(self):
        sig = PluginSignal()
        seen = []
        assert sig.connect(seen.append) is True
        n = sig.emit([("k1",)])
        assert n == 1 and seen == [[("k1",)]]
        assert sig.disconnect(seen.append) is True
        assert sig.emit("x") == 0

    def test_duplicate_connect_is_idempotent(self):
        sig = PluginSignal()
        seen = []
        sig.connect(seen.append)
        sig.connect(seen.append)              # 页面重建时容易重复订阅
        assert sig.emit(1) == 1
        assert seen == [1]

    def test_non_callable_rejected(self):
        sig = PluginSignal()
        assert sig.connect("not-callable") is False

    def test_slot_exception_isolated(self):
        sig = PluginSignal()
        seen = []

        def boom(_):
            raise RuntimeError("订阅者崩了")

        sig.connect(boom)
        sig.connect(seen.append)
        assert sig.emit("go") == 1            # 只有第二个槽成功
        assert seen == ["go"]


class TestContextSettingContract:
    def _ctx(self, tmp_path):
        return PluginContext(logger=None, data_dir_base=str(tmp_path),
                             plugin_id="demo",
                             manifest_settings=tuple(_SCHEMA))

    def test_get_setting_default_then_disk(self, tmp_path):
        ctx = self._ctx(tmp_path)
        assert ctx.get_setting("max_results") == _DEFAULTS["max_results"]
        plugin_settings.set_one("demo", _SCHEMA, "max_results", 66,
                                str(tmp_path))
        assert ctx.get_setting("max_results") == 66

    def test_get_setting_unknown_key_fallback(self, tmp_path):
        assert self._ctx(tmp_path).get_setting("nope", "FB") == "FB"

    def test_get_setting_without_schema_or_dir(self):
        # 未声明设置项 / 未注入数据目录 → fallback（降级语义）
        bare = PluginContext(logger=None, plugin_id="demo")
        assert bare.get_setting("max_results", "FB") == "FB"
        no_dir = PluginContext(logger=None, plugin_id="demo",
                               manifest_settings=tuple(_SCHEMA))
        assert no_dir.get_setting("max_results", "FB") == "FB"

    def test_for_plugin_carries_settings(self, tmp_path):
        root = PluginContext(logger=None, data_dir_base=str(tmp_path))
        derived = root.for_plugin("demo", "/plugin/dir", (),
                                  tuple(_SCHEMA))
        assert derived.plugin_id == "demo"
        assert derived.manifest_settings == tuple(_SCHEMA)
        assert derived.get_setting("engine") == "bm25"
        # 共享 ctx 的派生（不传 schema）→ get_setting 走 fallback
        plain = root.for_plugin("demo", "/plugin/dir")
        assert plain.get_setting("engine", "FB") == "FB"

    def test_settings_changed_signal_wired(self, tmp_path):
        ctx = self._ctx(tmp_path)
        seen = []
        sig = getattr(ctx, "settings_changed", None)
        connect = getattr(sig, "connect", None)
        assert callable(connect)
        connect(seen.append)
        assert sig.emit(["a", "b"]) == 1
        assert seen == [["a", "b"]]

    def test_loader_derives_settings_into_ctx(self, tmp_path):
        root = PluginContext(logger=None, data_dir_base=str(tmp_path))
        loader = PluginLoader(registry=None, ctx=root)
        ctx = loader._ctx_for("demo", "/plugin/dir", (), tuple(_SCHEMA))
        assert ctx.manifest_settings == tuple(_SCHEMA)
        assert ctx.get_setting("max_results") == _DEFAULTS["max_results"]

    def test_loader_falls_back_for_old_host_ctx(self):
        """旧宿主 ctx 的 for_plugin 只收 3 参 → loader 逐级降级不炸"""

        class OldCtx:
            def for_plugin(self, plugin_id, plugin_dir, capabilities):
                return ("old", plugin_id, capabilities)

        loader = PluginLoader(registry=None, ctx=OldCtx())
        assert loader._ctx_for("demo", "/d", (), _SCHEMA) == \
            ("old", "demo", ())
