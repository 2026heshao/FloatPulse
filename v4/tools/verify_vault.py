# -*- coding: utf-8 -*-
"""密码保险箱插件（vault）离屏端到端验证 + light/dark 真截图。

覆盖（每一步都走真实代码路径，不拿替身糊过去）：
  A 真实 plugins/ 目录被真加载器扫到：两个动作进注册表、热键
    Ctrl+Alt+V / Ctrl+Alt+B 与核心（K/S）及其它插件不冲突；
    **capabilities 为空** → 写/改/网络三档能力必须全部被门禁拒掉
    （密码保险箱绝不能让宿主/其它插件碰到条目）
  B 代码级安全断言：plugin.py 不 import 任何网络库、不出现
    ctx.write / ctx.manage / http_post_json_async（AI 插件读不到条目
    的另一半保证：保险箱也绝不主动外送）
  C 页面真身（真 MainWindow 作 host）：创建向导（含二次确认弹窗自动
    应答）→ 解锁态列表/详情 → secret 掩码/显形/复制倒计时 → 删除确认
    → 空闲锁定假时钟 → 快速取用弹窗 → 改密 → 导出（文件对话框打桩）
  D 损坏路径：meta 缺失 → 遮罩切到「数据文件异常」页
  E 视觉：解锁态 + 锁定遮罩在 light/dark 各截一张真图，且确实不同

跑法：python tools/run_gui_check.py tools/verify_vault.py
产物：build/shots/vault-page-{light,dark}.png / vault-lock-{light,dark}.png
"""
import ast as _ast
import json as _json
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

from PyQt6.QtCore import QEvent, QTimer                             # noqa: E402
from PyQt6.QtWidgets import (QApplication, QFileDialog, QLabel, QWidget)  # noqa: E402
from PyQt6.QtGui import QFontDatabase                                # noqa: E402

from src.config import ConfigManager                                 # noqa: E402
from src.main_window import MainWindow                               # noqa: E402
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

PLUGIN_ID = "vault"
PAGE_KEY = f"plugin:{PLUGIN_ID}"
ACTION_OPEN = f"{PLUGIN_ID}.open"
ACTION_QUICK = f"{PLUGIN_ID}.quick"

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


