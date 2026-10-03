# -*- coding: utf-8 -*-
"""临时素材异步缩略图管线（丝滑化）回归钉子。

核心契约：paint() 路径**永不解码**（未命中 → 占位 + 后台派发）；
到货入缓存 + 淡入 + 单格局部重绘；尺寸换代作废在途结果；
reduce_motion 下瞬显；loader=None 保持旧同步路径（独立使用场景兼容）。
"""

import time

import pytest

from src import motion

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_image(path, w=900, h=600, color_hue=120):
    from PyQt6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color_hue % 256, 200, 90))
    assert img.save(path)
    return path


class _Host:
    """最小宿主替身（AssetsPanel 构造所需全部）"""

    def __init__(self, mgr):
        self._config = {}
        self.current_theme = "light"
        self._temp_asset_manager = mgr
        from types import SimpleNamespace
        self.data_changed = SimpleNamespace(emit=lambda *a: None)


class _GatePool:
    """手动闸门线程池替身：start 只入队，由测试显式放行（测过期语义）"""

    def __init__(self):
        self.jobs = []

    def start(self, runnable, priority=0):
        self.jobs.append(runnable)

    def flush_one(self):
        self.jobs.pop(0).run()


@pytest.fixture()
def env(tmp_path):
    _app()                      # ★ 夹具先建 QApplication：任何测试都不许在
                                #   无应用状态下碰 Qt（qFatal 静默崩，rc=127）
    from src.temp_asset_manager import TempAssetManager
    mgr = TempAssetManager(str(tmp_path))
    ids = []
    for i in range(3):
        p = _make_image(str(tmp_path / f"s{i}.png"), color_hue=60 * i)
        assert mgr.add_asset(p, f"图{i}.png")
        ids.append(mgr.get_all_assets()[-1].asset_id)
    return mgr, ids


def _make_panel(mgr):
    from src.assets_panel import AssetsPanel
    panel = AssetsPanel(_Host(mgr))
    panel._valid_ids = {a.asset_id for a in mgr.get_all_assets()}
    panel.refresh()
    return panel


def _drain(app, panel, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
        if not panel._pending:
            return
    pytest.fail(f"后台管线超时，仍有在途: {panel._pending}")


# ====================================================================
# 1. 纯解码函数
# ====================================================================
def test_decode_image_thumb_exact_size_and_corrupt(tmp_path):
    from src.assets_panel import _decode_image_thumb
    p = _make_image(str(tmp_path / "a.png"), 1200, 800)
    img = _decode_image_thumb(p, 128, 84)
    assert img is not None and img.width() == 128 and img.height() == 84
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"this is not an image")
    assert _decode_image_thumb(str(bad), 128, 84) is None
    assert _decode_image_thumb(str(tmp_path / "nope.png"), 128, 84) is None


# ====================================================================
# 2. delegate：占位 / 哨兵 / 同步回退
# ====================================================================
def test_delegate_paint_path_never_blocks_marks_pending_and_dispatches(env):
    from src.assets_panel import _PENDING
    mgr, ids = env
    panel = _make_panel(mgr)
    calls = []
    panel._thumb_delegate._loader = lambda asset: calls.append(asset.asset_id)
    asset = mgr.get_all_assets()[0]
    assert panel._thumb_delegate._thumb(asset) is None          # 占位
    assert panel._thumb_cache[asset.asset_id] is _PENDING
    assert calls == [asset.asset_id]                            # 派发恰一次
    assert panel._thumb_delegate._thumb(asset) is None          # 再画不重派发
    assert calls == [asset.asset_id]


def test_delegate_non_image_gets_false_sentinel(tmp_path):
    from src.temp_asset_manager import TempAssetManager
    mgr = TempAssetManager(str(tmp_path))
    txt = tmp_path / "t.txt"
    txt.write_text("x", encoding="utf-8")
    mgr.add_asset(str(txt), "t.txt")
    panel = _make_panel(mgr)
    asset = mgr.get_all_assets()[0]
    assert panel._thumb_delegate._thumb(asset) is None
    assert panel._thumb_cache[asset.asset_id] is False          # 非 _PENDING


