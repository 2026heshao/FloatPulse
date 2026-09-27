# -*- coding: utf-8 -*-
"""fragment_classifier 单元测试：每条规则正反例 + 边界安全 + 旧数据迁移。

classify() 优先级：link -> path -> command -> code -> text。
"宁可判成 text"原则的边界（如「正文里带链接的长段落」）单独覆盖。
"""
import pytest

from src.fragment_classifier import (
    CAT_TEXT, CAT_LINK, CAT_CODE, CAT_PATH, CAT_COMMAND,
    CATEGORY_LABELS, CATEGORY_ORDER, VALID_CATEGORIES,
    classify,
)


# ---------------- a) link ----------------
@pytest.mark.parametrize("content", [
    "https://github.com/example/repo",
    "http://news.example.com/article?id=42&page=2",
    "ftp://files.example.com/pub/readme.txt",
    "www.baidu.com/s?wd=floatpulse",
])
def test_link_positive(content):
    assert classify(content) == CAT_LINK


@pytest.mark.parametrize("content", [
    # 正文里提到链接的一整段话 -> 必须是 text（边界重点）
    "这篇文章讲得不错 https://example.com/a 建议你看完再决定",
    "访问 www.example.com 获取详情",
    # 缺协议头
    "example.com/index.html",
    # URL 换行拼接
    "https://example.com/a\nhttps://example.com/b",
])
def test_link_negative(content):
    assert classify(content) == CAT_TEXT


# ---------------- b) path ----------------
@pytest.mark.parametrize("content", [
    "C:/Users/a.txt",
    "D:\\桌面\\x.exe",
    r"\\server\share\docs",
    "/usr/local/bin",
    "//srv/data",
])
def test_path_positive(content):
    assert classify(content) == CAT_PATH


@pytest.mark.parametrize("content", [
    "/",                       # 单段不算
    "/tmp",                    # 至少两段
    "相对/路径/不带根",          # 非 / 开头的类 unix 路径
    "C: 桌面里有空格的句子",      # 盘符后不是路径分隔符
    "这是一句话，不是 /usr/bin 路径",  # 含正文
])
def test_path_negative(content):
    assert classify(content) == CAT_TEXT


# ---------------- c) command ----------------
@pytest.mark.parametrize("content", [
    "pip install PyQt6",
    "git clone https://github.com/example/repo.git",
    "explorer.exe C:\\Windows",
    "npm run build",
    "taskkill /IM notepad.exe /F",
])
def test_command_positive(content):
    assert classify(content) == CAT_COMMAND


@pytest.mark.parametrize("content", [
    "pip 是 Python 的包管理器，今天聊到了它",   # pip 不是首 token
    "learn pip quickly",                      # 不在白名单的首 token
    "explorer.exe",                           # .exe 无参数 -> 不判命令
    "pip install x\npip install y",           # 多行不判命令（宁可 text）
])
def test_command_negative(content):
    assert classify(content) == CAT_TEXT


# ---------------- d) code ----------------
@pytest.mark.parametrize("content", [
    "def hello():\n    return 1",             # 多行 + 缩进
    "import os\nimport sys",
    "class Foo:\n    pass",
    "const f = (x) => x * 2;",                # JS 箭头 + 分号
    "<html><body>hi</body></html>",           # HTML 闭合标签
    "SELECT id, name FROM users WHERE age > 18;",  # SQL
    "for (int i = 0; i < n; i++) { sum += i; }",   # 括号分号密度
])
def test_code_positive(content):
    assert classify(content) == CAT_CODE


@pytest.mark.parametrize("content", [
    "我们讨论了 import 这个词的含义",            # 含关键词字样但明显是中文句子
    "价格是 (15 + 20) * 2 = 70 元",           # 括号密度不足
])
def test_code_negative(content):
    assert classify(content) == CAT_TEXT


# ---------------- e) text 兜底 ----------------
@pytest.mark.parametrize("content", [
    "今天天气不错，适合出去走走",
    "Plain English sentence about nothing in particular",
    "1234567890",
    "42",
    "",
    "   ",
    "\n\n",
])
def test_text_fallback(content):
    assert classify(content) == CAT_TEXT


# ---------------- 任意输入安全 ----------------
def test_classify_none_safe():
    assert classify(None) == CAT_TEXT


def test_classify_non_str_safe():
    # 非字符串不崩（coerce 成 str 后分类）
    assert classify(123) == CAT_TEXT
    assert classify(3.14) == CAT_TEXT
    # str(list) 是 Python 列表字面量形态（括号密度高）-> code 合理
    assert classify(["a", "b"]) == CAT_CODE