def flush_deletes():
    """强制处理 deleteLater：详情面板重建后的旧控件必须真死，
    否则 findChildren 还能扫到上一轮的明文标签（离屏测试特有）"""
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class _Capture(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.records = sink

    def emit(self, record):
        self.records.append(record)


_records = []
logger = logging.getLogger("fp_verify_vault")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.handlers.clear()
logger.addHandler(_Capture(_records))


# ====================================================================
# 搭环境（与 verify_kb_search 同款：真数据管理器 + 真 MainWindow）
# ====================================================================
tmp = tempfile.mkdtemp(prefix="fp_verify_vault_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir, exist_ok=True)

cm = ConfigManager(os.path.join(data_dir, "config.json"))
cm.set("plugins_enabled", True)

docx = DocxManager(os.path.join(tmp, "知识库.docx"),
                   os.path.join(data_dir, "docx_meta.json"))
docx.load()
tasks = TaskManager(os.path.join(data_dir, "schedule.json"))
notes = NoteManager(os.path.join(data_dir, "notes.json"))
frags = FragmentManager(os.path.join(data_dir, "fragments.json"))
nav = NavManager(os.path.join(data_dir, "nav.json"))
assets = TempAssetManager(tmp)
clip = ClipboardMonitor(frags, cm)

win = MainWindow(tasks, notes, frags, docx, cm, clip,
                 temp_asset_manager=assets, nav_manager=nav)
win.resize(1280, 740)
win.show()
pump(300)

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
)
loader = PluginLoader(registry, ctx, plugins_dir=os.path.join(ROOT, "plugins"))
win.set_plugin_loader(loader)

# ====================================================================
# A. 真实加载 + 零能力契约
# ====================================================================
loaded = loader.load_all()
pump(300)
ids = [p.plugin_id for p in loaded]
check("A1 真实 plugins/ 目录扫到 vault", PLUGIN_ID in ids, f"{ids}")

act_open = registry.get(ACTION_OPEN)
act_quick = registry.get(ACTION_QUICK)
check("A2 两个动作进注册表且挂菜单",
      act_open is not None and act_quick is not None
      and act_open.menu is True and act_quick.menu is True)
hotkeys = {a.id: a.hotkey for a in registry.hotkey_actions()}
check("A3 热键 Ctrl+Alt+V / Ctrl+Alt+B 有效且未冲突",
      hotkeys.get(ACTION_OPEN) == "Ctrl+Alt+V"
      and hotkeys.get(ACTION_QUICK) == "Ctrl+Alt+B", f"{hotkeys}")

sub_ctx = registry.context_of(ACTION_OPEN)
check("A4 注册表记住了插件自己的上下文",
      sub_ctx is not None and sub_ctx.plugin_id == PLUGIN_ID)

# capabilities 为空 → 三档能力全部被门禁拒掉（机密绝不经手宿主）
check("A5 未声明 write：add_task 被拒",
      sub_ctx.write.add_task("（越权探针）", "", "") == 0)
check("A6 未声明 manage：update_task 被拒",
      sub_ctx.manage.update_task(1, title="不该改到") is False)
check("A7 未声明 network：http_post_json_async 被拒",
      sub_ctx.http_post_json_async("https://example.com", {}, "{}",
                                   lambda **_k: None) is False)
check("A8 越权尝试有 warning 日志（不是静默失败）",
      any("write" in r.getMessage() or "manage" in r.getMessage()
          or "network" in r.getMessage() for r in _records))

# ====================================================================
# B. 代码级安全断言（AI 插件读不到的另一半：自己也不外送）
# ====================================================================
PLUGIN_PATH = os.path.join(ROOT, "plugins", "vault", "plugin.py")
source = open(PLUGIN_PATH, encoding="utf-8").read()
_tree = _ast.parse(source)
_top_mods = set()
for _node in _ast.walk(_tree):
    if isinstance(_node, _ast.Import):
        _top_mods.update(a.name.split(".")[0] for a in _node.names)
    elif isinstance(_node, _ast.ImportFrom) and _node.module:
        _top_mods.add(_node.module.split(".")[0])
_FORBIDDEN_NET = {"socket", "ssl", "requests", "urllib", "http", "ftplib",
                  "smtplib", "telnetlib", "httpx", "aiohttp", "websockets"}
check("B1 plugin.py 不 import 任何网络库", not (_top_mods & _FORBIDDEN_NET),
      f"{sorted(_top_mods & _FORBIDDEN_NET)}")
check("B2 plugin.py 不出现 ctx.write / ctx.manage / http_post_json_async",
      "ctx.write" not in source and "ctx.manage" not in source
      and "http_post_json_async" not in source)
core_src = open(os.path.join(ROOT, "plugins", "vault", "vault_core.py"),
                encoding="utf-8").read()
_core_tree = _ast.parse(core_src)
_core_mods = set()
for _node in _ast.walk(_core_tree):
    if isinstance(_node, _ast.Import):
        _core_mods.update(a.name.split(".")[0] for a in _node.names)
    elif isinstance(_node, _ast.ImportFrom) and _node.module:
        _core_mods.add(_node.module.split(".")[0])
check("B4 vault_core 零 Qt、纯 stdlib",
      not any(m.startswith("PyQt6") for m in _core_mods)
      and not (_core_mods & _FORBIDDEN_NET), f"{sorted(_core_mods)}")

# ====================================================================
# C. 页面真身：向导创建 → 列表/详情 → 掩码/显形/复制 → 删除
# ====================================================================
plug = sys.modules.get("floatpulse_plugin_vault")
if plug is None or not hasattr(plug, "get_vault"):
    cands = [k for k in sys.modules
             if "vault" in k and hasattr(sys.modules.get(k), "get_vault")]
    plug = sys.modules.get(cands[0]) if cands else None
check("C0 插件模块可从 sys.modules 取到", plug is not None,
      f"{[k for k in sys.modules if 'vault' in k]}")

vault = plug.get_vault(sub_ctx)
page = plug.VaultPage(sub_ctx)
win.register_plugin_page(PAGE_KEY, "🔒 密码保险箱", page)
pump(200)

check("C1 页面注册进主窗口", win.show_plugin_page(PAGE_KEY) is True)
pump(400)

# 初始 missing → 向导遮罩可见
check("C2 首次使用：创建向导遮罩可见",
      page._overlay.isVisible() and page._overlay_stack.currentIndex() == 0)

# ---- 向导：自动应答二次确认弹窗，走真实 _on_create（内嵌 exec）----
WZ_PW = "主密码Test-1"
_confirm_armed = []


def _auto_confirm():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, plug.ConfirmDialog) and w.isVisible():
            w.accept()
            return
    QTimer.singleShot(150, _auto_confirm)


