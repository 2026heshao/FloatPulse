# -*- coding: utf-8 -*-
"""
====================================================================
配置管理模块  -  ConfigManager
====================================================================
管理用户配置的读写，持久化到同目录下 config.json。

设计要点：
  1. 使用与 exe / py 同目录下的 config.json 持久化用户配置
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化默认配置；json 解析异常回退到默认配置
  4. 配置项带类型校验，损坏值回退默认
  5. 所有修改后调用 save() 写盘，原子写入避免损坏
  6. UI 层只能通过本类公开方法操作，禁止直接读写 config.json

配置项：
  - theme:                主题名 ("light" | "dark")，默认值见 constants.DEFAULT_THEME
  - clipboard_max_items:  剪贴板历史条数上限
  - auto_hide_seconds:    悬浮球空闲吸边隐藏秒数
  - auto_hide_enabled:    悬浮球空闲吸边自动隐藏总开关（关闭后球始终完整显示）
  - clipboard_filter_apps: 剪贴板过滤应用列表（不捕获这些进程的复制）
  - clipboard_capture_images: 剪贴板中的图片是否自动存入临时素材池
  - main_window_geometry: 大窗口几何 "x,y,w,h"（空串 = 无记忆，启动用默认尺寸居中）
  - restore_last_page:    启动时是否恢复上次浏览的页面
  - last_page_index:      最后浏览的页面索引（0..LAST_PAGE_INDEX_MAX，说明页 8 不记录）
  - close_to_tray:        关闭主窗口时最小化到托盘（不退出程序）
  - task_reminder_enabled: 任务到期提醒开关（启动时 + 每日 9:00 托盘气泡）
  - quick_capture_enabled: 全局快速捕捉条开关
  - quick_capture_hotkey:  快速捕捉全局热键（如 "Ctrl+Alt+K"）
  - quick_capture_pos:     快速捕捉输入条最后拖动位置 [x, y]（None=屏幕居中）
  - screenshot_enabled:   截图钉屏开关（Ctrl+Alt+S 框选 → 置顶参考浮窗）
  - screenshot_hotkey:     截图钉屏全局热键（如 "Ctrl+Alt+S"）
  - pomodoro_enabled:      番茄钟总开关（球体进度环 + 右键菜单控制）
  - pomodoro_focus_minutes: 专注相位时长（分钟，1-120，默认 25）
  - pomodoro_break_minutes: 休息相位时长（分钟，1-60，默认 5）
  - pomodoro_auto_break:   专注结束后自动进入休息
  - plugins_enabled:      悬浮球外置插件总闸（启用 plugins/ 下的插件包）
  - plugins_disabled:     被单独停用的插件 id 列表（空 = 全部启用）
  - obsidian_vault_path:  Obsidian vault 根目录（空串=未选择，导出时弹框让用户选）
  - export_notes:         导出笔记到 Obsidian（默认开）
  - export_fragments:     导出碎片到 Obsidian（默认开）
  - export_tasks:         导出任务到 Obsidian（默认开）
  - ball_size:            悬浮球球体直径（像素，48-88，默认 64）
  - hide_on_fullscreen:   全屏应用（视频/游戏/演示）前台时自动隐藏悬浮球
  - temp_asset_max_count: 临时素材数量上限（超出按添加时间淘汰最旧的）
  - temp_asset_max_days:  临时素材自动清理天数（0 = 不按天数清理）
  - temp_asset_max_file_mb: 单个临时素材体积上限（MB，0 = 不限制）
  - asset_thumb_size:     临时素材缩略图宽度（像素，80-160，决定网格每行个数）
====================================================================
"""

import os
import json

from src.constants import DEFAULT_THEME


