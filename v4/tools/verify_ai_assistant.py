# -*- coding: utf-8 -*-
"""AI 助手插件离屏端到端验证：真实加载器（含 capabilities）→ 真实对话框。

覆盖（每一步都走真实代码路径）：
  A 真实 plugins/ 目录扫到 ai-assistant：动作进注册表、热键合法、
    派生 ctx 带 network 能力、宿主桥已注入
  B 纯函数层：format_tasks / format_fragments / format_notes /
    build_request（含校验）/ parse_reply（401/404/坏 JSON/正常）
  C 真实对话框：构造不崩、欢迎气泡、设置卡显隐、快捷指令经桥发出
    （假桥捕获：URL / headers / body / system 提示词全对）、回复气泡、
    历史落位、清空对话、配置落盘 data_dir
  D light / dark 双主题真截图（真实 MainWindow 作 host）→ build/shots/

跑法：python tools/run_gui_check.py tools/verify_ai_assistant.py
"""
import json
import logging
import os
import sys
import tempfile
import time
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)

from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtGui import QFontDatabase             # noqa: E402

from src.config import ConfigManager              # noqa: E402
from src.docx_manager import DocxManager          # noqa: E402
from src.task_manager import TaskManager          # noqa: E402
from src.note_manager import NoteManager          # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow            # noqa: E402
from src.plugin_api import (                      # noqa: E402
    ActionRegistry, PluginContext, PluginData,
)
from src.plugin_loader import PluginLoader        # noqa: E402
from src.plugin_net import make_async_poster      # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setApplicationName("verify_ai_assistant")
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

_results = []
PLUGIN_ID = "ai-assistant"
ACTION_ID = f"{PLUGIN_ID}.chat"


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


# ---------------- 日志 ----------------
class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
_logger = logging.getLogger("fp_verify_ai_assistant")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_logger.handlers.clear()
_logger.addHandler(_Capture(_records))


# ====================================================================
# 搭数据 / 真实主窗口
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_ai_")
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

today = date.today()
done_id = task_mgr.add_task("已完成的任务", "备注文字", today.isoformat())
task_mgr.set_done(done_id, True)
task_mgr.add_focus_session(done_id, 2)
task_mgr.add_task("截止周五的交付", "", (today.isoformat()))
frag_mgr.add_clipboard_text("https://example.com/case-a", source="剪贴板")
frag_mgr.add_clipboard_text("def demo():\n    return 42", source="剪贴板")
frag_mgr.flush()
note_mgr.add_note("本周会议要点正文", title="周会记录")

# 兼容垫片：导航键位不一致时补齐（与 verify_weekly_report 同款，不改源码）
_mw = sys.modules["src.main_window"]
if "plugins" not in _mw.NAV_PAGE_TITLES:
    _mw.NAV_PAGE_TITLES["plugins"] = "🔌  插件中心"
    _mw.NAV_PAGE_INDEX.setdefault("plugins", 9)

win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr)
win.resize(1000, 760)
win.show()
pump(200)

# ====================================================================
# A. 真实加载链路（capabilities 生效）
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
    "pomodoro": lambda: {"state": "idle"},
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
    http_post_async=make_async_poster(logger=_logger),   # 真桥
)
loader = PluginLoader(registry, ctx, plugins_dir=plugins_dir)
loaded = loader.load_all()
ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 ai-assistant", PLUGIN_ID in ids, f"{ids}")

act = registry.get(ACTION_ID)
check("A2 动作已进注册表且挂菜单", act is not None and act.menu is True)
check("A3 热键 Ctrl+Alt+I 合法且生效",
      act is not None and act.hotkey == "Ctrl+Alt+I"
      and ACTION_ID in [a.id for a in registry.hotkey_actions()])

sub_ctx = registry.context_of(ACTION_ID)
check("A4 派生 ctx 带 network 能力",
      sub_ctx is not None and sub_ctx.has_capability("network"))
check("A5 宿主桥已注入派生 ctx",
      sub_ctx is not None and sub_ctx._http_post_async is not None)
