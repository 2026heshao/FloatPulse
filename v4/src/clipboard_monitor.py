# -*- coding: utf-8 -*-
"""
====================================================================
剪贴板被动监听模块  -  ClipboardMonitor
====================================================================
监听系统剪贴板变化（QClipboard.dataChanged 信号），
将用户 Ctrl+C 复制的内容自动归一化进 FragmentManager。

设计要点：
  1. 被动监听，不引入全局鼠标/键盘钩子
  2. 自己写入剪贴板前调用 suppress_next() 标记，避免循环捕获
  3. 自动识别文件路径（os.path.exists() 为真 → 标记为 path 类型）
  4. 多文件复制时按行拆分，每行作为一条独立路径碎片
  5. 短时间内相同内容去重（防止部分应用重复触发 dataChanged）
  6. 通过 fragment_added 信号通知 UI 刷新
  7. 容量上限自动调用 FragmentManager.trim_to_max FIFO 淘汰
  8. 可通过 set_enabled(False) 临时关闭监听
  9. 应用过滤（D4）：前台进程名在 config「clipboard_filter_apps」
     名单中的复制不捕获（通过 ctypes 取前台窗口进程名，无额外依赖）
 10. 图片捕获（Y2）：剪贴板里是图片（截图/复制图片）且不含文本时，
     落盘为临时文件后交给 TempAssetManager 存入素材池，
     再删除中转文件；单张上限 IMAGE_MAX_BYTES，按内容哈希去重

不实现的功能：
  - 不把图片存成碎片（图片统一进素材池，与拖拽到悬浮球的行为一致）
====================================================================
"""

import os
import shutil
import hashlib
import tempfile
import ctypes
from ctypes import wintypes
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from src.fragment_manager import (
    FragmentManager,
    TYPE_CLIPBOARD_TEXT,
    TYPE_CLIPBOARD_PATH,
)

# 剪贴板图片 MIME → 落盘扩展名（md.hasImage() 为假时的兜底路径）
_IMAGE_MIME_EXTS = {
    "image/png":  ".png",
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/bmp":  ".bmp",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "image/tiff": ".tiff",
}


