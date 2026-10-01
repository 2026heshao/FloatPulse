# -*- coding: utf-8 -*-
"""侧栏宽度分割手柄（_NavSplitHandle + MainWindow._on_split_*）回归。

钉死的行为：
  A 手柄存在且接线：位于侧栏与内容区之间，宽 6、SizeHor 光标、四个
    信号全部连到宿主槽
  B 宽度恢复：config.side_bar_width 合法值启动生效；越界/非法回退夹取
  C 拖动：press → moved 按位移实时套用（上限/下限都夹住），release 落盘
  D 双击：恢复默认宽度并落盘
  E 落盘省写：宽度未变化时重复 release 不写盘

与 test_nav_groups.py（纯逻辑）互补：本文件跑真实 MainWindow（offscreen），
防止「信号接错槽 / 配置键写错」这类静默断线。
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from PyQt6.QtCore import Qt                               # noqa: E402
from PyQt6.QtWidgets import QApplication                  # noqa: E402

from src.clipboard_monitor import ClipboardMonitor        # noqa: E402
from src.config import ConfigManager                      # noqa: E402
from src.docx_manager import DocxManager                  # noqa: E402
from src.fragment_manager import FragmentManager          # noqa: E402
from src.main_window import MainWindow                    # noqa: E402
from src.note_manager import NoteManager                  # noqa: E402
from src.task_manager import TaskManager                  # noqa: E402
from src.temp_asset_manager import TempAssetManager       # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _build_window(config: ConfigManager) -> MainWindow:
    """与 tools/verify_ai_assistant.py 同款的真实施工配方（临时目录数据）"""
    tmp = tempfile.mkdtemp(prefix="fp_split_test_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
    note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
    frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
    clip = ClipboardMonitor(frag_mgr, config)
    temp_mgr = TempAssetManager(tmp)
    return MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                      clip, temp_mgr)


@pytest.fixture(scope="module")
def win(qapp):
    """默认配置的 MainWindow：断言手柄存在性、拖动、双击恢复"""
    tmp = tempfile.mkdtemp(prefix="fp_split_test_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    w = _build_window(config)
    w.show()
    qapp.processEvents()          # 激活布局（offscreen 下 show 不立即排布）
    yield w
    w.close()
    w.deleteLater()


# ---------------- A 手柄存在与接线 ----------------
def test_handle_exists_between_side_and_content(win):
    h = win._nav_split
    assert h is not None and h.objectName() == "navSplitHandle"
    assert h.width() == 6
    assert h.cursor().shape() == Qt.CursorShape.SizeHorCursor
    # 几何上：手柄左缘应紧贴侧栏右缘（同一水平布局、spacing=0）
    assert h.x() == win._side_bar.x() + win._side_bar.width()


def test_handle_signals_wired(win):
    """四个信号都连到宿主槽（防止后续重构时静默断线）"""
    win._nav_split.split_pressed.emit(777)
    assert win._split_press_x == 777
    assert win._split_base_w == win._side_bar.width()
    base = win._split_base_w
    win._nav_split.split_moved.emit(777 + 30)
    assert win._side_bar.width() == win._apply_side_width(base + 30)
    win._nav_split.split_released.emit()      # 落盘（下面 C 段细验内容）


# ---------------- B 宽度恢复与夹取 ----------------
def test_clamp_bounds(win):
    assert win._apply_side_width(9999) == win.SIDE_BAR_MAX_W
    assert win._side_bar.width() == win.SIDE_BAR_MAX_W
    assert win._apply_side_width(5) == win.SIDE_BAR_MIN_W
    assert win._side_bar.width() == win.SIDE_BAR_MIN_W


def test_restore_from_config():
    """config.side_bar_width=220 → 启动即用 220（不是默认 168）

    非法值双层防护（2026-10-01 起三件套声明该键，校验前移到配置层）：
      1. 配置层 set() 拒绝越界/类型错误 → 内存保持上一个合法值；
      2. UI 层 _clamped_side_width() 兜底夹取（防旧版 json / 手改文件
         绕过 set 直塞脏值）。
    """
    tmp = tempfile.mkdtemp(prefix="fp_split_restore_")
    config = ConfigManager(os.path.join(tmp, "config.json"))
    assert config.set("side_bar_width", 220)
    config.save()
    w = _build_window(config)
    assert w._side_bar.width() == 220
    # 配置层：越界大数 / 非数字字符串 → set 拒绝，内存保持 220
    assert config.set("side_bar_width", 99999) is False
    assert w._clamped_side_width() == 220
    assert config.set("side_bar_width", "abc") is False
    assert w._clamped_side_width() == 220
    # UI 层兜底：脏值直塞内存（模拟旧版本落盘 / 手改 json）仍被夹取
    config._config["side_bar_width"] = 99999
    assert w._clamped_side_width() == MainWindow.SIDE_BAR_MAX_W
    config._config["side_bar_width"] = "abc"
    assert w._clamped_side_width() == MainWindow.SIDE_BAR_WIDTH
    w.close()
    w.deleteLater()


# ---------------- C 拖动 ----------------
def test_drag_applies_and_saves(win):
    win._on_split_pressed(1000)
    base = win._split_base_w
    win._on_split_moved(1000 + 40)
    assert win._side_bar.width() == win._apply_side_width(base + 40)
    win._on_split_released()
    assert win._config.get("side_bar_width") == win._side_bar.width()
    # 极限拖动：两个方向都夹在 [MIN, MAX]
    win._on_split_moved(1000 + 5000)
    assert win._side_bar.width() == win.SIDE_BAR_MAX_W
    win._on_split_moved(1000 - 5000)
    assert win._side_bar.width() == win.SIDE_BAR_MIN_W
    win._on_split_released()


# ---------------- D 双击恢复默认 ----------------
def test_double_click_resets_default(win):
    win._apply_side_width(240)
    win._on_split_reset()
    assert win._side_bar.width() == win.SIDE_BAR_WIDTH
    assert win._config.get("side_bar_width") == win.SIDE_BAR_WIDTH


# ---------------- E 落盘省写 ----------------
def test_save_skips_when_unchanged(win, qapp):
    win._on_split_reset()                     # 先确保有一个已落盘的值
    path = win._config._json_path
    before = open(path, "rb").read()
    win._on_split_released()                  # 同宽度再 release → 不写盘
    assert open(path, "rb").read() == before
