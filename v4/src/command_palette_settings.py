# -*- coding: utf-8 -*-
"""命令面板设置控件  -  HotkeyCaptureEdit / CommandKeysGrid / CustomActionsPanel
====================================================================
设置页「命令面板」卡的三块专用控件（2026-10-06），从 settings_panel 外迁
成独立模块，避免设置面板文件膨胀失控：

  · HotkeyCaptureEdit  : 捕捉式热键输入框 —— 只读 QLineEdit，点击进入
                         捕捉态，按下完整组合键（含修饰键）后发
                         ``captured(str)`` 信号；Esc 取消、无修饰键给出
                         行内提示不退出（轻量口径，不用模态框）。
  · CommandKeysGrid    : 命令直达键网格 —— 列出全部可绑命令（10 页面 +
                         3 动作），每行「自绘图标 + 名称 + 捕捉输入框」；
                         捕获完成后查重（其他命令 / 自定义动作 / 截图
                         热键 / 全局唤醒键都算冲突）→ set + save +
                         ``changed`` 信号。
  · CustomActionsPanel : 自定义动作面板 —— 列表（悬停显删除钮）+ 表单
                         （类型三选一 / 名称 / 内容 / 直达键），上限 20 条。

数据读写：构造时注入 host（MainWindow），一律经 ``host.config`` 公开面
读配置、``set + save`` 写回，变更通过 ``changed`` 信号广播（settings_panel
接到后转发 host.command_palette_changed 让主窗重注册直达键）。

清洗与校验单一真相源 = src/command_palette.py 的 sanitize_* 纯函数；
本模块不做第二套校验逻辑。图标一律 icon_render 自绘（禁 emoji）；
按钮用 controls 的 SmoothButton / IconButton（禁裸 QPushButton）。
====================================================================
"""

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
    QWidget,
)

from src import command_palette as cp
from src import icon_render
from src.controls import IconButton, SmoothButton, SmoothInput
from src.theme import DEFAULT_THEME, get_colors

# 捕捉输入框固定宽度（与设置页截图热键输入框同档，太窄放不下
# "Ctrl+Shift+F12" 这类长组合）
CAPTURE_EDIT_WIDTH = 130

# 纯修饰键（按住等待，不算完整组合）
_MODIFIER_ONLY_KEYS = frozenset({
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
})

# 组合键串比较口径：小写 + 去空格（wiring.PluginWiring 的核心热键让位同款）
def _norm_combo(combo: str) -> str:
    return str(combo or "").strip().lower().replace(" ", "")


def _is_valid_wake_combo(text) -> bool:
    """全局唤醒键轻校验：与 cp.is_valid_combo 同骨架（修饰键白名单复用
    cp._COMBO_MODIFIERS、至少一个修饰键、F1-F24），但末段放宽为**任意
    单字符**——"/"、";" 等符号经 global_hotkey.parse_hotkey 的 VkKeyScanW
    通道可映射为虚拟键，而 is_valid_combo 只认字母 / 数字，直接复用会把
    默认值 "Alt+/" 误杀（用户在设置页重绑默认值会被自己的 UI 拒绝）。

    仅做形态校验（离屏 / 跨平台不依赖 Windows-only 的 parse_hotkey）；
    注册层仍由 RegisterHotKey 把关，失败走既有 warning + toast 口径。
    """
    if not isinstance(text, str) or not text.strip():
        return False
    tokens = [t.strip() for t in text.split("+") if t.strip()]
    if len(tokens) < 2:
        return False                      # 必须至少带一个修饰键
    mods = [t.lower() for t in tokens[:-1]]
    if any(m not in cp._COMBO_MODIFIERS for m in mods):   # noqa: SLF001
        return False
    last = tokens[-1].lower()
    if len(last) == 1:
        return True                       # 任意单字符（含符号，如 "/"）
    return (len(last) >= 2 and last[0] == "f" and last[1:].isdigit()
            and 1 <= int(last[1:]) <= 24)


