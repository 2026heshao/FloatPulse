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
  - 单张堆被画标题 → test_single_asset_groups_have_no_header（2026-10-03 观感返工）
  - 标题条挤占正文 → test_group_mode_reserves_header_height_without_breaking_uniform_cells
"""

import os
import sys

# ★路径自举：本文件必须可**单独**跑（pytest v4/tests/test_assets_grouping.py），
# 不能吸血其它测试模块先插好的 sys.path——此前无自举，单跑必挂
# ModuleNotFoundError: No module named 'src'（2026-10-04 终检发现）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    数据：t=0 / t=10（第一对）、t=300 / t=310（第二对）。
      gap=600 → 4 张全并 1 堆 → 1 个标题
      gap=120 → 两对各 1 堆     → 2 个标题
      gap=10  → 4 张各自成堆     → 0 个标题（<2 张的堆不画标题，见下方
                                      test_single_asset_groups_have_no_header）
    若实现把阈值写死（例如硬编码 60 秒），三次读数不会出现 1/2/0 的阶梯。
    """
    _app()
    from src.config import ConfigManager
    import tempfile
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    cfg = ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))
    mgr = TempAssetManager(str(tmp_path))
    img = _make_image(str(tmp_path / "x.png"))
    mgr._assets = [
        AssetInfo(1, "a.png", img, True, 10, _t(0)),
        AssetInfo(2, "b.png", img, True, 10, _t(10)),
        AssetInfo(3, "c.png", img, True, 10, _t(300)),
        AssetInfo(4, "d.png", img, True, 10, _t(310)),
    ]
    panel = _make_panel(mgr, cfg)
    panel.set_grouping(True)

    def _headers():
        return len(panel._thumb_delegate._header_texts)

    cfg.set("asset_group_gap_seconds", 600)
    panel.refresh()
    assert _headers() == 1, "gap=600 应把两对并成 1 堆"
    cfg.set("asset_group_gap_seconds", 120)
    panel.refresh()
    assert _headers() == 2, "阈值没生效=可能写死了"
    cfg.set("asset_group_gap_seconds", 10)
    panel.refresh()
    assert _headers() == 0, "gap=10 全拆散 → 全是单张堆 → 无标题"


def test_single_asset_groups_have_no_header(tmp_path):
    """★ 观感返工护栏：单张素材不构成「会话」，**不许**给它画标题条。

    2026-10-03 用户目检：真实数据 17 张聚出 13 堆，其中 11 堆是单张，
    每张都顶着一条 "1 张 · HH:MM" 标题 → 读起来比平铺更乱。反向验证：
    把 ``if len(group) >= 2`` 改回 ``if group`` 本用例立刻变红。
    """
    _app()
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    mgr = TempAssetManager(str(tmp_path))
    img = _make_image(str(tmp_path / "x.png"))
    # 三张彼此相隔 1 小时 → gap=120 下各自成堆（全单张）
    mgr._assets = [AssetInfo(i, f"s{i}.png", img, True, 10, _t(i * 3600))
                   for i in range(1, 4)]
    cfg_over = {"asset_group_gap_seconds": 120, "asset_group_enabled": True}
    panel = _make_panel(mgr, _NoopConfig(cfg_over))
    assert panel._is_grouping()
    d = panel._thumb_delegate
    assert d._header_texts == {}, "单张堆不许画标题条"
    assert d._member_ordinals == {}, "单张堆没有堆内序号"
    assert panel._asset_list.count() == 3, "单张堆仍须逐张铺出来（不丢图）"
    # 但分组态下单元仍要垫高（否则同排带标题的格子会与单张格错位）
    assert d._group_mode is True
    assert d.CELL_H == d.THUMB_H + 64 + d.HEADER_H


def test_group_members_render_ordinal_instead_of_repeated_filename(tmp_path):
    """★ 观感返工护栏：堆内**非首格**改显 "#2" 序号，不再重复雷同文件名。

    反向验证：把 ``if j:`` 去掉（每格都挂序号）→ 首格断言变红；
    把 ``ordinals`` 整表清空 → 非首格断言变红。
    """
    _app()
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    mgr = TempAssetManager(str(tmp_path))
    img = _make_image(str(tmp_path / "x.png"))
    mgr._assets = [AssetInfo(i, "剪贴板图片_%d.png" % i, img, True, 10, _t(i))
                   for i in range(1, 4)]
    panel = _make_panel(mgr, _NoopConfig({"asset_group_enabled": True}))
    d = panel._thumb_delegate
    assert len(d._header_texts) == 1, "三张同刻应聚成 1 堆"
    assert d._member_ordinals == {2: "#2", 3: "#3"}, d._member_ordinals
    assert 1 not in d._member_ordinals, "堆首格保留真实文件名，不挂序号"
    # 平铺态两张表都清空（不能把分组态残留带回去）
    panel.set_grouping(False)
    assert d._member_ordinals == {} and d._header_texts == {}


