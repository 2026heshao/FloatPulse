# -*- coding: utf-8 -*-
"""AI 文本工坊离屏端到端验证（v1.4 零配置形态）：真加载器 → 真主窗口
侧栏页面 → 假网络桥。AI 后端唯一来源 = 设置页「AI 总配置」。

覆盖（每一步都走真实代码路径，网络用替身）：
  A 插件被真实扫到：动作进注册表、热键合法无冲突、ai 能力已声明
  B 纯函数层抽查（全量覆盖在 tests/test_text_workshop.py）
  E 页面注册：索引 ≥10、侧栏按钮存在；热键动作 → show_plugin_page 真切页
    且 showEvent 消费 PENDING_HOTKEY 标记自动带入剪贴板
  F 未接入分支：动作被拦 + 提示去设置页（不发注定失败的请求）
  G 接入分支：勾选后 is_attached 立即翻转（不重建 ctx）；请求走总配置
    地址 / 模型 / key（本插件已无任何私有后端配置代码）
  H 「提取待办 → 转任务」全链路：解析行动项 → ctx.write.add_task 落库
  I 错误路径：401 提示 key、busy 复位；未声明 write 时落库按钮隐藏
  L 落库出口：存碎片（来源=插件:ai-text-workshop）/ 存笔记（标题取首行）
  P 隐私边界：请求体只含原文+指令，绝无应用内任务/碎片/笔记数据
  D light / dark 双主题真截图（页面嵌进真实 MainWindow）→ build/shots/

跑法：python tools/run_gui_check.py tools/verify_text_workshop.py
"""
import json
import logging
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)

from PyQt6.QtCore import QTimer                    # noqa: E402
from PyQt6.QtWidgets import QApplication           # noqa: E402
from PyQt6.QtGui import QFontDatabase              # noqa: E402

from src.config import ConfigManager               # noqa: E402
from src.docx_manager import DocxManager           # noqa: E402
from src.task_manager import TaskManager           # noqa: E402
from src.note_manager import NoteManager           # noqa: E402
from src.fragment_manager import FragmentManager   # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow             # noqa: E402
from src.plugin_api import (                       # noqa: E402
    ActionRegistry, PluginContext, PluginData,
)
from src.plugin_loader import PluginLoader         # noqa: E402
from src.ai_server import AI_SERVER                # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setApplicationName("verify_text_workshop")
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

_results = []
PLUGIN_ID = "ai-text-workshop"
ACTION_ID = f"{PLUGIN_ID}.open"
PAGE_KEY = f"plugin:{PLUGIN_ID}"


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


class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_logger = logging.getLogger("fp_verify_text_workshop")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_logger.handlers.clear()
_logger.addHandler(_Capture([]))

tmp = tempfile.mkdtemp(prefix="fp_verify_tw_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)

config = ConfigManager(os.path.join(data_dir, "config.json"))
docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                       os.path.join(data_dir, "docx_meta.json"))
docx_mgr.load()
task_mgr = TaskManager(os.path.join(data_dir, "schedule.json"))
note_mgr = NoteManager(os.path.join(data_dir, "notes.json"))
frag_mgr = FragmentManager(os.path.join(data_dir, "fragments.json"))
clip = ClipboardMonitor(frag_mgr, config)
temp_mgr = TempAssetManager(tmp)

task_mgr.add_task("已完成的任务", "", "")   # 隐私检查的探针标题

_mw = sys.modules["src.main_window"]
if "plugins" not in _mw.NAV_PAGE_TITLES:
    _mw.NAV_PAGE_TITLES["plugins"] = "🔌  插件中心"
    _mw.NAV_PAGE_INDEX.setdefault("plugins", 9)

win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr)
win.resize(1000, 900)
win.show()
pump(200)

plugins_dir = os.path.join(ROOT, "plugins")

bridge_calls = []
bridge_mode = {"ok": True, "content": ""}


def fake_bridge(url, headers, body, timeout, on_done):
    bridge_calls.append({"url": url, "headers": dict(headers or {}),
                         "body": body})
    if bridge_mode["ok"]:
        payload = json.dumps({"choices": [
            {"message": {"content": bridge_mode["content"]}}]})
    else:
        payload = '{"error": {"message": "bad key"}}'
    QTimer.singleShot(30, lambda: on_done({
        "ok": bridge_mode["ok"], "status": 200 if bridge_mode["ok"] else 401,
        "body": payload, "error": "" if bridge_mode["ok"] else "HTTP 401",
        "url": url}))
    return True


