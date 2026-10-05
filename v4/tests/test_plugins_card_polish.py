# -*- coding: utf-8 -*-
"""插件中心卡片降噪（2026-10-01，用户拍板「降噪保留 + 动作保持逐行」）。

钉死的行为：
  1. clip_text 纯函数：短文本原样、超长截断加省略号、空串安全
  2. 长描述（>DESC_MAX=64 字）卡面截断 + 悬停提示含全文；短描述原样无提示
  3. 依赖（requires）不再占卡面行——卡面文本无「依赖:」，
     悬停提示（卡片整体 tooltip）含完整依赖清单（有无能力行都一样）
  4. 能力徽章：每个 capability 一个 pluginCapBadge 胶囊（短文案），
     完整语义在徽章 tooltip；旧「能力: …」文字行不再出现
  5. 标题行拆分：名称与版本是两个独立 QLabel（版本弱化为灰小字）
  6. 商店卡 / 在线市场卡同款降噪（徽章行 + 版本拆分）
  7. 网格回归：降噪后卡片照常进双列网格（_plugin_cards 计数不变）
"""

import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel      # noqa: E402

from src.plugins_panel import (                        # noqa: E402
    CAP_BADGES, DESC_MAX, PluginsPanel, clip_text,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeLoader:
    def __init__(self, plugins):
        self._plugins = plugins

    def loaded_plugins(self):
        return list(self._plugins)

    # 面板按属性读取（loader.plugins_dir），必须是 @property（项目已知坑）
    @property
    def plugins_dir(self):
        return os.path.join(os.path.sep, "nonexistent_plugins_dir")

    @property
    def store_dir(self):
        return os.path.join(os.path.sep, "nonexistent_store_dir")


class _FakeHost:
    def __init__(self, loader=None):
        self._config = {"plugins_enabled": True}
        self._loader = loader

    @property
    def plugin_loader(self):
        return self._loader

    @property
    def config(self):
        return self._config


def _make_loaded_plugin(description="", caps=(), requires=("PyQt6",),
                        plugin_id="demo", name="演示插件", version="1.2.3"):
    """LoadedPlugin 形状替身（鸭子类型：面板只读公开字段）"""
    action = types.SimpleNamespace(
        id="demo.act", title="做件事", hotkey="Ctrl+Alt+D", menu=True)
    manifest = {
        "id": plugin_id, "name": name, "version": version,
        "entry": "main.py", "requires": list(requires),
        "capabilities": list(caps),
        "actions": [{"id": "demo.act", "title": "做件事",
                     "hotkey": "Ctrl+Alt+D", "menu": True}],
        "description": description,
    }
    return types.SimpleNamespace(
        plugin_id=plugin_id, name=name, version=version,
        path=os.path.join(os.path.sep, "nonexistent_demo_dir"),
        plugin=types.SimpleNamespace(), manifest=manifest,
        actions_raw=[action])


def _plugin_cards(panel):
    cards = []
    for i in range(panel._cards_layout.count()):
        w = panel._cards_layout.itemAt(i).widget()
        if w is not None and w.objectName() == "pluginCard":
            cards.append(w)
    return cards


def _joined(card):
    return " ".join(lbl.text() for lbl in card.findChildren(QLabel))


def _badges(card):
    return [lbl for lbl in card.findChildren(QLabel)
            if lbl.objectName() == "pluginCapBadge"]


# ====================================================================
# 1. clip_text 纯函数
# ====================================================================
class TestClipText:
    def test_short_text_untouched(self):
        assert clip_text("一句话", 64) == "一句话"

    def test_long_text_truncated_with_ellipsis(self):
        out = clip_text("字" * 100, 64)
        assert len(out) == 65 and out.endswith("…")
        assert out.startswith("字" * 20)

    def test_exact_length_no_ellipsis(self):
        assert clip_text("字" * 64, 64) == "字" * 64

    def test_none_and_empty_safe(self):
        assert clip_text("", 64) == ""
        assert clip_text(None, 64) == ""      # type: ignore[arg-type]

    def test_desc_max_constant(self):
        # 2026-10-02 卡片重设计：卡面描述上限 48 → 64 字（双列卡宽下仍是
        # 「一行半」，但能容纳更多真实插件的完整一句介绍，减少半句截断）。
        assert DESC_MAX == 64


# ====================================================================
# 2. 已装插件卡片降噪
# ====================================================================
class TestCardPolish:
    def _panel_with(self, qapp, **kw):
        lp = _make_loaded_plugin(**kw)
        return PluginsPanel(_FakeHost(_FakeLoader([lp])))

    def test_long_description_truncated_with_tooltip(self, qapp):
        long_desc = "这是很长的描述" * 12      # 84 字 > 64
        panel = self._panel_with(qapp, description=long_desc)
        card = _plugin_cards(panel)[0]
        desc_labels = [lb for lb in card.findChildren(QLabel)
                       if lb.objectName() == "pluginCardDesc"]
        assert len(desc_labels) == 1
        shown = desc_labels[0].text()
        assert shown.endswith("…") and len(shown) == DESC_MAX + 1
        assert desc_labels[0].toolTip() == long_desc   # 全文进悬停
        assert not shown == long_desc

    def test_short_description_plain_without_tooltip(self, qapp):
        panel = self._panel_with(qapp, description="整理碎片用")
        card = _plugin_cards(panel)[0]
        dl = [lb for lb in card.findChildren(QLabel)
              if lb.objectName() == "pluginCardDesc"][0]
        assert dl.text() == "整理碎片用"
        assert dl.toolTip() == ""

    def test_requires_never_on_card_face(self, qapp):
        """依赖不占卡面行（有无能力行都一样），全文在卡片悬停提示里"""
        for caps in ((), ("network", "write")):
            panel = self._panel_with(
                qapp, requires=("PyQt6.QtCore", "PyQt6.QtWidgets"),
                caps=caps)
            card = _plugin_cards(panel)[0]
            assert "依赖:" not in _joined(card)
            assert "依赖:" in card.toolTip()
            assert "PyQt6.QtCore" in card.toolTip()

    def test_truncated_description_full_text_in_card_tooltip(self, qapp):
        long_desc = "很长" * 40
        panel = self._panel_with(qapp, description=long_desc)
        card = _plugin_cards(panel)[0]
        assert long_desc in card.toolTip()

    def test_capability_badges_short_labels(self, qapp):
        panel = self._panel_with(
            qapp, caps=("network", "write", "manage", "ai"))
        card = _plugin_cards(panel)[0]
        badges = _badges(card)
        assert len(badges) == 4
        assert [b.text() for b in badges] == [
            CAP_BADGES["network"], CAP_BADGES["write"],
            CAP_BADGES["manage"], CAP_BADGES["ai"]]
        # 完整语义进徽章悬停提示；旧「能力:」文字行不再出现
        assert "app.log 可审计" in badges[0].toolTip()
        assert "能力:" not in _joined(card)

    def test_no_caps_no_badge_row(self, qapp):
        panel = self._panel_with(qapp, caps=())
        card = _plugin_cards(panel)[0]
        assert _badges(card) == []

    def test_title_and_version_split(self, qapp):
        """名称（大字）与版本（弱化小字）是两个独立标签"""
        panel = self._panel_with(qapp)
        card = _plugin_cards(panel)[0]
        titles = [lb.text() for lb in card.findChildren(QLabel)
                  if lb.objectName() == "pluginCardTitle"]
        ids = [lb.text() for lb in card.findChildren(QLabel)
               if lb.objectName() == "pluginCardId"]
        assert titles == ["演示插件"]
        assert "v1.2.3" in ids
        assert "演示插件  v1.2.3" not in _joined(card)

    def test_grid_regression_card_count(self, qapp):
        """降噪后卡片照常进网格（含徽章/截断路径）"""
        panel = PluginsPanel(_FakeHost(_FakeLoader([
            _make_loaded_plugin(description="长" * 100,
                                caps=("network",), plugin_id="a",
                                name="甲", version="1.0"),
            _make_loaded_plugin(description="短", caps=(), plugin_id="b",
                                name="乙", version="2.0"),
        ])))
        assert len(_plugin_cards(panel)) == 2


# ====================================================================
# 3. 商店卡 / 在线卡同款降噪
# ====================================================================
class TestStoreCardPolish:
    def _panel(self, qapp):
        return PluginsPanel(_FakeHost(_FakeLoader([])))

    def test_store_card_badges_and_split(self, qapp):
        panel = self._panel(qapp)
        entry = types.SimpleNamespace(
            plugin_id="demo-a", name="商店插件", version="1.0.0",
            description="测试商店条目", usable=True, installed=False,
            error="", filename="demo-a.fpplug",
            path="", manifest={"capabilities": ["network", "ai"]})
        card = panel._make_store_card(entry)
        badges = _badges(card)
        assert len(badges) == 2
        assert "能力:" not in _joined(card)
        titles = [lb.text() for lb in card.findChildren(QLabel)
                  if lb.objectName() == "pluginStoreTitle"]
        assert titles == ["商店插件"]
        assert "v1.0.0" in _joined(card)
        assert "包: demo-a.fpplug" in _joined(card)   # 源包名保留文字行

    def test_online_card_badges_and_truncation(self, qapp):
        from src.plugins_panel import PluginStoreDialog
        panel = self._panel(qapp)
        item = {
            "id": "remote-x", "name": "远程插件", "version": "2.0",
            "description": "很长的远程描述" * 15,
            "capabilities": ["write"], "hotkeys": ["Ctrl+Alt+R"],
            "size": 2048, "file": "remote-x.fpplug", "sha256": "",
        }
        # _make_online_card 定义在 PluginStoreDialog 上（构建弹窗卡），
        # 但只依赖 self._make_cap_badge（panel 同款方法）——借 panel 实例
        # 调用未绑定方法即可单测卡片骨架，不必拉起整个商店弹窗。
        card = PluginStoreDialog._make_online_card(panel, item)
        badges = _badges(card)
        assert len(badges) == 1 and badges[0].text() == CAP_BADGES["write"]
        dl = [lb for lb in card.findChildren(QLabel)
              if lb.objectName() == "pluginCardDesc"][0]
        assert dl.text().endswith("…")
        assert dl.toolTip() == item["description"]
        assert "热键: Ctrl+Alt+R" in _joined(card)
        assert "2.0 KB" in _joined(card)
