# -*- coding: utf-8 -*-
"""周报插件离屏端到端验证：真实数据 → 真实加载器 → 真实菜单 → 真实对话框。

覆盖（每一步都走真实代码路径）：
  A 插件被真实扫到：动作进注册表、热键格式合法、独立 ctx（plugin_id/plugin_dir）
  B 纯函数层：as_date / relative_deadline / safe_filename / one_line /
    resolve_range / build_report（含脏数据、跨类别、缺完成时间）
  C 真实宿主数据 → 报告各分区齐全（完成任务 / 逾期未完成 / 碎片分类 / 笔记 / 番茄）
  D 悬浮球右键菜单出现插件项，且夹在「打开主窗口」与「退出程序」之间
  E 真实触发动作 → QTimer.singleShot(0) 路径真的构造了对话框（父窗口=主窗口）
  F 对话框真身：范围控件 / 三个输出按钮 / vault 未配置时置灰
  G 复制到剪贴板
  H 另存为 .md（文件对话框替身）→ 内容=预览文本、UTF-8 无 BOM、换行 LF
  I 写入 Obsidian vault → <vault>/FloatPulse/报告/…；已存在时询问后覆盖
  J 增强能力：data_dir 自动创建、深拷贝只读、parent_window

跑法：python tools/run_gui_check.py tools/verify_weekly_report.py
"""
import logging
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication, QMessageBox   # noqa: E402
from PyQt6.QtGui import QFontDatabase                   # noqa: E402

from src.config import ConfigManager                       # noqa: E402
from src.docx_manager import DocxManager                   # noqa: E402
from src.task_manager import TaskManager                   # noqa: E402
from src.note_manager import NoteManager                   # noqa: E402
from src.fragment_manager import FragmentManager           # noqa: E402
from src.clipboard_monitor import ClipboardMonitor         # noqa: E402
from src.temp_asset_manager import TempAssetManager        # noqa: E402
from src.main_window import MainWindow                     # noqa: E402
from src.plugin_api import (                               # noqa: E402
    ActionRegistry, PluginContext, PluginData,
)
from src.plugin_loader import PluginLoader                 # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setApplicationName("verify_weekly_report")
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

_results = []
PLUGIN_ID = "weekly-report"
ACTION_ID = f"{PLUGIN_ID}.draft"
MENU_TITLE = "📝 生成日报 / 周报草稿"


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


def pump(ms=0):
    if ms <= 0:
        _app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.01)


# ====================================================================
# 替身
# ====================================================================
class _FakeFileDialog:
    result = ""
    calls = 0

    @staticmethod
    def getSaveFileName(*_a, **_k):
        _FakeFileDialog.calls += 1
        return _FakeFileDialog.result, ""


class _FakeMessageBox:
    """QMessageBox 替身：StandardButton 复用真枚举（stub 必须支持 ``Yes | No``）"""
    StandardButton = QMessageBox.StandardButton
    answer = None
    asked = 0

    @classmethod
    def question(cls, *_a, **_k):
        cls.asked += 1
        if cls.answer is not None:
            return cls.answer
        return QMessageBox.StandardButton.No

    @staticmethod
    def warning(*_a, **_k):
        return None


class _SpyDialog:
    """ReportDialog 替身：只记录构造参数，exec 立即返回。"""
    instances = []

    def __init__(self, ctx, parent=None):
        self.ctx = ctx
        self.parent = parent
        _SpyDialog.instances.append(self)

    def exec(self):
        return 0


# ====================================================================
# 日志（真实 logger：插件 run() 里会调 ctx.logger.info）
# ====================================================================
class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
_logger = logging.getLogger("fp_verify_weekly_report")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_logger.handlers.clear()
_logger.addHandler(_Capture(_records))
warns = lambda: [r.getMessage() for r in _records                      # noqa: E731
                 if r.levelno >= logging.WARNING]


