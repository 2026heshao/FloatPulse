# -*- coding: utf-8 -*-
"""说明页内部分类导航（MainWindow._build_help_page）单元测试。

背景（2026-10-02）：说明页从「13 个章节挤一条长滚动」重构为与设置页
同款「左侧分类导航 + 右侧分类页」，每章独立滚动；同日正文观感升级
（真列表 + [[热键]] 键帽）并补齐「截图钉屏 / 插件中心 / 内置插件」
三章至 16 章节（17 个分类含总览）。正文 HTML 由 _help_html() 整篇
按 <h3> 运行时切片（_help_section_html），[[…]] 键帽与主题色在
_decorate_help_html 统一注入，测试重心是「壳」「两表一致」与装饰：

  1. HELP_CATEGORIES 常量完整性（key 唯一 / 图标已登记 / 总览在首位）
  2. 两表一致：每个非总览分类的名称都能在正文里找到同字 <h3> 标题
     （左栏名称改了字、正文没改 → 切片落空页面变白，本组断言拦住）
  3. 切片语义：总览 = 首个 <h3> 之前的导语段且不含 h3；每章含自己的
     h3 与代表性文案、不含别的章节标题（防串页）
  4. 装饰语义：[[键位]] → 等宽键帽、未闭合保留、h3 染主色
  5. 真窗构建：按钮数 / stack 页数 / objectName 与图标名 / 互斥切换 /
     未知 key 容错 / 正文取色与键帽覆盖全部分类页
"""

import os
import sys
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel           # noqa: E402

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
        colors = {"bg_level2": "#EEE", "text": "#111", "primary": "#0F6E56"}
        html = MainWindow._decorate_help_html("<h3>悬浮球</h3>", colors)
        assert '<h3><span style="color:#0F6E56' in html
        assert html.endswith("</span></h3>")

    def test_empty_section_renders_empty(self):
        assert MainWindow._decorate_help_html("", {}) == ""

    def test_unknown_key_returns_empty(self):
        assert MainWindow._help_section_html("不存在") == ""
        assert MainWindow._help_section_html("") == ""


# ====================================================================
# 4. 真窗构建与切换（test_side_split 同款施工配方）
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
    yield w
    w.deleteLater()


class TestHelpNavPanel:
    def test_builder_registers_rail_and_stack(self, window):
        assert window._help_stack.count() == len(CATS)
        assert len(window._help_btns) == len(CATS)
        assert 8 in window._real_pages   # ensure 路径已登记真页

    def test_nav_buttons_carry_category_contract(self, window):
        for key, icon, label in CATS:
            btn = window._help_btns[key]
            assert btn.objectName() == "settingsNavBtn"
            assert btn.icon_name == icon
            assert label in btn.text()

    def test_initial_category_is_first(self, window):
        assert window._help_stack.currentIndex() == 0
        assert window._help_btns[KEYS[0]].isChecked()

    def test_show_category_switches_exclusively(self, window):
        window.show_help_category("tasks")
        assert window._help_stack.currentIndex() == window._help_index["tasks"]
        assert window._help_btns["tasks"].isChecked()
        assert not window._help_btns[KEYS[0]].isChecked()

    def test_show_category_unknown_key_noop(self, window):
        cur = window._help_stack.currentIndex()
        window.show_help_category("不存在")
        window.show_help_category("")
        window.show_help_category(None)
        assert window._help_stack.currentIndex() == cur

    def test_stack_pages_carry_decorated_section(self, window):
        """stack 第 i 页正文 == 该分类切片经 _decorate_help_html 装饰后
        的输出（页序与左栏严格对齐；装饰以 _help_raw 缓存为源，
        颜色用当前主题，与 builder 末尾 _apply_help_content_color 同参）。"""
        from src.theme import get_colors
        colors = get_colors(window._theme)
        for idx, (key, _icon, _label) in enumerate(CATS):
            scroll = window._help_stack.widget(idx)
            labels = scroll.findChildren(QLabel)
            assert len(labels) == 1, "每章一页一正文"
            expected = window._decorate_help_html(
                window._help_raw[key], colors)
            assert labels[0].text() == expected, (
                "第 %d 页（%s）正文与装饰输出不一致" % (idx, key))

    def test_theme_color_reaches_every_section(self, window):
        window._apply_help_content_color()
        assert len(window._help_contents) == len(CATS)
        for content in window._help_contents:
            assert "color:" in content.styleSheet()
            assert "background: transparent" in content.styleSheet()

    def test_keycap_markup_rendered_not_literal(self, window):
        """热键页渲染后不许残留 [[ 字面标记（中间态回归闸）。"""
        idx = window._help_index["hotkeys"]
        labels = window._help_stack.widget(idx).findChildren(QLabel)
        assert "[[" not in labels[0].text()
        assert "background-color:" in labels[0].text()   # 键帽行内样式已注入
