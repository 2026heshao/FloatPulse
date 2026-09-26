# -*- coding: utf-8 -*-
"""
====================================================================
主题系统模块  -  theme
====================================================================
配色字典 + QSS 模板，支持浅色/深色两套主题。

v2 视觉规范（对照 `设计稿/ui-preview.html` 高仿真稿）：
  · 玻璃壳 = 半透明填充 + 顶部高光带 + 1px 上亮下暗描边 + 噪点，
    前四层由 src/glass.py 的 GlassPanel 手绘，QSS 只负责子控件样式
  · 圆角：大窗口 14 / 小卡片 22 / 内嵌面板 12 / 行与按钮 9~10
  · 主色三档渐变：#6FFFE9 → #5BC0BE → #3D9E9C
  · 动效统一 160~220ms、OutCubic；hover 只做视觉，切换靠点击

设计要点：
  1. 用 string.Template + $var 占位符（QSS 中无 $ 符号，安全）
  2. 所有颜色集中在 THEMES 字典；旧键名全部保留，避免其它模块引用失效
  3. get_main_window_qss / get_card_window_qss / get_menu_qss 三套 QSS
====================================================================
"""

from string import Template

from src.constants import DEFAULT_THEME


# ====================================================================
# 配色字典
# ====================================================================
THEMES = {
    "light": {
        "_name": "light",

        # ---- 玻璃壳（由 GlassPanel 使用，见 glass.py）----
        # 说明：没有真模糊时，背景内容会原样透上来与前景文字打架，
        # 因此填充实度必须更高（实机 67% 明显不够用，见 docs/背景适配方案）。
        "glass_fill":        "rgba(255, 255, 255, 224)",   # 容器填充 88%（实机验证：67% 背景会打架）
        "glass_edge":        "rgba(255, 255, 255, 236)",   # 描边：上（亮）
        "glass_edge_bottom": "rgba(16, 32, 48, 20)",       # 描边：下（暗，模拟厚度）
        "glass_highlight":   "rgba(255, 255, 255, 140)",   # 顶部高光带 55%
        "noise_alpha":       10,                            # 噪点强度

        # ---- 内嵌面板（列表/输入框等第二层面）----
        "panel_fill":        "rgba(255, 255, 255, 170)",   # 67%
        "panel_edge":        "rgba(255, 255, 255, 217)",   # 85%
        "hair":              "rgba(16, 32, 48, 18)",       # 分隔线 7%
        "slider_handle":     "#FFFFFF",                    # 滑杆手柄（圆形）

        # ---- 背景（兼容旧引用）----
        "bg":                "#F8F9FA",
        "card_bg":           "rgba(255, 255, 255, 148)",
        "card_bg_solid":     "#FFFFFF",
        "input_bg":          "rgba(255, 255, 255, 132)",
        "menu_bg":           "rgba(255, 255, 255, 232)",
        "list_bg":           "rgba(255, 255, 255, 90)",
        "bg_level2":         "#EEF0F2",

        # ---- 主色三档 ----
        "primary":           "#5BC0BE",
        "primary_lite":      "#6FFFE9",
        "primary_deep":      "#3D9E9C",
        "primary_hover":     "#6FFFE9",
        "primary_pressed":   "#3D9E9C",
        "primary_alpha":     "rgba(91, 192, 190, 0.12)",
        "primary_a08":       "rgba(91, 192, 190, 0.08)",
        "primary_a12":       "rgba(91, 192, 190, 0.12)",
        "primary_a18":       "rgba(91, 192, 190, 0.18)",
        "primary_a30":       "rgba(91, 192, 190, 0.30)",
        "primary_border":    "rgba(91, 192, 190, 0.30)",
        "primary_border_strong": "rgba(91, 192, 190, 0.40)",
        "list_border":       "rgba(91, 192, 190, 0.22)",
        "list_item_hover":   "rgba(91, 192, 190, 0.08)",
        "list_item_selected": "rgba(91, 192, 190, 0.16)",

        # ---- 主色底上的文字色 ----
        # 浅色主色 #5BC0BE 明度偏高：白字对比度仅 2.16:1，必须用深墨（6.76:1）
        "on_primary":        "#1B2B33",
        "on_disabled":       "#6B747E",

        # ---- 文字 ----
        "text":              "#2C3E50",
        "text_secondary":    "#8B96A3",
        "text_placeholder":  "#AAB4BF",
        "text_disabled":     "#C0C8D0",
        "app_name_text":     "#1A1A1A",

        # ---- 危险/成功 ----
        "danger":            "#D6483A",
        "danger_hover":      "#E4593B",
        "danger_alpha":      "rgba(214, 72, 58, 0.10)",
        "danger_border":     "rgba(214, 72, 58, 0.28)",
        "warn":              "#C97A1E",
        "warn_alpha":        "rgba(230, 126, 34, 0.12)",
        "warn_border":       "rgba(230, 126, 34, 0.30)",
        "success":           "#1F8A4C",

        # ---- 日程任务状态色（A2/A3，delegate 自绘取色，QSS 集中于此）----
        "task_overdue":      "#E74C3C",                    # 逾期（浅色）
        "task_today":        "#E67E22",                    # 今日到期
        "task_done":         "#9AA5B1",                    # 已完成（次级灰）
        "task_none":         "#2C3E50",                    # 无截止 / 普通行
        "task_check":        "#1F8A4C",                    # 勾选框对勾/填充
        "undo_bg":           "rgba(44, 62, 80, 235)",      # 撤销条底色
        "undo_text":         "#FFFFFF",                    # 撤销条文字

        # ---- 其它 ----
        "shadow":            "rgba(0, 0, 0, 70)",
        "side_bar_bg":       "transparent",
        "side_bar_btn":      "#8B96A3",
    },

    "dark": {
        "_name": "dark",

        "glass_fill":        "rgba(20, 22, 32, 208)",      # 82%
        "glass_edge":        "rgba(255, 255, 255, 46)",    # 18%
        "glass_edge_bottom": "rgba(0, 0, 0, 120)",
        "glass_highlight":   "rgba(255, 255, 255, 40)",    # 16%
        "noise_alpha":       12,

        "panel_fill":        "rgba(255, 255, 255, 26)",    # 10%
        "panel_edge":        "rgba(255, 255, 255, 44)",    # 17%
        "hair":              "rgba(255, 255, 255, 23)",    # 9%
        "slider_handle":     "#E8ECF2",                    # 滑杆手柄（圆形）

        "bg":                "#1E1E2E",
        "card_bg":           "rgba(20, 22, 32, 158)",
        "card_bg_solid":     "#232536",
        "input_bg":          "rgba(255, 255, 255, 18)",
        "menu_bg":           "rgba(28, 30, 42, 240)",
        "list_bg":           "rgba(255, 255, 255, 10)",
        "bg_level2":         "#2A2A3C",

        "primary":           "#6FFFE9",
        "primary_lite":      "#8BFFF0",
        "primary_deep":      "#4AA8A6",
        "primary_hover":     "#8BFFF0",
        "primary_pressed":   "#4AA8A6",
        "primary_alpha":     "rgba(111, 255, 233, 0.15)",
        "primary_a08":       "rgba(111, 255, 233, 0.08)",
        "primary_a12":       "rgba(111, 255, 233, 0.12)",
        "primary_a18":       "rgba(111, 255, 233, 0.18)",
        "primary_a30":       "rgba(111, 255, 233, 0.30)",
        "primary_border":    "rgba(111, 255, 233, 0.30)",
        "primary_border_strong": "rgba(111, 255, 233, 0.45)",
        "list_border":       "rgba(111, 255, 233, 0.20)",
        "list_item_hover":   "rgba(111, 255, 233, 0.08)",
        "list_item_selected": "rgba(111, 255, 233, 0.18)",

        # ---- 主色底上的文字色 ----
        # 深色主色 #6FFFE9 极浅：白字对比度仅 1.22:1（几乎不可读），必须用深墨（11.9:1）
        "on_primary":        "#1B2B33",
        "on_disabled":       "rgba(255, 255, 255, 180)",

        "text":              "#E4E8EE",
        "text_secondary":    "#98A2AE",
        "text_placeholder":  "#7A8592",
        "text_disabled":     "#5A6170",
        "app_name_text":     "#E4E8EE",

        "danger":            "#E4593B",
        "danger_hover":      "#FF6B5B",
        "danger_alpha":      "rgba(228, 89, 59, 0.14)",
        "danger_border":     "rgba(228, 89, 59, 0.34)",
        "warn":              "#E0A34B",
        "warn_alpha":        "rgba(224, 163, 75, 0.14)",
        "warn_border":       "rgba(224, 163, 75, 0.32)",
        "success":           "#2ECC71",

        # ---- 日程任务状态色（A2/A3，delegate 自绘取色，QSS 集中于此）----
        "task_overdue":      "#FF6B5B",                    # 逾期（深色）
        "task_today":        "#F39C12",                    # 今日到期
        "task_done":         "#8892A0",                    # 已完成（次级灰）
        "task_none":         "#E4E8EE",                    # 无截止 / 普通行
        "task_check":        "#2ECC71",                    # 勾选框对勾/填充
        "undo_bg":           "rgba(40, 44, 58, 242)",      # 撤销条底色
        "undo_text":         "#E4E8EE",                    # 撤销条文字

        "shadow":            "rgba(0, 0, 0, 120)",
        "side_bar_bg":       "transparent",
        "side_bar_btn":      "#98A2AE",
    },
}


