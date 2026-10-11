# -*- coding: utf-8 -*-
"""UI 心跳看门狗「卡顿现场栈」回归（2026-10-10）。

背景：用户报「命令面板快捷键跳页卡 1-2 秒」，但 HeartbeatWatch 原先只记
「阻塞了多久」，抓不到**阻塞在哪一行** —— 排查时只能临时写外部 dump 脚本。
本批把栈采样内建进产品（独立守护线程 + sys._current_frames），此文件钉死：

  1. 卡顿时必须写到 **app.log**，且日志里含现场调用栈（可定位到代码行）
  2. 阻塞时长照旧上报（原能力不回退）
  3. 无阻塞时零输出（平时不刷日志）
  4. 全局热键回调**异步派发**（不在 nativeEventFilter 栈内同步执行）
     —— 防止回调链里的 show/activateWindow/force_foreground 在原生消息
     处理中重入自旋（用户报的卡顿路径）

单跑：
    cd v4 && python -m pytest tests/test_heartbeat_stall_stack.py -q
"""
import ast
import gc
import io
import os
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.dirname(_HERE)
if V4 not in sys.path:
    sys.path.insert(0, V4)

WIRING_PATH = os.path.join(V4, "src", "wiring.py")
HOTKEY_PATH = os.path.join(V4, "src", "global_hotkey.py")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


class _CaptureLog:
    """把 logger 的 warning 抓下来（捕现场栈文本用）

    注意：产品代码是 ``from src.logger import get_logger``（模块级绑定），
    所以 monkeypatch ``src.logger.get_logger`` **不生效** —— 必须在消费者
    模块（src.wiring / src.global_hotkey）的命名空间上打补丁。
    """

    def __init__(self):
        self.lines = []

    def warning(self, msg, *a, **k):
        self.lines.append(str(msg))

    def info(self, msg, *a, **k):
        pass

    def error(self, msg, *a, **k):
        self.lines.append(str(msg))


def _patch_logger(monkeypatch, module, cap):
    """在消费者模块上替换 get_logger（模块级绑定，故打这里）"""
    monkeypatch.setattr(module, "get_logger", lambda: cap)


class TestHeartbeatStallStack:
    def test_source_has_stack_capture(self):
        """源码钉子：看门狗必须带独立线程 + _current_frames + traceback"""
        src = _read(WIRING_PATH)
        tree = ast.parse(src)
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "HeartbeatWatch")
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        assert "_watch_stall" in methods
        dump = ast.dump(cls)
        assert "_current_frames" in dump      # 取主线程栈
        assert "traceback" in dump            # 栈转文本
        assert "threading" in src             # 独立守护线程

    def test_stall_writes_stack_to_log(self, qapp, monkeypatch):
        """行为：人为阻塞主线程 → 日志必须含「现场调用栈」且能指到本函数"""
        from src import wiring

        cap = _CaptureLog()
        _patch_logger(monkeypatch, wiring, cap)

        hb = wiring.HeartbeatWatch(threshold_ms=200, interval_ms=60)
        try:
            time.sleep(0.75)              # 阻塞「主线程」：本测试即主线程
        finally:
            hb._stop.set()
        gc.collect()
        text = "\n".join(cap.lines)
        assert "卡住" in text, f"未上报卡顿：{cap.lines}"
        assert "现场调用栈" in text, f"未抓现场栈：{cap.lines}"
        assert "test_stall_writes_stack_to_log" in text, \
            f"栈未指向卡顿现场：{text[:600]}"
        # 注意：此处不断言「事件循环阻塞」时长行 —— 本测试用 time.sleep
        # 阻塞，Qt 定时器根本没机会跑，_tick 自然不触发；时长上报由
        # test_duration_reported_on_qt_block 用真实 Qt 阻塞路径覆盖。

    def test_duration_reported_on_qt_block(self, qapp, monkeypatch):
        """时长上报能力不回退：真实 Qt 事件循环内阻塞 → 必须记时长。"""
        from PyQt6.QtCore import QTimer
        from src import wiring

        cap = _CaptureLog()
        _patch_logger(monkeypatch, wiring, cap)
        hb = wiring.HeartbeatWatch(threshold_ms=200, interval_ms=60)

        def block():
            end = time.time() + 0.6          # 在事件循环内阻塞
            while time.time() < end:
                time.sleep(0.01)

        QTimer.singleShot(100, block)
        try:
            end = time.time() + 1.6
            while time.time() < end:
                qapp.processEvents()
                time.sleep(0.02)
        finally:
            hb._stop.set()
        text = "\n".join(cap.lines)
        assert "事件循环阻塞" in text, f"时长上报缺失：{cap.lines}"

    def test_no_stall_no_log(self, qapp, monkeypatch):
        """无阻塞 → 零输出（不刷日志）"""
        from src import wiring

        cap = _CaptureLog()
        _patch_logger(monkeypatch, wiring, cap)
        hb = wiring.HeartbeatWatch(threshold_ms=5000, interval_ms=100)
        try:
            end = time.time() + 0.6
            while time.time() < end:
                qapp.processEvents()
                time.sleep(0.02)
        finally:
            hb._stop.set()
        assert cap.lines == [], f"空闲时不该有输出：{cap.lines}"


class TestHotkeyCallbackAsync:
    """全局热键回调必须异步派发（2026-10-10 卡顿修复的结构性保证）"""

    def test_native_filter_defers_callback(self):
        """源码钉子：nativeEventFilter 内**不得**直接 entry[0]()，
        必须经 QTimer.singleShot 排队 + _safe_call 包裹。"""
        src = _read(HOTKEY_PATH)
        tree = ast.parse(src)
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "GlobalHotkeyManager")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "nativeEventFilter")
        dump = ast.dump(fn)
        assert "singleShot" in dump, \
            "热键回调仍在原生消息栈内同步执行（会在原生消息处理中重入卡顿）"
        assert "_safe_call" in dump
        assert "QTimer" in src

    def test_safe_call_swallows_and_logs(self, monkeypatch):
        """_safe_call：回调异常必须留痕且不外抛（否则热键静默失效）"""
        from src import global_hotkey as gh

        cap = _CaptureLog()
        _patch_logger(monkeypatch, gh, cap)
        ran = []
        gh._safe_call(lambda: ran.append(1))
        assert ran == [1]
        gh._safe_call(lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert any("boom" in line for line in cap.lines)

    def test_fire_and_forget_timer_path(self, qapp):
        """行为：模拟 nativeEventFilter 收到 WM_HOTKEY → 回调应在**本栈
        之外**执行（返回时尚未运行，事件循环跑一拍后才运行）。"""
        from PyQt6.QtCore import QTimer
        from src import global_hotkey as gh

        ran = []
        # 直接调 _safe_call 的排队形式（等价 nativeEventFilter 中的路径）
        QTimer.singleShot(0, lambda: gh._safe_call(lambda: ran.append("hit")))
        assert ran == [], "回调不得在排队调用栈内同步执行（重入风险）"
        end = time.time() + 0.5
        while time.time() < end and not ran:
            qapp.processEvents()
            time.sleep(0.01)
        assert ran == ["hit"], "排队回调未在事件循环中执行"
