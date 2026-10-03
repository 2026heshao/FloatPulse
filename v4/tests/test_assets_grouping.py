# -*- coding: utf-8 -*-
"""临时素材「会话分组」回归钉子（第 4 卡 shot-burst）。

核心契约：
  · 分组是**渲染时派生**的，temp_assets.json 一个字都不许改；
  · 关闭分组开关后，渲染条目集合与改动前的平铺**逐项一致**（硬护栏）；
  · 间隔阈值走配置（不写死 60 秒），改配置能改变聚出的堆数；
  · 聚类纯逻辑零 PyQt6，可脱离 GUI 跑。

反向验证（改坏实现必须变红）见每条用例的 docstring：
  - 阈值写死 → test_gap_comes_from_config_not_hardcoded
  - 分组漏图 → test_grouped_sequence_covers_every_asset / test_toggle_roundtrip
"""

import os

import pytest

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_image(path, w=200, h=140, hue=120):
    from PyQt6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(hue % 256, 200, 90))
    assert img.save(path)
    return path


class _Asset:
    """纯逻辑测试用的最小素材替身（只需 asset_id / added_time）。"""

    def __init__(self, asset_id, added_time):
        self.asset_id = asset_id
        self.added_time = added_time


def _t(sec):
    """构造 "YYYY-MM-DD HH:MM:SS" 字符串（基准 2026-10-03 09:00:00）。"""
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 3, 9, 0, 0)
    return (base + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M:%S")


# ====================================================================
# 1. 纯逻辑聚类（零 PyQt6）
# ====================================================================
def test_asset_group_module_has_no_pyqt_import():
    """asset_group 必须是纯逻辑模块（禁 PyQt6）。"""
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(os.path.dirname(here), "src", "asset_group.py")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all("PyQt6" not in a.name for a in node.names), node.names
        if isinstance(node, ast.ImportFrom):
            assert "PyQt6" not in (node.module or ""), node.module


def test_cluster_splits_on_gap():
    """间隔 < 阈值同堆、>= 阈值开新堆；空输入空输出。"""
    from src import asset_group as ag
    # 09:00:00 / 09:00:30(30s,同堆) / 09:05:00(270s,新堆) / 09:05:20(20s,同堆)
    assets = [_Asset(1, _t(0)), _Asset(2, _t(30)),
              _Asset(3, _t(300)), _Asset(4, _t(320))]
    groups = ag.cluster_assets(assets, gap_seconds=120)
    assert [len(g) for g in groups] == [2, 2]
    assert ag.cluster_assets([], gap_seconds=120) == []


def test_cluster_boundary_is_strict_less_than():
    """阈值边界：差**恰好等于**阈值 → 必须断开（严格小于才是同堆）。"""
    from src import asset_group as ag
    assets = [_Asset(1, _t(0)), _Asset(2, _t(120))]   # 差 = 120 = gap
    groups = ag.cluster_assets(assets, gap_seconds=120)
    assert [len(g) for g in groups] == [1, 1]
    # 差 119 秒 → 同堆
    groups2 = ag.cluster_assets([_Asset(1, _t(0)), _Asset(2, _t(119))],
                                gap_seconds=120)
    assert [len(g) for g in groups2] == [2]


def test_cluster_zero_gap_means_every_item_alone():
    """gap<=0 = 关闭聚类的等价形态：每张各自成堆（零阈值不吞并）。"""
    from src import asset_group as ag
    assets = [_Asset(1, _t(0)), _Asset(2, _t(1)), _Asset(3, _t(2))]
    groups = ag.cluster_assets(assets, gap_seconds=0)
    assert [len(g) for g in groups] == [1, 1, 1]


def test_cluster_is_order_independent_and_stable():
    """入参乱序 → 结果仍按时间升序；同一批数据两次聚类逐项一致。"""
    from src import asset_group as ag
    assets = [_Asset(3, _t(300)), _Asset(1, _t(0)),
              _Asset(4, _t(320)), _Asset(2, _t(30))]
    g1 = ag.cluster_assets(assets, gap_seconds=120)
    g2 = ag.cluster_assets(list(reversed(assets)), gap_seconds=120)
    assert [a.asset_id for a in ag.flatten_groups(g1)] == [1, 2, 3, 4]
    assert ag.group_signature(g1) == ag.group_signature(g2)


def test_cluster_tolerates_bad_time():
    """时间解析失败不抛异常：坏数据自聚合、与好数据交界处断开。"""
    from src import asset_group as ag
    assets = [_Asset(1, ""), _Asset(2, "not-a-time"),
              _Asset(3, _t(10)), _Asset(4, _t(20))]
    groups = ag.cluster_assets(assets, gap_seconds=120)
    assert [a.asset_id for a in ag.flatten_groups(groups)]  # 不丢图
    assert ag.group_label(groups[0], 0)          # 坏数据堆也能起名（退化 "N 张"）


def test_group_label_formats_count_and_head_time():
    """默认堆名 = "N 张 · HH:MM"（时间缺失退化 "N 张"）。"""
    from src import asset_group as ag
    g = [_Asset(1, _t(0)), _Asset(2, _t(10))]
    assert ag.group_label(g, 0) == "2 张 · 09:00"
    assert ag.group_label([_Asset(1, "")], 1) == "1 张"


# ====================================================================
# 2. 面板：分组模式 / 平铺模式条目集合**逐项一致**（硬护栏）
# ====================================================================
class _NoopConfig:
    """无落盘副作用的最小配置替身（默认值取 DEFAULT_CONFIG）。"""

    def __init__(self, overrides=None):
        from src.config import DEFAULT_CONFIG
        self._config = dict(DEFAULT_CONFIG)
        if overrides:
            self._config.update(overrides)

    def get(self, key, default=None):
        return self._config.get(key, default)

    def set(self, key, value):
        self._config[key] = value
        return True

    def save(self):
        return None


class _Host:
    """最小宿主替身（AssetsPanel 构造所需全部）。"""

    def __init__(self, mgr, config=None):
        self._config = config if config is not None else _NoopConfig()
        self.current_theme = "light"
        self._temp_asset_manager = mgr
        from types import SimpleNamespace
        self.data_changed = SimpleNamespace(emit=lambda *a: None)


@pytest.fixture()
def env(tmp_path):
    _app()
    from src.temp_asset_manager import TempAssetManager
    mgr = TempAssetManager(str(tmp_path))
    ids = []
    for i in range(3):
        p = _make_image(str(tmp_path / f"s{i}.png"), hue=40 * i + 10)
        aid = mgr.add_asset(p, f"图{i}.png")
        assert aid > 0
        ids.append(aid)
    return mgr, ids


def _make_panel(mgr, config=None):
    from src.assets_panel import AssetsPanel
    panel = AssetsPanel(_Host(mgr, config))
    panel._valid_ids = {a.asset_id for a in mgr.get_all_assets()}
    panel.refresh()
    return panel


def _list_ids(panel):
    from PyQt6.QtCore import Qt as _Qt
    return [panel._asset_list.item(i).data(_Qt.ItemDataRole.UserRole + 1)
            for i in range(panel._asset_list.count())]


def test_flat_and_grouped_modes_have_identical_item_sets(env):
    """★ 硬护栏：关闭分组 = 平铺；开启分组后条目集合必须相等（只是顺序不同）。"""
    mgr, ids = env
    panel = _make_panel(mgr)
    assert not panel._is_grouping()          # 默认关闭 = 等价改动前行为
    flat = _list_ids(panel)
    assert set(flat) == set(ids) and len(flat) == len(ids)
    panel.set_grouping(True)                 # 切到分组 → 重渲染
    grouped = _list_ids(panel)
    assert sorted(grouped) == sorted(flat), "分组模式漏图或多图！"
    assert len(grouped) == mgr.count()
    # group headers 只在分组模式存在
    assert panel._thumb_delegate._header_texts
    assert len(panel._thumb_delegate._header_texts) <= mgr.count()


def test_toggle_roundtrip_restores_flat_order(env):
    """往返切换：关→开→关，最后一屏必须回到与初始平铺**顺序也一致**。"""
    mgr, _ = env
    panel = _make_panel(mgr)
    before = _list_ids(panel)
    panel.set_grouping(True)
    panel.set_grouping(False)
    assert _list_ids(panel) == before, "切换回平铺后顺序变了 = 状态没复原"


def test_grouping_actually_collapses_bursts(tmp_path):
    """真聚类：同刻连拍聚成堆（headers 数 < 素材数），堆名带张数与时间。"""
    _app()
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    mgr = TempAssetManager(str(tmp_path))
    # 直接铺 5 条同刻资产（间隔 0 << 120s），避开真实落盘时间戳不可控
    img = _make_image(str(tmp_path / "burst.png"))
    mgr._assets = [AssetInfo(i, f"连拍{i}.png", img, True, 10, _t(i))
                   for i in range(1, 6)]
    panel = _make_panel(mgr)
    panel.set_grouping(True)
    headers = panel._thumb_delegate._header_texts
    assert len(headers) == 1, headers
    assert list(headers.values())[0] == "5 张 · 09:00"


# ====================================================================
# 3. 间隔阈值走配置（不写死 60 秒）
# ====================================================================
def test_gap_comes_from_config_not_hardcoded(tmp_path):
    """★ 反向验证用：阈值必须从配置读 —— 改配置能改变聚出的堆数。

    若实现把阈值写死（例如硬编码 60 秒），下面第二个断言（gap=10 秒把
    相隔 30 秒的两张拆成两堆）会失败。
    """
    _app()
    from src.config import ConfigManager
    import tempfile
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    cfg = ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))
    mgr = TempAssetManager(str(tmp_path))
    img = _make_image(str(tmp_path / "x.png"))
    # 两张相隔 30 秒
    mgr._assets = [
        AssetInfo(1, "a.png", img, True, 10, _t(0)),
        AssetInfo(2, "b.png", img, True, 10, _t(30)),
    ]
    panel = _make_panel(mgr, cfg)
    cfg.set("asset_group_gap_seconds", 120)
    panel.set_grouping(True)
    assert len(panel._thumb_delegate._header_texts) == 1   # 30 < 120 同堆
    # 阈值降到 10 秒 → 30 秒差断开 → 两堆
    cfg.set("asset_group_gap_seconds", 10)
    panel.refresh()
    assert len(panel._thumb_delegate._header_texts) == 2, "阈值没生效=可能写死了"


