# -*- coding: utf-8 -*-
"""
====================================================================
主题系统模块  -  theme
====================================================================
配色字典 + QSS 模板，支持浅色/深色两套主题；
另提供「跟随系统」解析（config 取值 "follow" → resolve_theme_name）。

v3 视觉规范（UI 重构 00/01，对照 `设计稿/ui-redesign-preview.html`）：
  · 玻璃拟态数值退役：glass_fill 实度 96%+，GlassPanel 高光/噪点归零，
    视觉等同实底（等 03/05 包清完调用点再决定是否删类）
  · 圆角四档：r_win=12 / r_panel=8 / r_ctl=6 / r_chip=4（$r_* 令牌，
    自绘取数走 constants.RADIUS_*；滑杆手柄除外——正圆几何）
  · 主色：墨绿实底 #0F6E56（浅）/ #5DCAA5（深），只用于主按钮与选中态；
    primary_lite/deep 保留旧薄荷值（撤销条按钮/启动屏仍在用）
  · 按钮三级制：primaryBtn 实底 / secondaryBtn ghost / textBtn 文字 /
    dangerBtn 危险文字钮（hover 填充实底）
  · 动效统一 160ms、OutCubic；hover 只做视觉，切换靠点击

设计要点：
  1. 用 string.Template + $var 占位符（QSS 中无 $ 符号，安全）
  2. 所有颜色集中在 THEMES 字典；旧键名全部保留，避免其它模块引用失效
  3. get_main_window_qss / get_card_window_qss / get_menu_qss 三套 QSS
====================================================================
"""

import re
from string import Template

from src import accent
from src.constants import DEFAULT_THEME

# ---- 旧薄荷硬编码兜底（强调色系统 2026-10-03 收口）----
# 强调色可被 override（8 套预设 + 自定义 HEX），散落各处的旧薄荷字面量
# fallback 一旦生效就是错色。统一从这里 import；除本文件的常量定义行与
# 仍为活 token 的 primary_lite / primary_deep 行外，源码任何位置禁止再
# 出现这三个值（test_theme_accent 源码扫描护栏）。
FALLBACK_ACCENT = "#5BC0BE"        # 旧薄荷（primary 兜底）
FALLBACK_ACCENT_DEEP = "#3D9E9C"   # 旧薄荷深（primary_deep 兜底）
FALLBACK_ACCENT_LITE = "#6FFFE9"   # 旧薄荷亮（primary_lite 兜底）

# 强调色运行时状态 + merge 结果缓存（key = 主题名, accent id, custom hex）
_ACTIVE_ACCENT = accent.DEFAULT_ACCENT
_ACTIVE_CUSTOM = ""
_ACCENT_CACHE = {}


