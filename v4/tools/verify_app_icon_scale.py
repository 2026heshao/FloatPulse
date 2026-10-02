# -*- coding: utf-8 -*-
"""离屏验证：软件导航卡片图标**真的**随卡片尺寸缩放（+ shell 大图标提取）。

背景（2026-10-02，用户第二次反馈「图标大小调不动」）：
卡片框跟着设置放大了，里面的图标纹丝不动。根因两层：
  1. ``QIcon.pixmap()`` **从不放大** —— 源帧小于请求尺寸就原样返回；
     对 ``.lnk`` 更极端：请求 40/76/108/120 一律只回 32px（125% DPI 实测
     40px），而 ``availableSizes()`` / ``actualSize()`` 都谎报大尺寸。
  2. 因此必须①优先用 shell jumbo 图像列表拿 256px 真高分图，②无论走哪条
     路径都用 ``_fit_icon`` 把结果收口到请求尺寸。

真实 Windows 平台（dpr=1.25）实测对照：
  BEFORE  card 60→140 图标可见宽 39→39（跨度 0）
  AFTER   card 60→140 图标可见宽 38→116（跨度 78）

覆盖：
  A. _fit_icon：放大 / 缩小 / 已正确不重采样 / 高 dpr 源保留 / 空位图与 0 尺寸
  B. extract_exe_icon：Qt 兜底路径也收口、shell 优先且收口、缓存命中
  C. 端到端：真实卡片在四档尺寸下的图标**可见外接框**必须单调增大
  D. 源码级钉子：两条防线（shell 大图 + 尺寸收口）不得被删
  E. 真截图：修复前/后对照（build/shots/icon_scale_{before,after}.png）

运行方式（必须 offscreen）：
  python tools/run_gui_check.py tools/verify_app_icon_scale.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,  # noqa: E402
                             QLabel, QFileIconProvider)
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QFontDatabase,  # noqa: E402
                         QPixmapCache, QGuiApplication)
from PyQt6.QtCore import QSize, QFileInfo, Qt  # noqa: E402

import src.widget_app_launcher as wal  # noqa: E402
from src.constants import MINI_ICON_DEFAULT  # noqa: E402
from src import win_icons  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(BASE, "build", "shots")
SIZES = (60, 96, 128, 140)

FAIL = []


def check(cond, msg):
    if cond:
        print("[OK] %s" % msg)
    else:
        print("[!!] %s" % msg)
        FAIL.append(msg)


def logical(pixmap):
    """位图的逻辑边长（Qt 绘制时用的尺寸）。"""
    dpr = pixmap.devicePixelRatio() or 1.0
    return pixmap.width() / dpr


def content_bbox(pixmap):
    """非透明像素外接框的逻辑尺寸 —— 「图标看起来多大」。"""
    if pixmap is None or pixmap.isNull():
        return (0, 0)
    img = pixmap.toImage()
    dpr = pixmap.devicePixelRatio() or 1.0
    minx, miny, maxx, maxy = 10 ** 6, 10 ** 6, -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 16:
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
    if maxx < 0:
        return (0, 0)
    return ((maxx - minx + 1) / dpr, (maxy - miny + 1) / dpr)


def solid_pixmap(size, dpr=1.0):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setBrush(QColor("#5BC0BE"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class _FakeProvider:
    """模拟「系统只给 32px 图标」的 exe —— 即 QIcon 不放大时的情形。"""

    def icon(self, _info):
        return self


class _FakeIcon:
    def pixmap(self, _size):
        return solid_pixmap(32)


def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    QApplication.setApplicationName("verify_app_icon_scale")
    if os.path.exists(r"C:\Windows\Fonts\msyh.ttc"):
        QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")

    scr = QGuiApplication.primaryScreen()
    dpr = scr.devicePixelRatio() if scr else 1.0
    print("平台=%s  dpr=%s  shell 大图标可用=%s\n"
          % (QGuiApplication.platformName(), dpr, win_icons.is_available()))

    # ================= A. _fit_icon =================
    print("== A. _fit_icon 尺寸收口 ==")
    check(abs(logical(wal._fit_icon(solid_pixmap(32), 76)) - 76) < 0.5,
          "A1 32px 源图请求 76px → 逻辑 %.1f（放大生效）"
          % logical(wal._fit_icon(solid_pixmap(32), 76)))
    check(abs(logical(wal._fit_icon(solid_pixmap(256), 108)) - 108) < 0.5,
          "A2 256px 源图请求 108px → 正确缩小")
    same = wal._fit_icon(solid_pixmap(76), 76)
    check(abs(logical(same) - 76) < 0.5, "A3 已正确的位图不无谓重采样")
    hi = solid_pixmap(256, dpr=2.3703703703703702)     # 逻辑 ≈108
    check(wal._fit_icon(hi, 108) is hi, "A4 高 dpr 源位图原样保留")
    check(wal._fit_icon(QPixmap(), 64).isNull(), "A5 空位图安全")
    tiny = wal._fit_icon(solid_pixmap(32), 0)
    check(not tiny.isNull() and tiny.width() >= 1, "A6 尺寸 0 不返回空图")

    # ================= B. extract_exe_icon =================
    print("\n== B. extract_exe_icon 两条路径都收口 ==")
    fake_exe = os.path.join(BASE, "build", "_verify_fake.exe")
    os.makedirs(os.path.dirname(fake_exe), exist_ok=True)
    with open(fake_exe, "wb") as f:
        f.write(b"MZ")

    real_shell = win_icons.shell_icon_image
    real_provider = wal.QFileIconProvider
    try:
        # ① shell 拿不到 → Qt 兜底也必须收口
        win_icons.shell_icon_image = lambda _p: None
        wal.QFileIconProvider = lambda: _FakeProvider()
        ok = True
        detail = []
        for size in (40, 76, 108, 120):
            QPixmapCache.clear()
            got = logical(wal.extract_exe_icon(fake_exe, size))
            detail.append("%d→%.0f" % (size, got))
            ok = ok and abs(got - size) < 0.5
        check(ok, "B1 shell 不可用时 Qt 兜底仍收口（%s）" % " ".join(detail))

        # ② shell 有大图 → 优先用且收口
        img = solid_pixmap(256).toImage()
        win_icons.shell_icon_image = lambda _p: img
        QPixmapCache.clear()
        got = logical(wal.extract_exe_icon(fake_exe, 108))
        check(abs(got - 108) < 0.5, "B2 shell 大图优先且收口（108→%.0f）" % got)

        # ③ 缓存命中
        QPixmapCache.clear()
        a = wal.extract_exe_icon(fake_exe, 76)
        b = wal.extract_exe_icon(fake_exe, 76)
        check(a.cacheKey() == b.cacheKey(), "B3 同 (路径,尺寸) 命中缓存")
    finally:
        win_icons.shell_icon_image = real_shell
        wal.QFileIconProvider = real_provider
        if os.path.exists(fake_exe):
            os.remove(fake_exe)

    check(abs(logical(wal.extract_exe_icon("", 64)) - 64) < 0.5,
          "B4 空路径回退占位图且尺寸正确")

    # ================= C. 端到端：真实卡片 =================
    print("\n== C. 端到端：卡片尺寸 → 图标可见尺寸 ==")
    real_app_entry = {"name": "演示", "exe_path": "",
                      "icon_path": ""}
    if win_icons.is_available():
        for cand in (r"C:\Windows\System32\notepad.exe",
                     r"C:\Windows\explorer.exe"):
            if os.path.exists(cand):
                real_app_entry = {"name": "记事本", "exe_path": cand,
                                  "icon_path": ""}
                break
    print("   使用条目: %s" % real_app_entry["exe_path"] or "（占位图）")

    bboxes = []
    for size in SIZES:
        QPixmapCache.clear()
        card = wal.AppCardWidget(real_app_entry, 0, size)
        card.show()
        app.processEvents()
        pm = card._icon_label.pixmap()
        bboxes.append(content_bbox(pm)[0])
        card.hide()
        card.deleteLater()
    print("   可见图标宽: %s" % ["%.0f" % b for b in bboxes])

    grew = all(bboxes[i] < bboxes[i + 1] for i in range(len(bboxes) - 1))
    span = bboxes[-1] - bboxes[0]
    check(grew and span > 40,
          "C1 图标可见尺寸随卡片单调增大（跨度 %.0f，修复前恒为 0）" % span)

    # 逐档核对「请求 = 卡片 - 20，实测 = 内容外接框」
    for size, bw in zip(SIZES, bboxes):
        want = size - 20
        check(bw > want * 0.6,
              "C2 卡片 %d → 请求 %dpx，实测可见 %.0fpx（≥60%%）"
              % (size, want, bw))

    # ================= D. 源码级钉子 =================
    print("\n== D. 源码级钉子 ==")
    import inspect
    src_extract = inspect.getsource(wal.extract_exe_icon)
    check("shell_icon_image" in src_extract,
          "D1 extract_exe_icon 保留 shell 大图标路径（.lnk 否则退回 32px）")
    check("_fit_icon" in src_extract,
          "D2 extract_exe_icon 保留尺寸收口（QIcon 不放大）")
    check("_fit_icon" in inspect.getsource(wal.load_icon_pixmap),
          "D3 load_icon_pixmap 走同一套收口（口径唯一）")
    check(MINI_ICON_DEFAULT == 36, "D4 小卡片默认图标尺寸常量未漂移")

    # ================= E. 真截图 =================
    print("\n== E. 真截图（修复前 / 修复后对照）==")
    before, after = [], []
    QPixmapCache.clear()
    for size in SIZES:
        class _OldCard(wal.AppCardWidget):
            def _load_icon(self):
                ic = QFileIconProvider().icon(QFileInfo(real_app_entry["exe_path"]))
                self._icon_label.setPixmap(ic.pixmap(QSize(self._card_size - 20,
                                                           self._card_size - 20)))
        before.append(_OldCard(real_app_entry, 0, size))
    QPixmapCache.clear()
    win_icons.clear_cache()
    after = [wal.AppCardWidget(real_app_entry, 0, size) for size in SIZES]

    p1 = _strip("修复前：图标恒定 32px，卡片再大也不动", before)
    p1.show()
    app.processEvents()
    p1.grab().save(os.path.join(SHOT_DIR, "icon_scale_before.png"))
    p2 = _strip("修复后：图标真正随卡片缩放", after)
    p2.show()
    app.processEvents()
    p2.grab().save(os.path.join(SHOT_DIR, "icon_scale_after.png"))
    for name in ("icon_scale_before.png", "icon_scale_after.png"):
        check(os.path.exists(os.path.join(SHOT_DIR, name)),
              "E1 截图已保存 %s" % name)

    print("\n产物目录：%s" % SHOT_DIR)
    if FAIL:
        print("\n[FAIL] %d 项未通过：" % len(FAIL))
        for m in FAIL:
            print("  - %s" % m)
        return 1
    print("\n[DONE] 全部通过")
    return 0


def _strip(label, cards):
    """四档卡片并排的对照图。"""
    page = QWidget()
    page.setStyleSheet("background:#1b2430;")
    v = QVBoxLayout(page)
    v.setContentsMargins(18, 14, 18, 14)
    t = QLabel(label)
    t.setStyleSheet("color:#eaf2f8;font-size:15px;font-weight:bold;")
    v.addWidget(t)
    row = QHBoxLayout()
    row.setSpacing(14)
    for card in cards:
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
        cap = QLabel("卡片 %d" % card._card_size)
        cap.setStyleSheet("color:#9fb3c8;font-size:11px;")
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(cap)
        col.addStretch()
        row.addLayout(col)
    row.addStretch()
    v.addLayout(row)
    v.addStretch()
    page.resize(760, 260 + max(SIZES))
    return page


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)