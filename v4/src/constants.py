# -*- coding: utf-8 -*-
"""
====================================================================
共享常量定义  -  constants
====================================================================
集中定义跨模块复用的业务/UI 常量，消除散落在各文件中的魔法数字
与魔法字符串，便于统一维护与一致性校验。

仅收纳「确实被多个模块共用」或「语义明确、易误改」的常量；
单一模块内独有的尺寸常量仍保留在各自模块（如 MainWindow 的
窗口尺寸、CardWindow 的标签栏宽度等），避免过度集中。
"""

import os
import shutil


# ---- 默认主题 ----
# 单一事实来源：config.json 的初始值、各控件构造函数的默认参数、
# theme.get_colors()/get_*_qss() 的兜底值都引用本常量，避免多处写死后漂移。
# 想改回浅色默认，只改这一行即可。
DEFAULT_THEME = "dark"

# ---- 笔记自动保存防抖间隔（毫秒）----
# card_window（小卡片笔记）与 notes_panel（大窗口笔记）共用同一语义：
# 文本停止编辑后延迟该时长再落盘，合并连续输入、降低写盘频率。
NOTE_AUTOSAVE_INTERVAL_MS = 800

# ---- 笔记自动标题长度 ----
# 未手动命名（title_auto=True）的笔记，标题由内容前 N 字自动生成，
# 见 NoteManager._auto_title。旧数据无 title_auto 字段 → 迁移为 True（默认跟随）。
NOTE_TITLE_MAX_CHARS = 12

# ---- 时间字符串切片常量 ----
# 时间戳统一格式为 "YYYY-MM-DD HH:MM"（见各 manager 的 strftime），
# 以下切片位置用于从完整时间戳中拆分日期与时分，避免散落的魔法下标。
DATETIME_DATE_LEN = 10        # "YYYY-MM-DD" 长度
DATETIME_TIME_START = 11       # 时分起始下标（跳过 " "）
DATETIME_TIME_LEN = 5          # "HH:MM" 长度
DATETIME_MIN_LEN = 16          # 完整日期+时分的最小长度

# ---- 碎片列表预览长度 ----
# 碎片/段落预览截断长度，多处 UI 复用，统一在此定义。
FRAGMENT_PREVIEW_LEN = 60
PARAGRAPH_PREVIEW_LEN = 80
NOTE_PREVIEW_LEN = 80

# ---- 日程任务：勾选动画（体感优化 A2；V3 四段式错峰 2026-10-07）----
# 勾选动画**总长**（毫秒）；实际时长 = motion.duration(本值, anim_speed)，
# 缩放口径统一在 src/motion.py（UI 强化方案 A1）。
# V3 起总长覆盖四段错峰时间轴（见 task_delegate 的 CHECK_* 轴常量）：
# 勾选圈 pop 0→220 + 对勾描画 60→210 + 删除线扫过 80→230 + 文字沉降 0→120。
CHECK_ANIM_MS = 230
# 勾选框回弹峰值缩放：圆框按 1.0 → 1.15 → 1.0 做一次「回弹」。
CHECK_BOUNCE_SCALE = 1.15

# ---- 小卡片（悬浮球旁）软件导航页：图标边长与由此推出的按钮边长 ----
# 三处共用，故收在此处：card_window（渲染）、settings_panel（步进器范围）、
# config._CONFIG_RANGES（越界丢弃）。默认值 36 与历史硬编码一致，
# 老用户视觉零变化。上限 56 的依据：内容区可用宽
# ≈ 440 - 48(侧栏) - 40(边距) = 352px，按钮 96px 时仍排得下 3 列。
MINI_ICON_MIN = 24
MINI_ICON_MAX = 56
MINI_ICON_DEFAULT = 36
# 按钮边长 = 图标 + 40（图标上下留白 24px + 名称文字带约 16px）；36 → 76。
MINI_BTN_PAD = 40