def _make_ctx(window):
    write_providers = {
        "fragment": lambda content, source: frag_mgr.add_clipboard_text(
            content, source=source),
        "task": lambda title, note, deadline: task_mgr.add_task(
            title, note, deadline),
        "note": lambda title, content: note_mgr.add_note(content, title),
    }

    def _is_attached(plugin_id):
        return str(plugin_id or "") in (config.get("ai_plugins", []) or [])

    def _params():
        return {
            "mode": str(config.get("ai_backend_mode", "cloud") or "cloud"),
            "base_url": str(config.get("ai_cloud_base_url", "") or "").strip(),
            "api_key": str(config.get("ai_cloud_api_key", "") or ""),
            "model": str(config.get("ai_cloud_model", "") or "").strip(),
            "local_port": int(config.get("ai_local_port", 8095) or 8095),
            "local_ready": AI_SERVER.status == "ready",
            "local_status": AI_SERVER.status,
            "local_detail": AI_SERVER.detail,
        }

    ctx = PluginContext(
        logger=_logger, config=config.as_dict(),
        show_toast=window.show_toast,
        open_main_window=lambda: None,
        open_card_mode=lambda mode: True,
        data=PluginData(providers={
            "tasks": lambda: [t.to_dict() for t in task_mgr.get_all_tasks()],
            "fragments": lambda: [f.to_dict()
                                  for f in frag_mgr.get_all_fragments()],
            "notes": lambda: [n.to_dict() for n in note_mgr.get_all_notes()],
            "pomodoro": lambda: {"state": "idle"},
        }),
        data_dir_base=os.path.join(data_dir, "plugins"),
        parent_window=lambda w=window: w,
        http_post_async=fake_bridge,
        write_providers=write_providers,
        ai_providers={
            "is_attached": _is_attached, "params": _params,
            "add_listener": AI_SERVER.add_listener,
            "remove_listener": AI_SERVER.remove_listener,
            "stop_local": lambda: (AI_SERVER.stop(), True)[1],
        },
    )
    # 与 loader 真实口径一致：按 manifest 能力派生
    return ctx.for_plugin(
        PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID),
        ["network", "write", "ai"])


# ====================================================================
# A. 真实加载器扫到真插件
# ====================================================================
registry = ActionRegistry(logger=_logger, reserved_hotkeys=("Ctrl+Alt+K",
                                                            "Ctrl+Alt+S"))
ctx = _make_ctx(win)
loader = PluginLoader(registry, ctx, plugins_dir=plugins_dir)
loaded = loader.load_all()
win.set_plugin_loader(loader)
ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 ai-text-workshop", PLUGIN_ID in ids, f"{ids}")

act = registry.get(ACTION_ID)
check("A2 动作已进注册表（页面插件不挂右键菜单）",
      act is not None and act.menu is False)
check("A3 热键 Ctrl+Alt+T 生效且无冲突",
      act is not None and act.hotkey == "Ctrl+Alt+T"
      and not registry.hotkey_blocked_by_reserved("Ctrl+Alt+T")
      and ACTION_ID in [a.id for a in registry.hotkey_actions()]
      and ACTION_ID not in
      [i for ids_ in registry.entry_conflicts().values() for i in ids_])

sub_ctx = registry.context_of(ACTION_ID)
check("A4 独立 ctx：plugin_id + ai 能力",
      sub_ctx is not None and sub_ctx.plugin_id == PLUGIN_ID
      and sub_ctx.has_capability("ai") and sub_ctx.ai.enabled())
plug = sys.modules.get("floatpulse_plugin_ai_text_workshop")
check("A5 插件模块可从 sys.modules 取到", plug is not None)

# ====================================================================
# B. 纯函数层抽查（全量在 tests/test_text_workshop.py）
# ====================================================================
check("B1 normalize_source 空/截断口径",
      plug.normalize_source("  \n ") is None
      and plug.normalize_source("a" * 100, cap=10).startswith("a" * 10))
check("B2 parse_task_lines 三种前缀 + 去重",
      plug.parse_task_lines("- [ ] 甲\n1. 乙\n- 甲") == ["甲", "乙"])
check("B3 build_request 认 ctx.ai 参数（云端 URL + 鉴权头）",
      plug.build_request({"mode": "cloud", "base_url": "https://x/v1",
                          "api_key": "k", "model": "m"},
                         "指令", "原文")[0] == "https://x/v1/chat/completions")
