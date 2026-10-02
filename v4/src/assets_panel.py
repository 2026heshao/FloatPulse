# -*- coding: utf-8 -*-
"""
====================================================================
临时素材面板  -  AssetsPanel
====================================================================
2026-09-24 素材可视化改造：文字列表 → 缩略图网格（与小卡片素材页统一）。
- 图片素材：圆角真缩略图（QImageReader 解码期缩放，大图也不卡）；
- 文件素材：类型 emoji + 文件底卡；
- 每格：缩略图 + 文件名（中部省略）+ 大小 · 时间；
- 交互不变：多选、右键 打开/另存为/删除（新增多选批量删除）、双击打开、
  清空确认、打开素材文件夹；
- 缩略图按 asset_id 惰性生成并缓存（读失败的记 False 哨兵防反复重读），
  refresh 时清理已删除素材的缓存。
遵循项目铁律：自定义网格内容用 QStyledItemDelegate 自绘，不用 setItemWidget。
====================================================================
"""

import os
import shutil

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QFileDialog,
    QStackedWidget, QStyledItemDelegate, QStyle,
)
from PyQt6.QtCore import (
    QEasingCurve, QObject, QRunnable, QRectF, QSize, Qt, QThreadPool,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QImageReader, QPainter, QPainterPath, QPen, QPixmap,
)
from src.constants import DATETIME_MIN_LEN
from src.theme import DEFAULT_THEME, get_colors
from src.controls import SmoothButton, EmptyState, IconButton, PageTitle
from src import motion

# 非图片文件的类型图标（与 card_window._AssetItemWidget 同一套语义）
EXT_ICON = {
    ".txt": "📄", ".md": "📝", ".pdf": "📕", ".doc": "📘", ".docx": "📘",
    ".xls": "📗", ".xlsx": "📗", ".ppt": "📙", ".pptx": "📙",
    ".zip": "📦", ".rar": "📦", ".7z": "📦",
    ".mp3": "🎵", ".wav": "🎵", ".mp4": "🎬", ".avi": "🎬", ".mov": "🎬",
    ".py": "🐍", ".js": "📜", ".json": "📋", ".html": "🌐",
}

# 缩略图缓存哨兵:_PENDING=后台生成中(画占位图),False=确认不可预览。
# 与 QPixmap 同存一个 dict,用 object() 保证不会和任何合法值撞车。
_PENDING = object()