# ====================================================================
# 搭数据 / 上下文
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_wr_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)
vault = os.path.join(tmp, "vault")
os.makedirs(vault, exist_ok=True)

config = ConfigManager(os.path.join(data_dir, "config.json"))
docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data_dir, "docx_meta.json"))
docx_mgr.load()
task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
clip = ClipboardMonitor(frag_mgr, config)
temp_mgr = TempAssetManager(tmp)

today = date.today()
monday = today - timedelta(days=today.weekday())

# 完成任务（完成时间 = 此刻，必落在默认「本周」区间）
done_id = task_mgr.add_task("已完成的任务", "备注文字", today.isoformat())
task_mgr.set_done(done_id, True)
task_mgr.add_focus_session(done_id, 2)
# 逾期未完成
overdue_id = task_mgr.add_task("逾期的任务", "", (today - timedelta(days=3)).isoformat())
task_mgr.add_focus_session(overdue_id, 1)
# 远期任务（应被排除）
task_mgr.add_task("远期任务", "", (today + timedelta(days=30)).isoformat())

frag_mgr.add_clipboard_text("https://example.com/case-a", source="剪贴板")
frag_mgr.add_clipboard_text("def demo():\n    return 42", source="剪贴板")
frag_mgr.add_clipboard_path(r"C:\Users\x\report.md", source="拖拽拾取")
frag_mgr.flush()

note_mgr.add_note("本周会议要点正文", title="周会记录")
temp_note = note_mgr.get_temp_note()          # 临时笔记必须被报告跳过

# 兼容垫片：并行会话正在新增「插件中心」页（config.NAV_PAGE_KEYS 已含
# "plugins"，但 main_window.NAV_PAGE_TITLES / NAV_PAGE_INDEX 尚未同步），
# 会让侧边栏构建 KeyError。本脚本只验证周报窗口，不关心导航，
# 因此把 TITLES/INDEX 补齐到与 NAV_PAGE_KEYS 对齐即可（不改动源码）。
_mw = sys.modules["src.main_window"]
if "plugins" not in _mw.NAV_PAGE_TITLES:
    _mw.NAV_PAGE_TITLES["plugins"] = "🔌  插件中心"
    _mw.NAV_PAGE_INDEX.setdefault("plugins", 9)

win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr)
win.resize(1000, 760)
win.show()
pump(200)

# ====================================================================
# A. 真实加载器扫到真插件（同 main() 的接线方式）
# ====================================================================
plugins_dir = os.path.join(ROOT, "plugins")
registry = ActionRegistry(
    logger=_logger,
    reserved_hotkeys=(config.get("quick_capture_hotkey", "Ctrl+Alt+K"),
                      config.get("screenshot_hotkey", "Ctrl+Alt+S")))
plugin_data = PluginData(providers={
    "tasks": lambda: [t.to_dict() for t in task_mgr.get_all_tasks()],
    "fragments": lambda: [f.to_dict() for f in frag_mgr.get_all_fragments()],
    "notes": lambda: [n.to_dict() for n in note_mgr.get_all_notes()],
    "pomodoro": lambda: {"enabled": True, "state": "idle", "phase": "focus",
                         "remaining_seconds": 0, "progress": 0.0,
                         "focus_minutes": 25, "break_minutes": 5,
                         "auto_break": False, "bound_task_id": None,
                         "bound_title": ""},
})
ctx = PluginContext(
    logger=_logger,
    config=config.as_dict(),
    show_toast=win.show_toast,
    open_main_window=lambda: None,
    open_card_mode=lambda mode: True,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
)
loader = PluginLoader(registry, ctx, plugins_dir=plugins_dir)
loaded = loader.load_all()
ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 weekly-report", PLUGIN_ID in ids, f"{ids}")

act = registry.get(ACTION_ID)
check("A2 动作已进注册表且挂菜单", act is not None and act.menu is True)
check("A3 热键保留（Ctrl+Alt+W 与核心热键不冲突；2026-10 起 5 插件各注册热键，改判包含）",
      act is not None and act.hotkey == "Ctrl+Alt+W"
      and ACTION_ID in [a.id for a in registry.hotkey_actions()],
      f"{act.hotkey if act else None}")