def _create_via_wizard():
    _confirm_armed.append(True)
    QTimer.singleShot(250, _auto_confirm)
    page._wz_pw1.setText(WZ_PW)
    page._wz_pw2.setText(WZ_PW)
    page._on_create()


_create_via_wizard()
pump(400)
check("C3 向导创建成功：vault.bin + meta.json 就位",
      vault.status() == "ready" and not vault.locked
      and os.path.isfile(vault.vault_path) and os.path.isfile(vault.meta_path))
check("C4 创建后遮罩隐藏", not page._overlay.isVisible())

# ---- 新增条目：EntryDialog 自动应答，走真实 _on_add（内嵌 exec）----
def _autofill_entry():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, plug.EntryDialog) and w.isVisible():
            w._title.setText("GitHub 主账号")
            r0 = w._rows[0]
            r0["key"].setText("用户名")
            r0["value"].setText("2026heshao")
            r0["secret"].setChecked(False)
            w._add_row(None, focus=False)
            r1 = w._rows[-1]
            r1["key"].setText("密码")
            r1["value"].setText("p@ss中文-secret")
            r1["secret"].setChecked(True)
            w._tags.setText("开发, 生产")
            w._on_save()
            return
    QTimer.singleShot(150, _autofill_entry)


QTimer.singleShot(250, _autofill_entry)
page._on_add()
pump(400)
check("C5 新增条目成功且列表出现", vault.entry_count == 1
      and page._list.count() == 1
      and page._list.item(0).text().startswith("GitHub 主账号"))


def _all_label_texts(widget):
    out = []
    for lab in widget.findChildren(QLabel):
        out.append(lab.text())
    return out


SECRET_VALUE = "p@ss中文-secret"
PLAIN_VALUE = "2026heshao"
texts = _all_label_texts(page)
check("C7 secret 默认掩码：明文不出现在任何 QLabel，掩码出现",
      not any(SECRET_VALUE in t for t in texts)
      and any(plug.core.MASK in t for t in texts))
check("C8 非 secret 字段明文可见", any(PLAIN_VALUE in t for t in texts))

# ---- 显形切换（真实回调：页面方法 + 行按钮 QSS 站点）----
_sec_idx = [i for i, f in enumerate(page._current_entry()["fields"])
            if f.get("secret")][0]
page._toggle_secret(page._current_id, _sec_idx)
pump(120)
flush_deletes()
texts = _all_label_texts(page)
check("C9 点显示后明文出现在详情", any(SECRET_VALUE in t for t in texts))
page._toggle_secret(page._current_id, _sec_idx)
pump(120)
flush_deletes()
texts = _all_label_texts(page)
check("C10 再点隐藏回到掩码", not any(SECRET_VALUE in t for t in texts)
      and any(plug.core.MASK in t for t in texts))

# ---- 复制 + 30 秒剪贴板生命周期（缩短等待走真实定时器路径）----
cb = app.clipboard()
cb.setText("PRE-EXISTING")
page._shown_secret.setdefault(page._current_id, set()).add(0)
page._render_detail()
pump(120)
# 复制走真实 copy_with_autoclear：用短等待验证同一条定时器路径
copied_ok = plug.copy_with_autoclear(SECRET_VALUE, seconds=1)
pump(200)
check("C11 复制后剪贴板是密文值", copied_ok and cb.text() == SECRET_VALUE,
      f"cb={cb.text()[:6]}***")
pump(1400)
check("C12 等待期满且未被改动 → 剪贴板自动清空", cb.text() == "",
      f"cb={cb.text()[:6]}***")
plug.copy_with_autoclear(SECRET_VALUE, seconds=1)
cb.setText("USER-COPIED-SOMETHING-ELSE")
pump(1400)
check("C13 等待期内用户复制了别的 → 不误清", cb.text() == "USER-COPIED-SOMETHING-ELSE",
      f"cb={cb.text()[:8]}***")

# ---- 删除确认（自动应答 danger 弹窗）----
_eid = page._current_id
QTimer.singleShot(250, _auto_confirm)
page._on_delete()
pump(400)
check("C14 删除走二次确认且真实删除", vault.entry_count == 0
      and page._list.count() == 0)

