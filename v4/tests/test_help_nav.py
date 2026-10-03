# -*- coding: utf-8 -*-
"""说明页目录导航（MainWindow._build_help_page）单元测试。

背景（2026-10-02）：说明页从「13 个章节挤一条长滚动」重构为「左侧分类
导航 + 右侧分类页」；2026-10-03 目录化：17 枚 settingsNavBtn 按钮换成
6 组纯文字目录（TOC），右侧 17 章合并为**单个 QTextBrowser 长滚动**——
点目录跳章（动画滚动条，QTextBrowser.scrollToAnchor 只会瞬跳）、滚动
正文时目录高亮跟随（scrollspy，视口顶部所在章节）。正文 HTML 仍由
_help_html() 整篇按 <h3> 运行时切片（_help_section_html），[[…]] 键帽
与主题色在 _decorate_help_html 统一注入，测试重心是「壳」「两表一致」
「分组」与装饰：

  1. HELP_CATEGORIES 常量完整性（key 唯一 / 图标已登记 / 总览在首位）
  2. 两表一致：每个非总览分类的名称都能在正文里找到同字 <h3> 标题
  3. 切片语义：总览 = 首个 <h3> 之前的导语段且不含 h3；每章含自己的
     h3 与代表性文案、不含别的章节标题（防串页）
  4. 装饰语义：[[键位]] → 等宽键帽、未闭合保留、h3 染主色 + 章间距与
     分隔线（细行模拟，Qt 富文本不支持块级 border / <hr>）
  5. 目录分组：HELP_TOC_GROUPS 6 组，并集 = 17 键、拼接序 = 主表键序
  6. 真窗构建：TOC 目录项 / 单浏览器 / 互斥高亮 / 跳章落位（reduce
     瞬时 + 正常动画收尾）/ scrollspy 跟随 / 未知 key 容错 / 主题重渲染
"""

import os
import sys
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest                            # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel          # noqa: E402
from PyQt6.QtWidgets import QPushButton, QFrame, QWidget  # noqa: E402

from src import motion                                    # noqa: E402
from src.clipboard_monitor import ClipboardMonitor        # noqa: E402
from src.config import ConfigManager                      # noqa: E402
from src.docx_manager import DocxManager                  # noqa: E402
from src.fragment_manager import FragmentManager          # noqa: E402
from src.icons import has_icon                            # noqa: E402
from src.main_window import MainWindow                    # noqa: E402
from src.note_manager import NoteManager                  # noqa: E402
from src.task_manager import TaskManager                  # noqa: E402
from src.temp_asset_manager import TempAssetManager       # noqa: E402

CATS = MainWindow.HELP_CATEGORIES
KEYS = [c[0] for c in CATS]
LABELS = [c[2] for c in CATS]


# ====================================================================
# 1-2. 常量完整性与两表一致（纯逻辑，不需要 QApplication）
# ====================================================================
class TestHelpCategories:
    def test_keys_unique_nonempty(self):
        assert len(KEYS) == len(set(KEYS))
        assert all(KEYS)

    def test_entries_are_key_icon_label(self):
        for key, icon, label in CATS:
            assert isinstance(key, str) and key
            assert isinstance(icon, str) and icon
            assert isinstance(label, str) and label

    def test_icons_all_registered(self):
        """左栏图标全部取自 icons.py 既有字形（本重构零新增图标）。"""
        for _key, icon, _label in CATS:
            assert has_icon(icon), "分类图标未登记：%r" % icon

    def test_overview_is_first(self):
        assert KEYS[0] == "overview"

    def test_seventeen_categories(self):
        # 总览 + 16 个正文章节（与 _help_html() 的 h3 数量一致，见下）
        assert len(CATS) == 17

    def test_labels_unique(self):
        """章节标题互不相同 —— 切片按标题做键，撞名会静默并页。"""
        assert len(LABELS) == len(set(LABELS))

    def test_every_label_matches_a_h3_in_html(self):
        """两表一致：左栏名称必须逐字命中正文 <h3> 标题。"""
        html = MainWindow._help_html()
        for key, _icon, label in CATS:
            if key == "overview":
                continue
            assert "<h3>%s</h3>" % label in html, (
                "分类 %r 的名称在正文里找不到 <h3>%s</h3>" % (key, label))


