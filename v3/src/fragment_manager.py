# -*- coding: utf-8 -*-
"""
====================================================================
碎片统一管理模块  -  FragmentManager
====================================================================
统一管理四类碎片（剪贴板文本 / 剪贴板路径 / 文件拾取 / 知识卡片段落），
与 UI 层解耦。所有碎片归一化进同一 fragments.json，便于浏览/筛选/合并。

设计要点：
  1. 使用与 exe / py 同目录下的 fragments.json 持久化碎片数据
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化空列表；json 解析异常不崩溃
  4. 每条碎片拥有自增且不复用的唯一 fragment_id 主键
  5. 碎片字段：fragment_id / type / content / source / created_at
  6. 所有增删改先操作内存列表，完毕统一调用 _save() 写盘
  7. 删除严格按 fragment_id 过滤
  8. 提供 trim_to_max() 在剪贴板历史超限时 FIFO 淘汰

碎片类型常量：
  - TYPE_CLIPBOARD_TEXT    : 剪贴板文本（Ctrl+C 复制的文本）
  - TYPE_CLIPBOARD_PATH   : 剪贴板路径（Ctrl+C 复制的文件路径）
  - TYPE_FILE_PICKUP      : 文件拾取（拖拽文件到悬浮球）
  - TYPE_KNOWLEDGE_SEGMENT: 知识卡片段落（用户勾选 docx 段落加入）
====================================================================
"""

import os
import json
import threading
from datetime import datetime

from src.json_store import load_records


# ====================================================================
# 去抖写盘器
# ====================================================================
class _DebouncedSaver:
    """
    线程安全的去抖写盘器。

    高频调用 schedule() 时，仅在「连续调用平息后等待 interval 秒」
    才真正执行一次 callback（合并多次写入）。
    - 合并写入：窗口期内任意次 schedule 只触发一次真实写盘
    - 线程安全：内部用锁保护定时器，可在主线程或任意线程调用
    - flush()：立即触发未决写盘并等待完成（程序退出/关键落盘前用）
    - cancel()：取消未决写盘

    设计上不依赖 Qt，避免 fragment_manager 模块被绑定到 GUI 事件循环，
    同时也兼容 Qt 主线程调用（callback 在定时器线程执行，不触碰 GUI）。
    """

    def __init__(self, interval: float, callback):
        self._interval = interval
        self._callback = callback
        self._lock = threading.Lock()
        self._timer = None
        self._pending = False

    def schedule(self):
        """安排一次延迟写盘（去抖）。"""
        with self._lock:
            self._pending = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._interval, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        with self._lock:
            self._timer = None
            if not self._pending:
                return
            self._pending = False
            callback = self._callback
        # 在锁外执行回调，避免死锁
        try:
            callback()
        except Exception:
            pass

    def flush(self):
        """立即触发未决写盘并等待其执行完毕（同步）。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._pending
            self._pending = False
        if pending:
            try:
                self._callback()
            except Exception:
                pass

    def cancel(self):
        """取消未决写盘（不执行）。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False


# 碎片类型常量
TYPE_CLIPBOARD_TEXT    = "clipboard_text"
TYPE_CLIPBOARD_PATH    = "clipboard_path"
TYPE_FILE_PICKUP       = "file_pickup"
TYPE_KNOWLEDGE_SEGMENT = "knowledge_segment"

# 类型中文名映射（UI 显示用）
TYPE_LABELS = {
    TYPE_CLIPBOARD_TEXT:    "剪贴板文本",
    TYPE_CLIPBOARD_PATH:    "剪贴板路径",
    TYPE_FILE_PICKUP:       "文件拾取",
    TYPE_KNOWLEDGE_SEGMENT: "知识段落",
}

# 类型图标映射（UI 显示用）
TYPE_ICONS = {
    TYPE_CLIPBOARD_TEXT:    "📋",
    TYPE_CLIPBOARD_PATH:    "📁",
    TYPE_FILE_PICKUP:       "📥",
    TYPE_KNOWLEDGE_SEGMENT: "📚",
}


