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
     「插件商店」按钮弹出独立窗口（GlassDialog，与主窗口同主题），
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
import re

from src.plugin_loader import PLUGIN_PACKAGE_EXT

from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QTextBrowser,
    QSizePolicy, QApplication, QLineEdit, QMenu, QWidgetAction,
    QComboBox,
)

# 弹窗基类：PluginStoreDialog 在模块加载期就需要它作基类，
# 因此必须是模块级 import（_build_usage_viewer 里的局部 import 只是历史写法）。
from src.glass_dialog import GlassDialog
from src.glass_message_box import GlassMessageBox

from src import plugin_market
from src import plugin_settings
from src.controls import (SmoothButton, EmptyState, IconButton, PageTitle,
                          IconLabel, Stepper, ToggleSwitch)
from src.theme import DEFAULT_THEME, get_colors
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

# 能力徽章图标（自绘，UI 重构 05）：network→nav / write→edit / manage→settings / ai→ai
CAP_ICONS = {
    "network": "nav", "write": "edit",
    "manage": "settings", "ai": "ai",
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
# 2026-10-02 卡片重设计 48 → 64：卡面内容列左侧让出了 28px 图标槽，
# 48 字在双列卡宽下只够一行半、像「被切断」；64 字正好占满两行收口干净）
DESC_MAX = 64
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


class _FlowLayout(QLayout):
    """横向排列、放不下自动换行的流式布局（能力标签 / 动作 chip 用）。

    Qt 没有内置 flow layout，而这两类标签的数量与宽度都由插件 manifest
    决定（一个插件可能注册 1~5 个动作，名字长短不一），固定列数必然在
    某个宽度下溢出。★ 实现要点：``hasHeightForWidth`` 必须为 True，
    且 ``heightForWidth`` 与 ``setGeometry`` 走**同一套**排布逻辑 ——
    否则布局给的高度与实际排出的行数不一致，末行会被卡片裁掉。
    """

    def __init__(self, parent=None, spacing=5):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ---- QLayout 必备接口 ----
    def addItem(self, item):                       # noqa: N802 - Qt 命名
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):                       # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):                       # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):                 # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):                   # noqa: N802
        return True

    def heightForWidth(self, width):               # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):                   # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):                            # noqa: N802
        return self.minimumSize()

    def minimumSize(self):                         # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ---- 排布（两个入口共用，保证 heightForWidth 与实际几何一致）----
    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        left = rect.x() + m.left()
        right = rect.right() - m.right()
        x, y, line_h = left, rect.y() + m.top(), 0
        for item in self._items:
            hint = item.sizeHint()
            if line_h and x + hint.width() > right:
                x, y, line_h = left, y + line_h + self._spacing, 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


# 插件 manifest 里的 emoji 前缀（动作标题 / 页面标题普遍带，渲染成豆腐块）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\u2B00-\u2BFF"
    "\uFE0E\uFE0F\u200D]+")


def strip_emoji(text: str) -> str:
    """剥离展示层用文本的 emoji 前缀（2026-10-02 卡片重设计）。

    插件包的 ``actions[].title`` 与 ``page.title`` 绝大多数带 emoji
    （🤖 AI 助手 / 🔒 密码保险箱 / 📝 生成日报 / 周报草稿）——这些字符在
    微软雅黑下缺字形、渲染成豆腐块（评审 P0-1 的残留源头之一）。但
    manifest 归插件作者维护、id 与能力声明更是宿主契约，所以**只在展示
    层剥离**，不改任何 manifest 字段。
    """
    return _EMOJI_RE.sub("", text or "").strip()


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
        GlassMessageBox.warning(
            parent, "无法打开",
            f"系统没有找到能打开此文件的程序：\n{path}\n\n{e}\n\n"
            f"可以关联一个 Markdown 编辑器，或直接在本窗口查看。")


