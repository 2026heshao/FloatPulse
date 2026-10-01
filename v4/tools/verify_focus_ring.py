# -*- coding: utf-8 -*-
"""键盘焦点态（UI 强化方案 A4）离屏验证 + 真实截图。

为什么需要它（pytest 覆盖不到的部分）
====================================================================
``tests/test_keyboard_focus.py`` 能钉住「QSS 写了什么」和「单控件渲不渲染」，
但钉不住三件事，必须在真窗口上验：

  1. **真实 MainWindow** 里焦点环会不会被别的规则盖掉（QSS 一致性问题）；
  2. **真按 Tab 能不能走到**这些控件（focusPolicy / tab order 问题）——
     很多「补了焦点态」的改动实际是无用的，因为控件根本进不了 Tab 链；
  3. 卡片窗口那份**独立 QSS**（`get_card_window_qss`）同样生效。

顺带产出 light/dark 两套截图供人工核对（A4 的验收物）。

用法：
    python tools/run_gui_check.py tools/verify_focus_ring.py
产物：
    build/shots/focus-<theme>-*.png
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt                 # noqa: E402
from PyQt6.QtGui import QColor, QFontDatabase, QKeyEvent            # noqa: E402
from PyQt6.QtWidgets import (                                       # noqa: E402
    QApplication, QCheckBox, QHBoxLayout, QPushButton, QWidget,
)

from src.config import ConfigManager                                # noqa: E402
from src.controls import Stepper, ToggleSwitch                      # noqa: E402
from src.docx_manager import DocxManager                            # noqa: E402
from src.fragment_manager import FragmentManager                     # noqa: E402
from src.nav_manager import NavManager                              # noqa: E402
from src.note_manager import NoteManager                            # noqa: E402
from src.task_manager import TaskManager                            # noqa: E402
from src.temp_asset_manager import TempAssetManager                 # noqa: E402
from src.theme import THEMES, get_card_window_qss, get_main_window_qss  # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "build", "shots")

# 环色像素判据：逐通道容差。取 24 而不是更宽 —— $focus_ring 与同族
# $secondary_text 同值，容差放太宽会把「文字色」误判成「环」。
TOL = 24

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, ms=400):
    from PyQt6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        app.processEvents()


def ring_count(img, rect: QRect, color: str, tol: int = TOL) -> int:
    """数 rect 内的「环色」像素"""
    want = QColor(color)
    n = 0
    x0 = max(0, rect.left())
    y0 = max(0, rect.top())
    x1 = min(img.width(), rect.right() + 1)
    y1 = min(img.height(), rect.bottom() + 1)
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            if (abs(c.red() - want.red()) < tol
                    and abs(c.green() - want.green()) < tol
                    and abs(c.blue() - want.blue()) < tol):
                n += 1
    return n


# ★ 判据必须是「只扫边框带」，不能扫整块控件（2026-09-30 实测踩坑）：
#   环色是**刻意**复用现有语义色的 —— 主色实底按钮用 $on_primary（= 它的文字色）、
#   危险按钮用 $danger（= 它的文字色）、深色主题的 $focus_ring 就等于 $primary
#   （= 主色强调文字/选中项）。整块扫描会把文字/强调色一起算成"环"，
#   于是「失焦后残留几百像素」这种假失败必然出现，且深色主题必然全红。
#   只扫四条边的 band 像素则天然把内部文字排除在外。
BAND = 3


def _bands(rect: QRect, band: int = BAND):
    return [
        QRect(rect.left(), rect.top(), rect.width(), band),                     # 上
        QRect(rect.left(), rect.bottom() - band + 1, rect.width(), band),       # 下
        QRect(rect.left(), rect.top(), band, rect.height()),                    # 左
        QRect(rect.right() - band + 1, rect.top(), band, rect.height()),        # 右
    ]


def ring_count_band(img, rect: QRect, color: str, tol: int = TOL) -> int:
    """只数控件四条边框带内的环色像素（排除内部文字/强调色）"""
    return sum(ring_count(img, b, color, tol) for b in _bands(rect))


def ring_count_indicator(img, host, widget, color: str, tol: int = TOL) -> int:
    """只数 QCheckBox 左侧 indicator 所在的窄条（宽 20px）。

    不用 ``QStyle.subElementRect(SE_CheckBoxIndicator)``：QSS 覆盖了
    indicator 的 width/height 之后，它给出的位置与实际绘制位置可能差几个
    像素 —— 环宽只有 1px，一旦错过就是「明明画出来了却扫不到」的假失败。
    20px 足够盖住 16px 的 indicator，又短于「spacing 6px + 文字」的起点
    （文字从 x≈22 开始），因此不会把文字算进来。
    """
    rect = widget_rect_in(host, widget)
    return ring_count(img, QRect(rect.left(), rect.top(), 20, rect.height()),
                      color, tol)


def scan_ring(img, host, widget, color: str, tol: int = TOL) -> int:
    """按控件上声明的 ringScan 属性选扫描策略（默认 band）"""
    mode = widget.property("ringScan") or "band"
    if mode == "indicator":
        return ring_count_indicator(img, host, widget, color, tol)
    return ring_count_band(img, widget_rect_in(host, widget), color, tol)


def widget_rect_in(win, widget) -> QRect:
    return QRect(widget.mapTo(win, QPoint(0, 0)), widget.size())


def send_tab(app, target):
    """发一次 Tab（用 QKeyEvent + sendEvent，不用 QTest.keyClick）"""
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                   Qt.KeyboardModifier.NoModifier)
    app.sendEvent(target, ev)
    app.processEvents()


# ====================================================================
# A. 真实 MainWindow：焦点环 + 真 Tab 走查
# ====================================================================
def build_real_window(tmp):
    from src.main_window import MainWindow

    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx_path = os.path.join(tmp, "知识库.docx")
    docx = DocxManager(docx_path, os.path.join(tmp, "docx_meta.json"))
    docx.load()

    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    frags.add_clipboard_text("焦点态走查用的一条碎片：先跑通流程，再谈优化")
    frags.add_clipboard_path(r"D:\work\report\2026-09 月报汇总.pptx")

    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(1280, 740)
    return win


def section_main_window(app, theme):
    print(f"== A.{theme} 真实 MainWindow：焦点环 + Tab 走查 ==")
    tmp = tempfile.mkdtemp(prefix="fp_verify_focus_")
    win = build_real_window(tmp)
    ring = THEMES[theme]["focus_ring"]

    win._theme = theme
    win._config.set("theme", theme)
    win._apply_theme()
    win.show()
    win._switch_page(0)
    pump(app, 1400)

    # --- 基线：全体失焦，任何一个目标控件都不该有环 ---
    win.setFocus()
    win.clearFocus()
    pump(app, 300)
    base = win.grab().toImage()
    os.makedirs(OUT_DIR, exist_ok=True)
    base_png = os.path.join(OUT_DIR, f"focus-{theme}-00-none.png")
    base.save(base_png)

    nav_key = next(iter(win._nav_btns))
    nav_btn = win._nav_btns[nav_key]
    search = win._page_fragments._frag_search
    frag_list = win._page_fragments._frag_list

    targets = [
        ("navbtn", nav_btn),
        ("search", search),
        ("list", frag_list),
    ]
    for tag, w in targets:
        w.clearFocus()
    pump(app, 200)
    base = win.grab().toImage()
    for tag, w in targets:
        n = ring_count_band(base, widget_rect_in(win, w), ring)
        check(f"{theme} 失焦基线：{tag} 无环色像素", n == 0, f"命中 {n}")

    # --- 逐个聚焦，断言环真出现 + 截图 ---
    for tag, w in targets:
        w.setFocus()
        pump(app, 300)
        ok_focus = w.hasFocus()
        img = win.grab().toImage()
        n = ring_count_band(img, widget_rect_in(win, w), ring)
        png = os.path.join(OUT_DIR, f"focus-{theme}-{tag}.png")
        img.save(png)
        check(f"{theme} 聚焦 {tag}：拿到焦点且画出环色", ok_focus and n > 0,
              f"hasFocus={ok_focus} 环色像素={n}")
        w.clearFocus()
        pump(app, 120)

    # --- 真 Tab 走查：Tab 键能不能走到这些控件 ---
    win.activateWindow()
    pump(app, 200)
    visited = []
    for _ in range(24):
        send_tab(app, app.focusWidget() or win)
        fw = app.focusWidget()
        if fw is None:
            continue
        visited.append((type(fw).__name__, fw.objectName()))
    kinds = {t for t, _ in visited}
    check(f"{theme} Tab 走查命中 ≥3 类控件", len(kinds) >= 3,
          f"实际 {sorted(kinds)}")
    check(f"{theme} Tab 能走到侧栏导航按钮",
          any(o == "navBtn" for _, o in visited),
          f"实际 {sorted({o for _, o in visited})}")

    # 停在某个 QPushButton 上截图（Tab 走查产物）
    for _ in range(6):
        send_tab(app, app.focusWidget() or win)
    img = win.grab().toImage()
    tab_png = os.path.join(OUT_DIR, f"focus-{theme}-90-tabwalk.png")
    img.save(tab_png)
    print(f"  [OK] {tab_png}")
    print(f"  [i] Tab 走查序列：{visited}")

    win._allow_close = True
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# B. 控件台：主窗 QSS 与卡片窗 QSS 两套分别验
# ====================================================================
def _bench(qss: str, theme: str, builders, tag: str, app):
    """builders: [(名字, 构造函数)]。

    构造函数返回 ``目标控件``，或 ``(放进布局的控件, 目标控件)`` —— 后者用于
    Stepper 这种「外层容器进布局、真正的焦点目标在内部」的复合控件。
    每个目标控件必须自带 ``ringColor`` 动态属性（本脚本据此判色）。
    """
    host = QWidget()
    host.resize(520, 90)
    host.setStyleSheet(qss)
    lay = QHBoxLayout(host)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(14)

    made = []          # [(名字, 目标控件, 期望环色)]
    alive = []         # 防 GC
    for name, fn in builders:
        out = fn()
        if isinstance(out, tuple):
            add, target = out
        else:
            add = target = out
        lay.addWidget(add)
        alive.append(add)
        made.append((name, target,
                     str(target.property("ringColor") or "")))
    host.show()
    pump(app, 300)

    for name, w, want in made:
        assert want, f"{tag}/{theme} {name} 构造时未设置 ringColor 属性"
        w.setFocus()
        pump(app, 250)
        img = host.grab().toImage()
        n = scan_ring(img, host, w, want)
        check(f"{tag}/{theme} {name} 聚焦画出环色 {want}", n > 0,
              f"hasFocus={w.hasFocus()} 环色像素={n}")
        w.clearFocus()
        pump(app, 150)
        img2 = host.grab().toImage()
        n2 = scan_ring(img2, host, w, want)
        check(f"{tag}/{theme} {name} 失焦后环消失", n2 == 0,
              f"hasFocus={w.hasFocus()} 残留 {n2}")

    png = os.path.join(OUT_DIR, f"focus-{theme}-{tag}-bench.png")
    host.grab().save(png)
    print(f"  [OK] {png}")
    host.close()


def section_bench(app, theme):
    print(f"== B.{theme} 控件台：主窗 QSS + 卡片窗 QSS ==")
    ring = THEMES[theme]["focus_ring"]
    on_primary = THEMES[theme]["on_primary"]
    danger = THEMES[theme]["danger"]

    def mk_btn(obj: str, text: str, want: str):
        def _f():
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setFixedSize(120, 34)
            b.setProperty("ringColor", want)
            return b
        return _f

    def mk_check():
        c = QCheckBox("勾选")
        c.setProperty("ringColor", ring)
        c.setProperty("ringScan", "indicator")
        return c

    def mk_stepper_edit():
        """Stepper 的 ± 钮是 NoFocus，只有中间可键入的 QLineEdit 该有焦点环。"""
        s = Stepper(50, 200, 100)
        edit = s._edit
        edit.setProperty("ringColor", ring)
        return s, edit          # 容器进布局，焦点看内部 edit

    def mk_toggle():
        t = ToggleSwitch(checked=False, theme=theme)
        t.setProperty("ringColor", ring)
        return t

    _bench(get_main_window_qss(theme), theme, [
        ("secondaryBtn", mk_btn("secondaryBtn", "次按钮", ring)),
        ("primaryBtn", mk_btn("primaryBtn", "主按钮", on_primary)),
        ("dangerBtn", mk_btn("dangerBtn", "删除", danger)),
        ("QCheckBox", mk_check),
    ], "main", app)

    # 卡片窗口那份 QSS 是独立的，必须单独验
    _bench(get_card_window_qss(theme), theme, [
        ("modeBtn", mk_btn("modeBtn", "模式", ring)),
        ("nextBtn", mk_btn("nextBtn", "下一页", on_primary)),
        ("taskAddBtn", mk_btn("taskAddBtn", "＋ 添加", on_primary)),
        ("cardCloseBtn", mk_btn("cardCloseBtn", "关闭", danger)),
    ], "card", app)

    _bench(get_main_window_qss(theme), theme, [
        ("Stepper.stepValue", mk_stepper_edit),
        ("ToggleSwitch", mk_toggle),
    ], "main2", app)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\seguiemj.ttf"):
        if os.path.exists(cand):
            QFontDatabase.addApplicationFont(cand)

    for theme in ("light", "dark"):
        section_main_window(app, theme)
        section_bench(app, theme)

    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    print(f"(截图见 {OUT_DIR}\\focus-*.png)")
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
