# -*- coding: utf-8 -*-
"""离屏功能验证：设置页分组重构 + 悬浮球自动隐藏总开关。

覆盖：
  A. 分组结构：10 个分类（含关于，2026-10-02 起番茄钟独立成页）+ 关于页内
     「软件更新」子卡，标题与导航顺序正确
  B. 每张分组卡内：分隔线数 = 行数 - 1（末行无悬空线）——逐卡动态校验，
     新增分组/行数变化不需改本脚本
  C. 自动隐藏开关默认开启；关闭 → 配置持久化 + 信号广播 + 秒数步进器灰化
  D. 重新打开 → 配置恢复 + 步进器恢复可编辑
  E. refresh() 在关闭状态下同步控件且不误触发广播（blockSignals 护栏）
  F. 恢复默认设置 → 开关回默认值、灰化解除（QMessageBox 替身驱动）
  G. 悬浮球：关闭时 _start_idle_hide_timer 永不启动；半隐藏态立即复位
  H. 配置三件套：默认 True / 类型 bool / 非法类型被拒

运行方式（必须 offscreen）：
  python tools/run_gui_check.py tools/verify_settings_groups.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QLabel, QMessageBox, QWidget,
)

from src.config import ConfigManager, DEFAULT_CONFIG  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402
from knowledge_ball import FloatingBall, get_screen_geometry  # noqa: E402

PASS = 0

# 各分类分组卡的标题原文（settings_panel._build_ui 里各 group() 调用处），
# 顺序 = 左导航 SETTINGS_CATEGORIES 页序 + 关于页内子卡。
# 与导航短名（如「外观」，UI 重构 04 起左侧配 palette 自绘图标）刻意不同：
# 卡片标题沿用历史全称，两处口径不要混改。
# 2026-10-02 UI 重构 04：分组标题去 emoji（纯文字），此处同步去字符。
EXPECTED_CARD_TITLES = [
    # 2026-10-03 主题扩展：「主题配色」「背景图」两张卡加在外观分类页里
    "外观与主题", "主题配色", "背景图", "悬浮球", "剪贴板与碎片", "临时素材",
    "全局工具", "番茄钟", "启动与系统", "导出", "AI 总配置",
    "软件更新",   # 关于分类页内的子卡（手动检查更新），排在「关于」卡上方
    "关于",
]


def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def group_boxes(sp):
    """按分类栈页序收集分组卡（每页内再按 y 排序）。

    2026-09-29 起设置页是「左导航 + 分类 QStackedWidget」：不同页的卡片
    在各自页内布局，跨页 mapTo 的 y 排序没有意义，必须按 _cat_stack 页序
    展开才能得到与导航一致的卡片顺序。
    """
    boxes = []
    stack = sp._cat_stack
    for i in range(stack.count()):
        page = stack.widget(i)
        page_boxes = [w for w in page.findChildren(QWidget)
                      if w.objectName() == "settingsGroup"]
        page_boxes.sort(key=lambda w: w.mapTo(page, w.rect().topLeft()).y())
        boxes.extend(page_boxes)
    return boxes


def section_title(box):
    for lab in box.findChildren(QLabel):
        if lab.objectName() == "sectionLabel":
            return lab.text()
    return ""


class _FakeMsgBox:
    """QMessageBox 替身：自动确认，避免离屏脚本被模态框卡死"""
    StandardButton = QMessageBox.StandardButton

    @staticmethod
    def question(*_a, **_k):
        return QMessageBox.StandardButton.Yes

    @staticmethod
    def information(*_a, **_k):
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def warning(*_a, **_k):
        return QMessageBox.StandardButton.Ok


def main():
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_settings_groups")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_settings_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config = ConfigManager(os.path.join(data_dir, "config.json"))

    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)

    win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr,
                     config, clip, temp_mgr)
    win.resize(1000, 760)
    win.show()

    sp = win._page_settings
    win._stack.setCurrentWidget(sp)     # 隐藏页不参与布局，必须先切过来
    pump(app, 200)

    # ---------------- A. 分组结构 ----------------
    boxes = group_boxes(sp)
    titles = [section_title(b) for b in boxes]
    n_expect = len(EXPECTED_CARD_TITLES)
    assert len(boxes) == n_expect, \
        f"A. 期望 {n_expect} 张分组卡，实际 {len(boxes)}: {titles}"
    assert titles == EXPECTED_CARD_TITLES, \
        f"A. 分类卡标题/顺序异常: {titles}"
    ok(f"A. 分组结构：{n_expect} 张卡，顺序与导航一致（含软件更新子卡）")

    # ---------------- B. 每张卡内末行无分隔线（逐卡动态校验） ----------------
    for box, title in zip(boxes, titles):
        rows = len([lab for lab in box.findChildren(QLabel)
                    if lab.objectName() == "settingTitle"])
        seps = len([f for f in box.findChildren(QFrame)
                    if f.objectName() == "settingsSeparator"])
        # 「关于」这类展示卡不走 _add_row（无 settingTitle），只要不悬空线即可
        if rows == 0:
            assert seps == 0, f"B. 卡「{title}」无设置行却有 {seps} 条分隔线"
            continue
        assert seps == rows - 1, \
            f"B. 卡「{title}」{rows} 行应有 {rows - 1} 条分隔线，实际 {seps}（末行悬空线？）"
    ok(f"B. {len(boxes)} 张卡逐卡校验：分隔线数 = 行数 - 1，末行无悬空线")

    # ---------------- C. 关掉自动隐藏 ----------------
    assert hasattr(sp, "_set_auto_hide_enabled"), "C. 设置页缺少自动隐藏开关"
    assert sp._set_auto_hide_enabled.isChecked() is True, "C. 默认应为开启"
    assert sp._set_auto_hide.isEnabled() is True, "C. 默认秒数步进器应可编辑"

    seen = []
    win.auto_hide_enabled_changed.connect(lambda v: seen.append(v))
    sp._set_auto_hide_enabled.setChecked(False)     # 真实交互路径
    pump(app, 150)
    assert config.get("auto_hide_enabled") is False, \
        f"C. 配置未持久化: {config.get('auto_hide_enabled')}"
    assert os.path.exists(config._json_path), "C. 配置未写盘"
    assert seen == [False], f"C. 信号广播异常: {seen}"
    assert sp._set_auto_hide.isEnabled() is False, "C. 秒数步进器未灰化"
    ok("C. 关闭自动隐藏：配置落盘 + 广播 False + 秒数步进器灰化")

    # ---------------- D. 重新打开 ----------------
    sp._set_auto_hide_enabled.setChecked(True)
    pump(app, 150)
    assert config.get("auto_hide_enabled") is True, "D. 重新打开未持久化"
    assert seen == [False, True], f"D. 信号序列异常: {seen}"
    assert sp._set_auto_hide.isEnabled() is True, "D. 步进器未恢复可编辑"
    ok("D. 重新打开：配置恢复 + 广播 True + 步进器恢复可编辑")

    # ---------------- E. refresh 同步且不误广播 ----------------
    config.set("auto_hide_enabled", False)
    config.save()
    n_before = len(seen)
    sp.refresh()
    assert sp._set_auto_hide_enabled.isChecked() is False, "E. refresh 未同步开关值"
    assert sp._set_auto_hide.isEnabled() is False, "E. refresh 未同步灰化态"
    assert len(seen) == n_before, f"E. refresh 误触发广播: {seen}"
    ok("E. refresh：同步开关与灰化态，且不误触发广播（blockSignals 护栏有效）")

    # ---------------- F. 恢复默认设置 ----------------
    import src.settings_panel as sp_mod
    sp_mod.QMessageBox = _FakeMsgBox
    sp._on_reset_settings()
    pump(app, 200)
    assert config.get("auto_hide_enabled") is True, "F. 恢复默认后开关未回默认"
    assert sp._set_auto_hide_enabled.isChecked() is True, "F. 开关控件未刷新"
    assert sp._set_auto_hide.isEnabled() is True, "F. 灰化态未解除"
    assert seen[-1] is True, f"F. 恢复默认未广播: {seen}"
    ok("F. 恢复默认：开关回默认值、灰化解除、广播联动（含 auto_hide_enabled_changed）")

    # ---------------- G. 悬浮球侧行为 ----------------
    ball = FloatingBall([], task_manager=task_mgr, config_manager=config)
    screen = get_screen_geometry()

    ball.set_auto_hide_enabled(True)
    ball._normal_pos = ball.pos()
    ball.move(screen.left(), ball.y())
    assert ball._is_near_edge() is True, "G. 前置条件不成立：球应判定为贴边"
    ball._start_idle_hide_timer()
    assert ball._idle_hide_timer.isActive() is True, "G. 开启时贴边应启动计时"

    ball._hidden_to_edge = True          # 模拟已半隐藏
    ball._edge_side = "left"
    ball.set_auto_hide_enabled(False)
    assert ball._auto_hide_enabled is False, "G. 开关状态未落到球上"
    assert ball._idle_hide_timer.isActive() is False, "G. 关闭后定时器未停止"
    assert ball._hidden_to_edge is False, "G. 半隐藏态未立即复位（球会卡在屏外）"

    ball.move(screen.left(), ball.y())
    ball._start_idle_hide_timer()
    assert ball._idle_hide_timer.isActive() is False, "G. 关闭后不得再启动计时"
    ok("G. 悬浮球：开启时贴边计时 → 关闭即停表 + 半隐藏复位 + 不再启动")

    # ---------------- H. 配置三件套 ----------------
    assert DEFAULT_CONFIG["auto_hide_enabled"] is True, "H. 默认值应为 True"
    assert config.set("auto_hide_enabled", "yes") is False, "H. 字符串应被拒"
    assert config.set("auto_hide_enabled", 1) is False, "H. int 1 不是 bool，应被拒"
    assert config.set("auto_hide_enabled", False) is True, "H. 合法 bool 应被接受"
    config.set("auto_hide_enabled", True)
    ok("H. 配置校验：默认 True / 仅接受 bool，非法类型均被拒")

    print(f"[DONE] {PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