sub_ctx = registry.context_of(ACTION_ID)
check("A4 注册表记住了插件自己的上下文",
      sub_ctx is not None and sub_ctx.plugin_id == PLUGIN_ID)
check("A5 plugin_dir 指向插件目录",
      sub_ctx is not None
      and os.path.normcase(sub_ctx.plugin_dir) ==
      os.path.normcase(os.path.join(plugins_dir, PLUGIN_ID)),
      getattr(sub_ctx, "plugin_dir", None))

plug = sys.modules.get("floatpulse_plugin_weekly_report")
check("A6 插件模块可从 sys.modules 取到", plug is not None)

# ====================================================================
# J. 增强能力（data_dir / 深拷贝 / parent_window）
# ====================================================================
dd = sub_ctx.data_dir
check("J1 data_dir 首次访问自动创建",
      bool(dd) and os.path.isdir(dd)
      and os.path.basename(dd) == PLUGIN_ID, dd)
check("J2 parent_window 返回真实主窗口", ctx.parent_window() is win)

snapshot = ctx.data.tasks()
before_title = snapshot[0]["title"]
snapshot[0]["title"] = "被插件改了"
again = ctx.data.tasks()
check("J3 只读快照是真副本：改返回值碰不到宿主数据",
      again[0]["title"] == before_title and before_title == "已完成的任务",
      f"{again[0]['title']!r}")
check("J4 数据源清单可自省",
      set(ctx.data.sources()) == {"tasks", "fragments", "notes", "pomodoro"},
      f"{ctx.data.sources()}")

# ====================================================================
# B. 纯函数层
# ====================================================================
check("B1 as_date 认 ISO / 拒脏值",
      plug.as_date("2026-09-27 14:03") == date(2026, 9, 27)
      and plug.as_date("") is None and plug.as_date("2026/09/27") is None
      and plug.as_date(None) is None)
check("B2 relative_deadline 相对文案",
      plug.relative_deadline("2026-09-27", date(2026, 9, 27)) == "今天"
      and plug.relative_deadline("2026-09-26", date(2026, 9, 27)) == "⚠ 逾期 1 天"
      and plug.relative_deadline("", date(2026, 9, 27)) == ""
      and "9月29日" in plug.relative_deadline("2026-09-29", date(2026, 9, 27)))
raw_name = '周报 2026/09: x*?"<>|'
clean = plug.safe_filename(raw_name)
check("B3 safe_filename 洗非法字符且稳定",
      not any(ch in clean for ch in '\\/:*?"<>|')
      and clean.startswith("周报 2026")
      and clean == plug.safe_filename(raw_name)
      and plug.safe_filename("CON") == "_CON"
      and plug.safe_filename("...") == "未命名",
      repr(clean))
check("B4 one_line 压平换行并截断",
      plug.one_line("第一行\n第二行") == "第一行 第二行"
      and plug.one_line("x" * 200, 10) == "x" * 10 + "…")

t = date(2026, 9, 27)          # 周日
check("B5 resolve_range：本周取周一起点（ISO 周）",
      plug.resolve_range("week", t) == (date(2026, 9, 21), t, "本周"),
      f"{plug.resolve_range('week', t)}")
check("B6 resolve_range：最近 7 天含今天",
      plug.resolve_range("7d", t) == (date(2026, 9, 21), t, "最近 7 天"))
check("B7 resolve_range：自定义起止颠倒自动交换",
      plug.resolve_range("custom", t, date(2026, 9, 20), date(2026, 9, 10))
      == (date(2026, 9, 10), date(2026, 9, 20), "自定义区间"))

