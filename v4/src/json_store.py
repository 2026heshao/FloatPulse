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
  5. schema 版本层：顶层 data_version + MIGRATIONS 注册表，旧数据打开时
     逐级迁移（成熟化路线图 1.2 后半）；save_records 统一「写前滚动备份 +
     带版本号落盘」（1.2 前半）
====================================================================
"""

import json
import os

from src.constants import safe_int, backup_corrupt_file
from src.data_backups import rotate_backup
from src.logger import get_logger


# ====================================================================
# schema 版本与迁移层（成熟化路线图 1.2）
# ====================================================================
# 数据 JSON 顶层版本字段名；config.json 用独立字段名 schema_version
STORE_VERSION_KEY = "data_version"
CONFIG_VERSION_KEY = "schema_version"


def _migrate_fragments_0_to_1(data: dict) -> dict:
    """示例迁移：fragments v0 → v1，为每条碎片补全 source 字段（缺省空串）。

    与 Fragment.from_dict 的字段兜底保持一致；幂等（已有值原样保留），
    纯函数（只改传入 data 并返回，不读盘不写盘）。
    """
    for frag in data.get("fragments", []):
        if isinstance(frag, dict):
            frag.setdefault("source", "")
    return data


# 各数据 store 的当前 schema 版本（键 = 各文件顶层记录数组键名，config 除外）。
# 结构变更时把对应项 +1，并在 MIGRATIONS 注册逐级迁移函数。
STORE_VERSIONS = {
    "config":    1,     # config.json（schema_version 字段）
    "fragments": 1,     # fragments.json
    "notes":     1,     # notes.json
    "tasks":     1,     # schedule.json
    "groups":    1,     # nav.json
    "stickies":  1,     # stickies.json
    "assets":    1,     # temp_assets.json
}

# 迁移注册表：{store: {from_version: 迁移函数}}。
# 迁移函数签名 fn(data) -> data，必须是幂等纯函数；某一级未注册 →
# 版本号照常推进、数据原样跳过（适用于新增字段且读取端已有兜底的场景）。
# 当前全部 store 为 v1，config 留空表占位，待首次结构变更时填充。
MIGRATIONS = {
    "config":    {},
    "fragments": {0: _migrate_fragments_0_to_1},
    "notes":     {},
    "tasks":     {},
    "groups":    {},
    "stickies":  {},
    "assets":    {},
}


def migrate_data(store: str, data: dict, from_version: int,
                 to_version: int = None) -> dict:
    """把 data 从 from_version 逐级迁移到 to_version（缺省为该 store 当前版本）。

    逐级查 MIGRATIONS[store][ver] 执行 ver → ver+1 的迁移；未注册的
    中间级原样跳过。返回迁移后的 data（可能被迁移函数就地修改）。
    """
    if to_version is None:
        to_version = STORE_VERSIONS.get(store, 1)
    table = MIGRATIONS.get(store, {})
    ver = int(from_version)
    while ver < to_version:
        fn = table.get(ver)
        if fn is not None:
            data = fn(data)
        ver += 1
    return data


def load_records(json_path: str, records_key: str, factory,
                 min_next_id=None) -> tuple:
    """
    读取「单文件 JSON 记录数组」，返回 (records, next_id)。

    - 文件不存在 → ([], 1)
    - 顶层不是 dict / JSON 解析失败 → 备份原文件后返回 ([], 1)
    - 顶层 data_version 缺失视为该 store 当前版本（存量文件零迁移）；
      显式旧版本号 → 逐级跑 MIGRATIONS 迁移后返回，落盘交给调用方
      的正常保存路径（下次写时经 save_records 带上版本号）
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

        # schema 版本迁移：只认显式版本号（缺失 = 存量文件 = 当前版本）；
        # 版本号非法（非数字）按当前版本处理，不迁移也不清空
        raw_version = data.get(STORE_VERSION_KEY)
        if raw_version is not None:
            current = STORE_VERSIONS.get(records_key, 1)
            from_version = safe_int(raw_version, current)
            if from_version < current:
                data = migrate_data(records_key, data, from_version, current)

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


def save_records(json_path: str, data: dict, store: str = None,
                 compact: bool = False) -> bool:
    """写入单个数据 JSON 文件：写前滚动备份 + 自动补版本号 + 原子写入。

    - store：STORE_VERSIONS 的键（如 "fragments"）；缺省按 data 里现存的
      记录键自动识别。识别到即补顶层 data_version = 当前版本，旧文件
      首次经此保存后即带上版本号
    - compact：True 时紧凑输出（separators 无空格），保持 fragments.json
      的既有文件格式；缺省 indent=2，与其余数据文件一致
    - 覆盖写之前调用 data_backups.rotate_backup 做当日一次滚动备份
      （失败不阻断保存）
    - 原子写入：先写 .tmp 再 os.replace，与各管理器既有写法一致
    - 返回是否写盘成功；OSError 等失败返回 False，不抛异常

    返回值：True = 写盘成功，False = 写盘失败（保持磁盘上的旧文件）。
    """
    try:
        if store is None:
            store = next((k for k in STORE_VERSIONS if k in data), "")
        payload = dict(data)
        if store:
            payload[STORE_VERSION_KEY] = STORE_VERSIONS.get(store, 1)
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        rotate_backup(json_path)
        tmp_path = json_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            if compact:
                json.dump(payload, f, ensure_ascii=False,
                          separators=(",", ":"))
            else:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, json_path)
        return True
    except OSError:
        return False
