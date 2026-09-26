# -*- coding: utf-8 -*-
"""
====================================================================
桌面便签多钉  -  sticky_notes
====================================================================
允许把任意笔记同时钉成 N 个独立桌面便签浮窗（可拖动、可缩放、
位置持久化、跟随主题）。关闭便签 = 取消钉住（不删笔记数据）。

设计要点：
  1. 几何数据（位置/尺寸/透明度/置顶）存独立文件 float_data/stickies.json，
     **不**进 notes.json、**不**给 Note 加字段——几何与笔记内容生命周期
     不同（关窗口丢几何、留笔记），分文件避免删除笔记变成跨文件级联
  2. StickyStore      : stickies.json 增删改查（纯逻辑，无 Qt Widgets，
                        可直接 pytest）；_load() 后处理做「孤儿清理」
                        （note_id 已不存在的记录直接丢弃）
  3. StickyNoteWindow : 单个便签窗。窗口 flags 与 PinWindow 完全一致
                        （Frameless + Tool + StaysOnTop，不进任务栏、
                        show 时不抢焦点）；顶部 26px 标题栏拖动；
                        右下角抓手等比例缩放（delta=max(dx,dy)）；
                        正文 QTextEdit 800ms 防抖自动保存
  4. StickyNoteManager: 生命周期（open/close/close_all/bring_all_to_front），
                        数量上限 MAX_STICKIES=20 超出拒绝；
                        新窗位置自动寻找与现有便签不重叠的屏幕空位；
                        位置/尺寸落盘 600ms 防抖 + save_now 退出兜底
====================================================================
"""

import json
import os
from datetime import datetime

from PyQt6.QtCore import QObject, QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from src.json_store import load_records
from src.constants import safe_int, NOTE_AUTOSAVE_INTERVAL_MS
from src.theme import get_colors, get_menu_qss
from src.app_paths import get_data_dir, get_screen_geometry
from src.logger import get_logger