def test_group_mode_reserves_header_height_without_breaking_uniform_cells(env):
    """★ 观感返工护栏：标题条走**专属**高度，不挤正文；平铺态尺寸一字不变。

    反向验证：把 CELL_H 改回 ``THUMB_H + 64``（不垫高）→ 本用例变红。
    """
    mgr, _ = env
    panel = _make_panel(mgr)
    d = panel._thumb_delegate
    flat_h = d.CELL_H
    assert flat_h == d.THUMB_H + 64, "平铺态单元高不许变（改动前行为）"
    panel.set_grouping(True)
    assert d.CELL_H == flat_h + d.HEADER_H, "分组态须为标题条垫高专属高度"
    # 缩略图尺寸不受分组影响（只有单元变高）
    assert d.THUMB_H == flat_h - 64
    # 分组态每一格 sizeHint 相同 → setUniformItemSizes(True) 依然成立
    hints = set()
    for i in range(panel._asset_list.count()):
        sz = panel._asset_list.sizeHintForIndex(
            panel._asset_list.model().index(i, 0))
        hints.add((sz.width(), sz.height()))
    assert hints == {(d.CELL_W, d.CELL_H)}, hints
    panel.set_grouping(False)
    assert d.CELL_H == flat_h, "切回平铺必须恢复原单元高"


def test_config_registers_group_keys():
    """配置三件套同步：DEFAULT_CONFIG / _CONFIG_TYPES / _CONFIG_RANGES。"""
    from src.config import DEFAULT_CONFIG, _CONFIG_TYPES, _CONFIG_RANGES
    from src import asset_group as ag
    assert DEFAULT_CONFIG["asset_group_enabled"] is False       # 默认=平铺
    assert DEFAULT_CONFIG["asset_group_gap_seconds"] == 900     # 2026-10-03 由 120 上调
    assert ag.DEFAULT_GAP_SECONDS == DEFAULT_CONFIG["asset_group_gap_seconds"]
    assert _CONFIG_TYPES["asset_group_enabled"] is bool
    assert _CONFIG_TYPES["asset_group_gap_seconds"] is int
    assert "asset_group_enabled" not in _CONFIG_RANGES          # bool 不进 RANGES
    assert _CONFIG_RANGES["asset_group_gap_seconds"] == (10, 3600)


# ====================================================================
# 3b. config v1 → v2 迁移（阈值旧默认 120 → 新默认 900）
# ====================================================================
def test_migration_constants_pin_new_default():
    """★ 护栏必须能红灯：迁移的目标值必须与 DEFAULT_CONFIG 同步。

    改默认却忘了改迁移 → 老用户永远停在旧值，本用例立刻红。
    """
    from src.config import DEFAULT_CONFIG
    from src import json_store
    assert json_store.STORE_VERSIONS["config"] == 2
    assert json_store.MIGRATIONS["config"][1] is \
        json_store._migrate_config_1_to_2
    assert json_store._CONFIG_V1_NEW_GAP_SECONDS == \
        DEFAULT_CONFIG["asset_group_gap_seconds"]
    # ★ 三处版本号必须同步：DEFAULT_CONFIG 落后 → 文件每次加载都重跑迁移，
    #   会把用户显式设回的 120 再改一次（静默覆盖用户意图）。
    assert DEFAULT_CONFIG["schema_version"] == json_store.STORE_VERSIONS["config"]


def test_migration_v1_rewrites_only_the_old_default():
    """迁移只改写「恰好等于旧默认 120」的值，用户手改过的值一律不动。"""
    from src import json_store as js
    assert js._CONFIG_V1_OLD_GAP_SECONDS == 120
    # 旧默认 → 改写
    assert js._migrate_config_1_to_2({"asset_group_gap_seconds": 120}) == \
        {"asset_group_gap_seconds": 900}
    # 用户手改过 → 原样
    assert js._migrate_config_1_to_2({"asset_group_gap_seconds": 300}) == \
        {"asset_group_gap_seconds": 300}
    # 缺键 → 不凭空造键（新建配置走 DEFAULT_CONFIG 自然生效）
    assert js._migrate_config_1_to_2({}) == {}
    # 幂等：连跑两次结果一致
    once = js._migrate_config_1_to_2({"asset_group_gap_seconds": 120})
    assert js._migrate_config_1_to_2(dict(once)) == once


