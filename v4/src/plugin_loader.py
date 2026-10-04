# -*- coding: utf-8 -*-
"""
====================================================================
外置插件加载器  -  plugin_loader
====================================================================
扫描 ``<base_dir>/plugins/``，把外置插件包导入进程并登记进 ActionRegistry。

**双目录模型**（2026-09-27 晚起，商店 / 安装分离）::

    <base_dir>/plugin_store/         插件商店：放 *.fpplug 源包，**永不解压**
      timer-tool.fpplug
      ai-assistant.fpplug

    <base_dir>/plugins/              插件安装目录：放解压后的插件文件夹
      color-picker/                  ← 唯一被 load_all() 加载的位置
        manifest.json               必需
        plugin.py                   入口模块，必需
        icon.png                    可选（右键菜单图标）

为什么分两处：
  - 商店是「可安装清单」，安装是**用户显式动作**（点「安装」→ 解压到 plugins/）
  - 卸载只删 ``plugins/<id>/``，商店里的 ``.fpplug`` **一根毛都不动**
    → 卡片回落为「未安装」，随时可再装。这正是「卸载后文件直接没了」
    这个问题的解法：源包不在被删的那一侧。
  - 开发者也因此能留着自己的包反复装/卸，不用改一行代码。

兼容：历史上 ``plugins/*.fpplug`` 会被 ``load_all()`` 静默自动解压。
该行为**已移除**——否则商店包被自动装上，卸载后立刻复活
（这正是需要避免的）。老用户把包挪到 ``plugin_store/`` 即可。

分发格式 ``<id>.fpplug``（本质是 zip）：
  - 运行时只加载**文件夹**，严禁 zipimport 直接从压缩包导入
    （PyInstaller 打包后 zipimport 行为不确定，且插件内资源读取会踩坑）
  - ``plugins/`` 下残留的 ``.fpplug`` 不再处理（仅记一条提示日志），
    解压一律走 ``install_from_store()`` / ``install_package()`` 显式入口

容错铁律（一条插件坏掉绝不能拖垮球或别的插件）：
  - 目录不存在 → 创建后返回空列表，不报错
  - manifest 缺失字段 / JSON 损坏 / requires 不在白名单 / 入口模块不存在
    / 导入异常 / 没有 BallPlugin 子类 / 实例化失败 / create_actions 抛异常
    → 只记 ``logger.warning`` 并跳过该插件
  - 动作级问题（run 抛异常、热键冲突）由 ActionRegistry 兜住
  - 热键**格式**非法 → 只丢掉该热键、保留动作（菜单里仍可用）；
    热键**被占用/冲突** → 整个动作让位。两者是不同处置，见开发说明第 7 节

每个插件在 ``create_actions()`` 前会派生一个绑定自身身份的 PluginContext
（``ctx.plugin_id`` / ``ctx.data_dir`` / ``ctx.plugin_dir``），
并随动作一起交给注册表 —— 触发时 ``run(ctx)`` 拿到的就是这个实例。

「禁用可以，真卸载不要」：
  ``deactivate()`` 只把动作从注册表摘掉（模块对象仍驻留 ``sys.modules``），
  ``activate()`` 直接用缓存的插件对象重新登记，**不重新导入**。
====================================================================
"""

import importlib.util
import json
import os
import re
import shutil
import sys
import zipfile

from src.app_version import (
    APP_VERSION, PLUGIN_API_VERSION, is_version_ge, parse_version,
)
from src.plugin_api import (
    KNOWN_CAPABILITIES,
    BallAction, BallPlugin, is_allowed_requirement, is_safe_plugin_id,
    is_valid_hotkey,
)

# 插件包固定文件名
MANIFEST_NAME = "manifest.json"
PLUGIN_PACKAGE_EXT = ".fpplug"

# manifest 必需字段
REQUIRED_FIELDS = ("id", "name", "version", "entry")

# ---------------- 插件设置 schema（manifest.settings 校验，2026-10-04） ----------------
# 插件在 manifest 里声明可选 ``settings``（设置项条目列表），宿主插件中心
# 按 schema 通用渲染表单，存储走插件私有目录（src/plugin_settings.py），
# **不进主 config**。非法 → 整个 manifest 拒载（与 capabilities「未知能力名
# 拒载」同一思路：拼写错误静默失效比直接拒载危害大得多）。
SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SETTING_TYPES = ("bool", "int", "float", "enum")   # 纯字符串 key 不开放（防脏数据面）
MAX_PLUGIN_SETTINGS = 12        # 单插件设置条数上限
MAX_SETTING_LABEL_CHARS = 30    # 设置项 label 字符数上限
MAX_ENUM_CHOICES = 10           # enum 选项数上限
# int / float 未声明 min/max 时的兜底边界（要负数 / 更大范围必须显式声明）
SETTING_DEFAULT_MIN = 0
SETTING_DEFAULT_MAX = 100000

# 「版本不匹配」类错误的统一前缀。``validate_manifest`` 返回的 error 若以此开头，
# ``_load_one`` 就不再归为 STAGE_MANIFEST_INVALID，而是 STAGE_VERSION_MISMATCH ——
# 两者对用户是完全不同的处置：前者是「你的 manifest 写错了」，
# 后者是「插件太新，请升级程序」。混在一起会给出误导性的修复建议。
VERSION_ERROR_PREFIX = "\x00version\x00"

# 导入到 sys.modules 时的模块名前缀（避免与主程序模块撞名）
_MODULE_PREFIX = "floatpulse_plugin_"

# ---------------- 失败阶段（FailedPlugin.stage） ----------------
# 供插件中心面板分类展示。stage 用稳定的英文标识，面板负责映射中文文案。
STAGE_MANIFEST_READ = "manifest_read"      # manifest.json 读不了 / JSON 坏
STAGE_MANIFEST_INVALID = "manifest_invalid"  # manifest 字段不合法
STAGE_REQUIRES_REJECTED = "requires"       # 依赖不在白名单
STAGE_ENTRY_MISSING = "entry_missing"      # 入口模块不存在 / 越出目录
STAGE_IMPORT_FAILED = "import_failed"      # 入口模块导入抛异常
STAGE_NO_PLUGIN_CLASS = "no_plugin_class"  # 模块里没有 BallPlugin 子类
STAGE_INSTANTIATE_FAILED = "instantiate"   # 插件类实例化失败
STAGE_CREATE_ACTIONS_FAILED = "create_actions"  # create_actions 抛异常 / 返回值非法
STAGE_REGISTER_FAILED = "register"         # 登记期整体失败（动作全被拒等）
STAGE_VERSION_MISMATCH = "version_mismatch"  # 插件要求的 API / 程序版本高于宿主

