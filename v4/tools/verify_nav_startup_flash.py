# -*- coding: utf-8 -*-
"""启动期导航栏「闪跳」离屏验证（tools/run_gui_check.py 驱动）。

背景（2026-10-12 用户报障）
--------------------------
用户报「每次启动，主窗口刚加载完成时，导航栏会闪跳一下」。

根因（逐帧实测）：插件页装配被推迟到主窗显示后 ~960ms
（2026-10-08 启动提速的代价），于是侧栏露脸时没有插件按钮，约 1s 后
一次性长出 5 行 → 整列下移 190px + 滚动条 max 0→90。实测帧序列：

    t= 27ms hash=2bdde1bd content_h=596 vbar= 0 设置_y=420 按钮= 8
    t=816ms hash=5994ba7f content_h=686 vbar=90 设置_y=610 按钮=13

修复：`show` 前按纯 manifest 预扫（~1.6ms）预留槽位，侧栏首帧即终态；
随后真页注册只走幂等分支。本脚本做**真实驱动**验证（真 MainWindow +
真 PluginLoader + 真插件目录），按真实时序跑事件循环并逐帧采样侧栏指纹。

验证点：
  A. 预扫：只读 manifest、不 import 插件模块、顺序与候选目录一致
  B. 预留：整批 1 次重排、不落盘、幂等、emoji 同源剥离
  C. 首帧即终态：预留后侧栏高度/滚动条/固定项 Y 与装载后**完全一致**
  D. 逐帧：延迟装载前后无指纹变化帧（修复前是 1 次整列跳变）
  E. 真实装载：5 个真页全部替换占位（物理索引不变）
  F. 幽灵页回收：加载失败的插件不留空页
  G. 截图 docs/images/验证-导航栏首帧稳定.png

运行：python tools/run_gui_check.py tools/verify_nav_startup_flash.py
"""
import hashlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QBuffer, QByteArray, QTimer
from PyQt6.QtWidgets import QApplication

from src.app_paths import get_base_dir, get_data_dir
from src.clipboard_monitor import ClipboardMonitor
from src.config import ConfigManager
from src.fragment_manager import FragmentManager
from src.main_window import MainWindow
from src.note_manager import NoteManager
from src.plugin_api import ActionRegistry, PluginContext, PluginData
from src.plugin_bridge import PluginHostBridge
from src.plugin_loader import MANIFEST_NAME, PluginLoader
from src.plugin_net import make_async_poster
from src.task_manager import TaskManager
from src.temp_asset_manager import TempAssetManager
from src.wiring import PluginWiring

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "docs", "images")

# 用户真实配置（float_data/config.json 实读）：插件组展开才会"整列长出来"
USER_GROUPS = ["workbench", "tools", "plugin_pages", "system"]
USER_ORDER = ["fragments", "plugins", "notes", "assets", "knowledge",
              "tasks", "apps", "nav", "plugin:ai-assistant",
              "plugin:kb-search", "plugin:recurring-tasks",
              "plugin:ai-text-workshop", "plugin:vault"]

_checkpoints = []


def check(name, cond, detail=""):
    _checkpoints.append((name, bool(cond)))
    tail = f"  [{detail}]" if detail else ""
    print(f"[{'OK' if cond else 'FAIL'}] {name}{tail}")


def fp(widget):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    widget.grab().save(buf, "PNG")
    buf.close()
    return hashlib.md5(bytes(ba)).hexdigest()[:8]


def nav_state(win):
    return {
        "content_h": win._nav_content.height(),
        "vbar": win._nav_scroll.verticalScrollBar().maximum(),
        "settings_y": win._settings_btn.y(),
        "help_y": win._help_btn.y(),
        "n": len(win._nav_btns),
        "fp": fp(win._side_bar),
    }