# ====================================================================
# 配色字典
# ====================================================================
THEMES = {
    "light": {
        "_name": "light",

        # ---- 玻璃壳（由 GlassPanel 使用，见 glass.py）----
        # UI 重构 01（2026-10-02）：玻璃拟态数值退役 —— 填充实度提到 96%+，
        # 底色对齐新 bg（纸感暖白），视觉上等同实底；高光带与噪点的真身是
        # GlassPanel 类常量（HIGHLIGHT_ALPHA / NOISE_OPACITY），已同步归零。
        # glass_highlight / noise_alpha 两个键历史上就没有消费者（死键），
        # 按总纲一并归零留档，等 03/05 包清完调用点后连同键一起移除。
        "glass_fill":        "rgba(250, 250, 248, 246)",   # 容器填充 ≈97%（纸感暖白实底）
        "glass_edge":        "rgba(255, 255, 255, 236)",   # 描边：上（亮）
        "glass_edge_bottom": "rgba(16, 32, 48, 20)",       # 描边：下（暗，模拟厚度）
        "glass_highlight":   "rgba(255, 255, 255, 0)",     # 顶部高光带 → 0（退役）
        "noise_alpha":       0,                             # 噪点强度 → 0（退役）

        # ---- 内嵌面板（列表/输入框等第二层面）----
        # panel_fill/panel_edge 仍走半透明（02–05 包迁移调用点后再实底化）
        "panel_fill":        "rgba(255, 255, 255, 170)",   # 67%
        "panel_edge":        "rgba(255, 255, 255, 217)",   # 85%
        "hair":              "rgba(16, 32, 48, 18)",       # 分隔线 7%
        "slider_handle":     "#FFFFFF",                    # 滑杆手柄（圆形）

        # ---- 新令牌（UI 重构 00/01，四档 surface + 两档线）----
        "surface":           "#FFFFFF",                    # 卡片、面板
        "surface_2":         "#F5F4F0",                    # 次级底、hover、标题栏
        "surface_3":         "#F1EFE8",                    # 分段控件底、chip、骨架
        "line":              "#E4E2DB",                    # 0.5~1px 分隔线
        "line_2":            "#D3D1C7",                    # 描边（按钮/输入框外框）
        "on_accent":         "#FFFFFF",                    # 强调色上的文字（= on_primary 值，语义独立）
        "accent_soft":       "#E1F5EE",                    # 选中行底
        # 碎片行首类别点（02 包 delegate 取用）
        "cat_code":          "#185FA5",
        "cat_cmd":           "#A32D2D",
        "cat_link":          "#185FA5",
        "cat_plain":         "#888780",
        "cat_img":           "#3B6D11",

        # ---- 背景（兼容旧引用，值对齐新令牌）----
        "bg":                "#FAFAF8",                    # 窗口底（纸感暖白）
        "card_bg":           "rgba(255, 255, 255, 148)",
        "card_bg_solid":     "#FFFFFF",                    # = surface
        "input_bg":          "#F5F4F0",                    # = surface_2
        "menu_bg":           "#FFFFFF",                    # = surface（菜单实底化）
        "list_bg":           "#F5F4F0",                    # = surface_2
        "bg_level2":         "#F1EFE8",                    # = surface_3

        # ---- 圆角四档（QSS 用字符串；自绘取数走 constants.RADIUS_*）----
        "r_win":             "12",                         # 窗口
        "r_panel":           "8",                          # 面板/卡片/列表容器
        "r_ctl":             "6",                          # 按钮/输入框
        "r_chip":            "4",                          # chip/徽章

        # ---- 字阶四档（QSS 用字符串；自绘取数走 constants.FS_*）----
        "fs_xs":             "11px",                       # 时间戳/计数
        "fs_sm":             "13px",                       # 正文与列表行
        "fs_md":             "15px",                       # 页标题/卡标题
        "fs_lg":             "20px",                       # 大标题

        # ---- 主色（强调色）----
        # UI 重构 01：主色从「薄荷青渐变」改为「墨绿实底」。#0F6E56 对白底
        # 6.2:1，配白字（on_primary）过 WCAG AA。只用于主按钮与选中态，
        # 不做大面积铺色。
        # hover/pressed（2026-10-02 悬停配色优化）：实底主色钮的 hover 从
        # 「同色系压暗」（#0C5A47，大色块发闷）改为「轻提亮」#227A64（白字
        # 5.2:1）—— 按钮有「浮起」的前置感；pressed 下沉复用旧 hover 值
        # #0C5A47（白字 8.2:1）。两值只被 SmoothButton overlay 消费。
        # primary_lite/deep 保留旧薄荷值：只剩撤销条按钮（深底亮字）与
        # 启动屏渐变在用，不随新主色走。
        "primary":           "#0F6E56",
        "primary_lite":      "#6FFFE9",
        "primary_deep":      "#3D9E9C",
        "primary_hover":     "#227A64",
        "primary_pressed":   "#0C5A47",
        "primary_alpha":     "rgba(15, 110, 86, 0.12)",
        "primary_a08":       "rgba(15, 110, 86, 0.08)",
        "primary_a12":       "rgba(15, 110, 86, 0.12)",
        "primary_a18":       "rgba(15, 110, 86, 0.18)",
        "primary_a30":       "rgba(15, 110, 86, 0.30)",
        "primary_border":    "rgba(15, 110, 86, 0.30)",
        "primary_border_strong": "rgba(15, 110, 86, 0.40)",
        "list_border":       "rgba(15, 110, 86, 0.22)",
        "list_item_hover":   "rgba(15, 110, 86, 0.08)",
        "list_item_selected": "rgba(15, 110, 86, 0.16)",

        # ---- 主色底上的文字色 ----
        # 新主色是深绿实底：白字 6.2:1（旧浅青底必须压深墨的约束已随改色消失）
        "on_primary":        "#FFFFFF",
        "on_disabled":       "#6B747E",
        # ---- 次按钮（secondaryBtn）文字色 ----
        # 次按钮 = $surface 实底 + $line_2 描边（不再全员淡青）。#0C5A47
        # 对白底 8.2:1（恰为旧 primary_hover 值，两 token 语义各自独立）；
        # focus_ring 与它同值同因，独立命名让「次按钮文字」与「焦点环」
        # 两个语义不绑死。
        "secondary_text":    "#0C5A47",
        # ---- 键盘焦点环（UI 强化方案 A4）----
        # 焦点环是「非文本前景」，须在面板底上达 WCAG 下限 3:1。
        # 旧断言「$primary 对白底不足 3:1」已随主色改深失效（现 6.2:1），
        # focus_ring 保留独立 token 并与 secondary_text 同值，理由同上。
        "focus_ring":        "#0C5A47",

        # ---- 文字 ----
        "text":              "#2C2C2A",                    # 正文（12.6:1）
        "text_secondary":    "#5F5E5A",                    # 次级文字（6.5:1）
        "text_placeholder":  "#6E6D67",                    # 时间戳/提示（11px 专用，5.4:1）
        "text_disabled":     "#B4B2A9",
        "app_name_text":     "#1A1A1A",

        # ---- 危险/警告/链接/成功 ----
        "danger":            "#A32D2D",                    # 危险 = 文字按钮 + 二次确认
        "danger_hover":      "#E4593B",                    # 保留旧值（dangerBtn 文字钮 hover 走实底填充）
        "danger_alpha":      "rgba(163, 45, 45, 0.10)",
        "danger_border":     "rgba(163, 45, 45, 0.28)",
        "warn":              "#854F0B",
        "warn_alpha":        "rgba(133, 79, 11, 0.12)",
        "warn_border":       "rgba(133, 79, 11, 0.30)",
        "warning":           "#B8770F",                    # 警告图标琥珀（GlassMessageBox warning 语义着色；与 warn 的语义色区分）
        "success":           "#1F8A4C",
        "link":              "#185FA5",                    # 碎片内容类别色条（链接）

        # ---- 日程任务状态色（A2/A3，delegate 自绘取色，QSS 集中于此）----
        # （02 包接管任务面板时再对齐新色板，本包不动语义色）
        "task_overdue":      "#E74C3C",                    # 逾期（浅色）
        "task_today":        "#E67E22",                    # 今日到期
        "task_done":         "#9AA5B1",                    # 已完成（次级灰）
        "task_none":         "#2C3E50",                    # 无截止 / 普通行
        "task_check":        "#1F8A4C",                    # 勾选框对勾/填充

        # ---- 日期选择器弹层（自绘，见 src/date_picker.py）----
        # 弹层不走 QSS 而是 QPainter 自绘，配色只能从本表取 —— 这几个键
        # 是「自绘控件的色板」，与 QSS 令牌同受 test_theme_contrast 的
        # 「浅底不得压白字」通用护栏约束。
        # ★ cal_accent 是全库**唯一一处非主色的强调色**：用户明确要求照抄
        #   Chromium 原生 date picker（选中日的蓝块），因此不跟随产品主色
        #   绿。浅色取 Chromium 的 #1A73E8、深色取其暗色主题的 #8AB4F8；
        #   除日历弹层外不得扩散使用。
        # ⚠ cal_muted 对弹层底只有约 2.6:1，**刻意低于正文 4.5:1 的下限**
        #   —— 跨月补位日是装饰性信息，Chromium 原样也是这个灰度；需要读清
        #   的文字（星期表头）走 cal_weekday（约 4.6:1）。tests/test_date_picker
        #   有一条"muted 必须比 text / weekday 更浅"的**意图护栏**，
        #   别为了凑对比度把它们调成同一个灰。
        "cal_popup_bg":      "#FFFFFF",
        "cal_popup_edge":    "#DADCE0",
        "cal_title":         "#202124",
        "cal_text":          "#3C4043",
        "cal_muted":         "#9AA0A6",     # 跨月补位日（刻意压浅，见下方说明）
        "cal_weekday":       "#70757A",     # 星期表头（一 二 三 …，要看得清）
        "cal_nav_icon":      "#5F6368",     # 翻月/翻年箭头 + 月标题的 ▼
        "cal_hover_bg":      "#F1F3F4",
        "cal_divider":       "#E8EAED",     # 页脚上方的分隔线
        "cal_accent":        "#1A73E8",
        "cal_on_accent":     "#FFFFFF",

        # ---- 其它 ----
        "shadow":            "rgba(0, 0, 0, 70)",
        "side_bar_bg":       "transparent",
        "side_bar_btn":      "#8B96A3",
    },

    "dark": {
        "_name": "dark",

        # 玻璃拟态数值退役（与 light 同因，见 light 处注释）：底色去紫调，
        # 对齐新 bg #17191C 中性深灰
        "glass_fill":        "rgba(23, 25, 28, 248)",      # ≈97% 实底
        "glass_edge":        "rgba(255, 255, 255, 46)",    # 18%
        "glass_edge_bottom": "rgba(0, 0, 0, 120)",
        "glass_highlight":   "rgba(255, 255, 255, 0)",     # 退役
        "noise_alpha":       0,                             # 退役

        "panel_fill":        "rgba(255, 255, 255, 26)",    # 10%
        "panel_edge":        "rgba(255, 255, 255, 44)",    # 17%
        "hair":              "rgba(255, 255, 255, 23)",    # 9%
        "slider_handle":     "#E8ECF2",                    # 滑杆手柄（圆形）

        # ---- 新令牌（UI 重构 00/01，深色底去紫调）----
        "surface":           "#1E2126",
        "surface_2":         "#23262C",
        "surface_3":         "#2A2E34",
        "line":              "#33373D",
        "line_2":            "#454A52",
        "on_accent":         "#04342C",
        "accent_soft":       "#1E332C",
        "cat_code":          "#85B7EB",
        "cat_cmd":           "#F09595",
        "cat_link":          "#85B7EB",
        "cat_plain":         "#888780",
        "cat_img":           "#97C459",

        # ---- 背景（兼容旧引用，值对齐新令牌）----
        "bg":                "#17191C",
        "card_bg":           "rgba(20, 22, 32, 158)",
        "card_bg_solid":     "#1E2126",                    # = surface
        "input_bg":          "#23262C",                    # = surface_2
        "menu_bg":           "#1E2126",                    # = surface（菜单实底化）
        "list_bg":           "#23262C",                    # = surface_2
        "bg_level2":         "#2A2E34",                    # = surface_3

        # ---- 圆角四档（与 light 同值）----
        "r_win":             "12",
        "r_panel":           "8",
        "r_ctl":             "6",
        "r_chip":            "4",

        # ---- 字阶四档（与 light 同值）----
        "fs_xs":             "11px",
        "fs_sm":             "13px",
        "fs_md":             "15px",
        "fs_lg":             "20px",

        # ---- 主色（强调色）----
        # 深色主色换成中亮度的绿（#5DCAA5 对深底 8:1），压 on_primary
        # 深墨 6.8:1；hover 同走「轻提亮」但幅度收敛（#68CFAC 压深墨
        # 7.3:1，比旧端点 #6FD6B4 更沉稳），pressed 下沉（深色分支）
        # primary_lite/deep 保留旧值（撤销条按钮 / 启动屏在用）
        "primary":           "#5DCAA5",
        "primary_lite":      "#8BFFF0",
        "primary_deep":      "#4AA8A6",
        "primary_hover":     "#68CFAC",
        "primary_pressed":   "#4BB894",
        "primary_alpha":     "rgba(93, 202, 165, 0.15)",
        "primary_a08":       "rgba(93, 202, 165, 0.08)",
        "primary_a12":       "rgba(93, 202, 165, 0.12)",
        "primary_a18":       "rgba(93, 202, 165, 0.18)",
        "primary_a30":       "rgba(93, 202, 165, 0.30)",
        "primary_border":    "rgba(93, 202, 165, 0.30)",
        "primary_border_strong": "rgba(93, 202, 165, 0.45)",
        "list_border":       "rgba(93, 202, 165, 0.20)",
        "list_item_hover":   "rgba(93, 202, 165, 0.08)",
        "list_item_selected": "rgba(93, 202, 165, 0.18)",

        # ---- 主色底上的文字色 ----
        # 深色主色中亮：深墨 #04342C 压底 6.8:1（旧薄荷亮色 fallback 需深墨的强约束放宽）
        "on_primary":        "#04342C",
        "on_disabled":       "rgba(255, 255, 255, 180)",
        # ---- 次按钮（secondaryBtn）文字色 ----
        # 次按钮 = $surface 实底 + $line_2 描边；文字直接用主色
        # （#5DCAA5 在 #1E2126 上 8:1），与 light 保持同一 token 名
        "secondary_text":    "#5DCAA5",
        # ---- 键盘焦点环（A4）----
        # 与 $secondary_text 同值同因（理由见 light 主题处注释）
        "focus_ring":        "#5DCAA5",

        "text":              "#E9EAE7",                    # 正文
        "text_secondary":    "#A8ADA5",                    # 次级文字
        "text_placeholder":  "#8A8F88",                    # 时间戳/提示（11px 专用）
        "text_disabled":     "#5A6170",
        "app_name_text":     "#E4E8EE",

        "danger":            "#F09595",
        "danger_hover":      "#FF6B5B",
        "danger_alpha":      "rgba(240, 149, 149, 0.14)",
        "danger_border":     "rgba(240, 149, 149, 0.34)",
        "warn":              "#EF9F27",
        "warn_alpha":        "rgba(239, 159, 39, 0.14)",
        "warn_border":       "rgba(239, 159, 39, 0.32)",
        "warning":           "#F0B45E",                    # 警告图标琥珀（GlassMessageBox warning 语义着色；与 warn 的语义色区分）
        "success":           "#2ECC71",
        "link":              "#85B7EB",                    # 碎片内容类别色条（链接）

        # ---- 日程任务状态色（A2/A3，delegate 自绘取色，QSS 集中于此）----
        "task_overdue":      "#FF6B5B",                    # 逾期（深色）
        "task_today":        "#F39C12",                    # 今日到期
        "task_done":         "#8892A0",                    # 已完成（次级灰）
        "task_none":         "#E4E8EE",                    # 无截止 / 普通行
        "task_check":        "#2ECC71",                    # 勾选框对勾/填充

        # ---- 日期选择器弹层（自绘；理由与浅色处注释同源）----
        # 弹层是独立窗口，底色必须实底 —— 取 surface_3（深色下页面底同值）
        "cal_popup_bg":      "#2A2E34",
        "cal_popup_edge":    "#454A52",                    # = line_2
        "cal_title":         "#E9EAE7",
        "cal_text":          "#DADCE0",
        "cal_muted":         "#7A7F85",                    # 跨月补位日
        "cal_weekday":       "#A8ADA5",                    # 星期表头 = text_secondary
        "cal_nav_icon":      "#A8ADA5",                    # = text_secondary
        "cal_hover_bg":      "#3A3F46",
        "cal_divider":       "#33373D",                    # = line
        "cal_accent":        "#8AB4F8",
        "cal_on_accent":     "#202124",

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
/* 侧栏右侧分割手柄：平时隐形（不占地观感），悬停显一条细线提示可拖，
   拖动中主色高亮；dragging 是 _NavSplitHandle 设的动态属性 */
QWidget#navSplitHandle {
    background-color: transparent;
}
QWidget#navSplitHandle:hover {
    background-color: $hair;
}
QWidget#navSplitHandle[dragging="true"] {
    background-color: $primary_a30;
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
    border-radius: $r_panel;
    /* 左内边距比右大一档 = 组内缩进（按钮现在嵌在分组标题下面）。
       只动左右、不动上下 —— 上下内边距决定行高，改它会连带改 sizeHint。 */
    padding: 8px 12px 8px 20px;
    /* ★ 行间距放在 **margin-bottom** 而不是 layout spacing（2026-09-29）：
       分组折叠动画是对每条目做 maximumHeight 过渡，margin 属于条目自身，
       收起来时行高与空隙**一起**归零；若用 layout 的 spacing，收起后
       每组会残留 (n-1)×spacing 的空档，动画末尾会"啪"地跳一下。 */
    margin: 0 0 4px 0;
    text-align: left;
    font-size: 13px;
}
QPushButton#navBtn:hover {
    /* hover 只做视觉反馈（背景 + 轻微右移），切页靠点击。
       背景（a08）过渡走 SmoothButton overlay（清单 S4）；右移是
       padding 几何变化，保持即时 */
    color: $text;
    padding-left: 22px;
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

