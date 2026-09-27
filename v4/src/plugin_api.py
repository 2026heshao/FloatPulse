# -*- coding: utf-8 -*-
"""
====================================================================
插件契约与动作注册表  -  plugin_api
====================================================================
给悬浮球定义「动作注册表 + 外置插件」的公共契约。本模块只依赖
Python 标准库（不 import PyQt6），因此可以在无 GUI 环境直接跑 pytest。

三个层次：
  1. PluginContext —— 插件能拿到的宿主能力，只暴露白名单方法
     （logger / 只读 config 快照 / 只读数据快照 PluginData / 插件私有目录 /
       父窗口 / show_toast / open_main_window / open_card_mode）。
     插件拿不到 ball、main_window、Task/Fragment/Note 等宿主对象本体：
     数据一律以 deepcopy 出来的纯 dict/list 副本交出去（见 PluginData）。
  2. BallAction   —— 一个可触发的动作（右键菜单项 / 热键 / 编程调用）
  3. BallPlugin   —— 一个插件：create_actions(ctx) 产出若干 BallAction

数据与目录能力（2026-09-27 增强，方向「日报/周报草稿」驱动）：
  - PluginData  : 宿主注入 providers，插件读 tasks / fragments / notes /
                  pomodoro 的**只读快照**。没有它，插件只能干「打开某物」
                  这类无意义动作。
  - data_dir    : 插件私有可写目录 ``<float_data>/plugins/<插件id>/``
  - plugin_dir  : 插件自己的文件夹（读自带资源用）
  - parent_window: 插件弹自定义对话框时的父窗口（保证居中/不被压住）

ActionRegistry 负责登记、查询、触发与「入口冲突」判定：
  - 同一 action id 重复登记 → 拒绝（返回 False），保留先登记的那个
  - 热键按大小写/空格归一后判定冲突；核心功能热键（reserve_hotkeys）
    优先级最高，插件不得抢占
  - trigger() 内部吞掉插件抛出的任何异常并记日志，绝不让球崩

依赖白名单（硬性）：插件只允许依赖 PyQt6 + Python 标准库。
is_allowed_requirement() 同时拒绝 ssl / 各种 http 客户端 ——
插件**永远不直接持有网络库**，联网走宿主代理桥。

能力模型（2026-09-27 起支持，对标 VSCode 扩展的声明式权限）：
  - manifest 里声明 ``"capabilities": ["network"]`` 才能联网；
  - 联网方式 = ``ctx.http_post_json_async(...)``（宿主在后台线程发请求，
    完成后回调回 UI 线程），插件自身不 import 任何网络库；
  - 未声明 network 能力的插件调用桥 → 安全拒绝（记 warning，回调错误结果），
    不抛异常。requires 白名单规则**原样保留**：网络库仍然不允许出现在
    requires 里，capability 授权的是「使用宿主桥」，不是「放开 import」。
====================================================================
"""

import copy
import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from types import MappingProxyType

# 依赖白名单：允许的顶层模块名
_PYQT_PREFIX = "PyQt6"

# 宿主未注入日志器时的兜底：插件里的 ``ctx.logger.info(...)`` 永远安全。
# 没有它，只要传入 logger=None，插件第一行日志就会 AttributeError，
# 而异常发生在 Qt 槽里会被解释器升级成 qFatal → 整个进程 0xC0000409。
_NULL_LOGGER = logging.getLogger("floatpulse.plugins.null")
_NULL_LOGGER.addHandler(logging.NullHandler())
_NULL_LOGGER.propagate = False

# 显式拒绝的顶层模块名 —— 即使它们在标准库里：
#   ssl 被 PyInstaller spec 的 excludes 剔除，打包后 import 会失败；
#   其余是网络客户端，插件一律不允许联网（本工程离线定位）。
_DENIED_REQUIRES = frozenset({
    "ssl", "_ssl",
    "socket", "http", "urllib", "ftplib", "smtplib", "telnetlib",
    "requests", "urllib3", "httpx", "aiohttp", "websockets", "websocket",
})

