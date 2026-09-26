# -*- coding: utf-8 -*-
"""
====================================================================
随时笔记管理模块  -  NoteManager
====================================================================
独立封装随时笔记的全部业务逻辑，与 UI 层解耦。
与 TaskManager（schedule.json）、docx 知识库完全隔离，互不干扰。

设计要点：
  1. 使用与 exe / py 同目录下的 notes.json 持久化笔记数据
     （单独文件，不与 schedule.json 混用）
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化空笔记列表；json 解析异常不崩溃
  4. 每条笔记拥有自增且不复用的唯一 note_id 主键
  5. 笔记字段：note_id / content / create_time / update_time
  6. 所有增删改先操作内存列表，完毕统一调用 _save() 写盘
  7. 删除严格按 note_id 过滤，禁止文本内容匹配删除
  8. UI 层只能通过本类公开方法操作，禁止直接读写 notes.json

模块导出：
  - Note        : 笔记数据类
  - NoteManager : 笔记管理器
====================================================================
"""

import os
import json
from datetime import datetime

from src.json_store import load_records
from src.constants import (
    safe_int, backup_corrupt_file,
    NOTE_TITLE_MAX_CHARS,
)


# ====================================================================
# 笔记数据类
# ====================================================================
class Note:
    """单条笔记的数据载体"""

    # 临时笔记固定标题（小卡片专用，主窗口可改名）
    TEMP_NOTE_TITLE = "📌 临时笔记"

    def __init__(self, note_id, content, create_time, update_time, title="",
                 title_auto=True):
        self.note_id = note_id              # 唯一主键，自增不复用
        self.content = content              # 笔记内容
        self.create_time = create_time      # 创建时间 "YYYY-MM-DD HH:MM"
        self.update_time = update_time      # 最后修改时间 "YYYY-MM-DD HH:MM"
        self.title = title                  # 笔记标题（旧数据为空时由 manager 兜底）
        self.title_auto = title_auto        # 标题是否由内容自动生成（True 时随内容刷新）

    def to_dict(self):
        """序列化为字典（用于写 json）"""
        return {
            "note_id": self.note_id,
            "title": self.title,
            "title_auto": self.title_auto,
            "content": self.content,
            "create_time": self.create_time,
            "update_time": self.update_time,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化（用于读 json），带类型校验防止损坏数据崩溃"""
        return cls(
            note_id=int(d.get("note_id", 0) or 0),
            content=str(d.get("content", "")),
            update_time=str(d.get("update_time", "")),
            title=str(d.get("title", "")),
            create_time=str(d.get("create_time", "")),
            title_auto=bool(d.get("title_auto", True)),
        )


# ====================================================================
# 笔记管理器
# ====================================================================
class NoteManager:
    """
    随时笔记管理器。

    对外提供增删改查接口，内部维护内存笔记列表，
    修改完毕统一调用 _save() 写入磁盘 json 文件。
    UI 层禁止直接读写 notes.json 文件。
    """

    def __init__(self, json_path: str):
        self._json_path = json_path         # notes.json 完整路径
        self._notes = []                    # 内存笔记列表
        self._next_id = 1                   # 下一个自增 note_id（不复用）
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """从磁盘加载笔记数据（骨架见 json_store.load_records）。

        - 文件不存在 → 初始化空列表
        - json 解析异常 → 备份原文件后初始化空列表，不崩溃
        - 后处理：兼容旧数据，title 为空时用内容前 N 字兜底
        """
        self._notes, self._next_id = load_records(
            self._json_path, "notes", Note.from_dict,
            min_next_id=lambda ns: max((n.note_id for n in ns), default=0) + 1,
        )
        # 兼容旧数据：title 为空时用内容前 N 字兜底
        for n in self._notes:
            if not n.title:
                n.title = self._auto_title(n.content)
                n.title_auto = True
            elif n.title == Note.TEMP_NOTE_TITLE:
                # 临时笔记标题固定，绝不参与自动更新（否则 get_temp_note
                # 会按标题找不到它，每次调用都新建一条）
                n.title_auto = False

    def _save(self):
        """
        统一保存：将内存笔记列表一次性写入磁盘 json。
        所有增删改操作完成后调用本方法。
        """
        data = {
            "notes": [n.to_dict() for n in self._notes],
            "next_id": self._next_id,
        }
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            # 原子写入：先写临时文件，再替换原文件，避免写一半损坏
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            # 写盘失败不崩溃（可在此处加日志）
            pass

    # ---------------- 增删改查 ----------------
    @staticmethod
    def _auto_title(content: str, limit: int = NOTE_TITLE_MAX_CHARS) -> str:
        """从内容生成默认标题：取前 limit 个非空字符（含中文计 1 字）"""
        text = (content or "").replace("\n", " ").replace("\r", " ").strip()
        return text[:limit] + ("..." if len(text) > limit else "")

    def add_note(self, content: str, title: str = "") -> int:
        """
        新建笔记（空白内容也允许保存）。
        title 为空时自动用内容前 N 字作为标题，并标记为自动标题。
        返回新笔记的 note_id。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = Note(
            note_id=self._next_id,
            content=content,
            create_time=now,
            update_time=now,
            title=title if title else self._auto_title(content),
            title_auto=not title,
        )
        self._notes.append(note)
        self._next_id += 1
        self._save()
        return note.note_id

    def update_note(self, note_id: int, content: str, title: str = None) -> bool:
        """
        编辑笔记内容（严格按 note_id 查找）。
        更新 update_time 为当前时间。

        标题处理规则：
          - title=None  ：不改标题；但若该笔记是自动标题，则跟随内容刷新
          - title=""    ：恢复为自动标题（内容前 N 字）
          - title=非空  ：手动命名，标记为自动标题=False（此后不再被内容覆盖）
        """
        for n in self._notes:
            if n.note_id == note_id:
                n.content = content
                if title is None:
                    if n.title_auto:
                        self._refresh_title(n)
                elif title:
                    n.title = title
                    n.title_auto = False
                else:
                    n.title = self._auto_title(content)
                    n.title_auto = True
                n.update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._save()
                return True
        return False

    def _refresh_title(self, note: Note):
        """自动标题笔记：按当前内容重算标题。临时笔记标题固定，不参与刷新"""
        if note.title == Note.TEMP_NOTE_TITLE:
            note.title_auto = False
            return
        note.title = self._auto_title(note.content)

    def set_title_auto(self, note_id: int, auto: bool) -> bool:
        """
        切换单条笔记「标题是否随内容更新」。
        切回自动（auto=True）时立即按当前内容重算标题。
        临时笔记固定标题，不参与自动更新 → 返回 False。
        """
        for n in self._notes:
            if n.note_id == note_id:
                if n.title == Note.TEMP_NOTE_TITLE:
                    return False
                n.title_auto = bool(auto)
                if auto:
                    self._refresh_title(n)
                n.update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._save()
                return True
        return False

    def update_title(self, note_id: int, title: str) -> bool:
        """仅修改笔记标题（严格按 note_id 查找）。视为手动命名，冻结不再被内容覆盖"""
        for n in self._notes:
            if n.note_id == note_id:
                n.title = title
                n.title_auto = False
                n.update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._save()
                return True
        return False

    def get_temp_note(self):
        """
        获取小卡片专用的临时笔记（固定标题「📌 临时笔记」）。
        若不存在则创建一条空内容的临时笔记，返回该 Note。
        若有多条（旧数据残留），保留最新一条，其余改名避免冲突。
        """
        TEMP_TITLE = Note.TEMP_NOTE_TITLE
        matched = [n for n in self._notes if n.title == TEMP_TITLE]
        if matched:
            # 取最近修改的一条
            matched.sort(key=lambda n: n.update_time, reverse=True)
            return matched[0]
        # 不存在 → 创建一条空内容的临时笔记
        note_id = self.add_note("", title=TEMP_TITLE)
        return self.get_note(note_id)

    def delete_note(self, note_id: int) -> bool:
        """
        删除笔记。
        严格按 note_id 过滤，禁止使用文本内容匹配删除。
        临时笔记（标题为「📌 临时笔记」）不可删除。
        """
        # 查找目标笔记，若为临时笔记则拒绝删除
        for n in self._notes:
            if n.note_id == note_id:
                if n.title == Note.TEMP_NOTE_TITLE:
                    return False
                break

        before = len(self._notes)
        self._notes = [n for n in self._notes if n.note_id != note_id]
        if len(self._notes) < before:
            self._save()
            return True
        return False

    def get_all_notes(self):
        """返回全部笔记，按最后修改时间倒序（最近编辑的在前）"""
        return sorted(self._notes, key=lambda n: n.update_time, reverse=True)

    def get_note(self, note_id: int):
        """按 note_id 获取单条笔记，不存在返回 None"""
        for n in self._notes:
            if n.note_id == note_id:
                return n
        return None