# ====================================================================
# 3. 切片语义（纯逻辑）
# ====================================================================
class TestHelpSections:
    def test_overview_is_intro_without_h3(self):
        ov = MainWindow._help_section_html("overview")
        assert "悬浮球效率工具" in ov
        assert "<h3>" not in ov, "总览应只含首个 h3 之前的导语段"

    def test_section_starts_with_own_h3(self):
        for key, _icon, label in CATS:
            if key == "overview":
                continue
            assert MainWindow._help_section_html(key).startswith(
                "<h3>%s</h3>" % label)

    def test_section_anchor_copy_stays_in_place(self):
        """每章一枚代表性文案：内容整段搬页时不许串章、不许丢段。"""
        anchors = {
            "hotkeys": "Ctrl+Alt+K",
            "ball": "吸附到最近的屏幕边缘",
            "card": "七个页签",
            "fragments": "合并选中",
            "tasks": "逾期 → 今天",
            "notes": "标题跟随内容",
            "knowledge": "知识库.docx",
            "assets": "打开素材文件夹",
            "nav": "添加至网址导航",
            "apps": "管理软件列表",
            "shot": "松开即钉屏",
            "pluginhub": "重新扫描",
            "builtin": "密码保险箱",
            "tray": "单实例运行",
            "settings": "恢复默认设置",
            "data": "float_data/",
        }
        for key, anchor in anchors.items():
            assert anchor in MainWindow._help_section_html(key), (
                "章节 %r 丢了代表性文案 %r" % (key, anchor))

    def test_no_cross_section_bleed(self):
        for key, _icon, label in CATS:
            if key == "overview":
                continue
            html = MainWindow._help_section_html(key)
            for _k2, _i2, other in CATS:
                if other != label:
                    assert "<h3>%s</h3>" % other not in html

    def test_sections_count_matches_categories(self):
        sections = MainWindow._help_html_sections()
        assert len(sections) == len(CATS)   # overview + 16 个 h3 章节


class TestHelpDecorate:
    """[[…]] 键帽装饰与 h3 主色注入（纯逻辑，不需要 QApplication）。"""

    def test_hotkey_becomes_keycap(self):
        colors = {"bg_level2": "#EEE", "text": "#111", "primary": "#0F6E56"}
        html = MainWindow._decorate_help_html(
            "<li><b>[[Ctrl+W]]</b>：隐藏</li>", colors)
        assert "[[" not in html and "]]" not in html
        assert "Ctrl+W" in html
        assert "background-color:#EEE" in html
        assert "color:#111" in html
        assert "font-family:Consolas" in html

    def test_unclosed_marker_kept_verbatim(self):
        colors = {"bg_level2": "#EEE", "text": "#111", "primary": "#0F"}
        raw = "<p>只有半个 [[ 标记</p>"
        assert MainWindow._decorate_help_html(raw, colors) == raw

    def test_h3_gets_primary_color(self):
        colors = {"bg_level2": "#EEE", "text": "#111", "primary": "#0F6E56",
                  "line": "#E4E2DB"}
        html = MainWindow._decorate_help_html("<h3>悬浮球</h3>", colors)
        assert '<h3 style="margin-top:22px;">' in html   # 章间距（目录化 2026-10-03）
        assert '<span style="color:#0F6E56' in html
        # 标题下 1px 分隔线：透明单元格 border-bottom（Qt 富文本无块级
        # border/<hr>，背景色细行最薄 ~10px 太粗，见 _decorate_help_html）
        assert "</span></h3><table " in html
        assert "border-bottom:1px solid #E4E2DB" in html

    def test_form_tokens_replaced_with_theme_colors(self):
        """2026-10-03 形态 token：提示块底 / 导语灰字 / 前缀主色 / 色块行。"""
        colors = {"bg_level2": "#EEE", "text": "#111", "primary": "#0F6E56",
                  "text_placeholder": "#888", "danger": "#D00", "warn": "#D80"}
        html = MainWindow._decorate_help_html(
            '<td bgcolor="TIPBG"><b style="color:PRIMCOLOR">提示</b>'
            '<span style="color:PHCOLOR">lead</span>'
            '<span style="background-color:DANGERCOLOR">红</span>'
            '<span style="background-color:WARNCOLOR">橙</span></td>', colors)
        for token in ("TIPBG", "PHCOLOR", "PRIMCOLOR",
                      "DANGERCOLOR", "WARNCOLOR"):
            assert token not in html
        assert 'bgcolor="#EEE"' in html
        assert "color:#0F6E56" in html
        assert "color:#888" in html
        assert "background-color:#D00" in html
        assert "background-color:#D80" in html

    def test_empty_section_renders_empty(self):
        assert MainWindow._decorate_help_html("", {}) == ""

    def test_unknown_key_returns_empty(self):
        assert MainWindow._help_section_html("不存在") == ""
        assert MainWindow._help_section_html("") == ""