/* ---- 侧栏分组标题（可点击展开/折叠；箭头是自绘子控件，不在文案里）---- */
QPushButton#navGroupHeader {
    background-color: transparent;
    color: $text_placeholder;
    border: 1px solid transparent;
    border-radius: $r_panel;
    /* 上下 margin 对称 4px：箭头按几何中心定位（见 NavGroupHeader.resizeEvent），
       不对称的话箭头会偏离文字基线。上 4 + 上一条目的下 margin 4 = 组间 8px，
       与组内 4px 拉开层次。 */
    margin: 4px 0;
    /* 左 22px = 箭头位（箭头 x=10，宽 8，右侧再留 4px 与文字分开） */
    padding: 4px 10px 4px 22px;
    text-align: left;
    font-size: 11px;
}
QPushButton#navGroupHeader:hover {
    color: $text_secondary;
    background-color: $primary_a08;
}

/* ---- 侧栏滚动容器（溢出后才出现滚动条）---- */
QScrollArea#navScroll {
    background-color: transparent;
    border: none;
}
QScrollArea#navScroll > QWidget > QWidget {
    background-color: transparent;
}
/* 滚动条按需出现，宽度压到 6px：168px 宽的侧栏里，标准宽度
   （~15px）会把条目文字挤到换行，而条目一换行就变高 → 更需要滚动，
   形成"出现→变高→还在溢出"的抖动循环。 */
QScrollArea#navScroll QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
    border: none;
}
QScrollArea#navScroll QScrollBar::handle:vertical {
    background: $hair;
    border-radius: $r_chip;
    min-height: 24px;
}
QScrollArea#navScroll QScrollBar::handle:vertical:hover {
    background: $text_placeholder;
}
QScrollArea#navScroll QScrollBar::add-line:vertical,
QScrollArea#navScroll QScrollBar::sub-line:vertical {
    height: 0;
    border: none;
    background: none;
}
QScrollArea#navScroll QScrollBar::add-page:vertical,
QScrollArea#navScroll QScrollBar::sub-page:vertical {
    background: none;
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
    border-radius: $r_chip;
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
/* 设置页分组卡标题（UI 重构 04）：比通用 sectionLabel 大一档，
   用字阶令牌 $fs_md + 中等字重提升层级。★必须用属性选择器限定作用域——
   #sectionLabel 是全局共享的（EmptyState 标题、碎片预览标题、知识库/合并
   对话框等都在用），直接改上面那条会把别处标题一起放大。 */
QLabel#sectionLabel[groupTitle="true"] {
    font-size: $fs_md;
    font-weight: 500;
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
    /* 按钮三级制（UI 重构 00/01）：
       · primaryBtn   实底 $primary + $on_primary —— 一屏至多 1 个
       · secondaryBtn ghost：$surface 底 + $line_2 描边（文字固定 $secondary_text，铁律）
       · textBtn      透明底 + $text_secondary —— 第三级操作、批量操作
       · dangerBtn    透明底 + $danger 文字钮（hover 才填充实底），不做红胶囊
       丝滑化清单 S2 仍然有效：QPushButton 的 hover/press 背景过渡由
       SmoothButton（controls.py）在 paint 层插值，端点色契约见其
       _SMOOTH_OVERLAYS；QSS 只保留文字/边框变化；**:pressed 禁止再用
       margin 做位移** —— 位移走绘制级 -1px 下沉 + 0.98 微缩。
       基态一律声明 1px 边框（与底同色或透明）：焦点环只换 border-color，
       不撑盒模型（A4 纪律）。
       ★ 本区块的说明注释一律写在规则体内部：规则之间的注释会被
       test_theme_contrast 等正则切分器并进下一条规则的选择器键。 */
    background-color: $primary;
    color: $on_primary;
    border: 1px solid transparent;
    border-radius: $r_ctl;
    padding: 7px 15px;
    font-size: 13px;
}
QPushButton:hover {
    color: $on_primary;
}
QPushButton:disabled {
    background-color: $text_disabled;
    color: $on_disabled;
}

QPushButton#primaryBtn {
    /* 主按钮：主色实底（渐变已退役）。border 与底同色 = 视觉 none，
       但保住了 1px 边框位给焦点环换色，盒模型稳定 */
    background-color: $primary;
    color: $on_primary;
    font-weight: 600;
    border: 1px solid $primary;
    border-radius: $r_ctl;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton#primaryBtn:hover {
    /* 背景过渡走 SmoothButton overlay（端点 $primary_hover，
       未命名按钮兜底 spec），QSS 不再跳变 */
}

QPushButton#secondaryBtn {
    /* 次按钮：ghost —— surface 实底 + line_2 描边（替代全员淡青胶囊） */
    background-color: $surface;
    color: $secondary_text;
    border: 1px solid $line_2;
    border-radius: $r_ctl;
}
QPushButton#secondaryBtn:hover {
    /* 背景过渡走 SmoothButton overlay（端点 primary a18），QSS 不再跳变 */
}
QPushButton#secondaryBtn:checked {
    background-color: $accent_soft;
    border: 1px solid $primary;
    color: $secondary_text;
    font-weight: 600;
}

