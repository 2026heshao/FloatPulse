# -*- coding: utf-8 -*-
"""
====================================================================
密码保险箱  -  FloatPulse 外置插件（vault）
====================================================================
一个**只活在本机、只信本机**的键值保险箱：自定义字段收纳账号密码与
任意机密，热键取用，不做云同步、不做自动捕捉、不碰宿主数据链。

安全设计（细节见 vault_core.py 模块头 + docs/密码保险箱插件设计-2026-10-01.md）：

  - 加密 = Windows DPAPI + PBKDF2(200k) 派生的主密码双因子，整库一次加密
  - manifest ``capabilities: []``：不联网、不读宿主数据、不写宿主数据，
    AI 插件（有 manage/网络能力）永远接触不到条目
  - secret 字段默认掩码显示，点「显示」才显形；搜索**永不**命中 secret 值
  - 复制密文后倒计时自动清剪贴板（默认 30 秒，可在插件中心设置；
    先比对再清，不误伤用户后来的复制）
  - 空闲自动锁定（默认 5 分钟，可改 1/15/永不），锁定即丢弃内存明文
  - 主密码不可找回；导出明文 JSON 必须二次确认

UI 分层照抄 kb-search 的「纯逻辑核 + Qt 壳」：core.Vault 管状态机与落盘，
本文件只管 QWidget / PluginDialog 与定时器。跨「页面重建」共享的保险箱
实例与空闲定时器放**模块级单例**（recurring-tasks 的教训：插件中心
「重新扫描」会重建页面，实例不能挂在页面上）。

已知坑（项目内实测）：热键触发的 run() 跑在原生事件过滤器里，开任何
窗口/对话框必须 ``QTimer.singleShot(0, ...)`` 延后一轮事件循环。
====================================================================
"""

import importlib.util
import os
import sys
import time

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QStackedWidget, QVBoxLayout, QWidget,
)

from src.controls import IconButton, SmoothButton
from src.plugin_api import BallAction, BallPlugin
from src.plugin_ui import (
    PluginDialog, flash_button, make_hint_label, make_section_label,
    make_separator,
)

