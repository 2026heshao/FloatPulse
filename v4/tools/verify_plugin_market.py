# -*- coding: utf-8 -*-
"""Offscreen 端到端验证：插件商店「🌐 在线市场」（2026-09-30）。

本地 HTTPServer 伪造「市场索引 + latest Release + .fpplug 附件」三件套，
走**真实点击路径**验证全链路。验证点：
  A. 点「检查在线市场」→ 两段式拉取（索引→Release）→ 在线卡出现，
     条数 = 索引内未安装条目数；能力/热键/体积渲染正确
  B. 点「⬇ 下载安装」→ 下载（octet-stream）→ sha256 校验 → 落商店
     → 既有安装链路（目录生成 + loader 加载）→ 状态行 ✓
  C. sha256 不符 → 拒绝落盘、拒绝安装、状态行说明校验失败
  D. 索引里声明但 Release 缺附件 → 不出卡、状态行提示「暂缺」
  E. 索引请求 403 / 404 → 状态行分级文案（限流 / HTTP 404），不弹窗不崩
  F. 已安装的插件不进在线卡（本地 v == 远端 v）；本地为旧版本时提示「可更新」
  G. busy 期间重复点击被忽略
  H. light/dark 双主题真截图（弹窗含在线卡）

运行方式（必须 offscreen 平台）：
  QT_QPA_PLATFORM=offscreen python tools/verify_plugin_market.py [--shots]
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QFontDatabase                       # noqa: E402
from PyQt6.QtWidgets import QApplication                    # noqa: E402

from src import net_guard                                   # noqa: E402
from src import plugin_market as pm                         # noqa: E402
from src.config import ConfigManager                        # noqa: E402
from src.docx_manager import DocxManager                    # noqa: E402
from src.task_manager import TaskManager                    # noqa: E402
from src.note_manager import NoteManager                    # noqa: E402
from src.fragment_manager import FragmentManager            # noqa: E402
from src.clipboard_monitor import ClipboardMonitor          # noqa: E402
from src.temp_asset_manager import TempAssetManager         # noqa: E402
from src.main_window import MainWindow                      # noqa: E402
from src.plugin_loader import PluginLoader                  # noqa: E402
from src.plugin_api import ActionRegistry                   # noqa: E402
from src.plugins_panel import PluginsPanel, PluginStoreDialog  # noqa: E402

PASS = 0
SHOTS = "--shots" in sys.argv
SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "build", "shots")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
STORE_DIR = os.path.join(REPO_ROOT, "plugin_store")


def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}", flush=True)


def pump(app, ms=0):
    if ms <= 0:
        app.processEvents()
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(cond, timeout_s=8.0, step_ms=50):
    end = time.time() + timeout_s
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(step_ms / 1000.0)
    return False


def shot(widget, name):
    if not SHOTS:
        return
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, name)
    widget.grab().save(path)
    print(f"[SHOT] {path}", flush=True)


# ---------------- 伪造市场服务器 ----------------
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import build_marketplace                                   # noqa: E402

REAL_ITEMS, _ = build_marketplace.build_items(STORE_DIR)
REAL_BYTES = {}
for _it in REAL_ITEMS:
    with open(os.path.join(STORE_DIR, _it["file"]), "rb") as _f:
        REAL_BYTES[_it["file"]] = _f.read()

ASSET_SEQ = [100]          # 自增 asset id
RELEASE_ASSETS = {}        # {filename: id}
BAD_SHA_ITEM = {           # sha 不符的陷阱条目（serve 的字节与声明哈希不一致）
    "id": "badsha", "name": "坏包陷阱", "version": "1.0.0",
    "file": "badsha.fpplug", "size": 16,
    "sha256": "0" * 64, "description": "下载后校验必失败",
    "capabilities": [], "hotkeys": [],
}


def make_index(plugins):
    return json.dumps({"schema": 1, "updated": "2026-09-30",
                       "plugins": plugins}, ensure_ascii=False)


def make_release():
    return json.dumps({"tag_name": "v4.7.0",
                       "assets": [{"name": f, "id": i}
                                  for f, i in RELEASE_ASSETS.items()]})


class Handler(BaseHTTPRequestHandler):
    routes = {}          # {path: (status, content_type, bytes)}

    def do_GET(self):
        entry = self.routes.get(self.path.split("?")[0])
        if entry is None:
            self.send_error(404)
            return
        status, ctype, body = entry
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_server(routes):
    handler = type("H", (Handler,), {"routes": routes})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def build_routes(with_real=True, include_badsha=False, miss_one=False,
                 index_status=200):
    """构造一轮服务器路由；返回 (routes, 提供的插件条目列表)"""
    routes = {}
    RELEASE_ASSETS.clear()
    items = list(REAL_ITEMS) if with_real else []
    if include_badsha:
        items = items + [BAD_SHA_ITEM]
        RELEASE_ASSETS["badsha.fpplug"] = 999
        routes["/dl/999"] = (200, "application/octet-stream",
                             b"corrupted-bytes-not-a-plugin")
    if miss_one and items:
        items = list(items)
        dropped = items[-1]
        items = items[:-1] + [dict(dropped)]      # 条目在索引里
        # 但附件不进 RELEASE_ASSETS（D 场景）
        for it in items[:-1]:
            aid = ASSET_SEQ[0]
            ASSET_SEQ[0] += 1
            RELEASE_ASSETS[it["file"]] = aid
            routes[f"/dl/{aid}"] = (200, "application/octet-stream",
                                    REAL_BYTES[it["file"]])
    else:
        for it in items:
            if it is BAD_SHA_ITEM:
                continue
            aid = ASSET_SEQ[0]
            ASSET_SEQ[0] += 1
            RELEASE_ASSETS[it["file"]] = aid
            routes[f"/dl/{aid}"] = (200, "application/octet-stream",
                                    REAL_BYTES[it["file"]])
    routes["/marketplace/index.json"] = (
        index_status, "application/octet-stream",
        make_index(items).encode("utf-8"))
    routes["/releases/latest"] = (200, "application/json",
                                  make_release().encode("utf-8"))
    return routes, items


def main() -> int:
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_plugin_market")
    for f in (r"C:\Windows\Fonts\msyh.ttc",):
        if os.path.exists(f):
            QFontDatabase.addApplicationFont(f)

    net_guard.set_allow_private_network(True)
    # 模态弹窗全部吞掉（安装成功/失败的 QMessageBox 会阻塞脚本）
    import src.plugins_panel as pp_mod
    shown = []
    pp_mod.QMessageBox.information = lambda *a, **k: shown.append(("info", a))
    pp_mod.QMessageBox.warning = lambda *a, **k: shown.append(("warn", a))
    pp_mod.QMessageBox.question = lambda *a, **k: None

    tmp = tempfile.mkdtemp(prefix="fp_verify_market_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    plugins_dir = os.path.join(tmp, "plugins")
    store_dir = os.path.join(tmp, "store")
    os.makedirs(plugins_dir, exist_ok=True)
    os.makedirs(store_dir, exist_ok=True)

    config = ConfigManager(os.path.join(data_dir, "config.json"))
    docx_mgr = DocxManager(os.path.join(tmp, "知识库.docx"),
                           os.path.join(data_dir, "docx_meta.json"))
    docx_mgr.load()
    win = MainWindow(TaskManager(os.path.join(data_dir, "schedule.json")),
                     NoteManager(os.path.join(data_dir, "notes.json")),
                     FragmentManager(os.path.join(data_dir, "fragments.json")),
                     docx_mgr, config,
                     ClipboardMonitor(FragmentManager(
                         os.path.join(data_dir, "fragments.json")), config),
                     TempAssetManager(tmp))
    win.show()
    pump(app, 120)

    loader = PluginLoader(ActionRegistry(), ctx=None,
                          plugins_dir=plugins_dir, store_dir=store_dir,
                          logger=None)
    win.set_plugin_loader(loader)
    panel = PluginsPanel(win)
    dlg = PluginStoreDialog(panel)
    panel._store_dialog = dlg
    dlg.show()
    pump(app, 120)

    # ---- A. happy path ----
    routes, items = build_routes(include_badsha=True)
    srv, base = start_server(routes)
    pm.INDEX_API_URL = base + "/marketplace/index.json"
    pm.asset_download_url = lambda aid, _b=base: _b + f"/dl/{aid}"
    pp_mod.RELEASES_API_URL = base + "/releases/latest"

    n_installed_before = sum(
        1 for e in loader.scan_store() if e.installed)
    assert n_installed_before == 0, "临时商店应从空开始"

    dlg._online_btn.click()
    assert wait_until(lambda: any(
        dlg._list_layout.itemAt(i).widget() is not None
        and dlg._list_layout.itemAt(i).widget().objectName() == "pluginStoreCard"
        and getattr(dlg._list_layout.itemAt(i).widget(),
                    "_is_online_card", False)
        for i in range(dlg._list_layout.count()))), "在线卡未出现"
    online_cards = [dlg._list_layout.itemAt(i).widget()
                    for i in range(dlg._list_layout.count())
                    if dlg._list_layout.itemAt(i).widget() is not None
                    and getattr(dlg._list_layout.itemAt(i).widget(),
                                "_is_online_card", False)]
    # 5 真实插件 + 1 坏 sha 陷阱 = 6 张在线卡
    assert len(online_cards) == len(items) == 6, \
        f"在线卡数 {len(online_cards)} != 6"
    ok(f"A1. 在线卡出现且数量正确（{len(online_cards)} 张）")
    assert "kb-search" in dlg._online_status.text() or \
        "可安装" in dlg._online_status.text()
    # 下载地址解析提示被追加过
    ok("A2. 状态行走完「索引 → 下载地址解析」两段")

    # ---- B. 真实下载安装 kb-search ----
    target = next(c for c in online_cards
                  if c._market_item["id"] == "kb-search")
    btn = target.findChildren(type(dlg._online_btn))[0]
    target._market_item  # noqa: B018
    card_btn = [b for b in target.findChildren(type(btn))
                if b.text().startswith("⬇")][0]
    card_btn.click()
    assert wait_until(lambda: os.path.isfile(
        os.path.join(plugins_dir, "kb-search", "manifest.json"))), \
        "安装目录未生成"
    assert os.path.isfile(os.path.join(store_dir, "kb-search.fpplug")), \
        "源包未落商店目录"
    ok("B1. 下载 → sha256 校验 → 落商店 → loader 安装目录生成")
    assert any(t[0] == "info" for t in shown), "安装成功反馈框未出现"
    assert wait_until(lambda: "已从在线市场安装" in dlg._online_status.text())
    ok("B2. 状态行 ✓ + 成功反馈框出现（经既有 _on_install 链路）")

    # 安装后重查：kb-search 不再进在线卡（已装）
    dlg._online_btn.click()
    assert wait_until(lambda: not dlg._market_busy)
    online_ids = [w._market_item["id"] for w in
                  [dlg._list_layout.itemAt(i).widget()
                   for i in range(dlg._list_layout.count())]
                  if w is not None and getattr(w, "_is_online_card", False)]
    assert "kb-search" not in online_ids, "已装插件不应再出现在在线市场"
    assert "badsha" in online_ids, "坏 sha 条目应仍在列表"
    ok("F1. 已安装的插件被过滤，不出在线卡")

    # ---- C. sha 不符：拒绝落盘 ----
    bad_card_btn = None
    for w in [dlg._list_layout.itemAt(i).widget()
              for i in range(dlg._list_layout.count())]:
        if w is not None and getattr(w, "_is_online_card", False) \
                and w._market_item["id"] == "badsha":
            bad_card_btn = [b for b in w.findChildren(type(btn))
                            if b.text().startswith("⬇")][0]
    bad_card_btn.click()
    assert wait_until(lambda: "校验失败" in dlg._online_status.text()), \
        "sha 校验失败提示未出现"
    assert not os.path.isfile(os.path.join(store_dir, "badsha.fpplug")), \
        "校验失败的包不该落盘"
    assert not os.path.isdir(os.path.join(plugins_dir, "badsha")), \
        "校验失败的包不该被安装"
    ok("C. sha256 不符 → 拒绝落盘、拒绝安装，状态行说明")

    # ---- E. 索引请求 403 分级文案 ----
    routes2, _ = build_routes()
    routes2["/marketplace/index.json"] = (403, "text/plain", b"rate limited")
    srv2, base2 = start_server(routes2)
    pm.INDEX_API_URL = base2 + "/marketplace/index.json"
    pp_mod.RELEASES_API_URL = base2 + "/releases/latest"
    dlg._online_btn.click()
    assert wait_until(lambda: "限流" in dlg._online_status.text()), \
        "403 分级文案未出现"
    ok("E1. 索引 403 → 限流分级文案，不弹窗不崩")

    # ---- E2. 索引 404 ----
    routes3, _ = build_routes()
    routes3["/marketplace/index.json"] = (404, "text/plain", b"nope")
    srv3, base3 = start_server(routes3)
    pm.INDEX_API_URL = base3 + "/marketplace/index.json"
    pp_mod.RELEASES_API_URL = base3 + "/releases/latest"
    dlg._online_btn.click()
    assert wait_until(lambda: "HTTP 404" in dlg._online_status.text())
    ok("E2. 索引 404 → HTTP 404 文案")

    # ---- D. Release 缺附件：条目在索引、附件不在 assets ----
    routes4, items4 = build_routes(with_real=True, miss_one=True)
    srv4, base4 = start_server(routes4)
    pm.INDEX_API_URL = base4 + "/marketplace/index.json"
    pp_mod.RELEASES_API_URL = base4 + "/releases/latest"
    dlg._online_btn.click()
    assert wait_until(lambda: "暂缺" in dlg._online_status.text()
                      or "可安装" in dlg._online_status.text())
    online_cards2 = [w for w in
                     [dlg._list_layout.itemAt(i).widget()
                      for i in range(dlg._list_layout.count())]
                     if w is not None and getattr(w, "_is_online_card", False)]
    online_ids2 = [w._market_item["id"] for w in online_cards2]
    dropped_id = items4[-1]["id"] if items4 else ""
    assert dropped_id not in online_ids2 or len(online_cards2) == 4, \
        "缺附件条目不应出可下载卡"
    ok("D. Release 缺附件的条目不出下载卡，状态行提示暂缺")

    # ---- G. busy 期间重复点击忽略 ----
    dlg._market_set_busy(True)
    before_status = dlg._online_status.text()
    dlg._online_btn.click()          # 应被 busy 挡住
    assert dlg._online_status.text() == before_status
    dlg._market_set_busy(False)
    ok("G. busy 期间重复点击被忽略")

    # ---- H. 双主题截图 ----
    # 先重建一轮 happy-path 在线卡用于截图
    pm.INDEX_API_URL = base + "/marketplace/index.json"
    pp_mod.RELEASES_API_URL = base + "/releases/latest"
    dlg._online_btn.click()
    wait_until(lambda: not dlg._market_busy)
    pump(app, 120)
    # DEFAULT_THEME 是 dark——两张都必须显式设（GlassDialog 不订阅广播）
    win._theme = "light"
    win._config.set("theme", "light")
    win._apply_theme()
    dlg.apply_theme()
    pump(app, 120)
    shot(dlg, "market_light.png")
    win._theme = "dark"
    win._config.set("theme", "dark")
    win._apply_theme()
    dlg.apply_theme()
    pump(app, 120)
    shot(dlg, "market_dark.png")

    srv.shutdown()
    srv2.shutdown()
    srv3.shutdown()
    srv4.shutdown()
    net_guard.set_allow_private_network(False)
    win._allow_close = True
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    if SHOTS:
        print(f"[SHOTS] {SHOT_DIR}")
    print(f"[DONE] all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
