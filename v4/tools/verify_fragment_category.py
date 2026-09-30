# -*- coding: utf-8 -*-
"""碎片内容分类（第二筛选轴）离屏验证。

覆盖：下拉存在性与项数 / 双轴 AND 筛选联动 / 计数标签一致性 /
切回全部恢复 / 空结果态「清空筛选条件」同时复位两个下拉 /
列表渲染（含类别色条 delegate）不崩。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from src.fragment_manager import FragmentManager
from src.fragment_classifier import (
    CAT_CODE,
)

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeHost(QObject):
    """FragmentsPanel 所需的最小宿主替身"""

    data_changed = pyqtSignal(str)

    def __init__(self, frag_mgr):
        super().__init__()
        self._fragment_manager = frag_mgr
        self._note_manager = None
        self._docx_manager = None
        self._nav_manager = None
        self._clipboard_monitor = None
        from src.config import ConfigManager
        tmp = tempfile.mkdtemp(prefix="fp_verify_fragcat_")
        self._config = ConfigManager(os.path.join(tmp, "config.json"))

    @property
    def current_theme(self):
        return "dark"

    @property
    def _container(self):
        return None


def main():
    _app = QApplication.instance() or QApplication(sys.argv)
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    from src.fragments_panel import FragmentsPanel

    # ---- 混合数据：text 2 / link 1 / code 2 / path 1 / command 1 ----
    tmp = tempfile.mkdtemp(prefix="fp_verify_fragcat_data_")
    mgr = FragmentManager(os.path.join(tmp, "fragments.json"))
    mgr.add_clipboard_text("今天天气不错，适合出去走走")
    mgr.add_clipboard_text("https://github.com/example/repo")
    mgr.add_clipboard_text("def hello():\n    return 1")
    mgr.add_clipboard_text("class Foo:\n    pass")
    mgr.add_clipboard_path("C:/Users/a.txt")
    mgr.add_clipboard_text("pip install PyQt6")
    mgr.add_clipboard_text("普通文本第二条，没有任何特征")
    counts = mgr.category_counts()
    total = mgr.count()
    print(f"数据：共 {total} 条，类别计数 {counts}")

    panel = FragmentsPanel(FakeHost(mgr))
    panel.show()            # offscreen 下 show 才会走布局与绘制
    _app.processEvents()
    # 面板构造不填列表（初始填充靠主窗口 refresh_page），离屏脚本手动补
    panel.refresh(preserve_view=False)
    _app.processEvents()

    def real_items():
        n = 0
        for i in range(panel._frag_list.count()):
            if panel._frag_list.item(i).data(Qt.ItemDataRole.UserRole) is not None:
                n += 1
        return n

    print("== A. 类别下拉 ==")
    combo = panel._frag_category
    check("A1 下拉存在且 6 项", combo is not None and combo.count() == 6,
          f"count={combo.count() if combo else 'None'}")
    check("A2 首项为全部内容(all)",
          combo.itemData(0) == "all" and combo.currentIndex() == 0)

    print("== B. 全部内容：7 条全显 ==")
    check("B1 列表条数=总数", real_items() == total,
          f"list={real_items()} total={total}")
    check("B2 计数标签一致", f"共 {total} 条" in panel._frag_count_label.text(),
          panel._frag_count_label.text())

    print("== C. 切到「代码」 ==")
    code_idx = next(i for i in range(combo.count())
                    if combo.itemData(i) == CAT_CODE)
    combo.setCurrentIndex(code_idx)
    _app.processEvents()
    expect = counts.get(CAT_CODE, 0)
    check("C1 列表条数==category_counts()['code']", real_items() == expect,
          f"list={real_items()} expect={expect}")
    check("C2 计数标签同步", f"显示 {expect} 条" in panel._frag_count_label.text(),
          panel._frag_count_label.text())

    print("== D. 双轴 AND（类型=剪贴板路径 × 内容=路径） ==")
    combo.setCurrentIndex(0)               # 先复位内容轴，避免残留 C 段的「代码」
    _app.processEvents()
    type_combo = panel._frag_filter
    path_idx = next(i for i in range(type_combo.count())
                    if type_combo.itemData(i) == "clipboard_path")
    type_combo.setCurrentIndex(path_idx)
    _app.processEvents()
    # clipboard_path 只有 1 条且 category=path
    check("D1 AND 组合命中 1 条", real_items() == 1, f"list={real_items()}")
    type_combo.setCurrentIndex(0)          # 回到全部类型

    print("== E. 切回全部内容恢复 ==")
    combo.setCurrentIndex(0)
    _app.processEvents()
    check("E1 条数恢复", real_items() == total, f"list={real_items()}")

    print("== F. 空结果态 + 清空按钮复位两个下拉 ==")
    panel._frag_search.setText("绝不存在的关键词xyz")
    _app.processEvents()
    panel._search_timer.stop()
    panel.refresh(preserve_view=False)
    check("F1 进入 no_result 空态", panel._empty_state.isVisible()
          and panel._empty_state._action.isVisibleTo(panel._empty_state))
    combo.setCurrentIndex(code_idx)        # 类别也筛上，制造双轴筛选态
    _app.processEvents()
    panel._empty_state._action.click()     # 点「清空筛选条件」
    _app.processEvents()
    check("F2 类型下拉复位", panel._frag_filter.currentIndex() == 0)
    check("F3 内容下拉复位", panel._frag_category.currentIndex() == 0)
    check("F4 搜索词清空", panel._frag_search.text() == "")
    check("F5 列表恢复全量", real_items() == total, f"list={real_items()}")

    print("== G. 渲染冒烟（含类别色条 delegate） ==")
    try:
        panel.grab()
        ok = True
    except Exception as exc:
        ok = False
        print("   grab exception:", exc)
    check("G1 列表绘制不抛异常", ok)

    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
