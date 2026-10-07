# -*- coding: utf-8 -*-
"""CardWindow 页面家族 mixin（D2 上帝类拆分，拆分计划-wiring-2026-10-05 T03）。

从 ``src/card_window.py`` 外迁两组页面机器：

- :class:`TaskNotePagesMixin` —— 日程任务页（输入 / 分组列表 / 行内勾选
  动画 / 右键管理 / 编辑对话框）与临时笔记页（800ms 防抖自动保存）；
- :class:`NavAppAssetPagesMixin` —— 网址导航页（双列站点卡）、软件导航页
  （图标网格只读启动）、临时素材页（缩略图网格 + 拖拽取出 + 右键管理），
  以及素材项部件 ``_AssetItemWidget`` 与 ``_domain_of_url`` 纯函数。

**mixin 铁律**：无 pyqtSignal、无 __init__、只经 self 消费宿主状态、
禁止 import 宿主模块 ``src.card_window``（信号 / 窗口核心 / 公开门面
current_mode·reset_to_home·refresh_page·mini_icon_size 均留在主类）。
``_TAB_BAR_WIDTH`` 宿主私有常量经主类类属性透传（self._TAB_BAR_WIDTH，
同一对象）；``_domain_of_url`` 改由本模块持有，宿主 re-export 兼容
tools/verify_card_nav_grid.py 的历史导入路径。素材项右键删除改为鸭子类型
判定顶层窗口的 ``_delete_asset`` 入口（原 isinstance 宿主主类检查）。
"""

import os
from collections.abc import Callable
from datetime import date as _date
from typing import Any
from urllib.parse import urlparse

from PyQt6.QtCore import (
    Qt, QTimer, QDate, QPoint, QSize, QVariantAnimation,
    QEasingCurve, QUrl, QMimeData, pyqtSignal,
)
from PyQt6.QtGui import (QDesktopServices, QDrag, QImageReader,
                         QPixmap, QFontMetrics, QFont, QIcon)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QMenu, QToolButton,
    QListWidget, QListWidgetItem, QDateEdit,
    QDialog, QFormLayout, QTextEdit, QScrollArea,
    QFrame, QSizePolicy, QGridLayout,
)

from src.glass_dialog import make_dialog_buttons
from src.task_manager import (
    task_state, format_relative_deadline, format_completed_date,
    group_title, KIND_ROW, KIND_HEADER, GROUP_OVERDUE, STATE_OVERDUE, STATE_NONE,
)
from src.task_delegate import (
    TaskItemDelegate, TaskListWidget,
    KIND_ROLE, ROLE_TITLE, ROLE_REL, ROLE_STATE, ROLE_DONE,
)
from src.controls import (
    tune_list_scrolling, SmoothButton, EmptyState,
    SmoothInput,
)
from src.theme import get_menu_qss, get_colors
from src.icon_render import icon as render_icon
from src.assets_panel import EXT_ICON
from src.date_picker import DateField
from src.constants import (
    CHECK_ANIM_MS, DEFAULT_THEME, mini_btn_size,
)
from src import motion


def _domain_of_url(url: str) -> str:
    """从 URL 提取展示用域名：去 scheme、去 www.、去端口/路径。
    解析失败或无 host 时回退为去掉 scheme 的原始串。
    """
    raw = (url or "").strip()
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        host = ""
    host = host.strip()
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host
    # 回退：手动去掉 scheme:// 后取路径首段
    stripped = raw.split("://", 1)[-1]
    return stripped.split("/", 1)[0] or raw


