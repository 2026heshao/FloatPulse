# -*- coding: utf-8 -*-
"""
====================================================================
首启引导模块  -  onboarding（成熟化 3.4）
====================================================================
第一次启动时弹出的三步欢迎向导（QStackedWidget 步进），让新用户
30 秒内知道：① 这个程序是干嘛的 + 全局热键速查；② 悬浮球怎么用；
③ AI 是可选增强，不配置不影响任何功能。

设计要点：
  1. 纯逻辑面（should_show / mark_done / mark_show_again）与 UI 拆开，
     不起 GUI 也能测（首次/再次弹出的判定链与落盘行为）
  2. 对话框复用 GlassDialog 玻璃基类：无边框 + 玻璃壳 + 自绘标题栏 +
     主窗口 QSS，深浅两主题自动跟随（与全局搜索弹窗同一套风格）
  3. 关闭即落盘：完成 / Esc / 跳过 / 标题栏 × 任何路径关闭后
     first_run_done 置 True，之后不再骚扰（接线见 knowledge_ball.main）
  4. 文案口径对齐 README 与 F1 使用说明：全局热键只写已注册的
     Ctrl+Alt+K（快捕条）/ Ctrl+Alt+S（截图钉屏）/ Ctrl+K（站内搜索，
     需装 kb-search 插件）/ Esc（关闭当前表面）
====================================================================
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from src.glass_dialog import GlassDialog


# ====================================================================
# 纯逻辑面（无 GUI 依赖，测试直打）
# ====================================================================
def should_show(config_manager) -> bool:
    """是否需要弹首启引导：first_run_done 非 True 就需要（含旧配置缺键）。"""
    return not bool(config_manager.get("first_run_done", False))


def mark_done(config_manager) -> bool:
    """向导关闭（完成 / Esc / 跳过 / 标题栏 ×）统一落盘：置 True 并 save。

    返回 ConfigManager.set 的结果（非法写入防护失败时为 False，不写盘）。
    """
    ok = config_manager.set("first_run_done", True)
    if ok:
        config_manager.save()
    return ok


def mark_show_again(config_manager) -> None:
    """设置页「重看引导」入口：置回未完成并立即落盘。

    向导关闭时调用方再走 mark_done 置回 True——重看与首启共用同一条
    状态链，不会出现「看完引导下次启动又弹」的残留态。
    """
    config_manager.set("first_run_done", False)
    config_manager.save()


# ====================================================================
# 向导文案（口径对齐 README「默认热键」表与设置页关于页）
# ====================================================================
# 全局热键速查（① 欢迎页）：只列已注册/README 口径内的热键，
# Ctrl+K 标注「需安装 kb-search 插件」，不编造未实现的快捷键。
_HOTKEY_ROWS = (
    ("Ctrl+Alt+K", "快速捕捉条：任何界面下随手记一条碎片"),
    ("Ctrl+Alt+S", "截图钉屏：框选一块屏幕置顶参考（可批注）"),
    ("Ctrl+K",     "站内搜索：全局搜碎片 / 笔记 / 任务（需安装 kb-search 插件）"),
    ("Esc",        "关闭卡片 / 取消截图（不退出程序）"),
)

# 悬浮球用法（② 悬浮球页）：拖拽吸边 / 悬停弹卡 / 拖文件进碎片池 /
# 拖 .exe/.lnk 建启动器 / 关窗收托盘——README 功能表原文口径。
_BALL_TIPS = (
    "·  拖动球贴到屏幕边缘：自动吸附隐藏，鼠标移近自动滑出",
    "·  鼠标悬停在球上：弹出小卡片，任务 / 笔记 / 碎片一览",
    "·  把文件拖到球上：自动收进碎片池，不打断当前工作",
    "·  拖入 .exe / .lnk：一键生成软件启动器（软件导航页）",
    "·  主窗口点关闭按钮：收进系统托盘，从托盘图标或球随时找回",
)


class WelcomeDialog(GlassDialog):
    """首启三步欢迎向导（玻璃风格，与主窗口观感一致）

    步进状态机（step 索引 0..2，供测试断言）：
      go_next()  末步变「开始使用」→ accept；否则 step+1
      go_prev()  首步禁用；step-1
      skip()     reject（与 Esc / 标题栏 × 同路径，宿主统一 mark_done）
    """

    STEPS = ("欢迎", "悬浮球", "完成")     # 步骤名（指示条文案用）

    def __init__(self, host=None, parent=None):
        super().__init__(host=host, title="欢迎使用生活悬浮球",
                         subtitle="三步上手", size=(560, 470), parent=parent)
        self._step = 0

        # ---- 三步页面栈 ----
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_page_welcome())
        self._stack.addWidget(self._build_page_ball())
        self._stack.addWidget(self._build_page_finish())
        self.body_layout.addWidget(self._stack, 1)

        # ---- 步骤指示条 + 底部按钮（add_footer 追加在 body 末尾=页脚）----
        self._step_label = QLabel()
        self._step_label.setObjectName("hintLabel")
        self._step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self._step_label)

        # 顺序：跳过靠左收尾区，上一步 / 下一步主操作靠右
        self._skip_btn, self._prev_btn, self._next_btn = self.add_footer([
            ("跳过引导", "secondaryBtn", self.skip),
            ("上一步", "secondaryBtn", self.go_prev),
            ("下一步", "primaryBtn", self.go_next),
        ])
        self._sync_step()

    # ---------------- 三步页面 ----------------
    def _build_page_welcome(self) -> QWidget:
        """① 欢迎页：一句话定位 + 全局热键速查表"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(10)

        hello = QLabel("👋 从随手记开始")
        hello.setObjectName("pageTitle")
        v.addWidget(hello)

        intro = QLabel(
            "生活悬浮球管的是「记完之后的那一段」：碎片随手进池，"
            "归类成任务 / 笔记，再一键导出进你已有的知识库。")
        intro.setWordWrap(True)
        v.addWidget(intro)

        cap = QLabel("全局热键速查（不打开主窗口也能用）")
        cap.setObjectName("sectionLabel")
        v.addWidget(cap)
        v.addLayout(self._build_hotkey_table())
        v.addStretch()
        return page

    def _build_hotkey_table(self) -> QGridLayout:
        """热键速查表：左列键位 chip（valueChip 样式）+ 右列说明"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        for row, (key, desc) in enumerate(_HOTKEY_ROWS):
            k = QLabel(key)
            k.setObjectName("valueChip")
            d = QLabel(desc)
            grid.addWidget(k, row, 0,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(d, row, 1,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)
        return grid

    def _build_page_ball(self) -> QWidget:
        """② 悬浮球页：怎么用（吸边 / 悬停弹卡 / 拖放 / 托盘找回）"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(10)

        title = QLabel("🫧 悬浮球是你的随手入口")
        title.setObjectName("pageTitle")
        v.addWidget(title)

        lead = QLabel("桌面上那颗球不需要「打开软件」这个动作：")
        lead.setWordWrap(True)
        v.addWidget(lead)

        tips = QLabel("\n".join(_BALL_TIPS))
        tips.setTextFormat(Qt.TextFormat.PlainText)
        tips.setWordWrap(True)
        v.addWidget(tips, 1)
        return page

    def _build_page_finish(self) -> QWidget:
        """③ 收尾页：AI 可选说明（不配置不影响任何功能）+ 开始使用"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(10)

        title = QLabel("🎉 一切就绪")
        title.setObjectName("pageTitle")
        v.addWidget(title)

        # AI 说明装进玻璃卡片做视觉强调：定位是「可选增强」而非必填配置
        card = QFrame()
        card.setObjectName("glassCard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        ai_note = QLabel(
            "🧠 AI 能力（智能归类 / 问答）是可选增强：需要时到"
            "「设置 → 🧠 AI 配置」接一次后端即可；不配置不影响任何功能。")
        ai_note.setWordWrap(True)
        cv.addWidget(ai_note)
        v.addWidget(card)

        tip = QLabel("随时可到「设置 → 🚀 启动与系统 → 新手引导」重看本向导，"
                     "F1 打开使用说明。")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch()
        return page

    # ---------------- 步骤导航（状态机，测试直打 step 索引）----------------
    @property
    def step(self) -> int:
        return self._step

    def go_next(self):
        """下一步：末步即「开始使用」→ accept 关闭向导"""
        if self._step >= len(self.STEPS) - 1:
            self.accept()
            return
        self._set_step(self._step + 1)

    def go_prev(self):
        """上一步：首步为 no-op（按钮此时已禁用，双保险）"""
        self._set_step(self._step - 1)

    def skip(self):
        """跳过引导：reject 关闭（与 Esc / 标题栏 × 同路径，宿主统一 mark_done）"""
        self.reject()

    def _set_step(self, idx: int):
        self._step = max(0, min(idx, len(self.STEPS) - 1))
        self._sync_step()

    def _sync_step(self):
        """按 step 同步页面栈 / 指示条 / 三个按钮的文案与可用态"""
        last = self._step >= len(self.STEPS) - 1
        self._stack.setCurrentIndex(self._step)
        self._step_label.setText(
            f"第 {self._step + 1} / {len(self.STEPS)} 步 · {self.STEPS[self._step]}")
        self._prev_btn.setEnabled(self._step > 0)
        self._next_btn.setText("开始使用" if last else "下一步")
        # 末步「跳过」与「开始使用」语义重合，隐藏避免两个同义按钮
        self._skip_btn.setVisible(not last)
