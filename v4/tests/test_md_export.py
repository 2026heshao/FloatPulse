# -*- coding: utf-8 -*-
"""md_export 单元测试：文件名安全化 / frontmatter / 三个渲染器 / 导出编排。

md_export 是纯逻辑层（不 import PyQt6），因此本测试用「鸭子类型替身对象」
（SimpleNamespace）直接驱动，不需要 QApplication，也不需要任何管理器实例。
"""
import os
from types import SimpleNamespace

import pytest

from src.fragment_classifier import CATEGORY_LABELS
from src.fragment_manager import TYPE_LABELS
from src.md_export import (
    ExportResult, FALLBACK_FILENAME, TASKS_FILENAME, TEMP_NOTE_TITLE, TAG_VALUE,
    build_frontmatter, export_all, render_fragment_day, render_note,
    render_tasks, safe_filename,
)


# ====================================================================
# 替身工厂
# ====================================================================
def mk_note(note_id=1, title="我的笔记", content="正文内容",
            create_time="2026-09-27 10:00", update_time="2026-09-27 12:00"):
    return SimpleNamespace(note_id=note_id, title=title, content=content,
                           create_time=create_time, update_time=update_time)


def mk_frag(fid=1, ftype="clipboard_text", content="print(1)", category="code",
            created_at="2026-09-27 09:15", source="测试"):
    return SimpleNamespace(fragment_id=fid, type=ftype, content=content,
                           category=category, created_at=created_at,
                           source=source)


def mk_task(tid=1, title="写周报", note="备注", deadline="2026-09-30",
            done=False, focus_sessions=2, created_at="2026-09-27 08:00"):
    return SimpleNamespace(task_id=tid, title=title, note=note,
                           deadline=deadline, done=done,
                           focus_sessions=focus_sessions,
                           created_at=created_at)


# ====================================================================
# safe_filename
# ====================================================================
@pytest.mark.parametrize("bad", list('\\/:*?"<>|'))
def test_safe_filename_replaces_all_nine_illegal_chars(bad):
    """9 个 Windows 非法字符逐一被替换掉"""
    out = safe_filename(f"a{bad}b")
    assert bad not in out
    assert out == "a_b"


def test_safe_filename_pure_spaces_falls_back():
    assert safe_filename("     ") == FALLBACK_FILENAME


def test_safe_filename_pure_dots_falls_back():
    assert safe_filename("....") == FALLBACK_FILENAME


def test_safe_filename_overlong_truncated():
    out = safe_filename("中" * 300, maxlen=60)
    assert len(out) == 60
    assert out == "中" * 60


def test_safe_filename_chinese_preserved():
    assert safe_filename("我的学习笔记") == "我的学习笔记"


def test_safe_filename_empty_and_none():
    assert safe_filename("") == FALLBACK_FILENAME
    assert safe_filename(None) == FALLBACK_FILENAME


def test_safe_filename_strips_trailing_dot_and_space():
    assert safe_filename("标题. ") == "标题"
    assert safe_filename("  标题  ") == "标题"


def test_safe_filename_reserved_device_names():
    assert safe_filename("CON") == "_CON"
    assert safe_filename("nul") == "_nul"
    assert safe_filename("COM1") == "_COM1"


def test_safe_filename_truncation_does_not_leave_trailing_dot():
    out = safe_filename("a" * 59 + ".", maxlen=60)
    assert not out.endswith(".")


@pytest.mark.parametrize("raw", [
    "a\\b", "a/b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b",
    "", "   ", "....", "中" * 300, "CON", "普通标题", "\x07控制符",
])
def test_safe_filename_stable_across_calls(raw):
    """同名输入永远得同名输出（重复导出幂等的前提）"""
    assert safe_filename(raw) == safe_filename(raw)


# ====================================================================
# frontmatter
# ====================================================================
def test_frontmatter_tags_use_yaml_list_syntax():
    fm = build_frontmatter(created="2026-09-27 10:00",
                           updated="2026-09-27 12:00")
    assert fm.startswith("---\n")
    assert fm.endswith("\n---")
    assert f"tags: [{TAG_VALUE}]" in fm
    assert f'tags: "{TAG_VALUE}"' not in fm


def test_frontmatter_optional_keys_omitted_when_none():
    fm = build_frontmatter(created="2026-09-27", updated="2026-09-27")
    assert "source:" not in fm
    assert "category:" not in fm


def test_frontmatter_quotes_values_with_colon():
    """含冒号的时间戳必须加引号，否则 YAML 会被误解析"""
    fm = build_frontmatter(created="2026-09-27 10:00")
    assert 'created: "2026-09-27 10:00"' in fm


