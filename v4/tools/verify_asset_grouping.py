# -*- coding: utf-8 -*-
"""Offscreen 功能验证：临时素材「会话分组」视图（第 4 卡 shot-burst）。

验证点（全部离屏实跑 + 真像素/数值断言，不是"没报错就算过"）：
  A. 默认关闭：首屏条目集合 = 全部素材（等价改动前平铺）
  B. 开启分组：条目集合与平铺**逐项相等**（只重排，不增删）
  C. 真聚类：5 张同刻连拍 → 1 堆，堆名 "5 张 · HH:MM"
  D. 阈值可调：改 asset_group_gap_seconds 改变堆结构（配置生效，非写死）
  E. 分组模式真出像素：堆标题栏区域与平铺模式**渲染不同**（切片比对）
  F. 切回平铺：条目顺序复原
  G. 不落库：开关分组前后 temp_assets.json 字节不变
  H. 观感返工（2026-10-03）：单张堆不画标题 / 堆内非首格挂序号 /
     标题条走专属高度且平铺态单元尺寸一字不变
  I. 旁路标注（2026-10-04 P0-2）：命名 / 移出 / 取消移出 /
     堆身份=堆首 / temp_assets.json 零接触
  J. 并入任意堆（2026-10-05）：目标清单互斥 / 并入既有堆 /
     新建堆自锚 / 移出并入者清标注 / temp_assets.json 零接触

运行（offscreen）：
  python tools/run_gui_check.py tools/verify_asset_grouping.py
或直接：
  QT_QPA_PLATFORM=offscreen python tools/verify_asset_grouping.py
"""
import hashlib
import os
import sys
import tempfile
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.assets_panel import AssetsPanel  # noqa: E402
from src.config import ConfigManager, DEFAULT_CONFIG  # noqa: E402
from src.temp_asset_manager import TempAssetManager, AssetInfo  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print("[OK] %s" % msg)


def bad(msg):
    global FAIL
    FAIL += 1
    print("[FAIL] %s" % msg)


def check(cond, msg):
    ok(msg) if cond else bad(msg)


def pump(app, ms=0):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def make_image(path, hue=120):
    img = QImage(200, 140, QImage.Format.Format_RGB32)
    img.fill(QColor(hue % 256, 200, 90))
    assert img.save(path)
    return path


def t_str(sec):
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 3, 9, 0, 0)
    return (base + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M:%S")


class NoopConfig:
    def __init__(self):
        self._config = dict(DEFAULT_CONFIG)

    def get(self, key, default=None):
        return self._config.get(key, default)

    def set(self, key, value):
        self._config[key] = value
        return True

    def save(self):
        return None


class Host:
    def __init__(self, mgr, cfg):
        self._config = cfg
        self.current_theme = "light"
        self._temp_asset_manager = mgr
        from types import SimpleNamespace
        self.data_changed = SimpleNamespace(emit=lambda *a: None)

    @property
    def config(self):
        # ★ 面板经 getattr(host, "config", None) 读配置（阈值/开关）——
        # 必须是 property；只挂 _config 属性会让 gap 恒回退默认 900，
        # D 段「阈值可调」静默失真（docs 验证坑：替身属性一律 @property）。
        return self._config


def list_ids(panel):
    """真实素材 id 序列（滤掉分组态的 spacer 占位格）。

    方案 A v2（2026-10-06）起分组序列含行首对齐占位格：UserRole+1 为
    None、UserRole+2 带 ("pile", head_id)/("fill", None) 标记——与本轮
    test_assets_grouping.py 的 _list_ids 同口径，只数真实素材。
    """
    out = []
    for i in range(panel._asset_list.count()):
        aid = panel._asset_list.item(i).data(Qt.ItemDataRole.UserRole + 1)
        if aid is not None:
            out.append(aid)
    return out


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def grab_cell(panel, index):
    """渲染第 index 格为 QImage（真走 delegate.paint，取像素）。"""
    from PyQt6.QtGui import QPainter
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem
    d = panel._thumb_delegate
    model_index = panel._asset_list.model().index(index, 0)
    img = QImage(d.CELL_W, d.CELL_H, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.white)
    p = QPainter(img)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, d.CELL_W, d.CELL_H)
    opt.state = QStyle.StateFlag.State_Enabled
    opt.font = panel._asset_list.font()
    opt.widget = panel._asset_list   # 方案 A 起容器段需经 widget 取视口列数
    d.paint(p, opt, model_index)
    p.end()
    return img


