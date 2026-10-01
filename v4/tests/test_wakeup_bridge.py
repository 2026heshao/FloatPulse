# -*- coding: utf-8 -*-
"""第二实例唤醒信号桥回归（2026-10-01 真机炸过一次）。

背景：knowledge_ball.main() 内的 _WakeupBridge 落地时漏写
``woke_up = pyqtSignal()`` 类属性 → 运行期 AttributeError
（第二实例唤醒路径，异常钩子弹窗）。
本文件钉死两层：
  A AST 静态：_WakeupBridge 类体必须声明 woke_up 信号 + show_windows 槽
  B 运行时：按源码 exec 出类 → 实例化 → connect → emit 全链路可跑
    （QObject 无需 QApplication；同线程 emit 走直连，够验证接线；
     跨线程排队行为已由探针单独验证）
"""

import ast
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

KB_PATH = os.path.join(BASE, "knowledge_ball.py")


def _find_bridge_class():
    """从 knowledge_ball.py 提取 _WakeupBridge 的 ast.ClassDef 与源码段"""
    with open(KB_PATH, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "_WakeupBridge"]
    assert len(hits) == 1, f"_WakeupBridge 应恰有 1 处定义，实得 {len(hits)}"
    node = hits[0]
    seg = ast.get_source_segment(src, node)
    assert seg, "无法取回 _WakeupBridge 源码段"
    return node, seg


def test_bridge_declares_signal_and_slot():
    """AST 层：woke_up 信号声明 + show_windows 槽 + QObject 基类，缺一不可"""
    node, _ = _find_bridge_class()
    base_names = {getattr(b, "id", getattr(b, "attr", None)) for b in node.bases}
    assert "QObject" in base_names, "桥类必须继承 QObject（跨线程排队的前提）"

    signal_assigns = []
    methods = []
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            if "woke_up" in targets and \
                    isinstance(stmt.value, ast.Call) and \
                    getattr(stmt.value.func, "id", None) == "pyqtSignal":
                signal_assigns.append(stmt)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(stmt.name)
    assert signal_assigns, "类体缺 woke_up = pyqtSignal() —— 漏写即运行期 AttributeError"
    assert "show_windows" in methods, "桥类缺 show_windows 槽"


def test_bridge_instantiates_and_emits():
    """运行时：按真实源码 exec 出类 → 实例化 → connect → emit 落槽"""
    from PyQt6.QtCore import QObject, pyqtSignal  # noqa: F401 - exec 命名空间用

    _, seg = _find_bridge_class()
    calls = []
    ns = {"QObject": QObject, "pyqtSignal": pyqtSignal,
          "_on_wakeup_signal": lambda: calls.append(1)}
    exec(compile(seg, KB_PATH, "exec"), ns)   # noqa: S102 - 受控源码（本仓）
    Bridge = ns["_WakeupBridge"]
    bridge = Bridge()
    assert hasattr(Bridge, "woke_up"), "实例化后信号属性必须存在"
    bridge.woke_up.connect(bridge.show_windows)
    bridge.woke_up.emit()
    assert calls == [1], "emit 后 show_windows 未执行"


if __name__ == "__main__":
    test_bridge_declares_signal_and_slot()
    print("[OK] AST 断言通过")
    test_bridge_instantiates_and_emits()
    print("[OK] 实例化与 emit 通过")
    print("== 全部通过 ==")