def test_delegate_sync_fallback_when_loader_is_none(env):
    from PyQt6.QtGui import QPixmap

    from src.assets_panel import _AssetThumbDelegate
    mgr, _ = env
    asset = mgr.get_all_assets()[0]
    dlg = _AssetThumbDelegate(_Host(mgr), {}, loader=None)
    pix = dlg._thumb(asset)
    assert isinstance(pix, QPixmap) and pix.width() == dlg.THUMB_W


# ====================================================================
# 3. 面板异步管线
# ====================================================================
def test_async_pipeline_fills_cache_and_clears_pending(env):
    from PyQt6.QtGui import QPixmap
    mgr, _ = env
    panel = _make_panel(mgr)
    app = _app()
    asset = mgr.get_all_assets()[0]
    panel._thumb_delegate._thumb(asset)          # 触发占位+派发
    _drain(app, panel)
    v = panel._thumb_cache[asset.asset_id]
    assert isinstance(v, QPixmap) and v.width() == panel._thumb_delegate.THUMB_W


def test_epoch_drops_stale_result_after_size_change(env):
    """变尺寸换代后，旧尺寸的在途结果必须被丢弃（不许旧图回填）。"""
    mgr, _ = env
    panel = _make_panel(mgr)
    gate = _GatePool()
    panel._pool = gate
    asset = mgr.get_all_assets()[0]
    panel._thumb_delegate._thumb(asset)          # 入闸门（未执行）
    assert len(gate.jobs) == 1
    old_w = panel._thumb_delegate.THUMB_W
    panel.apply_thumb_size(old_w + 32)           # 换代 + 清缓存
    gate.flush_one()                             # 放行旧代际结果
    assert isinstance(panel._thumb_cache.get(asset.asset_id, None), type(None)) or \
        panel._thumb_cache.get(asset.asset_id) is None
    # 新尺寸下重新请求 → 结果正常入缓存
    panel._thumb_delegate._thumb(asset)
    gate.flush_one()
    v = panel._thumb_cache[asset.asset_id]
    assert v is False or hasattr(v, "width")


def test_result_for_deleted_asset_is_discarded(env):
    mgr, ids = env
    panel = _make_panel(mgr)
    gate = _GatePool()
    panel._pool = gate
    asset = mgr.get_all_assets()[0]
    panel._thumb_delegate._thumb(asset)
    mgr.delete_asset(asset.asset_id)
    panel.refresh()
    gate.flush_one()
    assert asset.asset_id not in panel._thumb_cache


def test_refresh_prunes_cache_and_fade_of_removed(env):
    mgr, ids = env
    panel = _make_panel(mgr)
    aid = ids[0]
    panel._thumb_cache[aid] = object()            # 随便占位
    panel._thumb_delegate._fade_values[aid] = 0.5
    mgr.delete_asset(aid)
    panel.refresh()
    assert aid not in panel._thumb_cache
    assert aid not in panel._thumb_delegate._fade_values


def test_apply_thumb_size_short_circuits_same_value(env):
    mgr, _ = env
    panel = _make_panel(mgr)
    epoch0 = panel._epoch
    panel.apply_thumb_size(panel._thumb_delegate.THUMB_W)   # 同值 → 短路
    assert panel._epoch == epoch0


# ====================================================================
# 4. 淡入与 reduce_motion
# ====================================================================
def test_fade_completes_to_full_and_detaches(env):
    mgr, _ = env
    panel = _make_panel(mgr)
    app = _app()
    asset = mgr.get_all_assets()[0]
    panel._thumb_delegate._thumb(asset)
    _drain(app, panel)
    end = time.time() + 3.0
    while time.time() < end and asset.asset_id in panel._fade_anims:
        app.processEvents()
        time.sleep(0.02)
    assert asset.asset_id not in panel._fade_anims
    assert asset.asset_id not in panel._thumb_delegate._fade_values  # 1.0 后摘除


