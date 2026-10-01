# -*- coding: utf-8 -*-
"""
====================================================================
大列表窗口化渲染助手  -  list_windowing
====================================================================
成熟化 3.6 / 优化调研 1.3：碎片工作台与日程任务的列表原先每次 refresh
都 clear() + 全量重建所有行；数据量大（几百上千条）时刷新明显卡顿。
真·QListView+model 改造风险大，本轮做「分块追加 + 滚动加载」窗口化：

  - 首屏只建前 FIRST_CHUNK 行（默认 120）；
  - 滚动接近底部时追加下一块（120 行/块）；
  - 行数 ≤ FULL_THRESHOLD（默认 200）时全量直建——小数据量路径与
    旧实现完全一致，避免小列表回归。

结构：**纯逻辑决策面**（first_build_count / next_build_count /
ListWindowing 状态机，不依赖 Qt，可离线单测）与 **Qt 接线**
（attach_scroll_loader，把「滚动接近底部」翻译成追加回调）分离。
面板侧持有 ListWindowing + 行描述符表，追加回调里按块建行。
====================================================================
"""

from PyQt6.QtWidgets import QAbstractScrollArea

# 首屏 / 每块行数：120 行足够填满任何常见视口，重建成本 ≈ 旧全量路径的零头
FIRST_CHUNK = 120
# 全量阈值：行数不超过它时一次性全建（行为与旧实现完全一致）
FULL_THRESHOLD = 200


def first_build_count(total: int, threshold: int = FULL_THRESHOLD,
                      chunk: int = FIRST_CHUNK) -> int:
    """首屏应建行数：小列表全量直建，大列表只建首块。"""
    if total <= threshold:
        return total
    return min(chunk, total)


def next_build_count(built: int, total: int, chunk: int = FIRST_CHUNK) -> int:
    """接近底部追加时应建行数：已建满返回 0，否则一块（封顶到剩余量）。"""
    if built >= total:
        return 0
    return min(chunk, total - built)


class ListWindowing:
    """分块渲染决策状态机：记录 total / built，回答「现在建几行」。

    面板用法（两步）：
      reset(total_rows)  → 返回首屏应建行数（换筛选/搜索/刷新时调用）；
      extend()           → 追加一块，返回本次应建行数（0 = 已建满）。
    面板按返回值从自己的行描述符表取行建 item，保证状态与列表一致。
    """

    def __init__(self, threshold: int = FULL_THRESHOLD,
                 chunk: int = FIRST_CHUNK):
        self._threshold = threshold
        self._chunk = chunk
        self._total = 0
        self._built = 0

    @property
    def total(self) -> int:
        return self._total

    @property
    def built(self) -> int:
        return self._built

    @property
    def windowed(self) -> bool:
        """是否处于窗口化（总行数超阈值）——未窗口化时追加回调直接跳过"""
        return self._total > self._threshold

    def reset(self, total: int) -> int:
        """换数据集（筛选/搜索/刷新）：整树重置，返回首屏应建行数。"""
        self._total = max(0, int(total))
        self._built = 0
        self._built = first_build_count(self._total, self._threshold,
                                        self._chunk)
        return self._built

    def extend(self) -> int:
        """追加下一块：返回本次应建行数（0 = 已建满，无需追加）。"""
        n = next_build_count(self._built, self._total, self._chunk)
        self._built += n
        return n


def attach_scroll_loader(scroll_widget: QAbstractScrollArea, on_more,
                         slack: int = 40):
    """把「滚动接近底部 → 请求追加」接到滚动区域（QListWidget 适用）。

    on_more 回调在滚动条接近底部（距底 ≤ slack 像素）时被调用；是否真的
    追加由回调自行判断（面板侧查 windowed / 已建满 / 是否在重建中）。
    返回解绑函数（面板与滚动条同生命周期，一般无需手动调用）。
    """
    bar = scroll_widget.verticalScrollBar()

    def _on_value(_value: int):
        if bar.maximum() - bar.value() <= slack:
            on_more()

    bar.valueChanged.connect(_on_value)

    def _detach():
        try:
            bar.valueChanged.disconnect(_on_value)
        except TypeError:
            pass

    return _detach
