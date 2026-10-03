# -*- coding: utf-8 -*-
"""Offscreen 功能验证：碎片「规则清仓 · 审查模式」（第 6 卡 inbox-triage）。

验证点（全部离屏实跑 + 真数值断言，不是"没报错就算过"）：
  A. 实时算 + 三条件 AND：候选集恰好是「久 + 未命中 + 短」那几条
  B. 阈值可调真生效：改 triage_min_days / triage_max_len → 候选集变化
  C. 单条删除真落盘：真读 fragments.json 内容核对（该条已消失）
  D. **delete_fragments 全程零调用**（本卡安全红线）
  E. 不产生任何缓存/快照文件（目录文件名前后比对；无 data_backups 目录）
  F. 老数据零迁移：写一份缺 hit_count 键的旧 json → 读出 hit_count==0 且不崩

运行（offscreen）：
  python tools/run_gui_check.py tools/verify_inbox_triage.py
或直接：
  QT_QPA_PLATFORM=offscreen python tools/verify_inbox_triage.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta  # noqa: E402

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src import inbox_triage  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.fragments_panel import FragmentsPanel  # noqa: E402
from src import fragments_panel as fp_mod  # noqa: E402

# ★ 关键：QApplication 必须保留**模块级强引用**。若写成
#   QApplication.instance() or QApplication([]) 丢弃返回值，PyQt6 下新建的
#   QApplication 无 Python 强引用会立刻被 GC 回收，QApplication.instance()
#   恒为 None，随后构造任何真控件树会**静默硬崩**（退出码 3221226505 =
#   0xC0000409，无 traceback），前面检查全过、后面的根本没跑，却看到"全绿"。
_APP = QApplication.instance() or QApplication([])

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


class _Cfg(ConfigManager):
    pass


class _FakeClipboard:
    def put_text(self, text):
        pass


class _Host(QObject):
    data_changed = pyqtSignal(str)

    def __init__(self, cfg, mgr):
        super().__init__()
        self._config = cfg
        self._fragment_manager = mgr
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = _FakeClipboard()
        self._task_manager = None
        self._temp_asset_manager = None
        self.current_theme = "dark"
        self.anim_speed = 1.0

    def refresh_page(self, _n):
        pass

    def show_toast(self, _m):
        pass


def ts_days_ago(days, now):
    return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")


def _open_dialog(panel):
    """构造清仓对话框但不 exec，返回对话框对象（离屏直连内部句柄）。"""
    captured = {}

    def _fake_exec(self):
        captured["dlg"] = self
        return 0

    orig = fp_mod.GlassDialog.exec
    fp_mod.GlassDialog.exec = _fake_exec
    try:
        panel._open_triage_dialog()
    finally:
        fp_mod.GlassDialog.exec = orig
    return captured["dlg"]


def main():
    now = datetime.now()
    tmp = tempfile.mkdtemp(prefix="fp_verify_triage_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, "fragments.json")

    cfg = _Cfg(os.path.join(data_dir, "config.json"))

    # ---- 构造数据：候选 2 条 + 各种「差一条不满足」的干扰项 ----
    mgr = FragmentManager(json_path)
    f_old_short = mgr.add_clipboard_text("很久以前的短碎片")      # 候选
    f_old_short2 = mgr.add_clipboard_text("另一个旧短碎片")        # 候选
    f_old_long = mgr.add_clipboard_text("很长" * 40)              # 太长 → 不候选
    f_old_hit = mgr.add_clipboard_text("旧的短但被搜过")           # 命中 → 不候选
    f_new_short = mgr.add_clipboard_text("今天的短碎片")           # 太新 → 不候选
    frags = mgr.get_all_fragments()
    frags_by_id = {f.fragment_id: f for f in frags}
    for fid in (f_old_short, f_old_short2, f_old_long, f_old_hit):
        frags_by_id[fid].created_at = ts_days_ago(90, now)
    frags_by_id[f_new_short].created_at = ts_days_ago(1, now)
    mgr.note_search_hit([f_old_hit])      # 制造一次命中
    mgr.flush()

    host = _Host(cfg, mgr)
    panel = FragmentsPanel(host)
    panel.refresh(preserve_view=False)

    # ---------------- D 前置：给 delete_fragments 打 spy ----------------
    spy = {"batch": 0, "single": 0}
    real_single = mgr.delete_fragment
    real_batch = mgr.delete_fragments

    def s_single(fid):
        spy["single"] += 1
        return real_single(fid)

    def s_batch(ids):
        spy["batch"] += 1
        return real_batch(ids)

    mgr.delete_fragment = s_single
    mgr.delete_fragments = s_batch

    # ---------------- E 前置：抓目录文件快照 ----------------
    def listing():
        out = []
        for root, _dirs, files in os.walk(data_dir):
            for fn in files:
                out.append(os.path.relpath(os.path.join(root, fn), data_dir))
        return sorted(out)

    files_before = listing()

    # ---------------- A. 实时算 + 三条件 AND ----------------
    dlg = _open_dialog(panel)
    got_a = {f.fragment_id for f in dlg._triage_candidates()}
    check(got_a == {f_old_short, f_old_short2},
          "A1 候选集恰为「久+未命中+短」两条 (实际 %s)" % sorted(got_a))
    check(f_old_long not in got_a, "A2 太长的不候选")
    check(f_old_hit not in got_a, "A3 被搜索命中过的不候选")
    check(f_new_short not in got_a, "A4 太新的不候选")

    # ---------------- B. 阈值可调真生效 ----------------
    dlg._triage_days_stepper.setValue(200)      # 放到 200 天 → 全都不够旧
    got_b1 = dlg._triage_candidates()
    check(got_b1 == [], "B1 天数阈值提到 200 → 无候选（配置真生效）")
    dlg._triage_days_stepper.setValue(1)        # 放到 1 天 → 命中项仍被挡
    got_b2 = {f.fragment_id for f in dlg._triage_candidates()}
    check(f_new_short in got_b2 and f_old_long not in got_b2,
          "B2 天数放宽到 1 → 新碎片进候选，但长碎片仍被长度阈值挡住")
    dlg._triage_len_stepper.setValue(500)       # 长度放到 500 → 长碎片也进
    got_b3 = {f.fragment_id for f in dlg._triage_candidates()}
    check(f_old_long in got_b3, "B3 长度阈值放宽 → 长碎片进候选")
    check(f_old_hit not in got_b3, "B4 无论阈值怎么放宽，命中过的一律不候选")
    # 还原阈值供后续
    dlg._triage_days_stepper.setValue(30)
    dlg._triage_len_stepper.setValue(40)

    # ---------------- C+D. 单条删除真落盘 + delete_fragments 零调用 ----------------
    target = f_old_short2
    # 自动确认二次确认框
    orig_q = fp_mod.QMessageBox.question
    fp_mod.QMessageBox.question = staticmethod(
        lambda *a, **k: fp_mod.QMessageBox.StandardButton.Yes)
    try:
        dlg._triage_delete_one(target)
    finally:
        fp_mod.QMessageBox.question = orig_q
    mgr.flush()

    # C. 真读盘核对
    with open(json_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    ids_on_disk = {int(r.get("fragment_id")) for r in raw.get("fragments", [])}
    check(target not in ids_on_disk, "C1 被删碎片已从 fragments.json 消失")
    check(f_old_short in ids_on_disk, "C2 未删的候选碎片仍在盘上")
    check(f_new_short in ids_on_disk, "C3 非候选碎片未被误删")

    # D. 安全红线：全程零调用批量删除
    check(spy["single"] == 1, "D1 单条 delete_fragment 恰好调用 1 次 (实际 %d)"
          % spy["single"])
    check(spy["batch"] == 0,
          "★D2 红线：delete_fragments 全程零调用 (实际 %d)" % spy["batch"])

    # 关掉对话框（还原 spy 后再 reject，避免误触）
    mgr.delete_fragment = real_single
    mgr.delete_fragments = real_batch
    dlg.reject()

    # ---------------- E. 不产生**额外的**缓存/快照文件 ----------------
    # 说明：``json_store.save_records`` 自身带「当日首次覆盖写前滚动备份」
    # （backups/<stem>-<YYYYMMDD>.json，见 data_backups），任何一次正常落盘
    # 都会产生它——这与本卡的删除动作无关，不是「为删除做快照」。
    # 本卡红线要钉的是：**删除路径不得额外产生** 任何缓存 / 快照 / 回收站 /
    # 软删除标记文件，尤其不得新建 data_backups 目录或调 rotate_backup。
    files_after = listing()
    new_files = [f for f in files_after if f not in files_before]
    today = datetime.now().strftime("%Y%m%d")
    routine_backup = os.path.join("backups",
                                  "fragments-%s.json" % today)
    unexpected = [f for f in new_files if f != routine_backup]
    check(not unexpected,
          "E1 删除路径无额外缓存/快照文件（新增仅常规滚动备份：%s）" % new_files)
    check(not os.path.exists(os.path.join(data_dir, "data_backups")),
          "E2 未创建 data_backups 目录（未调 rotate_backup 的独立备份）")
    check(not os.path.exists(os.path.join(data_dir, "trash")),
          "E3 未创建回收站目录")
    # backups/ 里只允许常规当日滚动备份，不得多出删除触发的第二份
    bdir = os.path.join(data_dir, "backups")
    if os.path.isdir(bdir):
        bak_names = sorted(os.listdir(bdir))
        check(bak_names == ["fragments-%s.json" % today],
              "E4 backups/ 仅 1 份常规滚动备份（无删除额外快照）：%s"
              % bak_names)
    else:
        check(True, "E4 未产生 backups/ 目录")
    # 目录里不得出现 rotate_backup 风格的 .bak 快照
    baks = [f for f in files_after if f.endswith(".bak")]
    check(not baks, "E5 无 .bak 快照文件 (%s)" % baks)

    # ---------------- F. 老数据零迁移 ----------------
    old_path = os.path.join(tmp, "old_fragments.json")
    with open(old_path, "w", encoding="utf-8") as fh:
        json.dump({"fragments": [
            {"fragment_id": 1, "type": "clipboard_text", "content": "老王",
             "source": "test", "created_at": "2026-01-01 09:00",
             "category": "text", "pinned": False},
            {"fragment_id": 2, "type": "clipboard_text", "content": "老李",
             "source": "test", "created_at": "2026-01-02 10:00",
             "category": "text"},
        ], "next_id": 3}, fh, ensure_ascii=False)
    old_mgr = FragmentManager(old_path)
    check(old_mgr.count() == 2, "F1 旧格式 json（缺 hit_count）读出 2 条不崩")
    check(all(f.hit_count == 0 for f in old_mgr.get_all_fragments()),
          "F2 旧数据 hit_count 全部默认 0（零迁移）")
    cands_old = inbox_triage.collect_candidates(
        old_mgr.get_all_fragments(), now=now, min_days=30, max_len=40)
    check(len(cands_old) == 2, "F3 旧数据可正常进候选（读时补齐字段）")

    print("\n==== 验证结果：%d 通过 / %d 失败 ====" % (PASS, FAIL))
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