def test_config_manager_migrates_old_config_file(tmp_path):
    """端到端：schema_version=1 的旧 config.json 打开后取到 900（且不手改用户文件）。"""
    import json
    from src.config import ConfigManager
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"schema_version": 1,
                             "asset_group_gap_seconds": 120}),
                 encoding="utf-8")
    cfg = ConfigManager(str(p))
    assert cfg.get("asset_group_gap_seconds") == 900
    # 迁移只发生在内存；原文件要等下次 save() 才更新（加载期零副作用）
    assert json.loads(p.read_text(encoding="utf-8"))["asset_group_gap_seconds"] == 120
    # 已是 v2 的文件不再被改写（否则用户后来的显式选择会被反复覆盖）
    p2 = tmp_path / "config2.json"
    p2.write_text(json.dumps({"schema_version": 2,
                              "asset_group_gap_seconds": 120}),
                  encoding="utf-8")
    assert ConfigManager(str(p2)).get("asset_group_gap_seconds") == 120


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


# ====================================================================
# 6. 会话堆旁路标注（asset_groups.json：命名 / 移出；2026-10-04 P0-2）
# ====================================================================
def test_groups_store_module_has_no_pyqt_import():
    """标注存储必须纯逻辑（禁 PyQt6），与 asset_group 同款约束。"""
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(os.path.dirname(here), "src", "asset_groups_store.py")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all("PyQt6" not in a.name for a in node.names), node.names
        if isinstance(node, ast.ImportFrom):
            assert "PyQt6" not in (node.module or ""), node.module


class TestGroupsStore:
    def test_missing_file_returns_empty(self, tmp_path):
        from src import asset_groups_store as gs
        st = gs.load(str(tmp_path / "nope.json"))
        assert st == {gs.NAMES_KEY: {}, gs.DETACHED_KEY: []}

    def test_save_roundtrip_and_version_stamp(self, tmp_path):
        import json
        from src import asset_groups_store as gs
        p = str(tmp_path / gs.FILE_NAME)
        st = {gs.NAMES_KEY: {"3": "排障现场"}, gs.DETACHED_KEY: [7]}
        assert gs.save(p, st) is True
        data = json.loads(open(p, encoding="utf-8").read())
        assert data["data_version"] == 1                 # save_records 补版本
        assert data[gs.STORE_KEY] == st
        assert gs.load(p) == st

    def test_load_corrupt_file_backed_up(self, tmp_path):
        from src import asset_groups_store as gs
        p = tmp_path / gs.FILE_NAME
        p.write_text("{ broken", encoding="utf-8")
        assert gs.load(str(p)) == gs.empty_store()
        # 损坏文件先备份再清空（与全部数据文件同一纪律）
        assert (tmp_path / (gs.FILE_NAME + ".corrupt.bak")).exists()

    def test_sanitize_garbage(self):
        from src import asset_groups_store as gs
        out = gs.sanitize({
            gs.NAMES_KEY: {"3": "  名字  ", "x": "非数字键", "4": 123,
                           "5": "   "},
            gs.DETACHED_KEY: ["7", 8, -1, 0, "abc", 7, None],
            "junk": 1,
        })
        assert out == {gs.NAMES_KEY: {"3": "名字"},
                       gs.DETACHED_KEY: [7, 8]}
        assert gs.sanitize(None) == gs.empty_store()
        assert gs.sanitize(42) == gs.empty_store()

    def test_sanitize_truncates_long_name(self):
        from src import asset_groups_store as gs
        out = gs.sanitize({gs.NAMES_KEY: {"1": "长" * 100}})
        assert len(out[gs.NAMES_KEY]["1"]) == gs.NAME_MAX

    def test_prune_drops_orphans(self):
        from src import asset_groups_store as gs
        st = {gs.NAMES_KEY: {"1": "活着", "9": "已删"},
              gs.DETACHED_KEY: [1, 9, 10]}
        out = gs.prune(st, valid_ids=[1, 2])
        assert out == {gs.NAMES_KEY: {"1": "活着"}, gs.DETACHED_KEY: [1]}


