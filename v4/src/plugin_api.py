# -*- coding: utf-8 -*-
"""
====================================================================
插件契约与动作注册表  -  plugin_api
====================================================================
给悬浮球定义「动作注册表 + 外置插件」的公共契约。本模块只依赖
Python 标准库（不 import PyQt6），因此可以在无 GUI 环境直接跑 pytest。

三个层次：
  1. PluginContext —— 插件能拿到的宿主能力，只暴露白名单方法
     （logger / 只读 config 快照 / show_toast / open_main_window /
       open_card_mode）。插件拿不到 ball、main_window 等宿主对象本体。
  2. BallAction   —— 一个可触发的动作（右键菜单项 / 热键 / 编程调用）
  3. BallPlugin   —— 一个插件：create_actions(ctx) 产出若干 BallAction

ActionRegistry 负责登记、查询、触发与「入口冲突」判定：
  - 同一 action id 重复登记 → 拒绝（返回 False），保留先登记的那个
  - 热键按大小写/空格归一后判定冲突；核心功能热键（reserve_hotkeys）
    优先级最高，插件不得抢占
  - trigger() 内部吞掉插件抛出的任何异常并记日志，绝不让球崩

依赖白名单（硬性）：插件只允许依赖 PyQt6 + Python 标准库。
is_allowed_requirement() 同时拒绝 ssl / 各种 http 客户端 ——
FloatPulse.spec 的 excludes 含 "ssl"，打包后插件做不了 https 请求。
====================================================================
"""

import sys
from abc import ABC, abstractmethod
from types import MappingProxyType

# 依赖白名单：允许的顶层模块名
_PYQT_PREFIX = "PyQt6"

# 显式拒绝的顶层模块名 —— 即使它们在标准库里：
#   ssl 被 PyInstaller spec 的 excludes 剔除，打包后 import 会失败；
#   其余是网络客户端，插件一律不允许联网（本工程离线定位）。
_DENIED_REQUIRES = frozenset({
    "ssl", "_ssl",
    "socket", "http", "urllib", "ftplib", "smtplib", "telnetlib",
    "requests", "urllib3", "httpx", "aiohttp", "websockets", "websocket",
})


def is_allowed_requirement(name: str) -> bool:
    """判断一条 requires 声明是否在白名单内。

    - 顶层模块名以 ``.`` 切分，只校验顶层（如 ``urllib.request`` → ``urllib``）
    - ``PyQt6`` 及其子模块允许
    - Python 标准库模块名允许（sys.stdlib_module_names）
    - ``_DENIED_REQUIRES`` 里的名字即使命中标准库也拒绝
    - 空串/非字符串/带路径分隔符的名字一律拒绝
    """
    if not isinstance(name, str):
        return False
    top = name.strip().split(".")[0].strip()
    if not top:
        return False
    # 名字里不允许出现路径/文件分隔符，避免被当成路径用
    if any(ch in top for ch in ("/", "\\", ":")) or top in (".", ".."):
        return False
    if top in _DENIED_REQUIRES:
        return False
    if top == _PYQT_PREFIX:
        return True
    return top in getattr(sys, "stdlib_module_names", frozenset())


def normalize_hotkey(text) -> str:
    """热键归一化：去空白、转小写（用于冲突判定，不用于实际注册）。

    非字符串/空串 → 空串（视为"未声明热键"）。
    """
    if not isinstance(text, str):
        return ""
    return text.strip().lower().replace(" ", "")


