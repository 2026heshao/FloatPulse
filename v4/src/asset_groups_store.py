# -*- coding: utf-8 -*-
"""素材会话堆「旁路标注」的存取（**禁 import PyQt6**）。

为什么存在
==========
临时素材的会话分组（``asset_group.py``）是**渲染时算出来的纯派生**——
``temp_assets.json`` 一个字节都不许改。用户要给某个堆**命名**、把某张
素材**从堆里移出**、把某张素材**并入指定堆**（2026-10-05），这些标注
必须另找落点：本模块管理的 ``float_data/asset_groups.json`` 就是那个
旁路文件（细节强化项检索 2026-10-04 P0-2，题面许可「命名/成员调整走
旁路文件」）。

红线对齐
========
- **不新增备份动作**：落盘走 ``json_store.save_records`` 既有通道
  （写前 rotate_backup 与全部数据文件同源），UI 不暴露任何备份入口。
- **不是数据沉淀层**：只存「用户对渲染结果的标注」——删掉本文件，
  分组立刻回到纯时间聚类，零数据损失。
- **temp_assets.json 零接触**：素材本体与元数据的读写仍全部在
  TempAssetManager，本模块对它一无所知。

文件结构（v1）::

    {"data_version": 1,
     "asset_groups": {
         "names":    {"<堆首 asset_id>": "自定义堆名", ...},
         "detached": [<asset_id>, ...],     # 被移出、永远单独渲染的素材
         "merged":   {"<asset_id>": "<目标堆首 asset_id>", ...}
     }}                                     # 被强制并入指定堆（2026-10-05）

``merged`` 是 2026-10-05「并入任意堆」新增的**旁路键**（v1 结构的加法
扩展：旧版本读到这里会静默忽略，不破坏旧读者）。值为素材自有时表示
「新建堆」——素材自锚成一堆，后续还能往里并。堆身份 = **堆首
asset_id**：时间聚类的锚点在每个堆的首成员上，新增素材只会并入堆尾
或新开一堆，堆首不变（被并入的素材接在堆尾，不抢堆首）；堆首素材被
删除时标注成为孤儿，由 :func:`prune` 在面板刷新时惰性清理（不主动
全量重写）。
"""

import json
import os

from src.constants import safe_int, backup_corrupt_file
from src.json_store import save_records
from src.logger import get_logger

# 文件名与顶层记录键（save_records 按 STORE_VERSIONS["asset_groups"] 补版本号）
FILE_NAME = "asset_groups.json"
STORE_KEY = "asset_groups"

NAMES_KEY = "names"          # {str(head_asset_id): 自定义堆名}
DETACHED_KEY = "detached"    # [asset_id]（移出堆，永远单独渲染）
MERGED_KEY = "merged"        # {str(asset_id): str(目标堆首 id)}（并入任意堆）

# 堆名上限：标题条宽约一张缩略图，超长名字反而截成一团（渲染层还会再省略）
NAME_MAX = 40


def empty_store() -> dict:
    """一份合法的空标注结构（三键恒在，调用方无需判键）。"""
    return {NAMES_KEY: {}, DETACHED_KEY: [], MERGED_KEY: {}}


def sanitize(raw) -> dict:
    """任意脏输入 → 合法结构（纯函数；绝不抛异常）。

    - ``names``：键必须是纯数字字符串（堆首 id），值截到 NAME_MAX、
      空白名丢弃——空名等价「恢复默认」，存它没有意义；
    - ``detached``：只留正整数，去重保序；
    - ``merged``（并入任意堆）：键值都收敛为正整数 id（自并为合法值
      =「新建堆」锚点），键归一化成 ``str(int)``，脏键值静默丢弃。
    """
    out = empty_store()
    if not isinstance(raw, dict):
        return out
    names = raw.get(NAMES_KEY)
    if isinstance(names, dict):
        for k, v in names.items():
            key = str(k).strip()
            name = v.strip() if isinstance(v, str) else ""
            if key.isdigit() and name:
                out[NAMES_KEY][key] = name[:NAME_MAX]
    detached = raw.get(DETACHED_KEY)
    if isinstance(detached, list):
        for v in detached:
            iv = safe_int(v, 0)
            if iv > 0 and iv not in out[DETACHED_KEY]:
                out[DETACHED_KEY].append(iv)
    merged = raw.get(MERGED_KEY)
    if isinstance(merged, dict):
        for k, v in merged.items():
            aid = safe_int(k, 0)
            head = safe_int(v, 0)
            if aid > 0 and head > 0:
                out[MERGED_KEY][str(aid)] = str(head)
    return out


def load(json_path: str) -> dict:
    """读取标注文件；缺失 → 空结构，损坏 → 备份后空结构（绝不抛异常）。"""
    if not json_path or not os.path.exists(json_path):
        return empty_store()
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        backup_corrupt_file(json_path)
        get_logger().warning("素材堆标注文件损坏已备份：%s（%s: %s）",
                             json_path, type(exc).__name__, exc)
        return empty_store()
    raw = data.get(STORE_KEY) if isinstance(data, dict) else None
    return sanitize(raw)


def save(json_path: str, store: dict) -> bool:
    """原子落盘（经 save_records：写前滚动备份 + 自动补 data_version）。

    传入的 store 先过一遍 :func:`sanitize`——写出去的永远是合法结构。
    失败返回 False 不抛异常（标注写不进去只影响「记住」，不阻断界面）。
    """
    if not json_path:
        return False
    return bool(save_records(json_path, {STORE_KEY: sanitize(store)},
                             store=STORE_KEY))


def prune(store: dict, valid_ids) -> dict:
    """清掉指向已删除素材的标注（纯函数）；返回清理后的结构。

    ``valid_ids`` 是当前素材池的 asset_id 集合。孤儿标注无害（应用不到
    任何堆上），但会让文件越积越脏，顺手在面板刷新时清一次。
    """
    valid = set()
    for v in (valid_ids or ()):
        iv = safe_int(v, 0)
        if iv > 0:
            valid.add(iv)
    names = {k: v for k, v in (store.get(NAMES_KEY) or {}).items()
             if safe_int(k, 0) in valid}
    detached = [i for i in (store.get(DETACHED_KEY) or []) if i in valid]
    # 并入标注：素材或目标堆首任一被删 → 整条成孤儿（应用不到任何堆，
    # 渲染层也会退化成独立单元），一并清掉。
    merged = {}
    for k, v in (store.get(MERGED_KEY) or {}).items():
        aid, head = safe_int(k, 0), safe_int(v, 0)
        if aid in valid and head in valid:
            merged[str(aid)] = str(head)
    return {NAMES_KEY: names, DETACHED_KEY: detached, MERGED_KEY: merged}