class TestApplyOverrides:
    def _assets(self):
        return [_Asset(1, _t(0)), _Asset(2, _t(10)),      # 同堆
                _Asset(3, _t(300)), _Asset(4, _t(310))]   # 同堆

    def test_detach_splits_and_keeps_coverage(self):
        from src import asset_group as ag
        groups = ag.cluster_assets(self._assets(), gap_seconds=120)
        out = ag.apply_group_overrides(groups, detached=[2])
        assert [a.asset_id for a in ag.flatten_groups(out)] == [1, 2, 3, 4]
        # 2 被拆出成独立单元：按时间插在 1 与 3 之间
        assert [[a.asset_id for a in g] for g in out] == [[1], [2], [3, 4]]

    def test_detach_everything_degenerates_to_singletons(self):
        from src import asset_group as ag
        groups = ag.cluster_assets(self._assets(), gap_seconds=120)
        out = ag.apply_group_overrides(groups, detached=[1, 2, 3, 4])
        assert [len(g) for g in out] == [1, 1, 1, 1]

    def test_unknown_and_dirty_ids_ignored(self):
        from src import asset_group as ag
        groups = ag.cluster_assets(self._assets(), gap_seconds=120)
        out = ag.apply_group_overrides(groups, detached=["99", None, "x"])
        assert [len(g) for g in out] == [2, 2]

    def test_custom_label_beats_default(self):
        from src import asset_group as ag
        g = [_Asset(1, _t(0)), _Asset(2, _t(10))]
        assert ag.group_label(g, 0, custom_name="排障现场") == "排障现场"
        assert ag.group_label(g, 0, custom_name="   ") == "2 张 · 09:00"
        assert ag.group_label(g, 0) == "2 张 · 09:00"      # 缺省参数兼容


# ====================================================================
# 7. 面板集成：命名 / 移出改变分组渲染，temp_assets.json 依旧零接触
# ====================================================================
def _burst_env(tmp_path, n=4):
    """n 张同刻连拍 + 分组开启的现成面板（不走真实落盘时间戳）。

    直铺后立即 _save()：temp_assets.json 落到磁盘，供「零接触」护栏
    做字节摘要比对。
    """
    _app()
    from src.temp_asset_manager import TempAssetManager, AssetInfo
    mgr = TempAssetManager(str(tmp_path))
    img = _make_image(str(tmp_path / "burst.png"))
    mgr._assets = [AssetInfo(i + 1, "剪贴板图片_%d.png" % i, img, True, 10,
                             _t(i)) for i in range(n)]
    mgr._next_id = n + 1
    mgr._save()
    panel = _make_panel(mgr, _NoopConfig({"asset_group_enabled": True}))
    return mgr, panel


def test_rename_pile_updates_header_and_sidecar_file(tmp_path):
    """命名 → 堆标题条换自定义名，标注落 asset_groups.json（旁路文件）。"""
    import json as _json
    mgr, panel = _burst_env(tmp_path)
    head_id = panel._pile_heads[0]
    groups_path = panel._groups_path
    assert groups_path and groups_path.endswith("asset_groups.json")

    panel._apply_pile_rename(head_id, " 登录页排障现场 ")
    header = panel._thumb_delegate._header_texts[head_id]
    assert header == "登录页排障现场", header
    data = _json.loads(open(groups_path, encoding="utf-8").read())
    assert data["asset_groups"]["names"] == {str(head_id): "登录页排障现场"}

    # 清空 = 恢复默认名（删键，不留空串）
    panel._apply_pile_rename(head_id, "")
    assert panel._thumb_delegate._header_texts[head_id] == \
        "%d 张 · 09:00" % 4
    data = _json.loads(open(groups_path, encoding="utf-8").read())
    assert data["asset_groups"]["names"] == {}


