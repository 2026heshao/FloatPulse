# -*- coding: utf-8 -*-
"""大列表窗口化渲染 · 离屏性能验证（run_gui_check.py 包装，offscreen 平台）。

成熟化 3.6 / 优化调研 1.3 验收：碎片工作台与日程任务面板在大数据量下
refresh 走「分块追加 + 滚动加载」，行为等价、首屏只建 FIRST_CHUNK 行。

覆盖：
  A 500 条碎片：refresh 总耗时（数值输出）+ 首屏行数 = FIRST_CHUNK 上限
  B 滚动追加：scrollToBottom 后行数分块增长，反复到底可建满；建满后到底不再追加
  C 500 条任务：同 A（多组分布）
  D 小数据量（≤阈值）行为不变：全量直建，行数 = 描述符总数
  E 窗口化收益参考：同数据全量直建耗时对比（信息输出，不做硬门槛）

只断言首屏行数与追加生效；耗时不设硬性毫秒门槛（防 CI 环境差异），
数字以本脚本输出为准。
"""
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from src.fragment_manager import FragmentManager, TYPE_CLIPBOARD_TEXT  # noqa: E402
from src.list_windowing import FIRST_CHUNK, FULL_THRESHOLD, ListWindowing  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402

_results = []


def check(name, ok, extra=""):
    _results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{extra}）" if extra else ""),
          flush=True)


tmp = tempfile.mkdtemp(prefix="list_perf_")


class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeHost(QObject):
    """面板 refresh 路径所需的最小宿主替身"""

    data_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._config = _FakeConfig()
        # 2026-10-06 修复存量失修：fragments_panel 现经 host.config 读
        # 预览显隐配置，替身宿主补一个只回默认值的 config 口
        self.config = self._config
        self._task_manager = None
        self._fragment_manager = None
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = None
        self.current_theme = "dark"
        self.anim_speed = 1.0


def _build_fragments(n):
    mgr = FragmentManager(os.path.join(tmp, f"fragments_{n}.json"))
    for i in range(n):
        mgr.add_fragment(TYPE_CLIPBOARD_TEXT, f"碎片内容 {i} 正文正文正文",
                         source=f"来源{i}")
    return mgr


def _build_tasks(n):
    mgr = TaskManager(os.path.join(tmp, f"tasks_{n}.json"))
    deadlines = ["2026-01-10", "2026-01-20", "2030-05-01", ""]
    for i in range(n):
        mgr.add_task(f"任务 {i}", "", deadlines[i % len(deadlines)])
    return mgr


def _scroll_to_bottom_until_full(panel, bar, total_rows, max_rounds=50):
    """反复滚到底直到建满（或达到轮次上限），返回追加轮数"""
    rounds = 0
    while panel._frag_list.count() < total_rows and rounds < max_rounds:
        bar.setValue(bar.maximum())
        app.processEvents()
        rounds += 1
    return rounds


# ================== A/B：500 条碎片 ==================
print("== A 500 条碎片 refresh（窗口化）==", flush=True)
from src.fragments_panel import FragmentsPanel  # noqa: E402

host_f = _FakeHost()
host_f._fragment_manager = _build_fragments(500)
panel = FragmentsPanel(host_f)
panel.resize(900, 620)
panel.show()
app.processEvents()

panel.refresh(preserve_view=False)   # 预热一次：排除类初始化/字体引擎等一次性开销
app.processEvents()
t0 = time.perf_counter()
panel.refresh(preserve_view=False)
app.processEvents()
frag_ms = (time.perf_counter() - t0) * 1000.0

total_rows = len(panel._rows)
first_count = panel._frag_list.count()
check("A1 500 条碎片首屏行数 = FIRST_CHUNK 上限",
      first_count == min(FIRST_CHUNK, total_rows),
      f"首屏 {first_count} 行 / 全量 {total_rows} 行")
check("A2 大数据量确实窗口化（首屏 < 总行数）",
      total_rows > FULL_THRESHOLD and first_count < total_rows)
check("A3 计数标签口径不变（显示 500 / 共 500）",
      panel._frag_count_label.text() == "显示 500 条 / 共 500 条",
      panel._frag_count_label.text())
