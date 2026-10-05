# -*- coding: utf-8 -*-
"""启动期数据完整性入口壳（自 knowledge_ball.main 下沉，收尾任务 C1）。

背景：knowledge_ball.py 对 2860 行上限仅余 1 行，启动入口的
「数据完整性检查」段（扫描 → len>=2 弹窗 → 汇总日志）整体下沉到本模块，
knowledge_ball.main 只留一行调用。行为零变化：

  - 扫描段仍是 src/app_paths.check_data_integrity（纯扫描、无 UI）；
  - 弹窗文案与触发条件逐字保留（原生 QMessageBox.warning，len>=2 才弹）；
  - 汇总日志（warning / info）逐字保留。

位置说明：原生 QMessageBox 在 src/ 全域禁用（tests/test_no_native_messagebox
闸门只扫 v4/src/，唯一白名单 src/logger.py 崩溃兜底）。本模块与
knowledge_ball.py 同居仓库根（入口层），不在闸门扫描范围，因此继续使用
原生弹窗——启动极早期主窗/主题管理器尚未就绪，原生 QMessageBox 不依赖
任何 glass/主题链路，是行为等价的最保守选择（GlassMessageBox 需要主题
上下文，时序上不保证等价，故不走 src/wiring.py + glass 方案）。
"""
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from PyQt6.QtWidgets import QMessageBox          # noqa: E402

from src.app_paths import check_data_integrity   # noqa: E402


def run_startup_data_integrity_check(data_dir: str, logger) -> None:
    """启动数据完整性检查入口：扫描 → 弹窗（≥2 个损坏）→ 汇总日志。

    Args:
        data_dir: float_data 数据目录绝对路径。
        logger: 启动期日志器（app.log 的 logger）。

    文案与 knowledge_ball 原入口段逐字一致（C1 下沉，行为零变化）。
    """
    corrupted = check_data_integrity(data_dir, logger)
    if corrupted:
        # 损坏文件较多时弹窗提示用户
        if len(corrupted) >= 2:
            details = "\n".join(f"  • {f}: {r}" for f, r in corrupted)
            QMessageBox.warning(
                None, "数据完整性检查",
                f"检测到 {len(corrupted)} 个数据文件损坏，已自动重置为空数据：\n\n"
                f"{details}\n\n"
                f"详情请查看日志：float_data/app.log"
            )
        logger.warning(f"启动检查完成：{len(corrupted)} 个文件损坏已重置")
    else:
        logger.info("启动检查完成：所有 JSON 文件完整")
