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
  6. **插件商店区**（2026-09-27 晚）：列出商店目录里的 ``*.fpplug`` 可安装包，
     每个包一个「安装」按钮；已装好的包标记「已安装」且按钮禁用

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
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextBrowser, QMessageBox,
)

# 插件包内使用说明文件约定（按优先级探测）
USAGE_FILENAMES = ("使用说明.md", "README.md")
# 卡片上显示的使用说明摘要最大长度（用户要求：简短一两句话）
USAGE_SUMMARY_MAX = 90


def find_usage_file(plugin_dir: str) -> str:
    """探测插件包内的使用说明文件，返回完整路径（找不到返回空串）"""
    for name in USAGE_FILENAMES:
        path = os.path.join(plugin_dir or "", name)
        if os.path.isfile(path):
            return path
    return ""


def usage_summary(path: str, max_len: int = USAGE_SUMMARY_MAX) -> str:
    """从使用说明 md 提取一句话摘要（卡片展示用）。

    规则：跳过 # 标题行 / 表格 / 空行 / 列表符号，取第一条正文文字，
    超长截断加省略号；读失败返回空串（不阻塞渲染）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                if text.startswith("#"):
                    continue
                if text.startswith("|"):
                    continue                       # markdown 表格行
                # 去掉常见 markdown 修饰：列表符 / 引用符 / 加粗星号
                text = text.lstrip("-*> ").strip()
                if not text:
                    continue
                text = text.replace("**", "").replace("`", "")
                if len(text) > max_len:
                    text = text[:max_len].rstrip() + "…"
                return text
    except (OSError, UnicodeDecodeError):
        pass
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
        ("📁 用系统程序打开", "secondaryBtn",
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
        self._store_layout = None
        self._store_box = None
        self._store_label = None
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
        title = QLabel("🔌 插件中心")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self._count_label = QLabel("共 0 个插件")
        self._count_label.setObjectName("hintLabel")
        header.addWidget(self._count_label)
        v.addLayout(header)

        # ---- 提示 ----
        hint = QLabel("💡 插件商店放插件包（.fpplug / 含 manifest.json 的文件夹），"
                      "点「安装」解压到安装目录后即可使用；"
                      "「卸载」只删安装目录里的副本，商店里的源包会保留，随时能再装。"
                      "插件包内建议放一份「使用说明.md」，摘要会自动显示在卡片上")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 两个目录的绝对路径（用户最常搞混「装在哪 / 包放哪」）----
        self._dir_label = QLabel()
        self._dir_label.setObjectName("pluginDirLabel")
        self._dir_label.setWordWrap(True)
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._dir_label)

        self._store_dir_label = QLabel()
        self._store_dir_label.setObjectName("pluginDirLabel")
        self._store_dir_label.setWordWrap(True)
        self._store_dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._store_dir_label)

        # ---- 插件总闸关闭提示条（默认隐藏）----
        self._gate_label = QLabel("⛔ 插件功能已在设置页停用（全局开关），"
                                  "如需使用请到「设置」开启后重启程序")
        self._gate_label.setObjectName("pluginGateHint")
        self._gate_label.setWordWrap(True)
        self._gate_label.setVisible(False)
        v.addWidget(self._gate_label)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        open_dir_btn = QPushButton("📁 打开插件目录")
        open_dir_btn.setObjectName("secondaryBtn")
        open_dir_btn.clicked.connect(self._on_open_plugins_dir)
        toolbar.addWidget(open_dir_btn)

        open_store_btn = QPushButton("🏪 打开插件商店")
        open_store_btn.setObjectName("secondaryBtn")
        open_store_btn.setToolTip("打开插件商店目录（放 .fpplug 插件包的地方）")
        open_store_btn.clicked.connect(self._on_open_store_dir)
        toolbar.addWidget(open_store_btn)

        self._rescan_btn = QPushButton("🔄 重新扫描")
        self._rescan_btn.setObjectName("secondaryBtn")
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
        self._error_label = QLabel("⚠ 加载失败的插件")
        self._error_label.setObjectName("pluginSectionLabel")
        eb.addWidget(self._error_label)
        self._error_layout = QVBoxLayout()
        self._error_layout.setContentsMargins(0, 0, 0, 0)
        self._error_layout.setSpacing(8)
        eb.addLayout(self._error_layout)
        self._error_box.setVisible(False)
        v.addWidget(self._error_box)

        # ---- 插件商店区（默认隐藏；商店里没有可用包时不显示）----
        self._store_box = QFrame()
        self._store_box.setObjectName("pluginStoreBox")
        sb = QVBoxLayout(self._store_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)
        self._store_label = QLabel("🏪 可安装的插件")
        self._store_label.setObjectName("pluginSectionLabel")
        sb.addWidget(self._store_label)
        self._store_layout = QVBoxLayout()
        self._store_layout.setContentsMargins(0, 0, 0, 0)
        self._store_layout.setSpacing(8)
        sb.addLayout(self._store_layout)
        self._store_box.setVisible(False)
        v.addWidget(self._store_box)

        # ---- 滚动区：插件卡片列表 ----
        scroll = QScrollArea()
        scroll.setObjectName("pluginsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("pluginsContainer")
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(0, 0, 8, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()          # 卡片永远顶对齐
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        # ---- 空态提示（默认显示，refresh 时按有无插件切换）----
        self._empty_label = QLabel(
            "还没有安装任何插件\n\n"
            "把插件包（.fpplug 压缩包，或含 manifest.json 的文件夹）\n"
            "放进插件商店目录，回到本页点「安装」即可\n\n"
            "也可以直接放到插件安装目录后点「重新扫描」\n\n"
            "插件包内建议放一份「使用说明.md」，卡片会自动显示其中的一句话摘要")
        self._empty_label.setObjectName("pluginEmptyHint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("padding: 40px;")
        self._empty_label.setWordWrap(True)
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
        self._dir_label.setText(
            "📂 插件安装目录：%s" % self._plugins_dir_text(loader))
        self._store_dir_label.setText(
            "🏪 插件商店目录：%s" % self._store_dir_text(loader))

        # 清空旧卡片（保留末尾 stretch）
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 清空旧失败卡片
        while self._error_layout.count():
            item = self._error_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 清空旧商店卡片
        while self._store_layout.count():
            item = self._store_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 失败区
        has_errors = bool(errors)
        self._error_box.setVisible(has_errors)
        if has_errors:
            self._error_label.setText(
                f"⚠ 加载失败的插件（{len(errors)} 个）——见下方原因与修复建议")
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

        for lp in plugins:
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, self._make_card(lp))

        # 商店区：只列「可用」的包（manifest 合法的），坏包不在这里刷屏——
        # 它们的 error 会由 _make_store_card 以提示形式展示，但为确保列表
        # 不因一个坏包变噪音，这里仍把坏包一并列出并标出原因（用户需要知道）。
        entries = [e for e in store if self._entry_visible(e)]
        self._store_box.setVisible(bool(entries))
        if entries:
            n_inst = sum(1 for e in entries if getattr(e, "installed", False))
            extra = f"，{n_inst} 个已安装" if n_inst else ""
            self._store_label.setText(
                f"🏪 可安装的插件（{len(entries)} 个{extra}）"
                f"——点「安装」解压到插件安装目录")
            for e in entries:
                self._store_layout.addWidget(self._make_store_card(e))

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
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        manifest = getattr(lp, "manifest", {}) or {}

        # ---- 标题行：名称 + 版本 + 状态标签 + id ----
        head = QHBoxLayout()
        name = QLabel(f"{lp.name}  v{lp.version}")
        name.setObjectName("pluginCardTitle")
        head.addWidget(name)

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
        desc = (manifest.get("description") or "").strip()
        if not desc:
            doc = getattr(type(lp.plugin), "__doc__", "") or ""
            desc = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if desc:
            desc_label = QLabel(desc)
            desc_label.setObjectName("pluginCardDesc")
            desc_label.setWordWrap(True)
            v.addWidget(desc_label)

        # ---- 使用说明摘要行：来自插件包内 使用说明.md / README.md ----
        usage_path = find_usage_file(lp.path)
        if usage_path:
            summary = usage_summary(usage_path)
            if summary:
                usage_label = QLabel(f"📖 {summary}")
                usage_label.setObjectName("pluginCardUsage")
                usage_label.setWordWrap(True)
                usage_label.setToolTip("摘自插件包内使用说明，点下方按钮查看全文")
                v.addWidget(usage_label)

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
            no_act = QLabel("·  （无注册动作）")
            no_act.setObjectName("pluginCardDesc")
            v.addWidget(no_act)

        # ---- 依赖 + 能力 + 操作按钮行 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        requires = list(manifest.get("requires", []) or [])
        if requires:
            req = QLabel("依赖: " + "、".join(requires))
            req.setObjectName("pluginCardId")
            bottom.addWidget(req)
        caps = list(manifest.get("capabilities", []) or [])
        if caps:
            # 能力声明可视化（2026-09-27 权限模型）：network / write。
            # 让用户看到「这个插件会联网 / 能往你的数据里写东西」，
            # 是声明式权限的最小可见性。
            cap_labels = {"network": "🌐 网络访问", "write": "✍ 写入数据"}
            cap_tips = {
                "network": "该插件在 manifest 里声明了 network 能力，"
                           "可经宿主网络桥发起联网请求（app.log 可审计）",
                "write": "该插件在 manifest 里声明了 write 能力，"
                         "可经宿主桥新增碎片 / 任务 / 笔记（只能新增，"
                         "不能修改或删除已有数据）",
            }
            cap = QLabel("能力: " + "、".join(
                cap_labels.get(c, c) for c in caps))
            cap.setObjectName("pluginCardId")
            cap.setToolTip("；\n".join(cap_tips.get(c, "") for c in caps).strip("；\n"))
            bottom.addWidget(cap)
        bottom.addStretch()

        toggle_btn = self._make_toggle_btn(lp, actions)
        if toggle_btn is not None:
            bottom.addWidget(toggle_btn)

        open_btn = QPushButton("📁 打开目录")
        open_btn.setObjectName("secondaryBtn")
        open_btn.clicked.connect(
            lambda _checked=False, p=lp.path: self._on_open_dir(p))
        bottom.addWidget(open_btn)

        readme = find_usage_file(lp.path)
        if readme:
            readme_btn = QPushButton("📄 查看使用说明")
            readme_btn.setObjectName("secondaryBtn")
            readme_btn.clicked.connect(
                lambda _checked=False, p=readme: self._on_open_file(p))
            bottom.addWidget(readme_btn)

        uninstall_btn = QPushButton("🗑 卸载")
        uninstall_btn.setObjectName("secondaryBtn")
        uninstall_btn.setToolTip("删除插件目录并摘掉它的动作 / 热键 / 菜单项；"
                                 "插件私有数据（float_data/plugins/）会保留")
        uninstall_btn.clicked.connect(
            lambda _checked=False, p=lp.plugin_id,
            n=getattr(lp, "name", "") or lp.plugin_id,
            d=lp.path: self._on_uninstall(p, n, d))
        bottom.addWidget(uninstall_btn)

        v.addLayout(bottom)
        return card

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

        # ---- 标题行：名称 + 版本 + 状态标签 + id ----
        head = QHBoxLayout()
        title_text = getattr(entry, "name", "") or getattr(entry, "filename", "")
        version = getattr(entry, "version", "") or ""
        name = QLabel(f"{title_text}  v{version}" if version else title_text)
        name.setObjectName("pluginStoreTitle")
        head.addWidget(name)

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

        # ---- 底部：能力提示 + 源包文件名 + 操作按钮 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        caps = list((getattr(entry, "manifest", None) or {}).get(
            "capabilities", []) or [])
        if caps:
            cap_labels = {"network": "🌐 网络访问", "write": "✍ 写入数据"}
            cap = QLabel("能力: " + "、".join(cap_labels.get(c, c) for c in caps))
            cap.setObjectName("pluginCardId")
            bottom.addWidget(cap)
        fname = getattr(entry, "filename", "")
        if fname:
            fn = QLabel(f"包: {fname}")
            fn.setObjectName("pluginCardId")
            bottom.addWidget(fn)
        bottom.addStretch()

        install_btn = QPushButton("⬇ 安装")
        install_btn.setObjectName("primaryBtn")
        install_btn.setToolTip(
            "把该插件包解压到插件安装目录并加载；源包保留在商店目录，"
            "卸载后仍可再装")
        if not usable or installed:
            install_btn.setEnabled(False)
            if installed:
                install_btn.setText("✓ 已安装")
        else:
            install_btn.clicked.connect(
                lambda _checked=False, p=plugin_id,
                n=title_text: self._on_install(p, n))
        bottom.addWidget(install_btn)

        open_btn = QPushButton("📁 打开目录")
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
            open_btn = QPushButton("📁 打开目录")
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
        btn = QPushButton("⏸ 停用" if on else "▶ 启用")
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
            QMessageBox.information(self, "已安装",
                                    f"{msg}\n\n插件已加载，可直接使用。")
        else:
            QMessageBox.warning(self, "安装失败", msg)

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
            QMessageBox.information(self, "已卸载", msg)
        else:
            QMessageBox.warning(self, "卸载失败", msg)