class HotkeyCaptureEdit(QLineEdit):
    """捕捉式热键输入框：点击进入捕捉态，按下完整组合键后发 captured。"""

    captured = pyqtSignal(str)

    # 全局唯一捕捉态持有者（类级）：点另一个捕捉框时先让旧框退出，
    # 避免多个框同时卡在捕捉文案上（2026-10-06 修复）
    _active: "HotkeyCaptureEdit | None" = None

    # 提示文案刻意精短：输入框 130px 定宽，长文案会被截断成半句；
    # 「Esc 取消」语义放 tooltip（见 __init__）
    _CAPTURE_HINT = "按下组合键…"
    _NEED_MODIFIER_HINT = "需含修饰键"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._combo = ""
        self._capturing = False
        self.setReadOnly(True)
        self.setFixedWidth(CAPTURE_EDIT_WIDTH)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(
            "点击后按下组合键（需含 Ctrl/Alt/Shift 修饰键，Esc 取消）")

    # ---------------- 对外 ----------------
    def combo(self) -> str:
        """当前已绑定的组合键串（未绑为空串）。"""
        return self._combo

    def set_combo(self, combo: str):
        """设置组合键串（空串 = 未绑键），退出捕捉态。"""
        self._combo = str(combo or "")
        self._stop_capture()
        self.setText(self._combo)

    # ---------------- 捕捉态 ----------------
    def mousePressEvent(self, event):
        """点击进入捕捉态（只读框，点击即聚焦 + 开始监听按键）。"""
        if not self._capturing:
            # 同一时刻至多一个捕捉框：旧活跃框先退出（还原旧值）
            active = HotkeyCaptureEdit._active
            if active is not None and active is not self:
                active._stop_capture()
            self._capturing = True
            HotkeyCaptureEdit._active = self
            self.setText(self._CAPTURE_HINT)
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        """焦点离开即取消捕捉态（点另一个捕捉框 / 点别处都还原旧值）。

        没有这条路径时，焦点被点走后旧框永远停在捕捉文案上（2026-10-06
        真机截图实锤的「多个框同时卡住」根因）。
        """
        if self._capturing:
            self._stop_capture()
        super().focusOutEvent(event)

    def event(self, e):
        """捕捉态吃掉 ShortcutOverride：组合键不被 ApplicationShortcut
        （如 "/" 触发键、Ctrl+T）抢走，全部进 keyPressEvent。"""
        if self._capturing and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._stop_capture()          # Esc 取消 → 还原旧值
            event.accept()
            return
        if key in _MODIFIER_ONLY_KEYS or key == Qt.Key.Key_unknown:
            # 纯修饰键按住不算，等完整组合
            event.accept()
            return
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        meta = bool(mods & Qt.KeyboardModifier.MetaModifier)
        if not (ctrl or alt or shift or meta):
            # 无修饰键（如单按字母）：给改正机会，不退出捕捉态
            self.setText(self._NEED_MODIFIER_HINT)
            event.accept()
            return
        key_text = QKeySequence(int(key)).toString()
        if not key_text:
            event.accept()
            return
        parts = []
        if ctrl:
            parts.append("Ctrl")
        if alt:
            parts.append("Alt")
        if shift:
            parts.append("Shift")
        if meta:
            # Qt 把 Windows 键映射为 Meta，全局热键侧叫 "Win"
            parts.append("Win")
        parts.append(key_text.upper() if len(key_text) == 1 else key_text)
        combo = "+".join(parts)
        self._combo = combo
        self._stop_capture()
        self.captured.emit(combo)

    def _stop_capture(self):
        self._capturing = False
        if HotkeyCaptureEdit._active is self:
            HotkeyCaptureEdit._active = None
        self.setText(self._combo)


def _make_row_icon(icon_name: str, size: int, host) -> QLabel:
    """自绘小图标（主题跟随；离屏 / 无 QApplication 时容错回灰）。"""
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    theme = getattr(host, "current_theme", None) or DEFAULT_THEME
    try:
        color = get_colors(theme)["text_secondary"]
        lbl.setPixmap(icon_render.icon_pixmap(icon_name, size, color))
    except Exception:                      # noqa: BLE001 - 图标失败不拖垮设置页
        pass
    return lbl


