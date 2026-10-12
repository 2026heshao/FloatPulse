# -*- coding: utf-8 -*-
"""
====================================================================
文本控件右键菜单  -  text_context_menu
====================================================================
把 Qt 给文本控件生成的**英文、不套主题、几乎全灰**的标准右键菜单
（``createStandardContextMenu()``），换成应用自己的中文菜单。

背景（2026-10-12 用户报障，取证结论）
--------------------------------------
用户在多个窗口右键都弹出同一张菜单：``Undo / Redo / Cut / Copy /
Paste / Delete / Select All``，纯英文、白底、且除「Select All」外几乎
全部置灰（"没有实质作用"）。取证（真机 windows 平台复现，逐项一致）：

  1. 该菜单来自 Qt 文本控件自带的 ``createStandardContextMenu()``
     —— 与本仓代码无关，全仓搜索不到 "Undo"/"Select All" 字面量；
  2. 它**不走应用主题**：菜单 ``styleSheet()`` 长度为 0，应用主窗 /
     卡片窗口 QSS 都覆盖不到它（实测：只显式 ``setStyleSheet`` 后
     才吃到主题）；
  3. 空 / 未聚焦控件确实只有「全选」可用 —— 这正是"没有实质作用"
     的观感来源；
  4. 受影响面：全仓 55 处文本控件（笔记正文与搜索框、碎片编辑、任务
     输入、网址导航、知识库段落、设置页 12 个输入框、命令面板、便签
     正文、素材重命名、合并预览、以及插件页的文本工坊 / AI 助手 /
     周期任务 / Vault 密码框）。

设计要点
--------
1. **一条应用级事件过滤器覆盖全部窗口**（含后续加载的插件页）：
   逐个给 55 处控件接线既漏又散，且在插件页里根本不可达。
2. **只列对当前控件真正适用的项**（用户口径 A），不适用的项**不出现**：
   - 只读控件（揭示原文 / 使用说明 / 知识库结果）→ 只有「复制」「全选」；
   - 密码回显控件（``EchoMode != Normal``）→ **不给**「剪切」「复制」
     —— 这是安全闸门：Qt 标准菜单在密码态同样禁用二者，本模块必须
     保持同等约束，绝不把回显掩码下的明文读进菜单作用域；
   - 可编辑控件 → 撤销 / 重做 / 剪切 / 复制 / 粘贴 / 删除 / 全选。
   同一组内**保留但置灰**（用户口径）：暂不可用时置灰而非隐藏，菜单
   结构稳定，用户能看出有哪些能力。
3. **套用应用菜单主题**：``get_menu_qss(theme)``，主题沿控件父链
   解析（``_theme`` → ``current_theme`` → 默认），浅/深色都跟着走。
4. **零副作用**：动作一律转发到控件自身方法（``undo`` / ``copy`` /
   ``paste`` / ``selectAll`` …），不自行改文本、不写剪贴板、不动锚点；
   快捷键提示按 Qt 自己的做法用 ``\\t`` 嵌在文本里展示，**不**
   ``setShortcut()`` —— 避免在菜单存活期注册出真实快捷键。
5. **不抢已有菜单**：控件若自行设了 ``CustomContextMenu`` 策略（列表类
   均如此）或 ``NoContextMenu``，一律放行不接管。

为什么不复用 ``controls._detect_theme``：那是三个控件类的实例方法，
另有 ``nav_panel._nav_theme_of`` 同款。把四处收口成公共函数需要同时改动
四个已上线调用点，属独立重构；本模块先自带一份最小实现，
**收敛为公共函数列为后续项**（见收尾报告「已知限制」）。
====================================================================
"""

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QLineEdit, QMenu, QPlainTextEdit, QTextEdit,
)

from src.constants import DEFAULT_THEME
from src.theme import get_menu_qss