check("B4 插件已无任何私有后端配置符号",
      all(not hasattr(plug, gone) for gone in (
          "BACKEND_PRESETS", "DEFAULT_CONFIG", "load_config", "save_config",
          "backend_configured", "LocalServerManager")))

# ====================================================================
# J. data_dir 与 parent_window
# ====================================================================
dd = sub_ctx.data_dir
check("J1 data_dir 首次访问自动创建",
      bool(dd) and os.path.isdir(dd) and os.path.basename(dd) == PLUGIN_ID, dd)
check("J2 parent_window 返回真实主窗口", sub_ctx.parent_window() is win)

# ====================================================================
# E. 页面注册 + 热键触发切页 + showEvent 带入剪贴板
# ====================================================================
CLIP_TEXT = "客户电话 13800000000；下周三上午十点开评审会；记得准备 Q3 预算初稿"
_app.clipboard().setText(CLIP_TEXT)

page = plug.AiWorkshopPage(sub_ctx)
idx = win.register_plugin_page(PAGE_KEY, "文本工坊", page)
check("E1 页面注册：索引在插件页区（≥10）+ 侧栏按钮存在",
      idx >= 10 and win._nav_btns.get(PAGE_KEY) is not None, f"idx={idx}")

plug.PENDING_HOTKEY["flag"] = True
check("E2 动作可触发", registry.trigger(ACTION_ID))
pump(120)
check("E3 热键动作真切到工坊页",
      win._stack.currentIndex() == idx,
      f"cur={win._stack.currentIndex()} want={idx}")
check("E4 showEvent 消费标记：原文自动带入剪贴板",
      page._src_edit.toPlainText().strip() == CLIP_TEXT
      and plug.PENDING_HOTKEY["flag"] is False)

# ====================================================================
# F. 未接入分支：动作被拦 + 引导去设置页
# ====================================================================
check("F1 页面真身：无任何后端配置入口（零配置形态）",
      not hasattr(page, "_settings_card") and not hasattr(page, "_settings_btn")
      and page._ai_hint.isVisibleTo(page))
check("F2 初始态：无结果禁复制、转任务禁用",
      page._copy_btn.isEnabled() is False and page._tasks_btn.isEnabled() is False)
calls_before = len(bridge_calls)
page._run_action("summarize", "总结要点")
pump(120)
check("F3 未接入 AI：请求被拦 + 提示去设置页",
      len(bridge_calls) == calls_before
      and "尚未接入" in page._status.text(), page._status.text())

# ====================================================================
# G. 接入分支：设置页勾选（模拟 changed 落盘）→ 请求走总配置
# ====================================================================
config.set("ai_plugins", [PLUGIN_ID])
config.save()
pump(30)
check("G1 不重建 ctx：is_attached 立即翻转为 True",
      sub_ctx.ai.is_attached() is True)
config.set("ai_cloud_base_url", "https://central.example/v1")
config.set("ai_cloud_api_key", "sk-central")
config.set("ai_cloud_model", "central-model")
config.save()
bridge_mode["ok"] = True
bridge_mode["content"] = "要点一\n要点二"
page._run_action("summarize", "把待处理文本总结成要点")
pump(250)
call = bridge_calls[-1] if bridge_calls else {}
body_str = json.dumps(call.get("body") or {}, ensure_ascii=False)
check("G2 请求走总配置地址与模型（实时读取，无需重建）",
      call.get("url", "").startswith(
          "https://central.example/v1/chat/completions")
      and (call.get("body") or {}).get("model") == "central-model"
      and call.get("headers", {}).get("Authorization") == "Bearer sk-central",
      call.get("url"))
check("G3 消息结构 system+user，user 含指令与原文",
      [m["role"] for m in (call.get("body") or {}).get("messages", [])]
      == ["system", "user"]
      and "总结成要点" in body_str and CLIP_TEXT[:12] in body_str)
check("G4 结果落到结果框、复制按钮解锁",
      page._result_edit.toPlainText() == "要点一\n要点二"
      and page._copy_btn.isEnabled())
check("P1 请求体只含原文与指令，无宿主任务标题",
      "已完成的任务" not in body_str)

# ====================================================================
# H. 提取待办 → 转任务 全链路
# ====================================================================
TASKS_REPLY = "- [ ] 给客户回邮件\n- [ ] 准备 Q3 预算初稿\n- [ ] 预约会议室"
bridge_mode["content"] = TASKS_REPLY
tasks_before = {t.title for t in task_mgr.get_all_tasks()}
page._run_action("extract_tasks", "提取指令")
pump(250)
check("H1 提取待办：识别 3 条、转任务按钮解锁",
      page._tasks_btn.isEnabled()
      and "3" in page._status.text(), page._status.text())