QPushButton#modeBtn {
    /* 模式切换小胶囊（设置页 AI 后端模式云端/本地、插件页同款）：
       ghost 常态 + 选中实底反白 —— 镜像卡片模板同名规则。卡片模板的
       规则不会级联进主窗，此前主窗 modeBtn 掉进通用 QPushButton 实底：
       选中态与常态底色无差别、primary 图标画在 primary 底上不可见
       （2026-10-02 随悬停配色优化一并修复）。背景过渡走 SmoothButton
       overlay（端点 primary 46% 淡染 / 按下 77%，叠在选中实底上同色
       no-op）。 */
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
    border-radius: $r_ctl;
    padding: 5px 14px;
    font-size: 12px;
}
QPushButton#modeBtn:checked {
    background-color: $primary;
    color: $on_primary;
    border: 1px solid $primary;
}

/* ---- 任务提醒弹窗（src/task_reminder_popup，顶层自绘卡片）---- */
QFrame#taskReminderCard {
    /* 中性骨架；触发链路不变（启动 +4s 与每日 9:00 三桶口径），语气色
       （逾期 danger / 今日 primary / 未安排中性）由 _apply_palette 按主题
       内联取色。★说明注释只放规则体内：裸放在选择器前会被 QSS 切分器
       并进选择器（test_task_reminder_popup 的骨架契约因此漏检）。 */
    background-color: $surface;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
}
QLabel#taskRemindHead {
    color: $text;
    font-size: 14px;
    font-weight: 600;
}
QLabel#taskRemindTitle {
    color: $text_secondary;
    font-size: 13px;
}
QLabel#taskRemindCount {
    color: $text_secondary;
    font-size: 12px;
}
QLabel#taskRemindOverflow {
    color: $text_placeholder;
    font-size: 12px;
}
QFrame#taskRemindFoot {
    border-top: 1px solid $hair;
}

QPushButton#textBtn {
    /* 第三级：文字按钮（透明底，批量操作/弱操作用） */
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: $r_ctl;
    padding: 7px 15px;
    font-size: 13px;
}
QPushButton#textBtn:hover {
    /* 背景过渡走 SmoothButton overlay（端点 primary a20 淡染），
       文字加深即时 */
    color: $text;
}

QPushButton#dangerBtn {
    /* 危险操作：文字按钮 + 二次确认（调用点负责确认）。静止态透明底 +
       $danger 文字；hover 由 SmoothButton overlay 填 $danger_hover 实底
       （端点见 controls.py _SMOOTH_OVERLAYS，两边必须同步） */
    background-color: transparent;
    color: $danger;
    border: 1px solid transparent;
}
QPushButton#dangerBtn:hover {
    /* 背景过渡走 SmoothButton overlay（端点 $danger_hover 实底）。
       对比度修复（2026-10，GlassMessageBox danger 按钮专项拍板）：实底红上
       保持 $danger 红字几乎不可读 → hover 文字改白。本规则刻意不声明
       底色，避免与 overlay 打架；实底端点 overlay 会按 QSS 状态补画 label */
    color: white;
}

QPushButton#iconBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: $r_panel;
    padding: 6px;
    font-size: 15px;
}
QPushButton#iconBtn:hover {
    /* 背景（a12）过渡走 SmoothButton overlay，色/边框保持即时 */
    color: $primary;
    border: 1px solid $primary_a30;
}
QPushButton#iconBtn[danger="true"]:hover {
    color: $danger;
    border: 1px solid $danger_border;
}

/* ---- 轻提示条（Toast）：底部居中浮层，用于"自动清理 N 条"这类通报 ---- */
QLabel#toastBar {
    background-color: $glass_fill;
    color: $text;
    border: 1px solid $primary_a30;
    border-radius: $r_panel;
    padding: 10px 18px;
    font-size: 12px;
}

/* ---- 内嵌面板（第二层玻璃）---- */
QWidget#glassCard, QFrame#glassCard {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
}

/* ---- 设置页分组卡片 ---- */
QWidget#settingsGroup {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
}

/* ---- 设置页内部分类导航（2026-09-29）：三态对齐侧栏 navBtn、去掉拖拽态 ----
   checked 的 font-weight 变粗会改 sizeHint —— 侧栏 navBtn 因此禁止加粗
   （拖拽期间 sizeHint 缓存失效），这里的条目是静态 VBox、无高度动画，
   加粗安全。 */
QPushButton#settingsNavBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: $r_panel;
    padding: 8px 12px;
    text-align: left;
    font-size: 13px;
}
QPushButton#settingsNavBtn:hover {
    /* 背景（a08）过渡走 SmoothButton overlay，文字色保持即时 */
    color: $text;
}
QPushButton#settingsNavBtn:checked {
    /* 选中行底走 $accent_soft（UI 重构 04：左导航「图标 + 文字」两列），
       文字取主色；不再用 primary_a18 半透明染，避免与卡片底叠色发灰。 */
    background-color: $accent_soft;
    color: $primary;
    border: 1px solid transparent;
    font-weight: 600;
}
QFrame#settingsNavDivider {
    background-color: $hair;
    border: none;
}

/* ---- 使用说明页目录（2026-10-03 目录化）：纯文字 TOC，与按钮形态区分 ----
   目录项是「目录的一行」不是按钮：无图标无底色块，常态纯文字；当前
   位置用左缘竖条表达（QSS 唯一可行画法：3px 左描边，常态透明占位保证
   文字对齐不跳）；分组标签 11px 灰字。圆角取 4——左缘有 3px 描边，
   沿用 $r_panel 会让竖条跟着大圆角拐弯。 */
QLabel#helpTocGroup {
    color: $text_placeholder;
    font-size: $fs_xs;
    padding: 10px 10px 3px 0;
}
/* 组标题主色小刻度：与选中项左缘竖条同语系、降一档权重（a30），
   3×10px 圆角小条——组头在纯文字列表里的「这是标题」提示 */
QFrame#helpTocTick {
    background-color: $primary_a30;
    border: none;
    border-radius: 1px;
}
QPushButton#helpTocItem {
    background-color: transparent;
    color: $text_secondary;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 4px;
    padding: 3px 10px 3px 7px;
    text-align: left;
    font-size: 12px;
}
QPushButton#helpTocItem:hover {
    background-color: $primary_a08;
    color: $text;
}
QPushButton#helpTocItem:checked {
    background-color: $accent_soft;
    color: $primary;
    border-left: 3px solid $primary;
    font-weight: 600;
}
QPushButton#helpTocItem:focus {
    /* 键盘可达性：焦点环描上/右/下三边（全局 border 会盖掉左缘竖条，
       与 checked 的位置语义冲突），左缘保持竖条/透明占位不动 */
    border-top: 1px solid $focus_ring;
    border-right: 1px solid $focus_ring;
    border-bottom: 1px solid $focus_ring;
    outline: none;
}

/* ---- 插件中心 ---- */
QFrame#pluginCard {
    /* 实底 + 左缘 3px 状态条（属性选择器按状态换色，2026-10-02 卡片重设计）：
       状态条是插件页唯一「一眼可扫」的信号——启用=主色 / 停用=中性灰 /
       警告=橙。用 $surface 实底而非旧的 $panel_fill 半透明：卡片是信息
       容器，不是玻璃层。★卡片须 setProperty("state", ...) 后再 polish。 */
    background-color: $surface;
    border: 1px solid $line;
    border-left: 3px solid $line_2;
    border-radius: $r_panel;
}
QFrame#pluginCard[state="on"] {
    border-left-color: $primary;
}
QFrame#pluginCard[state="warn"] {
    border-left-color: $warn;
}
/* 卡片图标槽（28×28）：给卡片一个视觉锚点；启用态淡主色底 + 主色描边 */
QFrame#pluginIconSlot {
    background-color: $surface_2;
    border: 1px solid $line;
    border-radius: $r_ctl;
}
QFrame#pluginIconSlot[state="on"] {
    background-color: $accent_soft;
    border-color: $primary_a30;
}
QFrame#pluginIconSlot[state="warn"] {
    background-color: $warn_alpha;
    border-color: $warn_border;
}
/* 卡片底部操作行：通栏 + 上分隔线（布局里靠 addStretch(1) 吸底） */
QFrame#pluginCardFoot {
    border: none;
    border-top: 1px solid $line;
}
/* ---- 页面插件容器（AI 助手等）：实底遮住玻璃壳，避免透出屏幕后内容 ---- */
QWidget#pluginPage {
    background-color: $bg;
    border-radius: $r_panel;
}
QLabel#pluginCardTitle {
    color: $text;
    font-size: $fs_md;
    font-weight: 500;
}
QLabel#pluginCardId {
    /* 版本号 / 目录名：等宽小字弱化到 text_placeholder，不再与标题抢层级 */
    color: $text_placeholder;
    font-family: 'Consolas', 'Cascadia Mono', monospace;
    font-size: $fs_xs;
}
QLabel#pluginCardDesc {
    color: $text_secondary;
    font-size: $fs_sm;
}
QTextBrowser#usageViewer {
    background-color: transparent;
    color: $text;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
    padding: 8px;
    font-size: 13px;
}
/* AI 助手「↓ 回到最新」浮按钮（2026-10-03 P2）：消息流右下角的 44px
   圆钮，浮在内容上必须实底（$surface）才可读；hover 换 $surface_2 +
   主色描边提示可点。图标色走 $text（plugin.py 经 icon_render 自绘）。 */
