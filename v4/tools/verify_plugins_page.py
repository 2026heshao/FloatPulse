# -*- coding: utf-8 -*-
"""
插件中心页面离屏验证（tools/run_gui_check.py 驱动）。

验证点：
  A. 左栏出现「插件中心」导航按钮且可见（8 个功能页键）
  B. 9 号页可切换，页面标题与空态正确（真实 loader 无用户插件时）
  C. 注入假 loader 后卡片渲染（动作/热键/右键菜单标记/描述回退）
  D. 旧 7 键 nav_order 配置无损升级
  E. 使用说明查看器：程序内 Markdown 渲染（不依赖系统文件关联）
  F. 截图 docs/images/验证-插件中心.png

运行：python tools/run_gui_check.py tools/verify_plugins_page.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from src.app_paths import get_base_dir, get_data_dir
from src.config import ConfigManager, sanitize_nav_order
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "docs", "images")

_checkpoints = []


def check(name, cond):
    _checkpoints.append((name, bool(cond)))
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


class _FakeLoader:
    def __init__(self, plugins, plugins_dir):
        self._plugins = plugins
        self._dir = plugins_dir

    def loaded_plugins(self):
        return list(self._plugins)

    # ⚠ 面板按属性读取（loader.plugins_dir），必须是 @property——
    #   普通方法会让 setToolTip 收到 bound method 直接 TypeError
    @property
    def plugins_dir(self):
        return self._dir

    @property
    def store_dir(self):
        return self._dir


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    base = get_base_dir()
    data = get_data_dir(base)

    # ---- 配置用临时副本（绝不写用户 config.json）----
    import shutil
    tmp = tempfile.mkdtemp(prefix="fp_plugins_verify_")
    test_cfg = os.path.join(tmp, "config.json")
    if os.path.exists(os.path.join(data, "config.json")):
        shutil.copy2(os.path.join(data, "config.json"), test_cfg)
    config = ConfigManager(test_cfg)
    config.set("theme", "light")

    tm = TaskManager(os.path.join(data, "schedule.json"))
    nm = NoteManager(os.path.join(data, "notes.json"))
    fm = FragmentManager(os.path.join(data, "fragments.json"))
    am = TempAssetManager(base)
    clip = ClipboardMonitor(fm, config, am)
    win = MainWindow(tm, nm, fm, None, config, clip, am)
    win.show()
    app.processEvents()

    # ---- A. 导航按钮 ----
    check("A1. nav 按钮 8 个", len(win._nav_btns) == 8)
    btn = win._nav_btns.get("plugins")
    check("A2. 插件中心按钮存在", btn is not None)
    check("A3. 按钮文案正确",
          btn is not None and "插件中心" in btn.text())
    check("A4. 按钮可见", btn is not None and btn.isVisible())

    # ---- B. 9 号页切换与空态 ----
    win._switch_page(9)
    app.processEvents()
    check("B1. 切到 9 号页", win._stack.currentIndex() == 9)
    page = win._page_plugins
    labels = [lbl.text() for lbl in page.findChildren(QLabel)]
    check("B2. 页面标题存在", any("插件中心" in t for t in labels))
    # 真实环境 loader 未注入 → 空态
    check("B3. 未注入 loader 显示空态", page._empty_label.isVisibleTo(page))

    # ---- D. 旧 7 键配置无损升级（纯逻辑，放中间跑）----
    legacy = ["nav", "tasks", "fragments", "knowledge",
              "assets", "apps", "notes"]
    out = sanitize_nav_order(legacy)
    check("D1. 旧 7 键追加 plugins", out is not None
          and out[:7] == legacy and out[-1] == "plugins")

    # ---- C. 注入假 loader 渲染卡片 ----
    import types
    action = types.SimpleNamespace(
        id="weekly.report", title="生成周报草稿",
        hotkey="Ctrl+Alt+W", menu=True)
    plugin = type("_DemoPlugin", (), {"__doc__": "周报草稿生成器。"})()
    pdir = os.path.join(tmp, "weekly-report")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "使用说明.md"), "w", encoding="utf-8") as f:
        f.write("# 使用说明\n\n选定范围后一键汇总任务与碎片生成草稿。\n")
    manifest = {
        "id": "weekly-report", "name": "周报助手", "version": "1.0.0",
        "entry": "plugin.py", "requires": ["PyQt6"],
        "actions": [{"id": "weekly.report", "title": "生成周报草稿",
                     "hotkey": "Ctrl+Alt+W", "menu": True}],
        "description": "把本周碎片整理成周报草稿",
    }
    lp = types.SimpleNamespace(
        plugin_id="weekly-report", name="周报助手", version="1.0.0",
        path=pdir, plugin=plugin, manifest=manifest,
        actions_raw=[action])
    win.set_plugin_loader(_FakeLoader([lp], tmp))
    # 走真实触发链：切页 → _refresh_page(9) → 面板自动重建卡片
    win._switch_page(9)
    app.processEvents()
    check("C1. 空态消失", not page._empty_label.isVisibleTo(page))
    check("C2. 计数 1", "共 1 个插件" in page._count_label.text())
    page_labels = " ".join(
        lbl.text() for lbl in page.findChildren(QLabel))
    check("C3. manifest 描述优先", "把本周碎片整理成周报草稿" in page_labels)
    check("C4. 动作与热键", "生成周报草稿" in page_labels
          and "Ctrl+Alt+W" in page_labels)
    check("C5. 右键菜单标记", "右键菜单" in page_labels)
    check("C6. 使用说明摘要不上卡（详情看 md 文档）",
          "选定范围后一键汇总" not in page_labels and "📖" not in page_labels)
    check("C7. 查看使用说明按钮", any(
        "查看使用说明" in b.text()
        for b in page.findChildren(QPushButton)))

    # ---- E. 程序内 Markdown 查看器（不 exec，仅验证构建与渲染）----
    from PyQt6.QtWidgets import QTextBrowser
    from src.plugins_panel import _build_usage_viewer
    md_path = os.path.join(pdir, "使用说明.md")
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    check("E1. 说明文件可 utf-8 读出（走查看器分支的前提）",
          "选定范围后一键汇总" in md_text)
    dlg = _build_usage_viewer(win, md_path, md_text)
    try:
        viewers = dlg.findChildren(QTextBrowser)
        check("E2. 查看器可构建且含 QTextBrowser#usageViewer",
              len(viewers) == 1
              and viewers[0].objectName() == "usageViewer")
        check("E3. markdown 已程序内渲染",
              "选定范围后一键汇总" in viewers[0].toMarkdown())
        check("E4. 备选「用系统程序打开」按钮存在", any(
            "用系统程序打开" in b.text()
            for b in dlg.findChildren(QPushButton)))
    finally:
        dlg.deleteLater()

    # 截图（切到插件页）
    pix = win.grab()
    pix.save(os.path.join(OUT_DIR, "验证-插件中心.png"))

    # ---- 收尾 ----
    win.hide()
    app.processEvents()
    failed = [n for n, ok in _checkpoints if not ok]
    print(f"[DONE] {len(_checkpoints) - len(failed)}/{len(_checkpoints)} 通过")
    if failed:
        print(f"[FAIL] 未通过: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
