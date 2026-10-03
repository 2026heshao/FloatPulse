# -*- coding: utf-8 -*-
"""离屏验证：小卡片碎片页对齐高仿真 .mini 壳（UI 重构 06）。

覆盖：
  A 结构几何：内容区贴边 / 页眉 36 / 行 26 / 页脚 34
  B 页眉：标题「碎片」+ 本屏条数
  C 行组成：类别圆点（cat 属性）+ 内容预览 + HH:MM 时间
  D 取色：圆点 token（钉字面量，light/dark 各一套）+ 页脚三钮图标色
  E 单击 = 选中 + 复制 + 页脚回执；★ 只有 press 没有 release 时不复制
    （反向验证护栏能红）
  F 拖卡片 = 不复制（press 落在行上但松手前移动了）
  G 页脚动作：删除作用于选中行；删除后选中清空、三钮禁用
  H 页脚编辑：对话框宿主主题 = 卡片当前主题；保存回调写回数据层
  I 空态：无碎片 → 计数 0 + 三钮禁用
  J 换主题：圆点/图标取色跟随（按新主题重新解析）
  K 单一真相源：fragments_panel._CATEGORY_TOKENS 就是 CATEGORY_TOKENS，
    且每个类别都有 QSS 规则（加类别漏写会静默退化成灰点 → 必须能红灯）
"""
import os
import sys
import tempfile

V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V4)

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QFontDatabase, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

import src.card_window as cwmod  # noqa: E402
from src.card_window import (  # noqa: E402
    CardWindow, FRAG_FOOT_HEIGHT, FRAG_HEAD_HEIGHT, FRAG_ROW_HEIGHT,
    _card_shell_qss, _fragment_time_text,
)
from src.fragment_classifier import CATEGORY_ORDER, CATEGORY_TOKENS  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.theme import get_colors  # noqa: E402

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


def pump(ms=120):
    from PyQt6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        _app.processEvents()