# 已知的能力名（manifest.capabilities 合法取值）。
# 未知能力名 → manifest 校验失败（防拼写错误静默失效，如 "netwrork"）。
KNOWN_CAPABILITIES = frozenset({"network"})


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


# 插件 id 合法形态：字母数字开头，允许 . _ -，1~64 位。
# 它同时被用作目录名，所以必须在契约层就挡掉路径穿越。
_RE_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_safe_plugin_id(plugin_id) -> bool:
    """插件 id 能否安全用作目录名（契约层唯一实现，loader 复用）。

    拒绝：非字符串 / 空 / 含 ``..`` / 不以字母数字开头 / 超 64 位。
    """
    if not isinstance(plugin_id, str) or not plugin_id:
        return False
    if ".." in plugin_id:
        return False
    return bool(_RE_SAFE_ID.match(plugin_id))


# 热键修饰键名（与 global_hotkey._MOD_MAP 对齐，但这里不依赖 Win32/Qt）
_HOTKEY_MODIFIERS = frozenset({
    "ctrl", "control", "alt", "shift", "win", "windows", "meta", "super",
})


def is_valid_hotkey(text) -> bool:
    """热键字符串格式校验（纯字符串，跨平台可用）。

    与 ``global_hotkey.parse_hotkey`` 的最终判定一致，但**提前到加载期**：
    注册期才发现非法只能静默失败，加载期发现才能给出明确日志。

    合法要求：
      - 至少两段（``+`` 分隔）
      - ``+`` 之前的段必须**全部**是修饰键
      - 至少一个修饰键（必须带修饰键，否则会抢占普通按键）
      - 最后一段是主键，且不能是修饰键
      - 主键形态：单个可打印字符 或 F1~F24
    """
    if not isinstance(text, str):
        return False
    tokens = [t.strip().lower() for t in text.split("+") if t.strip()]
    if len(tokens) < 2:
        return False
    if tokens[-1] in _HOTKEY_MODIFIERS:
        return False
    lead = tokens[:-1]
    if not lead or any(t not in _HOTKEY_MODIFIERS for t in lead):
        return False
    last = tokens[-1]
    if len(last) == 1:
        return last.isprintable()
    if last[0] == "f" and last[1:].isdigit():
        return 1 <= int(last[1:]) <= 24
    return False


