# -*- coding: utf-8 -*-
"""
====================================================================
插件独立设置存储层  -  plugin_settings
====================================================================
插件经 manifest ``settings`` 字段声明设置项 schema（校验在
``plugin_loader.validate_manifest``），宿主插件中心按 schema 渲染表单；
本模块负责**存取**：``<float_data>/plugins/<插件id>/settings.json``。

为什么独立成文件、且**不进主 config**（2026-10-04 立项时的硬决定）：
  - 主 config 有 ``schema_version`` 迁移链与设置页十分类，插件设置一旦
    混进去，任何插件加一个设置项都要动 ``DEFAULT_CONFIG`` / 迁移链 /
    设置页白名单——契约面被无限放大；
  - 插件私有目录（``ctx.data_dir`` 约定）本来就是插件数据的位置，
    卸载插件时刻意保留（loader.uninstall 的既定语义），设置随目录走。

合并语义（读的时候发生，写的时候收紧）：
  - ``get_all``：只收 schema 里**声明过**的 key；磁盘值类型不符 / 越界
    → **静默回落 default**（记 warning，不抛异常不弹窗——脏值是升级 /
    手改文件的自然产物，不该变成用户的故障）；schema 未声明的磁盘
    遗留键**原样保留不删**（防插件降级/升级丢数据）。
  - ``set_one``：key 必须在 schema 里声明、值必须过类型与边界校验，
    否则拒绝写入（返回 ``(False, 原因)``）——写入侧收紧，读取侧宽容，
    脏数据面只减不增。

依赖纪律：纯逻辑、禁 PyQt6（无 GUI 环境可单测）；**禁 import**
``config`` / ``json_store``（避免环 + 避免把 schema_version 迁移链
牵连进来）；对 ``plugin_api`` 只用 ``is_safe_plugin_id``（防护目录穿越）。
写盘原子化：tmp + ``os.replace``（与 json_store / 插件搜索历史同一手法）。
====================================================================
"""

import json
import logging
import os
import re

from src.plugin_api import is_safe_plugin_id

# settings.json 固定放在插件私有目录根部（与搜索历史等同级）
SETTINGS_FILENAME = "settings.json"

# plugin_id 形态校验在 plugin_api（is_safe_plugin_id）；这里只补设置项
# key 的形态（manifest 校验期同一条正则，读取侧再防一次手改文件）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_LOG = logging.getLogger("floatpulse.plugin_settings")


def settings_file(data_dir_base: str, plugin_id: str) -> str:
    """settings.json 完整路径（不创建目录、不检查存在性）。

    ``data_dir_base`` 是 ``<float_data>/plugins`` 这一层；目录穿越由
    ``is_safe_plugin_id`` 在写入侧挡（读侧拼出的路径最多打不开而已）。
    """
    return os.path.join(str(data_dir_base or ""),
                        str(plugin_id or ""), SETTINGS_FILENAME)


def schema_entries(manifest_settings) -> list:
    """从 manifest.settings 里筛出**形态合法**的条目（防御性拷贝）。

    manifest 校验期已保证条目质量，这里再筛一遍是因为本模块也可能被
    拿手写的 schema 直接调（测试 / 未来调用方）：key 不合形态、条目不是
    dict、key 重复的一律丢弃，绝不把脏条目放进合并语义。
    """
    if not isinstance(manifest_settings, (list, tuple)):
        return []
    out = []
    seen = set()
    for entry in manifest_settings:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(entry))
    return out


def value_matches_schema(entry, value) -> bool:
    """单个值是否符合设置项 schema（类型 + 边界）。

    与 ``validate_manifest`` 的 default 校验同一套口径：
      - bool：只认真 bool（``1`` 不是 ``True``——int 是 bool 的父类，
        不挡的话磁盘里的 ``1`` 会被悄悄当成开）
      - int ：真整数（挡 bool）且落在 [min, max]
      - float：int / float 都收（json 里 ``1`` 与 ``1.0`` 等价）且落界
      - enum：字符串且 ∈ choices
    """
    if not isinstance(entry, dict):
        return False
    stype = entry.get("type")
    if stype == "bool":
        return isinstance(value, bool)
    if stype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return _within_bounds(entry, value)
    if stype == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return _within_bounds(entry, value)
    if stype == "enum":
        choices = entry.get("choices")
        return (isinstance(value, str)
                and isinstance(choices, (list, tuple))
                and value in choices)
    return False


def _within_bounds(entry: dict, value) -> bool:
    """数值是否落在条目声明的 [min, max] 内（缺边即该侧不设限）"""
    lo = entry.get("min")
    hi = entry.get("max")
    if isinstance(lo, (int, float)) and not isinstance(lo, bool) \
            and value < lo:
        return False
    if isinstance(hi, (int, float)) and not isinstance(hi, bool) \
            and value > hi:
        return False
    return True


