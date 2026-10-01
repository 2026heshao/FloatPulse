# -*- coding: utf-8 -*-
"""图标体系（UI 强化方案 A2）离屏渲染验证 + 真实截图。

为什么需要它（pytest 覆盖不到的部分）
====================================================================
``tests/test_icons.py`` 能钉住「路径数据合法 / 名称唯一 / 覆盖清单完整」，
但**钉不住"画出来"这件事** —— 路径数据可以完全合法却画不出任何东西
（坐标系写错、子路径没闭合、颜色传了透明值），而这正是图标体系最容易
静默失败的地方：界面上只表现为"少了一个图标"，不报任何错。

所以本脚本做四件 pytest 做不到的事：

  1. **逐图标逐尺寸真渲染**：16/24/32/48px 各渲一遍，数不透明像素 ——
     0 像素就是"画了但没画出来"；
  2. **颜色等于传入 token**：把图标渲染成主题色，逐像素比对；
  3. **真实 MainWindow**：侧栏按钮的图标非空、文案里没有 emoji 残留、
     选中态取主色（``QIcon.State.On``）、换主题后取色跟随；
  4. **light/dark 双主题真截图**，且**刻意只挂中文字体、不挂 emoji 字体**
     —— 这恰恰是图标体系的价值证明：以前必须挂 seguiemj.ttf 才能让
     emoji 不变成空心方框（见 ``shot_readme.py`` 的注释），现在不需要了。

用法：
    python tools/run_gui_check.py tools/verify_icons_render.py
产物：
    build/shots/icons-<theme>-*.png
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QElapsedTimer, QRectF, QSize         # noqa: E402
from PyQt6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from src import icon_render, icons                              # noqa: E402
from src.config import ConfigManager                            # noqa: E402
from src.docx_manager import DocxManager                        # noqa: E402
from src.fragment_manager import FragmentManager                 # noqa: E402
from src.nav_layout import NAV_GROUPS                           # noqa: E402
from src.nav_manager import NavManager                          # noqa: E402
from src.note_manager import NoteManager                        # noqa: E402
from src.task_manager import TaskManager                        # noqa: E402
from src.temp_asset_manager import TempAssetManager             # noqa: E402
from src.theme import get_colors                                # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "build", "shots")

# 取色容差：图标是抗锯齿描边，核心像素才会落在纯色附近，容差放太宽会把
# 背景的 primary_a18 浅染误判成图标笔画。
TOL = 32

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
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        app.processEvents()


def load_cjk_font(app):
    """只挂中文字体，**刻意不挂 emoji / 符号字体**（价值证明，见模块头）。"""
    loaded = []
    for cand in (r"C:\Windows\Fonts\msyh.ttc",):
        if os.path.exists(cand) and app is not None:
            if QFontDatabase.addApplicationFont(cand) >= 0:
                loaded.append(os.path.basename(cand))
    return loaded


# ====================================================================
# 像素工具
# ====================================================================
def opaque_count(img) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 0:
                n += 1
    return n


def color_count(img, color, tol=TOL) -> int:
    want = QColor(color)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() < 128:
                continue
            if (abs(c.red() - want.red()) <= tol
                    and abs(c.green() - want.green()) <= tol
                    and abs(c.blue() - want.blue()) <= tol):
                n += 1
    return n


def bbox_ratio(img) -> float:
    """不透明像素的外接框占整幅的比例（防"只画了一个孤点"）。"""
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 0:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0.0
    w = max(xs) - min(xs) + 1
    h = max(ys) - min(ys) + 1
    return (w * h) / float(img.width() * img.height())


def mean_opaque_color(img):
    """不透明**核心**像素的均值色（只取 alpha≥200 的，避开抗锯齿过渡边）。"""
    rs = gs = bs = n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() >= 200:
                rs += c.red()
                gs += c.green()
                bs += c.blue()
                n += 1
    if not n:
        return None
    return (rs / float(n), gs / float(n), bs / float(n))


def rgb_of(color) -> tuple:
    c = QColor(color)
    return (c.red(), c.green(), c.blue())


def color_dist(c1, c2) -> float:
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


def _same_color(c1, c2, tol: float = 6.0) -> bool:
    return color_dist(rgb_of(c1), rgb_of(c2)) <= tol


def render_transparent(w):
    """把控件渲染到**透明**底上。

    ★ ``QWidget.grab()`` 会先用调色板的窗口底色填底 —— 对无背景的自绘控件
      （IconLabel）来说，底噪会把均值色淹没成一个浅灰，断言因此失去意义
      （实测踩坑：18×18 图标 grab 出来均值 (177,183,188)，既不像深色文字
      也不像浅色文字，纯粹是底色）。``render()`` 只带 DrawChildren、
      不带 DrawWindowBackground，才是"只看笔画"的正确姿势。
    """
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QRegion
    from PyQt6.QtWidgets import QWidget

    pm = QPixmap(w.width(), w.height())
    pm.fill(QColor(0, 0, 0, 0))
    w.render(pm, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    return pm


# ====================================================================
# A/B/C/D. 图标本身的渲染矩阵
# ====================================================================
def section_render_matrix():
    print("== A. 逐图标逐尺寸真渲染（不透明像素 > 0）==")
    empty = []
    for name in icons.icon_names():
        row = []
        for size in (16, 24, 32, 48):
            img = icon_render.icon_pixmap(name, size, "#27787A").toImage()
            n = opaque_count(img)
            row.append(n)
            if n == 0:
                empty.append((name, size))
        print("     %-10s 16=%-5d 24=%-5d 32=%-5d 48=%-5d" % (name, *row))
    check("11 个图标 × 4 档尺寸全部画出内容", not empty, f"空渲染：{empty}")

    print("== B. 外接框占比（防'孤点'）==")
    # P1 起图标集里有「一根线条」的一维字形（minimize/minus）——外接框
    # 面积对线条恒小（宽 58% 但面积只有 ~4%），判据对它们改按「最长边
    # 跨度」；其余二维图形仍按面积 ≥25%。
    THIN_GLYPHS = {"minimize", "minus"}

    def bbox_span(img) -> float:
        xs, ys = [], []
        for y in range(img.height()):
            for x in range(img.width()):
                if img.pixelColor(x, y).alpha() > 0:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return 0.0
        side = float(max(img.width(), img.height()))
        return max(max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) / side

    thin = []
    for name in icons.icon_names():
        img = icon_render.icon_pixmap(name, 32, "#27787A").toImage()
        ok = (bbox_span(img) >= 0.5 if name in THIN_GLYPHS
              else bbox_ratio(img) >= 0.25)
        if not ok:
            thin.append((name, round(bbox_ratio(img), 3)))
    check("每个图标外接框达标（二维按面积≥25%，线条字形按跨度≥50%）",
          not thin, f"过小：{thin}")

    print("== C. 颜色等于传入 token ==")
    wrong = []
    for theme in ("light", "dark"):
        for tok in ("text_secondary", "primary"):
            want = get_colors(theme)[tok]
            for name in icons.icon_names():
                img = icon_render.icon_pixmap(name, 32, want).toImage()
                if color_count(img, want) == 0:
                    wrong.append((theme, tok, name))
    check("双主题 × 两种 token × 11 图标：渲染色都等于传入色", not wrong,
          f"不符：{wrong[:6]}")

    print("== D. 缓存语义 ==")
    a = icon_render.icon_pixmap("tasks", 20, "#111111")
    b = icon_render.icon_pixmap("tasks", 20, "#111111")
    c = icon_render.icon_pixmap("tasks", 20, "#222222")
    d = icon_render.icon_pixmap("tasks", 24, "#111111")
    check("同参数命中同一 QPixmap", a is b)
    check("异色不共用缓存对象", a is not c)
    check("异尺寸不共用缓存对象", a is not d)
    check("路径缓存按名命中同一个 QPainterPath",
          icon_render.icon_path("nav") is icon_render.icon_path("nav"))
    bad = False
    try:
        icon_render.icon_path("不存在的图标")
    except KeyError:
        bad = True
    check("未登记名称抛 KeyError（不静默画空）", bad)


# ====================================================================
# E. 真实 MainWindow
# ====================================================================
def build_real_window(tmp):
    from src.main_window import MainWindow

    config = ConfigManager(os.path.join(tmp, "config.json"))
    docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(tmp, "docx_meta.json"))
    docx.load()
    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    frags.add_clipboard_text("图标验证用的一条碎片：先跑通流程，再谈优化")
    frags.add_clipboard_path(r"D:\work\report\2026-09 月报汇总.pptx")

    win = MainWindow(tasks, notes, frags, docx, config, None,
                     temp_asset_manager=assets, nav_manager=nav)
    win.resize(1280, 740)
    return win, config


EMOJI_SAMPLE = "🧩📋📝📚📎🚀🌐🔌⚙❓"


def section_main_window(app, theme):
    print(f"== E.{theme} 真实 MainWindow：导航图标 ==")
    tmp = tempfile.mkdtemp(prefix="fp_verify_icons_")
    win, config = build_real_window(tmp)

    win._theme = theme
    config.set("theme", theme)
    win._apply_theme()
    # ★ 必须补这一步广播：面板标题图标是**订阅方**（见 controls.PageTitle），
    #   只改 _theme + _apply_theme() 不会触发它们重取色 —— 真实换主题链路
    #   是 _toggle_theme()/set_theme() 里"_apply_theme() 之后再 emit"。
    win.theme_changed.emit(theme)

    # 注册一个带 emoji 标题的演示插件页（验证剥离 + 占位图标）
    from PyQt6.QtWidgets import QWidget
    win.register_plugin_page("plugin:demo", "🤖 演示插件", QWidget())

    # 四组全展开，所有导航项都可见（默认只展开 workbench）
    win._current_expanded_groups = set(NAV_GROUPS)
    win._relayout_nav(win._nav_order)

    win.show()
    win._switch_page(0)
    pump(app, 1200)

    colors = get_colors(theme)
    off = colors["text_secondary"]
    on = colors["primary"]

    btn_list = list(win._nav_btns.items()) + list(win._nav_fixed_btns.items())

    # --- E1 结构：每个导航按钮都有图标，且图标名已登记 ---
    missing = []
    for key, btn in btn_list:
        name = btn.property("iconName")
        if not name or not icons.has_icon(name) or btn.icon().isNull():
            missing.append((key, name))
    check(f"E1 全部 {len(btn_list)} 个导航按钮都有已登记的图标",
          not missing, f"缺：{missing}")

    # --- E2 文案里没有 emoji 残留 ---
    leftovers = []
    for key, btn in btn_list:
        bad = [ch for ch in btn.text() if ch in EMOJI_SAMPLE]
        if bad:
            leftovers.append((key, btn.text(), bad))
    check("E2 导航按钮文案已无 emoji 残留", not leftovers, f"残留：{leftovers}")

    # --- E3 插件页：emoji 标题被剥离 + 用通用占位图标 ---
    plug_btn = win._nav_btns.get("plugin:demo")
    check("E3 插件页标题剥离 emoji（'🤖 演示插件' → '演示插件'）",
          plug_btn is not None and plug_btn.text() == "演示插件",
          f"实际 {getattr(plug_btn, 'text', lambda: None)()!r}")
    check("E3 插件页用通用占位图标 plugin",
          plug_btn is not None
          and plug_btn.property("iconName") == icons.PLUGIN_PAGE_ICON)

    # --- E4 图标取色 = 主题 token（Off 态）---
    nav_btn = win._nav_btns["fragments"]
    img_off = nav_btn.icon().pixmap(QSize(16, 16), QIcon.Mode.Normal,
                                    QIcon.State.Off).toImage()
    check(f"E4 Off 态图标取色 = text_secondary（{off}）",
          color_count(img_off, off) > 0,
          f"命中 {color_count(img_off, off)} 像素")

    # --- E5 选中态取主色（QIcon.State.On）---
    img_on = nav_btn.icon().pixmap(QSize(16, 16), QIcon.Mode.Normal,
                                   QIcon.State.On).toImage()
    check(f"E5 On 态图标取色 = primary（{on}）",
          color_count(img_on, on) > 0,
          f"命中 {color_count(img_on, on)} 像素")

    # --- E6 真窗口像素：选中项与未选中项的图标带取色确实不同 ---
    # ⚠ 不要用"同一个按钮 setChecked(True/False)"做对照：导航按钮挂在
    #   独占 QButtonGroup 里，且 fragments 正是当前页 —— 程序化取消选中
    #   不会生效，两次 grab 拿到的是同一张图（假失败）。改用两个不同按钮。
    cur_btn = win._nav_btns["fragments"]      # 当前页 → 已选中
    other_btn = win._nav_btns["tasks"]        # 未选中
    pump(app, 300)

    def band(b):
        return QRectF(20, 0, 16, b.height()).toRect()

    cur_on = _rect_color_count(cur_btn.grab().toImage(), band(cur_btn), on, tol=12)
    oth_on = _rect_color_count(other_btn.grab().toImage(), band(other_btn), on, tol=12)
    oth_off = _rect_color_count(other_btn.grab().toImage(), band(other_btn), off, tol=12)
    check("E6 选中项图标带出现主色、未选中项不出现主色",
          cur_on > 0 and oth_on == 0,
          f"选中={cur_on} 未选中={oth_on}")
    check("E6 未选中项图标带出现次要文字色（确实是按主题取色的图标）",
          oth_off > 0, f"命中 {oth_off}")

    # --- F 截图 ---
    os.makedirs(OUT_DIR, exist_ok=True)
    win._switch_page(0)
    pump(app, 600)
    p1 = os.path.join(OUT_DIR, f"icons-{theme}-01-sidebar.png")
    win.grab().save(p1)
    print(f"     [OK] {p1}")

    # 各页面标题（三种排版：左对齐行 / 独立行 / 居中行）
    for idx, tag in ((1, "taskpage"), (6, "settingspage"), (8, "helppage")):
        win._switch_page(idx)
        pump(app, 500)
        p = os.path.join(OUT_DIR, f"icons-{theme}-01-{tag}.png")
        win.grab().save(p)
        print(f"     [OK] {p}")

    # 侧栏局部放大图（图标细节更清楚）
    nav_area = win._nav_area
    p2 = os.path.join(OUT_DIR, f"icons-{theme}-02-navzoom.png")
    nav_area.grab().save(p2)
    print(f"     [OK] {p2}")

    # 图标对照板（11 个图标 × 3 档尺寸，直接渲在玻璃底上）
    p3 = os.path.join(OUT_DIR, f"icons-{theme}-03-bench.png")
    _save_bench(theme, p3)
    print(f"     [OK] {p3}")

    return win, config


def _rect_color_count(img, rect, color, tol=TOL) -> int:
    want = QColor(color)
    n = 0
    for y in range(max(0, rect.top()), min(img.height(), rect.bottom() + 1)):
        for x in range(max(0, rect.left()), min(img.width(), rect.right() + 1)):
            c = img.pixelColor(x, y)
            if c.alpha() < 128:
                continue
            if (abs(c.red() - want.red()) <= tol
                    and abs(c.green() - want.green()) <= tol
                    and abs(c.blue() - want.blue()) <= tol):
                n += 1
    return n


def _save_bench(theme, path):
    colors = get_colors(theme)
    icon_names = list(icons.icon_names())
    sizes = (16, 24, 32)
    cell_w, cell_h = 150, 52
    pm = QPixmap(cell_w * len(sizes) + 120, cell_h * len(icon_names) + 16)
    pm.fill(QColor(colors["panel_bg"]) if "panel_bg" in colors
            else QColor(colors["bg"]))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    f = p.font()
    f.setPointSize(9)
    p.setFont(f)
    for r, name in enumerate(icon_names):
        y = 8 + r * cell_h
        p.setPen(QColor(colors["text"]))
        p.drawText(6, y + 30, name)
        for c, size in enumerate(sizes):
            box = QRectF(120 + c * cell_w, y + (cell_h - size) / 2.0, size, size)
            icon_render.paint_icon(p, name, box, colors["text"])
    p.end()
    pm.save(path)


# ====================================================================
# H. 各面板页标题图标
# ====================================================================
PAGE_TITLES = [
    ("_page_fragments", "fragments"),
    ("_page_tasks", "tasks"),
    ("_page_notes", "notes"),
    ("_page_knowledge", "knowledge"),
    ("_page_assets", "assets"),
    ("_page_nav", "nav"),
    ("_page_plugins", "plugins"),
    ("_page_settings", "settings"),
    ("_page_app_launcher", "apps"),
    ("_page_help", "help"),
]


def section_panel_titles(app, win):
    print("== H. 各面板页标题图标 ==")
    from PyQt6.QtWidgets import QLabel

    from src.controls import PageTitle

    problems = []
    for attr, want_icon in PAGE_TITLES:
        panel = getattr(win, attr, None)
        if panel is None:
            problems.append((attr, "面板不存在"))
            continue
        # 标题行是各面板 _build_ui 里的局部变量，没有对外属性 —— 用类型
        # 查找即可覆盖，不必为了可测性在每个面板加一个平行属性。
        found = panel.findChildren(PageTitle)
        if len(found) != 1:
            problems.append((attr, "PageTitle 数量 = %d" % len(found)))
            continue
        row = found[0]
        if row.icon_name != want_icon:
            problems.append((attr, "图标 %r ≠ %r" % (row.icon_name, want_icon)))
        elif row.label.objectName() != "pageTitle":
            problems.append((attr, "文字标签 objectName 被改成了 %r"
                             % row.label.objectName()))
        elif any(ch in EMOJI_SAMPLE for ch in row.text()):
            problems.append((attr, "标题仍有 emoji：%r" % row.text()))
    check("H1 10 个页面标题：PageTitle + 正确图标 + 文字标签仍挂 pageTitle",
          not problems, f"问题：{problems}")

    labels = win.findChildren(QLabel, "pageTitle")
    check("H2 全窗 QLabel#pageTitle 仍在（QSS 命中口径零变化）",
          len(labels) >= 10, f"找到 {len(labels)} 个")

    # --- 标题图标跟随主题（真像素：透明底渲染图标小部件本身）---
    row = win._page_fragments.findChildren(PageTitle)[0]
    light_txt = get_colors("light")["text"]
    dark_txt = get_colors("dark")["text"]

    win._theme = "light"
    win._apply_theme()
    win.theme_changed.emit("light")
    pump(app, 250)
    m_light = mean_opaque_color(render_transparent(row.icon).toImage())
    attr_light = row.icon.color

    win._theme = "dark"
    win._apply_theme()
    win.theme_changed.emit("dark")
    pump(app, 250)
    m_dark = mean_opaque_color(render_transparent(row.icon).toImage())
    attr_dark = row.icon.color

    check("H3 light：标题图标取色属性 == light 的 text",
          _same_color(attr_light, light_txt),
          f"{attr_light.name()} vs {light_txt}")
    check("H3 dark：标题图标取色属性 == dark 的 text",
          _same_color(attr_dark, dark_txt),
          f"{attr_dark.name()} vs {dark_txt}")
    check("H4 light：标题图标**真画出**的笔画色更接近 light 的 text",
          m_light is not None
          and color_dist(m_light, rgb_of(light_txt))
          < color_dist(m_light, rgb_of(dark_txt)),
          f"均值={m_light} light={rgb_of(light_txt)} dark={rgb_of(dark_txt)}")
    check("H4 dark：标题图标**真画出**的笔画色更接近 dark 的 text",
          m_dark is not None
          and color_dist(m_dark, rgb_of(dark_txt))
          < color_dist(m_dark, rgb_of(light_txt)),
          f"均值={m_dark}")


# ====================================================================
# G. 换主题后图标取色跟随
# ====================================================================
def section_theme_switch(app, win):
    print("== G. 换主题后导航图标取色跟随 ==")
    light_off = get_colors("light")["text_secondary"]
    dark_off = get_colors("dark")["text_secondary"]
    # ⚠ 两个主题的 text_secondary 只差 ~13/通道（#8B96A3 vs #98A2AE），
    #   比任何能可靠匹配抗锯齿笔画的容差都窄 —— 所以本组**不能用"数像素"**，
    #   必须比"均值色离哪个参考色更近"。这是一次实测踩坑：最初用 tol=32
    #   去数 dark 色像素，在 light 图标上照样数出 92 个假阳性。
    check("两个主题的 text_secondary 本身不同（否则本组断言无意义）",
          light_off != dark_off)

    nav_btn = win._nav_btns["fragments"]

    def mean_of():
        img = nav_btn.icon().pixmap(QSize(16, 16), QIcon.Mode.Normal,
                                    QIcon.State.Off).toImage()
        return mean_opaque_color(img)

    win._theme = "light"
    win._apply_theme()
    pump(app, 300)
    m_light = mean_of()

    win._theme = "dark"
    win._apply_theme()
    pump(app, 300)
    m_dark = mean_of()

    check("light：图标均值色更接近 light 的 text_secondary",
          m_light is not None
          and color_dist(m_light, rgb_of(light_off))
          < color_dist(m_light, rgb_of(dark_off)),
          f"均值={m_light} light={rgb_of(light_off)} dark={rgb_of(dark_off)}")
    check("dark：图标均值色更接近 dark 的 text_secondary",
          m_dark is not None
          and color_dist(m_dark, rgb_of(dark_off))
          < color_dist(m_dark, rgb_of(light_off)),
          f"均值={m_dark}")
    check("两次切换后图标确实重绘（均值色发生了变化）",
          m_light is not None and m_dark is not None
          and color_dist(m_light, m_dark) > 1.0,
          "Δ=%s" % (None if (m_light is None or m_dark is None)
                    else round(color_dist(m_light, m_dark), 2)))


# ====================================================================
# H. P1 动作按钮（IconButton）真窗口接线
# ====================================================================
def section_action_buttons(app, win):
    print("== H. P1 动作按钮（IconButton）真窗口接线 ==")
    from src.controls import IconButton
    from src.icons import strip_leading_emoji

    btns = win.findChildren(IconButton)
    check("真窗口里 IconButton ≥ 18（标题栏 4 + Stepper ±2 + 各面板动作钮）",
          len(btns) >= 18, f"实际 {len(btns)}")

    def pm_of(b, mode=QIcon.Mode.Normal, state=QIcon.State.Off):
        sz = b._icon_size
        return b.icon().pixmap(QSize(sz, sz), mode, state)

    no_off = [b.icon_name for b in btns if pm_of(b).isNull()]
    check("全部 IconButton 的 Off 位图非空", not no_off, str(no_off[:5]))
    no_more = []
    for b in btns:
        if pm_of(b, state=QIcon.State.On).isNull():
            no_more.append(b.icon_name + ":on")
        if pm_of(b, QIcon.Mode.Disabled).isNull():
            no_more.append(b.icon_name + ":disabled")
    check("On 态与 Disabled 态位图齐全", not no_more, str(no_more[:5]))

    leftover = [(b.icon_name, b.text()) for b in btns
                if b.text() and b.text() != strip_leading_emoji(b.text())]
    check("动作按钮文案无 emoji 前缀残留（P1 迁移完整性）",
          not leftover, str(leftover[:5]))

    check("主题切换钮一钮两态（moon/sun）",
          win._theme_btn.icon_name in ("moon", "sun"),
          win._theme_btn.icon_name)
    check("最大化钮一钮两态（maximize/restore）",
          win._max_btn.icon_name in ("maximize", "restore"),
          win._max_btn.icon_name)

    steppers = sorted(b.icon_name for b in btns if b.objectName() == "stepBtn")
    check("Stepper ± 钮图标化（minus/plus，objectName=stepBtn 契约保持）",
          steppers.count("minus") >= 1 and steppers.count("plus") >= 1,
          str(steppers))

    # 换主题后动作按钮取色跟随（判据同 G 段：均值色离哪个参考色更近）
    probe = win._page_settings._upd_btn     # icon_size=14 的文字+图标钮
    light_off = get_colors("light")["text_secondary"]
    dark_off = get_colors("dark")["text_secondary"]

    def mean_of():
        return mean_opaque_color(pm_of(probe).toImage())

    win._theme = "light"
    win._apply_theme()
    win.theme_changed.emit("light")
    pump(app, 250)
    m_light = mean_of()
    win._theme = "dark"
    win._apply_theme()
    win.theme_changed.emit("dark")
    pump(app, 250)
    m_dark = mean_of()

    check("light：动作钮图标均值色更接近 light 的 text_secondary",
          m_light is not None
          and color_dist(m_light, rgb_of(light_off))
          < color_dist(m_light, rgb_of(dark_off)),
          f"均值={m_light}")
    check("dark：动作钮图标均值色更接近 dark 的 text_secondary",
          m_dark is not None
          and color_dist(m_dark, rgb_of(dark_off))
          < color_dist(m_dark, rgb_of(light_off)),
          f"均值={m_dark}")

    # 双主题真截图（动作按钮集中在标题栏与各页工具栏，整窗截即covers）
    os.makedirs(OUT_DIR, exist_ok=True)
    shots = []
    for theme in ("light", "dark"):
        win._theme = theme
        win._apply_theme()
        win.theme_changed.emit(theme)
        pump(app, 250)
        path = os.path.join(OUT_DIR, f"icons-actions-{theme}.png")
        win.grab().toImage().save(path)
        shots.append(path)
    check("双主题动作按钮截图已保存",
          all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in shots),
          str(shots))


def main():
    app = QApplication(sys.argv)
    loaded = load_cjk_font(app)
    print(f"已挂字体：{loaded}（刻意不含 emoji / 符号字体）")
    print()

    icon_render.clear_cache()
    section_render_matrix()
    print()

    win = None
    for theme in ("light", "dark"):
        w, _ = section_main_window(app, theme)
        if win is None:
            win = w
        print()

    section_theme_switch(app, win)
    print()

    section_panel_titles(app, win)
    print()

    section_action_buttons(app, win)
    print()

    print(f"共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    print(f"（截图见 {OUT_DIR}\\icons-*.png）")

    win.deleteLater()
    app.processEvents()
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