# ====================================================================
# 4. 真窗构建与切换（test_side_split 同款施工配方）
# ====================================================================
# ====================================================================
# 5. 目录分组（2026-10-03 目录化，纯逻辑）
# ====================================================================
class TestHelpTocGroups:
    """HELP_TOC_GROUPS 双向约束：分组只是展示层，键集与键序归主表管。"""

    def test_six_groups(self):
        assert len(MainWindow.HELP_TOC_GROUPS) == 6

    def test_group_names_nonempty_unique(self):
        names = [name for name, _keys in MainWindow.HELP_TOC_GROUPS]
        assert all(names) and len(names) == len(set(names))

    def test_union_covers_all_keys_exactly_once(self):
        flat = [k for _name, keys in MainWindow.HELP_TOC_GROUPS for k in keys]
        assert len(flat) == len(set(flat)), "同一章节不得出现在两个组"
        assert sorted(flat) == sorted(KEYS)

    def test_member_order_follows_master_table(self):
        """组内键序必须与 HELP_CATEGORIES 严格同序——目录自上而下的
        阅读顺序 = 文档自前而后的章节顺序，错位会让 scrollspy 高亮乱跳。"""
        flat = [k for _name, keys in MainWindow.HELP_TOC_GROUPS for k in keys]
        assert flat == KEYS

    def test_toc_width_constant(self):
        assert MainWindow.HELP_TOC_WIDTH == 168


# ====================================================================
# 6. 真窗构建与切换（test_side_split 同款施工配方：show + pump 激活布局）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _build_window() -> MainWindow:
    tmp = tempfile.mkdtemp(prefix="fp_help_nav_test_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    config = ConfigManager(os.path.join(tmp, "config.json"))
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)
    return MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                      clip, temp_mgr)


@pytest.fixture(scope="module")
def window(qapp):
    w = _build_window()
    # 走真实懒加载入口（builder 直调的返回页无父级会被 GC 连带删光子控件
    # —— PyQt 所有权铁律；ensure 路径把真页插进 stack，所有权归 stack）
    page = w._ensure_page_built(8)
    assert page is not None, "说明页（索引 8）应有懒加载 builder"
    # QTextBrowser 是懒布局：不 show 文档高度为 0、章节定位退化——
    # show 主窗 + 切到说明页 + 泵事件（offscreen 下 show 不立即排布）
    w.show()
    w._stack.setCurrentIndex(8)
    qapp.processEvents()
    yield w
    w.deleteLater()