class PluginsPanel(QWidget):
    """插件中心面板（主窗口 9 号页）"""

    # 「重新扫描」点击 → 真正开扫的延迟（毫秒）：留出让按钮禁用态与
    # 「正在扫描」轻提示先上屏的一拍（见 _on_rescan 的黑闪修复注释）
    RESCAN_DEFER_MS = 80

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._cards_layout = None
        self._error_layout = None
        self._error_box = None
        self._error_toggle = None     # 失败折叠条标题钮（checkable，2026-10-04）
        self._error_body = None       # 失败卡片容器（折叠时整体隐藏）
        self._error_expanded = False  # 折叠状态（会话级；默认收起）
        self._store_dialog = None     # 插件商店弹窗（懒创建，见 _on_open_store_dialog）
        self._empty_label = None
        self._gate_label = None
        self._count_label = None
        self._dir_label = None
        self._store_dir_label = None
        self._rescan_btn = None
        self._rescan_pending = False  # 重扫描两步走（见 _on_rescan）进行中
        # 搜索 + 状态筛选（2026-10-04 三件套之二）
        self._search_input = None
        self._seg_buttons = {}        # "all"/"on"/"off" -> QPushButton
        self._filter_hint = None
        self._card_records = []       # [(card, lp)]，过滤判据与卡片一一对应
        self._visible_cards = []      # 过滤后参与网格摆放的卡片
        self._build_ui()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 + 唯一主操作 ----
        # 计数从「共 N 个插件」扩成「共 N 个插件 · M 启用」：只有总数时
        # 看不出健康度，带状态才能一眼知道有没有被停掉的插件。
        header = QHBoxLayout()
        title = PageTitle("plugins", "插件中心", self._host)
        header.addWidget(title)
        self._count_label = QLabel("共 0 个插件")
        self._count_label.setObjectName("hintLabel")
        header.addWidget(self._count_label)
        header.addStretch()
        # 「插件商店」升为主按钮并移到页头右端：它是本页唯一的高频正向
        # 操作（装新插件）。此前与「打开目录 / 重新扫描」并排、三者同权重。
        open_store_btn = IconButton("store", text="插件商店",
                                    icon_size=14, object_name="primaryBtn")
        open_store_btn.setToolTip("浏览商店目录里的可安装插件包（独立窗口）")
        open_store_btn.clicked.connect(self._on_open_store_dialog)
        header.addWidget(open_store_btn)
        v.addLayout(header)

        # ---- 提示（2026-10-02 压成一句：原来四句共占 3 行竖向空间）----
        hint = QLabel("插件包（.fpplug）放进安装目录后点「重新扫描」即可用；"
                      "「卸载」只删安装目录里的副本，商店里的源包会保留")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 两个目录的绝对路径（用户最常搞混「装在哪 / 包放哪」）----
        # 单行显示 + 完整路径进 tooltip：路径很长时会自动换行占掉太多竖向
        # 空间，单行截断、tooltip 给全文，兼顾可读与紧凑。
        # 2026-10-02：两个标签并进一个 spacing=0 的紧凑容器（原来各占一行
        # 且各自再吃一个 10px 段间距），视觉上归入「管理信息」而非说明书。
        dirs = QVBoxLayout()
        dirs.setContentsMargins(0, 0, 0, 0)
        dirs.setSpacing(0)
        self._dir_label = QLabel()
        self._dir_label.setObjectName("pluginDirLabel")
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        dirs.addWidget(self._dir_label)

        self._store_dir_label = QLabel()
        self._store_dir_label.setObjectName("pluginDirLabel")
        self._store_dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        dirs.addWidget(self._store_dir_label)
        v.addLayout(dirs)

        # ---- 插件总闸关闭提示条（默认隐藏）----
        self._gate_label = QLabel("插件功能已在设置页停用（全局开关），"
                                  "如需使用请到「设置」开启后重启程序")
        self._gate_label.setObjectName("pluginGateHint")
        self._gate_label.setWordWrap(True)
        self._gate_label.setVisible(False)
        v.addWidget(self._gate_label)

        # ---- 工具栏：搜索 + 状态筛选 + 两个低频操作（主按钮已在页头）----
        # 搜索/筛选（2026-10-04 三件套之二，设计稿 plugins-center-redesign）：
        # 6 个插件时可有可无，装到 15+ 才真正省事——按插件名或动作名过滤 +
        # 全部/已启用/已停用分段，两者叠加生效，只改可见性不重建卡片。
        # 「打开插件目录」「重新扫描」由描边按钮降为文字按钮：一屏只留
        # 一个主按钮，其余按层级递降，不再是一排同权重的按钮汤。
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("pluginSearchInput")
        self._search_input.setPlaceholderText("搜索插件名 / 动作名…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedWidth(190)
        self._search_input.setToolTip("按插件名、插件 id 或动作名过滤下方卡片")
        self._search_input.textChanged.connect(lambda _t: self._apply_filter())
        toolbar.addWidget(self._search_input)

        seg_tips = {
            "all": "显示全部已安装插件",
            "on": "只看状态为「已启用」的插件",
            "off": "只看已停用 / 未提供动作 / 未生效的插件",
        }
        for key, label in (("all", "全部"), ("on", "已启用"), ("off", "已停用")):
            btn = QPushButton(label)
            btn.setObjectName("pluginSegBtn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setChecked(key == "all")
            btn.setToolTip(seg_tips[key])
            btn.clicked.connect(lambda _c=False, _k=key: self._apply_filter())
            self._seg_buttons[key] = btn
            toolbar.addWidget(btn)

        toolbar.addStretch()

        open_dir_btn = IconButton("folder_open", text="打开插件目录",
                                  icon_size=14, object_name="textBtn")
        open_dir_btn.clicked.connect(self._on_open_plugins_dir)
        toolbar.addWidget(open_dir_btn)

        self._rescan_btn = IconButton("refresh", text="重新扫描", icon_size=14,
                                      object_name="textBtn")
        self._rescan_btn.setToolTip(
            "重新扫描插件安装目录与商店目录（不必重启程序）")
        self._rescan_btn.clicked.connect(self._on_rescan)
        toolbar.addWidget(self._rescan_btn)

        toolbar.addStretch()
        v.addLayout(toolbar)

        # ---- 加载失败区（默认隐藏；有失败项时显示在卡片列表上方）----
        # 2026-10-04 折叠条（三件套之三，设计稿 plugins-center-redesign）：
        # 失败是低频状态，不该向每个健康会话征收竖向空间——默认收起成
        # 一条可点击的提示钮，点击才展开失败卡；展开状态会话内记住。
        self._error_box = QFrame()
        self._error_box.setObjectName("pluginErrorBox")
        eb = QVBoxLayout(self._error_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(8)
        self._error_toggle = QPushButton()
        self._error_toggle.setObjectName("pluginErrorToggle")
        self._error_toggle.setCheckable(True)
        self._error_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._error_toggle.setToolTip("展开 / 收起加载失败的插件详情")
        self._error_toggle.toggled.connect(self._on_error_toggled)
        eb.addWidget(self._error_toggle)
        self._error_body = QWidget()
        error_body_lay = QVBoxLayout(self._error_body)
        error_body_lay.setContentsMargins(0, 0, 0, 0)
        error_body_lay.setSpacing(8)
        self._error_layout = QVBoxLayout()
        self._error_layout.setContentsMargins(0, 0, 0, 0)
        self._error_layout.setSpacing(8)
        error_body_lay.addLayout(self._error_layout)
        eb.addWidget(self._error_body)
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
        # 搜索/筛选把所有卡都滤掉时的提示（与「还没安装」空态区分：
        # 这里是「装了但被当前条件滤掉」）
        self._filter_hint = QLabel(
            "没有符合当前搜索 / 筛选条件的插件——换个关键词，或切回「全部」")
        self._filter_hint.setObjectName("hintLabel")
        self._filter_hint.setWordWrap(True)
        self._filter_hint.setContentsMargins(2, 12, 2, 2)
        self._filter_hint.setVisible(False)
        self._cards_outer.addWidget(self._filter_hint)
        self._cards_outer.addStretch()           # 卡片永远顶对齐
        self._card_widgets = []                  # 插入顺序的卡片（重排依据）
        self._card_cols = 2                      # 当前列数（_reflow_cards 维护）
        self._last_rebuild_fp = None             # 重建指纹（refresh_if_stale 守卫）
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        # ---- 空态引导（A3 通用化，2026-10-01）----
        # ★ objectName 用独立的 pluginEmptyState：QSS 里 QLabel#pluginEmptyHint
        #   是商店弹窗那个 QLabel 空态的契约（verify_kb_search 断言 QSS 含该
        #   选择器），不能动；本组件内部标签走 sectionLabel/hintLabel 通用样式。
        # ★ 绝不能放进 _cards_layout —— 测试对卡片网格有精确占位断言。
        # 2026-10-02：补一个动作钮（评审 P2-7「占位文案当设计用」的整改）——
        # 空态是唯一能给出「下一步做什么」的位置，只有说明没有动作等于没引导。
        self._empty_label = EmptyState(
            "plugins", "还没有安装任何插件",
            "点下方按钮从商店安装插件包（.fpplug）\n"
            "或把插件文件夹放进安装目录后点「重新扫描」",
            action_text="打开插件商店", on_action=self._on_open_store_dialog,
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

        # 重建指纹：强刷后同步刷新，供 refresh_if_stale 做切页守卫
        self._last_rebuild_fp = self._fingerprint(
            plugins, errors, self._store_stat_fingerprint(loader), gate_on)

        # ★ 先建后拆（黑闪修复 2026-10-04）：全部新卡片先在布局外建好，
        #   再一次性拆旧挂新。旧顺序「清空 → 逐个建」在半透明玻璃壳
        #   （WA_TranslucentBackground + GlassPanel 半透明填充）下会产生
        #   一帧无 widget 覆盖的空网格，DWM 合成表现为黑底闪现——插件
        #   中心是唯一每次切页都整页重建的页面，故只有它闪。
        new_card_widgets = []
        new_card_records = []
        for lp in plugins:
            card = self._make_card(lp)
            new_card_widgets.append(card)
            new_card_records.append((card, lp))
        new_error_cards = [self._make_error_card(fe) for fe in errors]

        # 清空旧卡片（网格只装卡片本体；尾部 stretch 在外层 vbox，不会被动到）
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._card_widgets = new_card_widgets
        self._card_records = new_card_records

        # 清空旧失败卡片并挂上新的
        while self._error_layout.count():
            item = self._error_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for fe_card in new_error_cards:
            self._error_layout.addWidget(fe_card)

        # 失败区（折叠条：标题常显，卡片按展开态决定可见；卡片本体已在
        # 上方「先建后拆」阶段挂入 new_error_cards，这里只管折叠条状态）
        has_errors = bool(errors)
        self._error_box.setVisible(has_errors)
        if has_errors:
            self._error_toggle.setText(
                f"加载失败的插件（{len(errors)} 个）"
                f"——{'点击收起' if self._error_expanded else '点击展开'}原因与修复建议")
            self._error_toggle.blockSignals(True)
            self._error_toggle.setChecked(self._error_expanded)
            self._error_toggle.blockSignals(False)
            self._error_body.setVisible(self._error_expanded)

        has_plugins = bool(plugins)
        show_empty = (not has_plugins and not has_errors
                      and not self._has_store_pkgs(store))
        # 空态两形态（2026-10-02）：总闸关闭时插件**根本没被加载**（loader
        # 返回空列表），空态必须如实说「未加载」而不是误导成「还没安装任何
        # 插件」；只有真没装时才给出「去商店」这个下一步动作。
        if not gate_on:
            self._empty_label.set_state(
                "plugins", "插件未加载",
                "插件的动作、热键与插件页都已一并摘掉\n"
                "到「设置」打开插件总开关后重启程序即可恢复",
                show_action=False)
        else:
            self._empty_label.set_state(
                "plugins", "还没有安装任何插件",
                "点下方按钮从商店安装插件包（.fpplug）\n"
                "或把插件文件夹放进安装目录后点「重新扫描」",
                show_action=True)
        self._empty_label.setVisible(show_empty)

        # 计数：保留「共 N 个插件」前缀（既有断言口径不变），再补启用数 ——
        # 「有几个」之外还要能一眼看出「有几个是活的」。
        cnt = f"共 {len(plugins)} 个插件"
        n_on = sum(1 for lp in plugins
                   if (self._status_of(lp) or ("", ""))[1] == "pluginStatusOn")
        if plugins:
            cnt += f"，{n_on} 个已启用"
        if has_errors:
            cnt += f"，{len(errors)} 个加载失败"
        n_store = self._installable_count(store)
        if n_store:
            cnt += f"，商店可安装 {n_store} 个"
        self._count_label.setText(cnt)

        # 已安装卡片按行优先进网格（[c0 c1 / c2 c3 ...]，与列表顺序一致）；
        # 卡片本体已在「先建后拆」阶段构建并赋给 _card_records，
        # 这里只按搜索/筛选条件决定可见与占位——过滤只改可见性，
        # 不重建卡片，切回「全部」零开销。
        self._apply_filter()

        # 商店清单本身不在这页渲染（2026-09-28 起在独立弹窗 PluginStoreDialog），
        # 这里只留计数与空态判据：让用户知道「有包可装」，去点工具栏的商店按钮。

    # ---------------- 切页守卫（黑闪修复 2026-10-04） ----------------
    def _store_stat_fingerprint(self, loader):
        """商店目录的轻量指纹（文件名+大小+mtime），避免每次切页读 zip。

        返回 None 表示目录读不了 → 调用方视为「未知」，强制全量刷新
        （保持旧行为）。目录属性拿不到同样返回 None。
        """
        try:
            d = loader.store_dir
        except (AttributeError, TypeError):
            return None
        if not d:
            return None
        try:
            out = []
            for name in sorted(os.listdir(d)):
                if not name.endswith(PLUGIN_PACKAGE_EXT):
                    continue
                st = os.stat(os.path.join(d, name))
                out.append((name, st.st_size, st.st_mtime))
            return tuple(out)
        except OSError:
            return None

    def _fingerprint(self, plugins, errors, store_stats, gate_on):
        """卡片重建指纹：覆盖 _make_card / 失败卡 / 空态 / 计数的数据面。

        - 每个 LoadedPlugin：身份 + 登记结果 + 动作数 + 状态键（停用态
          由 _state_key 动态判，启停按钮就地更新后指纹自然失配）；
        - 每个 FailedPlugin：身份 + 阶段 + 原因；
        - store 维度用 stat 指纹（新丢包 / 换包即失配）；
        - 总闸状态直接入指纹。
        """
        pl = tuple(
            (getattr(lp, "plugin_id", ""), getattr(lp, "name", ""),
             getattr(lp, "version", ""), bool(getattr(lp, "registered", False)),
             getattr(lp, "actions_ok", 0),
             len(getattr(lp, "actions_raw", []) or []),
             len(getattr(lp, "warnings", []) or []),
             PluginsPanel._state_key(lp))
            for lp in plugins)
        er = tuple(
            (getattr(fe, "plugin_id", "") or getattr(fe, "folder", ""),
             getattr(fe, "stage", ""), str(getattr(fe, "reason", "")))
            for fe in errors)
        return (pl, er, store_stats, bool(gate_on))

    def refresh_if_stale(self):
        """切页路径专用刷新：数据没变就零重建。

        插件中心此前每次切页都整页重建卡片（全窗最大面积重绘），在
        WA_TranslucentBackground 半透明壳架构下存在 DWM 合成黑帧的
        触发面（用户报障：每次点插件中心导航键闪黑窗）。指纹相同
        （且面板曾渲染过）时直接返回——卡片、失败卡、计数全部不动；
        数据有任何变化（启停/安装/卸载/重扫/丢包）则走全量 refresh()。
        """
        loader = getattr(self._host, "plugin_loader", None)
        gate_on = True
        try:
            gate_on = bool(self._host._config.get("plugins_enabled", True))
        except (AttributeError, TypeError):
            pass
        plugins = []
        errors = []
        if loader is not None and gate_on:
            try:
                plugins = list(loader.loaded_plugins())
            except Exception:                     # noqa: BLE001 - 展示层兜底
                plugins = []
            try:
                errors = list(loader.load_errors())
            except Exception:                     # noqa: BLE001 - 旧 loader 兜底
                errors = []
        store_stats = ()
        if loader is not None and gate_on:
            store_stats = self._store_stat_fingerprint(loader)
        fp = self._fingerprint(plugins, errors, store_stats, gate_on)
        ever_built = bool(self._card_records) or bool(self._card_widgets)
        if ever_built and fp == self._last_rebuild_fp:
            return
        self.refresh()

    # ---------------- 双列网格与搜索/筛选 ----------------
    def _regrid(self):
        """把**当前可见**的卡片按行优先摆进网格（过滤 / 重排共用一处）。

        被滤掉的卡片不进网格（隐藏控件留在布局里会留下空洞占位），
        只作为 self._card_records 的成员保留，切回「全部」时零重建回来。
        """
        while self._cards_layout.count():
            self._cards_layout.takeAt(0)
        for idx, card in enumerate(self._visible_cards):
            row, col = divmod(idx, self._card_cols)
            self._cards_layout.addWidget(card, row, col)
        self._cards_layout.setColumnStretch(1, 1 if self._card_cols == 2 else 0)

    def _reflow_cards(self, width: int):
        """容器宽度变化：两列放不下按钮行时回落单列（反之亦然），重排现有卡片。

        由 _CardsScroll.resizeEvent 驱动；列数没变就不动（resize 高频
        触发，重排一次要拆装全部条目）。
        """
        want = 2 if width >= GRID_TWO_COL_MIN_WIDTH else 1
        if want == self._card_cols:
            return
        self._card_cols = want
        self._regrid()

    def _current_seg(self) -> str:
        """当前分段筛选键："all" / "on" / "off"（无勾选时兜底 "all"）"""
        for key, btn in self._seg_buttons.items():
            if btn.isChecked():
                return key
        return "all"

    def _card_matches_filter(self, lp) -> bool:
        """单张卡片是否通过当前「分段 + 搜索」条件（两者叠加，AND 语义）。

        分段按 :meth:`_state_key` 同源判定：已启用 = on；已停用段收拢
        其余全部状态（已停用 / 未提供动作 / 未生效）——三段式 UI 里
        「非全部启用」是它唯一的反义，tooltip 里已写明口径。
        搜索词匹配插件名 / 插件 id / 动作名（大小写不敏感的包含匹配）。
        """
        seg = self._current_seg()
        state = self._state_key(lp)
        if seg == "on" and state != "on":
            return False
        if seg == "off" and state == "on":
            return False
        query = ""
        if self._search_input is not None:
            query = self._search_input.text().strip().lower()
        if not query:
            return True
        actions = list(getattr(lp, "actions_raw", []) or [])
        haystack = " ".join(
            [strip_emoji(getattr(lp, "name", "") or ""),
             getattr(lp, "plugin_id", "") or ""]
            + [strip_emoji(getattr(a, "title", "") or getattr(a, "id", "") or "")
               for a in actions]
        ).lower()
        return query in haystack

    def _apply_filter(self):
        """按搜索词 + 分段重摆卡片：只改可见性与网格占位，不重建卡片。"""
        self._visible_cards = []
        for card, lp in self._card_records:
            visible = self._card_matches_filter(lp)
            card.setVisible(visible)
            if visible:
                self._visible_cards.append(card)
        self._regrid()
        if self._filter_hint is not None:
            # 「装了但全被滤掉」才提示；真没装走 EmptyState 空态
            self._filter_hint.setVisible(
                bool(self._card_records) and not self._visible_cards)

    def _on_error_toggled(self, checked: bool):
        """失败折叠条展开 / 收起（标题钮 checked ↔ 卡片容器可见）"""
        self._error_expanded = bool(checked)
        if self._error_body is not None:
            self._error_body.setVisible(self._error_expanded)
        if self._error_toggle is not None and self._error_box is not None \
                and self._error_box.isVisibleTo(self):
            n = self._error_layout.count()
            self._error_toggle.setText(
                f"加载失败的插件（{n} 个）"
                f"——{'点击收起' if checked else '点击展开'}原因与修复建议")

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
        """重新扫描插件目录（不必重启程序）——两步走 + 轻提示。

        2026-10-05 黑闪修复：原实现把「扫描装配 + 菜单重建 + 整页重建
        卡片」整段压在一个点击处理器里同步跑完，半透明玻璃壳
        （WA_TranslucentBackground）在整个期间无法重绘，重绘空档被 DWM
        合成成黑帧（与 refresh() 的 build-then-swap 修的是同族根因），
        且动作完成前没有任何反馈。现在点击只禁用按钮 + 弹「正在扫描」
        轻提示并立即返回（这两件事先上屏），重活由单发定时器延到下一拍
        （``_do_rescan``）执行，完成后用结果轻提示收尾。
        """
        if self._rescan_pending:
            return
        self._rescan_pending = True
        if self._rescan_btn is not None:
            self._rescan_btn.setEnabled(False)
        self._toast("正在重新扫描插件…")
        # 定时器挂 self：面板销毁时一并销毁，不会回调到已删除的对象
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.timeout.connect(self._do_rescan)
        self._rescan_timer.start(self.RESCAN_DEFER_MS)

    def _do_rescan(self):
        """deferred 槽：真正执行扫描 + 装配 + 刷新（见 _on_rescan 注释）"""
        try:
            loader = getattr(self._host, "plugin_loader", None)
            n_ok = n_err = 0
            scan_error = None
            if loader is not None:
                rescan = getattr(loader, "rescan", None)
                try:
                    plugins = (rescan() if callable(rescan)
                               else loader.load_all())
                    plugins = list(plugins or [])
                    n_ok = len(plugins)
                    try:
                        n_err = len(loader.load_errors())
                    except Exception:              # 旧 loader 无此接口
                        n_err = 0
                except Exception as e:             # noqa: BLE001 - 扫描失败不崩 UI
                    print(f"[插件中心] 重新扫描失败: {e}")
                    scan_error = str(e) or type(e).__name__
            # 插件增减会影响悬浮球右键菜单，通知宿主动作表重建
            self._notify_menu_rebuild()
            self.refresh()
            if scan_error is not None:
                done = f"重新扫描失败：{scan_error}"
            elif n_ok or n_err:
                done = f"重新扫描完成：{n_ok} 个插件可用"
                if n_err:
                    done += f"，{n_err} 个加载失败"
            else:
                done = "重新扫描完成：未发现插件"
            self._toast(done)
        finally:
            self._rescan_pending = False
            if self._rescan_btn is not None:
                self._rescan_btn.setEnabled(True)

    def _toast(self, text: str):
        """轻提示（宿主无 show_toast 能力时静默跳过，测试替身同款容错）"""
        toast = getattr(self._host, "show_toast", None)
        if callable(toast):
            try:
                toast(text)
            except Exception:                      # noqa: BLE001 - 展示层兜底
                pass

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
        """单个已装插件卡片（2026-10-02 重设计）。

        版式：卡体分「主体」与「底部操作行（#pluginCardFoot，通栏 + 上
        分隔线、吸底）」两段；主体再横排成 ``28×28 图标槽 ‖ 四行内容``：

            [图标槽]  插件名   v1.2.3            ● 已启用
                      描述（最多 DESC_MAX 字，全文进悬停提示）
                      能力标签（描边＝标签性质）…
                      动作 chip（实底＝可执行性质）…

        卡片左缘 3px 状态条、图标槽与状态点都由 ``setProperty("state", …)``
        驱动 QSS 属性选择器（``on`` / ``off`` / ``warn``），三处状态同源
        （见 :meth:`_state_key`），不会互相打架。
        """
        card = QFrame()
        card.setObjectName("pluginCard")
        state = self._state_key(lp)
        card.setProperty("state", state)

        manifest = getattr(lp, "manifest", {}) or {}

        # v（卡片总列）只负责「主体吸顶 + 操作行吸底」，间距全交给子布局，
        # 这样主体与操作行之间的留白不会被 addStretch 吃掉。
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        main = QHBoxLayout()
        main.setContentsMargins(12, 12, 14, 11)
        main.setSpacing(10)
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(7)

        # ---- 图标槽：28×28 卡片视觉锚点（独立控件，不参与文字度量）----
        slot = QFrame()
        slot.setObjectName("pluginIconSlot")
        slot.setProperty("state", state)
        slot.setFixedSize(28, 28)
        slot_lay = QVBoxLayout(slot)
        slot_lay.setContentsMargins(0, 0, 0, 0)
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        _colors = get_colors(theme)
        icon_color = {"on": _colors["primary"], "warn": _colors["warn"]}.get(
            state, _colors["text_secondary"])
        slot_lay.addWidget(IconLabel("plugin", 15, icon_color), 0,
                           Qt.AlignmentFlag.AlignCenter)
        main.addWidget(slot, 0, Qt.AlignmentFlag.AlignTop)

        # ---- 标题行：名称（大字）+ 版本（等宽小字）+ 状态点&文字 ----
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        name = QLabel(strip_emoji(lp.name))
        name.setObjectName("pluginCardTitle")
        head.addWidget(name)

        ver = QLabel(f"v{lp.version}")
        ver.setObjectName("pluginCardId")
        head.addWidget(ver)

        head.addStretch()

        # 状态＝圆点 + 文字（取代旧描边胶囊）：胶囊在暗色下发重、抢标题，
        # 点+字轻，且文字钉在标题行右端，一排卡自然形成可扫读的「状态列」。
        status = self._status_of(lp)
        if status is not None:
            dot = QFrame()
            dot.setObjectName("pluginStatusDot")
            dot.setProperty("state", state)
            dot.setFixedSize(6, 6)
            head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            tag = QLabel(status[0])
            tag.setObjectName(status[1])
            head.addWidget(tag)

        # ---- ⋯ 渐进披露菜单入口（2026-10-04 三件套之一）----
        # 插件 ID / 依赖 / 注册页面是「需要时一眼能找到，不需要时不出现」
        # 的低频元信息（设计稿 plugins-center-redesign），收进菜单而非
        # 常驻卡面——卡面最贵的右上角留给状态列。
        more_btn = IconButton("more", size=20, icon_size=14,
                              object_name="pluginMoreBtn",
                              tooltip="插件 ID / 依赖 / 注册页面 / 复制 ID")
        more_btn.clicked.connect(lambda _checked=False, p=lp:
                                 self._show_card_menu(p))
        head.addWidget(more_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(head)

        # ---- 描述行：manifest.description > 类 docstring 首行 ----
        # 卡面最多 DESC_MAX 字（双列卡宽下约一行半，一眼扫完），
        # 超长截断加省略号，全文进悬停提示（2026-10-01 卡片降噪；
        # 用户反馈：介绍看一眼定位即可，详细用法点「查看使用说明」看 md）。
        desc = (manifest.get("description") or "").strip()
        if not desc:
            doc = getattr(type(lp.plugin), "__doc__", "") or ""
            desc = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if desc:
            desc_label = QLabel(clip_text(strip_emoji(desc), DESC_MAX))
            desc_label.setObjectName("pluginCardDesc")
            desc_label.setWordWrap(True)
            if len(desc) > DESC_MAX:
                desc_label.setToolTip(desc)
            body.addWidget(desc_label)

        # ---- 使用说明摘要不上卡（2026-10-01 用户拍板）----
        # 描述与「摘自 md 的摘要」两段都在介绍「这插件是干嘛的」，重复；
        # 详细用法本来就有点下方「查看使用说明」按钮（程序内渲染 md），
        # 卡面不再重复展示——需要详情时按一次按钮就能看到全文。

        # ---- 动作级告警（热键被占 / 未声明等）----
        for warn in list(getattr(lp, "warnings", []) or []):
            wl = QLabel(warn)
            wl.setObjectName("pluginErrorHint")
            wl.setWordWrap(True)
            body.addWidget(wl)

        # ---- 能力标签 + 卡片悬停详情（2026-10-01 卡片降噪）----
        # 能力：一排描边标签（网络 / 写入 / 改删 / AI，各带自绘图标），
        # 完整语义在标签悬停提示里；依赖是开发者信息，不占卡面行，连同被
        # 截断的描述全文一起挂进**卡片整体悬停提示**（悬停卡面空白处可见
        # ——QToolTip 沿父链找最近的有提示的控件）。
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
            caps_host = QWidget()
            caps_host.setSizePolicy(QSizePolicy.Policy.Preferred,
                                    QSizePolicy.Policy.Minimum)
            caps_lay = _FlowLayout(caps_host, spacing=5)
            for c in caps:
                caps_lay.addWidget(self._make_cap_badge(c))
            body.addWidget(caps_host)

        # ---- 动作 chip：一个动作一枚实底 chip，横向流式排列、自动换行 ----
        # 取代此前「· 动作名〔右键菜单〕热键」三标签逐行铺开——动作多的
        # 插件（如定时任务）逐行能吃掉半屏；chip 化后宽度自适应、可并排。
        actions = list(getattr(lp, "actions_raw", []) or [])
        acts_host = QWidget()
        acts_host.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Minimum)
        acts_lay = _FlowLayout(acts_host, spacing=5)
        if actions:
            for act in actions:
                acts_lay.addWidget(self._make_act_chip(act))
        else:
            no_act = QLabel("（无注册动作）")
            no_act.setObjectName("pluginActTag")   # 弱化灰，不与描述抢层级
            acts_lay.addWidget(no_act)
        body.addWidget(acts_host)

        main.addLayout(body, 1)
        v.addLayout(main, 1)

        # ---- 底部操作行（通栏 + 上分隔线、吸底）----
        # 「停用 / 启用」是唯一次高频开关 → 固定留在左侧；其余三个低频操作
        # （打开目录 / 查看使用说明 / 卸载）靠右，一屏只有一个视觉重心。
        foot = QFrame()
        foot.setObjectName("pluginCardFoot")
        bottom = QHBoxLayout(foot)
        bottom.setContentsMargins(12, 9, 14, 10)
        bottom.setSpacing(6)

        toggle_btn = self._make_toggle_btn(lp, actions)
        if toggle_btn is not None:
            bottom.addWidget(toggle_btn)

        bottom.addStretch()

        open_btn = IconButton("folder_open", text="打开目录", icon_size=14,
                              object_name="textBtn")
        open_btn.clicked.connect(
            lambda _checked=False, p=lp.path: self._on_open_dir(p))
        bottom.addWidget(open_btn)

        readme = find_usage_file(lp.path)
        if readme:
            readme_btn = IconButton("file_text", text="查看使用说明",
                                    icon_size=14, object_name="textBtn")
            readme_btn.clicked.connect(
                lambda _checked=False, p=readme: self._on_open_file(p))
            bottom.addWidget(readme_btn)

        # ---- 设置按钮（2026-10-04 插件独立设置）----
        # 只有 manifest 声明了非空 settings 的插件才有（与能力三档同款
        # 声明式模型：不声明就没有入口）。编辑走 GlassDialog 弹层表单，
        # 存储落插件私有目录（plugin_settings），不进主 config。
        if manifest.get("settings"):
            settings_btn = IconButton("settings", text="设置", icon_size=14,
                                      object_name="textBtn")
            settings_btn.setToolTip("配置该插件声明的设置项"
                                    "（保存在插件私有数据目录，不进主配置）")
            settings_btn.clicked.connect(
                lambda _checked=False, p=lp: self._on_open_plugin_settings(p))
            bottom.addWidget(settings_btn)

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

        v.addWidget(foot)
        return card

    def _make_act_chip(self, act) -> QWidget:
        """一枚动作 chip：动作名 +（右键菜单 / 热键）归属（2026-10-02）。

        动作名来自 manifest，普遍带 emoji 前缀（🤖 / 📝 / 🔒），交给
        :func:`strip_emoji` 在展示层剥离（雅黑缺字形会渲染成豆腐块）；
        热键用等宽小字 + 浅底，与动作名在视觉上分层。
        """
        chip = QFrame()
        chip.setObjectName("pluginActChip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(7, 3, 8, 3)
        row.setSpacing(5)

        title = QLabel(strip_emoji(getattr(act, "title", "") or act.id))
        title.setObjectName("pluginActName")
        row.addWidget(title)

        if getattr(act, "menu", False):
            menu_tag = QLabel("右键菜单")
            menu_tag.setObjectName("pluginActTag")
            row.addWidget(menu_tag)

        hotkey = (getattr(act, "hotkey", None) or "").strip()
        if hotkey:
            hk = QLabel(hotkey)
            hk.setObjectName("pluginActHotkey")
            row.addWidget(hk)
        return chip

    def _make_cap_badge(self, cap: str):
        """能力胶囊徽章：自绘图标 + 短文案，完整语义进悬停提示（三卡共用）。

        文字仍是 ``QLabel#pluginCapBadge``（QSS 胶囊契约与既有断言口径不变）；
        图标是它左侧的独立 ``IconLabel``，不参与文字度量。
        """
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        row.addWidget(IconLabel(CAP_ICONS.get(cap, "info"), 12,
                                get_colors(theme)["secondary_text"]))
        lab = QLabel(CAP_BADGES.get(cap, CAP_LABELS.get(cap, cap)))
        lab.setObjectName("pluginCapBadge")
        lab.setToolTip(CAP_TIPS.get(cap, ""))
        row.addWidget(lab)
        return wrap

    # ---------------- 卡片 ⋯ 渐进披露菜单（2026-10-04 三件套之一）----------------
    def _host_qss(self) -> str:
        """宿主主窗口当前 QSS（给 ⋯ 菜单与主窗右键菜单同一套视觉）。

        测试替身没有 _container / styleSheet → 返回空串，菜单退回系统
        默认样式，不崩。
        """
        container = getattr(self._host, "_container", None)
        ss = getattr(container, "styleSheet", None)
        if callable(ss):
            try:
                return ss() or ""
            except Exception:                     # noqa: BLE001 - 展示层兜底
                return ""
        return ""

    def _build_card_menu(self, lp) -> QMenu:
        """构建单张卡片的 ⋯ 菜单（构建与弹出分离，离屏测试可直达构建）。

        信息行（插件 ID / 依赖 / 注册页面）用 QWidgetAction 承载——它们是
        「展示」不是「命令」，不该混进可勾选的 QAction 文本流里。依赖在
        卡面悬停提示里仍保留（降噪批次的既有契约，不动）。
        """
        menu = QMenu(self)
        qss = self._host_qss()
        if qss:
            menu.setStyleSheet(qss)

        manifest = getattr(lp, "manifest", {}) or {}
        requires = [str(r).strip() for r in (manifest.get("requires") or [])
                    if str(r).strip()]
        page = manifest.get("page") or {}
        page_title = ""
        if isinstance(page, dict):
            page_title = strip_emoji(str(page.get("title", "") or ""))
        rows = [
            ("插件 ID", getattr(lp, "plugin_id", "") or "（未知）"),
            ("依赖", "、".join(requires) if requires else "（未声明）"),
            ("注册页面", page_title or "（无插件页）"),
        ]
        for title, value in rows:
            widget = QWidget()
            lay = QHBoxLayout(widget)
            lay.setContentsMargins(12, 4, 14, 4)
            lay.setSpacing(12)
            t = QLabel(title)
            t.setObjectName("pluginMenuInfoTitle")
            v = QLabel(value)
            v.setObjectName("pluginMenuInfoValue")
            v.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(v, 1, Qt.AlignmentFlag.AlignVCenter)
            act = QWidgetAction(menu)
            act.setDefaultWidget(widget)
            menu.addAction(act)

        menu.addSeparator()
        pid = getattr(lp, "plugin_id", "") or ""
        copy_act = menu.addAction("复制插件 ID")
        copy_act.setEnabled(bool(pid))
        copy_act.triggered.connect(lambda _c=False, p=pid:
                                   self._copy_plugin_id(p))
        return menu

    def _show_card_menu(self, lp):
        """在光标处弹出 ⋯ 菜单（构建与弹出分离：离屏测试只调构建）"""
        menu = self._build_card_menu(lp)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def _copy_plugin_id(self, plugin_id: str):
        """复制插件 id 到剪贴板（⋯ 菜单唯一命令项），成功给一条轻提示"""
        if not plugin_id:
            return
        QApplication.clipboard().setText(plugin_id)
        toast = getattr(self._host, "show_toast", None)
        if callable(toast):
            try:
                toast(f"已复制插件 ID：{plugin_id}")
            except Exception:                     # noqa: BLE001 - 展示层兜底
                pass

    def _make_store_card(self, entry) -> QWidget:
        """商店里一个可安装包的卡片：名称 + 版本 + 状态 + 描述 + 安装按钮。

        三种形态：
          - 未安装（可用）→ 主按钮「安装」
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
            el = QLabel(error)
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

        install_btn = IconButton("download", text="安装", icon_size=14,
                                 object_name="primaryBtn")
        install_btn.setToolTip(
            "把该插件包解压到插件安装目录并加载；源包保留在商店目录，"
            "卸载后仍可再装")
        if not usable:
            install_btn.setEnabled(False)
            install_btn.setText("无法安装")
            install_btn.setToolTip("插件包不合法，先按下方提示修正后再试")
        elif installed:
            install_btn.setEnabled(False)
            install_btn.setText("已安装")
            install_btn.setToolTip("该插件已装在插件安装目录；如需重装请先卸载")
        else:
            install_btn.clicked.connect(
                lambda _checked=False, p=plugin_id,
                n=title_text: self._on_install(p, n))
        bottom.addWidget(install_btn)

        open_btn = IconButton("folder_open", text="打开目录", icon_size=14,
                              object_name="secondaryBtn")
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
        # 阶段标签走专用 pluginStageTag（描边胶囊）：正常卡的状态已改成
        # 「圆点+文字」，失败卡是异常物、可以比正常卡重一点，两者不再共用
        # 同一个 objectName，改一处不会误伤另一处。
        stage_tag = QLabel(stage_label(getattr(fe, "stage", "")))
        stage_tag.setObjectName("pluginStageTag")
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
            hint_label = QLabel(hint)
            hint_label.setObjectName("pluginErrorHint")
            hint_label.setWordWrap(True)
            v.addWidget(hint_label)

        # 操作行
        path = getattr(fe, "path", "")
        if path:
            bottom = QHBoxLayout()
            bottom.addStretch()
            open_btn = IconButton("folder_open", text="打开目录", icon_size=14,
                                  object_name="secondaryBtn")
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

    @staticmethod
    def _state_key(lp) -> str:
        """卡片状态键（QSS 属性选择器用）：``on`` / ``off`` / ``warn``。

        与 :meth:`_status_of` **同源**（后者给 objectName，本方法映射成属性
        值），避免「左缘状态条」与「状态文字」各判一次而打架。判定不出来时
        返回 ``off`` —— 中性灰是最诚实的「未知」表达。
        """
        st = PluginsPanel._status_of(lp)
        if st is None:
            return "off"
        return {"pluginStatusOn": "on",
                "pluginStatusWarn": "warn"}.get(st[1], "off")

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
        btn = SmoothButton("停用" if on else "启用")
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

    # ---------------- 插件设置（2026-10-04） ----------------
    def _on_open_plugin_settings(self, lp):
        """打开单个插件的设置弹层（GlassDialog 按 manifest.settings 渲染表单）。

        构建与弹出分离（build_plugin_settings_dialog 是模块级函数），
        离屏测试可以只构建不弹出。无设置项 / manifest 缺失 → 静默返回：
        按钮本就不该出现在这类插件上，这里是双保险。
        """
        dlg = build_plugin_settings_dialog(self._host, lp)
        if dlg is None:
            return
        dlg.exec()

    # ---------------- 安装 / 卸载 ----------------
    def _on_install(self, plugin_id: str, name: str):
        """从商店安装插件：委托 loader.install_from_store()，成功即重新扫描。

        与「卸载」对称：卸载把包留在商店，安装把包从商店解出来。
        解压逻辑**全部在 loader 层**，UI 只负责触发与展示结果
        （UI 层不碰 zip，这条约束与「不碰插件目录」同级）。
        """
        loader = getattr(self._host, "plugin_loader", None)
        if loader is None or not hasattr(loader, "install_from_store"):
            GlassMessageBox.warning(self, "无法安装",
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
            # 成功回执降级为屏幕 toast：与窗口 z 序无关，天然不会被
            # 商店弹窗等模态窗口盖住（2026-10 统一改造，交互变化已拍板）
            toast = getattr(self._host, "show_toast", None)
            if callable(toast):
                toast(f"已安装：{msg}（插件已加载，可直接使用）")
        else:
            GlassMessageBox.warning(self._active_dialog_or_self(), "安装失败", msg)

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
            GlassMessageBox.warning(self, "无法卸载",
                                    "当前插件加载器不支持卸载，请手动删除插件目录。")
            return

        if not GlassMessageBox.question(
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
                danger=True):
            return

        try:
            ok, msg = loader.uninstall(plugin_id)
        except Exception as exc:                   # noqa: BLE001 - 卸载失败不崩 UI
            ok, msg = False, f"卸载过程出错：{exc}"

        if ok:
            # 卸载会改变动作集合 → 重建右键菜单 + 刷新卡片
            self._notify_menu_rebuild()
            self.refresh()
            # 成功回执降级为屏幕 toast（同 _on_install 口径）
            toast = getattr(self._host, "show_toast", None)
            if callable(toast):
                toast(f"已卸载：{msg}")
        else:
            GlassMessageBox.warning(self._active_dialog_or_self(), "卸载失败", msg)


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
        super().__init__(host=panel._host, title="插件商店",
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
        self._online_btn = IconButton("nav", text="检查在线市场",
                                      icon_size=14, object_name="secondaryBtn")
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
            ("打开商店目录", "secondaryBtn",
             lambda _checked=False: self._panel._on_open_store_dir(),
             "folder_open"),
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
            self._dir_label.setText("商店目录：%s" % store_dir)
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
                f"另有 {n_installed} 个插件包已安装——"
                f"卸载插件后可回到此处重装")
            theme = (getattr(self._panel._host, "current_theme", None)
                     or DEFAULT_THEME)
            self._installed_hint.setStyleSheet(
                f"color: {get_colors(theme)['success']};")
            self._installed_hint.setVisible(True)
        else:
            self._installed_hint.setVisible(False)

        self._empty_label.setVisible(not pending)
        if not pending:
            self._empty_label.setText(
                "商店目录里还没有可安装的插件包\n\n"
                "把 .fpplug 插件包（或含 manifest.json 的文件夹）\n"
                "放进上方商店目录，回到本窗口点「刷新」即可看到"
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
        """「检查在线市场」：拉索引 → 拉最新 Release 解析附件 id → 出卡片。

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
            note += f"\n{p}"
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

    def _make_cap_badge(self, cap: str):
        """能力徽章：委托面板实现（三卡共用同一渲染，避免重复定义）。"""
        return self._panel._make_cap_badge(cap)

    def _make_online_card(self, it: dict) -> QWidget:
        """在线市场卡片：与本地商店卡同款骨架，按钮是「下载安装」。

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
        btn = IconButton("download", text="下载安装", icon_size=14,
                         object_name="primaryBtn")
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
        btn.setText("下载中…")
        self._set_online_status(f"正在下载 {it['name']}（{it['size'] / 1024:.1f} KB）…")
        started = self._market_bytes_getter(
            plugin_market.asset_download_url(asset_id),
            headers=plugin_market.download_headers(),
            timeout=plugin_market.DOWNLOAD_TIMEOUT_S,
            on_done=lambda res, item=it: self._on_downloaded(item, res))
        if not started:
            self._market_set_busy(False)
            btn.setEnabled(True)
            btn.setText("下载安装")

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
        self._set_online_status(f"{it['name']} 已从在线市场安装")


class PluginSettingsDialog(GlassDialog):
    """插件独立设置弹层（2026-10-04）：按 manifest.settings 通用渲染表单。

    声明式契约：宿主不认识任何具体插件的设置语义，只按 schema 渲染——
      - ``bool``  → ToggleSwitch（与设置页同款开关，主题色自绘）
      - ``int``   → Stepper（禁 QSlider；min/max 来自 schema）
      - ``float`` → Stepper（divisor=100 / decimals=2，两位小数档）
      - ``enum``  → QComboBox（choices 来自 schema）
    type 只有四种是 manifest 校验期收窄的（validate_manifest），这里
    不存在第五种分支——多出来的类型根本进不了插件加载。

    主题：GlassDialog 复用主窗口 QSS，light/dark 自动跟随；保存 / 取消
    走 primaryBtn / secondaryBtn（$on_primary / $secondary_text 由 QSS
    统一管）。表单构建完成后**再调一次 apply_theme**：基类 __init__ 末尾
    首次调用时 Stepper 内部的 IconButton 还不存在，需要 findChildren 兜底
    （dark 主题下否则会拿到 light 配色的 ± 图标）。

    保存语义：逐项 plugin_settings.set_one（类型 / 边界收紧校验、原子
    落盘、遗留键保留）；**实际有改动**的 key 列表经 ctx.settings_changed
    发射（无改动不发，插件不必空转重读）；任一项写失败 → 弹窗保持打开
    让用户改（绝不带病关闭丢输入）。
    """

    # float 档位：内部整数 = 显示值 × 100（两位小数），与设置页
    # 「 pomodoro 长度」同款手法；schema 未给 min/max 时存储层按
    # [0, 100000] 兜底，这里再兜一层防 KeyError（normal 之后必有两键，
    # 双保险只为测试替手写 schema 的场景）
    FLOAT_DIVISOR = 100

    def __init__(self, host, lp, data_dir_base=None):
        self._lp = lp
        self._plugin_id = getattr(lp, "plugin_id", "") or ""
        manifest = getattr(lp, "manifest", {}) or {}
        self._entries = [dict(e) for e in (manifest.get("settings") or [])
                         if isinstance(e, dict)]
        self._data_dir_base = data_dir_base
        self._widgets = {}            # key -> (widget, 取值 getter)
        theme = getattr(host, "current_theme", None) or DEFAULT_THEME
        self._theme = theme if theme in ("light", "dark") else DEFAULT_THEME
        name = strip_emoji(getattr(lp, "name", "") or "") or self._plugin_id
        super().__init__(host=host, title="插件设置",
                         subtitle=f"{name} · {self._plugin_id}",
                         size=(520, 500))
        self._build_form()
        self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            ("保存", "primaryBtn", self._on_save),
        ])
        self.apply_theme()            # 补一轮：Stepper 内部 IconButton 主题色

    # ---------------- 表单 ----------------
    def _build_form(self):
        """按 schema 顺序渲染设置行；≤12 项一屏放得下，仍给滚动区兜底"""
        body = QWidget(self)
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        if not self._entries:
            hint = QLabel("该插件没有声明任何设置项", body)
            hint.setObjectName("hintLabel")
            form.addWidget(hint)
        for entry in self._entries:
            form.addWidget(self._make_row(entry))
        form.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.body_layout.addWidget(scroll, 1)
        self._apply_current_values()

    def _make_row(self, entry) -> QWidget:
        """一行 = 左侧 label + 右侧控件（label 承载插件给的文案）"""
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        label = QLabel(str(entry.get("label") or entry.get("key") or ""), row)
        label.setObjectName("fieldLabel")
        label.setToolTip(f"key: {entry.get('key')} · type: {entry.get('type')}")
        lay.addWidget(label)
        lay.addStretch(1)
        widget, getter = self._make_control(entry)
        lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._widgets[entry["key"]] = (widget, getter)
        return row

    def _make_control(self, entry) -> tuple:
        """按 type 构造控件，返回 ``(widget, 取值 getter)``。

        getter 返回值必须与 schema 类型对齐：bool→bool、int→int、
        float→float（内部整数 ÷ 100）、enum→str——set_one 的写入校验
        以此为准。
        """
        stype = entry.get("type")
        if stype == "bool":
            switch = ToggleSwitch(checked=bool(entry.get("default", False)),
                                  theme=self._theme)
            return switch, switch.isChecked
        if stype == "enum":
            combo = QComboBox(self)
            combo.addItems([str(c) for c in entry.get("choices", [])])
            combo.setCurrentText(str(entry.get("default") or ""))
            return combo, combo.currentText

        lo = entry.get("min", 0)
        hi = entry.get("max", 100000)
        default = entry.get("default", lo)
        if stype == "float":
            step = Stepper(int(round(float(lo) * self.FLOAT_DIVISOR)),
                           int(round(float(hi) * self.FLOAT_DIVISOR)),
                           int(round(float(default) * self.FLOAT_DIVISOR)),
                           divisor=self.FLOAT_DIVISOR, decimals=2)
            return step, (lambda st=step: st.value() / float(self.FLOAT_DIVISOR))
        stepper = Stepper(int(lo), int(hi), int(default))
        return stepper, stepper.value

    def _apply_current_values(self):
        """按当前生效值批量刷新控件（blockSignals：控件此刻虽无业务订阅，
        批量刷新前统一静默是本面板的既定纪律——防未来接信号时踩雷）"""
        values = plugin_settings.get_all(self._plugin_id, self._entries,
                                         self._data_dir_base)
        for entry in self._entries:
            packed = self._widgets.get(entry["key"])
            if packed is None:
                continue
            widget, _getter = packed
            widget.blockSignals(True)
            self._set_widget_value(widget, entry, values.get(entry["key"]))
            widget.blockSignals(False)

    @staticmethod
    def _set_widget_value(widget, entry, value):
        """把一个生效值写回控件（value 为 None 时落在 schema default）"""
        stype = entry.get("type")
        if value is None:
            value = entry.get("default")
        if stype == "bool":
            widget.setChecked(bool(value))
        elif stype == "enum":
            text = str(value if value is not None else entry.get("default"))
            items = [widget.itemText(i) for i in range(widget.count())]
            if text in items:
                widget.setCurrentText(text)
        elif stype == "float":
            widget.setValue(int(round(float(value)
                                      * PluginSettingsDialog.FLOAT_DIVISOR)))
        else:
            widget.setValue(int(value))

    # ---------------- 保存 ----------------
    def _on_save(self):
        """保存：逐项合并校验落盘；有实际改动的 key 经 settings_changed 发射。

        控件已按 schema 收窄（Stepper 钳边界、开关只有两态、下拉只有
        choices），set_one 的写入校验正常不会拒——它防的是手改文件之外
        的编程调用。写失败保持弹窗打开，用户的其余改动不丢。
        """
        current = plugin_settings.get_all(self._plugin_id, self._entries,
                                          self._data_dir_base)
        changed = []
        failed = []
        for entry in self._entries:
            key = entry["key"]
            packed = self._widgets.get(key)
            if packed is None:
                continue
            widget, getter = packed
            try:
                value = getter()
            except Exception as exc:      # noqa: BLE001 - 控件读值失败不崩弹窗
                failed.append(f"{entry.get('label') or key}：读取控件值失败"
                              f"（{exc}）")
                continue
            if key in current and value == current[key]:
                continue
            ok, msg = plugin_settings.set_one(
                self._plugin_id, self._entries, key, value,
                self._data_dir_base)
            if ok:
                changed.append(key)
            else:
                failed.append(f"{entry.get('label') or key}：{msg}")
        if failed:
            GlassMessageBox.warning(self, "保存失败",
                                "以下设置项未能保存：\n· "
                                + "\n· ".join(failed))
            return
        if changed:
            self._emit_settings_changed(changed)
        self.accept()

    def _emit_settings_changed(self, keys):
        """把改动 key 列表广播给插件（守卫模式：ctx/信号缺失不抛异常）"""
        ctx = getattr(self._lp, "ctx", None)
        sig = getattr(ctx, "settings_changed", None)
        emit = getattr(sig, "emit", None) if sig is not None else None
        if not callable(emit):
            return
        try:
            emit(list(keys))
        except Exception:                 # noqa: BLE001 - 通知失败不阻断保存
            pass


def build_plugin_settings_dialog(host, lp, data_dir_base=None):
    """构建插件设置弹层（构建与弹出分离，离屏测试可直达构建）。

    返回 ``None`` 的情形：无 plugin_id / manifest 未声明任何 settings
    条目——调用方（卡片）本就不该在这类插件上出现设置按钮，这里是
    双保险。``data_dir_base`` 供测试注入临时插件数据目录；生产调用不传，
    存储层按运行环境解析 ``<float_data>/plugins``。
    """
    plugin_id = getattr(lp, "plugin_id", "") or ""
    manifest = getattr(lp, "manifest", {}) or {}
    has_settings = any(isinstance(e, dict)
                       for e in (manifest.get("settings") or []))
    if not plugin_id or not has_settings:
        return None
    return PluginSettingsDialog(host, lp, data_dir_base=data_dir_base)