# ====================================================================
# 大窗口主 UI QSS
# ====================================================================
_QSS_MAIN_WINDOW = Template("""
* {
    font-family: 'Microsoft YaHei', '微软雅黑';
}

/* 窗口根容器：只负责透明，玻璃壳由 GlassPanel 手绘 */
QWidget#windowRoot {
    background-color: transparent;
}
QWidget#mainWindow {
    background-color: transparent;
}

/* ---- 标题栏 ---- */
QWidget#titleBar {
    background-color: transparent;
    border-bottom: 1px solid $hair;
}
QLabel#titleBarLabel {
    color: $text;
    font-size: 14px;
    font-weight: 600;
}
QLabel#titleBarSub {
    color: $text_placeholder;
    font-size: 11px;
}

/* ---- 导航栏 ---- */
QWidget#sideBar {
    background-color: transparent;
    border-right: 1px solid $hair;
}
QLabel#sideBarTitle {
    color: $text_placeholder;
    font-size: 11px;
    font-weight: 600;
    padding: 0 12px;
}
QPushButton#navBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 8px 12px;
    text-align: left;
    font-size: 13px;
}
QPushButton#navBtn:hover {
    /* hover 只做视觉反馈（背景 + 轻微右移），切页靠点击 */
    background-color: $primary_a08;
    color: $text;
    padding-left: 14px;
}
QPushButton#navBtn:checked {
    background-color: $primary_a18;
    color: $primary;
    border: 1px solid $primary_a30;
    font-weight: 600;
}
QPushButton#navBtn[dragging="true"] {
    /* 拖拽中"提起"：比 hover/checked 更实。
       ⚠ 两条纪律：
       1) 不加 font-weight —— 改字重会改 sizeHint，拖拽中每轮重排都按
          "加粗高度"排布，会把整列推下去；
       2) 本规则不得改动盒模型（padding / border 宽度 / margin）—— sizeHint
          一旦在拖拽期间被算大并被缓存，Qt 不会自动失效。
       （"每轮拖拽后行高 +1px"的真凶其实是全局 `QPushButton:pressed` 的
         margin-top:1px，已在 main_window._clear_nav_drag_lift 用
         "先 setDown(False) 再 polish" 解决，详见该处注释与
         tests/test_nav_drag_invariants.py。）*/
    background-color: $primary_a18;
    color: $primary;
    border: 1px solid $primary;
}
QLabel#sideBarFoot {
    color: $text_placeholder;
    font-size: 11px;
}

/* ---- 内容区 ---- */
QWidget#contentArea {
    background-color: transparent;
}
QLabel#pageTitle {
    color: $text;
    font-size: 17px;
    font-weight: 600;
}
QLabel#countChip {
    color: $primary;
    background-color: $primary_a12;
    border: 1px solid $primary_a30;
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}
QLabel {
    color: $text;
}
QLabel#hintLabel {
    color: $text_placeholder;
    font-size: 11px;
}
QLabel#sectionLabel {
    color: $text;
    font-size: 13px;
    font-weight: 600;
}

/* 设置页分隔线 */
QFrame#settingsSeparator {
    background-color: $hair;
    max-height: 1px;
    min-height: 1px;
    border: none;
    margin: 4px 0px;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: $primary;
    color: $on_primary;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 7px 15px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: $primary_hover;
    color: $on_primary;
}
QPushButton:pressed {
    background-color: $primary_pressed;
    margin-top: 1px;
}
QPushButton:disabled {
    background-color: $text_disabled;
    color: $on_disabled;
}

/* 主按钮：主色三档渐变（与效果图一致） */
QPushButton#primaryBtn {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $primary_lite, stop:0.6 $primary, stop:1 $primary_deep);
    color: $on_primary;
    font-weight: 600;
    border: 1px solid $primary_a30;
    border-radius: 9px;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton#primaryBtn:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $primary_lite, stop:0.45 $primary, stop:1 $primary);
}
QPushButton#primaryBtn:pressed {
    margin-top: 1px;
}

QPushButton#secondaryBtn {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
    border-radius: 9px;
}
QPushButton#secondaryBtn:hover {
    background-color: $primary_a18;
}
QPushButton#secondaryBtn:checked {
    background-color: $primary_a18;
    border: 1px solid $primary;
    color: $primary;
    font-weight: 600;
}
QPushButton#secondaryBtn:pressed {
    margin-top: 1px;
}

QPushButton#dangerBtn {
    background-color: $danger_alpha;
    color: $danger;
    border: 1px solid $danger_border;
}
QPushButton#dangerBtn:hover {
    background-color: $danger;
    color: white;
}

QPushButton#iconBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px;
    font-size: 15px;
}
QPushButton#iconBtn:hover {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
}
QPushButton#iconBtn:pressed {
    background-color: $primary_a18;
}
QPushButton#iconBtn[danger="true"]:hover {
    background-color: $danger_alpha;
    color: $danger;
    border: 1px solid $danger_border;
}

/* ---- 轻提示条（Toast）：底部居中浮层，用于"自动清理 N 条"这类通报 ---- */
QLabel#toastBar {
    background-color: $glass_fill;
    color: $text;
    border: 1px solid $primary_a30;
    border-radius: 12px;
    padding: 10px 18px;
    font-size: 12px;
}

/* ---- 内嵌面板（第二层玻璃）---- */
QWidget#glassCard, QFrame#glassCard {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 12px;
}

/* ---- 设置页分组卡片 ---- */
QWidget#settingsGroup {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 12px;
}
QLabel#fieldLabel {
    color: $text_secondary;
    font-size: 13px;
}
QLabel#settingTitle {
    color: $text;
    font-size: 13px;
    font-weight: 600;
}
QLabel#settingDesc {
    color: $text_secondary;
    font-size: 12px;
}
QLabel#valueChip {
    color: $primary;
    background-color: $primary_a12;
    border: 1px solid $primary_a30;
    border-radius: 7px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
}

/* ---- 步进器（设置页的「调大调小」按钮）---- */
QPushButton#stepBtn {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 9px;
    color: $text_secondary;
    font-size: 17px;
    font-weight: 700;
    padding: 0px;
}
QPushButton#stepBtn:hover {
    background-color: $primary_a18;
    border: 1px solid $primary_a30;
    color: $primary;
}
QPushButton#stepBtn:pressed {
    background-color: $primary_a30;
    border: 1px solid $primary_border_strong;
    color: $primary;
}
QPushButton#stepBtn:disabled {
    background-color: transparent;
    border: 1px solid $hair;
    color: $text_disabled;
}
QLineEdit#stepValue {
    background-color: transparent;
    border: none;
    padding: 0px;
    color: $text;
    font-size: 14px;
    font-weight: 600;
}
QLineEdit#stepValue:focus {
    border: none;
    border-bottom: 1px solid $primary;
}

/* ---- 滑杆：细槽 + 圆形手柄 ---- */
QSlider {
    background: transparent;
    min-height: 22px;
}
QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: $hair;
}
QSlider::sub-page:horizontal {
    height: 6px;
    border-radius: 3px;
    background: $primary_a30;
}
QSlider::add-page:horizontal {
    height: 6px;
    background: transparent;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0px;
    border-radius: 7px;
    background: $slider_handle;
    border: 1px solid $primary_a30;
}
QSlider::handle:horizontal:hover {
    border: 1px solid $primary;
}
QSlider::handle:horizontal:pressed {
    background: $primary;
    border: 1px solid $primary;
}
QSlider::handle:horizontal:disabled {
    background: $hair;
    border: 1px solid $hair;
}

/* ---- 输入控件 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QDateEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 9px;
    padding: 6px 10px;
    color: $text;
    font-size: 13px;
    selection-background-color: $list_item_selected;
    selection-color: $text;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
    border: 1px solid $primary_a30;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid $primary;
    background-color: $panel_fill;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    color: $text_disabled;
}

QComboBox::drop-down {
    width: 20px;
    border: none;
}
QComboBox QAbstractItemView {
    background-color: $menu_bg;
    border: 1px solid $primary_a30;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: $list_item_selected;
    color: $text;
    outline: none;
}

/* ---- 列表 ---- */
QListWidget {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: 12px;
    padding: 6px;
    color: $text;
    font-size: 13px;
    outline: none;
}
QListWidget::item {
    padding: 9px 11px;
    border-radius: 9px;
}
QListWidget::item:hover {
    background-color: $primary_a08;
}
QListWidget::item:selected {
    background-color: $list_item_selected;
    color: $text;
}

/* ---- 树形列表（全局搜索结果；QTreeWidget 是 QTreeView 子类，需单独给样式）---- */
QTreeWidget {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: 12px;
    padding: 4px;
    color: $text;
    font-size: 13px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeWidget::item {
    padding: 7px 8px;
    min-height: 18px;
    border-radius: 8px;
}
QTreeWidget::item:hover {
    background-color: $primary_a08;
}
QTreeWidget::item:selected {
    background-color: $list_item_selected;
    color: $text;
}
QTreeWidget::branch {
    background: transparent;
}
QTreeWidget QHeaderView::section {
    padding: 6px 10px;
}

/* ---- 表格 ---- */
QTableWidget {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: 12px;
    gridline-color: transparent;
    color: $text;
    font-size: 13px;
    outline: none;
}
QTableWidget QWidget {
    background-color: transparent;
}
QTableWidget::item {
    padding: 6px;
    background-color: transparent;
    border-radius: 8px;
}
QTableWidget::item:hover {
    background-color: $primary_a08;
}
QTableWidget::item:selected {
    background-color: $list_item_selected;
    color: $text;
}
QHeaderView {
    background-color: transparent;
    border: none;
}
QHeaderView::section {
    background-color: $primary_a12;
    color: $primary;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid $hair;
    font-weight: 600;
    font-size: 12px;
}
QHeaderView::section:last {
    border-right: none;
}
QPushButton#tableOpenBtn {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
    border-radius: 7px;
    padding: 2px 10px;
    font-size: 12px;
    min-width: 50px;
    max-width: 70px;
}
QPushButton#tableOpenBtn:hover {
    background-color: $primary;
    color: $on_primary;
}

/* ---- 网址导航行（拖拽动画列表，替代导航页的 QTableWidget）---- */
QFrame#navRow {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 10px;
}
QFrame#navRow:hover {
    background-color: $primary_a08;
}
QFrame#navRow[dragging="true"] {
    background-color: $card_bg_solid;
    border: 1px solid $primary;
}
QLabel#navRowTitle {
    color: $text;
    font-size: 13px;
    font-weight: 600;
    background: transparent;
    border: none;
}
QLabel#navRowUrl {
    color: $text_secondary;
    font-size: 12px;
    background: transparent;
    border: none;
}

/* ---- 复选框 ---- */
QCheckBox {
    color: $text;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1px solid $primary_a30;
    background-color: $panel_fill;
}
QCheckBox::indicator:hover {
    border: 1px solid $primary;
}
QCheckBox::indicator:checked {
    background-color: $primary;
    border: 1px solid $primary;
}

/* ---- 滚动条：纵向滑块常驻显形（用户要求所有页面可见），悬停加深；横向保持隐形 ---- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $primary_a18;
    border-radius: 4px;
    min-height: 30px;
    margin: 0 2px;
}
QScrollBar::handle:vertical:hover {
    background: $primary_a30;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: transparent;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: $primary_a30; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ---- 知识库长列表 ---- */
QListWidget#kbList QScrollBar:vertical {
    width: 12px;
    margin: 2px;
}
QListWidget#kbList QScrollBar::handle:vertical {
    background: $primary_a18;
    border-radius: 5px;
    min-height: 30px;
    margin: 0 2px;
}
QListWidget#kbList QScrollBar::handle:vertical:hover {
    background: $primary_a30;
}

/* ---- 菜单 ---- */
QMenu {
    background-color: $menu_bg;
    border: 1px solid $primary_a30;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 28px 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    color: $text;
}
QMenu::item:selected {
    background-color: $primary_a12;
    color: $primary;
}
QMenu::separator {
    height: 1px;
    background: $hair;
    margin: 4px 8px;
}

/* ---- 其它 ---- */
QStatusBar {
    background-color: transparent;
    color: $text_secondary;
    border-top: 1px solid $hair;
}
QSplitter::handle { background-color: $hair; }
QSplitter::handle:hover { background-color: $primary; }
QGroupBox {
    border: 1px solid $panel_edge;
    border-radius: 12px;
    margin-top: 12px;
    padding-top: 10px;
    color: $text;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: $primary;
}
QTabWidget::pane {
    border: 1px solid $panel_edge;
    border-radius: 12px;
    background-color: transparent;
}
QTabBar::tab {
    background-color: transparent;
    color: $text_secondary;
    padding: 8px 16px;
    border-radius: 9px;
    margin-right: 4px;
}
QTabBar::tab:hover { background-color: $primary_a08; color: $primary; }
QTabBar::tab:selected {
    background-color: $primary_a12;
    color: $primary;
    font-weight: 600;
}
QProgressBar {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 6px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $primary;
    border-radius: 5px;
}

/* ---- 设置页 / 通用滚动区（视口透明，避免透明窗口下渲染成黑色）---- */
QScrollArea, QScrollArea > QWidget {
    background-color: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ---- 撤销提示条（UndoBar，浮动子控件；行内容由 delegate 自绘）---- */
QWidget#undoBar {
    background-color: $undo_bg;
    border: 1px solid $primary_a30;
    border-radius: 10px;
}
QLabel#undoBarLabel {
    color: $undo_text;
    font-size: 12px;
    background: transparent;
}
QPushButton#undoUndoBtn {
    background-color: transparent;
    color: $primary_lite;
    border: 1px solid $primary_lite;
    border-radius: 7px;
    padding: 3px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#undoUndoBtn:hover {
    background-color: $primary_lite;
    color: $on_primary;
}
""")
_QSS_MAIN_WINDOW = _QSS_MAIN_WINDOW  # 保持名称稳定，便于外部引用


