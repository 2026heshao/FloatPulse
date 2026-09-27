# -*- coding: utf-8 -*-
"""AI 助手插件离屏端到端验证（页面插件版）：
真实加载器（含 capabilities + page）→ 真实主窗口页面注入 → 真实页面。

覆盖（每一步都走真实代码路径）：
  A 真实 plugins/ 目录扫到 ai-assistant：动作进注册表、热键生效且
    **不进悬浮球菜单**（menu=false）、manifest page 字段解析、
    create_page 返回真实页面、派生 ctx 带 network 能力
  B 纯函数层：format_* / build_request（含校验）/ parse_reply
  C 真实 AiChatPage：构造不崩、欢迎气泡、设置卡显隐、快捷指令经桥
    发出（假桥捕获）、Enter 发送 / Shift+Enter 换行、保存并测试连接
    反馈（✓/✗ + 按钮复位）、本地服务状态跟随（ready → 自动接后端）、
    页面销毁退订本地服务 listener、配置落盘 data_dir
  E 页面注入链路：register_plugin_page（索引 10+ / 幂等）、
    show_plugin_page（切页 / 未知 key 拒绝）、last_page_index 不写插件页
  F 页面注销 / 重建链路：unregister_plugin_page（按钮摘除 / 占位补槽 /
    自动切回首页 / 可重入）、重新启用走全新注册分支、
    rebuild_plugin_page（启用分支真实路径）、未知插件拒绝
  D light / dark 双主题真截图（页面嵌进真实 MainWindow）→ build/shots/

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

from PyQt6.QtCore import Qt, QEvent               # noqa: E402
from PyQt6.QtWidgets import (                     # noqa: E402
    QApplication, QFrame, QLabel, QScrollArea,
)
from PyQt6.QtGui import QFontDatabase, QKeyEvent  # noqa: E402

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


def send_key(widget, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
    """构造真实 QKeyEvent 走 sendEvent 分发（offscreen 下 QTest.keyClick 会崩）"""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key,
                                             mods, text))


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
# A. 真实加载链路（capabilities + page 生效）
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
check("A2 动作已注册且**不进悬浮球菜单**（menu=false）",
      act is not None and act.menu is False,
      f"menu={getattr(act, 'menu', 'N/A')}")
check("A3 热键 Ctrl+Alt+I 合法且生效（menu=false 不影响热键）",
      act is not None and act.hotkey == "Ctrl+Alt+I"
      and ACTION_ID in [a.id for a in registry.hotkey_actions()])

lp_ai = next((p for p in loaded if p.plugin_id == PLUGIN_ID), None)
check("A4 manifest page 字段解析通过（title 保留）",
      lp_ai is not None and lp_ai.manifest.get("page")
      == {"title": "🤖 AI 助手"},
      str(lp_ai.manifest.get("page") if lp_ai else None))

sub_ctx = registry.context_of(ACTION_ID)
check("A5 派生 ctx 带 network 能力",
      sub_ctx is not None and sub_ctx.has_capability("network"))
check("A6 宿主桥已注入派生 ctx",
      sub_ctx is not None and sub_ctx._http_post_async is not None)
check("A7 未声明的插件没有能力（weekly-report）",
      all(not registry.context_of(f"{pid}.{suffix}").has_capability("network")
          for pid in ("weekly-report",)
          for suffix in ("draft",)
          if registry.context_of(f"{pid}.{suffix}") is not None))

# create_page 入口（knowledge_ball._register_plugin_pages 的调用方式）
page_from_plugin = lp_ai.plugin.create_page(lp_ai.ctx)
check("A8 create_page 返回 AiChatPage 实例",
      type(page_from_plugin).__name__ == "AiChatPage",
      type(page_from_plugin).__name__)
page_from_plugin.destroyed.emit()   # 模拟销毁路径（测试完即弃）
page_from_plugin.deleteLater()

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
check("B9 parse_reply 401 提示本地推理", err401 and "本地推理" in err401)
_, err404 = plug.parse_reply({"ok": False, "status": 404, "body": "",
                              "error": "HTTP 404"})
check("B10 parse_reply 404 提示地址", err404 and "base_url" in err404)
_, errbad = plug.parse_reply({"ok": True, "status": 200, "body": "not-json"})
check("B11 parse_reply 坏 JSON 归一错误", errbad and "协议" in errbad)

# ====================================================================
# C. 真实页面（假桥捕获请求；页面代码路径全真）
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


page_ctx = PluginContext(
    logger=_logger, config=config.as_dict(),
    show_toast=lambda *a, **k: None,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    http_post_async=fake_bridge,
).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), ["network"])

page = plug.AiChatPage(page_ctx)
check("C1 页面构造不崩（真实 MainWindow host）", page is not None)
page.show()
pump()
check("C2 欢迎气泡已在流里", page._stream.count() == 2)   # stretch + 1 卡

check("C3 设置卡默认收起", not page._settings_card.isVisible())
page._toggle_settings()
pump()
check("C4 设置卡可展开（动态显隐给了 parent，不崩）",
      page._settings_card.isVisible())
# ---- 后端设置卡纵向滚动兜底（2026-09-27 用户反馈：窗口矮时被挤瘪）----
card_scroll = page._settings_card.findChild(QScrollArea)
check("C4b 设置卡内容套 QScrollArea 且 widgetResizable",
      card_scroll is not None and card_scroll.widgetResizable())
check("C4c 设置卡高度上限 430（超出卡内滚动，不挤消息流）",
      page._settings_card.maximumHeight() == 430)
# 模拟矮窗口：页面压到 360 高 → 卡片被布局压缩，纵向滚动条必须可用
page.resize(480, 360)
pump(50)
sb_max = card_scroll.verticalScrollBar().maximum()
check("C4d 矮窗口下设置卡内容可纵向滚动",
      sb_max > 0,
      f"sb_max={sb_max} card_h={page._settings_card.height()}")
page.resize(1000, 700)
page._toggle_settings()

# ---- 快捷指令 ----
page.send_quick("tasks", "请总结我的任务")
pump(50)
check("C5 快捷指令经桥发出：URL 正确",
      captured and captured[0]["url"].endswith("/chat/completions"),
      captured[0]["url"] if captured else "无请求")
b = captured[0]["body"]
check("C6 请求体含 system 提示词 + 用户消息 + 真实宿主数据",
      b["messages"][0]["role"] == "system"
      and "截止周五的交付" in b["messages"][-1]["content"]
      and "AI 助手" in b["messages"][0]["content"])
check("C7 本地 key 空时不带 Authorization",
      "Authorization" not in captured[0]["headers"])
check("C8 busy 态已恢复 + AI 气泡出现",
      not page._busy and page._stream.count() == 4)
check("C9 成功轮次落进历史（user+assistant 成对）",
      len(page._history) == 2
      and page._history[0]["role"] == "user"
      and page._history[1]["content"] == "测试回复 ok")

# ---- Enter 发送 / Shift+Enter 换行 ----
n_before = len(captured)
page._input.setPlainText("你好")
send_key(page._input, Qt.Key.Key_Return)          # 无 Shift → 发送
pump(50)
check("C10 Enter 直发（不经按钮）",
      len(captured) == n_before + 1 and page._input.toPlainText() == "")
send_key(page._input, Qt.Key.Key_Return,
         Qt.KeyboardModifier.ShiftModifier)       # Shift → 换行
pump(50)
check("C11 Shift+Enter 只换行不发送",
      len(captured) == n_before + 1
      and "\n" in page._input.toPlainText())
page._input.clear()

# ---- 保存并测试连接（用户要求：确定按钮 + 反馈）----
page._status.clear()
page._save_and_test()
pump(50)
check("C12 保存并测试：探活请求 max_tokens=1",
      captured and captured[-1]["body"].get("max_tokens") == 1)
check("C13 保存并测试：成功反馈 ✓ + 按钮复位可点",
      "✓" in page._status.text()
      and page._save_btn.isEnabled()
      and page._save_btn.text() == "保存并测试连接",
      page._status.text())

# ---- 失败反馈（桥返回失败 → ✗ + 错误提示）----
captured_fail = []


def failing_bridge(url, headers, body, timeout, on_done):
    on_done({"ok": False, "status": 401, "body": "",
             "error": "HTTP 401", "url": url})
    return True


page._ctx._http_post_async = failing_bridge     # 临时换假桥
page._save_and_test()
pump(50)
check("C14 保存并测试：失败反馈 ✗ + 提示 + 按钮复位",
      "✗" in page._status.text() and page._save_btn.isEnabled())
page._ctx._http_post_async = fake_bridge        # 换回来

# ---- 本地服务状态机（不真启动：假路径 → error）----
plug.LOCAL_SERVER.start(page_ctx, "no-such.exe", "no-such.gguf", 8093)
check("C15 本地服务启动校验：假 exe 路径 → error 状态",
      plug.LOCAL_SERVER.status == "error"
      and "程序不存在" in plug.LOCAL_SERVER.detail,
      f"{plug.LOCAL_SERVER.status}/{plug.LOCAL_SERVER.detail}")
listeners_n0 = len(plug.LOCAL_SERVER._listeners)
page.destroyed.emit()          # 触发退订（真实销毁路径的等价操作）
listeners_n1 = len(plug.LOCAL_SERVER._listeners)
check("C16 页面销毁退订本地服务 listener（防死引用累积）",
      listeners_n1 == listeners_n0 - 1,
      f"{listeners_n0} -> {listeners_n1}")

# 就绪状态自动接后端（新页面重新挂 listener 验证页面响应）
page2 = plug.AiChatPage(page_ctx)
page2.show()
plug.LOCAL_SERVER.port = 8093     # 模拟 start 成功后的状态（假路径未走到赋值）
plug.LOCAL_SERVER._emit("ready", "本地服务就绪（127.0.0.1:8093）")
pump()
check("C17 ready → 按钮变「停止」+ URL 自动切本地 8093",
      page2._local_btn.text() == "停止本地服务"
      and page2._url_edit.text() == "http://127.0.0.1:8093/v1",
      f"{page2._local_btn.text()}/{page2._url_edit.text()}")
check("C17b ready → 快捷行出现「⏹ 停止模型服务」（主界面直接可停）",
      page2._stop_model_btn.isVisible(),
      page2._stop_model_btn.text())
plug.LOCAL_SERVER._emit("stopped", "")   # 复位，别污染后面
check("C17c stopped → 停止按钮隐藏（不占聊天界面空间）",
      not page2._stop_model_btn.isVisible())

# ---- 左右气泡（2026-09-28 用户要求：一左一右对话式）----
page2.add_bubble("你", "测试用户消息")
page2.add_bubble("AI", "测试 AI 回复")
page2.add_bubble("提示", "测试提示消息")
pump(50)
_stream_items = [page2._stream.itemAt(i)
                 for i in range(page2._stream.count())]
_user_cards = [it.widget() for it in _stream_items
               if it.widget() is not None
               and it.widget().objectName() == "chatBubbleUser"]
_ai_cards = [it.widget() for it in _stream_items
             if it.widget() is not None
             and it.widget().objectName() == "chatBubbleAI"]
_hint_cards = [it.widget() for it in _stream_items
               if it.widget() is not None
               and it.widget().objectName() == "chatBubbleHint"]
check("C20 左右气泡：三种角色 objectName 正确（用户主色底/AI 中性/提示警示）",
      len(_user_cards) == 1 and len(_ai_cards) >= 1 and len(_hint_cards) >= 1,
      f"user={len(_user_cards)} ai={len(_ai_cards)} hint={len(_hint_cards)}")
def _item_of(card):
    """布局里这张卡对应的 QLayoutItem（读 insertWidget 设的 alignment）"""
    for i in range(page2._stream.count()):
        it = page2._stream.itemAt(i)
        if it.widget() is card:
            return it
    return None


_user_item = _item_of(_user_cards[0]) if _user_cards else None
check("C21 用户气泡靠右对齐（insertWidget alignment=AlignRight）",
      _user_item is not None
      and bool(_user_item.alignment() & Qt.AlignmentFlag.AlignRight),
      str(_user_item.alignment() if _user_item else "无 item"))
check("C22 气泡是窄卡（最大宽 ≤ 可视区 78%，不撑满整行）",
      bool(_user_cards)
      and _user_cards[0].maximumWidth() <= max(
          360, int(page2._scroll.viewport().width() * 0.78)),
      f"maxW={_user_cards[0].maximumWidth() if _user_cards else '?'} "
      f"vpW={page2._scroll.viewport().width()}")

# ---- 云端断开连接（2026-09-28 用户要求：云端要有启动/暂停式控制）----
check("C23 断开按钮存在（与保存并测试并排）",
      page2._disconnect_btn is not None
      and page2._disconnect_btn.text() == "断开连接",
      page2._disconnect_btn.text() if page2._disconnect_btn else "无")
n_before_disc = len(captured)
# 闸门只拦云端（本地 URL 不受影响）：先把后端临时指到云端再测
_real_url = page2._url_edit.text()
page2._url_edit.setText("https://api.deepseek.com/v1")
page2._collect_settings()
page2._cloud_active = False
page2._input.setPlainText("断开后发不出去")
page2._on_send_clicked()
pump(50)
_hints_after = [w for w in page2._stream_host.findChildren(QFrame)
                if w.objectName() == "chatBubbleHint"]
check("C24 断开后云端请求被拦（桥零调用 + 提示气泡 + 展开设置卡 + 状态行反馈）",
      len(captured) == n_before_disc
      and page2._settings_card.isVisible()
      and len(_hints_after) >= 2      # C20 的测试提示气泡 + 本条拦截气泡
      and "已断开" in page2._status.text(),
      f"captured={len(captured)} hints={len(_hints_after)} "
      f"status={page2._status.text()}")
page2._save_and_test()
pump(50)
check("C25 探活成功 → 云端闸门自动恢复（可再发）",
      page2._cloud_active is True and len(captured) == n_before_disc + 1,
      f"active={page2._cloud_active} captured={len(captured)}")
page2._url_edit.setText(_real_url)     # 恢复原配置，别污染 C19 落盘结论
page2._collect_settings()

# ---- 清空 + 配置落盘 ----
page2._clear_chat()
pump()
check("C18 清空对话：历史与气泡都清（欢迎语重新出现）",
      page2._history == [] and page2._stream.count() == 2)

page2._url_edit.setText("http://127.0.0.1:8080/v1")
page2._key_edit.setText("sk-test")
page2._model_edit.setText("qwen3-4b")
page2._collect_settings()
cfg_path = os.path.join(page_ctx.data_dir, "config.json")
stored = json.load(open(cfg_path, encoding="utf-8"))
check("C19 配置落盘 data_dir/config.json",
      stored["base_url"] == "http://127.0.0.1:8080/v1"
      and stored["api_key"] == "sk-test", str(stored))
page2.deleteLater()
page.deleteLater()

# ====================================================================
# E. 页面注入链路（register_plugin_page / show_plugin_page / last_page）
# ====================================================================
page3 = plug.AiChatPage(page_ctx)
idx1 = win.register_plugin_page(PAGE_KEY, "🤖 AI 助手", page3)
check("E1 插件页注入：物理索引 ≥ 10（固定页 0-9 之外）", idx1 >= 10, str(idx1))
check("E2 侧栏出现该页面按钮（插在设置按钮之前）",
      win._nav_btns.get(PAGE_KEY) is not None)

# 幂等：重复注册（rescan 重建路径）= 新 widget 换旧 widget，索引不变
page4 = plug.AiChatPage(page_ctx)
idx2 = win.register_plugin_page(PAGE_KEY, "🤖 AI 助手", page4)
check("E3 幂等注册：同 key 索引不变 + stack 里是新页面",
      idx2 == idx1 and win._stack.widget(idx1) is page4,
      f"{idx1} -> {idx2}")

ok = win.show_plugin_page(PAGE_KEY)
pump(50)
check("E4 show_plugin_page 切页成功且 currentIndex 正确",
      ok and win._stack.currentIndex() == idx1,
      f"ok={ok} cur={win._stack.currentIndex()}")
check("E5 插件页不写入 last_page_index（config 上限 9）",
      config.get("last_page_index", 0) != idx1,
      str(config.get("last_page_index", 0)))
check("E6 未知 key / 固定页 key 被拒绝",
      win.show_plugin_page("plugin:no-such") is False
      and win.show_plugin_page("plugin:main") is False)
page3.deleteLater()
page4.deleteLater()

# ====================================================================
# F. 页面注销 / 重建链路（停用插件 → 页面与导航键同步消失；重新启用 → 回归）
# ====================================================================
# E4 已把页面切到前台：注销时必须自动切回首页
ok = win.unregister_plugin_page(PAGE_KEY)
check("F1 注销成功 + 模块级注册表清除",
      ok is True
      and PAGE_KEY not in _mw.NAV_PAGE_INDEX
      and PAGE_KEY not in _mw.NAV_PAGE_TITLES)
check("F2 侧栏按钮已摘除", win._nav_btns.get(PAGE_KEY) is None)
check("F3 正显示该页时注销 → 自动切回首页",
      win._stack.currentIndex() == 0, str(win._stack.currentIndex()))
check("F4 占位补槽：stack 总页数不变 + 已注销 key 被拒绝",
      win._stack.count() == idx1 + 1
      and win.show_plugin_page(PAGE_KEY) is False,
      f"count={win._stack.count()}")
check("F5 重复注销返回 False（可安全重入）",
      win.unregister_plugin_page(PAGE_KEY) is False)

# 重新启用：register 走全新注册分支（新索引、新按钮）
page5 = plug.AiChatPage(page_ctx)
idx3 = win.register_plugin_page(PAGE_KEY, "🤖 AI 助手", page5)
check("F6 重新注册：全新分支新索引 + 按钮回归",
      idx3 == idx1 + 1 and win._nav_btns.get(PAGE_KEY) is not None,
      f"{idx1} -> {idx3}")

# rebuild_plugin_page（插件中心「启用」分支的真实调用路径）
win.set_plugin_loader(loader)      # 产品里由 knowledge_ball 启动时注入
saved_widget = win._stack.widget(idx3)
check("F7 rebuild 重建页面：索引稳定 + stack 换新 widget",
      win.rebuild_plugin_page(PLUGIN_ID) is True
      and win._stack.widget(idx3) is not None
      and win._stack.widget(idx3) is not saved_widget)
check("F8 rebuild 未知插件返回 False",
      win.rebuild_plugin_page("no-such-plugin") is False)

# 收尾：模拟「再次停用」，把全局注册痕迹清干净（D 段多实例不受串台影响）
check("F9 收尾注销成功（全局 dict 已清）",
      win.unregister_plugin_page(PAGE_KEY) is True
      and PAGE_KEY not in _mw.NAV_PAGE_INDEX)

# ====================================================================
# D. light / dark 双主题真截图（页面嵌进真实 MainWindow 作 host）
# ====================================================================
shots = os.path.join(ROOT, "build", "shots")
os.makedirs(shots, exist_ok=True)
saved = []
for theme in ("light", "dark"):
    config.set("theme", theme)
    # NAV_PAGE_INDEX/NAV_PAGE_TITLES 是模块级单例：前面的窗口实例已注册过
    # 插件页，多实例下会串台（新窗口走幂等分支却把按钮插进旧窗口侧栏）。
    # 产品单实例运行无此问题；离屏多实例测试需先清掉全局注册痕迹。
    _mw.NAV_PAGE_INDEX.pop(PAGE_KEY, None)
    _mw.NAV_PAGE_TITLES.pop(PAGE_KEY, None)
    win_t = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                       clip, temp_mgr)
    win_t.resize(1000, 760)
    win_t.show()
    pump(300)
    ctx_t = PluginContext(
        logger=_logger, config=config.as_dict(),
        show_toast=lambda *a, **k: None,
        data=plugin_data,
        data_dir_base=os.path.join(data_dir, "plugins"),
        parent_window=lambda w=win_t: w,
        http_post_async=fake_bridge,
    ).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), ["network"])
    page_t = plug.AiChatPage(ctx_t)
    # 真实时序：先注入主窗口（reparent 到 QSS 作用域内）再显示页面；
    # 反过来先 show 会让页面先成为无 QSS 祖先的顶层窗口，离屏下样式残留
    page_t._toggle_settings()          # 展开设置卡：截图信息量更足
    win_t.register_plugin_page(PAGE_KEY, "🤖 AI 助手", page_t)
    win_t.show_plugin_page(PAGE_KEY)
    pump(300)
    check(f"D-{theme} host 主题=当前主题",
          getattr(win_t, "current_theme", "") == theme,
          getattr(win_t, "current_theme", None))
    path = os.path.join(shots, f"ai_assistant_{theme}.png")
    # 全窗 grab：对刚 reparent 的子件单独 grab，离屏下 QSS 合成不完整
    # （实测 dark 下字色残留 light 态），真实显示无此问题
    if win_t.grab().save(path):
        saved.append(theme)
    page_t.deleteLater()
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