QPushButton#backToLatestBtn {
    background-color: $surface;
    border: 1px solid $line_2;
    border-radius: 22px;
    padding: 0;
}
QPushButton#backToLatestBtn:hover {
    background-color: $surface_2;
    border: 1px solid $primary_a30;
}
/* 动作 chip（2026-10-02 卡片重设计）：一个动作一枚「实底 chip」——
   动作名 + 热键（等宽小字）+「右键菜单」归属，由 _FlowLayout 横向排列
   并自动换行。实底＝可执行性质，与能力标签（描边＝标签性质）区分开。
   取代此前「· 动作名〔右键菜单〕热键」三标签逐行排列（旧
   #pluginActionTitle / #pluginActionTag 随之退役）。 */
QFrame#pluginActChip {
    background-color: $surface_2;
    border: none;
    border-radius: $r_chip;
}
QLabel#pluginActName {
    color: $text_secondary;
    font-size: $fs_xs;
}
QLabel#pluginActHotkey {
    color: $text_placeholder;
    font-family: 'Consolas', 'Cascadia Mono', monospace;
    font-size: 10px;
    background-color: $surface_3;
    border-radius: 3px;
    padding: 1px 5px;
}
QLabel#pluginActTag {
    color: $text_placeholder;
    font-size: 10px;
}
QLabel#pluginGateHint {
    color: $danger;
    background-color: $danger_alpha;
    border: 1px solid $danger_border;
    border-radius: $r_panel;
    padding: 8px 12px;
    font-size: 12px;
}
QLabel#pluginEmptyHint {
    color: $text_secondary;
    font-size: 13px;
}

/* ---- 插件中心：加载失败卡片 ---- */
QFrame#pluginErrorCard {
    background-color: $danger_alpha;
    border: 1px solid $danger_border;
    border-radius: $r_panel;
}
QLabel#pluginErrorTitle {
    color: $danger;
    font-size: 14px;
    font-weight: 600;
}
QLabel#pluginErrorReason {
    color: $text;
    font-size: 12px;
}
QLabel#pluginErrorHint {
    color: $text_secondary;
    font-size: 12px;
}
/* ---- 插件中心：状态（已启用 / 已停用 / 部分生效）---- */
/* 2026-10-02 卡片重设计：描边胶囊退役，改「6px 圆点 + 11px 文字」——
   胶囊在暗色下发重、抢标题注意力；点+字轻，且状态文字固定钉在标题行
   右端，一排卡自然形成可扫读的「状态列」。圆点是独立的
   QFrame#pluginStatusDot（QSS 无法给 QLabel 加伪元素）。 */
QLabel#pluginStatusOn {
    color: $primary;
    font-size: $fs_xs;
    font-weight: 600;
}
QLabel#pluginStatusOff {
    color: $text_placeholder;
    font-size: $fs_xs;
    font-weight: 600;
}
QLabel#pluginStatusWarn {
    color: $warn;
    font-size: $fs_xs;
    font-weight: 600;
}
QFrame#pluginStatusDot {
    background-color: $line_2;
    border: none;
    border-radius: 3px;
}
QFrame#pluginStatusDot[state="on"] {
    background-color: $primary;
}
QFrame#pluginStatusDot[state="warn"] {
    background-color: $warn;
}
/* 加载失败卡的阶段标签：沿用旧「状态胶囊」的描边样式，与卡片上的
   状态点区分开（失败卡是异常物，可以比正常卡重一点） */
QLabel#pluginStageTag {
    color: $warn;
    background-color: $warn_alpha;
    font-size: $fs_xs;
    font-weight: 600;
    padding: 1px 7px;
    border: 1px solid $warn_border;
    border-radius: $r_chip;
}
/* ---- 插件中心：失败区小标题 ---- */
QLabel#pluginSectionLabel {
    color: $text_secondary;
    font-size: 12px;
    font-weight: 600;
}
/* ---- 插件中心：插件安装目录 / 商店目录（等宽，便于核对路径）----
   2026-10-02 起降为 11px text_placeholder：路径是「设置期信息」，
   完整绝对路径仍在标签的 tooltip 上，不删信息只降视觉重量。 */
QLabel#pluginDirLabel {
    color: $text_placeholder;
    font-family: 'Consolas', 'Cascadia Mono', monospace;
    font-size: $fs_xs;
}

/* ---- 插件中心：卡片 ⋯ 渐进披露菜单入口（2026-10-04 三件套）----
   ID / 依赖 / 注册页面这些「需要时一眼能找到，不需要时不出现」的元信息
   收进菜单（设计稿 plugins-center-redesign）；卡面只留一枚 20px 圆角
   透明图标钮，hover 才浮出底色。 */
QPushButton#pluginMoreBtn {
    background-color: transparent;
    border: none;
    border-radius: $r_chip;
    padding: 0;
}
QPushButton#pluginMoreBtn:hover {
    background-color: $surface_3;
}
/* ---- 插件中心：加载失败折叠条（2026-10-04 三件套）----
   失败是低频状态，不该向每个健康会话征收竖向空间：默认收起成一条
   提示钮，点击展开失败卡。checked 态用 danger 色系点明「有问题在这」。 */
QPushButton#pluginErrorToggle {
    text-align: left;
    color: $text_secondary;
    background-color: $surface_2;
    border: 1px solid $line;
    border-radius: $r_ctl;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#pluginErrorToggle:hover {
    color: $danger;
    border-color: $danger_border;
}
QPushButton#pluginErrorToggle:checked {
    color: $danger;
    background-color: $danger_alpha;
    border-color: $danger_border;
}
/* ---- 插件中心：搜索 + 状态分段筛选（2026-10-04 三件套）----
   输入框走通用 QLineEdit 底座；分段钮用 :checked 表达当前段
   （全部 / 已启用 / 已停用，autoExclusive 互斥）。 */
QPushButton#pluginSegBtn {
    background-color: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-radius: $r_chip;
    padding: 3px 11px;
    font-size: $fs_xs;
}
QPushButton#pluginSegBtn:hover {
    color: $text;
    background-color: $surface_2;
}
QPushButton#pluginSegBtn:checked {
    color: $text;
    background-color: $surface_3;
    border-color: $line;
    font-weight: 600;
}
/* ---- 插件中心：卡片 ⋯ 菜单的信息行（QWidgetAction 容器内）---- */
QLabel#pluginMenuInfoTitle {
    color: $text_placeholder;
    font-size: 11px;
}
QLabel#pluginMenuInfoValue {
    color: $text;
    font-size: 12px;
    font-family: 'Consolas', 'Cascadia Mono', monospace;
}

/* ---- 插件中心：插件商店（可安装包）---- */
QFrame#pluginStoreBox {
    background-color: transparent;
}
QFrame#pluginStoreCard {
    background-color: $panel_fill;
    border: 1px dashed $primary_border;
    border-radius: $r_panel;
}
QLabel#pluginStoreTitle {
    color: $text;
    font-size: 14px;
    font-weight: 600;
}
/* 「未安装」标记：中性虚线色，与 pluginStatusOn（已装，绿）区分 */
QLabel#pluginStoreBadge {
    color: $secondary_text;
    background-color: $primary_a12;
    font-size: 11px;
    padding: 1px 6px;
    border: 1px solid $primary_a30;
    border-radius: $r_chip;
}
/* 能力标签（网络 / 写入 / 改删 / AI）：描边＝标签性质，与动作 chip
   （实底＝可执行性质）形成层级差；完整语义仍在悬停提示里。
   商店卡与在线市场卡复用同一 objectName —— 样式一处改、三处生效。 */
QLabel#pluginCapBadge {
    color: $text_placeholder;
    background-color: transparent;
    font-size: $fs_xs;
    padding: 1px 7px;
    border: 1px solid $line;
    border-radius: $r_chip;
}

/* ---- AI 助手页面：左右对话气泡 ---- */
/* 用户气泡靠右主色底——主色上的文字必须 $on_primary（对比度铁律）。
   AI / 提示气泡浮在主窗口内容之上，底色必须近乎不透明（$menu_bg 94%
   实底）——2026-09-28 用户反馈：原先半透明 panel_fill（10%）把底下
   滚过的文字透出来，整条回复糊成一片没法读。
   尾角收小指向说话人（用户右下 / AI 左下），对话感更强 */