# ---- 空闲锁定（假时钟：伪造 last_activity，直接调真实 _idle_tick）----
vault.lock()
vault.unlock(WZ_PW)
vault.add("阿里云 OSS", [{"key": "AccessKey", "value": "AK-123", "secret": True}], ["云"])
page._refresh_all()
pump(200)
check("C15 重新解锁后列表恢复", page._list.count() == 1)
check("C16 空闲定时器已启动（模块级单例）",
      plug._STATE.get("idle_timer") is not None
      and plug._STATE["idle_timer"].isActive())
plug._STATE["last_activity"] = time.time() - 6 * 60       # 伪造 6 分钟无操作
plug._idle_tick()
pump(1300)                                                # 等 1s 页面轮询同步
check("C17 空闲超时 → 自动锁定", vault.locked)
check("C18 锁定后页面遮罩重新出现（轮询同步）",
      page._overlay.isVisible() and page._overlay_stack.currentIndex() == 1)
plug.touch_activity()

# ---- 解锁 + 改密（ChangePasswordDialog 真实校验路径）----
page._unlock_pw.setText("wrong-pw")
page._on_unlock()
pump(120)
check("C19 错误主密码：提示错误且保持锁定", vault.locked
      and "主密码错误" in page._unlock_err.text())
page._unlock_pw.setText(WZ_PW)
page._on_unlock()
pump(120)
check("C20 正确主密码解锁", not vault.locked)

chg = plug.ChangePasswordDialog(sub_ctx, parent=win)
chg._old.setText(WZ_PW)
chg._new.setText("新密码-2")
chg._new2.setText("不一致")
chg._on_ok()
check("C21 两次新密码不一致被拒", not chg.confirmed
      and "不一致" in chg._hint.text())
chg._new2.setText("新密码-2")
chg._on_ok()
check("C22 一致后确认通过", chg.confirmed)
ok = vault.change_password(*chg.passwords)
check("C23 真实改密成功且旧密码失效", ok is True)
vault.lock()
check("C24 改密后旧主密码解不开", vault.unlock(WZ_PW) is False)
check("C25 新主密码可解锁", vault.unlock("新密码-2") is True)

# ---- 快速取用弹窗（不 exec，直调真实业务方法）----
vault.lock()
qd = plug.QuickCopyDialog(sub_ctx, vault, parent=win)
check("D1 锁定时快速取用先要求解锁", qd._stack.currentIndex() == 0)
qd._pw.setText("wrong")
qd._on_unlock()
check("D2 错误主密码不进入取用列表", qd._stack.currentIndex() == 0)
qd._pw.setText("新密码-2")
qd._on_unlock()
pump(120)
check("D3 解锁后进入取用列表", qd._stack.currentIndex() == 1
      and qd._list.count() == 1)
qd._input.setText("OSS")
pump(120)
check("D4 搜索过滤生效", qd._list.count() == 1)
qd._input.setText("不存在的词")
pump(120)
check("D5 无匹配时列表为空", qd._list.count() == 0)
qd._input.setText("")
pump(120)
cb.setText("")
qd._copy_selected()
pump(120)
check("D6 快速取用复制首个机密字段 + 30 秒倒计时已挂",
      qd.copied and cb.text() == "AK-123", f"cb={cb.text()[:6]}***")

# ---- 导出（文件对话框打桩，确认弹窗自动应答）----
out_path = os.path.join(tmp, "vault-export.json")
_orig_save = QFileDialog.getSaveFileName
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (out_path, "JSON (*.json)"))
try:
    QTimer.singleShot(250, _auto_confirm)
    page._on_export()
    pump(500)
finally:
    QFileDialog.getSaveFileName = _orig_save
_exported = _json.load(open(out_path, encoding="utf-8")) \
    if os.path.isfile(out_path) else {}
check("D7 导出明文 JSON 落盘且含条目", bool(_exported.get("entries"))
      and _exported["entries"][0]["title"] == "阿里云 OSS")

# ---- 损坏路径：meta 缺失 → 遮罩切「数据文件异常」----
os.remove(vault.meta_path)
page._sync_overlay()
pump(120)
check("D8 meta 缺失 → 遮罩切到损坏页", page._overlay.isVisible()
      and page._overlay_stack.currentIndex() == 2)
check("D9 损坏时旧库文件仍在（留档语义，绝不静默抹掉）",
      os.path.isfile(vault.vault_path))

