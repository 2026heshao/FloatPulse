# -*- coding: utf-8 -*-
"""
====================================================================
外置插件加载器  -  plugin_loader
====================================================================
扫描 ``<base_dir>/plugins/``，把外置插件包导入进程并登记进 ActionRegistry。

目录约定（与 docs/插件开发说明.md 一致）::

    plugins/color-picker/
      manifest.json    必需
      plugin.py        入口模块，必需
      icon.png         可选（右键菜单图标）

分发格式 ``<id>.fpplug``（本质是 zip）：
  - 运行时只加载**文件夹**，严禁 zipimport 直接从压缩包导入
    （PyInstaller 打包后 zipimport 行为不确定，且插件内资源读取会踩坑）
  - 扫描到 ``plugins/*.fpplug`` → 先解压到 ``plugins/<manifest.id>/`` 再加载，
    解压后**保留原 .fpplug 文件**（便于用户重新安装/备份）

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
import shutil
import sys
import zipfile

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

# 失败阶段的修复建议（面板直接展示；缺省给通用建议）
FAIL_HINTS = {
    STAGE_MANIFEST_READ:
        "确认插件目录里有 manifest.json，且是合法 UTF-8 编码的 JSON"
        "（注意别把文件存成了 GBK，或用记事本另存时带了 BOM）",
    STAGE_MANIFEST_INVALID:
        "对照 docs/插件开发说明.md 第 3 节检查 manifest.json："
        "id / name / version / entry 四个字段必需，且都必须是字符串；"
        "capabilities（可选）必须是字符串列表，能力名只能是 network",
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
    }.get(stage, "加载失败")


def is_safe_id(plugin_id) -> bool:
    """插件 id 是否可安全用作目录名（实现已上移到 plugin_api，此处保留兼容名）"""
    return is_safe_plugin_id(plugin_id)


def validate_manifest(data):
    """校验 manifest 结构。

    返回 ``(manifest, error)``：
      - 合法 → ``(归一化后的 dict, "")``
      - 不合法 → ``(None, 原因字符串)``
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

    return {
        "id": data["id"],
        "name": data["name"].strip(),
        "version": data["version"].strip(),
        "entry": entry,
        "requires": list(requires),
        "actions": actions,
        "description": description.strip(),
        "capabilities": list(dict.fromkeys(caps)),   # 去重保序
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


class PluginLoader:
    """插件加载器。

    参数：
      registry:     ActionRegistry，动作登记目标
      ctx:          PluginContext，传给插件的能力集合
      plugins_dir:  插件目录；None = ``<base_dir>/plugins``（延迟解析）
      logger:       日志器；None 时退化为静默
    """

    def __init__(self, registry, ctx, plugins_dir=None, logger=None):
        self._registry = registry
        self._ctx = ctx
        self._dir = plugins_dir
        self._logger = logger
        self._loaded = []        # list[LoadedPlugin]：导入成功后缓存，activate 复用
        self._failed = []        # list[FailedPlugin]：扫描到但加载失败的插件
        self._scanned = False

    # ---------------- 目录 ----------------
    @property
    def plugins_dir(self) -> str:
        """插件目录绝对路径（默认 <base_dir>/plugins，源码运行即项目根/plugins）"""
        if not self._dir:
            from src.app_paths import get_base_dir
            self._dir = os.path.join(get_base_dir(), "plugins")
        return self._dir

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

    # ---------------- 对外入口 ----------------
    def load_all(self) -> list:
        """扫描 + 导入 + 登记，返回本轮可用的插件列表。

        幂等：重复调用不会重复导入，也不会重复登记
        （已 `deactivate()` 的会被重新登记）。
        """
        if not self.ensure_dir():
            return []
        if not self._scanned:
            self._unpack_packages()
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

    # ---------------- 打包解压 ----------------
    def _unpack_packages(self):
        """扫描 *.fpplug → 解压到 plugins/<manifest.id>/，原文件保留"""
        try:
            names = sorted(os.listdir(self.plugins_dir))
        except OSError as exc:
            self._warn(f"插件目录读取失败：{exc}")
            return
        for name in names:
            if not name.lower().endswith(PLUGIN_PACKAGE_EXT):
                continue
            self._unpack_one(os.path.join(self.plugins_dir, name))

    def _unpack_one(self, package_path: str):
        name = os.path.basename(package_path)
        try:
            with zipfile.ZipFile(package_path) as zf:
                member, prefix = self._locate_manifest_member(zf.namelist())
                if member is None:
                    self._warn(f"{name} 内未找到 manifest.json，跳过解压")
                    return
                try:
                    data = json.loads(zf.read(member).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._warn(f"{name} 内 manifest.json 无法解析，跳过解压：{exc}")
                    return
                manifest, err = validate_manifest(data)
                if manifest is None:
                    self._warn(f"{name} 内 manifest 不合法，跳过解压：{err}")
                    return
                plugin_id = manifest["id"]
                target = os.path.join(self.plugins_dir, plugin_id)
                if os.path.isfile(os.path.join(target, MANIFEST_NAME)):
                    self._info(f"{name} 对应目录已存在（plugins/{plugin_id}/），"
                               f"保留现有内容并跳过解压")
                    return
                os.makedirs(target, exist_ok=True)
                self._safe_extract(zf, target, prefix)
            self._info(f"已解压插件包 {name} → plugins/{plugin_id}/（原文件保留）")
        except zipfile.BadZipFile:
            self._warn(f"{name} 不是合法的 zip 包，已跳过")
        except OSError as exc:
            self._warn(f"解压插件包失败：{name}（{exc}）")

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
        plugin_ctx = self._ctx_for(plugin_id, dirpath,
                                   manifest.get("capabilities", ()))

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

    def _warn_to(self, lp: LoadedPlugin, msg: str):
        """同时写日志 + 记进插件的告警清单（插件级告警的统一出口）"""
        full = f"{lp.plugin_id}: {msg}"
        lp.warnings.append(msg)
        self._warn(full)

    # ---------------- 工具 ----------------
    def _ctx_for(self, plugin_id: str, plugin_dir: str, capabilities=()):
        """派生绑定插件身份的上下文；宿主 ctx 不支持派生时退回共享实例"""
        factory = getattr(self._ctx, "for_plugin", None)
        if not callable(factory):
            return self._ctx
        try:
            # capabilities 只影响派生实例的权限判定（network 桥），
            # 旧版 for_plugin 不收该参数时退回不传（能力自然全无）
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
