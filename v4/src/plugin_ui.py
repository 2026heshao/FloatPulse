# -*- coding: utf-8 -*-
"""
====================================================================
插件 UI 组件  -  plugin_ui
====================================================================
给外置插件用的**官方 UI 组件**，解决「插件弹窗是系统原生外观、
和主窗口风格割裂」的问题。

为什么不放进 plugin_api：
  plugin_api 是纯逻辑契约层，刻意不 import PyQt6（这样 pytest 能
  在无 GUI 环境直接跑它）。UI 组件必须依赖 PyQt6，因此单独成模块：
  插件不弹窗就不 import 本模块，互不牵连。

用法（插件的对话框直接继承 PluginDialog）：

    from src.plugin_ui import PluginDialog, flash_button

    class MyDialog(PluginDialog):
        def __init__(self, ctx, parent=None):
            super().__init__(ctx, title="我的面板", subtitle="子标题",
                             parent=parent, size=(760, 560))
            self.body_layout.addWidget(...)        # 内容区
            btns = self.add_footer([
                ("📋 复制", "primaryBtn", self._do_copy),
                ("关闭", "secondaryBtn", self.accept),
            ])

自带能力（全部与主窗口一致，且跟随 light/dark 主题自动换肤）：
  - 无边框 + 真正圆角 + 外圈柔和阴影
  - 玻璃壳（半透明填充 / 顶部高光 / 双色描边 / 噪点）
  - 自绘标题栏（图标 + 标题 + 副标题 + 右上角关闭按钮），可拖动
  - 复用主窗口 QSS，因此 primaryBtn / secondaryBtn / hintLabel /
    sectionLabel / settingsSeparator 等 objectName 直接可用
  - ``ctx`` 也接受直接传宿主窗口（两种写法都能拿到主题）

本模块由宿主提供并维护，属于**插件可用能力的一部分**，比直接 import
``src.glass_dialog`` 更稳妥：后者是宿主内部模块，随时可能重构。
====================================================================
"""

from PyQt6.QtWidgets import QWidget

from src.glass_dialog import GlassDialog, flash_button, make_separator  # noqa: F401


class PluginDialog(GlassDialog):
    """插件对话框基类：GlassDialog 的插件友好封装。

    与直接继承 GlassDialog 的区别只有一点——构造参数收 ``ctx`` 而不是
    ``host``，并自动从 ctx 解析出宿主窗口用于取主题：

        PluginDialog(ctx, title="...")          # 推荐：传 PluginContext
        PluginDialog(None, title="...")         # 也允许：拿不到主题则用默认主题

    取主题失败（ctx.parent_window() 返回 None / 抛异常）不会崩，
    会退回 DEFAULT_THEME——插件在无主窗口场景下依然能弹窗。
    """

    def __init__(self, ctx=None, title: str = "", subtitle: str = "",
                 parent=None, size=None):
        self._plugin_ctx = ctx
        host = self._resolve_host(ctx, parent)
        # 父窗口优先级：显式 parent > ctx.parent_window()
        super().__init__(host=host, title=title, subtitle=subtitle,
                         parent=parent if parent is not None else host,
                         size=size)

    @staticmethod
    def _resolve_host(ctx, parent):
        """从 ctx / parent 里找出可用于取主题的宿主窗口。

        三种调用写法都要支持（否则主题会静默丢失，退化成默认配色）：
          1. PluginDialog(ctx, parent=win)   → parent 就是宿主
          2. PluginDialog(ctx)               → 走 ctx.parent_window()
          3. PluginDialog(win)               → 直接传宿主窗口（无 parent_window 方法）
        """
        # 写法 1：parent 本身是宿主
        if parent is not None and hasattr(parent, "current_theme"):
            return parent
        if ctx is None:
            return None
        # 写法 3：直接传宿主窗口（有 current_theme 但没有 parent_window）
        if hasattr(ctx, "current_theme") and not callable(
                getattr(ctx, "parent_window", None)):
            return ctx
        # 写法 2：标准 PluginContext
        getter = getattr(ctx, "parent_window", None)
        if callable(getter):
            try:
                win = getter()
            except Exception:                   # noqa: BLE001 - 取主题不能崩
                win = None
            if win is not None:
                return win
        return None


def make_section_label(text: str) -> "QWidget":
    """小标题标签（复用主窗口 sectionLabel 样式）"""
    from PyQt6.QtWidgets import QLabel
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def make_hint_label(text: str) -> "QWidget":
    """灰色提示标签（复用主窗口 hintLabel 样式）"""
    from PyQt6.QtWidgets import QLabel
    label = QLabel(text)
    label.setObjectName("hintLabel")
    return label


__all__ = [
    "PluginDialog",
    "flash_button",
    "make_separator",
    "make_section_label",
    "make_hint_label",
]