# 本模块接管的文本控件类型。
# ★ QPlainTextEdit **不是** QTextEdit 的子类（实测 issubclass 为 False），
#   QTextBrowser 则是 QTextEdit 子类 —— 三者必须并列写全，漏掉
#   QPlainTextEdit 会让插件页（文本工坊 / AI 助手 / 周报）继续弹英文菜单。
_TEXT_EDIT_TYPES = (QTextEdit, QPlainTextEdit)

# 主题解析失败时的兜底（与 theme.DEFAULT_THEME 同源，不另立默认值）
_FALLBACK_THEME = DEFAULT_THEME


def _is_text_edit(w) -> bool:
    """QTextEdit 家族（含 QTextBrowser 子类）判定。"""
    return isinstance(w, _TEXT_EDIT_TYPES)


def resolve_text_widget(obj):
    """把事件目标解析成真正要出菜单的文本控件（解析不到返回 ``None``）。

    事件可能落在两处（实测）：控件本体（QLineEdit / QTextEdit），
    或滚动区的 viewport（QAbstractScrollArea 的 ContextMenu 先到
    viewport）。viewport 是裸 QWidget，靠父链回升到文本控件。
    """
    if isinstance(obj, QLineEdit) or _is_text_edit(obj):
        return obj
    parent = getattr(obj, "parentWidget", None)
    if callable(parent):
        p = obj.parentWidget()
        if isinstance(p, QLineEdit) or _is_text_edit(p):
            return p
    return None


def resolve_theme(widget) -> str:
    """沿父链解析控件当前主题（``_theme`` → ``current_theme`` → 默认）。

    只认 ``"light"`` / ``"dark"`` 两个合法值：链上出现同名但取值非法
    的属性（如 ``_theme = "blue-ish"``）不误判，继续往上找。
    """
    w = widget
    while w is not None:
        t = getattr(w, "_theme", None)
        if t in ("light", "dark"):
            return t
        w = w.parentWidget()
    w = widget
    while w is not None:
        t = getattr(w, "current_theme", None)
        if t in ("light", "dark"):
            return t
        w = w.parentWidget()
    return _FALLBACK_THEME


# --------------------------------------------------------------------
# 控件能力探测（唯一取数处；菜单启用态与动作分发共用同一判据）
# --------------------------------------------------------------------
def _is_read_only(w) -> bool:
    return bool(w.isReadOnly())


def _is_secret(w) -> bool:
    """密码 / 无回显控件：对外的剪切、复制一律禁用（安全闸门）。"""
    if not isinstance(w, QLineEdit):
        return False
    try:
        return w.echoMode() != QLineEdit.EchoMode.Normal
    except Exception:                              # noqa: BLE001
        return True        # 判不出来就按敏感处理，宁严不漏


def _has_selection(w) -> bool:
    if isinstance(w, QLineEdit):
        return bool(w.hasSelectedText())
    return bool(w.textCursor().hasSelection())


def _has_content(w) -> bool:
    if isinstance(w, QLineEdit):
        return bool(w.text())
    return not bool(w.document().isEmpty())


def _can_undo(w) -> bool:
    if isinstance(w, QLineEdit):
        return bool(w.isUndoAvailable())
    return bool(w.document().isUndoAvailable())


def _can_redo(w) -> bool:
    if isinstance(w, QLineEdit):
        return bool(w.isRedoAvailable())
    return bool(w.document().isRedoAvailable())


def _clipboard_has_text() -> bool:
    """剪贴板是否有文本可粘。

    只用 ``clipboard.text()``：离屏环境下 ``mimeData()`` 实测会挂住。
    """
    try:
        from PyQt6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        return bool(cb is not None and cb.text())
    except Exception:                              # noqa: BLE001
        return False


def _can_delete(w) -> bool:
    """有选区可删，或光标未到末尾（删除键删得掉一个字符）。"""
    if not _has_content(w):
        return False
    if _has_selection(w):
        return True
    if isinstance(w, QLineEdit):
        return w.cursorPosition() < len(w.text())
    return not w.textCursor().atEnd()