def test_rename_and_detach_never_touch_temp_assets_json(tmp_path):
    """★ 红线：命名 / 移出全程 temp_assets.json 字节不变（标注在旁路文件）。"""
    import hashlib
    mgr, panel = _burst_env(tmp_path)

    def _digest():
        with open(mgr._json_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    before = _digest()
    panel._apply_pile_rename(panel._pile_heads[0], "现场")
    panel._detach_asset(mgr._assets[1].asset_id, detach=True)   # 非堆首
    panel._detach_asset(mgr._assets[1].asset_id, detach=False)
    panel.set_grouping(False)
    panel.refresh()
    assert _digest() == before, "旁路标注动了 temp_assets.json（红线）"


def test_detach_member_renders_standalone_and_restores(tmp_path):
    """移出 → 该素材脱离堆、单独渲染无标题；取消移出 → 回到原堆。"""
    mgr, panel = _burst_env(tmp_path, n=4)
    aid = mgr._assets[2].asset_id                    # 堆中第 3 张
    panel._detach_asset(aid, detach=True)
    d = panel._thumb_delegate
    assert aid not in panel._pile_of, "移出后仍算堆成员"
    assert aid not in d._member_ordinals, "独立素材不挂堆内序号"
    assert len(d._header_texts) == 1                 # 剩 3 张仍是 1 堆
    assert d._header_texts[panel._pile_heads[0]] == "3 张 · 09:00"
    # 渲染条目依旧齐全（只拆堆，不丢图）
    assert panel._asset_list.count() == 4
    # 取消移出 → 回到原堆
    panel._detach_asset(aid, detach=False)
    assert panel._pile_of.get(aid) == panel._pile_heads[0]
    assert d._member_ordinals.get(aid) in ("#3", "#4")


def test_detached_state_survives_refresh_and_flat_mode(tmp_path):
    """标注是持久态：refresh 后仍生效；平铺态两张表照旧为空（不受标注影响）。

    另钉住堆身份语义：堆名挂在**堆首**上——移出非首成员，堆名不动。
    """
    mgr, panel = _burst_env(tmp_path, n=4)
    aid = mgr._assets[1].asset_id                    # 非堆首成员
    panel._apply_pile_rename(panel._pile_heads[0], "现场")
    panel._detach_asset(aid, detach=True)
    panel.refresh()
    assert aid not in panel._pile_of
    assert panel._thumb_delegate._header_texts[
        panel._pile_heads[0]] == "现场"              # 堆首未动，堆名保留
    panel.set_grouping(False)
    assert panel._thumb_delegate._header_texts == {}
    assert panel._asset_list.count() == 4
    panel.set_grouping(True)
    assert aid not in panel._pile_of                 # 重新进入分组仍生效


def test_detaching_the_head_hands_the_name_to_the_new_head(tmp_path):
    """堆身份 = 堆首：堆首被移出 → 剩余成员在新堆首下聚成堆，旧堆名
    成为孤儿标注（不应用到新堆，等素材删除时被 prune 清理）。"""
    mgr, panel = _burst_env(tmp_path, n=4)
    old_head = panel._pile_heads[0]
    panel._apply_pile_rename(old_head, "现场")
    panel._detach_asset(old_head, detach=True)
    new_head = panel._pile_heads[0]
    assert new_head != old_head
    assert old_head not in panel._pile_of
    assert panel._thumb_delegate._header_texts[new_head] == "3 张 · 09:00"


def test_orphan_annotations_pruned_on_refresh(tmp_path):
    """素材被删 → 指向它的堆名 / 移出标注在下次刷新时被惰性清理。"""
    import json as _json
    from src import asset_groups_store
    mgr, panel = _burst_env(tmp_path, n=4)
    dead_id = mgr._assets[3].asset_id
    panel._apply_pile_rename(panel._pile_heads[0], "现场")
    panel._detach_asset(dead_id, detach=True)
    assert panel._group_store[asset_groups_store.NAMES_KEY]
    # 模拟素材被外部删除
    mgr._assets = [a for a in mgr._assets if a.asset_id != dead_id]
    panel.refresh()
    assert dead_id not in panel._group_store[asset_groups_store.DETACHED_KEY]
    data = _json.loads(open(panel._groups_path, encoding="utf-8").read())
    assert dead_id not in data["asset_groups"]["detached"]


def test_flat_mode_has_no_pile_semantics(tmp_path):
    """平铺态没有堆语义：归属速查恒空（右键菜单据此不出堆操作项）。"""
    mgr, panel = _burst_env(tmp_path, n=3)
    panel._detach_asset(mgr._assets[0].asset_id, detach=True)   # 先在分组态留标注
    panel.set_grouping(False)
    assert panel._pile_of == {} and panel._pile_heads == []
    panel.set_grouping(True)
    assert panel._pile_of, "重新进入分组后堆归属必须重建（refresh 路径）"