def make_fake_plugins(root, specs):
    """造插件目录：specs=[(id, title|None)]，入口是能真加载的最小插件。"""
    pdir = os.path.join(root, "plugins")
    os.makedirs(pdir, exist_ok=True)
    for pid, title in specs:
        d = os.path.join(pdir, pid)
        os.makedirs(d, exist_ok=True)
        man = {"id": pid, "name": pid, "version": "1.0.0",
               "entry": "plugin.py"}
        if title is not None:
            man["page"] = {"title": title}
        with open(os.path.join(d, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(man, f)
        with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
            # ★ 必须真子类化 BallPlugin：loader 的 _find_plugin_class 只认
            #   本模块内定义的 BallPlugin 子类（按导出的类名找是不行的）。
            f.write(
                "from PyQt6.QtWidgets import QWidget\n"
                "from src.plugin_api import BallPlugin\n"
                "class FakePlugin(BallPlugin):\n"
                "    id = %r\n"
                "    def create_actions(self, ctx):\n"
                "        return []\n"
                "    def create_page(self, ctx):\n"
                "        w = QWidget()\n"
                "        w.setObjectName('fakePage_%s')\n"
                "        return w\n" % (pid, pid))
    return pdir


def build_wiring(win, config, pdir, data_dir):
    registry = ActionRegistry(reserved_hotkeys=("Ctrl+Alt+S",))
    bridge = PluginHostBridge(
        fragment_manager=None, task_manager=None, note_manager=None,
        docx_manager=None, config_manager=config, ui=win, ball=None)
    ctx = PluginContext(
        logger=None, config=config.as_dict(), show_toast=win.show_toast,
        open_main_window=lambda: None, open_card_mode=lambda *a: None,
        data=PluginData(providers={}),
        data_dir_base=os.path.join(data_dir, "plugins"),
        parent_window=lambda: win,
        http_post_async=make_async_poster(),
        write_providers=bridge.write_providers(),
        manage_providers=bridge.manage_providers(),
        ai_providers=bridge.ai_providers(),
    )
    loader = PluginLoader(registry, ctx, plugins_dir=pdir, logger=None)

    class _Ball:
        def refresh_plugin_menu(self):
            pass

    class _Hotkeys:
        def unregister_all(self):
            return 0

        def register(self, *a, **k):
            return False

    return PluginWiring(loader=loader, registry=registry, ctx=ctx,
                        hotkey_mgr=_Hotkeys(), config_manager=config,
                        main_window=win, ball=_Ball()), loader


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    base = get_base_dir()
    data = get_data_dir(base)
    tmp = tempfile.mkdtemp(prefix="fp_navflash_verify_")
    pdir = make_fake_plugins(tmp, [
        ("fake-a", "甲插件"), ("fake-b", "🤖 乙插件"),
        ("fake-c", "丙插件"), ("fake-d", "丁插件"), ("fake-e", "戊插件"),
    ])

    config = ConfigManager(os.path.join(tmp, "config.json"))
    config.set("theme", "light")
    config.set("anim_speed", 2.0)
    config.set("nav_expanded_groups", USER_GROUPS)
    config.set("nav_order", USER_ORDER)

    tm = TaskManager(os.path.join(data, "schedule.json"))
    nm = NoteManager(os.path.join(data, "notes.json"))
    fm = FragmentManager(os.path.join(data, "fragments.json"))
    am = TempAssetManager(base)
    clip = ClipboardMonitor(fm, config, am)
    win = MainWindow(tm, nm, fm, None, config, clip, am)
    win.resize(1313, 738)
    wiring, loader = build_wiring(win, config, pdir, data)

    # ---- A. 预扫：只读 manifest、不 import 模块 ----
    t0 = time.perf_counter()
    specs = loader.scan_page_specs()
    t_scan = (time.perf_counter() - t0) * 1000
    check("A1. 预扫只返回声明 page 的插件（5 个）", len(specs) == 5,
          f"实际 {len(specs)}")
    check("A2. 预扫不 import 插件模块（loaded 为空）",
          loader.loaded_plugins() == [])
    check("A3. 预扫顺序 == 候选目录顺序（物理索引一致）",
          [p for p, _ in specs] == ["fake-a", "fake-b", "fake-c",
                                    "fake-d", "fake-e"])
    check(f"A4. 纯 manifest 预扫足够廉价（{t_scan:.1f}ms < 50ms）",
          t_scan < 50)

    # ---- B. 预留：1 次重排、不落盘、幂等 ----
    calls = {"relayout": 0, "save": 0}
    orig_relayout = win._relayout_nav
    orig_save = win._apply_nav_order

    def counting_relayout(*a, **k):
        calls["relayout"] += 1
        return orig_relayout(*a, **k)

    def counting_order(order, save=False):
        if save:
            calls["save"] += 1
        return orig_save(order, save=save)

    win._relayout_nav = counting_relayout
    win._apply_nav_order = counting_order
    try:
        added = win.reserve_plugin_pages([(p, t) for p, t in specs])
        check("B1. 预留建出 5 个槽位", added == 5, f"实际 {added}")
        check("B2. 整批只重排 1 次（逐个注册是 5 次）",
              calls["relayout"] == 1, f"实际 {calls['relayout']}")
        check("B3. 预留不落盘（占位顺序不入 nav_order）",
              calls["save"] == 0, f"实际 {calls['save']}")
        again = win.reserve_plugin_pages([(p, t) for p, t in specs])
        check("B4. 预留幂等（重入不重复建）", again == 0, f"实际 {again}")
        check("B5. 预留与注册同源剥 emoji",
              win.NAV_PAGE_TITLES["plugin:fake-b"] == "乙插件")
        check("B6. 插件页物理索引落在插件段（>=10）",
              min(win.NAV_PAGE_INDEX[f"plugin:{p}"] for p, _ in specs) >= 10)
    finally:
        win._relayout_nav = orig_relayout
        win._apply_nav_order = orig_save

    # ---- C/D. 逐帧：延迟装载前后必须无变化帧 ----
    win.show()
    for _ in range(5):
        app.processEvents()
    before = nav_state(win)

    def do_load():
        wiring.apply_plugins()
    QTimer.singleShot(800, do_load)
    frames = []

    def sample():
        frames.append((fp(win._side_bar), nav_state(win)))

    timer = QTimer()
    timer.setInterval(16)
    timer.timeout.connect(sample)
    timer.start()
    QTimer.singleShot(1200, app.quit)
    app.exec()
    timer.stop()

    after = nav_state(win)
    check("C1. 首帧即终态：内容高度不变",
          after["content_h"] == before["content_h"],
          f"{before['content_h']} -> {after['content_h']}")
    check("C2. 首帧即终态：滚动条范围不变",
          after["vbar"] == before["vbar"],
          f"{before['vbar']} -> {after['vbar']}")
    check("C3. 首帧即终态：「设置」Y 不变（修复前 +190px）",
          after["settings_y"] == before["settings_y"],
          f"{before['settings_y']} -> {after['settings_y']}")
    check("C4. 首帧即终态：「使用说明」Y 不变",
          after["help_y"] == before["help_y"],
          f"{before['help_y']} -> {after['help_y']}")

    distinct = len({h for h, _ in frames})
    check("D1. 装载全程侧栏无像素变化帧（闪跳消除）", distinct == 1,
          f"出现 {distinct} 种指纹帧")
    check("D2. 采样确实覆盖了装载时刻（帧数 > 20）", len(frames) > 20,
          f"实际 {len(frames)} 帧")

    # ---- E. 真页替换占位 ----
    real_pages = 0
    for spec in loader.loaded_plugins():
        key = getattr(spec, "page_key", None)
        if not key:
            continue
        idx = win.NAV_PAGE_INDEX.get(key)
        widget = win._stack.widget(idx) if idx is not None else None
        if widget is not None and str(widget.objectName()).startswith("fakePage"):
            real_pages += 1
    check("E1. 5 个真页就地替换了占位（索引不变）", real_pages == 5,
          f"实际 {real_pages}")
    check("E2. 按钮总数 = 固定 8 + 预留 5", len(win._nav_btns) == 13,
          f"实际 {len(win._nav_btns)}")

    # ---- F. 幽灵页回收 ----
    before_n = len(win._nav_btns)
    win.reserve_plugin_pages([("ghost", "幽灵")])
    dropped = win.drop_reserved_page("ghost")
    check("F1. 未装成的预留槽位可回收（不留空白幽灵页）",
          dropped is True and len(win._nav_btns) == before_n,
          f"drop={dropped} n={len(win._nav_btns)}")

    # ---- G. 截图 ----
    shot = os.path.join(OUT_DIR, "验证-导航栏首帧稳定.png")
    win._side_bar.grab().save(shot)
    check("G1. 侧栏截图落盘", os.path.exists(shot))

    win.hide()
    app.processEvents()
    failed = [n for n, ok in _checkpoints if not ok]
    print(f"[DONE] {len(_checkpoints) - len(failed)}/{len(_checkpoints)} 通过")
    if failed:
        print(f"[FAIL] 未通过: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