check("A6 未声明的插件没有能力（weekly-report）",
      all(not registry.context_of(f"{pid}.{suffix}").has_capability("network")
          for pid in ("weekly-report",)
          for suffix in ("draft",)
          if registry.context_of(f"{pid}.{suffix}") is not None))

# ====================================================================
# B. 纯函数层
# ====================================================================
plug = sys.modules.get("floatpulse_plugin_ai_assistant")
check("B0 插件模块可从 sys.modules 取到", plug is not None)

tasks = [
    {"title": "写周报", "done": False, "deadline": today.isoformat(),
     "focus_sessions": 0},
    {"title": "已结项", "done": True, "deadline": "", "focus_sessions": 3},
    {"title": "无日期的", "done": False, "deadline": "", "focus_sessions": 0},
]
ft = plug.format_tasks(tasks, 6000)
check("B1 format_tasks 标注状态/截止/番茄",
      "[待办] 写周报" in ft and "[已完成] 已结项" in ft
      and f"截止 {today.isoformat()}" in ft and "3 个番茄" in ft)

frags = [
    {"category": "link", "content": "https://a.com"},
    {"category": "code", "content": "x = 1"},
    {"category": "code", "content": "y = 2"},
]
ff = plug.format_fragments(frags, 6000)
check("B2 format_fragments 分类计数", "[link] 共 1 条" in ff and "[code] 共 2 条" in ff)
check("B3 format_notes 空数据直说空", plug.format_notes([], 6000) == "（暂无笔记）")

url, headers, body = plug.build_request(
    {"base_url": "http://127.0.0.1:11434/v1", "api_key": "", "model": "q3"},
    [{"role": "user", "content": "hi"}])
check("B4 build_request 本地端点：URL/无 Authorization",
      url == "http://127.0.0.1:11434/v1/chat/completions"
      and "Authorization" not in headers and body["model"] == "q3")
url2, h2, _ = plug.build_request(
    {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-1",
     "model": "m"}, [])
check("B5 build_request 云端带 Bearer",
      url2.endswith("/chat/completions") and h2["Authorization"] == "Bearer sk-1")
bad = plug.build_request({"base_url": "", "model": ""}, [])
check("B6 build_request 空配置被拦", bad[0] is None and "base_url" in bad[2])
bad2 = plug.build_request({"base_url": "ftp://x", "model": "m"}, [])
check("B7 build_request 拒非 http scheme", bad2[0] is None)

rep, err = plug.parse_reply({"ok": True, "status": 200,
                             "body": json.dumps(
                                 {"choices": [{"message": {"content": "答复"}}]})})
check("B8 parse_reply 提取 content", rep == "答复" and err is None)
_, err401 = plug.parse_reply({"ok": False, "status": 401, "body": "",
                              "error": "HTTP 401"})
check("B9 parse_reply 401 提示 key", err401 and "key" in err401)
_, err404 = plug.parse_reply({"ok": False, "status": 404, "body": "",
                              "error": "HTTP 404"})
check("B10 parse_reply 404 提示地址", err404 and "base_url" in err404)
_, errbad = plug.parse_reply({"ok": True, "status": 200, "body": "not-json"})
check("B11 parse_reply 坏 JSON 归一错误", errbad and "协议" in errbad)

# ====================================================================
# C. 真实对话框（假桥捕获请求；对话框代码路径全真）
# ====================================================================
captured = []


def fake_bridge(url, headers, body, timeout, on_done):
    captured.append({"url": url, "headers": dict(headers),
                     "body": dict(body), "timeout": timeout})
    on_done({"ok": True, "status": 200,
             "body": json.dumps(
                 {"choices": [{"message": {"content": "测试回复 ok"}}]}),
             "error": "", "url": url})
    return True


dlg_ctx = PluginContext(
    logger=_logger, config=config.as_dict(),
    show_toast=lambda *a, **k: None,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    http_post_async=fake_bridge,
).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), ["network"])

