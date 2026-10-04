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
    QStackedWidget, QStyledItemDelegate, QStyle, QToolButton, QLineEdit,
)
from PyQt6.QtCore import (
    QEasingCurve, QObject, QRunnable, QRectF, QSize, Qt, QThreadPool,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QImageReader, QPainter, QPainterPath, QPen, QPixmap,
)
from src.constants import DATETIME_MIN_LEN
from src.theme import DEFAULT_THEME, get_colors
from src.icon_render import icon as render_icon, paint_icon
from src.controls import EmptyState, IconButton, PageTitle
from src.glass import _to_color   # QSS 风格颜色字符串（含 rgba）→ QColor
from src import motion
# 会话分组纯逻辑（零 PyQt6）：按 added_time 间隔聚类，渲染时派生、不落库
from src import asset_group
# 会话堆旁路标注（命名 / 移出）：float_data/asset_groups.json，零 PyQt6
from src import asset_groups_store

# 非图片文件的类型图标（与 card_window._AssetItemWidget 同一套语义）。
# 值为 icons.py 的 file_* 图标名（01 包图集；两处引用同一批名字，保持一致）。
EXT_ICON = {
    ".txt": "file_text", ".md": "file_md", ".pdf": "file_pdf",
    ".doc": "file_doc", ".docx": "file_doc",
    ".xls": "file_xls", ".xlsx": "file_xls",
    ".ppt": "file_ppt", ".pptx": "file_ppt",
    ".zip": "file_zip", ".rar": "file_zip", ".7z": "file_zip",
    ".mp3": "file_audio", ".wav": "file_audio",
    ".mp4": "file_video", ".avi": "file_video", ".mov": "file_video",
    ".py": "file_code", ".js": "file_code", ".json": "file_text",
    ".html": "file_web",
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
    # 会话堆标题条的**专属**高度（仅分组态占用）。
    # ★ 别再把它塞进 CELL_H 的既有 64px 里：正文（缩略图 + 名 + 元）在平铺
    #   态已把 64px 用满，标题条若与正文抢空间，实测「大小 · 时间」那行会
    #   溢出约 24px 压到下一行上 —— 这正是 2026-10-03 用户截图里"标题条与
    #   缩略图挤压"的根因。分组态整体垫高，平铺态一字不变。
    HEADER_H = 24
    HEADER_BAR_H = 20        # 标题条可视条高（刻度与文字居中于其中）

    def __init__(self, host, thumb_cache: dict, loader=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._thumbs = thumb_cache   # id -> QPixmap | False | _PENDING
        self._loader = loader        # 未命中回调(面板的异步派发);None=旧同步路径
        self._fade_values = {}       # asset_id -> 0.0~1.0(淡入进度,面板维护)
        self._header_texts = {}      # asset_id -> 会话堆标题(仅 ≥2 张的堆;平铺=空)
        self._member_ordinals = {}   # asset_id -> "#2" 堆内序号(非堆首;平铺=空)
        self._group_mode = False     # 分组态才垫高 HEADER_H(见 HEADER_H 注释)
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
        self.CELL_H = self.THUMB_H + 64 + (self.HEADER_H if self._group_mode else 0)

    def set_group_mode(self, active: bool):
        """切换「分组态」：整体垫高一条专属标题条高度（重算 CELL_H）。

        ★ 分组/平铺**保持 setUniformItemSizes(True)** —— 变的是"整批统一
        垫高"，不是"逐格变高"，所以不会出现同排参差行高。分组态下**没有**
        标题的格子一样垫高，否则同一排里带标题的格子缩略图会低 24px、
        与邻格错位。
        """
        active = bool(active)
        if active == self._group_mode:
            return
        self._group_mode = active
        self.set_thumb_size(self.THUMB_W)

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

        # ---- 会话堆标题条（仅分组态；平铺态 _group_mode=False → 整段跳过）----
        # ★ 分组态下**每一格都垫高 HEADER_H**（含没有标题的单张堆），标题只
        #   画在首格：这样同排所有格子的 rect.top() 一致，缩略图不会错位。
        live_rect = rect
        if self._group_mode:
            bar = QRectF(rect.left(), rect.top(), rect.width(), self.HEADER_H)
            # 缩进到缩略图左缘，与下方格子对齐（比整格左缘更内敛）
            bar = bar.adjusted(self.PAD, 0, -self.PAD, 0)
            header_text = self._header_texts.get(asset.asset_id)
            if header_text:
                bar_h = float(self.HEADER_BAR_H)
                top = bar.top() + (self.HEADER_H - bar_h) / 2.0
                accent = _to_color(colors["primary"])
                tick = QRectF(bar.left(), top, 3.0, bar_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(tick, 1.5, 1.5)
                f = painter.font()
                f.setBold(True)
                painter.setFont(f)
                painter.setPen(_to_color(colors["text_secondary"]))
                painter.drawText(
                    QRectF(tick.right() + 8, top, bar.width() - 14, bar_h),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    header_text)
                painter.setFont(option.font)
            live_rect = QRectF(rect.left(), rect.top() + self.HEADER_H,
                               rect.width(), rect.height() - self.HEADER_H)
        rect = live_rect

        # ---- 单元背景卡（选中/hover/常态）----
        # ★ 色值一律走 _to_color：这里 4 个令牌是 QSS 的 rgba() 写法，
        #   QColor("rgba(...)") 会得到**无效色**，画出来是纯黑 —— hover 变
        #   "黑块卡"的根因（notes_panel/controls 同款坑早已用 _to_color 收口，
        #   本文件在 UI 重构 05 改 delegate 时漏了这层）。
        if selected:
            painter.setPen(QPen(_to_color(colors["primary"]), 1.4))
            painter.setBrush(_to_color(colors["primary_a12"]))
        elif hover:
            # 悬停 = 淡主色底 + 1px 主色细环，与主窗输入框 hover（$primary_a30
            # 环）同一语言；只铺底色的话白底缩略图几乎看不出悬停反馈。
            painter.setPen(QPen(_to_color(colors["primary_a30"]), 1))
            painter.setBrush(_to_color(colors["primary_a08"]))
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
            painter.setPen(QPen(_to_color(colors["hair"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(trect, 8, 8)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_to_color(colors["panel_fill"]))
            painter.drawRoundedRect(trect, 8, 8)
            if not exists:
                icon_name, sub = "warning", "文件已失效"
            elif asset.is_image:
                icon_name, sub = "image", "图片"
            else:
                ext = os.path.splitext(asset.original_name)[1].lower()
                icon_name = EXT_ICON.get(ext, "file_generic")
                sub = (ext or "文件").lstrip(".").upper()
            # 类型/状态图标：自绘描边图标替代旧 emoji 字形（UI 重构 05）
            avail_h = max(20.0, trect.height() - 30)
            side = min(48.0, trect.width() * 0.5, avail_h)
            paint_icon(painter, icon_name,
                       QRectF(trect.center().x() - side / 2,
                              trect.top() + 6 + (avail_h - side) / 2,
                              side, side),
                       colors["text_secondary"])
            sub_f = painter.font()
            sub_f.setPointSize(8)
            painter.setFont(sub_f)
            painter.setPen(_to_color(colors["text_secondary"]))
            painter.drawText(QRectF(trect.left(), trect.bottom() - 20,
                                    trect.width(), 16),
                             Qt.AlignmentFlag.AlignHCenter, sub)

        # ---- 文件名 / 堆内序号 ----
        # 分组态里同一堆的**非首格**不再重复渲染同一个文件名：连拍截图的
        # 文件名高度雷同（`剪贴板图片_*.png`），一堆里重复 7 次是纯噪音。
        # 改显 "#2" "#3" 序号 —— 既去噪，又保留连拍先后顺序这一有用信息。
        painter.setFont(option.font)
        fm = painter.fontMetrics()
        painter.setPen(_to_color(colors["text"]))
        name = fm.elidedText(self._member_ordinals.get(asset.asset_id)
                             or asset.original_name,
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
        painter.setPen(_to_color(colors["text_secondary"]))
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
        # ---- 会话堆旁路标注（命名 / 移出）----
        # 文件与 temp_assets.json 同目录（float_data/）；加载失败/缺失
        # 一律空结构，绝不影响素材列表本身。
        self._groups_path = ""
        self._group_store = asset_groups_store.empty_store()
        try:
            self._groups_path = os.path.join(
                os.path.dirname(self._temp_asset_manager._json_path),
                asset_groups_store.FILE_NAME)
            self._group_store = asset_groups_store.load(self._groups_path)
        except (AttributeError, TypeError, OSError):
            self._groups_path = ""
        # 堆归属速查（_build_sequence 在 refresh 时维护）：
        #   _pile_of      asset_id -> 所在堆的堆首 asset_id（单张/独立不在表内）
        #   _pile_heads   [堆首 asset_id]（≥2 张的堆，按时间序）
        #   _detached_ids 被用户移出时间聚类的 asset_id 集合
        self._pile_of = {}
        self._pile_heads = []
        self._detached_ids = set()
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
        hint = QLabel("拖拽图片/文件到悬浮球即可自动复制保存到此处")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 工具栏 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        open_folder_btn = IconButton("folder_open", text="打开素材文件夹",
                                     icon_size=14, object_name="secondaryBtn")
        open_folder_btn.clicked.connect(self._on_open_folder)
        toolbar.addWidget(open_folder_btn)

        refresh_btn = IconButton("refresh", text="刷新", icon_size=14,
                                 object_name="secondaryBtn")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        # 会话分组：一键在「平铺列表 ↔ 会话堆」之间切换
        # （默认取配置 asset_group_enabled；默认 False = 等价改动前的平铺）
        cfg = getattr(self._host, "_config", None)
        grouped = bool(cfg.get("asset_group_enabled", False)) if cfg else False
        self._group_btn = IconButton("merge", text="会话分组", icon_size=14,
                                     object_name="secondaryBtn",
                                     checkable=True)
        self._group_btn.setToolTip("按添加时间间隔把连拍截图聚成若干会话堆")
        self._group_btn.setChecked(grouped)
        self._group_btn.toggled.connect(self._on_group_toggled)
        toolbar.addWidget(self._group_btn)

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
        self._group_headers = {}
        self._group_ordinals = {}
        # 会话分组视图模式：QListView.setViewMode 在部分平台/离屏后端会被
        # 忽略，QToolButton 作为 QListWidget 的 setViewport 子级（顶层同窗）
        # 才是最可靠的「同列表两种呈现」载体，且分组/平铺共用同一个
        # QListWidget —— 条目集合天然一致，不需要两套列表同步。
        self._group_view_btn = QToolButton(self._asset_list.viewport())
        self._group_view_btn.setAutoRaise(True)
        self._group_view_btn.setText("分组")
        self._group_view_btn.setToolTip("切换 会话分组 / 平铺")
        self._group_view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._group_view_btn.clicked.connect(
            lambda: self._group_btn.toggle())
        self._group_view_btn.setVisible(grouped)
        self._asset_list.viewport().installEventFilter(self)
        self._asset_list.setItemDelegate(self._thumb_delegate)
        self._sync_scroll_step()
        self._stack.addWidget(self._asset_list)

        self._empty_state = EmptyState(
            "assets", "暂无素材",
            "拖拽图片/文件到悬浮球即可自动收录\n素材默认保留 30 天，注意及时归档")
        self._stack.addWidget(self._empty_state)

        v.addWidget(self._stack, 1)

    # ---- 主题联动 ----
    def _colors(self) -> dict:
        """面板级取色（右键菜单这类"非 delegate"场景用）。

        ★ 同名方法在 `_AssetThumbDelegate` 上还有一个 —— 那是 delegate 自己
          的；本方法补的是**面板这层**的入口。2026-10-02 右键菜单给删除项
          上 danger 色图标时误用了 delegate 的入口，`AssetsPanel` 没有这个
          属性，一右键就 AttributeError 弹程序异常窗。
        """
        theme = getattr(self._host, "current_theme", None) or DEFAULT_THEME
        return get_colors(theme)

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
        # 堆标注（命名/移出）惰性清理：素材删了，指向它的标注成孤儿
        pruned = asset_groups_store.prune(self._group_store, valid_ids)
        if pruned != self._group_store:
            self._group_store = pruned
            self._save_group_store()

        self._asset_list.clear()
        self._items_by_id = {}
        # ---- 序列决定呈现顺序：分组模式=按会话堆铺开；平铺=沿用旧顺序 ----
        # 两种模式喂给同一个 QListWidget 同一批 asset 对象 → 条目集合天然
        # 相等（"关闭分组后渲染结果逐项一致"由此成立）。
        sequence, self._group_headers, self._group_ordinals = \
            self._build_sequence(assets)
        # 分组态先把单元垫高（改 CELL_H）再灌条目 —— addItem 时 sizeHint 已定型，
        # 避免"先按旧高排、再重排"的闪烁；滚动步长随新 CELL_H 一起校准。
        self._thumb_delegate.set_group_mode(self._is_grouping())
        self._thumb_delegate._header_texts = self._group_headers
        self._thumb_delegate._member_ordinals = self._group_ordinals
        self._sync_scroll_step()
        for a in sequence:
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
        self._position_group_view_btn()

    # ---- 会话分组视图（渲染时派生，不落库）----
    def _build_sequence(self, assets):
        """返回 ``(渲染顺序, {asset_id: 堆标题}, {asset_id: 堆内序号})``。

        分组关闭 → 原样返回（与改动前逐项一致，两张表都为空）。
        分组开启 → 按 ``asset_group.cluster_assets`` 时间聚类，再套旁路
        标注（``asset_groups_store``：移出的素材拆成独立单元），堆内按
        时间升序铺开，**只有 ≥2 张的堆才挂标题**：单张素材本来就不是
        一次"会话"，给它画标题条纯属噪音 —— 真实数据 18 张里 11 张是
        单张，这正是 2026-10-03 用户截图里"到处都是标题条 / 比平铺更乱"
        的来源。堆名优先取用户自定义（asset_groups.json），无则默认
        ``N 张 · HH:MM``。非堆首格挂 ``"#2"``/``"#3"`` 序号，避免堆内
        重复渲染雷同文件名。
        """
        # 先清堆归属速查（平铺态也必须清干净，不能残留分组态的表）
        self._pile_of = {}
        self._pile_heads = []
        self._detached_ids = set(
            self._group_store.get(asset_groups_store.DETACHED_KEY, []))
        if not self._is_grouping():
            return list(assets), {}, {}
        gap = self._group_gap_seconds()
        groups = asset_group.cluster_assets(assets, gap_seconds=gap)
        groups = asset_group.apply_group_overrides(
            groups, detached=self._detached_ids)
        names = self._group_store.get(asset_groups_store.NAMES_KEY, {})
        headers = {}
        ordinals = {}
        sequence = []
        for i, group in enumerate(groups):
            sequence.extend(group)
            head_id = group[0].asset_id if group else None
            if len(group) >= 2 and head_id is not None:
                self._pile_heads.append(head_id)
                custom = names.get(str(head_id), "")
                headers[head_id] = asset_group.group_label(
                    group, i, custom_name=custom)
                for a in group:
                    self._pile_of[a.asset_id] = head_id
            for j, a in enumerate(group):
                if j:
                    ordinals[a.asset_id] = "#%d" % (j + 1)
        return sequence, headers, ordinals

    def _is_grouping(self) -> bool:
        btn = getattr(self, "_group_btn", None)
        return bool(btn.isChecked()) if btn is not None else False

    def _group_gap_seconds(self) -> float:
        """读分组间隔阈值配置（缺配置/脏值 → 回退纯逻辑模块默认值）。"""
        cfg = getattr(self._host, "_config", None)
        if cfg is None:
            return float(asset_group.DEFAULT_GAP_SECONDS)
        try:
            return float(cfg.get("asset_group_gap_seconds",
                                 asset_group.DEFAULT_GAP_SECONDS))
        except (TypeError, ValueError):
            return float(asset_group.DEFAULT_GAP_SECONDS)

    def _on_group_toggled(self, checked):
        """一键切回平铺 / 切到会话分组：持久化开关 + 重渲染。

        分组是纯渲染派生 —— 落盘的只有这个 bool 开关，temp_assets.json 不动。
        """
        cfg = getattr(self._host, "_config", None)
        if cfg is not None:
            if cfg.set("asset_group_enabled", bool(checked)):
                save = getattr(cfg, "save", None)
                if callable(save):
                    save()
        btn = getattr(self, "_group_view_btn", None)
        if btn is not None:
            btn.setVisible(bool(checked))
        self.refresh()

    def set_grouping(self, enabled: bool):
        """程序化切换分组视图（按钮 setChecked + 强制 refresh）。

        ★ 不依赖 ``toggled`` 信号是否送达（未 show 的按钮在离屏/无事件循环
        下 setChecked 未必发 toggled）—— 开关状态落在按钮上，refresh() 现读
        按钮态决定渲染，保证「点了就一定重排」。
        """
        changed = (self._group_btn.isChecked() != bool(enabled))
        if changed:
            self._group_btn.blockSignals(True)
            self._group_btn.setChecked(bool(enabled))
            self._group_btn.blockSignals(False)
        self._on_group_toggled(bool(enabled))

    # ---- 会话堆旁路标注（命名 / 移出；2026-10-04 P0-2）----
    # 标注落在 asset_groups.json（旁路文件，见 asset_groups_store）：
    # temp_assets.json 依旧一个字节不动；删掉标注文件即回到纯时间聚类。
    def _save_group_store(self):
        """把当前标注落盘；失败只记一行（标注丢失可接受，不阻断界面）"""
        if not self._groups_path:
            return
        if not asset_groups_store.save(self._groups_path, self._group_store):
            print("[素材面板] 堆标注写入失败（不影响本次操作）：",
                  self._groups_path)

    def _pile_display_name(self, head_id) -> str:
        """堆首 id → 用户自定义堆名（没有自定义返回空串）"""
        names = self._group_store.get(asset_groups_store.NAMES_KEY, {})
        return names.get(str(head_id), "")

    def _apply_pile_rename(self, head_id: int, name: str):
        """写入 / 清除堆名并落盘 + 重渲染（弹窗之外的纯逻辑，测试直达）。

        清空输入 = 恢复默认名（删键，不留空串脏数据）。堆身份 = 堆首
        asset_id：新增素材只会并入堆尾或新开一堆，堆首不变，标注稳定。
        """
        names = self._group_store.setdefault(asset_groups_store.NAMES_KEY, {})
        key = str(int(head_id))
        name = (name or "").strip()
        if name:
            names[key] = name[:asset_groups_store.NAME_MAX]
        else:
            names.pop(key, None)
        self._save_group_store()
        self.refresh()

    def _detach_asset(self, asset_id: int, detach: bool = True):
        """移出 / 取消移出：改 detached 标注 → 落盘 → 重渲染。

        移出后该素材脱离时间聚类、单独渲染（单张不画标题）；取消移出
        即回到纯时间聚类的天然归属。平铺视图不受任何影响（两条标注
        只在分组渲染路径被消费）。
        """
        aid = int(asset_id)
        lst = self._group_store.setdefault(asset_groups_store.DETACHED_KEY, [])
        if detach and aid not in lst:
            lst.append(aid)
        elif not detach:
            self._group_store[asset_groups_store.DETACHED_KEY] = [
                i for i in lst if i != aid]
        self._save_group_store()
        self.refresh()

    def _open_rename_dialog(self, head_id):
        """重命名会话堆弹窗：单行输入 + 保存/取消（Esc=取消）。

        弹窗是薄壳：存取逻辑在 :meth:`_apply_pile_rename`（可脱离 GUI
        单测）。留空保存 = 恢复默认名。
        """
        from src.glass_dialog import GlassDialog
        if head_id is None:
            return
        current = self._pile_display_name(head_id)
        head = self._temp_asset_manager.get_asset(head_id)
        subtitle = "堆名只在「会话分组」视图显示；留空保存 = 恢复默认名"
        if head is not None:
            subtitle = f"{len(self._pile_members(head_id))} 张 · " \
                       f"{head.added_time[11:16] if len(head.added_time) >= 16 else ''}｜" + subtitle
        dlg = GlassDialog(self._host, title="重命名会话堆",
                          subtitle=subtitle, size=(460, 200))
        edit = QLineEdit(current)
        edit.setPlaceholderText("例如：登录页排障现场")
        edit.selectAll()
        dlg.body_layout.addWidget(edit, 0, Qt.AlignmentFlag.AlignTop)

        def _save():
            self._apply_pile_rename(head_id, edit.text())
            dlg.accept()

        edit.returnPressed.connect(_save)
        dlg.add_footer([
            ("保存", "primaryBtn", _save),
            ("取消", "secondaryBtn", dlg.reject),
        ])
        dlg.exec()

    def _pile_members(self, head_id):
        """堆首 id → 该堆当前成员列表（Asset 对象；找不到返回空表）"""
        if not self._is_grouping():
            return []
        assets = self._temp_asset_manager.get_all_assets()
        gap = self._group_gap_seconds()
        groups = asset_group.apply_group_overrides(
            asset_group.cluster_assets(assets, gap_seconds=gap),
            detached=self._group_store.get(
                asset_groups_store.DETACHED_KEY, []))
        for group in groups:
            if group and group[0].asset_id == head_id:
                return group
        return []

    def _position_group_view_btn(self):
        """把「分组」浮钮钉在列表视口右上角（内容/滚动条之外，不遮挡首行）。"""
        btn = getattr(self, "_group_view_btn", None)
        if btn is None:
            return
        vp = self._asset_list.viewport()
        btn.adjustSize()
        btn.move(max(4, vp.width() - btn.width() - 12), 6)
        btn.raise_()

    def eventFilter(self, obj, event):
        # 视口尺寸变化时重摆浮钮（resize / 首次布局都会走到这里）
        if obj is getattr(self._asset_list, "viewport", lambda: None)():
            if event.type() in (event.Type.Resize, event.Type.Show):
                self._position_group_view_btn()
        return super().eventFilter(obj, event)

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
        act_open = menu.addAction("打开")
        act_save_as = menu.addAction("另存为...")
        menu.addSeparator()
        act_delete = menu.addAction("删除" if n <= 1 else f"删除（{n} 个）")
        # 删除项危险语义：Qt 菜单无法按 action 单独设文字色，
        # 用 danger 色的自绘 trash 图标承载（UI 重构 05）
        act_delete.setIcon(
            render_icon("trash", 14, self._colors()["danger"]))

        # ---- 会话堆操作（仅分组视图；2026-10-04 P0-2）----
        # 堆名/移出是「会话分组」视图的专属语义：平铺态没有堆，菜单里
        # 就不该出现这些项（也避免误触写了标注却看不到效果）。
        act_rename = act_detach = act_restore = None
        if self._is_grouping():
            pile_head = self._pile_of.get(aid)
            if pile_head is not None:
                menu.addSeparator()
                pile_name = self._pile_display_name(pile_head)
                act_rename = menu.addAction(
                    "重命名会话堆…" if not pile_name
                    else f"重命名会话堆（{pile_name}）…")
                act_detach = menu.addAction("从会话堆移出")
                act_detach.setToolTip(
                    "这张素材不再参与时间聚类，单独渲染；可随时取消移出")
            elif aid in self._detached_ids:
                menu.addSeparator()
                act_restore = menu.addAction("取消移出（回到时间分组）")
        action = menu.exec(self._asset_list.mapToGlobal(pos))
        if action == act_open:
            self._open(aid)
        elif action == act_save_as:
            self._save_as(aid)
        elif action == act_rename:
            self._open_rename_dialog(self._pile_of.get(aid))
        elif action == act_detach:
            self._detach_asset(aid, detach=True)
        elif action == act_restore:
            self._detach_asset(aid, detach=False)
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
