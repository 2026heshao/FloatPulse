# -*- coding: utf-8 -*-
"""
====================================================================
碎片内容语义分类  -  fragment_classifier
====================================================================
给碎片加「内容语义」维度（这条碎片是什么东西）：链接 / 代码 / 路径 /
命令行 / 普通文本。与 fragment_manager 的 type（来源渠道：这条碎片是
怎么进来的）互不替代，两个维度独立筛选。

设计要点：
  1. 纯 Python 标准库实现，禁止 import PyQt6 —— 便于直接跑 pytest，
     也保证数据层在无 GUI 环境可用
  2. 判定优先级按 classify() 内顺序短路：link -> path -> command ->
     code -> text；先命中先返回
  3. "宁可判成 text"原则：误判为具体类别会让筛选漏内容，
     因此每条规则都要求整串形态严格匹配（如链接不允许含空白、
     命令要求单行），拿不准一律落到 text
  4. 对任意输入安全：None / 非字符串 / 超长（>100KB）/ emoji /
     控制字符都不抛异常
====================================================================
"""

import re

# 类别常量（存入 Fragment.category 与 UI 下拉 userData）
CAT_TEXT    = "text"      # 普通文本（兜底类别）
CAT_LINK    = "link"      # 独立 URL
CAT_CODE    = "code"      # 代码片段
CAT_PATH    = "path"      # 文件系统路径（Windows 盘符 / UNC / 类 unix 绝对路径）
CAT_COMMAND = "command"   # 命令行

# 类别中文名映射（UI 显示用）
CATEGORY_LABELS = {
    CAT_TEXT:    "普通文本",
    CAT_LINK:    "链接",
    CAT_CODE:    "代码",
    CAT_PATH:    "路径",
    CAT_COMMAND: "命令",
}

# UI 下拉的展示顺序（全部内容 / 链接 / 代码 / 路径 / 命令 / 普通文本）
CATEGORY_ORDER = [CAT_LINK, CAT_CODE, CAT_PATH, CAT_COMMAND, CAT_TEXT]

# 合法类别集合（数据层校验用：from_dict 拿到非法值时兜底重算）
VALID_CATEGORIES = frozenset(CATEGORY_LABELS.keys())

# ---- 规则用正则（模块级预编译，避免逐次调用重编译）----
# 独立 URL：协议开头或 www. 开头；不允许任何空白（整串必须是 URL 本身）
_RE_URL = re.compile(
    r"^(?:https?://|ftp://|www\.)\S+$",
    re.IGNORECASE,
)

# Windows 盘符路径：C:\foo 或 C:/foo，整串不含空白
_RE_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]\S*$")

# UNC 路径：\\server\share 或 //server/share，主机段非空且不含空白
_RE_UNC_PATH = re.compile(r"^\\\\[^\s\\/]+[\\/]\S*$")

# 类 unix 绝对路径：/ 或 // 开头 + 至少两段（"/" 或 "/tmp" 单段不算）
# //server/share 形式同时覆盖部分 unix 风格 UNC 写法
_RE_UNIX_PATH = re.compile(r"^(?://|/)(?:[^/\s]+/)+[^/\s]+/?$")

# `xxx.exe <参数>` 形态的命令：第一个 token 以 .exe 结尾且后面还有参数
_RE_EXE_CMD = re.compile(r"^[\w.\-]+\.exe\s+\S+", re.IGNORECASE)

# 强代码特征（命中任一即判 code）
_RE_CODE_FEATURES = re.compile(
    r"\bdef\s"            # Python 函数
    r"|\bclass\s+\w+\s*:"  # Python 类
    r"|\bimport\s"         # import 语句
    r"|\bfrom\s+[\w.]+\s+import\b"  # from x import y
    r"|#include"           # C/C++ 头文件
    r"|\bpublic\s"         # Java/C#
    r"|\bprivate\s"
    r"|\bfunction\b"       # JS
    r"|=>"                 # JS 箭头函数
    r"|\(\)\s*\{"          # C/Java/Go 函数体
    r"|;\s*$"              # 以分号结尾的行
    r"|</\w+>"             # HTML/XML 闭合标签
    r"|\bSELECT\b.+\bFROM\b",  # SQL
    re.IGNORECASE | re.MULTILINE,
)

# 命令白名单：首个 token 命中即判 command（小写比较）
_COMMAND_WHITELIST = frozenset({
    "pip", "pip3", "python", "python3", "git", "npm", "npx", "node",
    "curl", "wget", "docker", "gradle", "mvn", "conda", "explorer",
    "start", "cd", "dir", "copy", "del", "robocopy", "net", "sc",
    "taskkill", "winget", "choco", "scoop", "ssh", "ping", "ipconfig",
    "adb", "make", "cmake", "gcc", "g++", "java", "javac", "dotnet",
    "code", "notepad", "mspaint", "powershell", "cmd",
})

