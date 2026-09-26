# -*- coding: utf-8 -*-
"""
====================================================================
临时素材管理模块  -  TempAssetManager
====================================================================
独立封装临时素材（图片/文件）的全部业务逻辑，与 UI 层解耦。

设计要点：
  1. 使用与 exe / py 同目录下的 temp_assets/ 文件夹存放素材
     同时维护 temp_assets.json 元数据（记录添加时间、原文件名等）
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 拖入素材时自动复制原文件到 temp_assets/，文件名加时间戳防重名
  4. 总上限默认 10 个，超出按 added_time 升序淘汰最旧的（LRU）
  5. 元数据损坏不崩溃，自动初始化为空列表
  6. 文件被外部删除时，refresh() 同步清理失效记录
  7. UI 层只能通过本类公开方法操作，禁止直接读写 temp_assets/

模块导出：
  - AssetInfo      : 素材数据类
  - TempAssetManager : 素材管理器

素材字段（AssetInfo）：
  - asset_id       : 唯一主键，自增不复用
  - original_name  : 原文件名（拖入时的名称，便于识别）
  - stored_path    : 复制后的完整路径（temp_assets/ 下）
  - is_image       : 是否为图片（True 时主窗口可预览）
  - size_bytes     : 文件大小（字节）
  - added_time     : 添加时间 "YYYY-MM-DD HH:MM:SS"
====================================================================
"""

import os
import json
import shutil
import hashlib
from datetime import datetime, timedelta

from src.constants import sanitize_filename
from src.json_store import load_records


def _file_sha256(path: str) -> str:
    """计算文件 sha256 哈希，用于拖拽去重；失败返回空串。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


# 图片扩展名集合（小写，含点）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".svg"}


def _is_image(filename: str) -> bool:
    """根据扩展名判断是否为图片"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in _IMAGE_EXTS


