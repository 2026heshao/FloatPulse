# -*- coding: utf-8 -*-
"""
====================================================================
软件导航组件  -  AppLauncherPage / AppEditDialog / AppManageDialog
====================================================================
软件导航页面：嵌入主窗口 QStackedWidget 的浏览页面组件。

职责划分：
  · AppLauncherPage —— 浏览页面：卡片左键启动；卡片/空白区右键菜单
  · AppManageDialog  —— 管理对话框（新增/编辑/删除），由主窗口设置页调用
  · AppEditDialog    —— 新增/编辑软件条目的模态弹窗，管理对话框与
                        导航页右键菜单共用

本文件包含：
  · AppCardWidget       —— 单张软件卡片（图标 + 名称 + 右键菜单）
  · AppLauncherPage     —— 嵌入主窗口的导航浏览页面
  · AppManageDialog     —— 软件列表管理对话框（新增/编辑/删除）
  · AppEditDialog       —— 新增/编辑软件条目的模态弹窗
  · extract_exe_icon / load_icon_pixmap / draw_placeholder_icon
                        图标工具函数

设计要点：
  1. AppLauncherPage 继承 QWidget，卡片左键启动软件；
     卡片右键弹专属菜单（启动/编辑/定位/复制路径/移除），
     页面空白处右键弹新增/管理菜单——事件均就地消费，
     不再冒泡到主窗口的全局右键兜底菜单
  2. 卡片尺寸步进器位于主窗口全局设置页，通过 apply_card_size() 实时刷新本页
  3. AppManageDialog 继承 QDialog，管理结束发射 apps_changed 刷新导航页
  4. 软件列表数据存放在宿主已有 config.json 的根节点 "apps" 数组中，
     复用宿主的 ConfigManager 读写，禁止新建第二个 json 文件
  5. 本模块不创建 QApplication
====================================================================
"""

import os
import subprocess
import shlex

from PyQt6.QtWidgets import (
    QWidget,
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QFormLayout,
    QFileDialog,
    QFileIconProvider,
    QScrollArea,
    QGridLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QMenu,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize, QFileInfo, pyqtSignal
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QPen, QPixmapCache,
                         QGuiApplication, QIcon)

from src.theme import (FALLBACK_ACCENT, get_main_window_qss, get_colors, scale_px_fonts)
from src.glass_message_box import GlassMessageBox
from src.constants import DEFAULT_THEME
from src import win_icons
from src.controls import (
    tune_list_scrolling, SmoothButton, EmptyState, IconButton, PageTitle,
    ScreenToast, SmoothInput, SmoothCheckBox,
)


# ====================================================================
# 数据结构
# ====================================================================

# 单条软件条目模板：定义每个软件条目的完整字段结构
APP_ITEM_TEMPLATE = {
    "name":          "",    # 软件名称
    "exe_path":      "",    # 可执行文件路径
    "icon_path":     "",    # 图标路径（为空则使用默认图标）
    "remark":        "",    # 备注说明
    "enable":        True,  # 是否启用
    "launch_args":   "",    # 启动附加参数
}


# ====================================================================
# 图标工具函数
# ====================================================================