# 失败阶段的修复建议（面板直接展示；缺省给通用建议）
FAIL_HINTS = {
    STAGE_MANIFEST_READ:
        "确认插件目录里有 manifest.json，且是合法 UTF-8 编码的 JSON"
        "（注意别把文件存成了 GBK，或用记事本另存时带了 BOM）",
    STAGE_MANIFEST_INVALID:
        "对照 docs/插件开发说明.md 第 3 节检查 manifest.json："
        "id / name / version / entry 四个字段必需，且都必须是字符串；"
        "capabilities（可选）必须是字符串列表，能力名只能是 network；"
        "settings（可选）条目必须有 key / label / type / default，"
        "type 只允许 bool / int / float / enum",
    STAGE_REQUIRES_REJECTED:
        "插件只能依赖 PyQt6 和 Python 标准库。ssl / socket / requests 等"
        "联网库在打包后不可用，必须从 requires 里删掉并改用离线实现",
    STAGE_ENTRY_MISSING:
        "确认 manifest.json 里 entry 指向的文件真实存在，"
        "且路径没有用 .. 跳出插件目录",
    STAGE_IMPORT_FAILED:
        "入口模块 import 时抛了异常。常见原因：语法错误、"
        "import 了未在白名单里的库、模块顶层代码依赖了不存在的东西",
    STAGE_NO_PLUGIN_CLASS:
        "入口模块里必须定义一个继承 BallPlugin 的类，"
        "并实现 create_actions(self, ctx) 方法",
    STAGE_INSTANTIATE_FAILED:
        "插件类必须能无参数构造（__init__ 不能要求传参，"
        "也别在 __init__ 里做重活，初始化逻辑放 create_actions）",
    STAGE_CREATE_ACTIONS_FAILED:
        "create_actions 抛了异常或返回值不是列表。"
        "它必须返回 BallAction 实例的列表（可以返回空列表）",
    STAGE_REGISTER_FAILED:
        "动作登记全部被拒。检查 action id 是否与其他插件重复"
        "（建议写成 '<插件id>.<动作名>'），以及动作定义是否完整",
    STAGE_VERSION_MISMATCH:
        "插件要求的宿主 / API 版本高于当前程序，请升级 FloatPulse 或联系插件作者。"
        "若你是插件作者：api_version 只在用到较新契约时才需要提高，"
        "min_app_version 应填插件实际兼容的最低程序版本",
}


def stage_label(stage: str) -> str:
    """失败阶段的中文短标签（面板卡片标题用）"""
    return {
        STAGE_MANIFEST_READ: "manifest 读取失败",
        STAGE_MANIFEST_INVALID: "manifest 不合法",
        STAGE_REQUIRES_REJECTED: "依赖被拒",
        STAGE_ENTRY_MISSING: "入口模块缺失",
        STAGE_IMPORT_FAILED: "导入失败",
        STAGE_NO_PLUGIN_CLASS: "缺少插件类",
        STAGE_INSTANTIATE_FAILED: "实例化失败",
        STAGE_CREATE_ACTIONS_FAILED: "动作创建失败",
        STAGE_REGISTER_FAILED: "动作登记失败",
        STAGE_VERSION_MISMATCH: "版本不匹配",
    }.get(stage, "加载失败")


def is_safe_id(plugin_id) -> bool:
    """插件 id 是否可安全用作目录名（实现已上移到 plugin_api，此处保留兼容名）"""
    return is_safe_plugin_id(plugin_id)


def _validate_settings_entry(index, item, seen_keys) -> tuple:
    """校验一条 manifest.settings 条目，返回 ``(归一化 dict, "")`` 或 ``(None, 错误)``。

    错误信息必须指明**第几条哪个字段**（``settings[2].default …``）——
    插件作者对着一串 JSON 排查时，笼统的「settings 非法」帮不上忙。
    归一化只保留该 type 实际使用的字段（bool 不带 min/max、非 enum 不带
    choices），存储层与设置弹层拿到的就是干净形状。
    """
    tag = f"settings[{index}]"
    if not isinstance(item, dict):
        return None, f"{tag} 不是 JSON 对象"

    key = item.get("key")
    if not isinstance(key, str) or not SETTING_KEY_RE.match(key):
        return None, (f"{tag}.key 非法（须匹配 ^[a-z][a-z0-9_]{{0,63}}$，"
                      f"小写字母开头，≤64 位）：{key!r}")
    if key in seen_keys:
        return None, f"{tag}.key 与其他条目重复：{key!r}"
    seen_keys.add(key)

    label = item.get("label")
    if not isinstance(label, str) or not label.strip():
        return None, f"{tag}.label 必须是非空字符串：{label!r}"
    label = label.strip()
    if len(label) > MAX_SETTING_LABEL_CHARS:
        return None, (f"{tag}.label 超长（最多 {MAX_SETTING_LABEL_CHARS} 字符，"
                      f"实际 {len(label)}）：{label!r}")

    stype = item.get("type")
    if stype not in SETTING_TYPES:
        return None, (f"{tag}.type 非法（只允许 "
                      f"{'/'.join(SETTING_TYPES)}）：{stype!r}")

    entry = {"key": key, "label": label, "type": stype}

    if stype == "bool":
        default = item.get("default", False)
        if not isinstance(default, bool):
            return None, f"{tag}.default 必须是 bool（type=bool）：{default!r}"
        entry["default"] = default
        return entry, ""

    if stype in ("int", "float"):
        lo = item.get("min", SETTING_DEFAULT_MIN)
        hi = item.get("max", SETTING_DEFAULT_MAX)
        for name, bound in (("min", lo), ("max", hi)):
            if isinstance(bound, bool) or not isinstance(bound, (int, float)):
                return None, f"{tag}.{name} 必须是数字：{bound!r}"
            if stype == "int" and not isinstance(bound, int):
                return None, f"{tag}.{name} 必须是整数（type=int）：{bound!r}"
        if lo > hi:
            return None, f"{tag}.min 不能大于 max：{lo!r} > {hi!r}"
        default = item.get("default", lo)
        if isinstance(default, bool):
            return None, (f"{tag}.default 不能是 bool（type={stype}，"
                          f"请写 {int(default)}）：{default!r}")
        if not isinstance(default, (int, float)):
            return None, (f"{tag}.default 必须是数字（type={stype}）："
                          f"{default!r}")
        if stype == "int" and not isinstance(default, int):
            return None, f"{tag}.default 必须是整数（type=int）：{default!r}"
        if default < lo or default > hi:
            return None, f"{tag}.default {default!r} 越界（须落在 [{lo}, {hi}]）"
        entry["default"] = default
        entry["min"] = lo
        entry["max"] = hi
        return entry, ""

    # enum：choices 必须是非空字符串列表（去重、≤10 项），default ∈ choices
    choices = item.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, f"{tag}.choices 必须是非空字符串列表（type=enum）"
    if len(choices) > MAX_ENUM_CHOICES:
        return None, (f"{tag}.choices 超限（最多 {MAX_ENUM_CHOICES} 项，"
                      f"实际 {len(choices)} 项）")
    cleaned = []
    for choice in choices:
        if not isinstance(choice, str) or not choice.strip():
            return None, f"{tag}.choices 含非字符串或空白项：{choice!r}"
        cleaned.append(choice.strip())
    if len(set(cleaned)) != len(cleaned):
        return None, f"{tag}.choices 含重复项（去空格后比较）：{choices!r}"
    default = item.get("default")
    if not isinstance(default, str) or default.strip() not in cleaned:
        return None, (f"{tag}.default 必须是 choices 之一（type=enum，"
                      f"必填）：{default!r}")
    entry["default"] = default.strip()
    entry["choices"] = cleaned
    return entry, ""


