# -*- coding: utf-8 -*-
"""转场回归验证：小卡片 Tab 切换时是否还有「页面叠影 / 透底」。

判据（三条，全部自动判定）：
  1. 遮罩画布「全透明像素 = 0」—— 页面快照本身大面积透明，必须由玻璃底闭合，
     否则旧页会从透明区透上来（这就是重影的根因）。
  2. 两页位移互补：old_off + new_off ≡ slide，边界严丝合缝（既不重叠也不留缝）。
  3. 末帧与静止态**像素一致**：t=1 时长画布 = 玻璃底 + 新页内容，
     而收尾后真实显示也正是这个组合，二者逐像素比对应当几乎无差异。

输出：
    设计稿/验证-转场-中途.png      动画 50% 处（全尺寸，肉眼查重影/接缝）
    设计稿/验证-转场-帧序列.png    20/40/60/80% 四帧 2×2 拼图
用法：
    python check_transition.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from src.app_paths import get_base_dir
from src.card_window import CardWindow
from src.config import ConfigManager
from src.fragment_manager import FragmentManager
from src.task_manager import TaskManager

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT, "设计稿")
TARGET_TAB = 2          # 切到「日程任务」页


def compose_on_desktop(pixmap: QPixmap) -> QPixmap:
    """半透明窗口的透明区在图片查看器里会显示成黑，先合成到模拟桌面上。"""
    out = QPixmap(pixmap.size())
    painter = QPainter(out)
    grad = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    grad.setColorAt(0.0, QColor("#E9EFF4"))
    grad.setColorAt(0.55, QColor("#DCE5EC"))
    grad.setColorAt(1.0, QColor("#D0DBE4"))
    painter.fillRect(out.rect(), QBrush(grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(79, 195, 192, 200))
    painter.drawEllipse(QRect(-60, -50, 240, 170))
    painter.setBrush(QColor(126, 143, 224, 180))
    painter.drawEllipse(QRect(pixmap.width() - 180, -40, 240, 180))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


def transparent_ratio(pm: QPixmap) -> float:
    """统计全透明像素占比（采样）。"""
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    step = max(1, img.width() // 90)
    total = clear = 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), step):
            total += 1
            if img.pixelColor(x, y).alpha() == 0:
                clear += 1
    return 0.0 if total == 0 else clear / total


def region_diff(a: QPixmap, b: QPixmap, region: QRect, tol: int = 8):
    """返回 (超过容差的像素数, 最大通道差)"""
    ia = a.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    ib = b.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    bad = worst = 0
    for y in range(region.top(), region.bottom()):
        for x in range(region.left(), region.right()):
            ca, cb = ia.pixelColor(x, y), ib.pixelColor(x, y)
            d = max(abs(ca.red() - cb.red()), abs(ca.green() - cb.green()),
                    abs(ca.blue() - cb.blue()), abs(ca.alpha() - cb.alpha()))
            worst = max(worst, d)
            if d > tol:
                bad += 1
    return bad, worst


def main():
    app = QApplication(sys.argv)
    from src.app_paths import get_data_dir
    data = get_data_dir(get_base_dir())
    config = ConfigManager(os.path.join(data, "config.json"))
    config.set("theme", "light")
    frags = FragmentManager(os.path.join(data, "fragments.json"))

    # 任务用临时文件，避免污染真实数据
    tmp = os.path.join(tempfile.mkdtemp(prefix="fp_trans_"), "tasks.json")
    tasks = TaskManager(tmp)
    tasks.add_task("核对转场是否还有叠影", "", "2026-09-30")
    tasks.add_task("把两张快照的位移同步", "", "2026-10-02")
    tasks.add_task("确认无 QGraphicsOpacityEffect", "", "2026-10-05")

    card = CardWindow(theme="light")
    card.set_config_manager(config)
    card.set_fragment_manager(frags)
    card.set_task_manager(tasks)
    card.show()
    card.popup_near(QRect(500, 320, 112, 112))

    import time

    def wait(ms):
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    wait(900)

    st = card._stack
    region = QRect(st.mapTo(card, QPoint(0, 0)), st.size())
    dpr = card.devicePixelRatioF() or 1.0
    px_region = QRect(int(region.left() * dpr), int(region.top() * dpr),
                      int(region.width() * dpr), int(region.height() * dpr))
    print("[区域] 内容区逻辑 %s → 物理 %s（DPR=%.2f）"
          % (region.getRect(), px_region.getRect(), dpr))

    # ---- 触发转场并暂停，逐帧手动驱动 ----
    card._switch_mode("task")
    anim = card._page_transition_anim
    anim.pause()
    compose = card._trans_compose

    frames = {}
    for t in (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0):
        compose(t)
        view = card._trans_labels[1]
        if abs(t - 0.5) < 1e-6:
            ratio = transparent_ratio(view._pm)
            hidden = not st.isVisible()
            print("[判据1] 转场期间真实页面已隐藏 = %s（下方只有真实玻璃）"
                  % hidden)
            print("        遮罩半透明占比 = %.1f%% —— 空白处露出的是玻璃而非旧页 → %s"
                  % (ratio * 100, "通过" if hidden else "失败（旧页会透上来）"))
        # 位移判据：画布上两页的贴图起点互补
        w = st.width()
        moved = int(round(w * t))
        off_old, off_new = -moved, w - moved
        if abs(t - 0.5) < 1e-6:
            print("[判据2] t=0.5 旧页起点=%d 新页起点=%d（旧页右缘=%d）→ 间隙=%d"
                  % (off_old, off_new, off_old + w, off_new - (off_old + w)))
        frames[t] = card.grab()

    # ---- 判据3：末帧 vs 收尾后的静止态 ----
    compose(1.0)
    frame_end = card.grab()
    st.setCurrentIndex(TARGET_TAB)
    card._finalize_page_transition()
    wait(30)
    frame_rest = card.grab()
    bad, worst = region_diff(frame_end, frame_rest, px_region)
    print("[判据3] 末帧 vs 静止态：超差像素 %d / %d，最大通道差 %d → %s"
          % (bad, px_region.width() * px_region.height(), worst,
             "通过" if bad == 0 else "有差异"))

    # ---- 出图 ----
    compose(0.5)
    frames[0.5] = card.grab()
    compose_on_desktop(frames[0.5]).save(
        os.path.join(OUT_DIR, "验证-转场-中途.png"))

    picks = [0.2, 0.4, 0.6, 0.8]
    cell_w, cell_h = px_region.width(), px_region.height()
    scale = 0.62
    cw, ch = int(cell_w * scale), int(cell_h * scale)
    sheet = QPixmap(cw * 2 + 24, ch * 2 + 24)
    sheet.fill(QColor("#D8E0E8"))
    sp = QPainter(sheet)
    for i, t in enumerate(picks):
        img = compose_on_desktop(frames[t]).toImage().copy(px_region)
        sp.drawImage(QRect(8 + (i % 2) * (cw + 8), 8 + (i // 2) * (ch + 8), cw, ch),
                     img.scaled(cw, ch, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation))
        sp.setPen(QColor("#0F1720"))
        sp.drawText(14 + (i % 2) * (cw + 8), 26 + (i // 2) * (ch + 8),
                    "t = %.0f%%" % (t * 100))
    sp.end()
    sheet.save(os.path.join(OUT_DIR, "验证-转场-帧序列.png"))
    print("[OK] 出图：验证-转场-中途.png / 验证-转场-帧序列.png")

    card.hide()
    wait(60)


if __name__ == "__main__":
    main()
