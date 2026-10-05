# -*- coding: utf-8 -*-
"""插件中心页面（PluginsPanel）单元测试。

覆盖：
  1. sanitize_nav_order：8 键新排列 / 7 键旧配置无损升级 / 非法回退
  2. validate_manifest：可选 description 字段（缺省 / 合法 / 非字符串）
  3. PluginsPanel：空态渲染 / 卡片渲染（假 loader）/ 总闸关闭提示
  4. 使用说明查看器：程序内 Markdown 渲染（不依赖系统文件关联）
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import (           # noqa: E402
    NAV_PAGE_KEYS, DEFAULT_NAV_ORDER, sanitize_nav_order,
)
from src.plugin_loader import validate_manifest  # noqa: E402


# ====================================================================
# sanitize_nav_order：新 8 键 + 旧 7 键兼容
# ====================================================================
class TestSanitizeNavOrder:
    def test_full_8key_order_accepted(self):
        order = ["tasks", "notes", "fragments", "knowledge",
                 "assets", "apps", "nav", "plugins"]
        assert sanitize_nav_order(order) == order

    def test_default_order_valid(self):
        assert sanitize_nav_order(list(DEFAULT_NAV_ORDER)) == \
            list(DEFAULT_NAV_ORDER)

    def test_legacy_7key_order_appends_plugins(self):
        """旧版 7 键自定义顺序 → 追加 plugins，用户排序不丢"""
        legacy = ["nav", "tasks", "fragments", "knowledge",
                  "assets", "apps", "notes"]
        out = sanitize_nav_order(legacy)
        assert out is not None
        assert out[:7] == legacy
        assert out[7] == "plugins"
        assert set(out) == set(NAV_PAGE_KEYS)

    def test_wrong_length_returns_none(self):
        assert sanitize_nav_order(["fragments", "tasks"]) is None

    def test_dup_or_missing_key_returns_none(self):
        assert sanitize_nav_order(
            ["fragments"] * 7 + ["tasks"]) is None
        bad = list(DEFAULT_NAV_ORDER)
        bad[0] = "nope"
        assert sanitize_nav_order(bad) is None

    def test_non_list_returns_none(self):
        assert sanitize_nav_order("fragments") is None
        assert sanitize_nav_order(None) is None


# ====================================================================
# validate_manifest：可选 description
# ====================================================================
def _base_manifest(**extra):
    data = {
        "id": "demo", "name": "演示插件", "version": "1.0.0",
        "entry": "main.py",
        "actions": [{"id": "demo.act", "title": "做件事"}],
    }
    data.update(extra)
    return data


class TestManifestDescription:
    def test_without_description_defaults_empty(self):
        m, err = validate_manifest(_base_manifest())
        assert m is not None and err == ""
        assert m["description"] == ""

    def test_with_description_kept(self):
        m, err = validate_manifest(
            _base_manifest(description="  一句话说明  "))
        assert m is not None and err == ""
        assert m["description"] == "一句话说明"

    def test_none_description_becomes_empty(self):
        m, err = validate_manifest(_base_manifest(description=None))
        assert m is not None and err == ""
        assert m["description"] == ""

    def test_non_string_description_rejected(self):
        m, err = validate_manifest(_base_manifest(description=123))
        assert m is None and "description" in err


# ====================================================================
# PluginsPanel：空态 / 卡片 / 总闸提示（离屏 QApplication）
# ====================================================================
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_loaded_plugin(tmp_path, description="", doc=None,
                        readme=False, requires=("PyQt6",),
                        plugin_id="demo", name="演示插件", version="1.2.3"):
    """构造 LoadedPlugin 形状的替身（不 import 真插件模块）"""
    import types as _types
    # 动作替身（鸭子类型：面板只读 title/hotkey/menu 属性 + enabled 开关）
    _state = {"on": True}
    action = _types.SimpleNamespace(
        id=f"{plugin_id}.act", title="做件事", hotkey="Ctrl+Alt+D", menu=True,
        enabled=lambda: _state["on"],
        set_enabled=lambda v: _state.__setitem__("on", bool(v)))

    plugin = _types.SimpleNamespace()
    if doc is not None:
        # SimpleNamespace 是 immutable type 不能设 __doc__，用一次性子类
        plugin = type("_FakePlugin", (), {"__doc__": doc})()

    pdir = tmp_path / plugin_id
    pdir.mkdir(exist_ok=True)
    if readme:
        (pdir / "README.md").write_text("# demo", encoding="utf-8")
    manifest = {
        "id": plugin_id, "name": name, "version": version,
        "entry": "main.py", "requires": list(requires),
        "actions": [{"id": f"{plugin_id}.act", "title": "做件事",
                     "hotkey": "Ctrl+Alt+D", "menu": True}],
        "description": description,
    }
    return _types.SimpleNamespace(
        plugin_id=plugin_id, name=name, version=version,
        path=str(pdir), plugin=plugin, manifest=manifest,
        actions_raw=[action], actions_ok=1)


def _plugin_cards(panel):
    """_cards_layout 里的「已装插件卡片」列表。

    布局结构（2026-09-29 起）为双列 QGridLayout：网格只装已装卡片本体，
    尾部 stretch 在外层 vbox（2026-09-28 起商店区已拆入 PluginStoreDialog
    独立弹窗）。不能再用固定下标取卡片，必须按 objectName == "pluginCard"
    过滤；QGridLayout 的 count()/itemAt() 与 VBox 同构，本助手无需改动。
    """
    cards = []
    for i in range(panel._cards_layout.count()):
        w = panel._cards_layout.itemAt(i).widget()
        if w is not None and w.objectName() == "pluginCard":
            cards.append(w)
    return cards


def _store_cards(dialog):
    """商店弹窗 _list_layout 里的商店卡片（按 objectName 过滤，同上理）"""
    cards = []
    for i in range(dialog._list_layout.count()):
        w = dialog._list_layout.itemAt(i).widget()
        if w is not None and w.objectName() == "pluginStoreCard":
            cards.append(w)
    return cards


def _make_store_entry(tmp_path, plugin_id="demo", installed=False,
                      usable=True, error=""):
    """构造 StoreEntry 形状的替身（面板/弹窗只读这些公开字段）"""
    return types.SimpleNamespace(
        plugin_id=plugin_id, name="演示插件", version="1.0.0",
        description="测试商店条目", usable=usable, installed=installed,
        error=error, filename=f"{plugin_id}.fpplug",
        path=str(tmp_path / f"{plugin_id}.fpplug"), manifest={})


class _FakeLoader:
    def __init__(self, plugins):
        self._plugins = plugins

    def loaded_plugins(self):
        return list(self._plugins)

    # 生产侧 PluginLoader.plugins_dir / store_dir 均为 @property（见
    # plugin_loader.py:419/427），替身必须同契约，否则面板按属性取到
    # bound method，setToolTip() 抛 TypeError。
    @property
    def plugins_dir(self):
        return os.path.join(os.path.sep, "nonexistent_plugins_dir")

    @property
    def store_dir(self):
        return os.path.join(os.path.sep, "nonexistent_store_dir")


class _FakeHost:
    """最小 host 替身：_config + plugin_loader 属性"""

    def __init__(self, loader=None, plugins_enabled=True):
        self._config = {"plugins_enabled": plugins_enabled}
        self._loader = loader

    @property
    def plugin_loader(self):
        return self._loader

    @property
    def config(self):
        return self._config


class TestPluginsPanel:
    def test_empty_state(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        panel = PluginsPanel(_FakeHost(loader=None))
        assert panel._empty_label.isVisibleTo(panel)
        assert "共 0 个插件" in panel._count_label.text()
        # 卡片区除 store_box（无商店包时隐藏）与 stretch 外无卡片
        assert _plugin_cards(panel) == []

    def test_card_rendered_with_actions(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path, description="整理碎片用")
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        assert not panel._empty_label.isVisibleTo(panel)
        assert "共 1 个插件" in panel._count_label.text()
        cards = _plugin_cards(panel)
        assert len(cards) == 1
        card = cards[0]
        assert card is not None
        joined = " ".join(lbl.text() for lbl in card.findChildren(QLabel))
        assert "演示插件" in joined and "v1.2.3" in joined
        assert "整理碎片用" in joined          # manifest.description 优先
        assert "做件事" in joined and "Ctrl+Alt+D" in joined
        assert "右键菜单" in joined

    def test_description_fallback_docstring(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(
            tmp_path, description="", doc="周报草稿生成器。")
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        card = _plugin_cards(panel)[0]
        texts = " ".join(lbl.text() for lbl in card.findChildren(QLabel))
        assert "周报草稿生成器" in texts

    def test_gate_off_shows_hint(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path)
        panel = PluginsPanel(
            _FakeHost(loader=_FakeLoader([lp]), plugins_enabled=False))
        assert panel._gate_label.isVisibleTo(panel)
        # 总闸关闭 → 即使 loader 有插件也不渲染卡片
        assert _plugin_cards(panel) == []

    def test_refresh_rebuilds(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        host = _FakeHost(loader=None)
        panel = PluginsPanel(host)
        assert _plugin_cards(panel) == []
        host._loader = _FakeLoader([_make_loaded_plugin(tmp_path)])
        panel.refresh()
        assert len(_plugin_cards(panel)) == 1


# ====================================================================
# 启停状态持久化（plugins_disabled）—— 面板侧写配置
# ====================================================================
class _FakeConfig:
    """ConfigManager 的最小替身：只实现 set/get/save"""

    def __init__(self, **initial):
        self.data = dict(initial)
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


class _ToggleHost:
    """带真 ConfigManager 替身 + 假注册表的 host"""

    def __init__(self, loader, config):
        self._config = config
        self._loader = loader

    @property
    def plugin_loader(self):
        return self._loader

    @property
    def config(self):
        return self._config

    def _rebuild_context_menu(self):
        pass


class _FakeAction:
    def __init__(self, aid="demo.act"):
        self.id = aid
        self.title = "做件事"
        self.hotkey = None
        self.menu = True
        self._en = True

    def enabled(self):
        return self._en

    def set_enabled(self, on):
        self._en = bool(on)


class _FakeRegistry:
    def __init__(self, actions):
        self._actions = {a.id: a for a in actions}

    def set_enabled(self, aid, on):
        act = self._actions.get(aid)
        if act is None:
            return False
        act.set_enabled(on)
        return True


class TestDisabledPersistence:
    def _panel(self, qapp, config):
        from src.plugins_panel import PluginsPanel
        act = _FakeAction()
        loader = _FakeLoader([])
        loader.registry = _FakeRegistry([act])
        panel = PluginsPanel(_ToggleHost(loader, config))
        return panel, act

    def test_disable_writes_config(self, qapp):
        cfg = _FakeConfig(plugins_disabled=[])
        panel, act = self._panel(qapp, cfg)
        panel._toggle_plugin([act], False, plugin_id="demo")
        assert cfg.get("plugins_disabled") == ["demo"]
        assert cfg.saved == 1                      # 落盘只调一次

    def test_enable_removes_from_config(self, qapp):
        cfg = _FakeConfig(plugins_disabled=["demo", "other"])
        panel, act = self._panel(qapp, cfg)
        panel._toggle_plugin([act], True, plugin_id="demo")
        assert cfg.get("plugins_disabled") == ["other"]

    def test_disable_twice_no_duplicate_no_extra_save(self, qapp):
        cfg = _FakeConfig(plugins_disabled=["demo"])
        panel, act = self._panel(qapp, cfg)
        panel._toggle_plugin([act], False, plugin_id="demo")
        assert cfg.get("plugins_disabled") == ["demo"]
        assert cfg.saved == 0                      # 值没变就不写盘

    def test_unknown_or_empty_plugin_id_is_noop(self, qapp):
        cfg = _FakeConfig(plugins_disabled=[])
        panel, act = self._panel(qapp, cfg)
        panel._toggle_plugin([act], False, plugin_id="")
        assert cfg.get("plugins_disabled") == []
        assert cfg.saved == 0

    def test_dirty_config_value_is_sanitized(self, qapp):
        """配置里混入非字符串（手改坏了）→ 只留下合法项，不崩"""
        cfg = _FakeConfig(plugins_disabled=["keep", 123, None])
        panel, act = self._panel(qapp, cfg)
        panel._toggle_plugin([act], False, plugin_id="demo")
        assert cfg.get("plugins_disabled") == ["keep", "demo"]

    def test_no_config_manager_does_not_raise(self, qapp):
        from src.plugins_panel import PluginsPanel

        class _NoCfgHost:
            def __init__(self):
                self._config = None
                self._loader = None

            @property
            def plugin_loader(self):
                return None

            @property
            def config(self):
                return self._config

        _ = _FakeAction()  # 构造不抛异常即视为通过（宿主无 config 时动作仍可实例化）
        panel = PluginsPanel(_NoCfgHost())
        panel._persist_disabled("demo", False)     # 不应抛异常


# ====================================================================
# 使用说明 md：探测 / 卡面摘要不上卡（2026-10-01 用户拍板，详情看 md）
# ====================================================================
class TestUsageDoc:
    def test_find_usage_prefers_chinese_name(self, tmp_path):
        from src.plugins_panel import find_usage_file
        d = tmp_path / "p1"
        d.mkdir()
        assert find_usage_file(str(d)) == ""          # 无文件 → 空串
        (d / "README.md").write_text("# a", encoding="utf-8")
        assert find_usage_file(str(d)).endswith("README.md")
        (d / "使用说明.md").write_text("# b", encoding="utf-8")
        assert find_usage_file(str(d)).endswith("使用说明.md")

    def test_card_hides_usage_summary_but_keeps_button(self, qapp, tmp_path):
        """包内有使用说明 md：卡面**不**展示「📖 摘要」（与描述重复），
        但「查看使用说明」按钮仍在——详情走程序内 md 查看器"""
        from PyQt6.QtWidgets import QLabel, QPushButton
        from src.plugins_panel import PluginsPanel
        d = tmp_path / "demo"
        d.mkdir()
        (d / "使用说明.md").write_text(
            "# 使用说明\n\n选定范围后一键汇总任务与碎片生成周报草稿。\n",
            encoding="utf-8")
        lp = _make_loaded_plugin(tmp_path)
        lp.path = str(d)
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        card = _plugin_cards(panel)[0]
        texts = " ".join(lbl.text() for lbl in card.findChildren(QLabel))
        assert "选定范围后一键汇总" not in texts       # 摘要不再上卡
        assert "📖" not in texts
        btn_texts = " ".join(b.text() for b in card.findChildren(QPushButton))
        assert "查看使用说明" in btn_texts              # 按钮保留

    def test_card_without_usage_doc_has_no_summary(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path)              # 无任何 md
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        card = _plugin_cards(panel)[0]
        texts = " ".join(lbl.text() for lbl in card.findChildren(QLabel))
        assert "📖" not in texts


# ====================================================================
# 使用说明查看器：程序内 Markdown 渲染，不依赖系统文件关联
# ====================================================================
class TestUsageViewer:
    def test_viewer_builds_with_markdown(self, qapp, tmp_path):
        """查看器含 QTextBrowser#usageViewer，markdown 已渲染，footer 双按钮"""
        from PyQt6.QtWidgets import QTextBrowser, QPushButton
        from src.plugins_panel import _build_usage_viewer
        p = tmp_path / "使用说明.md"
        text = "# 标题\n\n一键汇总任务与碎片。\n"
        p.write_text(text, encoding="utf-8")
        dlg = _build_usage_viewer(_FakeHost(), str(p), text)
        try:
            viewers = dlg.findChildren(QTextBrowser)
            assert len(viewers) == 1
            v = viewers[0]
            assert v.objectName() == "usageViewer"
            assert "一键汇总任务与碎片" in v.toMarkdown()
            btn_texts = [b.text() for b in dlg.findChildren(QPushButton)]
            assert any("用系统程序打开" in t for t in btn_texts)
            assert "关闭" in btn_texts
        finally:
            dlg.deleteLater()

    def test_open_file_renders_viewer(self, qapp, tmp_path, monkeypatch):
        """点「查看使用说明」→ 构建查看器（文件全文）并 exec，不走 startfile"""
        import src.plugins_panel as pp
        from src.plugins_panel import PluginsPanel
        p = tmp_path / "README.md"
        p.write_text("# demo\n正文内容", encoding="utf-8")
        calls = []

        class _FakeDlg:
            def exec(self):
                calls.append("exec")

        def _fake_builder(host, path, text):
            calls.append((host, path, text))
            return _FakeDlg()

        monkeypatch.setattr(pp, "_build_usage_viewer", _fake_builder)
        panel = PluginsPanel(_FakeHost())
        panel._on_open_file(str(p))
        assert calls[0][1] == str(p)
        assert "正文内容" in calls[0][2]
        assert calls[-1] == "exec"

    def test_open_file_unreadable_falls_back_to_startfile(
            self, qapp, tmp_path, monkeypatch):
        """文件读不了（不存在/非 utf-8）→ 委托 _startfile_warn 兜底"""
        import src.plugins_panel as pp
        from src.plugins_panel import PluginsPanel
        called = []
        monkeypatch.setattr(
            pp, "_startfile_warn",
            lambda parent, path: called.append((parent, path)))
        panel = PluginsPanel(_FakeHost())
        missing = str(tmp_path / "不存在.md")
        panel._on_open_file(missing)
        assert called == [(panel, missing)]

    def test_startfile_warn_shows_messagebox(self, qapp, monkeypatch):
        """无文件关联时 _startfile_warn 弹提示而非抛异常"""
        import src.plugins_panel as pp
        warned = []

        class _FakeMB:
            @staticmethod
            def warning(parent, title, text):
                warned.append((title, text))

        monkeypatch.setattr(pp, "GlassMessageBox", _FakeMB)

        def _boom(path):
            raise OSError("no association")

        monkeypatch.setattr(pp.os, "startfile", _boom)
        pp._startfile_warn(None, "x.md")          # 不抛异常即通过
        assert len(warned) == 1
        assert "x.md" in warned[0][1]

    def test_viewer_system_button_calls_startfile(self, qapp, tmp_path,
                                                  monkeypatch):
        """查看器 footer「用系统程序打开」点击 → os.startfile 真被调用"""
        from PyQt6.QtWidgets import QPushButton
        import src.plugins_panel as pp
        from src.plugins_panel import _build_usage_viewer
        p = tmp_path / "README.md"
        p.write_text("# demo", encoding="utf-8")
        opened = []
        monkeypatch.setattr(pp.os, "startfile",
                            lambda path: opened.append(path))
        dlg = _build_usage_viewer(_FakeHost(), str(p), "# demo")
        try:
            btns = [b for b in dlg.findChildren(QPushButton)
                    if "用系统程序打开" in b.text()]
            assert len(btns) == 1
            btns[0].click()
            assert opened == [str(p)]
        finally:
            dlg.deleteLater()


