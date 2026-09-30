# -*- coding: utf-8 -*-
"""换主题「即时跟随」离屏验证（2026-09-30 修复回归护栏）。

背景（用户报的 bug）
====================================================================
点右上角换风格按钮后，碎片工作台的列表文字不跟随：浅色主题下仍是深色
主题的字色（#E4E8EE 落在白底上几乎看不见），必须切一次页面才恢复。

根因：列表有两处颜色**不经 QSS**——
  1. 绘制代理的字色/时间色/高亮色/类别色条（``_delegate.set_theme``）
  2. 条目前景色（日期分组行=主色、路径行=link 色，创建时烘进 item）
两者都只在 ``refresh()`` 里赋值，而 refresh 只在**切到该页**时由
``host.refresh_page`` 触发 → 换主题时列表停留在旧配色。

本脚本用两段式验证：
  A. 面板级（FakeHost，主题可切）逐项断言代理 / 条目 / 类别色条已跟随
  B. 真实 MainWindow 端到端：**点右上角按钮**换主题，不切页面，
     直接抓列表 viewport 数像素（旧主题字色像素应大幅消失、
     新主题字色像素应大量出现）——纯逻辑断言会被"切页顺带刷新"掩盖，
     像素判据才能钉死"不切页面也生效"。

产出（供人工核对）：
    build/shots/theme-switch-before-dark.png   （dark 主题，refresh 后）
    build/shots/theme-switch-after-light.png   （点按钮切 light，未切页）
    build/shots/theme-switch-after-dark.png    （再切回 dark，未切页）

用法：
    python tools/run_gui_check.py tools/verify_theme_live_switch.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QObject, Qt, pyqtSignal                  # noqa: E402
from PyQt6.QtGui import QColor, QFontDatabase                     # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from src.theme import get_colors                                  # noqa: E402
from src.fragment_classifier import CAT_PATH, CAT_LINK            # noqa: E402
from src.fragment_manager import FragmentManager                   # noqa: E402
from src.config import ConfigManager                               # noqa: E402
from src.docx_manager import DocxManager                           # noqa: E402
from src.nav_manager import NavManager                             # noqa: E402
from src.note_manager import NoteManager                           # noqa: E402
from src.task_manager import TaskManager                           # noqa: E402
from src.temp_asset_manager import TempAssetManager                # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "build", "shots")

PASS = 0
FAIL = 0

DARK_BASE = QColor("#E4E8EE")     # dark 主题 text（代理默认字色）
LIGHT_BASE = QColor("#2C3E50")    # light 主题 text

# 像素判据：逐通道容差（#E4E8EE 与纯白 #FFFFFF 相差 27，取 20 可把白底排除）
TOL = 20


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, ms=1200):
    """离屏事件泵：给动画/主题重绘留时间（纯 processEvents 会漏掉延时重绘）"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def near(c: QColor, target: QColor, tol: int = TOL) -> bool:
    return (abs(c.red() - target.red()) <= tol
            and abs(c.green() - target.green()) <= tol
            and abs(c.blue() - target.blue()) <= tol)


def count_color(pixmap, target: QColor, tol: int = TOL) -> int:
    """数 pixmap 里接近 target 的像素数（alpha<30 的透明像素跳过）"""
    img = pixmap.toImage()
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            px = img.pixelColor(x, y)
            if px.alpha() < 30:
                continue
            if near(px, target, tol):
                n += 1
    return n


class FakeHost(QObject):
    """FragmentsPanel 所需的最小宿主替身（仿 verify_fragment_category.py）"""

    data_changed = pyqtSignal(str)

    def __init__(self, frag_mgr, theme="dark"):
        super().__init__()
        self._fragment_manager = frag_mgr
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = None
        self._theme = theme
        tmp = tempfile.mkdtemp(prefix="fp_verify_themels_")
        self._config = ConfigManager(os.path.join(tmp, "config.json"))

    @property
    def current_theme(self):
        return self._theme

    @property
    def _container(self):
        return None


def find_items(panel):
    """返回 (日期行, 路径行, 普通行) 三个样本 item（找不到给 None）"""
    from src.fragments_panel import COLOR_TOKEN_ROLE, TIME_ROLE
    date_item = path_item = normal_item = None
    for i in range(panel._frag_list.count()):
        it = panel._frag_list.item(i)
        token = it.data(COLOR_TOKEN_ROLE)
        if token == "primary":
            date_item = date_item or it
        elif token == "link":
            path_item = path_item or it
        elif it.data(TIME_ROLE) is not None:
            normal_item = normal_item or it
    return date_item, path_item, normal_item


