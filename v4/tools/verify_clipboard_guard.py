# -*- coding: utf-8 -*-
"""Offscreen 功能验证：凭证哨兵 clipboard-guard（第 2 卡）。

验证点（全部离屏实跑 + 真数值断言，不是"没报错就算过"）：
  A. 默认关闭：无配置 / enabled=False → 凭证原样落盘，与旧行为等价
  B. mask 处置：命中后明文不进池（真读 fragments.json 内容），占位落盘
  C. deny 处置：整条不落盘，但队列 + 信号均通报（非静默丢弃）
  D. allow 处置：仅本次记录原文
  E. 白名单：commit SHA / UUID / 数字 ID 原样放行，桶计数不涨
  F. 回溯改判：resolve_guard 三态真改碎片内容 / 真删 / 真补写
  G. 「本次记住」：只在本进程生效，配置对象字节不变
  H. 性能：实测「复制 → 落盘」延迟增量（ms），须 < 5ms
  I. 面板入口：命中后按钮出现且计数正确；零裸 QPushButton
  J. 审核计数：凭证进明文池次数（plain.total）随处置正确累加

运行（offscreen）：
  QT_QPA_PLATFORM=offscreen python tools/verify_clipboard_guard.py
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from src import secret_guard as sg  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print("[OK] %s" % msg)


def bad(msg):
    global FAIL
    FAIL += 1
    print("[XX] %s" % msg)


def check(cond, msg):
    ok(msg) if cond else bad(msg)


# 结构化假凭证（非真实密钥）
GH = "ghp_" + "a1B2c3D4" * 5
SK = "sk-" + "Xy9zAb2C" * 5
SHA40 = "a3f9c2e1b4d6a8f0c2e4b6d8a0f2c4e6b8d0a2f4"
UUID = "550e8400-e29b-41d4-a716-446655440000"


class _Cfg:
    def __init__(self, data=None):
        self._d = dict(data or {})

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v
        return True

    def save(self):
        pass


class _CB(QObject):
    dataChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._text = ""

    def text(self):
        return self._text

    def mimeData(self):
        return None

    def setText(self, t):
        self._text = t


class _StubQApp:
    _clip = None

    @classmethod
    def clipboard(cls):
        return cls._clip


def _paste(clip, text):
    clip._text = text
    clip.dataChanged.emit()


def _make(monkeypatch, cfg, json_path):
    import src.clipboard_monitor as clm
    from src.clipboard_monitor import ClipboardMonitor
    from src.fragment_manager import FragmentManager

    clip = _CB()
    _StubQApp._clip = clip
    clm.QApplication = _StubQApp
    clm._get_foreground_process_name = lambda: ""
    fm = FragmentManager(json_path)
    mon = ClipboardMonitor(fm, config_manager=cfg)
    mon.start()
    events = []
    mon.secret_guarded.connect(events.append)
    return mon, clip, fm, events


def _stored_contents(json_path):
    """真读落盘 json，取全部碎片内容（验证"真没进明文池"）。"""
    if not os.path.exists(json_path):
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("fragments", [])
    return [it.get("content", "") for it in items]


# ⚠ 必须保留强引用（2026-10-03 实测踩坑）：
#   `QApplication.instance() or QApplication([])` 这种「丢弃返回值」的写法
#   在 PyQt6 下会让新建的 QApplication 立刻被 Python GC 回收 —— C++ 对象随
#   之析构，此后 QApplication.instance() 恒为 None。本脚本前面 A–H 组只跑
#   纯逻辑不受影响，但 I 组要构造 FragmentsPanel（真 Qt 控件树），没有存活的
#   QApplication 时会**静默硬崩**：退出码 0xC0000409（STATUS_STACK_BUFFER_
#   OVERRUN），无 traceback，stdout 在上一行截断。
#   本脚本原实现正是丢弃返回值，导致 I/J 两组的 11 项检查从未真正执行过。
_APP = None


def main():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp(prefix="floatpulse_guard_")
    from src.fragment_manager import FragmentManager
    from src.clipboard_monitor import ClipboardMonitor
    ClipboardMonitor._audit.update(
        {"plain": 0, "masked": 0, "denied": 0, "allowed": 0})

    # ---- A. 默认关闭 ----
    p_a = os.path.join(tmp, "a.json")
    fm = FragmentManager(p_a)
    mon, clip, fm, events = _make(None, None, p_a)
    _paste(clip, GH)
    fm.save()
    check(_stored_contents(p_a) == [GH],
          "A1 无配置 → 凭证原样落盘（明文池含原文）")
    check(events == [] and ClipboardMonitor.guard_audit()["masked"] == 0,
          "A2 无配置 → 不拦截、不计入哨兵桶")

    p_a2 = os.path.join(tmp, "a2.json")
    mon2, clip2, fm2, ev2 = _make(None, _Cfg({"clipboard_guard_enabled": False}),
                                  p_a2)
    _paste(clip2, SK)
    fm2.save()
    check(_stored_contents(p_a2) == [SK], "A3 enabled=False → 与旧行为等价")

    # ---- B. mask ----
    p_b = os.path.join(tmp, "b.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "mask"}), p_b)
    _paste(clip, f"部署密钥 {SK} 请勿外传")
    fm.save()
    stored = _stored_contents(p_b)
    check(len(stored) == 1 and SK not in stored[0] and sg.MASK_PLACEHOLDER in stored[0],
          "B1 mask → 明文未进池，占位落盘（%r）" % (stored[0][:40] if stored else ""))
    check("部署密钥" in (stored[0] if stored else ""),
          "B2 mask → 上下文保留，用户看得出是哪条")
    check(ClipboardMonitor.guard_audit()["masked"] == 1, "B3 masked 桶 +1")

    # ---- C. deny ----
    p_c = os.path.join(tmp, "c.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "deny"}), p_c)
    _paste(clip, GH)
    fm.save()
    check(_stored_contents(p_c) == [], "C1 deny → 整条不落盘")
    check(len(events) == 1 and events[0]["mode"] == "deny",
          "C2 deny → 信号仍通报（非静默丢弃）")
    check(mon.guard_pending_count() == 1, "C3 deny → 进待办队列供回溯")

    # ---- D. allow ----
    p_d = os.path.join(tmp, "d.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "allow"}), p_d)
    _paste(clip, GH)
    fm.save()
    check(_stored_contents(p_d) == [GH], "D1 allow → 本次记录原文")
    check(ClipboardMonitor.guard_audit()["allowed"] == 1, "D2 allowed 桶 +1")

    # ---- E. 白名单 ----
    p_e = os.path.join(tmp, "e.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "mask"}), p_e)
    before = ClipboardMonitor.guard_audit()["masked"]
    for t in (SHA40, UUID, "12345678901234567890123456789012",
              "commit %s" % SHA40, "rebase %s" % SHA40):
        _paste(clip, t)
    fm.save()
    after = ClipboardMonitor.guard_audit()["masked"]
    check(after == before, "E1 白名单 5 例全部放行（桶计数不变 %d→%d）"
          % (before, after))
    check(set(_stored_contents(p_e)) == {SHA40, UUID,
          "12345678901234567890123456789012", "commit %s" % SHA40,
          "rebase %s" % SHA40}, "E2 白名单内容原样落盘（5 条全在）")

    # ---- F. 回溯改判 ----
    p_f = os.path.join(tmp, "f.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "mask"}), p_f)
    _paste(clip, GH)
    check(mon.guard_pending_count() == 1, "F1 命中入队")
    check(mon.resolve_guard(0, "allow") is True
          and GH in [f.content for f in fm.get_all_fragments()],
          "F2 allow 改判 → 碎片内容改回原文")
    _paste(clip, SK)
    check(mon.resolve_guard(0, "deny") is True and fm.count() == 1,
          "F3 deny 改判 → 该条被删（只剩 F2 那条）")
    p_f2 = os.path.join(tmp, "f2.json")
    mon, clip, fm, events = _make(
        None, _Cfg({"clipboard_guard_enabled": True,
                    "clipboard_guard_mode": "deny"}), p_f2)
    _paste(clip, GH)
    check(fm.count() == 0 and mon.resolve_guard(0, "allow") is True
          and fm.count() == 1, "F4 deny 后回溯 allow → 原文补写回池")

    # ---- G. 本次记住 ----
    cfg_g = _Cfg({"clipboard_guard_enabled": True, "clipboard_guard_mode": "mask"})
    snapshot = dict(cfg_g._d)
    p_g = os.path.join(tmp, "g.json")
    mon, clip, fm, events = _make(None, cfg_g, p_g)
    mon.remember_guard_mode("deny")
    _paste(clip, GH)
    check(fm.count() == 0, "G1 本次记住 deny → 命中被丢弃")
    check(cfg_g._d == snapshot, "G2 未写持久配置（配置对象逐键不变）")

    # ---- H. 性能 ----
    p_h1 = os.path.join(tmp, "h1.json")
    p_h2 = os.path.join(tmp, "h2.json")
    long_text = ("这是一段很长的普通文本，用来说明性能。 " * 60).strip()

    def _latency(cfg, path):
        mon, clip, fm, events = _make(None, cfg, path)
        for _ in range(5):
            _paste(clip, "warm-%d" % time.perf_counter_ns())
        t0 = time.perf_counter()
        n = 300
        for i in range(n):
            _paste(clip, "%s-%d" % (long_text, i))
        return (time.perf_counter() - t0) / n * 1000.0

    base = _latency(_Cfg({"clipboard_guard_enabled": False}), p_h1)
    armed = _latency(_Cfg({"clipboard_guard_enabled": True,
                           "clipboard_guard_mode": "mask"}), p_h2)
    delta = armed - base
    check(delta < 5.0, "H1 延迟增量 %.4f ms < 5ms（base=%.4f armed=%.4f）"
          % (delta, base, armed))

    # ---- I. 面板入口 ----
    # ⚠ 隔离原因（2026-10-03 实测踩坑）：上面的 _make() 把
    #    clipboard_monitor 模块级的 QApplication 换成了 _StubQApp，用来驱动
    #    假剪贴板。但 ClipboardMonitor.__init__ 里的
    #    `self._clipboard = QApplication.clipboard()` 在真类下会抓到真 Qt
    #    剪贴板，start() 又把它的 dataChanged 接上 _on_data_changed ——
    #    真剪贴板与假剪贴板两套事件源并存时，构造 FragmentsPanel 会静默硬崩
    #    （退出码 0xC0000409，无 traceback，输出在 H1 处截断）。
    #    修法：I 组独立建一套「真 QApplication + 真剪贴板」的监控实例，
    #    不复用前面被 monkeypatch 污染的那一个。
    import src.clipboard_monitor as clm
    clm.QApplication = QApplication
    from src.fragments_panel import FragmentsPanel

    class _Host(QObject):
        data_changed = pyqtSignal(str)

        def __init__(self, mgr, mon):
            super().__init__()
            self._config = _Cfg({"clipboard_guard_enabled": True,
                                 "clipboard_guard_mode": "mask"})
            self._fragment_manager = mgr
            self._note_manager = None
            self._docx_manager = None
            self._nav_manager = None
            self._clipboard_monitor = mon
            self._task_manager = None
            self._temp_asset_manager = None
            self.current_theme = "dark"
            self.anim_speed = 1.0
            self.toasts = []
            self._mgr = mgr

        def refresh_page(self, _n):
            pass

        def show_toast(self, m):
            self.toasts.append(m)

    # I 组独立实例：真 QApplication + 内存档案（不 start()，不接管真剪贴板事件）
    i_path = os.path.join(tmp, "i.json")
    i_mgr = FragmentManager(i_path)
    i_mon = ClipboardMonitor(
        i_mgr, config_manager=_Cfg({"clipboard_guard_enabled": True,
                                    "clipboard_guard_mode": "mask"}))
    host = _Host(i_mgr, i_mon)
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)
    check(panel._guard_btn.isHidden() is True, "I1 无命中 → 入口隐藏")
    # 直接驱动隔离实例的写入路径（等价于命中一次凭证），不走假剪贴板
    i_mon._add_text_fragment(GH)
    check(panel._guard_btn.isHidden() is False, "I2 命中 → 入口出现")
    check("1" in panel._guard_btn.text(), "I3 入口计数含 1")
    check(any("凭证" in t for t in host.toasts), "I4 命中只 toast，不弹窗")
    bare = [w for w in panel.findChildren(QPushButton)
            if type(w).__name__ == "QPushButton"]
    check(not bare, "I5 面板零裸 QPushButton（I5 检出 %d）" % len(bare))

    # ---- J. 审核计数 ----
    audit = ClipboardMonitor.guard_audit()
    check(set(audit) == {"plain", "masked", "denied", "allowed"},
          "J1 审核计数四桶齐备：%r" % audit)
    check(audit["masked"] >= 1 and audit["denied"] >= 1 and audit["allowed"] >= 1,
          "J2 三态处置均被计数：%r" % audit)

    print("\n==== 验证结果：%d 通过 / %d 失败 ====" % (PASS, FAIL))
    print("实测延迟：base=%.4fms armed=%.4fms delta=%.4fms"
          % (base, armed, delta))
    print("审核计数：%r" % audit)
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