def validate_manifest(data):
    """校验 manifest 结构。

    返回 ``(manifest, error)``：
      - 合法 → ``(归一化后的 dict, "")``
      - 不合法 → ``(None, 原因字符串)``

    错误字符串若以 ``VERSION_ERROR_PREFIX`` 开头，表示这不是「字段写错了」，
    而是「版本不匹配」——``_load_one`` 据此归到 ``STAGE_VERSION_MISMATCH``，
    让面板显示针对性的修复建议（升级程序），而不是笼统的「manifest 不合法」。
    """
    if not isinstance(data, dict):
        return None, "manifest 顶层不是 JSON 对象"
    for field in REQUIRED_FIELDS:
        val = data.get(field)
        if not isinstance(val, str) or not val.strip():
            return None, f"缺少必需字段或字段非法：{field}"
    if not is_safe_id(data["id"]):
        return None, f"id 非法（只能字母数字开头，含 . _ -）：{data['id']!r}"

    entry = data["entry"].strip()
    if os.path.isabs(entry) or ".." in entry.replace("\\", "/").split("/"):
        return None, f"entry 不允许使用绝对路径或上级目录：{entry!r}"

    requires = data.get("requires", [])
    if requires is None:
        requires = []
    if not isinstance(requires, list) or \
            not all(isinstance(r, str) for r in requires):
        return None, "requires 必须是字符串列表"

    raw_actions = data.get("actions", [])
    if raw_actions is None:
        raw_actions = []
    if not isinstance(raw_actions, list):
        return None, "actions 必须是列表"
    actions = []
    for i, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            return None, f"actions[{i}] 不是 JSON 对象"
        aid = item.get("id")
        title = item.get("title")
        if not isinstance(aid, str) or not aid.strip():
            return None, f"actions[{i}].id 非法"
        if not isinstance(title, str) or not title.strip():
            return None, f"actions[{i}].title 非法"
        hotkey = item.get("hotkey")
        if hotkey is not None and not isinstance(hotkey, str):
            return None, f"actions[{i}].hotkey 必须是字符串"
        actions.append({
            "id": aid.strip(),
            "title": title.strip(),
            "hotkey": (hotkey.strip() if isinstance(hotkey, str) and hotkey.strip()
                       else None),
            "menu": bool(item.get("menu", False)),
        })

    # 可选 description：插件中心页面的一句话说明（2026-09-27 起支持；
    # 缺省空串，面板回退到插件类 docstring / README 展示）
    description = data.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        return None, "description 必须是字符串"

    # 可选 capabilities：能力声明（2026-09-27 起支持，目前仅 "network"）。
    # 未知能力名 → 直接拒载（防 "netwrork" 这类拼写错误静默失效）。
    caps = data.get("capabilities", [])
    if caps is None:
        caps = []
    if not isinstance(caps, list) or \
            not all(isinstance(c, str) for c in caps):
        return None, "capabilities 必须是字符串列表"
    unknown = [c for c in caps if c not in KNOWN_CAPABILITIES]
    if unknown:
        return None, (f"capabilities 含未知能力名：{unknown}"
                      f"（合法取值：{sorted(KNOWN_CAPABILITIES)}）")

    # 可选 page：声明本插件要注入主窗口导航页（2026-09-27 起支持）。
    # 目前只有一个字段 title（侧栏按钮文案）；插件须实现 create_page()。
    page = data.get("page")
    if page is not None:
        if not isinstance(page, dict):
            return None, "page 必须是 JSON 对象（如 {\"title\": \"🤖 AI 助手\"}）"
        ptitle = page.get("title")
        if not isinstance(ptitle, str) or not ptitle.strip():
            return None, "page.title 必须是非空字符串"
        page = {"title": ptitle.strip()}

    # 可选 api_version：插件要求的插件契约版本（2026-09-27 起支持）。
    # 缺省 1；非 int / <=0 直接拒载（写错了就是写错了，别静默当 1）；
    # 高于宿主 PLUGIN_API_VERSION → 拒载（插件用到了本程序还没有的契约）。
    api_version = data.get("api_version", 1)
    if api_version is None:
        api_version = 1
    if isinstance(api_version, bool) or not isinstance(api_version, int) \
            or api_version <= 0:
        return None, (f"api_version 必须是正整数（当前契约版本 "
                      f"{PLUGIN_API_VERSION}）：{api_version!r}")
    if api_version > PLUGIN_API_VERSION:
        return None, (VERSION_ERROR_PREFIX +
                      f"插件要求 plugin api_version={api_version}，"
                      f"但当前程序只支持到 {PLUGIN_API_VERSION}"
                      f"（请升级 FloatPulse 或联系插件作者）")

    # 可选 min_app_version：插件要求的最低程序版本。
    # 格式非法 → 拒载（这是作者显式写下的约束，格式错了就没法保证语义）；
    # 格式合法但宿主版本偏低 → 拒载。
    min_app_version = data.get("min_app_version", "")
    if min_app_version is None:
        min_app_version = ""
    if not isinstance(min_app_version, str):
        return None, "min_app_version 必须是字符串（如 \"4.6.0\"）"
    min_app_version = min_app_version.strip()
    if min_app_version:
        if parse_version(min_app_version) is None:
            return None, (f"min_app_version 必须是点分数字（如 \"4.6.0\"）："
                          f"{min_app_version!r}")
        if not is_version_ge(APP_VERSION, min_app_version):
            return None, (VERSION_ERROR_PREFIX +
                          f"插件要求 FloatPulse >= {min_app_version}，"
                          f"当前版本 {APP_VERSION}（请升级程序）")

    # 可选 settings：插件独立设置项 schema（2026-10-04 起支持）。
    # 声明了非空 settings 的插件会在插件中心卡片上出现「设置」按钮，宿主
    # 按这份 schema 通用渲染表单；存储走插件私有目录，不进主 config。
    # 不声明 / 声明为 [] / 声明为 None → 都视为无设置项（三种都合法）。
    raw_settings = data.get("settings", [])
    if raw_settings is None:
        raw_settings = []
    if not isinstance(raw_settings, list):
        return None, "settings 必须是条目列表"
    if len(raw_settings) > MAX_PLUGIN_SETTINGS:
        return None, (f"settings 条数超限（最多 {MAX_PLUGIN_SETTINGS} 条，"
                      f"实际 {len(raw_settings)} 条）")
    settings = []
    seen_setting_keys = set()
    for i, item in enumerate(raw_settings):
        # ★ 循环变量不可叫 entry：上面 ``entry`` 是入口模块路径字符串，
        #   在这里被覆盖的话返回值里 entry 会变成 settings 条目 dict
        #   （插件加载直接 TypeError——离屏验证抓住过一次）。
        norm, err = _validate_settings_entry(i, item, seen_setting_keys)
        if err:
            return None, err
        settings.append(norm)

    return {
        "id": data["id"],
        "name": data["name"].strip(),
        "version": data["version"].strip(),
        "entry": entry,
        "requires": list(requires),
        "actions": actions,
        "description": description.strip(),
        "capabilities": list(dict.fromkeys(caps)),   # 去重保序
        "page": page,
        "api_version": api_version,
        "min_app_version": min_app_version,
        "settings": settings,
    }, ""


class LoadedPlugin:
    """一个已成功导入（可能尚未登记）的插件"""

    def __init__(self, plugin_id, name, version, path, plugin, manifest, ctx=None):
        self.plugin_id = plugin_id
        self.name = name
        self.version = version
        self.path = path
        self.plugin = plugin
        self.manifest = manifest
        self.ctx = ctx            # 绑定本插件身份的 PluginContext（loader 派生）
        self.actions_raw = []      # create_actions 的原始产出
        self.registered = False    # 当前是否已登记进注册表
        # 登记期的动作级告警（热键被占 / 动作未声明 / 格式非法等）。
        # 这些不影响插件整体可用，但用户需要看到「某个动作没生效」的原因。
        self.warnings = []         # list[str]，人类可读
        # 生效动作数（登记完成后填充；启用/停用开关据此显示）
        self.actions_ok = 0

    def __repr__(self):
        return (f"<LoadedPlugin {self.plugin_id} v{self.version} "
                f"actions={len(self.actions_raw)} registered={self.registered}>")