class PluginData:
    """插件可见的**只读数据快照**（宿主在 main() 注入 providers）。

    providers 是「数据源名 → 无参 callable」，callable 返回 dict 或
    ``list[dict]``。插件拿到的永远是 ``deepcopy`` 出来的纯数据副本：

      - 插件改的是自己的副本，**碰不到宿主的 Task / Fragment / Note 对象**
      - 宿主内部结构调整时，只需同步 provider，插件的读法不用改

    所有方法都不抛异常：数据源缺失 / 调用失败 → 记 warning 并返回空值，
    插件据此自然降级（例如「本周没有已完成任务」），而不是崩掉。
    """

    SOURCE_TASKS = "tasks"
    SOURCE_FRAGMENTS = "fragments"
    SOURCE_NOTES = "notes"
    SOURCE_POMODORO = "pomodoro"

    def __init__(self, logger=None, providers=None):
        self._logger = logger
        # 只留下 callable，挡掉误注入的数据本体
        self._providers = {name: fn for name, fn in dict(providers or {}).items()
                           if callable(fn)}

    # ---------------- 自省 ----------------
    def sources(self) -> tuple:
        """当前可用的数据源名（插件可据此决定降级策略）"""
        return tuple(sorted(self._providers))

    def has(self, name: str) -> bool:
        return name in self._providers

    # ---------------- 通用取数 ----------------
    def fetch(self, name: str):
        """按名取只读副本；数据源缺失或读取失败 → ``None``"""
        fn = self._providers.get(name)
        if fn is None:
            self._warn(f"数据源不可用，插件拿到空值：{name}")
            return None
        try:
            data = fn()
        except Exception as exc:          # noqa: BLE001 - 宿主 provider 异常也要隔离
            self._warn(f"数据源读取失败，插件拿到空值：{name} -> {exc!r}")
            return None
        try:
            return copy.deepcopy(data)
        except Exception as exc:          # noqa: BLE001
            self._warn(f"数据源副本化失败，插件拿到空值：{name} -> {exc!r}")
            return None

    # ---------------- 命名访问器 ----------------
    def tasks(self) -> list:
        """全部任务快照（dict 字段见 note/任务模块：task_id/title/note/
        deadline/done/created_at/completed_at/focus_sessions）"""
        return self.fetch(self.SOURCE_TASKS) or []

    def fragments(self) -> list:
        """全部碎片快照（fragment_id/type/category/content/source/created_at）"""
        return self.fetch(self.SOURCE_FRAGMENTS) or []

    def notes(self) -> list:
        """全部笔记快照（note_id/title/content/create_time/update_time）"""
        return self.fetch(self.SOURCE_NOTES) or []

    def pomodoro(self) -> dict:
        """番茄钟当前状态快照（state/phase/remaining_seconds/progress/
        focus_minutes/break_minutes/auto_break/bound_task_id）"""
        return self.fetch(self.SOURCE_POMODORO) or {}

    # ---------------- 内部 ----------------
    def _warn(self, msg: str):
        if self._logger is not None:
            self._logger.warning(f"[插件] {msg}")