QFrame#chatBubbleUser {
    background-color: $primary;
    border-radius: $r_win;
    border-bottom-right-radius: $r_chip;
}
QLabel#chatBubbleText {
    color: $on_primary;
    font-size: 13px;
}
QLabel#chatBubbleAiText {
    color: $text;
    font-size: 13px;
}
QFrame#chatBubbleAI {
    background-color: $menu_bg;
    border: 1px solid $panel_edge;
    border-radius: $r_win;
    border-bottom-left-radius: $r_chip;
}
QFrame#chatBubbleHint {
    background-color: $menu_bg;
    border: 1px solid $warn_border;
    border-radius: $r_win;
}
/* 思考动画气泡：AI 回复在途时的「打字中」三点波（造型随 AI 气泡）；
   点本身由插件的 ThinkingDots 自绘（零字体依赖 + 主题 $primary 跟随），
   因此这里只有气泡壳，不再有 chatThinkingDots 文字规则 */
QFrame#chatBubbleThinking {
    background-color: $menu_bg;
    border: 1px solid $panel_edge;
    border-radius: $r_win;
    border-bottom-left-radius: $r_chip;
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
    border-radius: $r_chip;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
}

/* ---- 步进器（设置页的「调大调小」按钮）---- */
QPushButton#stepBtn {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_ctl;
    color: $text_secondary;
    font-size: 17px;
    font-weight: 700;
    padding: 0px;
}
QPushButton#stepBtn:hover {
    /* 背景（a18）过渡走 SmoothButton overlay */
    border: 1px solid $primary_a30;
    color: $primary;
}
QPushButton#stepBtn:pressed {
    /* 背景（a30）过渡走 SmoothButton overlay */
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
    border-bottom: 1px solid $focus_ring;
    outline: none;
}
/* 步进器灰化态（自动隐藏总开关关闭时其秒数步进器整体 setEnabled(False)）：
   stepValue 有专属 objectName，特化规则会盖掉通用的 QLineEdit:disabled，
   必须单独声明，否则数字/单位仍是正常色，看不出已被禁用 */
QLineEdit#stepValue:disabled,
QLabel#fieldLabel:disabled {
    color: $text_disabled;
}

/* ---- 滑杆：细槽 + 圆形手柄 ---- */
QSlider {
    background: transparent;
    min-height: 22px;
}
QSlider::groove:horizontal {
    height: 6px;
    border-radius: $r_chip;
    background: $hair;
}
QSlider::sub-page:horizontal {
    height: 6px;
    border-radius: $r_chip;
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
    /* 圆形手柄：半径必须 = 边长一半，不参与四档收敛（6px 会变圆角方） */
    border-radius: $r_ctl;
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
    border-radius: $r_ctl;
    padding: 6px 10px;
    color: $text;
    font-size: 13px;
    selection-background-color: $list_item_selected;
    selection-color: $text;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
    border: 1px solid $primary_a30;
}
/* 焦点环色用 $focus_ring 而非 $primary（A4）：$primary 在浅色主题的面板底上
   只有约 2.1:1，等于「有焦点态但看不见」；$focus_ring 与面板底 ≥4:1 */
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid $focus_ring;
    outline: none;
    background-color: $panel_fill;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    color: $text_disabled;
}

/* ---- 截止日期输入框 ----
   2026-10-02：弹层换成自绘日历（见 src/date_picker.py），此处只补两件事：
   右侧 30px 内边距给自绘的日历图标钮留位；字号与相邻的标题输入框持平。
   原生上下微调钮由控件里的 setButtonSymbols(NoButtons) 关掉，不靠 QSS
   隐藏 —— QSS 的 ::up-button{width:0} 在 Fusion 下仍会留出 2px 毛边。 */
QDateEdit#taskDate {
    padding: 5px 30px 5px 10px;
    font-size: 13px;
}
QDateEdit#taskDate:disabled {
    color: $text_disabled;
}

QComboBox::drop-down {
    width: 20px;
    border: none;
}
QComboBox QAbstractItemView {
    background-color: $menu_bg;
    border: 1px solid $primary_a30;
    border-radius: $r_panel;
    padding: 4px;
    selection-background-color: $list_item_selected;
    color: $text;
    outline: none;
}

/* ---- 列表 ---- */
QListWidget {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
    padding: 6px;
    color: $text;
    font-size: 13px;
    outline: none;
}
QListWidget::item {
    padding: 9px 11px;
    border-radius: $r_ctl;
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
    border-radius: $r_panel;
    padding: 4px;
    color: $text;
    font-size: 13px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeWidget::item {
    padding: 7px 8px;
    min-height: 18px;
    border-radius: $r_panel;
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
    border-radius: $r_panel;
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
    border-radius: $r_panel;
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
    border-radius: $r_ctl;
    padding: 2px 10px;
    font-size: 12px;
    min-width: 50px;
    max-width: 70px;
}
QPushButton#tableOpenBtn:hover {
    /* 背景（primary 46% 淡染）过渡走 SmoothButton overlay —— 保持 ghost
       语系、不再突跳实底；文字保持主色，描边加深一档 */
    border-color: $primary_border_strong;
}

/* ---- 网址导航行（拖拽动画列表，替代导航页的 QTableWidget）---- */
QFrame#navRow {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
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
    border-radius: $r_chip;
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
    border-radius: $r_chip;
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
    border-radius: $r_chip;
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
    border-radius: $r_chip;
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
    border-radius: $r_panel;
    padding: 6px;
}
QMenu::item {
    padding: 8px 28px 8px 16px;
    border-radius: $r_panel;
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
    border-radius: $r_panel;
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
    border-radius: $r_panel;
    background-color: transparent;
}
QTabBar::tab {
    background-color: transparent;
    color: $text_secondary;
    padding: 8px 16px;
    border-radius: $r_ctl;
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
    border-radius: $r_ctl;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $primary;
    border-radius: $r_chip;
}

/* ---- 设置页 / 通用滚动区（视口透明，避免透明窗口下渲染成黑色）---- */
QScrollArea, QScrollArea > QWidget {
    background-color: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ====================================================================
   ---- 键盘焦点态（UI 强化方案 A4）----
   纪律一：**只改 border-color，绝不增删边框宽度、绝不改 padding/margin**。
   下列每个控件在基态就已声明 1px 边框（透明或弱色），所以焦点态不改变
   盒模型 → 不触发重排，项目里大量几何断言才不会被这一层影响。
   纪律二：环色按「环压在什么底色上」选 ——
     · 透明/浅底 → $focus_ring（对面板底 ≥4:1）
     · 主色实底 → $on_primary（与主色 ≥4.5:1，护栏 test_theme_contrast 背书）
     · 危险底   → $danger（对白玻璃底约 4.2:1）
   为什么不用 outline：Qt QSS 的 outline 只对 item view 可靠，且不参与盒模型，
   各控件表现不一；统一用 1px 边框最可控。
   注意：QPushButton#stepBtn（Controls.Stepper 的 ± 钮）与
   QPushButton#navGroupHeader（NavGroupHeader）都是显式 NoFocus，不在这里
   出现死规则；步进器只有可键入的 QLineEdit#stepValue 需要焦点环。
   ==================================================================== */
QPushButton:focus {
    /* 基础按钮/primaryBtn 是主色实底，深墨环才看得见 */
    border: 1px solid $on_primary;
    outline: none;
}
QPushButton#primaryBtn:focus {
    border: 1px solid $on_primary;
    outline: none;
}
QPushButton#secondaryBtn:focus,
QPushButton#textBtn:focus,
QPushButton#iconBtn:focus,
QPushButton#navBtn:focus,
QPushButton#settingsNavBtn:focus,
QPushButton#tableOpenBtn:focus {
    border: 1px solid $focus_ring;
    outline: none;
}
QPushButton#dangerBtn:focus {
    border: 1px solid $danger;
    outline: none;
}
/* 列表基态是 outline:none（原生焦点框被关掉了），不补这一条键盘用户
   在列表里完全看不到自己在哪 */
QListWidget:focus {
    border: 1px solid $focus_ring;
    outline: none;
}
/* 复选标记的焦点环写在 indicator 子控件上。
   ★ 写法必须是「子控件在前、伪态在后」= ``QCheckBox::indicator:focus``。
   实测（2026-09-30，见 tests/test_keyboard_focus.py 的变体探针结论）：
   写成 ``QCheckBox:focus::indicator`` 会被 Qt **无条件应用** —— 从未聚焦的
   复选框也带着焦点环，等于把「焦点态」变成「常态」。 */