def test_frontmatter_source_and_category_when_given():
    fm = build_frontmatter(source="碎片", category="代码")
    assert 'source: "碎片"' in fm
    assert 'category: "代码"' in fm


# ====================================================================
# render_note
# ====================================================================
def test_render_note_filename_and_body():
    note = mk_note(title="会议记录", content="今天讨论了 A/B 方案")
    filename, md = render_note(note)
    assert filename == "会议记录.md"
    assert md.startswith("---\n")
    assert "今天讨论了 A/B 方案" in md


def test_render_note_escapes_leading_fence():
    """正文以 --- 开头时不得破坏 frontmatter 围栏"""
    note = mk_note(content="---\ntitle: 假元数据\n---\n真实正文")
    _, md = render_note(note)
    lines = md.split("\n")
    assert lines[0] == "---"
    close = lines.index("---", 1)          # frontmatter 收尾行
    # 正文首个非空行必须已被转义，不能与收尾围栏形成「未闭合的元数据块」
    body_first = next(line for line in lines[close + 1:] if line.strip())
    assert body_first != "---", "正文首行仍是裸围栏，frontmatter 会被吞掉"
    assert body_first.startswith("\\-\\-\\-")
    assert "真实正文" in md


def test_render_note_empty_content_ok():
    filename, md = render_note(mk_note(title="空笔记", content=""))
    assert filename == "空笔记.md"
    assert md.startswith("---\n")


# ====================================================================
# render_fragment_day
# ====================================================================
def test_render_fragment_day_filename_and_dims():
    frags = [
        mk_frag(fid=1, ftype="clipboard_text", category="code",
                created_at="2026-09-27 09:15"),
        mk_frag(fid=2, ftype="file_pickup", category="path",
                created_at="2026-09-27 10:20"),
    ]
    filename, md = render_fragment_day("2026-09-27", frags)
    assert filename == "2026-09-27.md"
    # 时间维度
    assert "09:15" in md and "10:20" in md
    # 来源 type 维度
    assert TYPE_LABELS["clipboard_text"] in md
    assert TYPE_LABELS["file_pickup"] in md
    # 内容类别 category 维度
    assert CATEGORY_LABELS["code"] in md
    assert CATEGORY_LABELS["path"] in md


def test_render_fragment_day_empty():
    filename, md = render_fragment_day("2026-09-27", [])
    assert filename == "2026-09-27.md"
    assert "共 0 条" in md


# ====================================================================
# render_tasks
# ====================================================================
def test_render_tasks_filename_deadline_and_focus():
    filename, md = render_tasks([mk_task(title="写周报",
                                         deadline="2026-09-30",
                                         focus_sessions=3)])
    assert filename == TASKS_FILENAME
    assert "2026-09-30" in md
    assert "🍅×3" in md


def test_render_tasks_undone_before_done():
    tasks = [mk_task(tid=1, title="未完成甲", done=False),
             mk_task(tid=2, title="已完成乙", done=True)]
    _, md = render_tasks(tasks)
    assert md.index("## 未完成") < md.index("## 已完成")
    assert md.index("未完成甲") < md.index("已完成乙")


def test_render_tasks_empty_ok():
    _, md = render_tasks([])
    assert "共 0 条" in md
    assert "| 任务 |" not in md


def test_render_tasks_escapes_pipe_and_newline_in_note():
    _, md = render_tasks([mk_task(note="a|b\n第二行")])
    assert "a\\|b<br>第二行" in md


def test_render_tasks_no_focus_marker_when_zero():
    _, md = render_tasks([mk_task(focus_sessions=0)])
    assert "🍅" not in md


def test_render_tasks_no_deadline_shows_dash():
    _, md = render_tasks([mk_task(deadline="")])
    assert "| — |" in md


# ====================================================================
# export_all
# ====================================================================
def test_export_all_layout(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [mk_note()], [mk_frag()], [mk_task()])
    assert res.ok, res.errors
    root = vault / "FloatPulse"
    assert (root / "笔记" / "我的笔记.md").is_file()
    assert (root / "碎片" / "2026-09-27.md").is_file()
    assert (root / "任务" / TASKS_FILENAME).is_file()
    assert len(res.files_written) == 3


def test_export_all_idempotent(tmp_path):
    """同一输入导出两次：文件清单相同、内容逐字节相同"""
    vault = tmp_path / "vault"
    vault.mkdir()
    notes, frags, tasks = [mk_note()], [mk_frag()], [mk_task()]
    r1 = export_all(str(vault), notes, frags, tasks)
    snapshot = {p: open(p, "rb").read() for p in r1.files_written}
    r2 = export_all(str(vault), notes, frags, tasks)
    assert r2.files_written == r1.files_written
    for path in r2.files_written:
        with open(path, "rb") as fh:
            assert fh.read() == snapshot[path]
    assert len(r2.overwritten) == 3     # 第二次导出如实记录覆盖


