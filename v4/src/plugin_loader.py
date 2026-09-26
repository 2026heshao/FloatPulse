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

from src.plugin_api import BallAction, BallPlugin, is_allowed_requirement

# 插件包固定文件名
MANIFEST_NAME = "manifest.json"
PLUGIN_PACKAGE_EXT = ".fpplug"

# manifest 必需字段
REQUIRED_FIELDS = ("id", "name", "version", "entry")

# 插件 id 合法形态：字母数字开头，允许 . _ -，1~64 位（同时用作目录名，必须安全）
_RE_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 导入到 sys.modules 时的模块名前缀（避免与主程序模块撞名）
_MODULE_PREFIX = "floatpulse_plugin_"


def is_safe_id(plugin_id) -> bool:
    """插件 id 是否可安全用作目录名（拒绝路径分隔符 / ``..`` / 空）"""
    if not isinstance(plugin_id, str) or not plugin_id:
        return False
    if plugin_id in (".", "..") or ".." in plugin_id:
        return False
    return bool(_RE_SAFE_ID.match(plugin_id))


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

    return {
        "id": data["id"],
        "name": data["name"].strip(),
        "version": data["version"].strip(),
        "entry": entry,
        "requires": list(requires),
        "actions": actions,
    }, ""


class LoadedPlugin:
    """一个已成功导入（可能尚未登记）的插件"""

    def __init__(self, plugin_id, name, version, path, plugin, manifest):
        self.plugin_id = plugin_id
        self.name = name
        self.version = version
        self.path = path
        self.plugin = plugin
        self.manifest = manifest
        self.actions_raw = []      # create_actions 的原始产出
        self.registered = False    # 当前是否已登记进注册表

    def __repr__(self):
        return (f"<LoadedPlugin {self.plugin_id} v{self.version} "
                f"actions={len(self.actions_raw)} registered={self.registered}>")


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
        self._scanned = False

    # ---------------- 目录 ----------------
    @property
    def plugins_dir(self) -> str:
        """插件目录绝对路径（默认 <base_dir>/plugins，源码运行即项目根/plugins）"""
        if not self._dir:
            from src.app_paths import get_base_dir
            self._dir = os.path.join(get_base_dir(), "plugins")
        return self._dir

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
            for dirpath in self._candidate_dirs():
                lp = self._load_one(dirpath)
                if lp is not None:
                    self._loaded.append(lp)
            self._scanned = True
            self._info(f"插件扫描完成：{self.plugins_dir} "
                       f"→ 可用插件 {len(self._loaded)} 个")
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
            self._warn(f"manifest 读取失败，跳过插件：{folder}（{exc}）")
            return None

        manifest, err = validate_manifest(data)
        if manifest is None:
            self._warn(f"manifest 不合法，跳过插件：{folder}（{err}）")
            return None

        plugin_id = manifest["id"]
        if plugin_id != folder:
            self._info(f"插件目录名与 manifest.id 不一致（{folder} vs {plugin_id}），"
                       f"以 manifest 为准")

        # 依赖白名单（硬性）
        bad = [r for r in manifest["requires"] if not is_allowed_requirement(r)]
        if bad:
            self._warn(f"依赖不在白名单内，拒绝加载插件：{plugin_id} -> {bad}"
                       f"（只允许 PyQt6 + Python 标准库；ssl 与网络库不可用）")
            return None

        # 入口模块
        entry_path = os.path.normpath(os.path.join(dirpath, manifest["entry"]))
        if not self._within(entry_path, dirpath):
            self._warn(f"入口模块越出插件目录，跳过插件：{plugin_id}")
            return None
        if not os.path.isfile(entry_path):
            self._warn(f"入口模块不存在，跳过插件：{plugin_id} -> {manifest['entry']}")
            return None

        module = self._import_entry(plugin_id, entry_path)
        if module is None:
            return None

        cls = self._find_plugin_class(module)
        if cls is None:
            self._warn(f"入口模块中没有 BallPlugin 子类，跳过插件：{plugin_id}")
            return None

        try:
            plugin = cls()
        except Exception as exc:          # noqa: BLE001 - 第三方代码异常必须隔离
            self._warn(f"插件实例化失败，跳过：{plugin_id}（{exc!r}）")
            return None

        # manifest 是元信息唯一真相源
        plugin.id = plugin_id
        plugin.name = manifest["name"]
        plugin.version = manifest["version"]

        try:
            actions = plugin.create_actions(self._ctx)
        except Exception as exc:          # noqa: BLE001
            self._warn(f"create_actions 抛出异常，跳过插件：{plugin_id}（{exc!r}）")
            self._log_traceback("create_actions 异常详情")
            return None

        if actions is None:
            actions = []
        if not isinstance(actions, (list, tuple)):
            self._warn(f"create_actions 返回值不是列表，跳过插件：{plugin_id}"
                       f"（{type(actions).__name__}）")
            return None

        lp = LoadedPlugin(plugin_id, manifest["name"], manifest["version"],
                          dirpath, plugin, manifest)
        lp.actions_raw = list(actions)
        self._info(f"已导入插件：{plugin_id} v{manifest['version']}"
                   f"（动作 {len(lp.actions_raw)} 个）")
        return lp

    def _import_entry(self, plugin_id: str, entry_path: str):
        """用 importlib 从文件路径导入入口模块（模块名带前缀避免撞名）"""
        module_name = _MODULE_PREFIX + plugin_id.replace("-", "_").replace(".", "_")
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry_path)
            if spec is None or spec.loader is None:
                self._warn(f"无法为入口模块建立导入规格，跳过插件：{plugin_id}")
                return None
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
            self._warn(f"入口模块导入失败，跳过插件：{plugin_id}（{exc!r}）")
            return None

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
        """按 manifest 声明顺序把动作登记进注册表（含热键冲突消解）"""
        impls = {}
        for act in lp.actions_raw:
            if not isinstance(act, BallAction):
                self._warn(f"{lp.plugin_id}: create_actions 返回了非 BallAction 对象"
                           f"（{type(act).__name__}），已忽略")
                continue
            if not act.id:
                self._warn(f"{lp.plugin_id}: 有动作缺少 id，已忽略")
                continue
            impls.setdefault(act.id, act)

        icon = os.path.join(lp.path, "icon.png")
        icon_path = icon if os.path.isfile(icon) else None

        ok = 0
        for spec in lp.manifest["actions"]:
            aid = spec["id"]
            act = impls.pop(aid, None)
            if act is None:
                self._warn(f"{lp.plugin_id}: manifest 声明了动作 {aid}，"
                           f"但 create_actions 未提供实现，已跳过")
                continue
            # manifest 覆盖入口声明：hotkey / menu 一律以 manifest 为准；
            # title 以插件代码为准（可能动态生成），manifest 只作缺省值
            act.hotkey = spec["hotkey"]
            act.menu = spec["menu"]
            if not act.title:
                act.title = spec["title"]
            act.icon_path = icon_path

            hotkey = act.declared_hotkey()
            if hotkey:
                if self._registry.hotkey_blocked_by_reserved(hotkey):
                    self._warn(f"{lp.plugin_id}/{aid}: 热键 {act.hotkey} "
                               f"已被核心功能占用，已跳过该动作")
                    continue
                taken = self._registry.hotkey_claims().get(hotkey)
                if taken:
                    self._warn(f"{lp.plugin_id}/{aid}: 热键 {act.hotkey} "
                               f"与 {taken} 冲突，后登记者让位（已跳过）")
                    continue
            if self._registry.register(act, lp.plugin_id):
                ok += 1

        for leftover in impls:
            self._warn(f"{lp.plugin_id}: 动作 {leftover} 未在 manifest.actions 里声明，"
                       f"已跳过（manifest 是声明唯一真相源）")

        lp.registered = True
        self._info(f"插件登记完成：{lp.plugin_id} → 生效动作 {ok} 个")

    # ---------------- 工具 ----------------
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

    def _info(self, msg: str):
        if self._logger is not None:
            self._logger.info(f"[插件] {msg}")

    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}")