QCheckBox::indicator:focus {
    border: 1px solid $focus_ring;
}
""")
_QSS_MAIN_WINDOW = _QSS_MAIN_WINDOW  # 保持名称稳定，便于外部引用


# ====================================================================
# 小卡片 QSS
# ====================================================================
_QSS_CARD_WINDOW = Template("""
QWidget#cardContainer {
    background-color: transparent;
    border-radius: $r_win;
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
    border-radius: $r_panel;
    font-size: 17px;
    padding: 0px;
}
QPushButton#sideTabIconBtn:hover {
    /* 背景（a12）过渡走 SmoothButton overlay，文字色保持即时 */
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
    border-radius: $r_ctl;
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
    border-radius: $r_ctl;
    font-size: 12px;
    font-weight: bold;
    padding: 0px;
}
QPushButton#cardCloseBtn:hover {
    /* 背景（$danger 实底）过渡走 SmoothButton overlay，白字保持即时 */
    color: white;
}
QPushButton#nextBtn {
    /* UI 重构 01：渐变退役，改与新 primaryBtn 一致的主色实底。
       A4：1px 透明边框保留（焦点环不撑盒模型），padding 补偿不动。 */
    background-color: $primary;
    color: $on_primary;
    border: 1px solid transparent;
    border-radius: $r_ctl;
    padding: 8px 23px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#nextBtn:hover {
    /* 背景（$primary_hover）过渡走 SmoothButton overlay，文字色保持即时 */
    color: $on_primary;
}

/* ---- 日程任务 ---- */
QLineEdit#taskInput {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_ctl;
    padding: 6px 10px;
    font-size: 13px;
    color: $text;
}
QLineEdit#taskInput:focus {
    border: 1px solid $focus_ring;
    outline: none;
}
QDateEdit#taskDate {
    background-color: $panel_fill;
    border: 1px solid $panel_edge;
    border-radius: $r_ctl;
    /* 右侧 30px 留给自绘的日历图标钮；旧 ::drop-down 规则随原生弹层一起
       退役（原生微调钮/下拉箭头已由 setButtonSymbols + 关闭 calendarPopup
       彻底关掉，见 src/date_picker.py） */
    padding: 5px 30px 5px 10px;
    font-size: 12px;
    color: $text;
}
QPushButton#taskAddBtn {
    background-color: $primary;
    color: $on_primary;
    /* A4：同 #nextBtn，透明边框 + padding 补偿（7px 15px → 6px 14px） */
    border: 1px solid transparent;
    border-radius: $r_ctl;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton#taskAddBtn:hover {
    /* 背景（$primary_hover）过渡走 SmoothButton overlay，文字色保持即时 */
    color: $on_primary;
}
QListWidget#taskList {
    background-color: transparent;
    border: 1px solid $panel_edge;
    border-radius: $r_panel;
    padding: 5px;
    font-size: 13px;
    color: $text;
    outline: none;
}
QListWidget#taskList::item {
    padding: 7px 9px;
    border-radius: $r_panel;
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
    border-radius: $r_ctl;
    padding: 8px;
    font-size: 13px;
    color: $text;
}
QTextEdit#noteEdit:focus {
    border: 1px solid $focus_ring;
    outline: none;
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
    border-radius: $r_chip;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $primary_a30; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ---- 网址导航（双列卡片：标题+域名副标题） ---- */
QPushButton#navSiteCard {
    background-color: $primary_a12;
    border: 1px solid $primary_a30;
    border-radius: $r_ctl;
    padding: 0px;
}
QPushButton#navSiteCard:hover {
    /* 背景（a18）过渡走 SmoothButton overlay（复合卡片按钮只换底不位移），
       边框变化保持即时 */
    border: 1px solid $primary;
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
    border-radius: $r_panel;
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
    border-radius: $r_ctl;
    font-size: 11px;
    padding: 0px;
}
QPushButton#fragCopyBtn {
    background-color: $primary_a12;
    color: $primary;
    border: 1px solid $primary_a30;
}
QPushButton#fragCopyBtn:hover {
    /* 背景（$primary 实底）过渡走 SmoothButton overlay，文字色保持即时 */
    color: $on_primary;
}
QPushButton#fragDelBtn {
    background-color: $danger_alpha;
    color: $danger;
    border: 1px solid $danger_border;
}
QPushButton#fragDelBtn:hover {
    /* 背景（$danger 实底）过渡走 SmoothButton overlay，白字保持即时 */
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
    border-radius: $r_panel;
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

/* ====================================================================
   ---- 键盘焦点态（A4）---- 规则同主窗口：只改 border-color。
   卡片窗是常驻悬浮窗，Tab 键在这里是真实的导航路径（7 个模式 Tab +
   关闭/翻页/动作按钮），此前只有 taskInput / noteEdit 有焦点态。
   ==================================================================== */
