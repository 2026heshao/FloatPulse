# -*- coding: utf-8 -*-
"""临时素材「会话分组」的聚类纯逻辑（**禁 import PyQt6**）。

为什么单独成模块
================
临时素材池里的连续截图（剪贴板 `剪贴板图片_*.png`）本质上是一次排障的
现场记录，却被平铺成互不相关的散图。要按 `added_time` 把"一口气连拍"的
截图聚成若干"会话堆"，核心问题 —— "怎么切段、段怎么命名、两笔数据聚出来
是不是同一些堆" —— **与 Qt 无关**，抽出来就能脱离 GUI 跑 pytest。

聚类按时间间隔切段，与侧栏 ``nav_layout`` 纯逻辑 / 渲染分离是同款切法：
离屏脚本跑一次要好几秒，纯函数测试毫秒级，能把"阈值边界切错 / 分组漏图"
这类静默缺陷钉死在提交前。

★ 本模块不落库：聚类只是**渲染时算出来的**，``temp_assets.json`` 一个字
都不许改（分组纯派生，关掉开关即回退平铺）。
"""

from datetime import datetime

# added_time 的落盘格式（temp_asset_manager.AssetInfo.added_time 同源）
_TIME_FMT = "%Y-%m-%d %H:%M:%S"

# 默认间隔阈值（秒）：两次添加间隔不超过它即视为"同一会话"。
# 该值是配置项 `asset_group_gap_seconds` 的默认值 —— 生产路径一律读
# 配置，本常量只作为「未显式传参」时的兜底（独立单测 / 旧调用方），
# 不算写死阈值。
DEFAULT_GAP_SECONDS = 120


def parse_added_time(text):
    """解析 ``"YYYY-MM-DD HH:MM:SS"`` → datetime；解析失败返回 None。

    失败**不抛异常**：素材元数据可能被手改 / 损坏，聚类要对坏数据容错
    （坏数据单独成堆，不拖垮整批）。
    """
    if not text:
        return None
    if isinstance(text, datetime):
        return text
    try:
        return datetime.strptime(str(text), _TIME_FMT)
    except (ValueError, TypeError):
        return None


def sort_key(asset):
    """把素材对象排成时间序： ``(dt, asset_id)``。

    时间缺失的素材排在**最前**（dt 用 datetime.min），且同一时间戳内按
    asset_id 稳定排序 —— 保证同一批数据每次聚出的堆**逐项一致**（顺序
    不稳定会让"关闭分组后条目集合相等"的断言忽红忽绿）。
    """
    dt = parse_added_time(getattr(asset, "added_time", ""))
    aid = getattr(asset, "asset_id", 0)
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        aid = 0
    return (dt if dt is not None else datetime.min, aid)


def cluster_assets(assets, gap_seconds=DEFAULT_GAP_SECONDS):
    """把素材切成有序的「会话堆」（纯函数，无副作用）。

    规则：按 ``added_time`` 升序（同刻按 asset_id），相邻两条时间差
    **严格小于** ``gap_seconds`` 归入同一堆，否则开新堆。

    - ``gap_seconds <= 0`` → 关闭聚类的等价形态：**每张各自成堆**
      （间隔 0 秒也断开，杜绝"零阈值吞并多张"的歧义）。
    - 解析不出时间的素材：相邻两条都坏时同堆（当作同一批现场记录），
      坏数据与好数据交界处按 `datetime.min` 参与比较（坏数据排序在前，
      通常自成一段）。

    返回： ``[[asset, ...], ...]``（堆内升序、堆间升序）。空输入 → ``[]``。
    """
    items = sorted(assets or [], key=sort_key)
    if not items:
        return []
    try:
        gap = float(gap_seconds)
    except (TypeError, ValueError):
        gap = float(DEFAULT_GAP_SECONDS)

    groups = [[items[0]]]
    prev = parse_added_time(getattr(items[0], "added_time", ""))
    for asset in items[1:]:
        cur = parse_added_time(getattr(asset, "added_time", ""))
        same = _within_gap(prev, cur, gap)
        if same:
            groups[-1].append(asset)
        else:
            groups.append([asset])
        prev = cur
    return groups


def _within_gap(prev_dt, cur_dt, gap: float) -> bool:
    """相邻两条是否属于同一堆（间隔严格小于 gap 秒）。"""
    if gap <= 0:
        return False                      # 关闭聚类：一张一堆
    if prev_dt is None or cur_dt is None:
        # 任一时间缺失 → 无法判定间隔。两条全缺视为同一批（坏数据自聚合），
        # 一好一坏视为断裂（留出边界，不把坏数据混进时间明确的会话）。
        return prev_dt is None and cur_dt is None
    return (cur_dt - prev_dt).total_seconds() < gap


def group_label(group, index: int) -> str:
    """一堆素材的默认名： ``"3 张 · 09:12"``（张数 · 首张 HH:MM）。

    首张时间缺失时退化为 ``"3 张"``。``index`` 保留在签名里以便调用方
    需要序号时复用（当前默认名不含序号，避免与时间信息重复啰嗦）。
    """
    n = len(group)
    head = group[0] if group else None
    dt = parse_added_time(getattr(head, "added_time", "")) if head else None
    if dt is None:
        return "%d 张" % n
    return "%d 张 · %02d:%02d" % (n, dt.hour, dt.minute)


def group_signature(groups):
    """把堆结构映射成可比较的签名（列表的列表 of ``(asset_id, added_time)``）。

    供"关闭分组后条目集合逐项一致"这类断言使用：两种渲染模式若产生
    **相同素材集合**，展平后的签名必须相等 —— 分组漏图会当场变红。
    """
    return [
        [(getattr(a, "asset_id", 0), getattr(a, "added_time", ""))
         for a in group]
        for group in groups
    ]


def flatten_groups(groups):
    """展平堆 → 素材列表（顺序 = 堆序 × 堆内序）。"""
    return [a for group in groups for a in group]