# 默认配置
DEFAULT_CONFIG = {
    "theme":                DEFAULT_THEME,
    "clipboard_max_items":  200,
    "auto_hide_seconds":    3,
    "auto_hide_enabled":    True,         # 悬浮球空闲吸边自动隐藏总开关
    "clipboard_filter_apps": [],
    "main_window_geometry": "",
    "card_always_show":     False,
    "temp_asset_max_count": 50,           # 临时素材数量上限
    "temp_asset_max_days":  30,           # 临时素材自动清理天数（0 表示不按天数清理）
    "temp_asset_max_file_mb": 50,         # 单个素材体积上限（MB，0 表示不限制）
    "asset_thumb_size":     128,          # 素材缩略图宽度（像素，80-160，决定每行个数）
    "ball_visible":         True,         # 悬浮球是否显示
    "apps":                 [],           # 软件导航条目列表
    "app_card_size":        96,           # 软件卡片边长（像素）
    "app_auto_back_home":   False,        # 启动软件后是否自动回到主页面
    "anim_speed":           1.0,          # 悬浮球动画速度档位（0.5-2.0，统一缩放动画时长）
    "ball_position":        None,         # 悬浮球最后保存位置 [x, y]
    "restore_last_page":    False,        # 启动时是否恢复上次浏览的页面
    "last_page_index":      0,            # 最后浏览的页面索引（0..LAST_PAGE_INDEX_MAX）
    "close_to_tray":        True,         # 关闭主窗口 → 最小化到托盘（False 沿用旧规则）
    "task_reminder_enabled": True,        # 任务到期提醒（托盘气泡）
    "quick_capture_enabled": True,        # 全局快速捕捉条
    "quick_capture_hotkey": "Ctrl+Alt+K", # 快速捕捉全局热键
    "quick_capture_pos":    None,         # 快速捕捉输入条拖动后位置 [x, y]
    "screenshot_enabled":   True,         # 截图钉屏开关（Ctrl+Alt+S）
    "screenshot_hotkey":    "Ctrl+Alt+S", # 截图钉屏全局热键
    "pomodoro_enabled":     True,         # 番茄钟总开关（球体进度环 + 右键菜单）
    "pomodoro_focus_minutes": 25,         # 专注相位时长（分钟，1-120）
    "pomodoro_break_minutes": 5,          # 休息相位时长（分钟，1-60）
    "pomodoro_auto_break":  False,        # 专注结束后是否自动进入休息
    "plugins_enabled":      True,         # 悬浮球外置插件总闸（plugins/ 下的插件包）
    "plugins_disabled":     [],           # 被单独停用的插件 id 列表（插件中心开关落盘）
    "obsidian_vault_path":  "",           # Obsidian vault 根目录（空=未选择）
    "export_notes":         True,         # 导出笔记到 Obsidian
    "export_fragments":     True,         # 导出碎片到 Obsidian
    "export_tasks":         True,         # 导出任务到 Obsidian
    "ball_size":            64,           # 悬浮球球体直径（像素，48-88）
    "hide_on_fullscreen":   True,         # 全屏应用前台时自动隐藏悬浮球
    "fragment_preview_visible": True,     # 碎片工作台右侧预览面板是否显示
    "clipboard_capture_images": True,     # 剪贴板图片自动存入临时素材池
    "nav_order":            [],           # 左栏功能页显示顺序（空 = 从未自定义，用默认顺序）
    # 左栏当前**已展开**的分组集合（多组可同时展开；空列表 = 全部折叠）
    # 取值见 src/nav_layout.NAV_GROUPS；收敛逻辑在 sanitize_expanded_groups
    "nav_expanded_groups":  ["workbench"],
    # 插件声明 capabilities=["ai"] 且被用户在设置页下拉框勾选接入后，
}

# 配置项类型映射（用于校验）
_CONFIG_TYPES = {
    "theme":                str,
    "clipboard_max_items":  int,
    "auto_hide_seconds":    int,
    "auto_hide_enabled":    bool,
    "clipboard_filter_apps": list,
    "main_window_geometry": str,
    "card_always_show":     bool,
    "temp_asset_max_count": int,
    "temp_asset_max_days":  int,
    "temp_asset_max_file_mb": int,
    "asset_thumb_size":     int,
    "ball_visible":         bool,
    "apps":                 list,
    "app_card_size":        int,
    "app_auto_back_home":   bool,
    "anim_speed":           float,
    "ball_position":        list,
    "restore_last_page":    bool,
    "last_page_index":      int,
    "close_to_tray":        bool,
    "task_reminder_enabled": bool,
    "quick_capture_enabled": bool,
    "quick_capture_hotkey": str,
    "quick_capture_pos":    list,
    "screenshot_enabled":   bool,
    "screenshot_hotkey":    str,
    "pomodoro_enabled":     bool,
    "pomodoro_focus_minutes": int,
    "pomodoro_break_minutes": int,
    "pomodoro_auto_break":  bool,
    "plugins_enabled":      bool,
    "plugins_disabled":     list,
    "obsidian_vault_path":  str,
    "export_notes":         bool,
    "export_fragments":     bool,
    "export_tasks":         bool,
    "ball_size":            int,
    "hide_on_fullscreen":   bool,
    "fragment_preview_visible": bool,
    "clipboard_capture_images": bool,
    "nav_order":            list,
    "nav_expanded_groups":  list,
}

