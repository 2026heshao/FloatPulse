# -*- coding: utf-8 -*-
"""离屏验证：设置页「小卡片图标大小」能真正改变小卡片里的应用图标。

背景（2026-10-02）：小卡片（CardWindow 第6 页软件导航）的 icon_px=36 /
btn_size=76 是硬编码，设置页的「软件卡片尺寸」只管主窗口，用户反馈
「图标大小调不动」。本脚本验证修复后：

  A. 配置三件套 + 常量一致（默认 36=历史硬编码，范围 24-56）
  B. mini_icon_size 懒读配置 / 越界钳制 / 脏值容错 / 换源重读
  C. apply_icon_size 公开门面：可见且停在 app 页 → 立刻重建；
     不可见 → 只更值不重建（切页自然生效）；同值不重复重建
  D. 真实渲染：三档尺寸下按钮 iconSize 与按钮边长都跟着变
  E. 信号链路：settings_panel._on_mini_icon_size_changed 落盘 + 广播；
     MainWindow 声明了 mini_icon_size_changed 信号
  F. 主窗口「软件卡片尺寸」链路不受影响（回归护栏）
  G. 真截图：设置页 + 小卡片 24/36/56 三档（产物走 build/shots/）

运行方式（必须 offscreen）：
  python tools/run_gui_check.py tools/verify_mini_icon_size.py
"""
import os
import sys
import time
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QBrush  # noqa: E402
from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtCore import Qt, QRectF  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.config import ConfigManager, DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES  # noqa: E402
from src.constants import (  # noqa: E402
    MINI_ICON_MIN, MINI_ICON_MAX, MINI_ICON_DEFAULT, mini_btn_size,
)
from src.card_window import CardWindow  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(BASE, "build", "shots")

FAIL = []


def check(cond, msg):
    if cond:
        print("[OK] %s" % msg)
    else:
        print("[!!] %s" % msg)
        FAIL.append(msg)


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def flush_deletes(app):
    """强制结算挂起的 deleteLater。

    ``processEvents`` 不保证派发 DeferredDelete 事件（取决于调用时机），
    网格重建后旧按钮会残留成"幽灵"，把findChildren 的计数算重。
    """
    from PyQt6.QtCore import QCoreApplication, QEvent
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def compose(pixmap):
    """把带透明通道的小卡片截图合成到模拟桌面上（半透明区否则显示成黑）。"""
    out = QPixmap(pixmap.size())
    p = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    grad.setColorAt(0.0, QColor("#E9EFF4"))
    grad.setColorAt(0.55, QColor("#DCE5EC"))
    grad.setColorAt(1.0, QColor("#D0DBE4"))
    p.fillRect(out.rect(), QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(79, 195, 192, 210))
    p.drawEllipse(QRectF(-140, -120, 560, 400))
    p.setBrush(QColor(126, 143, 224, 190))
    p.drawEllipse(QRectF(pixmap.width() - 400, -80, 560, 420))
    p.drawPixmap(0, 0, pixmap)
    p.end()
    return out


def app_buttons(cw):
    """从 _app_content 找按钮（不是 _app_grid —— layout 的 findChildren 恒空）。"""
    return [w for w in cw._app_content.findChildren(QWidget)
            if w.objectName() == "appLaunchBtn"]


