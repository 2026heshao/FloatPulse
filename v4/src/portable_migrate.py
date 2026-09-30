# -*- coding: utf-8 -*-
r"""
====================================================================
旧版数据迁移  -  portable_migrate
====================================================================
成熟化 2.2（数据目录与安装解耦 · 双轨制）的迁移辅助模块。

背景：双轨制之前安装版把 float_data/ 写在安装目录里，升级 / 重装换目录
数据不跟随。双轨制之后安装版数据固定走 %APPDATA%\FloatPulse\float_data\，
首次启动若检测到「旧位置（exe 同目录 float_data/）有用户数据、新位置为
空」，弹一次询问把旧数据整体搬到新位置。

安全铁律（全模块通用，存量数据安全第一）：
  1. 源数据绝不删除——迁移成功后旧目录只是**改名** float_data.migrated-<时间戳>
     留作后悔药；失败时更是连碰都不碰；
  2. 目标目录只写不抢——新位置已有任何数据就拒绝迁移，绝不覆盖；
  3. 半途失败即清理——目标目录删干净回到「未迁移」状态，下次启动可重试。

调用时机：必须在此之前没有任何数据文件被创建（ConfigManager 创建之前）。
因此除 PyQt6 弹窗（惰性导入）外只用标准库，也不调用会建目录的
app_paths.get_data_dir() 系函数，检测阶段零副作用。
"""

import os
import shutil
import sys
import time

from src.app_paths import (DATA_DIR_NAME, DOCX_FILENAME, get_base_dir,
                           get_data_root, is_portable_mode)


# 旧 float_data/ 顶层的「用户数据」标记文件：命中任意一个才算有历史数据
# （只有 app.log 的空壳目录视为没数据，不打扰用户；名单与 knowledge_ball
# 启动时拼装的数据文件一致，另含 stickies.json / temp_assets.json）
_LEGACY_DATA_FILES = (
    "config.json", "schedule.json", "notes.json", "fragments.json",
    "docx_meta.json", "nav.json", "temp_assets.json", "stickies.json",
    DOCX_FILENAME,
)


def _count_files(root: str) -> int:
    """递归统计目录内文件总数（目录不存在返回 0）"""
    n = 0
    for _dirpath, _dirnames, filenames in os.walk(root):
        n += len(filenames)
    return n


def detect_legacy_data(install_mode: bool, *, exe_dir: str = "",
                       data_root: str = "") -> dict | None:
    """检测是否存在「需要迁移的旧版数据」（零副作用，不建任何目录）。

    install_mode : 是否安装版（frozen 且无 portable.marker）。便携 / 源码
                   模式一律不迁移，返回 None。
    exe_dir      : 注入 exe 所在目录（测试用）；缺省用 get_base_dir()。
    data_root    : 注入数据根目录（测试用，即 float_data/ 的父目录，安装版
                   为 %APPDATA%\\FloatPulse）；缺省用 get_data_root()。

    命中条件（须全部满足）：
      - exe 同目录的旧 float_data/ 存在，且顶层含 config.json / 任意数据
        json / 知识库.docx 之一；
      - 新数据目录（<数据根>/float_data/）不存在或为空——已迁移过
        （旧目录已改名）或新位置已在用（非空）都不命中。

    命中返回摘要 {"src": 旧目录, "dst": 新目录, "files": 旧目录内文件总数}，
    否则 None。
    """
    if not install_mode:
        return None
    src_root = exe_dir or get_base_dir()
    src = os.path.join(src_root, DATA_DIR_NAME)
    if not os.path.isdir(src):
        return None  # 没有旧目录（含已迁移改名后的情形）
    if not any(os.path.isfile(os.path.join(src, name))
               for name in _LEGACY_DATA_FILES):
        return None  # 有目录没数据（纯 app.log 空壳），不值得打扰用户
    dst_root = data_root or get_data_root()
    dst = os.path.join(dst_root, DATA_DIR_NAME)
    if os.path.isdir(dst) and os.listdir(dst):
        return None  # 新位置已有数据（已迁移 / 已在用），绝不覆盖
    return {"src": src, "dst": dst, "files": _count_files(src)}