class _StoreFakeLoader(_FakeLoader):
    """带商店能力的 loader 替身：scan_store / install_from_store / rescan"""

    def __init__(self, plugins, entries):
        super().__init__(plugins)
        self._entries = entries
        self.install_calls = []

    def scan_store(self):
        return list(self._entries)

    def install_from_store(self, plugin_id):
        self.install_calls.append(plugin_id)
        for e in self._entries:
            if getattr(e, "plugin_id", "") == plugin_id:
                e.installed = True
        return True, f"已安装 {plugin_id}"

    def rescan(self):
        return list(self._plugins)


class TestStoreDialog:
    """插件商店独立弹窗（2026-09-28 拆出，取代页面内嵌商店区）"""

    def test_panel_has_no_inline_store(self, qapp, tmp_path):
        # 回归钉子：商店区必须离开插件中心页
        from src.plugins_panel import PluginsPanel
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([])))
        names = [panel._cards_layout.itemAt(i).widget().objectName()
                 for i in range(panel._cards_layout.count())
                 if panel._cards_layout.itemAt(i).widget() is not None]
        assert "pluginStoreBox" not in names

    def test_dialog_renders_cards_and_states(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QPushButton
        from src.plugins_panel import PluginStoreDialog, PluginsPanel
        entries = [
            _make_store_entry(tmp_path, "demo-a", installed=False),
            _make_store_entry(tmp_path, "demo-b", installed=True),
        ]
        panel = PluginsPanel(_FakeHost(loader=_StoreFakeLoader([], entries)))
        dlg = PluginStoreDialog(panel)
        try:
            # 已安装的包不重复列出（页面本体已有同款插件卡片），只列未安装
            cards = _store_cards(dlg)
            assert len(cards) == 1
            btns_a = [b for b in cards[0].findChildren(QPushButton)
                      if b.text() == "安装"]
            assert len(btns_a) == 1 and btns_a[0].isEnabled()
            # 已装数量压缩成一行提示
            hint = dlg._installed_hint.text()
            assert "1 个插件包已安装" in hint
            assert dlg._installed_hint.isVisibleTo(dlg)
            assert not dlg._empty_label.isVisibleTo(dlg)
            # 弹窗里没有已安装条目的卡片形态（「已安装」按钮不该存在）
            assert not [b for c in _store_cards(dlg)
                        for b in c.findChildren(QPushButton)
                        if "已安装" in b.text()]
        finally:
            dlg.deleteLater()

    def test_dialog_empty_store_shows_hint(self, qapp, tmp_path):
        from src.plugins_panel import PluginStoreDialog, PluginsPanel
        panel = PluginsPanel(_FakeHost(loader=_StoreFakeLoader([], [])))
        dlg = PluginStoreDialog(panel)
        try:
            assert _store_cards(dlg) == []
            assert dlg._empty_label.isVisibleTo(dlg)
            assert "还没有可安装的插件包" in dlg._empty_label.text()
            assert not dlg._installed_hint.isVisibleTo(dlg)
        finally:
            dlg.deleteLater()

    def test_dialog_all_installed_shows_fallback_hint(self, qapp, tmp_path):
        """商店里的包全装过了：不列卡片，空态文案换成「卸载后可重装」"""
        from src.plugins_panel import PluginStoreDialog, PluginsPanel
        entries = [_make_store_entry(tmp_path, "demo-b", installed=True)]
        panel = PluginsPanel(_FakeHost(loader=_StoreFakeLoader([], entries)))
        dlg = PluginStoreDialog(panel)
        try:
            assert _store_cards(dlg) == []
            assert dlg._empty_label.isVisibleTo(dlg)
            assert "都已安装" in dlg._empty_label.text()
            assert "重装" in dlg._empty_label.text()
        finally:
            dlg.deleteLater()

    def test_dialog_bad_package_shows_error(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel, QPushButton
        from src.plugins_panel import PluginStoreDialog, PluginsPanel
        entries = [_make_store_entry(tmp_path, "bad", usable=False,
                                     error="manifest 不合法")]
        panel = PluginsPanel(_FakeHost(loader=_StoreFakeLoader([], entries)))
        dlg = PluginStoreDialog(panel)
        try:
            cards = _store_cards(dlg)
            assert len(cards) == 1
            joined = " ".join(lbl.text() for lbl in cards[0].findChildren(QLabel))
            assert "manifest 不合法" in joined
            # 坏包不给可点的安装按钮（文案是「无法安装」且禁用）
            assert [b for b in cards[0].findChildren(QPushButton)
                    if b.text() == "安装"] == []
        finally:
            dlg.deleteLater()

    def test_install_updates_dialog(self, qapp, tmp_path, monkeypatch):
        """点安装 → loader 装包 → 弹窗卡片即时翻到「已安装」"""
        from PyQt6.QtWidgets import QPushButton
        from src.plugins_panel import PluginStoreDialog, PluginsPanel
        entries = [_make_store_entry(tmp_path, "demo-a", installed=False)]
        loader = _StoreFakeLoader([], entries)
        panel = PluginsPanel(_FakeHost(loader=loader))
        dlg = PluginStoreDialog(panel)
        # 真实流程里 _on_open_store_dialog 会登记弹窗引用，这里对齐它
        panel._store_dialog = dlg
        try:
            # 安装成功路径已降级为 show_toast（宿主无该能力时静默跳过），
            # 不再弹模态提示框，无需替身（2026-10 统一改造）
            card = _store_cards(dlg)[0]
            btn = [b for b in card.findChildren(QPushButton)
                   if b.text() == "安装"][0]
            btn.click()
            assert loader.install_calls == ["demo-a"]
            # reload 后：该包已安装 → 不再列卡片，回落为已装数量提示
            assert _store_cards(dlg) == []
            assert "1 个插件包已安装" in dlg._installed_hint.text()
            assert "都已安装" in dlg._empty_label.text()
        finally:
            dlg.deleteLater()


# ====================================================================
# 双列网格（2026-09-29）：行优先摆放 + 窄视口回落单列
# ====================================================================
class TestTwoColumnGrid:
    def _make_panel(self, qapp, tmp_path, n=4):
        from src.plugins_panel import PluginsPanel
        lps = [_make_loaded_plugin(tmp_path, description=f"第{i}个")
               for i in range(n)]
        return PluginsPanel(_FakeHost(loader=_FakeLoader(lps)))

    def test_row_major_two_columns(self, qapp, tmp_path):
        panel = self._make_panel(qapp, tmp_path, 4)
        panel._reflow_cards(1024)     # 直接以视口宽度驱动（确定性，免事件循环）
        grid = panel._cards_layout
        assert [grid.getItemPosition(i)[:2] for i in range(grid.count())] == [
            (0, 0), (0, 1), (1, 0), (1, 1)]

    def test_falls_back_to_single_column_when_narrow(self, qapp, tmp_path):
        panel = self._make_panel(qapp, tmp_path, 3)
        panel._reflow_cards(700)
        grid = panel._cards_layout
        assert [grid.getItemPosition(i)[:2] for i in range(grid.count())] == [
            (0, 0), (1, 0), (2, 0)]

    def test_reflow_back_to_two_columns(self, qapp, tmp_path):
        panel = self._make_panel(qapp, tmp_path, 2)
        panel._reflow_cards(700)
        panel._reflow_cards(1024)
        grid = panel._cards_layout
        assert [grid.getItemPosition(i)[:2] for i in range(grid.count())] == [
            (0, 0), (0, 1)]

    def test_resize_event_drives_reflow(self, qapp, tmp_path):
        """真实事件路径：滚动区 resizeEvent（按视口宽度）驱动列数。

        视口是唯一可靠信号——widgetResizable 会把容器钳在网格最小宽上，
        双列最小宽大于回落阈值时容器永远收不到「变窄」的 resize（离屏
        无字体环境实测复现），只有滚动区本体每次尺寸变化都走 resizeEvent。
        """
        from PyQt6.QtWidgets import QApplication
        panel = self._make_panel(qapp, tmp_path, 2)
        panel.resize(1280, 700)
        panel.show()
        for _ in range(5):
            QApplication.processEvents()
        assert panel._card_cols == 2
        panel.resize(500, 700)
        for _ in range(5):
            QApplication.processEvents()
        assert panel._card_cols == 1


# ====================================================================
# 插件中心三件套（2026-10-04，设计稿 plugins-center-redesign 收尾）：
#   ① ⋯ 渐进披露菜单（ID / 依赖 / 注册页面 / 复制 ID）
#   ② 加载失败折叠条（默认收起，点击展开）
#   ③ 搜索 + 全部/已启用/已停用分段筛选
# ====================================================================
class TestCardMoreMenu:
    def _panel_with(self, qapp, tmp_path, **kw):
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path, **kw)
        return PluginsPanel(_FakeHost(loader=_FakeLoader([lp]))), lp

    def test_more_button_on_card(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QPushButton
        panel, _ = self._panel_with(qapp, tmp_path)
        cards = _plugin_cards(panel)
        btns = [b for b in cards[0].findChildren(QPushButton)
                if b.objectName() == "pluginMoreBtn"]
        assert len(btns) == 1

    def test_menu_rows_and_copy_action(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        panel, lp = self._panel_with(
            qapp, tmp_path,
            requires=("PyQt6.QtCore", "PyQt6.QtWidgets"))
        lp.manifest["page"] = {"title": "🤖 演示页"}
        menu = panel._build_card_menu(lp)
        try:
            # 信息行以 QWidgetAction 内嵌 QLabel 承载（展示不是命令）
            texts = " ".join(lb.text() for lb in menu.findChildren(QLabel))
            assert "插件 ID" in texts and "demo" in texts
            assert "依赖" in texts and "PyQt6.QtCore" in texts
            assert "注册页面" in texts and "演示页" in texts   # emoji 已剥离
            assert "🤖" not in texts
            # 命令项：复制插件 ID
            copy_acts = [a for a in menu.actions() if a.text() == "复制插件 ID"]
            assert len(copy_acts) == 1 and copy_acts[0].isEnabled()
        finally:
            menu.deleteLater()

    def test_copy_action_writes_clipboard(self, qapp, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QApplication
        panel, lp = self._panel_with(qapp, tmp_path)
        QApplication.clipboard().setText("")
        panel._copy_plugin_id(lp.plugin_id)
        assert QApplication.clipboard().text() == "demo"
        # 空 id 是 no-op（不崩、不清剪贴板）
        panel._copy_plugin_id("")
        assert QApplication.clipboard().text() == "demo"

    def test_menu_without_requires_and_page(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        panel, lp = self._panel_with(qapp, tmp_path, requires=())
        menu = panel._build_card_menu(lp)
        try:
            texts = " ".join(lb.text() for lb in menu.findChildren(QLabel))
            assert "（未声明）" in texts and "（无插件页）" in texts
        finally:
            menu.deleteLater()


class TestErrorCollapse:
    def _panel_with_errors(self, qapp, tmp_path):
        """借 _FakeLoader 无 load_errors → 直接构造含失败项的 panel 不便，
        用「好 loader + 手动注入错误列表」的轻量替身走 refresh 路径"""
        from src.plugins_panel import PluginsPanel

        class _ErrLoader(_FakeLoader):
            def __init__(self, plugins, errors):
                super().__init__(plugins)
                self._errors = errors

            def load_errors(self):
                return list(self._errors)

        err = types.SimpleNamespace(
            folder="bad-one", name="坏插件", stage="import",
            reason="entry 不存在", hint="检查 manifest.json",
            path=str(tmp_path / "bad-one"))
        lp = _make_loaded_plugin(tmp_path)
        panel = PluginsPanel(_FakeHost(loader=_ErrLoader([lp], [err])))
        return panel

    def test_collapsed_by_default(self, qapp, tmp_path):
        panel = self._panel_with_errors(qapp, tmp_path)
        assert panel._error_box.isHidden() is False      # 提示条在
        assert panel._error_body.isVisibleTo(panel) is False  # 卡片收起
        assert "1 个" in panel._error_toggle.text()
        assert panel._error_toggle.isCheckable()

    def test_toggle_expands_and_collapses(self, qapp, tmp_path):
        panel = self._panel_with_errors(qapp, tmp_path)
        panel._error_toggle.setChecked(True)
        assert panel._error_body.isVisibleTo(panel) is True
        assert "点击收起" in panel._error_toggle.text()
        panel._error_toggle.setChecked(False)
        assert panel._error_body.isVisibleTo(panel) is False
        assert "点击展开" in panel._error_toggle.text()

    def test_expand_state_survives_refresh(self, qapp, tmp_path):
        panel = self._panel_with_errors(qapp, tmp_path)
        panel._error_toggle.setChecked(True)
        panel.refresh()
        assert panel._error_expanded is True
        assert panel._error_body.isVisibleTo(panel) is True


class TestSearchAndSegFilter:
    def _panel(self, qapp, tmp_path, n=3):
        from src.plugins_panel import PluginsPanel
        lps = [_make_loaded_plugin(tmp_path, description=f"第{i}个",
                                   plugin_id=f"p{i}", name=f"插件{i}")
               for i in range(n)]
        return PluginsPanel(_FakeHost(loader=_FakeLoader(lps)))

    def test_search_by_name(self, qapp, tmp_path):
        panel = self._panel(qapp, tmp_path)
        assert len(panel._visible_cards) == 3
        panel._search_input.setText("插件1")
        assert [w for w in panel._visible_cards] != []
        assert len(panel._visible_cards) == 1
        panel._search_input.setText("")
        assert len(panel._visible_cards) == 3

    def test_search_by_action_title(self, qapp, tmp_path):
        panel = self._panel(qapp, tmp_path, n=1)
        panel._search_input.setText("做件事")          # 动作 title
        assert len(panel._visible_cards) == 1
        panel._search_input.setText("不存在的动作")
        assert panel._visible_cards == []
        assert panel._filter_hint.isVisibleTo(panel) is True
        panel._search_input.setText("")
        assert panel._filter_hint.isVisibleTo(panel) is False

    def test_search_case_insensitive_and_id(self, qapp, tmp_path):
        panel = self._panel(qapp, tmp_path, n=2)
        panel._search_input.setText("P1")              # plugin_id 大写
        assert len(panel._visible_cards) == 1

    def test_seg_filter_on_off(self, qapp, tmp_path):
        panel = self._panel(qapp, tmp_path, n=2)
        # 停用第一张卡对应的插件动作（走 enabled() → _status_of 同源判定）
        panel._card_records[0][1].actions_raw[0].set_enabled(False)
        panel.refresh()
        panel._seg_buttons["on"].setChecked(True)
        panel._apply_filter()
        assert len(panel._visible_cards) == 1
        assert panel._visible_cards[0] is not panel._card_records[0][0]
        panel._seg_buttons["off"].setChecked(True)
        panel._apply_filter()
        assert len(panel._visible_cards) == 1
        assert panel._visible_cards[0] is panel._card_records[0][0]
        panel._seg_buttons["all"].setChecked(True)
        panel._apply_filter()
        assert len(panel._visible_cards) == 2

    def test_search_and_seg_stack(self, qapp, tmp_path):
        panel = self._panel(qapp, tmp_path, n=2)
        panel._card_records[0][1].actions_raw[0].set_enabled(False)
        panel.refresh()
        panel._seg_buttons["on"].setChecked(True)
        panel._search_input.setText("插件1")           # p1 是启用中的那张
        panel._apply_filter()
        assert len(panel._visible_cards) == 1
        panel._search_input.setText("插件0")           # 停用卡被 seg 滤掉
        panel._apply_filter()
        assert panel._visible_cards == []
        assert panel._filter_hint.isVisibleTo(panel) is True

    def test_filter_does_not_rebuild_cards(self, qapp, tmp_path):
        """过滤只改可见性：切换前后是同一批 QWidget 实例"""
        panel = self._panel(qapp, tmp_path)
        ids_before = [id(c) for c, _ in panel._card_records]
        panel._search_input.setText("插件0")
        panel._search_input.setText("")
        assert [id(c) for c, _ in panel._card_records] == ids_before

    def test_grid_positions_compact_after_filter(self, qapp, tmp_path):
        """滤掉中间一张后，网格占位收紧无空洞（可见卡行优先 0,0 → 0,1）"""
        panel = self._panel(qapp, tmp_path, n=3)
        panel._reflow_cards(1024)                      # 双列
        panel._search_input.setText("插件")            # 全部匹配
        panel._search_input.setText("插件1")           # 只剩 1 张
        grid = panel._cards_layout
        assert [grid.getItemPosition(i)[:2] for i in range(grid.count())] == [
            (0, 0)]


# ====================================================================
# 重新扫描两步走（2026-10-05 黑闪修复 + 轻提示）—— 回归钉
# ====================================================================
class _RescanLoader:
    """记录 rescan / load_all 调用的 loader 替身（errors 可选）"""

    def __init__(self, plugins=(), errors=()):
        self._plugins = list(plugins)
        self._errors = list(errors)
        self.rescan_calls = 0
        self.load_all_calls = 0

    def rescan(self):
        self.rescan_calls += 1
        return list(self._plugins)

    def load_all(self):
        self.load_all_calls += 1
        return list(self._plugins)

    def loaded_plugins(self):
        return list(self._plugins)

    def load_errors(self):
        return list(self._errors)

    def scan_store(self):
        return []

    @property
    def plugins_dir(self):
        return os.path.join(os.path.sep, "nonexistent_plugins_dir")

    @property
    def store_dir(self):
        return os.path.join(os.path.sep, "nonexistent_store_dir")


class _ToastHost:
    """记录 show_toast 与菜单重建调用的 host 替身"""

    def __init__(self, loader):
        self._loader = loader
        self._config = {"plugins_enabled": True}
        self.toasts = []
        self.menu_rebuilds = 0

    @property
    def plugin_loader(self):
        return self._loader

    @property
    def config(self):
        return self._config

    def show_toast(self, text, ms=2800):
        self.toasts.append(text)

    def _rebuild_context_menu(self):
        self.menu_rebuilds += 1


class TestRescanToastAndDefer:
    def _make_panel(self, host):
        from src.plugins_panel import PluginsPanel
        return PluginsPanel(host)

    def test_click_defers_and_disables_button(self, qapp, tmp_path):
        """点击当下不开扫：按钮禁用 + 开始提示，重活等 deferred 槽"""
        loader = _RescanLoader([_make_loaded_plugin(tmp_path)])
        host = _ToastHost(loader)
        panel = self._make_panel(host)
        panel._on_rescan()
        assert not panel._rescan_btn.isEnabled()
        assert host.toasts == ["正在重新扫描插件…"]
        assert loader.rescan_calls == 0          # 同步路径必须没有扫
        assert panel._rescan_pending is True

    def test_deferred_slot_scans_and_toasts_result(self, qapp, tmp_path):
        lp = _make_loaded_plugin(tmp_path)
        host = _ToastHost(_RescanLoader([lp]))
        panel = self._make_panel(host)
        panel._on_rescan()
        panel._do_rescan()
        assert host._loader.rescan_calls == 1
        assert host.menu_rebuilds == 1
        assert len(_plugin_cards(panel)) == 1    # 整页已刷新
        assert host.toasts == ["正在重新扫描插件…",
                               "重新扫描完成：1 个插件可用"]
        assert panel._rescan_btn.isEnabled()     # 收尾恢复按钮
        assert panel._rescan_pending is False

    def test_deferred_toast_counts_errors(self, qapp, tmp_path):
        lp = _make_loaded_plugin(tmp_path)
        host = _ToastHost(_RescanLoader([lp], errors=["bad"]))
        panel = self._make_panel(host)
        panel._do_rescan()
        assert host.toasts[-1] == "重新扫描完成：1 个插件可用，1 个加载失败"

    def test_deferred_toast_when_nothing_found(self, qapp):
        host = _ToastHost(_RescanLoader())
        panel = self._make_panel(host)
        panel._do_rescan()
        assert host.toasts[-1] == "重新扫描完成：未发现插件"

    def test_scan_failure_toasts_error(self, qapp):
        class _BoomLoader(_RescanLoader):
            def rescan(self):
                raise RuntimeError("boom")

        host = _ToastHost(_BoomLoader())
        panel = self._make_panel(host)
        panel._do_rescan()
        assert host.toasts[-1] == "重新扫描失败：boom"
        assert panel._rescan_btn.isEnabled()     # 失败也要恢复按钮

    def test_old_loader_falls_back_to_load_all(self, qapp):
        class _OldLoader(_RescanLoader):
            rescan = None                        # 旧 loader 无 rescan 接口

        loader = _OldLoader()
        host = _ToastHost(loader)
        panel = self._make_panel(host)
        panel._do_rescan()
        assert loader.load_all_calls == 1
        assert host.toasts[-1] == "重新扫描完成：未发现插件"

    def test_reentry_guard(self, qapp):
        """扫描进行中再点无效：不重复弹开始提示、不叠加定时器"""
        host = _ToastHost(_RescanLoader())
        panel = self._make_panel(host)
        panel._on_rescan()
        panel._on_rescan()
        assert host.toasts == ["正在重新扫描插件…"]
        panel._do_rescan()
        assert host._loader.rescan_calls == 1
        assert host.toasts[-1] == "重新扫描完成：未发现插件"


# ====================================================================
# 启动小窗闪现回归护栏（2026-10-05）
# ====================================================================
class TestCardNeverTopLevel:
    """插件卡在构建全程不得以「顶层窗」身份 Show。

    根因（用户报「主窗口出现后上层连续闪现几个小窗口后消失」）：
    「先建后拆」先批量建卡再挂网格，旧码卡片用 ``QFrame()`` 无 parent
    构造——_apply_filter 对尚未挂入布局的卡 ``setVisible(True)`` 时，
    每张卡瞬时成为屏幕 (0,0) 处的真实顶层 OS 窗口，随 addWidget
    reparent 才消失。懒加载预热构建本页时逐张触发，即「连续闪现几个
    小窗口」。修复后卡片以面板为临时 parent，永远不是顶层窗。
    """

    _CARD_NAMES = {"pluginCard", "pluginStoreCard", "pluginErrorCard"}

    def test_no_top_level_show_during_build_and_filter(self, qapp, tmp_path):
        from PyQt6.QtCore import QEvent, QObject
        from src.plugins_panel import PluginsPanel

        shown = []

        class _TopLevelShowFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Show:
                    try:
                        if (obj.isWindow()
                                and obj.objectName() in self._CARD_NAMES):
                            shown.append(obj.objectName())
                    except RuntimeError:
                        pass  # C++ 侧已销毁的对象不参与记录
                return False

        f = _TopLevelShowFilter()
        f._CARD_NAMES = self._CARD_NAMES
        qapp.installEventFilter(f)
        try:
            lps = [_make_loaded_plugin(tmp_path, plugin_id=f"p{i}")
                   for i in range(3)]
            panel = PluginsPanel(_FakeHost(loader=_FakeLoader(lps)))
            # 复跑一次过滤（与启动预热/切页守卫同路径，验重入不闪）
            panel._apply_filter()
        finally:
            qapp.removeEventFilter(f)

        assert panel._cards_layout.count() == 3
        assert shown == [], f"插件卡以顶层窗身份闪现：{shown}"
