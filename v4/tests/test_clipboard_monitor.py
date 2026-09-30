# -*- coding: utf-8 -*-
"""剪贴板被动监听（clipboard_monitor）逻辑层回归。

不碰真实剪贴板：模块级 QApplication.clipboard() 与
_get_foreground_process_name() 全部 monkeypatch 替身；
剪贴板变化通过 FakeClipboard.dataChanged 信号驱动（等同真实回调路径）。

覆盖：
  1. 去重（相同内容连续触发只入池一次 / reset 清缓存 / 图片入库清文本缓存）
  2. 自写抑制（suppress_next 一次性 / put_text 自动抑制）
  3. 开关与启停（set_enabled 关闭不监听 / start-stop 断连 / 幂等）
  4. 分发判定（文本 vs 路径 vs 文件：单行 / 多行全路径 / 多行混合 / 引号包裹）
  5. 历史上限裁剪（trim_to_max FIFO + fragments_trimmed 信号）
  6. 应用过滤（D4：归一化匹配、过滤内容不污染去重缓存）
  7. 图片捕获（Y2：文本优先 / 开关 / 大小闸门 / 最小边长 / 内容哈希去重 / 降级）
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

import src.clipboard_monitor as clm  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor, _sha256_of_file  # noqa: E402
from src.fragment_manager import (  # noqa: E402
    FragmentManager, TYPE_CLIPBOARD_TEXT, TYPE_CLIPBOARD_PATH,
)


# ====================================================================
# 替身与夹具
# ====================================================================
class _StubQApp:
    """QApplication 替身：clipboard() 返回注入的 FakeClipboard，
    ClipboardMonitor.__init__ 因此绝不触碰真实剪贴板。"""

    _clip = None

    @classmethod
    def clipboard(cls):
        return cls._clip


class FakeMimeData:
    """QMimeData 替身：可声明 hasImage / 图片 MIME 格式 / 原始字节。"""

    def __init__(self, has_image=False, image_data=None,
                 formats=None, payload=None):
        self._has_image = has_image
        self._image_data = image_data
        self._formats = set(formats or ())
        self._payload = payload or {}

    def hasImage(self):
        return self._has_image

    def image(self):  # 保留接口完整性（模块未使用）
        return self._image_data

    def hasFormat(self, fmt):
        return fmt in self._formats

    def data(self, fmt):
        return self._payload.get(fmt)

    def imageData(self):
        return self._image_data


class _RaisingMimeData:
    """任何方法都抛异常的 MIME 替身（验证模块的防御性 try/except）。"""

    def hasImage(self):
        raise RuntimeError("boom")

    def hasFormat(self, _fmt):
        raise RuntimeError("boom")


class FakeClipboard(QObject):
    """QClipboard 替身：text/mimeData 可编程，dataChanged 手动发射。"""

    dataChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._text = ""
        self._mime = FakeMimeData()
        self.set_text_calls = []

    def text(self):
        return self._text

    def mimeData(self):
        return self._mime

    def setText(self, t):
        self.set_text_calls.append(t)
        self._text = t
        self.dataChanged.emit()


class FakeConfig:
    """ConfigManager 替身：内存 dict，绝不读写用户 config.json。"""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class FakeTempAssets:
    """TempAssetManager 替身：记录 add_asset 调用并回放预设结果。"""

    def __init__(self, results=None):
        self.calls = []          # {"path", "name", "src_exists"}
        self._results = list(results or [])
        self._fallback_id = 1

    def add_asset(self, path, display_name=""):
        self.calls.append({
            "path": path,
            "name": display_name,
            "src_exists": os.path.exists(path),
        })
        if self._results:
            return self._results.pop(0)
        aid = self._fallback_id
        self._fallback_id += 1
        return aid


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def make_image(w=20, h=20, color=0xFFFF0000):
    """离屏可用的内存 QImage（不依赖任何屏幕内容）。"""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(color)
    return img


def make_monitor(monkeypatch, *, config=None, tam=None, foreground=""):
    """构建全替身环境下的 ClipboardMonitor，返回 (monitor, clip, fm, box)。

    box 收集 4 个对外信号，便于断言 UI 通知行为。
    """
    clip = FakeClipboard()
    monkeypatch.setattr(clm, "QApplication", _StubQApp)
    monkeypatch.setattr(_StubQApp, "_clip", clip)
    # 前台进程名默认空串（过滤逻辑对空串放行），避免真实 ctypes 调用
    monkeypatch.setattr(clm, "_get_foreground_process_name",
                        lambda: foreground)
    fm = FragmentManager(":memory:")
    mon = ClipboardMonitor(fm, config_manager=config, temp_asset_manager=tam)
    mon.start()
    box = {"added": [], "paths": [], "trimmed": [], "images": [],
           "add_calls": []}
    # 监听层 spy：记录监听器实际发起的入池请求。
    # FragmentManager 自身还有「最近 12 条内容去重」（DUP_WINDOW），
    # spy 用于把监听器的去重行为与 FM 的去重行为区分开。
    _orig_text, _orig_path = fm.add_clipboard_text, fm.add_clipboard_path

    def _spy_text(content, source=""):
        box["add_calls"].append(("text", content))
        return _orig_text(content, source=source)

    def _spy_path(path, source=""):
        box["add_calls"].append(("path", path))
        return _orig_path(path, source=source)

    fm.add_clipboard_text = _spy_text
    fm.add_clipboard_path = _spy_path
    mon.fragment_added.connect(box["added"].append)
    mon.path_detected.connect(box["paths"].append)
    mon.fragments_trimmed.connect(box["trimmed"].append)
    mon.image_captured.connect(box["images"].append)
    return mon, clip, fm, box


def paste_text(clip, text):
    """模拟系统剪贴板变化为纯文本并触发 dataChanged。"""
    clip._text = text
    clip._mime = FakeMimeData()
    clip.dataChanged.emit()


def paste_image(clip, mime):
    """模拟系统剪贴板变化为纯图片（无文本）并触发 dataChanged。"""
    clip._text = ""
    clip._mime = mime
    clip.dataChanged.emit()


# ====================================================================
# 1. 去重
# ====================================================================
class TestDedup:
    def test_same_text_twice_captured_once(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        paste_text(clip, "hello")
        paste_text(clip, "hello")
        # 监听层：第二次未发起入池请求
        assert box["add_calls"] == [("text", "hello")]
        assert fm.count() == 1
        assert len(box["added"]) == 1

    def test_different_text_both_captured(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        paste_text(clip, "a")
        paste_text(clip, "b")
        assert len(box["add_calls"]) == 2
        assert fm.count() == 2

    def test_dedup_is_consecutive_content_only(self, qapp, monkeypatch):
        """监听层去重键是「上一条捕获内容」：A→B→A 的第二次 A 仍会请求入池。

        实现无时间维度（DEDUP_INTERVAL_MS 常量未被引用），此处按真实
        行为锁定：只有与上一条完全相同的内容才被监听器跳过。
        （随后 FragmentManager 还有自己的最近 12 条局部去重，与本层无关。）
        """
        mon, clip, fm, box = make_monitor(monkeypatch)
        paste_text(clip, "A")
        paste_text(clip, "B")
        paste_text(clip, "A")
        assert box["add_calls"] == [("text", "A"), ("text", "B"), ("text", "A")]

    def test_reset_last_text_reenables_capture(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        paste_text(clip, "same")
        mon.reset_last_text()
        paste_text(clip, "same")
        assert len(box["add_calls"]) == 2

    def test_reset_clears_image_hash_too(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch, tam=FakeTempAssets())
        tam = mon._temp_asset_manager
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert len(tam.calls) == 1
        mon.reset_last_text()
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert len(tam.calls) == 2   # 哈希缓存已清 → 同一张图重新入库


# ====================================================================
# 2. 自写抑制（避免循环捕获）
# ====================================================================
class TestSuppress:
    def test_suppress_next_skips_exactly_once(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        mon.suppress_next()
        paste_text(clip, "self-written")
        assert fm.count() == 0            # 第一次被抑制
        paste_text(clip, "self-written")
        assert fm.count() == 1            # 抑制是一次性的，之后恢复
        assert len(box["added"]) == 1

    def test_put_text_suppresses_and_writes(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        mon.put_text("merged")
        assert clip.set_text_calls == ["merged"]
        assert fm.count() == 0            # 自己写入不被捕获
        paste_text(clip, "merged")
        assert fm.count() == 1            # 之后的正常变化仍捕获


# ====================================================================
# 3. 开关与启停
# ====================================================================
class TestEnabledSwitch:
    def test_disabled_ignores_everything(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        mon.set_enabled(False)
        assert mon.is_enabled() is False
        paste_text(clip, "ignored")
        assert fm.count() == 0
        assert box["added"] == []
        mon.set_enabled(True)
        paste_text(clip, "kept")
        assert fm.count() == 1

    def test_disabled_does_not_update_dedup_cache(self, qapp, monkeypatch):
        """关闭期间的内容不进 _last_text（重新打开后直接可捕获）。"""
        mon, clip, fm, _ = make_monitor(monkeypatch)
        paste_text(clip, "first")
        mon.set_enabled(False)
        paste_text(clip, "second")
        mon.set_enabled(True)
        paste_text(clip, "second")
        assert fm.count() == 2

    def test_stop_disconnects_signal(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        mon.stop()
        paste_text(clip, "after-stop")
        assert fm.count() == 0
        mon.start()
        paste_text(clip, "after-restart")
        assert fm.count() == 1

    def test_start_stop_idempotent(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        mon.start()
        mon.start()                       # 重复 start 不重复连接
        paste_text(clip, "once")
        assert fm.count() == 1
        mon.stop()
        mon.stop()                        # 重复 stop 不报错
        paste_text(clip, "x")
        assert fm.count() == 1

    def test_start_without_clipboard_is_noop(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        mon.stop()
        monkeypatch.setattr(_StubQApp, "_clip", None)
        mon2 = ClipboardMonitor(fm)       # clipboard() 返回 None
        mon2.start()
        mon2.stop()                       # 未启动时 stop 也不报错
        mon2._on_data_changed()           # 直接回调也应安全返回

    def test_clipboard_none_guard(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        mon._clipboard = None
        mon._on_data_changed()            # 不崩溃即可


# ====================================================================
# 4. 分发判定：文本 vs 路径 vs 文件
# ====================================================================
class TestDispatch:
    def test_plain_text_captured_as_text(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        paste_text(clip, "普通文本，不是路径")
        frags = fm.get_all_fragments()
        assert len(frags) == 1
        assert frags[0].type == TYPE_CLIPBOARD_TEXT
        assert frags[0].content == "普通文本，不是路径"

    def test_existing_file_path_detected_as_path(self, qapp, monkeypatch, tmp_path):
        mon, clip, fm, box = make_monitor(monkeypatch)
        p = tmp_path / "doc.txt"
        p.write_text("x", encoding="utf-8")
        paste_text(clip, str(p))
        frags = fm.get_all_fragments()
        assert len(frags) == 1
        assert frags[0].type == TYPE_CLIPBOARD_PATH
        assert box["paths"] == [str(p)]

    def test_existing_dir_path_detected_as_path(self, qapp, monkeypatch, tmp_path):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        paste_text(clip, str(tmp_path))
        assert fm.get_all_fragments()[0].type == TYPE_CLIPBOARD_PATH

    def test_quoted_path_unwrapped(self, qapp, monkeypatch, tmp_path):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        p = tmp_path / "a b.txt"
        p.write_text("x", encoding="utf-8")
        paste_text(clip, f'"{p}"')
        assert fm.get_all_fragments()[0].type == TYPE_CLIPBOARD_PATH

    def test_nonexistent_path_is_plain_text(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch)
        paste_text(clip, r"C:\definitely\not\here.txt")
        frag = fm.get_all_fragments()[0]
        assert frag.type == TYPE_CLIPBOARD_TEXT
        assert box["paths"] == []

    def test_multiline_all_paths_split_per_line(self, qapp, monkeypatch, tmp_path):
        mon, clip, fm, box = make_monitor(monkeypatch)
        p1 = tmp_path / "one.txt"
        p2 = tmp_path / "two.txt"
        for p in (p1, p2):
            p.write_text("x", encoding="utf-8")
        paste_text(clip, f"{p1}\n{p2}")
        frags = fm.get_all_fragments()
        assert len(frags) == 2
        assert all(f.type == TYPE_CLIPBOARD_PATH for f in frags)
        assert set(box["paths"]) == {str(p1), str(p2)}
        assert len(box["added"]) == 2    # 每条路径各发一次 fragment_added

    def test_multiline_blank_lines_skipped(self, qapp, monkeypatch, tmp_path):
        mon, clip, fm, _ = make_monitor(monkeypatch)
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        for p in (p1, p2):
            p.write_text("x", encoding="utf-8")
        paste_text(clip, f"{p1}\n\n{p2}\n")
        assert fm.count() == 2

    def test_multiline_mixed_stays_one_text(self, qapp, monkeypatch, tmp_path):
        """部分是路径 → 整体作为一条文本碎片（不拆分）。"""
        mon, clip, fm, box = make_monitor(monkeypatch)
        p = tmp_path / "real.txt"
        p.write_text("x", encoding="utf-8")
        paste_text(clip, f"{p}\n这只是普通文字")
        frags = fm.get_all_fragments()
        assert len(frags) == 1
        assert frags[0].type == TYPE_CLIPBOARD_TEXT
        assert frags[0].content == f"{p}\n这只是普通文字"
        assert box["paths"] == []

    def test_is_valid_path_unit(self, qapp, monkeypatch, tmp_path):
        p = tmp_path / "ok.txt"
        p.write_text("x", encoding="utf-8")
        assert ClipboardMonitor._is_valid_path(str(p)) is True
        assert ClipboardMonitor._is_valid_path(f'  {p}  ') is True  # 去空白
        assert ClipboardMonitor._is_valid_path(f'"{p}"') is True    # 去引号
        assert ClipboardMonitor._is_valid_path("") is False
        assert ClipboardMonitor._is_valid_path("   ") is False
        assert ClipboardMonitor._is_valid_path('""') is False       # 剥引号后为空
        assert ClipboardMonitor._is_valid_path(str(tmp_path / "nope.txt")) is False


# ====================================================================
# 5. 历史上限裁剪
# ====================================================================
class TestTrim:
    def test_over_limit_trims_fifo_and_signals(self, qapp, monkeypatch):
        cfg = FakeConfig({"clipboard_max_items": 3})
        mon, clip, fm, box = make_monitor(monkeypatch, config=cfg)
        for i in range(5):
            paste_text(clip, f"content-{i}")
        assert fm.count() == 3
        assert box["trimmed"] == [1, 1]   # 每次插入最多淘汰 1 条，信号按次通报
        # FIFO：留下的是最新的 3 条
        contents = {f.content for f in fm.get_all_fragments()}
        assert contents == {"content-2", "content-3", "content-4"}

    def test_under_limit_no_trim_signal(self, qapp, monkeypatch):
        cfg = FakeConfig({"clipboard_max_items": 10})
        mon, clip, fm, box = make_monitor(monkeypatch, config=cfg)
        for i in range(3):
            paste_text(clip, f"c{i}")
        assert fm.count() == 3
        assert box["trimmed"] == []

    def test_no_config_no_trim(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch, config=None)
        for i in range(10):
            paste_text(clip, f"c{i}")
        assert fm.count() == 10
        assert box["trimmed"] == []

    def test_paths_also_counted_toward_limit(self, qapp, monkeypatch, tmp_path):
        cfg = FakeConfig({"clipboard_max_items": 2})
        mon, clip, fm, _ = make_monitor(monkeypatch, config=cfg)
        for i in range(4):
            p = tmp_path / f"f{i}.txt"
            p.write_text("x", encoding="utf-8")
            paste_text(clip, str(p))
        assert fm.count() == 2


# ====================================================================
# 6. 应用过滤（D4）
# ====================================================================
class TestAppFilter:
    def test_filtered_app_skips_capture(self, qapp, monkeypatch):
        cfg = FakeConfig({"clipboard_filter_apps": ["wechat"]})
        mon, clip, fm, box = make_monitor(
            monkeypatch, config=cfg, foreground=r"C:\Apps\WeChat.EXE")
        paste_text(clip, "secret")
        assert fm.count() == 0
        assert box["added"] == []

    def test_filter_normalizes_case_and_exe(self, qapp, monkeypatch):
        # 名单写 "WeChat.exe"、前台是完整大写路径 → 仍命中
        cfg = FakeConfig({"clipboard_filter_apps": ["WeChat.exe"]})
        mon, clip, fm, _ = make_monitor(
            monkeypatch, config=cfg, foreground=r"C:\APP\WECHAT.EXE")
        paste_text(clip, "x")
        assert fm.count() == 0

    def test_unfiltered_app_captures(self, qapp, monkeypatch):
        cfg = FakeConfig({"clipboard_filter_apps": ["wechat"]})
        mon, clip, fm, _ = make_monitor(
            monkeypatch, config=cfg, foreground=r"C:\Apps\notepad.exe")
        paste_text(clip, "ok")
        assert fm.count() == 1

    def test_filtered_content_keeps_dedup_cache_clean(self, qapp, monkeypatch):
        """被过滤的复制不触碰 _last_text：解除过滤后同内容可正常捕获。"""
        cfg = FakeConfig({"clipboard_filter_apps": ["wechat"]})
        mon, clip, fm, _ = make_monitor(
            monkeypatch, config=cfg, foreground=r"C:\Apps\WeChat.exe")
        paste_text(clip, "secret")
        assert fm.count() == 0
        mon._config = FakeConfig({})      # 模拟用户随后把名单清空
        paste_text(clip, "secret")
        assert fm.count() == 1

    def test_no_config_never_filters(self, qapp, monkeypatch):
        mon, clip, fm, _ = make_monitor(
            monkeypatch, config=None, foreground=r"C:\Apps\WeChat.exe")
        paste_text(clip, "x")
        assert fm.count() == 1

    def test_normalize_process_name_unit(self):
        n = ClipboardMonitor._normalize_process_name
        assert n(r"C:\Apps\WeChat.EXE") == "wechat"
        assert n("/usr/bin/app") == "app"
        assert n("  Notepad.exe  ") == "notepad"
        assert n("") == ""
        assert n("plain") == "plain"

    def test_is_filtered_app_unit(self, monkeypatch):
        mon = ClipboardMonitor.__new__(ClipboardMonitor)
        mon._config = FakeConfig({"clipboard_filter_apps": ["wechat", "QQ"]})
        assert mon._is_filtered_app(r"C:\x\wechat.exe") is True
        assert mon._is_filtered_app(r"C:\x\QQ.EXE") is True
        assert mon._is_filtered_app(r"C:\x\other.exe") is False
        assert mon._is_filtered_app("") is False          # 空前台放行
        mon._config = None
        assert mon._is_filtered_app(r"C:\x\wechat.exe") is False


# ====================================================================
# 7. 图片捕获（Y2，素材池）
# ====================================================================
class TestImageCapture:
    def test_image_only_goes_to_asset_pool(self, qapp, monkeypatch):
        tam = FakeTempAssets()
        mon, clip, fm, box = make_monitor(monkeypatch, tam=tam)
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert len(tam.calls) == 1
        assert box["images"] == [1]
        # 中转文件在 add_asset 时必须真实存在
        assert tam.calls[0]["src_exists"] is True
        assert tam.calls[0]["name"].startswith("剪贴板图片_")
        # 图片不进碎片池
        assert fm.count() == 0
        assert box["added"] == []

    def test_text_takes_priority_over_image(self, qapp, monkeypatch):
        """带文本的复制（如 Excel 文字+位图）按文本处理，不收图。"""
        tam = FakeTempAssets()
        mon, clip, fm, box = make_monitor(monkeypatch, tam=tam)
        clip._mime = FakeMimeData(has_image=True, image_data=make_image())
        paste_text(clip, "带格式的文字")
        assert fm.count() == 1
        assert tam.calls == []
        assert box["images"] == []

    def test_image_capture_disabled_by_config(self, qapp, monkeypatch):
        cfg = FakeConfig({"clipboard_capture_images": False})
        tam = FakeTempAssets()
        mon, clip, fm, box = make_monitor(monkeypatch, config=cfg, tam=tam)
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert tam.calls == []
        assert box["images"] == []

    def test_no_asset_manager_degrades_silently(self, qapp, monkeypatch):
        mon, clip, fm, box = make_monitor(monkeypatch, tam=None)
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert box["images"] == []
        assert fm.count() == 0

    def test_duplicate_image_deduped_by_hash(self, qapp, monkeypatch):
        tam = FakeTempAssets()
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        md = FakeMimeData(has_image=True, image_data=make_image())
        paste_image(clip, md)
        paste_image(clip, md)             # 同一张图重复触发
        assert len(tam.calls) == 1
        assert box["images"] == [1]

    def test_different_images_both_captured(self, qapp, monkeypatch):
        tam = FakeTempAssets()
        mon, clip, _, _ = make_monitor(monkeypatch, tam=tam)
        paste_image(clip, FakeMimeData(has_image=True,
                                       image_data=make_image(color=0xFF00FF00)))
        paste_image(clip, FakeMimeData(has_image=True,
                                       image_data=make_image(color=0xFF0000FF)))
        assert len(tam.calls) == 2

    def test_tiny_image_rejected(self, qapp, monkeypatch):
        """小于 IMAGE_MIN_SIDE 的垃圾位图（取色器 1×1）不入库。"""
        tam = FakeTempAssets()
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        paste_image(clip, FakeMimeData(has_image=True,
                                       image_data=make_image(2, 2)))
        assert tam.calls == []
        assert box["images"] == []

    def test_oversize_image_rejected_by_size_gate(self, qapp, monkeypatch):
        """超过 IMAGE_MAX_BYTES 的图片不入库（用小上限模拟超大图）。"""
        tam = FakeTempAssets()
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        monkeypatch.setattr(ClipboardMonitor, "IMAGE_MAX_BYTES", 10)
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert tam.calls == []
        assert box["images"] == []

    def test_raw_mime_payload_stored(self, qapp, monkeypatch):
        """无标准图片对象时按原始 MIME 字节落盘入库。"""
        tam = FakeTempAssets()
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        payload = b"\x89PNG\r\n\x1a\n" + b"fake-bytes" * 4
        md = FakeMimeData(formats={"image/png"}, payload={"image/png": payload})
        paste_image(clip, md)
        assert len(tam.calls) == 1
        assert box["images"] == [1]

    def test_empty_raw_payload_rejected(self, qapp, monkeypatch):
        tam = FakeTempAssets()
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        md = FakeMimeData(formats={"image/png"}, payload={"image/png": b""})
        paste_image(clip, md)
        assert tam.calls == []
        assert box["images"] == []

    def test_add_asset_failure_is_silent(self, qapp, monkeypatch):
        """素材池返回非法 id（None/0/负数）→ 不发信号、不崩溃。"""
        tam = FakeTempAssets(results=[None])
        mon, clip, _, box = make_monitor(monkeypatch, tam=tam)
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert len(tam.calls) == 1        # 尝试过入库
        assert box["images"] == []

    def test_image_capture_clears_text_dedup_cache(self, qapp, monkeypatch):
        """图片入库后清 _last_text：同一文本随后会再次进入解析管线。

        FragmentManager 自带 12 条窗口去重，同内容仍不会产生第二条碎片，
        故这里断言「监听器放行」（进入 _parse_and_add），而非碎片池新增。
        """
        tam = FakeTempAssets()
        mon, clip, fm, box = make_monitor(monkeypatch, tam=tam)
        paste_text(clip, "x")
        paste_image(clip, FakeMimeData(has_image=True, image_data=make_image()))
        assert box["images"] == [1]       # 图片确实入库（清缓存的前提）
        seen = []
        monkeypatch.setattr(mon, "_parse_and_add", seen.append)
        paste_text(clip, "x")             # 与首条相同：缓存未清会被去重跳过
        assert seen == ["x"]

    def test_mime_has_image_unit(self):
        has = ClipboardMonitor._mime_has_image
        assert has(FakeMimeData(has_image=True)) is True
        assert has(FakeMimeData(formats={"image/jpeg"})) is True
        assert has(FakeMimeData()) is False
        assert has(_RaisingMimeData()) is False    # 异常安全兜底

    def test_sha256_of_file_unit(self, tmp_path):
        p = tmp_path / "bin.dat"
        p.write_bytes(b"floatpulse")
        digest = _sha256_of_file(str(p))
        assert len(digest) == 64
        assert _sha256_of_file(str(tmp_path / "missing.bin")) == ""