# 主窗口「最后浏览页面」允许的最大物理索引。
# 页面组成：0-6 面板 / 7 软件导航 / 8 说明页（不记录）/ 9 插件中心。
# 新增页面时**必须同步抬高此值**，否则该页存不进配置（读取时越界丢弃）。
LAST_PAGE_INDEX_MAX = 9

# 配置项取值范围（数值类）
_CONFIG_RANGES = {
    "clipboard_max_items":  (10, 10000),
    "auto_hide_seconds":    (1, 60),
    "temp_asset_max_count": (5, 500),
    "temp_asset_max_days":  (0, 365),
    # 单文件体积上限：与设置页步进器范围 0-2048（每档 10MB）保持一致；0 = 不限制
    "temp_asset_max_file_mb": (0, 2048),
    # 素材缩略图宽度：与设置页步进器范围 80-160（每档 8px）保持一致
    "asset_thumb_size":     (80, 160),
    # 软件卡片尺寸：与主窗口设置页步进器范围 60-140（每档 4px）保持一致
    "app_card_size":        (60, 140),
    "anim_speed":           (0.5, 2.0),
    # 主窗口页面索引上限 = 最大物理索引（0-6 面板 + 7 软件导航 + 9 插件中心；
    # 说明页 8 不记录）。页面增删时**必须同步这里**，否则新页存不进配置：
    # 读取时 lo<=val<=hi 不满足会静默丢弃，表现为「启动时恢复不到该页」。
    "last_page_index":      (0, LAST_PAGE_INDEX_MAX),
    # 悬浮球球体直径：与设置页 Stepper 范围 48-88 保持一致
    "ball_size":            (48, 88),
    # 番茄钟时长（分钟）：与设置页 Stepper 范围保持一致
    "pomodoro_focus_minutes": (1, 120),
    "pomodoro_break_minutes": (1, 60),
}


# ====================================================================
# 左栏功能页排序（nav_order）：key 集合与默认顺序
# ====================================================================
# key 是稳定的功能标识（与 UI 文案、物理索引解耦）；
# main_window 侧维护 key → QStackedWidget 固定物理索引的映射，
# 物理索引与功能的绑定永不改变（拖动换位只改变左栏显示顺序）。
NAV_PAGE_KEYS = frozenset({
    "fragments",   # 碎片工作台
    "tasks",       # 日程任务
    "notes",       # 笔记管理
    "knowledge",   # 知识库
    "assets",      # 临时素材
    "apps",        # 软件导航
    "nav",         # 网址导航
    "plugins",     # 插件中心（2026-09-27 新增，物理索引 9）
})

# 默认显示顺序（与未自定义时的历史左栏顺序一致）
DEFAULT_NAV_ORDER = ["fragments", "tasks", "notes", "knowledge",
                     "assets", "apps", "nav", "plugins"]

# 2026-09-27 之前的 7 键旧版顺序集（用于旧配置无损升级）
_LEGACY_NAV_KEYS = NAV_PAGE_KEYS - {"plugins"}