# ---- UI 设计令牌：圆角与字阶（UI 重构 00/01，2026-10-02）----
# 与 theme.py QSS 模板里的 $r_* / $fs_* 令牌**同源同值**：
# QSS 用字符串（"8"），自绘 delegate / overlay 取这里的 int。
# 改任何一个数值必须两边同步（theme.py 圆角四档注释同款纪律）。
RADIUS_WIN = 12      # 窗口
RADIUS_PANEL = 8     # 面板/卡片/列表容器
RADIUS_CTL = 6       # 按钮/输入框
RADIUS_CHIP = 4      # chip/徽章
FS_XS = 11           # 时间戳/计数（等宽 Consolas）
FS_SM = 13           # 正文与列表行
FS_MD = 15           # 页标题/卡标题
FS_LG = 20           # 大标题


def mini_btn_size(icon_px: int) -> int:
    """小卡片图标边长 → 按钮边长（唯一换算点，别处不要另写公式）。"""
    return max(MINI_ICON_MIN, min(MINI_ICON_MAX, int(icon_px))) + MINI_BTN_PAD

# ---- 命令面板触发键候选（2026-10-06 设置页「命令面板」卡）----
# "/" 面板的触发键三选一候选：config.command_palette_trigger 只收这几个值
# （白名单同源引用，见 config._CONFIG_VALUE_WHITELISTS）；Ctrl+K 是站内
# 搜索的键，刻意不在候选里（与命令面板分工的硬边界）。
TRIGGER_KEYS = ("/", ";", "`")

# ---- 轻提示设置项（2026-10-06 设置页「轻提示」卡）----
# config.toast_position / toast_duration 的枚举白名单与基准时长表：
# 三处同源引用（config._CONFIG_VALUE_WHITELISTS、src/toast.py 的时长
# 策略、settings_panel 的分段控件候选），改一处即全链路同步。
TOAST_POSITIONS = ("center", "corner")   # 中下方居中 / 屏幕右下角
TOAST_DURATIONS = ("brief", "standard", "relaxed")   # 短 / 标准 / 长
# 档位 → 纯文本/成功/信息类基准驻留毫秒（错误类 ×1.875、带动作钮
# ×1.5625 的倍率不暴露给用户，见 src/toast.KINDS 的时长策略注释）
TOAST_BASE_MS = {"brief": 2000, "standard": 3200, "relaxed": 5000}

# ---- 文件名非法字符净化 ----
# Windows 不允许出现在文件名中的字符（含保留设备名前缀风险由调用方规避）。
# 集中定义便于各落盘点（拖拽落盘、素材复制）复用同一套净化规则。
_ILLEGAL_FILENAME_CHARS = ["\\", "/", ":", "*", "?", "\"", "<", ">", "|"]


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """将文件名中的 Windows 非法字符替换为安全字符。

    仅替换会导致落盘失败/语义异常的字符，不改动合法内容。
    空字符串或净化后为空时回退为 "file"。
    """
    if not name:
        return "file"
    cleaned = name
    for ch in _ILLEGAL_FILENAME_CHARS:
        cleaned = cleaned.replace(ch, replacement)
    cleaned = cleaned.strip()
    return cleaned or "file"


# ====================================================================
# 容错工具：JSON 数据读写的安全转换与损坏备份
# ====================================================================
def safe_int(value, default: int = 1) -> int:
    """把从 JSON 读出的值安全转换为 int，失败时返回 default。

    各 manager 的 _load() 用它读取 next_id：旧版本或手工编辑过的 JSON
    可能把该字段写成字符串（"12"），若直接参与 max() 会抛 TypeError，
    被外层 except Exception 吞掉后，整个列表会被当成损坏数据清空 ——
    这条链路会造成静默数据丢失，故在读取处即做类型收敛。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def backup_corrupt_file(path: str, suffix: str = ".corrupt.bak") -> str:
    """把疑似损坏的数据文件另存一份副本，避免后续写盘覆盖后无法恢复。

    - 文件不存在或复制失败 → 返回空串，不抛异常（数据加载流程不应被备份失败阻断）
    - 备份成功后返回备份文件路径，便于日志记录
    """
    try:
        if path and os.path.exists(path):
            dst = path + suffix
            shutil.copy2(path, dst)
            return dst
    except OSError:
        pass
    return ""