class CommandKeysGrid(QWidget):
    """命令直达键网格：全部可绑命令逐行「图标 + 名称 + 捕捉输入框」。

    命令清单（单一真相源，与命令面板注册表同源）：
      · 页面 10 条 = main_window.NAV_PAGE_TITLES（8 固定页）+
        NAV_PAGE_TITLES_FIXED（设置 / 使用说明）；
      · 动作 3 条 = command_palette.action_commands() 里 target 为
        ("action", ...) 的项（导出 / 切主题 / 新建任务；help 深链不收）。
    """

    changed = pyqtSignal()

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self.setObjectName("cmdKeyGrid")
        self._edits = {}                   # cid -> HotkeyCaptureEdit
        self._specs = self._build_specs()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(5)
        self._grid = grid
        self.refresh()

    # ---------------- 数据 ----------------
    @staticmethod
    def _build_specs():
        """[(cid, 展示名, 图标名), ...]（页面在前、动作随后，稳定顺序）。"""
        from src.icons import NAV_ICON
        from src.main_window import NAV_PAGE_TITLES, NAV_PAGE_TITLES_FIXED
        specs = []
        merged = dict(NAV_PAGE_TITLES)
        merged.update(NAV_PAGE_TITLES_FIXED)
        for key, title in merged.items():
            specs.append((f"page.{key}", str(title),
                          NAV_ICON.get(key, "command")))
        for entry in cp.action_commands():
            if entry.target[0] == "action":
                specs.append((entry.cid, entry.title, entry.icon))
        return specs

    def _titles(self) -> dict:
        """cid → 展示名（冲突提示「与『xx』冲突」用）。"""
        return {cid: title for cid, title, _icon in self._specs}

    def refresh(self):
        """按 config.command_hotkeys 重建网格（直达键变更 / 恢复默认后调用）。"""
        self._edits = {}
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        hotkeys = cp.sanitize_hotkeys_map(
            self._host.config.get("command_hotkeys", {}) or {})
        for i, (cid, title, icon) in enumerate(self._specs):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            h.addWidget(_make_row_icon(icon, 14, self._host))
            name = QLabel(title)
            h.addWidget(name)
            h.addStretch()
            edit = HotkeyCaptureEdit()
            edit.set_combo(hotkeys.get(cid, ""))
            edit.captured.connect(
                lambda combo, c=cid, e=edit: self._on_captured(c, e, combo))
            self._edits[cid] = edit
            h.addWidget(edit)
            self._grid.addWidget(row, i // 2, i % 2)

    # ---------------- 捕获落盘 ----------------
    def _on_captured(self, cid: str, edit, combo: str):
        """组合键捕获完成：查重 → set + save + changed（冲突拒绝并还原）。"""
        config = self._host.config
        if not cp.is_valid_combo(combo):
            edit.set_combo(self._current_map().get(cid, ""))
            return
        norm = _norm_combo(combo)
        holder = self._find_conflict(cid, norm)
        if holder is not None:
            self._toast(f"与『{holder}』冲突，已拒绝")
            edit.set_combo(self._current_map().get(cid, ""))
            return
        new_map = self._current_map()
        new_map[cid] = combo
        config.set("command_hotkeys", new_map)
        config.save()
        edit.set_combo(combo)
        self.changed.emit()
        self._toast(f"直达键已设为 {combo}")

    def _current_map(self) -> dict:
        return dict(cp.sanitize_hotkeys_map(
            self._host.config.get("command_hotkeys", {}) or {}))

    def _find_conflict(self, cid: str, norm: str, include_wake: bool = True):
        """该组合键是否已被占用：返回占用方展示名，无冲突返回 None。

        冲突口径：其他命令的直达键 / 自定义动作的直达键 / 截图钉屏热键 /
        全局唤醒键（2026-10-06）。``include_wake=False`` 供全局唤醒键自身
        的捕获处理复用（自己与自己不构成冲突，见 settings_panel
        _on_cmd_wake_changed）。
        """
        titles = self._titles()
        for other_cid, other in self._current_map().items():
            if other_cid != cid and _norm_combo(other) == norm:
                return titles.get(other_cid, other_cid)
        for action in cp.sanitize_custom_actions(
                self._host.config.get("custom_actions", []) or []):
            if action["hotkey"] and _norm_combo(action["hotkey"]) == norm:
                return action["title"]
        shot = str(self._host.config.get("screenshot_hotkey", "") or "")
        if shot and _norm_combo(shot) == norm:
            return "截图钉屏"
        if include_wake:
            wake = str(self._host.config.get(
                "command_palette_global_hotkey", "") or "")
            if wake and _norm_combo(wake) == norm:
                return "全局唤醒键"
        return None

    def _toast(self, text: str):
        show = getattr(self._host, "show_toast", None)
        if callable(show):
            show(text)


class _ActionRow(QWidget):
    """自定义动作列表行：悬停时显示删除钮（enterEvent / leaveEvent 驱动）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._delete_btn = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_delete_btn(self, btn):
        self._delete_btn = btn
        btn.hide()

    def enterEvent(self, event):
        if self._delete_btn is not None:
            self._delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._delete_btn is not None:
            self._delete_btn.hide()
        super().leaveEvent(event)


class CustomActionsPanel(QWidget):
    """自定义动作面板：列表 + 表单（新建 / 编辑复用同一表单）。"""

    changed = pyqtSignal()

    # 类型三选一：(type, 展示名, 图标名)——与 cp._CUSTOM_ACTION_ICONS 同源
    _TYPE_SPECS = (("url", "网址", "nav"), ("folder", "文件夹", "folder"),
                   ("text", "文本", "clipboard"))
    _PLACEHOLDERS = {
        "url": "https://example.com",
        "folder": r"C:\path\to\folder",
        "text": "要复制的文本内容",
    }

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._editing_id = None             # None = 新建；否则为编辑中的 id
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(6)

        # ---- 列表区（refresh 整体重建）----
        self._list_holder = QWidget()
        self._list_v = QVBoxLayout(self._list_holder)
        self._list_v.setContentsMargins(0, 0, 0, 0)
        self._list_v.setSpacing(2)
        root.addWidget(self._list_holder)

        # ---- 表单区（默认隐藏）----
        self._form = QWidget()
        fv = QVBoxLayout(self._form)
        fv.setContentsMargins(0, 4, 0, 0)
        fv.setSpacing(6)
        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(8)
        type_row.addWidget(QLabel("类型"))
        self._type_group = QButtonGroup(self._form)
        self._type_group.setExclusive(True)
        self._type_btns = {}
        for atype, label, _icon in self._TYPE_SPECS:
            btn = SmoothButton(label)
            # 复用 secondaryBtn 的 checkable QSS（theme.py 有 :checked 态），
            # 不新增 QSS token（任务书既定取舍）
            btn.setObjectName("secondaryBtn")
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _chk=False, t=atype: self._on_type_selected(t))
            self._type_group.addButton(btn)
            self._type_btns[atype] = btn
            type_row.addWidget(btn)
        type_row.addStretch()
        fv.addLayout(type_row)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(QLabel("名称"))
        self._title_edit = SmoothInput()
        self._title_edit.setPlaceholderText("如：打开 GitHub")
        title_row.addWidget(self._title_edit, 1)
        fv.addLayout(title_row)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(8)
        value_row.addWidget(QLabel("内容"))
        self._value_edit = SmoothInput()
        value_row.addWidget(self._value_edit, 1)
        fv.addLayout(value_row)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        key_row.addWidget(QLabel("直达键"))
        self._hotkey_edit = HotkeyCaptureEdit()
        key_row.addWidget(self._hotkey_edit)
        key_row.addStretch()
        fv.addLayout(key_row)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()
        self._cancel_btn = SmoothButton("取消")
        self._cancel_btn.setObjectName("secondaryBtn")
        self._cancel_btn.clicked.connect(self._stop_edit)
        self._save_btn = SmoothButton("保存")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._save_btn)
        fv.addLayout(btn_row)

        self._form.hide()
        root.addWidget(self._form)

        self.refresh()

    # ---------------- 列表 ----------------
    def refresh(self):
        """按 config.custom_actions 重建列表（变更 / 恢复默认后调用）。"""
        while self._list_v.count():
            item = self._list_v.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        actions = cp.sanitize_custom_actions(
            self._host.config.get("custom_actions", []) or [])
        if not actions:
            self._form.hide()
            empty = QLabel("还没有自定义动作，点击上方「＋ 新建动作」添加")
            empty.setObjectName("settingDesc")
            self._list_v.addWidget(empty)
            return
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        colors = get_colors(theme)
        icon_by_type = {t: icon for t, _label, icon in self._TYPE_SPECS}
        for action in actions:
            self._list_v.addWidget(self._build_row(action, icon_by_type,
                                                   colors))

    def _build_row(self, action: dict, icon_by_type: dict, colors: dict):
        row = _ActionRow()
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 3, 2, 3)
        h.setSpacing(8)
        h.addWidget(_make_row_icon(icon_by_type[action["type"]], 14,
                                   self._host))
        text_v = QVBoxLayout()
        text_v.setContentsMargins(0, 0, 0, 0)
        text_v.setSpacing(1)
        title = QLabel(action["title"])
        text_v.addWidget(title)
        info = QLabel(self._row_info_text(action, title))
        info.setObjectName("settingDesc")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text_v.addWidget(info)
        h.addLayout(text_v, 1)
        del_btn = IconButton("trash", size=22, icon_size=12,
                             object_name="iconBtn", host=self._host,
                             tooltip="删除该动作")
        del_btn.clicked.connect(
            lambda _chk=False, a=action: self._on_delete(a))
        h.addWidget(del_btn)
        row.set_delete_btn(del_btn)
        row.mousePressEvent = lambda _event, a=action: self._start_edit(a)  # noqa: E731
        row.setToolTip("点击编辑")
        return row

    @staticmethod
    def _row_info_text(action: dict, sample_label) -> str:
        """第二行灰字：内容（中段省略防撑宽卡片）· 键位 / 未绑键。"""
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(sample_label.font())
        elided = fm.elidedText(str(action["value"]),
                               Qt.TextElideMode.ElideMiddle, 340)
        key = action["hotkey"] or "未绑键"
        return f"{elided} · {key}"

    def _on_delete(self, action: dict):
        """删除动作：无需确认框，toast 告知（config 单一写入口）。"""
        config = self._host.config
        actions = cp.sanitize_custom_actions(
            config.get("custom_actions", []) or [])
        actions = [a for a in actions if a["id"] != action["id"]]
        config.set("custom_actions", actions)
        config.save()
        if self._editing_id == action["id"]:
            self._stop_edit()
        self.refresh()
        self.changed.emit()
        self._toast(f"已删除「{action['title']}」")

    # ---------------- 表单 ----------------
    def start_new(self):
        """「＋ 新建动作」入口（settings_panel 的按钮连接到这里）。"""
        self._editing_id = None
        self._title_edit.clear()
        self._value_edit.clear()
        self._hotkey_edit.set_combo("")
        first_type = self._TYPE_SPECS[0][0]
        self._type_btns[first_type].setChecked(True)
        self._on_type_selected(first_type)
        self._form.show()

    def _start_edit(self, action: dict):
        """点击列表行进入编辑态（复用同一表单）。"""
        self._editing_id = action["id"]
        self._title_edit.setText(action["title"])
        self._value_edit.setText(action["value"])
        self._hotkey_edit.set_combo(action["hotkey"])
        self._type_btns[action["type"]].setChecked(True)
        self._on_type_selected(action["type"])
        self._form.show()

    def _stop_edit(self):
        self._editing_id = None
        self._form.hide()

    def _on_type_selected(self, atype: str):
        """类型切换：内容输入框占位文案随类型变。"""
        self._value_edit.setPlaceholderText(
            self._PLACEHOLDERS.get(atype, ""))

    def _on_save(self):
        """保存：名称/内容非空 → 直达键查重 → 上限 20 → set + save + changed。"""
        config = self._host.config
        title = self._title_edit.text().strip()
        value = self._value_edit.text().strip()
        if not title or not value:
            self._toast("名称与内容不能为空")
            return
        atype = next((t for t, _label, _icon in self._TYPE_SPECS
                      if self._type_btns[t].isChecked()), "url")
        combo = self._hotkey_edit.combo()
        actions = cp.sanitize_custom_actions(
            config.get("custom_actions", []) or [])
        if combo:
            holder = self._hotkey_conflict(actions, combo)
            if holder is not None:
                self._toast(f"与『{holder}』冲突，已拒绝")
                return
        if self._editing_id is None:
            if len(actions) >= cp.MAX_CUSTOM_ACTIONS:
                self._toast(f"自定义动作最多 {cp.MAX_CUSTOM_ACTIONS} 条")
                return
            next_id = max((a["id"] for a in actions), default=0) + 1
            actions.append({"id": next_id, "type": atype, "title": title,
                            "value": value, "hotkey": combo})
        else:
            for a in actions:
                if a["id"] == self._editing_id:
                    a["type"] = atype
                    a["title"] = title
                    a["value"] = value
                    a["hotkey"] = combo
        config.set("custom_actions", actions)
        config.save()
        self._stop_edit()
        self.refresh()
        self.changed.emit()
        self._toast("自定义动作已保存")

    def _hotkey_conflict(self, actions, combo: str):
        """表单直达键与既有绑定冲突（其他动作 / 命令直达键 / 截图热键 /
        全局唤醒键）。"""
        norm = _norm_combo(combo)
        for a in actions:
            if a["id"] != self._editing_id and a["hotkey"] \
                    and _norm_combo(a["hotkey"]) == norm:
                return a["title"]
        grid = self._find_sibling_grid()
        if grid is not None:
            for cid, other in grid._current_map().items():  # noqa: SLF001
                if _norm_combo(other) == norm:
                    return grid._titles().get(cid, cid)  # noqa: SLF001
        shot = str(self._host.config.get("screenshot_hotkey", "") or "")
        if shot and _norm_combo(shot) == norm:
            return "截图钉屏"
        wake = str(self._host.config.get(
            "command_palette_global_hotkey", "") or "")
        if wake and _norm_combo(wake) == norm:
            return "全局唤醒键"
        return None

    def _find_sibling_grid(self):
        """同卡直达键网格（settings_panel 挂在同层；找不到回 None）。"""
        parent = self.parentWidget()
        while parent is not None:
            grid = parent.findChild(CommandKeysGrid)
            if grid is not None and grid is not self:
                return grid
            parent = parent.parentWidget()
        return None

    def _toast(self, text: str):
        show = getattr(self._host, "show_toast", None)
        if callable(show):
            show(text)