# ---- 同目录纯逻辑模块 ----
# 加载器只把 entry 文件按路径加载，插件目录不在 sys.path 上，
# 显式按文件路径加载并加插件前缀防撞名（kb-search 同款）
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str):
    mod_name = f"floatpulse_plugin_vault_{name}"
    path = os.path.join(_HERE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load_sibling("vault_core")

PLUGIN_ID = "vault"
PAGE_KEY = f"plugin:{PLUGIN_ID}"

CLIPBOARD_CLEAR_SECONDS = 30        # 复制密文后自动清剪贴板的等待（默认；
# v1.1.0 起可在插件中心「设置」调整，本常量作旧宿主兜底）
CLEAR_SECONDS_CHOICES = ("10", "30", "60")   # 与 manifest.settings.choices 同源
PAGE_SYNC_MS = 1000                 # 页面可见时轮询锁定态的间隔
IDLE_TICK_MS = 5000                 # 空闲锁定检查的节拍

# 空闲锁定档位（分钟）——0 = 永不
IDLE_LABELS = (("永不", 0), ("1 分钟", 1), ("5 分钟", 5), ("15 分钟", 15))


def normalize_clear_seconds(raw, fallback=CLIPBOARD_CLEAR_SECONDS):
    """「复制后清剪贴板等待」生效值归一（纯函数，测试钉行为）。

    manifest 里是 enum（choices 存字符串 "10"/"30"/"60"），手改文件可能
    出整数——两种都收；不在 choices 里的值一律回落 fallback。等待秒数
    是安全相关参数：非法值宁可保守走默认，也不放大成任意秒数。
    """
    text = str(raw) if raw is not None else ""
    if text not in CLEAR_SECONDS_CHOICES:
        return fallback
    return int(text)


def clear_seconds_for(ctx, fallback=CLIPBOARD_CLEAR_SECONDS):
    """从 ctx 读清剪贴板等待的生效值；旧宿主无 get_setting 契约 /
    读取异常 → 常量兜底（不抛，复制路径绝不能因设置读取而失败）。"""
    getter = getattr(ctx, "get_setting", None) if ctx is not None else None
    if not callable(getter):
        return fallback
    try:
        raw = getter("clipboard_clear_seconds", fallback)
    except Exception:                             # noqa: BLE001
        return fallback
    return normalize_clear_seconds(raw, fallback)


def _log(ctx, level: str, msg: str) -> None:
    """ctx.logger 可能是 None（极早期加载阶段），判空防吞栈"""
    logger = getattr(ctx, "logger", None)
    if logger is None:
        return
    try:
        getattr(logger, level)(f"[{PLUGIN_ID}] {msg}")
    except Exception:                             # noqa: BLE001
        pass


# ====================================================================
# 模块级单例：保险箱实例 + 空闲锁定 + 全局活动监听
# --------------------------------------------------------------------
# 页面会被「重新扫描」重建、对话框是短命对象，只有模块级状态是稳定的
# （recurring-tasks 的 ensure_scheduler 同款思路）。定时器以 qApp 为父，
# 不挂在任何插件控件上。
# ====================================================================
_STATE = {
    "vault": None,
    "data_dir": None,
    "ctx": None,
    "idle_timer": None,
    "activity_filter": None,
    "last_activity": 0.0,
}


def touch_activity() -> None:
    _STATE["last_activity"] = time.time()


class _ActivityFilter(QObject):
    """全局活动监听：任意按键/按下/滚轮都算「有操作」（屏幕保护语义）。

    只记时间戳、永远返回 False，不影响任何事件分发。
    """

    def eventFilter(self, obj, ev):               # noqa: N802 - Qt 命名
        try:
            et = ev.type()
            if et in (ev.Type.KeyPress, ev.Type.MouseButtonPress, ev.Type.Wheel):
                touch_activity()
        except Exception:                         # noqa: BLE001
            pass
        return False


def get_vault(ctx):
    """取跨页面共享的 Vault 实例（data_dir 变了才重建）"""
    data_dir = str(getattr(ctx, "data_dir", "") or "")
    v = _STATE.get("vault")
    if v is None or _STATE.get("data_dir") != data_dir:
        v = core.Vault(data_dir)
        _STATE["vault"] = v
        _STATE["data_dir"] = data_dir
    _STATE["ctx"] = ctx
    return v


def ensure_idle_timer(ctx) -> None:
    """懒启动空闲锁定定时器（幂等；重复扫描不会新建多个定时器）"""
    app = QApplication.instance()
    if app is None:
        return
    get_vault(ctx)
    if _STATE.get("idle_timer") is None:
        timer = QTimer(app)
        timer.setInterval(IDLE_TICK_MS)
        timer.timeout.connect(_idle_tick)
        timer.start()
        _STATE["idle_timer"] = timer
        filt = _ActivityFilter()
        app.installEventFilter(filt)
        _STATE["activity_filter"] = filt
        touch_activity()
        _log(ctx, "info", "空闲锁定定时器已启动")


def _idle_tick() -> None:
    """到点检查：解锁中 + 档位>0 + 超时 → 锁定并提示"""
    v = _STATE.get("vault")
    if v is None or v.locked:
        return
    try:
        minutes = core.load_settings(_STATE.get("data_dir") or "")["idle_minutes"]
    except Exception:                             # noqa: BLE001
        minutes = core.IDLE_DEFAULT
    if minutes <= 0:
        return
    if time.time() - _STATE.get("last_activity", 0.0) < minutes * 60:
        return
    v.lock()
    ctx = _STATE.get("ctx")
    _log(ctx, "info", f"空闲 {minutes} 分钟，已自动锁定")
    if ctx is not None:
        try:
            ctx.show_toast("密码保险箱已自动锁定")
        except Exception:                         # noqa: BLE001
            pass


# ====================================================================
# 剪贴板生命周期：复制 → 等待若干秒后若原样仍在则清空
# ====================================================================
def copy_with_autoclear(text: str, seconds: int = None) -> bool:
    """写剪贴板；``seconds`` 秒后若剪贴板还是这串内容就清空。

    ``seconds`` 传 None（旧调用方 / 未传）时用默认常量
    ``CLIPBOARD_CLEAR_SECONDS``；插件页面传设置生效值（v1.1.0）。
    **先比对再清**：用户若在等待期内复制了别的东西，绝不清——那是
    用户的数据。多个等待计时器并存时，旧的发现自己不是当前内容就退场。
    """
    app = QApplication.instance()
    if app is None or not isinstance(text, str):
        return False
    cb = app.clipboard()
    cb.setText(text)
    sentinel = text

    def _clear_if_untouched():
        try:
            if cb.text() == sentinel:
                cb.clear()
        except Exception:                         # noqa: BLE001
            pass

    if seconds is None:
        seconds = CLIPBOARD_CLEAR_SECONDS
    QTimer.singleShot(max(1, int(seconds)) * 1000, _clear_if_untouched)
    return True


def _qbtn(text: str, object_name: str = "secondaryBtn", tooltip: str = "",
          checkable: bool = False, danger: bool = False):
    """文字按钮（QSS 契约站点；vault 的图标库里没有 eye/copy，文字更明确）"""
    btn = SmoothButton(text)
    btn.setObjectName(object_name)
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    if danger:
        btn.setProperty("danger", "true")
    return btn


def _entry_subtitle(entry: dict) -> str:
    tags = entry.get("tags") or []
    return " · ".join(tags) if tags else "无标签"


# ====================================================================
# 弹窗：通用确认 / 新增编辑条目 / 修改主密码 / 快速取用
# ====================================================================
class ConfirmDialog(PluginDialog):
    """通用二次确认弹窗。``danger=True`` 用红色主按钮 + 警示文案"""

    def __init__(self, ctx, title: str, message: str,
                 confirm_text: str = "确认", danger: bool = False,
                 hint: str = "", parent=None):
        super().__init__(ctx, title=title, subtitle=hint,
                         parent=parent, size=(520, 300))
        msg = QLabel(message)
        msg.setWordWrap(True)
        self.body_layout.addWidget(msg)
        self.body_layout.addStretch(1)
        name = "dangerBtn" if danger else "primaryBtn"
        self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            (confirm_text, name, self.accept),
        ])