# ====================================================================
# 小卡片 QSS
# ====================================================================
_QSS_CARD_WINDOW = Template("""
QWidget#cardContainer {
    background-color: transparent;
    border-radius: 22px;
}

/* ---- 左侧 Tab 栏 ---- */
QWidget#sideTabBar {
    background-color: transparent;
    border-right: 1px solid $primary_a30;
}
QPushButton#sideTabIconBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: 10px;
    font-size: 17px;
    padding: 0px;
}
QPushButton#sideTabIconBtn:hover {
    background-color: $primary_a12;
    color: $primary;
}
QPushButton#sideTabIconBtn:checked {
    background-color: $primary_a18;
    color: $primary;
    border: 1px solid $primary_a30;
}

/* ---- 文本 ---- */
QLabel#titleLabel {
    color: $primary;
    font-size: 13px;
    font-weight: 600;
}
QLabel#contentLabel {
    color: $text;
    font-size: 15px;
}
QLabel#hintLabel {
    color: $text_placeholder;
    font-size: 11px;
}
QLabel {
    color: $text;
}

/* ---- 按钮 ---- */
QPushButton#modeBtn {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
    border-radius: 11px;
    padding: 5px 14px;
    font-size: 12px;
}
QPushButton#modeBtn:checked {
    background-color: $primary;
    color: $on_primary;
    border: 1px solid $primary;
}
QPushButton#cardCloseBtn {
    background-color: $danger_alpha;
    color: $danger;
    border: 1px solid $danger_border;
    border-radius: 11px;
    font-size: 12px;
    font-weight: bold;
    padding: 0px;
}
QPushButton#cardCloseBtn:hover {
    background-color: $danger;
    color: white;
}
QPushButton#nextBtn {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $primary_lite, stop:0.6 $primary, stop:1 $primary_deep);
    color: $on_primary;
    border: none;
    border-radius: 13px;
    padding: 9px 24px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#nextBtn:hover {
    background-color: $primary_hover;
    color: $on_primary;
}
QPushButton#nextBtn:pressed {
    background-color: $primary_pressed;
    margin-top: 1px;
}

/* ---- 日程任务 ---- */
QLineEdit#taskInput {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 9px;
    padding: 6px 10px;
    font-size: 13px;
    color: $text;
}
QLineEdit#taskInput:focus {
    border: 1px solid $primary;
}
QDateEdit#taskDate {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 9px;
    padding: 5px 4px;
    font-size: 12px;
    color: $text;
}
QDateEdit#taskDate::drop-down { width: 18px; }
QPushButton#taskAddBtn {
    background-color: $primary;
    color: $on_primary;
    border: none;
    border-radius: 9px;
    padding: 7px 15px;
    font-size: 12px;
}
QPushButton#taskAddBtn:hover {
    background-color: $primary_hover;
    color: $on_primary;
}
QListWidget#taskList {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: 11px;
    padding: 5px;
    font-size: 13px;
    color: $text;
    outline: none;
}
QListWidget#taskList::item {
    padding: 7px 9px;
    border-radius: 8px;
}
QListWidget#taskList::item:hover {
    background-color: $primary_a08;
}
QListWidget#taskList::item:selected {
    background-color: $list_item_selected;
    color: $text;
}
QTextEdit#noteEdit {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: 11px;
    padding: 8px;
    font-size: 13px;
    color: $text;
}
QTextEdit#noteEdit:focus {
    border: 1px solid $primary;
}

/* ---- 滚动区：视口透明，透出玻璃壳（漏掉这条会在深色系统下渲染成黑块）---- */
QScrollArea, QScrollArea > QWidget {
    background-color: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ---- 滚动条：纵向滑块常驻显形，悬停加深 ---- */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $primary_a18;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $primary_a30; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ---- 网址导航（双列卡片：标题+域名副标题） ---- */
QPushButton#navSiteCard {
    background-color: $primary_a12;
    border: 1px solid $primary_a30;
    border-radius: 9px;
    padding: 0px;
}
QPushButton#navSiteCard:hover {
    background-color: $primary_a18;
    border: 1px solid $primary;
}
QPushButton#navSiteCard:pressed {
    background-color: $primary_a30;
}
QLabel#navSiteCardTitle {
    background: transparent;
    color: $text;
    font-size: 13px;
}
QPushButton#navSiteCard:hover QLabel#navSiteCardTitle {
    color: $primary;
}
QLabel#navSiteCardDomain {
    background: transparent;
    color: $text_secondary;
    font-size: 10px;
}

/* ---- 碎片行 ---- */
QWidget#fragItemRow {
    background-color: transparent;
    border-radius: 8px;
}
QWidget#fragItemRow:hover {
    background-color: $primary_a08;
}
QLabel#fragPreview {
    color: $text_secondary;
    font-size: 13px;
    padding: 4px 6px;
    background: transparent;
    border: none;
}
QPushButton#fragCopyBtn, QPushButton#fragDelBtn {
    border-radius: 7px;
    font-size: 11px;
    padding: 0px;
}
QPushButton#fragCopyBtn {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
}
QPushButton#fragCopyBtn:hover {
    background-color: $primary;
    color: $on_primary;
}
QPushButton#fragDelBtn {
    background-color: $danger_alpha;
    color: $danger;
    border: 1px solid $danger_border;
}
QPushButton#fragDelBtn:hover {
    background-color: $danger;
    color: white;
}

/* ---- 临时素材 ---- */
QLabel#assetName {
    color: $text_secondary;
    font-size: 11px;
}

/* ---- 软件导航 ---- */
QToolButton#appLaunchBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 4px;
    color: $app_name_text;
}
QToolButton#appLaunchBtn:hover {
    background-color: $primary_a08;
    border: 1px solid $primary_a30;
}
QToolButton#appLaunchBtn:pressed {
    background-color: $list_item_selected;
}
QToolButton#appLaunchBtn:disabled {
    color: $text_disabled;
}

/* ---- 撤销提示条（UndoBar，小卡片任务页浮动子控件）---- */
QWidget#undoBar {
    background-color: $undo_bg;
    border: 1px solid $primary_a30;
    border-radius: 10px;
}
QLabel#undoBarLabel {
    color: $undo_text;
    font-size: 11px;
    background: transparent;
}
QPushButton#undoUndoBtn {
    background-color: transparent;
    color: $primary_lite;
    border: 1px solid $primary_lite;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#undoUndoBtn:hover {
    background-color: $primary_lite;
    color: $on_primary;
}
""")


# ====================================================================
# 右键菜单 QSS（悬浮球用）
# ====================================================================
_QSS_MENU = Template("""
QMenu {
    background-color: $menu_bg;
    border: 1px solid $primary_a30;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 28px 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    color: $text;
}
QMenu::item:selected {
    background-color: $primary_a12;
    color: $primary;
}
QMenu::separator {
    height: 1px;
    background: $hair;
    margin: 4px 8px;
}
""")


# ====================================================================
# 公开接口
# ====================================================================
def get_colors(theme_name: str = DEFAULT_THEME) -> dict:
    """获取指定主题的配色字典，未知主题回退到默认主题"""
    return THEMES.get(theme_name, THEMES[DEFAULT_THEME])


def get_main_window_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取大窗口主UI的QSS"""
    return _QSS_MAIN_WINDOW.substitute(get_colors(theme_name))


def get_card_window_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取小卡片弹窗的QSS"""
    return _QSS_CARD_WINDOW.substitute(get_colors(theme_name))


def get_menu_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取右键菜单的QSS"""
    return _QSS_MENU.substitute(get_colors(theme_name))