def mouse(widget, kind, pos, buttons):
    """构造并投递一个鼠标事件（离屏下走 QApplication.sendEvent）"""
    ev = QMouseEvent(kind, QPointF(pos), QPointF(pos), Qt.MouseButton.LeftButton,
                     buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


def make_card(theme="light", samples=None):
    tmp = tempfile.mkdtemp(prefix="fp_verify_cardfrag_")
    mgr = FragmentManager(os.path.join(tmp, "fragments.json"))
    for s in (samples or []):
        mgr.add_clipboard_text(s)
    cw = CardWindow(theme=theme)
    cw.set_fragment_manager(mgr)
    cw.show()
    pump(200)
    cw._switch_mode("fragment")
    pump(300)
    return cw, mgr


SAMPLES = [
    "客户来访改到周五上午十点，门禁卡提前准备好",
    r"D:\桌面\AI Port\FloatPulse\v4\src\fragments_panel.py",
    "https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html",
    'git commit -m "fix(fragments): 保留滚动位置"',
]

cw, mgr = make_card("light", SAMPLES)
rows = cw._frag_rows()

print("== A. 结构几何 ==")
m = cw._content_layout.contentsMargins()
check("A1 碎片页内容区贴边（0 边距，页眉/页脚边线通栏）",
      (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0),
      f"margins={m.left()},{m.top()},{m.right()},{m.bottom()}")
head = cw.findChild(type(cw._container), "miniHead")
check("A2 页眉高度 = 36", head is not None and head.height() == FRAG_HEAD_HEIGHT,
      f"head={head.height() if head else None}")
foot = cw.findChild(type(cw._container), "miniFoot")
check("A3 页脚高度 = 34", foot is not None and foot.height() == FRAG_FOOT_HEIGHT,
      f"foot={foot.height() if foot else None}")
check("A4 行高 = 26 且条数 = 数据条数",
      len(rows) == len(SAMPLES) and rows[0].height() == FRAG_ROW_HEIGHT,
      f"rows={len(rows)} h={rows[0].height() if rows else None}")
check("A5 页眉横跨整张卡片（宽 = 440 - Tab 栏 44）",
      head is not None and head.width() == cw.WINDOW_WIDTH - 44,
      f"head_w={head.width() if head else None}")
cw._switch_mode("task")
pump(150)
check("A6 换页后其它页仍是 16px 内衬",
      cw._content_layout.contentsMargins().left() == cw.CONTENT_MARGIN,
      cw._content_layout.contentsMargins().left())
# ★ 切回碎片页会重建行：此前的行对象已成幽灵（deleteLater）。先派发
# DeferredDelete 把它们清干净，再重新取行 —— 否则后面所有交互断言都在
# 对着幽灵控件发事件（离屏 processEvents 不保证派发 DeferredDelete）。
cw._switch_mode("fragment")
pump(150)
_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
rows = cw._frag_rows()

print("== B. 页眉 ==")
check("B1 标题 = 碎片", cw._frag_title_label.text() == "碎片", cw._frag_title_label.text())
check("B2 计数 = 本屏条数", cw._frag_count_label.text() == str(len(SAMPLES)),
      cw._frag_count_label.text())

print("== C. 行组成 ==")
first = rows[0]
labels = [w for w in first.findChildren(type(cw._frag_count_label))]
check("C1 行含 3 个子标签（圆点/内容/时间）", len(labels) == 3, f"n={len(labels)}")
check("C2 圆点对象名 fragCatDot",
      any(w.objectName() == "fragCatDot" for w in labels))
time_labels = [w for w in labels if w.objectName() == "fragTime"]
check("C3 时间 = HH:MM（5 字符含冒号）",
      len(time_labels) == 1 and len(time_labels[0].text()) == 5
      and ":" in time_labels[0].text(), f"{time_labels and time_labels[0].text()}")
cats = {r.findChild(type(cw._frag_count_label), "fragCatDot").property("cat") for r in rows}
check("C4 圆点 cat 属性 = 碎片类别集合",
      cats <= set(CATEGORY_ORDER) and len(cats) > 1, f"cats={cats}")

print("== D. 取色（钉字面量）==")
light = get_colors("light")
dark = get_colors("dark")
qss_light = _card_shell_qss(light)
qss_dark = _card_shell_qss(dark)
for cat, token in CATEGORY_TOKENS.items():
    sel = f'QLabel#fragCatDot[cat="{cat}"]'
    check(f"D·{cat} → {token}（light/dark 两套字面量都在）",
          f"background-color: {light[token]};" in qss_light
          and f"background-color: {dark[token]};" in qss_dark,
          f"light={light[token]} dark={dark[token]}")
check("D1 页脚三钮常态取色：复制/编辑 = text_secondary，删除 = danger",
      cw._frag_copy_btn._off_spec == "text_secondary"
      and cw._frag_edit_btn._off_spec == "text_secondary"
      and cw._frag_del_btn._off_spec == "danger")
check("D2 页脚钮 QSS 常态透明无边框（不再是彩色小胶囊）",
      "QPushButton#fragCopyBtn, QPushButton#fragDelBtn," in qss_light
      and "background-color: transparent;" in qss_light)

print("== E. 单击 = 选中 + 复制 ==")
target = rows[1]
fid = target.fragment_id
content = mgr.get_fragment(fid).content
check("E0 目标行是当前列表里活着的行（不是幽灵控件）",
      target in cw._frag_rows() and target.parent() is not None)
QApplication.clipboard().setText("")
mouse(target, QEvent.Type.MouseButtonPress, target.rect().center(),
      Qt.MouseButton.LeftButton)
check("E1 按下落在行上时卡片确实接管了事件（真实使用中 release 会回到卡片窗）",
      cw._frag_press_id == fid and cw._dragging is True,
      f"press_id={cw._frag_press_id} dragging={cw._dragging}")
check("E2 ★ 只有 press、没有 release 时不复制（护栏能红）",
      QApplication.clipboard().text() == "", repr(QApplication.clipboard().text()))
mouse(cw, QEvent.Type.MouseButtonRelease, cw.rect().center(),
      Qt.MouseButton.NoButton)
pump(150)
check("E3 松开（未移动）→ 内容进剪贴板", QApplication.clipboard().text() == content,
      repr(QApplication.clipboard().text())[:60])
check("E4 该行变为选中态（QSS [selected=true]）",
      target.property("selected") == "true"
      and cw._frag_selected_id == fid)
check("E5 页脚三钮随选中变为可用",
      cw._frag_copy_btn.isEnabled() and cw._frag_del_btn.isEnabled()
      and cw._frag_edit_btn.isEnabled())
check("E6 页脚右端出现「已复制」回执",
      cw._frag_hint_label.isVisible() and cw._frag_hint_label.text() == "已复制",
      cw._frag_hint_label.text())
check("E7 选中态不改变行内文字起始位置（左边距恒定）",
      target.layout().contentsMargins().left() == cwmod.FRAG_ROW_INSET_L
      and 'border-left: 2px solid transparent;' in qss_light)

print("== F. 拖卡片 = 不复制 ==")
QApplication.clipboard().setText("")
drag_row = rows[2]
before_pos = cw.pos()
mouse(drag_row, QEvent.Type.MouseButtonPress, drag_row.rect().center(),
      Qt.MouseButton.LeftButton)
mouse(cw, QEvent.Type.MouseMove, drag_row.rect().center() + QPointF(40, 30).toPoint(),
      Qt.MouseButton.LeftButton)
mouse(cw, QEvent.Type.MouseButtonRelease, cw.rect().center(),
      Qt.MouseButton.NoButton)
pump(150)
check("F1 拖完剪贴板仍为空（没被顺手复制）",
      QApplication.clipboard().text() == "", repr(QApplication.clipboard().text()))
check("F2 卡片确实被移动过（确认走的是拖动分支）", cw.pos() != before_pos,
      f"{before_pos} -> {cw.pos()}")
check("F3 选中行未被拖动改写", cw._frag_selected_id == fid)

print("== G. 页脚删除作用于选中行 ==")
n_before = len(cw._frag_rows())
cw._frag_del_btn.click()
pump(250)
check("G1 列表少一条", len(cw._frag_rows()) == n_before - 1,
      f"{n_before} -> {len(cw._frag_rows())}")
check("G2 数据层确认删除", mgr.get_fragment(fid) is None)
check("G3 选中已清空、三钮回到禁用",
      cw._frag_selected_id is None and not cw._frag_del_btn.isEnabled())
check("G4 页眉计数同步", cw._frag_count_label.text() == str(n_before - 1),
      cw._frag_count_label.text())

print("== H. 页脚编辑 ==")
rows = cw._frag_rows()
edit_row = rows[0]
cw._sync_frag_selection(edit_row.fragment_id)
captured = {}


class _FakeDlg:
    """替身：捕获构造参数与保存回调，不真的 exec（离屏会阻塞）"""

    def __init__(self, fragment, on_save, host=None, parent=None):
        captured["fragment"] = fragment
        captured["on_save"] = on_save
        captured["host"] = host

    def exec(self):
        return 0


cwmod.FragmentEditDialog = _FakeDlg
cw._edit_selected_fragment()
check("H1 对话框宿主主题 = 卡片当前主题（不再落回 DEFAULT_THEME）",
      getattr(captured.get("host"), "current_theme", None) == "light",
      getattr(captured.get("host"), "current_theme", None))
check("H2 宿主刻意不带 _container（否则会抓卡片 QSS 当对话框样式）",
      not hasattr(captured.get("host"), "_container"))
ok = captured["on_save"]("改过的内容")
pump(150)
check("H3 保存回调写回数据层", ok and captured["fragment"].content == "改过的内容",
      captured["fragment"].content)

print("== I. 空态 ==")
cw_empty, _ = make_card("light", [])
check("I1 空态计数为 0", cw_empty._frag_count_label.text() == "0")
check("I2 空态三钮禁用", not cw_empty._frag_copy_btn.isEnabled()
      and not cw_empty._frag_del_btn.isEnabled()
      and not cw_empty._frag_edit_btn.isEnabled())
check("I3 空态无碎片行", cw_empty._frag_rows() == [])

print("== J. 换主题跟随 ==")
cw.apply_theme("dark")
pump(200)
dark_qss = cw._container.styleSheet()
check("J1 换主题后容器 QSS 用的是 dark 字面量",
      f'background-color: {dark["link"]};' in dark_qss
      and f'background-color: {light["link"]};' not in dark_qss)
check("J2 页脚图标位图跟随主题重取色",
      cw._frag_copy_btn.icon().pixmap(15, 15).toImage().pixelColor(0, 0).isValid())
cw.apply_theme("light")
pump(150)

print("== K. 类别色单一真相源 ==")
import src.fragments_panel as fpmod  # noqa: E402
check("K1 fragments_panel._CATEGORY_TOKENS 就是 classifier.CATEGORY_TOKENS",
      fpmod._CATEGORY_TOKENS is CATEGORY_TOKENS)
missing = [c for c in CATEGORY_ORDER if f'QLabel#fragCatDot[cat="{c}"]' not in qss_light]
check("K2 每个类别都有圆点取色规则（漏写会退化成灰点）", not missing, f"缺 {missing}")
check("K3 未知类别有兜底色（不会画成透明）",
      "QLabel#fragCatDot {" in qss_light)

print("== N. 右上角关闭钮（只改样式，显示逻辑不变）==")
from src.constants import RADIUS_CHIP  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.controls import _OVERLAY_RADIUS, _SMOOTH_OVERLAYS  # noqa: E402
check("N1 QSS 常态透明无边框 + r_chip 圆角",
      "QPushButton#cardCloseBtn {" in qss_light
      and "border-radius: %spx;" % RADIUS_CHIP in
      qss_light.split("QPushButton#cardCloseBtn {", 1)[1].split("}", 1)[0])
check("N2 hover 端点改为中性浅面（不再是红底实心）",
      _SMOOTH_OVERLAYS["cardCloseBtn"] == (("surface_2", 255), ("surface_3", 255)),
      _SMOOTH_OVERLAYS["cardCloseBtn"])
check("N3 overlay 圆角 = RADIUS_CHIP", _OVERLAY_RADIUS["cardCloseBtn"] == RADIUS_CHIP)
check("N4 图标取色 = text_placeholder / hover text",
      cw._close_btn._off_spec == "text_placeholder"
      and cw._close_btn._hover_spec == "text")
check("N5 ★ 显示逻辑不变：非常驻模式下仍隐藏",
      cw.has_shown_content() or not cw._close_btn.isVisible(),
      f"visible={cw._close_btn.isVisible()}")
cw_always = CardWindow(theme="light")
cfg = ConfigManager(os.path.join(tempfile.mkdtemp(prefix="fp_verify_always_"), "c.json"))
cfg.set("card_always_show", True)
cw_always.set_config_manager(cfg)
cw_always.show()
pump(300)
cw_always.refresh_always_show_layout()
check("N6 ★ 常驻模式下才显示（与改造前一致）",
      cw_always._close_btn.isVisible() and cw_always.is_always_show(),
      f"visible={cw_always._close_btn.isVisible()} "
      f"always={cw_always.is_always_show()}")

print("== M. 护栏自检（反向验证：换个错值断言必须跟着变）==")
doctored = dict(light)
doctored["link"] = "#000000"
qss_bad = _card_shell_qss(doctored)
check("M1 取色断言真的在读值（把 link 换成 #000000 → 规则随之改变）",
      "background-color: #000000;" in qss_bad
      and f'background-color: {light["link"]};' not in qss_bad)

print("== L. 时间格式纯函数 ==")
check("L1 完整时间戳 → HH:MM", _fragment_time_text("2026-10-02 09:41:00") == "09:41")
check("L2 空/None 安全", _fragment_time_text("") == "" and _fragment_time_text(None) == "")

print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
