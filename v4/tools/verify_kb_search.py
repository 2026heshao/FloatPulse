# -*- coding: utf-8 -*-
"""站内搜索插件（kb-search）离屏端到端验证 + light/dark 真截图。

覆盖（每一步都走真实代码路径，不拿替身糊过去）：
  A 真实 plugins/ 目录被真加载器扫到：动作进注册表、热键 Ctrl+Alt+F 不与
    核心（K/S）及其它插件冲突；**capabilities 为空** → 写/改/网络三档
    能力必须全部被门禁拒掉（检索是只读功能）
  B 真实数据 → 真实索引：把任务/笔记/碎片/知识库/素材五类宿主数据喂进去，
    逐项验证「五类都能搜到」「词典外专有名词能搜到」「全角与英文能搜到」
    「多命中项排更前」「结果条数与 kind 正确」
  C 页面真身：注册进主窗口导航、show_plugin_page 切过去、去抖定时器生效、
    结果 HTML 带加粗着色、用户原文里的 < 被转义、空查询/无匹配两种空态、
    重建索引按钮走真回调
  D **结果跳转**（并入宿主全库搜索后新增的能力）：点结果标题 → 真跑
    anchorClicked 回调 → 切到对应页；知识库要**定位到那一段**
  E **Ctrl+K 两态**：插件启用 → 切到站内搜索页；插件停用 → 只提示不崩
    （宿主侧不做第二套 UI）
  F 视觉：页面在 light / dark 各截一张真图，且两图确实不同（主题生效）

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

from PyQt6.QtCore import Qt, QUrl                                   # noqa: E402
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
# 素材：并入宿主全库搜索后新增的第 5 类数据源（只索引文件名）
_dummy = os.path.join(tmp, "客户报价单.xlsx")
with open(_dummy, "w", encoding="utf-8") as f:
    f.write("dummy")
ASSET_ID = assets.add_asset(_dummy, "客户报价单.xlsx")

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
    "assets": lambda: [
        {"asset_id": a.asset_id, "original_name": a.original_name,
         "is_image": a.is_image, "size_bytes": a.size_bytes,
         "added_time": a.added_time}
        for a in assets.get_all_assets()],
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
# 3 段知识库 + 1 条任务 + 2 条笔记 + 1 条碎片 + 1 条素材 = 8
check(f"B3 五类数据都进了索引（{n_docs} 条）", n_docs == 8, f"n={n_docs}")


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
        ("task", "验收结论", "任务"),
        ("asset", "客户报价单", "素材")):
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
# D. 结果跳转（并入宿主全库搜索后新增的能力）
# ====================================================================
# 知识库那一段的编号：KB_PARAS 第 3 条「每周五下班前把本周的月报归档到知识库」
KB_TARGET_NUM = 3
hits = _run("月报 归档")
kb_hits = [i for i, h in enumerate(hits) if h.kind == "knowledge"]
check("D0 「月报 归档」命中知识库那一段", bool(kb_hits),
      f"{[(h.uid, h.kind) for h in hits]}")

if kb_hits:
    i = kb_hits[0]
    win._switch_page(0)                       # 先离开，确保是跳转把它带过去的
    pump(150)
    # 真跑锚点回调（等价于用户点了结果标题）
    page._on_anchor(QUrl(f"{plug.RESULT_SCHEME}:{i}"))
    pump(250)
    check("D1 点结果标题切到知识库页",
          win._stack.currentIndex() == NAV_PAGE_INDEX["knowledge"],
          f"idx={win._stack.currentIndex()}")
    kp = win._page_knowledge
    cur = kp._kb_list.currentItem()
    got = cur.data(Qt.ItemDataRole.UserRole) if cur is not None else None
    check(f"D2 知识库定位到第 {KB_TARGET_NUM} 段（选中项 = 那一段）",
          got == KB_TARGET_NUM - 1, f"current={got}")
    check("D3 定位时清空了知识库搜索框（否则那一段可能被过滤掉）",
          kp._kb_search.text() == "", repr(kp._kb_search.text()))

# 碎片：带关键词进去后列表应当被过滤（宿主 _on_search_jump 的既有能力）
frag_hits = [i for i, h in enumerate(_run("bm25-index")) if h.kind == "fragment"]
if frag_hits:
    win._switch_page(0)
    pump(150)
    page._on_anchor(QUrl(f"{plug.RESULT_SCHEME}:{frag_hits[0]}"))
    pump(250)
    check("D4 点碎片结果：切到碎片页并把关键词带进搜索框",
          win._stack.currentIndex() == NAV_PAGE_INDEX["fragments"]
          and win._page_fragments._frag_search.text() != "",
          f"kw={win._page_fragments._frag_search.text()!r}")

# 关键词必须是**字面命中的单项**，不能是整条查询（多词查询在目标面板
# 的子串过滤里匹配不到任何东西 → 切过去列表是空的）
multi = [(i, h) for i, h in enumerate(_run("月报 归档")) if h.kind == "note"]
check("D5 多词查询跳转时取字面命中的最长项，而不是整条查询",
      all(plug.pick_jump_keyword(h.matched, "月报 归档") != "月报 归档"
          for _i, h in multi) or not multi,
      f"{[(h.uid, h.matched[:3]) for _i, h in multi]}")

# 未知 kind / 越界下标：不能崩、不能跳到错误页面
before_idx = win._stack.currentIndex()
page._on_anchor(QUrl(f"{plug.RESULT_SCHEME}:99999"))
page._on_anchor(QUrl("http://example.com/not-ours"))
pump(120)
check("D6 越界下标 / 外来链接被忽略（不切页、不崩）",
      win._stack.currentIndex() == before_idx)
check("D7 宿主 show_search_result 对未知 kind 返回 False（不静默落到 0 号页）",
      win.show_search_result("不存在的来源", "关键词") is False)

# ====================================================================
# E. Ctrl+K 两态（宿主侧不做第二套 UI）
# ====================================================================
win.show_plugin_page(PAGE_KEY)
pump(200)
win._switch_page(0)
pump(150)
win._open_search_entry()
pump(250)
check("E1 插件启用时 Ctrl+K 切到站内搜索页",
      win._stack.currentIndex() == target_idx,
      f"idx={win._stack.currentIndex()} target={target_idx}")

# 停用插件页（等价于用户在插件中心点「停用」）→ Ctrl+K 只提示，不弹对话框
_toasts = []
_real_toast = win.show_toast


def _fake_toast(text, ms=2800):
    _toasts.append(text)


win.show_toast = _fake_toast
win.unregister_plugin_page(PAGE_KEY)
pump(150)
win._switch_page(0)
pump(150)
try:
    win._open_search_entry()
    pump(200)
    crashed = False
except Exception as exc:                      # noqa: BLE001
    crashed = True
    print(f"    Ctrl+K 抛异常：{exc!r}", flush=True)
check("E2 插件停用后 Ctrl+K 不抛异常", crashed is False)
check("E3 插件停用后 Ctrl+K 给出提示（否则用户以为程序坏了）",
      len(_toasts) == 1 and "插件中心" in _toasts[0], f"{_toasts}")
check("E4 插件停用后 Ctrl+K 不切页（停用即停用，不做第二套 UI）",
      win._stack.currentIndex() == 0, f"idx={win._stack.currentIndex()}")
win.show_toast = _real_toast

# 重新启用（页面上插件中心走的 rebuild 路径），恢复后续截图所需的页面
rebuild = getattr(win, "rebuild_plugin_page", None)
if callable(rebuild):
    rebuild(PLUGIN_ID)
pump(300)
page = win._stack.widget(NAV_PAGE_INDEX[PAGE_KEY])
check("E5 重新启用后插件页回来了",
      win.show_plugin_page(PAGE_KEY) is True and page is not None,
      f"{type(page).__name__ if page else None}")

# ====================================================================
# F. 双主题（真截图 + 富文本强调色跟随）
# ====================================================================
win.show_plugin_page(PAGE_KEY)
pump(400)
page = win._stack.widget(NAV_PAGE_INDEX[PAGE_KEY])
page._input.setText("月报 归档")
page._run_search()
pump(120)
sizes = {}
html_by_theme = {}
for theme in ("light", "dark"):
    cm.set("theme", theme)
    # ⚠ 走 apply_external_theme（设置面板的真实路径）：它会广播
    # theme_changed，插件页据此换强调色。直接调 _apply_theme() 只换 QSS，
    # QTextBrowser 里的行内颜色不会变——这正是本条要钉的东西。
    win.apply_external_theme(theme)
    pump(300)
    page._input.setText("月报 归档")
    page._run_search()
    pump(120)
    html_by_theme[theme] = page._view.toHtml()
    p = os.path.join(OUT_DIR, f"kb-search-page-{theme}.png")
    img = win.grab()
    img.save(p)
    sizes[theme] = os.path.getsize(p) if os.path.isfile(p) else 0
    print(f"    saved {p} ({sizes[theme]} bytes)", flush=True)

check("F1 两张截图都落盘且非空",
      all(v > 20000 for v in sizes.values()), f"{sizes}")

# 主题真的生效：两张图的字节不同（不是同一张）
with open(os.path.join(OUT_DIR, "kb-search-page-light.png"), "rb") as f:
    light_bytes = f.read()
with open(os.path.join(OUT_DIR, "kb-search-page-dark.png"), "rb") as f:
    dark_bytes = f.read()
check("F2 light / dark 两图不同（主题确实生效）",
      light_bytes != dark_bytes)

# 富文本强调色必须跟随主题（QSS 管不到 QTextBrowser 内部的行内样式）。
# 修复前这里写死 #0a7d7b：深色主题下结果标题对比度只有 3.05:1，几乎看不见。
# ⚠ toHtml() 会把颜色统一成**小写**（#6FFFE9 → #6fffe9），比较前要归一
def _has_accent(html, accent):
    return accent.lower() in (html or "").lower()


check("F3 切到 dark 后插件页强调色换成 dark 主题色",
      page._accent == plug.accent_for("dark")
      and _has_accent(html_by_theme["dark"], plug.accent_for("dark")),
      f"accent={page._accent}")
check("F4 light / dark 的强调色不相同，且各自用在对应主题的渲染里",
      plug.accent_for("light") != plug.accent_for("dark")
      and _has_accent(html_by_theme["light"], plug.accent_for("light")),
      f"light={plug.accent_for('light')} dark={plug.accent_for('dark')}")


def _lin(v):
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _contrast(c1, c2):
    def _lum(hx):
        hx = hx.lstrip("#")
        if len(hx) == 3:
            hx = "".join(c * 2 for c in hx)
        r, g, b = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    a, b = _lum(c1), _lum(c2)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


from src.theme import get_colors                                # noqa: E402

ratios = {}
for theme in ("light", "dark"):
    accent = plug.accent_for(theme)
    ratios[theme] = round(_contrast(accent, get_colors(theme)["card_bg_solid"]), 2)
check("F5 两个主题下强调色对卡片底色的 WCAG 对比度都 ≥ 4.5",
      all(r >= 4.5 for r in ratios.values()), f"{ratios}")

# 宿主 QSS 覆盖插件页用到的样式钩子
qss = win._container.styleSheet()
check("F6 宿主 QSS 覆盖插件页样式钩子",
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
