# -*- coding: utf-8 -*-
"""
====================================================================
插件中心面板  -  PluginsPanel
====================================================================
主窗口第 9 页：集中展示悬浮球外置插件、使用说明与加载状态。

职责：
  1. 列出 PluginLoader **成功加载**的插件：名称 / 版本 / id / 描述 /
     状态标签 / 动作清单（标题 + 热键 + 是否进右键菜单）/ 动作级告警 / 依赖
  2. 列出**加载失败**的插件：失败阶段 + 原因 + 修复建议 + 打开目录
     （解决「插件装死了用户不知道」——此前失败项不进任何列表）
  3. 每个插件提供「打开目录」「查看使用说明」「卸载」，以及启用/停用开关
     （调用此前全项目无 UI 入口的 ActionRegistry.set_enabled）
  4. 顶部「重新扫描」按钮：无需重启即可加载新放入的插件
  5. 插件总闸关闭时显示提示条；无插件且无失败时显示安装引导空态
  6. **插件商店独立弹窗**（2026-09-28 起，取代原先内嵌在页面里的商店区）：
     「🏪 插件商店」按钮弹出独立窗口（GlassDialog，与主窗口同主题），
     列出商店目录里的 ``*.fpplug`` 可安装包，每个包一个「安装」按钮；
     已装好的包标记「已安装」且按钮禁用。页面本体只留已安装插件卡片

成对目录（面板顶部都会写出绝对路径）：
  - 插件商店   ``<base_dir>/plugin_store/``  放 ``*.fpplug`` 源包，永不被自动解压
  - 安装目录   ``<base_dir>/plugins/``       放解压后的插件文件夹，唯一被加载的位置
  「卸载」只删安装目录那一侧 → 商店里的源包保留 → 卡片回落「未安装」，可再装。

数据来源：host.plugin_loader（MainWindow 持有，knowledge_ball 注入）。
插件描述优先级：manifest.description > 插件类 docstring 首行。

设计约束：
  - UI 层不直接碰插件目录，只经 LoadedPlugin / FailedPlugin / StoreEntry
    公开字段读取（唯一的例外是「卸载」——它必须展示将被删除的绝对路径，
    也只读路径字符串，真正的删除动作委托给 loader.uninstall()）
  - 启停开关经 loader.registry.set_enabled 生效；注册表不可用时降级为只读展示
  - 打开目录用 os.startfile；使用说明优先程序内 Markdown 渲染
  - **安装**只走 loader.install_from_store(id)，不在 UI 层解压任何 zip
====================================================================
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextBrowser, QMessageBox,
)

# 弹窗基类：PluginStoreDialog 在模块加载期就需要它作基类，
# 因此必须是模块级 import（_build_usage_viewer 里的局部 import 只是历史写法）。
from src.glass_dialog import GlassDialog

from src import plugin_market
from src.controls import SmoothButton, EmptyState, IconButton, PageTitle
from src.plugin_net import make_async_getter, make_async_bytes_getter
from src.update_checker import RELEASES_API_URL, check_headers

# 卡片能力标签（商店卡与在线市场卡共用，改一处两处生效）
CAP_LABELS = {
    "network": "网络访问", "write": "写入数据",
    "manage": "改删数据", "ai": "AI 总配置",
}

# 能力徽章短文案（2026-10-01 卡片降噪：卡面只放短词，完整语义进悬停提示）
CAP_BADGES = {
    "network": "网络", "write": "写入",
    "manage": "改删", "ai": "AI",
}

# 能力徽章悬停提示（完整语义；manage 蕴含 write、ai 由设置页勾选授权）
CAP_TIPS = {
    "network": "该插件在 manifest 里声明了 network 能力，"
               "可经宿主网络桥发起联网请求（app.log 可审计）",
    "write": "该插件在 manifest 里声明了 write 能力，"
             "可经宿主桥新增碎片 / 任务 / 笔记（只能新增，"
             "不能修改或删除已有数据）",
    "manage": "该插件在 manifest 里声明了 manage 能力，"
              "可经宿主桥修改 / 完成 / 删除已有的碎片、任务、"
              "笔记（同时具备 write 的只增权限）；删除可由插件"
              "侧发起撤销，每次操作记入 app.log",
    "ai": "该插件在 manifest 里声明了 ai 能力，可接入设置页"
          "「AI 总配置」共用云端 / 本地后端——是否接入由你在"
          "设置页下拉框勾选决定（勾选 = 授权）",
}

# 插件包内使用说明文件约定（按优先级探测；「查看使用说明」按钮用）
USAGE_FILENAMES = ("使用说明.md", "README.md")
# 卡片描述在卡面上最多显示的字符数（超出截断加省略号，全文进悬停提示；
# 双列卡宽下 48 字约一行半——介绍看一眼定位即可，详情看使用说明 md）
DESC_MAX = 48
# 已装插件卡片双列网格的最小容器宽度：低于此值回落单列。
# 依据实测（带主题 QSS，light/dark 一致）：最宽卡片（启停 + 打开目录 +
# 查看使用说明 + 卸载四按钮行）最小宽 368px，两列需容器 ≥ 2×368 + 间距
# 10 + 滚动条/边距余量 ≈ 760，取 780 再留呼吸空间。
# 主窗口里容器 ≈ 窗口宽 − 252（阴影 36 + 侧栏 168 + 内容边距 48），
# 即窗口 ≥ 约 1030 时双列：默认 1280 双列，最小 920 单列。
GRID_TWO_COL_MIN_WIDTH = 780


class _CardsScroll(QScrollArea):
    """插件卡片滚动区：以**视口可用宽度**驱动列数自适应（双列 ↔ 单列）。

    为什么不挂在网格容器上：widgetResizable 下容器被取 max(视口, 自身
    最小宽)——双列网格的最小宽（两张最宽卡 + 间距）大于回落阈值时，
    容器宽度永远压不进阈值以下，resizeEvent 不再触发，单列回落就死锁了
    （离屏无字体环境实测复现）。视口宽度是「可用空间」的可靠信号：滚动区
    本体每次尺寸变化都会走 resizeEvent，与容器是否被钳住无关。
    """

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._panel._reflow_cards(self.viewport().width())


def clip_text(text: str, max_len: int) -> str:
    """超长截断加省略号（纯文本处理；全文由调用方挂进悬停提示）"""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def find_usage_file(plugin_dir: str) -> str:
    """探测插件包内的使用说明文件，返回完整路径（找不到返回空串）"""
    for name in USAGE_FILENAMES:
        path = os.path.join(plugin_dir or "", name)
        if os.path.isfile(path):
            return path
    return ""


def _build_usage_viewer(host, path: str, text: str):
    """构建使用说明查看弹窗（程序内渲染 Markdown，独立出来便于测试）。

    - QTextBrowser.setMarkdown 为 Qt6 内置渲染，零额外依赖
    - 底部保留「用系统程序打开」（交给系统关联，失败不崩溃）
    """
    from src.glass_dialog import GlassDialog
    dlg = GlassDialog(host, title="使用说明",
                      subtitle=os.path.basename(os.path.dirname(path)),
                      size=(640, 560))
    viewer = QTextBrowser()
    viewer.setObjectName("usageViewer")
    viewer.setOpenExternalLinks(False)
    viewer.setMarkdown(text)   # 非 md 内容也能按纯文本显示，不抛异常
    dlg.body_layout.addWidget(viewer, 1)
    dlg.add_footer([
        ("用系统程序打开", "secondaryBtn",
         lambda _checked=False: _startfile_warn(viewer, path)),
        ("关闭", "primaryBtn", dlg.accept),
    ])
    return dlg


def _startfile_warn(parent, path: str):
    """交给系统默认程序打开；无关联或失败时提示而非崩溃"""
    try:
        os.startfile(path)                        # noqa: S606
    except OSError as e:
        QMessageBox.warning(
            parent, "无法打开",
            f"系统没有找到能打开此文件的程序：\n{path}\n\n{e}\n\n"
            f"可以关联一个 Markdown 编辑器，或直接在本窗口查看。")


class PluginsPanel(QWidget):
    """插件中心面板（主窗口 9 号页）"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._cards_layout = None
        self._error_layout = None
        self._error_box = None
        self._error_label = None
        self._store_dialog = None     # 插件商店弹窗（懒创建，见 _on_open_store_dialog）
        self._empty_label = None
        self._gate_label = None
        self._count_label = None
        self._dir_label = None
        self._store_dir_label = None
        self._rescan_btn = None
        self._build_ui()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = PageTitle("plugins", "插件中心", self._host)
        header.addWidget(title)
        header.addStretch()
        self._count_label = QLabel("共 0 个插件")
        self._count_label.setObjectName("hintLabel")
        header.addWidget(self._count_label)
        v.addLayout(header)

        # ---- 提示 ----
        hint = QLabel("插件商店放插件包（.fpplug / 含 manifest.json 的文件夹），"
                      "点「安装」解压到安装目录后即可使用；"
                      "「卸载」只删安装目录里的副本，商店里的源包会保留，随时能再装。"
                      "插件包内建议放一份「使用说明.md」，摘要会自动显示在卡片上")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 两个目录的绝对路径（用户最常搞混「装在哪 / 包放哪」）----
        # 单行显示 + 完整路径进 tooltip：路径很长时会自动换行占掉太多竖向空间，
        # 单行截断、tooltip 给全文，兼顾可读与紧凑。
        self._dir_label = QLabel()
        self._dir_label.setObjectName("pluginDirLabel")
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._dir_label)

        self._store_dir_label = QLabel()
        self._store_dir_label.setObjectName("pluginDirLabel")
        self._store_dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._store_dir_label)

        # ---- 插件总闸关闭提示条（默认隐藏）----
        self._gate_label = QLabel("插件功能已在设置页停用（全局开关），"
                                  "如需使用请到「设置」开启后重启程序")
        self._gate_label.setObjectName("pluginGateHint")
        self._gate_label.setWordWrap(True)
        self._gate_label.setVisible(False)
        v.addWidget(self._gate_label)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        open_dir_btn = SmoothButton("打开插件目录")
        open_dir_btn.setObjectName("secondaryBtn")
        open_dir_btn.clicked.connect(self._on_open_plugins_dir)
        toolbar.addWidget(open_dir_btn)

        open_store_btn = SmoothButton("插件商店")
        open_store_btn.setObjectName("secondaryBtn")
        open_store_btn.setToolTip("浏览商店目录里的可安装插件包（独立窗口）")
        open_store_btn.clicked.connect(self._on_open_store_dialog)
        toolbar.addWidget(open_store_btn)

        self._rescan_btn = IconButton("refresh", text="重新扫描", icon_size=14,
                                      object_name="secondaryBtn")
        self._rescan_btn.setToolTip(
            "重新扫描插件安装目录与商店目录（不必重启程序）")
        self._rescan_btn.clicked.connect(self._on_rescan)
        toolbar.addWidget(self._rescan_btn)

        toolbar.addStretch()
        v.addLayout(toolbar)

        # ---- 加载失败区（默认隐藏；有失败项时显示在卡片列表上方）----
        self._error_box = QFrame()
        self._error_box.setObjectName("pluginErrorBox")
        eb = QVBoxLayout(self._error_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(8)
        self._error_label = QLabel("加载失败的插件")
        self._error_label.setObjectName("pluginSectionLabel")
        eb.addWidget(self._error_label)
        self._error_layout = QVBoxLayout()
        self._error_layout.setContentsMargins(0, 0, 0, 0)
        self._error_layout.setSpacing(8)
        eb.addLayout(self._error_layout)
        self._error_box.setVisible(False)
        v.addWidget(self._error_box)

        # ---- 滚动区：插件卡片（双列网格，窄窗自动回落单列）----
        # 2026-09-28 起商店区移入独立弹窗（PluginStoreDialog），
        # 页面本体只保留已安装插件卡片。
        # 2026-09-29 起卡片改双列网格：外层 vbox 只负责「网格 + 尾部
        # stretch」，网格本身只装卡片本体——refresh 清空与列数重排都会把
        # 网格整体拆装，stretch 放外层才不会被 takeAt 扫掉。
        scroll = _CardsScroll(self)
        scroll.setObjectName("pluginsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("pluginsContainer")
        self._cards_outer = QVBoxLayout(container)
        self._cards_outer.setContentsMargins(0, 0, 8, 0)
        self._cards_outer.setSpacing(0)
        self._cards_layout = QGridLayout()
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setColumnStretch(0, 1)
        self._cards_layout.setColumnStretch(1, 1)
        self._cards_outer.addLayout(self._cards_layout)
        self._cards_outer.addStretch()           # 卡片永远顶对齐
        self._card_widgets = []                  # 插入顺序的卡片（重排依据）
        self._card_cols = 2                      # 当前列数（_reflow_cards 维护）
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        # ---- 空态引导（A3 通用化，2026-10-01）----
        # ★ objectName 用独立的 pluginEmptyState：QSS 里 QLabel#pluginEmptyHint
        #   是商店弹窗那个 QLabel 空态的契约（verify_kb_search 断言 QSS 含该
        #   选择器），不能动；本组件内部标签走 sectionLabel/hintLabel 通用样式。
        # ★ 绝不能放进 _cards_layout —— 测试对卡片网格有精确占位断言。
        self._empty_label = EmptyState(
            "plugins", "还没有安装任何插件",
            "点上方「插件商店」安装插件包（.fpplug）\n"
            "或把插件文件夹放进安装目录后点「重新扫描」",
            object_name="pluginEmptyState")
        v.addWidget(self._empty_label, 1)

        self.refresh()

    # ---------------- 数据与刷新 ----------------
    def refresh(self):
        """重建插件卡片列表（面板可见时由 refresh_page 调用）"""
        loader = getattr(self._host, "plugin_loader", None)
        plugins = []
        errors = []
        store = []
        gate_on = True
        try:
            gate_on = bool(self._host._config.get("plugins_enabled", True))
        except (AttributeError, TypeError):
            gate_on = True
        if loader is not None and gate_on:
            try:
                plugins = list(loader.loaded_plugins())
            except Exception:                     # noqa: BLE001 - 展示层兜底
                plugins = []
            try:
                errors = list(loader.load_errors())
            except Exception:                     # noqa: BLE001 - 旧 loader 无此接口
                errors = []
            try:
                # 商店清单只读 manifest，不解压；旧 loader 没有该接口 → 空列表
                store = list(loader.scan_store())
            except Exception:                     # noqa: BLE001 - 旧 loader 兜底
                store = []

        # 总闸提示条
        self._gate_label.setVisible(not gate_on)

        # 安装目录 / 商店目录（绝对路径）——用户最常搞混「装在哪 / 包放哪」，
        # 直接写出来比「与程序同级」这类相对说法可核对得多。
        # 标签单行显示（长路径由 Qt 自带省略），完整路径挂在 tooltip 上。
        plugins_text = self._plugins_dir_text(loader)
        store_text = self._store_dir_text(loader)
        self._dir_label.setText("插件安装目录：%s" % plugins_text)
        self._dir_label.setToolTip(plugins_text)
        self._store_dir_label.setText("插件商店目录：%s" % store_text)
        self._store_dir_label.setToolTip(store_text)

        # 清空旧卡片（网格只装卡片本体；尾部 stretch 在外层 vbox，不会被动到）
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._card_widgets = []

        # 清空旧失败卡片
        while self._error_layout.count():
            item = self._error_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 失败区
        has_errors = bool(errors)
        self._error_box.setVisible(has_errors)
        if has_errors:
            self._error_label.setText(
                f"加载失败的插件（{len(errors)} 个）——见下方原因与修复建议")
            for fe in errors:
                self._error_layout.addWidget(self._make_error_card(fe))

        has_plugins = bool(plugins)
        self._empty_label.setVisible(
            not has_plugins and not has_errors and not self._has_store_pkgs(store))
        cnt = f"共 {len(plugins)} 个插件"
        if has_errors:
            cnt += f"，{len(errors)} 个加载失败"
        n_store = self._installable_count(store)
        if n_store:
            cnt += f"，商店可安装 {n_store} 个"
        self._count_label.setText(cnt)

        # 已安装卡片按行优先进网格（[c0 c1 / c2 c3 ...]，与列表顺序一致）
        for lp in plugins:
            self._grid_add_card(self._make_card(lp))

        # 商店清单本身不在这页渲染（2026-09-28 起在独立弹窗 PluginStoreDialog），
        # 这里只留计数与空态判据：让用户知道「有包可装」，去点工具栏的商店按钮。

    # ---------------- 双列网格 ----------------
    def _grid_add_card(self, card):
        """把卡片按行优先顺序放进网格（刷新路径；列数随容器宽度自适应）"""
        self._card_widgets.append(card)
        self._place_card(len(self._card_widgets) - 1)

    def _place_card(self, idx: int):
        row, col = divmod(idx, self._card_cols)
        self._cards_layout.addWidget(self._card_widgets[idx], row, col)

    def _reflow_cards(self, width: int):
        """容器宽度变化：两列放不下按钮行时回落单列（反之亦然），重排现有卡片。

        由 _CardsContainer.resizeEvent 驱动；列数没变就不动（resize 高频
        触发，重排一次要拆装全部条目）。
        """
        want = 2 if width >= GRID_TWO_COL_MIN_WIDTH else 1
        if want == self._card_cols:
            return
        self._card_cols = want
        while self._cards_layout.count():
            self._cards_layout.takeAt(0)
        for idx in range(len(self._card_widgets)):
            self._place_card(idx)
        self._cards_layout.setColumnStretch(1, 1 if want == 2 else 0)

    @staticmethod
    def _has_store_pkgs(store) -> bool:
        """商店里是否有任何包（含坏包）——空态判据用"""
        return bool(store)

    @staticmethod
    def _entry_visible(entry) -> bool:
        """商店条目是否该出现在列表里（有 id 能装的，或能给出错误原因的）"""
        return bool(getattr(entry, "plugin_id", "") or
                    getattr(entry, "error", ""))

    @staticmethod
    def _installable_count(store) -> int:
        """商店里当前**未安装**的可用包数量（计数标签用）"""
        try:
            return sum(1 for e in store
                       if getattr(e, "usable", False)
                       and not getattr(e, "installed", False))
        except Exception:                          # noqa: BLE001
            return 0

    def _on_rescan(self):
        """重新扫描插件目录（不必重启程序）"""
        loader = getattr(self._host, "plugin_loader", None)
        if loader is not None:
            rescan = getattr(loader, "rescan", None)
            try:
                if callable(rescan):
                    rescan()
                else:                              # 旧 loader：退化为刷新
                    loader.load_all()
            except Exception as e:                 # noqa: BLE001 - 扫描失败不崩 UI
                print(f"[插件中心] 重新扫描失败: {e}")
        # 插件增减会影响悬浮球右键菜单，通知宿主动作表重建
        self._notify_menu_rebuild()
        self.refresh()

    def _notify_menu_rebuild(self):
        """让悬浮球右键菜单反映最新的插件动作（能力缺失时静默跳过）。

        优先走宿主既有的菜单重建钩子；公共入口是 ``plugins_changed`` 信号——
        它原本就承载「插件动作集合变了，请重载并重建菜单」的语义
        （knowledge_ball 侧连着 ``_apply_plugins`` → ``refresh_plugin_menu``），
        复用它可以不新增任何跨层 API。
        """
        rebuild = getattr(self._host, "_rebuild_context_menu", None)
        if callable(rebuild):
            try:
                rebuild()
                return
            except Exception:                      # noqa: BLE001
                pass
        sig = getattr(self._host, "plugins_changed", None)
        emit = getattr(sig, "emit", None)
        if callable(emit):
            try:
                gate_on = True
                try:
                    gate_on = bool(self._host._config.get("plugins_enabled", True))
                except (AttributeError, TypeError):
                    gate_on = True
                emit(gate_on)
            except Exception:                      # noqa: BLE001
                pass

    # ---------------- 卡片构建 ----------------
    def _make_card(self, lp) -> QWidget:
        """单个插件卡片：标题行 + 描述 + 告警 + 动作清单 + 依赖 + 操作按钮"""
        card = QFrame()
        card.setObjectName("pluginCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(7)

        manifest = getattr(lp, "manifest", {}) or {}

        # ---- 标题行：名称 + 版本（弱化小字）+ 状态标签 + id ----
        head = QHBoxLayout()
        head.setSpacing(6)
        name = QLabel(lp.name)
        name.setObjectName("pluginCardTitle")
        head.addWidget(name)

        ver = QLabel(f"v{lp.version}")
        ver.setObjectName("pluginCardId")
        head.addWidget(ver)

        status = self._status_of(lp)
        if status is not None:
            tag = QLabel(status[0])
            tag.setObjectName(status[1])
            head.addWidget(tag)

        head.addStretch()
        pid = QLabel(lp.plugin_id)
        pid.setObjectName("pluginCardId")
        head.addWidget(pid)
        v.addLayout(head)

        # ---- 描述行：manifest.description > 类 docstring 首行 ----
        # 卡面最多 DESC_MAX 字（双列卡宽下约一行半，一眼扫完），
        # 超长截断加省略号，全文进悬停提示（2026-10-01 卡片降噪；
        # 用户反馈：介绍看一眼定位即可，详细用法点「查看使用说明」看 md）。
        desc = (manifest.get("description") or "").strip()
        if not desc:
            doc = getattr(type(lp.plugin), "__doc__", "") or ""
            desc = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if desc:
            desc_label = QLabel(clip_text(desc, DESC_MAX))
            desc_label.setObjectName("pluginCardDesc")
            desc_label.setWordWrap(True)
            if len(desc) > DESC_MAX:
                desc_label.setToolTip(desc)
            v.addWidget(desc_label)

        # ---- 使用说明摘要不上卡（2026-10-01 用户拍板）----
        # 描述与「📖 摘自 md 的摘要」两段都在介绍「这插件是干嘛的」，重复；
        # 详细用法本来就有点下方「查看使用说明」按钮（程序内渲染 md），
        # 卡面不再重复展示——需要详情时按一次按钮就能看到全文。

        # ---- 动作级告警（热键被占 / 未声明等）----
        for warn in list(getattr(lp, "warnings", []) or []):
            wl = QLabel(f"⚠ {warn}")
            wl.setObjectName("pluginErrorHint")
            wl.setWordWrap(True)
            v.addWidget(wl)

        # ---- 动作清单 ----
        actions = list(getattr(lp, "actions_raw", []) or [])
        for act in actions:
            row = QHBoxLayout()
            row.setSpacing(8)
            title = QLabel(f"·  {getattr(act, 'title', '') or act.id}")
            title.setObjectName("pluginActionTitle")
            row.addWidget(title)
            if getattr(act, "menu", False):
                menu_tag = QLabel("[右键菜单]")
                menu_tag.setObjectName("pluginActionTag")
                row.addWidget(menu_tag)
            hotkey = (getattr(act, "hotkey", None) or "").strip()
            if hotkey:
                hk = QLabel(f"⌨ {hotkey}")
                hk.setObjectName("pluginActionTag")
                row.addWidget(hk)
            row.addStretch()
            v.addLayout(row)
        if not actions:
            no_act = QLabel("（无注册动作）")
            no_act.setObjectName("pluginActionTag")   # 弱化灰，不与描述抢层级
            v.addWidget(no_act)

        # ---- 能力徽章行 + 卡片悬停详情（2026-10-01 卡片降噪）----
        # 能力：一排胶囊徽章（🌐 网络 / ✍ 写入 / 🛠 改删 / 🧠 AI），
        # 完整语义在徽章悬停提示里；依赖是开发者信息，不再占卡面行，
        # 连同被截断的描述全文一起挂进**卡片整体悬停提示**（悬停卡面
        # 空白处可见——QToolTip 沿父链找最近的有提示的控件）。
        requires = list(manifest.get("requires", []) or [])
        caps = list(manifest.get("capabilities", []) or [])
        card_tips = []
        if requires:
            card_tips.append("依赖: " + "、".join(requires))
        if desc and len(desc) > DESC_MAX:
            card_tips.append("简介: " + desc)
        if card_tips:
            card.setToolTip("\n\n".join(card_tips))
        if caps:
            cap_row = QHBoxLayout()
            cap_row.setSpacing(6)
            for c in caps:
                cap_row.addWidget(self._make_cap_badge(c))
            cap_row.addStretch()
            v.addLayout(cap_row)

        # ---- 操作按钮行（右对齐，与原视觉一致）----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()

        toggle_btn = self._make_toggle_btn(lp, actions)
        if toggle_btn is not None:
            bottom.addWidget(toggle_btn)

        open_btn = SmoothButton("📁 打开目录")
        open_btn.setObjectName("secondaryBtn")
        open_btn.clicked.connect(
            lambda _checked=False, p=lp.path: self._on_open_dir(p))
        bottom.addWidget(open_btn)

        readme = find_usage_file(lp.path)
        if readme:
            readme_btn = SmoothButton("📄 查看使用说明")
            readme_btn.setObjectName("secondaryBtn")
            readme_btn.clicked.connect(
                lambda _checked=False, p=readme: self._on_open_file(p))
            bottom.addWidget(readme_btn)

        uninstall_btn = IconButton("trash", text="卸载", icon_size=14,
                                   object_name="dangerBtn",
                                   off_color="danger", hover_color="#FFFFFF")
        uninstall_btn.setToolTip("删除插件目录并摘掉它的动作 / 热键 / 菜单项；"
                                 "插件私有数据（float_data/plugins/）会保留")
        uninstall_btn.clicked.connect(
            lambda _checked=False, p=lp.plugin_id,
            n=getattr(lp, "name", "") or lp.plugin_id,
            d=lp.path: self._on_uninstall(p, n, d))
        bottom.addWidget(uninstall_btn)

        v.addLayout(bottom)
        return card

    def _make_cap_badge(self, cap: str) -> QLabel:
        """能力胶囊徽章：短文案上卡面，完整语义进悬停提示（三卡共用）"""
        lab = QLabel(CAP_BADGES.get(cap, CAP_LABELS.get(cap, cap)))
        lab.setObjectName("pluginCapBadge")
        lab.setToolTip(CAP_TIPS.get(cap, ""))
        return lab

    def _make_store_card(self, entry) -> QWidget:
        """商店里一个可安装包的卡片：名称 + 版本 + 状态 + 描述 + 安装按钮。

        三种形态：
          - 未安装（可用）→ 主按钮「⬇ 安装」
          - 已安装       → 按钮禁用，显示「已安装」；同时给「打开目录」
          - 包不合法     → 标红展示 error + 提示，不给安装按钮
        """
        card = QFrame()
        card.setObjectName("pluginStoreCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        usable = bool(getattr(entry, "usable", False))
        installed = bool(getattr(entry, "installed", False))
        plugin_id = getattr(entry, "plugin_id", "") or ""

        # ---- 标题行：名称 + 版本（弱化小字）+ 状态标签 + id ----
        head = QHBoxLayout()
        head.setSpacing(6)
        title_text = getattr(entry, "name", "") or getattr(entry, "filename", "")
        version = getattr(entry, "version", "") or ""
        name = QLabel(title_text)
        name.setObjectName("pluginStoreTitle")
        head.addWidget(name)
        if version:
            ver = QLabel(f"v{version}")
            ver.setObjectName("pluginCardId")
            head.addWidget(ver)

        if not usable:
            tag = QLabel("包不合法")
            tag.setObjectName("pluginStatusWarn")
            head.addWidget(tag)
        elif installed:
            tag = QLabel("已安装")
            tag.setObjectName("pluginStatusOn")
            head.addWidget(tag)
        else:
            tag = QLabel("未安装")
            tag.setObjectName("pluginStoreBadge")
            head.addWidget(tag)

        head.addStretch()
        pid = QLabel(plugin_id or getattr(entry, "filename", ""))
        pid.setObjectName("pluginCardId")
        head.addWidget(pid)
        v.addLayout(head)

        # ---- 描述 ----
        desc = (getattr(entry, "description", "") or "").strip()
        if desc:
            dl = QLabel(desc)
            dl.setObjectName("pluginCardDesc")
            dl.setWordWrap(True)
            v.addWidget(dl)

        # ---- 非法包的报错 ----
        error = (getattr(entry, "error", "") or "").strip()
        if error:
            el = QLabel(f"⚠ {error}")
            el.setObjectName("pluginErrorReason")
            el.setWordWrap(True)
            v.addWidget(el)

        # ---- 能力徽章行 + 元信息行：徽章化能力，热键/体积/包名保持文字 ----
        caps = list((getattr(entry, "manifest", None) or {}).get(
            "capabilities", []) or [])
        if caps:
            cap_row = QHBoxLayout()
            cap_row.setSpacing(6)
            for c in caps:
                cap_row.addWidget(self._make_cap_badge(c))
            cap_row.addStretch()
            v.addLayout(cap_row)
        fname = getattr(entry, "filename", "")
        if fname:
            meta = QLabel(f"包: {fname}")
            meta.setObjectName("pluginCardId")
            meta.setWordWrap(True)
            v.addWidget(meta)

        # ---- 底部操作行：按钮靠右，独占横向空间（避免文字挤压按钮）----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()

        install_btn = SmoothButton("⬇ 安装")
        install_btn.setObjectName("primaryBtn")
        install_btn.setToolTip(
            "把该插件包解压到插件安装目录并加载；源包保留在商店目录，"
            "卸载后仍可再装")
        if not usable:
            install_btn.setEnabled(False)
            install_btn.setText("⊘ 无法安装")
            install_btn.setToolTip("插件包不合法，先按下方提示修正后再试")
        elif installed:
            install_btn.setEnabled(False)
            install_btn.setText("✓ 已安装")
            install_btn.setToolTip("该插件已装在插件安装目录；如需重装请先卸载")
        else:
            install_btn.clicked.connect(
                lambda _checked=False, p=plugin_id,
                n=title_text: self._on_install(p, n))
        bottom.addWidget(install_btn)

        open_btn = SmoothButton("📁 打开目录")
        open_btn.setObjectName("secondaryBtn")
        open_btn.clicked.connect(
            lambda _checked=False, p=getattr(entry, "path", ""):
            self._on_open_dir(os.path.dirname(p) if p else ""))
        bottom.addWidget(open_btn)

        v.addLayout(bottom)
        return card

    def _make_error_card(self, fe) -> QWidget:
        from src.plugin_loader import stage_label
        card = QFrame()
        card.setObjectName("pluginErrorCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        # 标题行：插件名 + 失败阶段 + 目录名
        head = QHBoxLayout()
        title = QLabel(getattr(fe, "name", "") or getattr(fe, "folder", ""))
        title.setObjectName("pluginErrorTitle")
        head.addWidget(title)
        stage_tag = QLabel(stage_label(getattr(fe, "stage", "")))
        stage_tag.setObjectName("pluginStatusWarn")
        head.addWidget(stage_tag)
        head.addStretch()
        folder = getattr(fe, "folder", "")
        if folder and folder != (getattr(fe, "name", "") or ""):
            folder_tag = QLabel(folder)
            folder_tag.setObjectName("pluginCardId")
            head.addWidget(folder_tag)
        v.addLayout(head)

        # 原因
        reason = QLabel(getattr(fe, "reason", "") or "未知原因")
        reason.setObjectName("pluginErrorReason")
        reason.setWordWrap(True)
        v.addWidget(reason)

        # 修复建议
        hint = (getattr(fe, "hint", "") or "").strip()
        if hint:
            hint_label = QLabel(f"💡 {hint}")
            hint_label.setObjectName("pluginErrorHint")
            hint_label.setWordWrap(True)
            v.addWidget(hint_label)

        # 操作行
        path = getattr(fe, "path", "")
        if path:
            bottom = QHBoxLayout()
            bottom.addStretch()
            open_btn = SmoothButton("📁 打开目录")
            open_btn.setObjectName("secondaryBtn")
            open_btn.clicked.connect(
                lambda _checked=False, p=path: self._on_open_dir(p))
            bottom.addWidget(open_btn)
            v.addLayout(bottom)
        return card

    # ---------------- 启停开关 ----------------
    @staticmethod
    def _status_of(lp):
        """返回 (文本, objectName) 状态标签；无法判定时返回 None。

        优先级：用户停用 > 登记失败 > 部分生效 > 已启用。
        「停用」是运行时开关（ActionRegistry.set_enabled），与登记结果无关，
        所以必须最先判——否则停用后的卡片仍显示「已启用」，误导用户。
        """
        actions = list(getattr(lp, "actions_raw", []) or [])
        if not actions:
            return ("未提供动作", "pluginStatusOff")
        # 停用态：全部动作都被 set_enabled(False) 关掉了
        if all(hasattr(a, "enabled") and not a.enabled() for a in actions):
            return ("已停用", "pluginStatusOff")
        ok = getattr(lp, "actions_ok", None)
        if ok is None:
            return None
        if ok == 0:
            return ("未生效", "pluginStatusWarn")
        if ok < len(actions):
            return (f"部分生效 {ok}/{len(actions)}", "pluginStatusWarn")
        return ("已启用", "pluginStatusOn")

    def _registry(self):
        """取动作注册表：优先 loader.registry（少一处跨层耦合），
        再退回宿主直挂的几种常见属性名；都拿不到返回 None"""
        loader = getattr(self._host, "plugin_loader", None)
        reg = getattr(loader, "registry", None) if loader is not None else None
        if reg is not None and hasattr(reg, "set_enabled"):
            return reg
        for attr in ("action_registry", "plugin_registry", "_action_registry"):
            reg = getattr(self._host, attr, None)
            if reg is not None and hasattr(reg, "set_enabled"):
                return reg
        return None

    def _make_toggle_btn(self, lp, actions):
        """构造「停用 / 启用」按钮；注册表不可用或插件无动作时返回 None。

        开关状态取自插件第一个动作的 enabled()（同一插件的动作同开同关）。
        点击后逐个调用 ActionRegistry.set_enabled、把状态写进配置并通知宿主动作表重建。
        """
        if not actions:
            return None
        registry = self._registry()
        if registry is None:
            return None
        first = actions[0]
        aid = getattr(first, "id", "")
        if not aid or not hasattr(first, "enabled"):
            return None
        on = bool(first.enabled())
        btn = SmoothButton("⏸ 停用" if on else "▶ 启用")
        btn.setObjectName("secondaryBtn")
        btn.setToolTip("停用后不挂右键菜单、不绑热键（不卸载插件模块），"
                       "状态会记住，重启后依然生效")
        btn.clicked.connect(
            lambda _checked=False, s=self, aa=actions, pid=getattr(lp, "plugin_id", ""),
            now=on: s._toggle_plugin(aa, not now, plugin_id=pid))
        return btn

    def _toggle_plugin(self, actions, on: bool, plugin_id: str = ""):
        """把该插件的全部动作统一切换到 on 状态，并把结果写进配置。

        配置持久化（``plugins_disabled``）让「停用」跨重启生效——
        此前只改内存，重启后所有插件都恢复启用，用户预期落空。

        页面插件同步（2026-09-27）：停用 → 摘掉主窗口里的导航键与页面
        （unregister_plugin_page），启用 → 经 create_page 重建
        （rebuild_plugin_page）。页面是插件的一部分，开关必须连带它。
        """
        registry = self._registry()
        if registry is None:
            return
        for act in actions:
            aid = getattr(act, "id", "")
            if aid:
                try:
                    registry.set_enabled(aid, on)
                except Exception:                  # noqa: BLE001 - 单个失败不阻塞
                    pass
        self._persist_disabled(plugin_id, on)
        if plugin_id:
            host = self._host
            if on:
                rebuild = getattr(host, "rebuild_plugin_page", None)
                if callable(rebuild):
                    rebuild(plugin_id)
            else:
                unreg = getattr(host, "unregister_plugin_page", None)
                if callable(unreg):
                    unreg(f"plugin:{plugin_id}")
        self._notify_menu_rebuild()
        self.refresh()

    def _persist_disabled(self, plugin_id: str, on: bool):
        """把单个插件的启停状态写进配置（复用宿主持有的 ConfigManager，不自造写盘路径）。

        宿主的惯例是 ``self._config.set(key, val)`` + ``self._config.save()``
        （见 main_window 的 last_page_index / theme 等写法），这里照做。

        拿不到插件 id / 配置不可写 → 静默跳过（内存态已改，只是不落盘），
        绝不因为写配置失败而阻断 UI 操作。
        """
        if not plugin_id:
            return
        cfg = getattr(self._host, "_config", None)
        if cfg is None or not hasattr(cfg, "set"):
            print("[插件中心] 宿主没有配置管理器，启停状态未能持久化")
            return
        try:
            raw = cfg.get("plugins_disabled", None)
            cur = [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
        except Exception:                          # noqa: BLE001
            cur = []
        if on:
            new = [p for p in cur if p != plugin_id]
        else:
            new = cur if plugin_id in cur else cur + [plugin_id]
        if new == cur:
            return
        try:
            cfg.set("plugins_disabled", new)
            save = getattr(cfg, "save", None)
            if callable(save):
                save()
        except Exception as exc:                   # noqa: BLE001
            print(f"[插件中心] 保存插件启停状态失败: {exc}")

    # ---------------- 动作 ----------------
    @staticmethod
    def _plugins_dir_text(loader) -> str:
        """插件安装目录的显示文本（拿不到时给出兜底提示，绝不抛异常）。

        默认目录 = ``<base_dir>/plugins``：源码运行即 **项目根/plugins**
        （与 ``float_data/plugins`` 不是一回事，后者是插件私有数据目录）。
        """
        if loader is None:
            return "（插件功能未启用）"
        try:
            return loader.plugins_dir
        except Exception:                          # noqa: BLE001 - 旧 loader/未初始化
            return "（暂不可用）"

    @staticmethod
    def _store_dir_text(loader) -> str:
        """插件商店目录的显示文本（拿不到时兜底，绝不抛异常）"""
        if loader is None:
            return "（插件功能未启用）"
        try:
            return loader.store_dir
        except Exception:                          # noqa: BLE001 - 旧 loader 无此属性
            return "（暂不可用）"

    def _on_open_plugins_dir(self):
        """在资源管理器中打开插件安装目录（不存在则先创建）

        注意：loader.plugins_dir 是 @property，不能加括号调用。
        """
        loader = getattr(self._host, "plugin_loader", None)
        path = ""
        if loader is not None:
            path = loader.plugins_dir
        if not path:
            return
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)                    # noqa: S606 - 仅资源管理器
        except OSError as e:
            print(f"[插件中心] 打开插件目录失败: {e}")

    def _on_open_store_dialog(self):
        """打开插件商店独立弹窗（懒创建；同一实例反复 exec，状态实时刷新）。

        每次打开前都 reload：商店目录可能在两次打开之间被放入新包。
        """
        if self._store_dialog is None:
            self._store_dialog = PluginStoreDialog(self)
        self._store_dialog.reload()
        self._store_dialog.exec()

    def _on_open_store_dir(self):
        """在资源管理器中打开插件商店目录（不存在则先创建）"""
        loader = getattr(self._host, "plugin_loader", None)
        path = ""
        if loader is not None:
            try:
                path = loader.store_dir
            except Exception:                      # noqa: BLE001 - 旧 loader
                path = ""
        if not path:
            return
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)                    # noqa: S606
        except OSError as e:
            print(f"[插件中心] 打开插件商店目录失败: {e}")

    def _on_open_dir(self, path: str):
        """打开单个插件目录"""
        try:
            os.startfile(path)                    # noqa: S606
        except OSError as e:
            print(f"[插件中心] 打开插件目录失败: {e}")

    def _on_open_file(self, path: str):
        """程序内查看使用说明（Markdown 渲染），不依赖系统文件关联。

        QTextBrowser.setMarkdown 是 Qt6 内置能力，零额外依赖；
        「用系统程序打开」按钮保留给想用外部编辑器的用户（startfile
        失败时仅提示，不崩溃——用户机器可能没有任何 .md 关联）。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            # 文件读不了 → 尝试交给系统关联程序（内部自带失败提示框）
            self._on_startfile(path)
            return
        _build_usage_viewer(self._host, path, text).exec()

    def _on_startfile(self, path: str):
        """交给系统默认程序打开（委托模块级 _startfile_warn，避免两份逻辑）"""
        _startfile_warn(self, path)

    # ---------------- 安装 / 卸载 ----------------
    def _on_install(self, plugin_id: str, name: str):
        """从商店安装插件：委托 loader.install_from_store()，成功即重新扫描。

        与「卸载」对称：卸载把包留在商店，安装把包从商店解出来。
        解压逻辑**全部在 loader 层**，UI 只负责触发与展示结果
        （UI 层不碰 zip，这条约束与「不碰插件目录」同级）。
        """
        loader = getattr(self._host, "plugin_loader", None)
        if loader is None or not hasattr(loader, "install_from_store"):
            QMessageBox.warning(self, "无法安装",
                                "当前插件加载器不支持从商店安装，"
                                "请手动把插件包解压到插件安装目录。")
            return
        try:
            ok, msg = loader.install_from_store(plugin_id)
        except Exception as exc:                   # noqa: BLE001 - 安装失败不崩 UI
            ok, msg = False, f"安装过程出错：{exc}"

        if ok:
            # 新插件会带来新动作 → 重建右键菜单 + 重新扫描 + 刷新卡片
            self._notify_menu_rebuild()
            self._on_rescan()
            # 商店弹窗若开着，同步刷新它的卡片（安装按钮 →「✓ 已安装」）
            if self._store_dialog is not None:
                self._store_dialog.reload()
            # 反馈框挂**当前活动窗口**：挂 self（藏在主窗口 stack 里）时，
            # ApplicationModal 消息框可能被模态弹窗盖住 → 用户点不到「确定」
            # → 全应用看似锁死（2026-09-28 用户实测）
            QMessageBox.information(self._active_dialog_or_self(), "已安装",
                                    f"{msg}\n\n插件已加载，可直接使用。")
        else:
            QMessageBox.warning(self._active_dialog_or_self(), "安装失败", msg)

    def _active_dialog_or_self(self) -> QWidget:
        """消息框应该挂的 parent：商店弹窗开着就挂弹窗（保证 z 序可控），
        否则挂面板自身（页面本体操作时行为与旧版一致）"""
        dlg = getattr(self, "_store_dialog", None)
        if dlg is not None and dlg.isVisible():
            return dlg
        return self

    def _on_uninstall(self, plugin_id: str, name: str, path: str):
        """卸载插件：二次确认（列出将被删除的绝对路径）→ 委托 loader.uninstall()

        涉及删除，因此确认框必须说清三件事：
          1. 具体删哪个目录（绝对路径，用户能核对）
          2. 会连带失效什么（动作 / 热键 / 右键菜单 / 插件页面）
          3. **什么不会被删**（插件私有数据 + 商店里的源包），
             避免用户误以为数据也清了、或以为包没了装不回来
        """
        loader = getattr(self._host, "plugin_loader", None)
        if loader is None or not hasattr(loader, "uninstall"):
            QMessageBox.warning(self, "无法卸载",
                                "当前插件加载器不支持卸载，请手动删除插件目录。")
            return

        ret = QMessageBox.question(
            self, "卸载插件",
            f"确定要卸载插件「{name}」吗？\n\n"
            f"将删除目录：\n{path}\n\n"
            f"该插件的动作、热键、右键菜单项与插件页面会一并失效，"
            f"重启后不再加载。\n\n"
            f"注意：以下内容**不会**被删除——\n"
            f"  · 插件私有数据：float_data/plugins/{plugin_id}/\n"
            f"  · 商店里的源包：plugin_store/{plugin_id}.fpplug（若存在）\n"
            f"卸载后商店卡片会回到「未安装」，可随时重装。\n\n"
            f"此操作不可撤销，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            ok, msg = loader.uninstall(plugin_id)
        except Exception as exc:                   # noqa: BLE001 - 卸载失败不崩 UI
            ok, msg = False, f"卸载过程出错：{exc}"

        if ok:
            # 卸载会改变动作集合 → 重建右键菜单 + 刷新卡片
            self._notify_menu_rebuild()
            self.refresh()
            # parent 用当前活动窗口（同 _on_install：防消息框被模态弹窗盖住）
            QMessageBox.information(self._active_dialog_or_self(), "已卸载", msg)
        else:
            QMessageBox.warning(self._active_dialog_or_self(), "卸载失败", msg)


class PluginStoreDialog(GlassDialog):
    """插件商店独立弹窗（2026-09-28 起，取代内嵌在插件中心页里的商店区）。

    为什么拆出来：商店卡片与已安装卡片同页渲染时，插件中心页被拉得过长、
    视觉拥挤（用户反馈「都显示在这不太美观」）。拆成独立窗口后：
      - 页面本体只看「已装了什么」；
      - 商店窗口只看「还能装什么」，安装/已安装/坏包三态照旧；
      - 安装动作仍委托 panel._on_install（loader 层解压），本窗口不碰 zip。

    主题：GlassDialog 直接取宿主主窗口的 QSS，light/dark 自动跟随；
    卡片构建完全复用 PluginsPanel._make_store_card，三态行为与旧内嵌版一致。
    """

    def __init__(self, panel):
        self._panel = panel
        super().__init__(host=panel._host, title="🏪 插件商店",
                         subtitle="浏览并安装插件包（安装 = 解压到插件目录）",
                         size=(720, 560))

        body = self.body_layout

        # ---- 商店目录绝对路径（用户最常问「包放哪」）----
        self._dir_label = QLabel()
        self._dir_label.setObjectName("pluginDirLabel")
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self._dir_label)

        # ---- 已安装数量提示（默认隐藏）----
        # 已安装的包**不重复列出**（页面本体已有同款插件卡片，用户反感重复），
        # 只用一行交代数量；卸载后包回落「未安装」，会重新出现在列表里。
        self._installed_hint = QLabel()
        self._installed_hint.setObjectName("pluginDirLabel")
        self._installed_hint.setWordWrap(True)
        self._installed_hint.setVisible(False)
        body.addWidget(self._installed_hint)

        # ---- 在线市场（2026-09-30 起，用户主动点击才联网）----
        self._online_btn = SmoothButton("🌐 检查在线市场")
        self._online_btn.setObjectName("secondaryBtn")
        self._online_btn.setToolTip(
            "联网拉取官方插件市场索引（GitHub API）。\n"
            "只在点击这一刻发请求；不点不联网，离线时本地安装不受影响")
        self._online_btn.clicked.connect(self._on_check_online)
        self._online_status = QLabel()
        self._online_status.setObjectName("pluginCardId")
        self._online_status.setWordWrap(True)
        online_row = QHBoxLayout()
        online_row.addWidget(self._online_btn)
        online_row.addWidget(self._online_status, 1)
        body.addLayout(online_row)
        # 市场状态：_market_items=待安装条目 / _market_assets={文件名: asset id}
        # / _market_busy=任一网络步骤进行中（防重复点击）
        self._market_items = []
        self._market_assets = {}
        self._market_busy = False
        from src.logger import get_logger
        _log = get_logger()
        self._market_getter = make_async_getter(_log)
        self._market_bytes_getter = make_async_bytes_getter(_log)

        # ---- 滚动区：商店卡片列表（空态提示也在其中）----
        scroll = QScrollArea()
        scroll.setObjectName("storeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("storeContainer")
        self._list_layout = QVBoxLayout(container)
        self._list_layout.setContentsMargins(0, 0, 8, 0)
        self._list_layout.setSpacing(10)
        # 固定结构 [空态label(可隐藏), 卡片..., stretch]：
        # empty 先加、stretch 最后，卡片 reload 时插到 stretch 之前
        self._empty_label = QLabel()
        self._empty_label.setObjectName("pluginEmptyHint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("padding: 40px;")
        self._empty_label.setWordWrap(True)
        self._list_layout.addWidget(self._empty_label)
        self._list_layout.addStretch()
        scroll.setWidget(container)
        body.addWidget(scroll, 1)

        self.add_footer([
            ("📁 打开商店目录", "secondaryBtn",
             lambda _checked=False: self._panel._on_open_store_dir()),
            ("刷新", "secondaryBtn", self.reload, "refresh"),
            ("关闭", "primaryBtn", self.accept),
        ])
        self.reload()

    # ---------------- 数据 ----------------
    def reload(self):
        """重扫商店目录并重建卡片（安装/卸载后由 panel 回调，也可手动触发）。

        卡片必须 insertWidget 到 stretch 之前——addWidget 会排到 stretch
        后面（QVBoxLayout 的 alignment 陷阱，见气泡卡片同款教训）。
        """
        panel = self._panel
        loader = getattr(panel._host, "plugin_loader", None)

        # 商店目录路径（label 单行 + tooltip 全文）
        store_dir = ""
        try:
            if loader is not None:
                store_dir = loader.store_dir
        except Exception:                          # noqa: BLE001 - 旧 loader 兜底
            store_dir = ""
        if store_dir:
            self._dir_label.setText("🏪 商店目录：%s" % store_dir)
            self._dir_label.setToolTip(store_dir)
            self._dir_label.setVisible(True)
        else:
            self._dir_label.setVisible(False)

        # 清空旧卡片（按标记逆序删，空态 label 与末尾 stretch 保留）。
        # 先 hide 再 deleteLater：deleteLater 要等事件循环才真正删除，
        # 不 hide 的话旧卡片会残影叠加在新卡片上（离屏截图实测）。
        for i in reversed(range(self._list_layout.count())):
            item = self._list_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None and w is not self._empty_label:
                self._list_layout.takeAt(i)
                w.hide()
                w.deleteLater()

        # 扫描商店（只读 manifest，不解压；旧 loader 无此接口 → 空列表）
        entries = []
        try:
            store = list(loader.scan_store()) if loader is not None else []
            entries = [e for e in store if panel._entry_visible(e)]
        except Exception:                          # noqa: BLE001 - 展示层兜底
            entries = []

        # 已安装的包**不重复列出**：页面本体的已装卡片就是它的展示位，
        # 这里只列「未安装」与「坏包」（坏包要给出错原因，用户才能修）。
        # 已装数量压缩成一行提示；卸载后包回落「未安装」，重新出现。
        pending = [e for e in entries if not getattr(e, "installed", False)]
        n_installed = len(entries) - len(pending)

        if n_installed:
            self._installed_hint.setText(
                f"✓ 另有 {n_installed} 个插件包已安装——"
                f"卸载插件后可回到此处重装")
            self._installed_hint.setVisible(True)
        else:
            self._installed_hint.setVisible(False)

        self._empty_label.setVisible(not pending)
        if not pending:
            self._empty_label.setText(
                "商店目录里还没有可安装的插件包\n\n"
                "把 .fpplug 插件包（或含 manifest.json 的文件夹）\n"
                "放进上方商店目录，回到本窗口点「🔄 刷新」即可看到"
                if not entries else
                "商店里的插件包都已安装\n\n"
                "卸载插件后，它的源包会回到这里，可随时重装")
        for e in pending:
            self._list_layout.insertWidget(
                self._list_layout.count() - 1, panel._make_store_card(e))

    # ---------------- 在线市场 ----------------
    def _set_online_status(self, text: str):
        self._online_status.setText(text)

    def _market_set_busy(self, busy: bool):
        self._market_busy = busy
        self._online_btn.setEnabled(not busy)

    def _on_check_online(self):
        """「🌐 检查在线市场」：拉索引 → 拉最新 Release 解析附件 id → 出卡片。

        两段式的原因：索引只存**文件名**（asset id 每次发版都变），
        下载地址要在运行时从 /releases/latest 里按文件名解析。
        """
        if self._market_busy:
            return
        self._market_set_busy(True)
        self._market_items = []
        self._market_assets = {}
        self._set_online_status("正在获取在线市场索引…")
        started = self._market_getter(
            plugin_market.INDEX_API_URL, headers=plugin_market.index_headers(),
            timeout=plugin_market.INDEX_TIMEOUT_S,
            on_done=self._on_index_loaded)
        if not started:
            self._market_set_busy(False)
            self._set_online_status("无法发起网络请求")

    def _on_index_loaded(self, res: dict):
        if not res.get("ok"):
            self._market_set_busy(False)
            status = res.get("status", 0)
            if status == 403:
                self._set_online_status(
                    "在线市场获取失败：GitHub API 限流（每小时 60 次），"
                    "稍后再试。离线不影响本地安装")
            elif status:
                self._set_online_status(f"在线市场获取失败：HTTP {status}")
            elif "超时" in (res.get("error") or "") or "timed out" in (
                    res.get("error") or ""):
                self._set_online_status(
                    "在线市场获取超时，可重试。离线不影响本地安装")
            else:
                self._set_online_status(
                    "无法连接 GitHub，离线不影响任何本地功能")
            return
        items, problems = plugin_market.parse_index(res.get("body", ""))
        if not items:
            self._market_set_busy(False)
            self._set_online_status(
                "在线市场响应格式异常：" + "；".join(problems[:2]))
            return
        # 已装版本表：loader 扫商店（installed=True 的才进已装清单）
        installed_versions = {}
        loader = getattr(self._panel._host, "plugin_loader", None)
        try:
            for e in (loader.scan_store() if loader is not None else []):
                if getattr(e, "usable", False) and getattr(e, "installed", False):
                    installed_versions[e.plugin_id] = getattr(e, "version", "")
        except Exception:                      # noqa: BLE001 - 展示层兜底
            pass
        pending, updatable = plugin_market.installed_state(
            items, installed_versions)
        self._market_items = pending
        note = f"在线市场共 {len(items)} 个插件：{len(pending)} 个未安装"
        if updatable:
            names = "、".join(i["name"] for i in updatable)
            note += (f"；{len(updatable)} 个有新版本（{names}）——"
                     f"先卸载旧版，再从这里安装新版")
        for p in problems[:2]:
            note += f"\n⚠ {p}"
        self._set_online_status(note)
        if not pending:
            self._market_set_busy(False)
            return
        # 第二段：最新 Release 的 assets（按文件名解析 asset id）
        self._set_online_status(note + "\n正在解析下载地址…")
        started = self._market_getter(
            RELEASES_API_URL, headers=check_headers(),
            timeout=plugin_market.RELEASE_TIMEOUT_S,
            on_done=self._on_release_loaded)
        if not started:
            self._market_set_busy(False)
            self._set_online_status("无法发起网络请求")

    def _on_release_loaded(self, res: dict):
        self._market_set_busy(False)
        body = res.get("body", "") if res.get("ok") else ""
        for it in self._market_items:
            aid = plugin_market.find_asset_id(body, it["file"])
            if aid:
                self._market_assets[it["file"]] = aid
        missing = [i["name"] for i in self._market_items
                   if i["file"] not in self._market_assets]
        if not self._market_assets:
            self._set_online_status(
                "在线列表已获取，但解析下载地址失败"
                "（最新 Release 还没有插件附件），请稍后再试")
            return
        note = (f"在线市场 {len(self._market_assets)} 个插件可安装"
                + (f"；{len(missing)} 个暂缺附件" if missing else ""))
        self._set_online_status(note)
        # 出卡片：插在本地卡片**之前**（紧跟空态 label），末尾 stretch 前的
        # 位置由本地卡片继续占用——reload() 会连在线卡一起清掉，语义简单
        insert_at = 1      # [empty_label, 在线卡..., 本地卡..., stretch]
        for it in reversed(self._market_items):
            if it["file"] in self._market_assets:
                self._list_layout.insertWidget(
                    insert_at, self._make_online_card(it))
        if not self._market_items:
            self._empty_label.setVisible(False)

    def _make_online_card(self, it: dict) -> QWidget:
        """在线市场卡片：与本地商店卡同款骨架，按钮是「⬇ 下载安装」。

        刻意不复用 _make_store_card：那张卡的安装按钮走「包已在商店目录」
        的前提（_on_install → install_from_store），在线包还没落地，路径不同。
        """
        card = QFrame()
        card.setObjectName("pluginStoreCard")
        card._is_online_card = True       # 测试/刷新遍历用（区别于本地商店卡）
        card._market_item = dict(it)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        name = QLabel(it["name"])
        name.setObjectName("pluginStoreTitle")
        head.addWidget(name)
        ver = QLabel(f"v{it['version']}")
        ver.setObjectName("pluginCardId")
        head.addWidget(ver)
        tag = QLabel("远程")
        tag.setObjectName("pluginStoreBadge")
        head.addWidget(tag)
        head.addStretch()
        pid = QLabel(it["id"])
        pid.setObjectName("pluginCardId")
        head.addWidget(pid)
        v.addLayout(head)

        if it["description"]:
            dl = QLabel(clip_text(it["description"], DESC_MAX))
            dl.setObjectName("pluginCardDesc")
            dl.setWordWrap(True)
            if len(it["description"].strip()) > DESC_MAX:
                dl.setToolTip(it["description"])
            v.addWidget(dl)

        # 能力徽章行（与已装卡片/商店卡同款）
        if it["capabilities"]:
            cap_row = QHBoxLayout()
            cap_row.setSpacing(6)
            for c in it["capabilities"]:
                cap_row.addWidget(self._make_cap_badge(c))
            cap_row.addStretch()
            v.addLayout(cap_row)

        meta_parts = []
        if it["hotkeys"]:
            meta_parts.append("热键: " + " ".join(it["hotkeys"]))
        meta_parts.append(f"{it['size'] / 1024:.1f} KB")
        meta = QLabel("　".join(meta_parts))
        meta.setObjectName("pluginCardId")
        meta.setWordWrap(True)
        v.addWidget(meta)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()
        btn = SmoothButton("⬇ 下载安装")
        btn.setObjectName("primaryBtn")
        btn.setToolTip(
            "从 GitHub Releases 下载插件包（sha256 校验后放进商店目录）"
            "并安装；已装插件不会被覆盖")
        btn.clicked.connect(
            lambda _checked=False, item=dict(it), b=btn:
            self._download_and_install(item, b))
        bottom.addWidget(btn)
        v.addLayout(bottom)
        return card

    def _download_and_install(self, it: dict, btn: QPushButton):
        """下载 .fpplug（octet-stream）→ sha256 校验 → 落商店 → 走既有安装。

        安装委托 panel._on_install：它内部完成 rescan / 菜单重建 /
        商店弹窗刷新 / 结果反馈，本方法只负责把包「送到商店目录」。
        """
        if self._market_busy:
            return
        asset_id = self._market_assets.get(it["file"], 0)
        if not asset_id:
            self._set_online_status(f"{it['name']}：下载地址尚未解析，请重新检查在线市场")
            return
        self._market_set_busy(True)
        btn.setEnabled(False)
        btn.setText("⬇ 下载中…")
        self._set_online_status(f"正在下载 {it['name']}（{it['size'] / 1024:.1f} KB）…")
        started = self._market_bytes_getter(
            plugin_market.asset_download_url(asset_id),
            headers=plugin_market.download_headers(),
            timeout=plugin_market.DOWNLOAD_TIMEOUT_S,
            on_done=lambda res, item=it: self._on_downloaded(item, res))
        if not started:
            self._market_set_busy(False)
            btn.setEnabled(True)
            btn.setText("⬇ 下载安装")

    def _on_downloaded(self, it: dict, res: dict):
        self._market_set_busy(False)
        if not res.get("ok"):
            status = res.get("status", 0)
            why = (f"HTTP {status}" if status
                   else "网络失败（离线或 GitHub 不可达）")
            self._set_online_status(f"{it['name']} 下载失败：{why}")
            self.reload()          # 恢复按钮文字（卡片已重建）
            return
        data = res.get("data") or b""
        if not plugin_market.sha256_ok(data, it["sha256"]):
            self._set_online_status(
                f"{it['name']} 下载校验失败（sha256 不符），已丢弃，请稍后重试")
            self.reload()
            return
        loader = getattr(self._panel._host, "plugin_loader", None)
        store_dir = ""
        try:
            store_dir = loader.store_dir if loader is not None else ""
        except Exception:                  # noqa: BLE001
            store_dir = ""
        ok, where = plugin_market.save_to_store(
            data, store_dir, it["file"])
        if not ok:
            self._set_online_status(f"{it['name']} 保存失败：{where}")
            self.reload()
            return
        # 包已进商店目录 → 走既有安装（rescan + 商店刷新 + 结果弹窗全覆盖）
        self._set_online_status(f"{it['name']} 已下载并开始安装…")
        self._panel._on_install(it["id"], it["name"])
        self._set_online_status(f"✓ {it['name']} 已从在线市场安装")
