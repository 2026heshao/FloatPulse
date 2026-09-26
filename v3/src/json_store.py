# -*- coding: utf-8 -*-
"""
====================================================================
JSON 记录文件读写骨架  -  json_store
====================================================================
5 个数据管理器（碎片 / 笔记 / 日程任务 / 网址导航 / 临时素材）此前
各自实现了一份**逐字重复**的 `_load()`：文件缺失容错、顶层结构校验、
反序列化、next_id 类型收敛与下界修正、损坏文件备份。本模块把这套骨架
抽成纯函数，各管理器只保留自己特有的后处理（如笔记的标题兜底、
素材的失效记录清理、导航的嵌套站点 id 修正）。

设计要点：
  1. 纯函数、无 Qt 依赖，可独立单测
  2. 行为与原有 5 份实现逐条等价，由 `tests/test_json_store_guard.py` 护栏守护
  3. 损坏文件先备份再清空，绝不静默丢数据
  4. next_id 一律 safe_int 收敛 + max(…, 1) 兜底，避免字符串 id 触发 TypeError
====================================================================
"""

import json
import os

from src.constants import safe_int, backup_corrupt_file
from src.logger import get_logger


def load_records(json_path: str, records_key: str, factory,
                 min_next_id=None) -> tuple:
    """
    读取「单文件 JSON 记录数组」，返回 (records, next_id)。

    - 文件不存在 → ([], 1)
    - 顶层不是 dict / JSON 解析失败 → 备份原文件后返回 ([], 1)
    - 数组里非 dict 的元素会被丢弃（损坏数据不致崩溃）
    - next_id：`safe_int` 收敛 → 抬到 `max(next_id, min_next_id(records), 1)`

    min_next_id: 可选回调 `(records) -> int`，用于把 next_id 抬到不与现有
                 记录冲突的值（通常 `max(记录 id) + 1`；导航需遍历嵌套站点，
                 由其自行计算）。
    """
    if not os.path.exists(json_path):
        return [], 1

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid json structure: expected dict")
        raw = data.get(records_key, [])
        records = [factory(d) for d in raw if isinstance(d, dict)]
        next_id = safe_int(data.get("next_id", 1), 1)
        if min_next_id is not None:
            next_id = max(next_id, int(min_next_id(records)))
        next_id = max(next_id, 1)
        return records, next_id
    except Exception as exc:
        # 损坏文件先备份再返回空数据，避免后续写盘覆盖后无法恢复
        backup_corrupt_file(json_path)
        # B4：数据丢失不能无迹可寻 —— 记录是哪个文件、为何损坏
        get_logger().warning("数据文件损坏已备份：%s（%s: %s）",
                             json_path, type(exc).__name__, exc)
        return [], 1