def get_all(plugin_id, manifest_settings, data_dir_base=None) -> dict:
    """读一个插件的**全部生效值**：磁盘值与 schema 合并后的 dict。

    返回值**只含 schema 声明过的 key**（未声明 → 不出现，调用方用
    ``key not in values`` 区分「没有这项」与「值恰好等于 default」）。
    磁盘文件缺失 → 全 default；plugin_id 非法 / 目录不可用 → 全 default
    并记 warning（降级语义与 ``ctx.data_dir`` 返回空串一致：拿不到私有
    目录就别假装读到了用户数据）。

    ``data_dir_base`` 为 None 时按运行环境解析 ``<float_data>/plugins``
    （显式传参供测试 / 多数据目录场景注入）。
    """
    entries = schema_entries(manifest_settings)
    effective = {e["key"]: e.get("default") for e in entries}
    if not effective:
        return {}
    base = _resolve_base(data_dir_base)
    if not base or not is_safe_plugin_id(plugin_id):
        _LOG.warning("[插件设置] 插件私有目录不可用（base=%r id=%r），"
                     "%d 项设置全部回落默认值", base, plugin_id, len(effective))
        return effective
    raw = _read_raw(base, plugin_id)
    for entry in entries:
        key = entry["key"]
        if key not in raw:
            continue
        if value_matches_schema(entry, raw[key]):
            effective[key] = raw[key]
        else:
            _LOG.warning("[插件设置] %s.%s 磁盘值 %r 与 schema（type=%s）"
                         "不符，静默回落默认值 %r", plugin_id, key, raw[key],
                         entry.get("type"), entry.get("default"))
    return effective


def set_one(plugin_id, manifest_settings, key, value,
            data_dir_base=None) -> tuple:
    """写入一个设置项，返回 ``(是否成功, 原因)``。

    写入前三道闸（任一不过即拒绝，**绝不半写**）：
      1. plugin_id 过 ``is_safe_plugin_id``（防目录穿越）
      2. key 在 schema 里声明过（未声明的一律拒——磁盘不是插件的自由
         数据库，那是 data_dir 里其他文件的职责）
      3. 值过 ``value_matches_schema``（类型 + 边界；控件层已收窄，
         这里防的是手改文件之外的编程调用）

    落盘 = 读原文件 → 只改这一个 key → 原子写回：schema 未声明的遗留键
    **原样保留**（防升级丢数据），写坏临时文件自动清理。
    """
    base = _resolve_base(data_dir_base)
    if not base:
        return False, "插件数据目录不可用（宿主未注入插件目录）"
    if not is_safe_plugin_id(plugin_id):
        return False, f"插件 id 非法，拒绝写入设置：{plugin_id!r}"
    entry = next((e for e in schema_entries(manifest_settings)
                  if e["key"] == key), None)
    if entry is None:
        return False, (f"设置项 {key!r} 未在该插件 manifest.settings 里"
                       f"声明，拒绝写入")
    if not value_matches_schema(entry, value):
        return False, (f"设置项 {key} 的值 {value!r} 不符合声明约束"
                       f"（type={entry.get('type')!r}），拒绝写入")

    path = settings_file(base, plugin_id)
    raw = _read_raw(base, plugin_id)
    raw[key] = value
    ok, msg = _write_atomic(path, raw)
    if ok:
        _LOG.info("[插件设置] %s.%s = %r（%s）", plugin_id, key, value, path)
    return ok, msg


# ---------------- 内部 ----------------
def _resolve_base(data_dir_base=None) -> str:
    """设置根目录：显式传参优先；缺省按运行环境解析（惰性 import，
    避免模块加载期触发目录创建等副作用）"""
    if data_dir_base is not None:
        return str(data_dir_base or "")
    from src.app_paths import get_data_dir
    return os.path.join(get_data_dir(), "plugins")


def _read_raw(data_dir_base: str, plugin_id: str) -> dict:
    """读磁盘原始 dict；文件缺失 / JSON 坏 / 顶层不是对象 → 空表。

    坏文件**不删不改**：留给下一次 ``set_one`` 整体覆盖（那时写出的
    是合法 JSON），期间读到的空表只意味着「全部回落 default」。
    """
    path = settings_file(data_dir_base, plugin_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        _LOG.warning("[插件设置] settings.json 读取失败，按空表处理：%s"
                     "（%s）", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: str, data: dict) -> tuple:
    """原子落盘：tmp + os.replace（进程中途被杀也不会留下半个文件）"""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True, ""
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f"插件设置写入失败：{exc}"
