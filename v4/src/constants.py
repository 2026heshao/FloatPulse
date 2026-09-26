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

# ---- 日程任务：勾选动画与撤销条（体感优化 A2 / A3）----
# 勾选动画基准时长（毫秒）；实际时长 = 本值 / anim_speed（见各任务页）。
CHECK_ANIM_MS = 150
# 勾选框回弹峰值缩放：圆框按 1.0 → 1.15 → 1.0 做一次「回弹」。
CHECK_BOUNCE_SCALE = 1.15
# 撤销提示条自动隐藏时长（毫秒）——误勾撤销窗口。
UNDO_BAR_MS = 5000

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