# ====================================================================
# A. 面板级：apply_theme() 必须把代理与条目前景色一起换掉
# ====================================================================
def section_a(app):
    from src.fragments_panel import FragmentsPanel

    print("== A. 面板级 apply_theme（不重建列表） ==")
    tmp = tempfile.mkdtemp(prefix="fp_verify_themels_data_")
    mgr = FragmentManager(os.path.join(tmp, "fragments.json"))
    mgr.add_clipboard_text("普通文本一条，用于对比前景色")
    mgr.add_clipboard_path(r"D:\桌面\AI Port\FloatPulse\v4\src\main_window.py")
    mgr.add_clipboard_text("https://docs.python.org/3/library/dataclasses.html")
    mgr.add_clipboard_text("pip install PyQt6")

    host = FakeHost(mgr, theme="dark")
    panel = FragmentsPanel(host)
    panel.show()
    app.processEvents()
    panel.refresh(preserve_view=False)      # 模拟「切到该页」的初始填充
    app.processEvents()

    d_before = get_colors("dark")
    check("A1 dark 代理字色=dark.text",
          panel._delegate._base_color.name().upper() == d_before["text"].upper(),
          panel._delegate._base_color.name())
    date_item, path_item, normal_item = find_items(panel)
    check("A2 找到日期行/路径行/普通行样本",
          None not in (date_item, path_item, normal_item),
          f"date={date_item is not None} path={path_item is not None} "
          f"normal={normal_item is not None}")
    check("A3 dark 日期行前景=dark.primary",
          near(date_item.foreground().color(), QColor(d_before["primary"])),
          date_item.foreground().color().name())
    check("A4 dark 路径行前景=dark.link",
          near(path_item.foreground().color(), QColor(d_before["link"])),
          path_item.foreground().color().name())
    check("A5 普通条目前景色为空（留给代理取字色）",
          normal_item.foreground().style() == Qt.BrushStyle.NoBrush,
          str(normal_item.foreground().style()))

    # ★ 换主题：只调 apply_theme，**不 refresh**
    host._theme = "light"
    panel.apply_theme()
    app.processEvents()

    l_now = get_colors("light")
    check("A6 light 代理字色=light.text",
          near(panel._delegate._base_color, QColor(l_now["text"])),
          panel._delegate._base_color.name())
    check("A7 light 时间色=light.text_placeholder",
          near(panel._delegate._time_color, QColor(l_now["text_placeholder"])),
          panel._delegate._time_color.name())
    check("A8 light 日期行前景=light.primary",
          near(date_item.foreground().color(), QColor(l_now["primary"])),
          date_item.foreground().color().name())
    check("A9 light 路径行前景=light.link",
          near(path_item.foreground().color(), QColor(l_now["link"])),
          path_item.foreground().color().name())
    check("A10 普通条目前景色仍为空（未被误染色）",
          normal_item.foreground().style() == Qt.BrushStyle.NoBrush,
          str(normal_item.foreground().style()))
    check("A11 类别色条跟随（path→light.warn）",
          near(panel._delegate._cat_colors.get(CAT_PATH, QColor("#000")),
               QColor(l_now["warn"])),
          panel._delegate._cat_colors.get(CAT_PATH, QColor("#000")).name())
    check("A12 内容命中高亮色跟随（light.primary + alpha 85）",
          near(panel._delegate._hl_bg, QColor(l_now["primary"]))
          and panel._delegate._hl_bg.alpha() == 85,
          f"{panel._delegate._hl_bg.name()} a={panel._delegate._hl_bg.alpha()}")
    check("A13 类别色条 link token 存在映射",
          CAT_LINK in panel._delegate._cat_colors)

    # 反向：切回 dark 也要跟随（防止写成单向）
    host._theme = "dark"
    panel.apply_theme()
    app.processEvents()
    check("A14 切回 dark：代理字色回到 dark.text",
          near(panel._delegate._base_color, QColor(d_before["text"])),
          panel._delegate._base_color.name())
    check("A15 切回 dark：日期行回到 dark.primary",
          near(date_item.foreground().color(), QColor(d_before["primary"])),
          date_item.foreground().color().name())

    panel.hide()
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# B. 端到端：点右上角主题按钮，不切页面，像素判据
# ====================================================================
def build_real_window(app, tmp):
    """真实 MainWindow（数据全在临时目录，不碰用户数据）"""
    from src.main_window import MainWindow

    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx_path = os.path.join(tmp, "知识库.docx")
    try:
        from docx import Document
        doc = Document()
        for t in ("改动前先跑 pytest", "报价保留两位小数", "客户资料统一放 float_data"):
            doc.add_paragraph(t)
        doc.save(docx_path)
    except Exception as exc:
        print(f"  [i] 演示知识库生成失败（不影响 B 段）: {exc!r}")
    docx = DocxManager(docx_path, os.path.join(tmp, "docx_meta.json"))
    docx.load()

    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    for text in ("周会要点：下周三前把报价单模板定稿，接口文档同步给测试",
                 "客户来访时间改到周五上午十点，会议室改三楼",
                 "先跑通流程，再谈优化"):
        frags.add_clipboard_text(text)
    frags.add_clipboard_path(r"D:\work\report\2026-09 月报汇总.pptx")
    frags.add_clipboard_text("python -m PyInstaller --noconfirm FloatPulse.spec")

    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(1280, 740)
    return win


