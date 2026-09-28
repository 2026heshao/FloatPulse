# -*- coding: utf-8 -*-
"""站内搜索插件（kb-search）离屏端到端验证 + light/dark 真截图。

覆盖（每一步都走真实代码路径，不拿替身糊过去）：
  A 真实 plugins/ 目录被真加载器扫到：动作进注册表、热键 Ctrl+Alt+F 不与
    核心（K/S）及其它插件冲突；**capabilities 为空** → 写/改/网络三档
    能力必须全部被门禁拒掉（检索是只读功能）
  B 真实数据 → 真实索引：把任务/笔记/碎片/知识库四类宿主数据喂进去，
    逐项验证「四类都能搜到」「词典外专有名词能搜到」「全角与英文能搜到」
    「多命中项排更前」「结果条数与 kind 正确」
  C 页面真身：注册进主窗口导航、show_plugin_page 切过去、去抖定时器生效、
    结果 HTML 带 <span class="hit"> 高亮、用户原文里的 < 被转义、
    空查询/无匹配两种空态、重建索引按钮走真回调
  D 视觉：页面在 light / dark 各截一张真图，且两图确实不同（主题生效）

跑法：python tools/run_gui_check.py tools/verify_kb_search.py
产物：build/shots/kb-search-page-{light,dark}.png
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time

# ★ 必须在 import PyQt6 之前设定，否则进程硬崩
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # v4/
ROOT = os.path.dirname(BASE)                                        # 仓库根
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication, QTextBrowser                # noqa: E402
from PyQt6.QtGui import QFontDatabase                                # noqa: E402

from src.config import ConfigManager                                 # noqa: E402
from src.main_window import MainWindow, NAV_PAGE_INDEX               # noqa: E402
from src.plugin_api import (ActionRegistry, PluginContext,           # noqa: E402
                            PluginData)
from src.plugin_loader import PluginLoader                           # noqa: E402
from src.docx_manager import DocxManager                             # noqa: E402
from src.task_manager import TaskManager                             # noqa: E402
from src.note_manager import NoteManager                             # noqa: E402
from src.fragment_manager import FragmentManager                     # noqa: E402
from src.nav_manager import NavManager                               # noqa: E402
from src.temp_asset_manager import TempAssetManager                  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor                   # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

OUT_DIR = os.path.join(ROOT, "build", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

PLUGIN_ID = "kb-search"
PAGE_KEY = f"plugin:{PLUGIN_ID}"
ACTION_OPEN = f"{PLUGIN_ID}.open"

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    tag = "[OK]  " if cond else "[FAIL]"
    suffix = f"  -> {detail}" if (detail and not cond) else ""
    print(f"{tag} {name}{suffix}", flush=True)


def pump(ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
logger = logging.getLogger("fp_verify_kb_search")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.handlers.clear()
logger.addHandler(_Capture(_records))


# ====================================================================
# 搭环境
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_kbs_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)

cm = ConfigManager(os.path.join(data_dir, "config.json"))
cm.set("plugins_enabled", True)

# ⚠ DocxManager 不会凭空创建 docx（load() 只报「找不到文件」，append 返回 -1），
# 所以这里先用 python-docx 落一个真文件出来——这也是用户真实的使用姿势
# （他本来就是拿 Word/WPS 编辑这个文件）
from docx import Document                                          # noqa: E402

KB_PATH = os.path.join(tmp, "知识库.docx")
KB_PARAS = [
    "报价流程先问清楼层与平方数，再按面积段给单价",       # 含 报价 / 流程
    "项目代号 星尘三号 的内部复盘纪要放在共享盘",           # 词典外专有名词
    "每周五下班前把本周的月报归档到知识库",                 # 含 月报 / 归档 / 周报
]
_seed = Document()
for _t in KB_PARAS:
    _seed.add_paragraph(_t)
_seed.save(KB_PATH)

docx = DocxManager(KB_PATH, os.path.join(data_dir, "docx_meta.json"))
docx.load()

tasks = TaskManager(os.path.join(data_dir, "schedule.json"))
notes = NoteManager(os.path.join(data_dir, "notes.json"))
frags = FragmentManager(os.path.join(data_dir, "fragments.json"))
nav = NavManager(os.path.join(data_dir, "nav.json"))
assets = TempAssetManager(tmp)
clip = ClipboardMonitor(frags, cm)

# ---- 四类数据各埋一条可辨识的内容 ----
TASK_ID = tasks.add_task("撰写周报", "带上本周的交付数据与验收结论", "2026-09-30")
NOTE_ID = notes.add_note("报价单里必须写明服务范围与结算方式", "客户报价单模板")
FRAG_TEXT = "https://example.com/bm25-index 参考资料"
frags.add_clipboard_text(FRAG_TEXT, "剪贴板")
# 用户原文里带尖括号：渲染必须转义
XSS_ID = notes.add_note("数据库索引 <script>alert(1)</script> 的复现步骤",
                        "代码片段")

win = MainWindow(tasks, notes, frags, docx, cm, clip,
                 temp_asset_manager=assets, nav_manager=nav)
win.resize(1280, 740)
win.show()
pump(300)

# ---- 真实接线（与 knowledge_ball 的装配方式一致）----
def _add_task(title, note, deadline):
    tid = tasks.add_task(title, note, deadline)
    win.refresh_tasks()
    return tid


def _update_task(task_id, title, note, deadline):
    tasks.update_task(task_id, title, note, deadline)
    return True


registry = ActionRegistry(
    logger=logger,
    reserved_hotkeys=(cm.get("quick_capture_hotkey", "Ctrl+Alt+K"),
                      cm.get("screenshot_hotkey", "Ctrl+Alt+S")))
plugin_data = PluginData(providers={
    "tasks": lambda: [t.to_dict() for t in tasks.get_all_tasks()],
    "fragments": lambda: [f.to_dict() for f in frags.get_all_fragments()],
    "notes": lambda: [n.to_dict() for n in notes.get_all_notes()],
    "knowledge": lambda: [
        {"num": i + 1, "text": p.text, "preview": p.preview, "hash": p.hash}
        for i, p in enumerate(docx.get_paragraphs())],
})
ctx = PluginContext(
    logger=logger,
    config=cm.as_dict(),
    show_toast=win.show_toast,
    open_main_window=lambda: None,
    open_card_mode=lambda mode: True,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    write_providers={"task": _add_task},
    manage_providers={"update_task": _update_task},
)
loader = PluginLoader(registry, ctx, plugins_dir=os.path.join(ROOT, "plugins"))
win.set_plugin_loader(loader)

# ====================================================================
# A. 真实加载 + 只读契约
# ====================================================================
loaded = loader.load_all()
pump(300)
ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 kb-search", PLUGIN_ID in ids, f"{ids}")

act = registry.get(ACTION_OPEN)
check("A2 打开动作已进注册表且挂菜单",
      act is not None and act.menu is True)
check("A3 热键 Ctrl+Alt+F 有效且未冲突",
      act is not None and act.hotkey == "Ctrl+Alt+F"
      and ACTION_OPEN in [a.id for a in registry.hotkey_actions()],
      f"{getattr(act, 'hotkey', None)}")

sub_ctx = registry.context_of(ACTION_OPEN)
check("A4 注册表记住了插件自己的上下文",
      sub_ctx is not None and sub_ctx.plugin_id == PLUGIN_ID)

# capabilities 为空 → 三档能力全部被门禁拒掉（只读！）
check("A5 未声明 write：add_task 被拒",
      sub_ctx.write.add_task("（越权探针）", "", "") == 0)
check("A6 未声明 manage：update_task 被拒",
      sub_ctx.manage.update_task(TASK_ID, title="不该改到") is False)
check("A7 未声明 network：http_post_json_async 被拒",
      sub_ctx.http_post_json_async("https://example.com", {}, "{}",
                                   lambda **_k: None) is False)
check("A8 越权尝试有 warning 日志（不是静默失败）",
      any("write" in r.getMessage() or "manage" in r.getMessage()
          or "network" in r.getMessage() for r in _records),
      f"{[r.getMessage()[:40] for r in _records][:3]}")

# ====================================================================
# B. 页面真身 + 真实索引
# ====================================================================
plug = sys.modules.get("floatpulse_plugin_" + PLUGIN_ID.replace("-", "_"))
if plug is None:
    cands = [k for k in sys.modules if "kb_search" in k and k.endswith("plugin")]
    plug = sys.modules.get(cands[0]) if cands else None
check("B0 插件模块可从 sys.modules 取到", plug is not None,
      f"{[k for k in sys.modules if 'kb_search' in k]}")

page = plug.SearchPage(sub_ctx)
win.register_plugin_page(PAGE_KEY, "🔍 站内搜索", page)
pump(200)

check("B1 页面注册进主窗口（物理索引 10+）",
      win.show_plugin_page(PAGE_KEY) is True)
pump(400)

stat = page._stat.text()
check("B2 切入页面即自动重建索引，状态行给出条数",
      "已索引" in stat and "检索项" in stat, stat)

n_docs = page._docs
# 3 段知识库 + 1 条任务 + 2 条笔记 + 1 条碎片 = 7
check(f"B3 四类数据都进了索引（{n_docs} 条）", n_docs == 7, f"n={n_docs}")


def _run(q):
    page._input.setText(q)
    page._run_search()
    pump(30)
    return page._index.search(q, top_n=plug.MAX_RESULTS)


# 四类数据源各自可命中
for kind, query, hint in (
        ("knowledge", "报价流程", "知识库"),
        ("note", "结算方式", "笔记"),
        ("fragment", "bm25-index", "碎片"),
        ("task", "验收结论", "任务")):
    hits = _run(query)
    kinds = {h.kind for h in hits}
    check(f"B4 搜「{query}」命中 {hint}（kind={kind}）",
          kind in kinds, f"{kinds}")

# 词典外的自造专有名词（只能靠字符 bigram 召回）
hits = _run("星尘三号")
check("B5 词典外专有名词「星尘三号」可召回",
      bool(hits) and all("星尘三号" in h.text for h in hits),
      f"{[h.uid for h in hits]}")
hits = _run("星尘")
check("B6 只查词组的一半也能召回", bool(hits), f"{[h.uid for h in hits]}")

# 全角输入（从 Word / 微信粘进来的形态）
hits_half = _run("2026")
hits_full = _run("２０２６")
check("B7 全角数字等价于半角（归一化生效）",
      bool(hits_half) and [h.uid for h in hits_full] == [h.uid for h in hits_half],
      f"half={[h.uid for h in hits_half]} full={[h.uid for h in hits_full]}")

# 英文大写同样归一
check("B8 英文大小写不敏感",
      [h.uid for h in _run("BM25")] == [h.uid for h in _run("bm25")])

# 多命中项排更前
hits = _run("月报 归档")
check("B9 命中更多关键词的文档排第一",
      bool(hits) and "月报" in hits[0].text and "归档" in hits[0].text,
      f"{[(h.uid, round(h.score, 2)) for h in hits]}")

# 无匹配 → 空
check("B10 完全不相干的查询返回空", _run("企鹅南极洲冰川") == [])

# 结果按分数降序且分数为正
hits = _run("报价")
check("B11 结果按分数降序、分数为正",
      bool(hits) and all(h.score > 0 for h in hits)
      and [h.score for h in hits] == sorted([h.score for h in hits],
                                            reverse=True))

# ====================================================================
# C. UI 真身
# ====================================================================
page._input.setText("月报 归档")
page._run_search()
pump(60)
html_out = page._view.toHtml()
check("C1 结果是 QTextBrowser（能渲染 HTML 子集）",
      isinstance(page._view, QTextBrowser))
# ⚠ 不能断言 class="hit"：QTextBrowser 会把 class 内联成 style
check("C2 渲染出的命中处带加粗着色（高亮生效）",
      "font-weight:700" in html_out or "font-weight: 700" in html_out,
      html_out[:160])
check("C3 命中的每一条都带数据源标签（标签与实际 kind 对得上）",
      bool(_run("月报 归档")) and all(
          plug.KIND_LABEL[h.kind] in html_out for h in _run("月报 归档")),
      f"{[(h.uid, h.kind) for h in _run('月报 归档')]}")
check("C4 有结果时隐藏空态提示", page._empty.isVisible() is False)

# 用户原文里的尖括号必须被转义（否则被 QTextBrowser 当标签吃掉）
page._input.setText("复现步骤")
page._run_search()
pump(60)
xss_html = page._view.toHtml()
# ⚠ 片段是从命中点向两侧取窗口，`&lt;script` 不一定完整落在窗口里；
# 判据改成「有转义产物」+「没有任何未转义的标签开头」
check("C5 用户原文里的 < 被转义（防被当标签吃掉）",
      "&lt;" in xss_html and "<script" not in xss_html,
      xss_html[-260:])

# 无匹配 → 提示文案 + 空态可见
page._input.setText("企鹅南极洲冰川")
page._run_search()
pump(60)
check("C6 无匹配时文案说明「没有匹配」且空态可见",
      "没有匹配" in page._stat.text() and page._empty.isVisible() is True,
      page._stat.text())

# 空查询 → 清空结果 + 空态
page._input.setText("")
page._run_search()
pump(60)
# ⚠ toHtml() 永远返回 HTML 骨架，不能拿它判空；看纯文本
check("C7 清空输入后结果区清空、空态回来",
      page._view.toPlainText().strip() == "" and page._empty.isVisible() is True,
      repr(page._view.toPlainText()[:60]))

# 去抖：textChanged 起定时器，且定时器是单次
page._input.setText("价")
pump(20)
check("C8 输入触发去抖定时器（不是每敲一个字就搜）",
      page._debounce.isActive() is True
      and page._debounce.isSingleShot() is True
      and page._debounce.interval() == plug.SEARCH_DEBOUNCE_MS,
      f"active={page._debounce.isActive()}")

# 重建索引按钮走真回调
before_docs = page._docs
page._rebuild_btn.click()
pump(150)
check("C9「重建索引」按钮走真回调，索引条数不变（数据没动）",
      page._docs == before_docs and "已索引" in page._stat.text(),
      f"{before_docs} -> {page._docs}")

# 数据新增后重建能看到新内容（懒建索引的正确性）
notes.add_note("这是一条用来验证重建的独特词条 蓝鲸七号", "临时新增")
page._rebuild_btn.click()
pump(150)
check("C10 新增数据后重建索引即可搜到（懒建索引有效）",
      page._docs == before_docs + 1 and bool(_run("蓝鲸七号")),
      f"docs={page._docs}")
page._input.setText("")

# 动作真跑一遍：OpenSearchAction.run → 必须在事件循环里延后执行且切到本页
target_idx = NAV_PAGE_INDEX.get(PAGE_KEY)
win._switch_page(0)
pump(150)
check("C11 动作执行前确实不在插件页", win._stack.currentIndex() != target_idx,
      f"idx={win._stack.currentIndex()}")
act.run(sub_ctx)
pump(300)
check("C12 打开动作延后执行并真的切到插件页（Ctrl+Alt+F 的实际路径）",
      win._stack.currentIndex() == target_idx,
      f"idx={win._stack.currentIndex()} target={target_idx}")

# ====================================================================
# D. 双主题真截图
# ====================================================================
page._input.setText("月报 归档")
page._run_search()
win.show_plugin_page(PAGE_KEY)
pump(400)
sizes = {}
for theme in ("light", "dark"):
    cm.set("theme", theme)
    win._theme = theme
    win._apply_theme()
    pump(300)
    page._input.setText("月报 归档")
    page._run_search()
    pump(120)
    p = os.path.join(OUT_DIR, f"kb-search-page-{theme}.png")
    img = win.grab()
    img.save(p)
    sizes[theme] = os.path.getsize(p) if os.path.isfile(p) else 0
    print(f"    saved {p} ({sizes[theme]} bytes)", flush=True)

check("D1 两张截图都落盘且非空",
      all(v > 20000 for v in sizes.values()), f"{sizes}")

# 主题真的生效：两张图的字节不同（不是同一张）
with open(os.path.join(OUT_DIR, "kb-search-page-light.png"), "rb") as f:
    light_bytes = f.read()
with open(os.path.join(OUT_DIR, "kb-search-page-dark.png"), "rb") as f:
    dark_bytes = f.read()
check("D2 light / dark 两图不同（主题确实生效）",
      light_bytes != dark_bytes)

# 宿主 QSS 覆盖插件页用到的样式钩子
qss = win._container.styleSheet()
check("D3 宿主 QSS 覆盖插件页样式钩子",
      "pluginPage" in qss and "pluginEmptyHint" in qss
      and "secondaryBtn" in qss, f"qss_len={len(qss)}")

# ====================================================================
win.close()
pump(150)
shutil.rmtree(tmp, ignore_errors=True)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