class FailedPlugin:
    """一个扫描到但未能加载/登记的插件（失败可感知的载体）。

    此前所有失败路径都是 ``_warn(...) + return None``，失败项完全不进
    ``_loaded``，面板因此只能显示「共 0 个插件」——用户无从判断是目录没找到、
    manifest 写错、依赖被拒还是代码抛异常。本结构把失败原因结构化留存，
    供插件中心面板展示与给出修复建议。

    字段：
      folder:   插件目录名（拿不到 manifest 时唯一能定位的标识）
      path:     插件目录绝对路径（「打开目录」按钮用）
      stage:    失败阶段标识（见 FAIL_STAGES，用于面板分类与图标）
      reason:   人类可读的失败原因（等价于日志里的那句话）
      hint:     修复建议（面板直接展示；空串表示无需额外提示）
      plugin_id: 若 manifest 已解析成功则填入，否则为 ""
      name:     若 manifest 已解析成功则填入，否则回退 folder
    """

    def __init__(self, folder, path, stage, reason, hint="",
                 plugin_id="", name=""):
        self.folder = folder
        self.path = path
        self.stage = stage
        self.reason = reason
        self.hint = hint
        self.plugin_id = plugin_id or ""
        self.name = name or folder

    def __repr__(self):
        return (f"<FailedPlugin {self.folder} stage={self.stage} "
                f"reason={self.reason[:40]!r}>")


class StoreEntry:
    """插件商店里的一个可安装包（``plugin_store/*.fpplug``）。

    **纯只读描述**：``scan_store()`` 只读 zip 内的 ``manifest.json``，
    一个字节都不解压到磁盘。安装是用户的显式动作（``install_from_store``）。

    字段：
      plugin_id:   manifest.id（合法时）；非法/读不到时为 ""
      name:        展示名；manifest.name 缺失时回退文件名
      version:     manifest.version（可能为空串）
      description: manifest.description（可空）
      path:        该 .fpplug 的绝对路径（打开目录 / 安装都用它）
      filename:    包文件名（列表展示 + 报错定位）
      installed:   对应的 ``plugins/<id>/`` 当前是否存在
      error:       包不合法时的原因（合法时为空串）
      manifest:    已解析的完整 manifest（合法时）；否则 None
    """

    def __init__(self, plugin_id="", name="", version="", description="",
                 path="", filename="", installed=False, error="",
                 manifest=None):
        self.plugin_id = plugin_id or ""
        self.name = name or filename or plugin_id
        self.version = version or ""
        self.description = description or ""
        self.path = path
        self.filename = filename
        self.installed = bool(installed)
        self.error = error or ""
        self.manifest = manifest

    @property
    def usable(self) -> bool:
        """包是否可用（manifest 合法、id 也拿到了）"""
        return bool(self.plugin_id) and not self.error

    def __repr__(self):
        return (f"<StoreEntry {self.plugin_id or '?'} v{self.version} "
                f"installed={self.installed} err={self.error[:30]!r}>")


