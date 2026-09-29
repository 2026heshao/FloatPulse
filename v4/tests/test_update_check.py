# -*- coding: utf-8 -*-
"""应用内检查更新（update_checker + 设置页接线）回归测试。

钉死的行为（2026-09-29）：
  1. 纯逻辑：tag 解析 / 版本比较（宁漏报不误报）/ 响应提取 / 请求头
  2. 设置页接线：关于分类有「软件更新」卡；点击检查 → 请求打
     GitHub Releases API 且带 User-Agent；新版 → 出现下载页按钮；
     失败/等版本 → 按钮恢复、无假出口
  3. 「程序不联网」承诺：不点按钮就没有任何网络动作（懒创建桥）
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import update_checker as uc                     # noqa: E402
from src.app_version import APP_VERSION                  # noqa: E402
from src.update_checker import (                         # noqa: E402
    RELEASES_API_URL, check_headers, extract_tag, is_newer, parse_tag,
)


# ====================================================================
# 纯逻辑：parse_tag / is_newer / extract_tag / check_headers
# ====================================================================
class TestParseTag:
    @pytest.mark.parametrize("tag,expect", [
        ("v4.7.0", (4, 7, 0)),
        ("V4.7.0", (4, 7, 0)),
        ("4.7.0", (4, 7, 0)),
        ("v4.7", (4, 7)),
        ("v4.7.0-beta", (4, 7, 0)),      # 取 - 前的数字段
        (" v4.7.0 ", (4, 7, 0)),         # 容忍空白
    ])
    def test_valid(self, tag, expect):
        assert parse_tag(tag) == expect

    @pytest.mark.parametrize("bad", ["", None, "abc", "v", "4.x.0", 470])
    def test_invalid_returns_none(self, bad):
        assert parse_tag(bad) is None


class TestIsNewer:
    def test_newer_patch(self):
        assert is_newer("v4.7.1") is True

    def test_equal_is_not_newer(self):
        assert is_newer(f"v{APP_VERSION}") is False

    def test_older_is_not_newer(self):
        assert is_newer("v0.0.1") is False

    def test_padding_equalizes(self):
        assert is_newer("v4.8", "4.8.0") is False    # (4,8) ≡ (4,8,0)
        assert is_newer("v4.8", "4.7.9") is True

    def test_unparseable_never_newer(self):
        # 宁漏报不误报：怪 tag 一律 False，不让用户白跑下载页
        assert is_newer("nonsense") is False
        assert is_newer(None) is False


class TestExtractTag:
    def test_valid_json(self):
        body = json.dumps({"tag_name": "v5.0.0", "name": "x"})
        assert extract_tag(body) == "v5.0.0"

    @pytest.mark.parametrize("bad", ["", None, "not json", '{"other": 1}',
                                     '[1, 2]', '{"tag_name": 3}'])
    def test_bad_inputs_empty(self, bad):
        assert extract_tag(bad) == ""


def test_headers_have_user_agent():
    headers = check_headers()
    assert "FloatPulse/" in headers["User-Agent"]
    assert "vnd.github" in headers["Accept"]


def test_api_url_points_at_repo():
    assert RELEASES_API_URL.startswith(
        "https://api.github.com/repos/2026heshao/FloatPulse/")
    assert RELEASES_API_URL.endswith("/releases/latest")


# ====================================================================
# 设置页接线（离屏 QApplication + 假 host，同 test_settings_nav 模式）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeConfig:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def save(self):
        pass


@pytest.fixture(scope="module")
def panel(qapp):
    from src.settings_panel import SettingsPanel
    host = types.SimpleNamespace(_config=_FakeConfig(), current_theme="dark")
    p = SettingsPanel(host)
    # 更新卡在「关于」分类：isVisibleTo 对未激活的 stack 页恒 False，
    # 先切过去再做可见性断言
    p.show_category("about")
    return p


def _install_fake_getter(monkeypatch, panel, responses):
    """把设置页的异步 GET 桥换成假体：记录调用、按序吐响应。

    必须先清 ``panel._upd_getter`` —— 桥是懒创建后缓存的（生产语义：
    复用同一条管道），而 panel 是模块级共享 fixture，不清的话后续测试
    点击时走的还是上一个测试弹空了的旧假体，``responses.pop(0)`` 抛
    IndexError，PyQt6 槽内未捕获异常会 qFatal 中止整个 pytest 进程。
    """
    panel._upd_getter = None
    calls = []

    def fake_getter():
        def get(url, headers=None, timeout=30.0, on_done=None):
            calls.append({"url": url, "headers": headers})
            assert callable(on_done)
            on_done(responses.pop(0))
            return True
        return get

    monkeypatch.setattr("src.settings_panel.make_async_getter", fake_getter)
    return calls


class TestSettingsUpdateCard:
    def test_update_card_built(self, panel):
        assert panel._upd_btn.isVisibleTo(panel)
        assert not panel._upd_open_btn.isVisibleTo(panel)   # 无新版不给假出口
        assert panel._upd_getter is None                    # 懒创建：未点击零联网

    def test_check_calls_releases_api_with_ua(self, panel, monkeypatch):
        calls = _install_fake_getter(monkeypatch, panel, [
            {"ok": True, "status": 200,
             "body": json.dumps({"tag_name": f"v{APP_VERSION}"})}])
        panel._upd_btn.click()
        assert len(calls) == 1
        assert calls[0]["url"] == RELEASES_API_URL
        assert "FloatPulse/" in calls[0]["headers"]["User-Agent"]
        assert "已是最新" in panel._upd_status.text()

    def test_newer_version_reveals_download_button(self, panel, monkeypatch):
        _install_fake_getter(monkeypatch, panel, [
            {"ok": True, "status": 200,
             "body": json.dumps({"tag_name": "v99.0.0"})}])
        panel._upd_btn.click()
        assert "v99.0.0" in panel._upd_status.text()
        assert panel._upd_open_btn.isVisibleTo(panel)

    def test_offline_failure_shows_hint_and_recovers(self, panel, monkeypatch):
        _install_fake_getter(monkeypatch, panel, [
            {"ok": False, "status": 0, "error":
             "URLError(ConnectionRefusedError)"}])
        panel._upd_btn.click()
        assert panel._upd_btn.isEnabled()                   # 按钮恢复可重试
        assert "检查失败" in panel._upd_status.text()
        assert "离线不影响任何功能" in panel._upd_status.text()
        assert not panel._upd_open_btn.isVisibleTo(panel)

    def test_timeout_gets_retry_hint(self, panel, monkeypatch):
        """超时是常见瞬时态，给「可重试」而非「无法连接」的误导性文案"""
        _install_fake_getter(monkeypatch, panel, [
            {"ok": False, "status": 0, "error": "timed out"}])
        panel._upd_btn.click()
        assert "网络超时" in panel._upd_status.text()

    def test_rate_limited_gets_specific_hint(self, panel, monkeypatch):
        _install_fake_getter(monkeypatch, panel, [
            {"ok": False, "status": 403, "error": "HTTP 403"}])
        panel._upd_btn.click()
        assert "限流" in panel._upd_status.text()

    def test_bad_response_body_is_tolerated(self, panel, monkeypatch):
        _install_fake_getter(monkeypatch, panel, [
            {"ok": True, "status": 200, "body": "not json"}])
        panel._upd_btn.click()
        assert panel._upd_btn.isEnabled()
        assert "格式异常" in panel._upd_status.text()
        assert not panel._upd_open_btn.isVisibleTo(panel)

    def test_open_button_wired_to_downloads_page(self, panel, monkeypatch):
        """下载页按钮不带网络：只调系统打开（本测只验接线，不真开浏览器）"""
        opened = []
        monkeypatch.setattr("src.settings_panel.os.startfile",
                            lambda url: opened.append(url))
        panel._upd_open_btn.click()
        assert opened == [uc.RELEASES_PAGE_URL]
