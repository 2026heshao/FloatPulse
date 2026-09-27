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
                        readme=False, requires=("PyQt6",)):
    """构造 LoadedPlugin 形状的替身（不 import 真插件模块）"""
    import types as _types
    # 动作替身（鸭子类型：面板只读 title/hotkey/menu 属性）
    action = _types.SimpleNamespace(
        id="demo.act", title="做件事", hotkey="Ctrl+Alt+D", menu=True)

    plugin = _types.SimpleNamespace()
    if doc is not None:
        # SimpleNamespace 是 immutable type 不能设 __doc__，用一次性子类
        plugin = type("_FakePlugin", (), {"__doc__": doc})()

    pdir = tmp_path / "demo"
    pdir.mkdir(exist_ok=True)
    if readme:
        (pdir / "README.md").write_text("# demo", encoding="utf-8")
    manifest = {
        "id": "demo", "name": "演示插件", "version": "1.2.3",
        "entry": "main.py", "requires": list(requires),
        "actions": [{"id": "demo.act", "title": "做件事",
                     "hotkey": "Ctrl+Alt+D", "menu": True}],
        "description": description,
    }
    return _types.SimpleNamespace(
        plugin_id="demo", name="演示插件", version="1.2.3",
        path=str(pdir), plugin=plugin, manifest=manifest,
        actions_raw=[action])


class _FakeLoader:
    def __init__(self, plugins):
        self._plugins = plugins

    def loaded_plugins(self):
        return list(self._plugins)

    def plugins_dir(self):
        return os.path.join(os.path.sep, "nonexistent_plugins_dir")


class _FakeHost:
    """最小 host 替身：_config + plugin_loader 属性"""

    def __init__(self, loader=None, plugins_enabled=True):
        self._config = {"plugins_enabled": plugins_enabled}
        self._loader = loader

    @property
    def plugin_loader(self):
        return self._loader


class TestPluginsPanel:
    def test_empty_state(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        panel = PluginsPanel(_FakeHost(loader=None))
        assert panel._empty_label.isVisibleTo(panel)
        assert "共 0 个插件" in panel._count_label.text()
        # 卡片区除 stretch 外无卡片
        assert panel._cards_layout.count() == 1

    def test_card_rendered_with_actions(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path, description="整理碎片用")
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        assert not panel._empty_label.isVisibleTo(panel)
        assert "共 1 个插件" in panel._count_label.text()
        assert panel._cards_layout.count() == 2   # 1 卡片 + stretch
        card = panel._cards_layout.itemAt(0).widget()
        assert card is not None
        joined = " ".join(l.text() for l in card.findChildren(QLabel))
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
        card = panel._cards_layout.itemAt(0).widget()
        texts = " ".join(l.text() for l in card.findChildren(QLabel))
        assert "周报草稿生成器" in texts

    def test_gate_off_shows_hint(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path)
        panel = PluginsPanel(
            _FakeHost(loader=_FakeLoader([lp]), plugins_enabled=False))
        assert panel._gate_label.isVisibleTo(panel)
        # 总闸关闭 → 即使 loader 有插件也不渲染卡片
        assert panel._cards_layout.count() == 1

    def test_refresh_rebuilds(self, qapp, tmp_path):
        from src.plugins_panel import PluginsPanel
        host = _FakeHost(loader=None)
        panel = PluginsPanel(host)
        assert panel._cards_layout.count() == 1
        host._loader = _FakeLoader([_make_loaded_plugin(tmp_path)])
        panel.refresh()
        assert panel._cards_layout.count() == 2


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

        act = _FakeAction()
        panel = PluginsPanel(_NoCfgHost())
        panel._persist_disabled("demo", False)     # 不应抛异常


# ====================================================================
# 使用说明 md：探测 / 摘要提取 / 卡片展示
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

    def test_summary_skips_headings_and_tables(self, tmp_path):
        from src.plugins_panel import usage_summary
        p = tmp_path / "使用说明.md"
        p.write_text(
            "# 标题\n\n## 怎么用\n"
            "| a | b |\n|---|---|\n"
            "- **一键汇总**任务与碎片，生成 `Markdown` 草稿。\n"
            "\n后续段落",
            encoding="utf-8")
        s = usage_summary(str(p))
        assert s.startswith("一键汇总")
        assert "**" not in s and "`" not in s and "|" not in s
        assert "后续段落" not in s                      # 只取第一段

    def test_summary_truncates_long_lines(self, tmp_path):
        from src.plugins_panel import usage_summary, USAGE_SUMMARY_MAX
        p = tmp_path / "使用说明.md"
        p.write_text("很" * 300, encoding="utf-8")
        s = usage_summary(str(p))
        assert len(s) == USAGE_SUMMARY_MAX + 1          # 截断 + 省略号
        assert s.endswith("…")

    def test_summary_missing_file_returns_empty(self, tmp_path):
        from src.plugins_panel import usage_summary
        assert usage_summary(str(tmp_path / "不存在.md")) == ""

    def test_card_shows_usage_summary(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        d = tmp_path / "demo"
        d.mkdir()
        (d / "使用说明.md").write_text(
            "# 使用说明\n\n选定范围后一键汇总任务与碎片生成周报草稿。\n",
            encoding="utf-8")
        lp = _make_loaded_plugin(tmp_path)
        lp.path = str(d)
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        card = panel._cards_layout.itemAt(0).widget()
        texts = " ".join(l.text() for l in card.findChildren(QLabel))
        assert "选定范围后一键汇总" in texts
        # 按钮文案更新为「查看使用说明」
        btn_texts = " ".join(b.text() for b in card.findChildren(
            __import__("PyQt6.QtWidgets", fromlist=["QPushButton"]).QPushButton))
        assert "查看使用说明" in btn_texts

    def test_card_without_usage_doc_has_no_summary(self, qapp, tmp_path):
        from PyQt6.QtWidgets import QLabel
        from src.plugins_panel import PluginsPanel
        lp = _make_loaded_plugin(tmp_path)              # 无任何 md
        panel = PluginsPanel(_FakeHost(loader=_FakeLoader([lp])))
        card = panel._cards_layout.itemAt(0).widget()
        texts = " ".join(l.text() for l in card.findChildren(QLabel))
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

        monkeypatch.setattr(pp, "QMessageBox", _FakeMB)

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