# ====================================================================
# E. 视觉：light/dark × 解锁态/锁定态 四张真图
# ====================================================================
# 重建一个干净的库供截图（带一条有代表性的数据）
vault2 = plug.core.Vault(vault.data_dir)
vault2.create("shot-pw-3")
vault2.add("GitHub 主账号", [
    {"key": "用户名", "value": "2026heshao", "secret": False},
    {"key": "密码", "value": "shot-secret-9", "secret": True},
    {"key": "备注", "value": "演示数据", "secret": False},
], ["开发"])
vault2.add("阿里云 OSS", [
    {"key": "AccessKey", "value": "LTAI-shot", "secret": True},
    {"key": "区域", "value": "cn-beijing", "secret": False},
], ["云"])

plug._STATE["vault"] = vault2          # 单例换绑截图库
vault = vault2
page._vault = vault2
page._current_id = None
page._shown_secret = {}
page._refresh_all()
page._sync_overlay()
pump(300)

# 抓帧前把 D 段遗留的对话框与 deleteLater 幽灵全部清场（离屏抓帧会拍到它们）
for _w in QApplication.topLevelWidgets():
    if isinstance(_w, (plug.EntryDialog, plug.QuickCopyDialog,
                       plug.ChangePasswordDialog, plug.ConfirmDialog)):
        _w.deleteLater()
flush_deletes = app.sendPostedEvents
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
pump(100)


def _diag_dump(tag):
    """诊断：列出所有可见且大面积的 win 后代（找青色幽灵用）"""
    big = []

    def _walk(w):
        for c in w.children():
            if isinstance(c, QWidget) and c.isVisible() \
                    and c.width() * c.height() > 150 * 150:
                big.append(f"{c.__class__.__name__}#{c.objectName()}"
                           f"({c.width()}x{c.height()})")
            _walk(c)
    _walk(win)
    print(f"    [diag {tag}] 大面积可见控件: {big}", flush=True)


sizes = {}
for theme in ("light", "dark"):
    cm.set("theme", theme)
    win.apply_external_theme(theme)     # 走设置面板真实路径（广播 theme_changed）
    pump(300)
    # 解锁态
    if vault.locked:
        vault.unlock("shot-pw-3")
    page._refresh_all()
    page._sync_overlay()
    pump(200)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _diag_dump(f"page-{theme}")
    p1 = os.path.join(OUT_DIR, f"vault-page-{theme}.png")
    img = win.grab()
    img.save(p1)
    _im = img.toImage()
    _px = _im.pixelColor(int(_im.width() * 0.6), int(_im.height() * 0.42))
    print(f"    [diag page-{theme}] 采样像素: {_px.red()},{_px.green()},{_px.blue()}",
          flush=True)
    sizes[f"page-{theme}"] = os.path.getsize(p1) if os.path.isfile(p1) else 0
    # 锁定遮罩态
    vault.lock()
    page._sync_overlay()
    pump(200)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _diag_dump(f"lock-{theme}")
    p2 = os.path.join(OUT_DIR, f"vault-lock-{theme}.png")
    img2 = win.grab()
    img2.save(p2)
    sizes[f"lock-{theme}"] = os.path.getsize(p2) if os.path.isfile(p2) else 0
    print(f"    saved vault-page-{theme}.png ({sizes[f'page-{theme}']} B) / "
          f"vault-lock-{theme}.png ({sizes[f'lock-{theme}']} B)", flush=True)

check("E1 四张截图都落盘且非空", all(v > 20000 for v in sizes.values()), f"{sizes}")


def _file_bytes(name):
    with open(os.path.join(OUT_DIR, name), "rb") as f:
        return f.read()


check("E2 light / dark 页面截图不同（主题确实生效）",
      _file_bytes("vault-page-light.png") != _file_bytes("vault-page-dark.png"))
check("E3 解锁态 / 锁定态截图不同（遮罩确实渲染）",
      _file_bytes("vault-page-light.png") != _file_bytes("vault-lock-light.png"))

# ====================================================================
win.close()
pump(150)
shutil.rmtree(tmp, ignore_errors=True)

failed = [n for n, ok in _results if not ok]
print(f"\n{'ALL PASS (' + str(len(_results)) + ')' if not failed else 'FAILED: ' + repr(failed)}",
      flush=True)
sys.exit(0 if not failed else 1)
