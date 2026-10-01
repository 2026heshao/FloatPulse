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