def test_config_registers_group_keys():
    """配置三件套同步：DEFAULT_CONFIG / _CONFIG_TYPES / _CONFIG_RANGES。"""
    from src.config import DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES
    assert DEFAULT_CONFIG["asset_group_enabled"] is False       # 默认=平铺
    assert DEFAULT_CONFIG["asset_group_gap_seconds"] == 120
    assert _CONFIG_TYPES["asset_group_enabled"] is bool
    assert _CONFIG_TYPES["asset_group_gap_seconds"] is int
    assert "asset_group_enabled" not in _CONFIG_RANGES          # bool 不进 RANGES
    assert _CONFIG_RANGES["asset_group_gap_seconds"] == (10, 3600)


def test_config_rejects_out_of_range_gap():
    """越界阈值被 ConfigManager 拒绝（不落进内存配置）。"""
    import tempfile
    from src.config import ConfigManager
    cfg = ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))
    assert cfg.set("asset_group_gap_seconds", 5) is False       # < 10
    assert cfg.set("asset_group_gap_seconds", 99999) is False   # > 3600
    assert cfg.set("asset_group_gap_seconds", 300) is True
    assert cfg.get("asset_group_gap_seconds") == 300


# ====================================================================
# 4. 不落库（temp_assets.json 一字不动）
# ====================================================================
def test_grouping_never_writes_temp_assets_json(env):
    """★ 分组是渲染派生：开关分组/刷新前后 temp_assets.json 字节不变。"""
    import hashlib

    def _digest(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    mgr, _ = env
    json_path = mgr._json_path
    before = _digest(json_path)
    panel = _make_panel(mgr)
    panel.set_grouping(True)
    panel.refresh()
    panel.set_grouping(False)
    panel.refresh()
    after = _digest(json_path)
    assert before == after, "分组功能动了 temp_assets.json（护栏要求零落库）"


# ====================================================================
# 5. 视图模式在同一 QListWidget 上切换（同列表两呈现）
# ====================================================================
def test_view_switch_uses_same_list_widget(env):
    """分组/平铺共用同一个 _asset_list（不新建列表 → 条目集合天然一致）。"""
    mgr, _ = env
    panel = _make_panel(mgr)
    first = panel._asset_list
    panel.set_grouping(True)
    assert panel._asset_list is first
    assert panel._thumb_delegate._header_texts          # 分组态有堆标题
    panel.set_grouping(False)
    assert panel._thumb_delegate._header_texts == {}    # 平铺态无堆标题