dlg = plug.ChatDialog(dlg_ctx)
check("C1 对话框构造不崩（真实 MainWindow host）", dlg is not None)
dlg.show()
pump()
check("C2 欢迎气泡已在流里", dlg._stream.count() == 2)   # stretch + 1 卡

check("C3 设置卡默认收起", not dlg._settings_card.isVisible())
dlg._toggle_settings()
pump()
check("C4 设置卡可展开（动态显隐给了 parent，不崩）",
      dlg._settings_card.isVisible())
dlg._toggle_settings()

dlg.send_quick("tasks", "请总结我的任务")
pump(50)
check("C5 快捷指令经桥发出：URL 正确",
      captured and captured[0]["url"].endswith("/chat/completions"),
      captured[0]["url"] if captured else "无请求")
b = captured[0]["body"]
check("C6 请求体含 system 提示词 + 用户消息 + 数据",
      b["messages"][0]["role"] == "system"
      and "截止周五的交付" in b["messages"][-1]["content"]
      and "AI 助手" in b["messages"][0]["content"])
check("C7 本地 key 空时不带 Authorization",
      "Authorization" not in captured[0]["headers"])
check("C8 busy 态已恢复 + AI 气泡出现",
      not dlg._busy and dlg._stream.count() == 4)
check("C9 成功轮次落进历史（user+assistant 成对）",
      len(dlg._history) == 2
      and dlg._history[0]["role"] == "user"
      and dlg._history[1]["content"] == "测试回复 ok")

dlg._clear_chat()
pump()
check("C10 清空对话：历史与气泡都清（欢迎语重新出现）",
      dlg._history == [] and dlg._stream.count() == 2)

dlg._url_edit.setText("http://127.0.0.1:8080/v1")
dlg._key_edit.setText("sk-test")
dlg._model_edit.setText("qwen3-4b")
dlg._collect_settings()
cfg_path = os.path.join(dlg_ctx.data_dir, "config.json")
stored = json.load(open(cfg_path, encoding="utf-8"))
check("C11 配置落盘 data_dir/config.json",
      stored["base_url"] == "http://127.0.0.1:8080/v1"
      and stored["api_key"] == "sk-test", str(stored))
dlg.close()

# ====================================================================
# D. light / dark 双主题真截图（各自真实 MainWindow 作 host）
# ====================================================================
shots = os.path.join(ROOT, "build", "shots")
os.makedirs(shots, exist_ok=True)
saved = []
for theme in ("light", "dark"):
    config.set("theme", theme)
    win_t = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                       clip, temp_mgr)
    win_t.resize(1000, 760)
    win_t.show()
    pump(300)
    # 关键：host 必须是**这个**主题的主窗口（ctx.parent_window 决定取主题）
    ctx_t = PluginContext(
        logger=_logger, config=config.as_dict(),
        show_toast=lambda *a, **k: None,
        data=plugin_data,
        data_dir_base=os.path.join(data_dir, "plugins"),
        parent_window=lambda w=win_t: w,
        http_post_async=fake_bridge,
    ).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), ["network"])
    dlg_t = plug.ChatDialog(ctx_t)
    dlg_t.show()
    pump(300)
    check(f"D-{theme} host 主题=当前主题",
          getattr(dlg_t._host, "current_theme", "") == theme,
          getattr(dlg_t._host, "current_theme", None))
    path = os.path.join(shots, f"ai_assistant_{theme}.png")
    if dlg_t.grab().save(path):
        saved.append(theme)
    dlg_t.close()
    win_t.close()
    pump(100)
check("D1 light/dark 双主题截图落盘 build/shots/", saved == ["light", "dark"],
      str(saved))

# ====================================================================
# 汇总
# ====================================================================
fails = [n for n, ok in _results if not ok]
print(f"\n== verify_ai_assistant: {len(_results) - len(fails)}"
      f"/{len(_results)} passed ==", flush=True)
if fails:
    print("失败项：", fails, flush=True)
    sys.exit(1)
print("[DONE]", flush=True)