def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_mini_icon_size")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    tmp = tempfile.mkdtemp(prefix="fp_verify_mini_icon_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    config = ConfigManager(os.path.join(data_dir, "config.json"))
    config.set("apps", [
        {"name": "记事本", "exe_path": r"C:\Windows\System32\notepad.exe",
         "icon_path": ""},
        {"name": "计算器", "exe_path": "", "icon_path": ""},
        {"name": "画图", "exe_path": "", "icon_path": ""},
        {"name": "命令提示符", "exe_path": "", "icon_path": ""},
        {"name": "浏览器", "exe_path": "", "icon_path": ""},
    ])
    config.save()

    # ================= A. 常量与配置三件套 =================
    print("== A. 常量与配置三件套 ==")
    check(MINI_ICON_DEFAULT == 36,
          "A1 默认 36 = 历史硬编码值（老用户视觉零变化）")
    check((MINI_ICON_MIN, MINI_ICON_MAX) == (24, 56), "A2 范围 24-56")
    check(DEFAULT_CONFIG["app_mini_icon_size"] == MINI_ICON_DEFAULT,
          "A3 DEFAULT_CONFIG 与常量一致")
    check(_CONFIG_TYPES["app_mini_icon_size"] is int, "A4 类型为 int")
    check(_CONFIG_RANGES["app_mini_icon_size"] == (MINI_ICON_MIN, MINI_ICON_MAX),
          "A5 范围表与常量一致")
    check(mini_btn_size(MINI_ICON_DEFAULT) == 76,
          "A6 默认 36 → 按钮 76（与历史硬编码一致）")
    check(mini_btn_size(0) == mini_btn_size(MINI_ICON_MIN)
          and mini_btn_size(9999) == mini_btn_size(MINI_ICON_MAX),
          "A7 越界输入按端点钳制，不外扩")

    # ================= B. mini_icon_size 懒读 =================
    print("\n== B. mini_icon_size 懒读配置 ==")
    cw = CardWindow()
    check(cw._config_manager is None and cw.mini_icon_size == MINI_ICON_DEFAULT,
          "B1 未注入 ConfigManager 时回退默认，不抛")

    cw.set_config_manager(config)
    check(cw.mini_icon_size == MINI_ICON_DEFAULT, "B2 默认读配置 36")

    # 未被 apply 显式覆盖前，property 每次都回落到配置源 → 直接改 config
    # 就能生效（恢复默认设置走的就是这条路径）
    config.set("app_mini_icon_size", 52)
    check(cw.mini_icon_size == 52, "B3 未覆盖时 property 回落到配置源新值")
    config.set("app_mini_icon_size", MINI_ICON_DEFAULT)

    cw.apply_icon_size(52)
    check(cw.mini_icon_size == 52, "B4 apply 后值已更新")

    cw.set_config_manager(ConfigManager(os.path.join(data_dir, "c2.json")))
    check(cw.mini_icon_size == MINI_ICON_DEFAULT,
          "B5 换配置源使缓存作废并重读")

    cw.set_config_manager(config)
    # 数值脏输入：钳制到端点
    for raw, want in ((0, MINI_ICON_MIN), (9999, MINI_ICON_MAX)):
        cw.apply_icon_size(raw)
        got = cw.mini_icon_size
        check(got == want,
              "B6 越界 %r → %s（期望 %s）" % (raw, got, want))
    # 非数值脏输入：**忽略**（保持上一个有效值），不得把界面打成默认值
    keep = cw.mini_icon_size
    for raw in ("abc", None, [1]):
        cw.apply_icon_size(raw)
        check(cw.mini_icon_size == keep,
              "B7 脏输入 %r 被忽略，保持 %s" % (raw, keep))
    # property 侧的脏配置（绕过 apply 直接读）→ 回退默认
    for raw in ("abc", None):
        config.set("app_mini_icon_size", raw)
        cw2 = CardWindow()
        cw2.set_config_manager(config)
        check(cw2.mini_icon_size == MINI_ICON_DEFAULT,
              "B8 脏配置 %r → property 回退默认 %s" % (raw, MINI_ICON_DEFAULT))
        cw2.deleteLater()
    config.set("app_mini_icon_size", MINI_ICON_DEFAULT)

    cw.set_config_manager(config)

    # ================= C. apply_icon_size 门面 =================
    print("\n== C. apply_icon_size 公开门面 ==")
    check(callable(getattr(CardWindow, "apply_icon_size", None)),
          "C1 apply_icon_size 是公开方法（D3 门面，非私有穿透）")

    calls = []
    real_refresh = cw._refresh_app_page

    def counting_refresh():
        calls.append(1)
        real_refresh()

    cw.switch_mode("app")
    cw.show()
    pump(app, 200)
    cw._refresh_app_page = counting_refresh

    cw.apply_icon_size(28)
    pump(app, 120)
    check(len(calls) == 1, "C2 可见且停在 app 页 → 立刻重建（%d 次）" % len(calls))
    cw.apply_icon_size(28)
    pump(app, 120)
    check(len(calls) == 1, "C3 同值重复调用不重复重建（长按 ± 友好）")

    cw.hide()
    pump(app, 100)
    before = len(calls)
    cw.apply_icon_size(50)
    pump(app, 100)
    check(len(calls) == before,
          "C4 不可见时不重建（切到 app 页自然会读到新值）")
    check(cw.mini_icon_size == 50, "C5 但值本身已更新")

    cw._refresh_app_page = real_refresh

    # ================= D. 真实渲染三档 =================
    print("\n== D. 真实渲染：三档尺寸 ==")
    rows = []
    for icon in (MINI_ICON_MIN, MINI_ICON_DEFAULT, MINI_ICON_MAX):
        cw.apply_icon_size(icon)
        cw.show()
        cw.switch_mode("app")
        cw._refresh_app_page()
        flush_deletes(app)
        pump(app, 250)
        btns = app_buttons(cw)
        check(len(btns) == 5,
              "D1 icon=%d 渲染出 %d 个按钮（期望 5，无 deleteLater 残留）"
              % (icon, len(btns)))
        if not btns:
            continue
        icon_sizes = {b.iconSize().width() for b in btns}
        btn_sizes = {b.width() for b in btns}
        check(icon_sizes == {icon},
              "D2 icon=%d → 按钮 iconSize 实测 %s" % (icon, icon_sizes))
        check(btn_sizes == {mini_btn_size(icon)},
              "D3 icon=%d → 按钮边长实测 %s（期望 {%d}）"
              % (icon, btn_sizes, mini_btn_size(icon)))
        rows.append((icon, sorted(icon_sizes)[0], sorted(btn_sizes)[0]))
        compose(cw.grab()).save(
            os.path.join(SHOT_DIR, "mini_icon_%d.png" % icon))

    check(len({r[1] for r in rows}) == len(rows) and len(rows) == 3,
          "D4 三档实测 iconSize = %s，彼此不同 → 图标真的在变"
          % [r[1] for r in rows])

    # ================= E. 设置页落盘 + 广播 =================
    print("\n== E. 设置页槽位与信号 ==")
    import src.settings_panel as sp_mod
    check(hasattr(sp_mod.SettingsPanel, "_on_mini_icon_size_changed"),
          "E1 设置页有 _on_mini_icon_size_changed 槽")

    docx = DocxManager(os.path.join(tmp, "kb.docx"),
                       os.path.join(data_dir, "docx_meta.json"))
    docx.load()
    tasks = TaskManager(os.path.join(data_dir, "schedule.json"))
    notes = NoteManager(os.path.join(data_dir, "notes.json"))
    frags = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frags, config)
    temp_mgr = TempAssetManager(tmp)

    win = MainWindow(tasks, notes, frags, docx, config, clip, temp_mgr)
    win.resize(1000, 760)
    win.show()
    sp = win._page_settings
    win._stack.setCurrentWidget(sp)
    pump(app, 300)

    check(hasattr(sp, "_set_mini_icon_size"),
          "E2 设置页外观分类有「小卡片图标大小」步进器")
    check(sp._set_mini_icon_size._min == MINI_ICON_MIN
          and sp._set_mini_icon_size._max == MINI_ICON_MAX,
          "E3 步进器范围与常量一致（%d-%d）"
          % (sp._set_mini_icon_size._min, sp._set_mini_icon_size._max))

    seen = []
    win.mini_icon_size_changed.connect(lambda v: seen.append(v))
    sp._set_mini_icon_size.setValue(44)
    pump(app, 150)
    check(config.get("app_mini_icon_size") == 44,
          "E4 步进 → 配置落盘 44（实测 %r）"
          % config.get("app_mini_icon_size"))
    check(seen == [44], "E5 步进 → 信号广播 %s" % seen)

    sp._set_mini_icon_size.setValue(999)
    pump(app, 150)
    check(config.get("app_mini_icon_size") == MINI_ICON_MAX,
          "E6 越界被钳制后落盘（%r）" % config.get("app_mini_icon_size"))

    n = len(seen)
    sp.refresh()
    pump(app, 100)
    check(len(seen) == n, "E7 refresh 不误触发广播（blockSignals 护栏）")
    check(sp._set_mini_icon_size.value() == MINI_ICON_MAX,
          "E8 refresh 从配置同步控件值")

    # ================= F. 主窗口卡片链路回归 =================
    print("\n== F. 主窗口「软件卡片尺寸」链路回归 ==")
    launcher = win._page_app_launcher
    launcher.apply_card_size(60)
    launcher.apply_card_size(140)
    flush_deletes(app)
    pump(app, 150)
    from src.widget_app_launcher import AppCardWidget
    cards = [w for w in launcher.findChildren(AppCardWidget)]
    check(bool(cards), "F1 主窗口卡片已渲染 %d 张" % len(cards))
    if cards:
        pm = cards[0]._icon_label.pixmap()
        check(pm is not None and pm.width() == 120,
              "F2 card=140 → 图标位图 120px（实测 %s）"
              % (pm.width() if pm else None))

    # ================= G. 真截图 =================
    print("\n== G. 真截图 ==")
    win._switch_page(6)
    pump(app, 500)
    win.grab().save(os.path.join(SHOT_DIR, "mini_icon_settings.png"))
    check(os.path.exists(os.path.join(SHOT_DIR, "mini_icon_settings.png")),
          "G1 设置页截图已保存")
    for icon in (MINI_ICON_MIN, MINI_ICON_DEFAULT, MINI_ICON_MAX):
        p = os.path.join(SHOT_DIR, "mini_icon_%d.png" % icon)
        check(os.path.exists(p), "G2 小卡片 %dpx 截图已保存" % icon)

    print("\n产物目录：%s" % SHOT_DIR)
    if FAIL:
        print("\n[FAIL] %d 项未通过：" % len(FAIL))
        for m in FAIL:
            print("  - %s" % m)
        return 1
    print("\n[DONE] 全部通过")
    return 0


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)