class EntryDialog(PluginDialog):
    """新增 / 编辑条目：标题 + 自定义字段行（key/value/secret）+ 标签"""

    def __init__(self, ctx, entry=None, extra_empty_row=False, parent=None):
        title = "编辑条目" if entry else "新增条目"
        super().__init__(ctx, title=title, subtitle="字段名完全自定义：账号 / API Key / 服务器 IP 都行",
                         parent=parent, size=(640, 560))
        self._result = None
        self._rows = []                            # [{"key","value","secret","del"}]

        head = QHBoxLayout()
        head.addWidget(make_section_label("标题"))
        self._title = QLineEdit()
        self._title.setPlaceholderText("例如：GitHub 主账号")
        if entry:
            self._title.setText(str(entry.get("title", "")))
        head.addWidget(self._title, 1)
        self.body_layout.addLayout(head)

        self.body_layout.addWidget(make_section_label("字段（勾选 = 视为机密，掩码显示 + 复制倒计时）"))
        self._fields_box = QVBoxLayout()
        self._fields_box.setSpacing(6)
        self.body_layout.addLayout(self._fields_box)

        add_row = QHBoxLayout()
        self._add_row_btn = IconButton("plus", text="添加一行", object_name="secondaryBtn",
                                       tooltip="再加一个自定义字段")
        self._add_row_btn.clicked.connect(lambda: self._add_row(None, focus=True))
        add_row.addWidget(self._add_row_btn)
        add_row.addStretch(1)
        self.body_layout.addLayout(add_row)

        tags_head = QHBoxLayout()
        tags_head.addWidget(make_section_label("标签（逗号分隔，可选）"))
        tags_head.addStretch(1)
        self.body_layout.addLayout(tags_head)
        self._tags = QLineEdit()
        self._tags.setPlaceholderText("例如：开发, 生产环境")
        if entry:
            self._tags.setText(", ".join(entry.get("tags") or []))
        self.body_layout.addWidget(self._tags)

        self._warn = make_hint_label("")
        self.body_layout.addWidget(self._warn)

        existing = list((entry or {}).get("fields") or [])
        for f in existing:
            self._add_row(f, focus=False)
        if not existing or extra_empty_row:
            self._add_row(None, focus=not existing)
        if entry:
            self._result_id = entry.get("id")
        else:
            self._result_id = None

        btns = self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            ("保存", "primaryBtn", self._on_save),
        ])
        self._save_btn = btns[-1]

    def _add_row(self, field, focus=False):
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        key_in = QLineEdit(str((field or {}).get("key", "")))
        key_in.setPlaceholderText("字段名")
        key_in.setMaximumWidth(140)
        val_in = QLineEdit(str((field or {}).get("value", "")))
        val_in.setPlaceholderText("值")
        secret_chk = QCheckBox("机密")
        secret_chk.setChecked(bool((field or {}).get("secret", True)))

        def _apply_echo(checked):
            val_in.setEchoMode(QLineEdit.EchoMode.Password if checked
                               else QLineEdit.EchoMode.Normal)

        secret_chk.toggled.connect(_apply_echo)
        _apply_echo(secret_chk.isChecked())
        del_btn = IconButton("trash", object_name="iconBtn", tooltip="删除此字段",
                             danger=True)
        lay.addWidget(key_in)
        lay.addWidget(val_in, 1)
        lay.addWidget(secret_chk)
        lay.addWidget(del_btn)
        row_def = {"key": key_in, "value": val_in, "secret": secret_chk,
                   "widget": row}
        del_btn.clicked.connect(lambda: self._remove_row(row_def))
        self._rows.append(row_def)
        self._fields_box.addWidget(row)
        if focus:
            key_in.setFocus()

    def _remove_row(self, row_def):
        if row_def in self._rows:
            self._rows.remove(row_def)
            row_def["widget"].setParent(None)
            row_def["widget"].deleteLater()

    def _on_save(self):
        try:
            title = core.normalize_title(self._title.text())
            fields = [{"key": r["key"].text(), "value": r["value"].text(),
                       "secret": r["secret"].isChecked()} for r in self._rows]
            fields = core.normalize_fields(fields)
            tags = core.normalize_tags(
                [t.strip() for t in self._tags.text().split(",") if t.strip()])
        except ValueError as exc:
            self._warn.setText(f"{exc}")
            flash_button(self._save_btn, "再检查一下")
            return
        self._result = {"title": title, "fields": fields, "tags": tags}
        self.accept()

    @property
    def result_entry(self):
        return self._result

    @property
    def result_id(self):
        return self._result_id


class ChangePasswordDialog(PluginDialog):
    """修改主密码：旧密码 + 新密码 + 确认新密码"""

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, title="修改主密码",
                         subtitle="改密会换 salt 并整库重加密，原有条目不变",
                         parent=parent, size=(520, 380))
        self._ok = False
        form = QVBoxLayout()
        form.setSpacing(8)
        self._old = QLineEdit()
        self._old.setEchoMode(QLineEdit.EchoMode.Password)
        self._new = QLineEdit()
        self._new.setEchoMode(QLineEdit.EchoMode.Password)
        self._new2 = QLineEdit()
        self._new2.setEchoMode(QLineEdit.EchoMode.Password)
        for label, w in (("旧主密码", self._old), ("新主密码", self._new),
                         ("再输一遍新主密码", self._new2)):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(120)
            row.addWidget(lab)
            row.addWidget(w, 1)
            form.addLayout(row)
        self.body_layout.addLayout(form)
        self._hint = make_hint_label("")
        self.body_layout.addWidget(self._hint)
        self.body_layout.addStretch(1)
        self.add_footer([
            ("取消", "secondaryBtn", self.reject),
            ("确认修改", "primaryBtn", self._on_ok),
        ])

    def _on_ok(self):
        new = self._new.text()
        if not new:
            self._hint.setText("新主密码不能为空")
            return
        if new != self._new2.text():
            self._hint.setText("两次输入的新主密码不一致")
            return
        self._ok = True
        self.accept()

    @property
    def passwords(self):
        return self._old.text(), self._new.text()

    @property
    def confirmed(self):
        return self._ok


