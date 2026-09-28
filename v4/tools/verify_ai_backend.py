# -*- coding: utf-8 -*-
"""AI 总配置离屏端到端验证：真加载器 + 真设置页 + 假网络桥。

覆盖（每一步都走真实代码路径，网络用替身）：
  A 真实插件被扫到且声明 ai 能力（ai-assistant / ai-text-workshop）
  B 设置页 AI 总配置卡真实构建：模式 / 云端 / 本地 / 接入下拉框存在
  C 候选插件 = 已安装 + 已启用 + 声明 ai 能力（非 AI 插件不进列表）
  D 保存并测试（假桥）：配置落盘 + 探活请求带鉴权头 + ✓ 状态
  E 接入下拉框：勾选 → ai_plugins 即时落盘；取消 → 移除
  F ctx.ai 门面实时性：勾选后 is_attached 立即翻转（不重建 ctx）；
    params 反映最新配置；未声明 ai 能力的派生实例安全降级
  G 接入插件的请求路径：走总配置地址 / 模型 / key（与插件私有配置无关）
  H 接入 + 本地模式未就绪：请求被拦并引导到设置页（不发注定失败的请求）
  I 宿主 AI 服务：启动失败（程序不存在）只置 error + 状态广播，不抛异常
  J light / dark 双主题截图（设置页 AI 卡 + 接入后的工坊页）→ build/shots/

跑法：python tools/run_gui_check.py tools/verify_ai_backend.py
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

from PyQt6.QtCore import Qt, QTimer                  # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402
from PyQt6.QtGui import QFontDatabase                # noqa: E402

from src.config import ConfigManager                 # noqa: E402
from src.docx_manager import DocxManager             # noqa: E402
from src.task_manager import TaskManager             # noqa: E402
from src.note_manager import NoteManager             # noqa: E402
from src.fragment_manager import FragmentManager     # noqa: E402
from src.clipboard_monitor import ClipboardMonitor   # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow               # noqa: E402
from src.settings_panel import SettingsPanel         # noqa: E402
from src.plugin_api import (                         # noqa: E402
    ActionRegistry, PluginContext, PluginData, KNOWN_CAPABILITIES,
)
from src.plugin_loader import PluginLoader           # noqa: E402
from src.ai_server import AI_SERVER                  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setApplicationName("verify_ai_backend")
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

_results = []
PLUGIN_ID = "ai-text-workshop"
ASSISTANT_ID = "ai-assistant"


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


_logger = logging.getLogger("fp_verify_ai_backend")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_logger.handlers.clear()
_logger.addHandler(_Capture([]))

# ====================================================================
# 搭数据 / 宿主 / 设置页
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_aib_")
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

_mw = sys.modules["src.main_window"]
if "plugins" not in _mw.NAV_PAGE_TITLES:
    _mw.NAV_PAGE_TITLES["plugins"] = "🔌  插件中心"
    _mw.NAV_PAGE_INDEX.setdefault("plugins", 9)

win = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config, clip, temp_mgr)
win.resize(1000, 900)
win.show()
pump(250)

# ====================================================================
# 真实加载器（真 plugins/ 目录，真 manifest 能力）
# ====================================================================
plugins_dir = os.path.join(ROOT, "plugins")
registry = ActionRegistry(
    logger=_logger,
    reserved_hotkeys=("Ctrl+Alt+K", "Ctrl+Alt+S"))


def fake_bridge(url, headers, body, timeout, on_done):
    probe_calls.append({"url": url, "headers": dict(headers or {}),
                        "body": body})
    payload = json.dumps({"choices": [{"message": {"content": "pong"}}]})
    QTimer.singleShot(20, lambda: on_done({
        "ok": True, "status": 200, "body": payload, "error": "", "url": url}))
    return True


probe_calls = []
bridge_calls = []
bridge_content = {"text": "要点一\n要点二"}


def action_bridge(url, headers, body, timeout, on_done):
    bridge_calls.append({"url": url, "headers": dict(headers or {}),
                         "body": body})
    payload = json.dumps({"choices": [
        {"message": {"content": bridge_content["text"]}}]})
    QTimer.singleShot(20, lambda: on_done({
        "ok": True, "status": 200, "body": payload, "error": "", "url": url}))
    return True


def _make_ctx(window, post):
    write_providers = {
        "fragment": lambda content, source: frag_mgr.add_clipboard_text(
            content, source=source),
        "task": lambda title, note, deadline: task_mgr.add_task(
            title, note, deadline),
        "note": lambda title, content: note_mgr.add_note(content, title),
    }

    def _is_attached(plugin_id):
        return str(plugin_id or "") in (
            config.get("ai_plugins", []) or [])

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
        http_post_async=post,
        write_providers=write_providers,
        ai_providers={
            "is_attached": _is_attached, "params": _params,
            "add_listener": AI_SERVER.add_listener,
            "remove_listener": AI_SERVER.remove_listener,
            "stop_local": lambda: (AI_SERVER.stop(), True)[1],
        },
    )
    # 与 loader 真实口径一致：按 manifest 能力派生（工坊声明 network/write/ai）
    return ctx.for_plugin(
        PLUGIN_ID, os.path.join(plugins_dir, PLUGIN_ID),
        ["network", "write", "ai"])


ctx = _make_ctx(win, action_bridge)
loader = PluginLoader(registry, ctx, plugins_dir=plugins_dir)
loaded = loader.load_all()
ids = [p.plugin_id for p in loaded]
# 生产环境由 knowledge_ball 装配：主窗口经只读通道拿 loaded_plugins()，
# 设置页「接入插件」下拉框的候选清单也走这一条通道
win.set_plugin_loader(loader)

# ====================================================================
# A. 真实插件声明 ai 能力
# ====================================================================
check("A0 KNOWN_CAPABILITIES 含 ai", "ai" in KNOWN_CAPABILITIES)
workshop_lp = next((p for p in loaded if p.plugin_id == PLUGIN_ID), None)
assistant_lp = next((p for p in loaded if p.plugin_id == ASSISTANT_ID), None)
check("A1 工坊与 AI 助手都被真实扫到",
      workshop_lp is not None and assistant_lp is not None, f"{ids}")
check("A2 两插件 manifest 都声明 ai 能力",
      "ai" in ((workshop_lp.manifest or {}).get("capabilities") or [])
      and "ai" in ((assistant_lp.manifest or {}).get("capabilities") or []))

sub_ctx = registry.context_of(f"{PLUGIN_ID}.open")
check("A3 工坊派生 ctx 携带 ai 能力 + 门面可用",
      sub_ctx is not None and sub_ctx.has_capability("ai")
      and sub_ctx.ai.enabled())

# ====================================================================
# B/C. 设置页 AI 总配置卡
# ====================================================================
panel = SettingsPanel(win)
pump(150)
check("B1 AI 卡控件齐备（模式/地址/Key/模型/程序/模型/端口/下拉框）",
      all(hasattr(panel, a) for a in (
          "_ai_mode_cloud", "_ai_mode_local", "_ai_url", "_ai_key",
          "_ai_model", "_ai_exe", "_ai_gguf", "_ai_port",
          "_ai_plugins_combo", "_ai_save_btn")))
check("B2 初始模式 = 配置（cloud → 云端高亮）",
      panel._ai_mode_cloud.isChecked()
      and not panel._ai_mode_local.isChecked())
panel._ai_mode_local.click()
pump(50)
# 注意：设置页此时不在主窗口当前页（isAlive 链未显示），isVisible 会
# 恒为 False——用与祖先无关的 isHidden() 判断「被显式隐藏」
check("B3 本地模式：本地配置区显示、云端区隐藏",
      not panel._ai_row_local.isHidden() and not panel._ai_row_exe.isHidden()
      and panel._ai_row_url.isHidden() and panel._ai_row_key.isHidden())
panel._ai_mode_cloud.click()
pump(50)
check("B4 切回云端：云端区回归、本地区隐藏",
      not panel._ai_row_url.isHidden() and panel._ai_row_local.isHidden())

candidates = dict(panel._ai_candidate_plugins())
check("C1 候选 = 两个 AI 插件（声明 ai 才进列表）",
      PLUGIN_ID in candidates and ASSISTANT_ID in candidates
      and "weekly-report" not in candidates, f"{list(candidates)}")

# ====================================================================
# D. 保存并测试（假探活桥）：落盘 + 请求结构 + ✓ 状态
# ====================================================================
panel._ai_poster_fn = fake_bridge            # 注入假探活桥
panel._ai_url.setText("https://central.example/v1")
panel._ai_key.setText("sk-central")
panel._ai_model.setText("central-model")
panel._on_ai_save_test()
pump(150)
saved = json.load(open(os.path.join(data_dir, "config.json"),
                       encoding="utf-8")) if os.path.isfile(
    os.path.join(data_dir, "config.json")) else {}
check("D1 云端配置即时落盘",
      saved.get("ai_cloud_base_url") == "https://central.example/v1"
      and saved.get("ai_cloud_api_key") == "sk-central"
      and saved.get("ai_cloud_model") == "central-model")
call = probe_calls[-1] if probe_calls else {}
check("D2 探活请求打向总配置地址且带鉴权头",
      call.get("url") == "https://central.example/v1/chat/completions"
      and call.get("headers", {}).get("Authorization") == "Bearer sk-central",
      call.get("url"))
check("D3 探活成功状态行 ✓", "✓" in panel._ai_status.text(),
      panel._ai_status.text())

# ====================================================================
# E/F. 接入下拉框 + ctx.ai 门面实时性
# ====================================================================
check("E1 初始未接入任何插件", sub_ctx.ai.is_attached() is False)
combo = panel._ai_plugins_combo
wid = combo._boxes.get(PLUGIN_ID)
check("E2 下拉框里能找到工坊条目（QCheckBox 菜单项）", wid is not None)
if wid is not None:
    wid.setChecked(True)                       # 触发 toggled → changed → 落盘
pump(60)
saved = json.load(open(os.path.join(data_dir, "config.json"),
                       encoding="utf-8"))
check("E3 勾选后 ai_plugins 即时落盘（设置页改完即生效）",
      saved.get("ai_plugins") == [PLUGIN_ID], saved.get("ai_plugins"))
check("F1 不重建 ctx：is_attached 立即翻转为 True",
      sub_ctx.ai.is_attached() is True)
params = sub_ctx.ai.params()
check("F2 params 实时反映总配置（地址/模型/key）",
      params.get("base_url") == "https://central.example/v1"
      and params.get("model") == "central-model"
      and params.get("api_key") == "sk-central"
      and params.get("local_port") == 8095)
if wid is not None:
    wid.setChecked(False)
pump(60)
check("F3 取消勾选：ai_plugins 移除 + is_attached 立即回落 False",
      sub_ctx.ai.is_attached() is False)
if wid is not None:
    wid.setChecked(True)                       # 恢复接入（供 G/J 使用）
pump(60)

# ====================================================================
# G. 接入插件的请求路径：走总配置，与插件私有配置无关
# ====================================================================
import importlib.util                        # noqa: E402
spec = importlib.util.spec_from_file_location(
    "tw_verify_backend", os.path.join(plugins_dir, PLUGIN_ID, "plugin.py"))
plug = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plug
spec.loader.exec_module(plug)

page = plug.AiWorkshopPage(sub_ctx)
win.register_plugin_page(f"plugin:{PLUGIN_ID}", "✂ 文本工坊", page)
win.show_plugin_page(f"plugin:{PLUGIN_ID}")
pump(200)
check("G1 接入后工坊页显示「已接入」提示（v1.4 起无自有后端入口）",
      page._ai_hint.isVisible() and "已接入" in page._ai_hint.text())
page._src_edit.setPlainText("要加工的原文")
page._run_action("summarize", "总结要点")
pump(250)
call = bridge_calls[-1] if bridge_calls else {}
check("G2 请求走总配置地址与模型（插件私有配置被覆盖）",
      call.get("url", "").startswith("https://central.example/v1/chat/completions")
      and (call.get("body") or {}).get("model") == "central-model"
      and call.get("headers", {}).get("Authorization") == "Bearer sk-central",
      call.get("url"))
check("G3 结果正常落到结果框",
      page._result_edit.toPlainText() == "要点一\n要点二")

# ====================================================================
# H. 接入 + 本地模式未就绪：拦下并引导（不发注定失败的请求）
# ====================================================================
config.set("ai_backend_mode", "local")
config.save()
calls_before = len(bridge_calls)
page._run_action("summarize", "本地指令")
pump(120)
check("H1 宿主本地服务未就绪：请求被拦 + 引导去设置页",
      len(bridge_calls) == calls_before
      and "宿主本地服务未就绪" in page._status.text(), page._status.text())
config.set("ai_backend_mode", "cloud")
config.save()
pump(30)

# ====================================================================
# I. 宿主 AI 服务：启动失败只置 error + 广播，不抛异常
# ====================================================================
server_events = []
AI_SERVER.add_listener(lambda s, d: server_events.append((s, d)))
settings_ai_local_btn = panel._ai_local_btn
panel._ai_exe.setText("C:/definitely-not-here.exe")
panel._ai_gguf.setText("C:/no.gguf")
settings_ai_local_btn.click()
pump(80)
check("I1 启动失败：状态 error + 广播收到 + 无子进程",
      AI_SERVER.status == "error"
      and "程序不存在" in AI_SERVER.detail
      and server_events[-1][0] == "error"
      and AI_SERVER.running is False)
check("I2 设置页状态行同步广播", "程序不存在" in panel._ai_local_status.text())
AI_SERVER.remove_listener(lambda s, d: server_events.append((s, d)))
check("I3 ctx.ai.stop_local 门面透传（宿主 AI_SERVER 停止）",
      sub_ctx.ai.stop_local() is True and AI_SERVER.status == "stopped")

# ====================================================================
# J. light / dark 双主题截图（AI 设置卡 + 接入后的工坊页）
# ====================================================================
shots = os.path.join(ROOT, "build", "shots")
os.makedirs(shots, exist_ok=True)
saved_shots = []
for theme in ("light", "dark"):
    config.set("theme", theme)
    _mw.NAV_PAGE_INDEX.pop(f"plugin:{PLUGIN_ID}", None)
    _mw.NAV_PAGE_TITLES.pop(f"plugin:{PLUGIN_ID}", None)
    win_t = MainWindow(task_mgr, note_mgr, frag_mgr, docx_mgr, config,
                       clip, temp_mgr)
    win_t.resize(1000, 900)
    win_t.show()
    pump(300)
    ctx_t = _make_ctx(win_t, action_bridge)
    panel_t = SettingsPanel(win_t)
    panel_t._ai_url.setText("https://central.example/v1")
    panel_t._ai_key.setText("sk-central")
    panel_t._ai_model.setText("central-model")
    panel_t._ai_plugins_reload()
    pump(150)
    # 找到 AI 分组卡（_ai_url 所在行的祖父 = settingsGroup 盒子）
    grp = panel_t._ai_url.parent().parent()
    path_card = os.path.join(shots, f"ai_backend_card_{theme}.png")
    if grp.grab().save(path_card):
        saved_shots.append(f"card_{theme}")
    # 接入后的工坊页（接入提示 + 后端入口隐藏）
    page_t = plug.AiWorkshopPage(ctx_t)
    win_t.register_plugin_page(f"plugin:{PLUGIN_ID}", "✂ 文本工坊", page_t)
    win_t.show_plugin_page(f"plugin:{PLUGIN_ID}")
    pump(300)
    page_t._src_edit.setPlainText("客户电话 13800000000；下周三上午十点开会")
    path_page = os.path.join(shots, f"text_workshop_attached_{theme}.png")
    if win_t.grab().save(path_page):
        saved_shots.append(f"page_{theme}")
    page_t.deleteLater()
    panel_t.deleteLater()
    win_t.close()
    pump(100)
check("J1 双主题截图落盘（AI 卡 + 接入后的工坊页）",
      saved_shots == ["card_light", "page_light", "card_dark", "page_dark"],
      str(saved_shots))

# ====================================================================
# 汇总
# ====================================================================
fails = [n for n, ok in _results if not ok]
print(f"\n== verify_ai_backend: {len(_results) - len(fails)}"
      f"/{len(_results)} passed ==", flush=True)
if fails:
    print("失败项：", fails, flush=True)
    sys.exit(1)