class PluginLoader:
    """插件加载器。

    参数：
      registry:     ActionRegistry，动作登记目标
      ctx:          PluginContext，传给插件的能力集合
      plugins_dir:  插件安装目录；None = ``<base_dir>/plugins``（延迟解析）
      logger:       日志器；None 时退化为静默
      store_dir:    插件商店目录；None = ``<base_dir>/plugin_store``（延迟解析）
                    只存 ``*.fpplug`` 源包，**永不被 load_all() 自动解压**
    """

    def __init__(self, registry, ctx, plugins_dir=None, logger=None,
                 store_dir=None):
        self._registry = registry
        self._ctx = ctx
        self._dir = plugins_dir
        self._store = store_dir
        self._logger = logger
        self._loaded = []        # list[LoadedPlugin]：导入成功后缓存，activate 复用
        self._failed = []        # list[FailedPlugin]：扫描到但加载失败的插件
        self._scanned = False

    # ---------------- 目录 ----------------
    @property
    def plugins_dir(self) -> str:
        """插件安装目录绝对路径（默认 <base_dir>/plugins，源码运行即项目根/plugins）"""
        if not self._dir:
            from src.app_paths import get_base_dir
            self._dir = os.path.join(get_base_dir(), "plugins")
        return self._dir

    @property
    def store_dir(self) -> str:
        """插件商店目录绝对路径（默认 <base_dir>/plugin_store）。

        与 ``plugins_dir`` 平级、**互为独立**：商店只放 ``*.fpplug`` 源包，
        安装目录只放解压后的文件夹。两者分开是「卸载后商店包仍在」的前提
        ——被删的只有安装目录那一侧。
        """
        if not self._store:
            from src.app_paths import get_base_dir
            self._store = os.path.join(get_base_dir(), "plugin_store")
        return self._store

    @property
    def registry(self):
        """本加载器登记目标注册表（只读暴露）。

        插件中心面板需要它来调用 ``set_enabled`` 实现单插件启停，
        从而不必让 MainWindow 再多持一个注册表引用（少一处跨层耦合）。
        """
        return self._registry

    def ensure_dir(self) -> bool:
        """确保插件目录存在（不存在就创建，失败只记日志不抛异常）"""
        try:
            os.makedirs(self.plugins_dir, exist_ok=True)
            return True
        except OSError as exc:
            self._warn(f"插件目录不可用，跳过插件加载：{self.plugins_dir}（{exc}）")
            return False

    def ensure_store_dir(self) -> bool:
        """确保插件商店目录存在（不存在就创建，失败只记日志不抛异常）"""
        try:
            os.makedirs(self.store_dir, exist_ok=True)
            return True
        except OSError as exc:
            self._warn(f"插件商店目录不可用：{self.store_dir}（{exc}）")
            return False

    # ---------------- 对外入口 ----------------
    def load_all(self) -> list:
        """扫描 + 导入 + 登记，返回本轮可用的插件列表。

        幂等：重复调用不会重复导入，也不会重复登记
        （已 `deactivate()` 的会被重新登记）。

        **不再自动解压** ``.fpplug``（2026-09-27 晚改）：商店/安装分离后，
        只有 ``install_from_store()`` 才写 ``plugins/``。否则商店里的包
        每次启动都被重新装上，卸载等于没卸。
        """
        if not self.ensure_dir():
            return []
        if not self._scanned:
            self._warn_stray_packages()
            self._loaded = []
            self._failed = []
            for dirpath in self._candidate_dirs():
                lp = self._load_one(dirpath)
                if lp is not None:
                    self._loaded.append(lp)
            self._scanned = True
            summary = (f"插件扫描完成：{self.plugins_dir} "
                       f"→ 可用插件 {len(self._loaded)} 个")
            if self._failed:
                summary += f"，加载失败 {len(self._failed)} 个（见插件中心面板）"
            self._info(summary)
        for lp in self._loaded:
            if not lp.registered:
                self._register_plugin(lp)
        return list(self._loaded)

    def deactivate(self) -> int:
        """把全部插件动作从注册表摘掉（模块仍驻留，不真卸载），返回摘掉的动作数"""
        n = 0
        for lp in self._loaded:
            if lp.registered:
                # 先给插件 flush 的机会，再摘动作（顺序：钩子 → unregister）
                self._safe_hook(lp, "on_disable")
                n += self._registry.unregister(lp.plugin_id)
                lp.registered = False
        if n:
            self._info(f"插件已禁用：摘除 {n} 个动作（模块仍驻留内存）")
        return n

    def activate(self) -> list:
        """重新登记已导入的插件（不重新导入模块），返回插件列表"""
        for lp in self._loaded:
            if not lp.registered:
                self._register_plugin(lp)
        return list(self._loaded)

    def loaded_plugins(self) -> list:
        return list(self._loaded)

    def set_plugin_enabled(self, plugin_id: str, on: bool) -> int:
        """按插件 id 统一开关该插件的全部动作，返回改动的动作数。

        与插件中心卡片上的「停用 / 启用」是同一件事（``registry.set_enabled``
        逐个动作调用），只是入口不同：面板点按钮，这里供宿主按配置回置状态。

        未知 id / 未登记的插件 → 返回 0（**不报错**：配置里可能残留已被
        删除的插件 id，那不该让启动流程出问题）。
        """
        n = 0
        for lp in self._loaded:
            if lp.plugin_id != plugin_id:
                continue
            for act in lp.actions_raw:
                aid = getattr(act, "id", "")
                if not aid or not hasattr(act, "set_enabled"):
                    continue
                try:
                    if self._registry.set_enabled(aid, bool(on)):
                        n += 1
                except Exception:          # noqa: BLE001 - 单个动作失败不阻塞
                    continue
        return n

    def disabled_plugin_ids(self) -> list:
        """当前处于「全部动作已停用」状态的插件 id 列表（供面板/宿主回写配置）。"""
        out = []
        for lp in self._loaded:
            acts = [a for a in lp.actions_raw if getattr(a, "id", "")]
            if acts and all(hasattr(a, "enabled") and not a.enabled() for a in acts):
                out.append(lp.plugin_id)
        return out

    def load_errors(self) -> list:
        """扫描到但加载失败的插件（list[FailedPlugin]）。

        与 ``loaded_plugins()`` 互补：两者加起来才是「插件目录里到底有什么」。
        面板据此展示失败原因与修复建议，解决「用户不知道插件为什么没生效」。
        仅反映最近一次扫描的结果；``load_all()`` 每次重新扫描会重置。
        """
        return list(self._failed)

    def has_errors(self) -> bool:
        return bool(self._failed)

    def rescan(self) -> list:
        """强制重新扫描插件目录（丢弃扫描缓存后重新 load_all）。

        用于「重新扫描」按钮：用户新放入插件后无需重启程序即可加载。
        注意：已成功导入的插件模块仍留在 ``sys.modules``，重复扫描不会二次导入
        （Python 模块缓存保证）；但插件**目录被删掉**的情况下旧插件对象
        仍在内存中——这里会一并清掉，使其从注册表摘除。
        """
        self.deactivate()
        self._loaded = []
        self._failed = []
        self._scanned = False
        return self.load_all()

    # ---------------- 卸载 ----------------
    def uninstall(self, plugin_id: str) -> tuple:
        """删除插件目录并摘除其全部痕迹，返回 ``(ok, 消息)``。

        做四件事，顺序有讲究：
          1. 校验 —— id 合法 + 目标目录**确实在 plugins_dir 内**（防目录穿越；
             越界一律拒绝，不动任何文件）
          2. ``on_uninstall(ctx)`` 钩子（若插件实现了；异常只记日志不阻断）
          3. ``shutil.rmtree`` 删目录 → ``unregister`` 摘动作 → 从 ``_loaded`` 移除
          4. 清掉 ``sys.modules`` 里的模块缓存，使后续 ``rescan()`` 真正重新导入

        **刻意不删** ``float_data/plugins/<id>/``：那是插件的私有数据，
        用户可能还想留着重装后用。确认框里会明确说明这一点。

        失败一律返回 ``(False, 原因)``，不抛异常——调用方（插件中心）负责展示。
        """
        if not is_safe_plugin_id(plugin_id):
            return False, f"插件 id 非法，拒绝卸载：{plugin_id!r}"

        target = os.path.join(self.plugins_dir, plugin_id)
        if not self._within(target, self.plugins_dir):
            return False, f"插件目录越出插件安装目录，拒绝卸载：{target}"
        if not os.path.isdir(target):
            return False, f"插件目录不存在（可能已手动删除）：{target}"

        lp = next((p for p in self._loaded if p.plugin_id == plugin_id), None)

        # 1. 卸载前钩子：给插件 flush 缓存 / 关闭句柄的机会
        if lp is not None:
            self._safe_hook(lp, "on_uninstall")

        # 2. 删目录
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"删除插件目录失败（文件可能被占用）：{target}（{exc}）"

        # 3. 摘动作 + 移出内存列表
        n = self._registry.unregister(plugin_id)
        if lp is not None:
            lp.registered = False
            self._loaded = [p for p in self._loaded if p.plugin_id != plugin_id]

        # 4. 清模块缓存（否则 rescan 会命中旧 sys.modules 条目，插件"删不掉"）
        module_name = _MODULE_PREFIX + plugin_id.replace("-", "_").replace(".", "_")
        sys.modules.pop(module_name, None)

        self._info(f"插件已卸载：{plugin_id}（摘除动作 {n} 个，"
                   f"私有数据保留在 float_data/plugins/{plugin_id}/）")
        return True, f"已卸载 {plugin_id}（插件私有数据保留在数据目录）"

    # ---------------- 插件商店（惰性安装） ----------------
    def scan_store(self) -> list:
        """扫描商店目录，返回 ``list[StoreEntry]``（**只读 manifest，不解压**）。

        每个 ``*.fpplug`` 都尝试读 zip 内 ``manifest.json``：
          - 合法 → ``StoreEntry(plugin_id=..., installed=<plugins/<id>/ 是否存在>)``
          - 非法 → ``StoreEntry(error=原因)``，仍在列表里（面板可以提示用户）
        按 ``plugin_id``（非法包按文件名）排序，保证顺序确定。

        本方法不做任何写操作，随时可调，也不会因为一个坏包中断整轮扫描。
        """
        try:
            names = sorted(os.listdir(self.store_dir))
        except OSError as exc:
            self._warn(f"插件商店目录读取失败：{self.store_dir}（{exc}）")
            return []
        entries = []
        for name in names:
            if not name.lower().endswith(PLUGIN_PACKAGE_EXT):
                continue
            entries.append(self._read_package(
                os.path.join(self.store_dir, name)))
        entries.sort(key=lambda e: (not e.usable, e.plugin_id or e.filename))
        return entries

    def _read_package(self, package_path: str) -> StoreEntry:
        """只读解析一个 ``.fpplug`` 的 manifest，构造 StoreEntry（不写盘）"""
        name = os.path.basename(package_path)
        try:
            with zipfile.ZipFile(package_path) as zf:
                member, _prefix = self._locate_manifest_member(zf.namelist())
                if member is None:
                    return StoreEntry(path=package_path, filename=name,
                                      error="包内未找到 manifest.json")
                try:
                    raw = zf.read(member).decode("utf-8")
                except UnicodeDecodeError as exc:
                    return StoreEntry(path=package_path, filename=name,
                                      error=f"manifest.json 不是 UTF-8：{exc}")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return StoreEntry(path=package_path, filename=name,
                                      error=f"manifest.json 不是合法 JSON：{exc}")
        except zipfile.BadZipFile:
            return StoreEntry(path=package_path, filename=name,
                              error="不是合法的 zip 包（.fpplug 本质是 zip）")
        except OSError as exc:
            return StoreEntry(path=package_path, filename=name,
                              error=f"读取失败：{exc}")

        manifest, err = validate_manifest(data)
        if manifest is None:
            return StoreEntry(path=package_path, filename=name,
                              error=err[len(VERSION_ERROR_PREFIX):]
                              if err.startswith(VERSION_ERROR_PREFIX) else err)

        plugin_id = manifest["id"]
        target = os.path.join(self.plugins_dir, plugin_id)
        installed = os.path.isfile(os.path.join(target, MANIFEST_NAME))
        return StoreEntry(
            plugin_id=plugin_id,
            name=manifest["name"],
            version=manifest["version"],
            description=(manifest.get("description") or "").strip(),
            path=package_path,
            filename=name,
            installed=installed,
            manifest=manifest,
        )

    def install_from_store(self, plugin_id: str) -> tuple:
        """把商店里 id 为 ``plugin_id`` 的包装进 ``plugins/``，返回 ``(ok, 消息)``。

        步骤：
          1. ``scan_store()`` 找到这个 id 对应的 ``.fpplug``（找不到 → 失败）
          2. 目标目录 ``plugins/<id>/`` 已存在 → **拒绝覆盖**（保护用户已装内容）
          3. 解压 + 清扫描缓存，使下次 ``load_all()`` 重新扫到它

        解压走 ``_safe_extract``（zip-slip 逐成员防护），与原自动解压同一条路径。
        商店里的 ``.fpplug`` 全程只读，**永不移动、永不删除**。
        """
        if not is_safe_plugin_id(plugin_id):
            return False, f"插件 id 非法，拒绝安装：{plugin_id!r}"
        if not self.ensure_dir():
            return False, f"插件目录不可用：{self.plugins_dir}"

        entry = next((e for e in self.scan_store()
                      if e.usable and e.plugin_id == plugin_id), None)
        if entry is None:
            return False, (f"商店里没有 id 为 {plugin_id!r} 的可用插件包"
                           f"（目录：{self.store_dir}）")
        return self.install_package(entry.path, expect_id=plugin_id)

    def install_package(self, package_path: str,
                        expect_id: str = "") -> tuple:
        """解压指定 ``.fpplug`` 到 ``plugins/<manifest.id>/``，返回 ``(ok, 消息)``。

        ``expect_id`` 非空时校验包内 id 与之一致（防止文件被换掉后装错东西）。
        同样**不删源包**，且拒绝覆盖已有插件目录。
        """
        name = os.path.basename(package_path)
        if not self.ensure_dir():
            return False, f"插件目录不可用：{self.plugins_dir}"
        if not os.path.isfile(package_path):
            return False, f"插件包不存在：{package_path}"
        # 防目录穿越：源包必须位于商店目录内（install_package 也对外暴露，
        # 不能因为调用方传了任意路径就解压任意 zip 到插件目录）
        if not self._within(package_path, self.store_dir):
            return False, f"插件包不在商店目录内，拒绝安装：{package_path}"

        try:
            with zipfile.ZipFile(package_path) as zf:
                member, prefix = self._locate_manifest_member(zf.namelist())
                if member is None:
                    return False, f"{name} 内未找到 manifest.json，无法安装"
                try:
                    data = json.loads(zf.read(member).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    return False, f"{name} 内 manifest.json 无法解析：{exc}"
                manifest, err = validate_manifest(data)
                if manifest is None:
                    reason = (err[len(VERSION_ERROR_PREFIX):]
                              if err.startswith(VERSION_ERROR_PREFIX) else err)
                    return False, f"{name} 内 manifest 不合法：{reason}"

                plugin_id = manifest["id"]
                if expect_id and plugin_id != expect_id:
                    return False, (f"插件包 id 不匹配（期望 {expect_id}，"
                                   f"实际 {plugin_id}），拒绝安装")

                target = os.path.join(self.plugins_dir, plugin_id)
                if not self._within(target, self.plugins_dir):
                    return False, f"目标目录越出插件安装目录，拒绝安装：{target}"
                if os.path.isdir(target) and \
                        os.path.isfile(os.path.join(target, MANIFEST_NAME)):
                    return False, (f"插件 {plugin_id} 已安装"
                                   f"（plugins/{plugin_id}/ 已存在）；"
                                   f"如需重装请先卸载")

                os.makedirs(target, exist_ok=True)
                self._safe_extract(zf, target, prefix)
        except zipfile.BadZipFile:
            return False, f"{name} 不是合法的 zip 包，无法安装"
        except OSError as exc:
            return False, f"解压插件包失败：{name}（{exc}）"

        # 新插件目录要能被下一轮 load_all 扫到
        self._scanned = False
        self._info(f"已从商店安装插件：{plugin_id} → plugins/{plugin_id}/"
                   f"（源包保留在 plugin_store/）")
        return True, f"已安装 {plugin_id}（源包保留在插件商店目录）"

    def store_entries(self) -> list:
        """``scan_store()`` 的别名（语义更贴近「取商店清单」，供面板调用）"""
        return self.scan_store()

    def _warn_stray_packages(self):
        """提示 plugins/ 里残留的 .fpplug（新模型下这里不该有包）"""
        try:
            names = os.listdir(self.plugins_dir)
        except OSError:
            return
        stray = [n for n in names if n.lower().endswith(PLUGIN_PACKAGE_EXT)]
        if stray:
            self._warn(
                f"插件安装目录里发现 {len(stray)} 个插件包（{', '.join(sorted(stray))}）"
                f"——新版本不再自动解压，请把它们移到插件商店目录：{self.store_dir}")

    # ---------------- 打包解压 ----------------
    @staticmethod
    def _locate_manifest_member(names) -> tuple:
        """在 zip 成员里定位 manifest.json。

        返回 ``(成员名, 前缀)``；找不到返回 ``(None, "")``。
        兼容两种打包方式：manifest 在压缩包根目录，或整体套一层同名目录。
        """
        if MANIFEST_NAME in names:
            return MANIFEST_NAME, ""
        candidates = [n for n in names
                      if n.endswith("/" + MANIFEST_NAME) and n.count("/") == 1]
        if len(candidates) == 1:
            member = candidates[0]
            return member, member[:-len(MANIFEST_NAME)]
        return None, ""

    def _safe_extract(self, zf: zipfile.ZipFile, target: str, prefix: str = ""):
        """解压到 target，逐条剔除路径穿越成员（zip-slip 防护）"""
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace("\\", "/")
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts):
                self._warn(f"插件包含非法路径成员，已跳过：{info.filename}")
                continue
            dest = os.path.join(target, *parts)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            except OSError as exc:
                self._warn(f"写出插件文件失败，已跳过：{info.filename}（{exc}）")

    # ---------------- 扫描 ----------------
    def _candidate_dirs(self) -> list:
        """插件候选目录：直接子目录且含 manifest.json，按目录名排序保证顺序确定"""
        try:
            names = sorted(os.listdir(self.plugins_dir))
        except OSError as exc:
            self._warn(f"插件目录读取失败：{exc}")
            return []
        dirs = []
        for name in names:
            path = os.path.join(self.plugins_dir, name)
            if os.path.isdir(path) and \
                    os.path.isfile(os.path.join(path, MANIFEST_NAME)):
                dirs.append(path)
        return dirs

    # ---------------- 单个插件 ----------------
    def _load_one(self, dirpath: str):
        folder = os.path.basename(dirpath)
        manifest_path = os.path.join(dirpath, MANIFEST_NAME)
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._fail(folder, dirpath, STAGE_MANIFEST_READ,
                              f"manifest 读取失败，跳过插件：{folder}（{exc}）")

        manifest, err = validate_manifest(data)
        if manifest is None:
            # 版本类错误单独归类：面板据此给出「请升级程序」而非
            # 「你的 manifest 写错了」，两者的处置完全不同
            if err.startswith(VERSION_ERROR_PREFIX):
                return self._fail(folder, dirpath, STAGE_VERSION_MISMATCH,
                                  f"插件版本不兼容，跳过插件：{folder}"
                                  f"（{err[len(VERSION_ERROR_PREFIX):]}）")
            return self._fail(folder, dirpath, STAGE_MANIFEST_INVALID,
                              f"manifest 不合法，跳过插件：{folder}（{err}）")

        plugin_id = manifest["id"]
        pname = manifest["name"]
        if plugin_id != folder:
            self._info(f"插件目录名与 manifest.id 不一致（{folder} vs {plugin_id}），"
                       f"以 manifest 为准")

        # 依赖白名单（硬性）
        bad = [r for r in manifest["requires"] if not is_allowed_requirement(r)]
        if bad:
            return self._fail(
                folder, dirpath, STAGE_REQUIRES_REJECTED,
                f"依赖不在白名单内，拒绝加载插件：{plugin_id} -> {bad}"
                f"（只允许 PyQt6 + Python 标准库；ssl 与网络库不可用）",
                plugin_id=plugin_id, name=pname)

        # 入口模块
        entry_path = os.path.normpath(os.path.join(dirpath, manifest["entry"]))
        if not self._within(entry_path, dirpath):
            return self._fail(folder, dirpath, STAGE_ENTRY_MISSING,
                              f"入口模块越出插件目录，跳过插件：{plugin_id}",
                              plugin_id=plugin_id, name=pname)
        if not os.path.isfile(entry_path):
            return self._fail(
                folder, dirpath, STAGE_ENTRY_MISSING,
                f"入口模块不存在，跳过插件：{plugin_id} -> {manifest['entry']}",
                plugin_id=plugin_id, name=pname)

        module = self._import_entry(plugin_id, entry_path, folder, dirpath, pname)
        if module is None:
            return None

        cls = self._find_plugin_class(module)
        if cls is None:
            return self._fail(
                folder, dirpath, STAGE_NO_PLUGIN_CLASS,
                f"入口模块中没有 BallPlugin 子类，跳过插件：{plugin_id}",
                plugin_id=plugin_id, name=pname)

        try:
            plugin = cls()
        except Exception as exc:          # noqa: BLE001 - 第三方代码异常必须隔离
            return self._fail(
                folder, dirpath, STAGE_INSTANTIATE_FAILED,
                f"插件实例化失败，跳过：{plugin_id}（{exc!r}）",
                plugin_id=plugin_id, name=pname)

        # manifest 是元信息唯一真相源
        plugin.id = plugin_id
        plugin.name = manifest["name"]
        plugin.version = manifest["version"]

        # 派生绑定本插件身份的上下文：插件由此拿到 plugin_id / data_dir /
        # plugin_dir / parent_window，以及按 manifest 声明授权的能力（桥）
        # 与设置项 schema（2026-10-04 插件独立设置）
        plugin_ctx = self._ctx_for(plugin_id, dirpath,
                                   manifest.get("capabilities", ()),
                                   manifest.get("settings", ()))

        try:
            actions = plugin.create_actions(plugin_ctx)
        except Exception as exc:          # noqa: BLE001
            self._log_traceback("create_actions 异常详情")
            return self._fail(
                folder, dirpath, STAGE_CREATE_ACTIONS_FAILED,
                f"create_actions 抛出异常，跳过插件：{plugin_id}（{exc!r}）",
                plugin_id=plugin_id, name=pname)

        if actions is None:
            actions = []
        if not isinstance(actions, (list, tuple)):
            return self._fail(
                folder, dirpath, STAGE_CREATE_ACTIONS_FAILED,
                f"create_actions 返回值不是列表，跳过插件：{plugin_id}"
                f"（{type(actions).__name__}）",
                plugin_id=plugin_id, name=pname)

        lp = LoadedPlugin(plugin_id, manifest["name"], manifest["version"],
                          dirpath, plugin, manifest, ctx=plugin_ctx)
        lp.actions_raw = list(actions)
        self._info(f"已导入插件：{plugin_id} v{manifest['version']}"
                   f"（动作 {len(lp.actions_raw)} 个）")
        return lp

    def _import_entry(self, plugin_id: str, entry_path: str,
                      folder: str = "", dirpath: str = "", name: str = ""):
        """用 importlib 从文件路径导入入口模块（模块名带前缀避免撞名）"""
        folder = folder or plugin_id
        dirpath = dirpath or os.path.dirname(entry_path)
        module_name = _MODULE_PREFIX + plugin_id.replace("-", "_").replace(".", "_")
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry_path)
            if spec is None or spec.loader is None:
                return self._fail(folder, dirpath, STAGE_IMPORT_FAILED,
                                  f"无法为入口模块建立导入规格，跳过插件：{plugin_id}",
                                  plugin_id=plugin_id, name=name)
            module = importlib.util.module_from_spec(spec)
            # 先登记进 sys.modules：dataclass/反射等场景依赖它，且便于排查
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)   # 导入失败不留半成品
                raise
            return module
        except Exception as exc:          # noqa: BLE001
            return self._fail(folder, dirpath, STAGE_IMPORT_FAILED,
                              f"入口模块导入失败，跳过插件：{plugin_id}（{exc!r}）",
                              plugin_id=plugin_id, name=name)

    @staticmethod
    def _find_plugin_class(module):
        """在模块里找 BallPlugin 子类（限定本模块定义，取第一个）"""
        for name, obj in vars(module).items():
            if not isinstance(obj, type):
                continue
            if not issubclass(obj, BallPlugin) or obj is BallPlugin:
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            return obj
        return None

    # ---------------- 登记 ----------------
    def _register_plugin(self, lp: LoadedPlugin):
        """按 manifest 声明顺序把动作登记进注册表（含热键冲突消解）

        登记期的「动作级告警」（热键被占 / 格式非法 / 动作未声明 / 返回非
        BallAction）不影响插件整体可用，但用户需要知道「某个动作为什么没生效」，
        因此逐条收进 ``lp.warnings``，由插件中心面板展示。
        """
        impls = {}
        for act in lp.actions_raw:
            if not isinstance(act, BallAction):
                self._warn_to(lp, f"create_actions 返回了非 BallAction 对象"
                                  f"（{type(act).__name__}），已忽略")
                continue
            if not act.id:
                self._warn_to(lp, "有动作缺少 id，已忽略")
                continue
            impls.setdefault(act.id, act)

        icon = os.path.join(lp.path, "icon.png")
        icon_path = icon if os.path.isfile(icon) else None

        ok = 0
        for spec in lp.manifest["actions"]:
            aid = spec["id"]
            act = impls.pop(aid, None)
            if act is None:
                self._warn_to(lp, f"manifest 声明了动作 {aid}，"
                                  f"但 create_actions 未提供实现，已跳过")
                continue
            # manifest 覆盖入口声明：hotkey / menu 一律以 manifest 为准；
            # title 以插件代码为准（可能动态生成），manifest 只作缺省值
            act.hotkey = spec["hotkey"]
            act.menu = spec["menu"]
            if not act.title:
                act.title = spec["title"]
            act.icon_path = icon_path

            # 热键格式校验（加载期就报，别等注册期静默失败）：
            # 格式非法 → 只丢掉这个热键，保留动作（菜单里仍可用），
            # 与「热键被占用 → 整个动作让位」是两种不同处置，见开发说明第 7 节
            if act.hotkey and not is_valid_hotkey(act.hotkey):
                self._warn_to(lp, f"动作 {aid} 的热键格式非法，已改为仅菜单触发："
                                  f"{act.hotkey!r}（正确形态如 'Ctrl+Alt+W'，"
                                  f"必须含修饰键 + 主键）")
                act.hotkey = None

            hotkey = act.declared_hotkey()
            if hotkey:
                if self._registry.hotkey_blocked_by_reserved(hotkey):
                    self._warn_to(lp, f"动作 {aid} 的热键 {act.hotkey} "
                                      f"已被核心功能占用，该动作未生效")
                    continue
                taken = self._registry.hotkey_claims().get(hotkey)
                if taken:
                    self._warn_to(lp, f"动作 {aid} 的热键 {act.hotkey} "
                                      f"与 {taken} 冲突，后登记者让位（未生效）")
                    continue
            if self._registry.register(act, lp.plugin_id, lp.ctx):
                ok += 1
            else:
                # 注册表拒绝（几乎只有「action id 已被占用」一种）。
                # 注册表自己会记日志，但插件卡片要显示原因，所以这里补一条
                # 插件级告警——否则用户只看到「未生效」却不知为什么。
                owner = ""
                try:
                    owner = self._registry.owner_of(aid)
                except Exception:                  # noqa: BLE001
                    owner = ""
                who = f"（已被 {owner} 占用）" if owner else ""
                self._warn_to(lp, f"动作 {aid} 登记被拒{who}——"
                                  f"action id 全局唯一，建议写成 "
                                  f"'<插件id>.<动作名>'")

        for leftover in impls:
            self._warn_to(lp, f"动作 {leftover} 未在 manifest.actions 里声明，"
                              f"已跳过（manifest 是声明唯一真相源）")

        lp.registered = True
        lp.actions_ok = ok
        if ok == 0 and lp.actions_raw:
            # 有动作但一个都没生效 → 记一条插件级失败，面板会显著提示
            self._failed.append(FailedPlugin(
                folder=os.path.basename(lp.path), path=lp.path,
                stage=STAGE_REGISTER_FAILED,
                reason=f"插件 {lp.plugin_id} 的动作全部未能登记（0 个生效）",
                hint=FAIL_HINTS[STAGE_REGISTER_FAILED],
                plugin_id=lp.plugin_id, name=lp.name))
        elif ok == 0 and not lp.actions_raw:
            self._warn_to(lp, "插件未提供任何动作，安装后不会产生可点击入口")
        self._info(f"插件登记完成：{lp.plugin_id} → 生效动作 {ok} 个")

        # 登记成功后调 on_enable（可选钩子）。首次加载与「从停用恢复」都会走到
        # 这里，插件只需在这里做一次性准备（初始化缓存 / 打开句柄）。
        self._safe_hook(lp, "on_enable")

    def _warn_to(self, lp: LoadedPlugin, msg: str):
        """同时写日志 + 记进插件的告警清单（插件级告警的统一出口）"""
        full = f"{lp.plugin_id}: {msg}"
        lp.warnings.append(msg)
        self._warn(full)

    def _safe_hook(self, lp: LoadedPlugin, name: str) -> bool:
        """调用插件的可选生命周期钩子（``on_enable`` / ``on_disable`` / ``on_uninstall``）。

        - 插件没实现该方法 → 直接返回 True（老插件零影响，不进 abstractmethod）
        - 钩子抛异常 → 记 warning 并**继续流程**，绝不反噬宿主与其他插件
        - 返回 True 表示钩子被成功调用或不存在（两种都算"正常"）
        """
        hook = getattr(lp.plugin, name, None)
        if not callable(hook):
            return True
        try:
            hook(lp.ctx)
            return True
        except Exception as exc:          # noqa: BLE001 - 插件钩子异常必须隔离
            self._warn_to(lp, f"生命周期钩子 {name}() 抛异常，已忽略：{exc!r}")
            if self._logger is not None:
                self._logger.warning(f"[插件] {lp.plugin_id} {name} 异常详情",
                                     exc_info=True)
            return False

    # ---------------- 工具 ----------------
    def _ctx_for(self, plugin_id: str, plugin_dir: str, capabilities=(),
                 manifest_settings=()):
        """派生绑定插件身份的上下文；宿主 ctx 不支持派生时退回共享实例"""
        factory = getattr(self._ctx, "for_plugin", None)
        if not callable(factory):
            return self._ctx
        try:
            # capabilities 只影响派生实例的权限判定（network 桥），
            # 旧版 for_plugin 不收该参数时退回不传（能力自然全无）；
            # manifest_settings（2026-10-04 插件设置）同理逐级降级——
            # 旧宿主 ctx 不收第 4 参时退回 3 参，插件侧 get_setting
            # 拿不到 schema 自然全程走 fallback。
            try:
                return factory(plugin_id, plugin_dir, capabilities,
                               manifest_settings)
            except TypeError:
                pass
            try:
                return factory(plugin_id, plugin_dir, capabilities)
            except TypeError:
                return factory(plugin_id, plugin_dir)
        except Exception as exc:          # noqa: BLE001 - 派生失败不该拖垮加载
            self._warn(f"{plugin_id}: 上下文派生失败，退回共享上下文（{exc!r}）")
            return self._ctx

    @staticmethod
    def _within(path: str, parent: str) -> bool:
        """path 是否位于 parent 目录内（归一化前缀比较，防目录穿越）"""
        parent_norm = os.path.normcase(os.path.abspath(parent))
        path_norm = os.path.normcase(os.path.abspath(path))
        return path_norm == parent_norm or \
            path_norm.startswith(parent_norm + os.sep)

    def _log_traceback(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}", exc_info=True)

    def _fail(self, folder, path, stage, reason, plugin_id="", name=""):
        """记录一个加载失败并返回 None（供 ``_load_one`` 的失败分支统一出口）。

        同时做两件事：
          - 写日志（保持既有行为：日志仍是完整的排查通道）
          - 结构化留存到 ``self._failed``（新增：让面板能展示失败原因）
        """
        hint = FAIL_HINTS.get(stage, "")
        self._failed.append(FailedPlugin(
            folder=folder, path=path, stage=stage, reason=reason,
            hint=hint, plugin_id=plugin_id, name=name))
        self._warn(reason)
        return None

    def _info(self, msg: str):
        if self._logger is not None:
            self._logger.info(f"[插件] {msg}")

    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}")