class PluginContext:
    """插件可见的宿主能力（白名单）。

    所有可选能力都以「调用时必须存在」为前提注入；缺失时对应方法安全降级
    （记录一条 warning 并返回 False），绝不抛异常到插件里。

    ``config`` 暴露的是**只读快照**（MappingProxyType）——插件不能改宿主配置，
    这是硬边界：配置的唯一写入方是设置页。
    """

    def __init__(self, logger, config=None, *,
                 show_toast=None, open_main_window=None, open_card_mode=None):
        self._logger = logger
        self._config = MappingProxyType(dict(config or {}))
        self._show_toast = show_toast
        self._open_main_window = open_main_window
        self._open_card_mode = open_card_mode

    # ---------------- 白名单能力 ----------------
    @property
    def logger(self):
        """宿主日志器（插件日志走同一份 float_data/app.log）"""
        return self._logger

    @property
    def config(self):
        """只读配置快照（映射，不可修改）"""
        return self._config

    def show_toast(self, text: str, ms: int = 2800) -> bool:
        """弹主窗口轻提示；宿主未提供该能力时返回 False"""
        if self._show_toast is None:
            self._warn("show_toast 不可用")
            return False
        try:
            self._show_toast(str(text), int(ms))
            return True
        except Exception as exc:          # noqa: BLE001 - 能力调用失败不反噬插件
            self._warn(f"show_toast 调用失败：{exc!r}")
            return False

    def open_main_window(self) -> bool:
        """打开主窗口"""
        if self._open_main_window is None:
            self._warn("open_main_window 不可用")
            return False
        try:
            self._open_main_window()
            return True
        except Exception as exc:          # noqa: BLE001
            self._warn(f"open_main_window 调用失败：{exc!r}")
            return False

    def open_card_mode(self, mode: str) -> bool:
        """弹出小卡片并切到指定模式

        mode 取值：fragment / task / note / nav / asset / app
        """
        if self._open_card_mode is None:
            self._warn("open_card_mode 不可用")
            return False
        try:
            ok = self._open_card_mode(str(mode))
            return bool(ok)
        except Exception as exc:          # noqa: BLE001
            self._warn(f"open_card_mode 调用失败：{exc!r}")
            return False

    # ---------------- 内部 ----------------
    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}")


class BallAction(ABC):
    """一个可触发的动作（对应右键菜单项 / 热键 / 编程调用）。

    子类必须提供类属性 ``id``、``title`` 并实现 ``run(ctx)``；
    ``hotkey`` / ``menu`` 通常由插件在 manifest 里声明，loader 会覆盖到实例上。

    刻意不定义 ``__init__``：子类无需调用 super().__init__()，
    避免"忘了调 super 就静默丢状态"这类坑。
    """

    id: str = ""
    title: str = ""
    hotkey = None            # 如 "Ctrl+Alt+C"；None = 未声明
    menu: bool = False       # True = 挂到悬浮球右键菜单
    icon_path = None         # 可选：菜单图标 png 绝对路径（loader 填充）
    _enabled: bool = True    # 运行时开关（禁用可以，真卸载不要）

    @abstractmethod
    def run(self, ctx: PluginContext):
        """执行动作。允许抛异常——注册表会兜住并记日志。"""

    def enabled(self) -> bool:
        """动作当前是否启用（禁用的动作不出现在菜单、不绑热键）"""
        return bool(self._enabled)

    def set_enabled(self, on: bool):
        """运行时启用/禁用本动作（不卸载插件模块）"""
        self._enabled = bool(on)

    def declared_hotkey(self) -> str:
        """归一化后的热键（空串 = 未声明）"""
        return normalize_hotkey(self.hotkey)


class BallPlugin(ABC):
    """一个插件。loader 会实例化它（要求无参可构造）并调用 create_actions。"""

    id: str = ""
    name: str = ""
    version: str = ""

    @abstractmethod
    def create_actions(self, ctx: PluginContext) -> list:
        """产出本插件的动作列表（元素须为 BallAction 实例）"""


