# -*- coding: utf-8 -*-
"""离屏功能验证：全局异常钩子收敛（优化调研清单 3.4）。

覆盖：
  A. 结构中只保留一份钩子实现：knowledge_ball 不再定义 _install_global_excepthook
  B. 弹窗正文不含完整堆栈（无 "Traceback (most recent call last)"）
  C. 弹窗正文不含源码文件/路径信息（不泄露环境）
  D. 弹窗含异常类型 + 消息摘要 + 日志文件路径
  E. 完整堆栈确实落到了日志文件（信息没丢，只是换了地方）
  F. 同一异常重复发生只弹一次（防弹窗风暴）；不同异常仍会弹
  G. KeyboardInterrupt 走 sys.exit(0)

运行方式（必须 offscreen）：
  python tools/run_gui_check.py tools/verify_excepthook.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyQt6.QtWidgets as QtWidgets  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.logger import init_logger, install_excepthook, get_log_file_path  # noqa: E402

PASS = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}")


class _FakeIcon:
    Critical = 1


class _FakeStdButton:
    Ok = 1


class _FakeMessageBox:
    """QMessageBox 替身：只记录被设置的文案，不真的弹窗"""

    instances = []

    Icon = _FakeIcon
    StandardButton = _FakeStdButton

    def __init__(self):
        self.title = ""
        self.text = ""
        self.info = ""
        self.executed = 0
        _FakeMessageBox.instances.append(self)

    def setIcon(self, *_a):
        pass

    def setWindowTitle(self, t):
        self.title = t

    def setText(self, t):
        self.text = t

    def setInformativeText(self, t):
        self.info = t

    def setStandardButtons(self, *_a):
        pass

    def exec(self):
        self.executed += 1
        return 0


def main():
    app = QApplication.instance() or QApplication([])

    # ---------------- A. 唯一实现 ----------------
    import knowledge_ball
    assert not hasattr(knowledge_ball, "_install_global_excepthook"), \
        "A. knowledge_ball 仍残留第二份异常钩子实现"
    ok("A. 全局异常钩子只剩 src/logger.py 一份实现（重复实现已删除）")

    tmp = tempfile.mkdtemp(prefix="fp_hook_")
    init_logger(tmp, level=10)          # DEBUG=10
    install_excepthook()
    log_path = get_log_file_path()
    assert log_path and os.path.exists(os.path.dirname(log_path)), \
        f"A. 日志路径异常：{log_path!r}"

    real_qmb = QtWidgets.QMessageBox
    QtWidgets.QMessageBox = _FakeMessageBox
    try:
        # ---------------- B/C/D. 首次异常 → 弹窗内容 ----------------
        try:
            raise ValueError("boom-测试消息")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        assert len(_FakeMessageBox.instances) == 1, \
            f"B. 首次异常应恰好弹 1 次，实际 {len(_FakeMessageBox.instances)} 次"
        box = _FakeMessageBox.instances[0]
        body = box.text + "\n" + box.info

        assert box.executed == 1, "B. 弹窗未真正 exec"
        assert "Traceback (most recent call last)" not in body, \
            "B. 弹窗里仍带完整堆栈"
        ok("B. 弹窗正文不含完整堆栈（Traceback 字样未出现）")

        assert "verify_excepthook" not in body, "C. 弹窗泄露了源码文件名"
        assert ".py" not in body, "C. 弹窗泄露了 .py 路径"
        # 注意：弹窗**应当**含日志文件路径（在临时目录里），因此不能断言不含 tmp；
        # 真正要保证的是不含「项目源码目录」这类环境信息
        src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assert src_root not in body, f"C. 弹窗泄露了源码目录：{src_root}"
        ok("C. 弹窗不含源码文件名与项目源码路径（无外传泄露风险）")

        assert "ValueError" in box.info and "boom-测试消息" in box.info, \
            f"D. 弹窗缺异常类型/消息：{box.info!r}"
        assert "app.log" in box.info, f"D. 弹窗未给出日志路径：{box.info!r}"
        ok("D. 弹窗含「异常类型 + 消息」摘要与日志文件路径")

        # ---------------- E. 完整性落在日志里 ----------------
        with open(log_path, "r", encoding="utf-8") as f:
            logged = f.read()
        assert "Traceback (most recent call last)" in logged, \
            "E. 日志里没有完整堆栈"
        assert "ValueError: boom-测试消息" in logged, "E. 日志缺异常消息"
        assert "verify_excepthook" in logged, "E. 日志缺出错帧的文件名"
        ok("E. 完整堆栈（含出错帧与文件名）仍完整落在 app.log")

        # ---------------- F. 去重 ----------------
        try:
            raise ValueError("boom-测试消息")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert len(_FakeMessageBox.instances) == 1, \
            f"F. 同一异常不应重复弹窗，实际累计 {len(_FakeMessageBox.instances)}"
        ok("F. 同一异常（类型+消息相同）重复发生只弹一次，防弹窗风暴")

        try:
            raise KeyError("另一个错误")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        assert len(_FakeMessageBox.instances) == 2, \
            f"F. 不同异常应再弹一次，实际累计 {len(_FakeMessageBox.instances)}"
        ok("F. 不同异常仍会正常弹窗（去重只针对同一错误）")

        # ---------------- G. KeyboardInterrupt ----------------
        raised = False
        try:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        except SystemExit as e:
            raised = True
            assert e.code == 0, f"G. 退出码应为 0，实际 {e.code}"
        assert raised, "G. KeyboardInterrupt 未触发 sys.exit(0)"
        ok("G. KeyboardInterrupt 走 sys.exit(0)，不弹窗")
    finally:
        QtWidgets.QMessageBox = real_qmb

    print(f"[DONE] {PASS} 项全部通过")


if __name__ == "__main__":
    main()