# --------------------------------------------------------------------
# 动作分发（一律转发控件自身方法，不自行改文本）
# --------------------------------------------------------------------
def _do_undo(w) -> None:
    w.undo()


def _do_redo(w) -> None:
    w.redo()


def _do_cut(w) -> None:
    w.cut()


def _do_copy(w) -> None:
    w.copy()


def _do_paste(w) -> None:
    w.paste()


def _do_select_all(w) -> None:
    w.selectAll()


def _do_delete(w) -> None:
    """删除选区；无选区则删光标处一个字符。

    QLineEdit 有 ``del_()``（实测存在且行为正确）；QTextEdit 家族没有，
    走 ``QTextCursor`` —— 有选区 ``removeSelectedText()``，否则
    ``deleteChar()``（实测两者均按预期改动文本）。
    """
    if isinstance(w, QLineEdit):
        w.del_()
        return
    cur = w.textCursor()
    if cur.hasSelection():
        cur.removeSelectedText()
    else:
        cur.deleteChar()


def _shortcut_text(std) -> str:
    """标准键的首个绑定文本（如 ``Ctrl+Z``）；取不到返回空串。

    用 ``QKeySequence.keyBindings`` 现取而不是硬编码字面量，平台/版本
    差异自动跟随（实测 Undo↦Ctrl+Z、Redo↦Ctrl+Y、Delete↦Del）。
    """
    try:
        seqs = QKeySequence.keyBindings(std)
        return seqs[0].toString() if seqs else ""
    except Exception:                              # noqa: BLE001
        return ""


def _label(text: str, std) -> str:
    """菜单项文案：``名称\\t快捷键``（Qt 原生做法，右对齐渲染快捷键）。"""
    sc = _shortcut_text(std)
    return f"{text}\t{sc}" if sc else text


# ====================================================================
# 菜单构建
# ====================================================================
def build_text_menu(widget, parent=None) -> QMenu:
    """按控件能力构建中文右键菜单（只列适用项，不适用项置灰）。

    返回的 QMenu 未 exec —— 便于测试与离屏验证直接断言内容。
    """
    menu = QMenu(parent if parent is not None else widget)
    menu.setStyleSheet(get_menu_qss(resolve_theme(widget)))

    K = QKeySequence.StandardKey
    editable = not _is_read_only(widget)
    secret = _is_secret(widget)
    has_sel = _has_selection(widget)
    has_content = _has_content(widget)

    def _add(text, std, enabled, handler):
        act = menu.addAction(_label(text, std))
        act.setEnabled(bool(enabled))
        act.triggered.connect(lambda _checked=False, h=handler: h(widget))
        return act

    def _sep():
        """分组分隔线（仅当组内已有内容时才画）。

        ★ 条件化是必需的：只读 + 密码回显的控件（两条件同时成立时上方
        两组**都为空**）若无条件加分隔线，菜单会以一条悬空分隔线开头。
        当前仓内无此类控件，但判据组合可达，护栏按此钉死。
        """
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()

    # ---- 编辑历史（只读控件无历史可言，整组不出现）----
    if editable:
        _add("撤销", K.Undo, _can_undo(widget), _do_undo)
        _add("重做", K.Redo, _can_redo(widget), _do_redo)
        _sep()

    # ---- 剪贴板 ----
    # 密码 / 无回显控件：剪切、复制整组不出现（安全闸门，见模块头 §2）
    if editable and not secret:
        _add("剪切", K.Cut, has_sel, _do_cut)
    if not secret:
        _add("复制", K.Copy, has_sel, _do_copy)
    if editable:
        _add("粘贴", K.Paste, _clipboard_has_text(), _do_paste)
        _add("删除", K.Delete, _can_delete(widget), _do_delete)

    _sep()

    # ---- 全选（任何文本控件都有意义，包括只读）----
    _add("全选", K.SelectAll, has_content, _do_select_all)

    return menu


