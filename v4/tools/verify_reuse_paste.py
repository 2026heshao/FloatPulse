# -*- coding: utf-8 -*-
"""Offscreen 功能验证：碎片「一键粘回」+ 置顶（第 3 卡 reuse）。

验证点（全部离屏实跑 + 真数值/像素断言，不是"没报错就算过"）：
  A. 老 fragments.json 无 pinned 键 → 读出照常工作、pinned=False、条数不变
  B. 置顶真生效：置顶条目在列表**第一条真实碎片行**
  C. 置顶淘汰豁免：trim_to_max 不动置顶，只淘汰普通条目（真删计数）
  D. 粘回成功路径：真面板 + 替身后端 → 剪贴板有内容 + 后端真收到 send
  E. 降级路径：还原焦点失败 → 内容仍在剪贴板 + toast，不抛异常
  F. 焦点记录顺序：非前台时才刷新句柄（模拟前台切换后 last_foreign_hwnd 变化）
  G. 重复复制计数：同内容复制 3 次 → count_of=3, duplicate_total=2

运行（offscreen）：
  python tools/run_gui_check.py tools/verify_reuse_paste.py
或直接：
  QT_QPA_PLATFORM=offscreen python tools/verify_reuse_paste.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.fragment_manager import Fragment, FragmentManager  # noqa: E402
from src.fragments_panel import FragmentsPanel  # noqa: E402
from src.paste_helper import PasteHelper  # noqa: E402

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


# ---- 替身 ----
class _Cfg:
    def __init__(self):
        self._d = {}

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v
        return True

    def save(self):
        pass


class _CB:
    def __init__(self):
        self.text = None

    def put_text(self, t):
        self.text = t


class _Host(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _Cfg()
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = _CB()
        self.current_theme = "dark"
        self.anim_speed = 1.0
        self.toasts = []

    def refresh_page(self, _n):
        pass

    def show_toast(self, m):
        self.toasts.append(m)


class _Apis:
    def __init__(self, set_ok=True):
        self._set_ok = set_ok
        self.sent = 0

    def get_foreground_window(self):
        return 7777

    def is_window(self, hwnd):
        return True

    def set_foreground_window(self, hwnd):
        return self._set_ok

    def send_ctrl_v(self):
        self.sent += 1
        return True


def main():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    tmp = tempfile.mkdtemp(prefix="fp_reuse_verify_")

    # ---- A. 老数据零迁移 ----
    path = os.path.join(tmp, "old_fragments.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"fragments": [
            {"fragment_id": 1, "type": "clipboard_text", "content": "老王",
             "source": "test", "created_at": "2026-10-01 09:00",
             "category": "text"},
            {"fragment_id": 2, "type": "clipboard_text", "content": "老李",
             "source": "test", "created_at": "2026-10-02 10:00",
             "category": "text"},
        ], "next_id": 3}, f, ensure_ascii=False)
    mgr = FragmentManager(path)
    check(mgr.count() == 2, "A1 老 fragments.json 读出 2 条（零迁移不丢）")
    check(all(f.pinned is False for f in mgr.get_all_fragments()),
          "A2 老数据 pinned 全部默认 False")
    check(Fragment.from_dict({"fragment_id": 9}).pinned is False,
          "A3 from_dict 缺 pinned 键 → False")

    # ---- B. 置顶排序 ----
    old_id = 1
    mgr.set_pinned(old_id, True)
    order = [f.fragment_id for f in mgr.get_all_fragments()]
    check(order[0] == old_id,
          "B1 置顶碎片（老的一条）排序到最前：%r" % order)

    # ---- C. 淘汰豁免 ----
    for i in range(5):
        mgr.add_clipboard_text("普通内容%d" % i)
    before = mgr.count()
    removed = mgr.trim_to_max(3)
    check(mgr.get_fragment(old_id) is not None,
          "C1 淘汰后置顶碎片仍在（豁免生效）")
    check(mgr.count() == 3 and removed == before - 3,
          "C2 淘汰计数正确：之前 %d → 之后 %d，淘汰 %d"
          % (before, mgr.count(), removed))

    # ---- G. 重复复制计数 ----
    from src.paste_helper import ReuseCounter
    rc = ReuseCounter()
    for _ in range(3):
        rc.record("重复内容")
    check(rc.count_of("重复内容") == 3 and rc.duplicate_total() == 2,
          "G1 重复复制计数：count_of=3, duplicate_total=2")

    # ---- D/E/F. 真面板粘回 ----
    pmgr = FragmentManager(os.path.join(tmp, "panel.json"))
    pmgr.add_clipboard_text("要粘回的内容")
    host = _Host()
    host._fragment_manager = pmgr
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    # D. 成功路径
    apis = _Apis(set_ok=True)
    panel._paste_helper = PasteHelper(apis=apis, sleep=lambda _s: None)
    panel._last_foreign_hwnd = 7777
    fid = None
    for i in range(panel._frag_list.count()):
        v = panel._frag_list.item(i).data(0x0100)
        if v is not None:
            panel._frag_list.item(i).setSelected(True)
            fid = v
            break
    panel._on_paste()
    check(host._clipboard_monitor.text == "要粘回的内容",
          "D1 粘回后剪贴板内容 = 要粘回的内容")
    check(apis.sent == 1, "D2 后端真收到 1 次 Ctrl+V（send 计数=%d）" % apis.sent)
    check(fid is not None, "D3 列表可选中真实碎片行（fid=%r）" % fid)

    # E. 降级路径
    apis2 = _Apis(set_ok=False)
    panel._paste_helper = PasteHelper(apis=apis2, sleep=lambda _s: None)
    host.toasts.clear()
    host._clipboard_monitor.text = None
    panel._on_paste()
    check(host._clipboard_monitor.text == "要粘回的内容",
          "E1 还原失败仍写入剪贴板（降级不丢内容）")
    check(apis2.sent == 0, "E2 还原失败不发键（send=0）")
    check(any("剪贴板" in t or "手动" in t for t in host.toasts),
          "E3 降级给出 toast 提示：%r" % host.toasts)
    assert isinstance(PasteHelper(apis=_Apis(set_ok=False)).paste_to(7777)[0],
                      bool)

    # F. 焦点记忆：模拟"本程序不在前台"时刷新句柄
    class _Win:
        def __init__(self, active):
            self._a = active

        def isActiveWindow(self):
            return self._a

    panel._last_foreign_hwnd = 0
    panel.window = lambda: _Win(False)      # 本程序不在前台
    panel._poll_foreign_foreground()
    check(panel._last_foreign_hwnd == 7777,
          "F1 非前台时记录外部前台窗口 → %r" % panel._last_foreign_hwnd)
    panel._last_foreign_hwnd = 0
    panel.window = lambda: _Win(True)       # 本程序在前台
    panel._poll_foreign_foreground()
    check(panel._last_foreign_hwnd == 0,
          "F2 本程序在前台时不覆盖（保持 0）")

    # E 补充：非 Windows 降级
    from src import paste_helper as ph
    _orig = ph._get_apis
    ph._get_apis = lambda: None
    try:
        helper = PasteHelper()
        okk, reason = helper.paste_to(1)
        check(okk is False and reason == ph.REASON_NOT_WINDOWS,
              "E4 无后端 → 降级 REASON_NOT_WINDOWS（reason=%r）" % reason)
    finally:
        ph._get_apis = _orig

    # 无效句柄：不发键
    check(PasteHelper(apis=_Apis()).paste_to(0)[1] == ph.REASON_NO_FOREGROUND,
          "E5 句柄 0 → REASON_NO_FOREGROUND（不误发键）")

    print("\n==== 验证结果：%d 通过 / %d 失败 ====" % (PASS, FAIL))
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
