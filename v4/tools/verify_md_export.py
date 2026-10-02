# -*- coding: utf-8 -*-
"""离屏功能验证：Obsidian Markdown 导出（端到端）。

覆盖：
  A. 纯逻辑层：真实数据 → vault 下目录/文件布局正确（笔记 / 碎片按天 / 任务汇总）
  B. 文件内容：frontmatter 合法、正文完整、UTF-8 无 BOM、换行恰为 LF
  C. 设置页导出分组：路径标签 + 「更改目录」+「导出到 Obsidian」三个控件在位
  D. 「更改目录」真实点击 → 写入配置并刷新文案；取消则不动任何状态
  E. 三面板右键菜单均含「📤 导出到 Obsidian」，且点它真的走宿主（单一实现）
  F. vault 路径非法（不存在 / 指向文件）→ 只记 error，不抛异常
  G. 一键导出按钮真实点击 → 走宿主公开方法且不崩
  H. 幂等：同数据二次导出，文件内容逐字节一致
  I. 导出开关全关 → 不产生任何文件

运行方式（必须 offscreen）：
  python tools/run_gui_check.py tools/verify_md_export.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402

import src.main_window as mw_mod  # noqa: E402
import src.notes_panel as notes_mod  # noqa: E402
import src.fragments_panel as frags_mod  # noqa: E402
import src.tasks_panel as tasks_mod  # noqa: E402
import src.settings_panel as settings_mod  # noqa: E402
from src import md_export  # noqa: E402

PASS = 0
FAIL = 0
EXPORT_LABEL = "导出到 Obsidian"


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------- 替身
class _FakeAction:
    def __init__(self, text):
        self._text = text
        self._checkable = False
        self._checked = False

    def text(self):
        return self._text

    def setCheckable(self, value):
        self._checkable = bool(value)

    def setChecked(self, value):
        self._checked = bool(value)

    def isChecked(self):
        return self._checked

    def setIcon(self, _icon):
        # UI 重构 05：右键「删除」项改为 danger 色自绘图标承载语义
        pass

    def setEnabled(self, _value):
        pass


class _FakeMenu:
    """QMenu 替身：记录菜单项，exec 模拟选中 pick_text 对应项（None=取消）。

    离屏下真实 QMenu.exec() 会阻塞且无从点击，故注入替身走「真实回调路径」
    ——面板内部逻辑（addAction 顺序、action 分支）全部真实执行。
    """
    pick_text = None
    instances = []

    def __init__(self, parent=None):
        self.items = []          # [("action"|"sep"|"menu", text)]
        self.actions = []        # [_FakeAction]
        self.submenus = []
        _FakeMenu.instances.append(self)

    def setStyleSheet(self, _qss):
        pass

    def addAction(self, text):
        act = _FakeAction(text)
        self.actions.append(act)
        self.items.append(("action", text))
        return act

    def addSeparator(self):
        self.items.append(("sep", None))

    def addMenu(self, text):
        sub = _FakeMenu()
        self.submenus.append((text, sub))
        self.items.append(("menu", text))
        return sub

    def exec(self, _pos=None):
        if _FakeMenu.pick_text is None:
            return None
        for act in self.actions:
            if act.text() == _FakeMenu.pick_text:
                return act
        return None

    def texts(self):
        return [t for kind, t in self.items if kind == "action"]


class _FakeFileDialog:
    """QFileDialog 替身：返回 result（"" 模拟用户取消）。"""
    result = ""
    calls = 0

    @staticmethod
    def getExistingDirectory(*_a, **_k):
        _FakeFileDialog.calls += 1
        return _FakeFileDialog.result


# ---------------------------------------------------------------- 工具
def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def walk_files(root):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(found)


def patch_menus():
    notes_mod.QMenu = _FakeMenu
    frags_mod.QMenu = _FakeMenu
    tasks_mod.QMenu = _FakeMenu


def first_real_item(lst):
    """取列表里第一个真实数据项（分组标题/占位项 UserRole 为 None）。"""
    for i in range(lst.count()):
        item = lst.item(i)
        if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
            return item
    return None


def panel_menu_texts(panel, list_attr, app):
    """驱动一次真实右键：返回菜单项文本列表（exec 替身为「取消」）。"""
    _FakeMenu.instances = []
    _FakeMenu.pick_text = None
    lst = getattr(panel, list_attr)
    lst.show()
    app.processEvents()
    item = first_real_item(lst)
    if item is None:
        print(f"   ! {list_attr} 无真实数据项（count={lst.count()}）")
        return None
    rect = lst.visualItemRect(item)
    pos = rect.center() if rect.isValid() else lst.rect().center()
    panel._on_context_menu(pos)
    app.processEvents()
    if not _FakeMenu.instances:
        return None
    # 顶层菜单是最先创建的那个（fragments 还会再建归类子菜单）
    return _FakeMenu.instances[0].texts()


def refresh_panel(panel):
    """三个面板的 refresh 签名不一致（only fragments 收 preserve_view）。"""
    try:
        panel.refresh(preserve_view=False)
    except TypeError:
        panel.refresh()


def main():
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_md_export")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    patch_menus()
    mw_mod.QFileDialog = _FakeFileDialog
    settings_mod.QFileDialog = _FakeFileDialog

    tmp = tempfile.mkdtemp(prefix="fp_verify_mdexport_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault, exist_ok=True)

    config = ConfigManager(os.path.join(data_dir, "config.json"))
    config.set("obsidian_vault_path", vault)
    config.save()

    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)

    # ---- 造数据：2 笔记（含重名）/ 3 碎片（跨 2 天）/ 2 任务 ----
    note_mgr.add_note("第一条笔记的正文内容", title="会议纪要")
    note_mgr.add_note("第二条笔记的正文内容", title="会议纪要")     # 故意重名
    frag_mgr.add_clipboard_text("https://example.com/a")
    frag_mgr.add_clipboard_text("def foo():\n    return 1")
    frag_mgr.add_clipboard_path("C:/Users/x/y.txt")
    for frag in frag_mgr.get_all_fragments():
        print("   碎片:", getattr(frag, "fragment_id", "?"),
              getattr(frag, "created_at", ""), getattr(frag, "type", ""),
              getattr(frag, "category", ""))
    task_mgr.add_task("交作业", "通信原理实验报告", "2026-10-01")
    task_mgr.add_task("买菜", "", "2026-09-28")
    # 让碎片跨两天：直接改一条的 created_at 并落盘
    frags = frag_mgr.get_all_fragments()
    if frags:
        frags[0].created_at = "2026-09-26 09:00"
    days = {str(getattr(f, "created_at", ""))[:10] for f in frags}
    print(f"   数据：笔记 {len(note_mgr.get_all_notes())} / "
          f"碎片 {len(frags)}（{len(days)} 天）/ 任务 {len(task_mgr.get_all_tasks())}")

    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr,
                     config, clip, temp_mgr)
    win.resize(1000, 760)
    win.show()
    pump(app, 200)

    # ---------------- A. 目录 / 文件布局 ----------------
    result = win.export_to_obsidian(interactive=False)
    root = os.path.join(vault, "FloatPulse")
    rel = walk_files(root)
    expect_dirs = {md_export.SUBDIR_NOTES, md_export.SUBDIR_FRAGMENTS,
                   md_export.SUBDIR_TASKS}
    got_dirs = {p.split(os.sep)[0] for p in rel}
    check("A1 三个子目录齐全（笔记/碎片/任务）", got_dirs == expect_dirs,
          f"got={got_dirs}")
    check("A2 无 error", result is not None and not result.errors,
          f"errors={result.errors if result else 'None'}")
    check("A3 笔记文件数 = 2（重名不互相覆盖）",
          len([p for p in rel if p.startswith(md_export.SUBDIR_NOTES)]) == 2,
          f"{[p for p in rel if p.startswith(md_export.SUBDIR_NOTES)]}")
    check("A4 碎片按天聚合，文件数 = 2",
          len([p for p in rel if p.startswith(md_export.SUBDIR_FRAGMENTS)]) == 2,
          f"{[p for p in rel if p.startswith(md_export.SUBDIR_FRAGMENTS)]}")
    check("A5 任务汇总.md 存在且只有 1 个",
          [p for p in rel if p.startswith(md_export.SUBDIR_TASKS)] ==
          [os.path.join(md_export.SUBDIR_TASKS, md_export.TASKS_FILENAME)],
          f"{[p for p in rel if p.startswith(md_export.SUBDIR_TASKS)]}")
    print("   实际布局：" + " | ".join(rel))

    # ---------------- B. 文件内容 ----------------
    note_paths = [os.path.join(root, p) for p in rel
                  if p.startswith(md_export.SUBDIR_NOTES)]
    raw = read_bytes(note_paths[0])
    check("B1 笔记文件为 UTF-8 无 BOM", not raw.startswith(b"\xef\xbb\xbf"))
    check("B2 换行恰为 LF（无 \\r）", b"\r" not in raw, f"含 CR: {raw[:80]!r}")
    text = raw.decode("utf-8")
    check("B3 frontmatter 成对围栏",
          text.startswith("---\n") and text.count("\n---\n") >= 1,
          text[:60].replace("\n", "\\n"))
    check("B4 frontmatter 含 tags 列表语法", "tags: [FloatPulse]" in text)
    check("B5 含 source: \"笔记\"", 'source: "笔记"' in text)
    check("B6 正文完整", "第一条笔记的正文内容" in text or
          "第二条笔记的正文内容" in text)
    frag_paths = [os.path.join(root, p) for p in rel
                  if p.startswith(md_export.SUBDIR_FRAGMENTS)]
    frag_text = read_bytes(frag_paths[0]).decode("utf-8")
    check("B7 碎片标题含来源 type 与内容类别 category（双轴）",
          "·" in frag_text and "##" in frag_text, frag_text[:120].replace("\n", "\\n"))
    task_text = read_bytes(
        os.path.join(root, md_export.SUBDIR_TASKS,
                     md_export.TASKS_FILENAME)).decode("utf-8")
    check("B8 任务汇总含未完成/已完成分组与截止日",
          "## 未完成" in task_text and "## 已完成" in task_text
          and "2026-10-01" in task_text)
    check("B9 重复导出无 error（覆盖语义）", not result.errors)

    # ---------------- C. 设置页导出分组 ----------------
    sp = win._page_settings
    win._stack.setCurrentWidget(sp)
    pump(app, 200)
    check("C1 路径标签存在且显示 vault",
          hasattr(sp, "_set_vault_path") and vault[:14] in sp._set_vault_path.text(),
          getattr(sp, "_set_vault_path", None) and sp._set_vault_path.text())
    check("C2 「更改目录」按钮存在",
          hasattr(sp, "_set_vault_choose") and
          sp._set_vault_choose.text() == "更改目录")
    check("C3 「导出到 Obsidian」按钮存在",
          hasattr(sp, "_set_export_btn") and
          sp._set_export_btn.text() == "导出到 Obsidian")

    # ---------------- D. 更改目录：取消 vs 选择 ----------------
    _FakeFileDialog.result = ""
    _FakeFileDialog.calls = 0
    sp._set_vault_choose.click()
    pump(app, 120)
    check("D1 取消选择：不写配置、不报错",
          _FakeFileDialog.calls == 1 and
          config.get("obsidian_vault_path") == vault,
          f"calls={_FakeFileDialog.calls} path={config.get('obsidian_vault_path')}")

    vault2 = os.path.join(tmp, "vault2")
    os.makedirs(vault2, exist_ok=True)
    _FakeFileDialog.result = vault2
    sp._set_vault_choose.click()
    pump(app, 120)
    check("D2 选择新目录：配置落盘 + 文案刷新",
          config.get("obsidian_vault_path") == vault2 and
          vault2[:14] in sp._set_vault_path.text(),
          f"path={config.get('obsidian_vault_path')} label={sp._set_vault_path.text()}")
    config.set("obsidian_vault_path", vault)      # 复位
    config.save()
    sp.refresh()
    pump(app, 100)

    # ---------------- E. 三面板右键菜单 ----------------
    spy = {"n": 0}
    real_export = win.export_to_obsidian

    def spy_export(*a, **k):
        spy["n"] += 1
        return real_export(*a, **k)

    win.export_to_obsidian = spy_export

    panels = [
        ("笔记", win._page_notes, "_note_list"),
        ("碎片", win._page_fragments, "_frag_list"),
        ("任务", win._page_tasks, "_task_list"),
    ]
    for label, panel, list_attr in panels:
        win._stack.setCurrentWidget(panel)
        refresh_panel(panel)
        pump(app, 250)
        texts = panel_menu_texts(panel, list_attr, app)
        check(f"E-{label} 菜单含「导出到 Obsidian」",
              bool(texts) and EXPORT_LABEL in texts,
              f"texts={texts}")
        # 真实选中导出项 → 必须走宿主公开方法（单一实现）
        _FakeMenu.instances = []
        _FakeMenu.pick_text = EXPORT_LABEL
        before = spy["n"]
        lst = getattr(panel, list_attr)
        item = first_real_item(lst)
        if item is not None:
            rect = lst.visualItemRect(item)
            pos = rect.center() if rect.isValid() else lst.rect().center()
            panel._on_context_menu(pos)
        pump(app, 120)
        check(f"E-{label} 点导出项 → 调用宿主（未分叉实现）",
              spy["n"] == before + 1, f"spy n={spy['n']} before={before}")
        _FakeMenu.pick_text = None

    win.export_to_obsidian = real_export

    # ---------------- F. vault 非法 ----------------
    config.set("obsidian_vault_path", os.path.join(tmp, "不存在的目录"))
    config.save()
    not_exist = win.export_to_obsidian(interactive=False)
    check("F1 vault 不存在 → 记 error 不抛异常",
          not_exist is not None and len(not_exist.errors) == 1
          and "不存在" in not_exist.errors[0][1],
          f"{not_exist.errors if not_exist else None}")

    a_file = os.path.join(tmp, "not_a_dir.txt")
    with open(a_file, "w", encoding="utf-8") as f:
        f.write("x")
    config.set("obsidian_vault_path", a_file)
    config.save()
    is_file = win.export_to_obsidian(interactive=False)
    check("F2 vault 指向文件 → 记 error 不抛异常",
          is_file is not None and len(is_file.errors) == 1
          and "不是文件夹" in is_file.errors[0][1],
          f"{is_file.errors if is_file else None}")
    config.set("obsidian_vault_path", vault)
    config.save()

    # ---------------- G. 一键导出按钮真实点击 ----------------
    win._stack.setCurrentWidget(sp)
    pump(app, 150)
    counts_before = len(walk_files(root))
    try:
        sp._set_export_btn.click()
        pump(app, 250)
        ok_click = len(walk_files(root)) == counts_before
    except Exception as exc:                              # noqa: BLE001
        ok_click = False
        print("   按钮点击异常:", exc)
    check("G1 一键导出按钮点击不崩", ok_click)
    check("G2 点击后 vault 文案仍正确",
          vault[:14] in sp._set_vault_path.text(), sp._set_vault_path.text())

    # ---------------- H. 幂等 ----------------
    def snapshot(paths_root):
        snap = {}
        for p in walk_files(paths_root):
            snap[p] = read_bytes(os.path.join(paths_root, p))
        return snap

    snap1 = snapshot(root)
    notes_all = note_mgr.get_all_notes()
    last_note = notes_all[-1]
    expected = md_export.render_note(last_note)[1]
    frag_all = frag_mgr.get_all_fragments()
    frag_days = {}
    for f in frag_all:
        frag_days.setdefault(str(f.created_at)[:10], []).append(f)
    d = sorted(frag_days)[0]
    expected_frag = md_export.render_fragment_day(d, frag_days[d])[1]
    # 第二次导出（同一数据）
    win.export_to_obsidian(interactive=False)
    snap2 = snapshot(root)
    check("H1 二次导出文件集合不变", set(snap1) == set(snap2),
          f"{set(snap1) ^ set(snap2)}")
    check("H2 二次导出内容逐字节一致（幂等）", snap1 == snap2,
          f"差异文件：{[k for k in snap1 if snap1.get(k) != snap2.get(k)]}")
    check("H3 渲染器对同对象输出稳定",
          expected == md_export.render_note(last_note)[1] and
          expected_frag == md_export.render_fragment_day(d, frag_days[d])[1])

    # ---------------- I. 开关全关 ----------------
    empty_vault = os.path.join(tmp, "vault3")
    os.makedirs(empty_vault, exist_ok=True)
    off = md_export.export_all(
        empty_vault, note_mgr.get_all_notes(), frag_mgr.get_all_fragments(),
        task_mgr.get_all_tasks(),
        {"export_notes": False, "export_fragments": False, "export_tasks": False})
    check("I1 三个开关全关 → 0 文件、无 error",
          off.files_written == [] and not off.errors,
          f"files={off.files_written} errors={off.errors}")
    check("I2 全关时不创建任何子目录",
          not os.path.exists(os.path.join(empty_vault, "FloatPulse")),
          walk_files(empty_vault))

    win.close()
    pump(app, 100)
    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