def test_export_all_isolates_render_error(tmp_path):
    """单条渲染失败不中断整批，进 errors 后继续"""
    vault = tmp_path / "vault"
    vault.mkdir()

    class BoomNote:
        note_id = 99
        title = "坏笔记"
        create_time = "2026-09-27 10:00"
        update_time = "2026-09-27 10:00"

        @property
        def content(self):
            raise RuntimeError("boom")

    res = export_all(str(vault), [mk_note(title="好笔记"), BoomNote()], [], [])
    assert len(res.files_written) == 1
    assert (vault / "FloatPulse" / "笔记" / "好笔记.md").is_file()
    assert len(res.errors) == 1
    assert "坏笔记" in res.errors[0][0]


def test_export_all_empty_data_produces_no_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [], [], [])
    assert res.ok, res.errors
    assert res.files_written == []
    assert (vault / "FloatPulse" / "笔记").is_dir()    # 目录仍建好


def test_export_all_vault_is_file(tmp_path):
    target = tmp_path / "not_a_dir.txt"
    target.write_text("x", encoding="utf-8")
    res = export_all(str(target), [mk_note()], [], [])
    assert not res.ok
    assert res.files_written == []


def test_export_all_vault_missing(tmp_path):
    res = export_all(str(tmp_path / "nope"), [mk_note()], [], [])
    assert not res.ok
    assert res.errors


def test_export_all_vault_empty_string():
    res = export_all("", [mk_note()], [], [])
    assert not res.ok
    assert res.errors


def test_export_all_writes_lf_not_crlf(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [mk_note()], [mk_frag()], [mk_task()])
    for path in res.files_written:
        with open(path, "rb") as fh:
            raw = fh.read()
        assert b"\r\n" not in raw, f"{path} 出现 CRLF"
        raw.decode("utf-8")        # 必须能以 utf-8 解码


def test_export_all_utf8_roundtrip(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [mk_note(content="中文 emoji 🎯 混排")], [], [])
    with open(res.files_written[0], encoding="utf-8") as fh:
        assert "中文 emoji 🎯 混排" in fh.read()


def test_export_all_skips_temp_note(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [mk_note(title=TEMP_NOTE_TITLE)], [], [])
    assert res.files_written == []
    assert res.skipped and res.skipped[0][0] == "临时笔记"


def test_export_all_opts_disable_sections(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    res = export_all(str(vault), [mk_note()], [mk_frag()], [mk_task()],
                     opts={"export_notes": True, "export_fragments": False,
                           "export_tasks": False})
    assert len(res.files_written) == 1
    assert not (vault / "FloatPulse" / "任务" / TASKS_FILENAME).exists()
    assert not (vault / "FloatPulse" / "碎片" / "2026-09-27.md").exists()


def test_export_all_same_title_notes_not_overwritten(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    notes = [mk_note(note_id=1, title="同名", content="第一条"),
             mk_note(note_id=2, title="同名", content="第二条")]
    res = export_all(str(vault), notes, [], [])
    assert len(res.files_written) == 2
    names = {os.path.basename(p) for p in res.files_written}
    assert names == {"同名.md", "同名-2.md"}
    joined = "".join(open(p, encoding="utf-8").read()
                     for p in res.files_written)
    assert "第一条" in joined and "第二条" in joined


def test_export_all_large_fragment_not_truncated(tmp_path):
    """>100KB 的碎片完整写入，不截断"""
    vault = tmp_path / "vault"
    vault.mkdir()
    big = "中" * 120_000
    res = export_all(str(vault), [], [mk_frag(content=big)], [])
    assert res.ok, res.errors
    with open(res.files_written[0], encoding="utf-8") as fh:
        assert big in fh.read()


def test_export_all_fragments_grouped_by_day(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    frags = [mk_frag(fid=1, created_at="2026-09-26 23:10"),
             mk_frag(fid=2, created_at="2026-09-27 00:05"),
             mk_frag(fid=3, created_at="2026-09-27 08:00")]
    res = export_all(str(vault), [], frags, [])
    assert res.ok, res.errors
    names = sorted(os.path.basename(p) for p in res.files_written)
    assert names == ["2026-09-26.md", "2026-09-27.md"]


def test_export_result_summary():
    assert ExportResult(files_written=[1, 2]).summary() == "共 2 个文件"
    res = ExportResult(files_written=[1], errors=[("a", "b")])
    assert "失败 1 条" in res.summary()