def migrate_legacy_data(summary: dict) -> dict:
    """执行迁移：整树复制 → 数量校验 → 旧目录改名留备份。

    summary 为 detect_legacy_data 的返回摘要（src / dst / files）。
    config.json、各数据 json、知识库.docx、temp_assets/、plugins/、
    backups/ 等整棵树全部复制，不挑拣。

    成功返回 {"ok": True, "src":…, "dst":…, "backup":…, "files": N}；
    任何一步失败返回 {"ok": False, "error": …}，此时目标目录已清理回
    「未迁移」状态，源数据保持原样（绝不删除 / 改名）。
    """
    src = (summary or {}).get("src", "")
    dst = (summary or {}).get("dst", "")
    if not src or not dst or not os.path.isdir(src):
        return {"ok": False, "error": "迁移摘要无效或旧数据目录不存在"}
    if os.path.isdir(dst) and os.listdir(dst):
        return {"ok": False, "error": f"目标目录已有数据，拒绝覆盖：{dst}"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)  # dst 为空目录时也兼容
        n_src, n_dst = _count_files(src), _count_files(dst)
        if n_src != n_dst:
            raise OSError(f"复制后数量校验不符（源 {n_src} / 目标 {n_dst} 个文件）")
        backup = _backup_dirname(src)
        os.rename(src, backup)  # 只改名不删除，留后悔药
        return {"ok": True, "src": src, "dst": dst,
                "backup": backup, "files": n_dst}
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(dst, ignore_errors=True)  # 半成品目标清理，源数据绝不动
        return {"ok": False, "error": str(exc)}


def _backup_dirname(src: str) -> str:
    """生成不冲突的备份目录名：<旧目录>.migrated-<时间戳>（撞名加序号）"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{src}.migrated-{stamp}"
    k = 0
    while os.path.exists(backup):
        k += 1
        backup = f"{src}.migrated-{stamp}-{k}"
    return backup


def maybe_prompt_migrate(parent=None, *, install_mode: bool = None,
                         exe_dir: str = "", data_root: str = "") -> dict | None:
    """安装版首启检测旧数据 → 弹窗询问 → 同意则迁移。

    接线约定：在 ConfigManager 创建之前调用（本函数不依赖也不触发任何
    数据文件的创建），确保迁移发生在第一条数据落盘之前。

    parent       : QMessageBox 父控件（启动早期通常还没有窗口，传 None）。
    install_mode : 缺省按运行环境自动判定（frozen 且无 portable.marker）；
                   测试可显式注入 True/False。
    exe_dir / data_root : 注入路径（测试用），语义同 detect_legacy_data。

    返回：用户同意并执行后返回 migrate_legacy_data 的结果摘要（含 ok 标志，
          失败时已弹 warning 说明且源数据原样）；未命中 / 用户拒绝 /
          非安装版返回 None。
    """
    if install_mode is None:
        frozen = bool(getattr(sys, 'frozen', False))
        exe = exe_dir or (_current_runtime_exe_dir() if frozen else "")
        install_mode = frozen and not is_portable_mode(frozen=True, exe_dir=exe)
    summary = detect_legacy_data(install_mode, exe_dir=exe_dir,
                                 data_root=data_root)
    if summary is None:
        return None
    # PyQt6 惰性导入：检测 / 迁移路径保持无 GUI 依赖，可在任意纯逻辑环境调用
    from PyQt6.QtWidgets import QMessageBox
    text = (
        "检测到旧版本的数据文件，是否迁移到新的数据目录？\n\n"
        f"旧位置：{summary['src']}\n"
        f"新位置：{summary['dst']}\n\n"
        f"共 {summary['files']} 个文件。原数据会保留备份"
        "（迁移后旧目录改名为 float_data.migrated-<时间戳>，不会删除）。"
    )
    ret = QMessageBox.question(
        parent, "数据迁移", text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if ret != QMessageBox.StandardButton.Yes:
        return None
    result = migrate_legacy_data(summary)
    if not result.get("ok"):
        QMessageBox.warning(
            parent, "数据迁移失败",
            "旧数据保持原样、未做任何改动，程序将继续正常启动：\n\n"
            f"{result.get('error', '')}"
        )
    return result


def _current_runtime_exe_dir() -> str:
    """运行时 exe 所在目录（仅 frozen 下会被用到；口径同 app_paths）"""
    return os.path.dirname(sys.executable)