# 脏数据 / 边界：空标题、未知类别、缺完成任务时间、空来源
dirty = plug.build_report(
    [{"task_id": 1, "title": "缺完成时间", "note": "", "deadline": "2026-09-23",
      "done": True, "created_at": "2026-09-19 09:00", "completed_at": "",
      "focus_sessions": 0},
     {"task_id": 2, "title": "", "note": None, "deadline": "2026-09-20",
      "done": False, "created_at": "", "completed_at": "", "focus_sessions": 0},
     {"task_id": 3, "title": "远期", "note": "", "deadline": "2026-12-01",
      "done": False, "created_at": "", "completed_at": "", "focus_sessions": 0},
     {"task_id": 4, "title": "已完成但无任何日期", "note": "", "deadline": "",
      "done": True, "created_at": "脏值", "completed_at": "脏值",
      "focus_sessions": 0},
     None],
    [{"fragment_id": 1, "category": "weird", "content": "未知类别",
      "source": None, "created_at": "2026-09-25 10:00"},
     {"fragment_id": 2, "category": None, "content": None,
      "source": "", "created_at": "2026-09-25 11:00"}],
    [{"note_id": 1, "title": "📌 临时笔记", "content": "x",
      "create_time": "2026-09-25 10:00", "update_time": "2026-09-25 10:00"},
     {"note_id": 2, "title": "", "content": "y",
      "create_time": "2026-09-25 10:00", "update_time": "2026-09-25 10:00"}],
    "不是 dict", date(2026, 9, 21), date(2026, 9, 27), label="本周",
    now=datetime(2026, 9, 27, 16, 30))
check("B8 缺完成时间 → 归入文末并标注",
      "完成时间缺失" in dirty and "※" in dirty and "缺完成时间" in dirty,
      dirty[:80])
check("B9 未知类别归入「其他」、category=None 兜底为文本",
      "### 其他（1）" in dirty and "### 📄 文本（1）" in dirty)
check("B10 空标题/无来源/None 条目不崩（兜底文案）",
      "（无标题）" in dirty and "未知来源" in dirty)
check("B11 三条日期全脏的已完成任务不被静默丢弃（进文末）",
      "无日期已完成" in dirty and "已完成但无任何日期" in dirty
      and "无法归入任何区间" in dirty)
check("B12 临时笔记与空标题笔记被跳过", "临时笔记" not in dirty)
check("B13 远期任务不出现在未完成列表",
      "远期" not in dirty.split("## 🔄")[1].split("## 🧩")[0])
check("B14 番茄计数如实标注「按任务累计」",
      "按任务累计" in dirty and "非时间区间统计" in dirty
      and "当前番茄钟：空闲" in dirty)

# ====================================================================
# C. 真实宿主数据 → 报告内容
# ====================================================================
report = plug.build_report(ctx.data.tasks(), ctx.data.fragments(),
                           ctx.data.notes(), ctx.data.pomodoro(),
                           monday, today, label="本周")
head = f"# 周报 {monday.isoformat()} ~ {today.isoformat()}"
check("C1 标题按区间生成", report.startswith(head), report.split("\n")[0])
check("C2 已完成任务出现在报告里",
      "已完成的任务" in report and "- [x]" in report)
check("C3 逾期任务出现在未完成列表并标红",
      "逾期的任务" in report and "逾期" in report)
check("C4 远期任务被排除", "远期任务" not in report)
check("C5 碎片分区含链接与代码两类",
      "## 🧩 本周期碎片" in report and "🔗 链接" in report and "💻 代码" in report)
check("C6 笔记分区含周会记录且排除临时笔记",
      "## 📝 本周期更新的笔记" in report and "周会记录" in report
      and "临时笔记" not in report)
check("C7 番茄分区存在且含累计次数",
      "## 🍅 专注统计" in report
      and "本周期已完成任务累计专注 **2** 次" in report, report[-260:])
check("C8 范围与来源写进头部",
      "范围：本周" in report and "weekly-report 插件" in report)