def section_b(app):
    print("== B. 端到端：点右上角按钮换主题（不切页面） ==")
    tmp = tempfile.mkdtemp(prefix="fp_verify_themels_win_")
    win = build_real_window(app, tmp)

    # 起始态固定为 dark（并落盘，避免读用户真实 config）
    win._theme = "dark"
    win._config.set("theme", "dark")
    win._apply_theme()
    win.show()
    win._switch_page(0)              # 砌出列表（等价于用户切到碎片页）
    pump(app, 1500)

    viewport = win._page_fragments._frag_list.viewport()
    os.makedirs(OUT_DIR, exist_ok=True)
    p_before = os.path.join(OUT_DIR, "theme-switch-before-dark.png")
    p_after = os.path.join(OUT_DIR, "theme-switch-after-light.png")
    p_back = os.path.join(OUT_DIR, "theme-switch-after-dark.png")
    before = count_color(viewport.grab(), DARK_BASE)
    # ★ dark 态留证必须在点击前抓（点完就变 light 了）
    win.grab().save(p_before)
    print(f"  [i] dark 态：dark 字色像素={before}")
    print(f"  [OK] {p_before}")

    # ★ 等价于用户点右上角主题按钮
    btn = win._theme_btn
    check("B1 主题按钮存在且已接 _toggle_theme",
          btn is not None and win._theme == "dark")
    btn.click()
    pump(app, 900)
    check("B2 点击后 win._theme=light", win._theme == "light", win._theme)
    check("B3 未切页面（stack 仍在 0）", win._stack.currentIndex() == 0,
          str(win._stack.currentIndex()))

    # --- 逻辑层 ---
    frag_panel = win._page_fragments
    light = get_colors("light")
    check("B4 碎片代理字色已跟随 light.text",
          near(frag_panel._delegate._base_color, QColor(light["text"])),
          frag_panel._delegate._base_color.name())
    date_item, path_item, _ = find_items(frag_panel)
    check("B5 日期行前景已跟随 light.primary",
          date_item is not None
          and near(date_item.foreground().color(), QColor(light["primary"])),
          date_item.foreground().color().name() if date_item else "无日期行")
    check("B6 路径行前景已跟随 light.link",
          path_item is not None
          and near(path_item.foreground().color(), QColor(light["link"])),
          path_item.foreground().color().name() if path_item else "无路径行")

    # --- 像素层（钉死"不切页面也生效"）---
    after_dark = count_color(viewport.grab(), DARK_BASE)
    after_light = count_color(viewport.grab(), LIGHT_BASE)
    print(f"  [i] light 态（未切页）：dark 字色像素={after_dark} "
          f"light 字色像素={after_light}")
    check("B7 旧主题字色像素大幅消失（<30% 原值）",
          after_dark < max(20, before * 0.3),
          f"before={before} after={after_dark}")
    check("B8 新主题字色像素大量出现（≥50）", after_light >= 50,
          f"count={after_light}")

    # ★ light 态截图必须在"不切页面"的当下抓：切页会顺带 refresh 一遍，
    #   那样截出来的"正常"证明不了修复生效（原 bug 正是被切页掩盖的）
    win.grab().save(p_after)
    print(f"  [OK] {p_after}")

    # 再切回 dark（同一个按钮），同样不切页面
    btn.click()
    pump(app, 900)
    check("B9 再点一次回到 dark", win._theme == "dark", win._theme)
    back_dark = count_color(viewport.grab(), DARK_BASE)
    back_light = count_color(viewport.grab(), LIGHT_BASE)
    win.grab().save(p_back)
    print(f"  [OK] {p_back}")
    print(f"  [i] 回 dark 态：dark 字色像素={back_dark} light 字色像素={back_light}")
    check("B10 回 dark：dark 字色像素重新出现（≥50）", back_dark >= 50,
          f"count={back_dark}")
    check("B11 回 dark：light 字色像素回落到 <30%",
          back_light < max(20, after_light * 0.3),
          f"after={after_light} back={back_light}")

    check("B12 三张截图产物齐全",
          all(os.path.exists(p) for p in (p_before, p_after, p_back)))

    win.hide()
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# C. 知识库面板：inline stylesheet 警告色也要跟随
# ====================================================================
def section_c(app):
    print("== C. 知识库「外部修改」警告色跟随 ==")
    from src.knowledge_panel import KnowledgePanel

    tmp = tempfile.mkdtemp(prefix="fp_verify_themels_kb_")
    docx_path = os.path.join(tmp, "知识库.docx")
    try:
        from docx import Document
        doc = Document()
        for i in range(6):
            doc.add_paragraph(f"这是第 {i + 1} 段要被识别的正文内容，长度足够。")
        doc.save(docx_path)
    except Exception as exc:
        print(f"  [i] 知识库生成失败，C 段跳过: {exc!r}")
        return
    docx = DocxManager(docx_path, os.path.join(tmp, "docx_meta.json"))
    docx.load()

    host = FakeHost(None, theme="dark")
    host._docx_manager = docx
    panel = KnowledgePanel(host)
    panel.show()
    app.processEvents()

    panel._kb_modify_label.setText("⚠️ 检测到外部修改，建议重新加载")
    panel._apply_modify_label_color()
    check("C1 dark 下警告色=#F39C12",
          "F39C12" in panel._kb_modify_label.styleSheet().upper(),
          panel._kb_modify_label.styleSheet())
    host._theme = "light"
    panel.apply_theme()
    check("C2 切 light 后警告色=#E67E22",
          "E67E22" in panel._kb_modify_label.styleSheet().upper(),
          panel._kb_modify_label.styleSheet())
    # 正常态（无警告文案）不应被误着色
    panel._kb_modify_label.setText("✓ 文件无外部修改")
    panel._kb_modify_label.setStyleSheet("")
    host._theme = "dark"
    panel.apply_theme()
    check("C3 正常态不被误着色", panel._kb_modify_label.styleSheet() == "",
          panel._kb_modify_label.styleSheet())

    panel.hide()
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# D. apply_external_theme（设置面板路径）与按钮路径行为一致
# ====================================================================
def section_d(app):
    print("== D. apply_external_theme 路径一致性 ==")
    tmp = tempfile.mkdtemp(prefix="fp_verify_themels_ext_")
    win = build_real_window(app, tmp)
    win._theme = "dark"
    win._apply_theme()
    win.show()
    win._switch_page(0)
    pump(app, 1200)

    win.apply_external_theme("light")
    pump(app, 700)
    light = get_colors("light")
    check("D1 外部切主题后碎片代理字色跟随",
          near(win._page_fragments._delegate._base_color, QColor(light["text"])),
          win._page_fragments._delegate._base_color.name())
    check("D2 非法主题名被忽略",
          (win.apply_external_theme("bogus"), win._theme == "light")[1],
          win._theme)
    win.hide()
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\seguiemj.ttf"):
        if os.path.exists(cand):
            QFontDatabase.addApplicationFont(cand)

    section_a(app)
    section_b(app)
    section_c(app)
    section_d(app)

    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    print("(B 段截图见 build/shots/theme-switch-*.png)")
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