class TestHelpNavPanel:
    """TOC + 单 QTextBrowser 壳契约（真窗构建，布局已激活）。"""

    def test_builder_registers_toc_and_browser(self, window):
        assert getattr(window, "_help_stack", None) is None  # 旧 stack 已退役
        assert len(window._help_toc_btns) == len(CATS)
        assert window._help_browser is not None
        assert window._help_toc_scroll is not None
        assert 8 in window._real_pages   # ensure 路径已登记真页

    def test_toc_buttons_carry_category_contract(self, window):
        for key, _icon, label in CATS:
            btn = window._help_toc_btns[key]
            assert btn.objectName() == "helpTocItem"
            assert btn.isCheckable()
            assert btn.text() == label

    def test_toc_groups_rendered_in_order(self, window):
        """目录按 6 组分组渲染：组标签与成员按钮按 HELP_TOC_GROUPS 顺序，
        组间 1px hairline 分隔（组数-1 条，首组贴页标题不加）。"""
        rail = window._help_toc_scroll.widget()
        btns = rail.findChildren(QPushButton)
        assert [b.text() for b in btns] == LABELS
        glabels = [lbl.text() for lbl in rail.findChildren(QLabel)
                   if lbl.objectName() == "helpTocGroup"]
        assert glabels == [name for name, _k in MainWindow.HELP_TOC_GROUPS]
        dividers = [f for f in rail.findChildren(QFrame)
                    if f.objectName() == "settingsNavDivider"]
        assert len(dividers) == len(MainWindow.HELP_TOC_GROUPS) - 1
        # 分隔在布局序里先于其后每组的首枚按钮（首组之前无分隔）
        order = [(w.text() if isinstance(w, QPushButton) else w.objectName())
                 for w in rail.findChildren(QWidget)]
        first_div_idx = order.index("settingsNavDivider")
        assert "总览" in order[:first_div_idx]      # 首组成员在第一条分隔之前
        assert order.count("settingsNavDivider") == len(dividers)

    def test_toc_group_header_details(self, window):
        """组标题细节提示（用户反馈 2026-10-03）：每组一枚主色小刻度
        （3×10，helpTocTick——与选中项左缘竖条同语系降一档权重）+
        标签 1.5px 字距（QSS 无 letter-spacing，走 QFont）。"""
        rail = window._help_toc_scroll.widget()
        ticks = [t for t in rail.findChildren(QFrame)
                 if t.objectName() == "helpTocTick"]
        assert len(ticks) == len(MainWindow.HELP_TOC_GROUPS)
        for t in ticks:
            assert (t.width(), t.height()) == (3, 10)
        labels = [lbl for lbl in rail.findChildren(QLabel)
                  if lbl.objectName() == "helpTocGroup"]
        assert len(labels) == len(MainWindow.HELP_TOC_GROUPS)
        for lbl in labels:
            assert abs(lbl.font().letterSpacing() - 1.5) < 1e-6

    def test_browser_carries_full_document(self, window):
        """单浏览器正文 = 17 章合并：章节标题全在、键帽/形态 token 已注入。"""
        plain = window._help_browser.toPlainText()
        for key, _icon, label in CATS:
            if key == "overview":
                continue          # 总览是导语段，正文里本就没有「总览」标题
            assert label in plain, "章节 %r 不在单页正文里" % label
        assert "[[" not in plain          # 键帽已渲染（中间态回归闸）
        assert "TIPBG" not in plain       # 形态 token 不许以原文上墙
        assert window._help_full_html().count('name="ch_') == len(CATS)

    def test_initial_category_is_first(self, window):
        assert window._help_active_key == KEYS[0]
        assert window._help_toc_btns[KEYS[0]].isChecked()

    def test_show_category_unknown_key_noop(self, window):
        prev_key = window._help_active_key
        value = window._help_browser.verticalScrollBar().value()
        for bad in ("不存在", "", None):
            window.show_help_category(bad)
        assert window._help_active_key == prev_key
        assert window._help_browser.verticalScrollBar().value() == value

    def test_reduce_jump_is_instant_and_exclusive(self, window):
        """reduce_motion：跳章瞬时落位（无动画），目录互斥高亮。"""
        motion.set_reduce_motion(True)
        try:
            window.show_help_category("tasks")
            assert window._help_scroll_anim is None
            assert window._help_toc_btns["tasks"].isChecked()
            assert not window._help_toc_btns[KEYS[0]].isChecked()
            window._help_locate_chapters()
            sb = window._help_browser.verticalScrollBar()
            expected = max(0, min(int(window._help_chapter_y["tasks"]) - 6,
                                  sb.maximum()))
            assert sb.value() == expected
        finally:
            motion.set_reduce_motion(False)

    def test_normal_jump_animates_then_settles(self, window):
        """正常节奏：跳章开滚动条动画，收尾后落位到目标章节顶。"""
        window.show_help_category("hotkeys")
        assert window._help_scroll_anim is not None       # 动画在途
        assert window._help_active_key == "hotkeys"       # 高亮先行
        window._help_scrollspy()                          # 动画期 spy 挂起
        assert window._help_active_key == "hotkeys"
        QTest.qWait(motion.MOTION["base"] * 2 + 120)      # 动画收尾
        assert window._help_scroll_anim is None
        window._help_locate_chapters()
        sb = window._help_browser.verticalScrollBar()
        expected = max(0, min(int(window._help_chapter_y["hotkeys"]) - 6,
                              sb.maximum()))
        assert sb.value() == expected

    def test_scrollspy_follows_viewport(self, window):
        """正文滚动 → 视口顶部所在章节 → 目录高亮跟随并互斥。"""
        window._help_locate_chapters()
        ys = window._help_chapter_y
        sb = window._help_browser.verticalScrollBar()
        sb.setValue(int(ys["builtin"]) + 5)
        window._help_scrollspy()
        assert window._help_active_key == "builtin"
        assert window._help_toc_btns["builtin"].isChecked()
        assert not window._help_toc_btns[KEYS[0]].isChecked()
        sb.setValue(0)                                    # 回顶 → 总览
        window._help_scrollspy()
        assert window._help_active_key == KEYS[0]
        assert window._help_toc_btns[KEYS[0]].isChecked()

    def test_theme_rerender_keeps_document(self, window):
        """主题重渲染：键帽/形态 token 仍注入、滚动比例恢复、章位重算。"""
        sb = window._help_browser.verticalScrollBar()
        sb.setValue(sb.maximum())                         # 滚到底
        window._apply_help_content_color()
        plain = window._help_browser.toPlainText()
        assert "[[" not in plain and "TIPBG" not in plain
        assert sb.value() > 0                             # 底部还在底部附近
        window._help_locate_chapters()
        assert len(window._help_chapter_y) == len(CATS)   # 17 章可重定位