class PluginContext:
    """插件可见的宿主能力（白名单）。

    所有可选能力都以「调用时必须存在」为前提注入；缺失时对应方法安全降级
    （记录一条 warning 并返回 False/空值），绝不抛异常到插件里。

    ``config`` 暴露的是**只读快照**（MappingProxyType）——插件不能改宿主配置，
    这是硬边界：配置的唯一写入方是设置页。

    身份与目录（loader 调用 ``for_plugin()`` 派生后才有值）：
      - ``plugin_id``  : 本插件 id；共享上下文下为空串
      - ``plugin_dir`` : 插件自己的文件夹（读自带资源）
      - ``data_dir``   : 插件私有可写目录，首次访问自动创建
    """

    def __init__(self, logger, config=None, *,
                 show_toast=None, open_main_window=None, open_card_mode=None,
                 data=None, data_dir_base="", parent_window=None,
                 plugin_id="", plugin_dir="",
                 capabilities=(), http_post_async=None):
        # logger=None → 退化为 NullHandler 日志器：插件可以无条件调用
        # ctx.logger.info(...)，不必自己判空
        self._logger = logger if logger is not None else _NULL_LOGGER
        self._config = MappingProxyType(dict(config or {}))
        self._show_toast = show_toast
        self._open_main_window = open_main_window
        self._open_card_mode = open_card_mode
        self._data = data if data is not None else PluginData(logger)
        self._data_dir_base = str(data_dir_base or "")
        self._parent_window = parent_window
        self._plugin_id = str(plugin_id or "")
        self._plugin_dir = str(plugin_dir or "")
        # 本插件声明的能力集合（共享宿主 ctx 为空 = 无特殊权限）。
        # 只保留合法字符串，防脏数据；未知能力名在 manifest 校验期已拒。
        self._capabilities = frozenset(
            c for c in (capabilities or ()) if isinstance(c, str))
        # 宿主注入的网络桥：fn(url, headers_dict, body_json_str, timeout,
        # on_done) -> bool。None = 宿主未编译网络能力，桥一律拒绝。
        self._http_post_async = http_post_async

    # ---------------- 身份与目录 ----------------
    @property
    def plugin_id(self) -> str:
        """本插件 id（共享上下文下为空串）"""
        return self._plugin_id

    @property
    def plugin_dir(self) -> str:
        """插件自己的文件夹绝对路径；未注入时返回空串。

        用途：读插件自带的资源（模板 / 图标 / 数据文件）。
        """
        return self._plugin_dir

    @property
    def data_dir(self) -> str:
        """插件私有可写目录（``<data_dir_base>/<插件id>/``），首次访问自动创建。

        - 未注入 base 或 plugin_id 非法 → 记 warning 并返回空串
        - 创建失败（无权限等）→ 记 warning 并返回空串
        插件应把「返回空串」当作「本机不可持久化」的降级信号，而不是去猜路径。
        """
        if not self._data_dir_base or not self._plugin_id:
            self._warn("data_dir 不可用（宿主未注入目录或 plugin_id 为空）")
            return ""
        if not is_safe_plugin_id(self._plugin_id):
            self._warn(f"data_dir 拒绝非法 plugin_id：{self._plugin_id!r}")
            return ""
        path = os.path.join(self._data_dir_base, self._plugin_id)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            self._warn(f"data_dir 创建失败：{path}（{exc}）")
            return ""
        return path

    def parent_window(self):
        """插件弹自定义对话框时的父窗口；不可用时返回 None。

        返回 None 时 Qt 仍可建对话框（只是不居中、可能被主窗口压住），
        因此插件无需为此特判。
        """
        if self._parent_window is None:
            return None
        try:
            return self._parent_window()
        except Exception as exc:          # noqa: BLE001
            self._warn(f"parent_window 调用失败：{exc!r}")
            return None

    def for_plugin(self, plugin_id: str, plugin_dir: str = "",
                   capabilities=()) -> "PluginContext":
        """派生一个绑定了插件身份的上下文（共享全部能力，只换身份字段）。

        loader 在调用 ``create_actions()`` 前派生，并随动作一起交给注册表；
        这样插件能从 ``ctx`` 直接拿到自己的 id 与私有目录，不必自己传参。

        ``capabilities`` 是**本插件**在 manifest 里声明的能力（宿主级桥
        ``http_post_async`` 共享，权限按派生时的声明逐实例判定）。
        """
        return PluginContext(
            logger=self._logger,
            config=dict(self._config),
            show_toast=self._show_toast,
            open_main_window=self._open_main_window,
            open_card_mode=self._open_card_mode,
            data=self._data,
            data_dir_base=self._data_dir_base,
            parent_window=self._parent_window,
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            capabilities=capabilities,
            http_post_async=self._http_post_async,
        )

    # ---------------- 白名单能力 ----------------
    @property
    def logger(self):
        """宿主日志器（插件日志走同一份 float_data/app.log）"""
        return self._logger

    @property
    def config(self):
        """只读配置快照（映射，不可修改）"""
        return self._config

    @property
    def data(self):
        """只读数据快照门面（tasks / fragments / notes / pomodoro）"""
        return self._data

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

    # ---------------- 能力模型 ----------------
    def has_capability(self, name: str) -> bool:
        """本插件是否声明了某项能力（manifest.capabilities）"""
        return name in self._capabilities

    def capabilities(self) -> frozenset:
        """本插件声明的能力集合（只读）"""
        return frozenset(self._capabilities)

    def http_post_json_async(self, url, headers=None, body=None,
                             timeout=30.0, on_done=None) -> bool:
        """通过宿主网络桥发一个 POST JSON 请求（需声明 network 能力）。

        插件**不持有任何网络库**：宿主在后台线程发请求，完成后把结果
        dict 回调进 ``on_done``（保证在 UI 线程执行，可直接更新控件）。

        参数：
          url     : 完整 URL（含 http:// 或 https://）
          headers : 额外请求头 dict（如 {"Authorization": "Bearer ..."}）
          body    : 请求体，dict 会被 JSON 序列化；None 视为 {}
          timeout : 秒（默认 30）
          on_done : 回调 fn(result: dict)，字段：
                      ok     bool   请求是否成功（HTTP 2xx 且未超限）
                      status int    HTTP 状态码（失败 0）
                      body   str    响应文本（UTF-8 解码）
                      error  str    失败原因（成功为空串）
                      url    str    回显请求 URL

        返回值：
          True  = 请求已发起（结果走 on_done）
          False = 未发起（能力未声明 / 宿主未注入桥 / 参数非法）；
                  此时若传了 on_done，仍会回调一次 ok=False 的结果，
                  插件只需统一处理 ``result["ok"]``，不必双轨判断。
        """
        result = {"ok": False, "status": 0, "body": "",
                  "error": "", "url": str(url or "")}

        def _fail(msg: str) -> bool:
            result["error"] = msg
            self._warn(f"http_post_json_async 被拒绝：{msg}")
            if callable(on_done):
                try:
                    on_done(dict(result))
                except Exception as exc:      # noqa: BLE001 - 回调异常不反噬宿主
                    self._warn(f"on_done 回调异常：{exc!r}")
            return False

        if not self.has_capability("network"):
            return _fail("插件未在 manifest 声明 capabilities=[\"network\"]")
        if not callable(self._http_post_async):
            return _fail("宿主未注入网络桥（宿主不支持联网）")
        url_s = str(url or "").strip()
        if not (url_s.startswith("http://") or url_s.startswith("https://")):
            return _fail(f"URL 非法（必须以 http:// 或 https:// 开头）：{url_s!r}")
        if body is not None and not isinstance(body, dict):
            return _fail(f"body 必须是 dict（JSON 对象），收到 {type(body).__name__}")
        try:
            timeout_f = float(timeout)
            if timeout_f <= 0 or timeout_f > 120:
                return _fail(f"timeout 须在 (0, 120] 秒内：{timeout!r}")
        except (TypeError, ValueError):
            return _fail(f"timeout 非法：{timeout!r}")

        try:
            return bool(self._http_post_async(
                url_s, dict(headers or {}), body if body is not None else {},
                timeout_f, on_done))
        except Exception as exc:              # noqa: BLE001 - 桥异常不反噬插件
            return _fail(f"网络桥调用失败：{exc!r}")

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
        self._ctxs = {}          # action_id -> 该动作所属插件的 PluginContext
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
    def register(self, action, plugin_id: str = "", ctx=None) -> bool:
        """登记一个动作。id 缺失或重复 → 拒绝并返回 False。

        ``ctx`` 是该动作所属插件的上下文（loader 传入派生后的实例）。
        注册表记住它，触发时优先用它——否则插件在 ``run(ctx)`` 里
        拿不到自己的 ``plugin_id`` / ``data_dir``。
        """
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
        self._ctxs[action_id] = ctx
        self._order.append(action_id)
        return True

    def unregister(self, plugin_id: str) -> int:
        """注销某个插件的全部动作，返回注销数量（模块本身不卸载）"""
        dead = [aid for aid, owner in self._owners.items() if owner == plugin_id]
        for aid in dead:
            self._actions.pop(aid, None)
            self._owners.pop(aid, None)
            self._ctxs.pop(aid, None)
            if aid in self._order:
                self._order.remove(aid)
        return len(dead)

    def clear(self) -> int:
        """清空注册表（不卸载模块），返回清掉的动作数"""
        n = len(self._actions)
        self._actions.clear()
        self._owners.clear()
        self._ctxs.clear()
        self._order.clear()
        return n

    def context_of(self, action_id: str):
        """取该动作所属插件的上下文（未记录时返回 None）"""
        return self._ctxs.get(action_id)

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
        - ``ctx`` 优先用注册时记下的「该动作所属插件上下文」，
          拿不到才回退到调用方传入的共享上下文（兼容旧调用点）
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
        stored = self._ctxs.get(action_id)
        use_ctx = stored if stored is not None else ctx
        try:
            act.run(use_ctx)
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