# 命令/路径判定的行数上限：多行内容不判 command（宁可 text）
# 命令行理论上不会换行；路径同理由 \S*$ 隐式排除

# 括号/分号密度阈值：{}()[];= 合计占非空白字符比超过 4% 判 code
_CODE_DENSITY_THRESHOLD = 0.04
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")

# 含中文的句子走强特征正则的 CJK 占比上限：
# "我们讨论了 import 这个词的含义" 这类正文含代码词的场景必须落回 text
_CJK_RATIO_LIMIT = 0.5

# 常见 ASCII 空白（str.count 逐字符统计用，避免 findall 建巨型列表）
_ASCII_WS = " \t\n\r\x0b\x0c"
_DENSITY_LITERALS = "{}()[];="


def _is_link(content: str) -> bool:
    """整串是一个独立 URL（去首尾空白后不允许含任何空白/换行）"""
    stripped = content.strip()
    if not stripped or re.search(r"\s", stripped):
        return False
    return bool(_RE_URL.match(stripped))


def _is_path(content: str) -> bool:
    """整串是文件系统路径（Windows 盘符 / UNC / 类 unix 绝对路径）"""
    stripped = content.strip()
    if not stripped:
        return False
    if _RE_WIN_PATH.match(stripped):
        return True
    if _RE_UNC_PATH.match(stripped):
        return True
    if _RE_UNIX_PATH.match(stripped):
        return True
    return False


def _is_command(content: str) -> bool:
    """单行命令行：首个 token 命中白名单，或 `xxx.exe <参数>` 形态。

    含中文的一律不判命令（"pip 是 Python 的包管理器"这类句子
    首 token 也会命中白名单，按"宁可 text"原则排除）。
    """
    stripped = content.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return False
    if _RE_CJK.search(stripped):
        return False
    parts = stripped.split()
    first = parts[0].lower().strip('"')
    if first in _COMMAND_WHITELIST:
        return True
    return bool(_RE_EXE_CMD.match(stripped))


def _is_code(content: str) -> bool:
    """代码特征：多行缩进 / 强代码正则 / 括号分号密度。

    中文守卫：
      - 多行缩进规则不受 CJK 影响（带中文注释的缩进代码块仍是代码）
      - 强特征正则要求 CJK 占比 <= 50%（否则视为含代码词的正文句子）
      - 密度规则仅对完全不含 CJK 的内容启用
        （"价格是 (15 + 20) * 2 = 70 元"这类中文算式不判代码）
    """
    # a) 多行且存在缩进行（行首 4 空格或 tab）
    if "\n" in content:
        for line in content.splitlines():
            if line.startswith("    ") or line.startswith("\t"):
                return True

    has_cjk = bool(_RE_CJK.search(content))

    if has_cjk:
        # 计数用 str.count / sub 反推（C 速度，超长串不会建巨型列表）
        cjk = len(content) - len(_RE_CJK.sub("", content))
        non_ws = len(content) - sum(content.count(c) for c in _ASCII_WS)
        if non_ws and cjk / non_ws > _CJK_RATIO_LIMIT:
            return False
        return bool(_RE_CODE_FEATURES.search(content))

    # 无 CJK：强特征 + 密度规则都启用（计数走 str.count，C 速度）
    if _RE_CODE_FEATURES.search(content):
        return True
    non_ws = len(content) - sum(content.count(c) for c in _ASCII_WS)
    if non_ws:
        hits = sum(content.count(c) for c in _DENSITY_LITERALS)
        if hits / non_ws > _CODE_DENSITY_THRESHOLD:
            return True
    return False


def classify(content) -> str:
    """
    碎片内容 -> 内容语义类别。

    判定优先级（先命中先返回）：
      link -> path -> command -> code -> text

    任意输入安全：None / 非字符串 / 超长串 / emoji / 控制字符均不抛异常，
    拿不准的一律返回 CAT_TEXT（"宁可判成 text"原则）。
    """
    try:
        if content is None:
            return CAT_TEXT
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            return CAT_TEXT
        if _is_link(content):
            return CAT_LINK
        if _is_path(content):
            return CAT_PATH
        if _is_command(content):
            return CAT_COMMAND
        if _is_code(content):
            return CAT_CODE
        return CAT_TEXT
    except Exception:
        # 分类绝不能让业务链路崩溃
        return CAT_TEXT