print(f"  [PERF] 500 条碎片 refresh 总耗时 {frag_ms:.1f} ms"
      f"（首屏建 {first_count} 行 / 全量 {total_rows} 行）", flush=True)

# ---- B：滚动追加 ----
print("== B 滚动追加 ==", flush=True)
bar = panel._frag_list.verticalScrollBar()
before = panel._frag_list.count()
bar.setValue(bar.maximum())
app.processEvents()
after = panel._frag_list.count()
check("B1 滚到底触发追加一块",
      after == min(before + FIRST_CHUNK, total_rows),
      f"{before} -> {after} 行")
rounds = _scroll_to_bottom_until_full(panel, bar, total_rows)
check("B2 反复滚到底可建满全部行",
      panel._frag_list.count() == total_rows,
      f"{rounds} 轮追加至 {panel._frag_list.count()} 行")
bar.setValue(bar.maximum())
app.processEvents()
check("B3 建满后滚到底不再追加（分块决策归零）",
      panel._frag_list.count() == total_rows)

# ---- C：500 条任务 ----
print("== C 500 条任务 refresh（窗口化）==", flush=True)
from src.tasks_panel import TasksPanel  # noqa: E402

host_t = _FakeHost()
host_t._task_manager = _build_tasks(500)
panel_t = TasksPanel(host_t)
panel_t.resize(900, 620)
panel_t.show()
app.processEvents()

panel_t.refresh()                    # 预热一次：排除一次性开销
app.processEvents()
t0 = time.perf_counter()
panel_t.refresh()
app.processEvents()
task_ms = (time.perf_counter() - t0) * 1000.0

total_rows_t = len(panel_t._rows)
first_count_t = panel_t._task_list.count()
check("C1 500 条任务首屏行数 = FIRST_CHUNK 上限",
      first_count_t == min(FIRST_CHUNK, total_rows_t),
      f"首屏 {first_count_t} 行 / 全量 {total_rows_t} 行")
check("C2 任务计数标签口径不变（已完成 0/500 条，2026-10-06 紧凑改版）",
      panel_t._task_count_label.text() == "已完成 0/500 条",
      panel_t._task_count_label.text())
print(f"  [PERF] 500 条任务 refresh 总耗时 {task_ms:.1f} ms"
      f"（首屏建 {first_count_t} 行 / 全量 {total_rows_t} 行）", flush=True)

bar_t = panel_t._task_list.verticalScrollBar()
before_t = panel_t._task_list.count()
bar_t.setValue(bar_t.maximum())
app.processEvents()
after_t = panel_t._task_list.count()
check("C3 任务列表滚到底触发追加一块",
      after_t == min(before_t + FIRST_CHUNK, total_rows_t),
      f"{before_t} -> {after_t} 行")

# ---- D：小数据量行为不变 ----
print("== D 小数据量全量直建（≤阈值行为不变）==", flush=True)
host_s = _FakeHost()
host_s._fragment_manager = _build_fragments(30)
panel_s = FragmentsPanel(host_s)
panel_s.refresh(preserve_view=False)
rows_s = len(panel_s._rows)
check("D1 30 条碎片全量直建（行数 = 描述符总数）",
      panel_s._frag_list.count() == rows_s,
      f"{panel_s._frag_list.count()} 行（含组头）")
check("D2 未进入窗口化", panel_s._windowing.windowed is False)

# ---- E：窗口化收益参考（信息输出，不做门槛）----
print("== E 全量直建耗时对比（信息参考）==", flush=True)
panel._windowing = ListWindowing(threshold=10 ** 9)   # 临时关闭窗口化
t0 = time.perf_counter()
panel.refresh(preserve_view=False)
app.processEvents()
full_ms = (time.perf_counter() - t0) * 1000.0
check("E1 全量直建后行数回到总数（对照有效）",
      panel._frag_list.count() == total_rows)
print(f"  [PERF] 500 条碎片 全量直建 {full_ms:.1f} ms vs 窗口化首屏 {frag_ms:.1f} ms"
      f"（本机参考值，随环境浮动）", flush=True)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
