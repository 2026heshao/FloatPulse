# -*- coding: utf-8 -*-
"""离屏功能验证：设置页分组重构 + 悬浮球自动隐藏总开关。

覆盖：
  A. 分组结构：7 个语义分组 + 1 个关于卡片，标题与顺序正确
  B. 分组末行不再追加分隔线（分隔线数 = 总行数 - 分组数，消除贴边悬空线）
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

EXPECTED_GROUPS = ["🎨 外观与主题", "🔵 悬浮球", "📋 剪贴板与碎片",
                   "🖼 临时素材", "⚡ 全局工具", "🚀 启动与系统",
                   "📤 导出"]

# 各组行数（组1..组7），用于推导分隔线数量
GROUP_ROWS = [3, 7, 3, 3, 8, 4, 2]


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


def group_boxes(root):
    """取设置分组卡片，按屏幕上→下顺序返回（依赖已切到设置页并完成布局）"""
    boxes = [w for w in root.findChildren(QWidget)
             if w.objectName() == "settingsGroup"]
    boxes.sort(key=lambda w: w.mapTo(root, w.rect().topLeft()).y())
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
    n_groups = len(EXPECTED_GROUPS)
    assert len(boxes) == n_groups + 1, \
        f"A. 期望 {n_groups} 分组 + 1 关于 = {n_groups + 1} 张卡片，实际 {len(boxes)}"
    assert titles[:n_groups] == EXPECTED_GROUPS, f"A. 分组标题/顺序异常: {titles}"
    assert "关于" in titles[n_groups], f"A. 最后一张卡片应为关于，实际 {titles[n_groups]!r}"
    ok(f"A. 分组结构：{len(boxes) - 1} 组 + 关于，顺序 {' / '.join(t[:-4] for t in titles[:n_groups])}")

    # ---------------- B. 分组末行无分隔线 ----------------
    seps = [w for w in sp.findChildren(QFrame)
            if w.objectName() == "settingsSeparator"]
    expect_seps = sum(GROUP_ROWS) - len(GROUP_ROWS)   # 每组的最后一行不加线
    assert len(seps) == expect_seps, \
        f"B. 分隔线应为 {expect_seps} 条（{sum(GROUP_ROWS)} 行 - {len(GROUP_ROWS)} 组），实际 {len(seps)}"
    ok(f"B. 分隔线 {len(seps)} 条 = 总行数 {sum(GROUP_ROWS)} - 分组数 {len(GROUP_ROWS)}，分组末行无悬空线")

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
