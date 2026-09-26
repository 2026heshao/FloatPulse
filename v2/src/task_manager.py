# -*- coding: utf-8 -*-
"""
====================================================================
日程任务管理模块  -  TaskManager
====================================================================
独立封装日程任务的全部业务逻辑，与 UI 层解耦。
与 NoteManager（notes.json）、docx 知识库完全隔离，互不干扰。

设计要点：
  1. 使用与 exe / py 同目录下的 schedule.json 持久化任务数据
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化空任务列表；json 解析异常不崩溃
  4. 每条任务拥有自增且不复用的唯一 task_id 主键
  5. 任务字段：task_id / title / note / deadline / done / created_at
  6. 所有增删改先操作内存列表，完毕统一调用 _save() 写盘
  7. 删除严格按 task_id 过滤，禁止标题匹配删除
  8. UI 层只能通过本类公开方法操作，禁止直接读写 schedule.json

模块导出：
  - Task         : 任务数据类
  - TaskManager  : 任务管理器
====================================================================
"""

import os
import json
from datetime import datetime

from src.constants import safe_int, backup_corrupt_file


# ====================================================================
# 任务数据类
# ====================================================================
class Task:
    """单条任务的数据载体"""

    def __init__(self, task_id, title, note, deadline, done, created_at):
        self.task_id = task_id            # 唯一主键，自增不复用
        self.title = title                # 任务标题
        self.note = note                  # 备注
        self.deadline = deadline          # 截止日期 "YYYY-MM-DD"
        self.done = done                  # 是否完成
        self.created_at = created_at      # 创建时间 "YYYY-MM-DD HH:MM"

    def to_dict(self):
        """序列化为字典（用于写 json）"""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "note": self.note,
            "deadline": self.deadline,
            "done": self.done,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d):
        """从字典反序列化（用于读 json），带类型校验防止损坏数据崩溃"""
        return cls(
            task_id=int(d.get("task_id", 0) or 0),
            title=str(d.get("title", "")),
            note=str(d.get("note", "")),
            deadline=str(d.get("deadline", "")),
            done=bool(d.get("done", False)),
            created_at=str(d.get("created_at", "")),
        )


# ====================================================================
# 任务管理器
# ====================================================================
class TaskManager:
    """
    日程任务管理器。

    对外提供增删改查接口，内部维护内存任务列表，
    修改完毕统一调用 _save() 写入磁盘 json 文件。
    UI 层禁止直接读写 schedule.json 文件。
    """

    def __init__(self, json_path: str):
        self._json_path = json_path          # schedule.json 完整路径
        self._tasks = []                     # 内存任务列表
        self._next_id = 1                    # 下一个自增 task_id（不复用）
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """
        从磁盘加载任务数据。
        - 文件不存在 → 初始化空列表
        - json 解析异常或结构损坏 → 初始化空列表，不崩溃
        """
        if not os.path.exists(self._json_path):
            self._tasks = []
            self._next_id = 1
            return

        try:
            with open(self._json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 校验顶层结构必须是字典
            if not isinstance(data, dict):
                raise ValueError("Invalid json structure: expected dict")
            tasks_data = data.get("tasks", [])
            # 过滤非字典元素，防止损坏数据崩溃
            self._tasks = [Task.from_dict(d) for d in tasks_data if isinstance(d, dict)]
            # next_id 用 safe_int 收敛类型：字符串等非法值不再触发 TypeError
            self._next_id = safe_int(data.get("next_id", 1), 1)
            # 修正 next_id：确保不与已存在 id 冲突（不复用已删除的 id）
            if self._tasks:
                max_id = max(t.task_id for t in self._tasks)
                self._next_id = max(self._next_id, max_id + 1)
            # 确保下限：防止 JSON 被手动编辑为非法值
            self._next_id = max(self._next_id, 1)
        except Exception:
            # json 解析异常或文件损坏 → 先备份原文件，再初始化空列表
            backup_corrupt_file(self._json_path)
            self._tasks = []
            self._next_id = 1

    def _save(self):
        """
        统一保存：将内存任务列表一次性写入磁盘 json。
        所有增删改操作完成后调用本方法。
        """
        data = {
            "tasks": [t.to_dict() for t in self._tasks],
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
    def add_task(self, title: str, note: str, deadline: str) -> int:
        """
        新建任务。
        返回新任务的 task_id。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        task = Task(
            task_id=self._next_id,
            title=title,
            note=note,
            deadline=deadline,
            done=False,
            created_at=now,
        )
        self._tasks.append(task)
        self._next_id += 1
        self._save()
        return task.task_id

    def update_task(self, task_id: int, title: str, note: str, deadline: str) -> bool:
        """
        编辑任务（严格按 task_id 查找）。
        """
        for t in self._tasks:
            if t.task_id == task_id:
                t.title = title
                t.note = note
                t.deadline = deadline
                self._save()
                return True
        return False

    def toggle_task(self, task_id: int) -> bool:
        """切换任务完成状态（严格按 task_id 查找）"""
        for t in self._tasks:
            if t.task_id == task_id:
                t.done = not t.done
                self._save()
                return True
        return False

    def delete_task(self, task_id: int) -> bool:
        """
        删除任务。
        严格按 task_id 过滤，禁止使用标题匹配删除。
        """
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.task_id != task_id]
        if len(self._tasks) < before:
            self._save()
            return True
        return False

    def get_all_tasks(self):
        """返回全部任务，按 task_id 升序（创建顺序）"""
        return sorted(self._tasks, key=lambda t: t.task_id)

    def get_task(self, task_id: int):
        """按 task_id 获取单条任务，不存在返回 None"""
        for t in self._tasks:
            if t.task_id == task_id:
                return t
        return None
