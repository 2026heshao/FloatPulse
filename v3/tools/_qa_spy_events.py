# -*- coding: utf-8 -*-
"""临时调试：E 场景第一次拖拽的 insert 计算过程。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QEvent, QPointF, QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from src.config import ConfigManager  # noqa: E402
from src.docx_manager import DocxManager  # noqa: E402
from src.task_manager import TaskManager  # noqa: E402
from src.note_manager import NoteManager  # noqa: E402
from src.fragment_manager import FragmentManager  # noqa: E402
from src.clipboard_monitor import ClipboardMonitor  # noqa: E402
from src.temp_asset_manager import TempAssetManager  # noqa: E402
from src.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
tmp = tempfile.mkdtemp()
config = ConfigManager(os.path.join(tmp, "config.json"))
docx = DocxManager(os.path.join(tmp, "k.docx"), os.path.join(tmp, "meta.json"))
docx.load()
task = TaskManager(os.path.join(tmp, "sch.json"))
note = NoteManager(os.path.join(tmp, "n.json"))
frag = FragmentManager(os.path.join(tmp, "f.json"))
clip = ClipboardMonitor(frag, config)
temp = TempAssetManager(tmp)
win = MainWindow(task, note, frag, docx, config, clip, temp)
win.show()
app.processEvents()

default_order = list(win._nav_order)
# 模拟 QA 前置：apps 已在首位
win._apply_nav_order(["apps"] + [k for k in default_order if k != "apps"],
                     save=False)
app.processEvents()
# 回默认序 + 立即中断动画
win._apply_nav_order(list(default_order), save=False)
win._abort_nav_settle_animations()
app.processEvents()
print("order:", win._nav_order)
print("btn pos (frozen):",
      {k: (b.pos().x(), b.pos().y()) for k, b in win._nav_btns.items()})

orig_moved = MainWindow._on_nav_drag_moved


def spy_moved(self, btn, global_pos):
    pos = self._nav_area.mapFromGlobal(global_pos)
    print("drag_moved: mouse_in_area_y =", pos.y(),
          "| fragments tl in area =",
          self._nav_btns["fragments"].mapTo(self._nav_area, QPoint(0, 0)).y(),
          "| order =", self._nav_order)
    return orig_moved(self, btn, global_pos)


MainWindow._on_nav_drag_moved = spy_moved


def me(etype, gpos):
    b = win._nav_btns["notes"]
    return QMouseEvent(etype, QPointF(b.mapFromGlobal(gpos)), QPointF(gpos),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


btn = win._nav_btns["notes"]
target = win._nav_btns["fragments"].mapToGlobal(QPoint(2, 2))
print("target global y:", target.y())
start = btn.mapToGlobal(btn.rect().center())
btn.mousePressEvent(me(QEvent.Type.MouseButtonPress, start))
btn.mouseMoveEvent(me(QEvent.Type.MouseMove, target))
btn.mouseReleaseEvent(me(QEvent.Type.MouseButtonRelease, target))
app.processEvents()
print("result order:", win._nav_order)