# ====================================================================
# D. 悬浮球右键菜单（真实 FloatingBall）
# ====================================================================
from knowledge_ball import FloatingBall          # noqa: E402
ball = FloatingBall([])
ball.set_action_registry(registry, ctx)
# v2026-10-01 分组收纳：插件动作在「🧩 插件功能」子菜单里（一级只剩子菜单项）
_plug_menu = next((a.menu() for a in ball._menu.actions()
                   if a.text() == "🧩 插件功能"), None)
texts = [a.text() for a in _plug_menu.actions()] if _plug_menu else []
print(f"    插件功能子菜单：{texts}", flush=True)
check("D1 插件动作出现在球右键菜单的插件功能子菜单里",
      _plug_menu is not None and MENU_TITLE in texts, f"{texts}")
if MENU_TITLE in texts:
    i_plug = texts.index(MENU_TITLE)
    check("D2 位置正确：子菜单夹在「打开主窗口」与「退出程序」之间",
          _plug_menu.parent() is not None
          and ball._menu.actions()[-1].text() == "退出程序",
          f"first={ball._menu.actions()[0].text()} "
          f"last={ball._menu.actions()[-1].text()}")
else:
    check("D2 位置正确：子菜单夹在「打开主窗口」与「退出程序」之间", False)

# ====================================================================
# E. 真实触发：QTimer.singleShot 路径
# ====================================================================
real_dialog = plug.ReportDialog
plug.ReportDialog = _SpyDialog
_SpyDialog.instances = []
item = [a for a in _plug_menu.actions() if a.text() == MENU_TITLE] \
    if _plug_menu else []
check("E1 能找到菜单 QAction", len(item) == 1)
item[0].trigger()
pump(120)
check("E2 菜单触发 → 经单次定时器真的构造了对话框",
      len(_SpyDialog.instances) == 1, f"{len(_SpyDialog.instances)}")
if _SpyDialog.instances:
    spy = _SpyDialog.instances[0]
    check("E3 对话框拿到插件自己的 ctx（plugin_id 正确）",
          getattr(spy.ctx, "plugin_id", None) == PLUGIN_ID)
    check("E4 对话框父窗口 = 主窗口", spy.parent is win)

# 热键路径同样只排队（不在原生事件过滤器里直接开模态框）
_SpyDialog.instances = []
check("E5 热键路径的触发入口是注册表 trigger（同一条代码路径）",
      registry.trigger(ACTION_ID) is True)
pump(120)
check("E6 热键路径同样构造了对话框（未阻塞）",
      len(_SpyDialog.instances) == 1, f"{len(_SpyDialog.instances)}")
plug.ReportDialog = real_dialog

# ====================================================================
# F. 对话框真身
# ====================================================================
dlg = real_dialog(ctx, win)
pump(60)
check("F1 对话框构造成功（父窗口生效）", dlg.parent() is win)
check("F2 默认范围 = 本周，日期控件仅自定义时可用",
      dlg._current_key() == "week"
      and not dlg._from.isEnabled() and not dlg._to.isEnabled())
dlg._radios["custom"].setChecked(True)
pump(30)
check("F3 选「自定义区间」→ 起止日期控件启用",
      dlg._from.isEnabled() and dlg._to.isEnabled())
dlg._radios["today"].setChecked(True)
pump(30)
preview = dlg._text.toPlainText()
check("F4 切到「今日」→ 预览变成日报且范围收窄",
      preview.startswith(f"# 日报 {today.isoformat()}"),
      preview.split("\n")[0])
dlg._radios["week"].setChecked(True)
pump(30)
check("F5 切回「本周」→ 预览恢复周报", dlg._text.toPlainText().startswith("# 周报"))
check("F6 三个输出按钮文案正确（另存为已图标化：emoji 前缀剥除，图标走 IconButton）",
      dlg._copy_btn.text() == "📋 复制到剪贴板"
      and dlg._save_btn.text() == "另存为 .md…"
      and dlg._vault_btn.text() == "🗂 写入 Obsidian vault")