def test_classify_100kb_fast():
    """100KB 纯中文串必须廉价分类（回归护栏）。

    历史：此用例曾断言 < 50ms，实测分类本身仅约 6ms，但全量 pytest 时
    因 CPU 争抢偶发冲到 54ms 而假失败（单跑必过）。根因是 _is_code 里
    用 _RE_CJK.sub() 数 CJK 个数，re.sub 在 188KB 串上要花 9.4ms；
    已改为 str.translate(删除映射)，端到端 10.9ms -> 6.3ms。

    阈值取 150ms：约为当前实测值的 24 倍余量，仍能在真出现 O(n^2)
    退化时（例如换回 re.sub 叠加多轮扫描）稳定报警，同时隔绝机器负载抖动。
    """
    import time
    big = ("这是一段很长的普通文本内容，没有任何代码特征。" * 8192)   # >100KB
    assert len(big) > 100 * 1024
    t0 = time.perf_counter()
    assert classify(big) == CAT_TEXT
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.15, f"100KB 分类耗时 {elapsed * 1000:.1f}ms，超出 150ms 护栏"


def test_cjk_count_translate_equivalent_to_regex():
    """_CJK_DELETE_MAP 必须与 _RE_CJK 严格等价（性能改写不能漂移语义）。

    _is_code 的 CJK 占比守卫依赖这个计数。若两者范围不一致，
    含中文的代码块判定会静默跑偏，且因缺少断言而难以察觉。
    """
    from src.fragment_classifier import _RE_CJK, _CJK_DELETE_MAP

    # 映射规模与码位边界
    assert len(_CJK_DELETE_MAP) == 0x9FFF - 0x4E00 + 1
    for cp in (0x4DFF, 0x4E00, 0x9FFF, 0xA000, 0x1F389):
        ch = chr(cp)
        via_re = len(_RE_CJK.sub("", ch)) == 0
        via_map = len(ch.translate(_CJK_DELETE_MAP)) == 0
        assert via_re == via_map, f"U+{cp:04X} 计数口径不一致"

    # 随机串交叉验证
    import random

    random.seed(20260927)
    pool = "中文测试abcXYZ 123\n\t{}()[];=defclass import\u4dff\u4e00\u9fff\U0001F389"
    for _ in range(500):
        s = "".join(random.choice(pool) for _ in range(random.randint(0, 100)))
        assert (
            len(_RE_CJK.sub("", s)) == len(s.translate(_CJK_DELETE_MAP))
        ), f"计数不一致: {s!r}"


def test_classify_emoji_and_control_chars():
    assert classify("🎉🎉 带表情的碎片 ✨") == CAT_TEXT
    assert classify("带\x00控制\x01字符\x1b[31m的内容") == CAT_TEXT


# ---------------- 常量表一致性 ----------------
def test_category_tables_consistent():
    assert set(CATEGORY_LABELS.keys()) == VALID_CATEGORIES
    assert set(CATEGORY_ORDER) == VALID_CATEGORIES
    assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER))


# ---------------- 旧数据迁移（from_dict 兜底）----------------
def _make_fragment_dict(**overrides):
    """构造一条「没有 category 字段」的旧版碎片字典"""
    d = {
        "fragment_id": 1,
        "type": "clipboard_text",
        "content": "https://github.com/example/repo",
        "source": "",
        "created_at": "2026-09-26 10:00",
    }
    d.update(overrides)
    return d


def test_from_dict_migration_missing_category():
    from src.fragment_manager import Fragment
    frag = Fragment.from_dict(_make_fragment_dict())
    assert frag.category == CAT_LINK


def test_from_dict_migration_various_contents():
    from src.fragment_manager import Fragment
    cases = [
        ("pip install PyQt6", CAT_COMMAND),
        ("C:/Users/a.txt", CAT_PATH),
        ("def f():\n    pass", CAT_CODE),
        ("普通中文句子", CAT_TEXT),
    ]
    for content, expected in cases:
        frag = Fragment.from_dict(_make_fragment_dict(content=content))
        assert frag.category == expected, content


def test_from_dict_invalid_category_falls_back():
    from src.fragment_manager import Fragment
    frag = Fragment.from_dict(
        _make_fragment_dict(category="bogus_category"))
    assert frag.category == CAT_LINK     # 兜底按内容重算


def test_from_dict_valid_category_preserved():
    from src.fragment_manager import Fragment
    frag = Fragment.from_dict(
        _make_fragment_dict(content="普通中文句子", category=CAT_CODE))
    # 合法的手动分类应被保留（不被自动重算覆盖）
    assert frag.category == CAT_CODE


def test_roundtrip_to_dict_keeps_category():
    from src.fragment_manager import Fragment
    frag = Fragment.from_dict(_make_fragment_dict())
    d = frag.to_dict()
    assert d["category"] == CAT_LINK
    frag2 = Fragment.from_dict(d)
    assert frag2.category == CAT_LINK