# ====================================================================
# 素材项组件：支持拖拽出去（拖拽时携带文件路径）
# ====================================================================
class _AssetItemWidget(QWidget):
    """单个素材项，显示缩略图/图标 + 文件名，支持拖拽取出

    2026-10-08（清单 A2/B2/C2/E4 第二批）：
    - objectName ``assetItem``：QSS 三态（hover / :focus / [pressed]）
      挂在 theme.py 卡片窗模板，机制对照 fragItemRow（WA_StyledBackground
      + WA_Hover）——此前素材格连 hover 都没有；
    - 键盘可达：StrongFocus + Enter/Space → 打开（与双击同一通道，
      _on_double_click）；失效文件不响应；
    - 按下态：QFrame 式属性驱动（``pressed`` 属性 + repolish），
      拖拽判定位移阈值（manhattanLength ≥ 10）不受影响；
    - 失效文件名颜色改走主题 token（text_disabled），不再写死 #999。
    """

    DOUBLE_CLICK_THRESHOLD = 300  # 双击判定时间（毫秒）

    def __init__(self, asset, parent=None, theme: str = DEFAULT_THEME,
                 thumb_cache: dict | None = None):
        super().__init__(parent)
        self._asset = asset
        self._theme = theme
        self._thumb_cache = thumb_cache if thumb_cache is not None else {}
        self._drag_start = None
        self._last_click_time = 0
        self._is_valid = os.path.exists(asset.stored_path) if asset else False

        self.setObjectName("assetItem")
        self.setFixedSize(72, 84)
        # QSS 的 background-color / border 要生效，自定义 QWidget 必须
        # 显式打开样式背景；hover 态（:hover）需要 WA_Hover 驱动重绘
        # （写法对照 card_window.fragItemRow，2026-10-08 E4）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._is_valid
                       else Qt.CursorShape.ForbiddenCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(self._build_tooltip())

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 4, 2, 2)
        v.setSpacing(2)

        # 图标/缩略图区域
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(64, 64)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setObjectName("assetIcon")
        self._load_icon()
        v.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # 文件名
        name = asset.original_name if asset else ""
        if len(name) > 10:
            name = name[:9] + "…"
        self._name_label = QLabel(name)
        self._name_label.setObjectName("assetName")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(False)
        v.addWidget(self._name_label)

        if not self._is_valid:
            # E4：失效文件名颜色走主题 token（此前写死 #999，深色主题
            # 下突兀）；主题切换走重建路径（_apply_style），取色即所见
            self._name_label.setStyleSheet(
                "color: %s;" % get_colors(self._theme).get("text_disabled",
                                                           "#999999"))

    def _build_tooltip(self):
        if not self._asset:
            return ""
        tip = f"{self._asset.original_name}\n"
        tip += f"大小: {self._asset.size_display()}\n"
        tip += f"收录: {self._asset.added_time}\n"
        tip += "拖拽取出 | 双击打开 | 右键菜单"
        if not self._is_valid:
            tip += "\n文件已失效"
        return tip

    def _load_icon(self):
        """加载缩略图 / 类型图标。

        UI 重构 03：三处 emoji 占位全部换成 01 包自绘图标 ——
          · 失效文件       → ``warning``（danger 色）
          · 非图片文件     → 按扩展名查 :data:`EXT_ICON`（**唯一来源**：
                             ``assets_panel.EXT_ICON``，本包只 import 不另立一份，
                             缺省 ``file_generic``）
          · 图片但解码失败 → ``image``（占位色）

        图片缩略图走缓存 + 解码期缩放，大图不卡。
        """
        if not self._is_valid:
            self._show_type_icon("warning", "danger")
            return
        if not self._asset.is_image:
            ext = os.path.splitext(self._asset.original_name)[1].lower()
            self._show_type_icon(EXT_ICON.get(ext, "file_generic"),
                                 "text_secondary")
            return

        aid = self._asset.asset_id
        cached = self._thumb_cache.get(aid)
        if cached is None:
            # 缓存未命中：QImageReader 解码期先缩到 2x 目标尺寸，
            # 避免整图载入内存（截图 PNG 可达数 MB）
            reader = QImageReader(self._asset.stored_path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and (size.width() > 128 or size.height() > 128):
                scale = 128 / max(size.width(), size.height())
                reader.setScaledSize(QSize(int(size.width() * scale),
                                           int(size.height() * scale)))
            img = reader.read()
            if not img.isNull():
                pix = QPixmap.fromImage(img).scaled(
                    64, 64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                cached = pix
            else:
                cached = False
            self._thumb_cache[aid] = cached
        if cached is not False:
            self._icon_label.setPixmap(cached)
        else:
            self._show_type_icon("image", "text_placeholder")

    def _show_type_icon(self, icon_name: str, token: str):
        """把类型自绘图标画进 64×64 的缩略图位（36px 居中）。

        ``token`` 为主题色名（如 ``text_secondary``），主题切换时由
        :meth:`_apply_style` 走重建路径重新取色。
        """
        color = get_colors(self._theme).get(token, "#000000")
        self._icon_label.setPixmap(
            render_icon(icon_name, 36, color).pixmap(36, 36))

    # ---- 键盘与按下态（A2/C2）----
    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        """Enter / Space → 打开文件（与双击同一通道）；失效文件不响应。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                           Qt.Key.Key_Space):
            if self._is_valid:
                self._on_double_click()
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_pressed(self, on: bool) -> None:
        """属性驱动的按下态（QWidget 不吃 QSS :pressed）。"""
        self.setProperty("pressed", "true" if on else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
        if event.button() == Qt.MouseButton.LeftButton and self._is_valid:
            self._drag_start = event.pos()
        # 双击检测
        if event.button() == Qt.MouseButton.LeftButton:
            import time
            now = int(time.time() * 1000)
            if now - self._last_click_time < self.DOUBLE_CLICK_THRESHOLD:
                self._on_double_click()
                self._last_click_time = 0
            else:
                self._last_click_time = now
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start is None or not self._is_valid:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 10:
            return
        # 构造拖拽
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._asset.stored_path)])
        mime.setText(self._asset.stored_path)
        drag.setMimeData(mime)
        # 图片设置拖拽预览
        if self._asset.is_image:
            preview = QPixmap(self._asset.stored_path)
            if not preview.isNull():
                drag.setPixmap(preview.scaled(
                    80, 80,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))
                drag.setHotSpot(QPoint(40, 40))
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = None
        # 拖拽 exec 吞掉 release：就地收掉按下态，避免高亮残留
        self._set_pressed(False)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(False)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt 命名)
        """按住拖出素材格时收掉按下态（避免残留高亮）。"""
        if self.property("pressed") == "true":
            self._set_pressed(False)
        super().leaveEvent(event)

    def _on_double_click(self):
        """双击用系统默认程序打开"""
        if self._is_valid:
            try:
                os.startfile(self._asset.stored_path)
            except Exception:
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(self._asset.stored_path))
                except Exception:
                    pass

    def contextMenuEvent(self, event):
        """右键菜单"""
        if not self._asset:
            return
        menu = QMenu(self)
        # 跟随当前主题（原先硬编码 light，深色主题下会弹出白底菜单）
        menu.setStyleSheet(get_menu_qss(self._theme))

        act_open = menu.addAction("打开")
        act_copy = menu.addAction("复制路径")
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        # 删除项危险语义：Qt 菜单无法按 action 单独设文字色，
        # 用 danger 色的自绘 trash 图标承载（UI 重构 03）
        act_delete.setIcon(
            render_icon("trash", 14, get_colors(self._theme)["danger"]))

        action = menu.exec(event.globalPos())
        if action == act_open:
            self._on_double_click()
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            cb.setText(self._asset.stored_path)
        elif action == act_delete:
            # 顶层窗口处理数据层删除
            top = self.window()
            # mixin 化后不再 isinstance 宿主主类（禁 import 宿主模块）：
            # 鸭子类型判定顶层窗口具备删除入口（实际即宿主主类）
            delete = getattr(top, "_delete_asset", None)
            if callable(delete):
                delete(self._asset.asset_id)


# ====================================================================
# 卡片弹窗类
# ====================================================================


class TaskNotePagesMixin:
    """日程任务页 + 临时笔记页（构建与交互；铁律见模块 docstring）。"""

    # ---- 宿主（CardWindow）依赖面裸注解：仅类型声明，不建类属性、
    # ---- 零运行时行为、MRO 零变化（pyright reportAttributeAccessIssue 消音）。
    _theme: str
    _task_manager: Any
    _note_manager: Any
    _config_manager: Any
    _menu: QMenu
    _note_save_timer: QTimer
    data_changed: pyqtSignal

    # ==================================================================
    # 日程任务页面
    # ==================================================================
    def _build_task_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self._task_title_input = SmoothInput()
        self._task_title_input.setObjectName("taskInput")
        self._task_title_input.setPlaceholderText("输入任务标题，回车添加...")
        self._task_title_input.returnPressed.connect(self._on_add_task)

        # 2026-10-02：与主窗任务页同一套自绘日历弹层（src/date_picker.py）。
        # 主题显式传入 —— CardWindow 只有私有 _theme，没有 current_theme
        # 可读，构造期就必须给对。
        self._task_deadline = DateField(theme=self._theme, parent=self,
                                        object_name="taskDate")
        self._task_deadline.setFixedWidth(120)

        self._task_add_btn = SmoothButton("添加")
        self._task_add_btn.setObjectName("taskAddBtn")
        self._task_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._task_add_btn.clicked.connect(self._on_add_task)

        input_bar.addWidget(self._task_title_input, 1)
        input_bar.addWidget(self._task_deadline)
        input_bar.addWidget(self._task_add_btn)
        v.addLayout(input_bar)

        # 2026-10-08（清单 A3）：TaskListWidget —— Space/Enter 勾选、
        # Shift+F10/Menu 弹右键菜单（与主窗任务页共用同一实现）
        self._task_list = TaskListWidget()
        tune_list_scrolling(self._task_list)  # 丝滑化清单 L3：像素级滚动 + 统一步长
        self._task_list.setObjectName("taskList")
        # E1（2026-10-08）：与主窗任务页同款多选（批量操作语义对齐）
        self._task_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self._task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_list.customContextMenuRequested.connect(self._on_task_context_menu)
        # 自定义行渲染委托（与主窗口任务页共用同一实现）
        self._task_delegate = TaskItemDelegate(get_colors(self._theme),
                                               self._task_list)
        self._task_delegate.toggle_requested.connect(self._on_task_toggle_requested)
        self._task_list.setItemDelegate(self._task_delegate)
        v.addWidget(self._task_list, 1)

        # E1：空态引导与主窗同款（A3 通用化）——覆盖层叠在列表上，列表本体
        # 不动（itemAt/count 断言零影响）；由 _refresh_task_list 按数据有无显隐
        self._task_empty = EmptyState(
            "tasks", "还没有任务", "在上方输入待办回车添加")
        self._task_empty.attach_to(self._task_list)

        hint = QLabel("点击勾选框完成 | 右键任务：编辑 / 删除")
        hint.setObjectName("hintLabel")
        v.addWidget(hint)

        # 勾选动画 + 延时重建定时器
        self._task_anim = QVariantAnimation(self)
        self._task_anim.setStartValue(0.0)
        self._task_anim.setEndValue(1.0)
        self._task_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._task_anim.valueChanged.connect(self._on_task_anim_tick)
        self._task_anim.finished.connect(self._on_task_anim_finished)

        self._task_rebuild_timer = QTimer(self)
        self._task_rebuild_timer.setSingleShot(True)
        self._task_rebuild_timer.timeout.connect(self._refresh_task_list)
        return page

    # ==================================================================
    # 临时笔记页面
    # ==================================================================
    def _build_note_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        self._note_edit = QTextEdit()
        self._note_edit.setObjectName("noteEdit")
        self._note_edit.setPlaceholderText("临时笔记：随手记录，自动保存...")
        self._note_edit.textChanged.connect(self._on_note_text_changed)
        v.addWidget(self._note_edit, 1)
        return page


    # ---------------- 日程任务 ----------------
    def _on_add_task(self):
        if not self._task_manager:
            return
        title = self._task_title_input.text().strip()
        if not title:
            return
        d = self._task_deadline.dateOrNone()
        self._task_manager.add_task(title, "", d.toString("yyyy-MM-dd") if d else "")
        self._task_title_input.clear()
        self._refresh_task_list()
        self.data_changed.emit("task")

    def _refresh_task_list(self):
        """按分组重建卡片任务列表（与主窗口同口径、同渲染）。"""
        self._task_list.clear()
        if not self._task_manager:
            # E1：无管理器（异常路径）同样视为空列表 → 空态可见
            self._task_empty.setVisible(True)
            self._task_empty.setGeometry(self._task_list.rect())
            return
        self._task_delegate.set_colors(get_colors(self._theme))
        self._task_delegate.clear_progress_except(self._task_anim_task_id)

        today = _date.today().isoformat()
        for group_key, tasks in self._task_manager.get_tasks_grouped(today):
            # 组标题与主窗口同口径：标签 + 独立计数/逾期组角色
            # （2026-10-02 高仿真稿，见 TaskItemDelegate._paint_header）
            header_item = QListWidgetItem(group_title(group_key, today))
            header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header_item.setData(KIND_ROLE, KIND_HEADER)
            header_item.setData(ROLE_REL, str(len(tasks)))
            header_item.setData(
                ROLE_STATE,
                STATE_OVERDUE if group_key == GROUP_OVERDUE else STATE_NONE)
            self._task_list.addItem(header_item)
            for t in tasks:
                state, _delta = task_state(t.deadline, today)
                item = QListWidgetItem("")
                item.setData(Qt.ItemDataRole.UserRole, t.task_id)
                item.setData(KIND_ROLE, KIND_ROW)
                item.setData(ROLE_TITLE, t.title)
                # 已完成 → 行尾显示完成日期（不显示逾期等截止状态）
                item.setData(ROLE_REL,
                              format_completed_date(t.completed_at) if t.done
                              else format_relative_deadline(t.deadline, today))
                item.setData(ROLE_STATE, state)
                item.setData(ROLE_DONE, bool(t.done))
                self._task_list.addItem(item)

        # E1：空态显隐与主窗 tasks_panel 同口径（覆盖层贴合列表几何）
        empty = self._task_list.count() == 0
        self._task_empty.setVisible(empty)
        if empty:
            self._task_empty.setGeometry(self._task_list.rect())
            self._task_empty.raise_()

    # ---- 卡片任务：行内勾选 + 动画 + 撤销 ----
    def _task_anim_speed(self) -> float:
        """读取动画速度档位（与小卡片宿主 config 一致），异常回退 1.0。"""
        try:
            speed = float(self._config_manager.get("anim_speed", 1.0)) \
                if self._config_manager else 1.0
        except (TypeError, ValueError, AttributeError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    def _on_task_toggle_requested(self, task_id: int):
        """单击勾选框 / 标题 → 切换完成态并播放动画。"""
        if not self._task_manager:
            return
        task = self._task_manager.get_task(task_id)
        if task is None:
            return
        prev_done = bool(task.done)
        new_done = not prev_done
        if not self._task_manager.set_done(task_id, new_done):
            return

        if self._task_anim_task_id is not None \
                and self._task_anim_task_id != task_id:
            self._task_delegate.set_check_progress(
                self._task_anim_task_id, float(self._task_anim.endValue()))

        self._task_anim.stop()
        self._task_rebuild_timer.stop()
        self._task_anim_task_id = task_id
        start = 0.0 if new_done else 1.0
        end = 1.0 if new_done else 0.0
        self._task_delegate.set_check_progress(task_id, start)
        # 时长口径统一走 src.motion（UI 强化方案 A1），此处只保留「至少 1ms」
        duration = max(1, motion.duration(CHECK_ANIM_MS, self._task_anim_speed()))
        self._task_anim.setDuration(duration)
        self._task_anim.setStartValue(start)
        self._task_anim.setEndValue(end)
        self._task_list.viewport().update()
        self._task_anim.start()

        self.data_changed.emit("task")

    def _on_task_anim_tick(self, value):
        if self._task_anim_task_id is None:
            return
        self._task_delegate.set_check_progress(self._task_anim_task_id, float(value))
        self._task_list.viewport().update()

    def _on_task_anim_finished(self):
        self._task_anim_task_id = None
        self._task_rebuild_timer.start(250)

    def _stop_task_animations(self):
        """停止动画、清空进度（非动画路径的数据变更）。"""
        if self._task_anim is not None:
            self._task_anim.stop()
        if self._task_rebuild_timer is not None:
            self._task_rebuild_timer.stop()
        self._task_anim_task_id = None
        if getattr(self, "_task_delegate", None) is not None:
            self._task_delegate.clear_progress_except(None)

    def _on_task_context_menu(self, pos):
        item = self._task_list.itemAt(pos)
        if not item or not self._task_manager:
            return
        # 组标题行跳过
        if item.data(KIND_ROLE) != KIND_ROW:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return
        task = self._task_manager.get_task(task_id)
        if not task:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._menu.styleSheet())
        act_toggle = menu.addAction("取消完成" if task.done else "标记完成")
        act_edit = menu.addAction("编辑...")
        menu.addSeparator()
        act_delete = menu.addAction("删除")

        action = menu.exec(self._task_list.mapToGlobal(pos))
        if action == act_toggle:
            self._stop_task_animations()
            self._task_manager.set_done(task_id, not task.done)
            self._refresh_task_list()
            self.data_changed.emit("task")
        elif action == act_edit:
            self._edit_task(task)
        elif action == act_delete:
            self._stop_task_animations()
            self._task_manager.delete_task(task_id)
            self._refresh_task_list()
            self.data_changed.emit("task")

    def _edit_task(self, task):
        if not self._task_manager:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑任务")
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setFixedSize(320, 200)

        form = QFormLayout(dialog)
        form.setContentsMargins(20, 20, 20, 16)
        form.setSpacing(10)

        title_edit = SmoothInput(task.title)
        note_edit = SmoothInput(task.note)
        note_edit.setPlaceholderText("备注（可选）")
        deadline_edit = QDateEdit()
        deadline_edit.setCalendarPopup(True)
        deadline_edit.setDisplayFormat("yyyy-MM-dd")
        if task.deadline:
            d = QDate.fromString(task.deadline, "yyyy-MM-dd")
            if d.isValid():
                deadline_edit.setDate(d)
            else:
                orig = f"（原始截止: {task.deadline}）"
                note_edit.setText(f"{task.note} {orig}" if task.note else orig)

        form.addRow("标题:", title_edit)
        form.addRow("备注:", note_edit)
        form.addRow("截止:", deadline_edit)

        form.addRow(make_dialog_buttons(dialog))

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._task_manager.update_task(
                task.task_id,
                title_edit.text(),
                note_edit.text(),
                deadline_edit.date().toString("yyyy-MM-dd"),
            )
            self._refresh_task_list()
            self.data_changed.emit("task")

    # ---------------- 临时笔记 ----------------
    def _load_temp_note(self):
        if not self._note_manager:
            return
        self._loading_note = True
        try:
            temp = self._note_manager.get_temp_note()
            if temp is not None:
                self._current_note_id = temp.note_id
                self._note_edit.setPlainText(temp.content)
            else:
                self._current_note_id = None
                self._note_edit.clear()
        except Exception:
            self._current_note_id = None
            self._note_edit.clear()
        self._loading_note = False
        self._note_edit.setFocus()

    def _on_note_text_changed(self):
        if self._loading_note:
            return
        self._note_save_timer.start()

    def _on_save_note(self):
        if not self._note_manager:
            return
        content = self._note_edit.toPlainText()
        if self._current_note_id is None or self._note_manager.get_note(self._current_note_id) is None:
            try:
                temp = self._note_manager.get_temp_note()
                self._current_note_id = temp.note_id if temp else None
            except Exception:
                self._current_note_id = None
        if self._current_note_id is None:
            return
        self._note_manager.update_note(self._current_note_id, content, title=None)
        self.data_changed.emit("note")



class NavAppAssetPagesMixin:
    """网址导航 / 软件导航 / 临时素材页（构建、刷新与增删；铁律见模块 docstring）。"""

    # ---- 宿主（CardWindow）依赖面裸注解：仅类型声明，不建类属性、
    # ---- 零运行时行为、MRO 零变化（pyright reportAttributeAccessIssue 消音）。
    _theme: str
    _config_manager: Any
    _nav_manager: Any
    _asset_manager: Any
    _asset_thumb_cache: dict
    _make_page_title: Callable[..., QWidget]
    # mini_icon_size 宿主侧是 @property，此处只能宽声明为 Any
    # （int 会触发 reportIncompatibleVariableOverride）。
    mini_icon_size: Any
    WINDOW_WIDTH: int
    _TAB_BAR_WIDTH: int
    data_changed: pyqtSignal
    isVisible: Callable[..., bool]

    # ==================================================================
    # 网址导航页面（锁定常驻，仅跳转展示）
    # ==================================================================
    def _build_nav_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        v.addWidget(self._make_page_title("nav", "网址导航"))

        # 可滚动区域展示分组和站点（仅纵向滚动，禁止横向滚动条）
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_scroll.setObjectName("navScroll")
        self._nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._nav_content = QWidget()
        self._nav_content.setObjectName("navContent")
        self._nav_content_layout = QVBoxLayout(self._nav_content)
        self._nav_content_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_content_layout.setSpacing(8)
        self._nav_scroll.setWidget(self._nav_content)

        v.addWidget(self._nav_scroll, 1)

        hint = QLabel("点击站点用浏览器打开 | 编辑请打开主窗口")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _make_nav_empty(self) -> EmptyState:
        """网址页空态（E3，2026-10-08）：与素材页同口径的 EmptyState，
        替代原裸 QLabel——同一卡片内三页空态形态统一。图标 ``nav`` 自绘，
        无动作钮（新增动作只在主窗，鼠标全透明不挡滚动）。"""
        empty = EmptyState("nav", "暂无网址", "请在主窗口网址导航中添加",
                           icon_size=36, object_name="navPageEmpty")
        empty.apply_theme(self._theme)
        return empty

    def _refresh_nav_page(self):
        """刷新网址导航页面显示（不分分组，平铺所有站点）
        双列宽卡片：主标题 + 域名副标题（2026-09-25 用户拍板方案C）。
        超长标题省略号截断，tooltip 显示完整标题 + URL，点击整卡打开浏览器。
        """
        # 清除旧内容
        while self._nav_content_layout.count():
            item = self._nav_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._nav_manager:
            self._nav_content_layout.addWidget(self._make_nav_empty())
            return

        sites = self._nav_manager.get_all_sites_flat()
        if not sites:
            self._nav_content_layout.addWidget(self._make_nav_empty())
            return

        # 可用宽度推导：容器440 - 侧栏48 - 内容区margin16×2 - 页面margin4×2
        #   = 352；再预留纵向滚动条 ~12（出现时 viewport 变窄，这是最坏情况）
        available_width = self.WINDOW_WIDTH - self._TAB_BAR_WIDTH - 32 - 8 - 12
        cols = 2
        spacing = 6
        card_w = (available_width - spacing * (cols - 1)) // cols  # 截断估算用
        card_h = 46

        # 按钮字体度量（标题 13px，与 QSS navSiteCardTitle 一致）
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        fm = QFontMetrics(font)

        # 双列宽卡片：QPushButton 作壳（自带点击/hover），内部叠 标题+域名 两行 QLabel。
        # 高度固定、宽度不写死 → 由 QGridLayout 两列均分实际 viewport 宽，永不溢出
        grid_container = QWidget()
        grid_container.setObjectName("navContent")
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(spacing)
        grid.setVerticalSpacing(spacing)

        for i, site in enumerate(sites):
            card = SmoothButton()
            card.setObjectName("navSiteCard")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(f"{site.title}\n{site.url}" if site.title != site.url else site.url)
            card.setFixedHeight(card_h)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.clicked.connect(lambda checked=False, url=site.url: self._open_url(url))

            inner = QVBoxLayout(card)
            inner.setContentsMargins(10, 5, 10, 5)
            inner.setSpacing(0)

            title = QLabel(fm.elidedText(site.title, Qt.TextElideMode.ElideRight, card_w - 20))
            title.setObjectName("navSiteCardTitle")
            domain = QLabel(fm.elidedText(_domain_of_url(site.url), Qt.TextElideMode.ElideRight, card_w - 20))
            domain.setObjectName("navSiteCardDomain")

            inner.addWidget(title)
            inner.addWidget(domain)

            grid.addWidget(card, i // cols, i % cols)

        self._nav_content_layout.addWidget(grid_container)
        self._nav_content_layout.addStretch()

    def _open_url(self, url: str):
        """用系统默认浏览器打开 URL（优先 os.startfile，回退 QDesktopServices）"""
        if not url:
            return
        # 优先使用 os.startfile（Windows 系统级打开，更稳定）
        try:
            os.startfile(url)
            return
        except Exception:
            pass
        # 回退到 QDesktopServices
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # ==================================================================
    # 软件导航页面（小卡片内嵌只读浏览：图标 + 名称，点击启动）
    # ==================================================================
    def _build_app_page(self):
        """
        构建软件导航小卡片页面。

        - 只读浏览：与主窗口 AppLauncherPage 共用同一份 config["apps"] 数据
        - 仅显示软件图标 + 软件名称（QToolButton 文字在图标下方）
        - 点击卡片 → 调用公共 launch_app() 异步启动（含 740 提权处理）
        - exe 失效的条目置灰禁用
        - 新增/编辑/删除仍只在主窗口导航页完成，本页无任何管理入口
        """
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        v.addWidget(self._make_page_title("apps", "软件导航"))

        # 可滚动区域展示软件网格（仅纵向滚动）
        self._app_scroll = QScrollArea()
        self._app_scroll.setWidgetResizable(True)
        self._app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._app_scroll.setObjectName("appScroll")
        self._app_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._app_content = QWidget()
        self._app_content.setObjectName("appContent")
        self._app_grid = QGridLayout(self._app_content)
        self._app_grid.setContentsMargins(0, 0, 4, 0)
        self._app_grid.setSpacing(6)
        # 网格从左上角开始排列：列不拉伸铺满容器宽度，
        # 按钮按行从左到右、从上到下逐一排列（修复居中/散开问题）
        self._app_grid.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._app_scroll.setWidget(self._app_content)

        v.addWidget(self._app_scroll, 1)

        hint = QLabel("点击卡片启动软件 | 编辑请打开主窗口")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _make_app_empty(self) -> EmptyState:
        """软件页空态（E3，2026-10-08）：与素材页同口径的 EmptyState，
        替代原裸 QLabel——同一卡片内三页空态形态统一。图标 ``apps`` 自绘，
        无动作钮（新增/编辑只在主窗，鼠标全透明不挡滚动）。"""
        empty = EmptyState("apps", "暂无软件", "请在主窗口软件导航中添加",
                           icon_size=36, object_name="appPageEmpty")
        empty.apply_theme(self._theme)
        return empty

    def _refresh_app_page(self):
        """刷新软件导航小卡片页面：重读 config apps 并重建网格。"""
        # 清空旧网格内容
        while self._app_grid.count():
            item = self._app_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 数据来源：宿主 ConfigManager（与主窗口导航页共用）
        apps = []
        if self._config_manager:
            raw = self._config_manager.get("apps", [])
            if isinstance(raw, list):
                apps = raw

        if not apps:
            self._app_grid.addWidget(self._make_app_empty(), 0, 0)
            return

        # 图标/名称工具来自 widget_app_launcher（与主窗口卡片同一套）
        from src.widget_app_launcher import (
            extract_exe_icon, load_icon_pixmap, draw_placeholder_icon, launch_app,
        )

        # 小卡片内容区可用宽度：440 - 侧栏48 - 内容边距32 - 内层边距8 ≈ 352
        icon_px = self.mini_icon_size
        btn_size = mini_btn_size(icon_px)
        available = self.WINDOW_WIDTH - self._TAB_BAR_WIDTH - 40
        cols = max(1, available // (btn_size + 6))

        placed = 0  # 实际放置计数（非法条目跳过不留洞）
        for app in apps:
            # 容错：字段残缺自动补默认（禁止闪退）
            if not isinstance(app, dict):
                continue
            name = str(app.get("name") or "未命名")
            exe_path = str(app.get("exe_path") or "")
            icon_path = str(app.get("icon_path") or "")
            valid = bool(exe_path) and os.path.exists(exe_path)

            # 图标：自定义图标 > exe 内嵌图标 > 占位图标
            pixmap = None
            if icon_path and os.path.exists(icon_path):
                pixmap = load_icon_pixmap(icon_path, icon_px)
            if pixmap is None or pixmap.isNull():
                pixmap = extract_exe_icon(exe_path, icon_px)
            if pixmap is None or pixmap.isNull():
                pixmap = draw_placeholder_icon(icon_px)

            btn = QToolButton()
            btn.setObjectName("appLaunchBtn")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(icon_px, icon_px))
            btn.setIcon(QIcon(pixmap))
            # 名称过长截断（超6字符省略）
            display = name if len(name) <= 6 else name[:5] + "…"
            btn.setText(display)
            btn.setFixedSize(btn_size, btn_size)
            btn.setToolTip(f"{name}\n{exe_path}")

            if valid:
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda checked=False, a=dict(app): launch_app(a, parent=self)
                )
            else:
                # exe 失效：置灰禁用，悬浮提示说明原因
                btn.setEnabled(False)
                btn.setToolTip(f"{name}\n{exe_path}\n可执行文件不存在")

            self._app_grid.addWidget(btn, placed // cols, placed % cols)
            placed += 1

    # ==================================================================
    # 临时素材页面（网格布局，支持拖拽取出）
    # ==================================================================
    def _build_asset_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        # 顶部标题 + 计数
        header = QHBoxLayout()
        header.addWidget(self._make_page_title("assets", "临时素材"))
        header.addStretch()
        self._asset_count_label = QLabel("共 0 个")
        self._asset_count_label.setObjectName("hintLabel")
        header.addWidget(self._asset_count_label)
        v.addLayout(header)

        # 可滚动区域
        self._asset_scroll = QScrollArea()
        self._asset_scroll.setWidgetResizable(True)
        self._asset_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._asset_scroll.setObjectName("assetScroll")

        self._asset_content = QWidget()
        self._asset_content.setObjectName("assetContent")
        self._asset_content_layout = QVBoxLayout(self._asset_content)
        self._asset_content_layout.setContentsMargins(0, 0, 0, 0)
        self._asset_content_layout.setSpacing(8)
        self._asset_scroll.setWidget(self._asset_content)

        v.addWidget(self._asset_scroll, 1)

        hint = QLabel("拖拽素材到任意位置即可取出 | 双击打开 | 右键菜单")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)

        return page

    def _make_asset_empty(self) -> EmptyState:
        """素材页空态（UI 重构 03）：图标 + 标题 + 提示，替代原单行 QLabel。

        图标取页面同名 ``assets`` 自绘图标；无动作钮 —— 「收录」动作发生在
        悬浮球拖入，这里只做引导（EmptyState 无钮时自动鼠标穿透）。图标配色
        在这里就近刷一次，免等下一次换主题（构造时机晚于 ``_apply_style``）。
        """
        empty = EmptyState("assets", "暂无素材", "拖文件到悬浮球即可收录",
                           icon_size=36, object_name="assetEmpty")
        empty.apply_theme(self._theme)
        return empty

    def _refresh_asset_page(self):
        """刷新临时素材页面（数据变化后首次进入才调用，平常切页零开销）"""
        self._asset_page_dirty = False
        # 清除旧内容
        while self._asset_content_layout.count():
            item = self._asset_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        if not self._asset_manager:
            self._asset_content_layout.addWidget(self._make_asset_empty())
            self._asset_count_label.setText("共 0 个")
            return

        assets = self._asset_manager.get_all_assets()
        # 清理已删除素材的缩略图缓存
        valid_ids = {a.asset_id for a in assets}
        for key in list(self._asset_thumb_cache):
            if key not in valid_ids:
                del self._asset_thumb_cache[key]
        if not assets:
            self._asset_content_layout.addWidget(self._make_asset_empty())
            self._asset_count_label.setText("共 0 个")
            return

        # 网格布局：每行 4 个
        grid_container = QWidget()
        grid_container.setObjectName("assetGrid")
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        cols = 4
        for i, asset in enumerate(assets):
            item_widget = _AssetItemWidget(asset, theme=self._theme,
                                           thumb_cache=self._asset_thumb_cache)
            grid.addWidget(item_widget, i // cols, i % cols)
        # 补齐末行空白，让网格居中对齐
        total = len(assets)
        last_row = (total - 1) // cols
        if total % cols != 0:
            for j in range(total % cols, cols):
                placeholder = QWidget()
                placeholder.setFixedSize(72, 84)
                grid.addWidget(placeholder, last_row, j)

        self._asset_content_layout.addWidget(grid_container)
        self._asset_content_layout.addStretch()
        self._asset_count_label.setText(f"共 {total} 个")

    @staticmethod
    def _clear_layout(layout):
        """递归清除布局中的所有项"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                NavAppAssetPagesMixin._clear_layout(item.layout())

    def _delete_asset(self, asset_id: int):
        """删除素材（由 _AssetItemWidget 右键菜单调用）"""
        if not self._asset_manager:
            return
        if self._asset_manager.delete_asset(asset_id):
            self._refresh_asset_page()
            self.data_changed.emit("asset")

    def notify_assets_changed(self):
        """外部素材数据变化入口：置脏标记；卡片可见时立即重建。

        不可见时只置脏 —— 下次切到素材页/展开卡片时才重建，
        避免隐藏状态下白白解码缩略图。
        """
        self._asset_page_dirty = True
        if self.isVisible():
            self._refresh_asset_page()