class QuickCopyDialog(PluginDialog):
    """快速取用（Ctrl+Alt+B）：搜索 → 选中 → 复制首个机密字段 → 倒计时清剪贴板"""

    def __init__(self, ctx, vault, parent=None):
        super().__init__(ctx, title="快速取用",
                         subtitle="输入关键词，回车复制选中条目的第一个机密字段",
                         parent=parent, size=(560, 480))
        self._vault = vault
        self._entries = []
        self._copied = False
        # 清剪贴板等待：设置生效值（旧宿主 / 脏值 → 常量兜底）
        self._clear_seconds = clear_seconds_for(self._plugin_ctx)

        self._stack = QStackedWidget(self)
        self.body_layout.addWidget(self._stack, 1)

        # ---- 页 0：锁定（先解锁才能取用）----
        lock_page = QWidget(self)
        lock_lay = QVBoxLayout(lock_page)
        lock_lay.setSpacing(10)
        self._lock_hint = make_hint_label("保险箱处于锁定状态，先解锁：")
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("主密码")
        self._pw.returnPressed.connect(self._on_unlock)
        unlock_btn = _qbtn("解锁", "primaryBtn")
        unlock_btn.clicked.connect(self._on_unlock)
        row = QHBoxLayout()
        row.addWidget(self._pw, 1)
        row.addWidget(unlock_btn)
        lock_lay.addWidget(self._lock_hint)
        lock_lay.addLayout(row)
        lock_lay.addStretch(1)
        self._lock_err = make_hint_label("")
        lock_lay.addWidget(self._lock_err)
        self._stack.addWidget(lock_page)

        # ---- 页 1：搜索 + 列表 ----
        pick_page = QWidget(self)
        pick_lay = QVBoxLayout(pick_page)
        pick_lay.setContentsMargins(0, 0, 0, 0)
        pick_lay.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("搜索标题或字段（机密值永不参与搜索）")
        self._input.textChanged.connect(self._refresh_list)
        self._input.returnPressed.connect(self._copy_selected)
        pick_lay.addWidget(self._input)
        self._list = QListWidget(pick_page)
        self._list.setObjectName("vaultQuickList")
        self._list.itemActivated.connect(lambda _item: self._copy_selected())
        pick_lay.addWidget(self._list, 1)
        self._pick_hint = make_hint_label("")
        pick_lay.addWidget(self._pick_hint)
        self._stack.addWidget(pick_page)

        btns = self.add_footer([
            ("关闭", "secondaryBtn", self.reject),
            ("复制机密字段", "primaryBtn", self._copy_selected),
        ])
        self._footer_btns = btns
        self._sync_state()

    def _sync_state(self):
        if self._vault.locked:
            self._stack.setCurrentIndex(0)
            QTimer.singleShot(0, self._pw.setFocus)
        else:
            self._stack.setCurrentIndex(1)
            self._refresh_list()
            QTimer.singleShot(0, self._input.setFocus)

    def _on_unlock(self):
        if self._vault.unlock(self._pw.text()):
            self._lock_err.setText("")
            self._pw.clear()
            touch_activity()
            ensure_idle_timer(self._plugin_ctx)
            self._sync_state()
        else:
            self._lock_err.setText("主密码错误，或数据/账户环境已变化")

    def _refresh_list(self, *_args):
        if self._vault.locked:
            return
        query = self._input.text()
        try:
            self._entries = core.sort_entries(self._vault.search(query))
        except Exception as exc:                      # noqa: BLE001 - 未解锁等
            self._pick_hint.setText(f"{exc}")
            return
        self._list.clear()
        for e in self._entries:
            has_secret = core.first_secret_value(e) is not None
            label = f"{e.get('title', '')}　·　{_entry_subtitle(e)}"
            if not has_secret:
                label += "　（无机密字段）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, e.get("id"))
            self._list.addItem(item)
        if not self._entries:
            self._pick_hint.setText("没有匹配的条目")
        else:
            self._pick_hint.setText(f"{len(self._entries)} 条")
        if self._list.count():
            self._list.setCurrentRow(0)

    def _copy_selected(self):
        if self._vault.locked:
            return
        item = self._list.currentItem()
        if item is None:
            flash_button(self._footer_btns[-1], "先选一条")
            return
        entry = self._vault.get(item.data(Qt.ItemDataRole.UserRole))
        value = core.first_secret_value(entry or {})
        if not value:
            self._pick_hint.setText("该条目没有机密字段（勾了「机密」的才参与快速取用）")
            return
        if copy_with_autoclear(value, seconds=self._clear_seconds):
            self._copied = True
            ctx = self._plugin_ctx
            try:
                if ctx is not None:
                    ctx.show_toast(
                        f"已复制机密字段，{self._clear_seconds} 秒后自动清空剪贴板")
            except Exception:                         # noqa: BLE001
                pass
            self.accept()

    @property
    def copied(self):
        return self._copied