page._do_tasks()
pump(60)
added = {t.title for t in task_mgr.get_all_tasks()} - tasks_before
check("H2 转任务：三条行动项真实落库",
      {"给客户回邮件", "准备 Q3 预算初稿", "预约会议室"} <= added,
      f"added={added}")
check("H3 转任务后按钮禁用（防重复写入）",
      page._tasks_btn.isEnabled() is False)

# ====================================================================
# L. 存为碎片 / 存为笔记
# ====================================================================
frag_before = len(frag_mgr.get_all_fragments())
note_before = len(note_mgr.get_all_notes())
page._result_edit.setPlainText("# 本周小结\n做了三件事")
page._do_fragment()
page._do_note()
pump(60)
frags = frag_mgr.get_all_fragments()
notes = note_mgr.get_all_notes()
check("L1 存为碎片：数量 +1 且来源可追溯",
      len(frags) == frag_before + 1
      and (frags[-1].source or "").startswith("插件:"),
      getattr(frags[-1], "source", None))
check("L2 存为笔记：数量 +1 且标题取结果首行",
      len(notes) == note_before + 1
      and notes[-1].title == "本周小结", notes[-1].title)

# ====================================================================
# I. 错误路径：401 提示 + busy 复位 + 未声明 write 隐藏按钮
# ====================================================================
bridge_mode["ok"] = False
page._run_action("translate_zh", "翻译指令")
pump(250)
check("I1 401 时状态行提示 key 问题（指引到设置页）",
      "key" in page._status.text() and "设置" in page._status.text(),
      page._status.text())
check("I2 busy 复位：动作按钮恢复可用",
      all(b.isEnabled() for b in page._action_btns.values()))

_ctx_nowrite = PluginContext(
    logger=_logger, config=config.as_dict(),
    show_toast=lambda *a, **k: None,
    data=PluginData(providers={}),
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    http_post_async=fake_bridge,
).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), ["network"])
page_nw = plug.AiWorkshopPage(_ctx_nowrite)
check("I3 未声明 write：碎片/笔记/转任务按钮全部隐藏",
      page_nw._frag_btn.isHidden() and page_nw._note_btn.isHidden()
      and page_nw._tasks_btn.isHidden())

# ====================================================================
# D. light / dark 双主题真截图（接入态页面）→ build/shots/
# ====================================================================
shots = os.path.join(ROOT, "build", "shots")
os.makedirs(shots, exist_ok=True)
saved = []
SAMPLE_RESULT = ("主题：关于 Q3 预算评审会\n\n"
                 "王经理您好：\n\n现将评审会安排同步如下：下周三上午十点，"
                 "请携带 Q3 预算初稿出席。\n\n顺祝商祺。")
for theme in ("light", "dark"):
    config.set("theme", theme)
    _mw.NAV_PAGE_INDEX.pop(PAGE_KEY, None)
    _mw.NAV_PAGE_TITLES.pop(PAGE_KEY, None)
    win_t = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                       clip, temp_mgr)
    win_t.resize(1000, 900)
    win_t.show()
    pump(300)
    ctx_t = _make_ctx(win_t)
    page_t = plug.AiWorkshopPage(ctx_t)
    page_t._src_edit.setPlainText(CLIP_TEXT)
    page_t._result_edit.setPlainText(SAMPLE_RESULT)
    page_t._copy_btn.setEnabled(True)
    page_t._status.setText("✓ 完成，共 96 字")
    win_t.register_plugin_page(PAGE_KEY, "文本工坊", page_t)
    win_t.show_plugin_page(PAGE_KEY)
    pump(300)
    check(f"D-{theme} host 主题=当前主题",
          getattr(win_t, "current_theme", "") == theme,
          getattr(win_t, "current_theme", None))
    path = os.path.join(shots, f"text_workshop_{theme}.png")
    if win_t.grab().save(path):
        saved.append(theme)
    page_t.deleteLater()
    win_t.close()
    pump(100)
check("D1 light/dark 双主题截图落盘 build/shots/",
      saved == ["light", "dark"], str(saved))

fails = [n for n, ok in _results if not ok]
print(f"\n== verify_text_workshop: {len(_results) - len(fails)}"
      f"/{len(_results)} passed ==", flush=True)
if fails:
    print("失败项：", fails, flush=True)
    sys.exit(1)
