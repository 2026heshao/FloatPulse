# -*- coding: utf-8 -*-
"""离屏验证：小卡片网址导航页 = 双列宽卡片（标题+域名副标题，2026-09-25 方案C）
回归护栏：卡片右边缘必须落在内容区内（2026-09-25 曾因 margin 链漏算溢出 8px 被裁）
"""
import os
import sys

# 本脚本位于 <项目根>/v4/tools/ 下，需把 **v4** 加进 sys.path 才能 import src.*
# （2026-09-30 修正：此前写死指向已废弃的 v3 目录，脚本长期无法运行）
V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V4)

from PyQt6.QtWidgets import QApplication, QPushButton, QLabel  # noqa: E402
from PyQt6.QtGui import QFontDatabase  # noqa: E402

app = QApplication(sys.argv)

# offscreen 下无系统字体，中文渲染需手动注入
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

from src.card_window import CardWindow, _domain_of_url  # noqa: E402
from src.nav_manager import NavSite  # noqa: E402

# 先纯测域名提取
assert _domain_of_url("https://ico.bot") == "ico.bot"
assert _domain_of_url("https://www.baidu.com/s?wd=x") == "baidu.com"
assert _domain_of_url("http://openrouter.ai:8080/api/v1") == "openrouter.ai"
assert _domain_of_url("baidu.com").endswith("baidu.com")  # 无 scheme 回退
print("[0] PASS 域名提取：scheme/www./端口/路径 全部剥掉")

cw = CardWindow()
sites = [
    NavSite(1, "ico图标转换", "https://ico.bot"),
    NavSite(2, "合伙人后台", "https://hehuoren.cn"),
    NavSite(3, "自己的工具网址很长很长会被截断", "https://tools.example.com"),
    NavSite(4, "vpn", "https://vpn.example.com"),
    NavSite(5, "openrouter", "https://openrouter.ai"),
    NavSite(6, "这是一个特别特别特别长的站点名称用来测试省略号", "https://long.example.com"),
    NavSite(7, "百度", "https://www.baidu.com"),
    NavSite(8, "另一个稍长一些的标题也不错", "https://another.example.com"),
]


class FakeNM:
    def get_all_sites_flat(self):
        return sites


cw.set_nav_manager(FakeNM())
cw._refresh_nav_page()

# 先切到导航页再收集卡片：QStackedWidget 隐藏页不做布局，且 _switch_mode("nav")
# 会触发 _refresh_nav_page 重建全部卡片（先收集的会指向已 deleteLater 的旧对象）
cw.show()
cw._switch_mode("nav")
app.processEvents()

cards = []
grid = None
for i in range(cw._nav_content_layout.count()):
    w = cw._nav_content_layout.itemAt(i).widget()
    if w is not None and w.layout() is not None:
        grid = w.layout()
        for gi in range(grid.count()):
            it = grid.itemAt(gi)
            if it.widget() and isinstance(it.widget(), QPushButton):
                cards.append(it.widget())

assert len(cards) == len(sites), f"卡片数量 {len(cards)} != {len(sites)}"

# 尺寸：高度统一 46，宽度统一（由网格均分，不写死）
heights = {c.height() for c in cards}
widths = {c.width() for c in cards}
print(f"[1] 卡片宽度集合: {widths}，高度集合: {heights}")
assert heights == {46}, "卡片高度不统一"
assert len(widths) == 1, "卡片宽度不统一"

# ==== 回归护栏：卡片右边缘不得超出内容区（_nav_content）====
content_w = cw._nav_content.width()
card_w = cards[0].width()
rights = [c.mapTo(cw._nav_content, c.rect().topRight()).x() for c in cards]
print(f"[2] 内容区宽 {content_w}，卡片宽 {card_w}，最右边缘 {max(rights)}")
assert 300 <= content_w <= 352, f"内容区宽度异常: {content_w}"
assert abs(card_w - (content_w - 6) / 2) <= 2, f"卡片宽 {card_w} != 内容区均分 {(content_w - 6) / 2}"
assert max(rights) <= content_w, f"卡片溢出内容区: 最右 {max(rights)} > {content_w}"
print("[2] PASS 卡片右边缘全部在内容区内（无截断）")

# 网格坐标：2 列
cells = [grid.getItemPosition(grid.indexOf(c)) for c in cards]
grid_cols = {c for r, c, rs, cs in cells}
row_counts = {}
for r, c, rs, cs in cells:
    row_counts.setdefault(r, 0)
    row_counts[r] += 1
rows_sorted = [row_counts[r] for r in sorted(row_counts)]
print(f"[3] 行数: {len(row_counts)}，每行个数: {rows_sorted}，列数: {len(grid_cols)}")
assert len(grid_cols) == 2, f"列数 {len(grid_cols)} != 2"
assert all(n == 2 for n in rows_sorted[:-1]), "非末行不足2个"
print("[3] PASS 双列网格排布正确")

# 内容：每张卡 = 标题 + 域名 两个 QLabel；objectName 正确（QSS 能命中）
elided_any = False
for card, s in zip(cards, sites):
    labels = card.findChildren(QLabel)
    names = sorted(lb.objectName() for lb in labels)
    assert names == ["navSiteCardDomain", "navSiteCardTitle"], f"子标签异常: {names}"
    if "…" in labels[0].text():
        elided_any = True
    assert card.toolTip().startswith(s.title), f"tooltip 未含完整标题: {card.toolTip()!r}"
dom_of = {c.findChildren(QLabel)[0].text(): c.findChildren(QLabel)[1].text() for c in cards}
print(f"[4] 标题→域名: {dom_of}")
assert dom_of["百度"] == "baidu.com", "www. 未剥掉"
assert dom_of["ico图标转换"] == "ico.bot"
assert elided_any, "没有任何长标题被省略号截断"
print("[4] PASS 卡片内容（标题截断 + 域名副标题 + tooltip）正确")

print("ALL PASS")