# ====================================================================
# 页面
# ====================================================================
class VaultPage(QWidget):
    """主窗口侧栏插件页：左列表 / 右详情 / 未解锁时整页遮罩"""

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self.setObjectName("pluginPage")
        self._vault = get_vault(ctx)
        ensure_idle_timer(ctx)
        self._current_id = None
        self._shown_secret = {}                    # entry_id -> set(field 下标显形)
        # 清剪贴板等待：设置生效值（旧宿主 / 脏值 → 常量兜底）。必须先于
        # _build_ui()，页头提示与复制按钮文案都要用它
        self._clear_seconds = clear_seconds_for(ctx)
        self._build_ui()
        self._subscribe_settings()

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(PAGE_SYNC_MS)
        self._sync_timer.timeout.connect(self._sync_overlay)

    # ---------------- 插件设置（v1.1.0） ----------------
    def _subscribe_settings(self):
        """订阅插件中心的保存通知（守卫模式；旧宿主无信号时自然跳过）"""
        sig = getattr(self._ctx, "settings_changed", None)
        connect = getattr(sig, "connect", None) if sig is not None else None
        if callable(connect):
            try:
                connect(self._on_settings_changed)
            except Exception:                     # noqa: BLE001 - 订阅失败只影响实时性
                pass

    def _on_settings_changed(self, _keys=None):
        """设置保存后重读生效值：复制等待与页头文案即时跟随，无需重建页面"""
        self._clear_seconds = clear_seconds_for(self._ctx)
        # _subscribe_settings 在 _build_ui 之后调用，页头提示此时必已存在
        self._head_hint.setText(
            "只存本机：DPAPI + 主密码双因子加密；"
            f"机密值复制后 {self._clear_seconds} 秒自动清剪贴板")

    # ---------------- 界面骨架 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("密码保险箱")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        self._head_hint = make_hint_label(
            "只存本机：DPAPI + 主密码双因子加密；"
            f"机密值复制后 {self._clear_seconds} 秒自动清剪贴板")
        head.addWidget(self._head_hint)
        head.addStretch(1)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # ---- 左：搜索 + 标签 + 列表 ----
        left = QFrame()
        left.setObjectName("glassCard")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(10, 10, 10, 10)
        left_lay.setSpacing(8)
        self._search = QLineEdit()
        self._search.setObjectName("vaultSearchInput")
        self._search.setPlaceholderText("搜索标题 / 字段值（机密值不参与）")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._refresh_list())
        left_lay.addWidget(self._search)
        self._tag_combo = QComboBox()
        self._tag_combo.addItem("全部标签", None)
        self._tag_combo.currentIndexChanged.connect(lambda _i: self._refresh_list())
        left_lay.addWidget(self._tag_combo)
        self._list = QListWidget()
        self._list.setObjectName("vaultEntryList")
        self._list.currentItemChanged.connect(self._on_select)
        left_lay.addWidget(self._list, 1)
        add_btn = IconButton("plus", text="新增条目", object_name="secondaryBtn",
                             tooltip="新增一条密码 / 机密记录")
        add_btn.clicked.connect(self._on_add)
        left_lay.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(left, 5)

        # ---- 右：详情 ----
        right = QFrame()
        right.setObjectName("glassCard")
        self._detail_lay = QVBoxLayout(right)
        self._detail_lay.setContentsMargins(12, 10, 12, 10)
        self._detail_lay.setSpacing(8)
        body.addWidget(right, 7)

        # ---- 遮罩（未解锁时盖满整页）----
        self._overlay = QWidget(self)
        self._overlay.setObjectName("vaultOverlay")
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # ★ 必须带 #vaultOverlay 选择器：无选择器的内联规则会级联到全部后代，
        #   把遮罩里的 primaryBtn / 输入框一起染成半透明暗色（真截图踩过）
        self._overlay.setStyleSheet(
            "QWidget#vaultOverlay { background: rgba(15, 19, 26, 0.42); }")
        overlay_lay = QVBoxLayout(self._overlay)
        overlay_lay.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("glassCard")
        card.setFixedWidth(380)
        overlay_lay.addStretch(1)
        overlay_lay.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        overlay_lay.addStretch(1)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(10)
        self._overlay_stack = QStackedWidget()
        card_lay.addWidget(self._overlay_stack)
        self._build_wizard_page()
        self._build_unlock_page()
        self._build_corrupt_page()

    def _build_unlock_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lab = QLabel("保险箱已锁定")
        lab.setObjectName("sectionLabel")
        lay.addWidget(lab)
        lay.addWidget(make_hint_label("输入主密码解锁（空按回车即试）"))
        self._unlock_pw = QLineEdit()
        self._unlock_pw.setObjectName("vaultUnlockInput")
        self._unlock_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._unlock_pw.setPlaceholderText("主密码")
        self._unlock_pw.returnPressed.connect(self._on_unlock)
        lay.addWidget(self._unlock_pw)
        unlock_btn = _qbtn("解锁", "primaryBtn")
        unlock_btn.clicked.connect(self._on_unlock)
        lay.addWidget(unlock_btn)
        self._unlock_err = make_hint_label("")
        lay.addWidget(self._unlock_err)
        self._overlay_stack.addWidget(page)

    def _build_wizard_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lab = QLabel("创建保险箱")
        lab.setObjectName("sectionLabel")
        lay.addWidget(lab)
        lay.addWidget(make_hint_label(
            "主密码用于加密整库；它无法找回——忘了就解不开了"))
        self._wz_pw1 = QLineEdit()
        self._wz_pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self._wz_pw1.setPlaceholderText("设置主密码")
        self._wz_pw2 = QLineEdit()
        self._wz_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._wz_pw2.setPlaceholderText("再输入一遍")
        self._wz_pw2.returnPressed.connect(self._on_create)
        lay.addWidget(self._wz_pw1)
        lay.addWidget(self._wz_pw2)
        self._wz_btn = _qbtn("创建保险箱", "primaryBtn")
        self._wz_btn.clicked.connect(self._on_create)
        lay.addWidget(self._wz_btn)
        self._wz_err = make_hint_label("")
        lay.addWidget(self._wz_err)
        self._overlay_stack.addWidget(page)

    def _build_corrupt_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lab = QLabel("数据文件异常")
        lab.setObjectName("sectionLabel")
        lay.addWidget(lab)
        self._corrupt_hint = make_hint_label(
            "meta / 库文件缺失或损坏，无法解密。可以重建一个空保险箱"
            "（旧文件会自动留档为 *.corrupt-时间戳，不会被删掉）。")
        self._corrupt_hint.setWordWrap(True)
        lay.addWidget(self._corrupt_hint)
        btn = _qbtn("重建保险箱", "secondaryBtn", danger=True)
        btn.clicked.connect(self._on_corrupt_rebuild)
        lay.addWidget(btn)
        self._overlay_stack.addWidget(page)

    # ---------------- 状态同步 ----------------
    def showEvent(self, ev):                      # noqa: N802 - Qt 命名
        super().showEvent(ev)
        touch_activity()
        self._refresh_all()
        self._sync_overlay()
        self._sync_timer.start()

    def hideEvent(self, ev):                      # noqa: N802 - Qt 命名
        super().hideEvent(ev)
        self._sync_timer.stop()

    def resizeEvent(self, ev):                    # noqa: N802 - Qt 命名
        super().resizeEvent(ev)
        self._overlay.setGeometry(self.rect())

    def _sync_overlay(self):
        """轮询同步遮罩（1s 一次）：解锁态可能被空闲定时器 / 快速取用改变"""
        status = self._vault.status()
        if status == "missing":
            self._overlay_stack.setCurrentIndex(0)
            self._overlay.setVisible(True)
            self._overlay.raise_()
        elif status == "corrupt":
            self._overlay_stack.setCurrentIndex(2)
            self._overlay.setVisible(True)
            self._overlay.raise_()
        elif self._vault.locked:
            self._overlay_stack.setCurrentIndex(1)
            self._overlay.setVisible(True)
            self._overlay.raise_()
        else:
            self._overlay.setVisible(False)

    def _on_unlock(self):
        if self._vault.unlock(self._unlock_pw.text()):
            self._unlock_pw.clear()
            self._unlock_err.setText("")
            touch_activity()
            self._refresh_all()
            self._sync_overlay()
        else:
            self._unlock_err.setText("主密码错误，或数据 / 账户环境已变化")

    def _on_create(self):
        pw = self._wz_pw1.text()
        if not pw:
            self._wz_err.setText("主密码不能为空")
            return
        if pw != self._wz_pw2.text():
            self._wz_err.setText("两次输入不一致")
            return
        host = self._host_window()
        dlg = ConfirmDialog(
            self._ctx,
            title="最后确认",
            message="主密码【无法找回】：忘了就没有任何办法解开这个保险箱。\n"
                    "确认用它创建？（创建后可在页面里修改主密码）",
            confirm_text="创建",
            parent=host or self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._vault.create(pw)
        except (ValueError, core.VaultError) as exc:
            self._wz_err.setText(f"创建失败：{exc}")
            return
        self._wz_pw1.clear()
        self._wz_pw2.clear()
        self._wz_err.setText("")
        touch_activity()
        self._refresh_all()
        self._sync_overlay()
        self._toast("保险箱已创建")
        _log(self._ctx, "info", "保险箱已创建")

    def _on_corrupt_rebuild(self):
        """corrupt → 切到创建向导（core.create 会自动留档旧文件）"""
        self._overlay_stack.setCurrentIndex(0)
        self._wz_pw1.setFocus()

    # ---------------- 数据刷新 ----------------
    def _host_window(self):
        getter = getattr(self._ctx, "parent_window", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:                         # noqa: BLE001
            return None

    def _toast(self, text):
        try:
            self._ctx.show_toast(text)
        except Exception:                         # noqa: BLE001
            pass

    def _refresh_all(self):
        self._refresh_tags()
        self._refresh_list()

    def _visible_entries(self):
        """搜索框 + 标签下拉共同过滤后的条目（锁定时给空表）"""
        if self._vault.locked:
            return []
        try:
            entries = core.sort_entries(self._vault.search(self._search.text()))
        except Exception:                         # noqa: BLE001
            return []
        tag = self._tag_combo.currentData()
        if tag:
            entries = [e for e in entries if tag in (e.get("tags") or [])]
        return entries

    def _refresh_tags(self):
        keep = self._tag_combo.currentData()
        self._tag_combo.blockSignals(True)
        self._tag_combo.clear()
        self._tag_combo.addItem("全部标签", None)
        tags = []
        try:
            for e in self._vault.entries:
                for t in e.get("tags") or []:
                    if t not in tags:
                        tags.append(t)
        except Exception:                         # noqa: BLE001 - 锁定时 entries 抛错
            pass
        for t in sorted(tags):
            self._tag_combo.addItem(t, t)
        if keep:
            idx = self._tag_combo.findData(keep)
            if idx >= 0:
                self._tag_combo.setCurrentIndex(idx)
        self._tag_combo.blockSignals(False)

    def _refresh_list(self):
        if self._vault.locked:
            self._list.clear()
            self._render_detail()
            return
        entries = self._visible_entries()
        self._list.blockSignals(True)
        self._list.clear()
        for e in entries:
            item = QListWidgetItem(f"{e.get('title', '')}　·　{_entry_subtitle(e)}")
            item.setData(Qt.ItemDataRole.UserRole, e.get("id"))
            self._list.addItem(item)
        self._list.blockSignals(False)
        if entries:
            cur = self._current_id
            ids = [e.get("id") for e in entries]
            row = ids.index(cur) if cur in ids else 0
            self._list.setCurrentRow(row)
        else:
            self._current_id = None
        self._render_detail()

    def _on_select(self, cur, _prev=None):
        self._current_id = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        self._render_detail()

    def _current_entry(self):
        if self._vault.locked or not self._current_id:
            return None
        return self._vault.get(self._current_id)

    def _render_detail(self):
        """重建右侧详情面板（条目级粒度，开销可忽略）；
        行是 addLayout 加的，takeAt 后要连子控件一起清，否则重复渲染会累积驻留"""
        while self._detail_lay.count():
            item = self._detail_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)
                    sub_w = sub.widget()
                    if sub_w is not None:
                        sub_w.deleteLater()
        entry = self._current_entry()
        if entry is None:
            hint = make_hint_label("左侧选择或新增条目" if not self._vault.locked
                                   else "已锁定")
            self._detail_lay.addWidget(hint)
            self._detail_lay.addStretch(1)
            return

        head = QHBoxLayout()
        title = QLabel(str(entry.get("title", "")))
        title.setObjectName("sectionLabel")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        edit_btn = IconButton("edit", object_name="iconBtn", tooltip="编辑此条目")
        edit_btn.clicked.connect(self._on_edit)
        head.addWidget(edit_btn)
        del_btn = IconButton("trash", object_name="iconBtn", tooltip="删除此条目",
                             danger=True)
        del_btn.clicked.connect(self._on_delete)
        head.addWidget(del_btn)
        self._detail_lay.addLayout(head)

        tags = _entry_subtitle(entry)
        self._detail_lay.addWidget(make_hint_label(f"标签：{tags}　·　"
                                                   f"更新于 {entry.get('updated_at', '')}"))
        self._detail_lay.addWidget(make_separator())

        for i, f in enumerate(entry.get("fields") or []):
            self._detail_lay.addLayout(self._field_row(entry, i, f))

        self._detail_lay.addStretch(1)

        foot1 = QHBoxLayout()
        add_field_btn = _qbtn("添加字段", "secondaryBtn",
                              tooltip="给当前条目再加一个字段")
        add_field_btn.clicked.connect(lambda: self._on_edit(extra_empty_row=True))
        foot1.addWidget(add_field_btn)
        lock_btn = _qbtn("立即锁定", "primaryBtn", tooltip="丢弃内存明文并上锁")
        lock_btn.clicked.connect(self._on_lock)
        foot1.addWidget(lock_btn)
        foot1.addStretch(1)
        self._detail_lay.addLayout(foot1)

        foot2 = QHBoxLayout()
        foot2.addWidget(make_hint_label("空闲锁定"))
        self._idle_combo = QComboBox()
        for label, minutes in IDLE_LABELS:
            self._idle_combo.addItem(label, minutes)
        settings = core.load_settings(self._vault.data_dir)
        idx = [m for _l, m in IDLE_LABELS].index(settings["idle_minutes"])
        self._idle_combo.setCurrentIndex(idx)
        self._idle_combo.currentIndexChanged.connect(self._on_idle_changed)
        foot2.addWidget(self._idle_combo)
        chg_btn = _qbtn("修改主密码", "secondaryBtn")
        chg_btn.clicked.connect(self._on_change_password)
        foot2.addWidget(chg_btn)
        export_btn = _qbtn("导出明文 JSON", "secondaryBtn",
                           tooltip="换机迁移用；导出的文件是明文，用完请删除")
        export_btn.clicked.connect(self._on_export)
        foot2.addWidget(export_btn)
        foot2.addStretch(1)
        self._detail_lay.addLayout(foot2)

    def _field_row(self, entry, index, field):
        row = QHBoxLayout()
        row.setSpacing(6)
        key_lab = QLabel(str(field.get("key", "")))
        key_lab.setMinimumWidth(90)
        key_lab.setMaximumWidth(140)
        row.addWidget(key_lab)
        eid = entry.get("id")
        shown = index in self._shown_secret.get(eid, set())
        is_secret = bool(field.get("secret"))
        value = str(field.get("value", ""))
        if is_secret and not shown:
            val_lab = QLabel(core.mask_value(value))
        else:
            val_lab = QLabel(value)
        val_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(val_lab, 1)
        if is_secret:
            toggle = _qbtn("隐藏" if shown else "显示", "secondaryBtn",
                           tooltip="显形 / 掩码这个机密字段")
            toggle.setFixedWidth(64)
            # clicked(bool) 会把 checked 按位置传入 → lambda 首参接住防污染
            toggle.clicked.connect(
                lambda _checked=False, eid=eid, index=index:
                self._toggle_secret(eid, index))
            row.addWidget(toggle)
        copy_btn = _qbtn("复制", "secondaryBtn",
                         tooltip="复制到剪贴板" +
                                 (f"（{self._clear_seconds} 秒后自动清空）"
                                  if is_secret else ""))
        copy_btn.setFixedWidth(64)

        # clicked(bool) 同上：首参必须接住 checked
        def _do_copy(_checked=False, value=value, is_secret=is_secret):
            if copy_with_autoclear(value, seconds=self._clear_seconds):
                self._toast("已复制" + (
                    f"，{self._clear_seconds} 秒后自动清空剪贴板"
                    if is_secret else ""))

        copy_btn.clicked.connect(_do_copy)
        row.addWidget(copy_btn)
        return row

    # ---------------- 操作 ----------------
    def _toggle_secret(self, eid, index):
        """显形 / 掩码某个机密字段（独立成方法：真实回调路径 + 可测）"""
        set_ = self._shown_secret.setdefault(eid, set())
        if index in set_:
            set_.discard(index)
        else:
            set_.add(index)
        self._render_detail()

    def _on_lock(self):
        self._vault.lock()
        self._shown_secret = {}
        touch_activity()
        self._refresh_all()
        self._sync_overlay()

    def _on_add(self):
        if self._vault.locked:
            return
        dlg = EntryDialog(self._ctx, parent=self._host_window() or self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_entry:
            return
        try:
            entry = self._vault.add(dlg.result_entry["title"],
                                    dlg.result_entry["fields"],
                                    dlg.result_entry["tags"])
        except (ValueError, core.VaultError) as exc:
            self._toast(f"保存失败：{exc}")
            return
        self._current_id = entry.get("id")
        touch_activity()
        self._refresh_all()
        self._toast("已保存")

    def _on_edit(self, extra_empty_row=False):
        if self._vault.locked:
            return
        entry = self._current_entry()
        if entry is None:
            return
        dlg = EntryDialog(self._ctx, entry=entry,
                          extra_empty_row=extra_empty_row,
                          parent=self._host_window() or self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_entry:
            return
        data = dlg.result_entry
        try:
            updated = self._vault.update(entry["id"], title=data["title"],
                                         fields=data["fields"],
                                         tags=data["tags"])
        except (ValueError, core.VaultError) as exc:
            self._toast(f"保存失败：{exc}")
            return
        if updated is None:
            self._current_id = None
        touch_activity()
        self._refresh_all()

    def _on_delete(self):
        if self._vault.locked:
            return
        entry = self._current_entry()
        if entry is None:
            return
        host = self._host_window()
        dlg = ConfirmDialog(
            self._ctx, title="删除条目",
            message=f"确定删除「{entry.get('title', '')}」？\n此操作不可撤销。",
            confirm_text="删除", danger=True, parent=host or self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            removed = self._vault.delete(entry["id"])
        except core.VaultError as exc:
            self._toast(f"删除失败：{exc}")
            return
        if removed is not None:
            self._current_id = None
            self._shown_secret.pop(entry["id"], None)
            touch_activity()
            self._refresh_all()
            self._toast("已删除")

    def _on_idle_changed(self, index):
        minutes = self._idle_combo.itemData(index)
        if core.save_settings(self._vault.data_dir, {"idle_minutes": int(minutes or 0)}):
            label = self._idle_combo.currentText()
            self._toast(f"空闲锁定已设为：{label}")

    def _on_change_password(self):
        if self._vault.locked:
            return
        dlg = ChangePasswordDialog(self._ctx, parent=self._host_window() or self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.confirmed:
            return
        old, new = dlg.passwords
        try:
            ok = self._vault.change_password(old, new)
        except (ValueError, core.VaultError) as exc:
            self._toast(f"改密失败：{exc}")
            return
        if ok:
            self._toast("主密码已修改")
            _log(self._ctx, "info", "主密码已修改")
        else:
            self._toast("旧主密码错误")

    def _on_export(self):
        if self._vault.locked:
            return
        host = self._host_window()
        dlg = ConfirmDialog(
            self._ctx, title="导出明文 JSON",
            message="导出的文件是【明文】，任何人拿到都能看。\n"
                    "仅用于换机 / 重装系统迁移，用完请立即删除。\n继续？",
            confirm_text="导出", danger=True, parent=host or self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        default_name = f"vault-export-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出明文 JSON", default_name, "JSON (*.json)")
        if not path:
            return
        try:
            count = len(self._vault.entries)
            self._vault.export_json(path)
        except core.VaultError as exc:
            self._toast(f"导出失败：{exc}")
            return
        self._toast(f"已导出（明文！用完请删除）：{os.path.basename(path)}")
        _log(self._ctx, "info", f"已导出明文 JSON（条目数 {count}，未记内容）")


# ====================================================================
# 动作与插件
# ====================================================================
class OpenVaultAction(BallAction):
    """打开主窗口并切到保险箱页"""

    id = f"{PLUGIN_ID}.open"
    title = "密码保险箱"

    def run(self, ctx):
        # 热键路径 run() 在原生事件过滤器里执行，开窗必须延后一轮（项目铁律）
        QTimer.singleShot(0, lambda: self._go(ctx))

    def _go(self, ctx):
        try:
            ctx.open_main_window()
        except Exception as exc:                  # noqa: BLE001
            _log(ctx, "warning", f"open_main_window 失败：{exc!r}")
        host = None
        getter = getattr(ctx, "parent_window", None)
        if callable(getter):
            try:
                host = getter()
            except Exception:                     # noqa: BLE001
                host = None
        show = getattr(host, "show_plugin_page", None) if host else None
        if callable(show):
            try:
                show(PAGE_KEY)
                return
            except Exception as exc:              # noqa: BLE001
                _log(ctx, "warning", f"打开插件页失败：{exc!r}")
        try:
            ctx.show_toast("当前宿主版本不支持插件页面，请到插件中心启用")
        except Exception:                         # noqa: BLE001
            pass


class QuickCopyAction(BallAction):
    """快速取用：搜索 → 复制首个机密字段 → 倒计时自动清剪贴板"""

    id = f"{PLUGIN_ID}.quick"
    title = "快速取用"

    def run(self, ctx):
        QTimer.singleShot(0, lambda: self._go(ctx))

    def _go(self, ctx):
        ensure_idle_timer(ctx)
        v = get_vault(ctx)
        if v.status() == "missing":
            # 还没建库：直接把人领到页面里走创建向导
            OpenVaultAction().run(ctx)
            return
        host = None
        getter = getattr(ctx, "parent_window", None)
        if callable(getter):
            try:
                host = getter()
            except Exception:                     # noqa: BLE001
                host = None
        dlg = QuickCopyDialog(ctx, v, parent=host)
        dlg.exec()
        _log(ctx, "info", "快速取用完成" if dlg.copied else "快速取用取消")


class VaultPlugin(BallPlugin):
    id = PLUGIN_ID
    name = "密码保险箱"
    version = "1.1.0"

    def create_actions(self, ctx):
        return [OpenVaultAction(), QuickCopyAction()]

    def create_page(self, ctx):
        return VaultPage(ctx)