QPushButton#sideTabIconBtn:focus,
QPushButton#modeBtn:focus,
QPushButton#navSiteCard:focus,
QPushButton#fragCopyBtn:focus,
QToolButton#appLaunchBtn:focus {
    border: 1px solid $focus_ring;
    outline: none;
}
QPushButton#modeBtn:checked:focus,
QPushButton#nextBtn:focus,
QPushButton#taskAddBtn:focus {
    /* 主色实底：环必须用深墨色（$on_primary 与 $primary ≥4.5:1） */
    border: 1px solid $on_primary;
    outline: none;
}
QPushButton#cardCloseBtn:focus,
QPushButton#fragDelBtn:focus {
    border: 1px solid $danger;
    outline: none;
}
QDateEdit#taskDate:focus,
QListWidget#taskList:focus {
    border: 1px solid $focus_ring;
    outline: none;
}
""")


# ====================================================================
# 右键菜单 QSS（悬浮球用）
# ====================================================================
# 2026-10-02 菜单图标化调整（离屏实测，尺寸为 Qt 实际渲染值）：
#   · 决定「图标列」的是 QMenu::item 的 padding-left，不是 QMenu::icon ——
#     实测三档 padding-left（22/30/36px）菜单总宽依次 138/146/152px，
#     线性变化；而 ``QMenu::icon{left:…}`` 那种写法与完全不写逐像素一致，
#     属无效写法，刻意不加。取 30px = 图标 16 + 间隙 5 + 左内边距 9。
#   · 项内边距 8→6px（项高 29px）、外框 padding 6→5px、分隔线 margin
#     4px 8px → 3px 6px：把留白收进内容里，菜单项密度恢复正常。
#   · 项圆角用 $r_ctl(6) 而非 $r_panel(8)：菜单项是「控件」不是「面板」，
#     8px 配 29px 行高四角会圆过头。
_QSS_MENU = Template("""
QMenu {
    background-color: $menu_bg;
    border: 1px solid $primary_a30;
    border-radius: $r_panel;
    padding: 5px;
}
QMenu::item {
    padding: 6px 22px 6px 30px;
    border-radius: $r_ctl;
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
    margin: 3px 6px;
}
""")


# ====================================================================
# 界面缩放 / 全局字号（成熟化 3.5：活字缩放）
# ====================================================================
# 全局字体的基准 pointSize：实际字号 = round(BASE_FONT_PT * ui_scale / 100)。
# 刻意的低风险取舍：ui_scale 只缩放**字号**（QApplication 全局字号 + QSS
# 里全部 font-size，含写死的 px 与 $fs_* 令牌——2026-10-04 P1-3 补齐），
# 不缩放 QSS 里写死的 px **布局**（间距/圆角/控件尺寸）——全量 px token
# 化改动面太大；85–150% 全档观感稳定（设置页文案同口径）。
BASE_FONT_PT = 10

# 界面缩放合法档位（百分比）：config._CONFIG_RANGES 的范围校验之外，
# 设置页下拉框与 apply_app_font 的调用方都以此为准（收敛取值来源）。
UI_SCALE_VALUES = (85, 100, 115, 130, 150)

# 当前生效的缩放档位（模块级单点）：apply_app_font 是唯一写入方，QSS
# 生成端（get_main_window_qss / get_card_window_qss / get_menu_qss）与
# 自绘字号（knowledge_ball 徽标）读它。缺省 100 = 与未缩放逐字节等价。
_ACTIVE_UI_SCALE = 100


def set_ui_scale(ui_scale: int) -> None:
    """登记当前缩放档位（apply_app_font 专属写入方；脏值忽略保持原档）。"""
    global _ACTIVE_UI_SCALE
    try:
        _ACTIVE_UI_SCALE = int(ui_scale)
    except (TypeError, ValueError):
        pass


def current_ui_scale() -> int:
    """当前生效的缩放档位（QSS 生成端 / 自绘字号统一读这里）。"""
    return _ACTIVE_UI_SCALE


# font-size: Npx 的缩放只认这一种写法；`font:` 简写与 pt 单位全仓未用
_FONT_SIZE_PX_RE = re.compile(r"(font-size\s*:\s*)(\d+)px")


def scale_px_fonts(qss: str, ui_scale: int = None) -> str:
    """把成品 QSS / 内联样式里**所有** ``font-size: Npx`` 按档位缩放（纯函数）。

    - 100%（含缺省）→ 逐字节原样返回——这是「默认行为零变化」的硬护栏；
    - 只动 font-size，``padding: 0 13px`` / ``border-radius: 3px`` 等
      布局 px 一律不碰（活字方案的边界：缩字号不缩布局）；
    - 写死 px 与 $fs_* 令牌在替换后同形，一次正则全覆盖；
    - ui_scale 非法（非数值）原样返回，绝不抛异常（QSS 生成是渲染热路径）。
    """
    if ui_scale is None:
        ui_scale = _ACTIVE_UI_SCALE
    try:
        scale = int(ui_scale)
    except (TypeError, ValueError):
        return qss
    if scale == 100 or not qss:
        return qss
    return _FONT_SIZE_PX_RE.sub(
        lambda m: "%s%dpx" % (m.group(1),
                              max(1, round(int(m.group(2)) * scale / 100))),
        qss)


def scaled_font_pt(ui_scale: int) -> int:
    """按 ui_scale 百分比计算实际全局字号 pointSize（纯函数，无 Qt 依赖）。

    - 100 → 10、85 → 8、115 → 12、130 → 13、150 → 15
      （10*0.85=8.5、10*1.15=11.5 落在 .5 上，round 是 half-to-even，
      8.5→8 / 11.5→12；字号差半磅无观感问题）
    - 非法输入（非数值）原样抛 TypeError：调用方应传 config 收敛后的值
    """
    return round(BASE_FONT_PT * ui_scale / 100)


def apply_app_font(ui_scale: int) -> int:
    """把缩放后的字号设为 QApplication 全局字体（活字缩放，即时生效不重启）。

    - 字族固定 Microsoft YaHei（与 QSS 模板的 font-family 同源）
    - pointSize 由 scaled_font_pt 计算；Qt 的字体变更会自动重 polish
      所有未显式指定 font-size 的控件，调用方通常还需走一遍主题刷新
      链（main_window._apply_theme / theme_changed 广播）让 QSS 全量重载
    - **同时登记缩放档位**（set_ui_scale）：之后 get_*_qss 生成 QSS 时
      把全部 font-size 缩放到同一档——QSS 写死 px 的字号也跟随缩放
      （P1-3 补齐；登记必须在生成之前，调用方先生成 QSS 再调本函数的
      旧顺序不会出现，主窗刷新链是 apply_app_font → _apply_theme）
    - 无 QApplication 实例（纯逻辑测试 / 工具脚本）时跳过设置，仅返回
      计算结果——本函数保持「模块级可导入、无 GUI 可调用」
    - 返回实际应用的 pointSize（测试断言用）
    """
    set_ui_scale(ui_scale)
    pt = scaled_font_pt(ui_scale)
    try:
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return pt
        font = QFont("Microsoft YaHei")
        font.setPointSize(pt)
        app.setFont(font)
    except Exception:
        pass
    return pt


# ====================================================================
# 主题取值与「跟随系统」解析（成熟化 3.1）
# ====================================================================
# config["theme"] 的全部合法取值："light" / "dark" 是显式选择，
# "follow" 表示跟随系统深浅色。config.py 引用本常量做白名单校验；
# 本模块的模块级「不 import PyQt6」约束不破坏——Qt 相关引用全部
# 延迟到函数体内（纯逻辑测试无 GUI 也能 import 本模块）。
THEME_VALUES = frozenset({"light", "dark", "follow"})


def _system_scheme(style_hints=None) -> str:
    """读系统深浅色偏好，返回 "light" / "dark" / "unknown"。

    ``style_hints`` 可注入（测试替身用）；缺省取当前 QGuiApplication
    的 styleHints()。PyQt6 6.7 起才有 Qt.ColorScheme / colorScheme()，
    老版本、无 GUI 环境（无 QGuiApplication 实例）或调用出错一律归为
    "unknown"，由调用方决定回落色（本模块回落深色）。
    """
    if style_hints is None:
        try:
            from PyQt6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is None:            # 无 GUI 环境（纯逻辑测试 / 工具脚本）
                return "unknown"
            style_hints = app.styleHints()
        except Exception:
            return "unknown"
    try:
        from PyQt6.QtCore import Qt
        if not hasattr(Qt, "ColorScheme"):
            return "unknown"           # PyQt6 < 6.7：没有色 scheme API
        scheme = style_hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    return "unknown"


def resolve_theme_name(config_value, style_hints=None) -> str:
    """把 config 的 theme 取值解析成具体主题名 "light" / "dark"。

    - "light" / "dark" 原样返回（显式选择不经过系统判断，现有调用零变化）
    - "follow" 读系统色 scheme：Light → light，Dark → dark；
      Unknown / 老版本 PyQt6 / 无 GUI 环境一律回落 **dark**（与
      DEFAULT_THEME 一致，观感与「未选跟随」时的兜底一致）
    - 其余非法值回落 DEFAULT_THEME（与旧 get_colors 对未知主题的
      兜底同口径，不抛错）
    """
    if config_value == "follow":
        return "light" if _system_scheme(style_hints) == "light" else "dark"
    if config_value in ("light", "dark"):
        return config_value
    return DEFAULT_THEME


def next_theme_on_toggle(config_value, current_theme=None,
                         style_hints=None) -> tuple:
    """「切换主题」动作（Ctrl+T / 标题栏主题钮）的下一站。

    返回 ``(是否落盘, 应用主题名)``：

    - config 是显式 ``light`` / ``dark``：翻到相反主题并落盘（既有行为）；
    - config 是 ``"follow"``：只把**本会话**翻到相反主题、**不落盘** ——
      「跟随系统」的语义是系统变我变，一次快捷键就把偏好固化成显式主题
      等于静默取消跟随（成熟化路线图遗留备忘的 bug）。重启后回到跟随。

    翻转基准永远是「现在显示的主题」：``current_theme`` 是显示中的主题
    （可为 "follow" 原始值，非法 / 缺省时退回从 ``config_value`` 解析）。
    ★ 判定必须读 config 而不是显示值——follow 会话内翻转后显示值已是
    显式名，若按它落盘，第二按就会把跟随偏好固化掉。
    """
    base = current_theme if current_theme in ("light", "dark") else config_value
    resolved = resolve_theme_name(base, style_hints)
    applied = "dark" if resolved == "light" else "light"
    return config_value != "follow", applied


# ====================================================================
# 公开接口
# ====================================================================
def set_accent(accent_id, custom_hex: str = "") -> None:
    """设置当前强调色（全局单点），之后所有 ``get_colors()`` 自动带上。

    走「模块级当前值」而不是给 ``get_colors`` 加参数，是因为全仓有数十处
    调用点（含插件），逐一传参会漏。默认 ``default`` 时 override 为空、
    返回 THEMES 原字典，旧行为逐字节不变。
    """
    global _ACTIVE_ACCENT, _ACTIVE_CUSTOM
    ident = accent_id if accent_id in accent.ACCENT_IDS else accent.DEFAULT_ACCENT
    if (ident, custom_hex) == (_ACTIVE_ACCENT, _ACTIVE_CUSTOM):
        return
    _ACTIVE_ACCENT = ident
    _ACTIVE_CUSTOM = custom_hex or ""
    _ACCENT_CACHE.clear()


def get_accent() -> tuple:
    """当前强调色 ``(id, custom_hex)``（供设置面板回填 UI 状态）"""
    return (_ACTIVE_ACCENT, _ACTIVE_CUSTOM)


def get_colors(theme_name: str = DEFAULT_THEME) -> dict:
    """获取指定主题的配色字典。

    ``theme_name`` 允许直接传 config 的原始取值（含 "follow"）：
    先经 resolve_theme_name 解析成具体主题再查表，未知值仍回退默认
    主题——调用方无需关心 "follow" 的存在。

    叠加了 `set_accent()` 设置的强调色；未设置强调色时直接返回 THEMES
    里的原字典（不是副本，保持既有语义）。
    """
    name = resolve_theme_name(theme_name)
    if _ACTIVE_ACCENT == accent.DEFAULT_ACCENT and not _ACTIVE_CUSTOM:
        return THEMES.get(name, THEMES[DEFAULT_THEME])
    key = (name, _ACTIVE_ACCENT, _ACTIVE_CUSTOM)
    merged = _ACCENT_CACHE.get(key)
    if merged is None:
        merged = dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
        merged.update(accent.build_accent_override(
            _ACTIVE_ACCENT, name, _ACTIVE_CUSTOM))
        _ACCENT_CACHE[key] = merged
    return merged


def get_main_window_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取大窗口主UI的QSS（font-size 已按当前 ui_scale 档位缩放）"""
    return scale_px_fonts(_QSS_MAIN_WINDOW.substitute(get_colors(theme_name)))


def get_card_window_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取小卡片弹窗的QSS（font-size 已按当前 ui_scale 档位缩放）"""
    return scale_px_fonts(_QSS_CARD_WINDOW.substitute(get_colors(theme_name)))


def get_menu_qss(theme_name: str = DEFAULT_THEME) -> str:
    """获取右键菜单的QSS（font-size 已按当前 ui_scale 档位缩放）"""
    return scale_px_fonts(_QSS_MENU.substitute(get_colors(theme_name)))
