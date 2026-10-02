# -*- coding: utf-8 -*-
"""软件图标尺寸收口 + shell 大图标提取 —— 护栏测试。

背景（2026-10-02，用户第二次反馈「图标大小调不动」）：
用户在软件导航页看到「卡片跟着设置放大了，里面的图标纹丝不动」。

根因有两层，都在本文件钉死：
  1. ``QIcon.pixmap()`` **从不放大** —— 源帧小于请求尺寸就原样返回；
     对 ``.lnk`` 快捷方式更极端：无论请求 40/76/108/120 一律只回 32px
     （125% DPI 实测 40px）。而 ``availableSizes()`` / ``actualSize()``
     都谎报大尺寸，上游无从察觉。真实平台实测：card 60→140 图标恒定
     39×40，跨度 0。
  2. 因此 ``extract_exe_icon`` 必须①优先走 shell jumbo 图像列表拿 256px
     真高分图（``src.win_icons``），②无论如何都经 ``_fit_icon`` 把结果
     收口到请求尺寸。

修复后真实平台实测：card 60→140 图标 38→116（跨度 78），真正跟随。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import MINI_ICON_DEFAULT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _logical(pixmap):
    """位图的逻辑边长（Qt 绘制时用的尺寸）。"""
    dpr = pixmap.devicePixelRatio() or 1.0
    return pixmap.width() / dpr


def _content_bbox(pixmap):
    """位图非透明像素外接框的逻辑尺寸 —— 「图标看起来多大」。"""
    img = pixmap.toImage()
    dpr = pixmap.devicePixelRatio() or 1.0
    minx, miny, maxx, maxy = 10 ** 6, 10 ** 6, -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 16:
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
    if maxx < 0:
        return (0, 0)
    return ((maxx - minx + 1) / dpr, (maxy - miny + 1) / dpr)


def _solid_pixmap(size, dpr=1.0):
    """造一张实心位图（模拟「源图标只有 size px」）。"""
    from PyQt6.QtGui import QPixmap, QPainter, QColor
    from PyQt6.QtCore import Qt
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setBrush(QColor("#5BC0BE"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


# ====================================================================
# 1. _fit_icon：尺寸收口
# ====================================================================
class TestFitIcon:
    def _fit(self, pm, size):
        from src.widget_app_launcher import _fit_icon
        return _fit_icon(pm, size)

    def test_upscales_small_source(self, qapp):
        """★ 核心：源图小于请求尺寸时必须放大（QIcon 不放大就是这个坑）。"""
        out = self._fit(_solid_pixmap(32), 76)
        assert abs(_logical(out) - 76) < 0.5, \
            "32px 源图请求 76px 仍未放大：逻辑尺寸 %.1f" % _logical(out)

    def test_big_source_downscales(self, qapp):
        out = self._fit(_solid_pixmap(256), 108)
        assert abs(_logical(out) - 108) < 0.5

    def test_exact_size_untouched(self, qapp):
        src = _solid_pixmap(76)
        out = self._fit(src, 76)
        assert out is src, "已经对的位图不该无谓重采样"

    def test_keeps_high_dpr_source(self, qapp):
        """QIcon 用 dpr 凑逻辑尺寸的位图要原样保留（别再降级重采样）。"""
        src = _solid_pixmap(256, dpr=2.3703703703703702)   # 逻辑 ≈108
        out = self._fit(src, 108)
        assert out is src
        assert abs(_logical(out) - 108) < 0.5

    def test_null_pixmap_safe(self, qapp):
        from PyQt6.QtGui import QPixmap
        out = self._fit(QPixmap(), 64)
        assert out.isNull()

    def test_clamps_absurd_size(self, qapp):
        """尺寸为 0 也要给出非空位图（不能返回 0×0 让界面空白）。"""
        out = self._fit(_solid_pixmap(32), 0)
        assert not out.isNull()
        assert out.width() >= 1


# ====================================================================
# 2. extract_exe_icon：真实路径必须收口
# ====================================================================
class _FakeProvider:
    """QFileIconProvider 替身：模拟「系统只给 32px 图标」的 exe。"""

    def icon(self, _info):
        return _FakeIcon()


class _FakeIcon:
    def pixmap(self, _size):
        return _solid_pixmap(32)


class TestExtractExeIcon:
    @pytest.fixture
    def fake_exe(self, tmp_path):
        p = tmp_path / "demo.exe"
        p.write_bytes(b"MZ")
        return str(p)

    def _extract(self, path, size, monkeypatch, shell_image=None):
        import src.widget_app_launcher as wal
        from PyQt6.QtGui import QPixmapCache
        QPixmapCache.clear()
        monkeypatch.setattr(wal.win_icons, "shell_icon_image",
                            lambda _p: shell_image)
        monkeypatch.setattr(wal, "QFileIconProvider",
                            lambda: _FakeProvider())
        return wal.extract_exe_icon(path, size)

    def test_qt_fallback_still_fits_size(self, fake_exe, qapp, monkeypatch):
        """★ 核心：shell 拿不到大图时，Qt 兜底路径也必须收口到请求尺寸。

        修复前这里会原样返回 32px —— 用户看到的就是「卡片放大、图标不动」。
        """
        for size in (40, 76, 108, 120):
            pm = self._extract(fake_exe, size, monkeypatch)
            assert abs(_logical(pm) - size) < 0.5, \
                "请求 %d px 得到逻辑 %.1f px" % (size, _logical(pm))

    def test_shell_image_preferred_and_fitted(self, fake_exe, qapp, monkeypatch):
        """shell 有图时优先用，且同样收口到请求尺寸。"""
        img = _solid_pixmap(256).toImage()
        pm = self._extract(fake_exe, 108, monkeypatch, shell_image=img)
        assert abs(_logical(pm) - 108) < 0.5

    def test_visible_icon_grows_with_card(self, fake_exe, qapp, monkeypatch):
        """★ 端到端判据：card 小 ↔ 大，图标可见尺寸必须真的变。

        这正是用户报的现象（真实平台实测修复前跨度 0）。
        """
        img = _solid_pixmap(256).toImage()
        small = self._extract(fake_exe, 40, monkeypatch, shell_image=img)
        large = self._extract(fake_exe, 120, monkeypatch, shell_image=img)
        bw_small = _content_bbox(small)[0]
        bw_large = _content_bbox(large)[0]
        assert bw_large > bw_small * 2, \
            "图标没跟随缩放：40px 请求 → 可见 %.0f，120px 请求 → 可见 %.0f" % (
                bw_small, bw_large)

    def test_missing_path_placeholder(self, qapp):
        from src.widget_app_launcher import extract_exe_icon
        pm = extract_exe_icon("", 64)
        assert abs(_logical(pm) - 64) < 0.5

    def test_cache_hit_same_object(self, fake_exe, qapp, monkeypatch):
        first = self._extract(fake_exe, 76, monkeypatch)
        second = self._extract2(fake_exe, 76)
        assert first.cacheKey() == second.cacheKey()

    def _extract2(self, path, size):
        from src.widget_app_launcher import extract_exe_icon
        return extract_exe_icon(path, size)

    def test_source_level_pins(self):
        """源码级钉子：两条防线（shell 大图 + 尺寸收口）都不许被删掉。"""
        import inspect
        import src.widget_app_launcher as wal
        src = inspect.getsource(wal.extract_exe_icon)
        assert "shell_icon_image" in src, \
            "extract_exe_icon 丢了 shell 大图标路径（.lnk 会退回 32px）"
        assert "_fit_icon" in src, \
            "extract_exe_icon 丢了尺寸收口（QIcon 不放大 = 图标卡在源尺寸）"
        assert "_fit_icon" in inspect.getsource(wal.load_icon_pixmap), \
            "load_icon_pixmap 也要走同一套收口（口径唯一）"


# ====================================================================
# 3. win_icons：shell 大图标提取
# ====================================================================
class TestWinIcons:
    def test_never_raises_on_odd_input(self, qapp):
        from src import win_icons
        win_icons.clear_cache()
        for bad in ("", None, 0, [], "\x00bad"):
            assert win_icons.shell_icon_image(bad) is None

    def test_missing_file_returns_none(self, qapp):
        from src import win_icons
        win_icons.clear_cache()
        assert win_icons.shell_icon_image(
            "C:/definitely/not/here/xx.exe") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")
    def test_real_exe_gives_large_image(self, qapp):
        from src import win_icons
        if not win_icons.is_available():
            pytest.skip("shell 大图标 API 不可用")
        win_icons.clear_cache()
        exe = r"C:\Windows\System32\notepad.exe"
        if not os.path.exists(exe):
            pytest.skip("无 notepad.exe")
        img = win_icons.shell_icon_image(exe)
        if img is None:
            pytest.skip("该环境 shell 未提供 jumbo 图标")
        assert min(img.width(), img.height()) >= 128, \
            "jumbo 图像列表应给出 ≥128px（实测 256），得到 %dx%d" % (
                img.width(), img.height())

    @pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")
    def test_cache_returns_same_object(self, qapp):
        from src import win_icons
        if not win_icons.is_available():
            pytest.skip("shell 大图标 API 不可用")
        exe = r"C:\Windows\System32\notepad.exe"
        if not os.path.exists(exe) or win_icons.shell_icon_image(exe) is None:
            pytest.skip("该环境 shell 未提供 jumbo 图标")
        assert win_icons.shell_icon_image(exe) is win_icons.shell_icon_image(exe)

    def test_clear_cache(self, qapp):
        from src import win_icons
        win_icons.clear_cache()
        assert win_icons._cache == {}

    def test_is_available_matches_platform(self, qapp):
        from src import win_icons
        if sys.platform != "win32":
            assert win_icons.is_available() is False
        else:
            assert isinstance(win_icons.is_available(), bool)


# ====================================================================
# 4. 小卡片侧：图标尺寸仍由 app_mini_icon_size 驱动（上一轮修复不回归）
# ====================================================================
class TestMiniCardStillWired:
    def test_mini_card_uses_same_helper(self):
        """小卡片走同一套 extract_exe_icon → 自动享受大图标 + 尺寸收口。"""
        import inspect
        from src.card_window import CardWindow
        src = inspect.getsource(CardWindow._refresh_app_page)
        assert "extract_exe_icon" in src
        assert "load_icon_pixmap" in src

    def test_default_icon_size_constant(self):
        assert MINI_ICON_DEFAULT == 36