def draw_placeholder_icon(size: int = 64) -> QPixmap:
    """
    绘制默认占位图标（无图标时的兜底），返回 QPixmap。

    绘制一个青色圆角方块 + 白色"应用窗口"轮廓，风格与主题主色一致。
    结果按尺寸缓存，避免每次重建卡片时重复绘制。
    """
    size = max(16, int(size))
    cache_key = f"fp:placeholder:{size}"
    cached = QPixmapCache.find(cache_key)
    if cached is not None and not cached.isNull():
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 背景圆角方块（主题主色）
    painter.setBrush(QColor(FALLBACK_ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    corner = int(size * 0.18)
    painter.drawRoundedRect(0, 0, size, size, corner, corner)

    # 白色"应用窗口"占位轮廓
    pen_w = max(2, size // 14)
    painter.setPen(QPen(QColor(255, 255, 255), pen_w))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = int(size * 0.22)
    w = size - 2 * m
    painter.drawRoundedRect(m, m, w, w, int(size * 0.08), int(size * 0.08))

    # 顶部的"标题栏"横线，强化"应用窗口"识别度
    painter.drawLine(m, m + pen_w * 2 + 1, m + w, m + pen_w * 2 + 1)

    painter.end()
    QPixmapCache.insert(cache_key, pixmap)
    return pixmap


def _fit_icon(pixmap: QPixmap, size: int) -> QPixmap:
    """把任意来源的图标位图收口到「**逻辑**边长 == size」。

    ★ 这是 2026-10-02 那次「图标大小调不动」的第二处根因。``QIcon.pixmap()``
    **从不放大**——源帧小于请求尺寸就原样返回；对 ``.lnk`` 更极端，无论请求
    多大一律只回 32px（125% DPI 实测 40px），而 ``availableSizes()`` /
    ``actualSize()`` 都会谎报大尺寸，上游无从察觉。于是卡片跟着设置放大、
    里面的图标纹丝不动。

    这里按设备像素比收口：dpr>1 的位图（QIcon 用 dpr 凑逻辑尺寸）原样保留，
    dpr==1 的（shell 大图 / 自己新缩的）按屏幕 dpr 补物理像素，避免高 DPI
    下把图标放大得发虚。
    """
    if pixmap.isNull():
        return pixmap
    dpr = pixmap.devicePixelRatio() or 1.0
    if abs(pixmap.width() / dpr - size) < 0.5:
        return pixmap
    if dpr <= 1.0:
        try:
            scr = QGuiApplication.primaryScreen()
            dpr = float(scr.devicePixelRatio()) if scr else 1.0
        except Exception:
            dpr = 1.0
    dpr = max(1.0, dpr)
    dev = max(1, int(round(size * dpr)))
    out = pixmap.scaled(dev, dev, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def extract_exe_icon(exe_path: str, size: int = 64) -> QPixmap:
    """
    从 exe / 快捷方式提取图标，返回 QPixmap。

    优先级（2026-10-02 重排）：
      1. **shell jumbo 图像列表**（``src.win_icons``，256px 真高分图）——
         Qt 的 QFileIconProvider 对 ``.lnk`` 恒回 32px，只有这条能出大图；
      2. Qt 的 ``QFileIconProvider``（非 Windows / shell 取图失败时兜底）；
      3. 默认占位图标。
    两条真实路径的结果都经 ``_fit_icon`` 收口到请求尺寸 —— 否则图标会卡在
    源帧的原生尺寸上（QIcon 不放大）。

    同一 (路径, 尺寸) 的结果按 QPixmapCache 缓存，避免卡片重建时重复提取。
    """
    if not exe_path or not os.path.exists(exe_path):
        return draw_placeholder_icon(size)

    cache_key = f"fp:exe:{exe_path}:{size}"
    cached = QPixmapCache.find(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    pixmap = None
    try:
        img = win_icons.shell_icon_image(exe_path)
        if img is not None and not img.isNull():
            pixmap = _fit_icon(QPixmap.fromImage(img), size)
    except Exception:
        pixmap = None

    if pixmap is None or pixmap.isNull():
        try:
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(exe_path))
            pixmap = _fit_icon(icon.pixmap(QSize(size, size)), size)
        except Exception:
            pixmap = None

    if pixmap is None or pixmap.isNull():
        return draw_placeholder_icon(size)
    QPixmapCache.insert(cache_key, pixmap)
    return pixmap


def load_icon_pixmap(icon_path: str, size: int = 64) -> QPixmap:
    """
    加载外部图标文件（ico / png）为 QPixmap，用于手动覆盖图标。

    加载失败或路径为空时回退到默认占位图标。
    同一 (路径, 尺寸) 的结果按 QPixmapCache 缓存，避免重复解码。
    尺寸收口同样走 ``_fit_icon``（与 exe 路径同一口径，高 DPI 下不发虚）。
    """
    if not icon_path or not os.path.exists(icon_path):
        return draw_placeholder_icon(size)

    cache_key = f"fp:file:{icon_path}:{size}"
    cached = QPixmapCache.find(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    try:
        pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            return draw_placeholder_icon(size)
        pixmap = _fit_icon(pixmap, size)
        QPixmapCache.insert(cache_key, pixmap)
        return pixmap
    except Exception:
        return draw_placeholder_icon(size)


# ====================================================================
# 拖拽添加：从文件路径构造应用条目（悬浮球拖入 exe/lnk 时用）
# ====================================================================

def get_exe_name(exe_path: str) -> str:
    """
    从 exe 的 Win32 版本资源识别软件显示名称。

    优先级：FileDescription → ProductName → 文件名去扩展名。
    任何失败（无版本信息 / 路径无效 / 非 Windows）都回退到文件名。
    """
    fallback = (os.path.splitext(os.path.basename(exe_path))[0]
                if exe_path else "")
    if not exe_path or not os.path.exists(exe_path):
        return fallback
    try:
        import ctypes
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return fallback
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe_path, 0, size, data):
            return fallback

        # 取翻译表 → 拼出 StringFileInfo 的语言码（如 080404b0）
        buf = ctypes.c_void_p()
        buf_len = ctypes.c_uint()
        if not ver.VerQueryValueW(data, "\\VarFileInfo\\Translation",
                                  ctypes.byref(buf), ctypes.byref(buf_len)):
            return fallback
        if not buf.value or buf_len.value < 4:
            return fallback
        lang, codepage = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint16))[0:2]
        lang_id = f"{lang:04X}{codepage:04X}"

        # 依次查询 FileDescription / ProductName
        for key in ("FileDescription", "ProductName"):
            vbuf = ctypes.c_void_p()
            vlen = ctypes.c_uint()
            sub = f"\\StringFileInfo\\{lang_id}\\{key}"
            if ver.VerQueryValueW(data, sub, ctypes.byref(vbuf),
                                  ctypes.byref(vlen)) and vbuf.value:
                text = ctypes.wstring_at(vbuf.value, vlen.value - 1).strip()
                if text:
                    return text
    except Exception:
        pass
    return fallback


def make_app_from_path(path: str) -> dict | None:
    """
    从拖入的文件路径构造一条应用条目（悬浮球拖拽添加，与 LaunchDeck 同款）。

    - .lnk → 存快捷方式本身（点击即打开目标），名称为快捷方式文件名
    - .exe → 名称取 exe 版本资源识别结果
    - 其他类型 → 返回 None（由素材/碎片逻辑处理，不进启动器）
    路径不存在 → 返回 None。
    """
    if not path or not os.path.exists(path):
        return None
    low = path.lower()
    if low.endswith(".lnk"):
        return {"name": os.path.splitext(os.path.basename(path))[0],
                "exe_path": path}
    if low.endswith(".exe"):
        return {"name": get_exe_name(path), "exe_path": path}
    return None


# ====================================================================
# 公共启动函数（主窗口导航页 / 小卡片软件页共用）
# ====================================================================

def launch_app(app: dict, parent=None) -> bool:
    """
    异步启动一条软件条目，返回是否成功。

    - 使用 subprocess.Popen 启动，不 wait，绝不阻塞 Qt 事件循环
    - 兼容路径带中文、空格；launch_args 用 shlex 解析（Windows 模式）
    - 修复 Windows 错误 740（"请求的操作需要提升"）：
      exe 清单要求管理员权限时 CreateProcess 会直接失败，
      此时自动改用 ShellExecuteW 的 "runas" 动词提权重启
      （系统会弹出 UAC 确认框，用户同意后以管理员运行）
    - 启动失败弹出中文提示（parent 为弹窗宿主控件）
    """
    exe_path = (app.get("exe_path") or "").strip()
    name = app.get("name", "")
    launch_args = (app.get("launch_args") or "").strip()

    # ---- 前置校验：路径为空 ----
    if not exe_path:
        GlassMessageBox.warning(
            parent, "启动失败",
            f"「{name}」的可执行文件路径为空，请先编辑该条目。",
        )
        return False

    # ---- 前置校验：文件不存在 ----
    if not os.path.exists(exe_path):
        GlassMessageBox.warning(
            parent, "启动失败",
            f"找不到「{name}」的可执行文件：\n{exe_path}\n\n"
            "文件可能已被移动或删除，请编辑该条目修正路径。",
        )
        return False

    # ---- .lnk 快捷方式：CreateProcess 不支持直接执行，走 shell 打开 ----
    # （lnk 自带参数/工作目录/图标，launch_args 附加参数对 lnk 不生效）
    if exe_path.lower().endswith(".lnk"):
        try:
            os.startfile(exe_path)
            return True
        except FileNotFoundError:
            GlassMessageBox.warning(
                parent, "启动失败",
                f"快捷方式「{name}」指向的目标不存在：\n{exe_path}\n\n"
                "原文件可能已被移动或删除。",
            )
            return False
        except OSError as e:
            GlassMessageBox.critical(
                parent, "启动异常",
                f"启动「{name}」时发生异常：\n{str(e)}",
            )
            return False

    # ---- 组装参数列表（shlex posix=False 兼容 Windows 反斜杠路径） ----
    args_list = [exe_path]
    if launch_args:
        for token in shlex.split(launch_args, posix=False):
            args_list.append(token.strip('"'))

    # ---- 第一次尝试：普通权限异步启动 ----
    try:
        subprocess.Popen(args_list, shell=False, close_fds=True)
        return True
    except FileNotFoundError:
        GlassMessageBox.warning(
            parent, "启动失败",
            f"找不到「{name}」的可执行文件。\n路径：{exe_path}",
        )
        return False
    except OSError as e:
        # Windows 错误 740：请求的操作需要提升（exe 要求管理员权限）
        if getattr(e, "winerror", None) == 740:
            return _launch_elevated(exe_path, launch_args, name, parent)
        GlassMessageBox.critical(
            parent, "启动异常",
            f"启动「{name}」时发生异常：\n{str(e)}",
        )
        return False
    except Exception as e:
        GlassMessageBox.critical(
            parent, "启动异常",
            f"启动「{name}」时发生异常：\n{str(e)}",
        )
        return False


def _launch_elevated(exe_path: str, launch_args: str, name: str, parent=None) -> bool:
    """
    以管理员权限启动 exe（ShellExecuteW "runas" 动词）。

    - 系统弹出 UAC 确认框：用户点"是"→ 提权启动成功
    - 用户点"否"→ 返回 SE_ERR_ACCESSDENIED，提示已取消
    - 同样为异步启动，不阻塞事件循环
    """
    try:
        import ctypes
        # SW_SHOWNORMAL=1：正常显示窗口；返回值 >32 表示成功
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,                 # 无父窗口句柄
            "runas",              # 动词：请求管理员提权
            exe_path,             # 目标 exe
            launch_args or None,  # 附加参数（可为空）
            None,                 # 工作目录（默认）
            1,                    # 显示方式 SW_SHOWNORMAL
        )
        if ret > 32:
            return True  # UAC 确认，提权启动成功
        # 返回值 5 = SE_ERR_ACCESSDENIED：用户在 UAC 弹窗点了"否"
        GlassMessageBox.information(
            parent, "已取消",
            f"已取消以管理员权限启动「{name}」。",
        )
        return False
    except Exception as e:
        GlassMessageBox.critical(
            parent, "启动异常",
            f"以管理员权限启动「{name}」失败：\n{str(e)}",
        )
        return False


# ====================================================================
# 单张软件卡片（只读）
# ====================================================================

class AppCardWidget(QFrame):
    """
    单张软件卡片：图标 + 软件名称 + 右键菜单。

    - 左键点击发射 clicked 信号（携带卡片索引）
    - 右键弹卡片专属菜单，选择后发射 actionRequested(index, action)，
      由 AppLauncherPage 统一分发业务；事件就地消费，
      不冒泡到主窗口的全局右键兜底菜单
    - 路径失效（exe 文件不存在）时整张卡片置灰
    - hover 背景高亮（通过 QSS #appCard:hover 实现）

    2026-10-08（清单 A2/B2/C2，交互状态批第二批）：
    - **键盘可达**：StrongFocus + keyPressEvent（Enter/Space → clicked，
      与 mousePressEvent 同一信号通道）——本页唯一动作就是"点卡片启动
      软件"，此前键盘 100% 不可达；Tab 顺序按卡片创建顺序自然遍历。
    - **焦点态**：QSS `#appCard:focus`（$focus_ring，基态 1px transparent
      边框已备好，只换 border-color 不撑盒模型）。
    - **按下态**：Qt QSS 的 :pressed 对 QFrame 无效，走属性驱动
      （mousePress/Release 置 ``pressed`` 属性 + repolish，QSS
      `[pressed="true"]` 加深一档背景；禁 margin 位移，theme.py:577 红线）。
    """

    # 左键点击信号：参数为卡片在 app_list 中的索引
    clicked = pyqtSignal(int)

    # 右键菜单动作信号：(卡片索引, 动作 id)
    # 动作 id: launch / edit / locate / copypath / remove
    actionRequested = pyqtSignal(int, str)

    # 卡片名称区域高度（像素）
    _NAME_AREA_H = 28

    def __init__(self, app_data: dict, index: int, card_size: int, parent=None):
        super().__init__(parent)
        self._app = app_data
        self._index = index
        self._card_size = card_size

        self.setObjectName("appCard")
        self.setFixedSize(card_size, card_size + self._NAME_AREA_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_ui()
        self._load_icon()
        self._apply_state()

    def _build_ui(self):
        """构建卡片内部布局：图标 + 名称。"""
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 4)
        v.setSpacing(4)

        # 图标
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._icon_label, 1)

        # 名称
        name = self._app.get("name", "未命名")
        self._name_label = QLabel(name)
        self._name_label.setObjectName("appName")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(False)
        v.addWidget(self._name_label)

    def _load_icon(self):
        """加载图标：手动图标文件 > 从 exe 提取 > 占位图标。"""
        icon_size = self._card_size - 20
        icon_path = self._app.get("icon_path", "")
        exe_path = self._app.get("exe_path", "")

        if icon_path:
            pixmap = load_icon_pixmap(icon_path, icon_size)
        elif exe_path:
            pixmap = extract_exe_icon(exe_path, icon_size)
        else:
            pixmap = draw_placeholder_icon(icon_size)
        self._icon_label.setPixmap(pixmap)

    def _apply_state(self):
        """路径失效检测：exe 文件不存在时卡片置灰（半透明）。"""
        exe_path = self._app.get("exe_path", "")
        valid = bool(exe_path) and os.path.exists(exe_path)

        if not valid:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
            self.setToolTip(f"路径失效：{exe_path or '（空）'}")

    # ---------------- 键盘与按下态（A2/C2）----------------
    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        """Enter / Space → clicked（与鼠标点击同一信号通道）。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                           Qt.Key.Key_Space):
            self.clicked.emit(self._index)
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_pressed(self, on: bool) -> None:
        """属性驱动的按下态：QSS ``[pressed="true"]`` 加深一档背景。

        QFrame 不支持 QSS ``:pressed``，只能 mousePress/Release 手动置
        属性并 repolish。置位/还原都不改几何，不碰 margin（红线）。
        """
        self.setProperty("pressed", "true" if on else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def mousePressEvent(self, event):
        """左键点击：置按下态并发射 clicked 信号。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
            self.clicked.emit(self._index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(False)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt 命名)
        """按住拖出卡片时收掉按下态（避免残留高亮）。"""
        if self.property("pressed") == "true":
            self._set_pressed(False)
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：启动/编辑/定位/复制路径/移除。

        菜单选择后经 actionRequested 信号交由 AppLauncherPage 分发；
        exec 结束后 accept，阻止事件继续冒泡到主窗口兜底菜单。
        """
        menu = QMenu(self)
        menu.addAction("启动", lambda: self.actionRequested.emit(
            self._index, "launch"))
        menu.addAction("编辑…", lambda: self.actionRequested.emit(
            self._index, "edit"))
        menu.addAction("打开所在位置", lambda: self.actionRequested.emit(
            self._index, "locate"))
        menu.addAction("复制路径", lambda: self.actionRequested.emit(
            self._index, "copypath"))
        menu.addSeparator()
        menu.addAction("移除", lambda: self.actionRequested.emit(
            self._index, "remove"))
        menu.exec(event.globalPos())
        event.accept()


# ====================================================================
# 主页面（只读浏览，嵌入 QStackedWidget）
# ====================================================================

class AppLauncherPage(QWidget):
    """
    软件导航浏览页面：嵌入主窗口 QStackedWidget 的页面组件。

    - 顶部工具栏：标题 + 计数 + 【管理软件列表】按钮
      （管理按钮唤起 AppManageDialog 完成新增/编辑/删除，保存后立即刷新本页卡片）
    - 设置栏：自动回到主页复选框（卡片尺寸调节已迁移至主窗口设置页）
    - 滚动区域：卡片网格，列数随页面宽度自适应
    - 卡片交互：左键启动；右键弹专属菜单（启动/编辑/定位/复制路径/移除）
    - 页面空白处右键：新增软件 / 管理软件列表
    - 启动成功后若开启了"自动回到主页面"，发射 request_switch_to_home 信号

    对外接口：
      - apply_card_size(value)  —— 供主窗口设置页的尺寸步进器实时调用

    config 依赖：
      - apps:           软件列表
      - app_card_size:  卡片边长（60-140，仅可在主窗口设置页修改）
      - app_auto_back_home: 启动后自动回到主页面
    """

    # 请求宿主切换到首页（主页）的信号
    request_switch_to_home = pyqtSignal()

    # 网格卡片间距（像素）
    _GRID_SPACING = 10

    # 卡片尺寸滑块范围
    _CARD_SIZE_MIN = 60
    _CARD_SIZE_MAX = 140

    def __init__(self, config_manager, parent=None, theme: str = DEFAULT_THEME):
        super().__init__(parent)
        self._config_manager = config_manager
        self._theme = theme

        # 从配置读取
        self.app_list = []
        self._card_size = self._config_manager.get("app_card_size", 96)
        self._auto_back_home = self._config_manager.get("app_auto_back_home", False)
        self._current_cols = 1

        self._build_ui()
        self._apply_style()

        # 初始化时加载并渲染卡片
        self.load_apps_from_config()
        self._render_cards()
        self._update_count()

    # ================================ UI 构建 ================================
    def _build_ui(self):
        """构建页面布局：工具栏（标题+计数+复选框+管理按钮）+ 卡片滚动区域。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 顶部工具栏：标题 + 计数 + stretch + 回主页复选框 + 管理按钮 ----
        # 复选框收纳到标题行，卡片网格紧贴标题下方开始排列（无设置栏阻隔）
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 12, 16, 6)
        toolbar.setSpacing(10)

        self._page_title = PageTitle("apps", "软件导航", self)
        toolbar.addWidget(self._page_title)

        self._count_label = QLabel("共 0 个")
        self._count_label.setObjectName("hintLabel")
        toolbar.addWidget(self._count_label)

        toolbar.addStretch()

        # 自动回到主页面复选框（状态持久化到 config.json）
        self._back_home_cb = SmoothCheckBox("启动软件后自动回到主页面")
        self._back_home_cb.setChecked(self._auto_back_home)
        self._back_home_cb.stateChanged.connect(self._on_back_home_toggled)
        toolbar.addWidget(self._back_home_cb)

        # 管理软件列表按钮：唤起 AppManageDialog 完成新增/编辑/删除
        # （本页面唯一的软件管理入口，保存后立即刷新下方卡片网格）
        self._manage_btn = IconButton("apps", text="管理软件列表", icon_size=14,
                                      object_name="secondaryBtn")
        self._manage_btn.setFixedHeight(28)
        self._manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manage_btn.clicked.connect(self._on_manage_apps)
        toolbar.addWidget(self._manage_btn)

        root.addLayout(toolbar)

        # ---- 滚动区域 + 网格容器（卡片第一行紧贴标题下方） ----
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._grid_container = QWidget()
        self._grid_container.setObjectName("gridContainer")
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(16, 4, 16, 16)
        self._grid_layout.setSpacing(self._GRID_SPACING)
        # 网格整体从左上角开始排列：列不拉伸铺满容器宽度，
        # 卡片按行从左到右、从上到下逐一排列（修复卡片居中/散开问题）
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self._scroll.setWidget(self._grid_container)
        root.addWidget(self._scroll, 1)

        # 空态引导（A3）：覆盖层叠在滚动区 viewport 上。★不能放进
        # _grid_layout —— _render_cards 每次都 _clear_layout 整张网格；
        # 也不挂 _grid_container（空网格时它只有一行边距高，会裁掉空态）
        self._apps_empty = EmptyState(
            "apps", "还没有常用软件",
            "点上方「新增」把常用软件放进来，单击卡片直接启动")
        self._apps_empty.attach_to(self._scroll.viewport())
        self._apps_empty.setVisible(len(self.app_list) == 0)
        if self._apps_empty.isVisible():
            self._apps_empty.setGeometry(self._scroll.viewport().rect())
            self._apps_empty.raise_()

    def _apply_style(self):
        """应用主题 QSS + 卡片/页面专用样式。"""
        qss = get_main_window_qss(self._theme)
        colors = get_colors(self._theme)
        # 标题图标是自绘的（不在 QSS 管辖内），换主题必须显式重取色 ——
        # 本页没有 theme_changed 订阅，宿主换主题时是改 self._theme 后调
        # _apply_style()（见 main_window._apply_theme），所以接在这里。
        page_title = getattr(self, "_page_title", None)
        if page_title is not None:
            page_title.apply_theme(self._theme)
        card_extra = f"""
        QWidget#gridContainer {{
            /* 不再垫实色面板：与其他页面一致，卡片直接浮在玻璃底上 */
            background-color: transparent;
        }}
        QScrollArea {{
            background-color: transparent; border: none;
        }}
        QScrollArea > QWidget > QWidget {{ background-color: transparent; }}

        QFrame#appCard {{
            background-color: transparent;
            border-radius: 10px;
            border: 1px solid transparent;
        }}
        QFrame#appCard:hover {{
            background-color: {colors['list_item_hover']};
            border: 1px solid {colors['primary_border']};
        }}
        QFrame#appCard:focus {{
            /* A2/B2（2026-10-08）：键盘可达后的焦点可见性——基态 1px
               transparent 边框只换 border-color，不撑盒模型 */
            border: 1px solid {colors['focus_ring']};
        }}
        QFrame#appCard[pressed="true"] {{
            /* C2：QFrame 不吃 :pressed，属性驱动（见 _set_pressed）；
               背景加深一档复用 appLaunchBtn:pressed 同款色阶 */
            background-color: {colors['list_item_selected']};
        }}
        QLabel#appName {{
            color: {colors['text']};
            font-size: 12px;
            background: transparent;
        }}
        QLabel#pageTitle {{
            color: {colors['text']};
            font-size: 18px;
            font-weight: bold;
            background: transparent;
        }}
        QLabel#hintLabel {{
            color: {colors['text_secondary']};
            font-size: 11px;
            background: transparent;
        }}
        QCheckBox {{
            color: {colors['text']};
            font-size: 12px;
            spacing: 6px;
        }}
        """
        # 内联块里写死的 px 字号与全局 QSS 走同一缩放档（P1-3；100% 逐字节不变）
        self.setStyleSheet(scale_px_fonts(qss + card_extra))

    # ================================ 数据读写 ================================
    def load_apps_from_config(self):
        """
        从 config.json 的 "apps" 根节点读取软件列表到内存。
        - apps 字段缺失 / 非 list 时，默认赋值为空列表 []
        - 每个条目按 APP_ITEM_TEMPLATE 补齐缺失字段
        """
        raw = self._config_manager.get("apps", [])
        if not isinstance(raw, list):
            raw = []
        self.app_list = [
            {**APP_ITEM_TEMPLATE, **(item if isinstance(item, dict) else {})}
            for item in raw
        ]

    def save_apps_to_config(self):
        """将内存中的软件列表写回 config.json 的 "apps" 根节点。"""
        self._config_manager.set("apps", self.app_list)
        self._config_manager.save()

    def reload_settings(self):
        """
        从配置重新读取所有设置项（卡片尺寸、自动回到主页），
        并更新 UI 控件状态，然后刷新卡片渲染。
        宿主在切换到此页面时调用。
        """
        self._card_size = self._config_manager.get("app_card_size", 96)
        self._auto_back_home = self._config_manager.get("app_auto_back_home", False)

        # 同步复选框（blockSignals 避免触发额外回调）
        # 注：卡片尺寸滑动条已迁移至主窗口设置页，此处无需同步
        self._back_home_cb.blockSignals(True)
        self._back_home_cb.setChecked(self._auto_back_home)
        self._back_home_cb.blockSignals(False)

        self._refresh_cards()

    # ================================ 卡片渲染 ================================
    def _calc_columns(self) -> int:
        """根据滚动区域视口宽度计算网格列数（至少 1 列）。"""
        viewport_w = self._scroll.viewport().width()
        if viewport_w <= 0:
            return 1
        cols = (viewport_w + self._GRID_SPACING) // (
            self._card_size + self._GRID_SPACING
        )
        return max(1, int(cols))

    def _render_cards(self):
        """
        清空网格并重新渲染全部卡片（左键启动 + 右键菜单）。

        排列规则：从左上角开始，按行从左到右、放满一行换下一行，
        末行不满时右侧留空（左对齐，不做居中补位）。
        """
        self._clear_layout(self._grid_layout)

        cols = self._current_cols

        for i, app in enumerate(self.app_list):
            card = AppCardWidget(app, i, self._card_size, self)
            card.clicked.connect(self._on_card_clicked)
            card.actionRequested.connect(self._on_card_action)
            self._grid_layout.addWidget(card, i // cols, i % cols)

    @staticmethod
    def _clear_layout(layout):
        """递归清除布局中的所有项（widget 自动 deleteLater）。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub:
                    AppLauncherPage._clear_layout(sub)

    def _update_count(self):
        """更新顶部计数标签 + 空态引导显隐（A3）。"""
        self._count_label.setText(f"共 {len(self.app_list)} 个")
        empty = getattr(self, "_apps_empty", None)
        if empty is not None:
            empty.setVisible(len(self.app_list) == 0)
            if empty.isVisible():
                empty.setGeometry(self._scroll.viewport().rect())
                empty.raise_()

    # ================================ 事件重写 ================================
    def resizeEvent(self, event):
        """页面尺寸变化时，若列数改变则重新排列卡片。"""
        super().resizeEvent(event)
        new_cols = self._calc_columns()
        if new_cols != self._current_cols:
            self._current_cols = new_cols
            self._render_cards()

    # ================================ 卡片尺寸（供设置页调用） ================================
    def apply_card_size(self, value: int):
        """
        应用新的卡片尺寸（由主窗口设置页的滑动条实时调用）。

        - 数值钳制在 60-140 范围内
        - 立即重算网格列数并重新渲染全部卡片
        - 配置的读写由调用方（主窗口设置页）负责，此处只管界面
        """
        value = max(self._CARD_SIZE_MIN, min(self._CARD_SIZE_MAX, int(value)))
        if value == self._card_size:
            return
        self._card_size = value
        self._current_cols = self._calc_columns()
        self._render_cards()

    # ================================ 设置栏槽函数 ================================
    def _on_back_home_toggled(self, state: int):
        """自动回到主页复选框状态变更时持久化到配置。"""
        self._auto_back_home = bool(state)
        self._config_manager.set("app_auto_back_home", self._auto_back_home)
        self._config_manager.save()

    # ================================ 管理软件列表 ================================
    def _on_manage_apps(self):
        """
        打开软件列表管理对话框（AppManageDialog），完成新增/编辑/删除。

        - 管理对话框保存后发射 apps_changed 信号
        - 收到信号立即重载 config 中的软件列表并刷新本页全部卡片
        - AppEditDialog 仅在 AppManageDialog 内部被调用
        """
        dlg = AppManageDialog(self._config_manager, parent=self, theme=self._theme)
        dlg.apps_changed.connect(self._on_apps_changed)
        dlg.exec()

    def _on_apps_changed(self):
        """软件列表被管理对话框修改后：重载数据并刷新卡片网格。"""
        self.load_apps_from_config()
        self._refresh_cards()

    # ================================ 右键菜单（页面空白区） ================================
    def contextMenuEvent(self, event):
        """页面空白处右键：新增软件 / 管理软件列表。

        就地消费事件，不再冒泡到主窗口的全局右键兜底菜单
        （此前空白处右键会弹出「显示/隐藏悬浮球」，与本页无关）。
        """
        menu = QMenu(self)
        menu.addAction("新增软件", self._on_add_quick)
        menu.addAction("管理软件列表", self._on_manage_apps)
        menu.exec(event.globalPos())
        event.accept()

    # ================================ 卡片点击启动 ================================
    def _on_card_clicked(self, index: int):
        """左键点击卡片：异步启动外部 exe 程序。"""
        if index < 0 or index >= len(self.app_list):
            return
        self._launch_index(index)

    def _launch_index(self, index: int):
        """
        启动指定条目（左键与右键菜单「启动」共用）。

        - 具体启动逻辑（校验/Popen/740 提权处理）在模块级公共函数
          launch_app 中实现，与悬浮球小卡片的软件页共用同一套逻辑
        - 启动成功后若开启了 _auto_back_home，发射 request_switch_to_home 信号
        """
        if launch_app(self.app_list[index], parent=self):
            if self._auto_back_home:
                self.request_switch_to_home.emit()

    # ================================ 卡片右键动作分发 ================================
    def _on_card_action(self, index: int, action: str):
        """卡片右键菜单动作统一入口（action id 见 AppCardWidget.actionRequested）。"""
        if index < 0 or index >= len(self.app_list):
            return
        if action == "launch":
            self._launch_index(index)
        elif action == "edit":
            self._edit_app(index)
        elif action == "locate":
            self._locate_app(self.app_list[index])
        elif action == "copypath":
            self._copy_app_path(self.app_list[index])
        elif action == "remove":
            self._remove_app(index)

    def _edit_app(self, index: int):
        """编辑单个条目：复用 AppEditDialog，确认后写回 config 并刷新。"""
        dlg = AppEditDialog(self.app_list[index], parent=self,
                            theme=self._theme)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_app:
            self.app_list[index] = dlg.result_app
            self.save_apps_to_config()
            self._refresh_cards()

    def _locate_app(self, app: dict):
        """在资源管理器中定位软件文件（exe 或 lnk 本身，并选中）。"""
        path = (app.get("exe_path") or "").strip()
        if not path or not os.path.exists(path):
            GlassMessageBox.warning(
                self, "无法定位",
                f"「{app.get('name', '')}」的文件不存在：\n{path or '（空路径）'}\n\n"
                "文件可能已被移动或删除，请编辑该条目修正路径。")
            return
        try:
            # explorer /select,"path"：打开所在文件夹并选中该文件
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except OSError as e:
            GlassMessageBox.warning(self, "无法定位",
                                    f"打开资源管理器失败：\n{e}")

    def _copy_app_path(self, app: dict):
        """复制可执行文件路径到剪贴板（ScreenToast 轻提示反馈）。

        2026-10-05 改：此前刻意无声，现与全局复制操作反馈口径统一。
        """
        path = (app.get("exe_path") or "").strip()
        if path:
            QApplication.clipboard().setText(path)
            ScreenToast.show_msg("已复制路径", self._theme, kind="success")

    def _remove_app(self, index: int):
        """移除条目（二次确认；只从列表删除，不动电脑上的软件本体）。"""
        name = self.app_list[index].get("name", "")
        if GlassMessageBox.question(
                self, "确认移除",
                f"确定要从软件导航移除「{name}」吗？\n\n"
                "仅从列表中删除，不会卸载或删除电脑上的软件本体。",
                danger=True):
            del self.app_list[index]
            self.save_apps_to_config()
            self._refresh_cards()

    def _on_add_quick(self):
        """右键菜单直接新增一条软件（无需经过管理对话框）。"""
        dlg = AppEditDialog(None, parent=self, theme=self._theme)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_app:
            self.app_list.append(dlg.result_app)
            self.save_apps_to_config()
            self._refresh_cards()

    def _refresh_cards(self):
        """重算列数并重建卡片网格 + 更新计数（数据变更后统一出口）。"""
        self._current_cols = self._calc_columns()
        self._render_cards()
        self._update_count()


# ====================================================================
# 软件列表管理对话框（由主窗口设置页调用）
# ====================================================================

class AppManageDialog(QDialog):
    """
    软件列表管理对话框：新增、编辑、删除软件条目。

    由主窗口「设置」→「管理软件列表」按钮触发。
    管理结束后发射 apps_changed 信号，由主窗口刷新导航页面 UI。

    内部使用 AppEditDialog 进行新增/编辑操作。
    """

    # 软件列表发生变更时发射（新增/编辑/删除后）
    apps_changed = pyqtSignal()

    def __init__(self, config_manager, parent=None, theme: str = DEFAULT_THEME):
        super().__init__(parent)
        self._config_manager = config_manager
        self._theme = theme

        self.setWindowTitle("管理软件列表")
        self.setModal(True)
        self.resize(520, 420)

        self._build_ui()
        self._apply_style()
        self._refresh_list()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        """构建对话框布局：列表 + 操作按钮。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # 标题
        title = PageTitle("apps", "软件列表管理")
        title.apply_theme(self._theme)
        root.addWidget(title)

        # 提示
        hint = QLabel("在此管理所有已配置的软件条目，修改后导航页面会自动刷新。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 软件列表
        self._list_widget = QListWidget()
        tune_list_scrolling(self._list_widget)  # 丝滑化清单 L3：像素级滚动 + 统一步长
        self._list_widget.setAlternatingRowColors(True)
        self._list_widget.setSpacing(2)
        self._list_widget.currentRowChanged.connect(self._on_selection_changed)
        root.addWidget(self._list_widget, 1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._add_btn = IconButton("plus", text="新增", icon_size=14,
                                   object_name="secondaryBtn")
        self._add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self._add_btn)

        self._edit_btn = IconButton("edit", text="编辑", icon_size=14,
                                    object_name="secondaryBtn")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)
        btn_row.addWidget(self._edit_btn)

        self._delete_btn = IconButton("trash", text="删除", icon_size=14,
                                     object_name="secondaryBtn")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()

        self._close_btn = SmoothButton("关闭")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

    def _apply_style(self):
        """应用主题 QSS。"""
        qss = get_main_window_qss(self._theme)
        bg = get_colors(self._theme).get("bg", "#F8F9FA")
        extra = f"""
        QDialog {{ background-color: {bg}; }}
        QListWidget {{ font-size: 13px; }}
        """
        self.setStyleSheet(scale_px_fonts(qss + extra))

    # ---------------- 数据加载 ----------------
    @staticmethod
    def _status_dot(color: str) -> QIcon:
        """状态圆点（6px 直径、居中于 12px 画布）：有效 success / 失效 danger。"""
        pm = QPixmap(12, 12)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(3, 3, 6, 6)
        p.end()
        return QIcon(pm)

    def _refresh_list(self):
        """从 config 读取 apps 列表，刷新列表控件。"""
        self._list_widget.blockSignals(True)
        self._list_widget.clear()

        raw = self._config_manager.get("apps", [])
        if not isinstance(raw, list):
            raw = []
        self._apps = [
            {**APP_ITEM_TEMPLATE, **(item if isinstance(item, dict) else {})}
            for item in raw
        ]

        colors = get_colors(self._theme)
        for i, app in enumerate(self._apps):
            name = app.get("name", "未命名")
            exe = app.get("exe_path", "")
            valid = bool(exe) and os.path.exists(exe)
            # exe 是否有效的状态点：自绘 6px 圆点（有效 success / 失效 danger），
            # 语义与旧 🟢/🔴 一致，不再依赖 emoji 字体
            dot = self._status_dot(colors["success"] if valid else colors["danger"])
            item = QListWidgetItem(dot, f"{name}  —  {exe or '（空路径）'}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._list_widget.addItem(item)

        self._list_widget.blockSignals(False)

        # 选中第一项（如果有）
        if self._list_widget.count() > 0:
            self._list_widget.setCurrentRow(0)
        else:
            self._on_selection_changed(-1)

    def _on_selection_changed(self, row: int):
        """选中行变化时启用/禁用编辑和删除按钮。"""
        has_selection = row >= 0 and row < len(self._apps)
        self._edit_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    # ---------------- 操作槽函数 ----------------
    def _on_add(self):
        """新增软件条目。"""
        dlg = AppEditDialog(None, parent=self, theme=self._theme)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_app:
            self._apps.append(dlg.result_app)
            self._save_and_refresh()

    def _on_edit(self):
        """编辑选中的软件条目。"""
        row = self._list_widget.currentRow()
        if row < 0 or row >= len(self._apps):
            return
        dlg = AppEditDialog(self._apps[row], parent=self, theme=self._theme)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_app:
            self._apps[row] = dlg.result_app
            self._save_and_refresh()

    def _on_delete(self):
        """删除选中的软件条目（二次确认）。"""
        row = self._list_widget.currentRow()
        if row < 0 or row >= len(self._apps):
            return
        name = self._apps[row].get("name", "")
        if GlassMessageBox.question(
                self,
                "确认删除",
                f"确定要删除「{name}」吗？",
                danger=True):
            del self._apps[row]
            self._save_and_refresh()

    def _save_and_refresh(self):
        """保存列表到 config 并刷新 UI。"""
        self._config_manager.set("apps", self._apps)
        self._config_manager.save()
        self._refresh_list()
        self.apps_changed.emit()


# ====================================================================
# 新增/编辑软件弹窗（仅供 AppManageDialog 调用）
# ====================================================================

class AppEditDialog(QDialog):
    """
    新增 / 编辑软件条目弹窗（模态）。

    调用方：AppManageDialog（管理对话框）与 AppLauncherPage
    （导航页右键菜单「编辑…」/「新增软件」）。

    控件：
      - 软件名称输入框
      - exe 程序路径选择按钮（筛选 *.exe）
      - 图标路径选择按钮（可选 *.ico / *.png）
      - 备注输入框
      - 确认 / 取消按钮
      - 图标预览区（选择 exe 自动提取预览，手动选 ico/png 覆盖）

    用法：
      dlg = AppEditDialog(app_data)          # app_data 为 None 表示新增
      if dlg.exec() == QDialog.DialogCode.Accepted:
          result = dlg.result_app             # 编辑完成后的 app 对象（dict）
    """

    _ICON_PREVIEW_SIZE = 64  # 图标预览区边长（像素）

    def __init__(self, app_data=None, parent=None, theme: str = DEFAULT_THEME):
        super().__init__(parent)
        # 原始数据副本：新增时为空模板，编辑时为已有 app 数据。
        # 保留 enable / launch_args 等本阶段弹窗不暴露的字段。
        self._original = dict(APP_ITEM_TEMPLATE)
        if app_data:
            self._original.update(app_data)

        # 编辑完成后回传的 app 对象（确认后写入，取消则为 None）
        self.result_app = None

        self._theme = theme

        # 依据是否有已有数据判定标题：新增 / 编辑
        self.setWindowTitle("编辑软件" if app_data else "新增软件")
        self.setModal(True)

        self._build_ui()
        self._load_values()
        self._apply_style()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        """构建弹窗界面布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        # 软件名称
        self._name_edit = SmoothInput()
        self._name_edit.setPlaceholderText("请输入软件名称")
        form.addRow("软件名称:", self._name_edit)

        # exe 程序路径（输入框 + 浏览按钮）
        self._exe_edit = SmoothInput()
        self._exe_edit.setPlaceholderText("选择可执行文件（*.exe）")
        self._exe_btn = SmoothButton("浏览")
        self._exe_btn.setObjectName("secondaryBtn")
        self._exe_btn.clicked.connect(self._on_browse_exe)
        exe_row = QHBoxLayout()
        exe_row.setSpacing(6)
        exe_row.addWidget(self._exe_edit, 1)
        exe_row.addWidget(self._exe_btn)
        form.addRow("程序路径:", exe_row)

        # 图标路径（输入框 + 浏览按钮，可选）
        self._icon_edit = SmoothInput()
        self._icon_edit.setPlaceholderText("可选，图标文件（*.ico / *.png）")
        self._icon_btn = SmoothButton("浏览")
        self._icon_btn.setObjectName("secondaryBtn")
        self._icon_btn.clicked.connect(self._on_browse_icon)
        icon_row = QHBoxLayout()
        icon_row.setSpacing(6)
        icon_row.addWidget(self._icon_edit, 1)
        icon_row.addWidget(self._icon_btn)
        form.addRow("图标路径:", icon_row)

        # 备注
        self._remark_edit = SmoothInput()
        self._remark_edit.setPlaceholderText("备注说明（可选）")
        form.addRow("备注:", self._remark_edit)

        root.addLayout(form)

        # 图标预览区（居中显示）
        self._icon_preview = QLabel()
        self._icon_preview.setFixedSize(self._ICON_PREVIEW_SIZE, self._ICON_PREVIEW_SIZE)
        self._icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_preview.setToolTip("图标预览")
        preview_row = QHBoxLayout()
        preview_row.addStretch()
        preview_row.addWidget(self._icon_preview)
        preview_row.addStretch()
        root.addLayout(preview_row)
        root.addStretch()

        # 按钮区：取消 / 确认
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = SmoothButton("取消")
        self._cancel_btn.setObjectName("secondaryBtn")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._confirm_btn = SmoothButton("确认")
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm_btn)

        root.addLayout(btn_row)

        self.resize(420, 300)

    # ---------------- 数据回显 ----------------
    def _load_values(self):
        """将已有 app 数据回显到输入框，并刷新图标预览。"""
        self._name_edit.setText(self._original.get("name", ""))
        self._exe_edit.setText(self._original.get("exe_path", ""))
        self._icon_edit.setText(self._original.get("icon_path", ""))
        self._remark_edit.setText(self._original.get("remark", ""))
        self._refresh_preview()

    def _refresh_preview(self):
        """刷新图标预览：优先级为 手动图标文件 > 从 exe 提取 > 占位图标。"""
        icon_path = self._icon_edit.text().strip()
        exe_path = self._exe_edit.text().strip()

        if icon_path:
            pixmap = load_icon_pixmap(icon_path, self._ICON_PREVIEW_SIZE)
        elif exe_path:
            pixmap = extract_exe_icon(exe_path, self._ICON_PREVIEW_SIZE)
        else:
            pixmap = draw_placeholder_icon(self._ICON_PREVIEW_SIZE)

        self._icon_preview.setPixmap(pixmap)

    def _apply_style(self):
        """应用主题 QSS，保证弹窗与主窗口风格一致。"""
        qss = get_main_window_qss(self._theme)
        bg = get_colors(self._theme).get("bg", "#F8F9FA")
        dialog_extra = f"QDialog {{ background-color: {bg}; }}"
        self.setStyleSheet(qss + dialog_extra)

    # ---------------- 槽函数 ----------------
    def _on_browse_exe(self):
        """文件选择器：筛选 exe 文件，选中后自动提取并预览图标。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择可执行文件",
            self._exe_edit.text().strip() or "",
            "可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if not path:
            return
        self._exe_edit.setText(path)
        self._refresh_preview()

    def _on_browse_icon(self):
        """文件选择器：可选 ico/png 图标文件，选中后覆盖预览。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图标文件",
            self._icon_edit.text().strip() or "",
            "图标文件 (*.ico *.png);;所有文件 (*.*)",
        )
        if not path:
            return
        self._icon_edit.setText(path)
        self._refresh_preview()

    def _on_confirm(self):
        """确认：校验必填项，通过后组装 app 对象并 accept。"""
        name = self._name_edit.text().strip()
        exe_path = self._exe_edit.text().strip()

        # 必填校验：软件名称、exe 路径均不可为空
        if not name:
            GlassMessageBox.warning(self, "提示", "软件名称不能为空。")
            return
        if not exe_path:
            GlassMessageBox.warning(self, "提示", "请选择可执行文件（*.exe）。")
            return

        # 组装编辑结果：以原始模板为基底，覆盖弹窗暴露的字段，
        # 从而保留 enable / launch_args 等未在弹窗中编辑的字段
        result = dict(self._original)
        result["name"] = name
        result["exe_path"] = exe_path
        result["icon_path"] = self._icon_edit.text().strip()
        result["remark"] = self._remark_edit.text().strip()

        self.result_app = result
        self.accept()