def _decode_image_thumb(path: str, out_w: int, out_h: int):
    """在工作线程解码并裁切缩略图(QImage 线程安全;QPixmap 只能主线程建)。

    与旧同步路径同参数同产物:解码期按 2 倍目标缩放大图 → Smooth 缩放
    到目标 → 居中裁切。失败返回 None(调用方落 False 哨兵防反复重读)。
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    tw, th = out_w * 2, out_h * 2
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scale = max(tw / size.width(), th / size.height())
        if scale < 1.0:   # 大图在解码期先缩，避免整图载入内存
            reader.setScaledSize(QSize(int(size.width() * scale),
                                       int(size.height() * scale)))
    raw = reader.read()
    if raw.isNull():
        return None
    img = raw.scaled(out_w, out_h,
                     Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    x = (img.width() - out_w) // 2
    y = (img.height() - out_h) // 2
    return img.copy(x, y, out_w, out_h)


class _ThumbSignals(QObject):
    """工作线程 → 主线程 的到货信号(跨线程 emit 自动 Queued)。"""

    ready = pyqtSignal(int, int, object)     # asset_id, epoch, QImage|None


class _ThumbJob(QRunnable):
    """线程池里的解码任务:只做纯计算,结果经信号回主线程。"""

    def __init__(self, asset_id, epoch, path, w, h, signals):
        super().__init__()
        self._asset_id, self._epoch = asset_id, epoch
        self._path, self._w, self._h = path, w, h
        self._signals = signals

    def run(self):
        img = None
        try:
            if self._path and os.path.exists(self._path):
                img = _decode_image_thumb(self._path, self._w, self._h)
        except (OSError, ValueError, RuntimeError):
            img = None
        self._signals.ready.emit(self._asset_id, self._epoch, img)


class _AssetThumbDelegate(QStyledItemDelegate):
    """素材网格单元：圆角缩略图 + 文件名 + 大小·时间（可视化改造核心）"""

    DEFAULT_THUMB_W = 128   # 默认缩略图宽（一行 4 个；设置项 asset_thumb_size 可调）
    THUMB_RATIO = 100 / 152  # 高/宽比，沿用原 152x100 的视觉比例
    PAD = 10          # 格内左右留白

    def __init__(self, host, thumb_cache: dict, loader=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._thumbs = thumb_cache   # id -> QPixmap | False | _PENDING
        self._loader = loader        # 未命中回调(面板的异步派发);None=旧同步路径
        self._fade_values = {}       # asset_id -> 0.0~1.0(淡入进度,面板维护)
        config = getattr(host, "_config", None)
        init_w = (int(config.get("asset_thumb_size", self.DEFAULT_THUMB_W))
                  if config is not None else self.DEFAULT_THUMB_W)
        self.set_thumb_size(init_w)

    def set_thumb_size(self, width: int):
        """按缩略图宽度推导全套单元尺寸（比例与留白固定）"""
        w = max(60, int(width))
        self.THUMB_W = w
        self.THUMB_H = max(60, round(w * self.THUMB_RATIO))
        self.CELL_W = w + self.PAD * 2
        # 顶 8 + 缩略图 + 名 4+20 + 元 4+16 + 底 12
        self.CELL_H = self.THUMB_H + 64

    # ---------------- 颜色 ----------------
    def _colors(self) -> dict:
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        return get_colors(theme)

    # ---------------- 缩略图 ----------------
    def _decode_sync(self, asset):
        """旧同步解码路径(仅 loader=None 的独立使用场景;面板管线不走这里)。"""
        pix = None
        img = _decode_image_thumb(asset.stored_path, self.THUMB_W, self.THUMB_H)
        if img is not None:
            pix = QPixmap.fromImage(img)
        return pix

    def _thumb(self, asset):
        """取缩略图:命中即回;未命中画占位并(图片时)转后台生成。

        ★ 本方法在 paint() 里跑——**绝不在这里解码**(首开/滚动的卡顿
        根因)。未命中先落 _PENDING 占位(本帧画类型图标),由 loader
        派发后台线程,到货后入缓存 + 淡入 + 单格局部重绘。
        loader=None 时保持旧同步路径(无面板管线的独立使用场景)。
        """
        cached = self._thumbs.get(asset.asset_id)
        if cached is not None:
            return cached if (cached is not False
                              and cached is not _PENDING) else None
        if (asset.is_image and asset.stored_path
                and os.path.exists(asset.stored_path)):
            if self._loader is None:
                pix = self._decode_sync(asset)
                self._thumbs[asset.asset_id] = pix if pix is not None else False
                return pix
            self._thumbs[asset.asset_id] = _PENDING
            self._loader(asset)
            return None
        self._thumbs[asset.asset_id] = False   # 非图片/文件失效:不可预览
        return None

    # ---------------- 绘制 ----------------
    def sizeHint(self, option, index) -> QSize:
        return QSize(self.CELL_W, self.CELL_H)

    def paint(self, painter: QPainter, option, index):
        asset = index.data(Qt.ItemDataRole.UserRole)
        if asset is None:
            return
        colors = self._colors()
        rect = QRectF(option.rect).adjusted(6, 6, -6, -6)
        # 注意：PyQt6 6.7 的 style state 成员是 QStyle.StateFlag.*（不是 QStyle.State.*）
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ---- 单元背景卡（选中/hover/常态）----
        if selected:
            painter.setPen(QPen(QColor(colors["primary"]), 1.4))
            painter.setBrush(QColor(colors["primary_a12"]))
        elif hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["primary_a08"]))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)

        exists = bool(asset.stored_path) and os.path.exists(asset.stored_path)
        tw, th = self.THUMB_W, self.THUMB_H
        trect = QRectF(rect.left() + (rect.width() - tw) / 2.0,
                       rect.top() + 8, tw, th)

        # ---- 缩略图 / 类型图标 ----
        pix = self._thumb(asset) if exists else None
        if pix is not None:
            fade = self._fade_values.get(asset.asset_id)
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(trect, 8, 8)
            painter.setClipPath(clip)
            if fade is not None and fade < 1.0:
                painter.setOpacity(max(0.0, float(fade)))   # 到货淡入
            painter.drawPixmap(trect.toRect(), pix)
            painter.restore()
            painter.setPen(QPen(QColor(colors["hair"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(trect, 8, 8)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["panel_fill"]))
            painter.drawRoundedRect(trect, 8, 8)
            if not exists:
                glyph, sub = "⚠️", "文件已失效"
            elif asset.is_image:
                glyph, sub = "🖼️", "图片"
            else:
                ext = os.path.splitext(asset.original_name)[1].lower()
                glyph, sub = EXT_ICON.get(ext, "📄"), (ext or "文件").lstrip(".").upper()
            painter.setPen(QColor(colors["text_secondary"]))
            f = painter.font()
            f.setPointSize(24)
            painter.setFont(f)
            painter.drawText(trect, Qt.AlignmentFlag.AlignCenter, glyph)
            sub_f = painter.font()
            sub_f.setPointSize(8)
            painter.setFont(sub_f)
            painter.drawText(QRectF(trect.left(), trect.bottom() - 20,
                                    trect.width(), 16),
                             Qt.AlignmentFlag.AlignHCenter, sub)

        # ---- 文件名 ----
        painter.setFont(option.font)
        fm = painter.fontMetrics()
        painter.setPen(QColor(colors["text"]))
        name = fm.elidedText(asset.original_name,
                             Qt.TextElideMode.ElideMiddle,
                             int(rect.width() - self.PAD * 2))
        name_y = trect.bottom() + 6 + fm.ascent()
        painter.drawText(QRectF(rect.left() + self.PAD, trect.bottom() + 4,
                                rect.width() - self.PAD * 2, 20),
                         Qt.AlignmentFlag.AlignHCenter, name)

        # ---- 大小 · 时间 ----
        added = asset.added_time or ""
        time_display = (added[:DATETIME_MIN_LEN]
                        if len(added) >= DATETIME_MIN_LEN else added)
        meta_f = painter.font()
        meta_f.setPointSize(max(8, meta_f.pointSize() - 1))
        painter.setFont(meta_f)
        painter.setPen(QColor(colors["text_secondary"]))
        meta = f"{asset.size_display()} · {time_display}"
        meta_fm = painter.fontMetrics()
        meta = meta_fm.elidedText(meta, Qt.TextElideMode.ElideMiddle,
                                  int(rect.width() - self.PAD * 2))
        painter.drawText(QRectF(rect.left() + self.PAD, name_y + 4,
                                rect.width() - self.PAD * 2, 16),
                         Qt.AlignmentFlag.AlignHCenter, meta)
        painter.restore()


class AssetsPanel(QWidget):
    """临时素材面板（缩略图网格）"""

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._temp_asset_manager = host._temp_asset_manager
        self._thumb_cache = {}
        # ---- 异步缩略图管线(丝滑化):paint 永不解码 ----
        self._pending = set()        # 在途 asset_id(防重复派发)
        self._epoch = 0              # 缩略图尺寸代际(变尺寸后旧结果按代作废)
        self._valid_ids = set()
        self._items_by_id = {}
        self._fade_anims = {}
        self._signals = _ThumbSignals(self)
        self._signals.ready.connect(self._on_thumb_ready)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)   # 解码不抢满核,给 UI 留流畅度
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # ---- 顶部标题 + 计数 ----
        header = QHBoxLayout()
        title = PageTitle("assets", "临时素材", self._host)
        header.addWidget(title)
        header.addStretch()
        self._asset_count_label = QLabel("共 0 条 / 上限 10")
        self._asset_count_label.setObjectName("hintLabel")
        header.addWidget(self._asset_count_label)
        v.addLayout(header)

        # ---- 提示 ----
        hint = QLabel("💡 拖拽图片/文件到悬浮球即可自动复制保存到此处")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        open_folder_btn = SmoothButton("📁 打开素材文件夹")
        open_folder_btn.setObjectName("secondaryBtn")
        open_folder_btn.clicked.connect(self._on_open_folder)
        toolbar.addWidget(open_folder_btn)

        refresh_btn = IconButton("refresh", text="刷新", icon_size=14,
                                 object_name="secondaryBtn")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        toolbar.addStretch()

        clear_btn = IconButton("trash", text="清空全部", icon_size=14,
                               object_name="dangerBtn")
        clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(clear_btn)

        v.addLayout(toolbar)

        # ---- 素材网格（+ 空态）----
        self._stack = QStackedWidget()

        self._asset_list = QListWidget()
        self._asset_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._asset_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._asset_list.setMovement(QListWidget.Movement.Static)
        self._asset_list.setUniformItemSizes(True)
        self._asset_list.setMouseTracking(True)
        self._asset_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._asset_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._asset_list.customContextMenuRequested.connect(self._on_context_menu)
        self._asset_list.itemDoubleClicked.connect(self._on_double_click)
        self._thumb_delegate = _AssetThumbDelegate(
            self._host, self._thumb_cache, loader=self._request_thumb)
        self._asset_list.setItemDelegate(self._thumb_delegate)
        self._sync_scroll_step()
        self._stack.addWidget(self._asset_list)

        self._empty_state = EmptyState(
            "assets", "暂无素材",
            "拖拽图片/文件到悬浮球即可自动收录\n素材默认保留 30 天，注意及时归档")
        self._stack.addWidget(self._empty_state)

        v.addWidget(self._stack, 1)

    # ---- 主题联动 ----
    def apply_theme(self):
        """主题切换后重绘（delegate 每帧动态取色，无需重建）"""
        self._asset_list.viewport().update()
        # 空态图标是自绘位图，不在 QSS 管辖内（A3）
        self._empty_state.apply_theme(getattr(self._host, "current_theme",
                                              None))

    # ---- 异步缩略图管线(丝滑化核心)----
    def _request_thumb(self, asset):
        """delegate 未命中回调:派发后台解码(防重;文件已失就地判负)。"""
        aid = asset.asset_id
        if aid in self._pending:
            return
        self._pending.add(aid)
        if not (asset.stored_path and os.path.exists(asset.stored_path)):
            self._thumb_cache[aid] = False
            self._pending.discard(aid)
            self._update_cell(aid)
            return
        self._pool.start(_ThumbJob(aid, self._epoch, asset.stored_path,
                                   self._thumb_delegate.THUMB_W,
                                   self._thumb_delegate.THUMB_H,
                                   self._signals))

    def _on_thumb_ready(self, asset_id: int, epoch: int, img):
        """后台解码到货(主线程):入缓存 + 淡入 + 单格局部重绘。"""
        self._pending.discard(asset_id)
        if epoch != self._epoch or asset_id not in self._valid_ids:
            return                      # 尺寸换代 / 素材已删:结果作废
        if img is None:
            self._thumb_cache[asset_id] = False   # 读取失败哨兵,防反复重读
        else:
            self._thumb_cache[asset_id] = QPixmap.fromImage(img)
            self._begin_fade(asset_id)
        self._update_cell(asset_id)

    def _begin_fade(self, asset_id: int):
        """到货淡入(140ms OutCubic);reduce_motion 开 → 0ms 瞬显。"""
        ms = motion.duration(140, 1.0)
        if ms <= 0:
            self._thumb_delegate._fade_values.pop(asset_id, None)
            return
        old = self._fade_anims.pop(asset_id, None)
        if old is not None:
            old.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(
            lambda v, aid=asset_id: self._on_fade_tick(aid, v))
        anim.finished.connect(
            lambda aid=asset_id: self._on_fade_done(aid))
        self._thumb_delegate._fade_values[asset_id] = 0.0
        self._fade_anims[asset_id] = anim
        anim.start()

    def _on_fade_tick(self, asset_id: int, value):
        self._thumb_delegate._fade_values[asset_id] = float(value)
        self._update_cell(asset_id)

    def _on_fade_done(self, asset_id: int):
        self._thumb_delegate._fade_values.pop(asset_id, None)
        self._fade_anims.pop(asset_id, None)
        self._update_cell(asset_id)

    def _update_cell(self, asset_id: int):
        """单格局部重绘(到货/淡入每帧),不惊动整页。"""
        item = self._items_by_id.get(asset_id)
        if item is None:
            return
        rect = self._asset_list.visualItemRect(item)
        if rect.isValid():
            self._asset_list.viewport().update(rect)

    # ---- 缩略图尺寸（设置项联动）----
    def _sync_scroll_step(self):
        """纵向滚动步长 = 约 1/3 行（Qt 默认按一整行走，跨度太大不便细看）"""
        sb = self._asset_list.verticalScrollBar()
        if sb is not None:
            sb.setSingleStep(max(32, self._thumb_delegate.CELL_H // 3))

    def apply_thumb_size(self, size: int):
        """设置页改动缩略图尺寸：更新单元尺寸 + 清缓存（旧图是按旧尺寸裁的）+ 重排"""
        size = max(60, int(size))
        if size == self._thumb_delegate.THUMB_W:
            return
        self._thumb_delegate.set_thumb_size(size)
        self._epoch += 1            # 在途结果按代际作废(防旧尺寸回填)
        self._pending.clear()
        self._thumb_cache.clear()
        self._thumb_delegate._fade_values.clear()
        for anim in self._fade_anims.values():
            anim.stop()
        self._fade_anims.clear()
        self._sync_scroll_step()
        self.refresh()

    # ---- 刷新入口 ----
    def refresh(self):
        """刷新素材网格显示"""
        if self._temp_asset_manager is None:
            return
        self._temp_asset_manager.refresh()

        assets = self._temp_asset_manager.get_all_assets()
        # 清理已删除素材的缩略图缓存
        valid_ids = {a.asset_id for a in assets}
        self._valid_ids = valid_ids
        for key in list(self._thumb_cache):
            if key not in valid_ids:
                del self._thumb_cache[key]
        for key in list(self._thumb_delegate._fade_values):
            if key not in valid_ids:
                del self._thumb_delegate._fade_values[key]

        self._asset_list.clear()
        self._items_by_id = {}
        for a in assets:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, a)            # Asset 对象（delegate 用）
            item.setData(Qt.ItemDataRole.UserRole + 1, a.asset_id)
            item.setToolTip(
                f"原文件名: {a.original_name}\n"
                f"类型: {'图片' if a.is_image else '文件'}\n"
                f"大小: {a.size_display()}\n"
                f"添加时间: {a.added_time}\n"
                f"存储路径: {a.stored_path}")
            self._asset_list.addItem(item)
            self._items_by_id[a.asset_id] = item

        count = self._temp_asset_manager.count()
        max_assets = self._temp_asset_manager._max_assets
        self._asset_count_label.setText(f"共 {count} 条 / 上限 {max_assets}")
        self._stack.setCurrentIndex(1 if count == 0 else 0)

    # ---- 右键菜单 ----
    def _on_context_menu(self, pos):
        item = self._asset_list.itemAt(pos)
        if not item:
            return
        aid = item.data(Qt.ItemDataRole.UserRole + 1)
        selected_ids = [i.data(Qt.ItemDataRole.UserRole + 1)
                        for i in self._asset_list.selectedItems()]
        n = len(selected_ids) if aid in selected_ids else 1

        menu = QMenu(self)
        menu.setStyleSheet(self._host._container.styleSheet())
        act_open = menu.addAction("📂 打开")
        act_save_as = menu.addAction("💾 另存为...")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 删除" if n <= 1 else f"🗑 删除（{n} 个）")
        action = menu.exec(self._asset_list.mapToGlobal(pos))
        if action == act_open:
            self._open(aid)
        elif action == act_save_as:
            self._save_as(aid)
        elif action == act_delete:
            targets = selected_ids if n > 1 else [aid]
            removed = sum(1 for a_id in targets
                          if self._temp_asset_manager.delete_asset(a_id))
            if removed:
                self.refresh()
                self._host.data_changed.emit("asset")

    def _open(self, asset_id: int) -> bool:
        """打开素材；失败时提示（供双击/右键共用）"""
        if self._temp_asset_manager.open_asset(asset_id):
            return True
        QMessageBox.warning(self, "打开失败", "无法打开此素材，文件可能已被删除。")
        return False

    def _on_double_click(self, item):
        """双击素材 → 用系统默认程序打开"""
        self._open(item.data(Qt.ItemDataRole.UserRole + 1))

    def _save_as(self, asset_id: int):
        """另存为：用 QFileDialog 选目标位置，复制一份过去"""
        asset = self._temp_asset_manager.get_asset(asset_id)
        if not asset:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "另存为", asset.original_name, "All Files (*.*)"
        )
        if not target:
            return
        try:
            shutil.copy2(asset.stored_path, target)
            QMessageBox.information(self, "已另存为", f"文件已保存到：\n{target}")
        except OSError as e:
            QMessageBox.warning(self, "另存失败", f"另存失败：{e}")

    def _on_open_folder(self):
        """在资源管理器中打开 temp_assets 文件夹"""
        if self._temp_asset_manager is None:
            return
        folder = self._temp_asset_manager._assets_dir
        try:
            os.startfile(folder)
        except OSError:
            QMessageBox.warning(self, "打开失败", f"无法打开文件夹：\n{folder}")

    def _on_clear(self):
        """清空所有临时素材"""
        if self._temp_asset_manager is None:
            return
        count = self._temp_asset_manager.count()
        if count == 0:
            QMessageBox.information(self, "提示", "当前没有临时素材。")
            return
        ret = QMessageBox.question(
            self, "确认清空",
            f"确认清空所有 {count} 个临时素材？\n（仅删除程序目录下的副本，不影响原文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            cleared = self._temp_asset_manager.clear_all()
            self.refresh()
            self._host.data_changed.emit("asset")
            QMessageBox.information(self, "已清空", f"已清理 {cleared} 个临时素材。")