# ====================================================================
# 素材数据类
# ====================================================================
class AssetInfo:
    """单条临时素材的数据载体"""

    def __init__(self, asset_id, original_name, stored_path, is_image,
                 size_bytes, added_time, content_hash=""):
        self.asset_id = asset_id              # 唯一主键，自增不复用
        self.original_name = original_name    # 原文件名
        self.stored_path = stored_path        # 复制后的完整路径
        self.is_image = is_image              # 是否为图片
        self.size_bytes = size_bytes          # 文件大小（字节）
        self.added_time = added_time          # 添加时间 "YYYY-MM-DD HH:MM:SS"
        self.content_hash = content_hash      # 文件内容 sha256，用于拖拽去重

    def to_dict(self):
        """序列化为字典"""
        return {
            "asset_id": self.asset_id,
            "original_name": self.original_name,
            "stored_path": self.stored_path,
            "is_image": self.is_image,
            "size_bytes": self.size_bytes,
            "added_time": self.added_time,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化，带类型校验"""
        return cls(
            asset_id=int(d.get("asset_id", 0) or 0),
            original_name=str(d.get("original_name", "")),
            stored_path=str(d.get("stored_path", "")),
            is_image=bool(d.get("is_image", False)),
            size_bytes=int(d.get("size_bytes", 0) or 0),
            added_time=str(d.get("added_time", "")),
            content_hash=str(d.get("content_hash", "") or ""),
        )

    def size_display(self) -> str:
        """返回人类可读的大小字符串"""
        size = self.size_bytes
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.2f} MB"


# ====================================================================
# 临时素材管理器
# ====================================================================
class TempAssetManager:
    """
    临时素材管理器。

    对外提供增删改查接口，内部维护内存素材列表，
    修改完毕统一调用 _save() 写入磁盘 json 文件。
    """

    DEFAULT_MAX_ASSETS = 50  # 默认总上限

    def __init__(self, base_dir: str, max_assets: int = DEFAULT_MAX_ASSETS,
                 max_days: int = 0):
        """
        base_dir  : 程序主目录（temp_assets/ 和 data/temp_assets.json 都建在这里）
        max_assets: 总上限（图片+文件合计）
        max_days  : 自动清理天数（0 表示不按天数清理）
        """
        self._base_dir = base_dir
        self._assets_dir = os.path.join(base_dir, "temp_assets")
        self._json_path = os.path.join(base_dir, "data", "temp_assets.json")
        self._max_assets = max(1, max_assets)
        self._max_days = max(0, max_days)
        self._assets = []                # 内存素材列表
        self._next_id = 1                # 下一个自增 asset_id（不复用）
        self._hash_index = {}            # content_hash -> asset_id 索引（拖拽去重）

        # 确保 temp_assets/ 和 data/ 目录存在
        try:
            os.makedirs(self._assets_dir, exist_ok=True)
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
        except OSError:
            pass

        self._load()
        # 加载后按天数清理过期素材
        if self._max_days > 0:
            self._cleanup_expired()

    # ---------------- 持久化 ----------------
    def _load(self):
        """从磁盘加载素材元数据（骨架见 json_store.load_records）。

        - 文件不存在 → 初始化空列表
        - json 解析异常 → 备份原文件后初始化空列表，不崩溃
        - 后处理：清理 stored_path 失效的记录（文件被外部删除）+ 重建 hash 索引
        """
        self._assets, self._next_id = load_records(
            self._json_path, "assets", AssetInfo.from_dict,
            min_next_id=lambda rs: max((a.asset_id for a in rs), default=0) + 1,
        )

        # 清理 stored_path 失效的记录（文件被外部删除/移动）
        invalid = [a for a in self._assets
                   if not a.stored_path or not os.path.exists(a.stored_path)]
        if invalid:
            invalid_ids = {a.asset_id for a in invalid}
            self._assets = [a for a in self._assets if a.asset_id not in invalid_ids]
            self._save()

        # 构建 content_hash 索引（仅保留文件仍有效的记录）
        self._rebuild_hash_index()

    def _rebuild_hash_index(self):
        """依据当前内存素材列表重建 content_hash -> asset_id 索引"""
        self._hash_index = {}
        for a in self._assets:
            if a.content_hash:
                self._hash_index[a.content_hash] = a.asset_id

    def _save(self):
        """统一保存：将内存素材列表一次性写入磁盘 json。"""
        data = {
            "assets": [a.to_dict() for a in self._assets],
            "next_id": self._next_id,
        }
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            # 原子写入
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            pass

    # ---------------- 配置更新 ----------------
    def update_limits(self, max_assets: int = None, max_days: int = None):
        """
        更新上限配置（由外部 ConfigManager 变更时调用）。
        - max_assets: 新的数量上限，None 表示不修改
        - max_days:   新的天数上限，None 表示不修改
        更新后立即执行清理。
        """
        if max_assets is not None:
            self._max_assets = max(1, max_assets)
        if max_days is not None:
            self._max_days = max(0, max_days)
        # 立即清理过期和超量素材
        if self._max_days > 0:
            self._cleanup_expired()
        self._cleanup_over_limit()

    def _cleanup_expired(self):
        """清理超过 max_days 天的素材"""
        if self._max_days <= 0:
            return
        threshold = datetime.now() - timedelta(days=self._max_days)
        expired = []
        survived = []
        for a in self._assets:
            try:
                added = datetime.strptime(a.added_time, "%Y-%m-%d %H:%M:%S")
                if added < threshold:
                    expired.append(a)
                else:
                    survived.append(a)
            except (ValueError, TypeError):
                # 时间解析失败 → 保留，不误删
                survived.append(a)
        if expired:
            for a in expired:
                # 删除物理文件
                if a.stored_path and os.path.exists(a.stored_path):
                    try:
                        os.remove(a.stored_path)
                    except OSError:
                        pass
            self._assets = survived
            self._save()

    def _cleanup_over_limit(self):
        """清理超出数量上限的最旧素材"""
        if len(self._assets) <= self._max_assets:
            return
        # 按 added_time 升序排序，淘汰最旧的
        sorted_assets = sorted(self._assets, key=lambda a: a.added_time)
        to_remove = sorted_assets[:len(self._assets) - self._max_assets]
        remove_ids = {a.asset_id for a in to_remove}
        for a in to_remove:
            if a.stored_path and os.path.exists(a.stored_path):
                try:
                    os.remove(a.stored_path)
                except OSError:
                    pass
        self._assets = [a for a in self._assets if a.asset_id not in remove_ids]
        self._save()

    # ---------------- 增删改查 ----------------
    def add_asset(self, source_path: str, display_name: str = None) -> int:
        """
        复制源文件到 temp_assets/，加入素材列表。
        超出上限时自动按 added_time 升序淘汰最旧的（连同文件一起删除）。
        去重（任务 6.3）：若内容哈希（sha256）已存在且文件仍有效，
        视为重复拖入 —— 不再复制/新增，仅刷新其 added_time（移到最新，
        避免被淘汰）并返回已有 asset_id。
        display_name：可选。列表里展示的名称，默认取源文件名。
        剪贴板截图这类「中转文件」落盘名由本方法统一生成（带时间戳），
        展示名需要自己带时间戳以便区分，故单独传入。
        返回新素材的 asset_id；失败返回 -1。
        """
        if not source_path or not os.path.isfile(source_path):
            return -1

        # ---- 去重判断：基于内容哈希 ----
        src_hash = _file_sha256(source_path)
        if src_hash and src_hash in self._hash_index:
            existing_id = self._hash_index[src_hash]
            existing = next((a for a in self._assets if a.asset_id == existing_id), None)
            if existing is not None and existing.stored_path \
                    and os.path.exists(existing.stored_path):
                # 重复素材：刷新到最新时间，避免被过期/淘汰清理掉
                existing.added_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save()
                return existing.asset_id

        # 生成存储文件名：原文件名（去扩展名）+ 时间戳 + 原扩展名
        original_name = os.path.basename(source_path)
        name_stem, ext = os.path.splitext(original_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 防止文件名过长截断
        name_stem = name_stem[:30]
        # 净化落盘文件名中的 Windows 非法字符，避免 shutil.copy2 失败
        name_stem = sanitize_filename(name_stem)
        stored_filename = f"{name_stem}_{timestamp}{ext}"
        stored_path = os.path.join(self._assets_dir, stored_filename)

        # 防止重名（极小概率）：若已存在则加序号
        counter = 1
        while os.path.exists(stored_path):
            stored_filename = f"{name_stem}_{timestamp}_{counter}{ext}"
            stored_path = os.path.join(self._assets_dir, stored_filename)
            counter += 1

        # 复制文件
        try:
            shutil.copy2(source_path, stored_path)
        except OSError:
            return -1

        # 获取文件大小
        try:
            size_bytes = os.path.getsize(stored_path)
        except OSError:
            size_bytes = 0

        # 创建素材记录
        asset = AssetInfo(
            asset_id=self._next_id,
            original_name=sanitize_filename(display_name or original_name),
            stored_path=stored_path,
            is_image=_is_image(stored_path),
            size_bytes=size_bytes,
            added_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            content_hash=src_hash,
        )
        self._assets.append(asset)
        if src_hash:
            self._hash_index[src_hash] = asset.asset_id
        self._next_id += 1

        # 超出上限淘汰最旧的
        self._evict_if_needed()

        self._save()
        return asset.asset_id

    def _evict_if_needed(self):
        """超出上限时淘汰最旧的素材（按 added_time 升序，连带删除文件）"""
        while len(self._assets) > self._max_assets:
            # 按 added_time 升序，最早的在前
            self._assets.sort(key=lambda a: a.added_time)
            oldest = self._assets.pop(0)
            # 删除物理文件（失败不报错）
            try:
                if oldest.stored_path and os.path.exists(oldest.stored_path):
                    os.remove(oldest.stored_path)
            except OSError:
                pass
        # 淘汰可能移除记录，重建索引保持一致
        self._rebuild_hash_index()

    def delete_asset(self, asset_id: int) -> bool:
        """
        删除素材。
        严格按 asset_id 查找，删除记录 + 物理文件。
        """
        for i, a in enumerate(self._assets):
            if a.asset_id == asset_id:
                # 删除物理文件
                try:
                    if a.stored_path and os.path.exists(a.stored_path):
                        os.remove(a.stored_path)
                except OSError:
                    pass
                self._assets.pop(i)
                self._rebuild_hash_index()
                self._save()
                return True
        return False

    def get_asset(self, asset_id: int):
        """按 asset_id 获取单条素材，不存在返回 None"""
        for a in self._assets:
            if a.asset_id == asset_id:
                return a
        return None

    def get_all_assets(self):
        """返回全部素材，按添加时间倒序（最新在前）"""
        return sorted(self._assets, key=lambda a: a.added_time, reverse=True)

    def get_assets_dir(self) -> str:
        """返回素材存储目录的绝对路径（兼容打包环境）"""
        return self._assets_dir

    def count(self) -> int:
        """返回素材总数"""
        return len(self._assets)

    def refresh(self):
        """
        重新校验素材列表，清理已失效的记录（文件被外部删除/移动）。
        适合在主窗口面板显示前调用。
        """
        invalid = [a for a in self._assets
                   if not a.stored_path or not os.path.exists(a.stored_path)]
        if invalid:
            invalid_ids = {a.asset_id for a in invalid}
            self._assets = [a for a in self._assets if a.asset_id not in invalid_ids]
            self._rebuild_hash_index()
            self._save()

    def clear_all(self) -> int:
        """
        清空所有素材（删除记录 + 物理文件）。
        返回清理的数量。
        """
        count = len(self._assets)
        for a in self._assets:
            try:
                if a.stored_path and os.path.exists(a.stored_path):
                    os.remove(a.stored_path)
            except OSError:
                pass
        self._assets = []
        self._rebuild_hash_index()
        self._save()
        return count

    def open_asset(self, asset_id: int) -> bool:
        """
        用系统默认程序打开素材文件（Windows 用 os.startfile）。
        返回是否成功调用打开。
        """
        asset = self.get_asset(asset_id)
        if not asset or not asset.stored_path:
            return False
        if not os.path.exists(asset.stored_path):
            return False
        try:
            os.startfile(asset.stored_path)
            return True
        except OSError:
            return False
