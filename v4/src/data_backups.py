# -*- coding: utf-8 -*-
"""
====================================================================
数据文件写前滚动备份  -  data_backups
====================================================================
成熟化路线图 1.2 前半：每次覆盖写之前，把数据文件的**当日第一份**
快照轮转进数据目录下的 backups/ 子目录（即 float_data/backups/），
同一文件只保留最近 7 份。与「损坏才备份」的 .corrupt.bak 链路
（constants.backup_corrupt_file）互补：前者兜底写坏，本模块兜底误删/误改。

设计要点：
  1. 纯标准库，无 Qt / 第三方依赖，可独立单测
  2. 备份失败绝不抛异常 —— 备份是保险丝，不能阻断正常保存
  3. 同一文件同一天只备份一次（模块级登记表 abspath → 已备日期），
     当天第二次起 rotate 是 no-op，写盘热路径零开销
  4. 备份目录跟随数据文件所在目录：所有数据 JSON 与 config.json
     均位于 float_data/ 内（knowledge_ball 启动时统一拼装路径），
     故备份统一落 float_data/backups/，该目录随 float_data 一起被 .gitignore
====================================================================
"""

import os
import shutil
from datetime import date

from src.logger import get_logger

# 备份子目录名（位于数据目录 float_data/ 内）
BACKUPS_DIRNAME = "backups"
# 同一文件保留的最近备份份数（超出按文件名内日期从旧到新清理）
KEEP_COUNT = 7

# 当日已备份登记表：{文件 abspath: "YYYYMMDD"}。仅本进程内生效，
# 程序重启后当日首写会再备一份，与「每日首次写入前备份」语义一致。
_ROTATED_ON = {}


def _today() -> str:
    """当前日期（YYYYMMDD）。独立成函数便于测试注入跨日场景。"""
    return date.today().strftime("%Y%m%d")


def _backup_dir(json_path: str) -> str:
    """备份目录：数据文件所在目录下的 backups/ 子目录。"""
    return os.path.join(os.path.dirname(os.path.abspath(json_path)),
                        BACKUPS_DIRNAME)


def snapshot_status(data_dir: str) -> tuple:
    """扫描数据目录下 backups/ 内全部快照，返回 (最近mtime, 快照总数)。

    仅供设置页**被动展示**（细节强化 P2-8：自动快照此前完全静默，
    用户不知道有这份保险丝）：只读不写，绝不触碰快照文件本身。

    - mtime 取各快照 ``st_mtime`` 的最大值（rotate_backup 每天每文件
      一份、多文件并存，最大 mtime ≈ 最近一次自动快照的生成时刻）；
      无快照时为 0.0
    - 目录缺失 / 为空 / 扫描失败（权限等）一律 ``(0.0, 0)``——展示层
      据此显示「暂无」，与本模块「备份失败绝不抛异常」同口径
    """
    try:
        backup_dir = os.path.join(os.path.abspath(data_dir), BACKUPS_DIRNAME)
        latest = 0.0
        count = 0
        with os.scandir(backup_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue      # 子目录/非常规条目不计入
                count += 1
                mtime = entry.stat().st_mtime
                if mtime > latest:
                    latest = mtime
        return (latest, count)
    except OSError:
        return (0.0, 0)


def reset_rotation_state() -> None:
    """清空当日已备份登记表（仅供测试隔离使用，业务代码勿调）。"""
    _ROTATED_ON.clear()


def _cleanup_old_backups(backup_dir: str, stem: str, ext: str,
                         keep: int = KEEP_COUNT) -> None:
    """清理同文件名主干的历史备份，只保留最近 keep 份。

    备份名内嵌固定宽度日期（YYYYMMDD），按文件名排序即时间序；
    只精确匹配 ``<stem>-<日期><ext>`` 形态，不波及同目录其他文件。
    """
    prefix = stem + "-"
    names = sorted(
        n for n in os.listdir(backup_dir)
        if n.startswith(prefix) and n.endswith(ext)
        and len(n) > len(prefix) + len(ext)
    )
    for name in names[:-keep] if len(names) > keep else []:
        try:
            os.remove(os.path.join(backup_dir, name))
        except OSError:
            pass  # 单个旧备份清理失败不影响整体轮转


def rotate_backup(json_path: str, day: str = None) -> str:
    """覆盖写之前调用：把当前文件按日轮转备份一次，返回备份路径（未备份返回空串）。

    - 文件不存在（首次写盘）→ 跳过，无事可备
    - 同一文件同一天已备份 → no-op（性能：写盘热路径第二次起零开销）
    - 备份名：<文件名主干>-<YYYYMMDD>.<扩展名>，如 fragments-20260930.json
    - 轮转成功后清理同主干旧备份，只留最近 KEEP_COUNT 份
    - 任何失败只记 warning 不抛异常 —— 备份失败不能阻断保存；
      复制成功才登记当日已备，失败后下次保存仍会重试

    day: 覆盖"今天"（YYYYMMDD），供测试注入跨日场景；业务代码不传。
    """
    try:
        path = os.path.abspath(json_path)
        day = day or _today()
        if _ROTATED_ON.get(path) == day:
            return ""                # 当日已备，no-op
        if not os.path.isfile(path):
            return ""                # 首次写盘，尚无旧内容可备

        stem, ext = os.path.splitext(os.path.basename(path))
        backup_dir = _backup_dir(path)
        os.makedirs(backup_dir, exist_ok=True)
        dst = os.path.join(backup_dir, "%s-%s%s" % (stem, day, ext))
        shutil.copy2(path, dst)
        _ROTATED_ON[path] = day
        _cleanup_old_backups(backup_dir, stem, ext)
        return dst
    except Exception as exc:
        # 滚动备份是 best-effort：任何失败（权限/磁盘满/文件被占用）都不阻断保存
        get_logger().warning("滚动备份失败（不影响本次保存）：%s（%s: %s）",
                             json_path, type(exc).__name__, exc)
        return ""
