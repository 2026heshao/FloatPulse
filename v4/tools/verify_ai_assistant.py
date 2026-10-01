# -*- coding: utf-8 -*-
"""AI 助手插件离屏端到端验证（页面插件版）：
真实加载器（含 capabilities + page）→ 真实主窗口页面注入 → 真实页面。

覆盖（每一步都走真实代码路径）：
  A 真实 plugins/ 目录扫到 ai-assistant：动作进注册表、热键生效且
    **不进悬浮球菜单**（menu=false）、manifest page 字段解析、
    create_page 返回真实页面、派生 ctx 带 network 能力
  B 纯函数层：format_* / build_request（cloud/local 双模式校验 + 旧后端键
    被白名单合并忽略）/ parse_reply / build_system_prompt（规则库追加，
    停用/空/脏跳过）/ 动作协议 parse_actions（合法动作分级、编造 id 与
    未知 op 丢弃、坏 JSON 整块不执行、超上限截断、混合批次只丢非法条）
  C 真实 AiChatPage（2026-10-01 口径对齐：后端收归设置页「AI 总配置」，
    页面零后端配置 UI，经 ctx.ai 实时读参数快照 + 订阅本地服务广播）：
    构造不崩、欢迎气泡、页面无残留后端 UI、快捷指令经桥发出（假桥捕获）、
    Enter 发送 / Shift+Enter 换行、ctx.ai 监听（构造即订阅 / 销毁即退订 /
    ready 广播 → 快捷停止钮出现 / stopped → 隐藏 / 停止走 ctx.ai.stop_local）、
    参数实时直读（设置页改后端下一发即生效）、本地未就绪拦截、
    规则库入口（展开编辑 → 保存落盘 → 对话注入生效规则、停用不注入）、
    气泡宽度/高度协调（sizeHint 塌缩与截字回归钉子）、
    「存为笔记」右键菜单（菜单项可点 / 已存置灰、气泡内无常驻按钮）、
    数据操控全链路（授权才下发协议、add 直接执行、改删弹确认卡、
    点执行真落库并带撤销按钮、撤销恢复内容、取消零改动、
    未授权动作如实拒绝）、
    页面销毁退订 ctx.ai listener、插件配置零后端键
  E 页面注入链路：register_plugin_page（索引 10+ / 幂等）、
    show_plugin_page（切页 / 未知 key 拒绝）、last_page_index 不写插件页
  F 页面注销 / 重建链路：unregister_plugin_page（按钮摘除 / 占位补槽 /
    自动切回首页 / 可重入）、重新启用走全新注册分支、
    rebuild_plugin_page（启用分支真实路径）、未知插件拒绝
  D light / dark 双主题真截图（页面嵌进真实 MainWindow）→ build/shots/
    ├ ai_assistant_{theme}.png          常规页（设置卡展开）
    └ ai_assistant_actions_{theme}.png  数据操控卡（结果卡 + 确认卡 + 撤销按钮）

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
    QApplication, QFrame, QLabel, QPushButton,
)
from PyQt6.QtGui import QFontDatabase, QKeyEvent  # noqa: E402

from docx import Document                        # noqa: E402

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
# 知识库：现造一份带内容的 docx（真实 DocxManager 读写它）
_kb_path = os.path.join(tmp, "知识库.docx")
_kb_seed = Document()
for _t in ("知识库第一条内容", "知识库第二条内容", "知识库第三条内容",
           "知识库第四条内容"):
    _kb_seed.add_paragraph(_t)
_kb_seed.save(_kb_path)
docx_mgr = DocxManager(_kb_path, os.path.join(data_dir, "docx_meta.json"))
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
    "knowledge": lambda: [
        {"num": i + 1, "text": p.text, "preview": p.preview, "hash": p.hash}
        for i, p in enumerate(docx_mgr.get_paragraphs())],
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
# A7（v1.2.0 周报润色起 weekly-report 声明了 network+ai，「未声明」反例
# 换成真零能力的 kb-search：manifest capabilities 为空数组）
check("A7 未声明的插件没有能力（kb-search 只读零能力）",
      all(not registry.context_of(f"{pid}.{suffix}").has_capability("network")
          for pid in ("kb-search",)
          for suffix in ("open",)
          if registry.context_of(f"{pid}.{suffix}") is not None))

# 知识库只读快照：num 从 1 起（与知识库面板编号一致）+ 内容指纹
_kb_rows = plugin_data.knowledge()
check("A9 ctx.data.knowledge() 给编号 / 文本 / 指纹（只读快照）",
      len(_kb_rows) == 4 and _kb_rows[0]["num"] == 1
      and _kb_rows[0]["text"] == "知识库第一条内容"
      and _kb_rows[3]["num"] == 4
      and all(len(r["hash"]) == 16 for r in _kb_rows),
      f"rows={[(r['num'], r['text'], len(r['hash'])) for r in _kb_rows]}")
_kb_copy = plugin_data.knowledge()
_kb_copy[0]["text"] = "改坏了"
check("A9b 知识库快照是副本（插件改不到宿主内存模型）",
      docx_mgr.get_paragraphs()[0].text == "知识库第一条内容",
      docx_mgr.get_paragraphs()[0].text)

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

# —— 2026-10-01 口径对齐：后端字段已收归设置页「AI 总配置」——
# 插件侧 build_request 的参数即 ctx.ai.params() 快照：
# mode("local"/"cloud") / local_port / base_url / api_key / model / local_ready
url, headers, body = plug.build_request(
    {"mode": "local", "local_port": 8095},
    [{"role": "user", "content": "hi"}])
check("B4 build_request 本地模式：URL/无 Authorization/模型名 local",
      url == "http://127.0.0.1:8095/v1/chat/completions"
      and "Authorization" not in headers and body["model"] == "local")
url2, h2, _ = plug.build_request(
    {"mode": "cloud", "base_url": "https://api.deepseek.com/v1",
     "api_key": "sk-1", "model": "m"}, [])
check("B5 build_request 云端带 Bearer",
      url2.endswith("/chat/completions") and h2["Authorization"] == "Bearer sk-1")
bad = plug.build_request({"mode": "cloud", "base_url": "", "model": ""}, [])
check("B6 build_request 空配置被拦", bad[0] is None and "后端地址为空" in bad[2])
bad2 = plug.build_request(
    {"mode": "cloud", "base_url": "ftp://x", "model": "m"}, [])
check("B7 build_request 拒非 http scheme", bad2[0] is None)

# B7b 旧配置键（backend_mode / cloud_* / local_*）已收归设置页：插件
# config.json 里残留的旧键被 load_config 的「白名单合并」直接忽略，不再迁移
class _Log:
    def warning(self, *a, **k):
        pass

_mig_dir = os.path.join(data_dir, "mig_test")
os.makedirs(_mig_dir, exist_ok=True)
with open(os.path.join(_mig_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"base_url": "http://127.0.0.1:8093/v1", "api_key": "sk-old",
               "model": "local", "backend_mode": "cloud",
               "cloud_base_url": "http://127.0.0.1:9/v1"}, f)
_mig = plug.load_config(
    __import__("types").SimpleNamespace(data_dir=_mig_dir, logger=_Log()))
_stale = {"backend_mode", "cloud_base_url", "cloud_api_key", "cloud_model",
          "base_url", "api_key", "model"} & set(_mig)
check("B7b 旧后端键被白名单合并忽略（不迁移、不进配置、零残留）",
      not _stale and _mig == plug.DEFAULT_CONFIG,
      f"残留={sorted(_stale)} keys={sorted(_mig)}")

rep, err = plug.parse_reply({"ok": True, "status": 200,
                             "body": json.dumps(
                                 {"choices": [{"message": {"content": "答复"}}]})})
check("B8 parse_reply 提取 content", rep == "答复" and err is None)
_, err401 = plug.parse_reply({"ok": False, "status": 401, "body": "",
                              "error": "HTTP 401"})
check("B9 parse_reply 401 提示去设置页检查 key", err401 and "AI 总配置" in err401)
_, err404 = plug.parse_reply({"ok": False, "status": 404, "body": "",
                              "error": "HTTP 404"})
check("B10 parse_reply 404 提示地址", err404 and "地址" in err404)
_, errbad = plug.parse_reply({"ok": True, "status": 200, "body": "not-json"})
check("B11 parse_reply 坏 JSON 归一错误", errbad and "协议" in errbad)

# ---- 规则库：build_system_prompt（2026-09-28 用户要求，规则用户自编辑）----
check("B12 build_system_prompt 无规则=基础提示词（行为不变）",
      plug.build_system_prompt(None) == plug.SYSTEM_PROMPT
      and plug.build_system_prompt([]) == plug.SYSTEM_PROMPT)
_sp = plug.build_system_prompt([
    {"text": "回答不超过 200 字", "enabled": True},
    {"text": "   ", "enabled": True},          # 空白条目跳过
    {"text": "已停用的规则", "enabled": False},
    {"text": "周报用 Markdown 表格"},           # 缺 enabled 默认启用
])
check("B13 build_system_prompt 只拼启用规则（空白/停用跳过）",
      "回答不超过 200 字" in _sp and "周报用 Markdown 表格" in _sp
      and "已停用的规则" not in _sp and "规则库" in _sp
      and "1. " in _sp and "2. " in _sp,
      _sp[-120:])
check("B14 build_system_prompt 纯字符串条目按启用处理（兼容手改配置）",
      "手写规则" in plug.build_system_prompt(["手写规则"]))
check("B15 build_system_prompt 全停用=基础提示词",
      plug.build_system_prompt(
          [{"text": "x", "enabled": False}]) == plug.SYSTEM_PROMPT)

# ---- 动作协议（2026-09-28 用户要求：AI 按指令改数据，需要指令触发）----
# 快照里的 id 必须是「真实存在的记录」：解析器拿它挡掉模型编造的编号
_snap = {
    "tasks": [{"task_id": 3, "title": "写周报", "done": False},
              {"task_id": 7, "title": "买咖啡豆", "done": True}],
    "fragments": [{"fragment_id": 2, "content": "https://a.com"}],
    "notes": [{"note_id": 5, "title": "周会记录", "content": "正文"}],
}

_bt, _ba, _be = plug.parse_actions("这是普通回答，没有动作块。", _snap)
check("B16 parse_actions 无动作块 → 正文原样、零动作零错误",
      _bt == "这是普通回答，没有动作块。" and _ba == [] and _be == [],
      f"text={_bt!r} acts={_ba} errs={_be}")

_t, _a, _e = plug.parse_actions(
    '已帮你建好任务。\n```actions\n'
    '{"actions":[{"op":"add_task","title":"写季度总结"}]}\n```', _snap)
check("B17 合法 add_task → level=write + 正文剥离 JSON 块",
      len(_a) == 1 and _a[0]["op"] == "add_task" and _a[0]["level"] == "write"
      and "{" not in _t and "actions" not in _t and _e == [],
      f"acts={_a} clean={_t!r} errs={_e}")

_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"complete_task","id":3}]}\n```', _snap)
check("B18 合法 complete_task → level=manage + desc 引用真实标题",
      len(_a) == 1 and _a[0]["level"] == "manage" and "写周报" in _a[0]["desc"],
      f"acts={_a}")

_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"delete_task","id":999}]}\n```', _snap)
check("B19 id 不在快照（编造编号）→ 丢弃该条 + 给出原因",
      _a == [] and any("999" in x for x in _e), f"acts={_a} errs={_e}")

_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"drop_database","id":1}]}\n```', _snap)
check("B20 未知 op → 丢弃（op 白名单之外一律不认）",
      _a == [] and any("未知动作" in x for x in _e), f"acts={_a} errs={_e}")

_t, _a, _e = plug.parse_actions(
    '一些结论\n```actions\n{"actions":[{"op":"add_task", }\n```', _snap)
check("B21 动作块坏 JSON → 整块不执行 + 原因（正文不被吞掉）",
      _a == [] and _e and "一些结论" in _t, f"acts={_a} errs={_e} clean={_t!r}")

_many = ",".join('{"op":"add_fragment","content":"x%d"}' % i
                 for i in range(plug.MAX_ACTIONS + 5))
_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[%s]}\n```' % _many, _snap)
check("B22 动作数超上限 → 截断到 MAX_ACTIONS + 提示（防刷屏式输出）",
      len(_a) == plug.MAX_ACTIONS and any("超过上限" in x for x in _e),
      f"n={len(_a)} errs={_e}")

_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"add_task","title":123}]}\n```', _snap)
check("B23 参数类型错（title 非字符串）→ 丢该条",
      _a == [] and _e, f"acts={_a} errs={_e}")

_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"add_task","title":"合法新增"},'
    '{"op":"delete_note","id":888}]}\n```', _snap)
check("B24 混合批次：合法动作保留 / 非法单条丢弃（不整块作废）",
      len(_a) == 1 and _a[0]["op"] == "add_task" and _e,
      f"acts={_a} errs={_e}")

_bs = "== 你可以执行的应用操作 =="
check("B25 build_system_prompt：授权 manage 才下发动作协议",
      _bs in plug.build_system_prompt(None, can_manage=True)
      and _bs not in plug.build_system_prompt(None, can_manage=False))

# 纯动作回复（只有 ```actions 块、没有正文）→ 正文必须是空串，
# 不能回退成原文把 JSON 当正文显示（真截图发现的 bug）
_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"delete_task","id":7}]}\n```', _snap)
check("B26 纯动作回复 → 正文为空串（JSON 不得当正文渲染）",
      _t == "" and len(_a) == 1 and _e == [], f"clean={_t!r} acts={_a}")

# 正文里的普通 JSON 示例（不含 actions 键）→ 不是动作块，正文必须原样保留
_ex = '这段配置就是 {"name": "FloatPulse", "version": 4} 的意思。'
_t, _a, _e = plug.parse_actions(_ex, _snap)
check("B27 正文里的 JSON 示例（非动作块）→ 不剥离、不误删正文",
      _t == _ex and _a == [] and _e == [], f"clean={_t!r} acts={_a}")

# 描述会进 QLabel（不渲染 Markdown）→ 不能带 ** 之类的标记
_t, _a, _e = plug.parse_actions(
    '```actions\n{"actions":[{"op":"delete_note","id":5}]}\n```', _snap)
check("B28 删除类描述不带 Markdown 标记（QLabel 不渲染 **）",
      len(_a) == 1 and "**" not in _a[0]["desc"]
      and "删除" in _a[0]["desc"] and "周会记录" in _a[0]["desc"],
      f"{_a}")

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


# 假 AI 总配置 providers（2026-10-01 口径：后端收归设置页，页面经 ctx.ai
# 实时读快照 + 订阅本地服务广播）。改 _ai_params 即模拟「设置页改配置」。
# 默认本地已就绪：C5-C7 的首发请求走本地端口（key 空 → 不带 Authorization）。
_ai_params = {"mode": "local", "base_url": "http://127.0.0.1:8080/v1",
              "api_key": "sk-test", "model": "verify-model",
              "local_port": 8095, "local_ready": True}
_ai_listeners = []          # 页面构造时经 ctx.ai.add_listener 注册
_stop_calls = [0]           # 快捷「停止模型服务」→ ctx.ai.stop_local 调用数


def _fake_add_listener(fn):
    _ai_listeners.append(fn)
    return True


def _fake_remove_listener(fn):
    if fn in _ai_listeners:
        _ai_listeners.remove(fn)
    return True


def _fake_stop_local():
    _stop_calls[0] += 1
    return True


page_ctx = PluginContext(
    logger=_logger, config=config.as_dict(),
    show_toast=lambda *a, **k: None,
    data=plugin_data,
    data_dir_base=os.path.join(data_dir, "plugins"),
    parent_window=lambda: win,
    http_post_async=fake_bridge,
    ai_providers={
        "is_attached": lambda pid: True,
        "params": lambda: dict(_ai_params),
        "add_listener": _fake_add_listener,
        "remove_listener": _fake_remove_listener,
        "stop_local": _fake_stop_local,
    },
).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID),
             ["network", "write", "ai"])

page = plug.AiChatPage(page_ctx)
check("C1 页面构造不崩（真实 MainWindow host）", page is not None)
page.show()
pump()
check("C2 欢迎气泡已在流里", page._stream.count() == 2)   # stretch + 1 卡
from PyQt6.QtWidgets import QLabel as _QLabel  # noqa: E402
_welcome_lab = page._stream.itemAt(0).widget().findChild(_QLabel)
check("C2b 欢迎气泡文字完整（对齐项 wordWrap 高度已按实际宽校正）",
      _welcome_lab is not None
      and _welcome_lab.minimumHeight()
      >= _welcome_lab.heightForWidth(_welcome_lab.width()) - 2,
      f"minH={_welcome_lab.minimumHeight() if _welcome_lab else '?'} "
      f"needH={_welcome_lab.heightForWidth(_welcome_lab.width()) if _welcome_lab else '?'}")

check("C3 页面零后端配置 UI（后端收归设置页「AI 总配置」，页面只剩聊天/规则）",
      not hasattr(page, "_settings_card") and not hasattr(page, "_url_edit")
      and not hasattr(page, "_save_and_test"))

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

# ---- 本地服务状态跟随（2026-10-01 口径：后端收归设置页，页面只「听」广播）----
# 「保存并测试连接」已随配置移进设置页（由 verify_ai_backend.py 覆盖）；页面
# 侧保留的是「订阅宿主广播 + 快捷停止 + 参数实时直读」，用假 provider 验证。
check("C15 页面构造即订阅宿主本地服务状态（ctx.ai.add_listener）",
      len(_ai_listeners) == 1,          # 此时只有 page（page2 在 C16 后创建）
      f"listeners={len(_ai_listeners)}")
listeners_n0 = len(_ai_listeners)
page.destroyed.emit()          # 触发退订（真实销毁路径的等价操作）
listeners_n1 = len(_ai_listeners)
check("C16 页面销毁退订 ctx.ai listener（防死引用累积）",
      listeners_n1 == listeners_n0 - 1,
      f"{listeners_n0} -> {listeners_n1}")

# 就绪状态自动接后端（新页面重新挂 listener 验证页面响应）
page2 = plug.AiChatPage(page_ctx)
page2.show()
# 顶部独立窗口给足高度：C20 加完气泡内容也不溢出 → 纵向滚动条不出现。
# 否则气泡创建时（无滚动条）记下的 78% 最大宽，到 C22 断言时视口已被
# 滚动条压窄 14px，宽度基准漂移会让断言随机翻车（2026-09-28 实测）。
page2.resize(1000, 860)
pump()
for _fn in list(_ai_listeners):
    _fn("ready", "本地服务就绪（127.0.0.1:8093）")
pump()
check("C17 ready 广播 → 快捷行出现「⏹ 停止模型服务」+ 状态行反馈",
      page2._stop_model_btn.isVisible() and "就绪" in page2._status.text(),
      f"visible={page2._stop_model_btn.isVisible()} "
      f"status={page2._status.text()}")
page2._stop_model_btn.click()
pump()
check("C17b 快捷停止走 ctx.ai.stop_local（插件不自己管进程）",
      _stop_calls[0] >= 1, f"stop_calls={_stop_calls[0]}")
for _fn in list(_ai_listeners):
    _fn("stopped", "")   # 复位，别污染后面
pump()
check("C17c stopped 广播 → 停止按钮隐藏（不占聊天界面空间）",
      not page2._stop_model_btn.isVisible())

# 参数直读是**实时**的：改 providers 快照（=用户在设置页改配置），
# 下一发请求即用新值，页面无需重建
_n_live = len(captured)
_ai_params.update({"mode": "cloud", "base_url": "http://127.0.0.1:9/v1"})
page2._input.setPlainText("参数实时性")
page2._on_send_clicked()
pump(50)
check("C17d 设置页改后端 → 下一发请求即用新地址（params 每次实时读）",
      len(captured) == _n_live + 1
      and captured[-1]["url"].startswith("http://127.0.0.1:9/v1"),
      captured[-1]["url"] if captured else "无请求")
_ai_params.update({"mode": "local", "base_url": "http://127.0.0.1:8080/v1",
                   "local_ready": True})   # 还原本地

# 本地模式未就绪：只提示、绝不发出注定失败的请求
_ai_params.update({"mode": "local", "local_ready": False})
_n_gate = len(captured)
page2._input.setPlainText("本地未就绪")
page2._on_send_clicked()
pump(50)
_hints_gate = [w for w in page2._stream_host.findChildren(QFrame)
               if w.objectName() == "chatBubbleHint"]
check("C17e 本地未就绪：请求被拦（桥零调用）+ 提示气泡引导去设置页",
      len(captured) == _n_gate and len(_hints_gate) >= 1
      and any("设置" in lb.text()
              for w in _hints_gate for lb in w.findChildren(QLabel)),
      f"captured={len(captured)} hints={len(_hints_gate)}")
_ai_params.update({"mode": "local", "local_ready": True})   # 还原本地就绪
page2._clear_chat()   # 清掉上面两组探针气泡，别污染后面 C20 的计数断言
pump()
# ★ _clear_chat 的 deleteLater 是**延迟**删除：布局项立刻摘除（C18 的
#   count() 断言即时成立），但行容器 QWidget 仍挂在 _stream_host 下，
#   findChildren 能摸到"幽灵卡" —— 这里显式冲一次 DeferredDelete，
#   后面 C20/C26 系列按 findChildren 数卡才不会被探针残骸污染。
_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
_app.processEvents()

# ---- 左右气泡（2026-09-28 用户要求：一左一右对话式）----
page2.add_bubble("你", "测试用户消息")
page2.add_bubble("AI", "测试 AI 回复")
page2.add_bubble("提示", "测试提示消息")
pump(50)
# 气泡卡：新结构每行是一个 [stretch, card] 行容器（避开 alignment 不吃
# heightForWidth 的坑），故用 findChildren 按 objectName 取卡片
def _bubble_cards(obj_name: str):
    return [w for w in page2._stream_host.findChildren(QFrame)
            if w.objectName() == obj_name]


_user_cards = _bubble_cards("chatBubbleUser")
_ai_cards = _bubble_cards("chatBubbleAI")
_hint_cards = _bubble_cards("chatBubbleHint")
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


_row_lay = (_user_cards[0].parentWidget().layout()
            if _user_cards and _user_cards[0].parentWidget() else None)
check("C21 用户气泡靠右（行容器：[stretch, card]，卡片在最后）",
      _row_lay is not None and _row_lay.count() == 2
      and _row_lay.itemAt(0).spacerItem() is not None
      and _row_lay.itemAt(1).widget() is _user_cards[0],
      f"count={_row_lay.count() if _row_lay else '?'}")
check("C22 气泡是窄卡（最大宽 ≤ 可视区 78%，不撑满整行）",
      bool(_user_cards)
      and _user_cards[0].maximumWidth() <= max(
          360, int(page2._scroll.viewport().width() * 0.78)),
      f"maxW={_user_cards[0].maximumWidth() if _user_cards else '?'} "
      f"vpW={page2._scroll.viewport().width()}")

# ---- 气泡宽度/高度协调 + 存为笔记改右键菜单（2026-09-28 用户反馈）----
_long_ai = ("这是一条用于验证气泡宽度与换行高度的较长 AI 回复，"
            "包含足够文字让它在任何窗口宽度下都必须折行显示。") * 3
page2.add_bubble("AI", _long_ai)
pump(80)
_ai_now = _bubble_cards("chatBubbleAI")
_long_card = _ai_now[-1]
_long_label = _long_card.findChild(QLabel)
_vp = page2._scroll.viewport().width()
check("C26 长 AI 气泡宽度撑到上限附近（wordWrap sizeHint 塌缩已修）",
      _long_card.width() >= int(_vp * 0.6),
      f"w={_long_card.width()} vp={_vp}")
check("C27 长 AI 气泡高度覆盖全文（不截字）",
      _long_label is not None
      and _long_label.height()
      >= _long_label.heightForWidth(_long_label.width()) - 2,
      f"h={_long_label.height() if _long_label else '?'} "
      f"need={_long_label.heightForWidth(_long_label.width()) if _long_label else '?'}")
# 首张 AI 卡现为「对话已清空。」（_clear_chat 的收尾气泡，内容自然宽
# ~100px）；塌缩护栏取自绘地板 40px 的 2 倍，不再绑某条具体文案的宽度
check("C28 短 AI 气泡贴合内容（不塌缩成窄条、也不撑满）",
      bool(_ai_now) and _ai_now[0].width() >= 80
      and _ai_now[0].width() <= _long_card.width(),
      f"short={_ai_now[0].width() if _ai_now else '?'} long={_long_card.width()}")
check("C29 AI 气泡内不再有常驻按钮（存为笔记已改右键菜单）",
      not _long_card.findChildren(QPushButton),
      f"btns={[b.text() for b in _long_card.findChildren(QPushButton)]}")
check("C30 AI 气泡挂 CustomContextMenu（右键可存笔记）",
      _long_card.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu)
_menu_new = page2._build_bubble_menu("__some-new-text__")
check("C31 右键菜单「📥 存为笔记」可点（未存过）",
      len(_menu_new.actions()) == 1
      and "存为笔记" in _menu_new.actions()[0].text()
      and _menu_new.actions()[0].isEnabled(),
      str([a.text() for a in _menu_new.actions()]))
page2._noted_texts.add("__some-new-text__")
_menu_done = page2._build_bubble_menu("__some-new-text__")
check("C32 已存过 → 菜单项显示已存且禁用（防重复存）",
      "已存为笔记" in _menu_done.actions()[0].text()
      and not _menu_done.actions()[0].isEnabled(),
      str([f"{a.text()}/{a.isEnabled()}" for a in _menu_done.actions()]))
page2._noted_texts.discard("__some-new-text__")

# ---- 思考动画（2026-09-28 用户要求：请求在途时消息流里有活的三点波）----
# 挂起桥：捕获 on_done 但不回 → 模拟真实网络的在途窗口期
_deferred = []


def deferred_bridge(url, headers, body, timeout, on_done):
    _deferred.append(on_done)
    return True


page2._ctx._http_post_async = deferred_bridge
page2._input.setPlainText("在途测试")
page2._on_send_clicked()
pump(400)          # 跨 ≥12 个动画 tick（33ms/帧，v1.11.0 自绘三点）
_think_cards = [w for w in page2._stream_host.findChildren(QFrame)
                if w.objectName() == "chatBubbleThinking"]
_dots = page2._think_dots
_think_item = _item_of(_think_cards[0]) if _think_cards else None
check("C20b 请求在途 → 思考气泡挂进消息流（靠左）+ 自绘三点动画在跑且相位在推进",
      page2._busy and len(_think_cards) == 1
      and _dots is not None and _dots.is_animating()
      and _dots.phase_ms() > 0
      and bool(_think_item.alignment() & Qt.AlignmentFlag.AlignLeft),
      f"busy={page2._busy} cards={len(_think_cards)} "
      f"animating={_dots.is_animating() if _dots else None} "
      f"phase={_dots.phase_ms() if _dots else None}")
_deferred.pop()({"ok": True, "status": 200,
                 "body": json.dumps(
                     {"choices": [{"message": {"content": "回复到达"}}]})})
pump()
_still = [w for w in page2._stream_host.findChildren(QFrame)
          if w.objectName() == "chatBubbleThinking"]
check("C20c 回复到达 → 思考气泡拆除干净（动画停 + busy 复位 + 布局计数回落）",
      len(_still) == 0 and not page2._busy
      and page2._think_bubble is None and page2._think_dots is None,
      f"still={len(_still)} busy={page2._busy}")
page2._ctx._http_post_async = fake_bridge        # 换回正常假桥

# ----（C23-C25 云端断开闸门已随「后端收归设置页」移除：连接控制只在
#      设置页做，插件页不再有断开/探活入口——由 verify_ai_backend.py 覆盖）----

# ---- 规则库入口（2026-09-28 用户要求：入口让用户自己编辑，不写死）----
check("C26 规则库按钮存在且卡片默认收起",
      page2._rules_btn is not None and page2._rules_btn.text() == "📐 规则库"
      and not page2._rules_card.isVisible())
page2._toggle_rules()
pump(50)
check("C27 规则卡可展开；空规则库给一行可编辑空行",
      page2._rules_card.isVisible() and len(page2._rules_rows) == 1
      and page2._rules_rows[0]["edit"].text() == "")
page2._rules_rows[0]["edit"].setText("回答保持简洁，不超过 200 字")
page2._rule_add_btn.click()
pump()
check("C28 ＋ 添加规则 → 新空行（不被 checked=False 污染成 'False'）",
      len(page2._rules_rows) == 2 and page2._rules_rows[1]["edit"].text() == "")
page2._rules_rows[1]["edit"].setText("这条保持停用")
page2._rules_rows[1]["check"].setChecked(False)
page2._rule_add_btn.click()                          # 再加一行但不填：保存时应被跳过
pump()
page2._rules_rows[1]["edit"].returnPressed.emit()   # 文本框回车=直接保存
pump()
check("C29 保存规则落盘（空白行跳过 + 停用保留）",
      page2._cfg["custom_rules"] == [
          {"text": "回答保持简洁，不超过 200 字", "enabled": True},
          {"text": "这条保持停用", "enabled": False}],
      str(page2._cfg.get("custom_rules")))
_stored_rules = json.load(open(os.path.join(page_ctx.data_dir,
                                            "config.json"),
                               encoding="utf-8")).get("custom_rules")
check("C29b 规则写入插件私有 config.json",
      _stored_rules == page2._cfg["custom_rules"], str(_stored_rules))
n_before_rules = len(captured)
page2._input.setPlainText("规则生效了吗")
page2._on_send_clicked()
pump(50)
_sys_content = captured[-1]["body"]["messages"][0]["content"]
check("C30 对话请求 system 提示词：启用规则注入、停用规则不注入",
      len(captured) == n_before_rules + 1
      and _sys_content.startswith("你是办公工具")
      and "回答保持简洁，不超过 200 字" in _sys_content
      and "这条保持停用" not in _sys_content,
      _sys_content[-100:])
# （C31 旧「后端设置保存不冲掉规则」已随 _collect_settings 移除：
#   规则落盘独立于后端配置，C29/C29b 已覆盖持久化）
page2._toggle_rules()
pump(50)
check("C32 规则卡可收起（重开会从配置重建行）",
      not page2._rules_card.isVisible())

# ---- 数据操控（2026-09-28 用户要求：AI 按指令改数据 + 分级确认 + 可撤销）----
# 这条链路要真通道：写=增（write）、管理=改删（manage，带撤销栈）。
# 全部接到真管理器，动作真的落库，撤销真的恢复。
def _mk_manage_providers():
    stack, seq = [], [1]

    def _push(kind, payload):
        tok = seq[0]
        seq[0] += 1
        stack.append({"token": tok, "kind": kind, "payload": payload,
                      "used": False})
        return tok

    def _take(tok):
        for rec in stack:
            if rec["token"] == tok and not rec["used"]:
                rec["used"] = True
                return rec
        return None

    def _update_task(tid, title, note, deadline):
        cur = task_mgr.get_task(tid)
        if cur is None:
            return False
        d = cur.to_dict()
        return bool(task_mgr.update_task(
            tid, d["title"] if title is None else title,
            d["note"] if note is None else note,
            d["deadline"] if deadline is None else deadline))

    def _delete_task(tid):
        cur = task_mgr.get_task(tid)
        if cur is None:
            return 0
        payload = cur.to_dict()
        if not task_mgr.delete_task(tid):
            return 0
        return _push("task", payload)

    def _delete_fragment(fid):
        cur = frag_mgr.get_fragment(fid)
        if cur is None:
            return 0
        payload = cur.to_dict()
        if not frag_mgr.delete_fragment(fid):
            return 0
        return _push("fragment", payload)

    def _delete_note(nid):
        cur = note_mgr.get_note(nid)
        if cur is None:
            return 0
        payload = cur.to_dict()
        if not note_mgr.delete_note(nid):
            return 0
        return _push("note", payload)

    def _delete_knowledge(num, expect_hash):
        if _kb_blocked():
            return 0
        items = _kb_rows()
        if num < 1 or num > len(items) or items[num - 1].hash != expect_hash:
            return 0
        payload = {"text": items[num - 1].text, "pos": num - 1}
        if not docx_mgr.delete_paragraph(num - 1):
            return 0
        if not docx_mgr.save():
            docx_mgr.reload()
            return 0
        win.refresh_knowledge()
        return _push("knowledge", payload)

    def _undo(tok):
        rec = _take(tok)
        if rec is None:
            return False
        kind, p = rec["kind"], rec["payload"]
        if kind == "knowledge":
            # 原位恢复（结构化文档里位置本身就是信息）
            if _kb_blocked():
                return False
            text = p.get("text") or ""
            pos = int(p.get("pos") or 0)
            items = _kb_rows()
            if pos < 0 or pos > len(items):
                pos = len(items)
            if pos < len(items):
                new_idx = docx_mgr.insert_paragraph_before(pos, text)
            else:
                new_idx = docx_mgr.append_paragraph(text)
            if new_idx < 0:
                return False
            if not docx_mgr.save():
                docx_mgr.reload()
                return False
            win.refresh_knowledge()
            return True
        if kind == "task":
            nid = task_mgr.add_task(p.get("title") or "", p.get("note") or "",
                                    p.get("deadline") or "")
            if not nid:
                return False
            if p.get("done"):
                task_mgr.set_done(nid, True)
            return True
        if kind == "fragment":
            return bool(frag_mgr.add_fragment(
                p.get("type") or "clipboard_text", p.get("content") or "",
                p.get("source") or "撤销恢复"))
        return bool(note_mgr.add_note(p.get("content") or "",
                                      p.get("title") or ""))

    return {
        "update_task": _update_task,
        "set_task_done": lambda tid, done: bool(task_mgr.set_done(tid, done)),
        "delete_task": _delete_task,
        "update_fragment": lambda fid, content, source: bool(
            frag_mgr.update_fragment(fid, content=content, source=source)),
        "delete_fragment": _delete_fragment,
        "update_note": lambda nid, title, content: bool(note_mgr.update_note(
            nid, (note_mgr.get_note(nid).content if content is None
                  else content), title=title)),
        "delete_note": _delete_note,
        "update_knowledge": _update_knowledge,
        "delete_knowledge": _delete_knowledge,
        "undo_delete": _undo,
    }


_write_providers = {
    "task": lambda title, note, deadline:
        task_mgr.add_task(title, note, deadline),
    "fragment": lambda content, source:
        frag_mgr.add_clipboard_text(content, source=source),
    "note": lambda title, content: note_mgr.add_note(content, title),
}


# ---- 知识库通道（镜像 knowledge_ball 的实现，走真 DocxManager）----
def _kb_rows():
    return docx_mgr.get_paragraphs()


def _kb_blocked():
    """外部改动未重新加载时拒绝写（否则 save() 整篇回写会覆盖 Word 里的改动）"""
    return docx_mgr.check_external_modification()


def _add_knowledge(content):
    if _kb_blocked():
        return 0
    idx = docx_mgr.append_paragraph(content)
    if idx < 0:
        return 0
    if not docx_mgr.save():
        docx_mgr.reload()
        return 0
    win.refresh_knowledge()
    return idx + 1


def _update_knowledge(num, content, expect_hash):
    if _kb_blocked():
        return False
    items = _kb_rows()
    if num < 1 or num > len(items) or items[num - 1].hash != expect_hash:
        return False
    if not docx_mgr.update_paragraph_text(num - 1, content):
        return False
    if not docx_mgr.save():
        docx_mgr.reload()
        return False
    win.refresh_knowledge()
    return True


_write_providers["knowledge"] = _add_knowledge


def _mk_mgmt_ctx(caps):
    # 动作链路页同样要走 ctx.ai（后端收归设置页后，发送闸门先查接入与参数）
    caps = tuple(set(caps) | {"ai"})
    return PluginContext(
        logger=_logger, config=config.as_dict(),
        show_toast=lambda *a, **k: None, data=plugin_data,
        data_dir_base=os.path.join(data_dir, "plugins"),
        parent_window=lambda: win, http_post_async=fake_bridge,
        write_providers=_write_providers,
        manage_providers=_mk_manage_providers(),
        ai_providers={
            "is_attached": lambda pid: True,
            "params": lambda: dict(_ai_params),
            "add_listener": _fake_add_listener,
            "remove_listener": _fake_remove_listener,
            "stop_local": _fake_stop_local,
        },
    ).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID), caps)


def _cards(pg, obj):
    return [w for w in pg._stream_host.findChildren(QFrame)
            if w.objectName() == obj]


def _btns(pg, text):
    return [b for b in pg._stream_host.findChildren(QPushButton)
            if b.text() == text]


def _feed(pg, text):
    """模拟「模型回复到达」：走真实 _on_reply（含 parse_reply/parse_actions）"""
    pg._on_reply({"ok": True, "status": 200,
                  "body": json.dumps(
                      {"choices": [{"message": {"content": text}}]})})
    pump(30)


def _titles():
    return [t.title for t in task_mgr.get_all_tasks()]


pageM = plug.AiChatPage(_mk_mgmt_ctx(["network", "write", "manage"]))
pageM.resize(760, 640)
pageM.show()
pump(60)
check("C33 授权 manage → 页面识别可写可管（_can_write / _can_manage）",
      pageM._can_write() is True and pageM._can_manage() is True)

n_before = len(captured)
pageM._input.setPlainText("帮我加个任务")
pageM._on_send_clicked()
pump(50)
check("C34 授权 manage → 对话请求下发动作协议（system 提示词含协议段）",
      len(captured) == n_before + 1
      and "== 你可以执行的应用操作 ==" in
      captured[-1]["body"]["messages"][0]["content"])

# 新增类：直接执行，不弹确认
_n0 = len(_titles())
_feed(pageM, '好的，已为你创建。\n```actions\n'
      '{"actions":[{"op":"add_task","title":"AI 建的任务"}]}\n```')
_res = _cards(pageM, "chatBubbleHint")
_ai_text = " ".join(lb.text() for w in _cards(pageM, "chatBubbleAI")
                    for lb in w.findChildren(QLabel))
check("C35 add_task 直接执行（任务 +1、标题正确、正文剥离 JSON）",
      len(_titles()) == _n0 + 1 and "AI 建的任务" in _titles()
      and "actions" not in _ai_text and "{" not in _ai_text
      and _res and any("✅" in lb.text()
                       for lb in _res[-1].findChildren(QLabel)),
      f"titles={_titles()} res={len(_res)} ai={_ai_text!r}")

# 改删类：不直接执行，先弹确认卡
_victim = task_mgr.add_task("待删除的任务", "备注", "")
_n1 = len(_titles())
_feed(pageM, '```actions\n{"actions":[{"op":"delete_task","id":%d}]}\n```'
      % _victim)
_hint = _cards(pageM, "chatBubbleHint")
check("C36 delete_task 不直接执行（弹确认卡，数据未动）",
      len(_titles()) == _n1 and "待删除的任务" in _titles()
      and _btns(pageM, "执行") and _btns(pageM, "取消"),
      f"titles={_titles()} yes={len(_btns(pageM, '执行'))} "
      f"no={len(_btns(pageM, '取消'))}")
check("C37 确认卡列出将执行的操作（含目标记录标题）",
      any("待删除的任务" in lb.text() for lb in _hint[-1].findChildren(QLabel)),
      str([lb.text() for lb in _hint[-1].findChildren(QLabel)]))

# 点「执行」→ 真落库 + 结果卡带撤销按钮
_btns(pageM, "执行")[-1].click()
pump(30)
check("C38 点「执行」→ 真删除 + 结果卡带「↩ 撤销删除」",
      "待删除的任务" not in _titles() and _btns(pageM, "↩ 撤销删除"),
      f"titles={_titles()} undo={len(_btns(pageM, '↩ 撤销删除'))}")

# 点「撤销删除」→ 内容恢复
_btns(pageM, "↩ 撤销删除")[-1].click()
pump(30)
check("C39 点「↩ 撤销删除」→ 内容恢复（重新插入，标题原样回来）",
      "待删除的任务" in _titles(),
      f"titles={_titles()}")

# 点「取消」→ 不执行
_victim2 = task_mgr.add_task("不该被删的任务", "", "")
_feed(pageM, '```actions\n{"actions":[{"op":"delete_task","id":%d}]}\n```'
      % _victim2)
_btns(pageM, "取消")[-1].click()
pump(30)
check("C40 点「取消」→ 未执行（数据零改动）",
      "不该被删的任务" in _titles(), f"titles={_titles()}")

# 坏 JSON：整块不执行 + 提示气泡（不猜意图）
_n2 = len(_titles())
_feed(pageM, '我改好了\n```actions\n{"actions":[{"op":"add_task", }\n```')
check("C41 动作块坏 JSON → 零执行 + 出现「未被执行」提示气泡",
      len(_titles()) == _n2
      and any("未被执行" in lb.text()
              for w in _cards(pageM, "chatBubbleHint")
              for lb in w.findChildren(QLabel)),
      f"titles={_titles()}")

# 纯动作回复（正文本该为空）→ 不留 JSON 气泡（真截图发现的 bug 的页面级钉子）
_n3 = len(_titles())
_feed(pageM, '```actions\n{"actions":[{"op":"add_task","title":"静默新增"}]}\n```')
_ai_all = " ".join(lb.text() for w in _cards(pageM, "chatBubbleAI")
                   for lb in w.findChildren(QLabel))
check("C45 纯动作回复 → 不渲染 JSON 正文（只留结果卡）+ 动作照常执行",
      len(_titles()) == _n3 + 1 and "静默新增" in _titles()
      and "actions" not in _ai_all and "{" not in _ai_all,
      f"titles={_titles()} ai={_ai_all!r}")
pageM.deleteLater()

# 未授权 manage：不下发协议 + 改删被宿主安全拒绝（数据零改动）
pageN = plug.AiChatPage(_mk_mgmt_ctx(["network"]))
pageN.resize(760, 640)
pageN.show()
pump(60)
check("C42 未声明 manage → _can_manage 为假（不下发动作协议）",
      pageN._can_manage() is False and pageN._can_write() is False)
n_before2 = len(captured)
pageN._input.setPlainText("随便问问")
pageN._on_send_clicked()
pump(50)
check("C43 未声明 manage → 请求里没有动作协议段",
      len(captured) == n_before2 + 1
      and "== 你可以执行的应用操作 ==" not in
      captured[-1]["body"]["messages"][0]["content"])
_probe = task_mgr.add_task("未授权探测任务", "", "")
_feed(pageN, '```actions\n{"actions":[{"op":"delete_task","id":%d}]}\n```'
      % _probe)
check("C44 未声明 manage 却收到改删动作 → 如实拒绝（不弹确认卡 + 数据零改动）",
      "未授权探测任务" in _titles()
      and not _btns(pageN, "执行")
      and any("未授权" in lb.text()
              for w in _cards(pageN, "chatBubbleHint")
              for lb in w.findChildren(QLabel)),
      f"titles={_titles()} yes={len(_btns(pageN, '执行'))}")
pageN.deleteLater()

# ---- 知识库操作（2026-09-28 用户要求：增加 AI 对知识库的操作能力）----
# 与任务/碎片/笔记的关键差别：知识库用**编号（位置）**寻址，且 docx 允许
# 用户用 Word 外部编辑 → 改删必须带内容指纹，编号漂移时拒绝而不是改错段落。
pageK = plug.AiChatPage(_mk_mgmt_ctx(["network", "write", "manage"]))
pageK.resize(760, 640)
pageK.show()
pump(60)


def _kb_texts():
    return [p.text for p in docx_mgr.get_paragraphs()]


# 快照下发：「查知识库」快捷指令带编号清单
_n_kb = len(captured)
pageK.send_quick("knowledge", "请概括知识库内容")
pump(50)
_kb_body = captured[-1]["body"]["messages"][-1]["content"]
check("C46 查知识库快捷指令：请求携带编号清单（编号来自快照，不是模型编的）",
      len(captured) == _n_kb + 1
      and "[1] 知识库第一条内容" in _kb_body
      and "[4] 知识库第四条内容" in _kb_body
      and "编号" in _kb_body,
      _kb_body[-160:])

# 新增：直接执行，不带确认
_kb_before = _kb_texts()
_feed(pageK, '好的。\n```actions\n'
      '{"actions":[{"op":"add_knowledge","content":"AI 追加的知识条目"}]}\n```')
check("C47 add_knowledge 直接执行：docx 真的多了一段且编号正确",
      _kb_texts() == _kb_before + ["AI 追加的知识条目"]
      and any("5" in lb.text() and "知识库" in lb.text()
              for w in _cards(pageK, "chatBubbleHint")
              for lb in w.findChildren(QLabel)),
      f"kb={_kb_texts()}")

# 改写：走确认卡，点「执行」才落库
_kb_before = _kb_texts()
_feed(pageK, '```actions\n{"actions":[{"op":"update_knowledge","id":2,'
      '"content":"第二条已被 AI 改写"}]}\n```')
_kb_hint_cards = _cards(pageK, "chatBubbleHint")
_kb_hint_text = " ".join(lb.text() for lb in _kb_hint_cards[-1].findChildren(
    QLabel)) if _kb_hint_cards else ""
check("C48a update_knowledge 先弹确认卡（未执行，docx 零改动）",
      _kb_texts() == _kb_before and _btns(pageK, "执行")
      and "待确认" in _kb_hint_text
      and "知识库第二条内容" in _kb_hint_text,
      f"kb={_kb_texts()} hint={_kb_hint_text!r}")
_btns(pageK, "执行")[-1].click()
pump(30)
check("C48b 点执行 → docx 段落文本真的被改写（落盘生效）",
      _kb_texts() == ["知识库第一条内容", "第二条已被 AI 改写",
                      "知识库第三条内容", "知识库第四条内容",
                      "AI 追加的知识条目"],
      f"kb={_kb_texts()}")

# 指纹过期保护：确认卡弹出后该段被改过 → 执行时必须拒绝（不误删/误改）
_kb_before = _kb_texts()
_feed(pageK, '```actions\n{"actions":[{"op":"delete_knowledge","id":1}]}\n```')
docx_mgr.update_paragraph_text(0, "第一条被外部改掉了")     # 模拟引用已过期
_btns(pageK, "执行")[-1].click()
pump(30)
check("C49 指纹过期 → 拒绝执行（编号漂移时绝不改错段落）",
      _kb_texts() == ["第一条被外部改掉了", "第二条已被 AI 改写",
                      "知识库第三条内容", "知识库第四条内容",
                      "AI 追加的知识条目"],
      f"kb={_kb_texts()}")

# 删除 + 撤销：撤销要**放回原位**
_kb_before = _kb_texts()
_feed(pageK, '```actions\n{"actions":[{"op":"delete_knowledge","id":3}]}\n```')
_btns(pageK, "执行")[-1].click()
pump(30)
check("C50a 删除知识库段落落盘（该段消失 + 结果卡带撤销按钮）",
      "知识库第三条内容" not in _kb_texts()
      and len(_kb_texts()) == len(_kb_before) - 1
      and _btns(pageK, "↩ 撤销删除"),
      f"kb={_kb_texts()}")
_btns(pageK, "↩ 撤销删除")[-1].click()
pump(30)
check("C50b 撤销 → 内容与**位置**都恢复（不是追加到末尾）",
      _kb_texts() == _kb_before,
      f"got={_kb_texts()} want={_kb_before}")

# 同批多个删除：编号升序给出，仍须全部成功（靠降序执行）
_kb_before = _kb_texts()
_feed(pageK, '```actions\n{"actions":['
      '{"op":"delete_knowledge","id":1},'
      '{"op":"delete_knowledge","id":3}]}\n```')
_btns(pageK, "执行")[-1].click()
pump(30)
check("C51 同批两个删除（升序编号）→ 全部成功（内部按编号降序执行）",
      _kb_texts() == ["第二条已被 AI 改写", "知识库第四条内容",
                      "AI 追加的知识条目"],
      f"kb={_kb_texts()}")
_kb_ok = [lb.text() for w in _cards(pageK, "chatBubbleHint")
          for lb in w.findChildren(QLabel)]
check("C51b 结果卡两条都是成功（没有因编号前移而失败）",
      sum(1 for t in _kb_ok if t.startswith("✅ 已删除")) >= 2,
      str(_kb_ok[-3:]))

# 太短的内容会被拒（docx 段落最小 4 字）——如实报告失败而不是静默
_feed(pageK, '```actions\n{"actions":[{"op":"add_knowledge","content":"短"}]}\n```')
check("C52 过短内容被拒：如实报失败 + 提示可能原因",
      not any(t == "短" for t in _kb_texts())
      and any("✗" in lb.text() and "知识库" in lb.text()
              for w in _cards(pageK, "chatBubbleHint")
              for lb in w.findChildren(QLabel)),
      f"kb={_kb_texts()}")
pageK.deleteLater()

# ---- 清空 + 配置落盘 ----
page2._clear_chat()
pump()
check("C18 清空对话：历史与气泡都清（欢迎语重新出现）",
      page2._history == [] and page2._stream.count() == 2)

# 配置落盘口径（2026-10-01）：插件私有 config.json 只存聊天/规则等自有
# 字段；后端键（mode/base_url/api_key/model/local_*/cloud_*/backend_mode）
# 一律收归设置页「AI 总配置」，插件侧不落盘
cfg_path = os.path.join(page_ctx.data_dir, "config.json")
stored = json.load(open(cfg_path, encoding="utf-8"))
_BACKEND_KEYS = {"backend_mode", "cloud_base_url", "cloud_api_key",
                 "cloud_model", "mode", "base_url", "api_key", "model",
                 "local_port", "local_ready"}
check("C19 插件配置零后端键（后端收归设置页 AI 总配置）",
      not (_BACKEND_KEYS & set(stored)),
      f"残留={sorted(_BACKEND_KEYS & set(stored))} keys={sorted(stored)}")
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
saved_actions = []
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
        write_providers=_write_providers,
        manage_providers=_mk_manage_providers(),
        ai_providers={
            "is_attached": lambda pid: True,
            "params": lambda: dict(_ai_params),
            "add_listener": _fake_add_listener,
            "remove_listener": _fake_remove_listener,
            "stop_local": _fake_stop_local,
        },
    ).for_plugin(PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID),
                 ["network", "write", "manage", "ai"])
    page_t = plug.AiChatPage(ctx_t)
    # 真实时序：先注入主窗口（reparent 到 QSS 作用域内）再显示页面；
    # 反过来先 show 会让页面先成为无 QSS 祖先的顶层窗口，离屏下样式残留
    page_t._toggle_rules()             # 展开规则卡：截图信息量更足
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
    # 第二批截图：数据操控卡（结果卡 + 确认卡 + 撤销按钮）——2026-09-28 新 UI，
    # 必须真出图人工核对双主题（仅靠断言看不出配色/截断问题）
    page_t._toggle_rules()             # 收起规则卡，让消息流占满可视区
    pump(120)
    _feed(page_t, '好的，已建好任务。\n```actions\n'
          '{"actions":[{"op":"add_task","title":"整理本周会议纪要"}]}\n```')
    del_id = task_mgr.add_task("要合并的重复条目", "", "")
    _feed(page_t, '```actions\n{"actions":['
          '{"op":"complete_task","id":%d},'
          '{"op":"delete_task","id":%d}]}\n```' % (done_id, del_id))
    pump(250)
    apath = os.path.join(shots, f"ai_assistant_actions_{theme}.png")
    if win_t.grab().save(apath):
        saved_actions.append(theme)
    page_t.deleteLater()
    win_t.close()
    pump(100)
check("D1 light/dark 双主题截图落盘 build/shots/", saved == ["light", "dark"],
      str(saved))
check("D2 双主题「数据操控卡」截图落盘（确认卡/结果卡/撤销按钮）",
      saved_actions == ["light", "dark"], str(saved_actions))

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
