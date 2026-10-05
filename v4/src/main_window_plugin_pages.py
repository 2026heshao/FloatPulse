# -*- coding: utf-8 -*-
"""MainWindow 插件页注册机器 mixin（D2 上帝类拆分，拆分计划-wiring-2026-10-05 T03）。

从 ``src/main_window.py`` 外迁：``_build_plugins_page`` /
``register_plugin_page`` / ``show_plugin_page`` / ``unregister_plugin_page``
/ ``rebuild_plugin_page``（2026-09-27 插件页面注入族）。

**mixin 铁律**：无 pyqtSignal、无 __init__、只经 self 消费宿主状态、
禁止 import 宿主模块。``set_plugin_loader`` / ``plugin_loader`` @property
留在主类（D3 公开面，契约测试按主类体收集）。
"""

from collections.abc import Callable
from typing import Any

from PyQt6.QtWidgets import QButtonGroup, QLayout, QStackedWidget, QWidget

from src.icons import plugin_page_icon, strip_leading_emoji


class PluginPagesMixin:
    """插件页注册 / 展示 / 注销 / 重建（经 self 消费宿主 _stack 与导航机器）。"""

    # ---- 宿主（MainWindow）依赖面裸注解：仅类型声明，不建类属性、
    # ---- 零运行时行为、MRO 零变化（pyright reportAttributeAccessIssue 消音）。
    NAV_PAGE_INDEX: dict
    NAV_PAGE_TITLES: dict
    _stack: QStackedWidget
    _nav_btns: dict
    _nav_order: list
    _nav_group: QButtonGroup
    _nav_btns_layout: QLayout
    _make_nav_button: Callable[..., object]
    _apply_nav_order: Callable[..., None]
    _switch_page: Callable[[int], None]
    show: Callable[..., None]
    raise_: Callable[..., None]
    activateWindow: Callable[..., None]
    plugin_loader: Any

    # ---- 插件中心页面 ----
    def _build_plugins_page(self):
        """插件中心面板：展示已安装插件与使用说明（9 号页，只读展示）"""
        from src.plugins_panel import PluginsPanel
        return PluginsPanel(self)

    # ---- 插件页面注入（2026-09-27：manifest.page → 主窗口导航页） ----
    def register_plugin_page(self, key: str, title: str, widget) -> int:
        """注册插件提供的导航页面（knowledge_ball 对页面插件调用）。

        - 物理索引 = QStackedWidget 追加到末尾（0-9 为固定页，插件页 10+）
        - 侧栏按钮**参与拖拽换位**（2026-09-28 用户要求：AI 助手先行，
          后续所有页面插件一致）：顺序记进 ``nav_order`` 持久化，新插件
          默认排在末位（设置之前），重启后沿用用户调整过的位置
        - **不写入** ``last_page_index``（config 范围上限 9）
        - **幂等**：同一 key 重复注册（插件中心「重新扫描」会重跑装配）
          只替换页面内容，索引与按钮保持稳定，旧页面被安全销毁
        返回物理索引。
        """
        # ★ 2026-09-30（UI 强化 A2）：插件 manifest 的标题常自带 emoji 前缀
        #   （"🤖 AI 助手"）。图标改由宿主自绘后，这类前缀不但多余，还会因
        #   系统 emoji 字体缺失退化成豆腐块 —— 统一在这里剥掉。
        # ★ 2026-10-02（P5 插件图标）：出厂五插件改挂专属图标——icons 表
        #   PLUGIN_ICONS 按插件 id 映射（plugin_page_icon 消费）。宿主仍不
        #   认识任意插件的语义，映射查不到（第三方插件 / 未收录的 id）一律
        #   落回通用占位 PLUGIN_PAGE_ICON，兜底口径与占位时代完全一致。
        title = strip_leading_emoji(title)
        old_index = self.NAV_PAGE_INDEX.get(key)
        # ⚠ 幂等判定必须以「本实例已持有该键的按钮」为准：
        #   NAV_PAGE_INDEX 是模块级 dict，跨 MainWindow 实例共享（测试 /
        #   多窗口场景会建第二个实例）——只看 old_index 会让新实例误判
        #   「已注册」，只换 widget 不建按钮，插件导航键永远出不来。
        if old_index is not None and key in self._nav_btns:
            old = self._stack.widget(old_index)
            if old is not None:
                self._stack.removeWidget(old)
                old.deleteLater()
            self._stack.insertWidget(old_index, widget)
            # 侧栏按钮已存在：只更新文案
            # ⚠ 原实现写的是 ``for b, k in self._nav_btns.items():
            #   if k == key: b.setText(...)`` —— _nav_btns 是 {key: btn}，
            #   这样 unpack 出来 b 是**键**、k 是按钮，``k == key`` 恒为
            #   False，文案更新静默失效（"重复注册只换 widget 不换索引"
            #   的契约里漏了这一半）。2026-09-29 由离屏脚本 K 组断言抓出。
            self.NAV_PAGE_TITLES[key] = title
            old_btn = self._nav_btns.get(key)
            if old_btn is not None:
                old_btn.setText(title)
            return old_index

        index = self._stack.count()
        self.NAV_PAGE_TITLES[key] = title
        self.NAV_PAGE_INDEX[key] = index
        self._stack.addWidget(widget)
        btn = self._make_nav_button(title, index, nav_key=key,   # 参与换位
                                    icon_name=plugin_page_icon(key))
        self._nav_btns[key] = btn
        # 顺序：config 里记过位置（上次会话拖过）→ 沿用；新插件 → 追加
        # 到末位（设置之前）。_apply_nav_order 统一重排并交还布局。
        if key not in self._nav_order:
            self._nav_order.append(key)
        self._apply_nav_order(list(self._nav_order), save=True)
        return index

    def show_plugin_page(self, key: str) -> bool:
        """打开主窗口并切到插件页面（插件热键动作经 ctx.parent_window()
        调用的**约定公开入口**，与 show_xxx_page 系列同款行为）。

        key = 注册时的 ``plugin:<插件id>``；未知 key 返回 False。
        """
        idx = self.NAV_PAGE_INDEX.get(key)
        if idx is None or idx < 10:      # 只允许切到插件页段
            return False
        self.show()
        self.raise_()
        self.activateWindow()
        self._switch_page(idx)
        return True

    def unregister_plugin_page(self, key: str) -> bool:
        """注销插件提供的导航页面（停用 / 卸载页面插件时由宿主调用）。

        与 register_plugin_page 成对。三处停用路径——插件中心启停开关、
        启动回放 ``plugins_disabled``、总闸关闭——页面此前都无人摘除，
        导致「停用了导航键和页面还挂在主窗口」；本方法补齐注销环节。

        索引策略：**空占位补槽**而非直接 removeWidget——QStackedWidget
        移除中间页会把后续索引整体前移，连坐其他插件页的
        ``NAV_PAGE_INDEX`` 与 ``_nav_group`` 按钮 id 映射；占位页无按钮
        不可达，成本可忽略。注销后同步清除两个模块级 dict 项，重新启用
        时 register_plugin_page 走全新注册分支（新索引、新按钮）。
        正在显示该页时先切回首页。key 未注册返回 False（可安全重入）。
        """
        idx = self.NAV_PAGE_INDEX.pop(key, None)
        if idx is None or idx < 10:      # 未知 key；固定页段防御性回滚
            if idx is not None:
                self.NAV_PAGE_INDEX[key] = idx
            return False
        self.NAV_PAGE_TITLES.pop(key, None)
        if self._stack.currentIndex() == idx:
            self._switch_page(0)
        old = self._stack.widget(idx)
        if old is not None:
            self._stack.removeWidget(old)
            self._stack.insertWidget(idx, QWidget())   # 占位：索引全稳定
            old.deleteLater()
        btn = self._nav_btns.pop(key, None)
        if btn is not None:
            self._nav_group.removeButton(btn)
            self._nav_btns_layout.removeWidget(btn)
            # ★ deleteLater 是延迟回收（回到事件循环才销毁）：不先 hide 的
            #   话，旧按钮会带着最后几何继续可见地漂在布局外，与新布局的
            #   条目叠画（2026-10-05 插件停用→启用错乱排查的伴生问题）。
            btn.hide()
            btn.deleteLater()
        # 顺序表同步移除并重排落盘（插件键参与换位后它是 order 成员，
        # 留着会让 _apply_nav_order 引用已删除的按钮）
        if key in self._nav_order:
            self._nav_order.remove(key)
            self._apply_nav_order(list(self._nav_order), save=True)
        return True

    def rebuild_plugin_page(self, plugin_id: str) -> bool:
        """重新注册指定插件的页面（插件中心重新启用某插件时调用）。

        页面实例由插件自己的 create_page 产生（与 knowledge_ball 启动装配
        同一契约），这里只负责「要页面 → 塞回主窗口」。插件未加载、未声明
        page 或 create_page 失败一律返回 False，绝不把插件问题抛进 UI 操作。
        register_plugin_page 幂等，重复重建只换 widget 不换索引。
        """
        loader = self.plugin_loader
        if loader is None:
            return False
        for lp in loader.loaded_plugins():
            if lp.plugin_id != plugin_id:
                continue
            spec = getattr(lp, "manifest", {}).get("page")
            if not spec:
                return False
            try:
                widget = lp.plugin.create_page(lp.ctx)
            except Exception:              # noqa: BLE001 - 页面失败不拖垮 UI
                return False
            if widget is None:
                return False
            key = f"plugin:{plugin_id}"
            self.register_plugin_page(key, spec["title"], widget)
            try:
                lp.page_key = key          # 热键动作经 parent_window 切页用
            except Exception:              # noqa: BLE001 - 辅助标记，失败无害
                pass
            return True
        return False