def sanitize_nav_order(raw, valid_keys=NAV_PAGE_KEYS):
    """校验 nav_order 配置：固定功能页 key 的排列 + 可选插件页 key。

    2026-09-28 起插件页导航键（``plugin:<id>``）参与拖拽换位，顺序里
    可以混有插件 key。校验规则：
      - 固定功能页 key（valid_keys）必须**恰好各出现一次**（缺/多/重复 → None）
      - 非固定 key 必须 ``plugin:`` 开头（防拼写错误 / 非法值混入）且不重复
      - 合法 → 返回 list 副本（顺序即用户偏好，原样保留）
      - **旧版兼容**：7 键旧排列（缺 plugins）→ 保留用户自定义顺序，
        把 "plugins" 追加到末尾返回，不丢弃用户的排序偏好
    """
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        return None
    fixed = [k for k in raw if k in valid_keys]
    # 旧版 7 键排列 → 追加 plugins 后放行
    if len(fixed) == len(_LEGACY_NAV_KEYS) and set(fixed) == _LEGACY_NAV_KEYS:
        extra = [k for k in raw if k not in valid_keys]
        if all(k.startswith("plugin:") for k in extra) \
                and len(set(extra)) == len(extra):
            return fixed + ["plugins"] + extra
        return fixed + ["plugins"] if not extra else None
    if set(fixed) != set(valid_keys) or len(fixed) != len(valid_keys):
        return None
    extra = [k for k in raw if k not in valid_keys]
    if any(not k.startswith("plugin:") for k in extra):
        return None
    if len(set(extra)) != len(extra):
        return None
    return list(raw)


class ConfigManager:
    """
    用户配置管理器。

    对外提供 get / set / save 接口，内部维护内存配置字典，
    修改完毕调用 save() 写入磁盘 json 文件。
    UI 层禁止直接读写 config.json 文件。
    """

    def __init__(self, json_path: str):
        self._json_path = json_path     # config.json 完整路径
        self._config = dict(DEFAULT_CONFIG)  # 内存配置（默认值副本）
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """
        从磁盘加载配置。
        - 文件不存在 → 使用默认配置
        - json 解析异常 → 使用默认配置
        - 配置项缺失/类型错误/取值越界 → 该项回退默认
        """
        if not os.path.exists(self._json_path):
            return  # 直接用默认配置

        try:
            with open(self._json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return  # 结构异常，用默认

            # 逐项校验并合并
            for key, default_val in DEFAULT_CONFIG.items():
                if key not in data:
                    continue  # 缺失项保留默认值
                val = data[key]
                expected_type = _CONFIG_TYPES.get(key)
                if expected_type and not isinstance(val, expected_type):
                    continue  # 类型错误，保留默认值
                # 数值范围校验
                if key in _CONFIG_RANGES:
                    lo, hi = _CONFIG_RANGES[key]
                    if not (lo <= val <= hi):
                        continue
                self._config[key] = val
        except Exception:
            # json 解析异常或文件损坏 → 保留默认配置
            pass

    def save(self):
        """
        统一保存：将内存配置一次性写入磁盘 json。
        原子写入：先写临时文件，再替换原文件。
        """
        try:
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            # 写盘失败不崩溃
            pass

    # ---------------- 读写接口 ----------------
    def get(self, key: str, default=None):
        """
        读取配置项。
        - key 存在 → 返回值
        - key 不存在 → 返回 default（若 default 为 None 则返回 DEFAULT_CONFIG 中的默认值）
        """
        if key in self._config:
            return self._config[key]
        if default is not None:
            return default
        return DEFAULT_CONFIG.get(key)

    def set(self, key: str, value):
        """
        写入配置项（仅修改内存，不立即写盘）。
        - 自动校验类型与取值范围，非法值将被忽略
        - 修改成功返回 True，失败返回 False
        - 写盘需另外调用 save()
        """
        expected_type = _CONFIG_TYPES.get(key)
        if expected_type and not isinstance(value, expected_type):
            return False
        if key in _CONFIG_RANGES:
            lo, hi = _CONFIG_RANGES[key]
            if not (lo <= value <= hi):
                return False
        self._config[key] = value
        return True

    def update(self, items: dict):
        """
        批量写入配置项（仅修改内存，不立即写盘）。
        返回成功写入的项数。
        """
        count = 0
        for key, value in items.items():
            if self.set(key, value):
                count += 1
        return count

    def reset_to_default(self):
        """重置为默认配置（仅修改内存，不立即写盘）"""
        self._config = dict(DEFAULT_CONFIG)

    def as_dict(self) -> dict:
        """返回配置的完整副本（用于UI展示）"""
        return dict(self._config)