def test_reduce_motion_skips_fade(env):
    mgr, _ = env
    panel = _make_panel(mgr)
    app = _app()
    motion.set_reduce_motion(True)
    try:
        asset = mgr.get_all_assets()[0]
        panel._thumb_delegate._thumb(asset)
        _drain(app, panel)
        from PyQt6.QtGui import QPixmap
        v = panel._thumb_cache[asset.asset_id]
        assert isinstance(v, QPixmap)            # 到货入缓存
        assert asset.asset_id not in panel._thumb_delegate._fade_values
        assert asset.asset_id not in panel._fade_anims
    finally:
        motion.set_reduce_motion(False)


def test_motion_gate_still_governs_fade_duration():
    """淡入时长走 motion 总闸（reduce_motion 单点生效的又一路径）。"""
    motion.set_reduce_motion(True)
    try:
        assert motion.duration(140, 1.0) == 0
    finally:
        motion.set_reduce_motion(False)
    assert motion.duration(140, 1.0) == 140


# ====================================================================
# 5. 素材卡配色与右键菜单（2026-10-02 修复回归）
# ====================================================================
# 背景：UI 重构 05 给 delegate 换自绘配色时，把 QSS 的 rgba() 令牌直接喂
# QColor —— QColor 不认函数式写法，得到**无效色**，画出来是纯黑：
#   · hover 卡变成"黑块卡"（用户报障的第二个问题）
#   · 缩略图描边 / 失效占位底同样发黑
# 同一时段，右键菜单给删除项上 danger 色图标时调了 delegate 才有的
# `_colors`，AssetsPanel 没有这个属性 → 一右键就 AttributeError。
# --------------------------------------------------------------------

_PAINT_TOKENS = (
    "primary", "primary_a12", "primary_a08", "primary_a30",
    "hair", "panel_fill", "text", "text_secondary", "danger",
)


class TestDelegateColors:
    def test_every_paint_token_parses_to_a_valid_color(self):
        """paint() 用到的全部令牌，两个主题下都必须解析成**有效** QColor。

        钉的是「无效色 = 画出来纯黑」这条失败模式：只要有一个令牌解析
        失败，悬停卡就会回到黑块。
        """
        from src.glass import _to_color
        from src.theme import THEMES
        for theme in ("light", "dark"):
            for key in _PAINT_TOKENS:
                assert key in THEMES[theme], (theme, key)
                c = _to_color(THEMES[theme][key])
                assert c.isValid(), "%s 主题 %s(%r) 解析成无效色" % (
                    theme, key, THEMES[theme][key])

    def test_translucent_tokens_keep_their_alpha(self):
        """淡主色底/细环的价值就在那点透明度 —— 解析时不能被弄丢。"""
        from src.glass import _to_color
        from src.theme import THEMES
        for theme in ("light", "dark"):
            t = THEMES[theme]
            assert _to_color(t["primary_a08"]).alpha() < 60
            assert _to_color(t["primary_a30"]).alpha() < 120
            assert _to_color(t["hair"]).alpha() < 60


def _qcolor_subscript_lines(source: str):
    """AST 扫描：找出所有 ``QColor(<colors/c 字典下标>)`` 形式的调用行号。"""
    import ast
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "QColor"
                and node.args):
            continue
        arg = node.args[0]
        if (isinstance(arg, ast.Subscript)
                and isinstance(arg.value, ast.Name)
                and arg.value.id in ("colors", "c")):
            hits.append(node.lineno)
    return hits


