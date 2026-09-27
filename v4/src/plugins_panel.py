# -*- coding: utf-8 -*-
"""
====================================================================
插件中心面板  -  PluginsPanel
====================================================================
主窗口第 9 页：集中展示已安装的悬浮球外置插件、使用说明与加载状态。

职责：
  1. 列出 PluginLoader **成功加载**的插件：名称 / 版本 / id / 描述 /
     状态标签 / 动作清单（标题 + 热键 + 是否进右键菜单）/ 动作级告警 / 依赖
  2. 列出**加载失败**的插件：失败阶段 + 原因 + 修复建议 + 打开目录
     （解决「插件装死了用户不知道」——此前失败项不进任何列表）
  3. 每个插件提供「打开目录」「查看使用说明」，以及启用/停用开关
     （调用此前全项目无 UI 入口的 ActionRegistry.set_enabled）
  4. 顶部「重新扫描」按钮：无需重启即可加载新放入的插件
  5. 插件总闸关闭时显示提示条；无插件且无失败时显示安装引导空态

数据来源：host.plugin_loader（MainWindow 持有，knowledge_ball 注入）。
插件描述优先级：manifest.description > 插件类 docstring 首行。

设计约束：
  - UI 层不直接碰插件目录，只经 LoadedPlugin / FailedPlugin 公开字段读取
  - 启停开关经 loader.registry.set_enabled 生效；注册表不可用时降级为只读展示
  - 打开目录用 os.startfile；使用说明优先程序内 Markdown 渲染
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
        self._empty_label = None
        self._gate_label = None
        self._count_label = None
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
        hint = QLabel("💡 把插件包放进 plugins/ 文件夹（与程序同级的插件安装目录），"
                      "点「重新扫描」即可加载，无需重启；"
                      "插件包内建议放一份「使用说明.md」，摘要会自动显示在卡片上")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 插件安装目录（绝对路径，便于用户直接核对/复制）----
        self._dir_label = QLabel()
        self._dir_label.setObjectName("pluginDirLabel")
        self._dir_label.setWordWrap(True)
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._dir_label)

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

        self._rescan_btn = QPushButton("🔄 重新扫描")
        self._rescan_btn.setObjectName("secondaryBtn")
        self._rescan_btn.setToolTip(
            "重新扫描 plugins/ 目录并加载新放入的插件（不必重启程序）")
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
            "把插件包（含 manifest.json 的文件夹或 .fpplug 压缩包）\n"
            "放进 plugins/ 目录（与程序同级的插件安装目录），"
            "点上方「重新扫描」即可加载\n\n"
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

        # 总闸提示条
        self._gate_label.setVisible(not gate_on)

        # 安装目录（绝对路径）——用户最常搞混「插件装在哪」，
        # 直接写出来比「与程序同级」这类相对说法可核对得多。
        self._dir_label.setText("📂 插件安装目录：%s" % self._plugins_dir_text(loader))

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

        # 失败区
        has_errors = bool(errors)
        self._error_box.setVisible(has_errors)
        if has_errors:
            self._error_label.setText(
                f"⚠ 加载失败的插件（{len(errors)} 个）——见下方原因与修复建议")
            for fe in errors:
                self._error_layout.addWidget(self._make_error_card(fe))

        has_plugins = bool(plugins)
        self._empty_label.setVisible(not has_plugins and not has_errors)
        cnt = f"共 {len(plugins)} 个插件"
        if has_errors:
            cnt += f"，{len(errors)} 个加载失败"
        self._count_label.setText(cnt)

        for lp in plugins:
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, self._make_card(lp))

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

        # ---- 依赖 + 操作按钮行 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        requires = list(manifest.get("requires", []) or [])
        if requires:
            req = QLabel("依赖: " + "、".join(requires))
            req.setObjectName("pluginCardId")
            bottom.addWidget(req)
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
        v.addLayout(bottom)
        return card

    def _make_error_card(self, fe) -> QWidget:
        """加载失败的插件卡片：名称 + 失败阶段 + 原因 + 修复建议 + 打开目录"""
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
        点击后逐个调用 ActionRegistry.set_enabled 并通知宿主动作表重建。
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
        btn.setToolTip("停用后不挂右键菜单、不绑热键（不卸载插件模块），可随时启用")
        btn.clicked.connect(
            lambda _checked=False, s=self, aa=actions,
            now=on: s._toggle_plugin(aa, not now))
        return btn

    def _toggle_plugin(self, actions, on: bool):
        """把该插件的全部动作统一切换到 on 状态"""
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
        self._notify_menu_rebuild()
        self.refresh()

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