class ActionRegistry:
    """动作注册表：登记 / 查询 / 触发 / 冲突判定。

    登记顺序即「优先级」：先登记的赢（后登记的冲突项由 loader 跳过）。
    这也是"两个插件声明同一热键，后者被跳过"的实现依据。
    """

    def __init__(self, logger=None, reserved_hotkeys=()):
        self._logger = logger
        self._actions = {}       # action_id -> BallAction
        self._owners = {}        # action_id -> plugin_id
        self._order = []         # action_id 登记顺序
        self._reserved = set()   # 核心功能占用的热键（归一化）
        self.reserve_hotkeys(reserved_hotkeys)

    # ---------------- 核心热键保留 ----------------
    def reserve_hotkeys(self, hotkeys):
        """登记「核心功能已占用」的热键，插件不得抢占（可重复调用、增量合并）"""
        for hk in hotkeys or ():
            norm = normalize_hotkey(hk)
            if norm:
                self._reserved.add(norm)

    def reserved_hotkeys(self) -> frozenset:
        return frozenset(self._reserved)

    # ---------------- 登记 / 注销 ----------------
    def register(self, action, plugin_id: str = "") -> bool:
        """登记一个动作。id 缺失或重复 → 拒绝并返回 False。"""
        action_id = getattr(action, "id", None)
        if not action_id or not isinstance(action_id, str):
            self._warn("动作缺少合法的 id，已拒绝登记")
            return False
        if action_id in self._actions:
            owner = self._owners.get(action_id) or "内置功能"
            self._warn(f"动作 id 重复，已拒绝登记：{action_id}（已被 {owner} 占用）"
                       f"—— action id 全局唯一，建议写成 '<插件id>.<动作名>'")
            return False
        self._actions[action_id] = action
        self._owners[action_id] = plugin_id
        self._order.append(action_id)
        return True

    def unregister(self, plugin_id: str) -> int:
        """注销某个插件的全部动作，返回注销数量（模块本身不卸载）"""
        dead = [aid for aid, owner in self._owners.items() if owner == plugin_id]
        for aid in dead:
            self._actions.pop(aid, None)
            self._owners.pop(aid, None)
            if aid in self._order:
                self._order.remove(aid)
        return len(dead)

    def clear(self) -> int:
        """清空注册表（不卸载模块），返回清掉的动作数"""
        n = len(self._actions)
        self._actions.clear()
        self._owners.clear()
        self._order.clear()
        return n

    # ---------------- 查询 ----------------
    def get(self, action_id: str):
        return self._actions.get(action_id)

    def owner_of(self, action_id: str) -> str:
        return self._owners.get(action_id, "")

    def all_actions(self) -> list:
        """按登记顺序返回全部动作（含被禁用的）"""
        return [self._actions[aid] for aid in self._order if aid in self._actions]

    def menu_actions(self) -> list:
        """按登记顺序返回要挂到右键菜单的动作（menu=True 且当前启用）"""
        return [a for a in self.all_actions()
                if getattr(a, "menu", False) and a.enabled()]

    def hotkey_actions(self) -> list:
        """按登记顺序返回声明了热键且当前启用的动作"""
        return [a for a in self.all_actions()
                if a.declared_hotkey() and a.enabled()]

    def set_enabled(self, action_id: str, on: bool) -> bool:
        """运行时启用/禁用某个动作（禁用=不挂菜单不绑热键，不卸载插件）"""
        act = self._actions.get(action_id)
        if act is None:
            return False
        act.set_enabled(bool(on))
        return True

    # ---------------- 入口冲突 ----------------
    def hotkey_claims(self) -> dict:
        """热键占用表：归一化热键 -> [action_id...]（含只被一个动作占用的）。

        loader 靠它判断「这个热键是不是已经被先登记的动作占了」——
        注意不能用 entry_conflicts() 代替：那里只留下 >=2 个占用者的键，
        问不出「已被占」这个单例状态。
        """
        claims = {}
        for act in self.hotkey_actions():
            claims.setdefault(act.declared_hotkey(), []).append(act.id)
        return claims

    def entry_conflicts(self) -> dict:
        """返回「同一入口被多个启用动作抢占」的映射：归一化热键 -> [action_id...]。

        只统计启用中的动作；核心保留热键不算在返回值里（那是硬拒绝，
        见 hotkey_blocked_by_reserved）。
        """
        return {hk: ids for hk, ids in self.hotkey_claims().items()
                if len(ids) > 1}

    def hotkey_blocked_by_reserved(self, hotkey) -> bool:
        """该热键是否被核心功能保留（插件不得抢占）"""
        norm = normalize_hotkey(hotkey)
        return bool(norm) and norm in self._reserved

    # ---------------- 触发 ----------------
    def trigger(self, action_id: str, ctx=None) -> bool:
        """执行动作。

        - 动作不存在 / 已禁用 → 返回 False
        - run() 抛出的任何异常都被吞掉并记日志 → 返回 False，
          保证单个插件崩不了悬浮球，也不影响其他插件
        """
        act = self._actions.get(action_id)
        if act is None:
            self._warn(f"触发失败：动作不存在 {action_id}")
            return False
        if not act.enabled():
            self._warn(f"触发失败：动作已禁用 {action_id}")
            return False
        try:
            act.run(ctx)
            return True
        except Exception as exc:          # noqa: BLE001 - 插件异常必须隔离
            self._warn(f"动作执行异常，已隔离：{action_id} -> {exc!r}")
            if self._logger is not None:
                self._logger.warning("[插件] 动作异常详情", exc_info=True)
            return False

    # ---------------- 内部 ----------------
    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}")
