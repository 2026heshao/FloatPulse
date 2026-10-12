# -*- coding: utf-8 -*-
"""文本控件中文右键菜单离屏验证（tools/run_gui_check.py 驱动）。

背景（2026-10-12 用户报障）
--------------------------
多个窗口右键弹出 Qt 自带的英文标准菜单（Undo/Redo/Cut/Copy/Paste/
Delete/Select All），不套应用主题且空控件下几乎全灰（"没有实质作用"）。
本脚本对 ``src/text_context_menu.py`` 做**真实驱动**验证：真 MainWindow
+ 真面板 + 真 QTextEdit/QLineEdit，经应用级事件过滤器真投递 ContextMenu
事件，断言菜单内容与动作分发结果。

验证点：
  A. 装配：应用级过滤器已装且真实拦截
  B. 笔记页正文（用户截图所在窗口）：菜单中文化 + 主题 QSS 非空
  C. 能力差异化：只读 → 仅复制/全选；密码回显 → 无剪切/复制
  D. 动作分发真改数据：全选 → 复制进剪贴板；粘贴入文本；删除真删字符
  E. 不抢已有菜单：CustomContextMenu 控件放行不接管
  F. 主题跟随：浅/深色菜单 QSS 不同
  G. 截图 docs/images/验证-文本右键菜单.png

运行：python tools/run_gui_check.py tools/verify_text_context_menu.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import (
    QApplication, QLineEdit, QMenu, QTextEdit, QWidget,
)

from src import text_context_menu as tcm
from src.config import ConfigManager
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow
from src.app_paths import get_base_dir, get_data_dir

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "docs", "images")

_checkpoints = []


def check(name, cond):
    _checkpoints.append((name, bool(cond)))
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


def _ctx_event():
    return QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(100, 100))


def _labels(menu):
    """菜单项文案（剥掉 & 与 \\t 快捷键后缀）。"""
    out = []
    for a in menu.actions():
        if a.isSeparator():
            out.append("<sep>")
            continue
        out.append(a.text().replace("&", "").split("\t")[0])
    return out


def _enabled(menu, label):
    for a in menu.actions():
        if a.isSeparator():
            continue
        if a.text().replace("&", "").split("\t")[0] == label:
            return a.isEnabled()
    return None


def _trigger(menu, label):
    for a in menu.actions():
        if a.isSeparator():
            continue
        if a.text().replace("&", "").split("\t")[0] == label:
            a.trigger()
            return True
    return False


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    base = get_base_dir()
    data = get_data_dir(base)

    # ---- 配置用临时副本（绝不写用户 config.json）----
    tmp = tempfile.mkdtemp(prefix="fp_textmenu_verify_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    config.set("theme", "light")

    tm = TaskManager(os.path.join(data, "schedule.json"))
    nm = NoteManager(os.path.join(data, "notes.json"))
    fm = FragmentManager(os.path.join(data, "fragments.json"))
    am = TempAssetManager(base)
    clip = ClipboardMonitor(fm, config, am)
    win = MainWindow(tm, nm, fm, None, config, clip, am)
    win.show()
    app.processEvents()

    # ---- A. 装配 ----
    filt = tcm.install_text_context_menu()
    check("A1. 应用级过滤器已装配", filt.installed)
    check("A2. 过滤器装在 QApplication 上（幂等同实例）",
          tcm.install_text_context_menu() is filt)

    # QMenu.exec 打桩：离屏下真 exec 会挂，且我们要断言菜单内容
    captured = []
    orig_exec = QMenu.exec
    QMenu.exec = lambda self, *a, **k: captured.append(self)
    try:
        # ---- B. 笔记页正文（用户截图所在窗口）----
        win._switch_page(1)
        app.processEvents()
        note_edit = win._page_notes._note_edit
        note_edit.setPlainText("第一行内容\n第二行内容")
        note_edit.selectAll()
        app.processEvents()
        for w in (note_edit, note_edit.viewport()):
            if isinstance(w, QWidget):
                w.show()
        app.processEvents()

        captured.clear()
        ev = _ctx_event()
        app.sendEvent(note_edit.viewport(), ev)
        check("B1. 笔记正文右键被接管并弹出菜单", len(captured) == 1)
        check("B2. 事件被消费（Qt 不再弹英文标准菜单）", ev.isAccepted())

        menu = captured[0]
        labels = _labels(menu)
        check("B3. 菜单中文化（撤销/重做/剪切/复制/粘贴/删除/全选）",
              labels == ["撤销", "重做", "<sep>", "剪切", "复制",
                         "粘贴", "删除", "<sep>", "全选"])
        check("B4. 无任何英文残留",
              not any(en in labels for en in
                      ("Undo", "Redo", "Cut", "Copy", "Paste",
                       "Delete", "Select All")))
        check("B5. 套用了应用菜单主题 QSS（非系统白底）",
              len(menu.styleSheet()) > 100)
        check("B6. 快捷键提示按 Qt 口径右对齐展示（\\t 后缀）",
              any(a.text().endswith("\tCtrl+Z") for a in menu.actions()
                  if not a.isSeparator()))

        # ---- D. 动作分发真改数据 ----
        QApplication.clipboard().clear()
        _trigger(menu, "复制")
        check("D1. 「复制」真把选中文本写进剪贴板",
              QApplication.clipboard().text() == "第一行内容\n第二行内容")

        QApplication.clipboard().setText("粘贴进来的文本")
        note_edit.selectAll()
        # ★ 菜单是**构建时快照**（与 Qt 标准菜单同款语义）：剪贴板内容在菜单
        #   构建后才变，必须重建菜单，否则「粘贴」仍是构建时的置灰态。
        menu = tcm.build_text_menu(note_edit)
        _trigger(menu, "粘贴")
        check("D2. 「粘贴」真把剪贴板文本写进正文",
              note_edit.toPlainText() == "粘贴进来的文本")

        note_edit.setPlainText("abcdef")
        note_edit.selectAll()
        _trigger(menu, "全选")
        check("D3. 「全选」后确有选区", note_edit.textCursor().hasSelection())
        _trigger(menu, "删除")
        check("D4. 「删除」真删掉选中内容", note_edit.toPlainText() == "")

        note_edit.setPlainText("abcdef")
        cur = note_edit.textCursor()
        cur.setPosition(1)
        note_edit.setTextCursor(cur)
        menu_d = tcm.build_text_menu(note_edit)
        _trigger(menu_d, "删除")
        check("D5. 无选区时「删除」删光标处一个字符（abcdef→acdef）",
              note_edit.toPlainText() == "acdef")
        QApplication.clipboard().clear()

        # ---- C. 能力差异化 ----
        ro = QTextEdit()
        ro.setReadOnly(True)
        ro.setPlainText("只读正文")
        ro.selectAll()
        labels_ro = _labels(tcm.build_text_menu(ro))
        check("C1. 只读控件菜单只列「复制/全选」（无撤销/剪切/粘贴/删除）",
              labels_ro == ["复制", "<sep>", "全选"])
        check("C1b. 只读控件「复制」可用（有选区）",
              _enabled(tcm.build_text_menu(ro), "复制") is True)

        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw.setText("TOPSECRET")
        pw.selectAll()
        labels_pw = _labels(tcm.build_text_menu(pw))
        check("C2. 密码回显控件**不出现**「剪切」「复制」（安全闸门）",
              "剪切" not in labels_pw and "复制" not in labels_pw)
        check("C2b. 密码框仍保留撤销/重做/粘贴/删除/全选",
              labels_pw == ["撤销", "重做", "<sep>", "粘贴", "删除",
                            "<sep>", "全选"])

        le = QLineEdit()
        le.setText("普通输入")
        le.deselect()
        labels_le = _labels(tcm.build_text_menu(le))
        check("C3. 普通输入框七项齐全",
              labels_le == ["撤销", "重做", "<sep>", "剪切", "复制",
                            "粘贴", "删除", "<sep>", "全选"])
        check("C3b. 无选区时「复制」「剪切」「删除」置灰（保留但不可用）",
              _enabled(tcm.build_text_menu(le), "复制") is False
              and _enabled(tcm.build_text_menu(le), "剪切") is False
              and _enabled(tcm.build_text_menu(le), "删除") is False)

        empty = QLineEdit()
        check("C4. 空输入框「全选」置灰（无内容可选）",
              _enabled(tcm.build_text_menu(empty), "全选") is False)

        # ---- E. 不抢已有菜单 ----
        custom = QLineEdit()
        custom.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        captured.clear()
        ev2 = _ctx_event()
        app.sendEvent(custom, ev2)
        check("E1. CustomContextMenu 控件放行（本过滤器不接管）",
              len(captured) == 0)

        nocm = QLineEdit()
        nocm.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        captured.clear()
        app.sendEvent(nocm, _ctx_event())
        check("E2. NoContextMenu 控件放行", len(captured) == 0)

        plain = QWidget()
        captured.clear()
        app.sendEvent(plain, _ctx_event())
        check("E3. 非文本控件放行（不误伤）", len(captured) == 0)

        # ---- F. 主题跟随 ----
        # ★ 必须用**挂在真实窗口下**的控件：裸控件无父链，resolve_theme 按
        #   设计回落 DEFAULT_THEME，构建两次必然同值 —— 那是断言写错，
        #   不是主题没跟随（父链跟随即是本模块的取值口径）。
        themed = QLineEdit(win)
        themed.setText("主题探针")
        light_qss = tcm.build_text_menu(themed).styleSheet()
        check("F0. 控件主题沿父链解析（跟随所在窗口）",
              tcm.resolve_theme(themed) == win._theme == "light")
        win._theme = "dark"
        dark_qss = tcm.build_text_menu(themed).styleSheet()
        check("F1. 主题跟随：深色菜单 QSS 与浅色不同",
              light_qss != dark_qss and len(dark_qss) > 100)
        win._theme = "light"

        # ---- G. 截图：真窗口 + 真菜单 ----
        menu_shot = tcm.build_text_menu(note_edit)
        menu_shot.adjustSize()
        shot_path = os.path.join(OUT_DIR, "验证-文本右键菜单.png")
        menu_shot.grab().save(shot_path)
        check("G1. 中文菜单截图落盘", os.path.exists(shot_path))
    finally:
        QMenu.exec = orig_exec

    # ---- 收尾 ----
    tcm.uninstall_text_context_menu()
    check("H1. 卸载后过滤器解除", not filt.installed)
    win.hide()
    app.processEvents()
    failed = [n for n, ok in _checkpoints if not ok]
    print(f"[DONE] {len(_checkpoints) - len(failed)}/{len(_checkpoints)} 通过")
    if failed:
        print(f"[FAIL] 未通过: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