def main():
    app = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp(prefix="fp_verify_group_")

    cfg = ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))

    # ---- 构造 5 张同刻素材 + 2 张晚一小时的素材 ----
    mgr = TempAssetManager(tmp)
    imgs = [make_image(os.path.join(tmp, "burst%d.png" % i), 40 * i + 10)
            for i in range(5)]
    late = [make_image(os.path.join(tmp, "late%d.png" % i), 180 + 10 * i)
            for i in range(2)]
    mgr._assets = [
        AssetInfo(i + 1, "连拍%d.png" % i, imgs[i], True, 10, t_str(i * 15))
        for i in range(5)
    ] + [
        AssetInfo(i + 6, "晚%d.png" % i, late[i], True, 10, t_str(3600 + i))
        for i in range(2)
    ]
    mgr._next_id = 8

    panel = AssetsPanel(Host(mgr, cfg))
    panel._valid_ids = {a.asset_id for a in mgr._assets}
    panel.refresh()

    # A. 默认关闭 = 平铺
    check(not panel._is_grouping(), "A 默认关闭分组视图（等价改动前平铺行为）")
    flat = list_ids(panel)
    check(len(flat) == 7, "A 平铺模式铺满 7 条 (实际 %d)" % len(flat))
    check(panel._thumb_delegate._header_texts == {}, "A 平铺模式无堆标题")

    # G. 不落库：抓 json 摘要
    json_path = mgr._json_path
    d0 = digest(json_path) if os.path.exists(json_path) else None

    # B. 开启分组：集合相等
    panel.set_grouping(True)
    pump(app, 30)
    grouped = list_ids(panel)
    check(sorted(grouped) == sorted(flat), "B 分组后条目集合与平铺逐项相等（不丢图）")
    check(len(grouped) == 7, "B 分组模式仍 7 条 (实际 %d)" % len(grouped))

    # C. 真聚类：5 同刻 → 1 堆；2 晚一小时 → 1 堆；共 2 堆
    #    （方案 A 起标头为结构化 dict {"count","time","name","date"}）
    headers = panel._thumb_delegate._header_texts
    check(len(headers) == 2, "C 7 张聚成 2 堆 (实际 %d)" % len(headers))
    check(headers.get(1) == {"count": 5, "time": "09:00", "name": "",
                             "date": "10-03"},
          "C 连拍堆标头结构 = {count:5, time:'09:00', name:'', date:'10-03'}"
          " (实际 %r)" % (headers.get(1),))
    # E. 分组模式真出像素：首格（带堆标题）渲染与平铺不同
    cell_grouped = grab_cell(panel, 0)
    panel.set_grouping(False)
    pump(app, 30)
    cell_flat = grab_cell(panel, 0)
    check(cell_grouped != cell_flat, "E 分组首格渲染与平铺不同（堆标题真画上去了）")
    # 进一步：统计堆标题条内的主色像素（左缘 3px 刻度 + 文字）
    from src.theme import get_colors
    colors = get_colors("light")
    from src.glass import _to_color
    accent = _to_color(colors["primary"])
    hits = 0
    for y in range(2, 22):
        for x in range(6, 20):
            px = cell_grouped.pixelColor(x, y)
            if (abs(px.red() - accent.red()) < 60
                    and abs(px.green() - accent.green()) < 60
                    and abs(px.blue() - accent.blue()) < 60):
                hits += 1
    check(hits > 0, "E 堆标题条内检出主色刻度像素 (%d px)" % hits)

    # 存一张分组态截图供目检
    shots = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "build", "shots")
    try:
        os.makedirs(shots, exist_ok=True)
        panel.set_grouping(True)
        pump(app, 30)
        out = os.path.join(shots, "asset_group_final.png")
        cell_grouped.save(out)
        ok("E 分组态单元图已存 %s" % out)
    except OSError as exc:
        bad("E 截图落盘失败: %s" % exc)

    # D. 阈值可调（配置生效；用范围内值，≥10）
    #    5 张连拍间隔 15 秒 + 2 张晚片间隔 1 秒（gap 与「堆标题数」已解耦：
    #    单张堆不画标题，故额外用「堆内非首格数」的阶梯一起判）：
    #      gap=10  → 连拍各自独立(5) + 晚片并 1 堆 = 6 堆
    #                → 标题 1（只有晚片那对 ≥2）、序号 1（晚片第二张）
    #      gap=600 → 连拍并 1 堆 + 晚片并 1 堆 = 2 堆
    #                → 标题 2、序号 5（4 + 1）
    cfg.set("asset_group_gap_seconds", 10)
    panel.refresh()
    n_small = len(panel._thumb_delegate._header_texts)
    o_small = len(panel._thumb_delegate._member_ordinals)
    cfg.set("asset_group_gap_seconds", 600)
    panel.refresh()
    n_big = len(panel._thumb_delegate._header_texts)
    o_big = len(panel._thumb_delegate._member_ordinals)
    check(n_small == 1, "D gap=10s → 仅晚片 1 堆有标题（连拍的 5 张都是单张）"
                        "(实际 %d)" % n_small)
    check(n_big == 2, "D gap=600s → 2 堆都有标题 (实际 %d)" % n_big)
    check(o_small == 1 and o_big == 5,
          "D 序号阶梯 1 → 5（阈值改动真改变堆结构，非写死）(实际 %d/%d)"
          % (o_small, o_big))
    check(n_small != n_big and o_small != o_big,
          "D 阈值改动确实改变分组结果（配置生效，非写死）")
    # 越界值被拒（护栏：阈值有范围）
    check(cfg.set("asset_group_gap_seconds", 5) is False, "D 阈值 <10 被配置拒绝")
    check(cfg.set("asset_group_gap_seconds", 99999) is False, "D 阈值 >3600 被配置拒绝")

    # F. 切回平铺：顺序复原
    cfg.set("asset_group_gap_seconds", 120)
    panel.set_grouping(False)
    pump(app, 30)
    back = list_ids(panel)
    check(back == flat, "F 切回平铺后顺序与初始平铺一致")

    # G. 不落库
    d1 = digest(json_path) if os.path.exists(json_path) else None
    check(d0 == d1, "G 全程 temp_assets.json 字节不变（零落库）")

    # ---- H. 观感返工（2026-10-03 用户目检反馈）----
    # H1. 标题条走**专属**高度：分组态单元高 = 平铺 + HEADER_H，缩略图不变
    panel.set_grouping(False)
    pump(app, 30)
    flat_h = panel._thumb_delegate.CELL_H
    panel.set_grouping(True)
    pump(app, 30)
    d = panel._thumb_delegate
    check(d.CELL_H == flat_h + d.HEADER_H,
          "H1 分组态单元高 %d = 平铺 %d + 标题条 %d (实际 %d)"
          % (flat_h + d.HEADER_H, flat_h, d.HEADER_H, d.CELL_H))
    check(d.THUMB_H == flat_h - 64,
          "H1 缩略图尺寸不随分组变 (THUMB_H=%d)" % d.THUMB_H)

    # H2. 三张各隔 1 小时（全单张堆）→ 一个标题都不许有，但必须逐张铺出
    mgr2 = TempAssetManager(tmp)
    img2 = make_image(os.path.join(tmp, "solo.png"), 200)
    mgr2._assets = [AssetInfo(i, "solo%d.png" % i, img2, True, 10, t_str(i * 3600))
                    for i in range(1, 4)]
    mgr2._next_id = 4
    cfg.set("asset_group_gap_seconds", 120)
    panel2 = AssetsPanel(Host(mgr2, cfg))
    panel2._valid_ids = {a.asset_id for a in mgr2._assets}
    panel2.set_grouping(True)
    pump(app, 30)
    d2 = panel2._thumb_delegate
    check(d2._header_texts == {},
          "H2 单张堆不画标题条 (实际 %r)" % (d2._header_texts,))
    check(d2._member_ordinals == {}, "H2 单张堆无堆内序号")
    check(len(list_ids(panel2)) == 3, "H2 单张堆仍逐张铺出（不丢图，滤 spacer 后）")
    check(d2.CELL_H == d2.THUMB_H + 64 + d2.HEADER_H,
          "H2 有单张堆时整批仍统一垫高（否则同排缩略图错位）")

    # H3. 堆内非首格挂 "2 / 5" 序号（方案 A：分子=位次，分母=堆大小），
    #     堆首保留真实文件名
    cfg.set("asset_group_gap_seconds", 900)
    panel.refresh()
    ords = panel._thumb_delegate._member_ordinals
    check(sorted(ords.values()) == ["2 / 2", "2 / 5", "3 / 5", "4 / 5",
                                    "5 / 5"],
          "H3 两组各自从 2/N 起编号（5 张 → 2/5..5/5，2 张 → 2/2）(实际 %r)"
          % (sorted(ords.values()),))
    check(not (set(panel._thumb_delegate._header_texts) & set(ords)),
          "H3 堆首格不挂序号（保留真实文件名）")

    # H4. 切回平铺：两张表都清空（分组态残留不许带回去）
    panel.set_grouping(False)
    pump(app, 30)
    check(panel._thumb_delegate._header_texts == {}
          and panel._thumb_delegate._member_ordinals == {},
          "H4 平铺态无标题、无序号")
    check(panel._thumb_delegate._group_mode is False
          and panel._thumb_delegate.CELL_H == flat_h,
          "H4 平铺态单元高回到 %d" % flat_h)

    # ---- I. 会话堆旁路标注（命名 / 移出；2026-10-04 P0-2）----
    import json as _json
    d_before_annotation = digest(json_path) if os.path.exists(json_path) else None
    groups_path = panel._groups_path
    check(bool(groups_path) and groups_path.endswith("asset_groups.json"),
          "I0 旁路标注文件与 temp_assets.json 同目录 (%s)" % groups_path)

    # I1. 命名：堆标题换自定义名，标注落 asset_groups.json
    panel.set_grouping(True)
    pump(app, 30)
    head_id = panel._pile_heads[0]
    panel._apply_pile_rename(head_id, "登录页排障现场")
    check((panel._thumb_delegate._header_texts.get(head_id) or {})
          .get("name") == "登录页排障现场",
          "I1 堆标题换自定义名（结构化 name 字段）(实际 %r)"
          % (panel._thumb_delegate._header_texts.get(head_id),))
    data = _json.loads(open(groups_path, encoding="utf-8").read())
    check(data["asset_groups"]["names"] == {str(head_id): "登录页排障现场"},
          "I1 标注已落 asset_groups.json (names=%r)"
          % (data["asset_groups"]["names"],))

    # I2. 移出：成员拆出独立渲染（条目齐全、无序号），取消移出后回到堆
    victim = [a.asset_id for a in mgr._assets
              if panel._pile_of.get(a.asset_id) == head_id][1]  # 非堆首
    panel._detach_asset(victim, detach=True)
    check(victim not in panel._pile_of
          and victim not in panel._thumb_delegate._member_ordinals,
          "I2 移出后不再算堆成员、不挂序号")
    check(len(list_ids(panel)) == 7, "I2 移出只拆堆不丢图 (实际 %d)"
          % len(list_ids(panel)))
    panel._detach_asset(victim, detach=False)
    check(panel._pile_of.get(victim) == head_id, "I2 取消移出回到原堆")

    # I3. 标注全程 temp_assets.json 零接触（红线）
    d_after_annotation = digest(json_path) if os.path.exists(json_path) else None
    check(d_before_annotation == d_after_annotation,
          "I3 命名/移出全程 temp_assets.json 字节不变")

    # I4. 堆身份 = 堆首：堆首被移出 → 新堆首接棒，旧堆名不串堆
    panel._detach_asset(head_id, detach=True)
    new_head = panel._pile_heads[0]
    check(new_head != head_id
          and (panel._thumb_delegate._header_texts.get(new_head) or {})
          .get("name") == "",
          "I4 堆首移出后新堆首用默认名（旧堆名不串堆）(实际 %r)"
          % (panel._thumb_delegate._header_texts.get(new_head),))
    panel._detach_asset(head_id, detach=False)
    # 收尾：清掉测试标注，别把「现场」留进后续截图
    panel._apply_pile_rename(head_id, "")
    check((panel._thumb_delegate._header_texts.get(head_id) or {})
          == {"count": 5, "time": "09:00", "name": "", "date": "10-03"},
          "I5 清空堆名 = 恢复默认名（count/time 照填）")

    # ---- J. 并入任意堆（merged 旁路标注；2026-10-05）----
    # 此刻两堆：堆首 1（连拍 5 张，09:00–09:01）+ 堆首 6（晚片 2 张，10:00）
    check(panel._is_grouping() and panel._pile_heads == [1, 6],
          "J0 前置：分组态两堆 (实际 %r)" % (panel._pile_heads,))

    # J1. 目标清单：不含素材当前所在堆，文案带张数与时间
    targets = panel._merge_targets(2)
    check([h for h, _ in targets] == [6],
          "J1 并入目标清单不含当前所在堆 (实际 %r)" % (targets,))
    check(bool(targets) and targets[0][1] == "2 张 · 10:00",
          "J1 目标文案 = '2 张 · 10:00' (实际 %r)" % (targets,))
    check(panel._merge_targets(6) and
          [h for h, _ in panel._merge_targets(6)] == [1],
          "J1 目标堆一侧同样互斥（只列对方堆）")

    # J2. 并入既有堆：渲染归目标堆尾、堆首不变、标题计数更新
    panel._merge_asset(2, 6)
    check(panel._pile_of.get(2) == 6, "J2 并入后归属目标堆（堆首 6）")
    check((panel._thumb_delegate._header_texts.get(6) or {})
          .get("count") == 3
          and (panel._thumb_delegate._header_texts.get(6) or {})
          .get("time") == "10:00",
          "J2 目标堆标题计数更新 (实际 %r)"
          % (panel._thumb_delegate._header_texts.get(6),))
    check((panel._thumb_delegate._header_texts.get(1) or {})
          .get("count") == 4,
          "J2 原堆标题计数更新 (实际 %r)"
          % (panel._thumb_delegate._header_texts.get(1),))
    check(2 in panel._thumb_delegate._member_ordinals, "J2 并入者挂堆内序号")
    check(len(list_ids(panel)) == 7, "J2 并入只搬家不丢图 (实际 %d)"
          % len(list_ids(panel)))
    data = _json.loads(open(groups_path, encoding="utf-8").read())
    check(data["asset_groups"].get("merged") == {"2": "6"},
          "J2 并入标注落 asset_groups.json merged 键 (实际 %r)"
          % (data["asset_groups"].get("merged"),))

    # J3. 红线：并入全程 temp_assets.json 零接触
    check((digest(json_path) if os.path.exists(json_path) else None)
          == d_before_annotation,
          "J3 并入全程 temp_assets.json 字节不变")

    # J4. 「新建堆」：素材自锚成堆、单成员也挂标题；后续并入不抢锚点
    panel._merge_asset(5, None)
    check(panel._pile_of.get(5) == 5 and 5 in panel._pile_heads,
          "J4 新建堆：素材自锚成堆（锚点 5）")
    check((panel._thumb_delegate._header_texts.get(5) or {})
          .get("count") == 1
          and (panel._thumb_delegate._header_texts.get(5) or {})
          .get("time") == "09:01",
          "J4 新建堆单成员也挂标题 (实际 %r)"
          % (panel._thumb_delegate._header_texts.get(5),))
    panel._merge_asset(1, 5)
    ids_new = list_ids(panel)
    check(panel._pile_of.get(1) == 5 and ids_new.index(5) < ids_new.index(1),
          "J4 二次并入不抢锚点首格（时间更早也排后）")
    check((panel._thumb_delegate._header_texts.get(5) or {})
          .get("count") == 2,
          "J4 新建堆计数更新 (实际 %r)"
          % (panel._thumb_delegate._header_texts.get(5),))

    # J5. 移出并入者 = 并入标注一并清除；取消移出回天然聚类
    panel._detach_asset(1, detach=True)
    data = _json.loads(open(groups_path, encoding="utf-8").read())
    check("1" not in data["asset_groups"].get("merged", {}),
          "J5 移出并入者时并入标注一并清除 (实际 %r)"
          % (data["asset_groups"].get("merged"),))
    panel._detach_asset(1, detach=False)
    check(panel._pile_of.get(1) == 1, "J5 取消移出回天然聚类（堆首 1）")
    check((panel._thumb_delegate._header_texts.get(6) or {})
          .get("count") == 3,
          "J5 J2 的并入者不受影响（堆首 6 仍 3 张）")
    check((digest(json_path) if os.path.exists(json_path) else None)
          == d_before_annotation,
          "J5 全程 temp_assets.json 字节不变（红线复验）")

    print("\n==== 结果：%d 通过 / %d 失败 ====" % (PASS, FAIL))
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