def test_delegate_never_feeds_theme_tokens_into_qcolor_directly():
    """★ 静态护栏：本文件禁止再出现 ``QColor(colors[...])``。

    这类写法在 hex 令牌上是碰巧能跑的（QColor 认 #RRGGBB），等哪天有人把
    某个令牌换成 rgba() 写法，界面就悄悄变黑块 —— 所以按 kb-search 那次
    假护栏的教训，直接用 AST 钉死调用形态，而不是比较某个具体取值。
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(os.path.dirname(here), "src", "assets_panel.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    assert _qcolor_subscript_lines(source) == [], (
        "assets_panel.py 存在 QColor(主题令牌下标) 的直接调用（行 %s），"
        "rgba() 写法会解析成无效色并画成黑块；请改走 glass._to_color"
        % _qcolor_subscript_lines(source))

    # 反向验证：扫描器真的认得这种形态（否则上面那条是恒真假护栏）
    assert len(_qcolor_subscript_lines("def f(colors):\n"
                                       "    return QColor(colors['hair'])\n")) == 1


class _StubAction:
    def __init__(self, text):
        self.text = text

    def setIcon(self, icon):
        self.icon = icon


class _StubMenu:
    """QMenu 替身：只验证「菜单能构造出来」，不真弹（exec 不进事件循环）。"""

    last_qss = None

    def __init__(self, parent=None):
        self.actions = []

    def setStyleSheet(self, qss):
        _StubMenu.last_qss = qss

    def addAction(self, text):
        self.actions.append(_StubAction(text))
        return self.actions[-1]

    def addSeparator(self):
        return None

    def exec(self, pos):
        return None


class _HostWithShell(_Host):
    """_Host + 右键菜单要用的 _container.styleSheet()。"""

    def __init__(self, mgr):
        super().__init__(mgr)
        from PyQt6.QtWidgets import QWidget
        self._container = QWidget()
        self._container.setStyleSheet("QWidget { color: #000000; }")


def test_context_menu_builds_without_attribute_error(env, monkeypatch):
    """★ 右键素材必须能弹出菜单（曾因误用 delegate 的 _colors 直接炸）。"""
    import src.assets_panel as ap
    from PyQt6.QtCore import QPoint
    mgr, ids = env
    panel = ap.AssetsPanel(_HostWithShell(mgr))
    panel._valid_ids = set(ids)
    panel.refresh()
    monkeypatch.setattr(ap, "QMenu", _StubMenu)

    item = panel._asset_list.item(0)
    assert item is not None
    r = panel._asset_list.visualItemRect(item)
    # 不抛异常即通过；菜单项齐备 = 打开/另存为/删除
    panel._on_context_menu(QPoint(r.width() // 2, r.height() // 2))


def test_panel_has_its_own_colors_entry(env):
    """面板层必须有 `_colors`（右键菜单取 danger 色的入口）。"""
    import src.assets_panel as ap
    mgr, _ = env
    panel = ap.AssetsPanel(_Host(mgr))
    colors = panel._colors()
    assert isinstance(colors, dict) and colors.get("danger")
    # 主题跟随宿主
    panel._host.current_theme = "dark"
    assert panel._colors() is not colors          # 换主题后是重新取的色板


def test_hover_paint_is_visible_against_normal(env):
    """悬停必须有可感知的视觉反馈（曾因黑块被用户点名，修完不能没有反馈）。"""
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem
    import src.assets_panel as ap
    mgr, _ = env
    panel = ap.AssetsPanel(_HostWithShell(mgr))
    panel._valid_ids = {a.asset_id for a in mgr.get_all_assets()}
    panel.refresh()
    _drain(_app(), panel)

    d = panel._thumb_delegate
    index = panel._asset_list.model().index(0, 0)
    assert index.data(Qt.ItemDataRole.UserRole) is not None

    def render(state):
        img = QImage(d.CELL_W, d.CELL_H, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        p = QPainter(img)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, d.CELL_W, d.CELL_H)
        opt.state = state
        d.paint(p, opt, index)
        p.end()
        return img

    base = QStyle.StateFlag.State_Enabled
    normal = render(base)
    hover = render(base | QStyle.StateFlag.State_MouseOver)
    assert normal != hover, "hover 与常态渲染完全一致 = 悬停没有反馈"