# ====================================================================
# 数据层：Sticky 记录 + StickyStore
# ====================================================================
class Sticky:
    """单条便签几何记录的数据载体（note_id 一一对应一条笔记）"""

    def __init__(self, sticky_id, note_id, x, y, w, h,
                 opacity=100, always_on_top=True, created_at=""):
        self.sticky_id = int(sticky_id)          # 唯一主键，自增不复用
        self.note_id = int(note_id)              # 对应的笔记 id
        self.x = int(x)                          # 窗口左上角（逻辑坐标）
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.opacity = max(10, min(100, int(opacity)))   # 10~100
        self.always_on_top = bool(always_on_top)
        self.created_at = str(created_at)

    def to_dict(self):
        return {
            "sticky_id": self.sticky_id,
            "note_id": self.note_id,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "opacity": self.opacity,
            "always_on_top": self.always_on_top,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化（类型校验防损坏数据崩溃；非法值收敛到安全域）"""
        return cls(
            sticky_id=safe_int(d.get("sticky_id", 0), 0),
            note_id=safe_int(d.get("note_id", 0), 0),
            x=safe_int(d.get("x", 100), 100),
            y=safe_int(d.get("y", 100), 100),
            w=max(160, safe_int(d.get("w", 280), 280)),
            h=max(120, safe_int(d.get("h", 200), 200)),
            opacity=safe_int(d.get("opacity", 100), 100),
            always_on_top=bool(d.get("always_on_top", True)),
            created_at=str(d.get("created_at", "")),
        )


class StickyStore:
    """stickies.json 的增删改查（纯逻辑，无 Qt Widgets 依赖）。

    note_ids: 可调用对象，返回当前存在的全部 note_id 集合——
              _load() 的孤儿清理与 purge_orphans 都以它为准。
    """

    def __init__(self, json_path: str, note_ids=None):
        self._json_path = json_path
        self._stickies = []                  # 内存记录列表
        self._next_id = 1
        self._note_ids = note_ids if callable(note_ids) else (lambda: set())
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """骨架见 json_store.load_records；特有后处理 = 孤儿清理"""
        self._stickies, self._next_id = load_records(
            self._json_path, "stickies", Sticky.from_dict,
            min_next_id=lambda ss: max((s.sticky_id for s in ss), default=0) + 1,
        )
        if self.purge_orphans() > 0:
            self._save()

    def _save(self):
        data = {
            "stickies": [s.to_dict() for s in self._stickies],
            "next_id": self._next_id,
        }
        try:
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            pass  # 写盘失败不崩溃（与 NoteManager 同策略）

    # ---------------- 增删改查 ----------------
    def upsert(self, note_id: int, x: int, y: int, w: int, h: int,
               opacity: int = 100, always_on_top: bool = True,
               sticky_id=None) -> Sticky:
        """写入/更新一条便签几何。sticky_id=None 时按 note_id 复用或新建。"""
        rec = (self.get(sticky_id) if sticky_id is not None
               else self.get_by_note_id(note_id))
        if rec is not None:
            rec.x, rec.y, rec.w, rec.h = int(x), int(y), int(w), int(h)
            rec.opacity = max(10, min(100, int(opacity)))
            rec.always_on_top = bool(always_on_top)
        else:
            rec = Sticky(
                sticky_id=self._next_id, note_id=int(note_id),
                x=x, y=y, w=w, h=h,
                opacity=opacity, always_on_top=always_on_top,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
            self._stickies.append(rec)
            self._next_id += 1
        self._save()
        return rec

    def remove(self, sticky_id: int) -> bool:
        """按 sticky_id 删除记录（取消钉住时调用；笔记数据不受影响）"""
        before = len(self._stickies)
        self._stickies = [s for s in self._stickies
                          if s.sticky_id != int(sticky_id)]
        if len(self._stickies) < before:
            self._save()
            return True
        return False

    def remove_by_note_id(self, note_id: int) -> bool:
        before = len(self._stickies)
        self._stickies = [s for s in self._stickies
                          if s.note_id != int(note_id)]
        if len(self._stickies) < before:
            self._save()
            return True
        return False

    def get(self, sticky_id: int):
        for s in self._stickies:
            if s.sticky_id == int(sticky_id):
                return s
        return None

    def get_by_note_id(self, note_id: int):
        for s in self._stickies:
            if s.note_id == int(note_id):
                return s
        return None

    def get_all(self):
        return list(self._stickies)

    def clear_all(self):
        self._stickies = []
        self._save()

    def count(self) -> int:
        return len(self._stickies)

    def purge_orphans(self) -> int:
        """孤儿清理：note_id 已不存在的记录直接丢弃，返回清理条数"""
        valid = {int(nid) for nid in (self._note_ids() or set())}
        before = len(self._stickies)
        self._stickies = [s for s in self._stickies if s.note_id in valid]
        return before - len(self._stickies)


# ====================================================================
# 视图层：单个便签浮窗
# ====================================================================
class _StickyGrip(QWidget):
    """右下角缩放抓手（只负责光标形状与热区提示，事件由窗口统一处理）"""

    SIZE = 18

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._dot_color) if hasattr(self, "_dot_color") else QColor("#888888")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        # 三条斜杠提示可拖拽缩放
        for i in range(3):
            off = 4 + i * 5
            p.drawRoundedRect(self.width() - off - 6, self.height() - off - 1,
                              7, 2, 1, 1)
        p.end()


class StickyNoteWindow(QWidget):
    """单个桌面便签浮窗。

    交互：
      - 顶部 26px 标题栏拖动移动；右侧 ✕ 关闭（=取消钉住）
      - 右下角抓手等比例缩放（delta = max(dx, dy)，与 CardWindow 同思路）
      - 正文 QTextEdit 编辑后 NOTE_AUTOSAVE_INTERVAL_MS(800ms) 防抖自动保存
      - 右键菜单：标题重命名 / 置顶开关 / 透明度三档 / 删除并关闭 / 取消钉住
    信号：closed(object self)（Manager 据此摘除窗口与记录）
    """

    closed = pyqtSignal(object)
    geometry_changed = pyqtSignal()      # 移动/缩放（防抖到期后由 Manager 落盘）
    request_close = pyqtSignal()         # 用户点 ✕ / 菜单取消钉住
    request_delete = pyqtSignal()        # 菜单「删除并关闭」

    TITLE_H = 26
    MIN_W, MIN_H = 220, 160

    def __init__(self, note, store, theme: str):
        super().__init__(None)
        self._note = note                    # Note 对象（内容/标题实时引用）
        self._store = store                  # StickyStore（几何落盘）
        self._theme = theme
        self._sticky_id = None               # 关联的 Sticky.sticky_id
        self._drag_offset = None             # 标题栏拖动起点校准
        self._resizing = None                # (全局起点, start_w, start_h)
        self._loading = False                # 初始填入正文时不触发自动保存

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(False)

        self._build_ui()
        self.apply_theme(theme)

        # 几何落盘 600ms 防抖
        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.setInterval(600)
        self._geo_timer.timeout.connect(self._save_geometry)

        # 内容保存 800ms 防抖（与主窗口笔记面板同一节奏）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(NOTE_AUTOSAVE_INTERVAL_MS)
        self._save_timer.timeout.connect(self._save_content)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)

        # 顶部标题栏
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(self.TITLE_H)
        th = QHBoxLayout(self._title_bar)
        th.setContentsMargins(10, 0, 4, 0)
        th.setSpacing(4)
        self._title_label = QLabel(self._elide_title())
        self._title_label.setObjectName("stickyTitle")
        th.addWidget(self._title_label, 1)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("stickyClose")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.request_close.emit)
        th.addWidget(close_btn)
        v.addWidget(self._title_bar)

        # 正文（装载期关闭 textChanged 联动，防止初始化误触发自动保存）
        self._editor = QTextEdit(self)
        self._editor.setFrameShape(QTextEdit.Shape.NoFrame)
        self._loading = True
        self._editor.setPlainText(self._note.content)
        self._loading = False
        self._editor.textChanged.connect(self._on_text_changed)
        v.addWidget(self._editor, 1)

        # 抓手（悬浮在右下角，不参与布局）
        self._grip = _StickyGrip(self)
        self._grip.raise_()
        self._reposition_grip()

    def _reposition_grip(self):
        self._grip.move(self.width() - self._grip.width(),
                        self.height() - self._grip.height())

    def _elide_title(self):
        """标题过长省略（按像素宽度截断）"""
        text = self._note.title or "便签"
        fm = self.fontMetrics()
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, 180)

    # ---------------- 打开 / 几何 ----------------
    def apply_geometry(self, sticky: Sticky):
        """按记录还原几何（多屏安全：夹回当前可用屏幕）"""
        self._sticky_id = sticky.sticky_id
        self.resize(max(self.MIN_W, sticky.w), max(self.MIN_H, sticky.h))
        self.move(self._clamped_pos(QPoint(sticky.x, sticky.y)))
        self.setWindowOpacity(sticky.opacity / 100.0)
        self._set_always_on_top(sticky.always_on_top)

    def _clamped_pos(self, pos: QPoint) -> QPoint:
        """位置夹回可用屏幕（显示器被拔掉时坐标会漂出可视区）"""
        try:
            screen = get_screen_geometry()
            w, h = self.width(), self.height()
            x = max(screen.left(), min(pos.x(), screen.right() - w + 20))
            y = max(screen.top(), min(pos.y(), screen.bottom() - h + 20))
            return QPoint(x, y)
        except Exception:
            return pos

    def _set_always_on_top(self, on: bool):
        """切换置顶。仅在状态真变化时重设 flags（重设会重建窗口句柄、闪烁）"""
        if on != self._is_on_top():
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            if on:
                flags = flags | Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            if self.isVisible():
                self.show()

    def _is_on_top(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def sticky_id(self):
        return self._sticky_id

    def note_id(self):
        return self._note.note_id

    def title_text(self) -> str:
        return self._note.title or "便签"

    # ---------------- 保存 ----------------
    def _on_text_changed(self):
        if self._loading:
            return
        self._save_timer.start()

    def _save_content(self):
        """防抖到期：正文写回 NoteManager（title=None 时自动标题跟随刷新）"""
        text = self._editor.toPlainText()
        if text == self._note.content:
            return
        self._note.content = text
        if self._note_manager_update(text):
            self._refresh_title()

    def _note_manager_update(self, text: str) -> bool:
        """调用 note_manager.update_note（由 Manager 注入回调，避免直接依赖）"""
        return bool(self._update_cb(text)) if self._update_cb else False

    def set_update_callback(self, cb):
        """注入 content -> bool 的保存回调（Manager 注入 note_manager.update_note）"""
        self._update_cb = cb

    _update_cb = None

    def _save_geometry(self):
        if self._sticky_id is None:
            return
        self._store.upsert(
            self._note.note_id, self.x(), self.y(),
            self.width(), self.height(),
            opacity=round(self.windowOpacity() * 100),
            always_on_top=self._is_on_top(),
            sticky_id=self._sticky_id,
        )
        self.geometry_changed.emit()

    def _refresh_title(self):
        self._title_label.setText(self._elide_title())
        self._title_label.setToolTip(self._note.title or "")

    # ---------------- 主题 ----------------
    def apply_theme(self, theme_name: str):
        self._theme = theme_name
        colors = get_colors(theme_name)
        title_bg = QColor(str(colors.get("primary_deep", "#3D9E9C")))
        self._title_bg = title_bg
        grip_color = QColor(str(colors.get("on_primary", "#1B2B33")))
        grip_color.setAlpha(140)
        self._grip._dot_color = grip_color.name()
        self.setStyleSheet(f"""
            StickyNoteWindow {{
                background: {colors.get('card_bg_solid', '#FFFFFF')};
                border: 1px solid {colors.get('primary_border', '#888')};
            }}
            QWidget#stickyTitle {{
                color: {colors.get('on_primary', '#1B2B33')};
                font-size: 12px;
                background: transparent;
            }}
            QPushButton#stickyClose {{
                color: {colors.get('on_primary', '#1B2B33')};
                background: transparent;
                border: none;
                font-size: 13px;
            }}
            QPushButton#stickyClose:hover {{
                background: rgba(255, 255, 255, 60);
                border-radius: 4px;
            }}
            QTextEdit {{
                background: {colors.get('card_bg_solid', '#FFFFFF')};
                color: {colors.get('text', '#2C3E50')};
                border: none;
                font-size: 13px;
                padding: 6px 8px;
                selection-background-color: {colors.get('primary_a30', '#88CCCB')};
            }}
        """)
        self._title_bar.setStyleSheet(
            f"background: {title_bg.name()};")
        self._grip.update()

    # ---------------- 鼠标：拖动 / 缩放 ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            gpos = event.globalPosition().toPoint()
            if self._in_grip(event.position().toPoint()):
                self._resizing = (gpos, self.width(), self.height())
                return
            if event.position().toPoint().y() <= self.TITLE_H:
                self._drag_offset = gpos - self.frameGeometry().topLeft()
                return
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing is not None:
            gpos = event.globalPosition().toPoint()
            delta = max(gpos.x() - self._resizing[0].x(),
                        gpos.y() - self._resizing[0].y())
            self.resize(max(self.MIN_W, self._resizing[1] + delta),
                        max(self.MIN_H, self._resizing[2] + delta))
            self._reposition_grip()
            self._geo_timer.start()
            return
        if self._drag_offset is not None:
            self.move(self._clamped_pos(
                event.globalPosition().toPoint() - self._drag_offset))
            self._reposition_grip()
            self._geo_timer.start()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing is not None:
            self._resizing = None
            self._geo_timer.stop()
            self._save_geometry()        # 松手立即落盘一次
            return
        if self._drag_offset is not None:
            self._drag_offset = None
            self._geo_timer.stop()
            self._save_geometry()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, _event):
        self._reposition_grip()

    def _in_grip(self, local_pos: QPoint) -> bool:
        return (local_pos.x() >= self.width() - _StickyGrip.SIZE
                and local_pos.y() >= self.height() - _StickyGrip.SIZE)

    # ---------------- 右键菜单 ----------------
    def _show_context_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet(get_menu_qss(self._theme))

        act_rename = menu.addAction("✏️ 标题重命名...")
        act_top = menu.addAction("📌 取消置顶" if self._is_on_top()
                                 else "📌 窗口置顶")
        act_top.setCheckable(True)
        act_top.setChecked(self._is_on_top())
        # 透明度三档（spec 允许三档或 Stepper 滑块；菜单里用三档更轻）
        op_menu = menu.addMenu("🌗 透明度")
        op_actions = {}
        for label, value in (("不透明", 100), ("较透明", 80), ("半透明", 60)):
            a = op_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(round(self.windowOpacity() * 100) == value)
            op_actions[a] = value
        menu.addSeparator()
        act_unpin = menu.addAction("▢ 取消钉住")
        act_delete = menu.addAction("🗑 删除笔记并关闭")
        action = menu.exec(global_pos)

        if action == act_rename:
            self._rename_dialog()
        elif action == act_top:
            self._set_always_on_top(not self._is_on_top())
            self._geo_timer.stop()
            self._save_geometry()
        elif action in op_actions:
            self.setWindowOpacity(op_actions[action] / 100.0)
            self._geo_timer.stop()
            self._save_geometry()
        elif action == act_unpin:
            self.request_close.emit()
        elif action == act_delete:
            self.request_delete.emit()

    def _rename_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("便签标题")
        dlg.setWindowFlags(dlg.windowFlags()
                           | Qt.WindowType.WindowStaysOnTopHint)
        dlg.setFixedSize(360, 140)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(10)
        edit = QLineEdit(self._note.title or "")
        v.addWidget(edit)
        h = QHBoxLayout()
        h.addStretch()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        h.addWidget(ok_btn)
        h.addWidget(cancel_btn)
        v.addLayout(h)
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)

        def _apply():
            title = edit.text().strip()
            if title:
                self._rename_cb(title)
                self._refresh_title()
        ok_btn.clicked.connect(_apply)
        dlg.exec()

    def set_rename_callback(self, cb):
        """注入 title -> bool 的改名回调（Manager 注入 note_manager.update_title）"""
        self._rename_cb = cb

    _rename_cb = None

    def closeEvent(self, event):
        self._geo_timer.stop()
        self._save_timer.stop()
        self.closed.emit(self)
        super().closeEvent(event)

    def close_as_sticky(self):
        """取消钉住：移除几何记录后关窗（笔记数据不动）"""
        if self._sticky_id is not None:
            self._store.remove(self._sticky_id)
        self.close()


# ====================================================================
# 生命周期管理：StickyNoteManager
# ====================================================================
class StickyNoteManager(QObject):
    """便签生命周期：open/close/close_all/bring_all_to_front。

    - 便签与笔记一一对应：重复 open 同一 note_id → 复用并 raise 激活
    - 数量上限 MAX_STICKIES，超出拒绝（返回 (False, "limit")，由调用方提示）
    - 新窗位置自动寻找与现有便签不重叠的屏幕空位
    """

    MAX_STICKIES = 20
    DEFAULT_W, DEFAULT_H = 280, 200
    CASCADE = 48                # 找不到空位时的兜底级联步长

    changed = pyqtSignal()      # 便签集合变化（托盘子菜单据此刷新）

    def __init__(self, note_manager, store: StickyStore, theme: str = "light",
                 parent=None):
        super().__init__(parent)
        self._note_manager = note_manager
        self._store = store
        self._theme = theme
        self._windows = {}       # sticky_id -> StickyNoteWindow（存活窗口）

    # ---------------- 查询 ----------------
    def count(self) -> int:
        return len(self._windows)

    def get_all(self):
        """[(sticky_id, title, note_id), ...]（托盘子菜单用，按 sticky_id 排序）"""
        out = []
        for sid in sorted(self._windows):
            w = self._windows[sid]
            out.append((sid, w.title_text(), w.note_id()))
        return out

    def window_for_note(self, note_id: int):
        for w in self._windows.values():
            if w.note_id() == int(note_id):
                return w
        return None

    # ---------------- 打开 ----------------
    def open(self, note_id: int):
        """打开（或复用）指定笔记的桌面便签。返回 (ok, reason)。

        reason: "ok" / "limit"（数量上限）/ "missing"（笔记不存在）
        """
        # 已开 → 复用并前置激活（用户显式操作，允许激活）
        for w in self._windows.values():
            if w.note_id() == int(note_id):
                w.raise_()
                w.activateWindow()
                return True, "ok"

        if self.count() >= self.MAX_STICKIES:
            return False, "limit"

        note = self._note_manager.get_note(int(note_id))
        if note is None:
            return False, "missing"

        # 几何：有历史记录则还原（夹回屏幕），否则找不重叠空位
        sticky = self._store.get_by_note_id(note_id)
        win = StickyNoteWindow(note, self._store, self._theme)
        win.set_update_callback(
            lambda text, nid=int(note_id): self._note_manager.update_note(nid, text))
        win.set_rename_callback(
            lambda title, nid=int(note_id): self._note_manager.update_title(nid, title))
        win.request_close.connect(
            lambda sid=win: self._on_window_close(sid))
        win.request_delete.connect(
            lambda nid=int(note_id): self._on_window_delete(nid))
        if sticky is not None:
            win.apply_geometry(sticky)
            # 记录的尺寸夹回安全域（记录可能来自更大屏幕）
            if win.width() < win.MIN_W or win.height() < win.MIN_H:
                win.resize(win.MIN_W, win.MIN_H)
        else:
            # 新钉：先落一条几何记录（拿到 sticky_id），窗口按记录应用
            pos = self._find_free_position(self.DEFAULT_W, self.DEFAULT_H)
            rec = self._store.upsert(
                int(note_id), pos.x(), pos.y(),
                self.DEFAULT_W, self.DEFAULT_H)
            win.apply_geometry(rec)
        self._windows[win.sticky_id()] = win
        win.closed.connect(self._on_closed)
        win.show()          # WA_ShowWithoutActivating：不抢当前焦点
        self.changed.emit()
        return True, "ok"

    def _find_free_position(self, w: int, h: int) -> QPoint:
        """在可用屏幕内寻找与现有便签都不重叠的落点（从左上向右下扫描）"""
        try:
            screen = get_screen_geometry()
        except Exception:
            return QPoint(100, 100)
        existing = [QRect(win.x(), win.y(), win.width(), win.height())
                    for win in self._windows.values() if win.isVisible()]
        margin, step = 12, self.CASCADE
        y = screen.top() + margin
        while y + h <= screen.bottom():
            x = screen.left() + margin
            while x + w <= screen.right():
                r = QRect(x, y, w, h)
                if not any(r.intersects(e) for e in existing):
                    return QPoint(x, y)
                x += step
            y += step
        # 屏幕塞满 → 右下角级联兜底
        return QPoint(screen.right() - w - margin,
                      screen.bottom() - h - margin)

    # ---------------- 关闭 ----------------
    def close(self, sticky_id: int) -> bool:
        w = self._windows.get(int(sticky_id))
        if w is None:
            return False
        w.close_as_sticky()      # closed 信号 → _on_closed 摘表
        return True

    def close_by_note(self, note_id: int) -> bool:
        w = self.window_for_note(note_id)
        if w is None:
            return False
        w.close_as_sticky()
        return True

    def close_all(self):
        for w in list(self._windows.values()):
            w.close_as_sticky()

    def bring_all_to_front(self):
        for w in self._windows.values():
            w.raise_()

    def raise_sticky(self, sticky_id: int) -> bool:
        """把指定便签提到前台（托盘子菜单点击项）"""
        w = self._windows.get(int(sticky_id))
        if w is None:
            return False
        w.raise_()
        return True

    def _on_closed(self, win):
        """窗口关闭（任何途径）→ 摘除存活表 + 广播变化"""
        sid = win.sticky_id()
        if sid in self._windows:
            del self._windows[sid]
        win.deleteLater()
        self.changed.emit()

    def _on_window_close(self, win):
        win.close_as_sticky()

    def _on_window_delete(self, note_id: int):
        """菜单「删除笔记并关闭」：先删数据再关窗"""
        try:
            self._note_manager.delete_note(int(note_id))
        except Exception:
            pass
        self.close_by_note(note_id)

    # ---------------- 主题 / 落盘 / 校验 ----------------
    def apply_theme(self, theme_name: str):
        """主题广播：全部存活窗口同步换肤（新窗口用最新主题）"""
        self._theme = theme_name
        for w in self._windows.values():
            w.apply_theme(theme_name)

    def save_now(self):
        """退出兜底：把存活窗口的当前几何立即写盘"""
        for w in self._windows.values():
            try:
                w._save_geometry()
            except Exception:
                pass

    def validate_open_windows(self):
        """笔记被删除后：对应便签窗口自动关闭（数据没了窗口不留）"""
        for w in list(self._windows.values()):
            if self._note_manager.get_note(w.note_id()) is None:
                w.close_as_sticky()


# ====================================================================
# 工厂：默认路径
# ====================================================================
def default_stickies_path() -> str:
    """stickies.json 默认路径（float_data/stickies.json）"""
    return os.path.join(get_data_dir(), "stickies.json")
