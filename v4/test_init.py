# -*- coding: utf-8 -*-
"""初始化测试：创建所有管理器实例 + 大窗口 + 悬浮球，500ms 后自动退出。
验证初始化逻辑、信号槽连接、QSS 应用无异常。"""
import sys
import os

# ★ 必须在 import PyQt6 **之前**设定离屏平台。
#   本脚本会 show() 真实主窗口与悬浮球：若走默认 windows 平台，在开发机
#   直接跑就会在桌面上真的创建出两个悬浮球窗口；而本脚本的退出阶段在部分
#   环境下会挂死（已知问题），进程不退出 → 窗口一直留在桌面上，看上去像
#   "程序没启动却多出两个球"（2026-09-29 实害，用户报障）。
#   setdefault 语义：外部已显式指定平台时（如 run_gui_check.py 注入）不覆盖。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from src.config import ConfigManager
from src.docx_manager import DocxManager
from src.task_manager import TaskManager
from src.note_manager import NoteManager
from src.fragment_manager import FragmentManager
from src.clipboard_monitor import ClipboardMonitor
from src.temp_asset_manager import TempAssetManager
from src.main_window import MainWindow
from knowledge_ball import FloatingBall


def main():
    app = QApplication(sys.argv)
    # 与主程序保持一致：数据与知识库统一用项目根的 float_data/
    from src.app_paths import get_base_dir, get_data_dir, get_docx_path
    base_dir = get_base_dir()
    data_dir = get_data_dir(base_dir)

    # ★ 测试用「真实配置的副本」，绝不读写用户的 config.json ——
    #   apply_external_theme / 窗口几何保存等路径都会 set + save 落盘，
    #   之前直接用真实配置，导致每跑一次冒烟就把用户的主题改回 light。
    import shutil
    import tempfile
    real_cfg = os.path.join(data_dir, "config.json")
    tmp_dir = tempfile.mkdtemp(prefix="fp_test_init_")
    test_cfg = os.path.join(tmp_dir, "config.json")
    if os.path.exists(real_cfg):
        shutil.copy2(real_cfg, test_cfg)
    config = ConfigManager(test_cfg)

    docx_mgr = DocxManager(
        get_docx_path(base_dir),
        os.path.join(data_dir, "docx_meta.json"),
    )
    paragraphs, err = docx_mgr.load()
    if err:
        print(f"[WARN] docx load: {err}")
        cards = []
    else:
        cards = docx_mgr.get_cards()
        print(f"[OK] docx loaded: {len(cards)} cards")

    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    print(f"[OK] tasks: {len(task_mgr.get_all_tasks())}")

    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    print(f"[OK] notes: {len(note_mgr.get_all_notes())}")

    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    print(f"[OK] fragments: {frag_mgr.count()}")

    temp_mgr = TempAssetManager(base_dir)
    print(f"[OK] temp assets: {temp_mgr.count()}")

    clip = ClipboardMonitor(frag_mgr, config)
    clip.start()
    print("[OK] clipboard monitor started")

    main_win = MainWindow(
        task_mgr, note_mgr, frag_mgr,
        docx_mgr, config, clip, temp_mgr,
    )
    main_win.show()
    print("[OK] main window shown")

    ball = FloatingBall(
        cards, task_mgr, note_mgr,
        frag_mgr, docx_mgr, config,
        clip, main_win, temp_mgr,
    )
    ball.show()
    print("[OK] floating ball shown")

    # 信号槽桥梁（与 knowledge_ball.main() 一致）
    ball._card_window.request_quit.connect(QApplication.quit)
    ball._card_window.data_changed.connect(
        lambda kind: (
            main_win.refresh_tasks() if kind == "task"
            else main_win.refresh_notes() if kind == "note"
            else None
        )
    )
    main_win.theme_changed.connect(ball.apply_theme)
    clip.fragment_added.connect(lambda _: main_win.refresh_fragments())
    main_win.data_changed.connect(
        lambda kind: ball._card_window._refresh_task_list()
        if kind == "task" and ball._card_window.isVisible()
        else None
    )
    print("[OK] signal-slot bridges connected")

    # 切换页面测试（6 个面板）
    for i in range(6):
        main_win._switch_page(i)
        print(f"[OK] switch to page {i}")

    # 主题切换测试
    main_win.apply_external_theme("dark")
    print("[OK] theme switched to dark")
    main_win.apply_external_theme("light")
    print("[OK] theme switched to light")

    # 500ms 后退出
    QTimer.singleShot(500, app.quit)
    print("[OK] init test passed, will quit in 500ms", flush=True)
    app.exec()
    print("[DONE]", flush=True)
    # ★ 硬退出兜底：本脚本在部分环境下 `app.exec()` 走完、`[DONE]` 也打了，
    #   但解释器在 Qt 收尾阶段挂住不返回（已知问题，具体阻塞点未定位）。
    #   挂死进程会一直占着窗口与热键资源 → 桌面上残留悬浮球。
    #   冒烟脚本不需要优雅收尾，直接结束进程，保证"跑完即消失"。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