# ====================================================================
# 碎片数据类
# ====================================================================
class Fragment:
    """单条碎片的数据载体"""

    def __init__(self, fragment_id, ftype, content, source, created_at):
        self.fragment_id = fragment_id          # 唯一主键，自增不复用
        self.type = ftype                       # 碎片类型（见 TYPE_*）
        self.content = content                   # 碎片内容
        self.source = source                     # 来源描述
        self.created_at = created_at             # 创建时间 "YYYY-MM-DD HH:MM"

    def to_dict(self):
        """序列化为字典"""
        return {
            "fragment_id": self.fragment_id,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化，带类型校验防止损坏数据崩溃"""
        return cls(
            fragment_id=int(d.get("fragment_id", 0) or 0),
            ftype=str(d.get("type", TYPE_CLIPBOARD_TEXT)),
            content=str(d.get("content", "")),
            source=str(d.get("source", "")),
            created_at=str(d.get("created_at", "")),
        )

    def preview(self, max_len: int = 50) -> str:
        """返回用于列表预览的截断文本（去掉换行）"""
        text = self.content.replace("\n", " ").replace("\r", " ").strip()
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text


# ====================================================================
# 碎片管理器
# ====================================================================
class FragmentManager:
    """
    碎片统一管理器。

    对外提供增删改查接口，内部维护内存碎片列表，
    修改完毕统一调用 _save() 写入磁盘 json 文件。
    UI 层禁止直接读写 fragments.json 文件。
    """

    # 去抖写盘间隔（秒）：高频碎片新增在此窗口内合并为一次落盘
    SAVE_DEBOUNCE_SECONDS = 1.0

    def __init__(self, json_path: str):
        self._json_path = json_path     # fragments.json 完整路径
        self._fragments = []            # 内存碎片列表
        self._next_id = 1               # 下一个自增 fragment_id（不复用）
        self._saver = _DebouncedSaver(self.SAVE_DEBOUNCE_SECONDS, self._save)
        self._load()

    def close(self):
        """释放资源：强制落盘所有未决变更并停止定时器（程序退出时调用）。"""
        self._saver.flush()

    # ---------------- 持久化 ----------------
    def _load(self):
        """从磁盘加载碎片数据（骨架见 json_store.load_records）。

        - 文件不存在 → 初始化空列表
        - json 解析异常 → 备份原文件后初始化空列表，不崩溃
        """
        self._fragments, self._next_id = load_records(
            self._json_path, "fragments", Fragment.from_dict,
            min_next_id=lambda rs: max((f.fragment_id for f in rs), default=0) + 1,
        )

    def _save(self):
        """统一保存：将内存碎片列表一次性写入磁盘 json（原子写入）。"""
        data = {
            "fragments": [f.to_dict() for f in self._fragments],
            "next_id": self._next_id,
        }
        try:
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self._json_path)
        except OSError:
            pass

    def save(self):
        """立即落盘（同步）。用于需要保证数据已写入磁盘的场景，例如程序退出。"""
        self._saver.flush()

    def mark_dirty(self):
        """标记数据已变更，安排一次去抖写盘（高频新增由此合并写入）。"""
        self._saver.schedule()

    def flush(self):
        """立即落盘所有未决变更（同步）。"""
        self._saver.flush()

    # ---------------- 增 ----------------
    # 重复检测窗口：以某条碎片为中心，向上6条 + 自己 + 向下6条，
    # 连续13条范围内内容不可重复；超出该范围的旧碎片允许再次出现。
    # 新碎片固定追加在列表末尾（下方无碎片），故新增时向上检查最近12条，
    # 即可保证任意连续13条窗口内不出现重复碎片。
    DUP_WINDOW = 12

    def add_fragment(self, ftype: str, content: str, source: str = "") -> int:
        """
        新建碎片（追加到列表末尾），返回新碎片的 fragment_id。

        重复规则（局部去重，非全局）：
        - 仅与末尾最近 12 条比对（self.DUP_WINDOW）
        - 命中重复 → 静默跳过，返回已存在碎片的 fragment_id
        - 距离超过 12 条的相同内容 → 允许再次新增
        """
        # 局部去重：只检查最近 12 条（列表不足 12 条时全部检查）
        for f in self._fragments[-self.DUP_WINDOW:]:
            if f.content == content:
                return f.fragment_id
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        fragment = Fragment(
            fragment_id=self._next_id,
            ftype=ftype,
            content=content,
            source=source,
            created_at=now,
        )
        self._fragments.append(fragment)
        self._next_id += 1
        self.mark_dirty()
        return fragment.fragment_id

    def add_clipboard_text(self, content: str, source: str = "") -> int:
        """快捷方法：添加剪贴板文本碎片"""
        return self.add_fragment(TYPE_CLIPBOARD_TEXT, content, source)

    def add_clipboard_path(self, path: str, source: str = "") -> int:
        """快捷方法：添加剪贴板路径碎片"""
        return self.add_fragment(TYPE_CLIPBOARD_PATH, path, source)

    def add_file_pickup(self, path: str, source: str = "拖拽拾取") -> int:
        """快捷方法：添加文件拾取碎片"""
        return self.add_fragment(TYPE_FILE_PICKUP, path, source)

    def add_knowledge_segment(self, content: str, source: str = "知识库") -> int:
        """快捷方法：添加知识卡片段落碎片"""
        return self.add_fragment(TYPE_KNOWLEDGE_SEGMENT, content, source)

    # ---------------- 改 ----------------
    def update_fragment(self, fragment_id: int, content=None, source=None) -> bool:
        """更新碎片的 content / source（仅传入的字段生效）。

        - content 为 None 表示不改；去空白后为空则拒绝修改（返回 False），
          避免产生空碎片
        - 返回是否真的发生变化（无变化返回 False，不触发写盘）
        """
        frag = self.get_fragment(fragment_id)
        if frag is None:
            return False
        changed = False
        if content is not None:
            text = str(content)
            if not text.strip():
                return False
            if text != frag.content:
                frag.content = text
                changed = True
        if source is not None and str(source) != frag.source:
            frag.source = str(source)
            changed = True
        if changed:
            self.mark_dirty()
        return changed

    # ---------------- 删 ----------------
    def delete_fragment(self, fragment_id: int) -> bool:
        """删除单条碎片（严格按 fragment_id）"""
        before = len(self._fragments)
        self._fragments = [f for f in self._fragments if f.fragment_id != fragment_id]
        if len(self._fragments) < before:
            self.mark_dirty()
            return True
        return False

    def delete_fragments(self, fragment_ids: list) -> int:
        """批量删除碎片，返回成功删除的条数"""
        id_set = set(fragment_ids)
        before = len(self._fragments)
        self._fragments = [f for f in self._fragments if f.fragment_id not in id_set]
        deleted = before - len(self._fragments)
        if deleted > 0:
            self.mark_dirty()
        return deleted

    def clear_all(self) -> int:
        """清空全部碎片，返回被清空的条数"""
        count = len(self._fragments)
        self._fragments = []
        if count > 0:
            self.mark_dirty()
        return count

    def clear_by_type(self, ftype: str) -> int:
        """按类型清空，返回被清空的条数"""
        before = len(self._fragments)
        self._fragments = [f for f in self._fragments if f.type != ftype]
        deleted = before - len(self._fragments)
        if deleted > 0:
            self.mark_dirty()
        return deleted

    # ---------------- 查 ----------------
    def get_all_fragments(self) -> list:
        """
        返回全部碎片，按创建时间倒序（最新在前）。

        排序键：(created_at, fragment_id) 双键倒序。
        原因：created_at 只有分钟精度（%Y-%m-%d %H:%M），同一分钟内
        连续添加的多条碎片时间戳相同，若只用单键排序，稳定排序会
        保持添加顺序（旧的在前），导致新碎片"插在中间"而非置顶。
        fragment_id 严格自增，作次级键可精确区分同分钟碎片的先后，
        保证最新添加的碎片一定排在最上方。
        """
        return sorted(
            self._fragments,
            key=lambda f: (f.created_at, f.fragment_id),
            reverse=True,
        )

    def get_fragments_by_type(self, ftype: str) -> list:
        """按类型筛选，按创建时间倒序"""
        return [f for f in self.get_all_fragments() if f.type == ftype]

    def get_fragment(self, fragment_id: int):
        """按 fragment_id 获取单条碎片，不存在返回 None"""
        for f in self._fragments:
            if f.fragment_id == fragment_id:
                return f
        return None

    def get_fragments_by_ids(self, fragment_ids: list) -> list:
        """按 fragment_id 列表批量获取，保持传入顺序"""
        id_to_frag = {f.fragment_id: f for f in self._fragments}
        result = []
        for fid in fragment_ids:
            frag = id_to_frag.get(fid)
            if frag is not None:
                result.append(frag)
        return result

    def search_fragments(self, keyword: str) -> list:
        """按关键字搜索 content 和 source，返回匹配的碎片"""
        kw = keyword.lower().strip()
        if not kw:
            return self.get_all_fragments()
        return [f for f in self.get_all_fragments()
                if kw in f.content.lower() or kw in f.source.lower()]

    # ---------------- 容量管理 ----------------
    def count(self) -> int:
        """返回碎片总数"""
        return len(self._fragments)

    def count_by_type(self, ftype: str) -> int:
        """按类型计数"""
        return sum(1 for f in self._fragments if f.type == ftype)

    def trim_to_max(self, max_count: int) -> int:
        """
        超限时 FIFO 淘汰最早的碎片。
        返回被淘汰的条数。

        排序键与 get_all_fragments 保持一致（created_at + fragment_id 双键）：
        created_at 只有分钟精度，同一分钟内新增的多条碎片必须靠自增 id
        区分先后，否则淘汰顺序不确定（可能出现"该留的被删、该删的留下"）。
        """
        if max_count <= 0 or len(self._fragments) <= max_count:
            return 0
        # 按 (创建时间, 自增 id) 升序（最早在前）
        sorted_frags = sorted(self._fragments,
                              key=lambda f: (f.created_at, f.fragment_id))
        to_remove = len(self._fragments) - max_count
        # 取出要淘汰的 fragment_id
        remove_ids = {f.fragment_id for f in sorted_frags[:to_remove]}
        self._fragments = [f for f in self._fragments if f.fragment_id not in remove_ids]
        self.mark_dirty()
        return to_remove