check("F7 vault 未配置 → 写入按钮置灰且给出原因",
      not dlg._vault_btn.isEnabled() and "设置" in dlg._vault_btn.toolTip(),
      dlg._vault_btn.toolTip())

# ====================================================================
# F8-F13. UI 与主窗口一致性（本次改动的核心护栏）
#   对话框继承 PluginDialog（→ GlassDialog），复用主窗口 QSS 与玻璃壳，
#   因此「玻璃壳存在 / 无边框 / 圆角阴影 / 自绘标题栏 / 主题跟随」
#   这些视觉约定都必须成立，否则会退回系统原生外观。
# ====================================================================
from src.glass_dialog import GlassDialog            # noqa: E402
from src.plugin_ui import PluginDialog              # noqa: E402
from src.theme import get_colors     # noqa: E402
from PyQt6.QtCore import Qt as _Qt                  # noqa: E402

check("F8 对话框继承官方玻璃基类（PluginDialog → GlassDialog）",
      isinstance(dlg, PluginDialog) and isinstance(dlg, GlassDialog),
      f"mro={[c.__name__ for c in type(dlg).__mro__[:4]]}")

check("F9 无边框 + 半透明背景（圆角才真正生效）",
      bool(dlg.windowFlags() & _Qt.WindowType.FramelessWindowHint)
      and dlg.testAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground))

# 玻璃壳：与主窗口同样的 objectName + GlassPanel 类型
from src.glass import GlassPanel                    # noqa: E402
check("F10 挂载玻璃壳（GlassPanel + objectName=mainWindow，与主窗口一致）",
      isinstance(dlg._container, GlassPanel)
      and dlg._container.objectName() == "mainWindow",
      f"type={type(dlg._container).__name__} name={dlg._container.objectName()!r}")

# QSS 必须真的下发到容器上（复用主窗口 QSS → 按钮/输入框样式才生效）
_qss = dlg._container.styleSheet()
check("F11 容器已套用主窗口 QSS（非空且含主色按钮规则）",
      bool(_qss) and "primaryBtn" in _qss and "secondaryBtn" in _qss,
      f"qss_len={len(_qss)}")

# 自绘标题栏（标题文案 + 右上角关闭按钮）
from PyQt6.QtWidgets import QLabel as _QLabel, QWidget as _QWidget   # noqa: E402
_tb = dlg._container.findChild(_QWidget, "titleBar")
_tb_labels = [c.text() for c in _tb.findChildren(_QLabel)] if _tb else []
check("F12 自绘标题栏存在且含标题文案",
      _tb is not None and "日报 / 周报草稿" in _tb_labels,
      f"labels={_tb_labels}")

# 主题跟随：切到 dark 后颜色表必须变化（证明 apply_theme 真的读到了主题）
_light = get_colors("light")
_dark = get_colors("dark")
check("F13 light/dark 配色表不同（主题跟随有可观测差异）",
      _light["text"] != _dark["text"] and _light["panel_fill"] != _dark["panel_fill"],
      f"light.text={_light['text']} dark.text={_dark['text']}")

# 内容区关键控件都在 body 里（不是直接挂在对话框上）——
# 这是 GlassDialog 的布局约定，挂错会导致内容压在标题栏下面
_children = set(dlg.body.findChildren(object))
check("F14 预览/状态/按钮均位于 body 内容区（布局约定）",
      dlg._text in _children and dlg._status in _children
      and dlg._copy_btn in _children)

# ====================================================================
# G/H/I. 三个输出动作
# ====================================================================
plug.QFileDialog = _FakeFileDialog
plug.QMessageBox = _FakeMessageBox

# 配好 vault 的上下文（config 是构造期快照，必须换 ctx 才能改观）
config.set("obsidian_vault_path", vault)
config.save()
ctx_vault = PluginContext(
    logger=None, config=config.as_dict(), data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win, show_toast=win.show_toast)