def _sha256_of_file(path: str) -> str:
    """计算文件 sha256（用于图片去重），失败返回空串"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


# ---------------- 前台进程名获取（Windows 原生，无第三方依赖） ----------------
_kernel32 = None
_user32 = None

def _get_foreground_process_name() -> str:
    """
    返回当前前台窗口所属进程的完整镜像路径（如 C:\\...\\WeChat.exe）。
    任何一步失败返回空串（过滤逻辑对空串放行，不阻断正常捕获）。
    """
    global _kernel32, _user32
    try:
        if _user32 is None:
            _user32 = ctypes.windll.user32
            _kernel32 = ctypes.windll.kernel32
            _user32.GetForegroundWindow.restype = wintypes.HWND
            _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            _kernel32.OpenProcess.restype = wintypes.HANDLE
            _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            _kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION：无需管理员权限即可查询镜像路径
        handle = _kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(260)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:
        return ""


class ClipboardMonitor(QObject):
    """
    剪贴板被动监听器。

    信号：
      - fragment_added(int)   : 新增碎片时发射，参数为 fragment_id
      - path_detected(str)    : 检测到文件路径时发射（可用于UI提示）
      - fragments_trimmed(int): 容量超限自动淘汰碎片时发射，参数为淘汰条数
      - image_captured(int)   : 剪贴板图片已存入素材池，参数为 asset_id
    """

    fragment_added = pyqtSignal(int)
    path_detected = pyqtSignal(str)
    fragments_trimmed = pyqtSignal(int)
    image_captured = pyqtSignal(int)

    # 短时间内去重窗口（毫秒），同一内容在此间隔内不重复捕获
    DEDUP_INTERVAL_MS = 800

    # 单张剪贴板图片上限（落盘字节数），超过则跳过，避免一次复制塞满磁盘
    IMAGE_MAX_BYTES = 20 * 1024 * 1024

    # 图片最小边长：某些程序会把 1×1 位图放进剪贴板（如取色器），
    # 收集下来只会变成垃圾素材，直接放行
    IMAGE_MIN_SIDE = 4

    def __init__(self, fragment_manager: FragmentManager, config_manager=None,
                 temp_asset_manager=None):
        super().__init__()
        self._fm = fragment_manager
        self._config = config_manager
        # 素材池（Y2）：剪贴板图片统一进这里；未注入时图片捕获自动降级为不处理
        self._temp_asset_manager = temp_asset_manager
        self._clipboard = QApplication.clipboard()
        self._enabled = True
        # 自己写入时设为 True，下次 dataChanged 不处理
        self._suppress_flag = False
        # 上一次捕获的内容文本，用于去重
        self._last_text = ""
        # 上一次捕获的图片内容哈希，用于去重（同一张图多次 dataChanged）
        self._last_image_hash = ""
        # 监听是否已启动
        self._started = False

    # ---------------- 启停 ----------------
    def start(self):
        """开始监听剪贴板（连接 dataChanged 信号）"""
        if self._started:
            return
        if self._clipboard is not None:
            self._clipboard.dataChanged.connect(self._on_data_changed)
            self._started = True

    def stop(self):
        """停止监听（断开 dataChanged 信号）"""
        if not self._started:
            return
        if self._clipboard is not None:
            try:
                self._clipboard.dataChanged.disconnect(self._on_data_changed)
            except (TypeError, RuntimeError):
                pass
        self._started = False

    def set_enabled(self, enabled: bool):
        """启用/禁用捕获（不切断信号连接，仅不处理）"""
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    # ---------------- 自己写入剪贴板时调用 ----------------
    def suppress_next(self):
        """
        在调用 clipboard.setText() 之前调用本方法，
        下一次 dataChanged 信号将被忽略，避免循环捕获。
        """
        self._suppress_flag = True

    def put_text(self, text: str):
        """
        主动写入剪贴板文本（自动 suppress 下一次 dataChanged）。
        供「碎片合并 → 复制到剪贴板」使用。
        """
        if self._clipboard is not None:
            self.suppress_next()
            self._clipboard.setText(text)

    # ---------------- 信号处理 ----------------
    def _on_data_changed(self):
        """剪贴板变化回调"""
        # 抑制标志：本次是自己写入，跳过
        if self._suppress_flag:
            self._suppress_flag = False
            return
        if not self._enabled:
            return
        if self._clipboard is None:
            return

        # 应用过滤（D4）：前台进程在过滤名单中 → 不捕获。
        # 注意放在读取文本与去重缓存更新之前，被过滤的内容不影响 _last_text。
        if self._is_filtered_app(_get_foreground_process_name()):
            return

        try:
            md = self._clipboard.mimeData()
        except Exception:
            md = None
        try:
            text = self._clipboard.text()
        except Exception:
            text = ""

        # 图片优先（Y2）：仅当剪贴板「没有文本」时才把图片收进素材池。
        # 这样 Excel/Word 里连文字带位图的复制仍按文本处理（用户要的是文字），
        # 只有 Win+Shift+S 截图、浏览器「复制图片」这类纯图片复制才走本分支。
        if not text:
            if self._capture_images_enabled() and md is not None \
                    and self._mime_has_image(md):
                if self._capture_clipboard_image(md):
                    # 图片入库后清掉文本去重缓存，避免影响后续文本判定
                    self._last_text = ""
            return

        # 去重：与上次相同则跳过
        if text == self._last_text:
            return
        self._last_text = text

        # 解析内容：判断是文件路径还是普通文本
        self._parse_and_add(text)

    def _parse_and_add(self, text: str):
        """
        解析剪贴板文本并加入碎片池。
        - 多行文本：检查每行是否为存在的文件路径
          - 全部都是路径 → 每行作为一条路径碎片
          - 部分是路径 → 整体作为一条文本碎片（不拆分）
          - 都不是路径 → 整体作为一条文本碎片
        """
        lines = text.splitlines()
        if len(lines) > 1:
            # 多行：检查是否全部是路径
            all_paths = all(self._is_valid_path(line) for line in lines if line.strip())
            if all_paths:
                # 每行作为一条路径碎片
                for line in lines:
                    line = line.strip()
                    if line:
                        self._add_path_fragment(line)
                return
        # 单行或非全路径：检查单行是否为路径
        single = text.strip()
        if self._is_valid_path(single):
            self._add_path_fragment(single)
        else:
            self._add_text_fragment(text)

    def _add_text_fragment(self, content: str):
        """添加文本碎片"""
        try:
            fid = self._fm.add_clipboard_text(content, source="剪贴板")
            self._trim_if_needed()
            self.fragment_added.emit(fid)
        except Exception:
            pass

    def _add_path_fragment(self, path: str):
        """添加路径碎片"""
        try:
            fid = self._fm.add_clipboard_path(path, source="剪贴板")
            self._trim_if_needed()
            self.fragment_added.emit(fid)
            self.path_detected.emit(path)
        except Exception:
            pass

    # ---------------- 图片捕获（Y2） ----------------
    def _capture_images_enabled(self) -> bool:
        """配置开关：剪贴板图片是否自动存入素材池（未注入配置时默认开启）"""
        if self._config is None:
            return True
        return bool(self._config.get("clipboard_capture_images", True))

    @staticmethod
    def _mime_has_image(md) -> bool:
        """剪贴板 MIME 里是否带图片（标准 hasImage 或明确的图片 MIME 格式）"""
        try:
            if md.hasImage():
                return True
            for fmt in _IMAGE_MIME_EXTS:
                if md.hasFormat(fmt):
                    return True
        except Exception:
            pass
        return False

    def _capture_clipboard_image(self, md) -> bool:
        """
        把剪贴板图片存入素材池。

        流程：写入系统临时目录的中转文件 → 大小闸门 → 内容哈希去重
        → TempAssetManager.add_asset（复制一份进 temp_assets/）→ 删除中转文件。
        返回是否成功入库；任何一步失败都安静返回 False（不影响文本捕获路径）。
        """
        if self._temp_asset_manager is None:
            return False
        try:
            tmp_dir = tempfile.mkdtemp(prefix="floatpulse_clip_")
        except OSError:
            return False

        try:
            path = self._write_clipboard_image(md, tmp_dir)
            if not path:
                return False
            try:
                size = os.path.getsize(path)
            except OSError:
                return False
            if size <= 0 or size > self.IMAGE_MAX_BYTES:
                return False

            digest = _sha256_of_file(path)
            if digest and digest == self._last_image_hash:
                return False        # 同一张图重复触发 dataChanged

            # 展示名自带时间戳：多张截图在素材列表里可区分
            # （落盘名由 add_asset 统一生成，不带重复时间戳）
            ext = os.path.splitext(path)[1] or ".png"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            asset_id = self._temp_asset_manager.add_asset(
                path, display_name=f"剪贴板图片_{ts}{ext}")
            if asset_id is None or asset_id <= 0:
                return False
            if digest:
                self._last_image_hash = digest
            self.image_captured.emit(int(asset_id))
            return True
        except Exception:
            return False
        finally:
            # 中转文件用完即删（add_asset 已把内容复制进 temp_assets/）
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _write_clipboard_image(md, tmp_dir: str) -> str:
        """
        把剪贴板图片写进指定目录，返回文件路径；失败返回空串。
        优先用 QImage 统一转 PNG；没有标准图片对象时按原始 MIME 字节存盘。
        文件名固定为「剪贴板图片」，时间戳与去重后缀交给
        TempAssetManager.add_asset 统一生成，避免名称里出现两段时间戳。
        """
        try:
            if md.hasImage():
                img = QImage(md.imageData())
                if img.isNull() or img.width() <= 0 or img.height() <= 0:
                    return ""
                # 垃圾位图（取色器/单像素）不入库
                if (img.width() < ClipboardMonitor.IMAGE_MIN_SIDE
                        or img.height() < ClipboardMonitor.IMAGE_MIN_SIDE):
                    return ""
                path = os.path.join(tmp_dir, "剪贴板图片.png")
                return path if img.save(path, "PNG") else ""
            for fmt, ext in _IMAGE_MIME_EXTS.items():
                if md.hasFormat(fmt):
                    raw = md.data(fmt)
                    data = bytes(raw) if raw is not None else b""
                    if not data:
                        continue
                    path = os.path.join(tmp_dir, f"剪贴板图片{ext}")
                    with open(path, "wb") as f:
                        f.write(data)
                    return path
        except Exception:
            return ""
        return ""

    def _trim_if_needed(self):
        """检查碎片容量，超限时按 FIFO 淘汰，并对外通报淘汰条数"""
        if self._config is None:
            return
        max_items = self._config.get("clipboard_max_items", 200)
        try:
            removed = self._fm.trim_to_max(int(max_items))
        except Exception:
            return
        if removed > 0:
            # 让 UI 能提示"数据为什么少了"（此前是静默删除）
            self.fragments_trimmed.emit(int(removed))

    @staticmethod
    def _normalize_process_name(name: str) -> str:
        """进程名归一化：取文件名、转小写、去掉 .exe 后缀"""
        name = str(name).strip().replace("\\", "/").split("/")[-1].lower()
        if name.endswith(".exe"):
            name = name[:-4]
        return name

    def _is_filtered_app(self, process_path: str) -> bool:
        """判断前台进程是否在过滤名单中（大小写与 .exe 后缀不敏感）"""
        if self._config is None:
            return False
        foreground = self._normalize_process_name(process_path)
        if not foreground:
            return False
        filters = self._config.get("clipboard_filter_apps", []) or []
        for item in filters:
            f = self._normalize_process_name(item)
            if f and f == foreground:
                return True
        return False

    @staticmethod
    def _is_valid_path(text: str) -> bool:
        """判断文本是否为存在的文件/目录路径"""
        text = text.strip()
        if not text:
            return False
        # 去除可能的引号包裹
        if len(text) >= 2 and text[0] in '"\'' and text[-1] == text[0]:
            text = text[1:-1]
        if not text:
            return False
        try:
            return os.path.exists(text)
        except (OSError, ValueError):
            return False

    # ---------------- 工具 ----------------
    def reset_last_text(self):
        """重置去重缓存（切换主题/重新加载时调用）"""
        self._last_text = ""
        self._last_image_hash = ""
