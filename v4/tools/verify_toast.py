# -*- coding: utf-8 -*-
"""离屏验证：轻提示气泡重设计（2026-10-06，src/toast.py）。

覆盖：
  A 行为契约（断言）
    A1 全语义变体构造不崩，色条 token 两主题可解析
    A2 定宽 272 / 底缘 56px / 水平居中
    A3 堆叠上限 3（连发 5 条只留 3，FIFO 挤出）
    A4 hover 暂停倒计时（enterEvent/leaveEvent 直调）
    A5 动作钮回调 + 退场；关闭钮命中
    A6 loading 常驻（无倒计时）→ morph(success) 原地变体
    A7 主题切换原地换肤
  B 视觉快照（供目检 / 视觉验收）
    B1 全变体 contact sheet（浅色 + 深色）落 PNG
    B2 堆叠场景快照（3 条同屏）落 PNG

用法：python tools/verify_toast.py [--out DIR]
"""
import os
import sys

V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V4)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF  # noqa: E402
from PyQt6.QtGui import QColor, QEnterEvent, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src import motion  # noqa: E402
from src.theme import get_colors  # noqa: E402
from src.toast import (  # noqa: E402
    SHADOW_M, ToastCard, ToastCenter, WIDTH,
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


def grab_variants(theme: str, out_path: str):
    """全变体 contact sheet：每行一条气泡，带主题底色。"""
    cards = []
    specs = [
        dict(kind="success", title="已保存到随手记",
             msg="碎片「会议室白板要点」已收进今天"),
        dict(kind="info", title="发现新版本 v4.3.1",
             msg="修复了素材分组折行的显示问题"),
        dict(kind="warning", title="存储空间不足 10%",
             msg="截图钉屏可能无法继续保存"),
        dict(kind="danger", title="导出失败",
             msg="目标文件夹没有写入权限",
             action=("重试", None), ghost=("忽略", None)),
        dict(kind="accent", title="已删除 1 条碎片",
             msg="「临时存的接口地址」", action=("撤销", None)),
        dict(kind="loading", title="正在索引本地素材…",
             msg="68 张 · 索引期间可正常使用"),
        dict(kind="neutral", title="静音已开启"),
    ]
    for spec in specs:
        card = ToastCard(theme=theme, **spec)
        card._apply_colors()
        card._resize_for_content()
        # 静态呈现：入场终态、倒计时走条画到 2/3 处
        card._target = card.pos()
        card._phase = "live"
        if card._countdown is not None:
            card._countdown.stop()
            card._countdown.setCurrentTime(card._countdown.duration() * 2 // 3)
        cards.append(card)

    sheet_w = WIDTH + SHADOW_M * 2 + 48
    sheet_h = sum(c.height() + 14 for c in cards) + 24
    bg = QColor(get_colors(theme)["bg"])
    img = QImage(sheet_w, sheet_h, QImage.Format.Format_ARGB32)
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    y = 12
    for c in cards:
        c.render(p, QPoint(24, y))
        y += c.height() + 14
    p.end()
    img.save(out_path)
    for c in cards:
        c.deleteLater()
    return out_path


def main():
    global _APP
    _APP = QApplication.instance() or QApplication([])   # 保留强引用防 GC（verify_clipboard_guard 教训）
    out_dir = V4
    if "--out" in sys.argv:
        out_dir = sys.argv[sys.argv.index("--out") + 1]
    os.makedirs(out_dir, exist_ok=True)

    motion.set_reduce_motion(False)

    # ---- A1 全语义构造 ----
    print("A. 行为契约")
    ok = True
    for kind in ("success", "info", "warning", "danger", "accent",
                 "neutral", "loading"):
        for theme in ("light", "dark"):
            c = ToastCard(kind=kind, title="标题", msg="正文", theme=theme)
            c.tint_color()  # 不抛即 token 可解析
            c.deleteLater()
    check("A1 全语义 × 双主题构造 + tint 解析", ok)

    # ---- A2 几何 ----
    ToastCenter._reset_all()
    card = ToastCenter.push(kind="success", title="已保存", theme="light")
    from src.app_paths import get_screen_geometry
    screen = get_screen_geometry()
    check("A2 定宽 272", card.width() - SHADOW_M * 2 == WIDTH)
    check("A2 水平居中",
          card.x() == screen.left() + (screen.width() - card.width()) // 2)
    bottom_gap = (screen.bottom() + 1) - (card.y() + SHADOW_M
                                          + card.card_height)
    check("A2 底缘 56px（入场 +12px 在途）",
          44 <= bottom_gap <= 56, f"gap={bottom_gap}")

    # ---- A3 堆叠上限 ----
    for i in range(4):
        ToastCenter.push(kind="info", title=f"第{i}条", theme="light")
    check("A3 连发 5 条只留 3", len(ToastCenter.live_cards()) == 3,
          f"live={len(ToastCenter.live_cards())}")

    # ---- A4 hover 暂停 ----
    c4 = ToastCenter.live_cards()[0]
    running_before = c4._countdown is None or \
        c4._countdown.state() == c4._countdown.state().Running
    c4.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    paused = c4._countdown is not None and \
        c4._countdown.state() == c4._countdown.state().Paused
    c4.leaveEvent(QEvent(QEvent.Type.Leave))
    resumed = c4._countdown is None or \
        c4._countdown.state() == c4._countdown.state().Running
    check("A4 hover 暂停 → 离开恢复",
          running_before and paused and resumed,
          f"before={running_before} paused={paused} resumed={resumed}")

    # ---- A5 动作钮 / 关闭钮 ----
    fired = []
    ToastCenter._reset_all()
    c5 = ToastCenter.push(kind="danger", title="导出失败",
                          msg="目标文件夹没有写入权限",
                          action=("重试", lambda: fired.append(1)),
                          theme="light")
    c5._buttons[-1].click()
    check("A5 动作钮回调 + 退场", fired == [1] and c5._phase == "leave")

    # ---- A6 loading 常驻 + morph ----
    ToastCenter._reset_all()
    c6 = ToastCenter.push(kind="loading", title="正在索引本地素材…",
                          theme="light")
    no_timer = c6._countdown is None and c6._lifetime == 0
    c6.morph("success", title="索引完成", msg="68 张已就绪")
    check("A6 loading 无倒计时 → morph 后重启倒计时",
          no_timer and c6._countdown is not None and c6.kind == "success")

    # ---- A7 主题切换 ----
    t_before = c6.tint_color().name()
    ToastCenter.set_theme("dark")
    t_after = c6.tint_color().name()
    check("A7 set_theme 原地换肤", t_before != t_after,
          f"{t_before} vs {t_after}")

    # ---- B 视觉快照 ----
    print("B. 视觉快照")
    for theme in ("light", "dark"):
        path = os.path.join(out_dir, f"toast_variants_{theme}.png")
        grab_variants(theme, path)
        check(f"B1 {theme} 全变体快照", os.path.exists(path), path)
        print(f"         -> {path}")

    # B2 堆叠场景（3 条同屏，等入场完成后的目标位）
    ToastCenter._reset_all()
    scene_cards = [
        ToastCard(kind="warning", title="番茄钟即将结束",
                  msg="还剩 1 分钟，记得收尾", theme="dark"),
        ToastCard(kind="accent", title="已归档 2 条碎片",
                  action=("撤销", None), theme="dark"),
        ToastCard(kind="success", title="已复制到剪贴板", theme="dark"),
    ]
    y = 20
    for c in scene_cards:
        c._phase = "live"
        c.move(24, y)
        y += c.height() + 10
    sheet_w = WIDTH + SHADOW_M * 2 + 48
    sheet_h = y + 16
    img = QImage(sheet_w, sheet_h, QImage.Format.Format_ARGB32)
    img.fill(QColor(get_colors("dark")["bg"]))
    p = QPainter(img)
    for c in scene_cards:
        c.render(p, QPoint(24, c.y()))
    p.end()
    path = os.path.join(out_dir, "toast_stack_dark.png")
    img.save(path)
    check("B2 堆叠场景快照", os.path.exists(path), path)
    print(f"         -> {path}")
    for c in scene_cards:
        c.deleteLater()

    ToastCenter._reset_all()
    print(f"\n===== verify_toast: {PASS} passed, {FAIL} failed =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
