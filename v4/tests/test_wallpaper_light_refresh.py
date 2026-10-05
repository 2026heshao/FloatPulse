# -*- coding: utf-8 -*-
"""壁纸参数轻量刷新护栏（2026-10-05 用户报告「主题色遮罩 / 图片模糊 /
图片不透明度三个 ± 步进器连点卡顿」）

根因（两层叠加）：
  1. SurfaceBackground.configure 每次调用都 QPixmap(path) 从盘上重解码
     整张壁纸 —— 参数微调（遮罩/模糊/不透明度）根本没换图也照付；
  2. 设置面板壁纸改动走 refresh_appearance → 全量 _apply_theme（整窗
     QSS repolish + 全部 IconButton 重取色 + 菜单/便签换肤），而这些
     对壁纸参数来说是纯浪费（壁纸不改任何取色与 QSS）。

修复：
  · configure 只在路径变化时读盘（解码失败 _path 收敛 ""，仍可重试）；
  · MainWindow.refresh_wallpaper 只重挂壁纸图层 + 广播 wallpaper_changed
    （小卡片经 knowledge_ball 接线跟进），设置面板壁纸项全部改走此路。

钉法：
  1. 同图 configure 不再读盘（QPixmap 构造计数），参数变化仍返回 True、
     换图才第二次解码、完全同参返回 False —— SurfaceBackground 契约
  2. 壁纸步进器变更 → 只调 host.refresh_wallpaper，绝不碰
     refresh_appearance；无实际变化不写盘不刷新
  3. 变更即落盘（此前壁纸参数从不 save，只有别的设置顺手救场）
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import wallpaper                                    # noqa: E402


# ====================================================================
# 1. SurfaceBackground.configure：同图不重解码
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_image(tmp_path, name):
    """落一张真实小图（QPixmap 需要 qapp），返回绝对路径"""
    from PyQt6.QtGui import QColor, QPixmap
    path = str(tmp_path / name)
    pm = QPixmap(8, 8)
    pm.fill(QColor("#336699"))
    assert pm.save(path)
    return path


def test_configure_skips_decode_when_path_unchanged(qapp, tmp_path,
                                                    monkeypatch):
    import src.glass as glass_mod
    from src.glass import SurfaceBackground

    img_a = _make_image(tmp_path, "a.png")
    img_b = _make_image(tmp_path, "b.png")

    real_qpixmap = glass_mod.QPixmap
    loads = []

    def counting_qpixmap(*args):
        if args and args[0]:        # 只记「按路径解码」（空构造是初始化常态）
            loads.append(args[0])
        return real_qpixmap(*args)

    monkeypatch.setattr(glass_mod, "QPixmap", counting_qpixmap)

    bg = SurfaceBackground()
    # 首次挂图：解码一次，返回 True
    assert bg.configure(img_a, "cover", 100, 0, 40, "#FAFAF8") is True
    assert loads == [img_a]
    # 同图只改遮罩：不读盘，但参数变了仍返回 True（宿主要 update()）
    assert bg.configure(img_a, "cover", 100, 0, 60, "#FAFAF8") is True
    assert loads == [img_a]
    # 完全相同参数：False，零开销
    assert bg.configure(img_a, "cover", 100, 0, 60, "#FAFAF8") is False
    assert loads == [img_a]
    # 换图：才第二次解码
    assert bg.configure(img_b, "cover", 100, 0, 60, "#FAFAF8") is True
    assert loads == [img_a, img_b]


def test_configure_failed_load_still_retries(qapp, tmp_path, monkeypatch):
    """解码失败语义不变：路径收敛 ""（关闭壁纸），同坏路径再调仍重试"""
    import src.glass as glass_mod
    from src.glass import SurfaceBackground

    real_qpixmap = glass_mod.QPixmap
    loads = []

    def counting_qpixmap(*args):
        if args and args[0]:
            loads.append(args[0])
        return real_qpixmap(*args)

    monkeypatch.setattr(glass_mod, "QPixmap", counting_qpixmap)

    missing = str(tmp_path / "ghost.png")
    bg = SurfaceBackground()
    assert bg.configure(missing, "cover", 100, 0, 40) is True   # 挂图失败 → 关
    assert bg.enabled is False
    # 参数完全相同的第二次调用：仍重试读盘（文件可能已修好），但状态
    # 无实质变化 → False（宿主不白刷一遍）
    assert bg.configure(missing, "cover", 100, 0, 40) is False
    assert len(loads) == 2


# ====================================================================
# 2. 设置面板：壁纸步进器只走轻量刷新 + 即时落盘
# ====================================================================
class _FakeConfig:
    def __init__(self):
        self._d = {}
        self.saves = 0

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        self.saves += 1


@pytest.fixture(scope="module")
def panel(qapp):
    from src.settings_panel import SettingsPanel
    return SettingsPanel  # 占位：真正构造在用例内（要带记录型 host）


def _make_panel():
    from src.settings_panel import SettingsPanel
    cfg = _FakeConfig()
    calls = {"wallpaper": 0, "appearance": 0}
    host = types.SimpleNamespace(
        _config=cfg, current_theme="dark",
        refresh_wallpaper=lambda: calls.__setitem__(
            "wallpaper", calls["wallpaper"] + 1),
        refresh_appearance=lambda: calls.__setitem__(
            "appearance", calls["appearance"] + 1),
    )
    return SettingsPanel(host), cfg, calls


def test_wallpaper_param_change_uses_light_refresh_only(qapp):
    p, cfg, calls = _make_panel()
    # 初值即默认：直接触发一次应是无实际变化 → 不写盘不刷新
    p._on_wallpaper_param_changed()
    assert cfg.saves == 0
    assert calls == {"wallpaper": 0, "appearance": 0}

    # 转动「主题色遮罩」：轻量刷新恰一次，绝不走全量 refresh_appearance
    p._set_wallpaper_veil.setValue(60)
    assert cfg._d["wallpaper_veil"] == 60
    assert cfg.saves == 1
    assert calls["wallpaper"] == 1
    assert calls["appearance"] == 0

    # 连点（模拟步进器连发）：每档都轻量刷新，appearance 始终为 0
    p._set_wallpaper_blur.setValue(4)
    p._set_wallpaper_opacity.setValue(80)
    assert calls["wallpaper"] == 3
    assert calls["appearance"] == 0
    assert cfg._d["wallpaper_blur"] == 4
    assert cfg._d["wallpaper_opacity"] == 80
    assert cfg.saves == 3


def test_wallpaper_param_change_persists_values(qapp):
    """写盘语义：值进配置（save 由 FakeConfig 计数，见上一用例）；
    这里钉默认值兜底 —— 空配置下 get(key, default) 必须拿到出厂值"""
    p, cfg, calls = _make_panel()
    p._on_wallpaper_param_changed()
    # 全默认 → 无变化；但配置里此刻也没有这些键（不写无意义的盘）
    assert "wallpaper_veil" not in cfg._d
    p._set_wallpaper_veil.setValue(30)
    assert cfg.get("wallpaper_veil", wallpaper.DEFAULT_VEIL) == 30