# ====================================================================
# 应用级事件过滤器
# ====================================================================
class TextContextMenuFilter(QObject):
    """应用级过滤器：接管文本控件的 ContextMenu 事件 → 弹本模块菜单。

    装在 ``QApplication`` 上，覆盖主窗口、卡片窗口、桌面便签、各
    ``GlassDialog`` 以及后续加载的插件页 —— 无需逐控件接线。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.installed = False
        self.shown = 0        # 诊断/测试用：实际弹过多少次菜单

    # ---------------- 装配 ----------------
    def install(self) -> "TextContextMenuFilter":
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self.installed = True
        return self

    def uninstall(self) -> None:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.installed = False

    # ---------------- 事件 ----------------
    def eventFilter(self, obj, event):            # noqa: N802 (Qt 命名)
        if event.type() != QEvent.Type.ContextMenu:
            return False
        widget = resolve_text_widget(obj)
        if widget is None:
            return False
        # 未启用 / 已被显式关闭右键：不接管
        if not widget.isEnabled():
            return False
        policy = widget.contextMenuPolicy()
        if policy in (Qt.ContextMenuPolicy.NoContextMenu,
                      Qt.ContextMenuPolicy.CustomContextMenu):
            return False
        return self._show(widget, event)

    def _show(self, widget, event) -> bool:
        """弹菜单并消费事件（阻止 Qt 再弹一次英文标准菜单）。

        ★ 消费机制是**事件过滤器的返回值 True**，不是 ``event.accept()``：
        ``QContextMenuEvent`` 新建时 ``isAccepted()`` 即为 True，accept()
        只是空操作。实测把返回值改成 False 后，控件自身的
        ``contextMenuEvent`` 立刻被调用（Qt 自带的英文标准菜单照弹）。
        故 ``return True`` 是唯一有效闸门，``accept()`` 仅用于把意图写在
        事件对象上（便于断言与排障）。
        """
        menu = build_text_menu(widget)
        try:
            gpos = event.globalPos()
        except Exception:                          # noqa: BLE001
            gpos = widget.mapToGlobal(widget.rect().center())
        self.shown += 1
        try:
            menu.exec(gpos)
        finally:
            menu.deleteLater()
        event.accept()
        return True


# 模块级单例引用：应用级过滤器**必须留引用**，否则被 GC 后静默失效
# （与本仓 HeartbeatWatch「实例持 QTimer 引用防 GC」同款纪律）。
_INSTANCE = None
# 装配日志只记一次的标志（模块级真实状态）。
# ⚠ 不要改成往函数对象上挂属性（``install_text_context_menu._done = True``）：
#   pyright 会报 `Cannot assign to attribute "_done" for class "FunctionType"`
#   并卡死 CI 的 pyright 闸门（2026-10-12 实测踩过），且那本就是把状态挂在
#   函数对象上的坏味道 —— 状态应与单例同处一个模块。
_LOGGED = False


def install_text_context_menu() -> TextContextMenuFilter:
    """装配入口（幂等）：返回并持有唯一过滤器实例。

    幂等且**日志只记一次**（重复调用不刷屏）；首次装配时留一条 INFO，
    便于排障时确认过滤器确实装上了。
    """
    global _INSTANCE, _LOGGED
    if _INSTANCE is None:
        _INSTANCE = TextContextMenuFilter()
    if not _INSTANCE.installed:
        _INSTANCE.install()
    if not _LOGGED:
        _LOGGED = True
        try:
            from src.logger import get_logger
            get_logger().info(
                "[右键菜单] 文本控件中文右键菜单已装配（应用级过滤器）")
        except Exception:                          # noqa: BLE001 - 日志失败不影响装配
            pass
    return _INSTANCE


def uninstall_text_context_menu() -> None:
    """卸载（测试与收尾用）。"""
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.uninstall()