dlg = real_dialog(ctx_vault, win)
pump(60)
check("G0 vault 配置后 → 写入按钮可用", dlg._vault_btn.isEnabled())
dlg._radios["today"].setChecked(True)          # 今日 → 文件名可预测
pump(30)
text = dlg._text.toPlainText()

# ---- G. 复制到剪贴板 ----
dlg._copy_btn.click()
pump(60)
clipboard = QApplication.clipboard()
copied = clipboard.text() if clipboard is not None else ""
check("G1 复制到剪贴板：内容与预览一致",
      copied == text and "已复制" in dlg._status.text(),
      f"status={dlg._status.text()!r} len={len(copied)}")

# ---- H. 另存为 ----
target = os.path.join(tmp, "out", "我的周报")     # 无扩展名 → 自动补 .md
_FakeFileDialog.result = target
_FakeFileDialog.calls = 0
dlg._save_btn.click()
pump(60)
saved = target + ".md"
check("H1 另存为：按下后调用文件对话框且自动补 .md",
      _FakeFileDialog.calls == 1 and os.path.isfile(saved),
      f"calls={_FakeFileDialog.calls} exists={os.path.isfile(saved)}")
if os.path.isfile(saved):
    raw = open(saved, "rb").read()
    check("H2 落盘内容 = 预览文本", raw.decode("utf-8") == text)
    check("H3 UTF-8 无 BOM、换行恰为 LF",
          not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw)
_FakeFileDialog.result = ""
dlg._save_btn.click()
pump(40)
check("H4 取消另存为：不写文件、状态可读",
      "取消" in dlg._status.text(), dlg._status.text())

# ---- I. 写入 vault ----
vault_target = os.path.join(vault, "FloatPulse", "报告",
                            plug.default_file_stem(today, today) + ".md")
dlg._vault_btn.click()
pump(60)
check("I1 写入 vault：目录与文件按约定布局生成",
      os.path.isfile(vault_target), vault_target)
if os.path.isfile(vault_target):
    check("I2 vault 文件内容 = 预览文本",
          open(vault_target, encoding="utf-8").read() == text)

# 已存在 → 先问再覆盖；选「否」必须原地不动
_FakeMessageBox.asked = 0
_FakeMessageBox.answer = _FakeMessageBox.StandardButton.No
dlg._text.setPlainText("被改动过的正文")
dlg._vault_btn.click()
pump(60)
kept = open(vault_target, encoding="utf-8").read() if os.path.isfile(vault_target) else ""
check("I3 目标已存在 → 弹确认；选「否」不覆盖",
      _FakeMessageBox.asked == 1 and kept == text
      and "取消" in dlg._status.text(),
      f"asked={_FakeMessageBox.asked} status={dlg._status.text()!r}")
_FakeMessageBox.answer = _FakeMessageBox.StandardButton.Yes
dlg._vault_btn.click()
pump(60)
check("I4 选「是」→ 覆盖为当前预览内容",
      open(vault_target, encoding="utf-8").read() == "被改动过的正文"
      and "已写入" in dlg._status.text(),
      dlg._status.text())

# vault 指向文件（非法）→ 只报错不崩（落盘失败被兜住）
bad_vault = os.path.join(tmp, "not_a_dir.txt")
with open(bad_vault, "w", encoding="utf-8") as f:
    f.write("x")
config.set("obsidian_vault_path", bad_vault)
config.save()
dlg_bad = real_dialog(PluginContext(logger=_logger, config=config.as_dict(),
                                    data=plugin_data), win)
pump(30)
_FakeMessageBox.answer = _FakeMessageBox.StandardButton.No
dlg_bad._radios["today"].setChecked(True)
pump(30)
dlg_bad._vault_btn.click()
pump(60)
check("I5 vault 指向文件 → 写入失败被兜住、不抛异常",
      "写入失败" in dlg_bad._status.text(), dlg_bad._status.text())

dlg.close()
dlg_bad.close()
ball.deleteLater()
win.close()
pump(100)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
